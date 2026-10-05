"""歌姬名归一化：VocaDB / 站上的写法 → 站上条目名 / 链接写法 / 分类名 / 合成引擎。

* `vocaloid_names`：VocaDB 那边给的名字（`初音ミク V4X (Original)` 这类声库全名也在内，
  靠 `name_shorten()` 砍后缀）→ 站上条目名；
* `ENGINES`：引擎 → 歌姬表，`get_engine()` / `engines_of()` 用它写 `[[分类:使用X的歌曲]]`、
  简介里的 `[[X]]日语原创歌曲` 与 `{{虚拟歌手歌曲荣誉题头|X|…}}`。
  各引擎的歌姬表在 `utils/engine_characters.py`（照 voca.wiki `Category:音声合成软件模板`
  下的引擎模板整理）；
* `WIKI_LINK_NAMES` / `cat_transform`：条目名跟显示名 / 分类名不一致的那几个例外。
"""
from typing import Dict, List, Sequence
import re

from utils.engine_characters import (ACE_CHARACTERS, AISINGERS_CHARACTERS,
                                     A_I_VOICE_CHARACTERS, DEEPVOCAL_CHARACTERS,
                                     DIFFSINGER_CHARACTERS, EXTRA_CHARACTERS,
                                     JAPANESE_ALIASES, MAC_CHARACTERS,
                                     NIAONIAO_CHARACTERS, VOCALSHARP_CHARACTERS,
                                     VOGEN_CHARACTERS, VOICEVOX_CHARACTERS,
                                     VOICEROID_CHARACTERS, VOICE_MITH_CHARACTERS,
                                     X_STUDIO_CHARACTERS)

vocaloid_names = {
    '初音ミク': "初音未来",
    '鏡音リン': "镜音铃",
    '鏡音レン': "镜音连",
    '巡音ルカ': "巡音流歌",
    'カイト': "KAITO",
    'メイコ': "MEIKO",
    # vocadb 里 KAITO / MEIKO 的声库名是**拉丁字**（KAITO V3 (Whisper)、MEIKO V3 (Power)…），
    # 上面那两条片假名的键在它们身上匹配不上 → 歌姬名会整串漏出去（模板 / 分类 / 中文名全对不上）。
    # 加两条拉丁别名让 name_shorten 能把声库后缀砍掉（用户 2026-09 报的「歌姬名称识别不了」）。
    'KAITO': "KAITO",
    'MEIKO': "MEIKO",
    '音街ウナ': "音街鳗",
    '歌愛ユキ': "歌爱雪",
    '結月ゆかり': "结月缘",
    '神威がくぽ': "神威乐步",
    'ブイフラワ': "v flower",
    'イア': "IA",
    'マユ': "MAYU",
    'GUMI': "GUMI",
    # vocadb 里 Synthesizer V 的 GUMI 叫「Synthesizer V AI Megpoid」（artistType=SynthesizerV，
    # 声库全名），旧实现一个字都砍不掉 → 歌姬名整串漏进条目（用户 2026-09 报
    # 「识别歌姬时并没有分辨出 Synthesizer V AI Megpoid 是 Megpoid」，参 voca.wiki《小小星座》）。
    # name_shorten 命中的是**键**，所以这里写词条名 Megpoid；
    # 中文名 / 分类仍走 cat_transform 的 GUMI → Megpoid（见下），链接写法见 WIKI_LINK_NAMES。
    'Megpoid': "GUMI",
    'ふかせ': "Fukase",
    'ギャラ子': 'Galaco',
    '心華': "心华",
    "紲星あかり": "绁星灯",
    '重音テト': "重音Teto",
    '鳴花ヒメ': '鸣花姬',
    '鳴花ミコト': '鸣花尊',
    '시유': 'SeeU',
    'Eleanor Forte': '爱莲娜·芙缇',
    'KAFU': '可不',
    'SeKai': '星界',
    # vocadb 里带 SV 后缀的是重音テト的 Synthesizer V 声库（模板/分类仍算「重音Teto」）
    '重音テトSV': '重音Teto',
    'ナースロボ＿タイプＴ': 'NurseRobot_TypeT',
    # 以下是 vocadb 的 Default 名（日文 / 繁体）与工具里的写法不一致的，统一到这里，
    # 否则引擎识别、歌手模板查找和「XX歌曲」分类名都会对不上。
    # 注意长名字放前面：name_shorten 取的是第一个命中的键。
    '琴葉茜・葵': '琴叶茜·葵',
    '琴葉茜': '琴叶茜',
    '琴葉葵': '琴叶葵',
    '裏命': '里命',
    'さとうささら': '佐藤莎莎拉',
    'ずんだもん': '俊达萌',
    '東北ずん子': '东北俊子',
    '東北きりたん': '东北切蒲英',
    '東北イタコ': '东北伊达子',
    '大江戸あいこ': '大江户相子',
    '四国めたん': '四国玫碳',
    'あんこもん': '安可萌',
    '夏語遙': '夏语遥',
    '双葉湊音': '双叶凑音',
    '猫村いろは': '猫村伊吕波',
    '狐狸座Vul': '狐狸座',
    # ↓↓↓ 爬站上 `Category:按虚拟歌手分类的歌曲`（20 个引擎子分类 / 429 个「XX歌曲」）
    # 拿到各歌手页的重定向反推出来的：**这些键是 VocaDB 用的写法，值是站上的条目名**。
    # 不映射的话「XX歌曲」分类会照抄 VocaDB 的名字 —— 用户 2026-10-05 报的
    # 《毒电波》diff 257148 就是把分类写成了 `[[分类:りむる歌曲]]`（应为 `[[分类:Reml歌曲]]`）。
    # 同批还有 50 多个歌姬有一样的问题（`ゲキヤク`→Gekiyaku、`カゼヒキ`→Kazehiki…）。
    # 长名字放前面：name_shorten 取的是第一个命中的键。
    '分散型自律ゴーレム りむる': 'Reml',
    'ナースロボ＿タイプT': 'NurseRobot_TypeT',
    'ゲキヤクβ': 'Gekiyaku',
    'カゼヒキβ': 'Kazehiki',
    'がくっぽいど': 'Gackpoid',
    'ガチャッポイド': 'Gachapoid',
    'あきこロイドちゃん': 'Akikoloid-chan',
    'アルスロイド': 'ARSLOID',
    'メグッポイド': 'Megpoid',
    'Mac音ナナ': 'Mac音奈奈',
    '氷山キヨテル': '冰山清辉',
    '宮舞モカ': '宫舞茉歌',
    '兎眠りおん': '兔眠莉音',
    'AIきりたん': '东北切蒲英',
    '彩澄りりせ': '彩澄梨理世',
    '彩澄しゅお': '彩澄珠绚',
    '歌手音ピコ': '歌手音PIKO',
    '蒼姫ラピス': '苍姬拉碧斯',
    '湯鬱声からす': '汤郁声Karasu',
    'すずきつづみ': '铃木梓梓弥',
    'つくよみちゃん': '小月相',
    '闇音レンリ': '暗音Renri',
    '暗音レンリ': '暗音Renri',
    '暗鳴ニュイ': '暗鸣Nyui',
    '旭音エマ': '旭音Ema',
    '足立レイ': '足立零',
    '茶運めぐり': '足立零',
    'デフォ子': '呗音Uta',
    '唄音ウタ': '呗音Uta',
    '阿久女イク': '阿久女Iku',
    '亞北ネル': '亚北音留',
    '桃音モモ': '桃音Momo',
    '欲音ルコ': '欲音Ruko',
    '狼音アロ': '狼音阿罗',
    '雪歌ユフ': '雪歌Yufu',
    '雨歌エル': '雨歌Eru',
    '眠歌ユメ': '眠歌梦',
    '弱音ハク': '弱音白',
    '根音ネネ': '根音Nene',
    '健音テイ': '健音帝',
    '使音アキ': '使音Aki',
    'カゼヒキ': 'Kazehiki',
    'ゲキヤク': 'Gekiyaku',
    'フリモメン': '弗里摩侠',
    '滲音かこい': '渗音Kakoi',
    'こんばん4号': 'Koronba4号',
    'ころんば4号': 'Koronba4号',
    '桃音もも': '桃音Momo',
    'グミ': 'Megpoid',
    'ふりゅね': 'Fifne',
    'キズナ': 'Kizuna',
    'さてまろ': 'Satemaro',
    'ネウマフ': 'Neumaf',
    'デルピス': '海豚',
    'りむる': 'Reml',
    'シユ': 'SeeU',
    'うい': '雨衣',
}

# 站上 `Category:音声合成软件模板` 下那些引擎模板里的歌姬（见 utils/engine_characters.py），
# 只管补缺：已有的键先命中（`name_shorten()` 取第一个子串命中的键）。
for _japanese, _chinese in JAPANESE_ALIASES.items():
    vocaloid_names.setdefault(_japanese, _chinese)

CEVIO_CHARACTERS = {
    "白咲优大": "白咲优大",
    "赤咲湊": "赤咲湊",
    "东北切蒲英": "东北切蒲英",
    "高桥": "高桥",
    "HAL-O-ROID": "HAL-O-ROID",
    "狐子": "狐子",
    "花隈千冬": "花隈千冬",
    "黄咲爱里": "黄咲爱里",
    "结月缘": "结月缘",
    "金咲小春": "金咲小春",
    "可不": "可不",
    "Kizuna": "Kizuna",
    "里命": "里命",
    "铃木梓梓弥": "铃木梓梓弥",
    "绿咲香澄": "绿咲香澄",
    "ONE": "ONE",
    "POPY": "POPY",
    "ROSE": "ROSE",
    "双叶凑音": "双叶凑音",
    "夏色花梨": "夏色花梨",
    "小春六花": "小春六花",
    "星界": "星界",
    "银咲大和": "银咲大和",
    "知声": "知声",
    "佐藤莎莎拉": "佐藤莎莎拉",
}

UTAU_CHARACTERS = {
    '重音Teto': '重音Teto',
    # vocadb 里未标注 SV 的重音テト 就是 UTAU（带 SV 后缀的另算 Synthesizer V，见上方 vocaloid_names）
    '重音テト': '重音Teto',
}

SYNTHESIZER_V_CHARACTERS = {
    '爱莲娜·芙缇': '爱莲娜·芙缇',
    '小春六花': '小春六花',
    # 「AI」是 VocaDB 给小春六花的 Synthesizer V 声库起的后缀（`小春六花 AI`），
    # 站上 `小春六花 AI 2` 也是同一副声库的新版本 —— 引擎一样算 Synthesizer V
    '小春六花 AI': '小春六花',
    '小春六花 AI 2': '小春六花',
    '弦卷真纪': '弦卷真纪',
    '可不': '可不',
    '星界': '星界',
    '结月缘': '结月缘',
    '花隈千冬': '花隈千冬',
    '重音テトSV': '重音Teto',
    'GUMI': 'GUMI',
    'Megpoid': 'GUMI',
    '夏语遥': '夏语遥',
    '苍穹': '苍穹',
    '海伊': '海伊',
    '诗岸': '诗岸',
    '赤羽': '赤羽',
    '牧心': '牧心',
    '星尘': '星尘',
    '岸晓': '岸晓',
    '默辰': '默辰',
    '鸣花姬': '鸣花姬',
    '鸣花尊': '鸣花尊',
    'GENBU': 'GENBU',
    'SOLARIA': 'SOLARIA',
    'Mai': 'Mai',
    '里命': '里命',
    'Kizuna': 'Kizuna',
    'HAL-O-ROID': 'HAL-O-ROID',
    # ⚠️ 这里以前还挂着 `俊达萌`，照 `Template:Synthesizer V` 删掉了（用户 2026-09-30 要求）：
    # 站上 SynthV 模板里根本没有 ずんだもん，VocaDB 也只给了 UTAU / VOICEVOX / NEUTRINO 三条 ——
    # 于是无类型时（其他版本 / 手填名字）它落回本就在表里的 NEUTRINO；有 artistType 时照旧以类型为准。
    '樱乃空': '樱乃空',
    '桜乃そら': '樱乃空',          # AHS 的 SynthV 声库；`Template:Synthesizer V` 里有「樱乃空」
}

NEUTRINO_CHARACTERS = {
    '东北切蒲英': '东北切蒲英',
    '俊达萌': '俊达萌',
    '四国麦丹': '四国麦丹',
    '九州空': '九州空',
    'NurseRobot_TypeT': 'NurseRobot_TypeT',
}

VOISONA_CHARACTERS = {
    '可不': '可不',
    '星界': '星界',
    '里命': '里命',
    'POPY': 'POPY',
    'ROSE': 'ROSE',
    '小春六花': '小春六花',
    '花隈千冬': '花隈千冬',
    '结月缘': '结月缘',
    '夏色花梨': '夏色花梨',
    '双叶凑音': '双叶凑音',
    '狐子': '狐子',
    '俊达萌': '俊达萌',
    '知声': '知声',
    '佐藤莎莎拉': '佐藤莎莎拉',
}

VOICEPEAK_CHARACTERS = {
    '东北切蒲英': '东北切蒲英',
    '俊达萌': '俊达萌',
    '四国麦丹': '四国麦丹',
    '九州空': '九州空',
    '结月缘': '结月缘',
    '绁星灯': '绁星灯',
    'VOICEPEAK (Unknown)': 'VOICEPEAK',
}

# 歌手引擎名称及对应的角色字典，按优先级排列（同一角色属于多个引擎时，取靠前者）。
# 后面那批（VOICEVOX 起）是 2026-09-30 照站上 `Category:音声合成软件模板` 里的引擎模板
# 整理出来的（utils/engine_characters.py）—— 一律**接在后面**，免得改了前面六个的优先级，
# 让已有条目的引擎 / 分类默默变样。
_BASE_ENGINES = [
    ("UTAU", {**UTAU_CHARACTERS, **EXTRA_CHARACTERS.get("UTAU", {})}),
    ("CeVIO", {**CEVIO_CHARACTERS, **EXTRA_CHARACTERS.get("CeVIO", {})}),
    ("Synthesizer V", {**SYNTHESIZER_V_CHARACTERS,
                       **EXTRA_CHARACTERS.get("Synthesizer V", {})}),
    ("NEUTRINO", {**NEUTRINO_CHARACTERS, **EXTRA_CHARACTERS.get("NEUTRINO", {})}),
    ("VoiSona", VOISONA_CHARACTERS),
    ("VOICEPEAK", {**VOICEPEAK_CHARACTERS, **EXTRA_CHARACTERS.get("VOICEPEAK", {})}),
]
# 前六个引擎已经认得的歌姬（`结月缘` / `绁星灯` / `樱乃空` …）：给它们补日文写法会把
# `結月ゆかり` / `紲星あかり` 从 VOCALOID 搬走 —— 而它们的主力就是 VOCALOID，
# 所以这一批照旧不碰（见 `wikitext-generation` 里那条「不要先归一化再查引擎」）。
_KNOWN_CHARACTERS = {name for _engine, _table in _BASE_ENGINES for name in _table}

# 一样道理，这两个名字的日文写法也不补：主力都是 VOCALOID（音街ウナ V4 / ずんだもん
# 是 NEUTRINO・VOICEVOX 那边的说法），模板里捎带的 VOICEROID / VOICEVOX 声库
# 不该把整首歌的引擎分类搬走。
_VOCALOID_FIRST = {"音街ウナ", "ずんだもん"}


# VocaDB 的 `artistType` 直接写着引擎（`ArtistType` 枚举，取值见 VocaDB/vocadb
# 的 `VocaDbWeb/Scripts/Models/Artists/ArtistType.ts`）：实测
# `四国めたん(VOICEVOX)` / `(UTAU)` / `(NEUTRINO)`、`紲星あかり(Voiceroid)` /
# `(AIVOICE)`、`春日部つむぎ(VOICEVOX)`、`小夜(ACEVirtualSinger)` —— 歌**这首**用哪
# 一副声库，VocaDB 的署名数据里就写着哪一个，比拿歌姬名去猜准确得多。
# 键一律小写（VocaDB 的大小写不统一，实测 `Voiceroid` 与 `VOICEVOX` 混着来）。
# ⚠️ 认不出引擎的类型（`OtherVoiceSynthesizer` / `Unknown` …）不给映射，交给角色表兜底；
# VocaDB 的枚举里**没有** VOICEPEAK / DiffSinger / DeepVocal / VocalSharp / X Studio，
# 所以那几个引擎全靠角色表。
ARTIST_TYPE_ENGINES = {
    "vocaloid": "VOCALOID",
    "utau": "UTAU",
    "cevio": "CeVIO",
    "synthesizerv": "Synthesizer V",
    "neutrino": "NEUTRINO",
    "voisona": "VoiSona",
    "voiceroid": "VOICEROID",
    "voicevox": "VOICEVOX",
    "aivoice": "A.I.VOICE",
    "acevirtualsinger": "ACE",
    # 实测《奔跑吧！蓝色！》的题头就是 `{{虚拟歌手歌曲荣誉题头|New Type|…}}`，
    # 分类也叫「使用New Type的歌曲」（= 初音ミク NT 那一类）
    "newtype": "New Type",
    # VocaDB 偶尔会把引擎名直接写进 artistType（实测 `VOICEPEAK`）；
    # 枚举里没有 VOICEPEAK，所以名字里的 `(VOICEPEAK)` 标记也要能当引擎用（见 `get_engine()`）
    "voicepeak": "VOICEPEAK",
}


def engine_from_type(artist_type: str) -> str:
    """VocaDB 的 `artistType` → 引擎名（认不出返回空串）。"""
    return ARTIST_TYPE_ENGINES.get(str(artist_type or "").strip().lower(), "")


# 带声库标记的名字：`小春六花 (VOICEPEAK)`、`初音ミク V4X (Original)`、`重音テトSV`。
# VocaDB 里 VOICEPEAK / DiffSinger 这类**不在 ArtistType 枚举里**的引擎，
# 署名只能写成 `(VOICEPEAK)` 这种后缀 —— 引擎就从这里认。
_BANK_MARKER_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
# 名字末尾直接用空格接的声库标记（`小春六花 AI`；站上 `小春六花 AI 2` 也常见）
_BANK_TAIL_MARKERS = ("AI",)
_BANK_TAIL_RE = re.compile(r"\s+(?:%s)(?:\s*\d+)?\s*$" % "|".join(_BANK_TAIL_MARKERS),
                           re.IGNORECASE)


def bank_marker(name: str) -> str:
    """名字末尾的声库标记（`小春六花 (VOICEPEAK)` → `VOICEPEAK`；没有则空串）。"""
    match = _BANK_MARKER_RE.search(str(name or ""))
    return match.group(1).strip() if match else ""


def strip_voice_bank(name: str) -> str:
    """去掉声库标记，只留歌姬名：

    `小春六花 (VOICEPEAK)` → `小春六花`、`小春六花 AI` → `小春六花`、
    `四国めたん(VOICEVOX)` → `四国めたん`。
    括号里不是引擎名时也去掉（`初音ミク V4X (Original)` → `初音ミク V4X`，
    再由 `name_shorten` 的子串规则收尾）。
    """
    text = str(name or "").strip()
    while True:
        stripped = _BANK_MARKER_RE.sub("", text)
        stripped = _BANK_TAIL_RE.sub("", stripped).strip()
        if stripped == text or not stripped:
            return stripped or text
        text = stripped


def is_known_character(name: str) -> bool:
    """这个名字能不能在歌姬表里查到（用来判断「去掉标记后仍然是一个歌姬」）。"""
    if name in vocaloid_names:
        return True
    return any(name in characters for _engine, characters in ENGINES)


def engine_from_bank_marker(name: str) -> str:
    """名字里的声库标记 → 引擎名（`小春六花 (VOICEPEAK)` → `VOICEPEAK`）。

    括号里的东西原样比对 `engine_names()`（忽略大小写与空格），
    认不出来（`(Original)` 这种）返回空串，交给角色表兜底。
    """
    marker = bank_marker(name)
    if not marker:
        return ""
    wanted = re.sub(r"\s+", "", marker).lower()
    for engine in engine_names():
        if re.sub(r"\s+", "", engine).lower() == wanted:
            return engine
    return ""


def _with_japanese(table: Dict[str, str], aliases: Dict[str, str]) -> Dict[str, str]:
    """给引擎表补上日文写法：`四国めたん` / `春日部つむぎ`（VocaDB 那边的名字）也要能查到
    VOICEVOX（表里的键是**站上条目名** `四国玫碳` / `春日部紬`）。

    `aliases` 就是 `vocaloid_names`（名 → 站上条目名）；目标已经在 `_KNOWN_CHARACTERS`
    里的一概不补 —— 那些歌姬的主力是 VOCALOID（`結月ゆかり` / `紲星あかり`…），
    补进来就会被搬去 CeVIO / VOICEROID。

    ⚠️ 特意**不**在 `get_engine()` 里做这种归一化：那样 `結月ゆかり` 会被
    `name_to_chinese()` 变成 `结月缘` 然后命中 CeVIO，正好把 VOCALOID 的歌姬认错
    （旧实现踩过这个坑）。表里同时写着两种写法才好分。
    """
    extra = {japanese: table[chinese] for japanese, chinese in aliases.items()
             if chinese in table and chinese not in _KNOWN_CHARACTERS
             and japanese not in _VOCALOID_FIRST}
    return {**table, **extra}


ENGINES = [
    *_BASE_ENGINES,
    ("VOICEVOX", _with_japanese(VOICEVOX_CHARACTERS, vocaloid_names)),
    ("VOICEROID", _with_japanese(VOICEROID_CHARACTERS, vocaloid_names)),
    ("A.I.VOICE", _with_japanese(A_I_VOICE_CHARACTERS, vocaloid_names)),
    ("ACE", _with_japanese(ACE_CHARACTERS, vocaloid_names)),
    ("DiffSinger", _with_japanese(DIFFSINGER_CHARACTERS, vocaloid_names)),
    ("DeepVocal", _with_japanese(DEEPVOCAL_CHARACTERS, vocaloid_names)),
    ("VocalSharp", _with_japanese(VOCALSHARP_CHARACTERS, vocaloid_names)),
    ("AISingers", _with_japanese(AISINGERS_CHARACTERS, vocaloid_names)),
    ("X Studio", _with_japanese(X_STUDIO_CHARACTERS, vocaloid_names)),
    ("VOICE MITH", _with_japanese(VOICE_MITH_CHARACTERS, vocaloid_names)),
    ("Vogen", _with_japanese(VOGEN_CHARACTERS, vocaloid_names)),
    ("Mac音", _with_japanese(MAC_CHARACTERS, vocaloid_names)),
    ("袅袅虚拟歌手", _with_japanese(NIAONIAO_CHARACTERS, vocaloid_names)),
]


def engine_names() -> List[str]:
    """所有能识别出来的引擎名（站上「分类:使用X的歌曲」里的写法，按优先级）。"""
    return [engine for engine, _characters in ENGINES]


def get_engine(name: str, artist_type: str = "") -> str:
    """返回歌手所属引擎，不属于上述引擎时默认视为 VOCALOID。

    `artist_type` 给得出 VocaDB 的 `artistType` 时**以它为准**（`engine_from_type()`）——
    那是「这首歌用了哪一副声库」的精确答案；认不出来（`OtherVoiceSynthesizer` / 空）
    才退回角色表。

    ⚠️ 表这边只按**原样**比：表里本来就同时收着日文与中文两种写法（`重音テト` / `重音Teto`、
    `春日部つむぎ` / `春日部紬`），不能先过 `name_to_chinese()` 再比 —— 那样
    `結月ゆかり`（VOCALOID）会被归一化成 `结月缘` 然后命中 CeVIO。
    """
    engine = engine_from_type(artist_type)
    if engine:
        return engine
    # `artist_type` 直接就是一个引擎名时（`parse_creators()` 会把名字里的 `(VOICEPEAK)`
    # 标记当提示传下来）也认它
    wanted = re.sub(r"\s+", "", str(artist_type or "")).lower()
    if wanted:
        for engine in engine_names():
            if re.sub(r"\s+", "", engine).lower() == wanted:
                return engine
    # 类型认不出来（`OtherVoiceSynthesizer` / 空）时先看名字里的声库标记：
    # `小春六花 (VOICEPEAK)` 按标记就是 VOICEPEAK（VocaDB 的枚举里没有这个引擎）
    engine = engine_from_bank_marker(name)
    if engine:
        return engine
    base = strip_voice_bank(name)
    for candidate in (name, base) if base != name else (name,):
        for engine, characters in ENGINES:
            if candidate in characters:
                return engine
    return "VOCALOID"


def engines_of(vocalists: Sequence) -> List[str]:
    """一组歌姬用到的合成引擎：每个歌姬只算一个引擎，去重并保持出现顺序。

    同一个歌姬可能同时挂在多个引擎的角色表里（例：可不 在 CeVIO / Synthesizer V / VoiSona
    三张表里都有），旧实现会把三个引擎全写进简介，这里只取优先最高的那个。
    主版本、其他版本的简介与荣誉题头都走这一套。

    `vocalists` 里可以放名字（`str`），也可以放 `models.creators.Person` —— 带 VocaDB 的
    `artist_type` 时以它为准（见 `get_engine()`）。
    """
    engines: List[str] = []
    for vocalist in vocalists:
        if isinstance(vocalist, str):
            name, artist_type = vocalist, ""
        else:
            name = getattr(vocalist, "name", str(vocalist))
            artist_type = getattr(vocalist, "artist_type", "")
        engine = get_engine(name, artist_type)
        if engine not in engines:
            engines.append(engine)
    return engines


def name_shorten(name: str) -> str:
    """歌姬名归一化：砍掉声库 / 版本标记，只留歌姬本身。

    ⚠️ VocaDB 里同一歌姬的不同声库会用**名字后缀**区分（用户 2026-10-05 报的
    《彩色粉笔装饰物》：`小春六花 (VOICEPEAK)` 与 `小春六花 AI` 没被归一化，
    结果一条曲目写出了两个「小春六花」）。另外那几种写法（`初音ミク V4X (Original)`、
    `重音テトSV`、`小春六花 AI`）靠的是「表里的键是名字的子串」这条老规则，
    而 `(VOICEPEAK)` 这种标记把歌姬名截断了，得先把标记去掉再查表。
    """
    for n in vocaloid_names.keys():
        if n in name:
            return n
    base = strip_voice_bank(name)
    if base != name and is_known_character(base):
        return name_to_chinese(base)
    return name


def name_to_chinese(name: str) -> str:
    if name in vocaloid_names:
        return vocaloid_names[name]
    for _, characters in ENGINES:
        if name in characters:
            return characters[name]
    # 带声库标记的名字（`小春六花 (VOICEPEAK)`）先去标记再查一次
    base = strip_voice_bank(name)
    if base != name:
        return name_to_chinese(base)
    return name


# 站内链接的写法：默认拿 `name_to_chinese` 的结果当条目名，
# 这里给「条目名与显示名不同」的歌姬单独写死（`条目名|显示名`，配合 `[[ ]]` 用）。
# 实测 voca.wiki《视力检查》：`|演唱 = [[Megpoid|GUMI]]`、简介「由[[Megpoid|GUMI]]演唱。」
WIKI_LINK_NAMES = {
    "GUMI": "Megpoid|GUMI",
}


def name_to_wiki(name: str) -> str:
    """歌姬名 → 站内链接的写入内容（可能是「条目名|显示名」）。

    只用在**要套 `[[ ]]` 的地方**（Songbox 的 |演唱、简介的「由…演唱」）。
    分类（`name_to_cat`）、歌手模板（`main.get_vocaloid_template`）等仍走 `name_to_chinese`，
    否则「条目名|显示名」里的竖线会跑进模板参数或 `[[分类:…]]` 里。
    """
    chinese = name_to_chinese(name)
    return WIKI_LINK_NAMES.get(chinese, chinese)


# 快给我变.jpg
cat_transform = {
    "GUMI": "Megpoid",
    "神威乐步": "Gackpoid"
}


def name_to_cat(name: str) -> str:
    name = name_to_chinese(name)
    if name in cat_transform.keys():
        return cat_transform[name]
    return name
