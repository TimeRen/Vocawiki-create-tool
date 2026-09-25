from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from models.color import ColorEditing, ColorScheme
from models.creators import Creators, Person
from models.video import Video
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


def add_no_hover(lyrics: str) -> str:
    if is_empty(lyrics):
        return lyrics
    return "\n".join("#NoHover" if is_empty(line) else line
                     for line in lyrics.splitlines())


def get_manual_lyrics(initial_text: str = "") -> Lyrics:
    """弹出手动整理歌词窗口（HTML 界面），返回整理好的 Lyrics。

    取消 / 关闭窗口 / pywebview 不可用时返回空 Lyrics（与旧 tkinter 版本行为一致）。
    initial_text 可用于预填待归类歌词。
    """
    return open_lyrics_editor(initial_text) or Lyrics()
