"""utils/lyrics_colors.py 的测试（不联网）：颜色表解析 + charas/colors 生成 + 行首标记。

口径参考 voca.wiki 的真实条目（{{LyricsKai/colors/hover}}）：
    |colors= #72A6C0; …; lg(left, #72A6C0, #827595); co(#72A6C0, …)
    |charas= 宮舞モカ；…；宮舞モカ+Ryo(@nolink)；合唱(@nolink)
    |original=
    @1ああ　またダメだったな
    @6未来は@1誰も知らない          ← 同一行切几段、每段各选一个颜色
"""
from unittest import TestCase
from unittest import mock

from utils import lyrics_colors
from utils.lyrics_colors import CharasPlan, build_colors_params, build_plan, color_lookup

MODULE_TEXT = """local module = {}

local colors = {
        -- 在下方添加内容，{颜色，歌姬名，其他名称···}
    {'#ed6772','Mai'},
    {'#EB6238','Ryo'},
    {'#72A6C0','宫舞茉歌','宮舞モカ'},
    {'#48929b','弗里摩侠','Frimomen','フリモメン'},
    {'#a1d6b7','花隈千冬'},
    {'#d93a49','重音Teto','重音テト','teto'},
    {'#777777','合唱或真人','合唱','真人'}
}

local defaultColor = '#333333'
return module
"""

TABLE = color_lookup(lyrics_colors.parse_module(MODULE_TEXT))
SINGERS = ["宮舞モカ", "Ryo", "Mai", "フリモメン", "花隈千冬", "重音テト"]


class ParseModuleTest(TestCase):
    def test_reads_color_and_names(self):
        entries = lyrics_colors.parse_module(MODULE_TEXT)
        self.assertEqual(("#ed6772", ["Mai"]), entries[0])
        self.assertEqual(("#72A6C0", ["宫舞茉歌", "宮舞モカ"]), entries[2])
        self.assertEqual(("#d93a49", ["重音Teto", "重音テト", "teto"]), entries[5])

    def test_ignores_comments_and_other_lines(self):
        entries = lyrics_colors.parse_module(MODULE_TEXT)
        self.assertEqual(7, len(entries))
        self.assertNotIn("defaultColor", [color for color, _ in entries])

    def test_empty(self):
        self.assertEqual([], lyrics_colors.parse_module(""))
        self.assertEqual([], lyrics_colors.parse_module("local module = {}"))


class ColorLookupTest(TestCase):
    def test_normalizes_name(self):
        self.assertEqual("#72A6C0", TABLE[lyrics_colors.normalize("宮舞モカ")])
        self.assertEqual("#ed6772", TABLE[lyrics_colors.normalize("mai")])   # 大小写无关
        self.assertEqual("#333333", lyrics_colors.color_of("不存在的人", TABLE))

    def test_color_of_uses_alias_and_default(self):
        self.assertEqual("#72A6C0", lyrics_colors.color_of("宮舞モカ", TABLE))
        self.assertEqual("#d93a49", lyrics_colors.color_of("重音テト", TABLE))
        self.assertEqual("#EB6238", lyrics_colors.color_of("ryo", TABLE))       # 大小写无关
        self.assertEqual("#333333", lyrics_colors.color_of("某人", TABLE))

    def test_fetch_colors_uses_wiki_api(self):
        response = mock.Mock()
        response.json.return_value = {"query": {"pages": {"1": {
            "title": "Module:Vocalist_Colors",
            "revisions": [{"slots": {"main": {"content": MODULE_TEXT}}}]}}}}
        session = mock.Mock()
        session.get.return_value = response
        with mock.patch.object(lyrics_colors, "_colors_cache", None), \
             mock.patch("utils.login.get_api_session", return_value=session), \
             mock.patch("utils.login.api_url", return_value="https://voca.wiki/api.php"):
            table = lyrics_colors.fetch_colors(refresh=True)
        self.assertEqual("#72A6C0", table["宮舞モカ"])
        self.assertIn("Module:Vocalist_Colors", session.get.call_args.kwargs["params"]["titles"])

    def test_fetch_colors_retries_then_gives_up(self):
        with mock.patch.object(lyrics_colors, "_colors_cache", None), \
             mock.patch.object(lyrics_colors, "time") as fake_time, \
             mock.patch("utils.login.get_api_session", side_effect=RuntimeError("boom")):
            self.assertEqual({}, lyrics_colors.fetch_colors(refresh=True))
        self.assertEqual(lyrics_colors.RETRY_TIMES, fake_time.sleep.call_count)


class BuildPlanTest(TestCase):
    def test_single_singer_per_line(self):
        plan = build_plan(SINGERS, {"0": ["宮舞モカ"], "3": ["重音テト"]}, TABLE)
        self.assertEqual(SINGERS, plan.charas)
        self.assertEqual(["#72A6C0", "#EB6238", "#ed6772", "#48929b", "#a1d6b7", "#d93a49"],
                         plan.colors)
        self.assertEqual({0: [1], 3: [6]}, plan.line_index)

    def test_pair_creates_gradient_entry(self):
        plan = build_plan(SINGERS, {"1": ["Ryo", "宮舞モカ"]}, TABLE)
        self.assertEqual(SINGERS + ["宮舞モカ+Ryo(@nolink)"], plan.charas)
        self.assertEqual("lg(left, #72A6C0, #EB6238)", plan.colors[-1])
        self.assertEqual({1: [7]}, plan.line_index)

    def test_all_singers_become_chorus(self):
        plan = build_plan(SINGERS, {"2": list(SINGERS)}, TABLE)
        self.assertEqual(SINGERS + ["合唱(@nolink)"], plan.charas)
        self.assertEqual("co(#72A6C0, #EB6238, #ed6772, #48929b, #a1d6b7, #d93a49)",
                         plan.colors[-1])
        self.assertEqual({2: [7]}, plan.line_index)

    def test_chorus_color_excludes_combo_gradients(self):
        # 同一行先出现组合、后出现合唱时，co() 里不能混进组合的 lg(...)
        plan = build_plan(SINGERS, {"0": ["宮舞モカ", "Ryo"], "1": list(SINGERS)}, TABLE)
        self.assertEqual("co(#72A6C0, #EB6238, #ed6772, #48929b, #a1d6b7, #d93a49)",
                         plan.colors[-1])
        self.assertEqual({0: [7], 1: [8]}, plan.line_index)

    def test_partial_selection_is_not_chorus(self):
        plan = build_plan(["A", "B", "C"], {"0": ["A", "B"]},
                          {"A": "#111111", "B": "#222222", "C": "#333333"})
        self.assertEqual("lg(left, #111111, #222222)", plan.colors[-1])
        self.assertEqual(["A", "B", "C", "A+B(@nolink)"], plan.charas)

    def test_reuses_same_combo_entry(self):
        plan = build_plan(SINGERS, {"0": ["宮舞モカ", "Ryo"], "5": ["Ryo", "宮舞モカ"]}, TABLE)
        self.assertEqual(7, len(plan.charas))                 # 只加一项组合
        self.assertEqual({0: [7], 5: [7]}, plan.line_index)

    def test_unknown_singers_are_ignored(self):
        plan = build_plan(SINGERS, {"0": ["路人甲"], "1": ["宮舞モカ", "路人乙"]}, TABLE)
        self.assertEqual({1: [1]}, plan.line_index)
        self.assertEqual(SINGERS, plan.charas)

    def test_no_marks_keeps_only_singers(self):
        plan = build_plan(SINGERS, None, TABLE)
        self.assertEqual(SINGERS, plan.charas)
        self.assertEqual({}, plan.line_index)
        self.assertTrue(plan.available)

    def test_no_singers(self):
        plan = build_plan([], {"0": ["A"]}, TABLE)
        self.assertFalse(plan.available)
        self.assertEqual("", build_colors_params([], {"0": ["A"]}, TABLE)[1])

    def test_dedupes_and_drops_empty_names(self):
        plan = build_plan(["A", "A", "", "  ", "B"], None, {"A": "#111111", "B": "#222222"})
        self.assertEqual(["A", "B"], plan.charas)

    def test_bad_line_keys_are_skipped(self):
        plan = build_plan(["A"], {"x": ["A"], "0": ["A"]}, {"A": "#111111"})
        self.assertEqual({0: [1]}, plan.line_index)

    def test_params_block(self):
        plan, params = build_colors_params(["A", "B"], {"0": ["A"]}, {"A": "#111111", "B": "#222222"})
        self.assertIn("|colors= #111111; #222222\n", params)
        self.assertIn("|charas= A；B\n", params)
        self.assertIn("|traColors= on\n", params)
        self.assertIn("|charaBlock= on\n", params)
        self.assertEqual(1, params.count("|colors="))


class InlineSegmentTest(TestCase):
    """行内分段：一行切几刀、每段各选各的（@6未来は@1誰も知らない 这种写法）。"""

    def test_segment_gets_its_own_color(self):
        plan = build_plan(SINGERS, {"0": [["宮舞モカ"], ["Ryo"]]}, TABLE)
        self.assertEqual({0: [1, 2]}, plan.line_index)
        self.assertEqual(SINGERS, plan.charas)               # 单人段复用歌姬本人的项

    def test_segment_without_pick_writes_no_number(self):
        plan = build_plan(SINGERS, {"0": [["宮舞モカ"], []]}, TABLE)
        self.assertEqual({0: [1, None]}, plan.line_index)

    def test_line_without_any_pick_is_skipped(self):
        plan = build_plan(SINGERS, {"0": [[], ["路人"]]}, TABLE)
        self.assertEqual({}, plan.line_index)

    def test_combo_and_chorus_inside_one_line(self):
        plan = build_plan(SINGERS, {"0": [["宮舞モカ", "Ryo"], list(SINGERS)]}, TABLE)
        self.assertEqual({0: [7, 8]}, plan.line_index)
        self.assertEqual(["宮舞モカ+Ryo(@nolink)", "合唱(@nolink)"], plan.charas[-2:])

    def test_cuts_are_stored_per_track(self):
        plan = build_plan(SINGERS, {"0": [["宮舞モカ"], ["Ryo"]]}, TABLE,
                          splits={"0": {"jap": [2], "chs": [2]}})
        self.assertEqual({0: {"jap": [2], "chs": [2]}}, plan.line_cuts)

    def test_plain_list_splits_mean_japanese(self):
        plan = build_plan(SINGERS, {"0": [["宮舞モカ"], ["Ryo"]]}, TABLE, splits={"0": [1]})
        self.assertEqual({0: {"jap": [1]}}, plan.line_cuts)

    def test_bad_splits_are_dropped(self):
        marks = {"0": [["A"], ["B"]]}
        table = {"A": "#111111", "B": "#222222"}
        plan = build_plan(["A", "B"], marks, table, splits={"0": {"jap": ["x", 1, 1]}})
        self.assertEqual({0: {"jap": [1]}}, plan.line_cuts)
        self.assertEqual({}, build_plan(["A", "B"], marks, table,
                                        splits={"0": {"jap": []}}).line_cuts)
        self.assertEqual({}, build_plan(["A", "B"], marks, table, splits={"9": [1]}).line_cuts)

    def test_flat_marks_still_work_with_splits(self):
        plan = build_plan(SINGERS, {"0": ["宮舞モカ"]}, TABLE, splits={"0": {"jap": [1]}})
        self.assertEqual({0: [1]}, plan.line_index)
        self.assertEqual({0: {"jap": [1]}}, plan.line_cuts)


class MarkLinesTest(TestCase):
    def test_marks_matching_lines(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"], line_index={0: [1], 3: [2]})
        self.assertEqual("@1あ\nい\n\n@2え", lyrics_colors.mark_lines("あ\nい\n\nえ", plan))

    def test_skips_blank_and_no_hover_lines(self):
        plan = CharasPlan(charas=["A"], colors=["#1"], line_index={0: [1], 1: [1]})
        self.assertEqual("@1あ\n#NoHover\nう", lyrics_colors.mark_lines("あ\n#NoHover\nう", plan))

    def test_without_marks_returns_text(self):
        plan = CharasPlan()
        self.assertEqual("あ\nい", lyrics_colors.mark_lines("あ\nい", plan))
        self.assertEqual("", lyrics_colors.mark_lines("", plan))

    def test_splits_one_line_into_segments(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"],
                          line_index={0: [6, 1]}, line_cuts={0: {"jap": [2]}})
        self.assertEqual("@6未来@1は誰も知らない",
                         lyrics_colors.mark_lines("未来は誰も知らない", plan))

    def test_line_without_cuts_uses_first_pick_only(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"], line_index={0: [6, 1]})
        self.assertEqual("@6未来は誰も知らない",
                         lyrics_colors.mark_lines("未来は誰も知らない", plan))

    def test_each_track_uses_its_own_cuts(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"], line_index={0: [6, 1]},
                          line_cuts={0: {"jap": [2], "chs": [2]}})
        self.assertEqual("@6未来@1は誰も知らない",
                         lyrics_colors.mark_lines("未来は誰も知らない", plan, "jap"))
        self.assertEqual("@6未來@1無人知曉",
                         lyrics_colors.mark_lines("未來無人知曉", plan, "chs"))

    def test_column_without_cuts_uses_first_color_only(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"], line_index={0: [6, 1]},
                          line_cuts={0: {"jap": [2]}})
        self.assertEqual("@6未來無人知曉", lyrics_colors.mark_lines("未來無人知曉", plan, "chs"))

    def test_segment_without_number_keeps_previous_color(self):
        plan = CharasPlan(charas=["A"], colors=["#1"], line_index={0: [6, None]},
                          line_cuts={0: {"jap": [2]}})
        self.assertEqual("@6未来は誰も知らない", lyrics_colors.mark_lines("未来は誰も知らない", plan))

    def test_out_of_range_cuts_are_ignored(self):
        plan = CharasPlan(charas=["A", "B"], colors=["#1", "#2"], line_index={0: [1, 2]},
                          line_cuts={0: {"jap": [0, 99]}})
        self.assertEqual("@1あい", lyrics_colors.mark_lines("あい", plan))

    def test_more_segments_than_text_are_ignored(self):
        plan = CharasPlan(charas=["A", "B", "C"], colors=["#1", "#2", "#3"],
                          line_index={0: [1, 2, 3]}, line_cuts={0: {"jap": [1]}})
        self.assertEqual("@1あ@2い", lyrics_colors.mark_lines("あい", plan))
