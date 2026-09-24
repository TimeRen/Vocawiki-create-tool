import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import List, Dict

from i18n.i18n import _
from models.color import ColorScheme
from models.creators import Creators, Person
from models.video import Video
from utils.helpers import prompt_response, prompt_multiline, prompt_choices, prompt_number
from utils.japanese import is_kana, is_kanji
from utils.string import is_empty

import tkinter as tk


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
    color_wiki: str = None


def process_translation(translation: str, group_length: int, target_line: int) -> str:
    index = 0
    result = []
    lines: List[str] = translation.split("\n")
    while index < len(lines):
        if is_empty(lines[index]):
            if len(lines) > 0 and not is_empty(result[-1]):
                result.append("")
            index += 1
        else:
            if index + target_line - 1 >= len(lines):
                break
            result.append(lines[index + target_line - 1])
            index += group_length
    return "\n".join(result)


def add_no_hover(lyrics: str) -> str:
    if is_empty(lyrics):
        return lyrics
    return "\n".join("#NoHover" if is_empty(line) else line
                     for line in lyrics.splitlines())


def get_text(t: tk.Text) -> str:
    return t.get("1.0", tk.END).strip()


def _classify_stanza(stanza: List[str]):
    """对一个不含空行的歌词段落分类，返回 (日语行列表, 中文行列表, 罗马音行列表)。

    含假名的行=日语、纯ASCII=罗马音；纯汉字无假名的行按段落内结构判定：
    - 块状格式（日语行与汉字行互不交错）按前后位置切分；
    - 交替格式按日语行所在固定周期（2 或 3 行一组）判定。
    """
    tagged = []
    for line in stanza:
        stripped = line.strip()
        if any(is_kana(c) for c in stripped):
            tagged.append(('jap', line))
        elif all(c.isascii() for c in stripped):
            tagged.append(('roma', line))
        elif any(is_kanji(c) for c in stripped):
            tagged.append(('amb', line))
        else:
            tagged.append(('other', line))

    jap_pos = [i for i, (k, _) in enumerate(tagged) if k == 'jap']
    amb_pos = [i for i, (k, _) in enumerate(tagged) if k == 'amb']
    roma_pos = [i for i, (k, _) in enumerate(tagged) if k == 'roma']

    # 块状格式：日语行与汉字行互不交错（日语块在前 / 汉字块在前）
    block = None
    if jap_pos and amb_pos:
        if max(jap_pos) < min(amb_pos):
            block = 'jap_first'
        elif max(amb_pos) < min(jap_pos):
            block = 'chs_first'

    # 交替格式：日语行位置满足固定周期（有罗马音时优先按 3 行一组判断）
    period = None
    jap_phase = None
    if jap_pos and block is None:
        candidate_periods = (3, 2) if roma_pos else (2, 3)
        for p in candidate_periods:
            phases = {pos % p for pos in jap_pos}
            if len(phases) == 1:
                period = p
                jap_phase = phases.pop()
                break

    jap_out, chs_out, roma_out = [], [], []
    for i, (kind, line) in enumerate(tagged):
        if kind == 'jap':
            jap_out.append(line)
        elif kind == 'roma':
            roma_out.append(line)
        elif kind == 'amb':
            if block == 'jap_first':
                chs_out.append(line)
            elif block == 'chs_first':
                jap_out.append(line)
            elif period is not None and i % period == jap_phase:
                jap_out.append(line)
            else:
                chs_out.append(line)
        # 'other'（纯标点等）忽略
    return jap_out, chs_out, roma_out


def get_manual_lyrics() -> Lyrics:
    root = tk.Tk("LyricsSelector")
    TRANSLATION_ROW_SPAN = 6
    translation = tk.Text(root, height=40, width=80)
    translation.grid(row=0, rowspan=TRANSLATION_ROW_SPAN)
    input_fields = tk.PanedWindow(root)
    entry_names = ['group_length', 'jap_line', 'chs_line', 'roma_line', 'translator', 'translator_url',
                   'source_name', 'source_url']
    entry_labels = {
        'group_length': _("group_length"),
        'jap_line': _("jap_line"),
        'chs_line': _("chs_line"),
        'roma_line': _("roma_line"),
        'translator': _("translator"),
        'translator_url': _("translator_url"),
        'source_name': _("source_name"),
        'source_url': _("source_url"),
    }
    entries: Dict[str, tk.StringVar] = {}
    for index, entry_name in enumerate(entry_names):
        tk.Label(input_fields, text=entry_labels[entry_name]).grid(row=index + TRANSLATION_ROW_SPAN)
        v = tk.StringVar()
        e = tk.Entry(input_fields, textvariable=v)
        e.grid(row=index + TRANSLATION_ROW_SPAN, column=1)
        entries[entry_name] = v
    input_fields.grid(row=TRANSLATION_ROW_SPAN)
    jap_label = tk.Label(root, text=_("jap"))
    jap = tk.Text(root, height=10)
    chs_label = tk.Label(root, text=_("chs"))
    chs = tk.Text(root, height=10)
    roma_label = tk.Label(root, text=_("roma"))
    roma = tk.Text(root, height=10)
    buttons = tk.PanedWindow(root)

    def classify_by_script(text: str) -> bool:
        """逐行识别语言并按段落结构分类，分别填入日语/中文/罗马音栏。"""
        jap_lines = []
        chs_lines = []
        roma_lines = []
        has_content = False
        lines = text.splitlines()
        i = 0
        n = len(lines)
        while i < n:
            if is_empty(lines[i]):
                jap_lines.append("")
                chs_lines.append("")
                roma_lines.append("")
                i += 1
                continue
            stanza = []
            while i < n and not is_empty(lines[i]):
                stanza.append(lines[i])
                i += 1
            j, c, r = _classify_stanza(stanza)
            if j or c or r:
                has_content = True
            jap_lines.extend(j)
            chs_lines.extend(c)
            roma_lines.extend(r)
        if not has_content:
            return False
        jap.replace("1.0", tk.END, "\n".join(jap_lines).strip())
        chs.replace("1.0", tk.END, "\n".join(chs_lines).strip())
        roma.replace("1.0", tk.END, "\n".join(roma_lines).strip())
        return True

    def extract_chs_by_jap(translation_text: str) -> bool:
        """日语歌词栏已有内容时，以日语歌词为参照，从待归类歌词中提取中文翻译。"""
        jap_text = get_text(jap)
        if is_empty(jap_text):
            return False
        jap_lines = {line.strip() for line in jap_text.splitlines() if not is_empty(line)}
        chs_lines = []
        for line in translation_text.splitlines():
            if is_empty(line):
                chs_lines.append("")
            elif line.strip() not in jap_lines:
                chs_lines.append(line)
        chs.replace("1.0", tk.END, "\n".join(chs_lines).strip())
        return True

    def auto_line_numbers():
        translation_text: str = get_text(translation)
        if extract_chs_by_jap(translation_text):
            return
        if classify_by_script(translation_text):
            return
        groups = [len(list(repeat)) for char, repeat in groupby(translation_text) if char == '\n']
        possibilities = list(Counter(groups).keys())
        text = translation_text
        while len(possibilities) > 0:
            text = text.split("\n" * possibilities[-1])
            for section in text:
                if len(section.split("\n")) != len(text[0].split("\n")):
                    break
            else:
                break
            text = text[0].strip()
            possibilities.pop()
        if len(possibilities) == 0:
            print("Failed...")
            return
        group_length = len(text[0].split("\n")) + 1
        entries['group_length'].set(str(group_length))
        section1 = translation_text.split("\n")[0:group_length]
        for line_number, line in enumerate(section1):
            if any([is_kana(c) for c in line]):
                entries['jap_line'].set(str(line_number + 1))
            if any([is_kanji(c) for c in line]) and all([not is_kana(c) for c in line]):
                entries['chs_line'].set(str(line_number + 1))
            if all([c.isascii() for c in line]) and not is_empty(line):
                entries['roma_line'].set(str(line_number + 1))
        convert_translation()

    auto = tk.Button(buttons, text=_("auto"), command=auto_line_numbers)

    def convert_translation():
        translation_text: str = translation.get("1.0", tk.END).strip()
        chs_text = entries['chs_line'].get()
        jap_text = entries['jap_line'].get()
        roma_text = entries['roma_line'].get()
        try:
            group_length = int(entries['group_length'].get())
            if not is_empty(chs_text):
                chs_line = int(chs_text)
                chs.replace("1.0", tk.END, process_translation(translation_text, group_length, chs_line))
            if not is_empty(jap_text):
                jap_line = int(jap_text)
                jap.replace("1.0", tk.END, process_translation(translation_text, group_length, jap_line))
            if not is_empty(roma_text):
                roma_line = int(roma_text)
                roma.replace("1.0", tk.END, process_translation(translation_text, group_length, roma_line))
        except Exception as e:
            print(e)
            print(f"Chs: {chs_text}\nJap: {jap_text}\nRoma: {roma_text}")

    convert = tk.Button(buttons, text=_("convert"), command=convert_translation)

    result = Lyrics()

    def destroy():
        nonlocal result
        result = Lyrics(translator=entries['translator'].get(), translator_url=entries['translator_url'].get(),
                source_name=entries['source_name'].get(),
                        source_url=entries['source_url'].get(),
                        lyrics_chs=get_text(chs), lyrics_jap=get_text(jap), lyrics_roma=get_text(roma))
        root.destroy()

    confirm = tk.Button(buttons, text=_("done"), command=destroy)
    col_2_widgets: List = [jap_label, jap, chs_label, chs, roma_label, roma, buttons, auto, convert, confirm]
    for index, w in enumerate(col_2_widgets):
        w.grid(row=index, column=2)
    root.mainloop()
    return result
