"""Android 软键盘的输入类型（`mobile/ime.py` + `mobile/screens/common.py` 的 `text_input()`）。

用户 2026-10-23 报：APK 里填「歌曲原名」时输入法被强锁在英文键盘，打不出日文 / 中文。
原因是 Kivy 2.1 起 `FocusBehavior.input_type` 的默认值从 `'text'` 改成了 `'null'`：
`null` 在 Android 上对应 `InputType.TYPE_NULL`，含义是「只要按键事件、不要输入法组词」，
搜狗 / Gboard 于是停在英文键盘、候选区不出现。

这里不导入 Kivy（CI 的 Python 3.14 装不上 Kivy），所以：常量直接测，
`text_input()` 那一处用**读源码**的方式守住（同 `tests/test_mobile_build.py` 的做法）。
"""
import re
from pathlib import Path
from unittest import TestCase

from mobile import ime

ROOT = Path(__file__).resolve().parent.parent
COMMON = ROOT / "mobile" / "screens" / "common.py"
MOBILE_DIRS = (ROOT / "mobile", ROOT / "mobile" / "screens")


class InputTypeTest(TestCase):
    def test_text_is_requested_not_null(self):
        self.assertEqual("text", ime.INPUT_TYPE)
        self.assertNotEqual("null", ime.INPUT_TYPE,
                            "`null` 会让输入法只剩英文键盘（用户 2026-10-23 报）")

    def test_suggestions_stay_on(self):
        """候选词开着：关掉（`TYPE_TEXT_FLAG_NO_SUGGESTIONS`）时不少输入法也不出中文候选。"""
        self.assertTrue(ime.KEYBOARD_SUGGESTIONS)

    def test_options_carry_both(self):
        options = ime.input_options()
        self.assertEqual("text", options["input_type"])
        self.assertTrue(options["keyboard_suggestions"])
        self.assertIsInstance(options, dict, "要能直接 **展开进 TextInput 的构造参数")


class TextInputHelperTest(TestCase):
    """`text_input()` 必须把 `input_type` 交给每个输入框。"""

    def setUp(self):
        self.source = COMMON.read_text(encoding="utf-8")

    def test_the_helper_passes_the_input_options(self):
        self.assertIn("ime.input_options()", self.source)
        # 显式传进来的（比如数字输入框）要能盖掉默认值
        self.assertRegex(self.source, r"options\.update\(kwargs\)")

    def test_no_input_widget_bypasses_the_helper(self):
        """所有输入框都走 `text_input()`：别处再直接造一个就会漏掉 `input_type`。"""
        offenders = []
        for folder in MOBILE_DIRS:
            for path in folder.glob("*.py"):
                text = path.read_text(encoding="utf-8")
                if path.name == "common.py":
                    continue
                if re.search(r"\b(ThemeInput|TextInput)\(", text):
                    offenders.append(path.name)
        self.assertEqual([], offenders, "这几个文件直接造了输入框，会漏掉 input_type")
