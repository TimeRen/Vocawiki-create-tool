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

    def test_label_writes_box_and_text_declarations(self):
        """标签格不只写两个颜色：padding / border / 圆角 / 阴影 跟 lbgcolor、字号字重跟 ltcolor。

        用户 2026-09 报「AI 生成 Introduction 的颜色毫无变化」：AI 给的 CSS 里除了颜色
        还有 padding / border-radius / border / font-size / font-weight / box-shadow，
        旧实现只把两个颜色写出去，其余全丢。
        """
        states = st.tpl_default_states()
        intro = states["introLabel"]
        intro.update(bgSolid="#2b2a3a", color="#e8e4f0", padX=14, padY=6, radius=6,
                     fontSize=14, weight=600, borderWidth=1, borderStyle="solid",
                     borderCurrent=False, borderColor="#8a7fa8",
                     boxShadows=[{"x": 0, "y": 2, "blur": 8, "spread": 0,
                                  "color": "#141222", "alpha": 0.6, "inset": False}])
        lines = st.tpl_wiki_text(states).split("\n")
        self.assertEqual(
            "|lbgcolor = #2b2a3a; padding: 6px 14px; border: 1px solid #8a7fa8; "
            "border-radius: 6px; box-shadow: 0px 2px 8px 0px rgba(20, 18, 34, 0.60)",
            lines[0])
        self.assertEqual("|ltcolor = #e8e4f0; font-size: 14px; font-weight: 600", lines[1])
        self.assertEqual("|rbdcolor = #2b2a3a", lines[2])

    def test_label_keeps_hand_written_extra_declarations(self):
        """用户手写的「额外声明」照旧跟在 lbgcolor 后面。"""
        states = st.tpl_default_states()
        states["introLabel"].update(bgSolid="#4a3e4d", extras=["opacity: 0.8"])
        self.assertEqual("|lbgcolor = #4a3e4d; opacity: 0.8",
                         st.tpl_wiki_text(states).split("\n")[0])

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

    def test_label_line_survives_a_round_trip(self):
        """用户手写的那行标签格参数：读回来再写出去不能把声明弄丢。"""
        line = ("|lbgcolor = #4a3e4d; padding: 6px 12px; border-radius: 4px 0 0 4px; "
                "box-shadow: 2px 2px 5px rgba(0,0,0,0.2);\n|ltcolor = #ffffff; font-weight: bold")
        _states, parsed = st.parse_wiki_text(line)
        intro = parsed["introLabel"]
        self.assertEqual("#4a3e4d", intro["bgSolid"])
        self.assertEqual(6, intro["padY"])
        self.assertEqual("#ffffff", intro["color"])
        self.assertEqual(700, intro["weight"])          # bold → 700
        out = st.tpl_wiki_text(parsed)
        self.assertIn("padding: 6px 12px", out)
        self.assertIn("border-radius: 4px 0 0 4px", out)   # 四个角分开写 → 原样保留
        self.assertNotIn("border-radius: 10px", out)       # 不能既给默认单值又给手写四值
        self.assertIn("box-shadow: 2px 2px 5px 0px rgba(0, 0, 0, 0.20)", out)
        self.assertIn("font-weight: 700", out)
        self.assertIn("|rbdcolor = #4a3e4d", out)

    def test_spaced_rgba_is_kept_in_shadows_and_borders(self):
        """`rgba(20, 18, 34, 0.6)` 里有空格：按空白切会把颜色切成碎片，变成白色的阴影 / 边框。"""
        intro = st.tpl_default_states()["introLabel"]
        st.apply_decls(intro, st.parse_decl_text(
            "box-shadow: 0 2px 8px rgba(20, 18, 34, 0.6); border: 1px solid rgba(20, 18, 34, 0.6)"))
        self.assertEqual({"x": 0.0, "y": 2.0, "blur": 8.0, "spread": 0.0,
                          "color": "#141222", "alpha": 0.6, "inset": False},
                         intro["boxShadows"][0])
        self.assertEqual(("#141222", 0.6), (intro["borderColor"], intro["borderAlpha"]))
        shadows = st.parse_text_shadows("0 1px 2px rgba(10, 20, 30, 0.5)")
        self.assertEqual(("#0a141e", 0.5), (shadows[0]["color"], shadows[0]["alpha"]))

    def test_multi_value_border_radius_goes_to_extras(self):
        """四值圆角状态里装不下，原样留进 extras（不然会被简写成一个角）。"""
        intro = st.tpl_default_states()["introLabel"]
        st.apply_decls(intro, [("border-radius", "4px 0 0 4px")])
        self.assertEqual(["border-radius: 4px 0 0 4px"], intro["extras"])
        st.apply_decls(intro, [("border-radius", "6px")])
        self.assertEqual(6, intro["radius"])

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

    def test_template_target_by_name(self):
        """界面上的当前对象可能是**模板名**（Introduction / 歌词）。

        以前这里直接拿它和 0 比大小 → `TypeError: '>=' not supported between instances of
        'str' and 'int'`，也就是用户报的「Songbox 生成完、准备给 Introduction 生成时界面报错」。
        """
        targets = st.ai_payload_targets([st.blank_state() for _ in range(3)], "introLabel",
                                        st.tpl_default_states(), color_only=False)
        self.assertEqual("introLabel", targets[0]["id"])
        self.assertEqual(st.TPL_TARGETS["introLabel"]["label"], targets[0]["label"])
        self.assertIn("font-size", targets[0]["props"])
        self.assertEqual(list(st.TPL_ALLOW["lyrOrig"]),
                         st.ai_payload_targets([st.blank_state()], "lyrOrig",
                                               st.tpl_default_states(),
                                               color_only=False)[0]["props"])

    def test_template_target_honours_color_only(self):
        targets = st.ai_payload_targets([st.blank_state()], "lyrTrans", st.tpl_default_states(),
                                        color_only=True)
        self.assertIn("color", targets[0]["props"])
        self.assertNotIn("font-size", targets[0]["props"])

    def test_unknown_template_target_is_skipped(self):
        self.assertEqual([], st.ai_payload_targets([st.blank_state()], "nope",
                                                   st.tpl_default_states(), color_only=False))
