"""真机语料的排版记分卡（用户 2026-10-29 要求「上站上抓条目和翻译来测」）。

`tests/data/lyrics_corpus.json` 里是 27 条**真实**三元组：条目日语栏 / 条目中文栏 /
网易云歌词接口给的「日/中」逐句译文（取自 voca.wiki 上引网易云的条目，只留歌词文本）。

判定口径：**只看逐行对应，不看措辞**。条目上的译文常被校对改字（`“诸事随缘”之类` →
`「诸事随缘？之类」`），那是人的编辑，不是排版错误；所以每一行按相似度 ≥ 0.6 判「是不是同一句」。

改动排版逻辑后跑这个文件：正确率掉了就说明改坏了（以前没有这层度量，
每次只能等用户再报一个 bug 才发现 —— 这正是「优化十几次还是错」的原因）。
"""
import difflib
import json
from pathlib import Path
from unittest import TestCase

from utils import lyrics_editor

CORPUS = Path(__file__).resolve().parent.parent / "data" / "lyrics_corpus.json"
MIN_RATE = 0.90                     # 逐行正确率下限（实测约 0.905）


def _load() -> list:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _similarity(left: str, right: str) -> float:
    one, two = lyrics_editor._squeeze(left), lyrics_editor._squeeze(right)
    if not one or not two:
        return 1.0 if one == two else 0.0
    return difflib.SequenceMatcher(None, one, two).ratio()


def _per_line_score(item: dict) -> tuple:
    """`(正确的行数, 总行数, 结果)`。

    **按位置比**：排版结果保留了日语栏的分段，所以「第 N 行」就是第 N 行 —— 留空的行
    也是结果的一部分（条目里那一行本来就该有译文、我们没填上，就是错）。用空格子过滤掉
    会把这笔账算错（真机《世界上最后的旅鸽》就是这样被误判成 34/40）。
    """
    pairs = lyrics_editor.source_pairs(item["source"], item["jap"])
    aligned = lyrics_editor.align_lyrics(item["jap"], pairs)
    if aligned is None:
        want = lyrics_editor.normalize_blank_lines(item["chs"]).split("\n")
        return 0, max(1, len(want)), None
    want = lyrics_editor.normalize_blank_lines(item["chs"]).split("\n")
    got = aligned["chs"].split("\n")
    matcher = difflib.SequenceMatcher(
        None, [lyrics_editor._squeeze(line) for line in want],
        [lyrics_editor._squeeze(line) for line in got])
    good = total = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            good += i2 - i1
            total += i2 - i1
            continue
        paired = min(i2 - i1, j2 - j1)
        for offset in range(paired):
            total += 1
            if _similarity(want[i1 + offset], got[j1 + offset]) >= 0.6:
                good += 1
        total += max(0, (i2 - i1) - paired) + max(0, (j2 - j1) - paired)
    return good, max(1, total), aligned


class CorpusScoreboardTest(TestCase):
    def test_the_corpus_file_is_real_and_complete(self):
        items = _load()
        self.assertGreaterEqual(len(items), 20, "语料太少，覆盖不到各种形状")
        for item in items:
            with self.subTest(title=item["title"]):
                self.assertTrue(item["jap"].strip())
                self.assertTrue(item["source"].strip())
                self.assertTrue(item["chs"].strip())

    def test_alignment_accuracy_stays_above_the_bar(self):
        """逐行对齐正确率 ≥ 90%（改造前实测 13%）。纯音乐那种「没有歌词」的不计入。"""
        items = _load()
        good = total = 0
        failures = []
        for item in items:
            if lyrics_editor.is_instrumental(item["source"]):
                continue                        # 「纯音乐，请欣赏」没有歌词可对
            got, count, aligned = _per_line_score(item)
            good += got
            total += count
            if aligned is None or got / count < MIN_RATE:
                detail = "没有结果" if aligned is None else (
                    f"{got}/{count} 行；填 {aligned['filled']} 空 {aligned['blank']} "
                    f"丢 {aligned['dropped']} 合 {aligned['merged']} 拆 {aligned['split']}")
                failures.append(f"{item['title']}: {detail}")
        rate = good / total
        self.assertGreaterEqual(
            rate, MIN_RATE,
            f"逐行正确率 {rate:.1%} < {MIN_RATE:.0%}\n失败明细：\n  " + "\n  ".join(failures))

    def test_the_reported_song_is_reproduced_exactly(self):
        """用户 2026-10-29 报的《404 Ressenti Girl》：要逐字等于条目。"""
        item = next(entry for entry in _load() if entry["title"] == "404 Ressenti Girl")
        pairs = lyrics_editor.source_pairs(item["source"], item["jap"])
        aligned = lyrics_editor.align_lyrics(item["jap"], pairs)
        self.assertIsNotNone(aligned)
        self.assertEqual(item["chs"].strip(), aligned["chs"].strip())
        # 拆分（`さぁ` / `こんがらがってるアナタに404！`）与合并（`神様にでも…`）都要发生
        self.assertGreaterEqual(aligned["split"], 2)
        self.assertGreaterEqual(aligned["merged"], 3)


class SourceShapeTest(TestCase):
    """让整首作废的那几种「坏行」（真机 52 条里 20 条栽在这里）。"""

    def test_an_untranslated_line_does_not_void_the_source(self):
        """`music...` 这类没有译文的行只影响自己（以前整首判「不是逐句配对」）。"""
        source = ("一つ目\n第一句\nmusic...\n二つ目\n第二句\n三つ目\n第三句")
        pairs = lyrics_editor.source_pairs(source)
        self.assertEqual([("一つ目", "第一句"), ("music...", ""),
                          ("二つ目", "第二句"), ("三つ目", "第三句")], pairs)

    def test_noise_lines_are_dropped(self):
        """`♪` / `↓` 这种没有「字」的行直接丢掉（`-END-` / `music...` 留作无译文的行，
        对齐时会被当成多写的来源丢掉）。"""
        for noise in ("♪", "↓", "---", "＊"):
            with self.subTest(noise=noise):
                pairs = lyrics_editor.source_pairs(f"一つ目\n第一句\n{noise}")
                self.assertEqual([("一つ目", "第一句")], pairs)
        pairs = lyrics_editor.source_pairs("一つ目\n第一句\n-END-")
        aligned = lyrics_editor.align_lyrics("一つ目", pairs)
        self.assertEqual("第一句", aligned["chs"])
        self.assertEqual(1, aligned["dropped"])

    def test_english_lines_pair_with_themselves(self):
        """原文与译文相同的英文行（`Get ready! Get ready!`）只留一份。"""
        source = "Get ready! Get ready!\nGet ready! Get ready!\n走り出す\n跑起来"
        self.assertEqual([("Get ready! Get ready!", ""), ("走り出す", "跑起来")],
                         lyrics_editor.source_pairs(source))

    def test_a_note_only_translation_still_counts(self):
        """`lie（谎言）`：去掉括号注后没有汉字，不能因此判成「不是中文」。"""
        pairs = lyrics_editor.source_pairs("lie\nlie（谎言）")
        self.assertEqual([("lie", "lie（谎言）")], pairs)

    def test_the_down_arrow_shares_one_translation(self):
        """`↓` = 「下面那句译文属于上面那些日文」（真机《下手者》）。"""
        pairs = lyrics_editor.source_pairs("歌を\n↓\n延々喚いたって\n将歌一遍再一遍叫嚷出来")
        self.assertEqual([("歌を\n延々喚いたって", "将歌一遍再一遍叫嚷出来")], pairs)

    def test_instrumental_sources_are_named(self):
        self.assertTrue(lyrics_editor.is_instrumental("纯音乐，请欣赏"))
        self.assertTrue(lyrics_editor.is_instrumental("純音樂，請欣賞"))
        self.assertFalse(lyrics_editor.is_instrumental("一句歌词\n一句译文"))


class SplitAndMergeTest(TestCase):
    def test_splitting_drops_the_clause_comma(self):
        """`好了，给你这个混乱的404！` → `好了` / `给你这个混乱的404！`（条目的写法）。"""
        self.assertEqual(["好了", "给你这个混乱的404！"],
                         lyrics_editor._split_cn_for(2, "好了，给你这个混乱的404！"))

    def test_splitting_without_punctuation_uses_the_japanese_lengths(self):
        """没有标点时按各行日文的长度比例切（`将歌` / `一遍再一遍叫嚷出来`）。"""
        self.assertEqual(["将歌", "一遍再一遍叫嚷出来"],
                         lyrics_editor._split_cn_for(2, "将歌一遍再一遍叫嚷出来", [2, 8]))

    def test_unsplittable_text_stays_on_the_first_line(self):
        """切不出来就整句留在第一行、其余留空 —— 绝不造字。"""
        self.assertEqual(["短", ""], lyrics_editor._split_cn_for(2, "短", [1, 1]))
