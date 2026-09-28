"""utils/family_template.py 的单元测试。

覆盖三块：
1. 导航框默认展开 / 折叠的判断与 `|collapsed` 追加（原 utils/navbox.py，已合并进本模块）；
2. 把条目插入歌手大家族模板的荣誉小节（殿堂曲 / 传说曲 / 神话曲…）；
3. 未达殿堂时插入「部分非殿堂曲」，以及插入 The VOCALOID Collection 的榜单段落。

模板片段取自 voca.wiki 的 Template:可不/2024、Template:歌爱雪、Template:诗岸、
Template:The VOCALOID Collection2024冬（结构相同，条目列表做了删减）。HTTP 全部 mock，不联网。
"""
from datetime import datetime
from unittest import TestCase
from unittest import mock

from utils import family_template as ft
from utils.family_template import CollectionSync, FamilySync

TEMPLATE = """{{Navbox
|name = 可不/2024
|title = {{coloredlink|#4d79ff|可不}}2024年歌曲
|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsed|mw-collapsible mw-uncollapsed}}
    |group1 = {{coloredlink|#4d79ff|CeVIO传说曲|传说曲}}
    |list1 = {{Navbox subgroup
    |group1 = niconico
    |list1 = 
\t|group2 = bilibili
\t|list2 = {{lj|<!-- 07-17 13:59 -->[[踩到猫儿。|ねこふんじゃった。]]}}
    }}

    |group2 = {{coloredlink|#4d79ff|CeVIO殿堂曲|殿堂曲}}
    |list2 = {{Navbox subgroup
    |group1 = niconico
    |list1 = {{lj|<!-- 01-14 17:00 -->[[爱相爱|愛し愛]]{{W}}<!--
                       02-21 20:00 -->[[出租车|タクシィ]]{{W}}<!--
                       02-23 00:00 -->[[Kaf-eine|可不ェイン]]}}
    |group2 = bilibili
    |list2 = {{lj|[[出租车|タクシィ]]{{W}}<!--
               -->[[电脑眠眠猫|電脳眠眠猫]]}}
    }}
|group3 = 其它{{注||收录Vocawiki已有条目。}}
|list3 = {{lj|[[Fallen]]{{W}}[[Camouflage|カモフラージュ]]}}
}}
<includeonly>{{#if: {{{ nocate | }}} | | [[分类:可不歌曲]] }}</includeonly>"""

ENTRY = "[[活死人乐队|リビングデッドバンデッド]]"
ENTRY_LJ_IN = "[[活死人乐队|{{lj|リビングデッドバンデッド}}]]"      # `[[中文|{{lj|日文}}]]` 写法（歌爱雪）
ENTRY_LJ_OUT = "{{lj|[[活死人乐队|リビングデッドバンデッド]]}}"       # `{{lj|[[中文|日文]]}}` 写法（心华 / 活动模板）
ENTRY_LINKS = "活死人乐队{{!}}{{lj|リビングデッドバンデッド}}"         # `{{links}}` 写法（Dixie Flatline）


class DatedInsertTest(TestCase):
    """列表按**投稿时间**插条目：实测 Template:可不/2024 的条目都带 `<!-- 02-22 23:00 -->`。

    用户 2026-09 报：条目被追加到了列表末尾，而应该插在「同一时刻那几条」后面
    （对照 voca.wiki Template:可不/2024 的 diff 251197）。
    """

    def _posted(self, month=2, day=22, hour=23, minute=0, site="niconico"):
        return ft.PostedAt(datetime(2024, month, day, hour, minute), site)

    def test_inserts_between_the_neighbouring_times(self):
        # 「殿堂曲 → niconico」那一段：01-14 17:00 / 02-21 20:00 / 02-23 00:00
        updated, details = ft.add_honors(TEMPLATE, "niconico", 120_000, ENTRY, year=2024,
                                        posted=self._posted())
        self.assertIn("02-21 20:00 -->[[出租车|タクシィ]]{{W}}<!--\n"
                      "                       02-22 23:00 -->"
                      "[[活死人乐队|リビングデッドバンデッド]]{{W}}<!--\n"
                      "                       02-23 00:00 -->[[Kaf-eine|可不ェイン]]", updated)
        self.assertTrue(any("已加入" in detail for detail in details))

    def test_same_moment_goes_after_the_existing_ones(self):
        # 时刻完全相同 → 插在那几条后面（不是前面）
        updated, _details = ft.add_honors(TEMPLATE, "niconico", 120_000, ENTRY, year=2024,
                                         posted=self._posted(month=2, day=23, hour=0, minute=0))
        self.assertIn("02-23 00:00 -->[[Kaf-eine|可不ェイン]]{{W}}<!--\n"
                      "                       02-23 00:00 -->"
                      "[[活死人乐队|リビングデッドバンデッド]]", updated)

    def test_print_when_later_than_everything(self):
        # 比列表里所有条目都晚 → 追加到末尾，仍然带日期注释（邻居写了时分，自己也写）
        updated, _details = ft.add_honors(TEMPLATE, "niconico", 120_000, ENTRY, year=2024,
                                         posted=self._posted(month=9, day=1))
        self.assertIn("02-23 00:00 -->[[Kaf-eine|可不ェイン]]{{W}}<!--\n"
                      "                       09-01 23:00 -->"
                      "[[活死人乐队|リビングデッドバンデッド]]}}", updated)

    def test_undated_list_still_appends(self):
        # 「其它」那一段没有日期注释（fixture 里只有两条）→ 还是追加到末尾
        updated, _details = ft.add_non_honor(TEMPLATE, ENTRY, year=2024,
                                            posted=self._posted())
        self.assertIn("|list3 = {{lj|[[Fallen]]{{W}}[[Camouflage|カモフラージュ]]"
                      "{{W}}[[活死人乐队|リビングデッドバンデッド]]}}", updated)

    def test_without_posted_time_behaviour_is_unchanged(self):
        updated, _details = ft.add_honors(TEMPLATE, "niconico", 120_000, ENTRY, year=2024)
        self.assertIn("02-23 00:00 -->[[Kaf-eine|可不ェイン]]{{W}}"
                      "[[活死人乐队|リビングデッドバンデッド]]}}", updated)

    def test_stamp_format(self):
        self.assertEqual("02-22 23:00", self._posted().stamp())
        self.assertEqual("02-22", self._posted(hour=0, minute=0).stamp())
        self.assertEqual("youtube 04-20", self._posted(month=4, day=20, hour=0, minute=0,
                                                         site="YouTube").stamp())
        self.assertEqual("Bilibili 07-18", self._posted(month=7, day=18, hour=0, minute=0,
                                                          site="bilibili").stamp())

    def test_honor_without_site_sublist_falls_back_to_the_catch_all_group(self):
        # 殿堂曲那一段只有 niconico / bilibili（实测 Template:可不/2024）→
        # YouTube 上到的殿堂退到「其它」，而不是一处都写不进去
        updated, _reports, fallback = ft.apply_honors(
            TEMPLATE, [("YouTube", 731_918)], ENTRY, 2024, posted=self._posted())
        self.assertIn("|list3 = {{lj|[[Fallen]]{{W}}[[Camouflage|カモフラージュ]]"
                      "{{W}}[[活死人乐队|リビングデッドバンデッド]]}}", updated)
        self.assertTrue(any("已加入「其他」" in detail for detail in fallback), fallback)
        self.assertNotIn("活死人乐队", TEMPLATE.split("|group3")[0], "荣誉小节那半边不该动")

    def test_song_on_two_sites_is_not_written_into_the_catch_all_group(self):
        """多站同时到殿堂时**不再**多写一处「其它」。

        实测 Template:可不/2023：《做吧! 新鲜的小年轻》（やっちゃえ！フレッシュヤング）在
        niconico 与 YouTube 上都过了 10 万，而「殿堂曲」只有 niconico / bilibili 两格 ——
        旧实现在 YouTube 那一步**按站点**兜底，于是荣誉小节里写了一处、「其它」里又写了一处
        （用户 2026-09 对照 diff 251215 报的 diff 251213 就是多了「其它」那一行）。
        """
        updated, reports, fallback = ft.apply_honors(
            TEMPLATE, [("niconico", 500_000), ("YouTube", 731_918)], ENTRY, 2024,
            posted=self._posted())
        self.assertEqual([], fallback)
        self.assertIn("02-22 23:00 -->" + ENTRY, updated.split("|group3")[0])   # 殿堂曲里写上了
        self.assertEqual(TEMPLATE.split("|group3")[1], updated.split("|group3")[1],
                         "「其它」那一组不该被动过")
        details = [detail for _site, _views, group in reports for detail in group]
        self.assertTrue(any("没有 YouTube" in detail for detail in details), details)

# 未达殿堂：平铺 ` • ` 列表，条目里用 `[[中文|{{lj|日文}}]]` 写法（实测 Template:歌爱雪）
NON_HONOR_TEMPLATE = """{{Navbox
|group1 = {{coloredlink|#FFF|VOCALOID殿堂曲|殿堂曲}}
|list1 = [[嘴唇核子弹|{{lj|くちびる核爆弾}}]] • [[ITAMIWAKE]]
|group2 = {{mousetext|部分未殿堂曲|指niconico及bilibili投稿}}
|list2 = [[嘴唇核子弹|{{lj|くちびる核爆弾}}]] • <!--
             -->[[ITAMIWAKE]]
}}"""

# 未达殿堂：组内还按年份分格（实测 Template:SOLARIA / Template:夏语遥）
NON_HONOR_YEAR_TEMPLATE = """{{Navbox
|group1 = 部分<br class='nomobile'/>非殿堂曲
|list1 = {{Navbox subgroup
    |groupstyle = background:#fff
    |group1 = 2022年
    |list1 = <!--1.2-->[[Our Universe]]
    |group2 = 2023年
    |list2 = <!--1.20-->[[赛赫美特，太阳之女]] • [[untied laces]]
}}
}}"""

# 未达殿堂：`{{hlist|…}}` 多行写法（实测 Template:诗岸 / Template:心华）
NON_HONOR_HLIST_TEMPLATE = """{{Navbox
|list1 = {{Navbox subgroup
|group1 = {{coloredlink|#000|Synthesizer V殿堂曲|殿堂曲}}
|list1 =  {{hlist|
{{lj|[[惊蛰正中央]]}}|
[[无|-{無}-]]
}}
|group2 = 部分<br class='nomobile'/>非殿堂曲
|list2 =  {{hlist|
[[迷途]]
}}
}}}}"""

# 实测 Template:The VOCALOID Collection2024冬（删去部分名次段落）
COLLECTION_TEMPLATE = """{{Navbox
| name  = The VOCALOID Collection2024冬
| title = {{Coloredlink|white|The VOCALOID Collection}} ~2024 Winter~
| state =  mw-collapsible {{#ifeq:{{{1}}}|uncollapsed|mw-uncollapsed|mw-collapsed}}
| list1  = 
{{Navbox|child
 | title = TOP100
 | state = mw-collapsed
 | group1 = 1-10位
 | list1  = {{lj|[[医学|イガク]]}}<!--
     --> • {{lj|[[Smart???|スマート???]]}}<!--
     --> • [[CAMPAIGNR]]
 | group2 = 11-20位
 | list2 = {{lj|[[流行的ice|流行りのアイス]]}}
}}
| list4  = 
{{Navbox|child
 | title = 其他歌曲
 | group1 = 其他部门
 | list1 = <!-- sm43360036 -->{{lj|[[长音厨肺活量测试|長音厨肺活量テスト]]}}
 | group2 = 未上榜歌曲
 | list2 = <!-- sm43412292 -->{{lj|[[困困章鱼在此|ねむねむだこがいる]]}}<!--
     sm43424452 --> • {{lj|[[Prima donna|プリマドンナ]]}}
}}
}}"""

# 实测抓自 voca.wiki 的两种 state 写法
EXPANDED_TEMPLATE = """{{Navbox
|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsed|mw-collapsible mw-uncollapsed}}
|list1 = {{Navbox subgroup
|state = mw-collapsible mw-collapsed
}}
}}"""

# 旧写法：条目还没建时，榜单模板里只能用日文原名链
# （实测 Template:The VOCALOID Collection2024冬 里的 {{lj|[[どろぼうねこ]]}}）
COLLECTION_TEMPLATE_OLD_LINK = """{{Navbox
| list1  = 
{{Navbox|child
 | title = TOP100
 | group1 = 1-10位
 | list1  = {{lj|[[医学|イガク]]}}<!--
     --> • {{lj|[[リビングデッドバンデッド]]}}
 | group2 = 11-20位
 | list2 = {{lj|[[流行的ice|流行りのアイス]]}}
}}
}}"""

# 未达殿堂的平铺列表里也有同样的旧写法
NON_HONOR_TEMPLATE_OLD_LINK = """{{Navbox
|group2 = {{mousetext|部分非殿堂曲|指niconico及bilibili投稿}}
|list2 = [[嘴唇核子弹|{{lj|くちびる核爆弾}}]] • {{lj|[[リビングデッドバンデッド]]}}
}}"""

# 实测 Template:NurseRobot_TypeT：根本没有「非殿堂曲」这一组，只有
# 「传说曲 / 殿堂曲 / 其他（收录 Vocawiki 已有条目）」，未达殿堂的歌只能写进「其他」
NON_HONOR_OTHER_TEMPLATE = """{{Navbox
|title = [[NurseRobot_TypeT]]
    |group1 = {{color|#400101|传说曲}}
    |list1 = {{Navbox subgroup
        |group1 = YouTube
        |list1 = {{lj|[[如若遭遇梦魇|かなしばりに遭ったら]]}}
    }}
    |group2 = {{color|#400101|殿堂曲}}
    |list2 = {{Navbox subgroup
        |group1 = niconico
        |list1 = {{lj|[[从乌托邦中逃出|ユートピアから抜け出して]]}}
    }}
|group3 = 其他{{注||收录Vocawiki已有条目。}}
|list3  = {{lj|<!-- 2023-04-19 -->[[%]] • <!-- 2024-02-23 -->[[column|コラム]]}}
}}"""

# 实测：同一首歌在活动模板里可能已经列在**别的赛道 / 名次段落**里
COLLECTION_TWO_TRACKS = """{{Navbox
| list1  = 
{{Navbox|child
 | title = TOP100
 | group1 = 81-90位
 | list1  = {{lj|[[ぎゃらくしぃ☆れふれじれいたぁ]]}}<!--
     --> • {{lj|[[column|コラム]]}}
}}
| list2  = 
{{Navbox|child
 | title = ROOKIE
 | group1 = 81-90位
 | list1  = {{lj|[[アグノスティック]]}}
}}
}}"""

# 实测抓自 Template:The VOCALOID Collection2022春（条目列表有删减）。
# 涅槃(HotaRu) 真身：TOP100「61-70位」的最后一条（= 第 70 名）+ ROOKIE「41-50位」的第 2 条（= 第 42 名），
# 所以这里的名次按「区间起点 + 段内第几个」算：TOP100 那段里它排第 3 个 → 61 + 2 = 63。
# `イガクを語る何か` 是故意留的坑：它是别人的条目，文字里含到另一首歌的名字。
COLLECTION_2022_SPRING = """{{Navbox
| name  = The VOCALOID Collection2022春
| title = {{Coloredlink|white|The VOCALOID Collection}} ~2022 Spring~
| list1  = 
{{Navbox|child
 | title = TOP100
 | group1 = 1-10位
 | list1  = {{lj|[[随之任之|まにまに]]}}<!--
     --> • {{lj|[[感情欺诈|感情ディシーブ]]}}
 | group7 = 61-70位
 | list7  = {{lj|[[オットセイ]]}}<!--
     --> • {{lj|[[イガクを語る何か]]}}<!--
     --> • {{lj|[[ニルヴァーナ]]}}
}}
| list2  = 
{{Navbox|child
 | title = ROOKIE
 | group1 = 41-50位
 | list1 = [[Reboot(イツカのヨルに)|Reboot]]<!--
     --> • {{lj|[[ニルヴァーナ]]}}
}}
| list3  = 
{{Navbox|child
 | title = REMIX
 | group1 = 1-10位
 | list1 = {{lj|[[炉心融解/OSTER project|炉心融解 (OSTER project Remix)]]}}
}}
| list4  = 
{{Navbox|child
 | title = 其他歌曲
 | group2 = 未上榜歌曲
 | list2 = <!-- sm40327558 -->[[Hello, world(Lastscaler)|Hello, world]]<!--
     sm40337868 --> • {{lj|[[风太郎|風太郎]]}}
}}
}}"""

# ============ P主模板片段（结构均取自 voca.wiki，条目列表有删减）============

# Template:Chinozo：`{{lj|… • …}}`
PRODUCER_TEMPLATE = """{{Navbox
|name = Chinozo
|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsed|mw-uncollapsed}}
| group1  = 原创&合作<br>歌声合成曲目
| list1   = {{Navbox subgroup
  |group1  =2025年
  |list1   = {{lj|[[ANTI YOU|アンチユー]] • [[螃蟹|カニ]]}}
  |group2  =2026年
  |list2   = {{lj|[[笨蛋狂欢|バッカアノ]] • [[发发发发现象|ファファファ現象]]}}
 }}
| group2 = 专辑
| list2  = [[Chinozo#Rena|Rena]]
}}"""

# Template:Dixie Flatline：`{{links|页面名{{!}}{{lj|日文名}}|…}}`
LINKS_TEMPLATE = """{{Navbox
|group1 = 原创投稿曲目
|list1  = {{Navbox subgroup
  |group1 = 2023年
  |list1  = {{links|武装少女炼狱变{{!}}{{lj|武装少女煉獄変}}|I Know{{!}}{{lj|アイノウ}}}}
  |group2 = 2024年
  |list2  = {{links|Lonesome Girl{{!}}{{lj|ロンサムガール}}}}
}}
|group2 = 专辑
|list2  = x
}}"""

# Template:Kanaria：年份标签不带「年」，`{{lj|{{Links|页面名{{!}}日文名}}}}`
LINKS_IN_LJ_TEMPLATE = """{{Navbox
| group2 = 原创<br>歌声合成曲目
|list2 = {{Navbox subgroup
 | group1 = 2024
 | list1 = {{lj|{{Links|Dec.|冠军{{!}}チャンピオン}}}}
 | group2 = 2025
 | list2 = {{lj|{{Links|卡通女孩{{!}}カートゥーンガール}}}}
}}
}}"""

# Template:buzzG：`{{lj|{{hlist|[[A]]|[[B|あ]]}}}}`
HLIST_IN_LJ_TEMPLATE = """{{Navbox
|group1 = 原创/参与曲目
|list1  = {{Navbox subgroup
    |group1    = 2009
    |list1     = {{lj|{{hlist
        |[[LAST YEAR]]
        |[[红雨|赤い雨]]
    }}}}
}}
}}"""

# 没有年份分组（如 Template:Livetune）
NO_YEAR_TEMPLATE = """{{Navbox
|group2 = 作品
|list2 = {{Navbox subgroup
   |group1 = 出道前作品
   |list1 = [[Packaged]]{{w}}[[Light Song]]
   |group2 = 出道后作品（包括合作曲）
   |list2 = [[Tell Your World]]
}}
}}"""

COLLAPSED_TEMPLATE = """{{Navbox
|name = 重音Teto
|state = {{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|mw-collapsible mw-collapsed}}
}}"""

SINGER_TEMPLATE = """{{Navbox
|state = mw-collapsible {{#ifeq:{{{1<noinclude>|uncollapsed</noinclude>}}}|uncollapsed|mw-uncollapsed|mw-collapsed}}
}}"""

# 没有荣誉 / 非殿堂分组、只按年份罗列曲目（实测 Template:梦的结唱，夢ノ結唱 的 POPY / ROSE / …共用）
DREAM_TEMPLATE = """{{vtestyle|vte=color:white|{{navbox
|name = 梦的结唱
|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsed|mw-collapsible mw-uncollapsed}}
|group1 = {{color|white|聲庫}}
|list1 = {{coloredlink|#fa006e|POPY}} • {{coloredlink|#5050d2|ROSE}} • {{coloredlink|#fadc1f|HALO(梦的结唱)|HALO}}
|group2 = 優秀曲目
|list2 = {{Navbox subgroup
	|group1 = 2023年
	|list1 = {{coloredlink|#5050d2|LOUDER}} • <!--0113
		 -->{{coloredlink|#5050d2|面具依存|{{lj|役にすがる}}}}
	|group2 = 2024年
	|list2 = {{coloredlink|#fa006e|汀の宿|{{lj|汀の宿}}}} • {{coloredlink|#5050d2|繁缕}}<!-- 0818 -->
}}
|group3 = 專輯
|list3 = [[Infructescence]]
}}}}"""


class EntryLinkTest(TestCase):
    def test_uses_chinese_page_name_with_japanese_display(self):
        self.assertEqual(ENTRY, ft.entry_link("活死人乐队", "リビングデッドバンデッド"))

    def test_plain_link_when_names_match(self):
        self.assertEqual("[[Melt]]", ft.entry_link("Melt", "Melt"))
        self.assertEqual("[[Melt]]", ft.entry_link("Melt", ""))
        self.assertEqual("[[Melt]]", ft.entry_link("Melt", None))


class HonorKeywordTest(TestCase):
    def test_levels_include_lower_ones(self):
        self.assertEqual(["传说", "殿堂"], [k[0] for k in ft.honor_keywords(1_200_000)])
        self.assertEqual(["神话", "传说", "殿堂"], [k[0] for k in ft.honor_keywords(12_000_000)])
        self.assertEqual(["破亿", "神话", "传说", "殿堂"], [k[0] for k in ft.honor_keywords(100_000_000)])

    def test_below_hall_of_fame(self):
        self.assertEqual([], ft.honor_keywords(99_999))


class ScanParamsTest(TestCase):
    def test_reads_top_level_groups(self):
        names = [name for name, *_ in ft.scan_params(TEMPLATE)]
        self.assertIn("group1", names)
        self.assertIn("list2", names)
        self.assertNotIn("groupstyle", names)          # 子模板里的参数不算顶层

    def test_piped_links_do_not_split_params(self):
        text = "{{Navbox\n|list1 = {{lj|[[出租车|タクシィ]]{{W}}[[电脑眠眠猫|電脳眠眠猫]]}}\n|group2 = bilibili\n}}"
        params = dict((name, value) for name, value, *_ in ft.scan_params(text))
        self.assertEqual("{{lj|[[出租车|タクシィ]]{{W}}[[电脑眠眠猫|電脳眠眠猫]]}}", params["list1"])

    def test_iter_blocks_finds_nested_calls(self):
        text = "{{Navbox|list1 = {{Navbox subgroup|group1 = niconico}}}}{{lj|[[A]]}}"
        starts = [start for start, _ in ft.iter_blocks(text)]
        self.assertEqual([0, text.index("{{Navbox subgroup"), text.index("{{lj|")], starts)

    def test_iter_groups_looks_into_subtemplates(self):
        # 分组藏在 {{Navbox subgroup}} 里也要能找到（诗岸 / 言和 就是这种结构）
        labels = [label for label, _, _ in ft.iter_groups(NON_HONOR_HLIST_TEMPLATE)]
        self.assertIn("{{coloredlink|#000|Synthesizer V殿堂曲|殿堂曲}}", labels)
        self.assertIn("部分<br class='nomobile'/>非殿堂曲", labels)

    def test_find_group_span_and_exclude(self):
        label, _, _ = ft.find_group_span(TEMPLATE, ("殿堂",))
        self.assertIn("殿堂曲", label)
        self.assertIn("未殿堂", ft.find_group_span(NON_HONOR_TEMPLATE, ("未殿堂",))[0])
        # 「部分非殿堂曲」也含「殿堂」二字：排在前面时会被误认，必须靠 exclude 排掉
        ordered = "{{Navbox\n|group1 = 部分非殿堂曲\n|list1 = [[A]]\n|group2 = 殿堂曲\n|list2 = [[B]]\n}}"
        self.assertIn("非殿堂曲", ft.find_group_span(ordered, ("殿堂",))[0])
        self.assertEqual("殿堂曲", ft.find_group_span(
            ordered, ("殿堂",), exclude=ft.NON_HONOR_KEYWORDS)[0])
        self.assertIsNone(ft.find_group_span(ordered, ("神话",)))

    def test_short_label(self):
        self.assertEqual("传说曲", ft._short_label("{{color|#f2dfe6|传说曲}}"))
        self.assertEqual("CeVIO传说曲", ft._short_label("{{coloredlink|#4d79ff|CeVIO传说曲|传说曲}}"))
        self.assertEqual("部分未殿堂曲", ft._short_label("{{mousetext|部分未殿堂曲|指niconico投稿}}"))
        self.assertEqual("2019年", ft._short_label("2019年"))


class NavboxStateTest(TestCase):
    def test_template_expanded_by_default(self):
        # 不传参 -> mw-collapsible mw-uncollapsed（展开）→ 需要 |collapsed
        self.assertEqual(ft.EXPANDED, ft.default_state(EXPANDED_TEMPLATE))
        self.assertTrue(ft.needs_collapsed(EXPANDED_TEMPLATE))

    def test_template_collapsed_by_default(self):
        # 不传参 -> mw-collapsible mw-collapsed（折叠）→ 不用动
        self.assertEqual(ft.COLLAPSED, ft.default_state(COLLAPSED_TEMPLATE))
        self.assertFalse(ft.needs_collapsed(COLLAPSED_TEMPLATE))

    def test_real_collection_template_is_collapsed(self):
        self.assertEqual(ft.COLLAPSED, ft.default_state(COLLECTION_TEMPLATE))

    def test_default_value_with_noinclude(self):
        # {{{1<noinclude>|uncollapsed</noinclude>}}} 的默认值是 uncollapsed → 展开
        self.assertEqual(ft.EXPANDED, ft.default_state(SINGER_TEMPLATE))

    def test_bare_collapsible_counts_as_expanded(self):
        text = "|state = {{#ifeq:{{{1|}}}|collapsed|mw-collapsed|mw-collapsible}}\n"
        self.assertEqual(ft.EXPANDED, ft.default_state(text))

    def test_hard_coded_state_is_unknown(self):
        # 状态写死了，条目里传参也没用 -> 不改
        text = "|state = mw-collapsible mw-collapsed\n|x = {{#ifeq:{{{a}}}|b|c|d}}\n"
        self.assertEqual(ft.UNKNOWN, ft.default_state(text))
        self.assertFalse(ft.needs_collapsed(text))

    def test_missing_or_unparsable(self):
        self.assertEqual(ft.UNKNOWN, ft.default_state(None))
        self.assertEqual(ft.UNKNOWN, ft.default_state(""))
        self.assertEqual(ft.UNKNOWN, ft.default_state("{{Navbox\n|list1 = x\n}}"))
        # 认不出的写法（#switch）-> 不动
        text = "|state = {{#switch:{{{1|}}}|collapsed=mw-collapsed|#default=mw-uncollapsed}}\n"
        self.assertEqual(ft.UNKNOWN, ft.default_state(text))


class NavboxFetchTest(TestCase):
    def setUp(self):
        ft._template_cache.clear()

    def _payload(self, pages):
        return {"query": {"pages": pages}}

    def test_fetches_and_caches(self):
        session = mock.Mock()
        session.get.return_value.json.return_value = self._payload([
            {"title": "Template:可不/2024",
             "revisions": [{"slots": {"main": {"content": EXPANDED_TEMPLATE}}}]}
        ])
        with mock.patch.object(ft.login, "get_api_session", return_value=session):
            first = ft.fetch_template_text("可不/2024")
            second = ft.fetch_template_text("可不/2024")
        self.assertEqual(EXPANDED_TEMPLATE, first)
        self.assertEqual(EXPANDED_TEMPLATE, second)
        self.assertEqual(1, session.get.call_count)          # 第二次走缓存
        self.assertEqual("Template:可不/2024", session.get.call_args.kwargs["params"]["titles"])

    def test_missing_page_returns_none(self):
        session = mock.Mock()
        session.get.return_value.json.return_value = self._payload(
            [{"title": "Template:X", "missing": True}])
        with mock.patch.object(ft.login, "get_api_session", return_value=session):
            self.assertIsNone(ft.fetch_template_text("X"))

    def test_network_error_is_swallowed(self):
        session = mock.Mock()
        session.get.side_effect = OSError("boom")
        with mock.patch.object(ft.login, "get_api_session", return_value=session), \
             mock.patch.object(ft.time, "sleep"):
            self.assertIsNone(ft.fetch_template_text("X"))
        # 网络类失败不缓存 -> 下次还会再试
        self.assertNotIn("Template:X", ft._template_cache)

    def test_retries_once_after_failure(self):
        session = mock.Mock()
        session.get.side_effect = [OSError("boom"),
                                   mock.Mock(json=lambda: self._payload([
                                       {"title": "Template:可不/2024",
                                        "revisions": [{"slots": {"main": {"content": EXPANDED_TEMPLATE}}}]}
                                   ]))]
        with mock.patch.object(ft.login, "get_api_session", return_value=session), \
             mock.patch.object(ft.time, "sleep"):
            text = ft.fetch_template_text("可不/2024")
        self.assertEqual(EXPANDED_TEMPLATE, text)
        self.assertEqual(2, session.get.call_count)


class CollapseTest(TestCase):
    def setUp(self):
        ft._template_cache.clear()

    def _with_text(self, text):
        return mock.patch.object(ft, "fetch_template_text", return_value=text)

    def test_adds_collapsed_when_expanded(self):
        with self._with_text(EXPANDED_TEMPLATE):
            self.assertEqual("可不/2024|collapsed", ft.collapse_if_expanded("可不/2024"))

    def test_keeps_template_when_already_collapsed(self):
        with self._with_text(COLLAPSED_TEMPLATE):
            self.assertEqual("重音Teto/2024", ft.collapse_if_expanded("重音Teto/2024"))

    def test_keeps_template_when_unknown(self):
        with self._with_text(None):
            self.assertEqual("某人", ft.collapse_if_expanded("某人"))

    def test_skips_name_already_containing_collapsed(self):
        with mock.patch.object(ft, "fetch_template_text") as fetch:
            self.assertEqual("可不|collapsed", ft.collapse_if_expanded("可不|collapsed"))
        fetch.assert_not_called()

    def test_collapse_all_keeps_order(self):
        def fake(name):
            return f"{name}|collapsed" if name.startswith("可不") else name

        with mock.patch.object(ft, "collapse_if_expanded", side_effect=fake):
            self.assertEqual(["A", "可不/2024|collapsed", "B"],
                             ft.collapse_all(["A", "可不/2024", "B"]))


class AppendEntryTest(TestCase):
    def test_inside_lj_wrapper(self):
        self.assertEqual("{{lj|[[A]]{{W}}[[B]]}}",
                         ft.append_entry("{{lj|[[A]]}}", "[[B]]"))

    def test_keeps_trailing_comment(self):
        self.assertEqual("{{lj|[[A]]{{W}}<!-- 1 -->[[B]]{{W}}[[C]]}}",
                         ft.append_entry("{{lj|[[A]]{{W}}<!-- 1 -->[[B]]}}", "[[C]]"))

    def test_empty_list_gets_lj_wrapper(self):
        self.assertEqual("{{lj|[[B]]}}", ft.append_entry("", "[[B]]"))
        self.assertEqual("{{lj|[[B]]}}", ft.append_entry("   \n", "[[B]]"))
        self.assertEqual("{{lj|[[B]]}}", ft.append_entry("{{lj|}}", "[[B]]"))

    def test_plain_list(self):
        self.assertEqual("[[A]]{{W}}[[B]]", ft.append_entry("[[A]]", "[[B]]"))

    def test_flat_list_keeps_bullet_separator_and_style(self):
        value = "[[嘴唇核子弹|{{lj|くちびる核爆弾}}]] • [[ITAMIWAKE]]"
        self.assertEqual(value + " • [[新曲|{{lj|しんきょく}}]]",
                         ft.append_entry(value, "[[新曲|しんきょく]]"))

    def test_single_lj_item_is_not_mistaken_for_wrapper(self):
        # `{{lj|A}}{{W}}-->{{lj|B}}` 不是「整段被 lj 包住」，要按平铺列表处理
        value = "{{lj|[[A|あ]]}}{{W}}<!--\n -->{{lj|[[B|び]]}}"
        self.assertEqual(value + "{{W}}{{lj|[[C|し]]}}", ft.append_entry(value, "[[C|し]]"))

    def test_hlist_single_line(self):
        self.assertEqual("{{hlist|[[A]]|[[B]]}}", ft.append_entry("{{hlist|[[A]]}}", "[[B]]"))

    def test_hlist_multi_line_keeps_indent(self):
        value = "{{hlist|\n              |[[弒月]]\n              |[[ONE OFF MIND]]\n            }}"
        self.assertEqual("{{hlist|\n              |[[弒月]]\n              |[[ONE OFF MIND]]\n"
                         "              |[[新曲]]\n            }}",
                         ft.append_entry(value, "[[新曲]]"))

    def test_hlist_uses_item_style(self):
        value = "{{hlist|\n{{lj|[[猫之街|貓の街]]}}|\n[[Rain Swing!]]\n}}"
        self.assertIn("{{lj|[[新曲|しんきょく]]}}", ft.append_entry(value, "[[新曲|しんきょく]]"))


class AddEntryTest(TestCase):
    def test_adds_to_matching_honor_and_site(self):
        new, detail = ft.add_entry(TEMPLATE, "bilibili", ("传说",), ENTRY)
        self.assertIn("已加入", detail)
        self.assertIn("[[踩到猫儿。|ねこふんじゃった。]]{{W}}" + ENTRY, new)
        self.assertTrue(ft._balanced(new))

    def test_adds_to_empty_site_list(self):
        new, detail = ft.add_entry(TEMPLATE, "niconico", ("传说",), ENTRY)
        self.assertIn("已加入", detail)
        self.assertIn("|list1 = \n\t{{lj|" + ENTRY + "}}\n|group2", new)

    def test_appends_inside_existing_lj(self):
        new, _ = ft.add_entry(TEMPLATE, "bilibili", ("殿堂",), ENTRY)
        self.assertIn("[[电脑眠眠猫|電脳眠眠猫]]{{W}}" + ENTRY + "}}", new)

    def test_missing_group_or_site(self):
        _, detail = ft.add_entry(TEMPLATE, "bilibili", ("神话",), ENTRY)
        self.assertIn("没有", detail)
        _, detail = ft.add_entry(TEMPLATE, "YouTube", ("殿堂",), ENTRY)
        self.assertIn("没有 YouTube 子列表", detail)

    def test_idempotent(self):
        once, _ = ft.add_entry(TEMPLATE, "bilibili", ("殿堂",), ENTRY)
        twice, detail = ft.add_entry(once, "bilibili", ("殿堂",), ENTRY)
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_idempotent_ignores_item_style(self):
        # 模板里写成 `[[中文|{{lj|日文}}]]` 时也不该重复插入
        once, _ = ft.add_non_honor(NON_HONOR_TEMPLATE, "[[新曲|しんきょく]]")
        twice, [detail] = ft.add_non_honor(once, "[[新曲|しんきょく]]")
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_other_sections_are_untouched(self):
        new, _ = ft.add_entry(TEMPLATE, "bilibili", ("殿堂",), ENTRY)
        self.assertIn("|list3 = {{lj|[[Fallen]]{{W}}[[Camouflage|カモフラージュ]]}}", new)
        self.assertNotIn(ENTRY + "{{W}}", new.split("|list3")[1])

    def test_add_honors_inserts_every_reached_level(self):
        new, details = ft.add_honors(TEMPLATE, "bilibili", 1_200_000, ENTRY)
        self.assertEqual(2, len(details))
        self.assertIn(ENTRY, new.split("|group2 = {{coloredlink")[0])       # 传说曲
        self.assertIn(ENTRY, new.split("|group2 = {{coloredlink")[1])       # 殿堂曲

    def test_honor_lookup_skips_non_honor_group(self):
        # 「部分未殿堂曲」也含「殿堂」二字，不能被当成殿堂曲小节
        new, _ = ft.add_honors(NON_HONOR_TEMPLATE, "bilibili", 200_000, ENTRY)
        honor_part, non_honor_part = new.split("|group2")
        self.assertIn("[[ITAMIWAKE]] • " + ENTRY_LJ_IN, honor_part)
        self.assertNotIn("活死人乐队", non_honor_part)


class NonHonorTest(TestCase):
    def test_flat_list_uses_existing_style(self):
        new, [detail] = ft.add_non_honor(NON_HONOR_TEMPLATE, ENTRY)
        self.assertIn("已加入「部分非殿堂曲」", detail)
        self.assertIn("[[ITAMIWAKE]] • " + ENTRY_LJ_IN, new)
        self.assertTrue(ft._balanced(new))

    def test_year_subgroup_is_picked_by_upload_year(self):
        new, _ = ft.add_non_honor(NON_HONOR_YEAR_TEMPLATE, ENTRY, year=2023)
        self.assertIn("[[赛赫美特，太阳之女]] • [[untied laces]] • " + ENTRY, new)
        self.assertNotIn(ENTRY, new.split("|group2")[0])            # 没写进 2022 年那格
        self.assertTrue(ft._balanced(new))

    def test_year_subgroup_without_matching_year_reports(self):
        new, [detail] = ft.add_non_honor(NON_HONOR_YEAR_TEMPLATE, ENTRY, year=2019)
        self.assertEqual(NON_HONOR_YEAR_TEMPLATE, new)
        self.assertIn("2019 年", detail)

    def test_hlist_group(self):
        new, [detail] = ft.add_non_honor(NON_HONOR_HLIST_TEMPLATE, ENTRY)
        self.assertIn("已加入「部分非殿堂曲」", detail)
        self.assertIn("{{hlist|\n[[迷途]]\n|" + ENTRY + "\n}}", new)
        self.assertTrue(ft._balanced(new))

    def test_no_non_honor_group_falls_back_to_other(self):
        """连「非殿堂曲」都没有时退到「其他 / 其它」（实测 Template:可不/2024 只有「其它」）。"""
        new, [detail] = ft.add_non_honor(TEMPLATE, ENTRY)
        self.assertEqual("已加入「其他」", detail)
        self.assertIn("[[Camouflage|カモフラージュ]]{{W}}" + ENTRY, new)
        self.assertTrue(ft._balanced(new))

    def test_other_group_used_when_template_has_no_non_honor_group(self):
        """实测 Template:NurseRobot_TypeT：未达殿堂的歌写进「其他」那一组。"""
        new, [detail] = ft.add_non_honor(NON_HONOR_OTHER_TEMPLATE, ENTRY)
        self.assertEqual("已加入「其他」", detail)
        self.assertIn("[[column|コラム]] • " + ENTRY, new)
        self.assertNotIn(ENTRY, new.split("|group2")[1].split("|group3")[0])   # 没跑去「殿堂曲」
        self.assertTrue(ft._balanced(new))

    def test_non_honor_group_wins_over_other(self):
        """两都有时优先「非殿堂曲」，不去动「其他」。"""
        text = "{{Navbox\n|group1 = 部分非殿堂曲\n|list1 = [[迷途]]\n" \
               "|group2 = 其他\n|list2 = [[别动我]]\n}}"
        new, [detail] = ft.add_non_honor(text, ENTRY)
        self.assertEqual("已加入「部分非殿堂曲」", detail)
        self.assertIn("[[迷途]]{{W}}" + ENTRY, new)
        self.assertIn("[[别动我]]", new)

    def test_no_group_at_all_reports(self):
        _, [detail] = ft.add_non_honor("{{Navbox\n|group1 = 殿堂曲\n|list1 = [[A]]\n}}", ENTRY)
        self.assertIn("模板里没有荣誉 / 非殿堂分组", detail)
        self.assertIn("不知道投稿年份", detail)

    def test_add_honors_falls_back_when_below_threshold(self):
        new, details = ft.add_honors(NON_HONOR_TEMPLATE, "bilibili", 99_999, ENTRY)
        self.assertEqual(1, len(details))
        self.assertIn(ENTRY_LJ_IN, new.split("|group2")[1])


class YearListTemplateTest(TestCase):
    """没有荣誉 / 非殿堂分组、只按投稿年份罗列曲目的模板（实测 Template:梦的结唱）。

    以前这种模板只会提示「模板里没有“殿堂”分组」，条目根本写不进去 ——
    现在退到「按投稿年份写进年份格」，与 P主模板的写法一致。
    """

    def test_honored_song_goes_into_the_year_slot(self):
        new, [detail] = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2024)
        self.assertEqual("已加入「優秀曲目 → 2024年」", detail)
        # 条目按邻居的写法套上 `{{lj|}}`，用列表原本的 ` • ` 分隔
        self.assertIn("{{coloredlink|#5050d2|繁缕}}<!-- 0818 --> • " + ENTRY_LJ_IN, new)
        self.assertTrue(ft._balanced(new))
        self.assertNotIn(ENTRY_LJ_IN, new.split("|group2 = 2024年")[0], "别动其它年份")

    def test_song_below_the_hall_of_fame_also_goes_by_year(self):
        new, [detail] = ft.add_non_honor(DREAM_TEMPLATE, ENTRY, 2023)
        self.assertEqual("已加入「優秀曲目 → 2023年」", detail)
        self.assertIn("{{lj|役にすがる}}}}" + " • " + ENTRY_LJ_IN, new)
        self.assertTrue(ft._balanced(new))

    def test_year_not_in_the_template_is_reported(self):
        new, [detail] = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2019)
        self.assertEqual(DREAM_TEMPLATE, new)
        self.assertIn("也没有 2019 年的分组", detail)

    def test_duplicate_is_not_added_twice(self):
        once, _ = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2024)
        twice, [detail] = ft.add_honors(once, "niconico", 1_200_000, ENTRY, 2024)
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_entry_already_wrapped_in_lj_is_not_wrapped_again(self):
        """邻居项目已经是 `[[中文|{{lj|日文}}]]` 时，别套成 `{{lj|{{lj|…}}}}`。"""
        new, _ = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY_LJ_IN, 2024)
        self.assertIn(ENTRY_LJ_IN, new)
        self.assertNotIn("{{lj|{{lj|", new)

    def test_named_color_is_not_mistaken_for_the_group_label(self):
        """`{{color|white|聲庫}}` / `{{coloredlink|#fa006e|POPY}}` 这类标签要取真正的那段文字。"""
        self.assertEqual("聲庫", ft._short_label("{{color|white|聲庫}}"))
        self.assertEqual("POPY", ft._short_label("{{coloredlink|#fa006e|POPY}}"))
        # 带前缀的写法本来就含「传说」关键词，保持原样（CeVIO传说曲）
        self.assertEqual("CeVIO传说曲", ft._short_label("{{coloredlink|#4d79ff|CeVIO传说曲|传说曲}}"))

    def test_honor_groups_still_win_when_the_template_has_them(self):
        """有荣誉小节时照旧走荣誉（不能跑去写年份格）。"""
        new, details = ft.add_honors(TEMPLATE, "bilibili", 1_200_000, ENTRY)
        self.assertIn("已加入「传说 → bilibili」", details[0])
        self.assertIn("已加入「殿堂 → bilibili」", details[1])


class VoicebankColorTest(TestCase):
    """邻居用 `{{coloredlink|#色|…}}` 时，按本曲歌姬给条目配色（实测 Template:梦的结唱）。"""

    def test_colors_are_read_from_the_template(self):
        """颜色表从模板里现读，不写死：`{{coloredlink|#fa006e|POPY}}` → POPY 粉色。"""
        colors = ft.voicebank_colors(DREAM_TEMPLATE)
        self.assertEqual("#fa006e", colors["popy"])
        self.assertEqual("#5050d2", colors["rose"])
        self.assertEqual("#fadc1f", colors["halo"], "`HALO(梦的结唱)` 的消歧义后缀要去掉")

    def test_color_matches_the_song_vocalist(self):
        self.assertEqual("#5050d2", ft.color_for(DREAM_TEMPLATE, ["ROSE"]))
        # 多个歌姬时取第一个认得出的
        self.assertEqual("#fa006e", ft.color_for(DREAM_TEMPLATE, ["鏡音リン", "POPY"]))
        self.assertIsNone(ft.color_for(DREAM_TEMPLATE, ["初音ミク"]))
        self.assertIsNone(ft.color_for(DREAM_TEMPLATE, []))
        self.assertIsNone(ft.color_for(TEMPLATE, ["POPY"]), "没用颜色的模板不该给出颜色")

    def test_entry_gets_the_vocalist_color(self):
        new, [detail] = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2024,
                                      ["POPY"])
        self.assertEqual("已加入「優秀曲目 → 2024年」", detail)
        self.assertIn(" • {{coloredlink|#fa006e|活死人乐队|{{lj|リビングデッドバンデッド}}}}", new)
        self.assertTrue(ft._balanced(new))

    def test_unknown_vocalist_falls_back_to_a_plain_link(self):
        """歌姬不在配色表里时不猜颜色，写成普通链接。"""
        new, _ = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2023, ["初音ミク"])
        self.assertIn(" • " + ENTRY_LJ_IN, new)
        self.assertNotIn("{{coloredlink|#fa006e|活死人乐队", new)
        self.assertNotIn("{{coloredlink|#5050d2|活死人乐队", new)

    def test_second_vocalist_color_is_used_when_the_first_is_unknown(self):
        new, _ = ft.add_honors(DREAM_TEMPLATE, "niconico", 1_200_000, ENTRY, 2024,
                               ["初音ミク", "ROSE"])
        self.assertIn("{{coloredlink|#5050d2|活死人乐队|", new)

    def test_family_sync_passes_the_vocalists_through(self):
        """整条链路：`FamilySync(vocalists=…)` → 写回时颜色也在（提交窗口走的就是它）。"""
        with mock.patch("utils.family_template.fetch_template_text", return_value=DREAM_TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync(FamilySync(templates=["梦的结唱"], year=2024, vocalists=["ROSE"]),
                            "活死人乐队", "リビングデッドバンデッド")
        self.assertTrue(lines)
        written = edit.call_args.args[1]
        self.assertIn("{{coloredlink|#5050d2|活死人乐队|{{lj|リビングデッドバンデッド}}}}", written)
        self.assertTrue(ft._balanced(written))


class CollectionTest(TestCase):
    def test_rank_goes_into_matching_range(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "TOP100", 5, ENTRY)
        self.assertEqual("已加入「TOP100 → 1-10位」", detail)
        self.assertIn("[[CAMPAIGNR]] • {{lj|" + ENTRY + "}}", new)
        self.assertTrue(ft._balanced(new))

    def test_rank_goes_into_second_range(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "TOP100", 15, ENTRY)
        self.assertEqual("已加入「TOP100 → 11-20位」", detail)
        # 单项 `{{lj|…}}` 列表按「整段包住」处理，沿用 `{{W}}` 分隔
        self.assertIn("{{lj|[[流行的ice|流行りのアイス]]{{W}}" + ENTRY, new)

    def test_rank_without_section(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "TOP100", 99, ENTRY)
        self.assertEqual(COLLECTION_TEMPLATE, new)
        self.assertIn("没有第 99 名所在的段落", detail)

    def test_unranked_goes_to_unranked_list(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "榜外", None, ENTRY)
        self.assertEqual("已加入「其他歌曲 → 未上榜歌曲」", detail)
        self.assertIn("[[Prima donna|プリマドンナ]]}} • {{lj|" + ENTRY + "}}", new)

    def test_missing_track(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "ROOKIE", 3, ENTRY)
        self.assertEqual(COLLECTION_TEMPLATE, new)
        self.assertIn("没有「ROOKIE」赛道", detail)

    def test_missing_rank(self):
        _, detail = ft.add_collection_entry(COLLECTION_TEMPLATE, "TOP100", None, ENTRY)
        self.assertIn("缺少名次", detail)

    def test_idempotent(self):
        once, _ = ft.add_collection_entry(COLLECTION_TEMPLATE, "TOP100", 5, ENTRY)
        twice, detail = ft.add_collection_entry(once, "TOP100", 5, ENTRY)
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_song_listed_in_another_track_is_not_duplicated(self):
        """实测：TOP100 → 81-90位 里已有这首歌，按 ROOKIE 同步时不能再加一份。"""
        new, detail = ft.add_collection_entry(COLLECTION_TWO_TRACKS, "ROOKIE", 85,
                                              "[[column|コラム]]")
        self.assertEqual(COLLECTION_TWO_TRACKS, new)
        self.assertIn("「TOP100 → 81-90位」里已有该条目", detail)

    def test_song_linked_by_japanese_name_in_another_track_is_relinked(self):
        """实测那次错误编辑：TOP100 里用日文原名链着 `[[コラム]]`，
        同步时按赛道/名次算到的是 ROOKIE → 应该改指原来那条，而不是往 ROOKIE 再塞一份。"""
        text = COLLECTION_TWO_TRACKS.replace("[[column|コラム]]", "[[コラム]]")
        self.assertIn("[[コラム]]", text)
        new, detail = ft.add_collection_entry(text, "ROOKIE", 85, "[[column|コラム]]")
        self.assertEqual("已把「TOP100 → 81-90位」里的「コラム」改指到「column」", detail)
        self.assertIn("{{lj|[[column|コラム]]}}", new)
        self.assertNotIn("[[コラム]]", new)
        self.assertNotIn("column", new.split("ROOKIE")[1], "ROOKIE 那段不该被动")
        self.assertTrue(ft._balanced(new))

    def test_ranked_flag(self):
        self.assertTrue(CollectionSync("X", "TOP100", 3).ranked)
        self.assertFalse(CollectionSync("X", "榜外", None).ranked)
        self.assertFalse(CollectionSync("X", "TOP100", None).ranked)

    def test_placements_fall_back_to_track_and_rank(self):
        """没给 places（人工询问那条路）时就看 track / rank 这一对。"""
        self.assertEqual([("TOP100", 3)], CollectionSync("X", "TOP100", 3).placements())
        self.assertEqual([(None, None)], CollectionSync("X").placements())

    def test_places_are_written_into_both_tracks(self):
        """新歌（还没列在模板里）两榜都有时逐赛道写：第二遍发现已在模板里就不再塞一份。"""
        sync = CollectionSync("The VOCALOID Collection2022春", "TOP100", 70,
                              [("TOP100", 70), ("ROOKIE", 42)])
        self.assertEqual([("TOP100", 70), ("ROOKIE", 42)], sync.placements())
        self.assertTrue(sync.ranked)
        with mock.patch("utils.family_template.fetch_template_text",
                        return_value=COLLECTION_2022_SPRING):
            lines = ft.build_collection_plan(sync, "新歌", "あたらしい歌")
        self.assertEqual(2, len(lines))
        self.assertIn("TOP100 第 70 名", lines[0])
        self.assertIn("已加入「TOP100 → 61-70位」", lines[0])
        self.assertIn("ROOKIE 第 42 名", lines[1])
        self.assertIn("已有该条目，未重复添加", lines[1])

    def test_both_tracks_of_an_existing_entry_are_relinked(self):
        """涅槃这种真身就在模板里（TOP100 + ROOKIE 各一处旧写法）→ 一次同步两处都改指。"""
        sync = CollectionSync("The VOCALOID Collection2022春", "TOP100", 70,
                              [("TOP100", 70), ("ROOKIE", 42)])
        with mock.patch("utils.family_template.fetch_template_text",
                        return_value=COLLECTION_2022_SPRING), \
             mock.patch("utils.family_template.resolve_template_title",
                        side_effect=lambda name: name), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync_collection(sync, "涅槃(HotaRu)", "ニルヴァーナ")
        written = edit.call_args.args[1]
        self.assertEqual(2, written.count("[[涅槃(HotaRu)|ニルヴァーナ]]"))
        self.assertNotIn("[[ニルヴァーナ]]", written)
        self.assertIn("（共 2 处）", lines[0])
        self.assertIn("已有该条目", lines[1])


class CollectionReadTest(TestCase):
    """从活动模板里读「这首歌在哪个赛道、第几名」（用户 2026-09 要求）。

    赛道 / 名次 VocaDB 上没有，所以去爬那一届的模板：榜单按名次分段（`61-70位`）、
    段内按名次排列，名次 = 区间起点 + 段内第几个；同一首歌**两榜都在**就两条都返回。
    """

    def test_both_tracks_are_read_with_ranks(self):
        places = ft.read_collection_places(COLLECTION_2022_SPRING, "涅槃(HotaRu)", "ニルヴァーナ")
        self.assertEqual([("TOP100", 63), ("ROOKIE", 42)],
                         [(place.track, place.rank) for place in places])
        self.assertEqual("TOP100 → 61-70位", places[0].section)
        self.assertEqual("ROOKIE → 41-50位", places[1].section)
        self.assertTrue(all(place.ranked for place in places))

    def test_chinese_link_is_matched_too(self):
        """条目已经建好、模板里改指中文条目后，链接目标是中文名，照样要认得。"""
        text = COLLECTION_2022_SPRING.replace("{{lj|[[ニルヴァーナ]]}}",
                                             "{{lj|[[涅槃(HotaRu)|ニルヴァーナ]]}}")
        places = ft.read_collection_places(text, "涅槃(HotaRu)", "ニルヴァーナ")
        self.assertEqual([("TOP100", 63), ("ROOKIE", 42)],
                         [(place.track, place.rank) for place in places])

    def test_not_listed_anywhere_is_unranked(self):
        self.assertEqual([], ft.read_collection_places(COLLECTION_2022_SPRING,
                                                      "还没上榜的歌", "まだ無い歌"))

    def test_only_in_remix_is_the_remix_track(self):
        """REMIX 也是赛道（用户 2026-09-28 要求，参 Relay Outer/Iyowa）。"""
        places = ft.read_collection_places(COLLECTION_2022_SPRING,
                                          "炉心融解/OSTER project", "炉心融解")
        self.assertEqual([("REMIX", 1)], [(place.track, place.rank) for place in places])
        self.assertEqual("REMIX → 1-10位", places[0].section)

    def test_unlisted_child_is_unranked(self):
        """连 REMIX 也没有（列在「其他歌曲 → 未上榜歌曲」里）→ 榜外。"""
        self.assertEqual([], ft.read_collection_places(COLLECTION_2022_SPRING,
                                                      "Hello, world(Lastscaler)", "Hello, world"))

    def test_song_name_inside_another_entry_does_not_match(self):
        """只在**链接**上对名字：模板里「イガクを語る何か」是别人的条目，别当成这首歌。"""
        self.assertEqual([], ft.read_collection_places(COLLECTION_2022_SPRING, "医学", "イガク"))

    def test_fetch_failure_returns_none(self):
        """模板取不到（不存在 / 网络失败）要能与「两榜都没有」区分开：前者退回问用户。"""
        with mock.patch("utils.family_template.fetch_template_text", return_value=None):
            self.assertIsNone(ft.find_collection_places("The VOCALOID Collection2022春",
                                                       "医学", "イガク"))

    def test_template_name_from_event(self):
        self.assertEqual("The VOCALOID Collection2022春",
                         ft.collection_template_name("ボカコレ2022春"))
        # VocaDB 的英文名会先被 `vocadb.collection_name_to_japanese` 换成ボカコレ…，
        # 直接传英文名时只把前缀去掉（不去汉化季节）
        self.assertEqual("The VOCALOID Collection2024 Winter",
                         ft.collection_template_name("The VOCALOID Collection 2024 Winter"))
        self.assertIsNone(ft.collection_template_name(""))


class RelinkEntryTest(TestCase):
    """列表里已经用日文原名链着同一首歌时，要改指到中文条目，而不是再追加一条。

    实测：Template:The VOCALOID Collection2024冬 里 `{{lj|[[どろぼうねこ]]}}`
    被误改成了 `{{lj|[[どろぶうねこ]]}} • {{lj|[[偷腥猫|どろぶうねこ]]}}`（重复一条），
    正确做法是把原来那条改成 `{{lj|[[偷腥猫|どろぶうねこ]]}}`。
    """

    def test_wraps_display_name_when_missing(self):
        value = "{{lj|[[医学|イガク]]}}{{W}}{{lj|[[リビングデッドバンデッド]]}}"
        new, count = ft.relink_entry(value, ENTRY)
        self.assertEqual(1, count)
        self.assertEqual("{{lj|[[医学|イガク]]}}{{W}}{{lj|[[活死人乐队|リビングデッドバンデッド]]}}", new)

    def test_keeps_existing_display_name(self):
        new, count = ft.relink_entry("[[リビングデッドバンデッド|リビングデッドバンデッド]]", ENTRY)
        self.assertEqual(1, count)
        self.assertEqual("[[活死人乐队|リビングデッドバンデッド]]", new)

    def test_plain_when_display_is_page_name(self):
        new, count = ft.relink_entry("{{lj|[[リビングデッドバンデッド|活死人乐队]]}}", ENTRY)
        self.assertEqual(1, count)
        self.assertEqual("{{lj|[[活死人乐队]]}}", new)

    def test_replaces_every_occurrence(self):
        value = "{{lj|[[リビングデッドバンデッド]]}} • {{lj|[[リビングデッドバンデッド]]}}"
        new, count = ft.relink_entry(value, ENTRY)
        self.assertEqual(2, count)
        self.assertNotIn("[[リビングデッドバンデッド]]", new)

    def test_other_entries_untouched(self):
        value = "{{lj|[[医学|イガク]]}} • [[CAMPAIGNR]]"
        self.assertEqual((value, 0), ft.relink_entry(value, ENTRY))

    def test_needs_japanese_name(self):
        self.assertEqual(("[[活死人乐队]]", 0), ft.relink_entry("[[活死人乐队]]", "[[活死人乐队]]"))

    def test_links_item_replaced(self):
        body = "{{links|ロンサムガール|卡通女孩{{!}}カートゥーンガール}}"
        new, count = ft.relink_links_item(body, "Lonesome Girl", "ロンサムガール")
        self.assertEqual(1, count)
        self.assertEqual("{{links|Lonesome Girl{{!}}ロンサムガール|卡通女孩{{!}}カートゥーンガール}}", new)

    def test_links_item_inside_lj_not_touched(self):
        body = "{{links|Dec.{{!}}{{lj|チャンピオン}}}}"
        self.assertEqual((body, 0), ft.relink_links_item(body, "Dec.", "チャンピオン"))

    def test_collection_entry_is_relinked(self):
        new, detail = ft.add_collection_entry(COLLECTION_TEMPLATE_OLD_LINK, "TOP100", 5, ENTRY)
        self.assertEqual("已把「TOP100 → 1-10位」里的「リビングデッドバンデッド」改指到「活死人乐队」", detail)
        self.assertIn("{{lj|[[活死人乐队|リビングデッドバンデッド]]}}", new)
        self.assertNotIn("{{lj|[[リビングデッドバンデッド]]}}", new)
        self.assertTrue(ft._balanced(new))

    def test_collection_relink_is_idempotent(self):
        once, _ = ft.add_collection_entry(COLLECTION_TEMPLATE_OLD_LINK, "TOP100", 5, ENTRY)
        twice, detail = ft.add_collection_entry(once, "TOP100", 5, ENTRY)
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_non_honor_entry_is_relinked(self):
        new, [detail] = ft.add_non_honor(NON_HONOR_TEMPLATE_OLD_LINK, ENTRY)
        self.assertIn("改指到「活死人乐队」", detail)
        self.assertIn("[[嘴唇核子弹|{{lj|くちびる核爆弾}}]] • {{lj|[[活死人乐队|リビングデッドバンデッド]]}}", new)

    def test_producer_links_item_is_relinked(self):
        text = """{{Navbox
|group1 = 原创投稿曲目
|list1  = {{Navbox subgroup
  |group1 = 2024年
  |list1  = {{links|ロンサムガール|{{lj|チャンピオン}}}}
}}
}}"""
        new, detail = ft.add_producer_entry(text, 2024, "Lonesome Girl", "ロンサムガール")
        self.assertIn("改指到「Lonesome Girl」", detail)
        self.assertIn("{{links|Lonesome Girl{{!}}ロンサムガール|{{lj|チャンピオン}}}}", new)
        self.assertNotIn("|ロンサムガール|", new)


class ProducerTemplateTest(TestCase):
    """P主模板：条目写进「投稿年份」那一格（结构照 voca.wiki 的实测模板）。"""

    def test_year_group_label(self):
        self.assertEqual(2013, ft.producer_year("2013年"))
        self.assertEqual(2020, ft.producer_year("2020"))
        self.assertIsNone(ft.producer_year("原创投稿曲目"))
        self.assertIsNone(ft.producer_year("专辑"))

    def test_adds_into_year_group_inside_original_section(self):
        new, detail = ft.add_producer_entry(PRODUCER_TEMPLATE, 2026, "活死人乐队",
                                            "リビングデッドバンデッド")
        self.assertEqual("已加入「原创&合作 歌声合成曲目 → 2026年」", detail)
        self.assertIn("[[发发发发现象|ファファファ現象]] • " + ENTRY, new)      # 沿用 ` • `
        self.assertTrue(ft._balanced(new))

    def test_adds_into_links_list(self):
        new, detail = ft.add_producer_entry(LINKS_TEMPLATE, 2024, "活死人乐队",
                                            "リビングデッドバンデッド")
        self.assertEqual("已加入「原创投稿曲目 → 2024年」", detail)
        # `{{links}}` 里写「页面名{{!}}{{lj|日文名}}」，与邻居一致
        self.assertIn("Lonesome Girl{{!}}{{lj|ロンサムガール}}|"
                      "活死人乐队{{!}}{{lj|リビングデッドバンデッド}}", new)
        self.assertTrue(ft._balanced(new))

    def test_year_label_without_nian(self):
        new, detail = ft.add_producer_entry(LINKS_IN_LJ_TEMPLATE, 2025, "活死人乐队",
                                            "リビングデッドバンデッド")
        self.assertEqual("已加入「原创 歌声合成曲目 → 2025」", detail)
        self.assertIn("卡通女孩{{!}}カートゥーンガール|活死人乐队{{!}}リビングデッドバンデッド",
                      new)

    def test_appends_inside_nested_hlist(self):
        new, _ = ft.add_producer_entry(HLIST_IN_LJ_TEMPLATE, 2009, "活死人乐队",
                                       "リビングデッドバンデッド")
        self.assertIn("|[[红雨|赤い雨]]\n        |" + ENTRY + "\n    }}}}", new)
        self.assertTrue(ft._balanced(new))

    def test_missing_year_group(self):
        new, detail = ft.add_producer_entry(PRODUCER_TEMPLATE, 2019, "A")
        self.assertEqual(PRODUCER_TEMPLATE, new)
        self.assertIn("没有 2019 年", detail)
        new, detail = ft.add_producer_entry(NO_YEAR_TEMPLATE, 2024, "A")
        self.assertEqual(NO_YEAR_TEMPLATE, new)
        self.assertIn("没有 2024 年", detail)
        self.assertIn("取不到投稿年份", ft.add_producer_entry(PRODUCER_TEMPLATE, None, "A")[1])

    def test_idempotent_for_links_and_plain_lists(self):
        once, _ = ft.add_producer_entry(LINKS_TEMPLATE, 2024, "活死人乐队",
                                        "リビングデッドバンデッド")
        twice, detail = ft.add_producer_entry(once, 2024, "活死人乐队",
                                              "リビングデッドバンデッド")
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

        once, _ = ft.add_producer_entry(PRODUCER_TEMPLATE, 2026, "活死人乐队",
                                        "リビングデッドバンデッド")
        twice, detail = ft.add_producer_entry(once, 2026, "活死人乐队",
                                              "リビングデッドバンデッド")
        self.assertEqual(once, twice)
        self.assertIn("已有该条目", detail)

    def test_entry_without_japanese_name(self):
        new, _ = ft.add_producer_entry(PRODUCER_TEMPLATE, 2026, "Melt")
        self.assertIn("ファファファ現象]] • [[Melt]]}}", new)
        new, _ = ft.add_producer_entry(LINKS_TEMPLATE, 2024, "Melt")
        self.assertIn("ロンサムガール}}|Melt}}", new)

    def test_plan_and_sync(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=PRODUCER_TEMPLATE):
            lines = ft.plan(FamilySync(producers=["Chinozo"], year=2026), "活死人乐队")
        self.assertEqual(1, len(lines))
        self.assertIn("Template:Chinozo", lines[0])

        with mock.patch("utils.family_template.fetch_template_text", return_value=LINKS_TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync(FamilySync(producers=["Dixie Flatline"], year=2024),
                            "活死人乐队", "リビングデッドバンデッド")
        self.assertEqual("Template:Dixie Flatline", edit.call_args.args[0])
        self.assertIn(ENTRY_LINKS, edit.call_args.args[1])
        self.assertIn("2024年", lines[0])

    def test_available_with_only_producers(self):
        self.assertTrue(FamilySync(producers=["Chinozo"]).available)
        self.assertFalse(FamilySync().available)


class TemplateRedirectTest(TestCase):
    """模板页本身是重定向时（实测：Template:柊キライ → Template:Hiiragi Kirai）。"""

    def setUp(self):
        ft._template_cache.clear()
        ft._redirect_cache.clear()

    tearDown = setUp

    def _mock(self):
        texts = {"Template:柊キライ": "#重定向 [[Template:Hiiragi Kirai]]",
                 "Template:Hiiragi Kirai": PRODUCER_TEMPLATE}
        return mock.patch.object(ft, "_fetch_template_raw", side_effect=lambda t: texts.get(t))

    def test_fetch_follows_redirect(self):
        with self._mock():
            self.assertEqual(PRODUCER_TEMPLATE, ft.fetch_template_text("柊キライ"))

    def test_resolve_title_for_write_back(self):
        with self._mock():
            self.assertEqual("Template:Hiiragi Kirai", ft.resolve_template_title("柊キライ"))

    def test_sync_writes_to_target_page(self):
        with self._mock(), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            ft.sync_producer("柊キライ", 2026, "活死人乐队", "リビングデッドバンデッド")
        self.assertEqual("Template:Hiiragi Kirai", edit.call_args.args[0])
        self.assertIn(ENTRY, edit.call_args.args[1])


class FamilySyncPlanTest(TestCase):
    def test_plan_lists_each_step(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE):
            lines = ft.plan(FamilySync(templates=["可不/2024"], honors=[("bilibili", 1_200_000)]),
                            "活死人乐队", "リビングデッドバンデッド")
        self.assertEqual(2, len(lines))
        self.assertTrue(all("Template:可不/2024" in line for line in lines))

    def test_plan_without_honors_targets_non_honor_section(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=NON_HONOR_TEMPLATE):
            lines = ft.plan(FamilySync(templates=["歌爱雪"], honors=[], year=2024), "活死人乐队")
        self.assertEqual(1, len(lines))
        self.assertIn("未达殿堂", lines[0])
        self.assertIn("部分非殿堂曲", lines[0])

    def test_plan_covers_collection_template(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=COLLECTION_TEMPLATE):
            lines = ft.plan(FamilySync(collections=[CollectionSync("The VOCALOID Collection2024冬",
                                                                  "TOP100", 5)]), "活死人乐队")
        self.assertEqual(1, len(lines))
        self.assertIn("1-10位", lines[0])

    def test_plan_reports_missing_template(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=None):
            lines = ft.plan(FamilySync(templates=["X/2024"], honors=[("bilibili", 200_000)]), "A")
        self.assertIn("模板不存在或读取失败", lines[0])

    def test_available_flag(self):
        self.assertFalse(FamilySync().available)
        self.assertTrue(FamilySync(templates=["X"]).available)         # 未达殿堂时会写非殿堂曲
        self.assertTrue(FamilySync(collections=[CollectionSync("TVC2024冬")]).available)


class SyncTemplateTest(TestCase):
    def test_writes_back_when_changed(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync_template("可不/2024", [("bilibili", 1_200_000)], "活死人乐队",
                                     "リビングデッドバンデッド")
        self.assertEqual(2, len(lines))
        self.assertEqual("Template:可不/2024", edit.call_args.args[0])
        written = edit.call_args.args[1]
        self.assertIn(ENTRY, written)
        self.assertTrue(ft._balanced(written))

    def test_no_edit_when_nothing_changed(self):
        """模板里既没有「非殿堂曲」也没有「其他」时不写回，只给说明。"""
        nowhere = "{{Navbox\n|group1 = 殿堂曲\n|list1 = [[A]]\n}}"
        with mock.patch("utils.family_template.fetch_template_text", return_value=nowhere), \
             mock.patch("utils.family_template.wiki_api.edit_page") as edit:
            lines = ft.sync_template("可不/2024", [("bilibili", 50_000)], "活死人乐队")
        edit.assert_not_called()
        self.assertTrue(lines)                              # 仍给出说明

    def test_song_on_two_sites_is_written_only_into_the_honor_sublist(self):
        """多站到殿堂：荣誉小节里写一处就够，不再多写「其它」。

        实测 Template:可不/2023（《做吧! 新鲜的小年轻》niconico + YouTube 双殿堂）：
        旧实现按站点兜底，把这一条又写进了「其它」（diff 251213），用户手动去掉了那一行（251215）。
        """
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync_template("可不/2024", [("niconico", 500_000), ("YouTube", 731_918)],
                                     "活死人乐队", "リビングデッドバンデッド",
                                     posted=ft.PostedAt(datetime(2024, 2, 22, 23, 0), "niconico"))
        written = edit.call_args.args[1]
        self.assertIn("02-22 23:00 -->" + ENTRY, written.split("|group3")[0])
        self.assertEqual(TEMPLATE.split("|group3")[1], written.split("|group3")[1],
                         "「其它」那一组不该被动过")
        self.assertTrue(any("没有 YouTube" in line for line in lines), lines)

    def test_sync_below_threshold_writes_other_group(self):
        """未达殿堂、模板又只有「其它」时，写进「其它」（实测 Template:可不/2024）。"""
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync_template("可不/2024", [("bilibili", 50_000)], "活死人乐队",
                                     "リビングデッドバンデッド")
        written = edit.call_args.args[1]
        self.assertIn(ENTRY, written)
        self.assertTrue(ft._balanced(written))
        self.assertIn("其他", lines[0])

    def test_sync_without_honors_writes_non_honor_section(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=NON_HONOR_TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync_template("歌爱雪", [], "活死人乐队", "リビングデッドバンデッド")
        self.assertIn(ENTRY_LJ_IN, edit.call_args.args[1])
        self.assertIn("部分非殿堂曲", lines[0])

    def test_sync_collection_template(self):
        with mock.patch("utils.family_template.fetch_template_text",
                        return_value=COLLECTION_TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": True}) as edit:
            lines = ft.sync(FamilySync(collections=[CollectionSync("The VOCALOID Collection2024冬",
                                                                   "TOP100", 15)]),
                            "活死人乐队", "リビングデッドバンデッド")
        self.assertEqual("Template:The VOCALOID Collection2024冬", edit.call_args.args[0])
        self.assertIn(ENTRY, edit.call_args.args[1])
        self.assertIn("11-20位", lines[0])

    def test_aborts_on_unbalanced_result(self):
        # 故意让插入结果不配平：模拟坏结构
        broken = "{{Navbox\n|group1 = 殿堂曲\n|list1 = {{Navbox subgroup\n|group1 = bilibili\n|list1 = {{lj|[[A]]}}\n}"
        with mock.patch("utils.family_template.fetch_template_text", return_value=broken), \
             mock.patch("utils.family_template.wiki_api.edit_page") as edit:
            lines = ft.sync_template("X/2024", [("bilibili", 200_000)], "B")
        edit.assert_not_called()
        self.assertTrue(lines)

    def test_reports_write_failure(self):
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page",
                        return_value={"ok": False, "error": "permission denied"}):
            lines = ft.sync_template("可不/2024", [("bilibili", 200_000)], "活死人乐队")
        self.assertIn("写回失败", lines[-1])

    def test_sync_swallows_exceptions_per_template(self):
        with mock.patch("utils.family_template.sync_template", side_effect=RuntimeError("boom")):
            lines = ft.sync(FamilySync(templates=["A/2024", "B/2024"], honors=[("bilibili", 200_000)]), "X")
        self.assertEqual(2, len(lines))
        self.assertIn("同步失败", lines[0])
