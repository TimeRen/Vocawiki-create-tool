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
