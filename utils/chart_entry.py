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

from utils import login, user_dict, wiki_api
from utils.wiki_api import REQUEST_TIMEOUT, api_url

PAGE_BATCH = 40
# 尾部的噪声：`～カバー～`、`（あさ）`、`(カバー)`、空格后面那一截
TAIL_NOISE_RE = re.compile(r"[～~]\s*[^～~]{1,20}?\s*[～~]|\s*[（(][^（()）]{1,20}[）)]")
QUOTED_RE = re.compile(r"[『「]([^』」]{2,40})[』」]")
SPLIT_RE = re.compile(r"[\s　]+")
TEXT_SPACE_RE = re.compile(r"\s+")
# 标题开关的「分类标签」：`[UTAU]…` / `【UTAU】…` / `[ボカロ]…`（用户 2026-11-19 的 #61）
TAG_PREFIX_RE = re.compile(r"^\s*[\[【(（]\s*(?:UTAU|utau|ボカロ|VOCALOID|vocaloid|ニコニコ)\s*[\]】)）]\s*")
# 「歌姬／歌姬・曲名」「A - 曲名」「（歌姬）ー曲名」这类分隔（只取尾段当候选）。
# 注意别拿单个 `ー` 当分隔符：它是片假名的长音符（`ワールドイズマイン`、`カバー`）。
TAIL_SPLIT_RES = (re.compile(r"\s+[-－–—]\s+"), re.compile(r"[・／]"),
                  re.compile(r"[）)]\s*[ー－]\s*"))
# `Xを歌わせてみた` / `Xを歌ってみた` / `Xを歌ってもらった`
SING_IT_RE = re.compile(r"^(.*?)\s*を\s*歌(?:わせて|って|ってもら)")


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
    """曲名 → 可能的条目名（越靠前越贴近曲名）。

    先给「精确」候选（原串 / 引号里的 / 逐词去尾），后面再补几类宽松候选（用户 2026-11-19
    的 #61：`右肩の蝶を歌わせてみた`、`[UTAU]You -黄色い犬、黒犬`、
    `UTAU新音源『有家音アラタ／ネオ』＋αで耳のあるロボットの唄` 都查不到页）。
    宽松候选排后面，免得抢走精确候选已经能对上的页。
    """
    found: List[str] = []

    def add(name: str) -> None:
        name = str(name or "").strip(" 　-ー－/／・,、:：")
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
    exact = list(found)
    for name in exact:
        for loose in _loose_candidates(name):
            add(loose)
    return found


def _loose_candidates(title: str) -> List[str]:
    """宽松候选：剥掉 `[UTAU]` 这类前缀、取 ` - ` / `ー` / `・` / `／` / 最后一个 `で` 的尾段、
    `Xを歌わせてみた` → `X`。只在与原标题明显不同、且尾段 ≥2 字时才给。"""
    found: List[str] = []
    text = str(title or "").strip()

    def add(name: str) -> None:
        name = str(name or "").strip(" 　-ー－/／・,、:：～~「」『』【】（）()")
        if len(name) >= 2 and name != text and name not in found:
            found.append(name)

    for match in TAG_PREFIX_RE.finditer(text):
        add(text[match.end():])
    for pattern in TAIL_SPLIT_RES:
        for piece in pattern.split(text)[1:]:
            add(piece)
    if "で" in text:
        add(text.rsplit("で", 1)[1])
    match = SING_IT_RE.search(text)
    if match:
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
                "prop": "pageprops", "ppprop": "disambig",
                "format": "json", "formatversion": "2",
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
            if not title or page.get("missing") or page.get("invalid"):
                continue
            if _is_disambig(page):
                continue                             # 消歧义页（`1`）不能当条目名
            for source in batch:
                target = resolved.get(source, source)
                if target == title or source == title:
                    answer[source] = title
    return answer


def _is_disambig(page: dict) -> bool:
    """`prop=pageprops` 里带 `disambig` 的就是消歧义页（`1` 这种）。"""
    props = page.get("pageprops") or {}
    return bool(props.get("disambig") is not None or props.get("disambiguation") is not None)


def fill_entries(rows, lookup=None, converter=None) -> int:
    """给每行补 `条目`（只补空着的）→ 补了几行。`lookup` / `converter` 是给测试用的注入点。"""
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
    filled += _retry_traditional(targets, lookup or resolve, converter or wiki_api.traditional)
    if filled:
        logging.info("条目：按站上页面自动填了%d行", filled)
    covers = _fill_covers(targets, table)
    if covers:
        logging.info("翻唱：%d行按「站上已有同名歌曲页」补了翻唱（原稿已削除，可改）", covers)
    return filled


def _retry_traditional(rows, resolve_, converter) -> int:
    """还没对上条目的行，把曲名换成**传统 / 日文汉字**再查一次 → 补了几行。

    用户 2026-11-17：AI / OCR 常把日文汉字抄成简体（`鳥の詩` 抄成 `鸟の诗`），
    拿简体查站上页面查不到，条目就空着、曲名也只好留在视频原名上。
    """
    pending = [row for row in rows or [] if not str(getattr(row, "entry", "") or "").strip()]
    if not pending:
        return 0
    variants: Dict[str, object] = {}
    for row in pending:
        title = str(getattr(row, "title", "") or "").strip()
        if not title:
            continue
        try:
            fixed = str(converter(title) or "").strip()
        except Exception as error:                   # noqa: BLE001 - 转换失败就当没换
            logging.debug("换写法失败（%s）：%s", title, error)
            continue
        if fixed and fixed != title:
            variants[fixed] = row
    if not variants:
        return 0
    try:
        table = resolve_(list(variants)) or {}
    except Exception as error:                       # noqa: BLE001
        logging.info("按繁体写法再查条目失败：%s", error)
        return 0
    filled = 0
    for name, row in variants.items():
        for candidate in candidates(name):
            entry = table.get(candidate)
            if not entry or entry == str(getattr(row, "title", "") or "").strip():
                continue
            row.entry = entry
            row.auto_entry = entry
            filled += 1
            logging.info("条目：「%s」被抄成了简体，按「%s」查到%s", row.title, name, entry)
            break
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