"""config/config.py 里的「写回配置」：save_config_values / save_credentials。"""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

from config import config as config_module

# 和 config_simple.yaml 同一套结构（有注释、有节），用来验证「只改值、保住注释」
SAMPLE = """\
--- !Config
save_to_file: ""
# 程序语言
lang: "zh"
# 输出目录
output_dir: "output"
proxies: ""
wikitext: !WikitextConfig
  # 检测歌词里的括号
  furigana_local: true
  furigana_all: false
  producer_template: false
  uploader_note: false
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
  create_redirect: false
  disambiguate: true
"""


class ConfigSaveTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root.joinpath("config.yaml")
        self.path.write_text(SAMPLE, encoding="utf-8")
        # 这几个用例会真的 load_config，测完要把全局状态放回去
        self._original = (config_module.config_xxx, config_module.program_output_path,
                          getattr(config_module.get_config(), "lang", "zh"))
        patcher = mock.patch.object(config_module, "application_path", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        config_module.config_xxx, config_module.program_output_path, lang = self._original
        config_module.set_language(lang or "zh")
        self._tmp.cleanup()

    def saved_text(self):
        return self.path.read_text(encoding="utf-8")

    def test_replaces_values_in_place(self):
        self.assertTrue(config_module.save_config_values({
            "lang": "en",
            "wikitext.producer_template": True,
            "color.ai_css": False,
            "wiki.api_url": "https://example.org/api.php",
        }))
        text = self.saved_text()
        self.assertIn('lang: "en"', text)
        self.assertIn("producer_template: true", text)
        self.assertIn("ai_css: false", text)
        self.assertIn('api_url: "https://example.org/api.php"', text)

    def test_comments_and_structure_survive(self):
        config_module.save_config_values({"wikitext.furigana_local": False})
        text = self.saved_text()
        self.assertIn("--- !Config", text)
        self.assertIn("# 检测歌词里的括号", text)
        self.assertIn("wikitext: !WikitextConfig", text)
        self.assertIn("color: !ColorConfig", text)
        self.assertEqual(len(SAMPLE.splitlines()), len(text.splitlines()))

    def test_same_key_name_in_other_section_is_untouched(self):
        path = self.root.joinpath("config.yaml")
        path.write_text("--- !Config\ncrop: true\nimage: !ImageConfig\n  crop: true\n",
                        encoding="utf-8")
        config_module.save_config_values({"image.crop": False})
        text = path.read_text(encoding="utf-8")
        self.assertIn("crop: true\nimage:", text)          # 顶格那个没被动
        self.assertIn("  crop: false", text)               # 节里那个改了

    def test_missing_key_is_added_to_its_section(self):
        config_module.save_config_values({"wikitext.optimize_Introduction_color": True,
                                          "human_original": False})
        text = self.saved_text()
        lines = text.splitlines()
        index = lines.index("  optimize_Introduction_color: true")
        # 就插在 wikitext 节里（在下一节之前）
        self.assertLess(index, lines.index("color: !ColorConfig"))
        self.assertIn("human_original: false", lines)

    def test_empty_string_and_bool_forms(self):
        config_module.save_config_values({"proxies": "", "color.ai_prompt_songbox": "画个渐变色",
                                          "image.download_cover": False})
        text = self.saved_text()
        self.assertIn('proxies: ""', text)
        self.assertIn('ai_prompt_songbox: "画个渐变色"', text)
        self.assertIn("download_cover: false", text)

    def test_multiline_prompt_round_trips(self):
        prompt = "第一行\n第二行"
        config_module.save_config_values({"color.ai_prompt_lyrics": prompt})
        loaded = config_module.yaml.load(self.saved_text(), Loader=config_module.Loader)
        self.assertEqual(prompt, loaded.color.ai_prompt_lyrics)

    def test_reload_makes_it_effective(self):
        config_module.save_config_values({"wikitext.collapse_navbox": False, "lang": "en"})
        config_module.load_config(self.path)
        self.assertFalse(config_module.get_config().wikitext.collapse_navbox)
        self.assertEqual("en", config_module.get_config().lang)

    def test_missing_file_is_created(self):
        self.path.unlink()
        self.assertTrue(config_module.save_config_values({"lang": "zh"}))
        self.assertIn('lang: "zh"', self.saved_text())

    def test_reports_failure_when_not_writable(self):
        directory = self.root.joinpath("config.yaml")
        directory.unlink()                     # 占位成一个目录，写入必然失败
        directory.mkdir()
        with mock.patch.object(config_module, "config_path", return_value=directory):
            self.assertFalse(config_module.save_config_values({"lang": "en"}))


class CredentialsSaveTest(TestCase):
    SAMPLE_CREDS = ('# Vocawiki 登录凭据\n'
                    'username: ""\n'
                    'password: ""\n'
                    '\n'
                    '# AI\n'
                    'ai_provider: "openai"\n'
                    'ai_base_url: "https://api.deepseek.com/v1"\n'
                    'ai_model: "deepseek-flash"\n'
                    'ai_api_key: ""\n'
                    'ai_thinking: false\n')

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.creds = self.root.joinpath("wiki_credentials.yaml")
        self.creds.write_text(self.SAMPLE_CREDS, encoding="utf-8")
        patcher = mock.patch.object(config_module, "application_path", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_batch_write_strings_and_bools(self):
        self.assertTrue(config_module.save_credentials({
            "username": "user@bot", "password": "secret",
            "ai_api_key": "sk-abc", "ai_thinking": True, "ai_model": "claude",
        }))
        text = self.creds.read_text(encoding="utf-8")
        self.assertIn('username: "user@bot"', text)
        self.assertIn('ai_api_key: "sk-abc"', text)
        self.assertIn("ai_thinking: true", text)          # 布尔写成裸值
        self.assertIn("# Vocawiki 登录凭据", text)          # 注释还在
        self.assertEqual(1, text.count("ai_api_key:"))

    def test_reads_back(self):
        config_module.save_credentials({"username": "user@bot", "password": "secret",
                                        "ai_provider": "anthropic", "ai_base_url": "https://x",
                                        "ai_model": "m", "ai_api_key": "sk-abc",
                                        "ai_thinking": True})
        self.assertEqual(("user@bot", "secret"), config_module.get_wiki_credentials())
        ai = config_module.get_ai_credentials()
        self.assertEqual("anthropic", ai["provider"])
        self.assertEqual("sk-abc", ai["api_key"])
        self.assertTrue(ai["thinking"])

    def test_unknown_key_is_appended(self):
        config_module.save_credentials({"ai_extra": "x"})
        self.assertIn('ai_extra: "x"', self.creds.read_text(encoding="utf-8"))
