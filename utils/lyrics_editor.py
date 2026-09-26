"""歌词整理的数据侧：分类 / 切分 / 保存，以及 LyricsApi（界面与终端共用）。

界面已经是 PyQt5 主窗口里的「歌词」页（utils/ui/lyrics_panel.py），
本模块只留纯逻辑与 LyricsApi，便于单测。

    process_lyrics_jap()  日语歌词预处理：整行被多个换行裹住时重新分段（原在 utils/string.py，曾由配置项控制，现固定启用）
    normalize_blank_lines()  把「连续空行」压成一个空行（自动识别前 / 结果里都不会留 2 个以上空行）
    auto()     自动识别：日语栏有内容 -> 以它为准挑中文；否则按脚本分类；再不行按重复段结构猜行号
    ai_auto()  AI 分栏（需 config.yaml 的 wikitext.ai_lyrics 允许 + wiki_credentials.yaml 的 ai_api_key）
    convert()  按「每组几行、取组内第几行」切分
    save()     收集结果（含「使用 LyricsKai/hover」开关）并交回 Lyrics
"""
import json
import logging
import re
from collections import Counter
from itertools import groupby
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from utils import ai_lyrics, lyrics_colors, source_filler
from utils.japanese import is_kana, is_kanji
from utils.string import is_empty

if TYPE_CHECKING:                      # 仅用于类型标注，避免运行时循环导入
    from models.song import Lyrics


# ---------------------------------------------------------------- 纯逻辑

def process_lyrics_jap(lyrics: str) -> str:
    """日语歌词预处理：换行过多时重新分段。

    vocadb 的部分歌词每行之间都夹着好几个换行，直接用会多出大片空白。
    这里统计连续换行的长度：单换行不占多数时判定为「换行过多」，
    按出现最多的那个长度（divider）重新切分——
    短于 divider 的连续换行断成普通换行，长于等于 divider 的当作段落分隔，统一成一个空行。
    空输入返回空串。
    """
    if is_empty(lyrics):
        return ""
    lyrics = lyrics.replace("\r", "")
    groups = [len(list(repeat)) for char, repeat in groupby(lyrics) if char == '\n']
    total = len(groups)
    counter = Counter(groups)
    if counter.get(1, 0) < total / 2:
        logging.info("Too many newlines. Trying to remove them.")
        divider = max(counter.keys(), key=counter.get)
        # newline chars below the divider -> one line; above the divider -> two lines
        sections = re.split("\n" * divider + "\n+", lyrics)
        lyrics = "\n\n".join(["\n".join(re.split("\n+", section)) for section in sections])
    return lyrics


# 连续空行（含只打了空格 / 制表符的「空行」）
BLANK_LINES_RE = re.compile(r"[ \t]*\n(?:[ \t]*\n)+")


def normalize_blank_lines(text: str) -> str:
    """把「连续空行」压成一个空行：段落分隔保留，但不会出现 2 个以上空行。

    与 process_lyrics_jap 的分工：那个处理「每行歌词都被多个换行裹住」的 vocadb 歌词
    （要单换行不占多数才认），段落之间的空行它管不到；用普通歌词（行与行之间就是单个换行）
    时会原样保留 2 个以上空行，于是「自动识别」出来的三栏里空行还是一大片。
    所以自动识别 / 分类 / 提取文字前都先过一遍这里。空行里的空白字符一并忽略，换行统一成 \\n。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return BLANK_LINES_RE.sub("\n\n", text)


def process_translation(translation: str, group_length: int, target_line: int) -> str:
    """按「每组 group_length 行、取组内第 target_line 行」抽取一路歌词。

    空行原样保留（用来分隔段落），最后不足一组的部分直接丢弃。
    """
    index = 0
    result: List[str] = []
    lines: List[str] = translation.split("\n")
    while index < len(lines):
        if is_empty(lines[index]):
            if result and not is_empty(result[-1]):
                result.append("")
            index += 1
        else:
            if index + target_line - 1 >= len(lines):
                break
            result.append(lines[index + target_line - 1])
            index += group_length
    return "\n".join(result)


def classify_stanza(stanza: List[str]) -> Tuple[List[str], List[str], List[str]]:
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


def classify_by_script(text: str) -> Tuple[str, str, str]:
    """逐行识别语言并按段落结构分类。

    返回 (日语, 中文, 罗马音) 三路文本；完全认不出语言时三项都是空串。
    段落之间的连续空行先压成一个（否则每多一个空行就多输出一个，三栏里会留一大片空行）。
    """
    jap_lines: List[str] = []
    chs_lines: List[str] = []
    roma_lines: List[str] = []
    has_content = False
    lines = normalize_blank_lines(text).splitlines()
    i, n = 0, len(lines)
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
        j, c, r = classify_stanza(stanza)
        if j or c or r:
            has_content = True
        jap_lines.extend(j)
        chs_lines.extend(c)
        roma_lines.extend(r)
    if not has_content:
        return "", "", ""
    return ("\n".join(jap_lines).strip(),
            "\n".join(chs_lines).strip(),
            "\n".join(roma_lines).strip())


def extract_chs_by_jap(translation_text: str, jap_text: str) -> str:
    """日语栏已有内容时，以日语歌词为参照，从待归类歌词中提取中文翻译。"""
    if is_empty(jap_text):
        return ""
    jap_lines = {line.strip() for line in jap_text.splitlines() if not is_empty(line)}
    chs_lines: List[str] = []
    for line in normalize_blank_lines(translation_text).splitlines():
        if is_empty(line):
            chs_lines.append("")
        elif line.strip() not in jap_lines:
            chs_lines.append(line)
    return "\n".join(chs_lines).strip()


def guess_layout(text: str) -> Optional[Dict[str, str]]:
    """按重复段结构推测「每组行数」与第一组里各语言所在行号。

    推不出来时返回 None（对应原来「自动」按钮失败的提示）。
    """
    groups = [len(list(repeat)) for char, repeat in groupby(text) if char == "\n"]
    possibilities = list(Counter(groups).keys())
    sections: object = text
    while len(possibilities) > 0:
        sections = sections.split("\n" * possibilities[-1])
        widths = {len(section.split("\n")) for section in sections}
        if len(widths) == 1:                       # 每段行数一致 -> 认定这个空行数
            break
        sections = sections[0].strip()
        possibilities.pop()
    if not possibilities or not isinstance(sections, list):
        return None
    group_length = len(sections[0].split("\n")) + 1
    layout = {"group_length": str(group_length), "jap_line": "", "chs_line": "", "roma_line": ""}
    for line_number, line in enumerate(text.split("\n")[:group_length]):
        if any(is_kana(c) for c in line):
            layout["jap_line"] = str(line_number + 1)
        if any(is_kanji(c) for c in line) and all(not is_kana(c) for c in line):
            layout["chs_line"] = str(line_number + 1)
        if not is_empty(line) and all(c.isascii() for c in line):
            layout["roma_line"] = str(line_number + 1)
    return layout


# ---------------------------------------------------------------- pywebview 接口

def _load_payload(payload_json: str) -> Optional[dict]:
    try:
        data = json.loads(payload_json or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class LyricsApi:
    """暴露给前端 JS 的接口：自动识别 / 转换 / 保存 / 取消。"""

    def __init__(self, initial_text: str = "", source_hint: str = "", use_hover: bool = False,
                 use_colors: bool = False, charas: Sequence[str] = ()):
        self._initial_text = initial_text or ""
        self._source_hint = source_hint or ""
        self._use_hover = bool(use_hover)
        self._use_colors = bool(use_colors)
        self._charas = [str(name) for name in (charas or []) if not is_empty(str(name))]
        self.result: Optional["Lyrics"] = None
        self._window = None

    def chara_options(self) -> List[dict]:
        """歌姬 + 颜色（颜色来自 voca.wiki 的 Module:Vocalist_Colors，取不到就是默认色）。"""
        try:
            table = lyrics_colors.fetch_colors()
        except Exception as e:                  # 断网也要能用，颜色全是默认色
            logging.warning("获取歌姬颜色失败：%s", e)
            table = {}
        return [{"name": name, "color": lyrics_colors.color_of(name, table)}
                for name in self._charas]

    def get_context(self) -> dict:
        """窗口初始内容（供宿主注入）。"""
        return {"initial": self._initial_text, "sourceHint": self._source_hint,
                "useHover": self._use_hover, "useColors": self._use_colors,
                "charas": self.chara_options(), "aiLyrics": ai_lyrics.context()}

    def ai_auto(self, payload_json: str) -> dict:
        """AI 分栏：把混在一起的歌词交给大模型分日语 / 中文 / 罗马音。

        是否允许由 config.yaml 的 wikitext.ai_lyrics 决定（关闭时直接返回错误，不联网）。
        """
        return ai_lyrics.recognize(payload_json)

    def auto(self, payload_json: str) -> dict:
        """自动识别：日语栏有内容就先按它挑中文，否则按脚本分类，再不行猜行号。"""
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        text = normalize_blank_lines(str(data.get("text") or ""))
        if is_empty(text):
            return {"ok": False, "error": "请先在左边粘贴歌词"}

        jap = str(data.get("jap") or "")
        if not is_empty(jap):
            chs = extract_chs_by_jap(text, jap)
            if not is_empty(chs):
                return {"ok": True, "mode": "extract", "jap": normalize_blank_lines(jap), "chs": chs,
                        "roma": normalize_blank_lines(str(data.get("roma") or "")),
                        "message": "已以日语栏为参照挑出中文行"}

        classified_jap, classified_chs, classified_roma = classify_by_script(text)
        if classified_jap or classified_chs or classified_roma:
            return {"ok": True, "mode": "classify",
                    "jap": classified_jap, "chs": classified_chs, "roma": classified_roma,
                    "message": "已按语言自动分类"}

        layout = guess_layout(text)
        if not layout:
            return {"ok": False, "error": "自动识别失败：请手动填写每组行数与行号后点「按行号转换」"}
        return {"ok": True, "mode": "lines", "layout": layout,
                "message": "已推测出每组行数与行号，确认后点「按行号转换」"}

    def convert(self, payload_json: str) -> dict:
        """按「每组几行、取组内第几行」把待归类歌词切成三路。"""
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        text = str(data.get("text") or "")
        if is_empty(text):
            return {"ok": False, "error": "请先在左边粘贴歌词"}
        try:
            group_length = int(str(data.get("groupLength") or "").strip())
        except ValueError:
            return {"ok": False, "error": "每组行数要填一个整数"}

        def pick(key: str) -> str:
            raw = str(data.get(key) or "").strip()
            if is_empty(raw):
                return ""
            try:
                return process_translation(text, group_length, int(raw))
            except ValueError:
                return ""

        return {"ok": True, "jap": pick("japLine"), "chs": pick("chsLine"), "roma": pick("romaLine"),
                "message": "已按行号切分"}

    def save(self, payload_json: str) -> dict:
        """收集三路歌词与来源信息，关窗并返回给 Python。"""
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        jap = str(data.get("jap") or "").strip()
        chs = str(data.get("chs") or "").strip()
        roma = str(data.get("roma") or "").strip()
        if is_empty(jap) and is_empty(chs):
            return {"ok": False, "error": "日语与中文歌词都是空的，先点「自动识别并填入」或手动填写"}

        from models.song import Lyrics        # 延迟导入，避免与本模块的调用方循环依赖
        marks = data.get("charaMarks") or {}
        chara_marks = {}
        for line, segments in marks.items():
            if not isinstance(segments, list) or not segments:
                continue
            if all(isinstance(item, str) for item in segments):
                kept = [name for name in segments if name]      # 整行一段（不分段的老写法）
            else:
                kept = [[str(name) for name in seg if name]
                        for seg in segments if isinstance(seg, list)]
                while kept and not kept[-1]:                    # 结尾的空段没意义
                    kept.pop()
            if any(kept):
                chara_marks[str(line)] = kept

        splits = data.get("charaSplits") or {}
        chara_splits = {}
        for line, tracks in splits.items():
            if not isinstance(tracks, dict):
                continue
            kept = {}
            for track, cuts in tracks.items():
                offsets = []
                for cut in cuts if isinstance(cuts, list) else []:
                    try:
                        offsets.append(int(cut))
                    except (TypeError, ValueError):
                        continue        # 界面上给的都是整数，这里只防手改的脏数据
                if offsets:
                    kept[str(track)] = offsets
            if kept:
                chara_splits[str(line)] = kept
        self.result = Lyrics(
            translator=str(data.get("translator") or "").strip(),
            translator_url=str(data.get("translatorUrl") or "").strip(),
            source_name=str(data.get("sourceName") or "").strip(),
            source_url=str(data.get("sourceUrl") or "").strip(),
            lyrics_jap=jap,
            lyrics_chs=chs,
            lyrics_roma=roma,
            use_hover=bool(data.get("useHover")),
            use_colors=bool(data.get("useColors")),
            chara_marks=chara_marks or None,
            chara_splits=chara_splits or None,
        )
        self._destroy()
        return {"ok": True, "message": "已保存歌词"}

    def cancel(self) -> dict:
        """放弃编辑（窗口直接关闭）。"""
        self.result = None
        self._destroy()
        return {"ok": True}

    def fill_source(self, url: str) -> dict:
        """按「来源链接」自动识别翻译者 / 翻译链接 / 来源（见 utils/source_filler.py）。"""
        return source_filler.fill_source(url)

    def _destroy(self):
        window = self._window
        self._window = None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------- 打开窗口

def open_lyrics_editor(initial_text: str = "", source_hint: str = "",
                       use_hover: bool = False, use_colors: bool = False,
                       charas: Sequence[str] = ()) -> Optional["Lyrics"]:
    """打开主窗口里的「歌词」页，返回用户确认的 Lyrics；取消 / 界面不可用时返回 None。

    use_hover 为「使用 LyricsKai/hover」开关的初始状态（也是窗口关闭、用户未改时的兼容传参）。
    use_colors 为「使用 LyricsKai/colors」开关的初始状态；charas 是本曲歌姬（界面上给每行标演唱者用）。
    """
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，无法打开歌词整理页。")
        return None
    return ui.open_lyrics_editor(initial_text, source_hint, use_hover, use_colors, charas)
