"""「设置」页导入 config.yaml / wiki_credentials.yaml 的落地逻辑（不需要 Kivy）。"""
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from config import config as config_module
from mobile import settings_io

CONFIG_TEXT = """--- !Config
lang: "zh"
dark_mode: true
wikitext: !WikitextConfig
  # 注释也要原样保留
  ai_lyrics: false
wiki: !WikiConfig
  submit_window: true
"""

CREDENTIALS_TEXT = """username: "someone"
password: "secret"
ai_api_key: "sk-test"
"""


class AndroidStreamBytesTest(TestCase):
    """Android SAF 把文件读回来那段（`mobile/platform.py` 的 `read_stream_bytes()`）。

    2026-10-21 踩过：以前用 jnius 的 `byte[]` 中转 —— jnius 的字节元素是**有符号**的
    （-128…127），而带中文注释的 `config.yaml` 转成 UTF-8 后必然有 ≥ 0x80 的字节，
    `bytes(...)` 于是抛 `ValueError`，APK 里「导入 config.yaml」永远失败。现在改成
    逐字节 `read()`（0…255，EOF 是 -1），跟 Python 的 bytes 同口径。
    """

    class FakeStream:
        """跟 Java 的 `InputStream.read()` 一样：返回 0…255，读完返回 -1。"""

        def __init__(self, raw: bytes):
            self._raw = raw
            self._index = 0

        def read(self) -> int:
            if self._index >= len(self._raw):
                return -1
            value = self._raw[self._index]
            self._index += 1
            return value

    def test_bytes_above_0x7f_survive(self):
        from mobile import platform

        raw = CONFIG_TEXT.encode("utf-8")
        self.assertTrue(any(byte > 0x7F for byte in raw), "用例本身要有中文，不然测不到这个坑")
        self.assertEqual(raw, platform.read_stream_bytes(self.FakeStream(raw)))

    def test_an_empty_stream_reads_as_empty_bytes(self):
        from mobile import platform

        self.assertEqual(b"", platform.read_stream_bytes(self.FakeStream(b"")))

    def test_the_result_can_be_imported(self):
        from mobile import platform
        from mobile import settings_io

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / settings_io.CONFIG_NAME
            raw = platform.read_stream_bytes(self.FakeStream(CONFIG_TEXT.encode("utf-8")))
            ok, message = settings_io.import_settings_bytes(raw, target)
            self.assertTrue(ok, message)
            self.assertEqual(CONFIG_TEXT, target.read_text(encoding="utf-8"))

    def test_a_utf16_file_from_notepad_still_imports(self):
        """记事本「Unicode」存出来的配置是 UTF-16（带 BOM）：以前会解成乱码 → 「不是合法的 YAML」。

        导入时按识别出来的文本重写成 UTF-8（应用自己的口径），所以文件里读回来是 UTF-8。
        """
        from mobile import settings_io

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / settings_io.CONFIG_NAME
            ok, message = settings_io.import_settings_bytes(
                CONFIG_TEXT.encode("utf-16"), target)
            self.assertTrue(ok, message)
            self.assertEqual(CONFIG_TEXT, target.read_text(encoding="utf-8"))


class ImportSettingsTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_imports_a_config_file_verbatim(self):
        target = self.root / settings_io.CONFIG_NAME
        ok, message = settings_io.import_settings_text(CONFIG_TEXT, target)
        self.assertTrue(ok, message)
        self.assertIn("config.yaml", message)
        self.assertEqual(CONFIG_TEXT, target.read_text(encoding="utf-8"),
                         "内容要原样写进去：注释与 !Config 标签都别动")

    def test_imports_a_credentials_file(self):
        target = self.root / settings_io.CREDENTIALS_NAME
        ok, message = settings_io.import_settings_text(CREDENTIALS_TEXT, target)
        self.assertTrue(ok, message)
        self.assertIn("someone", target.read_text(encoding="utf-8"))

    def test_the_previous_file_is_backed_up(self):
        target = self.root / settings_io.CONFIG_NAME
        target.write_text("lang: en\n", encoding="utf-8")
        ok, message = settings_io.import_settings_text(CONFIG_TEXT, target)
        self.assertTrue(ok, message)
        backup = target.with_name(target.name + settings_io.BACKUP_SUFFIX)
        self.assertTrue(backup.is_file())
        self.assertEqual("lang: en\n", backup.read_text(encoding="utf-8"))
        self.assertIn("备份", message)

    def test_a_credentials_file_cannot_overwrite_config(self):
        target = self.root / settings_io.CONFIG_NAME
        target.write_text(CONFIG_TEXT, encoding="utf-8")
        ok, message = settings_io.import_settings_text(CREDENTIALS_TEXT, target)
        self.assertFalse(ok)
        self.assertIn("wiki_credentials.yaml", message)
        self.assertEqual(CONFIG_TEXT, target.read_text(encoding="utf-8"), "不能动现有设置")

    def test_a_config_file_cannot_overwrite_credentials(self):
        target = self.root / settings_io.CREDENTIALS_NAME
        ok, message = settings_io.import_settings_text(CONFIG_TEXT, target)
        self.assertFalse(ok)
        self.assertIn("config.yaml", message)
        self.assertFalse(target.exists())

    def test_broken_yaml_is_rejected(self):
        ok, message = settings_io.import_settings_text("a: [1,\n b: 2", self.root / "config.yaml")
        self.assertFalse(ok)
        self.assertIn("YAML", message)

    def test_empty_file_is_rejected(self):
        ok, message = settings_io.import_settings_text("   \n", self.root / "config.yaml")
        self.assertFalse(ok)
        self.assertIn("空", message)

    def test_unknown_target_is_rejected(self):
        ok, message = settings_io.import_settings_text(CONFIG_TEXT, self.root / "other.yaml")
        self.assertFalse(ok)
        self.assertIn("不支持", message)

    def test_bytes_import_handles_a_utf8_bom(self):
        target = self.root / settings_io.CONFIG_NAME
        raw = b"\xef\xbb\xbf" + CONFIG_TEXT.encode("utf-8")
        ok, message = settings_io.import_settings_bytes(raw, target)
        self.assertTrue(ok, message)
        self.assertTrue(target.read_text(encoding="utf-8").startswith("--- !Config"))

    def test_bytes_import_rejects_an_empty_pick(self):
        ok, message = settings_io.import_settings_bytes(None, self.root / "config.yaml")
        self.assertFalse(ok)
        self.assertIn("没有读到", message)


class MobileDefaultsTest(TestCase):
    """手机端第一次启动补的默认值：生成流程里要先走一屏「样式」页。"""

    SAMPLE = """--- !Config
lang: "zh"
dark_mode: false
color: !ColorConfig
  ai_css: true
  color_editor: false
"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "config.yaml"
        self.path.write_text(self.SAMPLE, encoding="utf-8")
        self._original = (config_module.config_xxx, config_module.program_output_path)
        patcher = mock.patch.object(config_module, "application_path", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)
        config_module.load_config(self.path)

    def _restore(self):
        config_module.config_xxx, config_module.program_output_path = self._original
        self._tmp.cleanup()

    def test_style_step_is_turned_on_once(self):
        self.assertFalse(config_module.get_config().color.color_editor)
        self.assertTrue(settings_io.apply_mobile_defaults(self.path))
        self.assertTrue(config_module.get_config().color.color_editor,
                        "手机上没这一屏就改不了颜色 / 换封面")
        self.assertTrue(config_module.get_config().mobile_defaults_applied)
        self.assertIn("color_editor: true", self.path.read_text(encoding="utf-8"))

    def test_a_later_opt_out_is_respected(self):
        settings_io.apply_mobile_defaults(self.path)
        config_module.save_config_values({"color.color_editor": False})
        config_module.load_config(self.path)
        self.assertFalse(settings_io.apply_mobile_defaults(self.path),
                         "已经套过一次就不再覆盖用户的选择")
        self.assertFalse(config_module.get_config().color.color_editor)
