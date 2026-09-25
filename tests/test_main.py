"""main.py 的引擎识别与分类输出测试（不联网）。

背景：同一个歌姬可能同时挂在多个引擎的角色表里（例：可不 在 CeVIO / Synthesizer V /
VoiSona 三张表里都有），旧实现会把三个引擎全写进简介，且在有专属歌手模板时还会整段
丢掉引擎分类。
"""
from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

import main
from models.video import VideoSite


def _video(site=VideoSite.NICO_NICO, year=2024, month=2, day=22, canonical=True):
    return SimpleNamespace(site=site, canonical=canonical, uploaded=date(year, month, day),
                           identifier="sm1", views=0)


def _song(vocalists=("可不",), name_jap="リビングデッドパンデッド", name_chs="活死人乐队",
          videos=None):
    return SimpleNamespace(
        name_jap=name_jap, name_chs=name_chs,
        videos=list(videos) if videos else [],
        albums=[],
        creators=SimpleNamespace(
            vocalists_str=lambda: list(vocalists),
            vocalists=[SimpleNamespace(name=n) for n in vocalists],
            producers_str=lambda: ["すぷいちゃん"],
            producers=[SimpleNamespace(name="すぷいちゃん")],
            staff_list=lambda: [],
        ),
        vocaloid_collection=None, vocaloid_collection_rank=None,
        vocaloid_collection_track=None,
        image=SimpleNamespace(creators=None, file_name="x.jpg", source_url=""),
        colors=None, color_editing=None,
    )


class EngineDetectionTest(TestCase):
    def test_kafu_counts_as_cevio_only(self):
        # 可不 同时在 CeVIO / Synthesizer V / VoiSona 三张表里，优先级最高的是 CeVIO
        self.assertEqual(["CeVIO"], main.get_song_engines(_song(["可不"])))

    def test_same_engine_is_deduped(self):
        self.assertEqual(["CeVIO"], main.get_song_engines(_song(["可不", "星界"])))

    def test_unknown_vocalist_falls_back_to_vocaloid(self):
        self.assertEqual(["VOCALOID"], main.get_song_engines(_song(["初音ミク"])))

    def test_engines_keep_vocalist_order(self):
        self.assertEqual(["VOCALOID", "CeVIO"],
                         main.get_song_engines(_song(["初音ミク", "可不"])))

    def test_no_vocalist(self):
        self.assertEqual([], main.get_song_engines(_song([])))
        self.assertEqual(["VOCALOID"], main.get_song_categories(_song([])))   # 简介沿用旧兜底

    def test_teto_defaults_to_utau(self):
        # vocadb 里未标注 SV 的重音テト 就是 UTAU
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音テト"])))

    def test_teto_sv_is_synthesizer_v(self):
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["重音テトSV"])))

    def test_teto_chinese_name_is_utau(self):
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音Teto"])))


class EngineCategoryTest(TestCase):
    def _end(self, song, producer_template=False, producer_info=None, collapse_navbox=False):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(producer_template=producer_template,
                                                      collapse_navbox=collapse_navbox))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main, "get_producer_info",
                               mock.AsyncMock(return_value=list(producer_info or []))):
            return main.create_end(song)

    def test_cevio_category_is_emitted(self):
        end = self._end(_song(["可不"]))
        self.assertIn("[[分类:使用CeVIO的歌曲]]", end)
        self.assertNotIn("Synthesizer V", end)
        self.assertNotIn("VoiSona", end)

    def test_engine_category_kept_next_to_vocalist_template(self):
        # 有专属模板（{{初音未来}}→ 由 vocalist_cat 覆盖）时引擎分类过去会被整段丢掉
        end = self._end(_song(["初音ミク"], name_jap="メルト", name_chs="Melt"))
        self.assertIn("[[分类:使用VOCALOID的歌曲]]", end)
        self.assertIn("[[分类:初音未来歌曲]]", end)

    def test_kafu_template_carries_vocalist_category(self):
        # 可不/2024 模板自带「可不歌曲」分类，所以这里不再重复写
        end = self._end(_song(["可不"], videos=[_video()]))
        self.assertIn("{{可不/2024}}", end)
        self.assertNotIn("[[分类:可不歌曲]]", end)

    def test_singer_template_ignores_producer_switch(self):
        # producer_template 只管要不要联网找 P主大家族模板，歌手模板照旧输出
        with_producer = self._end(_song(["可不"]), producer_template=True, producer_info=["Producer"])
        without = self._end(_song(["可不"]), producer_template=False)
        self.assertIn("{{Producer}}", with_producer)
        self.assertNotIn("{{Producer}}", without)
        self.assertIn("{{可不}}", with_producer)
        self.assertIn("{{可不}}", without)

    def test_teto_utau_vs_sv(self):
        utau = self._end(_song(["重音テト"], videos=[_video()]))
        sv = self._end(_song(["重音テトSV"], videos=[_video()]))
        self.assertIn("[[分类:使用UTAU的歌曲]]", utau)
        self.assertIn("{{重音Teto/2024}}", utau)
        self.assertIn("[[分类:使用Synthesizer V的歌曲]]", sv)
        self.assertIn("{{重音Teto/2024}}", sv)          # 两种声库共用同一个歌手模板
        self.assertNotIn("UTAU", sv)

    def test_collapse_navbox_switch(self):
        # 开关打开时，默认展开的模板会被补上 |collapsed
        with mock.patch.object(main, "collapse_all", side_effect=lambda names: [f"{n}|collapsed" for n in names]) as collapse:
            end = self._end(_song(["可不"]), collapse_navbox=True)
        collapse.assert_called_once_with(["可不"])
        self.assertIn("{{可不|collapsed}}\n", end)

    def test_collapse_navbox_off_keeps_templates(self):
        with mock.patch.object(main, "collapse_all") as collapse:
            end = self._end(_song(["可不"]), collapse_navbox=False)
        collapse.assert_not_called()
        self.assertIn("{{可不}}\n", end)


class SingerTemplateTest(TestCase):
    """歌手大家族模板表（对照 voca.wiki 的 Category:虚拟歌手模板 逐条核对）。"""

    def _end(self, vocalists):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(producer_template=False,
                                                       collapse_navbox=False))
        with mock.patch.object(main, "get_config", return_value=cfg):
            return main.create_end(_song(vocalists, videos=[_video(year=2024)]))

    def test_template_per_singer(self):
        cases = {
            "可不": "{{可不/2024}}",            # 按年份分页
            "KAFU": "{{可不/2024}}",            # 同一个声库的别名
            "重音テト": "{{重音Teto/2024}}",
            "重音テトSV": "{{重音Teto/2024}}",
            "Ci flower": "{{Flower/2024}}",
            "KAITO": "{{KAITO/2024}}",
            "歌愛ユキ": "{{歌爱雪}}",
            "洛天依": "{{洛天依}}",
            "心華": "{{心华}}",
            "SeKai": "{{星界}}",
            "ずんだもん": "{{俊达萌}}",
            "ナースロボ＿タイプＴ": "{{NurseRobot TypeT}}",
            "鳴花ヒメ": "{{鸣花姬·尊}}",
            "裏命": "{{里命}}",
            "さとうささら": "{{佐藤莎莎拉}}",
            "夏語遙": "{{夏语遥}}",
            "双葉湊音": "{{双叶凑音}}",
            "琴葉茜・葵": "{{琴叶茜·葵}}",
            "猫村いろは": "{{猫村伊吕波}}",
            "結月ゆかり": "{{结月缘}}",
            "시유": "{{SeeU}}",
            "イア": "{{IA}}",
            "ROSE": "{{梦的结唱}}",
            "Eleanor Forte": "{{爱莲娜·芙缇}}",
        }
        for singer, template in cases.items():
            with self.subTest(singer=singer):
                self.assertIn(template, self._end([singer]))

    def test_miku_and_miku_chinese_are_excluded(self):
        # 这两个模板不收录：条目里按需手写，分类照旧由 vocalist_cat 补
        for singer in ("初音ミク", "初音未来", "初音未来(中文)", "雪未来"):
            with self.subTest(singer=singer):
                end = self._end([singer])
                self.assertNotIn("{{初音未来", end)
                self.assertNotIn("{{雪未来", end)
                self.assertIn("[[分类:", end)

    def test_template_without_category_keeps_manual_category(self):
        # 梦的结唱 / 鸣花姬·尊 是纯导航框（或要传参才加分类），「XX歌曲」分类由 vocalist_cat 补
        end = self._end(["ROSE"])
        self.assertIn("{{梦的结唱}}", end)
        self.assertIn("[[分类:ROSE歌曲]]", end)
        end = self._end(["鳴花ヒメ"])
        self.assertIn("{{鸣花姬·尊}}", end)
        self.assertIn("[[分类:鸣花姬歌曲]]", end)

    def test_zuundamon_project_template_is_excluded(self):
        # 项目导航框不随条目输出，这些歌手只写分类
        for singer, category in (("東北きりたん", "[[分类:东北切蒲英歌曲]]"),
                                 ("東北ずん子", "[[分类:东北俊子歌曲]]"),
                                 ("東北イタコ", "[[分类:东北伊达子歌曲]]")):
            with self.subTest(singer=singer):
                end = self._end([singer])
                self.assertNotIn("东北俊子·俊达萌项目", end)
                self.assertIn(category, end)
        # 俊达萌 自己那个模板是保留的
        self.assertIn("{{俊达萌}}", self._end(["ずんだもん"]))

    def test_self_categorizing_template_does_not_duplicate_category(self):
        end = self._end(["洛天依"])
        self.assertIn("{{洛天依}}", end)
        self.assertNotIn("[[分类:洛天依歌曲]]", end)

    def test_unknown_suffix_in_name_is_ignored(self):
        # vocadb 里有「小春六花 (Unknown)」这种声库名
        self.assertIn("{{小春六花}}", self._end(["小春六花 (Unknown)"]))

    def test_templates_are_deduped(self):
        self.assertEqual(["可不/2024"], main.get_vocaloid_templates(["可不", "KAFU"], 2024))
        self.assertEqual(["可不"], main.get_vocaloid_templates(["可不"], None))


class ProducerTemplateWiringTest(TestCase):
    """P主模板：注释区输出与提交窗口同步用的是同一份清单（只联网算一次）。"""

    def _end(self, producer_templates, switch=True, fetched=("Chinozo",)):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(producer_template=switch,
                                                       collapse_navbox=False))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main, "get_producer_info",
                               mock.AsyncMock(return_value=list(fetched))) as info:
            end = main.create_end(_song(["可不"], videos=[_video(year=2024)]),
                                  producer_templates)
        return end, info

    def test_given_producer_templates_are_used_without_searching(self):
        end, info = self._end(["Chinozo"])
        self.assertIn("{{Chinozo}}", end)
        info.assert_not_called()

    def test_computes_when_not_given(self):
        end, info = self._end(None)
        self.assertIn("{{Chinozo}}", end)
        info.assert_called_once()

    def test_switch_off_skips_producer_templates(self):
        end, info = self._end(None, switch=False)
        self.assertNotIn("{{Chinozo}}", end)
        info.assert_not_called()

    def test_family_sync_carries_producers(self):
        song = _song(["可不"], videos=[_video(year=2024)])
        family = main.build_family_sync(song, ["Chinozo"])
        self.assertEqual(["Chinozo"], family.producers)
        self.assertEqual(2024, family.year)
        self.assertTrue(family.available)
        self.assertEqual([], main.build_family_sync(song).producers)


class HonorSyncTest(TestCase):
    """提交窗口「同步修改大家族模板」用到的荣誉与模板清单。"""

    def test_honors_only_include_hall_of_fame(self):
        song = _song(["可不"], videos=[
            _video(site=VideoSite.NICO_NICO, year=2024),        # views 默认 0 -> 不算
            _video(site=VideoSite.YOUTUBE),
        ])
        song.videos[1].views = 1_500_000
        self.assertEqual([("YouTube", 1_500_000)], main.get_song_honors(song))

    def test_honors_ignore_non_canonical(self):
        song = _song(["可不"], videos=[_video(canonical=False)])
        song.videos[0].views = 5_000_000
        self.assertEqual([], main.get_song_honors(song))

    def test_build_family_sync_uses_singer_template_and_year(self):
        family = main.build_family_sync(_song(["可不"], videos=[_video(year=2024)]))
        self.assertEqual(["可不/2024"], family.templates)         # 模板带投稿年份
        self.assertEqual([], family.honors)                       # 0 播放不算殿堂
        self.assertEqual(2024, family.year)
        # 未达殿堂时仍可同步（会写进「部分非殿堂曲」）
        self.assertTrue(family.available)

    def test_family_sync_available_only_with_both(self):
        song = _song(["可不"], videos=[_video(year=2024)])
        song.videos[0].views = 200_000
        family = main.build_family_sync(song)
        self.assertTrue(family.available)
        self.assertEqual([("niconico", 200_000)], family.honors)

    def test_collection_track_and_rank(self):
        song = _song(["可不"])
        song.vocaloid_collection = "ボカコレ2024冬"
        song.vocaloid_collection_track = "TOP100"
        song.vocaloid_collection_rank = "15"
        item = main.get_collection_sync(song)
        self.assertEqual("The VOCALOID Collection2024冬", item.template)
        self.assertEqual("TOP100", item.track)
        self.assertEqual(15, item.rank)
        self.assertTrue(item.ranked)

    def test_collection_unranked_goes_to_unranked_list(self):
        song = _song(["可不"])
        song.vocaloid_collection = "ボカコレ2024冬"
        song.vocaloid_collection_track = "榜外"
        item = main.get_collection_sync(song)
        self.assertEqual("The VOCALOID Collection2024冬", item.template)
        self.assertFalse(item.ranked)

    def test_collection_rank_without_track_defaults_to_top100(self):
        song = _song(["可不"])
        song.vocaloid_collection = "ボカコレ2023春"
        song.vocaloid_collection_rank = "42"
        self.assertEqual("TOP100", main.get_collection_sync(song).track)

    def test_no_collection(self):
        self.assertIsNone(main.get_collection_sync(_song(["可不"])))

    def test_collection_template_name_matches_article(self):
        song = _song(["可不"])
        song.vocaloid_collection = "ボカコレ2024冬"
        song.vocaloid_collection_track = "TOP100"
        song.vocaloid_collection_rank = "15"
        with mock.patch.object(main, "get_config", return_value=SimpleNamespace(
                wikitext=SimpleNamespace(producer_template=False, collapse_navbox=False))), \
             mock.patch.object(main, "get_producer_info", mock.AsyncMock(return_value=[])):
            end = main.create_end(song)
        self.assertIn("{{The VOCALOID Collection2024冬}}", end)
        self.assertEqual("The VOCALOID Collection2024冬",
                         main.build_family_sync(song).collections[0].template)


class IntroEngineTest(TestCase):
    def test_intro_names_only_cevio(self):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(producer_template=False))
        with mock.patch.object(main, "get_config", return_value=cfg):
            intro = main.create_intro(_song(["可不"]))
        self.assertIn("的[[CeVIO]]日语原创歌曲", intro)
        self.assertNotIn("Synthesizer V", intro)
        self.assertNotIn("VoiSona", intro)

    def test_intro_lists_engines_in_order(self):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(producer_template=False))
        with mock.patch.object(main, "get_config", return_value=cfg):
            intro = main.create_intro(_song(["初音ミク", "可不"]))
        self.assertIn("的[[VOCALOID]]及[[CeVIO]]日语原创歌曲", intro)
