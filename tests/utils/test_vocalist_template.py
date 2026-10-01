"""`utils/vocalist_template.py` 的单测：殿堂页解析、分栏、模板拼接、样式/相关人物继承。

全部 HTTP 都 mock 掉（这一层要求能在终端里跑），真实网络那套由 `.tmp_vt*.py` 那类探针核对。
"""
import io
import json
import tempfile
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


CARD_ONLY_PAGE = """{{标题替换|{{lj|深海}}}}
{{VOCALOID_Songbox
|image = 深海 ネイル.jpg
|演唱 = [[歌爱雪]]
|歌曲名称 = {{lj|深海}}
|P主 = {{lj|[[たると]]}}
|投稿 = {{VOCALOID Songbox/card|nnd|sm27471201|2015年10月29日}}
}}
"""

# 多版本（`{{tabs}}`）条目：一个版本一个 Songbox，`|演唱 =` 各不同（照 `深海(たると)` 摭）
MULTI_VERSION_PAGE = """{{标题替换|{{lj|深海}}}}
{{tabs
|bt1 = 原版
|tab1 = {{VOCALOID_Songbox
|演唱 = [[初音未来]]
|歌曲名称 = {{lj|深海}}
|P主 = {{lj|[[たると]]}}
|投稿 = {{VOCALOID Songbox/card|nnd|sm13549415|2011年2月10日}}
}}
|bt2 = 歌爱雪
|tab2 = {{VOCALOID_Songbox
|演唱 = [[歌爱雪]]
|歌曲名称 = {{lj|深海}}
|P主 = {{lj|[[たると]]}}
|投稿 = {{VOCALOID Songbox/card|nnd|sm27471201|2015年10月29日}}
}}
}}
"""


def _fact(title, ja="", stations=(), ranks=None, date="", singers=("歌爱雪",), exists=True):
    return vt.SongFact(title=title, exists=exists, is_song=exists, ja=ja or title,
                       stations=tuple(stations), ranks=dict(ranks or {}), date=date,
                       singers=tuple(singers))


# 拆分前的 `Template:歌爱雪`（revid 251675）那种写法：`{{#invoke:Nav|box|subgroup` 套三层，
# 曲目**一条一行**（续行以 `-->` 开头）。照抄几段实写的：翻唱记 `*`、红链、以及站上按
# 「无法收录」处理的裸日文名（`{{lj|パラオナボーイ}}*`）。
OLD_STYLE_TEMPLATE = """{{#invoke:Nav|box
|name = 歌爱雪
|title = {{coloredlink|#333333|歌爱雪}}
|titlestyle = background:#f38286;color:#333333
|groupstyle = background:#f38286
|list1 = {{#invoke:Nav|box|subgroup
         |title = 相关人物
         |group1 = AH-Software
         |list1 = [[冰山清辉]] • <!--
                  -->[[miki]]
         }}
|list2 = {{#invoke:Nav|box|subgroup
         |title = 歌曲
         |group1 = 殿堂曲
         |list1 = {{#invoke:Nav|box|subgroup
                  |group1 = niconico
                  |list1 = {{#invoke:Nav|box|subgroup
                           |group1 = 2014年
                           |list1 = [[凤仙花|{{lj|鳳仙花}}]]* • <!--
                                    -->[[夜晚的梦|{{lj|よるのゆめ}}]]
                           |group2 = 2023年
                           |list2 = {{lj|パラオナボーイ}}* • <!-- Paraona Boy，无法收录。-->
                           }}
                  }}
         |group2 = 其他
         |list2 = {{#invoke:Nav|box|subgroup
                  |group1 = {{mousetext|部分YouTube投稿|指YouTube投稿}}
                  |list1 = [[Five Nights At Freddy's Song]]* • <!--
                           -->[[此岸花]]
                  }}
         }}
}}<includeonly>{{#if: {{{ nocate | }}} | | {{ac|歌爱雪歌曲}} }}</includeonly>"""


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

    def test_temple_song_with_underscore(self):
        """YouTube 那批页写的是 `{{Temple_Song}}`（**下划线版**）：实测
        `VOCALOID传说曲/YouTube投稿/2023年投稿` 90 个里 89 个是下划线 ——
        不认就会把 YouTube 的传说 / 神话曲整页漏掉。
        """
        page = ("{{Temple_Song\n|yt_id = a\n|投稿时间 = 2023-01-03\n|条目 = [[宇宙船]]\n}}\n"
                "{{Temple_Song\n|神话 = 1\n|yt_id = b\n|條目 = [[Tell Your World]]\n}}\n")
        entries = vt.parse_hall_page("VOCALOID传说曲/YouTube投稿/2023年投稿", page)
        self.assertEqual([("宇宙船", "传说曲", "YouTube"),
                          ("Tell Your World", "神话曲", "YouTube")],
                         [(entry.title, entry.rank, entry.station) for entry in entries])

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
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}), \
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
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}), \
                mock.patch.object(vt.wiki_api, "fetch_pages_text", side_effect=always_fail), \
                mock.patch.object(vt.time, "sleep"):
            entries, pages = vt.fetch_halls("VOCALOID")
        self.assertEqual([], pages)
        self.assertEqual([], entries)

    def test_redirect_pages_take_their_station_from_the_target_title(self):
        """`VOCALOID破亿曲` 是**重定向** → `YouTube上播放数量超过1亿的VOCALOID歌曲`：

        站点信息只在目标标题里（原标题里没有 `/YouTube投稿` 那套后缀）——
        不认就会把 YouTube 的破亿曲全算成 niconico，破亿栏也就归错了站点。
        """
        with mock.patch.object(vt, "hall_page_titles", return_value=["VOCALOID破亿曲"]), \
                mock.patch.object(vt.wiki_api, "redirect_targets",
                                  return_value={"VOCALOID破亿曲":
                                                "YouTube上播放数量超过1亿的VOCALOID歌曲"}), \
                mock.patch.object(vt.wiki_api, "fetch_pages_text",
                                  return_value={"VOCALOID破亿曲": HALL_NICO_2023}), \
                mock.patch.object(vt.time, "sleep"):
            entries, _pages = vt.fetch_halls("VOCALOID")
        self.assertTrue(entries)
        self.assertEqual({"YouTube"}, {entry.station for entry in entries})
        self.assertEqual({vt.RANK_BILLION}, {entry.rank for entry in entries})

    def test_billion_title_parsing(self):
        self.assertEqual({"rank": "破亿曲", "station": "YouTube", "year": ""},
                         vt.parse_hall_title(
                             "VOCALOID破亿曲",
                             "YouTube上播放数量超过1亿的VOCALOID歌曲"))
        self.assertEqual("niconico", vt.parse_hall_title("VOCALOID殿堂曲")["station"])


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

    def test_card_station_code_is_nnd(self):
        """投稿卡片里的站点码是 `nnd`（实测站上 23 张卡里 15 张是它）——

        不认它的话整首歌就成了「既没有投稿 ID 也没有荣誉题头」（用户 2026-09-30 报的
        `深海(たると)`：站点与栏都定不下来，还被丢进待复核）。
        """
        fact = vt.song_fact("深海(たると)", CARD_ONLY_PAGE)
        self.assertEqual(("niconico",), fact.stations)
        self.assertEqual("2015-10-29", fact.date)
        self.assertEqual("2015", fact.year)

    def test_multi_version_page_picks_the_version_by_singer(self):
        """多版本（`{{tabs}}`）条目按 `|演唱 =` 挑这位歌姬那一版（用户 2026-09-30）：

        实测 `深海(たると)`：原版是初音未来 2011 年、歌爱雪唱的那版 2015 年 ——
        默认取第一个会把年份算成 2011。
        """
        fact = vt.song_fact("深海(たると)", MULTI_VERSION_PAGE, vocalist="歌爱雪")
        self.assertEqual("2015-10-29", fact.date)
        self.assertEqual("2015", fact.year)
        self.assertEqual(("niconico",), fact.stations)
        self.assertEqual(("初音未来", "歌爱雪"), fact.singers)      # 所有版本取并集
        self.assertEqual("2011", vt.song_fact("深海(たると)", MULTI_VERSION_PAGE,
                                              vocalist="初音未来").year)
        # 没给歌姬名（或没命中）就用第一个 Songbox
        self.assertEqual("2011", vt.song_fact("深海(たると)", MULTI_VERSION_PAGE).year)
        self.assertEqual("2011", vt.song_fact("深海(たると)", MULTI_VERSION_PAGE,
                                              vocalist="重音Teto").year)

    def test_the_japanese_name_also_counts(self):
        """`|演唱 =` 只写日文名的也得认（实测歌爱雪 142 个多版本条目里有 34 个是
        `{{lj|[[歌愛ユキ]]}}`，不靠别名表就得靠「第一个恰好是对的」碰运气）。
        """
        text = MULTI_VERSION_PAGE.replace("|演唱 = [[歌爱雪]]", "|演唱 = {{lj|[[歌愛ユキ]]}}")
        fact = vt.song_fact("深海(たると)", text, vocalist="歌爱雪")
        self.assertEqual("2015-10-29", fact.date)
        self.assertEqual("2015", fact.year)
        self.assertTrue(vt.belongs_to(text, "歌爱雪"))
        self.assertFalse(vt.belongs_to(text, "重音Teto"))

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
        # 用户 2026-09-30：**以歌曲页面本身数据为准** —— 条目写 niconico=2 档、YouTube=4 档，
        # 殿堂页只说 niconico 是殿堂 → 按条目算，不再丢待复核
        self.assertEqual([("破亿播放曲目", "YouTube"), ("传说曲", "niconico")], song.places)
        self.assertFalse(song.flag)
        self.assertEqual(0, len(work.flags))
        self.assertIn("按条目", song.note)

    def test_honor_header_defines_the_rank_when_the_hall_pages_miss_it(self):
        """殿堂页里没查到的档也由条目定出来 —— 站上没有「破亿曲」页面，破亿只能看题头。

        实测 `Template:重音Teto/2024` 的「破亿播放曲目」栏就是这么来的。
        """
        work = _work()
        facts = {"某曲": _fact("某曲", stations=("niconico", "YouTube"),
                              ranks={"niconico": 2, "YouTube": 4})}
        vt.classify(work, [], facts, ["某曲"])
        song = work.songs[0]
        self.assertEqual([("破亿播放曲目", "YouTube"), ("传说曲", "niconico")], song.places)
        self.assertEqual("荣誉题头", song.source)
        self.assertEqual("", song.kind)                     # 有荣誉栏就不算「其他」的子栏
        self.assertIn("只有条目里写着", song.note)

    def test_classify_uses_the_version_the_singer_sang(self):
        work = _work()
        fact = vt.song_fact("深海(たると)", MULTI_VERSION_PAGE, vocalist="歌爱雪")
        vt.classify(work, [], {"深海(たると)": fact}, ["深海(たると)"])
        song = work.songs[0]
        self.assertEqual("2015", song.year)
        self.assertEqual([("其他", "niconico")], song.places)
        self.assertEqual("", song.flag)

    def test_youtube_hall_songs_are_not_listed(self):
        """**YouTube 的殿堂曲不进模板**（用户 2026-09-30）：站上「殿堂曲」栏只有
        niconico 与 bilibili（`VOCALOID殿堂曲/YouTube投稿` 自己写着不罗列此列表）。
        """
        work = _work()
        entries = [vt.HallEntry("某曲", "某曲", vt.RANK_HALL, "YouTube", "2019"),
                   vt.HallEntry("某曲", "某曲", vt.RANK_HALL, "niconico", "2019")]
        facts = {"某曲": _fact("某曲", stations=("niconico", "YouTube"))}
        vt.classify(work, entries, facts, ["某曲"])
        song = work.songs[0]
        self.assertEqual([("殿堂曲", "niconico")], song.places)
        self.assertIn("YouTube 上的殿堂曲不在模板里列", song.note)

    def test_youtube_only_hall_songs_fall_back_to_other(self):
        work = _work()
        entries = [vt.HallEntry("只发YT", "只发YT", vt.RANK_HALL, "YouTube", "2019")]
        facts = {"只发YT": _fact("只发YT", stations=("YouTube",))}
        vt.classify(work, entries, facts, ["只发YT"])
        song = work.songs[0]
        self.assertEqual([("其他", "YouTube")], song.places)
        self.assertEqual(vt.OTHER_YOUTUBE, song.kind)
        self.assertIn("归入「其他」", song.note)

    def test_matched_by_japanese_name(self):
        work = _work()
        entries = [vt.HallEntry("强风大背头", "強風オールバック", vt.RANK_LEGEND, "niconico", "2023")]
        facts = {"強風オールバック": _fact("強風オールバック", ja="強風オールバック",
                                       stations=("niconico",), date="2023-03-15")}
        vt.classify(work, entries, facts, ["強風オールバック"])
        self.assertEqual("传说曲", work.songs[0].rank)

    def test_a_short_japanese_name_does_not_match_another_song(self):
        """日文名兜底只认「殿堂页本来就拿日文名当标题」的那种（用户 2026-09-30）。

        实测 `歌爱雪` 的 `N(take)`（日文名只有一个字母 `N`）会被按日文名错配到
        `N(水母P)`（另一个人写的另一首歌，日文名恰好也是 `N`）的殿堂记录上 ——
        而站上 `Template:歌爱雪/2012` 是把 `N(take)` 放在「其他」栏的。
        """
        work = _work()
        entries = [vt.HallEntry("N(水母P)", "N", vt.RANK_HALL, "niconico", "2017")]
        facts = {"N(take)": _fact("N(take)", ja="N", stations=("niconico",))}
        vt.classify(work, entries, facts, ["N(take)"])
        self.assertEqual([("其他", "niconico")], work.songs[0].places)

    def test_a_bare_japanese_entry_still_matches(self):
        """殿堂页里写成裸日文名的（`{{lj|パラオナボーイ}}`，标题=日文名）照旧能兜底。"""
        work = _work()
        entries = [vt.HallEntry("パラオナボーイ", "パラオナボーイ", vt.RANK_HALL, "niconico", "2009")]
        facts = {"Paraona Boy": _fact("Paraona Boy", ja="パラオナボーイ",
                                     stations=("niconico",))}
        vt.classify(work, entries, facts, ["Paraona Boy"])
        self.assertEqual([("殿堂曲", "niconico")], work.songs[0].places)

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


# ============================================================ 翻唱记号 / 红链与「无法收录」

class CoverAndRedLinkTest(unittest.TestCase):
    """两件站上一定会写、我们以前会丢掉的东西（用户 2026-10-01）。

    * 「翻唱曲目添加「*」号」：殿堂页写 `(翻)`，模板里写 `*`；
    * 红链与「无法收录」的曲子（`{{假链|条目名|理由}}` / `{{lj|パラオナボーイ}}*`）——
      它们不在 `Category:<歌姬>歌曲` 里，光看分类重建就会整批消失。
    """

    def test_cover_mark_is_read_from_the_hall_page(self):
        page = ("{{Temple Song\n|nnd_id = sm22863738\n|投稿时间 = 2014-02-11\n"
                "|曲目 = [[凤仙花|{{lj|鳳仙花}}]](翻)\n}}\n"
                "{{Temple Song\n|nnd_id = sm1\n|曲目 = [[夜晚的梦]]\n}}\n")
        entries = vt.parse_hall_page("VOCALOID殿堂曲/2014年投稿", page)
        self.assertEqual([True, False], [entry.cover for entry in entries])

    def test_fake_link_entry_keeps_the_japanese_name(self):
        """殿堂页写 `{{假链|Paraona Boy|…}} (翻)` 时：条目名是 `Paraona Boy`、
        日文名在脚注里，而且是「翻唱」+「不给链接」。"""
        page = ("{{Temple Song\n|nnd_id = sm41628309\n|投稿时间 = 2023-01-09 16:23\n"
                "|曲目 = {{假链|Paraona Boy|由于歌词为AI自动生成，不符合收录条件，无法收录。}}"
                " (翻)<ref>《[[パラオナボーイ]]》原稿（sm41628309）已被作者删除。</ref>\n}}\n")
        entry = vt.parse_hall_page("VOCALOID殿堂曲/2023年投稿", page)[0]
        self.assertEqual(("Paraona Boy", "パラオナボーイ"), (entry.title, entry.ja))
        self.assertTrue(entry.cover)
        self.assertTrue(entry.unlinked)

    def test_cover_song_gets_a_star_in_the_list(self):
        """翻唱曲目在曲目后面写 `*`（用户 2026-10-01）—— 记号本身从既有模板的 `*` 来。"""
        work = _work(split=True)
        work.songs = [vt.VocalistSong(title="凤仙花", ja="鳳仙花", year="2014", cover=True,
                                      places=[(vt.RANK_HALL, vt.STATION_NICO)])]
        self.assertIn("{{lj|[[凤仙花|鳳仙花]]}}*", vt._songs_line(work.songs))
        self.assertIn("{{lj|[[凤仙花|鳳仙花]]}}*", vt.build_year_page(work, "2014"))
        self.assertNotIn("*", vt.build_year_page(work, "2013"))      # 别的年份页不受影响

    def test_unlinked_song_is_written_without_a_link(self):
        """站上按「无法收录」处理的曲子：只写日文名，不给链接（旧模板里就是
        `{{lj|パラオナボーイ}}*`）。"""
        work = _work()
        entries = [vt.HallEntry("Paraona Boy", "パラオナボーイ", vt.RANK_HALL, "niconico",
                                "2023", cover=True, unlinked=True)]
        facts = {"Paraona Boy": _fact("Paraona Boy", ja="パラオナボーイ",
                                     stations=("niconico",), date="2023-01-09")}
        vt.classify(work, entries, facts, ["Paraona Boy"])
        song = work.songs[0]
        self.assertTrue(song.unlinked)
        self.assertEqual("{{lj|パラオナボーイ}}", song.link)
        self.assertIn("{{lj|パラオナボーイ}}*", vt._songs_line([song]))

    def test_continuation_lines_are_read(self):
        """曲目一条一行，续行以 `-->` 开头 —— 不接回上一条 `|listN` 就会只读到第一首。"""
        entries = vt.template_song_entries(OLD_STYLE_TEMPLATE)
        self.assertEqual(["凤仙花", "夜晚的梦", "パラオナボーイ", "Five Nights At Freddy's Song",
                          "此岸花"], [entry["title"] or entry["ja"] for entry in entries])

    def test_related_people_are_not_taken_as_songs(self):
        entries = vt.template_song_entries(OLD_STYLE_TEMPLATE)
        names = [entry["title"] or entry["ja"] for entry in entries]
        self.assertNotIn("冰山清辉", names)
        self.assertNotIn("miki", names)

    def test_existing_template_positions_are_kept(self):
        entries = {entry["title"] or entry["ja"]: entry
                   for entry in vt.template_song_entries(OLD_STYLE_TEMPLATE)}
        self.assertEqual(("殿堂曲", "niconico", "2014", True),
                         (entries["凤仙花"]["rank"], entries["凤仙花"]["station"],
                          entries["凤仙花"]["year"], entries["凤仙花"]["cover"]))
        self.assertEqual(("殿堂曲", "niconico", "2023", True, True),
                         (entries["パラオナボーイ"]["rank"], entries["パラオナボーイ"]["station"],
                          entries["パラオナボーイ"]["year"], entries["パラオナボーイ"]["cover"],
                          entries["パラオナボーイ"]["unlinked"]))
        # 「其他」栏是平铺的（没有年份小格）：拿不到年份，站点按子栏算
        self.assertEqual(("其他", "YouTube", ""),
                         (entries["Five Nights At Freddy's Song"]["rank"],
                          entries["Five Nights At Freddy's Song"]["station"],
                          entries["Five Nights At Freddy's Song"]["year"]))

    def test_existing_songs_are_carried_over_on_load(self):
        work = _work(split=True)
        work.songs = [vt.VocalistSong(title="夜晚的梦", ja="よるのゆめ", year="2014")]
        pages = {"Template:歌爱雪": OLD_STYLE_TEMPLATE}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        by_title = {song.title or song.ja: song for song in work.songs}
        self.assertTrue(by_title["凤仙花"].cover)                 # 翻唱记号搬过来了
        self.assertEqual([("殿堂曲", "niconico")], by_title["凤仙花"].places)
        self.assertEqual("2014", by_title["凤仙花"].year)
        # 红链与「无法收录」的曲子也搬过来了，而且位置照旧
        self.assertEqual("{{lj|パラオナボーイ}}", by_title["パラオナボーイ"].link)
        self.assertEqual("鳳仙花", by_title["凤仙花"].ja)
        # 没有年份的（旧模板「其他」栏是平铺的）要挂待复核
        self.assertIn("取不到投稿年", by_title["Five Nights At Freddy's Song"].flag)
        # 已经有的歌不会被重复搬一遍
        self.assertEqual(1, len([song for song in work.songs if song.title == "夜晚的梦"]))
        self.assertIn("从既有模板补了", work.summary)

    def test_split_template_falls_back_to_an_earlier_revision(self):
        """主模板已拆成「年份转接行」、里面没有曲目名单时，往前翻最近一版带名单的当「原模板」。

        实测 `Template:歌爱雪` 就是这个情形：拆分后主模板只剩转接行，年份子页里是我们按
        分类生成的名单 —— 手写版里的红链与翻唱得从历史版本里捞回来。
        """
        split_template = ("{{Navbox\n|name = 歌爱雪\n"
                          "|title = {{coloredlink|#333333|歌爱雪}}\n"
                          "|list1 = {{歌爱雪/2014|nocate=1|state=uncollapsed|child|noabove}}\n}}\n")
        work = _work(split=True)
        work.songs = [vt.VocalistSong(title="夜晚的梦", ja="よるのゆめ", year="2014")]
        pages = {"Template:歌爱雪": split_template}
        history = [{"revid": 252330, "content": split_template, "timestamp": "t2",
                    "user": "辅助工具"},
                   {"revid": 251675, "content": OLD_STYLE_TEMPLATE, "timestamp": "t1",
                    "user": "人间百态"}]
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "recent_revision_texts",
                                  return_value=history), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        by_title = {song.title or song.ja: song for song in work.songs}
        self.assertIn("凤仙花", by_title)
        self.assertTrue(by_title["凤仙花"].cover)
        self.assertIn("251675", by_title["凤仙花"].note)      # 说明是从哪一版捞回来的
        self.assertIn("パラオナボーイ", by_title)

    def test_existing_redirect_names_are_matched(self):
        """既有模板里的重定向名（`再见_97` → `再见 97`）不能当成新歌补一遍。"""
        template = ("{{#invoke:Nav|box\n|list1 = {{#invoke:Nav|box|subgroup\n"
                    "|title = 歌曲\n|group1 = 殿堂曲\n|list1 = [[再见_97|Sayonara_97]]\n}}\n}}")
        work = _work()
        work.songs = [vt.VocalistSong(title="再见 97", ja="Sayonara_97", year="2012")]
        pages = {"Template:歌爱雪": template}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets",
                                  return_value={"再见_97": "再见 97"}) as redirects:
            vt.load_existing(work)
        self.assertEqual(["再见 97"], [song.title for song in work.songs])
        redirects.assert_called_once()


# ============================================================ 红链 → VocaDB

class VocadbFillTest(unittest.TestCase):
    """用户 2026-10-01：红链先在 VocaDB 搜原始名补数据（查不到才放复核）；殿堂页里的红链合唱曲
    用 VocaDB 的歌姬名单确认收不收。"""

    def _found(self, **kwargs):
        base = {"name": "某曲", "date": "2018-12-19", "year": "2018", "favorited": 23,
                "rating": 183, "singers": (), "pvs": (("NicoNicoDouga", "sm1"),), "url": ""}
        return {**base, **kwargs}

    def test_red_link_song_gets_its_year_from_vocadb(self):
        work = _work(split=True)
        song = vt.VocalistSong(title="", ja="浮遊月光街", page_exists=False,
                               places=[(vt.RANK_HALL, vt.STATION_NICO)])
        song.flag = ("既有模板里列着这首歌，但分类里没有 —— 按原样搬过来；"
                     "取不到投稿年（拆分成年份子页时要你指定）")
        work.songs = [song]
        work.flags = [{"title": "", "ja": "浮遊月光街", "reason": song.flag,
                       "rank": vt.RANK_HALL, "station": "niconico", "year": ""}]
        with mock.patch.object(vt, "vocadb_song",
                               return_value=self._found(name="浮遊月光街")), \
                mock.patch.object(vt, "play_counts", create=True,
                                  return_value=["nico 1,081,622"]):
            self.assertEqual(0, vt.fill_from_vocadb(work))
        self.assertEqual("2018", song.year)
        self.assertEqual("2018-12-19", song.date)
        self.assertIn("VocaDB", song.note)
        self.assertIn("播放量 nico 1,081,622", song.note)
        self.assertNotIn("取不到投稿年", song.flag)
        self.assertEqual("2018", work.flags[0]["year"])

    def test_play_counts_read_nico_and_bilibili(self):
        payload = mock.Mock()
        payload.raise_for_status = mock.Mock()
        payload.json.return_value = {"code": 0, "data": {"stat": {"view": 987654}}}
        with mock.patch.object(vt.nicolog, "fetch") as fetch, \
                mock.patch("utils.source_filler.bilibili_view",
                           return_value={"stat": {"view": 12345}}), \
                mock.patch("models.video.get_yt_info") as yt, \
                mock.patch.object(vt, "http_get", return_value=payload) as http_get:
            fetch.return_value = mock.Mock(views=1081622)
            yt.return_value = mock.Mock(views=7654321)
            counts = vt.play_counts((("Youtube", "Jhw7Hum-eLw"),
                                     ("Youtube", "kD951HMCT7s"),
                                     ("NicoNicoDouga", "sm34347007"),
                                     ("Bilibili", "BV1Yo4y1Y75k"),
                                     ("Bilibili", "38760155"),
                                     ("NicoNicoDouga", "")))
        self.assertEqual(["YouTube 7,654,321", "nico 1,081,622", "bilibili 12,345"], counts)
        self.assertEqual(1, yt.call_count)                 # 同一站点只查一个 PV
        http_get.assert_not_called()                       # 这条 BV 已经代表 b 站了

    def test_play_counts_accepts_a_numeric_bilibili_aid(self):
        """VocaDB 的 Bilibili PV 有时给的是数字 aid（实测 浮遊月光街 = 38760155）。"""
        payload = mock.Mock()
        payload.raise_for_status = mock.Mock()
        payload.json.return_value = {"code": 0, "data": {"stat": {"view": 224089}}}
        with mock.patch.object(vt, "http_get", return_value=payload) as http_get:
            self.assertEqual(["bilibili 224,089"],
                             vt.play_counts((("Bilibili", "38760155"),)))
        self.assertIn("aid=38760155", http_get.call_args[0][0])

    def test_play_counts_swallow_failures(self):
        with mock.patch.object(vt.nicolog, "fetch", side_effect=RuntimeError("boom")):
            self.assertEqual([], vt.play_counts((("NicoNicoDouga", "sm1"),)))
        self.assertEqual([], vt.play_counts(("Youtube",)))          # 畸形条目不报错

    def test_red_link_choral_song_is_added_when_vocadb_lists_the_singer(self):
        work = _work()
        entries = [vt.HallEntry("白色幸福", "白色幸福", vt.RANK_LEGEND, "bilibili", "2022",
                                singer="初音未来、歌爱雪、VY1、POYOROID")]
        with mock.patch.object(vt.wiki_api, "pages_exist",
                               return_value={"白色幸福": False}), \
                mock.patch.object(vt, "vocadb_song", return_value=self._found(
                    name="白色幸福", date="2022-03-01", year="2022",
                    singers=("初音ミク", "歌愛ユキ"))) as lookup:
            self.assertEqual(1, vt.fill_from_vocadb(work, entries))
        lookup.assert_called_once()
        song = work.songs[0]
        self.assertEqual(("白色幸福", "2022", "传说曲"), (song.title, song.year, song.rank))
        self.assertIn("VocaDB 歌姬名单里有 歌爱雪", song.note)
        self.assertEqual(1, len(work.flags))

    def test_red_link_choral_song_without_our_singer_is_skipped(self):
        work = _work()
        entries = [vt.HallEntry("别人合唱", "别人合唱", vt.RANK_HALL, "bilibili", "2022",
                                singer="初音未来、歌爱雪")]
        with mock.patch.object(vt.wiki_api, "pages_exist",
                               return_value={"别人合唱": False}), \
                mock.patch.object(vt, "vocadb_song", return_value=self._found(
                    name="别人合唱", singers=("初音ミク",))):
            self.assertEqual(0, vt.fill_from_vocadb(work, entries))
        self.assertEqual([], work.songs)

    def test_existing_pages_are_not_looked_up(self):
        """条目已经建好的不走合唱那条路（分类那条路管它们）。"""
        work = _work()
        entries = [vt.HallEntry("已建条目", "已建条目", vt.RANK_HALL, "bilibili", "2022",
                                singer="歌爱雪")]
        with mock.patch.object(vt.wiki_api, "pages_exist",
                               return_value={"已建条目": True}), \
                mock.patch.object(vt, "vocadb_song") as lookup:
            self.assertEqual(0, vt.fill_from_vocadb(work, entries))
        self.assertEqual([], work.songs)
        lookup.assert_not_called()

    def test_lookup_failure_is_swallowed(self):
        work = _work()
        song = vt.VocalistSong(title="查不到的歌", year="", page_exists=False)
        work.songs = [song]
        with mock.patch.object(vt, "vocadb_song", return_value=None):
            self.assertEqual(0, vt.fill_from_vocadb(work))
        self.assertEqual("", song.year)


class AlbumOnlyTest(unittest.TestCase):
    """只收在专辑里的曲子不进模板（用户 2026-10-01）。

    实测 `八十八键的宇宙`：信息框写着 `|收录专辑 = [[SEASIDE SOLILOQUIES]]`，却一个投稿 ID
    都没有 —— 这种「专辑曲」没有自己的投稿页，模板里不该收（与 P主模板的专辑处理一致）。
    """

    ALBUM_PAGE = ("{{VOCALOID Songbox\n|演唱 = [[歌爱雪]]\n"
                  "|歌曲名称 = {{lj|八十八鍵の宇宙}}\n|P主 = [[Orangestar]]\n"
                  "|收录专辑 = [[SEASIDE SOLILOQUIES]]\n}}\n")
    NORMAL_PAGE = ("{{VOCALOID Songbox\n|演唱 = [[歌爱雪]]\n"
                   "|歌曲名称 = {{lj|強風オールバック}}\n|P主 = {{lj|ゆこぴ}}\n"
                   "|收录专辑 = [[专辑名]]\n|nnd_id = sm41926207\n}}\n")

    def test_album_only_page_is_detected(self):
        self.assertTrue(vt.song_fact("八十八键的宇宙", self.ALBUM_PAGE,
                                     vocalist="歌爱雪").album_only)
        # 有投稿 ID 的（专辑只是顺带写着）不算专辑曲
        self.assertFalse(vt.song_fact("强风大背头", self.NORMAL_PAGE,
                                      vocalist="歌爱雪").album_only)

    def test_album_only_song_is_left_out_of_the_template(self):
        work = _work()
        fact = vt.song_fact("八十八键的宇宙", self.ALBUM_PAGE, vocalist="歌爱雪")
        vt.classify(work, [], {"八十八键的宇宙": fact}, ["八十八键的宇宙"])
        self.assertEqual([], work.songs)
        self.assertEqual([], work.flags)

    def test_infobox_song_album_page_is_left_out(self):
        """`Captain little` 那种用的信息框是 `{{Infobox Song}}`（不是 Songbox），同样是专辑曲。"""
        page = ("{{Infobox Song\n|歌曲名={{lj|キャプテンリトル}}<br/>Captain little\n"
                "|演唱=[[IA]]\n|作词={{lj|[[じん]]}}\n"
                "|收录专辑=《'''[[IA THE WORLD ～光～]]'''》\n}}\n")
        fact = vt.song_fact("Captain little", page, vocalist="IA")
        self.assertTrue(fact.is_song)                  # 认得出这是歌曲页
        self.assertTrue(fact.album_only)
        work = vt.VocalistWork(name="IA", engine="VOCALOID")
        vt.classify(work, [], {"Captain little": fact}, ["Captain little"])
        self.assertEqual([], work.songs)

    # 实测 `超次元爱歌`（用户 2026-10-01 报的）：信息框里**没有**「专辑」参数，专辑写在
    # **正文**里（「收录于专辑《未完成エイトビーツ》中」），而原来那个 niconico 投稿
    # （`二次元の女の子に恋をしてしまって辛い…w`）已被作者删除 —— 页面里一个投稿 ID 都没有，
    # 只有专辑版的 `{{music163}}`，同样不该进模板。
    PROSE_ALBUM_PAGE = (
        "{{VOCALOID Songbox\n|image = 未完成エイトビーツ.jpeg\n"
        "|颜色 = #b7d07a; color:#FFF\n|演唱 = [[IA]]\n"
        "|歌曲名称 = {{lj|超次元愛歌}}<br>'''超次元爱歌'''\n|P主 = [[Orangestar]]\n}}\n"
        "《'''{{lj|超次元愛歌}}'''》是[[Orangestar]]创作的[[VOCALOID]]日语原创歌曲，"
        "由[[IA]]演唱，收录于专辑'''{{lj|[[未完成エイトビーツ]]}}'''中。\n"
        "Orangestar曾将本曲以'''二次元の女の子に恋をしてしまって辛い…w '''的歌名投稿至"
        "niconico，后{{黑幕|因其为黑历史而}}删除。\n\n== 歌曲 ==\n'''专辑版'''\n"
        "{{music163|id=31830618}}\n[[Category:IA歌曲]]\n")

    def test_prose_album_page_is_detected(self):
        fact = vt.song_fact("超次元爱歌", self.PROSE_ALBUM_PAGE, vocalist="IA")
        self.assertTrue(fact.is_song)
        self.assertTrue(fact.album_only)

    def test_prose_album_song_is_left_out_of_the_template(self):
        work = _work(name="IA")
        fact = vt.song_fact("超次元爱歌", self.PROSE_ALBUM_PAGE, vocalist="IA")
        vt.classify(work, [], {"超次元爱歌": fact}, ["超次元爱歌"])
        self.assertEqual([], work.songs)
        self.assertEqual([], work.flags)

    def test_hall_song_is_kept_even_when_the_page_mentions_an_album(self):
        """上了殿堂页的歌照样收 —— 条目的信息框漏写投稿 ID 时，年份还能从殿堂页拿到。"""
        entries = [vt.HallEntry(title="超次元爱歌", ja="超次元愛歌", rank=vt.RANK_HALL,
                               station=vt.STATION_NICO, year="2015",
                               page="VOCALOID殿堂曲/2015年投稿")]
        work = _work(name="IA")
        fact = vt.song_fact("超次元爱歌", self.PROSE_ALBUM_PAGE, vocalist="IA")
        vt.classify(work, entries, {"超次元爱歌": fact}, ["超次元爱歌"])
        self.assertEqual(["超次元爱歌"], [song.title for song in work.songs])


class AlbumCreditSingerTest(unittest.TestCase):
    """「演唱」里把她**只写在专辑 / 精选碟**上 → 不算她的曲子（用户 2026-10-01）。

    实测 `黎明与萤火`（`夜明けと蛍`）：条目 `|演唱 = {{lj|[[初音ミク]]}}（投稿）、[[IA]]（IA精选碟）`
    —— IA 只是收了专辑版，没给这首歌投稿；站上 `Template:IA/2014` rev 255012 把它删了
    （我们的工具当时还留着）。反过来，`|演唱 = [[初音ミク]]（投稿）、[[IA]]` 这种正常写法照收。
    """

    PAGE = ("{{VOCALOID Songbox\n|演唱 = {{lj|[[初音ミク]]}}（投稿）、[[IA]]（IA精选碟）\n"
            "|歌曲名称 = {{lj|夜明けと蛍}}\n|P主 = {{lj|[[ナブナ]]}}\n"
            "|nnd_id = sm24626484\n|nnd_date = 2014-11-27\n}}\n")

    def test_album_only_credit_is_recognized(self):
        fact = vt.song_fact("黎明与萤火", self.PAGE, vocalist="IA")
        self.assertEqual(("IA",), fact.album_singers)
        self.assertIn("IA", fact.singers)                  # 「她」还是在演唱那一栏里的

    def test_the_same_writing_without_the_album_note_is_normal(self):
        page = self.PAGE.replace("[[IA]]（IA精选碟）", "[[IA]]")
        fact = vt.song_fact("黎明与萤火", page, vocalist="IA")
        self.assertEqual((), fact.album_singers)

    def test_song_is_dropped_for_that_singer(self):
        fact = vt.song_fact("黎明与萤火", self.PAGE, vocalist="IA")
        entries = [vt.HallEntry(title="黎明与萤火", ja="夜明けと蛍", rank=vt.RANK_LEGEND,
                                station=vt.STATION_NICO, year="2014",
                                page="VOCALOID传说曲/2014年投稿")]
        work = _work(name="IA", split=True)
        vt.classify(work, entries, {"黎明与萤火": fact}, ["黎明与萤火"])
        song = work.songs[0]
        self.assertTrue(song.album_credit)
        self.assertFalse(song.own_version)
        self.assertEqual(["黎明与萤火"], vt.prune_cover_mismatch(work))
        self.assertEqual([], work.songs)
        self.assertEqual(["黎明与萤火"], vt.dropped_titles(work))

    def test_the_other_singer_still_gets_it(self):
        """投稿的那位（初音ミク）照样收 —— 专辑说明只影响她一个。"""
        fact = vt.song_fact("黎明与萤火", self.PAGE, vocalist="初音ミク")
        self.assertEqual(("IA",), fact.album_singers)      # 页面上的事实不变
        work = _work(name="初音ミク", split=True)
        vt.classify(work, [], {"黎明与萤火": fact}, ["黎明与萤火"])
        self.assertEqual(["黎明与萤火"], [song.title for song in work.songs])
        self.assertFalse(work.songs[0].album_credit)


class StationYearTest(unittest.TestCase):
    """一个站一年：各站点只上它**自己投稿那一年**的年份子页（用户 2026-10-01）。

    实测 `六兆年零一夜的故事`：niconico 2012-04-11、YouTube 2013-01-07 —— 站上
    `Template:IA/2012` 只在**神话曲/niconico** 里列它、`Template:IA/2013` 只在
    **神话曲/YouTube** 里列它（用户拿这两页的两个修订指出来的）。以前是「这首歌跨到的
    年份各来一份、每个站点都写一遍」，于是同一首在两个年份页里都挂在两个站下。
    """

    PAGE = ("{{虚拟歌手歌曲荣誉题头|VOCALOID|nrank=3|yrank=3}}\n"
            "{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
            "|歌曲名称 = {{lj|{{lang|ja|六兆年と一夜物語}}}}\n|P主 = {{lj|kemu}}\n"
            "|nnd_id = sm17520464\n|nnd_date = 2012-04-11\n"
            "|yt_id = 8ZIeJR5o5uQ\n|yt_date = 2013-01-07\n}}\n")

    def _work(self):
        fact = vt.song_fact("六兆年零一夜的故事", self.PAGE, vocalist="IA")
        entries = [
            vt.HallEntry(title="六兆年零一夜的故事", ja="六兆年と一夜物語", rank=vt.RANK_MYTH,
                         station=vt.STATION_NICO, year="2012", page="VOCALOID传说曲/2012年投稿"),
            vt.HallEntry(title="六兆年零一夜的故事", ja="六兆年と一夜物語", rank=vt.RANK_MYTH,
                         station=vt.STATION_YOUTUBE, year="2013",
                         page="VOCALOID传说曲/YouTube投稿/2013年投稿")]
        work = _work(name="IA", split=True)
        vt.classify(work, entries, {"六兆年零一夜的故事": fact}, ["六兆年零一夜的故事"])
        return work

    def test_station_dates_are_read_from_the_infobox(self):
        fact = vt.song_fact("六兆年零一夜的故事", self.PAGE, vocalist="IA")
        self.assertEqual({"niconico": "2012", "YouTube": "2013"}, fact.station_years)
        self.assertEqual(("2012", "2013"), fact.years)

    def test_each_place_lands_on_its_own_year(self):
        song = self._work().songs[0]
        self.assertEqual(["2012", "2013"], song.all_years)
        self.assertEqual([(vt.RANK_MYTH, vt.STATION_NICO)], song.places_in("2012"))
        self.assertEqual([(vt.RANK_MYTH, vt.STATION_YOUTUBE)], song.places_in("2013"))
        self.assertEqual("2012", song.year_of(vt.RANK_MYTH, vt.STATION_NICO))
        self.assertEqual("2013", song.year_of(vt.RANK_MYTH, vt.STATION_YOUTUBE))

    def test_the_two_year_pages_differ(self):
        work = self._work()
        page2012, page2013 = vt.build_year_page(work, "2012"), vt.build_year_page(work, "2013")
        for page in (page2012, page2013):
            self.assertIn("六兆年と一夜物語", page)
            self.assertIn("神话曲", page)
        self.assertIn("|group1 = niconico", page2012)
        self.assertNotIn("YouTube", page2012)          # 2013 年的 YouTube 不该出现在 2012 页
        self.assertIn("|group1 = YouTube", page2013)
        self.assertNotIn("niconico", page2013)

    def test_the_writeback_writes_both_year_calls(self):
        """条目里写的是两条调用（站上 `舞蹈着言语的行星` 就是 `{{IA/2019}}` + `{{IA/2021}}`）。"""
        work = self._work()
        self.assertEqual(["IA/2012", "IA/2013"],
                         vt.template_calls_for(work, "六兆年零一夜的故事"))

    def test_unknown_station_year_stays_on_the_earliest_page(self):
        """殿堂页跟信息框都说不出这一站是哪年时 → 只挂在最早那一年，不跨年重复。"""
        song = vt.VocalistSong(title="某曲", ja="某曲", year="2012",
                               places=[(vt.RANK_OTHER, vt.STATION_NICO),
                                       (vt.RANK_OTHER, vt.STATION_BILIBILI)])
        song.place_years[(vt.RANK_OTHER, vt.STATION_NICO)] = "2012"
        song.place_years[(vt.RANK_OTHER, vt.STATION_BILIBILI)] = "2020"
        song.years = ["2012", "2020"]
        self.assertEqual([(vt.RANK_OTHER, vt.STATION_NICO)], song.places_in("2012"))
        self.assertEqual([(vt.RANK_OTHER, vt.STATION_BILIBILI)], song.places_in("2020"))


class CoverYearTest(unittest.TestCase):
    """翻唱曲：**条目「演唱」栏里认不出这位歌姬就整首不收**（用户 2026-10-01）。

    实测（`Template:IA/2012` 的 255000 那笔编辑）：
    * `视力检查` 的条目写的是 `|演唱 = [[Megpoid|GUMI]]`，殿堂页里那两条 `(翻)` 是别人的
      翻唱版 → 站上把它删了；
    * `magnet` 的条目写的是 `|演唱 = [[初音未来]]、[[巡音流歌]]`（原曲）→ 同样删了；
    * 反面例子 `Ave Maria` / `Historia:opening theme`：`|演唱 = [[IA]]` → 留着；
    * `夜咄ディセイブ` 这种条目本来就是这位歌姬唱的，殿堂页里那些 `(翻)` 是**别人翻的**，
      不影响它自己的位置。
    """

    ORIGINAL = vt.HallEntry(title="magnet", ja="magnet", rank=vt.RANK_LEGEND,
                            station=vt.STATION_NICO, year="2009", page="VOCALOID传说曲/2009年投稿")
    COVER = vt.HallEntry(title="magnet", ja="magnet", rank=vt.RANK_HALL,
                         station=vt.STATION_NICO, year="2012", cover=True,
                         page="VOCALOID殿堂曲/2012年投稿")

    # `magnet` / `视力检查` 的条目写的是**原曲**（初音未来 × 巡音流歌 / GUMI）
    OTHER_PAGE = ("{{VOCALOID Songbox\n|演唱 = [[初音未来]]、[[巡音流歌]]\n"
                  "|歌曲名称 = [[magnet]]\n|P主 = {{lj|流星P}}\n"
                  "|nnd_id = sm6909505\n|nnd_date = 2009-05-01\n}}\n")
    IA_PAGE = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
               "|歌曲名称 = [[夜咄ディセイブ]]\n|P主 = {{lj|Jin}}\n"
               "|nnd_id = sm20116702\n|nnd_date = 2013/2/17\n}}\n")

    def _classify(self, entries, page=None, title="magnet", prune=True):
        fact = vt.song_fact(title, page or self.OTHER_PAGE, vocalist="IA")
        work = _work(name="IA", split=True)
        vt.classify(work, entries, {title: fact}, [title])
        if prune:                                  # 剔除在 `load_existing()` 末尾跑（认得出混音版）
            vt.prune_cover_mismatch(work)
        return work.songs

    def test_cover_without_the_singer_is_skipped(self):
        """歌唱栏里是别人（原曲 + 别人的翻唱记录）→ 整首不收。"""
        self.assertEqual([], self._classify([self.ORIGINAL, self.COVER]))

    def test_remix_cover_is_kept(self):
        """混音版（名字里带 Remix / リミックス）在站上是**另一首独立的歌**，照样收。"""
        work = _work(name="IA", split=True)
        song = vt.VocalistSong(title="透明哀歌", ja="透明エレジー -Morimoto hiroCt Remix-",
                              year="2014", cover=True, own_version=False,
                              places=[(vt.RANK_HALL, vt.STATION_NICO)],
                              place_years={(vt.RANK_HALL, vt.STATION_NICO): "2014"})
        work.songs = [song]
        self.assertEqual([], vt.prune_cover_mismatch(work))
        self.assertEqual(["透明哀歌"], [item.title for item in work.songs])

    def test_song_without_any_cover_record_is_kept(self):
        """没有 `(翻)` 记录（就是她自己那首，只是条目漏写投稿 ID）→ 照收。"""
        songs = self._classify([self.ORIGINAL])
        self.assertEqual(1, len(songs))
        self.assertFalse(songs[0].cover)
        self.assertEqual([(vt.RANK_LEGEND, vt.STATION_NICO)], songs[0].places)
        self.assertEqual(["2009"], songs[0].all_years)

    def test_skipped_covers_are_not_imported_back_from_the_existing_template(self):
        """既有页面里还写着它时也不能按「以模板为准」搬回来。"""
        work = _work(name="IA", split=True)
        work.skipped_covers = ["magnet"]
        page = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = niconico\n|list1 = [[magnet]]*{{W}}<!--\n"
                "          -->[[Ave Maria]]*\n  }}\n}}\n")
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2012": page}, ["2012"])
        titles = [song.title for song in work.songs]
        self.assertNotIn("magnet", titles)
        self.assertIn("Ave Maria", titles)                     # 别的照旧搬

    def test_own_version_ignores_other_peoples_covers(self):
        """条目本来就是这位歌姬唱的（`夜咄ディセイブ`）→ 殿堂页里那些 `(翻)` 是别人翻的，不算。"""
        entry = vt.HallEntry(title="夜谈欺骗", ja="夜咄ディセイブ", rank=vt.RANK_LEGEND,
                             station=vt.STATION_NICO, year="2013",
                             page="VOCALOID传说曲/2013年投稿")
        other = vt.HallEntry(title="夜谈欺骗", ja="夜咄ディセイブ", rank=vt.RANK_HALL,
                             station=vt.STATION_NICO, year="2015", cover=True,
                             page="VOCALOID殿堂曲/2015年投稿")
        song = self._classify([entry, other], page=self.IA_PAGE, title="夜谈欺骗")[0]
        self.assertFalse(song.cover)
        self.assertEqual([(vt.RANK_LEGEND, vt.STATION_NICO)], song.places)
        self.assertEqual(["2013"], song.all_years)

    def test_cover_with_the_singer_is_kept(self):
        """`Ave Maria` 那种：条目 `|演唱 = [[IA]]` + 殿堂页里带 `(翻)` → 留着，按条目的年份。"""
        page = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n|歌曲名称 = [[Ave Maria]]\n"
                "|P主 = {{lj|kz}}\n|nnd_id = sm17083937\n|nnd_date = 2012-02-10\n}}\n")
        entry = vt.HallEntry(title="Ave Maria", ja="Ave Maria", rank=vt.RANK_HALL,
                             station=vt.STATION_NICO, year="2012", cover=True,
                             page="VOCALOID殿堂曲/2012年投稿")
        song = self._classify([entry], page=page, title="Ave Maria")[0]
        self.assertEqual([(vt.RANK_HALL, vt.STATION_NICO)], song.places)
        self.assertEqual(["2012"], song.all_years)


class CoverExistingPlaceTest(unittest.TestCase):
    """既有年份子页里校对过的翻唱位置优先（`_merge_existing_songs()`）。"""

    def test_existing_year_page_placement_wins_for_covers(self):
        work = _work(name="IA", split=True)
        song = vt.VocalistSong(title="magnet", ja="magnet", year="2009",
                               places=[(vt.RANK_LEGEND, vt.STATION_NICO)],
                               place_years={(vt.RANK_LEGEND, vt.STATION_NICO): "2009"},
                               years=["2009"], source="殿堂页")
        work.songs = [song]
        page = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = niconico\n|list1 = [[magnet]]*\n  }}\n}}\n")
        texts = {"Template:IA/2012": page}
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, texts, ["2012"])
        self.assertTrue(song.cover)
        self.assertEqual([(vt.RANK_HALL, vt.STATION_NICO)], song.places)
        self.assertEqual(["2012"], song.all_years)
        self.assertEqual("2012", song.year_of(vt.RANK_HALL, vt.STATION_NICO))

    def test_non_cover_songs_keep_our_placement(self):
        work = _work(name="IA", split=True)
        song = vt.VocalistSong(title="某曲", ja="某曲", year="2013",
                               places=[(vt.RANK_MYTH, vt.STATION_YOUTUBE)],
                               place_years={(vt.RANK_MYTH, vt.STATION_YOUTUBE): "2013"},
                               years=["2013"])
        work.songs = [song]
        page = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = niconico\n|list1 = [[某曲]]\n  }}\n}}\n")
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2012": page}, ["2012"])
        self.assertFalse(song.cover)                            # 不是翻唱就不动位置
        self.assertEqual([(vt.RANK_MYTH, vt.STATION_YOUTUBE)], song.places)


class DuplicateEntryTest(unittest.TestCase):
    """同一首曲子在同一个格子里只列一次（用户 2026-10-01 手工删过 `Template:IA/2012` 里重复的
    `视力检查` / `Historia:opening theme`）。

    来源：既有页面里同一首歌可能在**两张年份子页**上各写了一遍 —— 采下来的位置不去重，
    页面上就会列两遍。
    """

    PAGE = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
            "    |group1 = niconico\n|list1 = {{lj|[[视力检查|シリョクケンサ]]}}*\n  }}\n}}\n")

    def test_places_are_deduped_by_place(self):
        work = _work(name="IA", split=True)
        song = vt.VocalistSong(title="视力检查", ja="シリョクケンサ", year="2012",
                               places=[(vt.RANK_HALL, vt.STATION_NICO)],
                               place_years={(vt.RANK_HALL, vt.STATION_NICO): "2012"},
                               years=["2012"], cover=True)
        work.songs = [song]
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2011": self.PAGE,
                                            "Template:IA/2012": self.PAGE}, ["2011", "2012"])
        self.assertEqual([(vt.RANK_HALL, vt.STATION_NICO)], song.places)

    def test_render_never_lists_a_song_twice_in_one_cell(self):
        song = vt.VocalistSong(title="某曲", ja="某曲", year="2012",
                               places=[(vt.RANK_HALL, vt.STATION_NICO)],
                               place_years={(vt.RANK_HALL, vt.STATION_NICO): "2012"})
        page = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = niconico\n|list1 = [[某曲]]\n  }}\n}}\n")
        work = _work(name="IA", split=True)
        work.songs = [song]
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2012": page}, ["2012"])
        work.songs = [song, song]                  # 同一首歌被收了两遍
        text = vt.build_year_page(work, "2012")
        self.assertEqual(1, text.count("[[某曲"))


class SecondaryCreationTest(unittest.TestCase):
    """条目「== 二次创作 ==」段落里点到这位歌姬 → 收，链接挂 `#二次创作`（用户 2026-10-01）。

    实测 `胸部××××`：信息框 `|演唱 = [[初音未来]]`，但「二次创作」那段写着
    「由[[初音ミク]]…，[[IA]]，IA（Rock），[[GUMI]]…演唱」→ 站上 `Template:IA/2023` 里
    写的是 `{{lj|[[胸部××××#二次创作|胸部××××]]}}*`；
    `视力检查` / `magnet` 的那一段里没有 IA → 整首不收。
    """

    PAGE = ("{{VOCALOID Songbox\n|演唱 = [[初音未来]]\n"
            "|歌曲名称 = {{lj|おっぱい××××}}\n|P主 = {{lj|CヰEL}}\n"
            "|nnd_id = sm36746450\n|nnd_date = 2020-07-19\n}}\n"
            "\n== 二次创作 ==\n"
            "由[[初音ミク]]（Sweet），[[IA]]，IA（Rock），[[GUMI]]演唱的版本。\n"
            "{{bv|BV1v54y1w7qt}}\n\n== 注释 ==\n{{reflist}}\n")
    PLAIN_PAGE = ("{{VOCALOID Songbox\n|演唱 = [[Megpoid|GUMI]]\n"
                  "|歌曲名称 = {{lj|シリョクケンサ}}\n|nnd_id = sm15230821\n"
                  "|nnd_date = 2011-08-06\n}}\n"
                  "\n== 二次创作 ==\n'''DECO*27 ver.'''\n"
                  "{{VOCALOID Small Songbox\n|演唱 = [[Megpoid|GUMI]]\n}}\n")

    ENTRY = vt.HallEntry(title="胸部××××", ja="胸部××××", rank=vt.RANK_HALL,
                         station=vt.STATION_BILIBILI, year="2023", cover=True,
                         page="VOCALOID殿堂曲/bilibili投稿/2023年投稿")

    def test_secondary_section_names_the_singer(self):
        fact = vt.song_fact("胸部××××", self.PAGE, vocalist="IA")
        self.assertTrue(fact.secondary)
        self.assertFalse(vt.song_fact("视力检查", self.PLAIN_PAGE, vocalist="IA").secondary)

    def test_song_is_kept_with_the_anchor(self):
        fact = vt.song_fact("胸部××××", self.PAGE, vocalist="IA")
        work = _work(name="IA", split=True)
        vt.classify(work, [self.ENTRY], {"胸部××××": fact}, ["胸部××××"])
        self.assertEqual([], vt.prune_cover_mismatch(work))       # 不收的会被剔掉 → 空表
        song = work.songs[0]
        self.assertEqual("二次创作", song.anchor)
        self.assertTrue(song.cover)
        # 锚点挂在链接上（显示名照站上惯例用日文名；站上那一格写的是 `[[胸部××××#二次创作|胸部××××]]`）
        self.assertTrue(song.link.startswith("{{lj|[[胸部××××#二次创作|"), song.link)
        self.assertTrue(song.link.endswith("]]}}"), song.link)

    def test_section_without_the_singer_is_still_dropped(self):
        fact = vt.song_fact("视力检查", self.PLAIN_PAGE, vocalist="IA")
        work = _work(name="IA", split=True)
        entry = vt.HallEntry(title="视力检查", ja="シリョクケンサ", rank=vt.RANK_LEGEND,
                             station=vt.STATION_NICO, year="2012", cover=True,
                             page="VOCALOID传说曲/2012年投稿")
        vt.classify(work, [entry], {"视力检查": fact}, ["视力检查"])
        self.assertEqual(["视力检查"], vt.prune_cover_mismatch(work))
        self.assertEqual([], work.songs)

    def test_anchor_from_the_existing_page_is_kept(self):
        """既有页面里已经是 `[[胸部××××#二次创作|胸部××××]]` → 再生成时别写成 `#二次创作#二次创作`。"""
        page = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = bilibili\n"
                "    |list1 = {{lj|[[胸部××××#二次创作|胸部××××]]}}*\n  }}\n}}\n")
        entries = vt.template_song_entries(page, default_year="2023")
        self.assertEqual(("胸部××××", "二次创作"), (entries[0]["title"], entries[0]["anchor"]))
        work = _work(name="IA", split=True)
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2023": page}, ["2023"])
        self.assertEqual("{{lj|[[胸部××××#二次创作|胸部××××]]}}*", work.songs[0].link + "*")


class RemixEntryTitleTest(unittest.TestCase):
    """混音版曲目的条目名写 `<原曲>/<remixer>`（用户 2026-10-01 的 `Template:IA/2014` 修订）。

    实测：`{{lj|[[Antibeat|アンチビート／DIVELA REMIX]]}}*` →
    `{{lj|[[Antibeat/DIVELA|アンチビート／DIVELA REMIX]]}}*`（254876），
    `ロストワンの号哭／DIVELA REMIX` → `Lost one的号哭/DIVELA`。
    """

    TEMPLATE = ("{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n"
                "    |group1 = niconico\n"
                "    |list1 = {{lj|[[Antibeat|アンチビート／DIVELA REMIX]]}}*{{W}}<!--\n"
                "          -->{{lj|[[Lost one的号哭|ロストワンの号哭／DIVELA REMIX]]}}*{{W}}<!--\n"
                "          -->{{lj|[[透明哀歌|透明エレジー -Morimoto hiroCt Remix-]]}}*\n"
                "  }}\n}}\n")

    def _songs(self):
        work = _work(name="IA", split=True)
        with mock.patch("utils.wiki_api.redirect_targets", return_value={}):
            vt._merge_existing_songs(work, {"Template:IA/2014": self.TEMPLATE}, ["2014"])
        return work.songs

    def test_remix_gets_the_subpage_title(self):
        titles = [song.title for song in self._songs()]
        self.assertIn("Antibeat/DIVELA", titles)
        self.assertIn("Lost one的号哭/DIVELA", titles)
        self.assertIn("透明哀歌", titles)          # 连字符写法（`-X Remix-`）站上没改，不动

    def test_display_name_and_marks_are_kept(self):
        song = next(item for item in self._songs() if item.title == "Antibeat/DIVELA")
        self.assertEqual("アンチビート／DIVELA REMIX", song.ja)   # 显示还是原样
        self.assertTrue(song.cover)                               # `*` 保留
        self.assertEqual("2014", song.year)
        self.assertEqual("{{lj|[[Antibeat/DIVELA|アンチビート／DIVELA REMIX]]}}*", song.link + "*")

    def test_plain_titles_are_untouched(self):
        self.assertEqual("Antibeat", vt._remix_entry_title("Antibeat", "アンチビート"))
        self.assertEqual("某曲", vt._remix_entry_title("某曲", "某曲"))


class CategoryRereadTest(unittest.TestCase):
    """分类成员比上次少就读重一次（用户 2026-10-01）。

    实测 `Category:IA歌曲` 偶尔只回 512 条（应为 514），`magnet` 就这样漏过一次。
    """

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        vt._category_cache = {}
        self.addCleanup(setattr, vt, "_category_cache", None)
        self.path = mock.patch.object(vt, "get_output_path",
                                      return_value=self.folder.name)
        self.path.start()
        self.addCleanup(self.path.stop)

    def _read(self, *results):
        with mock.patch.object(vt.wiki_api, "category_members",
                               side_effect=list(results)) as fetch:
            titles = vt.fetch_category_titles("Category:IA歌曲", 2500)
        return titles, fetch

    def test_shorter_list_is_read_again(self):
        """上次那趟里还有 `magnet`，这次没读到 → 重读一次把它找回来。"""
        vt._category_cache = {"Category:IA歌曲": ["A", "B", "magnet"]}
        titles, fetch = self._read(["A", "B"], ["A", "B", "magnet"])
        self.assertEqual(["A", "B", "magnet"], titles)
        self.assertEqual(2, fetch.call_count)

    def test_full_list_is_not_read_twice(self):
        vt._category_cache = {"Category:IA歌曲": ["A", "B"]}
        titles, fetch = self._read(["A", "B"])
        self.assertEqual(["A", "B"], titles)
        self.assertEqual(1, fetch.call_count)

    def test_真被移出分类时保留新结果(self):
        vt._category_cache = {"Category:IA歌曲": ["A", "B", "C"]}
        titles, fetch = self._read(["A", "B"], ["A", "B"])
        self.assertEqual(["A", "B"], titles)          # 重读后还是少 → 旧的就不要了
        self.assertEqual(2, fetch.call_count)

    def test_名单写进缓存文件(self):
        self._read(["A", "B"])
        with io.open(Path(self.folder.name) / vt.CATEGORY_CACHE_NAME, encoding="utf-8") as fh:
            saved = json.loads(fh.read())
        self.assertEqual(["A", "B"], saved["Category:IA歌曲"])


class SongNameCleaningTest2(unittest.TestCase):
    """信息框里 `|歌曲名称 =` 的别名串要剥干净（用户 2026-10-01 的 `Template:IA/2013` 修订）。"""

    def test_slash_alias_chain_is_cut(self):
        """`'''夜咄ディセイブ'''/夜咄Deceive/夜谈欺骗` → `夜咄ディセイブ`。"""
        page = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
                "|歌曲名称 = '''夜咄ディセイブ'''/夜咄Deceive/夜谈欺骗<br />"
                "'''09:{{lj|目を欺く話}}(欺骗双目的故事)'''\n"
                "|nnd_id = sm20116702\n|nnd_date = 2013/2/17\n}}\n")
        self.assertEqual("夜咄ディセイブ", vt.song_fact("夜谈欺骗", page, vocalist="IA").ja)

    def test_ascii_alias_chain_is_cut_too(self):
        """`マトリョシカ/Matryoshka` → `マトリョシカ`（`前线` 那一类的中英混排也一样）。"""
        page = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
                "|歌曲名称 = {{lj|マトリョシカ/Matryoshka}}\n|nnd_id = sm11784443\n"
                "|nnd_date = 2010-08-19\n}}\n")
        self.assertEqual("マトリョシカ", vt.song_fact("俄罗斯套娃", page, vocalist="IA").ja)

    def test_bracket_alias_before_the_slash_cut(self):
        """`{{lj|如月アテンション}}(如月Attention/如月专注)`：括号里的斜杠不能先切。"""
        page = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
                "|歌曲名称 = '''{{lj|如月アテンション}}'''(如月Attention/如月专注)\n"
                "|nnd_id = sm17814127\n|nnd_date = 2012-05-27\n}}\n")
        self.assertEqual("如月アテンション", vt.song_fact("如月专注", page, vocalist="IA").ja)

    def test_lang_ja_wrapper_is_unwrapped(self):
        """`{{lj|{{lang|ja|六兆年と一夜物語}}}}` → `六兆年と一夜物語`。"""
        page = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
                "|歌曲名称 = {{lj|{{lang|ja|六兆年と一夜物語}}}}\n|nnd_id = sm17520464\n"
                "|nnd_date = 2012-04-11\n}}\n")
        self.assertEqual("六兆年と一夜物語", vt.song_fact("六兆年零一夜的故事", page,
                                                        vocalist="IA").ja)


class LinksTemplateTest(unittest.TestCase):
    """站上把一长串曲目打包的 `{{Links|条目{{!}}日文|条目2}}` 要摊开。

    实测 `Template:IA`：不摊开的话整串被当成一首曲子 —— 认不出名字、又跟分类里同一首歌
    对不上号，于是 `六兆年と一夜物語` 会被当成新歌补一遍并丢进人工复核（用户 2026-10-01）。
    """

    TEMPLATE = ("{{Navbox\n|list1 = {{Navbox subgroup\n|title = 歌曲\n|group1 = 神话曲\n"
                "|list1 = {{lj|{{Links|六兆年零一夜的故事{{!}}六兆年と一夜物語|"
                "明日的夜空哨戒班{{!}}アスノヨゾラ哨戒班}}}}\n}}\n}}")

    def test_links_template_is_expanded(self):
        entries = vt.template_song_entries(self.TEMPLATE)
        self.assertEqual([("六兆年零一夜的故事", "六兆年と一夜物語"),
                          ("明日的夜空哨戒班", "アスノヨゾラ哨戒班")],
                         [(entry["title"], entry["ja"]) for entry in entries])

    def test_expanded_songs_match_the_category(self):
        work = _work(name="IA")
        work.songs = [vt.VocalistSong(title="六兆年零一夜的故事", ja="六兆年と一夜物語",
                                      year="2012")]
        pages = {"Template:IA": self.TEMPLATE}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        titles = sorted(song.title for song in work.songs)
        self.assertEqual(["六兆年零一夜的故事", "明日的夜空哨戒班"], titles)
        self.assertEqual(1, len([song for song in work.songs
                                 if song.title == "六兆年零一夜的故事"]))
        self.assertFalse([flag for flag in work.flags if "六兆年" in str(flag.get("title"))])

    def test_html_header_row_is_not_taken_as_a_song(self):
        """模板里手写的 HTML 表头不是曲目（用户 2026-10-01 在人工复核里看到的那条空白条目）。

        实测 `Template:IA`：`<tr><th class="mw-customtoggle-1 navbox-title" …>其他歌曲
        <sub>(点击展开)</sub></th></tr><!-- 此处按条目首字添加未殿堂的已创建条目的歌曲`
        夹在曲目行中间，旧代码把它当一首无名曲子 → 复核弹窗里就是一条空白标题 +「取不到投稿年」。
        """
        template = ("{{Navbox\n|list1 = {{Navbox subgroup\n|title = 歌曲\n|group1 = 殿堂曲\n"
                    "|list1 = [[某曲]]{{W}}<!--\n"
                    " <tr><th class=\"mw-customtoggle-1 navbox-title\" style=\"cursor:pointer;\">"
                    "其他歌曲<sub>(点击展开)</sub></th></tr><!-- 此处按条目首字添加"
                    "未殿堂的已创建条目的歌曲\n"
                    "-->[[另一曲]]\n}}\n}}")
        entries = vt.template_song_entries(template)
        self.assertEqual(["某曲", "另一曲"],
                         [entry["title"] or entry["ja"] for entry in entries])

    def test_template_only_song_without_a_year_is_still_flagged(self):
        work = _work(name="IA")
        pages = {"Template:IA": self.TEMPLATE}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        self.assertTrue(all("取不到投稿年" in song.flag for song in work.songs))


class ExtraGroupsTest(unittest.TestCase):
    """拆分子页后，主模板里「演唱会 / 官方专辑」这两栏要保留（用户 2026-10-01）。"""

    TEMPLATE = ("{{#invoke:Nav|box\n|title = IA\n"
                "|list1 = {{#invoke:Nav|box|subgroup\n"
                "         |title = 相关人物\n         |list1 = [[じん]]\n         }}\n"
                "|list2 = {{#invoke:Nav|box|subgroup\n"
                "         |title = 演唱会\n         |list1 = [[IA 1st LIVE]]\n         }}\n"
                "|list3 = {{#invoke:Nav|box|subgroup\n"
                "         |title = 官方专辑\n         |list1 = [[IA THE WORLD]]\n         }}\n"
                "|list4 = {{#invoke:Nav|box|subgroup\n"
                "         |title = 歌曲\n         |list1 = [[某曲]]\n         }}\n}}")

    def test_extra_groups_are_inherited_and_rendered(self):
        work = vt.VocalistWork(name="IA", engine="VOCALOID", split=True)
        work.songs = [vt.VocalistSong(title="某曲", year="2012",
                                      places=[(vt.RANK_HALL, vt.STATION_NICO)])]
        pages = {"Template:IA": self.TEMPLATE}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        self.assertEqual(["演唱会", "官方专辑"], [title for title, _block in work.extra_groups])
        text = vt.build_main_template(work)
        self.assertIn("|title = 演唱会", text)
        self.assertIn("|title = 官方专辑", text)
        self.assertIn("{{IA/2012|nocate=1", text)


# ============================================================ 上标（`<sup>CeVIO</sup>`）

class SuperScriptTest(unittest.TestCase):
    """曲目后面的上标要保留（用户 2026-10-01：拆 `Template:IA` 时那些 CeVIO 版的上标）。

    站上写法：`[[脑内disco|ノウナイディスコ]]<sup>CeVIO</sup>`、
    `[[鸟之诗|鳥之詩]]<sup>CeVIO</sup>*`（上标在 `*` **前面**）。
    """

    TEMPLATE = ("{{Navbox\n|list1 = {{Navbox subgroup\n|title = 歌曲\n|group1 = 殿堂曲\n"
                "|list1 = [[脑内disco|ノウナイディスコ]]<sup>CeVIO</sup> • <!--\n"
                "         -->[[鸟之诗|鳥之詩]]<sup>CeVIO</sup>* • <!--\n"
                "         -->[[普通歌|普通歌]]\n}}\n}}")

    def test_superscript_is_parsed_from_the_existing_template(self):
        entries = {entry["title"]: entry for entry in vt.template_song_entries(self.TEMPLATE)}
        self.assertEqual("CeVIO", entries["脑内disco"]["engine"])
        self.assertEqual("", entries["普通歌"]["engine"])
        self.assertTrue(entries["鸟之诗"]["cover"])          # 上标 + `*` 同时有

    def test_superscript_is_carried_over_and_rendered_before_the_star(self):
        work = _work(name="IA")
        work.songs = [vt.VocalistSong(title="鸟之诗", ja="鳥之詩", year="2023",
                                      places=[(vt.RANK_HALL, vt.STATION_NICO)])]
        pages = {"Template:IA": self.TEMPLATE}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}), \
                mock.patch.object(vt.wiki_api, "redirect_targets", return_value={}):
            vt.load_existing(work)
        song = work.songs[0]
        self.assertEqual("CeVIO", song.super_engine)
        self.assertTrue(song.cover)
        self.assertIn("{{lj|[[鸟之诗|鳥之詩]]}}<sup>CeVIO</sup>*", vt._songs_line([song]))
        self.assertIn("<sup>CeVIO</sup>*", vt.build_year_page(work, "2023"))

    def test_superscript_comes_from_the_honor_header_engine(self):
        fact = vt.song_fact("某曲", "{{虚拟歌手歌曲荣誉题头|CeVIO|nrank=1}}\n", vocalist="IA")
        self.assertEqual(("CeVIO",), fact.engines)
        main = vt.VocalistWork(name="IA", engine="VOCALOID")
        vt.classify(main, [], {"某曲": fact}, ["某曲"])
        self.assertEqual("CeVIO", main.songs[0].super_engine)
        same = vt.VocalistWork(name="IA", engine="CeVIO")
        vt.classify(same, [], {"某曲": fact}, ["某曲"])
        self.assertEqual("", same.songs[0].super_engine)     # 与主引擎一样就不用标

    def test_multi_version_page_does_not_add_a_superscript(self):
        """多版本条目的荣誉题头说的往往是另一个版本。

        实测 `相思相爱`：题头写 `UTAU|yrank=1`，可页里同时有初音未来 ver 与 IA ver ——
        用户 2026-10-01 指出它是 VOCALOID 曲目，不该标 UTAU。
        """
        page = ("{{虚拟歌手歌曲荣誉题头|UTAU|yrank=1}}\n{{tabs\n"
                "|bt1=初音未来 ver\n|tab1={{VOCALOID_Songbox|演唱=[[初音未来]]|"
                "歌曲名称={{lj|相思相愛}}|bb_id=BV1}}\n"
                "|bt2=IA ver\n|tab2={{VOCALOID_Songbox|演唱=[[IA]]|"
                "歌曲名称={{lj|相思相愛}}|yt_id=abc}}\n}}\n")
        fact = vt.song_fact("相思相爱", page, vocalist="IA")
        self.assertTrue(fact.multi_version)
        work = vt.VocalistWork(name="IA", engine="VOCALOID")
        vt.classify(work, [], {"相思相爱": fact}, ["相思相爱"])
        self.assertEqual("", work.songs[0].super_engine)


class SongNameCleaningTest(unittest.TestCase):
    """条目信息框里的名字要收拾干净（用户 2026-10-01）。"""

    PAGE = ("{{VOCALOID Songbox\n|演唱 = [[IA]]\n"
            "|歌曲名称 = '''{{lj|如月アテンション}}'''(如月Attention/如月专注)"
            "<br />'''07:{{lj|目を奪う話}}(夺去目光的故事)'''\n|nnd_id = sm17930619\n}}\n")

    def test_alias_and_bold_are_stripped_from_the_declared_name(self):
        self.assertEqual("如月アテンション", vt._declared_ja(self.PAGE))
        fact = vt.song_fact("如月专注", self.PAGE, vocalist="IA")
        self.assertEqual("如月アテンション", fact.ja)

    def test_ruby_is_unwrapped(self):
        self.assertEqual("塵塵呪詛", vt.clean_title("{{ruby|塵塵呪詛|チリチリジュソ}}"))
        page = self.PAGE.replace("'''{{lj|如月アテンション}}'''(如月Attention/如月专注)",
                                 "{{ruby|塵塵呪詛|チリチリジュソ}}")
        self.assertEqual("塵塵呪詛", vt._declared_ja(page))


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
        # 「相关人物」那一行**不带** `|groupN = 相关人物`（用户 2026-10-01 在
        # `Template:歌爱雪` 上把这个标签删掉了）：那个块自己就是 subgroup，带 |title = 相关人物。
        self.assertNotIn("|group1 = 相关人物", text)
        self.assertIn("|list1 = {{#invoke:Nav|box|subgroup|title = 相关人物", text)
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
        # `|above` 的 `年份=` 填**最早那一年**（用户 2026-09-30 拿 `Template:歌爱雪/2019`、
        # `/2022` 两个修订指出）：`{{虚拟歌姬年份计算}}` 是从这个年份循环到今年的，
        # 填成本页年份的话，这一页的题头就只会列 2024~今年，前面的年份全漏掉。
        self.assertIn("{{虚拟歌姬年份计算|年份=2008|歌姬名=重音Teto|color=#f2dfe6}}", text)
        self.assertIn("|group1 = 破亿播放曲目", text)          # 站上写法：破亿排最前
        self.assertIn("|group2 = 神话曲", text)
        self.assertIn("|group3 = 殿堂曲", text)
        self.assertIn("|group1 = niconico", text)
        self.assertIn("|group1 = bilibili", text)
        self.assertIn("|group1 = YouTube", text)
        self.assertIn("{{lj|[[催眠者|メズマライザー]]}}", text)
        self.assertIn("<includeonly>{{#if: {{{ nocate | }}} | | {{ac|重音Teto歌曲}} }}"
                      "</includeonly>", text)

    def test_songs_are_separated_by_w_and_a_comment(self):
        """曲目之间用 `{{W}}<!--\\n<缩进>-->`（用户 2026-10-01 要求；站上 `Template:重音Teto/*`）。

        `{{W}}` 渲染出来就是 ` • `（实测 `Template:W`），但**一首一行** ——
        好比对也好手改，那条注释把换行吃掉，页面上不会多出空白。
        """
        self.work.songs.append(vt.VocalistSong(title="第二首", ja="二番目", year="2024",
                                              places=[("破亿播放曲目", "YouTube")]))
        text = vt.build_year_page(self.work, "2024")
        # `|list1 = ` 那行缩进 4 格（`    |list1 = …`），`-->` 再深 6 格
        # （列表按投稿日期排，新加的那首没有日期 → 排在前面）
        self.assertIn("{{lj|[[第二首|二番目]]}}{{W}}<!--\n          -->"
                      "{{lj|[[催眠者|メズマライザー]]}}", text)
        self.assertNotIn(" • ", text)                  # 老的 ` • ` 写法不再出现

    def test_longer_lists_indent_their_continuation_lines(self):
        """嵌套更深的列表，`-->` 跟着 `|listN = ` 的缩进走（站上就是这么排的）。"""
        self.work.songs.append(vt.VocalistSong(title="第二首", ja="二番目", year="2008",
                                              places=[("其他", "niconico")]))
        text = vt.build_year_page(self.work, "2008")
        for line in text.split("\n"):
            if line.endswith("{{W}}<!--"):
                indent = len(line) - len(line.lstrip())
                index = text.split("\n").index(line)
                following = text.split("\n")[index + 1]
                self.assertEqual(indent + 6, len(following) - len(following.lstrip()),
                                 f"续行缩进不对：{line!r} → {following!r}")

    def test_every_year_page_uses_the_earliest_year(self):
        """每张年份子页的 `|above` 写的是**同一个**最早年份（站上 `Template:歌爱雪/*` 都写 2009）。"""
        self.assertEqual("2008", vt.first_year(self.work))
        for year in ("2008", "2024"):
            self.assertIn("年份=2008|", vt.build_year_page(self.work, year))
        self.assertNotIn("年份=2024|", vt.build_year_page(self.work, "2024"))

    def test_year_page_follows_an_existing_one(self):
        """标题里名字与年份之间要不要空格、要不要 `|abovestyle`：跟着这位歌姬**已有的**子页走。

        站上两种写法各占一半（实测 50 张：`}}年` 17 张 / `}} 年` 32 张；
        `|abovestyle` 38 张有 12 张没）—— 歌爱雪那套是不带空格、不写 abovestyle
        （用户 2026-09-30 给的两个修订就是这两页）。
        """
        text = vt.build_year_page(self.work, "2024")           # 没参照时：带空格 + 照配色写
        self.assertIn("|title = {{coloredlink|#f2dfe6|重音Teto}} 2024年歌曲", text)
        self.assertIn("|abovestyle = background:#d93a49;color:#f2dfe6", text)
        self.work.existing_year = ("{{Navbox\n|name = 重音Teto/2024\n"
                                   "|title = {{coloredlink|#f2dfe6|重音Teto}}2024年歌曲\n}}\n")
        text = vt.build_year_page(self.work, "2024")
        self.assertIn("|title = {{coloredlink|#f2dfe6|重音Teto}}2024年歌曲", text)
        self.assertNotIn("|abovestyle", text)                   # 既有子页没写 → 不写

    def test_abovestyle_is_copied_from_the_existing_year_page(self):
        self.work.existing_year = ("{{Navbox\n|title = X 2024年歌曲\n"
                                   "|abovestyle = background:#d93a49;color:#f2dfe6\n}}\n")
        text = vt.build_year_page(self.work, "2024")
        self.assertIn("|abovestyle = background:#d93a49;color:#f2dfe6", text)
        self.assertIn("|title = {{coloredlink|#f2dfe6|重音Teto}} 2024年歌曲", text)

    def test_year_page_carries_the_singer_template_category(self):
        """年份子页的分类是「<歌姬>模板」、不是「虚拟歌手模板」（用户 2026-09-30 报的）：

        实测站上 `Template:歌爱雪/2009` / `Template:重音Teto/2024` 都是子分类。
        """
        text = vt.build_year_page(self.work, "2024")
        self.assertIn("<noinclude>[[Category:重音Teto模板]]</noinclude>", text)
        self.assertNotIn("虚拟歌手模板", text)

    def test_category_page_matches_the_site(self):
        """分类页照站上 `Category:歌爱雪模板` / `Category:重音Teto模板` 那几页写。"""
        self.assertEqual("Category:重音Teto模板", vt.category_page_title(self.work))
        self.assertEqual("[[Category:重音Teto模板]]", vt.year_category(self.work))
        self.assertEqual("{{catnav|内容模板|虚拟歌手模板}}\n[[Category:虚拟歌手模板]]\n",
                         vt.build_category_page(self.work))

    def test_the_doc_page_is_categorised(self):
        """文档页挂「模板文档」（实测 `Template:重音Teto/doc`）。"""
        self.assertIn("<noinclude>[[Category:模板文档]]</noinclude>", vt.build_doc(self.work))

    def test_non_split_template_nests_years_under_stations(self):
        self.work.split = False
        text = vt.build_main_template(self.work)
        self.assertIn("|list1 = {{#invoke:Nav|box|subgroup|title = 相关人物", text)
        self.assertNotIn("|group1 = 相关人物", text)          # 同上：那一行不带标签
        self.assertIn("|group2 = 歌曲", text)                # 歌曲那一行照旧带标签
        self.assertIn("|group1 = 破亿播放曲目", text)          # 歌曲栏里破亿排最前
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
        # 分类页排最前：年份子页挂的就是它，先建出来子页的分类就不是红链
        self.assertEqual(["Category:重音Teto模板", "Template:重音Teto/2008",
                          "Template:重音Teto/2024", "Template:重音Teto/doc",
                          "Template:重音Teto"],
                         [spec["name"] for spec in specs])
        self.assertEqual("歌姬模板_重音Teto_分类页.wikitext", specs[0]["file"].name)
        self.assertEqual("歌姬模板_重音Teto_2024.wikitext", specs[2]["file"].name)
        self.assertEqual("歌姬模板_重音Teto_doc.wikitext", specs[3]["file"].name)
        self.assertEqual("歌姬模板_重音Teto.wikitext", specs[4]["file"].name)

    def test_template_links_reads_both_forms(self):
        text = ("{{lj|[[催眠者|メズマライザー]]}} • [[テトリス]] "
                "{{lj|[[テトリス|テトリス]]}} • [[Template:重音Teto/2024]]")
        self.assertEqual(["催眠者", "テトリス"], vt.template_links(text))

    def test_rank_counts_counts_every_placement(self):
        counts = vt.rank_counts(self.work)
        self.assertEqual({"破亿播放曲目": 1, "神话曲": 1, "殿堂曲": 1, "其他": 1}, counts)


class TemplateCallTest(unittest.TestCase):
    """写回条目的写法（用户 2026-09-30 / 2026-10-01 定的）：

    * 曲子条目 → `{{歌姬/年份}}`（**不带 `|collapsed`**：年份子页默认就是折叠的）；
    * 跨年的歌 → 每一年各一条（`面包屑` 就是 `{{歌爱雪/2023}}` + `{{歌爱雪/2026}}`）；
    * 歌姬条目 → `{{歌姬|nocate=1}}`；
    * 不拆时 → `{{歌姬|collapsed}}`（主模板认 `{{{1}}}`）；
    * 位置 → `position="after_producer"`（P主模板后面、活动模板前面）。
    """

    def test_split_uses_the_year_page(self):
        work = vt.VocalistWork(name="重音Teto", split=True, songs=[
            vt.VocalistSong(title="2代目閻魔", year="2024")])
        self.assertEqual(["重音Teto/2024"], vt.template_calls_for(work, "2代目閻魔"))
        self.assertEqual(["重音Teto|nocate=1"], vt.template_calls_for(work, "重音Teto"))

    def test_a_song_uploaded_in_two_years_gets_two_calls(self):
        """跨年的歌要挂两张年份子页（用户 2026-10-01 拿 `面包屑` 指出的）。

        实测 `面包屑`：`nnd_date = 2023/8/4` + `yt_date = 2023/8/5` + `bb_date = 2026/7/10`
        → 站上条目里写着 `{{歌爱雪/2023}}` 与 `{{歌爱雪/2026}}` 两行。
        """
        work = vt.VocalistWork(name="歌爱雪", split=True, songs=[
            vt.VocalistSong(title="面包屑", year="2023", date="2023-08-04",
                            years=["2023", "2026"])])
        self.assertEqual(["歌爱雪/2023", "歌爱雪/2026"], vt.template_calls_for(work, "面包屑"))
        self.assertEqual(["2023", "2026"], work.songs[0].all_years)

    def test_not_split_uses_the_main_template(self):
        work = vt.VocalistWork(name="歌爱雪", split=False)
        self.assertEqual(["歌爱雪|collapsed"], vt.template_calls_for(work, "不去大海"))

    def test_insert_groups_by_year_page(self):
        work = vt.VocalistWork(name="重音Teto", split=True, songs=[
            vt.VocalistSong(title="A", year="2024"), vt.VocalistSong(title="B", year="2023")])
        with mock.patch.object(vt, "insert_into_pages",
                               return_value=[{"title": "x", "ok": True, "count": 1}]) as insert:
            vt.insert_into_pages_for(work, ["A", "B", "重音Teto"])
        calls = {(call.args[0], tuple(call.kwargs.get("call") or [])): call.args[1]
                 for call in insert.call_args_list}
        self.assertIn(("重音Teto", ("重音Teto/2024",)), calls)
        self.assertIn(("重音Teto", ("重音Teto/2023",)), calls)
        self.assertIn(("重音Teto", ("重音Teto|nocate=1",)), calls)
        self.assertEqual(["A"], calls[("重音Teto", ("重音Teto/2024",))])
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
        drops = {tuple(call.kwargs.get("call") or []): call.kwargs.get("drop_category")
                 for call in insert.call_args_list}
        self.assertEqual({("弗里摩侠|collapsed",): "弗里摩侠歌曲",
                          ("弗里摩侠|nocate=1",): ""}, drops)
        # 歌姬模板拆年份后，页面上的旧写法要跟着改写（rewrite=True）
        self.assertTrue(all(call.kwargs.get("rewrite") for call in insert.call_args_list))

    def test_dropped_songs_lose_the_leftover_call(self):
        """不再收录的曲子（`dropped_titles()`）反过来做：把条目里残留的调用删掉。

        用户 2026-10-01 在 `magnet` 上手工删了 `{{IA/2012}}`（该曲已从歌姬模板里撤下）。
        """
        work = vt.VocalistWork(name="IA", split=True, skipped_covers=["magnet"])
        with mock.patch.object(vt, "insert_into_pages",
                               return_value=[{"title": "x", "ok": True, "count": 1}]) as insert, \
                mock.patch.object(vt, "remove_template_from_pages",
                                  return_value=[{"title": "magnet", "ok": True,
                                                 "count": 1}]) as remove:
            vt.insert_into_pages_for(work, ["magnet", "2代目閻魔"])
        self.assertEqual(["magnet"], remove.call_args.args[1])
        inserted = [title for call in insert.call_args_list for title in call.args[1]]
        self.assertEqual(["2代目閻魔"], inserted)              # 不再收录的不进插入那一拨

    def test_album_only_songs_are_also_cleaned_up(self):
        """只收在专辑里的曲子同样记进不再收录名单（条目里残留的调用一并清）。"""
        page = ("{{Infobox Song\n|演唱=[[IA]]\n|收录专辑=《'''[[IA THE WORLD ～光～]]'''》\n}}\n")
        fact = vt.song_fact("Captain little", page, vocalist="IA")
        work = vt.VocalistWork(name="IA", split=True)
        vt.classify(work, [], {"Captain little": fact}, ["Captain little"])
        self.assertEqual(["Captain little"], vt.dropped_titles(work))


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

    def test_load_existing_also_reads_a_year_page(self):
        """年份子页的写法（标题空格 / `|abovestyle`）也要读一张回来当参照。"""
        work = _work(split=True)
        work.songs = [vt.VocalistSong(title="歌A", year="2010")]
        pages = {"Template:歌爱雪": EXISTING_TEMPLATE, "Template:歌爱雪/doc": "文档正文",
                 "Template:歌爱雪/2010": "{{Navbox\n|name = 歌爱雪/2010\n"
                                        "|title = {{coloredlink|#333333|歌爱雪}}2010年歌曲\n}}\n"}
        with mock.patch.object(vt.wiki_api, "fetch_pages_text",
                               side_effect=lambda titles: {title: pages[title]
                                                           for title in titles
                                                           if title in pages}):
            vt.load_existing(work)
        self.assertIn("2010年歌曲", work.existing_year)
        self.assertEqual("", vt._year_title_joiner(work))       # 参照的那页没空格
        self.assertEqual("", vt._year_above_style(work))

    def test_footnotes_are_stripped_from_names(self):
        """名字里挂的脚注要剥掉（用户 2026-09-30 拿 `Template:歌爱雪/2019`、`/2022` 指出）。"""
        self.assertEqual("Sayonara_97", vt.clean_title(
            "{{lj|Sayonara_97}}<ref> [[tokumei|NMKK]]在专辑《'''{{lj|やわらかなひめい}}'''》中"
            "对此曲标注“BPM应该是97”。</ref>"))
        self.assertEqual("ᅠᅠᅠᅠᅠᅠᅠ", vt.clean_title("ᅠᅠᅠᅠᅠᅠᅠ{{refn|name=title|niconico标题名称}}"))
        self.assertEqual("ふかみ", vt.clean_title("{{lj|[[深海|ふかみ]]}}"))   # 普通名字不动


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
        # 分类页也算在建的名单里（年份子页挂着它）
        self.assertEqual(["Category:初音未来模板", "Template:初音未来/2009",
                          "Template:初音未来/2010"],
                         [spec["name"] for spec in specs])
        self.assertEqual({"year", "category"}, {spec["kind"] for spec in specs})
        self.assertEqual("歌姬模板_初音未来_2009.wikitext", specs[1]["file"].name)

    def test_the_doc_page_is_left_alone_too(self):
        """既有模板的文档页可能有自己的内容，这一模式不去动它。"""
        work = self._big_work()
        self.assertNotIn("/doc", [spec["name"] for spec in vt.page_specs(work)])

    def test_split_without_the_flag_still_writes_the_main_template(self):
        work = self._big_work()
        work.subpages_only = False
        names = [spec["name"] for spec in vt.page_specs(work)]
        self.assertEqual(["Category:初音未来模板", "Template:初音未来/2009",
                          "Template:初音未来/2010", "Template:初音未来/doc",
                          "Template:初音未来"], names)

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
