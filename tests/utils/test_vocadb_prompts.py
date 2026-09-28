"""vocadb 里的问答文案：选项要和界面语言一致，不能写死英文（回归）。"""
from unittest import TestCase, mock

from utils import vocadb


ZH = {
    "collection_track": "检测到发行活动“{name}”，请选择歌曲所属赛道：",
    "collection_rank": "请输入歌曲在{track}中的排名：",
    "not_ranked": "榜外",
    "none_of_above": "以上都不是",
}


class CollectionPromptTest(TestCase):
    """ボカコレ 之类的活动：选赛道 → （TOP100 / ROOKIE / REMIX 才要）排名。

    这是**兑底路径**（活动模板取不到时才走）；平时赛道与名次都由 `detect_collection_details`
    爬那一届的模板读出来。
    """

    def _zh(self):
        return mock.patch.object(vocadb, "_", side_effect=lambda key: ZH.get(key, key))

    def test_track_prompt_is_translated(self):
        with self._zh(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=4) as choices:
            track, rank = vocadb.prompt_vocaloid_collection_details("ボカコレ2024冬")
        self.assertEqual(("榜外", None), (track, rank))
        prompt, options = choices.call_args.args
        self.assertIn("ボカコレ2024冬", prompt)
        self.assertIn("赛道", prompt)
        self.assertEqual(["TOP100", "ROOKIE", "REMIX", "榜外"], options)

    def test_top100_asks_for_the_rank(self):
        with self._zh(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=1), \
             mock.patch.object(vocadb, "prompt_response", return_value="12") as response:
            track, rank = vocadb.prompt_vocaloid_collection_details("ボカコレ2024冬")
        self.assertEqual(("TOP100", "12"), (track, rank))
        self.assertIn("TOP100", response.call_args.args[0])

    def test_rookie_asks_for_the_rank(self):
        with self._zh(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=2), \
             mock.patch.object(vocadb, "prompt_response", return_value="7") as response:
            track, rank = vocadb.prompt_vocaloid_collection_details("ボカコレ2024冬")
        self.assertEqual(("ROOKIE", "7"), (track, rank))
        self.assertIn("ROOKIE", response.call_args.args[0])

    def test_remix_is_selectable(self):
        """REMIX 也是赛道（参 Relay Outer/Iyowa）。"""
        with self._zh(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=3), \
             mock.patch.object(vocadb, "prompt_response", return_value="6") as response:
            track, rank = vocadb.prompt_vocaloid_collection_details("ボカコレ2024冬")
        self.assertEqual(("REMIX", "6"), (track, rank))
        self.assertIn("REMIX", response.call_args.args[0])

    def test_none_of_above_option_is_translated(self):
        """多个 VocaDB 结果时的兜底选项原来写死了英文。"""
        hits = [{"id": 1, "defaultName": "A", "artistString": "P1"},
                {"id": 2, "defaultName": "B", "artistString": "P2"}]
        with self._zh(), \
             mock.patch.object(vocadb, "search_narrow", return_value=hits), \
             mock.patch.object(vocadb, "prompt_choices", return_value=1) as choices:
            self.assertEqual(1, vocadb.search_song_id("A"))
        self.assertEqual(["A by P1", "B by P2", "以上都不是"],
                         choices.call_args.args[1])
