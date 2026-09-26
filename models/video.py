import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Union, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from i18n.i18n import _
from utils import nicolog
from utils.helpers import prompt_response, prompt_choices, http_get
from utils.string import split_number, is_empty


REQUEST_TIMEOUT = 20
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0 Safari/537.36"
}


class VideoSite(Enum):
    NICO_NICO = "niconico"
    BILIBILI = "bilibili"
    YOUTUBE = "YouTube"


class Video:
    def __init__(self, site: VideoSite, identifier: str, url: str, views: int, uploaded: datetime,
                 thumb_url: str = None, canonical: bool = True, deleted: bool = False):
        self.site: VideoSite = site
        self.identifier: str = identifier
        self.url = url
        self.views: int = views
        self.uploaded: datetime = uploaded
        self.thumb_url: str = thumb_url
        self.canonical = canonical
        # 视频已被设为非公開 / 删除（数据来自 nicolog）：条目改用 {{VOCALOID Songbox/card}} 写投稿栏
        self.deleted = deleted

    def __str__(self) -> str:
        return f"VideoSite: {self.site}\n" \
               f"Id: {self.identifier}\n" \
               f"Views: {self.views}\n" \
               f"Uploaded: {self.uploaded}\n" \
               f"Thumb: {self.thumb_url}\n\n"


table = 'fZodR9XQDSUm21yCkr6zBqiveYah8bt4xsWpHnJE7jL5VG3guMTKNPAwcF'
tr = {}
for index in range(58):
    tr[table[index]] = index
s = [11, 10, 3, 8, 4, 6]
xor = 177451812
add = 8728348608


def av_to_bv(av: str) -> str:
    x = int(av[2:])
    x = (x ^ xor) + add
    r = list('BV1  4 1 7  ')
    for i in range(6):
        r[s[i]] = table[x // 58 ** i % 58]
    return ''.join(r)


def parse_nc_url(vid: str) -> str:
    if vid.find("nicovideo") != -1:
        vid = vid[vid.rfind("/") + 1:]
    return vid


CN_TIMEZONE = timezone(timedelta(hours=8))


def nico_date_to_cn(date: str) -> datetime:
    """将niconico返回的ISO时间(东九区)转换为东八区的日期。"""
    try:
        dt = datetime.fromisoformat(date)
    except ValueError:
        return str_to_date(date)
    if dt.tzinfo is None:
        return dt
    dt = dt.astimezone(CN_TIMEZONE)
    return datetime(year=dt.year, month=dt.month, day=dt.day)


def get_nc_thumbnail(soup) -> Optional[str]:
    """优先取 OGP 高清图（og:image），其次 twitter:image、thumbnail。"""
    for attrs in ({"property": "og:image"},
                  {"name": "twitter:image"},
                  {"name": "thumbnail"}):
        meta = soup.find("meta", attrs)
        if meta and meta.get("content"):
            return meta["content"]
    return None


def get_nc_info(vid: str) -> Video:
    """取 niconico 视频信息；视频非公開 / 被删时改用 nicolog 的记录（见 utils/nicolog.py）。"""
    vid = parse_nc_url(vid)
    url = f"https://www.nicovideo.jp/watch/{vid}"
    result = http_get(url, use_proxy=True).text
    soup = BeautifulSoup(result, "html.parser")
    date = datetime.fromtimestamp(0)
    views = 0
    for script in soup.find_all('script'):
        t: str = script.get_text()
        match = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', t)
        if match:
            date = nico_date_to_cn(match.group(1))
        index_start = t.find("userInteractionCount")
        if index_start != -1:
            index_start += len("userInteractionCount") + 2
            index_end = t.find("}", index_start)
            views = int(t[index_start:index_end])
    if date == datetime.fromtimestamp(0):
        # watch 页面只有 404 错误页（非公開 / 削除済み）→ 从 nicolog 取投稿日与最后的播放量
        archived = nicolog.fetch(vid)
        if archived is not None:
            logging.info("niconico %s 非公開，改用 nicolog 的数据（投稿日 %s，播放 %s）",
                         vid, archived.uploaded, archived.views)
            return Video(VideoSite.NICO_NICO, vid, url, archived.views,
                         archived.uploaded or date, archived.thumbnail or None, deleted=True)
        logging.warning("niconico %s 取不到信息，nicolog 也没有记录", vid)
    thumb = get_nc_thumbnail(soup)
    return Video(VideoSite.NICO_NICO, vid, url, views, date, thumb)


def get_bv(vid: str) -> str:
    search_bv = re.search("BV[0-9a-zA-Z]+", vid, re.IGNORECASE)
    if search_bv is not None:
        return search_bv.group(0)
    search_av = re.search("av[0-9]+", vid, re.IGNORECASE)
    if search_av is not None:
        return av_to_bv(search_av.group(0))
    return vid


def get_bb_info(vid: str) -> Video:
    vid = get_bv(vid)
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={vid}"
    response = http_get(url, use_proxy=False, headers=REQUEST_HEADERS,
                        timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get('code') != 0 or not payload.get('data'):
        raise ValueError(f"Bilibili API error: {payload.get('code')} {payload.get('message', '')}")
    data = payload['data']
    epoch_time = int(data['pubdate'])
    date = datetime.fromtimestamp(epoch_time)
    # remove extra information to be in sync with YT and Nico
    date = datetime(year=date.year, month=date.month, day=date.day)
    pic = data['pic']
    views = data['stat']['view']
    return Video(VideoSite.BILIBILI, vid, url, views, date, pic)


def parse_yt_url(vid: str) -> str:
    vid = vid.strip()
    if not vid:
        return vid
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        return vid
    parsed = urlparse(vid if "://" in vid else "https://" + vid)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0]
    if parsed.hostname and parsed.hostname.endswith("youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith("/shorts/") or parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
    return vid


def _int_or_none(value) -> Optional[int]:
    """把页面里的计数转成 int（可能是 '1,234' 这种带分隔符的字符串）。"""
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_yt_view_count(soup) -> Optional[int]:
    """YouTube 页面的播放量；认不出来返回 None。

    页面上有多个 interactionStatistic 块（实测 2026-09：第一条是 LikeAction
    **点赞数**，第二条才是 WatchAction 播放量），所以必须按 interactionType 挑。
    直接取第一个 `meta[itemprop="userInteractionCount"]` 会误把点赞当播放量，
    导致「明明是殿堂曲却没有荣誉题头」。
    """
    others: List[int] = []
    for block in soup.find_all(attrs={"itemprop": "interactionStatistic"}):
        count = block.find("meta", attrs={"itemprop": "userInteractionCount"})
        value = _int_or_none(count.get("content") if count is not None else None)
        if value is None:
            continue
        kind = block.find("meta", attrs={"itemprop": "interactionType"})
        if kind is not None and "WatchAction" in (kind.get("content") or ""):
            return value                                     # 播放量
        others.append(value)
    legacy = soup.find("meta", attrs={"itemprop": "interactionCount"})   # 旧版页面的裸计数
    value = _int_or_none(legacy.get("content") if legacy is not None else None)
    if value is not None:
        return value
    return others[0] if others else None


def ld_json_video_object(soup) -> Optional[dict]:
    """页面 ld+json 里的 VideoObject；取不到返回 None。"""
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            candidate = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        items = candidate if isinstance(candidate, list) else [candidate]
        metadata = next((item for item in items
                         if isinstance(item, dict) and item.get('@type') == 'VideoObject'), None)
        if metadata:
            return metadata
    return None


def ld_json_view_count(metadata: dict) -> Optional[int]:
    """ld+json 里的播放量：VideoObject.interactionCount 最优先，
    其次 interactionStatistic 里 WatchAction 的那一条，都没有才退回第一个计数。"""
    direct = _int_or_none(metadata.get('interactionCount'))
    if direct is not None:
        return direct
    statistics = metadata.get('interactionStatistic') or []
    if isinstance(statistics, dict):
        statistics = [statistics]
    counts: List[int] = []
    for statistic in statistics:
        if not isinstance(statistic, dict):
            continue
        value = _int_or_none(statistic.get('userInteractionCount'))
        if value is None:
            continue
        kind = statistic.get('interactionType')
        kind = kind.get('@type') if isinstance(kind, dict) else kind
        if isinstance(kind, str) and "WatchAction" in kind:
            return value
        counts.append(value)
    return counts[0] if counts else None


def get_yt_info(vid: str) -> Union[Video, None]:
    vid = parse_yt_url(vid)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        raise ValueError(f"Invalid YouTube video ID: {vid}")
    url = 'https://www.youtube.com/watch?v=' + vid
    response = http_get(url, use_proxy=True, headers=REQUEST_HEADERS,
                        timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    views = parse_yt_view_count(soup)
    published = soup.select_one('meta[itemprop="datePublished"][content]')
    if views is not None and published is not None:
        date = str_to_date(published['content'])
    else:
        metadata = ld_json_video_object(soup) or {}
        if views is None:
            views = ld_json_view_count(metadata)
        upload_date = metadata.get('uploadDate')
        if views is None or not upload_date:
            raise ValueError("YouTube page does not contain video metadata")
        date = str_to_date(upload_date)
    return Video(VideoSite.YOUTUBE, vid, url, views, date,
                 thumb_url="https://img.youtube.com/vi/{}/maxresdefault.jpg".format(vid))


info_func = {
    VideoSite.NICO_NICO: get_nc_info,
    VideoSite.BILIBILI: get_bb_info,
    VideoSite.YOUTUBE: get_yt_info
}


def view_count_from_site(video: Video) -> str:
    # requires Python 3.10; too many compatibility issues
    # match video.site:
    #     case VideoSite.NICO_NICO:
    #         return f"{{{{NiconicoCount|id={video.identifier}}}}}"
    #     case VideoSite.YOUTUBE:
    #         return f"{{{{YoutubeCount|id={video.identifier}|fallback={video.views}+}}}}"
    #     case VideoSite.BILIBILI:
    #         return f"{{{{BilibiliCount|id={video.identifier}}}}}"
    #     case _:
    #         return "ERROR"
    if video.site == VideoSite.NICO_NICO:
        return f"{{{{NiconicoCount|id={video.identifier}}}}}"
    if video.site == VideoSite.YOUTUBE:
        return f"{{{{YoutubeCount|id={video.identifier}}}}}"
    if video.site == VideoSite.BILIBILI:
        return f"{{{{BilibiliCount|id={video.identifier}}}}}"
    return "ERROR"


def video_from_site(site: VideoSite, identifier: str, canonical: bool = True) -> Union[Video, None]:
    logging.info('Fetching video from ' + site.value)
    logging.debug(f"Video identifier: {identifier}")
    try:
        v = info_func[site](identifier)
    except Exception as e:
        logging.warning(_("fail_fetch") + site.value)
        logging.exception("Failed to fetch %s: %s", site.value, e)
        v = None
    if not v:
        identifier = parse_yt_url(identifier) if site == VideoSite.YOUTUBE else parse_nc_url(identifier)
        return Video(site, identifier, "", 0, datetime.fromtimestamp(0))
    v.canonical = canonical
    return v


def str_to_date(date: str) -> datetime:
    if 'T' in date:
        date = date[:date.find('T')]
    date = date.split("-")
    if len(date) != 3:
        logging.warning(_("invalid_date"))
        return datetime.fromtimestamp(0)
    year = int(date[0])
    month = int(date[1])
    day = int(date[2])
    return datetime(year=year, month=month, day=day)


def get_video_bilibili() -> Union[Video, None]:
    bv = prompt_response(_("bilibili_link"))
    if bv.isspace() or len(bv) == 0:
        return None
    if bv:
        bv = resolve_short_link(bv) or bv          # b23.tv 短链先换成真实链接
        bv_canonical = prompt_choices(_("bv_canonical"), ["Yes", "No"])
        bv_canonical = bv_canonical == 1
        return video_from_site(VideoSite.BILIBILI, bv, bv_canonical)


# 用户手输的链接（或裸 ID）分别长什么样
NICO_LINK_RE = re.compile(r"nicovideo\.jp|^s[mo]\d+$", re.IGNORECASE)
# B 站短链：只有短码，必须联网跳转后才能拿到 BV 号
SHORT_LINK_HOSTS = ("b23.tv", "bili2233.cn")
SHORT_LINK_RE = re.compile(r"b23\.tv|bili2233\.cn", re.IGNORECASE)
BILIBILI_LINK_RE = re.compile(r"bilibili\.com|^av\d+$", re.IGNORECASE)
BV_ID_RE = re.compile(r"^BV[0-9A-Za-z]+$", re.IGNORECASE)
YOUTUBE_LINK_RE = re.compile(r"youtu\.?be", re.IGNORECASE)
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def short_link_url(link: str) -> Optional[str]:
    """链接是 B 站短链时返回补好协议的完整 URL，否则返回 None。"""
    text = (link or "").strip()
    if not text:
        return None
    url = text if "://" in text else "https://" + text
    if (urlparse(url).hostname or "").lower() not in SHORT_LINK_HOSTS:
        return None
    return url


def resolve_short_link(link: str) -> Optional[str]:
    """B 站短链（b23.tv / bili2233.cn）→ 真实链接；不是短链或跳转失败时返回 None。

    实测 <https://b23.tv/Zt8UWvS> → 302 Location: https://www.bilibili.com/video/BV1fc386VE6n?…，
    所以只发一个不跟随跳转的请求读 Location，不把整个视频页拉下来。
    （图文 / 笔记会跳到 m.bilibili.com/opus/…，不是视频，留给后面的 BV 号校验拒掉。）
    """
    url = short_link_url(link)
    if url is None:
        return None
    try:
        response = http_get(url, use_proxy=False, headers=REQUEST_HEADERS,
                            timeout=REQUEST_TIMEOUT, allow_redirects=False)
    except Exception as e:                      # 短链解析失败不影响提示重输
        logging.warning("无法解析B站短链 %s：%s", url, e)
        return None
    location = (response.headers.get("Location") or "").strip()
    if not location:
        logging.warning("B站短链 %s 没有跳转地址（HTTP %s）", url, response.status_code)
        return None
    logging.info("B站短链 %s → %s", url, location)
    return location


@dataclass
class HumanOriginal:
    """人声本家：同一首歌的人声演唱版本（参 voca.wiki 条目 如月车站、红色房间）。

    video 是它的 niconico 或 YouTube 稿件，bilibili 是它的 B 站稿件，都可以没有。
    """
    video: Optional[Video] = None
    bilibili: Optional[Video] = None


def guess_video_site(link: str) -> Optional[VideoSite]:
    """从链接（或裸视频 ID）判断站点；认不出来返回 None。"""
    text = (link or "").strip()
    if not text:
        return None
    if NICO_LINK_RE.search(text):
        return VideoSite.NICO_NICO
    # B 站的 BV 号有 12 位，不会和 YouTube 的 11 位 ID 混淆，但仍先判 B 站
    if BILIBILI_LINK_RE.search(text) or SHORT_LINK_RE.search(text) or BV_ID_RE.match(text):
        return VideoSite.BILIBILI
    if YOUTUBE_LINK_RE.search(text) or YOUTUBE_ID_RE.match(text):
        return VideoSite.YOUTUBE
    return None


def video_link(link: str) -> Optional[Video]:
    """把用户输入的链接（或裸 ID）解析成 Video；站点认不出来时返回 None。

    只有 B 站短链（b23.tv）需要联网跳转一次，其余都是本地解析：
    人声本家只需要站点与视频 ID，不必联网取播放量与投稿日。
    短链跳不出 BV 号（网络失败、或是图文 / 笔记）时返回 None，由调用方提示重输。
    """
    text = (link or "").strip()
    site = guess_video_site(text)
    if site is None:
        return None
    if site == VideoSite.BILIBILI:
        identifier = get_bv(resolve_short_link(text) or text)
        if not BV_ID_RE.match(identifier):
            return None
        url = f"https://www.bilibili.com/video/{identifier}"
    elif site == VideoSite.NICO_NICO:
        identifier = parse_nc_url(text)
        url = f"https://www.nicovideo.jp/watch/{identifier}"
    else:
        identifier = parse_yt_url(text)
        url = f"https://www.youtube.com/watch?v={identifier}"
    return Video(site, identifier, url, 0, datetime.fromtimestamp(0))


def prompt_video_link(prompt: str, sites: Sequence[VideoSite]) -> Optional[Video]:
    """询问一个视频链接（只接受 sites 里的站点）；留空或认不出来时提示重输，留空即跳过。"""
    while True:
        answer = prompt_response(prompt)
        if is_empty(answer):
            return None
        video = video_link(answer)
        if video is not None and video.site in sites:
            return video
        print(_("link_not_recognized"))


def get_human_original() -> Optional[HumanOriginal]:
    """询问是否存在人声本家；存在则收集它的 niconico / YouTube 链接与 bilibili 链接。

    见 config.yaml 的 wikitext.human_original。两个链接都可以留空（跳过），
    都没填就当作没有人声本家。
    """
    if prompt_choices(_("human_original_ask"), ["Yes", "No"]) == 2:
        return None
    video = prompt_video_link(_("human_original_video"),
                              (VideoSite.NICO_NICO, VideoSite.YOUTUBE))
    bilibili = prompt_video_link(_("human_original_bilibili"), (VideoSite.BILIBILI,))
    if video is None and bilibili is None:
        logging.warning("人声本家没有填任何链接，按「没有」处理。")
        return None
    logging.info("人声本家：%s %s",
                 video.identifier if video else "", bilibili.identifier if bilibili else "")
    return HumanOriginal(video=video, bilibili=bilibili)


def get_video(videos: List[Video], site: VideoSite):
    for v in videos:
        if v.site == site:
            return v
    return None


def only_canonical_videos(videos: List[Video]) -> List[Video]:
    return [v for v in videos if v.canonical]
