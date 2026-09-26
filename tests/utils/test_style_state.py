"""utils/ui/style_state.py 的单测：状态 → CSS 声明 → wikitext 参数，以及反向解析。"""
from unittest import TestCase

from utils.ui import style_state as st


class ColorTest(TestCase):
    def test_norm_hex(self):
        self.assertEqual("#aabbcc", st.norm_hex("#AABBCC"))
        self.assertEqual("#aabbcc", st.norm_hex("abc"))
        self.assertIsNone(st.norm_hex("红色"))
        self.assertIsNone(st.norm_hex(""))

    def test_css_color(self):
        self.assertEqual("#39c5bb", st.css_color("#39c5bb", 1))
        self.assertEqual("transparent", st.css_color("#39c5bb", 0))
        self.assertEqual("rgba(57, 197, 187, 0.50)", st.css_color("#39c5bb", 0.5))

    def test_parse_color(self):
        self.assertEqual(("#ff0000", 1.0), st.parse_color("#f00"))
        self.assertEqual(("#ffffff", 0.0), st.parse_color("transparent"))
        self.assertEqual(("#336699", 0.5), st.parse_color("rgba(51, 102, 153, 0.5)"))
        self.assertEqual(("#005500", 1.0), st.parse_color("rgb(0, 85, 0)"))

    def test_auto_text_color_switches_at_threshold(self):
        self.assertEqual(st.DEFAULT_FG, st.auto_text_color("#ffffff", 60))
        self.assertEqual("#ffffff", st.auto_text_color("#000000", 60))
        # 阈值拉到 100 时纯白底也刚好落在「亮」这一侧（>= 阈值 判黑字）
        self.assertEqual(st.DEFAULT_FG, st.auto_text_color("#ffffff", 100))
        self.assertEqual("#ffffff", st.auto_text_color("#808080", 60))


class DeclsTest(TestCase):
    def test_default_state_writes_no_wiki_decls(self):
        self.assertEqual([], st.wiki_decls(st.blank_state()))

    def test_background_goes_first_and_bare(self):
        state = st.blank_state()
        state["color"] = "#ff0000"
        state["bgSolid"] = "#123456"
        decls = st.wiki_decls(state)
        self.assertEqual("background", decls[0][0])
        self.assertEqual("#123456", decls[0][1])
        self.assertIn(("color", "#ff0000"), decls)

    def test_force_keeps_default_values(self):
        state = st.blank_state()
        state["force"] = {"font-size"}
        decls = dict(st.delta_decls(state))
        self.assertIn("font-size", decls)
        self.assertEqual("12px", decls["font-size"])

    def test_gradient_layer_is_written_into_background(self):
        state = st.blank_state()
        state["bgLayers"] = [st.default_layer()]
        value = st.background_value(state)
        self.assertIn("linear-gradient(135deg, #39c5bb 0%, #6fe3da 100%)", value)
        self.assertTrue(value.endswith("#39c5bb"))

    def test_gradient_hard_edge_duplicates_stops(self):
        layer = st.default_layer()
        layer["hard"] = True
        layer["stops"] = [{"color": "#000000", "alpha": 1.0, "pos": 0, "aa": False},
                          {"color": "#ffffff", "alpha": 1.0, "pos": 100, "aa": False}]
        css = st.gradient_css(layer)
        self.assertEqual("linear-gradient(135deg, #000000 0%, #000000 100%, #ffffff 100%)", css)

    def test_antialias_stop_uses_calc(self):
        layer = st.default_layer()
        layer["stops"][0]["aa"] = True
        self.assertIn("calc(0% - 1px)", st.gradient_css(layer))

    def test_radial_and_conic(self):
        radial = st.default_layer("radial")
        self.assertTrue(st.gradient_css(radial).startswith("radial-gradient(circle at 50% 50%"))
        conic = st.default_layer("conic")
        self.assertTrue(st.gradient_css(conic).startswith("conic-gradient(from 135deg"))

    def test_shadows(self):
        state = st.blank_state()
        state["boxShadows"] = [st.default_box_shadow()]
        state["textShadows"] = [st.default_text_shadow()]
        decls = dict(st.build_decls(state))
        self.assertEqual("0px 2px 6px 0px rgba(15, 23, 42, 0.25)", decls["box-shadow"])
        self.assertEqual("0px 1px 2px rgba(0, 0, 0, 0.50)", decls["text-shadow"])

    def test_border_current_color(self):
        state = st.blank_state()
        self.assertEqual("2px solid currentColor", dict(st.build_decls(state))["border"])
        state["borderCurrent"] = False
        state["borderColor"] = "#ff0000"
        self.assertEqual("2px solid #ff0000", dict(st.build_decls(state))["border"])
        state["borderWidth"] = 0
        self.assertEqual("0", dict(st.build_decls(state))["border"])

    def test_extras_are_appended(self):
        state = st.blank_state()
        state["extras"] = ["transform: rotate(-2deg)"]
        decls = st.build_decls(state)
        self.assertEqual(("transform", "rotate(-2deg)"), decls[-1])


class WikiTextTest(TestCase):
    def test_songbox_always_writes_three_blocks(self):
        text = st.songbox_wiki_text([st.blank_state() for _ in range(3)])
        self.assertEqual(["|颜色1 = ", "|颜色2 = ", "|颜色3 = "], text.split("\n"))

    def test_songbox_block_format(self):
        state = st.blank_state()
        state["bgSolid"] = "#1e90ff"
        state["color"] = "#ffffff"
        text = st.songbox_wiki_text([state, st.blank_state(), st.blank_state()])
        lines = text.split("\n")
        self.assertEqual("|颜色1 = #1e90ff;", lines[0])
        self.assertEqual("  color: #ffffff;", lines[1])
        self.assertEqual("|颜色2 = ", lines[2])

    def test_template_params_order_and_defaults(self):
        text = st.tpl_wiki_text(st.tpl_default_states())
        self.assertEqual(["|lbgcolor = #000000", "|ltcolor = #ffffff"], text.split("\n"))

    def test_rbdcolor_only_when_label_has_extras(self):
        states = st.tpl_default_states()
        self.assertNotIn("rbdcolor", st.tpl_wiki_text(states))
        states["introLabel"]["extras"] = ["border-radius: 6px"]
        text = st.tpl_wiki_text(states)
        self.assertIn("|lbgcolor = #000000; border-radius: 6px", text)
        self.assertIn("|rbdcolor = #000000", text)

    def test_label_gradient_uses_background_image(self):
        states = st.tpl_default_states()
        states["introLabel"]["bgLayers"] = [st.default_layer()]
        text = st.tpl_wiki_text(states)
        self.assertIn("background-image: linear-gradient(", text)

    def test_lyrics_lines_follow_output_switch(self):
        states = st.tpl_default_states()
        self.assertNotIn("|lstyle", st.tpl_wiki_text(states))
        states["lyrOrig"]["enabled"] = True
        states["lyrOrig"]["color"] = "#ff0000"
        self.assertIn("|lstyle = color: #ff0000;", st.tpl_wiki_text(states))

    def test_lyrics_background_only_when_set(self):
        states = st.tpl_default_states()
        states["lyrContainer"]["enabled"] = True
        self.assertNotIn("|containerstyle = background", st.tpl_wiki_text(states))
        states["lyrContainer"]["bgSet"] = True
        self.assertIn("background: #ffffff;", st.tpl_wiki_text(states))

    def test_full_text_puts_songbox_before_templates(self):
        text = st.full_wiki_text([st.blank_state() for _ in range(3)], st.tpl_default_states())
        self.assertTrue(text.startswith("|颜色1 = "))
        self.assertIn("|lbgcolor = #000000", text)
        self.assertLess(text.index("|颜色3"), text.index("|lbgcolor"))


class ParseTest(TestCase):
    def test_round_trip_songbox(self):
        state = st.blank_state()
        state["bgSolid"] = "#1e90ff"
        state["color"] = "#ffffff"
        state["radius"] = 18
        text = st.full_wiki_text([state, st.blank_state(), st.blank_state()],
                                 st.tpl_default_states())
        states, _templates = st.parse_wiki_text(text)
        self.assertEqual("#1e90ff", states[0]["bgSolid"])
        self.assertEqual("#ffffff", states[0]["color"])
        self.assertEqual(18, states[0]["radius"])
        self.assertTrue(states[0]["bgSet"])
        # 重新生成应当得到同一份文本（解析 → 生成 是稳定映射）
        self.assertEqual(text, st.full_wiki_text(states, st.tpl_default_states()))

    def test_round_trip_template_params(self):
        templates = st.tpl_default_states()
        templates["lyrOrig"]["enabled"] = True
        templates["lyrOrig"]["color"] = "#123456"
        templates["lyrContainer"]["enabled"] = True
        templates["lyrContainer"]["bgSet"] = True
        templates["lyrContainer"]["bgSolid"] = "#fafafa"
        text = st.tpl_wiki_text(templates)
        _states, parsed = st.parse_wiki_text(text)
        self.assertTrue(parsed["lyrOrig"]["enabled"])
        self.assertEqual("#123456", parsed["lyrOrig"]["color"])
        self.assertTrue(parsed["lyrContainer"]["enabled"])
        self.assertEqual("#fafafa", parsed["lyrContainer"]["bgSolid"])
        self.assertEqual(text, st.tpl_wiki_text(parsed))

    def test_round_trip_gradient_and_shadow(self):
        state = st.blank_state()
        state["bgLayers"] = [st.default_layer()]
        state["boxShadows"] = [st.default_box_shadow()]
        text = st.songbox_wiki_text([state, st.blank_state(), st.blank_state()])
        states, _templates = st.parse_wiki_text(text)
        self.assertEqual(1, len(states[0]["bgLayers"]))
        self.assertEqual("linear", states[0]["bgLayers"][0]["kind"])
        self.assertEqual(100, states[0]["bgLayers"][0]["stops"][-1]["pos"])
        self.assertEqual(1, len(states[0]["boxShadows"]))
        self.assertEqual(6, states[0]["boxShadows"][0]["blur"])

    def test_unknown_declarations_land_in_extras(self):
        states, _ = st.parse_wiki_text("|颜色1 = color: #ff0000; filter: blur(2px);")
        self.assertIn("filter: blur(2px)", states[0]["extras"])

    def test_parse_code_css_ignores_selector_and_comments(self):
        decls = st.parse_code_css("/* 演唱 */\n.tag-1 {\n  color: #ff0000;\n  width: 60%;\n}")
        self.assertEqual([("color", "#ff0000"), ("width", "60%")], decls)

    def test_split_params_keeps_indented_continuation(self):
        params = st.split_params("|颜色1 = #123456;\n  color: #ffffff;\n|ltcolor = #000000")
        self.assertEqual("#123456;\ncolor: #ffffff;", params["颜色1"])
        self.assertEqual("#000000", params["ltcolor"])


class AiTest(TestCase):
    def test_apply_ai_css_writes_state_and_marks_force(self):
        states = [st.blank_state() for _ in range(3)]
        templates = st.tpl_default_states()
        st.apply_ai_css("pill0", "color: #ffffff; font-size: 12px;", states, templates)
        self.assertEqual("#ffffff", states[0]["color"])
        self.assertIn("font-size", states[0]["force"])
        # 只有被点到的那个矩形被改
        self.assertEqual(st.blank_state()["color"], states[1]["color"])
        # force 让「等于默认值」的属性也能写出去
        self.assertIn(("font-size", "12px"), st.delta_decls(states[0]))

    def test_apply_ai_css_global_hits_all_pills(self):
        states = [st.blank_state() for _ in range(3)]
        st.apply_ai_css("songboxGlobal", "background-color: #222222;", states,
                        st.tpl_default_states())
        self.assertEqual(["#222222"] * 3, [state["bgSolid"] for state in states])

    def test_apply_ai_css_turns_template_output_on(self):
        templates = st.tpl_default_states()
        st.apply_ai_css("lyrOrig", "color: #ff0000;", [st.blank_state()], templates)
        self.assertTrue(templates["lyrOrig"]["enabled"])

    def test_targets_list_carries_props_and_current(self):
        targets = st.ai_payload_targets([st.blank_state() for _ in range(3)], 1,
                                        st.tpl_default_states(), color_only=True)
        self.assertEqual("pill1", targets[0]["id"])
        self.assertIn("color", targets[0]["props"])
        self.assertNotIn("font-size", targets[0]["props"])
        self.assertIn("color:", targets[0]["current"])

    def test_all_targets_cover_every_object(self):
        targets = st.ai_all_targets([st.blank_state() for _ in range(3)],
                                    st.tpl_default_states(), color_only=False)
        self.assertEqual(["songboxGlobal", "introLabel", "lyrContainer", "lyrOrig", "lyrTrans"],
                         [item["id"] for item in targets])
