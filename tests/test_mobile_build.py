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
