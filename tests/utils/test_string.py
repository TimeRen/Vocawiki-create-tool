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

    def test_keeps_real_lyric_lines(self):
        text = ("曲がり角で君を待つ\n歌詞のない歌\n唱歌的旋律\nMusic を聴いて\n曲名：テスト\n"
                "作詞の話をしよう")
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

