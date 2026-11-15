"""榜单条目的 nico 侧数据：榜单视频本身的元数据、按曲名找稿件、补齐每条的名次数据。

用户 2026-11-15 要求新增「生成榜单条目」功能（侧栏「刊」）。这一层只做**只读查询**，
不生成任何 wikitext（那是 `utils/chart_wikitext.py`）：

* `thumbinfo()` —— `ext.nicovideo.jp/api/getthumbinfo/<id>`（不需要登录、不受 Cloudflare 影响）
  给出标题 / 简介 / 投稿时间 / 时长 / 上传者 / 播放数 / 评论数 / 收藏数 / 标签；
* `search_ids()` —— `nicovideo.jp/search/<关键词>` 的 HTML 里按出现顺序取 `sm` 号
  （实测搜「Lilium UTAU」第一条正是 #52 里 Lilium 的 `sm8673630`）；
* `find_ranking_video()` —— 先读站上已有页面的 `|id`，再退到站内搜索，最后交给调用方问用户；
* `fill_row()` —— 给识别出来的一行补 `id` / `时间` / `P主` / `歌姬`。

时间口径（用户 2026-11-15 定）：榜单页的 `|发布时间` 用 **UTC+8**，每条榜单的 `|时间`
用稿件 `first_retrieve` 的 **JST 原样**（实测 #52 / #813 两代页面都这么写）。
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from utils import wiki_api
from utils.helpers import http_get
from utils.string import title_match_key

THUMBINFO_URL = "https://ext.nicovideo.jp/api/getthumbinfo/{}"
SEARCH_URL = "https://www.nicovideo.jp/search/{}"
WATCH_URL = "https://www.nicovideo.jp/watch/{}"

REQUEST_TIMEOUT = 25
JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))

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


def search_ids(keyword: str, limit: int = 8) -> List[str]:
    """按关键词搜 niconico，返回稿件编号候选。"""
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    from urllib.parse import quote
    try:
        return parse_search_ids(_get(SEARCH_URL.format(quote(keyword, safe=""))), limit)
    except Exception as e:                       # noqa: BLE001
        logging.info("搜索niconico「%s」失败：%s", keyword, e)
        return []


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

    顺序：站上已有页面的 `|id` → niconico 站内搜索（标题里带 `#期数` 的那条）→ 放弃
    （由调用方问用户要链接，用户 2026-11-15 选的兜底）。
    """
    identifier = page_video_id(spec, index)
    if identifier:
        return identifier, "站上已有页面"
    for candidate in search_ids(f"{spec.search_term} #{index}", limit=10):
        info = thumbinfo(candidate)
        if info is None:
            continue
        if parse_index(info.get("title") or "") == int(index):
            return candidate, "niconico搜索"
    return "", ""


def parse_period(description: str) -> Tuple[str, str]:
    """简介里的「○集計期間」→ `(起始, 终止)`，格式与站上一致（`2009年11月01日 00:00`）。"""
    match = PERIOD_RE.search(str(description or ""))
    if not match:
        return "", ""
    start = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)),
                     int(match.group(4)))
    end = datetime(int(match.group(5)), int(match.group(6)), int(match.group(7)),
                   int(match.group(8)))
    return _format_cn(start), _format_cn(end)


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
        for source in sources:
            for key, value in source.items():
                table.setdefault(title_match_key(key), value)
                table.setdefault(title_match_key(value), value)
        _VOCALISTS = {key: value for key, value in table.items() if key}
    return _VOCALISTS


_VOCALISTS: Optional[Dict[str, str]] = None


def vocalists_from_tags(tags: Sequence[str]) -> str:
    """从 tag 里挑出歌姬名（`重音テト` → `重音Teto`），用「、」连起来。"""
    known = vocalist_lookup()
    found: List[str] = []
    for tag in tags or []:
        name = known.get(title_match_key(tag))
        if name and name not in found:
            found.append(name)
    return "、".join(found)


def fill_row(row: dict, search: bool = True) -> dict:
    """给识别出来的一行补 `id` / `时间` / `P主` / `歌姬`（原地改，也返回它）。

    已经有 `id` 的（识别时就看到了）直接用它取元数据；没有就用**曲名**搜一遍。
    搜不到就留空 —— 表格里标黄，让用户自己填（宁可空着也不瞎猜）。
    """
    title = str(row.get("曲名") or row.get("title") or "").strip()
    identifier = normalize_id(row.get("id") or "")
    info = thumbinfo(identifier) if identifier else None
    if info is None and search and title:
        candidates = search_ids(title, limit=6)
        # 完整歌名 + 「UTAU」更容易命中 UTAU 稿件
        if not candidates:
            candidates = search_ids(f"{title} UTAU", limit=6)
        info = best_match(title, candidates)
    if info is None:
        row.setdefault("id", identifier)
        return row
    row["id"] = str(info.get("id") or identifier)
    if not str(row.get("时间") or "").strip():
        row["时间"] = format_row_time(info["date_jst"])
    if not str(row.get("P主") or "").strip():
        row["P主"] = info.get("uploader") or ""
    if not str(row.get("歌姬") or "").strip():
        row["歌姬"] = vocalists_from_tags(info.get("tags") or [])
    if not str(row.get("曲名") or "").strip():
        row["曲名"] = info.get("title") or ""
    return row


# ---------------------------------------------------------------- bilibili 转载

def find_bilibili(spec: ChartSpec, index: int) -> str:
    """按榜单标题搜 bilibili 转载的视频号（BV…）；找不到返回空串。

    站上那些榜单条目里都有 `{{BilibiliVideo|id=BV…}}`，但 nico 那边没有这个信息，所以只能搜。
    只在**标题里同时出现「UTAU」与期数**时才采用（`#162` 不能算成第 62 / 52 期），
    搜不到就留空 —— 这一栏在预览表格里可以直接改。
    """
    if not index:
        return ""
    try:
        from utils.producer_template import bilibili_search_items
    except Exception as e:                       # noqa: BLE001 - 移动端没有这些依赖
        logging.info("bilibili搜索不可用：%s", e)
        return ""
    for keyword in (f"{spec.search_term} #{index}", f"UTAU周刊 {index}",
                    f"{spec.search_term} {index}"):
        try:
            found = bilibili_search_items(keyword, limit=10)
        except Exception as e:                   # noqa: BLE001 - 搜不到就算了
            logging.info("搜索bilibili「%s」失败：%s", keyword, e)
            continue
        for item in found:
            name = PLAIN_TITLE_RE.sub("", str(item.get("title") or ""))
            if "UTAU" not in name.upper():
                continue
            if not _mentions_index(name, index):
                continue
            identifier = str(item.get("bvid") or "")
            if identifier.startswith("BV"):
                return identifier
    return ""


def _mentions_index(title: str, index: int) -> bool:
    """标题里有没有「第 index 期」（`#52` / `第52期` / `♫052`，别把 `#152` 当成 52）。"""
    patterns = (rf"#\s*{index}(?!\d)", rf"第\s*0*{index}\s*[期回]",
                rf"♫\s*0*{index}(?!\d)")
    return any(re.search(pattern, str(title or "")) for pattern in patterns)


__all__ = ["CHARTS", "ChartSpec", "normalize_id", "parse_thumbinfo", "thumbinfo",
           "parse_search_ids", "search_ids", "best_match", "parse_index",
           "page_video_id", "find_ranking_video", "parse_period", "format_publish",
           "format_row_time", "split_description", "vocalist_lookup",
           "vocalists_from_tags", "fill_row", "find_bilibili", "JST", "CST"]
