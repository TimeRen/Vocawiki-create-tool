"""同一期榜单的识别结果缓存（见 utils/chart_cache.py）。"""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils import chart_cache, chart_nico

SPEC = chart_nico.CHARTS["utau"]


class Work:
    """`ChartWork` 的最小替身（本模块只认属性和 `row.to_dict()`）。"""

    def __init__(self, rows):
        self.video_id = "sm8763104"
        self.uploader = "あーさん"
        self.publish = "2009年11月10日 00:06"
        self.start = "2009年11月01日 00:00"
        self.end = "2009年11月08日 00:00"
        self.rule = "2008"
        self.description = "○集計期間：…"
        self.bilibili = "BV1GZ4y1x7Zo"
        self.notes = ""
        self.frames = 38
        self.source = "站上已有页面"
        self.rows = rows


class Row:
    def __init__(self, rank, title):
        self.rank = rank
        self.title = title

    def to_dict(self):
        return {"rank": self.rank, "title": self.title}


class CacheTest(unittest.TestCase):
    def setUp(self):
        self._folder = tempfile.TemporaryDirectory()
        self.folder = Path(self._folder.name) / ".chart_cache"
        chart_cache.set_folder(self.folder)

    def tearDown(self):
        chart_cache.set_folder(None)
        self._folder.cleanup()

    def test_path_is_per_chart_and_issue(self):
        self.assertEqual("utau-53.json", chart_cache.path_for(SPEC, 53).name)
        self.assertEqual(self.folder, chart_cache.path_for(SPEC, 53).parent)

    def test_missing_cache_is_none(self):
        self.assertIsNone(chart_cache.load(SPEC, 53))
        self.assertFalse(chart_cache.is_usable(None))

    def test_round_trip_keeps_the_work(self):
        chart_cache.save(SPEC, 52, Work([Row("1", "Lilium"), Row("2", "白花")]))
        payload = chart_cache.load(SPEC, 52)
        self.assertEqual("sm8763104", payload["video_id"])
        self.assertEqual("BV1GZ4y1x7Zo", payload["bilibili"])
        self.assertEqual(2, chart_cache.row_count(payload))
        self.assertTrue(chart_cache.is_usable(payload))
        self.assertIn("created_at", payload)

    def test_rows_without_data_are_not_usable(self):
        chart_cache.save(SPEC, 52, Work([]))
        self.assertFalse(chart_cache.is_usable(chart_cache.load(SPEC, 52)))

    def test_broken_json_is_none(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        chart_cache.path_for(SPEC, 52).write_text("{不是json", encoding="utf-8")
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(chart_cache.load(SPEC, 52))

    def test_age_text(self):
        now = datetime.now(timezone.utc)
        self.assertEqual("刚刚", chart_cache.age_text(now.isoformat(timespec="seconds")))
        self.assertEqual("2小时前", chart_cache.age_text((now - timedelta(hours=2)).isoformat()))
        self.assertEqual("3天前", chart_cache.age_text((now - timedelta(days=3)).isoformat()))
        self.assertEqual("", chart_cache.age_text(""))
        self.assertEqual("", chart_cache.age_text("看不懂的时间"))

    def test_reuse_hint_mentions_rows_and_age(self):
        created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        payload = {"rows": [{"rank": "1"}], "created_at": created}
        hint = chart_cache.reuse_hint(SPEC, 53, payload)
        self.assertIn("第53期上次识别过", hint)
        self.assertIn("1行", hint)
        self.assertIn("2小时前", hint)

    def test_clear_removes_one_issue_or_everything(self):
        chart_cache.save(SPEC, 52, Work([Row("1", "A")]))
        chart_cache.save(SPEC, 53, Work([Row("1", "B")]))
        self.assertEqual(1, chart_cache.clear(SPEC, 52))
        self.assertIsNone(chart_cache.load(SPEC, 52))
        self.assertIsNotNone(chart_cache.load(SPEC, 53))
        self.assertEqual(1, chart_cache.clear())
        self.assertIsNone(chart_cache.load(SPEC, 53))

    def test_payload_is_json_serialisable(self):
        text = json.dumps(chart_cache.payload_for(SPEC, 52, Work([Row("1", "Lilium")])),
                          ensure_ascii=False)
        self.assertIn('"title": "Lilium"', text)
        self.assertIn('"chart": "utau"', text)


if __name__ == "__main__":
    unittest.main()
