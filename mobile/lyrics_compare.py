"""中日歌词逐行比对（APK「歌词」页的「比对中日歌词」用）。

两栏在生成流程里本来就该一行对一行（日语栏是排版的基准，中文栏由
`utils.lyrics_editor.align_chs_to_jap()` 对齐过去）。但实际拿到的常常对不上：
译文少一句、译者把两句并成一句、或者哪一行里混进了日文原文 —— 手机上又没法两栏并排看。
所以这里把两栏拆成「一行一组」的配对数据，交给界面逐行显示 / 修改。

纯字符串逻辑，**不依赖 Kivy**，单测可以直接跑。
"""
from __future__ import annotations

from typing import Dict, List, Optional


def _lines(text: str) -> List[str]:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _strip_blank_edges(lines: List[str]) -> List[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def pair_lines(jap: str, chs: str) -> List[Dict[str, object]]:
    """两栏 → 逐行配对的数据。

    返回的每一项是下面两种之一：

    * `{'kind': 'line', 'no': 第几行（从 1 起，空行不算）, 'jap': ..., 'chs': ...}`；
    * `{'kind': 'gap'}` —— 段落之间的空行（按**日语栏**的位置插，它是排版基准）。

    配对规则：两栏都先去掉首尾空行，然后**非空行按顺序一一对应**；段内某一边多出来的行
    也照实排出来（另一边是空串），界面据此标出「这一行译文缺了 / 多了」。
    这样即便译文少一句，后面的歌词也不会整段错位（按绝对行号配就会全串行）。
    """
    jap_lines = _strip_blank_edges(_lines(jap))
    chs_lines = _strip_blank_edges(_lines(chs))
    rows: List[Dict[str, object]] = []
    jap_index = chs_index = 0
    number = 0
    # 以日语栏的空行为段落边界：走到空行就插一条 gap，然后跳过两栏各自连续的空行
    while jap_index < len(jap_lines) or chs_index < len(chs_lines):
        while jap_index < len(jap_lines) and not jap_lines[jap_index].strip():
            if rows and rows[-1]["kind"] == "line":
                rows.append({"kind": "gap"})
            jap_index += 1
        while chs_index < len(chs_lines) and not chs_lines[chs_index].strip():
            chs_index += 1
        if jap_index >= len(jap_lines) and chs_index >= len(chs_lines):
            break
        number += 1
        rows.append({
            "kind": "line",
            "no": number,
            "jap": jap_lines[jap_index].strip() if jap_index < len(jap_lines) else "",
            "chs": chs_lines[chs_index].strip() if chs_index < len(chs_lines) else "",
        })
        jap_index += 1
        chs_index += 1
    return rows


def join_lines(rows: List[Dict[str, object]]) -> tuple[str, str]:
    """配对数据 → `(日语栏文本, 中文栏文本)`（空行按 gap 还原，末尾不留空行）。"""
    jap: List[str] = []
    chs: List[str] = []
    for row in rows or []:
        if str(row.get("kind") or "line") == "gap":
            jap.append("")
            chs.append("")
            continue
        jap.append(str(row.get("jap") or "").strip())
        chs.append(str(row.get("chs") or "").strip())
    while jap and not jap[-1]:
        jap.pop()
    while chs and not chs[-1]:
        chs.pop()
    return "\n".join(jap), "\n".join(chs)


def _has_kana(text: str) -> bool:
    from utils.japanese import is_kana_letter
    return any(is_kana_letter(char) for char in text or "")


def row_problems(rows: List[Dict[str, object]]) -> Dict[str, int]:
    """统计几类需要人看一眼的行：中文缺了、中文里还留着日文、日语多出来。

    返回 `{'missing_chs': n, 'missing_jap': n, 'japanese_in_chs': n}`，
    界面上据此提示「有 3 行译文是空的」这类话。
    """
    counts = {"missing_chs": 0, "missing_jap": 0, "japanese_in_chs": 0}
    for row in rows or []:
        if str(row.get("kind") or "line") != "line":
            continue
        jap = str(row.get("jap") or "").strip()
        chs = str(row.get("chs") or "").strip()
        if jap and not chs:
            counts["missing_chs"] += 1
        if chs and not jap:
            counts["missing_jap"] += 1
        if chs and _has_kana(chs):
            counts["japanese_in_chs"] += 1
    return counts


def summary(rows: List[Dict[str, object]]) -> str:
    """界面顶部那句话：行数与几类问题各几条。"""
    total = len([row for row in rows or [] if str(row.get("kind") or "line") == "line"])
    gaps = len(rows or []) - total
    counts = row_problems(rows)
    parts = [f"{total} 行" + (f"、{gaps} 个分段" if gaps else "")]
    if counts["missing_chs"]:
        parts.append(f"{counts['missing_chs']} 行没有译文")
    if counts["missing_jap"]:
        parts.append(f"{counts['missing_jap']} 行没有日文")
    if counts["japanese_in_chs"]:
        parts.append(f"{counts['japanese_in_chs']} 行中文里还带着假名")
    return "；".join(parts)


def first_problem(row: Dict[str, object]) -> Optional[str]:
    """单行的问题（`None` 表示没问题），界面用来给输入框描红。"""
    if str(row.get("kind") or "line") != "line":
        return None
    jap = str(row.get("jap") or "").strip()
    chs = str(row.get("chs") or "").strip()
    if jap and not chs:
        return "这一行还没有译文"
    if chs and not jap:
        return "这一行没有对应的日文（多出来的？）"
    if chs and _has_kana(chs):
        return "中文里还带着假名，可能没译完"
    return None
