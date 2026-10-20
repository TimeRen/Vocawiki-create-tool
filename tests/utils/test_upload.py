"""封面歌姬询问 + 档案页说明的分类（回归）。"""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from PIL import Image

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

    def test_only_the_first_author_is_categorised(self):
        """一张图只挂一个作者分类（用户 2026-10-21 反馈「一张图对应一个作者吧」）。

        以前把 VocaDB 记的**每一位**曲绘都写成 `[[分类:作者:…]]`，合作曲的文件页就冒出
        六七个作者分类；站上的惯例是一张图一个作者，其余由 `choose_cover_author` 让用户挑。
        """
        authors = [Person("カノエ"), Person("きとん"), Person("ケローン")]
        text = upload.build_image_description("x", authors, "https://nico.ms/sm1")
        self.assertEqual(1, text.count("[[分类:作者:"))
        self.assertIn("[[分类:作者:カノエ]]", text)
        for name in ("きとん", "ケローン"):
            self.assertNotIn(f"[[分类:作者:{name}]]", text)


class ChooseCoverAuthorTest(TestCase):
    """文件说明页的作者分类只挂一位：曲绘有好几位时问用户挑（用户 2026-10-21 要求）。"""

    ZH = {"cover_author_choose": "这张封面算谁的？VocaDB 记了 {names} 好几位曲绘。",
          "cover_author_none": "都不对（不挂作者分类）"}

    def _zh(self):
        return mock.patch.object(upload, "_", side_effect=lambda key: self.ZH.get(key, key))

    def test_single_illustrator_is_used_without_asking(self):
        with mock.patch.object(upload, "prompt_choices") as choices:
            self.assertEqual(["りんく"], [a.name for a in
                                          upload.choose_cover_author([Person("りんく")])])
        choices.assert_not_called()

    def test_no_illustrator_returns_empty(self):
        with mock.patch.object(upload, "prompt_choices") as choices:
            self.assertEqual([], upload.choose_cover_author(None))
            self.assertEqual([], upload.choose_cover_author([]))
        choices.assert_not_called()

    def test_multiple_illustrators_are_offered_as_options(self):
        authors = [Person("カノエ"), Person("きとん"), Person("ケローン")]
        with self._zh(), \
             mock.patch.object(upload, "prompt_choices", return_value=2) as choices:
            chosen = upload.choose_cover_author(authors)
        self.assertEqual(["きとん"], [person.name for person in chosen])
        prompt, labels = choices.call_args.args
        self.assertIn("カノエ、きとん、ケローン", prompt)
        self.assertEqual(["カノエ", "きとん", "ケローン", "都不对（不挂作者分类）"], labels)

    def test_last_option_skips_the_author_category(self):
        authors = [Person("A"), Person("B")]
        with self._zh(), \
             mock.patch.object(upload, "prompt_choices", return_value=3):
            self.assertEqual([], upload.choose_cover_author(authors))


class UploadImageTest(TestCase):
    """封面真正发出去的那一步：同名文件要能覆盖，超大图要先压小。"""

    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "封面.jpg"
        Image.new("RGB", (40, 30), "#39c5bb").save(self.path, "JPEG")

    def _response(self, payload):
        response = mock.Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def _upload(self, response, path=None, **kwargs):
        session = mock.Mock()
        session.post.return_value = response
        config = mock.Mock()
        config.wiki.api_url = "https://voca.wiki/api.php"
        with mock.patch.object(upload.login, "is_logged_in", return_value=True), \
                mock.patch.object(upload.login, "get_session", return_value=session), \
                mock.patch.object(upload.login, "get_csrf_token", return_value="token"), \
                mock.patch.object(upload, "get_config", return_value=config):
            result = upload.upload_image(path or self.path, "封面.jpg", "歌曲", **kwargs)
        return result, session

    def test_existing_file_is_overwritten(self):
        result, session = self._upload(self._response(
            {"upload": {"result": "Success"}}))
        self.assertTrue(result["ok"], result)
        data = session.post.call_args.kwargs["data"]
        self.assertEqual("1", data["ignorewarnings"])
        _name, content, mime = session.post.call_args.kwargs["files"]["file"]
        self.assertEqual("image/jpeg", mime)
        self.assertEqual(self.path.read_bytes(), content)

    def test_opt_out_keeps_the_exists_warning(self):
        result, session = self._upload(
            self._response({"upload": {"result": "Warning", "warnings": {"exists": ""}}}),
            overwrite=False)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result.get("exists"))
        self.assertNotIn("ignorewarnings", session.post.call_args.kwargs["data"])

    def test_huge_image_is_resized_before_upload(self):
        big = Path(self.folder.name) / "big.jpg"
        Image.new("RGB", (2000, 1200), "#123456").save(big, "JPEG", quality=95)
        with mock.patch.object(upload, "MAX_UPLOAD_PIXELS", 500_000), \
                mock.patch.object(upload, "MAX_UPLOAD_EDGE", 1000):
            result, session = self._upload(self._response(
                {"upload": {"result": "Success"}}), path=big)
        self.assertTrue(result["ok"], result)
        _name, content, mime = session.post.call_args.kwargs["files"]["file"]
        self.assertEqual("image/jpeg", mime)
        with Image.open(io.BytesIO(content)) as sent:
            self.assertLessEqual(sent.width * sent.height, 500_000)
            self.assertLessEqual(max(sent.size), 1000)
