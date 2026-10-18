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
        self.assertIn("kivy==2.3.1", requirements)

    def test_version_is_templated(self):
        self.assertIn("version = __VERSION__", self._spec())

    def test_uses_the_real_p4a_repository(self):
        # Buildozer 默认的 kivy/python-for-android 才是存在的仓库；
        # python-for-android/python-for-android 会让 git clone 要求登录。
        self.assertIn("p4a.fork = kivy", self._spec())

    def test_numpy_is_not_packaged(self):
        # numpy 2.x 在 NDK 25b 的 libc++ 下编译失败；Android 端走纯 PIL 兜底。
        requirements = next(line for line in self._spec().splitlines()
                            if line.startswith("requirements ="))
        self.assertNotIn("numpy", requirements.lower())

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
        self.assertIn("Vocawiki-create-tool (", script)
        self.assertIn('cp -f "$APK" "$TARGET"', script)

    def test_build_bat_has_apk_entrypoint(self):
        batch = (ROOT / "build.bat").read_text(encoding="utf-8")
        self.assertIn(":apk", batch)
        self.assertIn("build_android.sh", batch)
        self.assertIn("--check apk", batch)

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
