import asyncio
import json
import logging
import sys
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
from utils import login
from utils.string import is_empty

BASE_TEMPLATE = "https://voca.wiki/api.php?action=parse&format=json" \
                "&page=Template:{}&prop=categories"

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


def _strip_template_prefix(title: str) -> str:
    """`Template:40mP` → `40mP`；下划线按 wiki 习惯视为空格。"""
    title = str(title or "").replace("_", " ").strip()
    if title.startswith(_TEMPLATE_PREFIX):
        title = title[len(_TEMPLATE_PREFIX):]
    return title


def _normalize(name: str) -> str:
    """比对用的归一化（忽略下划线/空格、忽略大小写）。"""
    return str(name or "").replace("_", " ").strip().casefold()


def fetch_producer_templates(refresh: bool = False) -> Dict[str, str]:
    """取「P主模板名 → 模板名」字典（含重定向）；键值都不带 Template: 前缀。

    来源是 voca.wiki 的 `Category:P主模板`，用 generator 一次把分类成员和各自的重定向都拿回来，
    所以 wiki 上改完分类下次运行就是新的（同一次运行只联网一次，`refresh=True` 可强制重取）。
    取不到时返回空字典（有旧结果就用旧的），调用方据此回退到逐个搜索。
    """
    global _producer_template_cache, _producer_template_index
    if _producer_template_cache is not None and not refresh:
        return _producer_template_cache

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
        logging.warning("无法获取 P主模板清单（%s）：%s", PRODUCER_TEMPLATE_CATEGORY, e)
        return _producer_template_cache or {}

    # 先放真正的模板名，再补重定向名（同名时以模板为准）
    index = {_normalize(title): title for title in set(mapping.values())}
    for name, target in mapping.items():
        index.setdefault(_normalize(name), target)
    _producer_template_cache, _producer_template_index = mapping, index
    logging.info("已载入 %d 个P主模板（含重定向）", len(mapping))
    return mapping


def lookup_producer_template(name: str) -> Optional[str]:
    """按 P主名（或它的重定向名）查模板名；字典里没有则返回 None。"""
    mapping = fetch_producer_templates()
    if not mapping or not name:
        return None
    if name in mapping:
        return mapping[name]
    if _producer_template_index:
        return _producer_template_index.get(_normalize(name))
    return None


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
    names = [producer.name, *producer.name_eng]
    names = [name for name in names if not is_empty(name)]
    names.extend([name[:-1] for name in names if len(name) > 0 and name[-1] == 'P'])
    return list(set(names))


async def producer_checker(producers: List[Person], base_url: str, predicate: Callable[[str], bool]):
    try:
        with FuturesSession() as session:
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
        templates.extend(await producer_checker(unknown, BASE_TEMPLATE, producer_template_exists))
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
