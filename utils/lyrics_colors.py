"""{{LyricsKai/colors}} 的支持：歌姬颜色（voca.wiki 的 Module:Vocalist_Colors）+ 逐行演唱者标记。

voca.wiki 上真实条目的写法（见「前へ！」等条目）：

    {{LyricsKai/colors/hover
    |colors= #72A6C0; #d14f58; #ED6772; #827595; #A1D6B7; #D93A49; lg(left, #72A6C0, #827595); co(#72A6C0, #d14f58, #ED6772, #827595, #A1D6B7, #D93A49)
    |charas= 宮舞モカ；Ryo；Mai；フリモメン；花隈千冬；重音テト；宮舞モカ+Ryo(@nolink)；合唱(@nolink)
    |traColors= on
    |charaBlock= on
    |original=
    @1ああ　またダメだったな
    …
    }}

规则（据 Template:LyricsKai/colors/doc）：
  * `colors` 与 `charas` 一一对应（`;` 分隔 colors、全角 `；` 分隔 charas）；
  * 歌词里 `@n` 表示第 n 个颜色，作用到下一个标记之前；
  * 组合用渐变色 `lg(left, 色1, 色2…)`，合唱（全员）用交替色 `co(色1, 色2…)`；
  * 组合的名字不属于真人，按文档加 `(@nolink)` 阻止 charaBlock 自动加链接；
  * `traColors= on` 让翻译栏也解析标记，`charaBlock= on` 生成角色颜色提示栏；
  * 一行里可以出现多个标记（`@6未来は@1誰も知らない`）：每个标记一直生效到下一个标记，
    所以「行内分段」就是给同一行切几刀、每段各选演唱者（见 splits / line_cuts）。

本模块负责：抓取 / 解析颜色表、按「每行有谁唱」生成 charas / colors 与行首标记。
"""
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils.string import is_empty

MODULE_TITLE = "Module:Vocalist_Colors"
REQUEST_TIMEOUT = 60
RETRY_TIMES = 3                     # voca.wiki 偶发 SSL / 连接错误，见 family_template 的同款做法

DEFAULT_COLOR = "#333333"          # 与模块里的 defaultColor 一致（认不出歌姬时的兜底）
CHORUS_NAME = "合唱"               # 全员合唱的组合名
NO_LINK = "(@nolink)"              # 组合名不是真人，阻止 charaBlock 自动加链接
COLOR_SEPARATOR = "; "             # colors 用半角分号（空格随意，模板会自动去首尾空白）
CHARA_SEPARATOR = "；"             # charas 用全角分号
NO_HOVER = "#NoHover"
TRACK_JAP = "jap"                  # 行内切分点按栏分开存：日语栏
TRACK_CHS = "chs"                  # 行内切分点按栏分开存：中文栏

_LUA_ENTRY_RE = re.compile(r"\{([^{}]*)\}")
_LUA_STRING_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

_colors_cache: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------- 颜色表

def parse_module(text: str) -> List[Tuple[str, List[str]]]:
    """解析 Module:Vocalist_Colors 里的颜色表 → [(颜色, [歌姬名, 别名…]), …]。

    表里一行一个条目（`{'#d93a49','重音Teto','重音テト','teto'},`），
    但这里不依赖换行：按大括号切段后取段内所有字符串，第一个是颜色、其余是名字。
    """
    body = "\n".join(line.split("--")[0] for line in (text or "").splitlines())
    entries: List[Tuple[str, List[str]]] = []
    for match in _LUA_ENTRY_RE.finditer(body):
        strings = [a or b for a, b in _LUA_STRING_RE.findall(match.group(1))]
        if len(strings) < 2 or not strings[0].startswith("#"):
            continue
        entries.append((strings[0], [name.strip() for name in strings[1:] if name.strip()]))
    return entries


def normalize(name: str) -> str:
    """与模块里的 normalizeSinger 一致：下划线/全角下划线当空格、去首尾、转大写。"""
    return re.sub(r"[_\uff3f]", " ", str(name or "")).strip().upper()


def color_lookup(entries: Sequence[Tuple[str, List[str]]]) -> Dict[str, str]:
    """[(颜色, [名字…])] → {归一化名字: 颜色}（同名时保留先出现的）。"""
    table: Dict[str, str] = {}
    for color, names in entries:
        for name in names:
            table.setdefault(normalize(name), color)
    return table


def fetch_colors(refresh: bool = False) -> Dict[str, str]:
    """从 voca.wiki 取颜色表 → {归一化歌姬名: 颜色}；失败时返回上次结果（可能为空）。"""
    global _colors_cache
    if _colors_cache is not None and not refresh:
        return _colors_cache
    from utils import login
    for attempt in range(RETRY_TIMES):
        try:
            response = login.get_api_session().get(login.api_url(), params={
                "action": "query", "format": "json", "prop": "revisions", "rvprop": "content",
                "rvslots": "main", "titles": MODULE_TITLE}, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages", {})
            for page in pages.values():
                if page.get("missing"):
                    continue
                revision = (page.get("revisions") or [{}])[0]
                slot = (revision.get("slots") or {}).get("main") or {}
                text = slot.get("content") or slot.get("*") or revision.get("*") or ""
                table = color_lookup(parse_module(text))
                if table:
                    _colors_cache = table
                    return table
            logging.warning("颜色表 %s 里没找到颜色条目", MODULE_TITLE)
        except Exception as e:                   # 网络失败不该影响生成
            logging.warning("无法获取歌姬颜色表（%s，第 %d 次）：%s", MODULE_TITLE, attempt + 1, e)
            time.sleep(0.4 * (attempt + 1))
    return _colors_cache or {}


def color_of(name: str, table: Optional[Dict[str, str]] = None) -> str:
    """歌姬颜色；认不出时给模块的默认色。"""
    lookup = fetch_colors() if table is None else table
    return lookup.get(normalize(name)) or DEFAULT_COLOR


# ---------------------------------------------------------------- charas / colors

@dataclass
class CharasPlan:
    """一次生成用到的 charas / colors 与每行标记。"""

    charas: List[str] = field(default_factory=list)          # 名称（组合项带 (@nolink)）
    colors: List[str] = field(default_factory=list)          # 与 charas 一一对应
    line_index: Dict[int, List[Optional[int]]] = field(default_factory=dict)
    # ↑ 行下标(0 起) → 该行「每一段」用的 @n（None = 这一段不写标记）；不分段时就是一项
    line_cuts: Dict[int, Dict[str, List[int]]] = field(default_factory=dict)
    # ↑ 行下标 → {栏: [行内切分偏移…]}，偏移表示「在第几个字符前面切开」

    @property
    def available(self) -> bool:
        return bool(self.charas)

    def charas_text(self) -> str:
        return CHARA_SEPARATOR.join(self.charas)

    def colors_text(self) -> str:
        return COLOR_SEPARATOR.join(self.colors)


def _as_segments(value) -> List[List[str]]:
    """把 marks 的一个值统一成「段列表」。

    `[名字…]` 当成整行一段；`[[名字…], [名字…]]` 是行内分段（每段一组名字）。
    """
    if not value or isinstance(value, (str, bytes)):
        return []
    items = list(value)
    if all(isinstance(item, str) for item in items):
        return [items]
    return [list(seg) for seg in items if isinstance(seg, (list, tuple))]


def _cut_tracks(value) -> Dict[str, List[int]]:
    """把 splits 的一个值统一成 {栏: [偏移…]}（直接给列表就当成日语栏）。"""
    if not value:
        return {}
    source = value if isinstance(value, dict) else {TRACK_JAP: value}
    tracks: Dict[str, List[int]] = {}
    for track, cuts in source.items():
        if isinstance(cuts, (str, bytes)) or not isinstance(cuts, (list, tuple, set)):
            continue
        offsets = set()
        for cut in cuts:
            try:
                offsets.add(int(cut))
            except (TypeError, ValueError):
                continue
        if offsets:
            tracks[str(track)] = sorted(offsets)
    return tracks


def _entry_index(plan: CharasPlan, picked: List[str], names: List[str],
                 singer_colors: List[str], table: Optional[Dict[str, str]]) -> Optional[int]:
    """一段歌词的演唱者 → charas 里的序号（1 起）；这一段没选人就返回 None。"""
    if not picked:
        return None
    if len(picked) == 1:
        return names.index(picked[0]) + 1
    if len(picked) == len(names):
        combo, color = CHORUS_NAME, "co(" + ", ".join(singer_colors) + ")"
    else:
        combo = "+".join(picked)
        color = "lg(left, " + ", ".join(color_of(name, table) for name in picked) + ")"
    entry = combo + NO_LINK
    if entry not in plan.charas:
        plan.charas.append(entry)
        plan.colors.append(color)
    return plan.charas.index(entry) + 1


def build_plan(singers: Iterable[str], marks: Optional[Dict] = None,
               table: Optional[Dict[str, str]] = None,
               splits: Optional[Dict] = None) -> CharasPlan:
    """按「每行标了谁」生成 charas / colors / 行内标记。

    singers：vocadb 顺序的歌姬名（去重保序），它们构成 charas 的前几项；
    marks：{行下标: [歌姬名…]}，一行可以多个歌姬 ——
      1 个 → 直接用该歌姬的序号；
      全员 → charas 里加一项「合唱(@nolink)」，颜色用交替色 co(所有歌姬的颜色)；
      其余多个 → 加一项「A+B(@nolink)」，颜色用渐变色 lg(left, 这些歌姬的颜色…)。
      值也可以是 [[歌姬名…], [歌姬名…]]：同一行按行内分段各选各的。
    splits：{行下标: {栏: [字符偏移…]}}（栏是 jap / chs），偏移 = 「在第几个字符前面切开」，
      每一栏各存各的，所以两栏可以用不同的切分位置。
    认不出的歌姬名会被忽略（不写进 charas）。
    """
    names: List[str] = []
    for singer in singers or []:
        if not is_empty(str(singer)) and singer not in names:
            names.append(singer)

    plan = CharasPlan(charas=list(names),
                      colors=[color_of(name, table) for name in names])
    singer_colors = list(plan.colors)        # 全员合唱的交替色只用歌姬本人的颜色
    for raw_line, marked in (marks or {}).items():
        try:
            line = int(raw_line)
        except (TypeError, ValueError):
            continue
        picks = [_entry_index(plan, [name for name in names if name in segment], names,
                              singer_colors, table)
                 for segment in _as_segments(marked)]
        if not any(pick is not None for pick in picks):
            continue
        plan.line_index[line] = picks
        tracks = _cut_tracks((splits or {}).get(raw_line) or (splits or {}).get(line))
        if tracks:
            plan.line_cuts[line] = tracks
    return plan


def mark_lines(text: str, plan: CharasPlan, track: str = TRACK_JAP) -> str:
    """给每行加 `@n` 标记（空行与 #NoHover 行不动）。

    track 说明这份文本是哪一栏（jap / chs）：行内分段只按该栏自己的切分点切，
    所以两栏切在不同位置也没问题；某一栏没切分过时整行只用第一段的颜色。
    """
    if is_empty(text) or not plan.line_index:
        return text
    out = []
    for index, line in enumerate(text.split("\n")):
        pieces = plan.line_index.get(index)
        if not pieces or is_empty(line) or line.strip() == NO_HOVER:
            out.append(line)
            continue
        cuts = (plan.line_cuts.get(index) or {}).get(track) or []
        out.append(_mark_line(line, pieces, cuts))
    return "\n".join(out)


def _mark_line(line: str, pieces: Sequence[Optional[int]], cuts: Sequence[int]) -> str:
    """按切分点把一行拆成几段，每段前面写 `@n`（这一段没选人就不写，颜色延续上一段）。"""
    length = len(line)
    bounds = sorted({cut for cut in cuts if 0 < cut < length})
    count = min(len(pieces), len(bounds) + 1)     # 段落比文本多时，多出来的没地方放，忽略
    edges = [0] + bounds[:count - 1] + [length]
    chunks = []
    for seg in range(count):
        chunk = line[edges[seg]:edges[seg + 1]]
        number = pieces[seg]
        chunks.append(f"@{number}{chunk}" if number and chunk else chunk)
    return "".join(chunks) or line


def build_colors_params(singers: Iterable[str], marks: Optional[Dict] = None,
                        table: Optional[Dict[str, str]] = None,
                        splits: Optional[Dict] = None) -> Tuple[CharasPlan, str]:
    """给 wikitext 用的 (plan, 参数块)；没有可用歌姬时参数块是空串。"""
    plan = build_plan(singers, marks, table, splits)
    if not plan.available:
        return plan, ""
    return plan, (f"|colors= {plan.colors_text()}\n"
                  f"|charas= {plan.charas_text()}\n"
                  "|traColors= on\n"
                  "|charaBlock= on\n")
