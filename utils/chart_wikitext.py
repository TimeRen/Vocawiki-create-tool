"""榜单条目的 wikitext：`{{UTAU Ranking}}` 题头 + 导语 + 视频本体 + 每个名次一个 bricks。

版式照站上《周刊UTAU排行榜52》（用户 2026-11-15 指定的参考条目）：

    {{UTAU Ranking
    |id = 8763104
    |index = 52
    ...
    }}

    '''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''投稿于niconico的UTAU歌曲周刊。

    ==视频本体==
    {{BilibiliVideo|id=BV…}}
    ;视频简介
    {{lj|<poem>…</poem>}}
    {{clear}}
    ==榜单==
    {{VOCALOID_&_UTAU_Ranking/bricks
    |id = 8673630
    ...
    }}
    （每个名次一块，块与块之间空一行）

    ==杂谈==
    （待补）

    {{周刊UTAU排行榜}}
    [[Category:周刊VOCAL Character & UTAU排行榜]]

时间口径（用户 2026-11-15 定）：题头的 `|发布时间` 用 **UTC+8**，每个名次的 `|时间` 用稿件
`first_retrieve` 的 **JST 原样**（#52 / #813 两代页面都这么写，见 utils/chart_nico.py）。

题头固定用 `{{UTAU Ranking}}`（参考条目就是这一套）。2024 年之后站上新条目改用
`{{Infobox/new}}`，这里不生成 —— 生成完在提交页里可以直接改。
"""
import logging
import re
from typing import Dict, Iterable, List, Sequence, Tuple

from utils.chart_nico import ChartSpec
from utils.string import is_empty

# 题头参数（顺序与站上一致；`image` / `title-color` 留空照样写出来）
HEADER_ORDER = ("id", "index", "image", "title-color", "发布时间", "起始时间", "终止时间",
                "统计规则")
# 每个名次一块 bricks 的参数顺序：先照 #52（`color` / `bottom-column` 在最后），
# 再照 #813 补上 `再生权重` / `点赞` 这些新参数。站上这两代页面自己的顺序就不一致，
# 这里取「#52 的顺序 + 新参数插到对应位置」，保证 #52 能逐字节复现。
ROW_ORDER = (
    "前缀", "id", "曲名", "条目", "后缀", "翻唱", "歌姬", "P主", "P主条目", "P主后缀",
    "时间", "image", "image link", "本周", "上周", "走势", "rate", "得点",
    "再生", "再生权重", "评论", "评论权重", "点赞", "点赞权重",
    "收藏", "收藏权重", "四倍阀", "五倍阀", "color", "bottom-column",
)
BRICK_TEMPLATE = "VOCALOID_&_UTAU_Ranking/bricks"
CATEGORY = "[[Category:周刊VOCAL Character & UTAU排行榜]]"
PLACEHOLDER_NOTES = "（待补）"
LEAD_KIND = "UTAU歌曲周刊"
LEAD_KIND_MODERN = "UTAU周刊"
SM_PREFIXES = ("sm", "nm", "so")


def _params_text(params: Dict[str, object], order: Sequence[str]) -> str:
    """按给定顺序拼 `|键 = 值`（不认识的键按出现过的方式排在最后）。

    空值写成 `|键 =`（站上 `|image =` / `|title-color =` 就是这样，没有行尾空格）。
    """
    names = [name for name in order if name in params]
    names.extend(name for name in params if name not in order)
    lines = []
    for name in names:
        value = _value_text(params[name])
        lines.append(f"|{name} = {value}" if value else f"|{name} =")
    return "\n".join(lines)


def _value_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def header(params: Dict[str, object]) -> str:
    """`{{UTAU Ranking}}` 题头（`image` / `title-color` 没有值也照样写一行）。"""
    values = {name: params.get(name, "") for name in HEADER_ORDER}
    values.update({name: value for name, value in params.items() if name not in HEADER_ORDER})
    return "{{UTAU Ranking\n" + _params_text(values, HEADER_ORDER) + "\n}}"


def brick(params: Dict[str, object]) -> str:
    """一个名次的 `{{VOCALOID_&_UTAU_Ranking/bricks}}`（值就是传进来的那些）。"""
    return "{{" + BRICK_TEMPLATE + "\n" + _params_text(params, ROW_ORDER) + "\n}}"


_BRICK_RE = re.compile(r"\{\{" + re.escape(BRICK_TEMPLATE) + r"\s*\n(.*?)\n\}\}", re.S)
_HEADER_RE = re.compile(r"\{\{UTAU Ranking\s*\n(.*?)\n\}\}", re.S)


def _parse_params(text: str) -> Dict[str, str]:
    """`|键 = 值` 一行一条 → 字典（值里的换行不会出现，站上都是单行）。"""
    params: Dict[str, str] = {}
    for line in str(text or "").split("\n"):
        line = line.strip()
        if not line.startswith("|") or "=" not in line:
            continue
        name, _, value = line[1:].partition("=")
        params[name.strip()] = value.strip()
    return params


def parse_bricks(text: str) -> List[Dict[str, str]]:
    """页面里所有名次块 → 参数字典列表（测试与「从已有页面回填」都用它）。"""
    return [_parse_params(match.group(1)) for match in _BRICK_RE.finditer(str(text or ""))]


def parse_header(text: str) -> Dict[str, str]:
    """页面题头的参数（找不到返回空字典）。"""
    match = _HEADER_RE.search(str(text or ""))
    return _parse_params(match.group(1)) if match else {}


def plain_id(video_id: object) -> str:
    """`sm8673630` → `8673630`（站上的 `|id` 只写数字，`sm`/`nm` 写在 `|前缀`）。"""
    text = str(video_id or "").strip()
    lowered = text.lower()
    for prefix in SM_PREFIXES:
        if lowered.startswith(prefix) and lowered[len(prefix):].isdigit():
            return lowered[len(prefix):]
    return lowered


def id_prefix(video_id: object) -> str:
    """`nm1234567` → `nm`；默认的 `sm` 不写（站上只在非 sm 时写 `|前缀`）。"""
    text = str(video_id or "").strip().lower()
    for prefix in SM_PREFIXES:
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return "" if prefix == "sm" else prefix
    return ""


def lead_sentence(spec: ChartSpec, index: int, publish: str, uploader: str,
                  rule: str = "") -> str:
    """导语：「'''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''投稿于niconico的…」。"""
    title = f"{spec.page_prefix} #{index}"
    date = _lead_date(publish)
    author = f"由'''{uploader}'''投稿于niconico的" if not is_empty(uploader) else "投稿于niconico的"
    if _is_old_rule(rule):
        return f"'''{title}'''是在{date}{author}{LEAD_KIND}。"
    return f"'''{title}'''是{date}{author}{LEAD_KIND_MODERN}。"


def _lead_date(publish: str) -> str:
    """`2009年11月10日 00:06` → `2009年11月10日`（导语里不补前导零，与站上一致）。"""
    text = str(publish or "").strip()
    match = re.match(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if not match:
        return text
    return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日"


def _is_old_rule(rule: str) -> bool:
    """2008 规则（U 榜刚开播那几年）用的导语与导航框跟现在不一样。"""
    return str(rule or "").strip().startswith("2008")


def navbox(rule: str) -> str:
    """页脚导航框：2008 规则挂 `{{周刊UTAU排行榜}}`，其余按规则年份挂合并后的那个。"""
    year = re.sub(r"\D", "", str(rule or ""))[:4]
    if _is_old_rule(rule):
        return "{{周刊UTAU排行榜}}"
    return f"{{{{周刊VOCAL Character & UTAU RANKING|{year or '2023'}}}}}"


def video_section(bilibili: str, description: str) -> str:
    """`==视频本体==`：bilibili 转载 + 「;视频简介」里的日文简介。"""
    lines = ["==视频本体=="]
    if not is_empty(bilibili):
        lines.append(f"{{{{BilibiliVideo|id={str(bilibili).strip()}}}}}")
    text = str(description or "").strip()
    if text:
        lines.append(";视频简介")
        lines.append(f"{{{{lj|<poem>{text}</poem>}}}}")
    if is_empty(bilibili):
        # 没有转载时 `{{clear}}` 没有意义（它只是把 bilibili 播放器后面的浮动清掉）
        return "\n".join(lines)
    lines.append("{{clear}}")
    return "\n".join(lines)


def render_page(header_params: Dict[str, object], lead: str, description: str,
                bilibili: str, groups: Iterable[Tuple[str, Sequence[Dict[str, object]]]],
                notes: str = "", rule: str = "") -> str:
    """整页 wikitext；`groups` 是 `[(小节标题, [名次参数, …]), …]`（标题为空＝主榜单）。"""
    parts: List[str] = [header(header_params), "", lead, "", video_section(bilibili, description),
                        "==榜单=="]
    first = True
    for heading, rows in groups:
        blocks = [brick(row) for row in rows if row]
        if not blocks:
            continue
        if not first:
            parts.append("")
        if heading:
            parts.append(heading)
        first = False
        parts.append("\n\n".join(blocks))
    parts.extend(["", "==杂谈==", str(notes or "").strip() or PLACEHOLDER_NOTES, "",
                  navbox(rule), CATEGORY])
    return "\n".join(parts) + "\n"


def render(work) -> str:
    """把 `ChartWork`（见 utils/chart_editor.py）渲染成整页 wikitext。"""
    header_params = {
        "id": plain_id(work.video_id),
        "index": work.index,
        "发布时间": work.publish,
        "起始时间": work.start,
        "终止时间": work.end,
        "统计规则": work.rule,
    }
    rows = [row.to_params() for row in work.rows if not row.is_empty()]
    skipped = len(list(work.rows)) - len(rows)
    if skipped:
        logging.info("榜单：%d行没有名次也没有曲名，已跳过", skipped)
    return render_page(header_params,
                       lead_sentence(work.spec, work.index, work.publish, work.uploader,
                                     work.rule),
                       work.description, work.bilibili, [("", rows)], work.notes, work.rule)


def page_title(work) -> str:
    """条目名（`周刊UTAU排行榜813`）。"""
    return work.spec.page_title(work.index)


__all__ = ["header", "brick", "parse_bricks", "parse_header", "plain_id", "id_prefix",
           "lead_sentence", "navbox", "video_section", "render_page", "render", "page_title",
           "HEADER_ORDER", "ROW_ORDER", "CATEGORY", "BRICK_TEMPLATE"]
