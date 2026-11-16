"""榜单条目流程（见 utils/chart_editor.py）。"""
import re
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock
from utils import chart_cache, chart_editor as ce
from utils import chart_nico, user_dict

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


class ProgressReporterTest(unittest.TestCase):
    """底部「正在…（N%）」：分段换算 + 只往上走。"""

    def test_percent_follows_the_stage_ranges(self):
        seen = []
        reporter = ce.ProgressReporter(seen.append)
        reporter.at("find", "正在找视频…")
        reporter.at("download", "正在下载…", 0.5)
        reporter.at("extract", "正在抽帧…", 0.0)
        reporter.at("recognize", "正在识别…", 0.5)
        reporter.at("enrich", "正在补全…", 1.0)
        reporter.done("完成")
        self.assertEqual(["正在找视频…（0%）", "正在下载…（25%）", "正在抽帧…（45%）",
                          "正在识别…（70%）", "正在补全…（96%）", "完成（100%）"], seen)

    def test_percent_never_goes_backwards(self):
        seen = []
        reporter = ce.ProgressReporter(seen.append)
        reporter.at("download", "正在下载…", 0.8)
        reporter.at("download", "正在下载…", 0.1)      # yt-dlp 换流时会重算
        reporter.at("extract", "正在抽帧…", 0.0)
        percents = [int(text.split("（")[1].rstrip("%）")) for text in seen]
        self.assertEqual(percents, sorted(percents))

    def test_unknown_stage_or_fraction_is_safe(self):
        seen = []
        reporter = ce.ProgressReporter(seen.append)
        reporter.at("nope", "随便…", "不是数字")
        self.assertEqual(["随便…（0%）"], seen)


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

    def test_brackets_become_the_vocalist(self):
        """`【重音テト】世界の終わり` → 曲名是「世界の終わり」，歌姬是「重音Teto」。"""
        row = ce.ChartRow.from_recognized({"本周": "3",
                                           "曲名": "【重音テト】世界の終わり【オリジナル】"})
        self.assertEqual("世界の終わり", row.title)
        self.assertEqual("重音Teto", row.vocalist)
        self.assertEqual("【重音テト】世界の終わり【オリジナル】", row.raw_title)

    def test_badge_adds_color_and_bottom_column(self):
        row = ce.ChartRow(rank="1", title="A", video_id="sm1", badge="TOPIC")
        params = row.to_params()
        self.assertEqual("#01BB00", params["color"])
        self.assertEqual("{{color|#01BB00|T O P I C}}", params["bottom-column"])

    def test_user_colour_beats_the_badge_table(self):
        row = ce.ChartRow(rank="1", title="A", video_id="sm1", badge="PICKUP",
                          other={"color": "#123456"})
        self.assertEqual("#123456", row.to_params()["color"])

    def test_cover_column_writes_the_site_param(self):
        """「翻唱」列非空就写 `|翻唱 = 1`；空着不写。"""
        self.assertEqual("1", ce.ChartRow(rank="1", title="A", video_id="sm1",
                                          cover="1").to_params()["翻唱"])
        self.assertNotIn("翻唱", ce.ChartRow(rank="1", title="A", video_id="sm1").to_params())

    def test_cover_written_in_other_params_still_works(self):
        row = ce.ChartRow(rank="1", title="A", video_id="sm1", other={"翻唱": "1"})
        self.assertEqual("1", row.to_params()["翻唱"])

    def test_dict_round_trip_for_the_cache(self):
        row = ce.ChartRow(rank="1", title="A", video_id="sm1", badge="PICKUP", section="番外",
                          other={"翻唱": "1"}, raw_title="【x】A", auto_entry="条目")
        restored = ce.ChartRow.from_dict(row.to_dict())
        self.assertEqual(row.to_dict(), restored.to_dict())
        self.assertEqual("番外", restored.section)
        self.assertEqual({"翻唱": "1"}, restored.other)


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
        self.assertEqual("没找到稿件（曲名可能有乱码，建议手填id）", rows[1].note)

    def test_vocalist_in_the_producer_column_is_moved(self):
        """截图 5：模型把歌姬名抄进了「P主」，要挪到「歌姬」并让补全重新填 P主。"""
        rows = [ce.ChartRow(rank="110", title="曲名A", producer="重音テト", vocalist="")]
        self.assertEqual(1, ce.fix_row_names(rows))
        self.assertEqual("", rows[0].producer)
        self.assertEqual("重音Teto", rows[0].vocalist)

    def test_producer_equal_to_the_title_is_dropped(self):
        rows = [ce.ChartRow(rank="1", title="Lilium", producer="Lilium")]
        self.assertEqual(1, ce.fix_row_names(rows))
        self.assertEqual("", rows[0].producer)

    def test_a_real_producer_is_left_alone(self):
        rows = [ce.ChartRow(rank="1", title="Lilium", producer="むのーP")]
        self.assertEqual(0, ce.fix_row_names(rows))
        self.assertEqual("むのーP", rows[0].producer)

    def test_progress_is_reported_per_row_batch(self):
        rows = [ce.ChartRow(rank=str(index), title=f"曲{index}") for index in range(1, 7)]
        seen = []
        with mock.patch.object(chart_nico, "fill_row", return_value={"id": ""}):
            ce.enrich_rows(rows, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual((1, 6), seen[0])
        self.assertEqual((6, 6), seen[-1])


class WritePageTest(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        user_dict.set_path(Path(self._folder.name) / "user_names.yaml")

    def tearDown(self):
        user_dict.set_path(None)
        self._folder.cleanup()

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

    def test_learned_names_are_recorded_when_generating(self):
        """用户手填的「条目」与归一后的歌姬，在生成条目时记进 user_names.yaml。"""
        work = work_with([
            ce.ChartRow(rank="1", title="ワールドイズマイン", video_id="sm1", score="100",
                        entry="世界属于我", auto_entry=""),
            ce.ChartRow(rank="2", title="A", video_id="sm2", score="90",
                        raw_vocalist="和音マコ", vocalist="和音Mako")])
        with mock.patch.object(ce, "get_output_path", return_value=self._folder.name):
            ce.write_page(work)
        self.assertEqual("世界属于我", user_dict.lookup_title("ワールドイズマイン"))
        self.assertEqual("和音Mako", user_dict.lookup_vocalist("和音マコ"))

    def test_untouched_values_are_not_recorded(self):
        """程序自己算出来的「条目」/ 歌姬不算手填，不要写进词典。"""
        work = work_with([ce.ChartRow(rank="1", title="白花", video_id="sm1", score="100",
                                      entry="白花(usimitsudoki)",
                                      auto_entry="白花(usimitsudoki)"),
                          ce.ChartRow(rank="2", title="A", video_id="sm2", score="90",
                                      raw_vocalist="重音Teto", vocalist="重音Teto")])
        with mock.patch.object(ce, "get_output_path", return_value=self._folder.name):
            ce.write_page(work)
        self.assertEqual("", user_dict.lookup_title("白花"))
        self.assertEqual("", user_dict.lookup_vocalist("重音Teto"))


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
    def setUp(self):
        # 缓存放到临时目录：否则上一轮测试写下的 utau-52.json 会让下一轮直接问「沿用？」
        self._folder = tempfile.TemporaryDirectory()
        chart_cache.set_folder(self._folder.name)

    def tearDown(self):
        chart_cache.set_folder(None)
        self._folder.cleanup()

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

    def test_progress_callback_only_gets_text(self):
        """状态栏回调只收一句话（带百分比，且百分比不能往回跳）。"""
        seen: List[object] = []
        recognized = [{"本周": "1", "曲名": "Lilium", "得点": "12759"}]
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[(0.1, b"x")]), \
             mock.patch("utils.chart_recognize.recognize", return_value=recognized), \
             mock.patch.object(chart_nico, "fill_row", return_value={"id": "sm1"}), \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52, progress=seen.append, reuse=lambda *args: False)
        self.assertEqual(1, len(work.rows))
        self.assertTrue(seen)
        self.assertTrue(all(isinstance(text, str) for text in seen), seen)
        self.assertTrue(all("%" in text for text in seen), seen)
        percents = [int(re.search(r"（(\d+)%）", text).group(1)) for text in seen]
        self.assertEqual(percents, sorted(percents), "百分比不能往回跳")
        self.assertEqual(100, percents[-1])

    def test_recognition_gets_the_main_limit_from_config(self):
        """「主榜只收录前 20 名」的阈值从配置传给识别（`chart.main_limit`）。"""
        recognized = [{"本周": "1", "曲名": "Lilium", "得点": "12759"}]
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[(0.1, b"x")]), \
             mock.patch("utils.chart_recognize.recognize",
                        return_value=recognized) as recognize, \
             mock.patch.object(ce, "enrich_rows"), \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            ce.collect_work(SPEC, 52, reuse=lambda *args: False)
        self.assertEqual(20, recognize.call_args.kwargs.get("main_limit"))

    def test_enrich_uses_the_submission_window(self):
        """补全稿件时把「集計対象動画期間」当时间窗传给搜索（短关键词也能用）。"""
        rows = [ce.ChartRow(rank="1", title="Lilium")]
        seen = {}
        work = work_with(rows)
        work.description = "○集計対象動画期間：2009年10月24日18時～2009年11月7日18時"
        with mock.patch.object(chart_nico, "fill_row",
                               side_effect=lambda row, search=True, window=None:
                               seen.update(window=window) or row):
            ce.enrich_rows(rows, window=ce.search_window(work))
        start, end = seen["window"]
        self.assertEqual(2009, start.year)
        self.assertEqual(10, start.month)

    def test_missing_rank_is_reported_for_the_first_twenty(self):
        """#53 的真事：主榜第 1～5 名整段没读到，要在日志里说出来。"""
        rows = [ce.ChartRow(rank=str(index), title=f"曲{index}") for index in range(6, 21)]
        with mock.patch.object(ce, "main_limit", return_value=20), \
             self.assertLogs(level="WARNING") as logs:
            ce.report_missing(rows)
        self.assertIn("缺第1、2、3、4、5名", "\n".join(logs.output))

    def test_result_is_cached_for_next_time(self):
        recognized = [{"本周": "1", "曲名": "Lilium", "得点": "12759"}]
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[(0.1, b"x")]), \
             mock.patch("utils.chart_recognize.recognize", return_value=recognized), \
             mock.patch.object(ce, "enrich_rows"), \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            ce.collect_work(SPEC, 52)
        payload = chart_cache.load(SPEC, 52)
        self.assertEqual(1, chart_cache.row_count(payload))
        self.assertEqual("sm8763104", payload["video_id"])
        self.assertIn("created_at", payload)

    def test_reusing_the_cache_skips_every_request(self):
        """「沿用上次结果」时一次网络请求都不发（连补全也不跑）。"""
        chart_cache.save(SPEC, 52, work_with([
            ce.ChartRow(rank="1", title="Lilium", video_id="sm8673630")]))
        with mock.patch.object(chart_nico, "find_ranking_video",
                               side_effect=AssertionError("不该再找视频")), \
             mock.patch.object(ce, "enrich_rows",
                               side_effect=AssertionError("不该再补全")), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52, reuse=lambda *args: True)
        self.assertTrue(work.from_cache)
        self.assertEqual(["Lilium"], [row.title for row in work.rows])
        self.assertEqual("sm8673630", work.rows[0].video_id)
        self.assertEqual("sm8763104", work.video_id)

    def test_declining_the_cache_recognises_again(self):
        chart_cache.save(SPEC, 52, work_with([ce.ChartRow(rank="1", title="旧的")]))
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[]), \
             mock.patch("utils.chart_editor.capture_in_browser", return_value=[]), \
             mock.patch.object(ce, "enrich_rows") as enrich, \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52, reuse=lambda *args: False)
        self.assertFalse(work.from_cache)
        self.assertEqual([], work.rows)
        enrich.assert_called_once()

    def test_bilibili_answer_is_kept_when_recognising_again(self):
        """重新识别时保留上次手填的 BV 号（搜不到就用它，别让用户重填）。"""
        chart_cache.save(SPEC, 52, ce.ChartWork(spec=SPEC, index=52, video_id="sm8763104",
                                                bilibili="BV11e411x7qq",
                                                rows=[ce.ChartRow(rank="1", title="旧的")]))
        with mock.patch.object(chart_nico, "find_ranking_video",
                               return_value=("sm8763104", "站上已有页面")), \
             mock.patch.object(chart_nico, "thumbinfo", return_value=INFO), \
             mock.patch("utils.chart_video.video_frames", return_value=[]), \
             mock.patch("utils.chart_editor.capture_in_browser", return_value=[]), \
             mock.patch.object(ce, "enrich_rows"), \
             mock.patch.object(chart_nico, "find_bilibili", return_value=""), \
             mock.patch("utils.ui.status"):
            work = ce.collect_work(SPEC, 52, reuse=lambda *args: False)
        self.assertEqual("BV11e411x7qq", work.bilibili)


if __name__ == "__main__":
    unittest.main()
