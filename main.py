import asyncio
import logging
import os
import shutil
import subprocess
import sys
import traceback
import webbrowser
from pathlib import Path
from typing import List, Optional

from config import data
from config.config import load_config, get_config, application_path, get_output_path
from models.creators import person_list_to_str, Staff, role_priority
from models.song import Song, Lyrics, add_no_hover
from models.video import VideoSite, Video, view_count_from_site, get_video, only_canonical_videos
from utils import login
from utils.helpers import prompt_choices, prompt_response, prompt_multiline
from utils.image import write_to_file
from utils.voca import get_producer_info
from utils.name_converter import name_to_cat, name_to_chinese, vocaloid_names, ENGINES, get_engine
from utils.save_input import setup_save_input
from utils.string import auto_lj, is_empty, datetime_to_ymd, assert_str_exists, join_string, safe_filename
from utils.upload import choose_characters
from utils.vocadb import get_song_by_name
from utils.color_editor import open_color_editor, build_initial_color_wiki
from utils.submit_editor import open_submit_editor, CoverInfo

from i18n.i18n import _


def get_song_names(song: Song) -> List[str]:
    # FIXME: disable name_other?
    names = [auto_lj(song.name_jap), song.name_chs if song.name_chs != song.name_jap else None, *song.name_other]
    return [name for name in names if not is_empty(name)]


def get_song_categories(song: Song) -> List[str]:
    vocalist_names = song.creators.vocalists_str()
    categories = [engine for engine, characters in ENGINES
                  if any(name in characters for name in vocalist_names)]
    if not categories:
        categories = ["VOCALOID"]
    return categories


def join_engines(categories: List[str]) -> str:
    linked = [f"[[{cat}]]" for cat in categories]
    if len(linked) <= 1:
        return "".join(linked)
    return "、".join(linked[:-1]) + "及" + linked[-1]


def get_cover_filename(song: Song) -> str:
    """Songbox 的 |image 参数，同时也是上传到 Vocawiki 的文件名（两者必须一致）。"""
    return f"{safe_filename(song.name_chs)}.jpg"


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
    video_fields = []
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
    return (start +
            f"{'' if nc == nj else f'（{nc}）'}" +
            f"""是由{join_string(song.creators.producers_str()[:1],
                               inner_wrapper=('[[', ']]'),
                               mapper=auto_lj)}""" +
            videos_to_str2(videos) + f"的{join_engines(categories)}日语原创歌曲，" +
            f"""由{join_string(song.creators.vocalists_str(),
                              outer_wrapper=('[[', ']]'),
                              mapper=name_to_chinese)}演唱。""" +
            tail + "\n")


def create_song(song: Song):
    video_player = ""
    v = get_video(song.videos, VideoSite.BILIBILI)
    if v:
        video_player = f"{{{{" \
                       f"bilibiliVideo|id={v.identifier}" \
                       f"}}}}"
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
    lyrics_chs = lyrics.lyrics_chs
    lyrics_roma = lyrics.lyrics_roma
    chs_exist = lyrics_chs is not None
    if get_config().wikitext.no_hover:
        lyrics_jap = add_no_hover(lyrics.lyrics_jap)
        if chs_exist:
            lyrics_chs = add_no_hover(lyrics_chs)
        lyrics_roma = add_no_hover(lyrics_roma)
    else:
        lyrics_jap = lyrics.lyrics_jap
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
    has_roma = not get_config().wikitext.no_hover and not is_empty(lyrics.lyrics_roma)
    lyrics_template = "/hover" if get_config().wikitext.no_hover else ""
    editing = song.color_editing
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
{style_block}|original=
{assert_str_exists(lyrics_jap).strip()}
|translated=
{lyrics_chs.strip() if chs_exist else ''}
{("|photrans=" + lyrics_roma) if has_roma else ''}}}}}
"""


VOCALOID_TEMPLATES = {
                      '歌爱雪', 
                      'SeeU', 
                      '夏语遥', 
                      '爱莲娜·芙缇', 
                      '艾可', 
                      '赤羽', 
                      '诗岸', 
                      '苍穹', 
                      '海伊',
                      '牧心', 
                      'Minus', 
                      '岸晓', 
                      'Infinity', 
                      '默辰', 
                      '星界'
                    }

vocaloid_template_mapper = {
                      '鸣花': '鸣花姬·尊'
                      }

VOCALOID_TEMPLATES_years = {
                      'v flower': 'Flower',
                      'Ci flower': 'Flower',
                      '重音Teto': '重音Teto',
                      '可不': '可不'
                      }


def get_vocaloid_templates(vocaloids: List[str], year: int = None) -> List[str]:
    result = []
    for v in vocaloids:
        name = vocaloid_names[v] if v in vocaloid_names else v
        if name in VOCALOID_TEMPLATES:
            result.append(name)
        elif name in VOCALOID_TEMPLATES_years:
            template_name = VOCALOID_TEMPLATES_years[name]
            result.append(f"{template_name}/{year}" if year is not None else template_name)
        for key, value in vocaloid_template_mapper.items():
            if key in name:
                result.append(value)
                break
    return result


def get_song_upload_year(song: Song):
    videos = only_canonical_videos(song.videos)
    return min((video.uploaded for video in videos), default=None).year if videos else None


def create_end(song: Song):
    vocaloid_templates = []
    upload_year = get_song_upload_year(song)
    vccl_templates = ""
    if song.vocaloid_collection:
        collection_name = song.vocaloid_collection
        if collection_name.startswith("ボカコレ"):
            collection_name = collection_name[len("ボカコレ"):]
        elif collection_name.startswith("The VOCALOID Collection"):
            collection_name = collection_name[len("The VOCALOID Collection"):].strip()
        vccl_templates = f"{{{{The VOCALOID Collection{collection_name}}}}}\n"
    if get_config().wikitext.producer_template:
        list_templates = asyncio.run(get_producer_info(song.creators.producers))
        vocaloid_templates = get_vocaloid_templates(song.creators.vocalists_str(), upload_year)
        list_templates.extend(vocaloid_templates)
        # FIXME: duplicates reported here
        producer_templates = join_string(list_templates, deliminator="",
                                         outer_wrapper=("{{", "}}\n"))
    else:
        producer_templates = ""
    vocalist_cat = join_string([vocalist for vocalist in song.creators.vocalists_str()
                                if len(get_vocaloid_templates([vocalist])) == 0],
                               deliminator="", mapper=name_to_cat,
                               outer_wrapper=('[[分类:', '歌曲]]\n'))
    if len(vocaloid_templates) == 0:
        engine_cats = set()
        for vocalist in song.creators.vocalists:
            engine_cats.add(get_engine(vocalist.name))
        engine_cat = "".join(f"[[分类:使用{cat}的歌曲]]\n" for cat in engine_cats)
    else:
        engine_cat = ""
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


def main():
    sys.stdout.reconfigure(encoding='utf-8')
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
    if get_config().color.color_editor:
        song.color_editing = open_color_editor(build_initial_color_wiki(song), get_cover_path(song))
    header = create_header(song)
    uploader_note = create_uploader_note(song)
    intro = create_intro(song)
    song_body = create_song(song)
    lyrics = create_lyrics(song)
    end = create_end(song)
    wikitext_dir = get_output_path().joinpath(f"{safe_filename(song.name_chs)}.wikitext")
    content = "\n".join(part for part in [header, uploader_note, intro, song_body, lyrics, end] if part)
    write_to_file(content, wikitext_dir)
    print(_("prog_end"))
    if get_config().wiki.submit_window:
        # 弹出提交窗口：实时预览 / 编辑 / 提交条目与封面；打开失败时回退到 VS Code
        opened = open_submit_editor(page_name=song.name_chs, wikitext=content,
                                    source_path=wikitext_dir, ja_name=song.name_jap,
                                    create_redirect=get_config().wiki.create_redirect,
                                    cover=build_cover_info(song))
        if opened:
            return
    open_output_file(wikitext_dir)


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
    input(_("enter_exit"))
