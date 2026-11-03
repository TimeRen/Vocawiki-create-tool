import unittest
from typing import Callable
from unittest import TestCase

from utils.japanese import (is_hiragana, is_katakana, is_kana, is_kanji, furigana_local,
                            kana_to_romaji, to_photrans)

hiragana = "あいうえおをだってん"
katakana = "アイウエオダッテン"
kanji = "々我你他心踊重甜笺繰"


class Test(TestCase):
    def test(self, func: Callable[[str], bool] = None,
             true: str = "", false: str = ""):
        for c in true:
            self.assertTrue(func(c))
        for c in false:
            self.assertFalse(func(c))

    def test_is_hiragana(self):
        self.test(is_hiragana, hiragana, kanji + katakana)

    def test_is_katakana(self):
        self.test(is_katakana, katakana, kanji + hiragana)

    def test_is_kana(self):
        self.test(is_kana, hiragana + katakana, kanji)

    def test_is_kanji(self):
        self.test(is_kanji, kanji, hiragana + katakana)

    def test_furigana_local(self):
        original = "接木（つぎき）のような時制の不連続。広場に殺（し）した感嘆符の葬列。"
        expected = "{{photrans|接木|つぎき}}のような時制の不連続。広場に{{photrans|殺|し}}した感嘆符の葬列。"
        self.assertEqual(expected, furigana_local(original))
        original = "水底に沈(しず)く彫刻の太()陽。"
        expected = "水底に{{photrans|沈|しず}}く彫刻の太()陽。"
        self.assertEqual(expected, furigana_local(original))
        original = "水底に沈(测试)く彫刻の（の）太陽。"
        self.assertEqual(original, furigana_local(original))

    def test_ruby_is_normalised_to_photrans(self):
        """站上的注音模板是 `{{Photrans}}`；`{{ruby}}` 要统一改回来（用户 2026-11-03）。"""
        original = "{{ruby|興奮|こうふん}}してきた{{ruby|人様|ひとさま}}の{{ruby|業|ぎょう}}で"
        expected = "{{photrans|興奮|こうふん}}してきた{{photrans|人様|ひとさま}}の{{photrans|業|ぎょう}}で"
        self.assertEqual(expected, to_photrans(original))
        self.assertEqual(expected, furigana_local(original))
        # 大小写 / 全角半角不同的写法也认
        self.assertEqual("{{photrans|漢字|かんじ}}", to_photrans("{{Ruby|漢字|かんじ}}"))
        self.assertEqual("{{photrans|腸|はらわた}}", to_photrans("{{ルビ|腸|はらわた}}"))

    def test_ruby_that_is_not_furigana_is_left_alone(self):
        """`{{ruby|2=罗马音|1=…}}`（整行罗马音）与「第二个参数是罗马音」的都不是注音。"""
        romaji_line = "{{ruby|2=zetsubou no|1={{Photrans|絶望|ぜつぼう}}の}}"
        self.assertEqual(romaji_line, to_photrans(romaji_line))
        self.assertEqual("{{ruby|漢字|kanji}}", to_photrans("{{ruby|漢字|kanji}}"))

    def test_photrans_is_idempotent(self):
        text = "{{photrans|接木|つぎき}}のような時制"
        self.assertEqual(text, furigana_local(furigana_local(text)))
        self.assertEqual(text, to_photrans(text))

    def test_kana_to_romaji(self):
        """同名条目的 P主名要把假名写成罗马音（站内条目：`Eureka(柊kirai)`）。"""
        self.assertEqual("kirai", kana_to_romaji("キライ"))
        self.assertEqual("nanashi", kana_to_romaji("ななし"))
        self.assertEqual("mikitoP", kana_to_romaji("みきとP"))
        self.assertEqual("柊kirai", kana_to_romaji("柊キライ"))         # 汉字原样留给调用方
        self.assertEqual("Hobo日P", kana_to_romaji("Hobo日P"))

    def test_kana_to_romaji_handles_digraphs_and_small_kana(self):
        self.assertEqual("kya", kana_to_romaji("きゃ"))
        self.assertEqual("sha", kana_to_romaji("シャ"))
        self.assertEqual("ja", kana_to_romaji("じゃ"))
        self.assertEqual("fa", kana_to_romaji("ふぁ"))
        self.assertEqual("chi", kana_to_romaji("ち"))
        self.assertEqual("tsu", kana_to_romaji("つ"))
        self.assertEqual("shi", kana_to_romaji("し"))

    def test_kana_to_romaji_handles_sokuon_and_long_vowels(self):
        self.assertEqual("rokku", kana_to_romaji("ロック"))             # っ 把辅音写两遍
        self.assertEqual("koohii", kana_to_romaji("コーヒー"))          # ー 重复上一个元音
        self.assertEqual("kyaa", kana_to_romaji("きゃー"))             # 长音跟在拗音后面

    def test_kana_to_romaji_keeps_unknown_characters(self):
        self.assertEqual("", kana_to_romaji(""))
        self.assertEqual("40mP", kana_to_romaji("40mP"))
        self.assertEqual("ヷ", kana_to_romaji("ヷ"))                    # 表里没有的假名原样返回

    def test_kana_to_romaji_keeps_trailing_sokuon(self):
        self.assertEqual("mi", kana_to_romaji("みっ"))


if __name__ == "__main__":
    unittest.main()
