"""utils/disambig.py 的测试（不联网）：命名、About/Otheruseslist、消歧义页文本、链入替换与处理流程。

口径对照站内真实条目：
    向日葵(Teary Planet)  → {{About|[[Teary Planet|…]]創作的歌曲|[[Project Lumina]]创作的歌曲|向日葵(Project Lumina)}}
    时光机(1640P)         → {{Otheruseslist|40㍍P和164創作的歌曲|[[Aira]]创作的歌曲|时光机(Aira)|…}}
"""
from pathlib import Path
from unittest import TestCase
from unittest import mock

from models.creators import Creators, Person
from models.song import Image, Lyrics, Song
from utils import disambig, wiki_api

DISAMBIG_PAGE = """'''向日葵'''可以指：

== 歌曲 ==
* '''[[向日葵(Project Lumina)]]'''（{{lj|ひまわり}}）————[[Project Lumina]]制作，[[IA]]演唱的[[VOCALOID]]日语原创歌曲。

{{disambig}}
"""

TIME_MACHINE = """{{Otheruseslist|40㍍P和164創作的歌曲|[[Aira]]创作的歌曲|时光机(Aira)|[[SmileR]]创作的VOCALOID歌曲|Time Machine}}
{{標題替換|zh-hans=时光机|zh-hant=時光機|zh=時光機}}
{{VOCALOID Songbox
|image = Miku 1640m guitar13234939.jpg
|演唱 = [[初音未來]]
|歌曲名称 = {{lj|タイムマシン}}<br />時光機
|P主 = [[40mP]]×[[164]]
}}
[[分类:使用VOCALOID的歌曲]]
"""

VOCALOID_TEXTS = {
    "向日葵(Project Lumina)": """{{VOCALOID_Songbox
|演唱 = [[IA]]
|歌曲名称 = {{lj|ひまわり}}<br/>向日葵
|P主 = [[Project Lumina]]
}}
[[分类:使用VOCALOID的歌曲]]
""",
}


def _song(name_chs="向日葵", name_jap="向日葵", producers=(("Teary Planet", ()),),
          vocalists=("v flower",), page_name=None):
    song = Song(name_jap=name_jap, name_chs=name_chs, name_other=[],
                creators=Creators(producers=[Person(n, list(a)) for n, a in producers],
                                  vocalists=[Person(n) for n in vocalists], staffs={}),
                lyrics=Lyrics(), image=Image(path=Path("."), file_name="x.jpg", source_url=""))
    song.page_name = page_name
    return song


class NamingTest(TestCase):
    def test_ascii_name_kept(self):
        self.assertEqual("Teary Planet", disambig.romanized("Teary Planet"))

    def test_japanese_name_uses_vocadb_alias(self):
        self.assertEqual("HachioujiP", disambig.romanized("八王子P", ["HachioujiP", "8#Prince"]))
        self.assertEqual("MikitoP", disambig.romanized("みきとP", ["MikitoP", "愛島"]))

    def test_japanese_name_without_alias(self):
        self.assertEqual("謎のP", disambig.romanized("謎のP", ["愛島"]))

    def test_producer_suffix_joins_with_cross(self):
        producers = [Person("40mP"), Person("164")]
        self.assertEqual("40mP×164", disambig.producer_suffix(producers))

    def test_producer_suffix_romanizes_japanese(self):
        self.assertEqual("HachioujiP×164",
                         disambig.producer_suffix([Person("八王子P", ["HachioujiP"]), Person("164")]))

    def test_entry_title(self):
        self.assertEqual("向日葵(Teary Planet)", disambig.entry_title("向日葵", "Teary Planet"))
        self.assertEqual("向日葵", disambig.entry_title("向日葵", ""))

    def test_join_names(self):
        self.assertEqual("A", disambig.join_names(["A"]))
        self.assertEqual("A和B", disambig.join_names(["A", "B"]))
        self.assertEqual("A、B、C", disambig.join_names(["A", "B", "C"]))

    def test_strip_links(self):
        self.assertEqual("Teary Planet(七尾-nanao)",
                         disambig.strip_links("[[Teary Planet|Teary Planet(七尾-nanao)]]"))
        self.assertEqual("Aira", disambig.strip_links("[[Aira]]"))


class EntryTest(TestCase):
    def test_our_entry_matches_site_convention(self):
        song = _song(page_name="向日葵(Teary Planet)")
        entry = disambig.our_entry(song)
        self.assertEqual("向日葵(Teary Planet)", entry.title)
        self.assertEqual("[[Teary Planet]]创作的歌曲", entry.description)
        self.assertEqual("* '''[[向日葵(Teary Planet)]]'''（{{lj|向日葵}}）————"
                         "[[Teary Planet]]制作，[[v flower]]演唱的[[VOCALOID]]日语原创歌曲。",
                         entry.line)

    def test_our_entry_without_japanese_name(self):
        song = _song(name_jap="", page_name="向日葵(Teary Planet)")
        self.assertNotIn("{{lj|}}", disambig.our_entry(song).line)

    def test_parse_entry_reads_songbox(self):
        entry = disambig.parse_entry("时光机(1640P)", TIME_MACHINE)
        self.assertEqual("时光机(1640P)", entry.title)
        self.assertEqual("[[40mP]]×[[164]]创作的歌曲", entry.description)
        self.assertEqual("* '''[[时光机(1640P)]]'''（{{lj|タイムマシン}}）————"
                         "[[40mP]]×[[164]]制作，[[初音未來]]演唱的[[VOCALOID]]日语原创歌曲。",
                         entry.line)

    def test_parse_entry_without_songbox_fields(self):
        entry = disambig.parse_entry("某条目", "普通页面")
        self.assertEqual("同名条目", entry.description)
        self.assertIn("同名条目", entry.line)

    def test_parse_entry_lines(self):
        entries = disambig.parse_entry_lines(DISAMBIG_PAGE)
        self.assertEqual(["向日葵(Project Lumina)"], [entry.title for entry in entries])
        self.assertTrue(entries[0].line.startswith("* '''[[向日葵(Project Lumina)]]'''"))


class TextTest(TestCase):
    def test_about_when_two_entries(self):
        plan = disambig.Plan(base_title="向日葵", our_title="向日葵(Teary Planet)",
                             mode=disambig.MODE_DISAMBIG,
                             others=[disambig.Entry(title="向日葵(Project Lumina)",
                                                    description="[[Project Lumina]]创作的歌曲")])
        plan.our_entry = disambig.our_entry(_song(page_name="向日葵(Teary Planet)"))
        self.assertEqual("{{About|[[Teary Planet]]创作的歌曲|[[Project Lumina]]创作的歌曲|"
                         "向日葵(Project Lumina)}}", disambig.top_template(plan))

    def test_otheruseslist_from_three_entries(self):
        plan = disambig.Plan(base_title="时光机", our_title="时光机(1640P)",
                             mode=disambig.MODE_DISAMBIG,
                             others=[disambig.Entry("时光机(Aira)", "[[Aira]]创作的歌曲"),
                                     disambig.Entry("Time Machine", "[[SmileR]]创作的歌曲")])
        plan.our_entry = disambig.our_entry(
            _song("时光机", "タイムマシン", producers=(("40mP", ()), ("164", ()))))
        self.assertEqual("{{Otheruseslist|40mP和164创作的歌曲|[[Aira]]创作的歌曲|时光机(Aira)|"
                         "[[SmileR]]创作的歌曲|Time Machine}}", disambig.top_template(plan))

    def test_no_template_without_others(self):
        self.assertEqual("", disambig.top_template(None))
        self.assertEqual("", disambig.top_template(disambig.Plan(base_title="向日葵")))
        plan = disambig.Plan(base_title="向日葵", our_title="向日葵(Teary Planet)",
                             mode=disambig.MODE_OCCUPIED, others=[])
        self.assertEqual("", disambig.top_template(plan))

    def test_about_points_to_disambig_page_when_alone(self):
        # 消歧义页上只有自己时按站内写法指向消歧义页本身，参 Melt(ryo)
        plan = disambig.Plan(base_title="Melt", our_title="Melt(ryo)", mode=disambig.MODE_DISAMBIG)
        plan.our_entry = disambig.Entry("Melt(ryo)", description="[[ryo]]创作的歌曲")
        self.assertEqual("{{About|本条目描述=[[ryo]]创作的歌曲|消歧义页=Melt}}",
                         disambig.top_template(plan))

    def test_disambig_page_text(self):
        entry = disambig.Entry(title="向日葵(Teary Planet)", line="* '''[[X]]'''————Y。")
        text = disambig.disambig_page_text("向日葵", [entry])
        self.assertTrue(text.startswith("'''向日葵'''可以指：\n\n== 歌曲 ==\n"))
        self.assertIn("* '''[[X]]'''————Y。", text)
        self.assertTrue(text.rstrip().endswith("{{disambig}}"))

    def test_append_entry_before_template(self):
        entry = disambig.Entry(title="向日葵(Teary Planet)", line="* NEW")
        text = disambig.append_entry_to_disambig(DISAMBIG_PAGE, entry)
        self.assertLess(text.index("* NEW"), text.index("{{disambig}}"))
        self.assertIn("== 歌曲 ==", text)
        self.assertEqual(1, text.count("{{disambig}}"))

    def test_append_entry_without_disambig_template(self):
        text = disambig.append_entry_to_disambig("'''歌名'''可以指：\n", disambig.Entry("A", line="* A"))
        self.assertIn("== 歌曲 ==", text)
        self.assertIn("* A", text)
        self.assertTrue(text.rstrip().endswith("{{disambig}}"))

    def test_append_entry_is_not_duplicated(self):
        entry = disambig.Entry(title="向日葵(Project Lumina)", line="* NEW")
        self.assertEqual(DISAMBIG_PAGE.rstrip("\n"),
                         disambig.append_entry_to_disambig(DISAMBIG_PAGE, entry).rstrip("\n"))


class ReplaceLinksTest(TestCase):
    def test_plain_and_piped_links(self):
        text = "见[[时光机]]与[[时光机|时光机]]，还有[[时光机(1640P)]]。"
        new_text, count = disambig.replace_links(text, "时光机", "时光机(1640P)")
        self.assertEqual("见[[时光机(1640P)]]与[[时光机(1640P)|时光机]]，还有[[时光机(1640P)]]。", new_text)
        self.assertEqual(2, count)

    def test_does_not_touch_templates_or_text(self):
        text = "{{lj|时光机}} 和纯文字时光机"
        new_text, count = disambig.replace_links(text, "时光机", "时光机(1640P)")
        self.assertEqual(text, new_text)
        self.assertEqual(0, count)

    def test_same_title_is_noop(self):
        self.assertEqual(("[[A]]", 0), disambig.replace_links("[[A]]", "A", "A"))

    def test_regex_metachars_in_title(self):
        new_text, count = disambig.replace_links("[[歌名(a)|x]]", "歌名(a)", "歌名(b)")
        self.assertEqual("[[歌名(b)|x]]", new_text)
        self.assertEqual(1, count)

    def test_snippet(self):
        text = "A" * 40 + "[[时光机]]" + "B" * 40
        self.assertIn("[[时光机]]", disambig.snippet(text, "时光机"))
        self.assertEqual("", disambig.snippet("没有链接", "时光机"))


# 榜单类模板的实际写法（Template:VOCALOID & UTAU Ranking/bricks）：
#     [[{{{条目|{{{曲名}}}{{{后缀|}}}}}}|查看条目]]
BRICKS_WITH_ENTRY = """{{VOCALOID_&_UTAU_Ranking/bricks
|id = 37464090
|曲名 = 向日葵
|条目 = 向日葵(Teary Planet)
|歌姬 = v flower
|P主 = Teary Planet
|本周 = 10
}}
"""

BRICKS_NAME_ONLY = """{{VOCALOID_&_UTAU_Ranking/bricks
|id = 37441963
|color = #AA0000
|曲名 = Darling Dance
|歌姬 = 初音未来
|本周 = OP
|bottom-column = {{color|#AA0000|上周冠军}}
}}
"""

BRICKS_WITH_SUFFIX = """{{VOCALOID_&_UTAU_Ranking/bricks
|id = 37478808
|曲名 = Anti Joker
|后缀 = (MaikiP)
|本周 = 1
}}
"""


class TemplateParameterTest(TestCase):
    """模板参数写法（页面里没有 [[ ]]）也要能替换：|条目 = 旧名、曲名 + 后缀。"""

    def test_entry_parameter_is_replaced(self):
        new_text, count = disambig.replace_entry_parameters(
            BRICKS_WITH_ENTRY, "向日葵(Teary Planet)", "向日葵(Project Lumina)")
        self.assertEqual(1, count)
        self.assertIn("|条目 = 向日葵(Project Lumina)", new_text)
        self.assertIn("|曲名 = 向日葵", new_text)                 # 显示名不动
        self.assertIn("|P主 = Teary Planet", new_text)

    def test_entry_parameter_without_spaces(self):
        new_text, count = disambig.replace_entry_parameters("|条目=旧名\n", "旧名", "新名")
        self.assertEqual(1, count)
        self.assertEqual("|条目=新名\n", new_text)

    def test_entry_parameter_not_partially_matched(self):
        text = "|条目 = 旧名字\n|条目 = 旧名(2)\n"
        new_text, count = disambig.replace_entry_parameters(text, "旧名", "新名")
        self.assertEqual(0, count)
        self.assertEqual(text, new_text)

    def test_song_name_only_adds_entry_parameter(self):
        # 只有 曲名、没有 后缀/条目 → 链接目标就是曲名本身 → 补一行 |条目 = 新名
        new_text, count = disambig.replace_song_name_reference(
            BRICKS_NAME_ONLY, "Darling Dance", "Darling Dance(かいりきベア)")
        self.assertEqual(1, count)
        self.assertIn("|曲名 = Darling Dance\n|条目 = Darling Dance(かいりきベア)\n", new_text)
        self.assertEqual(1, new_text.count("|条目 ="))

    def test_song_name_with_suffix_replaces_suffix(self):
        new_text, count = disambig.replace_song_name_reference(
            BRICKS_WITH_SUFFIX, "Anti Joker(MaikiP)", "Anti Joker(MaikiP2)")
        self.assertEqual(1, count)
        self.assertIn("|曲名 = Anti Joker\n|后缀 = (MaikiP2)\n", new_text)

    def test_suffix_not_touched_when_entry_present(self):
        text = BRICKS_WITH_SUFFIX.replace("|后缀 = (MaikiP)", "|后缀 = (MaikiP)\n|条目 = Anti Joker(MaikiP)")
        new_text, count = disambig.replace_song_name_reference(text, "Anti Joker(MaikiP)", "Anti Joker(X)")
        self.assertEqual(0, count)
        self.assertEqual(text, new_text)

    def test_fix_page_text_prefers_links_then_parameters(self):
        self.assertEqual(("见[[新名]]", 1, "wiki 链接"),
                         disambig.fix_page_text("见[[旧名]]", "旧名", "新名"))
        new_text, count, kind = disambig.fix_page_text(BRICKS_WITH_ENTRY, "向日葵(Teary Planet)",
                                                       "向日葵(Project Lumina)")
        self.assertEqual((1, "模板参数（条目）"), (count, kind))
        self.assertIn("|条目 = 向日葵(Project Lumina)", new_text)
        new_text, count, kind = disambig.fix_page_text(BRICKS_NAME_ONLY, "Darling Dance", "Darling Dance(X)")
        self.assertEqual((1, "模板参数（曲名 / 后缀）"), (count, kind))

    def test_fix_page_text_reports_nothing_found(self):
        self.assertEqual(("没有引用", 0, ""), disambig.fix_page_text("没有引用", "旧名", "新名"))
        self.assertEqual(("", 0, ""), disambig.fix_page_text("", "旧名", "新名"))
        self.assertEqual(("|条目 = 旧名", 0, ""),
                         disambig.fix_page_text("|条目 = 旧名", "旧名", "旧名"))

    def test_snippet_finds_parameter_reference(self):
        self.assertIn("|条目 = 向日葵(Teary Planet)",
                      disambig.snippet(BRICKS_WITH_ENTRY, "向日葵(Teary Planet)"))


class DetectTest(TestCase):
    def _detect(self, facts, siblings=(), song=None, texts=None):
        with mock.patch.object(wiki_api, "fetch_page_facts", return_value=facts), \
             mock.patch.object(wiki_api, "list_titles_with_prefix", return_value=list(siblings)), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts or {}):
            return disambig.detect(song or _song())

    def test_no_same_name_page(self):
        plan = self._detect({"ok": True, "exists": False})
        self.assertFalse(plan.needed)
        self.assertEqual(disambig.MODE_NONE, plan.mode)

    def test_query_failure_is_treated_as_no_conflict(self):
        plan = self._detect({"ok": False, "exists": False})
        self.assertFalse(plan.needed)
        self.assertIn("无法确认", plan.note)

    def test_siblings_without_base_page(self):
        plan = self._detect({"ok": True, "exists": False}, siblings=["向日葵(Project Lumina)"])
        self.assertFalse(plan.needed)
        self.assertIn("向日葵(Project Lumina)", plan.note)

    def test_existing_disambig_page(self):
        plan = self._detect({"ok": True, "exists": True, "disambig": True, "text": DISAMBIG_PAGE},
                            texts=VOCALOID_TEXTS)
        self.assertEqual(disambig.MODE_DISAMBIG, plan.mode)
        self.assertEqual("向日葵(Teary Planet)", plan.our_title)
        self.assertEqual(["向日葵(Project Lumina)"], [entry.title for entry in plan.others])
        self.assertEqual("[[Project Lumina]]创作的歌曲", plan.others[0].description)

    def test_own_entry_is_filtered_out_of_others(self):
        # 消歧义页里已经列着自己（条目建过一遍又重跑）时，不能把自己当成「另一含义」
        text = DISAMBIG_PAGE.replace(
            "== 歌曲 ==\n",
            "== 歌曲 ==\n* '''[[向日葵(Teary Planet)]]'''（{{lj|向日葵}}）————[[Teary Planet]]制作。\n")
        plan = self._detect({"ok": True, "exists": True, "disambig": True, "text": text},
                            texts=VOCALOID_TEXTS)
        self.assertEqual(["向日葵(Project Lumina)"], [entry.title for entry in plan.others])

    def test_occupied_by_another_song(self):
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": TIME_MACHINE},
                            song=_song("时光机", "タイムマシン", producers=(("40mP", ()), ("164", ()))))
        self.assertEqual(disambig.MODE_MOVE, plan.mode)
        self.assertEqual("时光机(40mP×164)", plan.our_title)
        self.assertEqual("时光机", plan.others[0].title)

    def test_occupied_by_other_page(self):
        plan = self._detect({"ok": True, "exists": True, "text": "普通条目"})
        self.assertEqual(disambig.MODE_OCCUPIED, plan.mode)
        self.assertIn("非歌曲页面", plan.note)
        self.assertEqual("同名条目", plan.others[0].description)

    def test_without_producer_suffix(self):
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": TIME_MACHINE},
                            song=_song(producers=(), vocalists=()))
        self.assertFalse(plan.needed)
        self.assertIn("没有 P主名", plan.error)

    def test_finish_plan_sets_page_name_and_entry(self):
        plan = self._detect({"ok": True, "exists": True, "disambig": True, "text": DISAMBIG_PAGE},
                            texts=VOCALOID_TEXTS)
        song = _song()
        disambig.finish_plan(plan, song)
        self.assertEqual("向日葵(Teary Planet)", song.page_name)
        self.assertEqual("向日葵(Teary Planet)", plan.our_entry.title)

    def test_finish_plan_without_conflict_keeps_name(self):
        plan = self._detect({"ok": True, "exists": False})
        song = _song()
        disambig.finish_plan(plan, song)
        self.assertIsNone(song.page_name)
        self.assertIsNone(plan.our_entry)


class HandleSubmitTest(TestCase):
    def _plan(self, mode, **kw):
        plan = disambig.Plan(base_title="向日葵", our_title="向日葵(Teary Planet)", mode=mode, **kw)
        plan.our_entry = disambig.Entry("向日葵(Teary Planet)", line="* NEW")
        return plan

    def test_nothing_to_do(self):
        result = disambig.handle_submit(disambig.Plan(base_title="向日葵"))
        self.assertTrue(result["ok"])
        self.assertEqual([], result["steps"])

    def test_occupied_only_reports(self):
        plan = self._plan(disambig.MODE_OCCUPIED, note="简介文字")
        result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(["简介文字"], result["steps"])

    def test_edits_existing_disambig_page(self):
        plan = self._plan(disambig.MODE_DISAMBIG)
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value={"向日葵": DISAMBIG_PAGE}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertTrue(plan.step_page_done)
        self.assertIn("* NEW", edit.call_args.args[1])
        self.assertEqual("向日葵", edit.call_args.args[0])

    def test_move_then_create_disambig_page(self):
        plan = self._plan(disambig.MODE_MOVE,
                          others=[disambig.Entry("向日葵(Project Lumina)", line="* OLD")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=["某页面"]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(["某页面"], result["backlinks"])
        move.assert_called_once()
        self.assertIn("向日葵(Project Lumina)", move.call_args.args[1])
        self.assertEqual("向日葵", edit.call_args.args[0])
        self.assertIn("* OLD", edit.call_args.args[1])
        self.assertIn("* NEW", edit.call_args.args[1])
        self.assertTrue(edit.call_args.kwargs["create_only"])

    def test_retry_does_not_move_twice(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}):
            disambig.handle_submit(plan)
            disambig.handle_submit(plan)
        self.assertEqual(1, move.call_count)

    def test_move_failure_is_reported(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": False, "error": "没权限"}):
            result = disambig.handle_submit(plan)
        self.assertFalse(result["ok"])
        self.assertIn("没权限", result["error"])
        self.assertFalse(plan.step_moved)

    def test_disambig_creation_failure_keeps_move(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": False, "error": "已存在"}):
            result = disambig.handle_submit(plan)
        self.assertFalse(result["ok"])
        self.assertTrue(plan.step_moved)
        self.assertFalse(plan.step_page_done)


class BacklinksTest(TestCase):
    def test_plan_backlinks_counts(self):
        texts = {"A": "见[[时光机]]", "B": "没有链接"}
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts):
            result = disambig.plan_backlinks("时光机", "时光机(1640P)", ["A", "B"])
        self.assertEqual({"title": "A", "count": 1, "kind": "wiki 链接",
                          "snippet": "见[[时光机]]", "reason": ""}, result[0])
        self.assertEqual(0, result[1]["count"])
        self.assertIn("没找到", result[1]["reason"])

    def test_plan_backlinks_recognizes_template_parameter(self):
        texts = {"周刊": BRICKS_WITH_ENTRY}
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts):
            result = disambig.plan_backlinks("向日葵(Teary Planet)", "向日葵(Project Lumina)",
                                             ["周刊"])
        self.assertEqual(1, result[0]["count"])
        self.assertEqual("模板参数（条目）", result[0]["kind"])
        self.assertEqual("", result[0]["reason"])
        self.assertIn("|条目 = 向日葵(Teary Planet)", result[0]["snippet"])

    def test_apply_backlinks_edits_pages(self):
        texts = {"A": "见[[时光机]]", "B": "没有链接"}
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.apply_backlinks("时光机", "时光机(1640P)", ["A", "B"])
        self.assertTrue(result[0]["ok"])
        self.assertEqual("见[[时光机(1640P)]]", edit.call_args.args[1])
        self.assertFalse(result[1]["ok"])

    def test_apply_backlinks_reports_edit_error(self):
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value={"A": "[[时光机]]"}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": False, "error": "受保护"}):
            result = disambig.apply_backlinks("时光机", "时光机(1640P)", ["A"])
        self.assertFalse(result[0]["ok"])
        self.assertEqual("受保护", result[0]["error"])
