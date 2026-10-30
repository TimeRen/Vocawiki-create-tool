from dataclasses import dataclass, field

from typing import List, Dict, Tuple


@dataclass
class Person:
    name: str
    name_eng: List[str] = field(default_factory=list)
    # VocaDB 的 `artistType`（`Vocaloid` / `CeVIO` / `SynthesizerV` / `VOICEVOX` …）——
    # 引擎识别的**首选**依据（`name_converter.engines_of()` 会用它，见 `get_engine()`）：
    # 同一歌姬名下好几副声库时，只有这个字段能说清这首用的是哪一副。
    # 从 artistString 兑底出来的（或手填的）Person 给空串，那时才回去查角色表。
    artist_type: str = ""


def person_list_to_str(lst: List[Person]) -> List[str]:
    """人名列表（按出现顺序去重）。

    ⚠️ **同一个歌姬的不同声库要只写一次**（用户 2026-10-05 报《彩色粉笔装饰物》：
    歌曲栏写成了两个「小春六花」—— VocaDB 里那是 `小春六花 (VOICEPEAK)` 与 `小春六花 AI`，
    归一化后名字一样）。注意 `Creators.vocalists` **不去重**：引擎列表要靠里面每个
    `Person.artist_type` 才能把 VOICEPEAK 与 Synthesizer V 都认出来。
    """
    result: List[str] = []
    for person in lst:
        if person.name not in result:
            result.append(person.name)
    return result


Staff = Tuple[str, List[Person]]

role_mapping = {
    "PLACEHOLDER": "词曲",
    "Composer": "作曲",
    "Lyricist": "作词",
    "Arranger": "编曲",
    "Arrange": "编曲",
    "VoiceManipulator": "调教",
    "Mixer": "混音",
    "Mastering": "母带处理",
    "Instrumentalist": "乐器演奏",
    "Illustrator": "曲绘",
    "Animator": "PV制作",
    "Animators": "PV制作",
    "Publisher": "出版社",
    "Vocalist": "演唱",
}


def role_transform(role: str) -> str:
    return role_mapping.get(role, role)


def role_priority(role: str) -> int:
    for index, value in enumerate(role_mapping.values()):
        if role == value:
            return index
    return len(role_mapping) // 2


def merge_composer_lyricist(staffs: Dict[str, List[Person]]) -> None:
    composers = staffs.get("作曲", [])
    lyricists = staffs.get("作词", [])
    lyricist_names = {person.name for person in lyricists}
    shared = [person for person in composers if person.name in lyricist_names]
    if not shared:
        return
    shared_names = {person.name for person in shared}
    staffs["词曲"] = staffs.get("词曲", []) + shared
    staffs["作曲"] = [person for person in composers if person.name not in shared_names]
    staffs["作词"] = [person for person in lyricists if person.name not in shared_names]
    if not staffs["作曲"]:
        del staffs["作曲"]
    if not staffs["作词"]:
        del staffs["作词"]


# 「曲绘」与「PV制作」是同一个（同一批）人时，栏名这么写（参 voca.wiki《学习室》：
# `|group2 = 曲绘、PV制作`）。**只有这两栏单独并在一起时**才换成 `、`：同一个人还兼着别的角色时
# 一律用 `</br>` 连（参《Poolside Daydream》的 `作曲<br/>编曲<br/>作词<br/>曲绘<br/>PV`）。
MERGED_ROLE_PAIRS = (("曲绘", "PV制作"),)

# 同一个人占了几栏就并成一栏，栏名按 `role_priority` 顺序用 `</br>` 连起来。
# 用户 2026-10-12 对照 voca.wiki《Gimme Money》diff 260901 要求（Yopi 一个人写 作曲 / 编曲 / 混音）：
#
#     |group1 = 作曲</br>编曲</br>混音
#     |list1 = Yopi
#
# 用户随后又要求「同一个人不管什么角色都并成一栏」，所以**不做角色白名单**：作词与曲绘
# 是同一个人时也并（站上《伪造少女》的 `部分作词<br>曲绘` 就是这个写法；
# 《无可奈何》`编曲</br>混音</br>母带处理</br>乐器演奏》、《Front Glass ni chou à la crème》
# `调教</br>混音</br>母带处理》则是同一类的另外两例）。


def merge_person_rows(rows: List[Staff]) -> List[Staff]:
    """同一个（同一批）人占了几栏时并成一栏（`rows` 要已按 `role_priority` 排好序）。

    栏名 = 这几个角色按出现顺序（也就是 `role_priority` 顺序）用 `</br>` 连接，合并后落在
    这一组里**最靠前**那一栏的位置上。是不是「同一批人」按**名字集合**判断：
    作曲 `A` 与 编曲 `A` 并；`作曲 A` 与 `编曲 A、B` 不并（多出来的人不能被吞掉）。
    """
    merged: List[Staff] = []
    index_of: Dict[frozenset, int] = {}            # 人名集合 → 并好的那一栏在 merged 里的下标
    for role, people in rows:
        key = frozenset(person.name for person in people)
        if people and key in index_of:
            index = index_of[key]
            label, kept = merged[index]
            merged[index] = (f"{label}</br>{role}", kept)
            continue
        if people:
            index_of[key] = len(merged)
        merged.append((role, people))
    return merged


# VocaDB 的 `artistType` 里这些是「团队 / 工作室」而不是个人。实测《为犬之人》：
# 曲绘写着 `ATUHI` 与 `スタジオ猿芝居`，后者是团队名（artistType = OtherGroup），
# 条目里只该署 `曲绘 by ATUHI`（用户 2026-10-30 对照 diff 276889 要求）。
GROUP_ARTIST_TYPES = ("Group", "OtherGroup", "Band", "Circle", "Label")


def is_group_person(person) -> bool:
    """这个「人」其实是团队 / 工作室吗（只看 VocaDB 的 `artist_type`）？"""
    kind = str(getattr(person, "artist_type", "") or "").strip()
    return kind in GROUP_ARTIST_TYPES


def credited_illustrators(people: List[Person]) -> List[Person]:
    """曲绘署名里去掉团队 / 工作室；**全是团队时原样返回**（宁可写团队名，也不留空栏）。"""
    individuals = [person for person in people or [] if not is_group_person(person)]
    return individuals or list(people or [])


def merge_staff_rows(rows: List[Staff]) -> List[Staff]:
    """同一个人占了两栏时并成一栏（`rows` 要已按 `role_priority` 排好序）。

    - 「编曲」的人**全都已经在「词曲」那一栏里** → 丢掉「编曲」栏（人还在词曲里，
      参 voca.wiki《小小星座》的要求）；
    - **不分角色**：同一个（同一批）人身兼数职就并成一栏，栏名按角色顺序用 `</br>` 连起来
      （见 `merge_person_rows`；用户 2026-10-12 要求「不管什么角色都并成一栏」）；
    - 并出来正好是「曲绘」+「PV制作」两栏时，栏名改写成「曲绘、PV制作」
      （参《学习室》；用户 2026-09 对照《小小星座》要求）。

    只影响「VOCALOID Songbox Introduction」这张表，不动 `Creators.staffs`：
    封面那份「曲绘 by …」还要从 `staffs["曲绘"]` 里取人（两处都用
    `credited_illustrators()` 把团队 / 工作室滤掉）。
    """
    merged: List[Staff] = list(rows)
    composer_names = set()
    for role, people in merged:
        if role == "词曲":
            composer_names = {person.name for person in people}
    if composer_names:
        merged = [(role, people) for role, people in merged
                  if not (role == "编曲" and all(person.name in composer_names for person in people))]
    merged = merge_person_rows(merged)
    labels = {f"{first}</br>{second}": f"{first}、{second}"
              for first, second in MERGED_ROLE_PAIRS}
    return [(labels.get(role, role), people) for role, people in merged]


@dataclass
class Creators:
    producers: List[Person]
    vocalists: List[Person]
    staffs: Dict[str, List[Person]]

    def producers_str(self) -> List[str]:
        return person_list_to_str(self.producers)

    def vocalists_str(self) -> List[str]:
        return person_list_to_str(self.vocalists)

    def staff_list(self) -> List[Staff]:
        return [(role, self.staffs[role]) for role in self.staffs]
