"""utils/ai_css.py 的单元测试：配置读取、输出清洗、请求构造与错误处理。

不联网：所有 HTTP 都由 mock 替换。
"""
import base64
import io
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from PIL import Image

from utils import ai_css


def _image_bytes(size=(1600, 900), color=(20, 22, 28)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _data_uri(size=(1600, 900)) -> str:
    return "data:image/png;base64," + base64.b64encode(_image_bytes(size)).decode("ascii")


OPENAI_REPLY = {
    "choices": [{"message": {"content": '{"lyrOrig": "color: #eaeaea; font-size: 15px;"}'}}]
}


class SettingsTest(TestCase):
    def test_defaults_when_credentials_incomplete(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"provider": "", "base_url": "", "model": "", "api_key": "k"}):
            cfg = ai_css.settings()
        self.assertEqual("openai", cfg["provider"])
        self.assertEqual(ai_css.DEFAULT_BASE_URL, cfg["base_url"])
        self.assertEqual(ai_css.DEFAULT_MODEL, cfg["model"])
        self.assertEqual("k", cfg["api_key"])
        self.assertFalse(cfg["thinking"])

    def test_defaults_are_deepseek(self):
        """预设必须是 DeepSeek-V4.1-Flash（deepseek-flash，支持看图）。"""
        self.assertIn("deepseek", ai_css.DEFAULT_BASE_URL)
        self.assertEqual("deepseek-flash", ai_css.DEFAULT_MODEL)

    def test_thinking_flag_parsing(self):
        for raw, expected in (("true", True), (True, True), ("false", False), ("", False), (None, False)):
            with mock.patch.object(ai_css, "get_ai_credentials",
                                   return_value={"api_key": "k", "thinking": raw}):
                self.assertEqual(expected, ai_css.settings()["thinking"], f"thinking={raw!r}")

    def test_anthropic_defaults_and_trailing_slash(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"provider": "Anthropic", "base_url": "https://x.test/",
                                             "model": "", "api_key": "k"}):
            cfg = ai_css.settings()
        self.assertEqual("anthropic", cfg["provider"])
        self.assertEqual("https://x.test", cfg["base_url"])          # 去掉结尾斜杠
        self.assertEqual(ai_css.DEFAULT_ANTHROPIC_MODEL, cfg["model"])

    def test_unknown_provider_falls_back_to_openai(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"provider": "openrouter", "api_key": "k"}):
            self.assertEqual("openai", ai_css.settings()["provider"])

    def test_context_reports_missing_key(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"api_key": ""}), \
             mock.patch.object(ai_css, "get_config",
                               return_value=SimpleNamespace(color=SimpleNamespace(ai_css=True))):
            ctx = ai_css.context()
        self.assertFalse(ctx["enabled"])
        self.assertFalse(ctx["hidden"])
        self.assertIn("ai_api_key", ctx["reason"])

    def test_context_hidden_when_switched_off(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"api_key": "k"}), \
             mock.patch.object(ai_css, "get_config",
                               return_value=SimpleNamespace(color=SimpleNamespace(ai_css=False))):
            ctx = ai_css.context()
        self.assertFalse(ctx["enabled"])
        self.assertTrue(ctx["hidden"])

    def test_context_enabled(self):
        with mock.patch.object(ai_css, "get_ai_credentials",
                               return_value={"api_key": "k", "model": "m"}), \
             mock.patch.object(ai_css, "get_config",
                               return_value=SimpleNamespace(color=SimpleNamespace(ai_css=True))):
            ctx = ai_css.context()
        self.assertTrue(ctx["enabled"])
        self.assertEqual("m", ctx["model"])

    # —— 三栏默认提示词（config.yaml 的 color.ai_prompt_*） ——

    def test_context_carries_prompt_defaults(self):
        color = SimpleNamespace(ai_css=True, ai_prompt_songbox=" 以封面主色为底 ",
                                ai_prompt_intro="", ai_prompt_lyrics="容器加圆角")
        with mock.patch.object(ai_css, "get_ai_credentials", return_value={"api_key": "k"}), \
             mock.patch.object(ai_css, "get_config", return_value=SimpleNamespace(color=color)):
            ctx = ai_css.context()
        self.assertEqual({"songbox": "以封面主色为底", "intro": "", "lyrics": "容器加圆角"},
                         ctx["prompts"])

    def test_prompt_defaults_missing_fields_are_empty(self):
        # 旧配置文件里没有这三个字段时也要能跑
        with mock.patch.object(ai_css, "get_config",
                               return_value=SimpleNamespace(color=SimpleNamespace(ai_css=True))):
            self.assertEqual({"songbox": "", "intro": "", "lyrics": ""}, ai_css.prompt_defaults())

    def test_prompt_defaults_treat_none_as_empty(self):
        color = SimpleNamespace(ai_prompt_songbox=None, ai_prompt_intro=None, ai_prompt_lyrics=None)
        with mock.patch.object(ai_css, "get_config", return_value=SimpleNamespace(color=color)):
            self.assertEqual({"songbox": "", "intro": "", "lyrics": ""}, ai_css.prompt_defaults())

    def test_prompt_keys_match_editor_tabs(self):
        self.assertEqual(["songbox", "intro", "lyrics"], [k for k, _ in ai_css.PROMPT_KEYS])


class CleanCssTest(TestCase):
    def test_strips_fences_comments_and_important(self):
        css = ai_css.clean_css("```css\n/* 注释 */ color: #fff !important;\n```")
        self.assertEqual("color: #fff;", css)

    def test_drops_selectors_and_blocks(self):
        css = ai_css.clean_css(".tag { color: #fff; }")
        self.assertEqual("color: #fff;", css)

    def test_drops_junk_and_empty_values(self):
        css = ai_css.clean_css("color: ;;; 乱七八糟; background: #000")
        self.assertEqual("background: #000;", css)

    def test_salvages_prefixed_property(self):
        css = ai_css.clean_css(".foo color: #123456;")
        self.assertEqual("color: #123456;", css)

    def test_removes_external_url(self):
        css = ai_css.clean_css("background: url(http://x/y.png); color: #fff")
        self.assertEqual("background: none; color: #fff;", css)

    def test_keeps_only_first_hundreds_of_chars(self):
        css = ai_css.clean_css(";".join(f"p{i}: {i}px" for i in range(400)))
        self.assertLessEqual(len(css), ai_css.MAX_CSS_LENGTH + 1)
        self.assertTrue(css.endswith(";"))

    def test_empty_input(self):
        self.assertEqual("", ai_css.clean_css(""))
        self.assertEqual("", ai_css.clean_css(None))


class ExtractJsonTest(TestCase):
    def test_plain_json(self):
        self.assertEqual({"a": "b"}, ai_css.extract_json('{"a": "b"}'))

    def test_fenced_json_with_text(self):
        self.assertEqual({"a": "b"}, ai_css.extract_json('好的：\n```json\n{"a": "b"}\n```'))

    def test_invalid_json(self):
        self.assertIsNone(ai_css.extract_json("not json"))
        self.assertIsNone(ai_css.extract_json('["a"]'))


class PromptTest(TestCase):
    def test_prompt_lists_targets_props_and_note(self):
        targets = [{"id": "lyrOrig", "label": "原文（|lstyle）", "props": ["color", "font-size"],
                    "current": "color: #000000"}]
        prompt = ai_css.build_prompt(targets, True, "以封面主色为准")
        self.assertIn("colorOnly = true", prompt)
        self.assertIn("lyrOrig", prompt)
        self.assertIn("color, font-size", prompt)
        self.assertIn("以封面主色为准", prompt)
        self.assertIn('{"lyrOrig"', prompt)


class BuildRequestTest(TestCase):
    def _cfg(self, provider, base_url=None):
        return {"provider": provider, "model": "m", "api_key": "secret", "thinking": False,
                "base_url": base_url or ("https://api.test/v1" if provider == "openai"
                                         else "https://api.anthropic.com")}

    def test_openai_body_carries_image_and_json_mode(self):
        url, headers, body = ai_css.build_request(self._cfg("openai"), "提示", ("image/jpeg", "AAA"))
        self.assertEqual("https://api.test/v1/chat/completions", url)
        self.assertEqual("Bearer secret", headers["Authorization"])
        self.assertEqual({"type": "json_object"}, body["response_format"])
        content = body["messages"][1]["content"]
        self.assertEqual("提示", content[0]["text"])
        self.assertEqual("data:image/jpeg;base64,AAA", content[1]["image_url"]["url"])
        self.assertEqual(ai_css.SYSTEM_PROMPT, body["messages"][0]["content"])
        self.assertNotIn("thinking", body)                   # 非 DeepSeek 不传该项

    def test_deepseek_disables_thinking_by_default(self):
        cfg = self._cfg("openai", "https://api.deepseek.com/v1")
        url, _, body = ai_css.build_request(cfg, "提示", ("image/jpeg", "AAA"))
        self.assertEqual("https://api.deepseek.com/v1/chat/completions", url)
        self.assertEqual({"type": "disabled"}, body["thinking"])

    def test_deepseek_thinking_can_be_enabled(self):
        cfg = dict(self._cfg("openai", "https://api.deepseek.com/v1"), thinking=True)
        _, _, body = ai_css.build_request(cfg, "提示", None)
        self.assertEqual({"type": "enabled"}, body["thinking"])

    def test_deepseek_anthropic_endpoint(self):
        cfg = self._cfg("anthropic", "https://api.deepseek.com/anthropic")
        url, headers, body = ai_css.build_request(cfg, "提示", ("image/jpeg", "AAA"))
        self.assertEqual("https://api.deepseek.com/anthropic/v1/messages", url)
        self.assertEqual("secret", headers["x-api-key"])
        self.assertEqual(ai_css.SYSTEM_PROMPT, body["system"])

    def test_anthropic_body(self):
        url, headers, body = ai_css.build_request(self._cfg("anthropic"), "提示", ("image/jpeg", "AAA"))
        self.assertEqual("https://api.anthropic.com/v1/messages", url)
        self.assertEqual("secret", headers["x-api-key"])
        self.assertEqual(ai_css.ANTHROPIC_VERSION, headers["anthropic-version"])
        self.assertEqual("base64", body["messages"][0]["content"][0]["source"]["type"])
        self.assertEqual("AAA", body["messages"][0]["content"][0]["source"]["data"])
        self.assertNotIn("response_format", body)          # Anthropic 不带该参数

    def test_openai_without_image(self):
        _, _, body = ai_css.build_request(self._cfg("openai"), "提示", None)
        self.assertEqual(1, len(body["messages"][1]["content"]))


class ImageEncodeTest(TestCase):
    def test_data_uri_is_downscaled_to_jpeg(self):
        result = ai_css.encode_data_uri(_data_uri((1600, 900)))
        self.assertIsNotNone(result)
        mime, data = result
        self.assertEqual("image/jpeg", mime)
        image = Image.open(io.BytesIO(base64.b64decode(data)))
        self.assertLessEqual(max(image.size), ai_css.MAX_IMAGE_EDGE)

    def test_small_image_is_kept(self):
        mime, data = ai_css.encode_data_uri(_data_uri((300, 200)))
        self.assertEqual((300, 200), Image.open(io.BytesIO(base64.b64decode(data))).size)

    def test_invalid_inputs(self):
        self.assertIsNone(ai_css.encode_data_uri("http://x/y.png"))
        self.assertIsNone(ai_css.encode_data_uri(""))
        self.assertIsNone(ai_css.encode_image_file("C:/definitely/not/here.png"))
        self.assertIsNone(ai_css.encode_image_bytes(b"not an image"))


class GenerateCssTest(TestCase):
    def setUp(self):
        self.cfg = {"provider": "openai", "base_url": "https://api.test/v1", "thinking": False,
                    "model": "m", "api_key": "secret"}
        self.patcher = mock.patch.object(ai_css, "settings", return_value=dict(self.cfg))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _payload(self, **kwargs):
        payload = {
            "colorOnly": True,
            "targets": [{"id": "lyrOrig", "label": "原文", "props": ["color"]}],
            "image": _data_uri((64, 64)),
        }
        payload.update(kwargs)
        return json.dumps(payload)

    def test_bad_json(self):
        result = ai_css.generate_css("{not json")
        self.assertFalse(result["ok"])
        self.assertIn("JSON", result["error"])

    def test_missing_api_key(self):
        with mock.patch.object(ai_css, "settings",
                               return_value={"provider": "openai", "base_url": "", "model": "", "api_key": ""}):
            result = ai_css.generate_css(self._payload())
        self.assertFalse(result["ok"])
        self.assertIn("ai_api_key", result["error"])

    def test_no_targets(self):
        result = ai_css.generate_css(self._payload(targets=[]))
        self.assertFalse(result["ok"])
        self.assertIn("没有需要生成的对象", result["error"])

    def test_no_image(self):
        result = ai_css.generate_css(self._payload(image=""), cover_image=None)
        self.assertFalse(result["ok"])
        self.assertIn("封面图", result["error"])

    def test_success(self):
        with mock.patch.object(ai_css, "_post", return_value=(OPENAI_REPLY, "")) as post:
            result = ai_css.generate_css(self._payload())
        self.assertTrue(result["ok"])
        self.assertEqual("color: #eaeaea; font-size: 15px;", result["css"]["lyrOrig"])
        self.assertEqual("m", result["model"])
        # 请求里确实带了图
        _, headers, body = post.call_args[0]
        self.assertTrue(body["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg"))

    def test_image_falls_back_to_cover_file(self):
        import tempfile
        from pathlib import Path
        with mock.patch.object(ai_css, "_post", return_value=(None, "没有图")):
            self.assertFalse(ai_css.generate_css(self._payload(image=""), cover_image=None)["ok"])
        with tempfile.TemporaryDirectory() as folder:
            cover = Path(folder) / "cover.png"
            cover.write_bytes(_image_bytes((80, 80)))
            with mock.patch.object(ai_css, "_post", return_value=(OPENAI_REPLY, "")):
                result = ai_css.generate_css(self._payload(image=""), cover_image=cover)
        self.assertTrue(result["ok"])

    def test_retries_without_response_format(self):
        with mock.patch.object(ai_css, "_post",
                               side_effect=[(None, '接口返回 400：{"error": "response_format not supported"}'),
                                            (OPENAI_REPLY, "")]) as post:
            result = ai_css.generate_css(self._payload())
        self.assertTrue(result["ok"])
        self.assertEqual(2, post.call_count)
        self.assertNotIn("response_format", post.call_args_list[1][0][2])

    def test_retries_without_thinking(self):
        cfg = dict(self.cfg, base_url="https://api.deepseek.com/v1")
        seen = []

        def fake_post(url, headers, body):
            seen.append(json.loads(json.dumps(body)))        # 快照：重试会就地删掉该字段
            if len(seen) == 1:
                return None, "接口返回 400：unsupported parameter thinking"
            return OPENAI_REPLY, ""

        with mock.patch.object(ai_css, "settings", return_value=cfg), \
             mock.patch.object(ai_css, "_post", side_effect=fake_post):
            result = ai_css.generate_css(self._payload())
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(seen))
        self.assertEqual({"type": "disabled"}, seen[0]["thinking"])
        self.assertNotIn("thinking", seen[1])

    def test_plain_text_reply_for_single_target(self):
        reply = {"choices": [{"message": {"content": "```css\ncolor: #123456;\n```"}}]}
        with mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(self._payload())
        self.assertTrue(result["ok"])
        self.assertEqual("color: #123456;", result["css"]["lyrOrig"])

    def test_http_error_is_reported(self):
        with mock.patch.object(ai_css, "_post", return_value=(None, "接口返回 401：unauthorized")):
            result = ai_css.generate_css(self._payload())
        self.assertFalse(result["ok"])
        self.assertIn("401", result["error"])

    def test_unparsable_reply(self):
        reply = {"choices": [{"message": {"content": "抱歉，我无法完成"}}]}
        with mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(self._payload())
        self.assertFalse(result["ok"])
        self.assertIn("解析", result["error"])

    def test_anthropic_reply_shape(self):
        cfg = dict(self.cfg, provider="anthropic")
        reply = {"content": [{"type": "text", "text": '{"lyrOrig": "color: #fff;"}'}]}
        with mock.patch.object(ai_css, "settings", return_value=cfg), \
             mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(self._payload())
        self.assertTrue(result["ok"])
        self.assertEqual("color: #fff;", result["css"]["lyrOrig"])

    def test_list_reply_is_joined(self):
        reply = {"choices": [{"message": {"content": '{"lyrOrig": ["color: #fff", "font-size: 14px"]}'}}]}
        with mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(self._payload())
        self.assertEqual("color: #fff; font-size: 14px;", result["css"]["lyrOrig"])

    def test_ignores_ids_not_requested(self):
        reply = {"choices": [{"message": {"content": '{"lyrTrans": "color: #fff;"}'}}]}
        with mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(self._payload())
        self.assertFalse(result["ok"])
