"""同名条目（消歧义）处理：译名与 Vocawiki 已有条目重名时怎么起名、加模板、建消歧义页。

站内惯例（参考条目：时光机 / 向日葵）：
  * 同名条目的条目名是「歌名(P主名)」，例：`向日葵(Project Lumina)`、`时光机(1640P)`；
    P主名是日文时取 vocadb 的罗马音转写（`八王子P` → `HachiojiP`）；
  * 条目顶部加 `{{About|本条目描述|另一含义|另一条目名}}`（同名条目共 2 个）；
    3 个及以上改为 `{{Otheruseslist|本条目描述|描述1|条目1|描述2|条目2}}`；
  * 消歧义页长这样（放在条目名这个「裸标题」上）：

        '''向日葵'''可以指：

        == 歌曲 ==
        * '''[[向日葵(Teary Planet)]]'''（{{lj|向日葵}}）————[[Teary Planet|…]]制作，[[v flower]]演唱的[[VOCALOID]]日语原创歌曲。

        {{disambig}}

  * 提交时：裸标题已是消歧义页 → 往里面补自己那一行；
    裸标题被另一首歌占用 → **不留重定向**把它移到「歌名(它的P主名)」，再在裸标题建消歧义页；
    然后列出裸标题的链入页面（Special:WhatLinksHere），把这些页面里指向旧条目的链接换到新条目名。

本模块只负责「算」：探测 / 生成文本 / 规划链入替换；真正写维基的调用集中在
plan_actions() / handle_submit() 与 utils/wiki_api.py 里，便于单测。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from utils import wiki_api
from utils.name_converter import get_engine

# 处理方式
MODE_NONE = "none"              # 裸标题没人用，不需要消歧义
MODE_DISAMBIG = "disambig"      # 裸标题已经是消歧义页 → 往里面补自己
MODE_MOVE = "move"              # 裸标题被另一首歌占用 → 移动它 + 建消歧义页
MODE_OCCUPIED = "occupied"      # 裸标题被非歌曲页面占用 → 只改自己的条目名与 About

DISAMBIG_SECTION = "== 歌曲 =="
DISAMBIG_TEMPLATE = "{{disambig}}"
ENTRY_PREFIX = "* '''[["

DISAMBIG_TMPL_RE = re.compile(r"\{\{\s*(disambig|消歧义页?|disambiguation)\s*(?:\||\}\})", re.I)
ENTRY_LINE_RE = re.compile(r"^\*\s*'''\s*\[\[\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]\s*'''", re.M)
JAPANESE_RE = re.compile(r"\{\{\s*lj\s*\|\s*([^}]+?)\s*\}\}", re.I)
SONGBOX_FIELD_RE = re.compile(r"\|\s*([^=\n|]+?)\s*=\s*([^\n]*)")
ENGINE_CATEGORY_RE = re.compile(r"\[\[分类:使用([^\]的]+)的歌曲\]\]")
ASCII_RE = re.compile(r"^[\x20-\x7e]+$")

# 榜单类模板（如 Template:VOCALOID & UTAU Ranking/bricks）用参数拼出链接目标：
#     [[{{{条目|{{{曲名}}}{{{后缀|}}}}}}|查看条目]]
# 所以这类页面的「链入」根本不写 [[ ]]，只是某个参数的值恰好等于条目名。
#   条目 / 条目名 …  → 直接给出链接目标
#   曲名 / 歌名 …    → 显示名，没有后缀时它自己就是链接目标
#   后缀              → 拼在显示名后面（例：曲名=Anti Joker + 后缀=(MaikiP) → Anti Joker(MaikiP)）
ENTRY_PARAMS = ("条目", "条目名", "页面", "页面名", "title", "page", "article")
NAME_PARAMS = ("曲名", "歌名", "歌曲名")
SUFFIX_PARAMS = ("后缀", "後缀")

SUFFIX_VALUE_RE = re.compile(r"(\|\s*(?:" + "|".join(SUFFIX_PARAMS) + r")\s*=\s*)([^\n|]*)")
BLOCK_END_RE = re.compile(r"\n[ \t]*\}\}")


# ---------------------------------------------------------------- 命名

def is_ascii(text: str) -> bool:
    """是否全是 ASCII 可见字符（用来判断「已经是罗马音」）。"""
    return bool(text) and bool(ASCII_RE.match(text))


def romanized(name: str, aliases: Optional[Sequence[str]] = None) -> str:
    """日文名 → vocadb 里的罗马音别名；本身已是拉丁字母 / 找不到别名时原样返回。

    vocadb 的 artist.additionalNames 里第一个 ASCII 名通常就是罗马音转写
    （`八王子P` → `HachioujiP, HachiojiP, 8#Prince…`，`みきとP` → `MikitoP, …`）。
    """
    name = (name or "").strip()
    if not name or is_ascii(name):
        return name
    for alias in aliases or []:
        alias = str(alias or "").strip()
        if alias and is_ascii(alias):
            return alias
    return name


def producer_suffix(producers) -> str:
    """条目名括号里的 P主名：多个用 × 连接（`40mP×164`）。"""
    names = [romanized(getattr(p, "name", p), getattr(p, "name_eng", None)) for p in producers or []]
    return "×".join([name for name in names if name])


def entry_title(name: str, suffix: str) -> str:
    """「歌名(P主名)」；没有 P主名时退回裸标题。"""
    name = (name or "").strip()
    return f"{name}({suffix})" if name and suffix else name


def strip_links(text: str) -> str:
    """去掉 wiki 链接标记：`[[A|B]]` → `B`，`[[A]]` → `A`。"""
    text = re.sub(r"\[\[\s*[^\]|]+\s*\|\s*([^\]]+?)\s*\]\]", r"\1", text or "")
    return re.sub(r"\[\[\s*([^\]]+?)\s*\]\]", r"\1", text)


def join_names(names: Sequence[str]) -> str:
    """多个歌姬 / P主的中文并列写法：2 个用「和」，3 个及以上用「、」。"""
    names = [name for name in names if name]
    if len(names) <= 1:
        return names[0] if names else ""
    if len(names) == 2:
        return f"{names[0]}和{names[1]}"
    return "、".join(names)


# ---------------------------------------------------------------- 条目（同名条目之一）

@dataclass
class Entry:
    """一个同名条目：标题 + 描述（`[[P主]]创作的歌曲`）+ 消歧义页里那一行。"""

    title: str
    description: str = "同名歌曲"
    line: str = ""

    def as_dict(self) -> dict:
        return {"title": self.title, "description": self.description, "line": self.line}


@dataclass
class Plan:
    """同名条目处理计划（生成 wikitext 与提交窗口都用它）。"""

    base_title: str = ""                       # 译名（裸标题）
    our_title: str = ""                        # 本条目实际使用的条目名
    mode: str = MODE_NONE
    others: List[Entry] = field(default_factory=list)
    our_entry: Optional[Entry] = None          # 自己写在消歧义页里的那一行
    note: str = ""
    error: str = ""
    # 提交时的进度标记：让「移动 / 建消歧义页」可以安全重试（失败后再点一次不会重复移动）
    step_moved: bool = False
    step_page_done: bool = False
    backlinks: List[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        """是否需要消歧义处理（要用带 P主名的条目名）。"""
        return self.mode != MODE_NONE and bool(self.our_title)

    @property
    def total(self) -> int:
        """同名条目总数（含自己）。"""
        return len(self.others) + (1 if self.our_title else 0)

    def as_dict(self) -> dict:
        return {
            "needed": self.needed,
            "mode": self.mode,
            "base": self.base_title,
            "title": self.our_title,
            "total": self.total,
            "others": [entry.as_dict() for entry in self.others],
            "note": self.note,
            "error": self.error,
            "action": {"disambig": "edit", "move": "move", "occupied": "none"}.get(self.mode, "none"),
        }


# ---------------------------------------------------------------- 文本生成

def our_entry(song) -> Entry:
    """自己这一行：`* '''[[歌名(P主名)]]'''（{{lj|日文名}}）————[[P主]]制作，[[歌手]]演唱的[[引擎]]日语原创歌曲。`"""
    engines = []
    for vocalist in song.creators.vocalists_str():
        engine = get_engine(vocalist)
        if engine not in engines:
            engines.append(engine)
    title = song.page_name or song.name_chs
    producers = [f"[[{name}]]" for name in song.creators.producers_str()]
    vocalists = [f"[[{name}]]" for name in song.creators.vocalists_str()]
    return Entry(
        title=title,
        description=f"{join_names(producers)}创作的歌曲",
        line=build_entry_line(title, song.name_jap, join_names(producers),
                              join_names(vocalists), engines[0] if engines else "VOCALOID"),
    )


def build_entry_line(title: str, japanese: str, producer_text: str, vocalist_text: str,
                     engine: str) -> str:
    """拼消歧义页里的一行（缺哪部分就少哪段，尽量不留空话）。

    producer_text / vocalist_text 传的是（可带链接的）名字，本函数自己补「制作」「演唱」。
    """
    parts = [f"{ENTRY_PREFIX}{title}]]'''"]
    japanese = (japanese or "").strip()
    if japanese:
        if not JAPANESE_RE.search(japanese):
            japanese = f"{{{{lj|{japanese}}}}}"
        parts.append(f"（{japanese}）")
    body = []
    if producer_text:
        body.append(f"{producer_text}制作")
    # 站内写法是「XX制作，YY演唱的[[引擎]]日语原创歌曲」——「演唱的」直接连引擎
    if vocalist_text and engine:
        body.append(f"{vocalist_text}演唱的[[{engine}]]日语原创歌曲")
    elif vocalist_text:
        body.append(f"{vocalist_text}演唱的歌曲")
    elif engine:
        body.append(f"[[{engine}]]日语原创歌曲")
    tail = "，".join(body)
    parts.append(f"————{tail}。" if tail else "————同名歌曲。")
    return "".join(parts)


def parse_entry(title: str, text: str) -> Entry:
    """从已有条目的 wikitext 里读出 P主 / 演唱 / 日文名 / 引擎，拼出它那一行。"""
    fields = {}
    for key, value in SONGBOX_FIELD_RE.findall(text or ""):
        fields.setdefault(key.strip(), value.strip())
    producer = fields.get("P主", "")
    vocalist = fields.get("演唱", "")
    name = fields.get("歌曲名称", "")
    match = JAPANESE_RE.search(name)
    japanese = match.group(0) if match else name.split("<br")[0].strip()
    engine_match = ENGINE_CATEGORY_RE.search(text or "")
    engine = engine_match.group(1) if engine_match else "VOCALOID"

    producer_text = producer
    vocalist_text = vocalist
    if not (producer or vocalist):
        return Entry(title=title, description="同名条目",
                     line=f"{ENTRY_PREFIX}{title}]]'''————同名条目。")
    return Entry(title=title,
                 description=f"{producer}创作的歌曲" if producer else "同名歌曲",
                 line=build_entry_line(title, japanese, producer_text, vocalist_text, engine))


def parse_entry_lines(text: str) -> List[Entry]:
    """解析消歧义页里已有的条目行（`* '''[[标题]]'''…`）→ [Entry]。"""
    entries = []
    matches = list(ENTRY_LINE_RE.finditer(text or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        line = (text or "")[match.start():end].strip()
        entries.append(Entry(title=match.group(1).strip(), description="同名歌曲", line=line))
    return entries


def disambig_page_text(base_title: str, entries: Sequence[Entry]) -> str:
    """新建消歧义页的正文。"""
    lines = "".join(f"{entry.line}\n" for entry in entries if entry.line)
    return (f"'''{base_title}'''可以指：\n\n"
            f"{DISAMBIG_SECTION}\n"
            f"{lines}\n"
            f"{DISAMBIG_TEMPLATE}\n")


def append_entry_to_disambig(text: str, entry: Entry) -> str:
    """把一行插进已有消歧义页：放在 `{{disambig}}` 之前，没有模板就接在末尾。"""
    text = (text or "").rstrip("\n")
    if entry.title and re.search(r"\[\[\s*" + re.escape(entry.title) + r"\s*(?:\||\]\])", text):
        return text + "\n"              # 已经列过了
    match = DISAMBIG_TMPL_RE.search(text)
    line = entry.line
    if match:
        head = text[:match.start()].rstrip("\n")
        tail = text[match.start():]
        if DISAMBIG_SECTION not in head:
            head += f"\n\n{DISAMBIG_SECTION}"
        return f"{head}\n{line}\n\n{tail}".rstrip("\n") + "\n"
    head = text
    if DISAMBIG_SECTION not in head:
        head += f"\n\n{DISAMBIG_SECTION}"
    return f"{head}\n{line}\n\n{DISAMBIG_TEMPLATE}\n"


def top_template(plan: Optional[Plan]) -> str:
    """条目顶部要加的 {{About}} / {{Otheruseslist}}（不需要时返回空串）。

    参数写法对照站内条目：
        {{About|[[P主]]创作的歌曲|[[另一P主]]创作的歌曲|另一条目名}}
        {{Otheruseslist|本条目描述|描述1|条目1|描述2|条目2}}
    """
    if plan is None or not plan.needed:
        return ""
    ours = plan.our_entry
    ours_desc = ours.description if ours else "本条目"
    if not plan.others:
        # 消歧义页上没有别的条目（例如只有自己）→ 按站内写法指向消歧义页本身，参 Melt(ryo)
        if plan.mode == MODE_DISAMBIG:
            return f"{{{{About|本条目描述={ours_desc}|消歧义页={plan.base_title}}}}}"
        return ""
    if len(plan.others) == 1:                       # 一共两个条目 → About
        other = plan.others[0]
        return f"{{{{About|{ours_desc}|{other.description}|{other.title}}}}}"
    parts = [strip_links(ours_desc)]
    for other in plan.others:
        parts.extend([other.description, other.title])
    return "{{Otheruseslist|" + "|".join(parts) + "}}"


# ---------------------------------------------------------------- 探测

def detect(song, name: Optional[str] = None) -> Plan:
    """探测 Vocawiki 上的同名条目 → Plan。

    只有「裸标题确实被占用」时才用带 P主名的条目名，避免把没有冲突的条目也改名。
    """
    base = (name or song.name_chs or "").strip()
    plan = Plan(base_title=base)
    if not base:
        return plan
    suffix = producer_suffix(song.creators.producers)
    facts = wiki_api.fetch_page_facts(base)
    if not facts.get("ok"):
        plan.note = "无法确认 Vocawiki 上的同名条目，本次按普通条目上传"
        return plan
    if not facts.get("exists"):
        sibling = wiki_api.list_titles_with_prefix(f"{base}(")
        if sibling:
            plan.note = f"站内已有 {'、'.join(sibling)}，但「{base}」未被占用，本次按普通条目上传"
        return plan

    plan.our_title = entry_title(base, suffix)
    if not suffix:
        # 没有 P主名就没法给条目名加后缀，只能提醒用户手动处理（needed 保持 False，不加模板）
        plan.mode = MODE_OCCUPIED
        plan.our_title = ""
        plan.error = f"「{base}」已被占用，但没有 P主名可做消歧义后缀，条目名无法区分"
        plan.note = plan.error
        return plan
    # 自己这一行的标题要等 song.page_name 定下来再算，这里先记下后缀
    if facts.get("disambig"):
        plan.mode = MODE_DISAMBIG
        # 消歧义页里可能已经列着自己（重跑一遍 / 条目已建过），要排掉，否则会把自己当「另一含义」
        plan.others = [entry for entry in _disambig_others(facts.get("text", ""))
                       if entry.title != plan.our_title]
    elif facts.get("song"):
        plan.mode = MODE_MOVE
        plan.others = [parse_entry(base, facts.get("text", ""))]
    else:
        plan.mode = MODE_OCCUPIED
        plan.others = [Entry(title=base, description="同名条目",
                             line=f"{ENTRY_PREFIX}{base}]]'''————同名条目。")]
        plan.note = f"「{base}」已被非歌曲页面占用，只把本条目改名为「{plan.our_title}」"
    return plan


def _disambig_others(text: str) -> List[Entry]:
    """消歧义页里已有的条目：为了拿到 P主 描述，再读一次这些条目的正文。"""
    entries = parse_entry_lines(text)
    texts = wiki_api.fetch_pages_text([entry.title for entry in entries])
    result = []
    for entry in entries:
        body = texts.get(entry.title)
        if body:
            parsed = parse_entry(entry.title, body)
            parsed.line = entry.line or parsed.line
            result.append(parsed)
        else:
            result.append(entry)
    return result


def finish_plan(plan: Plan, song) -> Plan:
    """条目名定下来之后补上自己那一行（detect 之后再调用）。"""
    if plan.needed:
        song.page_name = plan.our_title or plan.base_title
        plan.our_entry = our_entry(song)
    return plan


# ---------------------------------------------------------------- 链入页面

def replace_links(text: str, old_title: str, new_title: str) -> Tuple[str, int]:
    """把 `[[旧名]]` / `[[旧名|显示]]` 换成新名（不动已经是新名的链接）。"""
    if not old_title or not new_title or old_title == new_title:
        return text or "", 0
    pattern = re.compile(r"\[\[\s*" + re.escape(old_title) + r"\s*(\||\]\])")
    return pattern.subn(lambda match: f"[[{new_title}{match.group(1)}", text or "")


def _param_re(params: Sequence[str], value: str) -> "re.Pattern":
    """匹配「|参数名 = 值」且值正好是 value（允许尾随空格、后面跟换行 / | / }}）。"""
    return re.compile(r"(\|\s*(?:" + "|".join(params) + r")\s*=\s*)" + re.escape(value) +
                      r"(?=[ \t]*(?:\n|\||\}\}|$))")


def _block_bounds(text: str, index: int) -> Tuple[int, int]:
    """index 所在的模板块范围（这些榜单模板都是「一行一个参数」，取上一个行首 {{ 到下一个行首 }}）。"""
    start = text.rfind("\n{{", 0, index)
    if start < 0:
        start = text.rfind("{{", 0, index)
    end = BLOCK_END_RE.search(text, index)
    return (start if start >= 0 else 0), (end.start() if end else len(text))


def replace_entry_parameters(text: str, old_title: str, new_title: str) -> Tuple[str, int]:
    """把模板参数里直接写着旧条目名的地方换成新条目名（`|条目 = 旧名`，参榜单模板）。

    这类参数就是链接目标本身，与「后缀」无关，所以可以安全地直接改值。
    """
    if not text or not old_title or old_title == new_title:
        return text or "", 0
    return _param_re(ENTRY_PARAMS, old_title).subn(
        lambda match: match.group(1) + new_title, text)


def replace_song_name_reference(text: str, old_title: str, new_title: str) -> Tuple[str, int]:
    """处理「曲名（+后缀）」拼出来的链接目标（参榜单模板）。

    * 块里没有 后缀 / 条目 参数 → 目标就是曲名本身，在曲名下面补一行 `|条目 = 新名`
      （不动曲名，表格里显示的还是歌名）；
    * 块里 `曲名 + 后缀 == 旧名` → 把后缀换成新名字里对应的一段（同样不动显示名）。
    """
    if not text or not old_title or old_title == new_title:
        return text or "", 0
    count = 0
    # 1) 补 |条目 = 新名（从后往前插入，避免位置失效）
    inserts = []
    for match in _param_re(NAME_PARAMS, old_title).finditer(text):
        start, end = _block_bounds(text, match.start())
        block = text[start:end]
        if "|条目" in block or any(f"|{name}" in block for name in SUFFIX_PARAMS):
            continue                    # 这一块的链接目标不是曲名本身，交给别的规则
        line_start = text.rfind("\n", 0, match.start()) + 1
        indent = re.match(r"[ \t]*", text[line_start:]).group(0)
        inserts.append((text.find("\n", match.end()), f"\n{indent}|条目 = {new_title}"))
    for position, insertion in sorted(inserts, reverse=True):
        if position < 0:
            continue
        text = text[:position] + insertion + text[position:]
        count += 1
    # 2) 后缀那一行：曲名 + 后缀 == 旧名 时换成新名对应的后缀
    for match in SUFFIX_VALUE_RE.finditer(text):
        start, end = _block_bounds(text, match.start())
        block = text[start:end]
        name_match = re.search(r"\|\s*(?:" + "|".join(NAME_PARAMS) + r")\s*=\s*([^\n|]*)", block)
        if not name_match:
            continue
        if "|条目" in block:
            continue
        song_name = name_match.group(1).strip()
        if song_name + match.group(2).strip() != old_title or not new_title.startswith(song_name):
            continue
        new_suffix = new_title[len(song_name):]
        text = text[:match.start(2)] + new_suffix + text[match.end(2):]
        count += 1
    return text, count


def fix_page_text(text: str, old_title: str, new_title: str) -> Tuple[str, int, str]:
    """把一页里指向旧条目的引用改成新条目名 → (新正文, 改了几处, 改的是哪种写法)。"""
    if not text or not old_title or old_title == new_title:
        return text or "", 0, ""
    new_text, count = replace_links(text, old_title, new_title)
    if count:
        return new_text, count, "wiki 链接"
    new_text, count = replace_entry_parameters(text, old_title, new_title)
    if count:
        return new_text, count, "模板参数（条目）"
    new_text, count = replace_song_name_reference(text, old_title, new_title)
    if count:
        return new_text, count, "模板参数（曲名 / 后缀）"
    return text, 0, ""


def snippet(text: str, old_title: str, width: int = 60) -> str:
    """链入位置周围的一小段上下文（`[[旧名]]` 或 `|条目 = 旧名` 都认）。"""
    index = text.find(f"[[{old_title}")
    if index < 0:
        for name in ENTRY_PARAMS:
            index = text.find(f"|{name}", 0)
            while index >= 0:
                line_end = text.find("\n", index)
                line = text[index:line_end if line_end >= 0 else len(text)]
                if old_title in line:
                    break
                index = text.find(f"|{name}", index + 1)
            if index >= 0:
                break
    if index < 0:
        return ""
    start = max(0, index - width // 2)
    return text[start:index + len(old_title) + width].replace("\n", " ⏎ ").strip()


def plan_backlinks(old_title: str, new_title: str, titles: Sequence[str]) -> List[dict]:
    """预览：每个链入页面里能替换几处（不写维基，正文一次批量读回）。"""
    titles = [title for title in titles if title]
    texts = wiki_api.fetch_pages_text(titles)
    result = []
    for title in titles:
        text = texts.get(title, "")
        _, count, kind = fix_page_text(text, old_title, new_title)
        result.append({"title": title, "count": count, "kind": kind,
                       "snippet": snippet(text, old_title),
                       "reason": "" if count else (
                           f"没找到 [[{old_title}]] 或 |条目 = {old_title} 形式的引用"
                           "（可能通过模板 / 模块链入）")})
    return result


def apply_backlinks(old_title: str, new_title: str, titles: Sequence[str],
                    summary: str = "修正同名条目的内部链接") -> List[dict]:
    """真的去改：逐页替换并保存，返回每页结果（支持 wiki 链接与模板参数两种写法）。"""
    texts = wiki_api.fetch_pages_text(titles)
    results = []
    for title in titles:
        text = texts.get(title)
        if text is None:
            results.append({"title": title, "ok": False, "error": "读不到页面内容"})
            continue
        new_text, count, kind = fix_page_text(text, old_title, new_title)
        if not count:
            results.append({"title": title, "ok": False, "error": "没有可替换的引用"})
            continue
        outcome = wiki_api.edit_page(title, new_text, f"{summary}（{kind}）")
        results.append({"title": title, "ok": bool(outcome.get("ok")), "count": count,
                        "kind": kind,
                        "error": "" if outcome.get("ok") else str(outcome.get("error", "编辑失败"))})
    return results


# ---------------------------------------------------------------- 提交时的动作

def handle_submit(plan: Plan, summary: str = "同名条目消歧义") -> dict:
    """提交条目**之前**做的事：修订消歧义页，或移动旧条目并新建消歧义页。

    返回 {'ok', 'steps': [说明…], 'backlinks': [标题…], 'error'}；
    backlinks 是移动前抓的裸标题链入列表，供窗口里逐页确认替换。
    plan.step_moved / step_page_done 让重试安全：移动成功后再点一次不会重复移动。
    """
    if not plan.needed:
        return {"ok": True, "steps": [], "backlinks": []}
    if plan.mode == MODE_OCCUPIED:
        return {"ok": True, "steps": [plan.note or f"「{plan.base_title}」被占用，本条目使用「{plan.our_title}」"],
                "backlinks": []}
    if not plan.our_entry:
        return {"ok": False, "steps": [], "backlinks": [], "error": "条目名还没定，无法处理同名条目"}

    base = plan.base_title
    steps: List[str] = []
    if plan.mode == MODE_DISAMBIG:
        if plan.step_page_done:
            return {"ok": True, "steps": [f"消歧义页「{base}」已经处理过了"], "backlinks": []}
        text = wiki_api.fetch_pages_text([base]).get(base, "")
        if not text:
            return {"ok": False, "steps": steps, "backlinks": [], "error": f"读不到消歧义页「{base}」"}
        new_text = append_entry_to_disambig(text, plan.our_entry)
        if new_text.strip() == text.strip():
            plan.step_page_done = True
            return {"ok": True, "steps": [f"消歧义页「{base}」里已有「{plan.our_title}」，跳过"],
                    "backlinks": []}
        outcome = wiki_api.edit_page(base, new_text, summary)
        if not outcome.get("ok"):
            return {"ok": False, "steps": steps, "backlinks": [],
                    "error": f"修订消歧义页失败：{outcome.get('error')}"}
        plan.step_page_done = True
        return {"ok": True, "steps": [f"已在消歧义页「{base}」里补上「{plan.our_title}」"],
                "backlinks": []}

    # MODE_MOVE：先抓链入，再不留重定向移动旧条目，最后在裸标题建消歧义页
    other = plan.others[0] if plan.others else None
    if other is None:
        return {"ok": False, "steps": steps, "backlinks": [], "error": "找不到要移动的旧条目"}
    if not plan.backlinks:
        plan.backlinks = wiki_api.fetch_backlinks(base)
    if not plan.step_moved:
        moved = wiki_api.move_page(base, other.title,
                                   f"为「{plan.our_title}」让出标题，改名为「{other.title}」")
        if not moved.get("ok"):
            return {"ok": False, "steps": steps, "backlinks": plan.backlinks,
                    "error": f"移动「{base}」失败：{moved.get('error')}"}
        plan.step_moved = True
        steps.append(f"已把「{base}」移动到「{other.title}」（不留重定向）")
    if not plan.step_page_done:
        page = disambig_page_text(base, [*plan.others, plan.our_entry])
        created = wiki_api.edit_page(base, page, summary, create_only=True)
        if not created.get("ok"):
            steps.append(f"创建消歧义页失败：{created.get('error')}")
            return {"ok": False, "steps": steps, "backlinks": plan.backlinks,
                    "error": str(created.get("error"))}
        plan.step_page_done = True
        steps.append(f"已创建消歧义页「{base}」")
    return {"ok": True, "steps": steps, "backlinks": plan.backlinks}
