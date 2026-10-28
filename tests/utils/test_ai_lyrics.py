"""utils/ai_lyrics.py 的测试（不联网）：开关、可用状态、请求内容与回复解析。"""
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

from utils import ai_lyrics


def _config(allowed=True, furigana=False):
    return SimpleNamespace(wikitext=SimpleNamespace(ai_lyrics=allowed, furigana_all=furigana))


SETTINGS = {"provider": "openai", "base_url": "https://api.example.com/v1",
            "model": "test-model", "api_key": "sk-test", "thinking": False}


def _reply(payload):
    """模拟 OpenAI 兼容接口的回复。"""
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


def _text_reply(content):
    """模拟直接返回纯文本的回复（振假名那一路不用 JSON）。"""
    return {"choices": [{"message": {"content": content}}]}


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

    def test_parenthesised_readings_are_templated_before_the_model_sees_them(self):
        """`漢字(かんじ)` 先转成 {{photrans}} 再交给模型。

        模型看到「矜持(きょうじ)」这类括号注音时，常把它当成可以省略的注释删掉，返回
        「矜持きょうじ」——读音糊进正文，之后再跑 `with_furigana()` 也救不回来
        （实测 voca.wiki《欢。》里初音ミク Wiki 的 `矜持(きょうじ)` / `音(ね)` / `咲(わら)` /
        `夜(よ)` 全变成了这种写法）。转成模板后模型只会原样抄回来。
        """
        result, post = self._recognize(
            {"text": "｢矜持(きょうじ)｣だ、って\n“是矜持”"},
            _reply({"jap": "｢{{photrans|矜持|きょうじ}}｣だ、って", "chs": "“是矜持”", "roma": ""}))
        prompt = post.call_args.args[2]["messages"][1]["content"][0]["text"]
        self.assertIn("{{photrans|矜持|きょうじ}}", prompt)
        self.assertNotIn("矜持(きょうじ)", prompt)
        self.assertTrue(result["ok"])
        self.assertIn("{{photrans|矜持|きょうじ}}", result["jap"])

    def test_the_reference_column_is_templated_too(self):
        """「已确认的日语原文」里的括号注音同样先转成模板。"""
        _, post = self._recognize({"text": "あ", "jap": "またここに夜(よ)が沈む。"},
                                  _reply({"jap": "あ", "chs": "", "roma": ""}))
        prompt = post.call_args.args[2]["messages"][1]["content"][0]["text"]
        self.assertIn("{{photrans|夜|よ}}が沈む。", prompt)

    def test_prompt_tells_the_model_to_keep_photrans(self):
        self.assertIn("photrans", ai_lyrics.SYSTEM_PROMPT)
        self.assertIn("注音", ai_lyrics.SYSTEM_PROMPT)

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


class MarkTranslationTest(TestCase):
    """中文栏和日语栏行数对不上时，让 AI 给出「每行中文对应第几行日语」。"""

    def _mark(self, jap, chs, reply=None, marks=None, allowed=True, settings=None):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(allowed)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=settings or SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post",
                               return_value=(reply, "")) as post:
            return ai_lyrics.mark_translation(jap, chs, marks or {}), post

    def test_pairs_are_zero_based_and_in_range(self):
        result, post = self._mark("あ\nい\nう", "啊\n咦", _reply({"pairs": {"1": [1], "2": [2, 3]}}))
        self.assertTrue(result["ok"])
        self.assertEqual({0: [0], 1: [1, 2]}, result["pairs"])
        self.assertTrue(post.called)

    def test_out_of_range_and_junk_are_dropped(self):
        result, _ = self._mark("あ\nい", "啊",
                               _reply({"pairs": {"1": [1, 99, "x"], "abc": [1], "0": [1]}}))
        self.assertTrue(result["ok"])
        self.assertEqual({0: [0]}, result["pairs"])

    def test_no_usable_pairs_is_an_error(self):
        result, _ = self._mark("あ\nい", "啊", _reply({"pairs": "什么"}))
        self.assertFalse(result["ok"])
        self.assertIn("对齐", result["error"])

    def test_prompt_carries_both_columns_and_marks(self):
        _, post = self._mark("あ\nい", "啊\n咦", _reply({"pairs": {"1": [1], "2": [2]}}),
                             marks={"0": ["初音未来"]})
        prompt = post.call_args.args[2]["messages"][1]["content"][0]["text"]
        self.assertIn("日语歌词", prompt)
        self.assertIn("中文歌词", prompt)
        self.assertIn("初音未来", prompt)
        self.assertIn("1 | 初音未来 | あ", prompt)

    def test_empty_columns_do_not_call_the_api(self):
        result, post = self._mark("", "啊", _reply({"pairs": {}}))
        self.assertFalse(result["ok"])
        self.assertIn("日语栏", result["error"])
        result, post = self._mark("あ", "  ", _reply({"pairs": {}}))
        self.assertFalse(result["ok"])
        self.assertIn("中文栏", result["error"])
        self.assertFalse(post.called)


class LayoutTranslationTest(TestCase):
    """没有「待归类歌词」时：按日语栏给中文栏排版（只动换行，不改字）。"""

    def _layout(self, jap, chs, reply=None, allowed=True, settings=None):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(allowed)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=settings or SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post",
                               return_value=(reply, "")) as post:
            return ai_lyrics.layout_translation(jap, chs), post

    def test_returns_the_laid_out_chinese_column(self):
        result, post = self._layout("あ\nい\n\nう", "啊咦\n\n呜",
                                    _reply({"chs": "啊\n咦\n\n呜"}))
        self.assertTrue(result["ok"])
        self.assertEqual("啊\n咦\n\n呜", result["chs"])
        self.assertEqual("test-model", result["model"])
        self.assertTrue(post.called)

    def test_prompt_carries_both_columns(self):
        _, post = self._layout("あ\nい", "啊咦", _reply({"chs": "啊\n咦"}))
        prompt = post.call_args.args[2]["messages"][1]["content"][0]["text"]
        self.assertIn("日语歌词", prompt)
        self.assertIn("中文翻译", prompt)
        self.assertIn("あ\nい", prompt)

    def test_rewritten_text_is_rejected(self):
        """模型只许动换行：偷偷改了字（翻译 / 润色 / 丢字）就整条判失败。"""
        result, _ = self._layout("あ\nい", "啊咦", _reply({"chs": "啊\n哟"}))
        self.assertFalse(result["ok"])
        self.assertIn("改动", result["error"])

    def test_whitespace_changes_are_fine(self):
        result, _ = self._layout("あ\nい", "啊咦", _reply({"chs": "啊 咦\n"}))
        self.assertTrue(result["ok"])

    def test_empty_columns_do_not_call_the_api(self):
        result, post = self._layout("", "啊", _reply({"chs": "啊"}))
        self.assertFalse(result["ok"])
        self.assertIn("日语栏", result["error"])
        result, post = self._layout("あ", "  ", _reply({"chs": "啊"}))
        self.assertFalse(result["ok"])
        self.assertIn("中文栏", result["error"])
        self.assertFalse(post.called)

    def test_switch_off_rejects(self):
        result, post = self._layout("あ", "啊", _reply({"chs": "啊"}), allowed=False)
        self.assertFalse(result["ok"])
        self.assertIn("ai_lyrics", result["error"])
        self.assertFalse(post.called)

    def test_prompt_tells_the_model_to_merge_short_lines(self):
        """译者把一句拆成好几行短句时要合并成一行（用户 2026-10-29 报《幽灵(KAIRUI)》）。"""
        self.assertIn("合并成一行", ai_lyrics.LAYOUT_SYSTEM_PROMPT)
        self.assertIn("一个空格", ai_lyrics.LAYOUT_SYSTEM_PROMPT)
        self.assertIn("行数刚好相等", ai_lyrics.LAYOUT_SYSTEM_PROMPT)

    def test_a_reply_that_merged_nothing_is_rejected(self):
        """中文 17 行、日语 11 行，模型把原文抄回来 → 不可能一行对一句，判失败。"""
        jap = "あ\nい\nう\n\nえ\nお\nか\nき\n\nく\nけ\nこ"
        chs = "甲\n乙\n丙\n\n丁\n戊\n己\n\n庚\n辛\n壬\n\n癸\n子\n丑\n\n寅\n卯\n辰\n巳"
        result, _ = self._layout(jap, chs, _reply({"chs": chs}))
        self.assertFalse(result["ok"])
        self.assertIn("合并", result["error"])

    def test_a_merged_reply_is_accepted(self):
        jap = "あ\nい\n\nう\nえ"
        chs = "甲\n乙\n\n丙\n丁"
        result, _ = self._layout(jap, chs, _reply({"chs": "甲 乙\n\n丙\n丁"}))
        self.assertTrue(result["ok"])
        self.assertEqual("甲 乙\n\n丙\n丁", result["chs"])


class SameTextIgnoringBreaksTest(TestCase):
    def test_ignores_whitespace_only(self):
        self.assertTrue(ai_lyrics.same_text_ignoring_breaks("啊\n咦", "啊 咦"))
        self.assertFalse(ai_lyrics.same_text_ignoring_breaks("啊\n咦", "啊\n哟"))
        self.assertFalse(ai_lyrics.same_text_ignoring_breaks("啊\n咦", "啊"))


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


class FuriganaTest(TestCase):
    """AI 生成振假名（wikitext.furigana_all）：只允许加注释，不许动歌词正文。"""

    LYRICS = "食べる\n初音ミク"

    def _add(self, reply, source=None, allowed=True, furigana=True, settings=None):
        with mock.patch.object(ai_lyrics, "get_config",
                               return_value=_config(allowed, furigana)), \
             mock.patch.object(ai_lyrics.ai_css, "settings",
                               return_value=settings or SETTINGS), \
             mock.patch.object(ai_lyrics.ai_css, "_post",
                               return_value=(reply, "")) as post:
            return ai_lyrics.add_furigana(source or self.LYRICS), post

    def test_reads_config_switch(self):
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True, True)):
            self.assertTrue(ai_lyrics.furigana_enabled())
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True, False)):
            self.assertFalse(ai_lyrics.furigana_enabled())

    def test_adds_furigana_to_kanji(self):
        reply = _text_reply("{{photrans|食|た}}べる\n{{photrans|初音|はつね}}ミク")
        result, post = self._add(reply)
        self.assertTrue(result["ok"])
        self.assertEqual("{{photrans|食|た}}べる\n{{photrans|初音|はつね}}ミク", result["lyrics"])
        self.assertEqual(2, result["added"])
        self.assertIn("2", result["message"])
        body = post.call_args.args[2]
        self.assertEqual(ai_lyrics.FURIGANA_SYSTEM_PROMPT, body["messages"][0]["content"])
        self.assertIn("食べる", body["messages"][1]["content"][0]["text"])

    def test_rejects_reply_that_changes_the_lyrics(self):
        # 模型敢改歌词（少一行 / 改词）就整段丢弃，宁可不要振假名
        result, post = self._add(_text_reply("{{photrans|食|た}}べる"))
        self.assertTrue(post.called)
        self.assertFalse(result["ok"])
        self.assertIn("改动", result["error"])
        result, _ = self._add(_text_reply("{{photrans|食|た}}べる\n初音未来"))
        self.assertFalse(result["ok"])

    def test_parenthesised_furigana_counts_as_unchanged(self):
        # 原文写成「漢字(かんじ)」、模型写成 {{photrans}} 算等价，不算改歌词
        result, _ = self._add(_text_reply("{{photrans|漢字|かんじ}}\nあ"),
                              source="漢字(かんじ)\nあ")
        self.assertTrue(result["ok"])
        self.assertEqual(1, result["added"])

    def test_switch_off_never_calls_api(self):
        result, post = self._add(_text_reply("x"), furigana=False)
        self.assertFalse(result["ok"])
        self.assertIn("furigana_all", result["error"])
        self.assertFalse(post.called)

    def test_missing_api_key(self):
        result, post = self._add(_text_reply("x"), settings=dict(SETTINGS, api_key=""))
        self.assertFalse(result["ok"])
        self.assertIn("ai_api_key", result["error"])
        self.assertFalse(post.called)

    def test_empty_lyrics(self):
        result, post = self._add(_text_reply("x"), source="   ")
        self.assertFalse(result["ok"])
        self.assertFalse(post.called)

    def test_generate_returns_original_when_disabled_or_failed(self):
        # 生成流程用的包装：关着 / 失败都原样返回
        with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True, False)):
            self.assertEqual(self.LYRICS, ai_lyrics.generate_furigana(self.LYRICS))
        with mock.patch.object(ai_lyrics, "add_furigana",
                               return_value={"ok": False, "error": "boom"}):
            with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True, True)):
                self.assertEqual(self.LYRICS, ai_lyrics.generate_furigana(self.LYRICS))
        # 空歌词（只有空白）也原样返回
        self.assertEqual("  ", ai_lyrics.generate_furigana("  "))

    def test_generate_returns_annotated_lyrics(self):
        with mock.patch.object(ai_lyrics, "add_furigana",
                               return_value={"ok": True, "lyrics": "{{photrans|食|た}}べる",
                                             "message": "已补 1 处振假名"}):
            with mock.patch.object(ai_lyrics, "get_config", return_value=_config(True, True)):
                self.assertEqual("{{photrans|食|た}}べる", ai_lyrics.generate_furigana("食べる"))
