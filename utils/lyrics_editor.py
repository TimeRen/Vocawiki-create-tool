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
import difflib
import json
import logging
import re
from collections import Counter
from itertools import groupby
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from utils import ai_lyrics, japanese, lyrics_colors, source_filler
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


def with_furigana(jap: str) -> str:
    """「歌词括号里的假名」→ `{{photrans|汉字|读音}}`。

    设置页里已经没这个开关了：它现在是「自动识别并填入 / AI 识别并填入」的固定一步，
    这样手动粘进歌词页、或歌词来自别的地方时也能一并转好。
    （生成阶段还会先转一遍，见 utils/vocadb.py，那一路保留了配置项，终端模式也能用。）
    """
    return japanese.furigana_local(jap or "")


def normalize_blank_lines(text: str) -> str:
    """把「连续空行」压成一个空行：段落分隔保留，但不会出现 2 个以上空行。

    与 process_lyrics_jap 的分工：那个处理「每行歌词都被多个换行裹住」的 vocadb 歌词
    （要单换行不占多数才认），段落之间的空行它管不到；用普通歌词（行与行之间就是单个换行）
    时会原样保留 2 个以上空行，于是「自动识别」出来的三栏里空行还是一大片。
    所以自动识别 / 分类 / 提取文字前都先过一遍这里。空行里的空白字符一并忽略，换行统一成 \\n。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return BLANK_LINES_RE.sub("\n\n", text)


# 逐行比对时**不参与**的标点：模型常「顺手」把 、 补上或换一种写法的标点，不该因此对不上
_IGNORED_CHARS_RE = re.compile(r"[、。，．,\.!！?？…・;；:：\-—–―「」『』（）()\[\]【】~～\"'“”‘’]")


def _match_key(line: str) -> str:
    """两栏逐行比对用的「行指纹」：还原注音、忽略空白与标点。

    日语栏过完 `with_furigana()` 之后，汉字被包进 `{{photrans|漢字|かんじ}}`，
    待归类歌词里还是裸汉字；来源里的日语行又有「長音符写成 一」这类写法差异，
    直接逐字比对会一行都对不上，中文栏便从那一行起整栏错位
    （用户 2026-10-07 报的「AI 排版没跟着日语栏走」就是这么来的）。
    """
    text = ai_lyrics.PHOTRANS_RE.sub(lambda match: match.group(1), line or "")
    text = ai_lyrics.PHOTRANS_RE.sub(lambda match: match.group(1), japanese.furigana_local(text))
    text = _IGNORED_CHARS_RE.sub("", text.replace("ー", "一"))
    text = re.sub(r"[ \t\u3000]+", "", text)
    return text or line.strip()            # 整行都是标点：退回原样比对


def _similar(left: str, right: str) -> bool:
    """两行像不像同一句：只在精确比对失败时用来认领被改写过的日语行。"""
    return difflib.SequenceMatcher(None, left, right).ratio() >= 0.6


def align_blank_lines(text: str, reference: str) -> str:
    """让 `text`（中文 / 罗马音栏）的**分段空行位置**跟着 `reference`（日语栏）。

    用户 2026-10-03：「用 AI 识别歌词时，待识别歌词里的中文歌词并没有改成和我已输入的日文
    歌词一样的格式」—— 日语栏是「每行一句 + 段落之间一个空行」，AI 给的中文栏却是一整块
    （空行位对不上）。这里拿日语栏当模板：它空行的地方中文栏也空行，非空行按**原序**一个个
    填进去（中文多出来的行排在后面，不会丢）。

    两边都先过 `normalize_blank_lines()`（连续空行只留一个），换行统一成 `\n`。
    """
    source = normalize_blank_lines(reference).strip("\n").split("\n")
    lines = [line for line in normalize_blank_lines(text).strip("\n").split("\n")]
    if not source or not [line for line in lines if not is_empty(line)]:
        return "\n".join(lines).strip()
    pending = iter([line.strip() for line in lines if not is_empty(line)])
    result: List[str] = []
    for line in source:
        if is_empty(line):
            result.append("")                     # 日语栏这里空行 → 中文栏也跟着空行
            continue
        value = next(pending, "")
        if not is_empty(value):
            result.append(value)
    result += [line for line in pending]           # 多出来的中文行（译者加句）留在最后
    return "\n".join(result).strip()


def is_english_line(line: str) -> bool:
    """这一行是不是「英文歌词」（有拉丁字母、且不含假名 / 汉字 / 中文）。

    中文标点 + 英文单词算英文；只有标点（`——`）或纯数字的行走不上这条路。
    日语行不会含罗马音：`kimi no na wa` 这种**整行都是拉丁字母**的行同样是「英文字符」，
    所以判断结果要靠上下文（它在 jap 栏里才代表歌里的英文），见 `mirror_english_lines()`。
    """
    chars = [c for c in (line or "").strip() if not c.isspace()]
    if not chars:
        return False
    if any(is_kana(c) or is_kanji(c) for c in chars):
        return False
    return any(c.isascii() and c.isalpha() for c in chars)


def mirror_english_lines(chs: str, jap: str) -> str:
    """把日语栏里的英文行补进中文栏的同一位置。

    用户 2026-10-05 报：「用 AI 识别歌词时，中文歌词并不会包含日文歌词里的英文」。
    歌里本来就唱的英文（`Fly away` 这种）模型有时只放进日语栏，中文栏直接缺一行 ——
    两栏行数错位，标记、对齐、段落全都跟着错。这里以日语栏为骨架补一遍：
    日语栏的英文行在中文栏里没有对应内容时**原样补一份**（中文栏保留英文），
    已经有（模型照抄过）就用中文栏的那一行，一个字都不动。

    ⚠️ 只补不删：模型若把英文翻成了中文，那一行中文会留着（多一行给人核对，
    总比默默丢掉英文好）。日语栏没有英文行时原样返回，不影响既有输出。
    """
    jap_lines = normalize_blank_lines(jap).strip("\n").split("\n") if jap else []
    if not [line for line in jap_lines if is_english_line(line)]:
        return chs
    chs_lines = normalize_blank_lines(chs).strip("\n").split("\n") if chs else []
    result: List[str] = []
    index = 0
    for line in jap_lines:
        if is_empty(line):
            result.append("")
            continue
        if is_english_line(line):
            value = chs_lines[index] if index < len(chs_lines) else ""
            if value.strip() == line.strip():
                result.append(value)
                index += 1
            else:
                result.append(line.strip())
            continue
        if index < len(chs_lines):
            result.append(chs_lines[index])
            index += 1
    result += chs_lines[index:]                    # 多出来的中文行（译者加句）留在最后
    return "\n".join(result).strip()


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
    jap_lines = {_match_key(line) for line in jap_text.splitlines() if not is_empty(line)}
    chs_lines: List[str] = []
    for line in normalize_blank_lines(translation_text).splitlines():
        if is_empty(line):
            chs_lines.append("")
        elif _match_key(line) not in jap_lines:
            chs_lines.append(line)
    return "\n".join(chs_lines).strip()


def is_japanese_line(line: str) -> bool:
    """这一行像不像日语原文（含假名）。"""
    return any(is_kana(c) for c in (line or "").strip())


def pair_chs_with_jap(text: str, jap: str) -> Optional[str]:
    """日文一行、中文一行交替的「待归类歌词」→ 按**相邻关系**配对出中文栏。

    用户 2026-10-05 报：b 站动态粘过来的日/中交替歌词（开头还有一句「尝试着翻译了一下…」），
    生成的**中文栏没有跟着日语栏的排版走**，整栏从第一行起就顶开了一格，后面每一句中文
    都跑到日语前一句的位置上。原因是按「来源里的顺序」往日语栏的格子里填，
    遇上一句没有对应日文的说明文字（或译注）就把后面全部顶开了。

    这里换成分行**配对**：拿日语栏每一行去来源里找到它自己，紧跟其后那一行就是它的译文
    （前提：它本身不是日文行）；找不到对应日文行的来源行（说明 / 译者注）不算歌词，
    统一排到中文栏**最后**，不会再顶开后面的内容。结果与日语栏**同构**（空行位置一致）。

    来源里的日语行跟日语栏对不上（比如日语栏是另行整理的）时返回 None，调用方退回原来的做法。
    比对用 `_match_key()`（忽略注音模板 / 空白 / 标点，「ー」「一」视为同一个字），
    个别行被改写过时按相似度就近认领，免得一行对不上就把后面每一句中文都顶开。
    """
    jap_lines = normalize_blank_lines(jap).strip("\n").split("\n") if str(jap or "").strip() else []
    if not [line for line in jap_lines if not is_empty(line)]:
        return None
    source = normalize_blank_lines(text).split("\n")
    used = [False] * len(source)
    result: List[str] = []
    index = 0
    pairs = 0
    for jap_line in jap_lines:
        want = _match_key(jap_line)
        if not want:
            result.append("")
            continue
        found = next((pos for pos in range(index, len(source))
                      if not used[pos] and _match_key(source[pos]) == want), None)
        if found is None:
            # 这一句被改写过（换了用词 / 长音符 / 标点），来源里却还留着原句：
            # 就近认领「长得像」的那一行，译文照旧取它后面那行，免得从这里起整栏错位
            found = next((pos for pos in range(index, len(source))
                          if not used[pos] and is_japanese_line(source[pos])
                          and _similar(_match_key(source[pos]), want)), None)
        if found is None:
            result.append("")
            continue
        used[found] = True
        index = found + 1
        value = ""
        if index < len(source) and not used[index]:
            candidate = source[index].strip()
            # 紧跟其后那一行：非空、不是日文原文、也不是另一句日文原文的重复
            if candidate and not is_japanese_line(candidate):
                value = candidate
                used[index] = True
                index += 1
                pairs += 1
        result.append(value)
    wanted = len([line for line in jap_lines if not is_empty(line)])
    if pairs < max(2, wanted // 3):            # 对上的太少 → 这来源跟日语栏没关系
        return None
    result += [line.strip() for pos, line in enumerate(source) if not used[pos] and line.strip()]
    return "\n".join(result).strip()


def align_chs_to_jap(text: str, jap: str, chs: str) -> str:
    """中文栏对齐到日语栏：能用**相邻配对**就用（见 `pair_chs_with_jap`），否则按顺序填。

    两种做法都保证「多出来的中文行排在最后、不丢」，区别只在「哪一行算哪一行的译文」。
    """
    paired = pair_chs_with_jap(text, jap)
    if paired is not None:
        return mirror_english_lines(paired, jap)
    return align_blank_lines(mirror_english_lines(chs, jap), jap)


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


def _clean_marks(marks) -> Dict[str, Any]:
    """洗一遍界面上报上来的「每行谁唱」：`{行: [名…]}` 或 `{行: [[段0…], [段1…]]}`。

    空行、空段、非字符串项都丢：结果里至少有一段有名字才留下。
    """
    cleaned: Dict[str, Any] = {}
    for line, segments in (marks or {}).items():
        if not isinstance(segments, list) or not segments:
            continue
        if all(isinstance(item, str) for item in segments):
            kept: Any = [str(name) for name in segments if name]      # 整行一段（不分段的老写法）
        else:
            kept = [[str(name) for name in seg if name]
                    for seg in segments if isinstance(seg, list)]
            while kept and not kept[-1]:                    # 结尾的空段没意义
                kept.pop()
        if any(kept):
            cleaned[str(line)] = kept
    return cleaned


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
        日语栏：用户已经填过就以**用户那栏为准**（它是「已确认的日语原文」），
        否则用模型分出来的那一栏；两种情况都会把「漢字(かんじ)」转成 `{{photrans|漢字|かんじ}}`。
        分完栏后的**统一格式化**（口径与「自动识别并填入」一致）：
        * 三栏都过 `normalize_blank_lines()`（连续空行只留一个）；
        * 中文栏按日语栏**逐行**对齐（`align_chs_to_jap()`，能用相邻配对就用），
          这样中文栏跟已输入的日语栏一行对一行；
        * 中文栏还要把日语栏里的**英文行**补上（`mirror_english_lines()`）：
          歌里唱的英文（`Fly away` 这种）模型常常只放进日语栏（用户 2026-10-05 报），
          中文栏缺一行就跟日语栏错位；
        * 罗马音栏按日语栏的**分段空行**对齐（`align_blank_lines()`）。
        """
        result = ai_lyrics.recognize(payload_json)
        if result.get("ok"):
            data = _load_payload(payload_json) or {}
            text = normalize_blank_lines(str(data.get("text") or ""))
            # 用户自己填了日语栏（「已确认的日语原文」）就以它为准，别用模型那栏顶掉：
            # 模型常把长音符 / 标点「顺手」改对，再拿它去来源里找行就一行都对不上，
            # 中文栏跟着整栏错位（用户 2026-10-07 报）。auto() 也是这么做的。
            given = normalize_blank_lines(str(data.get("jap") or ""))
            jap = with_furigana(given or normalize_blank_lines(str(result.get("jap") or "")))
            result["jap"] = jap
            for key in ("chs", "roma"):
                raw = str(result.get(key) or "")
                if is_empty(raw.strip()):
                    continue
                value = normalize_blank_lines(raw)
                # 中文栏交给 align_chs_to_jap（它自己会按日语栏分段），
                # 不要在这里再过一次 align_blank_lines：那会把「没配到译文的行」压掉，中文栏又错位。
                if key == "chs":
                    result[key] = align_chs_to_jap(text, jap, value)
                else:
                    result[key] = align_blank_lines(value, jap)
        return result

    def ai_mark_chs(self, payload_json: str) -> dict:
        """按**日语栏的标记**给中文栏打标记（界面上的「按日语标记中文」）。

        两栏行数一样时直接按行号照搬（不用联网、瞬间出结果）；
        行数不一样（译者把两句合成一句 / 多补一句）时交给大模型对齐行号，再把标记搬过去。
        返回 {'ok': True, 'marks': {行: [歌姬名…]}, 'message': …} 或 {'ok': False, 'error': …}。
        """
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        jap = str(data.get("jap") or "")
        chs = str(data.get("chs") or "")
        marks = _clean_marks(data.get("charaMarks"))
        if is_empty(jap.strip()):
            return {"ok": False, "error": "日语栏是空的，先把日语歌词填上"}
        if is_empty(chs.strip()):
            return {"ok": False, "error": "中文栏是空的，没有可以标记的译文"}
        if not marks:
            return {"ok": False, "error": "日语栏还没有标记：先在「日语栏」里点亮每行是谁唱"}

        jap_lines = jap.rstrip().split("\n")
        chs_lines = chs.rstrip().split("\n")
        if len(jap_lines) == len(chs_lines):
            pairs = {index: [index] for index in range(len(chs_lines))}
            note = "两栏行数一致，已按行号照搬"
        else:
            aligned = ai_lyrics.mark_translation(jap, chs, marks)
            if not aligned.get("ok"):
                return {"ok": False, "error": str(aligned.get("error"))}
            pairs = aligned["pairs"]
            note = (f"AI 已对齐两栏行号（{aligned.get('model')}，"
                    f"日语 {len(jap_lines)} 行 / 中文 {len(chs_lines)} 行）")
        new_marks = lyrics_colors.retarget_marks(pairs, marks)
        if not new_marks:
            return {"ok": False, "error": "没找到可以搬过去的标记，请手动在中文栏标一下"}
        return {"ok": True, "marks": new_marks,
                "message": f"{note}，中文栏已标好 {len(new_marks)} 行——请核对后点「完成」"}

    def auto(self, payload_json: str) -> dict:
        """自动识别：日语栏有内容就先按它挑中文，否则按脚本分类，再不行猜行号。

        装进日语栏之前会把「漢字(かんじ)」转成 {{photrans|漢字|かんじ}}（固定行为，不用配置）。
        """
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
                fixed_jap = with_furigana(normalize_blank_lines(jap))
                return {"ok": True, "mode": "extract",
                        "jap": fixed_jap,
                        "chs": align_chs_to_jap(text, fixed_jap, chs),
                        "roma": align_blank_lines(
                            normalize_blank_lines(str(data.get("roma") or "")), fixed_jap),
                        "message": "已以日语栏为参照挑出中文行"}

        classified_jap, classified_chs, classified_roma = classify_by_script(text)
        if classified_jap or classified_chs or classified_roma:
            fixed_jap = with_furigana(classified_jap)
            return {"ok": True, "mode": "classify",
                    "jap": fixed_jap,
                    "chs": align_chs_to_jap(text, fixed_jap, classified_chs),
                    "roma": align_blank_lines(classified_roma, fixed_jap),
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
        chara_marks = _clean_marks(data.get("charaMarks"))
        chara_marks_chs = _clean_marks(data.get("charaMarksChs"))

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
            chara_marks_chs=chara_marks_chs or None,
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
