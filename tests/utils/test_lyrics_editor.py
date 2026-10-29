"""utils/lyrics_editor.py 的单元测试：分类 / 切分 / pywebview 接口。"""
import json
from unittest import TestCase
from unittest import mock

from utils import lyrics_editor
from utils import source_filler
from utils.lyrics_editor import (LyricsApi, align_chs_lines, classify_by_script, classify_stanza,
                                 extract_chs_by_jap, guess_layout, is_chinese_only,
                                 is_english_line, merge_chs_blocks, merge_chs_lines,
                                 mirror_english_lines,
                                 normalize_blank_lines, normalize_no_hover,
                                 process_lyrics_jap, process_translation, split_paired_lines)


class ProcessLyricsJapTest(TestCase):
    """日语歌词预处理：换行过多时重新分段（原在 utils/string.py，现并入本模块）。"""

    SONG = """翼広げ、どこか遠く、

ひとりという名の鳥になり飛んで

いけたらいいな、そこには見たことない

きれいなものがあるの"""

    def test_keeps_normal_paragraphs(self):
        self.assertEqual("\n".join(self.SONG.split("\n\n")), process_lyrics_jap(self.SONG))

    def test_collapses_excessive_newlines(self):
        text = "\n\n\n\n\n\n".join(["ABC\n\n\nDEF\n\n\n\nGHI\n\n\n\nJKL" for _ in range(3)])
        expected = "\n\n".join(["ABC\nDEF\nGHI\nJKL" for _ in range(3)])
        self.assertEqual(expected, process_lyrics_jap(text))

    def test_drops_carriage_returns(self):
        self.assertEqual("a\nb", process_lyrics_jap("a\r\nb"))

    def test_empty_returns_empty(self):
        self.assertEqual("", process_lyrics_jap(""))
        self.assertEqual("", process_lyrics_jap("   "))


class NormalizeBlankLinesTest(TestCase):
    """段落之间的连续空行只留一个（否则自动识别出来的三栏里空行会连成一片）。"""

    def test_collapses_run_to_single_blank_line(self):
        self.assertEqual("A\nB\n\nC", normalize_blank_lines("A\nB\n\n\n\nC"))

    def test_single_blank_line_is_kept(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\n\nB"))

    def test_blank_line_with_spaces_counts(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\n  \n\t\nB"))

    def test_carriage_returns_are_normalized(self):
        self.assertEqual("A\n\nB", normalize_blank_lines("A\r\n\r\n\r\nB"))

    def test_lines_are_not_merged(self):
        self.assertEqual("A\nB\nC", normalize_blank_lines("A\nB\nC"))

    def test_empty(self):
        self.assertEqual("", normalize_blank_lines(""))
        self.assertEqual("", normalize_blank_lines(None))


class NormalizeNoHoverTest(TestCase):
    """`#NoHover`（`{{LyricsKai/hover}}` 的段落标记）→ 空行。

    用户 2026-10-13 对照 voca.wiki《Equivalent》：那条目的歌词是用 `#NoHover` 分段的
    （一整块里一个空行都没有），而本工具内部一律用**空行**分段、输出时才由
    `models.song.add_no_hover()` 补标记，所以来源里的标记得先还原。
    """

    def test_marker_lines_become_blank_lines(self):
        self.assertEqual("あ\n\nい\n\nう", normalize_no_hover("あ\n#NoHover\nい\n#NoHover\nう"))

    def test_line_count_is_unchanged(self):
        """逐行替换：行号不动，界面上按行号记的演唱者标记才不会错位。"""
        text = "あ\n#NoHover\nい"
        self.assertEqual(len(text.split("\n")), len(normalize_no_hover(text).split("\n")))

    def test_tolerates_spacing_and_case(self):
        self.assertEqual("あ\n\nい", normalize_no_hover("あ\n  # nohover  \nい"))

    def test_only_marker_lines_are_touched(self):
        self.assertEqual("あ\n歌#NoHoverだ", normalize_no_hover("あ\n歌#NoHoverだ"))

    def test_is_idempotent(self):
        once = normalize_no_hover("あ\n#NoHover\nい")
        self.assertEqual(once, normalize_no_hover(once))

    def test_normalizes_carriage_returns(self):
        self.assertEqual("あ\n\nい", normalize_no_hover("あ\r\n#NoHover\r\nい"))

    def test_empty(self):
        self.assertEqual("", normalize_no_hover(""))
        self.assertEqual("", normalize_no_hover(None))


class NoHoverSourceTest(TestCase):
    """从条目里复制来的歌词（`#NoHover` 分段）→ 分栏结果要还原成空行分段。

    用户 2026-10-13 对照 voca.wiki《Equivalent》：那种歌词的段落边界全在 `#NoHover` 上。
    以前的 AI 分栏按「逐字保留原文」把标记当歌词行收下 —— 三栏里一个空行都没有
    （段落结构全丢），「自动识别并填入」还会把标记当纯 ASCII 行分进**罗马音栏**；
    非 hover 生成时标记更会原样写进条目正文（它只是模板标记，不是歌词）。
    """

    SOURCE = ("あ\nい\n#NoHover\nう\nえ\n"
              "你的\n我的\n#NoHover\n你的呢\n我的呢")
    JAP = "あ\nい\n\nう\nえ"
    CHS = "你的\n我的\n\n你的呢\n我的呢"

    def test_the_model_sees_standard_stanzas_instead_of_the_marker(self):
        seen = {}

        def fake(payload_json):
            seen.update(json.loads(payload_json))
            return {"ok": True, "jap": self.JAP, "chs": self.CHS}

        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", side_effect=fake):
            LyricsApi().ai_auto(json.dumps({"text": self.SOURCE, "jap": ""}))
        self.assertNotIn("#NoHover", seen["text"])          # 模型不必认识这个标记
        self.assertEqual("あ\nい\n\nう\nえ\n你的\n我的\n\n你的呢\n我的呢", seen["text"])

    def test_ai_auto_restores_the_stanza_breaks(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": self.JAP, "chs": self.CHS}):
            result = LyricsApi().ai_auto(json.dumps({"text": self.SOURCE}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual(self.CHS, result["chs"])

    def test_marker_echoed_by_the_model_is_cleaned_up(self):
        """模型认识这个模板、把标记抄回来时也要清掉（三栏同一位置换回空行）。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": "あ\nい\n#NoHover\nう\nえ",
                "chs": "你的\n我的\n#NoHover\n你的呢\n我的呢"}):
            result = LyricsApi().ai_auto(json.dumps({"text": self.SOURCE}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual(self.CHS, result["chs"])

    def test_auto_keeps_the_marker_out_of_the_romaji_column(self):
        result = LyricsApi().auto(json.dumps({"text": self.SOURCE}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual(self.CHS, result["chs"])
        self.assertEqual("", result["roma"])


class ProcessTranslationTest(TestCase):
    def test_takes_requested_line_of_each_group(self):
        text = "日1\n中1\n日2\n中2"
        self.assertEqual("日1\n日2", process_translation(text, 2, 1))
        self.assertEqual("中1\n中2", process_translation(text, 2, 2))

    def test_collapses_paragraph_separator(self):
        # 段间的两个空行：组首落在空行上时输出一个空行作为段落分隔
        text = "日1\n中1\n\n\n日2\n中2"
        self.assertEqual("日1\n\n日2", process_translation(text, 3, 1))

    def test_keeps_blank_lines_as_separators(self):
        # 空行落在组首时保留为空行；落在组内其他位置则不影响
        self.assertEqual("日1\n日2", process_translation("日1\n中1\n\n日2\n中2", 3, 1))

    def test_drops_group_missing_target_line(self):
        # 末尾不足一组（少了中文行）时，中文的那一路直接丢掉
        self.assertEqual("中1", process_translation("日1\n中1\n日2", 2, 2))

    def test_leading_blank_line_does_not_crash(self):
        # 旧实现会在 result 为空时访问 result[-1]，这里应安全返回
        self.assertEqual("", process_translation("\n\n日1", 2, 2))

    def test_empty_text(self):
        self.assertEqual("", process_translation("", 3, 1))


class ClassifyStanzaTest(TestCase):
    def test_kana_is_japanese(self):
        jap, chs, roma = classify_stanza(["きょうの", "今日的"])
        self.assertEqual(["きょうの"], jap)
        self.assertEqual(["今日的"], chs)
        self.assertEqual([], roma)

    def test_ascii_is_romaji(self):
        jap, chs, roma = classify_stanza(["君の名は", "kimi no na wa"])
        self.assertEqual(["君の名は"], jap)
        self.assertEqual([], chs)
        self.assertEqual(["kimi no na wa"], roma)

    def test_alternating_block(self):
        # 日语 / 中文 交替
        stanza = ["きみの", "你的", "ぼくの", "我的"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["きみの", "ぼくの"], jap)
        self.assertEqual(["你的", "我的"], chs)

    def test_alternating_block_with_romaji(self):
        # 日语 / 罗马音 / 中文 三行一组
        stanza = ["きみの", "kimi no", "你的", "ぼくの", "boku no", "我的"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["きみの", "ぼくの"], jap)
        self.assertEqual(["你的", "我的"], chs)
        self.assertEqual(["kimi no", "boku no"], roma)

    def test_block_format_japanese_first(self):
        # 块状：日语在上、纯汉字译文在下（无假名，需按块判定）
        stanza = ["君の名前", "僕の名前", "你的名字", "我的名字"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["君の名前", "僕の名前"], jap)
        self.assertEqual(["你的名字", "我的名字"], chs)

    def test_punctuation_only_lines_are_dropped(self):
        jap, chs, roma = classify_stanza(["きみの", "——", "你的"])
        self.assertEqual(["きみの"], jap)
        self.assertEqual(["你的"], chs)

    def test_an_english_line_in_the_japanese_slot_is_a_lyric(self):
        """「日/中」逐行交替时，落在日语格子上的纯拉丁行是**歌里唱的英文**，不是罗马音。

        用户 2026-10-25 报的网易云《パンドラ》：`PANDORA BOX` 有两处，被当成罗马音之后
        日语栏少两行、中文栏多两行，整栏错位。
        """
        stanza = ["きみの", "你的", "PANDORA BOX", "潘多拉魔盒", "ぼくの", "我的"]
        jap, chs, roma = classify_stanza(stanza)
        self.assertEqual(["きみの", "PANDORA BOX", "ぼくの"], jap)
        self.assertEqual(["你的", "潘多拉魔盒", "我的"], chs)
        self.assertEqual([], roma)

    def test_empty_stanza(self):
        self.assertEqual(([], [], []), classify_stanza([]))


class PairedSourceTest(TestCase):
    """「一行日文、一行中文」的来源（从链接填充网易云时的样子）。

    用户 2026-10-25 给的《パンドラ》：网易云的待整理歌词就是逐行穿插的 50 对，
    日语栏却是 VocaDB 的**合并行**原文（30 行）—— 两栏行数对不上就没法一一对应。
    """

    PAIRS = ("無理矢理に酔っ払い（トリップ）\n强人所难地踏上任性的旅程\n"
             "お手軽マジック\n随手挥出的魔法\n"
             "不細工な僕らはまた\n笨拙的我们\n"
             "9パーの愛を喰らう\n只得捡拾残缺的爱")
    MERGED_HEAD = "無理矢理に酔っ払い（トリップ）　お手軽マジック"
    MERGED_REST = "不細工な僕らはまた　9パーの愛を喰らう"

    @property
    def merged(self) -> str:
        return f"{self.MERGED_HEAD}\n{self.MERGED_REST}"

    def test_a_paired_source_splits_without_any_guessing(self):
        self.assertEqual(("無理矢理に酔っ払い（トリップ）\nお手軽マジック\n不細工な僕らはまた\n9パーの愛を喰らう",
                          "强人所难地踏上任性的旅程\n随手挥出的魔法\n笨拙的我们\n只得捡拾残缺的爱"),
                         split_paired_lines(self.PAIRS))

    def test_a_source_that_is_not_paired_is_left_alone(self):
        self.assertIsNone(split_paired_lines("きみの\nぼくの\nあなたの\nだれかの\n"
                                             "你的\n我的\n你的啊\n谁的"))          # 块状
        self.assertIsNone(split_paired_lines("きみの\n你的\nぼくの\n我的"))          # 太短
        self.assertIsNone(split_paired_lines("きみの\n你的\n\nぼくの\n我的\n"
                                             "あなたの\n你的啊"))                 # 有一段是奇数行

    def test_stanza_breaks_are_kept(self):
        """逐句配对 + 空行分段（b 站专栏那种）：现在认得出，分段原样保留。

        用户 2026-10-29 给的 opus 链接（cv41298766）正文就是「日/中」逐句、段间空一行；
        以前只要有空行就整段放弃，只能交给模型。
        """
        self.assertEqual(("きみの\n\nぼくの\nあなたの\nだれかの\nそらの",
                          "你的\n\n我的\n你的啊\n谁的\n天空的"),
                         split_paired_lines("きみの\n你的\n\nぼくの\n我的\nあなたの\n你的啊\n"
                                            "だれかの\n谁的\nそらの\n天空的"))

    def test_parenthetical_notes_do_not_break_the_pairing(self):
        """中文格里的括号注带假名（`（日本网络用语「草」（くさ）…）`）也要照认。

        用户 2026-10-29 报的《image(picco)》：网易云 28 句逐句配对，就因为有这么一句
        被判成「不是这个形状」，整段被丢给模型去做排版。
        """
        source = ("夏の葉末　浮かぶ雲の群泳\n夏叶末端 浮云群游\n"
                  "騙されて大草原\n被骗了 笑不活啦（日本网络用语「草」（くさ）来表示“笑”的意思）\n"
                  "触れる指先\n触摸到的指尖\n"
                  "遠い夜\n遥远的夜晚")
        pairs = split_paired_lines(source)
        self.assertEqual(("夏の葉末　浮かぶ雲の群泳\n騙されて大草原\n触れる指先\n遠い夜",
                          "夏叶末端 浮云群游\n"
                          "被骗了 笑不活啦（日本网络用语「草」（くさ）来表示“笑”的意思）\n"
                          "触摸到的指尖\n遥远的夜晚"),
                         pairs)

    def test_the_translation_is_merged_to_the_japanese_line_breaks(self):
        """译文按日语栏的换行合并（中间留一个空格），行数就与日语栏一致。"""
        self.assertEqual("强人所难地踏上任性的旅程 随手挥出的魔法\n笨拙的我们 只得捡拾残缺的爱",
                         merge_chs_lines(
                             "無理矢理に酔っ払い（トリップ）\nお手軽マジック\n不細工な僕らはまた\n9パーの愛を喰らう",
                             "强人所难地踏上任性的旅程\n随手挥出的魔法\n笨拙的我们\n只得捡拾残缺的爱",
                             self.merged))

    def test_merging_keeps_the_paragraph_breaks(self):
        jap = f"きみの　ぼくの\n\nあなたの　だれかの"
        chs = merge_chs_lines("きみの\nぼくの\nあなたの\nだれかの", "你的\n我的\n你的啊\n谁的", jap)
        self.assertEqual("你的 我的\n\n你的啊 谁的", chs)

    def test_another_song_is_not_merged(self):
        """日语栏与来源不是同一份歌词时返回 None（交给原来的逻辑，绝不硬凑）。"""
        self.assertIsNone(merge_chs_lines("きみの\nぼくの", "你的\n我的", "ぜんぜん違う歌詞"))
        self.assertIsNone(merge_chs_lines("きみの\nぼくの", "你的", "きみの　ぼくの"))

    def test_ai_auto_uses_the_paired_source_without_calling_the_model(self):
        api = LyricsApi()
        payload = json.dumps({"text": self.PAIRS, "jap": "", "chs": "", "roma": ""},
                             ensure_ascii=False)
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               side_effect=AssertionError("不该调用模型")) as recognize:
            result = api.ai_auto(payload)
        recognize.assert_not_called()
        self.assertEqual("paired", result["mode"])
        self.assertEqual("無理矢理に酔っ払い（トリップ）\nお手軽マジック\n不細工な僕らはまた\n9パーの愛を喰らう",
                         result["jap"])
        self.assertEqual("强人所难地踏上任性的旅程\n随手挥出的魔法\n笨拙的我们\n只得捡拾残缺的爱",
                         result["chs"])
        self.assertEqual("", result["roma"])

    def test_ai_auto_merges_the_paired_translation_to_the_japanese_column(self):
        api = LyricsApi()
        payload = json.dumps({"text": self.PAIRS, "jap": self.merged, "chs": "", "roma": ""},
                             ensure_ascii=False)
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               side_effect=AssertionError("不该调用模型")):
            result = api.ai_auto(payload)
        self.assertEqual(self.merged, result["jap"])
        self.assertEqual("强人所难地踏上任性的旅程 随手挥出的魔法\n笨拙的我们 只得捡拾残缺的爱",
                         result["chs"])

    def test_the_auto_button_also_merges(self):
        api = LyricsApi()
        payload = json.dumps({"text": self.PAIRS, "jap": self.merged, "chs": "", "roma": ""},
                             ensure_ascii=False)
        result = api.auto(payload)
        self.assertEqual(self.merged, result["jap"])
        self.assertEqual("强人所难地踏上任性的旅程 随手挥出的魔法\n笨拙的我们 只得捡拾残缺的爱",
                         result["chs"])


class ClassifyByScriptTest(TestCase):
    def test_is_chinese_only(self):
        """只有中文 → 从链接填充时直接进「中文」栏。"""
        self.assertTrue(is_chinese_only("想一眼就看穿那橙色之物吧?\n這點我跟你真是一丘之貉呢"))

    def test_is_chinese_only_rejects_mixed_text(self):
        self.assertFalse(is_chinese_only("きみの名前を呼ぶ\n你的名字"))
        self.assertFalse(is_chinese_only("きみの\n你的"))

    def test_is_chinese_only_ignores_roma_and_blank_lines(self):
        self.assertTrue(is_chinese_only("\n你的名字\n\nkimi no\n"))
        self.assertFalse(is_chinese_only(""))
        self.assertFalse(is_chinese_only("   \n"))

    def test_is_chinese_only_judges_by_kana_only(self):
        # 只按假名判定（与 classify_by_script 口径一致）：没有假名的表头行也算中文行，
        # 所以各来源的抽词函数要先把「翻譯：…」这类表头去掉（atwiki 走 parse_body）。
        self.assertTrue(is_chinese_only("翻译：夜明晴月\n你的名字"))

    def test_middle_dots_in_chinese_do_not_make_it_japanese(self):
        """中文译文里的 `・・・`（片假名区的间隔号）不算假名。

        用户 2026-10-22 报：某首曲子的整段中文翻译被放进了「待归类歌词」栏。
        原因是 `・`（U+30FB）落在片假名码位区间里，被当成假名，那一行于是判成日语；
        整段里只要有一行被判成日语，同一段的其它中文行也会被「块状格式」带进日语栏，
        `is_chinese_only()` 于是为假，译文就进了「待归类歌词」。
        """
        text = "抱歉啦。\n我完全忘記你了\n「我才沒有・・・討厭你喔？」"
        self.assertTrue(is_chinese_only(text))
        self.assertEqual("", classify_by_script(text)[0], "不该有一行被判成日语")
        self.assertTrue(is_chinese_only("你的内側只有13公分ー"), "长音符 `ー` 同理")

    def test_real_katakana_still_means_japanese(self):
        self.assertFalse(is_chinese_only("ゴメン。\nアンタ何スッカリ忘れてたワ"))
        self.assertFalse(is_chinese_only("「我才沒有・・・討厭你喔？」\nソウイヤ、マタ別レテナカッタカ"))

    def test_classifies_full_text(self):
        text = "きみの\n你的\n\nぼくの\n我的"
        jap, chs, roma = classify_by_script(text)
        self.assertEqual("きみの\n\nぼくの", jap)
        self.assertEqual("你的\n\n我的", chs)
        self.assertEqual("", roma)

    def test_returns_empty_strings_when_unknown(self):
        self.assertEqual(("", "", ""), classify_by_script("？？？\n！！！"))

    def test_blank_runs_collapse_to_one_line(self):
        # 待归类歌词里段落间有 3 个空行时，三栏里只应留 1 个
        text = "きみの\n你的\n\n\n\nぼくの\n我的"
        jap, chs, roma = classify_by_script(text)
        self.assertEqual("きみの\n\nぼくの", jap)
        self.assertEqual("你的\n\n我的", chs)
        self.assertEqual("", roma)


class ExtractChsByJapTest(TestCase):
    def test_keeps_lines_not_in_japanese(self):
        text = "きみの\n你的\nぼくの\n我的"
        self.assertEqual("你的\n我的", extract_chs_by_jap(text, "きみの\nぼくの"))

    def test_keeps_blank_lines(self):
        self.assertEqual("你的\n\n我的", extract_chs_by_jap("きみの\n你的\n\nぼくの\n我的", "きみの\nぼくの"))

    def test_blank_runs_collapse_to_one_line(self):
        self.assertEqual("你的\n\n我的",
                         extract_chs_by_jap("きみの\n你的\n\n\nぼくの\n我的", "きみの\nぼくの"))

    def test_empty_japanese_returns_empty(self):
        self.assertEqual("", extract_chs_by_jap("きみの\n你的", ""))
        self.assertEqual("", extract_chs_by_jap("きみの\n你的", "   "))

    def test_matches_lines_that_only_differ_by_furigana(self):
        """日语栏过完 with_furigana 后是 {{photrans}}，来源里是裸汉字：那些行同样算日语行。"""
        self.assertEqual("你的", extract_chs_by_jap("{{photrans|君|きみ}}の\n你的", "君の"))


class MirrorEnglishLinesTest(TestCase):
    """日语栏里的英文行必须也出现在中文栏（用户 2026-10-05 报）。

    歌里唱的英文（`Fly away`）模型常常只放进日语栏 —— 中文栏缺一行就跟日语栏错位。
    """

    def setUp(self):
        self.api = LyricsApi()

    def test_recognises_english_lines(self):
        self.assertTrue(is_english_line("Fly away"))
        self.assertTrue(is_english_line("I love you, yeah!"))
        self.assertTrue(is_english_line("kimi no na wa"))     # 拉丁字母就是拉丁字母，靠上下文区分
        self.assertFalse(is_english_line("きみの"))
        self.assertFalse(is_english_line("你的名字"))
        self.assertFalse(is_english_line("——"))
        self.assertFalse(is_english_line("　"))
        self.assertFalse(is_english_line(""))

    def test_missing_english_line_is_copied_into_chs(self):
        jap = "きみの\nFly away\nぼくの"
        # 模型只给了中文译文，英文那行漏了
        self.assertEqual("你的\nFly away\n我的", mirror_english_lines("你的\n我的", jap))

    def test_english_line_already_in_chs_is_kept_as_is(self):
        jap = "きみの\nFly away\nぼくの"
        self.assertEqual("你的\nFly away\n我的", mirror_english_lines("你的\nFly away\n我的", jap))

    def test_blank_lines_are_preserved(self):
        jap = "きみの\n\nFly away\nぼくの"
        self.assertEqual("你的\n\nFly away\n我的", mirror_english_lines("你的\n我的", jap))

    def test_blank_lines_in_both_columns_do_not_shift_the_result(self):
        """两栏都带分段空行时，中文栏的空行不许被当成一行内容吃掉（用户 2026-10-12）。

        中文栏的分段一律照日语栏重排（与 `align_blank_lines` 同一口径），
        所以两边都带空行时结果要与「只有日语栏带空行」完全一样。
        """
        jap = "きみの\n\nFly away\nぼくの"
        self.assertEqual("你的\n\nFly away\n我的",
                         mirror_english_lines("你的\n\n我的", jap))

    def test_japanese_only_columns_are_untouched(self):
        jap = "きみの\n\nぼくの"
        self.assertEqual("你的\n\n我的", mirror_english_lines("你的\n\n我的", jap))
        self.assertEqual("", mirror_english_lines("", jap))
        # 中文行比日语多：多出来的留在最后，不丢
        self.assertEqual("你的\n我的\n多的一句",
                         mirror_english_lines("你的\n我的\n多的一句", "きみの\nぼくの"))

    def test_english_only_song_lines_go_to_chs(self):
        """整段都是英文时中文栏也要有（两栏行数一致）。"""
        self.assertEqual("Fly away\nFar away", mirror_english_lines("", "Fly away\nFar away"))

    def test_ai_auto_copies_english_lines_into_the_chinese_column(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": "きみの\nFly away\n\nぼくの", "chs": "你的\n我的"}):
            result = self.api.ai_auto('{"text": "x"}')
        self.assertEqual("きみの\nFly away\n\nぼくの", result["jap"])
        self.assertEqual("你的\nFly away\n\n我的", result["chs"])

    def test_translated_english_line_is_not_duplicated(self):
        """用户 2026-10-12 报的《咬人兔》：日语那一行是 `"I miss U."`，译者是译成中文的
        （`"我想你"`）—— 两栏行数本来就一样，再补一份英文就多出一行、从那里起整栏错位。"""
        jap = 'きみの\n"I miss U."\nぼくの'
        self.assertEqual('你的\n"我想你"\n我的',
                         mirror_english_lines('你的\n"我想你"\n我的', jap))

    def test_ai_auto_aligns_when_the_english_line_is_translated(self):
        """同一件事放到整条分栏流程里：译文顶替了英文行时，中文栏仍要跟着日语栏分段。

        以前这里会判成「中文栏缺 1 行」而整栏放弃对齐 —— 生成出来的条目里日语栏有 `#NoHover`
        分段、中文栏却是一整块，悬停（`LyricsKai/hover`）从第一个分段起就配错
        （《咬人兔》与《Lost Train》两篇条目对照出来的就是这个）。
        """
        jap = 'きみの\n\n"I miss U."\nぼくの'
        chs = '你的\n"我想你"\n我的'
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": jap, "chs": chs}):
            result = self.api.ai_auto('{"text": "x"}')
        self.assertEqual(jap, result["jap"])
        self.assertEqual('你的\n\n"我想你"\n我的', result["chs"])

    def test_auto_extract_keeps_english_lines(self):
        """非 AI 的「以日语栏为参照挑中文」同样不能把英文漏掉。"""
        jap = "きみの\nFly away"
        result = self.api.auto(json.dumps({"text": "きみの\nFly away\n你的", "jap": jap}))
        self.assertEqual("你的\nFly away", result["chs"])


class PairChsWithJapTest(TestCase):
    """日/中交替的来源：中文栏必须**跟着日语栏的排版走**（用户 2026-10-05 报）。

    用户从 b 站动态（`/opus/…`）粘过来的就是这种：开头一句「尝试着翻译了一下…」不是歌词、
    日语栏里没有对应行，然后日文一行、中文一行交替。旧做法按**来源里的顺序**往日语栏的
    格子里填，那一句说明就把后面每一句中文都顶开了一格（中文栏整栏错位）。
    """

    NOTE = "尝试着翻译了一下，仅供参考jpg"
    SOURCE = NOTE + "\n\nきみの\n你的\n\nぼくの\n我的\nあいの\n爱的\n"
    JAP = "きみの\n\nぼくの\nあいの"

    def test_pairs_each_chinese_line_with_the_japanese_line_above_it(self):
        paired = lyrics_editor.pair_chs_with_jap(self.SOURCE, self.JAP)
        self.assertEqual("你的\n\n我的\n爱的\n" + self.NOTE, paired)

    def test_japanese_only_source_returns_none(self):
        """来源里的日语行跟日语栏对不上时不许硬凑，交给调用方回退。"""
        self.assertIsNone(lyrics_editor.pair_chs_with_jap("きみの\nぼくの", "きみの\nぼくの"))
        self.assertIsNone(lyrics_editor.pair_chs_with_jap(self.SOURCE, ""))

    # ---- 来源是「日语一整块 + 中文一整块」（用户 2026-10-17 报的《怨灵案件》）

    TWO_BLOCK_SOURCE = "きみの\nぼくの\nあいの\n你的\n我的\n爱的"
    TWO_BLOCK_JAP = "きみの\n\nぼくの\nあいの"

    def test_pairs_a_whole_japanese_block_followed_by_a_whole_chinese_block(self):
        """整块原文 + 整块译文：按行号对折后逐行配对，中文栏跟着日语栏分段。"""
        self.assertEqual("你的\n\n我的\n爱的",
                         lyrics_editor.pair_chs_with_jap(self.TWO_BLOCK_SOURCE,
                                                         self.TWO_BLOCK_JAP))

    def test_auto_aligns_a_two_block_source(self):
        result = LyricsApi().auto(json.dumps({"text": self.TWO_BLOCK_SOURCE,
                                              "jap": self.TWO_BLOCK_JAP}))
        self.assertEqual(self.TWO_BLOCK_JAP, result["jap"])
        self.assertEqual("你的\n\n我的\n爱的", result["chs"])

    def test_ai_auto_aligns_a_two_block_source(self):
        """AI 只把中文原样分出来（还是一整块）时，中文栏也要按日语栏分段。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": self.TWO_BLOCK_JAP, "chs": "你的\n我的\n爱的"}):
            result = LyricsApi().ai_auto(json.dumps({"text": self.TWO_BLOCK_SOURCE,
                                                     "jap": self.TWO_BLOCK_JAP}))
        self.assertEqual("你的\n\n我的\n爱的", result["chs"])

    def test_two_block_source_with_uneven_lines_is_not_forced(self):
        """两块行数对不上（译者合并 / 拆开了句子）→ 不硬凑，交给原来的回退。"""
        self.assertIsNone(lyrics_editor.pair_chs_with_jap(
            "きみの\nぼくの\nあいの\n你的\n我的", self.TWO_BLOCK_JAP))

    def test_prefers_a_nearby_rewritten_line_over_a_far_away_exact_duplicate(self):
        """日语栏写「ほんじゃまぁ」、来源写「ほんじゃまあ」：不能因为后半首还有一句一模一样的
        「ほんじゃまあ」就跳到那里，把中间每一句都跳过（用户 2026-10-17 报的《怨灵案件》：
        后半首因此一句也配不上，中文栏整栏丢失分段）。"""
        source = ("きみの\n你的\nほんじゃまあ\n那么好吧\nあいの\n爱的\n"
                  "ほんじゃまあ\n那么再会\nうた\n歌")
        jap = "きみの\nほんじゃまぁ\nあいの\nほんじゃまぁ\nうた"
        self.assertEqual("你的\n那么好吧\n爱的\n那么再会\n歌",
                         lyrics_editor.pair_chs_with_jap(source, jap))

    # ---- 《若要讨伐那条龙的话》（用户 2026-10-22 报）：全汉字的日文行 + 来源拆行

    LONG_SOURCE = (
        "森羅万象掌握　豪放磊落必勝\n掌握森罗万象，口出必胜豪言\n"
        "魑魅魍魎一蹴　酩酊酩酊\n剿灭魑魅魍魉，痛饮酩酊大醉\n"
        "\n"
        "無間虚空来来　ようこそ旅人よ\n自无间虚无而来的旅人，欢迎欢迎\n"
        "手に余るデザイア苦しかろうて\n欲求满溢而出很艰辛吧？\n"
        "映したまえ\n显现出吧！\n"
        "\n"
        "あの龍を討つというのなら\n若是要讨伐那条恶龙的话\n"
        "今鏡面　己が旅路\n直面过去，你已踏上旅路\n"
        "己が龍に成ると決めたのなら\n若是你已决定成为那恶龙的话\n"
        "いざ　此方へ\n来吧，向这里来"
    )
    LONG_JAP = (
        "森羅万象掌握　豪放磊落必勝\n魑魅魍魎一蹴　酩酊酩酊\n"
        "\n"
        "無間虚空来来　ようこそ旅人よ\n手に余るデザイア苦しかろうて　映したまえ\n"
        "\n"
        "あの龍を討つというのなら　今鏡面　己が旅路\n己が龍に成ると決めたのなら　いざ　此方へ"
    )

    def test_kanji_only_japanese_lines_are_not_taken_for_chinese(self):
        """整行都是汉字的日文（`森羅万象掌握　豪放磊落必勝`）不能当成上一句的中文。

        网易云给的日文有整行没有假名（《若要讨伐那条龙的话》开头那两句），只看「有没有假名」
        会把它们当成译文，整段歌词从第一句起就错位，最后全堆到中文栏末尾。
        """
        self.assertEqual(
            "掌握森罗万象，口出必胜豪言\n剿灭魑魅魍魉，痛饮酩酊大醉\n"
            "\n"
            "自无间虚无而来的旅人，欢迎欢迎\n欲求满溢而出很艰辛吧？ 显现出吧！\n"
            "\n"
            "若是要讨伐那条恶龙的话 直面过去，你已踏上旅路\n"
            "若是你已决定成为那恶龙的话 来吧，向这里来",
            lyrics_editor.pair_chs_with_jap(self.LONG_SOURCE, self.LONG_JAP))

    def test_a_source_line_split_in_two_is_joined_with_a_space(self):
        """来源把一句断成两行（`手に余るデザイア苦しかろうて` + `映したまえ`）时，
        两截的译文要合成一行 —— 以前只配上第一截，后半截跟后面每一句都错开。"""
        paired = lyrics_editor.pair_chs_with_jap(self.LONG_SOURCE, self.LONG_JAP)
        self.assertIn("欲求满溢而出很艰辛吧？ 显现出吧！", paired)
        self.assertNotIn("映したまえ", paired, "日文行绝不能留在中文栏里")

    def test_leftover_japanese_lines_make_the_pairing_bail_out(self):
        """来源里还有日文行没被认领（日语栏另整理过 / 少了重复段）→ 交还原译文，绝不往中文栏塞日文。"""
        source = "きみの\n你的\nぼくの\n我的\nあいの\n爱的"
        self.assertIsNone(lyrics_editor.pair_chs_with_jap(source, "きみの\nぼくの"))

    def test_auto_extract_keeps_the_chinese_column_aligned(self):
        result = LyricsApi().auto(json.dumps({"text": self.SOURCE, "jap": self.JAP}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual("你的\n\n我的\n爱的\n" + self.NOTE, result["chs"])

    def test_auto_classify_keeps_the_chinese_column_aligned(self):
        result = LyricsApi().auto(json.dumps({"text": self.SOURCE}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual("你的\n\n我的\n爱的\n" + self.NOTE, result["chs"])

    def test_ai_auto_also_pairs_when_the_model_shifts_the_column(self):
        """AI 那一栏也照这个配对纠正：模型把说明放进中文栏时不该顶开后面每一行。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": self.JAP, "chs": self.NOTE + "\n你的\n我的\n爱的"}):
            result = LyricsApi().ai_auto(json.dumps({"text": self.SOURCE}))
        self.assertEqual(self.JAP, result["jap"])
        self.assertEqual("你的\n\n我的\n爱的\n" + self.NOTE, result["chs"])

    def test_pairs_when_the_japanese_column_only_differs_by_furigana(self):
        """日语栏被注音模板包着（上一次 AI 的结果），跟来源里的裸汉字也要配得上。"""
        source = "君の\n你的\nぼくの\n我的"
        self.assertEqual("你的\n我的",
                         lyrics_editor.pair_chs_with_jap(source, "{{photrans|君|きみ}}の\nぼくの"))

    def test_pairs_despite_long_vowel_and_punctuation_rewrites(self):
        """来源把长音符写成「一」、标点也跟日语栏不同：照样逐行配对，不许整栏错位。"""
        source = "サイファ一!\n加密吧\nギタ一！\n吉他\nきみの\n你的"
        self.assertEqual("加密吧\n吉他\n你的",
                         lyrics_editor.pair_chs_with_jap(source, "サイファー!\nギター！\nきみの"))

    def test_rewritten_japanese_line_does_not_shift_the_rest(self):
        """个别行被改了词时按相似度认领它自己那一句，后面的中文不许跟着顶开。"""
        source = "きみの\n你的\nあいのうた\n爱之歌\nぼくの\n我的"
        self.assertEqual("你的\n爱之歌\n我的",
                         lyrics_editor.pair_chs_with_jap(source, "きみの\nあいの歌\nぼくの"))

    def test_ai_auto_keeps_the_japanese_column_the_user_typed(self):
        """用户填了日语栏就以它为准（用户 2026-10-07 报）：模型那栏常把长音符 / 标点「顺手改对」，
        再拿它去来源里找行就一行都对不上，中文栏跟着整栏错位。"""
        source = "サイファ一!\n加密吧\nギタ一！\n吉他"
        jap = "サイファ一!\nギタ一！"
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": "サイファー!\nギター！", "chs": "加密吧\n吉他"}):
            result = LyricsApi().ai_auto(json.dumps({"text": source, "jap": jap}))
        self.assertEqual(jap, result["jap"])             # 用户那栏一个字都不动
        self.assertEqual("加密吧\n吉他", result["chs"])    # 中文栏仍与日语栏一行对一行

    def test_ai_auto_aligns_even_when_the_model_rewrites_a_japanese_line(self):
        """没填日语栏时用模型那栏，个别行被改写也不能让中文栏从那里起错开。"""
        source = "きみの\n你的\nあいのうた\n爱之歌\nぼくの\n我的"
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": "きみの\nあいの歌\nぼくの", "chs": "你的\n爱之歌\n我的"}):
            result = LyricsApi().ai_auto(json.dumps({"text": source}))
        self.assertEqual("你的\n爱之歌\n我的", result["chs"])

    def test_ai_auto_preserves_merged_translation_stanzas(self):
        """译文把同一段的两句合并时，不要按日语行数把后续段落的中文顶错位。"""
        source = "きみの\nぼくの\n你的和我的\n\nあいの\nうた\n爱之歌"
        jap = "きみの\nぼくの\n\nあいの\nうた"
        chs = "你的和我的\n\n爱之歌"
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": jap, "chs": chs}):
            result = LyricsApi().ai_auto(json.dumps({"text": source}))

        self.assertEqual(jap, result["jap"])
        self.assertEqual(chs, result["chs"])


class BlockSourceStanzaTest(TestCase):
    """块状交替的来源（日语一整段、中文一整段，逐段交替）→ AI 分栏要按它分段。

    用户 2026-10-12 对照 voca.wiki《告别交信》与它引的巴哈姆特投稿要求：那种投稿是
    「日语一整段 + 中文一整段」逐段交替、**段与段之间没有空行**，段落边界只存在于这个
    交替结构里。模型按「原样保留来源的空行」分栏后三栏都没有空行，生成出来的条目连一个
    `#NoHover` 都没有 —— 整首歌词糊成一块、悬停译文整首错位。
    """

    def _ai_auto(self, source, jap, chs):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": jap, "chs": chs}):
            return LyricsApi().ai_auto(json.dumps({"text": source}))

    def test_stanza_breaks_are_recovered_from_a_block_source(self):
        """来源里没有空行，两栏的分段要从「日语块 / 中文块」的边界恢复出来。"""
        source = ("作词： 某人\n"
                  "きみの\nぼくの\nあなたの\n"
                  "你的\n我的\n你的呢\n"
                  "あいの\nうた\n"
                  "爱之歌\n")
        result = self._ai_auto(source, "きみの\nぼくの\nあなたの\nあいの\nうた",
                               "你的\n我的\n你的呢\n爱之歌")
        self.assertEqual("きみの\nぼくの\nあなたの\n\nあいの\nうた", result["jap"])
        self.assertEqual("你的\n我的\n你的呢\n\n爱之歌", result["chs"])

    def test_english_line_missing_from_the_chinese_block_is_restored(self):
        """歌里的英文行中文块少抄了一行（《告别交信》第 7 段）→ 补回同一位置，分段也要在。"""
        source = ("作词： 某人\n"
                  "How are you?\nきみの\nぼくの\n"
                  "你好\n我的\n"
                  "あいの\n"
                  "爱之歌\n")
        result = self._ai_auto(source, "How are you?\nきみの\nぼくの\nあいの",
                               "你好\n我的\n爱之歌")
        self.assertEqual("How are you?\nきみの\nぼくの\n\nあいの", result["jap"])
        self.assertEqual("How are you?\n你好\n我的\n\n爱之歌", result["chs"])

    def test_line_alternating_sources_are_left_to_the_pairing(self):
        """逐行交替的来源不归它管（那是 `pair_chs_with_jap` 的活），别切成一堆单行段。"""
        self.assertIsNone(lyrics_editor.pair_blocks_with_source(
            "きみの\n你的\nぼくの\n我的", "きみの\nぼくの", "你的\n我的"))

    def test_sources_without_stanza_blocks_are_left_alone(self):
        """来源只有一整块（没有段落边界可言）→ 不动。"""
        self.assertIsNone(lyrics_editor.pair_blocks_with_source(
            "きみの\nぼくの\n你的\n我的", "きみの\nぼくの", "你的\n我的"))
        self.assertIsNone(lyrics_editor.pair_blocks_with_source(
            "全然对不上\n的东西\n", "きみの\nぼくの", "你的\n我的"))


class MergeChsBlocksTest(TestCase):
    """「只有中文、译者用空行分块」的译文 → 按日语栏的分段合并（用户 2026-10-29 报）。

    真机：条目 https://voca.wiki/幽灵(KAIRUI)（`幽霊` 是重定向）的日语栏 11 行、3 段（3/4/4），
    待识别歌词是 b 站评论区那条（BV1wv41137pk，评论 128854373232）：只有中文、17 行、
    按译者的习惯分成 5 块（3/3/3/4/4）。条目里前三个块各自合并成了一行。
    """

    JAP = ("夏の葉末 浮かぶ雲の群泳 重ねては\n"
           "見える限り 美しい形 探している\n"
           "ふと瞬きすれば 見失うような憂の果てで\n\n"
           "幽霊みたいな\n幽霊みたいな\n透けるように生きる\n私でありたい\n\n"
           "幽霊みたいな\n幽霊みたいな\n姿をして\n形をして")

    SRC = ("夏叶末端\n浮云群游\n层层叠叠\n\n"
           "遥而望尽\n寻踪觅迹\n那奇丽之形\n\n"
           "在转眼之间\n便会消失不见的\n忧郁尽头\n\n"
           "像幽灵一样啊\n像幽灵一样呐\n透明般的生存着\n我也想要那样啊\n\n"
           "像幽灵一样啊\n像幽灵一样呐\n化为那样的姿态\n化为那样的形态")

    EXPECT = ("夏叶末端 浮云群游 层层叠叠\n"
              "遥而望尽 寻踪觅迹 那奇丽之形\n"
              "在转眼之间 便会消失不见的 忧郁尽头\n\n"
              "像幽灵一样啊\n像幽灵一样呐\n透明般的生存着\n我也想要那样啊\n\n"
              "像幽灵一样啊\n像幽灵一样呐\n化为那样的姿态\n化为那样的形态")

    def test_the_reported_case_merges_into_the_entry_layout(self):
        self.assertEqual(self.EXPECT, merge_chs_blocks(self.JAP, self.SRC))

    def test_nothing_but_breaks_and_spaces_changes(self):
        merged = merge_chs_blocks(self.JAP, self.SRC)
        self.assertEqual(lyrics_editor._squeeze(self.SRC), lyrics_editor._squeeze(merged))
        self.assertEqual(11, len([line for line in merged.split("\n") if line]))
        self.assertEqual([3, 4, 4], [len(block.split("\n")) for block in merged.split("\n\n")])

    def test_ai_layout_uses_it_without_asking_the_model(self):
        """「AI 识别并填入」这条路上不该为这件事花一次请求。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation") as layout:
            result = LyricsApi().ai_layout_chs(self.JAP, self.SRC)
        layout.assert_not_called()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.EXPECT, result["chs"])
        self.assertIn("合并", result["message"])

    def test_ai_auto_uses_it_for_a_chinese_only_source(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize") as recognize:
            result = LyricsApi().ai_auto(json.dumps({"text": self.SRC, "jap": self.JAP}))
        recognize.assert_not_called()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.EXPECT, result["chs"])
        self.assertIn("幽霊みたいな", result["jap"])

    def test_ambiguous_shapes_are_left_to_the_model(self):
        """块数与段数对不上、又有多解时不许硬凑。"""
        # 日语只有一整段（没有分段可参照）：`3/3/3/4/4` → 11 行有不止一种合并方式
        self.assertIsNone(merge_chs_blocks("\n".join(["あ"] * 11), self.SRC))
        # 只有一块、日语两段
        self.assertIsNone(merge_chs_blocks("あ\nい\n\nう\nえ", "甲\n乙\n丙"))

    def test_already_one_to_one_is_left_alone(self):
        """中文本来就是一行对一行（没有块需要合并）→ 不动，交给原来的对齐逻辑。"""
        same = ("甲\n\n乙\n\n丙\n\n丁\n戊\n己\n庚\n\n辛\n壬\n癸\n子")
        self.assertIsNone(merge_chs_blocks(self.JAP, same))

    def test_japanese_mixed_sources_are_not_merged(self):
        """来源里混着日文行就不是「只有中文的译文」，得走另一条路。"""
        mixed = self.SRC + "\n\n幽霊みたいな"
        self.assertIsNone(merge_chs_blocks(self.JAP, mixed))


class SourceShapeAuditTest(TestCase):
    """各种来源形状走**同一条链路**（`ai_auto`）时，是「确定性对齐」还是「必须模型判断」。

    用户 2026-10-29 要求核对「其他来源的翻译是否也会中日对不上」，并提醒
    「行数相同不代表就能完全对应」。逐条写下来，免得以后又靠猜：

    | 来源 | 形状 | 走哪条路 |
    | --- | --- | --- |
    | 网易云 | 一行日文一行中文（时间标签配对） | `split_paired_lines` + `merge_chs_lines`（确定） |
    | b 站评论区 / 简介 / 专栏 | 只有中文 + 译者空行分块 | `merge_chs_blocks`（确定，解不唯一才交给模型） |
    | 巴哈姆特 | 日语一整段 + 中文一整段逐段交替 | `pair_blocks_with_source`（确定） |
    | atwiki | 只有中文、整段没有空行 | 只能问模型（没有任何对齐依据） |
    """

    JAP = MergeChsBlocksTest.JAP
    SRC = MergeChsBlocksTest.SRC

    def _ai_auto(self, text, jap, reply):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value=reply) as recognize:
            return LyricsApi().ai_auto(json.dumps({"text": text, "jap": jap})), recognize

    def test_chinese_only_blocks_align_without_the_model(self):
        result, recognize = self._ai_auto(self.SRC, self.JAP, {})
        recognize.assert_not_called()
        self.assertEqual(MergeChsBlocksTest.EXPECT, result["chs"])

    def test_an_ambiguous_block_source_falls_back_to_the_model(self):
        """日语栏整首一段（没有分段可参照）→ 合并方式不唯一，只能问模型。"""
        flat = "\n".join(["あ"] * 11)
        result, recognize = self._ai_auto(
            self.SRC, flat, {"ok": True, "jap": flat, "chs": "甲\n乙\n丙"})
        self.assertTrue(recognize.called)
        self.assertTrue(result["ok"], result)

    def test_a_chinese_block_without_any_breaks_goes_to_the_model(self):
        """atwiki 那种「整段中文、一个空行都没有」：块比日语段少，没有对齐依据。"""
        atwiki = "\n".join(line for block in self.SRC.split("\n\n") for line in block.split("\n"))
        result, recognize = self._ai_auto(
            atwiki, self.JAP, {"ok": True, "jap": self.JAP, "chs": atwiki})
        self.assertTrue(recognize.called)
        self.assertTrue(result["ok"], result)

    def test_a_netease_paired_source_never_reaches_the_model(self):
        """网易云逐句配对（真机《パンドラ》的形状）：日语栏是合并行，译文逐句。"""
        paired = ("無理矢理に酔っ払い\n硬来的醉醺醺\nお手軽マジック\n随手变出的魔术\n"
                  "もう戻れない\n已经回不去了\n"
                  "朝まで踊る\n跳到天亮\n"
                  "夢の中\n梦境之中\n"
                  "醒めないで\n请不要醒来\n"
                  "このまま\n就这样下去")
        jap = ("無理矢理に酔っ払い　お手軽マジック\nもう戻れない\n朝まで踊る\n夢の中\n"
               "醒めないで\nこのまま")
        result, recognize = self._ai_auto(paired, jap, {})
        recognize.assert_not_called()
        self.assertEqual("硬来的醉醺醺 随手变出的魔术\n已经回不去了\n跳到天亮\n梦境之中\n"
                         "请不要醒来\n就这样下去", result["chs"])


class AlignChsLinesTest(TestCase):
    """来源结构 ≠ 日语栏结构时的确定性对齐（用户 2026-10-29 报的《image(picco)》）。

    条目 https://voca.wiki/image(picco)：日语栏 20 行 / 6 段（4-4-2-4-4-2），中文栏同结构；
    网易云 `2037363383` 是 28 句「日/中」逐句配对：
      * 第 2、4、5 段条目把两句并成一行，来源是分开的 → 合并；
      * `くだんない偶像` / `早く目 覚まして！` 来源写了 4 遍、条目只有 2 遍 → 丢掉多出来的；
      * 副歌那 4 行条目写了两遍、来源只有一遍 → 同一句日文共用同一份中文。
    """

    JAP = ("つまらない 夜をまた過ごして\n憂さ晴らし もう忘れてよ ねぇ\n"
           "嘘じゃない でも君以外でも\n無論 甘い夢見れるから Ah\n\n"
           "都合のいい愛嬌で 撃ち抜いてく大本命\n騙されて大草原 笑わせないでよ\n"
           "君の体温触れて 愛でもっと埋めて\n朝焼けの感傷で消えてしまう前に\n\n"
           "くだんない偶像\n早く目 覚まして！\n\n"
           "都合のいい愛嬌で 撃ち抜いてく大本命\n騙されて大草原 笑わせないでよ\n"
           "君の体温触れて 愛でもっと埋めて\n朝焼けの感傷で消えてしまう前に\n\n"
           "吐きそうな深夜二時 君とあたしで一対一\n阿鼻叫喚うっさいわ お咎めなしで\n"
           "キュンとした瞬間を 見逃さないで駐車場\nユートピアなんてない 触れる指先\n\n"
           "くだんない偶像\n早く目 覚まして！")

    NOTE = "（日本网络用语「草」（くさ）来表示“笑”的意思）"
    PAIRS = [("つまらない 夜をまた過ごして", "又度过了一个百无聊赖的夜晚"),
             ("憂さ晴らし もう忘れてよ ねぇ", "消愁解闷 已经忘掉喽 呐"),
             ("嘘じゃない でも君以外でも", "不是谎言 除你之外"),
             ("無論 甘い夢見れるから Ah", "当然会做甜美的梦啦 Ah"),
             ("都合のいい愛嬌で", "用那恰到好处的可爱"),
             ("撃ち抜いてく大本命", "来射穿 最喜欢的人的心"),
             ("騙されて大草原", f"被骗了 笑不活啦{NOTE}"),
             ("笑わせないでよ", "别太搞笑了"),
             ("君の体温触れて", "触摸你的体温"),
             ("愛でもっと埋めて", "用更多爱淹没我吧"),
             ("朝焼けの感傷で", "朝霞之下 触景伤感"),
             ("消えてしまう前に", "在它们消失之前"),
             ("くだんない偶像", "无趣的偶像"),
             ("早く目 覚まして！", "快醒醒 睁开双眼！"),
             ("くだんない偶像", "无趣的偶像"),
             ("早く目 覚まして！", "快醒醒 睁开双眼！"),
             ("吐きそうな深夜二時", "快要呕吐的深夜两点"),
             ("君とあたしで一対一", "你和我一对一"),
             ("阿鼻叫喚うっさいわ", "悲惨地叫唤（陷入阿鼻地狱后的呼叫声） 真吵啊"),
             ("お咎めなしで", "别再责怪了"),
             ("キュンとした瞬間を", "心动的那一瞬间"),
             ("見逃さないで駐車場", "不要错过停车场"),
             ("ユートピアなんてない", "可不存在什么乌托邦"),
             ("触れる指先", "触摸到的指尖"),
             ("くだんない偶像", "无趣的偶像"),
             ("早く目 覚まして！", "快醒醒 睁开双眼！"),
             ("くだんない偶像", "无趣的偶像"),
             ("早く目 覚まして！", "快醒醒 睁开双眼！")]

    @property
    def source(self) -> str:
        return "\n".join(line for pair in self.PAIRS for line in pair)

    @property
    def expected(self) -> str:
        return ("又度过了一个百无聊赖的夜晚\n消愁解闷 已经忘掉喽 呐\n不是谎言 除你之外\n"
                "当然会做甜美的梦啦 Ah\n\n"
                f"用那恰到好处的可爱 来射穿 最喜欢的人的心\n被骗了 笑不活啦{self.NOTE} 别太搞笑了\n"
                "触摸你的体温 用更多爱淹没我吧\n朝霞之下 触景伤感 在它们消失之前\n\n"
                "无趣的偶像\n快醒醒 睁开双眼！\n\n"
                f"用那恰到好处的可爱 来射穿 最喜欢的人的心\n被骗了 笑不活啦{self.NOTE} 别太搞笑了\n"
                "触摸你的体温 用更多爱淹没我吧\n朝霞之下 触景伤感 在它们消失之前\n\n"
                "快要呕吐的深夜两点 你和我一对一\n"
                "悲惨地叫唤（陷入阿鼻地狱后的呼叫声） 真吵啊 别再责怪了\n"
                "心动的那一瞬间 不要错过停车场\n可不存在什么乌托邦 触摸到的指尖\n\n"
                "无趣的偶像\n快醒醒 睁开双眼！")

    def _aligned(self):
        pairs = split_paired_lines(self.source)
        self.assertIsNotNone(pairs, "这 28 句就是逐句配对")
        return align_chs_lines(self.JAP, pairs[0], pairs[1])

    def test_reproduces_the_entry_layout(self):
        aligned = self._aligned()
        self.assertIsNotNone(aligned, "结构不同也要能确定地对上")
        self.assertEqual(self.expected, aligned[0])
        self.assertEqual(6, aligned[1], "来源多写的 6 句重复段要报出来")

    def test_nothing_changes_but_line_breaks(self):
        merged = self._aligned()[0]
        self.assertEqual(lyrics_editor._squeeze(self.expected), lyrics_editor._squeeze(merged))
        self.assertEqual([4, 4, 2, 4, 4, 2], [len(block.split("\n"))
                                             for block in merged.split("\n\n")])

    def test_ai_auto_takes_the_deterministic_path(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               side_effect=AssertionError("不该调用模型")), \
             mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation",
                               side_effect=AssertionError("不该调用模型")):
            result = LyricsApi().ai_auto(json.dumps(
                {"text": self.source, "jap": self.JAP}, ensure_ascii=False))
        self.assertTrue(result["ok"], result)
        # 注 → `<ref>译者注：…</ref>` 之后就是条目里的原文：第一处带脚注，重复的那一段
        # 去掉注释（`<references/>` 里才不会出现两条一样的），见 to_note_refs
        want = self.expected.replace(f"笑不活啦{self.NOTE}",
                                     f"笑不活啦<ref>译者注：{self.NOTE[1:-1]}</ref>", 1)
        want = want.replace(f"笑不活啦{self.NOTE}", "笑不活啦")
        # 第二处注在解释原文里的词（`阿鼻` ↔ `阿鼻叫喚`），同样转成脚注
        want = want.replace("（陷入阿鼻地狱后的呼叫声）",
                            "<ref>译者注：陷入阿鼻地狱后的呼叫声</ref>")
        self.assertEqual(want, result["chs"])
        self.assertIn("6 句", result["message"])

    def test_another_song_is_not_aligned(self):
        """日语栏是另一首歌时一行都拼不出来 → 返回 None（绝不硬凑）。"""
        other = "\n".join(["ぜんぜん違う歌詞", "全然別の歌", "知らない言葉", "違う歌だよ"])
        self.assertIsNone(align_chs_lines(other, *split_paired_lines(self.source)))

    def test_too_few_lines_are_left_alone(self):
        """只有两三句时不猜（太短，随便都能拼出个「唯一解」）。"""
        pairs = split_paired_lines("きみの\n你的\nぼくの\n我的\nあなたの\n你的啊\nそらの\n天空的")
        self.assertIsNone(align_chs_lines("きみの\nぼくの", pairs[0], pairs[1]))

    def test_the_strict_merge_still_wins_when_it_applies(self):
        """来源与日语栏 1:1 时严格版对得上，用不着放宽（`ai_auto` 也先试它）。"""
        pairs = split_paired_lines(self.source)
        jap = "\n".join(pairs[0].split("\n"))
        self.assertEqual("\n".join(pairs[1].split("\n")),
                         merge_chs_lines(pairs[0], pairs[1], jap))


class OpusSourceShapeTest(TestCase):
    """b 站专栏《虹捜索》（`/opus/1036390280160346128`）取回来的正文 → 逐句配对。

    用户 2026-10-29 报：这条链接点「从链接填充」显示检测不到。真机页面已回风控验证码页，
    改走 `opus/detail` 接口后拿到 113 段正文；开头那段是页眉
    （`虹搜索` / `翻译：我 校对：你` / `注：虹搜索=niji sousaku=二次创作`），
    清理后应当从 `目が覚めて仰天だ！` 开始、按空行分段、认成「日/中」逐句配对。
    """

    BODY = ("虹搜索\n翻译：我 校对：你\n注：虹搜索=niji sousaku=二次创作\n\n"
            "目が覚めて仰天だ！\n一觉醒来吓了一大跳！\n"
            "七色のそっくりさん\n七色彩虹般的人凭空出现\n"
            "モノクロで閉じた世界を\n这些挑战者将这封闭黑白的世界\n"
            "滅茶苦茶にするチャレンジヤー\n变得一团糟\n\n"
            "それぞれが表現者\n每个人都是表现者\n"
            "嵐のように喋ん\n像暴风雨般喋喋不休地争吵")

    def test_the_header_block_is_not_lyrics(self):
        cleaned = source_filler.strip_credit_lines(
            source_filler._drop_header_block(self.BODY, "【重音テトSV】虹捜索【読谷あかね】"))
        self.assertTrue(cleaned.startswith("目が覚めて仰天だ！"), cleaned[:40])
        for junk in ("虹搜索", "翻译：", "注："):
            self.assertNotIn(junk, cleaned)

    def test_it_pairs_as_day_chinese(self):
        cleaned = source_filler.strip_credit_lines(
            source_filler._drop_header_block(self.BODY, "【重音テトSV】虹捜索【読谷あかね】"))
        pairs = split_paired_lines(cleaned)
        self.assertIsNotNone(pairs, cleaned)
        self.assertEqual(["目が覚めて仰天だ！", "七色のそっくりさん", "モノクロで閉じた世界を",
                          "滅茶苦茶にするチャレンジヤー", "それぞれが表現者", "嵐のように喋ん"],
                         [line for line in pairs[0].split("\n") if line])
        self.assertEqual([4, 2], [len(block.split("\n")) for block in pairs[0].split("\n\n")])

    def test_a_real_first_stanza_is_kept(self):
        """正文第一段就是歌词时不许丢（页眉判定只认署名 / 注释行与单行歌名）。"""
        body = ("目が覚めて仰天だ！\n一觉醒来吓了一大跳！\n"
                "七色のそっくりさん\n七色彩虹般的人凭空出现\n\n"
                "それぞれが表現者\n每个人都是表现者")
        self.assertEqual(body, source_filler._drop_header_block(body, "【初音ミク】某首歌"))


class GuessLayoutTest(TestCase):
    def test_guesses_group_length_and_line_numbers(self):
        text = ("きみの\n你的\n\n" * 3).strip()
        layout = guess_layout(text)
        self.assertIsNotNone(layout)
        self.assertEqual("3", layout["group_length"])
        self.assertEqual("1", layout["jap_line"])
        self.assertEqual("2", layout["chs_line"])
        self.assertEqual("", layout["roma_line"])

    def test_detects_romaji_line(self):
        text = ("きみの\nkimi no\n你的\n\n" * 3).strip()
        layout = guess_layout(text)
        self.assertIsNotNone(layout)
        self.assertEqual("2", layout["roma_line"])

    def test_single_group_without_blank_lines(self):
        # 没有分隔空行时按「每组两行」处理（与原 tkinter 版行为一致）
        layout = guess_layout("きみの\n你的")
        self.assertEqual("2", layout["group_length"])
        self.assertEqual("1", layout["jap_line"])
        self.assertEqual("2", layout["chs_line"])

    def test_returns_none_for_single_line(self):
        self.assertIsNone(guess_layout("きみの"))
        self.assertIsNone(guess_layout(""))


class LyricsApiTest(TestCase):
    def setUp(self):
        self.api = LyricsApi()
        self.window = mock.Mock()
        self.api._window = self.window

    def _call(self, method, **payload):
        return method(json.dumps(payload))

    # —— auto ——

    def test_auto_requires_text(self):
        result = self._call(self.api.auto, text="  ")
        self.assertFalse(result["ok"])
        self.assertIn("粘贴歌词", result["error"])

    def test_auto_bad_json(self):
        self.assertFalse(self.api.auto("{oops")["ok"])

    def test_auto_prefers_extracting_with_existing_japanese(self):
        result = self._call(self.api.auto, text="きみの\n你的", jap="きみの")
        self.assertTrue(result["ok"])
        self.assertEqual("extract", result["mode"])
        self.assertEqual("你的", result["chs"])

    def test_auto_classifies_by_script(self):
        result = self._call(self.api.auto, text="きみの\n你的")
        self.assertEqual("classify", result["mode"])
        self.assertEqual("きみの", result["jap"])
        self.assertEqual("你的", result["chs"])

    def test_auto_falls_back_to_guessing_lines(self):
        # 纯标点（既非假名、非 ASCII、非汉字）时脚本分类认不出，改按重复段结构猜
        text = "？？\n。，\n\n？？\n。，"
        self.assertEqual(("", "", ""), classify_by_script(text))
        result = self._call(self.api.auto, text=text)
        self.assertTrue(result["ok"])
        self.assertEqual("lines", result["mode"])
        self.assertEqual("3", result["layout"]["group_length"])

    def test_auto_reports_failure(self):
        result = self._call(self.api.auto, text="？？？")
        self.assertFalse(result["ok"])
        self.assertIn("失败", result["error"])

    def test_auto_collapses_blank_runs(self):
        # 点「自动识别」后段落之间不能留 2 个以上空行
        result = self._call(self.api.auto, text="きみの\n你的\n\n\n\nぼくの\n我的")
        self.assertTrue(result["ok"])
        self.assertEqual("きみの\n\nぼくの", result["jap"])
        self.assertEqual("你的\n\n我的", result["chs"])

    def test_auto_normalizes_existing_columns(self):
        # 日语栏自己写的 2 个空行也一起压掉
        result = self._call(self.api.auto, text="きみの\n你的\n\n\nぼくの",
                            jap="きみの\n\n\nぼくの", roma="a\n\n\nb")
        self.assertEqual("extract", result["mode"])
        self.assertEqual("きみの\n\nぼくの", result["jap"])
        self.assertEqual("a\n\nb", result["roma"])

    def test_auto_converts_parenthesised_furigana(self):
        # 「漢字(かんじ)」→ {{photrans|漢字|かんじ}} 现在是自动识别固定做的一步
        result = self._call(self.api.auto, text="漢字(かんじ)を読む\n读汉字")
        self.assertTrue(result["ok"])
        self.assertEqual("{{photrans|漢字|かんじ}}を読む", result["jap"])
        self.assertEqual("读汉字", result["chs"])

    def test_auto_keeps_existing_photrans(self):
        # 已经是 {{photrans}} 的不再套一层
        result = self._call(self.api.auto, text="{{photrans|漢字|かんじ}}を読む\n读汉字")
        self.assertEqual("{{photrans|漢字|かんじ}}を読む", result["jap"])

    def test_auto_converts_furigana_with_existing_japanese_column(self):
        result = self._call(self.api.auto, text="漢字(かんじ)\n读汉字",
                            jap="漢字(かんじ)")
        self.assertEqual("extract", result["mode"])
        self.assertEqual("{{photrans|漢字|かんじ}}", result["jap"])

    # —— 从来源链接填充 ——
    def test_fill_source_delegates(self):
        filled = {"ok": True, "translator": "白夜落星", "sourceName": "网易云音乐"}
        with mock.patch.object(lyrics_editor.source_filler, "fill_source",
                               return_value=filled) as filler:
            self.assertEqual(filled, self.api.fill_source("https://music.163.com/#/song?id=1"))
        filler.assert_called_once_with("https://music.163.com/#/song?id=1")

    def test_fill_source_marks_chinese_only_lyrics_for_the_chinese_column(self):
        """取回来的只有中文（atwiki 上不少页面只写译文）→ 直接进「中文」栏。"""
        filled = {"ok": True, "lyrics": "想一眼就看穿那橙色之物吧?\n這點我跟你真是一丘之貉呢"}
        with mock.patch.object(lyrics_editor.source_filler, "fill_source", return_value=filled):
            self.assertEqual("chs", self.api.fill_source("https://x/1")["target"])

    def test_fill_source_sends_mixed_lyrics_to_the_pending_column(self):
        filled = {"ok": True, "lyrics": "きみの名前を呼ぶ\n你的名字"}
        with mock.patch.object(lyrics_editor.source_filler, "fill_source", return_value=filled):
            self.assertEqual("source", self.api.fill_source("https://x/1")["target"])

    def test_fill_source_without_lyrics_has_no_target(self):
        """填充失败 / 没有歌词时不该多出 target（界面按老样子处理）。"""
        with mock.patch.object(lyrics_editor.source_filler, "fill_source",
                               return_value={"ok": False, "error": "boom"}):
            self.assertNotIn("target", self.api.fill_source("https://x/1"))
        with mock.patch.object(lyrics_editor.source_filler, "fill_source",
                               return_value={"ok": True, "lyrics": ""}):
            self.assertNotIn("target", self.api.fill_source("https://x/1"))

    # —— 演唱者上色（{{LyricsKai/colors}}）——

    def test_context_carries_colors_and_charas(self):
        api = LyricsApi(charas=["宮舞モカ", "Ryo"], use_colors=True)
        with mock.patch.object(lyrics_editor.lyrics_colors, "fetch_colors",
                               return_value={"宫舞茉歌": "#72A6C0", "RYO": "#EB6238"}):
            context = api.get_context()
        self.assertTrue(context["useColors"])
        self.assertEqual([{"name": "宮舞モカ", "color": "#333333"},
                          {"name": "Ryo", "color": "#EB6238"}], context["charas"])

    def test_context_without_charas(self):
        self.assertEqual([], LyricsApi().get_context()["charas"])

    def test_save_records_chara_marks(self):
        self._call(self.api.save, jap="a\nb", chs="啊\n哦", useColors=True,
                   charaMarks={"0": ["A"], "1": ["A", "B"], "2": []})
        self.assertTrue(self.api.result.use_colors)
        self.assertEqual({"0": ["A"], "1": ["A", "B"]}, self.api.result.chara_marks)

    def test_save_records_inline_splits(self):
        self._call(self.api.save, jap="未来は誰も知らない", chs="未來無人知曉", useColors=True,
                   charaMarks={"0": [["A"], ["B"]]},
                   charaSplits={"0": {"jap": [2], "chs": [2], "roma": []}, "1": {}})
        self.assertEqual({"0": [["A"], ["B"]]}, self.api.result.chara_marks)
        self.assertEqual({"0": {"jap": [2], "chs": [2]}}, self.api.result.chara_splits)

    def test_save_without_splits(self):
        self._call(self.api.save, jap="a", chs="啊")
        self.assertIsNone(self.api.result.chara_splits)

    def test_save_without_marks(self):
        self._call(self.api.save, jap="a", chs="啊")
        self.assertFalse(self.api.result.use_colors)
        self.assertIsNone(self.api.result.chara_marks)

    def test_save_records_the_chinese_track_marks(self):
        """中文栏可以有自己的标记（用户 2026-09 要求：中文歌词的标记也要能改）。"""
        self._call(self.api.save, jap="a\nb", chs="啊\n哦", useColors=True,
                   charaMarks={"0": ["A"]}, charaMarksChs={"0": ["A"], "1": ["B"]})
        self.assertEqual({"0": ["A"]}, self.api.result.chara_marks)
        self.assertEqual({"0": ["A"], "1": ["B"]}, self.api.result.chara_marks_chs)

    def test_save_without_chinese_marks_keeps_it_none(self):
        self._call(self.api.save, jap="a", chs="啊", useColors=True, charaMarks={"0": ["A"]})
        self.assertIsNone(self.api.result.chara_marks_chs)

    # —— 按日语标记中文（ai_mark_chs）——

    def _mark_chs(self, jap="きみの\nはるか", chs="你的名字\n远方", marks=None, **extra):
        payload = {"jap": jap, "chs": chs, "charaMarks": marks if marks is not None
                   else {"0": ["A"], "1": ["B"]}}
        payload.update(extra)
        return self._call(self.api.ai_mark_chs, **payload)

    def test_mark_chs_copies_by_line_when_counts_match(self):
        """两栏行数一致、空行位置也一致 → 直接按行号照搬，不用联网。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation") as aligned:
            result = self._mark_chs(jap="きみの\n\nはるか", chs="你的名字\n\n远方",
                                    marks={"0": ["A"], "2": ["B"]})
        self.assertTrue(result["ok"])
        self.assertEqual({"0": ["A"], "2": ["B"]}, result["marks"])
        self.assertIn("照搬", result["message"])
        self.assertFalse(aligned.called, "行数一致时不该去调 AI")
        # 没有空行的两栏同样是 1:1
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation") as aligned:
            result = self._mark_chs()
        self.assertEqual({"0": ["A"], "1": ["B"]}, result["marks"])
        self.assertFalse(aligned.called)

    def test_mark_chs_asks_the_ai_when_only_the_stanza_shape_differs(self):
        """行数一样、空行位置不一样 → 不可能逐行对应，交给 AI（用户 2026-10-29 提醒）。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation",
                               return_value={"ok": True, "pairs": {0: [0], 1: [1], 2: [2]},
                                             "model": "test-model"}) as aligned:
            result = self._mark_chs(jap="きみの\nはるか\n\nやまの", chs="你的名字\n\n远方\n山的那边",
                                    marks={"0": ["A"], "2": ["B"]})
        self.assertTrue(aligned.called, "空行位置不同就不该按行号硬搬")
        self.assertTrue(result["ok"], result)
        self.assertEqual({"0": ["A"], "2": ["B"]}, result["marks"])

    def test_mark_chs_asks_the_ai_when_counts_differ(self):
        """行数不一样（译者合并 / 拆开）→ 交给 AI 对齐行号，再把标记搬过去。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation",
                               return_value={"ok": True, "pairs": {0: [0], 1: [0, 1]},
                                             "model": "test-model"}) as aligned:
            result = self._mark_chs(chs="你的名字\n远方\n多一行")
        self.assertTrue(result["ok"])
        self.assertEqual({"0": ["A"], "1": ["A", "B"]}, result["marks"])
        self.assertIn("test-model", result["message"])
        self.assertTrue(aligned.called)

    def test_mark_chs_reports_ai_failure(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation",
                               return_value={"ok": False, "error": "模型返回的内容不是 JSON"}):
            result = self._mark_chs(chs="你的名字\n远方\n多一行")
        self.assertFalse(result["ok"])
        self.assertEqual("模型返回的内容不是 JSON", result["error"])

    def test_mark_chs_needs_japanese_marks(self):
        result = self._mark_chs(marks={})
        self.assertFalse(result["ok"])
        self.assertIn("日语栏", result["error"])

    def test_mark_chs_needs_both_columns(self):
        result = self._mark_chs(chs="   ")
        self.assertFalse(result["ok"])
        self.assertIn("中文栏", result["error"])
        result = self._mark_chs(jap="  ")
        self.assertFalse(result["ok"])
        self.assertIn("日语栏", result["error"])

    def test_mark_chs_drops_marks_that_do_not_map(self):
        """对应关系里全是空数组（对不上）→ 报错，别把中文栏标成一片空白。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation",
                               return_value={"ok": True, "pairs": {0: [], 1: []},
                                             "model": "test-model"}):
            result = self._mark_chs(chs="你的名字\n远方\n多一行")
        self.assertFalse(result["ok"])
        self.assertIn("标记", result["error"])

    # —— convert ——

    def test_convert_splits_by_line_numbers(self):
        result = self._call(self.api.convert, text="日1\n中1\n日2\n中2",
                            groupLength="2", japLine="1", chsLine="2", romaLine="")
        self.assertTrue(result["ok"])
        self.assertEqual("日1\n日2", result["jap"])
        self.assertEqual("中1\n中2", result["chs"])
        self.assertEqual("", result["roma"])

    def test_convert_requires_group_length(self):
        result = self._call(self.api.convert, text="日1\n中1", groupLength="")
        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])

    def test_convert_requires_text(self):
        self.assertFalse(self._call(self.api.convert, text="", groupLength="3")["ok"])

    # —— save / cancel ——

    def test_save_builds_lyrics_and_closes_window(self):
        result = self._call(self.api.save, jap="きみの", chs="你的", roma="kimi no",
                            translator="某人", translatorUrl="https://x", sourceName="AtWiki",
                            sourceUrl="https://y")
        self.assertTrue(result["ok"])
        lyrics = self.api.result
        self.assertEqual("きみの", lyrics.lyrics_jap)
        self.assertEqual("你的", lyrics.lyrics_chs)
        self.assertEqual("kimi no", lyrics.lyrics_roma)
        self.assertEqual("某人", lyrics.translator)
        self.assertEqual("AtWiki", lyrics.source_name)
        self.window.destroy.assert_called_once()

    def test_save_requires_some_lyrics(self):
        result = self._call(self.api.save, jap="", chs="  ", roma="kimi")
        self.assertFalse(result["ok"])
        self.assertIn("都是空的", result["error"])
        self.assertIsNone(self.api.result)

    def test_cancel_leaves_no_result(self):
        self.assertTrue(self.api.cancel()["ok"])
        self.assertIsNone(self.api.result)
        self.window.destroy.assert_called_once()

    # —— 使用 LyricsKai/hover 开关 ——

    def test_save_records_hover_switch(self):
        self._call(self.api.save, jap="きみの", chs="你的", useHover=True)
        self.assertTrue(self.api.result.use_hover)

    def test_save_defaults_hover_to_off(self):
        self._call(self.api.save, jap="きみの", chs="你的")
        self.assertFalse(self.api.result.use_hover)

    # —— 使用 LyricsKai/Roma 开关（罗马音栏默认藏起来）——

    def test_save_records_roma_switch(self):
        self._call(self.api.save, jap="きみの", chs="你的", roma="kimi no", useRoma=True)
        self.assertTrue(self.api.result.use_roma)
        self.assertEqual("kimi no", self.api.result.lyrics_roma)

    def test_save_defaults_roma_to_off(self):
        self._call(self.api.save, jap="きみの", chs="你的", roma="kimi no")
        self.assertFalse(self.api.result.use_roma)

    def test_get_context(self):
        api = LyricsApi("预填歌词", "AtWiki", use_hover=True)
        with mock.patch.object(lyrics_editor.ai_lyrics, "context",
                               return_value={"enabled": True, "hidden": False}):
            context = api.get_context()
        self.assertEqual({"initial": "预填歌词", "jap": "", "chs": "", "sourceHint": "AtWiki",
                          "translator": "", "translatorUrl": "", "sourceName": "",
                          "sourceUrl": "", "prefillMessage": "",
                          "useHover": True, "useColors": False, "useRoma": False, "charas": [],
                          "aiLyrics": {"enabled": True, "hidden": False}},
                         context)

    def test_context_carries_the_japanese_column_prefill(self):
        """日语原文预填进**日语栏**（不是「待归类歌词」），用户只要补译文。"""
        context = LyricsApi(jap_text="きみの名前を呼ぶ").get_context()
        self.assertEqual("きみの名前を呼ぶ", context["jap"])
        self.assertEqual("", context["initial"])

    def test_ai_auto_without_pending_lyrics_lays_out_the_chinese_column(self):
        """没有「待归类歌词」时，改成「按日语栏给中文栏排版」（用户 2026-10-16 要求）。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize") as recognize, \
             mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation",
                               return_value={"ok": True, "chs": "啊\n咦", "model": "m"}) as layout:
            result = self.api.ai_auto(json.dumps({"jap": "あ\nい", "chs": "啊咦"}))
        recognize.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual("layout", result["mode"])
        self.assertEqual("啊\n咦", result["chs"])
        self.assertIsNone(result["jap"], "日语栏不动")
        self.assertIn("按日语栏给中文栏排版", result["message"])
        layout.assert_called_once_with("あ\nい", "啊咦")

    def test_ai_auto_without_pending_lyrics_needs_both_columns(self):
        """只有一边有内容时不做排版，仍按「先粘贴歌词」报错（走原来的分栏那条路）。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value={"ok": False, "error": "请先在左边粘贴歌词"}):
            result = self.api.ai_auto(json.dumps({"jap": "あ\nい", "chs": ""}))
        self.assertFalse(result["ok"])
        self.assertIn("粘贴", result["error"])

    def test_ai_layout_chs_rejects_a_rewritten_translation(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation",
                               return_value={"ok": True, "chs": "啊\n哟", "model": "m"}):
            result = self.api.ai_layout_chs("あ\nい", "啊咦")
        self.assertFalse(result["ok"])
        self.assertIn("改动", result["error"])

    def test_ai_layout_chs_keeps_the_result_when_only_breaks_changed(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation",
                               return_value={"ok": True, "chs": "啊\n咦\n\n呜", "model": "m"}):
            result = self.api.ai_layout_chs("あ\nい\n\nう", "啊咦\n\n呜")
        self.assertTrue(result["ok"])
        self.assertEqual("啊\n咦\n\n呜", result["chs"])

    def test_ai_layout_chs_restores_stanza_breaks_when_the_model_flattens_them(self):
        """模型只断行、没留空行时（中文来源本来就是一整块）按日语栏补回段落空行。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "layout_translation",
                               return_value={"ok": True, "chs": "啊\n咦\n呜", "model": "m"}):
            result = self.api.ai_layout_chs("あ\nい\n\nう", "啊\n咦\n呜")
        self.assertTrue(result["ok"])
        self.assertEqual("啊\n咦\n\n呜", result["chs"])

    def test_ai_layout_chs_needs_both_columns(self):
        result = self.api.ai_layout_chs("", "啊")
        self.assertFalse(result["ok"])
        self.assertIn("日语栏", result["error"])
        result = self.api.ai_layout_chs("あ", "   ")
        self.assertFalse(result["ok"])
        self.assertIn("中文栏", result["error"])

    def test_ai_auto_delegates_to_ai_lyrics(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value={"ok": True, "jap": "あ"}) as recognize:
            self.assertEqual({"ok": True, "jap": "あ"}, self.api.ai_auto('{"text": "あ"}'))
        recognize.assert_called_once_with('{"text": "あ"}')

    def test_ai_auto_converts_parenthesised_furigana(self):
        # AI 分完栏也会把日语栏里的「漢字(かんじ)」转成 {{photrans}}
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value={"ok": True, "jap": "漢字(かんじ)", "chs": "读汉字"}):
            result = self.api.ai_auto('{"text": "x"}')
        self.assertEqual("{{photrans|漢字|かんじ}}", result["jap"])
        self.assertEqual("读汉字", result["chs"])

    def test_ai_auto_leaves_jap_alone_when_it_failed(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize",
                               return_value={"ok": False, "error": "boom"}):
            self.assertEqual({"ok": False, "error": "boom"}, self.api.ai_auto('{"text": "x"}'))

    def test_align_blank_lines_follows_the_reference(self):
        """中文栏跟着日语栏分段（用户 2026-10-03：「中文栏没跟日语栏一个格式」）。"""
        jap = "あ\nい\n\nう\nえ"
        chs = "甲\n乙\n丙\n丁"
        self.assertEqual("甲\n乙\n\n丙\n丁", lyrics_editor.align_blank_lines(chs, jap))
        # 中文行比日语多：多出来的排在最后，不丢
        self.assertEqual("甲\n\n乙\n丙", lyrics_editor.align_blank_lines("甲\n乙\n丙", "あ\n\nい"))
        # 日语栏没有空行时中文也跟着没有（连续空行先压成一个，再按日语栏的分段走）
        self.assertEqual("甲\n乙", lyrics_editor.align_blank_lines("甲\n\n\n乙", "あ\nい"))
        self.assertEqual("", lyrics_editor.align_blank_lines("", jap))
        self.assertEqual("甲\n乙", lyrics_editor.align_blank_lines("甲\n乙", ""))

    def test_ai_auto_aligns_chs_and_roma_with_the_japanese_column(self):
        with mock.patch.object(lyrics_editor.ai_lyrics, "recognize", return_value={
                "ok": True, "jap": "あ\nい\n\nう\nえ", "chs": "甲\n乙\n丙\n丁",
                "roma": "a\ni\nu\ne"}):
            result = self.api.ai_auto('{"text": "x"}')
        self.assertEqual("あ\nい\n\nう\nえ", result["jap"])
        self.assertEqual("甲\n乙\n\n丙\n丁", result["chs"])      # 与日语栏同一位置空行
        self.assertEqual("a\ni\n\nu\ne", result["roma"])


class LyricsPrefillContextTest(TestCase):
    """流程先查到 atwiki 翻译时，「歌词」页打开就把「从链接填充」会填的东西填好（用户 2026-10-20）。"""

    JAP = "月明かりがそっと照らしだす"
    CHS = "月光悄悄照耀而下"

    def _api(self, **kwargs):
        api = LyricsApi(jap_text=self.JAP, source_hint="https://w.atwiki.jp/vocaloidchly/pages/1.html",
                        **kwargs)
        api._window = mock.Mock()
        return api

    def test_chinese_only_translation_goes_into_the_chs_column(self):
        context = self._api(prefill={
            "lyrics": self.CHS, "target": "chs", "translator": "yanao",
            "translatorUrl": "", "sourceName": "VOCALOID中文歌词wiki",
            "sourceUrl": "https://w.atwiki.jp/vocaloidchly/pages/1.html",
            "message": "已从 VOCALOID中文歌词wiki 预填翻译（翻译者：yanao）"}).get_context()
        self.assertEqual("", context["initial"], "只有中文的译文不该进「待归类歌词」")
        self.assertEqual(self.CHS, context["chs"])
        self.assertEqual(self.JAP, context["jap"])
        self.assertEqual("yanao", context["translator"])
        self.assertEqual("VOCALOID中文歌词wiki", context["sourceName"])
        self.assertEqual("https://w.atwiki.jp/vocaloidchly/pages/1.html", context["sourceUrl"])
        self.assertEqual("https://w.atwiki.jp/vocaloidchly/pages/1.html", context["sourceHint"])
        self.assertIn("已从 VOCALOID中文歌词wiki 预填翻译", context["prefillMessage"])

    def test_mixed_translation_goes_into_the_pending_column(self):
        context = self._api(prefill={"lyrics": "あ\n你的", "target": "source"}).get_context()
        self.assertEqual("あ\n你的", context["initial"])
        self.assertEqual("", context["chs"])

    def test_without_prefill_the_columns_stay_untouched(self):
        context = self._api().get_context()
        self.assertEqual("", context["initial"])
        self.assertEqual("", context["chs"])
        self.assertEqual("", context["translator"])
        self.assertEqual("", context["prefillMessage"])

    def test_the_prefilled_url_wins_over_an_empty_hint(self):
        context = LyricsApi(prefill={"lyrics": "x", "target": "chs",
                                     "sourceUrl": "https://w.atwiki.jp/x"}).get_context()
        self.assertEqual("https://w.atwiki.jp/x", context["sourceHint"])


class OpenEditorTest(TestCase):
    """歌词整理入口：交给 GUI 门面（界面已是主窗口里的「歌词」页）。"""

    def test_returns_none_without_gui(self):
        from utils import lyrics_editor, ui
        with mock.patch.object(ui, "is_active", return_value=False):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())

    def test_delegates_to_gui_facade(self):
        from models.song import Lyrics
        from utils import lyrics_editor, ui
        lyrics = Lyrics(lyrics_jap="きみの", lyrics_chs="你的", use_hover=True)
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_lyrics_editor", return_value=lyrics) as open_editor:
            result = lyrics_editor.open_lyrics_editor("预填", "手动粘贴", True, False,
                                                      ["初音未来"], "きみの", True)
        self.assertIs(result, lyrics)
        open_editor.assert_called_once_with("预填", "手动粘贴", True, False, ["初音未来"], "きみの",
                                            True, prefill=None)

    def test_returns_none_when_cancelled(self):
        from utils import lyrics_editor, ui
        with mock.patch.object(ui, "is_active", return_value=True), \
             mock.patch.object(ui, "open_lyrics_editor", return_value=None):
            self.assertIsNone(lyrics_editor.open_lyrics_editor())


class SongWiringTest(TestCase):
    def test_get_manual_lyrics_delegates_to_editor(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            lyrics = models.song.get_manual_lyrics("预填", use_hover=True)
        self.assertEqual("きみの", lyrics.lyrics_jap)
        editor.assert_called_once_with("预填", use_hover=True, use_colors=False, charas=(),
                                       jap_text="", use_roma=False, source_hint="", prefill=None)

    def test_get_manual_lyrics_prefills_the_japanese_column(self):
        """日语原文进的是**日语栏**（`jap_text`），不是「待归类歌词」。"""
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            models.song.get_manual_lyrics(jap_text="きみの名前を呼ぶ")
        self.assertEqual("きみの名前を呼ぶ", editor.call_args.kwargs["jap_text"])
        self.assertEqual("", editor.call_args.args[0])

    def test_get_manual_lyrics_defaults_hover_off(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            models.song.get_manual_lyrics("预填")
        editor.assert_called_once_with("预填", use_hover=False, use_colors=False, charas=(),
                                       jap_text="", use_roma=False, source_hint="", prefill=None)

    def test_get_manual_lyrics_returns_empty_on_cancel(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor", return_value=None):
            lyrics = models.song.get_manual_lyrics()
        self.assertIsNone(lyrics.lyrics_jap)
        self.assertIsNone(lyrics.lyrics_chs)
