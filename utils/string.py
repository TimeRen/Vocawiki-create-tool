import re
from datetime import datetime
from typing import Union, Callable, Tuple, List


def auto_lj(s: str) -> str:
    if s.isascii():
        return s
    return "{{lj|" + s + "}}"


def is_empty(s: str) -> bool:
    return not s or s.isspace() or len(s) == 0


def split(s: str, regex: str = "[・/，, ]+") -> List[str]:
    return re.split(regex, s)


def datetime_to_ymd(u: datetime) -> str:
    return f"{u.year}年{u.month}月{u.day}日"


def assert_str_exists(s: str) -> str:
    return s if not is_empty(s) else ""


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\uff0f]', '_', name)
    name = name.rstrip(" .")
    return name or "untitled"


def split_number(num: int) -> str:
    s = str(num)
    chunks = []
    index = len(s) - 3
    while index > 0:
        chunks.insert(0, s[index:index + 3])
        index -= 3
    chunks.insert(0, s[0:index + 3])
    if num >= 100000:
        chunks[-1] = "000+"
    return ",".join(chunks)


def join_string(lst: list, deliminator: Union[str, Tuple[str, str]] = ("、", "和"),
                inner_wrapper: Tuple[str, str] = ("", ""), outer_wrapper: Tuple[str, str] = ("", ""),
                mapper: Callable = lambda x: x) -> str:
    if isinstance(deliminator, str):
        deliminator = (deliminator, deliminator)
    lst = [(outer_wrapper[0] + mapper(inner_wrapper[0] + elem + inner_wrapper[1]) + outer_wrapper[1]) for elem in lst]
    if len(lst) == 0:
        return ""
    if len(lst) == 1:
        return lst[0]
    front = deliminator[0].join(lst[:len(lst) - 1])
    return front + deliminator[1] + lst[-1]


# 歌词正文里常混进来的「资讯行」：作詞：… / 作曲：… / 曲　動画：… / Lyrics: … 这类署名行。
# 各来源的写法不一样，所以角色名之间允许用「、・,，/&＋」或空白分隔，也允许直接连写
# （`作詞作曲：`）；冒号（半角 / 全角）是判定依据，所以「曲がり角で：」这种歌词行不会被误伤。
CREDIT_ROLES = (r"作詞|作词|作曲|編曲|编曲|詞|词|曲|歌詞|歌词|歌|唄|唱|歌唱|訳|譯|翻譯|翻译|"
                r"ボーカル|コーラス|イラスト|插畫|插画|曲绘|繪師|绘师|繪圖|绘图|插圖|動画|动画|"
                r"映像|作品类型|作品類型|类型|類型|简介|簡介|简介补充|備註|备注|說明|说明|公告|"
                r"調声|调声|調教|调教|後期|后期|"
                r"混音|演唱|演出|作画|背景|PV|MV|MIX|ミックス|マスタリング|録音|レコーディング|"
                r"カラオケ|オフボーカル|インスト|本家|原曲|収録|"
                r"Lyrics?|Music|Vocal|Chorus|Illust(?:ration)?|Movie|Arrangement|Arrange[rd]?|"
                r"Compose[rd]?|Mixing?|Mastering|Guitar|Bass|Piano|Drums?|Strings?|Violin|"
                r"Special Thanks|Special Thx|Thanks|Thx|Credit|Credits|Staff|Streaming|Download|DL|"
                r"Off ?Vocal|OffVocal|Inst")
CREDIT_LINE_RE = re.compile(
    r"^[\s\u3000（(【\[「『<＜・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}"
    r"(?:" + CREDIT_ROLES + r")(?:[\s\u3000、・,，/&＆+]{0,2}(?:" + CREDIT_ROLES + r"))*"
    r"\s*[:：]",
    re.IGNORECASE)
# 整行只有一条链接（裸 URL、`<url>`、`[文字](url)`）：简介 / 作品开头的「本家」「動画」行那种
LINK_ONLY_RE = re.compile(
    r"^[\s\u3000・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}"
    r"(?:\[[^\]\n]{1,60}\]\(\s*)?<?https?://\S+>?\)?\s*$",
    re.IGNORECASE)
# 行里带网址：歌词行不会带（`offvocal → https://…`、`■ブログ　http://…` 这类说明行才会）
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
# nico / b 站视频号、mylist：`sm43424916`、`失效补档av36439`、`■前作「clock lock works」sm8933166`
VIDEO_ID_RE = re.compile(r"(?:^|[^0-9A-Za-z])(?:sm|nm|so|av)\d{4,}|BV[0-9A-Za-z]{10}|mylist/\d+",
                         re.IGNORECASE)
SEPARATOR_LINE_RE = re.compile(r"^[-=_~ー—–‐]{3,}$")
LAUGH_LINE_RE = re.compile(r"^[wWｗＷ]{2,}$")
NUMBER_LINE_RE = re.compile(r"^[0-9０-９]+$")
# 「Sheena版:」「ミスターレイヴン cover版:」这类版本标签（多版本作品的正文小标题）
VERSION_LABEL_RE = re.compile(r"^[^版\n]{0,24}版\s*[:：]?$")
# 「简介」里还常写成「角色 + 空白 + 名字」不带冒号（`Music 袖野あらわ`、`Illust 薬屋`、
# `Movie Fujiyama`）。只在简介模式认这种：值要么整段没有拉丁字母、要么就是**一个**英文词，
# 否则 `Music is my life` 这种歌词会被误删。
DESC_CREDIT_NO_COLON_RE = re.compile(
    r"^[\s\u3000・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}(?:" + CREDIT_ROLES + r")"
    r"\s+(?:[^\sA-Za-z]{1,20}|[A-Za-z][A-Za-z.'’\-]{1,17})$",
    re.IGNORECASE)
# 整行就是一个栏目标签
LABEL_LINES = {"歌詞", "歌词", "原文", "譯文", "译文", "中譯", "中译", "中文歌詞", "中文歌词",
               "日文歌詞", "日文歌词", "翻譯", "翻译", "credit", "credits", "staff",
               "クレジット", "スタッフ", "lyrics", "lyric"}
# 「单独成段的整句话」的收尾标点（只在简介那种笔记式文本上用，见 strip_credit_lines）
SENTENCE_END_CHARS = "。．.！!？?…~～"
PROSE_MIN_CHARS = 8


def is_junk_line(line: str, description: bool = False) -> bool:
    """这一行是不是「资讯 / 杂物」行（不是歌词）？`description` 见 `strip_credit_lines()`。"""
    text = (line or "").strip()
    if not text:
        return False
    stripped = text.lstrip("・◆◇■□▲△▼▽●○*＊#＃>＞ \u3000").strip()
    return bool(CREDIT_LINE_RE.match(text)
                or LINK_ONLY_RE.match(text)
                or URL_RE.search(text)
                or VIDEO_ID_RE.search(text)
                or SEPARATOR_LINE_RE.match(stripped)
                or LAUGH_LINE_RE.match(stripped)
                or NUMBER_LINE_RE.match(stripped)
                or VERSION_LABEL_RE.match(stripped)
                or stripped.lower() in LABEL_LINES
                or (description and DESC_CREDIT_NO_COLON_RE.match(text)))


def _join_with_single_blank_lines(lines: List[str]) -> str:
    """首尾不留空行，连续空行只留一个（与歌词页 `normalize_blank_lines` 同一口径）。"""
    result: List[str] = []
    blank = False
    for line in lines:
        if not line:
            blank = True
            continue
        if blank and result:
            result.append("")
        blank = False
        result.append(line)
    return "\n".join(result)


def _drop_isolated_prose(lines: List[str]) -> List[str]:
    """去掉**单独成段**的整句话（宣传语 / 说明句）。

    只给 b 站视频简介那种「笔记式」文本用：那里除了歌词还写着「期待する方が悪い。」
    「总而言之3千粉啦…」这类话，整句独占一段时删掉。判定是「这一段只有一行 + 以句末
    标点收尾 + 不少于 8 个字」，所以歌词段（多行，哪怕每行都以 ！ 结尾）不会被误删。
    """
    result: List[str] = []
    index = 0
    while index < len(lines):
        if not lines[index]:
            result.append("")
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index]:
            index += 1
        block = lines[start:index]
        single = len(block) == 1
        if not (single and len(block[0]) >= PROSE_MIN_CHARS
                and block[0].rstrip()[-1] in SENTENCE_END_CHARS):
            result.extend(block)
    return result


def strip_credit_lines(text: str, description: bool = False) -> str:
    """摘掉歌词里的**资讯行 / 杂物行**。

    包括：`作詞：ねこぼーろ` / `作詞、作曲: Shin` / `曲　動画：Tonbi` / `Lyrics: …` 这类
    署名行，整行只有一条链接的行（`[初音ミク](https://…)`、裸 URL），带网址的说明行
    （`offvocal → https://…`、`Streaming & DL：https://…`），nico / b 站视频号与 mylist
    （`sm43424916`、`失效补档av36439`），`20` 这种纯数字行，`-----` 分隔线，`www` 这类
    笑声行，「Sheena版:」这种版本标签行，以及整行就是一个栏目标签的「歌詞 / 原文」行。

    来源页（AtWiki 页面正文、网易云歌词、b 站简介、巴哈马特作品开头）经常把这些粘在歌词
    前面或中间（用户 2026-10-16 报），不摘掉就会被当成歌词行填进「待归类歌词」/「中文」栏；
    只有中文的译文夹着日文署名行时，「只有中文」的判断也会跟着失灵。

    `description=True` 给 b 站视频简介那种**笔记式**文本用（用户 2026-10-17 要的「宣传语也
    一并清掉」）：除了上面那些，还删「单独成段的整句话」与「角色 + 空白 + 名字」的署名行
    （见 `_drop_isolated_prose()` / `DESC_CREDIT_NO_COLON_RE`）；歌词页那些正经来源不开这个，
    免得把歌词里的整句行误删。
    顺手按歌词页的口径规整空行（首尾不留空行、段落之间只留一个）。
    """
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    kept = [line for line in lines if not is_junk_line(line, description)]
    if description:
        kept = _drop_isolated_prose(kept)
    return _join_with_single_blank_lines(kept)
