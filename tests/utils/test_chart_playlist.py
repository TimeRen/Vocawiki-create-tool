"""官方播放列表解析 + 与画面 OCR 的对齐（见 utils/chart_playlist.py）。"""
import unittest

from utils import chart_playlist as cp

# FC2 播放列表页的真实结构：每条形如
#   <iframe …><a href="…/watch/sm1">【ニコニコ動画】标题</a></iframe>　1位<br>
# 标签在条目**后面**（这一点读错，`かえりみち` 就会被当成主榜 21 位）
MARKUP = """
<div class="e-text">
<iframe src="http://ext.nicovideo.jp/thumb/sm8753968"><a href="http://www.nicovideo.jp/watch/sm8753968">【ニコニコ動画】【リン＆テト】 汽車の窓が映す恋 【オリジナル】</a></iframe>　1位<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm8673630"><a href="http://www.nicovideo.jp/watch/sm8673630">【ニコニコ動画】【UTAU連続音】Lilium　【重音テト】</a></iframe>　2位<br>
<iframe src="http://ext.nicovideo.jp/thumb/nm8753537"><a href="http://www.nicovideo.jp/watch/nm8753537">【ニコニコ動画】【重音テト】かえりみち【オリジナル曲】【弾き語り】</a></iframe>　ピックアップ曲<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm8792484"><a href="http://www.nicovideo.jp/watch/sm8792484">【ニコニコ動画】【重音テト】口論【オリジナル曲】</a></iframe>　ピックアップ曲<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm8736774"><a href="http://www.nicovideo.jp/watch/sm8736774">【ニコニコ動画】UTAU三人娘でGO MY WAY!! 改【MMD】</a></iframe>　番外ランキング1位<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm8752783"><a href="http://www.nicovideo.jp/watch/sm8752783">【ニコニコ動画】【Voc@loidM@ster祭り3】アイマスの3人で驫&#40612;～とりぷるばか～【トレスMAD】</a></iframe>　番外ランキング3位<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm8740457"><a href="http://www.nicovideo.jp/watch/sm8740457">【ニコニコ動画】【UTAU新音源】今もあなたのために(オリジナル)【阿井植男】</a></iframe>　新音源<br>
<iframe src="http://ext.nicovideo.jp/thumb/sm5183609"><a href="http://www.nicovideo.jp/watch/sm5183609">【ニコニコ動画】【UTAU】初音ミクの消失【重音テト/カバー】</a></iframe>　ヒストリー1位<br>
<iframe src="http://ext.nicovideo.jp/thumb/nm6962413"><a href="http://www.nicovideo.jp/watch/nm6962413">【ニコニコ動画】【揺歌サユ】あの虹の真下へ【重音テト】</a></iframe>　オープニング曲<br>
</div>
"""

DESCRIPTION = ("○集計期間：2009年11月8日0時～2009年11月15日0時\n"
               "PL:終了 blogにて(http://utauhirohiro.blog56.fc2.com/blog-entry-452.html)")


class PlaylistUrlTest(unittest.TestCase):
    def test_finds_the_blog_entry_link(self):
        self.assertEqual("http://utauhirohiro.blog56.fc2.com/blog-entry-452.html",
                         cp.playlist_url(DESCRIPTION))

    def test_no_link_means_nothing(self):
        self.assertEqual("", cp.playlist_url("○集計期間：…"))


class ParsePlaylistTest(unittest.TestCase):
    def setUp(self):
        self.items = cp.parse_playlist(MARKUP)
        self.by_id = {item["video_id"]: item for item in self.items}

    def test_label_belongs_to_the_item_before_it(self):
        self.assertEqual(1, self.by_id["sm8753968"]["rank"])
        self.assertEqual(2, self.by_id["sm8673630"]["rank"])

    def test_pickup_group(self):
        self.assertEqual(cp.PICKUP, self.by_id["nm8753537"]["badge"])
        self.assertEqual(cp.PICKUP, self.by_id["sm8792484"]["badge"])
        self.assertIsNone(self.by_id["nm8753537"]["rank"])

    def test_extra_ranking_number(self):
        # 用户纠正过：`GO MY WAY!! 改` 是番外 1 位，不是主榜 1 位
        self.assertEqual(cp.EXTRA, self.by_id["sm8736774"]["section"])
        self.assertEqual(1, self.by_id["sm8736774"]["rank"])
        self.assertEqual(3, self.by_id["sm8752783"]["rank"])

    def test_new_source_is_only_a_topic_candidate(self):
        item = self.by_id["sm8740457"]
        self.assertEqual(cp.TOPIC, item["candidate"])
        self.assertEqual("", item["badge"])

    def test_history_opening_and_ending_are_kept_with_badges(self):
        # 用户 2026-11-17：HISTORY / OP / ED 要写进条目（照 #54 / #55 的写法）
        history = self.by_id["sm5183609"]
        self.assertEqual(cp.MAIN, history["section"])
        self.assertEqual(cp.HISTORY, history["badge"])
        self.assertEqual(1, history["rank"])
        opening = self.by_id["nm6962413"]
        self.assertEqual(cp.OP, opening["badge"])
        self.assertEqual("OP", opening["rank"])
        self.assertEqual(cp.ED, cp.label_of("エンディング曲")["badge"])
        self.assertEqual("ED", cp.label_of("エンディング曲")["rank"])
        self.assertEqual(cp.DROP, cp.label_of("CM")["section"])

    def test_titles_are_cleaned_and_entities_restored(self):
        self.assertEqual("Lilium", self.by_id["sm8673630"]["title"])
        self.assertIn("驫麤", self.by_id["sm8752783"]["title"])   # &#40612; = U+9EA4
        self.assertEqual("重音Teto", self.by_id["nm8753537"]["vocalist"])


def item(video_id, title, section=cp.MAIN, rank=None, badge="", candidate=""):
    return {"video_id": video_id, "raw_title": title, "title": title, "vocalist": "",
            "section": section, "rank": rank, "badge": badge, "candidate": candidate}


class AlignTest(unittest.TestCase):
    def test_ranks_come_from_the_playlist_not_the_pictures(self):
        # 画面把番外 1 位读成了「主榜 1 位」——对齐后必须是番外 1 位
        ocr = [{"本周": "1", "曲名": "UTAU三人娘でGO MY WAY!! 改", "得点": "9000"},
               {"本周": "2", "曲名": "Lilium", "得点": "8000"}]
        items = [item("sm8736774", "UTAU三人娘でGO MY WAY!! 改", cp.EXTRA, 1),
                 item("sm8673630", "Lilium", cp.MAIN, 2)]
        rows, _stat = cp.align(ocr, items, 20)
        self.assertEqual(["sm8736774", "sm8673630"], [row["id"] for row in rows])
        self.assertEqual("番外", rows[0]["小节"])
        self.assertEqual("1", rows[0]["本周"])
        self.assertEqual("9000", rows[0]["得点"], "数字要从画面搬过来")
        self.assertEqual("2", rows[1]["本周"])

    def test_pickup_keeps_the_big_rank_from_the_full_table(self):
        ocr = [{"本周": "83", "曲名": "口論", "得点": "120"}]
        rows, _stat = cp.align(ocr, [item("sm8792484", "口論", badge=cp.PICKUP)], 20)
        self.assertEqual(1, len(rows))
        self.assertEqual("PICKUP", rows[0]["角标"])
        self.assertEqual("83", rows[0]["本周"])

    def test_pickup_keeps_the_rank_read_from_the_picture(self):
        # 播放列表不给 ピックアップ曲 号码，画面里读到多少就用多少（#53：かえりみち 21、口論 83）
        ocr = [{"本周": "21", "曲名": "かえりみち"}]
        rows, stat = cp.align(ocr, [item("nm8753537", "かえりみち", badge=cp.PICKUP)], 20)
        self.assertEqual("21", rows[0]["本周"])
        self.assertEqual("PICKUP", rows[0]["角标"])
        self.assertEqual([], list(stat["missing"]))

    def test_two_cards_for_one_song_take_the_numbered_one(self):
        # 用户 2026-11-17：朝食（あさ）在 TOPIC 段那张卡上没有号码，整页排名表里是 44 位
        ocr = [{"曲名": "朝食（あさ）"},
               {"本周": "44", "曲名": "朝食（あさ）", "得点": "1120", "再生": "780"}]
        rows, stat = cp.align(ocr, [item("nm8744495", "朝食（あさ）", badge=cp.PICKUP)], 20)
        self.assertEqual(1, len(rows))
        self.assertEqual("44", rows[0]["本周"])
        self.assertEqual("1120", rows[0]["得点"])
        self.assertEqual("PICKUP", rows[0]["角标"])
        self.assertEqual([], list(stat["dropped"]), "同一首曲子的另一张卡不该算「不在列表里」")
        self.assertEqual(1, stat["merged"])

    def test_garbled_ocr_title_still_matches(self):
        ocr = [{"本周": "3", "曲名": "アイマスの3人で驫?～とぅりぷるか～", "得点": "5000"}]
        items = [item("sm8752783", "アイマスの3人で驫龤～とりぷるばか～", cp.EXTRA, 3)]
        rows, _stat = cp.align(ocr, items, 20)
        self.assertEqual(1, len(rows))
        self.assertEqual("sm8752783", rows[0]["id"])
        self.assertEqual("5000", rows[0]["得点"])

    def test_rows_outside_the_playlist_are_dropped(self):
        ocr = [{"曲名": "散らばる五枚の絵。"}, {"曲名": "きみがくれた唄でひとり"},
               {"本周": "1", "曲名": "汽車の窓が映す恋"}]
        rows, stat = cp.align(ocr, [item("sm8753968", "汽車の窓が映す恋", cp.MAIN, 1)], 20)
        self.assertEqual(1, len(rows))
        self.assertEqual(2, len(stat["dropped"]))

    def test_topic_rows_from_the_picture_are_kept_even_if_unmatched(self):
        ocr = [{"曲名": "今もあなたのために", "角标": "TOPIC", "得点": "77"},
               {"曲名": "散らばる五枚の絵。"}]
        rows, stat = cp.align(ocr, [], 20)
        self.assertEqual(1, len(rows))
        self.assertEqual("TOPIC", rows[0]["角标"])
        self.assertEqual(["散らばる五枚の絵。"], stat["dropped"])

    def test_missing_items_are_added_without_numbers(self):
        items = [item("sm8753968", "汽車の窓が映す恋", cp.MAIN, 1),
                 item("nm8770781", "六花待ち", badge=cp.PICKUP)]
        rows, stat = cp.align([], items, 20)
        self.assertEqual(2, len(rows))
        self.assertEqual("1", rows[0]["本周"])
        self.assertNotIn("本周", rows[1], "ピックアップ曲 的名次画面里没读到就空着")
        self.assertEqual("PICKUP", rows[1]["角标"])
        self.assertEqual(1, len(stat["missing"]), "只有 ピックアップ曲 缺名次")

    def test_main_limit_only_keeps_the_top_n(self):
        items = [item("sm1", "一首目", cp.MAIN, 1), item("sm2", "二十一名", cp.MAIN, 21)]
        rows, _stat = cp.align([], items, 20)
        self.assertEqual(["sm1"], [row["id"] for row in rows])

    def test_op_and_ed_survive_the_main_limit(self):
        """用户 2026-11-18：第 61 期生成不了 —— `int('OP')` 直接抛 ValueError。

        播放列表页给 `オープニング曲` / `エンディング曲` 时名次是 `OP` / `ED`（不是数字），
        以前 `wanted()` 会拿它去比 `main_limit`。
        """
        items = [item("sm1", "オープニング曲", cp.MAIN, cp.OP, badge=cp.OP),
                 item("sm2", "一首目", cp.MAIN, 1),
                 item("sm3", "エンディング曲", cp.MAIN, cp.ED, badge=cp.ED),
                 item("sm4", "二十一名", cp.MAIN, 21)]
        self.assertTrue(cp.wanted(items[0], 20))
        self.assertTrue(cp.wanted(items[2], 20))
        self.assertFalse(cp.wanted(items[3], 20))
        rows, _stat = cp.align([], items, 20)
        self.assertEqual(["OP", "1", "ED"], [row.get("本周") for row in rows])
        self.assertEqual([cp.OP, None, cp.ED], [row.get("角标") for row in rows])

    def test_rank_number_ignores_non_numeric_ranks(self):
        self.assertEqual(12, cp.rank_number("12"))
        self.assertEqual(44, cp.rank_number("44位"))
        self.assertIsNone(cp.rank_number(cp.OP))
        self.assertIsNone(cp.rank_number(None))
        self.assertIsNone(cp.rank_number(" "))

    def test_topic_candidates_without_a_picture_are_kept_with_a_note(self):
        # 用户 2026-11-17：憑歌ルク（中二病賛歌）与 きるきる 画面没读到就整首消失了，
        # 现在候选一律先占一行，名次空着 + 备注提醒手填
        items = [item("sm8740457", "今もあなたのために", candidate=cp.TOPIC),
                 item("sm5161232", "【short】驫龤", candidate=cp.TOPIC)]
        rows, stat = cp.align([], items, 20)
        self.assertEqual(["sm8740457", "sm5161232"], [row["id"] for row in rows])
        self.assertNotIn("本周", rows[0])
        self.assertEqual("TOPIC", rows[0]["角标"])
        self.assertIn("TOPIC名次没读到", rows[0]["备注"])
        self.assertEqual(2, len(stat["unread_topics"]))

    def test_topic_candidate_seen_on_screen_gets_the_badge(self):
        # 用户 2026-11-17：TOPIC 段就是播放列表的「新音源」段，画面里出现过就该标 TOPIC
        items = [item("sm8785342", "きるきるにしてあげる", candidate=cp.TOPIC)]
        rows, _stat = cp.align([{"本周": "18", "曲名": "きるきるにしてあげる",
                                 "得点": "900"}], items, 20)
        self.assertEqual("TOPIC", rows[0]["角标"])
        self.assertEqual("18", rows[0]["本周"])
        self.assertEqual("sm8785342", rows[0]["id"])

    def test_topic_candidates_helper(self):
        items = [item("sm1", "一", candidate=cp.TOPIC), item("sm2", "二", cp.MAIN, 1)]
        self.assertEqual(["sm1"], [entry["video_id"] for entry in cp.topic_candidates(items)])


class CandidateTitleTest(unittest.TestCase):
    """用户 2026-11-17：`【新音源】憑歌ルク【中二病賛歌】【UTAU】` 的曲名是「中二病賛歌」。"""

    def test_splits_song_and_vocalist(self):
        self.assertEqual(("中二病賛歌", "憑歌ルク"),
                         cp.split_candidate_title("【新音源】憑歌ルク【中二病賛歌】【ＵＴＡu】"))

    def test_leaves_leading_bracketed_titles_alone(self):
        # 前导带括号说明括号里是曲名的一部分，不能当歌姬
        self.assertEqual(("", ""),
                         cp.split_candidate_title("【新音源】今もあなたのために(オリジナル)【阿井植男】"))

    def test_leaves_composite_titles_alone(self):
        self.assertEqual(("", ""),
                         cp.split_candidate_title("【新音源】綿歌チトセ ／ Fire◎Flower 【UTAUカバー】"))

    def test_ignores_titles_without_the_marker(self):
        self.assertEqual(("", ""), cp.split_candidate_title("【UTAU】冬の金魚【揺歌サク】"))

    def test_playlist_uses_the_split_title(self):
        markup = ('<a href="http://www.nicovideo.jp/watch/sm8757272">'
                  '【新音源】憑歌ルク【中二病賛歌】【ＵＴＡu】</a></iframe> 新音源<br>')
        items = cp.parse_playlist(markup)
        self.assertEqual("中二病賛歌", items[0]["title"])
        self.assertEqual("憑歌ルク", items[0]["vocalist"])
        self.assertEqual(cp.TOPIC, items[0]["candidate"])


if __name__ == "__main__":
    unittest.main()
