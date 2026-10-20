from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from models.color import ColorEditing, ColorScheme
from models.creators import Creators, Person
from models.video import HumanOriginal, OtherVersion, Video
from utils.lyrics_editor import open_lyrics_editor, process_translation   # noqa: F401
from utils.string import is_empty


@dataclass
class Lyrics:
    staff: list = field(default_factory=list)
    translator: str = None
    translator_url: str = None
    source_name: str = None
    source_url: str = None
    lyrics_chs: str = None
    lyrics_jap: str = None
    lyrics_roma: str = None
    # 使用 {{LyricsKai/hover}}（悬停显示译文）：由歌词整理窗口的开关决定
    use_hover: bool = False
    # 使用 {{LyricsKai/colors}}（按演唱者给歌词上色）：歌词整理窗口的开关，见 utils/lyrics_colors.py
    use_colors: bool = False
    # 使用 {{LyricsKai/Roma}}（罗马音栏）：歌词整理窗口的开关，勾选了才输出 |photrans=
    use_roma: bool = False
    chara_marks: Optional[Dict[str, List[str]]] = None    # {行下标: [歌姬名…]}，一行可以多个（日语栏）
    chara_marks_chs: Optional[Dict[str, List[str]]] = None
    # ↑ 中文栏**单独**的一套标记（没标过就是 None → 沿用日语栏；两栏行数不一样时要单独标，
    #   界面上的「中文栏」+「按日语标记中文」，见 utils/lyrics_editor.LyricsApi.ai_mark_chs）
    chara_splits: Optional[Dict[str, Dict[str, List[int]]]] = None
    # ↑ {行下标: {栏(jap/chs): [行内切分偏移…]}}：把一行切成几段，每段各选演唱者（见 lyrics_colors.line_cuts）


@dataclass
class Image:
    path: Path
    file_name: str
    source_url: str
    creators: List[Person] = None
    # 文件说明页上挂 `[[分类:作者:…]]` 的那一位：一张图只对应一个作者
    # （见 utils.upload.choose_cover_author）；None = 还没问过，空表 = 不挂作者分类
    cover_authors: Optional[List[Person]] = None


@dataclass
class Song:
    name_jap: str
    name_chs: str
    name_other: List[str]
    creators: Creators
    lyrics: Lyrics
    image: Image
    videos: List[Video] = field(default_factory=list)
    albums: List[str] = field(default_factory=list)
    colors: ColorScheme = None
    vocaloid_collection: str = None
    vocaloid_collection_rank: str = None
    vocaloid_collection_track: str = None
    # 各赛道的 (赛道, 名次)：爬活动模板得到的，可能同时有 TOP100 与 ROOKIE
    # （实测 涅槃(HotaRu)：TOP100 第 70 名 + ROOKIE 第 42 名）；见 utils.family_template
    vocaloid_collection_places: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    color_editing: ColorEditing = None
    # 同名条目（消歧义）处理：page_name 是实际提交用的条目名（无冲突时等于 name_chs）；
    # disambig 是 utils.disambig.Plan（同名条目探测结果与处理步骤）
    page_name: str = None
    disambig: object = None
    # 人声本家（同曲的人声演唱版本）：来自 config.yaml 的 wikitext.human_original，见 models/video.py
    human_original: Optional[HumanOriginal] = None
    # 同一首歌的其他版本（VocaDB 的 alternateVersions + 用户选中/填链接后的结果）：
    # 来自 config.yaml 的 wikitext.other_versions，见 utils/other_versions.py
    other_versions: List[OtherVersion] = field(default_factory=list)


def add_no_hover(lyrics: str) -> str:
    if is_empty(lyrics):
        return lyrics
    return "\n".join("#NoHover" if is_empty(line) else line
                     for line in lyrics.splitlines())


def get_manual_lyrics(initial_text: str = "", use_hover: bool = False,
                      use_colors: bool = False, charas: Sequence[str] = (),
                      jap_text: str = "", use_roma: bool = False,
                      source_hint: str = "", prefill: Optional[dict] = None) -> Lyrics:
    """弹出手动整理歌词窗口（HTML 界面），返回整理好的 Lyrics。

    取消 / 关闭窗口 / pywebview 不可用时返回空 Lyrics（与旧 tkinter 版本行为一致）。
    initial_text 可用于预填待归类歌词；jap_text 预填**日语栏**（流程里已经取到日语原文时，
    直接放进日语栏，用户只要补译文、再用「从链接填充」/「AI 识别并填入」排版）；
    use_hover 为「使用 LyricsKai/hover」开关的初始值；
    use_colors 为「使用 LyricsKai/colors」开关的初始值；charas 是本曲歌姬（供界面做演唱者标记）；
    use_roma 为「使用 LyricsKai/Roma」开关的初始值（勾选了才显示罗马音栏）。
    source_hint 预填「来源链接」；prefill 是**已经取好的**「从链接填充」结果
    （流程在打开窗口前先查了 vocaloid中文歌词wiki，见 `utils.vocadb.prefetch_translation`）。
    """
    return open_lyrics_editor(initial_text, use_hover=use_hover, use_colors=use_colors,
                              charas=charas, jap_text=jap_text, use_roma=use_roma,
                              source_hint=source_hint, prefill=prefill) or Lyrics()
