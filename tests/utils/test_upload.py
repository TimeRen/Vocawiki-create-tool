"""封面歌姬询问：问句与选项都要跟界面语言走，且「是」包含、「否」丢掉（回归）。"""
from unittest import TestCase, mock

from utils import upload


ZH = {
    "Yes": "是",
    "No": "否",
    "has_characters": "该图片是否有出现歌姬？",
    "has_character_named": "该图片中是否有出现「{name}」？",
    "character_name_input": "请输入出现的歌姬名（如「初音未来」，留空结束）：",
}


class ChooseCharactersTest(TestCase):
    """`upload.choose_characters`：先把 VocaDB 认出来的歌姬逐个问一遍。"""

    def _zh(self):
        return mock.patch.object(upload, "_", side_effect=lambda key: ZH.get(key, key))

    def _names(self):
        return mock.patch.object(upload, "name_to_chinese", side_effect=lambda name: name)

    def test_questions_and_options_are_translated(self):
        with self._zh(), self._names(), \
             mock.patch.object(upload, "prompt_choices", return_value=1) as choices:
            self.assertEqual(["NurseRobot_TypeT"],
                             upload.choose_characters(["NurseRobot_TypeT"]))
        self.assertEqual(2, choices.call_count)
        first, second = [call.args for call in choices.call_args_list]
        self.assertEqual("该图片是否有出现歌姬？", first[0])
        self.assertEqual(["是", "否"], first[1], "选项不能写死中文，得跟界面语言走")
        self.assertEqual("该图片中是否有出现「NurseRobot_TypeT」？", second[0])
        self.assertEqual(["是", "否"], second[1])

    def test_answers_no_returns_empty(self):
        with self._zh(), self._names(), \
             mock.patch.object(upload, "prompt_choices", return_value=2) as choices:
            self.assertEqual([], upload.choose_characters(["鸣花姬"]))
        self.assertEqual(1, choices.call_count, "第一问答「否」就不该再逐个问")

    def test_second_answer_no_drops_that_vocalist(self):
        answers = iter([1, 1, 2, 1])            # 有歌姬 → 有 / 没有 / 有
        with self._zh(), self._names(), \
             mock.patch.object(upload, "prompt_choices",
                               side_effect=lambda *a, **k: next(answers)):
            self.assertEqual(["初音未来", "巡音流歌"],
                             upload.choose_characters(["初音未来", "鸣花姬", "巡音流歌"]))

    def test_asks_for_names_when_vocadb_knows_none(self):
        answers = iter(["初音未来", ""])
        with self._zh(), self._names(), \
             mock.patch.object(upload, "prompt_choices", return_value=1), \
             mock.patch.object(upload, "prompt_response",
                               side_effect=lambda *a, **k: next(answers)) as response:
            self.assertEqual(["初音未来"], upload.choose_characters([]))
        self.assertEqual("请输入出现的歌姬名（如「初音未来」，留空结束）：",
                         response.call_args.args[0])
