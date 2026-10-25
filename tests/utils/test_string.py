import unittest
from unittest import TestCase

from utils.string import (auto_lj, drop_lead_intro, is_label_line, needs_lang_tag,
                          strip_credit_lines, strip_translator_note, title_match_key)


class TestString(TestCase):
    def test_auto_lj(self):
        pass


class AutoLjTest(TestCase):
    """`{{lj}}` 只套在**真有日文**的名字上（用户 2026-10-24：voca.wiki diff 275395）。

    《-２５-》的 `|歌曲名称` 从 `{{lj|-２５-}}` 改成 `-２５-` —— 整个名字就是全角数字加连字符，
    按半角算之后一个日文字都没有。同一页的 `{{lj|13943号室}}`（汉字）、
    `{{lj|VanaN’Ice LIVE 2018 …}}`（弯引号）都还套着。
    """

    def test_full_width_digits_and_symbols_need_no_lang_tag(self):
        self.assertFalse(needs_lang_tag("-\uff12\uff15-"))
        self.assertEqual("-\uff12\uff15-", auto_lj("-\uff12\uff15-"))

    def test_kanji_and_kana_still_get_the_tag(self):
        self.assertEqual("{{lj|13943\u53f7\u5ba4}}", auto_lj("13943\u53f7\u5ba4"))
        self.assertEqual("{{lj|\u9ce5\u306e\u8a69}}", auto_lj("\u9ce5\u306e\u8a69"))

    def test_non_ascii_punctuation_also_gets_the_tag(self):
        """`VanaN’Ice …` 那种弯引号照旧套 `{{lj}}`（站上就是这么写的）。"""
        name = "VanaN\u2019Ice LIVE 2018"
        self.assertTrue(needs_lang_tag(name))
        self.assertEqual("{{lj|" + name + "}}", auto_lj(name))

    def test_ascii_is_left_alone(self):
        self.assertEqual("Last Color", auto_lj("Last Color"))
        self.assertEqual("", auto_lj(""))


class TitleMatchKeyTest(TestCase):
    """歌名比较用的键：全角 / 半角、空白、大小写都算同一个（用户 2026-10-23 报《5℃》）。"""

    def test_full_width_digits_match_half_width(self):
        self.assertEqual(title_match_key("5℃"), title_match_key("５℃"))
        self.assertEqual(title_match_key("MELT"), title_match_key("ＭＥＬＴ"))

    def test_spaces_and_case_are_ignored(self):
        self.assertEqual(title_match_key("5 ℃"), title_match_key("5℃"))
        self.assertEqual(title_match_key("Fly away"), title_match_key("flyaway"))

    def test_different_titles_stay_different(self):
        self.assertNotEqual(title_match_key("5℃"), title_match_key("6℃"))
        self.assertNotEqual(title_match_key("メルト"), title_match_key("メルт"))

    def test_empty_input_is_safe(self):
        self.assertEqual("", title_match_key(""))
        self.assertEqual("", title_match_key(None))


class StripCreditLinesTest(TestCase):
    """歌词里的资讯行（作詞 / 作曲 / 編曲 / 歌 …）要摘掉。

    用户 2026-10-16 报：「從連結填充」填进来的歌词里带着

        作詞：ねこぼーろ(ササノマリイ)

        作曲：ねこぼーろ(ササノマリイ)

        編曲：ねこぼーろ(ササノマリイ)

        歌：[初音ミク](https://w.atwiki.jp/vocaloidchly/pages/178.html)

    这类内容 —— 它们不是歌词，填进「待归类歌词」/「中文」栏会被当成歌词行。
    """

    def test_removes_the_reported_credit_block(self):
        text = ("作詞：ねこぼーろ(ササノマリイ)\n\n作曲：ねこぼーろ(ササノマリイ)\n\n"
                "編曲：ねこぼーろ(ササノマリイ)\n\n"
                "歌：[初音ミク](https://w.atwiki.jp/vocaloidchly/pages/178.html)\n\n"
                "想一眼就看穿那橙色之物吧?\n這點我跟你真是一丘之貉呢")
        self.assertEqual("想一眼就看穿那橙色之物吧?\n這點我跟你真是一丘之貉呢",
                         strip_credit_lines(text))

    def test_removes_the_forms_the_sources_actually_use(self):
        # 真机实测：b 站简介「曲　動画：Tonbi　https://…」「マスタリング：やまぐち　…」，
        # 巴哈姆特「作詞、作曲:Shin」；网易云的 LRC 头是「作词 : …」
        self.assertEqual("", strip_credit_lines("曲\u3000動画：Tonbi\u3000https://twitter.com/Tonbism"))
        self.assertEqual("", strip_credit_lines("マスタリング：やまぐち\u3000https://twitter.com/ym_dsu"))
        self.assertEqual("", strip_credit_lines("歌：初音ミク"))
        self.assertEqual("", strip_credit_lines("作詞、作曲:Shin"))
        self.assertEqual("", strip_credit_lines("作詞作曲：ねこぼーろ"))
        self.assertEqual("", strip_credit_lines("◆作詞：ねこぼーろ"))
        self.assertEqual("", strip_credit_lines("Music, Lyrics: nekobolo"))
        self.assertEqual("", strip_credit_lines("作词 : ねこぼーろ"))

    def test_removes_link_only_lines(self):
        self.assertEqual("", strip_credit_lines("https://w.atwiki.jp/vocaloidchly/pages/178.html"))
        self.assertEqual("", strip_credit_lines("<https://www.bilibili.com/video/BV1pj421Q7V3>"))
        self.assertEqual("", strip_credit_lines("[初音ミク](https://w.atwiki.jp/vocaloidchly/pages/178.html)"))

    def test_removes_the_other_debris_the_sources_leave(self):
        """真机实测的杂物行（b 站简介 / 巴哈姆特）：视频号、纯数字、分隔线、版本标签、网址说明。"""
        for junk in ("sm43424916", "失效补档av36439", "■前作「clock lock works」sm8933166",
                     "20", "-----------------------------------------", "www", "ｗｗｗ",
                     "Sheena版:", "ミスターレイヴン cover版:", "Credit", "歌詞", "原文",
                     "offvocal → https://drive.google.com/drive/u/0/folders/1VT7",
                     "Streaming & DL：https://nodee.net/a/zpsewrgrf7qi",
                     "■ブログ　http://yakari.blog17.fc2.com",
                     "絵：カトレア　https://twitter.com/katorea_0139",
                     "作品类型：原创曲", "简介补充：第一次调术力口！耶！"):
            self.assertEqual("", strip_credit_lines(junk), junk)

    def test_removes_atwiki_edit_notes(self):
        """AtWiki 末尾的「12.22　事隔兩年餘修正」不是歌词（用户 2026-10-18 报）。"""
        self.assertEqual("最後一句", strip_credit_lines("最後一句\n\n12.22\u3000事隔兩年餘修正"))
        self.assertEqual("", strip_credit_lines("2010.12.22 修正"))
        self.assertEqual("", strip_credit_lines("12/22 更新"))

    def test_removes_an_atwiki_tag_line(self):
        """AtWiki 页尾的 `tag：P名,曲名,…` 是页面元数据（真机 chly《日蝕》）。"""
        self.assertEqual("最後一句",
                         strip_credit_lines("最後一句\ntag：すんｚりヴぇｒ,鏡音リン・鏡音レンな,も"))
        self.assertEqual("", strip_credit_lines("Tag: 初音ミク"))
        # 只在行首才算标签：歌词里写着 tag 这个词不能删
        self.assertEqual("my tag is blue", strip_credit_lines("my tag is blue"))

    def test_removes_a_bracketed_label_line(self):
        """`【歌词】` 这种带括号的栏目标签不是歌词（用户 2026-10-22 报《Short Hand》）。"""
        self.assertTrue(is_label_line("【歌词】"))
        self.assertTrue(is_label_line("[歌詞]"))
        self.assertTrue(is_label_line("「歌词」"))
        self.assertTrue(is_label_line("歌词"))
        self.assertFalse(is_label_line("【這是歌名】"))
        self.assertFalse(is_label_line("这是一句歌词"))
        self.assertEqual("第一句\n第二句",
                         strip_credit_lines("【歌词】\n第一句\n第二句"))
        self.assertEqual("第一句\n第二句",
                         strip_credit_lines("[Translation]\n第一句\n第二句"))


class StripTranslatorNoteTest(TestCase):
    """译者名后面的括号是自述，不是名字的一部分（用户 2026-10-25 报）。

    实测 w.atwiki.jp/vocaloidchly/pages/2231.html 的表头：`翻譯：cyataku(不當之處敬請指正)`。
    """

    def test_drops_a_trailing_note(self):
        self.assertEqual("cyataku", strip_translator_note("cyataku(不當之處敬請指正)"))
        self.assertEqual("cyataku", strip_translator_note("cyataku（不當之處敬請指正）"))
        self.assertEqual("yanao", strip_translator_note("yanao (可自由取用，請註明出處)"))
        self.assertEqual("rufus0616", strip_translator_note("rufus0616(日語學習中)"))
        self.assertEqual("ak", strip_translator_note("ak(如有錯誤歡迎指正)"))

    def test_plain_names_are_untouched(self):
        for name in ("Cilde", "yanao", "cyataku", "婁", ""):
            with self.subTest(name=name):
                self.assertEqual(name, strip_translator_note(name))

    def test_a_reading_that_belongs_to_the_name_is_kept(self):
        self.assertEqual("紫(し)", strip_translator_note("紫(し)"))

    def test_only_the_note_part_is_dropped(self):
        self.assertEqual("A(b)", strip_translator_note("A(b)(轉載請註明)"))

    def test_a_note_without_a_name_stays(self):
        self.assertEqual("(不當之處敬請指正)", strip_translator_note("(不當之處敬請指正)"))

    def test_surrounding_spaces_are_trimmed(self):
        self.assertEqual("cyataku", strip_translator_note("  cyataku(不當之處敬請指正) "))


class DropLeadIntroTest(TestCase):
    """标签**之前**的说明不是歌词（用户 2026-10-22 报《Short Hand》）。"""

    def test_drops_the_caption_before_the_label(self):
        text = ("这是一个关于时钟的短针爱上长针的故事\n\n【歌词】\n\n"
                "孤独地等待　1小时5分多一点\n连轻轻擦肩而过都做不到")
        self.assertEqual("孤独地等待　1小时5分多一点\n连轻轻擦肩而过都做不到",
                         strip_credit_lines(drop_lead_intro(text)))

    def test_keeps_the_text_when_there_is_no_label(self):
        text = "这是一句歌词\n【这不是标签】"
        self.assertEqual(text, drop_lead_intro(text))

    def test_keeps_the_text_when_the_label_is_last(self):
        """标签后面什么都不剩（`歌词` 单独一行收尾）时不要整段丢掉。"""
        text = "第一句\n第二句\n【歌词】"
        self.assertEqual(text, drop_lead_intro(text))

    def test_only_cuts_at_the_first_label(self):
        text = "说明\n【歌词】\n第一句\n[歌詞]\n第二句"
        self.assertEqual("第一句\n[歌詞]\n第二句", drop_lead_intro(text))

    def test_keeps_real_lyric_lines(self):
        text = ("曲がり角で君を待つ\n歌詞のない歌\n唱歌的旋律\nMusic を聴いて\n曲名：テスト\n"
                "作詞の話をしよう\n行け！\nFly away!")
        self.assertEqual(text, strip_credit_lines(text))

    def test_description_mode_drops_isolated_sentences(self):
        """简介（笔记式文本）里单独成段的整句话是宣传语，不是歌词 —— 只在简介模式删。

        `Music 袖野あらわ` / `Illust 薬屋` 这种「角色 + 空白 + 名字」也在简介模式里删
        （真机 BV1DH4y1E7aA 的写法）。
        """
        self.assertEqual("", strip_credit_lines("期待する方が悪い。", description=True))
        self.assertEqual("", strip_credit_lines("www\n\n总而言之3千粉啦，感谢大家的支持与厚爱！！！",
                                               description=True))
        self.assertEqual("", strip_credit_lines("Music 袖野あらわ", description=True))
        self.assertEqual("", strip_credit_lines("Illust 薬屋", description=True))
        self.assertEqual("", strip_credit_lines("Movie Fujiyama", description=True))
        self.assertEqual("", strip_credit_lines("special thx さようなら", description=True))
        # 不开简介模式时都留着（歌词页那些来源里它们可能就是歌词）
        self.assertEqual("期待する方が悪い。", strip_credit_lines("期待する方が悪い。"))
        self.assertEqual("Music 袖野あらわ", strip_credit_lines("Music 袖野あらわ"))

    def test_description_mode_keeps_lyric_blocks(self):
        """多行歌词段不会被当成宣传语：哪怕整段每行都以「！」收尾。"""
        lyrics = "君が好きだ！\n僕も好きだ！\n\nWow!\nそうさFly away!"
        self.assertEqual(lyrics, strip_credit_lines(lyrics, description=True))
        # 英文歌词行（多个英文词）也不按「角色 + 名字」删
        self.assertEqual("Music is my life", strip_credit_lines("Music is my life", description=True))
        self.assertEqual("Vocal が きこえる", strip_credit_lines("Vocal が きこえる", description=True))

    def test_removes_translator_notes(self):
        """译者 / 转载者的自述行不是歌词（用户 2026-10-16 报的评论第一行就是这种）。"""
        for note in ("歌词渣翻。大部分机翻+个人理解。如有错误，欢迎指正",
                     "渣翻，如有錯誤歡迎指正", "機翻+个人理解，有错请指正", "翻譯練習，請多指教",
                     "轉載請註明出處", "本视频仅供学习交流，严禁转载", "版权归原作者所有",
                     "歌词转自网易云音乐", "以下为歌词", "歌詞如下"):
            self.assertEqual("", strip_credit_lines(note), note)
            self.assertEqual("歌詞本文", strip_credit_lines(note + "\n歌詞本文"), note)

    def test_removes_transcription_notes(self):
        """「（動画より書き起こし）」这种**来源说明**不是歌词（真机《咲うわ。》正文第一行）。"""
        for note in ("（動画より書き起こし）", "（動画より書きおこし）", "※耳コピです",
                     "歌詞は動画より書き起こし"):
            self.assertEqual("", strip_credit_lines(note), note)
            self.assertEqual("歌詞本文", strip_credit_lines(note + "\n歌詞本文"), note)

    def test_keeps_lyric_lines_that_merely_look_vaguely_like_notes(self):
        text = ("未經允許的溫柔\n原曲のまま歌う\n水平有限でも\n能力有限でも歌う\n"
                "搬运着梦想\n自転車で行こう")
        self.assertEqual(text, strip_credit_lines(text))

    def test_squeezes_blank_lines(self):
        self.assertEqual("いち\n\nに", strip_credit_lines("\n\nいち\n\n\n\nに\n\n"))
        self.assertEqual("", strip_credit_lines(""))

    def test_is_idempotent(self):
        once = strip_credit_lines("作詞：x\n\n歌詞本文\n")
        self.assertEqual(once, strip_credit_lines(once))
        self.assertEqual("歌詞本文", once)


if __name__ == "__main__":
    unittest.main()

