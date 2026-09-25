"""打包脚本的凭据清洗测试：分发包里绝不能带真实密码或 AI 密钥。"""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock

import build


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
