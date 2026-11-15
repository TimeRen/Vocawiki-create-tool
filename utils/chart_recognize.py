"""榜单视频识别：把周刊视频里的每一张名次卡片交给多模态模型抄成表格。

用户 2026-11-15 的要求是「下载榜单的 niconico 视频并根据识别视频填写相关数据」，
所以数据来源是**画面**（再生 / コメント / マイリスト 这些是**当周统计值**，现在的
`getthumbinfo` 给的只是累计值，不能用）。模型只负责「看图抄字」，剩下的补齐
（稿件编号、投稿时间、歌姬）交给 `utils/chart_nico.fill_row()`。

设计要点：

* 帧按 `chart.ai_batch` 张一批发过去（每批一个请求）：一次只看一张会把同一个名次的
  曲名与数字拆到两次请求里，模型很难对上；
* 提示词要求「看不清就写 null」——宁可在表格里留空让用户补，也不要模型编数字；
* 同一名次在多批里出现时按 `本周` 合并，非空值优先（先出现的保留）；
* `derive_weights()` 用站上的计分公式反推权重（`得点 = 再生 + 评论×评论权重 + 收藏×20`）。
"""
import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils import ai_css
from utils.chart_nico import ChartSpec

SYSTEM_PROMPT = """\
你是日文榜单视频的数据抄录员。用户会给你一张或几张视频截图，上面是排行数据
（名次 / 曲名 / P名 / 再生数 / コメント数 / マイリスト数 等）。请把画面里能看清的数据
抄成 JSON。只输出 JSON，不要解释、不要 Markdown 代码块、不要任何多余文字。
看不清或画面里没有的字段一律写 null —— 绝对不要凭印象补数字或猜歌名。"""

PROMPT = """\
这些图是《{title}》（{label}第{index}期）周刊视频里的帧，按时间顺序排列，共{count}张，
取自全片第{first}%–{last}%处。

请输出这样的 JSON：
{{"rows": [
  {{"本周": 1, "曲名": "曲名原文", "P主": "P名", "歌姬": null,
    "得点": 12759, "再生": 5358, "评论": 146, "收藏": 363, "点赞": null,
    "上周": null, "走势": null}}
]}}

规则：
1. 只抄画面上真实出现的文字与数字，看不清 / 没出现的字段写 null，不要猜。
2. 「本周」是名次；「OP」「ED」「PICKUP」这类非数字名次照抄原词。
3. 「曲名」用画面上的日文原文，不要翻译、不要改写；「P主」是 P名 / 作者那一列；
   「歌姬」只在画面明确写了演唱者时才填（用「、」分隔）。
4. 数字只要数字本身：不带单位、不加千分位、不要小数点。「得点」= ポイント / 合計。
5. 「收藏」是 マイリスト 数；「点赞」是 いいね 数（2023 年后的新版视频才有）；
   「上周」是「先週」那一列；「走势」只抄画面上的箭头 / 文字（如 ↑ / ↓ / NEW / RE）。
6. 同一个名次在多张图里都出现时，合并成一条（信息最全的那条）。
7. 一张图里可能出现多行，也可能只有一行的一部分 —— 看到什么抄什么。
{extra}"""

# 模型可能用的键名 → 我们表格里的列名（中英日都认）
KEY_ALIASES = {
    "rank": "本周", "順位": "本周", "名次": "本周", "thisweek": "本周", "本周": "本周",
    "title": "曲名", "曲名": "曲名", "song": "曲名", "name": "曲名", "タイトル": "曲名",
    "producer": "P主", "p主": "P主", "p名": "P主", "作者": "P主", "artist": "P主",
    "vocalist": "歌姬", "歌姬": "歌姬", "歌手": "歌姬", "演唱": "歌姬", "ボーカル": "歌姬",
    "score": "得点", "得点": "得点", "ポイント": "得点", "points": "得点",
    "view": "再生", "再生": "再生", "播放": "再生", "views": "再生", "再生数": "再生",
    "comment": "评论", "评论": "评论", "コメント": "评论", "comments": "评论",
    "mylist": "收藏", "收藏": "收藏", "マイリスト": "收藏", "マイリス": "收藏",
    "like": "点赞", "点赞": "点赞", "いいね": "点赞", "likes": "点赞",
    "last": "上周", "上周": "上周", "先週": "上周", "previous": "上周", "前回": "上周",
    "trend": "走势", "走势": "走势", "rate": "rate", "前週比": "rate",
}
COLUMN_ORDER = ("本周", "曲名", "P主", "歌姬", "得点", "再生", "评论", "收藏", "点赞",
                "上周", "走势", "rate")
# 一批图能读出的行数不固定（一张卡片可能跨两批），给够输出预算
MAX_TOKENS = 4096
NUMBER_RE = re.compile(r"^\s*(-?\d[\d,]*)\s*$")


def build_prompt(spec: ChartSpec, index: int, count: int, first: float, last: float,
                 extra: str = "") -> str:
    """一批帧的提示词（`first` / `last` 是这一批在整片里的位置，0~1）。"""
    tail = f"\n补充要求：{extra.strip()}\n" if str(extra or "").strip() else ""
    return PROMPT.format(title=f"{spec.page_prefix} #{index}", label=spec.label, index=index,
                         count=count, first=int(first * 100), last=int(last * 100),
                         extra=tail)


def parse_reply(text: str) -> List[dict]:
    """模型回复 → 名次字典列表（容忍代码块包裹、`{"rows": [...]}` 与裸数组两种写法）。"""
    raw = str(text or "").strip()
    if not raw:
        return []
    body = raw
    if body.startswith("```"):                  # ```json …``` 代码块
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
    data: object = None
    if body.lstrip().startswith("["):            # 裸数组优先（`extract_json` 会抠出里面那条记录）
        data = _load_array(body)
    if data is None:
        payload = ai_css.extract_json(raw)
        if isinstance(payload, dict):
            for key in ("rows", "data", "list", "items", "result", "榜单"):
                if isinstance(payload.get(key), list):
                    data = payload[key]
                    break
            if data is None:                     # 直接就是 {名次: {...}} 的写法
                data = [value for value in payload.values() if isinstance(value, dict)]
    if data is None:
        data = _load_array(raw)
    if not data:
        logging.warning("榜单识别：回复里没有数据行（前120字：%s）", raw[:120])
        return []
    return [row for row in (normalize_row(item) for item in data) if row]


def _load_array(text: str) -> Optional[list]:
    """文本里第一个 `[` 到最后一个 `]` 之间的 JSON 数组（抠不出来返回 None）。"""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        loaded = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return loaded if isinstance(loaded, list) else None


def normalize_row(raw: object) -> dict:
    """把模型给的一条记录规整成我们认识的列（不认识的键丢掉，数字去掉千分位）。"""
    if not isinstance(raw, dict):
        return {}
    row: Dict[str, str] = {}
    for key, value in raw.items():
        name = KEY_ALIASES.get(str(key or "").strip().lower())
        if name is None:
            name = KEY_ALIASES.get(str(key or "").strip())
        if name is None:
            continue
        text = _text(value)
        if text and (name not in row or not row[name]):
            row[name] = text
    if not row.get("本周") and not row.get("曲名"):
        return {}
    return row


def _text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "n/a", "-", "—", "不明", "nan"):
        return ""
    match = NUMBER_RE.match(text)
    if match:
        return match.group(1).replace(",", "")
    return text


def merge(rows: Iterable[dict]) -> List[dict]:
    """按名次合并多批结果：名次相同的并成一条，先出现的非空值优先。"""
    merged: Dict[str, dict] = {}
    order: List[str] = []
    for row in rows or []:
        if not row:
            continue
        rank = str(row.get("本周") or "").strip()
        key = rank or f"_{len(order)}"
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)
            continue
        target = merged[key]
        for name, value in row.items():
            if value and not target.get(name):
                target[name] = value
    return [merged[key] for key in order]


def sort_rows(rows: Sequence[dict]) -> List[dict]:
    """按名次排一下（`OP` / `ED` 这类非数字名次放在最前，再按数字升序）。"""

    def key_of(row: dict) -> Tuple[int, float, str]:
        rank = str(row.get("本周") or "").strip()
        digits = re.sub(r"[^\d.]", "", rank)
        if not digits:
            return (0, 0.0, rank)
        try:
            return (1, float(digits), rank)
        except ValueError:
            return (1, 0.0, rank)

    return sorted(rows, key=key_of)


def derive_weights(rows: Sequence[dict]) -> None:
    """按站上的计分公式反推权重（只补缺，已有的不动）。

    * 2008 规则：`得点 = 再生 + 评论×评论权重 + 收藏×20`，收藏权重固定 20；
    * 2023 规则：`得点 = 再生 + 评论×评论权重 + (收藏+点赞)×收藏权重`，
      收藏权重与点赞权重相同，`再生权重` 恒为 1.00。评论权重没读到时按 1.00 算 ——
      评论数通常远小于收藏数，误差只落在小数点后第二位。
    """
    for row in rows or []:
        score = _number(row.get("得点"))
        view = _number(row.get("再生"))
        comment = _number(row.get("评论"))
        mylist = _number(row.get("收藏"))
        like = _number(row.get("点赞"))
        if score is None or view is None:
            continue
        if like is not None and not row.get("再生权重"):
            row["再生权重"] = "1.00"
        weight = _number(row.get("评论权重"))
        if comment and weight is None and like is None and mylist is not None:
            row["评论权重"] = _round((score - view - mylist * 20) / comment)
        if (mylist or like) and like is not None:
            if not row.get("收藏权重"):
                used = weight if weight is not None else 1.0
                total = (mylist or 0) + (like or 0)
                if total:
                    value = _round((score - view - comment * used) / total)
                    row["收藏权重"] = value
                    row["点赞权重"] = value


def _round(value: float) -> str:
    text = f"{value:.2f}"
    if text.endswith("0"):
        text = f"{value:.1f}"
    return text


def _number(value: object) -> Optional[float]:
    """`"1,234"` → `1234.0`；认不出返回 None。"""
    text = re.sub(r"[^\d.\-]", "", str(value or ""))
    if not text or text in ("-", "."):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def batches(items: Sequence, size: int) -> List[List]:
    """把一长串东西切成每份 `size` 个（`size <= 0` 时一份一个）。"""
    step = max(1, int(size or 1))
    return [list(items[index:index + step]) for index in range(0, len(items), step)]


def recognize(frames: Sequence[Tuple[float, bytes]], spec: ChartSpec, index: int,
              batch: int = 4, extra: str = "",
              progress=None) -> List[dict]:
    """把抽出来的帧分批发给模型 → 合并后的名次列表（按名次排序）。

    `progress(已做, 总数)` 用来更新界面上的状态文字；某一批失败就跳过那一批
    （只记日志），整体不抛异常 —— 识别不出来的时候用户还能在表格里手填。
    """
    cfg = ai_css.settings()
    if not cfg.get("api_key"):
        raise RuntimeError("未配置ai_api_key（见wiki_credentials.yaml），无法识别榜单画面")
    if not frames:
        return []
    groups = batches(list(frames), batch)
    collected: List[dict] = []
    for number, group in enumerate(groups, start=1):
        if progress:
            progress(number - 1, len(groups))
        images = []
        for _fraction, data in group:
            encoded = ai_css.encode_image_bytes(data)
            if encoded:
                images.append(encoded)
        if not images:
            continue
        prompt = build_prompt(spec, index, len(group), group[0][0], group[-1][0], extra)
        text, error = ai_css.ask(cfg, prompt, images, system=SYSTEM_PROMPT,
                                 max_tokens=MAX_TOKENS, temperature=0.1)
        if error:
            logging.warning("榜单识别第%d/%d批失败：%s", number, len(groups), error)
            continue
        rows = parse_reply(text)
        logging.info("榜单识别第%d/%d批：读到%d行", number, len(groups), len(rows))
        collected.extend(rows)
    if progress:
        progress(len(groups), len(groups))
    merged = sort_rows(merge(collected))
    if not merged:
        logging.warning("榜单识别没读到任何一行（%d批全部失败或画面里没有数据）", len(groups))
    return merged


__all__ = ["recognize", "parse_reply", "normalize_row", "merge", "sort_rows",
           "derive_weights", "build_prompt", "batches", "SYSTEM_PROMPT", "PROMPT",
           "COLUMN_ORDER"]
