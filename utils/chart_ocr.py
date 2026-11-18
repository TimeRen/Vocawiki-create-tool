# -*- coding: utf-8 -*-
"""本地 OCR 读周刊卡片（数字交给 OCR，AI 只做兜底）。

为什么要有这一层（用户 2026-11-17 的建议）：周刊卡片上的东西其实很规整 ——
左上角一个名次，下面几行 `LAST WEEK` / `TOTAL` / `VIEW` / `COMMENT` / `MYLIST`，
再下面是曲名与歌姬。数字是最适合 OCR 的部分，用本地引擎读**零成本、无网络、可重复**，
比每次都把 4 张图丢给 AI 稳得多，也便宜得多。

引擎按优先级挑（都是懒加载，移动端/精简安装没有就返回不可用，调用方退回 AI）：

1. `rapidocr`（2.x，onnxruntime 版，`Rec.lang_type="japan"` 用多语种识别模型，
   日文假名 + 数字 + 拉丁标签都能读；首次用会自动下载模型，之后离线可用）；
2. `rapidocr_onnxruntime`（1.x，自带中英模型 —— 数字照样读得准，假名读不出）；
3. `pytesseract`（装了 `jpn` + `eng` 语言包时）。

对外只有三件事：`available()` / `read_card(图片字节)` / `parse_card(文本行)`。
`parse_card()` 是纯函数，测试直接喂字符串即可。
"""
import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

# 名次：`149`、`149 位`、`149 LAST WEEK new!!` 都算；`TOTAL 164` 这种带标签的不算
DIGITS_ONLY_RE = re.compile(r"^\s*(\d{1,4})\s*位?\s*$")
LEADING_RANK_RE = re.compile(r"^\s*(\d{1,4})(?![\d])\s*(?:位)?\s*[^\d]")
RANK_WORD_RE = re.compile(r"(\d{1,4})\s*位")
# 标签 → 字段名（拉丁标签大小写不敏感；日文标签用原文）
NUMBER_LABELS = (
    (("得点", "得點", "TOTAL", "合計", "POINT", "SCORE"), "得点"),
    (("再生", "VIEW", "再生数"), "再生"),
    (("コメント", "COMMENT", "评论"), "评论"),
    (("マイリスト", "MYLIST", "收藏"), "收藏"),
)
LAST_LABELS = ("LAST WEEK", "前回", "上期", "先週", "先週順位")
NEW_WORDS = ("new", "初", "—", "---", "-", "圏外", "圈外")
MAX_RANK = 400

_ENGINE = None
_ENGINE_TRIED = False


def _build_engine():
    """按优先级构造引擎 → `(引擎名, 可调用对象)`；都不可用返回 `(None, None)`。"""
    try:
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Rec.lang_type": "japan"})
        logging.info("榜单读数：用本地OCR（rapidocr日文模型）")
        return "rapidocr", engine
    except Exception as error:                   # noqa: BLE001 - 没装/模型下不来就往下试
        logging.debug("rapidocr日文模型不可用：%s", error)
    try:
        from rapidocr import RapidOCR

        # 日文模型没打进包（或首次下载失败）时退回**默认中英模型**：卡片上的名次与
        # TOTAL / VIEW / COMMENT / MYLIST 都是数字，默认模型照样读得准，只是假名读不出。
        engine = RapidOCR()
        logging.info("榜单读数：用本地OCR（rapidocr默认模型，日文模型不可用）")
        return "rapidocr", engine
    except Exception as error:                   # noqa: BLE001
        logging.debug("rapidocr不可用：%s", error)
    try:
        from rapidocr_onnxruntime import RapidOCR as LegacyRapidOCR

        engine = LegacyRapidOCR()
        logging.info("榜单读数：用本地OCR（rapidocr_onnxruntime中英模型）")
        return "rapidocr_onnxruntime", engine
    except Exception as error:                   # noqa: BLE001
        logging.debug("rapidocr_onnxruntime不可用：%s", error)
    try:
        import pytesseract  # noqa: F401

        logging.info("榜单读数：用本地OCR（pytesseract jpn）")
        return "pytesseract", None
    except Exception as error:                   # noqa: BLE001
        logging.debug("pytesseract不可用：%s", error)
    return None, None


def engine():
    """引擎单例（第一次真正读数时才构造；不可用就一直返回 None，不反复重试）。"""
    global _ENGINE, _ENGINE_TRIED
    if not _ENGINE_TRIED:
        _ENGINE_TRIED = True
        name, instance = _build_engine()
        _ENGINE = (name, instance) if name else (None, None)
    return _ENGINE


def available() -> bool:
    """本地 OCR 能不能用（不能就由调用方继续用 AI）。

    这里只**探测依赖装没装**（`find_spec`），不构造引擎 —— 构造 `RapidOCR` 要加载 / 下载
    onnx 模型（几十 MB），在「一帧都不是图片」的调用里（单元测试、浏览器截图）完全没必要。
    """
    return _name_available() is not None


def _name_available():
    """按优先级返回第一个可用引擎的名字（只看装没装）。"""
    import importlib.util

    for name, module in (("rapidocr", "rapidocr"),
                         ("rapidocr_onnxruntime", "rapidocr_onnxruntime"),
                         ("pytesseract", "pytesseract")):
        try:
            if importlib.util.find_spec(module) is not None:
                return name
        except Exception as error:               # noqa: BLE001 - 坏安装就当没有
            logging.debug("%s探测失败：%s", module, error)
    return None


def reset_engine() -> None:
    """忘掉缓存的引擎（测试用）。"""
    global _ENGINE, _ENGINE_TRIED, _FAILURE_LOGGED
    _ENGINE, _ENGINE_TRIED = None, False
    _FAILURE_LOGGED = False


def read_text(image: bytes) -> List[str]:
    """一张 JPEG → OCR 文本行（按识别顺序）。引擎不可用或读不出来返回 `[]`。"""
    if not image:
        return []
    name, instance = engine()
    if name is None:
        return []
    try:
        if name == "pytesseract":
            import io

            import pytesseract
            from PIL import Image

            image_file = Image.open(io.BytesIO(image))
            text = pytesseract.image_to_string(image_file, lang="jpn+eng")
            return [line.strip() for line in text.splitlines() if line.strip()]
        if name == "rapidocr":
            output = instance(image)
            return [str(text) for text in (getattr(output, "txts", None) or []) if str(text).strip()]
        started, pairs = instance(image)
        if not started:
            return []
        return [str(pair[1]) for pair in pairs or [] if str(pair[1]).strip()]
    except Exception as error:                   # noqa: BLE001 - 单帧读失败不该拖垮整轮
        _note_read_failure(error)
        return []


def _number(value: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


# 第一次读图失败要留一条 WARNING（用户 2026-11-18：日志里 OCR 静默返回 0 行，查不出原因）
_FAILURE_LOGGED = False


def _note_read_failure(error: Exception) -> None:
    """第一次失败写成 WARNING（之后退回 DEBUG，免得几百帧刷满日志）。"""
    global _FAILURE_LOGGED
    if not _FAILURE_LOGGED:
        _FAILURE_LOGGED = True
        logging.warning("本地OCR读这张图失败（后续同类错误只记DEBUG）：%s", error)
    else:
        logging.debug("本地OCR读这张图失败：%s", error)


def parse_card(lines: Sequence[str]) -> Dict[str, str]:
    """OCR 文本行 → `{本周, 得点, 再生, 评论, 收藏, 上周}`（读不到的键不出现）。"""
    texts = [str(line or "").strip() for line in lines or []]
    joined = " ".join(texts)
    upper = joined.upper()
    card: Dict[str, str] = {}
    for index, line in enumerate(texts):
        rank = _rank_of(line)
        if rank is not None:
            card["本周"] = str(rank)
            break
    if "本周" not in card:
        match = RANK_WORD_RE.search(joined)
        if match and _number(match.group(1)) is not None:
            card["本周"] = match.group(1)
    for labels, name in NUMBER_LABELS:
        value = _labelled_number(texts, upper, labels)
        if value is not None:
            card[name] = str(value)
    # 标签本身也会被 OCR 读花（实测 `VIEW` 常读成 `VIEH` / `UIEH`、`MYLIST` 读成 `MVLIST`），
    # 所以标签认不全时按卡片版式兜底：`TOTAL` 之后依次是 得点 / 再生 / 评论 / 收藏。
    for name, value in zip(("得点", "再生", "评论", "收藏"), _number_sequence(texts)):
        card.setdefault(name, str(value))
    last = _last_week(upper, joined)
    if last:
        card["上周"] = last
    return card


def _number_sequence(lines: Sequence[str]) -> List[int]:
    """`TOTAL`（或 `得点`）之后那串纯数字行，按出现顺序 —— 卡片版式固定，用来兜底。"""
    start = None
    for index, line in enumerate(lines or []):
        text = str(line or "").upper()
        if any(label.upper() in text for label in ("TOTAL", "得点", "合計")):
            start = index
            break
    if start is None:
        return []
    numbers: List[int] = []
    for line in lines[start:]:
        text = str(line or "").strip()
        if not re.fullmatch(r"[\d][\d,，]*", text):
            continue
        value = _number(text)
        if value is not None:
            numbers.append(value)
    return numbers[:4]


def _rank_of(line: str) -> Optional[int]:
    """这一行是不是「纯名次」（`149` / `149 位` / `149 LAST WEEK new!!`）。"""
    text = str(line or "")
    if not text or any(word in text.upper() for word in
                       ("TOTAL", "VIEW", "COMMENT", "MYLIST", "得点", "再生", "コメント",
                        "マイリスト", "LAST WEEK")):
        return None
    match = DIGITS_ONLY_RE.match(text)
    if match:
        value = _number(match.group(1))
        return value if value is not None and 1 <= value <= MAX_RANK else None
    match = LEADING_RANK_RE.match(text)
    if match:
        value = _number(match.group(1))
        return value if value is not None and 1 <= value <= MAX_RANK else None
    return None


def _labelled_number(lines: Sequence[str], upper: str, labels: Sequence[str]) -> Optional[int]:
    """`TOTAL 164` / `得点 164` / 分两行的 `TOTAL` + `164` 都要认。"""
    for label in labels:
        key = label.upper()
        pattern = re.escape(key) + r"\s*[:：]?\s*([\d][\d,，]*)"
        match = re.search(pattern, upper)
        if match:
            return _number(match.group(1))
        for index, line in enumerate(lines):
            if key in line.upper():
                rest = line.upper().split(key, 1)[1]
                value = _number(rest)
                if value is not None:
                    return value
                if index + 1 < len(lines):
                    nxt = _number(lines[index + 1])
                    if nxt is not None and len(str(lines[index + 1]).strip()) <= 8:
                        return nxt
    return None


def _last_week(upper: str, joined: str) -> str:
    """`LAST WEEK 22` → `22`；`LAST WEEK new!!` 或读不到 → 空串（＝新曲）。"""
    for label in LAST_LABELS:
        key = label.upper()
        position = upper.find(key)
        if position < 0:
            continue
        tail = upper[position + len(key):position + len(key) + 16]
        if any(word.upper() in tail for word in NEW_WORDS):
            return ""
        match = re.search(r"(\d{1,4})", tail)
        if match:
            value = _number(match.group(1))
            return str(value) if value is not None and value <= MAX_RANK else ""
    return ""


def is_complete(card: Dict[str, str]) -> bool:
    """这张卡读全了没有：名次 + 至少两个数字（得点/再生 其中之一必须有）。"""
    if not card.get("本周"):
        return False
    numbers = [card.get(name) for name in ("得点", "再生", "评论", "收藏") if card.get(name)]
    if len(numbers) < 2 or not (card.get("得点") or card.get("再生")):
        return False
    total = _number(card.get("得点") or "")
    if total is not None:
        for name in ("再生", "评论", "收藏"):
            value = _number(card.get(name) or "")
            if value is not None and value > total:
                return False
    return True


def is_usable(card: Dict[str, str]) -> bool:
    """够不够凑成一行：有名次 + 至少一个数字，且不违反「得点 ≥ 其余」。

    比 `is_complete` 松一档。用户 2026-11-19 报「番外榜少了这么多曲子」：番外 /
    TOPIC 的卡片往往只读得到名次和一两项（实测番外第 2 名只读出
    `得点 7156 / 再生 3947 / 评论 268 / 收藏 148`，有的卡只剩名次），按 `is_complete`
    要求「名次 + 两个数字且必须有得点或再生」就会整张丢掉。曲名后面还要跟播放列表
    对得上才会成行，所以这里松一点不会放进垃圾。
    """
    if not card.get("本周"):
        return False
    numbers = [card.get(name) for name in ("得点", "再生", "评论", "收藏") if card.get(name)]
    if not numbers:
        return False
    total = _number(card.get("得点") or "")
    if total is not None:
        for name in ("再生", "评论", "收藏"):
            value = _number(card.get(name) or "")
            if value is not None and value > total:
                return False
    return True


def read_card(image: bytes) -> Dict[str, str]:
    """一张 JPEG → `parse_card(read_text(...))`。"""
    return parse_card(read_text(image))


def read_card_lines(image: bytes) -> Tuple[Dict[str, str], List[str]]:
    """一张 JPEG → `(卡片数据, OCR 文本行)`（文本行用来跟播放列表标题配对）。"""
    lines = read_text(image)
    return parse_card(lines), lines


__all__ = ["available", "engine", "reset_engine", "read_text", "read_card", "read_card_lines",
           "parse_card", "is_complete", "is_usable", "MAX_RANK"]
