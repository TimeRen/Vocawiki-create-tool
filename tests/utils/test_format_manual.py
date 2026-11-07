"""按《格式手册》守住源码口径（用户 2026-11-07 让对着七份手册校对代码）。

管三件事，都是「一处写错、全站跟着错」的那种：

1. **中西文之间不留空格**（《格式手册/跨文种混排》，配合《定义》：阿拉伯数字、汉语拼音、
   日语罗马字都算「西文」）。所以写「已载入712个P主模板」「未达殿堂（10万播放）」
   「第%d次失败」，不写「已载入 712 个」。
2. **固定大小写的平台名**（《格式手册/大小写》）：`bilibili` / `B站` / `niconico` /
   `YouTube` / `pixiv` / `P站` / `P主`，不写 `b 站`、`b站`、`B 站`。
3. **数值分节**（《格式手册/官方材料》·数值）：五位及以上才用半角逗号分节。

扫描范围就是用户看得见的那些字符串：界面面板（`utils/ui`）里的全部单行字面量，
其它模块只看日志 / 报错 / 提示那一类语句（wikitext 生成、LLM 提示词、正则、网址一律不看）。
整段拼起来返回的多行提示（如 `vocadb._offline_notice()`）不在这套规则里，靠 code review 兜。
"""
import io
import os
import re
import tokenize
import unittest
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HAN = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
KANA = "\u3040-\u30ff"
CJK_GAP = re.compile(rf"(?<=[{HAN}{KANA}]) (?=[A-Za-z0-9%])|(?<=[A-Za-z0-9%]) (?=[{HAN}{KANA}])")
# 错写法：`b 站` / `B 站` / `b站`（正确的 `B站` 不算）
B_STATION = re.compile(r"(?:b\s?站|B 站)")

# 只有这些语句算「用户看得见的文案」（其余模块里全是 wikitext / 提示词 / 数据）
MESSAGE_STATEMENT = re.compile(r"(logging\.[a-z]+\(|raise [A-Za-z]*Error|raise SourceError|"
                               r"_\(|prompt_[a-z_]+\()")
# 正则、数据表、LLM 提示词整段不动
SKIP_STATEMENT = re.compile(r"(re\.(?:compile|sub|search|match|findall|finditer|split)|"
                            r"\b[A-Za-z_]*_RE\s*=|\b[A-Za-z_]*PATTERN\s*=|PROMPT|__all__)")
# 网址 / wikitext 标记 / CSS 声明：这些不是散文，空格规则不适用
SKIP_TEXT = re.compile(r"(\{\{|\}\}|\[\[|\]\]|https?://|://|\{[^}]*;[^}]*\}|"
                       r"^\s*[.#@][A-Za-z-]|\d+px|\bcolor\s*:|background\s*:|font-|border|"
                       r"=>|function\s*\(|const\s)")

UI_DIR = os.path.join(ROOT, "utils", "ui")
# 这几个文件里的字面量是样式表 / 图形坐标，不是文案（`_()` 里的提示仍由 MESSAGE_STATEMENT 兜住）
UI_DATA_FILES = {"theme.py", "style_state.py", "style_preview.py", "icons.py"}


def source_files():
    files = [os.path.join(ROOT, "main.py")]
    utils = os.path.join(ROOT, "utils")
    files += [os.path.join(utils, name) for name in sorted(os.listdir(utils))
              if name.endswith(".py")]
    files += [os.path.join(UI_DIR, name) for name in sorted(os.listdir(UI_DIR))
              if name.endswith(".py")]
    return [path for path in files if os.path.exists(path)]


def statements(tokens):
    """按语句（逻辑行）分组 token 下标。"""
    current = []
    for index, token in enumerate(tokens):
        if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
            if current:
                yield current
            current = []
            continue
        current.append(index)
    if current:
        yield current


def visible_literals(path):
    """用户可见文案里的字符串字面量 → `[(行号, 原文)]`（按文件缓存，三个用例共用）。"""
    return _literals(path)


@lru_cache(maxsize=None)
def _literals(path):
    with io.open(path, encoding="utf-8") as handle:
        source = handle.read()
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    broad = os.path.dirname(path) == UI_DIR and os.path.basename(path) not in UI_DATA_FILES
    found = []
    for indexes in statements(tokens):
        meaningful = [index for index in indexes
                      if tokens[index].type not in (tokenize.NL, tokenize.INDENT,
                                                    tokenize.DEDENT, tokenize.COMMENT)]
        if len(meaningful) == 1 and tokens[meaningful[0]].type == tokenize.STRING:
            continue                                       # docstring / 裸字符串
        text = "".join(tokens[index].line for index in meaningful)
        if SKIP_STATEMENT.search(text):
            continue
        if not broad and not MESSAGE_STATEMENT.search(text):
            continue
        for index in meaningful:
            token = tokens[index]
            if token.type == tokenize.STRING:
                raw = token.string
            elif getattr(tokenize, "FSTRING_MIDDLE", None) and token.type == tokenize.FSTRING_MIDDLE:
                raw = token.string
            else:
                continue
            if SKIP_TEXT.search(raw.strip("\"'f")):
                continue
            found.append((token.start[0], raw))
    return tuple(found)


class SpacingTest(unittest.TestCase):
    """中西文之间不留空格（`已载入712个P主模板`，不是 `已载入 712 个`）。"""

    def test_no_space_between_han_and_latin_or_digits(self):
        offenders = []
        for path in source_files():
            for line, raw in visible_literals(path):
                if CJK_GAP.search(raw):
                    offenders.append(f"{os.path.basename(path)}:{line}: {raw}")
        self.assertEqual([], offenders, "按《格式手册/跨文种混排》去掉汉字与西文之间的空格")


class PlatformNameTest(unittest.TestCase):
    """固定大小写的平台名（《格式手册/大小写》）。"""

    def test_b_station_is_written_without_a_space(self):
        offenders = []
        for path in source_files():
            for line, raw in visible_literals(path):
                if B_STATION.search(raw):
                    offenders.append(f"{os.path.basename(path)}:{line}: {raw}")
        self.assertEqual([], offenders, "写作 B站，不写 b 站 / B 站 / b站")

    def test_video_site_values_use_the_fixed_casing(self):
        from models.video import VideoSite

        self.assertEqual({"niconico", "bilibili", "YouTube"},
                         {site.value for site in VideoSite})

    def test_pv_service_names_map_to_the_fixed_casing(self):
        import main

        self.assertTrue(set(main.PV_SERVICE_SITES.values()) <= {"niconico", "bilibili", "YouTube"},
                        main.PV_SERVICE_SITES)


class NumberGroupingTest(unittest.TestCase):
    """数值：五位及以上才分节（《格式手册/官方材料》·数值）。"""

    def test_four_digit_numbers_stay_ungrouped(self):
        from utils.string import group_number

        self.assertEqual("4321", group_number(4321))
        self.assertEqual("9999", group_number(9999))

    def test_five_digit_numbers_are_grouped(self):
        from utils.string import group_number

        self.assertEqual("54,321", group_number(54321))
        self.assertEqual("1,081,622", group_number(1081622))

    def test_no_more_rounding_to_a_plus_sign(self):
        from utils.string import split_number

        self.assertNotIn("+", split_number(123456))


class RomajiApostropheTest(unittest.TestCase):
    """拨音隔音符（《格式手册/日语罗马字》第 4 条）。"""

    def test_entry_names_use_the_apostrophe(self):
        from utils.japanese import kana_to_romaji

        self.assertEqual("ken’i", kana_to_romaji("けんい", apostrophe=True))
        self.assertEqual("shin’in", kana_to_romaji("しんいん", apostrophe=True))

    def test_alias_matching_still_uses_plain_ascii(self):
        from utils.japanese import kana_to_romaji

        reading = kana_to_romaji("けんいち")
        self.assertTrue(reading.isascii())
        self.assertEqual("kenichi", reading)


if __name__ == "__main__":
    unittest.main()
