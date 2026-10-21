import logging
import re
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from typing import Tuple, List, Optional, Set

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
# 按**页面名**搜（`?cmd=list` 那张列表的过滤框）：比全文搜更准，结果是 `/pages/N.html`
CHLY_LIST_URL = "https://w.atwiki.jp/vocaloidchly/list?keyword={}"
CHLY_PAGE_URL = "https://w.atwiki.jp/vocaloidchly/pages/{}.html"
# 初音ミク Wiki（日文歌词的来源）：同一套 `/list` + `/search`
HMIKU_LIST_URL = "https://w.atwiki.jp/hmiku/list?keyword={}"
HMIKU_SEARCH_URL = "https://w.atwiki.jp/hmiku/search?andor=and&keyword={}&search_field=source"
HMIKU_PAGE_URL = "https://w.atwiki.jp/hmiku/pages/{}.html"
# wiki 名 → 两种搜索地址（见 `candidate_urls()`）
LIST_URLS = {"vocaloidchly": CHLY_LIST_URL, "hmiku": HMIKU_LIST_URL}
SEARCH_URLS = {"vocaloidchly": CHLY_SEARCH_URL, "hmiku": HMIKU_SEARCH_URL}


def candidate_urls(wiki: str, name: str, producer: str = "") -> List[str]:
    """搜页面的候选地址：**先按页面名搜（`/list`），再退到全文搜（`/search`）**。

    `/list` 是普通的列表页（不是那个一直被 Cloudflare 挑战的 `/search`），curl / 真浏览器
    一取就到；`/search` 常常卡在人机验证上，所以把可靠的那条放在前面（2026-10-21 实测：
    `/list?keyword=咲うわ。` 直接 200 并给出 `/pages/56279.html`）。全文搜仍然留着 ——
    页面名跟歌名不一样时（`曲名：…` 那种）只有它能找得到。
    """
    list_url = LIST_URLS.get(wiki) or f"https://w.atwiki.jp/{wiki}/list?keyword={{}}"
    search_url = SEARCH_URLS.get(wiki) or (
        f"https://w.atwiki.jp/{wiki}/search?andor=and&keyword={{}}&search_field=source")
    keywords = [f"{name}+{producer}", name] if producer else [name]
    urls = [list_url.format(quote(name, safe=""))]
    urls += [search_url.format(quote(word, safe="+")) for word in keywords]
    return urls

# 搜索结果里的链接：`/vocaloidchly/pages/3236.html` 或 `?cmd=word&…&pageid=3236`
PAGE_LINK_RE = re.compile(r"/[^/\"']+/pages/(\d+)\.html|[?&]pageid=(\d+)")
# Cloudflare 挑战页的标志
CHALLENGE_MARK = "Just a moment"


def _challenge_or_error(response) -> bool:
    """这个响应能不能当「正常页面」用（Cloudflare 挑战页 / 非 200 都不行）。"""
    body = getattr(response, "text", "") or ""
    return getattr(response, "status_code", 0) != 200 or CHALLENGE_MARK in body[:3000]


def _fetch_atwiki_html(url: str) -> Optional[str]:
    """取 atwiki 的一页 HTML：先直接请求，被 Cloudflare 挡住时借真浏览器（`fetch_page_html()` 同一套）。

    2026-10-20 实测：`/search` 这类地址**一直**是 403 挑战页（`requests` 的指纹过不去），
    所以「顺手查一下有没有翻译」以前永远查不到东西 —— 用户报「171 明明有翻译页却没预填」。
    """
    try:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        if not _challenge_or_error(response):
            return response.text or ""
        logging.info("AtWiki 请求被挡（HTTP %s），改借真浏览器：%s",
                     getattr(response, "status_code", "?"), url)
    except Exception as e:                    # noqa: BLE001 - 网络问题一律退回浏览器那条路
        logging.info("AtWiki 请求失败（%s），改借真浏览器：%s", e, url)
    return browser_fetch.fetch_html(url)


def _result_links(html: str, page_url: str = CHLY_PAGE_URL) -> List[Tuple[str, str]]:
    """搜索结果页里的 `(页面名, 页面地址)`，两种页面结构都认（见 `CHLY_SEARCH_URL` / `CHLY_LIST_URL`）。

    `page_url` 是「页面 id → 地址」的模板：搜的是哪个 wiki，结果就该回到哪个 wiki
    （`w.atwiki.jp/hmiku` 的老搜索也走这里）。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    body = soup.find("div", {"id": "wikibody"}) or soup
    found: List[Tuple[str, str]] = []
    for link in body.find_all("a", href=True):
        match = PAGE_LINK_RE.search(link.get("href") or "")
        if match is None:
            continue
        page_id = match.group(1) or match.group(2)
        title = (link.get("title") or link.get_text(strip=True) or "").strip()
        found.append((title, page_url.format(page_id)))
    return found


# `/list`（按页面名搜）在页面名后面加了一截 ` (5696d)` / ` (1.2KB)` 之类的尾巴
PAGE_SIZE_SUFFIX_RE = re.compile(r"\s*\([^()]*\)$")


def _title_matches(title: str, wanted: Set[str]) -> bool:
    """这个页面名是不是要找的那首（`/list` 那个尾巴要去掉再比一次）。"""
    if title in wanted:
        return True
    stripped = PAGE_SIZE_SUFFIX_RE.sub("", title).strip()
    return bool(stripped) and stripped in wanted


def find_translation_page(name: str, producer: str = "") -> Optional[str]:
    """在 vocaloid中文歌词wiki 上按歌名（+P主）找翻译页，返回页面地址；找不到返回 None。

    用户 2026-10-20 要求：切到「歌词」页时，站上若有这首的翻译就自动预填。
    atwiki 现在挂着 Cloudflare、`requests` 一律被挡（见 `_fetch_atwiki_html()`），
    所以这里也要能借真浏览器（窗口开在屏幕外，用户看不见）。
    """
    if is_empty(name):
        return None
    urls = candidate_urls("vocaloidchly", name, producer)
    try:
        return find_at_wiki_page(name, urls, producer, web_search=False)
    except Exception as e:                    # noqa: BLE001 - 查不到就当没有翻译
        logging.info("在 vocaloid中文歌词wiki 搜翻译页失败：%s", e)
        return None


def get_page_lyrics_plain(url: str, name: str = "") -> Optional[Lyrics]:
    """预填用：按地址取歌词，与「从链接填充」走同一条路（`get_page_lyrics()`）。

    早先这里只走 `requests`、被挡就放弃（怕「顺手预填」弹出浏览器）；可 atwiki 现在
    **只有**真浏览器能过 Cloudflare，于是「预填」永远拿不到正文 —— 改成跟「从链接填充」
    一样退回 `utils/browser_fetch.py`（那个窗口开在屏幕外，不会打扰用户）。
    """
    return get_page_lyrics(url, name)


def find_at_wiki_page(name: str, urls: List[str], producer: str,
                      web_search: bool = True) -> Optional[str]:
    """在 atwiki 的搜索结果里找页面地址。

    `web_search=False` 时不再退到 DuckDuckGo 外网搜索 —— 「切到歌词页时顺手查一下有没有翻译」
    那一步用它：查不到就该立刻返回，不能为了它多等一次外网搜索。
    Cloudflare 挡住时由 `_fetch_atwiki_html()` 借真浏览器取，所以 `urls` 用哪个地址都行。
    """
    wanted = {name, name + "/" + producer}
    for url in urls:
        html = _fetch_atwiki_html(url)
        if not html:
            continue
        wiki = (urlparse(url).path.split("/")[1:2] or [""])[0]
        page_url = (f"https://w.atwiki.jp/{wiki}/pages/{{}}.html" if wiki
                    else CHLY_PAGE_URL)
        for page_name, found_url in _result_links(html, page_url):
            if _title_matches(page_name, wanted):
                return found_url
    if not web_search:
        return None
    # 外网搜索兜底：搜的是哪个 wiki 就只搜那个 wiki（日文歌词在 hmiku、译文在 vocaloidchly）
    wiki = (urlparse(urls[0]).path.split("/")[1:2] or [""])[0] if urls else ""
    page_url = (f"https://w.atwiki.jp/{wiki}/pages/{{}}.html" if wiki else CHLY_PAGE_URL)
    fallback_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(
        f'site:w.atwiki.jp/{wiki or "vocaloidchly"} "{name}"')
    try:
        response = http_get(fallback_url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        if response.ok:
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.select("a.result__a[href]"):
                href = link.get("href")
                match = PAGE_LINK_RE.search(href or "")
                if match:
                    return page_url.format(match.group(1) or match.group(2))
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
    _drop_table_of_contents(soup)
    node = soup.find("div", {"id": "wikibody"})
    if node is None:
        return None, soup
    return node.text, soup


def _drop_table_of_contents(soup) -> None:
    """拆掉 AtWiki 的「目次」插件块（`#contents` 那种可折叠的目录）。

    目录里原样列着 `曲紹介 / 歌詞 / コメント` 这几行，而 `parse_body()` 靠「コメント」定位
    评论区、靠「歌詞」定位正文起点 —— 目录在前时两边都会被它带偏：`parse_body()` 会把整页
    截断在目录里的「コメント」上，于是初音ミク Wiki 的页面全都解析不出歌词（2026-10-21 实测
    《咲うわ。》只解析出「詞」一个字）。目录不是正文，直接拆掉。
    """
    for toc in soup.select("div.plugin_contents"):
        box = toc.find_parent("table") or toc
        box.decompose()


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
    return _fetch_atwiki_html(url)


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
    index_comment = text.rfind("\nコメント\n")
    if index_comment != -1:
        text = text[:index_comment]
    split_index = max(text.find("翻譯"), text.find("翻译"))
    if split_index == -1:
        split_index = text.find("：")
    if split_index == -1:                 # 整页没有资讯表头（没有「：」）：全是正文
        header, body = "", text
    else:
        divider = text.find("\n", split_index)
        if divider == -1:                 # 表头只有一行、后面没换行
            divider = len(text)
        header = text[:divider]
        body = text[divider:]
    keywords = ["ブロマガより転載", "\n歌詞\n", "\n" + name + "\n"]
    index = max([body.find(k) for k in keywords])
    if index != -1:
        # 定位到那一行之后：`find("\n", index)` 取行尾，再往后移一位
        index = body.find("\n", index) + 1
        body = body[index:]
    return parse_at_wiki_header(header), parse_at_wiki_body(body, name)


def get_japanese_lyrics(name: str, producer: str = "") -> str:
    logging.info(_("jap_atwiki"))
    res = get_at_wiki_body(name, candidate_urls("hmiku", name, producer),
                           "Japanese", producer)
    return res.lyrics_chs if res else ""


def get_chinese_lyrics(name: str, producer: str = "") -> Optional[Lyrics]:
    logging.info(_("chs_atwiki"))
    return get_at_wiki_body(name, candidate_urls("vocaloidchly", name, producer),
                            "Chinese", producer)
