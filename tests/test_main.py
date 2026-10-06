"""main.py 的引擎识别与分类输出测试（不联网）。

背景：同一个歌姬可能同时挂在多个引擎的角色表里（例：可不 在 CeVIO / Synthesizer V /
VoiSona 三张表里都有），旧实现会把三个引擎全写进简介，且在有专属歌手模板时还会整段
丢掉引擎分类。
"""
from datetime import date, datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest import mock

import main
from models.creators import Creators, Person, person_list_to_str
from models.song import Lyrics
from models.video import HumanOriginal, OtherVersion, VideoSite, video_link
from utils import disambig, lyrics_colors
from utils.name_converter import name_to_cat


def _video(site=VideoSite.NICO_NICO, year=2024, month=2, day=22, canonical=True,
           deleted=False, identifier="sm1", views=0):
    return SimpleNamespace(site=site, canonical=canonical, uploaded=date(year, month, day),
                           identifier=identifier, views=views, deleted=deleted)


def _song(vocalists=("可不",), name_jap="リビングデッドパンデッド", name_chs="活死人乐队",
          videos=None, human_original=None, staffs=(), other_versions=()):
    return SimpleNamespace(
        name_jap=name_jap, name_chs=name_chs, name_other=[],
        videos=list(videos) if videos else [],
        albums=[],
        human_original=human_original,
        other_versions=list(other_versions),
        creators=SimpleNamespace(
            vocalists_str=lambda: list(vocalists),
            vocalists=[SimpleNamespace(name=n) for n in vocalists],
            producers_str=lambda: ["すぷいちゃん"],
            producers=[SimpleNamespace(name="すぷいちゃん")],
            staff_list=lambda: [(role, list(people)) for role, people in staffs],
        ),
        vocaloid_collection=None, vocaloid_collection_rank=None,
        vocaloid_collection_track=None,
        vocaloid_collection_places=None,
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

    def test_voicepeak_voicebank_is_recognised_from_its_name_marker(self):
        """VocaDB 把 VOICEPEAK 声库写成 `小春六花 (VOICEPEAK)`（`artistType` 是
        `OtherVoiceSynthesizer`，枚举里根本没有 VOICEPEAK）—— 引擎要从名字里的标记认出来。

        用户 2026-10-05 报《彩色粉笔装饰物》：以前这一条被当成 VOCALOID，
        `[[分类:使用VOICEPEAK的歌曲]]` 缺失，简介里也没有 [[VOICEPEAK]]。
        """
        song = _song(["初音ミク", "小春六花 (VOICEPEAK)", "小春六花 AI"])
        self.assertEqual(["VOCALOID", "VOICEPEAK", "Synthesizer V"],
                         main.get_song_engines(song))
        categories = main.get_engine_categories(song)
        self.assertIn("[[分类:使用VOICEPEAK的歌曲]]", categories)
        self.assertIn("[[分类:使用Synthesizer V的歌曲]]", categories)

    def test_a_singers_voicebanks_are_written_once(self):
        """同一个歌姬的不同声库在**列表里只写一次**（歌曲栏曾经写出两个「小春六花」）。

        `Creators.vocalists` 本身**不去重**（引擎列表要靠每个人各自的 artist_type），
        去重发生在 `vocalists_str()` / `person_list_to_str()` 这一步。
        """
        creators = Creators(producers=[], staffs={},
                            vocalists=[Person("初音ミク"), Person("小春六花"), Person("小春六花")])
        self.assertEqual(["初音ミク", "小春六花"], creators.vocalists_str())
        self.assertEqual(["初音ミク", "小春六花"], person_list_to_str(creators.vocalists))

    def test_teto_defaults_to_utau(self):
        # vocadb 里未标注 SV 的重音テト 就是 UTAU
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音テト"])))

    def test_teto_sv_is_synthesizer_v(self):
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["重音テトSV"])))

    def test_teto_chinese_name_is_utau(self):
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音Teto"])))

    def test_synthesizer_v_table_follows_the_wiki_template(self):
        """按模板改掉 SynthV 表里的两条（用户 2026-09-30 要求）。

        * `俊达萌` 从 SynthV 表里删了 —— 站上 `Template:Synthesizer V` 里没有 ずんだもん，
          VocaDB 也只给了 UTAU / VOICEVOX / NEUTRINO 三条；于是**无类型**时它落回原有的
          NEUTRINO 表（那条本来就对），有 `artistType` 时照旧以类型为准；
        * `樱乃空` / `桜乃そら` 补进 SynthV 表 —— `Template:Synthesizer V` 里有樱乃空，
          以前她落到 VOICEROID（新表里带的），现在和 小春六花 / 弦卷真纪 那些 AHS 声库一致。
        """
        self.assertEqual(["NEUTRINO"], main.get_song_engines(_song(["俊达萌"])))
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["樱乃空"])))
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["桜乃そら"])))
        # 有类型时还是类型说了算（她 VocaDB 上两条都有）
        for artist_type, engine in (("Voiceroid", "VOICEROID"), ("SynthesizerV", "Synthesizer V")):
            song = _song(["桜乃そら"])
            song.creators.vocalists = [SimpleNamespace(name="桜乃そら", artist_type=artist_type)]
            self.assertEqual([engine], main.get_song_engines(song))

    def test_old_tables_are_filled_from_the_templates_too(self):
        """前六表也照模板补了一遍（用户 2026-09-30 要求）：以前谁都不认识、一律算 VOCALOID
        的歌姬现在能认出来；已经在表里的那些一个都没动（生成时过滤过）。"""
        cases = {
            "呗音Uta": "UTAU",                 # UTAU 模板（补了 204 个）
            "桃音Momo": "UTAU",
            "机流音": "CeVIO",                  # CeVIO 模板（30 个）
            "羽累": "CeVIO",
            "永夜Minus": "Synthesizer V",       # Synthesizer V 模板（56 个）
            "艾可": "Synthesizer V",
            "邪神酱": "VOICEPEAK",              # VOICEPEAK 模板（11 个）
            "JSUT": "NEUTRINO",
        }
        for vocalist, engine in cases.items():
            self.assertEqual([engine], main.get_song_engines(_song([vocalist])), vocalist)
        # 已经在别的表里的照旧（没被新名字抢走）
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音テト"])))
        self.assertEqual(["CeVIO"], main.get_song_engines(_song(["可不"])))

    def test_artist_type_wins_over_the_character_tables(self):
        """VocaDB 的 `artistType` 是「这首用了哪副声库」的准确答案（用户 2026-09-30 要求接上）。

        以前只能拿歌姬名去查角色表 —— ずんだもん 就被算成 Synthesizer V（旧 SynthV 表里
        有 `俊达萌`）；现在署名里写着 `VOICEVOX` 就照 VOICEVOX 算。
        """
        song = _song(["ずんだもん"])
        song.creators.vocalists = [SimpleNamespace(name="ずんだもん", artist_type="VOICEVOX")]
        self.assertEqual(["VOICEVOX"], main.get_song_engines(song))
        song.creators.vocalists = [SimpleNamespace(name="ずんだもん", artist_type="UTAU")]
        self.assertEqual(["UTAU"], main.get_song_engines(song))
        song.creators.vocalists = [SimpleNamespace(name="初音ミク", artist_type="NewType")]
        self.assertEqual(["New Type"], main.get_song_engines(song))
        # 认不出引擎的类型 → 回去查角色表
        song.creators.vocalists = [SimpleNamespace(name="四国玫碳",
                                                  artist_type="OtherVoiceSynthesizer")]
        self.assertEqual(["VOICEVOX"], main.get_song_engines(song))
        # 引擎分类也跟着类型走
        song.creators.vocalists = [SimpleNamespace(name="ずんだもん", artist_type="VOICEVOX")]
        self.assertIn("[[分类:使用VOICEVOX的歌曲]]", main.get_engine_categories(song))

    def test_engines_added_from_the_wiki_templates(self):
        """用户 2026-09-30：照 voca.wiki `Category:音声合成软件模板` 里的引擎模板补。

        每个引擎表里同时收着**站上条目名**与日文写法（`春日部紬` / `春日部つむぎ`），
        VocaDB 给的是日文名，两种写法都得能识别。
        """
        cases = {
            "四国めたん": "VOICEVOX",
            "四国玫碳": "VOICEVOX",
            "春日部つむぎ": "VOICEVOX",          # 表里叫「春日部紬」
            "冥鳴ひまり": "VOICEVOX",
            "琴葉茜・葵": "VOICEROID",
            "伊織弓鶴": "VOICEROID",
            "月読アイ": "VOICEROID",
            "嫣汐": "AISingers",
            "陈水若": "X Studio",
            "云灏": "ACE",
            "小冰": "X Studio",
        }
        for vocalist, engine in cases.items():
            self.assertEqual([engine], main.get_song_engines(_song([vocalist])), vocalist)

    def test_new_engines_do_not_steal_the_old_ones(self):
        """新引擎接在原有六个后面，而且主力是 VOCALOID 的不补日文写法。

        `結月ゆかり` / `紲星あかり` / `鳴花ヒメ` 的中文写法（结月缘 / 绁星灯 / …）
        确实挂在 CeVIO / VOICEPEAK / Synthesizer V 的表里，但它们的主力是 VOCALOID ——
        拿日文名去归一化就会认错（记忆里记的这个坑）。
        """
        # 既有归属原样
        self.assertEqual(["UTAU"], main.get_song_engines(_song(["重音テト"])))
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["重音テトSV"])))
        self.assertEqual(["CeVIO"], main.get_song_engines(_song(["可不"])))
        # 主力是 VOCALOID 的：照旧 VOCALOID（新引擎一张表都不碰）
        for vocalist in ("初音ミク", "洛天依", "言和", "flower", "VY1", "鳴花ヒメ",
                         "結月ゆかり", "紲星あかり", "音街ウナ", "ずんだもん"):
            self.assertEqual(["VOCALOID"], main.get_song_engines(_song([vocalist])), vocalist)

    def test_multi_engine_song_lists_each_engine_once(self):
        """实测《阿卡贝拉一起唱！！》：`{{虚拟歌手歌曲荣誉题头|VOCALOID|…|VOICEROID|…}}`
        是按歌姬出现顺序一个引擎一个参数写的，工具这边也一样。"""
        song = _song(["初音未来", "四国玫碳", "春日部紬", "嫣汐"])
        self.assertEqual(["VOCALOID", "VOICEVOX", "AISingers"],
                         main.get_song_engines(song))


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
            "歌愛ユキ": "{{歌爱雪/2024}}",
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
            "イア": "{{IA/2024}}",
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


class SongPostedTest(TestCase):
    """家族模板列表的日期注释（`<!-- 02-22 23:00 -->`）：取最早的投稿，用东八区到分钟。"""

    def _video(self, uploaded=date(2024, 2, 22), uploaded_cn=datetime(2024, 2, 22, 23, 0),
               site=VideoSite.NICO_NICO, canonical=True):
        return SimpleNamespace(site=site, canonical=canonical, uploaded=uploaded,
                              uploaded_cn=uploaded_cn, identifier="sm1", views=0,
                              deleted=False)

    def test_uses_the_china_wall_clock(self):
        # nico 的 `2024-02-23T00:00:00+09:00` 在东八区是 02-22 23:00（模板里就是这么写的）
        posted = main.get_song_posted(_song(["可不"], videos=[self._video()]))
        self.assertEqual(datetime(2024, 2, 22, 23, 0), posted.when)
        self.assertEqual("02-22 23:00", posted.stamp())
        self.assertEqual("niconico", posted.site)

    def test_earliest_video_wins(self):
        song = _song(["可不"], videos=[
            self._video(uploaded=date(2024, 3, 2), uploaded_cn=None, site=VideoSite.YOUTUBE),
            self._video(),
        ])
        self.assertEqual("niconico", main.get_song_posted(song).site)

    def test_falls_back_to_the_date_when_time_is_unknown(self):
        posted = main.get_song_posted(_song(["可不"], videos=[
            self._video(uploaded_cn=None, site=VideoSite.YOUTUBE,
                        uploaded=date(2024, 4, 20))]))
        self.assertEqual(date(2024, 4, 20), posted.when.date())
        self.assertEqual("youtube 04-20", posted.stamp())

    def test_epoch_is_ignored(self):
        # 抓取失败时是 epoch，不能写进模板（否则会去列表最前面）
        video = self._video(uploaded=datetime.fromtimestamp(0), uploaded_cn=None)
        self.assertIsNone(main.get_song_posted(_song(["可不"], videos=[video])))

    def test_no_videos(self):
        self.assertIsNone(main.get_song_posted(_song(["可不"], videos=[])))

    def test_build_family_sync_carries_posted(self):
        family = main.build_family_sync(_song(["可不"], videos=[self._video()]))
        self.assertEqual(datetime(2024, 2, 22, 23, 0), family.posted.when)


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

    def test_collection_places_are_carried_into_the_sync(self):
        """两榜都在（爬模板读出来的 places）→ 同步时两个赛道各写一处。"""
        song = _song(["可不"])
        song.vocaloid_collection = "ボカコレ2022春"
        song.vocaloid_collection_track = "TOP100"
        song.vocaloid_collection_rank = "70"
        song.vocaloid_collection_places = [("TOP100", 70), ("ROOKIE", 42)]
        item = main.get_collection_sync(song)
        self.assertEqual([("TOP100", 70), ("ROOKIE", 42)], item.placements())
        self.assertTrue(item.ranked)

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

    def _collection_song(self, versions=()):
        song = _song(["可不"], other_versions=versions)
        song.vocaloid_collection = "ボカコレ2024冬"
        song.vocaloid_collection_track = "TOP100"
        song.vocaloid_collection_rank = "15"
        return song

    def test_other_versions_collections_are_added_as_more_templates(self):
        """每个版本参加的活动都要写模板（主版本 2024冬 + 翻唱版 2025春 → 两个模板）。"""
        version = _other_version(collection="ボカコレ2025春", track="ROOKIE", rank="7")
        song = self._collection_song([version])
        with mock.patch.object(main, "get_config", return_value=SimpleNamespace(
                wikitext=SimpleNamespace(producer_template=False, collapse_navbox=False))), \
             mock.patch.object(main, "get_producer_info", mock.AsyncMock(return_value=[])):
            end = main.create_end(song)
        self.assertIn("{{The VOCALOID Collection2024冬}}\n{{The VOCALOID Collection2025春}}\n", end)
        # 大家族模板同步的清单也跟上（否则只同步了主版本那一届）
        collections = main.build_family_sync(song).collections
        self.assertEqual(["The VOCALOID Collection2024冬", "The VOCALOID Collection2025春"],
                         [item.template for item in collections])
        self.assertEqual(("ROOKIE", 7), (collections[1].track, collections[1].rank))

    def test_same_collection_is_written_once(self):
        """同一个版本 / 同一届活动只写一遍，主版本的赛道名次优先。"""
        same = _other_version(collection="ボカコレ2024冬", track="ROOKIE", rank="7")
        song = self._collection_song([same, _other_version(label="另一个版本")])
        collections = main.get_collection_syncs(song)
        self.assertEqual(["The VOCALOID Collection2024冬"],
                         [item.template for item in collections])
        self.assertEqual("TOP100", collections[0].track)
        self.assertEqual(15, collections[0].rank)

    def test_no_collection_anywhere_gives_an_empty_list(self):
        self.assertEqual([], main.get_collection_syncs(_song(["可不"])))
        self.assertEqual([], main.get_collection_syncs(
            _song(["可不"], other_versions=[_other_version()])))

    def test_a_collection_only_from_an_other_version_is_still_written(self):
        """主版本没参加、某个版本参加了 → 注释区依旧要写那一届的模板。"""
        song = _song(["可不"], other_versions=[_other_version(collection="ボカコレ2025春")])
        self.assertEqual(["The VOCALOID Collection2025春"],
                         [item.template for item in main.get_collection_syncs(song)])


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

    def test_chinese_column_can_have_its_own_marks(self):
        """中文栏单独标过（用户 2026-09 要求）：两栏各用各的标记，行数不一样也没问题。"""
        song = self._song(marks={"0": ["宮舞モカ"], "2": ["Ryo"]},
                          chara_marks_chs={"0": ["Ryo"], "1": ["宮舞モカ"]})
        song.lyrics.lyrics_chs = "啊\n咦"
        out = self._render(song)
        self.assertIn("@1あ\n\n@2い", out)          # 日语栏照旧
        self.assertIn("@2啊\n@1咦", out)            # 中文栏用自己那套

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


class TranslationNoticeTest(TestCase):
    """翻译栏无译者时直接标注转载来源。"""

    def _render(self, **lyrics_fields):
        song = _song(["初音ミク"])
        song.lyrics = Lyrics(lyrics_jap="原文", lyrics_chs="译文", **lyrics_fields)
        return main.create_lyrics(song)

    def test_missing_translator_uses_direct_linked_source_notice(self):
        out = self._render(source_name="网易云", source_url="https://music.163.com/song")
        self.assertIn("*翻译转载自[https://music.163.com/song 网易云]\n", out)
        self.assertNotIn("*翻译：", out)
        self.assertNotIn("<ref>", out)

    def test_missing_translator_with_source_name_only_uses_direct_notice(self):
        out = self._render(source_name="网易云")
        self.assertIn("*翻译转载自网易云\n", out)
        self.assertNotIn("<ref>", out)

    def test_missing_translator_and_source_omits_empty_notice(self):
        out = self._render()
        self.assertNotIn("*翻译", out)

    def test_known_translator_keeps_existing_reference_format(self):
        out = self._render(translator="译者", source_name="网易云",
                           source_url="https://music.163.com/song")
        self.assertIn("*翻译：译者<ref>翻译转载自[https://music.163.com/song 网易云]</ref>\n",
                      out)


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


class UploaderNoteTest(TestCase):
    """「填写投稿文？」：选「是」→ 收日语版 + 中文版，拼成 Cquote（署名用 P 主名）。"""

    def _ask(self, song, choices=1, lines=("あいさつ", "问候")):
        config = SimpleNamespace(wikitext=SimpleNamespace(uploader_note=True))
        answers = iter([list(lines[:1]), list(lines[1:])])
        with mock.patch.object(main, "get_config", return_value=config), \
             mock.patch.object(main, "prompt_choices", return_value=choices), \
             mock.patch.object(main, "prompt_multiline",
                               side_effect=lambda *a, **k: next(answers)):
            return main.create_uploader_note(song)

    def test_answers_no_returns_empty(self):
        config = SimpleNamespace(wikitext=SimpleNamespace(uploader_note=True))
        with mock.patch.object(main, "get_config", return_value=config), \
             mock.patch.object(main, "prompt_choices", return_value=2), \
             mock.patch.object(main, "prompt_multiline") as multiline:
            self.assertEqual("", main.create_uploader_note(_song(["可不"])))
        multiline.assert_not_called()

    def test_yes_collects_both_languages(self):
        out = self._ask(_song(["可不"]))
        self.assertIn("{{Cquote|{{lj|あいさつ}}", out)
        self.assertIn("----\n问候", out)
        self.assertIn("投稿文", out)                    # 署名贴着中文那段

    def test_song_without_producer_does_not_crash(self):
        song = _song(["可不"])
        song.creators.producers = []                    # VocaDB 上没写 P 主
        out = self._ask(song)
        self.assertIn("{{lj|あいさつ}}", out)
        self.assertNotIn("投稿文", out)                 # 没署名，但内容照出


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
    return SimpleNamespace(wikitext=SimpleNamespace(**kwargs))


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

    def test_sentence_comes_after_collection_and_albums(self):
        """简介的顺序：「…演唱。」→「本曲参与了…活动，收录于专辑…。」→「另有…人声本家。」

        参 voca.wiki《小小星座》的 diff 251205（用户 2026-09-28 指出这一句要在活动句下面）；
        旧实现把它插在活动句**前面**。
        """
        song = _song(["初音ミク"], human_original=_human())
        song.vocaloid_collection = "ボカコレ2021秋"
        song.vocaloid_collection_rank = "45"
        song.albums = ["RuLu"]
        intro = main.create_intro(song)
        self.assertLess(intro.index("本曲参与了"), intro.index("人声本家"))
        self.assertLess(intro.index("收录于专辑"), intro.index("人声本家"))
        self.assertTrue(intro.rstrip().endswith("另有P主本人演唱的人声本家。"), intro)

    def test_disabled_config_never_asks(self):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(human_original=False))
        with mock.patch.object(main, "get_config", return_value=cfg), \
             mock.patch.object(main, "get_human_original") as ask:
            self.assertFalse(cfg.wikitext.human_original)
        ask.assert_not_called()

    def test_prompt_helper_is_not_shadowed(self):
        """`models.video.get_human_original` 是**问用户**的那个，main 里别再定义同名函数。

        以前 main 里有个同名辅助函数把它遮蔽掉，`generate()` 走到
        `song.human_original = get_human_original()` 就会以「缺 1 个参数」抛 TypeError
        ——也就是 wikitext.human_original 一开启，主流程就崩。
        """
        from models import video as video_module
        self.assertIs(video_module.get_human_original, main.get_human_original,
                      "main.get_human_original 必须是 models.video 里那个问用户的函数")
        with mock.patch.object(video_module, "prompt_choices", return_value=2) as choices:
            self.assertIsNone(main.get_human_original())
        choices.assert_called_once()
        self.assertIsNone(main.song_human_original(_song(["初音ミク"])))


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
                      f";人声本家\n{{{{BilibiliVideo|id={BB_HUMAN}}}}}", body)

    def test_bilibili_alone_is_written_when_it_exists(self):
        """有 B 站稿件时**不写** nico / YouTube（用户 2026-09 要求）。

        参 voca.wiki 红色房间 / 如月车站 / 小小星座：`;人声本家` 下面只有一行 `{{BilibiliVideo}}`，
        即便这份人声本家还挂在 niconico / YouTube 上。
        """
        song = self._song_with_human(human_original=_human())
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn("{{sm|", body)
        self.assertNotIn("{{YoutubeVideo", body)
        self.assertIn(f";人声本家\n{{{{BilibiliVideo|id={BB_HUMAN}}}}}", body)

    def test_without_human_original_output_is_unchanged(self):
        song = self._song_with_human()
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn("人声本家", body)
        self.assertNotIn("本家", body)
        self.assertTrue(body.endswith(f"{{{{bilibiliVideo|id={BB_MAIN}}}}}"))

    def test_youtube_template_only_when_there_is_no_bilibili(self):
        """一个 B 站稿件都没有时才写 `{{YoutubeVideo}}`（有 B 站那份时不写）。"""
        song = self._song_with_human(human_original=_human(video="TG9IjsxAWUs", bilibili=None))
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertIn(f";人声本家\n{{{{YoutubeVideo|id=TG9IjsxAWUs}}}}", body)
        self.assertNotIn("BilibiliVideo", body.split(";人声本家")[1])
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
        # 主版本没有 B 站稿件时只出人声本家那一块（不带主版本标签）
        song = _song(["初音ミク"], human_original=_human(bilibili=None))
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertNotIn(";VOCALOID本家", body)
        self.assertIn(f";人声本家\n{{{{sm|{NICO_HUMAN}}}}}", body)

    def test_staff_only_composer_vocalist_keeps_heading_and_labels(self):
        # 词曲+演唱时不写「VOCALOID Songbox Introduction」表，但「== 歌曲 ==」小节标题必须留下
        # （用户 2026-09 报「生成歌曲 君が僕を嗤う日 时『== 歌曲 ==』不见了」）
        song = _song(["初音ミク"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     human_original=_human(),
                     staffs=[("词曲", [SimpleNamespace(name="x")]),
                             ("演唱", [SimpleNamespace(name="y")])])
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            body = main.create_song(song)
        self.assertTrue(body.startswith("== 歌曲 ==\n\n"))
        self.assertNotIn("{{VOCALOID Songbox Introduction", body)
        self.assertIn(f";VOCALOID本家\n{{{{bilibiliVideo|id={BB_MAIN}}}}}", body)
        self.assertIn(f";人声本家\n{{{{BilibiliVideo|id={BB_HUMAN}}}}}", body)


class SongSectionHeadingTest(TestCase):
    """「== 歌曲 ==」小节：没有 Introduction 表时（词曲+演唱）也不能把小节标题一起丢掉。"""

    _STAFFS = [("词曲", [SimpleNamespace(name="x")]), ("演唱", [SimpleNamespace(name="y")])]

    def _body(self, song):
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            return main.create_song(song)

    def test_bilibili_only_song_keeps_heading(self):
        # 实测 voca.wiki《你嘲笑我那天》：只有「== 歌曲 ==」+ 播放器，没有表
        body = self._body(_song(["可不"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                                staffs=self._STAFFS))
        self.assertTrue(body.startswith("== 歌曲 ==\n\n"))
        self.assertIn(f"{{{{bilibiliVideo|id={BB_MAIN}}}}}", body)

    def test_song_without_any_video_has_no_empty_section(self):
        # 这一节什么都没有时，连标题都不写（不留一个空小节）
        self.assertEqual("", self._body(_song(["可不"], videos=[], staffs=self._STAFFS)))

    def test_heading_and_table_for_full_staff(self):
        # 有编曲 / 曲绘等额外分工时照旧写表，标题只出现一次
        song = _song(["可不"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     staffs=[*self._STAFFS, ("曲绘", [SimpleNamespace(name="z")])])
        body = self._body(song)
        self.assertTrue(body.startswith("== 歌曲 ==\n"))
        self.assertIn("{{VOCALOID Songbox Introduction", body)
        self.assertEqual(1, body.count("== 歌曲 =="))


class MegpoidVocalistTest(TestCase):
    """歌姬名归一化后的各处写法（参 voca.wiki《小小星座》rev 251206）。"""

    def test_songbox_and_intro_link_to_megpoid(self):
        """链接写成 `[[Megpoid|GUMI]]`（参 voca.wiki《视力检查》），而不是 `[[Megpoid]]`。"""
        song = _song(["Megpoid", "花隈千冬"])
        self.assertIn("|演唱    = [[Megpoid|GUMI]]、[[花隈千冬]]", main.create_songbox(song))
        self.assertIn("由[[Megpoid|GUMI]]和[[花隈千冬]]演唱。", main.create_intro(song))

    def test_megpoid_counts_as_synthesizer_v(self):
        # 以前 'Megpoid' 不在任何引擎表里 → 简介写 [[VOCALOID]]、分类也错
        self.assertEqual(["Synthesizer V"], main.get_song_engines(_song(["Megpoid"])))

    def test_category_name_goes_through_gumi_to_megpoid(self):
        # [[分类:Megpoid歌曲]]：name_to_cat 先 name_to_chinese（Megpoid → GUMI）再过 cat_transform
        self.assertEqual("Megpoid", name_to_cat("Megpoid"))


class StaffRowMergeTest(TestCase):
    """Introduction 表里「同一个人占了两栏」的合并（用户 2026-09-28 对照《小小星座》要求）。"""

    def _body(self, staffs):
        song = _song(["可不"], videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN)],
                     staffs=staffs)
        with mock.patch.object(main, "get_config", return_value=_wikitext_config()):
            return main.create_song(song)

    @staticmethod
    def _people(*names):
        return [SimpleNamespace(name=name) for name in names]

    def test_illustrator_and_animator_merge_into_one_row(self):
        # 实测《小小星座》：VocaDB 上月乃同时挂着 Illustrator 与 Animator，
        # 旧实现写出两张一模一样的表 → `|group2 = 曲绘、PV制作`（参《学习室》）
        body = self._body([("词曲", self._people("Capchii")),
                           ("曲绘", self._people("月乃")),
                           ("PV制作", self._people("月乃"))])
        self.assertIn("|group2 = 曲绘、PV制作\n|list2 = {{lj|月乃}}\n", body)
        self.assertEqual(1, body.count("月乃"))

    def test_different_people_keep_two_rows(self):
        body = self._body([("词曲", self._people("P")),
                           ("曲绘", self._people("A")),
                           ("PV制作", self._people("B"))])
        self.assertIn("|group2 = 曲绘\n|list2 = A\n", body)
        self.assertIn("|group3 = PV制作\n|list3 = B\n", body)

    def test_arranger_who_is_also_the_composer_joins_the_composer_row(self):
        body = self._body([("词曲", self._people("Capchii")),
                           ("编曲", self._people("Capchii"))])
        self.assertIn("|group1 = 词曲\n|list1 = Capchii\n", body)
        self.assertNotIn("编曲", body)

    def test_arranger_with_an_extra_person_keeps_its_row(self):
        """编曲那栏还有别人时不能整栏丢掉（否则那个人的名字就没了）。"""
        body = self._body([("词曲", self._people("A")),
                           ("编曲", self._people("A", "B"))])
        self.assertIn("|group2 = 编曲\n|list2 = A<br/>B\n", body)

    def test_arranger_without_a_composer_row_is_untouched(self):
        body = self._body([("作曲", self._people("A")), ("编曲", self._people("A"))])
        self.assertIn("|group1 = 作曲\n|list1 = A\n", body)
        self.assertIn("|group2 = 编曲\n|list2 = A\n", body)


# 其他版本：同一首歌的翻唱 / 改编版本（参 voca.wiki 条目《鸟之诗》）
BB_VERSION_1 = "BV1is411f772"
BB_VERSION_2 = "BV1Pgxvz9E41"


def _other_version(label="镜音铃版", identifier=BB_VERSION_1, views=0, canonical=True,
                   vocalists=("鏡音リン",), producers=("じゃがりこP",),
                   publish=date(2007, 12, 28), song_type="Cover", pv_services="",
                   tab_label="", videos=None, albums=None, collection="",
                   track=None, rank=None, places=None):
    return OtherVersion(
        version_id=1, label=label, tab_label=tab_label, song_type=song_type,
        artist_string="{} feat. {}".format("、".join(producers), "、".join(vocalists)),
        vocalists=list(vocalists), producers=list(producers), publish_date=publish,
        pv_services=pv_services, canonical=canonical, videos=list(videos or []),
        albums=list(albums or []), vocaloid_collection=collection,
        vocaloid_collection_track=track, vocaloid_collection_rank=rank,
        vocaloid_collection_places=list(places or []),
        video=_video(VideoSite.BILIBILI, identifier=identifier, views=views,
                     year=publish.year, month=publish.month, day=publish.day,
                     canonical=canonical))


class OtherVersionsTest(TestCase):
    """选中的其他版本：整页套 `{{tabs}}`，`== 歌曲 ==` 里按 `;版本名` 列 B 站播放器。

    参 voca.wiki《鸟之诗》：tab 里是「荣誉题头 + Songbox + 简介」，
    共用的歌词 / 注释 / 分类都留在 tabs 外面。
    """

    def _song(self, versions):
        return _song(["初音ミク"], name_jap="鳥の詩", name_chs="鸟之诗",
                     videos=[_video(VideoSite.BILIBILI, identifier=BB_MAIN,
                                    year=2007, month=9, day=1, views=100689)],
                     other_versions=versions)

    def test_tabs_wrap_the_main_version_and_every_other_version(self):
        song = self._song([_other_version()])
        page = main.create_page_title(song) + main.create_tabs(song, main.create_intro(song))
        self.assertIn("{{tabs\n|color=transparent", page)
        self.assertIn("|bt1=原版", page)                          # 主版本在 tab 上就叫「原版」
        self.assertIn("|bt2=镜音铃版", page)
        self.assertIn("|tab2=", page)
        self.assertIn("{{虚拟歌手歌曲荣誉题头|VOCALOID|brank=1}}", page)
        self.assertIn("|bb_id = BV1nv411N7mY", page)
        # 其他版本的简介也是生成出来的（同一套句式），不再是让人手写一句
        self.assertIn("是由{{lj|[[じゃがりこP]]}}于2007年12月28日投稿至[[bilibili]]的"
                      "[[VOCALOID]]日语翻唱歌曲，由[[镜音铃]]演唱。", page)
        self.assertEqual(2, page.count("{{VOCALOID_Songbox"), "主版本 + 其他版本各一个")

    def test_title_templates_stay_outside_the_tabs(self):
        song = self._song([_other_version()])
        page = main.create_page_title(song) + main.create_tabs(song, main.create_intro(song))
        self.assertTrue(page.startswith("{{标题替换|{{lj|鳥の詩}}}}\n"))
        self.assertNotIn("{{标题替换|", page[page.index("{{tabs"):])

    def test_song_section_lists_every_version(self):
        song = self._song([_other_version(),
                           _other_version(label="重音Teto UTAU版", identifier=BB_VERSION_2,
                                          vocalists=("重音テト",), producers=("tattoo2003",))])
        body = main.create_song(song)
        self.assertIn(f";すぷいちゃん初音未来版\n{{{{bilibiliVideo|id={BB_MAIN}}}}}", body)
        self.assertIn(f";镜音铃版\n{{{{BilibiliVideo|id={BB_VERSION_1}}}}}", body)
        self.assertIn(f";重音Teto UTAU版\n{{{{BilibiliVideo|id={BB_VERSION_2}}}}}", body)
        self.assertLess(body.index(";すぷいちゃん初音未来版"), body.index(";镜音铃版"))

    def test_without_other_versions_output_is_unchanged(self):
        """一个都没选 → `generate()` 走单版本那条路，主版本的播放器也不带 `;版本名`。"""
        song = self._song([])
        body = main.create_song(song)
        self.assertNotIn(";", body)
        self.assertTrue(body.endswith(f"{{{{bilibiliVideo|id={BB_MAIN}}}}}"))
        self.assertEqual(1, main.create_tabs(song, "简介").count("|bt"),
                         "没有其他版本时 tabs 里只有主版本")

    def test_other_version_honor_header_needs_hall_of_fame(self):
        self.assertEqual("", main.other_version_honor_header(_other_version(views=99999)))
        self.assertIn("brank=1", main.other_version_honor_header(_other_version(views=100000)))
        self.assertIn("brank=2",
                      main.other_version_honor_header(_other_version(views=1_200_000)))

    def test_other_version_honor_header_counts_nico_and_youtube_too(self):
        """该版本自己在 nico / YouTube 上的稿件也算殿堂（与主版本同一套规则）。"""
        version = _other_version(videos=[
            _video(VideoSite.NICO_NICO, identifier="sm44829675", views=1_200_000),
            _video(VideoSite.YOUTUBE, identifier="4FEzamGv7tM", views=100000)])
        header = main.other_version_honor_header(version)
        self.assertIn("nrank=2", header)
        self.assertIn("yrank=1", header)
        self.assertNotIn("brank", header)
        # 次序与主版本一致：nrank → yrank → brank
        self.assertLess(header.index("nrank"), header.index("yrank"))

    def test_other_version_honor_header_keeps_its_own_video_below_the_threshold_out(self):
        # 该版本自己的 nico 稿件没到殿堂 -> 只有 brank，没有 nrank
        version = _other_version(views=1_200_000,
                                 videos=[_video(VideoSite.NICO_NICO, identifier="sm1", views=99999)])
        header = main.other_version_honor_header(version)
        self.assertIn("brank=2", header)
        self.assertNotIn("nrank", header)
        self.assertEqual("", main.other_version_honor_header(
            _other_version(views=99999, videos=[_video(VideoSite.NICO_NICO, identifier="sm1",
                                                       views=99999)])))

    def test_other_version_honor_header_skips_unofficial_uploads(self):
        # 转载 / 非 P主 投稿的稿件不计入殿堂（与主版本的规则一致）
        self.assertEqual("", main.other_version_honor_header(
            _other_version(views=1_200_000, canonical=False)))

    def test_reprinted_bilibili_still_lets_the_official_nico_upload_rank(self):
        """B 站那份是转载时不算 brank，但该版本自己在 nico 上的殿堂稿仍算 nrank（例：ROCK_VER）。"""
        version = _other_version(views=2_000_000, canonical=False,
                                 videos=[_video(VideoSite.NICO_NICO, identifier="sm44829675",
                                                views=1_200_000)])
        header = main.other_version_honor_header(version)
        self.assertIn("nrank=2", header)
        self.assertNotIn("brank", header)

    def test_other_version_honor_header_follows_the_engine(self):
        header = main.other_version_honor_header(
            _other_version(views=100000, vocalists=("重音テト",)))
        self.assertIn("{{虚拟歌手歌曲荣誉题头|UTAU|brank=1}}", header)

    def test_other_version_songbox_uses_bilibili_id_and_date(self):
        box = main.create_other_version_songbox(self._song([]), _other_version())
        self.assertIn("|演唱    = [[镜音铃]]", box)
        self.assertIn("|P主 = [[{{lj|じゃがりこP}}]]", box)
        self.assertIn("|歌曲名称 = {{lj|鳥の詩}}<br/>鸟之诗", box)
        self.assertIn(f"|bb_id = {BB_VERSION_1}", box)
        self.assertIn("|bb_date = 2007年12月28日", box)

    def test_tabs_use_the_short_version_names(self):
        """tab 按钮只写短名：主版本「原版」、有补充说明的版本只写那一截（ROCK_VER → ROCK版）。"""
        version = _other_version(label="Shu初音未来、巡音流歌版（ROCK_VER）", tab_label="ROCK版")
        song = self._song([version])
        page = main.create_tabs(song, "简介")
        self.assertIn("|bt1=原版", page)
        self.assertIn("|bt2=ROCK版", page)
        # 「== 歌曲 ==」里的 `;版本名` 仍是完整版本名
        self.assertIn(";Shu初音未来、巡音流歌版（ROCK_VER）", main.create_song(song))

    def test_other_version_songbox_lists_its_own_nico_and_youtube(self):
        """其他版本自己在 nico / YouTube 上的稿件也要写进 Songbox（以前只有 B 站那两栏）。"""
        version = _other_version(
            videos=[_video(VideoSite.NICO_NICO, identifier="sm44829675",
                           year=2007, month=12, day=27),
                    _video(VideoSite.YOUTUBE, identifier="4FEzamGv7tM",
                           year=2007, month=12, day=29)])
        box = main.create_other_version_songbox(self._song([]), version)
        self.assertIn("|nnd_id = sm44829675", box)
        self.assertIn("|nnd_date = 2007年12月27日", box)
        self.assertIn(f"|bb_id = {BB_VERSION_1}", box)
        self.assertIn("|yt_id = 4FEzamGv7tM", box)
        self.assertIn("|yt_date = 2007年12月29日", box)
        # 栅位顺序跟主版本的 Songbox 一致：nnd → bb → yt
        self.assertLess(box.index("|nnd_id"), box.index("|bb_id"))
        self.assertLess(box.index("|bb_id"), box.index("|yt_id"))

    def test_other_version_songbox_falls_back_to_vocadb_date_for_every_site(self):
        # 站点取不到日期（epoch）时，每一栏都退回 VocaDB 的 publishDate
        version = _other_version(publish=date(2013, 4, 7),
                                 videos=[_video(VideoSite.NICO_NICO, identifier="sm9",
                                                year=1970, month=1, day=1),
                                         _video(VideoSite.YOUTUBE, identifier="abc12345678",
                                                year=1970, month=1, day=1)])
        box = main.create_other_version_songbox(self._song([]), version)
        self.assertIn("|nnd_date = 2013年4月7日", box)
        self.assertIn("|yt_date = 2013年4月7日", box)

    def test_other_version_songbox_skips_sites_it_has_no_video_for(self):
        version = _other_version()
        version.videos = [_video(VideoSite.NICO_NICO, identifier="sm1")]
        box = main.create_other_version_songbox(self._song([]), version)
        self.assertIn("|nnd_id = sm1", box)
        self.assertNotIn("|yt_id", box)

    def test_unknown_date_is_not_written_as_1970(self):
        # B 站与 VocaDB 都拿不到日期时宁可不写，也不要写「1970年1月1日」
        version = _other_version(publish=date(1970, 1, 1))
        version.video = _video(VideoSite.BILIBILI, identifier=BB_VERSION_1,
                               year=1970, month=1, day=1)
        box = main.create_other_version_songbox(self._song([]), version)
        self.assertIn(f"|bb_id = {BB_VERSION_1}", box)
        self.assertNotIn("|bb_date", box)

    def test_other_version_songbox_falls_back_to_vocadb_date(self):
        # B 站 API 拿不到发布日期（epoch）时用 VocaDB 的 publishDate
        version = _other_version(publish=date(2013, 4, 7))
        version.video = _video(VideoSite.BILIBILI, identifier=BB_VERSION_1,
                               year=1970, month=1, day=1)
        box = main.create_other_version_songbox(self._song([]), version)
        self.assertIn("|bb_date = 2013年4月7日", box)

    def test_other_version_intro_has_the_same_shape_as_the_main_one(self):
        """其他版本的简介与主简介同一句式：`《'''歌名'''》（译名）是由…的…歌曲，由…演唱。`"""
        song = self._song([_other_version()])
        self.assertEqual(
            "《'''{{lj|鳥の詩}}'''》（鸟之诗）是由{{lj|[[じゃがりこP]]}}于2007年12月28日"
            "投稿至[[bilibili]]的[[VOCALOID]]日语翻唱歌曲，由[[镜音铃]]演唱。",
            main.create_other_version_intro(song, song.other_versions[0]).strip())
        # 主简介走的是同一个 intro_sentence()
        self.assertIn("是由{{lj|[[すぷいちゃん]]}}于2007年9月1日投稿至[[bilibili]]的"
                      "[[VOCALOID]]日语原创歌曲，由[[初音未来]]演唱。", main.create_intro(song))

    def test_other_version_intro_lists_the_albums_it_is_on(self):
        """其他版本被专辑收录时也写「本曲收录于专辑《…》。」（用户 2026-09 要求，与主简介同一套）。"""
        version = _other_version(albums=["after EXCURSION -家に帰るまでが遠足です。-"])
        text = main.create_other_version_intro(self._song([]), version)
        self.assertIn("本曲收录于专辑《'''{{lj|after EXCURSION -家に帰るまでが遠足です。-}}'''》。",
                      text)
        # 单独一段：先一句介绍，空行之后再写专辑
        self.assertIn("演唱。\n\n本曲收录于专辑", text)

    def test_other_version_intro_writes_the_collection_sentence(self):
        """其他版本参加了活动（VocaDB 按版本记的 releaseEvents）时，也写主简介那句（用户 2026-09）。"""
        version = _other_version(collection="ボカコレ2024冬", track="TOP100", rank="3",
                                 albums=["EGO1STECH"])
        text = main.create_other_version_intro(self._song([]), version)
        self.assertIn("本曲参与了[[The VOCALOID Collection]]({{lj|ボカコレ2024冬}})活动"
                      "并获得TOP100中的第'''3'''名，收录于专辑《'''EGO1STECH'''》。", text)

    def test_other_version_collection_without_rank(self):
        """榜外（或没名次）只写活动，不写名次；专辑那句照旧接在后面。"""
        version = _other_version(collection="ボカコレ2024冬", track="榜外", rank=None,
                                 albums=["EGO1STECH"])
        text = main.create_other_version_intro(self._song([]), version)
        self.assertIn("本曲参与了[[The VOCALOID Collection]]({{lj|ボカコレ2024冬}})活动，"
                      "收录于专辑《'''EGO1STECH'''》。", text)
        self.assertNotIn("并获得", text)

    def test_other_version_collection_without_albums_ends_with_a_period(self):
        version = _other_version(collection="ボカコレ2024冬", track="ROOKIE", rank="7")
        text = main.create_other_version_intro(self._song([]), version)
        # ROOKIE 要带「榜」字（实测 Doomer：「获得ROOKIE榜中的第'''3'''名」）
        self.assertIn("并获得ROOKIE榜中的第'''7'''名。", text)
        self.assertNotIn("收录于专辑", text)

    def test_collection_sentence_writes_both_tracks(self):
        """两榜都在时两榜都写（参 涅槃(HotaRu)：TOP100 第 70 名、ROOKIE 第 42 名）。"""
        places = [("TOP100", 70), ("ROOKIE", 42)]
        self.assertEqual(
            "本曲参与了[[The VOCALOID Collection]]({{lj|ボカコレ2022春}})活动"
            "并获得TOP100中的第'''70'''名、ROOKIE榜中的第42名。",
            main.collection_sentence("ボカコレ2022春", "TOP100", "70", places))
        # 只有 ROOKIE 时那个名次加粗（实测 Doomer）
        self.assertIn("并获得ROOKIE榜中的第'''3'''名。",
                      main.collection_sentence("ボカコレ2025夏", "ROOKIE", "3", [("ROOKIE", 3)]))
        # 榜外（空 places）不写名次
        self.assertNotIn("获得", main.collection_sentence("ボカコレ2022春", "榜外", None, []))

    def test_collection_sentence_writes_the_remix_track(self):
        """REMIX 也是赛道：写成「Remix榜中的第'''1'''名」（参 Relay Outer/Iyowa）。"""
        self.assertEqual(
            "本曲参与了[[The VOCALOID Collection]]({{lj|ボカコレ2024冬}})活动"
            "并获得Remix榜中的第'''1'''名。",
            main.collection_sentence("ボカコレ2024冬", "REMIX", "1", [("REMIX", 1)]))

    def test_other_version_without_albums_has_no_album_sentence(self):
        text = main.create_other_version_intro(self._song([]), _other_version())
        self.assertNotIn("收录于专辑", text)

    def test_albums_sentence_matches_the_main_intro(self):
        self.assertEqual("", main.albums_sentence([]))
        self.assertEqual("收录于专辑《'''A'''》和《'''{{lj|B盤}}'''》。",
                         main.albums_sentence(["A", "B盤"]))
        self.assertEqual("本曲收录于专辑《'''A'''》。",
                         main.albums_sentence(["A"], subject=True))

    def test_album_sentence_gets_its_own_subject_when_there_is_no_collection(self):
        """没参加活动时，专辑那句自己带主语：「本曲收录于专辑《…》。」（用户 2026-09 要求）。"""
        song = _song(["初音ミク"])
        song.albums = ["RuLu"]
        intro = main.create_intro(song)
        self.assertIn("本曲收录于专辑《'''RuLu'''》。", intro)

    def test_album_sentence_does_not_repeat_the_subject_after_the_collection(self):
        """有活动那句时主语已经在「本曲参与了…」上，专辑那句不再重复「本曲」。"""
        song = _song(["初音ミク"])
        song.vocaloid_collection = "ボカコレ2024冬"
        song.vocaloid_collection_rank = "45"
        song.albums = ["RuLu"]
        intro = main.create_intro(song)
        self.assertIn("本曲参与了", intro)
        self.assertNotIn("本曲收录于专辑", intro)
        self.assertIn("活动并获得TOP100中的第'''45'''名，收录于专辑《'''RuLu'''》。", intro)

    def test_reprint_uses_the_version_own_upload_site(self):
        """回答「不是 P主 自己提交的」时，写这个版本本身投稿的站点，不把转载说成投稿。"""
        version = _other_version(canonical=False, pv_services="NicoNicoDouga, Youtube")
        text = main.create_other_version_intro(self._song([]), version)
        self.assertIn("于2007年12月28日投稿至[[niconico]]的[[VOCALOID]]日语翻唱歌曲", text)
        self.assertNotIn("bilibili", text)

    def test_remix_is_written_as_adapted(self):
        version = _other_version(song_type="Remix")
        self.assertIn("日语改编歌曲",
                      main.create_other_version_intro(self._song([]), version))

    def test_engines_are_deduped_and_vocalists_joined(self):
        version = _other_version(vocalists=("初音ミク", "鏡音リン"))
        text = main.create_other_version_intro(self._song([]), version)
        self.assertEqual(1, text.count("[[VOCALOID]]"))
        self.assertIn("由[[初音未来]]和[[镜音铃]]演唱。", text)

    def test_unknown_date_still_reads_smoothly(self):
        version = _other_version(publish=date(1970, 1, 1))
        version.video = _video(VideoSite.BILIBILI, identifier=BB_VERSION_1,
                               year=1970, month=1, day=1)
        self.assertIn("是由{{lj|[[じゃがりこP]]}}投稿至[[bilibili]]的",
                      main.create_other_version_intro(self._song([]), version))

    def test_switch_off_never_asks(self):
        cfg = SimpleNamespace(wikitext=SimpleNamespace(other_versions=False))
        with mock.patch.object(main.other_versions, "choose_other_versions") as ask:
            self.assertFalse(cfg.wikitext.other_versions)
        ask.assert_not_called()
