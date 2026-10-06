import json
import logging
import re
import time
import urllib
from datetime import datetime
from pathlib import Path
from typing import Union, List, Dict, Optional, Sequence, Tuple
from urllib.parse import urlencode

import requests

import utils.string
from config.config import get_config, get_output_path
from i18n.i18n import _
from models.creators import Person, Creators, merge_composer_lyricist, role_transform
from models.song import Song, Image, get_manual_lyrics, Lyrics
from models.video import (Video, VideoSite, OtherVersion, video_from_site,
                          get_video_bilibili, str_to_date)
from utils import string, japanese, lyrics_editor, ai_lyrics
from utils import browser_fetch
from utils import family_template
from utils import identity
from utils.at_wiki import get_chinese_lyrics, get_japanese_lyrics, get_vocaloid_collection_info
from utils.helpers import prompt_choices, prompt_multiline, prompt_response, http_get
from utils.image import download_thumbnail, remove_black_boarders
from utils.name_converter import name_shorten, engine_from_type, engine_from_bank_marker
from utils.string import split, is_empty, safe_filename

VOCADB_SONG_QUERY_URL = "https://vocadb.net/api/songs"
VOCADB_ARTIST_QUERY_URL = "https://vocadb.net/api/artists"
# 提示里给用户举例用的**真实存在**的详情地址（千万不能写成 `<歌曲ID>` 那种占位符：
# 用户 2026-10-03 直接复制粘贴，浏览器跳到了 `https://vocadb.net/Error?code=404`）。
VOCADB_JSON_EXAMPLE_URL = "https://vocadb.net/api/songs/588755/details"
# VocaDB 的 API 文档（https://wiki.vocadb.net/docs/public-api，用户 2026-10-03 让看的）
# 在「API usage rules」里明说了三件事，下面这三条常量就是照着做的：
#   ① **请用自定义 User-Agent**，方便他们识别流量来源；
#   ② **请在自己这边缓存响应**，别反复要同一份数据；
#   ③ 「不考虑服务器压力地每天几千次请求」会**被当成 DoS 封 IP**
#      → 所以加了请求间隔 + 每天次数记数（到阈值只在日志里提醒一句）。
# 它**没有**提供 API key / 白名单之类的「免过人机校验」通道：GET 本来就是匿名公开的，
# 403 那种「Just a moment...」是 Cloudflare 在域名前面挡的，跟文档里的 UA 规则无关。
VOCADB_CACHE_NAME = "vocadb_cache.json"
# 缓存多久算过期：VocaDB 的数据（歌曲详情 / 歌名→id）不会天天变，7 天够用
VOCADB_CACHE_TTL = 7 * 24 * 3600
# 两次真实请求之间至少隔这么久（文档第 ③ 条）
VOCADB_MIN_INTERVAL = 1.0
# 一天内请求超过这么多次就在日志里提醒（不拦，只提醒 —— 别被当成 DoS）
VOCADB_DAILY_WARN = 500
# 被 Cloudflare 的人机校验挡住（实测 403，页面上是「Just a moment...」）时给的提示。
# 2026-10-03 用浏览器实测出来的结论（很关键，别再走弯路）：
#   * 在浏览器里 `fetch('/api/songs?...', {credentials:'include'})` → **200**；
#   * 同一个页面、同一个浏览器，`{credentials:'omit'}`（不带 Cookie）→ **403 挑战页**。
#   → 挡住的是**没有 cf_clearance 这个 Cookie**，不是请求库的 TLS 指纹、不是 UA 被拉黑。
#     所以「伪造 TLS 指纹 / 自动解验证码」那类做法对这堵墙**没有用**（实测证伪了）。
VOCADB_BLOCK_NOTICE = (
    "VocaDB 被 Cloudflare 的人机校验挡住了（HTTP 403）。**大部分情况你不用管**："
    "工具会自动借本机的 Chrome / Edge（停在屏幕外，一次约十秒）去取同一份数据。"
    "只有它也没取到时才需要你出手："
    "① 在浏览器里打开 https://vocadb.net （过掉校验），F12 → Network → 任一 vocadb.net 请求 → "
    "把请求头里 `Cookie:` 那一整行（含 cf_clearance）贴到 config.yaml 的 vocadb_cookie；"
    "② 把同一请求的 `User-Agent:` 那串也贴到 vocadb_user_agent（cf_clearance 绑 IP + UA，"
    "对不上照样 403；工具在配了 Cookie 时会自动改用浏览器 UA，但版本号对不对得靠你确认）；"
    "③ 用浏览器打开 https://vocadb.net/api/songs/588755/details ，把地址末尾那串数字换成这首歌的 ID"
    "（网站上的歌曲页地址 https://vocadb.net/S/588755 里那串数字就是 ID），把页面上那段 JSON "
    "直接粘进工具（粘过一次就永久缓存在本地，以后不用再粘）；"
    "④ 换一个没被标记的出口 IP（config.yaml 的 proxies），或过几分钟重试（工具每 60 秒自动试探一次）。")
# 挡上一次之后，这么长时间内不再去碰 VocaDB（免得一个歌名一次、连环 403）
VOCADB_BLOCK_COOLDOWN = 600
# 但冷却期内**每隔这么久放一次试探**：用户 2026-10-03「挡过一次后如果我切换 IP
# 就不要继续拦我十分钟」—— 换了代理（`config.yaml` 的 `proxies`）冷却直接作废；
# 靠 VPN 换出口（配置没变）的那种，最多等这一分钟就有下一次试探。
VOCADB_BLOCK_PROBE = 60
_blocked_until = 0.0
_blocked_key = ""          # 上次被挡时的出口指纹（代理设置）
_next_probe = 0.0          # 冷却期内下一次允许试探的时刻
_last_request = 0.0        # 上一次真实请求的时刻（限速用，见 `_before_request()`）
_daily_warned = False      # 今天的「请求太多」提醒过了没
_ua_notice_shown = False   # 「配了 Cookie 就自动用浏览器 UA」这条提示只记一次
_notice_shown = False      # 被挡住时的长提示只详细说一次（批量生成时别再刷屏）
_cache: Optional[dict] = None      # 本地响应缓存（见 `_load_cache()`）


class VocadbBlocked(RuntimeError):
    """VocaDB 取不到数据（被人机校验 / 限流挡住）—— 调用方拿不到就不该硬崩。"""


class FetchedResponse:
    """浏览器取回来的响应：假装是 requests 的响应，够 `.text` / `.json()` 用就行。"""

    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text
        self.headers: Dict[str, str] = {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        pass


def _with_params(url: str, params: Optional[dict]) -> str:
    """把查询参数拼进 URL —— 借浏览器取数时用。

    ⚠️ 坑（2026-10-04 踩到）：`requests` 会自己把 `params` 编进 URL，但浏览器那条路只拿到一个
    字符串 URL —— 不拼的话搜索会变成「无条件的列表」，于是一直搜不到歌。
    """
    if not params:
        return url
    query = urlencode({key: value for key, value in params.items() if value is not None})
    return f"{url}{'&' if '?' in url else '?'}{query}"


def _browser_text(url: str, params: Optional[dict] = None) -> Optional[str]:
    """让**用户自己的 Chrome/Edge**（带界面、停到屏幕外）去取这份 JSON。

    用户 2026-10-04 选的就是这条路：他不想再贴 Cookie/UA（会过期）、也不想手动粘 JSON。
    实测里只有带界面的真浏览器过得了 Cloudflare（内置 WebEngine 5.15 和 headless Chrome 都不行）。
    取不到就返回 `None`，调用方退回「给提示 + 手动粘」那条老路 —— **绝不会挂死**（各有硬超时）。
    """
    if not browser_fetch.available():
        return None
    request_url = _with_params(url, params)
    logging.info("VocaDB 直接请求被挡，借本机浏览器去取：%s", request_url)
    text = browser_fetch.fetch_text(request_url)
    if text:
        logging.info("浏览器取到了（%s 字符）", len(text))
    return text


def _proxy_key() -> str:
    """当前出口设置的指纹（`config.yaml` 的 `proxies`）—— 换代理 = 换 IP，冷却该作废。"""
    try:
        return str(get_config().proxies or "").strip()
    except Exception as e:                        # noqa: BLE001 - 配置读不到就当没变
        logging.debug("读代理设置失败（按没变处理）：%s", e)
        return ""


def _cooling_down() -> bool:
    """现在是否「刚被挡、先别请求」。

    不算冷却的两种情况（用户 2026-10-03 要求）：
    * **出口换了**（`config.yaml` 的 `proxies` 与上次不同）→ 立刻重试；
    * 到了试探间隔（`VOCADB_BLOCK_PROBE`）→ 放一次试探，恢复就继续用、还被挡就重新计时。
    """
    global _next_probe
    now = time.time()
    if now >= _blocked_until:
        return False
    if _proxy_key() != _blocked_key:
        logging.info("VocaDB 冷却作废：出口（proxies）换了，重新试一次")
        return False
    if now >= _next_probe:
        _next_probe = now + VOCADB_BLOCK_PROBE
        logging.info("VocaDB 冷却期内放一次试探（换过 IP / 代理就能马上恢复）")
        return False
    return True


def reset_block() -> None:
    """手动清掉冷却（「再试一次」时用）。"""
    global _blocked_until, _blocked_key, _next_probe, _notice_shown
    _blocked_until, _blocked_key, _next_probe = 0.0, "", 0.0
    _notice_shown = False        # 下一次真被挡了再完整说一遍处理办法


def _is_challenge(response) -> bool:
    """这个响应是不是 Cloudflare 的挑战页（或限流）。"""
    try:
        status = int(getattr(response, "status_code", 0) or 0)
    except (TypeError, ValueError):      # 测试里的 Mock / 奇怪对象：当普通响应处理
        status = 0
    if status == 429:
        return True
    if status != 403:
        return False
    body = str(getattr(response, "text", "") or "")[:600].lower()
    return "just a moment" in body or "cf-" in body or "cloudflare" in body


def _vocadb_headers() -> Dict[str, str]:
    """请求 VocaDB 时带的请求头：User-Agent + 可选的浏览器 Cookie。

    **UA 的取法（顺序）：**
    1. `vocadb_user_agent` 里填了就用它（**精确匹配**用 —— Cloudflare 的 `cf_clearance`
       绑「IP + UA」，你浏览器是什么 UA 就得填什么）；
    2. 配了 `vocadb_cookie` → 自动用 `BROWSER_USER_AGENT`（那份 Cookie 本来就是浏览器发给它的）；
    3. `vocadb_browser_ua: true` → 也用 `BROWSER_USER_AGENT`；
    4. 否则用**工具自己的 UA**（VocaDB 的 API 文档要求用自定义 UA 方便识别流量来源）。

    为什么这么绕：2026-10-03 用浏览器实测发现，挡住请求的是**缺 `cf_clearance` 那个 Cookie**
    （同一页面带 Cookie 是 200、不带就是 403），而 `cf_clearance` 绑 UA —— 所以「配了 Cookie
    却还发工具 UA」是自相矛盾的组合，会把好不容易拿到的 Cookie 白费。
    """
    global _ua_notice_shown
    try:
        config = get_config()
    except Exception as e:                        # noqa: BLE001 - 配置读不到就用默认
        logging.debug("读配置失败（按默认处理）：%s", e)
        config = None
    cookie = str(getattr(config, "vocadb_cookie", "") or "").strip()
    ua = str(getattr(config, "vocadb_user_agent", "") or "").strip()
    if not ua:
        if cookie or bool(getattr(config, "vocadb_browser_ua", False)):
            ua = identity.BROWSER_USER_AGENT
            if cookie and not _ua_notice_shown:
                _ua_notice_shown = True
                logging.info("配了 vocadb_cookie → 自动用浏览器 UA（cf_clearance 绑 IP + UA）；"
                             "如果这串 UA 和当初过校验的浏览器对不上，请把浏览器里那串 "
                             "User-Agent 贴到 config.yaml 的 vocadb_user_agent。")
        else:
            ua = identity.USER_AGENT
    headers = {"User-Agent": ua}
    if cookie:
        headers["Cookie"] = cookie
    return headers


def probe_access() -> Tuple[bool, str]:
    """试一次最小的 VocaDB 请求 → `(通没通, 一句人话)`。

    给「自检」用：403 就说明 Cookie 缺了 / 过期了 / UA 对不上 / 换了 IP，而不是我们代码的问题
    （2026-10-03 用浏览器实测过：同样的 URL，带浏览器那份 Cookie 是 200、不带就是 403）。
    """
    try:
        response = vocadb_get("https://vocadb.net/api/songs?query=a&maxResults=1")
    except VocadbBlocked as blocked:
        return False, str(blocked)
    except Exception as e:                        # noqa: BLE001 - 网络 / 超时都算「不通」
        return False, f"请求 VocaDB 失败：{e}"
    return True, f"VocaDB 正常（HTTP {getattr(response, 'status_code', '?')}）。"


def _budget_key() -> str:
    return time.strftime("%Y-%m-%d")


def _log_block(url: str) -> None:
    """记「被 Cloudflare 挡了」。

    `VOCADB_BLOCK_NOTICE` 很长（四条办法），批量生成时每首歌都刷一遍会把日志冲烂
    → 只详细说一次，后面的给一句话提醒。
    """
    global _notice_shown
    if _notice_shown:
        logging.error("VocaDB 仍被 Cloudflare 挡着（%s）；处理办法见上面那条日志。", url)
        return
    _notice_shown = True
    logging.error("%s（%s）", VOCADB_BLOCK_NOTICE, url)


def _before_request() -> None:
    """真实请求之前的记账：限速（文档第 ③ 条）+ 每天次数记数。

    限速是「同一个进程内两次请求至少隔 `VOCADB_MIN_INTERVAL` 秒」，不会把整个生成流程拖慢
    （一个歌名原本只有 2~4 次请求，且有缓存时不请求）。
    """
    global _last_request, _daily_warned
    now = time.time()
    wait = VOCADB_MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()
    cache = _load_cache()
    budget = cache.setdefault("requests", {"date": _budget_key(), "count": 0})
    if budget.get("date") != _budget_key():
        budget["date"], budget["count"] = _budget_key(), 0
        _daily_warned = False
    budget["count"] = int(budget.get("count", 0)) + 1
    _save_cache()
    if budget["count"] >= VOCADB_DAILY_WARN and not _daily_warned:
        _daily_warned = True
        logging.warning("今天已经向 VocaDB 发了 %s 次请求：VocaDB 的 API 文档说「每天上千次"
                        "又不提前打招呼会被当成 DoS 封 IP」，建议先在 config.yaml 里配好"
                        " proxies 或检查是不是卡在重试上。", budget["count"])


def vocadb_get(url: str, **kwargs):
    """访问 VocaDB 的 GET：带自定义 UA / Cookie，限速，被挡住时重试一次再抛 `VocadbBlocked`。

    为什么要它：以前直接 `raise_for_status()`，一个 403 就把整个生成流程带堆栈打断
    （用户 2026-10-03 报的）。现在换成一句能看懂的原因，而且挡过一次之后短时间内不再请求
    —— 但**换了出口就会重试**（见 `_cooling_down()`）。
    """
    global _blocked_until, _blocked_key, _next_probe
    if _cooling_down():
        # 冷却期内也允许借浏览器：那条路有 cf_clearance，能自己恢复就不用让你等十分钟。
        text = _browser_text(url, kwargs.get("params"))
        if text:
            return FetchedResponse(text)
        raise VocadbBlocked(VOCADB_BLOCK_NOTICE)
    headers = {**_vocadb_headers(), **(kwargs.pop("headers", None) or {})}
    _before_request()
    response = http_get(url, use_proxy=True, timeout=30, headers=headers or None, **kwargs)
    if _is_challenge(response):
        logging.warning("VocaDB 返回 %s（Cloudflare 人机校验）：%s；2 秒后重试一次",
                        response.status_code, url)
        time.sleep(2)
        _before_request()
        response = http_get(url, use_proxy=True, timeout=30, headers=headers or None, **kwargs)
        if _is_challenge(response):
            # 最后一条路（也是用户最省事的那条）：借本机的真浏览器去取同一个地址。
            text = _browser_text(url, kwargs.get("params"))
            if text:
                return FetchedResponse(text)
            _blocked_until = time.time() + VOCADB_BLOCK_COOLDOWN
            _blocked_key = _proxy_key()
            _next_probe = time.time() + VOCADB_BLOCK_PROBE
            _log_block(url)
            raise VocadbBlocked(VOCADB_BLOCK_NOTICE)
    response.raise_for_status()
    return response


# ---------------------------------------------------------------- 响应缓存
# VocaDB 的 API 文档第 ② 条：请在自己这边缓存响应，别反复请求同一份数据。
# 这不只是为了礼貌 —— 每个歌名原本要 2~4 次请求，同一个条目反复生成时全是白发的，
# 而请求发得越少，越不容易再撞上 Cloudflare 的人机校验 / IP 封禁。
# 文件落在 `output/vocadb_cache.json`（与 `category_cache.json` 同处，可随手删）。


def _cache_path() -> Optional[Path]:
    try:
        return Path(get_output_path()) / VOCADB_CACHE_NAME
    except Exception as e:                        # noqa: BLE001 - 拿不到目录就不缓存
        logging.debug("取输出目录失败（VocaDB 缓存这次不落盘）：%s", e)
        return None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        _cache = {"songs": {}, "searches": {}, "requests": {}}
        path = _cache_path()
        if path is not None and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("songs", "searches", "requests"):
                        value = data.get(key)
                        if isinstance(value, dict):
                            _cache[key] = value
            except (ValueError, OSError) as e:
                logging.warning("VocaDB 缓存读不出来（当作没有）：%s", e)
    return _cache


def _save_cache() -> None:
    path = _cache_path()
    if path is None or _cache is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_cache, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logging.debug("VocaDB 缓存写不进去（忽略）：%s", e)


def clear_cache() -> None:
    """清空 VocaDB 的本地缓存（想强制重新联网取数时用）。"""
    global _cache
    _cache = None
    path = _cache_path()
    if path is not None and path.is_file():
        try:
            path.unlink()
        except OSError as e:
            logging.debug("删 VocaDB 缓存失败（忽略）：%s", e)


def _cache_lookup(bucket: str, key: str):
    entry = _load_cache()[bucket].get(str(key))
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("at", 0) or 0) > VOCADB_CACHE_TTL:
        return None
    return entry.get("value")


def cached_song_payload(song_id) -> Optional[dict]:
    """命中过期的歌曲详情 JSON 就返回它（**包括**用户手动粘进来的那份）。"""
    value = _cache_lookup("songs", song_id)
    return value if isinstance(value, dict) else None


def store_song_payload(song_id, payload: dict) -> None:
    if payload and song_id:
        _load_cache()["songs"][str(song_id)] = {"at": time.time(), "value": payload}
        _save_cache()


def cached_song_id(name: str) -> Optional[str]:
    value = _cache_lookup("searches", name)
    return str(value) if value else None


def store_song_id(name: str, song_id) -> None:
    """歌名 → id 的缓存：**只记命中**（搜不到的不记，免得歌后来录入了还一直说没有）。"""
    if song_id:
        _load_cache()["searches"][str(name)] = {"at": time.time(), "value": str(song_id)}
        _save_cache()



# 这些 artistType 都是「歌手」（唱的人）：名字统一过一遍 `name_shorten`，
# 把声库前缀 / 版本后缀砍掉（`初音ミク V4X (Original)` → 初音ミク、
# `Synthesizer V AI Megpoid` → Megpoid）。以前只认 'Vocaloid' 一种，
# 于是 Synthesizer V / CeVIO / NEUTRINO 的歌姬名会整串漏进条目（用户 2026-09 报的《小小星座》）。
VOICE_ARTIST_TYPES = {'Vocaloid', 'UTAU', 'CeVIO', 'SynthesizerV', 'NEUTRINO', 'VoiSona',
                      'VOICEPEAK', 'Voicepeak', 'NewType', 'OtherVoiceSynthesizer',
                      # VocaDB 的 ArtistType 枚举里另外几种声库类型，同样是「唱的人」：
                      # 名字一样要砍声库后缀，而且 `name_converter` 就照这个类型认引擎
                      'Voiceroid', 'VOICEVOX', 'AIVOICE', 'ACEVirtualSinger'}

PARAMS_BROAD = {
    'start': 0,
    'maxResults': 50,
    'fields': 'None',
    'lang': 'Default',
    'nameMatchMode': 'Exact',
    'sort': 'PublishDate',
    'childTags': 'false',
    'artistParticipationStatus': 'Everything',
    'onlyWithPvs': 'false',
    'getTotalCount': 'true'
}

PARAMS_NARROW = {**PARAMS_BROAD,
                 'songTypes': 'Original'}


COLLECTION_SEASONS_JA = {
    'Winter': '冬',
    'Spring': '春',
    'Summer': '夏',
    'Autumn': '秋',
    'Fall': '秋',
}

COLLECTION_NAME_PATTERN = re.compile(
    r"The VOCALOID Collection\s+(20\d{2})\s+(Winter|Spring|Summer|Autumn|Fall)",
    re.IGNORECASE)


def collection_name_to_japanese(name: str) -> str:
    """将VocaDB返回的英文活动名转换为日文（如 The VOCALOID Collection 2024 Winter -> ボカコレ2024冬）。"""
    if 'ボカコレ' in name:
        return name
    match = COLLECTION_NAME_PATTERN.search(name)
    if match:
        return f"ボカコレ{match.group(1)}{COLLECTION_SEASONS_JA[match.group(2)]}"
    return name


def get_vocaloid_collection_event(release_events: list):
    for release_event in release_events or []:
        event = release_event.get('event', release_event)
        name = event.get('name', '') if isinstance(event, dict) else str(event)
        if 'ボカコレ' in name or 'VOCALOID Collection' in name:
            return collection_name_to_japanese(name)
    return None


def prompt_vocaloid_collection_details(event_name: str):
    """问用户赛道与名次（**兑底路径**：活动模板取不到时才走）。

    选项按 `family_template.COLLECTION_TRACKS`（TOP100 / ROOKIE / REMIX）来，
    最后多一个「榜外」——实测 2023秋 / 2024春 / 2024夏 / 2025春 这几届 wiki 上没有模板，
    只能走这条路。
    """
    tracks = list(family_template.COLLECTION_TRACKS)
    choice = prompt_choices(
        _("collection_track").format(name=event_name),
        [*tracks, _("not_ranked")])
    if choice == len(tracks) + 1:
        return family_template.UNRANKED_TRACK, None
    track = tracks[choice - 1]
    rank = prompt_response(
        _("collection_rank").format(track=track),
        validity_checker=lambda value: value.isdigit() and int(value) > 0)
    return track, rank


def detect_collection_details(event_name: str, page_name: str,
                              ja_name: str = ""):
    """活动 → （各赛道的 [(赛道, 名次), …], 主赛道, 主名次）。

    赛道 / 名次 VocaDB 都没有（releaseEvents 只说明「参加了哪一届」），所以去**爬那一届的
    活动模板**（`The VOCALOID Collection2022春`）现读：榜单按名次分段（`61-70位`）、段内按
    名次排列，所以名次能直接算出来；TOP100 / ROOKIE / REMIX **多个赛道都在就都返回**
    （实测 涅槃(HotaRu)：TOP100 第 70 名 + ROOKIE 第 42 名）。
    哪个赛道都没有（含列在「未上榜歌曲」里）→ 榜外。
    模板取不到（不存在 / 网络失败）才退回问用户，那时只问得出一个赛道。
    """
    template = family_template.collection_template_name(event_name)
    places = family_template.find_collection_places(template, page_name, ja_name)
    if places is None:                                    # 模板读不到 → 照旧问用户
        track, rank = prompt_vocaloid_collection_details(event_name)
        ranked = [(track, int(rank))] if (track in family_template.COLLECTION_TRACKS
                                          and rank and str(rank).isdigit()) else []
        return ranked, track, rank
    if not places:                                        # 两榜都没有（也没有列在别处）
        return [], family_template.UNRANKED_TRACK, None
    ranked = [(place.track, place.rank) for place in places]
    for place in places:
        logging.info("活动模板 %s：本曲在 %s（%s）",
                     template, place.section or place.track,
                     f"第 {place.rank} 名" if place.rank else "无名次")
    primary = places[0]
    return (ranked, primary.track,
            None if primary.rank is None else str(primary.rank))


def artist_aliases(name: str) -> List[str]:
    """按名字在 VocaDB 上找艺术家（P主），返回它的别名表 —— 里面那个 ASCII 名就是罗马音。

    同名条目给**旧条目**起名字时用它兜底（见 `utils.disambig.move_target`）：
    实测 `雄之助` → artist 23981，`additionalNames = "Yunosuke, 유노스케"` → `Yunosuke`。
    ⚠️ **必须带 `fields=AdditionalNames`**：不传这个字段，`additionalNames` 返回 null。
    查不到 / 网络失败返回空表（调用方退回用原名）。
    """
    name = str(name or "").strip()
    if not name:
        return []
    try:
        resp = vocadb_get(VOCADB_ARTIST_QUERY_URL, params={
            "query": name, "lang": "Default", "maxResults": 5,
            "fields": "AdditionalNames", "nameMatchMode": "Auto"})
        items = resp.json().get("items") or []
    except Exception as e:                        # 查不到就当没有别名，别把生成流程打断
        logging.warning("查 %s 的 VocaDB 艺术家信息失败：%s", name, e)
        return []
    exact = [item for item in items if str(item.get("name") or "").strip() == name]
    for item in exact or items:                   # 同名优先，否则就取第一条
        aliases = [part.strip() for part in split(str(item.get("additionalNames") or ""))
                   if part.strip()]
        if aliases:
            return aliases
    return []


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _artist_string_part(artist_string: str, index: int) -> List[str]:
    """取 artistString 里「feat.」前后那半串名字（0 = P主那半，1 = 歌姬那半）。

    `split` 会把开头 / 结尾的分隔符也切出一段空串（"P feat. A" → ['', 'A']），
    空名字会让「演唱」栏多出一个 `[[]]`、charas 里多一项——统一在这里滤掉。
    没有 feat.（或只有前半）时返回空表，别让 ft. 那半截把调用方炸了。
    """
    parts = str(artist_string or "").split("feat.")
    if len(parts) <= index:
        return []
    return [name for name in split(parts[index]) if not is_empty(name)]


def parse_creators(artists: list, artist_string: str) -> Creators:
    mapping: Dict[str, List[Person]] = dict()
    for artist in artists:
        artist_type = ""
        engine_hint = ""
        if 'artist' in artist:
            name = artist['artist']['name']
            artist_type = artist['artist'].get('artistType') or ""
            # 名字里带 `(VOICEPEAK)` 这类标记、而 VocaDB 的 artistType 又认不出引擎时
            # （实测《彩色粉笔装饰物》：`小春六花 (VOICEPEAK)` 的 type 是 OtherVoiceSynthesizer），
            # 就把标记当引擎提示带上 —— 名字马上要被归一化成「小春六花」了，
            # 不在这儿记下来，后面的引擎分类就只剩 CeVIO 了（用户 2026-10-05 报的）
            if not engine_from_type(artist_type):
                engine_hint = engine_from_bank_marker(name)
            if artist_type in VOICE_ARTIST_TYPES:
                # shorten names like 初音ミク V4X / Synthesizer V AI Megpoid
                name = name_shorten(name)
            names_other = split(artist['artist']['additionalNames'])
        else:
            name = artist['name']
            names_other = []
        roles = artist['roles']
        if roles == 'Default':
            roles = artist['categories']
        if roles == 'Other':
            continue
        roles = split(roles)
        person = Person(name.strip(), names_other, engine_hint or artist_type)
        for role in roles:
            role = role.strip()
            if role in mapping:
                mapping[role].append(person)
            else:
                mapping[role] = [person]
    if "Vocalist" in mapping:
        vocalists: List[Person] = mapping.get("Vocalist")
    else:
        vocalists = [Person(name_shorten(n)) for n in _artist_string_part(artist_string, 1)]
    if "Producer" in mapping:
        producers = mapping.pop("Producer")
    else:
        producers = [Person(n) for n in _artist_string_part(artist_string, 0)]
    staffs: dict = dict()
    for role in mapping:
        staffs[role_transform(role)] = mapping[role]
    if "作词" not in staffs and "作曲" not in staffs:
        staffs['词曲'] = producers
    elif "作词" not in staffs:
        staffs['作词'] = producers
    elif "作曲" not in staffs:
        staffs['作曲'] = producers
    if "曲绘" not in staffs and "PV制作" in staffs:
        staffs["曲绘"] = staffs["PV制作"]
    merge_composer_lyricist(staffs)
    return Creators(producers, vocalists, staffs)


def parse_videos(videos: list, date_fallback: datetime = datetime.fromtimestamp(0)) -> List[Video]:
    service_to_site: dict = {
        'NicoNicoDouga': VideoSite.NICO_NICO,
        'Youtube': VideoSite.YOUTUBE
    }
    result = []
    for v in videos:
        service = v['service']
        if v['pvType'] == 'Original' and service in service_to_site.keys():
            url = v['url']
            # FIXME: only one video per site allowed for now
            video = video_from_site(service_to_site.pop(service), url)
            if video:
                if video.uploaded and video.uploaded.year < 2000:
                    video.uploaded = date_fallback
                # VocaDB 把「非公開 / 削除済み」的稿件标成 `disabled`（实测 sm42552106 与
                # sm41942916 都返回 400/404，同曲还活着的 sm43425344 则是 false）。
                # 站点那边认不出来时（nicolog 被 Cloudflare 挡住、YouTube 直接给不出元数据）
                # 就靠这个标记把投稿栏写成 `{{VOCALOID_Songbox/card}}`，别再当正常投稿。
                video.deleted = video.deleted or bool(v.get('disabled'))
                result.append(video)
    return result


def _normalize_album_name(name: str) -> str:
    """比对专辑名用：去掉空白。实测同一张碟有「ナ2モノ」/「ナ2 モノ」两种写法。"""
    return re.sub(r"\s+", "", name or "")


def get_album_track_song_ids(album_id: int) -> List[int]:
    """专辑里各曲目对应的 VocaDB 歌曲 id；取不到返回空表（调用方据此保守处理）。"""
    if not album_id:
        return []
    url = f"https://vocadb.net/api/albums/{album_id}?fields=Tracks"
    try:
        resp = vocadb_get(url)
        detail = json.loads(resp.text)
    except Exception as e:
        logging.warning("取专辑 %s 的曲目失败：%s", album_id, e)
        return []
    return [track['song']['id'] for track in detail.get('tracks') or []
            if (track.get('song') or {}).get('id')]


def _is_own_single_album(album: dict, name: str, song_names: Sequence[str],
                         song_id: int) -> bool:
    """专辑名就是歌曲原名，而且整张专辑只收录这一首曲子（这首歌自己的单曲碟）。"""
    if not song_id:
        return False
    wanted = {_normalize_album_name(name), _normalize_album_name(album.get('name') or "")}
    if not wanted & {_normalize_album_name(other) for other in song_names if other}:
        return False
    return get_album_track_song_ids(int(album.get('id') or 0)) == [song_id]


def parse_albums(albums: list, song_names: Sequence[str] = (), song_id: int = 0) -> List[str]:
    """VocaDB 的 `albums` → 收录专辑名。

    **同名单曲不写**（用户 2026-09 要求）：专辑名就是歌曲原名、而且整张专辑只收录这一首曲子时，
    这句「收录于专辑《ナ2モノ》」等于没说（实测 ナ2モノ 的 Single 版就只有它一首）；
    曲目数取不到（网络 / 接口失败）时保守起见照旧写出来。
    """
    result: List[str] = []
    for album in albums or []:
        name = album.get('defaultName') or album.get('name') or ""
        if _is_own_single_album(album, name, song_names, song_id):
            logging.info("专辑《%s》与歌曲同名且只收录本曲，不写进简介。", name)
            continue
        result.append(name)
    return result


def _version_artist_names(artist_string: str, index: int) -> List[str]:
    """取 artistString 里「feat.」前后那半串名字，**只按逗号 / 顿号切**。

    不能用 `utils.string.split`（它连空格也切）：alternateVersions 里的歌姬常带声库后缀，
    「初音ミク V4X (Original)」会被切成三段。留整之后再 name_shorten 归一化。
    """
    parts = str(artist_string or "").split("feat.")
    if len(parts) <= index:
        return []
    return [name.strip() for name in re.split(r"[，,、]", parts[index]) if name.strip()]


def parse_other_versions(alternate_versions: list) -> List[OtherVersion]:
    """VocaDB 详情里的 `alternateVersions` → 同一首歌的其他版本（参《鸟之诗》）。

    每一项长这样（只有 artistString 没有 artists 列表）：
    ```json
    {"id": 629, "name": "鳥の詩", "songType": "Cover",
     "artistString": "でんげん feat. 初音ミク", "publishDate": "2007-09-01T00:00:00Z"}
    ```
    所以 P主 / 歌姬 都得从 artistString 里按「feat.」前后切（和主条目的 `parse_creators`
    用的是同一套切法）；版本名与后续的 B 站链接收集见 utils/other_versions.py。
    """
    versions: List[OtherVersion] = []
    for item in alternate_versions or []:
        artist_string = item.get('artistString') or ""
        publish_date = item.get('publishDate')
        versions.append(OtherVersion(
            version_id=int(item.get('id') or 0),
            name=item.get('name') or item.get('defaultName') or "",
            song_type=item.get('songType') or "",
            # 歌姬名照样归一化（"初音ミク V4X (Original)" → 初音ミク），和主条目 parse_creators 一致
            vocalists=[name_shorten(name) for name in _version_artist_names(artist_string, 1)],
            producers=_version_artist_names(artist_string, 0),
            artist_string=artist_string,
            pv_services=item.get('pvServices') or "",
            publish_date=str_to_date(publish_date) if publish_date else None,
        ))
    return versions


def get_version_details(song: Song, version: OtherVersion) -> None:
    """取其他版本**自己**的详情：它在 niconico / YouTube 上的稿件与收录它的专辑。

    候选列表里的 `alternateVersions` 只有 `pvServices`（站点名），既没有稿件 ID 也没有专辑，
    所以要单独请求这个版本的详情（`pvs` + `albums`）；B 站那份由用户提供
    （`OtherVersion.video`），`pvs` 里的 Bilibili 跳过，免得同一个站点出现两份。
    日期取不到（站点 403 / 被风控）时退回这个版本的 `publishDate`，与主条目的做法一致；
    VocaDB 抽风时只当这个版本没有这些信息，不影响生成。
    """
    if not version.version_id:
        return
    url = f"https://vocadb.net/api/songs/{version.version_id}/details"
    try:
        resp = vocadb_get(url)
        response = json.loads(resp.text)
    except Exception as e:                      # 取不到就只当这个版本没有 nico / yt 稿件
        logging.warning("取其他版本「%s」的投稿信息失败：%s",
                        version.name or version.label, e)
        return
    pvs = [pv for pv in response.get('pvs') or [] if pv.get('service') != 'Bilibili']
    fallback = version.publish_date or datetime.fromtimestamp(0)
    version.videos = parse_videos(pvs, fallback)
    song_names = [getattr(song, 'name_jap', ''), getattr(song, 'name_chs', ''),
                  *(getattr(song, 'name_other', None) or [])]
    version.albums = parse_albums(response.get('albums'), song_names, version.version_id)
    # 活动（ボカコレ 等）：VocaDB 的 `releaseEvents` 是**按版本**记的，所以其他版本也检测得到；
    # 赛道 / 名次去爬那一届的活动模板（见 detect_collection_details），两榜都在就都记上。
    # 不拿 atwiki 那条路兑底：按歌名去查很可能查到**主版本**的记录，安到别人头上。
    event_name = get_vocaloid_collection_event(response.get('releaseEvents'))
    if event_name:
        version.vocaloid_collection = event_name
        (version.vocaloid_collection_places, version.vocaloid_collection_track,
         version.vocaloid_collection_rank) = detect_collection_details(
            event_name, song.name_chs, song.name_jap)


def process_image(image_in: Path, image_out: Path) -> None:
    """按配置裁剪封面黑边并输出到 image_out（取色已交由可视化颜色编辑器处理）。"""
    try:
        if image_in is not None and image_in.exists():
            if get_config().image.crop:
                remove_black_boarders(image_in, image_out)
            else:
                image_out.unlink(missing_ok=True)
                image_in.rename(image_out)
    except Exception as e:
        logging.error("Can't process cover image", exc_info=e)


VOCADB_SONG_URL_PATTERN = re.compile(r"(?:/S/|/Details/)(\d+)")


def parse_song_id_from_url(url: str) -> Optional[str]:
    """从 Vocadb 歌曲链接中解析出歌曲 ID，也支持直接输入纯数字 ID。"""
    url = url.strip()
    if url.isdigit():
        return url
    match = VOCADB_SONG_URL_PATTERN.search(url)
    return match.group(1) if match else None


def song_id_of_payload(payload: dict) -> Optional[str]:
    """从一份 VocaDB JSON 里取歌曲 id。

    ⚠️ **实测（2026-10-03）**：`/api/songs/{id}/details` 的顶层**没有** `id`，
    它在 `song.id` 里（顶层的 `name` 也没有，是 `song.defaultName`）；
    只有**搜索结果**那一项才是顶层 `id` / `defaultName`。
    以前只读顶层 `id`，用户把真 JSON 粘进来时会取不到 id → 当成「没填」→ 白白浪费一次手粘。
    """
    if not isinstance(payload, dict):
        return None
    song = payload.get("song")
    for value in (payload.get("id"), (song or {}).get("id") if isinstance(song, dict) else None):
        if value:
            return str(value)
    return None


def is_details_payload(payload) -> bool:
    """这份 JSON 是不是 `/api/songs/{id}/details`（而不是搜索结果那种单曲信息）。

    实测（2026-10-03）：详情带 `song` / `artists` / `lyricsFromParents`；
    而搜索结果那一项（或 `/api/songs/{id}`）只有 `defaultName` / `pvs` / `tags` ——
    **光凭 `defaultName` / `pvs` 判断会把搜索结果当成详情**，后面 `get_song_by_name` 就会
    在 `response['artists']` 上 KeyError。
    """
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("song"), dict):
        return True
    return any(key in payload for key in ("artists", "lyricsFromParents", "albums"))


def song_id_from_search_items(items, song_name: str = "") -> Optional[str]:
    """从搜索结果列表（`{"items": [...]}`）里挑出这首歌的 id。

    规则和 `search_song_id()` 一致：先找 `defaultName` 完全相同的；
    没有完全相同的、而列表里只有一项时就用那一项（用户自己粘的，应该就是它）。
    """
    if not isinstance(items, list):
        return None
    name = str(song_name or "").strip()
    if name:
        for item in items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("defaultName") or item.get("name") or "").strip()
            if text == name and item.get("id"):
                return str(item["id"])
    if len(items) == 1 and isinstance(items[0], dict) and items[0].get("id"):
        return str(items[0]["id"])
    return None


def parse_manual_song_reply(reply: str, song_name: str = ""):
    """手动那一步的输入：VocaDB 链接 / 纯数字 ID / **直接粘的 JSON**。

    → `(歌曲 id, 解析好的 JSON 或 None)`。粘 JSON 这条路是给「VocaDB 被 Cloudflare
    挡住」用的（用户 2026-10-03）：浏览器里能打开 `https://vocadb.net/api/songs/<id>/details`，
    把那段 JSON 复制进工具，就不需要工具自己能联网过校验。

    也吃**搜索结果那段 JSON**（`{"items": [...]}`）：用户 2026-10-03 实际粘的就是它
    （搜索页上随手复制的），以前一律当成「不是歌曲 JSON」直接丢掉 —— 明明里面有 id，
    白浪费一次粘贴。
    """
    text = str(reply or "").strip()
    if not text:
        return None, None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            return None, None
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            song_id = song_id_from_search_items(payload["items"], song_name)
            if song_id:
                logging.info("粘的是 VocaDB 搜索结果 JSON：挑到 id=%s", song_id)
            return song_id, payload
        if not isinstance(payload, dict) or not any(
                key in payload for key in ("defaultName", "name", "artists", "pvs", "song")):
            return None, None                        # 不是歌曲详情（粘错了）
        return song_id_of_payload(payload), payload
    return parse_song_id_from_url(text), None


def prompt_manual_song():
    """问用户要 VocaDB 歌曲链接 / ID / 直接粘 JSON → `(id, JSON 或 None)`。"""
    reply = prompt_response(_("vocadb_manual_url_prompt"))
    if is_empty(reply):
        return None, None
    song_id, payload = parse_manual_song_reply(reply)
    while song_id is None:
        reply = prompt_response(_("vocadb_manual_url_invalid"))
        if is_empty(reply):
            return None, None
        song_id, payload = parse_manual_song_reply(reply)
    return song_id, payload


def prompt_manual_song_json(song_id: Optional[str] = None,
                            song_name: str = "") -> Optional[dict]:
    """让用户把 `/api/songs/{id}/details` 那整段 JSON 粘进来（多行输入框）。

    ⚠️ **提示里绝不能出现带占位符的网址**（用户 2026-10-03 报的）：以前提示写的
    `https://vocadb.net/api/songs/<歌曲ID>/details`，用户直接复制到浏览器就会跳到
    `https://vocadb.net/Error?code=404`（那个 `<歌曲ID>` 没换成数字）。
    现在：已知 id 就给**完整可点开的地址**；未知 id 就只举例一个真实存在的地址，
    并把「数字要自己换」写在提示里。

    粘完留一个空行结束；认不出东西就给一次重试机会，再不行（留空）就放弃。
    返回值里可能只有 id（用户粘的是搜索结果 JSON）—— 那种情况由调用方接着去取详情。
    """
    url = (f"https://vocadb.net/api/songs/{song_id}/details" if song_id
           else VOCADB_JSON_EXAMPLE_URL)
    prompt = f"{_('vocadb_json_prompt')}\n{url}\n{_('vocadb_json_hint')}"
    for _attempt in range(2):
        lines = prompt_multiline(prompt, terminator=is_empty)
        text = "\n".join(lines).strip()
        if not text:
            return None
        pasted_id, payload = parse_manual_song_reply(text, song_name)
        if payload is None:
            logging.warning("%s", _json_paste_problem(text))
            continue
        if not is_details_payload(payload):
            # 粘的是搜索结果（只有 id，没有 artists / pvs 详情）——不能当详情用，
            # 但** id 拿到了**：把它交给调用方去取详情（至少不会白粘）。
            logging.info("粘的是搜索结果 JSON（只得到 id=%s），接着去取详情。", pasted_id)
            return {"_only_song_id": pasted_id} if pasted_id else None
        logging.info("已手动粘入 VocaDB 歌曲详情 JSON（id=%s）", song_id_of_payload(payload))
        return payload
    return None


def _json_paste_problem(text: str) -> str:
    """粘错东西时给一句**对症**的话（用户 2026-10-03 照抄网址得到 404 那次）。"""
    head = text.lstrip()[:200].lower()
    if "error?code=404" in head or "<html" in head or "<!doctype" in head:
        return _("vocadb_json_is_webpage")
    if not text.lstrip().startswith("{"):
        return _("vocadb_json_is_link")        # 粘成了链接 / 纯数字
    return _("vocadb_json_invalid")


def only_song_id(payload) -> Optional[str]:
    """`prompt_manual_song_json` 交回来的「只有 id」包（用户粘的是搜索结果 JSON）。"""
    if isinstance(payload, dict) and set(payload) == {"_only_song_id"}:
        return str(payload["_only_song_id"] or "") or None
    return None


def _prompt_blocked_json(song_id: Optional[str] = None,
                         song_name: str = "") -> Optional[dict]:
    """VocaDB 被挡住时问一句：要不要手动粘贴 JSON 继续？（已知 id 就把地址一起给出来）"""
    if prompt_choices(_("vocadb_blocked_manual"), [_("Yes"), _("No")]) != 1:
        return None
    return prompt_manual_song_json(song_id, song_name)


def _details_payload(song_name: str, song_id: Optional[str] = None
                     ) -> "Tuple[Optional[str], Optional[dict]]":
    """拿某一首歌的 `/details` JSON → `(id, JSON)`；拿不到就 `(None, None)`。

    顺序：**本地缓存** → 联网搜 → （被 Cloudflare 挡住时）问用户粘 JSON
    → （开关打开时）手动输链接 → 真的去请求 `/details`。

    拿到（联网或手粘的）都会写进缓存：同一个条目再生成一次就**一次请求都不发**
    —— 既是 VocaDB API 文档第 ② 条的要求，也是被风控之后最实用的兜底：
    手动粘一次，以后就不用再粘了。
    """
    payload: Optional[dict] = None
    if not song_id:
        song_id = cached_song_id(song_name)
        if song_id:
            logging.info("VocaDB 缓存命中：%s → id %s（不联网）", song_name, song_id)
    if song_id:
        payload = cached_song_payload(song_id)
    if payload is None and not song_id:
        try:
            song_id = search_song_id(song_name)
        except VocadbBlocked:
            logging.error("在 VocaDB 上搜「%s」时被挡住；处理办法见上面那条日志。", song_name)
            payload = _prompt_blocked_json(song_name=song_name)
        else:
            store_song_id(song_name, song_id)
    if payload is None and not song_id and get_config().vocadb_manual_url:
        song_id, payload = prompt_manual_song()
    # 用户粘的是**搜索结果** JSON：里面只有 id、没有详情（artists / pvs / albums）
    # → 把 id 抽出来，接着去取详情；网络不行时下面会再用**确切地址**问一次。
    # ⚠️ 不能把搜索结果当详情用：`get_song_by_name()` 会在 `response['artists']` 上 KeyError。
    if payload is not None and not is_details_payload(payload):
        new_id = only_song_id(payload) or song_id_of_payload(payload)
        if new_id:
            song_id = new_id
            store_song_id(song_name, song_id)      # 下次直接命中，不用再粘
        payload = None
    if payload is None:
        if not song_id:
            return None, None
        logging.info(f"Fetching song details with id {song_id} from vocadb.")
        try:
            resp = vocadb_get(f"https://vocadb.net/api/songs/{song_id}/details")
            payload = json.loads(resp.text)
        except VocadbBlocked as blocked:
            # 搜到了 id、但取详情时被挡（用户 2026-10-03 就是在这个阶段去手动下载 JSON 的）：
            # 这时候**我们已经知道 id**，把完整地址给出来，不会让他拼出 Error?code=404。
            logging.error("取 VocaDB 歌曲详情被挡住（id=%s）；处理办法见上面那条日志。", song_id)
            pasted = _prompt_blocked_json(song_id, song_name)
            if pasted is None:
                return song_id, None
            if not is_details_payload(pasted):
                # 又粘了一份搜索结果（id 已经知道了）→ 别再纠缠，这次先跳过这首歌
                logging.warning("粘进来的仍不是歌曲详情（只有搜索结果），先跳过这首歌。")
                return song_id, None
            payload = pasted
    if not song_id:
        song_id = song_id_of_payload(payload)       # 粘 JSON 时 id 从里面取（详情在 song.id 里）
    store_song_payload(song_id, payload)
    # **手粘的 JSON 也要把「歌名 → id」记下来**：不记的话下一次生成同一个条目时还是查不到 id
    # （详情缓存是按 id 存的），用户就得再粘一次。
    store_song_id(song_name, song_id)
    return song_id, payload


def prompt_manual_translation(creators: Creators) -> Lyrics:
    """中文翻译自动找不到时问一句「要不要手动输入」；要就开歌词页。

    歌姬列表必须一起交给歌词页：否则「演唱者上色」面板里一个歌姬按钮都没有，
    界面只会写「（这首歌没有识别出歌姬，无法上色）」——用户 2026-09 报的
    ナ2モノ 就是这个：vocadb 明明有歌姬（初音ミク / 巡音ルカ），歌词页却认不出来。
    """
    if get_config().wikitext.lyrics_chs_fail_fast:
        return Lyrics()
    if prompt_choices(_("manual_trans"), [_("Yes"), _("No")]) != 1:
        return Lyrics()
    return get_manual_lyrics(charas=creators.vocalists_str())


def get_song_by_name(song_name: str, name_chs: str) -> Union[Song, None]:
    song_id, response = _details_payload(song_name)
    if not song_id or response is None:
        return None
    name_ja = song_name
    name_other = [n.strip() for n in utils.string.split(",")]
    creators: Creators = parse_creators(response['artists'], response['artistString'])
    lyricsList = response['lyricsFromParents']
    producer_temp = creators.producers[0].name if len(creators.producers) > 0 else ""
    if len(lyricsList) > 0:
        lyrics_ja = get_lyrics(response['lyricsFromParents'][0]['id'])
    else:
        logging.warning("Lyrics not found on vocadb.")
        lyrics_ja = get_japanese_lyrics(name_ja, producer_temp)
    lyrics = get_chinese_lyrics(song_name, producer_temp)
    if lyrics is None:
        lyrics = prompt_manual_translation(creators)
    if not is_empty(lyrics.lyrics_jap):
        lyrics_ja = lyrics.lyrics_jap
    lyrics_ja = lyrics_editor.process_lyrics_jap(lyrics_ja)
    # 「歌词括号里的假名」→ photrans：歌词页的自动识别 / AI 识别也会做这一步，
    # 这里先做一遍是为了终端模式（不开歌词页）也能生效，且两遍是幂等的。
    if get_config().wikitext.furigana_local:
        lyrics_ja = japanese.furigana_local(lyrics_ja)
    # AI 生成振假名（wikitext.furigana_all）：给没写读音的汉字补 {{photrans|漢字|かんじ}}。
    # 失败 / 没密钥 / 模型改动了正文都只是记日志，歌词原样继续走。
    lyrics_ja = ai_lyrics.generate_furigana(lyrics_ja)
    lyrics.lyrics_jap = lyrics_ja
    date_fallback = datetime.fromtimestamp(0)
    if 'song' in response:
        date_fallback = str_to_date(response['song']['publishDate'])
    videos = parse_videos(response['pvs'], date_fallback)
    video_bilibili = get_video_bilibili()
    if video_bilibili:
        # B 站 API 取不到（被风控 412 / 视频被删）时 video_from_site 会给 epoch 日期，
        # 不兜底就会把「1970年1月1日投稿至[[bilibili]]」写进条目；用 VocaDB 的投稿日顶上。
        if video_bilibili.uploaded.year < 2000:
            video_bilibili.uploaded = date_fallback
        videos.append(video_bilibili)
    albums = parse_albums(response['albums'], [name_ja, name_chs, *name_other],
                          _int_or_zero(song_id))
    release_event_name = get_vocaloid_collection_event(response.get('releaseEvents'))
    collection_places = []
    if release_event_name:
        vocaloid_collection = release_event_name
        # 赛道 / 名次：爬那一届的活动模板（两榜都在就都记上）；模板取不到才问用户
        (collection_places, vocaloid_collection_track,
         vocaloid_collection_rank) = detect_collection_details(release_event_name,
                                                               name_chs, name_ja)
    else:
        # VocaDB 没记活动时，才去 atwiki 碰碰运气（那里只能拿到一个名次，没有赛道）
        collection_info = get_vocaloid_collection_info(name_ja, producer_temp)
        vocaloid_collection = collection_info[0] if collection_info else None
        vocaloid_collection_rank = collection_info[1] if collection_info else None
        vocaloid_collection_track = None
    if get_config().image.download_cover:
        res = download_thumbnail(videos, "cover.jpg")
        if res is None:
            # FIXME: what if no video?
            image_path, video = None, videos[0]
        else:
            image_path, video = res
    else:
        image_path, video = None, videos[0]
    cover_name = f"{safe_filename(name_chs)}封面.jpg"
    cover_path = get_output_path().joinpath(cover_name)
    process_image(image_path, cover_path)
    illustrators = creators.staffs.get("曲绘", None)
    image: Image = Image(image_path, cover_name, video.url, illustrators)
    return Song(name_ja, name_chs, name_other, creators, lyrics, image, videos, albums, None,
                vocaloid_collection, vocaloid_collection_rank, vocaloid_collection_track,
                other_versions=parse_other_versions(response.get('alternateVersions')),
                vocaloid_collection_places=collection_places)


def get_lyrics(lyrics_id: str) -> str:
    logging.info("Getting Japanese lyrics from vocadb.")
    url = f"https://vocadb.net/api/songs/lyrics/{lyrics_id}?v=25"
    resp = vocadb_get(url)
    response = json.loads(resp.text)
    return response['value']


def search_vocadb(name: str, params: dict) -> list:
    params = {**params,
              'query': name}
    resp = vocadb_get(VOCADB_SONG_QUERY_URL, params=params)
    response = json.loads(resp.text)
    response = response['items']
    response = [song for song in response if song['defaultName'].strip() == name]
    return response


def search_narrow(name: str) -> list:
    return search_vocadb(name, PARAMS_NARROW)


def search_broad(name: str) -> list:
    return search_vocadb(name, PARAMS_BROAD)


def search_song_id(name: str) -> Union[str, None]:
    logging.info(f"Searching for song named {name} on Vocadb")
    response = search_narrow(name)
    narrow: bool = True
    if len(response) == 0:
        narrow = False
        logging.info(_("narrow_to_broad"))
        response = search_broad(name)
    if len(response) == 0:
        logging.error(_("no_vocadb"))
        return None
    while len(response) > 1 or (len(response) == 1 and get_config().vocadb_manual):
        options = [f"{song['defaultName']} by {song['artistString']}"
                   for song in response]
        options.append(_("none_of_above"))
        result = prompt_choices(_("multiple_vocadb_results"), options)
        if result == len(options):
            if narrow:
                narrow = False
                logging.info(_("broader_search"))
                response = search_broad(name)
                continue
            else:
                logging.error(_("no_vocadb"))
                return None
        return response[result - 1]['id']
    r = response[0]['id']
    logging.info(f"Using {response[0]['defaultName']} 'by' {response[0]['artistString']}")
    return r
