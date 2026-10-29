"""歌词整理的数据侧：分类 / 切分 / 保存，以及 LyricsApi（界面与终端共用）。

界面已经是 PyQt5 主窗口里的「歌词」页（utils/ui/lyrics_panel.py），
本模块只留纯逻辑与 LyricsApi，便于单测。

    process_lyrics_jap()  日语歌词预处理：整行被多个换行裹住时重新分段（原在 utils/string.py，曾由配置项控制，现固定启用）
    normalize_blank_lines()  把「连续空行」压成一个空行（自动识别前 / 结果里都不会留 2 个以上空行）
    normalize_no_hover()  把来源里的 `#NoHover` 标记还原成空行（从条目复制来的歌词就带着它）
    auto()     自动识别：日语栏有内容 -> 以它为准挑中文；否则按脚本分类；再不行按重复段结构猜行号
    ai_auto()  AI 分栏（需 config.yaml 的 wikitext.ai_lyrics 允许 + wiki_credentials.yaml 的 ai_api_key）
    convert()  按「每组几行、取组内第几行」切分
    save()     收集结果（含「使用 LyricsKai/hover」开关）并交回 Lyrics
"""
import difflib
import json
import logging
import re
import unicodedata
from collections import Counter
from itertools import groupby
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from utils import ai_lyrics, japanese, lyrics_colors, source_filler
from utils.japanese import is_kana_letter, is_kanji
from utils.string import is_empty, is_junk_line, to_note_refs

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

# `{{LyricsKai/hover}}` 的段落标记：工具内部一律用**空行**分段，输出时才由
# `models/song.add_no_hover()` 把空行写成 `#NoHover`
NO_HOVER_RE = re.compile(r"^\s*#\s*NoHover\s*$", re.IGNORECASE)


def normalize_no_hover(text: str) -> str:
    """把来源里的 `#NoHover` 标记行还原成空行（本工具内部一律用空行分段）。

    用户 2026-10-13 对照 voca.wiki《Equivalent》：从条目里复制出来的歌词用的是
    `#NoHover` 标记分段 —— 一整块里**一个空行都没有**，段落边界只存在于这些标记上。
    不还原的话两件事一起坏：

    * 模型按提示的「逐字保留原文」把标记当歌词行收下，三栏里一个空行都没有
      （段落结构全丢，`LyricsKai/hover` 的悬停区整首错位）；
    * 标记还会被当成**纯 ASCII 行**分进罗马音栏（「自动识别并填入」那条路），
      非 hover 生成时更会原样写进条目正文（那时它只是模板标记，不是歌词）。

    逐行替换（一个标记行换一个空行），行数因此不变 —— 界面上按行号记的演唱者标记
    仍然对得上。空白与大小写宽容，可重复调用。
    """
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join("" if NO_HOVER_RE.match(line) else line for line in text.split("\n"))


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
# `{{ruby|漢字|かんじ}}`：与 `{{photrans}}` 一样，比对时只留汉字
RUBY_RE = re.compile(r"\{\{\s*ruby\s*\|([^|{}]*)\|([^{}]*)\}\}", re.IGNORECASE)


def _match_key(line: str) -> str:
    """两栏逐行比对用的「行指纹」：还原注音、忽略空白与标点、全半角归一。

    日语栏过完 `with_furigana()` 之后，汉字被包进 `{{photrans|漢字|かんじ}}`，
    待归类歌词里还是裸汉字；来源里的日语行又有「長音符写成 一」这类写法差异，
    直接逐字比对会一行都对不上，中文栏便从那一行起整栏错位
    （用户 2026-10-07 报的「AI 排版没跟着日语栏走」就是这么来的）。

    2026-10-29 又补两处（真机 52 条语料里因此多出 4 条能逐字重建）：
    `{{ruby|腸|はらわた}}` 要当成 `腸`（来源写的是 `腸（はらわた）`）；
    全半角统一（来源 `ＤＱＮ` vs 条目 `DQN`）。
    """
    text = RUBY_RE.sub(lambda match: match.group(1), line or "")
    text = unicodedata.normalize("NFKC", text)
    text = ai_lyrics.PHOTRANS_RE.sub(lambda match: match.group(1), text)
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
    if any(is_kana_letter(c) or is_kanji(c) for c in chars):
        return False
    return any(c.isascii() and c.isalpha() for c in chars)


def mirror_english_lines(chs: str, jap: str) -> str:
    """把日语栏里**缺掉**的英文行补进中文栏的同一位置。

    用户 2026-10-05 报：「用 AI 识别歌词时，中文歌词并不会包含日文歌词里的英文」。
    歌里本来就唱的英文（`Fly away` 这种）模型有时只放进日语栏，中文栏直接缺一行 ——
    两栏行数错位，标记、对齐、段落全都跟着错。这里以日语栏为骨架补一遍：
    日语栏的英文行在中文栏里没有对应内容时**原样补一份**（中文栏保留英文），
    已经有（模型照抄过 / 译者译成中文）就用中文栏的那一行，一个字都不动。

    ⚠️ **只补「缺口」那几行**（用户 2026-10-12 报的《咬人兔》）：译者把英文行翻成了中文
    时（`"I miss U."` → `"我想你"`），中文栏的行数并没有少 —— 照旧再补一份英文就会
    **多出一行**，从那里起整栏错位。所以先按「中文栏比日语栏少几行」算一个**补入预算**，
    只补到预算用完为止；两栏行数本来就一样时一个字都不补。
    日语栏没有英文行时原样返回，不影响既有输出。
    """
    jap_lines = normalize_blank_lines(jap).strip("\n").split("\n") if jap else []
    if not [line for line in jap_lines if is_english_line(line)]:
        return chs
    # 中文栏只看**非空行**：分段空行一律照日语栏重排（与 `align_blank_lines` 同一口径），
    # 否则两边都带空行时，这里会把中文栏的空行当成一行内容吃掉、整栏错开一行
    chs_lines = [line for line in normalize_blank_lines(chs).split("\n") if not is_empty(line)]
    budget = (len([line for line in jap_lines if not is_empty(line)])
              - len([line for line in chs_lines if not is_empty(line)]))
    if budget <= 0:                                # 中文栏一行不少（英文行已被译成中文）
        return chs
    result: List[str] = []
    index = 0
    inserted = 0
    for line in jap_lines:
        if is_empty(line):
            result.append("")
            continue
        if is_english_line(line):
            value = chs_lines[index] if index < len(chs_lines) else ""
            if value.strip() == line.strip():       # 模型照抄了英文 → 用中文栏那一行
                result.append(value)
                index += 1
            elif inserted < budget:                 # 缺这一行 → 补一份英文（不消耗中文栏）
                result.append(line.strip())
                inserted += 1
            elif index < len(chs_lines):            # 译者译成了中文 → 认领它，不补
                result.append(chs_lines[index])
                index += 1
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
        if any(is_kana_letter(c) for c in stripped):
            tagged.append(('jap', line))
        elif is_english_line(stripped) or all(c.isascii() for c in stripped):
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
            # 落在「日语行该在的位置」上的纯拉丁行是**歌里唱的英文**（`PANDORA BOX`），
            # 不是罗马音：它属于日语栏，中文栏同一位置才是它的译文。
            # 2026-10-25 踩过：网易云《パンドラ》的待整理歌词是「日/中」逐行交替，
            # 两处 `PANDORA BOX` 全被当成罗马音 → 日语栏少 2 行、中文栏多 2 行，
            # 整栏从此错位（用户报「AI 排版对不上」）。真正的罗马音栏是
            # 「日/中/罗马」三行一组，罗马音在 jap_phase 之外的格子上，不受影响。
            if period is not None and i % period == jap_phase:
                jap_out.append(line)
            else:
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
    """日语栏已有内容时，以日语歌词为参照，从待归类歌词中提取中文翻译。

    **所有日语行都不进中文栏**，不只是跟日语栏逐字对上的那些：来源与日语栏常有个别
    写法差异（长音符、送假名、标点），对不上的日语行以前会被当成中文留在中文栏，
    中文行数比日语栏多出几行，`align_chs_to_jap()` 便放弃对齐、一个段落空行都不补
    （用户 2026-10-17 报的《怨灵案件》：网易云原文有一句把两行并成一行）。
    去掉日语行后中文栏只剩余译文，行数对得上才谈得上按日语栏分段。
    """
    if is_empty(jap_text):
        return ""
    jap_lines = {_match_key(line) for line in jap_text.splitlines() if not is_empty(line)}
    chs_lines: List[str] = []
    for line in normalize_blank_lines(translation_text).splitlines():
        if is_empty(line):
            chs_lines.append("")
        elif _match_key(line) not in jap_lines and not is_japanese_line(line):
            chs_lines.append(line)
    return "\n".join(chs_lines).strip()


def is_japanese_line(line: str) -> bool:
    """这一行像不像日语原文（含假名）。"""
    return any(is_kana_letter(c) for c in (line or "").strip())


def is_chinese_only(text: str) -> bool:
    """这段歌词是不是**只有中文**（一行日语都没有）？

    「从链接填充」用它决定歌词该进哪一栏：atwiki 上不少页面只写译文、网易云也可能只给
    `tlyric` —— 这种只有中文的整段直接进「中文」栏最省事（日语栏通常已经有原文了）；
    中日混杂的才交给「待归类歌词」栏去分栏。判定口径与 `classify_by_script()` 一致。
    """
    jap, chs, _roma = classify_by_script(text)
    return bool(chs.strip()) and not jap.strip()


def _interleave_block_pairs(text: str) -> Optional[str]:
    """「日语一整块 + 中文一整块」→「日文一行、中文一行」；不是这个形状返回 None。

    来源只给**两段**（前半段全是日语原文、后半段全是中文译文，段间没有别的结构）时，
    `pair_chs_with_jap()` 按相邻关系一行也配不上：日语行全部排在前面，它后面跟着的
    还是日语行。整块中文于是被当成「多出来的行」堆在最后，中文栏一行也对不上日语栏
    （用户 2026-10-17 报的《怨灵案件》：网易云把原文与译文整块拼在一起）。

    这里把两块按行号对折成「日文一行、中文一行」，交给原来的配对逻辑（它会把结果
    对齐到日语栏的段落空行）。只有**两块、语言各占一块、行数相同**才动手；
    行数对不上（译者合并 / 拆开了句子）时返回 None，绝不硬凑。
    """
    lines = [line.strip() for line in normalize_blank_lines(text).split("\n")
             if not is_empty(line) and not is_junk_line(line)]
    runs: List[Tuple[str, List[str]]] = []
    for line in lines:
        if is_japanese_line(line):
            kind = "jap"
        elif any(is_kanji(c) for c in line):
            kind = "chs"
        else:
            kind = ""                      # 英文 / 纯标点：算进它所在的那一块
        if not kind:
            if runs:
                runs[-1][1].append(line)
            continue
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(line)
        else:
            runs.append((kind, [line]))
    if len(runs) != 2 or {kind for kind, _ in runs} != {"jap", "chs"}:
        return None
    jap = next(run for kind, run in runs if kind == "jap")
    chs = next(run for kind, run in runs if kind == "chs")
    if len(jap) < 2 or len(jap) != len(chs):
        return None
    result: List[str] = []
    for jap_line, chs_line in zip(jap, chs):
        result.extend((jap_line, chs_line))
    return "\n".join(result)


PAIRED_MIN_LINES = 8          # 少于这么多行时不走「已经逐行配对」的捷径（太短判不准）
MAX_MERGE_BLOCKS = 80         # 「块 → 日语段」枚举的上限（正常歌词的分块数远小于它）
ALIGN_MIN_SIMILARITY = 0.35   # 打分对齐里「这一行日语认成那句话」所需的最低相似度
ALIGN_MAX_SPAN = 6            # 一行日语最多由几句来源拼成（反之亦然）
ALIGN_DROP_COST = 0.45        # 丢掉一句来源的代价（比配错一句便宜，比配上贵）
ALIGN_BLANK_COST = 1.2        # 让一行日语没译文的代价（比勉强配上更贵：宁可先配上再让人核对）
# 括号注：全角 / 半角都算，允许嵌套（`（日本网络用语「草」（くさ）…）`）
NOTE_OPEN = "（("
NOTE_CLOSE = "）)"
# 整行没有任何「字」（♪ / ↓ / -END- / music... / -----）→ 纯记号行，不是歌词
LETTER_RE = re.compile(r"[A-Za-z\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 拆分译文时**丢弃**的从句标点：把它当成换行位置（条目里 `好了，给你…` → `好了` / `给你…`），
# 句末标点（。！？…）留住，那是句子的一部分
CLAUSE_TAIL_RE = re.compile(r"[\s\u3000，、,；;：:]+$")
# 拆分译文时的切点：标点 / 空白之后（「，」留在前半句，「\u3000」两边都不要）
CN_SPLIT_RE = re.compile(r"(?<=[，、,。！？!?；;…~～])|(?<=[\s\u3000])")


def _without_notes(line: str) -> str:
    """去掉括号注之后的正文（**只给判定用**，输出一律用原文）。

    网易云的译文常把译注写在括号里，里面还引着日文原文：
    `被骗了 笑不活啦（日本网络用语「草」（くさ）来表示“笑”的意思）`。
    `split_paired_lines()` 以前只看「有没有假名」，这样一行就被判成「不是中文」，
    整段逐句配对作废、白白丢给模型（用户 2026-10-29 报的《image(picco)》：28 句配对
    被这一句挡掉）。括号里的内容不改变「这一格是中文」这个事实，所以先剥掉再判定。
    """
    text = str(line or "")
    if not any(char in text for char in NOTE_OPEN):
        return text
    result: List[str] = []
    depth = 0
    for char in text:
        if char in NOTE_OPEN:
            depth += 1
            continue
        if char in NOTE_CLOSE and depth:
            depth -= 1
            continue
        if not depth:
            result.append(char)
    return "".join(result)


def split_paired_lines(text: str) -> Optional[Tuple[str, str]]:
    """「一行日文、一行中文」逐行交替的文本 → `(日语, 中文)`；不是这个形状返回 None。

    「从链接填充」取网易云歌词时，`netease_lyrics()` 已按时间标签把原文与译文**逐行穿插**
    （见 `utils/source_filler.py`），所以「待归类歌词」里本来就已经配好对了 —— 再交给模型
    分栏是白花一次请求，模型还可能自行合并 / 补行把对齐弄坏（用户 2026-10-25 给的
    《パンドラ》：100 行「日/中」交替，两处 `PANDORA BOX` 被算成罗马音后整栏错位）。
    这里先认这种形状：奇数格必须是日语、偶数格必须是中文，两边行数天然 1:1。

    判定从严（认不准就返回 None，交给原来的分栏 / AI 路径）：
    * 行数是偶数且不少于 `PAIRED_MIN_LINES`；
    * 日语格子里**带假名或整行英文**的行占多数（纯汉字的日文行允许少量，按位置照样归日语）——
      2026-10-27 踩过：《binary biotope》37 句里 24 句是英文（`I'm calling!` / `(I'm falling)`
      这种），只按「带假名的行」数就不到一半，这段明明配好对的文本被当成「认不出」丢给模型，
      模型分出来的结果反而丢了分段（用户报「AI 排版没用」）；
    * 中文格子里不能有假名（**括号注里的假名不算**，见 `_without_notes()`），且必须有汉字
      （英文行只允许出现在日语格子里）。

    分段空行按**块**处理：每一块都要单独满足上面几条（b 站专栏 / 图文就是「逐句配对 +
    空行分段」，用户 2026-10-29 给的 BV 专栏链接），分段位置原样保留 —— 以前只要有空行
    就整个放弃，这类来源只能交给模型。
    """
    blocks = _split_blocks([line.strip() for line in normalize_blank_lines(text).split("\n")])
    if not blocks:
        return None
    jap_out: List[str] = []
    chs_out: List[str] = []
    total = 0
    for block in blocks:
        paired = _split_paired_block(block)
        if paired is None:
            return None
        total += len(block)
        if jap_out:
            jap_out.append("")
            chs_out.append("")
        jap_out.extend(paired[0])
        chs_out.extend(paired[1])
    if total < PAIRED_MIN_LINES:
        return None
    return "\n".join(jap_out), "\n".join(chs_out)


def _split_paired_block(lines: Sequence[str]) -> Optional[Tuple[List[str], List[str]]]:
    """一块（段内，无空行）里的「日/中」交替 → 两栏的行；不是这个形状返回 None。"""
    if not lines or len(lines) % 2:
        return None
    jap_slots, chs_slots = list(lines[0::2]), list(lines[1::2])
    for line in chs_slots:
        # 括号注里的假名不算（见 `_without_notes()`）；整格就是一个括号（`（我正在坠落）`）时
        # 按原文判定 —— 那本身就是译文，不是「日文行漏进中文格」。
        bare = _without_notes(line).strip() or line
        if is_japanese_line(bare) or not any(is_kanji(c) for c in bare):
            return None
    original_lines = sum(1 for line in jap_slots
                         if is_japanese_line(line) or is_english_line(line))
    if original_lines * 2 < len(jap_slots):      # 原文行不到一半 → 不是这个形状
        return None
    return jap_slots, chs_slots


_SQUEEZE_RE = re.compile(r"\s+")


def _squeeze(text: str) -> str:
    """去掉所有空白（含全角空格）—— 只比「文字内容」时用。"""
    return _SQUEEZE_RE.sub("", text or "")


def merge_chs_lines(pairs_jap: str, pairs_chs: str, jap: str) -> Optional[str]:
    """把**逐句配对**的中文译文按日语栏自己的换行合并成同结构；对不上返回 None。

    场景（用户 2026-10-25 给的网易云《パンドラ》）：日语栏是 VocaDB 的**合并行**原文
    （`無理矢理に酔っ払い（トリップ）　お手軽マジック`，30 行），而「从链接填充」取回来的
    译文是网易云按**每一句**给的（50 行）。两栏行数对不上就没法一一对应，以前只能交给
    模型去合并 —— 而这件事其实是**确定的**：原文逐句拼起来与日语栏完全一样，译文照同样的
    句子边界合并（中间留一个空格）就得到与日语栏行数相同的中文。

    只在文本**逐字相同**（忽略空白）时才动手：日语（逐句拼起来）与日语栏对不上就是另一份
    歌词，返回 None 交给原来的逻辑，绝不硬凑。段落空行按日语栏原样补回去。
    """
    jap_phrases = [line.strip() for line in str(pairs_jap or "").split("\n") if line.strip()]
    chs_phrases = [line.strip() for line in str(pairs_chs or "").split("\n") if line.strip()]
    if not jap_phrases or len(jap_phrases) != len(chs_phrases):
        return None
    jap_lines = normalize_blank_lines(jap).split("\n")
    lines = [line for line in jap_lines if not is_empty(line)]
    if not lines or _squeeze("".join(lines)) != _squeeze("".join(jap_phrases)):
        return None
    merged: List[str] = []
    cursor = 0
    for line in lines:
        target = _squeeze(line)
        taken: List[str] = []
        consumed = ""
        while cursor < len(jap_phrases) and consumed != target:
            step = consumed + _squeeze(jap_phrases[cursor])
            if not target.startswith(step):
                return None                     # 这一句对不上：不是同一份歌词
            consumed = step
            taken.append(chs_phrases[cursor])
            cursor += 1
        if consumed != target or not taken:
            return None
        merged.append(" ".join(part for part in taken if part))
    if cursor != len(jap_phrases):
        return None
    result: List[str] = []
    index = 0
    for line in jap_lines:                      # 空行位置与日语栏逐行一致
        if is_empty(line):
            result.append("")
        else:
            result.append(merged[index])
            index += 1
    return "\n".join(result).strip("\n")


def align_chs_lines(jap: str, jap_phrases: str, chs_phrases: str) -> Optional[Tuple[str, int]]:
    """逐句配对的译文 → 按日语栏分行；重复段共用译文、来源多写的句子丢掉。返回 `(中文栏, 丢掉几旬)`。

    场景（用户 2026-10-29 给的《image(picco)》，网易云 `2037363383`）：来源是「日/中」逐句
    配对的 28 句，条目的日语栏却是 20 行 —— 两者的**结构**不一样：

    * 条目里第 2、4、5 段是「两句并一行」（`都合のいい愛嬌で 撃ち抜いてく大本命`），
      来源里是分开的两句 → 合并；
    * 条目里 `くだんない偶像` / `早く目 覚まして！` 出现在第 3、6 段（同一段日文出现两次），
      来源里写了 4 遍 → 同一句日文共用同一份中文，多出来的丢掉；
    * 副歌那 4 行条目写了两次、来源只有一次 → 同样靠「同一句日文共用同一份中文」补上。

    `merge_chs_lines()` 要求两边的字**一模一样**，这种形状它只能拒绝（于是整段丢给模型，
    模型又把重复段塞错地方）。这里放宽到「确定能证明的对齐」：

    * 每行日语都由**连续的若干句**来源逐字拼成（忽略空白）—— 只许合并，不许改写；
    * 同一句日语（去空白后相同）只对一次，其它出现共用那份中文；
    * 来源里没被用掉的句子允许存在（重复段 / 多写的副歌），数量会返回给调用方去提示；
    * **解唯一**才动手（多解 / 无解返回 None），且要求不同日语行 ≥ `ALIGN_MIN_LINES`、
      用掉的来源句 ≥ 一半 —— 免得把「另一份歌词」硬凑成对齐。

    空行按日语栏的位置补回去（与 `merge_chs_lines()` 一致）。
    """
    jap_lines = normalize_blank_lines(jap).split("\n")
    keys = [_squeeze(line) for line in jap_lines]
    unique: List[str] = []
    for key in keys:
        if key and key not in unique:
            unique.append(key)
    phrases = [line.strip() for line in str(jap_phrases or "").split("\n") if line.strip()]
    chs_lines = [line.strip() for line in str(chs_phrases or "").split("\n") if line.strip()]
    if len(phrases) != len(chs_lines) or not phrases:
        return None
    if len(unique) < ALIGN_MIN_LINES or len(phrases) <= len(unique):
        return None                        # 太短 / 没有多余的句子：走严格版或别的路径
    phrase_keys = [_squeeze(line) for line in phrases]
    result = _align_unique_lines(unique, phrase_keys, chs_lines)
    if result is None:
        return None
    table, dropped = result
    if (len(phrases) - dropped) * 2 < len(phrases):
        return None                        # 用掉的不够一半：来源多半是另一份歌词
    jap_result: List[str] = []
    for line in jap_lines:
        if is_empty(line):
            jap_result.append("")
        else:
            jap_result.append(table[_squeeze(line)])
    return "\n".join(jap_result).strip("\n"), dropped


ALIGN_MIN_LINES = 4           # 少于这么多「不同的日语句」时不猜对齐（太短判不准）
ALIGN_MAX_SPAN = 8            # 一行日语最多由几句来源拼成


def _align_unique_lines(unique: List[str], phrase_keys: Sequence[str],
                        chs_lines: Sequence[str]) -> Optional[Tuple[Dict[str, str], int]]:
    """`unique`（不同的日语行）→ 来源的连续几句；唯一解才返回 `({日语句: 中文}, 丢掉句数)`。"""
    memo: Dict[Tuple[int, int], Dict[str, int]] = {}

    def solve(index: int, cursor: int) -> Dict[str, int]:
        """`{中文栏: 丢掉的句数}`（同一个中文栏可能对应多种丢法，只记一个句数）。"""
        if (index, cursor) in memo:
            return memo[(index, cursor)]
        memo[(index, cursor)] = {}
        found: Dict[str, int] = {}
        if index == len(unique):
            found[""] = len(phrase_keys) - cursor
        else:
            want, consumed = unique[index], ""
            for span in range(1, ALIGN_MAX_SPAN + 1):
                if cursor + span > len(phrase_keys):
                    break
                consumed += phrase_keys[cursor + span - 1]
                if not want.startswith(consumed):
                    break
                if consumed != want:
                    continue
                value = " ".join(chs_lines[cursor:cursor + span])
                for tail, dropped in solve(index + 1, cursor + span).items():
                    found[f"{value}\n{tail}" if tail else value] = dropped
            if cursor < len(phrase_keys):          # 丢掉这一句来源（重复段）
                for tail, dropped in solve(index, cursor + 1).items():
                    found.setdefault(tail, dropped + 1)
        if len(found) > 2:                         # 只关心「唯一」：两个不同结果就够判了
            found = {text: found[text] for text in sorted(found)[:2]}
        memo[(index, cursor)] = found
        return found

    solutions = solve(0, 0)
    if len(solutions) != 1:
        return None
    text, dropped = next(iter(solutions.items()))
    values = text.split("\n") if text else []
    if len(values) != len(unique):
        return None
    return dict(zip(unique, values)), dropped


def _split_cn_for(count: int, text: str, weights: Optional[Sequence[int]] = None) -> List[str]:
    """一句译文要铺到 `count` 行日语上 → 在标点 / 空白处切成 `count` 段。

    切不出来（标点不够）就**整句放第一行、其余留空** —— 只动换行、绝不造字
    （用户 2026-10-29 给的《404 Ressenti Girl》：来源 `さぁ こんがらがってるアナタに404！`
    一行，条目拆成两行，`好了，给你这个混乱的404！` 在「，」处切开正好两段）。

    例外：整句**一个标点都没有**、而 `weights` 给了各行日文的长度时，按长度比例切
    （译文长度大致跟着原文走）。实测网易云《下手者》的 `歌を` / `延々喚いたって` 共用一句
    `将歌一遍再一遍叫嚷出来`，按 2:8 切开正好是条目里的 `将歌` / `一遍再一遍叫嚷出来`。
    """
    if count <= 1:
        return [text]
    pieces = [piece.strip() for piece in CN_SPLIT_RE.split(text or "") if piece.strip()]
    if len(pieces) < count:
        # 标点不够切：没有标点的整句按各行日文长度的比例切
        pieces = _split_cn_by_weight(text, weights) if weights else None
    elif len(pieces) > count:
        pieces = _merge_cn_pieces(pieces, count)
    if pieces is None:
        return [text] + [""] * (count - 1)
    # 从句标点（，、；：）在分行的位置就不要再留着了；句末标点（。！？）留住
    cleaned = [CLAUSE_TAIL_RE.sub("", piece) for piece in pieces]
    if any(not piece for piece in cleaned):
        return [text] + [""] * (count - 1)
    return cleaned


def _split_cn_by_weight(text: str, weights: Sequence[int]) -> Optional[List[str]]:
    """按各行日文长度的比例把没有标点的一句译文切开（切点只能落在字与字之间）。"""
    chars = [char for char in str(text or "") if not char.isspace()]
    total = sum(weights)
    if not chars or total <= 0 or len(chars) < len(weights) * 2:
        return None
    pieces: List[str] = []
    used = 0
    for weight in weights[:-1]:
        used += weight
        cut = max(1, round(len(chars) * used / total))
        pieces.append("".join(chars[:cut]))
        chars = chars[cut:]
    pieces.append("".join(chars))
    if len(pieces) != len(weights) or any(not piece for piece in pieces):
        return None
    return pieces


def _merge_cn_pieces(pieces: Sequence[str], count: int) -> Optional[List[str]]:
    """切点比需要的多 → 按字符长度把相邻片段并成 `count` 组。"""
    if len(pieces) == count:
        return list(pieces)
    total = sum(len(piece) for piece in pieces)
    groups: List[str] = []
    current = ""
    used = 0
    for piece in pieces:
        current += piece
        used += len(piece)
        if len(groups) < count - 1 and used >= total * (len(groups) + 1) / count:
            groups.append(current)
            current = ""
    groups.append(current)
    if len(groups) != count or any(not group.strip() for group in groups):
        return None
    return [group.strip() for group in groups]


def source_pairs(text: str, jap: str = "") -> List[Tuple[str, str]]:
    """「待归类歌词」→ `[(日文行, 译文)]`：**逐行定性**，不再要求整段严格交替。

    以前 `split_paired_lines()` 是全有或全无：一首 117 行的网易云歌词里只要有一行没有译文
    （纯音乐标记 `music...`、语气行 `See ya!`、记号 `♪`、`-END-`），或中文格整行英文
    （`Get ready! Get ready!`、`Dabadabadabada…`、`↓`），整首就判成「不是逐句配对」，
    一切退回模型 —— 真机 52 条语料里 **20 条**都栽在这里（用户 2026-10-29 报的《404》就是）。

    现在的规则（每行各自定性，坏行只影响自己那一处）：

    * `noise`：整行没有任何「字」（`♪` `↓` `-END-` `music...` `-----`）→ 丢掉；
    * 日文行后面紧跟的中文行就是它的译文；后面还是日文行 → 这一对的译文**留空**
      （纯音乐 / 念白没翻译），不再牵连整首；
    * **只有汉字的行**（`陰陽師`、`森羅万象掌握` 这种日文歌词）光看脚本分不出来 ——
      `jap` 给了就以它为准（`_looks_japanese()`）：出现在日语栏里就算日文行，
      否则才算译文（《藁》《下手者》就是被这一点错开一格的）；
    * 中文格判定先去掉括号注；**去掉后没有汉字就退回原文判**（`lie（谎言）` 这种，
      括号注是唯一含汉字的部分 —— 上一轮新加的 `_without_notes()` 反而把它判死了）；
    * 整行拉丁字母：紧跟同文拉丁行 → 是「原文即译文」的英文行，丢掉；否则按日文行处理；
    * 开头就有、没有对应日文的中文行 → 丢掉（网易云的末句时间标签常这样）。
    """
    jap_keys = _jap_line_keys(jap) if str(jap or "").strip() else []
    lines = [line.strip() for line in normalize_blank_lines(text).split("\n")]
    entries: List[Dict[str, str]] = []
    shared: Optional[int] = None       # 上一个 `↓` 的位置：它的译文要往前挂
    for line in lines:
        if not line:
            continue
        bare = _without_notes(line).strip()
        if not LETTER_RE.search(line) and not LETTER_RE.search(bare):
            # `↓` / `↓（下の行が訳）` 这类标记的意思是「**下面那句译文属于上面那些日文**」。
            # 实测网易云《下手者》：`歌を` / `↓` / `延々喚いたって` / `将歌一遍再一遍叫嚷出来`
            # —— 一句译文覆盖两行日文。以前把 `↓` 当空行丢掉，译文就挂到了**后**一行，
            # 后面整首跟着错开一行（用户 2026-10-29 报的「一直出错」就是这个形状）。
            if "↓" in line or "↓" in bare:
                if entries:
                    shared = len(entries) - 1      # 箭头**之前**那一行（`歌を`），不是下一格
            continue
        has_kana = any(is_kana_letter(char) for char in bare)
        # 汉字算**整行**（括号注里的汉字也算）：`lie（谎言）` 去掉注之后只剩 `lie`，
        # 若按去注后的文本判，它会被当成「整行英文」而丢掉译文
        has_kanji = any(is_kanji(char) for char in line)
        latin_only = not has_kana and not has_kanji
        if latin_only and not any(char.isascii() and char.isalpha() for char in bare):
            continue                                   # `↓` 之类
        if has_kana:
            entries.append({"kind": "jp", "line": line, "chs": ""})
        elif has_kanji:
            # 只有汉字：是日文歌词还是译文？有日语栏当参照时问它
            if jap_keys and _looks_japanese(bare, jap_keys) and (
                    not entries or entries[-1]["chs"]):
                entries.append({"kind": "jp", "line": line, "chs": ""})
            elif shared is not None and shared < len(entries):
                # `↓` 之后那句译文：它属于上面**整组**日文 —— 把这一组并成一个来源单位
                # （`jp` 里用换行放多行），这样对齐器就能把它拆回多行
                for position in range(shared, len(entries)):
                    entries[position]["group"] = shared
                entries[shared]["chs"] = line
                shared = None
            elif entries and not entries[-1]["chs"] and entries[-1]["kind"] in ("jp", "latin"):
                entries[-1]["chs"] = line
            elif entries and entries[-1]["kind"] in ("jp", "latin") and entries[-1]["chs"]:
                # 上一对已经有译文：这是**译文的续行**（译者把一句拆成两行），
                # 用空格并进同一对 —— 丢掉的话条目里那句译文就没了
                # （真机《最讨厌Hobo日P了》重复段就是这样）
                entries[-1]["chs"] = f"{entries[-1]['chs']} {line}"
            elif jap_keys and _looks_japanese(bare, jap_keys):
                entries.append({"kind": "jp", "line": line, "chs": ""})
            # 没有对应日文的中文行：丢掉（记在返回值里由调用方统计）
        elif latin_only:
            if (entries and entries[-1]["kind"] == "latin"
                    and _squeeze(entries[-1]["line"]) == _squeeze(line)):
                continue                               # 原文与译文相同的英文行
            entries.append({"kind": "latin", "line": line, "chs": ""})
    return _coalesce_groups([entry for entry in entries
                             if entry["kind"] in ("jp", "latin")])


def _coalesce_groups(entries: Sequence[Dict[str, str]]) -> List[Tuple[str, str]]:
    """把标记了同一个 `group` 的相邻日文行并成一个来源单位（`jp` 里是换行的多行）。"""
    result: List[Tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        group = entry.get("group")
        if group is None:
            result.append((entry["line"], entry["chs"]))
            index += 1
            continue
        lines = []
        while index < len(entries) and entries[index].get("group") == group:
            lines.append(entries[index]["line"])
            index += 1
        result.append(("\n".join(lines), entry["chs"]))
    return result


def _pair_key(jp: str) -> str:
    """来源单位的行指纹（`↓` 组是多行，按行拼起来再比较）。"""
    return "".join(_match_key(line) for line in str(jp or "").split("\n"))


INSTRUMENTAL_HINTS = ("纯音乐", "純音樂", "請欣賞", "请欣赏", "instrumental", "纯伴奏", "純伴奏",
                      "此歌曲为没有填词的纯音乐")


def is_instrumental(text: str) -> bool:
    """这段「歌词」其实只是「纯音乐，请欣赏」之类的提示（没有歌词）。

    实测网易云《Strong Zero》（`1992892042` 那一类）的歌词接口只给一句
    「纯音乐，请欣赏」：以前它被当成一行歌词塞进栏里，用户还得自己删。
    """
    lines = [line.strip() for line in str(text or "").split("\n") if line.strip()]
    if not lines or len(lines) > 2:
        return False
    joined = " ".join(lines).lower()
    return any(hint in joined for hint in INSTRUMENTAL_HINTS)


def align_lyrics(jap: str, pairs: Sequence[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
    """按日语栏给逐句译文排版：**单调最优对齐**，合并 / 拆分 / 丢句 / 留空都由打分决定。

    为什么要换掉「唯一解才动手」（`align_chs_lines()` 的口径）：真机 52 条语料里，
    只有 14 条（归一后 18 条）的日语栏能由来源日文逐字拼回来 —— 校对会删行
    （`大丈夫、`）、改字（`送る` → `贈る`）、删掉 `music...`。要求「逐字相等 + 解唯一」
    在真实数据上几乎必然失败，于是每次都退化成「交给模型」，而模型没有可核对的约束。
    这就是「优化十几次还是错」的机械原因（见 `tests/utils/test_lyrics_corpus.py` 的记分卡）。

    这里把两件事分开：
    * **配哪一句**用相似度打分（`_match_key()` 之后 `difflib` 比值 ≥ `ALIGN_MIN_SIMILARITY`）；
    * **怎么铺**允许四种形态（这正是条目里真实出现的）：

      | 形态 | 含义 |
      | --- | --- |
      | 1 行 ↔ 1 对 | 常态 |
      | 1 行 ↔ N 对 | 合并：N 句译文用空格拼成一行 |
      | N 行 ↔ 1 对 | 拆分：一句译文在标点处按长度切成 N 段（切不出就整句放第一行） |
      | 丢掉 1 对 / 跳过 1 行 | 来源多写的丢弃；条目这行没有可用译文则**留空**（不猜） |

    取总分最高的路径。返回 `{chs, filled, blank, dropped, merged, split, score, ratio}`；
    拿不到像样的对齐（覆盖率不到一半）返回 None。
    """
    jap_lines = normalize_blank_lines(jap).split("\n")
    targets = [line for line in jap_lines if line.strip()]
    if not targets or not pairs:
        return None
    keys = [_match_key(line) for line in targets]

    def score(index: int, source_key: str) -> float:
        left, right = keys[index], source_key
        if not left or not right:
            return 0.0
        if left == right:
            return 1.0
        # 条目那一行是来源的**前缀 / 子串**：校对常把长句截短（真机《下手者》的日语栏
        # 有 `将歌` 这种，来源是完整一句）—— 认成同一句，否则整段被判「没译文」
        shorter, longer = sorted((left, right), key=len)
        if len(shorter) >= 3 and shorter in longer:
            return 0.85
        return difflib.SequenceMatcher(None, left, right).ratio()

    # similarity[i][j]：第 i 行日语 与 第 j 对来源 的相似度
    similarity = [[score(i, _pair_key(pairs[j][0])) for j in range(len(pairs))]
                  for i in range(len(targets))]
    n_target, n_source = len(targets), len(pairs)
    # best[i][j] = (总分, 决策, 上一格)；决策见下面 walk 的说明
    best: Dict[Tuple[int, int], Tuple[float, str, Optional[Tuple[int, int]]]] = {}
    for i in range(n_target, -1, -1):
        for j in range(n_source, -1, -1):
            if i == n_target and j == n_source:
                best[(i, j)] = (0.0, "end", None)
                continue
            candidates: List[Tuple[float, str, Tuple[int, int]]] = []
            if i < n_target and j < n_source:
                ratio = similarity[i][j]
                if ratio >= ALIGN_MIN_SIMILARITY:
                    tail = best.get((i + 1, j + 1))
                    if tail:
                        candidates.append((ratio + tail[0], "one", (i + 1, j + 1)))
                # 合并：一行日语 = 后面连续 N 对来源的**日文逐字拼起来**（N ≥ 2）。
                # 只认逐字相等 —— 现实里条目把两句并成一行时，日文就是那两句的直接拼接；
                # 放宽成「相似度尚可」会让 DP 到处合并（真机测过：一首歌乱合 18 处）。
                joined = keys[i]
                consumed = ""
                for span in range(1, ALIGN_MAX_SPAN + 1):
                    if j + span > n_source:
                        break
                    consumed += _pair_key(pairs[j + span - 1][0])
                    if joined != consumed:
                        if not joined.startswith(consumed):
                            break
                        continue
                    if span < 2:
                        continue
                    tail = best.get((i + 1, j + span))
                    if tail:
                        candidates.append((1.0 + 0.01 * span + tail[0],
                                           f"merge{span}", (i + 1, j + span)))
                    break
                # 拆分：连续 N 行日语逐字拼起来 = 这一对来源的日文（N ≥ 2）
                assembled = ""
                for span in range(1, ALIGN_MAX_SPAN + 1):
                    if i + span > n_target:
                        break
                    assembled += keys[i + span - 1]
                    source_key = _pair_key(pairs[j][0])
                    if assembled != source_key:
                        if not source_key.startswith(assembled):
                            break
                        continue
                    if span < 2:
                        continue
                    tail = best.get((i + span, j + 1))
                    if tail:
                        candidates.append((1.0 + 0.01 * span + tail[0],
                                           f"split{span}", (i + span, j + 1)))
                    break
            if j < n_source:                       # 丢掉这一对来源
                tail = best.get((i, j + 1))
                if tail:
                    candidates.append((tail[0] - ALIGN_DROP_COST, "drop", (i, j + 1)))
            if i < n_target:                       # 这一行日语没有可用译文
                tail = best.get((i + 1, j))
                if tail:
                    candidates.append((tail[0] - ALIGN_BLANK_COST, "blank", (i + 1, j)))
            if not candidates:
                best[(i, j)] = (-1e9, "none", None)
                continue
            top = max(candidates, key=lambda item: item[0])
            best[(i, j)] = top

    total, decision, step = best.get((0, 0), (-1e9, "none", None))
    if decision == "none" or total <= 0:
        return None
    values: List[str] = []
    dropped = merged = split = 0
    position = (0, 0)
    while position != (n_target, n_source):
        _score_value, decision, step = best[position]
        if step is None:
            break
        index, cursor = position
        if decision == "one":
            values.append(pairs[cursor][1])
        elif decision.startswith("merge"):
            span = int(decision[5:])
            values.append(" ".join(pairs[cursor + offset][1]
                                   for offset in range(span) if pairs[cursor + offset][1]))
            merged += 1
        elif decision.startswith("split"):
            span = int(decision[5:])
            weights = [max(1, len(keys[index + offset])) for offset in range(span)]
            values.extend(_split_cn_for(span, pairs[cursor][1], weights))
            split += 1
        elif decision == "drop":
            dropped += 1
        elif decision == "blank":
            values.append("")
        position = step
    if len(values) != n_target:
        return None
    # 同一句日文在条目里出现多次、来源只给了一次（副歌重复）→ 共用同一份译文。
    # 真机《image(picco)》：`くだんない偶像` 那两行出现 4 次，网易云写了 4 遍、
    # 条目只写 2 遍（副歌反过来），靠这一条把重复段的译文补上。
    first_value: Dict[str, str] = {}
    for index, key in enumerate(keys):
        if len(key) < 3:
            continue
        if values[index].strip():
            first_value.setdefault(key, values[index])
        elif key in first_value:
            values[index] = first_value[key]
    filled = sum(1 for value in values if value.strip())
    if filled * 2 < n_target:                  # 填不到一半：多半不是同一份歌词
        return None
    # 空行位置按日语栏还原
    result: List[str] = []
    cursor = 0
    for line in jap_lines:
        if line.strip():
            result.append(values[cursor])
            cursor += 1
        else:
            result.append("")
    return {"chs": "\n".join(result).strip("\n"), "filled": filled, "blank": n_target - filled,
            "dropped": dropped, "merged": merged, "split": split,
            "score": round(total, 2), "ratio": round(filled / n_target, 2)}


def _align_message(aligned: Dict[str, Any]) -> str:
    """把对齐统计说给用户听（填了几行 / 留空几行 / 丢掉几句 / 合并拆分几处）。"""
    parts = [f"已按日语栏对齐（填了 {aligned['filled']} 行"]
    if aligned["blank"]:
        parts.append(f"留空 {aligned['blank']} 行")
    if aligned["dropped"]:
        parts.append(f"丢掉来源里多写的 {aligned['dropped']} 句")
    if aligned["merged"]:
        parts.append(f"合并 {aligned['merged']} 处")
    if aligned["split"]:
        parts.append(f"拆分 {aligned['split']} 处")
    text = "，".join(parts) + "）"
    if aligned["blank"]:
        text += "；留空的行没有对应译文，请补一下"
    return text + "，请核对"


def merge_chs_blocks(jap: str, chs: str) -> Optional[str]:
    """「只有中文、译者用空行分块」的译文 → 合并成与日语栏**同结构**；拿不准返回 None。

    场景（用户 2026-10-29 给的《幽灵(KAIRUI)》，待识别歌词是 b 站评论区那条）：日语栏 11 行、
    分 3 段（3 / 4 / 4 行），译文只有中文、按译者的习惯切成了 5 块（3 / 3 / 3 / 4 / 4 行）::

        夏叶末端 / 浮云群游 / 层层叠叠        ← 这三行是日语第 1 行的译文
        遥而望尽 / 寻踪觅迹 / 那奇丽之形       ← 日语第 2 行
        在转眼之间 / 便会消失不见的 / 忧郁尽头  ← 日语第 3 行
        像幽灵一样啊 …（4 行）                ← 日语第 2 段，一行对一行
        像幽灵一样啊 …（4 行）                ← 日语第 3 段

    条目里中文栏是 11 行、分段与日语栏一样，也就是**前三个块各自合并成一行**（合并处留一个
    空格）。这件事是确定的，不该丢给模型：模型只会按顺序一格格填，得到「行数对、对应全错」
    的结果，而 `align_chs_to_jap()` 又只看行数，一句都不会说。

    做法：日语栏按空行切出每段的行数，来源按空行切出每块的行数；把块**连续分组**、
    每组对应日语的一段，每块只有两种宽度 —— **合并成 1 行**（多行用单个空格连接）或
    **原样保留**（宽度＝块行数）。要求：

    * 每段各块宽度之和正好等于该段的行数，且**所有块都要用掉**；
    * 解**唯一**才动手（多解 / 无解返回 None，交给 AI 或原来的路径）；
    * 至少有一次合并（纯 1:1 的排版交给 `align_chs_to_jap()`），日语栏至少两段；
    * 中文行里不能有假名（那是日中混排的来源，得另一条路处理）。

    译文**逐字**保留（只动换行 / 空格 / 空行），失败不会改动任何内容。
    """
    jap_counts = [len(block) for block in _split_blocks(normalize_blank_lines(jap).split("\n"))]
    if len(jap_counts) < 2 or not all(jap_counts):
        return None
    chs_blocks = [block for block in
                  _split_blocks([line.strip() for line in normalize_blank_lines(chs).split("\n")])
                  if block]
    if not chs_blocks or len(chs_blocks) < len(jap_counts):
        return None
    if any(is_japanese_line(line) for block in chs_blocks for line in block):
        return None
    if sum(len(block) for block in chs_blocks) <= sum(jap_counts):
        return None                        # 没有多出来的行：不需要合并
    groups = _unique_block_groups(jap_counts, [len(block) for block in chs_blocks])
    if groups is None:
        return None
    if all(width == len(chs_blocks[index]) for group in groups for index, width in group):
        return None                        # 纯 1:1（没有块需要合并）：交给原来的路径
    result: List[str] = []
    for group in groups:
        if result:
            result.append("")
        for index, width in group:
            block = chs_blocks[index]
            result.append(" ".join(block) if width == 1 else "\n".join(block))
    return "\n".join(result)


def _split_blocks(lines: Sequence[str]) -> List[List[str]]:
    """把若干行按空行切成「块」（块内不含空行）。"""
    blocks: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        if is_empty(line):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _unique_block_groups(jap_counts: Sequence[int],
                         block_counts: Sequence[int]) -> Optional[List[List[Tuple[int, int]]]]:
    """把块分成与日语各段行数对应的组；**唯一解**才返回 `[[(块下标, 宽度), …], …]`。

    宽度只有两种：1（这一块合并成一行）或块自己的行数（原样保留）。多解 / 无解返回 None ——
    宁可退回去让模型判断，也不猜（《幽灵(KAIRUI)》的五块 `3/3/3/4/4` 对 `3/4/4` 只有
    「前三块各合并成一行」这一种分法）。
    """
    solutions: List[List[List[Tuple[int, int]]]] = []
    total = len(block_counts)
    if total > MAX_MERGE_BLOCKS:
        return None                        # 块太多，枚举不划算（也说明来源不是这个形状）

    def walk(stanza: int, start: int, acc: List[List[Tuple[int, int]]]) -> None:
        if len(solutions) > 1:
            return
        if stanza == len(jap_counts):
            if start == total:
                solutions.append([list(group) for group in acc])
            return
        want = jap_counts[stanza]

        def pick(index: int, width: int, chosen: List[Tuple[int, int]]) -> None:
            if len(solutions) > 1 or width > want:
                return
            if width == want:
                walk(stanza + 1, index, acc + [chosen])
                return
            if index >= total:
                return
            pick(index + 1, width + 1, chosen + [(index, 1)])
            size = block_counts[index]
            if size > 1:
                pick(index + 1, width + size, chosen + [(index, size)])

        pick(start, 0, [])

    walk(0, 0, [])
    return solutions[0] if len(solutions) == 1 else None


def _jap_line_keys(jap: str) -> List[str]:
    """日语栏每行的「行指纹」（空白行不参与）。"""
    return [_match_key(line) for line in normalize_blank_lines(jap).splitlines() if line.strip()]


def _looks_japanese(line: str, jap_keys: Sequence[str]) -> bool:
    """这一行像不像**日文原文**。

    光看「有没有假名」不够：日文歌词里有整行只有汉字的句子（《若要讨伐那条龙的话》开头的
    「森羅万象掌握　豪放磊落必勝」「魑魅魍魎一蹴　酩酊酩酊」），跟中文行长得一模一样，
    以前会把它们当成上一句的中文 —— 整段歌词因此错位、最后全堆到中文栏末尾。
    所以再拿**日语栏**当参照：这一行是日语栏某一句的整行 / 前半截 / 后半截时，也算日文。
    """
    if is_japanese_line(line):
        return True
    key = _match_key(line)
    if len(key) < 3:
        return False
    return any(want == key or want.startswith(key) or want.endswith(key) for want in jap_keys)


def _source_pairs(source: List[str],
                  jap_keys: Sequence[str] = ()) -> Tuple[List[Dict[str, str]], List[str]]:
    """「待归类歌词」→ 若干条「日文行 +（它后面那些）译文行」，外加没挂上日文的行。

    返回 `(entries, orphans)`：`entries` 每条是 `{'key', 'line', 'chs'}`（`chs` 可以为空，
    多行译文用空格拼起来），`orphans` 是没有日文行打头的行（开头的说明 / 译注），
    最后由调用方补到中文栏末尾。判断「是不是日文行」见 `_looks_japanese()`。
    """
    entries: List[Dict[str, str]] = []
    orphans: List[str] = []
    index = 0
    while index < len(source):
        line = source[index].strip()
        if not line:
            index += 1
            continue
        if not _looks_japanese(line, jap_keys):
            orphans.append(line)
            index += 1
            continue
        chs_parts: List[str] = []
        index += 1
        while index < len(source):
            following = source[index].strip()
            if not following:
                index += 1
                continue
            if _looks_japanese(following, jap_keys):
                break
            chs_parts.append(following)
            index += 1
        entries.append({"key": _match_key(line), "line": line, "chs": " ".join(chs_parts)})
    return entries, orphans


def _find_jap_pair(entries: List[Dict[str, str]], cursor: int,
                   want: str) -> Optional[Tuple[List[int], str]]:
    """在 `entries[cursor:]` 里找到日语栏这一句，返回 `(吃掉的条目下标, 中文)`。

    先按**精确**匹配找 —— 包括「来源把这一句拆成了好几行」：连续几行的指纹拼起来正好是
    这一句时，把它们一起吃掉、译文用空格拼成一行。都没找到才退回「长得像」（`_similar`，
    来源个别字被改写过时）。
    以前只按单行比对：网易云把一句断成「手に余るデザイア苦しかろうて」「映したまえ」两行、
    而站上的日语栏写成一行时，只能配上前半截的译文，后半截和后面每一句的译文全错开、
    最后统统堆到中文栏末尾（用户 2026-10-22 报的《若要讨伐那条龙的话》）。
    """
    for start in range(cursor, len(entries)):
        key = entries[start]["key"]
        if key == want:
            return [start], entries[start]["chs"]
        acc, taken = key, [start]
        for nxt in range(start + 1, len(entries)):
            if len(acc) >= len(want) + 2:           # 已经拼得比这一句还长，不可能是它
                break
            acc += entries[nxt]["key"]
            taken.append(nxt)
            # 只认**完全拼得上**：「长得像」在这条路上太松了（拼进下一句的指纹后
            # 相似度照样能过 0.6，整段于是被并成一句）
            if acc == want:
                chs = " ".join(entries[pos]["chs"] for pos in taken if entries[pos]["chs"])
                return taken, chs
    for start in range(cursor, len(entries)):
        if _similar(entries[start]["key"], want):
            return [start], entries[start]["chs"]
    return None


def pair_chs_with_jap(text: str, jap: str) -> Optional[str]:
    """日文一行、中文一行交替的「待归类歌词」→ 按**相邻关系**配对出中文栏。

    用户 2026-10-05 报：b 站动态粘过来的日/中交替歌词（开头还有一句「尝试着翻译了一下…」），
    生成的**中文栏没有跟着日语栏的排版走**，整栏从第一行起就顶开了一格，后面每一句中文
    都跑到日语前一句的位置上。原因是按「来源里的顺序」往日语栏的格子里填，
    遇上一句没有对应日文的说明文字（或译注）就把后面全部顶开了。

    这里换成分行**配对**：拿日语栏每一行去来源里找到它自己，紧跟其后那一行就是它的译文
    （前提：它本身不是日文行）；找不到对应日文行的来源行（说明 / 译者注）不算歌词，
    统一排到中文栏**最后**，不会再顶开后面的内容。结果与日语栏**同构**（空行位置一致）。

    **来源把一句拆成好几行**时，把这几行一起吃掉、译文用空格拼成一行（见 `_find_jap_pair()`）。

    来源里还有日语行没被认领时（说明两栏本来就对不上：日语栏另整理过 / 少了重复段）
    返回 None，调用方沿用原译文 —— 绝不把日文行塞进中文栏。句式整块拼在一起时
    （`_interleave_block_pairs`）先对折成一行日文一行中文再配一次。
    """
    jap_lines = normalize_blank_lines(jap).strip("\n").split("\n") if str(jap or "").strip() else []
    if not [line for line in jap_lines if not is_empty(line)]:
        return None
    entries, orphans = _source_pairs(normalize_blank_lines(text).split("\n"), _jap_line_keys(jap))
    if not entries:
        return None
    used: set = set()
    result: List[str] = []
    cursor = 0
    pairs = 0
    # 精确匹配与「长得像」一起找**最早**的那一条：歌里重复的句子很多，先在整个剩余范围里
    # 找精确匹配会跳到很远的同名句，把中间那些只差长音符 / 送假名 / 标点的行全部跳过
    # （用户 2026-10-17 报的《怨灵案件》）。
    for jap_line in jap_lines:
        want = _match_key(jap_line)
        if not want:
            result.append("")
            continue
        match = _find_jap_pair(entries, cursor, want)
        if match is None:
            result.append("")
            continue
        taken, value = match
        used.update(taken)
        cursor = taken[-1] + 1
        if value:
            pairs += 1
        result.append(value)
    wanted = len([line for line in jap_lines if not is_empty(line)])
    if pairs < max(2, wanted // 2 + 1):        # 未过半 → 不足以证明来源是逐行交替
        # 整块原文 + 整块译文（中间没有交替结构）：先对折成一行日文一行中文再配一次
        interleaved = _interleave_block_pairs(text)
        if interleaved is not None:
            return pair_chs_with_jap(interleaved, jap)
        return None
    if any(entry["line"].strip() for index, entry in enumerate(entries) if index not in used):
        return None                            # 还有日文行没认领：这份结果不可靠
    result += orphans
    return "\n".join(result).strip()


def same_line_shape(jap: str, chs: str) -> bool:
    """两栏是不是「一行对一行」：有效行数相同，**空行位置也相同**。

    「行数一样」不等于「逐行对应」（用户 2026-10-29 提醒）：译者把一句拆成三行短句、
    又在别处并掉两句时，两栏的行数照样能相等，位置却全错。空行位置是译者 / 整理者留下的
    段落边界，位置一样才说明两栏真的同步；位置不同就交给 `ai_lyrics.mark_translation()`
    去对齐，别按行号硬搬标记。
    """
    jap_lines = [line.strip() for line in str(jap or "").rstrip().split("\n")]
    chs_lines = [line.strip() for line in str(chs or "").rstrip().split("\n")]
    if len(jap_lines) != len(chs_lines):
        return False
    return all(bool(one) == bool(other) for one, other in zip(jap_lines, chs_lines))


def align_chs_to_jap(text: str, jap: str, chs: str) -> str:
    """中文栏对齐到日语栏：能用**相邻配对**就用（见 `pair_chs_with_jap`），否则按顺序填。

    只有日中行数能对应时才按日语栏重排空行；译文合并 / 拆分了句子时保留 AI
    给出的中文分段，避免从第一处行数差异开始错配后续歌词。

    ⚠️ 「行数能对应」要把**英文行**算进去（用户 2026-10-12 报的《咬人兔》）：
    日语栏有一行英文歌词、中文栏给的是它的译文（`"I miss U."` → `"我想你"`）时，
    两栏行数其实一样 —— 以前按「英文行名对不上」判成缺行，于是整栏都不再对齐，
    中文栏一个分段空行都没有，跟日语栏完全错位。现在只有**中文栏真的少了几行**、
    且少掉的行数能用「英文行没照抄」解释时，才走补英文 + 对齐这条路。
    """
    paired = pair_chs_with_jap(text, jap)
    if paired is not None:
        return normalize_blank_lines(mirror_english_lines(paired, jap))

    jap_lines = [line for line in normalize_blank_lines(jap).splitlines() if not is_empty(line)]
    chs_lines = [line for line in normalize_blank_lines(chs).splitlines() if not is_empty(line)]
    jap_english = Counter(line.strip() for line in jap_lines if is_english_line(line))
    chs_english = Counter(line.strip() for line in chs_lines if is_english_line(line))
    english_gap = sum((jap_english - chs_english).values())     # 中文栏没照抄的英文行数
    missing = len(jap_lines) - len(chs_lines)                  # 中文栏比日语栏少几行
    if missing < 0 or missing > english_gap:
        return normalize_blank_lines(chs).strip()
    return align_blank_lines(mirror_english_lines(chs, jap), jap)


def _walk_blocks(source: List[str], jap_lines: List[str], chs_lines: List[str],
                 hold_back: bool) -> Optional[Tuple[List[List[str]], List[List[str]]]]:
    """按「日语块 + 中文块」逐段吃掉来源，返回两栏的逐段行；对不上返回 None。

    `hold_back=True` 时，某一栏吃到「同时也是另一栏该吃的下一行」的行就让位给另一栏 ——
    歌里的英文行在日语块、中文块里各抄了一份（《告别交信》全曲都是这样），只有让位才能把
    段落切开；`False` 时一路吃到吃不动为止，给前一种走法失败时兜底。
    """
    jap_blocks: List[List[str]] = []
    chs_blocks: List[List[str]] = []
    position = jap_index = chs_index = 0
    # 来源开头不是歌词的行（`作词：…` 这类投稿头部）跳过
    heads = {_match_key(jap_lines[0]), _match_key(chs_lines[0])}
    while position < len(source) and _match_key(source[position]) not in heads:
        position += 1
    while jap_index < len(jap_lines) or chs_index < len(chs_lines):
        jap_run: List[str] = []
        while (jap_index < len(jap_lines) and position < len(source)
               and _match_key(source[position]) == _match_key(jap_lines[jap_index])):
            if (hold_back and jap_run and chs_index < len(chs_lines)
                    and _match_key(source[position]) == _match_key(chs_lines[chs_index])):
                break
            jap_run.append(jap_lines[jap_index])
            jap_index += 1
            position += 1
        chs_run: List[str] = []
        while (chs_index < len(chs_lines) and position < len(source)
               and _match_key(source[position]) == _match_key(chs_lines[chs_index])):
            if (hold_back and chs_run and jap_index < len(jap_lines)
                    and _match_key(source[position]) == _match_key(jap_lines[jap_index])):
                break
            chs_run.append(chs_lines[chs_index])
            chs_index += 1
            position += 1
        if not jap_run and not chs_run:            # 对不上 → 交给原来的逻辑
            return None
        jap_blocks.append(jap_run)
        chs_blocks.append(chs_run)
    return jap_blocks, chs_blocks


def pair_blocks_with_source(text: str, jap: str,
                            chs: str) -> Optional[Tuple[str, str]]:
    """块状交替的来源（日语一整段、中文一整段，逐段交替）→ 给两栏补上段落空行。

    用户 2026-10-12 对照 voca.wiki《告别交信》与它引的巴哈姆特投稿要求：那种投稿是
    「日语一整段 + 中文一整段」逐段交替、**段与段之间没有空行**，段落边界只存在于这个
    交替结构里。模型按「原样保留来源的空行」分栏，结果三栏一个空行都没有 —— 生成出来的
    条目连一个 `#NoHover` 都没有，整首歌词糊成一块（悬停译文也跟着整首错位）。

    做法：拿两栏的行按顺序去**吃掉**来源里的行 —— 先吃掉这一段日语块，再吃掉这一段中文块，
    吃出一段一段来；两栏的行都被吃掉、且确实分出多段时才返回带空行的两栏，否则返回 None
    （交给原来的对齐逻辑，绝不硬凑）。

    逐行交替的来源（日语一行、中文一行）不归这里管 —— 那种形状由 `pair_chs_with_jap`
    处理，这里要求每段的日语行平均在两行以上，免得把逐行交替也切成一段一段。
    """
    jap_lines = [line for line in normalize_blank_lines(jap).splitlines() if not is_empty(line)]
    chs_lines = [line for line in normalize_blank_lines(chs).splitlines() if not is_empty(line)]
    source = [line for line in normalize_blank_lines(text).splitlines() if not is_empty(line)]
    if not jap_lines or not chs_lines or not source:
        return None
    for hold_back in (True, False):
        blocks = _walk_blocks(source, jap_lines, chs_lines, hold_back)
        if blocks is None:
            continue
        jap_blocks, chs_blocks = blocks
        if len(jap_blocks) < 2 or len(jap_lines) < 2 * len(jap_blocks):
            continue                               # 分不出段落 / 看着像逐行交替 → 不动
        if any(abs(len(jap_run) - len(chs_run)) > 3
               for jap_run, chs_run in zip(jap_blocks, chs_blocks)):
            continue                               # 两栏差太多 → 分块没分对，别硬凑
        return ("\n\n".join("\n".join(block) for block in jap_blocks),
                "\n\n".join("\n".join(block) for block in chs_blocks))
    return None


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
        if any(is_kana_letter(c) for c in line):
            layout["jap_line"] = str(line_number + 1)
        if any(is_kanji(c) for c in line) and all(not is_kana_letter(c) for c in line):
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
                 use_colors: bool = False, charas: Sequence[str] = (), jap_text: str = "",
                 use_roma: bool = False, prefill: Optional[dict] = None):
        self._initial_text = initial_text or ""
        self._jap_text = jap_text or ""
        self._source_hint = source_hint or ""
        # 「从链接填充」的结果（流程在打开窗口前先查了 vocaloid中文歌词wiki）：
        # {lyrics, target, translator, translatorUrl, sourceName, sourceUrl, message}
        self._prefill = dict(prefill or {})
        self._use_hover = bool(use_hover)
        self._use_colors = bool(use_colors)
        self._use_roma = bool(use_roma)
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
        """窗口初始内容（供宿主注入）。

        `initial` 是「待归类歌词」栏的预填内容；`jap` 是**日语栏**的预填内容
        （流程里已经取到日语原文时直接放进日语栏，用户只要补译文）。

        流程事先查到了 vocaloid中文歌词wiki 的翻译时（`prefill`），这里连同**「从链接填充」
        会填的东西**一起交出去：`chs`（只有中文的译文直接进中文栏）、`translator` /
        `translatorUrl` / `sourceName` / `sourceUrl` 与一句 `prefillMessage`
        （用户 2026-10-20 要求：切到歌词页就把翻译填好）。
        """
        prefill = dict(self._prefill or {})
        lyrics = str(prefill.get("lyrics") or "").strip()
        initial, chs_text = self._initial_text, ""
        if lyrics and str(prefill.get("target") or "") == "chs":
            chs_text = lyrics
        elif lyrics:
            initial = "\n\n".join(part for part in (self._initial_text.strip(), lyrics) if part)
        return {"initial": initial, "jap": self._jap_text,
                "chs": chs_text,
                "sourceHint": self._source_hint or str(prefill.get("sourceUrl") or ""),
                "translator": str(prefill.get("translator") or ""),
                "translatorUrl": str(prefill.get("translatorUrl") or ""),
                "sourceName": str(prefill.get("sourceName") or ""),
                "sourceUrl": str(prefill.get("sourceUrl") or ""),
                "prefillMessage": str(prefill.get("message") or ""),
                "useHover": self._use_hover, "useColors": self._use_colors,
                "useRoma": self._use_roma,
                "charas": self.chara_options(), "aiLyrics": ai_lyrics.context()}

    def ai_auto(self, payload_json: str) -> dict:
        """AI 分栏 / AI 排版（返回前把译文里的**译者注**转成 `<ref>译者注：…</ref>`）。

        具体逻辑见 `_ai_auto()`；这里只做最后一步 —— 站上的写法是把译者的注释写成行内脚注
        （用户 2026-10-29 给的《image(picco)》：`被骗了 笑不活啦<ref>译者注：日本网络用语…</ref> 别太搞笑了`），
        见 `utils.string.to_note_refs()`（只看括号里的内容，是注释才转，幂等）。
        """
        return self._with_note_refs(self._ai_auto(payload_json))

    @staticmethod
    def _with_note_refs(result: dict, jap: str = "") -> dict:
        chs = result.get("chs")
        if result.get("ok") and isinstance(chs, str) and chs:
            fixed = to_note_refs(chs, str(result.get("jap") or jap))
            if fixed != chs:
                result = {**result, "chs": fixed}
        return result

    def _ai_auto(self, payload_json: str) -> dict:
        """AI 分栏：把混在一起的歌词交给大模型分日语 / 中文 / 罗马音。

        没有「待归类歌词」时**换一件事做**（用户 2026-10-16 要求）：日语栏与中文栏都已经
        有内容、只是行数 / 分段对不上（日语原文由流程预填、译文由「从链接填充」直接进中文栏）
        → 让模型按日语栏给中文栏排好行，见 `ai_layout_chs()`。

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
        * 罗马音栏按日语栏的**分段空行**对齐（`align_blank_lines()`）；
        * 来源是**块状交替**（日语一整段、中文一整段，逐段交替且段间没有空行）时，
          段落边界在两栏里都看不出来 —— 先按来源的块边界把分段补回两栏
          （见 `pair_blocks_with_source()`）；
        * 来源**只有中文、译者自己用空行分块**时（b 站评论区常见），先按日语栏的分段把
          译者的换行合并回去（见 `merge_chs_blocks()`）；合并方式不唯一才交给模型。
        * 来源里带 `#NoHover` 标记（从条目复制出来的 `LyricsKai/hover` 歌词）时先
          还原成空行再交给模型，模型返回的三栏也再清一遍（见 `normalize_no_hover()`）。
        * 日语栏已经有原文时，**先做一次单调对齐**（`source_pairs()` + `align_lyrics()`）：
          合并 / 拆分 / 丢句 / 留空都由打分决定，不需要联网。真机 52 条语料的对齐正确率
          由 13% 提到 90% 靠的就是这一步（见 `tests/utils/test_lyrics_corpus.py` 的记分卡）。
        """
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        # 先还原 `#NoHover`：模型看到的就是标准分段（它的「空行原样保留 / 三栏分段一致」
        # 两条规则都按空行走），返回的三栏里也不会再出现模板标记。
        text = normalize_no_hover(str(data.get("text") or ""))
        given = normalize_no_hover(str(data.get("jap") or ""))
        if text != str(data.get("text") or "") or given != str(data.get("jap") or ""):
            payload_json = json.dumps({**data, "text": text, "jap": given}, ensure_ascii=False)
        if is_empty(text.strip()):
            # 没有「待归类歌词」：日语栏 + 中文栏都已填好时，改成「按日语栏给中文栏排版」
            # （日语原文预填进日语栏、译文从链接直接进了中文栏时就是这个状态）
            chs_given = normalize_no_hover(str(data.get("chs") or ""))
            if not is_empty(given.strip()) and not is_empty(chs_given.strip()):
                return self.ai_layout_chs(given, chs_given)
        if is_instrumental(text):
            return {"ok": False,
                    "error": "这首在网易云那类来源里只有「纯音乐，请欣赏」之类的提示，"
                             "没有歌词可以填（歌词栏保持不动）"}
        # 「待归类歌词」本来就是「一行日文、一行中文」（从链接填充网易云时就是这样，
        # 见 `netease_lyrics()`）：自己分就 100% 对得上，既不用等模型、也不用花一次请求。
        pairs = split_paired_lines(text)
        # 主路：**单调对齐**（合并 / 拆分 / 丢句 / 留空都由打分决定，见 `align_lyrics()`）。
        # 它从逐行定性的来源出发（`source_pairs()`），所以缺译行、记号行、英文行都不再
        # 让整首作废 —— 真机 52 条语料里以前有 38 条根本走不到这里。
        if not is_empty(given.strip()):
            aligned = align_lyrics(given, source_pairs(text, given))
            if aligned is not None:
                return {"ok": True, "mode": "paired",
                        "jap": with_furigana(normalize_blank_lines(given)),
                        "chs": aligned["chs"], "roma": "",
                        "message": _align_message(aligned)}
        if pairs is not None:
            jap_text, chs_text = pairs
            if is_empty(given.strip()):
                chs_value = normalize_blank_lines(chs_text)
                note = ""
                # 逐行配对只说明「谁配谁」：整段一个空行都没有时，段落边界（`#NoHover`）
                # 只能按语义判断 → 让模型只给「在第几行之后空一行」，失败就照原样返回
                arranged = self._arrange_paragraphs(jap_text, chs_value)
                if arranged is not None:
                    jap_text, chs_value = arranged[0], arranged[1]
                    note = arranged[2]
                return {"ok": True, "mode": "paired", "jap": with_furigana(jap_text),
                        "chs": chs_value, "roma": "",
                        "message": "歌词本来就是「一行日文、一行中文」，已直接分栏" + note}
            # 日语栏已经有原文（VocaDB 给的是**合并行**），译文是逐句的：
            # 按日语栏的换行把译文合并成同结构（见 `merge_chs_lines()`）。
            merged = merge_chs_lines(jap_text, chs_text, given)
            if merged is not None:
                return {"ok": True, "mode": "paired", "jap": with_furigana(
                            normalize_blank_lines(given)), "chs": merged, "roma": "",
                        "message": "译文已按日语栏逐行合并对齐（没有改动任何文字）"}
            # 两边的**结构**不一样（来源把重复段写了两遍 / 日语栏把两句并成一行）时，
            # 严格版会拒绝；放宽到「重复的句子共用同一份中文 + 多出来的来源句可以丢」，
            # 见 `align_chs_lines()`（用户 2026-10-29 报的《image(picco)》）。
            aligned = align_chs_lines(given, jap_text, chs_text)
            if aligned is not None:
                chs_value, dropped = aligned
                note = f"，来源里多写的 {dropped} 句重复段已丢掉" if dropped else ""
                return {"ok": True, "mode": "paired", "jap": with_furigana(
                            normalize_blank_lines(given)), "chs": chs_value, "roma": "",
                        "message": f"译文已按日语栏合并对齐（没有改动任何文字{note}），请核对"}
            # 结构确实对不上（例如日语栏比来源多出一整段）：这次要的是「按日语栏排版
            # 逐句译文」，交给排版而不是分栏 —— 分栏那条路会把 28 行混合文本整个丢给模型。
            return self.ai_layout_chs(given, chs_text)
        # 来源是「只有中文、译者自己用空行分块」（b 站评论区常见）：日语栏已有原文时，
        # 按日语栏的分段把译者的换行合并回去 —— 这一步是确定的，见 `merge_chs_blocks()`。
        if (not is_empty(given.strip())
                and not any(is_japanese_line(line) for line in text.split("\n"))):
            merged = merge_chs_blocks(given, text)
            if merged is not None:
                return {"ok": True, "mode": "layout",
                        "jap": with_furigana(normalize_blank_lines(given)),
                        "chs": merged, "roma": "",
                        "message": "译文已按日语栏分段合并对齐（没有改动任何文字），请核对"}
        result = ai_lyrics.recognize(payload_json)
        if result.get("ok"):
            text = normalize_blank_lines(text)
            # 用户自己填了日语栏（「已确认的日语原文」）就以它为准，别用模型那栏顶掉：
            # 模型常把长音符 / 标点「顺手」改对，再拿它去来源里找行就一行都对不上，
            # 中文栏跟着整栏错位（用户 2026-10-07 报）。auto() 也是这么做的。
            given = normalize_blank_lines(given)
            jap = with_furigana(given or normalize_blank_lines(
                normalize_no_hover(str(result.get("jap") or ""))))
            blocks = pair_blocks_with_source(
                text, jap, normalize_blank_lines(
                    normalize_no_hover(str(result.get("chs") or ""))))
            if blocks is not None:
                jap, result["chs"] = blocks
            result["jap"] = jap
            for key in ("chs", "roma"):
                raw = normalize_no_hover(str(result.get(key) or ""))
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

    def _arrange_paragraphs(self, jap: str, chs: str) -> Optional[Tuple[str, str, str]]:
        """整首一个空行都没有时，让 AI 只补段落空行；返回 `(jap, chs, 提示)` 或 None。

        来源是「一行日文、一行中文」的网易云歌词时（`split_paired_lines()` 认出来的形状），
        两栏的配对与行数都已经确定，**唯一缺的就是分段**（条目里写成 `#NoHover`）。
        这件事按时间戳也判不出来（见 `ai_lyrics.PARAGRAPH_SYSTEM_PROMPT` 的说明），
        所以问一次模型要「在第几行之后空一行」；AI 关掉 / 没配密钥 / 模型答不上来时
        返回 None —— 调用方照原样返回不分段的结果，不影响原来的行为。
        """
        if normalize_blank_lines(jap) != str(jap or "").strip():
            return None                        # 已经有空行：分段是现成的
        if normalize_blank_lines(chs) != str(chs or "").strip():
            return None
        if any(not line.strip() for line in str(jap or "").split("\n")):
            return None                        # 中途就有空行：来源自己分了段，别再问
        if len(jap.split("\n")) < PAIRED_MIN_LINES:
            return None
        result = ai_lyrics.arrange_paragraphs(jap, chs)
        if not result.get("ok"):
            logging.info("AI 补分段未生效：%s", result.get("error"))
            return None
        return (str(result.get("jap") or ""), str(result.get("chs") or ""),
                f"；分段由 AI 判断（{result.get('model')}，共 "
                f"{len(result.get('breaks') or []) + 1} 段），请核对")

    def ai_layout_chs(self, jap: str, chs: str) -> dict:
        """按日语栏给中文栏排版（只动换行 / 空行，可以丢掉没有对应行的整行）。

        场景（用户 2026-10-16）：日语原文由流程预填进日语栏、「从链接填充」把**只有中文**
        的译文直接填进了中文栏 —— 没有「待归类歌词」可分，要做的是把译文按日语的行数 /
        分段重新断行（译者把两句合成一句时要并进同一行）。来源把副歌 / 重复段多写了一遍时，
        多出来的那几行要**整行丢掉**（`ai_lyrics.layout_result_ok()` 会核对，见其说明）。

        返回 {'ok': True, 'mode': 'layout', 'chs': …, 'jap': None, 'roma': None, 'message': …}；
        `jap` / `roma` 为 None 表示这两栏不动（界面只覆盖有值的栏）。
        """
        jap = normalize_no_hover(jap)
        chs = normalize_no_hover(chs)
        if is_empty(jap.strip()):
            return {"ok": False, "error": "日语栏是空的：先把日语歌词填上（或先粘到左边分栏）"}
        if is_empty(chs.strip()):
            return {"ok": False, "error": "中文栏是空的：没有可以排版的译文"}
        merged = merge_chs_blocks(jap, chs)
        if merged is not None:
            return self._with_note_refs(
                {"ok": True, "mode": "layout", "jap": None, "chs": merged, "roma": None,
                 "message": "已按日语栏把译者的换行合并（没有改动任何文字），请核对后点「完成」"},
                jap)
        result = ai_lyrics.layout_translation(jap, chs)
        if not result.get("ok"):
            return result
        laid_out = normalize_blank_lines(str(result.get("chs") or ""))
        if is_empty(laid_out.strip()):
            return {"ok": False, "error": "模型没有给出可用的排版结果，请重试"}
        ok, dropped = ai_lyrics.layout_result_ok(chs, laid_out)
        if not ok:
            return {"ok": False,
                    "error": "模型改动了译文内容（排版只该合并 / 丢掉整行），已放弃这次结果；请重试"}
        # 模型常常只把中文重新断行、一个空行都没留（来源本来就是「中文一整块」）——
        # 行数与日语栏对得上时，按日语栏把段落空行补回去（只动空行，不碰任何文字）。
        laid_out = align_chs_to_jap("", jap, laid_out)
        note = f"，丢掉来源里多写的 {dropped} 行重复段" if dropped else ""
        return self._with_note_refs(
            {"ok": True, "mode": "layout", "jap": None, "chs": laid_out, "roma": None,
             "message": f"已按日语栏给中文栏排版（{result.get('model')}{note}），"
                        "请核对后点「完成」"},
            jap)

    def ai_mark_chs(self, payload_json: str) -> dict:
        """按**日语栏的标记**给中文栏打标记（界面上的「按日语标记中文」）。

        两栏**行数与空行位置都一致**时直接按行号照搬（不用联网、瞬间出结果）；
        行数或空行位置对不上（译者把两句合成一句 / 多补一句 / 拆了几行短句）时
        交给大模型对齐行号，再把标记搬过去 —— 行数一样并不等于逐行对应，
        见 `same_line_shape()`（用户 2026-10-29 提醒）。
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
        if same_line_shape(jap, chs):
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
        来源里的 `#NoHover` 标记先还原成空行（见 `normalize_no_hover()`），
        所以分类 / 挑行都按标准分段来，标记也不会落进罗马音栏。
        """
        data = _load_payload(payload_json)
        if data is None:
            return {"ok": False, "error": "参数不是合法 JSON"}
        raw_text = normalize_blank_lines(str(data.get("text") or ""))
        # `#NoHover`（LyricsKai/hover 的段落标记）先还原成空行：不还原的话它是纯 ASCII，
        # 会被当成罗马音行进 roma 栏（见 `normalize_no_hover()`），段落也一起丢。
        text = normalize_blank_lines(normalize_no_hover(raw_text))
        if is_empty(text):
            return {"ok": False, "error": "请先在左边粘贴歌词"}

        jap = normalize_no_hover(str(data.get("jap") or ""))
        if not is_empty(jap):
            # 来源是「一行日文、一行中文」的逐句配对（网易云就是从链接填充来的样子）时，
            # 译文直接按日语栏的换行合并成同结构 —— 比按行挑中文准得多
            # （日语栏是 VocaDB 的合并行、译文是逐句的，见 `merge_chs_lines()`）。
            pairs = split_paired_lines(text)
            merged = merge_chs_lines(pairs[0], pairs[1], jap) if pairs else None
            if merged is not None:
                return {"ok": True, "mode": "extract",
                        "jap": with_furigana(normalize_blank_lines(jap)),
                        "chs": merged, "roma": "",
                        "message": "译文已按日语栏逐行合并对齐（没有改动任何文字）"}
            chs = extract_chs_by_jap(text, jap)
            if not is_empty(chs):
                fixed_jap = with_furigana(normalize_blank_lines(jap))
                return {"ok": True, "mode": "extract",
                        "jap": fixed_jap,
                        "chs": align_chs_to_jap(text, fixed_jap, chs),
                        "roma": align_blank_lines(normalize_blank_lines(
                            normalize_no_hover(str(data.get("roma") or ""))), fixed_jap),
                        "message": "已以日语栏为参照挑出中文行"}

        classified_jap, classified_chs, classified_roma = classify_by_script(text)
        if classified_jap or classified_chs or classified_roma:
            fixed_jap = with_furigana(classified_jap)
            return {"ok": True, "mode": "classify",
                    "jap": fixed_jap,
                    "chs": align_chs_to_jap(text, fixed_jap, classified_chs),
                    "roma": align_blank_lines(classified_roma, fixed_jap),
                    "message": "已按语言自动分类"}

        # 猜行号要按**界面里看到的那份**文本数行（`convert()` 拿的也是它，两边口径一致）
        layout = guess_layout(raw_text)
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
            use_roma=bool(data.get("useRoma")),
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
        """按「来源链接」自动识别翻译者 / 翻译链接 / 来源（见 utils/source_filler.py）。

        顺便判断取回来的歌词该进哪一栏：**只有中文**的（不少 atwiki 页面只写译文、
        网易云也可能只给 `tlyric`）直接进「中文」栏（返回的 `target` 是 `"chs"`），
        中日混杂的交由「待归类歌词」栏分栏（`"source"`）。
        """
        result = source_filler.fill_source(url)
        if result.get("ok") and str(result.get("lyrics") or "").strip():
            result["target"] = "chs" if is_chinese_only(result["lyrics"]) else "source"
        return result

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
                       charas: Sequence[str] = (), jap_text: str = "",
                       use_roma: bool = False, prefill: Optional[dict] = None) -> Optional["Lyrics"]:
    """打开主窗口里的「歌词」页，返回用户确认的 Lyrics；取消 / 界面不可用时返回 None。

    initial_text 预填「待归类歌词」栏；**jap_text 预填「日语」栏**（流程里已经取到日语原文时
    直接放进日语栏，用户只要补译文）。
    use_hover 为「使用 LyricsKai/hover」开关的初始状态（也是窗口关闭、用户未改时的兼容传参）。
    use_colors 为「使用 LyricsKai/colors」开关的初始状态；charas 是本曲歌姬（界面上给每行标演唱者用）。
    use_roma 为「使用 LyricsKai/Roma」开关的初始状态（勾选了界面才显示罗马音栏）。
    source_hint 预填「来源链接」；prefill 是已经取好的「从链接填充」结果（歌词 + 翻译者 + 来源 +
    该进哪一栏），见 `utils.vocadb.prefetch_translation()`。
    """
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，无法打开歌词整理页。")
        return None
    return ui.open_lyrics_editor(initial_text, source_hint, use_hover, use_colors, charas,
                                 jap_text, use_roma, prefill=prefill)
