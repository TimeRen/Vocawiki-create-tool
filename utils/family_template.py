"""歌手「大家族模板」工具：判断导航框折叠状态、把新条目写进模板对应小节。

本模块由原来的 `utils/navbox.py` 与 `utils/family_template.py` 合并而成，做两件事。

一、判断导航框默认是展开还是折叠，需要时给模板补 `|collapsed`
    维基上「大家族 / 歌手」模板的折叠状态写在 `|state =` 里，实测两种写法：

        |state = {{#ifeq:{{{1}}}|collapsed|mw-collapsed|mw-collapsible mw-uncollapsed}}
            不传参 = 展开（mw-uncollapsed）→ 条目里要写 {{模板|collapsed}} 才会默认折叠
            （例：Template:可不/2024、Template:歌爱雪）
        |state = {{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|mw-collapsible mw-collapsed}}
            不传参 = 折叠（mw-collapsed）→ 条目里什么都不用写
            （例：Template:重音Teto/2024）

    认不出来时（没有 state 行、状态写死、用了 #switch 等）一律原样返回 —— 宁可不改。

二、把新条目写进模板的对应小节（提交条目后的「同步修改大家族模板」）

    歌手模板（Template:可不/2024、Template:重音Teto/2024 等）实测结构：

        |group1 = {{coloredlink|#4d79ff|CeVIO传说曲|传说曲}}
        |list1 = {{Navbox subgroup
            |group1 = niconico
            |list1 = {{lj|[[A|あ]]{{W}}[[B|び]]}}
            |group2 = bilibili
            |list2 = {{lj|[[A|あ]]}}
        }}
        |group3 = 部分<br class='nomobile'/>非殿堂曲
        |list3 = {{hlist|[[迷途]]}}

    达到殿堂（10 万播放）及以上的写进荣誉小节，同一首歌会同时列在已达成的各档里
    （例：神话曲《医学》也列在传说曲、殿堂曲下），所以逐档写入；未达殿堂的写进
    「部分非殿堂曲」（模板没有这一组时退到「其他 / 其它」那组，如 Template:NurseRobot_TypeT）。
    组内按站点（niconico / bilibili / YouTube）或按年份分格时自动下钻。

    有些歌手模板**根本没有荣誉 / 非殿堂小节**，而是按年份罗列曲目，如 Template:梦的结唱
    （夢ノ結唱 的 POPY / ROSE / …共用）：

        |group2 = 優秀曲目
        |list2 = {{Navbox subgroup
            |group1 = 2022年
            |list1 = {{coloredlink|#fa006e|梦想梦想Gradation|{{lj|ゆめゆめグラデーション}}}} • …
            |group2 = 2023年
            …
        }}

    这类模板（不管播放量多少）按 `add_by_year()` 写进**投稿年份**那一格，与 P主模板的
    定位逻辑同一套 —— 以前只会提示「模板里没有“殿堂”分组」，条目就写不进去。

    活动模板（Template:The VOCALOID Collection2024冬）实测结构：

        | list1  = {{Navbox|child
         | title = TOP100
         | group1 = 1-10位
         | list1  = {{lj|[[医学|イガク]]}}<!--
             --> • {{lj|[[Smart???|スマート???]]}}
        }}
        | list4  = {{Navbox|child
         | title = 其他歌曲
         | group2 = 未上榜歌曲
         | list2 = ...
        }}

    条目写法有好几种（`{{lj|[[中文|日文]]}}`、`[[中文|{{lj|日文}}]]`、`[[中文|日文]]`、
    `{{hlist|…}}`、整段 `{{lj|…}}` 包住再用 `{{W}}` 隔开……），插入时先看目标列表里已有的
    条目用的是哪一种，再照同样的风格写。插入前会先在整篇模板里找这首歌：已经列在别的
    赛道 / 名次段落里（尤其是条目还没建、只能用日文原名链的旧写法）就改指 / 跳过，
    不会重复添加一份。

    P主模板（Template:Chinozo、Template:Dixie Flatline 等）实测结构：

        |group1 = 原创投稿曲目
        |list1  = {{Navbox subgroup
            |group1 = 2024年
            |list1  = {{lj|[[出租车|タクシィ]] • [[2036]]}}
            |group2 = 2025年
            |list2  = {{lj|{{links|百鬼祭|KING{{!}}KING}}}}
        }}

    这类模板按**投稿年份**分格，所以写进「<投稿年>年」那一格（年份标签也实测过不带「年」的写法，
    如 `|group1 = 2020`）；格内若是 `{{links|…}}`，条目写成 `页面名{{!}}显示名`，
    否则写成 `[[页面名|显示名]]`。

结构认不出（没有对应分组、分格认不出）一律不修改；写回前还会校验花括号配平。
"""
import contextlib
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from utils import login, wiki_api

# ============================================================ 取模板源码

REQUEST_TIMEOUT = 60
RETRY = 1                     # 失败重试次数
RETRY_DELAY = 0.5             # 重试前等待秒数
EXPANDED = "expanded"
COLLAPSED = "collapsed"
UNKNOWN = "unknown"

# |state = ...
STATE_RE = re.compile(r"^\s*\|\s*state\s*=\s*(.+)$")
# {{{参数名}}} 或 {{{参数名|默认值}}}
PARAM_RE = re.compile(r"\{\{\{([^{}]*)\}\}\}")
# {{#ifeq:值|比较|相等时|否则}}（分支里不能再有花括号，认不出就放弃）
IFEQ_RE = re.compile(r"\{\{#ifeq:([^|{}]*)\|([^|{}]*)\|([^|{}]*)\|([^{}]*)\}\}")

# 模板源码按标题缓存（同一次运行里可能被查多次；失败也缓存，避免反复请求）
_template_cache: Dict[str, Optional[str]] = {}
# 重定向页标题 → 目标页标题（写回要用目标页，否则会把重定向页正文改掉）
_redirect_cache: Dict[str, str] = {}

# 有些模板页本身就是重定向（例：Template:柊キライ → Template:Hiiragi Kirai）
REDIRECT_RE = re.compile(r"^\s*#\s*(?:REDIRECT|重定向)\s*:?\s*\[\[([^\]|#]+)", re.IGNORECASE)


def _param_default(inner: str) -> str:
    """`1|默认值` -> `默认值`；`1`（无默认值）-> 空串。"""
    _, sep, default = inner.partition("|")
    if not sep:
        return ""
    return re.sub(r"</?noinclude[^>]*>", "", default).strip()


def resolve_default(expr: str) -> str:
    """把「不传任何参数」时的 state 表达式化简成字面值。"""
    expr = PARAM_RE.sub(lambda m: _param_default(m.group(1)), expr)
    for _ in range(5):                      # #ifeq 可能多层嵌套，反复化简到不变
        simplified = IFEQ_RE.sub(lambda m: m.group(3) if m.group(1) == m.group(2) else m.group(4),
                                 expr)
        if simplified == expr:
            break
        expr = simplified
    return expr


def default_state(text: Optional[str]) -> str:
    """判断模板默认是展开还是折叠；认不出返回 UNKNOWN。"""
    if not text:
        return UNKNOWN
    for line in str(text).splitlines():
        match = STATE_RE.match(line)
        if not match:
            continue
        expr = match.group(1).strip()
        if "collaps" not in expr.lower():
            continue
        if "{{{" not in expr:               # 状态写死了，条目里传参也没用
            continue
        resolved = resolve_default(expr)
        if "{{" in resolved:                # 还有没化简掉的东西（#switch 等）→ 不猜
            continue
        if "mw-uncollapsed" in resolved:    # 不传参 -> 明确展开
            return EXPANDED
        if "mw-collapsed" in resolved:      # 不传参 -> 折叠
            return COLLAPSED
        if "mw-collapsible" in resolved:    # 只有 mw-collapsible = 默认展开
            return EXPANDED
    return UNKNOWN


def needs_collapsed(text: Optional[str]) -> bool:
    """模板默认展开时才需要补 |collapsed。"""
    return default_state(text) == EXPANDED


def _template_title(name: str) -> str:
    name = str(name or "").strip()
    return name if ":" in name else f"Template:{name}"


def fetch_template_text(name: str, _depth: int = 0) -> Optional[str]:
    """取模板源码；不存在或抓取失败返回 None。模板页本身是重定向时自动跟到目标页。

    成功与「页面不存在」都写入缓存；**网络类失败不缓存**，以便稍后再试一次（wiki 偶发断连）。
    """
    title = _template_title(name)
    if title in _template_cache:
        return _template_cache[title]

    raw = _fetch_template_raw(title)
    if raw is None:                          # 网络类失败不缓存，稍后还能再试
        return None
    match = REDIRECT_RE.match(raw) if raw else None
    if match and _depth < 3:
        target = match.group(1).strip()
        target = target if ":" in target else f"Template:{target}"
        logging.info("模板 %s 是重定向，改从 %s 读写", title, target)
        _redirect_cache[title] = target
        text = fetch_template_text(target, _depth + 1)
    else:
        text = raw or None                   # 页面不存在（空串）也缓存，避免反复请求
    _template_cache[title] = text
    return text


def resolve_template_title(name: str) -> str:
    """写回用标题：模板页本身是重定向时换成目标页标题。"""
    title = _template_title(name)
    if title not in _redirect_cache:
        fetch_template_text(title)               # 顺便把重定向信息填进缓存
    return _redirect_cache.get(title, title)


def _edit_cached_template(title: str, text: str, summary: str) -> Dict[str, object]:
    """写回成功后同步本进程缓存，避免后续同步用旧正文覆盖刚写入的模板。"""
    result = wiki_api.edit_page(title, text, summary)
    if result.get("ok"):
        _template_cache[title] = text
        for alias, target in _redirect_cache.items():
            if target == title:
                _template_cache[alias] = text
    return result


def _fetch_template_raw(title: str) -> Optional[str]:
    """按标题取模板源码（不处理重定向）。

    返回内容字符串；**页面不存在返回空串**；网络失败返回 None（调用方据此不缓存）。
    """
    payload = None
    for attempt in range(RETRY + 1):
        try:
            payload = login.get_api_session().get(wiki_api.api_url(), params={
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": title,
                "format": "json",
                "formatversion": "2",
            }, timeout=REQUEST_TIMEOUT).json()
            break
        except Exception as e:
            if attempt >= RETRY:
                logging.warning("无法获取模板 %s 的源码：%s", title, e)
                return None
            time.sleep(RETRY_DELAY)

    for page in payload.get("query", {}).get("pages", []):
        if page.get("missing"):
            return ""
        try:
            return page["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
    return None


def collapse_if_expanded(name: str) -> str:
    """模板默认展开时返回「模板名|collapsed」，否则原样返回。

    查询失败、模板不存在、认不出状态时都不改动 —— 生成结果保持可用。
    """
    name = str(name or "").strip()
    if not name or "collapsed" in name:
        return name
    if needs_collapsed(fetch_template_text(name)):
        logging.info("模板 %s 默认展开，补上 |collapsed", name)
        return f"{name}|collapsed"
    return name


def collapse_all(names) -> List[str]:
    """批量处理（保持顺序）。"""
    return [collapse_if_expanded(name) for name in names]


# ============================================================ 模板结构常量

# 播放量 -> 荣誉小组的关键词（从高到低）
HONOR_KEYWORDS: Tuple[Tuple[int, Tuple[str, ...]], ...] = (
    (100_000_000, ("破亿",)),
    (10_000_000, ("神话",)),
    (1_000_000, ("传说",)),
    (100_000, ("殿堂",)),
)

# 未达殿堂时写入的「部分非殿堂曲」小组（实测有「非殿堂曲」「未殿堂曲」两种写法）
NON_HONOR_KEYWORDS: Tuple[str, ...] = ("非殿堂", "未殿堂")
NON_HONOR_NAME = "部分非殿堂曲"
# 有些模板根本没有「非殿堂曲」这一组，只有「其他 / 其它」（例：Template:NurseRobot_TypeT
# 的 `|group3 = 其他{{注||收录Vocawiki已有条目。}}`、Template:可不/2024 的「其它」）
NON_HONOR_FALLBACK: Tuple[str, ...] = ("其他", "其它")
NON_HONOR_FALLBACK_NAME = "其他"
# 还有一种更绕的：标题行直接写在 `|listN =` 里面（实测 Template:猫村伊吕波，可折叠的自定义行）：
#     |list3 = <tr><th class="mw-customtoggle-1 navbox-title" …>其他歌曲<sub>(点击展开)</sub></th></tr><!--
#     此处按创建时间添加未殿堂的已创建条目的歌曲
#     --><div …></div><tr><td …>{{lj|{{links|<!-- 11-03-16 -->171<!-- … -->|…}}}}</td></tr>
# 认不出它就会退到「按年份写」那一步，把**未殿堂**的歌写进「殿堂曲 → 2011年」——
# 用户 2026-10-20 对照 diff 273880：站上编辑不得不把 `171` 从殿堂曲挪回「其他歌曲」。
EMBEDDED_LABEL_RE = re.compile(r"<(?:th|td|span|div|p)[^>]*>\s*([^<]{1,40}?)\s*<")

# 站点 -> 子分组标签里的关键词（小写比较）
SITE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "niconico": ("niconico", "nico"),
    "bilibili": ("bilibili",),
    "YouTube": ("youtube",),
}

# The VOCALOID Collection 模板里「没进榜」的段落
UNRANKED_KEYWORDS: Tuple[str, ...] = ("未上榜",)
UNRANKED_TITLES: Tuple[str, ...] = ("其他", "其它")
# 活动模板里的赛道子导航框标题（`|title = TOP100` / `TOP30` / `ROOKIE` / `REMIX`）：同一首歌
# **可能同时在两榜**里（实测 涅槃(HotaRu)：TOP100 第 70 名 + ROOKIE 第 42 名），
# 所以这是一份「有哪些赛道」的名单，不是「命中一个就结束」。2020冬另有 TOP30。
COLLECTION_TRACKS: Tuple[str, ...] = ("TOP100", "TOP30", "ROOKIE", "REMIX")
UNRANKED_TRACK = "榜外"

# P主模板：按投稿年份分格，年份就在「原创 / 投稿」这类分组里
# （实测标签：原创投稿曲目 / 原创/参与曲目 / 原创&合作歌声合成曲目 / nico上 原创投稿作品 / 投稿作品）
ORIGINAL_KEYWORDS: Tuple[str, ...] = ("原创", "投稿")
# 年份分组标签：`2013年`（多数）或 `2020`（如 Template:Kanaria）
PRODUCER_YEAR_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*年?")

GROUP_RE = re.compile(r"^group(\d+)$")
LIST_PARAM_RE = re.compile(r"^list(\d+)$")
NAME_VALUE_RE = re.compile(r"^\s*([^=\n]+?)\s*=\s*(.*)$", re.S)
# 分组标签里的年份（如 `2015年`）与名次区间（如 `11-20位`）
YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年")
RANGE_RE = re.compile(r"(\d+)\s*[-–—~～]\s*(\d+)")
# 列表条目的日期注释：实测 `<!-- 02-22 23:00 -->[[活死人乐队|リビングデッドバンデッド]]{{W}}`
# （`youtube 4-20` / `Bilibili 07-18` 这种带站点前缀、且不一定写时间）
ITEM_COMMENT_RE = re.compile(r"<!--([\s\S]*?)-->\s*(\[\[[\s\S]*?\]\])")
# 注释里的日期，三种写法都认：
#   `02-22 23:00`（月-日 时分，Template:可不/2024）
#   `11-03-16`（两位年-月-日，Template:猫村伊吕波 diff 273880 实测）
#   `2011-03-16`（四位年）
COMMENT_DATE_RE = re.compile(
    r"(?P<first>\d{1,4})\s*-\s*(?P<second>\d{1,2})"
    r"(?:\s*-\s*(?P<third>\d{1,2}))?"
    r"(?:\s+(?P<hour>\d{1,2})[:：](?P<minute>\d{2}))?")


class CommentDate(NamedTuple):
    """注释里读出来的日期。`year` 只有在注释**写了年**时才有值。"""

    year: Optional[int]
    month: int
    day: int
    hour: int = 0
    minute: int = 0
    year_digits: int = 0            # 注释里年写了几位（0 = 没写年）

    @property
    def has_time(self) -> bool:
        return bool(self.hour or self.minute)

    def sort_key(self, fallback_year: int) -> Tuple[int, int, int, int, int]:
        """排序用的键：注释没写年时按 `fallback_year`（本曲投稿年）算。"""
        return (self.year if self.year is not None else fallback_year,
                self.month, self.day, self.hour, self.minute)

# 主投稿站点 → 注释里的站点前缀。niconico 是主投稿站，**不写前缀**（用户 2026-10-30 要求：
# 「如果采用的是 niconico 数据就不要标注来源站点名」）；日期取自别的站时才标出来。
# 大小写照站上的写法（实测 91 个年份子页：`bilibili ` 158 处、`YouTube ` 51 处）。
SITE_STAMP_PREFIX: Dict[str, str] = {"YouTube": "YouTube ", "bilibili": "bilibili ",
                                     "niconico": ""}


@dataclass(frozen=True)
class PostedAt:
    """本曲的投稿时刻 / 主投稿站点：写进列表注释里用（`<!-- 02-22 23:00 -->`）。"""

    when: datetime
    site: Optional[str] = None                      # 主投稿站点（Video.site.value）

    def stamp(self, with_time: Optional[bool] = None, with_year: int = 0) -> str:
        """注释里的日期文字：`02-22 23:00` / `YouTube 04-20` / `11-03-16`。

        `with_time` 不传时——有时分就写时分；传 `False` 只写日期（列表里邻居都只写日期时），
        传 `True` 连 `00:00` 也写出来（邻居都写了时分时，见 `_insert_dated`）。
        `with_year` 照邻居的写法：2 = 写两位年（`11-03-16`，Template:猫村伊吕波），
        4 = 写四位年，0 = 不写年。以前只会写 `月-日`，插进带年份的列表里就成了
        `03-16` 这种与邻居格格不入的注释（用户 2026-10-20 对照 diff 273880 报的）。

        站点名只标**非 niconico** 的（见 `SITE_STAMP_PREFIX`）：niconico 是主投稿站。
        """
        text = f"{self.when.month:02d}-{self.when.day:02d}"
        if with_year in (2, 4):
            year = f"{self.when.year:04d}" if with_year == 4 else f"{self.when.year % 100:02d}"
            text = year + "-" + text
        has_time = bool(self.when.hour or self.when.minute)
        if with_time if with_time is not None else has_time:
            text += f" {self.when.hour:02d}:{self.when.minute:02d}"
        return SITE_STAMP_PREFIX.get(str(self.site or ""), "") + text

    def key(self) -> Tuple[int, int, int, int, int]:
        """排序用：(年, 月, 日, 时, 分)。"""
        return (self.when.year, self.when.month, self.when.day,
                self.when.hour, self.when.minute)


# 条目链接：[[页面名]] 或 [[页面名|显示名]]
LINK_RE = re.compile(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$")
# 同一个（不带 ^ $）用于在整段文本里找链接
INLINE_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


# ---------------------------------------------------------------- 参数扫描

def scan_params(text: str) -> List[Tuple[str, str, int, int]]:
    """扫描最外层 `{{...}}` 的顶层参数。

    返回 [(参数名, 参数值, 值起点, 值终点)]；参数值只含其本身（不含两侧空白）。
    只认花括号深度为 1、且不在 `[[...]]` 内的 `|` —— 因此 `[[A|B]]` 与嵌套模板里的 `|`
    都不会被当成参数分隔符。
    """
    start = text.find("{{")
    if start < 0:
        return []
    depth = 0
    bracket = 0
    index = start
    body_start = None
    cuts: List[int] = []
    end_of_body = len(text)
    while index < len(text):
        if text.startswith("<!--", index):
            close = text.find("-->", index)
            index = len(text) if close < 0 else close + 3
            continue
        pair = text[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            if depth == 1:
                body_start = index
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                end_of_body = index - 2
                break
            continue
        if pair == "[[":
            bracket += 1
            index += 2
            continue
        if pair == "]]":
            bracket = max(0, bracket - 1)
            index += 2
            continue
        if text[index] == "|" and depth == 1 and bracket == 0:
            cuts.append(index)
        index += 1

    if body_start is None:
        return []
    bounds = [body_start] + [c + 1 for c in cuts] + [end_of_body]
    params: List[Tuple[str, str, int, int]] = []
    for number in range(1, len(bounds) - 1):        # 跳过第 0 段（模板名）
        chunk = text[bounds[number]:end_of_body if number == len(bounds) - 2 else cuts[number]]
        match = NAME_VALUE_RE.match(chunk)
        if not match:
            continue
        value = match.group(2)
        value_start = bounds[number] + match.start(2)
        value = value.rstrip()                  # 尾随空白/缩进留给原文，插入时不碰它
        params.append((match.group(1).strip(), value, value_start, value_start + len(value)))
    return params


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--[\s\S]*?-->", "", text or "")


def iter_blocks(text: str) -> List[Tuple[int, int]]:
    """列出文本里所有 `{{…}}` 的 (起点, 终点)，按起点排序（外层排在它的内层之前）。"""
    stack: List[int] = []
    blocks: List[Tuple[int, int]] = []
    index = 0
    while index < len(text) - 1:
        if text.startswith("{{", index):
            stack.append(index)
            index += 2
            continue
        if text.startswith("}}", index):
            if stack:
                blocks.append((stack.pop(), index + 2))
            index += 2
            continue
        index += 1
    return sorted(blocks)


def _block_end(text: str, start: int) -> Optional[int]:
    """从 `start` 处的 `{{` 开始，返回配对 `}}` 之后的下标；不配平返回 None。"""
    depth = 0
    index = start
    while index < len(text) - 1:
        if text.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if text.startswith("}}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return index
            continue
        index += 1
    return None


def iter_groups(text: str) -> List[Tuple[str, int, int]]:
    """列出所有 `|groupN =` / `|listN =` 配对，返回 (标签, 值起点, 值终点)。

    不限深度：`{{Navbox subgroup}}`、`{{#invoke:Nav|box|subgroup}}` 里的分组同样能找出来。
    """
    groups: List[Tuple[str, int, int]] = []
    for start, end in iter_blocks(text):
        spans: Dict[str, Tuple[int, int]] = {}
        values: Dict[str, str] = {}
        for name, value, value_start, value_end in scan_params(text[start:end]):
            spans[name] = (value_start, value_end)
            values[name] = value
        for name, value in values.items():
            match = GROUP_RE.match(name)
            if match is None:
                continue
            span = spans.get(f"list{match.group(1)}")
            if span is None:
                continue
            groups.append((_strip_comments(value).strip(), start + span[0], start + span[1]))
    return groups


def find_group_span(text: str, keywords: Sequence[str],
                    exclude: Sequence[str] = ()) -> Optional[Tuple[str, int, int]]:
    """找标签含 keywords 之一、且不含 exclude 之一的组，返回 (标签, 值起点, 值终点)。"""
    for label, start, end in iter_groups(text):
        if not any(keyword in label for keyword in keywords):
            continue
        if any(bad in label for bad in exclude):
            continue
        return label, start, end
    return None


def find_embedded_group(text: str, keywords: Sequence[str],
                        exclude: Sequence[str] = (),
                        limit: int = 300) -> Optional[Tuple[str, int, int]]:
    """找「标题行写在 `|listN =` 里面」的那种组，返回 (标签, 值起点, 值终点)。

    见 `EMBEDDED_LABEL_RE` 上面那段：`Template:猫村伊吕波` 的「其他歌曲」就是
    `<tr><th>其他歌曲<sub>(点击展开)</sub></th></tr>` 直接写在 `|list3 =` 里的，
    `|groupN` 那一层根本没有它。
    """
    for name, value, start, end in scan_params(text):
        if not LIST_PARAM_RE.match(name.strip()):
            continue
        for label in EMBEDDED_LABEL_RE.findall(value[:limit]):
            if not any(keyword in label for keyword in keywords):
                continue
            if any(bad in label for bad in exclude):
                continue
            return label.strip(), start, end
    return None


def find_list_group(text: str, keywords: Sequence[str],
                    exclude: Sequence[str] = ()) -> Optional[Tuple[str, int, int]]:
    """先找正常的 `|groupN =`，找不到再看「标题写在 `|listN` 里」的那种（见 `find_embedded_group`）。"""
    return (find_group_span(text, keywords, exclude)
            or find_embedded_group(text, keywords, exclude))


def has_group(text: str, keywords_list: Sequence[Sequence[str]],
              exclude: Sequence[str] = ()) -> bool:
    """这几组关键词里是否至少有一个能在模板中找到对应的分组。"""
    return any(find_group_span(text, keywords, exclude) is not None
               for keywords in keywords_list)


# 分组标签里的颜色参数不当作标签（`{{color|white|聲庫}}` → `聲庫`/
# `{{coloredlink|#fa006e|POPY}}` → `POPY`）；除了 #hex，还会写 white / black 这类颜色名
COLOR_ARG_RE = re.compile(
    r"^(?:#[0-9A-Fa-f]{3,8}|white|black|transparent|none|inherit|currentcolor)$", re.IGNORECASE)


def _short_label(label: str) -> str:
    """把分组标签里的模板调用压成可读文字（`{{coloredlink|#fa006e|POPY}}` → `POPY`）。"""
    text = _strip_comments(label or "")
    text = re.sub(r"<br[^>]*/?>", " ", text)
    while True:
        match = re.search(r"\{\{([^{}]*)\}\}", text)
        if match is None:
            break
        args = [part.strip() for part in match.group(1).split("|")[1:] if part.strip()]
        # 颜色之类的样式参数不当作标签
        keep = [part for part in args if not COLOR_ARG_RE.match(part)]
        text = text[:match.start()] + (keep[0] if keep else "") + text[match.end():]
    return re.sub(r"\s+", " ", text).strip()


def _parent_label(text: str, start: int, end: int) -> Optional[str]:
    """包含 [start, end) 的最小分组的标签（用于把路径写成「優秀曲目 → 2024年」）。"""
    best: Optional[Tuple[str, int, int]] = None
    for label, group_start, group_end in iter_groups(text):
        if not group_start <= start or not end <= group_end:
            continue
        if group_end - group_start <= end - start:
            continue                            # 不是外层
        if best is None or group_end - group_start < best[2] - best[1]:
            best = (label, group_start, group_end)
    return _short_label(best[0]) if best else None


def _pick_sub(subs: Sequence[Tuple[str, int, int]], site: Optional[str],
              year: Optional[int]) -> Optional[Tuple[str, int, int]]:
    """在子分组里挑一格：先按站点，再按年份，最后只有一格时就是它；挑不出返回 None。

    「只有一格就用它」是用户 2026-10-04 报的 `Template:重音Teto/2023` 那条：
    那个页面的「其他」组下是一个**没有标签**的子分组（就是未进殿堂的曲目列表），
    既没有站点标签也没有年份标签 —— 以前选不出来就**整条都不写**（只说一句
    「没有 2023 年的子列表」），条目自然没被同步进去。
    """
    if site:
        aliases = SITE_ALIASES.get(site, (str(site).lower(),))
        for label, start, end in subs:
            low = label.lower()
            if any(alias in low for alias in aliases):
                return label, start, end
    if year:
        for label, start, end in subs:
            match = YEAR_RE.search(label)
            if match and int(match.group(1)) == int(year):
                return label, start, end
    if len(subs) == 1:
        return subs[0]
    return None


def locate_list(text: str, keywords: Sequence[str], site: Optional[str] = None,
                year: Optional[int] = None, exclude: Sequence[str] = (),
                name: Optional[str] = None,
                embedded: bool = False) -> Optional[Tuple[int, int, List[str]]]:
    """定位要写入的 `|listN =` 值区间，返回 (值起点, 值终点, 标签路径)。

    组内若还按站点 / 年份分格会自动下钻（最多两层）；分不出该写哪一格时返回 None。
    `embedded=True` 时也认「标题行写在 `|listN` 里」的那种组（见 `find_embedded_group`）。
    """
    found = (find_list_group(text, keywords, exclude) if embedded
             else find_group_span(text, keywords, exclude))
    if found is None:
        return None
    _, start, end = found
    path = [name or str(keywords[0])]
    for _ in range(2):
        subs = iter_groups(text[start:end])
        if not subs:
            break
        picked = _pick_sub(subs, site, year)
        if picked is None:
            return None                      # 分不出该写哪一格，宁可不改
        label, sub_start, sub_end = picked
        path.append(_short_label(label))
        start, end = start + sub_start, start + sub_end
    return start, end, path


def _no_sublist(name: str, site: Optional[str], year: Optional[int]) -> str:
    if site and year:
        return f"「{name}」分组里没有 {site} / {year} 年 对应的子列表"
    if site:
        return f"「{name}」分组里没有 {site} 子列表"
    if year:
        return f"「{name}」分组里没有 {year} 年的子列表"
    return f"「{name}」分组里分不出该写哪一格"


# ---------------------------------------------------------------- 条目写法

def entry_link(page_name: str, ja_name: Optional[str] = None) -> str:
    """模板里的条目写法：日语原名与条目名不同时写成 `[[中文条目|日文原名]]`。"""
    page_name = (page_name or "").strip()
    ja_name = (ja_name or "").strip()
    if ja_name and ja_name != page_name:
        return f"[[{page_name}|{ja_name}]]"
    return f"[[{page_name}]]"


def honor_keywords(views: int) -> List[Tuple[str, ...]]:
    """歌曲已达成（含更低档）的荣誉分组关键词，从高到低。

    实测维基上同一首歌会同时列在已达成的各档里（例：神话曲《医学》也列在传说曲、殿堂曲下），
    所以这里返回的是一个列表，逐档插入。未达殿堂（10 万播放）时返回空。
    """
    return [keywords for threshold, keywords in HONOR_KEYWORDS if views >= threshold]


def _link_parts(entry: str) -> Optional[Tuple[str, Optional[str]]]:
    """拆出 `[[页面名|显示名]]`；不是链接时返回 None。"""
    match = LINK_RE.match((entry or "").strip())
    return (match.group(1), match.group(2)) if match else None


# `{{coloredlink|#fa006e|POPY}}` / `{{coloredlink|#fadc1f|HALO(梦的结唱)|HALO}}`
COLORED_LINK_RE = re.compile(
    r"\{\{\s*colou?red?link\s*\|\s*(#[0-9A-Fa-f]{3,8})\s*\|([^|}]+)(?:\|([^|}]+))?\}\}")
# 声库名上的消歧义后缀：`HALO(梦的结唱)` → `HALO`
DISAMBIG_SUFFIX_RE = re.compile(r"\s*[（(][^）)]*[）)]\s*$")


def voicebank_colors(text: str) -> Dict[str, str]:
    """从模板里现读「名字 → 颜色」表：`{{coloredlink|#fa006e|POPY}}` → POPY: #fa006e。

    实测 Template:梦的结唱 就是靠这种写法给每个声库（POPY / ROSE / PASTEL / HALO / AVER /
    MEW）配色的，条目也照这个颜色写 —— 所以直接从模板里现读，不写死一张颜色表。
    带消歧义后缀的名字（`HALO(梦的结唱)`）与显示名（第 3 个参数）都入表。
    """
    colors: Dict[str, str] = {}
    for match in COLORED_LINK_RE.finditer(text or ""):
        color = match.group(1)
        for name in (match.group(2), match.group(3) or ""):
            name = DISAMBIG_SUFFIX_RE.sub("", (name or "").strip()).strip()
            if name:
                colors.setdefault(name.lower(), color)
    return colors


def color_for(text: str, vocalists: Sequence[str] = ()) -> Optional[str]:
    """这首歌该用哪个颜色（取第一个能在模板配色表里找到的歌姬）；找不到返回 None。"""
    colors = voicebank_colors(text)
    if not colors:
        return None
    for vocalist in vocalists:
        name = DISAMBIG_SUFFIX_RE.sub("", str(vocalist or "").strip()).strip()
        color = colors.get(name.lower())
        if color:
            return color
    return None


def _item_style(body: str) -> str:
    """看列表里已有的条目用的是哪种写法。

    colored（`{{coloredlink|#色|页面|显示}}`，如 Template:梦的结唱）/ lj_out（`{{lj|[[…]]}}`）/
    lj_in（`[[…|{{lj|…}}]]`）/ plain。colored 要最先判：它的第 3 个参数里也带着 `{{lj|}}`。
    """
    if "{{coloredlink|" in body or "{{colorlink|" in body:
        return "colored"
    if "{{lj|[[" in body:
        return "lj_out"
    if "|{{lj|" in body:
        return "lj_in"
    return "plain"


# 日文（假名 / 汉字）：`{{lj|}}` 只套在日文原名外面（用户 2026-10-28 定的口径）
JAPANESE_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def is_japanese_text(text: str) -> bool:
    """这段文字里有没有假名 / 汉字（决定要不要套 `{{lj|}}`）。"""
    return bool(JAPANESE_TEXT_RE.search(str(text or "")))


def _style_entry(entry: str, style: str, color: Optional[str] = None) -> str:
    """把条目改写成目标列表惯用的写法（没有原名、或已经套过 `{{lj|}}` 就不动它）。

    `style == "colored"` 时按 `color`（歌姬在模板里的颜色）写成
    `{{coloredlink|#色|页面|{{lj|日文}}}}`；**拿不到颜色就不猜**，退回 `[[页面|{{lj|日文}}]]`。

    `plain` / `lj_out` 两种平铺列表统一按**原名是不是日文**决定（用户 2026-10-28）：
    日文原名 → `{{lj|[[页面|原名]]}}`，英文原名（`EDEN` / `ZERO BLADE`）→ 裸链接。
    以前是「整段里只要有 `{{lj|[[` 就套」，于是英文原名也跟着被套上
    （`Template:IA/2022` 的 `{{lj|[[EDEN(醉shigure)|EDEN]]}}` 就是这么来的）。
    """
    parts = _link_parts(entry)
    if parts is None or not parts[1]:
        return entry
    if style == "colored":
        if not color:
            return _style_entry(entry, "lj_in")
        display = parts[1] if "{{lj|" in parts[1] else f"{{{{lj|{parts[1]}}}}}"
        return f"{{{{coloredlink|{color}|{parts[0]}|{display}}}}}"
    if "{{lj|" in parts[1]:               # 已经是 `[[中文|{{lj|日文}}]]`，别再套一层
        return entry
    if style == "lj_in":
        return f"[[{parts[0]}|{{{{lj|{parts[1]}}}}}]]"
    return "{{lj|" + entry + "}}" if is_japanese_text(parts[1]) else entry


def _single_wrapper(body: str) -> Optional[str]:
    """整段就是一个模板调用时返回它的名字（如 lj / hlist），否则返回 None。"""
    if not body.startswith("{{"):
        return None
    if _block_end(body, 0) != len(body):
        return None
    match = re.match(r"\{\{\s*([A-Za-z][\w/]*)", body)
    return match.group(1) if match else None


def _append_piped(body: str, item: str) -> str:
    """往用 `|` 分项的模板调用（`{{hlist|…}}` / `{{links|…}}`）里追加一项，沿用换行与缩进。"""
    inner = body[:-2]                        # 去掉结尾的 `}}`
    prefix = inner.rstrip()
    if prefix.endswith("|"):
        prefix = prefix[:-1]
    if "\n" not in inner:
        return prefix + "|" + item + "}}"
    indent = ""
    for line in reversed(inner.split("\n")):
        if line.strip():
            indent = line[:len(line) - len(line.lstrip())]
            break
    close_indent = inner[len(inner.rstrip()):].split("\n")[-1]
    return prefix + "\n" + indent + "|" + item + "\n" + close_indent + "}}"


# 「一行一项」的列表：每行以 `-->` 开头（`{{W}}<!--\n<缩进>-->[[下一首]]`）
LINE_ITEM_RE = re.compile(r"\n([ \t]*)-->")


def _line_indent(body: str) -> Optional[str]:
    """一行一项的列表里，`-->` 行用的缩进；不是这种写法返回 None。"""
    matches = list(LINE_ITEM_RE.finditer(str(body or "")))
    return matches[-1].group(1) if matches else None


def _append_item(body: str, item: str, separator: str = "{{W}}") -> str:
    """把一项接到列表末尾：一行一项的写法**另起一行**，否则按分隔符接在后面。

    站上的年份模板都是「一行一项」（`Template:IA/2022`、`Template:Flower/2022`、
    `Template:重音Teto/2024`）；以前直接拼在末项同一行
    （`-->[[Acceleration!!|…]]{{W}}{{lj|[[EDEN…]]}}}}`），站上要再拆成一行
    （用户 2026-10-28 给的 276340 → 276359 差异）。

    两种分隔符的换行位置不一样（都是站上实测）：
      * `{{W}}`：`条目{{W}}<!--\\n<缩进>-->下一条`
      * ` • ` ：`条目<!--\\n<缩进>--> • 下一条`
    """
    text = str(body or "").strip()
    if not text:
        return item
    indent = _line_indent(text)
    if indent is None:
        return text + separator + item
    tail = text.rstrip()
    if separator == "{{W}}":
        if tail.endswith(separator):       # 末项后面已经带了分隔符（少见），别再补一个
            return tail + "<!--\n" + indent + "-->" + item
        return tail + separator + "<!--\n" + indent + "-->" + item
    # ` • `：两种换行位置站上都有，跟着邻居走
    if re.search(r"\n[ \t]*-->[ \t]*•", text):     # `条目<!--\n<缩进>--> • 下一条`
        return tail + "<!--\n" + indent + "--> • " + item
    return tail + " • <!--\n" + indent + "-->" + item   # `条目 • <!--\n<缩进>-->下一条`


def append_entry(list_value: str, entry: str, links_entry: Optional[str] = None,
                 color: Optional[str] = None, wrapped: bool = False) -> str:
    """把条目追加到列表末尾，尽量沿用列表里已有的写法。

    - 整段 `{{lj|…}}` 包住 → 递归进去，在里面用原本的分隔符接上（**新条目是裸链接**，
      外面那层 `{{lj}}` 已经包好了）；
    - `{{links|…}}` → 用 `页面名{{!}}显示名` 的写法接一项（即 `links_entry`）；
    - `{{hlist|…}}` → 用 `|` 接上一项；
    - 平铺列表 → 沿用原有的 `{{W}}` / ` • ` 分隔符（一行一项的写法会**另起一行**），
      并按需套上 `{{lj|…}}`；邻居是 `{{coloredlink|#色|…}}` 时按 `color` 配色（见 `color_for`）。

    `wrapped=True` 表示外层已经有 `{{lj|…}}` 包着（递归进来时用），条目就**不要再套一层**。
    """
    body = (list_value or "").strip()
    if not body:
        return "{{lj|" + entry + "}}"
    wrapper = _single_wrapper(body)
    if wrapper and wrapper.lower() == "links":
        return _append_piped(body, links_entry or entry)
    if wrapper == "hlist":
        item = entry if wrapped else _style_entry(entry, _item_style(body), color)
        return _append_piped(body, item)
    if wrapper == "lj":
        inner = body[len("{{lj|"):-2].strip()
        if not inner:
            return "{{lj|" + (links_entry or entry) + "}}"
        if _single_wrapper(inner):           # 里面还套着 links / hlist（如 40mP、buzzG）
            return "{{lj|" + append_entry(inner, entry, links_entry, color, wrapped=True) + "}}"
        separator = " • " if "•" in inner else "{{W}}"
        return "{{lj|" + _append_item(inner, entry, separator) + "}}"
    separator = " • " if "•" in body else "{{W}}"
    item = entry if wrapped else _style_entry(entry, _item_style(body), color)
    return _append_item(body, item, separator)


def _comment_date(comment: str) -> Optional[CommentDate]:
    """条目注释里的日期 → `CommentDate`；注释里没写时间的按 0:00 算。

    两种写法都认：`02-22 23:00`（Template:可不/2024）与 `11-03-16` / `2011-03-16`
    （Template:猫村伊吕波 的「其他歌曲」，两位年就是 20xx 年）。
    """
    match = COMMENT_DATE_RE.search(comment or "")
    if match is None:
        return None
    first = int(match.group("first"))
    second = int(match.group("second"))
    third = match.group("third")
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    if third is None:
        return CommentDate(None, first, second, hour, minute)
    digits = len(match.group("first"))
    return CommentDate(first if digits >= 4 else 2000 + first,
                       second, int(third), hour, minute, digits)


def _has_time_of(comment: str) -> bool:
    """注释里写没写时分（决定新条目要不要跟着写 `00:00`）。"""
    match = COMMENT_DATE_RE.search(comment or "")
    return bool(match is not None and match.group("hour"))


def _dated_items(inner: str) -> List[Tuple[int, CommentDate, Optional[str], bool]]:
    """列出带日期注释的条目：(`<!--` 之后的位置, 日期, 缩进, 注释里有没有写时分)。

    条目 = `<!-- <日期> -->[[条目]]`。位置指向 `<!--` **之后**那一格 ——
    插入时正好把下一个条目的 `<!--` 借来当新行的行首（见 `_insert_dated`）。
    缩进是 `None` 表示那条注释是**紧跟在行内**的（如列表第一项 `{{lj|<!-- 03-16 -->…`），
    照抄它没有意义 —— 那时用要插入位置那一条的缩进（见 `_insert_dated`）。
    """
    items: List[Tuple[int, CommentDate, Optional[str], bool]] = []
    for match in ITEM_COMMENT_RE.finditer(inner or ""):
        comment = match.group(1)
        date = _comment_date(comment)
        if date is None:
            continue
        date_match = COMMENT_DATE_RE.search(comment)
        head = comment[:date_match.start()]
        indent = (re.match(r"[ \t]*", head.rsplit("\n", 1)[-1]).group(0)
                  if "\n" in head else None)
        items.append((match.start(1), date, indent, _has_time_of(comment)))
    return items


def _year_width(items: Sequence[CommentDate]) -> int:
    """邻居注释里年份写几位（0 = 都不写年，2 = `11-03-16`，4 = `2011-03-16`）。"""
    return max((date.year_digits for date in items), default=0)


# `{{links}}` 列表里两种日期注释写法都实测过：
#   ① Template:鸣花姬·尊 —— 注释跟在 `{{links` 后面，日期在注释**末尾**、紧挨着 `|`：
#     |list8 = {{lj|{{links<!--
#        02-20 23:00 -->|冥<!--
#        07-15 05:00 -->|1(Mealerrand){{!}}1<!--
#     }}}}
#   ② Template:猫村伊吕波 —— 注释跟在 `|` 后面，日期在注释**开头**，条目的收尾 `-->|`
#     在下一行（diff 273880：`|<!-- 11-03-16 -->171<!-- \n         -->|`）：
#     |list3 = {{lj|{{links|<!-- 10-11-05 -->Harmonia{{!}}<del>ハーモニア</del><!--
#              -->|<!-- 11-02-03 -->毁灭之匙与摇篮曲{{!}}<del>滅びの鍵と子守唄</del><!--
#              -->}}}}
# 条目写法都是 `页面名{{!}}显示名`。
LINKS_STAMP_RE = re.compile(r"<!--([\s\S]*?)-->(\s*)\|")


def _insert_dated_links(body: str, item: str, posted: PostedAt) -> Optional[str]:
    """`{{links}}` 列表按投稿时间插入一项（`<!-- 日期 -->|页面{{!}}显示` 或 `|<!-- 日期 -->页面`）。

    与平铺列表同一口径（见 `_insert_dated`）：插在**第一条日期更晚**的条目前面，
    同一天的排在已有的后面。列表里没有日期注释、或自己比所有条目都晚时返回 None
    （调用方退回「追加到末尾」）。

    用户 2026-10-14 对照 Template:鸣花姬·尊 diff 261395：新条目本该插在
    `<!-- 02-20 23:00 -->|冥` 之后（同一天），站上就是这么改的。
    用户 2026-10-20 对照 Template:猫村伊吕波 diff 273880：那种「日期在注释开头」的写法里
    要整段插在下一项的 `<!--` **前面**，否则条目会跟下一项粘在一起（少了 `|`）。
    """
    marks: List[Tuple[int, CommentDate, str, bool, bool]] = []
    for match in LINKS_STAMP_RE.finditer(body or ""):
        comment = match.group(1)
        date = _comment_date(comment)
        if date is None:
            continue
        date_match = COMMENT_DATE_RE.search(comment)
        before = comment[:date_match.start()]
        inline = "\n" not in before           # 日期紧跟 `<!--`（写法②，见上面的注释）
        if inline:
            tail = re.search(r"\n([ \t]*)$", comment)
            indent = tail.group(1) if tail else ""
            position = match.start()          # 整段插在下一项的 `<!--` 之前
        else:
            indent = re.match(r"[ \t]*", before[before.rfind("\n") + 1:]).group(0)
            # 插在 `<!--` **之后**：新行自己收尾的 `<!--` 正好把下一项原来的日期注释接回来
            position = match.start() + len("<!--")
        marks.append((position, date, indent, _has_time_of(comment), inline))
    if not marks:
        return None
    fallback = posted.when.year
    later = next((mark for mark in marks
                  if mark[1].sort_key(fallback) > posted.key()), None)
    if later is None:
        return None                       # 比列表里所有条目都晚 → 交给调用方追加到末尾
    position, _date, indent, _has_time, inline = later
    stamp = posted.stamp(any(mark[3] for mark in marks),
                         _year_width([mark[1] for mark in marks]))
    if inline:
        line = f"<!-- {stamp} -->{item}<!--\n{indent}-->|"
    else:
        line = "\n" + indent + stamp + " -->|" + item + "<!--"
    return body[:position] + line + body[position:]


def _insert_dated(value: str, entry: str, posted: Optional[PostedAt],
                  color: Optional[str] = None,
                  links_item: Optional[str] = None,
                  styled: Optional[str] = None) -> Optional[str]:
    """平铺列表按**投稿时间**插入条目；认不出日期时返回 None（调用方退回追加到末尾）。

    实测 Template:可不/2024 的荣誉 / 「其它」列表是按 niconico 投稿时间排的
    （`<!-- 02-22 23:00 -->[[活死人乐队|リビングデッドバンデッド]]{{W}}`，同一时刻的排在一起），
    以前只会把新条目追加到末尾 —— 用户 2026-09 报「应该插在相同日期的那几条后面」。
    新条目的注释照抄邻居的排版（缩进、站点前缀、`<!--` 换行写法）。

    `styled` 已经给了就用它当条目文本（整段被 `{{lj|…}}` 包住时由外层传裸链接进来）。
    """
    if posted is None:
        return None
    body = (value or "").strip()
    if not body:
        return None
    wrapper = _single_wrapper(body)
    if wrapper == "lj":                        # 整段被 `{{lj|…}}` 包住 → 进去改
        inner = body[len("{{lj|"):-2]
        updated = _insert_dated(inner, entry, posted, color, links_item,
                                styled if styled is not None else entry)
        return None if updated is None else "{{lj|" + updated + "}}"
    if wrapper is not None:                    # hlist 之类不按日期插
        if wrapper.lower() != "links" or links_item is None:
            return None
        return _insert_dated_links(body, links_item, posted)
    items = _dated_items(body)
    if not items:
        return None
    if styled is None:
        styled = _style_entry(entry, _item_style(body), color)
    key = posted.key()
    fallback = posted.when.year
    later = next((item for item in items if item[1].sort_key(fallback) > key), None)
    separator = " • " if "•" in body else "{{W}}"
    # 注释写法照邻居：邻居都写了时分就写时分（哪怕自己是 00:00），否则只写日期；
    # 邻居都写了年（`11-03-16`）时自己也要写，否则插进去就它一个短一截
    stamp = posted.stamp(any(item[3] for item in items),
                         _year_width([item[1] for item in items]))
    if later is None:                          # 比列表里所有条目都晚 → 追加到末尾
        return body + separator + "<!--\n" + (items[-1][2] or "") + stamp + " -->" + styled
    position, _date, indent, _has_time = later
    # 注释是行内写法（列表第一项就是 `{{lj|<!-- 03-16 -->…`）→ 接着同一行写；
    # 否则另起一行、照抄这一条的缩进（对齐全列）
    pad = " " if indent is None else "\n" + indent
    # 插在下一个条目的 `<!--` 后面：新行自己收尾的 `<!--` 正好接上它本来的注释内容
    line = pad + stamp + " -->" + styled + separator + "<!--"
    return body[:position] + line + body[position:]


def _needs_newline(text: str, value_start: int, value_end: int) -> bool:
    """原值为空、且 `=` 后本来换行时，插入后补一个换行，避免把下一个参数挤到同一行。"""
    if text[value_start:value_end].strip():
        return False
    equal = text.rfind("=", max(0, value_start - 200), value_start)
    return equal >= 0 and "\n" in text[equal + 1:value_start]


# ---------------------------------------------------------------- 按投稿日期插入（没有日期注释的列表）
# Songbox 里的投稿日：`|nnd_date = 2022-04-21` / `|bb_date =` / `|yt_date =`，
# 以及删稿条目用的 `{{VOCALOID Songbox/card|nnd|sm40348523|2022-04-21|…}}`。
DATE_PARAM_RE = re.compile(r"\|\s*(?:nnd|bb|yt)_date\s*=\s*(\d{4})-(\d{1,2})-(\d{1,2})")
CARD_DATE_RE = re.compile(
    r"\{\{\s*VOCALOID\s+Songbox/card\s*\|[^}]*?\|\s*(\d{4})-(\d{1,2})-(\d{1,2})", re.IGNORECASE)
# 条目名 → 最早投稿日 (年, 月, 日)；`None` = 认不出（页面上没有 Songbox 日期）。一次运行内一直有效。
_PUBLISH_DATE_CACHE: Dict[str, Optional[Tuple[int, int, int]]] = {}
# 要不要**去现取**各条目的投稿日。只在真正写回模板时打开（`sync_template()`）：
# 「准备怎么改」的预览与单测都不该为此多发请求（预览的提示文字也用不到位置）。
RESOLVE_PUBLISH_DATES = False


@contextlib.contextmanager
def _resolving_publish_dates():
    """写回模板期间的开关：允许 `_insert_by_date()` 去现取投稿日。"""
    global RESOLVE_PUBLISH_DATES
    previous = RESOLVE_PUBLISH_DATES
    RESOLVE_PUBLISH_DATES = True
    try:
        yield
    finally:
        RESOLVE_PUBLISH_DATES = previous


def publish_date_from_text(text: str) -> Optional[Tuple[int, int, int]]:
    """条目正文 → 最早投稿日 (年, 月, 日)；认不出返回 None。"""
    dates = []
    for pattern in (DATE_PARAM_RE, CARD_DATE_RE):
        for match in pattern.finditer(text or ""):
            dates.append((int(match.group(1)), int(match.group(2)), int(match.group(3))))
    return min(dates) if dates else None


def song_publish_dates(titles: Sequence[str]) -> Dict[str, Optional[Tuple[int, int, int]]]:
    """批量取这些条目的投稿日（`{条目: (年,月,日) 或 None}`）。

    一次 `action=query` 带 50 个标题（`wiki_api.fetch_pages_text()` 自己会分批），
    结果进模块缓存 —— 预览（`plan()`）与写回（`sync()`）同一次运行里只请求一遍。
    """
    wanted = list(dict.fromkeys(str(title).strip() for title in titles if str(title or "").strip()))
    missing = [title for title in wanted if title not in _PUBLISH_DATE_CACHE]
    if missing:
        texts = wiki_api.fetch_pages_text(missing)
        for title in missing:
            if title in texts:
                _PUBLISH_DATE_CACHE[title] = publish_date_from_text(texts[title])
            elif texts:
                # 有返回、只是没有这一页 → 页面不存在（或没有正文），记 None；
                # 整批取失败（请求出错，texts 为空）时**不写缓存**，下次还能再试
                _PUBLISH_DATE_CACHE[title] = None
    return {title: _PUBLISH_DATE_CACHE.get(title) for title in wanted}


def _list_links(body: str) -> List[Tuple[int, str]]:
    """列表里的条目链接 → `[(链接位置, 条目名)]`（跳过带命名空间的、去重）。"""
    items: List[Tuple[int, str]] = []
    seen: Dict[str, int] = {}
    for match in INLINE_LINK_RE.finditer(body or ""):
        target = (match.group(1) or "").strip().split("#")[0].strip()
        if not target or ":" in target:
            continue
        if target in seen:
            continue
        seen[target] = match.start()
        items.append((match.start(), target))
    return items


def _insert_before(body: str, position: int, item: str, indent: Optional[str]) -> str:
    """把一项插到 `position` 那一项**前面**（一行一项的写法按 `-->` 行插，否则按分隔符插）。"""
    separator = " • " if "•" in body else "{{W}}"
    if indent is None:                    # 平铺在同一行（` • ` / `{{W}}` 分隔）
        previous = body.rfind(separator, 0, max(0, position))
        start = previous + len(separator) if previous >= 0 else 0
        return body[:start] + item + separator + body[start:]
    line_start = body.rfind("\n", 0, max(0, position)) + 1
    if line_start == 0:                   # 要插在第一项之前：自己写一行，后面接回 `-->`
        return item + separator + "<!--\n" + indent + "-->" + body
    return body[:line_start] + indent + "-->" + item + separator + "<!--\n" + body[line_start:]


def _insert_by_date(value: str, entry: str, posted: Optional[PostedAt],
                    color: Optional[str] = None,
                    styled: Optional[str] = None) -> Optional[str]:
    """列表**没写日期注释**时，按投稿日期插到第一个「更晚」的条目之前。

    站上的年份模板常按投稿日排（实测 `Template:IA/2022` 的「其他」栏：
    2013-08-06 → … → 2014-11-04 → … → 2021-11-30 → 2022-04-22 → 2022-11-24），
    但没写 `<!-- 日期 -->` 注释，`_insert_dated()` 认不出来，以前只能追加到末尾 ——
    用户 2026-10-28 给的差异里本曲（2022-04-21）应当插在 `Noble Rose`(2022-04-22) 之前。
    日期从各条目的 Songbox 现取（见 `song_publish_dates()`，带缓存）。
    认不出日期 / 条目太少 / 本曲比所有条目都晚 → 返回 None（调用方追加到末尾）。

    ⚠️ 只有写回模板时才去现取（`RESOLVE_PUBLISH_DATES`，见 `_resolving_publish_dates()`）：
    预览与单测不联网；已经缓存过的日期照样能用。
    """
    if posted is None or not RESOLVE_PUBLISH_DATES:
        return None
    body = (value or "").strip()
    if not body:
        return None
    wrapper = _single_wrapper(body)
    if wrapper == "lj":                   # 整段被 `{{lj|…}}` 包住 → 进去插，新条目保持裸链接
        inner = body[len("{{lj|"):-2]
        updated = _insert_by_date(inner, entry, posted, color, styled=entry)
        return None if updated is None else "{{lj|" + updated + "}}"
    if wrapper is not None:               # hlist / links 之类不按日期插
        return None
    items = _list_links(body)
    if len(items) < 2:
        return None
    dates = song_publish_dates([target for _position, target in items])
    ours = (posted.when.year, posted.when.month, posted.when.day)
    indent = _line_indent(body)
    item = styled if styled is not None else _style_entry(entry, _item_style(body), color)
    for position, target in items:
        date = dates.get(target)
        if date is not None and date > ours:      # 同一天的不插它前面（站上把同日的排在一起）
            return _insert_before(body, position, item, indent)
    return None


def _has_entry(value: str, entry: str, producers: Sequence[str] = ()) -> bool:
    """目标列表里是否已有该条目（按页面名判断，兼容 `{{lj|[[…]]}}` 等几种写法）。

    2026-10-06 补：**条目名 / 日文名写反时也要认得出来**。那种输入下 `entry` 是
    `[[日文名|中文名]]`，而列表里写的是 `[[中文名|日文名]]` —— 按页面名找不见，
    于是同一首歌又被追加一条。这里再按「显示名 == 这首歌的日文名」看一眼。

    2026-10-28 改：同名条目并存时（`EDEN` / `EDEN(醉shigure)` / `EDEN(Mitsubachi Bocchi)`）
    只按歌名认会把**别人**那条算成「已经有了」，所以归属由 `link_score()` 判断。
    裸链接（`{{lj|[[日文名]]}}`）**不算「已经有了」** —— 它还得改指到中文条目。
    """
    parts = _link_parts(entry)
    if parts is None:
        return entry in value
    names = entry_names(entry, producers)
    for link in INLINE_LINK_RE.finditer(value):
        if link_score(names, link.group(1), link.group(2)) >= OWN_OURS:
            return True
        # `[[别的条目名|日文名]]`：这首歌已经链着了（只是条目名不是 entry 里那个）
        display = _norm_name(link.group(2))
        if display and display in names.bases and _norm_name(link.group(1)) != names.page:
            return True
    return False


def relink_entry(value: str, entry: str,
                 producers: Sequence[str] = ()) -> Tuple[str, int]:
    """把列表里「按日文原名链着的同一首歌」改指到中文条目，返回 (新文本, 改了几处)。

    榜单模板里常出现这两种旧写法（条目还没建、或条目改过名、译名变了）：
        {{lj|[[どろぼうねこ]]}}        → {{lj|[[偷腥猫|どろぼうねこ]]}}
        {{lj|[[旧译名|どろぼうねこ]]}}  → {{lj|[[偷腥猫|どろぼうねこ]]}}
    只看中文条目名的 _has_entry 认不出它们，会把同一首歌又追加一遍，所以这里先试着改指。
    只在 entry 写成 `[[中文条目|日文原名]]`（两个名字不同）时生效，显示名一律保留原样。

    2026-10-28 改：**同名歌的链接不能动**。`{{lj|[[EDEN]]}}`（另一首《EDEN》，榜上第 97 名）
    以前会被改指成 `[[EDEN(醉shigure)|EDEN]]`，站上只好再改回去；现在只改
    `歌名(我们的P主)` 与完整条目名，裸链接按 `bare_link_offsets()` 判断（同一赛道里
    我们已经有条目了，那条裸链接就不是我们的）。
    """
    parts = _link_parts(entry)
    if parts is None:
        return value, 0
    page_name, alias = parts
    if not alias or _norm_name(alias) == _norm_name(page_name):
        return value, 0
    names = entry_names(entry, producers)
    bare = bare_link_offsets(value, names)
    count = 0

    def replace(match: "re.Match") -> str:
        nonlocal count
        target, display = (match.group(1) or "").strip(), (match.group(2) or "").strip()
        score = link_score(names, target, display)
        if score < OWN_OURS and match.start() not in bare:
            return match.group(0)
        if _norm_name(target) == names.page:
            return match.group(0)             # 已经指到中文条目了
        if _norm_name(display) == names.page:
            # ⚠️ 要改成裸链 `[[日文名]]`（**把原来的目标去掉**）—— 不做。
            # 这是用户 2026-10-04 报的 bug：`[[FrailL'aVillanos|フレイラヴィランゼ]]`
            # 被这次「同步大家族模板」抹成了 `[[フレイラヴィランゼ]]`，条目白改了。
            return match.group(0)
        count += 1
        # 显示名保留模板里原来的写法（宽度可能和输入不同：模板写全角 `！`、输入半角 `!`）
        shown = display or target
        return f"[[{page_name}|{shown}]]"

    return INLINE_LINK_RE.sub(replace, value), count


LINKS_CALL_RE = re.compile(r"\{\{\s*links\b", re.IGNORECASE)


def _links_call_span(value: str) -> Optional[Tuple[int, int]]:
    """值里 `{{links…}}` 的位置区间（前面/后面可能还有 HTML 之类，实测 Template:猫村伊吕波）。

    `|list3 = <tr><th …>其他歌曲…</th></tr>…{{lj|{{links|<!-- 11-03-16 -->171…}}}}…td></tr>`
    这种写法里，`{{links}}` 只占整个值的一小段 —— 只按「整段就是 links」判断会把条目
    追加到那堆 HTML 后面去（站上编辑不得不手工把 `171` 挪回列表里）。
    也认 `{{links<!--\n   02-20 23:00 -->|冥`（Template:鸣花姬·尊）那种注释夹在中间的写法。
    """
    match = LINKS_CALL_RE.search(value or "")
    if match is None:
        return None
    depth = 0
    index = match.start()
    while index < len(value) - 1:
        if value.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if value.startswith("}}", index):
            depth -= 1
            index += 2
            if depth <= 0:
                return match.start(), index
            continue
        index += 1
    return None


def _insert_at(text: str, span: Tuple[int, int, List[str]], entry: str,
               links_entry: Optional[str] = None, color: Optional[str] = None,
               posted: Optional["PostedAt"] = None) -> Tuple[str, str]:
    """把条目追加到 span 指向的 `|listN =` 值里（含查重与「改指旧写法」），返回 (新文本, 说明)。

    `{{links|…}}` 列表里的条目**不能写成 `[[页面名|显示名]]`**，要写成 `页面名{{!}}显示名`
    （用户 2026-10-14 对照 Template:鸣花姬·尊 diff 261395：旧实现把链接当普通参数追加到
    `{{links}}` 末尾，站上改成 `|我爱你(Mealerrand){{!}}愛してます` 并插在对应日期位置）。
    """
    value_start, value_end, path = span
    where = " → ".join(path)
    value = text[value_start:value_end]
    call_span = _links_call_span(value)
    if call_span is not None:
        call = value[call_span[0]:call_span[1]]
        links_call = _unwrap(call)             # `{{lj|{{links|…}}}}` → `{{links|…}}`
        page_name, ja_name = _link_parts(entry) or ((entry or "").strip(), None)
        if _has_links_entry(value, page_name):
            return text, f"「{where}」里已有该条目，未重复添加"
        relinked, changed = relink_links_item(links_call, page_name, ja_name)
        if changed:
            new_call = call.replace(links_call, relinked, 1)
            return (text[:value_start] + value[:call_span[0]] + new_call
                    + value[call_span[1]:] + text[value_end:]), \
                f"已把「{where}」里的「{ja_name}」改指到「{page_name}」"
        item = links_entry or _links_item(page_name, ja_name, links_call)
        new_call = _insert_dated(call, entry, posted, color, links_item=item)
        if new_call is None:
            new_call = append_entry(call, entry, item, color)
        return (text[:value_start] + value[:call_span[0]] + new_call
                + value[call_span[1]:] + text[value_end:]), f"已加入「{where}」"
    if _has_entry(value, entry):
        return text, f"「{where}」里已有该条目，未重复添加"
    relinked, changed = relink_entry(value, entry)
    if changed:
        # 同一首歌以前只能用日文原名链，现在条目建好了 → 改指到中文条目（不重复追加）
        parts = _link_parts(entry)
        return text[:value_start] + relinked + text[value_end:], \
            f"已把「{where}」里的「{parts[1]}」改指到「{parts[0]}」"
    # 列表带投稿时间注释时按时间插；没有注释但能认出各条的投稿日时也按日期插；
    # 都认不出才照旧追加到末尾
    new_value = _insert_dated(value, entry, posted, color)
    if new_value is None:
        new_value = _insert_by_date(value, entry, posted, color)
    if new_value is None:
        new_value = append_entry(value, entry, links_entry, color)
    if _needs_newline(text, value_start, value_end):
        new_value += "\n"
    return text[:value_start] + new_value + text[value_end:], f"已加入「{where}」"


def add_entry(text: str, site: Optional[str], keywords: Sequence[str], entry: str,
              year: Optional[int] = None, exclude: Sequence[str] = (),
              name: Optional[str] = None, vocalists: Sequence[str] = (),
              posted: Optional["PostedAt"] = None,
              embedded: bool = False) -> Tuple[str, str]:
    """把 `entry` 加入「标签含 keywords 的组 → site 子列表」。

    返回 (新文本, 说明)；无法插入时新文本与原文相同，说明写明原因。
    邻居用 `{{coloredlink|#色|…}}` 时按 `vocalists` 里的歌姬配色（见 `color_for`）；
    列表按投稿时间分列时按 `posted` 插到对应位置（见 `_insert_dated`）。
    `embedded=True` 时也认「标题行写在 `|listN` 里」的那种组（见 `find_embedded_group`）。
    """
    if not text:
        return text, "模板内容为空"
    if not keywords:
        return text, "未达殿堂（10 万播放），无需加入荣誉小节"
    label = name or str(keywords[0])
    span = locate_list(text, keywords, site=site, year=year, exclude=exclude, name=label,
                       embedded=embedded)
    if span is None:
        if (find_list_group(text, keywords, exclude) if embedded
                else find_group_span(text, keywords, exclude)) is None:
            return text, f"模板里没有「{label}」分组"
        return text, _no_sublist(label, site, year)
    return _insert_at(text, span, entry, color=color_for(text, vocalists), posted=posted)


# 年份子页：`Template:重音Teto/2023`、`Template:可不/2021及以前`
YEAR_SUBPAGE_RE = re.compile(r"^[^/]+/\d{4}(?:及以前)?$")


def is_year_subpage(template: str) -> bool:
    """这个模板名是不是「年份子页」（`歌姬/2023`）。

    为什么要区分：子页里的曲目是按**站点**分组的（`|group1 = niconico` / `YouTube` /
    `bilibili`），年份已经写在页面名上了 —— 往里写条目时要靠「站点」选格，
    靠「年份」永远选不出来。用户 2026-10-04 报的就是这个：提交《Lie Lie Layla》时
    `Template:重音Teto/2023` 没被自动写上，日志只说「没有 2023 年的分组」。
    """
    return bool(YEAR_SUBPAGE_RE.match(_template_title(template)))


def add_non_honor(text: str, entry: str, year: Optional[int] = None,
                  vocalists: Sequence[str] = (),
                  posted: Optional["PostedAt"] = None,
                  title: str = "") -> Tuple[str, List[str]]:
    """未达殿堂（10 万播放）的歌曲写进「部分非殿堂曲」一组，返回 (新文本, 说明)。

    模板里没有这一组时退到「其他 / 其它」那组（实测 Template:NurseRobot_TypeT 只有
    「传说曲 / 殿堂曲 / 其他」，未达殿堂的歌不写进「其他」就没地方去了）；
    两组都没有时再退到「按投稿年份写」（见 `add_by_year`）。

    ⚠️ **年份子页（`歌姬/2023`）要按「站点」选格**（用户 2026-10-04 报的 bug）：那种页面里
    曲目是按 niconico / YouTube / bilibili 分组的，年份已经写在页面名上了 —— 只按年份找
    子列表永远找不到，于是**一个字都不写**（日志只说「没有 2023 年的分组」）。
    """
    # 年份子页用投稿站点选格；其它模板照旧只按年份（见 `is_year_subpage`）
    site = posted.site if (posted is not None and title and is_year_subpage(title)) else None
    for keywords, name in ((NON_HONOR_KEYWORDS, NON_HONOR_NAME),
                           (NON_HONOR_FALLBACK, NON_HONOR_FALLBACK_NAME)):
        if find_list_group(text, keywords) is None:
            continue
        updated, detail = add_entry(text, site, keywords, entry, year=year, name=name,
                                    vocalists=vocalists, posted=posted, embedded=True)
        if updated != text or site is None:
            return updated, [detail]
        # 靠站点没选中（子页里那个站点这一档没有列表）→ 再试一次不带站点的选法
        updated, detail = add_entry(text, None, keywords, entry, year=year, name=name,
                                    vocalists=vocalists, posted=posted, embedded=True)
        return updated, [detail]
    updated, detail = add_by_year(text, entry, year, vocalists, posted)
    return updated, [detail]


def add_by_year(text: str, entry: str, year: Optional[int],
                vocalists: Sequence[str] = (),
                posted: Optional["PostedAt"] = None) -> Tuple[str, str]:
    """模板里没有荣誉 / 非殿堂分组时，按**投稿年份**写进年份格（返回 (新文本, 说明)）。

    实测 Template:梦的结唱（夢ノ結唱 的 POPY / ROSE / … 共用模板）根本不按荣誉分档，
    而是「優秀曲目 → 2022年 / 2023年 / …」这样按年份罗列曲目（同 P主模板）；
    以前这种模板只会提示「模板里没有“殿堂”分组」，条目就写不进去。
    """
    if year is None:
        return text, "模板里没有荣誉 / 非殿堂分组，且不知道投稿年份，无法按年份写入"
    span = locate_producer_list(text, year)
    if span is None:
        return text, f"模板里没有荣誉 / 非殿堂分组，也没有 {year} 年的分组"
    return _insert_at(text, span, entry, color=color_for(text, vocalists), posted=posted)


def add_honors(text: str, site: str, views: int, entry: str,
               year: Optional[int] = None,
               vocalists: Sequence[str] = (),
               posted: Optional["PostedAt"] = None) -> Tuple[str, List[str]]:
    """把条目加入该站点已达成的各档荣誉小组；未达殿堂时改写「部分非殿堂曲」。

    只往荣誉小节里写：**这一站在荣誉小节里没有子列表时一个字都不写**（实测 Template:可不/2023 的
    「殿堂曲」只有 niconico / bilibili 两格，而《做吧! 新鲜的小年轻》在 YouTube 上也到了殿堂）。
    要不要退到「部分非殿堂曲 / 其他」交给 `apply_honors()` —— 它得看**所有站点都试完**之后
    模板里到底有没有写进去：以前在这里按站点立即兜底，于是多站到殿堂的歌一头写进殿堂曲、
    一头又写进「其它」（用户 2026-09 对照 Template:可不/2023 的 diff 251215 报的：应改的
    diff 251213 比它多写了「其它」那一处）。
    """
    levels = honor_keywords(views)
    if not levels:
        return add_non_honor(text, entry, year, vocalists, posted)
    if not has_group(text, levels, exclude=NON_HONOR_KEYWORDS):
        # 模板里没有荣誉小节（按年份罗列曲目那种）→ 直接按投稿年份写
        updated, detail = add_by_year(text, entry, year, vocalists, posted)
        return updated, [detail]
    details: List[str] = []
    for keywords in levels:
        # 「非殿堂曲」一组也含「殿堂」二字，查荣誉小组时要排掉
        text, detail = add_entry(text, site, keywords, entry, year=year,
                                 exclude=NON_HONOR_KEYWORDS, vocalists=vocalists, posted=posted)
        details.append(detail)
    return text, details


def apply_honors(text: str, honors: Sequence[Tuple[str, int]], entry: str,
                 year: Optional[int] = None, vocalists: Sequence[str] = (),
                 posted: Optional[PostedAt] = None
                 ) -> Tuple[str, List[Tuple[str, int, List[str]]], List[str]]:
    """把各站点已达成的荣誉写进模板，返回 (新文本, [(站点, 播放量, 说明)…], 兜底说明)。

    兜底只在「所有站点都试完、模板里一处都没写进去、而且模板里本来也没有这首歌」时才发生：
    退回「部分非殿堂曲 / 其他」那组（参 voca.wiki《小小星座》：殿堂是在 YouTube 上到的，
    而 Template:可不/2024 的殿堂曲只列 niconico / bilibili，不兜底就一处都写不进去）。
    多站同时到殿堂时不会多写：荣誉小节里已经写进去一处，就不再往「其它」塞一份。
    """
    updated = text
    reports: List[Tuple[str, int, List[str]]] = []
    for site, views in honors:
        updated, details = add_honors(updated, site, views, entry, year, vocalists, posted)
        reports.append((site, views, details))
    if updated == text and not _has_entry(text, entry):
        updated, fallback = add_non_honor(updated, entry, year, vocalists, posted)
        return updated, reports, fallback
    return updated, reports, []


# ---------------------------------------------------------------- P主模板

def producer_year(label: str) -> Optional[int]:
    """年份分组标签 → 年份（`2013年` / `2020`）；不是年份组返回 None。"""
    match = PRODUCER_YEAR_RE.match(_short_label(label) or "")
    return int(match.group(1)) if match else None


def _unwrap(body: str) -> str:
    """剥掉外层的 `{{lj|…}}`，方便判断里面到底是 links / hlist 还是平铺列表。"""
    while True:
        body = (body or "").strip()
        if _single_wrapper(body) != "lj":
            return body
        body = body[len("{{lj|"):-2]


def _find_year_group(text: str, year: int) -> Optional[Tuple[str, int, int]]:
    """在（一段）模板文本里找年份格。"""
    for label, start, end in iter_groups(text):
        if producer_year(label) == year:
            return label, start, end
    return None


def locate_producer_list(text: str, year: Optional[int]) -> Optional[Tuple[int, int, List[str]]]:
    """定位 P主模板里投稿年份那一格，返回 (值起点, 值终点, 标签路径)。"""
    if year is None:
        return None
    # 1) 「原创 / 投稿」分组里的年份格
    for label, start, end in iter_groups(text):
        if not any(keyword in label for keyword in ORIGINAL_KEYWORDS):
            continue
        found = _find_year_group(text[start:end], year)
        if found is not None:
            sub_label, sub_start, sub_end = found
            return (start + sub_start, start + sub_end,
                    [_short_label(label), _short_label(sub_label)])
    # 2) 没有「原创」这一层时，全模板找年份格（例：Template:梦的结唱 的「優秀曲目 → 2024年」）
    found = _find_year_group(text, year)
    if found is None:
        return None
    label, start, end = found
    parent = _parent_label(text, start, end)
    path = [parent, _short_label(label)] if parent else [_short_label(label)]
    return start, end, path


def _links_item(page_name: str, ja_name: Optional[str], body: str) -> str:
    """`{{links}}` 里的条目写法：裸 `页面名`，或 `页面名{{!}}显示名`（看邻居用的是哪种）。"""
    page_name = (page_name or "").strip()
    ja_name = (ja_name or "").strip()
    if not ja_name or ja_name == page_name:
        return page_name
    if "{{!}}{{lj|" in body:
        return f"{page_name}{{{{!}}}}{{{{lj|{ja_name}}}}}"
    return f"{page_name}{{{{!}}}}{ja_name}"


def _has_links_entry(value: str, page_name: str) -> bool:
    """`{{links}}` 列表里是否已有该条目（条目是裸页面名，不能用 `[[…]]` 判重）。"""
    pattern = r"(?:^|[|>])\s*" + re.escape(page_name) + r"\s*(?:\{\{!\}\}|\||<!--|$)"
    return re.search(pattern, value) is not None


def _top_level_items(body: str) -> List[Tuple[int, int]]:
    """`{{…}}` 里顶层 `|` 分出的各项（值的位置区间）；忽略嵌套模板与 `[[…]]` 里的 `|`。

    第 0 项是模板名，后面才是各个参数 / 位置参数（`{{links|A|B}}` 里的 A、B）。
    """
    start = body.find("{{")
    if start < 0:
        return []
    depth = 0
    bracket = 0
    index = start
    body_start: Optional[int] = None
    cuts: List[int] = []
    end_of_body = len(body)
    while index < len(body):
        if body.startswith("<!--", index):
            close = body.find("-->", index)
            index = len(body) if close < 0 else close + 3
            continue
        pair = body[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            if depth == 1:
                body_start = index
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                end_of_body = index - 2
                break
            continue
        if pair == "[[":
            bracket += 1
            index += 2
            continue
        if pair == "]]":
            bracket = max(0, bracket - 1)
            index += 2
            continue
        if body[index] == "|" and depth == 1 and bracket == 0:
            cuts.append(index)
        index += 1
    if body_start is None:
        return []
    # 区间不含尾随的 `|`（与 scan_params 取参数值的方式一致）
    starts = [body_start] + [cut + 1 for cut in cuts]
    ends = cuts + [end_of_body]
    return [(starts[i], ends[i]) for i in range(len(ends))]


def _name_inside(text: str) -> str:
    """`{{lj|名字}}` → `名字`（没套模板就原样返回）。"""
    return _unwrap(text) if _single_wrapper(text) else (text or "").strip()


def _links_item_parts(item: str) -> Tuple[str, Optional[str]]:
    """`{{links}}` 的一项拆成 (前半段, 日文名)；没有 `{{!}}` 时第二截为 None。

    先把 `<!-- … -->` 注释去掉（每一项后面都接着换行注释），`{{lj|…}}` 包着的名字也要能认出来。
    """
    text = _strip_comments(item).strip()
    head, sep, tail = text.partition("{{!}}")
    if not sep:
        return text, None
    return _name_inside(head), _name_inside(tail)


def relink_links_item(body: str, page_name: str, ja_name: Optional[str]) -> Tuple[str, int]:
    """`{{links}}` 列表里同一首歌改写成 `页面名{{!}}日文原名`，返回 (新文本, 改了几处)。

    两种写法都要认（用户 2026-09-29 报的 `Template:Meddmia` 就是第 2 种）：
      1. 整项就是日文原名（条目还没建时先按原名链）→ 补上页面名；
      2. 整项是 `旧页面名{{!}}日文原名`（译名改了 / 条目被移动过）→ 把前半段换成新页面名。
    第 2 种以前认不出来，于是同一首歌被**再追加一条**（diff 251212 在 `|list6 = {{lj|{{links|…}}}}`
    里多出一行 `|做吧! 新鲜的小年轻{{!}}やっちゃえ！フレッシュヤング`），站上正确做法是改原来那一条
    （diff 251242：`做吧！新鲜的小年轻{{!}}…` → `做吧! 新鲜的小年轻{{!}}…`）。
    只换名字本身：位置、缩进、`<!-- … -->` 注释都不动。
    """
    page_name = (page_name or "").strip()
    ja_name = (ja_name or "").strip()
    if not page_name or not ja_name or ja_name == page_name:
        return body, 0
    item = _links_item(page_name, ja_name, body)
    replaced = 0
    for start, end in reversed(_top_level_items(body)[1:]):
        head, tail = _links_item_parts(body[start:end])
        if tail is None:
            # 写法 1：整项就是日文原名 → 整项换成 `页面名{{!}}日文原名`
            if head != ja_name:
                continue
            offset = body.find(ja_name, start, end)
            if offset < 0:
                continue
            body = body[:offset] + item + body[offset + len(ja_name):]
        else:
            # 写法 2：`旧页面名{{!}}日文原名` → 只换前半段
            if tail != ja_name or not head or head == page_name:
                continue
            offset = body.find(head, start, end)
            if offset < 0:
                continue
            body = body[:offset] + page_name + body[offset + len(head):]
        replaced += 1
    return body, replaced


def add_producer_entry(text: str, year: Optional[int], page_name: str,
                       ja_name: Optional[str] = None,
                       posted: Optional[PostedAt] = None) -> Tuple[str, str]:
    """把条目写进 P主模板投稿年份那一格，返回 (新文本, 说明)。"""
    if not text:
        return text, "模板内容为空"
    if year is None:
        return text, "取不到投稿年份，无法定位年份分组"
    span = locate_producer_list(text, year)
    if span is None:
        return text, f"模板里没有 {year} 年的分组"
    # 查重 / 改指旧写法 / 追加都交给 `_insert_at()`（它会认 `{{links}}` 的写法与日期位置）
    return _insert_at(text, span, entry_link(page_name, ja_name), posted=posted)


# ---------------------------------------------------------------- 写回

def _balanced(text: str) -> bool:
    """花括号是否配平（写回前的安全校验）。"""
    depth = 0
    index = 0
    while index < len(text):
        pair = text[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
            continue
        if pair == "}}":
            depth -= 1
            index += 2
            if depth < 0:
                return False
            continue
        index += 1
    return depth == 0


LINKED_WITH_TARGET_RE = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")


def restore_dropped_targets(text: str, updated: str) -> Tuple[str, List[str]]:
    """写回前的兼底：**已经带着真实链接目标的条目，不许被抹成裸链**。

    用户 2026-10-05 报的（`Template:The VOCALOID Collection2023夏` diff 257672→257677，
    修订说明就是这个同步）：`{{lj|[[Last Meteor|ラストメティオ]]}}` 变成了
    `{{lj|[[ラストメティオ]]}}` —— 目标没了、条目白链（用户随后手动改了回去）。
    同一个改子里它又把《刑くしゃ》正确改指成了 `[[为犬之人|刑くしゃ]]`，所以是「改指」
    那套逻辑在**拿错输入**时伤到了邻座：只要「目标 ≠ 显示名」的链接被削掉，就一定不是想要的。

    哪条路径漏的都不重要了（`relink_entry` 的守卫、旧进程里没生效的新代码、没见过的输入形状……）：
    这里在**写回前**逐条对一遍。原文里 `[[目标|显示]]`（目标 ≠ 显示）的链接，更新后
    少了几个、而 `[[显示]]` 又多出来几个，就把多出来那几个补成原来的 `[[目标|显示]]`
    （只补不回退其它改动）。按**出现次数**配对，不是满篇找同名裸链 —— 否则会把
    别的行上本来就用裸链写的同名条目也一起改掉。返回 (新文本, 补回来的说明)。
    """
    if not text or not updated or text == updated:
        return updated, []
    restored: List[str] = []
    seen = set()
    for match in LINKED_WITH_TARGET_RE.finditer(text):
        target, display = match.group(1).strip(), match.group(2).strip()
        if not target or not display or target == display or (target, display) in seen:
            continue
        seen.add((target, display))
        link, bare = f"[[{target}|{display}]]", f"[[{display}]]"
        missing = max(0, text.count(link) - updated.count(link))       # 被削掉的条数
        fresh = max(0, updated.count(bare) - text.count(bare))         # 新冒出来的裸链条数
        for _ in range(min(missing, fresh)):
            updated = updated.replace(bare, link, 1)
            restored.append(link)
    return updated, restored


def _guard_update(title: str, text: str, updated: str) -> Tuple[str, List[str]]:
    """写回前统一跑一遍兼容校验，返回 (新文本, 警告语)。"""
    updated, restored = restore_dropped_targets(text, updated)
    notes: List[str] = []
    if restored:
        logging.warning("同步 %s 时差点抹掉已有链接目标，已补回：%s", title, "、".join(restored))
        notes.append(f"差点抹掉 {len(restored)} 个已写好的链接目标，已自动补回"
                     f"（{'、'.join(restored)}）")
    return updated, notes


# ---------------------------------------------------------------- 对外入口

@dataclass
class CollectionSync:
    """《The VOCALOID Collection》活动：要写进哪个模板、哪个赛道、第几名。

    同一首歌可能**同时在多榜**里（实测 涅槃(HotaRu)：TOP100 第 70 名 + ROOKIE 第 42 名）
    → `places` 带着全部赛道，写回时每榜各写一处；`track` / `rank` 是主赛道（按已知赛道顺序优先），
    给「只问用户一次」的旧路子和日志用。
    """
    template: str                              # 模板名，如 The VOCALOID Collection2024冬
    track: Optional[str] = None                # TOP100 / TOP30 / ROOKIE；None 与「榜外」都算未上榜
    rank: Optional[int] = None                 # 名次；没有名次时写「未上榜歌曲」
    places: List[Tuple[str, Optional[int]]] = field(default_factory=list)

    def placements(self) -> List[Tuple[Optional[str], Optional[int]]]:
        """要写进模板的 [(赛道, 名次), …]：`places` 优先（可能多个赛道都有）。"""
        return list(self.places) if self.places else [(self.track, self.rank)]

    @property
    def ranked(self) -> bool:
        return any(rank is not None and track and track != UNRANKED_TRACK
                   for track, rank in self.placements())


def _placement_text(track: Optional[str], rank: Optional[int]) -> str:
    """同步提示里的「TOP100 第 70 名」/「未上榜」。"""
    if rank is None or not track or track == UNRANKED_TRACK:
        return "未上榜"
    return f"{track} 第 {rank} 名"


@dataclass
class FamilySync:
    """提交窗口「同步修改大家族模板」所需的全部信息。

    templates 是条目「== 注释 ==」里的歌手模板名（如 `可不/2024`）；
    honors 是各站点达到殿堂（≥10 万播放）及以上的 (站点, 播放量)；
    producers 是同区的 P主模板名（写进投稿年份那一格）；
    collections 是参加过的《The VOCALOID Collection》活动模板；
    year 是投稿年份，用于进入「部分非殿堂曲」/P主模板里的按年份分的格子。
    """
    templates: List[str] = field(default_factory=list)
    honors: List[Tuple[str, int]] = field(default_factory=list)
    collections: List[CollectionSync] = field(default_factory=list)
    producers: List[str] = field(default_factory=list)
    # 本曲 P主自己的名字（VocaDB 的写法，如 `酔シグレ`）：活动模板里同名歌并存时靠它认出
    # `歌名(我们的P主)` 那一行（见 `entry_names()` / `link_score()`，用户 2026-10-28 的《EDEN》）。
    producer_names: List[str] = field(default_factory=list)
    year: Optional[int] = None
    # 本曲在各个站点的投稿年份（从早到晚）。一首歌 2025、2026 两年都有稿件时，
    # `templates` 里会同时有 `重音Teto/2025` 与 `重音Teto/2026`，各写各的年份格。
    years: List[int] = field(default_factory=list)
    vocalists: List[str] = field(default_factory=list)   # 本曲歌姬（模板用 `{{coloredlink}}` 时据此配色）
    posted: Optional[PostedAt] = None                    # 本曲投稿时刻（列表里按时间插条目用）
    # 每年各自最早的稿件时刻（`{2026: PostedAt(...)}`）：`/2026` 里按 2026 那份稿件的日期排
    posted_by_year: Dict[int, PostedAt] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """有模板可写就允许同步：未达殿堂时改写「部分非殿堂曲」，所以不要求 honors 非空。"""
        return bool(self.templates or self.collections or self.producers)


# ---------------------------------------------------------------- 活动模板

def _collection_children(text: str) -> List[Tuple[str, int, int]]:
    """列出子导航框 (标题, 起点, 终点)；标题就是模板里的 `|title =`。"""
    children: List[Tuple[str, int, int]] = []
    for start, end in iter_blocks(text):
        values = {name: value for name, value, _, _ in scan_params(text[start:end])}
        title = _strip_comments(values.get("title", "")).strip()
        if title:
            children.append((title, start, end))
    return children


def _find_collection_child(text: str, track: Optional[str]) -> Optional[Tuple[str, int, int]]:
    """找赛道对应的子导航框；未上榜时找「其他歌曲」。"""
    for title, start, end in _collection_children(text):
        if track and track != UNRANKED_TRACK:
            if track.lower() in title.lower():
                return title, start, end
        elif any(keyword in title for keyword in UNRANKED_TITLES):
            return title, start, end
    return None


def collection_template_name(collection: str) -> Optional[str]:
    """活动名（ボカコレ2024冬 / The VOCALOID Collection 2024 Winter）→ 注释区的模板名。"""
    if not collection:
        return None
    name = collection
    if name.startswith("ボカコレ"):
        name = name[len("ボカコレ"):]
    elif name.startswith("The VOCALOID Collection"):
        name = name[len("The VOCALOID Collection"):].strip()
    return f"The VOCALOID Collection{name}"


# ---------------------------------------------------------------- 从活动模板读位置

@dataclass(frozen=True)
class CollectionPlace:
    """这首歌在某一届《The VOCALOID Collection》活动模板里的位置。"""

    track: str                        # TOP100 / TOP30 / ROOKIE / REMIX / 榜外
    rank: Optional[int] = None        # 名次（分段区间起点 + 段内第几个）；算不出是 None
    section: str = ""                 # 人话路径（如「TOP100 → 61-70位」），写日志 / 提示用

    @property
    def ranked(self) -> bool:
        return self.rank is not None and self.track in COLLECTION_TRACKS


def _norm_name(name: str) -> str:
    """比歌名用：宽度归一（`！` 与 `!` 同形）、去掉多余空白、大小写不敏感。

    用户 2026-10-12 报的《忘记正疯狂地旋转着！》：输入的日语原名用**半角** `!`
    （`狂っと廻って忘れちゃう!`），活动模板里列的是**全角** `！`
    （`{{lj|[[狂っと廻って忘れちゃう！]]}}`）—— 不归一就当成两首歌，名次认不出来
    （当成榜外），同步时还会在榜单里重复追加一条。
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(name or ""))).strip().casefold()


# ---------------------------------------------------------------- 同名条目的归属
# 同一个榜单模板里会**同时列着好几首同名的歌**，只按歌名匹配就会把别人那一行认成自己的。
# 实测 `Template:The VOCALOID Collection2022春` 有两首《EDEN》：
#   61-70位  `[[EDEN(酔シグレ)|ENDN]]`      ← 本曲（P主 酔シグレ）
#   91-100位 `[[EDEN]]`                     ← 别人那首（`EDEN(Mitsubachi Bocchi)`）
# 旧实现两处都认成自己的：条目里的名次写成 **97**（应为 66）、同步模板时还把别人那条
# 改指到 `EDEN(醉shigure)`（用户 2026-10-28 给的两笔差异）。所以链接要判归属：
OWN_STRONG = 4       # `歌名(我们的P主)` —— 最硬的证据，模板用它区分同名歌
OWN_OURS = 3         # 目标 / 显示名就是我们的条目名
OWN_LIKELY = 2       # `歌名(括号里不是我们已知的写法)`，但我们也说不出 P主名 → 先按我们的算
OWN_MAYBE = 1        # 只对上了歌名（裸链接 `[[EDEN]]`）—— 同名歌里可能指的是别人
OWN_OTHER = 0        # 别人的条目（`歌名(别人的P主)`）或压根不是这首歌


class EntryNames(NamedTuple):
    """这首歌在模板里的各种名字（都由 `entry_link()` 的结果 + P主名推出来）。"""
    page: str                        # 条目名，已归一
    bases: Tuple[str, ...]           # 歌名（条目名括号前那段 + 日文原名），已归一
    producers: Tuple[str, ...]       # 括号里可能是「我们的」那些写法，已归一


def _split_title(name: str) -> Tuple[str, str]:
    """`EDEN(醉shigure)` → ("EDEN", "醉shigure")；没有括号 → (name, "")。"""
    text = str(name or "").strip()
    match = DISAMBIG_SUFFIX_RE.search(text)
    if not match:
        return text, ""
    return text[:match.start()].strip(), text[match.start():].strip("()（） ")


def entry_names(entry: str, producers: Sequence[str] = ()) -> EntryNames:
    """`entry_link()` 的写法（+ P主名）→ 判断「这条链接是不是这首歌」用的名字表。

    括号里的写法可能有好几种：条目名里的（`EDEN(醉shigure)` → `醉shigure`，站上的中文写法）、
    日文原名里的（`EDEN(酔シグレ)` → `酔シグレ`）、以及 P主自己的名字（VocaDB 给的
    `酔シグレ`）。三者都收下，才能认出「模板里那条写的还是日文 P主名」的旧条目。
    """
    parts = _link_parts(entry)
    if parts is None:
        page, ja = str(entry or "").strip(), ""
    else:
        page, ja = parts[0].strip(), str(parts[1] or "").strip()
    base, suffix = _split_title(page)
    ja_base, ja_suffix = _split_title(ja)
    bases = tuple(dict.fromkeys(
        key for key in (_norm_name(base), _norm_name(ja_base)) if key))
    keys = (suffix, ja_suffix, *[str(name or "") for name in producers])
    return EntryNames(
        page=_norm_name(page), bases=bases,
        producers=tuple(dict.fromkeys(key for key in (_norm_name(item) for item in keys) if key)))


def link_score(names: EntryNames, target: str, display: str) -> int:
    """一条 `[[目标|显示名]]` 是不是这首歌（见 `OWN_*`）。"""
    target, display = str(target or "").strip(), str(display or "").strip()
    target_key, display_key = _norm_name(target), _norm_name(display)
    base, suffix = _split_title(target)
    if suffix and _norm_name(base) in names.bases:
        if _norm_name(suffix) in names.producers:
            return OWN_STRONG
        # 括号里不是我们已知的写法：知道自己的 P主名时，这就是另一首同名歌
        # （`EDEN(Mitsubachi Bocchi)`）；连 P主名都不知道时，模板只给了这一个候选，
        # 先按我们的算（名次据此认出来，改指那边仍然不动它）。
        return OWN_OTHER if names.producers else OWN_LIKELY
    if names.page and names.page in (target_key, display_key):
        return OWN_OURS
    if (target_key and target_key in names.bases) or (display_key and display_key in names.bases):
        return OWN_MAYBE
    return OWN_OTHER


def bare_link_offsets(text: str, names: EntryNames) -> Set[int]:
    """裸链接（`[[EDEN]]`）里**可能属于这首歌**的那些位置。

    规则：**同一赛道里**已经有一条「带括号的」链接（`歌名(我们的P主)` / 我们的条目名）时，
    那条赛道里的裸链接就是另一首同名歌的 —— 一首歌在一个榜里只有一个名次
    （实测 2022春 TOP100：第 66 名是我们的 `[[EDEN(酔シグレ)|ENDN]]`，第 97 名是
    另一首《EDEN》的 `[[EDEN]]`，只该认前者）。跨赛道时仍然允许裸链接：
    同一首歌 TOP100 与 ROOKIE 两榜都在的写法很常见（实测《涅槃(HotaRu)》），
    那边只剩裸链接是旧写法，要照旧改指。
    """
    owned_tracks: Set[str] = set()
    bare: Set[int] = set()
    children = _collection_children(text)
    for link in INLINE_LINK_RE.finditer(str(text or "")):
        target, display = (link.group(1) or "").strip(), (link.group(2) or "").strip()
        score = link_score(names, target, display)
        suffixed = bool(_split_title(target)[1] or _split_title(display)[1])
        child = _child_at(children, link.start())
        track = _track_of(child[0]) if child is not None else None
        if score >= OWN_LIKELY and suffixed:
            owned_tracks.add(track or "")       # 带括号的写法才分得清是谁的，用它占住这个赛道
        elif score >= OWN_MAYBE:
            bare.add(link.start())
    if not owned_tracks:
        return bare
    allowed: Set[int] = set()
    for offset in bare:
        child = _child_at(children, offset)
        track = _track_of(child[0]) if child is not None else None
        if (track or "") not in owned_tracks:
            allowed.add(offset)
    return allowed


def _track_of(title: str) -> Optional[str]:
    """子导航框标题 → 赛道名；未上榜返回榜外，无法识别返回 None。"""
    text = _short_label(title)
    for track in COLLECTION_TRACKS:
        if track.lower() in text.lower():
            return track
    if any(keyword in text for keyword in UNRANKED_TITLES):
        return UNRANKED_TRACK
    return None


def _child_at(children: Sequence[Tuple[str, int, int]],
              offset: int) -> Optional[Tuple[str, int, int]]:
    """包含这个位置的最内层子导航框（外层那个包住了整篇模板，所以要取最小的）。"""
    found: Optional[Tuple[str, int, int]] = None
    for title, start, end in children:
        if start <= offset < end and (found is None or end - start < found[2] - found[1]):
            found = (title, start, end)
    return found


def _group_at(text: str, start: int, end: int,
              offset: int) -> Optional[Tuple[str, int, int]]:
    """[start, end) 里包含 offset 的那个 `|groupN` / `|listN`（返回绝对位置）。"""
    found: Optional[Tuple[str, int, int]] = None
    for label, group_start, group_end in iter_groups(text[start:end]):
        if start + group_start <= offset < start + group_end:
            if found is None or group_end - group_start < found[2] - found[1]:
                found = (label, start + group_start, start + group_end)
    return found


def _rank_in(label: str, value: str, offset: int) -> Optional[int]:
    """分段标签（`61-70位`）里的名次：区间起点 + 这条在本段里第几个。

    `offset` 是歌名在 `value` 里的位置；条目之间用 `•` 隔开（实测写法）。
    算出来的值超出这段区间（模板漏列 / 多列）就不认，宁可没有名次。
    """
    match = RANGE_RE.search(label or "")
    if match is None:
        return None
    first, last = int(match.group(1)), int(match.group(2))
    position, index = 0, 1
    for part in value.split("•"):
        if position <= offset < position + len(part):
            rank = first + index - 1
            return rank if first <= rank <= last else None
        position += len(part) + 1                 # +1 = 那个「•」本身
        index += 1
    return None


def read_collection_places(text: str, page_name: str,
                           ja_name: Optional[str] = None,
                           producers: Sequence[str] = ()) -> List[CollectionPlace]:
    """从活动模板源码里读这首歌在**各赛道**的位置（按已知赛道顺序）。

    只认**链接**：链接目标或显示名对上歌名才算（条目还没建时模板里链的是日文原名，
    也要能认）。不做「歌名在不在这段文字里」的判断 —— 实测 REMIX 里别人条目的文字
    也会含到我们这首歌的名字（`イガク`）。
    只在 `COLLECTION_TRACKS`（TOP100 / TOP30 / ROOKIE / REMIX）里找；
    列在「其他歌曲 → 未上榜歌曲」里或根本没列 → 榜外。

    ⚠️ 同一届会有**好几首同名的歌**（实测 `EDEN` 两首），所以每个赛道里按归属挑链接
    （见 `link_score()`）：`歌名(我们的P主)` 最可信，其次是完整条目名，
    最差的裸链接只在同一赛道里还没有「我们的」条目时才算数（见 `bare_link_offsets()`）。
    """
    if not text:
        return []
    names = entry_names(entry_link(page_name, ja_name), producers)
    if not names.page:
        return []
    bare = bare_link_offsets(text, names)
    children = _collection_children(text)
    found: Dict[str, Tuple[Tuple[int, int], CollectionPlace]] = {}
    skipped: List[str] = []
    for link in INLINE_LINK_RE.finditer(text):
        target, alias = (link.group(1) or "").strip(), (link.group(2) or "").strip()
        score = link_score(names, target, alias)
        if score == OWN_OTHER or (score == OWN_MAYBE and link.start() not in bare):
            continue
        child = _child_at(children, link.start())
        if child is None:
            continue
        track = _track_of(child[0])
        if track is None:
            skipped.append(_short_label(child[0]))
            continue
        # 带括号的写法（`歌名(某个P主)`）比裸链接更能说明是谁的那一条：
        # 同一个赛道里两者都在时，认带括号的（实测《EDEN》第 66 名与第 97 名同属 TOP100）
        suffixed = bool(_split_title(target)[1] or _split_title(alias)[1])
        key = (1 if suffixed else 0, score)
        previous = found.get(track)
        if track == UNRANKED_TRACK or (previous is not None and previous[0] >= key):
            continue                              # 未上榜那一格不算赛道；同一赛道只留最可信的
        group = _group_at(text, child[1], child[2], link.start())
        rank, section = None, _short_label(child[0])
        if group is not None:
            label, group_start, group_end = group
            rank = _rank_in(label, text[group_start:group_end], link.start() - group_start)
            section = f"{section} → {_short_label(label)}"
        found[track] = (key, CollectionPlace(track, rank, section))
    if skipped:
        logging.info("这首歌列在「%s」里（不是 %s），按榜外处理",
                     "、".join(dict.fromkeys(skipped)), " / ".join(COLLECTION_TRACKS))
    places = [place for _score, place in found.values()]
    places.sort(key=lambda place: COLLECTION_TRACKS.index(place.track))
    return places


def find_collection_places(template: str, page_name: str,
                           ja_name: Optional[str] = None,
                           producers: Sequence[str] = ()) -> Optional[List[CollectionPlace]]:
    """去那一届的活动模板里找这首歌的赛道 / 名次。

    模板取不到（不存在 / 网络失败 —— 实测 2023秋 / 2024春 / 2024夏 / 2025春 这几届
    wiki 上就没有模板）返回 **None**，调用方据此退回人工询问；
    模板读到了但哪个赛道都没有返回**空表**（= 榜外）。
    """
    if not template:
        return None
    text = fetch_template_text(template)
    if text is None:
        return None
    return read_collection_places(text, page_name, ja_name, producers)


def _location_text(text: str, offset: int) -> str:
    """offset 在哪个「子导航框 → 段落」里（只用于提示文字）。"""
    child: Optional[Tuple[int, int, str]] = None
    for title, start, end in _collection_children(text):
        if start <= offset < end and (child is None or end - start < child[1] - child[0]):
            child = (start, end, _short_label(title))
    if child is None:
        return "模板里"
    start, end, label = child
    for group_label, group_start, group_end in iter_groups(text[start:end]):
        if start + group_start <= offset < start + group_end:
            return f"{label} → {_short_label(group_label)}"
    return label


def _locate_anywhere(text: str, entry: str,
                     producers: Sequence[str] = ()) -> Optional[Tuple[int, bool]]:
    """在整篇模板里找这首歌，返回 (位置, 是否已经链到中文条目)。

    两种写法都要认：已经链到中文条目（`[[column|コラム]]`），以及条目还没建、
    只能用日文原名链的旧写法（`[[コラム]]`）。
    另外**条目名 / 日文名写反**时（`entry` = `[[日文名|中文名]]`，模板里写的是
    `[[中文名|日文名]]`）也算「已经链着了」—— 否则会在榜单里多塞一条（2026-10-06）。

    同名歌并存时（用户 2026-10-28）先认 `歌名(我们的P主)` 与完整条目名，
    裸链接只在同一赛道里还没有「我们的」条目时才算数（见 `bare_link_offsets()`）。
    """
    parts = _link_parts(entry)
    if parts is None:
        offset = text.find(entry)
        return None if offset < 0 else (offset, True)
    names = entry_names(entry, producers)
    bare = bare_link_offsets(text, names)
    # 名字比对一律走 `_norm_name`（宽度归一）：输入半角 `!`、模板写全角 `！` 时也要认得出
    # 是同一首歌（见 `_norm_name` 里 2026-10-12 那条）。
    fallback: Optional[int] = None
    for link in INLINE_LINK_RE.finditer(text):
        score = link_score(names, link.group(1), link.group(2))
        if score >= OWN_OURS:
            return link.start(), _norm_name(link.group(1)) == names.page
        if link.start() in bare and fallback is None:
            fallback = link.start()
    return None if fallback is None else (fallback, False)


def _legacy_offsets(text: str, entry: str, producers: Sequence[str] = ()) -> List[int]:
    """所有「还在用旧写法链这首歌」的链接位置（即 `relink_entry` 会改的那些）。

    同一首歌可能同时列在 TOP100 与 ROOKIE 两处，所以是**一处一处**地定位，
    提示里才能说清到底改了哪儿（实测：两处都改了，只说第一处会让人以为漏了一处）。
    """
    parts = _link_parts(entry)
    if parts is None:
        return []
    page_name, alias = parts
    if not alias or _norm_name(alias) == _norm_name(page_name):
        return []                                  # entry 就是 `[[日文原名]]`，没什么可改的
    names = entry_names(entry, producers)
    bare = bare_link_offsets(text, names)
    offsets: List[int] = []
    for link in INLINE_LINK_RE.finditer(text):
        score = link_score(names, link.group(1), link.group(2))
        if score < OWN_OURS and link.start() not in bare:
            continue
        if _norm_name(link.group(1)) != names.page:
            offsets.append(link.start())
    return offsets


def add_collection_entry(text: str, track: Optional[str], rank: Optional[int],
                         entry: str, producers: Sequence[str] = ()) -> Tuple[str, str]:
    """把条目写进活动模板对应的榜单段落，返回 (新文本, 说明)。

    先在**整篇模板**里找这首歌：榜单里的歌会同时出现在多个赛道 / 名次段落里
    （实测：`[[コラム]]` 在 TOP100 → 81-90位，而按赛道 / 名次算出来的位置是别处），
    找到就改指 / 跳过，绝不再塞一份进去。

    `producers` 是本曲的 P主名（日文原名即可）：同名歌并存时靠它认出
    `歌名(我们的P主)` 那一行（用户 2026-10-28 给的《EDEN》差异）。
    """
    if not text:
        return text, "模板内容为空"
    located = _locate_anywhere(text, entry, producers)
    if located is not None:
        offset, already = located
        where = _location_text(text, offset)
        # 先试全篇改指：同一首歌可能在多个赛道，两处旧写法都要改指
        legacy = _legacy_offsets(text, entry, producers)
        relinked, changed = relink_entry(text, entry, producers)
        if changed:
            page_name, alias = _link_parts(entry)
            spots = list(dict.fromkeys(_location_text(text, spot) for spot in legacy))
            if len(spots) > 1:                     # 两榜都改了 → 每一处都报出来
                where = "」和「".join(spots)
            elif spots:
                where = spots[0]
            return relinked, f"已把「{where}」里的「{alias}」改指到「{page_name}」"
        if already:
            return text, f"「{where}」里已有该条目，未重复添加"
    child = _find_collection_child(text, track)
    if child is None:
        return text, (f"模板里没有「{track}」赛道的榜单段落" if track and track != UNRANKED_TRACK
                      else "模板里没有「未上榜歌曲」段落")
    title, start, end = child
    subs = iter_groups(text[start:end])
    if track and track != UNRANKED_TRACK:
        if rank is None:
            return text, f"「{title}」缺少名次，无法定位段落"
        picked = None
        for label, sub_start, sub_end in subs:
            match = RANGE_RE.search(label)
            if match and int(match.group(1)) <= rank <= int(match.group(2)):
                picked = (label, sub_start, sub_end)
                break
        if picked is None:
            return text, f"「{title}」里没有第 {rank} 名所在的段落"
    else:
        picked = None
        for label, sub_start, sub_end in subs:
            if any(keyword in label for keyword in UNRANKED_KEYWORDS):
                picked = (label, sub_start, sub_end)
                break
        if picked is None:
            return text, f"「{title}」里没有「未上榜歌曲」段落"
    label, sub_start, sub_end = picked
    value_start, value_end = start + sub_start, start + sub_end
    where = f"{_short_label(title)} → {_short_label(label)}"
    if _has_entry(text[value_start:value_end], entry, producers):
        return text, f"「{where}」里已有该条目，未重复添加"
    relinked, changed = relink_entry(text[value_start:value_end], entry, producers)
    if changed:
        # 同一首歌以前只能用日文原名链，现在条目建好了 → 改指到中文条目（不重复追加）
        return text[:value_start] + relinked + text[value_end:], \
            f"已把「{where}」里的「{_link_parts(entry)[1]}」改指到「{_link_parts(entry)[0]}」"
    new_value = append_entry(text[value_start:value_end], entry)
    if _needs_newline(text, value_start, value_end):
        new_value += "\n"
    return text[:value_start] + new_value + text[value_end:], f"已加入「{where}」"


# ---------------------------------------------------------------- 计划与写回

def _vocalist_sections(text: str, vocalists: Sequence[str]) -> List[Tuple[int, int]]:
    """模板里「每个歌姬一段」的子导航框（`|title = 鸣花尊({{lj|鳴花ミコト}})`）区间。

    实测 Template:鸣花姬·尊：主框下面按歌姬各挂一个 subgroup（鸣花姬 / 鸣花尊），
    每段自己有「殿堂曲 / 其他」与年份子列表 —— **两位歌姬都唱的歌要写进两段**，
    只出现一位时只写她那一段（用户 2026-10-14：别把另一位那一段也改了）。
    用户 2026-10-14 对照 diff 261395：站上给 鸣花尊 那一段补了 `|group6 = 2026年` 一整组，
    而工具当时只写进了前一段（鸣花姬）。
    外层主框的 `|title` 里也带着歌姬名（`鸣花姬·尊({{lj|鳴花ヒメ・ミコト}})`），所以只留最内层。
    """
    wanted = [str(name) for name in vocalists if name]
    found: List[Tuple[int, int]] = []
    for start, end in iter_blocks(text):
        title = next((value for name, value, _start, _end in scan_params(text[start:end])
                      if name == "title"), "")
        if title and any(name in title for name in wanted):
            found.append((start, end))
    return [span for span in found
            if not any(span[0] < other[0] and other[1] < span[1] for other in found)]


def _append_year_group(text: str, keywords: Sequence[str], year: int, item: str,
                       stamp: Optional[str]) -> Optional[Tuple[str, str]]:
    """「其他」那一组里**还没有** year 那一格时，照邻居的写法补一组，返回 (新文本, 说明)。

    只在按歌姬分段的模板里用（见 `_vocalist_sections`）：那种模板每段的年份格是各自长出来的。
    实测 Template:鸣花姬·尊 的「鸣花尊」一段 2026 年就是新加的一组 —— 站上补的是
    `|group6 = 2026年` / `|list6 = {{lj|{{links…}}}}`（用户 2026-10-14 对照 diff 261395 hunk 2），
    而工具当时只写进前一段、这一年在那一段根本没有格子。
    普通模板保持「没有该年份就不改」的原有口径 —— 宁可不改，也别乱造分组。

    补写法照邻居：编号接着最大的那一号、组名写 `2026年`、缩进照抄最后一个 `|groupN` 行，
    条目写法 `{{lj|{{links<!-- 日期 -->|页面{{!}}显示}}}}`。认不出「按年份分格」时返回 None。
    """
    found = find_group_span(text, keywords)
    if found is None:
        return None
    _label, start, end = found
    value = text[start:end]
    numbers: List[int] = []
    years: List[int] = []
    for name, group_label, _value_start, _value_end in scan_params(value):
        match = GROUP_RE.match(name.strip())
        if match is None:
            continue
        numbers.append(int(match.group(1)))
        entry_year = producer_year(group_label)
        if entry_year:
            years.append(entry_year)
    if not numbers or year in years:
        return None
    number = max(numbers) + 1
    last = re.search(r"\n([ \t]*)\|\s*group" + str(max(numbers)) + r"\s*=", value)
    indent = last.group(1) if last else ""
    closing = value.rfind("}}")
    if closing < 0:
        return None
    head = value[:closing]
    gap = re.search(r"[ \t\n]*$", head).group(0)
    comment = f"<!--\n{indent}{stamp} -->" if stamp else ""
    block = ("\n\n" + indent + f"|group{number} = {year}年\n" + indent
             + f"|list{number} = " + "{{lj|{{links" + comment + "|" + item + "}}}}")
    # 新组接在最后一项后面，原来的收尾空行与缩进（`gap`）留在它后面
    new_value = head[:len(head) - len(gap)] + block + gap + value[closing:]
    return text[:start] + new_value + text[end:], f"已新建「{year}年」一组并写入"


def _add_entry_reports(text: str, entry: str, honors: Sequence[Tuple[str, int]],
                       year: Optional[int], vocalists: Sequence[str],
                       posted: Optional[PostedAt],
                       title: str) -> Tuple[str, List[Tuple[str, int, str]]]:
    """把条目写进这段文本，说明统一成 (站点, 播放量, 说明)；非荣誉那一路站点与播放量都是 0。"""
    if not honors:
        updated, details = add_non_honor(text, entry, year, vocalists, posted, title)
        return updated, [("", 0, detail) for detail in details]
    updated, reports, fallback = apply_honors(text, honors, entry, year, vocalists, posted)
    lines = [(site, views, detail) for site, views, details in reports for detail in details]
    lines += [("", 0, detail) for detail in fallback]
    return updated, lines


def _section_year_width(piece: str) -> int:
    """这一段里的日期注释写不写年（0 = 不写，2 = `11-03-16`，4 = `2011-03-16`）。

    新建年份格时要照邻居的写法（Template:猫村伊吕波 整页都是 `<!-- 11-04-03 -->`）。
    """
    widths = [date.year_digits for date in
              (_comment_date(match.group(1)) for match in ITEM_COMMENT_RE.finditer(piece or ""))
              if date is not None]
    widths += [date.year_digits for date in
               (_comment_date(match.group(1)) for match in LINKS_STAMP_RE.finditer(piece or ""))
               if date is not None]
    return max(widths, default=0)


def _add_section_year_group(piece: str, year: int, entry: str,
                            posted: Optional[PostedAt]) -> Optional[Tuple[str, str]]:
    """给这一段补「其他 → <year>年」一组（按歌姬分段时才用，见 `_append_year_group`）。"""
    for keywords in (NON_HONOR_KEYWORDS, NON_HONOR_FALLBACK):
        found = find_group_span(piece, keywords)
        if found is None:
            continue
        page_name, ja_name = _link_parts(entry) or ((entry or "").strip(), None)
        item = _links_item(page_name, ja_name, _unwrap(piece[found[1]:found[2]]))
        stamp = (posted.stamp(True, _section_year_width(piece)) if posted is not None else None)
        created = _append_year_group(piece, keywords, year, item, stamp)
        if created is not None:
            return created
    return None


def _has_year_group(piece: str, year: int) -> bool:
    """这一段「其他」那一组里已经有 `<year>年` 那一格了吗（分段模板才问，见 `_append_year_group`）。"""
    for keywords in (NON_HONOR_KEYWORDS, NON_HONOR_FALLBACK):
        found = find_group_span(piece, keywords)
        if found is None:
            continue
        for name, label, _start, _end in scan_params(piece[found[1]:found[2]]):
            if GROUP_RE.match(name.strip()) and producer_year(label) == year:
                return True
    return False


def _section_label(piece: str, vocalists: Sequence[str], number: int) -> str:
    """这一段是哪个歌姬的：段标题里 `(` 前面那半截（`鸣花姬({{lj|鳴花ヒメ}})` → `鸣花姬`）。

    段标题里的歌姬名可能是日文（vocadb 给的就是日文），括号前那半截才是中文写法 ——
    提示里写给用户看，所以取中文。认不出来就说第几段。
    """
    for start, end in iter_blocks(piece):          # 最外层那个框的 `|title` 才是段名
        title = next((value for name, value, _start, _end in scan_params(piece[start:end])
                      if name == "title"), "")
        if not title or not any(name and name in title for name in vocalists):
            continue
        return title.split("(")[0].strip() or title
    return f"第 {number} 段"


def _add_entry_everywhere(text: str, entry: str, honors: Sequence[Tuple[str, int]],
                          year: Optional[int], vocalists: Sequence[str],
                          posted: Optional[PostedAt],
                          title: str) -> Tuple[str, List[Tuple[str, int, str]]]:
    """模板按歌姬分段时只写**本曲用到的那几段**（见 `_vocalist_sections`），其余照旧整篇只写一处。

    两位歌姬都出现了 → 两段各写一次（《我爱你(Mealerrand)》就是这种）；
    只出现一位 → 只写她那一段，绝不能写到另一位那一段去（用户 2026-10-14）。
    """
    sections = _vocalist_sections(text, vocalists)
    if not sections:
        return _add_entry_reports(text, entry, honors, year, vocalists, posted, title)
    updated: str = text
    lines: List[Tuple[str, int, str]] = []
    for index, (start, end) in enumerate(reversed(sections)):   # 从后往前改，前面的下标才不失效
        piece = updated[start:end]
        name = _section_label(piece, vocalists, len(sections) - index)
        created = None
        if not honors and year is not None and not _has_year_group(piece, year):
            # 这一年在这一段还没有格子 → 照邻居补一组（站上给「鸣花尊」一段补的就是这种）。
            # 交给 `_add_entry_reports` 的话它会退到最近的那一格（实测写进了 2023 年那组），
            # 所以先问清楚这一年有没有格子，没有就先补格子再写。
            created = _add_section_year_group(piece, year, entry, posted)
        if created is not None:
            new_piece, detail = created
            piece_lines = [("", 0, detail)]
        else:
            new_piece, piece_lines = _add_entry_reports(piece, entry, honors, year, vocalists,
                                                        posted, title)
        # 写进哪一段要说清楚：几段各写一次，光看说明分不出是哪一段（用户 2026-10-14 的 diff）
        lines.extend((site, views, f"{detail}（{name} 段）" if len(sections) > 1 else detail)
                     for site, views, detail in piece_lines)
        if new_piece != piece:
            updated = updated[:start] + new_piece + updated[end:]
    return updated, lines


def build_plan(template: str, honors: Sequence[Tuple[str, int]], page_name: str,
               ja_name: Optional[str] = None, year: Optional[int] = None,
               vocalists: Sequence[str] = (),
               posted: Optional[PostedAt] = None) -> List[str]:
    """给出「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    entry = entry_link(page_name, ja_name)
    _, reports = _add_entry_everywhere(text, entry, honors, year, vocalists, posted, title)
    lines: List[str] = []
    for site, views, detail in reports:
        if not honors:
            lines.append(f"{title}：未达殿堂（10 万播放），{detail}")
        elif site:
            lines.append(f"{title}：{site} {views:,} 播放 → {detail}")
        else:
            lines.append(f"{title}：{detail}")
    return lines


def build_collection_plan(collection: CollectionSync, page_name: str,
                          ja_name: Optional[str] = None,
                          producers: Sequence[str] = ()) -> List[str]:
    """给出活动模板「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(collection.template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    entry = entry_link(page_name, ja_name)
    lines: List[str] = []
    updated = text
    reported: List[str] = []
    for track, rank in collection.placements():
        updated, detail = add_collection_entry(updated, track, rank, entry, producers)
        if detail in reported:
            continue                     # 同一结论只说一次，见 _collection_line
        reported.append(detail)
        lines.append(_collection_line(title, track, rank, detail))
    return lines


def _collection_line(title: str, track: Optional[str], rank: Optional[int],
                     detail: str) -> str:
    """活动模板同步的一行说明。

    两榜都有这首歌时（实测《暮光剧场》TOP100 第 39 名 + ROOKIE 第 23 名），第二次调用只是
    在模板里再找到同一处、得到与第一次一模一样的结论 —— 把同一句话写两遍很冗余
    （用户 2026-09-29 报「重复说明了两句…只写一个即可」），所以调用方按 detail 去重。
    """
    return f"{title}：{_placement_text(track, rank)} → {detail}"


def build_producer_plan(template: str, year: Optional[int], page_name: str,
                        ja_name: Optional[str] = None,
                        posted: Optional[PostedAt] = None) -> List[str]:
    """给出 P主模板「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    _, detail = add_producer_entry(text, year, page_name, ja_name, posted)
    return [f"{title}：{detail}"]


def template_year(template: str, default: Optional[int]) -> Optional[int]:
    """这条模板该按哪一年写：模板名自带的年份优先，否则用 `default`（本曲最早投稿年）。

    `重音Teto/2026` → 2026（写进 `/2026` 里 2026 那一格）；合并子页
    `重音Teto/2021及以前` 与不分年份的 `歌爱雪` 没有自己的年份 → 用本曲年份。
    """
    match = re.search(r"/(\d{4})$", str(template or "").strip())
    return int(match.group(1)) if match else default


def template_posted(template: str, family: "FamilySync") -> Optional[PostedAt]:
    """这条模板该用哪个投稿时刻排：模板名带年份时用**那一年**的稿件，否则用最早的。"""
    year = template_year(template, None)
    if year is not None and family.posted_by_year.get(year) is not None:
        return family.posted_by_year[year]
    return family.posted


def plan(family: "FamilySync", page_name: str, ja_name: Optional[str] = None) -> List[str]:
    """所有模板的预览说明（不改动任何东西）。"""
    lines: List[str] = []
    for producer in family.producers:
        lines.extend(build_producer_plan(producer, family.year, page_name, ja_name,
                                        family.posted))
    for template in family.templates:
        lines.extend(build_plan(template, family.honors, page_name, ja_name,
                                template_year(template, family.year),
                                family.vocalists, template_posted(template, family)))
    for collection in family.collections:
        lines.extend(build_collection_plan(collection, page_name, ja_name,
                                          family.producer_names))
    return lines


def sync(family: "FamilySync", page_name: str, ja_name: Optional[str] = None,
         summary: str = "同步大家族模板") -> List[str]:
    """把所有模板都更新一遍；返回给用户看的提示（不抛异常）。"""
    lines: List[str] = []
    for producer in family.producers:
        try:
            lines.extend(sync_producer(producer, family.year, page_name, ja_name, summary,
                                       family.posted))
        except Exception as e:                                # 单个模板失败不影响其它
            logging.error("同步 P主模板 %s 失败：%s", producer, e, exc_info=e)
            lines.append(f"{_template_title(producer)}：同步失败（{e}）")
    for template in family.templates:
        try:
            lines.extend(sync_template(template, family.honors, page_name, ja_name,
                                       summary, template_year(template, family.year),
                                       family.vocalists, template_posted(template, family)))
        except Exception as e:                                # 单个模板失败不影响其它
            logging.error("同步大家族模板 %s 失败：%s", template, e, exc_info=e)
            lines.append(f"{_template_title(template)}：同步失败（{e}）")
    for collection in family.collections:
        try:
            lines.extend(sync_collection(collection, page_name, ja_name, summary,
                                        family.producer_names))
        except Exception as e:
            logging.error("同步活动模板 %s 失败：%s", collection.template, e, exc_info=e)
            lines.append(f"{_template_title(collection.template)}：同步失败（{e}）")
    return lines


def sync_template(template: str, honors: Sequence[Tuple[str, int]], page_name: str,
                  ja_name: Optional[str] = None,
                  summary: str = "同步大家族模板",
                  year: Optional[int] = None,
                  vocalists: Sequence[str] = (),
                  posted: Optional[PostedAt] = None) -> List[str]:
    """读回模板、把条目加进各荣誉小节并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    entry = entry_link(page_name, ja_name)
    with _resolving_publish_dates():          # 写回时才去现取各条目的投稿日（按日期插）
        updated, reports = _add_entry_everywhere(text, entry, honors, year, vocalists,
                                                 posted, title)
    done: List[str] = []
    for site, _views, detail in reports:
        if not honors:
            done.append(f"{title}：未达殿堂（10 万播放），{detail}")
        elif site:
            done.append(f"{title}：{site} → {detail}")
        else:
            done.append(f"{title}：{detail}")

    if updated == text:
        return done
    updated, notes = _guard_update(title, text, updated)
    done.extend(f"{title}：{note}" for note in notes)
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = _edit_cached_template(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return done


def sync_collection(collection: CollectionSync, page_name: str,
                    ja_name: Optional[str] = None,
                    summary: str = "同步大家族模板",
                    producers: Sequence[str] = ()) -> List[str]:
    """读回活动模板、把条目加进各榜榜单段落并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(collection.template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    entry = entry_link(page_name, ja_name)
    updated = text
    done: List[str] = []
    reported: List[str] = []
    for track, rank in collection.placements():
        updated, detail = add_collection_entry(updated, track, rank, entry, producers)
        if detail in reported:
            continue                     # 同一结论只说一次（见 _collection_line）
        reported.append(detail)
        done.append(_collection_line(title, track, rank, detail))
    if updated == text:
        return done
    updated, notes = _guard_update(title, text, updated)
    done.extend(f"{title}：{note}" for note in notes)
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = _edit_cached_template(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return done


def sync_producer(template: str, year: Optional[int], page_name: str,
                  ja_name: Optional[str] = None,
                  summary: str = "同步大家族模板",
                  posted: Optional[PostedAt] = None) -> List[str]:
    """读回 P主模板、把条目加进投稿年份那一格并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    updated, detail = add_producer_entry(text, year, page_name, ja_name, posted)
    if updated == text:
        return [f"{title}：{detail}"]
    updated, notes = _guard_update(title, text, updated)
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = _edit_cached_template(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return [f"{title}：{detail}"]
