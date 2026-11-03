import functools
import re
from typing import Tuple

hiragana_pattern = re.compile("[\u3041-\u3096]")
katakana_pattern = re.compile("[\u30A0-\u30FF]")
kanji_pattern = re.compile("[\u3400-\u4DB5\u4E00-\u9FCB\uF900-\uFA6A]")

# 片假名那一段码位里其实混着**标点 / 记号**，判断「这行是日语吗」时不能算假名：
# `・`（间隔号 —— 中文译文也常写成「・・・」）、`ー`（长音符，中文里偶尔当破折号）、
# `゠`（双连字符）。以前把 `・` 当假名，一整段中文译文就被判成日语，
# 译文于是进了「待归类歌词」栏而不是「中文」栏（用户 2026-10-22 报）。
KANA_SYMBOLS = "\u30A0\u30FB\u30FC"


def is_hiragana(c: str) -> bool:
    return not not hiragana_pattern.fullmatch(c)


def is_katakana(c: str) -> bool:
    return not not katakana_pattern.fullmatch(c)


def is_kana(c: str) -> bool:
    return is_hiragana(c) or is_katakana(c)


def is_kana_letter(c: str) -> bool:
    """`c` 是不是**真正的假名字母**（片假名区里的标点不算，见 `KANA_SYMBOLS`）。

    只给「这一行是日语还是中文」这类判断用；注音、罗马音转换仍用 `is_kana()`
    （那里的 `ー` 确实是假名的一部分）。
    """
    return is_kana(c) and c not in KANA_SYMBOLS


def is_kanji(c: str) -> bool:
    return c == "々" or not not kanji_pattern.fullmatch(c)


def is_japanese(c: str) -> bool:
    return is_kana(c) or is_kanji(c)


# `{{ruby|漢字|かんじ}}` / `{{ルビ|…}}` / `{{Ruby|…}}`：站上的注音模板是 `{{photrans}}`，
# 但别的条目（以及模型自己）常写成 ruby。参数里不许有 `|` / 花括号，避免误呑嵌套模板。
RUBY_RE = re.compile(r"\{\{\s*(?:ruby|ルビ)\s*\|\s*([^|{}=]+?)\s*\|\s*([^|{}=]+?)\s*\}\}",
                     re.IGNORECASE)


def to_photrans(lyrics: str) -> str:
    """把 `{{ruby|漢字|かんじ}}` 统一成 `{{photrans|漢字|かんじ}}`（用户 2026-11-03 要求）。

    站上的注音模板是 `{{Photrans}}`（`Template:Photrans` → `Module:photrans`），歌词里的
    注音一律写它 —— 用户点「自动排版 / AI 排版」后看到日语栏还是 `{{ruby}}` 就是漏了这一步。

    只改**两个位置参数、第二个参数全是假名**的写法：
    * `{{ruby|2=かな naru…|1=…}}`（《phony》那种**整行罗马音**）参数里带 `=`，正则不匹配；
    * `{{ruby|漢字|kanji}}`（读音写成罗马音）第二个参数不是假名，也不动；
    * `{{PT|…}}` / `{{振假名|template=Photrans|…}}` 这些站上已有的写法同样不动（它们渲染出来就是注音）。
    """
    def replace(match: "re.Match") -> str:
        base, reading = match.group(1), match.group(2)
        if not reading or not all(is_kana(char) for char in reading):
            return match.group(0)
        return f"{{{{photrans|{base}|{reading}}}}}"

    return RUBY_RE.sub(replace, lyrics or "")


def furigana_local(lyrics: str) -> str:
    open_parentheses = ['(', '（']
    closing_parentheses = [')', '）']
    lines = (lyrics or "").split("\n")
    result = []
    for line in lines:
        prev_end = 0
        line_result = []
        for index in range(len(line)):
            if index < prev_end:
                continue
            if line[index] not in open_parentheses or index == 0 or not is_kanji(line[index - 1]):
                continue
            symbol = closing_parentheses[open_parentheses.index(line[index])]
            close_index = line.find(symbol, index + 1)
            furigana = line[index + 1:close_index]
            if close_index == -1 or index + 1 == close_index or \
                    not functools.reduce(lambda a, b: a and b,
                                         [is_kana(c) for c in furigana]):
                continue
            kanji_start = index - 1
            while kanji_start >= 0 and is_kanji(line[kanji_start]):
                kanji_start -= 1
            kanji_start += 1
            if kanji_start > 0:
                line_result.append(line[prev_end:kanji_start])
            line_result.append(f"{{{{photrans|{line[kanji_start:index]}|{furigana}"
                               f"}}}}")
            prev_end = close_index + 1
        line_result.append(line[prev_end:])
        result.append("".join(line_result))
    return to_photrans("\n".join(result))


# ---------------------------------------------------------------- 假名 → 罗马音

# 同名条目括号里的 P主名，假名部分站上写罗马音（`柊キライ` → `柊kirai`、
# `蜂屋ななし` → `蜂屋nanashi`，参站内条目 `Eureka(柊kirai)` / `生命体征(蜂屋nanashi)`）。
# 表按 Hepburn 拼写，键只写平假名一份，片假名先转平假名再查（见 `_to_hiragana`）。
_ROMAJI_TABLE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "ゕ": "ka", "ゖ": "ke",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ゎ": "wa",
    "ん": "n", "ゔ": "vu",
    # 拗音 / 外来语组合（两字一组，先按两字查）
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "しぇ": "she", "じぇ": "je", "ちぇ": "che",
    "いぇ": "ye", "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
}
_VOWELS = "aiueo"


def _to_hiragana(char: str) -> str:
    """片假名 → 平假名（罗马音表只写平假名一份）；其他字符原样返回。"""
    code = ord(char)
    return chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else char


def _romaji_at(text: str, index: int) -> Tuple[str, int]:
    """`text[index]` 起最长的一段假名 → `(罗马音, 吃掉的字符数)`；不是表里的假名就原样。"""
    for size in (2, 1):
        piece = text[index:index + size]
        if piece in _ROMAJI_TABLE:
            return _ROMAJI_TABLE[piece], size
    return text[index], 1


def kana_to_romaji(text: str) -> str:
    """假名 → 罗马音（Hepburn、小写）；假名以外的字符原样保留。

    同名条目的 P主名要用站上的写法，站上把假名写成罗马音：
    `柊キライ` → `柊kirai`、`蜂屋ななし` → `蜂屋nanashi`、`みきとP` → `mikitoP`。
    `ー` 按长音补一个元音（`コーヒー` → `koohii`），`っ` 把后面的辅音写两遍（`ロック` → `rokku`）。
    """
    hiragana = "".join(_to_hiragana(char) for char in text or "")
    result = []
    index = 0
    while index < len(hiragana):
        char = hiragana[index]
        if char == "っ":                                  # 促音：把后面的辅音写两遍
            following, _ = _romaji_at(hiragana, index + 1) if index + 1 < len(hiragana) \
                else ("", 1)
            if following[:1] and following[0] not in _VOWELS:
                result.append(following[0])
            index += 1
        elif char == "ー":                                # 长音符：重复上一个元音
            if result and result[-1][-1:] in _VOWELS:
                result.append(result[-1][-1])
            index += 1
        else:
            romaji, size = _romaji_at(hiragana, index)
            result.append(romaji)
            index += size
    return "".join(result)
