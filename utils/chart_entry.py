"""曲名 → 站上「条目」名（`|条目 =` 那一格）。

用户 2026-11-16 的批注给了四个例子（`炉心融解(Hard-R.K.mix)`→`炉心融解`、
`Packaged ～カバー～`→`Packaged`、`和音マコで『ワールドイズマイン』`→`世界属于我`、
`アイマスの3人で驫麤～とりぷるばか～`→`骉麤~三个笨蛋~`），说明这一格不该永远是空的：
以前只查用户词典（`user_dict.lookup_title`），机器上还没有那个文件时整列都是空的。

做法（都不阻塞生成）：

1. 先查用户词典（用户手改过的会由 `chart_editor.remember_user_names()` 学进去）；
2. 再拿一串「候选标题」问站上一趟：曲名原样 → `『…』` 里的歌名 → 逐级去掉尾部噪声；
3. 页面存在就用页面名；是重定向就跟着重定向走（`ワールドイズマイン` → `世界属于我`）；
4. 与曲名相同就不写（站上只在两者不同时才填 `|条目`）。

一次 `action=query&redirects=1` 就能把几十个候选全部问完，所以整期只多两次请求。
"""
import logging
import re
from typing import Dict, Iterable, List, Optional

from utils import login, user_dict
from utils.wiki_api import REQUEST_TIMEOUT, api_url

PAGE_BATCH = 40
# 尾部的噪声：`～カバー～`、`（あさ）`、`(カバー)`、空格后面那一截
TAIL_NOISE_RE = re.compile(r"[～~]\s*[^～~]{1,20}?\s*[～~]|\s*[（(][^（()）]{1,20}[）)]")
QUOTED_RE = re.compile(r"[『「]([^』」]{2,40})[』」]")
SPLIT_RE = re.compile(r"[\s　]+")
TEXT_SPACE_RE = re.compile(r"\s+")


def _trim_steps(text: str) -> Iterable[str]:
    """`Packaged 再エンコ版` → `Packaged`：从整串开始，一级一级去掉尾部的词。"""
    current = TEXT_SPACE_RE.sub(" ", str(text or "")).strip()
    yield current
    while True:
        trimmed = TAIL_NOISE_RE.sub(" ", current).strip()
        if trimmed and trimmed != current:
            current = trimmed
            yield current
            continue
        pieces = SPLIT_RE.split(current)
        if len(pieces) <= 1:
            return
        current = " ".join(pieces[:-1]).strip()
        if current:
            yield current


def candidates(title: str) -> List[str]:
    """曲名 → 可能的条目名（越靠前越贴近曲名）。"""
    found: List[str] = []

    def add(name: str) -> None:
        name = str(name or "").strip()
        if name and name not in found:
            found.append(name)

    raw = str(title or "").strip()
    add(raw)
    for match in QUOTED_RE.finditer(raw):
        add(match.group(1))
    for step in _trim_steps(raw):
        add(step)
        for match in QUOTED_RE.finditer(step):
            add(match.group(1))
    return found


def resolve(titles: Iterable[str]) -> Dict[str, str]:
    """批量问站上「这些标题对应的条目名」→ `{原标题: 条目名}`（不存在的标题不出现）。

    重定向跟着走（`ワールドイズマイン` → `世界属于我`）；查询失败就返回空表，调用方跳过。
    """
    pending = [str(title).strip() for title in titles if str(title or "").strip()]
    answer: Dict[str, str] = {}
    for start in range(0, len(pending), PAGE_BATCH):
        batch = pending[start:start + PAGE_BATCH]
        if not batch:
            continue
        try:
            response = login.get_api_session().get(api_url(), params={
                "action": "query", "titles": "|".join(batch), "redirects": "1",
                "prop": "info", "format": "json", "formatversion": "2",
            }, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as error:                   # noqa: BLE001 - 这一批就当问不到
            logging.info("查站上条目失败（%s）：%s", "、".join(batch[:3]), error)
            continue
        query = payload.get("query") or {}
        resolved: Dict[str, str] = {}
        for item in query.get("redirects") or []:
            resolved[str(item.get("from"))] = str(item.get("to"))
        for item in query.get("normalized") or []:
            source, target = str(item.get("from")), str(item.get("to") or "")
            if target:
                resolved.setdefault(source, resolved.get(target, target))
        for page in query.get("pages") or []:
            title = str(page.get("title") or "")
            if not title or page.get("missing"):
                continue
            for source in batch:
                target = resolved.get(source, source)
                if target == title or source == title:
                    answer[source] = title
    return answer


def fill_entries(rows, lookup=None) -> int:
    """给每行补 `条目`（只补空着的）→ 补了几行。`lookup` 是给测试用的注入点。"""
    targets = [row for row in rows or [] if not str(getattr(row, "entry", "") or "").strip()]
    if not targets:
        return 0
    filled = 0
    wanted: List[str] = []
    for row in targets:
        title = str(getattr(row, "title", "") or "").strip()
        if not title:
            continue
        known = user_dict.lookup_title(title)
        if known:
            row.entry = known
            row.auto_entry = known
            filled += 1
            continue
        for name in candidates(title):
            if name not in wanted:
                wanted.append(name)
    if not wanted:
        return filled
    try:
        table = (lookup or resolve)(wanted) or {}
    except Exception as error:                       # noqa: BLE001 - 查不到就别挡着生成
        logging.info("查站上条目失败，跳过：%s", error)
        return filled
    for row in targets:
        if str(getattr(row, "entry", "") or "").strip():
            continue
        title = str(getattr(row, "title", "") or "").strip()
        for name in candidates(title):
            entry = table.get(name)
            if not entry or entry == title:
                continue
            row.entry = entry
            row.auto_entry = entry
            filled += 1
            break
    if filled:
        logging.info("条目：按站上页面自动填了%d行", filled)
    covers = _fill_covers(targets, table)
    if covers:
        logging.info("翻唱：%d行按「站上已有同名歌曲页」补了翻唱（原稿已削除，可改）", covers)
    return filled


def _fill_covers(rows, table: Dict[str, str]) -> int:
    """没有 n 站元数据（削除 / 非公開）时，用「站上已有同名歌曲页」判翻唱 → 补了几行。

    用户 2026-11-17 的 `You`（`【嘘音ズニ】 You 【UTAU】`）就属于这种：标题里没有任何翻唱字样、
    原稿也删了，但站上确实有《You》这一首歌 —— 拿新音源去唱别人的歌，就是翻唱。
    只对「元数据取不到」的行这么做，正常稿件仍以标题 / tag / 简介为准，避免误判。
    """
    filled = 0
    for row in rows or []:
        if not getattr(row, "no_info", False):
            continue
        if str(getattr(row, "cover", "") or "").strip():
            continue
        title = str(getattr(row, "title", "") or "").strip()
        if not title:
            continue
        if not any(table.get(name) for name in candidates(title)):
            continue
        row.cover = "1"
        note = str(getattr(row, "note", "") or "").strip()
        extra = "按站上已有同名歌曲页判为翻唱，可改"
        row.note = f"{note}；{extra}" if note else extra
        filled += 1
    return filled


__all__ = ["candidates", "resolve", "fill_entries", "PAGE_BATCH"]