"""utils/ai_lyrics.py 的测试（不联网）：开关、可用状态、请求内容与回复解析。"""
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from utils import ai_lyrics


def _config(allowed=True):
    return SimpleNamespace(wikitext=SimpleNamespace(ai_lyrics=allowed))


SETTINGS = {"provider": "openai", "base_url": "https://api.example.com/v1",
            "model": "test-model", "api_key": "sk-test", "thinking": False}


def _reply(payload):
    """模拟 OpenAI 兼容接口的回复。"""
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


class EnabledTest(TestCase):
    def test_reads_config_switch(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)):
            self.assertTrue(ai_lyrics.enabled())
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(False)):
            self.assertFalse(ai_lyrics.enabled())

    def test_missing_field_is_treated_as_off(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=SimpleNamespace(wikitext=object())):
            self.assertFalse(ai_lyrics.enabled())

    def test_context_hidden_when_switch_off(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(False)), \
             mock.patch.object(ai_lyrics.ai_css, "settings", return_value=SETTINGS):
            ctx = ai_lyrics.context()
        self.assertTrue(ctx["hidden"])
        self.assertFalse(ctx["enabled"])
        self.assertIn("ai_lyrics", ctx["reason"])

    def test_context_needs_api_key(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=dict(SETTINGS, api_key="")):
            ctx = ai_lyrics.context()
        self.assertFalse(ctx["hidden"])              # 开关是开的，按钮要显示（只是置灰）
        self.assertFalse(ctx["enabled"])
        self.assertIn("ai_api_key", ctx["reason"])

    def test_context_ready(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)), \
             mock.patch.object(ai_lyrics.ai_css, "settings", return_value=SETTINGS):
            ctx = ai_lyrics.context()
        self.assertTrue(ctx["enabled"])
        self.assertFalse(ctx["hidden"])
        self.assertEqual("test-model", ctx["model"])


class RecognizeTest(TestCase):
    def _recognize(self, payload, reply=None, allowed=True, settings=None):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(allowed)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=settings or SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post",
                               return_value=(reply, "")) as post:
            return ai_lyrics.recognize(json.dumps(payload)), post

    def test_split_reply(self):
        result, post = self._recognize({"text": "ああ　またダメだったな\n啊啊 看来还是不行呢"},
                                       _reply({"jap": "ああ　またダメだったな",
                                               "chs": "啊啊 看来还是不行呢", "roma": ""}))
        self.assertTrue(result["ok"])
        self.assertEqual("ai", result["mode"])
        self.assertEqual("ああ　またダメだったな", result["jap"])
        self.assertEqual("啊啊 看来还是不行呢", result["chs"])
        self.assertEqual("", result["roma"])
        self.assertIn("test-model", result["message"])
        self.assertTrue(post.called)

    def test_request_uses_lyrics_prompt_and_token_budget(self):
        _, post = self._recognize({"text": "あ", "jap": "既有的日语行"},
                                  _reply({"jap": "あ", "chs": "", "roma": ""}))
        body = post.call_args.args[2]
        self.assertEqual(ai_lyrics.MAX_TOKENS, body["max_tokens"])
        self.assertEqual(ai_lyrics.SYSTEM_PROMPT, body["messages"][0]["content"])
        prompt = body["messages"][1]["content"][0]["text"]
        self.assertIn("既有的日语行", prompt)
        self.assertTrue(prompt.rstrip().endswith('{"jap": "…", "chs": "…", "roma": ""}'))

    def test_reply_wrapped_in_fences_is_accepted(self):
        fenced = "```json\n" + json.dumps({"jap": "あ\n\nい\n", "chs": "啊"}, ensure_ascii=False) + "\n```"
        result, _ = self._recognize({"text": "あ"}, {"choices": [{"message": {"content": fenced}}]})
        self.assertTrue(result["ok"])
        self.assertEqual("あ\n\nい", result["jap"])       # 行尾空白与结尾空行会被去掉
        self.assertEqual("啊", result["chs"])

    def test_switch_off_never_calls_api(self):
        result, post = self._recognize({"text": "あ"}, _reply({"jap": "あ"}), allowed=False)
        self.assertFalse(result["ok"])
        self.assertIn("ai_lyrics", result["error"])
        self.assertFalse(post.called)

    def test_missing_api_key(self):
        result, post = self._recognize({"text": "あ"}, _reply({"jap": "あ"}),
                                       settings=dict(SETTINGS, api_key=""))
        self.assertFalse(result["ok"])
        self.assertIn("ai_api_key", result["error"])
        self.assertFalse(post.called)

    def test_empty_text(self):
        result, post = self._recognize({"text": "   "})
        self.assertFalse(result["ok"])
        self.assertIn("粘贴歌词", result["error"])
        self.assertFalse(post.called)

    def test_bad_payload(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)):
            self.assertFalse(ai_lyrics.recognize("不是 JSON")["ok"])
            self.assertFalse(ai_lyrics.recognize("[1, 2]")["ok"])

    def test_network_error_is_reported(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True)), \
             mock.patch.object(ai_lyrics.ai_css, "settings", return_value=SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post", return_value=(None, "网络请求失败：boom")):
            result = ai_lyrics.recognize('{"text": "あ"}')
        self.assertFalse(result["ok"])
        self.assertEqual("网络请求失败：boom", result["error"])

    def test_reply_without_json(self):
        result, _ = self._recognize({"text": "あ"},
                                    {"choices": [{"message": {"content": "我不知道"}}]})
        self.assertFalse(result["ok"])
        self.assertIn("不是 JSON", result["error"])

    def test_reply_without_any_lyrics(self):
        result, _ = self._recognize({"text": "あ"},
                                    _reply({"jap": "", "chs": "", "roma": "  "}))
        self.assertFalse(result["ok"])
        self.assertIn("没有识别出", result["error"])


class CleanLinesTest(TestCase):
    def test_strips_fences_and_blank_edges(self):
        self.assertEqual("あ\nい", ai_lyrics.clean_lines("```\nあ\nい\n\n```"))
        self.assertEqual("あ\n\n い", ai_lyrics.clean_lines("あ  \n\n い "))   # 只去行尾空白
        self.assertEqual("", ai_lyrics.clean_lines(None))
        self.assertEqual("あ", ai_lyrics.clean_lines("あ\r\n"))

    def test_build_prompt_without_reference(self):
        prompt = ai_lyrics.build_prompt("あ\n啊")
        self.assertIn("待归类歌词", prompt)
        self.assertNotIn("已确认的日语原文", prompt)
        self.assertIn("あ\n啊", prompt)

    def test_build_prompt_with_reference(self):
        prompt = ai_lyrics.build_prompt("あ\n啊", jap="あ")
        self.assertIn("已确认的日语原文", prompt)
