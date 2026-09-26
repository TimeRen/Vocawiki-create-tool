from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from models.color import ColorEditing, ColorScheme
from models.creators import Creators, Person
from models.video import HumanOriginal, Video
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
    chara_marks: Optional[Dict[str, List[str]]] = None    # {行下标: [歌姬名…]}，一行可以多个
    chara_splits: Optional[Dict[str, Dict[str, List[int]]]] = None
    # ↑ {行下标: {栏(jap/chs): [行内切分偏移…]}}：把一行切成几段，每段各选演唱者（见 lyrics_colors.line_cuts）


@dataclass
class Image:
    path: Path
    file_name: str
    source_url: str
    creators: List[Person] = None


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
    color_editing: ColorEditing = None
    # 同名条目（消歧义）处理：page_name 是实际提交用的条目名（无冲突时等于 name_chs）；
    # disambig 是 utils.disambig.Plan（同名条目探测结果与处理步骤）
    page_name: str = None
    disambig: object = None
    # 人声本家（同曲的人声演唱版本）：来自 config.yaml 的 wikitext.human_original，见 models/video.py
    human_original: Optional[HumanOriginal] = None


def add_no_hover(lyrics: str) -> str:
    if is_empty(lyrics):
        return lyrics
    return "\n".join("#NoHover" if is_empty(line) else line
                     for line in lyrics.splitlines())


def get_manual_lyrics(initial_text: str = "", use_hover: bool = False,
                      use_colors: bool = False, charas: Sequence[str] = ()) -> Lyrics:
    """弹出手动整理歌词窗口（HTML 界面），返回整理好的 Lyrics。

    取消 / 关闭窗口 / pywebview 不可用时返回空 Lyrics（与旧 tkinter 版本行为一致）。
    initial_text 可用于预填待归类歌词；use_hover 为「使用 LyricsKai/hover」开关的初始值；
    use_colors 为「使用 LyricsKai/colors」开关的初始值；charas 是本曲歌姬（供界面做演唱者标记）。
    """
    return open_lyrics_editor(initial_text, use_hover=use_hover, use_colors=use_colors,
                              charas=charas) or Lyrics()
