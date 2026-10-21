import unittest
from unittest import TestCase

from utils.string import strip_credit_lines


class TestString(TestCase):
    def test_auto_lj(self):
        pass


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

