"""`{{bilibiliVideo}}` 的封面 / 标题：给「预览里也能看见播放器」用。

站上的模板把播放器交给 `Widget:BilibiliVideo` 的脚本去挂（见 `utils/preview_shim.py`），
桌面预览挂上 iframe 之后只会显示 bilibili 自己的「您当前的浏览器不支持 HTML5 播放器」——
PyQt5 的 QtWebEngine 不带 H.264（实测 `canPlayType('video/mp4; codecs="avc1.42E01E"')`
返回空串），**视频没法在预览里播**。

所以这里从 bilibili 的公开接口取封面图 + 标题 + UP 主，预览里按「封面 + 播放按钮」画一张
卡片（点封面去 bilibili 看），看上去跟站上的播放器一致 —— 图片 QtWebEngine 画得出来。

接口是公开的（`x/web-interface/view`），失败 / 超时一律返回 None，预览照常显示原来的链接卡片。
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

from utils.helpers import http_get

VIEW_API = "https://api.bilibili.com/x/web-interface/view"
TIMEOUT = 6
# 取不到（视频被删 / 网络不通 / 风控）时**短期内不再重试**：预览是每改一次字就刷一次，
# 每次都在工作线程里白等 6 秒会拖慢整个预览。
MISS_TTL = 300
# 一个条目里的 B 站视频通常就一两个，多了也别让预览等太久
MAX_VIDEOS = 4

BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")
AID_RE = re.compile(r"^(?:av)?(\d+)$", re.IGNORECASE)
# 渲染出来的容器：`<div class="bilibili-video-container" data-id="BV1xx" data-page="1" …>`
CONTAINER_RE = re.compile(
    r"<div\b[^>]*\bclass=\"[^\"]*\bbilibili-video-container\b[^\"]*\"[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"""([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")

# video_id -> (取到的时间, 信息或 None)；取到的永久留着，取不到按 MISS_TTL 过期
_CACHE: Dict[str, tuple] = {}


def _attributes(tag: str) -> Dict[str, str]:
    result = {}
    for match in ATTR_RE.finditer(tag or ""):
        value = next((item for item in match.groups()[1:] if item is not None), "")
        result[match.group(1).lower()] = value
    return result


def video_ids(html: str) -> List[str]:
    """渲染结果里所有 `{{bilibiliVideo}}` 容器的视频号（保持顺序并去重）。"""
    result: List[str] = []
    for tag in CONTAINER_RE.findall(str(html or "")):
        video_id = _attributes(tag).get("data-id", "").strip()
        if video_id and video_id not in result:
            result.append(video_id)
    return result


def _upgrade(url: str) -> str:
    """接口返回的封面是 http 的（`http://i0.hdslb.com/…`），预览文档是 https 时会被当混合内容拦掉。"""
    url = str(url or "").strip()
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    if url.startswith("//"):
        return "https:" + url
    return url


def video_info(video_id: str) -> Optional[Dict[str, Any]]:
    """单个视频的封面 / 标题 / UP 主；取不到返回 None（预览据此退回链接卡片）。"""
    video_id = str(video_id or "").strip()
    if not video_id:
        return None
    cached = _CACHE.get(video_id)
    if cached is not None:
        fetched_at, value = cached
        if value is not None or time.time() - fetched_at < MISS_TTL:
            return value

    params = {"bvid": video_id} if BVID_RE.match(video_id) else None
    aid = None if params else AID_RE.match(video_id)
    if params is None:
        if not aid:
            return None
        params = {"aid": aid.group(1)}
    value: Optional[Dict[str, Any]] = None
    try:
        response = http_get(VIEW_API, use_proxy=True, params=params,
                            headers={"Referer": "https://www.bilibili.com/"}, timeout=TIMEOUT)
        payload = response.json() if response is not None else {}
        data = (payload or {}).get("data") or {}
        if payload.get("code") == 0 and data.get("pic"):
            pages = data.get("pages") or []
            value = {
                "id": video_id,
                "title": str(data.get("title") or ""),
                "owner": str((data.get("owner") or {}).get("name") or ""),
                "pic": _upgrade(data.get("pic")),
                "url": "https://www.bilibili.com/video/" + video_id,
                "pages": len(pages),
            }
        else:
            logging.info("bilibili 视频 %s 取不到信息：%s",
                         video_id, (payload or {}).get("message") or "无 data.pic")
    except Exception as e:                       # noqa: BLE001 - 预览用的元信息，失败不算错
        logging.info("bilibili 视频 %s 取信息失败：%s", video_id, e)
    _CACHE[video_id] = (time.time(), value)
    return value


def video_cards(html: str) -> Dict[str, Dict[str, Any]]:
    """渲染结果里所有 B 站视频的元信息（视频号 -> 信息）；一个都取不到时返回空 dict。"""
    cards: Dict[str, Dict[str, Any]] = {}
    for video_id in video_ids(html)[:MAX_VIDEOS]:
        info = video_info(video_id)
        if info:
            cards[video_id] = info
    return cards


def reset_cache() -> None:
    """测试用：清掉进程内缓存。"""
    _CACHE.clear()
