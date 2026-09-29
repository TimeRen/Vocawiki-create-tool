"""同一首歌的「其他版本」（参 voca.wiki 条目《鸟之诗》）。

候选列表来自 VocaDB 的 `alternateVersions`（`utils/vocadb.parse_other_versions` 填进
`Song.other_versions`），这里只负责两件事：

1. 给每个候选起个版本名（`bt1=` / `;版本名` 上写的那句话）；
2. 逐个问用户要加入哪些版本，以及每个版本的 B 站链接、是否官方投稿、（可选）一句简介。

wikitext 的拼装在 `main.py`（整页套 `{{tabs}}` + 「== 歌曲 ==」里按版本列播放器）。
整段流程受 `config.yaml` 的 `wikitext.other_versions` 控制（默认开）。
"""
import logging
import re
from collections import Counter
from typing import List, Sequence

from i18n.i18n import _
from models.song import Song
from models.video import OtherVersion, VideoSite, prompt_video_link, video_from_site
from utils import vocadb
from utils.helpers import prompt_choices
from utils.name_converter import engines_of, get_engine, name_to_chinese
from utils.string import datetime_to_ymd, is_empty

# 一次最多列出多少个候选：热门曲子在 VocaDB 上的 alternateVersions 有一两百条，
# 全塞进选项列表没法看（终端模式下要滚好几屏），所以截断一部分并记日志
MAX_CANDIDATES = 30

# `{{tabs}}` 上主版本的按钮名：多个版本同台时「P主歌姬版」又长又不好认（用户 2026-09 要求）
MAIN_TAB_LABEL = "原版"


def _vocalist_text(version: OtherVersion) -> str:
    return "、".join(name_to_chinese(name) for name in version.vocalists)


def _base_label(version: OtherVersion, main_engines: Sequence[str]) -> str:
    """`歌姬[ 引擎]版`；歌姬认不出来时退回 P主 / 版本类型。"""
    vocalists = _vocalist_text(version)
    if not vocalists:
        fallback = version.producers[0] if version.producers else ""
        fallback = fallback or version.song_type or _("other_version_default")
        return f"{fallback}版"
    engine = get_engine(version.vocalists[0])
    # 引擎和本曲不同才写（参《鸟之诗》的「重音Teto UTAU版」「IA VOCALOID版」）
    suffix = "" if engine in main_engines else f" {engine}"
    return f"{vocalists}{suffix}版"


def _version_name_extra(song: Song, version: OtherVersion) -> str:
    """VocaDB 上这个版本的名字里多出来的那截：`ナ2モノ (ROCK_VER)` → `ROCK_VER`。

    同一首歌的多个版本常常同名（`ナ2モノ`、`ナ2モノ (ROCK_VER)`），
    多出来的那截是区分它们最有用的线索。
    """
    name = (version.name or "").strip()
    for base in (getattr(song, "name_jap", ""), getattr(song, "name_chs", "")):
        if base and name.startswith(base):
            name = name[len(base):].strip()
            break
    return name.strip("()（）[]【】").strip()


def _version_tag(song: Song, version: OtherVersion) -> str:
    """重名时补在版本名后面的那截：优先 VocaDB 名字里的说明，其次投稿年份，最后版本类型。"""
    extra = _version_name_extra(song, version)
    if extra:
        return extra
    if version.publish_date is not None and version.publish_date.year > 1970:
        return f"{version.publish_date.year}年"
    return version.song_type or _("other_version_default")


def _short_label(tag: str) -> str:
    """补充说明 → `{{tabs}}` 按钮上的短名：`ROCK_VER` → `ROCK版`、`2024年` → `2024年版`。

    tab 按钮很窄，`Shu初音未来、巡音流歌版（ROCK_VER）` 这种整句挤在一起认不出来
    （用户 2026-09 要求 bt1 写「原版」、bt2 写「ROCK版」）；`== 歌曲 ==` 里的 `;版本名`
    仍是完整版本名。
    """
    name = re.sub(r"[_\-\s]*(?:ver|version)\.?$", "", tag or "",
                  flags=re.IGNORECASE).strip(" _-")
    if not name:
        return tag or ""
    return name if name.endswith("版") else f"{name}版"


def label_versions(song: Song, versions: Sequence[OtherVersion] = None) -> None:
    """给候选版本起名（写进 `btN=` 与 `;版本名`）。

    规则：`[P主]歌姬[ 引擎]版`。引擎只在和本曲不同时写；和**主版本**或其他候选重名时
    先补上 P主（参《鸟之诗》的でんげん版 / mar 版），还是重名（同一个 P主 的两个版本，
    例：`ナ2モノ` 与 `ナ2モノ (ROCK_VER)`）再补 VocaDB 名字里的说明或投稿年份。
    补了说明的版本在 tab 上只写那一截（`ROCK_VER` → `ROCK版`），见 `_short_label`。
    """
    versions = list(versions if versions is not None else song.other_versions)
    main_engines = engines_of(song.creators.vocalists)
    main_label = main_version_label(song)
    for version in versions:
        version.label = _base_label(version, main_engines)
        version.tab_label = version.label
    counts = Counter(version.label for version in versions)
    for version in versions:
        if (counts[version.label] > 1 or version.label == main_label) and version.producers:
            version.label = f"{version.producers[0]}{version.label}"
            version.tab_label = version.label
    counts = Counter(version.label for version in versions)
    for version in versions:
        if counts[version.label] > 1 or version.label == main_label:
            tag = _version_tag(song, version)
            version.label = f"{version.label}（{tag}）"
            version.tab_label = _short_label(tag)


def main_version_label(song: Song) -> str:
    """本曲（主版本）在「== 歌曲 ==」的 `;版本名` 里叫什么。

    和候选版本同一套规则：`P主 + 歌姬 + 「版」`（参《鸟之诗》的「でんげん初音未来版」）。
    `{{tabs}}` 上主版本不写这一长串，只写「原版」（`MAIN_TAB_LABEL`）。
    """
    vocalists = "、".join(name_to_chinese(name) for name in song.creators.vocalists_str())
    producers = [person.name for person in getattr(song.creators, "producers", [])]
    prefix = producers[0] if producers else ""
    if not vocalists:
        return f"{prefix}版" if prefix else _("other_version_default")
    return f"{prefix}{vocalists}版"


def _option_text(song: Song, version: OtherVersion) -> str:
    parts = [version.label]
    if _version_name_extra(song, version):        # VocaDB 上这个版本的名字（和歌名不同才写）
        parts.append(version.name)
    parts.append(version.artist_string)
    if version.publish_date is not None and version.publish_date.year > 1970:
        parts.append(datetime_to_ymd(version.publish_date))
    return "｜".join(part for part in parts if not is_empty(part))


def _fetch_bilibili(identifier: str, canonical: bool):
    """取 B 站稿件的播放量与投稿日（荣誉题头 / `bb_date` 要用）；取不到就只留 ID。"""
    return video_from_site(VideoSite.BILIBILI, identifier, canonical)


def choose_other_versions(song: Song) -> List[OtherVersion]:
    """逐个问「要加入哪个其他版本」，并为每个选中的版本收集 B 站链接与官方与否。

    返回选中的版本列表（同时写回 `song.other_versions`）。一个都没选时返回空表，
    上层就按单版本生成（不套 `{{tabs}}`）。
    """
    candidates = list(getattr(song, "other_versions", None) or [])
    if not candidates:
        logging.info("VocaDB 上没有这首歌的其他版本（alternateVersions 为空），跳过。")
        return []
    label_versions(song, candidates)
    if len(candidates) > MAX_CANDIDATES:
        logging.info("VocaDB 上一共有 %d 个其他版本，只列出前 %d 个。",
                     len(candidates), MAX_CANDIDATES)
        candidates = candidates[:MAX_CANDIDATES]
    chosen: List[OtherVersion] = []
    while candidates:
        prompt = _("other_versions_choose") if not chosen else _("other_versions_choose_more")
        options = [_option_text(song, version) for version in candidates]
        options.append(_("other_versions_done"))
        index = prompt_choices(prompt, options) - 1
        if index == len(candidates):                 # 最后一项 = 不加了
            break
        version = candidates.pop(index)
        link = prompt_video_link(_("other_version_bilibili").format(label=version.label),
                                 (VideoSite.BILIBILI,))
        if link is None:
            logging.warning("「%s」没有填 B 站链接，这个版本不写进条目。", version.label)
            continue
        canonical = prompt_choices(
            _("other_version_canonical").format(label=version.label),
            [_("Yes"), _("No")]) == 1
        video = _fetch_bilibili(link.identifier, canonical)
        if video is None:                            # 理论上不会；取不到就退回本地解析的结果
            video = link
        version.video = video
        version.canonical = canonical
        # 它自己在 nico / YouTube 上的稿件（Songbox 的 nnd_ / yt_ 栏）与收录它的专辑（简介要用）
        vocadb.get_version_details(song, version)
        logging.info("其他版本：%s（%s，官方投稿=%s）", version.label,
                     version.video.identifier, canonical)
        chosen.append(version)
    song.other_versions = chosen
    if not chosen:
        logging.info("没有选择任何其他版本，按单版本生成。")
    return chosen
