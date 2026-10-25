"""同名条目（消歧义）处理：译名与 Vocawiki 已有条目重名时怎么起名、加模板、建消歧义页。

站内惯例（参考条目：时光机 / 向日葵 / 我爱你 / Redial）：
  * 同名条目的条目名是「歌名(P主名)」，例：`向日葵(Project Lumina)`、`Redial(鹤三)`、`Redial(kz)`；
    P主名的写法**按站上**（用户 2026-10-15 定的口径，见 `producer_title_name()`）：
    日文汉字转成站上的中文写法（`鶴三` → `鹤三`、`沢田` → `泽田`），假名写罗马音
    （`柊キライ` → `柊kirai`、`みきとP` → `MikitoP`），名字在站上是重定向就用真页面名
    （`kz(livetune)` → `kz`）；结尾的 `P` 照写（`八王子P` 不变）；
    **P主有两位时只取第一位**（`|P主` 写两位、条目名括号里只写第一位 —— 移动日志 logid 112233
    把 `我爱你(Mealerrand×Rakuda)` 改成了 `我爱你(Mealerrand)`，原因「錯名」）；
  * 条目顶部加 `{{About|本条目描述|另一含义|另一条目名}}`（同名条目共 2 个）；
    3 个及以上改为 `{{Otheruseslist|本条目描述|描述1|条目1|描述2|条目2}}`；
  * 消歧义页长这样（放在条目名这个「裸标题」上）：

        '''向日葵'''可以指：

        == 歌曲 ==
        * '''[[向日葵(Teary Planet)]]'''（{{lj|向日葵}}）————[[Teary Planet|…]]制作，[[v flower]]演唱的[[VOCALOID]]日语原创歌曲。

        {{disambig}}

  * 提交时：裸标题已是消歧义页 → 往里面补自己那一行，**并检查页面上已有的各条目**，
    缺 `{{About}}` / 标题模板就补上（不是歌曲条目 —— 读不出 P主 —— 的跳过，见 `fix_other_entries()`）；
    裸标题被另一首歌占用 → **不留重定向**把它移到「歌名(它的P主名)」，再在裸标题建消歧义页；
    然后列出裸标题的链入页面（Special:WhatLinksHere），把这些页面里指向旧条目的链接换到新条目名 ——
    只换链接**目标**，显示名保持原样（裸链接 `[[涅槃]]` → `[[涅槃(Yunosuke)|涅槃]]`）。

本模块只负责「算」：探测 / 生成文本 / 规划链入替换；真正写维基的调用集中在
plan_actions() / handle_submit() 与 utils/wiki_api.py 里，便于单测。
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from utils import wiki_api
from utils import vocadb
from utils.japanese import kana_to_romaji
from utils.name_converter import engines_of, name_to_wiki
from utils.string import title_match_key

# 处理方式
MODE_NONE = "none"              # 裸标题没人用，不需要消歧义
MODE_DISAMBIG = "disambig"      # 裸标题已经是消歧义页 → 往里面补自己
MODE_MOVE = "move"              # 裸标题被另一首歌占用 → 移动它 + 建消歧义页
MODE_OCCUPIED = "occupied"      # 裸标题被非歌曲页面占用 → 只改自己的条目名与 About

DISAMBIG_SECTION = "== 歌曲 =="
DISAMBIG_TEMPLATE = "{{disambig}}"
ENTRY_PREFIX = "* '''[["
# 旧条目（让出裸标题的那首）顶部要补的两样：标题格式化（把标题里的括号后缀去掉再显示）
# 与指向本条的 {{About}}。参 voca.wiki《Redial(kz)》修订 272305（用户 2026-10-16 给的差异）。
TITLE_FORMAT_TEMPLATE = "{{标题格式化}}"
# 页面自己已经有的「标题模板」（标题替换 / 标题格式化）——有了就别再插标题格式化，
# 否则两个模板抢显示标题（例：涅槃(Yunosuke) 自己写着 {{标题替换|{{lj|ネハン}}}}）。
# 站上只有简体的两个模板（Template:标题替换 / Template:标题格式化），但条目里也有把「标」写成
# 「標」的（例：Identity(Kanaria) 写着 {{標題替换|…}}）——看到就当作已经有了，别重复插。
TITLE_TEMPLATE_RE = re.compile(
    r"\{\{\s*(?:[标標][题題](?:格式化|替换|替換)|TitleFormat|DISPLAYTITLE)\s*(?:\||\}\})", re.I)
# 标题里的括号后缀（`Redial(kz)` → `Redial`）：只有这种标题才需要 {{标题格式化}}
TITLE_SUFFIX_RE = re.compile(r"[(（][^()（）]*[)）]\s*$")
# 顶部已有的消歧义指引模板（About / Otheruseslist / 消歧义）
HATNOTE_TEMPLATE_RE = re.compile(
    r"\{\{\s*(About|Other ?uses|Otheruseslist|消歧义|Disambig|Redirect)\s*(?:\||\}\})", re.I)

DISAMBIG_TMPL_RE = re.compile(r"\{\{\s*(disambig|消歧义页?|disambiguation)\s*(?:\||\}\})", re.I)
ENTRY_LINE_RE = re.compile(r"^\*\s*'''\s*\[\[\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]\s*'''", re.M)
JAPANESE_RE = re.compile(r"\{\{\s*lj\s*\|\s*([^}]+?)\s*\}\}", re.I)
SONGBOX_FIELD_RE = re.compile(r"\|\s*([^=\n|]+?)\s*=\s*([^\n]*)")
ENGINE_CATEGORY_RE = re.compile(r"\[\[分类:使用([^\]的]+)的歌曲\]\]")
ASCII_RE = re.compile(r"^[\x20-\x7e]+$")
# 假名 / 汉字（判断 P主名里哪一段要转罗马音、哪一段要转中文，见 `producer_title_name`）
KANA_RE = re.compile(r"[\u3041-\u3096\u309d-\u309f\u30a1-\u30fa\u30fd-\u30ff]")
KANJI_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

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


def same_reading_alias(name: str, aliases: Optional[Sequence[str]] = None) -> str:
    """别名里和这个名字**读音对得上**的 ASCII 写法（`みきとP` + `MikitoP` → `MikitoP`）。

    ⚠️ 别名不能直接取第一个 ASCII 名：VocaDB 是**按名字模糊搜**的，实测
    `utils.vocadb.artist_aliases("ミク")` 给回 `Mr.Sm@LL`、`("やなぎなぎ")` 给回 `Yanagi`，
    都不是这个人。所以先算这个名字的假名罗马音，只认**包含这个读音**的别名；
    名字里有汉字（读音算不出来）就不认别名。
    """
    reading = kana_to_romaji(name or "").lower()
    if not reading or not reading.isascii():
        return ""
    for alias in aliases or []:
        alias = str(alias or "").strip()
        if alias and is_ascii(alias) and reading in alias.lower().replace(" ", ""):
            return alias
    return ""


def first_producer_name(producer: str) -> str:
    """`|P主` 文本 → **第一位** P主（去掉链接与 `{{lj|…}}`、再按下标分隔符切开）。

    `|P主` 可以是多个人（站上写法：`[[Mealerrand]]&{{lj|[[駱駝法師]]}}`），
    而条目名括号里只写第一位（见 `producer_suffix()`）。
    """
    plain = strip_language_wrapper(strip_links(producer or ""))
    return re.split(r"[&＆、,，×]", plain)[0].strip()


def is_kana_only(name: str) -> bool:
    """整名只看得到假名（`みきとP`、`キライ`）—— 这种名字站上一般有罗马音别名。"""
    return bool(KANA_RE.search(name or "")) and not KANJI_RE.search(name or "")


def producer_title_name(name: str, aliases: Optional[Sequence[str]] = None) -> str:
    """P主名 → 条目名括号里写的名字（用户 2026-10-15 定的口径）。

    1. 名字在站上是**重定向** → 用真页面名：`kz(livetune)` → `kz`（线上 `Redial` 那首歌的
       `|P主` 就是 `[[livetune|kz(livetune)]]`，这条重定向指向真条目 `kz`）、
       `みきとP` → `MikitoP`、`ピノキオピー` → `匹诺曹P`；
    2. 其余按**站上的写法**来：日文汉字 → 中文汉字（`wiki_api.simplified` 走站点自己的变体
       转换：`鶴三` → `鹤三`、`稲葉曇` → `稻叶昙`、`積木P` → `积木P`），工具不自造转换表；
    3. 假名 → **罗马音**（`japanese.kana_to_romaji`）：`柊キライ` → `柊kirai`、`蜂屋ななし` →
       `蜂屋nanashi`（站内条目就是这么写的）；整名都是假名时优先用同音的别名
       （`same_reading_alias`），`みきとP` → `MikitoP`（站上是 `同级生(MikitoP)`）。
    结尾的 `P` 不删：站上就是 `八王子P` / `MikitoP`。
    """
    name = (name or "").strip()
    if not name:
        return ""
    resolved = wiki_api.redirect_targets([name]).get(name) or name
    if is_ascii(resolved):
        return resolved
    chinese = wiki_api.simplified(resolved) or resolved
    if not KANA_RE.search(chinese):
        return chinese
    converted = kana_to_romaji(chinese)
    if KANJI_RE.search(chinese):
        return converted                                 # 汉字留着，假名写罗马音（`柊kirai`）
    return same_reading_alias(resolved, aliases) or converted


def producer_suffix(producers) -> str:
    """条目名括号里的 P主名：**只取第一位 P主**，写法按 `producer_title_name()`。

    用户 2026-10-14 按站上的移动日志（logid 112233）修订：`我爱你(Mealerrand×Rakuda)`
    被管理员移到 `我爱你(Mealerrand)`（原因「錯名」）—— 那条的 `|P主` 是
    `[[Mealerrand]]&{{lj|[[駱駝法師]]}}` 两位，而条目名括号里只写第一位。
    旧实现把两位用 `×` 连起来（`40mP×164`），站上并不这么写。

    ⚠️ 站上还有**社区昵称**式的例外（`时光机(1640P)` 就是 40mP + 164 两个人），
    那是约定俗成的叫法，工具推不出来 —— 需要时在提交页手改条目名。
    """
    for producer in producers or []:
        name = producer_title_name(getattr(producer, "name", producer),
                                   getattr(producer, "name_eng", None))
        if name:
            return name
    return ""


def entry_title(name: str, suffix: str) -> str:
    """「歌名(P主名)」；没有 P主名时退回裸标题。"""
    name = (name or "").strip()
    return f"{name}({suffix})" if name and suffix else name


def move_target(base: str, entry: "Entry") -> str:
    """旧条目让出裸标题后要去的新名字：`歌名(它的P主)`。

    P主名的写法按 `producer_title_name()`：站上是重定向就用真页面名
    （实测 `Redial` 的 `|P主 = [[livetune|kz(livetune)]]` → `Redial(kz)`），
    日文汉字转中文写法，假名写罗马音。整名都是假名时再问一次
    **VocaDB 的艺术家别名**（`utils.vocadb.artist_aliases`），拿站上那种带大小写的写法
    （`みきと` → `MikitoP`）—— 别名会先按读音核对（见 `same_reading_alias`），
    免得把 VocaDB 模糊搜到的另一个人写进条目名。连 P主 都没写时返回空串
    （调用方按「没法让出裸标题」处理）。

    `|P主` 写了多位时同样**只取第一位**（与 `producer_suffix()` 一致，参 logid 112233）。
    """
    name = first_producer_name(entry.producer)
    if not name:
        return ""
    aliases = vocadb.artist_aliases(name) if is_kana_only(name) else None
    return entry_title(base, producer_title_name(name, aliases))


def retitle(entry: "Entry", new_title: str) -> "Entry":
    """把条目的标题改掉，**它那一行里的链接目标也要跟着改**。

    `parse_entry()` 是按当时的标题拼行的，而定下新标题（`涅槃(Yunosuke)`）得先读出它的 P主 ——
    不一起改的话，消歧义页里那一行会还写着已经变成消歧义页的裸标题。
    """
    if not new_title or new_title == entry.title:
        return entry
    pattern = re.compile(r"('''\s*\[\[\s*)" + re.escape(entry.title or "") + r"(\s*(?:\||\]\]))")
    entry.line = pattern.sub(lambda match: match.group(1) + new_title + match.group(2),
                             entry.line or "", count=1)
    entry.title = new_title
    return entry


def strip_links(text: str) -> str:
    """去掉 wiki 链接标记：`[[A|B]]` → `B`，`[[A]]` → `A`。"""
    text = re.sub(r"\[\[\s*[^\]|]+\s*\|\s*([^\]]+?)\s*\]\]", r"\1", text or "")
    return re.sub(r"\[\[\s*([^\]]+?)\s*\]\]", r"\1", text)


def strip_language_wrapper(text: str) -> str:
    """移除 P 主名外层的 `{{lj|…}}`，供重定向和别名查询使用。"""
    return re.sub(r"\{\{\s*lj\s*\|\s*(.*?)\s*\}\}", r"\1", text or "", flags=re.I)


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
    """一个同名条目：标题 + 描述（`[[P主]]创作的歌曲`）+ 消歧义页里那一行。

    `producer` 是它的 P主（去链接的文本，如 `雄之助`）—— 旧条目让出裸标题时要拿它拼新标题。
    """

    title: str
    description: str = "同名歌曲"
    line: str = ""
    producer: str = ""

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
    # 本条目之外的**同名歌**里，站上还没有列进消歧义页的那些（条目名可能完全不同，
    # 如《パンドラ》的 [[Pandora]]）。它们只进消歧义页的列表，不动它们自己的正文 ——
    # 站上 Pandora 那一页就只有 {{标题替换}}，没有 {{About}}（用户 2026-10-25 的 diff）。
    siblings: List[Entry] = field(default_factory=list)
    note: str = ""
    error: str = ""
    # 提交时的进度标记：让「移动 / 建消歧义页 / 封面改名」可以安全重试（失败后再点一次不会重复做）
    step_moved: bool = False
    step_cover_done: bool = False
    step_other_done: bool = False              # 同名条目的顶部模板（标题格式化 + About）补过了
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
            "siblings": [entry.as_dict() for entry in self.siblings],
            "note": self.note,
            "error": self.error,
            "action": {"disambig": "edit", "move": "move", "occupied": "none"}.get(self.mode, "none"),
        }


# ---------------------------------------------------------------- 文本生成

def our_entry(song) -> Entry:
    """自己这一行：`* '''[[歌名(P主名)]]'''（{{lj|日文名}}）————[[P主]]制作，[[歌手]]演唱的[[引擎]]日语原创歌曲。`

    歌姬要过 `name_to_wiki()`：VocaDB 给的是**日文名**（`初音ミク`），站上的条目名是中文
    （`初音未来`）——以前直接把 `初音ミク` 套进 `[[ ]]`，站上编辑得手动改
    （用户 2026-10-25 对照《潘多拉》修订 275790 → 275795 的 `[[初音ミク]]` → `[[初音未来]]`）。
    这与 Songbox 的 `|演唱=` 是同一套写法（`main.create_songbox()` 也走 `name_to_wiki`）。
    """
    engines = engines_of(song.creators.vocalists)
    title = song.page_name or song.name_chs
    producers = [f"[[{name}]]" for name in song.creators.producers_str()]
    vocalists = [f"[[{name_to_wiki(name)}]]" for name in song.creators.vocalists_str()]
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


def _songbox_fields(text: str) -> Dict[str, str]:
    """Songbox 的 `|字段 = 值`（同名取第一个）。"""
    fields: Dict[str, str] = {}
    for key, value in SONGBOX_FIELD_RE.findall(text or ""):
        fields.setdefault(key.strip(), value.strip())
    return fields


def japanese_of(fields: Dict[str, str]) -> str:
    """Songbox 的 `|歌曲名称=` → 日文原名（`{{lj|パンドラ}}<br/>Pandora` → `パンドラ`）。"""
    name = str(fields.get("歌曲名称") or "")
    match = JAPANESE_RE.search(name)
    return match.group(1) if match else name.split("<br")[0].strip()


def parse_entry(title: str, text: str) -> Entry:
    """从已有条目的 wikitext 里读出 P主 / 演唱 / 日文名 / 引擎，拼出它那一行。"""
    fields = _songbox_fields(text)
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
                 line=build_entry_line(title, japanese, producer_text, vocalist_text, engine),
                 producer=strip_links(strip_language_wrapper(producer)))


def same_name_songs(japanese: str, exclude: Sequence[str] = (), limit: int = 20) -> List[Entry]:
    """站上**另外那些同名歌**：日文原名相同、条目名却不一样（如《パンドラ》的 [[Pandora]]）。

    用户 2026-10-25 对照《潘多拉》修订 275790 → 275795：工具建的消歧义页只写了自己和
    「让出裸标题」的那一首，站上第三首同名曲 `[[Pandora]]`（ATOLS 制作）是站上编辑手动补的。

    做法：按**日文原名**全文搜一遍条目命名空间（`insource:"パンドラ"`：消歧义页、P主页、
    周刊榜页都会命中），再拿 Songbox 的 `|歌曲名称=` 精确核对（比较用 `title_match_key`，
    认全角 / 半角与空白），只有「本来就是这首歌」的才算。搜不到 / 网络失败返回空表。
    """
    japanese = str(japanese or "").strip()
    if not japanese:
        return []
    skip = {str(title).strip() for title in exclude if str(title).strip()}
    key = title_match_key(japanese)
    # 搜索词里的引号会截断 `insource:"…"` 那个查询
    term = 'insource:"{}"'.format(japanese.replace('"', ""))
    found: List[Entry] = []
    for title, text in wiki_api.search_pages_with_text(term, limit=limit):
        title = str(title or "").strip()
        if not title or title in skip or any(entry.title == title for entry in found):
            continue
        fields = _songbox_fields(text)
        if not fields.get("歌曲名称"):
            continue                      # 不是歌曲条目（消歧义页 / P主页 / 榜单页…）
        if title_match_key(japanese_of(fields)) != key:
            continue                      # 正文里提过这个名字，但不是这首歌
        found.append(parse_entry(title, text))
    return found


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


def page_entries(plan: Plan) -> List[Entry]:
    """消歧义页上该有的全部条目：已有的同名条目 + 站上另外找到的同名曲 + 本条目。

    以前只写「自己 + 让出裸标题的那一首」，站上第三首同名曲（如 [[Pandora]]）要编辑手补
    （用户 2026-10-25 对照《潘多拉》修订 275790 → 275795）。
    """
    entries = [*plan.others, *plan.siblings]
    if plan.our_entry is not None:
        entries.append(plan.our_entry)
    return entries


def has_entry(text: str, title: str) -> bool:
    """消歧义页里是不是已经列了这一条（`* '''[[标题]]'''`）。"""
    return bool(title) and bool(re.search(
        r"\[\[\s*" + re.escape(title) + r"\s*(?:\||\]\])", text or ""))


def append_entry_to_disambig(text: str, entry: Entry) -> str:
    """把一行插进已有消歧义页：放在 `{{disambig}}` 之前，没有模板就接在末尾。"""
    text = (text or "").rstrip("\n")
    if has_entry(text, entry.title):
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


def append_missing_entries(text: str, entries: Sequence[Entry]) -> str:
    """把还没列过的条目按顺序补进消歧义页（`append_entry_to_disambig()` 的批量版）。"""
    result = text or ""
    for entry in entries:
        if entry is None or not entry.line or has_entry(result, entry.title):
            continue
        result = append_entry_to_disambig(result, entry)
    return result


def hatnote_of(entry: Entry, others: Sequence[Entry]) -> str:
    """条目 `entry` 顶部该写的消歧义指引（`others` = 除它以外的同名条目）。

    只有一个同名条目 → `{{About|它自己的描述|别人的描述|别人的条目名}}`；
    两个以上 → `{{Otheruseslist|它自己的描述|描述1|条目1|描述2|条目2}}`（与 `top_template` 同一口径）。
    """
    siblings = [item for item in (others or ()) if item is not None and item.title]
    if entry is None or not siblings:
        return ""
    if len(siblings) == 1:
        other = siblings[0]
        return f"{{{{About|{entry.description}|{other.description}|{other.title}}}}}"
    parts = [strip_links(entry.description)]
    for other in siblings:
        parts.extend([other.description, other.title])
    return "{{Otheruseslist|" + "|".join(parts) + "}}"


def other_about(plan: Plan) -> str:
    """旧条目要补的 `{{About}}`：描述与条目名跟本条目对调（参《Redial(kz)》）。

        {{About|[[livetune|kz(livetune)]]创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)}}
    """
    other = plan.others[0] if plan.others else None
    if other is None or plan.our_entry is None or not plan.our_title:
        return ""
    return hatnote_of(other, [plan.our_entry])


def entry_top(text: str, entry: Entry, others: Sequence[Entry]) -> Tuple[str, List[str]]:
    """条目正文开头要补的模板（标题模板 + About / Otheruseslist）→ `(新正文, 补了哪些)`。

    参 voca.wiki《Redial(kz)》修订 272305（用户 2026-10-16 给的差异）：

        {{标题格式化}}
        {{About|[[livetune|kz(livetune)]]创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)}}

    `{{标题格式化}}` 把标题里的括号后缀（`Redial(kz)` → `Redial`）去掉再显示 —— 别的条目不知道
    日文名，所以不能像新条目那样写 `{{标题替换|日文名}}`；标题里没有括号后缀就不补。
    页面里已经有标题模板就不插，已经有 About / Otheruseslist 也不重复插（能重跑）。
    """
    if not text or not text.strip() or entry is None:
        return text, []
    added: List[str] = []
    prefix: List[str] = []
    if not TITLE_TEMPLATE_RE.search(text) and TITLE_SUFFIX_RE.search(entry.title or ""):
        prefix.append(TITLE_FORMAT_TEMPLATE)
        added.append("标题格式化")
    about = hatnote_of(entry, others)
    if about and not HATNOTE_TEMPLATE_RE.search(text):
        prefix.append(about)
        added.append("Otheruseslist" if about.startswith("{{Otheruseslist") else "About")
    if not prefix:
        return text, []
    return "\n".join([*prefix, text]), added


def other_entry_top(text: str, plan: Plan) -> Tuple[str, List[str]]:
    """要移动的旧条目正文开头要补的模板（`entry_top` 的 Plan 版）。"""
    other = plan.others[0] if plan.others else None
    siblings = [plan.our_entry] if (plan.our_entry is not None and plan.our_title) else []
    return entry_top(text, other, siblings)


def fix_other_entries(plan: Plan, summary: str) -> List[str]:
    """给消歧义页上已有的各条目补模板（缺 `{{About}}` / 标题模板就补）→ 说明列表。

    用户 2026-10-16 要求：本条目往消歧义页里加一行时，顺手把页面上其它条目也维护一下
    （它们往往没有通往本条的入口）。各条目的正文一次性读出来，描述用**刚读到的**正文重算
    （消歧义页里那一行可能已经过时）。读过 / 补过的步骤记为 `plan.step_other_done`，重试安全。
    """
    if plan.step_other_done:
        return []
    plan.step_other_done = True
    if plan.our_entry is None:
        return []
    titles = [entry.title for entry in plan.others if entry.title]
    if not titles:
        return []
    texts = wiki_api.fetch_pages_text(titles)

    def fresh_of(entry: Entry) -> Optional[Entry]:
        body = str(texts.get(entry.title) or "")
        return parse_entry(entry.title, body) if body.strip() else None

    parsed = {entry.title: fresh_of(entry) for entry in plan.others}

    def siblings_of(title: str) -> List[Entry]:
        """除 `title` 以外要列进它消歧义指引的条目：页面上的其它条目（按页面顺序）+ 本条目。

        本条目放最后：它会被 `append_entry_to_disambig()` 加在消歧义页末尾，列表顺序跟页面对齐。
        """
        result = []
        for entry in plan.others:
            if entry.title and entry.title != title:
                result.append(parsed.get(entry.title) or entry)
        result.append(plan.our_entry)
        return result

    steps: List[str] = []
    for entry in plan.others:
        if not entry.title:
            continue
        body = str(texts.get(entry.title) or "")
        if not body.strip():
            steps.append(f"读不到「{entry.title}」的正文，没能补上模板")
            continue
        own = parsed.get(entry.title) or entry
        if not own.producer:
            # 消歧义页上也可能是软件 / 专辑 / 人物页面：读不出 P主 就别乱加歌曲描述
            steps.append(f"「{entry.title}」不像是歌曲条目（没读出 P主），跳过")
            continue
        new_text, added = entry_top(body, own, siblings_of(entry.title))
        if not added:
            continue
        names = "、".join(f"{{{{{label}}}}}" for label in added)
        outcome = wiki_api.edit_page(entry.title, new_text, f"{summary}：加上 {names}")
        if outcome.get("ok"):
            steps.append(f"已给「{entry.title}」加上 {names}")
        else:
            steps.append(f"给「{entry.title}」加消歧义模板失败：{outcome.get('error')}")
    return steps


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
    ours = plan.our_entry or Entry(title=plan.our_title, description=ours_desc)
    return hatnote_of(ours, plan.others)


# ---------------------------------------------------------------- 探测

def detect(song, name: Optional[str] = None) -> Plan:
    """探测 Vocawiki 上的同名条目 → Plan。

    只有「裸标题确实被占用」时才用带 P主名的条目名，避免把没有冲突的条目也改名。
    """
    base = (name or song.name_chs or "").strip()
    plan = Plan(base_title=base)
    if not base:
        return plan
    facts = wiki_api.fetch_page_facts(base)
    if not facts.get("ok"):
        plan.note = "无法确认 Vocawiki 上的同名条目，本次按普通条目上传"
        return plan
    if not facts.get("exists"):
        sibling = wiki_api.list_titles_with_prefix(f"{base}(")
        if sibling:
            plan.note = f"站内已有 {'、'.join(sibling)}，但「{base}」未被占用，本次按普通条目上传"
        return plan

    # P主名的写法要问站点（重定向 / 中文写法），所以确认裸标题被占用后才算
    suffix = producer_suffix(song.creators.producers)
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
        plan.siblings = same_name_songs(
            song.name_jap,
            exclude=[base, plan.our_title, *(entry.title for entry in plan.others)])
    elif facts.get("song"):
        # 裸标题被另一首歌占用：要把它移到「歌名(它的P主)」把标题让出来。
        # ⚠️ 这里以前直接把 base 当成它的新标题（title 默认是传入的 base），
        # 于是计划成了「把「涅槃」移动到「涅槃」」——自己移动自己，提交时必然失败。
        other = parse_entry(base, facts.get("text", ""))
        target = move_target(base, other)
        if not target or target == base:
            # 旧条目连 P主 都没写 → 没法给它起消歧义名，裸标题让不出来
            other.title = base
            plan.mode = MODE_OCCUPIED
            plan.others = [other]
            plan.error = f"「{base}」已被另一首歌占用，但它没写 P主，无法改名让出标题"
            plan.note = plan.error
            return plan
        plan.mode = MODE_MOVE
        plan.others = [retitle(other, target)]
        # 除了「让出裸标题」的这一首，站上还可能有别的同名曲（条目名完全不同，如 [[Pandora]]）
        plan.siblings = same_name_songs(song.name_jap, exclude=[base, target, plan.our_title])
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
    """把 `[[旧名]]` / `[[旧名|显示]]` 换成新名（不动已经是新名的链接）。

    ⚠️ **裸链接要补上原来的显示名**：`[[涅槃]]` → `[[涅槃(Yunosuke)|涅槃]]`，不是 `[[涅槃(Yunosuke)]]`。
    站内惯例如此 —— 用户 2026-09 给的真实编辑（`Synthesizer V殿堂曲/2026年投稿`，diff 251406）里
    `|曲目 = [[涅槃]]` 被改成 `|曲目 = [[涅槃(Yunosuke)|涅槃]]`：榜单那一行显示的还得是歌名，
    只换链接目标；照我们以前的写法会突然冒出括号里的 P主名（diff 251400）。
    """
    if not old_title or not new_title or old_title == new_title:
        return text or "", 0
    # 标题后面必须紧跟 `|` 或 `]]`，否则 `[[涅槃(Yunosuke)|涅槃]]` 也会被当成 `[[涅槃…]]` 再改一次
    pattern = re.compile(r"\[\[\s*" + re.escape(old_title) + r"\s*(?:\|([^\]]*))?\]\]")

    def replace(match: "re.Match") -> str:
        if match.group(1) is None:
            return f"[[{new_title}|{old_title}]]"      # 裸链接：补上原显示名
        return f"[[{new_title}|{match.group(1)}]]"     # 已有显示名：原样保留

    return pattern.subn(replace, text or "")


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


def replace_piped_items(text: str, old_title: str, new_title: str) -> Tuple[str, int]:
    """把「位置参数里写的条目名」换成新条目名：`旧名{{!}}显示名` → `新名{{!}}显示名`。

    榜单 / P主模板（如 `Template:雄之助`）用 `{{lj|{{links|涅槃{{!}}ネハン<!--
    -->|…}}}}` 罗列曲目，这里的 `涅槃` 就是链接目标（`{{links}}` 把每项渲染成 `[[涅槃|ネハン]]`），
    但页面里既没有 `[[ ]]` 也不是 `|参数 = 旧名`，前三种写法都认不出来。
    用户 2026-09-29 报的站内编辑就是这一条（diff 251408）：`涅槃{{!}}ネハン` → `涅槃(Yunosuke){{!}}ネハン`。
    只改 `{{!}}` **左边**（链接目标）那一截，右边的显示名不动；
    前面的边界限定为 `|` / `>`（参数分隔处），所以不会动 `{{!}}旧名`（显示名那一侧）。
    """
    if not text or not old_title or old_title == new_title:
        return text or "", 0
    pattern = re.compile(r"(?<=[|>])(\s*)" + re.escape(old_title) + r"(\s*)\{\{!\}\}")
    return pattern.subn(
        lambda match: f"{match.group(1)}{new_title}{match.group(2)}{{{{!}}}}", text)


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
    new_text, count = replace_piped_items(text, old_title, new_title)
    if count:
        return new_text, count, "模板参数（曲目名）"
    return text, 0, ""


def snippet(text: str, old_title: str, width: int = 60) -> str:
    """链入位置周围的一小段上下文（`[[旧名]]` / `|条目 = 旧名` / `旧名{{!}}显示名` 都认）。"""
    index = text.find(f"[[{old_title}")
    if index < 0:
        index = text.find(f"{old_title}{{{{!}}}}")
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
                           f"没找到 [[{old_title}]]、|条目 = {old_title} 或 {old_title}{{{{!}}}} 形式的引用"
                           "（可能通过模板 / 模块链入）")})
    return result


def find_backlinks(old_title: str, new_title: str) -> List[str]:
    """要修正的链入页面 = 链入表（Special:WhatLinksHere）+ 全文搜索里**真能改**的模板 / 模块页。

    后一半是必须的：`{{links}}` 那类链接是 Lua 现拼的，根本不进链入表，
    光看 WhatLinksHere 会漏掉 `Template:雄之助`（用户 2026-09-29 报的就是这个）。
    全文搜索按子串匹配，会把只提到 `涅槃(HotaRu)` / `凤凰涅槃` 的页面也搜出来，
    所以先读回正文、只留 `fix_page_text` 真能改动的（改了几处的预览照旧交给 plan_backlinks）。
    """
    titles = [title for title in wiki_api.fetch_backlinks(old_title) if title]
    extra = [title for title in wiki_api.search_text_references(old_title)
             if title and title not in titles]
    if not extra:
        return titles
    texts = wiki_api.fetch_pages_text(extra)
    for title in extra:
        _new_text, count, _kind = fix_page_text(texts.get(title, ""), old_title, new_title)
        if count:
            titles.append(title)
    return titles


def apply_backlinks(old_title: str, new_title: str, titles: Sequence[str],
                    summary: str = "修正同名条目的内部链接",
                    progress: Optional[Callable[[dict], None]] = None) -> List[dict]:
    """真的去改：逐页替换并保存，返回每页结果（支持 wiki 链接与模板参数两种写法）。

    `progress` 每处理完一页就叫一次（传的是那一页的结果字典）—— 界面用它
    「一个条目一个条目地」冒提示；没改成的页（正文读不到 / 没有可替换的引用）也叫，
    这样界面能报「✗ 某某：原因」。回调在**调用线程**里执行（提交页是 QThread），
    Qt 信号会自动排队回主线程。
    """
    texts = wiki_api.fetch_pages_text(titles)
    results = []
    for title in titles:
        text = texts.get(title)
        if text is None:
            item = {"title": title, "ok": False, "error": "读不到页面内容"}
        else:
            new_text, count, kind = fix_page_text(text, old_title, new_title)
            if not count:
                item = {"title": title, "ok": False, "error": "没有可替换的引用"}
            else:
                outcome = wiki_api.edit_page(title, new_text, f"{summary}（{kind}）")
                item = {"title": title, "ok": bool(outcome.get("ok")), "count": count,
                        "kind": kind,
                        "error": "" if outcome.get("ok") else str(outcome.get("error", "编辑失败"))}
        results.append(item)
        if progress is not None:
            progress(item)
    return results


# ---------------------------------------------------------------- 提交时的动作

# Songbox 的封面字段：`|image = 涅槃.jpg`
IMAGE_FIELD_RE = re.compile(r"\|\s*image\s*=\s*([^\n|]*)", re.I)


def followed_cover(text: str, old_title: str, new_title: str) -> Tuple[str, str, str]:
    """旧条目的封面文件要不要跟着改名 → (旧文件名, 新文件名, 新正文)；不用改就返回空文件名。

    只动「文件名 = 旧条目名 + 扩展名」这种（`|image = 涅槃.jpg` → `涅槃(Yunosuke).jpg`）——
    条目改名叫 `涅槃(Yunosuke)` 了，封面还占着裸标题的名字会让人分不清是哪首；
    别的写法（`向日葵-七尾.jpg`、`Miku 1640m guitar13234939.jpg`）本来就不认标题，不动。
    """
    match = IMAGE_FIELD_RE.search(text or "")
    if match is None or not old_title or not new_title:
        return "", "", text or ""
    file_name = match.group(1).strip()
    stem, dot, extension = file_name.rpartition(".")
    if not dot or stem != old_title:
        return "", "", text or ""
    new_name = f"{new_title}.{extension}"
    return file_name, new_name, text[:match.start(1)] + new_name + text[match.end(1):]


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
        # 消歧义页上已有的各条目往往没有通往本条的入口：缺 {{About}} / 标题模板就补上
        # （用户 2026-10-16 要求，参《Redial(kz)》修订 272305 的写法）
        steps.extend(fix_other_entries(plan, summary))
        text = wiki_api.fetch_pages_text([base]).get(base, "")
        if not text:
            return {"ok": False, "steps": steps, "backlinks": [], "error": f"读不到消歧义页「{base}」"}
        # 站上另外的同名曲（条目名完全不同，如 [[Pandora]]）也补进列表 —— 只补列表，
        # **不动**它们自己的正文（站上 Pandora 那一页没有 {{About}}）
        new_text = append_missing_entries(text, [*plan.others, *plan.siblings])
        new_text = append_entry_to_disambig(new_text, plan.our_entry)
        if new_text.strip() == text.strip():
            plan.step_page_done = True
            return {"ok": True, "backlinks": [],
                    "steps": [*steps, f"消歧义页「{base}」里已有「{plan.our_title}」，跳过"]}
        outcome = wiki_api.edit_page(base, new_text, summary)
        if not outcome.get("ok"):
            return {"ok": False, "steps": steps, "backlinks": [],
                    "error": f"修订消歧义页失败：{outcome.get('error')}"}
        plan.step_page_done = True
        added = [entry.title for entry in plan.siblings if not has_entry(text, entry.title)]
        extra = f"（顺带补上同名的「{'、'.join(added)}」）" if added else ""
        return {"ok": True, "backlinks": [],
                "steps": [*steps, f"已在消歧义页「{base}」里补上「{plan.our_title}」{extra}"]}

    # MODE_MOVE：先抓链入，再不留重定向移动旧条目，最后在裸标题建消歧义页
    other = plan.others[0] if plan.others else None
    if other is None:
        return {"ok": False, "steps": steps, "backlinks": [], "error": "找不到要移动的旧条目"}
    if not plan.backlinks:
        plan.backlinks = find_backlinks(base, other.title)
    if not plan.step_moved:
        moved = wiki_api.move_page(base, other.title,
                                   f"为「{plan.our_title}」让出标题，改名为「{other.title}」")
        if not moved.get("ok"):
            return {"ok": False, "steps": steps, "backlinks": plan.backlinks,
                    "error": f"移动「{base}」失败：{moved.get('error')}"}
        plan.step_moved = True
        steps.append(f"已把「{base}」移动到「{other.title}」（不留重定向）")
    if not plan.step_cover_done:
        # 旧条目的封面若占着裸标题的名字，跟着改成新名字（`涅槃.jpg` → `涅槃(Yunosuke).jpg`）
        plan.step_cover_done = True
        text = wiki_api.fetch_pages_text([other.title]).get(other.title, "")
        old_file, new_file, new_text = followed_cover(text, base, other.title)
        if old_file:
            users = [title for title in wiki_api.file_usage(f"File:{old_file}")
                     if title != other.title]
            moved_file = wiki_api.move_page(f"File:{old_file}", f"File:{new_file}",
                                            f"随「{base}」改名为「{other.title}」",
                                            leave_redirect=bool(users))
            if not moved_file.get("ok"):
                steps.append(f"封面「{old_file}」改名失败：{moved_file.get('error')}")
            else:
                steps.append(f"封面「{old_file}」已改名为「{new_file}」")
                if new_text != text:
                    outcome = wiki_api.edit_page(other.title, new_text, summary)
                    if not outcome.get("ok"):
                        steps.append(f"更新「{other.title}」的封面字段失败：{outcome.get('error')}")
                if users:
                    steps.append(f"「{old_file}」还被 {'、'.join(users)} 使用，已留下重定向")
    if not plan.step_other_done:
        # 旧条目的标题已经变成「歌名(P主名)」：给它补上 {{标题格式化}} + 指向本条的 {{About}}，
        # 否则它既没说明自己为什么带括号，也没有通往另一首同名歌的入口（用户 2026-10-16 报）。
        plan.step_other_done = True
        text = wiki_api.fetch_pages_text([other.title]).get(other.title, "")
        if not text.strip():
            steps.append(f"读不到「{other.title}」的正文，没能补上 {TITLE_FORMAT_TEMPLATE} / "
                         "{{About}}，请手动补一下")
        else:
            new_text, added = other_entry_top(text, plan)
            if added:
                names = "、".join(f"{{{{{label}}}}}" for label in added)
                outcome = wiki_api.edit_page(other.title, new_text, f"{summary}：加上 {names}")
                if outcome.get("ok"):
                    steps.append(f"已给「{other.title}」加上 {names}")
                else:
                    steps.append(f"给「{other.title}」加消歧义模板失败：{outcome.get('error')}")
    if not plan.step_page_done:
        page = disambig_page_text(base, page_entries(plan))
        created = wiki_api.edit_page(base, page, summary, create_only=True)
        if not created.get("ok"):
            steps.append(f"创建消歧义页失败：{created.get('error')}")
            return {"ok": False, "steps": steps, "backlinks": plan.backlinks,
                    "error": str(created.get("error"))}
        plan.step_page_done = True
        steps.append(f"已创建消歧义页「{base}」")
    return {"ok": True, "steps": steps, "backlinks": plan.backlinks}
