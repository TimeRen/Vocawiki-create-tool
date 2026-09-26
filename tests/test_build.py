"""打包脚本的凭据清洗测试：分发包里绝不能带真实密码或 AI 密钥。"""
import dataclasses
import tempfile
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
            command = build.pyinstaller_command(None)
        self.assertNotIn("--icon", command)
        self.assertEqual(str(build.ROOT / "main.py"), command[-1])

    def test_command_with_icon_on_windows(self):
        with mock.patch.object(build.sys, "platform", "win32"):
            command = build.pyinstaller_command(self.ico)
        self.assertIn("--icon", command)
        self.assertEqual(str(self.ico), command[command.index("--icon") + 1])
        self.assertEqual(str(build.ROOT / "main.py"), command[-1])

    def test_command_skips_icon_elsewhere(self):
        # PyInstaller 只在 Windows 上支持 --icon
        with mock.patch.object(build.sys, "platform", "linux"):
            self.assertNotIn("--icon", build.pyinstaller_command(self.ico))
