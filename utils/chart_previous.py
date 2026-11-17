# -*- coding: utf-8 -*-
"""上一期条目 → 这一期的「上周」名次。

用户 2026-11-17 报「番外第 1、2、4 名明明 52 期都登场过，为什么还写成 new」：
周刊视频里卡片停留短、`上期` 那一格常常没被读到，光靠画面判断就会把老曲当新曲。
上一期条目（`周刊UTAU排行榜52`）里每行都有 `|id` 与 `|本周`，用它反查最稳 ——
实测 #52 的 `そう言えば、忘れてた = 22` 正是 #53 主榜第 5 名画面上的「上期 22」，
而 #52 的 `GO MY WAY!! 改 = 6`、`カルシウムガール = 1`、`オービタル = 5`
也正好对应用户确认的番外上周名次。

这一层只读一次页面，失败就返回空表（绝不因为查不到上一期而中断生成）。
"""
import logging
import re
from typing import Callable, Dict, Optional

from utils import wiki_api

# 一行 = 一个 `{{VOCALOID_&_UTAU_Ranking/bricks … }}`
BRICK_RE = re.compile(r"\{\{\s*VOCALOID_&_UTAU_Ranking/bricks(.*?)\n\s*\}\}", re.S)
ID_RE = re.compile(r"\|\s*id\s*=\s*([^\n|}]+)")
RANK_RE = re.compile(r"\|\s*本周\s*=\s*([^\n|}]+)")
SECTION_RE = re.compile(r"\|\s*小节\s*=\s*([^\n|}]+)")

_CACHE: Dict[str, Dict[str, Dict[str, str]]] = {}


def issue_title(spec, index: int) -> str:
    """上一期的页面名（`周刊UTAU排行榜52`）；期数 ≤ 1 或算不出来返回空串。"""
    try:
        number = int(index) - 1
        if number < 1:
            return ""
        return str(spec.page_title(number) or "")
    except Exception:                            # noqa: BLE001 - 期数不是数字就当没有
        return ""


def video_key(value: object) -> str:
    """稿件编号 → 纯数字（站上写的是 `|id = 8736774`，没有 sm/nm 前缀）。"""
    return re.sub(r"[^\d]", "", str(value or ""))


def parse_ranks(text: str) -> Dict[str, Dict[str, str]]:
    """条目 wikitext → `{纯数字编号: {"": 主榜名次, "番外": 番外名次}}`。"""
    table: Dict[str, Dict[str, str]] = {}
    for match in BRICK_RE.finditer(str(text or "")):
        block = match.group(1)
        ident = ID_RE.search(block)
        rank = RANK_RE.search(block)
        if not ident or not rank:
            continue
        key = video_key(ident.group(1))
        value = re.sub(r"[^\d]", "", rank.group(1))
        if not key or not value:
            continue
        section = SECTION_RE.search(block)
        name = str(section.group(1)).strip() if section else ""
        table.setdefault(key, {})[name] = value
    return table


def previous_ranks(spec, index: int, fetch: Optional[Callable[[str], str]] = None
                   ) -> Dict[str, Dict[str, str]]:
    """读上一期条目 → 编号到名次的表（同一次运行只读一遍；读不到返回 `{}`）。"""
    title = issue_title(spec, index)
    if not title:
        return {}
    if title in _CACHE:
        return _CACHE[title]
    reader = fetch or _fetch_text
    try:
        text = reader(title) or ""
    except Exception as error:                   # noqa: BLE001 - 上一期没写好也不该中断
        logging.info("读上一期「%s」失败：%s", title, error)
        text = ""
    table = parse_ranks(text) if text else {}
    if table:
        logging.info("榜单：从「%s」读到%d首曲子的上期名次", title, len(table))
    else:
        logging.info("榜单：没读到上一期「%s」的名次表", title)
    _CACHE[title] = table
    return table


def _fetch_text(title: str) -> str:
    return str(wiki_api.fetch_pages_text([title]).get(title) or "")


def forget(title: str = "") -> None:
    """清掉缓存（测试用）。"""
    if title:
        _CACHE.pop(title, None)
    else:
        _CACHE.clear()


def apply_last_rank(rows, table: Dict[str, Dict[str, str]]) -> int:
    """给还空着 `last` 的行补上上一期的名次 → 补了几行（同小节优先、跨小节兜底）。"""
    if not table:
        return 0
    filled = 0
    for row in rows or []:
        if str(getattr(row, "last", "") or "").strip():
            continue
        key = video_key(getattr(row, "video_id", ""))
        ranks = table.get(key)
        if not ranks:
            continue
        section = str(getattr(row, "section", "") or "").strip()
        value = ranks.get(section) or ranks.get("")
        if not value:
            value = next((item for item in ranks.values() if item), "")
        if not value:
            continue
        row.last = value
        filled += 1
    return filled


__all__ = ["issue_title", "video_key", "parse_ranks", "previous_ranks", "apply_last_rank",
           "forget"]
