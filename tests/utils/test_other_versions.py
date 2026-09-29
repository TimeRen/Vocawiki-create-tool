"""「其他版本」：版本命名 + 逐个询问的交互（参 voca.wiki《鸟之诗》）。

候选列表来自 VocaDB 的 `alternateVersions`（`utils/vocadb.parse_other_versions`），
这里只管命名与问答，wikitext 的拼装在 `main.create_tabs` / `main.create_song`
（见 tests/test_main.py::OtherVersionsTest）。
"""
from datetime import date
from types import SimpleNamespace
from unittest import TestCase, mock

from models.video import OtherVersion, VideoSite
from utils import other_versions


def _version(vocalists=("鏡音リン",), producers=("じゃがりこP",), song_type="Cover",
             publish=date(2007, 12, 28), artist_string=None, version_id=1, name=""):
    return OtherVersion(version_id=version_id, name=name, song_type=song_type,
                        artist_string=artist_string if artist_string is not None
                        else "{} feat. {}".format("、".join(producers), "、".join(vocalists)),
                        vocalists=list(vocalists), producers=list(producers),
                        publish_date=publish)


def _song(vocalists=("初音ミク",), producers=("でんげん",), versions=(),
          name_jap="鳥の詩", name_chs="鸟之诗", artist_types=()):
    """主版本的假 Song；`artist_types` 给得出就按 VocaDB 的 artistType 带上（认引擎用）。"""
    people = [SimpleNamespace(name=name, artist_type=artist_types[index]
                              if index < len(artist_types) else "")
              for index, name in enumerate(vocalists)]
    return SimpleNamespace(
        name_jap=name_jap, name_chs=name_chs,
        creators=SimpleNamespace(vocalists=people,
                                 vocalists_str=lambda: list(vocalists),
                                 producers=[SimpleNamespace(name=n) for n in producers]),
        other_versions=list(versions),
    )


class LabelTest(TestCase):
    """版本名：`[P主]歌姬[ 引擎]版`。"""

    def test_engine_is_written_when_it_differs(self):
        # 本曲是 VOCALOID（初音ミク），重音Teto 属于 UTAU → 要写出来（参《鸟之诗》）
        song = _song(versions=[_version(vocalists=("重音テト",), producers=("tattoo2003",))])
        other_versions.label_versions(song)
        self.assertEqual("重音Teto UTAU版", song.other_versions[0].label)

    def test_same_engine_is_not_repeated(self):
        song = _song(versions=[_version()])
        other_versions.label_versions(song)
        self.assertEqual("镜音铃版", song.other_versions[0].label)

    def test_duplicated_vocalist_gets_producer_prefix(self):
        """同一个歌姬的两个版本要能分辨：前面补 P主（参《鸟之诗》的でんげん版 / mar 版）。"""
        song = _song(vocalists=("鏡音リン",), producers=("じゃがりこP",),
                     versions=[_version(vocalists=("初音ミク",), producers=("でんげん",), version_id=1),
                               _version(vocalists=("初音ミク",), producers=("mar",), version_id=2)])
        other_versions.label_versions(song)
        self.assertEqual(["でんげん初音未来版", "mar初音未来版"],
                         [version.label for version in song.other_versions])

    def test_candidate_colliding_with_the_main_version_gets_a_tag(self):
        """主版本没写 P主 时（VocaDB 上确有这种条目），候选与主版本同名 → 补年份区分。"""
        song = _song(producers=(),
                     versions=[_version(vocalists=("初音ミク",), producers=(), version_id=1,
                                        publish=date(2009, 5, 1))])
        self.assertEqual("初音未来版", other_versions.main_version_label(song))
        other_versions.label_versions(song)
        self.assertEqual(["初音未来版（2009年）"],
                         [version.label for version in song.other_versions])

    def test_same_producer_and_vocalists_get_a_distinguishing_tag(self):
        """同一个 P主 的两个版本（ナ2モノ 与 ナ2モノ (ROCK_VER)）补上 VocaDB 名字里的说明。"""
        song = _song(name_jap="ナ2モノ", name_chs="ナ2モノ", vocalists=("初音ミク", "巡音ルカ"),
                     producers=("Shu",),
                     versions=[_version(vocalists=("初音ミク", "巡音ルカ"), producers=("Shu",),
                                        version_id=1, name="ナ2モノ", song_type="Remaster",
                                        publish=date(2024, 9, 1)),
                               _version(vocalists=("初音ミク", "巡音ルカ"), producers=("Shu",),
                                        version_id=2, name="ナ2モノ (ROCK_VER)",
                                        publish=date(2025, 3, 31))])
        other_versions.label_versions(song)
        remake, rock = song.other_versions
        self.assertNotEqual(remake.label, rock.label, "重名的版本必须能区分开")
        self.assertEqual("Shu初音未来、巡音流歌版（2024年）", remake.label)
        self.assertEqual("Shu初音未来、巡音流歌版（ROCK_VER）", rock.label)
        # tab 按钮上只写那一截补充说明（用户 2026-09 要求 bt2 写「ROCK版」）
        self.assertEqual("2024年版", remake.tab_label)
        self.assertEqual("ROCK版", rock.tab_label)

    def test_tab_label_is_the_version_name_when_it_stands_alone(self):
        """不用补充说明就能区分的版本，tab 上就是版本名本身。"""
        song = _song(versions=[_version()])
        other_versions.label_versions(song)
        self.assertEqual("镜音铃版", song.other_versions[0].tab_label)

    def test_producer_prefixed_version_keeps_its_name_on_the_tab(self):
        song = _song(vocalists=("鏡音リン",), producers=("じゃがりこP",),
                     versions=[_version(vocalists=("初音ミク",), producers=("でんげん",)),
                               _version(vocalists=("初音ミク",), producers=("mar",), version_id=2)])
        other_versions.label_versions(song)
        self.assertEqual(["でんげん初音未来版", "mar初音未来版"],
                         [version.tab_label for version in song.other_versions])

    def test_without_vocalist_falls_back_to_producer(self):
        song = _song(versions=[_version(vocalists=(), producers=("某人",), artist_string="某人")])
        other_versions.label_versions(song)
        self.assertEqual("某人版", song.other_versions[0].label)

    def test_main_version_label(self):
        self.assertEqual("でんげん初音未来版", other_versions.main_version_label(_song()))


class ChooseTest(TestCase):
    """逐个问「要加入哪个版本」，再问 B 站链接与是否官方投稿。"""

    def setUp(self):
        self.song = _song(versions=[_version(),
                                    _version(vocalists=("重音テト",), producers=("tattoo2003",),
                                             version_id=2)])
        self.video = SimpleNamespace(site=VideoSite.BILIBILI, identifier="BV1is411f772",
                                     views=1_200_000, uploaded=date(2007, 12, 28),
                                     url="", canonical=True, deleted=False)

    def _choose(self, choices, links=None, fetch=None, version_videos=None, version_albums=None):
        """choices 按调用顺序给（每次调用取下一个）；links 默认给一个能用的 B 站链接。

        `get_version_details` 会把稿件 / 专辑直接填到版本上，所以这里模拟它填数据的副作用。
        """
        choice_iter = iter(choices)
        parsed = SimpleNamespace(site=VideoSite.BILIBILI, identifier="BV1is411f772")
        link_iter = iter(links if links is not None else (parsed, parsed, parsed, parsed, parsed))

        def fill_details(song, version):
            version.videos = list(version_videos or [])
            version.albums = list(version_albums or [])

        with mock.patch.object(other_versions, "prompt_choices",
                               side_effect=lambda *a, **k: next(choice_iter)), \
             mock.patch.object(other_versions, "prompt_video_link",
                               side_effect=lambda *a, **k: next(link_iter)), \
             mock.patch.object(other_versions, "video_from_site",
                               side_effect=fetch or (lambda site, ident, canonical: self.video)), \
             mock.patch.object(other_versions.vocadb, "get_version_details",
                               side_effect=fill_details) as self.version_details:
            return other_versions.choose_other_versions(self.song)

    def test_picks_one_version_and_fills_the_bilibili_video(self):
        # 选「1」（镜音铃版）→ 问是否官方（1 = 是）→ 第二次菜单选最后一项（不加了）
        chosen = self._choose([1, 1, 2])
        self.assertEqual(1, len(chosen))
        version = chosen[0]
        self.assertEqual("镜音铃版", version.label)
        self.assertEqual("BV1is411f772", version.video.identifier)
        self.assertTrue(version.canonical)
        self.assertEqual(chosen, self.song.other_versions, "选中的结果要写回 song")

    def test_answering_no_canonical_marks_the_version(self):
        chosen = self._choose([1, 2, 2])
        self.assertFalse(chosen[0].canonical)

    def test_empty_link_skips_that_version(self):
        chosen = self._choose([1, 1, 1], links=(None, None))
        self.assertEqual([], chosen)
        self.assertEqual([], self.song.other_versions)
        self.version_details.assert_not_called()   # 跳过的版本不用去联网找 nico / yt 稿件

    def test_the_version_own_videos_and_albums_are_fetched(self):
        """选中后要拿这个版本自己的稿件（Songbox 的 nnd_ / yt_ 栏）与收录专辑（简介）。"""
        nico = SimpleNamespace(site=VideoSite.NICO_NICO, identifier="sm44829675")
        chosen = self._choose([1, 1, 2], version_videos=[nico],
                              version_albums=["after EXCURSION"])
        self.assertEqual([nico], chosen[0].videos)
        self.assertEqual(["after EXCURSION"], chosen[0].albums)
        self.assertEqual(self.song, self.version_details.call_args.args[0])
        self.assertEqual(chosen[0], self.version_details.call_args.args[1])

    def test_without_candidates_nothing_is_asked(self):
        song = _song(versions=[])
        with mock.patch.object(other_versions, "prompt_choices") as choices:
            self.assertEqual([], other_versions.choose_other_versions(song))
        choices.assert_not_called()

    def test_candidate_list_is_capped(self):
        versions = [_version(version_id=index) for index in range(other_versions.MAX_CANDIDATES + 5)]
        self.song.other_versions = versions
        done = other_versions.MAX_CANDIDATES + 1     # 最后一个选项 = 「不加了」
        with mock.patch.object(other_versions, "prompt_choices", return_value=done) as ask:
            other_versions.choose_other_versions(self.song)
        self.assertEqual(done, len(ask.call_args[0][1]),
                         "候选要截断到 MAX_CANDIDATES 个（+1 个「不加了」）")
