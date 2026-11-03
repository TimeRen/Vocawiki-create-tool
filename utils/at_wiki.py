import logging
import re
from urllib.parse import quote, quote_plus, urlparse

import requests
from bs4 import BeautifulSoup
from typing import Dict, Tuple, List, Optional, Sequence, Set

from config import data
from config.config import get_config
from i18n.i18n import _
from models.song import Lyrics
from utils import browser_fetch
from utils.helpers import prompt_response, http_get
from utils.string import (is_empty, is_junk_line, strip_credit_lines,
                          strip_translator_note)

ATWIKI_TIMEOUT = 20
# 站内「来源」栏的写法（实测条目里写「VOCALOID中文歌词wiki」）
ATWIKI_SOURCE_NAME = "VOCALOID中文歌词wiki"
# 页面末尾自动加的那行站内搜索链接（`「歌名」をウィキ内検索`）：不是歌词
ATWIKI_KEYWORD_LINE_RE = re.compile(r"^\s*「.*」をウィキ内検索\s*$")
# 正文里第一条 `<hr>` 之后算「譯註」，但分割线之前至少得有这么多行才算正文（见
# `_drop_notes_after_rule()`）—— 免得把放在页头当装饰线的分割线也当成译注起点。
_NOTES_RULE_MIN_HEAD = 4
# 譯註：正文里写 `(*注1)`，页面末尾（`<hr>` 之后）写 `注1：海容：意爲…`。
# 用户 2026-11-01 要求按 <https://voca.wiki/4> 的写法换成行内脚注：
#   `虛空（天空）與海容（大海）的輪廓線<ref>译者注：海容：意爲“…”</ref>`
NOTE_MARKER_RE = re.compile(r"[ \t\u3000]*[\(（]\s*[*＊]?\s*注\s*([0-9０-９]+)\s*[\)）]")
NOTE_LINE_RE = re.compile(r"^\s*注\s*([0-9０-９]+)\s*[：:]\s*(.*?)\s*$")
# AtWiki 的删除线插件渲染成 `[刪除線]著名妖團[刪除線]`（条目里是 `<del>著名妖團</del>`）
STRIKE_MARK_RE = re.compile(r"\[刪除線\](.*?)\[刪除線\]")
# 译者声明 / 使用礼节：固定写在「翻譯：名字」那一格的下一行（简体 / 繁体 / 日文写法都有），
# 不是歌词。实测 vocaloidchly 的《クラウドライダー》（pages/5168）、《上午4時的金星》（pages/5745）
# 都因此把 `(取用翻譯前請注意首頁的翻譯使用禮節, 並不要拿掉譯者的名字)` 当成中文歌词第一句。
ATWIKI_NOTICE_LINE_RE = re.compile(
    r"^\s*[\(（].*(?:翻譯|翻译|轉載|转载|転載|譯者|译者|取用|引用|請注意|请注意|使用禮節|使用礼节).*[\)）]\s*$")
# 页面里出现的稿件编号（niconico `sm…` / b 站 `BV…` / YouTube 11 位 id）：
# 用来核对「这一页是不是**这首歌**」——同名的歌在站上有好几首（见 `page_matches_song()`）。
_PAGE_VIDEO_ID_RE = re.compile(
    r"(?<![0-9A-Za-z])((?:sm|nm|so)\d{4,})(?![0-9A-Za-z])"
    r"|(?<![0-9A-Za-z])(BV[0-9A-Za-z]{10})(?![0-9A-Za-z])"
    r"|(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([0-9A-Za-z_-]{11})",
    re.IGNORECASE)


def parse_note_lines(text: str) -> Dict[str, str]:
    """`注1：海容：意爲…` / `注2：google或者…` → `{"1": "海容：意爲…", …}`。"""
    notes: Dict[str, str] = {}
    for line in str(text or "").replace("\r\n", "\n").split("\n"):
        match = NOTE_LINE_RE.match(line)
        if not match:
            continue
        number = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        value = STRIKE_MARK_RE.sub(r"<del>\1</del>", match.group(2)).strip()
        if value:
            notes.setdefault(number, value)
    return notes


def to_note_refs_from_markers(text: str, notes: Dict[str, str]) -> str:
    """把正文里的 `(*注1)` 换成 `<ref>译者注：…</ref>`；认不出号码的记号直接摘掉。

    注文（`notes`）是页面末尾 `注1：…` 那几行，由 `parse_note_lines()` 解析。
    记号连同前面的空格一起去掉，与条目里的写法一致（`…的輪廓線<ref>译者注：海容：…</ref>`）。
    """
    if not text:
        return text

    def replace(match: "re.Match") -> str:
        number = match.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        note = notes.get(number, "")
        return f"<ref>译者注：{note}</ref>" if note else ""

    return NOTE_MARKER_RE.sub(replace, text)


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
                ATWIKI_NOTICE_LINE_RE.match(line) or
                (data.name_japanese in line and ("オリジナル" in line or
                                                 re.match("[【『]+", line) or
                                                 "歌詞" in line)) or
                (("転載" in line or "转载" in line or "轉載" in line or "取り" in line)
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


# 歌名行里不该出现的字符：带这些的多半是歌词（句末标点 / 引号 / 括号）
_TITLE_PUNCTUATION = "。．！!？?…~～、，,；;：:「」『』（）()【】〔〕[]〈〉《》<>\"'“”‘’"


def _is_lead_title_line(line: str, title: str = "") -> bool:
    """这一行像不像「单独成段的歌名」（译者常把中文歌名单独写一行，它不是歌词）。"""
    text = (line or "").strip()
    if len(text) < 2 or len(text) > max(24, len(str(title or "")) + 6):
        return False
    return not any(char in text for char in _TITLE_PUNCTUATION)


def _drop_lead_title_block(lines: List[str], title: str = "") -> List[str]:
    """拆掉正文开头**单独成段**的那一行歌名（不是歌词）。

    vocaloidchly 的页面把歌名单独写在一个 `<div>` 里，转换成纯文本后它前后各有好几个空行
    （歌词行之间只隔一个空行）。《13歳ノ殺人鬼》（pages/1266.html）就因此把中文歌名
    「13歲的殺人魔」当成中文歌词的第一句填进「中文」栏（用户 2026-10-22 报）。
    只在「这一行独自成段 + 后面确实还有成段的歌词」时才拆，免得把歌词的第一句吃掉。
    """
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return lines
    end = index + 1
    blanks = 0
    while end < len(lines) and not lines[end].strip():
        blanks += 1
        end += 1
    if blanks < 2 or end >= len(lines):
        return lines
    if not _is_lead_title_line(lines[index], title):
        return lines
    return lines[end:]


def parse_at_wiki_body(body: str, title: str = "") -> str:
    lines = body.split("\n")
    result = []
    lines = strip_initial_lines(lines, title)
    lines = _drop_lead_title_block(lines, title)
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

    ⚠️ **`/list` 默认是「更新順」、每页只有 100 条**：老歌会被挤出去 —— 实测
    《all》(`hmiku/pages/3450.html`) 用 `?keyword=all` 一条都拿不到（100 条里全是
    Halloween / fall / Call…），换成 `&sort=pagename`（页面名順）第一条附近就是它
    （用户 2026-11-03 报「为什么没爬到日文歌词」）。所以 list 给三个排序各一条：
    页面名順（精确同名最容易命中）→ 作成日時・古い順（老歌排前面）→ 原样（更新順，
    新歌排前面）。`find_at_wiki_pages()` 一有结果就停，正常情况只请求第一条。
    """
    list_url = LIST_URLS.get(wiki) or f"https://w.atwiki.jp/{wiki}/list?keyword={{}}"
    search_url = SEARCH_URLS.get(wiki) or (
        f"https://w.atwiki.jp/{wiki}/search?andor=and&keyword={{}}&search_field=source")
    keywords = [f"{name}+{producer}", name] if producer else [name]
    keyword = list_url.format(quote(name, safe=""))
    urls = [f"{keyword}&sort=pagename",     # 页面名順：精确同名最容易命中
            f"{keyword}&sort=create",       # 作成日時・古い順：老歌排前面
            keyword]                        # 原样（更新順）：新歌排前面
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


def _fetch_atwiki_html(url: str, browser: bool = True) -> Optional[str]:
    """取 atwiki 的一页 HTML：先直接请求，被 Cloudflare 挡住时借真浏览器（`fetch_page_html()` 同一套）。

    2026-10-20 实测：`/search` 这类地址**一直**是 403 挑战页（`requests` 的指纹过不去），
    所以「顺手查一下有没有翻译」以前永远查不到东西 —— 用户报「171 明明有翻译页却没预填」。

    `browser=False` 时不再借真浏览器（只走 `requests` → 系统 curl），拿不到就返回 None。
    用户 2026-11-02 报「该去 B 站截图的时候却在请求 atwiki」：那两行其实是
    `get_vocaloid_collection_info()` 的 `/search`，它必 403、借了浏览器也只是白等 60 秒，
    还把封面截图要用的那个浏览器占住。这种**顺手查一下**的步骤不该有这种代价。
    """
    try:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        if not _challenge_or_error(response):
            return response.text or ""
        logging.info("AtWiki 请求被挡（HTTP %s），%s：%s",
                     getattr(response, "status_code", "?"),
                     "改借真浏览器" if browser else "改用 curl", url)
    except Exception as e:                    # noqa: BLE001 - 网络问题一律退回浏览器那条路
        logging.info("AtWiki 请求失败（%s），%s：%s", e,
                     "改借真浏览器" if browser else "改用 curl", url)
    if not browser:
        # 只再试一次 curl（AtWiki 只挡 requests 的 TLS 指纹），不再开真浏览器
        return browser_fetch.curl_html(url)
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


def find_translation_pages(name: str, producer: str = "") -> List[str]:
    """在 vocaloid中文歌词wiki 上按歌名（+P主）找翻译页**候选**（排好序，最贴的在前）。

    用户 2026-10-20 要求：切到「歌词」页时，站上若有这首的翻译就自动预填。
    atwiki 现在挂着 Cloudflare、`requests` 一律被挡（见 `_fetch_atwiki_html()`），
    所以这里也要能借真浏览器（窗口开在屏幕外，用户看不见）。
    候选可能有多个（同名歌、曖昧さ回避页），由调用方逐个核对后再用。
    """
    if is_empty(name):
        return []
    try:
        return find_at_wiki_pages(name, candidate_urls("vocaloidchly", name, producer),
                                  producer, web_search=False)
    except Exception as e:                    # noqa: BLE001 - 查不到就当没有翻译
        logging.info("在 vocaloid中文歌词wiki 搜翻译页失败：%s", e)
        return []


def find_translation_page(name: str, producer: str = "") -> Optional[str]:
    """在 vocaloid中文歌词wiki 上按歌名（+P主）找翻译页，返回页面地址；找不到返回 None。"""
    pages = find_translation_pages(name, producer)
    return pages[0] if pages else None


def get_page_lyrics_plain(url: str, name: str = "") -> Optional[Lyrics]:
    """预填用：按地址取歌词，与「从链接填充」走同一条路（`get_page_lyrics()`）。

    早先这里只走 `requests`、被挡就放弃（怕「顺手预填」弹出浏览器）；可 atwiki 现在
    **只有**真浏览器能过 Cloudflare，于是「预填」永远拿不到正文 —— 改成跟「从链接填充」
    一样退回 `utils/browser_fetch.py`（那个窗口开在屏幕外，不会打扰用户）。

    「这一页是不是这首歌」由调用方用 `page_matches_song()` 自己核对（`prefetch_translation()`
    要区分「取不到页面」与「同名的另一首歌」：前者只填链接，后者什么都不填）。
    """
    return get_page_lyrics(url, name)


def _title_rank(title: str, name: str, producer: str) -> Optional[int]:
    """这个页面名与要找的歌有多贴（越小越优先）；不是这首歌返回 None。

    `ピトフーイ` 在初音ミク Wiki 上有三页：`ピトフーイ/ナナホシ管弦楽団`、`ピトフーイ`（曖昧さ回避页）、
    `ピトフーイ/TakoyakiKZY`（本曲）。搜索结果是按站上顺序给的，曖昧さ回避页正好排在中间 ——
    以前「标题对得上就返回」，于是抓到了曖昧さ回避页（用户 2026-11-02 报的《林鵙鹟(TakoyakiKZY)》
    读不到 `pages/56985.html`）。所以按「越贴越前」排：
    ① `歌名/P主` ② 其他 `歌名/<P主>` ③ 裸 `歌名`。
    """
    stripped = PAGE_SIZE_SUFFIX_RE.sub("", title).strip()
    if not stripped:
        return None
    if producer and stripped == f"{name}/{producer}":
        return 0
    if stripped.startswith(f"{name}/"):
        return 1
    if stripped == name:
        return 2
    return None


def find_at_wiki_pages(name: str, urls: List[str], producer: str,
                       web_search: bool = True, browser: bool = True) -> List[str]:
    """在 atwiki 的搜索结果里找出**所有**可能是这首歌的页面，按贴合度排好。

    排序见 `_title_rank()`（`歌名/P主` → 其他 `歌名/<P主>` → 裸 `歌名`）；同一档保持站上的
    文档顺序。取不到正文的页面由调用方逐个试（见 `get_at_wiki_body()`）。
    `browser=False` 时被 Cloudflare 挡住也不开真浏览器（见 `_fetch_atwiki_html()`）。

    **一有候选就停**：`urls` 里排在前面的那几个是 `/list` 的不同排序（见 `candidate_urls()`），
    已经选出页面后没必要再去问后面的全文搜 —— 那个地址对 `requests` 与 `curl` 都是 403，
    只能退到真浏览器，为一个已有答案的问题白等一分钟的正是它。
    """
    ranked: List[Tuple[int, int, str]] = []
    seen: Set[str] = set()
    for url in urls:
        html = _fetch_atwiki_html(url, browser=browser)
        if not html:
            continue
        wiki = (urlparse(url).path.split("/")[1:2] or [""])[0]
        page_url = (f"https://w.atwiki.jp/{wiki}/pages/{{}}.html" if wiki
                    else CHLY_PAGE_URL)
        for page_name, found_url in _result_links(html, page_url):
            rank = _title_rank(page_name, name, producer)
            if rank is None or found_url in seen:
                continue
            seen.add(found_url)
            ranked.append((rank, len(ranked), found_url))
        if ranked:
            break
    if not ranked and web_search:
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
                        found_url = page_url.format(match.group(1) or match.group(2))
                        if found_url not in seen:
                            seen.add(found_url)
                            ranked.append((3, len(ranked), found_url))
        except Exception:
            logging.debug("External AtWiki search fallback failed.", exc_info=True)
    return [found_url for _rank, _order, found_url in sorted(ranked)]


def find_at_wiki_page(name: str, urls: List[str], producer: str,
                      web_search: bool = True, browser: bool = True) -> Optional[str]:
    """在 atwiki 的搜索结果里找页面地址（最贴的那一个）。

    `web_search=False` 时不再退到 DuckDuckGo 外网搜索 —— 「切到歌词页时顺手查一下有没有翻译」
    那一步用它：查不到就该立刻返回，不能为了它多等一次外网搜索。
    Cloudflare 挡住时由 `_fetch_atwiki_html()` 借真浏览器取，所以 `urls` 用哪个地址都行；
    `browser=False` 时连浏览器都不借（见那里的说明）。
    """
    pages = find_at_wiki_pages(name, urls, producer, web_search=web_search, browser=browser)
    return pages[0] if pages else None



def get_vocaloid_collection_info(
        name: str, producer: str = "") -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """VocaDB 没记活动时，去初音ミク Wiki 顺手看一眼这首是不是 VOCALOID Collection 参赛曲。

    2026-11-02 改：**不借真浏览器**。用户报「该去 B 站截图的时候却在请求 atwiki」——
    这里的 `/search` 对 `requests` 与 `curl` 都是 403（实测），借浏览器只是白等 60 秒，
    还把封面截图要用的那个浏览器占住。改成先按**页面名**查 `/list`（curl 就能 200），
    查不到就当场放弃：这只是「顺手补个赛道 / 名次」，VocaDB 本来就说这首没有活动。
    """
    logging.info("VocaDB 没记活动，去初音ミク Wiki 查这首是不是 VOCALOID Collection 参赛曲（不借浏览器）")
    list_url = LIST_URLS["hmiku"]
    try:
        found = find_at_wiki_page(name, [list_url.format(quote(name, safe=""))],
                                  producer, web_search=False, browser=False)
        if found is None:
            return None
        page = _atwiki_page_text(found)
        if page is None:
            return None
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


def _atwiki_page_text(url: str) -> Optional[str]:
    """取一页 atwiki 的 HTML（`requests` → curl，**不开浏览器**）；取不到返回 None。"""
    try:
        response = http_get(url, use_proxy=True, timeout=ATWIKI_TIMEOUT)
        response.raise_for_status()
        if not _challenge_or_error(response):
            return response.text or ""
        logging.info("AtWiki 页面被挡（HTTP %s），改用 curl 再试：%s",
                     getattr(response, "status_code", "?"), url)
    except Exception as e:                    # noqa: BLE001 - 网络问题一律退回 curl
        logging.info("AtWiki 页面请求失败（%s），改用 curl 再试：%s", e, url)
    return browser_fetch.curl_html(url)


def get_vocaloid_collection(name: str, producer: str = "") -> Optional[str]:
    info = get_vocaloid_collection_info(name, producer)
    return info[0] if info else None


def _drop_lead_title_div(soup) -> None:
    """拆掉正文开头那一格「加粗的中文歌名」（`<span style="font-weight: bold;">`）。

    atwiki 的页面把中文歌名单独写成一格、整格加粗（实测 pages/5168《クラウドライダー》里就是
    `<div><span style="font-weight: bold;">Cloud Rider</span></div>`）。它既不是日文歌名（页面标题是
    日文），也不是歌词，转换纯文本后就和歌词连在一起 —— 中文栏第一行于是多出一句歌名，
    整首往下一行错位（用户 2026-11-02 报的「排版老是错」）。只拆**第一个**整格都加粗的格子。
    """
    for div in soup.find_all("div"):
        text = div.get_text().strip()
        if not text or len(text) > 40:
            continue
        bold = div.find(["b", "strong"])
        if bold is None:
            bold = div.find("span", style=lambda value: bool(value) and "font-weight" in value)
        if bold is not None and bold.get_text().strip() == text:
            div.decompose()
            return


def _wikibody_text(html: str):
    """页面里 `#wikibody` 的纯文本（去掉「最終更新」那行）；取不到返回 None。

    返回 `(正文, soup, 譯註原文)`：第三项是 `<hr>` 之后被切掉的那一段（里面就是
    `注1：…` 这类注文，见 `parse_note_lines()`）。
    """
    soup = BeautifulSoup(html, "html.parser")
    # remove last modify message / auto-generated site search link from body
    for elem in soup.find_all("div", attrs={'class': ['atwiki-lastmodify', 'atwiki-page-keyword']}):
        elem.decompose()
    _drop_table_of_contents(soup)
    node = soup.find("div", {"id": "wikibody"})
    if node is None:
        return None, soup, ""
    _drop_lead_title_div(node)
    notes = _drop_notes_after_rule(node)
    return node.text, soup, notes


def _drop_notes_after_rule(node) -> str:
    """拆掉正文里第一条 `<hr>` **之后**的「譯註 / 譯者說明」块，并把它**返回**出来。

    2026-10-21 实测 vocaloidchly《171》（pages/3236.html）：歌词段落到此为止，
    再往下是一条 `<hr />`，然后是译者写的「東北地方太平洋沖地震應援曲。…」
    「標題的171是『災害用伝言ダイヤル』的號碼。…」—— 这些说明被当成中文歌词填进
    「中文」栏（用户报）。chly / hmiku 的译注都写在 `<hr>` 之后，所以按分割线整段切掉；
    分割线之前要是没几行字（万一有人把它当装饰线放在正文开头），就不切，宁可不清理。

    返回切掉的文本：`parse_note_lines()` 要从中取出 `注1：…` 这类注文，把正文里的
    `(*注1)` 记号换成行内脚注（用户 2026-11-01 要求按 <https://voca.wiki/4> 的写法）。
    """
    rule = node.find("hr")
    if rule is None:
        return ""
    def inside(elem) -> bool:
        return elem is not None and elem.find_parent("div", {"id": "wikibody"}) is not None

    tail_text = "".join(str(text) for text in rule.find_all_next(string=True) if inside(text.parent))
    total = node.get_text()
    head = total[: len(total) - len(tail_text)] if tail_text else total
    if len([line for line in head.split("\n") if line.strip()]) < _NOTES_RULE_MIN_HEAD:
        return ""
    tail = [elem for elem in rule.find_all_next() if inside(elem)]
    for elem in tail:
        elem.decompose()
    rule.decompose()
    return tail_text


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


def video_ids_in_html(html: str) -> Set[str]:
    """页面里出现的稿件编号（小写比较用）：`{'sm10349470'}` / `{'bv1dxgb6ee78'}`。"""
    ids: Set[str] = set()
    for match in _PAGE_VIDEO_ID_RE.finditer(str(html or "")):
        value = next((group for group in match.groups() if group), "")
        if value:
            ids.add(value.casefold())
    return ids


def _video_service(video_id: str) -> str:
    """稿件编号属于哪个站：`nico` / `bilibili` / `youtube`（认不出返回空串）。"""
    value = str(video_id or "").strip().casefold()
    if re.fullmatch(r"(?:sm|nm|so)\d{4,}", value):
        return "nico"
    if re.fullmatch(r"bv[0-9a-z]{10}", value):
        return "bilibili"
    if re.fullmatch(r"[0-9a-z_-]{11}", value):
        return "youtube"
    return ""


def page_matches_song(html: str, video_ids: Sequence[str] = ()) -> bool:
    """这一页是不是**这首歌**（按页面里嵌的稿件编号核对）。

    atwiki 上同名的歌有好几首，按歌名搜只能搜到「名字对得上」的页面。2026-10-25 实测：
    生成 voca.wiki《潘多拉》（buzzG / パンドラ / sm39484089 / 鏡音リン / 2021）时，
    「切到歌词页顺手查一次翻译」命中了 pages/2231.html —— 那是**やしきん 的《パンドラ》**
    （sm10349470 / 初音ミク / 2010），于是歌词页预填了一份跟这首歌毫无关系的译文
    （用户报「自动填充一个根本不是这首曲子的翻译」）。两页标题都只写「パンドラ」，
    光看标题分不出来，但**页面里嵌的稿件编号不同**。

    按站点分别比较：页面里嵌了 niconico 稿件、我们手上也有 niconico 编号，两边必须有一支
    对得上（「不同」= 另一首歌）；页面只有 b 站转载、而只有 nico 编号可比时不算数。
    页面没嵌稿件、或我们没有可比编号（VocaDB 没记 PV）时返回 True（无法核对，按老样子放行）。
    """
    found = video_ids_in_html(html)
    expected = {str(value or "").strip().casefold()
                for value in video_ids if str(value or "").strip()}
    if not found or not expected:
        return True
    for service in ("nico", "bilibili", "youtube"):
        mine = {value for value in found if _video_service(value) == service}
        wanted = {value for value in expected if _video_service(value) == service}
        if mine and wanted and not (mine & wanted):
            return False
    return True


def parse_page(html: str, name: str = "") -> Optional[Lyrics]:
    """把**一个 AtWiki 页面**的 HTML 解析成 Lyrics（资讯表头 / 翻译者 / 歌词正文）。

    `name` 不给时用页面标题当歌名（`parse_body` 要用它定位正文起点）。
    只解析，不发请求 —— 生成流程与歌词页「从链接填充」共用这一份口径。

    `(*注1)` 这类记号会换成 `<ref>译者注：…</ref>`（注文取自页面末尾的 `注1：…`，见
    `parse_note_lines()`）；`注N：…` 那几行本身不是歌词，从正文里摘掉。
    """
    text, soup, notes_text = _wikibody_text(html)
    if text is None:
        return None
    staff, body = parse_body(name or page_title(soup), text)
    translator = next((value for key, value in staff if key in ("翻译", "翻譯")), "")
    # 表头常写成 `翻譯：cyataku(不當之處敬請指正)` —— 括号里那句是自述，不是名字
    # （用户 2026-10-25 报的那条）。
    translator = strip_translator_note(translator)
    body = to_note_refs_from_markers(_drop_note_lines(body), parse_note_lines(notes_text))
    return Lyrics(staff=staff, source_name=ATWIKI_SOURCE_NAME, source_url="",
                  lyrics_chs=body, translator=translator)


def _drop_note_lines(body: str) -> str:
    """正文末尾的 `注1：…` 那几行不是歌词（页面没有 `<hr>` 时会跟正文混在一起）。"""
    lines = [line for line in str(body or "").split("\n") if not NOTE_LINE_RE.match(line)]
    return "\n".join(lines).strip("\n")


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


def is_disambiguation_page(html: str) -> bool:
    """这一页是不是**曖昧さ回避**（同名条目的目录页，没有任何歌词）。

    实测 `hmiku/pages/57904.html`（`ピトフーイ`）：`<meta name="description" content="【登録タグ:曖昧さ回避】
    曖昧さ回避のためのページ …">`，正文是一串指向 `歌名/P主` 的链接 —— 以前它被当成歌词页抓下来，
    《林鵙鹟(TakoyakiKZY)》就「读不到」真正的 `pages/56985.html`（用户 2026-11-02 报）。

    ⚠️ 不能只看「页面里出现曖昧さ回避」：普通曲目页的侧栏也列着一堆「…曖昧さ回避」的链接
    （实测 56985 就有），所以只认**登录标签 / 说明句**这两种固定写法。
    """
    text = str(html or "")
    if not text:
        return False
    return any(mark in text for mark in DISAMBIGUATION_MARKS)


DISAMBIGUATION_MARKS = ("【登録タグ:曖昧さ回避】", "曖昧さ回避のためのページ")
MAX_ATWIKI_CANDIDATES = 3            # 最多试几个候选页（每个都要真取一次 HTML，不能无限试）



def get_at_wiki_body(name: str, urls: List[str], lang: str, producer: str,
                     video_ids: Sequence[str] = ()) -> Optional[Lyrics]:
    """按歌名搜 atwiki 页面并读出正文：候选页**逐个试**，跳过不像这首歌的那些。

    候选顺序见 `find_at_wiki_pages()`（`歌名/P主` 最前）。每个候选页要过三道关：
    ① 不是**曖昧さ回避**页（`ピトフーイ` 那种目录页没有任何歌词，正文是一片导航垃圾）；
    ② `page_matches_song()` 认得这是同一支稿件（同名歌有好几首）；
    ③ 解析得出正文。任何一关不过就试下一个，日志里写清原因。
    """
    try:
        pages = find_at_wiki_pages(name, urls, producer)
        if not pages:
            return None
        attempted = 0
        for url in pages[:MAX_ATWIKI_CANDIDATES]:
            html = fetch_page_html(url)
            if html is None:
                continue
            if is_disambiguation_page(html):
                logging.info("搜到的 AtWiki 页面是曖昧さ回避页，跳过：%s", url)
                continue
            if not page_matches_song(html, video_ids):
                logging.info("搜到的 AtWiki 页面不是这首歌（稿件编号对不上），跳过：%s", url)
                continue
            attempted += 1
            lyrics = parse_page(html, name)
            if lyrics is None or is_empty(str(lyrics.lyrics_chs or "").strip()):
                logging.info("AtWiki 页面里没解析出正文，跳过：%s", url)
                continue
            logging.debug("At wiki url " + url)
            lyrics.source_url = shorten_url(url)
            if not lyrics.translator:
                # 生成流程里「页面没写翻译者」要显眼（旧实现就是这个行为，别悄悄变成空）
                lyrics.translator = "ERROR!"
            return lyrics
        if not attempted:
            logging.info("AtWiki 上没找到这首歌的页面（共 %d 个候选）", len(pages))
        return None
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
        # 「翻譯：名字」那一格后面常紧跟译者声明（`(取用翻譯前請注意首頁的翻譯使用禮節…)`），
        # 它在同一个 `<div>` 里、和表头一起转换成了纯文本 —— 一起算进表头，别让它进歌词。
        while divider < len(text):
            end = text.find("\n", divider + 1)
            end = len(text) if end == -1 else end
            if not ATWIKI_NOTICE_LINE_RE.match(text[divider + 1:end]):
                break
            divider = end
        header = text[:divider]
        body = text[divider:]
    keywords = ["ブロマガより転載", "\n歌詞\n", "\n" + name + "\n"]
    index = max([body.find(k) for k in keywords])
    if index != -1:
        # 定位到那一行之后：`find("\n", index)` 取行尾，再往后移一位
        index = body.find("\n", index) + 1
        body = body[index:]
    return parse_at_wiki_header(header), parse_at_wiki_body(body, name)


def get_japanese_lyrics(name: str, producer: str = "",
                        video_ids: Sequence[str] = ()) -> str:
    logging.info(_("jap_atwiki"))
    res = get_at_wiki_body(name, candidate_urls("hmiku", name, producer),
                           "Japanese", producer, video_ids=video_ids)
    return res.lyrics_chs if res else ""


def get_chinese_lyrics(name: str, producer: str = "",
                       video_ids: Sequence[str] = ()) -> Optional[Lyrics]:
    logging.info(_("chs_atwiki"))
    return get_at_wiki_body(name, candidate_urls("vocaloidchly", name, producer),
                            "Chinese", producer, video_ids=video_ids)
