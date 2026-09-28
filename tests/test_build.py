"""打包脚本的凭据清洗与发布包测试：分发包里绝不能带真实密码，也不再留 dist/。"""
import dataclasses
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

import yaml

import build
from config import config as _config_classes      # 先导入以注册 !Config 等 YAML 标签


class CredentialsTemplateTest(TestCase):
    def _write(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "wiki_credentials.yaml").write_text(text, encoding="utf-8")
            target = root / "out.yaml"
            with mock.patch.object(build, "ROOT", root):
                build.write_credentials_template(target)
            return target.read_text(encoding="utf-8")

    def test_secrets_are_blanked(self):
        out = self._write('username: "bot@example"\npassword: "s3cret"\n'
                          'ai_provider: "openai"\nai_api_key: "sk-abcdef"\n')
        self.assertIn('username: ""', out)
        self.assertIn('password: ""', out)
        self.assertIn('ai_api_key: ""', out)
        self.assertNotIn("bot@example", out)
        self.assertNotIn("s3cret", out)
        self.assertNotIn("sk-abcdef", out)
        # 非密钥字段保持原样
        self.assertIn('ai_provider: "openai"', out)

    def test_adds_ai_section_when_missing(self):
        out = self._write('username: "u"\npassword: "p"\n')
        self.assertIn("ai_api_key", out)
        self.assertIn('ai_base_url: "https://api.deepseek.com/v1"', out)
        self.assertIn('ai_model: "deepseek-flash"', out)
        self.assertIn("ai_thinking: false", out)

    def test_missing_source_file_still_outputs_template(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "out.yaml"
            with mock.patch.object(build, "ROOT", root):
                build.write_credentials_template(target)
            out = target.read_text(encoding="utf-8")
        self.assertIn('username: ""', out)
        self.assertIn('password: ""', out)
        self.assertIn("ai_api_key", out)


class VersionTest(TestCase):
    """发布包命名：Vocawiki-create-tool (版本号).zip；版本号可来自参数或终端询问。"""

    def test_version_from_argv(self):
        self.assertEqual(build.ask_version(["build.py", "1.2.3"]), "1.2.3")
        self.assertEqual(build.ask_version(["build.py", "  2.0  "]), "2.0")

    def test_ask_when_not_given(self):
        with mock.patch("builtins.input", return_value="3.1.4"):
            self.assertEqual(build.ask_version(["build.py"]), "3.1.4")

    def test_blank_input_falls_back(self):
        with mock.patch("builtins.input", return_value="   "):
            self.assertEqual(build.ask_version(["build.py"]), "0.0.0")

    def test_eof_falls_back(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertEqual(build.ask_version(["build.py"]), "0.0.0")

    def test_illegal_filename_chars_replaced(self):
        self.assertEqual(build.ask_version(["build.py", "1.0/beta:2"]), "1.0_beta_2")

    def test_zip_name(self):
        self.assertEqual(build.zip_name_for("1.0.0"), "Vocawiki-create-tool (1.0.0).zip")

    def test_switches_are_not_mistaken_for_a_version(self):
        # `python build.py --source 1.2.3`：开关不算版本号
        self.assertEqual(build.ask_version(["build.py", "--source", "1.2.3"]), "1.2.3")
        with mock.patch("builtins.input", return_value="9.9.9"):
            self.assertEqual(build.ask_version(["build.py", "--source"]), "9.9.9")


class PackagedConfigTest(TestCase):
    """打包模板 config_simple.yaml 的字段名必须与配置类一致。

    配置加载是「有什么字段就 setattr 什么」，写错名字会被**静默忽略**、取 dataclass 默认值，
    因此这里用回归测试把 old 坑堵住（曾出现 producer_template_and_cat / no_hover 这类无效项）。
    """

    @classmethod
    def setUpClass(cls):
        text = (build.ROOT / "config_simple.yaml").read_text(encoding="utf-8")
        cls.cfg = yaml.load(text, Loader=yaml.Loader)

    @staticmethod
    def _unknown(obj) -> list:
        fields = {f.name for f in dataclasses.fields(obj)}
        return sorted(k for k in obj.__dict__ if k not in fields)

    def test_top_level_fields_are_known(self):
        self.assertEqual([], self._unknown(self.cfg))

    def test_section_fields_are_known(self):
        for section in (self.cfg.wikitext, self.cfg.color, self.cfg.image, self.cfg.wiki):
            self.assertEqual([], self._unknown(section),
                             f"{type(section).__name__} 里有未知字段：{self._unknown(section)}")

    def test_ai_prompt_fields_exist(self):
        # 三栏默认提示词必须能在打包模板里配置
        for name in ("ai_prompt_songbox", "ai_prompt_intro", "ai_prompt_lyrics"):
            self.assertIn(name, self.cfg.color.__dict__)

    def test_ai_lyrics_switch_exists(self):
        # 「是否允许 AI 识别歌词」的开关也要能在打包模板里改（wikitext.ai_lyrics）
        self.assertIsInstance(self.cfg.wikitext.ai_lyrics, bool)

    def test_disambig_switch_exists(self):
        # 「同名条目（消歧义）处理」的开关也要能在打包模板里改（wiki.disambiguate）
        self.assertIsInstance(self.cfg.wiki.disambiguate, bool)

    def test_manual_lyrics_window_is_reachable(self):
        # 手动输入歌词窗口的唯一入口：utils/vocadb.py 只在
        # `not lyrics_chs_fail_fast` 时询问「是否手动输入」，选「是」才开窗。
        # 打包模板若写回 true 就永远弹不出窗口（曾多次出现该回归）。
        self.assertFalse(self.cfg.wikitext.lyrics_chs_fail_fast,
                         "config_simple.yaml 的 lyrics_chs_fail_fast 必须为 false，否则不会弹出歌词整理窗口")


class IconTest(TestCase):
    """exe 图标：assets/icon.png 自动转成多尺寸 assets/icon.ico，再交给 PyInstaller。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        assets = root / "assets"
        self.png = assets / "icon.png"
        self.ico = assets / "icon.ico"
        self.staging = root / "staging"       # 打包时 exe 的落地目录（不是项目里的 dist/）
        patches = (mock.patch.object(build, "ROOT", root),
                   mock.patch.object(build, "ICON_DIR", assets),
                   mock.patch.object(build, "ICON_ICO", self.ico),
                   mock.patch.object(build, "ICON_PNG", self.png))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _write_image(self, suffix=".png", size=(512, 512)):
        """在 assets/ 下写一张纯色测试图，返回它的路径。"""
        from PIL import Image
        target = self.png.with_suffix(suffix)
        self.png.parent.mkdir(parents=True, exist_ok=True)
        # JPEG 存不了透明通道，测试图就不带 alpha
        transparent = suffix.lower() == ".png"
        image = Image.new("RGBA" if transparent else "RGB", size,
                          (12, 200, 180, 255) if transparent else (12, 200, 180))
        image.save(target)
        return target

    def _write_broken_image(self, suffix=".png"):
        """写一个「后缀是图片、内容不是图片」的文件。"""
        target = self.png.with_suffix(suffix)
        self.png.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not an image")
        return target

    def test_no_icon_at_all(self):
        self.assertIsNone(build.make_icon())

    def test_png_is_converted_to_multi_size_ico(self):
        from PIL import Image
        self._write_image()
        self.assertEqual(self.ico, build.make_icon())
        with Image.open(self.ico) as image:
            sizes = image.info["sizes"]
        self.assertIn((16, 16), sizes)            # 任务栏
        self.assertIn((48, 48), sizes)            # 资源管理器中图标
        self.assertIn((256, 256), sizes)          # 大图标
        self.assertEqual(len(build.ICON_SIZES), len(sizes))

    def test_non_square_png_is_center_cropped(self):
        from PIL import Image
        self._write_image(size=(640, 320))
        self.assertEqual(self.ico, build.make_icon())
        with Image.open(self.ico) as image:
            # 裁成正方形后再缩小，横图不会被挤扁
            self.assertEqual((256, 256), image.size)

    def test_other_image_formats_work(self):
        self._write_image(suffix=".jpg", size=(320, 320))
        self.assertEqual(self.ico, build.make_icon())
        self.assertTrue(self.ico.is_file())

    def test_existing_ico_is_used_as_is(self):
        from PIL import Image
        self.ico.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(self.ico, format="ICO")
        before = self.ico.read_bytes()
        self.assertEqual(self.ico, build.make_icon())
        self.assertEqual(before, self.ico.read_bytes())

    def test_broken_image_reports_and_skips(self):
        self._write_broken_image()
        self.assertIsNone(build.make_icon())

    def test_command_without_icon(self):
        with mock.patch.object(build.sys, "platform", "win32"):
            command = build.pyinstaller_command(None, self.staging)
        self.assertNotIn("--icon", command)
        self.assertEqual(str(build.ROOT / "main.py"), command[-1])

    def test_command_with_icon_on_windows(self):
        with mock.patch.object(build.sys, "platform", "win32"):
            command = build.pyinstaller_command(self.ico, self.staging)
        self.assertIn("--icon", command)
        self.assertEqual(str(self.ico), command[command.index("--icon") + 1])
        self.assertEqual(str(build.ROOT / "main.py"), command[-1])

    def test_icon_is_also_added_as_bundle_data(self):
        """图标还要放进包内 assets/，窗口标题栏才能从 sys._MEIPASS 找到它。"""
        command = build.pyinstaller_command(self.ico, self.staging)
        self.assertIn("--add-data", command)
        value = command[command.index("--add-data") + 1]
        self.assertEqual(f"{self.ico}{os.pathsep}assets", value)

    def test_no_icon_means_no_bundle_data(self):
        self.assertNotIn("--add-data", build.pyinstaller_command(None, self.staging))

    def test_command_skips_icon_elsewhere(self):
        # PyInstaller 只在 Windows 上支持 --icon
        with mock.patch.object(build.sys, "platform", "linux"):
            self.assertNotIn("--icon", build.pyinstaller_command(self.ico, self.staging))

    def test_exe_goes_to_the_staging_directory_not_dist(self):
        """exe 输出到打包用的临时目录（用户 2026-09：项目里不再生成 dist/）。"""
        command = build.pyinstaller_command(None, self.staging)
        self.assertEqual(str(self.staging), command[command.index("--distpath") + 1])
        self.assertEqual(str(build.WORK), command[command.index("--workpath") + 1])


class PackTest(TestCase):
    """发布包：exe + 运行时资源打成 zip，项目里不再留 dist/ 目录。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patch = mock.patch.object(build, "ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def _staging(self):
        staging = self.root / "staging"
        staging.mkdir()
        (staging / f"{build.EXE_NAME}.exe").write_bytes(b"MZ fake exe")
        (staging / "config.yaml").write_text("lang: zh\n", encoding="utf-8")
        (staging / "i18n" / "zh" / "LC_MESSAGES").mkdir(parents=True)
        (staging / "i18n" / "zh" / "LC_MESSAGES" / "messages.mo").write_bytes(b"mo")
        return staging

    def test_zip_has_the_exe_and_resources_at_the_top_level(self):
        staging = self._staging()
        target = self.root / build.zip_name_for("1.0.0")
        zip_path = build.pack_zip(staging, target)
        self.assertEqual(target, zip_path)
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
        self.assertEqual([f"{build.EXE_NAME}.exe", "config.yaml",
                          "i18n/zh/LC_MESSAGES/messages.mo"], names)
        self.assertFalse(any(name.startswith("dist/") for name in names),
                         "zip 里不要再套一层 dist/（解压出来就是 exe 与资源）")

    def test_arc_prefix_wraps_the_files_in_a_folder(self):
        """源码包靠这个套一层 `Vocawiki-create-tool (版本号)/`（解压不会撒一地）。"""
        staging = self._staging()
        zip_path = build.pack_zip(staging, self.root / "src.zip", arc_prefix="源码包")
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
        self.assertEqual([f"源码包/{build.EXE_NAME}.exe", "源码包/config.yaml",
                          "源码包/i18n/zh/LC_MESSAGES/messages.mo"], names)

    def test_existing_zip_is_replaced(self):
        staging = self._staging()
        target = self.root / build.zip_name_for("1.0.0")
        first = build.pack_zip(staging, target)
        with zipfile.ZipFile(first, "a") as archive:
            archive.writestr("old.txt", "x")
        second = build.pack_zip(staging, target)
        self.assertEqual(first, second)
        with zipfile.ZipFile(second) as archive:
            self.assertNotIn("old.txt", archive.namelist())

    def test_source_package_has_a_top_level_folder_and_no_dist(self):
        """`python build.py --source`：不跑 PyInstaller，顶层是 `Vocawiki-create-tool (版本号)/`。"""
        (self.root / "main.py").write_text("# fake\n", encoding="utf-8")
        (self.root / "utils").mkdir()
        (self.root / "utils" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "utils" / "__pycache__").mkdir()
        (self.root / "utils" / "__pycache__" / "x.pyc").write_bytes(b"x")
        (self.root / "config_simple.yaml").write_text("lang: zh\n", encoding="utf-8")
        (self.root / "i18n" / "zh" / "LC_MESSAGES").mkdir(parents=True)
        (self.root / "i18n" / "zh" / "LC_MESSAGES" / "messages.po").write_text(
            'msgid "a"\nmsgstr "b"\n', encoding="utf-8")
        staging = self.root / "staging"

        def fake_run(cmd):
            print(">", " ".join(str(part) for part in cmd))

        with mock.patch.object(build, "run", side_effect=fake_run) as run, \
             mock.patch.object(build, "staging_directory", return_value=staging):
            zip_path = build.pack_source_zip("1.2.3")

        self.assertEqual(self.root / build.zip_name_for("1.2.3"), zip_path)
        self.assertIn("compile_mo.py", " ".join(str(part) for part in run.call_args.args[0]))
        with zipfile.ZipFile(zip_path) as archive:
            names = sorted(archive.namelist())
        self.assertEqual(["Vocawiki-create-tool (1.2.3)/config.yaml",
                          "Vocawiki-create-tool (1.2.3)/i18n/zh/LC_MESSAGES/messages.po",
                          "Vocawiki-create-tool (1.2.3)/main.py",
                          "Vocawiki-create-tool (1.2.3)/utils/__init__.py",
                          "Vocawiki-create-tool (1.2.3)/wiki_credentials.yaml"], names)
        self.assertFalse(any("__pycache__" in name for name in names))
        self.assertFalse((self.root / "dist").exists())
        self.assertFalse(staging.exists(), "临时目录不留在项目里")

    def test_source_package_blanks_the_credentials(self):
        """源码包也会被传出去，凭据文件必须和发布包一样清空（旧 make_source.sh 拷的是真文件）。"""
        (self.root / "wiki_credentials.yaml").write_text(
            'username: "bot"\npassword: "s3cret"\n', encoding="utf-8")
        (self.root / "config_simple.yaml").write_text("lang: zh\n", encoding="utf-8")
        with mock.patch.object(build, "run"), \
             mock.patch.object(build, "staging_directory", return_value=self.root / "staging"):
            zip_path = build.pack_source_zip("1.0.0")
        with zipfile.ZipFile(zip_path) as archive:
            credentials = archive.read(
                "Vocawiki-create-tool (1.0.0)/wiki_credentials.yaml").decode("utf-8")
        self.assertNotIn("s3cret", credentials)
        self.assertIn('password: ""', credentials)
        self.assertIn("ai_api_key", credentials)

    def test_source_mode_never_calls_pyinstaller(self):
        (self.root / "config_simple.yaml").write_text("lang: zh\n", encoding="utf-8")
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        with mock.patch.object(build, "run"), \
             mock.patch.object(build, "pyinstaller_command") as command, \
             mock.patch.object(build, "ask_version", return_value="1.0.0"), \
             mock.patch.object(build, "staging_directory", return_value=self.root / "s"), \
             mock.patch.object(build.sys, "argv", ["build.py", "--source", "1.0.0"]):
            build.main()
        command.assert_not_called()
        self.assertTrue((self.root / build.zip_name_for("1.0.0")).is_file())

    def test_staging_directory_is_outside_the_project(self):
        staging = build.staging_directory()
        self.addCleanup(shutil.rmtree, staging, True)
        self.assertTrue(staging.is_dir())
        self.assertNotEqual(self.root.resolve(), staging.resolve())

    def test_main_leaves_only_the_zip(self):
        """跑一遍 build.main(): 项目里只有 zip，没有 dist/，build/ 与临时目录也清掉了。"""
        (self.root / "config_simple.yaml").write_text("lang: zh\n", encoding="utf-8")
        (self.root / "wiki_credentials.yaml").write_text('password: "s3cret"\n',
                                                        encoding="utf-8")
        (self.root / "i18n" / "zh" / "LC_MESSAGES").mkdir(parents=True)
        (self.root / "i18n" / "zh" / "LC_MESSAGES" / "messages.po").write_text(
            'msgid "a"\nmsgstr "b"\n', encoding="utf-8")
        (self.root / "main.py").write_text("# fake\n", encoding="utf-8")
        work = self.root / "build"                # 上次留下的 PyInstaller 工作目录
        work.mkdir()
        (work / "leftover.toc").write_text("x", encoding="utf-8")
        legacy = self.root / "dist"               # 旧脚本留下的 dist/
        legacy.mkdir()
        (legacy / "old.exe").write_bytes(b"old")
        staging = self.root / "staging"
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)            # main() 会 chdir 到 ROOT

        def fake_run(cmd):
            print(">", " ".join(str(part) for part in cmd))
            if "PyInstaller" in " ".join(str(part) for part in cmd):
                dist_path = Path(cmd[cmd.index("--distpath") + 1])
                dist_path.mkdir(parents=True, exist_ok=True)   # PyInstaller 自己会建这个目录
                dist_path.joinpath(f"{build.EXE_NAME}.exe").write_bytes(b"MZ fake exe")

        with mock.patch.object(build, "run", side_effect=fake_run), \
             mock.patch.object(build, "make_icon", return_value=None), \
             mock.patch.object(build, "ask_version", return_value="0.0.0"), \
             mock.patch.object(build, "WORK", work), \
             mock.patch.object(build, "LEGACY_DIST", legacy), \
             mock.patch.object(build, "staging_directory", return_value=staging):
            build.main()

        self.assertFalse(legacy.exists(), "旧 dist/ 要清掉，且不能重新生成")
        self.assertFalse(work.exists(), "成功时清掉中间产物")
        self.assertFalse(staging.exists(), "打包用的临时目录不留在项目里")
        zip_path = self.root / "Vocawiki-create-tool (0.0.0).zip"
        self.assertTrue(zip_path.is_file(), "项目里只留 zip")
        with zipfile.ZipFile(zip_path) as archive:
            self.assertIn(f"{build.EXE_NAME}.exe", archive.namelist())
            self.assertIn("i18n/zh/LC_MESSAGES/messages.po", archive.namelist())
            credentials = archive.read("wiki_credentials.yaml").decode("utf-8")
        self.assertNotIn("s3cret", credentials, "凭据要清空后才进包")
