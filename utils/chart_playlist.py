"""官方播放列表页（`blog-entry-N.html`）→ 这一期到底有哪些行、编号是多少。

用户 2026-11-16 的两轮反馈把口径定死了：

* 周刊视频的简介里就写着这一期的播放列表页（`…blog-entry-452.html`），那一页逐条列着
  「曲名 + niconico 链接 + 段名/名次」，所以**行、名次、番外编号、PICKUP、稿件编号都以它为准**，
  画面 OCR 只负责抄 `得点 / 再生 / 评论 / 收藏` 这些当周数字；
* 那一页的标签写在条目**后面**（`<iframe …>标题</a></iframe>　番外ランキング3位`），不是前面 ——
  一开始按「标签在前」读，把 `かえりみち`（ピックアップ曲）读成了 21 位、把
  `GO MY WAY!! 改`（番外 1 位）读成了主榜 1 位，用户当场纠正过。这个读法用 #52 验证过：
  那页第一条「【UTAU連続音】Lilium」标 `1位`，站上《周刊UTAU排行榜52》的 1 位正是 Lilium
  （sm8673630）；
* `ピックアップ曲` → `角标 = PICKUP`（一定进表格）；`新音源` 段只是 TOPIC 的**候选**
  （是不是 TOPIC 由画面位置决定，见 `chart_recognize` 的段序说明），只有画面里读到了才收录；
  `番外ランキングN位` → `小节 = 番外`、`本周 = N`；`ヒストリーN位` / `オープニング曲` /
  `エンディング曲` 不属于本期榜单，丢弃。

这一层只做「解析 + 对齐」，不生成 wikitext。
"""
import difflib
import html as html_module
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

from utils.chart_nico import clean_song_title, core_title, vocalists_from_title
from utils.helpers import http_get
from utils.string import title_match_key

REQUEST_TIMEOUT = 30
# 简介里的播放列表页地址（FC2 博客）：`…/blog-entry-452.html`
PLAYLIST_URL_RE = re.compile(r"https?://[\w.\-]*fc2\.com/blog-entry-\d+\.html")
# 条目 = 一个指向 niconico 的 <a>（`【ニコニコ動画】` 是播放器缩略图带的固定前缀）
ANCHOR_RE = re.compile(r'<a[^>]*href="[^"]*nicovideo\.jp/watch/((?:sm|nm|so)\d+)"[^>]*>(.*?)</a>',
                       re.S)
ANCHOR_TITLE_PREFIX = "【ニコニコ動画】"
# 条目后面那 160 个字里找那行小字
TAIL_LEN = 160
# 少于这么多条就当没解析出来（正常一期三十条以上）
MIN_ITEMS = 8
# 播放列表标题与画面 OCR 标题对得上就算同一条（生僻字 / 截断 / 括号噪声都能过）
MATCH_MIN = 0.6

MAIN = ""
EXTRA = "番外"
PICKUP = "PICKUP"
TOPIC = "TOPIC"
HISTORY = "HISTORY"
OP = "OP"
ED = "ED"
DROP = "drop"
# 名次不是数字的角标（`本周 = OP` / `ED` / `HISTORY`）：它们不参与「只收录前 N 名」的比较
SEQUENCE_LABELS = (OP, ED, HISTORY)


def rank_number(value: object) -> Optional[int]:
    """`本周` / 播放列表名次 → 整数名次；`OP` / `ED` / 空 这类返回 None。

    用户 2026-11-18 报「第 61 期生成不了」查出来的：`label_of()` 把 `オープニング曲`
    标成 `rank='OP'`，而 `wanted()` 里还写着 `int(rank)` → `ValueError: invalid literal
    for int() with base 10: 'OP'`，整轮识别结果全丢。#53 的播放列表页没有 OP/ED 标签，
    所以这个坑一直到 #61 才踩到。凡是要把名次变数字的地方都走这里。
    """
    digits = re.sub(r"[^\d]", "", str(value if value is not None else ""))
    return int(digits) if digits else None


def playlist_url(description: str) -> str:
    """视频简介 → 播放列表页地址（没有就返回空字符串）。"""
    match = PLAYLIST_URL_RE.search(str(description or ""))
    return match.group(0) if match else ""


def label_of(text: str) -> Dict[str, object]:
    """一段小字（`番外ランキング3位` / `ピックアップ曲` / `新音源` / `ヒストリー2位`）→ 段 / 名次 / 角标。

    用户 2026-11-17（blog-entry-460 + 《周刊UTAU排行榜54》/`55`）：
    `ヒストリーN` → 角标 HISTORY、名次 N；`オープニング曲` → 角标 OP、名次 `OP`；
    `エンディング曲` → 角标 ED、名次 `ED`；只有 `CM` / 告知 才丢掉。
    """
    plain = html_module.unescape(str(text or "")).strip()
    if not plain:
        return {}
    if "番外" in plain:
        match = re.search(r"(\d{1,3})\s*位", plain)
        return {"section": EXTRA, "rank": int(match.group(1)) if match else None}
    if "ヒストリー" in plain or "history" in plain.lower():
        match = re.search(r"(\d{1,3})\s*位", plain)
        return {"section": MAIN, "badge": HISTORY,
                "rank": int(match.group(1)) if match else None}
    if "オープニング" in plain or re.search(r"\bOP\b", plain, re.I):
        return {"section": MAIN, "badge": OP, "rank": OP}
    if "エンディング" in plain or re.search(r"\bED\b", plain, re.I):
        return {"section": MAIN, "badge": ED, "rank": ED}
    if "ピックアップ" in plain:
        return {"section": MAIN, "badge": PICKUP}
    if "トピック" in plain:
        return {"section": MAIN, "badge": TOPIC}
    if "新音源" in plain:
        return {"section": MAIN, "candidate": TOPIC}
    if "CM" in plain.upper() or "告知" in plain:
        return {"section": DROP}
    match = re.match(r"^(\d{1,3})\s*位$", plain)
    if match:
        return {"section": MAIN, "rank": int(match.group(1))}
    return {}


def _new_item(video_id: str, title: str) -> dict:
    raw = title[len(ANCHOR_TITLE_PREFIX):] if title.startswith(ANCHOR_TITLE_PREFIX) else title
    return {"video_id": video_id, "raw_title": raw,
            "title": clean_song_title(raw) or raw,
            "vocalist": vocalists_from_title(raw),
            "section": MAIN, "rank": None, "badge": "", "candidate": ""}


# 标题里明显是「分类」而不是曲名的括号内容
CATEGORY_WORDS = ("utau", "オリジナル", "カバー", "歌ってみた", "short", "新音源", "アレンジ",
                  "連続音", "音源", "トレス", "歌わせて", "デュエット")
# `【新音源】<歌姬>【<曲名>】【分类】` —— 「新音源」条目常见的写法
CANDIDATE_TITLE_RE = re.compile(
    r"^【\s*新音源\s*】\s*(?P<lead>[^【】]{1,8}?)\s*【(?P<inner>[^【】]{2,40})】"
    r"(?P<tail>(?:【[^【】]*】)*)$")
# 前导文字里出现这些就不当作歌姬（括号 / 空格 / 斜杠说明是「歌姬／曲名」这种复合写法）
LEAD_REJECT_CHARS = "（）()「」『』/／・,、 　"


def _is_category_word(text: str) -> bool:
    lower = str(text or "").strip().lower()
    return any(word in lower for word in CATEGORY_WORDS)


def split_candidate_title(raw: str) -> Tuple[str, str]:
    """`【新音源】憑歌ルク【中二病賛歌】【UTAU】` → `("中二病賛歌", "憑歌ルク")`。

    用户 2026-11-17：这首歌的曲名是「中二病賛歌」，「憑歌ルク」是歌姬，
    以前把整串括号一删就只剩歌姬当曲名了。只在**新音源**条目、前导文字短且干净
    （不含括号/空格/斜杠）、括号内容不是分类词时才拆 —— `今もあなたのために(オリジナル)【阿井植男】`
    这种（前导带括号）与 `綿歌チトセ ／ Fire◎Flower 【UTAUカバー】`（前导带空格）都不动。
    """
    text = str(raw or "").strip()
    if not text.startswith("【新音源】") and not re.match(r"^【\s*新音源\s*】", text):
        return "", ""
    match = CANDIDATE_TITLE_RE.match(text)
    if not match:
        return "", ""
    lead, inner = match.group("lead").strip(), match.group("inner").strip()
    if not lead or not inner:
        return "", ""
    if any(char in lead for char in LEAD_REJECT_CHARS):
        return "", ""
    if _is_category_word(lead) or _is_category_word(inner):
        return "", ""
    return inner, lead


def _tail_label(text: str, start: int) -> Dict[str, object]:
    """锚点后面那段（`</iframe>　番外ランキング3位<br>`）里的那行小字 → 段 / 名次 / 角标。

    标签夹在「`</iframe>`」和下一个标签之间，所以取「第一个标签之后、第二个标签之前」那截。
    """
    tail = str(text or "")[start:start + TAIL_LEN]
    tail = tail.split("<a", 1)[0]                     # 只看这一条自己的尾巴
    parts = re.split(r"<[^>]*>", tail, maxsplit=2)
    if len(parts) > 1:
        return label_of(parts[1])
    return label_of(re.sub(r"(?s)<[^>]+>", " ", tail))


def parse_playlist(markup: str) -> List[dict]:
    """播放列表页 HTML → `[{title, raw_title, video_id, section, rank, badge, candidate}]`。

    标签写在条目**后面**，所以要把「条目」和「紧随其后的小字」配对，而不是先看标签再看标题。
    """
    text = str(markup or "")
    items: List[dict] = []
    pending: Optional[dict] = None
    for match in ANCHOR_RE.finditer(text):
        if pending is not None:
            items.append(pending)
        title = html_module.unescape(re.sub(r"(?s)<[^>]+>", "", match.group(2))).strip()
        item = _new_item(match.group(1), title)
        info = _tail_label(text, match.end())
        if info:
            item.update({key: value for key, value in info.items()
                         if key in ("section", "rank", "badge", "candidate")})
            items.append(item)
            pending = None
        else:
            pending = item
    if pending is not None:
        items.append(pending)
    for item in items:
        if item.get("candidate") != TOPIC:
            continue
        title, vocalist = split_candidate_title(str(item.get("raw_title") or ""))
        if title:
            logging.info("播放列表：「%s」的曲名按新音源写法拆成「%s」（歌姬%s）",
                         item.get("raw_title"), title, vocalist)
            item["title"] = title
            item["vocalist"] = item.get("vocalist") or vocalist
    return items


def fetch_playlist(description: str) -> List[dict]:
    """视频简介 → 解析好的播放列表（拿不到 / 解析不出来就返回 `[]`，调用方退回旧行为）。"""
    url = playlist_url(description)
    if not url:
        return []
    try:
        response = http_get(url, use_proxy=True, retries=1, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        markup = response.text or ""
    except Exception as error:                       # noqa: BLE001 - 博客挂了不能拖垮生成
        logging.info("取播放列表页失败（%s）：%s", url, error)
        return []
    items = parse_playlist(markup)
    if len(items) < MIN_ITEMS:
        logging.info("播放列表页只解析出%d条，当作没解析到", len(items))
        return []
    logging.info("播放列表：共%d条（主榜%d / ピックアップ%d / 番外%d / 新音源%d）", len(items),
                 sum(1 for item in items if item["section"] == MAIN and item.get("rank")),
                 sum(1 for item in items if item.get("badge") == PICKUP),
                 sum(1 for item in items if item["section"] == EXTRA),
                 sum(1 for item in items if item.get("candidate") == TOPIC))
    return items


def wanted(item: dict, limit: int = 0) -> bool:
    """这条该不该进表格（只看播放列表自身的信息，还没和画面配对）。

    `limit > 0` 时主榜只收前 N 名；但 **OP / ED / HISTORY 不吃这一条** ——
    它们本来就没有数字名次（`本周 = OP`），拿 `int()` 去比会直接抛异常。
    """
    if item.get("section") == DROP or not item.get("video_id"):
        return False
    badge = str(item.get("badge") or "").strip().upper()
    if badge in SEQUENCE_LABELS:
        return True
    number = rank_number(item.get("rank"))
    if item.get("section") == MAIN and number is not None and limit and number > int(limit):
        return False
    return True


def must_have(item: dict) -> bool:
    """画面里没读到也要补一行的那种：主榜 N 位、番外 N 位、PICKUP、新音源候选、HISTORY/OP/ED。

    「新音源」（TOPIC 候选）也算：用户 2026-11-17 报 `憑歌ルク`（中二病賛歌）与
    `きるきるにしてあげる` 两首「根本没进条目的TOP区」—— 卡片没被采到就整首消失了。
    现在候选一律先占一行（名次空着 + 备注提醒手填），读到画面再补数字。
    HISTORY / OP / ED 更是一定要收（条目里要写，见 `label_of`）。
    """
    return bool(item.get("rank") or item.get("badge") or item.get("candidate") == TOPIC)


def _rank_number(row: dict) -> Optional[int]:
    """这一行的名次（纯数字才算，`OP` 这类返回 None）。"""
    return rank_number(row.get("本周"))


def _score_of(key: str, other: str) -> float:
    """两个归一化标题的相似度（0～1）。"""
    if not key or not other:
        return 0.0
    if other == key:
        return 1.0
    if key in other or other in key:
        return min(len(key), len(other)) / max(len(key), len(other))
    return difflib.SequenceMatcher(None, key, other).ratio()


def _matching_rows(item: dict, rows: Sequence[dict], used: set) -> List[dict]:
    """播放列表条目 → 画面上所有对得上的行（按相似度从高到低）。

    同一首曲子常常有**两张卡**：TOPIC / PICKUP 段那张没有号码、末尾整页排名表那张有号码
    （用户 2026-11-17 的朝食（あさ）：整页表里是 44 位）。以前只挑「最像的一行」，
    挑中没号码的那张就把 44 位那张当垃圾丢了，所以这里要把匹配上的全都收下。
    """
    key = title_match_key(core_title(item.get("title") or item.get("raw_title") or ""))
    if not key:
        return []
    scored = []
    for row in rows:
        if id(row) in used:
            continue
        other = title_match_key(core_title(row.get("曲名") or ""))
        score = _score_of(key, other)
        if score >= MATCH_MIN:
            scored.append((score, row))
    scored.sort(key=lambda pair: (-pair[0], 0 if _rank_number(pair[1]) is not None else 1))
    return [row for _score, row in scored]


def _merge_rows(targets: Sequence[dict]) -> dict:
    """把同一首曲子的几行并成一行：名次取「有号码的那行」，其余字段非空优先。"""
    ranked = [row for row in targets if _rank_number(row) is not None]
    order = ranked + [row for row in targets if _rank_number(row) is None]
    merged: dict = {}
    for row in order:
        for name, value in row.items():
            if value in (None, "") or str(value).strip() == "":
                continue
            if not str(merged.get(name) or "").strip():
                merged[name] = value
    return merged


def _best_row(item: dict, rows: Sequence[dict], used: set) -> Optional[dict]:
    """在 OCR 结果里找这条播放列表条目对应的那一行（兼容旧调用：只返回最好的一行）。"""
    candidates = _matching_rows(item, rows, used)
    return candidates[0] if candidates else None


def align(rows: Sequence[dict], items: Sequence[dict], limit: int = 0) -> Tuple[List[dict], dict]:
    """把画面 OCR 的行对齐到播放列表 → `(新行, 统计)`。

    * `本周 / 小节 / 曲名 / id` 一律以播放列表为准；`角标` 优先用画面位置判出来的那个
      （见 `chart_recognize`），播放列表补 PICKUP 与「新音源」（TOPIC）；
    * 同一首曲子的**多张卡片会并成一行**（没号码的 TOPIC 卡 + 整页表里有号码的那行 →
      用有号码的名次，数字取非空值），见 `_matching_rows()`；
    * 对不上的 OCR 行丢掉（片头 / 宣传画面上的曲子就是这么消失的），但**已经按画面位置判成
      TOPIC 的行一定保留**（用户要求 TOPIC 全进表格）；
    * 播放列表里有、画面没读到数字的条目补一行（曲名 / id 齐全，数字空着）。
    """
    ocr_rows = [row for row in rows or [] if isinstance(row, dict)]
    stat: Dict[str, object] = {"dropped": [], "missing": [], "kept_topic": [], "merged": 0,
                               "unread_topics": []}
    used: set = set()
    merged_rows: List[dict] = []
    for index, item in enumerate(items or []):
        if not wanted(item, limit):
            continue
        targets = _matching_rows(item, ocr_rows, used)
        if not targets and not must_have(item):
            continue
        row = _merge_rows(targets) if targets else {}
        for target in targets:
            used.add(id(target))
        if len(targets) > 1:
            stat["merged"] = int(stat["merged"]) + 1        # type: ignore[call-overload]
        row["_序"] = str(index)                            # 播放列表顺序＝画面出现顺序
        title = item.get("title") or item.get("raw_title") or str(row.get("曲名") or "")
        row["曲名"] = title
        row["id"] = str(item["video_id"])
        row["raw_title"] = item.get("raw_title") or str(row.get("raw_title") or title)
        vocalist = str(row.get("歌姬") or "").strip() or item.get("vocalist") or ""
        if vocalist:
            row["歌姬"] = vocalist
        if item.get("section") == EXTRA:
            row["小节"] = EXTRA
            if item.get("rank"):
                row["本周"] = str(item["rank"])
        else:
            row.pop("小节", None)
            if item.get("rank"):
                row["本周"] = str(item["rank"])
            if item.get("badge") and not str(row.get("角标") or "").strip():
                row["角标"] = item["badge"]
            elif item.get("candidate") == TOPIC \
                    and str(row.get("角标") or "").strip() in ("", TOPIC):
                # 播放列表「新音源」段 = 影片里的 TOPIC 段（用户 2026-11-17 确认）；
                # 画面没读到也先标 TOPIC 占一行，名次空着等补（以前没画面就整首丢掉）。
                row["角标"] = TOPIC
        if not str(row.get("本周") or "").strip():
            row.pop("本周", None)
            stat["missing"].append(title)                 # type: ignore[union-attr]
            if item.get("candidate") == TOPIC and not targets:
                # 候选曲画面里没读到：名次空着进表格，备注提醒手填（用户 2026-11-17）
                existing = str(row.get("备注") or "").strip()
                note = "TOPIC名次没读到，请手填"
                row["备注"] = f"{existing}；{note}" if existing else note
                stat["unread_topics"].append(title)       # type: ignore[union-attr]
        merged_rows.append(row)
    for row in ocr_rows:
        if id(row) in used:
            continue
        if str(row.get("角标") or "").strip() == TOPIC:
            row.setdefault("_序", str(len(merged_rows) + 1000))
            merged_rows.append(row)
            stat["kept_topic"].append(str(row.get("曲名") or ""))   # type: ignore[union-attr]
            continue
        stat["dropped"].append(str(row.get("曲名") or row.get("title") or ""))  # type: ignore
    if stat["merged"]:
        logging.info("榜单：%d首曲子的多张卡片已并成一行（名次取有号码的那张）", stat["merged"])
    if stat["kept_topic"]:
        logging.info("榜单：按画面位置补进%d首TOPIC曲：%s", len(stat["kept_topic"]),
                     "、".join(str(name) for name in stat["kept_topic"]))
    if stat["dropped"]:
        logging.info("榜单：已丢弃%d行（不在官方播放列表里）：%s", len(stat["dropped"]),
                     "、".join(str(name) for name in stat["dropped"] if name) or "（无名）")
    if stat["missing"]:
        logging.info("官方列表还有%d首没在画面里读到数字：%s", len(stat["missing"]),
                     "、".join(str(name) for name in stat["missing"]))
    if stat["unread_topics"]:
        logging.info("榜单：%d首TOPIC候选画面里没读到名次，已先占一行等手填：%s",
                     len(stat["unread_topics"]),
                     "、".join(str(name) for name in stat["unread_topics"]))
    return merged_rows, stat


def topic_candidates(items: Sequence[dict]) -> List[dict]:
    """播放列表里「新音源」段的条目（＝影片 TOPIC 段的候选）。"""
    return [item for item in items or [] if item.get("candidate") == TOPIC]


def match_item(titles: Sequence[str], items: Sequence[dict], threshold: float = MATCH_MIN
               ) -> Optional[dict]:
    """按曲名在一串播放列表条目里找最像的那条（补读新增 TOPIC 行时用来配 id）。"""
    best: Optional[dict] = None
    best_score = 0.0
    for title in titles:
        key = title_match_key(core_title(title))
        if not key:
            continue
        for item in items or []:
            other = title_match_key(core_title(item.get("title") or item.get("raw_title") or ""))
            score = _score_of(key, other)
            if score > best_score:
                best, best_score = item, score
    return best if best is not None and best_score >= threshold else None


__all__ = ["playlist_url", "parse_playlist", "fetch_playlist", "align", "wanted", "must_have",
           "label_of", "topic_candidates", "match_item", "rank_number", "MAIN", "EXTRA",
           "PICKUP", "TOPIC", "HISTORY", "OP", "ED", "SEQUENCE_LABELS",
           "DROP", "MATCH_MIN", "MIN_ITEMS", "TAIL_LEN", "ANCHOR_TITLE_PREFIX"]
