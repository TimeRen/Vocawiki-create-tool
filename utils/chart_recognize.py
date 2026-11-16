"""榜单视频识别：把周刊视频里的每一张名次卡片交给多模态模型抄成表格。

用户 2026-11-15 的要求是「下载榜单的 niconico 视频并根据识别视频填写相关数据」，
所以数据来源是**画面**（再生 / コメント / マイリスト 这些是**当周统计值**，现在的
`getthumbinfo` 给的只是累计值，不能用）。模型只负责「看图抄字」，剩下的补齐
（稿件编号、投稿时间、歌姬）交给 `utils/chart_nico.fill_row()`。

设计要点：

* 帧按 `chart.ai_batch` 张一批发过去（每批一个请求）：一次只看一张会把同一个名次的
  曲名与数字拆到两次请求里，模型很难对上；
* 提示词要求「看不清就写 null」——宁可在表格里留空让用户补，也不要模型编数字；
* 提示词另外交代了三件用户踩过的事（截图 3 / 4 / 5）：
  `History` 整段不抄（它不是榜单）、`UTAU番外榜` 要标成 `小节 = 番外`、
  大号码的 TOPIC 曲照抄；`【…】` 里的是歌姬不是 P主；
* 同一名次在多批里出现时按 `(小节, 本周)` 合并，非空值优先（先出现的保留）——
  只按名次合并会让番外的 1～3 名和主榜的 1～3 名撞号；
* `derive_weights()` 用站上的计分公式反推权重（`得点 = 再生 + 评论×评论权重 + 收藏×20`）。
"""
import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils import ai_css
from utils.chart_nico import ChartSpec, check_cancelled
from utils.chart_wikitext import BADGE_NAMES

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
  {{"小节": null, "本周": 1, "曲名": "曲名原文", "P主": "P名", "歌姬": null, "角标": null,
    "得点": 12759, "再生": 5358, "评论": 146, "收藏": 363, "点赞": null,
    "上周": null, "走势": null}}
]}}

规则：
1. 只抄画面上真实出现的文字与数字，看不清 / 没出现的字段写 null，不要猜。
2. 只抄**本期排行榜本身**：OP / ED / 片头片尾的说明画面 / 广告 / 下期预告一律不抄。
3. 「History」（今週のHistory、この曲のHistory，通常是 5 首往期名曲）的整段都不要抄；
   万一抄了就把「小节」写成 "history"，程序会丢掉。
4. 视频后面有「UTAU番外榜」这类加场榜单时，那些行的「小节」写 "番外"；
   主榜的行「小节」写 null。
5. 主榜里号码很大的名次（75 / 88 / 110 / 125 / 139 …）是 TOPIC 曲，属于榜单，照抄；
   不要因为它们夹在中间就跳过。
6. 「本周」是名次；「OP」「ED」「PICKUP」这类非数字名次照抄原词。
7. 「曲名」用画面上的日文原文，不要翻译、不要改写；「P主」是 P名 / 作者那一列；
   「歌姬」只在画面明确写了演唱者时才填（用「、」分隔）。
8. 曲名开头的【…】里写的是**歌姬**（如【重音テト】世界の終わり）：把【…】里的名字填进
   「歌姬」，剩下的部分填「曲名」；「P主」是作者名，**绝不能等于曲名，也绝不能把
   【…】里的歌姬名填进「P主」**。
9. 卡片上的角标照抄文字：「角标」只填 PICKUP / TOPIC / 上周冠军 三者之一，没有就 null。
   粉色带 PICKUP 字样的是 PICKUP，绿色带 TOPIC 字样的是 TOPIC，两者不能互换。
10. 数字只要数字本身：不带单位、不加千分位、不要小数点。「得点」= ポイント / 合計。
11. 「收藏」是 マイリスト 数；「点赞」是 いいね 数（2023 年后的新版视频才有）；
    「上周」是「先週」那一列；「走势」只抄画面上的箭头 / 文字（如 ↑ / ↓ / NEW / RE）。
12. 同一个名次在多张图里都出现时，合并成一条（信息最全的那条）。
13. 一张图里可能出现多行，也可能只有一行的一部分 —— 看到什么抄什么。
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
    # 小节（主榜 / 番外 / history）与角标（PICKUP / TOPIC / 上周冠军）
    "小节": "小节", "section": "小节", "榜单": "小节", "sectionname": "小节",
    "part": "小节", "榜": "小节",
    "角标": "角标", "badge": "角标", "mark": "角标", "tag": "角标", "label": "角标",
    "color": "角标", "colour": "角标", "bottom-column": "角标", "bottomcolumn": "角标",
    "bottom_column": "角标", "角标文字": "角标",
}
COLUMN_ORDER = ("本周", "曲名", "P主", "歌姬", "得点", "再生", "评论", "收藏", "点赞",
                "上周", "走势", "rate", "小节", "角标")
# 小节：空字符串＝主榜，`番外`＝加场榜单，`history`＝往期回顾（会被丢掉）
SECTION_MAIN = ""
SECTION_EXTRA = "番外"
SECTION_HISTORY = "history"
SECTION_ORDER = (SECTION_MAIN, SECTION_EXTRA, SECTION_HISTORY)
SECTION_ALIASES = {
    "": SECTION_MAIN, "main": SECTION_MAIN, "主榜": SECTION_MAIN, "主榜单": SECTION_MAIN,
    "本期": SECTION_MAIN, "ランキング": SECTION_MAIN, "ranking": SECTION_MAIN,
    "番外": SECTION_EXTRA, "番外榜": SECTION_EXTRA, "番外編": SECTION_EXTRA,
    "utau番外榜": SECTION_EXTRA, "extra": SECTION_EXTRA, "bonus": SECTION_EXTRA,
    "history": SECTION_HISTORY, "历史": SECTION_HISTORY, "今週のhistory": SECTION_HISTORY,
    "この曲のhistory": SECTION_HISTORY, "往期回顾": SECTION_HISTORY,
}
# 角标只认名字（颜色由 `chart_wikitext.BADGES` 决定，绝不从颜色反推名字）
BADGE_ALIASES = {
    "pickup": "PICKUP", "p i c k u p": "PICKUP", "pick up": "PICKUP", "ピックアップ": "PICKUP",
    "topic": "TOPIC", "t o p i c": "TOPIC", "トピック": "TOPIC",
    "上周冠军": "上周冠军", "先週1位": "上周冠军", "前週1位": "上周冠军",
    "上周第一": "上周冠军", "lastweekno.1": "上周冠军",
}
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


def normalize_section(value: object) -> str:
    """`小节` 归一：认不出的按主榜处理（宁可多抄一行，也别把榜单行丢掉）。"""
    text = _text(value).strip()
    if not text:
        return SECTION_MAIN
    key = text.lower().replace(" ", "").replace("_", "")
    return SECTION_ALIASES.get(key, SECTION_ALIASES.get(text, SECTION_MAIN))


def normalize_badge(value: object) -> str:
    """`角标` 只认名字；认不出的一律当没有（颜色永远由名字查表决定）。"""
    text = _text(value).strip()
    if not text:
        return ""
    key = text.lower().replace("　", " ").strip()
    name = BADGE_ALIASES.get(key, BADGE_ALIASES.get(key.replace(" ", ""), ""))
    return name if name in BADGE_NAMES else ""


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
    if "小节" in row:
        section = normalize_section(row["小节"])
        if section:
            row["小节"] = section
        else:
            row.pop("小节", None)
    if "角标" in row:
        badge = normalize_badge(row["角标"])
        if badge:
            row["角标"] = badge
        else:
            row.pop("角标", None)
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
    """按 `(小节, 本周)` 合并多批结果：同一条并起来，先出现的非空值优先。

    键里必须带 `小节`：番外榜的 1～3 名和主榜的 1～3 名是**不同**的曲目，
    只按名次合并会让两组撞号、番外整段消失（截图 3 就是这个毛病）。
    """
    merged: Dict[Tuple[str, str], dict] = {}
    order: List[Tuple[str, str]] = []
    for row in rows or []:
        if not row:
            continue
        section = str(row.get("小节") or "").strip()
        rank = str(row.get("本周") or "").strip()
        key = (section, rank or f"_{section}_{len(order)}")
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)
            continue
        target = merged[key]
        for name, value in row.items():
            if value and not target.get(name):
                target[name] = value
    return [merged[key] for key in order]


def _rank_number(row: dict) -> Optional[float]:
    digits = re.sub(r"[^\d.]", "", str(row.get("本周") or "").strip())
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def sort_rows(rows: Sequence[dict]) -> List[dict]:
    """按 (小节, 名次) 排：主榜 → 番外 → history，各段内数字升序、非数字名次在前。

    以前只按名次排，番外的行会散进主榜里；现在分段之后 `render()` 才能分小节输出。
    """
    section_index = {name: index for index, name in enumerate(SECTION_ORDER)}

    def key_of(row: dict) -> Tuple[int, int, float, str]:
        section = str(row.get("小节") or "").strip()
        rank = str(row.get("本周") or "").strip()
        number = _rank_number(row)
        return (section_index.get(section, len(SECTION_ORDER)),
                0 if number is None else 1, number or 0.0, rank)

    return sorted(rows, key=key_of)


def missing_ranks(rows: Sequence[dict]) -> Dict[str, List[int]]:
    """各小节在 `1..max` 里缺了哪些名次 → `{"": [7], "番外": [2]}`（没缺就是空字典）。

    视频里同一张卡片可能被算法吃掉（截图 3：第 7 名没了），这里只负责**发现**，
    补行由用户在「榜单」页点「添加行」来做。
    """
    gaps: Dict[str, List[int]] = {}
    seen: Dict[str, set] = {}
    for row in rows or []:
        number = _rank_number(row)
        if number is None or number != int(number) or number < 1:
            continue
        section = str(row.get("小节") or "").strip()
        if section == SECTION_HISTORY:
            continue
        seen.setdefault(section, set()).add(int(number))
    for section, numbers in seen.items():
        if not numbers:
            continue
        top = max(numbers)
        lacking = [rank for rank in range(1, top + 1) if rank not in numbers]
        if lacking:
            gaps[section] = lacking
    return gaps


def drop_history(rows: Sequence[dict]) -> List[dict]:
    """丢掉 `小节 = history` 的行（History 是往期回顾，不属于本期榜单）。"""
    kept = [row for row in rows or []
            if str(row.get("小节") or "").strip() != SECTION_HISTORY]
    dropped = len(list(rows or [])) - len(kept)
    if dropped:
        logging.info("榜单识别：已忽略History的%d首（往期回顾不算本期榜单）", dropped)
    return kept



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
        check_cancelled()
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
    merged = sort_rows(drop_history(merge(collected)))
    if not merged:
        logging.warning("榜单识别没读到任何一行（%d批全部失败或画面里没有数据）", len(groups))
    else:
        gaps = missing_ranks(merged)
        for section, lacking in gaps.items():
            logging.warning("%s缺第%s名（可点「添加行」补）",
                            "番外榜" if section == SECTION_EXTRA else "主榜",
                            "、".join(str(rank) for rank in lacking))
    return merged


__all__ = ["recognize", "parse_reply", "normalize_row", "merge", "sort_rows",
           "derive_weights", "build_prompt", "batches", "SYSTEM_PROMPT", "PROMPT",
           "COLUMN_ORDER", "missing_ranks", "drop_history", "normalize_section",
           "normalize_badge", "SECTION_MAIN", "SECTION_EXTRA", "SECTION_HISTORY",
           "SECTION_ORDER", "BADGE_ALIASES"]
