import asyncio
import logging
import os
import shutil
import subprocess
import sys
import traceback
import webbrowser
from typing import List

from config import data
from config.config import load_config, get_config, application_path, get_output_path
from models.creators import person_list_to_str, Staff, role_priority
from models.song import Song, Lyrics, add_no_hover
from models.video import VideoSite, Video, view_count_from_site, get_video, only_canonical_videos
from utils import login
from utils.helpers import prompt_choices, prompt_response, prompt_multiline
from utils.image import write_to_file
from utils.voca import get_producer_info
from utils.name_converter import name_to_cat, name_to_chinese, vocaloid_names, UTAU_CHARACTERS, CEVIO_CHARACTERS
from utils.save_input import setup_save_input
from utils.string import auto_lj, is_empty, datetime_to_ymd, assert_str_exists, join_string, safe_filename
from utils.upload import upload_image
from utils.vocadb import get_song_by_name

from i18n.i18n import _


def get_song_names(song: Song) -> List[str]:
    # FIXME: disable name_other?
    names = [auto_lj(song.name_jap), song.name_chs if song.name_chs != song.name_jap else None, *song.name_other]
    return [name for name in names if not is_empty(name)]


def get_song_category(song: Song) -> str:
    vocalist_names = song.creators.vocalists_str()
    if any(name in UTAU_CHARACTERS for name in vocalist_names):
        return "UTAU"
    if any(name in CEVIO_CHARACTERS for name in vocalist_names):
        return "CeVIO"
    return "VOCALOID"


def create_header(song: Song) -> str:
    videos = sorted(song.videos, key=lambda v: v.uploaded)
    cat = get_song_category(song)
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
        top = "{{虚拟歌手歌曲荣誉题头|" + cat + "|" + "|".join(rank_fields) + "}}\n"
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
    return f"""{top}{{{{VOCALOID_Songbox
|image    = {song.name_chs}.jpg
{image_info_field}|颜色    = {f"{song.colors.background.to_hex()};color:{song.colors.text.to_hex()}" if song.colors else ''}
|演唱    = {join_string(song.creators.vocalists_str(), outer_wrapper=("[[", "]]"),
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
    cat = get_song_category(song)
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
            videos_to_str2(videos) + f"的[[{cat}]]日语原创歌曲，" +
            f"""由{join_string(song.creators.vocalists_str(),
                              outer_wrapper=('[[', ']]'),
                              mapper=name_to_chinese)}演唱。""" +
            tail)


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
    if song.colors:
        color_scheme = song.colors
        color = f"|lbgcolor = {color_scheme.background.to_hex()}{introduction_color_style}\n" \
                f"|ltcolor = {color_scheme.text.to_hex()}{introduction_text_style}\n"
    else:
        color = f"|lbgcolor = #000{introduction_color_style}\n" \
                f"|ltcolor = white{introduction_text_style}\n"
    return (f"== 歌曲 ==\n"
            "{{VOCALOID Songbox Introduction\n"
            + color +
            f"{''.join(groups)}"
            f"}}}}\n\n{video_player}")


def create_lyrics(lyrics: Lyrics):
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
    return f"""== 歌词 ==
{translation_notice}
{"{{LyricsKai/Roma/button}}" if has_roma else ""}
{{{{LyricsKai{lyrics_template}{'/Roma' if has_roma else ''}
|lstyle=color:;
|rstyle=color:;
|containerstyle=background:;
|original=
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
            if vocalist.name in UTAU_CHARACTERS:
                engine_cats.add("UTAU")
            elif vocalist.name in CEVIO_CHARACTERS:
                engine_cats.add("CeVIO")
            else:
                engine_cats.add("VOCALOID")
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


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    setup_logger()
    load_config(application_path.joinpath("config.yaml"))
    setup_save_input(get_config().save_to_file)
    if get_config().image.auto_upload:
        login.main()
    data.name_japanese = prompt_response(_("name_original"))
    name_chinese = prompt_response(_("name_trans"))
    if is_empty(name_chinese):
        name_chinese = data.name_japanese
    song = get_song_by_name(data.name_japanese, name_chinese)
    if not song:
        raise NotImplementedError(_("only_vocadb"))
    header = create_header(song)
    uploader_note = create_uploader_note(song)
    intro = create_intro(song)
    song_body = create_song(song)
    lyrics = create_lyrics(song.lyrics)
    end = create_end(song)
    wikitext_dir = get_output_path().joinpath(f"{safe_filename(song.name_chs)}.wikitext")
    write_to_file("\n".join([header, uploader_note, intro, song_body, lyrics, end]),
                  wikitext_dir)
    if song.image.path and get_config().image.auto_upload:
        response = prompt_choices("Upload image to commons?", ["Yes", "No"])
        if response == 1:
            image = song.image
            upload_image(image.path, filename=image.file_name, song_name=name_chinese,
                         authors=image.creators, source_url=image.source_url)
    print(_("prog_end"))
    code_command = shutil.which("code") or shutil.which("code.cmd")
    if code_command:
        try:
            subprocess.Popen([code_command, "--reuse-window", str(wikitext_dir.absolute())])
        except OSError:
            logging.warning("Unable to open the output file with VS Code. Falling back to the default browser.")
            webbrowser.open("file://" + str(wikitext_dir.absolute()))
    else:
        logging.warning("VS Code command 'code' was not found. Falling back to the default browser.")
        webbrowser.open("file://" + str(wikitext_dir.absolute()))


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
