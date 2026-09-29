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
    return [p.name for p in lst]


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


# 「曲绘」与「PV制作」是同一个人时并成一栏，栏名这么写（参 voca.wiki《学习室》：`|group2 = 曲绘、PV制作`）
MERGED_ROLE_PAIRS = (("曲绘", "PV制作"),)


def merge_staff_rows(rows: List[Staff]) -> List[Staff]:
    """同一个人占了两栏时并成一栏（`rows` 要已按 `role_priority` 排好序）。

    - 「曲绘」与「PV制作」的人**完全一样** → 并成一栏「曲绘、PV制作」（用户 2026-09 对照
      voca.wiki《小小星座》要求；VocaDB 上 月乃 同时挂着 Illustrator 与 Animator 两个角色，
      老实现就会写出两张一样的表）；
    - 「编曲」的人**全都已经在「词曲」那一栏里** → 丢掉「编曲」栏（人还在词曲里，
      参《小小星座》的同一句要求）。

    只影响「VOCALOID Songbox Introduction」这张表，不动 `Creators.staffs`：
    封面那份「曲绘 by …」还要从 `staffs["曲绘"]` 里取人。
    """
    merged: List[Staff] = list(rows)
    for first, second in MERGED_ROLE_PAIRS:
        names = {role: [person.name for person in people] for role, people in merged}
        if first in names and second in names and names[first] == names[second]:
            people = dict(merged)[first]
            merged = [(f"{first}、{second}", people) if role == first else (role, persons)
                      for role, persons in merged if role != second]
    composer_names = set()
    for role, people in merged:
        if role == "词曲":
            composer_names = {person.name for person in people}
    if composer_names:
        merged = [(role, people) for role, people in merged
                  if not (role == "编曲" and all(person.name in composer_names for person in people))]
    return merged


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
