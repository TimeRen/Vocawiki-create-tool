"""`utils/ai_names.py` 的单测：提示词、结果清洗、分批请求与出错处理（HTTP 全 mock）。"""
import json
import unittest
from unittest import mock

from utils import ai_css, ai_names


def _reply(payload) -> dict:
    """模拟 OpenAI 兼容接口的回复（`ai_css._reply_text` 从这里取 choices[0].message.content）。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"choices": [{"message": {"content": text}}]}


class BuildPromptTest(unittest.TestCase):
    def test_prompt_lists_songs_and_asks_for_json(self):
        songs = [{"ja": "ラグタイムレコード", "date": "2021-09-01"},
                 {"ja": "ネハン", "date": ""}]
        prompt = ai_names.build_prompt(songs, "雄之助")
        self.assertIn("P主：雄之助", prompt)
        self.assertIn("1. ラグタイムレコード｜2021-09-01", prompt)
        self.assertIn("2. ネハン｜（日期未知）", prompt)
        self.assertIn('{"ラグタイムレコード": "中文标题"}', prompt)

    def test_system_prompt_forbids_guessing(self):
        """宁可留空也不许编：这条是提示词的硬性规则 2。"""
        self.assertIn("不要直译、不要音译、不要编造", ai_names.SYSTEM_PROMPT)
        self.assertIn('空字符串 ""', ai_names.SYSTEM_PROMPT)

    def test_batches(self):
        songs = [{"ja": str(index)} for index in range(45)]
        groups = ai_names.batches(songs, 20)
        self.assertEqual([20, 20, 5], [len(group) for group in groups])


class CleanNamesTest(unittest.TestCase):
    SONGS = [{"ja": "ラグタイムレコード"}, {"ja": "ネハン"}]

    def test_keeps_only_requested_and_chinese_names(self):
        parsed = {"ラグタイムレコード": "时滞记录", "ネハン": "涅槃",
                  "没问过的歌": "随便", "ラグタイムレコード 2": ""}
        self.assertEqual({"ラグタイムレコード": "时滞记录", "ネハン": "涅槃"},
                         ai_names.clean_names(parsed, self.SONGS))

    def test_drops_kana_empty_and_decorated_values(self):
        parsed = {"ラグタイムレコード": "ラグタイムレコード",   # 原文照抄 → 不算
                  "ネハン": "【涅槃】"}                          # 装饰会清掉，留下「涅槃」
        self.assertEqual({"ネハン": "涅槃"}, ai_names.clean_names(parsed, self.SONGS))

    def test_matches_keys_loosely(self):
        """模型常把 `・` / 空格写歪，键要宽容一点对。"""
        parsed = {"ラグ タイム・レコード": "时滞记录"}
        self.assertEqual({"ラグタイムレコード": "时滞记录"},
                         ai_names.clean_names(parsed, self.SONGS))


class SuggestNamesTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(ai_css, "settings",
                                  return_value={"provider": "openai",
                                                "base_url": "https://api.deepseek.com/v1",
                                                "model": "deepseek-flash", "api_key": "k",
                                                "thinking": False})
        patch.start()
        self.addCleanup(patch.stop)
        self.payload = json.dumps({"artist": "雄之助", "songs": [
            {"ja": "ラグタイムレコード", "date": "2021-09-01"},
            {"ja": "ネハン", "date": ""}]}, ensure_ascii=False)

    def test_without_key(self):
        with mock.patch.object(ai_css, "settings",
                               return_value={"provider": "openai",
                                             "base_url": "https://api.deepseek.com/v1",
                                             "model": "deepseek-flash", "api_key": "",
                                             "thinking": False}):
            result = ai_names.suggest_names(self.payload)
        self.assertFalse(result["ok"])
        self.assertIn("ai_api_key", result["error"])

    def test_bad_payload(self):
        self.assertFalse(ai_names.suggest_names("不是 JSON")["ok"])
        self.assertFalse(ai_names.suggest_names(json.dumps({"songs": []}))["ok"])

    def test_suggests_names_and_asks_for_json_output(self):
        spoken: list = []
        with mock.patch.object(ai_css, "_post",
                               return_value=(_reply({"ラグタイムレコード": "时滞记录",
                                                     "ネハン": "涅槃"}), "")) as post:
            result = ai_names.suggest_names(self.payload, progress=spoken.append)
        self.assertTrue(result["ok"])
        self.assertEqual({"ラグタイムレコード": "时滞记录", "ネハン": "涅槃"}, result["names"])
        self.assertEqual("deepseek-flash", result["model"])
        self.assertIn("1/1 批", spoken[0])
        # 低温度 + 起名专用的 system 提示词，都传给了共用那套请求构造
        _url, _headers, body = post.call_args[0]
        self.assertEqual(ai_names.TEMPERATURE, body["temperature"])
        self.assertEqual(ai_names.SYSTEM_PROMPT, body["messages"][0]["content"])

    def test_splits_into_batches(self):
        songs = [{"ja": f"歌{index}", "date": ""} for index in range(25)]
        payload = json.dumps({"artist": "雄之助", "songs": songs}, ensure_ascii=False)
        replies = [(_reply({f"歌{index}": f"名字{index}" for index in range(20)}), ""),
                   (_reply({f"歌{index}": f"名字{index}" for index in range(20, 25)}), "")]
        with mock.patch.object(ai_css, "_post", side_effect=replies) as post:
            result = ai_names.suggest_names(payload)
        self.assertEqual(2, post.call_count)
        self.assertEqual(25, len(result["names"]))
        self.assertEqual("名字24", result["names"]["歌24"])

    def test_error_when_the_first_batch_fails(self):
        with mock.patch.object(ai_css, "_post", return_value=(None, "接口返回 500")):
            result = ai_names.suggest_names(self.payload)
        self.assertFalse(result["ok"])
        self.assertEqual("接口返回 500", result["error"])

    def test_keeps_partial_results_when_a_later_batch_fails(self):
        """第一批到手、第二批网络挂了：留着手上的，并带上警告。"""
        songs = [{"ja": f"歌{index}", "date": ""} for index in range(25)]
        payload = json.dumps({"artist": "雄之助", "songs": songs}, ensure_ascii=False)
        replies = [(_reply({f"歌{index}": f"名字{index}" for index in range(20)}), ""),
                   (None, "连接被重置")]
        with mock.patch.object(ai_css, "_post", side_effect=replies):
            result = ai_names.suggest_names(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(20, len(result["names"]))
        self.assertEqual("连接被重置", result["warning"])

    def test_no_json_when_the_model_chats(self):
        with mock.patch.object(ai_css, "_post", return_value=(_reply("我不确定这些歌"), "")):
            result = ai_names.suggest_names(self.payload)
        self.assertFalse(result["ok"])
        self.assertEqual("模型没有返回 JSON", result["error"])


if __name__ == "__main__":
    unittest.main()
