import logging
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from typing import Tuple, List, Optional

from config import data
from config.config import get_config
from i18n.i18n import _
from models.song import Lyrics
from utils import browser_fetch
from utils.helpers import prompt_response, http_get
from utils.string import is_empty, is_junk_line, strip_credit_lines

ATWIKI_TIMEOUT = 20
# 站内「来源」栏的写法（实测条目里写「VOCALOID中文歌词wiki」）
ATWIKI_SOURCE_NAME = "VOCALOID中文歌词wiki"
# 页面末尾自动加的那行站内搜索链接（`「歌名」をウィキ内検索`）：不是歌词
ATWIKI_KEYWORD_LINE_RE = re.compile(r"^\s*「.*」をウィキ内検索\s*$")


def parse_at_wiki_header(header: str) -> List[Tuple[str, str]]:
    lines = header.split("\n")
    result: List[Tuple[str, str]] = []
    for line in lines:
        index = line.find("：")
        if index != -1:
            result.append((line[:index].strip(), line[index + 1:].strip()))
    return result


def _title_key(text: str) -> str:
    """标题比较用的键：去掉空白与包裹用的括号 / 记号（「日蝕」与 日蝕 视为同一个）。"""
    return re.sub(r"[\s\u3000「」『』【】（）()<>＜＞\[\]#*・]+", "", str(text or "")).casefold()


# 标题前后常见的装饰符号：`-273.15` 与页面正文里的 `273.15` 是同一首歌
_TITLE_EDGE_RE = re.compile(r"^[-_−–—―~～+＋·・]+|[-_−–—―~～+＋·・]+$")


def _title_like(line: str, title: str) -> bool:
    """这一行是不是**歌名**（页面正文开头常单独写一行歌名，不是歌词）。

    `-273.15` 的页面正文写的是 `273.15`（少了负号），所以比较前把首尾的装饰符号去掉。
    """
    if not title:
        return False
    key = _TITLE_EDGE_RE.sub("", _title_key(line))
    wanted = _TITLE_EDGE_RE.sub("", _title_key(title))
    return bool(key) and key == wanted


def is_lyrics(line: str, title: str = "") -> bool:
    line = line.strip()
    return not (line == "歌詞" or
                line == data.name_japanese or
                _title_like(line, title) or
                is_junk_line(line) or
                (data.name_japanese in line and ("オリジナル" in line or
                                                 re.match("[【『]+", line) or
                                                 "歌詞" in line)) or
                (("転載" in line or "转载" in line or "取り" in line)
                 and re.match("[(（]+", line))
                )


def strip_initial_lines(lines: List[str], title: str = "") -> List[str]:
    """丢掉正文开头那些「不是歌词」的行（歌名、译者声明、资讯行、空行）。

    以前只跳过空行 / 歌名 / 署名，碰到译者声明（`基於相互尊重，請取用翻譯者不要改動我的翻譯，
    感謝`，用户 2026-10-19 报）就停在那儿，后面的歌名行也跟着进了歌词 —— 现在一律认
    `is_junk_line()`。
    """
    index = 0
    while index < len(lines):
        if is_empty(lines[index]) or not is_lyrics(lines[index], title):
            index += 1
            continue
        return lines[index:]
    return []


def parse_at_wiki_body(body: str, title: str = "") -> str:
    lines = body.split("\n")
    result = []
    lines = strip_initial_lines(lines, title)
    state = 0
    index = 0
    while index < len(lines):
        if is_empty(lines[index]):
            state += 1
        elif not ATWIKI_KEYWORD_LINE_RE.match(lines[index]):
            if state > 2:
                result.append("")
            result.append(lines[index].strip())
            state = 0
        # 「…をウィキ内検索」那行不是歌词，直接跳过（什么都不留）
        index += 1
    # 正文里也可能夹着资讯行（有的译者把「作詞：…／作曲：…／歌：…」写在「翻譯」后面，
    # 或者整段从别处粘来带着链接），一起摘掉 —— 生成流程与「从链接填充」共用这一份口径。
    return strip_credit_lines("\n".join(result))


def shorten_url(url: str) -> str:
    if "&pageid=" in url:
        page_id = url.split("pageid=")[1]
        url = f"https://w.atwiki.jp/vocaloidchly/pages/{page_id}.html"
    return url


# vocaloid中文歌词wiki 的站内搜索（「来源」域）
CHLY_SEARCH_URL = "https://w.atwiki.jp/vocaloidchly/search?andor=and&keyword={}&search_field=source"


def find_translation_page(name: str, producer: str = "") -> Optional[str]:
    """在 vocaloid中文歌词wiki 上按歌名（+P主）找翻译页，返回页面地址；找不到返回 None。

    只用普通 requests 查搜索页：这是生成流程里**顺手**做的一步，不该为了它弹出用户的
    浏览器。搜不到 / 被 Cloudflare 挡住就当没有翻译，用户仍可在歌词页点「从链接填充」。
    """
    if is_empty(name):
        return None
    keywords = [f"{name}+{producer}", name] if producer else [name]
    try:
        return find_at_wiki_page(name, [CHLY_SEARCH_URL.format(word) for word in keywords],
                                 producer, web_search=False)
    except Exception as e:                    # noqa: BLE001 - 查不到就当没有翻译
        logging.info("在 vocaloid中文歌词wiki 搜翻译页失败：%s", e)
        return None


def get_page_lyrics_plain(url: str, name: str = "") -> Optional[Lyrics]:
    """同 `get_page_lyrics()`，但**不借浏览器**：预填用，拿不到就当没有。

    `get_page_lyrics()` 被 Cloudflare 挡住时会开一次用户的浏览器；「切到歌词页时先预填」
    是顺手做的事，不能因此弹窗口 —— 拿不到就让用户自己点「从链接填充」（那条路仍会借浏览器）。
    """
    try:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        body = response.text or ""
        if response.status_code != 200 or "Just a moment" in body[:3000]:
            logging.info("预取 AtWiki 页面被挡（HTTP %s）：%s", response.status_code, url)
            return None
    except Exception as e:                    # noqa: BLE001 - 网络问题一律按「没预取到」处理
        logging.info("预取 AtWiki 页面失败（%s）：%s", e, url)
        return None
    lyrics = parse_page(body, name)
    if lyrics is not None:
        lyrics.source_url = shorten_url(url)
    return lyrics


def find_at_wiki_page(name: str, urls: List[str], producer: str,
                      web_search: bool = True) -> Optional[str]:
    """在 atwiki 的搜索结果里找页面地址。

    `web_search=False` 时不再退到 DuckDuckGo 外网搜索 —— 「切到歌词页时顺手查一下有没有翻译」
    那一步用它：查不到就该立刻返回，不能为了它多等一次外网搜索。
    """
    for url in urls:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        if response.status_code == 403:
            continue
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        wiki_body = soup.find("div", {"id": "wikibody"})
        result_list = wiki_body.find("ul") if wiki_body else None
        if result_list is None:
            continue
        for item in result_list.find_all("li"):
            link = item.find("a")
            if not link:
                continue
            page_name = link.get_text(strip=True)
            if page_name in {name, name + "/" + producer}:
                return "https:" + link.get("href")
    if not web_search:
        return None
    fallback_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(
        f'site:w.atwiki.jp/hmiku "{name}"')
    try:
        response = http_get(fallback_url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        if response.ok:
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.select("a.result__a[href]"):
                href = link.get("href")
                if "w.atwiki.jp/hmiku/pages/" in href:
                    return href.split("&rut=")[-1]
    except Exception:
        logging.debug("External AtWiki search fallback failed.", exc_info=True)
    return None


def get_vocaloid_collection_info(
        name: str, producer: str = "") -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    url_jap = "https://w.atwiki.jp/hmiku/search?andor=and&keyword={}&search_field=source"
    try:
        found = find_at_wiki_page(name, [url_jap.format(name + "+" + producer),
                                         url_jap.format(name)], producer)
        if found is None:
            return None
        response = http_get(found, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        response.raise_for_status()
        page = response.text
        if "ボカコレ" not in page:
            return None
        collection_match = re.search(r"ボカコレ20\d{2}[春夏秋冬]", page)
        if not collection_match:
            return None
        rank_match = re.search(
            r"(TOP100|TOP30|ROOKIE|REMIX).{0,200}?(?:第\s*)?(\d+)\s*(?:位|名)",
            page, re.DOTALL)
        if rank_match:
            return collection_match.group(0), rank_match.group(1), rank_match.group(2)
        return collection_match.group(0), None, None
    except Exception as e:
        logging.warning("Unable to check The VOCALOID Collection for %s: %s", name, e)
        return None


def get_vocaloid_collection(name: str, producer: str = "") -> Optional[str]:
    info = get_vocaloid_collection_info(name, producer)
    return info[0] if info else None


def _wikibody_text(html: str):
    """页面里 `#wikibody` 的纯文本（去掉「最終更新」那行）；取不到返回 None。"""
    soup = BeautifulSoup(html, "html.parser")
    # remove last modify message / auto-generated site search link from body
    for elem in soup.find_all("div", attrs={'class': ['atwiki-lastmodify', 'atwiki-page-keyword']}):
        elem.decompose()
    node = soup.find("div", {"id": "wikibody"})
    if node is None:
        return None, soup
    return node.text, soup


def page_title(soup) -> str:
    """HTML 标题里那一截页面名：`日蝕 - vocaloid中文歌詞wiki - atwiki（アットウィキ）` → `日蝕`。

    ⚠️ 必须取**文本**（`get_text()`）：BeautifulSoup 的 `soup.title` 是 Tag，
    `str()` 会把 `<title>` 标签一起带出来（变成 `"<title>日蝕"`）—— 那样 `parse_body`
    就认不出正文开头那行歌名，标题行会混进歌词（2026-10-17 修的）。
    """
    title = getattr(soup, "title", None)
    text = title.get_text() if hasattr(title, "get_text") else str(title or "")
    match = re.match(r"\s*(.+?)\s+-\s+", text)
    return match.group(1).strip() if match else ""


def parse_page(html: str, name: str = "") -> Optional[Lyrics]:
    """把**一个 AtWiki 页面**的 HTML 解析成 Lyrics（资讯表头 / 翻译者 / 歌词正文）。

    `name` 不给时用页面标题当歌名（`parse_body` 要用它定位正文起点）。
    只解析，不发请求 —— 生成流程与歌词页「从链接填充」共用这一份口径。
    """
    text, soup = _wikibody_text(html)
    if text is None:
        return None
    staff, body = parse_body(name or page_title(soup), text)
    translator = next((value for key, value in staff if key in ("翻译", "翻譯")), "")
    return Lyrics(staff=staff, source_name=ATWIKI_SOURCE_NAME, source_url="",
                  lyrics_chs=body, translator=translator)


def get_page_lyrics(url: str, name: str = "") -> Optional[Lyrics]:
    """按**页面地址**直接取歌词（不搜索）：歌词整理页「从链接填充」用这个。

    生成流程走的是 `get_at_wiki_body()`（先按歌名搜索）；两条路都经 `parse_page()`，
    所以「从链接填充」拿到的正文与自动取到的一致。
    """
    html = fetch_page_html(url)
    if html is None:
        return None
    lyrics = parse_page(html, name)
    if lyrics is not None:
        lyrics.source_url = shorten_url(url)
    return lyrics


def fetch_page_html(url: str) -> Optional[str]:
    """取 AtWiki 页面 HTML：先直接请求，被 Cloudflare 挑战时**借用户自己的真浏览器**取。

    2026-10-16 实测：AtWiki 现在也挂着 Cloudflare 的 JS 挑战 —— `requests` 拿到的永远是
    「Just a moment...」挑战页（curl 那类指纹能过，requests 过不去），跟 VocaDB / nicolog
    是同一堵墙，所以退回 `utils/browser_fetch.py`（开一次真浏览器、整轮复用）。
    两边都取不到就返回 None，调用方按「读不到这个页面」处理。
    """
    try:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        body = response.text or ""
        if response.status_code == 200 and "Just a moment" not in body[:3000]:
            return body
        logging.info("AtWiki 直接请求被挡（HTTP %s），改借真浏览器取：%s", response.status_code, url)
    except Exception as e:                       # noqa: BLE001 - 网络问题一律退回浏览器那条路
        logging.info("AtWiki 直接请求失败（%s），改借真浏览器取：%s", e, url)
    return browser_fetch.fetch_html(url)


def get_at_wiki_body(name: str, urls: List[str], lang: str, producer: str) -> Optional[Lyrics]:
    try:
        found = find_at_wiki_page(name, urls, producer)
        if found is None:
            return None
        logging.debug("At wiki url " + found)
        lyrics = get_page_lyrics(found, name)
        if lyrics is None:
            return None
        if not lyrics.translator:
            # 生成流程里「页面没写翻译者」要显眼（旧实现就是这个行为，别悄悄变成空）
            lyrics.translator = "ERROR!"
        return lyrics
    except Exception as e:
        logging.error(e)
        logging.error("An error occurred while fetching " + lang + " lyrics from atwiki. Falling back...")
        return None


def parse_body(name: str, text: str) -> Tuple[List[Tuple[str, str]], str]:
    """解析 atwiki 页面正文：去掉多余内容，抽出资讯（角色-名字）与歌词。

    返回 `(表头 [(角色, 名字)…], 歌词正文)`；类型注解必须写成 `Tuple[...]`，
    写成 `(List[...], str)` 是个元组表达式、不是类型（Pylance 会报「类型表达式中不允许使用 tuple 表达式」）。
    """
    index_comment = text.find("\nコメント\n")
    if index_comment != -1:
        text = text[:index_comment]
    split_index = max(text.find("翻譯"), text.find("翻译"))
    if split_index == -1:
        split_index = text.find("：")
    divider = text.find("\n", split_index)
    header = text[:divider]
    body = text[divider:]
    keywords = ["ブロマガより転載", "\n歌詞\n", "\n" + name + "\n"]
    index = max([body.find(k) for k in keywords])
    if index == -1:
        index = 0
    index = body.find("\n", index) + 1
    body = body[index:]
    return parse_at_wiki_header(header), parse_at_wiki_body(body, name)


def get_japanese_lyrics(name: str, producer: str = "") -> str:
    logging.info(_("jap_atwiki"))
    url_jap = "https://w.atwiki.jp/hmiku/search?andor=and&keyword={}&search_field=source"
    res = get_at_wiki_body(name, [url_jap.format(name + "+" + producer), url_jap.format(name)], "Japanese", producer)
    return res.lyrics_chs if res else ""


def get_chinese_lyrics(name: str, producer: str = "") -> Optional[Lyrics]:
    logging.info(_("chs_atwiki"))
    url_chs = "https://w.atwiki.jp/vocaloidchly/search?andor=and&keyword={}&search_field=source"
    return get_at_wiki_body(name, [url_chs.format(name + '+' + producer), url_chs.format(name)], "Chinese", producer)
