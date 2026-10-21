"""Android 构建脚本的静态检查（不需要 WSL / Buildozer）。"""
import configparser
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parent.parent


class AndroidBuildConfigTest(TestCase):
    def _spec(self) -> str:
        return (ROOT / "mobile" / "buildozer.spec.in").read_text(encoding="utf-8")

    def test_arm64_api26_and_private_storage(self):
        spec = self._spec()
        self.assertIn("android.archs = arm64-v8a", spec)
        self.assertIn("android.minapi = 26", spec)
        self.assertIn("android.private_storage = True", spec)
        self.assertIn("INTERNET", spec)

    def test_android_bundle_does_not_include_desktop_only_runtime(self):
        requirements = [part.strip() for part in
                        next(line for line in self._spec().splitlines()
                             if line.startswith("requirements =")).split("=", 1)[1].split(",")]
        self.assertNotIn("PyQt5", requirements)
        self.assertNotIn("PyQtWebEngine", requirements)
        self.assertNotIn("onnxruntime", requirements)
        # p4a v2024.01.21 的 kivy recipe 就是 2.3.0；写 2.3.1 会被要求另外的 recipe。
        self.assertIn("kivy==2.3.0", requirements)

    def test_version_is_templated(self):
        self.assertIn("version = __VERSION__", self._spec())

    def test_uses_the_real_p4a_repository(self):
        # Buildozer 默认的 kivy/python-for-android 才是存在的仓库；
        # python-for-android/python-for-android 会让 git clone 要求登录。
        self.assertIn("p4a.fork = kivy", self._spec())

    def test_p4a_is_pinned_to_a_released_version(self):
        # master 的 CPython 3.14 跑 Kivy 2.3.x 会启动即闪退；
        # v2024.01.21 是 CPython 3.11.5 + NDK 25b，属于已验证的组合。
        self.assertIn("p4a.branch = v2024.01.21", self._spec())
        self.assertNotIn("p4a.branch = master", self._spec())

    def test_app_starts_in_portrait(self):
        self.assertIn("orientation = portrait", self._spec())

    def test_android_entrypoint_hands_over_before_desktop_imports(self):
        """Android 启动时不能先导入桌面那一整套：任一导入失败就是「打开即闪退」。"""
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        dispatch = source.index('if __name__ == "__main__" and "ANDROID_ARGUMENT"')
        desktop = source.index("from config import data")
        self.assertLess(dispatch, desktop, "Android 分支要排在桌面 import 之前")
        self.assertIn("from mobile.main import run", source)

    def test_mobile_entrypoint_logs_startup_failures(self):
        source = (ROOT / "mobile" / "main.py").read_text(encoding="utf-8")
        self.assertIn("crash.log", source)
        self.assertIn("traceback.format_exc()", source)

    def test_numpy_is_not_packaged(self):
        # numpy 2.x 在 NDK 25b 的 libc++ 下编译失败；Android 端走纯 PIL 兜底。
        requirements = next(line for line in self._spec().splitlines()
                            if line.startswith("requirements ="))
        self.assertNotIn("numpy", requirements.lower())

    def test_transitive_pure_python_deps_are_listed_explicitly(self):
        """p4a 装纯 Python 包带 `--no-deps`，间接依赖不写就会在 APK 里 ImportError。

        beautifulsoup4 ≥ 4.13 会 `from typing_extensions import ...`，漏了它就是
        「点生成歌曲条目 → No module named 'typing_extensions'」。
        """
        requirements = next(line for line in self._spec().splitlines()
                            if line.startswith("requirements =")).split("=", 1)[1]
        names = [part.strip().split("==")[0].lower() for part in requirements.split(",")]
        self.assertIn("beautifulsoup4", names)
        self.assertIn("soupsieve", names)              # bs4 的解析器后端
        self.assertIn("typing_extensions", names)      # bs4 ≥ 4.13 的类型注解依赖

    def test_image_import_permissions_are_declared(self):
        spec = self._spec()
        self.assertIn("READ_MEDIA_IMAGES", spec)
        self.assertIn("READ_EXTERNAL_STORAGE", spec)

    def test_credentials_are_not_packaged(self):
        spec = self._spec()
        self.assertIn("wiki_credentials.yaml", spec)
        self.assertIn("config.yaml", spec)
        self.assertIn("source.exclude_patterns", spec)

    def test_spec_is_valid_ini(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(self._spec().replace("__VERSION__", "1.2.3"))
        self.assertEqual("1.2.3", parser["app"]["version"])
        self.assertEqual("arm64-v8a", parser["app"]["android.archs"])

    def test_build_android_script_calls_buildozer_and_copies_apk(self):
        script = (ROOT / "build_android.sh").read_text(encoding="utf-8")
        self.assertIn("buildozer -v android debug", script)
        self.assertIn("buildozer -v android release", script)
        self.assertIn("compile_mo.py", script)
        self.assertIn("Vocawiki-create-tool (", script)
        self.assertIn('cp -f "$APK" "$TARGET"', script)

    def test_every_build_is_signed_with_the_same_key(self):
        """APK 必须次次用同一把钥匙签，否则手机上的旧版装不上（用户 2026-10-21 报）。

        Gradle 打 debug 包时读 `~/.android/debug.keystore`，文件不在就**随机生成**一把 ——
        CI 是干净机器，于是每次发版都是新签名（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`）。
        仓库里带一份固定钥匙，`build_android.sh` 构建前把它装到那个位置，CI 再核对指纹。
        """
        keystore = ROOT / "mobile" / "signing" / "vocawiki-debug.keystore"
        self.assertTrue(keystore.is_file(), "仓库里要带一份固定的 debug 签名钥匙")
        self.assertGreater(keystore.stat().st_size, 1000)
        script = (ROOT / "build_android.sh").read_text(encoding="utf-8")
        self.assertIn("vocawiki-debug.keystore", script)
        self.assertIn("$HOME/.android/debug.keystore", script)
        workflow = (ROOT / ".github" / "workflows" / "android-apk.yml").read_text(encoding="utf-8")
        self.assertIn("467a0eb6e9cb10fc5eae4c56bb66cb618331726c", workflow)
        self.assertIn("--print-certs", workflow)
        # 钥匙是二进制，别让 git 动换行
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.keystore binary", attributes)
        # 也别打进 APK 里
        spec = self._spec()
        self.assertIn("*.keystore", spec)

    def test_apk_bytes_never_pass_through_python(self):
        """把文件写给 Java 时不能用 Python 的 `bytes`（有符号 / 无符号对不上）。

        2026-10-21 踩过两次：`config.yaml` 导入失败、APK 装不上，都是同一类问题 ——
        Python 的 `bytes` 元素是 0…255，Java 的 `byte[]` 是 -128…127。
        """
        platform_module = (ROOT / "mobile" / "platform.py").read_text(encoding="utf-8")
        self.assertNotIn("stream.write(path.read_bytes())", platform_module)
        self.assertIn("def copy_file_into_stream", platform_module)
        self.assertIn("java.nio.file.Files", platform_module)

    def test_the_installer_goes_straight_to_the_system_dialog(self):
        """点一次更新就该弹出系统安装界面，而不是把人丢到浏览器。"""
        platform_module = (ROOT / "mobile" / "platform.py").read_text(encoding="utf-8")
        self.assertIn("def publish_apk", platform_module)
        self.assertIn("ACTION_MANAGE_UNKNOWN_APP_SOURCES", platform_module)
        self.assertIn("canRequestPackageInstalls", platform_module)
        app = (ROOT / "mobile" / "app.py").read_text(encoding="utf-8")
        self.assertIn("def _run_update", app)
        self.assertIn("platform.install_apk(target)", app)
        self.assertIn("progress=progress", app)
        # 装不上时不能再自动弹浏览器（用户 2026-10-21 报）
        self.assertNotIn("platform.open_url(release.page_url", app)
        # 一次点击走完全程：不再需要「再点一次按钮下载」
        self.assertNotIn("再点一次按钮", app)
        self.assertNotIn("def _check_update", app)

    def test_build_bat_has_apk_entrypoint(self):
        batch = (ROOT / "build.bat").read_text(encoding="utf-8")
        self.assertIn(":apk", batch)
        self.assertIn("build_android.sh", batch)
        self.assertIn("--check apk", batch)

    def test_build_bat_asks_before_building_the_apk(self):
        """打 exe 时 APK 是可选的：`--with-apk` / `--no-apk`，双击时才问一句。"""
        batch = (ROOT / "build.bat").read_text(encoding="utf-8")
        self.assertIn("--with-apk", batch)
        self.assertIn("--no-apk", batch)
        self.assertIn("call :maybe_apk", batch)
        # 没有交互终端时不能停下来问（CI / <nul 会一直等输入）
        self.assertIn("timeout /t 1 /nobreak", batch)

    def test_back_navigation_reverses_the_slide_direction(self):
        """进页面从右往左滑，退回旧页面要反过来（从左往右），否则「返回」看着像又前进了一次。"""
        source = (ROOT / "mobile" / "app.py").read_text(encoding="utf-8")
        push = source[source.index("def push_screen"):source.index("def pop_screen")]
        pop = source[source.index("def pop_screen"):source.index("def go_home")]
        self.assertIn('self._slide("left")', push)
        self.assertIn('self._slide("right")', pop)
        # 先把 current 切回上一页让动画有对手戏，再延迟摘掉旧页面（立刻 remove 就是硬切）
        self.assertLess(pop.index("self.sm.current ="), pop.index("self._drop_after_transition"))
        self.assertIn("Clock.schedule_once", source[source.index("def _drop_after_transition"):])

    def test_mobile_has_a_light_and_a_dark_theme(self):
        """设置页的「深色模式」开关：两套配色都在 common.py 里，配置项是 Config.dark_mode。"""
        common = (ROOT / "mobile" / "screens" / "common.py").read_text(encoding="utf-8")
        self.assertIn('"light"', common)
        self.assertIn('"dark"', common)
        self.assertIn("def apply_theme", common)
        config = (ROOT / "config" / "config.py").read_text(encoding="utf-8")
        self.assertIn("dark_mode", config)
        app = (ROOT / "mobile" / "app.py").read_text(encoding="utf-8")
        self.assertIn("apply_theme_and_reopen_settings", app)
        self.assertIn("dark_mode", app)
        simple = (ROOT / "config_simple.yaml").read_text(encoding="utf-8")
        self.assertIn("dark_mode", simple)

    def test_multiline_inputs_grow_with_their_text(self):
        """歌词框以前固定高度、框里又拖不动（外层 ScrollView 抢拖动）：改成跟着内容长高。"""
        common = (ROOT / "mobile" / "screens" / "common.py").read_text(encoding="utf-8")
        flat = " ".join(common.split())
        self.assertIn("widget.bind(minimum_height=", flat)

    def test_preview_goes_through_the_loopback_server(self):
        """Android 不支持把 file:// 交给浏览器，预览改走 127.0.0.1。"""
        editors = (ROOT / "mobile" / "screens" / "editors.py").read_text(encoding="utf-8")
        self.assertIn("preview.serve", editors)
        self.assertIn("preview.build_document", editors)
        module = (ROOT / "mobile" / "preview.py").read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", module)
        self.assertIn("ThreadingHTTPServer", module)

    def test_settings_can_import_the_two_yaml_files(self):
        """设置页可以导入 config.yaml / wiki_credentials.yaml（Android 走 SAF，不需要存储权限）。"""
        app = (ROOT / "mobile" / "app.py").read_text(encoding="utf-8")
        self.assertIn("settings_io.CONFIG_NAME", app)
        self.assertIn("settings_io.CREDENTIALS_NAME", app)
        self.assertIn("platform.pick_document", app)
        self.assertIn("reload_settings", app)
        io_module = (ROOT / "mobile" / "settings_io.py").read_text(encoding="utf-8")
        self.assertIn("import_settings_bytes", io_module)
        self.assertIn("BACKUP_SUFFIX", io_module)
        platform_module = (ROOT / "mobile" / "platform.py").read_text(encoding="utf-8")
        self.assertIn("ACTION_OPEN_DOCUMENT", platform_module)
        self.assertIn("def pick_document", platform_module)

    def test_song_flow_includes_the_style_step(self):
        """手机上「生成歌曲条目」要先走一屏样式页（桌面是常驻的「样式」标签页）。"""
        io_module = (ROOT / "mobile" / "settings_io.py").read_text(encoding="utf-8")
        self.assertIn("MOBILE_DEFAULT_VALUES", io_module)
        self.assertIn('"color.color_editor"', io_module)
        self.assertIn("apply_mobile_defaults", io_module)
        app = (ROOT / "mobile" / "app.py").read_text(encoding="utf-8")
        self.assertIn("apply_mobile_defaults", app)
        self.assertIn("_toggle_style_step", app)
        config = (ROOT / "config" / "config.py").read_text(encoding="utf-8")
        self.assertIn("mobile_defaults_applied", config)
        simple = (ROOT / "config_simple.yaml").read_text(encoding="utf-8")
        self.assertIn("color_editor: true", simple)

    def test_mobile_lyrics_page_uses_the_prefilled_translation(self):
        """流程查到 atwiki 翻译时，手机端歌词页也要把译文 / 翻译者 / 链接填好。"""
        editors = (ROOT / "mobile" / "screens" / "editors.py").read_text(encoding="utf-8")
        self.assertIn('ctx.get("chs")', editors)
        self.assertIn('ctx.get("prefillMessage")', editors)
        self.assertIn('ctx.get("translator")', editors)

    def test_shell_scripts_use_lf_for_wsl(self):
        attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", attributes)
        self.assertNotIn(b"\r\n", (ROOT / "build_android.sh").read_bytes())
        self.assertNotIn(b"\r\n", (ROOT / "mobile" / "buildozer.spec.in").read_bytes())

    def test_github_action_builds_and_uploads_apk(self):
        workflow = (ROOT / ".github" / "workflows" / "android-apk.yml").read_text(
            encoding="utf-8")
        self.assertIn("./build_android.sh", workflow)
        self.assertIn("actions/upload-artifact", workflow)
        self.assertIn("ubuntu-22.04", workflow)
