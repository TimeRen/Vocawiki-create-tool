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

    def test_missing_key_falls_back_to_dataclass_default(self):
        """文件里没写这项时，应该用配置类的默认值（而不是 None）。"""
        config_module.load_config(self.path)
        self.assertTrue(config_module.get_config().confirm_clear_history)
        self.assertTrue(config_module.get_config().font_scale_with_window)

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

    def test_top_level_key_is_not_appended_again(self):
        """顶格项要**就地换值**：以前每存一次就在文件末尾追加一份，配置越存越乱。

        2026-09 用户在打包版看到的 `config.yaml` 就是被这样撑起来的（lang / proxies /
        font_file… 各出现两次以上），而 YAML 取**最后**一个：后来追加的那份如果是空的，
        就把前面的设置顶掉了——「字体变回默认」正是这么来的。
        """
        for lang in ("en", "zh", "en"):
            config_module.save_config_values({"lang": lang, "proxies": ""})
        lines = self.saved_text().splitlines()
        self.assertEqual(1, sum(1 for line in lines if line.startswith("lang:")), lines)
        self.assertEqual(1, sum(1 for line in lines if line.startswith("proxies:")), lines)
        self.assertIn('lang: "en"', lines)
        self.assertEqual(len(SAMPLE.splitlines()), len(lines), "改值不该增删行")

    def test_already_duplicated_keys_are_all_overwritten(self):
        """已经被写坏的配置（同一个顶格键出现多次）在下次保存时会被统一成新值。"""
        path = self.root.joinpath("config.yaml")
        path.write_text('--- !Config\nlang: "en"\nfont_file: ""\nlang: "zh"\n',
                        encoding="utf-8")
        config_module.save_config_values({"lang": "zh"})
        loaded = config_module.yaml.load(path.read_text(encoding="utf-8"),
                                        Loader=config_module.Loader)
        self.assertEqual("zh", loaded.lang)
        self.assertEqual(2, path.read_text(encoding="utf-8").count('lang: "zh"'))

    def test_missing_key_is_added_to_its_section(self):
        config_module.save_config_values({"wikitext.other_versions": True,
                                          "human_original": False})
        text = self.saved_text()
        lines = text.splitlines()
        index = lines.index("  other_versions: true")
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

    def test_duplicate_keys_are_reported(self):
        """老版本写坏的配置（同一个键好几份）载入时要说一声，不然用户只看到「设置没生效」。"""
        path = self.root.joinpath("config.yaml")
        path.write_text('--- !Config\nlang: "en"\nfont_file: ""\nlang: "zh"\n',
                        encoding="utf-8")
        with self.assertLogs(level="WARNING") as logs:
            config_module.load_config(path)
        self.assertTrue(any("重复的键" in line and "lang" in line for line in logs.output),
                        logs.output)

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


class ReadSettingsFileTest(TestCase):
    """「导入配置文件」：认得出这是 config.yaml 还是 wiki_credentials.yaml（见设置页的按钮）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name: str, text: str) -> Path:
        path = self.root.joinpath(name)
        path.write_text(text, encoding="utf-8")
        return path

    def test_reads_config_with_custom_tags(self):
        # config.yaml 带 !Config / !WikitextConfig 这类自定义标签，得能读回「节.键」
        path = self._write("config.yaml", '--- !Config\n'
                                          'lang: "en"\n'
                                          'wikitext: !WikitextConfig\n'
                                          '  producer_template: true\n')
        kind, values, error = config_module.read_settings_file(path)
        self.assertEqual("config", kind)
        self.assertEqual("", error)
        self.assertEqual("en", values["lang"])
        self.assertTrue(values["wikitext.producer_template"])

    def test_reads_plain_config_without_tags(self):
        path = self._write("config.yaml", 'lang: "zh"\nimage:\n  crop: false\n')
        kind, values, _error = config_module.read_settings_file(path)
        self.assertEqual("config", kind)
        self.assertFalse(values["image.crop"])

    def test_reads_credentials(self):
        path = self._write("wiki_credentials.yaml",
                           'username: "user@bot"\nai_api_key: "sk-1"\n')
        kind, values, _error = config_module.read_settings_file(path)
        self.assertEqual("credentials", kind)
        self.assertEqual("user@bot", values["username"])
        self.assertEqual("sk-1", values["ai_api_key"])

    def test_unknown_keys_are_rejected(self):
        path = self._write("other.yaml", "foo: 1\nbar: 2\n")
        kind, values, error = config_module.read_settings_file(path)
        self.assertEqual("", kind)
        self.assertEqual({}, values)
        self.assertIn("既不像", error)

    def test_non_utf8_file_is_still_read(self):
        """GBK / 记事本 ANSI 存的设置文件也要能读。

        2026-09 踩过：`read_text(encoding="utf-8")` 遇到非 UTF-8 时抛 UnicodeDecodeError
        （它不是 OSError、没人接住），点「导入配置文件」就什么都不发生、连红字都没有。
        """
        path = self.root.joinpath("gbk.yaml")
        path.write_bytes('# 中文注释\nusername: "user@bot"\nai_model: "m"\n'.encode("cp936"))
        kind, values, error = config_module.read_settings_file(path)
        self.assertEqual("credentials", kind)
        self.assertEqual("", error)
        self.assertEqual("user@bot", values["username"])

    def test_split_settings_routes_by_key(self):
        """按**键**分家：混合文件里的凭据还得算凭据（整份判成 config 时会被悄悄丢掉）。"""
        config_values, credential_values = config_module.split_settings(
            {"lang": "en", "wikitext.crop": False, "username": "user@bot",
             "ai_api_key": "sk-1", "ai_thinking": True})
        self.assertEqual({"lang": "en", "wikitext.crop": False}, config_values)
        self.assertEqual({"username": "user@bot", "ai_api_key": "sk-1", "ai_thinking": True},
                         credential_values)
        self.assertEqual(({}, {}), config_module.split_settings({}))

    def test_broken_yaml_is_rejected(self):
        path = self._write("broken.yaml", "lang: [1, 2\n")
        kind, _values, error = config_module.read_settings_file(path)
        self.assertEqual("", kind)
        self.assertIn("YAML", error)

    def test_missing_file_is_rejected(self):
        kind, _values, error = config_module.read_settings_file(
            self.root.joinpath("nope.yaml"))
        self.assertEqual("", kind)
        self.assertIn("读不了", error)

    def test_flatten_settings_expands_sections(self):
        flat = config_module.flatten_settings({"lang": "zh", "wikitext": {"crop": False}})
        self.assertEqual({"lang": "zh", "wikitext.crop": False}, flat)

    def test_flatten_settings_accepts_config_instance(self):
        flat = config_module.flatten_settings(config_module.Config())
        self.assertIn("wikitext.producer_template", flat)
        self.assertIn("wiki.api_url", flat)
