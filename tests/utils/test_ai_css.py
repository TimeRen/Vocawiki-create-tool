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

    def test_prompt_demands_an_entry_for_every_id(self):
        """模型经常只答其中几个（用户 2026-09-29 报：要 Introduction 却只给了歌词）→ 提示里写明。"""
        targets = [{"id": "introLabel", "label": "标签格", "props": ["color"]}]
        prompt = ai_css.build_prompt(targets, False, "")
        self.assertIn("每个 id 都要给一条", prompt)
        self.assertIn("不要自己编 id", prompt)

    def test_completion_prompt_asks_only_for_the_missing_ones(self):
        targets = [{"id": "introLabel", "label": "标签格", "props": ["color"]}]
        prompt = ai_css.build_prompt(targets, False, "", completion=True)
        self.assertIn("漏了", prompt)
        self.assertIn("只输出它们的", prompt)

    def test_prompt_lists_the_cover_palette_and_brightness(self):
        """把量出来的封面主色 / 明暗写进提示词给模型当依据。

        用户 2026-09-29 报「每次生成 |containerstyle= 的颜色都几乎一模一样」：那张封面是
        浅蓝白的，模型却每次都按「当前样式」回到同一套深紫 —— 给它真实主色才有得依。
        """
        targets = [{"id": "introLabel", "label": "标签格", "props": ["color"]}]
        palette = {"colors": [("#dce6ee", 0.42), ("#1b2130", 0.18)],
                   "brightness": 206, "light": True}
        prompt = ai_css.build_prompt(targets, False, "", palette=palette)
        self.assertIn("#dce6ee（42%）", prompt)
        self.assertIn("#1b2130（18%）", prompt)
        self.assertIn("平均亮度 206/255", prompt)
        self.assertIn("偏亮", prompt)

    def test_prompt_without_palette_has_no_palette_lines(self):
        targets = [{"id": "lyrOrig", "label": "原文", "props": ["color"]}]
        self.assertNotIn("封面配色", ai_css.build_prompt(targets, False, ""))

    def test_prompt_tells_the_model_not_to_copy_the_current_colours(self):
        targets = [{"id": "introLabel", "label": "标签格", "props": ["color"],
                    "current": "background: #1a2333;"}]
        prompt = ai_css.build_prompt(targets, False, "")
        self.assertIn("颜色不要沿用", prompt)
        self.assertIn("不要照抄", ai_css.SYSTEM_PROMPT)

    def test_prompt_carries_the_style_variant(self):
        """每次生成随机指定一种「配色用法」，让同一张封面每点一次都是新风格。"""
        targets = [{"id": "lyrContainer", "label": "容器", "props": ["color"]}]
        variant = "扁平双色：底色用封面次色（占比第二的那个），边框用主色，不加深渐变"
        prompt = ai_css.build_prompt(targets, False, "", variant=variant)
        self.assertIn("本次配色用法", prompt)
        self.assertIn(variant, prompt)

    def test_variant_list_is_varied_and_safe(self):
        # 变体要够多（不然连点两次就腻），而且都是「一句话要求」，不能带换行 / JSON 括号
        self.assertGreaterEqual(len(ai_css.STYLE_VARIANTS), 6)
        self.assertEqual(len(set(ai_css.STYLE_VARIANTS)), len(ai_css.STYLE_VARIANTS))
        for variant in ai_css.STYLE_VARIANTS:
            self.assertNotIn("\n", variant)
            self.assertNotIn("{", variant)

    def test_pick_variant_never_repeats_itself(self):
        """连点两次不该抽到同一种用法（用户 2026-09-29：每点一次换一种风格）。"""
        ai_css._last_variant = None
        self.addCleanup(setattr, ai_css, "_last_variant", None)
        offered = []

        def fake_choice(items):
            offered.append(list(items))
            return items[0]

        with mock.patch.object(ai_css.random, "choice", side_effect=fake_choice):
            first = ai_css.pick_variant()
            second = ai_css.pick_variant()
        self.assertEqual(list(ai_css.STYLE_VARIANTS), offered[0])   # 第一次八种都候选
        self.assertNotIn(first, offered[1])                         # 上一次用过的被剔除
        self.assertNotEqual(first, second)

        ai_css._last_variant = None
        with mock.patch.object(ai_css.random, "choice", side_effect=lambda items: items[-1]):
            picked = [ai_css.pick_variant() for _ in range(6)]
        for before, after in zip(picked, picked[1:]):
            self.assertNotEqual(before, after)      # 只保证「不撞上一次」


class CoverPaletteTest(TestCase):
    """`cover_palette()`：量封面主色与整体明暗（给提示词用）。"""

    @staticmethod
    def _png(size, color, band=None):
        image = Image.new("RGB", size, color)
        if band:
            height, band_color = band
            for x in range(size[0]):
                for y in range(height):
                    image.putpixel((x, y), band_color)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_light_cover_reports_light_scheme(self):
        # 浅蓝白底 + 上方一条深色（《涅槃(HotaRu)》那张封面就是这个调子）
        palette = ai_css.cover_palette(self._png((100, 100), (220, 230, 238), (20, (27, 33, 48))),
                                       count=3)
        colors = [color for color, _ratio in palette["colors"]]
        self.assertTrue(palette["light"])
        self.assertGreater(palette["brightness"], 128)
        self.assertGreater(int(colors[0][1:3], 16), 200)          # 占比最大的是浅底色
        self.assertTrue(any(int(color[1:3], 16) < 100 for color in colors))
        self.assertAlmostEqual(1.0, sum(ratio for _c, ratio in palette["colors"]), places=2)

    def test_dark_cover_reports_dark_scheme(self):
        palette = ai_css.cover_palette(self._png((60, 60), (18, 20, 26)))
        self.assertFalse(palette["light"])
        self.assertLess(palette["brightness"], 128)

    def test_unreadable_image_gives_empty_palette(self):
        self.assertEqual({}, ai_css.cover_palette(b"not an image"))
        self.assertEqual([], ai_css._palette_lines({}))             # 没有配色提示就不加那两行


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
        ai_css._last_variant = None                 # 「不撞上一次」是模块级状态，用例间要清干净
        self.addCleanup(setattr, ai_css, "_last_variant", None)

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
        # 提示词里带上了「从封面量出来的配色 / 明暗」，模型不再只能照抄当前样式
        prompt = body["messages"][1]["content"][0]["text"]
        self.assertIn("封面配色（工具从封面图里量出来的", prompt)
        self.assertIn("平均亮度", prompt)
        self.assertIn("本次配色用法", prompt)      # 每点一次换一种风格

    def test_each_request_picks_a_different_variant(self):
        """用户 2026-09-29 要求「每点一次换一种风格」：两次请求里的用法要不相同。"""
        seen = []

        def fake_post(url, headers, body):
            seen.append(body["messages"][1]["content"][0]["text"])
            return OPENAI_REPLY, ""

        with mock.patch.object(ai_css.random, "choice",
                               side_effect=[ai_css.STYLE_VARIANTS[0],
                                            ai_css.STYLE_VARIANTS[1]]), \
             mock.patch.object(ai_css, "_post", side_effect=fake_post):
            ai_css.generate_css(self._payload())
            ai_css.generate_css(self._payload())
        self.assertIn(ai_css.STYLE_VARIANTS[0], seen[0])
        self.assertIn(ai_css.STYLE_VARIANTS[1], seen[1])

    def test_retry_keeps_the_same_variant(self):
        """补问那一轮沿用同一个用法（不能两次请求变成两套风格）。"""
        prompts = []

        def fake_post(url, headers, body):
            prompts.append(body["messages"][1]["content"][0]["text"])
            if len(prompts) == 1:
                return {"choices": [{"message": {"content": '{"lyrOrig": "color: #fff;"}'}}]}, ""
            return {"choices": [{"message": {"content": '{"introLabel": "color: #fff;"}'}}]}, ""

        payload = {"colorOnly": False, "image": _data_uri((64, 64)),
                   "targets": [{"id": "introLabel", "label": "标签格", "props": ["color"]},
                               {"id": "lyrOrig", "label": "原文", "props": ["color"]}]}
        with mock.patch.object(ai_css.random, "choice",
                               return_value=ai_css.STYLE_VARIANTS[2]), \
             mock.patch.object(ai_css, "_post", side_effect=fake_post):
            result = ai_css.generate_css(json.dumps(payload))
        self.assertTrue(result["ok"])
        self.assertEqual(2, len(prompts))
        for prompt in prompts:
            self.assertIn(ai_css.STYLE_VARIANTS[2], prompt)

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

    def test_asks_again_for_targets_the_model_skipped(self):
        """模型只答了一部分 → 带着「只补这些」的要求再问一次。

        用户 2026-09-29 报：「生成 Introduction 的颜色，`|ltcolor` 没变化、还多了个 `|rstyle`」——
        就是模型只答了歌词那一项。
        """
        partial = {"choices": [{"message": {"content": '{"lyrTrans": "color: #fff;"}'}}]}
        complete = {"choices": [{"message": {"content": '{"introLabel": "color: #f2eef7;"}'}}]}
        payload = {"colorOnly": False, "image": _data_uri((64, 64)),
                   "targets": [{"id": "introLabel", "label": "标签格", "props": ["color"]},
                               {"id": "lyrTrans", "label": "译文", "props": ["color"]}]}
        with mock.patch.object(ai_css, "_post", side_effect=[(partial, ""), (complete, "")]) as post:
            result = ai_css.generate_css(json.dumps(payload))
        self.assertTrue(result["ok"])
        # 第一次答的 lyrTrans + 补问拿到的 introLabel，两个都在；模型没乱塞别的 id
        self.assertEqual(["introLabel", "lyrTrans"], sorted(result["css"]))
        self.assertEqual([], result["missing"])
        self.assertEqual(2, post.call_count)
        retry_prompt = post.call_args_list[1][0][2]["messages"][1]["content"][0]["text"]
        self.assertIn("introLabel", retry_prompt)
        self.assertNotIn("lyrTrans", retry_prompt)                # 补问只要漏掉的那些

    def test_missing_targets_are_reported(self):
        """补问也拿不到的对象要在结果里报出来（界面据此提醒「那几项没变」）。"""
        reply = {"choices": [{"message": {"content": '{"lyrTrans": "color: #fff;"}'}}]}
        payload = {"colorOnly": False, "image": _data_uri((64, 64)),
                   "targets": [{"id": "introLabel", "label": "标签格", "props": ["color"]},
                               {"id": "lyrTrans", "label": "译文", "props": ["color"]}]}
        with mock.patch.object(ai_css, "_post", return_value=(reply, "")):
            result = ai_css.generate_css(json.dumps(payload))
        self.assertTrue(result["ok"])
        self.assertEqual(["introLabel"], result["missing"])


class PostSessionTest(TestCase):
    """发给 AI 服务商的请求**不**带工具自己的 UA（用户 2026-09 特意要求）。

    「自报家门」那条规矩是给抓站用的（`utils/helpers.http_get`）；AI 接口是密钥鉴权的，
    跟「我们是谁」无关，没必要把工具身份写进第三方日志，所以走 requests 默认的 UA。
    """

    def test_request_carries_no_tool_user_agent(self):
        session = mock.Mock()
        session.headers = {}                     # 只关心有没有被塞 UA
        session.proxies = {}
        session.post.return_value = mock.Mock(status_code=200, json=lambda: {"ok": 1})
        with mock.patch.object(ai_css.requests, "Session", return_value=session), \
             mock.patch.object(ai_css, "get_config") as config:
            config.return_value.proxies = None
            data, error = ai_css._post("https://api.example.com/v1/chat/completions",
                                       {"Authorization": "Bearer k"}, {"model": "m"})
        self.assertEqual("", error)
        self.assertEqual({"ok": 1}, data)
        self.assertEqual({}, session.headers)
        self.assertEqual({}, session.proxies)
        session.post.assert_called_once_with("https://api.example.com/v1/chat/completions",
                                             headers={"Authorization": "Bearer k"},
                                             json={"model": "m"}, timeout=ai_css.TIMEOUT)

    def test_proxies_still_applied(self):
        session = mock.Mock()
        session.headers = {}
        session.proxies = {}
        session.post.return_value = mock.Mock(status_code=204, json=lambda: {})
        with mock.patch.object(ai_css.requests, "Session", return_value=session), \
             mock.patch.object(ai_css, "get_config") as config:
            config.return_value.proxies = "http://127.0.0.1:7890"
            ai_css._post("https://api.example.com/v1/chat/completions", {}, {})
        self.assertEqual({"https": "http://127.0.0.1:7890", "http": "http://127.0.0.1:7890"},
                         session.proxies)

