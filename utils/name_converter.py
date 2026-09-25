vocaloid_names = {
    '初音ミク': "初音未来",
    '鏡音リン': "镜音铃",
    '鏡音レン': "镜音连",
    '巡音ルカ': "巡音流歌",
    'カイト': "KAITO",
    'メイコ': "MEIKO",
    '音街ウナ': "音街鳗",
    '歌愛ユキ': "歌爱雪",
    '結月ゆかり': "结月缘",
    '神威がくぽ': "神威乐步",
    'ブイフラワ': "v flower",
    'イア': "IA",
    'マユ': "MAYU",
    'GUMI': "GUMI",
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
}

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
    '弦卷真纪': '弦卷真纪',
    '可不': '可不',
    '星界': '星界',
    '结月缘': '结月缘',
    '花隈千冬': '花隈千冬',
    '重音テトSV': '重音Teto',
    'GUMI': 'GUMI',
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
    '俊达萌': '俊达萌',
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

# 歌手引擎名称及对应的角色字典，按优先级排列（同一角色属于多个引擎时，取靠前者）
ENGINES = [
    ("UTAU", UTAU_CHARACTERS),
    ("CeVIO", CEVIO_CHARACTERS),
    ("Synthesizer V", SYNTHESIZER_V_CHARACTERS),
    ("NEUTRINO", NEUTRINO_CHARACTERS),
    ("VoiSona", VOISONA_CHARACTERS),
    ("VOICEPEAK", VOICEPEAK_CHARACTERS),
]


def get_engine(name: str) -> str:
    """返回歌手所属引擎，不属于上述引擎时默认视为 VOCALOID。"""
    for engine, characters in ENGINES:
        if name in characters:
            return engine
    return "VOCALOID"


def name_shorten(name: str) -> str:
    for n in vocaloid_names.keys():
        if n in name:
            return n
    return name


def name_to_chinese(name: str) -> str:
    if name in vocaloid_names:
        return vocaloid_names[name]
    for _, characters in ENGINES:
        if name in characters:
            return characters[name]
    return name


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
