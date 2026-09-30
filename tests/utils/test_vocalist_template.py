"""`utils/vocalist_template.py` 的单测：殿堂页解析、分栏、模板拼接、样式/相关人物继承。

全部 HTTP 都 mock 掉（这一层要求能在终端里跑），真实网络那套由 `.tmp_vt*.py` 那类探针核对。
"""
import unittest
from pathlib import Path
from unittest import mock

from utils import vocalist_template as vt

# —— 站上的真实写法（照 `VOCALOID殿堂曲/2023年投稿` / `VOCALOID神话曲/YouTube投稿/2023年投稿` 抄） ——
HALL_NICO_2023 = """
{{VOCALOID Song Counter|VOCALOID|site=n|rank=1}}
==一览==
{{Temple Song
|color = #39C5BB
|nnd_id = sm41926207
|P主 = {{lj|[[ゆこぴ]]}}
|投稿时间 = 2023-03-15 20:30
|曲目 = [[强风大背头|{{lj|強風オールバック}}]]
|image link = https://example.invalid/a.jpg}}
{{Temple Song
|传说 = 1
|nnd_id = sm1924663
|曲目 = [[把你给碾到哭哦|把你给辗到哭喔♪]](翻)
|image link = https://example.invalid/b.jpg}}
{{Temple Song
|nnd_id = sm999
|曲目 = {{lj|パラオナボーイ}}*
|image link = https://example.invalid/c.jpg}}
"""

HALL_YT_2023 = """
{{Song Honor
|歌手 = 初音未来
|yt_id = tktcOUi-x-A
|P主 = {{lj|[[カニミソP]]}}
|投稿时间 = 2023-02-03 20:07
|条目 = [[细菌污染]]
|image = 细菌污染A.jpg
}}
{{Song Honor
|神话 = 1
|yt_id = mizIaPGNJaE
|條目 = [[Tell Your World]]
}}
"""

HALL_PAGE_2024 = """
{{Temple Song
|曲目 = [[大人渣|{{lj|ド屑}}]]
|nnd_id = sm1}}
{{Temple Song
|传说 = 1
|曲目 = [[大人渣|{{lj|ド屑}}]]
|nnd_id = sm1}}
"""

SONG_PAGE = """{{标题替换|{{lj|強風オールバック}}}}
{{虚拟歌手歌曲荣誉题头|VOCALOID|nrank=2|yrank=4}}
{{VOCALOID Songbox
|image = 强风大背头封面.jpg
|演唱 = [[歌爱雪]]
|歌曲名称 = {{lj|強風オールバック}}<br/>强风大背头
|P主 = {{lj|[[ゆこぴ]]}}
|nnd_id = sm41926207
|nnd_date = 2023-03-15
|yt_id = D6DVTLvOupE
|yt_date = 2023-03-15
}}
《'''{{lj|強風オールバック}}'''》是……
[[分类:使用VOCALOID的歌曲]]
"""

ONLY_YT_PAGE = """{{VOCALOID_Songbox
|演唱 = {{lj|[[歌愛ユキ]]}}
|歌曲名称 = {{lj|此岸花}}
|P主 = {{lj|[[Nejimaki|病みちゃん]]}}
|投稿 =
{{VOCALOID_Songbox/card|yt|N8r4bHSqtPU|2022-03-26|原版|count=11,414|class=deleted}}
{{VOCALOID_Songbox/card|yt|3OudebR5LhM|2022-11-08|重投版|count=1,000+|class=deleted}}
}}
"""

EXISTING_TEMPLATE = """{{#invoke:Nav|box
|name = 歌爱雪
|title = {{coloredlink|#333333|歌爱雪}}
|state = mw-collapsible {{#ifeq:{{{1<noinclude>|uncollapsed</noinclude>}}}|uncollapsed|mw-uncollapsed|mw-collapsed}}
|titlestyle = background:#f38286;color:#333333
|groupstyle = background:#f38286
|evenstyle = background:{{ColorOps|-90|#f38286}}
|list1 = {{#invoke:Nav|box|subgroup
         |titlestyle = background:#f38286;color:#333333
         |groupstyle = text-align:center;background:{{ColorOps|-30|#f38286}};color:#333333
         |title = 相关人物
         |group1 = AH-Software
         |list1 = [[冰山清辉]] • [[miki]] • [[结缘]]
         }}
|list2 = {{#invoke:Nav|box|subgroup
         |title = 歌曲
         |group1 = 传说曲
         |groupstyle = text-align:center;background:{{ColorOps|-30|#f38286}};color:#cccccc
         |list1 = [[Mr.Music]]
         }}
}}<includeonly>{{#if: {{{ nocate | }}} | | {{ac|歌爱雪歌曲}} }}</includeonly><noinclude>[[Category:虚拟歌手模板]]</noinclude>
"""


def _brace_depth(text: str) -> int:
    """`{{ }}` 的配平深度（`{{{…}}}` 这种三联花括号按一层算）—— 不为 0 就是闭合错位。"""
    depth = 0
    index = 0
    while index < len(text):
        if text[index] in "{}":
            end = index
            while end < len(text) and text[end] == text[index]:
                end += 1
            steps = (end - index) // 2
            depth += steps if text[index] == "{" else -steps
            index = end
        else:
            index += 1
    return depth


def _fact(title, ja="", stations=(), ranks=None, date="", singers=("歌爱雪",), exists=True):
    return vt.SongFact(title=title, exists=exists, is_song=exists, ja=ja or title,
                       stations=tuple(stations), ranks=dict(ranks or {}), date=date,
                       singers=tuple(singers))


def _work(name="歌爱雪", split=False):
    return vt.VocalistWork(name=name, engine="VOCALOID", split=split)


# ============================================================ 殿堂页

class HallTitleTest(unittest.TestCase):
    def test_niconico_year_page(self):
        self.assertEqual({"rank": "殿堂曲", "station": "niconico", "year": "2008"},
                         vt.parse_hall_title("VOCALOID殿堂曲/2008年投稿"))

    def test_youtube_year_page(self):
        self.assertEqual({"rank": "神话曲", "station": "YouTube", "year": "2012"},
                         vt.parse_hall_title("VOCALOID神话曲/YouTube投稿/2012年投稿"))

    def test_bilibili_page_without_year(self):
        self.assertEqual({"rank": "传说曲", "station": "bilibili", "year": ""},
                         vt.parse_hall_title("UTAU传说曲/bilibili投稿"))

    def test_overview_page_is_niconico_without_year(self):
        self.assertEqual({"rank": "殿堂曲", "station": "niconico", "year": ""},
                         vt.parse_hall_title("VOCALOID殿堂曲"))

    def test_unknown_station_and_subpage_are_rejected(self):
        self.assertIsNone(vt.parse_hall_title("VOCALOID殿堂曲/acfun投稿"))
        self.assertIsNone(vt.parse_hall_title("VOCALOID殿堂曲/2008年投稿/doc"))
        self.assertIsNone(vt.parse_hall_title("VOCALOID殿堂曲列表"))


class HallParseTest(unittest.TestCase):
    def test_temple_song_entries(self):
        entries = vt.parse_hall_page("VOCALOID殿堂曲/2023年投稿", HALL_NICO_2023)
        self.assertEqual(3, len(entries))
        self.assertEqual(("强风大背头", "強風オールバック", "殿堂曲", "niconico", "2023"),
                         (entries[0].title, entries[0].ja, entries[0].rank,
                          entries[0].station, entries[0].year))
        # ⚠️ 殿堂页里也列着已经升到传说的歌，靠 `|传说 = 1` 标出来
        self.assertEqual(("把你给碾到哭哦", "传说曲"), (entries[1].title, entries[1].rank))
        # 红链写成裸名字（`{{lj|パラオナボーイ}}*`）也要收
        self.assertEqual(("パラオナボーイ", "殿堂曲"), (entries[2].title, entries[2].rank))

    def test_song_honor_entries_and_traditional_key(self):
        # 整页是神话曲：页面里没有 `|神话 = 1` 的那些也按神话曲算
        entries = vt.parse_hall_page("VOCALOID神话曲/YouTube投稿/2023年投稿", HALL_YT_2023)
        self.assertEqual([("细菌污染", "神话曲"), ("Tell Your World", "神话曲")],
                         [(entry.title, entry.rank) for entry in entries])
        self.assertTrue(all(entry.station == "YouTube" for entry in entries))

    def test_higher_flag_raises_the_page_level(self):
        page = ("{{Song Honor|歌手 = 初音未来|yt_id = a|条目 = [[甲]]}}\n"
                "{{Song Honor|神话 = 1|yt_id = b|條目 = [[乙]]}}\n")
        entries = vt.parse_hall_page("VOCALOID殿堂曲/YouTube投稿/2023年投稿", page)
        self.assertEqual([("甲", "殿堂曲"), ("乙", "神话曲")],
                         [(entry.title, entry.rank) for entry in entries])

    def test_highest_flag_wins(self):
        entries = vt.parse_hall_page("VOCALOID殿堂曲/2024年投稿", HALL_PAGE_2024)
        self.assertEqual(["殿堂曲", "传说曲"], [entry.rank for entry in entries])

    def test_hall_page_titles_filters_subpages(self):
        with mock.patch.object(vt.wiki_api, "pages_with_prefix", side_effect=[
                ["VOCALOID殿堂曲", "VOCALOID殿堂曲/2008年投稿", "VOCALOID殿堂曲/2008年投稿/doc"],
                [], [], []]):
            self.assertEqual(["VOCALOID殿堂曲", "VOCALOID殿堂曲/2008年投稿"],
                             vt.hall_page_titles("VOCALOID"))

class FetchHallsTest(unittest.TestCase):
    def test_batches_and_retries_missing_pages(self):
        calls = []
        failed_once = set()

        def fake_fetch(titles, batch=None):
            calls.append(list(titles))
            if "VOCALOID殿堂曲/2007年投稿" in titles and \
                    "VOCALOID殿堂曲/2007年投稿" not in failed_once:
                failed_once.add("VOCALOID殿堂曲/2007年投稿")
                return {title: HALL_NICO_2023 for title in titles
                        if title != "VOCALOID殿堂曲/2007年投稿"}
            return {title: HALL_NICO_2023 for title in titles}

        with mock.patch.object(vt, "hall_page_titles",
                               return_value=["VOCALOID殿堂曲/2023年投稿",
                                             "VOCALOID殿堂曲/2007年投稿"]), \
                mock.patch.object(vt.wiki_api, "fetch_pages_text", side_effect=fake_fetch), \
                mock.patch.object(vt.time, "sleep"):
            entries, pages = vt.fetch_halls("VOCALOID")
        self.assertEqual(["VOCALOID殿堂曲/2007年投稿", "VOCALOID殿堂曲/2023年投稿"],
                         sorted(pages))
        self.assertEqual(6, len(entries))                 # 两个页面各 3 条（重试不会重复收）
        self.assertEqual(2, len(calls))                   # 一批一次请求（不是一页一次）

    def test_two_failed_rounds_are_reported(self):
        def always_fail(titles, batch=None):
            return {}

        with mock.patch.object(vt, "hall_page_titles",
                               return_value=["VOCALOID殿堂曲/2023年投稿"]), \
                mock.patch.object(vt.wiki_api, "fetch_pages_text", side_effect=always_fail), \
                mock.patch.object(vt.time, "sleep"):
            entries, pages = vt.fetch_halls("VOCALOID")
        self.assertEqual([], pages)
        self.assertEqual([], entries)


# ============================================================ 条目侧

class HonorRankTest(unittest.TestCase):
    def test_station_ranks(self):
        self.assertEqual({"niconico": 2, "YouTube": 4},
                         vt.honor_ranks("{{虚拟歌手歌曲荣誉题头|VOCALOID|nrank=2|yrank=4}}"))

    def test_zero_and_unknown_are_ignored(self):
        self.assertEqual({}, vt.honor_ranks("{{虚拟歌手歌曲荣誉题头|VOCALOID}}"))
        self.assertEqual({"bilibili": 1},
                         vt.honor_ranks("{{虚拟歌手歌曲荣誉题头|VOCALOID|brank=1|nrank=0}}"))


class SongFactTest(unittest.TestCase):
    def test_stations_dates_singers_ja(self):
        fact = vt.song_fact("强风大背头", SONG_PAGE)
        self.assertTrue(fact.exists)
        self.assertTrue(fact.is_song)
        self.assertEqual(("niconico", "YouTube"), fact.stations)
        self.assertEqual("2023-03-15", fact.date)
        self.assertEqual("2023", fact.year)
        self.assertEqual("強風オールバック", fact.ja)
        self.assertEqual(("歌爱雪",), fact.singers)
        self.assertEqual({"niconico": 2, "YouTube": 4}, fact.ranks)

    def test_only_youtube_card(self):
        fact = vt.song_fact("此岸花", ONLY_YT_PAGE)
        self.assertEqual(("YouTube",), fact.stations)

    def test_missing_page(self):
        fact = vt.song_fact("没有这条", None)
        self.assertFalse(fact.exists)
        self.assertFalse(fact.is_song)
        self.assertEqual((), fact.stations)

    def test_fetch_facts_retries_missing_texts(self):
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=[{}, {"A": SONG_PAGE}]) as fetch, \
                mock.patch.object(vt.wiki_api, "pages_exist",
                                  return_value={"A": True, "B": False}) as exists:
            facts = vt.fetch_song_facts(["A", "B"])
        self.assertTrue(facts["A"].exists)
        self.assertEqual(2, fetch.call_count)             # 第一趟 + 重抓
        self.assertTrue(exists.called)
        self.assertFalse(facts["B"].exists)               # 真的没有这一页 → 红链

    def test_fetch_facts_treats_unknowable_pages_as_read_failures(self):
        with mock.patch.object(vt.wiki_api, "fetch_pages_text", return_value={}), \
                mock.patch.object(vt.wiki_api, "pages_exist", return_value={}), \
                mock.patch.object(vt, "song_fact") as fact:
            fact.return_value = vt.SongFact(title="A")
            vt.fetch_song_facts(["A"])
        self.assertTrue(fact.return_value.exists)         # 查不出来就别当红链


class BelongsTest(unittest.TestCase):
    def test_singers_field(self):
        self.assertTrue(vt.belongs_to(SONG_PAGE, "歌爱雪"))
        self.assertFalse(vt.belongs_to(SONG_PAGE, "重音Teto"))


# ============================================================ 分栏

class ClassifyTest(unittest.TestCase):
    def test_each_station_keeps_its_own_rank(self):
        """同一首歌在不同站点各算各的（重音Teto/2024 里 催眠者 就是这样）。"""
        work = _work()
        entries = [vt.HallEntry("催眠者", "催眠者", vt.RANK_HALL, "niconico", "2024"),
                   vt.HallEntry("催眠者", "催眠者", vt.RANK_MYTH, "niconico", "2024"),
                   vt.HallEntry("催眠者", "催眠者", "破亿曲", "YouTube", "2024")]
        facts = {"催眠者": _fact("催眠者", stations=("niconico", "YouTube"))}
        vt.classify(work, entries, facts, ["催眠者"])
        song = work.songs[0]
        self.assertEqual("破亿播放曲目", song.rank)
        self.assertEqual([("破亿播放曲目", "YouTube"), ("神话曲", "niconico")], song.places)
        self.assertEqual(("YouTube",), song.stations)
        self.assertEqual("2024", song.year)
        self.assertFalse(song.flag)

    def test_other_rows_split_by_station(self):
        work = _work()
        facts = {"只上YouTube": _fact("只上YouTube", stations=("YouTube",)),
                 "有b站": _fact("有b站", stations=("niconico", "bilibili")),
                 "查不到": _fact("查不到", stations=())}
        vt.classify(work, [], facts, ["只上YouTube", "有b站", "查不到"])
        others = {song.title: song for song in work.songs}
        self.assertEqual(vt.OTHER_YOUTUBE, others["只上YouTube"].other_kind)
        self.assertEqual(vt.OTHER_UNHALL, others["有b站"].other_kind)
        self.assertEqual([("其他", "niconico"), ("其他", "bilibili")], others["有b站"].places)
        self.assertIn("站点与栏都定不下来", others["查不到"].flag)

    def test_hall_pages_win_over_the_honor_header_and_conflicts_are_flagged(self):
        work = _work()
        entries = [vt.HallEntry("强风大背头", "強風オールバック", vt.RANK_HALL, "niconico", "2023")]
        facts = {"强风大背头": _fact("强风大背头", ja="強風オールバック",
                                 stations=("niconico", "YouTube"),
                                 ranks={"niconico": 2, "YouTube": 4}, date="2023-03-15")}
        vt.classify(work, entries, facts, ["强风大背头"])
        song = work.songs[0]
        self.assertEqual([("殿堂曲", "niconico")], song.places)     # 殿堂页说话
        self.assertIn("殿堂页算的是第 1 档", song.flag)               # 但对不上要复核
        self.assertEqual(1, len(work.flags))

    def test_honor_header_fills_in_when_the_hall_pages_miss_it(self):
        work = _work()
        facts = {"某曲": _fact("某曲", stations=("niconico",), ranks={"niconico": 2})}
        vt.classify(work, [], facts, ["某曲"])
        song = work.songs[0]
        self.assertEqual("荣誉题头", song.source)
        self.assertEqual(vt.OTHER_UNHALL, song.kind)

    def test_matched_by_japanese_name(self):
        work = _work()
        entries = [vt.HallEntry("强风大背头", "強風オールバック", vt.RANK_LEGEND, "niconico", "2023")]
        facts = {"強風オールバック": _fact("強風オールバック", ja="強風オールバック",
                                       stations=("niconico",), date="2023-03-15")}
        vt.classify(work, entries, facts, ["強風オールバック"])
        self.assertEqual("传说曲", work.songs[0].rank)

    def test_earliest_year_wins(self):
        work = _work()
        entries = [vt.HallEntry("A", "A", vt.RANK_HALL, "niconico", "2023"),
                   vt.HallEntry("A", "A", vt.RANK_HALL, "niconico", "2022")]
        vt.classify(work, entries, {"A": _fact("A", stations=("niconico",))}, ["A"])
        self.assertEqual("2022", work.songs[0].year)

    def test_missing_entry_is_flagged(self):
        work = _work()
        vt.classify(work, [], {"A": _fact("A", exists=False)}, ["A"])
        self.assertIn("条目不存在（红链）", work.songs[0].flag)


# ============================================================ 继承

class InheritTest(unittest.TestCase):
    def test_styles_come_from_the_head_only(self):
        """内层 subgroup 的 `|groupstyle = …;color:#cccccc` 不能当成外层字色。"""
        styles = vt.parse_styles(EXISTING_TEMPLATE)
        self.assertEqual({"titleBg": "#f38286", "titleFg": "#333333",
                          "groupBg": "#f38286"}, styles)
        self.assertNotIn("groupFg", styles)
        self.assertNotIn("listBg", styles)

    def test_relation_block_is_taken_whole(self):
        block = vt.extract_relation(EXISTING_TEMPLATE)
        self.assertTrue(block.startswith("{{#invoke:Nav|box|subgroup"))
        self.assertIn("|title = 相关人物", block)
        self.assertIn("[[结缘]]", block)
        self.assertNotIn("|title = 歌曲", block)          # 只取「相关人物」那一块
        self.assertTrue(block.endswith("}}"))

    def test_group_style_form(self):
        text = "{{Navbox|group1 = 相关人物|list1 = [[A]] • [[B]]|group2 = 歌曲}}"
        self.assertEqual("[[A]] • [[B]]", vt.extract_relation(text))

    def test_remap_colors_follows_the_style_page(self):
        work = _work()
        work.existing = EXISTING_TEMPLATE
        work.relation = vt.extract_relation(EXISTING_TEMPLATE)
        work.styles = {**vt.parse_styles(EXISTING_TEMPLATE), "groupBg": "#123456"}
        self.assertIn("#123456", vt.relation_text(work))
        self.assertNotIn("#f38286;color:#333333\"", vt.relation_text(work))


# ============================================================ 生成模板

class BuildTemplateTest(unittest.TestCase):
    def setUp(self):
        self.work = vt.VocalistWork(
            name="重音Teto", engine="UTAU", split=True,
            songs=[
                vt.VocalistSong(title="催眠者", ja="メズマライザー", year="2024",
                                date="2024-01-14", places=[("破亿播放曲目", "YouTube"),
                                                           ("神话曲", "niconico")],
                                kind="", source="殿堂页"),
                vt.VocalistSong(title="テトリス", ja="テトリス", year="2024",
                                date="2024-11-18", places=[("殿堂曲", "bilibili")]),
                vt.VocalistSong(title="旧曲", ja="旧曲", year="2008",
                                date="2008-04-01", places=[("其他", "niconico")],
                                kind=vt.OTHER_UNHALL),
            ],
            styles={"titleBg": "#d93a49", "titleFg": "#f2dfe6", "groupBg": "#d93a49"},
            relation="{{#invoke:Nav|box|subgroup|title = 相关人物|list1 = [[呗音Uta]]}}")

    def test_split_main_template_lists_years(self):
        text = vt.build_main_template(self.work)
        self.assertIn("|name = 重音Teto", text)
        self.assertIn("|group1 = 相关人物", text)
        self.assertIn("{{#invoke:Nav|box|subgroup|title = 相关人物", text)
        self.assertIn("|list2 = {{重音Teto/2008|nocate=1|state=uncollapsed|child|noabove}}",
                      text)
        self.assertIn("|list3 = {{重音Teto/2024|nocate=1|state=uncollapsed|child|noabove}}",
                      text)
        self.assertIn("<noinclude>{{Documentation}}[[Category:重音Teto模板]]</noinclude>", text)
        self.assertNotIn("|group2 = 歌曲", text)          # 拆分了就不在里面塞曲目

    def test_year_page_has_rank_and_station_rows(self):
        text = vt.build_year_page(self.work, "2024")
        self.assertIn("|name = 重音Teto/2024", text)
        self.assertIn("年歌曲", text)
        self.assertIn("{{虚拟歌姬年份计算|年份=2024|歌姬名=重音Teto|color=#f2dfe6}}", text)
        self.assertIn("|group1 = 神话曲", text)
        self.assertIn("|group2 = 殿堂曲", text)
        self.assertIn("|group3 = 破亿播放曲目", text)
        self.assertIn("|group1 = niconico", text)
        self.assertIn("|group1 = bilibili", text)
        self.assertIn("|group1 = YouTube", text)
        self.assertIn("{{lj|[[催眠者|メズマライザー]]}}", text)
        self.assertIn("<includeonly>{{#if: {{{ nocate | }}} | | {{ac|重音Teto歌曲}} }}"
                      "</includeonly>", text)

    def test_non_split_template_nests_years_under_stations(self):
        self.work.split = False
        text = vt.build_main_template(self.work)
        self.assertIn("|group1 = 相关人物", text)
        self.assertIn("|group2 = 歌曲", text)
        self.assertIn("|group3 = 破亿播放曲目", text)
        self.assertIn("[[Category:虚拟歌手模板]]", text)     # 不分年份的模板挂站上那个分类

    def test_other_row_uses_the_site_label_and_flat_list_when_only_one_kind(self):
        """「其他」栏按站上写法：栏名带 `{{注||收录Vocawiki已有条目。}}`。

        只有一种子栏（这里只有「部分未殿堂曲」）时**直接平铺曲目**，不套一层只有一行的小格 ——
        实测站上 NurseRobot TypeT / 琴叶茜 / 琴叶葵 / 双叶凑音 / SeeU 都是这样；
        两种子栏都有时才套「部分未殿堂曲 / 部分YouTube投稿」（`Template:歌爱雪`）。
        """
        self.work.split = False
        text = vt.build_main_template(self.work)
        self.assertIn("|group4 = 其他{{注||收录Vocawiki已有条目。}}", text)
        self.assertIn("|list4 = [[旧曲]]", text)             # 那一行只有其他栏里这一首
        self.assertNotIn("{{mousetext|部分未殿堂曲", text)    # 单一子栏 → 不套小格
        self.assertNotIn("|list1 = [[旧曲]]", text)

    def test_both_other_kinds_get_their_own_subgroup(self):
        self.work.split = False
        self.work.songs.append(vt.VocalistSong(
            title="只发YT", ja="ユーチューブ", year="2019", date="2019-01-01",
            places=[("其他", "YouTube")], kind=vt.OTHER_YOUTUBE))
        text = vt.build_main_template(self.work)
        self.assertIn("|group4 = 其他{{注||收录Vocawiki已有条目。}}", text)
        self.assertIn("|group1 = {{mousetext|部分未殿堂曲|指niconico及bilibili投稿}}", text)
        self.assertIn("|group2 = 部分YouTube投稿", text)
        self.assertIn("{{lj|[[只发YT|ユーチューブ]]}}", text)

    def test_other_row_can_group_by_year(self):
        """用户 2026-09-30：其他栏也可以**按年份分层**（站上 里命 / 狐子 / 鸣花姬·尊）。"""
        self.work.split = False
        self.work.other_years = True
        self.work.songs.append(vt.VocalistSong(
            title="旧曲二", ja="旧曲二", year="2019", date="2019-02-01",
            places=[("其他", "niconico")], kind=vt.OTHER_UNHALL))
        text = vt.build_main_template(self.work)
        body = text[text.index("|group4 = 其他"):]
        self.assertIn("|group1 = 2008年", body)
        self.assertIn("|list1 = [[旧曲]]", body)
        self.assertIn("|group2 = 2019年", body)
        self.assertIn("|list2 = [[旧曲二]]", body)
        self.assertNotIn("{{mousetext|部分未殿堂曲", body)
        self.assertNotIn("催眠者", body)          # 殿埅曲别混进「其他」的年份小格

    def test_other_by_year_falls_back_to_flat_with_a_single_year(self):
        self.work.split = False
        self.work.other_years = True             # 其他栏只有 2008 年一首
        text = vt.build_main_template(self.work)
        body = text[text.index("|group4 = 其他"):]
        self.assertIn("|list4 = [[旧曲]]", body)
        self.assertNotIn("2008年", body)          # 就一年 → 不再套一层

    def test_the_copy_keeps_the_other_layout(self):
        self.work.other_years = True
        self.assertTrue(self.work.copy().other_years)

    def test_subgroups_close_with_two_braces(self):
        """每个 `{{Navbox subgroup}}` 的收尾必须是**两个** `}}`：

        少一个就会闭合错位（用户 2026-09-30 报的 `Template:弗里摩侠`：`{{Navbox subgroup}}`
        全都只关了一个 `}`）。收尾缩进跟子分组自身一致，最外层收在 2 格 ——
        与用户手改后发到站上的那份逐字一致。
        """
        self.work.split = False
        self.work.other_years = True                    # 「其他」栏按年份分层 → 两层嵌套
        self.work.songs.append(vt.VocalistSong(
            title="旧曲二", ja="旧曲二", year="2019", date="2019-02-01",
            places=[("其他", "niconico")], kind=vt.OTHER_UNHALL))
        text = vt.build_main_template(self.work)
        self.assertEqual(0, _brace_depth(text))
        # 年份小格（缩进 4）→ 其他栏（缩进 0）→ Navbox 三层收尾
        self.assertIn("\n    }}\n  }}\n}}\n", text)

    def test_year_page_braces_are_balanced(self):
        self.assertEqual(0, _brace_depth(vt.build_year_page(self.work, "2024")))
        self.assertEqual(0, _brace_depth(vt.build_main_template(self.work)))

    def test_hall_songs_never_leak_into_the_other_row(self):
        """殿堂 / 传说 / 神话曲的 `kind` 是空的，`other_kind` 会兜底成「部分未殿堂曲」——\
        所以其他栏必须先按**栏**筛一遍，否则整张表会连殿堂曲一起列进「其他」。
        """
        self.work.split = False
        text = vt.build_main_template(self.work)
        other_line = next(line.strip() for line in text.split("\n")
                          if line.strip().startswith("|list4 = "))
        self.assertEqual("|list4 = [[旧曲]]", other_line)
        for title in ("催眠者", "テトリス"):
            self.assertNotIn(title, other_line)

    def test_doc_lists_the_year_pages(self):
        text = vt.build_doc(self.work)
        self.assertIn("[[Template:重音Teto/2024]]", text)
        self.assertIn("{{指定页面编辑按钮|Template:重音Teto/2024|编辑}}", text)
        self.assertIn("[[Template:重音Teto/2008]]", text)

    def test_page_specs_order_and_files(self):
        with mock.patch.object(vt, "get_output_path", return_value=Path("/tmp/out")):
            specs = vt.page_specs(self.work)
        self.assertEqual(["Template:重音Teto/2008", "Template:重音Teto/2024",
                          "Template:重音Teto/doc", "Template:重音Teto"],
                         [spec["name"] for spec in specs])
        self.assertEqual("歌姬模板_重音Teto_2024.wikitext", specs[1]["file"].name)
        self.assertEqual("歌姬模板_重音Teto_doc.wikitext", specs[2]["file"].name)
        self.assertEqual("歌姬模板_重音Teto.wikitext", specs[3]["file"].name)

    def test_template_links_reads_both_forms(self):
        text = ("{{lj|[[催眠者|メズマライザー]]}} • [[テトリス]] "
                "{{lj|[[テトリス|テトリス]]}} • [[Template:重音Teto/2024]]")
        self.assertEqual(["催眠者", "テトリス"], vt.template_links(text))

    def test_rank_counts_counts_every_placement(self):
        counts = vt.rank_counts(self.work)
        self.assertEqual({"破亿播放曲目": 1, "神话曲": 1, "殿堂曲": 1, "其他": 1}, counts)


class TemplateCallTest(unittest.TestCase):
    """写回条目的写法（用户 2026-09-30 定的）：

    * 曲子条目 → `{{歌姬/年份|collapsed}}`（在歌曲条目里默认折叠）；
    * 歌姬条目 → `{{歌姬|nocate=1}}`；
    * 位置 → `position="after_producer"`（P主模板后面、活动模板前面）。
    """

    def test_split_uses_the_year_page(self):
        work = vt.VocalistWork(name="重音Teto", split=True, songs=[
            vt.VocalistSong(title="2代目閻魔", year="2024")])
        self.assertEqual(("重音Teto", "重音Teto/2024|collapsed"),
                         vt.template_call_for(work, "2代目閻魔"))
        self.assertEqual(("重音Teto", "重音Teto|nocate=1"),
                         vt.template_call_for(work, "重音Teto"))

    def test_not_split_uses_the_main_template(self):
        work = vt.VocalistWork(name="歌爱雪", split=False)
        self.assertEqual(("歌爱雪", "歌爱雪|collapsed"), vt.template_call_for(work, "不去大海"))

    def test_insert_groups_by_year_page(self):
        work = vt.VocalistWork(name="重音Teto", split=True, songs=[
            vt.VocalistSong(title="A", year="2024"), vt.VocalistSong(title="B", year="2023")])
        with mock.patch.object(vt, "insert_into_pages",
                               return_value=[{"title": "x", "ok": True, "count": 1}]) as insert:
            vt.insert_into_pages_for(work, ["A", "B", "重音Teto"])
        calls = {(call.args[0], call.kwargs.get("call")): call.args[1] for call in insert.call_args_list}
        self.assertEqual({"A": ["A"]},
                         {group[0]: group for group in calls.values() if group == ["A"]})
        self.assertIn(("重音Teto", "重音Teto/2024|collapsed"), calls)
        self.assertIn(("重音Teto", "重音Teto/2023|collapsed"), calls)
        self.assertIn(("重音Teto", "重音Teto|nocate=1"), calls)
        self.assertTrue(all(call.kwargs.get("position") == "after_producer"
                            for call in insert.call_args_list))

    def test_song_pages_lose_the_handwritten_singer_category(self):
        """曲子条目里手写的 `[[分类:<歌姬>歌曲]]` 要删掉（模板自己会加这个分类）。

        用户 2026-09-30 报的：`阿卡贝拉一起唱！！` 里同时有 `{{弗里摩侠|collapsed}}` 与
        `[[分类:弗里摩侠歌曲]]`。歌姬条目那一份带 `nocate=1`，不加分类，也就不删东西。
        """
        work = vt.VocalistWork(name="弗里摩侠", split=False)
        with mock.patch.object(vt, "insert_into_pages",
                               return_value=[{"title": "x", "ok": True, "count": 1}]) as insert:
            vt.insert_into_pages_for(work, ["阿卡贝拉一起唱！！", "弗里摩侠"])
        drops = {call.kwargs.get("call"): call.kwargs.get("drop_category")
                 for call in insert.call_args_list}
        self.assertEqual({"弗里摩侠|collapsed": "弗里摩侠歌曲", "弗里摩侠|nocate=1": ""}, drops)


class EffectiveStylesTest(unittest.TestCase):
    def test_new_template_starts_from_nothing(self):
        """新建模板的配色从空开始（用户 2026-09-30）—— 不套 P主模板那套蓝黄。"""
        self.assertEqual({}, vt.effective_styles(_work()))

    def test_edited_styles_win_over_defaults(self):
        work = _work()
        work.styles = {"titleBg": "#ff0000"}
        self.assertEqual({"titleBg": "#ff0000"}, vt.effective_styles(work))

    def test_existing_template_style_wins_over_defaults(self):
        work = _work()
        work.existing = EXISTING_TEMPLATE
        styles = vt.effective_styles(work)
        self.assertEqual("#f38286", styles["titleBg"])
        self.assertNotIn("groupFg", styles)               # 没设的别拿默认值顶上

    def test_user_edits_win(self):
        work = _work()
        work.existing = EXISTING_TEMPLATE
        work.styles = {"titleBg": "#000000"}
        self.assertEqual("#000000", vt.effective_styles(work)["titleBg"])


# ============================================================ 抓全流程

class PrepareWorkTest(unittest.TestCase):
    def test_prepare_work_end_to_end(self):
        entries = [vt.HallEntry("强风大背头", "強風オールバック", vt.RANK_LEGEND, "niconico",
                                "2023", page="VOCALOID传说曲/2023年投稿")]
        facts = {"强风大背头": _fact("强风大背头", ja="強風オールバック",
                                 stations=("niconico", "YouTube"), date="2023-03-15"),
                 "不去大海": _fact("不去大海", stations=("niconico",), date="2009-12-17")}
        with mock.patch.object(vt.wiki_api, "category_members",
                               return_value=["强风大背头", "不去大海"]), \
                mock.patch.object(vt, "fetch_halls", return_value=(entries, ["P"])), \
                mock.patch.object(vt, "fetch_song_facts", return_value=facts):
            work = vt.prepare_work("歌爱雪", split=True)
        self.assertEqual("VOCALOID", work.engine)
        self.assertEqual(2, len(work.songs))
        self.assertEqual(["2009", "2023"], work.years())
        self.assertIn("2 首曲子", work.summary)
        self.assertEqual("殿堂页：VOCALOID传说曲/2023年投稿", work.songs[0].source)

    def test_empty_category_that_does_not_exist(self):
        with mock.patch.object(vt.wiki_api, "category_members", return_value=[]), \
                mock.patch.object(vt.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": False}):
            with self.assertRaises(ValueError) as raised:
                vt.prepare_work("不存在的歌姬")
        self.assertIn("没有分类", str(raised.exception))

    def test_empty_category_that_exists(self):
        with mock.patch.object(vt.wiki_api, "category_members", return_value=[]), \
                mock.patch.object(vt.wiki_api, "fetch_page_facts",
                                  return_value={"ok": True, "exists": True}):
            with self.assertRaises(ValueError) as raised:
                vt.prepare_work("空分类歌姬")
        self.assertIn("是空的", str(raised.exception))

    def test_category_read_failure_is_not_reported_as_missing(self):
        with mock.patch.object(vt.wiki_api, "category_members", return_value=[]), \
                mock.patch.object(vt.wiki_api, "fetch_page_facts",
                                  return_value={"ok": False, "exists": False}):
            with self.assertRaises(ValueError) as raised:
                vt.prepare_work("歌爱雪")
        self.assertIn("读取分类", str(raised.exception))

    def test_too_many_songs_is_refused(self):
        with mock.patch.object(vt.wiki_api, "category_members",
                               return_value=[f"歌{i}" for i in range(10)]):
            with self.assertRaises(ValueError) as raised:
                vt.prepare_work("初音未来", max_songs=5)
        self.assertIn("超过上限", str(raised.exception))

    def test_load_existing_reads_styles_and_relation(self):
        work = _work()
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               return_value={"Template:歌爱雪": EXISTING_TEMPLATE,
                                             "Template:歌爱雪/doc": "文档正文"}):
            vt.load_existing(work)
        self.assertEqual("#f38286", work.styles["titleBg"])
        self.assertIn("相关人物", work.relation)
        self.assertEqual("文档正文", work.existing_doc)


class SubpagesOnlyTest(unittest.TestCase):
    """「只新建年份子页、不动既有主模板」（用户 2026-09-30）。

    `Template:初音未来` 那种手写大导航框（`{{Navbox with collapsible groups}}` + `|selected`，
    分组是「角色 / 官方专辑 / 2009年…2019年 / 演唱会 …」）不该让工具整个重写：
    只把 `Template:<歌姬>/<年份>` 写好，之后自己把子页挂上去。
    """

    def _big_work(self) -> vt.VocalistWork:
        return vt.VocalistWork(
            name="初音未来", engine="VOCALOID", split=True, subpages_only=True, songs=[
                vt.VocalistSong(title="歌A", ja="歌A", year="2009", date="2009-06-25",
                                places=[("殿堂曲", "niconico")]),
                vt.VocalistSong(title="歌B", ja="歌B", year="2010", date="2010-01-05",
                                places=[("其他", "niconico")], kind=vt.OTHER_UNHALL)])

    def test_only_the_year_pages_are_generated(self):
        work = self._big_work()
        with mock.patch.object(vt, "get_output_path", return_value=Path("/tmp/out")):
            specs = vt.page_specs(work)
        self.assertEqual(["Template:初音未来/2009", "Template:初音未来/2010"],
                         [spec["name"] for spec in specs])
        self.assertEqual({"year"}, {spec["kind"] for spec in specs})
        self.assertEqual("歌姬模板_初音未来_2009.wikitext", specs[0]["file"].name)

    def test_the_doc_page_is_left_alone_too(self):
        """既有模板的文档页可能有自己的内容，这一模式不去动它。"""
        work = self._big_work()
        self.assertNotIn("/doc", [spec["name"] for spec in vt.page_specs(work)])

    def test_split_without_the_flag_still_writes_the_main_template(self):
        work = self._big_work()
        work.subpages_only = False
        names = [spec["name"] for spec in vt.page_specs(work)]
        self.assertEqual(["Template:初音未来/2009", "Template:初音未来/2010",
                          "Template:初音未来/doc", "Template:初音未来"], names)

    def test_prepare_work_carries_the_flag(self):
        entries = [vt.HallEntry("歌A", "歌A", vt.RANK_HALL, "niconico", "2009")]
        facts = {"歌A": _fact("歌A", stations=("niconico",), date="2009-06-25")}
        with mock.patch.object(vt.wiki_api, "category_members", return_value=["歌A"]), \
                mock.patch.object(vt, "fetch_halls", return_value=(entries, ["P"])), \
                mock.patch.object(vt, "fetch_song_facts", return_value=facts):
            work = vt.prepare_work("初音未来", True, subpages_only=True)
        self.assertTrue(work.subpages_only)
        self.assertTrue(work.split)
        self.assertIn("1 首曲子", work.summary)
        self.assertIn("自己挂到主模板上", vt.build_doc(work))

    def test_the_copy_keeps_the_mode(self):
        copied = self._big_work().copy()
        self.assertTrue(copied.subpages_only)
        self.assertTrue(copied.split)


if __name__ == "__main__":
    unittest.main()
