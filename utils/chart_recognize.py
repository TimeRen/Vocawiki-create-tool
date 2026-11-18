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
import difflib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from utils import ai_css, chart_ocr
from utils.chart_nico import ChartSpec, check_cancelled, core_title
from utils.chart_wikitext import BADGE_NAMES
from utils.string import title_match_key

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
   **曲名里的日文汉字要照抄日文写法**（`鳥の詩` 不要写成 `鸟の诗`、`櫻` 不要写成 `樱`），
   用户 2026-11-17 报过抄成简体后站上条目就对不上了。
9. 数字只要数字本身：不带单位、不加千分位、不要小数点。「得点」= ポイント / 合計。
10. 「收藏」是 マイリスト 数；「点赞」是 いいね 数（2023 年后的新版视频才有）；
    「上周」是卡片左下角的「先週／上期」那一行：写着 `上期：new!!`（或 `NEW`）就是新曲，
    「上周」写 null；写着 `上期：32位` 就写 32。「走势」不用管（程序 / 模板自己算箭头）。
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

# 补读：第一次就一行都没读到的帧，再用「点名」的方式问一遍。
# 用户 2026-11-17 的 #53：主榜第 1～5 名（在最前 / 最后那几批）与 TOPIC 段整段都落在空批次里，
# 数字与 `きるきるにしてあげる`（TOPIC 第 18 名）都因此丢了。
BACKFILL_PROMPT = """\
这些图是《{title}》（{label}第{index}期）周刊视频里的帧。

1. 画面里有没有印着 `T O P I C`（绿底）或 `P I C K U P`（粉底）的卡片？有就把卡片上的
   曲名、名次、得点、再生、评论、收藏、上期抄出来，「角标」写 TOPIC 或 PICKUP。
2. 顺便找这几首曲子的数据（曲名照画面原文，不看这几首也没关系）：
{wanted}

只输出 JSON：
{{"rows": [{{"曲名": "曲名原文", "本周": 3, "角标": "TOPIC", "得点": 1275, "再生": 535,
 "评论": 14, "收藏": 36, "上周": null}}]}}
这几首和 TOPIC / PICKUP 卡片都没出现就返回 {{"rows": []}}。看不清的字段写 null。"""

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
    "上期": "上周", "上一期": "上周", "上週": "上周", "先周": "上周",
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
# 小节：空字符串＝主榜，`番外`＝加场榜单
SECTION_MAIN = ""
SECTION_EXTRA = "番外"
# 下面两个常量只为兼容旧缓存 / 旧数据保留：HISTORY・OP・ED 现在用**角标**表示
# （用户 2026-11-17：《周刊UTAU排行榜54》里 HISTORY 就是 `|bottom-column = H I S T O R Y` 的普通行）。
SECTION_HISTORY = "history"
SECTION_NEW = "新音源"
SECTION_OP_ED = "op-ed"
SECTION_ORDER = (SECTION_MAIN, SECTION_EXTRA, SECTION_HISTORY, SECTION_NEW, SECTION_OP_ED)
# 只有「新音源」段不算榜单（HISTORY / OP / ED 现在要写进条目，见 `chart_playlist.label_of`）
NON_CHART_SECTIONS = (SECTION_NEW,)
SECTION_ALIASES = {
    "": SECTION_MAIN, "main": SECTION_MAIN, "主榜": SECTION_MAIN, "主榜单": SECTION_MAIN,
    "本期": SECTION_MAIN, "ランキング": SECTION_MAIN, "ranking": SECTION_MAIN,
    "番外": SECTION_EXTRA, "番外榜": SECTION_EXTRA, "番外編": SECTION_EXTRA,
    "utau番外榜": SECTION_EXTRA, "extra": SECTION_EXTRA, "bonus": SECTION_EXTRA,
    # HISTORY / OP / ED 用角标表示，所以画面里的小节一律归到主榜（顺序由 `sort_rows` 排）
    "history": SECTION_MAIN, "历史": SECTION_MAIN, "今週のhistory": SECTION_MAIN,
    "この曲のhistory": SECTION_MAIN, "往期回顾": SECTION_MAIN,
    "ヒストリー": SECTION_MAIN, "ヒストリー1位": SECTION_MAIN,
    "新音源": SECTION_NEW, "new": SECTION_NEW, "newsource": SECTION_NEW,
    "オープニング曲": SECTION_MAIN, "エンディング曲": SECTION_MAIN,
    "op": SECTION_MAIN, "ed": SECTION_MAIN, "オープニング": SECTION_MAIN,
    "エンディング": SECTION_MAIN,
}
# 角标只认名字（颜色由 `chart_wikitext.BADGES` 决定，绝不从颜色反推名字）
BADGE_ALIASES = {
    "pickup": "PICKUP", "p i c k u p": "PICKUP", "pick up": "PICKUP", "ピックアップ": "PICKUP",
    "ピックアップ曲": "PICKUP", "pickup曲": "PICKUP",
    "topic": "TOPIC", "t o p i c": "TOPIC", "トピック": "TOPIC", "topic曲": "TOPIC",
    "上周冠军": "上周冠军", "先週1位": "上周冠军", "前週1位": "上周冠军",
    "上周第一": "上周冠军", "lastweekno.1": "上周冠军",
    # 用户 2026-11-17：HISTORY / OP / ED 也要写进条目（#54 / #55 的写法）
    "history": "HISTORY", "h i s t o r y": "HISTORY", "ヒストリー": "HISTORY",
    "往期回顾": "HISTORY", "历史": "HISTORY",
    "op": "OP", "o p": "OP", "オープニング": "OP", "オープニング曲": "OP", "片头曲": "OP",
    "ed": "ED", "e d": "ED", "エンディング": "ED", "エンディング曲": "ED", "片尾曲": "ED",
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
    if "新音源" in lowered:
        return SECTION_NEW
    # HISTORY / OP / ED 现在用角标表示（见 BADGE_ALIASES），小节一律当主榜
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
            row.pop("角标", None)                 # 新音源段的角标没有意义
    if info.get("section") == SECTION_EXTRA and info.get("rank"):
        # 番外的名次以标签为准（卡片上写的就是「番外ランキングN位」）
        row["本周"] = info["rank"]
    if info.get("badge"):
        row["角标"] = info["badge"]
    if info.get("badge") == "HISTORY" and info.get("rank"):
        # 「ヒストリー2位」：名次用标签上的编号（HISTORY 自己有 1..N 的序号）
        row["本周"] = info["rank"]
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
    row = _clean_last_rank(row)
    return apply_label(row)


# 卡片左下角「上期：new!!」这类写法：新曲**不写** `|上周`（模板自己渲染彩色的 N E W ! !），
# 写了反而会变成灰色的「上期：new!!」，箭头那段 `#expr` 还会拿它比大小出错。
NEW_LAST_WORDS = ("new", "new!!", "new!", "初", "初登場", "初登场", "--", "---", "—", "圏外", "圈外",
                  "なし", "无")


def _clean_last_rank(row: dict) -> dict:
    """`上周` 里不是数字的写法 → 去掉；`32位` / `32 位` → `32`。"""
    value = str(row.get("上周") or "").strip()
    if not value:
        row.pop("上周", None)
        return row
    if value.lower().replace("！", "!").strip() in NEW_LAST_WORDS or not re.search(r"\d", value):
        row.pop("上周", None)
        return row
    digits = re.sub(r"[^\d]", "", value)
    rank = int(digits) if digits else 0
    if rank > 0:
        row["上周"] = str(rank)
    else:
        row.pop("上周", None)
    return row


def clean_last_rank(value: object) -> str:
    """`上期：new!!` → 空（新曲不写 `|上周`）；`32位` → `32`。"""
    return str(_clean_last_rank({"上周": value}).get("上周") or "")




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


def _group_of(row: dict) -> int:
    """条目里的先后：OP → 主榜 → HISTORY → ED → 番外（用户 2026-11-17，同 #54 / #55）。"""
    badge = str(row.get("角标") or "").strip().upper()
    rank = str(row.get("本周") or "").strip().upper()
    if badge == "OP" or rank == "OP":
        return 0
    if badge == "HISTORY":
        return 2
    if badge == "ED" or rank == "ED":
        return 3
    if str(row.get("小节") or "").strip() == SECTION_EXTRA:
        return 4
    return 1


def _order_in_group(row: dict) -> int:
    """主榜组内：主榜数字 → PICKUP → TOPIC → 其它。"""
    badge = str(row.get("角标") or "").strip().upper()
    if badge == "PICKUP":
        return 1
    if badge == "TOPIC":
        return 2
    if _rank_number(row) is not None:
        return 0
    return 3 if badge else 4


def sort_rows(rows: Sequence[dict]) -> List[dict]:
    """排成「OP → 主榜 → PICKUP → TOPIC → HISTORY → ED → 番外」。

    * 主榜 / PICKUP / 番外 / HISTORY 组内按名次升序，没有号码的行排到该组最后；
    * **TOPIC 组内按视频出现顺序**（`_序`，由 `chart_playlist.align()` 按播放列表顺序写入）——
      用户 2026-11-17 的要求：TOPIC 卡片上的数字（24 / 178 / 1 …）不是名次，按数字排会乱；
    * 以前「无号码在前」的规则让没读到号码的 PICKUP 曲跑到主榜第 1 位，现在它在组内排最后。
    """
    def key_of(row: dict) -> Tuple[int, int, float, int, str]:
        badge = str(row.get("角标") or "").strip().upper()
        order = _order_in_group(row)
        number = _rank_number(row)
        seq = int(str(row.get("_序") or "").strip() or 10 ** 6)
        if badge == "TOPIC":
            return (_group_of(row), order, float(seq), seq, "")
        if number is not None:
            return (_group_of(row), order, number, seq, "")
        return (_group_of(row), order, float(10 ** 6), seq, str(row.get("本周") or ""))

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
        badge = str(row.get("角标") or "").strip().upper()
        if badge in ("HISTORY", "OP", "ED"):
            continue                              # 这几段有自己的编号，不算主榜缺号
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
        badge = str(row.get("角标") or "").strip().upper()
        number = _rank_number(row)
        if badge in ("HISTORY", "OP", "ED"):
            kept.append(row)                      # HISTORY / OP / ED 不受「前 N 名」限制
            continue
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


def build_backfill_prompt(spec: ChartSpec, index: int, titles: Sequence[str]) -> str:
    """补读提问：TOPIC / PICKUP 卡片 + 点名的几首曲子。"""
    wanted = "\n".join(f"   - {title}" for title in titles if str(title or "").strip()) or "   - （无）"
    return BACKFILL_PROMPT.format(title=f"{spec.page_prefix} #{index}", label=spec.label,
                                  index=index, wanted=wanted)


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
              main_limit: int = 0, playlist: Optional[Sequence[dict]] = None,
              duration: float = 0.0, refetch=None, parallel: int = 1) -> List[dict]:
    """把抽出来的帧分批发给模型 → 合并后的名次列表（按小节+名次排序）。

    `progress(已做, 总数)` 用来更新界面上的状态文字；某一批失败就跳过那一批
    （只记日志），整体不抛异常 —— 识别不出来的时候用户还能在表格里手填。
    `parallel` 是同时问几批（`chart.ai_parallel`，默认 6）：每批互不依赖，串行跑就是
    「85～157 次 × 2.7 秒」，并行能把这段从 4～7 分钟压到 1～2 分钟。

    用户 2026-11-16 报过的几件事都在这里兜着：

    * **一行都没读到的批次要重试**：旧提示词里「片头片尾不抄」让模型把番外 / PICKUP /
      新音源 的卡片整段跳过了（实测 69 批里 11 批返回空，番外只认到一首）；
    * **角标读不到要单独问一次**：让模型在抄表时顺手判断粉/绿底不可靠，
      对「有超过前 N 名的行、但整批一个角标都没有」的批次再问一句「哪些行是粉/绿底」；
    * **TOPIC 按画面位置认**：周刊视频的段序是 `主榜 10→4 ➡ 情报段 ➡ TOPIC段 ➡ 主榜 3→1`，
      所以「主榜 4 位那一帧」与「主榜 3 位那一帧」之间的行就是 TOPIC（见 `_apply_topic_window`）；
    * **有官方播放列表时以它为准**：`playlist` 传进来就交给 `chart_playlist.align()`
      决定有哪些行、名次多少（画面只贡献数字），没有就沿用原来的合并 + 截断逻辑；
    * **先让本地 OCR 读一遍**（用户 2026-11-17 的建议）：卡片上的名次与
      `TOTAL / VIEW / COMMENT / MYLIST` 都是规整文本，本地引擎零成本就能读全；
      一整个批次的每一帧都被 OCR 读全了就不再调 AI，只有读不全的批次才走模型；
    * **时间轴缺口重采**：读不到卡片的那些帧连成「空档」，交给调用方按 0.2 秒重采
      （`refetch(起, 止)`）再读一遍 —— 卡片是按名次顺序播的，名次缺失就一定在空档里。
    """
    cfg = ai_css.settings()
    if not cfg.get("api_key"):
        raise RuntimeError("未配置ai_api_key（见wiki_credentials.yaml），无法识别榜单画面")
    if not frames:
        return []
    groups = batches(list(frames), batch)
    playlist_items = list(playlist or [])
    collected: List[dict] = []
    empty_batches = 0
    retried_rows = 0
    empty_groups: List[List[Tuple[float, bytes]]] = []
    badge_hits: Dict[str, str] = {}
    badge_asked = 0
    ocr_rows, ocr_covered = _ocr_rows(frames, playlist_items)
    if ocr_rows:
        logging.info("榜单读数：本地OCR先读了%d张卡片（%d帧）", len(ocr_rows), len(ocr_covered))
        collected.extend(ocr_rows)
    covered_groups: set = set()
    pending: List[Tuple[int, List[Tuple[float, bytes]]]] = []
    for number, group in enumerate(groups, start=1):
        if ocr_covered and all(_position_key(position) in ocr_covered for position, _data in group):
            covered_groups.add(number)                 # 这一批 OCR 已经读全，不用再问模型
            continue
        pending.append((number, list(group)))
    if progress:
        progress(len(covered_groups), len(groups))
    workers = max(1, int(parallel or 1))
    results = _run_batches(cfg, spec, index, extra, pending, len(groups), main_limit,
                           workers, progress, covered_groups)
    # 「这一批读到过东西」就把整批的每一帧都算作已看过（用户 2026-11-18 的 #61 实测）：
    # 以前只记每批**第一帧**，`ai_batch=4` 时 621 帧里有 3/4 被当成「没读到」，
    # 空档于是连成 0～98 秒、102～1202 秒这种巨型窗口，重采白烧 10 分钟。
    batch_covered: set = set()
    for number, row_list, badges, was_empty, retried, asked_badge in results:
        empty_batches += 1 if was_empty else 0
        retried_rows += retried
        badge_asked += 1 if asked_badge else 0
        badge_hits.update(badges)
        if was_empty:
            empty_groups.append(groups[number - 1])
        if not row_list:
            continue                                   # 重试后仍然 0 行 → 真·空档
        for row in row_list:
            # 这一批第一帧的位置（存成字符串：0.0 在 merge() 里会被当成「空值」）
            row.setdefault(FRAME_KEY, "%.3f" % float(groups[number - 1][0][0]))
        for position, _data in groups[number - 1]:
            batch_covered.add(_position_key(position))
        collected.extend(row_list)
    if progress and not pending:
        progress(len(groups), len(groups))
    if covered_groups:
        logging.info("榜单读数：%d/%d批由本地OCR直接读全，没调AI", len(covered_groups), len(groups))
        for number in covered_groups:
            for position, _data in groups[number - 1]:
                batch_covered.add(_position_key(position))
    if empty_batches:
        logging.info("榜单画面自查：%d/%d批第一次没读到数据，重试后补回%d行",
                     empty_batches, len(groups), retried_rows)
    if badge_asked:
        logging.info("角标自查：为%d批补问了底色，确认%d行的角标", badge_asked, len(badge_hits))
    if refetch is not None:
        resampled, resample_stat = _resample_gaps(cfg, spec, index, frames, collected,
                                                  refetch, playlist_items, batch, duration,
                                                  covered=batch_covered)
        if resampled:
            collected.extend(resampled)
        if resample_stat["windows"]:
            logging.info("榜单补采：重采了%d个时间空档（%.0f～%.0f秒），补回%d行",
                         resample_stat["windows"], resample_stat["seconds"],
                         resample_stat["first"], len(resampled))
    merged = merge(_apply_badges(collected, badge_hits))
    # 先按画面位置认 TOPIC，再丢「新音源 / History」：模型常把 TOPIC 段的卡片标成「新音源」，
    # 先丢的话它们就再也进不来了（用户 2026-11-16：「TOPIC 这几首都要加进条目里」）。
    merged, topic_count = _apply_topic_window(merged)
    if topic_count:
        logging.info("榜单：按画面位置认出%d首TOPIC曲", topic_count)
    merged = drop_non_chart(merged)
    if playlist_items:
        from utils import chart_playlist

        merged, _stat = chart_playlist.align(merged, playlist_items, main_limit)
    else:
        merged, dropped = trim_main(merged, main_limit)
        if dropped:
            logging.info("榜单：已忽略主榜第%d名之后的%d首（只收录前%d名 + PICKUP/TOPIC）",
                         main_limit + 1, dropped, main_limit)
    if empty_groups:
        merged, backfill = _backfill(cfg, spec, index, empty_groups, merged, playlist_items)
        if backfill["rows"] or backfill["added"]:
            logging.info("榜单补读：为%d首曲子补问了%d批，补回%d首的数字、新增%d首",
                         backfill["wanted"], backfill["asked"], backfill["rows"],
                         backfill["added"])
    merged = sort_rows(merged)
    for row in merged:
        row.pop(FRAME_KEY, None)
    _warn_rank_collisions(merged)
    if not merged:
        logging.warning("榜单识别没读到任何一行（%d批全部失败或画面里没有数据）", len(groups))
    else:
        for section, lacking in missing_ranks(merged, limit=main_limit).items():
            logging.warning("%s缺第%s名（可点「添加行」补）",
                            NAME_OF_SECTION.get(section, section or "主榜"),
                            "、".join(str(rank) for rank in lacking))
    return merged


def skeleton_rows(items: Sequence[dict], main_limit: int = 0) -> List[dict]:
    """只按官方播放列表摆好行（没有画面数字）→ 识别整段失败时的兜底。

    用户 2026-11-18：第 61 期在 `align()` 里抛 `ValueError`，整轮结果全丢、界面只弹
    「预期外的错误」。既然播放列表已经给了「有哪些曲子 / 名次 / 稿件编号」，
    就先把这些摆进表格（得点/再生等留空），让人能在「榜单」页手填，而不是白等十几分钟。
    """
    from utils import chart_playlist

    rows: List[dict] = []
    for index, item in enumerate(items or []):
        if not chart_playlist.wanted(item, main_limit):
            continue
        row: dict = {"_序": str(index),
                     "曲名": item.get("title") or item.get("raw_title") or "",
                     "id": str(item.get("video_id") or ""),
                     "raw_title": item.get("raw_title") or ""}
        number = chart_playlist.rank_number(item.get("rank"))
        if number is not None:
            row["本周"] = str(number)
        elif item.get("rank"):
            row["本周"] = str(item["rank"])            # OP / ED 这类非数字名次
        if item.get("section") == chart_playlist.EXTRA:
            row["小节"] = chart_playlist.EXTRA
        if item.get("badge"):
            row["角标"] = str(item["badge"])
        elif item.get("candidate") == chart_playlist.TOPIC:
            row["角标"] = chart_playlist.TOPIC
            row["备注"] = UNREAD_RANK_NOTE
        if item.get("vocalist"):
            row["歌姬"] = item["vocalist"]
        rows.append(row)
    return sort_rows(rows)


def _run_batches(cfg: dict, spec: ChartSpec, index: int, extra: str,
                 pending: Sequence[Tuple[int, List[Tuple[float, bytes]]]],
                 total_groups: int, main_limit: int, workers: int,
                 progress=None, covered_groups: Optional[set] = None
                 ) -> List[Tuple[int, List[dict], Dict[str, str], bool, int, bool]]:
    """把待识别的批次跑完（`workers > 1` 时并行）→ 按批序号排好的结果。

    每批都是互不依赖的「几张图 + 一句提示词」，串行跑就是「85～157 次 × 2.7 秒」
    （用户 2026-11-18 问「为什么这么久」查出来的主因之一）。这里用线程池并发，
    进度用锁包住，保证百分比只往上走。

    返回值每项：`(批序号, 行列表, 角标表, 是否第一次读空, 重试补回几行, 是否补问了底色)`。
    """
    if not pending:
        return []
    counter = [len(covered_groups or ())]
    lock = threading.Lock()
    badge_state = {"asked": 0, "hits": 0, "lock": lock}

    def tick() -> None:
        if progress is None:
            return
        with lock:
            progress(counter[0], total_groups)

    def work(item: Tuple[int, List[Tuple[float, bytes]]]):
        number, group = item
        check_cancelled()
        try:
            rows, badges, empty, retried, asked = _recognize_group(
                cfg, spec, index, extra, group, main_limit, badge_state)
            logging.info("榜单识别第%d/%d批：读到%d行", number, total_groups, len(rows))
            return number, rows, badges, empty, retried, asked
        finally:
            with lock:
                counter[0] += 1
            tick()

    if workers <= 1:
        return [work(item) for item in pending]

    logging.info("榜单识别：%d批待识别，%d路并行", len(pending), workers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, pending))
    results.sort(key=lambda entry: entry[0])
    return results


def _recognize_group(cfg: dict, spec: ChartSpec, index: int, extra: str,
                     group: Sequence[Tuple[float, bytes]], main_limit: int,
                     badge_state: Optional[dict] = None
                     ) -> Tuple[List[dict], Dict[str, str], bool, int, bool]:
    """问一批图 → `(行, 角标, 是否读空, 重试补回几行, 是否补问了底色)`。

    「第一次读空就换宽松提示词再问一次」「角标一个都没有就补问底色」这两条兜底
    以前写在 `recognize()` 的循环里，搬进来之后串行与并行走的是同一套逻辑。
    """
    images = _images_for(group)
    if not images:
        return [], {}, False, 0, False
    prompt = build_prompt(spec, index, len(group), group[0][0], group[-1][0], extra)
    payload = _ask(cfg, prompt, images)
    rows: List[dict] = []
    badges: Dict[str, str] = {}
    if payload is not None:
        rows = _apply_batch_section(payload["rows"], payload["section"])
        badges.update(payload["badges"])
    empty = not rows
    retried = 0
    if empty:
        # 一行都没抄到：可能是被「片头片尾不抄」误伤了，宽松地再问一次
        payload = _ask(cfg, build_retry_prompt(spec, index, extra), images)
        if payload is not None:
            rows = _apply_batch_section(payload["rows"], payload["section"])
            badges.update(payload["badges"])
            retried = len(rows)
    asked_badge = False
    if main_limit and _needs_badge_pass(rows, main_limit) and _badge_allowed(badge_state):
        asked_badge = True
        payload = _ask(cfg, build_badge_prompt(spec, index), images)
        hits = bool(payload is not None and payload["badges"])
        if hits:
            badges.update(payload["badges"])
        _badge_record(badge_state, hits)
    return rows, badges, empty, retried, asked_badge


def _badge_allowed(state: Optional[dict]) -> bool:
    """还该不该为这一批补问底色（总共 `BADGE_RETRY_BUDGET` 次；连着白问就提前收手）。"""
    if not state:
        return True
    with state["lock"]:
        asked, hits = state["asked"], state["hits"]
    if asked >= BADGE_RETRY_BUDGET:
        return False
    return not (hits == 0 and asked >= BADGE_GIVE_UP_AFTER)


def _badge_record(state: Optional[dict], hits: bool) -> None:
    if not state:
        return
    with state["lock"]:
        state["asked"] += 1
        state["hits"] += 1 if hits else 0


# 行里临时记的「出现在第几帧」（只在识别内部用，返回前删掉，免得流进 wikitext）
FRAME_KEY = "_帧"
# 本地 OCR 的预算：一张卡片 ~2 秒，一期 600 帧全读要 20 分钟（比 AI 还慢），
# 所以只均匀挑这么多帧、最多占这么多秒；剩下的交给并行的 AI。
OCR_FRAME_BUDGET = 120
OCR_DEADLINE = 60.0
OCR_WORKERS = 2
# TOPIC 段的段序：主榜 10→4、情报段、TOPIC 段、主榜 3→1（用户 2026-11-16 给的）
TOPIC_WINDOW = (4, 3)
# 补读最多再问几批（本期实测空 22 批；补齐就提前结束）
BACKFILL_BUDGET = 24
# 补读只并这些「数字 + 上周 + 角标」，名次 / id / 曲名一律不动
BACKFILL_FIELDS = ("得点", "再生", "评论", "收藏", "点赞", "上周", "角标")
# 补读点名列表的长度上限（以前写死 8，候选排在主榜行后面就一直轮不到）
BACKFILL_TITLES = 12
# 时间轴缺口重采：最短多久算「空档」、单个窗口多长封顶、最多重采几个窗口
GAP_SECONDS = 4.0
GAP_MAX_SECONDS = 60.0
RESAMPLE_BUDGET = 6
# 重采的总预算（视频秒数 / OCR 秒数），以及「连续几次请求失败就不干了」
# 用户 2026-11-18 的 #61：5 个窗口各 60 秒 OCR = 300 秒，代理抖动又让每条请求等到超时。
RESAMPLE_TOTAL_SECONDS = 180.0
RESAMPLE_OCR_DEADLINE = 45.0
RESAMPLE_OCR_FRAMES = 16
RESAMPLE_MAX_FAILURES = 3
# JPEG 文件头：只有真图片才送给 OCR（测试里的假字节直接跳过）
JPEG_MAGIC = b"\xff\xd8"
# 候选曲名次读不到时写进「备注」的那句（补到名次后要抹掉）
UNREAD_RANK_NOTE = "TOPIC名次没读到"


def _position_key(position: object) -> int:
    """帧位置的比较键（浮点别直接用 ==）。"""
    try:
        return int(round(float(position) * 10000))
    except (TypeError, ValueError):
        return -1


def _looks_like_image(data: object) -> bool:
    return isinstance(data, (bytes, bytearray)) and bytes(data[:2]) == JPEG_MAGIC


def _items_by_rank(items: Sequence[dict]) -> Dict[Tuple[str, int], dict]:
    """播放列表 → `{(小节, 名次): 条目}`（OCR 靠名次就能找到曲名）。

    主榜与番外榜各有「1 位」，同一个数字对上两首曲子时**不猜**（这张表里去掉那个名次，
    调用方改用曲名去配对），免得把番外卡片的数字并进主榜同名次那一行。
    """
    table: Dict[Tuple[str, int], dict] = {}
    counts: Dict[int, int] = {}
    for item in items or []:
        rank = item.get("rank")
        if not rank:
            continue
        try:
            number = int(rank)
        except (TypeError, ValueError):
            continue
        key = (str(item.get("section") or "").strip(), number)
        counts[number] = counts.get(number, 0) + 1
        table.setdefault(key, item)
    return {key: item for key, item in table.items() if counts.get(key[1], 0) == 1}


def _ocr_rows(frames: Sequence[Tuple[float, bytes]], items: Sequence[dict],
              budget: int = 0, deadline: float = 0.0, workers: int = 0
              ) -> Tuple[List[dict], set]:
    """本地 OCR 过一遍帧 → `(能成行的卡片, 已读全的帧位置键)`。

    卡片上的名次 + `TOTAL / VIEW / COMMENT / MYLIST` 是规整文本，本地引擎读得又快又准；
    曲名不用 OCR 认，直接按名次去播放列表里查（TOPIC 候选才用 OCR 文本跟候选标题配对）。

    **必须限时限量**（2026-11-18 实测）：一张 480×360 的卡片在这台机器上要 2.1 秒，
    而一期视频有 600 多帧 —— 全读一遍要 22 分钟，比 AI 还慢。所以只挑 `budget` 帧
    **均匀铺满整条时间轴**（漏掉的卡片后面还有 AI 兜底），多线程跑，并且最多用
    `deadline` 秒：到点就放弃还没读完的帧，绝不拖住整条流程。
    """
    from utils import chart_playlist

    rows: List[dict] = []
    covered: set = set()
    if not frames or not any(_looks_like_image(data) for _position, data in frames):
        return rows, covered
    if not chart_ocr.available():
        return rows, covered
    picks = _spread_frames(frames, budget or OCR_FRAME_BUDGET)
    if not picks:
        return rows, covered
    limit = float(deadline or OCR_DEADLINE)
    started = time.time()
    tried = 0
    failed = 0
    first_error = ""
    pool = ThreadPoolExecutor(max_workers=max(1, int(workers or OCR_WORKERS)))
    try:
        futures = [(position, pool.submit(chart_ocr.read_card_lines, data))
                   for position, data in picks]
        by_rank = _items_by_rank(items)
        candidates = chart_playlist.topic_candidates(items)
        for position, future in futures:
            left = limit - (time.time() - started)
            if left <= 0:
                logging.info("本地OCR到时间上限（%.0f秒），剩下的帧不再读", limit)
                break
            tried += 1
            try:
                card, lines = future.result(timeout=left)
            except Exception as error:               # noqa: BLE001 - 读不出来的帧跳过
                failed += 1
                first_error = first_error or f"{type(error).__name__}: {error}"
                continue
            if not chart_ocr.is_complete(card):
                continue
            row: dict = {name: card[name] for name in ("得点", "再生", "评论", "收藏", "上周")
                         if card.get(name)}
            row["本周"] = card["本周"]
            row[FRAME_KEY] = "%.3f" % float(position)
            rank = int(card["本周"])
            item = by_rank.get((SECTION_MAIN, rank)) or by_rank.get((SECTION_EXTRA, rank))
            if item is None:
                # 名次有歧义（主榜/番外都有 1 位）或这张是 TOPIC 候选：用 OCR 文本去配曲名
                names = [line for line in lines if line]
                item = chart_playlist.match_item(names, candidates or []) \
                    or chart_playlist.match_item(names, items)
                if item is not None and item.get("candidate") == TOPIC:
                    row["角标"] = TOPIC
            if item is None:
                continue                             # 认不出是哪首曲子的卡片先不用
            row["曲名"] = str(item.get("title") or item.get("raw_title") or "")
            rows.append(row)
            covered.add(_position_key(position))
    finally:
        pool.shutdown(wait=False)
    elapsed = time.time() - started
    if failed:
        logging.warning("本地OCR：%d/%d帧读取出错（例：%s）", failed, tried, first_error)
    logging.info("本地OCR：尝试%d帧 / 成行%d张 / 用时%.1f秒", tried, len(rows), elapsed)
    return rows, covered


def _spread_frames(frames: Sequence[Tuple[float, bytes]], budget: int
                   ) -> List[Tuple[float, bytes]]:
    """从整条时间轴上**均匀**挑 `budget` 帧（首帧与末帧一定入选，中间等距）。"""
    total = len(frames)
    limit = max(1, int(budget or 1))
    if total <= limit:
        return list(frames)
    if limit == 1:
        return [frames[0]]
    step = (total - 1) / float(limit - 1)
    return [frames[min(total - 1, int(round(index * step)))] for index in range(limit)]


def _gap_windows(positions: Sequence[float], covered: set, duration: float,
                 budget: int = 0, max_seconds: Optional[float] = None
                 ) -> List[Tuple[float, float]]:
    """没读到卡片的帧连成时间空档 → `[(起, 止), …]`（0~1 位置，已按预算截断）。

    * 单个空档超过 `GAP_MAX_SECONDS`（60 秒）就**切成几段**：连续一分钟都没有卡片，
      那是情报段 / 片头，整段按 0.2 秒重采只是白花时间（实测 1100 秒的窗口）；
    * `max_seconds` 是本次重采的**视频秒数总预算**（默认 `RESAMPLE_TOTAL_SECONDS`），
      按窗口顺序一路扣，扣完就停。
    """
    if not positions:
        return []
    ordered = sorted(float(position) for position in positions)
    spans: List[List[float]] = []
    current: List[float] = []
    for position in ordered:
        if _position_key(position) in covered:
            if current:
                spans.append(current)
                current = []
            continue
        if current and duration and (position - current[-1]) * duration > GAP_MAX_SECONDS:
            spans.append(current)
            current = []
        current.append(position)
    if current:
        spans.append(current)
    windows: List[Tuple[float, float]] = []
    pad = (2.0 / duration) if duration else 0.01
    step = (GAP_MAX_SECONDS / duration) if duration else 1.0
    for span in spans:
        start, end = max(0.0, span[0] - pad), min(1.0, span[-1] + pad)
        # 长空档切片：每片最多 GAP_MAX_SECONDS，片数受 budget 限制
        while start < end:
            piece = min(end, start + step) if step > 0 else end
            if duration and (piece - start) * duration < GAP_SECONDS:
                break
            windows.append((start, piece))
            start = piece
    limit = max(0, int(budget or RESAMPLE_BUDGET))
    total_cap = RESAMPLE_TOTAL_SECONDS if max_seconds is None else float(max_seconds)
    if duration and total_cap:
        kept: List[Tuple[float, float]] = []
        total = 0.0
        for item in windows:
            total += (item[1] - item[0]) * duration
            if total > total_cap:
                break
            kept.append(item)
        windows = kept
    return windows[:limit] if limit else windows


def _resample_gaps(cfg: dict, spec: ChartSpec, index: int,
                   frames: Sequence[Tuple[float, bytes]], rows: Sequence[dict],
                   refetch, items: Sequence[dict], batch: int,
                   duration: float, covered: Optional[set] = None
                   ) -> Tuple[List[dict], dict]:
    """把「一帧都没读到卡片」的时间空档重采一遍再读（用户 2026-11-17 的要求）。

    用户报的「主榜 1～5 名没有数据」「TOPIC 少两首」都是同一类问题：卡片停留时间比采样间隔短，
    根本没被采到。名片是按名次顺序播的，所以**没读到 = 时间轴上的空档**；空档重采后先本地
    OCR，读不全的那几帧再问模型。

    用户 2026-11-18 的 #61 实测（12.5 分钟里 11 分钟耗在这里）之后加了三条闸门：

    * `covered` 由调用方给（**整批**都算看过，而不是只算每批第一帧），空档不再虚胖；
    * 所有窗口**共用** `RESAMPLE_OCR_DEADLINE` 秒的 OCR 上限（以前每个窗口各 60 秒 → 5×60s）；
    * 代理连续报错 `RESAMPLE_MAX_FAILURES` 次就收手（这次 10053/10054 抖动让每条都等到超时）。
    """
    stat = {"windows": 0, "first": 0.0, "seconds": 0.0}
    if not frames or refetch is None:
        return [], stat
    positions = [float(position) for position, _data in frames]
    seen = set(covered or ())
    if not seen:
        seen = {_position_key(row.get(FRAME_KEY)) for row in rows or [] if row.get(FRAME_KEY)}
    windows = _gap_windows(positions, seen, duration)
    if not windows:
        return [], stat
    extra: List[dict] = []
    ocr_deadline = time.time() + float(RESAMPLE_OCR_DEADLINE)
    failures = 0
    for start, end in windows:
        check_cancelled()
        if failures >= RESAMPLE_MAX_FAILURES:
            logging.info("榜单补采：连续%d次请求失败，剩下的空档跳过", failures)
            break
        try:
            fresh = refetch(start, end) or []
        except Exception as error:                   # noqa: BLE001 - 重采失败不该中断整轮
            logging.debug("重采%.3f～%.3f失败：%s", start, end, error)
            continue
        stat["windows"] = int(stat["windows"]) + 1
        stat["seconds"] = float(stat["seconds"]) + (end - start) * (duration or 0.0)
        left = ocr_deadline - time.time()
        ocr_rows: List[dict] = []
        ocr_covered: set = set()
        if left > 0:
            ocr_rows, ocr_covered = _ocr_rows(fresh, items, budget=RESAMPLE_OCR_FRAMES,
                                              deadline=left)
        if ocr_rows:
            stat["first"] = float(stat["first"]) + len(ocr_rows)
            extra.extend(ocr_rows)
        rest = [(position, data) for position, data in fresh
                if _position_key(position) not in ocr_covered]
        for group in batches(rest, batch):
            images = _images_for(group)
            if not images:
                continue
            prompt = build_prompt(spec, index, len(group), group[0][0], group[-1][0], "")
            payload = _ask(cfg, prompt, images)
            if not payload:
                failures += 1
                if failures >= RESAMPLE_MAX_FAILURES:
                    break
                continue
            failures = 0
            rows_here = _apply_batch_section(payload["rows"], payload["section"])
            for row in rows_here:
                row.setdefault(FRAME_KEY, "%.3f" % float(group[0][0]))
            extra.extend(rows_here)
    return extra, stat


def _needs_numbers(row: dict) -> bool:
    """这一行是不是「有曲名却一个数字都没有」（补读要找的就是它）。"""
    return not str(row.get("得点") or "").strip() and not str(row.get("再生") or "").strip()


def _match_row(rows: Sequence[dict], title: str) -> Optional[dict]:
    """按曲名在现有行里找最像的一行（补读并数字用）。"""
    key = title_match_key(core_title(str(title or "")))
    if not key:
        return None
    best: Optional[dict] = None
    best_score = 0.0
    for row in rows or []:
        if str(row.get("小节") or "").strip() not in (SECTION_MAIN, SECTION_EXTRA):
            continue
        other = title_match_key(core_title(str(row.get("曲名") or "")))
        if not other:
            continue
        score = 1.0 if other == key else (
            min(len(key), len(other)) / max(len(key), len(other)) if key in other or other in key
            else difflib.SequenceMatcher(None, key, other).ratio())
        if score > best_score:
            best, best_score = row, score
    from utils.chart_playlist import MATCH_MIN as playlist_min

    return best if best is not None and best_score >= playlist_min else None


def _merge_backfill(row: dict, raw: dict) -> bool:
    """把补读到的数字并进这一行（只补空着的）→ 有没有补到东西。"""
    changed = False
    for name in BACKFILL_FIELDS:
        value = str(raw.get(name) or "").strip()
        if not value or str(row.get(name) or "").strip():
            continue
        if name == "角标" and value not in ("TOPIC", "PICKUP"):
            continue
        row[name] = value
        changed = True
    if not str(row.get("本周") or "").strip():
        # TOPIC 候选在播放列表里没有名次，只能靠画面补（用户 2026-11-17：きるきる是第 18 名）
        digits = re.sub(r"[^\d]", "", str(raw.get("本周") or ""))
        if digits:
            row["本周"] = digits
            note = str(row.get("备注") or "").strip()
            if note.startswith(UNREAD_RANK_NOTE):
                row.pop("备注", None)
            elif UNREAD_RANK_NOTE in note:
                row["备注"] = note.replace(UNREAD_RANK_NOTE, "").strip("；; ")
            changed = True
    return changed


def _missing_topics(rows: Sequence[dict], items: Sequence[dict]) -> List[dict]:
    """播放列表「新音源」段里还没出现在表格中的条目（＝还没读到的 TOPIC 曲）。"""
    if not items:
        return []
    from utils import chart_playlist

    listed = [str(row.get("id") or "").strip() for row in rows or []]
    names = [str(row.get("曲名") or "") for row in rows or []]
    missing = []
    for item in chart_playlist.topic_candidates(items):
        if str(item.get("video_id") or "") in listed:
            continue
        title = str(item.get("title") or item.get("raw_title") or "")
        if any(title_match_key(core_title(title)) == title_match_key(core_title(name))
               for name in names if name):
            continue
        missing.append(item)
    return missing


def _backfill(cfg: dict, spec: ChartSpec, index: int, groups: Sequence[Sequence],
              rows: List[dict], items: Sequence[dict]) -> Tuple[List[dict], dict]:
    """对「第一次就一行没读到」的帧再问一遍 → 补数字、补 TOPIC 曲（用户 2026-11-17 的要求）。

    #53 的实测：主榜第 1～5 名（在最前 / 最后几批）和 TOPIC 段整段都落在空批次里，
    `きるきるにしてあげる`（TOPIC 第 18 名）与 1～5 名的 得点/再生/上周 全丢了。
    这里用「点名」的提示词把它们问回来，只并数字与角标，不动名次（新增的 TOPIC 行除外）与 id。
    """
    from utils import chart_playlist

    stat = {"wanted": 0, "asked": 0, "rows": 0, "added": 0}
    todo = [row for row in rows if _needs_numbers(row)]
    missing_topics = _missing_topics(rows, items)
    if not todo and not missing_topics:
        return rows, stat
    stat["wanted"] = len(todo)
    filled: set = set()
    added_names: set = set()
    for group in list(groups)[:BACKFILL_BUDGET]:
        if not [row for row in todo if id(row) not in filled] and not missing_topics:
            break
        check_cancelled()
        images = _images_for(group)
        if not images:
            continue
        titles = [str(item.get("title") or item.get("raw_title") or "") for item in missing_topics]
        titles += [str(row.get("曲名") or "") for row in todo if id(row) not in filled]
        wanted = [title for title in titles if str(title or "").strip()][:BACKFILL_TITLES]
        stat["asked"] += 1
        payload = _ask(cfg, build_backfill_prompt(spec, index, wanted), images)
        if not payload:
            continue
        for raw in payload["rows"]:
            title = str(raw.get("曲名") or "")
            target = _match_row(rows, title)
            if target is not None:
                if _merge_backfill(target, raw):
                    filled.add(id(target))
                continue
            if str(raw.get("角标") or "").strip() != "TOPIC" or not title:
                continue
            key = title_match_key(core_title(title))
            if key in added_names:
                continue
            item = chart_playlist.match_item([title], missing_topics) \
                or chart_playlist.match_item([title], items)
            row = {name: str(raw.get(name) or "").strip() for name in BACKFILL_FIELDS}
            row = {name: value for name, value in row.items() if value}
            row["曲名"] = str((item or {}).get("title") or title)
            row["角标"] = "TOPIC"
            if item and item.get("video_id"):
                row["id"] = str(item["video_id"])
            if "本周" in raw or str(raw.get("本周") or "").strip():
                row["本周"] = str(raw.get("本周")).strip()
            rows.append(row)
            added_names.add(key)
            stat["added"] += 1
            if item is not None and item in missing_topics:
                missing_topics.remove(item)
    stat["rows"] = len(filled)
    return rows, stat


def _warn_rank_collisions(rows: Sequence[dict]) -> None:
    """同一个名次上出现两首曲子时，在「备注」里标出来（不静默覆盖任何一方）。

    HISTORY / OP / ED 各有自己的 1、2、3…（`本周 = OP` 这类不算数字），
    它们跟主榜的 1 位撞号是**正常的**，不提示。
    """
    seen: Dict[Tuple[str, str], dict] = {}
    for row in rows or []:
        if str(row.get("角标") or "").strip().upper() in ("HISTORY", "OP", "ED"):
            continue
        number = _rank_number(row)
        if number is None:
            continue
        key = (str(row.get("小节") or "").strip(), str(int(number)))
        first = seen.get(key)
        if first is None:
            seen[key] = row
            continue
        note = "与另一首撞号，请核对名次"
        for target in (first, row):
            existing = str(target.get("备注") or "").strip()
            target["备注"] = f"{existing}；{note}" if existing else note
        logging.warning("榜单：第%s名有两首曲子（%s / %s），请在表格里核对",
                        key[1], first.get("曲名"), row.get("曲名"))


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
# 连着这么多批「补问底色」都没读到角标就停止再问（用户 2026-11-18 的 #61：
# 29 批全白问、确认 0 行的角标，白花十几秒）
BADGE_GIVE_UP_AFTER = 5


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


__all__ = ["recognize", "skeleton_rows", "parse_reply", "parse_payload", "normalize_row",
           "merge", "sort_rows",
           "derive_weights", "build_prompt", "batches", "SYSTEM_PROMPT", "PROMPT",
           "RETRY_PROMPT", "BADGE_PROMPT", "COLUMN_ORDER", "missing_ranks", "expected_gaps",
           "drop_non_chart", "trim_main", "keep_pickup", "parse_label", "apply_label",
           "normalize_section", "normalize_badge", "NAME_OF_SECTION", "NON_CHART_SECTIONS",
           "SECTION_MAIN", "SECTION_EXTRA", "SECTION_HISTORY", "SECTION_NEW", "SECTION_OP_ED",
           "SECTION_ORDER", "BADGE_ALIASES", "FRAME_KEY", "TOPIC_WINDOW", "BACKFILL_BUDGET",
           "BACKFILL_PROMPT", "build_backfill_prompt", "clean_last_rank"]
