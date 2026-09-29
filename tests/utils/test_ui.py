"""utils/ui 的单测：门面、提问路由、主窗口与三个编辑器页。

Qt 相关用例跑在 offscreen 平台上（`QT_QPA_PLATFORM=offscreen`），
不开浏览器内核（`VOCAWIKI_NO_WEBENGINE=1`），也不需要真的显示窗口。
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from PyQt5 import QtWidgets

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_WEBENGINE"] = "1"          # 预览只用到「在浏览器里打开」
os.environ.pop("VOCAWIKI_CONSOLE", None)

# 单测里不要在槽函数抛异常时把整个测试进程 abort（PyQt5 默认行为）
from utils.ui.window import install_exception_guard                        # noqa: E402
from utils.ui.window import _Bridge as _UiBridge                           # noqa: E402

_guard_bridge = _UiBridge()
GUARDED_ERRORS = []


def _record_guard(message, trace):
    GUARDED_ERRORS.append(message)


_guard_bridge.crashed.connect(_record_guard)
install_exception_guard(_guard_bridge)

from utils import helpers, ui                                                    # noqa: E402
from utils.ui import style_state as st                                           # noqa: E402
from utils.ui import theme                                                       # noqa: E402
from tests.utils import some_font_file as _some_font_file                        # noqa: E402


def reset_font_scale() -> None:
    """字号缩放系数（和它带来的应用字体）是全局状态，用例之间必须还原，
    否则「前面哪个用例开了个大窗口」会把后面的字号断言/几何断言全部带偏；
    界面字体同理。什么都没变时直接返回——重套一次样式表很贵（Qt 要把整棵树重新 polish）。"""
    from PyQt5 import QtWidgets
    from utils.ui import theme
    changed = theme.set_font_family("")
    changed = theme.set_scale(1.0) or changed
    if not changed:
        return
    app = QtWidgets.QApplication.instance()
    if app is not None:
        theme.apply_theme(app)


def _pump(predicate, timeout=10.0):
    """转 Qt 事件循环直到条件成立（避免在单测里真的跑 app.exec_()）。"""
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance()
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    return predicate()


class FacadeTest(TestCase):
    def test_available_needs_qt_and_no_console_flag(self):
        from utils import ui as facade
        with mock.patch.object(sys, "argv", ["main.py"]), \
             mock.patch.object(facade, "qt_available", return_value=True):
            self.assertTrue(facade.available())
        with mock.patch.object(sys, "argv", ["main.py", "--console"]), \
             mock.patch.object(facade, "qt_available", return_value=True):
            self.assertFalse(facade.available())
        with mock.patch.object(sys, "argv", ["main.py"]), \
             mock.patch.object(facade, "qt_available", return_value=False):
            self.assertFalse(facade.available())

    def test_console_env_forces_terminal(self):
        from utils import ui as facade
        with mock.patch.dict(os.environ, {"VOCAWIKI_CONSOLE": "1"}):
            self.assertTrue(facade.console_requested())
            self.assertFalse(facade.available())

    def test_not_active_by_default(self):
        self.assertFalse(ui.is_active())
        self.assertIsNone(ui.main_window())

    def test_ask_requires_running_window(self):
        for call in (lambda: ui.ask_response("q"), lambda: ui.ask_choices("q", ["a"]),
                     lambda: ui.ask_multiline("q")):
            with self.assertRaises(RuntimeError):
                call()

    def test_status_and_open_folder_are_noops_when_idle(self):
        ui.status("没人听得到")                          # 不抛异常就是通过
        with mock.patch.object(ui, "open_folder", return_value=True) as opened:
            self.assertTrue(ui.open_folder("."))

    def test_output_prints_when_idle(self):
        with mock.patch("builtins.print") as printed:
            ui.output("hello")
        printed.assert_called_once_with("hello")

    def test_editors_return_false_or_none_when_idle(self):
        self.assertIsNone(ui.open_style_editor("|颜色1 = "))
        self.assertIsNone(ui.open_lyrics_editor("x"))
        self.assertFalse(ui.open_submit_editor("X", "正文", Path("out.wikitext")))

    def test_run_falls_back_without_gui(self):
        from utils import ui as facade
        calls = []
        with mock.patch.object(facade, "available", return_value=False):
            self.assertEqual(0, facade.run(lambda: calls.append("flow")))
        self.assertEqual(["flow"], calls)


class RunCancelTest(TestCase):
    """放弃正在跑的那一轮：`utils/ui/__init__.py` 的取消开关（「清除对话记录」用）。"""

    def setUp(self):
        self.ui = ui
        self.addCleanup(self._forget_run)

    def _forget_run(self):
        """`begin_run()` 是给流程线程用的：单测里用过之后要把本线程的开关收掉，
        否则后面所有 `check_run_cancelled()` 都会跟着抛。"""
        ui._run_local.run = None
        with ui._run_lock:
            ui._current_run = None

    def test_ask_raises_after_the_run_is_cancelled(self):
        self.ui.begin_run()
        self.assertFalse(self.ui.run_cancelled())
        self.ui.cancel_run()
        self.assertTrue(self.ui.run_cancelled())
        with self.assertRaises(self.ui.RunCancelled):
            self.ui.ask_response("歌名？")
        with self.assertRaises(self.ui.RunCancelled):
            self.ui.ask_choices("要吗？", ["要", "不要"])
        with self.assertRaises(self.ui.RunCancelled):
            self.ui.ask_multiline("把歌词贴进来")

    def test_request_wait_raises_when_the_run_was_cancelled(self):
        """流程正卡在某个提问上时被放弃：`wait()` 要马上抛，别把回答当成真的。"""
        from utils.ui.window import PromptRequest
        self.ui.begin_run()
        request = PromptRequest(kind="response", prompt="歌名？")
        request.done("上一首歌")                    # 界面那边把悬着的请求放掉
        self.ui.cancel_run()
        with self.assertRaises(self.ui.RunCancelled):
            request.wait()

    def test_a_new_run_does_not_reopen_the_old_one(self):
        """每轮流程有自己的开关：开新一轮不会把旧那一轮「取消标记」清掉。"""
        self.ui.begin_run()
        old = self.ui._run_local.run
        self.ui.begin_run()                        # 新一轮（同一线程里模拟）
        self.ui.cancel_run()                       # 只会取消「当前」那一轮
        self.assertFalse(old.cancelled.is_set())
        self.assertTrue(self.ui.run_cancelled())

    def test_without_a_run_nothing_is_cancelled(self):
        self.assertFalse(self.ui.run_cancelled())
        self.ui.cancel_run()                       # 没有一轮在跑时调用也不该炸
        self.ui.check_run_cancelled()


class PromptRoutingTest(TestCase):
    """utils/helpers.py 里的 prompt_* 在 GUI 模式下要转到门面。"""
    def test_response_goes_to_gui(self):
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "ask_response", return_value="歌曲名") as ask, \
             mock.patch.object(helpers, "save_input"):
            self.assertEqual("歌曲名", helpers.prompt_response("名字", auto_strip=True))
        self.assertEqual("名字", ask.call_args.args[0])

    def test_choices_goes_to_gui(self):
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "ask_choices", return_value=2) as ask, \
             mock.patch.object(helpers, "save_input"):
            self.assertEqual(2, helpers.prompt_choices("选一个", ["甲", "乙"]))
        self.assertEqual(["甲", "乙"], ask.call_args.args[1])

    def test_multiline_goes_to_gui(self):
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "ask_multiline", return_value=["一", "二"]) as ask, \
             mock.patch.object(helpers, "save_input"):
            self.assertEqual(["一", "二"], helpers.prompt_multiline("粘进来"))
        self.assertEqual("粘进来", ask.call_args.args[0])

    def test_terminal_path_still_used_when_idle(self):
        with mock.patch.object(ui, "is_active", return_value=False), \
             mock.patch.object(helpers, "get_input", return_value="终端里的答案"), \
             mock.patch.object(helpers, "save_input"):
            self.assertEqual("终端里的答案", helpers.prompt_response("名字"))


class WindowTest(TestCase):
    """主窗口：提问跨线程、编辑器页按需点亮。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils.ui.window import MainWindow
        reset_font_scale()            # 缩放系数是全局状态，每个用例都从 1.0 开始
        self.window = MainWindow()

    def tearDown(self):
        self.window.deleteLater()
        self.app.processEvents()
        reset_font_scale()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_submit_notifications_go_to_the_toaster(self):
        """提交页的通知要接到主窗口右下角的通知区（用户 2026-09-29 要求）。"""
        from utils.ui import toast as toast_module
        self.assertIsInstance(self.window.toaster, toast_module.Toaster)
        self.window.panels["submit"].notified.emit("已提交「A」", "ok")
        self.assertEqual(1, self.window.toaster.pending)
        self.assertEqual("已提交「A」", self.window.toaster._current.label.text())
        self.window.toaster._current.dismiss()
        self.app.processEvents()

    def test_fonts_grow_and_shrink_with_the_window(self):
        """窗口变大→字号跟着变大，缩回去→字号也缩回去（对话记录/日志等）。"""
        from utils.ui import theme
        tab = self.window.prompt_tab
        self.window.resize(1100, 768)
        self.window.show()
        self.app.processEvents()
        base = tab.history.font().pixelSize()
        self.assertEqual(theme.font_px(theme.HISTORY_FONT_PX), base)
        self.window.resize(1500, 1080)
        self.app.processEvents()
        self.assertGreater(theme.scale(), 1.0)
        self.assertGreater(tab.history.font().pixelSize(), base, "大窗口字要大")
        self.window.resize(1100, 768)
        self.app.processEvents()
        self.assertEqual(base, tab.history.font().pixelSize(), "缩回来字号也要回得去")

    def test_font_scale_applies_to_app_font_too(self):
        """应用级字体（按钮、标签…那些没单独设字号的）也要跟着缩放。"""
        from PyQt5 import QtWidgets
        from utils.ui import theme
        self.window.resize(1500, 1080)
        self.window.show()
        self.app.processEvents()
        self.assertGreater(theme.scale(), 1.0)
        app_font = QtWidgets.QApplication.instance().font()
        self.assertGreater(app_font.pointSizeF(), theme.APP_FONT_PT,
                           "QSS 里不再写死字号，应用字体才是基准")

    def test_font_scale_can_be_turned_off(self):
        from utils.ui import theme
        with mock.patch("config.config.get_config") as get_config:
            get_config.return_value = SimpleNamespace(font_scale_with_window=False)
            self.window.resize(1500, 1080)
            self.window.show()
            self.app.processEvents()
            self.window._apply_font_scale()
        self.assertEqual(1.0, theme.scale(), "关掉开关就该一直用基准字号")


    def test_panels_are_registered_but_disabled(self):
        self.assertEqual({"style", "lyrics", "submit", "producer", "producer-style"},
                         set(self.window.panels))
        for key in self.window.panels:
            index = self.window.page_index(key)
            self.assertFalse(self.window.tabs.isTabEnabled(index))

    def test_ask_response_crosses_threads(self):
        answers = []

        def flow():
            answers.append(self.window.ask_response("歌名？", auto_strip=True))

        thread = threading.Thread(target=flow)
        thread.start()
        self.assertTrue(_pump(lambda: self.window.prompt_tab._request is not None))
        self.window.prompt_tab.text_input.setText("  初音未来的消失  ")
        self.window.prompt_tab.submit_text()
        thread.join(timeout=5)
        self.assertEqual(["初音未来的消失"], answers)

    def test_ask_choices_returns_index(self):
        answers = []

        def flow():
            answers.append(self.window.ask_choices("引擎？", ["CeVIO", "Synthesizer V"]))

        thread = threading.Thread(target=flow)
        thread.start()
        self.assertTrue(_pump(lambda: self.window.prompt_tab._request is not None))
        self.window.prompt_tab._choose(2)
        thread.join(timeout=5)
        self.assertEqual([2], answers)

    def test_ask_multiline_stops_at_blank_line(self):
        answers = []

        def flow():
            answers.append(self.window.ask_multiline("粘歌词"))

        thread = threading.Thread(target=flow)
        thread.start()
        self.assertTrue(_pump(lambda: self.window.prompt_tab._request is not None))
        self.window.prompt_tab.multiline_input.setPlainText("第一行\n第二行\n\n后面的不要")
        self.window.prompt_tab.submit_multiline()
        thread.join(timeout=5)
        self.assertEqual([["第一行", "第二行"]], answers)

    def test_style_editor_request_opens_tab(self):
        results = []

        def flow():
            results.append(self.window.run_style_editor("|颜色1 = #1e90ff;", None, True))

        thread = threading.Thread(target=flow)
        thread.start()
        panel = self.window.panels["style"]
        self.assertTrue(_pump(lambda: self.window.tabs.currentIndex() == self.window.page_index("style")))
        self.assertTrue(panel.hover_check.isChecked(), "开关初始值要传进界面")
        self.assertIn("|颜色1 = #1e90ff;", panel.wiki_edit.toPlainText())
        panel._on_save()
        thread.join(timeout=5)
        self.assertEqual(1, len(results))
        text, hover = results[0]
        self.assertIn("|颜色1 = #1e90ff;", text)
        self.assertTrue(hover)

    def test_log_tab_filters_debug(self):
        self.window.append_log("普通信息")
        self.window.log_tab.append("调试信息", "DEBUG")
        self.assertNotIn("调试信息", self.window.log_tab.view.toPlainText())
        self.window.log_tab.debug_check.setChecked(True)
        self.window.log_tab.append("调试信息", "DEBUG")
        self.assertIn("调试信息", self.window.log_tab.view.toPlainText())

    def test_settings_tab_is_hidden_until_asked(self):
        """「设置」页不是工作流的一环：默认不在标签栏上，点齿轮才叫出来（用户 2026-09 要求）。"""
        index = self.window.page_index("settings")
        self.assertGreaterEqual(index, 0, "页还在，只是没挂到标签栏上")
        self.assertFalse(self.window.tabs.isTabVisible(index))
        self.assertFalse(self.window.settings_visible())
        self.assertNotEqual(index, self.window.tabs.currentIndex())
        # 其它三页仍然是「按流程点亮」，设置页那一格被藏掉了所以只会看到 3 个标签
        for key in self.window.panels:
            self.assertFalse(self.window.tabs.isTabEnabled(self.window.page_index(key)))
        labels = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())
                  if self.window.tabs.isTabVisible(i)]
        self.assertNotIn("设置", labels)

    def test_pages_are_scrollable_so_the_window_can_be_dragged_narrow(self):
        """窗口要能拖窄（用户 2026-09 反馈）：每页都套了滚动区，不会被内容顶宽。"""
        for key in ("style", "lyrics", "submit", "settings"):
            index = self.window.page_index(key)
            self.assertGreaterEqual(index, 0, f"{key} 页要在标签栏里")
            self.assertIsInstance(self.window.tabs.widget(index), QtWidgets.QScrollArea,
                                  f"{key} 页外面要套滚动区，否则它会把窗口的最小宽度顶上去")
        self.assertLessEqual(self.window.minimumWidth(), 760, "最小宽度要留出拖动余地")
        self.window.show()
        self.window.resize(760, 700)
        self.app.processEvents()
        self.assertLessEqual(self.window.width(), 780, "拖到 760 就该能拖到 760")

    def test_error_status_keeps_the_detail_in_the_tooltip(self):
        """长报错只显示前半句，括号里的细节挂 tooltip（用户 2026-09 要求）。"""
        detail = ("网络请求失败：('Connection aborted.', RemoteDisconnected("
                  "'Remote end closed connection without response'))")
        lyrics = self.window.panels["lyrics"]
        lyrics.set_status(detail, "err")
        label = lyrics.status_label
        self.assertEqual("网络请求失败", label.text())
        self.assertIn("Connection aborted", label.toolTip())
        # 正常短提示不该被拆，也不该挂着 tooltip
        lyrics.set_status("来源：手动粘贴")
        self.assertEqual("来源：手动粘贴", label.text())
        self.assertEqual("", label.toolTip())
        # 成功的提示（带本地路径）照旧原样显示，不藏进 tooltip
        saved = r"已保存到本地文件：C:\Users\me\AppData\Local\Temp\some-song.wiki"
        lyrics.set_status(saved, "ok")
        self.assertEqual(saved, label.text())
        self.assertEqual("", label.toolTip())
        # 提交页的报错同样只留前半句
        submit = self.window.panels["submit"]
        submit.set_status("提交失败：" + detail, "err")
        self.assertEqual("提交失败", submit.status_label.text())
        self.assertIn("Connection aborted", submit.status_label.toolTip())

    def test_settings_button_shows_and_switching_away_hides(self):
        self.window.tabs.setCurrentIndex(self.window.page_index("style"))
        self.window.settings_button.click()                 # 侧栏齿轮
        self.assertTrue(self.window.settings_visible())
        self.assertEqual(self.window.page_index("settings"), self.window.tabs.currentIndex())
        self.window.tabs.setCurrentIndex(self.window.page_index("style"))   # 切到别的页
        self.assertFalse(self.window.settings_visible(), "切走就该收回去")

    def test_settings_saved_hides_the_tab_and_goes_back(self):
        self.window.tabs.setCurrentWidget(self.window.log_tab)
        self.window.settings_button.click()
        with mock.patch.object(self.window, "_apply_font_scale"):    # 本用例只关心显隐
            self.window.settings_panel.saved.emit()                  # 保存成功才会发这个信号
        self.assertFalse(self.window.settings_visible())
        self.assertIs(self.window.log_tab, self.window.tabs.currentWidget(),
                      "关掉后回到进来之前那一页")
        # 再点一次还能正常叫出来
        self.window.settings_button.click()
        self.assertTrue(self.window.settings_visible())

    def test_settings_save_failure_keeps_the_tab_visible(self):
        """保存失败（不发 saved 信号）时不能收起来，否则用户看不到「保存失败：…」那句。"""
        self.window.settings_button.click()
        with mock.patch("utils.ui.settings_panel.save_config_values", return_value=False), \
             mock.patch("utils.ui.settings_panel.save_credentials", return_value=False):
            self.assertFalse(self.window.settings_panel.save())
        self.assertTrue(self.window.settings_visible())
        self.assertIn("保存失败", self.window.settings_panel.status_label.text())

    def test_status_text_lands_in_status_strip(self):
        self.window.set_status("干活中…")
        self.assertEqual("干活中…", self.window.status_label.text())
        self.window.append_log("一行日志")
        self.assertIn("一行日志", self.window.log_tab.view.toPlainText())

    # —— 左侧竖栏 / 头像 ——
    def test_clear_button_clears_current_input(self):
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        tab.start_request(PromptRequest(kind="multiline", prompt="把歌词整段粘进来"))
        tab.text_input.setText("初音未来的消失")
        tab.multiline_input.setPlainText("啊啊啊")
        tab.hint.setText("「x」不符合要求")
        tab.text_clear_button.click()
        self.assertEqual("", tab.text_input.text())
        self.assertEqual("", tab.multiline_input.toPlainText())
        self.assertEqual("", tab.hint.text())
        tab._finish([])                                # 收尾，别留下未完成的请求

    def test_clear_button_sits_with_the_input(self):
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        request = PromptRequest(kind="response", prompt="歌名？")
        tab.start_request(request)
        self.assertTrue(tab.text_clear_button.isEnabled())
        self.assertEqual("清空", tab.text_clear_button.text())
        self.assertEqual("清空", tab.multiline_clear_button.text(),
                         "多行输入页也有清空")
        tab.text_input.setText("x")
        tab.submit_text()
        self.assertEqual("x", request.value)

    def test_choice_page_has_no_clear_button(self):
        from PyQt5 import QtWidgets
        labels = [button.text() for button in
                  self.window.prompt_tab.choices_page.findChildren(QtWidgets.QPushButton)]
        self.assertNotIn("清空", labels, "选项页没什么可清的")

    def test_text_input_occupies_a_whole_row(self):
        """单行输入：输入框独占一整行，三颗按钮在下面一行靠右（用户 2026-09 指定）。"""
        tab = self.window.prompt_tab
        layout = tab.text_page.layout()
        self.assertIs(tab.text_input, layout.itemAt(0).widget())
        row = layout.itemAt(1).layout()
        self.assertIsNotNone(row, "第二行是按钮行")
        self.assertIsNotNone(row.itemAt(0).spacerItem(), "按钮行开头留白 → 按钮靠右下")
        self.assertEqual(["清空", "确定", "清除对话记录"],
                         [row.itemAt(index).widget().text() for index in range(1, row.count())])
        self.assertIsNotNone(layout.itemAt(layout.count() - 1).spacerItem(),
                             "末尾留白，高出来的高度不该把输入框和按钮撑开")

    def test_multiline_page_has_the_same_arrangement(self):
        """多行页同样：输入框一整行、按钮一行靠右。"""
        tab = self.window.prompt_tab
        layout = tab.multiline_page.layout()
        self.assertEqual(2, layout.count(), "第一行输入框、第二行按钮")
        self.assertIs(tab.multiline_input, layout.itemAt(0).widget())
        row = layout.itemAt(1).layout()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row.itemAt(0).spacerItem(), "按钮行开头留白 → 按钮靠右下")
        self.assertEqual(["清空", "完成", "清除对话记录"],
                         [row.itemAt(index).widget().text() for index in range(1, row.count())])

    def test_input_wider_than_the_buttons(self):
        """真正显示出来时输入框应该占满那一行（按钮不再挤在同一行里）。"""
        self.window.resize(1100, 768)
        self.window.show()
        self.app.processEvents()
        tab = self.window.prompt_tab
        self.assertGreater(tab.text_input.width(), 3 * tab.text_button.width(),
                           "输入框要占掉按钮让出来的那一整行")

    def test_input_area_stays_compact(self):
        """输入区不许往下长：多行框有上限，高出来的部分全给对话记录。"""
        from PyQt5 import QtWidgets
        tab = self.window.prompt_tab
        self.assertLessEqual(tab.multiline_input.maximumHeight(), 90)
        self.assertGreaterEqual(tab.multiline_input.maximumHeight(),
                                tab.multiline_input.minimumHeight())
        self.assertEqual(QtWidgets.QSizePolicy.Maximum,
                         tab.stack.sizePolicy().verticalPolicy())

    def test_clear_shortcut_exists(self):
        key = self.window.prompt_tab.clear_shortcut.key().toString().lower()
        self.assertEqual("ctrl+l", key.replace(" ", ""))

    def test_history_font_is_bigger_than_body(self):
        from utils.ui import theme
        tab = self.window.prompt_tab
        self.assertGreater(theme.HISTORY_FONT_PX, theme.FONT_SIZE_PX, "对话记录要比正文大")
        self.assertEqual(theme.font_px(theme.HISTORY_FONT_PX), tab.history.font().pixelSize())

    def test_first_choice_button_answers_one(self):
        """回归：以前按钮编号从 0 起，点「是」返回 0，流程把它当成「否」，就不弹歌词窗口了。"""
        from PyQt5 import QtWidgets
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        request = PromptRequest(kind="choices", prompt="是否手动输入中文翻译？",
                                choices=["是", "否"])
        tab.start_request(request)
        buttons = [tab.choices_layout.itemAt(index).widget()
                   for index in range(tab.choices_layout.count())
                   if isinstance(tab.choices_layout.itemAt(index).widget(),
                                 QtWidgets.QPushButton)]
        self.assertEqual(["1. 是", "2. 否"], [button.text() for button in buttons])
        buttons[0].click()                      # 点「是」
        self.assertEqual(1, request.value, "点第一个选项要返回 1（终端里 1 就是「是」）")

    def test_allow_zero_option_is_numbered_zero(self):
        from PyQt5 import QtWidgets
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        request = PromptRequest(kind="choices", prompt="选一个", choices=["甲", "乙"],
                                allow_zero=True)
        tab.start_request(request)
        items = [tab.choices_layout.itemAt(index).widget()
                 for index in range(tab.choices_layout.count())]
        buttons = [item for item in items if isinstance(item, QtWidgets.QPushButton)]
        self.assertEqual(["0. 都不要（留空）", "1. 甲", "2. 乙"],
                         [button.text() for button in buttons])
        buttons[0].click()                      # 点「都不要」= 0
        self.assertEqual(0, request.value)

    def test_zero_is_ignored_when_not_allowed(self):
        """没有「都不要」这项的提问，绝不能给出 0（流程会把它当成别的分支）。"""
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        request = PromptRequest(kind="choices", prompt="该图片是否有出现歌姬？",
                                choices=["是", "否"])
        tab.start_request(request)
        tab._choose(0)                          # 以前那颗「取消本次选择」就是这么干的
        self.assertIsNone(request.value, "0 不该被当成答案")
        self.assertIs(tab._request, request, "提问还等着真正的答案")
        self.assertNotIn("都不要", tab.history.toPlainText())
        tab._choose(1)                          # 真答一句还是正常的
        self.assertEqual(1, request.value)

    def test_choice_page_has_no_cancel_button(self):
        from PyQt5 import QtWidgets
        tab = self.window.prompt_tab
        labels = [button.text() for button in
                  tab.choices_page.findChildren(QtWidgets.QPushButton)]
        self.assertEqual([], labels, "选项页只该有这一次提问的选项按钮")
        self.assertFalse(hasattr(tab, "choices_button"))

    def test_three_option_prompt_reflects_the_choice(self):
        """赛道这类三选一：编号从 1 起，对话记录里显示选中的那一个。"""
        from PyQt5 import QtWidgets
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        request = PromptRequest(kind="choices", prompt="请选择歌曲所属赛道：",
                                choices=["TOP100", "ROOKIE", "榜外"])
        tab.start_request(request)
        items = [tab.choices_layout.itemAt(index).widget()
                 for index in range(tab.choices_layout.count())]
        buttons = [item for item in items if isinstance(item, QtWidgets.QPushButton)]
        self.assertEqual(["1. TOP100", "2. ROOKIE", "3. 榜外"],
                         [button.text() for button in buttons])
        buttons[2].click()                      # 点第三个「榜外」
        self.assertEqual(3, request.value)
        self.assertIn("榜外", tab.history.toPlainText())

    def test_history_box_aligns_with_the_first_tab(self):
        """对话记录框的左右边界要和上面第一个标签的左右边界对齐。"""
        from PyQt5 import QtCore
        self.window.resize(1100, 768)
        self.window.show()
        self.app.processEvents()
        bar = self.window.tabs.tabBar()
        tab_left = bar.mapTo(self.window, QtCore.QPoint(bar.tabRect(0).left(), 0)).x()
        tab_right = bar.mapTo(self.window, QtCore.QPoint(bar.width(), 0)).x()
        history = self.window.prompt_tab.history
        self.assertEqual(tab_left, history.mapTo(self.window, QtCore.QPoint(0, 0)).x(),
                         "左边要对齐")
        self.assertEqual(tab_right,
                         history.mapTo(self.window, QtCore.QPoint(history.width(), 0)).x(),
                         "右边也要对齐")

    def test_clear_history_button_asks_before_clearing(self):
        tab = self.window.prompt_tab
        tab.append_history("记了一行")
        with mock.patch.object(tab, "_confirm_clear_history", return_value=True) as asked:
            tab.multiline_history_button.click()
        asked.assert_called_once()
        self.assertEqual("", tab.history.toPlainText())

    def test_clear_history_button_keeps_history_when_declined(self):
        tab = self.window.prompt_tab
        tab.append_history("记了一行")
        with mock.patch.object(tab, "_confirm_clear_history", return_value=False):
            tab.text_history_button.click()
        self.assertIn("记了一行", tab.history.toPlainText())

    def test_history_clear_button_is_red_and_sits_right_of_the_primary(self):
        from PyQt5 import QtWidgets

        def button_labels(layout):
            return [layout.itemAt(index).widget().text()
                    for index in range(layout.count())
                    if isinstance(layout.itemAt(index).widget(), QtWidgets.QPushButton)]

        tab = self.window.prompt_tab
        for button in (tab.text_history_button, tab.multiline_history_button):
            self.assertEqual("清除对话记录", button.text())
            self.assertEqual("true", button.property("danger"), "要求是红色按钮")
        # 用户要求：红色的「清除对话记录」和「完成 / 确定」对调位置 → 主按钮在左、红色的在最右
        self.assertEqual(["清空", "完成", "清除对话记录"],
                         button_labels(tab.multiline_page.layout().itemAt(1).layout()))
        self.assertEqual(["清空", "确定", "清除对话记录"],
                         button_labels(tab.text_page.layout().itemAt(1).layout()))

    def test_clear_history_button_is_back_after_finishing_on_the_choices_page(self):
        """跑完流程后「填写信息」页一定要看得见红色的「清除对话记录」（用户 2026-09 反馈）。

        流程最后常常问的是**选择题**（要不要上传封面、投稿文…），而那颗按钮只挂在输入页上：
        停在选项页的话，跑完就找不到它了 → 所以收尾时要切回单行输入页。
        """
        from PyQt5 import QtWidgets
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        self.window.show()
        tab.start_request(PromptRequest(kind="choices", prompt="要不要同时上传封面？",
                                        choices=["要", "不要"]))
        tab._choose(1)
        self.app.processEvents()
        self.assertFalse(tab.text_history_button.isVisible(), "问选择题时界面停在选项页上")
        with mock.patch.object(QtWidgets.QMessageBox, "exec_", return_value=0):
            self.window._on_done(None)
        self.app.processEvents()
        self.assertEqual(0, tab.stack.currentIndex(), "收尾要回到单行输入页")
        self.assertTrue(tab.text_history_button.isVisible(), "那颗按钮必须看得见")
        self.assertTrue(tab.text_history_button.isEnabled(), "流程结束也不该禁用它")
        self.assertEqual(0, tab.choices_layout.count(), "上一次的选项按钮别留着")
        self.assertIn("已完成", tab.question.text())
        # 而且点了真的还能用
        with mock.patch.object(tab, "_confirm_clear_history", return_value=True):
            tab.text_history_button.click()
        self.assertEqual("", tab.history.toPlainText())

    def test_failure_also_puts_the_input_area_back(self):
        """出错结束时同样收拾：回到单行页，那颗按钮照样在。"""
        from PyQt5 import QtWidgets
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        self.window.show()
        tab.start_request(PromptRequest(kind="choices", prompt="要不要上传投稿文？",
                                        choices=["要", "不要"]))
        tab._choose(2)
        with mock.patch.object(QtWidgets.QMessageBox, "critical", return_value=0):
            self.window._on_failed("炸了", "")
        self.app.processEvents()
        self.assertEqual(0, tab.stack.currentIndex())
        self.assertEqual(0, tab.choices_layout.count())
        self.assertIn("出错", tab.question.text())
        # 出错时界面停在「日志」页；用户切回「填写信息」时那颗按钮照样在
        self.window.tabs.setCurrentWidget(tab)
        self.app.processEvents()
        self.assertTrue(tab.text_history_button.isVisible())

    def test_three_gutters_line_up(self):
        """对话记录文字与输入框文字同一条竖线；问题那句话贴最左边。"""
        from utils.ui import theme
        tab = self.window.prompt_tab
        self.assertEqual(0.0, tab.history.document().documentMargin(), "文档自带边距会顶歪文字")
        self.assertIn(f"padding: 4px {theme.CONTENT_PAD_PX}px", tab.history.styleSheet())
        self.assertEqual(0, tab.question.indent(), "问题那句话要与左侧对齐")
        self.assertEqual(0, tab.hint.indent())

    def test_question_label_is_bigger_than_body(self):
        from utils.ui import theme
        tab = self.window.prompt_tab
        self.assertGreater(theme.QUESTION_FONT_PX, theme.FONT_SIZE_PX, "问题那句话要比正文大")
        self.assertLess(theme.QUESTION_FONT_PX, theme.HISTORY_FONT_PX, "别抢对话记录的风头")
        self.assertEqual(theme.font_px(theme.QUESTION_FONT_PX),
                         tab.question.font().pixelSize())
        self.assertTrue(tab.question.font().bold())

    def test_hint_takes_no_space_when_empty(self):
        from utils.ui.window import PromptRequest
        tab = self.window.prompt_tab
        self.assertTrue(tab.hint.isHidden(), "没提示时整行收起来，不白占高度")
        tab.start_request(PromptRequest(kind="response", prompt="歌名？",
                                        checker=lambda value: False))
        tab.text_input.setText("x")
        tab.submit_text()
        self.assertFalse(tab.hint.isHidden())
        self.assertIn("不符合要求", tab.hint.text())
        tab._set_hint("")
        self.assertTrue(tab.hint.isHidden())
        tab._finish("x")

    def test_clear_history_dialog_defaults_to_cancel(self):
        """弹窗默认按钮是「取消」，回车不会误清。"""
        tab = self.window.prompt_tab
        tab.append_history("记了一行")
        captured = {}

        def fake_exec(box):
            captured["default"] = box.defaultButton().text()
            captured["title"] = box.windowTitle()
            captured["buttons"] = [button.text() for button in box.buttons()]
            return 0

        with mock.patch("PyQt5.QtWidgets.QMessageBox.exec_", fake_exec):
            self.assertFalse(tab.clear_history())
        self.assertEqual("取消", captured["default"])
        self.assertEqual("清除对话记录", captured["title"])
        self.assertIn("清除", captured["buttons"])
        self.assertIn("取消", captured["buttons"])
        self.assertIn("记了一行", tab.history.toPlainText())

    def test_clear_history_skips_dialog_when_configured(self):
        """设置里关掉确认后直接清掉，不弹窗。"""
        tab = self.window.prompt_tab
        tab.append_history("记了一行")
        with mock.patch("config.config.get_config") as get_config, \
             mock.patch("PyQt5.QtWidgets.QMessageBox.exec_") as exec_:
            get_config.return_value.confirm_clear_history = False
            self.assertTrue(tab.clear_history())
            exec_.assert_not_called()
        self.assertEqual("", tab.history.toPlainText())

    def test_clear_history_restarts_the_flow_and_wipes_every_page(self):
        """「清除对话记录」之后要能从头再来：记录清空 + 各页清干净 + 重新跑流程。

        用户 2026-09 报的 bug：清完只把记录清了，填写信息栏不再问「歌名？」，
        提交页还摆着上一首歌的条目与预览。
        """
        window = self.window
        started = []
        window._start_flow = lambda key="entry": started.append(key)
        window._flow_running = False
        window._finished = True                     # 上一轮已经结束
        tab = window.prompt_tab
        tab.append_history("上一轮的记录")
        submit = window._panels["submit"]
        submit.title_label.setText("条目：上一首歌")
        submit.editor.setPlainText("上一首歌的正文")
        lyrics = window._panels["lyrics"]
        lyrics.jap_edit.setPlainText("きみの")
        lyrics._marks["jap"]["0"] = ["初音未来"]
        with mock.patch.object(tab, "_confirm_clear_history", return_value=True):
            self.assertTrue(tab.clear_history())
        self.app.processEvents()
        self.assertEqual("", tab.history.toPlainText())
        self.assertEqual(1, len(started), "要重新跑一遍生成流程")
        self.assertFalse(window._finished, "新一轮还会报 done / failed")
        self.assertEqual("", submit.editor.toPlainText())
        self.assertEqual("条目：—", submit.title_label.text())
        self.assertFalse(submit.submit_button.isEnabled())
        self.assertEqual("", lyrics.jap_edit.toPlainText())
        self.assertEqual({"jap": {}, "chs": {}}, lyrics._marks)
        self.assertIn("重新开始", window.status_label.text())

    def test_clear_history_aborts_a_running_flow_and_starts_over(self):
        """这一轮还在跑也照清：先放弃它，再把界面恢复成刚打开的样子，然后重开一轮。

        用户 2026-09 要求：「如果这一轮还在跑，那就重置到刚打开界面的时候，
        将这一轮生成的内容全部清除。」
        """
        from utils import ui as ui_facade
        from utils.ui.window import PromptRequest
        window = self.window
        started = []
        window._start_flow = lambda key="entry": started.append(key)
        window._flow_running = True
        tab = window.prompt_tab
        tab.append_history("这一轮的记录")
        request = PromptRequest(kind="response", prompt="歌名？")
        tab.start_request(request)
        window.append_log("这一轮的日志")
        submit = window._panels["submit"]
        submit.editor.setPlainText("上一首歌的正文")
        with mock.patch.object(ui_facade, "cancel_run") as cancel, \
             mock.patch.object(tab, "_confirm_clear_history", return_value=True):
            self.assertTrue(tab.clear_history())
        self.app.processEvents()
        cancel.assert_called_once()                  # 让旧的那一轮停下来
        self.assertTrue(request.event.is_set(), "悬着的提问要放掉，不然旧线程一直等")
        self.assertEqual(1, len(started), "要重新跑一遍生成流程")
        self.assertEqual("", tab.history.toPlainText())
        self.assertEqual("准备中…", tab.question.text())
        self.assertFalse(tab.text_input.isEnabled(), "复位后不忙：等新一轮把问题问出来")
        self.assertEqual("", submit.editor.toPlainText())
        self.assertNotIn("这一轮的日志", window.log_tab.view.toPlainText())
        self.assertIn("恢复成刚打开的样子", window.log_tab.view.toPlainText())
        self.assertFalse(window._finished)

    def test_clear_history_without_a_flow_just_clears(self):
        """没有流程可跑（单测里手搓的窗口）时不要炸。"""
        window = self.window
        window._start_flow = None
        window._flow_running = False
        tab = window.prompt_tab
        tab.append_history("记了一行")
        with mock.patch.object(tab, "_confirm_clear_history", return_value=True):
            self.assertTrue(tab.clear_history())
        self.assertEqual("", tab.history.toPlainText())
        self.assertEqual("已清除对话记录", window.status_label.text())

    def test_sidebar_lists_the_entry_feature(self):
        self.assertEqual(["entry", "producer"], self.window.sidebar.keys())
        self.assertEqual("entry", self.window.sidebar.current_feature())
        self.assertIs(self.window.settings_button, self.window.sidebar.settings_button,
                      "设置齿轮就是侧栏底部那颗按钮")

    def test_feature_switch_keeps_workflow_page(self):
        self.window.sidebar.feature_selected.emit("entry")
        self.assertIs(self.window._feature_pages["entry"],
                      self.window.feature_stack.currentWidget())
        self.assertIn("已切换到", self.window.status_label.text())

    def test_producer_feature_shows_only_its_own_pages(self):
        """第二个功能（生成P主模板）：曲目 / 样式 / 提交可见，歌曲那几页收起。"""
        self.window.sidebar.feature_selected.emit("producer")
        self.assertEqual("producer", self.window.current_feature())
        for key in ("producer", "producer-style", "submit"):
            self.assertTrue(self.window.tabs.isTabVisible(self.window.page_index(key)), key)
        for key in ("style", "lyrics"):
            self.assertFalse(self.window.tabs.isTabVisible(self.window.page_index(key)), key)
        # 两个功能各有一页叫「样式」，同一时刻只看得见其中一个
        labels = [self.window.tabs.tabText(index)
                  for index in range(self.window.tabs.count())
                  if self.window.tabs.isTabVisible(index)]
        self.assertEqual(["填写信息", "日志", "曲目", "样式", "提交"], labels)
        # 切回去：歌曲那几页回来，P主那两页收起
        self.window.sidebar.feature_selected.emit("entry")
        self.assertEqual("entry", self.window.current_feature())
        for key in ("style", "lyrics"):
            self.assertTrue(self.window.tabs.isTabVisible(self.window.page_index(key)), key)
        for key in ("producer", "producer-style"):
            self.assertFalse(self.window.tabs.isTabVisible(self.window.page_index(key)), key)

    def test_switching_feature_restarts_the_flow(self):
        """切功能 = 停掉当前那一轮 + 复位界面 + 跑新功能的流程（launch() 挂的 _start_flow）。"""
        started = []
        self.window._start_flow = started.append
        self.window._flow_running = True
        self.window.sidebar.feature_selected.emit("producer")
        self.assertEqual(["producer"], started)
        self.assertIn("已切换到：生成P主模板", self.window.status_label.text())
        self.assertIn("已切换到「生成P主模板」", self.window.log_tab.view.toPlainText())
        self.assertIn("上一轮生成已放弃", self.window.log_tab.view.toPlainText())
        # 再点同一个功能不重启（免得白扔掉正在跑的一轮）
        self.window.sidebar.feature_selected.emit("producer")
        self.assertEqual(["producer"], started)

    def test_producer_panel_requests_round_trip(self):
        """曲目页 / 样式页的请求：把 work 交给面板，用户保存后把结果交回流程。"""
        from utils import producer_template as pt
        work = pt.ProducerWork(artist=pt.ProducerArtist(id=1, name="雄之助"))
        work_answers, style_answers = [], []
        thread = threading.Thread(target=lambda: work_answers.append(
            self.window.run_producer_works(work)))
        thread.start()
        panel = self.window.panels["producer"]
        self.assertTrue(_pump(lambda: self.window.tabs.currentIndex()
                              == self.window.page_index("producer")))
        self.assertTrue(panel.isEnabled())
        headers = [panel.table.horizontalHeaderItem(index).text()
                   for index in range(panel.table.columnCount())]
        self.assertEqual(["年份", "中文条目", "日文原名", "投稿日期", "状态"], headers)
        panel.saved.emit(work)
        thread.join(timeout=5)
        self.assertEqual([work], work_answers)

        thread = threading.Thread(target=lambda: style_answers.append(
            self.window.run_producer_style(work)))
        thread.start()
        style_panel = self.window.panels["producer-style"]
        self.assertTrue(_pump(lambda: self.window.tabs.currentIndex()
                              == self.window.page_index("producer-style")))
        style_panel.fields["titleBg"].set_value("#123456", notify=True)
        style_panel._on_save()
        thread.join(timeout=5)
        self.assertEqual(1, len(style_answers))
        self.assertEqual("#123456", style_answers[0]["titleBg"])

    def test_producer_pages_have_scroll_areas(self):
        for key in ("producer", "producer-style"):
            index = self.window.page_index(key)
            self.assertGreaterEqual(index, 0, f"{key} 页要在标签栏里")
            self.assertIsInstance(self.window.tabs.widget(index), QtWidgets.QScrollArea)

    def test_unknown_feature_is_ignored(self):
        current = self.window.feature_stack.currentWidget()
        self.window.sidebar.feature_selected.emit("不存在的功能")
        self.assertIs(current, self.window.feature_stack.currentWidget())

    def test_avatar_defaults_to_logged_out(self):
        self.assertFalse(self.window.avatar_button.logged_in)
        self.assertIn("未登录", self.window.avatar_button.toolTip())

    def test_avatar_click_without_credentials_opens_settings(self):
        with mock.patch("config.config.get_wiki_credentials", return_value=("", "")):
            self.window.avatar_button.click()
        self.assertEqual(self.window.page_index("settings"), self.window.tabs.currentIndex())
        self.assertIn("还没配置", self.window.status_label.text())

    def test_avatar_login_success_updates_bar(self):
        state = {"logged_in": False, "user": ""}

        def fake_login(username, password):
            state["logged_in"] = True
            state["user"] = username
            return True

        with mock.patch("config.config.get_wiki_credentials", return_value=("TimeRen", "pw")), \
             mock.patch("utils.login.login", side_effect=fake_login), \
             mock.patch("utils.login.is_logged_in", side_effect=lambda: state["logged_in"]), \
             mock.patch("utils.login.current_user", side_effect=lambda: state["user"]), \
             mock.patch("utils.ui.avatar.fetch_avatar", return_value=(None, "")):
            self.window.avatar_button.click()
            self.assertTrue(_pump(lambda: self.window.avatar_button.logged_in))
        self.assertIn("TimeRen", self.window.avatar_button.toolTip())
        self.assertIn("已登录 Vocawiki", self.window.status_label.text())

    def test_avatar_failure_is_explained_in_the_log(self):
        """取不到站点头像时要在「日志」页说清原因（DEBUG 级别默认看不见）。"""
        with mock.patch("utils.login.is_logged_in", return_value=True), \
             mock.patch("utils.login.current_user", return_value="TimeRen"), \
             mock.patch.object(self.window, "append_log") as append:
            self.window._on_avatar_loaded("TimeRen", None)
        message, level = append.call_args.args[0], append.call_args.args[1]
        self.assertEqual("INFO", level, "这条得让用户看得见")
        self.assertIn("Cloudflare", message)
        self.assertIn("用户名", message, "也要提一句可能是名字在站点上查不到")
        self.assertIn("首字母", message)

    def test_avatar_uses_webengine_only_when_available(self):
        """普通请求没拿到时，只有在能起 WebEngine 的情况下才去借它取图。"""
        url = "https://voca.wiki/images/avatars/35/128.png"
        with mock.patch.object(self.window, "_load_avatar_with_webengine") as loader, \
             mock.patch.object(self.window, "_on_avatar_loaded") as loaded, \
             mock.patch("utils.ui.avatar.WebEngineLoader.available", return_value=True):
            self.window._on_avatar_fetched("TimeRen", (None, url))
        loader.assert_called_once_with("TimeRen", url)
        loaded.assert_not_called()
        with mock.patch.object(self.window, "_load_avatar_with_webengine") as loader, \
             mock.patch.object(self.window, "_on_avatar_loaded") as loaded, \
             mock.patch("utils.ui.avatar.WebEngineLoader.available", return_value=False):
            self.window._on_avatar_fetched("TimeRen", (None, url))
        loader.assert_not_called()
        loaded.assert_called_once_with("TimeRen", None)

    def test_avatar_bytes_from_webengine_reach_the_sidebar(self):
        """WebEngine 抠出来的像素：写进缓存 + 立刻换掉侧栏头像。"""
        from PyQt5 import QtCore, QtGui
        image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#00ff00"))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        self.assertTrue(image.save(buffer, "PNG"))
        data = bytes(buffer.data())
        with mock.patch("utils.login.is_logged_in", return_value=True), \
             mock.patch("utils.login.current_user", return_value="TimeRen"), \
             mock.patch("utils.ui.avatar.store_bytes") as store:
            self.window._on_avatar_from_webengine("TimeRen", data)
        store.assert_called_once_with("TimeRen", 128, data)
        self.assertEqual(data, self.window._avatar_image)

    def test_avatar_login_failure_shows_dialog(self):
        captured = {}

        def fake_exec(box):
            captured["title"] = box.windowTitle()
            return 0

        with mock.patch("config.config.get_wiki_credentials", return_value=("TimeRen", "wrong")), \
             mock.patch("utils.login.login", return_value=False), \
             mock.patch("PyQt5.QtWidgets.QMessageBox.exec_", fake_exec):
            self.window.avatar_button.click()
            self.assertTrue(_pump(lambda: "title" in captured))
        self.assertEqual("登录失败", captured.get("title"))
        self.assertIn("登录 Vocawiki 失败", self.window.status_label.text())

    def test_avatar_click_when_logged_in_opens_account_menu(self):
        with mock.patch("utils.login.is_logged_in", return_value=True), \
             mock.patch.object(self.window, "_show_account_menu") as menu:
            self.window.avatar_button.click()
        menu.assert_called_once()

    def test_logout_returns_to_default_avatar(self):
        state = {"logged_in": True, "user": "TimeRen"}

        def fake_logout():
            state["logged_in"] = False
            state["user"] = ""
            return True

        with mock.patch("utils.login.is_logged_in", side_effect=lambda: state["logged_in"]), \
             mock.patch("utils.login.current_user", side_effect=lambda: state["user"]), \
             mock.patch("utils.ui.avatar.fetch_avatar", return_value=(None, "")):
            self.window._sync_avatar()
            self.assertTrue(self.window.avatar_button.logged_in)
            with mock.patch("utils.login.logout", side_effect=fake_logout):
                self.window.logout_vocawiki()
                self.assertTrue(_pump(lambda: not self.window.avatar_button.logged_in))
        self.assertIn("已退出", self.window.status_label.text())

    def test_report_error_shows_message(self):
        with mock.patch("PyQt5.QtWidgets.QMessageBox.critical") as critical:
            self.window.report_error("坏了")
        critical.assert_called_once()
        self.assertIn("坏了", self.window.prompt_tab.history.toPlainText())


class FontFamilyTest(TestCase):
    """「设置」页改应用字体 → 主窗口重套一次主题就全局生效；改窗口大小不许动字体。

    这里故意不建 MainWindow：`apply_theme()` 会把样式表重新发给整棵树，
    在一个真窗口上要十几秒（那几条字号缩放的用例就是这么贵的），
    本用例只验证 `_apply_font_scale()` 在两条路上的行为。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        reset_font_scale()
        from PyQt5 import QtWidgets
        self.holder = QtWidgets.QWidget()

    def tearDown(self):
        self.holder.deleteLater()
        self.app.processEvents()
        reset_font_scale()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def _config(self, **values):
        """假装成 config.yaml 里的值（`font_scale_with_window` 默认开着）。"""
        values.setdefault("font_scale_with_window", True)
        return SimpleNamespace(**values)

    def _pick_font(self, font_file=None, font_family=None, holder=None, **config):
        """走「设置页刚选好字体」那条路：`font_changed` → `_apply_font_scale(文件, 家族名)`。"""
        from utils.ui.window import MainWindow
        with mock.patch("config.config.get_config", return_value=self._config(**config)):
            MainWindow._apply_font_scale(self.holder if holder is None else holder,
                                        font_file, font_family)

    def _resize(self, holder=None, **config):
        """走「窗口大小变了」那条路：`_apply_font_scale()`（不带参数 = 只改字号）。"""
        from utils.ui.window import MainWindow
        with mock.patch("config.config.get_config", return_value=self._config(**config)):
            MainWindow._apply_font_scale(self.holder if holder is None else holder)

    def test_font_family_can_be_changed_from_settings(self):
        from PyQt5 import QtWidgets
        from utils.ui import theme
        self._pick_font(font_family="Some Font")
        self.assertEqual("Some Font", theme.font_family())
        self.assertIn('"Some Font"', QtWidgets.QApplication.instance().styleSheet())
        self.assertEqual("Some Font", QtWidgets.QApplication.instance().font().family())
        # 清空就回默认（这条只用 theme 层验证，免得再重套一次样式表）
        theme.set_font_family("")
        self.assertEqual(theme.DEFAULT_FONT_FAMILY, theme.font_family())

    def test_scale_switch_off_still_applies_the_font(self):
        from utils.ui import theme
        self._pick_font(font_family="Some Font", font_scale_with_window=False)
        self.assertEqual("Some Font", theme.font_family(), "字号不缩放不代表字体不换")

    def test_resize_without_scale_change_does_not_reapply_the_theme(self):
        from utils.ui import theme
        self._resize()                    # 先让缩放系数落到当前窗口尺寸那一档
        with mock.patch.object(theme, "apply_theme") as apply:
            self._resize()
        self.assertFalse(apply.called, "字号没变就别重套样式表")

    def test_resize_keeps_the_font_chosen_in_the_settings_page(self):
        """用户 2026-09 报的 bug：设置页刚选好字体（还没点保存）→ 改窗口大小 → 字体被打回默认。

        根因是缩放时回读 config.yaml 的 font_file / font_family。拖动窗口只是改字号，
        不应当动字体；config.yaml 里的字体在启动和保存时就已经生效了。
        """
        from utils.ui import theme
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        family = theme.load_font_file(font_file)
        self._pick_font(font_file=font_file, font_family=family)
        self.assertEqual(family, theme.font_family())
        # 配置里还是老字体（用户还没保存）→ 改窗口大小不能把刚选的字体盖掉
        self._resize(font_file="", font_family="Some Other Font")
        self.assertEqual(family, theme.font_family(), "改窗口大小又把字体打回 config 里的值了")
        self.assertEqual(font_file, theme.font_file())

    def test_font_file_beats_the_family_name(self):
        """设置页选了字体文件：文件里的家族名盖过 font_family（哪怕字体没装进系统）。"""
        from utils.ui import theme
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        family = theme.load_font_file(font_file)
        self._pick_font(font_file=font_file, font_family="Some Font")
        self.assertEqual(font_file, theme.font_file())
        self.assertEqual(family, theme.font_family(), "文件里的家族名要盖过 config 里写的名字")
        self.assertIn(f'"{family}"', theme.font_stack())

    def test_broken_font_file_falls_back_to_the_family_name(self):
        """选了已被删掉的字体文件：退回按 font_family 找系统字体，别把界面搞崩。"""
        from utils.ui import theme
        self._pick_font(font_file=str(Path(tempfile.gettempdir()) / "no-such-font.ttf"),
                        font_family="Some Font")
        self.assertEqual("Some Font", theme.font_family())

    def test_settle_layout_applies_pending_geometry_at_once(self):
        """`_settle_layout()` 要把队列里的布局请求跑掉（Qt 的布局默认是**延迟**的）。

        这是上面那条「字号变大后按钮得跟着变宽」的机制：字号变了以后几何要等下一轮事件循环
        才算，中间那一小段里控件还是旧宽度。离屏跑的时候字体可能压根没有，所以这条用例用
        `setMinimumWidth` 来造一个「尺寸提示变大了」的局面，跟字体无关、结果稳定。
        """
        from utils.ui.window import _settle_layout
        row = QtWidgets.QHBoxLayout(self.holder)
        row.addStretch(1)
        button = QtWidgets.QPushButton("清除对话记录", self.holder)
        row.addWidget(button)
        self.holder.resize(800, 200)
        self.holder.show()
        self.app.processEvents()
        before = button.width()
        button.setMinimumWidth(before + 80)          # 等价于「字变长了，尺寸提示跟着变」
        _settle_layout(self.holder)
        self.assertGreaterEqual(button.width(), before + 80,
                                "尺寸提示变了，布局却没当场跟上（会把按钮里的字裁掉）")

    def test_settle_layout_refreshes_the_layouts_of_sub_pages(self):
        """藏在子页面（滚动区 / 堆叠页）里的布局也要失效 + 重算。

        用户 2026-09 报的「提交到 Vocawiki」少最后一个字母：字号是在整棵树套样式表时**悄悄**
        换掉的（没走 `setFont`），主窗口自己 `updateGeometry()` 只让**祖先**那一串布局失效，
        提交页里那一层行布局的缓存还是旧字号量出来的宽度（实测：按钮一直卡在 165px、
        提示已经是 180px），于是最后一个字母被裁。修法是 `_settle_layout()` 往下走一遍：
        每个子控件的布局都 `invalidate()` + `activate()` 一次。
        """
        from utils.ui.window import _settle_layout

        class CountedRow(QtWidgets.QHBoxLayout):
            """数一数自己有没有被失效 / 重算。"""

            def __init__(self):
                super().__init__()
                self.invalidated = 0
                self.activated = 0

            def invalidate(self):
                self.invalidated += 1
                super().invalidate()

            def activate(self):
                self.activated += 1
                return super().activate()

        outer = QtWidgets.QVBoxLayout(self.holder)
        stack = QtWidgets.QStackedWidget(self.holder)
        outer.addWidget(stack)
        page = QtWidgets.QWidget(stack)
        row = CountedRow()
        page.setLayout(row)
        row.addWidget(QtWidgets.QLabel("x" * 4, page), 1)
        row.addWidget(QtWidgets.QPushButton("提交到 Vocawiki", page))
        stack.addWidget(page)
        self.holder.resize(900, 200)
        self.holder.show()
        self.app.processEvents()
        row.invalidated = 0
        row.activated = 0
        _settle_layout(self.holder)
        self.app.processEvents()
        self.assertGreater(row.invalidated, 0, "子页面里的布局没被失效，缓存还是旧尺寸")
        self.assertGreater(row.activated, 0, "子页面里的布局没被重算，控件宽度还是旧的")

    def test_bigger_font_relayouts_the_buttons_at_once(self):
        """字号变大后「清除对话记录」必须当场变宽。

        用户 2026-09 报的就是这个：字已经换成 13pt，按钮还留着 10.5pt 量出来的宽度，
        最后一个「录」正好被裁掉。布局是延迟的，所以 `_apply_font_scale()` 末尾要
        `_settle_layout()` 一次。
        """
        from utils.ui import theme
        from utils.ui.window import PromptTab
        tab = PromptTab()
        self.addCleanup(tab.deleteLater)
        tab.resize(1400, 900)
        tab.show()
        self.app.processEvents()
        theme.set_scale(1.0)
        theme.apply_theme(self.app)
        self.app.processEvents()
        button = tab.text_history_button
        small = button.fontMetrics().horizontalAdvance(button.text())
        self._resize(holder=tab)
        self.assertGreater(theme.scale(), 1.0, "这条用例要靠「字号变大」才有效")
        big = button.fontMetrics().horizontalAdvance(button.text())
        if big <= small:
            self.skipTest("离屏环境拿不到系统字体，字号变大时文字宽度并不会变")
        # 两边的内边距 + 边框一共 30px（QSS 里 padding: 5px 14px）
        self.assertGreaterEqual(button.width(), big + 28,
                                "按钮没跟着新字号变宽，文案会被裁掉")


class StylePanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        reset_font_scale()
        from utils.ui.style_panel import StylePanel
        self.panel = StylePanel()

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        reset_font_scale()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_start_parses_initial_text(self):
        self.panel.start({"initial": "|颜色1 = #1e90ff;\n  color: #ffffff;", "hover": False})
        self.assertEqual("#1e90ff", self.panel.states[0]["bgSolid"])
        self.assertEqual("#ffffff", self.panel.states[0]["color"])

    def test_widget_change_reaches_wikitext(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._select(0, section="songbox")
        self.panel.bg_field.set_value("#123456", 1.0, notify=True)
        self.assertIn("|颜色1 = #123456;", self.panel.wiki_edit.toPlainText())

    def test_global_edit_propagates_to_all_pills(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._select(-1, section="songbox")
        self.panel.bg_field.set_value("#abcdef", 1.0, notify=True)
        self.assertEqual(["#abcdef"] * 3, [state["bgSolid"] for state in self.panel.states])

    def test_global_border_color_reaches_all_pills(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._select(-1, section="songbox")
        self.panel.border_current_check.setChecked(False)
        self.panel.border_field.set_value("#ff0000", 1.0, notify=True)
        self.assertEqual(["#ff0000"] * 3, [state["borderColor"] for state in self.panel.states])

    def test_lyrics_target_output_switch(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._select("lyrOrig", section="lyrics")
        self.assertFalse(self.panel.enabled_check.isChecked())
        self.panel.enabled_check.setChecked(True)
        self.assertIn("|lstyle =", self.panel.wiki_edit.toPlainText())

    def test_reset_all_returns_defaults(self):
        self.panel.start({"initial": "|颜色1 = #1e90ff;", "hover": False})
        self.panel._reset_all()
        self.assertIn("|颜色1 = \n", self.panel.wiki_edit.toPlainText() + "\n")

    def test_save_emits_text_and_hover(self):
        received = []
        self.panel.saved.connect(received.append)
        self.panel.start({"initial": "|颜色1 = #1e90ff;", "hover": True})
        self.panel._on_save()
        self.assertEqual(1, len(received))
        self.assertIn("|颜色1 = #1e90ff;", received[0][0])
        self.assertTrue(received[0][1])

    def test_cancel_emits_cancelled(self):
        received = []
        self.panel.cancelled.connect(lambda: received.append(True))
        self.panel.start({"initial": "", "hover": False})
        self.panel._on_cancel()
        self.assertEqual([True], received)

    def test_cover_loading(self):
        from PyQt5 import QtGui
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cover.png"
            image = QtGui.QImage(8, 8, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#39c5bb"))
            image.save(str(path))
            self.panel.start({"initial": "", "cover": path, "hover": False})
            self.assertTrue(self.panel.cover_view.has_image())
            self.assertTrue(self.panel._ai_api._cover_image is not None)
            self.panel.cover_view.picked.emit("#123456")     # 没有吸管模式时什么也不做
            self.panel._pick_field = self.panel.bg_field
            self.panel.cover_view.picked.emit("#123456")
            self.assertEqual("#123456", self.panel.bg_field.value()[0])
            self.panel._clear_cover()
            self.assertFalse(self.panel.cover_view.has_image())

    def test_ai_panel_shows_context(self):
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "provider": "openai",
                                      "model": "deepseek-flash", "prompts": {"songbox": "提示"}}):
            self.panel.start({"initial": "", "hover": False})
        self.assertTrue(self.panel.ai_button.isEnabled())
        self.assertIn("deepseek-flash", self.panel.ai_tip.text())
        self.assertFalse(hasattr(self.panel, "ai_key_edit"), "密钥输入框已经挪到「设置」页")
        self.assertFalse(hasattr(self.panel, "ai_settings_button"), "跳转设置页的按钮已删掉")

    def test_ai_panel_disabled_reason(self):
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": False, "hidden": False,
                                      "reason": "请在 wiki_credentials.yaml 里填写 ai_api_key"}):
            self.panel.start({"initial": "", "hover": False})
        self.assertFalse(self.panel.ai_button.isEnabled())
        self.assertIn("ai_api_key", self.panel.ai_tip.text())

    def test_settings_changed_refreshes_ai_context(self):
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "model": "m"}) as context:
            self.panel.on_settings_changed()
        self.assertTrue(context.called)

    def test_ai_panel_defaults_to_the_current_object_and_no_color_only(self):
        """用户 2026-09 要求：范围默认「当前编辑对象」、只改颜色默认不勾选。"""
        self.assertEqual("cur", self.panel.ai_scope_combo.currentData())
        self.assertFalse(self.panel.ai_color_only_check.isChecked())
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}):
            self.panel.start({"initial": "", "hover": False})
        self.assertEqual("cur", self.panel.ai_scope_combo.currentData())
        self.assertFalse(self.panel.ai_color_only_check.isChecked())

    def test_start_resets_the_ai_scope(self):
        """上一首选的「全部」不该带到下一首（范围回到默认）。"""
        self.panel.ai_scope_combo.setCurrentIndex(1)
        self.panel.ai_color_only_check.setChecked(True)
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}):
            self.panel.start({"initial": "", "hover": False})
        self.assertEqual("cur", self.panel.ai_scope_combo.currentData())
        self.assertFalse(self.panel.ai_color_only_check.isChecked())

    def test_ai_generate_works_after_switching_to_the_introduction(self):
        """回归：Songbox 生成完切到 Introduction 再生成，以前会 `TypeError`（界面报错）。"""
        from PyQt5 import QtGui
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}), \
             mock.patch("utils.color_editor.EditorApi.ai_generate",
                        side_effect=[{"ok": True, "css": {"pill0": "color: #123456;"},
                                      "model": "m"},
                                     {"ok": True, "css": {"introLabel": "color: #654321;"},
                                      "model": "m"}]) as generate:
            self.panel.start({"initial": "", "hover": False})
            image = QtGui.QImage(4, 4, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#000000"))
            self.panel.cover_view._set_image(image)
            self.panel._select(0, section="songbox")
            self.panel._run_ai()
            self.assertTrue(_pump(lambda: self.panel.states[0]["color"] == "#123456"))
            self.panel._on_section_changed("intro")
            self.assertEqual("introLabel", self.panel.current)
            self.panel._run_ai()
            self.assertTrue(_pump(lambda: generate.call_count == 2))
            self.assertTrue(_pump(lambda: self.panel.tpl_states["introLabel"]["color"]
                                  == "#654321"))
        self.assertEqual(2, generate.call_count)
        self.assertEqual("#654321", self.panel.tpl_states["introLabel"]["color"])

    def test_ai_generate_introduction_shows_up_in_the_wikitext(self):
        """用户 2026-09 报：「AI 生成 Introduction 的颜色后毫无变化」。

        AI 返回的 CSS 里除两个颜色还有 padding / border / 圆角 / 阴影 / 字号字重，
        旧实现只把两个颜色写进 `|lbgcolor` / `|ltcolor`，其余全丢 —— 保存到站上等于没改。
        """
        from PyQt5 import QtGui
        initial = ("{{VOCALOID Songbox Introduction\n"
                   "|lbgcolor = #4a3e4d; padding: 6px 12px; border-radius: 4px 0 0 4px; "
                   "box-shadow: 2px 2px 5px rgba(0,0,0,0.2);\n"
                   "|ltcolor = #ffffff;\n")
        # 真实模型（deepseek-flash）对「把标签格改成深紫卡片」的回复
        reply = ("background-color: #2b2a3a; color: #e8e4f0; border: 1px solid #8a7fa8; "
                 "border-radius: 6px; padding: 6px 14px; font-size: 14px; font-weight: 600; "
                 "box-shadow: 0 2px 8px rgba(20, 18, 34, 0.6); opacity: 1;")
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}), \
             mock.patch("utils.color_editor.EditorApi.ai_generate",
                        return_value={"ok": True, "css": {"introLabel": reply}, "model": "m"}):
            self.panel.start({"initial": initial, "hover": False})
            self.panel._on_section_changed("intro")
            image = QtGui.QImage(4, 4, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#000000"))
            self.panel.cover_view._set_image(image)
            self.panel._run_ai()
            self.assertTrue(_pump(lambda: "padding: 6px 14px" in self.panel.wiki_edit.toPlainText()))
        text = self.panel.wiki_edit.toPlainText()
        self.assertIn("|lbgcolor = #2b2a3a; padding: 6px 14px; border: 1px solid #8a7fa8; "
                      "border-radius: 6px; box-shadow: 0px 2px 8px 0px rgba(20, 18, 34, 0.60)", text)
        self.assertIn("|ltcolor = #e8e4f0; font-size: 14px; font-weight: 600", text)
        # `|rbdcolor` 只在原文里本来就有时才写（用户 2026-09-29：不要自动补）
        self.assertNotIn("rbdcolor", text)
        # 阴影颜色不能被「按空白切分」切碎变成白色（rgba(20, 18, 34, 0.6) → #141222）
        self.assertNotIn("#ffffff; ", text.split("|lbgcolor")[1].split("\n")[0])
        captured = []
        self.panel.saved.connect(lambda payload: captured.append(payload))
        self.panel._on_save()
        self.assertIn("padding: 6px 14px", captured[0][0])

    def test_ai_note_follows_the_current_object(self):
        """「补充要求」按 Songbox / Introduction / 歌词 三栏预填（手写过的不会被冲掉）。"""
        prompts = {"songbox": "songbox 提示", "intro": "intro 提示", "lyrics": "歌词提示"}
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": prompts}):
            self.panel.start({"initial": "", "hover": False})
            self.assertEqual("songbox 提示", self.panel.ai_note_edit.toPlainText())
            self.panel._on_section_changed("intro")
            self.assertEqual("intro 提示", self.panel.ai_note_edit.toPlainText())
            self.panel._on_section_changed("lyrics")
            self.assertEqual("歌词提示", self.panel.ai_note_edit.toPlainText())
            self.panel.ai_note_edit.setPlainText("我自己写的")
            self.panel._on_section_changed("songbox")
            self.assertEqual("我自己写的", self.panel.ai_note_edit.toPlainText())

    def test_ai_missing_targets_are_reported(self):
        """模型漏了对象要明说（用户 2026-09-29 报：生成 Introduction 时 `|ltcolor` 没变化）。

        以前只显示「已应用 AI 生成的样式」，用户根本不知道 Introduction 那项没拿到。
        """
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}):
            self.panel.start({"initial": "|lbgcolor = #000000\n|ltcolor = #ffffff",
                              "hover": False})
        self.panel._on_section_changed("intro")
        self.panel._ai_snapshot = self.panel._snapshot()
        self.panel._ai_requested = ["introLabel"]
        self.panel._on_ai_done({"ok": True, "model": "m", "css": {}, "missing": ["introLabel"]})
        self.assertIn("标签格", self.panel.ai_tip.text())
        self.assertIn("没返回", self.panel.ai_tip.text())

    def test_ai_ignores_targets_not_requested(self):
        """用户 2026-09-29 报：只想改 Introduction，结果多出一个 `|rstyle`。

        AI 结果里出现没请求过的对象时不能顺手改它（模型乱答 / 以后代码改动都不行）。
        """
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}):
            self.panel.start({"initial": "|lbgcolor = #000000\n|ltcolor = #ffffff",
                              "hover": False})
        self.panel._on_section_changed("intro")
        self.panel._ai_snapshot = self.panel._snapshot()
        self.panel._ai_requested = ["introLabel"]
        self.panel._on_ai_done({"ok": True, "model": "m",
                                "css": {"introLabel": "color: #abcdef;",
                                        "lyrTrans": "color: #123456;"}})
        text = self.panel.wiki_edit.toPlainText()
        self.assertIn("|ltcolor = #abcdef", text)          # 请求的那项照常生效
        self.assertNotIn("|rstyle", text)                  # 没请求的那项一点都不能动
        self.assertFalse(self.panel.tpl_states["lyrTrans"]["enabled"])

    def test_ai_generate_applies_returned_css(self):
        from PyQt5 import QtGui
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}), \
             mock.patch("utils.color_editor.EditorApi.ai_generate",
                        return_value={"ok": True, "css": {"songboxGlobal": "color: #123456;"},
                                      "model": "m"}) as generate:
            self.panel.start({"initial": "", "hover": False})
            image = QtGui.QImage(4, 4, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#000000"))
            self.panel.cover_view._set_image(image)
            self.panel._run_ai()
            self.assertTrue(_pump(lambda: self.panel._ai_undo is not None))
        generate.assert_called_once()
        self.assertEqual("#123456", self.panel.states[0]["color"])
        self.assertFalse(self.panel.ai_undo_button.isHidden())
        self.panel._undo_ai()
        self.assertNotEqual("#123456", self.panel.states[0]["color"])

    def test_ai_rewrites_the_hand_edited_parameter_box(self):
        """手改过左下角的参数框之后再点 AI 生成，框里必须出现新样式。

        用户 2026-09 报：「改完左下角的框、勾上『在 Wikitext 里输出该参数』、
        点 AI 生成 CSS，框里却没有出现 `|containerstyle`」。
        旧实现里那个框一旦手改过（`_wiki_dirty`），`_set_wiki_text()` 就整场跳过重写
        （那是给「切标签别冲掉手写内容」用的）→ AI 的结果只在模型 / 预览里，
        而「保存并继续」发出去的**正是框里的文本**，等于把 AI 生成的样式整段丢掉。
        """
        from PyQt5 import QtGui
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "prompts": {}}), \
             mock.patch("utils.color_editor.EditorApi.ai_generate",
                        return_value={"ok": True, "css": {"lyrContainer": "background: #123456;"},
                                      "model": "m"}):
            self.panel.start({"initial": "", "hover": False})
            self.panel._on_section_changed("lyrics")
            self.panel._select("lyrContainer", section="lyrics")
            image = QtGui.QImage(4, 4, QtGui.QImage.Format_RGB32)
            image.fill(QtGui.QColor("#000000"))
            self.panel.cover_view._set_image(image)
            self.panel.wiki_edit.setPlainText("|lbgcolor = #000000")     # 手改 → 记成「脏」
            self.assertTrue(self.panel._wiki_dirty)
            self.assertNotIn("|containerstyle", self.panel.wiki_edit.toPlainText())
            self.panel._run_ai()
            self.assertTrue(_pump(lambda: "|containerstyle" in self.panel.wiki_edit.toPlainText()))
        self.assertFalse(self.panel._wiki_dirty)
        self.assertIn("|containerstyle = background: #123456;", self.panel.wiki_edit.toPlainText())
        # 「保存并继续」发出去的也是这份新文本（以前会发出框里那份旧的）
        captured = []
        self.panel.saved.connect(lambda payload: captured.append(payload))
        self.panel._on_save()
        self.assertIn("|containerstyle", captured[0][0])
        self.panel._undo_ai()                    # 撤销后框里也要跟着回到没有它
        self.assertNotIn("|containerstyle", self.panel.wiki_edit.toPlainText())

    def test_layer_editing(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._add_layer()
        self.assertEqual(1, len(self.panel.state()["bgLayers"]))
        self.assertIn("linear-gradient", self.panel.code_edit.toPlainText())
        layer = self.panel.state()["bgLayers"][0]
        self.panel._add_stop(layer)
        self.assertEqual(3, len(layer["stops"]))
        self.panel._remove_stop(layer, 2)
        self.assertEqual(2, len(layer["stops"]))
        self.panel._remove_layer(0)
        self.assertEqual([], self.panel.state()["bgLayers"])

    def test_shadow_editing(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._add_box_shadow()
        self.panel._add_text_shadow()
        self.assertIn("box-shadow", self.panel.code_edit.toPlainText())
        self.assertIn("text-shadow", self.panel.code_edit.toPlainText())
        self.panel._remove_shadow(0, True)
        self.assertEqual([], self.panel.state()["boxShadows"])

    def test_dynamic_rows_fit_the_right_column(self):
        """图层 / 阴影的每一行都要能塞进右栏可视区。

        以前阴影行把 x/y/模糊/扩散 + 取色器 + 内阴影 + 删除 塞成一行（1000px+），
        右栏只有 ~435px，后面一大半控件直接被裁掉看不见。
        """
        from PyQt5 import QtWidgets
        self.panel.resize(1012, 694)          # 默认窗口（1100×768）下样式页的大小
        self.panel.show()
        self.app.processEvents()
        self.panel.start({"initial": "", "hover": False})
        self.panel._add_layer()
        self.panel._add_box_shadow()
        self.panel._add_text_shadow()
        self.app.processEvents()
        rows = []
        for holder in (self.panel.layers_layout, self.panel.box_shadows_layout,
                       self.panel.text_shadows_layout):
            for index in range(holder.count()):
                rows.append(holder.itemAt(index).widget())
        self.assertTrue(rows, "图层 / 阴影行没建出来")
        areas = [area for area in self.panel.findChildren(QtWidgets.QScrollArea)
                 if area.widget() is not None
                 and area.widget().layout() is self.panel.panel_layout]
        self.assertEqual(1, len(areas), "右栏应该有一个滚动区")
        limit = areas[0].viewport().width()
        for row in rows:
            self.assertLessEqual(row.minimumSizeHint().width(), limit,
                                 "这一行最窄也放不下，右侧的控件会被裁掉")

    def test_numeric_fields_line_up_in_one_column(self):
        """数字框 / 下拉框紧跟标签（后面留白），输入框和取色器才撑满整行。"""
        from PyQt5 import QtWidgets
        spin_row = self.panel.width_spin.parentWidget()
        layout = spin_row.layout()
        self.assertEqual(0, layout.stretch(layout.indexOf(self.panel.width_spin)),
                         "数字框不该被拉宽（会被推到行尾）")
        last = layout.itemAt(layout.count() - 1)
        self.assertIsNotNone(last.spacerItem(), "数字框后面要留白")
        text_row = self.panel.text_edit.parentWidget()
        text_layout = text_row.layout()
        self.assertEqual(1, text_layout.stretch(text_layout.indexOf(self.panel.text_edit)),
                         "输入框该撑满整行")
        self.assertIsInstance(spin_row, QtWidgets.QWidget)

    def test_three_pills_are_shrunk_to_fit_the_canvas(self):
        """画布装不下时三块一起等比缩窄：第三块以前跑到画布外面、根本看不见。"""
        from utils.ui import style_preview
        self.panel.start({"initial": "", "hover": False})
        self.panel.canvas_spin.setValue(520)
        widths = [width for width, _height in self.panel.preview._pill_sizes()]
        self.assertEqual(3, len(widths))
        gaps = style_preview.PILL_GAP * (len(widths) - 1)
        self.assertLessEqual(sum(widths) + gaps, 521,
                             "三块要一起缩到画布宽以内")
        self.assertLessEqual(self.panel.preview.minimumSizeHint().width(), 520 + 24)

    def test_preview_is_inside_a_scroll_area(self):
        """预览台套在滚动区里：画布调大 / 窗口变小时出滚动条，而不是把内容裁掉。"""
        self.assertIs(self.panel.preview, self.panel.preview_scroll.widget())
        self.panel.start({"initial": "", "hover": False})
        self.assertGreaterEqual(self.panel.preview_scroll.minimumHeight(),
                                self.panel.preview.minimumSizeHint().height(),
                                "预览区要比内容高，最后一段不能被切掉")

    def test_auto_text_color_button(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel.state()["bgSolid"] = "#000000"
        self.panel.bg_field.set_value("#000000")
        self.panel.fg_threshold_spin.setValue(60)
        self.panel._auto_fg()
        self.assertEqual("#ffffff", self.panel.state()["color"])

    def test_apply_code_rebuilds_state(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel._select(0, section="songbox")
        self.panel.code_edit.setPlainText(".tag-1 {\n  color: #ff0000;\n  filter: blur(2px);\n}")
        self.panel._apply_code()
        self.assertEqual("#ff0000", self.panel.state()["color"])
        self.assertIn("filter: blur(2px)", self.panel.state()["extras"])

    def test_load_from_wiki_text(self):
        self.panel.start({"initial": "", "hover": False})
        self.panel.wiki_edit.setPlainText("|颜色1 = #abcdef;\n|ltcolor = #111111")
        self.panel._load_from_wiki()
        self.assertEqual("#abcdef", self.panel.states[0]["bgSolid"])
        self.assertEqual("#111111", self.panel.tpl_states["introLabel"]["color"])

    def test_hand_edited_wikitext_survives_switching_the_right_tabs(self):
        """左下角「Wikitext 参数」框里手改的内容，切右侧标签 / 改控件都不能冲掉。

        用户 2026-09 报：「改完后当我切换右侧的标签时会覆盖掉我写的内容」——
        那个框是三块颜色 + 模板参数的**总输出**，切标签跟它没关系，重写只会把那几行手写的
        Wikitext 冲掉。现在只有点「重置当前 / 重置全部」或重新生成才会重写它；
        点「从文本载入」只把内容解析回模型（框里还是你写的那份）。
        """
        from utils.ui.style_panel import WIKI_LABEL_DIRTY
        self.panel.start({"initial": "|颜色1 = #1e90ff;", "hover": False})
        hand = "|颜色1 = #abcdef;\n|ltcolor = #111111"
        self.panel.wiki_edit.setPlainText(hand)
        self.assertEqual(WIKI_LABEL_DIRTY, self.panel.wiki_label.text(), "该提示手改的内容会留着")
        self.panel._on_section_changed("lyrics")                       # 切分段
        self.panel._select(1, section="songbox")                       # 切目标
        self.panel._select(-1, confirm=False, section="songbox")       # 切「全局」
        self.panel.bg_field.set_value("#123456", 1.0, notify=True)     # 改控件
        self.assertEqual(hand, self.panel.wiki_edit.toPlainText())
        self.panel._load_from_wiki()                                   # 点了「从文本载入」
        self.assertEqual("#abcdef", self.panel.states[0]["bgSolid"])
        self.assertEqual("#111111", self.panel.tpl_states["introLabel"]["color"])
        self.assertEqual(hand, self.panel.wiki_edit.toPlainText(), "载入不能把我写的内容重写掉")
        self.assertEqual(WIKI_LABEL_DIRTY, self.panel.wiki_label.text())
        self.assertTrue(self.panel._wiki_dirty)
        self.panel._reset_all()
        self.assertIn("|颜色3", self.panel.wiki_edit.toPlainText())

    def test_load_from_wiki_keeps_my_text_verbatim(self):
        """「从文本载入」把文本解析进模型，但**不重写这个框**。

        用户 2026-09 报：「点『从文本载入』后我在框里改的内容被重置了」——旧实现解析完
        又用生成的文本把框重写一遍，重写会顺手把文本规范化（`0 0 4px #000` 变成
        `0px 0px 4px 0px #000000`、声明重排、空行被抹掉），看起来就像手写的内容被回滚。
        """
        self.panel.start({"initial": "|颜色1 = #1e90ff;", "hover": False})
        hand = "|颜色1 = #ABCDEF;\n  filter: blur(2px)\n|ltcolor = #123456"
        self.panel.wiki_edit.setPlainText(hand)
        self.panel._load_from_wiki()
        self.assertEqual(hand, self.panel.wiki_edit.toPlainText())
        self.assertEqual("#abcdef", self.panel.states[0]["bgSolid"])
        self.assertIn("filter: blur(2px)", self.panel.states[0]["extras"])
        self.assertEqual("#123456", self.panel.tpl_states["introLabel"]["color"])

    def test_load_from_wiki_rejects_text_without_params(self):
        """认不出参数（空框 / 贴错东西）时不拿默认值盖掉模型，只在标题上提示。"""
        from utils.ui.style_panel import WIKI_LABEL_UNKNOWN
        self.panel.start({"initial": "|颜色1 = #1e90ff;", "hover": False})
        self.panel.wiki_edit.setPlainText("这不是参数")
        self.panel._load_from_wiki()
        self.assertEqual("#1e90ff", self.panel.states[0]["bgSolid"])
        self.assertEqual("这不是参数", self.panel.wiki_edit.toPlainText())
        self.assertEqual(WIKI_LABEL_UNKNOWN, self.panel.wiki_label.text())

    def test_hand_edited_css_survives_switching_targets(self):
        """「完整 CSS」框手改的内容按编辑对象记着：切走看别的，切回来还是我写的那份。"""
        from utils.ui.style_panel import CODE_LABEL, CODE_LABEL_DIRTY
        self.panel.start({"initial": "", "hover": False})
        self.panel._select(0, section="songbox")
        hand = ".tag-1 {\n  color: #ff0000;\n}"
        self.panel.code_edit.setPlainText(hand)
        self.assertEqual(CODE_LABEL_DIRTY, self.panel.code_box.title())
        self.panel._select(1, section="songbox")                       # 切到别的对象
        self.assertNotEqual(hand, self.panel.code_edit.toPlainText(), "换对象该看新对象的 CSS")
        self.panel._select(0, section="songbox")                       # 切回来
        self.assertEqual(hand, self.panel.code_edit.toPlainText(), "手写的 CSS 要还回来")
        self.panel._apply_code()                                       # 应用之后才算进模型
        self.assertEqual("#ff0000", self.panel.state()["color"])
        self.assertEqual(CODE_LABEL, self.panel.code_box.title())
        self.assertNotIn("0", self.panel._code_dirty)


class LyricsPanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils.lyrics_editor import LyricsApi
        from utils.ui.lyrics_panel import LyricsPanel
        self.api = LyricsApi("", "手动粘贴", use_hover=False, use_colors=False,
                             charas=["初音未来"])
        self.panel = LyricsPanel()

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def _start(self):
        with mock.patch.object(self.api, "chara_options",
                              return_value=[{"name": "初音未来", "color": "#39c5bb"}]), \
             mock.patch("utils.ai_lyrics.context", return_value={"enabled": True, "hidden": False}):
            self.panel.start({"api": self.api})

    def test_start_fills_context(self):
        self._start()
        self.assertFalse(self.panel.colors_check.isChecked())
        self.assertFalse(self.panel.marker_box.isVisible())

    def test_auto_fills_columns(self):
        self._start()
        self.panel.source_edit.setPlainText("きみの名前を呼ぶ\n你的名字\nkimi no namae wo yobu")
        self.panel._auto()
        self.assertIn("きみの", self.panel.jap_edit.toPlainText())
        self.assertIn("你的名字", self.panel.chs_edit.toPlainText())

    def test_convert_uses_line_numbers(self):
        self._start()
        self.panel.source_edit.setPlainText("日语1\n中文1\n日2\n中2")
        self.panel.group_spin.setValue(2)
        self.panel.jap_line_spin.setValue(1)
        self.panel.chs_line_spin.setValue(2)
        self.panel._convert()
        self.assertEqual("日语1\n日2", self.panel.jap_edit.toPlainText())
        self.assertEqual("中文1\n中2", self.panel.chs_edit.toPlainText())

    def test_done_emits_lyrics(self):
        received = []
        self.panel.saved.connect(received.append)
        self._start()
        self.panel.jap_edit.setPlainText("きみの")
        self.panel.chs_edit.setPlainText("你的")
        self.panel.translator_edit.setText("译者")
        self.panel._on_done()
        self.assertEqual(1, len(received))
        self.assertEqual("きみの", received[0].lyrics_jap)
        self.assertEqual("译者", received[0].translator)

    def test_done_without_lyrics_warns(self):
        received = []
        self.panel.saved.connect(received.append)
        self._start()
        with mock.patch("PyQt5.QtWidgets.QMessageBox.warning") as warning:
            self.panel._on_done()
        warning.assert_called_once()
        self.assertEqual([], received)

    def test_cancel_emits_cancelled(self):
        received = []
        self.panel.cancelled.connect(lambda: received.append(True))
        self._start()
        self.panel._on_cancel()
        self.assertEqual([True], received)

    def test_marker_marks_and_splits(self):
        self._start()
        self.panel.jap_edit.setPlainText("きみの\nはるか")
        self.panel.colors_check.setChecked(True)
        self.panel._refresh_marker(force=True)
        self.assertEqual(2, self.panel.marker_layout.count() - 1)     # 两行 + stretch
        self.panel._toggle_mark(0, 0, "初音未来", True)
        self.assertEqual(["初音未来"], self.panel._marks["jap"]["0"])
        self.panel._splits["0"] = {"jap": [1]}
        self.panel._refresh_marker(force=True)
        # 切开后：整行标记会落到每一段上（不会只给第一段）
        self.panel._toggle_mark(0, 1, "初音未来", True)
        self.assertEqual([["初音未来"], ["初音未来"]], self.panel._marks["jap"]["0"])
        # 取消第一段
        self.panel._toggle_mark(0, 0, "初音未来", False)
        self.assertEqual([[], ["初音未来"]], self.panel._marks["jap"]["0"])
        self.panel._merge_line(0)
        self.assertNotIn("0", self.panel._splits)
        self.panel._clear_marks()
        self.assertEqual({}, self.panel._marks["jap"])

    def test_track_switch_marks_the_chinese_column(self):
        """「翻译栏」也能单独标：切到中文栏后，标记/切开都按中文那一栏算。"""
        self._start()
        self.panel.jap_edit.setPlainText("きみの\nはるか")
        self.panel.chs_edit.setPlainText("你的名字\n远方")
        self.panel.colors_check.setChecked(True)
        self.panel._refresh_marker(force=True)
        self.panel._toggle_mark(0, 0, "初音未来", True)
        self.panel._set_track("chs")
        self.assertTrue(self.panel.track_buttons["chs"].isChecked())
        self.assertFalse(self.panel.track_buttons["jap"].isChecked())
        self.assertNotIn("0", self.panel._marks["chs"], "两栏的标记各存各的")
        self.panel._toggle_mark(1, 0, "初音未来", True)
        self.assertEqual(["初音未来"], self.panel._marks["chs"]["1"])
        self.assertEqual(["初音未来"], self.panel._marks["jap"]["0"], "日语栏的标记没被动")
        # 切分点也按栏存
        self.panel._splits["1"] = {"jap": [1]}
        self.panel._set_track("chs")
        self.assertEqual(0, len(self.panel._line_cuts(1)), "中文栏没有切分点")
        # 只清当前栏
        self.panel._clear_marks()
        self.assertEqual({}, self.panel._marks["chs"])
        self.assertIn("0", self.panel._marks["jap"])

    def test_mark_chs_button_copies_the_japanese_marks(self):
        """「按日语标记中文」：行数一致时由 api 直接照搬，并把面板切到中文栏显示结果。"""
        self._start()
        self.panel.jap_edit.setPlainText("きみの\nはるか")
        self.panel.chs_edit.setPlainText("你的名字\n远方")
        self.panel.colors_check.setChecked(True)
        self.panel._toggle_mark(0, 0, "初音未来", True)
        with mock.patch.object(self.api, "ai_mark_chs",
                               return_value={"ok": True, "marks": {"0": ["初音未来"]},
                                             "message": "两栏行数一致，已按行号照搬"}) as call:
            self.panel._mark_chs()
        self.assertTrue(call.called)
        self.assertEqual(["初音未来"], self.panel._marks["chs"]["0"])
        self.assertEqual("chs", self.panel._track)
        self.assertIn("照搬", self.panel.status_label.text())

    def test_mark_chs_button_reports_failure(self):
        self._start()
        self.panel.jap_edit.setPlainText("きみの")
        self.panel.colors_check.setChecked(True)
        with mock.patch.object(self.api, "ai_mark_chs",
                               return_value={"ok": False, "error": "日语栏还没有标记"}):
            self.panel._mark_chs()
        self.assertEqual("日语栏还没有标记", self.panel.status_label.text())

    def test_fill_source_reports_error(self):
        self._start()
        self.panel.source_url_edit.setText("https://example.com/x")
        with mock.patch.object(self.api, "fill_source",
                               return_value={"ok": False, "error": "认不出这个链接"}):
            self.panel._fill_source()
        self.assertEqual("认不出这个链接", self.panel.status_label.text())

    def test_fill_source_writes_fields(self):
        self._start()
        self.panel.source_url_edit.setText("https://example.com/x")
        with mock.patch.object(self.api, "fill_source",
                               return_value={"ok": True, "translator": "译者",
                                             "translatorUrl": "https://t", "sourceName": "网易云",
                                             "sourceUrl": "https://s", "message": "已填充"}):
            self.panel._fill_source()
        self.assertEqual("译者", self.panel.translator_edit.text())
        self.assertEqual("网易云", self.panel.source_name_edit.text())


class LyricsHoverHighlightTest(TestCase):
    """四栏悬停联动高亮：鼠标停在哪一行，四栏里的同一行一起亮。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        reset_font_scale()
        from utils.lyrics_editor import LyricsApi
        from utils.ui.lyrics_panel import LyricsPanel
        self.api = LyricsApi("", "手动粘贴")
        self.panel = LyricsPanel()
        with mock.patch.object(self.api, "chara_options", return_value=[]), \
             mock.patch("utils.ai_lyrics.context",
                        return_value={"enabled": False, "hidden": True}):
            self.panel.start({"api": self.api})
        # 四栏都给两行：体面地比对「同一行下标」
        self.panel.source_edit.setPlainText("第一行\n第二行")
        self.panel.jap_edit.setPlainText("きみの\nはるか")
        self.panel.chs_edit.setPlainText("你的\n我的")
        self.panel.roma_edit.setPlainText("kimi no\nharuka")

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def _highlights(self, pane):
        return pane.extraSelections()

    def test_line_at_maps_mouse_position(self):
        from PyQt5 import QtCore
        self.assertEqual(0, self.panel._line_at(self.panel.jap_edit, QtCore.QPoint(2, 2)))
        self.assertEqual(1, self.panel._line_at(self.panel.jap_edit, QtCore.QPoint(2, 60)))

    def test_line_at_returns_minus_one_for_empty_pane(self):
        from PyQt5 import QtCore
        self.panel.roma_edit.clear()
        self.assertEqual(-1, self.panel._line_at(self.panel.roma_edit, QtCore.QPoint(2, 2)))

    def test_hover_marks_same_line_in_all_panes(self):
        self.panel._set_hover_line(self.panel.jap_edit, 1)
        for pane in self.panel._panes:
            self.assertEqual(1, len(self._highlights(pane)), "四栏都该标出第 2 行")

    def test_hovered_pane_is_stronger(self):
        self.panel._set_hover_line(self.panel.jap_edit, 0)
        here = self._highlights(self.panel.jap_edit)[0].format.background().color().alpha()
        other = self._highlights(self.panel.chs_edit)[0].format.background().color().alpha()
        self.assertGreater(here, other)

    def test_line_beyond_shorter_pane_is_skipped(self):
        self.panel.chs_edit.setPlainText("只有一行")
        self.panel._set_hover_line(self.panel.jap_edit, 1)
        self.assertEqual(1, len(self._highlights(self.panel.jap_edit)))
        self.assertEqual([], self._highlights(self.panel.chs_edit), "中文栏只有 1 行")

    def test_leaving_clears_every_highlight(self):
        self.panel._set_hover_line(self.panel.jap_edit, 0)
        self.panel._set_hover_line(None, None)
        for pane in self.panel._panes:
            self.assertEqual([], self._highlights(pane))

    def test_mouse_move_event_drives_highlight(self):
        from PyQt5 import QtCore, QtGui
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(4, 4),
                                  QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
        self.panel.eventFilter(self.panel.jap_edit.viewport(), event)
        self.assertIs(self.panel.jap_edit, self.panel._hover_edit)
        self.assertEqual(0, self.panel._hover_line)

    def test_leave_event_clears_highlight(self):
        from PyQt5 import QtCore
        self.panel._set_hover_line(self.panel.jap_edit, 0)
        event = QtCore.QEvent(QtCore.QEvent.Leave)
        self.panel.eventFilter(self.panel.jap_edit.viewport(), event)
        self.assertIsNone(self.panel._hover_line)
        self.assertEqual([], self._highlights(self.panel.jap_edit))

    def test_event_filter_ignores_other_widgets(self):
        from PyQt5 import QtCore, QtGui
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseMove, QtCore.QPointF(4, 4),
                                  QtCore.Qt.NoButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
        self.panel.eventFilter(self.panel.source_url_edit, event)
        self.assertIsNone(self.panel._hover_edit)

    def test_clearing_texts_drops_highlight(self):
        self.panel._set_hover_line(self.panel.jap_edit, 1)
        self.panel._clear()
        self.assertIsNone(self.panel._hover_line)
        for pane in self.panel._panes:
            self.assertEqual([], self._highlights(pane))


class SubmitPanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils.ui.submit_panel import SubmitPanel
        self.panel = SubmitPanel()
        self.api = mock.Mock()
        self.api.get_context.return_value = {
            "page": "测试曲", "file": "out.wikitext", "summary": "摘要", "canSubmit": True,
            "createRedirect": False, "redirect": None, "cover": None,
            "family": {"available": False}, "disambig": {"needed": False},
            "origin": "https://voca.wiki/",
        }
        self.api._wikitext = "正文内容"
        self.api.preview.return_value = {"html": "<p>正文</p>", "head": "", "css": ""}
        self.api.save.return_value = {"ok": True, "message": "已保存到本地文件"}

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_start_fills_editor_and_metadata(self):
        self.panel.start({"api": self.api})
        self.assertTrue(_pump(lambda: "正文内容" == self.panel.editor.toPlainText()))
        self.assertEqual("摘要", self.panel.summary_edit.text())
        self.assertIn("测试曲", self.panel.title_label.text())
        self.assertEqual("未开启日文原名重定向", self.panel.redirect_label.text())
        self.assertIn("未下载封面", self.panel.cover_label.text())

    def test_preview_builds_document(self):
        self.panel.start({"api": self.api})
        self.assertTrue(_pump(lambda: bool(getattr(self.panel, "_preview_document", ""))))
        document = self.panel._preview_document
        self.assertIn("<p>正文</p>", document)
        self.assertIn('id="mw-content-text"', document)

    def test_save_reports_status(self):
        self.panel.start({"api": self.api})
        self.panel._save_local()
        self.api.save.assert_called_once_with("正文内容")
        self.assertIn("已保存到本地文件", self.panel.status_label.text())

    def test_submit_success_shows_results(self):
        """提交成功：不弹居中窗口，底部栏只写「已完成所有操作」，结果一条条弹右下角。

        用户 2026-09-29：「提交完不要再弹居中弹窗，底部栏只写已完成所有操作即可」。
        """
        self.panel.start({"api": self.api})
        self.api.submit.return_value = {"ok": True, "message": "已上传封面「A.jpg」；已提交「测试曲」",
                                        "url": "https://voca.wiki/wiki/x"}
        seen = []
        self.panel.notified.connect(lambda text, kind: seen.append((text, kind)))
        self.panel._submit()
        self.assertTrue(_pump(lambda: self.panel._finished))
        self.api.submit.assert_called_once_with("正文内容", "摘要", False)
        # 底部栏就只有这一句，详细步骤挂在 tooltip
        self.assertEqual("✓ 已完成所有操作", self.panel.status_label.text())
        self.assertIn("已提交「测试曲」", self.panel.status_label.toolTip())
        # 右下角：一步一条
        self.assertEqual(["已上传封面「A.jpg」", "已提交「测试曲」"], [text for text, _k in seen])
        # 居中弹窗已经删掉，改成底部这一行的按钮
        self.assertFalse(hasattr(self.panel, "_show_done_dialog"))
        self.assertFalse(self.panel.open_button.isHidden())       # 面板本身没 show，不能用 isVisible
        with mock.patch("utils.ui.submit_panel.webbrowser.open") as opened:
            self.panel.open_button.click()
        opened.assert_called_once_with("https://voca.wiki/wiki/x")

    def test_submit_failure_keeps_retry(self):
        self.panel.start({"api": self.api})
        self.api.submit.return_value = {"ok": False, "error": "网络错误"}
        self.panel._submit()
        self.assertTrue(_pump(lambda: "网络错误" in self.panel.status_label.text()))
        self.assertFalse(self.panel._finished)
        self.assertTrue(self.panel.submit_button.isEnabled())
        self.assertIn("重试", self.panel.login_label.text())

    def test_late_preview_does_not_overwrite_submit_status(self):
        """预览是后台请求：回来晚了一步，也不许把提交结果那句盖掉。

        以前这里会盖成「预览已更新」，害得用户看不到「提交失败：网络错误」——
        而且它还会让测试偶发失败（谁先回来不一定）。
        """
        self.panel.start({"api": self.api})                  # start 里就请求了一次预览
        token = self.panel._status_token
        self.api.submit.return_value = {"ok": False, "error": "网络错误"}
        self.panel._submit()
        self.assertTrue(_pump(lambda: "网络错误" in self.panel.status_label.text()))
        # 把那次预览的结果「迟一步」送回来
        self.panel._on_preview(self.api.preview.return_value, silent=False, token=token)
        self.assertEqual("提交失败：网络错误", self.panel.status_label.text())

    def test_backlinks_open_dialog(self):
        """有链入要修正时仍然弹「修正链入」窗口（那不是「完成」弹窗，要继续操作）。"""
        self.panel.start({"api": self.api})
        result = {"ok": True, "message": "已提交", "backlinkOld": "旧", "backlinkNew": "新",
                  "backlinks": [{"title": "A", "count": 2, "kind": "wiki 链接"}]}
        with mock.patch("PyQt5.QtWidgets.QDialog.exec_") as exec_dialog:
            self.panel._on_submitted(result)
        exec_dialog.assert_called_once()
        self.assertEqual("✓ 已完成所有操作", self.panel.status_label.text())

    def test_template_mode_hides_entry_only_rows(self):
        """P主模板那一份 context 带 `kind=template`：重定向 / 封面 / 同名条目 / 大家族模板都收起。"""
        self.api.get_context.return_value = {
            "kind": "template", "page": "Template:雄之助", "file": "P主模板_雄之助.wikitext",
            "pageUrl": "https://voca.wiki/wiki/Template:%E9%9B%84%E4%B9%8B%E5%8A%A9",
            "origin": "https://voca.wiki/", "summary": "摘要", "canSubmit": True,
            "createRedirect": False, "redirect": "", "cover": None,
            "family": {"available": False}, "disambig": {"needed": False},
        }
        self.panel.start({"api": self.api})
        for label in (self.panel.redirect_label, self.panel.cover_label,
                      self.panel.disambig_label, self.panel.family_label):
            self.assertTrue(label.isHidden(), label.objectName() or label.text())
        self.assertTrue(self.panel.family_check.isHidden())
        self.assertIn("Template:雄之助", self.panel.title_label.text())

    def test_template_backlink_dialog_uses_backend_texts(self):
        """提交后那个「把模板加进条目」弹窗：标题 / 按钮 / 逐行说明都由 api 给。"""
        from PyQt5 import QtCore
        self.panel.start({"api": self.api})
        result = {"ok": True, "message": "已提交「Template:雄之助」",
                  "backlinkTitle": "把模板加进条目",
                  "backlinkHeader": "把 {{雄之助}} 加进这些条目",
                  "backlinkAction": "写入选中条目",
                  "backlinkSkipNote": " —— 条目还没建，跳过",
                  "backlinks": [{"title": "时滞记录", "count": 1, "note": "加入本模板"},
                                {"title": "Navy", "count": 0, "note": "条目还没建"}]}
        with mock.patch("PyQt5.QtWidgets.QDialog.exec_") as exec_dialog:
            self.panel._on_submitted(result)
        exec_dialog.assert_called_once()
        dialog = self.panel.findChildren(QtWidgets.QDialog)[-1]
        self.assertEqual("把模板加进条目", dialog.windowTitle())
        texts = [widget.text() for widget in dialog.findChildren(QtWidgets.QLabel)]
        self.assertIn("把 {{雄之助}} 加进这些条目", texts)
        buttons = [widget.text() for widget in dialog.findChildren(QtWidgets.QPushButton)]
        self.assertIn("写入选中条目", buttons)
        listing = dialog.findChildren(QtWidgets.QListWidget)[0]
        self.assertIn("时滞记录（加入本模板）", listing.item(0).text())
        self.assertIn("条目还没建，跳过", listing.item(1).text())
        self.assertEqual(QtCore.Qt.Checked, listing.item(0).checkState())
        self.assertEqual(QtCore.Qt.Unchecked, listing.item(1).checkState())
        dialog.deleteLater()

    def test_backlink_result_lines_are_one_per_page(self):
        """用户 2026-09-29 要求：替换链入的成功提醒要一个条目一个条目的冒。"""
        from utils.ui.submit_panel import backlink_page_text
        self.assertEqual("✓ Template:雄之助（1 处，模板参数（曲目名））",
                         backlink_page_text({"title": "Template:雄之助", "ok": True,
                                             "count": 1, "kind": "模板参数（曲目名）"}))
        self.assertEqual("✓ A（2 处，wiki 链接）",
                         backlink_page_text({"title": "A", "ok": True, "count": 2,
                                             "kind": "wiki 链接"}))
        self.assertEqual("✓ B（1 处）",
                         backlink_page_text({"title": "B", "ok": True, "count": 1}))
        self.assertEqual("✗ B：没有可替换的引用",
                         backlink_page_text({"title": "B", "ok": False,
                                             "error": "没有可替换的引用"}))
        self.assertEqual("✗ C：未改动", backlink_page_text({"title": "C"}))

    def test_backlink_progress_hops_to_the_main_thread(self):
        """逐页回调必须在**主线程**里跑：工作线程里直接写控件会炸。

        `_PageProgress` 把 handler 连在自己的 QObject 方法上，Qt 才会按线程关系
        选择队列连接（连普通函数 / lambda 时 PyQt 会当直接调用 —— 那就还在工作线程里）。
        """
        import threading
        from PyQt5 import QtCore
        from utils.ui.submit_panel import _PageProgress
        main_thread = threading.current_thread().name
        seen: List[str] = []
        progress = _PageProgress(lambda item: seen.append(threading.current_thread().name))

        class Worker(QtCore.QThread):
            def run(self) -> None:                      # noqa: D102 - QThread 约定
                progress.page.emit({"title": "A", "ok": True, "count": 1})

        worker = Worker()
        worker.start()
        self.assertTrue(_pump(lambda: bool(seen)))
        worker.wait(2000)
        self.assertEqual([main_thread], seen)

    def test_backlink_summary_counts_the_results(self):
        """用户 2026-10 报「明明写了三篇条目，弹窗却写成功 0 个」。

        真凶是作用域：计数器 `counts` 原来写在 `apply_fix()` 里，而逐页回调
        `_on_page_done()` 是它的**兄弟**函数 —— 看不到它，于是每页都抛
        `NameError: name 'counts' is not defined`（✓ 那几行照常写出来了，
        计数却没加上），收尾只好写「成功 0 个」。
        顺带把结算改成直接数 `results`（回调也改经 QObject 回主线程）。
        """
        def fake_fix(titles_json, progress=None):
            results = [{"title": "Last dinner", "ok": True, "count": 1, "kind": "插入模板"},
                       {"title": "迷途孩子的缎带", "ok": True, "count": 1, "kind": "插入模板"},
                       {"title": "ICON1C!!", "ok": True, "count": 1, "kind": "插入模板"}]
            for item in results:
                progress(item)                 # 真 worker 也是「先逐页回调，再返回结果」
            return {"ok": True, "message": "已把模板写进 3 个条目", "results": results}

        self.api.fix_backlinks.side_effect = fake_fix
        self.panel.start({"api": self.api})
        backlinks = [{"title": title, "count": 1, "note": "加入本模板"}
                     for title in ("Last dinner", "迷途孩子的缎带", "ICON1C!!")]
        result = {"ok": True, "message": "已提交「Template:Ruliea」",
                  "backlinkTitle": "把模板加进条目",
                  "backlinkHeader": "把 {{Ruliea}} 加进这些条目",
                  "backlinkAction": "写入选中条目",
                  "backlinkSkipNote": " —— 条目还没建，跳过",
                  "backlinks": backlinks}
        with mock.patch("PyQt5.QtWidgets.QDialog.exec_") as exec_dialog:
            self.panel._on_submitted(result)
        exec_dialog.assert_called_once()
        dialog = self.panel.findChildren(QtWidgets.QDialog)[-1]
        buttons = [widget for widget in dialog.findChildren(QtWidgets.QPushButton)
                   if widget.text() == "写入选中条目"]
        self.assertEqual(1, len(buttons))
        buttons[0].click()

        def summary_text() -> str:
            texts = [widget.text() for widget in dialog.findChildren(QtWidgets.QLabel)]
            return next((text for text in texts if "成功" in text), "")

        self.assertTrue(_pump(lambda: "成功 3 个" in summary_text()), summary_text())
        log = dialog.findChildren(QtWidgets.QPlainTextEdit)[0].toPlainText()
        self.assertIn("✓ Last dinner（1 处，插入模板）", log)
        self.assertIn("✓ ICON1C!!（1 处，插入模板）", log)
        self.assertTrue(log.rstrip().endswith("成功 3 个"), log)
        dialog.deleteLater()

    def test_webengine_is_skipped_in_tests(self):
        # 单测里不装浏览器内核：预览区为空，界面提供「在浏览器里打开预览」按钮
        self.assertIsNone(self.panel.preview_view)
        self.assertTrue(self.panel.browser_button.isEnabled())

    def test_notifications_pop_step_by_step(self):
        """提交页的通知：多步消息按「；」拆成一条一条发出去；进度提示（提交中…）不发。

        用户 2026-09-29：「提交页的通知也应该一条一条的在左下角通过弹窗的形式弹出来」。
        """
        seen = []
        self.panel.notified.connect(lambda text, kind: seen.append((text, kind)))
        self.panel.set_status("✓ 已提交「A」；已创建重定向 B → A；已上传封面", "ok")
        self.panel.set_status("提交中…")                       # 进度类不弹
        self.panel.set_status("预览已更新")                     # 同上
        self.panel.set_status("提交失败：站点 500", "err")
        self.assertEqual([("✓ 已提交「A」", "ok"), ("已创建重定向 B → A", "ok"),
                          ("已上传封面", "ok"), ("提交失败：站点 500", "err")], seen)

    def test_submit_button_font_is_bold_so_the_text_fits(self):
        """用户 2026-09-29 报「『提交到 Vocawiki』的按钮字体没有显示完全」。

        QSS 里 `:default` 按钮是 `font-weight: 600`，但那不参与尺寸计算：Qt 量文字宽度用的
        还是控件自身的（非粗体）字体，于是粗体渲染时最后一个字被裁。控件字体也粗体之后，
        `sizeHint()` 与渲染才一致（`theme.mark_accent` 现在会顺手设粗体）。
        """
        button = self.panel.submit_button
        self.assertTrue(button.font().bold(), "默认 / 主按钮的控件字体要是粗体")
        metrics = button.fontMetrics()
        # sizeHint 要盖住**粗体渲染**的宽度（以前量的非粗体，长标题差几个像素 → 末字被裁）
        self.assertGreaterEqual(button.sizeHint().width(),
                                metrics.horizontalAdvance(button.text()) + 4)


class ProducerPanelTest(TestCase):
    """「曲目」页（P主模板）：表格编辑、年份自动算、专辑、从维基补名、实时预览。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils import producer_template as pt
        from utils.ui.producer_panel import ProducerPanel
        self.pt = pt
        self.panel = ProducerPanel()
        self.work = pt.ProducerWork(
            artist=pt.ProducerArtist(id=23981, name="雄之助"),
            songs=[pt.ProducerSong(ja="天堂", cn="天堂中文", date="2024-08-28"),
                   pt.ProducerSong(ja="Navy", date="2024-01-05"),
                   pt.ProducerSong(ja="ラグタイムレコード", cn="时滞记录", date="2021-09-01")],
            albums=["Void", "Pathos"], page_name="雄之助", template_name="雄之助")

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_start_fills_table_and_preview(self):
        self.panel.start({"work": self.work})
        self.assertIn("雄之助", self.panel.artist_label.text())
        self.assertIn("23981", self.panel.artist_label.text())
        self.assertEqual("雄之助", self.panel.page_edit.text())
        self.assertEqual(3, self.panel.table.rowCount())
        self.assertEqual(2, self.panel.album_list.count())
        # 按日期排序：2021 那首在最上面
        self.assertEqual("时滞记录", self.panel.table.item(0, 1).text())
        self.assertEqual("2021", self.panel.table.item(0, 0).text())
        self.assertIn("|group1 = 投稿的</br>原创曲目", self.panel.preview.toPlainText())
        self.assertIn("|list2 = {{lj|{{linksplit|c=#|prefix=雄之助|Void|Pathos}}}}",
                      self.panel.preview.toPlainText())

    def test_editing_a_cell_updates_the_model_and_preview(self):
        self.panel.start({"work": self.work})
        self.panel.table.item(0, 1).setText("时滞记录（改）")
        self.assertEqual("时滞记录（改）", self.work.songs[0].cn)
        self.assertIn("时滞记录（改）{{!}}{{lj|ラグタイムレコード}}",
                      self.panel.preview.toPlainText())

    def test_editing_the_date_recomputes_the_year(self):
        """日期是各种写法都认的，年份格子跟着变。"""
        self.panel.start({"work": self.work})
        self.panel.table.item(0, 3).setText("2022年3月9日")
        self.assertEqual("2022-03-09", self.work.songs[0].date)
        self.assertEqual("2022", self.panel.table.item(0, 0).text())
        self.assertEqual("2022-03-09", self.panel.table.item(0, 3).text())
        self.assertIn("|group1 = 2022年", self.panel.preview.toPlainText())

    def test_year_and_status_columns_are_read_only(self):
        from PyQt5 import QtCore
        self.panel.start({"work": self.work})
        for column in (0, 4):
            flags = self.panel.table.item(0, column).flags()
            self.assertFalse(bool(flags & QtCore.Qt.ItemIsEditable), column)

    def test_add_and_remove_songs(self):
        self.panel.start({"work": self.work})
        self.panel._add_song()
        self.assertEqual(4, self.panel.table.rowCount())
        self.assertEqual(4, len(self.work.songs))
        self.panel.table.selectRow(0)
        self.panel._remove_songs()
        self.assertEqual(3, self.panel.table.rowCount())
        self.assertEqual(3, len(self.work.songs))

    def test_add_and_remove_albums(self):
        self.panel.start({"work": self.work})
        self.panel._add_album()
        self.album_text = self.panel.album_list.item(2).setText("Tranquilizer")
        self.panel._collect_albums()
        self.assertEqual(["Void", "Pathos", "Tranquilizer"], self.work.albums)
        self.panel.album_list.setCurrentRow(0)
        self.panel._remove_album()
        self.assertEqual(["Pathos", "Tranquilizer"], self.work.albums)

    def test_save_emits_the_work(self):
        self.panel.start({"work": self.work})
        seen = []
        self.panel.saved.connect(seen.append)
        self.panel.page_edit.setText("Yunosuke")
        self.panel._on_save()
        self.assertEqual([self.work], seen)
        self.assertEqual("Yunosuke", self.work.page_name)
        self.assertIn("prefix=Yunosuke", self.panel.preview.toPlainText())

    def test_refresh_status_marks_existing_pages(self):
        self.panel.start({"work": self.work})
        with mock.patch("utils.ui.producer_panel.wiki_api.fetch_pages_text",
                        return_value={"天堂中文": "正文", "时滞记录": "正文"}):
            self.panel._refresh_status()
            self.assertTrue(_pump(lambda: not self.panel._workers))
        rows = {self.panel.table.item(row, 1).text() or self.panel.table.item(row, 2).text():
                self.panel.table.item(row, 4).text()
                for row in range(self.panel.table.rowCount())}
        self.assertEqual("已建", rows["天堂中文"])
        self.assertEqual("待建", rows["Navy"])

    def test_search_names_reports_how_many_were_filled(self):
        self.panel.start({"work": self.work})
        with mock.patch("utils.ui.producer_panel.pt.fill_missing_names", return_value=2):
            self.panel._search_names()
            self.assertTrue(_pump(lambda: not self.panel._workers))
            self.app.processEvents()
        self.assertIn("补到 2 个条目名", self.panel.status_label.text())

    def test_search_names_when_everything_is_named(self):
        for song in self.work.songs:
            song.cn = song.cn or song.ja
        self.panel.start({"work": self.work})
        self.panel._search_names()
        self.assertIn("都已经有中文条目名", self.panel.status_label.text())

    # ---------------------------------------------------------- 外部链接 / AI 补名
    def test_has_the_two_name_buttons(self):
        self.panel.start({"work": self.work})
        self.assertEqual("从外部链接获取中文名", self.panel.external_button.text())
        self.assertEqual("AI填充中文名", self.panel.ai_button.text())
        self.assertTrue(self.panel.external_button.isVisibleTo(self.panel))

    def test_external_names_reports_sources(self):
        self.panel.start({"work": self.work})
        result = {"ok": True, "filled": 3, "checked": 3,
                  "by_source": {"网易云": 2, "bilibili": 1},
                  "names": {"Navy": "海军"}}
        with mock.patch("utils.ui.producer_panel.pt.fill_external_names", return_value=result):
            self.panel._external_names()
            self.assertTrue(_pump(lambda: not self.panel._workers))
            self.app.processEvents()
        self.assertIn("补到 3 个中文名（网易云 2 个、bilibili 1 个）",
                      self.panel.status_label.text())

    def test_external_names_says_when_nothing_found(self):
        self.panel.start({"work": self.work})
        result = {"ok": True, "filled": 0, "checked": 1, "by_source": {}, "names": {}}
        with mock.patch("utils.ui.producer_panel.pt.fill_external_names", return_value=result):
            self.panel._external_names()
            self.assertTrue(_pump(lambda: not self.panel._workers))
            self.app.processEvents()
        self.assertIn("都没搜到能用的中文名", self.panel.status_label.text())
        self.assertIn("AI填充中文名", self.panel.status_label.text())

    def test_external_names_skips_when_everything_is_named(self):
        for song in self.work.songs:
            song.cn = song.cn or song.ja
        self.panel.start({"work": self.work})
        with mock.patch("utils.ui.producer_panel.pt.fill_external_names") as fill:
            self.panel._external_names()
        fill.assert_not_called()
        self.assertIn("都已经有中文条目名", self.panel.status_label.text())

    def test_name_buttons_hide_when_config_turns_them_off(self):
        """`wikitext.producer_names: false` 时两个按钮都不显示（也就不会有联网调用）。"""
        self.panel.start({"work": self.work})
        with mock.patch.object(self.panel, "names_enabled", return_value=False):
            self.panel._check_buttons()
        self.assertFalse(self.panel.external_button.isVisibleTo(self.panel))
        self.assertFalse(self.panel.ai_button.isVisibleTo(self.panel))
        with mock.patch.object(self.panel, "names_enabled", return_value=True):
            self.panel._check_buttons()
        self.assertTrue(self.panel.external_button.isVisibleTo(self.panel))

    def test_ai_button_is_disabled_without_a_key(self):
        info = {"enabled": False, "reason": "请在 wiki_credentials.yaml 里填写 ai_api_key"}
        with mock.patch("utils.ui.producer_panel.ai_css.context", return_value=info):
            self.panel.start({"work": self.work})
        self.assertFalse(self.panel.ai_button.isEnabled())
        self.assertIn("ai_api_key", self.panel.ai_button.toolTip())

    def test_ai_names_asks_the_model_then_reviews_each_name(self):
        """用户 2026-10 要求：AI 每填一个都要弹窗让人工复检（采用 / 改字 / 跳过）。"""
        with mock.patch("utils.ui.producer_panel.ai_css.context",
                        return_value={"enabled": True, "provider": "deepseek",
                                      "model": "deepseek-flash", "reason": ""}):
            self.panel.start({"work": self.work})
        self.assertTrue(self.panel.ai_button.isEnabled())
        payload = self.panel.ai_payload()
        self.assertIn("Navy", payload)              # 只带没有中文名的那首
        self.assertNotIn("天堂", payload)
        self.assertNotIn("ラグタイムレコード", payload)
        result = {"ok": True, "model": "deepseek-flash",
                  "names": {"Navy": "海军", "ラグタイムレコード": "时滞记录"}}
        seen: List[tuple] = []

        def review(index, total, song, suggestion, model=""):
            seen.append((index, total, song.ja, suggestion))
            return ("accept", "海军（改）")           # 用户把模型给的字改了一下

        with mock.patch.object(self.panel, "_review_name", side_effect=review):
            self.panel._on_ai_done(result)
        self.assertEqual([(1, 1, "Navy", "海军")], seen)      # 已有中文名的那首不再问
        self.assertEqual("海军（改）", self.panel.work.songs[1].cn)   # 改过的名字也要写进去
        self.assertIn("海军（改）", self.panel.preview.toPlainText())
        # 表里立刻可见（Navy 按日期排在第二行：2021 那首在最上面）
        self.assertEqual("海军（改）", self.panel.table.item(1, 1).text())
        self.assertIn("采用 1 个中文名", self.panel.status_label.text())

    def test_ai_names_counts_the_skipped_ones(self):
        work = self.pt.ProducerWork(artist=self.pt.ProducerArtist(id=1, name="雄之助"),
                                    songs=[self.pt.ProducerSong(ja="A"),
                                           self.pt.ProducerSong(ja="B")],
                                    page_name="雄之助", template_name="雄之助")
        self.panel.start({"work": work})
        result = {"ok": True, "names": {"A": "甲", "B": "乙"}}
        with mock.patch.object(self.panel, "_review_name",
                               side_effect=[("accept", "甲"), ("skip", "")]):
            self.panel._on_ai_done(result)
        self.assertEqual("甲", work.songs[0].cn)
        self.assertEqual("", work.songs[1].cn)
        self.assertIn("采用 1 个中文名，跳过 1 个", self.panel.status_label.text())

    def test_ai_names_stop_skips_the_rest(self):
        work = self.pt.ProducerWork(artist=self.pt.ProducerArtist(id=1, name="雄之助"),
                                    songs=[self.pt.ProducerSong(ja="A"),
                                           self.pt.ProducerSong(ja="B")],
                                    page_name="雄之助", template_name="雄之助")
        self.panel.start({"work": work})
        result = {"ok": True, "names": {"A": "甲", "B": "乙"}}
        with mock.patch.object(self.panel, "_review_name", return_value=("stop", "")) as review:
            self.panel._on_ai_done(result)
        self.assertEqual(1, review.call_count)
        self.assertIn("采用 0 个中文名", self.panel.status_label.text())

    def test_ai_names_without_suggestions(self):
        self.panel.start({"work": self.work})
        self.panel._on_ai_done({"ok": True, "names": {}})
        self.assertIn("模型没给出能用的中文名", self.panel.status_label.text())

    def test_ai_names_error_is_shown(self):
        self.panel.start({"work": self.work})
        self.panel._on_ai_done({"ok": False, "error": "未配置 ai_api_key"})
        self.assertIn("AI 起名失败：未配置 ai_api_key", self.panel.status_label.text())

    def test_review_dialog_writes_the_edited_name(self):
        """复检弹窗本身：框里的字可以直接改，点「采用并下一个」就按改后的名字写。"""
        self.panel.start({"work": self.work})
        song = self.work.songs[1]
        clicked: List[str] = []
        titles: List[str] = []

        def fake_exec(dialog) -> int:
            titles.append(dialog.windowTitle())
            edit = dialog.findChildren(QtWidgets.QLineEdit)[0]
            self.assertEqual("海军", edit.text())              # 预填模型的建议
            edit.setText(" 海军（手改） ")
            for button in dialog.findChildren(QtWidgets.QPushButton):
                if button.text() == "采用并下一个":
                    clicked.append(button.text())
                    button.click()
            return dialog.result()

        with mock.patch("PyQt5.QtWidgets.QDialog.exec_", fake_exec):
            choice, name = self.panel._review_name(1, 2, song, "海军", "deepseek-flash")
        self.assertEqual(["采用并下一个"], clicked)
        self.assertEqual(["确认中文名（1/2）"], titles)
        self.assertEqual(("accept", "海军（手改）"), (choice, name))   # 前后空白会去掉

    def test_review_dialog_closing_counts_as_skip(self):
        self.panel.start({"work": self.work})
        with mock.patch("PyQt5.QtWidgets.QDialog.exec_", return_value=0):
            self.assertEqual(("skip", ""),
                             self.panel._review_name(1, 1, self.work.songs[1], "海军"))

    def test_review_dialog_with_an_emptied_box_skips(self):
        self.panel.start({"work": self.work})

        def fake_exec(dialog) -> int:
            dialog.findChildren(QtWidgets.QLineEdit)[0].clear()
            for button in dialog.findChildren(QtWidgets.QPushButton):
                if button.text() == "采用并下一个":
                    button.click()
            return dialog.result()

        with mock.patch("PyQt5.QtWidgets.QDialog.exec_", fake_exec):
            self.assertEqual(("skip", ""),
                             self.panel._review_name(1, 1, self.work.songs[1], "海军"))

    def test_review_dialog_has_a_stop_button(self):
        self.panel.start({"work": self.work})

        def fake_exec(dialog) -> int:
            for button in dialog.findChildren(QtWidgets.QPushButton):
                if button.text() == "剩下的都跳过":
                    button.click()
            return dialog.result()

        with mock.patch("PyQt5.QtWidgets.QDialog.exec_", fake_exec):
            self.assertEqual(("stop", ""),
                             self.panel._review_name(1, 5, self.work.songs[1], "海军"))

    def test_reset_clears_the_page(self):
        self.panel.start({"work": self.work})
        self.panel.reset()
        self.assertIsNone(self.panel.work)
        self.assertEqual(0, self.panel.table.rowCount())
        self.assertEqual(0, self.panel.album_list.count())
        self.assertEqual("", self.panel.preview.toPlainText())


class ProducerStylePanelTest(TestCase):
    """「样式」页（P主模板）：三组颜色 → 模板里的 titlestyle / groupstyle / liststyle。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils import producer_template as pt
        from utils.ui.producer_style_panel import ProducerStylePanel
        self.pt = pt
        self.panel = ProducerStylePanel()
        self.work = pt.ProducerWork(
            artist=pt.ProducerArtist(id=1, name="雄之助"),
            songs=[pt.ProducerSong(ja="Navy", date="2024-01-05")],
            albums=["Void"], page_name="雄之助", template_name="雄之助")

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_start_loads_defaults(self):
        self.panel.start({"work": self.work})
        self.assertEqual("#94ceda", self.panel.fields["titleBg"].value())
        self.assertEqual("#006cad", self.panel.fields["titleFg"].value())
        self.assertEqual("", self.panel.fields["listBg"].value())
        text = self.panel.preview.toPlainText()
        self.assertIn("|titlestyle = background:#94ceda;color:#006cad", text)
        self.assertIn("|groupstyle = background:#575134;color:#ffffff", text)
        self.assertNotIn("|liststyle", text)          # 默认不写列表底色

    def test_changing_a_colour_rewrites_the_template(self):
        self.panel.start({"work": self.work})
        self.panel.fields["listBg"].set_value("#FFF8B0", notify=True)
        self.panel.fields["listFg"].set_value("#3C4C54", notify=True)
        text = self.panel.preview.toPlainText()
        self.assertIn("|liststyle = background:#fff8b0;color:#3c4c54", text)
        self.assertEqual("#fff8b0", self.work.styles["listBg"])

    def test_default_button_clears_a_colour(self):
        self.panel.start({"work": self.work})
        self.panel.fields["titleBg"].set_value("", notify=True)
        self.assertNotIn("background:#94ceda", self.panel.preview.toPlainText())
        self.assertIn("|titlestyle = color:#006cad", self.panel.preview.toPlainText())

    def test_reset_restores_defaults(self):
        self.panel.start({"work": self.work})
        self.panel.fields["titleBg"].set_value("#000000", notify=True)
        self.panel._reset_styles()
        self.assertEqual("#94ceda", self.panel.fields["titleBg"].value())

    def test_save_emits_styles(self):
        self.panel.start({"work": self.work})
        self.panel.fields["groupBg"].set_value("#c8b492", notify=True)
        seen = []
        self.panel.saved.connect(seen.append)
        self.panel._on_save()
        self.assertEqual([self.panel.styles()], seen)
        self.assertEqual("#c8b492", seen[0]["groupBg"])

    def test_ai_result_is_applied_and_missing_ids_are_reported(self):
        self.panel.start({"work": self.work})
        self.panel._on_ai_done({"ok": True, "model": "deepseek-flash",
                                "css": {"title": "background: #204060; color: #ffffff;",
                                        "group": "background-color:#8899aa;color:#101820;"}})
        self.assertEqual("#204060", self.panel.fields["titleBg"].value())
        self.assertEqual("#ffffff", self.panel.fields["titleFg"].value())
        self.assertEqual("#8899aa", self.panel.fields["groupBg"].value())
        self.assertEqual("", self.panel.fields["listBg"].value())      # 没返回就不动
        self.assertIn("列表", self.panel.status_label.text())
        self.assertIn("|titlestyle = background:#204060;color:#ffffff",
                      self.panel.preview.toPlainText())

    def test_ai_result_with_unparseable_colours_keeps_old_values(self):
        """认不出的颜色**不要**当成白色写回去（用户 2026-09 报的老毛病）。"""
        from utils.ui.producer_style_panel import parse_ai_styles
        self.assertEqual({"titleBg": "#112233", "titleFg": "#445566"},
                         parse_ai_styles({"title": "background: #112233; color: #445566"}))
        self.assertEqual({"titleBg": "#ff8800"},
                         parse_ai_styles({"title":
                                          "background: linear-gradient(#ff8800, #004488)"}))
        self.assertEqual({"titleBg": "#336699"},
                         parse_ai_styles({"title": "background: rgb(51, 102, 153)"}))
        self.assertEqual({"listFg": "#0f1f2e"},
                         parse_ai_styles({"list": "color: hsl(210, 50%, 12%)"}))
        self.assertEqual({}, parse_ai_styles({"title": "background: 完全看不懂的东西"}))

    def test_ai_payload_carries_targets_and_image(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder).joinpath("cover.png")
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            self.panel.start({"work": self.work})
            self.panel.set_image(image)
            payload = json.loads(self.panel.ai_payload())
        self.assertEqual(["title", "group", "list"], [item["id"] for item in payload["targets"]])
        self.assertTrue(payload["colorOnly"])
        self.assertTrue(payload["image"].startswith("data:image/png;base64,"))
        self.assertIn("Navbox", payload["note"])

    def test_reset_clears_the_page(self):
        self.panel.start({"work": self.work})
        self.panel.reset()
        self.assertIsNone(self.panel.work)
        self.assertEqual("", self.panel.preview.toPlainText())

    # ---------------------------------------------------------- 默认参考图 = P主头像
    def _work_with_avatar(self):
        return self.pt.ProducerWork(
            artist=self.pt.ProducerArtist(
                id=23981, name="雄之助",
                picture="https://static.vocadb.net/img/Artist/mainOrig/23981.jpg?v=37"),
            songs=[self.pt.ProducerSong(ja="Navy", date="2024-01-05")],
            page_name="雄之助", template_name="雄之助")

    def _avatar_file(self) -> Path:
        folder = Path(tempfile.mkdtemp())
        path = folder.joinpath("P主头像_雄之助.jpg")
        path.write_bytes(b"\xff\xd8\xff" + b"0" * 32)
        return path

    def test_avatar_is_the_default_reference_image(self):
        """用户 2026-10 要求：「选择参考图」默认用下载下来的 P主头像，并交给 AI 配色。"""
        import json
        avatar = self._avatar_file()
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=avatar) as download:
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.assertEqual([mock.call(self.panel.work.artist)], download.call_args_list)
        self.assertEqual(avatar, self.panel._image)
        self.assertEqual("P主头像（VocaDB）", self.panel.image_label.text())
        self.assertIn("参考图默认用 P主头像", self.panel.status_label.text())
        self.assertTrue(self.panel.avatar_button.isEnabled())
        # 交给 AI 的就是这张头像
        payload = json.loads(self.panel.ai_payload())
        self.assertTrue(payload["image"].startswith("data:image/jpeg;base64,"))

    def test_avatar_button_is_disabled_without_one(self):
        self.panel.start({"work": self.work})                  # 这个 P主 没有头像
        self.assertFalse(self.panel.avatar_button.isEnabled())
        self.assertEqual("未选参考图", self.panel.image_label.text())

    def test_failed_avatar_download_falls_back_to_picking_a_file(self):
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar", return_value=None):
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.assertIsNone(self.panel._image)
        self.assertIn("没下载下来", self.panel.status_label.text())
        self.assertIn("选择参考图", self.panel.status_label.text())

    def test_the_users_own_image_is_not_replaced(self):
        """用户自己选过参考图，再打开同一页也不抢。"""
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=self._avatar_file()):
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.panel.set_image("D:/x/my-cover.png")              # 用户自己挑的
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar") as download:
            self.panel.start({"work": self._work_with_avatar()})
        download.assert_not_called()
        self.assertEqual("my-cover.png", self.panel.image_label.text())

    def test_avatar_button_switches_back(self):
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=self._avatar_file()) as download:
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
            self.panel.set_image("D:/x/my-cover.png")
            self.panel._load_avatar()                          # 点「P主头像」
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.assertEqual(2, download.call_count)
        self.assertEqual("P主头像（VocaDB）", self.panel.image_label.text())

    def test_switching_producer_downloads_the_new_avatar(self):
        """换了 P主 就重新下（旧的参考图不该留着）。"""
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=self._avatar_file()):
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        other = self._work_with_avatar()
        other.artist = self.pt.ProducerArtist(id=123, name="Ruliea",
                                              picture="https://static.vocadb.net/img/Artist/mainOrig/123.jpg")
        other.page_name = other.template_name = "Ruliea"
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=self._avatar_file()) as download:
            self.panel.start({"work": other})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.assertEqual(other.artist, download.call_args_list[0].args[0])

    def test_reset_drops_the_reference_image(self):
        with mock.patch("utils.ui.producer_style_panel.pt.download_avatar",
                        return_value=self._avatar_file()):
            self.panel.start({"work": self._work_with_avatar()})
            self.assertTrue(_pump(lambda: not self.panel._workers))
        self.panel.reset()
        self.assertIsNone(self.panel._image)
        self.assertEqual("未选参考图", self.panel.image_label.text())


class ToastTest(TestCase):
    """右下角的通知卡片：排队一条一条冒、贴在右下角、长文本换行不切字（用户 2026-09-29 要求）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from PyQt5 import QtWidgets
        from utils.ui import toast as toast_module
        self.module = toast_module
        self.host = QtWidgets.QWidget()
        self.host.resize(400, 300)
        self.toaster = toast_module.Toaster(self.host, seconds=30)

    def tearDown(self):
        if self.toaster._current is not None:
            self.toaster._current.dismiss()
        self.host.deleteLater()
        self.app.processEvents()

    def test_messages_show_one_at_a_time(self):
        for text in ("第一条", "第二条", "第三条"):
            self.toaster.show_message(text, "ok")
        self.assertEqual("第一条", self.toaster._current.label.text())
        self.assertEqual(3, self.toaster.pending)          # 正在显示 1 条 + 排队 2 条
        self.toaster._current.dismiss()
        self.assertEqual("第二条", self.toaster._current.label.text())
        self.toaster._current.dismiss()
        self.assertEqual("第三条", self.toaster._current.label.text())
        self.toaster._current.dismiss()
        self.assertIsNone(self.toaster._current)
        self.assertEqual(0, self.toaster.pending)

    def test_sits_in_the_bottom_right_corner(self):
        """用户 2026-09-29 要求：通知在窗口**右下角**弹（最初在左下角，当天改掉）。"""
        self.toaster.show_message("一条通知", "info")
        toast = self.toaster._current
        self.assertEqual(self.host.width() - toast.width() - self.module.MARGIN, toast.x())
        self.assertEqual(self.host.height() - toast.height() - self.module.MARGIN, toast.y())
        self.assertGreaterEqual(toast.x(), self.module.MARGIN)          # 窗口很窄时也得在窗口里

    def test_long_message_wraps_without_cutting_text(self):
        toast = self.module.Toast("很长的说明文字" * 20, "ok", 30, self.host)
        needed = toast.label.heightForWidth(toast.label.width())
        if needed > 0:
            self.assertGreaterEqual(toast.label.height(), needed)
        self.assertLessEqual(toast.width(), self.module.MAX_WIDTH)
        toast.dismiss()

    def test_empty_message_is_ignored(self):
        self.toaster.show_message("   ")
        self.assertIsNone(self.toaster._current)


class AccentButtonTest(TestCase):
    """主按钮 / 危险按钮的控件字体要跟着粗体（否则长标题会被裁）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_mark_accent_and_danger_use_bold_fonts(self):
        from PyQt5 import QtWidgets
        from utils.ui import theme
        accent = QtWidgets.QPushButton("提交到 Vocawiki")
        danger = QtWidgets.QPushButton("删除这一条")
        theme.mark_accent(accent)
        theme.mark_danger(danger)
        self.assertTrue(accent.font().bold())
        self.assertTrue(danger.font().bold())
        self.assertEqual("true", accent.property("accent"))
        self.assertEqual("true", danger.property("danger"))

    def test_bold_survives_font_rescaling(self):
        from PyQt5 import QtWidgets
        from utils.ui import theme
        button = QtWidgets.QPushButton("提交到 Vocawiki")
        theme.mark_accent(button)
        theme.scale_font(button, theme.FONT_SIZE_PX)      # 挂上「跟窗口缩放」的字号
        theme.rescale(button)
        self.assertTrue(button.font().bold(), "缩放后也必须是粗体，否则尺寸又会对不上")
        self.assertGreaterEqual(button.font().pixelSize(), theme.MIN_FONT_PX)


class SubmitPreviewHtmlTest(TestCase):
    """预览文档拼装（原 html 版 buildDoc 的 Python 版）。"""

    def test_uses_headhtml_skeleton(self):
        from utils.ui.submit_panel import build_preview_html
        result = {"html": "<p>正文</p>",
                  "head": '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
                          '<body class="mediawiki skin-citizen"></body></html>',
                  "css": ""}
        document = build_preview_html(result, "https://voca.wiki/")
        self.assertIn('class="mediawiki skin-citizen"', document)
        self.assertIn('<base href="https://voca.wiki/">', document)
        self.assertIn("<p>正文</p>", document)

    def test_strips_scripts_from_headhtml(self):
        from utils.ui.submit_panel import build_preview_html
        result = {"html": "", "head": "<html><head><script>alert(1)</script></head><body></body></html>",
                  "css": ""}
        self.assertNotIn("alert(1)", build_preview_html(result, ""))

    def test_falls_back_without_headhtml(self):
        from utils.ui.submit_panel import build_preview_html
        document = build_preview_html({"html": "<p>x</p>", "head": "", "css": ""}, "")
        self.assertIn("<!DOCTYPE html>", document)
        self.assertIn("<body", document)
        self.assertIn("<p>x</p>", document)

    def test_inlines_site_css(self):
        from utils.ui.submit_panel import build_preview_html
        document = build_preview_html({"html": "", "head": "<html><head></head><body></body></html>",
                                       "css": ".mw-body{color:red}"}, "")
        self.assertIn("data-vocawiki-site-css", document)
        self.assertIn(".mw-body{color:red}", document)

    def test_error_doc_escapes_message(self):
        from utils.ui.submit_panel import error_doc
        self.assertIn("&lt;b&gt;", error_doc("<b>糟糕</b>"))


class PreviewContextMenuTest(TestCase):
    """提交页预览的右键菜单是中文（用户 2026-09 要求）。

    菜单本身由 Qt 按上下文拼（`QWebEnginePage.createStandardContextMenu()`），这里把文案换成中文 ——
    PyQt5 没带 `qtwebengine_zh_CN.qm`，只有 en / de / ru。无头环境起不了浏览器内核
    （`VOCAWIKI_NO_WEBENGINE=1`），所以用假 page（返回一份英文条目的 QMenu）测「译文案」这一段。
    """

    class _Page:
        _NOTHING = object()

        def __init__(self, labels=(), menu=_NOTHING, error=None):
            self._error = error
            if menu is not self._NOTHING:
                self._menu = menu                    # 可以是 None（= 没有上下文数据）
            else:
                self._menu = QtWidgets.QMenu()
                for label in labels:
                    self._menu.addAction(QtWidgets.QAction(label, self._menu))

        def createStandardContextMenu(self):
            if self._error is not None:
                raise self._error
            return self._menu

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _labels(self, page):
        from utils.ui import submit_panel
        menu = submit_panel._preview_menu(page)
        self.assertIsNotNone(menu)
        return [action.text() for action in menu.actions()]

    def test_labels_are_chinese(self):
        # 这些英文原文是实测的 `QWebEnginePage` 标准动作文案
        labels = self._labels(self._Page(["Back", "Reload", "Copy", "Select all",
                                          "Copy link address", "Inspect"]))
        self.assertEqual(["返回", "重新载入", "复制", "全选", "复制链接地址", "检查元素"], labels)

    def test_unknown_label_is_kept(self):
        self.assertEqual(["??? 新动作 ???"], self._labels(self._Page(["??? 新动作 ???"])))

    def test_shortcut_is_preserved(self):
        self.assertEqual(["复制\tCtrl+C"], self._labels(self._Page(["Copy\tCtrl+C"])))

    def test_no_menu_without_context(self):
        """还没右键过时 `createStandardContextMenu()` 返回 None（Qt 的行为）→ 不弹菜单。"""
        from utils.ui import submit_panel
        self.assertIsNone(submit_panel._preview_menu(self._Page(menu=None)))

    def test_page_without_the_api_is_ignored(self):
        from utils.ui import submit_panel
        self.assertIsNone(submit_panel._preview_menu(object()))

    def test_error_is_swallowed(self):
        from utils.ui import submit_panel
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(submit_panel._preview_menu(self._Page(error=RuntimeError("内核崩了"))))

    def test_install_switches_to_a_custom_menu(self):
        """装完之后右键事件走我们自己的槽（`QWidget` 设了 CustomContextMenu 就不再弹默认菜单）。"""
        from PyQt5 import QtCore
        from utils.ui import submit_panel, theme
        widget = QtWidgets.QPlainTextEdit()
        with mock.patch.object(theme, "ui_language", return_value="zh"):
            submit_panel._install_preview_menu(widget)
        self.assertEqual(QtCore.Qt.CustomContextMenu, widget.contextMenuPolicy())
        # 假控件没有 page()：槽里必须自己兜住，不能把异常抛回 Qt
        widget.customContextMenuRequested.emit(QtCore.QPoint(1, 1))

    def test_english_interface_keeps_the_builtin_menu(self):
        """`lang: en` 时别接管（内核自带的英文菜单本来就是想要的）。"""
        from PyQt5 import QtCore
        from utils.ui import submit_panel, theme
        widget = QtWidgets.QPlainTextEdit()
        with mock.patch.object(theme, "ui_language", return_value="en"):
            submit_panel._install_preview_menu(widget)
        self.assertEqual(QtCore.Qt.DefaultContextMenu, widget.contextMenuPolicy())


class LaunchTest(TestCase):
    """把整套启动流程跑一遍：窗口 + 后台线程 + 提问 + 收尾。"""

    def tearDown(self):
        reset_font_scale()      # launch() 会开一个 1260×880 的窗口，字号缩放会留在全局

    def test_launch_runs_flow_in_window(self):
        from PyQt5 import QtCore, QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        answers = []
        service = {"done": False}

        def flow():
            answers.append(ui.ask_response("歌名？"))
            service["done"] = True
            return "output-dir"

        def poll():
            window = ui.main_window()
            if window is None:
                return
            tab = window.prompt_tab
            if tab._request is not None:
                tab.text_input.setText("初音未来的消失")
                tab.submit_text()
            elif service["done"]:
                app.quit()

        timer = QtCore.QTimer()
        timer.timeout.connect(poll)
        timer.start(30)
        stdout, stderr = sys.stdout, sys.stderr
        try:
            with mock.patch.object(QtWidgets.QMessageBox, "exec_", return_value=0):
                code = ui.run(flow)
        finally:
            timer.stop()
        self.assertEqual(0, code)
        self.assertEqual(["初音未来的消失"], answers)
        self.assertFalse(ui.is_active(), "跑完要把门面里的窗口清掉")
        self.assertIs(sys.stdout, stdout, "启动结束后要恢复 stdout")
        self.assertIs(sys.stderr, stderr, "启动结束后要恢复 stderr")


class AppIconTest(TestCase):
    """窗口（标题栏 / 任务栏）图标：程序目录 → 包内资源 → exe 自带图标。"""

    def setUp(self):
        from PyQt5 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _icon_dir(self, directory):
        from PyQt5 import QtGui
        assets = Path(directory) / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        image = QtGui.QImage(16, 16, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("#39c5bb"))
        self.assertTrue(image.save(str(assets / "icon.png"), "PNG"))
        return assets

    def _app_icon(self, application_path, bundle=None, frozen=False):
        from config import config as config_module
        from utils.ui import window as window_module
        patches = [mock.patch.object(config_module, "application_path", application_path),
                   mock.patch.object(sys, "_MEIPASS", bundle, create=True),
                   mock.patch.object(sys, "frozen", frozen, create=True)]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return window_module._app_icon()

    def test_prefers_the_icon_next_to_the_app(self):
        self._icon_dir(self.root)
        icon = self._app_icon(self.root)
        self.assertIsNotNone(icon)
        self.assertFalse(icon.isNull())

    def test_uses_the_bundled_icon_when_nothing_is_next_to_the_app(self):
        bundle = Path(self._tmp.name).joinpath("bundle")
        self._icon_dir(bundle)                       # 只放在 sys._MEIPASS 里
        icon = self._app_icon(self.root, bundle=str(bundle))
        self.assertIsNotNone(icon)
        self.assertFalse(icon.isNull())

    def test_falls_back_to_the_executable_icon_when_frozen(self):
        icon = self._app_icon(self.root, frozen=True)
        self.assertIsNotNone(icon, "打包后应该能读 exe 自己的图标")
        self.assertFalse(icon.isNull())

    def test_none_when_there_is_no_icon_at_all(self):
        self.assertIsNone(self._app_icon(self.root))

    def test_main_window_carries_the_icon(self):
        from utils.ui.window import MainWindow
        window = MainWindow()
        self.addCleanup(window.deleteLater)
        from utils.ui import window as window_module
        icon = window_module._app_icon()
        if icon is None:
            self.skipTest("当前环境找不到图标文件")
        window.setWindowIcon(icon)
        self.assertFalse(window.windowIcon().isNull())


class AuxToolsPanelTest(TestCase):
    """样式页的辅助工具：测量 / 参照物（点画布 → 画画 → 清掉）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils.ui.style_panel import StylePanel
        self.panel = StylePanel()
        self.panel.start({"initial": "", "hover": False})

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    @staticmethod
    def _click(widget, x, y, button=None):
        from PyQt5 import QtCore, QtGui
        button = button or QtCore.Qt.LeftButton
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(x, y),
                                  button, button, QtCore.Qt.NoModifier)
        widget.mousePressEvent(event)

    def test_buttons_switch_modes(self):
        self.assertIsNone(self.panel.aux.mode)
        self.panel.measure_button.click()
        self.assertEqual("measure", self.panel.aux.mode)
        # 点「开始放置」会顶掉测量模式
        self.panel.guide_button.click()
        self.assertEqual("guide", self.panel.aux.mode)
        self.assertFalse(self.panel.measure_button.isChecked())
        # 再点一次自己就退出
        self.panel.guide_button.click()
        self.assertIsNone(self.panel.aux.mode)

    def test_measure_on_preview_canvas(self):
        self.panel.measure_button.click()
        self._click(self.panel.preview, 20, 30)
        self._click(self.panel.preview, 50, 70)
        self.assertIn("距离 50px", self.panel.measure_label.text())
        self.assertIn("预览台", self.panel.measure_label.text())
        self.assertEqual([(20.0, 30.0), (50.0, 70.0)], self.panel.aux.points["preview"])

    def test_measure_on_cover_canvas(self):
        self.panel.measure_button.click()
        self._click(self.panel.cover_view, 0, 0)
        self._click(self.panel.cover_view, 3, 4)
        self.assertIn("封面", self.panel.measure_label.text())
        self.assertIn("距离 5px", self.panel.measure_label.text())
        # 两块画布各记各的
        self.assertEqual([], self.panel.aux.points["preview"])

    def test_clear_measure_button(self):
        self.panel.measure_button.click()
        self._click(self.panel.preview, 1, 1)
        self._click(self.panel.preview, 2, 2)
        self.panel.measure_clear_button.click()
        self.assertEqual([], self.panel.aux.points["preview"])
        self.assertIn("点两下量距离", self.panel.measure_label.text())

    def test_guide_uses_settings_and_clears(self):
        self.panel.guide_button.click()
        self.panel.guide_kind_combo.setCurrentIndex(
            self.panel.guide_kind_combo.findData("rect"))
        self.panel.guide_angle_spin.setValue(45)
        self.panel.guide_w_spin.setValue(200)
        self.panel.guide_h_spin.setValue(80)
        self.assertTrue(self.panel.guide_h_spin.isEnabled())
        self._click(self.panel.preview, 100, 100)
        guide = self.panel.aux.guides[0]
        self.assertEqual(("rect", 45, 200, 80), (guide["kind"], guide["angle"],
                                                 guide["w"], guide["h"]))
        self.assertIn("共 1 个参照物", self.panel.guide_label.text())
        self.assertIn("矩形 1 个", self.panel.guide_label.text())
        self.panel.guide_clear_button.click()
        self.assertEqual([], self.panel.aux.guides)

    def test_line_kind_disables_height(self):
        self.panel.guide_kind_combo.setCurrentIndex(self.panel.guide_kind_combo.findData("line"))
        self.assertFalse(self.panel.guide_h_spin.isEnabled())

    def test_guide_limit_is_reported_without_dialog(self):
        from utils.ui import aux_tools
        self.panel.guide_button.click()
        for index in range(aux_tools.MAX_GUIDES):
            self._click(self.panel.preview, index, 5)
        self._click(self.panel.preview, 0, 0)
        self.assertIn("最多 24 个", self.panel.guide_label.text())
        self.assertEqual(aux_tools.MAX_GUIDES, len(self.panel.aux.guides))

    def test_right_click_exits_mode(self):
        from PyQt5 import QtCore
        self.panel.guide_button.click()
        self._click(self.panel.preview, 10, 10, QtCore.Qt.RightButton)
        self.assertIsNone(self.panel.aux.mode)
        self.assertFalse(self.panel.guide_button.isChecked())

    def test_escape_exits_aux_mode_first(self):
        from PyQt5 import QtCore, QtGui
        self.panel.measure_button.click()
        self.panel.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape,
                                                 QtCore.Qt.NoModifier))
        self.assertIsNone(self.panel.aux.mode)

    def test_picking_colour_exits_aux_mode(self):
        self.panel.measure_button.click()
        self.panel._pick_field = None
        self.panel.cover_view._image = None
        with mock.patch("PyQt5.QtWidgets.QMessageBox.information"):
            self.panel._start_pick(self.panel.bg_field)
        self.assertIsNone(self.panel.aux.mode)

    def test_start_clears_previous_song_marks(self):
        self.panel.guide_button.click()
        self._click(self.panel.preview, 10, 10)
        self.panel.start({"initial": "", "hover": False})
        self.assertEqual([], self.panel.aux.guides)
        self.assertEqual([], self.panel.aux.points["preview"])
        self.assertIsNone(self.panel.aux.mode)

    def test_canvas_paints_with_aux_objects(self):
        """画布真的画得出来（含参照物与测量）——顺带验证 paint 不会抛异常。"""
        from PyQt5 import QtGui
        self.panel.measure_button.click()
        self._click(self.panel.preview, 30, 40)
        self._click(self.panel.preview, 120, 60)
        self.panel.guide_button.click()
        self._click(self.panel.cover_view, 40, 40)
        pixmap = QtGui.QPixmap(self.panel.preview.size())
        self.panel.preview.render(pixmap)
        self.panel.cover_view.render(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertEqual([], GUARDED_ERRORS, GUARDED_ERRORS)


class SettingsPanelTest(TestCase):
    """「设置」页：在界面里改 config.yaml 与 wiki_credentials.yaml。"""

    CONFIG = """\
--- !Config
save_to_file: ""
# 程序语言
lang: "zh"
output_dir: "output"
proxies: ""
wikitext: !WikitextConfig
  # 联网查 P主模板
  producer_template: false
  ai_lyrics: true
color: !ColorConfig
  color_editor: false
  ai_css: true
  ai_prompt_songbox: ""
image: !ImageConfig
  download_cover: false
  crop: true
wiki: !WikiConfig
  api_url: "https://voca.wiki/api.php"
  submit_window: false
"""
    CREDENTIALS = ('# 登录凭据\n'
                   'username: ""\n'
                   'password: ""\n'
                   'ai_provider: "openai"\n'
                   'ai_base_url: "https://api.deepseek.com/v1"\n'
                   'ai_model: "deepseek-flash"\n'
                   'ai_api_key: ""\n'
                   'ai_thinking: false\n')

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from config import config as config_module
        self.config_module = config_module
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_file = self.root.joinpath("config.yaml")
        self.config_file.write_text(self.CONFIG, encoding="utf-8")
        self.creds_file = self.root.joinpath("wiki_credentials.yaml")
        self.creds_file.write_text(self.CREDENTIALS, encoding="utf-8")
        self._original = (config_module.config_xxx, config_module.program_output_path,
                          config_module.get_config().lang)
        patcher = mock.patch.object(config_module, "application_path", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore_globals)
        config_module.load_config(self.config_file)
        from utils.ui.settings_panel import SettingsPanel
        self.panel = SettingsPanel()

    def _restore_globals(self):
        """设置页会真的重新载入配置，测试完把全局状态放回去，别影响其它用例。"""
        config_module, original = self.config_module, self._original
        config_module.config_xxx, config_module.program_output_path, lang = original
        from i18n.i18n import set_language
        set_language(lang or "zh")
        from utils.ui import theme
        theme.apply_font("", "")                    # 「应用字体」也是全局状态，别留给下个用例
        panel = getattr(self, "panel", None)
        if panel is not None:
            panel.deleteLater()
            self.app.processEvents()
        self._tmp.cleanup()

    def _check(self, key: str):
        return dict(self.panel._bool_fields)[key]

    def test_fields_reflect_loaded_config(self):
        self.assertEqual("zh", self.panel.lang_combo.currentData())
        self.assertFalse(self._check("wikitext.producer_template").isChecked())
        self.assertTrue(self._check("wikitext.ai_lyrics").isChecked())
        self.assertEqual("https://voca.wiki/api.php",
                         dict(self.panel._text_fields)["wiki.api_url"].text())
        self.assertEqual("deepseek-flash", self.panel.ai_model_edit.text())
        self.assertIn("config.yaml", self.panel.paths_label.text())

    def test_collect_keeps_credentials_out_of_config(self):
        self.panel.username_edit.setText("user@bot")
        self.panel.ai_key_edit.setText("sk-test")
        config_values, credential_values = self.panel.collect()
        self.assertNotIn("username", config_values)
        self.assertNotIn("ai_api_key", config_values)
        self.assertEqual("user@bot", credential_values["username"])
        self.assertEqual("sk-test", credential_values["ai_api_key"])
        self.assertIn("wikitext.producer_template", config_values)

    def test_edit_rate_spin_round_trip(self):
        """速率墙：默认一分钟 3 次（配置文件没写就是 3），改完能写回 config.yaml。"""
        spin = dict(self.panel._number_fields)["wiki.edits_per_minute"]
        self.assertEqual(3, spin.value())
        self.assertEqual((0, 60), (spin.minimum(), spin.maximum()))
        self.assertEqual(" 次", spin.suffix())
        self.assertIn("算一次编辑", spin.toolTip())             # 提示里说明白了「什么算一次编辑」
        spin.setValue(0)
        config_values, _credentials = self.panel.collect()
        self.assertEqual(0, config_values["wiki.edits_per_minute"])    # 存成数字（0 = 不限制）
        spin.setValue(10)
        self.assertTrue(self.panel.save())
        text = self.config_file.read_text(encoding="utf-8")
        self.assertIn("  edits_per_minute: 10", text)           # 写在 wiki 节里，没顶格
        self.assertEqual(10, self.config_module.get_config().wiki.edits_per_minute)

    def test_save_writes_both_files_and_reloads(self):
        self._check("wikitext.producer_template").setChecked(True)
        self.panel.username_edit.setText("user@bot")
        self.panel.ai_key_edit.setText("sk-test")
        self.assertTrue(self.panel.save())
        config_text = self.config_file.read_text(encoding="utf-8")
        self.assertIn("  producer_template: true", config_text)     # 写进了 wikitext 节
        self.assertNotIn("\nproducer_template:", config_text)       # 没有跑到顶格去
        self.assertIn("# 联网查 P主模板", config_text)              # 注释还在
        creds_text = self.creds_file.read_text(encoding="utf-8")
        self.assertIn('username: "user@bot"', creds_text)
        self.assertIn('ai_api_key: "sk-test"', creds_text)
        # 保存后重新载入，get_config() 拿到的是新值
        self.assertTrue(self.config_module.get_config().wikitext.producer_template)
        self.assertIn("已保存", self.panel.status_label.text())

    def test_save_reports_failure(self):
        with mock.patch("utils.ui.settings_panel.save_config_values", return_value=False):
            self.assertFalse(self.panel.save())
        self.assertIn("保存失败", self.panel.status_label.text())

    def test_reload_discards_unsaved_changes(self):
        self._check("wikitext.ai_lyrics").setChecked(False)
        self.panel.lang_combo.setCurrentIndex(self.panel.lang_combo.findData("en"))
        self.panel.load()
        self.assertTrue(self._check("wikitext.ai_lyrics").isChecked())
        self.assertEqual("zh", self.panel.lang_combo.currentData())

    def test_multiline_prompt_is_saved(self):
        prompt = "第一行\n第二行"
        dict(self.panel._area_fields)["color.ai_prompt_songbox"].setPlainText(prompt)
        self.assertTrue(self.panel.save())
        self.assertEqual(prompt, self.config_module.get_config().color.ai_prompt_songbox)

    # —— 导入配置文件 ——

    def _other_file(self, name: str, text: str) -> str:
        path = self.root.joinpath(name)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _import(self, paths):
        """点「导入配置文件」并选中 paths（绕过文件框）。"""
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileNames",
                               return_value=(list(paths), "")) as dialog:
            self.panel._import_file()
        return dialog

    def test_import_config_fills_the_form_but_does_not_save(self):
        """导入 config.yaml：值填进界面，**不写盘**——要再点「保存」才落到文件里。"""
        imported = self._other_file("other-config.yaml", """\
--- !Config
lang: "en"
output_dir: "D:/songs"
wikitext: !WikitextConfig
  producer_template: true
color: !ColorConfig
  color_editor: true
""")
        self._import([imported])
        self.assertEqual("en", self.panel.lang_combo.currentData())
        self.assertEqual("D:/songs", dict(self.panel._text_fields)["output_dir"].text())
        self.assertTrue(self._check("wikitext.producer_template").isChecked())
        self.assertTrue(self._check("color.color_editor").isChecked())
        self.assertIn("已导入", self.panel.status_label.text())
        self.assertIn("保存", self.panel.status_label.text())
        # 还没保存：文件里还是原样
        self.assertIn("  producer_template: false", self.config_file.read_text(encoding="utf-8"))
        self.assertFalse(self.config_module.get_config().wikitext.producer_template)
        # 点「保存」才写回并生效
        self.assertTrue(self.panel.save())
        self.assertIn("  producer_template: true", self.config_file.read_text(encoding="utf-8"))
        self.assertTrue(self.config_module.get_config().wikitext.producer_template)

    def test_import_config_keeps_settings_missing_from_the_file(self):
        """文件里没写的项保持界面现状：导入一份残缺的配置不会把别的设置清空。"""
        imported = self._other_file("partial.yaml", 'lang: "en"\n')
        self._import([imported])
        self.assertEqual("en", self.panel.lang_combo.currentData())
        self.assertEqual("output", dict(self.panel._text_fields)["output_dir"].text())
        self.assertTrue(self._check("wikitext.ai_lyrics").isChecked())
        self.assertEqual("https://voca.wiki/api.php",
                         dict(self.panel._text_fields)["wiki.api_url"].text())

    def test_import_credentials_fills_only_the_credentials(self):
        imported = self._other_file("other-creds.yaml", """\
username: "user@bot"
password: "secret"
ai_provider: "anthropic"
ai_base_url: "https://api.deepseek.com/anthropic"
ai_model: "deepseek-flash"
ai_api_key: "sk-imported"
ai_thinking: true
""")
        self._import([imported])
        self.assertEqual("user@bot", self.panel.username_edit.text())
        self.assertEqual("secret", self.panel.password_edit.text())
        self.assertEqual("sk-imported", self.panel.ai_key_edit.text())
        self.assertEqual("anthropic", self.panel.ai_provider_combo.currentData())
        self.assertTrue(self.panel.ai_thinking_check.isChecked())
        # 配置那半边一个字都不动
        self.assertEqual("zh", self.panel.lang_combo.currentData())
        self.assertEqual("output", dict(self.panel._text_fields)["output_dir"].text())
        self.assertFalse(self.config_module.get_config().wikitext.producer_template)

    def test_import_both_files_at_once(self):
        config_file = self._other_file("other-config.yaml", 'lang: "en"\n')
        creds_file = self._other_file("other-creds.yaml", 'username: "user@bot"\n')
        self._import([config_file, creds_file])
        self.assertEqual("en", self.panel.lang_combo.currentData())
        self.assertEqual("user@bot", self.panel.username_edit.text())
        self.assertIn("other-config.yaml", self.panel.status_label.text())
        self.assertIn("other-creds.yaml", self.panel.status_label.text())

    def test_import_rejects_unknown_file(self):
        bad = self._other_file("random.yaml", "foo: 1\n")
        self._import([bad])
        self.assertIn("导入失败", self.panel.status_label.text())
        self.assertIn("既不像", self.panel.status_label.text())
        self.assertEqual("zh", self.panel.lang_combo.currentData())      # 界面没被改动

    def test_import_mixed_file_fills_both_halves(self):
        """一份文件里既写配置又写凭据时，凭据那几项不能被丢掉。

        2026-09 用户报「导入 wiki_credentials.yaml 没有进设置页」：旧代码按**整份文件**
        归类，混合文件被判成 config，于是 username / ai_api_key 一项都没填。
        """
        imported = self._other_file("mixed.yaml", 'lang: "en"\nusername: "user@bot"\n'
                                                 'ai_api_key: "sk-m"\n')
        self._import([imported])
        self.assertEqual("en", self.panel.lang_combo.currentData())
        self.assertEqual("user@bot", self.panel.username_edit.text())
        self.assertEqual("sk-m", self.panel.ai_key_edit.text())

    def test_import_reports_which_items_changed(self):
        """状态栏写出这次改了哪几项（用界面上的中文名），不然看不出导入有没有生效。"""
        imported = self._other_file("creds.yaml", 'ai_model: "deepseek-chat"\n')
        self._import([imported])
        message = self.panel.status_label.text()
        self.assertIn("已导入", message)
        self.assertIn("模型名", message)
        self.assertIn("保存", message)

    def test_import_says_so_when_values_are_identical(self):
        """文件里的值与界面上一模一样时要说一声 —— 否则看着就像「导入没反应」。"""
        imported = self._other_file("same.yaml", 'ai_model: "deepseek-flash"\n')
        self._import([imported])
        message = self.panel.status_label.text()
        self.assertIn("已导入", message)
        self.assertIn("与当前一致", message)
        self.assertEqual("deepseek-flash", self.panel.ai_model_edit.text())

    # —— 应用字体（点输入栏弹文件框选字体文件） ——

    def test_font_defaults_to_the_system_default(self):
        values, _creds = self.panel.collect()
        self.assertEqual("", values["font_family"], "没选字体 = config.yaml 里的空串")
        self.assertEqual("", values["font_file"])
        self.assertEqual("", self.panel.font_edit.text())
        self.assertTrue(self.panel.font_edit.isReadOnly(), "这一栏是当按钮用的，不让人手打路径")
        self.assertIn(theme.DEFAULT_FONT_FAMILY, self.panel.font_edit.placeholderText())

    def test_clicking_the_font_field_picks_a_font_file(self):
        """用户 2026-09 要求：点输入栏 → 选字体文件 → 界面字体立刻换成它。"""
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        picked = []
        self.panel.font_changed.connect(lambda path, family: picked.append((path, family)))
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                               return_value=(font_file, "")) as dialog:
            self.panel.font_edit.browse_requested.emit()        # 点一下输入栏
        self.assertTrue(dialog.called, "点输入栏就该弹文件选择框")
        family = theme.load_font_file(font_file)
        self.assertTrue(family, "字体文件里应该能读出家族名")
        self.assertEqual([(font_file, family)], picked, "选完要通知主窗口换字体")
        self.assertEqual(font_file, theme.font_file())
        self.assertEqual(family, theme.font_family())
        self.assertIn(Path(font_file).name, self.panel.font_edit.text())
        self.assertIn("已换成", self.panel.status_label.text())
        # 「保存」是把路径写进 config.yaml，下次启动照它重新加载
        values, _creds = self.panel.collect()
        self.assertEqual(font_file, values["font_file"])
        self.assertEqual(family, values["font_family"])
        self.assertTrue(self.panel.save())
        self.assertEqual(font_file, self.config_module.get_config().font_file)
        self.assertEqual(family, self.config_module.get_config().font_family)
        self.assertIn("font_file:", self.config_file.read_text(encoding="utf-8"))

    def test_font_can_be_reset_to_default(self):
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                               return_value=(font_file, "")):
            self.panel._choose_font_file()
        self.panel._reset_font()
        values, _creds = self.panel.collect()
        self.assertEqual("", values["font_file"])
        self.assertEqual("", values["font_family"])
        self.assertEqual("", self.panel.font_edit.text())
        self.assertEqual(theme.DEFAULT_FONT_FAMILY, theme.font_family())

    def test_bad_font_file_keeps_the_current_font(self):
        bad = self.root.joinpath("not-a-font.txt")
        bad.write_text("这不是字体", encoding="utf-8")
        picked = []
        self.panel.font_changed.connect(lambda path, family: picked.append(path))
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                               return_value=(str(bad), "")):
            self.panel._choose_font_file()
        self.assertEqual([], picked, "读不出来的文件不该换字体")
        self.assertIn("读不出", self.panel.status_label.text())
        self.assertEqual("", self.panel.collect()[0]["font_file"])

    def test_font_file_from_config_is_shown(self):
        """config.yaml 里已经写了 font_file 时，界面上要把文件名显示出来。"""
        self.config_module.config_xxx.font_file = str(self.root.joinpath("MyFont.ttf"))
        self.config_module.config_xxx.font_family = "My Font"
        self.panel.load()
        self.assertIn("MyFont.ttf", self.panel.font_edit.text())
        self.assertIn("My Font", self.panel.font_edit.text())
        values, _creds = self.panel.collect()
        self.assertEqual("My Font", values["font_family"])
        self.assertEqual(str(self.root.joinpath("MyFont.ttf")), values["font_file"])
        self.config_module.config_xxx.font_file = ""
        self.config_module.config_xxx.font_family = ""
        self.panel.load()
        self.assertEqual("", self.panel.font_edit.text())

    def test_discard_reload_puts_the_saved_font_back(self):
        """点了「放弃改动并重新载入」：刚挑的字体要退回去（通知主窗口重套样式表）。"""
        font_file = _some_font_file()
        if font_file is None:
            self.skipTest("这台机器上没有可用的字体文件")
        with mock.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                               return_value=(font_file, "")):
            self.panel._choose_font_file()
        self.assertEqual(font_file, theme.font_file())
        picked = []
        self.panel.font_changed.connect(lambda path, family: picked.append((path, family)))
        self.panel.reload_button.click()
        self.assertEqual([("", "")], picked)
        self.assertEqual("", self.panel.collect()[0]["font_file"])
        self.assertEqual("", self.panel.font_edit.text())


class StyleStateBridgeTest(TestCase):
    """门面里的 open_style_editor 要把编辑器结果翻译回 ColorEditing。"""

    def test_parse_color_wiki_with_editor_output(self):
        from utils.color_editor import parse_color_wiki
        text = st.full_wiki_text([st.blank_state() for _ in range(3)], st.tpl_default_states())
        editing = parse_color_wiki(text, lyrics_hover=True)
        self.assertTrue(editing.lyrics_hover)
        # 三个颜色块始终写出来（空值 = 沿用模板默认），与原 html 版行为一致
        self.assertEqual("|颜色1 = \n|颜色2 = \n|颜色3 = ", editing.songbox)
        # lbgcolor / ltcolor 也是恒输出的
        self.assertEqual("#000000", editing.introduction_bg)
        self.assertEqual("#ffffff", editing.introduction_fg)

    def test_parse_color_wiki_keeps_colors(self):
        from utils.color_editor import parse_color_wiki
        states = [st.blank_state() for _ in range(3)]
        states[0].update(bgSolid="#1e90ff", color="#ffffff")
        editing = parse_color_wiki(st.songbox_wiki_text(states))
        self.assertIn("|颜色1 = #1e90ff", editing.songbox)
        self.assertIn("color: #ffffff;", editing.songbox)

    def test_style_editor_result_is_parsed(self):
        from models.color import ColorEditing
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "_window", mock.Mock()) as window:
            window.run_style_editor.return_value = ("|lbgcolor = #123456\n|ltcolor = #ffffff", True)
            editing = ui.open_style_editor("初始", None, False)
        self.assertIsInstance(editing, ColorEditing)
        self.assertEqual("#123456", editing.introduction_bg)
        self.assertTrue(editing.lyrics_hover)
