"""封面歌姬询问 + 档案页说明的分类（回归）。"""
from unittest import TestCase, mock

from models.creators import Person
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


class BuildImageDescriptionTest(TestCase):
    """档案页说明里的歌姬分类要写**分类名**（用户 2026-10-14 报 diff 261342）。

    实测 `File:1 2 FanClub Chinese.jpg` 挂 `[[分类:Megpoid]]`（不是 `GUMI`）、
    `File:Synthesizer×Dream.jpg` 挂 `[[Category:NAKUMO]]`（不是 `ナクモ`）。
    以前这里直接把歌姬名当分类名写出去，于是 `ナクモ` 变成一个不存在的分类。
    """

    def test_vocalist_categories_use_the_category_name(self):
        text = upload.build_image_description("Synthesizer×Dream", None, "", ["ナクモ"])
        self.assertIn("[[Category:NAKUMO]]", text)
        self.assertNotIn("ナクモ]]", text)

    def test_megpoid_is_written_as_its_category(self):
        text = upload.build_image_description("1 2 FanClub", None, "", ["GUMI"])
        self.assertIn("[[Category:Megpoid]]", text)
        self.assertNotIn("[[Category:GUMI]]", text)

    def test_authors_and_the_cover_category_are_kept(self):
        text = upload.build_image_description("x", [Person("りんく")], "https://nico.ms/sm1",
                                              ["めろう"])
        self.assertIn("[[分类:作者:りんく]]", text)
        self.assertIn("[[Category:视频封面]]", text)
        self.assertIn("[[Category:Merrow]]", text)
