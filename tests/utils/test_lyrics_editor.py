"""utils/lyrics_editor.py 的单元测试：分类 / 切分 / pywebview 接口。"""
import json
from unittest import TestCase
from unittest import mock

from utils import lyrics_editor
from utils.lyrics_editor import (LyricsApi, classify_by_script, classify_stanza,
                                 extract_chs_by_jap, guess_layout, is_english_line,
                                 mirror_english_lines, normalize_blank_lines,
                                 normalize_no_hover, process_lyrics_jap,
                                 process_translation)


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

    def test_empty_stanza(self):
        self.assertEqual(([], [], []), classify_stanza([]))


class ClassifyByScriptTest(TestCase):
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
        """两栏行数一致 → 直接按行号照搬，不用联网。"""
        with mock.patch.object(lyrics_editor.ai_lyrics, "mark_translation") as aligned:
            result = self._mark_chs()
        self.assertTrue(result["ok"])
        self.assertEqual({"0": ["A"], "1": ["B"]}, result["marks"])
        self.assertIn("照搬", result["message"])
        self.assertFalse(aligned.called, "行数一致时不该去调 AI")

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

    def test_get_context(self):
        api = LyricsApi("预填歌词", "AtWiki", use_hover=True)
        with mock.patch.object(lyrics_editor.ai_lyrics, "context",
                               return_value={"enabled": True, "hidden": False}):
            context = api.get_context()
        self.assertEqual({"initial": "预填歌词", "sourceHint": "AtWiki", "useHover": True,
                          "useColors": False, "charas": [],
                          "aiLyrics": {"enabled": True, "hidden": False}},
                         context)

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
            result = lyrics_editor.open_lyrics_editor("预填", "手动粘贴", True, False, ["初音未来"])
        self.assertIs(result, lyrics)
        open_editor.assert_called_once_with("预填", "手动粘贴", True, False, ["初音未来"])

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
        editor.assert_called_once_with("预填", use_hover=True, use_colors=False, charas=())

    def test_get_manual_lyrics_defaults_hover_off(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor",
                               return_value=mock.Mock(lyrics_jap="きみの")) as editor:
            models.song.get_manual_lyrics("预填")
        editor.assert_called_once_with("预填", use_hover=False, use_colors=False, charas=())

    def test_get_manual_lyrics_returns_empty_on_cancel(self):
        import models.song
        with mock.patch.object(models.song, "open_lyrics_editor", return_value=None):
            lyrics = models.song.get_manual_lyrics()
        self.assertIsNone(lyrics.lyrics_jap)
        self.assertIsNone(lyrics.lyrics_chs)
