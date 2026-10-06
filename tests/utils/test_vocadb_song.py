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
import os
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

# ⚠️ 跑测时**绝不能**真的去开浏览器取数（用户 2026-10-04：「测试的时候经常卡死」
# —— 之前真去起过 Chrome/WebEngine，一卡就是几十秒）。这里在模块导入时就关掉。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["VOCAWIKI_NO_BROWSER_FETCH"] = "1"

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


class CacheIsolationMixin:
    """把 VocaDB 的本地缓存 / 限速状态 / 「被挡」冷却都隔离掉。

    两件事必须做：
    * `_details_payload` 会先查缓存、拿到就写回 —— 不隔离的话测试会去读写真工作区的
      `output/vocadb_cache.json`，还会被上次跑测留下的内容污染（「明明 mock 了却一次请求都没发」）；
    * 没 mock 到的那次真实请求撞上 Cloudflare 后会设一个 10 分钟的冷却，
      后面的测试全变成 `VocadbBlocked`（第一次这么改就踩了：8 个看起来毫不相干的用例一起挂）。
    """

    def _isolate_cache(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        cache = mock.patch.object(vocadb, "_cache_path",
                                  return_value=Path(folder) / "vocadb_cache.json")
        cache.start()
        self.addCleanup(cache.stop)
        vocadb._cache = None
        self.addCleanup(setattr, vocadb, "_cache", None)
        vocadb._last_request = 0.0
        vocadb._daily_warned = False
        vocadb.reset_block()
        self.addCleanup(vocadb.reset_block)


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


class ParseAlbumsTest(CacheIsolationMixin, TestCase):
    """收录专辑：**同名单曲不写**（专辑名 = 歌曲原名，且整张专辑只收录本曲）。

    实测（ナ2モノ，songId 588755）：VocaDB 上挂着两张碟 ——
    「ナ2 モノ」(Single，只有它一首) 与「ボカMIX -VocaTECH & EDM Selection-」(Compilation，61 首)；
    前者写进简介等于没说，所以丢掉。
    """

    SONG_NAMES = ["ナ2モノ", "ナ2モノ"]
    SONG_ID = 588755

    def setUp(self):
        self._isolate_cache()

    def _album(self, name="ナ2モノ", album_id=41700, disc_type="Single", api_name=None):
        return {"id": album_id, "defaultName": name, "name": api_name or name,
                "discType": disc_type}

    def _tracks_response(self, song_ids):
        return mock.Mock(status_code=200, text=json.dumps(
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


class GetVersionDetailsTest(CacheIsolationMixin, TestCase):
    """其他版本自己的详情：它在 nico / YouTube 上的稿件 + 收录它的专辑。

    实测（ナ2モノ (ROCK_VER)，id 770801）：`alternateVersions` 里只有 `pvServices`，
    稿件 ID 与专辑都要另外请求这个版本的 /details 才有。

    ⚠️ 这里 mock 的是 `vocadb_get`（不是 `http_get`）：自 2026-10-03 起所有发往 vocadb 的
    请求都走那个入口（自定义 UA + 限速 + 被挡时给提示），只 mock `http_get` 会真的联网。
    """

    def setUp(self):
        self._isolate_cache()

    def _song(self):
        return SimpleNamespace(name_jap="ナ2モノ", name_chs="ナ2モノ", name_other=[])

    def _version(self, version_id=770801):
        return OtherVersion(version_id=version_id, name="ナ2モノ (ROCK_VER)",
                            publish_date=datetime(2025, 3, 31))

    def _response(self, pvs=(), albums=(), release_events=()):
        response = mock.Mock()
        # ⚠️ 必须给 status_code：`vocadb_get()` 会先用 `_is_challenge()` 看是不是 Cloudflare
        # 的挑战页，而 `int(mock.Mock())` 会直接 TypeError（不在那里吞掉就会把整个调用炸掉）。
        response.status_code = 200
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


class ArtistAliasesTest(CacheIsolationMixin, TestCase):
    """按名字查 VocaDB 艺术家的别名（拿 P主 罗马音用，实测 Ar/23981：雄之助 → Yunosuke）。"""

    def setUp(self):
        self._isolate_cache()


    def _response(self, items):
        response = mock.Mock()
        response.status_code = 200        # 见 GetVersionDetailsTest._response 里的说明
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


class VocadbBlockedTest(CacheIsolationMixin, TestCase):
    """VocaDB 被 Cloudflare 人机校验挡住时（用户 2026-10-03 贴的 403 堆栈）。

    实测：浏览器 UA / 工具 UA / 无 UA 三种都是 `403 + Just a moment...`，
    所以**改 UA 没用**。这里保证的是「别把整个生成流程带堆栈打断」：
    重试一次 → 还不行就抛一句能看懂的原因，并且短时间内不再连环请求。
    """

    def setUp(self):
        vocadb.reset_block()
        self.addCleanup(vocadb.reset_block)
        # ⚠️ `patch(...).start()` 返回的是 mock 本身；真实配置要自己造一个（改它的
        # `proxies` 就等于「用户在 config.yaml 里换了代理」）。
        self.config = SimpleNamespace(proxies=None)
        self.proxy = mock.patch.object(vocadb, "get_config", return_value=self.config)
        self.proxy.start()
        self.addCleanup(self.proxy.stop)
        self._isolate_cache()

    @staticmethod
    def _challenge(status=403):
        return SimpleNamespace(status_code=status, text="<!DOCTYPE html><title>Just a moment...</title>",
                               raise_for_status=lambda: None)

    @staticmethod
    def _ok(payload='{"items": []}'):
        response = SimpleNamespace(status_code=200, text=payload)
        response.raise_for_status = lambda: None
        return response

    def test_challenge_is_retried_once_then_reported(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()) as get, \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked) as raised:
                vocadb.search_vocadb("メルト", {"start": 0})
        self.assertEqual(2, get.call_count)                     # 重试了一次
        self.assertIn("Cloudflare", str(raised.exception))

    def test_a_retry_that_succeeds_is_used(self):
        responses = [self._challenge(), self._ok('{"items": [{"defaultName": "メルト"}]}')]
        with mock.patch.object(vocadb, "http_get", side_effect=responses), \
                mock.patch.object(vocadb.time, "sleep"):
            self.assertEqual([{"defaultName": "メルト"}], vocadb.search_vocadb("メルト", {}))

    def test_blocked_calls_are_skipped_for_a_while(self):
        """挡过一次之后短时间内不再去碰 VocaDB（一个歌名一次搜索会连环 403）。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("メルト", {})
        with mock.patch.object(vocadb, "http_get") as get:
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("またね", {})
        get.assert_not_called()

    def test_switching_the_proxy_clears_the_cooldown(self):
        """用户 2026-10-03：「挡过一次后如果我切换 IP 就不要继续拦我十分钟」。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("メルト", {})
        self.config.proxies = "http://127.0.0.1:7897"            # 换了出口
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._ok('{"items": []}')) as get:
            self.assertEqual([], vocadb.search_vocadb("メルト", {}))
        self.assertEqual(1, get.call_count)                      # 冷却作废、真的重新请求了

    def test_a_probe_is_allowed_every_minute(self):
        """配置没变（靠 VPN 换 IP）时：冷却期内每分钟放一次试探，恢复就继续用。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("メルト", {})
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._ok('{"items": []}')) as get, \
                mock.patch.object(vocadb.time, "time",
                                  return_value=vocadb._next_probe + 1):
            self.assertEqual([], vocadb.search_vocadb("メルト", {}))
        self.assertEqual(1, get.call_count)
        # 试探完紧接着再调：又回到冷却里，不再请求
        with mock.patch.object(vocadb, "http_get", return_value=self._ok('{"items": []}')) as get:
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("またね", {})
        get.assert_not_called()

    def test_reset_block_forces_a_retry(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("メルト", {})
        vocadb.reset_block()
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._ok('{"items": []}')) as get:
            self.assertEqual([], vocadb.search_vocadb("メルト", {}))
        self.assertEqual(1, get.call_count)

    def test_browser_cookie_is_sent_when_configured(self):
        """`vocadb_cookie`：把浏览器里过掉 Cloudflare 后的 Cookie 复用到请求上。"""
        self.assertEqual({}, {k: v for k, v in vocadb._vocadb_headers().items()
                             if k != "User-Agent"})              # 没配就不带 Cookie
        self.config.vocadb_cookie = "cf_clearance=abc; __cf_bm=xyz"
        self.assertEqual("cf_clearance=abc; __cf_bm=xyz",
                         vocadb._vocadb_headers()["Cookie"])
        with mock.patch.object(vocadb, "http_get",
                               return_value=self._ok('{"items": []}')) as get:
            vocadb.search_vocadb("メルト", {})
        self.assertEqual("cf_clearance=abc; __cf_bm=xyz",
                         get.call_args.kwargs["headers"]["Cookie"])

    def test_not_a_challenge_still_raises_http_error(self):
        """普通 500 之类的照旧走 raise_for_status()（不是 Cloudflare 的锅就别吞掉）。"""
        def boom():
            raise RuntimeError("500 Server Error")

        response = SimpleNamespace(status_code=500, text="", raise_for_status=boom)
        with mock.patch.object(vocadb, "http_get", return_value=response):
            with self.assertRaises(RuntimeError):
                vocadb.search_vocadb("メルト", {})


class ManualJsonPasteTest(CacheIsolationMixin, TestCase):
    """VocaDB 被挡住时的兜底：手动粘 `/api/songs/{id}/details` 的 JSON（用户 2026-10-03）。

    浏览器里 `https://vocadb.net/api/songs/<id>/details` 往往能正常打开，
    把那段 JSON 复制进工具就能继续生成，不必让工具自己去过校验。
    """

    SONG_JSON = ('{"id": 588755, "defaultName": "ナ2モノ", "artistString": "初音ミク",'
                 ' "artists": [], "pvs": [], "albums": [], "lyricsFromParents": []}')

    def setUp(self):
        self._isolate_cache()
        self.config = SimpleNamespace(vocadb_manual_url=False)
        patcher = mock.patch.object(vocadb, "get_config", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_link_and_a_bare_id_are_both_accepted(self):
        self.assertEqual(("12345", None),
                         vocadb.parse_manual_song_reply("https://vocadb.net/S/12345"))
        self.assertEqual(("588755", None), vocadb.parse_manual_song_reply("588755"))

    def test_pasted_json_is_recognised(self):
        song_id, payload = vocadb.parse_manual_song_reply(self.SONG_JSON)
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", payload["defaultName"])

    def test_junk_is_rejected(self):
        """粘错东西（不是歌曲 JSON）就当没填，别拿半个 dict 继续往下跑。"""
        self.assertEqual((None, None), vocadb.parse_manual_song_reply(""))
        self.assertEqual((None, None), vocadb.parse_manual_song_reply("{不是 JSON"))
        self.assertEqual((None, None), vocadb.parse_manual_song_reply('{"foo": 1}'))

    def test_a_real_details_payload_keeps_its_id(self):
        """⚠️ 真 /details 的顶层**没有** id / name，它们在 `song` 里（2026-10-03 实测发现）。

        只读顶层 `id` 的话，用户把真 JSON 粘进来会被当成「没填」→ 白粘一次。
        """
        real = ('{"artists": [], "artistString": "Shu feat. 初音ミク", "pvs": [],'
                ' "song": {"id": 588755, "defaultName": "ナ2モノ", "name": "ナ2モノ"}}')
        song_id, payload = vocadb.parse_manual_song_reply(real)
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", payload["song"]["defaultName"])

    def test_a_real_details_payload_is_cached_under_its_name(self):
        """粘一份真形状的 JSON → 拿到 id、按歌名记下，下次不用再粘。"""
        real = ('{"artists": [], "pvs": [], "song": {"id": 588755, "defaultName": "ナ2モノ"}}')
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[real]) as multiline, \
                mock.patch.object(vocadb, "vocadb_get") as get:
            song_id, payload = vocadb._details_payload("ナ2モノ")
            again = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("588755", again[0])
        self.assertEqual(1, multiline.call_count)
        get.assert_not_called()

    def test_the_prompt_never_shows_a_placeholder_url(self):
        """用户 2026-10-03 报的：把提示里的 `https://vocadb.net/api/songs/<歌曲ID>/details`
        当网址打开 → 跳到 `https://vocadb.net/Error?code=404`。

        所以提示里给 **真实能打开的地址**：已知 id 就用这条，未知 id 就给例子 + 说明。
        """
        with mock.patch.object(vocadb, "prompt_multiline", return_value=[]) as multiline:
            vocadb.prompt_manual_song_json("588755")
            known = multiline.call_args.args[0]
            vocadb.prompt_manual_song_json()
            example = multiline.call_args.args[0]
        self.assertIn("https://vocadb.net/api/songs/588755/details", known)
        self.assertIn(vocadb.VOCADB_JSON_EXAMPLE_URL, example)
        for text in (known, example):
            self.assertNotIn("<", text.replace("->", ""))       # 没有任何尖括号占位符
            self.assertNotIn(">", text.replace("->", ""))

    def test_pasting_the_error_page_says_so(self):
        """粘了 404 错误页 / 网页源码（用户实际踩的坑）→ 明确说是网页而不是 JSON。"""
        with mock.patch.object(vocadb, "prompt_multiline",
                               side_effect=[["<html><title>Error</title> Error?code=404"], []]), \
                mock.patch.object(vocadb.logging, "warning") as warning:
            self.assertIsNone(vocadb.prompt_manual_song_json("588755"))
        self.assertIn(vocadb._("vocadb_json_is_webpage"), warning.call_args.args[1])

    def test_pasting_a_link_says_so(self):
        with mock.patch.object(vocadb, "prompt_multiline",
                               side_effect=[["https://vocadb.net/S/588755"], []]), \
                mock.patch.object(vocadb.logging, "warning") as warning:
            self.assertIsNone(vocadb.prompt_manual_song_json())
        self.assertIn(vocadb._("vocadb_json_is_link"), warning.call_args.args[1])

    def test_being_blocked_at_the_details_step_gives_the_exact_url(self):
        """搜到了 id、取详情时才被挡（用户就是在这个阶段去手动下载 JSON 的）：
        这时把**完整地址**给出来，他不用自己拼 ID。"""
        real = ('{"artists": [], "pvs": [], "song": {"id": 588755, "defaultName": "ナ2モノ"}}')
        with mock.patch.object(vocadb, "search_song_id", return_value="588755"), \
                mock.patch.object(vocadb, "vocadb_get",
                                  side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[real]) as multiline:
            song_id, payload = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", payload["song"]["defaultName"])
        self.assertIn("/api/songs/588755/details", multiline.call_args.args[0])

    def test_being_blocked_at_the_details_step_and_declining(self):
        with mock.patch.object(vocadb, "search_song_id", return_value="588755"), \
                mock.patch.object(vocadb, "vocadb_get",
                                  side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=2), \
                mock.patch.object(vocadb, "prompt_multiline") as multiline:
            song_id, payload = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)      # id 是知道的，只是没拿到详情
        self.assertIsNone(payload)
        multiline.assert_not_called()

    # —— 用户 2026-10-03 贴的那张图：他粘的是**搜索结果**那段 JSON ——
    # 截图里的日志正是 `{"items":[..., "defaultName":"cold death", "pvServices":..., "tags":...]}`
    # 那种搜索结果的形状；以前一律当成「不是歌曲 JSON」丢掉，明明里面有 id。
    SEARCH_JSON = ('{"items": [{"id": 667990, "defaultName": "cold death",'
                   ' "artistString": "鬱P feat. 宮舞モカ", "pvServices": "NicoNicoDouga, Youtube, Bilibili"},'
                   ' {"id": 111, "defaultName": "cold death (别的版本)"}]}')

    DETAILS_JSON = ('{"artists": [], "pvs": [], "artistString": "鬱P feat. 宮舞モカ",'
                    ' "song": {"id": 667990, "defaultName": "cold death"}}')

    def test_pasted_search_results_give_the_id(self):
        """粘搜索结果 → 挑出同名那一项（`defaultName` 完全相同）→ 拿到 id 去取详情。"""
        payload = SimpleNamespace(text=self.DETAILS_JSON)
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[self.SEARCH_JSON]), \
                mock.patch.object(vocadb, "vocadb_get", return_value=payload) as get:
            song_id, response = vocadb._details_payload("cold death")
        self.assertEqual("667990", song_id)
        self.assertEqual("鬱P feat. 宮舞モカ", response["artistString"])
        self.assertIn("/api/songs/667990/details", get.call_args.args[0])
        self.assertEqual("667990", vocadb.cached_song_id("cold death"))   # 下次不用再粘

    def test_pasted_search_results_then_the_details_url_is_handed_over(self):
        """粘搜索结果拿到 id 后去取详情又被挡 → 这次给的是**带那个 id 的完整地址**。"""
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "vocadb_get",
                                  side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  side_effect=[[self.SEARCH_JSON], [self.DETAILS_JSON]]) as multiline:
            song_id, payload = vocadb._details_payload("cold death")
        self.assertEqual("667990", song_id)
        self.assertEqual("cold death", payload["song"]["defaultName"])
        second_prompt = multiline.call_args_list[1].args[0]
        self.assertIn("https://vocadb.net/api/songs/667990/details", second_prompt)

    def test_a_single_search_item_is_not_mistaken_for_details(self):
        """一项搜索结果（顶层 `defaultName` + `pvs`，但没有 `artists`）**不能当详情用**：
        以前光看 `defaultName`/`pvs` 就放行，`get_song_by_name()` 随后在
        `response['artists']` 上 KeyError。"""
        item = ('{"id": 667990, "defaultName": "cold death", "pvs": [], "urls": []}')
        self.assertFalse(vocadb.is_details_payload(json.loads(item)))
        self.assertTrue(vocadb.is_details_payload(json.loads(self.DETAILS_JSON)))

    def test_pasting_search_results_twice_gives_up(self):
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "vocadb_get",
                                  side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[self.SEARCH_JSON]):
            song_id, payload = vocadb._details_payload("cold death")
        self.assertEqual("667990", song_id)
        self.assertIsNone(payload)               # 不再无限问下去

    def test_blocked_then_pasted_json_needs_no_further_request(self):
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[self.SONG_JSON]), \
                mock.patch.object(vocadb, "vocadb_get") as get:
            song_id, payload = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", payload["defaultName"])
        get.assert_not_called()                          # 已经有 JSON 了，不再联网

    def test_blocked_and_declined_gives_up(self):
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=2), \
                mock.patch.object(vocadb, "vocadb_get") as get:
            self.assertEqual((None, None), vocadb._details_payload("ナ2モノ"))
        get.assert_not_called()

    def test_pasted_a_wrong_json_then_declined(self):
        """粘错一次 → 再给一次机会 → 留空就放弃。"""
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1), \
                mock.patch.object(vocadb, "prompt_multiline",
                                  side_effect=[["随便一段话"], []]) as multiline:
            self.assertEqual((None, None), vocadb._details_payload("ナ2モノ"))
        self.assertEqual(2, multiline.call_count)

    def test_manual_url_switch_still_works(self):
        """`vocadb_manual_url`（既有的开关）仍能手动输链接 / 直接粘 JSON。"""
        self.config.vocadb_manual_url = True
        payload = SimpleNamespace(text=self.SONG_JSON)
        with mock.patch.object(vocadb, "search_song_id", return_value=None), \
                mock.patch.object(vocadb, "prompt_response", return_value="588755"), \
                mock.patch.object(vocadb, "vocadb_get", return_value=payload) as get:
            song_id, response = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", response["defaultName"])
        self.assertIn("/api/songs/588755/details", get.call_args.args[0])

    def test_manual_url_accepts_pasted_json_too(self):
        """单行那一栏粘 JSON 也认（`parse_manual_song_reply` 两种都吃）。"""
        self.config.vocadb_manual_url = True
        with mock.patch.object(vocadb, "search_song_id", return_value=None), \
                mock.patch.object(vocadb, "prompt_response", return_value=self.SONG_JSON), \
                mock.patch.object(vocadb, "vocadb_get") as get:
            song_id, response = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", response["defaultName"])
        get.assert_not_called()

    def test_the_normal_path_is_unchanged(self):
        """没被挡住时照旧：搜 id → 取 /details。"""
        payload = SimpleNamespace(text=self.SONG_JSON)
        with mock.patch.object(vocadb, "search_song_id", return_value="588755"), \
                mock.patch.object(vocadb, "vocadb_get",
                                  return_value=payload) as get:
            song_id, response = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", response["defaultName"])
        self.assertIn("/api/songs/588755/details", get.call_args.args[0])

    def test_a_known_id_skips_the_search(self):
        payload = SimpleNamespace(text=self.SONG_JSON)
        with mock.patch.object(vocadb, "search_song_id") as search, \
                mock.patch.object(vocadb, "vocadb_get", return_value=payload):
            song_id, _response = vocadb._details_payload("ナ2モノ", song_id="588755")
        self.assertEqual("588755", song_id)
        search.assert_not_called()

    def test_get_song_by_name_survives_a_block(self):
        """整条 `get_song_by_name` 被挡住时返回 None，而不是把堆栈甩给界面。"""
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=2):
            self.assertIsNone(vocadb.get_song_by_name("ナ2モノ", "ナ2モノ"))


class BrowserFallbackTest(CacheIsolationMixin, TestCase):
    """被 Cloudflare 挡住时借**用户自己的 Chrome/Edge** 取数（用户 2026-10-04 选的 B 方案）。

    真实行为已经实测过：整条链路（搜索 + 详情）首次 18 秒、第二次走缓存 0 秒；
    这里用 mock 固定住调用契约，不去真开浏览器（见了 `VOCAWIKI_NO_BROWSER_FETCH`）。
    """

    DETAILS_JSON = '{"artists": [], "artistString": "P feat. 巡音ルカ", "song": {"id": 667990}}'

    def setUp(self):
        self._isolate_cache()
        self.config = SimpleNamespace(proxies=None, vocadb_manual_url=False, vocadb_manual=False)
        patcher = mock.patch.object(vocadb, "get_config", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _challenge():
        return SimpleNamespace(status_code=403, text="Just a moment...",
                               raise_for_status=lambda: None)

    def test_the_browser_is_used_when_requests_are_blocked(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=True), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text",
                                  return_value=self.DETAILS_JSON) as fetch:
            response = vocadb.vocadb_get("https://vocadb.net/api/songs/667990/details")
        self.assertEqual(self.DETAILS_JSON, response.text)
        self.assertEqual(200, response.status_code)
        self.assertEqual(667990, response.json()["song"]["id"])
        self.assertEqual("https://vocadb.net/api/songs/667990/details", fetch.call_args.args[0])

    def test_query_params_are_folded_into_the_browser_url(self):
        """⚠️ 回归：浏览器那条路只拿到一个字符串 URL —— 忘了拼 `params` 的话，
        搜索会变成「无条件的列表」，于是一直搜不到歌（2026-10-04 实测踩到）。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=True), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text",
                                  return_value='{"items": []}') as fetch:
            vocadb.search_vocadb("ナ2モノ", vocadb.PARAMS_NARROW)
        url = fetch.call_args.args[0]
        self.assertIn("query=", url)
        self.assertIn(f"maxResults={vocadb.PARAMS_NARROW['maxResults']}", url)
        self.assertIn("songTypes=Original", url)
        self.assertIn("nameMatchMode=Exact", url)

    def test_a_successful_browser_fetch_starts_no_cooldown(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=True), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text",
                                  return_value=self.DETAILS_JSON):
            vocadb.vocadb_get("https://vocadb.net/api/songs/667990/details")
        self.assertEqual(0.0, vocadb._blocked_until)

    def test_without_a_browser_the_notice_is_kept(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=False), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text") as fetch:
            with self.assertRaises(vocadb.VocadbBlocked) as raised:
                vocadb.vocadb_get("https://vocadb.net/api/songs/667990/details")
        fetch.assert_not_called()
        self.assertIn("Chrome", str(raised.exception))

    def test_a_browser_failure_falls_back_to_the_notice(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=True), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text", return_value=None):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.vocadb_get("https://vocadb.net/api/songs/667990/details")

    def test_the_details_flow_works_end_to_end_with_the_browser(self):
        """整条路：直接请求被挡 → 浏览器取到 → 正常解析出歌姬，结果进去缓存。"""
        payload = json.dumps({
            "artists": [{"artist": {"name": "巡音ルカ V4X (Hard)", "artistType": "Vocaloid",
                                    "additionalNames": ""},
                         "name": "巡音ルカ V4X (Hard)", "roles": "Vocalist",
                         "categories": "Vocalist"}],
            "artistString": "P feat. 巡音ルカ V4X (Hard)",
            "pvs": [], "albums": [], "lyricsFromParents": [],
            "song": {"id": 667990, "defaultName": "cold death", "publishDate": "2024-08-30"}})
        search = json.dumps({"items": [{"id": 667990, "defaultName": "cold death",
                                        "artistString": "P feat. 巡音ルカ"}]})
        with mock.patch.object(vocadb, "http_get", return_value=self._challenge()), \
                mock.patch.object(vocadb.time, "sleep"), \
                mock.patch.object(vocadb.browser_fetch, "available", return_value=True), \
                mock.patch.object(vocadb.browser_fetch, "fetch_text",
                                  side_effect=lambda url, timeout=None: (
                                      payload if "/details" in url else search)):
            song_id, details = vocadb._details_payload("cold death")
        self.assertEqual(667990, int(song_id))
        creators = vocadb.parse_creators(details["artists"], details["artistString"])
        self.assertEqual(["巡音ルカ"], creators.vocalists_str())
        self.assertEqual(payload, json.dumps(vocadb.cached_song_payload(str(song_id))))


class BrowserFetchModuleTest(TestCase):
    """`utils/browser_fetch` 自己的边界（不启动浏览器的那部分）。"""

    def setUp(self):
        from utils import browser_fetch
        self.module = browser_fetch
        self.addCleanup(self.module.shutdown)

    def test_disabled_in_tests(self):
        """跑测时这个开关必须关着 —— 否则「测试卡死」就是它造成的。"""
        self.assertEqual("1", os.environ.get(self.module.DISABLE_ENV))
        self.assertFalse(self.module.available())

    def test_only_allowlisted_urls_are_allowed(self):
        """别被顺手拿去抓别的站点（白名单见 `ALLOWED_HOSTS`）。"""
        self.assertTrue(self.module._allowed("https://vocadb.net/api/songs"))
        self.assertTrue(self.module._allowed("https://www.nicolog.jp/watch/sm42552106"))
        self.assertFalse(self.module._allowed("https://example.com/x"))
        self.assertFalse(self.module._allowed("https://wiki.vocadb.net.evil.com/x"))
        self.assertFalse(self.module._allowed("https://nicolog.jp.evil.com/x"))

    def test_no_browser_means_no_fetch(self):
        with mock.patch.object(self.module, "find_browser", return_value=None):
            self.assertFalse(self.module.available())
            self.assertIsNone(self.module.fetch_text("https://vocadb.net/api/songs"))


class DisabledPvTest(TestCase):
    """VocaDB 把「非公開 / 削除済み」的稿件标成 `disabled`（实测 sm42552106 / sm41942916
    都是 400/404，同曲还活着的 sm43425344 则是 false）。

    站点那边认不出来时（nicolog 被 Cloudflare 挡、YouTube 直接不给元数据）就靠它把投稿栏
    写成 `{{VOCALOID_Songbox/card}}` —— 否则已删稿件会被当成正常投稿，投稿日还会退化成
    VocaDB 的 `publishDate`。
    """

    @staticmethod
    def _pv(**extra):
        pv = {"service": "NicoNicoDouga", "pvType": "Original",
              "url": "http://www.nicovideo.jp/watch/sm42552106"}
        pv.update(extra)
        return pv

    def test_disabled_pv_is_marked_deleted(self):
        video = Video(VideoSite.NICO_NICO, "sm42552106", "", 0, datetime(2023, 8, 5))
        with mock.patch.object(vocadb, "video_from_site", return_value=video):
            videos = vocadb.parse_videos([self._pv(disabled=True)], datetime(2023, 8, 5))
        self.assertTrue(videos[0].deleted)

    def test_live_pv_is_not_marked_deleted(self):
        video = Video(VideoSite.NICO_NICO, "sm42552106", "", 100, datetime(2023, 8, 5))
        with mock.patch.object(vocadb, "video_from_site", return_value=video):
            videos = vocadb.parse_videos([self._pv(disabled=False)], datetime(2023, 8, 5))
        self.assertFalse(videos[0].deleted)

    def test_the_flag_never_unmarks_a_deleted_video(self):
        """站点自己认出来了（nicolog）→ VocaDB 说 false 也不改回来。"""
        video = Video(VideoSite.NICO_NICO, "sm42552106", "", 0, datetime(2023, 8, 5),
                      deleted=True)
        with mock.patch.object(vocadb, "video_from_site", return_value=video):
            videos = vocadb.parse_videos([self._pv(disabled=False)], datetime(2023, 8, 5))
        self.assertTrue(videos[0].deleted)

    def test_missing_flag_changes_nothing(self):
        """老接口 / 手粘的 JSON 里可能没有这个字段。"""
        video = Video(VideoSite.NICO_NICO, "sm42552106", "", 100, datetime(2023, 8, 5))
        with mock.patch.object(vocadb, "video_from_site", return_value=video):
            videos = vocadb.parse_videos([self._pv()], datetime(2023, 8, 5))
        self.assertFalse(videos[0].deleted)


class ApiDocComplianceTest(CacheIsolationMixin, TestCase):
    """照 VocaDB 的 API 文档（https://wiki.vocadb.net/docs/public-api）做的三件事。

    文档的「API usage rules」原文要点：① 请用**自定义 User-Agent**，方便他们识别流量来源；
    ② 请**在自己这边缓存响应**，别反复要同一份数据；③ 不考虑服务器压力地每天几千次请求
    会被当成 DoS、可能**封 IP**。用户 2026-10-03 让看的就是这页。

    403 那个「Just a moment...」是 Cloudflare 挡在域名前面的，跟这三条无关
    （文档里没有 API key / 白名单之类的免校验通道），但照做能明显少发请求。
    """

    SONG_JSON = ('{"id": 588755, "defaultName": "ナ2モノ", "artistString": "初音ミク",'
                 ' "artists": [], "pvs": [], "albums": [], "lyricsFromParents": []}')

    def setUp(self):
        self._isolate_cache()
        self.config = SimpleNamespace(vocadb_manual_url=False)
        patcher = mock.patch.object(vocadb, "get_config", return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _ok(payload='{"items": []}'):
        response = SimpleNamespace(status_code=200, text=payload)
        response.raise_for_status = lambda: None
        return response

    def test_custom_user_agent_per_the_docs(self):
        """文档第 ① 条：对 vocadb 的 API 发工具自己的 UA（里面有仓库地址）。"""
        from utils import identity
        self.assertEqual(identity.USER_AGENT, vocadb._vocadb_headers()["User-Agent"])
        with mock.patch.object(vocadb, "http_get", return_value=self._ok()) as get, \
                mock.patch.object(vocadb.time, "sleep"):
            vocadb.search_vocadb("メルト", {})
        self.assertEqual(identity.USER_AGENT,
                         get.call_args.kwargs["headers"]["User-Agent"])

    def test_browser_ua_escape_hatch(self):
        """万一自定义 UA 反而更容易被挡：`vocadb_browser_ua: true` 就换回浏览器 UA。"""
        from utils import identity
        self.config.vocadb_browser_ua = True
        self.assertEqual(identity.BROWSER_USER_AGENT,
                         vocadb._vocadb_headers()["User-Agent"])

    def test_a_cookie_switches_to_the_browser_ua(self):
        """2026-10-03 浏览器实测：挡住请求的是**缺 cf_clearance 这个 Cookie**，
        而它绑 IP + UA —— 所以「配了 Cookie 却还发工具 UA」是自相矛盾的组合，
        会把好不容易拿到的 Cookie 白费。"""
        from utils import identity
        self.config.vocadb_cookie = "cf_clearance=abc"
        headers = vocadb._vocadb_headers()
        self.assertEqual(identity.BROWSER_USER_AGENT, headers["User-Agent"])
        self.assertEqual("cf_clearance=abc", headers["Cookie"])

    def test_an_explicit_user_agent_wins(self):
        """想精确匹配当初过校验的那串 UA（版本号差一位也会 403）：`vocadb_user_agent` 说了算。"""
        self.config.vocadb_cookie = "cf_clearance=abc"
        exact = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0.7390.55 Safari/537.36"
        self.config.vocadb_user_agent = exact
        self.assertEqual(exact, vocadb._vocadb_headers()["User-Agent"])
        # 没 Cookie 的场合也用它（用户就是想固定这一串）
        self.config.vocadb_cookie = ""
        self.assertEqual(exact, vocadb._vocadb_headers()["User-Agent"])

    def test_probe_reports_what_is_wrong(self):
        """自检：通了回报状态码，被挡就把那句人话原样交出来（不抛异常）。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._ok()), \
                mock.patch.object(vocadb.time, "sleep"):
            ok, message = vocadb.probe_access()
        self.assertTrue(ok)
        self.assertIn("200", message)
        with mock.patch.object(vocadb, "http_get", side_effect=RuntimeError("boom")):
            ok, message = vocadb.probe_access()
        self.assertFalse(ok)
        self.assertIn("boom", message)

    def test_requests_are_throttled(self):
        """文档第 ③ 条：两次真实请求之间至少隔 `VOCADB_MIN_INTERVAL` 秒。"""
        with mock.patch.object(vocadb, "http_get", return_value=self._ok()) as get, \
                mock.patch.object(vocadb.time, "sleep") as sleep:
            vocadb.search_vocadb("メルト", {})
            vocadb.search_vocadb("またね", {})
        self.assertEqual(2, get.call_count)
        # 第一次不用等（上次请求是 0），第二次要等满一个间隔
        waits = [call.args[0] for call in sleep.call_args_list]
        self.assertEqual(1, len(waits))
        # 不写死 1.0：中间隔了几毫秒，差一点点很正常
        self.assertGreater(waits[0], 0)
        self.assertLessEqual(waits[0], vocadb.VOCADB_MIN_INTERVAL)

    def test_a_retry_also_counts_against_the_budget(self):
        """重试的那一次也是真请求：每天的次数要算两下（免得日志里的数字比实际少一半）。"""
        challenge = SimpleNamespace(status_code=403, text="Just a moment...",
                                    raise_for_status=lambda: None)
        with mock.patch.object(vocadb, "http_get", return_value=challenge), \
                mock.patch.object(vocadb.time, "sleep"):
            with self.assertRaises(vocadb.VocadbBlocked):
                vocadb.search_vocadb("メルト", {})
        self.assertEqual(2, vocadb._load_cache()["requests"]["count"])

    def test_daily_budget_is_counted(self):
        with mock.patch.object(vocadb, "http_get", return_value=self._ok()), \
                mock.patch.object(vocadb.time, "sleep"):
            vocadb.search_vocadb("メルト", {})
        budget = vocadb._load_cache()["requests"]
        self.assertEqual(1, budget["count"])
        self.assertEqual(vocadb._budget_key(), budget["date"])

    def test_cached_search_id_skips_the_request_entirely(self):
        """文档第 ② 条：同一个歌名再生成一次，**一次请求都不发**。"""
        payload = SimpleNamespace(text=self.SONG_JSON)
        with mock.patch.object(vocadb, "search_song_id", return_value="588755") as search, \
                mock.patch.object(vocadb, "vocadb_get", return_value=payload) as get:
            self.assertEqual(("588755", "ナ2モノ"),
                             (lambda r: (r[0], r[1]["defaultName"]))(
                                 vocadb._details_payload("ナ2モノ")))
            self.assertEqual(("588755", "ナ2モノ"),
                             (lambda r: (r[0], r[1]["defaultName"]))(
                                 vocadb._details_payload("ナ2モノ")))
        self.assertEqual(1, search.call_count)               # 第二次没再搜
        self.assertEqual(1, get.call_count)                  # 详情也只取了一次

    def test_manually_pasted_json_is_cached(self):
        """手动粘一次就够了：第二次生成同一个条目不会再问、也不会联网。"""
        with mock.patch.object(vocadb, "search_song_id",
                               side_effect=vocadb.VocadbBlocked("挡住了")), \
                mock.patch.object(vocadb, "prompt_choices", return_value=1) as choices, \
                mock.patch.object(vocadb, "prompt_multiline",
                                  return_value=[self.SONG_JSON]) as multiline, \
                mock.patch.object(vocadb, "vocadb_get") as get:
            vocadb._details_payload("ナ2モノ")
            song_id, payload = vocadb._details_payload("ナ2モノ")
        self.assertEqual("588755", song_id)
        self.assertEqual("ナ2モノ", payload["defaultName"])
        self.assertEqual(1, multiline.call_count)            # 只问了一次
        self.assertEqual(1, choices.call_count)
        get.assert_not_called()

    def test_an_expired_cache_entry_is_ignored(self):
        vocadb.store_song_payload("588755", {"id": 588755, "defaultName": "旧"})
        cache = vocadb._load_cache()
        cache["songs"]["588755"]["at"] -= vocadb.VOCADB_CACHE_TTL + 1
        self.assertIsNone(vocadb.cached_song_payload("588755"))

    def test_clear_cache_drops_everything(self):
        vocadb.store_song_payload("588755", {"id": 588755, "defaultName": "ナ2モノ"})
        vocadb.store_song_id("ナ2モノ", "588755")
        path = vocadb._cache_path()
        self.assertTrue(path.is_file())
        vocadb.clear_cache()
        self.assertFalse(path.is_file())
        self.assertIsNone(vocadb.cached_song_payload("588755"))
        self.assertIsNone(vocadb.cached_song_id("ナ2モノ"))

    def test_a_search_miss_is_not_cached(self):
        """搜不到的不记：歌后来录入了，不该一直说「没有」。"""
        with mock.patch.object(vocadb, "search_song_id", return_value=None) as search:
            vocadb._details_payload("新曲")
        vocadb.store_song_id("新曲", None)
        self.assertIsNone(vocadb.cached_song_id("新曲"))
        self.assertEqual(1, search.call_count)

    def test_other_vocadb_calls_go_through_vocadb_get(self):
        """专辑曲目 / 其他版本详情 / 艺术家别名也走同一个入口（UA + 限速 + 被挡时给提示）。"""
        with mock.patch.object(vocadb, "vocadb_get",
                               side_effect=vocadb.VocadbBlocked("挡住了")) as get:
            self.assertEqual([], vocadb.get_album_track_song_ids(123))
            self.assertEqual([], vocadb.artist_aliases("雄之助"))
        self.assertEqual(2, get.call_count)
        self.assertIn("/api/albums/123", get.call_args_list[0].args[0])

    def test_the_notice_mentions_the_documented_workarounds(self):
        self.assertIn("Cloudflare", vocadb.VOCADB_BLOCK_NOTICE)
        self.assertIn("cf_clearance", vocadb.VOCADB_BLOCK_NOTICE)   # 实测出的真正原因
        self.assertIn("vocadb_cookie", vocadb.VOCADB_BLOCK_NOTICE)
        self.assertIn("vocadb_user_agent", vocadb.VOCADB_BLOCK_NOTICE)
        self.assertIn("/api/songs/", vocadb.VOCADB_BLOCK_NOTICE)   # 手动粘 JSON 那条路
