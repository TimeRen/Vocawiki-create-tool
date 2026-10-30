import os
import sys

# Android 入口：在导入桌面那一整套之前就交给 Kivy 前端。
# 桌面栈（config / voca / upload…）只要有一个导入失败，进程在没有窗口的情况下就死掉，
# 手机上看到的就是「一打开就闪退」；放进 Kivy 里导入则能显示错误页并写 crash.log。
if __name__ == "__main__" and "ANDROID_ARGUMENT" in os.environ:
    from mobile.main import run as _run_android

    _run_android()
    raise SystemExit(0)

import asyncio
import logging
import re
import shutil
import subprocess
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Android：在导入 config 之前把可写目录和平台开关定好；桌面端完全不受影响。
if "ANDROID_ARGUMENT" in os.environ:
    os.environ.setdefault(
        "VOCAWIKI_HOME",
        os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH")
        or os.path.join(os.path.expanduser("~"), ".vocawiki"))
    os.environ.setdefault("VOCAWIKI_NO_BROWSER_FETCH", "1")
    os.environ.setdefault("VOCAWIKI_NO_LOCAL_UPSCALE", "1")

# ⚠️ onnxruntime 必须在 PyQt 的 DLL 之前加载：Win 上反过来导入会直接 0xC0000005
# （access violation，见本地 AI 超分）。导入失败不影响其它功能，后面会退回 Lanczos。
if "ANDROID_ARGUMENT" not in os.environ:   # Android 不打包 onnxruntime
    try:
        import onnxruntime  # noqa: F401
    except Exception:                   # noqa: BLE001 - 没装 / DLL 不兼容都只是没有本地超分
        pass

from config import data
from config.config import load_config, get_config, application_path, get_output_path
from models.creators import (credited_illustrators, person_list_to_str, Person, Staff,
                             merge_staff_rows, role_priority)
from models.song import Song, Lyrics, add_no_hover
from models.video import (HumanOriginal, VideoSite, Video, view_count_from_site, get_video,
                          get_human_original, only_canonical_videos)
from utils import login
from utils.helpers import prompt_choices, prompt_response, prompt_multiline
from utils.image import write_to_file
from utils.voca import get_producer_info
from utils.name_converter import (name_to_cat, name_to_chinese, name_to_wiki, vocaloid_names,
                                  get_engine, engines_of)
from utils.save_input import setup_save_input
from utils.producer_editor import generate_producer_template
from utils.vocalist_editor import generate_vocalist_template
from utils.string import (auto_lj, is_empty, datetime_to_ymd, datetime_to_iso,
                          assert_str_exists, join_string, protect_tildes, safe_filename)
from utils.upload import choose_characters, choose_cover_author
from utils.vocadb import get_song_by_name
from utils import browser_fetch
from utils import updater
from utils.color_editor import open_color_editor, build_initial_color_wiki
from utils import disambig
from utils import other_versions
from utils import ui
from utils import family_template
from utils.family_template import CollectionSync, FamilySync, PostedAt, collapse_all
from utils.lyrics_colors import build_colors_params, mark_lines
from utils.lyrics_editor import normalize_no_hover
from utils.submit_editor import open_submit_editor, CoverInfo

from i18n.i18n import _


def get_song_names(song: Song) -> List[str]:
    # FIXME: disable name_other?
    names = [auto_lj(song.name_jap), song.name_chs if song.name_chs != song.name_jap else None, *song.name_other]
    return [name for name in names if not is_empty(name)]


def get_song_engines(song: Song) -> List[str]:
    """本曲用到的合成引擎（见 `utils.name_converter.engines_of`）。

    传 `creators.vocalists`（`Person`）而不是名字 —— 里面带着 VocaDB 的 `artistType`，
    引擎就照它认（`紲星あかり` 是 Voiceroid 还是 AIVOICE，只有这个字段说得清）。
    """
    return engines_of(song.creators.vocalists)


def get_song_categories(song: Song) -> List[str]:
    """荣誉题头 / 简介里写的引擎；没有识别出歌姬时按 VOCALOID 处理。"""
    return get_song_engines(song) or ["VOCALOID"]


def get_engine_categories(song: Song) -> str:
    """[[分类:使用XX的歌曲]]。

    专属歌手模板（{{可不}} / {{歌爱雪}} 等）只给出「XX歌曲」分类，引擎分类必须自己写；
    实测 voca.wiki 上的条目（初音未来的消失、不去大海…）也都显式带着这一行。
    """
    return "".join(f"[[分类:使用{engine}的歌曲]]\n" for engine in get_song_engines(song))


def join_engines(categories: List[str]) -> str:
    linked = [f"[[{cat}]]" for cat in categories]
    if len(linked) <= 1:
        return "".join(linked)
    return "、".join(linked[:-1]) + "及" + linked[-1]


def get_cover_filename(song: Song) -> str:
    """Songbox 的 |image 参数，同时也是上传到 Vocawiki 的文件名（两者必须一致）。

    同名条目（消歧义）时用「歌名(P主名)」，与条目名保持一致（参 向日葵(Project Lumina).jpg）。
    """
    return f"{safe_filename(getattr(song, 'page_name', None) or song.name_chs)}.jpg"


def get_other_version_cover_name(song: Song, version) -> str:
    """其他版本的封面文件名（= 该版本 Songbox 的 `|image`，也是上传到 Vocawiki 的文件名）。

    主版本用 `<条目名>.jpg`，其他版本带上版本短名，免得几个版本互相覆盖：
    `<条目名>(镜音连版).jpg`（站上写成 `鸟之诗 镜音连.png`，这里统一成主版本那套命名）。
    """
    label = getattr(version, "tab_label", "") or getattr(version, "label", "") or "其他版本"
    page_name = getattr(song, "page_name", None) or song.name_chs
    return f"{safe_filename(f'{page_name}({label})')}.jpg"


def prepare_disambig(song: Song) -> None:
    """探测 Vocawiki 上的同名条目，决定条目名并准备好顶部模板（受 wiki.disambiguate 控制）。"""
    if not get_config().wiki.disambiguate:
        return
    plan = disambig.detect(song)
    if plan.error:
        logging.warning("同名条目处理：%s", plan.error)
    disambig.finish_plan(plan, song)
    song.disambig = plan
    if plan.needed:
        logging.info("检测到同名条目（%s），本条目使用「%s」", plan.base_title, plan.our_title)


def video_card(video: Video) -> str:
    """`{{VOCALOID Songbox/card|平台|ID|日期|再生=N|class=deleted}}` 一行。

    非公開 / 删稿的视频用它写进 Songbox 的 `|投稿 =`：模板会自动补「最终记录」，
    并且自己生成「YYYY年M月D日投稿至XX的歌曲」这类日期分类。
    日期按站上统一的 ISO 格式写（`2013-09-24`），见 `datetime_to_iso()`。
    """
    platform = {VideoSite.NICO_NICO: "nnd", VideoSite.BILIBILI: "bb",
                VideoSite.YOUTUBE: "yt"}[video.site]
    parts = [platform, video.identifier, datetime_to_iso(video.uploaded)]
    if video.views > 0:
        parts.append(f"再生={video.views:,}")
    if getattr(video, "deleted", False):
        parts.append("class=deleted")
    return "{{VOCALOID_Songbox/card|" + "|".join(parts) + "}}"


def video_embed(video: Video) -> str:
    """人声本家等版本用的视频嵌入模板（参 如月车站、红色房间、《我是，我们是》）。

    niconico 站点没有播放器模板，用站内已有的 {{sm}} 生成链接；
    YouTube 用 {{YoutubeVideo}}，B 站用 {{BilibiliVideo}}。
    """
    if video.site == VideoSite.YOUTUBE:
        return f"{{{{YoutubeVideo|id={video.identifier}}}}}"
    if video.site == VideoSite.BILIBILI:
        return f"{{{{BilibiliVideo|id={video.identifier}}}}}"
    return f"{{{{sm|{video.identifier}}}}}"


def song_human_original(song: Song) -> Optional[HumanOriginal]:
    """已收集到的人声本家（没有就是 None）。

    ⚠️ 这个名字不能叫 `get_human_original`：`models.video` 里那个同名函数是**问用户**的，
    而 `generate()` 还要调它；以前这里同名遮蔽了导入，`wikitext.human_original` 一开启
    `generate()` 就会 `TypeError: missing 1 required positional argument: 'song'`。
    """
    return getattr(song, "human_original", None)


def human_original_links(song: Song) -> List[str]:
    """人声本家的视频嵌入模板：**有 B 站稿件就只写 B 站那一个**。

    参 voca.wiki 红色房间 / 如月车站 / 小小星座：`;人声本家` 下面只有一行
    `{{BilibiliVideo|id=…}}`，即便这份人声本家还挂在 niconico / YouTube 上
    （用户 2026-09 要求：「除非没有 bilibili 视频才会输出 YoutubeVideo 模板，否则不会输出」）。
    一个 B 站稿件都没有时才退回 nico / YouTube 的写法（`{{sm}}` / `{{YoutubeVideo}}`）——
    那种情况下 `human.video` 是这份人声本家**唯一**能指的稿件，不写就没有东西可写了。
    """
    human = song_human_original(song)
    if human is None:
        return []
    if human.bilibili is not None:
        return [video_embed(human.bilibili)]
    return [video_embed(video) for video in (human.video,) if video]


def human_original_sentence(song: Song) -> str:
    """简介里的那句「另有…人声本家。」（参 红色房间：另有x0o0x_演唱的人声本家。）

    工具不追问演唱者，统一写成「P主本人」；若实际是其他唱见，在提交窗口里改这一句。
    """
    if not human_original_links(song):
        return ""
    return "另有P主本人演唱的人声本家。"


def view_rank(views: int) -> int:
    """播放量对应的等级：1 = 殿堂（10 万）、2 = 传说（100 万）、3 = 神话（1000 万）。"""
    if views >= 10000000:
        return 3
    if views >= 1000000:
        return 2
    return 1


def create_honor_header(song: Song) -> str:
    """`{{虚拟歌手歌曲荣誉题头|引擎…|nrank=1|…}}`；没有达到殿堂的站点时返回空串。

    多版本条目里每个版本各写各的（见 `other_version_honor_header`）。
    """
    videos = sorted(song.videos, key=lambda v: v.uploaded)
    categories = get_song_categories(song)
    rank_fields = []
    for site, rank_name in ((VideoSite.NICO_NICO, "nrank"),
                            (VideoSite.YOUTUBE, "yrank"),
                            (VideoSite.BILIBILI, "brank")):
        video = get_video(videos, site)
        if site == VideoSite.BILIBILI and video and not video.canonical:
            continue
        if video and video.canonical and video.views >= 100000:
            rank_fields.append(f"{rank_name}={view_rank(video.views)}")
    if not rank_fields:
        return ""
    return "{{虚拟歌手歌曲荣誉题头|" + "|".join([*categories, *rank_fields]) + "}}"


def _about_line(song: Song) -> str:
    """同名条目模板 + 换行（没有就是空串），参 向日葵(Teary Planet)。"""
    about = disambig.top_template(getattr(song, "disambig", None))
    return f"{about}\n" if about else ""


def _title_replace(song: Song) -> str:
    """顶部那行「标题模板」：中文名 ≠ 日文名时写 `{{标题替换|日文名}}`。

    中文名与日文名一样（`Redial`、「向日葵」这种同形写法）但条目名带了 `(P主名)` 后缀时，
    改用 `{{标题格式化}}` 把后缀从标题里去掉 —— 否则页面标题会一直显示 `Redial(鹤三)`
    （参 voca.wiki《向日葵(Teary Planet)》、《Redial(kz)》）。没有后缀就什么都不写
    （模板自己会报「错误使用标题格式化」）。
    """
    if song.name_chs != song.name_jap:
        return "{{标题替换|" + auto_lj(song.name_jap) + "}}\n"
    if (getattr(song, "page_name", None) or song.name_chs) != song.name_chs:
        return f"{disambig.TITLE_FORMAT_TEMPLATE}\n"
    return ""


def create_page_title(song: Song) -> str:
    """页面最顶部那两行：同名条目模板 + 标题模板（`{{标题替换}}` / `{{标题格式化}}`）。

    多版本条目里它们留在 `{{tabs}}` **外面**（参 voca.wiki《鸟之诗》）。
    """
    return _about_line(song) + _title_replace(song)


def create_header(song: Song) -> str:
    """单版本条目的顶部：About + 荣誉题头 + 标题模板 + Songbox（顺序与旧版一致）。

    多版本条目改用 `create_page_title()` + `create_tabs()`，见 `generate()`。
    """
    honor = create_honor_header(song)
    top = _about_line(song) + (honor + "\n" if honor else "") + _title_replace(song)
    return top + create_songbox(song)


def create_songbox(song: Song) -> str:
    """`{{VOCALOID_Songbox}}` 一块（封面 / 图片信息 / 颜色 / 演唱 / 歌名 / P主 / 投稿栏）。"""
    video_fields = []
    videos = sorted(song.videos, key=lambda v: v.uploaded)
    canonical = only_canonical_videos(videos)
    if any(getattr(video, "deleted", False) for video in canonical):
        # 有非公開 / 删稿的视频：整栏改用 {{VOCALOID_Songbox/card}}（参 杰西卡、赤点 赤点）
        cards = [v for v in (get_video(canonical, site) for site in
                             (VideoSite.NICO_NICO, VideoSite.BILIBILI, VideoSite.YOUTUBE)) if v]
        video_fields = ["|投稿 =\n" + "".join(f"{video_card(video)}\n" for video in cards)]
    else:
        for site, field_prefix in ((VideoSite.NICO_NICO, "nnd"),
                                   (VideoSite.BILIBILI, "bb"),
                                   (VideoSite.YOUTUBE, "yt")):
            video = get_video(videos, site)
            # 只在**这个站确实有官方投稿**时才写这两栏：没有 YouTube 投稿时写一个空的
            # `|yt_date =` 会让条目多出无意义的参数（用户 2026-10-22 报）；转载（非 P主 投稿）
            # 同理不写 —— 它只是播放器，见下面 `{{bilibiliVideo}}`。
            if not (video and video.canonical):
                continue
            video_fields.extend([f"|{field_prefix}_id = {video.identifier}\n",
                                 f"|{field_prefix}_date = {datetime_to_iso(video.uploaded)}\n"])
    illustrator = credited_illustrators(song.image.creators)
    image_info = ""
    if illustrator:
        image_info = "曲绘 by " + join_string(person_list_to_str(illustrator),
                                              mapper=auto_lj, deliminator="、")
    image_info_field = f"|图片信息 = {image_info}\n" if image_info else ""
    editing = song.color_editing
    if editing and editing.songbox:
        color_field = editing.songbox.strip() + "\n"
    elif song.colors:
        color_field = f"|颜色    = {song.colors.background.to_hex()};color:{song.colors.text.to_hex()}\n"
    else:
        color_field = "|颜色    = \n"
    return f"""{{{{VOCALOID_Songbox
|image    = {get_cover_filename(song)}
{image_info_field}{color_field}|演唱    = {join_string(song.creators.vocalists_str(), outer_wrapper=("[[", "]]"),
                      mapper=name_to_wiki, deliminator="、")}
|歌曲名称 = {"<br/>".join(get_song_names(song))}
|P主 = {"&".join([auto_lj('[[' + p.name + ']]') for p in song.creators.producers])}
{"".join(video_fields)}}}}}
"""


def other_version_honor_header(version) -> str:
    """其他版本的荣誉题头：该版本各站的播放量到殿堂 / 传说就写 `nrank` / `yrank` / `brank`。

    与主版本的 `create_honor_header()` 同一套规则：niconico / YouTube 的稿件来自 VocaDB
    （`OtherVersion.videos`），B 站那份是用户填的 —— **转载（非 P主 自己提交）不计**；
    所以一个版本可以只靠它自己在 nico 上的殿堂稿拿到 `nrank`。
    """
    videos = list(getattr(version, "videos", None) or [])
    bilibili = version.video if version.canonical else None
    rank_fields = []
    for site, rank_name in ((VideoSite.NICO_NICO, "nrank"),
                            (VideoSite.YOUTUBE, "yrank"),
                            (VideoSite.BILIBILI, "brank")):
        video = bilibili if site == VideoSite.BILIBILI else get_video(videos, site)
        if video and video.views >= 100000:
            rank_fields.append(f"{rank_name}={view_rank(video.views)}")
    if not rank_fields:
        return ""
    engines = engines_of(version.vocalists or []) or ["VOCALOID"]
    return "{{虚拟歌手歌曲荣誉题头|" + "|".join([*engines, *rank_fields]) + "}}"


# VocaDB 的 pvServices → 站内链接用的站点名（转载版本用来说明「这个版本投稿在哪个站」）
PV_SERVICE_SITES = {"NicoNicoDouga": "niconico", "Youtube": "YouTube", "Bilibili": "bilibili"}
# 其他版本在简介里的归类：Cover → 「日语翻唱歌曲」、Remix/Arrangement → 「日语改编歌曲」
OTHER_VERSION_KINDS = {"Cover": "翻唱", "Remix": "改编", "Arrangement": "改编", "Remaster": "改编",
                       "Instrumental": "改编", "MusicPV": "原创", "Original": "原创"}


def _same_producers(song: Song, version) -> bool:
    """这一版是不是**同一位 P主**重新投稿的（Live 版 / 重录），而不是别人的翻唱。"""
    main = {str(person.name or "").strip().casefold()
            for person in (getattr(song.creators, "producers", None) or [])}
    version_producers = {str(name or "").strip().casefold() for name in version.producers or []}
    main.discard("")
    version_producers.discard("")
    return bool(main & version_producers)


def other_version_kind(song: Optional[Song], version) -> str:
    """其他版本写进简介时算哪一类（主版本固定是「原创」）。

    有 VocaDB 明确的 `songType` 时按它来（Cover → 翻唱、Remix → 改编）；
    其余类型（`Mashup` 这种 VocaDB 自己的怪分类）看**P主 是否与本曲相同** ——
    同一首歌、同一位 P主 的另一次投稿（Live 版 / 重录）算「原创」，别人的才算翻唱。
    """
    kind = OTHER_VERSION_KINDS.get(version.song_type or "")
    if kind:
        return kind
    return "原创" if _same_producers(song, version) else "翻唱"


def _site_from_pv_services(services: str) -> str:
    """VocaDB 的 `pvServices`（`"NicoNicoDouga, Youtube"`）→ 第一个认得的站点名。"""
    for part in (services or "").split(","):
        name = PV_SERVICE_SITES.get(part.strip())
        if name:
            return name
    return ""


def other_version_upload_text(version) -> str:
    """其他版本简介里「于…投稿至[[站点]]」那一段，与主版本的 `videos_to_str2()` 同一套说法。

    * **官方投稿**（B 站那份就是 P主 自己投的）→ 用 B 站的投稿日，写「投稿至[[bilibili]]」；
    * **转载**（用户回答「不是 P主 自己提交的」）→ 用 VocaDB 的投稿日 + 它记的投稿站
      （`pvServices`）写，也就是「这个版本本身投稿在 nico / YouTube 上」，
      不会把转载说成是 P主 投的；
    * 日期取不到就只写站点，站点也认不出就只写「投稿」。
    """
    where = ""
    date = None
    if version.canonical and version.video is not None:
        where = "[[bilibili]]"
        date = version.video.uploaded if version.video.uploaded.year > 1970 else None
        if date is None:
            date = version.publish_date
    else:
        site = _site_from_pv_services(version.pv_services)
        where = f"[[{site}]]" if site else ""
        date = version.publish_date
    if date is not None and date.year <= 1970:      # epoch = 取不到日期
        date = None
    if date is not None:
        return f"于{datetime_to_ymd(date)}投稿至{where}" if where else f"于{datetime_to_ymd(date)}投稿"
    return f"投稿至{where}" if where else "投稿"


def create_other_version_intro(song: Song, version) -> str:
    """其他版本的简介：与 `create_intro()` 同一套句式 / 链接写法（参 voca.wiki《鸟之诗》）。

    收录专辑与活动（ボカコレ 等）这两句也照主简介写 —— 两者都来自 VocaDB 上**这个版本自己**的
    `albums` / `releaseEvents`（同名单曲已在 `vocadb.parse_albums` 里丢掉）；
    只属于主版本的人声本家那句不跟着搬过来。

    同一首歌的另一次投稿（Live 版 / 重录，见 `other_version_kind()`）另有两点不同，
    照 voca.wiki《13943号室》Live 版（diff 275394）：
      * 归成「日语**原创**歌曲」，不是「翻唱」；
      * 专辑那句的主语写成「此版本为2018 Live版，收录于专辑《…》」。
    """
    kind = other_version_kind(song, version)
    engines = engines_of(version.vocalists or []) or ["VOCALOID"]
    albums = list(getattr(version, "albums", None) or [])
    # 其他版本的活动也是**按版本**检测出来的（VocaDB 的 releaseEvents），写法与主简介一致
    collection = collection_sentence(getattr(version, "vocaloid_collection", ""),
                                     getattr(version, "vocaloid_collection_track", None),
                                     getattr(version, "vocaloid_collection_rank", None),
                                     getattr(version, "vocaloid_collection_places", None),
                                     "，" if albums else "。")
    # 没有活动那句时，专辑这句自己带主语（「本曲收录于专辑《…》。」）；
    # 同一首歌的另一次投稿用「此版本为2018 Live版，收录于专辑《…》。」
    lead = other_version_lead(version) if (kind == "原创" and not collection) else ""
    albums_text = albums_sentence(albums, subject=not collection and not lead, lead=lead)
    if collection:
        tail = f"\n\n{collection}{albums_text}"
    else:
        tail = f"\n\n{albums_text}" if albums_text else ""
    return intro_sentence(song, version.producers, version.vocalists,
                          other_version_upload_text(version), engines, kind) + tail + "\n"


def create_other_version_songbox(song: Song, version) -> str:
    """其他版本的 `{{VOCALOID_Songbox}}`：封面 / 演唱 / 歌曲名称 / P主 / 该版本在各站的投稿。

    niconico / YouTube 的稿件 ID 来自 VocaDB（`OtherVersion.videos`，见
    `vocadb.get_version_details`），B 站那份是用户填的（`OtherVersion.video`）；
    栏位顺序与主版本的 Songbox 一致（image → 图片信息 → 颜色 → 演唱 … nnd → bb → yt），
    日期取不到时退回 VocaDB 的 `publishDate`。

    `|image` 与配色都是那一版自己的（VocaDB 上拿不到）：文件名由
    `get_other_version_cover_name()` 按条目名 + 版本短名生成，`|颜色`（三栏不一样时是
    `|颜色1/2/3`）来自用户在「样式」页
    选到这一版后改的样式（`OtherVersion.songbox_colors`）。封面文件本身由样式页「导入图片」写进输出目录，
    提交时随条目一起上传（见 `build_version_covers()`）。
    """
    fields = [
        f"|image    = {get_other_version_cover_name(song, version)}\n",
    ]
    image_info = getattr(version, "image_info", "")
    if image_info:
        fields.append(f"|图片信息 = {image_info}\n")
    colors = (getattr(version, "songbox_colors", "") or "").strip()
    if colors:
        fields.append(colors + "\n")
    fields.extend([
        f"|演唱    = {join_string(version.vocalists, outer_wrapper=('[[', ']]'), mapper=name_to_wiki, deliminator='、')}\n",
        f"|歌曲名称 = {'<br/>'.join(get_song_names(song))}\n",
        f"|P主 = {join_string(version.producers, outer_wrapper=('[[', ']]'), mapper=auto_lj, deliminator='&')}\n",
    ])
    for site, field_prefix in ((VideoSite.NICO_NICO, "nnd"),
                               (VideoSite.BILIBILI, "bb"),
                               (VideoSite.YOUTUBE, "yt")):
        if site == VideoSite.BILIBILI:
            video = version.video
            # 与主版本 Songbox 同一套规矩：**转载**（不是 P主 自己投的）只当播放器用，
            # 不写 `|bb_id` / `|bb_date`（用户 2026-10-23 在《13943号室》Live 版里删掉了这两行）
            if video is not None and not getattr(version, "canonical", True):
                continue
        else:
            video = get_video(getattr(version, "videos", None) or [], site)
        if video is None:
            continue
        date = video.uploaded if video.uploaded.year > 1970 else version.publish_date
        fields.append(f"|{field_prefix}_id = {video.identifier}\n")
        if date is not None and date.year > 1970:
            fields.append(f"|{field_prefix}_date = {datetime_to_iso(date)}\n")
    return "{{VOCALOID_Songbox\n" + "".join(fields) + "}}"


def _tab_body(header: str, box: str, intro: str) -> str:
    """一个 tab 的内容：荣誉题头与 Songbox 贴着写，简介另起一段（参 voca.wiki《鸟之诗》）。"""
    top = "\n".join(part.strip() for part in (header, box) if part and part.strip())
    intro = (intro or "").strip()
    return f"{top}\n\n{intro}\n" if intro else f"{top}\n"


def create_tabs(song: Song, intro: str) -> str:
    """多版本条目：每个版本一个 tab（荣誉题头 + Songbox + 简介），参 voca.wiki《鸟之诗》。

    tab 按钮上只写短名：主版本是「原版」，其他版本平时用版本名、重名时只用补充说明
    （`ROCK_VER` → `ROCK版`，见 `other_versions._short_label`）；
    `== 歌曲 ==` 里的 `;版本名` 仍用完整版本名。
    歌词 / 注释 / 分类各版本共用，都留在 `{{tabs}}` 外面。
    """
    versions = list(getattr(song, "other_versions", None) or [])
    # 没有其他版本时不会走到这里（`generate()` 走单版本那条路），真被单独调用时用完整版本名
    main_tab = other_versions.MAIN_TAB_LABEL if versions else other_versions.main_version_label(song)
    tabs = ["{{tabs", "|color=transparent",
            f"|bt1={main_tab}", "|tab1=",
            _tab_body(create_honor_header(song), create_songbox(song), intro)]
    for index, version in enumerate(versions, start=2):
        tabs.append(f"|bt{index}={version.tab_label or version.label}")
        tabs.append(f"|tab{index}=")
        tabs.append(_tab_body(other_version_honor_header(version),
                              create_other_version_songbox(song, version),
                              create_other_version_intro(song, version)))
    tabs.append("}}")
    return "\n".join(tabs)


def videos_to_str2(videos: List[Video]):
    videos = only_canonical_videos(videos)
    dates = dict()
    for v in videos:
        original = dates.get(v.uploaded, [])
        original.append(v.site.value)
        dates[v.uploaded] = original
    lst = sorted(dates.keys())
    parts = []
    last_year = None
    same_year_written = False
    for index, date in enumerate(lst):
        if index == 0:
            date_part = f"于{datetime_to_ymd(date)}"
        elif date.year != last_year:
            date_part = f"{datetime_to_ymd(date)}"
            same_year_written = False
        elif not same_year_written:
            date_part = f"同年{date.month}月{date.day}日"
            same_year_written = True
        else:
            date_part = f"{date.month}月{date.day}日"
        last_year = date.year
        parts.append(f"{date_part}投稿至{join_string(dates[date], outer_wrapper=('[[', ']]'))}")
    return join_string(parts, deliminator="，")


def intro_sentence(song: Song, producers: Sequence[str], vocalists: Sequence[str],
                   upload_text: str, engines: Sequence[str], kind: str = "原创") -> str:
    """`《'''歌名'''》（译名）是由P主于…投稿至[[站点]]的[[引擎]]日语XX歌曲，由[[歌姬]]演唱。`

    主版本的 `create_intro()` 与其他版本的 `create_other_version_intro()` 共用这一套，
    所以两边简介的句式、链接写法、日期写法完全一致（参 voca.wiki《鸟之诗》各 tab）。
    """
    nc = song.name_chs
    nj = song.name_jap
    return ("《'''" + auto_lj(nj) + "'''》" +
            f"{'' if nc == nj else f'（{nc}）'}"
            f"是由{join_string(list(producers)[:1], inner_wrapper=('[[', ']]'), mapper=auto_lj)}"
            f"{upload_text}的{join_engines(list(engines))}日语{kind}歌曲，"
            f"由{join_string(vocalists, outer_wrapper=('[[', ']]'), mapper=name_to_wiki)}演唱。")


# 赛道在简介里的写法：TOP100 不带「榜」，其他榜名带（Remix 的大小写按实测条目写）
COLLECTION_RANK_NAMES = {"ROOKIE": "ROOKIE榜", "REMIX": "Remix榜"}


def collection_rank_text(track: str, rank, bold: bool = True) -> str:
    """`TOP100中的第'''70'''名` / `TOP30榜第30名` / `ROOKIE榜中的第42名`。

    实测条目写法：向日葵(Project Lumina)「获得TOP100中的第'''35'''名」、
    Doomer「获得ROOKIE榜中的第'''3'''名」、Relay Outer/Iyowa「Remix榜中的第'''1'''名」。
    """
    name = COLLECTION_RANK_NAMES.get(track, str(track))
    value = f"'''{rank}'''" if bold else str(rank)
    if track == "TOP30":
        return f"TOP30榜第{value}名"
    return f"{name}中的第{value}名"


def collection_sentence(collection: str, track: Optional[str] = None,
                        rank: Optional[str] = None,
                        places: Optional[Sequence[tuple]] = None,
                        punctuation: str = "。") -> str:
    """「本曲参与了[[The VOCALOID Collection]]({{lj|ボカコレ2024冬}})活动[并获得TOP100中的第'''3'''名]」。

    `places` 是爬活动模板读出来的 [(赛道, 名次), …]，**同一首歌可能多榜都在**：
    那时写「获得TOP100中的第'''70'''名、ROOKIE榜中的第'''42'''名」（参 咬人兔 /
    Gimme Money；每个名次都加粗）。REMIX 也是一个赛道
    （参 Relay Outer/Iyowa：「Remix榜中的第'''1'''名」）。
    没给 places 时退回旧的 (track, rank) 写法：只写一榜，
    只有名次、没有赛道时按 TOP100 算；有赛道时沿用识别到的赛道（atwiki 兑底路子）。
    版外（`榜外`）或没名次时不写名次，`punctuation` 看后面还接不接得上「收录于专辑…」。
    """
    if not collection:
        return ""
    # places 为空（模板里两榜都没有 / 人工询问那条路）时退回 (track, rank) 这一对
    items = [(place_track, place_rank)
             for place_track, place_rank in (places or [(track, rank)])
             if place_track and place_track != family_template.UNRANKED_TRACK
             and place_rank is not None]
    if items:
        rank_text = "并获得" + "、".join(
            collection_rank_text(place_track, place_rank)
            for place_track, place_rank in items)
    elif track == family_template.UNRANKED_TRACK:
        rank_text = ""
    else:
        fallback_track = track or "TOP100"
        rank_text = ("并获得" + collection_rank_text(fallback_track, rank)
                     if rank else "")
    return (f"本曲参与了[[The VOCALOID Collection]]({{{{lj|{collection}}}}})活动"
            f"{rank_text}{punctuation}")


def albums_sentence(albums: Sequence[str], subject: bool = False, lead: str = "") -> str:
    """「(本曲)收录于专辑《'''…'''》和《'''…'''》。」（没有专辑就是空串）。

    主简介与其它版本简介共用；同名单曲（专辑名 = 歌曲原名且只收录本曲）在
    `vocadb.parse_albums` 那一步就已经丢掉了。
    `subject=True` 时前面补上「本曲」——这句话单独成段（没有前面那句「本曲参与了…活动」）时
    要自己带主语才读得通（用户 2026-09 要求）。
    `lead` 换成别的主语（其他版本的「此版本为2018 Live版，」，见
    `other_version_lead()`；参 voca.wiki《13943号室》Live 版）。
    """
    if not albums:
        return ""
    prefix = lead or ("本曲" if subject else "")
    return (prefix + "收录于专辑" +
            join_string(albums, mapper=auto_lj, outer_wrapper=("《'''", "'''》")) + "。")


def other_version_lead(version) -> str:
    """其他版本简介里「此版本为2018 Live版，…」那一截（同一首歌的另一次投稿才写）。

    参 voca.wiki《13943号室》Live 版：「此版本为2018 Live版，收录于专辑《'''…'''》。」
    —— 年份是这个版本自己的投稿年，版本名就是 tab 上那个短名（`Live版`）。
    """
    label = str(getattr(version, "tab_label", "") or getattr(version, "label", "") or "").strip()
    if not label:
        return ""
    publish = getattr(version, "publish_date", None)
    year = getattr(publish, "year", 0) if publish is not None else 0
    return f"此版本为{year} {label}，" if year and year > 1970 else f"此版本为{label}，"


def create_intro(song: Song):
    # 本曲没参加活动时，专辑那句自己带主语（有活动时主语在「本曲参与了…」上）
    punctuation = "，" if song.albums else "。"
    collection = collection_sentence(song.vocaloid_collection, song.vocaloid_collection_track,
                                     song.vocaloid_collection_rank,
                                     getattr(song, "vocaloid_collection_places", None), punctuation)
    albums = albums_sentence(song.albums, subject=not collection)
    tail = f"\n\n{collection}{albums}" if collection else albums
    # 人声本家：单独一段，排在活动 / 专辑那段**之后**（参 voca.wiki《小小星座》：
    # 简介的顺序是「…演唱。」→「本曲参与了…活动，收录于专辑…。」→「另有…人声本家。」，
    # 用户 2026-09-28 对照 diff 251205 指出这一句要在活动句下面）
    human = human_original_sentence(song)
    human_tail = f"\n\n{human}" if human else ""
    # 稿件全被排除（例如只剩 YouTube 自动代传）时没有「投稿至…」这一段，
    # 用「创作」把句子接顺，别写成「是由P主的…歌曲」
    upload_text = videos_to_str2(song.videos) or "创作"
    return (intro_sentence(song, song.creators.producers_str(), song.creators.vocalists_str(),
                           upload_text, get_song_categories(song)) +
            tail +
            human_tail + "\n")


def create_song(song: Song):
    video_player = ""
    v = get_video(song.videos, VideoSite.BILIBILI)
    if v:
        video_player = f"{{{{" \
                       f"bilibiliVideo|id={v.identifier}" \
                       f"}}}}"
    # 有人声本家 / 其他版本时，按版本分块并加标签（参 红色房间 / 如月车站、《鸟之诗》）：
    #   ;VOCALOID本家
    #   {{BilibiliVideo|id=…}}
    #
    #   ;人声本家
    #   {{BilibiliVideo|id=…}}        ← 有 B 站稿件就只写这一行（见 human_original_links）
    #
    #   ;其他版本名
    #   {{BilibiliVideo|id=…}}
    other = list(getattr(song, "other_versions", None) or [])
    human = human_original_links(song)
    blocks = []
    if video_player:
        # 多版本条目里主版本也要有自己的 `;版本名`（参《鸟之诗》的「;でんげん初音未来版」）
        if other:
            video_player = f";{other_versions.main_version_label(song)}\n{video_player}"
        elif human:
            video_player = f";{get_song_categories(song)[0]}本家\n{video_player}"
    if human:
        blocks.append(";人声本家\n" + "\n".join(human))
    for version in other:
        if version.video is not None:
            blocks.append(f";{version.label}\n{video_embed(version.video)}")
    if other or human:
        if video_player:
            blocks.insert(0, video_player)
        video_player = "\n\n".join(blocks)
    groups: List[Staff] = merge_staff_rows(sorted(song.creators.staff_list(),
                                                 key=lambda staff: role_priority(staff[0])))
    # 「曲绘」只署个人：`スタジオ猿芝居` 这类**团队 / 工作室**（VocaDB 的 artistType 是
    # OtherGroup）不是画那张图的人（用户 2026-10-30 对照《为犬之人》diff 276889 要求）。
    # 全是团队时原样保留，别把这一栏弄空。
    groups = [(role, credited_illustrators(people) if role == "曲绘" else people)
              for role, people in groups]
    if {role for role, _ in groups} == {"词曲", "演唱"}:
        # 词曲 / 演唱在 Songbox 里已经写过，不再重复一张「VOCALOID Songbox Introduction」表；
        # 但这一节只要还有东西（B 站稿件 / 人声本家 / 其他版本），**小节标题必须留下** ——
        # 旧实现直接 `return video_player`，结果是播放器光秃秃地贴在「== 歌词 ==」上面
        # （用户 2026-09 报「生成歌曲 君が僕を嗤う日 时『== 歌曲 ==』不见了」；
        #   真实条目《你嘲笑我那天》也是「== 歌曲 ==」+ 播放器、没有表）
        return f"== 歌曲 ==\n\n{video_player}" if video_player else ""
    groups: List[str] = [f"|group{index + 1} = {g[0]}\n"
                         f"|list{index + 1} = {join_string(person_list_to_str(g[1]), deliminator='<br/>', mapper=auto_lj)}\n"
                         for index, g in enumerate(groups)]
    editing = song.color_editing
    default_bg = song.colors.background.to_hex() if song.colors else "#000"
    default_fg = song.colors.text.to_hex() if song.colors else "white"
    # 编辑器里改过就用编辑器写好的值（可能含多条 CSS 声明），否则用默认色。
    # 2026-09 删掉「配色优化」开关（`wikitext.optimize_Introduction_color`）：它会给
    # lbgcolor / ltcolor 各追加一串写死的声明，编辑器的设置得反过来把它们清掉才不打架。
    lbgcolor = editing.introduction_bg if editing and editing.introduction_bg else default_bg
    ltcolor = editing.introduction_fg if editing and editing.introduction_fg else default_fg
    color = f"|lbgcolor = {lbgcolor}\n|ltcolor = {ltcolor}\n"
    # 标签格带额外声明时，模板里的 border: <lbgcolor> 1px solid 会被写坏，
    # 由编辑器额外给出列表格边框色（与 lbgcolor 同色）
    if editing and editing.introduction_border:
        color += f"|rbdcolor = {editing.introduction_border}\n"
    return (f"== 歌曲 ==\n"
            "{{VOCALOID Songbox Introduction\n"
            + color +
            f"{''.join(groups)}"
            f"}}}}\n\n{video_player}")


def _name_key(text: str) -> str:
    """人名比较用的键：去掉空白与常见分隔符、统一大小写（`MINO-U` 与 `mino-u` 视为同一人）。"""
    return re.sub(r"[\s\u3000・·.,、，\-_/／]+", "", str(text or "")).casefold()


def is_producer_translation(lyrics: Lyrics, creators) -> bool:
    """翻译者是不是 **P 主本人**（名字 / 英文名与 VocaDB 里的 P 主对得上）。

    站内这种条目不署「翻译：某某」—— 名字已经写在 Songbox 的「P主」里了，
    改成注明译文来自本家（见 `create_lyrics()`，样例《Short Hand》）。
    """
    key = _name_key(getattr(lyrics, "translator", "") or "")
    if not key:
        return False
    for person in getattr(creators, "producers", None) or []:
        names = [getattr(person, "name", ""), *(getattr(person, "name_eng", None) or [])]
        if any(_name_key(name) == key for name in names if str(name or "").strip()):
            return True
    return False


def producer_translation_notice(source_name: str) -> str:
    """P 主本人译的：译文来自他**自己的**视频 / 投稿评论区（站内写法，见《Short Hand》）。

    评论区以外的来源（网易云之类）拿不到「本家」这种说法，返回空串 —— 调用方退回
    「*翻译转载自来源」那一套（仍然不署「翻译：P 主名」）。
    """
    if "评论" in str(source_name or ""):
        return "*翻译转载自本家视频评论区"
    return ""


def create_lyrics(song: Song):
    lyrics = song.lyrics
    editing = song.color_editing
    # 悬停显示译文（{{LyricsKai/hover}}）：歌词整理窗口与颜色编辑器「歌词」面板的开关任一开启即生效
    use_hover = bool(lyrics.use_hover or (editing and editing.lyrics_hover))
    # 歌词里带着 `#NoHover`（从条目复制过来的 `LyricsKai/hover` 歌词就是这样）时先还原成空行：
    # 它是**模板标记**、由 add_no_hover() 在输出时按需要补 —— 不还原的话非 hover 生成会把
    # `#NoHover` 原样写进条目正文（见 utils.lyrics_editor.normalize_no_hover）。
    lyrics_jap = normalize_no_hover(lyrics.lyrics_jap)
    lyrics_chs = None if lyrics.lyrics_chs is None else normalize_no_hover(lyrics.lyrics_chs)
    lyrics_roma = normalize_no_hover(lyrics.lyrics_roma)
    chs_exist = lyrics_chs is not None
    if use_hover:
        # hover 模式下译文与原文排在同一行，空行要补 #NoHover 才不会出现悬停区
        lyrics_jap = add_no_hover(lyrics_jap)
        if chs_exist:
            lyrics_chs = add_no_hover(lyrics_chs)
    # 演唱者上色（{{LyricsKai/colors}}）：歌词整理窗口的开关，按「每行标了谁」生成 charas / colors 与 @n 标记
    use_colors = bool(lyrics.use_colors)
    colors_params = ""
    if use_colors:
        plan, colors_params = build_colors_params(song.creators.vocalists_str(), lyrics.chara_marks,
                                                  splits=lyrics.chara_splits,
                                                  chs_marks=lyrics.chara_marks_chs)
        if plan.available:
            # 行内分段：两栏各按自己的切分点插标记（中文栏没切分过时整行用第一段的颜色）
            lyrics_jap = mark_lines(lyrics_jap, plan, "jap")
            if chs_exist:
                lyrics_chs = mark_lines(lyrics_chs, plan, "chs")
        else:                                  # 没有歌姬信息就不加 /colors，避免生成空参数
            use_colors, colors_params = False, ""
    if chs_exist:
        translator = assert_str_exists(lyrics.translator)
        source_name = assert_str_exists(lyrics.source_name)
        source_url = assert_str_exists(lyrics.source_url)
        # 翻译者就是 P 主本人时不署「翻译：名字」：名字在 Songbox 的「P主」里已经有了，
        # 站上改成注明译文来自本家（样例《Short Hand》：「*翻译转载自本家视频评论区」）
        by_producer = bool(translator) and is_producer_translation(lyrics, song.creators)
        producer_notice = producer_translation_notice(source_name) if by_producer else ""
        if by_producer:
            translator = ""
        if translator:
            if not is_empty(lyrics.translator_url):
                translator = f"[{lyrics.translator_url} {translator}]"
            translation_notice = f"*翻译：{translator}"
            if source_name and source_url:
                source = f"[{source_url} {source_name}]"
                translation_notice += f"<ref>翻译转载自{source}</ref>"
            elif source_name:
                translation_notice += f"<ref>翻译转载自{source_name}</ref>"
            elif source_url:
                translation_notice += f"<ref>翻译转载自[{source_url}]</ref>"
        elif producer_notice:
            translation_notice = producer_notice
        elif source_name and source_url:
            translation_notice = f"*翻译转载自[{source_url} {source_name}]"
        elif source_name:
            translation_notice = f"*翻译转载自{source_name}"
        elif source_url:
            translation_notice = f"*翻译转载自[{source_url}]"
        else:
            translation_notice = ""
    else:
        translation_notice = ""
    # 罗马音（{{LyricsKai/Roma}} + |photrans=）：只有歌词页勾了「使用 LyricsKai/Roma」才输出
    # —— 罗马音栏默认藏起来，没勾就不该因为栏里还留着字而生成模板（用户 2026-10-16 要求）；
    # hover 与 /Roma 互斥（站内没有这种组合）
    has_roma = bool(lyrics.use_roma) and not use_hover and not is_empty(lyrics.lyrics_roma)
    # 模板名顺序：LyricsKai + /colors + /hover + /Roma（四种组合在维基上都存在）
    lyrics_template = (("/colors" if use_colors else "") +
                       ("/hover" if use_hover else ""))
    # 只输出已设置的样式；未设置（含编辑器里关掉了「输出」开关）时整行省略
    # 三项的值都是编辑器写好的 CSS 声明文本，模板会用 cssText 解析
    style_params = []
    if editing is not None:
        if editing.lyrics_original:
            style_params.append(f"|lstyle={editing.lyrics_original}")
        if editing.lyrics_translated:
            style_params.append(f"|rstyle={editing.lyrics_translated}")
        if editing.lyrics_background:
            style_params.append(f"|containerstyle={editing.lyrics_background}")
    style_block = "".join(f"{param}\n" for param in style_params)
    # 歌词里的 `~~~` 包成 `<nowiki>`：三连半角波浪号在 wikitext 里是签名（见 protect_tildes）
    lyrics_jap = protect_tildes(assert_str_exists(lyrics_jap).strip())
    lyrics_chs = protect_tildes(lyrics_chs.strip()) if chs_exist else ""
    lyrics_roma = protect_tildes(lyrics_roma) if has_roma else ""
    return f"""== 歌词 ==
{translation_notice}
{"{{LyricsKai/Roma/button}}" if has_roma else ""}
{{{{LyricsKai{lyrics_template}{'/Roma' if has_roma else ''}
{colors_params}{style_block}|original=
{lyrics_jap}
|translated=
{lyrics_chs}
{("|photrans=" + lyrics_roma) if has_roma else ''}}}}}
"""


# 「== 注释 ==」里用到的歌手大家族模板（来源：voca.wiki 的 Category:虚拟歌手模板，2026-09 实测）。
# 键是歌手名（vocadb 返回的 Default 名，可能是日文 / 中文 / 英文），值是模板名；
# 值里带 {year} 的模板按投稿年份分页（如 可不/2024），取不到年份时退化成不带年份的写法。
# 不收进来的：初音未来 / 初音未来(中文)（按需手写，分类由 vocalist_cat 补）
# 与 东北俊子·俊达萌项目（项目导航框，不随条目输出）。
VOCALOID_TEMPLATES: Dict[str, str] = {
    # —— 按投稿年份分页 ——
    '可不': '可不/{year}',
    'KAFU': '可不/{year}',
    '重音Teto': '重音Teto/{year}',
    '重音テト': '重音Teto/{year}',
    'v flower': 'Flower/{year}',
    'Ci flower': 'Flower/{year}',
    'flower': 'Flower/{year}',
    'Flower': 'Flower/{year}',
    'KAITO': 'KAITO/{year}',
    '歌爱雪': '歌爱雪/{year}',
    'IA': 'IA/{year}',
    # 结月缘 也是按年份拆开的（`Template:结月缘/2011` … `/2026`，实测 16 个子页）；
    # `Template:结月缘` 只是把它们串起来的外层 navbox（`{{#invoke:Nav|box}}` 里逐个
    # `{{结月缘/<年>|nocate=1|state=uncollapsed|child|noabove}}`），往曲子条目里写它会把
    # 整个导航框塞进去，而不是这一年那一格。用户 2026-10-30 对照《轰鸣声、宛如万里晴空。》
    # 与 `Template:结月缘/2022` 的 diff 277051 报的。
    '结月缘': '结月缘/{year}',
    # —— 单页模板（键与模板名相同）——
    'D-Lin': 'D-Lin',
    'Kevin': 'Kevin',
    'Mai': 'Mai',
    'Ninezero': 'Ninezero',
    'NurseRobot_TypeT': 'NurseRobot TypeT',
    'Ritchy': 'Ritchy',
    'SOLARIA': 'SOLARIA',
    'SeeU': 'SeeU',
    'Weina': 'Weina',
    'Yuma': 'Yuma',
    '爱莲娜·芙缇': '爱莲娜·芙缇',
    '岸晓': '岸晓',
    '东方栀子': '东方栀子',
    '沨漪': '沨漪',
    '狐狸座': '狐狸座',
    '狐子': '狐子',
    '俊达萌': '俊达萌',
    '里命': '里命',
    '林籁': '林籁',
    '铃音环': '铃音环',
    '洛天依': '洛天依',
    '绮萱': '绮萱',
    '琴叶茜': '琴叶茜',
    '琴叶葵': '琴叶葵',
    '琴叶茜·葵': '琴叶茜·葵',
    '诗岸': '诗岸',
    '双叶凑音': '双叶凑音',
    '未抒': '未抒',
    '夏语遥': '夏语遥',
    '小春六花': '小春六花',
    '心华': '心华',
    '星尘': '星尘',
    '星界': '星界',
    '言和': '言和',
    '奕夕': '奕夕',
    '羽累': '羽累',
    '雨衣': '雨衣',
    '韵泉': '韵泉',
    '佐藤莎莎拉': '佐藤莎莎拉',
    '猫村伊吕波': '猫村伊吕波',
    # —— 歌手名与模板名不一致 ——
    'Ryo': 'Ryo(SynthV)',                       # vocadb 里 SynthV 的 Ryo 就叫 Ryo
    '狐狸座Vul': '狐狸座',
    '鸣花姬': '鸣花姬·尊',                        # 姬 / 尊 共用一个模板
    '鸣花尊': '鸣花姬·尊',
    # 夢ノ結唱（BanG Dream!）的声库共用一个模板
    'POPY': '梦的结唱',
    'ROSE': '梦的结唱',
    'PASTEL': '梦的结唱',
    'HALO': '梦的结唱',
    'AVER': '梦的结唱',
}

# 「第一档」的兜底：年份 ≤ 第一档年份时用这一档，别去造一个不存在的子页（`可不/2015` 会变红链）。
# 值 = (第一档的年份, 第一档的写法)。
#   * 早期作品全塞进一档的：实测（2026-10-27）`Template:可不/2021` 不存在，只有
#     `Template:可不/2021及以前`；站上 2021 年的 phony / Kyu-kurarin / Elite / binary_biotope
#     写的都是 `{{可不/2021及以前|collapsed}}` —— 用户对照 diff 275683 → 275686 报的就是这个。
#     整站只有 可不 与 初音未来(中文) 两处这么写，后者不收进 VOCALOID_TEMPLATES。
#   * 第一档就是普通年份的：表格里的年份是**实测的最早子页**（`list=allpages&apprefix=<模板>/`），
#     比它更早的投稿照样落到第一页 —— 实测 `Template:结月缘/2011` … `/2026`（16 页）、
#     `Template:KAITO/2007` …、`Template:重音Teto/2008` …、`Template:歌爱雪/2009` …、
#     `Template:IA/2011` …、`Template:Flower/2014` …。
VOCALOID_EARLY_YEARS: Dict[str, Tuple[int, str]] = {
    '可不': (2021, '2021及以前'),
    'KAFU': (2021, '2021及以前'),
    '结月缘': (2011, '2011'),
    '重音Teto': (2008, '2008'),
    'Flower': (2014, '2014'),
    'KAITO': (2007, '2007'),
    '歌爱雪': (2009, '2009'),
    'IA': (2011, '2011'),
}

# 名字里含关键词就套用（沿用原来的兜底写法，还能容忍 vocadb 名里的后缀）
vocaloid_template_mapper = {
    '鸣花': '鸣花姬·尊',
    'NurseRobot': 'NurseRobot TypeT',
}

# 实测这些模板不会自己加「XX歌曲」分类 → 分类仍由 vocalist_cat 手写
TEMPLATES_WITHOUT_CATEGORY = {
    '梦的结唱',                                  # 纯导航框，没有 {{ac}}
}

# 要把「歌姬名」当 {{{1}}} 传进去、模板才会自己补「XX歌曲」分类的歌手模板。
# 值 = (全都出现时的写法, 各歌姬自己的写法…)。
# ⚠️ 实测 Template:鸣花姬·尊（用户 2026-10-14 对照 diff 261395 / 261583）：
#     `{{鸣花姬·尊}}`（不传参）→ 一个分类都不加（旧实现因此手写 `[[分类:鸣花姬歌曲]]`
#     `[[分类:鸣花尊歌曲]]`）；`{{鸣花姬·尊|鸣花姬·尊}}` → `{{ac|鸣花姬歌曲}}{{ac|鸣花尊歌曲}}`。
#     站上给的是「传参 + 不手写分类」，所以 鸣花姬·尊 已从 TEMPLATES_WITHOUT_CATEGORY 移出。
TEMPLATES_WITH_NAME_PARAM: Dict[str, Tuple[str, ...]] = {
    '鸣花姬·尊': ('鸣花姬·尊', '鸣花姬', '鸣花尊'),
}

# vocadb 里有些声库名带「(Unknown)」后缀（如「小春六花 (Unknown)」），比对前先去掉
UNKNOWN_SUFFIX_RE = re.compile(r"\s*\(Unknown\)$")


def vocalist_name(vocalist: str) -> str:
    """vocadb 的歌姬名 → 工具里的写法（查表后去掉 `(Unknown)` 后缀，见 `get_vocaloid_template`）。"""
    return UNKNOWN_SUFFIX_RE.sub("", vocaloid_names.get(str(vocalist), str(vocalist)))


def get_vocaloid_template(vocalist: str, year: int = None) -> Optional[str]:
    """单个歌姬对应的「== 注释 ==」模板名；没有专属模板时返回 None。"""
    name = vocalist_name(vocalist)
    template = VOCALOID_TEMPLATES.get(name)
    if template is None:
        for key, value in vocaloid_template_mapper.items():
            if key in name:
                template = value
                break
    if template is None:
        return None
    if "{year}" in template:
        if year is None:
            return template.split("/")[0]
        base = template.split("/")[0]
        early = VOCALOID_EARLY_YEARS.get(base)
        if early and year <= early[0]:
            return f"{base}/{early[1]}"
        return template.format(year=year)
    return template


def get_vocaloid_templates(vocaloids: List[str], year: int = None) -> List[str]:
    """多个歌姬的模板（保持出现顺序并去重）。"""
    result: List[str] = []
    for vocalist in vocaloids:
        template = get_vocaloid_template(vocalist, year)
        if template and template not in result:
            result.append(template)
    return result


def get_vocaloid_templates_for_years(vocaloids: List[str], years: Sequence[int]) -> List[str]:
    """歌姬 × **每个投稿年份** 的模板（保持顺序并去重）。

    一首歌可能 2025 年在 niconico 投稿、2026 年才传上 YouTube / B 站：站上按年份拆开的
    歌姬模板（`Template:重音Teto/2025` 与 `Template:重音Teto/2026`）**两年都要收录它**，
    所以这里对每个年份各取一次模板；不分年份的模板（`Template:歌爱雪`）仍然只出现一次。

    取不到年份时退回不带年份的写法（与 `get_vocaloid_template(vocalist, None)` 一致）。
    """
    result: List[str] = []
    for vocalist in vocaloids:
        for year in (list(years) or [None]):
            template = get_vocaloid_template(vocalist, year)
            if template and template not in result:
                result.append(template)
    return result


def needs_manual_vocalist_category(vocalist: str) -> bool:
    """该歌姬是否还要手写 [[分类:XX歌曲]]：没有专属模板，或模板不带分类。"""
    template = get_vocaloid_template(vocalist)
    return template is None or template in TEMPLATES_WITHOUT_CATEGORY


def vocalist_template_call(template: str, vocalists: Sequence[str]) -> str:
    """歌姬模板在注释区里的调用写法（`模板名` 或 `模板名|歌姬名`）。

    多数模板直接写 `{{模板名}}`；少数要把 `{{{1}}}` 当成「给谁加分类」的开关
    （见 `TEMPLATES_WITH_NAME_PARAM`）—— 本曲的歌姬全都出现时写「全都出现」那个值，
    否则写对应的那一位。歌姬名要先过 `vocalist_name`：vocadb 给的是日文名（鳴花ヒメ），
    而模板参数用的是中文名（鸣花姬），不比一下就永远只能写「全都出现」那个值。
    """
    values = TEMPLATES_WITH_NAME_PARAM.get(template)
    if not values:
        return template
    names = {vocalist_name(vocalist) for vocalist in vocalists}
    if all(value in names for value in values[1:]):
        return f"{template}|{values[0]}"
    return f"{template}|{next((value for value in values[1:] if value in names), values[0])}"


def other_version_videos(song: Song) -> List[Video]:
    """各「其他版本」自己的**投稿**（`Version.videos` 的 nico / YouTube + 官方投的 B 站）。

    站上按年份拆开的歌姬模板要把这些年份也算进去：实测《13943号室》Live 版是 2018 年投稿，
    条目末尾因此同时有 `{{KAITO/2012}}`（原版）与 `{{KAITO/2018}}`（Live 版）
    —— 用户 2026-10-24 给的 diff 275394。
    转载的 B 站稿件（`canonical=False`）不算投稿，与主版本的 `only_canonical_videos()` 一致。
    """
    videos: List[Video] = []
    for version in (getattr(song, "other_versions", None) or []):
        for video in (getattr(version, "videos", None) or []):
            if video is not None and getattr(video, "uploaded", None) is not None:
                videos.append(video)
        video = getattr(version, "video", None)
        if video is not None and getattr(version, "canonical", True):
            videos.append(video)
    return [video for video in videos
            if video.uploaded and getattr(video.uploaded, "year", 0) >= 2000]


def get_song_upload_years(song: Song) -> List[int]:
    """本曲在各个站点的**原投稿**年份（去重、从早到晚）。

    一首歌可能 2025 年在 niconico 投稿、2026 年才传上 YouTube / B 站：站上按年份拆开的
    歌姬模板两年都要收录它（用户 2026-10-19 反馈「只有刚发的年份有加上」——旧实现只取
    最早那一年）。只算 `only_canonical_videos()`：搬运件 / YouTube 自动生成的稿件不算投稿，
    否则会给搬运年份也建一份。**其他版本自己的投稿年也算**（见 `other_version_videos()`）：
    多版本条目里 Live 版 2018 年投稿，`{{KAITO/2018}}` 同样要出现在注释区。

    抓取失败的时间会是 epoch（1970），这种年份直接丢掉，别把歌写进 1970 年的模板。
    """
    videos = [*only_canonical_videos(song.videos), *other_version_videos(song)]
    years = {video.uploaded.year for video in videos
             if video.uploaded and getattr(video.uploaded, "year", 0) >= 2000}
    return sorted(years)


def get_song_upload_year(song: Song):
    """最早的原投稿年份（P主模板、`|year` 那一类「投稿年」用它）。"""
    years = get_song_upload_years(song)
    return years[0] if years else None


def collection_template_name(collection: str) -> Optional[str]:
    """活动名（ボカコレ2024冬）→ 注释区的模板名；实现见 `utils.family_template`（那边读模板时也要用）。"""
    return family_template.collection_template_name(collection)


def _collection_sync(collection: str, track: Optional[str],
                     rank: Optional[str],
                     places: Optional[Sequence[tuple]] = None) -> Optional[CollectionSync]:
    """一个活动 → `CollectionSync`（各赛道 + 名次；榜外 / 没名次时只写模板）。"""
    template = collection_template_name(collection)
    if not template:
        return None
    if isinstance(rank, str):
        rank = int(rank) if rank.isdigit() else None
    if places:
        # 两榜都在（榜外也在内）：逐赛道写回，`track` / `rank` 仍是主赛道
        return CollectionSync(template=template, track=track, rank=rank, places=list(places))
    if track == family_template.UNRANKED_TRACK:
        return CollectionSync(template=template)
    if not track:
        # vocadb 只给名次时按 TOP100 处理（与简介里的写法一致）
        track = "TOP100" if rank is not None else None
    return CollectionSync(template=template, track=track, rank=rank)


def get_collection_syncs(song: Song) -> List[CollectionSync]:
    """本曲与**各其他版本**参加过的活动，一条一个（主版本在前，同一届只写一次）。

    多版本条目里各版本可能参加的是不同届（例：主版本 ボカコレ2024冬、某个翻唱版 2025春），
    注释区就要把两个模板都写上（用户 2026-09 要求）。
    """
    items = [_collection_sync(song.vocaloid_collection, song.vocaloid_collection_track,
                              song.vocaloid_collection_rank,
                              getattr(song, "vocaloid_collection_places", None))]
    for version in getattr(song, "other_versions", None) or []:
        items.append(_collection_sync(getattr(version, "vocaloid_collection", ""),
                                      getattr(version, "vocaloid_collection_track", None),
                                      getattr(version, "vocaloid_collection_rank", None),
                                      getattr(version, "vocaloid_collection_places", None)))
    result: List[CollectionSync] = []
    for item in items:
        if item is None or any(item.template == existing.template for existing in result):
            continue
        result.append(item)
    return result


def get_collection_sync(song: Song) -> Optional[CollectionSync]:
    """**主版本**参加的活动（规则见 `_collection_sync`）。"""
    return _collection_sync(song.vocaloid_collection, song.vocaloid_collection_track,
                            song.vocaloid_collection_rank,
                            getattr(song, "vocaloid_collection_places", None))


def get_producer_templates(song: Song) -> List[str]:
    """「== 注释 ==」里的 P主大家族模板（受 wikitext.producer_template 开关控制）。

    先查 voca.wiki 的 Category:P主模板 字典（含重定向），没命中才联网搜索；见 utils/voca.py。
    """
    if not get_config().wikitext.producer_template:
        return []
    return list(asyncio.run(get_producer_info(song.creators.producers)))


def create_end(song: Song, producer_templates: Optional[List[str]] = None,
               has_ref: bool = False):
    """条目末尾：注释小节（`<references/>` + 大家族模板）与分类。

    `has_ref` 是「正文里有没有 `<ref>` 脚注」：**没有脚注时连 `== 注释 ==` 标题都不写**
    （用户 2026-10-22 要求），大家族模板（P主 / 歌姬 / 活动）就裸放在歌词后面、
    分类上方 —— 站上就是这么排的：`== 注释 ==` 这一节的唯一作用是承载 `<references/>`，
    没有脚注时那一节只剩几个导航框，等于给它们凭空加了个空标题。
    实测《[Pandora](https://voca.wiki/Pandora)》（ATOLS）与《心淋欲》修订 275845 → 275851
    （用户 2026-10-26 给的差异：编辑把工具写的 `== 注释 ==` 整个删掉，模板留在原处）。
    有脚注时照旧：`== 注释 ==` + `<references/>` + 模板（参《潘多拉(buzzG)》《binary_biotope》）。
    """
    upload_years = get_song_upload_years(song)
    # 活动模板：主版本与各其他版本参加过的活动都写上（多届 → 多个模板）
    vccl_templates = "".join(f"{{{{{item.template}}}}}\n" for item in get_collection_syncs(song))
    # 歌手模板（{{可不/2024}} / {{歌爱雪}}…）只查本地对照表，不联网；它还负责「XX歌曲」分类，
    # 所以不受 producer_template 开关影响 —— 该开关只管要不要联网找 P主的大家族模板。
    # ⚠️ 每个投稿年份各写一次：2025 年投稿、2026 年又在别的站点投稿时，
    # `{{重音Teto/2025}}` 与 `{{重音Teto/2026}}` 都要出现（用户 2026-10-19 反馈）。
    vocaloid_templates = get_vocaloid_templates_for_years(song.creators.vocalists_str(),
                                                          upload_years)
    if producer_templates is None:
        producer_templates = get_producer_templates(song)
    templates = list(producer_templates) + list(vocaloid_templates)
    if get_config().wikitext.collapse_navbox:
        # 导航框默认展开的模板补上 |collapsed（通过 API 读模板源码判断，认不出/取不到则原样保留）
        templates = collapse_all(templates)
    # 少数歌手模板要带上歌姬名（`{{鸣花姬·尊|鸣花姬·尊}}`）才会自己补分类，见
    # `TEMPLATES_WITH_NAME_PARAM`；`collapse_all` 之后再补，免得那个参数被当成 {{{2}}}
    templates = [vocalist_template_call(template, song.creators.vocalists_str())
                 for template in templates]
    # FIXME: duplicates reported here
    producer_templates = join_string(templates, deliminator="",
                                     outer_wrapper=("{{", "}}\n"))
    vocalist_cat = join_string([vocalist for vocalist in song.creators.vocalists_str()
                                if needs_manual_vocalist_category(vocalist)],
                               deliminator="", mapper=name_to_cat,
                               outer_wrapper=('[[分类:', '歌曲]]\n'))
    # 引擎分类与「有没有专属歌手模板」无关：模板只给「XX歌曲」，[[分类:使用XX的歌曲]] 要自己写；
    # 旧实现在有模板时整段丢掉，导致 可不 / 星界 / 歌爱雪 这类歌手的条目缺少引擎分类。
    engine_cat = get_engine_categories(song)
    navbox = producer_templates + vccl_templates
    notes = ""
    if has_ref:
        # 有脚注 → 照旧 `== 注释 ==` + `<references/>` + 模板
        notes = "== 注释 ==\n<references/>\n" + navbox + "\n"
    elif navbox.strip():
        # 没有脚注 → **不写标题**，大家族模板裸放在歌词后面（用户 2026-10-26 给的《心淋欲》差异）
        notes = navbox + "\n"
    return (notes +
"""[[分类:日本音乐作品]]
[[分类:日语歌曲]]
""" + engine_cat + vocalist_cat)


_logger_ready = False


def setup_logger():
    """装上 logs.txt（放在程序目录；双击 exe 时 cwd 不一定是 exe 所在目录）。"""
    global _logger_ready
    if _logger_ready:
        return
    _logger_ready = True
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    try:
        file_handler = logging.FileHandler(application_path.joinpath("logs.txt"), encoding='utf-8')
    except OSError:
        file_handler = None                         # 只读目录（如 Program Files）也不拦着界面启动
    if file_handler is not None:
        file_handler.setFormatter(logging.Formatter('%(name)s :: %(asctime)s :: %(levelname)-8s :: %(message)s',
                                                    '%Y-%m-%d %H:%M:%S'))
        root.addHandler(file_handler)
    if sys.stdout is not None:                       # windowed exe 没有 stdout
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.INFO)
        root.addHandler(stdout_handler)


def create_uploader_note(song: Song) -> str:
    if not get_config().wikitext.uploader_note:
        return ""
    response = prompt_choices(_("uploader_note"), choices=[_("Yes"), _("No")])
    if response == 2:
        return ""
    japanese = prompt_multiline(_("uploader_note_jap"),
                                terminator=is_empty)
    japanese = "<br/>".join(japanese)
    chinese = prompt_multiline(_("uploader_note_chs"),
                               terminator=is_empty)
    chinese = "<br/>".join(chinese)
    # VocaDB 上没写 P 主的歌是存在的（见 utils/vocadb.py 里的同类判断），
    # 原来这里直接 producers[0] 会 IndexError 把整条流程打断
    producers = song.creators.producers
    signature = f"|{auto_lj(producers[0].name)}投稿文" if producers else ""
    return f"""{{{{Cquote|{{{{lj|{japanese}}}}}
----
{chinese}{signature}
}}}}
"""


def get_cover_file(song: Song) -> Path:
    """封面图在**输出目录**里的路径（`<译名>封面.jpg`）。

    自动下载的封面写在这里，用户在样式页「导入图片」换的那张也写在这里 ——
    写它等于换了提交的封面（Songbox 的 `|image` 名字不变）。
    """
    return get_output_path().joinpath(song.image.file_name)


def get_cover_path(song: Song) -> Optional[Path]:
    """本地封面文件路径，优先使用裁剪后的封面；没有本地封面时返回 None。"""
    cover = get_cover_file(song)
    if cover.exists():
        return cover
    if song.image.path and Path(song.image.path).exists():
        return Path(song.image.path)
    return None


def get_song_honors(song: Song):
    """各站点达到殿堂（≥10 万播放）的 (站点, 播放量)，供同步大家族模板用。"""
    honors = []
    for site in (VideoSite.NICO_NICO, VideoSite.BILIBILI, VideoSite.YOUTUBE):
        video = get_video(song.videos, site)
        if video and video.canonical and video.views >= 100000:
            honors.append((site.value, video.views))
    return honors


# 同一天有多支稿件时谁算「本曲投稿」：站上的顺序是 niconico > YouTube > bilibili
# （用户 2026-10-30 指定）。`min()` 取键最小的那条，所以数值越小越优先。
POST_SITE_ORDER: Dict[str, int] = {"niconico": 0, "YouTube": 1, "bilibili": 2}


def _posted_date_of(video) -> Optional[datetime]:
    """这条稿件「什么时候投的」：有时分的用东八区墙钟，只有日期的补 00:00。

    `uploaded_cn` 是抓页面拿到的精确时刻，没有（或也是 epoch）才退回 `uploaded`。
    """
    precise = getattr(video, "uploaded_cn", None)
    when = precise if getattr(precise, "year", 0) >= 2000 else getattr(video, "uploaded", None)
    if not when:
        return None
    if not isinstance(when, datetime):
        when = datetime(getattr(when, "year", 0), getattr(when, "month", 1),
                        getattr(when, "day", 1))
    return when


def _posted_sort_key(video) -> Tuple[int, ...]:
    """`get_song_posted()` 挑稿件用的排序键（越小越优先）。

    1. 日期最早的在前；
    2. 同一天：**有时分**的优先 —— 只有日期的那条等于 00:00，不能凭它判「更早」；
    3. 再按时分（两条都有时分时，真的更早的那支赢）；
    4. 还分不出就按站点：`niconico > YouTube > bilibili`（见 `POST_SITE_ORDER`）。

    以前只按 `video.uploaded`（只到日）排、靠列表顺序决胜：VocaDB 的 PV 顺序不保证 nico
    在前，实测《さよならだけが人生か》PV 是 YouTube → nico，于是取到了 YouTube 那条只有日期
    的记录 —— 注释写成 `youtube 10-13`（丢了 nico 的 21:00，还多标了站点名），
    也就是用户 2026-10-30 报的「niconico 更早却优先 YouTube」。
    """
    when = _posted_date_of(video) or datetime(1970, 1, 1)
    precise = 0 if getattr(video, "uploaded_cn", None) else 1
    site = POST_SITE_ORDER.get(str(getattr(getattr(video, "site", None), "value", "")), 9)
    return (when.year, when.month, when.day, precise, when.hour, when.minute, site)


def get_song_posted(song: Song, year: Optional[int] = None) -> Optional[PostedAt]:
    """本曲的投稿时刻（写进家族模板列表的日期注释用，如 `<!-- 02-22 23:00 -->`）。

    取**最早的那个原投稿**：模板列表是按投稿时间排的（同一时刻的排在一起）。同一日期上有
    好几支稿件时按 `_posted_sort_key()` 决胜（有时分的优先、再按 niconico > YouTube > bilibili）。
    时刻用东八区墙钟、保留到分钟（`Video.uploaded_cn`，与 `|nnd_date` 等口径一致）；
    拿不到时间时就只有日期 —— 那时注释写 `02-22`、排序也只看日。

    `year` 给定时只看**那一年**的稿件：跨年投稿（2025 年 niconico、2026 年 YouTube）时，
    `Template:<歌姬>/2026` 里应当按 2026 那个稿件的日期排，而不是 2025 年的。
    其他版本自己的投稿也算（见 `other_version_videos()`）：Live 版 2018 年投稿，
    `{{KAITO/2018}}` 里要按 2018 那一份的日期排。
    """
    videos = [*only_canonical_videos(song.videos), *other_version_videos(song)]
    if year is not None:
        # 换年投稿时那一年的稿件才算数；那一年一支都没有才退回全部（老口径）
        videos = [video for video in videos
                  if getattr(getattr(video, "uploaded", None), "year", 0) == year] or videos
    if not videos:
        return None
    # 抓取失败的稿件 `uploaded` 是 epoch（1970），先剔掉 —— 否则它会永远「最早」，
    # 把同一首歌里真正拿到日期的稿件挤掉（以前是取到它再返回 None，白丢一次机会）
    dated = [video for video in videos
             if getattr(_posted_date_of(video), "year", 0) >= 2000]
    if not dated:
        return None
    primary = min(dated, key=_posted_sort_key)
    when = _posted_date_of(primary)
    if when is None or when.year < 2000:
        return None
    # `has_time`：只有页面 / API 真给了时分时才在注释里写时刻。日期-only 的记录
    # （`when` 补成 00:00）以前会被写成「YouTube xx-xx 00:00」—— 用户 2026-10-31 反馈
    # 「为什么大部分都标的油管 0 点」，实际发布时间是 7 日 23 点。
    precise = getattr(primary, "uploaded_cn", None)
    return PostedAt(when=when, site=primary.site.value,
                    has_time=bool(precise) and getattr(precise, "year", 0) >= 2000)


def build_family_sync(song: Song, producer_templates: Sequence[str] = ()) -> FamilySync:
    """提交窗口「同步修改大家族模板」用：注释区模板 + 荣誉 / 活动信息。

    `templates` 是**每个投稿年份**各一份（2025 与 2026 都有稿件 → `重音Teto/2025` 与
    `重音Teto/2026` 都要写）；`year` 仍是最早那一年，给 P主模板与不分年份的模板用。
    """
    years = get_song_upload_years(song)
    year = years[0] if years else None
    return FamilySync(
        templates=get_vocaloid_templates_for_years(song.creators.vocalists_str(), years),
        honors=get_song_honors(song),
        collections=get_collection_syncs(song),
        producers=list(producer_templates),
        # 同名歌并存时活动模板靠 P主名认出「歌名(我们的P主)」那一行（《EDEN》那笔差异）
        producer_names=list(song.creators.producers_str()),
        year=year,
        years=years,
        # 歌姬名：模板用 `{{coloredlink|#色|…}}` 列条目时（如 {{梦的结唱}}）据此配色
        vocalists=list(song.creators.vocalists_str()),
        posted=get_song_posted(song),
        # 每个年份子页按**那一年那份稿件**的日期排（2026 年的模板里别用 2025 年的日期）
        posted_by_year={value: get_song_posted(song, value) for value in years},
    )


def build_cover_info(song: Song) -> Optional[CoverInfo]:
    """准备随条目一并提交的封面信息（含封面歌姬分类询问）；没有本地封面时返回 None。"""
    path = get_cover_path(song)
    if path is None:
        return None
    return CoverInfo(
        path=path,
        wiki_name=get_cover_filename(song),
        source_url=song.image.source_url,
        authors=cover_authors(song),
        characters=choose_characters(song.creators.vocalists_str()),
    )


def cover_authors(song: Song) -> List[Person]:
    """文件说明页的作者分类：**只挂一位**，曲绘有好几位时问用户挑谁（问过就记住）。

    站上的惯例是一张图一个作者（用户 2026-10-21 反馈「一张图对应一个作者吧」）：
    VocaDB 记了六位曲绘的合作曲，以前会一次挂六个 `[[分类:作者:…]]`。
    """
    image = song.image
    current = getattr(image, "cover_authors", None)
    if current is None:
        # 用 getattr/setattr 而不是直接读写字段：测试里有拿 SimpleNamespace 当 Image 的假对象
        current = choose_cover_author(getattr(image, "creators", None))
        try:
            image.cover_authors = current
        except AttributeError:                      # 不可写的假对象：每次都算一遍也无妨
            pass
    return list(current)


def build_version_covers(song: Song) -> List[CoverInfo]:
    """其他版本随条目一起提交的封面（样式页里为各版本「导入图片」写进输出目录的那些）。

    只收**本地真的有文件**的版本：没导入过封面就没有 `|image` 那行对应的图可传，
    提交时跳过（Songbox 里那个文件名留着，用户以后可以自己补图）。
    """
    covers = []
    for version in (getattr(song, "other_versions", None) or []):
        name = get_other_version_cover_name(song, version)
        path = get_output_path().joinpath(name)
        if path.exists():
            covers.append(CoverInfo(path=path, wiki_name=name, authors=cover_authors(song)))
    return covers


def build_other_version_styles(song: Song) -> List[Dict[str, str]]:
    """样式页里「版本」下拉框的选项：主版本之后每个其他版本一项。

    `key` 是 `song.other_versions` 的下标（字符串），样式页原样回传，
    `apply_other_version_styles()` 按它把结果对回版本对象。
    """
    options = []
    for index, version in enumerate(getattr(song, "other_versions", None) or []):
        # 这一版自己的稿件（VocaDB 的 nico / YouTube + 用户给的 B 站）：样式页「取该版封面」要用。
        # 只在这一层拿得到这些东西，所以随 payload 一起传给样式页（同进程的普通字典）
        videos = list(getattr(version, "videos", None) or [])
        if getattr(version, "video", None) is not None:
            videos = [*videos, version.video]
        options.append({
            "key": str(index),
            "label": version.tab_label or version.label or f"版本{index + 2}",
            "colors": getattr(version, "songbox_colors", "") or "",
            "image": get_other_version_cover_name(song, version),
            "cover_file": str(get_output_path().joinpath(
                get_other_version_cover_name(song, version))),
            "videos": videos,
        })
    return options


def apply_other_version_styles(song: Song, editing) -> None:
    """把样式页返回的各版本样式（颜色）写回对应的 `OtherVersion`。

    各版本的封面（`|image` 的文件名由 `get_other_version_cover_name()` 固定生成）在样式页
    「导入图片」时就已经写进输出目录，提交时由 `build_version_covers()` 一并上传，
    这里只需要把颜色带回来；空值不覆盖，免得把已有的配色抹掉。
    """
    versions = list(getattr(song, "other_versions", None) or [])
    for key, values in (getattr(editing, "other_versions", None) or {}).items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(versions):
            continue
        colors = (values.get("colors") or "").strip()
        if colors:
            versions[index].songbox_colors = colors


def open_output_file(path: Path) -> None:
    """用 VS Code 打开输出文件；不可用时回退到默认浏览器。"""
    code_command = shutil.which("code") or shutil.which("code.cmd")
    if code_command:
        try:
            subprocess.Popen([code_command, "--reuse-window", str(path.absolute())])
            return
        except OSError:
            logging.warning("Unable to open the output file with VS Code. Falling back to the default browser.")
    else:
        logging.warning("VS Code command 'code' was not found. Falling back to the default browser.")
    webbrowser.open("file://" + str(path.absolute()))


def generate():
    """跑一遍完整的生成流程（主界面与终端模式共用）；返回输出目录。"""
    # 日志：文件日志（logs.txt）照旧；界面模式下 sys.stdout 已经被导到「日志」页，
    # 所以控制台那一路会写进界面。只在这里装一次，避免重复加 handler。
    setup_logger()
    load_config(application_path.joinpath("config.yaml"))
    setup_save_input(get_config().save_to_file)
    if get_config().wiki.submit_window and not login.is_logged_in():
        # 提交窗口允许未登录时仅做预览/编辑，因此这里登录失败不中断流程
        login.try_login()
    data.name_japanese = prompt_response(_("name_original"))
    name_chinese = prompt_response(_("name_trans"))
    if is_empty(name_chinese):
        name_chinese = data.name_japanese
    song = get_song_by_name(data.name_japanese, name_chinese)
    if not song:
        raise NotImplementedError(_("only_vocadb"))
    # 同名条目：探测 Vocawiki 上是否已有同名条目，决定条目名 / 文件名与顶部模板
    prepare_disambig(song)
    # 人声本家（同曲的人声演唱版本）：WikitextConfig.human_original 开启时才问
    if get_config().wikitext.human_original:
        song.human_original = get_human_original()
    # 其他版本（同一首歌的翻唱 / 改编版本，参 voca.wiki《鸟之诗》）：
    # 候选列表来自 VocaDB 的 alternateVersions，用户逐条挑，挑中的那些要给出 B 站链接
    # 与「是否官方投稿」；一个都没挑中时下面按单版本生成（不套 {{tabs}}）
    if get_config().wikitext.other_versions:
        other_versions.choose_other_versions(song)
    if get_config().color.color_editor:
        # cover_file 一并传给样式页：用户在那儿「导入图片」换的图会写成提交用的封面
        # versions：每个「其他版本」一项，样式页可以让用户挑一版单独改它的 Songbox
        song.color_editing = open_color_editor(build_initial_color_wiki(song), get_cover_path(song),
                                               lyrics_hover=bool(song.lyrics.use_hover),
                                               cover_file=get_cover_file(song),
                                               videos=list(song.videos),
                                               versions=build_other_version_styles(song))
        if song.color_editing is not None:
            apply_other_version_styles(song, song.color_editing)
    version_tabs = ""
    if list(getattr(song, "other_versions", None) or []):
        # 多版本：About / 标题替换留在 {{tabs}} 外面，荣誉题头 + Songbox + 简介各自进自己的 tab
        header = create_page_title(song)
        version_tabs = create_tabs(song, create_intro(song))
        intro = ""
    else:
        header = create_header(song)
        intro = create_intro(song)
    uploader_note = create_uploader_note(song)
    song_body = create_song(song)
    lyrics = create_lyrics(song)
    # P主模板只算一次：注释区要用，提交窗口的「同步大家族模板」也要用
    producer_templates = get_producer_templates(song)
    # 正文里有没有 `<ref>` 脚注（翻译来源那类）：没有就不写 `== 注释 ==` / `<references/>`，
    # 大家族模板裸放在歌词后面（见 `create_end()`）
    body_parts = [header, uploader_note, version_tabs, intro, song_body, lyrics]
    body = "\n".join(part for part in body_parts if part)
    end = create_end(song, producer_templates, has_ref="<ref" in body)
    wikitext_dir = get_output_path().joinpath(f"{safe_filename(song.page_name or song.name_chs)}.wikitext")
    content = "\n".join(part for part in [body, end] if part)
    write_to_file(content, wikitext_dir)
    # 取数用的浏览器（见 utils/browser_fetch.py）到这就没用了：立刻收掉，别让它一直在后台占内存。
    # 下一轮再需要时它会自己重新开（profile 留着，所以不用再过人机校验）。
    browser_fetch.shutdown()
    print(_("prog_end"))
    if get_config().wiki.submit_window:
        # 点亮提交页：实时预览 / 编辑 / 提交条目与封面；用不了时回退到 VS Code
        opened = open_submit_editor(page_name=song.page_name or song.name_chs, wikitext=content,
                                    source_path=wikitext_dir, ja_name=song.name_jap,
                                    create_redirect=get_config().wiki.create_redirect,
                                    cover=build_cover_info(song),
                                    version_covers=build_version_covers(song),
                                    family=build_family_sync(song, producer_templates),
                                    disambig_plan=song.disambig)
        if opened:
            return get_output_path()
    open_output_file(wikitext_dir)
    return get_output_path()


def main():
    """入口：有图形界面就在主窗口里跑，否则（或加 --console）退回终端。

    图形界面下三个功能都在侧栏里（生成歌曲条目 / 生成P主模板 / 生成歌姬模板），
    切一下就换流程；终端模式下用 `--producer` / `--vocalist` 跑模板那两条。
    """
    setup_logger()                                # 先装日志：窗口程序没有 stderr 可看
    logging.info("启动：frozen=%s stdout=%r stderr=%r",
                 getattr(sys, "frozen", False), sys.stdout, sys.stderr)
    updater.cleanup_leftovers()                   # 上次「检查更新」换下来的旧 exe，删掉
    if ui.available():
        ui.run(generate, features={"entry": generate,
                                  "producer": generate_producer_template,
                                  "vocalist": generate_vocalist_template})
        return
    if getattr(sys, "frozen", False) and sys.stdout is None:
        # 打包成窗口程序又没有 Qt：既没有界面也没有终端，只能把原因写进日志
        logging.error("PyQt5 不可用，且当前没有终端可以交互，无法继续。"
                      "请把程序目录下的 logs.txt 发给开发者，或用 --console 从命令行启动。")
        return
    sys.stdout.reconfigure(encoding='utf-8')
    if "--producer" in sys.argv:
        generate_producer_template()
    elif "--vocalist" in sys.argv:
        generate_vocalist_template()
    else:
        generate()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    # Android 已在文件顶部交棒给 Kivy（见最前面的 ANDROID_ARGUMENT 分支）。
    try:
        main()
    except NotImplementedError as e:
        logging.error("NotImplementedError")
        logging.error(str(e))
    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(str(e))
    logging.error(_("err_unexpected"))
    if not ui.is_active() and sys.stdout is not None:
        input(_("enter_exit"))
