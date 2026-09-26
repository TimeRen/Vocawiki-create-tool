import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from config import data
from config.config import load_config, get_config, application_path, get_output_path
from models.creators import person_list_to_str, Staff, role_priority
from models.song import Song, Lyrics, add_no_hover
from models.video import (HumanOriginal, VideoSite, Video, view_count_from_site, get_video,
                          get_human_original, only_canonical_videos)
from utils import login
from utils.helpers import prompt_choices, prompt_response, prompt_multiline
from utils.image import write_to_file
from utils.voca import get_producer_info
from utils.name_converter import name_to_cat, name_to_chinese, vocaloid_names, get_engine
from utils.save_input import setup_save_input
from utils.string import auto_lj, is_empty, datetime_to_ymd, assert_str_exists, join_string, safe_filename
from utils.upload import choose_characters
from utils.vocadb import get_song_by_name
from utils.color_editor import open_color_editor, build_initial_color_wiki
from utils import disambig
from utils import ui
from utils.family_template import CollectionSync, FamilySync, collapse_all
from utils.lyrics_colors import build_colors_params, mark_lines
from utils.submit_editor import open_submit_editor, CoverInfo

from i18n.i18n import _


def get_song_names(song: Song) -> List[str]:
    # FIXME: disable name_other?
    names = [auto_lj(song.name_jap), song.name_chs if song.name_chs != song.name_jap else None, *song.name_other]
    return [name for name in names if not is_empty(name)]


def get_song_engines(song: Song) -> List[str]:
    """歌曲用到的合成引擎：每个歌姬按 ENGINES 的优先级只算一个引擎，去重并保持出现顺序。

    同一个歌姬可能同时出现在多个引擎的角色表里（例：可不 在 CeVIO / Synthesizer V / VoiSona
    三张表里都有），旧实现会把三个引擎全写进简介，这里只取优先级最高的那个。
    """
    engines: List[str] = []
    for vocalist in song.creators.vocalists_str():
        engine = get_engine(vocalist)
        if engine not in engines:
            engines.append(engine)
    return engines


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
    """
    platform = {VideoSite.NICO_NICO: "nnd", VideoSite.BILIBILI: "bb",
                VideoSite.YOUTUBE: "yt"}[video.site]
    parts = [platform, video.identifier, datetime_to_ymd(video.uploaded)]
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


def get_human_original(song: Song) -> Optional[HumanOriginal]:
    return getattr(song, "human_original", None)


def human_original_links(song: Song) -> List[str]:
    """人声本家的视频嵌入模板（可能只有 niconico/YouTube，也可能只有 B 站）。"""
    human = get_human_original(song)
    if human is None:
        return []
    return [video_embed(video) for video in (human.video, human.bilibili) if video]


def human_original_sentence(song: Song) -> str:
    """简介里的那句「另有…人声本家。」（参 红色房间：另有x0o0x_演唱的人声本家。）

    工具不追问演唱者，统一写成「P主本人」；若实际是其他唱见，在提交窗口里改这一句。
    """
    if not human_original_links(song):
        return ""
    return "另有P主本人演唱的人声本家。"


def create_header(song: Song) -> str:
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
            if video.views >= 10000000:
                rank = 3
            elif video.views >= 1000000:
                rank = 2
            else:
                rank = 1
            rank_fields.append(f"{rank_name}={rank}")
    top = ""
    if rank_fields:
        top = "{{虚拟歌手歌曲荣誉题头|" + "|".join([*categories, *rank_fields]) + "}}\n"
    if song.name_chs != song.name_jap:
        top += "{{标题替换|" + auto_lj(song.name_jap) + "}}\n"
    # 同名条目：最顶部加 {{About}}（共 2 个）/ {{Otheruseslist}}（3 个以上），参 向日葵(Teary Planet)
    about = disambig.top_template(getattr(song, "disambig", None))
    top = f"{about}\n{top}" if about else top
    video_fields = []
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
            if site == VideoSite.BILIBILI and video and not video.canonical:
                continue
            if video and video.canonical:
                video_id = video.identifier
                video_date = datetime_to_ymd(video.uploaded)
            else:
                video_id = ""
                video_date = ""
            video_fields.extend([f"|{field_prefix}_id = {video_id}\n",
                                 f"|{field_prefix}_date = {video_date}\n"])
    illustrator = song.image.creators
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
    return f"""{top}{{{{VOCALOID_Songbox
|image    = {get_cover_filename(song)}
{image_info_field}{color_field}|演唱    = {join_string(song.creators.vocalists_str(), outer_wrapper=("[[", "]]"),
                      mapper=name_to_chinese, deliminator="、")}
|歌曲名称 = {"<br/>".join(get_song_names(song))}
|P主 = {"<br/>".join([auto_lj('[[' + p.name + ']]') for p in song.creators.producers])}
{"".join(video_fields)}}}}}
"""


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


def create_intro(song: Song):
    nc = song.name_chs
    nj = song.name_jap
    videos = song.videos
    categories = get_song_categories(song)
    start = "《'''" + auto_lj(nj) + "'''》"
    albums = f"""收录于专辑{join_string(song.albums, mapper=auto_lj, outer_wrapper=("《'''", "'''》"))}。""" \
        if len(song.albums) > 0 else ""
    collection_punctuation = "，" if song.albums else "。"
    if song.vocaloid_collection_rank and song.vocaloid_collection_track:
        collection_rank = (f"并获得{song.vocaloid_collection_track}中的第"
                           f"'''{song.vocaloid_collection_rank}'''名")
    elif song.vocaloid_collection_track == "榜外":
        collection_rank = ""
    else:
        collection_rank = (f"并获得TOP100中的第'''{song.vocaloid_collection_rank}'''名"
                           if song.vocaloid_collection_rank else "")
    collection = (f"本曲参与了[[The VOCALOID Collection]]({{{{lj|{song.vocaloid_collection}}}}})活动{collection_rank}{collection_punctuation}"
                  if song.vocaloid_collection else "")
    tail = f"\n\n{collection}{albums}" if collection else albums
    # 人声本家：单独一段，排在活动 / 专辑那段之前（参 如月车站、泡沫金鱼）
    human = human_original_sentence(song)
    human_tail = f"\n\n{human}" if human else ""
    return (start +
            f"{'' if nc == nj else f'（{nc}）'}" +
            f"""是由{join_string(song.creators.producers_str()[:1],
                               inner_wrapper=('[[', ']]'),
                               mapper=auto_lj)}""" +
            videos_to_str2(videos) + f"的{join_engines(categories)}日语原创歌曲，" +
            f"""由{join_string(song.creators.vocalists_str(),
                              outer_wrapper=('[[', ']]'),
                              mapper=name_to_chinese)}演唱。""" +
            human_tail +
            tail + "\n")


def create_song(song: Song):
    video_player = ""
    v = get_video(song.videos, VideoSite.BILIBILI)
    if v:
        video_player = f"{{{{" \
                       f"bilibiliVideo|id={v.identifier}" \
                       f"}}}}"
    # 有人声本家时，按版本分块并加标签（参 红色房间 / 如月车站）：
    #   ;VOCALOID本家
    #   {{BilibiliVideo|id=…}}
    #
    #   ;人声本家
    #   {{sm|sm…}}
    #   {{BilibiliVideo|id=…}}
    human = human_original_links(song)
    if human:
        blocks = []
        if video_player:
            blocks.append(f";{get_song_categories(song)[0]}本家\n{video_player}")
        blocks.append(";人声本家\n" + "\n".join(human))
        video_player = "\n\n".join(blocks)
    groups: List[Staff] = sorted(song.creators.staff_list(),
                                 key=lambda staff: role_priority(staff[0]))
    if {role for role, _ in groups} == {"词曲", "演唱"}:
        return video_player
    groups: List[str] = [f"|group{index + 1} = {g[0]}\n"
                         f"|list{index + 1} = {join_string(person_list_to_str(g[1]), deliminator='<br/>', mapper=auto_lj)}\n"
                         for index, g in enumerate(groups)]
    introduction_color_style = ""
    if get_config().wikitext.optimize_Introduction_color:
        introduction_color_style = "; padding: 6px 12px; border-radius: 4px 0 0 4px; box-shadow: 2px 2px 5px rgba(0,0,0,0.2);"
    introduction_text_style = ""
    if get_config().wikitext.optimize_Introduction_color:
        introduction_text_style = "; border: 1px solid #B0C4DE; font-weight: bold"
    editing = song.color_editing
    default_bg = song.colors.background.to_hex() if song.colors else "#000"
    default_fg = song.colors.text.to_hex() if song.colors else "white"
    # 编辑器里改过就用编辑器写好的值（可能含多条 CSS 声明），否则用默认色 + 优化后缀
    if editing and editing.introduction_bg:
        lbgcolor = editing.introduction_bg
        introduction_color_style = ""
    else:
        lbgcolor = default_bg
    if editing and editing.introduction_fg:
        ltcolor = editing.introduction_fg
        introduction_text_style = ""
    else:
        ltcolor = default_fg
    color = f"|lbgcolor = {lbgcolor}{introduction_color_style}\n" \
            f"|ltcolor = {ltcolor}{introduction_text_style}\n"
    # 标签格带额外声明时，模板里的 border: <lbgcolor> 1px solid 会被写坏，
    # 由编辑器额外给出列表格边框色（与 lbgcolor 同色）
    if editing and editing.introduction_border:
        color += f"|rbdcolor = {editing.introduction_border}\n"
    return (f"== 歌曲 ==\n"
            "{{VOCALOID Songbox Introduction\n"
            + color +
            f"{''.join(groups)}"
            f"}}}}\n\n{video_player}")


def create_lyrics(song: Song):
    lyrics = song.lyrics
    editing = song.color_editing
    # 悬停显示译文（{{LyricsKai/hover}}）：歌词整理窗口与颜色编辑器「歌词」面板的开关任一开启即生效
    use_hover = bool(lyrics.use_hover or (editing and editing.lyrics_hover))
    lyrics_chs = lyrics.lyrics_chs
    lyrics_roma = lyrics.lyrics_roma
    chs_exist = lyrics_chs is not None
    if use_hover:
        # hover 模式下译文与原文排在同一行，空行要补 #NoHover 才不会出现悬停区
        lyrics_jap = add_no_hover(lyrics.lyrics_jap)
        if chs_exist:
            lyrics_chs = add_no_hover(lyrics_chs)
    else:
        lyrics_jap = lyrics.lyrics_jap
    # 演唱者上色（{{LyricsKai/colors}}）：歌词整理窗口的开关，按「每行标了谁」生成 charas / colors 与 @n 标记
    use_colors = bool(lyrics.use_colors)
    colors_params = ""
    if use_colors:
        plan, colors_params = build_colors_params(song.creators.vocalists_str(), lyrics.chara_marks,
                                                  splits=lyrics.chara_splits)
        if plan.available:
            # 行内分段：两栏各按自己的切分点插标记（中文栏没切分过时整行用第一段的颜色）
            lyrics_jap = mark_lines(lyrics_jap, plan, "jap")
            if chs_exist:
                lyrics_chs = mark_lines(lyrics_chs, plan, "chs")
        else:                                  # 没有歌姬信息就不加 /colors，避免生成空参数
            use_colors, colors_params = False, ""
    if chs_exist:
        translator = assert_str_exists(lyrics.translator)
        if translator and not is_empty(lyrics.translator_url):
            translator = f"[{lyrics.translator_url} {translator}]"
        translation_notice = f"*翻译：{translator}"
        source_name = assert_str_exists(lyrics.source_name)
        source_url = assert_str_exists(lyrics.source_url)
        if source_name and source_url:
            source = f"[{source_url} {source_name}]"
            translation_notice += f"<ref>翻译转载自{source}</ref>"
        elif source_name:
            if not translator and not lyrics.translator_url:
                translation_notice = f"*翻译转自{source_name} "
            else:
                translation_notice += f"<ref>翻译转载自{source_name}</ref>"
        elif source_url:
            translation_notice += f"<ref>翻译转载自[{source_url}]</ref>"
    else:
        translation_notice = ""
    has_roma = not use_hover and not is_empty(lyrics.lyrics_roma)
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
    return f"""== 歌词 ==
{translation_notice}
{"{{LyricsKai/Roma/button}}" if has_roma else ""}
{{{{LyricsKai{lyrics_template}{'/Roma' if has_roma else ''}
{colors_params}{style_block}|original=
{assert_str_exists(lyrics_jap).strip()}
|translated=
{lyrics_chs.strip() if chs_exist else ''}
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
    'IA': 'IA',
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
    '结月缘': '结月缘',
    '歌爱雪': '歌爱雪',
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

# 名字里含关键词就套用（沿用原来的兜底写法，还能容忍 vocadb 名里的后缀）
vocaloid_template_mapper = {
    '鸣花': '鸣花姬·尊',
    'NurseRobot': 'NurseRobot TypeT',
}

# 实测这些模板不会自己加「XX歌曲」分类 → 分类仍由 vocalist_cat 手写
TEMPLATES_WITHOUT_CATEGORY = {
    '梦的结唱',                                  # 纯导航框，没有 {{ac}}
    '鸣花姬·尊',                                  # 要传 {{{2}}} 才加分类，这里不传
}

# vocadb 里有些声库名带「(Unknown)」后缀（如「小春六花 (Unknown)」），比对前先去掉
UNKNOWN_SUFFIX_RE = re.compile(r"\s*\(Unknown\)$")


def get_vocaloid_template(vocalist: str, year: int = None) -> Optional[str]:
    """单个歌姬对应的「== 注释 ==」模板名；没有专属模板时返回 None。"""
    name = UNKNOWN_SUFFIX_RE.sub("", vocaloid_names[vocalist] if vocalist in vocaloid_names
                                 else vocalist)
    template = VOCALOID_TEMPLATES.get(name)
    if template is None:
        for key, value in vocaloid_template_mapper.items():
            if key in name:
                template = value
                break
    if template is None:
        return None
    if "{year}" in template:
        return template.format(year=year) if year is not None else template.split("/")[0]
    return template


def get_vocaloid_templates(vocaloids: List[str], year: int = None) -> List[str]:
    """多个歌姬的模板（保持出现顺序并去重）。"""
    result: List[str] = []
    for vocalist in vocaloids:
        template = get_vocaloid_template(vocalist, year)
        if template and template not in result:
            result.append(template)
    return result


def needs_manual_vocalist_category(vocalist: str) -> bool:
    """该歌姬是否还要手写 [[分类:XX歌曲]]：没有专属模板，或模板不带分类。"""
    template = get_vocaloid_template(vocalist)
    return template is None or template in TEMPLATES_WITHOUT_CATEGORY


def get_song_upload_year(song: Song):
    videos = only_canonical_videos(song.videos)
    return min((video.uploaded for video in videos), default=None).year if videos else None


def get_collection_template_name(song: Song) -> Optional[str]:
    """《The VOCALOID Collection》对应的模板名（如 The VOCALOID Collection2024冬）。"""
    name = song.vocaloid_collection
    if not name:
        return None
    if name.startswith("ボカコレ"):
        name = name[len("ボカコレ"):]
    elif name.startswith("The VOCALOID Collection"):
        name = name[len("The VOCALOID Collection"):].strip()
    return f"The VOCALOID Collection{name}"


def get_collection_sync(song: Song) -> Optional[CollectionSync]:
    """活动模板要写进哪一段：赛道 + 名次（榜外 / 没名次时写「未上榜歌曲」）。"""
    template = get_collection_template_name(song)
    if not template:
        return None
    track = song.vocaloid_collection_track
    rank = song.vocaloid_collection_rank
    if isinstance(rank, str):
        rank = int(rank) if rank.isdigit() else None
    if track == "榜外":
        return CollectionSync(template=template)
    if not track:
        # vocadb 只给名次时按 TOP100 处理（与简介里的写法一致）
        track = "TOP100" if rank is not None else None
    return CollectionSync(template=template, track=track, rank=rank)


def get_producer_templates(song: Song) -> List[str]:
    """「== 注释 ==」里的 P主大家族模板（受 wikitext.producer_template 开关控制）。

    先查 voca.wiki 的 Category:P主模板 字典（含重定向），没命中才联网搜索；见 utils/voca.py。
    """
    if not get_config().wikitext.producer_template:
        return []
    return list(asyncio.run(get_producer_info(song.creators.producers)))


def create_end(song: Song, producer_templates: Optional[List[str]] = None):
    upload_year = get_song_upload_year(song)
    collection_template = get_collection_template_name(song)
    vccl_templates = f"{{{{{collection_template}}}}}\n" if collection_template else ""
    # 歌手模板（{{可不/2024}} / {{歌爱雪}}…）只查本地对照表，不联网；它还负责「XX歌曲」分类，
    # 所以不受 producer_template 开关影响 —— 该开关只管要不要联网找 P主的大家族模板。
    vocaloid_templates = get_vocaloid_templates(song.creators.vocalists_str(), upload_year)
    if producer_templates is None:
        producer_templates = get_producer_templates(song)
    templates = list(producer_templates) + list(vocaloid_templates)
    if get_config().wikitext.collapse_navbox:
        # 导航框默认展开的模板补上 |collapsed（通过 API 读模板源码判断，认不出/取不到则原样保留）
        templates = collapse_all(templates)
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
    return ( """== 注释 ==
<references/>
""" + producer_templates +
vccl_templates +
"""
[[分类:日本音乐作品]]
[[分类:日语歌曲]]
""" + engine_cat + vocalist_cat)


def setup_logger():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler('logs.txt', encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(name)s :: %(asctime)s :: %(levelname)-8s :: %(message)s',
                                                '%Y-%m-%d %H:%M:%S'))
    root.addHandler(file_handler)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    root.addHandler(stdout_handler)


def create_uploader_note(song: Song) -> str:
    if not get_config().wikitext.uploader_note:
        return ""
    response = prompt_choices(_("uploader_note"), choices=["Yes", "No"])
    if response == 2:
        return ""
    japanese = prompt_multiline(_("uploader_note_jap"),
                                terminator=is_empty)
    japanese = "<br/>".join(japanese)
    chinese = prompt_multiline(_("uploader_note_chs"),
                               terminator=is_empty)
    chinese = "<br/>".join(chinese)
    return f"""{{{{Cquote|{{{{lj|{japanese}}}}}
----
{chinese}|{auto_lj(song.creators.producers[0].name)}投稿文
}}}}
"""


def get_cover_path(song: Song) -> Optional[Path]:
    """本地封面文件路径，优先使用裁剪后的封面；没有本地封面时返回 None。"""
    cover = get_output_path().joinpath(song.image.file_name)
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


def build_family_sync(song: Song, producer_templates: Sequence[str] = ()) -> FamilySync:
    """提交窗口「同步修改大家族模板」用：注释区模板 + 荣誉 / 活动信息。"""
    year = get_song_upload_year(song)
    collection = get_collection_sync(song)
    return FamilySync(
        templates=get_vocaloid_templates(song.creators.vocalists_str(), year),
        honors=get_song_honors(song),
        collections=[collection] if collection else [],
        producers=list(producer_templates),
        year=year,
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
        authors=song.image.creators,
        characters=choose_characters(song.creators.vocalists_str()),
    )


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
    if get_config().color.color_editor:
        song.color_editing = open_color_editor(build_initial_color_wiki(song), get_cover_path(song),
                                               lyrics_hover=bool(song.lyrics.use_hover))
    header = create_header(song)
    uploader_note = create_uploader_note(song)
    intro = create_intro(song)
    song_body = create_song(song)
    lyrics = create_lyrics(song)
    # P主模板只算一次：注释区要用，提交窗口的「同步大家族模板」也要用
    producer_templates = get_producer_templates(song)
    end = create_end(song, producer_templates)
    wikitext_dir = get_output_path().joinpath(f"{safe_filename(song.page_name or song.name_chs)}.wikitext")
    content = "\n".join(part for part in [header, uploader_note, intro, song_body, lyrics, end] if part)
    write_to_file(content, wikitext_dir)
    print(_("prog_end"))
    if get_config().wiki.submit_window:
        # 点亮提交页：实时预览 / 编辑 / 提交条目与封面；用不了时回退到 VS Code
        opened = open_submit_editor(page_name=song.page_name or song.name_chs, wikitext=content,
                                    source_path=wikitext_dir, ja_name=song.name_jap,
                                    create_redirect=get_config().wiki.create_redirect,
                                    cover=build_cover_info(song),
                                    family=build_family_sync(song, producer_templates),
                                    disambig_plan=song.disambig)
        if opened:
            return get_output_path()
    open_output_file(wikitext_dir)
    return get_output_path()


def main():
    """入口：有图形界面就在主窗口里跑，否则（或加 --console）退回终端。"""
    if ui.available():
        ui.run(generate)
        return
    if getattr(sys, "frozen", False) and sys.stdout is None:
        # 打包成窗口程序又没有 Qt：既没有界面也没有终端，只能把原因写进日志
        setup_logger()
        logging.error("PyQt5 不可用，且当前没有终端可以交互，无法继续。"
                      "请重新安装完整的分发包，或用 --console 从命令行启动。")
        return
    sys.stdout.reconfigure(encoding='utf-8')
    generate()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
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
