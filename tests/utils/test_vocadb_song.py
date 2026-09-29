"""VocaDB 歌姬名归一化 + 「中文翻译要手动输入」那条问答（回归）。

背景：用户 2026-09 报「生成歌曲 ナ2モノ 的条目时 vocadb 的歌姬名称识别不了」。
排查发现两个问题：
②（用户看到的就是它）中文翻译自动找不到时弹的歌词页**没把歌姬传进去**，
   于是「演唱者上色」面板里一个歌姬按钮都没有，界面只写着
   「（这首歌没有识别出歌姬，无法上色）」，尽管 vocadb 上明明有初音ミク / 巡音ルカ；
① （同类的潜在坑）声库名带后缀（`初音ミク V4X (Original)`）或干脆是拉丁字
   （`KAITO V3 (Unknown)`）时归一化不会做 → 歌姬名整串漏进条目，
   「歌手模板 / XX歌曲分类 / 中文名」全对不上。
"""
import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest import TestCase, mock

from models.creators import Creators, Person
from models.song import Lyrics
from models.video import OtherVersion, Video, VideoSite
from utils import vocadb
from utils import family_template as ft
from utils.name_converter import (engine_from_type as pt_engine_from_type, engines_of,
                                  get_engine, name_shorten, name_to_cat, name_to_chinese,
                                  name_to_wiki)
from utils.string import is_empty


def _artist(name, artist_type="Vocaloid", roles="Default", categories="Vocalist",
            additional_names=""):
    """VocaDB /details 接口里艺术家项的骨架（只留 parse_creators 用到的字段）。"""
    return {"artist": {"name": name, "artistType": artist_type,
                       "additionalNames": additional_names},
            "name": name, "roles": roles, "categories": categories}


class ParseCreatorsVocalistTest(TestCase):
    """歌姬名归一化。"""

    def test_v4x_voice_banks_are_shortened(self):
        """ナ2モノ（vocadb id 588755）真实数据：初音ミク V4X (Original) / 巡音ルカ V4X (Hard)。"""
        creators = vocadb.parse_creators(
            [_artist("初音ミク V4X (Original)", additional_names="Hatsune Miku V4X (Original)"),
             _artist("巡音ルカ V4X (Hard)", additional_names="Megurine Luka V4X (Hard)"),
             _artist("Shu", artist_type="Producer", roles="Composer, Lyricist",
                     categories="Producer")],
            "Shu feat. 初音ミク V4X (Original), 巡音ルカ V4X (Hard)")
        self.assertEqual(["初音ミク", "巡音ルカ"], creators.vocalists_str())
        self.assertEqual(["Shu"], creators.producers_str())

    def test_latin_voice_banks_are_shortened(self):
        """KAITO / MEIKO 在 vocadb 里是拉丁字（初音ミク 那类日文名是另一回事，见上一条）。"""
        self.assertEqual("KAITO", name_shorten("KAITO V3 (Unknown)"))
        self.assertEqual("MEIKO", name_shorten("MEIKO V3 (Power)"))
        creators = vocadb.parse_creators(
            [_artist("KAITO V3 (Unknown)", additional_names="カイトV3 (Unknown)"),
             _artist("鏡音レン")],
            "P feat. 鏡音レン, KAITO V3 (Unknown)")
        self.assertEqual(["KAITO", "鏡音レン"], creators.vocalists_str())

    def test_synthesizer_v_voice_bank_is_shortened(self):
        """《小小星座》(vocadb 588879) 真实数据：Synthesizer V AI Megpoid，artistType=SynthesizerV。

        2026-09-28 用户报「识别歌姬时并没有分辨出 Synthesizer V AI Megpoid 是 Megpoid」：
        旧实现只对 artistType == 'Vocaloid' 的艺术家做归一化，声库全名就整串漏进了条目
        （歌姬栏、歌手分类、引擎全对不上）。
        归一化后还要能各就各位：引擎 = Synthesizer V、`[[分类:Megpoid歌曲]]`、
        链接写成 `[[Megpoid|GUMI]]`（参 voca.wiki《视力检查》）。
        """
        creators = vocadb.parse_creators(
            [_artist("Synthesizer V AI Megpoid", artist_type="SynthesizerV"),
             _artist("花隈千冬", artist_type="SynthesizerV")],
            "Capchii feat. Synthesizer V AI Megpoid, 花隈千冬")
        self.assertEqual(["Megpoid", "花隈千冬"], creators.vocalists_str())
        self.assertEqual("Megpoid", name_to_cat("Megpoid"))
        self.assertEqual("Megpoid|GUMI", name_to_wiki("Megpoid"))
        self.assertEqual("初音未来", name_to_wiki("初音ミク"))

    def test_names_from_the_engine_templates(self):
        """用户 2026-09-30：照 voca.wiki `Category:音声合成软件模板` 里的引擎模板补的对照。

        `utils/engine_characters.py` 里的表用**站上条目名**做键，日文名 → 条目名 的对照
        另外归到 `JAPANESE_ALIASES`（并进 `vocaloid_names`），所以 VocaDB 那边给的
        日文名也能归一化。
        """
        self.assertEqual("春日部紬", name_to_chinese("春日部つむぎ"))
        self.assertEqual("四国玫碳", name_to_chinese("四国めたん"))
        self.assertEqual("伊织弓鹤", name_to_chinese("伊織弓鶴"))
        self.assertEqual("月读爱", name_to_chinese("月読アイ"))
        self.assertEqual("琴叶茜·葵", name_to_chinese("琴葉茜・葵"))
        # 引擎：表里同时写着条目名与日文写法，两种都能命中
        self.assertEqual("VOICEVOX", get_engine("春日部つむぎ"))
        self.assertEqual("VOICEVOX", get_engine("春日部紬"))
        self.assertEqual("VOICEVOX", get_engine("四国めたん"))
        self.assertEqual("VOICEROID", get_engine("伊織弓鶴"))
        self.assertEqual("AISingers", get_engine("嫣汐"))
        # 既有归属不变（新引擎接在 UTAU / CeVIO / Synthesizer V … 后面）
        self.assertEqual("UTAU", get_engine("重音テト"))
        self.assertEqual("Synthesizer V", get_engine("重音テトSV"))
        self.assertEqual("CeVIO", get_engine("可不"))
        self.assertEqual("VOCALOID", get_engine("初音ミク"))
        # 主力是 VOCALOID 的不补日文写法（归一化会认成 CeVIO / VOICEPEAK / SynthV）
        self.assertEqual("VOCALOID", get_engine("結月ゆかり"))
        self.assertEqual("VOCALOID", get_engine("紲星あかり"))
        self.assertEqual("VOCALOID", get_engine("鳴花ヒメ"))
        self.assertEqual("VOCALOID", get_engine("音街ウナ"))
        self.assertEqual("VOCALOID", get_engine("洛天依"))

    def test_vocalists_keep_the_vocadb_artist_type(self):
        """`Person` 上带着 VocaDB 的 `artistType`，引擎就照它认（用户 2026-09-30 要求接上）。

        一个歌姬名下常有好几副声库（ずんだもん 在 VocaDB 上就有 UTAU / VOICEVOX / NEUTRINO
        三条），只有署名里的这个类型能说清**这首**用的是哪副 —— 以前一律靠「拿名字查角色表」，
        于是 ずんだもん 被算成 Synthesizer V（旧 SynthV 表里挂着 `俊达萌`，那条已按模板删掉）。
        """
        creators = vocadb.parse_creators(
            [_artist("ずんだもん", artist_type="VOICEVOX"),
             _artist("紲星あかり", artist_type="Voiceroid")],
            "P feat. ずんだもん, 紲星あかり")
        self.assertEqual([("ずんだもん", "VOICEVOX"), ("紲星あかり", "Voiceroid")],
                         [(person.name, person.artist_type) for person in creators.vocalists])
        self.assertEqual(["VOICEVOX", "VOICEROID"], engines_of(creators.vocalists))
        # 同一首歌的另一副声库 → 引擎也跟着变（VocaDB 就是这么写的）
        utau = vocadb.parse_creators([_artist("ずんだもん", artist_type="UTAU")],
                                     "P feat. ずんだもん")
        self.assertEqual(["UTAU"], engines_of(utau.vocalists))
        # 没有类型的那条路（名字单独拿去查）还是老样子
        self.assertEqual("VOCALOID", get_engine("ずんだもん"))
        self.assertEqual(["VOCALOID"], engines_of(["ずんだもん"]))

    def test_artist_type_is_only_a_hint_where_it_maps(self):
        """认不出引擎的类型（`OtherVoiceSynthesizer` 等）→ 退回角色表。"""
        self.assertEqual("", pt_engine_from_type("OtherVoiceSynthesizer"))
        self.assertEqual("", pt_engine_from_type("Unknown"))
        self.assertEqual("VOICEVOX", get_engine("四国玫碳", "OtherVoiceSynthesizer"))
        self.assertEqual("New Type", get_engine("初音ミク", "NewType"))
        self.assertEqual("ACE", get_engine("小夜", "ACEVirtualSinger"))

    def test_vocalists_fall_back_to_artist_string(self):
        """一个 Vocalist 角色都没标时，退回 artistString 里 feat. 后面那串（开头那个空串要滤掉）。"""
        creators = vocadb.parse_creators(
            [_artist("Shu", artist_type="Producer", roles="Producer", categories="Producer")],
            "Shu feat. 初音ミク, 巡音ルカ")
        self.assertEqual(["初音ミク", "巡音ルカ"], creators.vocalists_str())
        self.assertEqual(["Shu"], creators.producers_str())

    def test_artist_string_without_feat_does_not_crash(self):
        """artistString 里没有 feat.（或只有前半）时，歌姬那半截算空，不能抛 IndexError。"""
        creators = vocadb.parse_creators(
            [_artist("Shu", artist_type="Producer", roles="Producer", categories="Producer")],
            "Shu")
        self.assertEqual([], creators.vocalists_str())
        self.assertEqual(["Shu"], creators.producers_str())


class ParseOtherVersionsTest(TestCase):
    """`alternateVersions` → 同一首歌的其他版本（参 voca.wiki《鸟之诗》）。

    真实数据（vocadb id 165696 的 alternateVersions 之一）：
    {"id": 629, "name": "鳥の詩", "songType": "Cover",
     "artistString": "でんげん feat. 初音ミク", "publishDate": "2007-09-01T00:00:00Z"}
    这些条目里**没有** artists 列表，P主 / 歌姬 只能从 artistString 里按 feat. 切。
    """

    def test_fields_are_parsed(self):
        versions = vocadb.parse_other_versions([{
            "id": 629, "name": "鳥の詩", "songType": "Cover",
            "artistString": "じゃがりこP feat. 鏡音リン",
            "pvServices": "NicoNicoDouga, Youtube",
            "publishDate": "2007-12-28T00:00:00Z"}])
        self.assertEqual(1, len(versions))
        version = versions[0]
        self.assertEqual(629, version.version_id)
        self.assertEqual("Cover", version.song_type)
        self.assertEqual(["じゃがりこP"], version.producers)
        self.assertEqual(["鏡音リン"], version.vocalists)
        self.assertEqual("NicoNicoDouga, Youtube", version.pv_services)
        self.assertEqual(date(2007, 12, 28), version.publish_date.date())

    def test_voice_bank_suffixes_are_shortened(self):
        """和主条目的 parse_creators 一致：`初音ミク V4X (Original)` → `初音ミク`。"""
        versions = vocadb.parse_other_versions([{
            "id": 1, "songType": "Cover",
            "artistString": "P feat. 初音ミク V4X (Original)"}])
        self.assertEqual(["初音ミク"], versions[0].vocalists)

    def test_missing_fields_do_not_crash(self):
        versions = vocadb.parse_other_versions([{"id": 1, "artistString": ""}, {}])
        self.assertEqual(2, len(versions))
        self.assertEqual([], versions[0].vocalists)
        self.assertIsNone(versions[1].publish_date)

    def test_empty_list(self):
        self.assertEqual([], vocadb.parse_other_versions(None))
        self.assertEqual([], vocadb.parse_other_versions([]))


class ParseAlbumsTest(TestCase):
    """收录专辑：**同名单曲不写**（专辑名 = 歌曲原名，且整张专辑只收录本曲）。

    实测（ナ2モノ，songId 588755）：VocaDB 上挂着两张碟 ——
    「ナ2 モノ」(Single，只有它一首) 与「ボカMIX -VocaTECH & EDM Selection-」(Compilation，61 首)；
    前者写进简介等于没说，所以丢掉。
    """

    SONG_NAMES = ["ナ2モノ", "ナ2モノ"]
    SONG_ID = 588755

    def _album(self, name="ナ2モノ", album_id=41700, disc_type="Single", api_name=None):
        return {"id": album_id, "defaultName": name, "name": api_name or name,
                "discType": disc_type}

    def _tracks_response(self, song_ids):
        return mock.Mock(text=json.dumps(
            {"tracks": [{"song": {"id": song_id}} for song_id in song_ids]}))

    def test_album_named_like_the_song_with_only_this_song_is_dropped(self):
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._tracks_response([self.SONG_ID])):
            albums = vocadb.parse_albums([self._album()], self.SONG_NAMES, self.SONG_ID)
        self.assertEqual([], albums)

    def test_same_name_album_with_more_songs_is_kept(self):
        """同名专（主打歌就是本曲）要留 —— 只有单曲碟才丢。"""
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._tracks_response([self.SONG_ID, 123])):
            albums = vocadb.parse_albums([self._album()], self.SONG_NAMES, self.SONG_ID)
        self.assertEqual(["ナ2モノ"], albums)

    def test_spacing_in_the_album_name_does_not_matter(self):
        """VocaDB 上这张碟的 name 写作「ナ2 モノ」（多一个空格），defaultName 才是「ナ2モノ」。"""
        album = self._album(api_name="ナ2 モノ")
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._tracks_response([self.SONG_ID])):
            albums = vocadb.parse_albums([album], self.SONG_NAMES, self.SONG_ID)
        self.assertEqual([], albums)

    def test_different_album_is_kept_without_asking_for_its_tracks(self):
        album = self._album(name="ボカMIX -VocaTECH & EDM Selection-", album_id=44390,
                            disc_type="Compilation")
        with mock.patch.object(vocadb, "http_get") as http_get:
            albums = vocadb.parse_albums([album], self.SONG_NAMES, self.SONG_ID)
        self.assertEqual(["ボカMIX -VocaTECH & EDM Selection-"], albums)
        http_get.assert_not_called()

    def test_failed_track_lookup_keeps_the_album(self):
        """拿不到曲目时保守处理：宁可能多写一张碟，也不要凭猜测丢。"""
        with mock.patch.object(vocadb, "http_get", side_effect=RuntimeError("boom")):
            albums = vocadb.parse_albums([self._album()], self.SONG_NAMES, self.SONG_ID)
        self.assertEqual(["ナ2モノ"], albums)

    def test_without_song_id_nothing_is_dropped(self):
        with mock.patch.object(vocadb, "http_get") as http_get:
            albums = vocadb.parse_albums([self._album()], self.SONG_NAMES, 0)
        self.assertEqual(["ナ2モノ"], albums)
        http_get.assert_not_called()

    def test_missing_fields_do_not_crash(self):
        self.assertEqual([], vocadb.parse_albums(None))
        self.assertEqual([""], vocadb.parse_albums([{}]))


class GetVersionDetailsTest(TestCase):
    """其他版本自己的详情：它在 nico / YouTube 上的稿件 + 收录它的专辑。

    实测（ナ2モノ (ROCK_VER)，id 770801）：`alternateVersions` 里只有 `pvServices`，
    稿件 ID 与专辑都要另外请求这个版本的 /details 才有。
    """

    def _song(self):
        return SimpleNamespace(name_jap="ナ2モノ", name_chs="ナ2モノ", name_other=[])

    def _version(self, version_id=770801):
        return OtherVersion(version_id=version_id, name="ナ2モノ (ROCK_VER)",
                            publish_date=datetime(2025, 3, 31))

    def _response(self, pvs=(), albums=(), release_events=()):
        response = mock.Mock()
        response.text = json.dumps({"pvs": list(pvs), "albums": list(albums),
                                    "releaseEvents": list(release_events)})
        return response

    def test_nico_and_youtube_pvs_are_written_into_the_version(self):
        payload = [{"service": "Youtube", "pvType": "Original",
                    "url": "https://youtu.be/4FEzamGv7tM"},
                   {"service": "NicoNicoDouga", "pvType": "Original",
                    "url": "https://www.nicovideo.jp/watch/sm44829675"},
                   # B 站那份由用户填（OtherVersion.video），不能重复
                   {"service": "Bilibili", "pvType": "Original",
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD"}]
        asked = []

        def fetch(site, url, canonical=True):
            asked.append(site)
            return Video(site, url.rsplit("/", 1)[-1], "", 0, datetime(2025, 3, 31))

        version = self._version()
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._response(pvs=payload)) as http_get, \
             mock.patch.object(vocadb, "video_from_site", side_effect=fetch):
            vocadb.get_version_details(self._song(), version)
        self.assertEqual({VideoSite.NICO_NICO, VideoSite.YOUTUBE},
                         {video.site for video in version.videos})
        self.assertEqual([VideoSite.YOUTUBE, VideoSite.NICO_NICO], asked)
        self.assertIn("/api/songs/770801/details", http_get.call_args.args[0])

    def test_albums_are_written_into_the_version(self):
        album = {"id": 44390, "defaultName": "after EXCURSION",
                 "discType": "Compilation"}
        version = self._version(version_id=617066)
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._response(albums=[album])):
            vocadb.get_version_details(self._song(), version)
        self.assertEqual(["after EXCURSION"], version.albums)

    def test_release_events_are_detected_and_the_template_is_read(self):
        """活动按**版本**检测（VocaDB 的 releaseEvents，实测 589198 就参加了 ボカコレ2024冬）；
        赛道 / 名次 VocaDB 上没有 → 爬那一届的活动模板（见 family_template.read_collection_places）。"""
        release_events = [{"name": "#コンパスアニメ曲エントリー"},
                          {"name": "The VOCALOID Collection 2024 Winter"}]
        version = self._version(version_id=589198)
        places = [ft.CollectionPlace("TOP100", 3, "TOP100 → 1-10位")]
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._response(release_events=release_events)), \
             mock.patch.object(vocadb.family_template, "find_collection_places",
                               return_value=places) as read, \
             mock.patch.object(vocadb, "prompt_vocaloid_collection_details") as ask:
            vocadb.get_version_details(self._song(), version)
        self.assertEqual("ボカコレ2024冬", version.vocaloid_collection)
        self.assertEqual("TOP100", version.vocaloid_collection_track)
        self.assertEqual("3", version.vocaloid_collection_rank)
        self.assertEqual([("TOP100", 3)], version.vocaloid_collection_places)
        self.assertEqual("The VOCALOID Collection2024冬", read.call_args.args[0])
        ask.assert_not_called()

    def test_unranked_when_the_song_is_in_no_track(self):
        """模板读到了但两榜都没有（含列在 REMIX / 未上榜歌曲里）→ 榜外，不写名次。"""
        release_events = [{"name": "The VOCALOID Collection 2024 Winter"}]
        version = self._version(version_id=589198)
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._response(release_events=release_events)), \
             mock.patch.object(vocadb.family_template, "find_collection_places",
                               return_value=[]), \
             mock.patch.object(vocadb, "prompt_vocaloid_collection_details") as ask:
            vocadb.get_version_details(self._song(), version)
        self.assertEqual("榜外", version.vocaloid_collection_track)
        self.assertIsNone(version.vocaloid_collection_rank)
        self.assertEqual([], version.vocaloid_collection_places)
        ask.assert_not_called()

    def test_template_failure_falls_back_to_asking(self):
        """活动模板取不到（不存在 / 网络失败）时才问用户，并只拿到一个赛道。"""
        release_events = [{"name": "The VOCALOID Collection 2024 Winter"}]
        version = self._version(version_id=589198)
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._response(release_events=release_events)), \
             mock.patch.object(vocadb.family_template, "find_collection_places",
                               return_value=None), \
             mock.patch.object(vocadb, "prompt_vocaloid_collection_details",
                               return_value=("ROOKIE", "7")) as ask:
            vocadb.get_version_details(self._song(), version)
        self.assertEqual("ボカコレ2024冬", ask.call_args.args[0])
        self.assertEqual("ROOKIE", version.vocaloid_collection_track)
        self.assertEqual("7", version.vocaloid_collection_rank)
        self.assertEqual([("ROOKIE", 7)], version.vocaloid_collection_places)

    def test_without_a_release_event_nothing_is_asked(self):
        version = self._version()
        with mock.patch.object(vocadb, "http_get", return_value=self._response()), \
             mock.patch.object(vocadb, "prompt_vocaloid_collection_details") as ask:
            vocadb.get_version_details(self._song(), version)
        self.assertEqual("", version.vocaloid_collection)
        ask.assert_not_called()

    def test_dates_fall_back_to_the_vocadb_publish_date(self):
        """站点取不到日期（epoch）时用这个版本的 publishDate，不然会写出 1970 年。"""
        epoch = Video(VideoSite.NICO_NICO, "sm44829675", "", 0, datetime(1970, 1, 1))
        version = self._version()
        with mock.patch.object(vocadb, "http_get", return_value=self._response(
                pvs=[{"service": "NicoNicoDouga", "pvType": "Original",
                      "url": "https://www.nicovideo.jp/watch/sm44829675"}])), \
             mock.patch.object(vocadb, "video_from_site", return_value=epoch):
            vocadb.get_version_details(self._song(), version)
        self.assertEqual(datetime(2025, 3, 31), version.videos[0].uploaded)

    def test_no_version_id_makes_no_request(self):
        with mock.patch.object(vocadb, "http_get") as http_get:
            vocadb.get_version_details(self._song(), OtherVersion())
        http_get.assert_not_called()

    def test_request_failure_keeps_the_version_usable(self):
        """VocaDB 抽风时只当这个版本没有 nico / yt 稿件与专辑，不影响生成。"""
        version = self._version()
        with mock.patch.object(vocadb, "http_get", side_effect=RuntimeError("boom")):
            vocadb.get_version_details(self._song(), version)
        self.assertEqual([], version.videos)
        self.assertEqual([], version.albums)


class ArtistAliasesTest(TestCase):
    """按名字查 VocaDB 艺术家的别名（拿 P主 罗马音用，实测 Ar/23981：雄之助 → Yunosuke）。"""

    def _response(self, items):
        response = mock.Mock()
        response.json.return_value = {"items": items}
        return response

    def test_aliases_are_returned(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._response(
                [{"id": 23981, "name": "雄之助",
                  "additionalNames": "Yunosuke, 유노스케"}])) as http_get:
            self.assertEqual(["Yunosuke", "유노스케"], vocadb.artist_aliases("雄之助"))
        # ⚠️ 不带 fields=AdditionalNames 时，接口返回的 additionalNames 是 null
        self.assertEqual("AdditionalNames", http_get.call_args.kwargs["params"]["fields"])
        self.assertEqual("雄之助", http_get.call_args.kwargs["params"]["query"])

    def test_exact_name_wins(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._response([
                {"name": "雄之助(カバー)", "additionalNames": "CoverP"},
                {"name": "雄之助", "additionalNames": "Yunosuke"}])):
            self.assertEqual(["Yunosuke"], vocadb.artist_aliases("雄之助"))

    def test_missing_or_failed_lookup_returns_empty(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._response(
                [{"name": "雄之助", "additionalNames": None}])):
            self.assertEqual([], vocadb.artist_aliases("雄之助"))
        with mock.patch.object(vocadb, "http_get", return_value=self._response([])):
            self.assertEqual([], vocadb.artist_aliases("不存在的P主"))
        with mock.patch.object(vocadb, "http_get", side_effect=RuntimeError("boom")):
            self.assertEqual([], vocadb.artist_aliases("雄之助"))
        self.assertEqual([], vocadb.artist_aliases(""))


class ManualTranslationPromptTest(TestCase):
    """中文翻译找不到 → 问「要不要手动输入」→ 开歌词页。"""

    def _creators(self):
        return Creators(producers=[Person("Shu")],
                        vocalists=[Person("初音ミク"), Person("巡音ルカ")], staffs={})

    def _config(self, fail_fast=False):
        return mock.patch.object(
            vocadb, "get_config",
            return_value=SimpleNamespace(
                wikitext=SimpleNamespace(lyrics_chs_fail_fast=fail_fast)))

    def test_lyrics_editor_gets_the_vocalists(self):
        """歌词页要拿到 vocadb 认出来的歌姬，不然「演唱者上色」是空的、没法标谁唱哪句。"""
        with self._config(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=1) as choices, \
             mock.patch.object(vocadb, "get_manual_lyrics", return_value=Lyrics()) as manual:
            vocadb.prompt_manual_translation(self._creators())
        self.assertEqual(["初音ミク", "巡音ルカ"], manual.call_args.kwargs["charas"])
        self.assertEqual([vocadb._("Yes"), vocadb._("No")], choices.call_args.args[1],
                         "选项仍走 i18n")

    def test_answering_no_skips_the_editor(self):
        with self._config(), \
             mock.patch.object(vocadb, "prompt_choices", return_value=2), \
             mock.patch.object(vocadb, "get_manual_lyrics") as manual:
            lyrics = vocadb.prompt_manual_translation(self._creators())
        manual.assert_not_called()
        self.assertTrue(is_empty(lyrics.lyrics_jap))

    def test_fail_fast_skips_the_prompt(self):
        """配置里开了 lyrics_chs_fail_fast：直接放弃，既不问也不弹歌词页。"""
        with self._config(fail_fast=True), \
             mock.patch.object(vocadb, "prompt_choices") as choices, \
             mock.patch.object(vocadb, "get_manual_lyrics") as manual:
            vocadb.prompt_manual_translation(self._creators())
        choices.assert_not_called()
        manual.assert_not_called()
