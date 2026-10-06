"""nicolog（ニコログ）抓取：视频被设为非公開 / 删除后仍能拿到它的数据。

niconico 视频一旦非公開 / 被删，`https://www.nicovideo.jp/watch/<id>` 只会返回 404 错误页，
拿不到投稿日与播放量 → 条目里 `|nnd_date` 空着、荣誉题头也算不出殿堂等级。

nicolog（<https://www.nicolog.jp/>，专门记录 niconico 标签与统计的站点）仍保留着：
動画タイトル（原标题）、投稿者、投稿日時、長さ、動画説明，以及**最后一次**记录的
播放 / 评论 / 收藏数（页面里画图用的 `dataProvider` 快照，最后一条即最新）。

于是这类视频可以照常填条目：
    |投稿 =
    {{VOCALOID_Songbox/card|nnd|sm16693848|2012年1月14日|再生=1,081,622|class=deleted}}
（参考条目：杰西卡 sm16693848；`再生=` 与 `count=` 等价，`class=deleted` 会由模板自动标注「最终记录」。）

⚠️ nicolog 现在也在 Cloudflare 后面：直接请求只会拿到 403「Just a moment...」（2026-10-06 实测）。
`fetch()` 因此带了**真浏览器**那条后路（`utils/browser_fetch.py`）—— 详情见 `fetch()` 的说明。
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from bs4 import BeautifulSoup

from utils import browser_fetch
from utils.helpers import http_get

REQUEST_TIMEOUT = 25
BASE_URL = "https://www.nicolog.jp/watch/{}"
# 投稿日時：`2012年1月14日 20時55分27秒`（日本时间）
DATETIME_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
                         r"(?:\s*(\d{1,2})\s*時\s*(\d{1,2})\s*分(?:\s*(\d{1,2})\s*秒)?)?")
DATA_PROVIDER_RE = re.compile(r'"dataProvider"\s*:\s*\[([^\[\]]*)\]', re.S)
SNAPSHOT_RE = re.compile(r"\{(.*?)\}", re.S)
COUNTER_RE = re.compile(r'<td class="counter[^"]*">\s*([\d,]+)\s*</td>')
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
UPLOADER_ID_RE = re.compile(r"\s*\(ID:[^)]*\)\s*$")

# 2026-09 实测：nicolog 的 `og:image` 现在是 niconico 新 CDN 的**列表小图**
# <https://nicovideo.cdn.nimg.jp/thumbnails/…>，只有 130x100（不加尺寸后缀就是最小那张；
# `utils/image.py` 的 `cover_urls()` 会加 `.L` 换成 360x270）。
# 早年记录里可能是已停用的 <https://tn.smilevideo.jp/smile?i=…>（TLS 握手直接失败）→ 直接丢掉。
DEAD_THUMBNAIL_HOSTS = ("tn.smilevideo.jp",)

JST = timezone(timedelta(hours=9))
CN_TIMEZONE = timezone(timedelta(hours=8))


@dataclass
class NicologVideo:
    """nicolog 上的一条视频记录。"""
    identifier: str
    title: str = ""
    uploaded: Optional[datetime] = None          # 已转成东八区的日期
    views: int = 0                               # 最后一次记录的播放量
    comments: int = 0
    mylists: int = 0
    uploader: str = ""
    duration: str = ""
    description: str = ""
    thumbnail: str = ""

    @property
    def available(self) -> bool:
        """确实读到了东西（而不是把 404 页面当成了数据）。"""
        return bool(self.title or self.uploaded or self.views)


def parse_datetime(text: str) -> Optional[datetime]:
    """`2012年1月14日 20時55分27秒`（日本时间）→ 东八区的日期。

    只有日期、没有时分时（统计表里的 `2019年3月31日`）不做时区换算，避免整体差一天。
    """
    match = DATETIME_RE.search(text or "")
    if not match:
        return None
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    if match.group(4) is None:
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=JST)
    except ValueError:
        return None
    moment = moment.astimezone(CN_TIMEZONE)
    return datetime(moment.year, moment.month, moment.day)


def usable_thumbnail(url: str) -> str:
    """nicolog 给的封面 URL 还能不能下载；指向已停用域名的返回空串。"""
    url = (url or "").strip()
    if not url or any(host in url for host in DEAD_THUMBNAIL_HOSTS):
        return ""
    return url


def _snapshot_stats(html: str) -> Dict[str, int]:
    """页面里画统计图用的 `dataProvider` 快照 → 最后一条（最新）的播放 / 评论 / 收藏数。"""
    block = DATA_PROVIDER_RE.search(html or "")
    if not block:
        return {}
    latest: Optional[tuple] = None
    for item in SNAPSHOT_RE.findall(block.group(1)):
        view = re.search(r'"view"\s*:\s*(\d+)', item)
        if not view:
            continue
        date = re.search(r'"date"\s*:\s*"([^"]*)"', item)
        key = date.group(1) if date else ""          # `YYYY-MM-DD HH:MM`，字典序即时间序
        if latest is not None and key <= latest[0]:
            continue
        stats = {"views": int(view.group(1))}
        for name, pattern in (("comments", r'"com"\s*:\s*(\d+)'),
                              ("mylists", r'"mylist"\s*:\s*(\d+)')):
            found = re.search(pattern, item)
            stats[name] = int(found.group(1)) if found else 0
        latest = (key, stats)
    return latest[1] if latest else {}


def _table_stats(html: str) -> Dict[str, int]:
    """没有 dataProvider 时的兜底：标签履历表里日期最新的一行（再生 / 评论 / マイリスト）。"""
    latest: Optional[tuple] = None
    for row in ROW_RE.findall(html or ""):
        counters = COUNTER_RE.findall(row)
        if len(counters) < 3:
            continue
        date = parse_datetime(row)
        if date is None:
            continue
        values = [int(value.replace(",", "")) for value in counters[:3]]
        if latest is None or date >= latest[0]:
            latest = (date, {"views": values[0], "comments": values[1], "mylists": values[2]})
    return latest[1] if latest else {}


def parse(html: str, identifier: str = "") -> Optional[NicologVideo]:
    """解析 nicolog 的视频页；页面上没有视频信息（404 等）时返回 None。"""
    soup = BeautifulSoup(html or "", "html.parser")
    fields: Dict[str, str] = {}
    for term in soup.find_all("dt"):
        value = term.find_next_sibling("dd")
        if value is None:
            continue
        fields.setdefault(term.get_text(strip=True), value.get_text(" ", strip=True).strip())
    video = NicologVideo(
        identifier=identifier,
        title=fields.get("動画タイトル", ""),
        uploaded=parse_datetime(fields.get("投稿日時", "")),
        uploader=UPLOADER_ID_RE.sub("", fields.get("投稿者", "")).strip(),
        duration=fields.get("長さ", ""),
        description=fields.get("動画説明", ""),
    )
    meta = soup.find("meta", {"property": "og:image"})
    video.thumbnail = usable_thumbnail(meta.get("content") if meta else "")
    stats = _snapshot_stats(html) or _table_stats(html)
    video.views = stats.get("views", 0)
    video.comments = stats.get("comments", 0)
    video.mylists = stats.get("mylists", 0)
    return video if video.available else None


def fetch(identifier: str) -> Optional[NicologVideo]:
    """按视频 ID（sm…）取 nicolog 的数据；取不到（含网络失败）时返回 None。

    直接请求会被 Cloudflare 挑战（2026-10-06 实测：403「Just a moment...」）—— 那时改借
    用户自己的浏览器取（`browser_fetch.fetch_html()`）。**没有这一层的话，非公開的稿件
    会被当成正常投稿**：条目里没有 `{{VOCALOID_Songbox/card}}` 栏，投稿日退化成 VocaDB 的
    `publishDate`（实测 希望夏天能够延续 sm42552106：丢了 2023-08-04 与 10,174 再生）。
    """
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    url = BASE_URL.format(identifier)
    try:
        response = http_get(url, use_proxy=True, timeout=REQUEST_TIMEOUT)
    except Exception as e:                      # 抓不到不影响正常流程
        logging.warning("无法从 nicolog 获取 %s：%s", identifier, e)
        return _fetch_with_browser(url, identifier)
    video = parse(response.text, identifier)
    if video is not None:
        return video
    if not _challenged(response):
        return None                             # 页面上确实没有这条记录：换浏览器也一样
    logging.info("nicolog %s 被 Cloudflare 挑战，改用真浏览器取数。", identifier)
    return _fetch_with_browser(url, identifier)


def _challenged(response) -> bool:
    """这次响应是 Cloudflare 的人机校验页（而不是真正的 nicolog 页面）。"""
    if getattr(response, "status_code", 200) in (403, 503):
        return True
    return "Just a moment" in (getattr(response, "text", "") or "")[:2000]


def _fetch_with_browser(url: str, identifier: str) -> Optional[NicologVideo]:
    """借真浏览器（见 `utils/browser_fetch.py`）取整页 HTML 再交给 `parse()`。"""
    if not browser_fetch.available():
        return None
    html = browser_fetch.fetch_html(url)
    return parse(html, identifier) if html else None
