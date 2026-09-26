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
from models.song import Lyrics
from models.video import HumanOriginal, VideoSite, video_link
from utils import disambig, lyrics_colors


def _video(site=VideoSite.NICO_NICO, year=2024, month=2, day=22, canonical=True,
           deleted=False, identifier="sm1", views=0):
    return SimpleNamespace(site=site, canonical=canonical, uploaded=date(year, month, day),
                           identifier=identifier, views=views, deleted=deleted)


def _song(vocalists=("可不",), name_jap="リビングデッドパンデッド", name_chs="活死人乐队",
          videos=None, human_original=None, staffs=()):
    return SimpleNamespace(
        name_jap=name_jap, name_chs=name_chs, name_other=[],
        videos=list(videos) if videos else [],
        albums=[],
        human_original=human_original,
        creators=SimpleNamespace(
            vocalists_str=lambda: list(vocalists),
            vocalists=[SimpleNamespace(name=n) for n in vocalists],
            producers_str=lambda: ["すぷいちゃん"],
            producers=[SimpleNamespace(name="すぷいちゃん")],
            staff_list=lambda: [(role, list(people)) for role, people in staffs],
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


class HonorHeaderTest(TestCase):
    """{{虚拟歌手歌曲荣誉题头}}：达到殿堂（≥10 万播放）的站点必须写进去。"""

    def test_youtube_honor_writes_header(self):
        song = _song(["可不"], videos=[_video(site=VideoSite.YOUTUBE, month=2, day=23)])
        song.videos[0].views = 287245
        header = main.create_header(song)
        self.assertIn("{{虚拟歌手歌曲荣誉题头|CeVIO|yrank=1}}", header)

    def test_below_hall_of_fame_has_no_header(self):
        # 点赞数 6768 曾被误当成播放量，导致这一档的殿堂曲没有荣誉题头
        song = _song(["可不"], videos=[_video(site=VideoSite.YOUTUBE, month=2, day=23)])
        song.videos[0].views = 92417
        self.assertNotIn("荣誉题头", main.create_header(song))

    def test_multiple_sites_are_listed(self):
        song = _song(["可不"], videos=[_video(site=VideoSite.NICO_NICO),
                                       _video(site=VideoSite.YOUTUBE)])
        song.videos[0].views = 1_200_000          # 传说曲
        song.videos[1].views = 287245
        self.assertIn("{{虚拟歌手歌曲荣誉题头|CeVIO|nrank=2|yrank=1}}", main.create_header(song))


class LyricsColorsTest(TestCase):
    """{{LyricsKai/colors}}：按演唱者上色（颜色取 voca.wiki 颜色表，charas 取本曲歌姬）。"""

    TABLE = {"宮舞モカ": "#72A6C0", "RYO": "#EB6238"}

    def _song(self, vocalists=("宮舞モカ", "Ryo"), marks=None, **kw):
        song = _song(list(vocalists))
        song.lyrics = Lyrics(lyrics_jap="あ\n\nい", lyrics_chs="啊\n\n咦",
                             use_colors=True, chara_marks=marks, **kw)
        return song

    def _render(self, song):
        with mock.patch.object(lyrics_colors, "fetch_colors", return_value=self.TABLE):
            return main.create_lyrics(song)

    def test_template_and_params(self):
        out = self._render(self._song(marks={"0": ["宮舞モカ"], "2": ["宮舞モカ", "Ryo"]}))
        self.assertIn("{{LyricsKai/colors\n", out)
        self.assertIn("|colors= #72A6C0; #EB6238; co(#72A6C0, #EB6238)\n", out)
        self.assertIn("|charas= 宮舞モカ；Ryo；合唱(@nolink)\n", out)
        self.assertIn("|traColors= on\n", out)
        self.assertIn("|charaBlock= on\n", out)

    def test_marks_both_columns(self):
        out = self._render(self._song(marks={"0": ["宮舞モカ"], "2": ["Ryo"]}))
        self.assertIn("@1あ\n\n@2い", out)          # 原词
        self.assertIn("@1啊\n\n@2咦", out)          # 翻译（traColors）

    def test_inline_segments_in_both_columns(self):
        song = self._song(marks={"0": [["宮舞モカ"], ["Ryo"]]},
                          chara_splits={"0": {"jap": [2], "chs": [2]}})
        song.lyrics.lyrics_jap = "未来は誰も知らない"
        song.lyrics.lyrics_chs = "未來無人知曉"
        out = self._render(song)
        self.assertIn("@1未来@2は誰も知らない", out)
        self.assertIn("@1未來@2無人知曉", out)

    def test_column_without_cuts_uses_first_color(self):
        song = self._song(marks={"0": [["宮舞モカ"], ["Ryo"]]},
                          chara_splits={"0": {"jap": [2]}})
        song.lyrics.lyrics_jap = "未来は誰も知らない"
        song.lyrics.lyrics_chs = "未來無人知曉"
        out = self._render(song)
        self.assertIn("@1未来@2は誰も知らない", out)
        self.assertIn("@1未來無人知曉", out)          # 中文栏没切分 → 整行用第一段的颜色

    def test_combines_with_hover(self):
        out = self._render(self._song(marks={"0": ["宮舞モカ"], "1": ["Ryo"]}, use_hover=True))
        self.assertIn("{{LyricsKai/colors/hover\n", out)
        self.assertIn("@1あ\n#NoHover\nい", out)     # 空行补 #NoHover 且不带标记

    def test_keeps_roma_variant(self):
        song = self._song(marks={"0": ["宮舞モカ"]})
        song.lyrics.lyrics_roma = "a"
        out = self._render(song)
        self.assertIn("{{LyricsKai/colors/Roma\n", out)
        self.assertIn("|photrans=a", out)

    def test_without_singers_falls_back(self):
        out = self._render(self._song(vocalists=(), marks={"0": ["A"]}))
        self.assertNotIn("/colors", out)
        self.assertNotIn("颜色", out)

    def test_switch_off_keeps_plain_lyrics(self):
        song = _song(["宮舞モカ"])
        song.lyrics = Lyrics(lyrics_jap="あ", lyrics_chs="啊")
        out = self._render(song)
        self.assertIn("{{LyricsKai\n", out)
        self.assertNotIn("@1", out)


class DisambigWiringTest(TestCase):
    """同名条目：条目名 / 封面文件名 / 顶部 {{About}}·{{Otheruseslist}}（不联网）。"""

    def _plan(self, mode=disambig.MODE_DISAMBIG, others=None, our_title="向日葵(Teary Planet)"):
        plan = disambig.Plan(base_title="向日葵", our_title=our_title, mode=mode,
                             others=others or [disambig.Entry("向日葵(Project Lumina)",
                                                             description="[[Project Lumina]]创作的歌曲")])
        plan.our_entry = disambig.Entry(our_title, description="[[Teary Planet]]创作的歌曲",
                                        line="* NEW")
        return plan

    def test_about_template_is_prepended_to_top(self):
        song = _song(["v flower"])
        song.disambig = self._plan()
        header = main.create_header(song)
        self.assertTrue(header.startswith(
            "{{About|[[Teary Planet]]创作的歌曲|[[Project Lumina]]创作的歌曲|向日葵(Project Lumina)}}\n"))

    def test_otheruseslist_goes_above_honor_header(self):
        song = _song(["v flower"])
        song.videos = [_video(year=2024, month=2, day=23)]
        song.videos[0].views = 287245
        song.disambig = self._plan(mode=disambig.MODE_DISAMBIG,
                                   others=[disambig.Entry("A", "[[Aira]]创作的歌曲"),
                                           disambig.Entry("B", "[[SmileR]]创作的歌曲")])
        header = main.create_header(song)
        self.assertTrue(header.startswith("{{Otheruseslist|"))
        self.assertLess(header.index("{{Otheruseslist|"), header.index("{{虚拟歌手歌曲荣誉题头"))

    def test_without_plan_no_template(self):
        self.assertNotIn("{{About", main.create_header(_song(["v flower"])))

    def test_cover_filename_uses_page_name(self):
        song = _song(["v flower"])
        song.page_name = "向日葵(Teary Planet)"
        self.assertEqual("向日葵(Teary Planet).jpg", main.get_cover_filename(song))

    def test_prepare_disambig_sets_page_name(self):
        song = _song(["v flower"])
        cfg = SimpleNamespace(wiki=SimpleNamespace(disambiguate=True))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main.disambig, "detect", return_value=self._plan()):
            main.prepare_disambig(song)
        self.assertEqual("向日葵(Teary Planet)", song.page_name)
        self.assertEqual("向日葵(Teary Planet)", song.disambig.our_entry.title)

    def test_prepare_disambig_respects_switch(self):
        song = _song(["v flower"])
        cfg = SimpleNamespace(wiki=SimpleNamespace(disambiguate=False))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main.disambig, "detect") as detect:
            main.prepare_disambig(song)
        detect.assert_not_called()
        self.assertIsNone(getattr(song, "page_name", None))


class DeletedVideoTest(TestCase):
    """非公開 / 删稿的视频（数据来自 nicolog）：Songbox 的投稿栏改用 {{VOCALOID Songbox/card}}。

    参考条目：杰西卡（sm16693848，作者隐退后视频被设为非公開）
        |投稿 =
        {{VOCALOID_Songbox/card|nnd|sm16693848|2012-01-14|count=1,081,660|class=deleted}}
    """

    def test_public_video_keeps_id_fields(self):
        song = _song(["初音ミク"], videos=[_video(views=287245)])
        header = main.create_header(song)
        self.assertIn("|nnd_id = sm1\n", header)
        self.assertIn("|nnd_date = 2024年2月22日\n", header)
        self.assertNotIn("|投稿", header)
        self.assertNotIn("card", header)

    def test_deleted_video_uses_card_with_playcount(self):
        song = _song(["初音ミク"], videos=[_video(deleted=True, identifier="sm16693848",
                                                  year=2012, month=1, day=14, views=1081622)])
        header = main.create_header(song)
        self.assertIn("|投稿 =\n{{VOCALOID_Songbox/card|nnd|sm16693848|2012年1月14日"
                      "|再生=1,081,622|class=deleted}}\n", header)
        self.assertNotIn("|nnd_id", header)
        self.assertNotIn("|nnd_date", header)
        self.assertTrue(header.endswith("}}\n"))

    def test_deleted_video_without_playcount(self):
        song = _song(["初音ミク"], videos=[_video(deleted=True, views=0)])
        self.assertIn("{{VOCALOID_Songbox/card|nnd|sm1|2024年2月22日|class=deleted}}",
                      main.create_header(song))

    def test_deleted_nico_lists_other_sites_as_cards(self):
        song = _song(["初音ミク"], videos=[_video(deleted=True, identifier="sm1", views=100),
                                           _video(VideoSite.BILIBILI, identifier="BV1", views=0),
                                           _video(VideoSite.YOUTUBE, identifier="yt1", views=0)])
        header = main.create_header(song)
        self.assertIn("|投稿 =\n", header)
        self.assertEqual(3, header.count("{{VOCALOID_Songbox/card|"))
        self.assertLess(header.index("|nnd|sm1"), header.index("|bb|BV1"))
        self.assertLess(header.index("|bb|BV1"), header.index("|yt|yt1"))
        self.assertEqual(1, header.count("class=deleted"))     # 只有非公開那个带 class

    def test_deleted_video_still_counts_for_honor_header(self):
        # nicolog 给的最后播放量要用进荣誉题头，否则殿堂曲会没有题头
        song = _song(["初音ミク"], videos=[_video(deleted=True, views=1081622)])
        self.assertIn("{{虚拟歌手歌曲荣誉题头|VOCALOID|nrank=2}}", main.create_header(song))

    def test_intro_uses_nicolog_date(self):
        song = _song(["初音ミク"], videos=[_video(deleted=True, year=2012, month=1, day=14)])
        self.assertIn("于2012年1月14日投稿至[[niconico]]", main.create_intro(song))


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


# 人声本家：同曲的人声演唱版本（参 voca.wiki 条目 如月车站、红色房间）
NICO_HUMAN = "sm27831783"
BB_HUMAN = "BV1Jv411N7Wn"
BB_MAIN = "BV1nv411N7mY"


def _human(video=NICO_HUMAN, bilibili=BB_HUMAN):
    return HumanOriginal(video=video_link(video) if video else None,
                         bilibili=video_link(bilibili) if bilibili else None)


def _wikitext_config(**kwargs):
    defaults = {"optimize_Introduction_color": False}
    defaults.update(kwargs)
    return SimpleNamespace(wikitext=SimpleNamespace(**defaults))


class HumanOriginalIntroTest(TestCase):
    """简介里追加一句「另有P主本人演唱的人声本家。」"""

    def test_sentence_is_added(self):
        intro = main.create_intro(_song(["初音ミク"], human_original=_human()))
        self.assertIn("演唱。\n\n另有P主本人演唱的人声本家。\n", intro)

    def test_no_sentence_without_human_original(self):
        intro = main.create_intro(_song(["初音ミク"]))
        self.assertNotIn("人声本家", intro)

    def test_empty_links_do_not_add_sentence(self):
        # 问了但两个链接都没填 → 当作没有人声本家，不写这一句
        self.assertEqual("", main.human_original_sentence(
            _song(["初音ミク"], human_original=HumanOriginal())))

    def test_sentence_comes_before_collection_and_albums(self):
        song = _song(["初音ミク"], human_original=_human())
        song.vocaloid_collection = "ボカコレ2021秋"
        song.vocaloid_collection_rank = "45"
        song.albums = ["RuLu"]
        intro = main.create_intro(song)
        self.assertLess(intro.index("人声本家"), intro.index("本曲参与了"))

    def test_disabled_config_never_asks(self):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(human_original=False))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main, "get_human_original") as ask:
            self.assertFalse(cfg.wikitext.human_original)
        ask.assert_not_called()


class HumanOriginalSongSectionTest(TestCase):
    """「== 歌曲 ==」小节按版本分块：;VOCALOID本家 / ;人声本家（参 红色房间）"""

    def _song_with_human(self, **kwargs):
        return _song(["初音ミク"],
                     videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     **kwargs)

    def test_labels_both_versions(self):
        song = self._song_with_human(human_original=_human())
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertIn(f";VOCALOID本家\n{{{{bilibiliVideo|id={BB_MAIN}}}}}\n\n"
                      f";人声本家\n{{{{sm|{NICO_HUMAN}}}}}\n"
                      f"{{{{BilibiliVideo|id={BB_HUMAN}}}}}", body)

    def test_without_human_original_output_is_unchanged(self):
        song = self._song_with_human()
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn("人声本家", body)
        self.assertNotIn("本家", body)
        self.assertTrue(body.endswith(f"{{{{bilibiliVideo|id={BB_MAIN}}}}}"))

    def test_youtube_uses_youtube_video_template(self):
        song = self._song_with_human(human_original=_human(video="TG9IjsxAWUs"))
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertIn("{{YoutubeVideo|id=TG9IjsxAWUs}}", body)
        self.assertNotIn("{{sm|", body)

    def test_bilibili_only_human_original(self):
        song = self._song_with_human(human_original=_human(video=None))
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertIn(f";人声本家\n{{{{BilibiliVideo|id={BB_HUMAN}}}}}", body)
        self.assertNotIn("{{sm|", body)

    def test_label_follows_engine(self):
        song = _song(["可不"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     human_original=_human())
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertIn(";CeVIO本家\n", body)

    def test_human_block_added_without_main_bilibili(self):
        # 本家没有 B 站稿件时只出人声本家那一块（不带主版本标签）
        song = _song(["初音ミク"], human_original=_human())
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn(";VOCALOID本家", body)
        self.assertIn(f";人声本家\n{{{{sm|{NICO_HUMAN}}}}}", body)

    def test_staff_only_composer_vocalist_keeps_labels(self):
        # 词曲+演唱时本来就不写「== 歌曲 ==」小节，人声本家照样要能分辨出版本
        song = _song(["初音ミク"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     human_original=_human(),
                     staffs=[("词曲", [SimpleNamespace(name="x")]),
                             ("演唱", [SimpleNamespace(name="y")])])
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn("== 歌曲 ==", body)
        self.assertIn(f";VOCALOID本家\n{{{{bilibiliVideo|id={BB_MAIN}}}}}", body)
        self.assertIn(f";人声本家\n{{{{sm|{NICO_HUMAN}}}}}", body)
