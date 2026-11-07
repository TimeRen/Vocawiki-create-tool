import asyncio
import difflib
import json
import logging
import re
import sys
import unicodedata
import urllib
from asyncio import Future
from concurrent.futures import as_completed
from json import JSONDecodeError
from typing import Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from requests_futures.sessions import FuturesSession

from i18n.i18n import _
from models.creators import Person
from utils import login, wiki_api
from utils.string import is_empty


def template_category_url() -> str:
    """「查模板分类」的 api.php 地址（主机跟着 `wiki.api_url` 走，自建 wiki 也能用）。

    以前这里硬编码了 voca.wiki（模块常量 BASE_TEMPLATE），换上自建 wiki 时
    这条搜索回退还会去问公开站点。URL 里留一个 `{}` 给模板名。
    """
    api = login.api_url()
    separator = "&" if "?" in api else "?"
    return f"{api}{separator}action=parse&format=json&page=Template:{{}}&prop=categories"


class ProducerSearchSession(FuturesSession, login.WikiSession):
    """并发查 voca.wiki 的 api.php，并复用 login 的「工具 UA 只发给 wiki」规矩。

    `FuturesSession.request()` 内部调的是 `requests.Session.request`（不是 super），
    而 `Session.request` 会调 `self.prepare_request`，所以把 `WikiSession` 混进来就生效。
    """


# voca.wiki 上 P主大家族模板所属的分类；分类里既有真正的模板，也有指向它们的大量重定向
# （例：Template:米津玄師 / Template:米津玄师 → Template:Hachi，Template:Kz → Template:Livetune）。
# 字典就是照着这个分类现取的，所以 wiki 上增删模板后不用改代码。
PRODUCER_TEMPLATE_CATEGORY = "Category:P主模板"
PRODUCER_TEMPLATE_NAMESPACE = "10"                   # Template:
REQUEST_TIMEOUT = 60
_TEMPLATE_PREFIX = "Template:"

# 运行期缓存：{P主模板名 / 重定向名: 模板名}（键值都不带 Template: 前缀）
_producer_template_cache: Optional[Dict[str, str]] = None
# 大小写与下划线不敏感的备用索引
_producer_template_index: Optional[Dict[str, str]] = None
# 「字典里查不到 → insource 搜一次」的结果缓存（键是归一化后的 P主名）
_producer_name_cache: Dict[str, Optional[str]] = {}


def _strip_template_prefix(title: str) -> str:
    """`Template:40mP` → `40mP`；下划线按 wiki 习惯视为空格。"""
    title = str(title or "").replace("_", " ").strip()
    if title.startswith(_TEMPLATE_PREFIX):
        title = title[len(_TEMPLATE_PREFIX):]
    return title


def _normalize(name: str) -> str:
    """比对用的归一化（忽略下划线/空格、忽略大小写、忽略全角/半角）。

    **全角/半角必须一起归一**：站上的模板叫 `Template:デッドボールＰ`（全角 Ｐ），
    VocaDB 上的 P主名是 `デッドボールP`（半角）—— 不归一的话字典查不到，
    只能退到别名兜底，而别名里恰好有 `Ryo Takeshita`（见用户 2026-10-22 报的那次错认）。
    """
    text = unicodedata.normalize("NFKC", str(name or ""))
    return text.replace("_", " ").strip().casefold()


def fetch_producer_templates(refresh: bool = False) -> Dict[str, str]:
    """取「P主模板名 → 模板名」字典（含重定向）；键值都不带 Template: 前缀。

    来源是 voca.wiki 的 `Category:P主模板`，用 generator 一次把分类成员和各自的重定向都拿回来，
    所以 wiki 上改完分类下次运行就是新的（同一次运行只联网一次，`refresh=True` 可强制重取）。
    取不到时返回空字典（有旧结果就用旧的），调用方据此回退到逐个搜索。
    """
    global _producer_template_cache, _producer_template_index
    if _producer_template_cache is not None and not refresh:
        return _producer_template_cache
    if refresh:
        _producer_name_cache.clear()             # 字典重取过，之前的「搜不到」也该重试

    mapping: Dict[str, str] = {}
    try:
        params = {
            "action": "query",
            "generator": "categorymembers",
            "gcmtitle": PRODUCER_TEMPLATE_CATEGORY,
            "gcmnamespace": PRODUCER_TEMPLATE_NAMESPACE,
            "gcmlimit": "500",
            "prop": "redirects",              # 每个模板的重定向（别名）
            "rdlimit": "max",
            "format": "json",
            "formatversion": "2",
        }
        session = login.get_api_session()
        while True:
            payload = session.get(login.api_url(), params=params,
                                  timeout=REQUEST_TIMEOUT).json()
            for page in payload.get("query", {}).get("pages", []):
                title = _strip_template_prefix(page.get("title", ""))
                if not title or page.get("missing"):
                    continue
                mapping[title] = title
                for redirect in page.get("redirects", []):
                    name = _strip_template_prefix(redirect.get("title", ""))
                    if name:
                        mapping[name] = title
            # 成员多于 500 时继续翻页（continue 里除 "continue" 之外的都是要继续带的参数）
            continuation = {key: value for key, value in (payload.get("continue") or {}).items()
                            if key != "continue"}
            if not continuation:
                break
            params.update(continuation)
    except Exception as e:
        logging.warning("无法获取P主模板清单（%s）：%s", PRODUCER_TEMPLATE_CATEGORY, e)
        return _producer_template_cache or {}

    # 先放真正的模板名，再补重定向名（同名时以模板为准）
    _producer_template_cache, _producer_template_index = mapping, build_index(mapping)
    logging.info("已载入%d个P主模板（含重定向）", len(mapping))
    return mapping


def build_index(mapping: Dict[str, str]) -> Dict[str, str]:
    """`{归一化名字: 模板名}` 的备用索引（大小写 / 全半角 / 下划线都不敏感）。"""
    index = {_normalize(title): title for title in set(mapping.values())}
    for name, target in mapping.items():
        index.setdefault(_normalize(name), target)
    return index


def lookup_producer_template(name: str) -> Optional[str]:
    """按 P主名（或它的重定向名）查模板名；字典里没有则返回 None。"""
    mapping = fetch_producer_templates()
    if not mapping or not name:
        return None
    if name in mapping:
        return mapping[name]
    index = _producer_template_index or build_index(mapping)
    if index:
        exact = index.get(_normalize(name))
        if exact:
            return exact
        near = _lookup_near_identical(name, index)
        if near:
            return near
        return _search_template_by_name(name, mapping)
    return None


# 「字典里没有这个名字」时的最后一招：在 Template 命名空间里全文搜这个名字。
# 实测《all》（voca.wiki 278119，用户 2026-11-03 给的差异）：模板页名是 `Tamaz-P`，
# 日文名 `たまぁ～ずP` 只写在模板的 `|title = {{coloredlink|…|Tamaz-P|たまぁ～ずP}}` 里，
# 而 VocaDB 给的就是 `たまぁ～ずP`；`Category:P主模板` 里既没有这个名字、也没有
# `Template:たまぁ～ずP` 这个重定向，近似匹配（长度必须相同）也够不着 —— 条目于是少了 P主模板。
# `insource:` 搜索一次就能把它找出来，结果只认**分类里已知的模板**（见 `_category_templates()`）。
_producer_name_cache: Dict[str, Optional[str]] = {}


def _category_templates(mapping: Dict[str, str]) -> set:
    """`Category:P主模板` 里**真正的模板名**集合（不含重定向别名）。"""
    return {_normalize(title) for title in set(mapping.values())}


def _search_template_by_name(name: str, mapping: Dict[str, str]) -> Optional[str]:
    """在 Template 命名空间里搜 `name`，只认 `Category:P主模板` 里、且**标题行写着这个名字**的模板。

    只接受**唯一**答案：搜到好几个不同模板时不猜（挂错模板比不挂难查得多，同 `_lookup_near_identical`）。
    `insource:` 是**子串**匹配，所以还要回头看模板自己：P主模板的 `|title` 那一行写着歌姬 / P主 的名字
    （`|title = {{coloredlink|#FFF|Tamaz-P|たまぁ～ずP}}`），核对这一行才认 ——
    否则「某首歌的作曲者是某人」这种顺带出现的名字也会把别人的模板认成他的
    （实测 `insource:"キュー"` 会命中 Dobu No Awa / 绵饴 / Nem / Risou 四份**别人**的模板）。
    """
    key = _normalize(name)
    if key in _producer_name_cache:
        return _producer_name_cache[key]
    found: Optional[str] = None
    try:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": 'insource:"' + str(name).replace('"', '\\"') + '"',
            "srnamespace": PRODUCER_TEMPLATE_NAMESPACE,
            "srlimit": "10",
            "format": "json",
            "formatversion": "2",
        }
        payload = login.get_api_session().get(login.api_url(), params=params,
                                              timeout=REQUEST_TIMEOUT).json()
        known = _category_templates(mapping)
        candidates: List[str] = []
        for hit in payload.get("query", {}).get("search") or []:
            title = _strip_template_prefix(hit.get("title", ""))
            if title and _normalize(title) in known and title not in candidates:
                candidates.append(title)
        texts = _template_texts(candidates)
        candidates = [title for title in candidates if _title_names(texts.get(title, ""), name)]
        if len(candidates) == 1:
            found = candidates[0]
            logging.info("P主「%s」在模板标题行里搜到模板「%s」", name, found)
        elif candidates:
            logging.info("P主「%s」搜到多个模板（%s），不猜", name, " / ".join(candidates))
    except Exception as e:                                # noqa: BLE001 - 查不到就当没有
        logging.warning("按P主名搜索模板失败（%s）：%s", name, e)
    _producer_name_cache[key] = found
    return found


def _title_names(text: str, name: str) -> bool:
    """模板正文里 `|title` 那一行有没有写着这个名字。"""
    for line in str(text or "").splitlines():
        if re.match(r"\s*\|\s*title\s*=", line) and str(name) and str(name) in line:
            return True
    return False


def _template_texts(templates: List[str]) -> Dict[str, str]:
    """一次取回这些模板的正文（`utils.wiki_api` 的批量接口）；取不到的给空串。"""
    if not templates:
        return {}
    titles = [f"Template:{_strip_template_prefix(template)}" for template in templates]
    try:
        texts = wiki_api.fetch_pages_text(titles)
    except Exception as e:                                # noqa: BLE001 - 取不到就不猜
        logging.warning("读取模板正文失败（%s）：%s", " / ".join(templates), e)
        return {}
    by_name = {_strip_template_prefix(title): text for title, text in texts.items()}
    return {template: by_name.get(_strip_template_prefix(template), "") for template in templates}


# 近似写法匹配的门槛：只在「归一化后长度相同、只差一两个字」时才算（繁简 / 异体 / 错字）。
# 实测《4》（voca.wiki 条目 4）：站上的模板是 `Template:西岛尊大`（简体「岛」），
# VocaDB 上的 P主名是 `西島尊大`（繁体「島」）—— NFKC 不管繁简，字典查不到，
# 条目里因此少了 P主模板（用户 2026-11-01 报）。相似度 3/4 = 0.75。
NEAR_IDENTICAL_RATIO = 0.75
NEAR_IDENTICAL_MIN_CHARS = 3


def _has_ascii(name: str) -> bool:
    """这个名字里有没有拉丁字母 / 数字（罗马音写的名字差一个字母就是另一个人）。"""
    return any(char.isascii() and char.isalnum() for char in str(name or ""))


def _lookup_near_identical(name: str, index: Dict[str, str]) -> Optional[str]:
    """字典里没有完全一样的写法时，找「只差一两个字」的那个；拿不准就返回 None。

    只比**长度相同**的名字（`西島尊大` vs `西岛尊大`），而且必须**唯一最高分** ——
    两个候选并列时宁可不要，也不能给条目挂错模板（挂错比不挂难查得多）。

    ⚠️ **只给汉字 / 假名名字用**（用户 2026-11-05 报的《永无止境的交响乐 ～交响曲2624～》）：
    那一首的 P主 是 `キュー`（VocaDB 上的写法还有 `Kyuu`），`expand_name()` 造出的 `KyuuP`
    与另一位 P主 的模板 `Kyusu` 只差一个字母、相似度 0.80 —— 于是条目里被塞了 `{{Kyusu|collapsed}}`，
    站上编辑只能删掉。拉丁字母 / 数字的名字差一个字母就是**另一个人**（`Kyuu` ≠ `Kyus`），
    罗马音写法又五花八门（`Kyuu` / `Kyu` / `Q`…），这类名字只认**完全相同**的写法（归一化后）。
    汉字 / 假名才谈得上繁简、异体、错字。
    """
    key = _normalize(name)
    if len(key) < NEAR_IDENTICAL_MIN_CHARS or _has_ascii(name):
        return None
    best: List[tuple] = []
    for candidate, title in index.items():
        if len(candidate) != len(key) or _has_ascii(candidate):
            continue
        score = difflib.SequenceMatcher(None, key, candidate).ratio()
        if score < NEAR_IDENTICAL_RATIO:
            continue
        best.append((score, title))
    if not best:
        return None
    best.sort(reverse=True)
    if len(best) > 1 and best[0][0] == best[1][0]:
        logging.info("P主「%s」有多个同样接近的模板（%s / %s），不猜",
                     name, best[0][1], best[1][1])
        return None
    logging.info("P主「%s」按近似写法匹配到模板「%s」（相似度%.2f）",
                 name, best[0][1], best[0][0])
    return best[0][1]


def producer_template_exists(response: str) -> bool:
    result = json.loads(response)
    if 'parse' not in result:
        return False
    cats = result['parse']['categories']
    for cat in cats:
        cat = cat['*']
        if cat == "音乐家模板" or cat == "虚拟歌手音乐人模板":
            return True
    return False


def expand_name(producer) -> List[str]:
    """返回艺名与 P 后缀互换的候选，兼容作者名与模板名不一致的惯例。"""
    names = [producer.name, *producer.name_eng]
    names = [name for name in names if not is_empty(name)]
    variants = [name[:-1] if name.endswith("P") else name + "P" for name in names]
    return list(dict.fromkeys([*names, *variants]))


async def producer_checker(producers: List[Person], base_url: str, predicate: Callable[[str], bool]):
    try:
        with ProducerSearchSession() as session:
            futures: List[Future] = []
            for p in producers:
                names = expand_name(p)
                for name in names:
                    url = base_url.format(urllib.parse.quote(name))
                    futures.append(session.get(url))
                    futures[-1].producer_name = name
            return [response.producer_name for response in as_completed(futures)
                    if not response.exception() and predicate(response.result().text)]
    except Exception as e:
        session.close()
        logging.error("Error occurred.", exc_info=e)
        return []


async def get_producer_templates(producers: List[Person]) -> List[str]:
    """P主对应的大家族模板名。

    先查 `Category:P主模板` 的字典（一次联网就拿到全部模板与重定向），命中就直接用；
    字典里没有的才退回原来的逐个搜索（看模板分类是不是 音乐家模板 / 虚拟歌手音乐人模板）。
    """
    logging.info(_("producer_template") + ", ".join([p.name for p in producers]))
    templates: List[str] = []
    unknown: List[Person] = []
    for producer in producers:
        hit = next((template for template in
                    (lookup_producer_template(name) for name in expand_name(producer))
                    if template), None)
        if hit:
            templates.append(hit)
        else:
            unknown.append(producer)
    if unknown:
        templates.extend(await producer_checker(unknown, template_category_url(),
                                               producer_template_exists))
    result: List[str] = []
    for template in templates:
        if template not in result:
            result.append(template)
    return result


async def get_producer_info(producers: List[Person]) -> List[str]:
    try:
        return await get_producer_templates(producers)
    except Exception as e:
        logging.warning("Error occurred while trying to fetch MGP templates. Continuing...", exc_info=e)
        return []
