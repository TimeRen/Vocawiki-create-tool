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
    「部分非殿堂曲」。组内按站点（niconico / bilibili / YouTube）或按年份分格时自动下钻。

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
    条目用的是哪一种，再照同样的风格写。

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
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

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

# 站点 -> 子分组标签里的关键词（小写比较）
SITE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "niconico": ("niconico", "nico"),
    "bilibili": ("bilibili",),
    "YouTube": ("youtube",),
}

# The VOCALOID Collection 模板里「没进榜」的段落
UNRANKED_KEYWORDS: Tuple[str, ...] = ("未上榜",)
UNRANKED_TITLES: Tuple[str, ...] = ("其他", "其它")

# P主模板：按投稿年份分格，年份就在「原创 / 投稿」这类分组里
# （实测标签：原创投稿曲目 / 原创/参与曲目 / 原创&合作歌声合成曲目 / nico上 原创投稿作品 / 投稿作品）
ORIGINAL_KEYWORDS: Tuple[str, ...] = ("原创", "投稿")
# 年份分组标签：`2013年`（多数）或 `2020`（如 Template:Kanaria）
PRODUCER_YEAR_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*年?")

GROUP_RE = re.compile(r"^group(\d+)$")
NAME_VALUE_RE = re.compile(r"^\s*([^=\n]+?)\s*=\s*(.*)$", re.S)
# 分组标签里的年份（如 `2015年`）与名次区间（如 `11-20位`）
YEAR_RE = re.compile(r"((?:19|20)\d{2})\s*年")
RANGE_RE = re.compile(r"(\d+)\s*[-–—~～]\s*(\d+)")
# 条目链接：[[页面名]] 或 [[页面名|显示名]]
LINK_RE = re.compile(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$")


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


def _short_label(label: str) -> str:
    """把分组标签里的模板调用压成可读文字（`{{color|#f2dfe6|传说曲}}` → `传说曲`）。"""
    text = _strip_comments(label or "")
    text = re.sub(r"<br[^>]*/?>", " ", text)
    while True:
        match = re.search(r"\{\{([^{}]*)\}\}", text)
        if match is None:
            break
        args = [part.strip() for part in match.group(1).split("|")[1:] if part.strip()]
        # 颜色之类的样式参数不当作标签
        keep = [part for part in args if not re.match(r"^#[0-9A-Fa-f]{3,8}$", part)]
        text = text[:match.start()] + (keep[0] if keep else "") + text[match.end():]
    return re.sub(r"\s+", " ", text).strip()


def _pick_sub(subs: Sequence[Tuple[str, int, int]], site: Optional[str],
              year: Optional[int]) -> Optional[Tuple[str, int, int]]:
    """在子分组里挑一格：先按站点，再按年份；挑不出返回 None。"""
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
    return None


def locate_list(text: str, keywords: Sequence[str], site: Optional[str] = None,
                year: Optional[int] = None, exclude: Sequence[str] = (),
                name: Optional[str] = None) -> Optional[Tuple[int, int, List[str]]]:
    """定位要写入的 `|listN =` 值区间，返回 (值起点, 值终点, 标签路径)。

    组内若还按站点 / 年份分格会自动下钻（最多两层）；分不出该写哪一格时返回 None。
    """
    found = find_group_span(text, keywords, exclude)
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


def _item_style(body: str) -> str:
    """看列表里已有的条目用的是哪种写法：lj_out（{{lj|[[…]]}}）/ lj_in（[[…|{{lj|…}}]]）/ plain。"""
    if "{{lj|[[" in body:
        return "lj_out"
    if "|{{lj|" in body:
        return "lj_in"
    return "plain"


def _style_entry(entry: str, style: str) -> str:
    """把条目改写成目标列表惯用的写法（没有日语原名时保持原样）。"""
    parts = _link_parts(entry)
    if parts is None or not parts[1]:
        return entry
    if style == "lj_out":
        return "{{lj|" + entry + "}}"
    if style == "lj_in":
        return f"[[{parts[0]}|{{{{lj|{parts[1]}}}}}]]"
    return entry


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


def append_entry(list_value: str, entry: str, links_entry: Optional[str] = None) -> str:
    """把条目追加到列表末尾，尽量沿用列表里已有的写法。

    - 整段 `{{lj|…}}` 包住 → 递归进去，在里面用原本的分隔符接上；
    - `{{links|…}}` → 用 `页面名{{!}}显示名` 的写法接一项（即 `links_entry`）；
    - `{{hlist|…}}` → 用 `|` 接上一项；
    - 平铺列表 → 沿用原有的 `{{W}}` / ` • ` 分隔符，并按需套上 `{{lj|…}}`。
    """
    body = (list_value or "").strip()
    if not body:
        return "{{lj|" + entry + "}}"
    wrapper = _single_wrapper(body)
    if wrapper and wrapper.lower() == "links":
        return _append_piped(body, links_entry or entry)
    if wrapper == "hlist":
        return _append_piped(body, _style_entry(entry, _item_style(body)))
    if wrapper == "lj":
        inner = body[len("{{lj|"):-2].strip()
        if not inner:
            return "{{lj|" + (links_entry or entry) + "}}"
        if _single_wrapper(inner):           # 里面还套着 links / hlist（如 40mP、buzzG）
            return "{{lj|" + append_entry(inner, entry, links_entry) + "}}"
        separator = " • " if "•" in inner else "{{W}}"
        return "{{lj|" + inner + separator + entry + "}}"
    separator = " • " if "•" in body else "{{W}}"
    return body + separator + _style_entry(entry, _item_style(body))


def _needs_newline(text: str, value_start: int, value_end: int) -> bool:
    """原值为空、且 `=` 后本来换行时，插入后补一个换行，避免把下一个参数挤到同一行。"""
    if text[value_start:value_end].strip():
        return False
    equal = text.rfind("=", max(0, value_start - 200), value_start)
    return equal >= 0 and "\n" in text[equal + 1:value_start]


def _has_entry(value: str, entry: str) -> bool:
    """目标列表里是否已有该条目（按页面名判断，兼容 `{{lj|[[…]]}}` 等几种写法）。"""
    parts = _link_parts(entry)
    if parts is None:
        return entry in value
    return re.search(r"\[\[" + re.escape(parts[0]) + r"(?:\||\]\])", value) is not None


def add_entry(text: str, site: Optional[str], keywords: Sequence[str], entry: str,
              year: Optional[int] = None, exclude: Sequence[str] = (),
              name: Optional[str] = None) -> Tuple[str, str]:
    """把 `entry` 加入「标签含 keywords 的组 → site 子列表」。

    返回 (新文本, 说明)；无法插入时新文本与原文相同，说明写明原因。
    """
    if not text:
        return text, "模板内容为空"
    if not keywords:
        return text, "未达殿堂（10 万播放），无需加入荣誉小节"
    label = name or str(keywords[0])
    span = locate_list(text, keywords, site=site, year=year, exclude=exclude, name=label)
    if span is None:
        if find_group_span(text, keywords, exclude) is None:
            return text, f"模板里没有「{label}」分组"
        return text, _no_sublist(label, site, year)
    value_start, value_end, path = span
    where = " → ".join(path)
    if _has_entry(text[value_start:value_end], entry):
        return text, f"「{where}」里已有该条目，未重复添加"
    new_value = append_entry(text[value_start:value_end], entry)
    if _needs_newline(text, value_start, value_end):
        new_value += "\n"
    return text[:value_start] + new_value + text[value_end:], f"已加入「{where}」"


def add_non_honor(text: str, entry: str, year: Optional[int] = None) -> Tuple[str, List[str]]:
    """未达殿堂（10 万播放）的歌曲写进「部分非殿堂曲」一组，返回 (新文本, 说明)。"""
    text, detail = add_entry(text, None, NON_HONOR_KEYWORDS, entry, year=year,
                             name=NON_HONOR_NAME)
    return text, [detail]


def add_honors(text: str, site: str, views: int, entry: str,
               year: Optional[int] = None) -> Tuple[str, List[str]]:
    """把条目加入该站点已达成的各档荣誉小组；未达殿堂时改写「部分非殿堂曲」。"""
    levels = honor_keywords(views)
    if not levels:
        return add_non_honor(text, entry, year)
    details: List[str] = []
    for keywords in levels:
        # 「非殿堂曲」一组也含「殿堂」二字，查荣誉小组时要排掉
        text, detail = add_entry(text, site, keywords, entry, year=year,
                                 exclude=NON_HONOR_KEYWORDS)
        details.append(detail)
    return text, details


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
    # 2) 没有「原创」这一层时，全模板找年份格
    found = _find_year_group(text, year)
    if found is None:
        return None
    label, start, end = found
    return start, end, [_short_label(label)]


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


def add_producer_entry(text: str, year: Optional[int], page_name: str,
                       ja_name: Optional[str] = None) -> Tuple[str, str]:
    """把条目写进 P主模板投稿年份那一格，返回 (新文本, 说明)。"""
    if not text:
        return text, "模板内容为空"
    if year is None:
        return text, "取不到投稿年份，无法定位年份分组"
    span = locate_producer_list(text, year)
    if span is None:
        return text, f"模板里没有 {year} 年的分组"
    value_start, value_end, path = span
    where = " → ".join(path)
    value = text[value_start:value_end]
    entry = entry_link(page_name, ja_name)
    links_body = _unwrap(value)
    links_wrapper = _single_wrapper(links_body)
    if links_wrapper and links_wrapper.lower() == "links":
        if _has_links_entry(links_body, (page_name or "").strip()):
            return text, f"「{where}」里已有该条目，未重复添加"
    elif _has_entry(value, entry):
        return text, f"「{where}」里已有该条目，未重复添加"
    new_value = append_entry(value, entry, _links_item(page_name, ja_name, links_body))
    if _needs_newline(text, value_start, value_end):
        new_value += "\n"
    return text[:value_start] + new_value + text[value_end:], f"已加入「{where}」"


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


# ---------------------------------------------------------------- 对外入口

@dataclass
class CollectionSync:
    """《The VOCALOID Collection》活动：要写进哪个模板、哪个赛道、第几名。"""
    template: str                              # 模板名，如 The VOCALOID Collection2024冬
    track: Optional[str] = None                # TOP100 / ROOKIE；None 与「榜外」都算未上榜
    rank: Optional[int] = None                 # 名次；没有名次时写「未上榜歌曲」

    @property
    def ranked(self) -> bool:
        return self.rank is not None and bool(self.track) and self.track != "榜外"


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
    year: Optional[int] = None

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
    """找赛道对应的子导航框（TOP100 / ROOKIE…）；未上榜时找「其他歌曲」。"""
    for title, start, end in _collection_children(text):
        if track and track != "榜外":
            if track.lower() in title.lower():
                return title, start, end
        elif any(keyword in title for keyword in UNRANKED_TITLES):
            return title, start, end
    return None


def add_collection_entry(text: str, track: Optional[str], rank: Optional[int],
                         entry: str) -> Tuple[str, str]:
    """把条目写进活动模板对应的榜单段落，返回 (新文本, 说明)。"""
    if not text:
        return text, "模板内容为空"
    child = _find_collection_child(text, track)
    if child is None:
        return text, (f"模板里没有「{track}」赛道的榜单段落" if track and track != "榜外"
                      else "模板里没有「未上榜歌曲」段落")
    title, start, end = child
    subs = iter_groups(text[start:end])
    if track and track != "榜外":
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
    if _has_entry(text[value_start:value_end], entry):
        return text, f"「{where}」里已有该条目，未重复添加"
    new_value = append_entry(text[value_start:value_end], entry)
    if _needs_newline(text, value_start, value_end):
        new_value += "\n"
    return text[:value_start] + new_value + text[value_end:], f"已加入「{where}」"


# ---------------------------------------------------------------- 计划与写回

def build_plan(template: str, honors: Sequence[Tuple[str, int]], page_name: str,
               ja_name: Optional[str] = None, year: Optional[int] = None) -> List[str]:
    """给出「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    entry = entry_link(page_name, ja_name)
    if not honors:
        _, details = add_non_honor(text, entry, year)
        return [f"{title}：未达殿堂（10 万播放），{detail}" for detail in details]
    lines: List[str] = []
    for site, views in honors:
        _, details = add_honors(text, site, views, entry, year)
        lines.extend(f"{title}：{site} {views:,} 播放 → {detail}" for detail in details)
    return lines


def build_collection_plan(collection: CollectionSync, page_name: str,
                          ja_name: Optional[str] = None) -> List[str]:
    """给出活动模板「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(collection.template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    _, detail = add_collection_entry(text, collection.track, collection.rank,
                                     entry_link(page_name, ja_name))
    where = f"{collection.track} 第 {collection.rank} 名" if collection.ranked else "未上榜"
    return [f"{title}：{where} → {detail}"]


def build_producer_plan(template: str, year: Optional[int], page_name: str,
                        ja_name: Optional[str] = None) -> List[str]:
    """给出 P主模板「准备怎么改」的文字说明（不改动任何东西）。"""
    title = _template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，将跳过"]
    _, detail = add_producer_entry(text, year, page_name, ja_name)
    return [f"{title}：{detail}"]


def plan(family: "FamilySync", page_name: str, ja_name: Optional[str] = None) -> List[str]:
    """所有模板的预览说明（不改动任何东西）。"""
    lines: List[str] = []
    for producer in family.producers:
        lines.extend(build_producer_plan(producer, family.year, page_name, ja_name))
    for template in family.templates:
        lines.extend(build_plan(template, family.honors, page_name, ja_name, family.year))
    for collection in family.collections:
        lines.extend(build_collection_plan(collection, page_name, ja_name))
    return lines


def sync(family: "FamilySync", page_name: str, ja_name: Optional[str] = None,
         summary: str = "同步大家族模板") -> List[str]:
    """把所有模板都更新一遍；返回给用户看的提示（不抛异常）。"""
    lines: List[str] = []
    for producer in family.producers:
        try:
            lines.extend(sync_producer(producer, family.year, page_name, ja_name, summary))
        except Exception as e:                                # 单个模板失败不影响其它
            logging.error("同步 P主模板 %s 失败：%s", producer, e, exc_info=e)
            lines.append(f"{_template_title(producer)}：同步失败（{e}）")
    for template in family.templates:
        try:
            lines.extend(sync_template(template, family.honors, page_name, ja_name,
                                       summary, family.year))
        except Exception as e:                                # 单个模板失败不影响其它
            logging.error("同步大家族模板 %s 失败：%s", template, e, exc_info=e)
            lines.append(f"{_template_title(template)}：同步失败（{e}）")
    for collection in family.collections:
        try:
            lines.extend(sync_collection(collection, page_name, ja_name, summary))
        except Exception as e:
            logging.error("同步活动模板 %s 失败：%s", collection.template, e, exc_info=e)
            lines.append(f"{_template_title(collection.template)}：同步失败（{e}）")
    return lines


def sync_template(template: str, honors: Sequence[Tuple[str, int]], page_name: str,
                  ja_name: Optional[str] = None,
                  summary: str = "同步大家族模板",
                  year: Optional[int] = None) -> List[str]:
    """读回模板、把条目加进各荣誉小节并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    entry = entry_link(page_name, ja_name)
    updated = text
    done: List[str] = []
    if not honors:
        updated, details = add_non_honor(updated, entry, year)
        done.extend(f"{title}：未达殿堂（10 万播放），{detail}" for detail in details)
    else:
        for site, views in honors:
            updated, details = add_honors(updated, site, views, entry, year)
            done.extend(f"{title}：{site} → {detail}" for detail in details)

    if updated == text:
        return done
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = wiki_api.edit_page(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return done


def sync_collection(collection: CollectionSync, page_name: str,
                    ja_name: Optional[str] = None,
                    summary: str = "同步大家族模板") -> List[str]:
    """读回活动模板、把条目加进榜单段落并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(collection.template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    updated, detail = add_collection_entry(text, collection.track, collection.rank,
                                           entry_link(page_name, ja_name))
    if updated == text:
        return [f"{title}：{detail}"]
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = wiki_api.edit_page(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return [f"{title}：{detail}"]


def sync_producer(template: str, year: Optional[int], page_name: str,
                  ja_name: Optional[str] = None,
                  summary: str = "同步大家族模板") -> List[str]:
    """读回 P主模板、把条目加进投稿年份那一格并写回；返回给用户看的提示（不抛异常）。"""
    title = resolve_template_title(template)
    text = fetch_template_text(title)
    if text is None:
        return [f"{title}：模板不存在或读取失败，已跳过"]

    updated, detail = add_producer_entry(text, year, page_name, ja_name)
    if updated == text:
        return [f"{title}：{detail}"]
    if not _balanced(updated):
        logging.error("同步 %s 时花括号不配平，已放弃写回", title)
        return [f"{title}：改动后模板不完整，已放弃（请手动处理）"]
    result = wiki_api.edit_page(title, updated, summary)
    if not result.get("ok"):
        return [f"{title}：写回失败（{result.get('error')}）"]
    return [f"{title}：{detail}"]
