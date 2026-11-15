"""榜单条目流程（见 utils/chart_editor.py）。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import chart_editor as ce
from utils import chart_nico

SPEC = chart_nico.CHARTS["utau"]
INFO = {"id": "sm8763104", "title": "週刊UTAUランキング　#52", "uploader": "あーさん",
        "description": "○集計期間：2009年11月1日0時～2009年11月8日0時",
        "date_jst": __import__("datetime").datetime(
            2009, 11, 10, 1, 6, tzinfo=__import__("datetime").timezone(
                __import__("datetime").timedelta(hours=9)))}


def work_with(rows=None):
    return ce.ChartWork(spec=SPEC, index=52, video_id="sm8763104", uploader="あーさん",
                        publish="2009年11月10日 00:06", start="2009年11月01日 00:00",
                        end="2009年11月08日 00:00", rule="2008", rows=list(rows or []))


class ExtraTest(unittest.TestCase):
    def test_parses_semicolon_pairs(self):
        self.assertEqual({"翻唱": "1", "评论权重": "0.97"}, ce.parse_extra("翻唱 = 1; 评论权重 = 0.97"))

    def test_accepts_fullwidth_signs_and_newlines(self):
        self.assertEqual({"翻唱": "1"}, ce.parse_extra("翻唱＝1\n"))

    def test_ignores_fragments(self):
        self.assertEqual({}, ce.parse_extra("随便写写"))
        self.assertEqual({}, ce.parse_extra(""))


class ChartRowTest(unittest.TestCase):
    def test_from_recognized_maps_columns_and_keeps_the_rest(self):
        row = ce.ChartRow.from_recognized({"本周": "1", "曲名": "Lilium", "得点": "12759",
                                          "评论权重": "0.97", "歌姬": "重音Teto"})
        self.assertEqual("1", row.rank)
        self.assertEqual("Lilium", row.title)
        self.assertEqual("重音Teto", row.vocalist)
        self.assertEqual({"评论权重": "0.97"}, row.other)

    def test_to_params_drops_empties_and_splits_the_id(self):
        row = ce.ChartRow(rank="25", title="白花", video_id="nm8676338", score="1223")
        params = row.to_params()
        self.assertEqual("8676338", params["id"])
        self.assertEqual("nm", params["前缀"])
        self.assertEqual("1223", params["得点"])
        self.assertNotIn("收藏", params)
        self.assertNotIn("P主", params)

    def test_other_params_survive_round_trip(self):
        row = ce.ChartRow(rank="1", title="A", other={"翻唱": "1", "color": "#FF9999"})
        self.assertEqual({"翻唱": "1", "color": "#FF9999"}, row.other)
        self.assertEqual("1", row.to_params()["翻唱"])
        text = row.other_text()
        restored = ce.ChartRow()
        restored.set_other_text(text)
        self.assertEqual(row.other, restored.other)

    def test_empty_rows_are_flagged(self):
        self.assertTrue(ce.ChartRow().is_empty())
        self.assertTrue(ce.ChartRow(rank="1").is_empty())      # 只填了名次也算空行
        self.assertFalse(ce.ChartRow(title="A").is_empty())
        self.assertFalse(ce.ChartRow(video_id="sm1").is_empty())
        self.assertFalse(ce.ChartRow(score="1223").is_empty())


class WorkTest(unittest.TestCase):
    def test_summary_counts_rows_and_missing_ids(self):
        work = work_with([ce.ChartRow(rank="1", title="A", video_id="sm1"),
                          ce.ChartRow(rank="2", title="B")])
        work.frames = 38
        work.source = "站上已有页面"
        self.assertEqual("2个名次 · 识别38帧 · 视频来自站上已有页面 · 1行没找到稿件",
                         work.summary())

    def test_title(self):
        self.assertEqual("周刊UTAU排行榜52", work_with().title)


class EnrichTest(unittest.TestCase):
    def test_fills_and_notes_the_missing_ones(self):
        rows = [ce.ChartRow(rank="1", title="Lilium"),
                ce.ChartRow(rank="2", title="无此曲")]
        answers = [{"id": "sm8673630", "曲名": "Lilium", "时间": "2009-11-01 02:39",
                    "P主": "むのーP", "歌姬": "重音Teto"},
                   {"id": "", "曲名": "无此曲", "时间": "", "P主": "", "歌姬": ""}]
        with mock.patch.object(chart_nico, "fill_row", side_effect=answers):
            ce.enrich_rows(rows)
        self.assertEqual("sm8673630", rows[0].video_id)
        self.assertEqual("2009-11-01 02:39", rows[0].time)
        self.assertEqual("むのーP", rows[0].producer)
        self.assertEqual("", rows[0].note)
        self.assertEqual("没找到niconico稿件", rows[1].note)


class WritePageTest(unittest.TestCase):
    def test_writes_the_rendered_wikitext(self):
        work = work_with([ce.ChartRow(rank="1", title="Lilium", video_id="sm8673630",
                                      score="12759", view="5358", comment="146",
                                      mylist="363", other={"评论权重": "0.97"})])
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(ce, "get_output_path", return_value=Path(folder)):
            path = ce.write_page(work)
            self.assertIsNotNone(path)
            text = Path(path).read_text(encoding="utf-8")
        self.assertEqual("周刊UTAU排行榜52.wikitext", Path(path).name)
        self.assertIn("|id = 8763104", text)
        self.assertIn("'''周刊UTAU排行榜 #52'''是在2009年11月10日由'''あーさん'''", text)
        self.assertIn("|id = 8673630\n|曲名 = Lilium", text)
        self.assertTrue(text.endswith("[[Category:周刊VOCAL Character & UTAU排行榜]]\n"))


class AskTest(unittest.TestCase):
    def test_single_chart_needs_no_question(self):
        self.assertIs(SPEC, ce.ask_chart())

    def test_index_accepts_noise(self):
        with mock.patch.object(ce, "prompt_response", return_value="第 52 期"):
            self.assertEqual(52, ce.ask_index(SPEC))

    def test_video_id_is_normalized(self):
        with mock.patch.object(ce, "prompt_response", return_value="https://nico.ms/sm8763104"):
            self.assertEqual("sm8763104", ce.ask_video_id(SPEC, 52))


class CollectWorkTest(unittest.TestCase):
    def test_collects_from_page_metadata_and_recognition(self):
        recognized = [{"本周": "1", "曲名": "Lilium", "得点": "12759", "再生": "5358",
                       "评论": "146", "收藏": "363"}]
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[(0.1, b"x")]), \
             mock.patch("utils.chart_recognize.recognize", return_value=recognized), \
             mock.patch("utils.chart_editor.enrich_rows") as enrich, \
             mock.patch.object(chart_nico, "find_bilibili", return_value="BV1xx"), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52)
        self.assertEqual("sm8763104", work.video_id)
        self.assertEqual("あーさん", work.uploader)
        self.assertEqual("2009年11月10日 00:06", work.publish)
        self.assertEqual("2009年11月01日 00:00", work.start)
        self.assertEqual("BV1xx", work.bilibili)
        self.assertEqual(1, len(work.rows))
        self.assertEqual("Lilium", work.rows[0].title)
        self.assertEqual("0.97", work.rows[0].other.get("评论权重"))
        self.assertEqual(1, work.frames)
        enrich.assert_called_once()

    def test_manual_link_when_search_fails(self):
        with mock.patch.object(chart_nico, "find_ranking_video", return_value=("", "")), \
             mock.patch.object(ce, "ask_video_id", return_value="sm8763104"), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[]), \
             mock.patch("utils.chart_editor.capture_in_browser", return_value=[]), \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52)
        self.assertEqual("手动填写", work.source)
        self.assertEqual([], work.rows)      # 一帧都没取到 → 表格留空等用户填

    def test_gives_up_when_the_user_declines(self):
        with mock.patch.object(chart_nico, "find_ranking_video", return_value=("", "")), \
             mock.patch.object(ce, "ask_video_id", return_value=""), \
             mock.patch("utils.ui.status"):
            self.assertIsNone(ce.collect_work(SPEC, 52))

    def test_deleted_video_returns_none(self):
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm1", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=None), \
             mock.patch("utils.ui.status"):
            self.assertIsNone(ce.collect_work(SPEC, 52))


if __name__ == "__main__":
    unittest.main()
