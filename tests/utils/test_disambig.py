"""utils/disambig.py 的测试（不联网）：命名、About/Otheruseslist、消歧义页文本、链入替换与处理流程。

口径对照站内真实条目：
    向日葵(Teary Planet)  → {{About|[[Teary Planet|…]]創作的歌曲|[[Project Lumina]]创作的歌曲|向日葵(Project Lumina)}}
    时光机(1640P)         → {{Otheruseslist|40㍍P和164創作的歌曲|[[Aira]]创作的歌曲|时光机(Aira)|…}}
我爱你(Mealerrand)    → 两位 P主 的条目名只写第一位（移动日志 logid 112233 对照）
"""
from pathlib import Path
from unittest import TestCase
from unittest import mock

from models.creators import Creators, Person
from models.song import Image, Lyrics, Song
from utils import disambig, vocadb, wiki_api

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

# 真机 `Template:SmileR`（oldid 258903）：这一行里写的是 `[[Image|image]]`，
# 而条目名（`song.name_chs`）是小写的 `image` —— MediaWiki 认为两者是同一页。
SMILER_TEMPLATE = """{{Navbox
|name = SmileR
|title = [[SmileR]]
|state =  {{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|mw-uncollapsed}}
   |group1 = VOCALOID<br />相關作品
   | list1 = {{Nowrap|[[Melody Line]]}}{{w}}{{Nowrap|[[Time Machine]]}}{{w}}{{Nowrap|[[Image|image]]}}{{w}}{{Nowrap|[[君色]]}}{{w}}{{Nowrap|[[安德洛墨達的夢|アンドロメダの夢]]}}
}}"""

VOCALOID_TEXTS = {
    "向日葵(Project Lumina)": """{{VOCALOID_Songbox
|演唱 = [[IA]]
|歌曲名称 = {{lj|ひまわり}}<br/>向日葵
|P主 = [[Project Lumina]]
}}
[[分类:使用VOCALOID的歌曲]]
""",
}


def _site(redirects=None, simplified=None):
    """把「站上怎么写」的两处查询打桩（默认都不改动名字）—— 单测不联网。

    `redirects` 是 `{重定向标题: 真页面名}`（`utils/wiki_api.redirect_targets`），
    `simplified` 是 `{原文: 站上的中文写法}`（`utils/wiki_api.simplified`）。
    """
    return mock.patch.multiple(
        wiki_api,
        redirect_targets=mock.Mock(return_value=dict(redirects or {})),
        simplified=mock.Mock(side_effect=lambda text: (simplified or {}).get(text, text)),
    )


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
        with _site():
            self.assertEqual("Teary Planet", disambig.producer_title_name("Teary Planet"))

    def test_same_reading_alias_ignores_other_artists(self):
        """`utils.vocadb.artist_aliases` 是按名字模糊搜的，别名要按读音核对再用。

        实测 `ミク` 的别名里有 `Mr.Sm@LL`（别人）、`やなぎなぎ` 的别名里有 `Yanagi`。
        """
        self.assertEqual("MikitoP", disambig.same_reading_alias("みきとP", ["MikitoP", "愛島"]))
        self.assertEqual("Chiitana", disambig.same_reading_alias("ちいたな", ["Titana", "Chiitana"]))
        self.assertEqual("", disambig.same_reading_alias("ミク", ["みくろぷろ", "Mr.Sm@LL"]))
        self.assertEqual("", disambig.same_reading_alias("やなぎなぎ", ["Yanagi", "Nagi"]))
        self.assertEqual("", disambig.same_reading_alias("柊キライ", ["Hiiragi Kirai"]))
        self.assertEqual("", disambig.same_reading_alias("みきとP", []))
        self.assertEqual("", disambig.same_reading_alias("", ["MikitoP"]))

    def test_producer_suffix_takes_only_the_first_producer(self):
        """两位 P主 只写第一位（用户 2026-10-14 按站上移动日志 logid 112233 修订）。

        `我爱你(Mealerrand×Rakuda)` 被管理员移到 `我爱你(Mealerrand)`（原因「錯名」）：
        那一条的 `|P主` 是两位（`[[Mealerrand]]&{{lj|[[駱駝法師]]}}`），
        条目名括号里只写第一位。
        """
        with _site():
            self.assertEqual("40mP", disambig.producer_suffix([Person("40mP"), Person("164")]))
            self.assertEqual("Mealerrand", disambig.producer_suffix(
                [Person("Mealerrand"), Person("駱駝法師", ["Rakuda"])]))
            self.assertEqual("", disambig.producer_suffix([]))

    def test_producer_suffix_writes_the_name_the_way_the_site_does(self):
        """用户 2026-10-15：`鶴三` 的条目名要写「Redial(鹤三)」——不是 VocaDB 的 `Tsuruzou`。"""
        with _site(simplified={"鶴三": "鹤三"}):
            self.assertEqual("鹤三", disambig.producer_suffix([Person("鶴三", ["Tsuruzou"])]))
            self.assertEqual("鹤三", disambig.producer_suffix([Person("鶴三"), Person("164")]))

    def test_producer_suffix_keeps_the_trailing_p(self):
        """结尾的 `P` 照写：站上就是 `八王子P`（不是 `HachioujiP`，也不删 P）。"""
        with _site():
            self.assertEqual("八王子P", disambig.producer_suffix([Person("八王子P", ["HachioujiP"])]))

    def test_producer_suffix_prefers_the_wiki_redirect_target(self):
        """名字在站上是重定向时用真页面名（`kz(livetune)` → `kz`）。"""
        with _site(redirects={"kz(livetune)": "kz"}):
            self.assertEqual("kz", disambig.producer_suffix([Person("kz(livetune)")]))

    def test_producer_title_name_writes_kana_as_romaji(self):
        """假名写罗马音：站内条目就是 `Eureka(柊kirai)` / `生命体征(蜂屋nanashi)`。"""
        with _site():
            self.assertEqual("柊kirai", disambig.producer_title_name("柊キライ"))
            self.assertEqual("蜂屋nanashi", disambig.producer_title_name("蜂屋ななし"))
            self.assertEqual("mikitoP", disambig.producer_title_name("みきとP"))

    def test_producer_title_name_uses_the_alias_for_kana_only_names(self):
        """整名都是假名时优先用同音的别名：`みきとP` → `MikitoP`（站上是 `同级生(MikitoP)`）。"""
        with _site():
            self.assertEqual("MikitoP", disambig.producer_title_name("みきとP", ["MikitoP", "愛島"]))
            self.assertEqual("miku", disambig.producer_title_name("ミク", ["みくろぷろ", "Mr.Sm@LL"]))

    def test_producer_title_name_is_empty_without_a_name(self):
        with _site():
            self.assertEqual("", disambig.producer_title_name(""))

    def test_first_producer_name_unwraps_links_and_takes_the_first(self):
        """`|P主` 里的多位写法（`[[A]]&{{lj|[[B]]}}`）解析出第一位。"""
        self.assertEqual("Mealerrand",
                         disambig.first_producer_name("[[Mealerrand]]&{{lj|[[駱駝法師]]}}"))
        self.assertEqual("40mP", disambig.first_producer_name("[[40mP]]×[[164]]"))
        self.assertEqual("", disambig.first_producer_name(""))

    def test_entry_title(self):
        self.assertEqual("向日葵(Teary Planet)", disambig.entry_title("向日葵", "Teary Planet"))
        self.assertEqual("向日葵", disambig.entry_title("向日葵", ""))

    def test_move_target_uses_the_redirect_target(self):
        """用户 2026-10-15 报的场景：`Redial` 是 `kz(livetune)` 的歌，而 `kz(livetune)`
        是站上 `kz` 的重定向 → 旧条目移到 `Redial(kz)`（不是 `Redial(kz(livetune))`）。"""
        entry = disambig.Entry(title="Redial", producer="[[livetune|kz(livetune)]]")
        with _site(redirects={"kz(livetune)": "kz"}), \
             mock.patch.object(vocadb, "artist_aliases") as aliases:
            self.assertEqual("Redial(kz)", disambig.move_target("Redial", entry))
        aliases.assert_not_called()          # 站上的写法已经够用，不必再问 VocaDB

    def test_move_target_writes_japanese_kanji_as_chinese(self):
        """旧条目的 P主 是日文汉字时按站上的中文写法（`鶴三` → `鹤三`）。"""
        entry = disambig.Entry(title="Redial", producer="{{lj|[[鶴三]]}}")
        with _site(simplified={"鶴三": "鹤三"}):
            self.assertEqual("Redial(鹤三)", disambig.move_target("Redial", entry))

    def test_move_target_asks_vocadb_for_kana_only_producers(self):
        """整名都是假名时拿 VocaDB 的罗马音别名（`みきと` → `MikitoP`，站上带大小写）。"""
        entry = disambig.Entry(title="同级生", producer="[[みきと]]")
        with _site(), mock.patch.object(vocadb, "artist_aliases",
                                        return_value=["MikitoP", "愛島"]) as aliases:
            self.assertEqual("同级生(MikitoP)", disambig.move_target("同级生", entry))
        self.assertEqual("みきと", aliases.call_args.args[0])

    def test_move_target_ignores_an_alias_from_another_artist(self):
        """VocaDB 模糊搜到的别名对不上读音就不用（实测 `ミク` → `Mr.Sm@LL`）。"""
        entry = disambig.Entry(title="X", producer="ミク")
        with _site(), mock.patch.object(vocadb, "artist_aliases",
                                        return_value=["みくろぷろ", "Mr.Sm@LL"]):
            self.assertEqual("X(miku)", disambig.move_target("X", entry))

    def test_move_target_romanizes_kana_without_any_alias(self):
        entry = disambig.Entry(title="X", producer="キライ")
        with _site(), mock.patch.object(vocadb, "artist_aliases", return_value=[]):
            self.assertEqual("X(kirai)", disambig.move_target("X", entry))

    def test_move_target_keeps_the_japanese_name_when_the_site_does_not_change_it(self):
        entry = disambig.Entry(title="涅槃", producer="雄之助")
        with _site(), mock.patch.object(vocadb, "artist_aliases", return_value=[]):
            self.assertEqual("涅槃(雄之助)", disambig.move_target("涅槃", entry))

    def test_move_target_needs_a_producer(self):
        self.assertEqual("", disambig.move_target("涅槃", disambig.Entry(title="涅槃")))

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

    def test_our_entry_uses_the_sites_chinese_vocalist_name(self):
        """VocaDB 给的是日文名，站上条目名是中文：`[[初音ミク]]` → `[[初音未来]]`。

        用户 2026-10-25 对照《潘多拉》修订 275790 → 275795：工具写的消歧义页那一行是
        `[[初音ミク]]`，站上编辑改成了 `[[初音未来]]`（与 Songbox 的 `|演唱=` 同一套写法）。
        """
        song = _song(name_chs="潘多拉", name_jap="パンドラ", producers=(("やしきん", ()),),
                     vocalists=("初音ミク",), page_name="潘多拉(Yashikin)")
        entry = disambig.our_entry(song)
        self.assertIn("[[やしきん]]制作，[[初音未来]]演唱的", entry.line)
        self.assertNotIn("初音ミク", entry.line)

    def test_our_entry_without_japanese_name(self):
        song = _song(name_jap="", page_name="向日葵(Teary Planet)")
        self.assertNotIn("{{lj|}}", disambig.our_entry(song).line)

    def test_parse_entry_reads_songbox(self):
        entry = disambig.parse_entry("时光机(1640P)", TIME_MACHINE)
        self.assertEqual("时光机(1640P)", entry.title)
        self.assertEqual("[[40mP]]×[[164]]创作的歌曲", entry.description)
        self.assertEqual("40mP×164", entry.producer)
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


PANDORA = """{{标题替换|{{lj|パンドラ}}}}
{{VOCALOID Songbox
|image    = Pandora_ATOLS.jpg
|演唱     = [[初音未来]]
|歌曲名称 = {{lj|パンドラ}}<br/>Pandora
|P主      = [[ATOLS]]
}}
[[分类:使用VOCALOID的歌曲]]
"""


class SameNameSongsTest(TestCase):
    """站上另外那些同名曲：日文原名一样、条目名完全不同（《パンドラ》→ [[Pandora]]）。

    用户 2026-10-25 对照《潘多拉》修订 275790 → 275795：工具建的消歧义页只列了自己和
    「让出裸标题」的那一首，第三首同名曲 `[[Pandora]]`（ATOLS）是站上编辑手动补的。
    """

    def _search(self, pages):
        return mock.patch.object(wiki_api, "search_pages_with_text",
                                 return_value=list(pages))

    def test_finds_a_song_listed_under_another_title(self):
        with self._search([("Pandora", PANDORA), ("潘多拉", "'''潘多拉'''可以指："),
                           ("ATOLS", "{{Infobox 人物}}パンドラ を作った")]):
            entries = disambig.same_name_songs("パンドラ", exclude=["潘多拉", "潘多拉(Yashikin)"])
        self.assertEqual(["Pandora"], [entry.title for entry in entries])
        self.assertIn("* '''[[Pandora]]'''（{{lj|パンドラ}}）————[[ATOLS]]制作，"
                      "[[初音未来]]演唱的[[VOCALOID]]日语原创歌曲。", entries[0].line)

    def test_pages_that_only_mention_the_name_are_skipped(self):
        """P主页 / 周刊榜页的正文里也有「パンドラ」，但没有 Songbox 或日文原名对不上。"""
        pages = [("周刊VOCALOID & UTAU排行榜320·262", "…パンドラ…"),
                 ("别的一首歌", "{{VOCALOID Songbox\n|歌曲名称 = {{lj|パンドラじゃない}}\n"
                              "|P主 = [[X]]\n}}")]
        with self._search(pages):
            self.assertEqual([], disambig.same_name_songs("パンドラ"))

    def test_excluded_titles_are_skipped(self):
        with self._search([("Pandora", PANDORA)]):
            self.assertEqual([], disambig.same_name_songs("パンドラ", exclude=["Pandora"]))

    def test_without_a_japanese_name_nothing_is_searched(self):
        with mock.patch.object(wiki_api, "search_pages_with_text") as search:
            self.assertEqual([], disambig.same_name_songs(""))
        search.assert_not_called()

    def test_full_width_and_half_width_names_match(self):
        page = PANDORA.replace("{{lj|パンドラ}}", "{{lj|５℃}}")
        with self._search([("Five", page)]):
            self.assertEqual(["Five"], [entry.title
                                        for entry in disambig.same_name_songs("5℃")])


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

    def test_page_entries_include_the_other_same_name_songs(self):
        plan = disambig.Plan(base_title="潘多拉", our_title="潘多拉(Yashikin)",
                             mode=disambig.MODE_MOVE,
                             others=[disambig.Entry("潘多拉(buzzG)", line="* buzzG")],
                             siblings=[disambig.Entry("Pandora", line="* ATOLS")])
        plan.our_entry = disambig.Entry("潘多拉(Yashikin)", line="* ours")
        self.assertEqual(["潘多拉(buzzG)", "Pandora", "潘多拉(Yashikin)"],
                         [entry.title for entry in disambig.page_entries(plan)])

    def test_append_missing_entries_skips_what_is_already_listed(self):
        entries = [disambig.Entry("向日葵(Project Lumina)", line="* 已有"),
                   disambig.Entry("Pandora", line="* '''[[Pandora]]'''————新的。")]
        text = disambig.append_missing_entries(DISAMBIG_PAGE, entries)
        self.assertEqual(1, text.count("向日葵(Project Lumina)"))
        self.assertIn("* '''[[Pandora]]'''————新的。", text)
        self.assertEqual(text, disambig.append_missing_entries(text, entries))


class ReplaceLinksTest(TestCase):
    def test_plain_and_piped_links(self):
        text = "见[[时光机]]与[[时光机|时光机]]，还有[[时光机(1640P)]]。"
        new_text, count = disambig.replace_links(text, "时光机", "时光机(1640P)")
        # 裸链接要补上原显示名（`[[新名|旧名]]`），不能直接把显示名换成带括号的新名
        self.assertEqual("见[[时光机(1640P)|时光机]]与[[时光机(1640P)|时光机]]，还有[[时光机(1640P)]]。",
                         new_text)
        self.assertEqual(2, count)

    def test_bare_link_keeps_the_displayed_song_name(self):
        """用户 2026-09 给的真实编辑（diff 251406）：`[[涅槃]]` → `[[涅槃(Yunosuke)|涅槃]]`。

        `Synthesizer V殿堂曲/2026年投稿` 里的 `{{Song Honor}}` 块：
        程序以前写成 `|曲目 = [[涅槃(Yunosuke)]]`（diff 251400），榜单那一行就显示出括号里的 P主名了。
        """
        page = ("{{Song Honor\n"
                "|曲目 = [[涅槃]]\n"
                "|P主 = [[雄之助]]\n"
                "|演唱 = 重音Teto\n"
                "|bb_id = BV1frFzzoEdu\n"
                "}}\n")
        new_text, count = disambig.replace_links(page, "涅槃", "涅槃(Yunosuke)")
        self.assertEqual(1, count)
        self.assertIn("|曲目 = [[涅槃(Yunosuke)|涅槃]]", new_text)
        self.assertNotIn("|曲目 = [[涅槃(Yunosuke)]]", new_text)
        self.assertIn("|P主 = [[雄之助]]", new_text)          # 别的链接不动

    def test_piped_link_keeps_its_own_display_text(self):
        new_text, count = disambig.replace_links("见[[涅槃|Nirvana]]。", "涅槃", "涅槃(Yunosuke)")
        self.assertEqual("见[[涅槃(Yunosuke)|Nirvana]]。", new_text)
        self.assertEqual(1, count)

    def test_does_not_touch_templates_or_text(self):
        text = "{{lj|时光机}} 和纯文字时光机"
        new_text, count = disambig.replace_links(text, "时光机", "时光机(1640P)")
        self.assertEqual(text, new_text)
        self.assertEqual(0, count)

    def test_already_replaced_link_is_not_matched_again(self):
        """`[[涅槃(Yunosuke)|涅槃]]` 不能被当成 `[[涅槃…]]` 再改一次。"""
        text = "|曲目 = [[涅槃(Yunosuke)|涅槃]]"
        new_text, count = disambig.replace_links(text, "涅槃", "涅槃(Yunosuke)")
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

    def test_the_first_letter_is_case_insensitive(self):
        """MediaWiki 的标题首字母不分大小写：`[[Image|image]]` 与 `[[image]]` 是同一页。

        用户 2026-10-29 报：生成《image(picco)》时，`Template:SmileR` 里写的
        `[[Image|image]]` 一处都改不出来，模板于是被从「替换模板」名单里丢掉
        （站内 diff 276413 就是人手动改的 `[[image(SmileR)|image]]`）。
        """
        template = '| list1 = {{Nowrap|[[Image|image]]}}{{w}}{{Nowrap|[[君色]]}}'
        new_text, count = disambig.replace_links(template, "image", "image(SmileR)")
        self.assertEqual(1, count)
        self.assertIn("[[image(SmileR)|image]]", new_text, "显示名要保留来源里的原写法")
        # 反过来（标题开头是大写）也要认
        self.assertEqual(1, disambig.replace_links("[[Image]]", "image", "image(SmileR)")[1])

    def test_case_insensitive_match_leaves_other_letters_alone(self):
        self.assertEqual(("[[Images]]", 0),
                         disambig.replace_links("[[Images]]", "image", "image(SmileR)"))

    def test_snippet_is_case_insensitive(self):
        text = "A" * 40 + "[[Image|image]]" + "B" * 40
        self.assertIn("[[Image|image]]", disambig.snippet(text, "image"))

    def test_fix_page_text_handles_the_smileR_template(self):
        """整页核对：模板里那一行 `[[Image|image]]` 要被换成 `[[image(SmileR)|image]]`。"""
        new_text, count, kind = disambig.fix_page_text(SMILER_TEMPLATE, "image",
                                                       "image(SmileR)")
        self.assertEqual(1, count)
        self.assertEqual("wiki 链接", kind)
        self.assertIn("[[image(SmileR)|image]]", new_text)


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
        self.assertEqual(("见[[新名|旧名]]", 1, "wiki 链接"),
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
    def _detect(self, facts, siblings=(), song=None, texts=None, redirects=None, simplified=None,
                same_name=()):
        # 「站上怎么写」（重定向 / 中文写法）、全文搜索与 VocaDB 那条路都打桩，别真联网
        with mock.patch.object(wiki_api, "fetch_page_facts", return_value=facts), \
             mock.patch.object(wiki_api, "list_titles_with_prefix", return_value=list(siblings)), \
             _site(redirects, simplified), \
             mock.patch.object(vocadb, "artist_aliases", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts or {}), \
             mock.patch.object(disambig, "same_name_songs", return_value=list(same_name)):
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
        self.assertEqual([], plan.siblings, "没冲突就不用去搜同名曲")

    def test_other_same_name_songs_are_collected(self):
        """裸标题被占用时，站上另外的同名曲也要收进 plan（只进消歧义页的列表）。"""
        with mock.patch.object(wiki_api, "search_pages_with_text") as search:
            plan = self._detect({"ok": True, "exists": True, "disambig": True, "text": DISAMBIG_PAGE},
                                same_name=[disambig.Entry("Pandora", line="* ATOLS")])
        self.assertEqual(["Pandora"], [entry.title for entry in plan.siblings])
        self.assertIn("Pandora", [entry["title"] for entry in plan.as_dict()["siblings"]])
        search.assert_not_called()               # 打桩的 `same_name_songs` 不走真搜索

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
        self.assertEqual("时光机(40mP)", plan.our_title)
        # 旧条目要被移到「歌名(它自己的P主)」——以前这里算出来是裸标题本身（自己移动自己）；
        # `|P主 = [[40mP]]×[[164]]` 是两位，括号里同样只写第一位（见 logid 112233）
        self.assertEqual("时光机(40mP)", plan.others[0].title)

    def test_japanese_lj_producer_is_unwrapped_for_move_target(self):
        existing = """{{VOCALOID Songbox
|演唱 = [[巡音流歌]]
|歌曲名称 = {{lj|天才}}
|P主 = {{lj|[[奏音69]]}}
}}
[[分类:使用VOCALOID的歌曲]]
"""
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": existing},
                            song=_song("天才", "天才", producers=(("yowanecity", ()),)))

        self.assertEqual(disambig.MODE_MOVE, plan.mode)
        self.assertEqual("天才(yowanecity)", plan.our_title)
        self.assertEqual("天才(奏音69)", plan.others[0].title)
        self.assertEqual("奏音69", disambig.parse_entry("天才", existing).producer)
        self.assertIn("|天才(奏音69)}}", disambig.top_template(plan))
        self.assertTrue(plan.others[0].line.startswith("* '''[[天才(奏音69)]]'''"))

    def test_move_target_uses_the_redirect_target(self):
        """用户 2026-10-15 报的场景：线上 `Redial` 是 `kz(livetune)` 的歌，
        而 `kz(livetune)` 是站上 `kz` 的重定向 → 旧条目移到 `Redial(kz)`。"""
        redial = """{{VOCALOID Songbox
|image = Redial.jpg
|演唱 = [[初音未来]]
|歌曲名称 = Redial
|P主 = [[livetune|kz(livetune)]]
}}"""
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": redial},
                            song=_song("Redial", "リダイヤル", producers=(("鶴三", ()),)),
                            redirects={"kz(livetune)": "kz"}, simplified={"鶴三": "鹤三"})
        self.assertEqual(disambig.MODE_MOVE, plan.mode)
        self.assertEqual("Redial(鹤三)", plan.our_title)
        self.assertEqual("Redial(kz)", plan.others[0].title)
        self.assertEqual("[[livetune|kz(livetune)]]创作的歌曲", plan.others[0].description)
        # 消歧义页里旧条目那一行也要指向新标题（不能还写着已经变成消歧义页的「Redial」）
        self.assertTrue(plan.others[0].line.startswith("* '''[[Redial(kz)]]'''"), plan.others[0].line)

    def test_move_target_keeps_the_japanese_name_when_the_site_does_not_change_it(self):
        """站上不改写 P主名、也没有罗马音别名时用原名做后缀（至少不和裸标题撞车）。"""
        nirvana = "|P主 = [[雄之助]]\n"
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": nirvana},
                            song=_song("涅槃", "ニルヴァー", producers=(("HotaRu", ()),)))
        self.assertEqual("涅槃(雄之助)", plan.others[0].title)

    def test_song_without_producer_cannot_be_moved(self):
        """旧条目连 P主 都没写 → 给它起不了消歧义名，不让它让出裸标题（我们用自己的名）。"""
        plan = self._detect({"ok": True, "exists": True, "song": True, "text": "|演唱 = [[X]]\n"},
                            song=_song("涅槃", "ニルヴァーナ", producers=(("HotaRu", ()),)))
        self.assertEqual(disambig.MODE_OCCUPIED, plan.mode)
        self.assertIn("没写 P主", plan.note)
        self.assertEqual("涅槃", plan.others[0].title)

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


class OtherEntryTopTest(TestCase):
    """旧条目（让出裸标题的那首）顶部要补的模板。

    用户 2026-10-16 给的差异（voca.wiki《Redial(kz)》修订 272305）就是这两行：

        {{标题格式化}}
        {{About|[[livetune|kz(livetune)]]创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)}}
    """

    PAGE = ("{{不完整|需要补充简介}}{{虚拟歌手歌曲荣誉题头|VOCALOID|yrank=3}}\n"
            "{{VOCALOID Songbox\n|P主 = [[livetune|kz(livetune)]]\n}}")

    def _plan(self):
        plan = disambig.Plan(base_title="Redial", our_title="Redial(鹤三)",
                             mode=disambig.MODE_MOVE)
        plan.others = [disambig.Entry("Redial(kz)",
                                      description="[[livetune|kz(livetune)]]创作的歌曲")]
        plan.our_entry = disambig.Entry("Redial(鹤三)", description="[[鶴三]]创作的歌曲",
                                        line="* NEW")
        return plan

    def test_adds_title_format_then_about(self):
        new_text, added = disambig.other_entry_top(self.PAGE, self._plan())
        self.assertEqual(["标题格式化", "About"], added)
        self.assertTrue(new_text.startswith(
            "{{标题格式化}}\n"
            "{{About|[[livetune|kz(livetune)]]创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)}}\n"))
        self.assertIn("{{不完整|需要补充简介}}", new_text)
        self.assertTrue(new_text.endswith("}}"))

    def test_keeps_the_page_own_title_template(self):
        """页面自己写着 `{{标题替换|…}}` 时不再插 `{{标题格式化}}`（两个会抢显示标题）。"""
        text = "{{标题替换|{{lj|ネハン}}}}\n{{VOCALOID Songbox\n}}"
        new_text, added = disambig.other_entry_top(text, self._plan())
        self.assertEqual(["About"], added)
        self.assertFalse(new_text.startswith("{{标题格式化}}"))
        self.assertTrue(new_text.startswith("{{About|"))

    def test_existing_title_format_is_kept(self):
        text = "{{标题格式化}}\n正文"
        new_text, added = disambig.other_entry_top(text, self._plan())
        self.assertEqual(["About"], added)
        self.assertTrue(new_text.startswith("{{About|"))

    def test_does_not_add_a_second_hatnote(self):
        text = "{{标题格式化}}\n{{About|别处的旧写法}}\n正文"
        self.assertEqual((text, []), disambig.other_entry_top(text, self._plan()))

    def test_without_our_entry_only_formats_the_title(self):
        plan = self._plan()
        plan.our_entry = None
        new_text, added = disambig.other_entry_top("正文", plan)
        self.assertEqual(["标题格式化"], added)
        self.assertEqual("{{标题格式化}}\n正文", new_text)

    def test_empty_text_is_left_alone(self):
        self.assertEqual(("", []), disambig.other_entry_top("", self._plan()))
        self.assertEqual(("   ", []), disambig.other_entry_top("   ", self._plan()))


class EntryTopVariantsTest(TestCase):
    """条目顶部模板的其它写法：繁体标题模板、没有括号后缀的标题、3 个同名条目。"""

    @staticmethod
    def _entry(title, description, producer="A"):
        return disambig.Entry(title, description=description, producer=producer)

    def test_traditional_title_template_is_respected(self):
        """站上只有简体标题模板，但条目里也有写「標題替換」的（如 Identity(Kanaria)）。"""
        text = "{{標題替換|zh-hans=时光机}}\n正文"
        entry = self._entry("时光机(1640P)", "[[164]]创作的歌曲")
        new_text, added = disambig.entry_top(text, entry, [self._entry("Redial(鹤三)", "[[鶴三]]创作的歌曲")])
        self.assertEqual(["About"], added)
        self.assertFalse(new_text.startswith("{{标题格式化}}"))

    def test_title_without_parentheses_keeps_title(self):
        """标题里没有括号后缀时 {{标题格式化}} 是空操作，不补（一条多余的编辑）。"""
        text = "正文"
        new_text, added = disambig.entry_top(
            text, self._entry("Melt", "[[ryo]]创作的歌曲"),
            [self._entry("Melt(鹤三)", "[[鶴三]]创作的歌曲")])
        self.assertEqual(["About"], added)
        self.assertFalse(new_text.startswith("{{标题格式化}}"))

    def test_two_siblings_use_otheruseslist(self):
        """同类条目 3 个（含本条）→ {{Otheruseslist|描述|描述1|条目1|描述2|条目2}}。"""
        entry = self._entry("Redial(kz)", "[[kz]]创作的歌曲")
        new_text, added = disambig.entry_top(
            "正文", entry, [self._entry("Redial(鹤三)", "[[鶴三]]创作的歌曲"),
                            self._entry("Redial(164)", "[[164]]创作的歌曲")])
        self.assertEqual(["标题格式化", "Otheruseslist"], added)
        self.assertTrue(new_text.startswith(
            "{{标题格式化}}\n"
            "{{Otheruseslist|kz创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)|"
            "[[164]]创作的歌曲|Redial(164)}}\n"))


class FixOtherEntriesTest(TestCase):
    """往已有消歧义页补自己那一行时，顺手维护页面上其它条目（用户 2026-10-16 要求）。"""

    @staticmethod
    def _plan():
        plan = disambig.Plan(base_title="Redial", our_title="Redial(鹤三)",
                             mode=disambig.MODE_DISAMBIG)
        plan.our_entry = disambig.Entry("Redial(鹤三)", description="[[鶴三]]创作的歌曲", line="* NEW")
        plan.others = [disambig.Entry("Redial(kz)", description="[[livetune|kz(livetune)]]创作的歌曲"),
                       disambig.Entry("Redial(164)", description="[[164]]创作的歌曲")]
        return plan

    _TEXTS = {
        "Redial(kz)": "{{VOCALOID Songbox\n|P主 = [[livetune|kz(livetune)]]\n}}",
        "Redial(164)": "{{标题格式化}}\n{{VOCALOID Songbox\n|P主 = [[164]]\n}}",
    }

    def _run(self, plan, texts):
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            steps = disambig.fix_other_entries(plan, "同名条目消歧义")
        return steps, {call.args[0]: call.args[1] for call in edit.call_args_list}

    def test_adds_missing_templates_to_each_entry(self):
        plan = self._plan()
        steps, edited = self._run(plan, self._TEXTS)
        self.assertEqual(["Redial(164)", "Redial(kz)"], sorted(edited))
        # 缺标题模板的补标题格式化；两个都有通往本条的 Otheruseslist
        self.assertTrue(edited["Redial(kz)"].startswith(
            "{{标题格式化}}\n"
            "{{Otheruseslist|kz(livetune)创作的歌曲|[[164]]创作的歌曲|Redial(164)|"
            "[[鶴三]]创作的歌曲|Redial(鹤三)}}\n"))
        self.assertTrue(edited["Redial(164)"].startswith("{{Otheruseslist|"))
        self.assertNotIn("{{标题格式化}}\n{{标题格式化}}", edited["Redial(164)"])
        self.assertTrue(plan.step_other_done)
        self.assertTrue(all("已给" in step for step in steps), steps)

    def test_entry_with_everything_is_left_alone(self):
        plan = self._plan()
        texts = dict(self._TEXTS)
        texts["Redial(164)"] = ("{{标题格式化}}\n{{About|别处的写法}}\n"
                                "{{VOCALOID Songbox\n|P主 = [[164]]\n}}")
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            disambig.fix_other_entries(plan, "同名条目消歧义")
        self.assertEqual(["Redial(kz)"], [call.args[0] for call in edit.call_args_list])

    def test_non_song_entry_is_skipped(self):
        """消歧义页上也可能是软件 / 专辑页面：读不出 P主 就别乱加歌曲描述。"""
        plan = self._plan()
        texts = dict(self._TEXTS)
        texts["Redial(164)"] = "这是 Synthesizer V 声库页面，没有 Songbox"
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            steps = disambig.fix_other_entries(plan, "同名条目消歧义")
        self.assertEqual(["Redial(kz)"], [call.args[0] for call in edit.call_args_list])
        self.assertTrue(any("不像是歌曲条目" in step for step in steps), steps)

    def test_unreadable_entry_is_reported(self):
        plan = self._plan()
        steps, edited = self._run(plan, {"Redial(kz)": self._TEXTS["Redial(kz)"]})
        self.assertEqual(["Redial(kz)"], sorted(edited))
        self.assertTrue(any("读不到「Redial(164)」" in step for step in steps), steps)

    def test_edit_failure_is_reported(self):
        plan = self._plan()
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=self._TEXTS), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": False, "error": "没权限"}):
            steps = disambig.fix_other_entries(plan, "同名条目消歧义")
        self.assertTrue(all("没权限" in step for step in steps), steps)
        self.assertTrue(plan.step_other_done)

    def test_second_call_does_nothing(self):
        plan = self._plan()
        plan.step_other_done = True
        with mock.patch.object(wiki_api, "fetch_pages_text") as fetch:
            self.assertEqual([], disambig.fix_other_entries(plan, "同名条目消歧义"))
        fetch.assert_not_called()

    def test_without_other_entries_nothing_happens(self):
        plan = self._plan()
        plan.others = []
        with mock.patch.object(wiki_api, "fetch_pages_text") as fetch:
            self.assertEqual([], disambig.fix_other_entries(plan, "同名条目消歧义"))
        fetch.assert_not_called()


class HandleSubmitTest(TestCase):
    def setUp(self):
        # 找链入页面时还会跑一次全文搜索（补 WhatLinksHere 查不到的模板引用）→ 一律打桩，别联网
        patcher = mock.patch.object(wiki_api, "search_text_references", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _plan(self, mode, base_title="向日葵", our_title="向日葵(Teary Planet)", **kw):
        plan = disambig.Plan(base_title=base_title, our_title=our_title, mode=mode, **kw)
        plan.our_entry = disambig.Entry(our_title, line="* NEW")
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

    def test_missing_same_name_songs_are_listed_on_the_existing_page(self):
        """站上另外的同名曲（如 [[Pandora]]）也要补进已有的消歧义页（用户 2026-10-25 的 diff）。"""
        plan = self._plan(disambig.MODE_DISAMBIG,
                          siblings=[disambig.Entry("Pandora", line="* '''[[Pandora]]'''————ATOLS。")])
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value={"向日葵": DISAMBIG_PAGE}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        page = edit.call_args.args[1]
        self.assertIn("* '''[[Pandora]]'''————ATOLS。", page)
        self.assertIn("* NEW", page)
        self.assertLess(page.index("[[Pandora]]"), page.index("* NEW"))
        self.assertTrue(any("顺带补上同名的「Pandora」" in step for step in result["steps"]),
                        result["steps"])

    def test_new_disambig_page_lists_the_other_same_name_songs(self):
        plan = self._plan(disambig.MODE_MOVE,
                          others=[disambig.Entry("向日葵(Project Lumina)", line="* OLD")],
                          siblings=[disambig.Entry("Pandora", line="* ATOLS")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value={}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            disambig.handle_submit(plan)
        page = edit.call_args.args[1]
        self.assertLess(page.index("* OLD"), page.index("* ATOLS"))
        self.assertLess(page.index("* ATOLS"), page.index("* NEW"))

    def test_existing_page_entries_are_maintained_too(self):
        """往已有的消歧义页补自己那一行时，页面上已有的条目也要补模板（用户 2026-10-16 要求）。"""
        plan = self._plan(disambig.MODE_DISAMBIG,
                          others=[disambig.Entry("向日葵(Project Lumina)",
                                                 description="[[Project Lumina]]创作的歌曲")])
        texts = {"向日葵": DISAMBIG_PAGE,
                 "向日葵(Project Lumina)": "{{VOCALOID Songbox\n|P主 = [[Project Lumina]]\n}}"}
        with mock.patch.object(wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {t: texts[t] for t in titles
                                                           if t in texts}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertTrue(plan.step_other_done)
        edited = {call.args[0]: call.args[1] for call in edit.call_args_list}
        self.assertTrue(edited["向日葵(Project Lumina)"].startswith(
            "{{标题格式化}}\n{{About|[[Project Lumina]]创作的歌曲|同名歌曲|向日葵(Teary Planet)}}\n"))
        self.assertIn("* NEW", edited["向日葵"])
        self.assertTrue(any("已给「向日葵(Project Lumina)」加上" in step for step in result["steps"]),
                        result["steps"])

    def test_existing_page_entry_maintenance_runs_only_once(self):
        plan = self._plan(disambig.MODE_DISAMBIG,
                          others=[disambig.Entry("向日葵(Project Lumina)",
                                                 description="[[Project Lumina]]创作的歌曲")])
        texts = {"向日葵(Project Lumina)": "{{VOCALOID Songbox\n|P主 = [[Project Lumina]]\n}}"}
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            disambig.handle_submit(plan)          # 消歧义页读不到 → 失败，但条目已补过模板
            first = [call.args[0] for call in edit.call_args_list]
            disambig.handle_submit(plan)
            second = [call.args[0] for call in edit.call_args_list]
        self.assertEqual(1, first.count("向日葵(Project Lumina)"))
        self.assertEqual(1, second.count("向日葵(Project Lumina)"))

    def test_move_then_create_disambig_page(self):
        plan = self._plan(disambig.MODE_MOVE,
                          others=[disambig.Entry("向日葵(Project Lumina)", line="* OLD")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=["某页面"]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value={}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(["某页面"], result["backlinks"])
        move.assert_called_once()
        self.assertEqual("向日葵", move.call_args.args[0])
        self.assertIn("向日葵(Project Lumina)", move.call_args.args[1])
        self.assertEqual("向日葵", edit.call_args.args[0])
        self.assertIn("* OLD", edit.call_args.args[1])
        self.assertIn("* NEW", edit.call_args.args[1])
        self.assertTrue(edit.call_args.kwargs["create_only"])

    def test_retry_does_not_move_twice(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value={}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}):
            disambig.handle_submit(plan)
            disambig.handle_submit(plan)
        self.assertEqual(1, move.call_count)

    def test_move_failure_is_reported(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value={}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": False, "error": "没权限"}):
            result = disambig.handle_submit(plan)
        self.assertFalse(result["ok"])
        self.assertIn("没权限", result["error"])
        self.assertFalse(plan.step_moved)

    def test_disambig_creation_failure_keeps_move(self):
        plan = self._plan(disambig.MODE_MOVE, others=[disambig.Entry("向日葵(Project Lumina)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text", return_value={}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": False, "error": "已存在"}):
            result = disambig.handle_submit(plan)
        self.assertFalse(result["ok"])
        self.assertTrue(plan.step_moved)
        self.assertFalse(plan.step_page_done)

    def test_moved_entry_gets_title_format_and_about(self):
        """旧条目移到「歌名(P主名)」后要补 `{{标题格式化}}` + `{{About}}`。

        用户 2026-10-16 报：消歧义处理完，**原条目**没人管 —— 标题带了括号却没写标题模板，
        也没有通往新条目的入口（参 voca.wiki《Redial(kz)》修订 272305）。
        """
        old_text = ("{{不完整|需要补充简介}}\n{{VOCALOID Songbox\n"
                    "|P主 = [[livetune|kz(livetune)]]\n}}")
        plan = self._plan(disambig.MODE_MOVE, base_title="Redial", our_title="Redial(鹤三)",
                          others=[disambig.Entry("Redial(kz)",
                                                 description="[[livetune|kz(livetune)]]创作的歌曲")])
        plan.our_entry = disambig.Entry("Redial(鹤三)", description="[[鶴三]]创作的歌曲",
                                        line="* NEW")
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"Redial(kz)": old_text}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertTrue(plan.step_other_done)
        edited = {call.args[0]: call.args[1] for call in edit.call_args_list}
        self.assertTrue(edited["Redial(kz)"].startswith(
            "{{标题格式化}}\n"
            "{{About|[[livetune|kz(livetune)]]创作的歌曲|[[鶴三]]创作的歌曲|Redial(鹤三)}}\n"))
        self.assertIn("* NEW", edited["Redial"])                   # 消歧义页照旧建
        self.assertTrue(any("标题格式化" in step for step in result["steps"]))

    def test_moved_entry_is_edited_only_once(self):
        """重试时不重复改旧条目（也不重复移动 / 建页）。"""
        old_text = "{{VOCALOID Songbox\n|P主 = [[雄之助]]\n}}"
        plan = self._plan(disambig.MODE_MOVE, base_title="涅槃", our_title="涅槃(HotaRu)",
                          others=[disambig.Entry("涅槃(Yunosuke)",
                                                 description="[[雄之助]]创作的歌曲")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"涅槃(Yunosuke)": old_text}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            disambig.handle_submit(plan)
            first = [call.args[0] for call in edit.call_args_list]
            disambig.handle_submit(plan)
            second = [call.args[0] for call in edit.call_args_list]
        self.assertEqual(1, move.call_count)
        self.assertEqual(1, first.count("涅槃(Yunosuke)"))
        self.assertEqual(1, second.count("涅槃(Yunosuke)"))        # 第二次没再动旧条目

    def test_moved_entry_edit_failure_is_reported(self):
        """改不动旧条目时也要说清楚（条目本身照样提交）。"""
        def fake_edit(title, text, summary, **kwargs):
            return {"ok": False, "error": "没权限"} if title == "向日葵(Project Lumina)" \
                else {"ok": True}

        plan = self._plan(disambig.MODE_MOVE,
                          others=[disambig.Entry("向日葵(Project Lumina)",
                                                 description="[[七尾]]创作的歌曲")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"向日葵(Project Lumina)": "{{VOCALOID Songbox\n}}"}), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}), \
             mock.patch.object(wiki_api, "edit_page", side_effect=fake_edit):
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertTrue(any("没权限" in step for step in result["steps"]))

    def test_cover_is_renamed_with_the_page(self):
        """旧条目封面占着裸标题的名字时跟着改名（`涅槃.jpg` → `涅槃(Yunosuke).jpg`），
        并把它的 `|image` 字段改掉（用户 2026-09 要求）。"""
        old_text = "{{VOCALOID Songbox\n|image = 涅槃.jpg\n|P主 = [[雄之助]]\n}}"
        plan = self._plan(disambig.MODE_MOVE,
                          base_title="涅槃", our_title="涅槃(HotaRu)",
                          others=[disambig.Entry("涅槃(Yunosuke)", line="* OLD")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"涅槃(Yunosuke)": old_text}), \
             mock.patch.object(wiki_api, "file_usage", return_value=["涅槃(Yunosuke)"]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}) as edit:
            result = disambig.handle_submit(plan)
        self.assertTrue(result["ok"])
        self.assertEqual(2, move.call_count)                       # 条目 + 封面
        self.assertEqual("File:涅槃.jpg", move.call_args.args[0])
        self.assertEqual("File:涅槃(Yunosuke).jpg", move.call_args.args[1])
        self.assertFalse(move.call_args.kwargs["leave_redirect"])  # 只有它自己用 → 不留重定向
        self.assertEqual("涅槃(Yunosuke)", edit.call_args_list[0].args[0])
        self.assertIn("|image = 涅槃(Yunosuke).jpg", edit.call_args_list[0].args[1])
        self.assertTrue(any("封面" in step for step in result["steps"]))

    def test_cover_redirect_is_kept_when_others_use_the_file(self):
        old_text = "|image = 涅槃.jpg\n"
        plan = self._plan(disambig.MODE_MOVE, base_title="涅槃", our_title="涅槃(HotaRu)",
                          others=[disambig.Entry("涅槃(Yunosuke)")])
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=[]), \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"涅槃(Yunosuke)": old_text}), \
             mock.patch.object(wiki_api, "file_usage",
                               return_value=["涅槃(Yunosuke)", "某列表页"]), \
             mock.patch.object(wiki_api, "move_page", return_value={"ok": True}) as move, \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}):
            result = disambig.handle_submit(plan)
        self.assertTrue(move.call_args.kwargs["leave_redirect"])    # 还有别人在用 → 留重定向
        self.assertTrue(any("重定向" in step for step in result["steps"]))


class FollowedCoverTest(TestCase):
    """旧条目改名后封面文件要不要跟着改（只动「文件名 = 旧条目名」那种）。"""

    def test_cover_named_after_the_page_is_renamed(self):
        self.assertEqual(("涅槃.jpg", "涅槃(Yunosuke).jpg", "|image = 涅槃(Yunosuke).jpg\n"),
                         disambig.followed_cover("|image = 涅槃.jpg\n", "涅槃", "涅槃(Yunosuke)"))

    def test_other_file_names_are_left_alone(self):
        for name in ["向日葵-七尾.jpg", "Miku 1640m guitar13234939.jpg", "涅槃.png.bak"]:
            self.assertEqual(("", "", f"|image = {name}\n"),
                             disambig.followed_cover(f"|image = {name}\n", "涅槃", "涅槃(X)"))

    def test_missing_image_field(self):
        self.assertEqual(("", "", "没有封面"),
                         disambig.followed_cover("没有封面", "涅槃", "涅槃(X)"))


# Template:雄之助 的真实片段（rev 250090 → 251408）：`{{links|条目名{{!}}日文名}}` 里的条目名要跟着改
YNS_PAGE = """{{Navbox
|group1 = 原创曲目
|list1  = {{Navbox subgroup
 |group13 = 2026年
 |list13 = {{lj|{{links|涅槃{{!}}ネハン<!--
                        -->|自律向き愛<!--
                        -->|音速を超えて<!--
                        -->|如蝴蝶一般{{!}}胡蝶が如く<!--
                        -->}}}}
}}
}}"""


class PipedItemTest(TestCase):
    """`{{links|条目名{{!}}日文名}}` 里那个条目名（链接目标）也要能替换。

    用户 2026-09-29 报的 `Template:雄之助`：站上正确编辑（diff 251408）把
    `|list13 = {{lj|{{links|涅槃{{!}}ネハン<!--…-->}}}}` 改成
    `|list13 = {{lj|{{links|涅槃(Yunosuke){{!}}ネハン<!--…-->}}}}`，
    而我们以前一个写法都认不出来（既没有 `[[ ]]` 也不是 `|参数 = 旧名`）。
    """

    def test_target_is_replaced(self):
        new_text, count, kind = disambig.fix_page_text(YNS_PAGE, "涅槃", "涅槃(Yunosuke)")
        self.assertEqual((1, "模板参数（曲目名）"), (count, kind))
        self.assertIn("|list13 = {{lj|{{links|涅槃(Yunosuke){{!}}ネハン<!--", new_text)
        self.assertNotIn("涅槃{{!}}", new_text)
        self.assertIn("|自律向き愛<!--", new_text)              # 别的项、注释都不动
        self.assertEqual(YNS_PAGE.count("-->"), new_text.count("-->"))

    def test_display_side_is_not_touched(self):
        """`{{!}}` 右边是显示名（那边不改），只认参数开头那一侧。"""
        text = "{{links|显示用的名字{{!}}涅槃}}"
        self.assertEqual((text, 0), disambig.replace_piped_items(text, "涅槃", "涅槃(Yunosuke)"))

    def test_longer_names_are_not_matched(self):
        text = "{{lj|[[涅槃(HotaRu)|ニルヴァーナ]]}} {{links|凤凰涅槃}} {{links|涅槃花香}}"
        self.assertEqual((text, 0), disambig.replace_piped_items(text, "涅槃", "涅槃(Yunosuke)"))

    def test_snippet_finds_the_piped_item(self):
        self.assertIn("涅槃{{!}}ネハン", disambig.snippet(YNS_PAGE, "涅槃"))


class FindBacklinksTest(TestCase):
    """链入页面 = 链入表 + 全文搜索里**真能改**的模板 / 模块页。

    `{{links}}` 的链接是 Lua 现拼的，MediaWiki 不把它记进链入表 —— 实测 `Template:雄之助`
    就不在 `涅槃` 的 WhatLinksHere 里，只看链入表会漏掉它（用户 2026-09-29 报的）。
    """

    def test_merges_search_hits_that_can_be_fixed(self):
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=["某条目"]), \
             mock.patch.object(wiki_api, "search_text_references",
                               return_value=["Template:雄之助", "Template:只是提到涅槃"]) as search, \
             mock.patch.object(wiki_api, "fetch_pages_text",
                               return_value={"Template:雄之助": YNS_PAGE,
                                             "Template:只是提到涅槃": "提到涅槃而已"}):
            titles = disambig.find_backlinks("涅槃", "涅槃(Yunosuke)")
        self.assertEqual(["某条目", "Template:雄之助"], titles)
        search.assert_called_once_with("涅槃")

    def test_search_hit_already_in_backlinks_is_not_duplicated(self):
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=["Template:雄之助"]), \
             mock.patch.object(wiki_api, "search_text_references", return_value=["Template:雄之助"]), \
             mock.patch.object(wiki_api, "fetch_pages_text") as texts:
            self.assertEqual(["Template:雄之助"], disambig.find_backlinks("涅槃", "涅槃(Yunosuke)"))
        self.assertFalse(texts.called)          # 没有新页就不用再读一回正文

    def test_search_failure_keeps_wikilinks_only(self):
        with mock.patch.object(wiki_api, "fetch_backlinks", return_value=["某条目"]), \
             mock.patch.object(wiki_api, "search_text_references", return_value=[]):
            self.assertEqual(["某条目"], disambig.find_backlinks("涅槃", "涅槃(Yunosuke)"))


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
        self.assertEqual("见[[时光机(1640P)|时光机]]", edit.call_args.args[1])
        self.assertFalse(result[1]["ok"])

    def test_apply_backlinks_reports_edit_error(self):
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value={"A": "[[时光机]]"}), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": False, "error": "受保护"}):
            result = disambig.apply_backlinks("时光机", "时光机(1640P)", ["A"])
        self.assertFalse(result[0]["ok"])
        self.assertEqual("受保护", result[0]["error"])

    def test_progress_reports_every_page(self):
        """逐页回调：改成的、没有引用的、读不到的都要报一条（界面一个条目冒一条）。"""
        texts = {"A": "见[[时光机]]", "B": "没有链接"}
        seen = []
        with mock.patch.object(wiki_api, "fetch_pages_text", return_value=texts), \
             mock.patch.object(wiki_api, "edit_page", return_value={"ok": True}):
            results = disambig.apply_backlinks("时光机", "时光机(1640P)", ["A", "B", "C"],
                                               progress=seen.append)
        self.assertEqual(["A", "B", "C"], [item["title"] for item in seen])
        self.assertEqual([True, False, False], [bool(item["ok"]) for item in seen])
        self.assertIn("读不到页面内容", seen[2]["error"])
        self.assertEqual(seen, results)                    # 回调里拿到的就是最终结果
