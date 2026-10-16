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
CREDIT_ROLES = (r"作詞|作词|作曲|編曲|编曲|詞|词|曲|歌詞|歌词|歌|唄|唱|訳|譯|翻譯|翻译|"
                r"ボーカル|コーラス|イラスト|插畫|插画|曲绘|繪師|绘师|動画|动画|映像|"
                r"PV|MV|MIX|ミックス|調声|调声|調教|调教|マスタリング|録音|レコーディング|"
                r"カラオケ|オフボーカル|インスト|本家|原曲|収録|"
                r"Lyrics?|Music|Vocal|Chorus|Illust(?:ration)?|Movie|Arrangement|Arrange[rd]?|"
                r"Compose[rd]?|Mixing?|Mastering|Guitar|Bass|Piano|Drums?")
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


def strip_credit_lines(text: str) -> str:
    """摘掉歌词里的**资讯行**：`作詞：ねこぼーろ` / `作詞、作曲: Shin` / `曲　動画：Tonbi` /
    `Lyrics: …` 这类署名行，以及整行只有一条链接的行（`[初音ミク](https://…)`、裸 URL）。

    来源页（AtWiki 页面正文、网易云歌词、b 站简介、巴哈姆特作品开头）经常把这些粘在歌词
    前面或中间（用户 2026-10-16 报），不摘掉就会被当成歌词行填进「待归类歌词」/「中文」栏；
    只有中文的译文夹着日文署名行时，「只有中文」的判断也会跟着失灵。
    顺手按歌词页的口径规整空行（首尾不留空行、段落之间只留一个）。
    """
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    return _join_with_single_blank_lines(
        [line for line in lines
         if not (line and (CREDIT_LINE_RE.match(line) or LINK_ONLY_RE.match(line)))])
