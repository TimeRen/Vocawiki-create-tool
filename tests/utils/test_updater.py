"""设置页「检查更新」的单元测试：GitHub release 解析 / 版本比较 / 下载 / 替换 exe。

全部离线（HTTP 与 subprocess 都 mock），不装包、不联网。
"""
import io
import os
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

from utils import updater

RELEASE_JSON = {
    "tag_name": "build-27",
    "published_at": "2026-10-20T08:14:32Z",
    "html_url": "https://github.com/TimeRen/Vocawiki-create-tool/releases/tag/build-27",
    "body": "version: 1.27\ncommit: abc1234\nbuilt_at: 2026-10-20T08:13:00Z\n",
    "assets": [
        {"name": "Vocawiki-create-tool (1.27).apk",
         "browser_download_url": "https://example.test/apk"},
        {"name": "Vocawiki-create-tool (1.27).zip",
         "browser_download_url": "https://example.test/zip"},
    ],
}


class _Response:
    def __init__(self, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._content = content
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1024):
        yield from (self._content[i:i + chunk_size] for i in range(0, len(self._content), chunk_size))


class FetchLatestTest(TestCase):
    def test_reads_version_and_assets(self):
        with mock.patch.object(updater, "http_get", return_value=_Response(payload=RELEASE_JSON)):
            release = updater.fetch_latest()
        self.assertEqual("1.27", release.version, "正文里的 `version:` 优先于 tag")
        self.assertEqual("build-27", release.tag)
        self.assertIn("releases/tag/build-27", release.page_url)
        self.assertEqual(("Vocawiki-create-tool (1.27).apk", "https://example.test/apk"),
                         release.asset(updater.APK_SUFFIX))
        self.assertEqual("https://example.test/zip", release.for_platform("windows")[1])
        self.assertEqual("https://example.test/apk", release.for_platform("android")[1])

    def test_missing_release_says_so(self):
        with mock.patch.object(updater, "http_get", return_value=_Response(status_code=404)):
            with self.assertRaises(updater.UpdaterError) as caught:
                updater.fetch_latest()
        self.assertIn("还没有发布过版本", str(caught.exception))

    def test_falls_back_to_the_newest_formal_release(self):
        """滚动构建那个 tag 还没建出来时，退回 GitHub 认为最新的正式版。"""
        responses = [_Response(status_code=404),
                     _Response(payload=dict(RELEASE_JSON, assets=[]))]
        with mock.patch.object(updater, "http_get", side_effect=responses) as http:
            release = updater.fetch_latest()
        self.assertEqual("1.27", release.version)
        self.assertEqual(2, http.call_count, "先问 tags/latest，再退到 releases/latest")
        self.assertIn("/releases/tags/latest", http.call_args_list[0].args[0])
        self.assertIn("/releases/latest", http.call_args_list[1].args[0])

    def test_network_errors_are_wrapped(self):
        with mock.patch.object(updater, "http_get", side_effect=OSError("boom")):
            with self.assertRaises(updater.UpdaterError) as caught:
                updater.fetch_latest()
        self.assertIn("连不上 GitHub", str(caught.exception))

    def test_a_release_without_assets_is_reported(self):
        payload = dict(RELEASE_JSON, assets=[])
        with mock.patch.object(updater, "http_get", return_value=_Response(payload=payload)):
            release = updater.fetch_latest()
        self.assertEqual(("", ""), release.for_platform("android"))


class VersionCompareTest(TestCase):
    def test_numeric_compare(self):
        self.assertTrue(updater.is_newer("1.27", "1.9"), "按数字比，不是按字符串")
        self.assertFalse(updater.is_newer("1.26", "1.27"))
        self.assertFalse(updater.is_newer("1.27", "1.27"))

    def test_unknown_current_version_offers_the_release(self):
        self.assertTrue(updater.is_newer("1.27", ""))
        self.assertTrue(updater.is_newer("build-27", "0.0.0"))

    def test_summary_mentions_both_versions(self):
        release = updater.ReleaseInfo(version="1.27")
        self.assertIn("发现新版本 1.27", updater.summarise(release, {"version": "1.0"}))
        self.assertIn("已是最新", updater.summarise(release, {"version": "1.99"}))

    def test_build_info_falls_back_when_not_packaged(self):
        with mock.patch.object(updater, "_build_info", None):
            self.assertEqual({"version": "", "built_at": "", "commit": ""}, updater.build_info())

    def test_build_info_is_read_from_the_generated_module(self):
        module = mock.Mock(VERSION="1.27", BUILT_AT="2026-10-20T08:14:32Z", COMMIT="abc1234")
        with mock.patch.object(updater, "_build_info", module):
            self.assertEqual("1.27", updater.build_info()["version"])
            self.assertEqual("abc1234", updater.build_info()["commit"])


class DownloadTest(TestCase):
    def test_download_writes_the_file_and_reports_progress(self):
        seen = []
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "update.apk"
            with mock.patch.object(updater, "http_get",
                                   return_value=_Response(content=b"x" * 5000,
                                                          headers={"Content-Length": "5000"})):
                updater.download("https://example.test/apk", target,
                                 progress=lambda done, total: seen.append((done, total)))
            self.assertEqual(5000, target.stat().st_size)
        self.assertEqual([(5000, 5000)], seen)

    def test_a_truncated_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "update.apk"
            with mock.patch.object(updater, "http_get",
                                   return_value=_Response(content=b"x" * 10,
                                                          headers={"Content-Length": "999"})):
                with self.assertRaises(updater.UpdaterError):
                    updater.download("https://example.test/apk", target)
            self.assertFalse(target.exists(), "半截文件不能留下")

    def test_error_status_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "update.apk"
            with mock.patch.object(updater, "http_get", return_value=_Response(status_code=403)):
                with self.assertRaises(updater.UpdaterError) as caught:
                    updater.download("https://example.test/apk", target)
        self.assertIn("403", str(caught.exception))


class InstallDesktopTest(TestCase):
    """exe 版更新：解压 → 只换程序本体 → 重启（配置文件绝不覆盖）。"""

    def _zip(self, folder: Path, files: dict) -> Path:
        path = folder / "Vocawiki-create-tool (1.27).zip"
        with zipfile.ZipFile(path, "w") as bundle:
            for name, data in files.items():
                bundle.writestr(name, data)
        return path

    def test_replaces_the_exe_and_keeps_the_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = root / "app"
            app.mkdir()
            exe = app / updater.DESKTOP_EXE
            exe.write_bytes(b"old")
            config = app / "config.yaml"
            config.write_text("keep me", encoding="utf-8")
            archive = self._zip(root, {
                "Vocawiki-create-tool/Vocawiki-create-tool.exe": b"new",
                "Vocawiki-create-tool/config.yaml": "template",
                "Vocawiki-create-tool/i18n/zh/LC_MESSAGES/messages.mo": "mo",
            })
            with mock.patch.object(updater, "exe_path", return_value=exe), \
                 mock.patch.object(updater, "_restart") as restart:
                ok, message = updater.install_desktop(archive)
            self.assertTrue(ok, message)
            self.assertEqual(b"new", exe.read_bytes())
            self.assertEqual("keep me", config.read_text(encoding="utf-8"),
                             "用户自己的 config.yaml 不能被更新包盖掉")
            self.assertTrue((app / "i18n/zh/LC_MESSAGES/messages.mo").exists())
            restart.assert_called_once_with(exe)
            self.assertFalse(archive.exists(), "下载的压缩包用完就删")

    def test_running_from_source_says_there_is_nothing_to_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "x.zip"
            archive.write_bytes(b"")
            with mock.patch.object(updater, "exe_path", return_value=None):
                ok, message = updater.install_desktop(archive)
        self.assertFalse(ok)
        self.assertIn("源码运行", message)

    def test_a_zip_without_exe_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            exe = root / updater.DESKTOP_EXE
            exe.write_bytes(b"old")
            archive = self._zip(root, {"readme.txt": "hi"})
            with mock.patch.object(updater, "exe_path", return_value=exe), \
                 mock.patch.object(updater, "_restart") as restart:
                ok, message = updater.install_desktop(archive)
            self.assertFalse(ok)
            self.assertIn("没有 exe", message)
            restart.assert_not_called()
            self.assertEqual(b"old", exe.read_bytes())

    def test_locked_exe_falls_back_to_the_swap_script(self):
        """exe 被占用（改名也失败）时，写一个批处理等它退出再替换。"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            exe = root / updater.DESKTOP_EXE
            exe.write_bytes(b"old")
            archive = self._zip(root, {
                "Vocawiki-create-tool/Vocawiki-create-tool.exe": b"new"})
            with mock.patch.object(updater, "exe_path", return_value=exe), \
                 mock.patch.object(updater, "_swap_now", return_value=False), \
                 mock.patch.object(updater.subprocess, "Popen") as popen:
                ok, message = updater.install_desktop(archive)
            self.assertTrue(ok, message)
            self.assertIn("重新打开", message)
            self.assertIn("cmd", popen.call_args.args[0][0])
            script = Path(popen.call_args.args[0][2])
            text = script.read_text(encoding="utf-8")
            self.assertIn("copy /y", text)
            self.assertIn(str(exe), text)

    def test_swap_script_content_restarts_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = updater._write_swap_script(root / "new.exe", root / "app.exe", root)
            text = script.read_text(encoding="utf-8")
        self.assertIn("start", text)
        self.assertIn("app.exe", text)
        # 新进程必须重新解压（见 `_relaunch_env()`），否则旧进程的 _MEI 目录删不掉、新进程也登不上
        self.assertIn("PYINSTALLER_RESET_ENVIRONMENT=1", text)

    def test_the_restarted_app_gets_a_fresh_pyinstaller_environment(self):
        """重启出来的新进程要带 `PYINSTALLER_RESET_ENVIRONMENT=1`（用户 2026-10-21 报的问题）。

        PyInstaller 6 会把 `_PYI_APPLICATION_HOME_DIR` / `_PYI_ARCHIVE_FILE` 传给子进程，
        子进程复用父进程的 `_MEI…` 目录：父进程退出时弹出
        「Failed to remove temporary directory: …\_MEI…」，而新进程继续从被删了一半的目录里
        读 certifi / DLL —— 登录 Vocawiki 的 HTTPS 请求失败，必须再重启一次才恢复。
        """
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / updater.DESKTOP_EXE
            with mock.patch.object(updater.subprocess, "Popen") as popen:
                updater._restart(exe)
            kwargs = popen.call_args.kwargs
        self.assertEqual("1", kwargs["env"][updater.RELAUNCH_ENV])
        self.assertEqual(str(exe.parent), kwargs["cwd"])


class AndroidInstallTest(TestCase):
    def test_missing_file_is_reported(self):
        ok, message = updater.install_android(Path("definitely-not-here.apk"))
        self.assertFalse(ok)
        self.assertIn("不见了", message)

    def test_on_desktop_it_explains_what_to_do(self):
        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "a.apk"
            apk.write_bytes(b"apk")
            with mock.patch.object(updater, "platform_kind", return_value="windows"):
                ok, message = updater.install_android(apk)
        self.assertFalse(ok)
        self.assertIn("解压覆盖 exe", message)


class PlatformKindTest(TestCase):
    def test_android_is_detected_from_the_environment(self):
        with mock.patch.dict(os.environ, {"ANDROID_ARGUMENT": "1"}):
            self.assertEqual("android", updater.platform_kind())

    def test_windows_is_detected_from_sys_platform(self):
        env = {key: value for key, value in os.environ.items() if key != "ANDROID_ARGUMENT"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(updater.sys, "platform", "win32"):
            self.assertEqual("windows", updater.platform_kind())


class BuildInfoWriterTest(TestCase):
    """`build.py` 写的 `_build_info.py` 要能被 `utils/updater.build_info()` 读回去。"""

    def test_round_trip(self):
        import build

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with mock.patch.object(build, "ROOT", root), \
                 mock.patch.dict(os.environ, {"GITHUB_SHA": "abc1234"}):
                target = build.write_build_info("1.27")
            text = target.read_text(encoding="utf-8")
            self.assertIn("VERSION = '1.27'", text)
            self.assertIn("COMMIT = 'abc1234'", text)
            module = mock.Mock(VERSION="1.27", BUILT_AT="", COMMIT="abc1234")
            self.assertTrue(text.strip().endswith("COMMIT = 'abc1234'"))
            self.assertEqual("1.27", module.VERSION)


class SettingsPanelUpdateBoxTest(TestCase):
    """桌面设置页的「程序更新」：检查 → 显示版本 → 下载安装（全部 mock）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt5 import QtWidgets
        cls.QtWidgets = QtWidgets
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        from config import config as config_module
        self.config_module = config_module
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        config = root / "config.yaml"
        config.write_text("--- !Config\nlang: \"zh\"\n", encoding="utf-8")
        (root / "wiki_credentials.yaml").write_text("username: \"\"\n", encoding="utf-8")
        self._original = (config_module.config_xxx, config_module.program_output_path,
                          config_module.get_config().lang)
        patcher = mock.patch.object(config_module, "application_path", root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)
        config_module.load_config(config)
        from utils.ui.settings_panel import SettingsPanel
        self.panel = SettingsPanel()

    def _restore(self):
        config_module = self.config_module
        config_module.config_xxx, config_module.program_output_path, lang = self._original
        from i18n.i18n import set_language
        set_language(lang or "zh")
        self.panel.deleteLater()

    def test_the_box_shows_the_current_version(self):
        self.assertIn("当前版本", self.panel.update_current_label.text())

    def test_check_update_enables_the_download_button(self):
        release = updater.ReleaseInfo(version="1.27", assets={
            "Vocawiki-create-tool (1.27).zip": "https://example.test/zip"})
        self.panel._on_update_checked(release)
        self.assertTrue(self.panel.update_install_button.isEnabled())
        self.assertIn("1.27", self.panel.update_status_label.text())

    def test_check_failure_is_shown(self):
        self.panel._on_update_checked({"ok": False, "error": "boom"})
        self.assertIn("boom", self.panel.update_status_label.text())
        self.assertFalse(self.panel.update_install_button.isEnabled())

    def test_a_release_without_zip_is_reported(self):
        self.panel._on_update_checked(updater.ReleaseInfo(version="1.27"))
        self.assertIn("没有 exe 包", self.panel.update_status_label.text())

    def test_successful_install_asks_the_window_to_close(self):
        with mock.patch.object(self.panel.window(), "close") as close:
            with mock.patch("PyQt5.QtCore.QTimer.singleShot") as single_shot:
                self.panel._on_update_downloaded({"ok": True, "message": "已更新，正在重新启动"})
                single_shot.assert_called_once()
                callback = single_shot.call_args.args[1]
                callback()
            close.assert_called_once()

    def test_a_failed_install_leaves_the_button_clickable(self):
        self.panel._on_update_downloaded({"ok": False, "message": "下载失败"})
        self.assertTrue(self.panel.update_install_button.isEnabled())
        self.assertIn("下载失败", self.panel.update_status_label.text())

    def test_a_running_flow_with_answered_questions_asks_first(self):
        """这一轮已经做过事了：更新会重启程序，先问一句；选「否」就什么都不动。"""
        QtWidgets = self.QtWidgets
        window = self.panel.window()
        with mock.patch.object(type(window), "_flow_running", True, create=True), \
             mock.patch.object(type(window), "_flow_answers", 3, create=True), \
             mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.No), \
             mock.patch("utils.ui.settings_panel.FunctionWorker") as worker:
            self.panel._update_release = updater.ReleaseInfo(
                version="1.27", assets={"Vocawiki-create-tool (1.27).zip": "https://x/zip"})
            self.panel._install_update()
        worker.return_value.start.assert_not_called()
        self.assertIn("已取消更新", self.panel.update_status_label.text())
        self.assertFalse(self.panel._stop_flow_on_restart)

    def test_confirming_the_question_installs_and_stops_the_flow_on_restart(self):
        QtWidgets = self.QtWidgets
        window = self.panel.window()
        with mock.patch.object(type(window), "_flow_running", True, create=True), \
             mock.patch.object(type(window), "_flow_answers", 2, create=True), \
             mock.patch.object(QtWidgets.QMessageBox, "question",
                               return_value=QtWidgets.QMessageBox.Yes), \
             mock.patch("utils.ui.settings_panel.FunctionWorker") as worker:
            self.panel._update_release = updater.ReleaseInfo(
                version="1.27", assets={"Vocawiki-create-tool (1.27).zip": "https://x/zip"})
            self.panel._install_update()
        worker.return_value.start.assert_called_once()
        self.assertTrue(self.panel._stop_flow_on_restart, "换好 exe 后要先把这一轮收掉")
        with mock.patch.object(type(window), "_abort_running_flow", create=True) as abort, \
             mock.patch.object(type(window), "close", create=True), \
             mock.patch("PyQt5.QtCore.QTimer.singleShot"):
            self.panel._on_update_downloaded({"ok": True, "message": "已更新"})
        abort.assert_called_once()
        self.assertFalse(self.panel._stop_flow_on_restart)

    def test_a_flow_that_has_not_done_anything_does_not_block_the_update(self):
        """刚打开 / 刚「清除对话记录」时流程虽然「在跑」，但其实挂在第一个问题上。

        用户 2026-10-21 报：这时点更新总说「生成流程还在跑」，重启程序也一样 ——
        因为窗口一建好就起了一轮流程。这种「什么都没做过」的一轮不该挡更新。
        """
        QtWidgets = self.QtWidgets
        window = self.panel.window()
        with mock.patch.object(type(window), "_flow_running", True, create=True), \
             mock.patch.object(type(window), "_flow_answers", 0, create=True), \
             mock.patch.object(QtWidgets.QMessageBox, "question") as question, \
             mock.patch("utils.ui.settings_panel.FunctionWorker") as worker:
            self.panel._update_release = updater.ReleaseInfo(
                version="1.27", assets={"Vocawiki-create-tool (1.27).zip": "https://x/zip"})
            self.panel._install_update()
        question.assert_not_called()
        worker.return_value.start.assert_called_once()
        self.assertNotIn("还在跑", self.panel.update_status_label.text())
        self.assertFalse(self.panel._stop_flow_on_restart)

    def test_an_idle_window_is_not_mistaken_for_a_running_flow(self):
        """没开生成流程时面板也是建好的 —— 以前按「有面板 + 没跑完」判断，在空闲时误报。"""
        window = self.panel.window()
        with mock.patch.object(type(window), "_flow_running", False, create=True), \
             mock.patch.object(type(window), "_finished", False, create=True), \
             mock.patch.object(type(window), "_panels", {"entry": object()}, create=True), \
             mock.patch("utils.ui.settings_panel.FunctionWorker") as worker:
            self.panel._update_release = updater.ReleaseInfo(
                version="1.27", assets={"Vocawiki-create-tool (1.27).zip": "https://x/zip"})
            self.panel._install_update()
        worker.return_value.start.assert_called_once()
        self.assertNotIn("还在跑", self.panel.update_status_label.text())
