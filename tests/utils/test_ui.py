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
from unittest import TestCase
from unittest import mock

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
        self.window = MainWindow()

    def tearDown(self):
        self.window.deleteLater()
        self.app.processEvents()
        self.assertEqual([], GUARDED_ERRORS, f"界面回抛出过异常：{GUARDED_ERRORS}")

    def test_panels_are_registered_but_disabled(self):
        self.assertEqual({"style", "lyrics", "submit"}, set(self.window.panels))
        for panel in self.window.panels.values():
            index = self.window.tabs.indexOf(panel)
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
        self.assertTrue(_pump(lambda: self.window.tabs.currentWidget() is panel))
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

    def test_settings_tab_is_always_enabled(self):
        index = self.window.tabs.indexOf(self.window.settings_panel)
        self.assertGreaterEqual(index, 0)
        self.assertTrue(self.window.tabs.isTabEnabled(index))
        for key in self.window.panels:
            self.assertFalse(self.window.tabs.isTabEnabled(self.window.tabs.indexOf(self.window.panels[key])))

    def test_settings_button_switches_to_settings_tab(self):
        self.window.tabs.setCurrentWidget(self.window.panels["style"])
        self.window.settings_button.click()
        self.assertIs(self.window.settings_panel, self.window.tabs.currentWidget())

    def test_style_panel_can_jump_to_settings(self):
        self.window.panels["style"].settings_requested.emit()
        self.assertIs(self.window.settings_panel, self.window.tabs.currentWidget())

    def test_status_text_lands_in_status_strip(self):
        self.window.set_status("干活中…")
        self.assertEqual("干活中…", self.window.status_label.text())
        self.window.append_log("一行日志")
        self.assertIn("一行日志", self.window.log_tab.view.toPlainText())

    # —— 左侧竖栏 / 头像 ——
    def test_sidebar_lists_the_entry_feature(self):
        self.assertEqual(["entry"], self.window.sidebar.keys())
        self.assertEqual("entry", self.window.sidebar.current_feature())
        self.assertIs(self.window.settings_button, self.window.sidebar.settings_button,
                      "设置齿轮就是侧栏底部那颗按钮")

    def test_feature_switch_keeps_workflow_page(self):
        self.window.sidebar.feature_selected.emit("entry")
        self.assertIs(self.window._feature_pages["entry"],
                      self.window.feature_stack.currentWidget())
        self.assertIn("已切换到", self.window.status_label.text())

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
        self.assertIs(self.window.settings_panel, self.window.tabs.currentWidget())
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
             mock.patch("utils.ui.avatar.load_bytes", return_value=None):
            self.window.avatar_button.click()
            self.assertTrue(_pump(lambda: self.window.avatar_button.logged_in))
        self.assertIn("TimeRen", self.window.avatar_button.toolTip())
        self.assertIn("已登录 Vocawiki", self.window.status_label.text())

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
             mock.patch("utils.ui.avatar.load_bytes", return_value=None):
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


class StylePanelTest(TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from utils.ui.style_panel import StylePanel
        self.panel = StylePanel()

    def tearDown(self):
        self.panel.deleteLater()
        self.app.processEvents()
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

    def test_ai_panel_shows_context_and_links_to_settings(self):
        requested = []
        self.panel.settings_requested.connect(lambda: requested.append(True))
        with mock.patch("utils.color_editor.EditorApi.get_ai_context",
                        return_value={"enabled": True, "hidden": False, "provider": "openai",
                                      "model": "deepseek-flash", "prompts": {"songbox": "提示"}}):
            self.panel.start({"initial": "", "hover": False})
        self.assertTrue(self.panel.ai_button.isEnabled())
        self.assertIn("deepseek-flash", self.panel.ai_tip.text())
        self.assertFalse(hasattr(self.panel, "ai_key_edit"), "密钥输入框已经挪到「设置」页")
        self.panel.ai_settings_button.click()
        self.assertEqual([True], requested)

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
        self.assertEqual(["初音未来"], self.panel._marks["0"])
        self.panel._splits["0"] = {"jap": [1]}
        self.panel._refresh_marker(force=True)
        # 切开后：整行标记会落到每一段上（不会只给第一段）
        self.panel._toggle_mark(0, 1, "初音未来", True)
        self.assertEqual([["初音未来"], ["初音未来"]], self.panel._marks["0"])
        # 取消第一段
        self.panel._toggle_mark(0, 0, "初音未来", False)
        self.assertEqual([[], ["初音未来"]], self.panel._marks["0"])
        self.panel._merge_line(0)
        self.assertNotIn("0", self.panel._splits)
        self.panel._clear_marks()
        self.assertEqual({}, self.panel._marks)

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
        self.panel.start({"api": self.api})
        self.api.submit.return_value = {"ok": True, "message": "已提交「测试曲」",
                                        "url": "https://voca.wiki/wiki/x"}
        with mock.patch.object(self.panel, "_show_done_dialog") as done:
            self.panel._submit()
            self.assertTrue(_pump(lambda: done.called))
        self.api.submit.assert_called_once_with("正文内容", "摘要", False)
        self.assertTrue(self.panel._finished)

    def test_submit_failure_keeps_retry(self):
        self.panel.start({"api": self.api})
        self.api.submit.return_value = {"ok": False, "error": "网络错误"}
        self.panel._submit()
        self.assertTrue(_pump(lambda: "网络错误" in self.panel.status_label.text()))
        self.assertFalse(self.panel._finished)
        self.assertTrue(self.panel.submit_button.isEnabled())
        self.assertIn("重试", self.panel.login_label.text())

    def test_backlinks_open_dialog(self):
        self.panel.start({"api": self.api})
        result = {"ok": True, "message": "已提交", "backlinkOld": "旧", "backlinkNew": "新",
                  "backlinks": [{"title": "A", "count": 2, "kind": "wiki 链接"}]}
        with mock.patch("PyQt5.QtWidgets.QDialog.exec_") as exec_dialog:
            self.panel._on_submitted(result)
        exec_dialog.assert_called_once()

    def test_webengine_is_skipped_in_tests(self):
        # 单测里不装浏览器内核：预览区为空，界面提供「在浏览器里打开预览」按钮
        self.assertIsNone(self.panel.preview_view)
        self.assertTrue(self.panel.browser_button.isEnabled())


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


class LaunchTest(TestCase):
    """把整套启动流程跑一遍：窗口 + 后台线程 + 提问 + 收尾。"""

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
