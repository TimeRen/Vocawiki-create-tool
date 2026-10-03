import json
import logging
import re
import time
import urllib
from datetime import datetime
from pathlib import Path
from typing import Union, List, Dict, Optional, Sequence

import requests

import utils.string
from config.config import get_config, get_output_path
from i18n.i18n import _
from models.creators import Person, Creators, merge_composer_lyricist, role_transform
from models.song import Song, Image, get_manual_lyrics, Lyrics
from models.video import (Video, VideoSite, OtherVersion, video_from_site,
                          get_video_bilibili, str_to_date)
from utils import string, japanese, lyrics_editor, ai_lyrics
from utils import family_template
from utils.at_wiki import get_chinese_lyrics, get_japanese_lyrics, get_vocaloid_collection_info
from utils.helpers import prompt_choices, prompt_response, http_get
from utils.image import download_thumbnail, remove_black_boarders
from utils.name_converter import name_shorten
from utils.string import split, is_empty, safe_filename

VOCADB_SONG_QUERY_URL = "https://vocadb.net/api/songs"
VOCADB_ARTIST_QUERY_URL = "https://vocadb.net/api/artists"
# 被 Cloudflare 的人机校验挡住（实测 403，页面上是「Just a moment...」）时给的提示。
# 2026-10-03 用户报的这个：不是我们的 UA 不对 —— 浏览器 UA / 工具 UA / 无 UA 实测都是 403。
VOCADB_BLOCK_NOTICE = (
    "VocaDB 被 Cloudflare 的人机校验挡住了（HTTP 403）。可以："
    "① 在 config.yaml 里配好 proxies 后再跑（换个出口 IP）；② 过几分钟重试；"
    "③ 先用浏览器打开 https://vocadb.net 确认站点本身没挂。")
# 挡上一次之后，这么长时间内不再去碰 VocaDB（免得一个歌名一次、连环 403）
VOCADB_BLOCK_COOLDOWN = 600
_blocked_until = 0.0


class VocadbBlocked(RuntimeError):
    """VocaDB 取不到数据（被人机校验 / 限流挡住）—— 调用方拿不到就不该硬崩。"""


def _is_challenge(response) -> bool:
    """这个响应是不是 Cloudflare 的挑战页（或限流）。"""
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        return True
    if status != 403:
        return False
    body = str(getattr(response, "text", "") or "")[:600].lower()
    return "just a moment" in body or "cf-" in body or "cloudflare" in body


def vocadb_get(url: str, **kwargs):
    """访问 VocaDB 的 GET：被 Cloudflare 挡（403 / 429）重试一次，还不行就抛 `VocadbBlocked`。

    为什么要它：以前直接 `raise_for_status()`，一个 403 就把整个生成流程带堆栈打断
    （用户 2026-10-03 报的）。现在换成一句能看懂的原因，而且挡过一次之后短时间内不再请求。
    """
    global _blocked_until
    if time.time() < _blocked_until:
        raise VocadbBlocked(VOCADB_BLOCK_NOTICE)
    response = http_get(url, use_proxy=True, timeout=30, **kwargs)
    if _is_challenge(response):
        logging.warning("VocaDB 返回 %s（Cloudflare 人机校验）：%s；2 秒后重试一次",
                        response.status_code, url)
        time.sleep(2)
        response = http_get(url, use_proxy=True, timeout=30, **kwargs)
        if _is_challenge(response):
            _blocked_until = time.time() + VOCADB_BLOCK_COOLDOWN
            logging.error("%s（%s）", VOCADB_BLOCK_NOTICE, url)
            raise VocadbBlocked(VOCADB_BLOCK_NOTICE)
    response.raise_for_status()
    return response

# 这些 artistType 都是「歌手」（唱的人）：名字统一过一遍 `name_shorten`，
# 把声库前缀 / 版本后缀砍掉（`初音ミク V4X (Original)` → 初音ミク、
# `Synthesizer V AI Megpoid` → Megpoid）。以前只认 'Vocaloid' 一种，
# 于是 Synthesizer V / CeVIO / NEUTRINO 的歌姬名会整串漏进条目（用户 2026-09 报的《小小星座》）。
VOICE_ARTIST_TYPES = {'Vocaloid', 'UTAU', 'CeVIO', 'SynthesizerV', 'NEUTRINO', 'VoiSona',
                      'VOICEPEAK', 'Voicepeak', 'NewType',
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
        resp = http_get(VOCADB_ARTIST_QUERY_URL, use_proxy=True, params={
            "query": name, "lang": "Default", "maxResults": 5,
            "fields": "AdditionalNames", "nameMatchMode": "Auto"})
        resp.raise_for_status()
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
        if 'artist' in artist:
            name = artist['artist']['name']
            artist_type = artist['artist'].get('artistType') or ""
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
        person = Person(name.strip(), names_other, artist_type)
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
        resp = http_get(url, use_proxy=True)
        resp.raise_for_status()
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
        resp = http_get(url, use_proxy=True)
        resp.raise_for_status()
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


def prompt_manual_song_url() -> Optional[str]:
    """提示用户手动输入 Vocadb 歌曲链接，并解析出歌曲 ID。"""
    url = prompt_response(_("vocadb_manual_url_prompt"))
    if is_empty(url):
        return None
    song_id = parse_song_id_from_url(url)
    while song_id is None:
        url = prompt_response(_("vocadb_manual_url_invalid"))
        if is_empty(url):
            return None
        song_id = parse_song_id_from_url(url)
    return song_id


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
    song_id = search_song_id(song_name)
    if not song_id and get_config().vocadb_manual_url:
        song_id = prompt_manual_song_url()
    if not song_id:
        return None
    logging.info(f"Fetching song details with id {song_id} from vocadb.")
    url = f"https://vocadb.net/api/songs/{song_id}/details"
    resp = vocadb_get(url)
    response = json.loads(resp.text)
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
