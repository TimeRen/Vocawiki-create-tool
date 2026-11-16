"""用户手填的名字（见 utils/user_dict.py）。"""
import tempfile
import unittest
from pathlib import Path

from utils import user_dict


class UserDictTest(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        user_dict.set_path(Path(self._folder.name) / "user_names.yaml")

    def tearDown(self):
        user_dict.set_path(None)
        self._folder.cleanup()

    def test_remembers_and_looks_up(self):
        self.assertTrue(user_dict.remember_vocalist("和音マコ", "和音Mako"))
        self.assertTrue(user_dict.remember_title("ワールドイズマイン", "世界属于我"))
        self.assertEqual("和音Mako", user_dict.lookup_vocalist("和音マコ"))
        self.assertEqual("世界属于我", user_dict.lookup_title("ワールドイズマイン"))
        user_dict.set_path(user_dict.path())          # 重新读一遍文件
        self.assertEqual("和音Mako", user_dict.lookup_vocalist("和音マコ"))

    def test_identical_or_empty_pairs_are_not_stored(self):
        self.assertFalse(user_dict.remember_vocalist("重音Teto", "重音Teto"))
        self.assertFalse(user_dict.remember_vocalist("", "重音Teto"))
        self.assertFalse(user_dict.remember_title("白花", ""))
        self.assertEqual({}, user_dict.load()["titles"])

    def test_unknown_names_are_empty(self):
        self.assertEqual("", user_dict.lookup_vocalist("没记录过的"))
        self.assertEqual("", user_dict.lookup_vocalist(""))

    def test_file_is_human_editable(self):
        user_dict.remember_vocalist("和音マコ", "和音Mako")
        text = user_dict.path().read_text(encoding="utf-8")
        self.assertIn("vocalists:", text)
        self.assertIn("和音マコ: 和音Mako", text)
        self.assertTrue(text.startswith("#"), "文件开头要有一句说明")

    def test_broken_file_is_treated_as_empty(self):
        user_dict.path().parent.mkdir(parents=True, exist_ok=True)
        user_dict.path().write_text("不是: [合法的: yaml", encoding="utf-8")
        with self.assertLogs(level="WARNING"):
            self.assertEqual({}, user_dict.load()["vocalists"])

    def test_values_with_colons_are_quoted(self):
        user_dict.remember_title("曲名", "条目: 副标题")
        user_dict.set_path(user_dict.path())
        self.assertEqual("条目: 副标题", user_dict.lookup_title("曲名"))

    def test_clear_forgets_everything(self):
        user_dict.remember_vocalist("和音マコ", "和音Mako")
        user_dict.clear()
        self.assertEqual("", user_dict.lookup_vocalist("和音マコ"))
        self.assertFalse(user_dict.path().exists())


if __name__ == "__main__":
    unittest.main()
