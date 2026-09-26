"""utils/family_template.py 的单元测试。

覆盖三块：
1. 导航框默认展开 / 折叠的判断与 `|collapsed` 追加（原 utils/navbox.py，已合并进本模块）；
2. 把条目插入歌手大家族模板的荣誉小节（殿堂曲 / 传说曲 / 神话曲…）；
3. 未达殿堂时插入「部分非殿堂曲」，以及插入 The VOCALOID Collection 的榜单段落。

模板片段取自 voca.wiki 的 Template:可不/2024、Template:歌爱雪、Template:诗岸、
Template:The VOCALOID Collection2024冬（结构相同，条目列表做了删减）。HTTP 全部 mock，不联网。
"""
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

    def test_no_non_honor_group_keeps_text(self):
        _, [detail] = ft.add_non_honor(TEMPLATE, ENTRY)
        self.assertIn("模板里没有「部分非殿堂曲」分组", detail)

    def test_add_honors_falls_back_when_below_threshold(self):
        new, details = ft.add_honors(NON_HONOR_TEMPLATE, "bilibili", 99_999, ENTRY)
        self.assertEqual(1, len(details))
        self.assertIn(ENTRY_LJ_IN, new.split("|group2")[1])


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

    def test_ranked_flag(self):
        self.assertTrue(CollectionSync("X", "TOP100", 3).ranked)
        self.assertFalse(CollectionSync("X", "榜外", None).ranked)
        self.assertFalse(CollectionSync("X", "TOP100", None).ranked)


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
        with mock.patch("utils.family_template.fetch_template_text", return_value=TEMPLATE), \
             mock.patch("utils.family_template.wiki_api.edit_page") as edit:
            lines = ft.sync_template("可不/2024", [("bilibili", 50_000)], "活死人乐队")
        edit.assert_not_called()
        self.assertTrue(lines)                              # 仍给出「没有非殿堂曲分组」说明

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
