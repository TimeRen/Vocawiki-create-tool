"""曲名 → 站上「条目」名（见 utils/chart_entry.py）。"""
import unittest
from unittest import mock

from utils import chart_entry as cx


class CandidatesTest(unittest.TestCase):
    def test_quoted_song_name_is_a_candidate(self):
        self.assertEqual(["和音マコで『ワールドイズマイン』", "ワールドイズマイン"],
                         cx.candidates("和音マコで『ワールドイズマイン』"))

    def test_tail_noise_is_trimmed_step_by_step(self):
        self.assertEqual(["Packaged ～カバー～ 再エンコ版", "Packaged   再エンコ版", "Packaged"],
                         cx.candidates("Packaged ～カバー～ 再エンコ版"))

    def test_parenthetical_tail(self):
        self.assertEqual(["朝食（あさ）", "朝食"], cx.candidates("朝食（あさ）"))

    def test_plain_title_only(self):
        self.assertEqual(["Lilium"], cx.candidates("Lilium"))
        self.assertEqual([], cx.candidates(""))


class FillEntriesTest(unittest.TestCase):
    class Row:
        def __init__(self, title, entry=""):
            self.title = title
            self.entry = entry
            self.auto_entry = entry

    def test_entries_come_from_the_wiki(self):
        rows = [self.Row("炉心融解(Hard-R.K.mix)"), self.Row("Lilium")]
        table = {"炉心融解(Hard-R.K.mix)": "炉心融解"}
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            filled = cx.fill_entries(rows, lookup=lambda titles: table)
        self.assertEqual(1, filled)
        self.assertEqual("炉心融解", rows[0].entry)
        self.assertEqual("炉心融解", rows[0].auto_entry)
        self.assertEqual("", rows[1].entry, "站上没有就不填")

    def test_entry_equal_to_the_title_is_not_written(self):
        rows = [self.Row("Lilium")]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            filled = cx.fill_entries(rows, lookup=lambda titles: {"Lilium": "Lilium"})
        self.assertEqual(0, filled)
        self.assertEqual("", rows[0].entry)

    def test_user_dictionary_wins(self):
        rows = [self.Row("ワールドイズマイン")]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value="世界属于我"):
            filled = cx.fill_entries(rows, lookup=lambda titles: {"ワールドイズマイン": "别的"})
        self.assertEqual(1, filled)
        self.assertEqual("世界属于我", rows[0].entry)

    def test_manual_entry_is_kept(self):
        rows = [self.Row("炉心融解(Hard-R.K.mix)", entry="手填的")]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            self.assertEqual(0, cx.fill_entries(rows, lookup=lambda titles: {}))

    def test_lookup_failure_is_silent(self):
        rows = [self.Row("Lilium")]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""), \
             mock.patch.object(cx, "resolve", side_effect=RuntimeError("boom")):
            self.assertEqual(0, cx.fill_entries(rows))
        self.assertEqual("", rows[0].entry)


class CoverByPageTest(unittest.TestCase):
    """没有 n 站元数据时，用「站上已有同名歌曲页」判翻唱（用户 2026-11-17 的 You）。"""

    def row(self, title, no_info=True, cover=""):
        row = mock.Mock()
        row.title = title
        row.entry = ""
        row.auto_entry = ""
        row.cover = cover
        row.note = ""
        row.no_info = no_info
        return row

    def test_page_exists_means_cover(self):
        rows = [self.row("You"), self.row("今もあなたのために")]
        table = {"You": "You"}
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            cx.fill_entries(rows, lookup=lambda titles: table)
        self.assertEqual("1", rows[0].cover)
        self.assertIn("同名歌曲页", rows[0].note)
        self.assertEqual("", rows[1].cover, "站上没有这首歌的页面就不标")

    def test_live_metadata_wins(self):
        rows = [self.row("You", no_info=False)]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            cx.fill_entries(rows, lookup=lambda titles: {"You": "You"})
        self.assertEqual("", rows[0].cover, "元数据正常的稿件不按这条判")

    def test_manual_cover_is_kept(self):
        rows = [self.row("You", cover="0")]
        with mock.patch.object(cx.user_dict, "lookup_title", return_value=""):
            cx.fill_entries(rows, lookup=lambda titles: {"You": "You"})
        self.assertEqual("0", rows[0].cover)


if __name__ == "__main__":
    unittest.main()
