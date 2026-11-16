"""榜单条目的 nico 侧数据：榜单视频本身的元数据、按曲名找稿件、补齐每条的名次数据。

用户 2026-11-15 要求新增「生成榜单条目」功能（侧栏「刊」）。这一层只做**只读查询**，
不生成任何 wikitext（那是 `utils/chart_wikitext.py`）：

* `thumbinfo()` —— `ext.nicovideo.jp/api/getthumbinfo/<id>`（不需要登录、不受 Cloudflare 影响）
  给出标题 / 简介 / 投稿时间 / 时长 / 上传者 / 播放数 / 评论数 / 收藏数 / 标签；
* `search_videos()` —— niconico 官方快照搜索接口（`snapshot.search.nicovideo.jp`，JSON），
  一次带回 `contentId` / 标题 / 投稿时间 / 标签；**查无结果是 200 + `totalCount:0`**；
* `search_ids()` —— snapshot 优先，HTML 搜索页兜底。⚠️ nico 的 HTML 搜索在**查无结果时回 404**，
  这是「没有结果」不是故障，所以这里把 404 当空表、只写 DEBUG 日志（用户 2026-11-15 报的满屏 404）；
* `find_ranking_video()` —— 先读站上已有页面的 `|id`，再退到搜索，最后交给调用方问用户；
* `fill_row()` —— 给识别出来的一行补 `id` / `时间` / `P主` / `歌姬`：
  分级查询 + 打分（标题相似度、歌姬 tag、投稿时间窗），拿不准就留空让人填。

时间口径（用户 2026-11-15 定）：榜单页的 `|发布时间` 用 **UTC+8**，每条榜单的 `|时间`
用稿件 `first_retrieve` 的 **JST 原样**（实测 #52 / #813 两代页面都这么写）。
"""
import difflib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from utils import user_dict, wiki_api
from utils.helpers import http_get
from utils.string import title_match_key

THUMBINFO_URL = "https://ext.nicovideo.jp/api/getthumbinfo/{}"
SEARCH_URL = "https://www.nicovideo.jp/search/{}"
WATCH_URL = "https://www.nicovideo.jp/watch/{}"
# 官方快照搜索（JSON）：`_sort` 是必填参数，缺了会 400 QUERY_PARSE_ERROR
SNAPSHOT_URL = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
SNAPSHOT_FIELDS = ("contentId,title,startTime,viewCounter,commentCounter,mylistCounter,"
                   "lengthSeconds,tags")
# 官方 nvapi（删稿 / 非公開也返回）：投稿时间与**上传者名字**都在这里，用来补 `P主`
NVAPI_URL = "https://nvapi.nicovideo.jp/v1/videos?watchIds={}"
NVAPI_HEADERS = {"X-Frontend-Id": "6", "X-Frontend-Version": "0"}

REQUEST_TIMEOUT = 25
JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))
# 标题相似度：几乎一模一样就采信；前两名分差小于它就当「拿不准」
TITLE_CLEAR = 0.98
TITLE_MATCH_MIN = 0.75
SCORE_GAP = 0.15

# 视频编号前缀：`sm` 是绝大多数，`nm`（旧投稿）/ `so`（旧渠道）也认
ID_RE = re.compile(r"\b((?:sm|nm|so)\d{4,})\b")
# `|id = 8763104` 这类只写数字的写法
BARE_ID_RE = re.compile(r"^\s*(\d{4,})\s*$")
# 上传者昵称尾巴上的 `(ID:12345)`
UPLOADER_ID_RE = re.compile(r"\s*\(ID:[^)]*\)\s*$")
# 简介里的「○集計期間：2009年11月1日0時～2009年11月8日0時」（#813 写成「○ 集計期間」换行再写
# 「　・UTAU ：2024年6月1日0時～…」），所以不要求「集計期間」紧跟其后，取出现的第一个区间。
PERIOD_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*時"
    r"\s*[～~〜\-—]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*時")
# 简介拍平之后要断回来的位置（站上是「○…」一行一条、「PL:」/「バックナンバー」各起一行）。
# 站上这几个位置前面还空一行，`_BLANK_BEFORE` 里列出。
DESCRIPTION_BREAK_RE = re.compile(
    r"(?=○|PL[:：]|月刊|(?<!の)バックナンバー|UTAU情報交換所|K!!te Playlist|"
    r"週刊UTAUランキングまとめ|計算式\()")
BLANK_BEFORE = ("PL:", "PL：", "UTAU情報交換所", "K!!te Playlist", "週刊UTAUランキングまとめ")
# bilibili 搜索结果里的标题带 `<em class="keyword">` 高亮标记
PLAIN_TITLE_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ChartSpec:
    """一种榜单。以后加 V 榜 / 月刊就往 `CHARTS` 里加一项。"""
    key: str
    label: str                    # 界面上显示的名字（U榜）
    page_prefix: str              # 条目名前缀（周刊UTAU排行榜）
    search_term: str              # 站内搜索用的标题关键词（週刊UTAUランキング）
    kind: str                     # 导语里的「…的UTAU歌曲周刊」
    default_rule: str = "2008"    # 统计规则默认值

    def page_title(self, index: int) -> str:
        return f"{self.page_prefix}{index}"


CHARTS: Dict[str, ChartSpec] = {
    "utau": ChartSpec(key="utau", label="U榜", page_prefix="周刊UTAU排行榜",
                      search_term="週刊UTAUランキング", kind="UTAU歌曲周刊"),
}


# ---------------------------------------------------------------- 基础请求

def _get(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    """取一个站外页面/接口的正文（带浏览器 UA、走配置里的代理）。失败抛异常。"""
    response = http_get(url, use_proxy=True, retries=1, timeout=timeout)
    response.raise_for_status()
    return response.text or ""


def check_cancelled() -> None:
    """耗时循环里调一次：这一轮被用户放弃（清除对话记录 / 切功能）就抛 `RunCancelled`。

    放在 chart_nico 里是因为识别 / 抽帧 / 搜索都要用，而它们已经都依赖本模块；
    测试与终端模式下 `ui` 没启动，这里什么都不做。
    """
    try:
        from utils import ui
    except Exception:                             # noqa: BLE001 - 没有 ui 模块也不该崩
        return
    ui.check_run_cancelled()


def normalize_id(value: str) -> str:
    """`sm8763104` / `8763104` / 带网址的链接 → `sm8763104`（认不出返回空串）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    match = ID_RE.search(text)
    if match:
        return match.group(1)
    match = BARE_ID_RE.match(text)
    if match:
        return f"sm{match.group(1)}"
    return ""


# ---------------------------------------------------------------- getthumbinfo

def _tag_text(xml: str, name: str) -> str:
    match = re.search(rf"<{name}>(.*?)</{name}>", xml, re.S)
    if not match:
        return ""
    text = match.group(1)
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    return _unescape(text).strip()


def _unescape(text: str) -> str:
    return (str(text or "").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#039;", "'").replace("&apos;", "'")
            .replace("&amp;", "&"))


def parse_thumbinfo(xml: str, video_id: str = "") -> Optional[dict]:
    """`getthumbinfo` 的 XML → 字典；拿不到（削除 / 非公開）返回 None。"""
    text = str(xml or "")
    if 'status="ok"' not in text:
        return None
    date_jst = _parse_nico_date(_tag_text(text, "first_retrieve"))
    if date_jst is None:
        return None
    length = _parse_length(_tag_text(text, "length"))
    tags = [_unescape(tag) for tag in re.findall(r"<tag(?:\s[^>]*)?>(.*?)</tag>", text, re.S)]
    return {
        "id": _tag_text(text, "video_id") or video_id,
        "title": _tag_text(text, "title"),
        "description": _tag_text(text, "description"),
        "date_jst": date_jst,
        "date_utc8": date_jst.astimezone(CST),
        "length": length,
        "uploader": UPLOADER_ID_RE.sub("", _tag_text(text, "user_nickname")),
        "view": _int_or_zero(_tag_text(text, "view_counter")),
        "comment": _int_or_zero(_tag_text(text, "comment_num")),
        "mylist": _int_or_zero(_tag_text(text, "mylist_counter")),
        "tags": tags,
        "watch_url": _tag_text(text, "watch_url") or WATCH_URL.format(
            _tag_text(text, "video_id") or video_id),
    }


def _parse_nico_date(text: str) -> Optional[datetime]:
    text = str(text or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_length(text: str) -> int:
    """`23:34` / `1:02:03` → 秒数（认不出返回 0）。"""
    parts = [part for part in str(text or "").split(":") if part.isdigit()]
    if not parts:
        return 0
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _int_or_zero(text: str) -> int:
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else 0


def thumbinfo(video_id: str) -> Optional[dict]:
    """按编号取视频元数据（`sm` / `nm` / `so` 都认，纯数字也认）。"""
    identifier = normalize_id(video_id)
    if not identifier:
        return None
    try:
        return parse_thumbinfo(_get(THUMBINFO_URL.format(identifier)), identifier)
    except Exception as e:                       # noqa: BLE001 - 网络问题一律当「取不到」
        logging.warning("取%s的niconico元数据失败：%s", identifier, e)
        return None


# ---------------------------------------------------------------- 站内搜索

def parse_search_ids(html: str, limit: int = 8) -> List[str]:
    """搜索页 HTML 里的稿件编号（按出现顺序、去重）。"""
    found: List[str] = []
    for match in ID_RE.finditer(str(html or "")):
        identifier = match.group(1)
        if identifier not in found:
            found.append(identifier)
        if len(found) >= limit:
            break
    return found


def _start_time(text: str) -> Optional[datetime]:
    value = str(text or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_snapshot(payload: dict) -> List[dict]:
    """快照搜索接口的 JSON → 候选列表（`{id, title, date_jst, view, comment, mylist, tags}`）。"""
    items = ((payload or {}).get("data") or []) if isinstance(payload, dict) else []
    found: List[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("contentId") or "").strip()
        if not identifier:
            continue
        tags = str(item.get("tags") or "")
        found.append({
            "id": identifier,
            "title": str(item.get("title") or ""),
            "date_jst": _start_time(item.get("startTime")),
            "view": _int_or_zero(item.get("viewCounter")),
            "comment": _int_or_zero(item.get("commentCounter")),
            "mylist": _int_or_zero(item.get("mylistCounter")),
            "tags": [tag for tag in tags.split() if tag],
        })
    return found


def search_videos(keyword: str, targets: str = "title", limit: int = 10) -> List[dict]:
    """用 niconico 官方快照搜索接口搜稿件 → 候选列表（失败返回空表，不抛异常）。

    实测（2026-11-15）：`q=【UTAU連続音】Lilium　【重音テト】` 只回 1 条、正是 `sm8673630`，
    还带回 `startTime=2009-11-01T02:39:15+09:00`（就是砖块里的 `|时间`）。
    查无结果时接口是 **HTTP 200 + `totalCount: 0`**，不像网页搜索那样 404。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    params = {"q": keyword, "targets": targets or "title", "fields": SNAPSHOT_FIELDS,
              "_sort": "-viewCounter", "_limit": max(1, int(limit or 10)),
              "_context": "vocawiki-chart"}
    try:
        response = http_get(SNAPSHOT_URL, use_proxy=True, params=params, retries=1,
                            timeout=REQUEST_TIMEOUT)
    except Exception as e:                       # noqa: BLE001 - 搜不到就当没有
        logging.debug("快照搜索「%s」失败：%s", keyword, e)
        return []
    if response.status_code != 200:
        logging.debug("快照搜索「%s」返回%s", keyword, response.status_code)
        return []
    try:
        payload = response.json()
    except ValueError:
        logging.debug("快照搜索「%s」返回的不是JSON", keyword)
        return []
    return parse_snapshot(payload)


def search_ids(keyword: str, limit: int = 8) -> List[str]:
    """按关键词搜 niconico → 稿件编号候选（快照接口优先，HTML 搜索页兜底）。

    ⚠️ HTML 搜索页**查无结果时回 404**（实测 `zzzzqqqqxxx` 每次 404、`Lilium` 每次 200），
    那是「没有结果」而不是故障 —— 只写 DEBUG，绝不 warning（用户 2026-11-15 报的满屏 404）。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    found = [item["id"] for item in search_videos(keyword, limit=limit)]
    if found:
        return found[:limit]
    try:
        html = _get(SEARCH_URL.format(quote(keyword, safe="")))
    except Exception as e:                       # noqa: BLE001
        status = getattr(getattr(e, "response", None), "status_code", 0)
        if status == 404:
            logging.debug("niconico没有「%s」的结果", keyword)
        else:
            logging.debug("搜索niconico「%s」失败：%s", keyword, type(e).__name__)
        return []
    return parse_search_ids(html, limit)


def _similar(left: str, right: str) -> float:
    """两段文本的相似度（NFKC + 去空白标点后的包含关系与长度比）。"""
    import difflib

    left_key, right_key = title_match_key(left), title_match_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    if left_key in right_key or right_key in left_key:
        return 0.9
    return difflib.SequenceMatcher(None, left_key, right_key).ratio()


def best_match(title: str, candidates: Sequence[str], threshold: float = 0.55) -> Optional[dict]:
    """在候选编号里挑**标题最像**的那一条（返回它的 `thumbinfo`，都不像返回 None）。"""
    scored: List[Tuple[float, dict]] = []
    for identifier in candidates:
        info = thumbinfo(identifier)
        if info is None:
            continue
        scored.append((_similar(title, info.get("title") or ""), info))
        if scored[-1][0] >= 0.99:                 # 一模一样就不用再看后面的
            break
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored[0][0] >= threshold else None


# ---------------------------------------------------------------- 榜单视频

def parse_index(title: str) -> int:
    """标题里的期数（`週刊UTAUランキング　#52` → 52；认不出返回 0）。"""
    match = re.search(r"#\s*(\d{1,4})", str(title or ""))
    return int(match.group(1)) if match else 0


def page_video_id(spec: ChartSpec, index: int) -> str:
    """站上已有这一期时它的 `|id`（没有这一页 / 没写 `|id` 都返回空串）。"""
    try:
        text = wiki_api.fetch_pages_text([spec.page_title(index)]).get(spec.page_title(index), "")
    except Exception as e:                       # noqa: BLE001 - 查不到就当没有
        logging.info("读站上「%s」失败：%s", spec.page_title(index), e)
        return ""
    match = re.search(r"\|\s*id\s*=\s*([^\n|}]*)", text or "")
    return normalize_id(match.group(1)) if match else ""


def find_ranking_video(spec: ChartSpec, index: int) -> Tuple[str, str]:
    """定位某一期的榜单视频 → `(编号, 来源说明)`；找不到返回 `("", "")`。

    顺序：站上已有页面的 `|id` → 快照搜索（标题里带 `#期数` 的那条）→ HTML 搜索 → 放弃
    （由调用方问用户要链接，用户 2026-11-15 选的兜底）。
    """
    identifier = page_video_id(spec, index)
    if identifier:
        return identifier, "站上已有页面"
    for candidate in search_videos(f"{spec.search_term} #{index}", limit=10):
        if parse_index(candidate.get("title") or "") == int(index):
            return candidate["id"], "niconico搜索"
    for candidate in search_ids(f"{spec.search_term} #{index}", limit=10):
        info = thumbinfo(candidate)
        if info is None:
            continue
        if parse_index(info.get("title") or "") == index:
            return candidate, "niconico搜索"
    return "", ""


def page_bilibili(spec: ChartSpec, index: int) -> str:
    """站上这一期页面里写的 B站转载 BV 号（没有这一页 / 没写都返回空串）。"""
    title = spec.page_title(index)
    try:
        text = wiki_api.fetch_pages_text([title]).get(title, "")
    except Exception as e:                       # noqa: BLE001 - 查不到就当没有
        logging.debug("读站上「%s」失败：%s", title, e)
        return ""
    match = re.search(r"BilibiliVideo\s*\|\s*id\s*=\s*(BV[0-9A-Za-z]+)", text or "")
    return match.group(1) if match else ""


def parse_period(description: str) -> Tuple[str, str]:
    """简介里的「○集計期間」→ `(起始, 终止)`，格式与站上一致（`2009年11月08日 00:00`）。

    注意这是**集計期間**（题头的 `|起始时间` / `|终止时间`），不是「集計対象動画期間」。
    """
    match = PERIOD_RE.search(str(description or ""))
    if not match:
        return "", ""
    start = _datetime_jst(match.group(1), match.group(2), match.group(3), match.group(4))
    end = _datetime_jst(match.group(5), match.group(6), match.group(7), match.group(8))
    if not start or not end:
        return "", ""
    return _format_cn(start), _format_cn(end)


def parse_target_period(description: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """简介里的「○集計対象動画期間」→ `(起, 止)`（JST）；没有就用「○集計期間」。"""
    text = str(description or "")
    marker = re.search(r"集計対象動画期間", text)
    if marker:
        match = PERIOD_RE.search(text, marker.end())
        if match:
            return (_datetime_jst(match.group(1), match.group(2), match.group(3), match.group(4)),
                    _datetime_jst(match.group(5), match.group(6), match.group(7), match.group(8)))
    match = PERIOD_RE.search(text)
    if not match:
        return None, None
    return (_datetime_jst(match.group(1), match.group(2), match.group(3), match.group(4)),
            _datetime_jst(match.group(5), match.group(6), match.group(7), match.group(8)))


def _datetime_jst(year, month, day, hour) -> Optional[datetime]:
    try:
        return datetime(int(year), int(month), int(day), int(hour), tzinfo=JST)
    except (TypeError, ValueError):
        return None


def _format_cn(value: datetime) -> str:
    return f"{value.year}年{value.month:02d}月{value.day:02d}日 {value.hour:02d}:{value.minute:02d}"


def format_publish(value: datetime) -> str:
    """`发布时间` 的写法（UTC+8，站上 #52 就是 UTC+8）。"""
    return _format_cn(value.astimezone(CST))


def format_row_time(value: datetime) -> str:
    """每条榜单 `|时间` 的写法（JST 原样，与站上 #52 / #813 一致）。"""
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M")


def split_description(description: str) -> str:
    """把拍平的简介按站上的分行习惯拆开（`○…` / `PL:` / `バックナンバー` 各起一行）。

    站上 `PL:` / `UTAU情報交換所` / `K!!te Playlist` / `週刊UTAUランキングまとめ` 前面还空一行，
    这里一并还原（简介原文里的换行原样保留 —— 拍平的只是老投稿）。
    """
    text = str(description or "").strip()
    if not text:
        return ""
    pieces = [piece.strip("\u3000\n") for piece in DESCRIPTION_BREAK_RE.split(text)]
    pieces = [piece for piece in pieces if piece.strip()]
    if len(pieces) <= 1:
        return text
    result = pieces[0]
    for piece in pieces[1:]:
        separator = "\n\n" if piece.startswith(BLANK_BEFORE) else "\n"
        # 原文里本来就有换行（新版投稿）时别再插一次
        result = result.rstrip("\n") + separator + piece
    return result


# ---------------------------------------------------------------- 曲名清洗

# 视频标题里的 `【…】` 段（`【UTAU連続音】Lilium　【重音テト】` 里两段都不是曲名）
SONG_BRACKET_RE = re.compile(r"【[^】]{0,60}】")
SPACE_RE = re.compile(r"[ \u3000]+")


def clean_song_title(text: str) -> str:
    """视频标题 → 曲名：去掉 `【…】` 段（用户 2026-11-15 要求）。

    站上 #52 第 1 名的视频标题是 `【UTAU連続音】Lilium　【重音テト】`，条目里只写 `Lilium`；
    现在表格里写着 `【重音テト】世界の…`，所以入库时就清掉。
    整条都在括号里（`【歌ってみた】`）时**保留原文**，不让曲名变空；
    只删 `【】`，`（）『』「」` 不动（`少年期（ドラえもんのやつ）`、`古川Pの『Alice』…` 都是原文）。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    stripped = SPACE_RE.sub(" ", SONG_BRACKET_RE.sub(" ", raw)).strip()
    return stripped or raw


def core_title(text: str) -> str:
    """搜索用的曲名（与 `clean_song_title` 同源，留着这个名字是为了以后好改规则）。"""
    return clean_song_title(text)


# ---------------------------------------------------------------- 补齐每条数据

def vocalist_lookup() -> Dict[str, str]:
    """已知歌姬名 → 站上写法（`重音テト` / `重音Teto` 都指向 `重音Teto`），只建一次。

    名字表借 `utils/name_converter.py`（VocaDB 那边的 `vocaloid_names` + 各引擎歌姬表，
    `JAPANESE_ALIASES` 已经并进 `vocaloid_names`），能认出来的才写进 `歌姬` 参数。
    """
    global _VOCALISTS
    if _VOCALISTS is None:
        from utils import name_converter

        table: Dict[str, str] = {}
        sources = [name_converter.vocaloid_names]
        sources.extend(characters for _engine, characters in name_converter.ENGINES)
        # 用户手填过的优先（`和音マコ` → `和音Mako` 这类只有用户知道该怎么写）
        sources.insert(0, user_dict.load().get("vocalists") or {})
        for source in sources:
            for key, value in source.items():
                table.setdefault(title_match_key(key), value)
                table.setdefault(title_match_key(value), value)
        _VOCALISTS = {key: value for key, value in table.items() if key}
    return _VOCALISTS


_VOCALISTS: Optional[Dict[str, str]] = None

# `【…】` / `（…）` 这类括起来的段（视频标题里通常是歌姬 / 引擎标签）
BRACKET_RE = re.compile(r"[【\[（(]([^】\]）)]{1,40})[】\]）)]")
# 片假名（判断要不要转罗马字）
KATAKANA_RE = re.compile(r"[\u30a0-\u30ff]")
# 标题括号里明显不是歌姬的标签（判断「片假名转罗马字」兜底时跳过）
NON_VOCALIST_TAGS = frozenset((
    "オリジナル", "オリジナル曲", "カバー", "歌ってみた", "演奏してみた", "踊ってみた",
    "utau", "vocaloid", "ボカロ", "新音源", "音源配布", "手描き", "手書き", "pv", "mmd",
    "full", "fullver", "フル", "short", "本家", "替え歌", "アレンジ", "remix",
))


def vocalists_from_tags(tags: Sequence[str]) -> str:
    """从 tag 里挑出歌姬名（`重音テト` → `重音Teto`），用「、」连起来。"""
    known = vocalist_lookup()
    found: List[str] = []
    for tag in tags or []:
        name = known.get(title_match_key(tag))
        if name and name not in found:
            found.append(name)
    return "、".join(found)


def _romaji_name(name: str) -> str:
    """片假名兜底：`和音マコ` → `和音Mako`（站上的写法就是「汉字 + 罗马字」）。

    站上已经有 `Category:和音Mako歌曲` 这种分类名，但角色页不一定存在，
    所以认不出时按这个规则拼一个，总比留着纯日文好（用户 2026-11-15）。
    """
    if not KATAKANA_RE.search(name):
        return ""
    try:
        from utils.japanese import kana_to_romaji
    except Exception:                            # noqa: BLE001 - 没有这个模块就放弃
        return ""
    pieces: List[str] = []
    for part in re.findall(r"[\u30a0-\u30ff]+|[^\u30a0-\u30ff]+", name):
        if KATAKANA_RE.search(part):
            reading = kana_to_romaji(part)
            if not reading or reading == part:
                return ""
            pieces.append(reading[:1].upper() + reading[1:])
        else:
            pieces.append(part)
    result = "".join(pieces)
    return result if result and result != name and " " not in result else ""


def normalize_vocalists(text: str) -> str:
    """把「歌姬」这一格归一成站上写法：`重音テト、初音ミク` → `重音Teto、初音未来`。

    顺序：用户手填过的（`user_dict`）→ `name_converter` 的表（含 `Category:UTAU角色歌曲`
    生成的那份）→ 片假名转罗马字兜底 → 实在认不出就原样保留（不瞎改）。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    from utils import name_converter

    found: List[str] = []
    for piece in re.split(r"[、,，/／]+", raw):
        name = piece.strip(" 　")
        if not name:
            continue
        value = (user_dict.lookup_vocalist(name)
                 or name_converter.name_to_chinese(name)
                 or name)
        if value == name:
            value = _romaji_name(name) or name
        if value not in found:
            found.append(value)
    return "、".join(found)


def vocalists_from_title(title: str) -> str:
    """从视频标题的括号里认歌姬：`【重音テト】La voix【UTAUカバー】` → `重音Teto`。

    卡片上没写歌姬时用它兜底（歌姬常常只在标题的 `【】` 里）。只在**歌姬表命中**时才认；
    表里没有时才退一步用「片假名转罗马字」，并且只对**第一个**括号试一次、
    跳过 `オリジナル` / `カバー` / `UTAU` 这类明显不是歌姬的标签。
    """
    found: List[str] = []
    known = vocalist_lookup()
    brackets = [match.group(1).strip() for match in BRACKET_RE.finditer(str(title or ""))]
    for inside in brackets:
        name = known.get(title_match_key(inside))
        if name and name not in found:
            found.append(name)
    if found:
        return "、".join(found)
    for inside in brackets[:1]:
        if not inside or inside.lower() in NON_VOCALIST_TAGS:
            continue
        if not KATAKANA_RE.search(inside):
            continue
        name = _romaji_name(inside)
        if name:
            return name
    return ""


def first_vocalist(text: str) -> str:
    """`重音Teto、初音未来` → `重音Teto`（搜索时只用一个歌姬当限定词）。"""
    for piece in re.split(r"[、,，/／]+", str(text or "")):
        if piece.strip():
            return piece.strip()
    return ""


def _score(row: dict, item: dict, window: Tuple[Optional[datetime], Optional[datetime]]
           ) -> Tuple[float, float, bool, bool]:
    """给候选打分 → `(总分, 标题相似度, 歌姬命中, 时间在窗口内)`。"""
    base = _similar(row.get("曲名") or row.get("title") or "", item.get("title") or "")
    bonus = 0.0
    tags = " ".join(item.get("tags") or [])
    vocalist_hit = any(piece and piece in tags
                       for piece in re.split(r"[、,，/／]+", str(row.get("歌姬") or "")))
    if vocalist_hit:
        bonus += 0.3
    when = item.get("date_jst")
    start, end = window
    in_window = bool(when and start and end
                     and start - timedelta(days=7) <= when <= end + timedelta(days=1))
    if in_window:
        bonus += 0.25
    return base + bonus, base, vocalist_hit, in_window


def fill_row(row: dict, search: bool = True,
             window: Tuple[Optional[datetime], Optional[datetime]] = (None, None)) -> dict:
    """给识别出来的一行补 `id` / `时间` / `P主` / `歌姬`（原地改，也返回它）。

    * 已经有 `id` → 直接取元数据；
    * 没有就分级查：`曲名` → `曲名 + 首个歌姬`（`targets=title,tags`）；
    * 打分挑最像的：标题相似度 + 歌姬 tag 命中 + 投稿时间落在期次窗口；
      前两名分差太小（同名曲有好几个稿件）就**不猜**，返回 `ambiguous=True` 让用户填；
    * 选中后用 `getthumbinfo` 复核一次（顺便拿权威投稿时间 / 上传者，已删除的跳过）。

    ⚠️ 以前还有一次 `曲名 + " UTAU"` 的搜索，实测**必 404**（查无结果），已经删掉。
    """
    title = str(row.get("曲名") or row.get("title") or "").strip()
    identifier = normalize_id(row.get("id") or "")
    info = thumbinfo(identifier) if identifier else None
    if info is None and search and title:
        info = _pick_candidate(row, title, window)
    if info is None:
        row.setdefault("id", identifier)
        return row
    if info.get("ambiguous"):
        row.setdefault("id", identifier)
        row["ambiguous"] = True
        return row
    row["id"] = str(info.get("id") or identifier)
    if not str(row.get("时间") or "").strip() and info.get("date_jst"):
        row["时间"] = format_row_time(info["date_jst"])
    if not str(row.get("P主") or "").strip():
        row["P主"] = info.get("uploader") or ""
    if not str(row.get("歌姬") or "").strip():
        row["歌姬"] = (vocalists_from_tags(info.get("tags") or [])
                      or vocalists_from_title(info.get("title") or ""))
    if not str(row.get("曲名") or "").strip():
        row["曲名"] = clean_song_title(info.get("title") or "")
    return row


def _queries(row: dict, title: str) -> List[Tuple[str, str]]:
    """分级查询：越靠前越精确（第一轮 `title` 就够用时最省事）。"""
    vocalist = first_vocalist(row.get("歌姬") or "")
    queries: List[Tuple[str, str]] = [(title, "title")]
    if vocalist:
        queries.append((f"{title} {vocalist}", "title,tags"))
    core = core_title(title)
    if core and core != title:
        queries.append((f"{core} {vocalist}".strip(), "title,tags"))
    return queries


def _pick_candidate(row: dict, title: str, window) -> Optional[dict]:
    """跑完分级查询后挑一条；拿不准就返回 `{"ambiguous": True}`。"""
    candidates: Dict[str, dict] = {}
    for keyword, targets in _queries(row, title):
        for item in search_videos(keyword, targets=targets, limit=10):
            candidates.setdefault(item["id"], item)
        if candidates:
            scored = sorted(((_score(row, item, window), item) for item in candidates.values()),
                            key=lambda pair: pair[0][0], reverse=True)
            best_score, best_base = scored[0][0][0], scored[0][0][1]
            if best_base >= TITLE_CLEAR:
                return _verify(scored[0][1]["id"])
    if not candidates:
        return None
    scored = sorted(((_score(row, item, window), item) for item in candidates.values()),
                    key=lambda pair: pair[0][0], reverse=True)
    best = scored[0]
    if best[0][1] < TITLE_MATCH_MIN:
        return None
    if len(scored) > 1 and best[0][0] - scored[1][0][0] < SCORE_GAP and best[0][1] < TITLE_CLEAR:
        # 同名曲有好几个稿件：宁可不填，让用户在表格里挑
        logging.debug("「%s」有多个候选（%s / %s），留空让人填",
                      title, best[1]["id"], scored[1][1]["id"])
        return {"ambiguous": True}
    return _verify(best[1]["id"])


def _verify(identifier: str) -> Optional[dict]:
    """用 getthumbinfo 复核：拿权威投稿时间 / 上传者，已删除的返回 None。"""
    info = thumbinfo(identifier)
    if info is not None and info.get("uploader") is None:
        owner = nvapi_owner(identifier)
        if owner:
            info = dict(info)
            info["uploader"] = owner
    return info


def nvapi_owner(identifier: str) -> str:
    """官方 nvapi 里的上传者名字（删稿 / 非公開 也有）；拿不到返回空串。

    老稿件 `getthumbinfo` 的 `user_nickname` 常常是空的（实测 `sm8673630`），
    而 nvapi 里 `video.owner.name` 有值（实测 `sm8780736` → `DJ横山`）。
    ⚠️ nvapi 的 `count.view` / `count.like` 是**累计值**，榜单要的是当周统计值，
    所以这里只取名字，绝不拿它的数字去覆盖得点 / 再生 / 评论 / 收藏 / 点赞。
    """
    identifier = normalize_id(identifier)
    if not identifier:
        return ""
    try:
        response = http_get(NVAPI_URL.format(identifier), use_proxy=True,
                            headers=dict(NVAPI_HEADERS), timeout=REQUEST_TIMEOUT)
        payload = response.json()
    except Exception as e:                       # noqa: BLE001 - 拿不到就算了
        logging.debug("nvapi取%s失败：%s", identifier, e)
        return ""
    items = ((payload or {}).get("data") or {}).get("items") or []
    if not items or not isinstance(items[0], dict):
        return ""
    video = items[0].get("video") or {}
    owner = video.get("owner") or {}
    return str(owner.get("name") or "").strip()


# ---------------------------------------------------------------- bilibili 转载

# 站上用的 BV 号；`bilibili` 搜索里偶尔给 `bv` 小写
BV_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")
# 搬运标题里必须写到的字样：`週刊VOCALOID #52` 是另一份榜（VOCALOID 榜），
# 拿它当 UTAU 榜的转载会写错链接，所以这里只认带 UTAU 的标题
BILIBILI_MUST = "utau"


def find_bilibili(spec: ChartSpec, index: int, ask=None) -> str:
    """找这一期的 B站转载 BV 号；找不到返回空串。

    用户 2026-11-15 报「#53 明明有 `BV11e411x7qq` 却识别不出来」。实测下来：
    搬运标题写的是 `週刊UTAU Ranking #53`，而 bilibili 的搜索对这套 2009 年的老搬运
    **基本搜不到**（`週刊UTAU Ranking #53` / `週刊UTAUランキング #53` / `周刊UTAU排行榜53`
    前十都没有；连 `週刊UTAU` 翻到第 3 页也没有；只有直接搜 BV 号才出来；
    相邻期的 `archive/related` 里也没有；搬运 UP 的空间接口要么限流、要么要 wbi 签名）。
    所以按这个顺序来：

    1. **站上已有页面**里写的 `{{BilibiliVideo|id=…}}`（最准）；
    2. 换几个更靠谱的关键词搜一遍（每个 1 次请求）；
    3. 都没有 → 由调用方传进来的 `ask()` 问用户一次（可留空），答案会被记住。
    """
    if not index:
        return ""
    existing = page_bilibili(spec, index)
    if existing:
        return existing
    # 搬运的标题写的是英文 `週刊UTAU Ranking #53`（不是站内搜索用的 `ランキング`），两种都试
    latin = str(spec.search_term).replace("ランキング", "Ranking")
    for keyword in (f"{latin} #{index}",
                    f"{spec.search_term} #{index}",
                    f"{spec.page_prefix} {index}",
                    f"UTAU周刊 {index}"):
        check_cancelled()
        if found := _search_bilibili(keyword, index):
            return found
    if ask is not None:
        answer = ask()
        found = BV_RE.search(str(answer or ""))
        if found:
            logging.info("B站转载用你填的%s", found.group(0))
            return found.group(0)
    logging.debug("第%s期的B站转载没搜到（试了4种关键词）", index)
    return ""


def _search_bilibili(keyword: str, index: int) -> str:
    """搜一次 bilibili，挑「标题里同时有 UTAU 与期数」的那条。"""
    try:
        from utils.producer_template import bilibili_search_items
    except Exception as e:                       # noqa: BLE001 - 移动端没有这些依赖
        logging.debug("bilibili搜索不可用：%s", e)
        return ""
    try:
        found = bilibili_search_items(keyword, limit=10)
    except Exception as e:                       # noqa: BLE001 - 搜不到就算了
        logging.debug("搜索bilibili「%s」失败：%s", keyword, e)
        return ""
    for item in found:
        name = PLAIN_TITLE_RE.sub("", str(item.get("title") or ""))
        lowered = name.lower()
        if BILIBILI_MUST not in lowered:
            continue
        if not _mentions_index(name, index):
            continue
        identifier = str(item.get("bvid") or "")
        if identifier.startswith("BV"):
            return identifier
    return ""


def _mentions_index(title: str, index: int) -> bool:
    """标题里有没有「第 index 期」（`#52` / `第52期` / `♫052`，别把 `#152` 当成 52）。"""
    patterns = (rf"#\s*0*{index}(?!\d)", rf"第\s*0*{index}\s*[期回]",
                rf"♫\s*0*{index}(?!\d)", rf"(?<!\d)0*{index}\s*期")
    return any(re.search(pattern, str(title or "")) for pattern in patterns)


__all__ = ["CHARTS", "ChartSpec", "normalize_id", "parse_thumbinfo", "thumbinfo",
           "parse_search_ids", "search_videos", "parse_snapshot", "search_ids",
           "best_match", "parse_index", "page_video_id", "page_bilibili",
           "find_ranking_video", "parse_period", "parse_target_period", "format_publish",
           "format_row_time", "split_description", "clean_song_title", "core_title",
           "vocalist_lookup", "normalize_vocalists", "vocalists_from_tags",
           "vocalists_from_title", "first_vocalist", "fill_row", "nvapi_owner",
           "find_bilibili", "JST", "CST", "TITLE_CLEAR", "TITLE_MATCH_MIN", "SCORE_GAP"]
