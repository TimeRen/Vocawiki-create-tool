"""提交页改条目名（用户 2026-11-02 要求）。

需求：译名不合适时能在提交页改，**左侧 wikitext 要同步**（封面文件名 —— 含输出目录里那张
实体图、`|歌曲名称` 里的名字、简介里的名字），消歧义里「要被移动的那个条目名」也要跟着改后缀/前缀；
`{{标题替换}}` 里是日文原名、条目里也没有自链接，这两处**不动**。
没有译名的条目（《EDEN》《404》那种不需要翻译的）**直接改掉旧名字**，不补 `<br/>译名`。
"""
from unittest import TestCase

from utils import page_rename


WIKITEXT = """{{虚拟歌手歌曲荣誉题头|CeVIO|nrank=1}}
{{标题替换|{{lj|ピトフーイ}}}}
{{VOCALOID_Songbox
|image    = 林鵙鹟(TakoyakiKZY).jpg
|演唱     = [[初音未来]]
|歌曲名称 = {{lj|ピトフーイ}}<br/>林鵙鹟
}}

《'''{{lj|ピトフーイ}}'''》（林鵙鹟）是由[[TakoyakiKZY]]于2023年8月4日投稿的歌曲。

== 歌词 ==
林鵙鹟是一种鸟（歌词正文，不该被动）
"""


class SplitTitleTest(TestCase):
    def test_a_suffix_is_split_off(self):
        self.assertEqual(("林鵙鹟", "TakoyakiKZY"), page_rename.split_title("林鵙鹟(TakoyakiKZY)"))

    def test_full_width_parentheses_count_too(self):
        self.assertEqual(("涅槃", "HotaRu"), page_rename.split_title("涅槃（HotaRu）"))

    def test_a_bare_title_has_no_suffix(self):
        self.assertEqual(("没有心", ""), page_rename.split_title("没有心"))

    def test_join_omits_an_empty_suffix(self):
        self.assertEqual("林鵙鹟(TakoyakiKZY)", page_rename.join_title("林鵙鹟", "TakoyakiKZY"))
        self.assertEqual("林鵙鹟", page_rename.join_title("林鵙鹟", ""))
        self.assertEqual("林鵙鹟", page_rename.join_title(" 林鵙鹟 ", "  "))


class RenameWikitextTest(TestCase):
    def test_the_cover_file_name_follows(self):
        text, changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "鸲鹟(TakoyakiKZY)")
        self.assertIn("|image    = 鸲鹟(TakoyakiKZY).jpg", text)
        self.assertIn("Songbox 的 |image 文件名", changes)

    def test_the_song_name_line_keeps_the_japanese_name(self):
        """`|歌曲名称 = {{lj|日文名}}<br/>译名`：只换 `<br/>` 后面那半截。"""
        text, changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "鸲鹟(TakoyakiKZY)")
        self.assertIn("|歌曲名称 = {{lj|ピトフーイ}}<br/>鸲鹟", text)
        self.assertIn("|歌曲名称 里的名字", changes)

    def test_the_intro_parenthesis_follows(self):
        text, changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "鸲鹟(TakoyakiKZY)")
        self.assertIn("《'''{{lj|ピトフーイ}}'''》（鸲鹟）", text)
        self.assertIn("简介里的「（译名）」", changes)

    def test_the_title_template_and_the_lyrics_are_left_alone(self):
        """`{{标题替换}}` 装的是日文原名；歌词正文里偶然出现的词也不该被改。"""
        text, _changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "鸲鹟(TakoyakiKZY)")
        self.assertIn("{{标题替换|{{lj|ピトフーイ}}}}", text)
        self.assertIn("林鵙鹟是一种鸟", text)

    def test_changing_only_the_suffix_keeps_the_translation(self):
        text, changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "林鵙鹟(Takoyaki KZY)")
        self.assertIn("|image    = 林鵙鹟(Takoyaki KZY).jpg", text)
        self.assertIn("{{lj|ピトフーイ}}<br/>林鵙鹟", text, "译名没变就别动它")
        self.assertIn("《'''{{lj|ピトフーイ}}'''》（林鵙鹟）", text)
        self.assertEqual(["Songbox 的 |image 文件名"], changes)

    def test_dropping_the_suffix_is_supported(self):
        text, _changes = page_rename.rename_wikitext(
            WIKITEXT, "林鵙鹟(TakoyakiKZY)", "鸲鹟")
        self.assertIn("|image    = 鸲鹟.jpg", text)
        self.assertIn("<br/>鸲鹟", text)


# 没有译名的条目（用户 2026-11-02：《EDEN》《404》那种本来就不需要翻译的）：
# `|歌曲名称` 只有一截、简介里也没有「（译名）」。
NO_TRANSLATION = """{{Songbox
|image    = 720°.jpg
|歌曲名称 = {{lj|720°}}
}}

《'''{{lj|720°}}'''》是由[[仄P]]于2022年6月25日投稿的歌曲。
"""


class RenameWithoutTranslationTest(TestCase):
    """没有译名时：**直接改掉旧名字**，不补 `<br/>译名`、也不补「（译名）」。"""

    def test_the_only_name_is_replaced_in_place(self):
        text, changes = page_rename.rename_wikitext(NO_TRANSLATION, "720°", "七二零度")
        self.assertIn("|歌曲名称 = {{lj|七二零度}}", text)
        self.assertIn("《'''{{lj|七二零度}}'''》", text)
        self.assertIn("|歌曲名称 里的名字", changes)
        self.assertIn("简介里的歌名", changes)

    def test_no_translation_half_is_added(self):
        text, _changes = page_rename.rename_wikitext(NO_TRANSLATION, "720°", "七二零度")
        self.assertNotIn("<br/>", text, "没有译名就是不翻译，不该凭空补一截")
        self.assertNotIn("（七二零度）", text, "也不该给简介补括号")

    def test_an_ascii_name_drops_the_lang_tag(self):
        text, _changes = page_rename.rename_wikitext(NO_TRANSLATION, "720°", "720D")
        self.assertIn("|歌曲名称 = 720D", text)
        self.assertIn("《'''720D'''》", text)

    def test_an_alias_after_the_japanese_name_is_left_alone(self):
        """`{{lj|日文名}}<br/>别名` 里后面那截是别名，不是译名：只换日文名那截。"""
        text = ("{{Songbox\n|image = 720°.jpg\n|歌曲名称 = {{lj|720°}}<br/>SevenTwenty\n}}\n\n"
                "《'''{{lj|720°}}'''》是由[[仄P]]投稿的歌曲。\n")
        new, _changes = page_rename.rename_wikitext(text, "720°", "七二零度")
        self.assertIn("|歌曲名称 = {{lj|七二零度}}<br/>SevenTwenty", new)
        self.assertIn("《'''{{lj|七二零度}}'''》", new)

    def test_the_title_template_is_left_alone(self):
        text = "{{标题替换|{{lj|720°}}}}\n" + NO_TRANSLATION
        new, _changes = page_rename.rename_wikitext(text, "720°", "七二零度")
        self.assertIn("{{标题替换|{{lj|720°}}}}", new)


class RenamePlanTest(TestCase):
    """消歧义计划跟着改名：别人的后缀（P主）原样保留。"""

    class _Entry:
        def __init__(self, title, line=""):
            self.title, self.line, self.description = title, line, "同名条目"

    class _Plan:
        def __init__(self):
            self.base_title = "林鵙鹟"
            self.our_title = "林鵙鹟(TakoyakiKZY)"
            self.others = [RenamePlanTest._Entry("林鵙鹟(nanahoshi管弦乐团)",
                                                 "* '''[[林鵙鹟(nanahoshi管弦乐团)]]'''")]
            self.siblings = [RenamePlanTest._Entry("林鵙鹟(别人的P主)")]
            self.note = "「林鵙鹟」已被占用"

    def test_the_other_entry_keeps_its_suffix(self):
        plan = self._Plan()
        page_rename.rename_plan(plan, "林鵙鹟(TakoyakiKZY)", "鸲鹟(TakoyakiKZY)")
        self.assertEqual("鸲鹟(TakoyakiKZY)", plan.our_title)
        self.assertEqual("鸲鹟", plan.base_title)
        self.assertEqual("鸲鹟(nanahoshi管弦乐团)", plan.others[0].title,
                         "另一位 P主 的后缀不能被改掉")
        self.assertIn("鸲鹟(nanahoshi管弦乐团)", plan.others[0].line)
        self.assertEqual("林鵙鹟(别人的P主)", plan.siblings[0].title,
                         "站上别的同名曲一个字都不该动")
        self.assertIn("鸲鹟", plan.note)

    def test_a_missing_plan_is_fine(self):
        self.assertIsNone(page_rename.rename_plan(None, "A", "B"))


class RenameCoverTest(TestCase):
    def test_the_upload_name_follows_the_page_name(self):
        class _Cover:
            wiki_name = "林鵙鹟(TakoyakiKZY).jpg"

        cover = _Cover()
        page_rename.rename_cover(cover, "鸲鹟(TakoyakiKZY)")
        self.assertEqual("鸲鹟(TakoyakiKZY).jpg", cover.wiki_name)
        self.assertIsNone(page_rename.rename_cover(None, "x"))

    def test_the_cover_file_on_disk_follows_too(self):
        """用户 2026-11-02：图片名也要改 —— `720°封面.jpg` → `七二零度封面.jpg`。"""
        import tempfile
        from pathlib import Path

        class _Cover:
            def __init__(self, path):
                self.path = path
                self.wiki_name = "720°.jpg"

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            old = folder.joinpath("720°封面.jpg")
            old.write_bytes(b"x")
            cover = _Cover(old)
            moved = page_rename.rename_cover_file(cover, "720°", "七二零度")
            self.assertEqual(folder.joinpath("七二零度封面.jpg"), moved)
            self.assertEqual(moved, cover.path)
            self.assertFalse(old.exists(), "旧文件名不该留着")
            self.assertTrue(moved.exists())

    def test_the_cover_file_is_left_alone_when_nothing_matches(self):
        import tempfile
        from pathlib import Path

        class _Cover:
            def __init__(self, path):
                self.path = path

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            old = folder.joinpath("别的名字.jpg")
            old.write_bytes(b"x")
            cover = _Cover(old)
            self.assertIsNone(page_rename.rename_cover_file(cover, "720°", "七二零度"))
            self.assertEqual(old, cover.path)
            self.assertTrue(old.exists())

    def test_an_existing_target_is_not_overwritten(self):
        import tempfile
        from pathlib import Path

        class _Cover:
            def __init__(self, path):
                self.path = path

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            old = folder.joinpath("720°封面.jpg")
            old.write_bytes(b"old")
            target = folder.joinpath("七二零度封面.jpg")
            target.write_bytes(b"already-there")
            cover = _Cover(old)
            self.assertIsNone(page_rename.rename_cover_file(cover, "720°", "七二零度"))
            self.assertEqual(old, cover.path)
            self.assertEqual(b"already-there", target.read_bytes())
