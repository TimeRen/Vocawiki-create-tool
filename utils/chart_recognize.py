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
{{"section": null,
 "rows": [
  {{"标签": null, "本周": 1, "曲名": "曲名原文", "P主": "P名", "歌姬": null, "角标": null,
    "得点": 12759, "再生": 5358, "评论": 146, "收藏": 363, "点赞": null,
    "上周": null, "走势": null}}
]}}

规则：
1. 只抄画面上真实出现的文字与数字，看不清 / 没出现的字段写 null，不要猜。
2. 只要画面里是**排名卡片或排名表**就抄，一张都不要漏（第 1 名、最后一名、加场榜单都要抄）。
   只有纯片头 / 纯片尾 / 广告 / 下期预告这类**没有名次也没有曲名**的画面才跳过。
3. 「标签」＝卡片上除了名次数字之外的**那行小字**，照抄原文，例如：
   `ピックアップ曲`（粉色带）、`T O P I C`（绿色带）、`番外ランキング3位`、
   `新音源`、`ヒストリー2位`、`オープニング曲`、`エンディング曲`。没有这行小字就写 null。
   **不要自己判断颜色，也不要自己起名字，只照抄你看到的那行字。**
4. 「本周」是名次数字。卡片写「番外ランキング3位」时，「本周」照写 3（程序会按「标签」归到番外榜）。
   名次是 `OP` / `ED` / `PICKUP` 这类非数字时照抄原词。
5. 「角标」只在卡片上确实有角标时填，只填 PICKUP / TOPIC / 上周冠军 三者之一，没有就 null；
   拿不准就写 null，程序会按「标签」再判断一次。
6. 「曲名」用画面上的日文原文，不要翻译、不要改写；「P主」是 P名 / 作者那一列；
   「歌姬」只在画面明确写了演唱者时才填（用「、」分隔）。
7. 曲名开头的【…】里写的是**歌姬**（如【重音テト】世界の終わり）：把【…】里的名字填进
   「歌姬」，剩下的部分填「曲名」；「P主」是作者名，**绝不能等于曲名，也绝不能把
   【…】里的歌姬名填进「P主」**。
8. **曲名里的空格要按画面原样保留**（`GO MY WAY` 不要写成 `GOMYWAY`，`手描きＰＶ` 不要写成
   `手描きP V`）—— 后面要靠它去 niconico 搜稿件。
9. 数字只要数字本身：不带单位、不加千分位、不要小数点。「得点」= ポイント / 合計。
10. 「收藏」是 マイリスト 数；「点赞」是 いいね 数（2023 年后的新版视频才有）；
    「上周」是「先週」那一列；「走势」只抄画面上的箭头 / 文字（如 ↑ / ↓ / NEW / RE）。
11. 同一个名次在多张图里都出现时，合并成一条（信息最全的那条）。
12. 一张图里可能出现多行（末尾常有整页排名表），也可能只有一行的一部分 —— 看到什么抄什么。
13. 整批图都属于同一段（例如全在番外榜）时，顶层的 `section` 写 `"番外"` / `"history"` /
    `"新音源"`；主榜写 null。每行的「标签」仍然要照抄。
14. **名次只抄卡片上真正印出来的大号码**：`ピックアップ曲` / `TOPIC` 卡片上没有名次数字时，
    「本周」写 null，**绝对不要按出场顺序自己编号**（以前这么编号，把番外 1～4 位顶成了
    主榜 1～4 位）。
{extra}"""

# 一批图一行都没读出来时用这个再问一次：上一版提示词里「片头片尾不抄」让模型把番外 /
# PICKUP / 新音源 的卡片整段跳过了（实测 69 批里有 11 批返回空）。
RETRY_PROMPT = """\
这些图是《{title}》（{label}第{index}期）周刊视频里的帧。

**上一轮这些图一行都没抄出来。请再看一次：只要画面里有名次（或加场榜单的编号）和曲名，就必须抄。**
番外榜、ピックアップ曲、TOPIC、新音源、ヒストリー 这些**都不是片头片尾，必须抄**，
并且把卡片上那行小字照抄到「标签」里（例如 `番外ランキング2位`、`ピックアップ曲`、`新音源`、
`ヒストリー1位`）。

输出 JSON：{{"section": null, "rows": [{{"标签": null, "本周": 1, "曲名": "曲名原文",
"P主": null, "歌姬": null, "角标": null, "得点": null, "再生": null}}]}}
看不清就写 null，一行都没有才返回 {{"rows": []}}。
{extra}"""

# 角标兜底：只问「哪些行是粉 / 绿底」，比让模型在抄表时顺手判断可靠。
BADGE_PROMPT = """\
这些图是《{title}》（{label}第{index}期）周刊视频里的帧。

只看**底色 / 角标**：这一屏里有没有底色是粉色（印着 `P I C K U P`）的行，
或者底色是绿色（印着 `T O P I C`）的行？

只输出 JSON，把它们的名次和曲名列出来；没有就返回空对象：
{{"badges": {{"26": "PICKUP", "32": "TOPIC"}}}}"""

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
    # 小节（主榜 / 番外 / history / 新音源）与角标（PICKUP / TOPIC / 上周冠军）
    "小节": "小节", "section": "小节", "榜单": "小节", "sectionname": "小节",
    "part": "小节", "榜": "小节",
    # 标签＝卡片上照抄的那行小字（番外ランキング3位 / ピックアップ曲 / 新音源 …）
    "标签": "标签", "label": "标签", "caption": "标签", "小字": "标签", "分类": "标签",
    "榜种": "标签", "badgetext": "标签", "cardename": "标签",
    "角标": "角标", "badge": "角标", "mark": "角标", "tag": "角标",
    "color": "角标", "colour": "角标", "bottom-column": "角标", "bottomcolumn": "角标",
    "bottom_column": "角标", "角标文字": "角标",
}
COLUMN_ORDER = ("本周", "曲名", "P主", "歌姬", "得点", "再生", "评论", "收藏", "点赞",
                "上周", "走势", "rate", "小节", "角标")
# 小节：空字符串＝主榜，`番外`＝加场榜单；其余几段都不属于本期榜单
SECTION_MAIN = ""
SECTION_EXTRA = "番外"
SECTION_HISTORY = "history"
SECTION_NEW = "新音源"
SECTION_OP_ED = "op-ed"
SECTION_ORDER = (SECTION_MAIN, SECTION_EXTRA, SECTION_HISTORY, SECTION_NEW, SECTION_OP_ED)
# 这些小节不算榜单，识别完就丢掉（`drop_non_chart`）
NON_CHART_SECTIONS = (SECTION_HISTORY, SECTION_NEW, SECTION_OP_ED)
SECTION_ALIASES = {
    "": SECTION_MAIN, "main": SECTION_MAIN, "主榜": SECTION_MAIN, "主榜单": SECTION_MAIN,
    "本期": SECTION_MAIN, "ランキング": SECTION_MAIN, "ranking": SECTION_MAIN,
    "番外": SECTION_EXTRA, "番外榜": SECTION_EXTRA, "番外編": SECTION_EXTRA,
    "utau番外榜": SECTION_EXTRA, "extra": SECTION_EXTRA, "bonus": SECTION_EXTRA,
    "history": SECTION_HISTORY, "历史": SECTION_HISTORY, "今週のhistory": SECTION_HISTORY,
    "この曲のhistory": SECTION_HISTORY, "往期回顾": SECTION_HISTORY,
    "ヒストリー": SECTION_HISTORY, "ヒストリー1位": SECTION_HISTORY,
    "新音源": SECTION_NEW, "new": SECTION_NEW, "newsource": SECTION_NEW,
    "オープニング曲": SECTION_OP_ED, "エンディング曲": SECTION_OP_ED,
    "op": SECTION_OP_ED, "ed": SECTION_OP_ED, "オープニング": SECTION_OP_ED,
    "エンディング": SECTION_OP_ED,
}
# 角标只认名字（颜色由 `chart_wikitext.BADGES` 决定，绝不从颜色反推名字）
BADGE_ALIASES = {
    "pickup": "PICKUP", "p i c k u p": "PICKUP", "pick up": "PICKUP", "ピックアップ": "PICKUP",
    "ピックアップ曲": "PICKUP", "pickup曲": "PICKUP",
    "topic": "TOPIC", "t o p i c": "TOPIC", "トピック": "TOPIC", "topic曲": "TOPIC",
    "上周冠军": "上周冠军", "先週1位": "上周冠军", "前週1位": "上周冠军",
    "上周第一": "上周冠军", "lastweekno.1": "上周冠军",
}
# 一批图能读出的行数不固定（一张卡片可能跨两批），给够输出预算
MAX_TOKENS = 4096
NUMBER_RE = re.compile(r"^\s*(-?\d[\d,]*)\s*$")
# 「标签」里认小节 / 名次：`番外ランキング3位` / `第3位` / `ヒストリー2位`
LABEL_RANK_RE = re.compile(r"(\d{1,4})\s*(?:位|い|st|nd|rd|th)", re.I)




def build_prompt(spec: ChartSpec, index: int, count: int, first: float, last: float,
                 extra: str = "") -> str:
    """一批帧的提示词（`first` / `last` 是这一批在整片里的位置，0~1）。"""
    tail = f"\n补充要求：{extra.strip()}\n" if str(extra or "").strip() else ""
    return PROMPT.format(title=f"{spec.page_prefix} #{index}", label=spec.label, index=index,
                         count=count, first=int(first * 100), last=int(last * 100),
                         extra=tail)


def parse_reply(text: str) -> List[dict]:
    """模型回复 → 名次字典列表（容忍代码块包裹、`{"rows": [...]}` 与裸数组两种写法）。"""
    return parse_payload(text)["rows"]


def parse_payload(text: str) -> dict:
    """模型回复 → `{"section": …, "badges": {名次: 角标}, "rows": […]}`。

    顶层除了 `rows` 还认 `section`（整批都在番外榜/新源时填一次）与 `badges`
    （角标兜底提问的回复）。`parse_reply` 只是取 `rows`，测试与旧调用不用改。
    """
    payload = {"section": SECTION_MAIN, "badges": {}, "rows": []}
    raw = str(text or "").strip()
    if not raw:
        return payload
    body = raw
    if body.startswith("```"):                  # ```json …``` 代码块
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
    data: object = None
    top: object = None
    if body.lstrip().startswith("["):            # 裸数组优先（`extract_json` 会抠出里面那条记录）
        data = _load_array(body)
    if data is None:
        top = ai_css.extract_json(raw)
        if isinstance(top, dict):
            for key in ("rows", "data", "list", "items", "result", "榜单"):
                if isinstance(top.get(key), list):
                    data = top[key]
                    break
            if data is None:                     # 直接就是 {名次: {...}} 的写法
                data = [value for value in top.values() if isinstance(value, dict)]
    if data is None:
        data = _load_array(raw)
    if isinstance(top, dict):
        payload["section"] = normalize_section(top.get("section") or top.get("小节"))
        payload["badges"] = _badge_map(top.get("badges"))
    if not data:
        logging.warning("榜单识别：回复里没有数据行（前120字：%s）", raw[:120])
        return payload
    payload["rows"] = [row for row in (normalize_row(item) for item in data) if row]
    return payload


def _badge_map(value: object) -> Dict[str, str]:
    """`{"26": "PICKUP"}` → `{"26": "PICKUP"}`（认不出的角标丢掉）。"""
    found: Dict[str, str] = {}
    if not isinstance(value, dict):
        return found
    for rank, badge in value.items():
        number = re.sub(r"\D", "", str(rank or ""))
        name = normalize_badge(badge)
        if number and name:
            found[number] = name
    return found



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
    """`小节` / `标签` 归一：认不出的按主榜处理（宁可多抄一行，也别把榜单行丢掉）。"""
    text = _text(value).strip()
    if not text:
        return SECTION_MAIN
    key = text.lower().replace(" ", "").replace("_", "")
    found = SECTION_ALIASES.get(key) or SECTION_ALIASES.get(text)
    if found is not None:
        return found
    # 整行小字（`番外ランキング3位` / `今週のヒストリー` / `新音源`）按关键词认
    lowered = key.lower()
    if "番外" in lowered:
        return SECTION_EXTRA
    if "history" in lowered or "ヒストリー" in lowered or "履歴" in lowered:
        return SECTION_HISTORY
    if "新音源" in lowered:
        return SECTION_NEW
    if "オープニング" in lowered or "エンディング" in lowered or "opening" in lowered \
            or "ending" in lowered:
        return SECTION_OP_ED
    return SECTION_MAIN


def normalize_badge(value: object) -> str:
    """`角标` / `标签` 只认名字；认不出的一律当没有（颜色永远由名字查表决定）。"""
    text = _text(value).strip()
    if not text:
        return ""
    key = text.lower().replace("　", " ").strip()
    name = BADGE_ALIASES.get(key, BADGE_ALIASES.get(key.replace(" ", ""), ""))
    if name in BADGE_NAMES:
        return name
    for alias, badge in BADGE_ALIASES.items():
        if alias and alias in key:
            return badge if badge in BADGE_NAMES else ""
    return ""


def parse_label(value: object) -> dict:
    """卡片上那行小字 → `{"section": …, "badge": …, "rank": …}`（没认出的键不出现）。

    实测（用户 2026-11-16 的 #53 缓存）：番外卡写的是 `番外ランキング1位`，模型把 1 当成主榜
    名次，结果番外 1～5 名顶掉了主榜 1～5 名、主榜第 1 名整首丢失。所以名次必须和「这是哪一段」
    一起读出来，**番外的名次由标签决定**；主榜的名次仍以画面上的大号码为准。
    """
    text = _text(value).strip()
    if not text:
        return {}
    found: Dict[str, str] = {}
    section = normalize_section(text)
    if section:
        found["section"] = section
    badge = normalize_badge(text)
    if badge:
        found["badge"] = badge
    match = LABEL_RANK_RE.search(text)
    if match:
        found["rank"] = str(int(match.group(1)))
    elif section in (SECTION_MAIN, SECTION_EXTRA) and text.isdigit():
        found["rank"] = str(int(text))
    return found


def apply_label(row: dict) -> dict:
    """把 `标签` 里读出的小节 / 角标 / 名次写回这一行（原地改，也返回它）。"""
    info = parse_label(row.get("标签"))
    if not info:
        return row
    if info.get("section") and info["section"] != SECTION_MAIN:
        row["小节"] = info["section"]
        if info["section"] != SECTION_EXTRA:
            row.pop("角标", None)                 # 历史 / 新音源 的角标没有意义
    if info.get("section") == SECTION_EXTRA and info.get("rank"):
        # 番外的名次以标签为准（卡片上写的就是「番外ランキングN位」）
        row["本周"] = info["rank"]
    if info.get("badge"):
        row["角标"] = info["badge"]
    return row


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
    return apply_label(row)




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


def missing_ranks(rows: Sequence[dict], limit: int = 0) -> Dict[str, List[int]]:
    """各小节在 `1..max` 里缺了哪些名次 → `{"": [7], "番外": [2]}`（没缺就是空字典）。

    视频里同一张卡片可能被算法吃掉（截图 3：第 7 名没了；用户 2026-11-16 的 #53：
    主榜第 1～5 名整段没读到），这里只负责**发现**，补行由用户在「榜单」页点「添加行」来做。
    `limit > 0` 时主榜只查到 `limit`（只收录前 N 名，后面本来就不要求）。
    """
    gaps: Dict[str, List[int]] = {}
    seen: Dict[str, set] = {}
    for row in rows or []:
        number = _rank_number(row)
        if number is None or number != int(number) or number < 1:
            continue
        section = str(row.get("小节") or "").strip()
        if section in NON_CHART_SECTIONS:
            continue
        seen.setdefault(section, set()).add(int(number))
    for section, numbers in seen.items():
        if not numbers:
            continue
        top = max(numbers)
        if limit and section == SECTION_MAIN:
            # 只收录前 limit 名：21 名之后是视频末尾的整页排名表，缺了也不提醒
            top = min(top, int(limit))
        lacking = [rank for rank in range(1, top + 1) if rank not in numbers]
        if lacking:
            gaps[section] = lacking
    return gaps


def expected_gaps(rows: Sequence[dict], limit: int = 0) -> Dict[str, List[int]]:
    """`missing_ranks` 的别名（「应该有的名次里缺哪些」），日志与面板都读它。"""
    return missing_ranks(rows, limit=limit)


def drop_non_chart(rows: Sequence[dict]) -> List[dict]:
    """丢掉 History / 新音源 / OP・ED —— 它们都不是本期榜单（只记一行汇总）。"""
    kept = [row for row in rows or []
            if str(row.get("小节") or "").strip() not in NON_CHART_SECTIONS]
    counts: Dict[str, int] = {}
    for row in rows or []:
        section = str(row.get("小节") or "").strip()
        if section in NON_CHART_SECTIONS:
            counts[section] = counts.get(section, 0) + 1
    if counts:
        parts = [f"{NAME_OF_SECTION.get(section, section)}{count}首"
                 for section, count in counts.items()]
        logging.info("榜单识别：已忽略%s（不算本期榜单）", "、".join(parts))
    return kept


# 日志里给人看的段名
NAME_OF_SECTION = {SECTION_MAIN: "主榜", SECTION_EXTRA: "番外榜", SECTION_HISTORY: "History",
                   SECTION_NEW: "新音源", SECTION_OP_ED: "OP/ED"}


def keep_pickup(row: dict) -> bool:
    """这一行是不是「点名曲」（PICKUP / TOPIC）—— 只有它们能突破前 N 名的限制。"""
    return str(row.get("角标") or "").strip() in ("PICKUP", "TOPIC")


def trim_main(rows: Sequence[dict], limit: int = 0) -> Tuple[List[dict], int]:
    """主榜只留前 `limit` 名（`limit <= 0` 表示不截断）→ `(保留的行, 丢掉几行)`。

    用户 2026-11-16 定的口径：**主榜只收录前二十名，二十名之后除非是 PICKUP / TOPIC 曲
    否则不收录**。周刊视频末尾会把 21～100 名整页列出来（还可能跟着「新音源」段的大号），
    不截断的话一张表会有一百多行。番外榜不受此限。
    """
    items = list(rows or [])
    if not limit or limit <= 0:
        return items, 0
    kept: List[dict] = []
    dropped = 0
    for row in items:
        section = str(row.get("小节") or "").strip()
        number = _rank_number(row)
        if section == SECTION_MAIN and number is not None and number > limit \
                and not keep_pickup(row):
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped




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


def build_retry_prompt(spec: ChartSpec, index: int, extra: str = "") -> str:
    """0 行批次的重试用提示词（`first`/`last` 用不到，不必再算）。"""
    tail = f"\n补充要求：{extra.strip()}\n" if str(extra or "").strip() else ""
    return RETRY_PROMPT.format(title=f"{spec.page_prefix} #{index}", label=spec.label,
                               index=index, extra=tail)


def build_badge_prompt(spec: ChartSpec, index: int) -> str:
    """角标兜底提问（只问粉/绿底的行）。"""
    return BADGE_PROMPT.format(title=f"{spec.page_prefix} #{index}", label=spec.label,
                               index=index)


def _images_for(group: Sequence[Tuple[float, bytes]]) -> List[str]:
    """一批帧 → 编码后的图片列表。"""
    images = []
    for _fraction, data in group:
        encoded = ai_css.encode_image_bytes(data)
        if encoded:
            images.append(encoded)
    return images


def _ask(cfg: dict, prompt: str, images: List[str]) -> Optional[dict]:
    """问一次模型 → `parse_payload` 的结果（失败返回 None，只记一行 DEBUG）。"""
    text, error = ai_css.ask(cfg, prompt, images, system=SYSTEM_PROMPT,
                             max_tokens=MAX_TOKENS, temperature=0.1)
    if error:
        logging.debug("榜单识别请求失败：%s", error)
        return None
    return parse_payload(text)


def recognize(frames: Sequence[Tuple[float, bytes]], spec: ChartSpec, index: int,
              batch: int = 4, extra: str = "", progress=None,
              main_limit: int = 0, playlist: Optional[Sequence[dict]] = None) -> List[dict]:
    """把抽出来的帧分批发给模型 → 合并后的名次列表（按小节+名次排序）。

    `progress(已做, 总数)` 用来更新界面上的状态文字；某一批失败就跳过那一批
    （只记日志），整体不抛异常 —— 识别不出来的时候用户还能在表格里手填。

    用户 2026-11-16 报过的几件事都在这里兜着：

    * **一行都没读到的批次要重试**：旧提示词里「片头片尾不抄」让模型把番外 / PICKUP /
      新音源 的卡片整段跳过了（实测 69 批里 11 批返回空，番外只认到一首）；
    * **角标读不到要单独问一次**：让模型在抄表时顺手判断粉/绿底不可靠，
      对「有超过前 N 名的行、但整批一个角标都没有」的批次再问一句「哪些行是粉/绿底」；
    * **TOPIC 按画面位置认**：周刊视频的段序是 `主榜 10→4 ➡ 情报段 ➡ TOPIC段 ➡ 主榜 3→1`，
      所以「主榜 4 位那一帧」与「主榜 3 位那一帧」之间的行就是 TOPIC（见 `_apply_topic_window`）；
    * **有官方播放列表时以它为准**：`playlist` 传进来就交给 `chart_playlist.align()`
      决定有哪些行、名次多少（画面只贡献数字），没有就沿用原来的合并 + 截断逻辑。
    """
    cfg = ai_css.settings()
    if not cfg.get("api_key"):
        raise RuntimeError("未配置ai_api_key（见wiki_credentials.yaml），无法识别榜单画面")
    if not frames:
        return []
    groups = batches(list(frames), batch)
    collected: List[dict] = []
    empty_batches = 0
    retried_rows = 0
    badge_hits: Dict[str, str] = {}
    badge_asked = 0
    for number, group in enumerate(groups, start=1):
        check_cancelled()
        if progress:
            progress(number - 1, len(groups))
        images = _images_for(group)
        if not images:
            continue
        prompt = build_prompt(spec, index, len(group), group[0][0], group[-1][0], extra)
        payload = _ask(cfg, prompt, images)
        if payload is not None:
            rows = _apply_batch_section(payload["rows"], payload["section"])
            badge_hits.update(payload["badges"])
        else:
            rows = []
        if not rows:
            # 一行都没抄到：可能是被「片头片尾不抄」误伤了，宽松地再问一次
            empty_batches += 1
            payload = _ask(cfg, build_retry_prompt(spec, index, extra), images)
            if payload is not None:
                rows = _apply_batch_section(payload["rows"], payload["section"])
                badge_hits.update(payload["badges"])
                retried_rows += len(rows)
        logging.info("榜单识别第%d/%d批：读到%d行", number, len(groups), len(rows))
        for row in rows:
            # 这一批第一帧的位置（存成字符串：0.0 在 merge() 里会被当成「空值」）
            row.setdefault(FRAME_KEY, "%.3f" % float(group[0][0]))
        if main_limit and badge_asked < BADGE_RETRY_BUDGET \
                and _needs_badge_pass(rows, main_limit):
            badge_asked += 1
            payload = _ask(cfg, build_badge_prompt(spec, index), images)
            if payload is not None and payload["badges"]:
                badge_hits.update(payload["badges"])
        collected.extend(rows)
    if progress:
        progress(len(groups), len(groups))
    if empty_batches:
        logging.info("榜单画面自查：%d/%d批第一次没读到数据，重试后补回%d行",
                     empty_batches, len(groups), retried_rows)
    if badge_asked:
        logging.info("角标自查：为%d批补问了底色，确认%d行的角标", badge_asked, len(badge_hits))
    merged = merge(_apply_badges(collected, badge_hits))
    # 先按画面位置认 TOPIC，再丢「新音源 / History」：模型常把 TOPIC 段的卡片标成「新音源」，
    # 先丢的话它们就再也进不来了（用户 2026-11-16：「TOPIC 这几首都要加进条目里」）。
    merged, topic_count = _apply_topic_window(merged)
    if topic_count:
        logging.info("榜单：按画面位置认出%d首TOPIC曲", topic_count)
    merged = drop_non_chart(merged)
    if playlist:
        from utils import chart_playlist

        merged, _stat = chart_playlist.align(merged, playlist, main_limit)
    else:
        merged, dropped = trim_main(merged, main_limit)
        if dropped:
            logging.info("榜单：已忽略主榜第%d名之后的%d首（只收录前%d名 + PICKUP/TOPIC）",
                         main_limit + 1, dropped, main_limit)
    merged = sort_rows(merged)
    for row in merged:
        row.pop(FRAME_KEY, None)
    if not merged:
        logging.warning("榜单识别没读到任何一行（%d批全部失败或画面里没有数据）", len(groups))
    else:
        for section, lacking in missing_ranks(merged, limit=main_limit).items():
            logging.warning("%s缺第%s名（可点「添加行」补）",
                            NAME_OF_SECTION.get(section, section or "主榜"),
                            "、".join(str(rank) for rank in lacking))
    return merged


# 行里临时记的「出现在第几帧」（只在识别内部用，返回前删掉，免得流进 wikitext）
FRAME_KEY = "_帧"
# TOPIC 段的段序：主榜 10→4、情报段、TOPIC 段、主榜 3→1（用户 2026-11-16 给的）
TOPIC_WINDOW = (4, 3)


def _frame_of(rows: Sequence[dict], rank: int) -> Optional[float]:
    """主榜第 `rank` 名是在第几帧出现的（读不到返回 None）。"""
    for row in rows or []:
        if str(row.get("小节") or "").strip() != SECTION_MAIN:
            continue
        number = _rank_number(row)
        if number is None or int(number) != int(rank):
            continue
        value = row.get(FRAME_KEY)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _apply_topic_window(rows: List[dict]) -> Tuple[List[dict], int]:
    """主榜 4 位之后、主榜 3 位之前的那些行 → `角标 = TOPIC`（TOPIC 段就在这里）。

    用户 2026-11-16 说明：「TOPIC 的标志是情报段结束，顺序是主榜 10-4 ➡ 情报段 ➡ TOPIC 段
    ➡ 主榜 3 到 1」。所以这一段里的卡片就是 TOPIC，不靠名字 / 不靠颜色猜。
    读不到主榜 3、4 位（窗口为空）就原样返回，交给播放列表的「新音源」段兜底。
    """
    start = _frame_of(rows, TOPIC_WINDOW[0])
    end = _frame_of(rows, TOPIC_WINDOW[1])
    if start is None or end is None or end <= start:
        return list(rows or []), 0
    count = 0
    for row in rows or []:
        value = row.get(FRAME_KEY)
        if value in (None, ""):
            continue
        try:
            frame = float(value)
        except (TypeError, ValueError):
            continue
        if not start < frame < end:
            continue
        number = _rank_number(row)
        if number is not None and int(number) in (1, 2, 3):
            continue
        if str(row.get("角标") or "").strip():
            continue
        # TOPIC 曲算主榜（写在「榜单」那一节），模型若把它标成「新音源」这里要改回来，
        # 否则紧接着的 `drop_non_chart()` 会把它丢掉。
        if str(row.get("小节") or "").strip() not in (SECTION_MAIN, SECTION_NEW):
            continue
        row.pop("小节", None)
        row["角标"] = "TOPIC"
        count += 1
    return list(rows or []), count


# 角标兜底最多补问几批（69 批全问一遍太贵）
BADGE_RETRY_BUDGET = 8


def _apply_batch_section(rows: List[dict], section: str) -> List[dict]:
    """整批都在番外榜时，模型只要在顶层写一次 `section` 就够了。"""
    if not rows or section in (None, "", SECTION_MAIN):
        return rows
    for row in rows:
        row.setdefault("小节", section)
        if not row.get("小节"):
            row["小节"] = section
    return rows


def _needs_badge_pass(rows: Sequence[dict], main_limit: int) -> bool:
    """这一批有「超过前 N 名」的行、却一个角标都没读到 → 值得单独问一次底色。"""
    over = [row for row in rows or []
            if str(row.get("小节") or "").strip() == SECTION_MAIN
            and (_rank_number(row) or 0) > main_limit]
    if not over:
        return False
    return not any(str(row.get("角标") or "").strip() for row in rows or [])


def _apply_badges(rows: List[dict], badges: Dict[str, str]) -> List[dict]:
    """把「只看底色」那一问读到的角标按名次写回主榜的行。"""
    if not badges:
        return list(rows or [])
    for row in rows or []:
        if str(row.get("小节") or "").strip() != SECTION_MAIN:
            continue
        number = _rank_number(row)
        if number is None or number != int(number):
            continue
        badge = badges.get(str(int(number)))
        if badge and not str(row.get("角标") or "").strip():
            row["角标"] = badge
    return list(rows or [])


__all__ = ["recognize", "parse_reply", "parse_payload", "normalize_row", "merge", "sort_rows",
           "derive_weights", "build_prompt", "batches", "SYSTEM_PROMPT", "PROMPT",
           "RETRY_PROMPT", "BADGE_PROMPT", "COLUMN_ORDER", "missing_ranks", "expected_gaps",
           "drop_non_chart", "trim_main", "keep_pickup", "parse_label", "apply_label",
           "normalize_section", "normalize_badge", "NAME_OF_SECTION", "NON_CHART_SECTIONS",
           "SECTION_MAIN", "SECTION_EXTRA", "SECTION_HISTORY", "SECTION_NEW", "SECTION_OP_ED",
           "SECTION_ORDER", "BADGE_ALIASES", "FRAME_KEY", "TOPIC_WINDOW"]
