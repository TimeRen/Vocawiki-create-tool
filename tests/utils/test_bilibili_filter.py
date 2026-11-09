"""`utils/bilibili_filter.py` 的测试（不联网）：相关性、黑名单、白屏兜底。

规则移植自 [houliabc/bilibili-search-purify](https://github.com/houliabc/bilibili-search-purify)
（MIT）：标题里没有搜索词任何片段的结果算无关；擦边 / 标题党 / 引流那类词进黑名单；
一条都不剩时**整批保留**（脚本的白屏兜底）。用户 2026-11-09 要求把这两条接进工具的 B 站搜索。
"""
from unittest import TestCase, mock

from utils import bilibili_filter as bf


class CleanTextTest(TestCase):
    def test_output_is_lowercase_without_punctuation_or_spaces(self):
        # 拉丁字母转小写；日文假名没有大小写，原样留着
        self.assertEqual("初音ミクハナビ", bf.clean_text("【初音ミク】 ハナビ！"))
        self.assertEqual("datekentear", bf.clean_text("DATEKEN - *tear* ！"))

    def test_stop_words_are_dropped(self):
        self.assertEqual("花火很好", bf.clean_text("花火是很好的呀"))

    def test_keyword_highlight_markup_is_stripped(self):
        """接口的标题是 `<em class="keyword">ネハン</em> / 雄之助`，比对要按可见文字。"""
        self.assertEqual("ネハン雄之助", bf.clean_text('<em class="keyword">ネハン</em> / 雄之助'))
        self.assertTrue(bf.is_relevant('<em class="keyword">ネハン</em> / 雄之助', "ネハン"))


class RelevanceTest(TestCase):
    def test_the_whole_keyword_matches(self):
        self.assertTrue(bf.is_relevant("涅槃/ネハン", "ネハン"))

    def test_a_two_char_fragment_is_enough(self):
        """脚本的核心规则：`打ち上げ花火` 能命中标题里的 `花火`（片段按 2 字切）。"""
        self.assertTrue(bf.is_relevant("【初音ミク】打ち上げ花火", "花火"))
        self.assertTrue(bf.is_relevant("打ち上げ花火 / 初音ミク", "ち上げ"))
        self.assertTrue(bf.is_relevant("【初音ミク】打ち上げ花火", "打ち上げ"))

    def test_an_unrelated_title_is_rejected(self):
        self.assertFalse(bf.is_relevant("震惊！家人们谁懂啊", "ネハン"))
        self.assertFalse(bf.is_relevant("2026年搞笑合集", "ハナビ"))

    def test_latin_tokens_are_matched_whole(self):
        """拉丁字母不能按 2 字片段比（`de`、`at` 会命中任何标题），只认整词。"""
        self.assertTrue(bf.is_relevant("DATEKEN - *tear*", "dateken tear"))
        self.assertTrue(bf.is_relevant("*tear* / DATEKEN", "dateken tear"))
        self.assertFalse(bf.is_relevant("deadline", "dateken"))

    def test_an_empty_keyword_matches_everything(self):
        self.assertTrue(bf.is_relevant("随便什么", ""))
        self.assertTrue(bf.is_relevant("随便什么", "   "))


class BlacklistTest(TestCase):
    def test_a_builtin_word_is_reported_with_its_group(self):
        reason = bf.spam_reason("震惊！老司机带你飙车")
        self.assertTrue(reason)
        self.assertTrue(reason.startswith("标题党：") or reason.startswith("擦边："), reason)

    def test_a_clean_title_has_no_reason(self):
        self.assertEqual("", bf.spam_reason("ハナビ / 丑三つ時 feat. GUMI"))

    def test_highlight_markup_does_not_hide_a_blacklisted_word(self):
        self.assertTrue(bf.spam_reason('<em class="keyword">福利</em>合集'))

    def test_the_song_name_exempts_a_blacklisted_word(self):
        """《泳装》《震惊》这种正经曲名不能被自己的名字判成垃圾（脚本的 keyword 豁免）。"""
        self.assertEqual("", bf.spam_reason("泳装 / 初音ミク", keywords=["泳装"]))
        self.assertEqual("", bf.spam_reason("【MV】震惊", keywords=["震惊"]))

    def test_user_blocked_up_is_reported(self):
        with mock.patch.object(bf, "extra_blacklist", return_value=[]), \
             mock.patch.object(bf, "extra_blacklist_up", return_value=["某搬运工"]):
            self.assertIn("某搬运工", bf.spam_reason("ハナビ", author="某搬运工"))
            self.assertEqual("", bf.spam_reason("ハナビ", author="丑三つ時"))

    def test_user_words_win_over_the_song_name_exemption(self):
        """用户自己写的屏蔽词不受豁免：写了就是不要。"""
        with mock.patch.object(bf, "extra_blacklist", return_value=[bf.re.compile("切片")]), \
             mock.patch.object(bf, "extra_blacklist_up", return_value=[]):
            self.assertTrue(bf.spam_reason("ハナビ 切片版", keywords=["ハナビ 切片版"]))

    def test_a_broken_user_regex_is_ignored(self):
        with mock.patch.object(bf, "extra_blacklist", return_value=[]):
            self.assertEqual([], bf._user_words("/(未闭合/"))


class PurifyItemsTest(TestCase):
    def test_irrelevant_items_are_dropped(self):
        items = [{"title": "涅槃/ネハン"}, {"title": "震惊！家人们谁懂啊"}]
        kept, dropped = bf.purify_items(items, "ネハン", enabled=True)
        self.assertEqual(["涅槃/ネハン"], [item["title"] for item in kept])
        self.assertEqual(1, dropped)

    def test_everything_irrelevant_keeps_the_whole_batch(self):
        """脚本的白屏兜底：宁可候选脏一点，也不能一首歌都搜不到。"""
        items = [{"title": "毫无关系"}, {"title": "另一个无关视频"}]
        kept, dropped = bf.purify_items(items, "ネハン", enabled=True)
        self.assertEqual(2, len(kept))
        self.assertEqual(0, dropped)

    def test_user_blocked_words_are_dropped(self):
        items = [{"title": "涅槃/ネハン"}, {"title": "ネハン 切片"}]
        with mock.patch.object(bf, "extra_blacklist", return_value=[bf.re.compile("切片")]), \
             mock.patch.object(bf, "extra_blacklist_up", return_value=[]):
            kept, dropped = bf.purify_items(items, "ネハン", enabled=True)
        self.assertEqual(["涅槃/ネハン"], [item["title"] for item in kept])
        self.assertEqual(1, dropped)

    def test_user_blocked_words_survive_no_fallback(self):
        """白屏兜底只救相关度：用户自己屏蔽的东西不会因为「一条不剩」又冒出来。"""
        items = [{"title": "毫无关系 切片"}]
        with mock.patch.object(bf, "extra_blacklist", return_value=[bf.re.compile("切片")]), \
             mock.patch.object(bf, "extra_blacklist_up", return_value=[]):
            kept, dropped = bf.purify_items(items, "ネハン", enabled=True)
        self.assertEqual([], kept)
        self.assertEqual(1, dropped)

    def test_the_switch_turns_everything_off(self):
        items = [{"title": "毫无关系"}]
        self.assertEqual((items, 0), bf.purify_items(items, "ネハン", enabled=False))

    def test_the_default_comes_from_the_config(self):
        from config import config

        with mock.patch.object(config, "get_config",
                               return_value=mock.Mock(bilibili_purify=False)):
            self.assertEqual(([{"title": "毫无关系"}], 0),
                             bf.purify_items([{"title": "毫无关系"}], "ネハン"))
