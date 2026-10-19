"""「设置」页导入 config.yaml / wiki_credentials.yaml 的落地逻辑（不需要 Kivy）。"""
import tempfile
from pathlib import Path
from unittest import TestCase

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
