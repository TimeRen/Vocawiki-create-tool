"""歌词页的「预填翻译」与「提交页标签重新变灰」（用户 2026-10-20 提的两处界面行为）。

Qt 用例跑在 offscreen 平台上（`QT_QPA_PLATFORM=offscreen`），不开 WebEngine。
"""
import os
from unittest import TestCase, mock

from PyQt5 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_WEBENGINE"] = "1"

from utils.ui.window import install_exception_guard                                # noqa: E402
from utils.ui.window import _Bridge as _UiBridge                                   # noqa: E402

GUARDED_ERRORS = []
_guard_bridge = _UiBridge()
_guard_bridge.crashed.connect(lambda message, trace: GUARDED_ERRORS.append(message))
install_exception_guard(_guard_bridge)


class _FakeLyricsApi:
    """够 `LyricsPanel.start()` 用就行：它只读 `get_context()`。"""

    def __init__(self, context):
        self._context = context

    def get_context(self):
        return self._context


class PanelTestBase(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from tests.utils.test_ui import reset_font_scale
        from utils.ui.window import MainWindow

        reset_font_scale()
        self.window = MainWindow()

    def tearDown(self):
        from tests.utils.test_ui import reset_font_scale

        self.window.deleteLater()
        self.app.processEvents()
        reset_font_scale()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")


class LyricsPrefillTest(PanelTestBase):
    """流程先查到 vocaloid中文歌词wiki 的翻译 → 歌词页一打开就填好「从链接填充」会填的东西。"""

    CONTEXT = {
        "initial": "", "jap": "月明かりがそっと照らしだす", "chs": "月光悄悄照耀而下",
        "sourceHint": "https://w.atwiki.jp/vocaloidchly/pages/3476.html",
        "translator": "yanao", "translatorUrl": "",
        "sourceName": "VOCALOID中文歌词wiki",
        "sourceUrl": "https://w.atwiki.jp/vocaloidchly/pages/3476.html",
        "prefillMessage": "已从 VOCALOID中文歌词wiki 预填翻译（翻译者：yanao）",
        "useHover": False, "useColors": False, "useRoma": False,
        "charas": [], "aiLyrics": {"enabled": False, "hidden": True},
    }

    def _panel(self):
        return self.window._panels["lyrics"]

    def test_prefilled_translation_is_in_the_columns_and_the_source_fields(self):
        panel = self._panel()
        panel.start({"api": _FakeLyricsApi(self.CONTEXT)})
        self.assertEqual("月光悄悄照耀而下", panel.chs_edit.toPlainText())
        self.assertEqual("月明かりがそっと照らしだす", panel.jap_edit.toPlainText())
        self.assertEqual("", panel.source_edit.toPlainText(), "只有中文的译文不该进待归类栏")
        self.assertEqual("yanao", panel.translator_edit.text())
        self.assertEqual("VOCALOID中文歌词wiki", panel.source_name_edit.text())
        self.assertEqual("https://w.atwiki.jp/vocaloidchly/pages/3476.html",
                         panel.source_url_edit.text(), "翻译链接要填上")
        self.assertIn("预填翻译", panel.status_label.text())

    def test_without_a_prefill_the_translation_boxes_stay_empty(self):
        panel = self._panel()
        context = dict(self.CONTEXT)
        for key in ("chs", "translator", "translatorUrl", "sourceName", "sourceUrl",
                    "prefillMessage"):
            context[key] = ""
        panel.start({"api": _FakeLyricsApi(context)})
        self.assertEqual("", panel.chs_edit.toPlainText())
        self.assertEqual("", panel.translator_edit.text())
        self.assertIn("来源", panel.status_label.text())


class SubmitTabResetTest(PanelTestBase):
    """「清除对话记录」之后提交页的内容清掉，标签也要重新变灰（用户 2026-10-20）。"""

    def test_the_submit_tab_is_greyed_out_again(self):
        index = self.window.page_index("submit")
        self.window.tabs.setTabEnabled(index, True)
        self.window.tabs.setCurrentIndex(index)
        self.assertTrue(self.window.tabs.isTabEnabled(index))

        self.window._reset_panels()

        self.assertFalse(self.window.tabs.isTabEnabled(index), "提交页要重新变灰")
        self.assertIsNot(self.window.tabs.currentWidget(), self.window._page_areas["submit"],
                         "当前页不该停在一个灰掉的标签上")

    def test_reset_after_a_full_run_also_greys_the_tab(self):
        """走一遍 `_reset_for_new_run()`（清除对话记录实际调的那个）。"""
        index = self.window.page_index("submit")
        self.window.tabs.setTabEnabled(index, True)
        self.window._reset_for_new_run()
        self.assertFalse(self.window.tabs.isTabEnabled(index))
