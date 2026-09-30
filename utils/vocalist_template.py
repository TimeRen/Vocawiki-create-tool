"""歌姬模板（虚拟歌手导航框，如 `Template:歌爱雪` / `Template:重音Teto`）的数据侧。

侧栏第三个功能「生成歌姬模板」用的就是这里：界面在 `utils/ui/vocalist_panel.py`（曲目）、
`utils/ui/producer_style_panel.py`（样式，与 P主模板共用一套）、`utils/ui/submit_panel.py`
（提交，支持多页面切换），流程在 `utils/vocalist_editor.py`。

站上的歌姬模板长这样（2026-09-30 实测 `Template:歌爱雪`）：

    {{#invoke:Nav|box
    |name = 歌爱雪
    |title = {{coloredlink|#333333|歌爱雪}}
    |titlestyle = background:#f38286;color:#333333
    |list1 = …相关人物…
    |list2 = …歌曲…
       |group1 = 传说曲 → |group1 = niconico → |group1 = 2011年 → 曲子…
       |group2 = 殿堂曲 → |group1 = niconico → |group1 = 2010年 → 曲子…
       |group3 = 其他   → |group1 = {{mousetext|部分未殿堂曲|指niconico及bilibili投稿}}…
    }}

曲子多的时候会**按投稿年拆成子页**（`Template:重音Teto/2024`），主模板只留
「相关人物 + 各年份的转接行」（用户 2026-09-30 给的就是这个形态）：

    |list2 = {{重音Teto/2008|nocate=1|state=uncollapsed|child|noabove}}
    |list3 = {{重音Teto/2009|nocate=1|state=uncollapsed|child|noabove}}

曲目从哪来（用户 2026-09-30 定的口径）：

* **殿堂 / 传说 / 神话**：歌姬所属**引擎**的殿堂曲 / 传说曲 / 神话曲页面
  （`VOCALOID殿堂曲/2008年投稿`、`VOCALOID神话曲/YouTube投稿/2012年投稿` …）。
  页面**标题**给出栏（殿堂曲/传说曲/神话曲）、站点（`/YouTube投稿`、`/bilibili投稿`，
  没有就是 niconico）与投稿年（`/2012年投稿`）；正文里
  `{{Temple Song|…|曲目 = [[条目名|日文名]]}}` / `{{Song Honor|…|条目 = [[条目名]]}}` 给条目名。
* **其他**：歌姬的分类 `Category:<歌姬>歌曲` 里**没进上面那些栏**的曲子，
  再按歌曲条目的投稿站点分「部分未殿堂曲」（有 niconico / bilibili 稿件）与
  「部分YouTube投稿」（只有 YouTube 稿件）。
* 同一首歌在不同站点可以处在不同档次（`强风大背头` 在 niconico 是传说曲、在 YouTube 破亿），
  所以每一栏的站点格按「**每个站点取它在那儿达到的最高档**」来定。
* **拿不准的**（殿堂页里查不到、条目里的荣誉题头跟殿堂页打架、连投稿年都取不到）
  都记进 `work.flags`：曲目页会弹窗让用户人工复核（用户 2026-09-30 要求）。

⚠️ 不要拿 `name_to_chinese()` 归一化歌姬名再查引擎（见 `/memories/repo/wikitext-generation.md`）：
`結月ゆかり` 会被搬去 CeVIO。这里的歌姬名一律是**条目名**（中文），`get_engine()` 直接吃。
"""
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from config.config import get_output_path
from utils import login, wiki_api
from utils.name_converter import get_engine
from utils.producer_template import (DEFAULT_STYLES, SONGBOX_RE, clean_title,
                                     declared_song_names, insert_into_pages,
                                     style_params)
from utils.string import safe_filename

TEMPLATE_PREFIX = "Template:"
# 站上的分类：不分年份的歌姬模板挂在「Category:虚拟歌手模板」（`Template:歌爱雪` 实测）
VOCALIST_TEMPLATE_CATEGORY = "[[Category:虚拟歌手模板]]"
# 拆成年份子页时主模板挂的分类（`Template:重音Teto` 实测挂在「Category:重音Teto模板」）
YEAR_CATEGORY_SUFFIX = "模板"

# ---------------------------------------------------------------- 栏 / 站点
RANK_HALL = "殿堂曲"
RANK_LEGEND = "传说曲"
RANK_MYTH = "神话曲"
RANK_BILLION = "破亿播放曲目"           # 播放量破亿：`Template:重音Teto/2024` 里那一栏
RANK_OTHER = "其他"
# 页面标题里的词 ↔ 档次（1 殿堂 / 2 传说 / 3 神话 / 4 破亿）
RANK_LEVELS: Dict[str, int] = {RANK_HALL: 1, RANK_LEGEND: 2, RANK_MYTH: 3, "破亿曲": 4}
LEVEL_TITLES: Dict[int, str] = {1: RANK_HALL, 2: RANK_LEGEND, 3: RANK_MYTH, 4: RANK_BILLION}
# 栏名（模板里写的那个）→ 档次：`places` 里存的是栏名，排序与比较得用这个表
TITLE_LEVELS: Dict[str, int] = {**RANK_LEVELS, RANK_BILLION: 4}
HALL_WORDS: Tuple[str, ...] = ("殿堂曲", "传说曲", "神话曲", "破亿曲")
# 栏在模板里的出场顺序（神话 → 传说 → 殿堂 → 破亿 → 其他）
RANK_ORDER: Tuple[str, ...] = (RANK_MYTH, RANK_LEGEND, RANK_HALL, RANK_BILLION, RANK_OTHER)

STATION_NICO = "niconico"
STATION_YOUTUBE = "YouTube"
STATION_BILIBILI = "bilibili"
# 站点的显示顺序（`Template:重音Teto/2024` 是 niconico / YouTube / bilibili）
STATIONS: Tuple[str, ...] = (STATION_NICO, STATION_YOUTUBE, STATION_BILIBILI)
STATION_ALIASES: Dict[str, str] = {
    "niconico": STATION_NICO, "nico": STATION_NICO, "nn": STATION_NICO,
    "youtube": STATION_YOUTUBE, "yt": STATION_YOUTUBE, "y": STATION_YOUTUBE,
    "bilibili": STATION_BILIBILI, "bili": STATION_BILIBILI, "bb": STATION_BILIBILI,
    "b": STATION_BILIBILI,
}
# 信息框里的站点 ID 参数（`|nnd_id` / `|yt_id` / `|bb_id`；`投稿 =` 里的 card 用 yt/bb）
STATION_ID_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("nnd_id", STATION_NICO), ("nn_id", STATION_NICO), ("yt_id", STATION_YOUTUBE),
    ("bb_id", STATION_BILIBILI),
)
# 其他栏的两个子栏（`Template:歌爱雪` 的小字提示）
OTHER_UNHALL = "部分未殿堂曲"
OTHER_YOUTUBE = "部分YouTube投稿"
OTHER_UNHALL_NOTE = "指niconico及bilibili投稿"
# 荣誉题头里的站点档位参数：nrank = niconico、yrank = YouTube、brank = bilibili
HONOR_RANK_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("nrank", STATION_NICO), ("yrank", STATION_YOUTUBE), ("brank", STATION_BILIBILI),
)

# ---------------------------------------------------------------- 正则
HONOR_HEADER_RE = re.compile(r"\{\{\s*虚拟歌手歌曲荣誉题头\s*\|([^}]*)\}\}")
# 页面标题：<引擎><殿堂曲|传说曲|神话曲|破亿曲>[/<站点>投稿][/<年份>年投稿]
HALL_TITLE_RE = re.compile(
    r"^(?P<engine>[^/]+?)(?P<word>殿堂曲|传说曲|神话曲|破亿曲)"
    r"(?:/(?P<station>[A-Za-z]+)投稿)?"
    r"(?:/(?P<year>\d{4})年投稿)?$")
# 殿堂页里每首歌的写法：`{{Temple Song|…|曲目 = [[条目名|日文名]]}}`（niconico 页）
# 与 `{{Song Honor|歌手 = …|条目 = [[条目名]]}}`（YouTube / bilibili 页）
HALL_ENTRY_TEMPLATES = ("Temple Song", "Song Honor", "TempleSong", "SongHonor")
HALL_ENTRY_CALL_RE = re.compile(r"\{\{\s*(?:" + "|".join(HALL_ENTRY_TEMPLATES) + r")\s*\|",
                                re.IGNORECASE)
# 曲目那一行：`|曲目 = [[条目名|日文名]]` / `|条目 = [[条目名]]`
# ⚠️ 站上有**繁体**写法 `|條目 =`（实测 `VOCALOID传说曲/2023年投稿`），必须一起收，
# 否则那些页只能读到零星几条（用户 2026-09-30 报的「传说曲怎么少了一半」）。
# ⚠️ 值里会嵌 `{{lj|…}}`（`[[强风大背头|{{lj|強風オーバック}}]]`），所以**不能**用
# `[^\n}]+` 收 —— 碰到 `}` 就断了，链接只剩半截。
HALL_ENTRY_RE = re.compile(r"\|\s*(?:曲目|条目|條目)\s*=\s*([^\n]*)")
# ⚠️ 殿堂曲页面里**也**列着传说 / 神话曲，靠这几个参数标出来（实测
# `VOCALOID殿堂曲/2008年投稿` 里 `四叶草♣俱乐部` 写着 `|传说 = 1`）——
# 不看它们就会把传说曲当成殿堂曲（用户 2026-09-30 报的）。
HALL_FLAG_LEVELS: Tuple[Tuple[str, int], ...] = (("破亿", 4), ("神话", 3), ("传说", 2),
                                                  ("殿堂", 1))
WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
LJ_OPEN_RE = re.compile(r"\{\{\s*(?:lj|lang\|ja)\s*\|", re.IGNORECASE)
LJ_RE = re.compile(r"\{\{\s*(?:lj|lang\|ja)\s*\|([^{}]*)\}\}", re.IGNORECASE)
SONGBOX_PARAM_RE = re.compile(r"\|\s*([^=|\n]+?)\s*=\s*([^\n]*)")
SONGBOX_CARD_RE = re.compile(r"\{\{\s*[^|{}\n]*Songbox/card\s*\|([^}]*)\}\}", re.IGNORECASE)
SINGER_PARAM_RE = re.compile(r"\|\s*演唱\s*=\s*([^\n]*)")
DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
YEAR_RE = re.compile(r"(\d{4})")
# 从既有模板里继承的两处：样式（titlestyle / groupstyle / liststyle）与「相关人物」那一栏
STYLE_PARAM_RE = re.compile(r"\|\s*(titlestyle|groupstyle|liststyle|evenstyle)\s*=\s*([^\n]*)")

# 一个歌姬分类里最多处理多少首（`Category:初音未来歌曲` 有 5254 首，生成出来根本没法看，
# 也没人核得完 —— 超过就当场报错，让用户先手工拆）
MAX_SONGS = 3000
MAX_FLAGS = 200                          # 弹窗复核的条数上限（再多也没人点得完）
HALL_BATCH = 10                          # 殿堂页一次请求带几个标题（见 `fetch_halls()`）


# ============================================================ 数据结构

@dataclass
class HallEntry:
    """殿堂 / 传说 / 神话页面里的一首歌。"""

    title: str                          # 条目名
    ja: str = ""                        # 页面里写的日文名（`[[条目|日文]]` 的后半截）
    rank: str = RANK_HALL
    station: str = STATION_NICO
    year: str = ""                      # 页面标题给的投稿年
    page: str = ""                      # 从哪个页面读到的（排查用）

    @property
    def level(self) -> int:
        return RANK_LEVELS.get(self.rank, 1)


@dataclass
class SongFact:
    """一首歌曲条目里读出来的事实（信息框 + 荣誉题头）。"""

    title: str
    exists: bool = False
    is_song: bool = False
    ja: str = ""
    stations: Tuple[str, ...] = ()
    ranks: Dict[str, int] = field(default_factory=dict)     # 站点 → 荣誉题头里的档
    date: str = ""                                          # 投稿日期（YYYY-MM-DD）
    singers: Tuple[str, ...] = ()

    @property
    def year(self) -> str:
        match = YEAR_RE.search(self.date or "")
        return match.group(1) if match else ""


@dataclass
class VocalistSong:
    """要写进模板的一首歌。

    ⚠️ 分栏是**按站点各算各的**：同一首歌在 niconico 是神话曲、在 YouTube 破亿时，
    两边都要出现（实测 `Template:重音Teto/2024`：`催眠者` 就在神话曲/niconico 与
    破亿播放曲目/YouTube 里各有一份）。所以真正存的是 `places`（一串 (栏, 站点)），
    `rank` / `stations` 只是「最高那一栏」的方便写法。
    """

    title: str                                  # 条目名（红链时是日文原名）
    ja: str = ""
    year: str = ""                              # 投稿年（分年份子页 / 年份小格看它）
    date: str = ""
    places: List[Tuple[str, str]] = field(default_factory=list)   # [(栏, 站点)]
    kind: str = ""                              # 其他栏的子栏（部分未殿堂曲 / 部分YouTube投稿）
    source: str = ""                            # 殿堂页 / 荣誉题头 / 分类
    note: str = ""                              # 界面上的说明（为什么在这个栏）
    page_exists: bool = True
    flag: str = ""                              # 需要人工复核的原因（空 = 没问题）

    @property
    def rank(self) -> str:
        """最高那一栏（没分栏时算「其他」）。

        按档位取最大，不拄 `places` 的顺序 —— 界面里手改过（`set_rank`）或数据从别处
        拼出来的对象，顺序不一定排好。
        """
        if not self.places:
            return RANK_OTHER
        return max(self.places, key=lambda item: TITLE_LEVELS.get(item[0], 1))[0]

    @property
    def stations(self) -> Tuple[str, ...]:
        """最高那一栏里的站点（按 niconico / YouTube / bilibili 的顺序）。"""
        ranks = {rank for rank, _station in self.places}
        target = self.rank if self.rank in ranks else RANK_OTHER
        return tuple(station for station in STATIONS
                     if (target, station) in self.places)

    @property
    def all_stations(self) -> Tuple[str, ...]:
        """它在**任何**栏里出现的站点（其他栏分「部分未殿堂曲 / 部分YouTube投稿」看它）。"""
        found = {station for _rank, station in self.places}
        return tuple(station for station in STATIONS if station in found)

    def set_rank(self, rank: str) -> None:
        """改栏（界面里改「栏」那一格）：站点保留，整体换个栏名。"""
        rank = str(rank or "").strip() or RANK_OTHER
        stations = [station for _rank, station in self.places] or [STATION_NICO]
        self.places = [(rank, station) for station in stations]
        if rank == RANK_OTHER and not self.kind:
            self.kind = OTHER_YOUTUBE if self.all_stations == (STATION_YOUTUBE,) \
                else OTHER_UNHALL

    def set_stations(self, stations: Sequence[str]) -> None:
        """改站点（界面里改「站点」那一格）：只留最高那一栏 + 这几个站点。"""
        rank = self.rank
        values = [str(value).strip() for value in stations if str(value).strip()]
        self.places = [(rank, station) for station in values] or [(rank, STATION_NICO)]

    @property
    def link(self) -> str:
        """条目链接的写法：`{{lj|[[中文|日文]]}}` / `[[中文]]` / `{{lj|日文}}`（红链）。"""
        title, ja = str(self.title or "").strip(), str(self.ja or "").strip()
        if not title:
            return f"{{{{lj|{ja}}}}}" if ja else ""
        if not ja or ja == title:
            return f"[[{title}]]"
        return f"{{{{lj|[[{title}|{ja}]]}}}}"

    @property
    def other_kind(self) -> str:
        """其他栏里的子栏（认不出的按「部分未殿堂曲」算）。"""
        return self.kind if self.kind in (OTHER_UNHALL, OTHER_YOUTUBE) else OTHER_UNHALL

    def places_text(self) -> str:
        """分栏的说明文字（界面「备注」列）。"""
        return "；".join(f"{rank}/{station}" for rank, station in self.places)


@dataclass
class VocalistWork:
    """一位歌姬的模板工程。"""

    name: str                                   # 歌姬名（= 模板名 = 条目名）
    engine: str = ""
    split: bool = False                         # 是否拆成年份子页
    songs: List[VocalistSong] = field(default_factory=list)
    # ⚠️ 默认**空**，不是 `DEFAULT_STYLES`：既有模板的样式要能原样顶上去（
    # `effective_styles()` 负责给新建模板补默认色，见那里的注释）。
    styles: Dict[str, str] = field(default_factory=dict)
    relation: str = ""                          # 「相关人物」那一栏的原文（从既有模板继承）
    existing: str = ""                          # 既有模板的正文（空 = 新建）
    existing_doc: str = ""                      # 既有文档页的正文
    summary: str = ""                           # 抓取过程的一句话统计（界面显示）
    flags: List[dict] = field(default_factory=list)   # 需要人工复核的项

    @property
    def page_name(self) -> str:
        return self.name

    @property
    def template_title(self) -> str:
        return f"{TEMPLATE_PREFIX}{self.name}"

    def copy(self) -> "VocalistWork":
        """深一点的拷贝（曲目与分栏都换成新的列表）——「恢复原样」靠它。"""
        songs = [VocalistSong(**{**vars(song), "places": list(song.places)})
                 for song in self.songs]
        return VocalistWork(name=self.name, engine=self.engine, split=self.split,
                            songs=songs, styles=dict(self.styles), relation=self.relation,
                            existing=self.existing, existing_doc=self.existing_doc,
                            summary=self.summary, flags=[dict(flag) for flag in self.flags])

    def years(self) -> List[str]:
        """有歌的年份（从早到晚）—— 拆分子页时只生成这些（用户 2026-09-30 选的）。"""
        return sorted({song.year for song in self.songs if song.year})

    def songs_in(self, year: str) -> List[VocalistSong]:
        return [song for song in self.songs if song.year == year]

    def yearless(self) -> List[VocalistSong]:
        """连投稿年都取不到的曲子（拆分时没法归页，要人工复核）。"""
        return [song for song in self.songs if not song.year]


# ============================================================ 抓素材：殿堂 / 传说 / 神话页面

def hall_page_titles(engine: str) -> List[str]:
    """某个引擎的殿堂曲 / 传说曲 / 神话曲 / 破亿曲页面（含站点页与年份页）。

    用 `list=allpages&apprefix=` 按标题前缀列全：`VOCALOID殿堂曲` 会带出
    `VOCALOID殿堂曲/2008年投稿`、`VOCALOID殿堂曲/bilibili投稿/2024年投稿` 这些子页。
    """
    engine = str(engine or "").strip()
    if not engine:
        return []
    titles: List[str] = []
    for word in HALL_WORDS:
        for title in wiki_api.pages_with_prefix(f"{engine}{word}"):
            if parse_hall_title(title) is None:
                continue                        # 子页里的子页（`/…/doc` 之类）不要
            if title not in titles:
                titles.append(title)
    return titles


def parse_hall_title(title: str) -> Optional[dict]:
    """页面标题 → `{'rank','station','year'}`（认不出返回 None）。"""
    match = HALL_TITLE_RE.match(str(title or "").strip())
    if not match:
        return None
    word = match.group("word")
    raw_station = str(match.group("station") or "").lower()
    station = STATION_ALIASES.get(raw_station, "")
    if raw_station and not station:
        return None                             # 认不出的站点（`/acfun投稿` 之类）先不收
    return {"rank": word, "station": station or STATION_NICO,
            "year": match.group("year") or ""}


def parse_hall_page(title: str, text: str) -> List[HallEntry]:
    """一个殿堂 / 传说 / 神话页面里的所有歌 → `HallEntry` 列表。

    页面写法（实测 niconico 页与 YouTube / bilibili 页两种）：

        {{Temple Song|nnd_id = sm1924663|曲目 = [[把你给碾到哭哦|把你给辗到哭喔♪]](翻)|…}}
        {{Song Honor|歌手 = 初音未来|yt_id = tktcOUi-x-A|条目 = [[细菌污染]]|…}}

    两处坑：

    * 殿堂曲页面里**也**列着已经升到传说 / 神话的歌，靠 `|传说 = 1` / `|神话 = 1`
      标出来（实测 `VOCALOID殿堂曲/2008年投稿` 的 `四叶草♣俱乐部`）—— 不读标记就会把
      传说曲当成殿堂曲（用户 2026-09-30 报的）；
    * 红链写成裸名字的（`|曲目 = {{lj|パラオナボーイ}}*`）也收，目标名就是那串日文。
    """
    info = parse_hall_title(title)
    if info is None:
        return []
    base = RANK_LEVELS.get(info["rank"], 1)
    entries: List[HallEntry] = []
    for match in HALL_ENTRY_CALL_RE.finditer(str(text or "")):
        block = _balanced_call(str(text), match.start())
        value = HALL_ENTRY_RE.search(block)
        if not value:
            continue
        level = max([base, *(lvl for flag, lvl in HALL_FLAG_LEVELS
                            if re.search(r"\|\s*" + flag + r"\s*=\s*1\b", block))])
        raw = value.group(1).strip()
        links = WIKI_LINK_RE.findall(raw)
        if links:
            target = clean_title(links[0][0])
            ja = clean_title(links[0][1])
        else:
            # 红链写成裸名字：`{{lj|パラオナボーイ}}*` → `パラオナボーイ`
            # （`*` 是站上标「翻唱」的记号，不是名字的一部分）
            plain = re.sub(r"[{}]", "", LJ_OPEN_RE.sub("", raw)).strip().rstrip("*").strip()
            target = clean_title(plain)
            ja = ""
        if not target:
            continue
        entries.append(HallEntry(title=target, ja=ja or target,
                                 rank=LEVEL_TITLES.get(level, RANK_HALL),
                                 station=info["station"], year=info["year"], page=title))
    return entries


def _balanced_call(text: str, start: int) -> str:
    """从 `start`（指向 `{{`）取到配对的 `}}` 为止的原文。"""
    depth, index = 0, start
    while index < len(text):
        if text.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if text.startswith("}}", index):
            depth -= 1
            index += 2
            if depth == 0:
                return text[start:index]
            continue
        index += 1
    return text[start:]


def fetch_halls(engine: str, progress=None) -> Tuple[List[HallEntry], List[str]]:
    """抓某个引擎的全部殿堂 / 传说 / 神话页面 → (全部条目, 真正读到的页面标题)。

    ⚠️ **一次一批**（默认 `HALL_BATCH = 10` 个标题一发请求），不要一个页面一个请求：
    VOCALOID 的殿堂页有近百个、单个页面能有 70KB，逐页抓要 100 次请求，实测跑着跑着站点
    就开始掉请求（读不到 → 那些曲子被当成「没上榜」，用户 2026-09-30 实测到的）；
    成批抓 + 失败批次重试一轮，稳得多。

    站点偶尔还是会抽风：失败的批次**最后再试一轮**，两轮都不行才跳过并报错。
    """
    titles = hall_page_titles(engine)
    entries: List[HallEntry] = []
    readable: List[str] = []
    if not titles:
        logging.warning("没找到 %s 的殿堂曲 / 传说曲 / 神话曲页面（引擎名对得上吗？）", engine)
        return entries, readable
    groups = [titles[start:start + HALL_BATCH] for start in range(0, len(titles), HALL_BATCH)]
    failed: List[List[str]] = []
    for round_index in (1, 2):
        pending = groups if round_index == 1 else failed
        failed = []
        for index, group in enumerate(pending, start=1):
            texts = wiki_api.fetch_pages_text(group, batch=len(group))
            missing = [title for title in group if title not in texts]
            if missing:
                failed.append(missing)
                logging.warning("这一批有 %d 个殿堂页没读到：%s", len(missing), "、".join(missing))
            for title in group:
                text = texts.get(title)
                if text is None:
                    continue
                if title not in readable:
                    readable.append(title)
                entries += parse_hall_page(title, text)
            if progress is not None and round_index == 1:
                progress(f"正在读殿堂页（{index}/{len(pending)} 批）：{group[0]} …")
        if not failed:
            break
        logging.warning("有 %d 批殿堂页没读到，等一会儿再试一轮", len(failed))
        time.sleep(2.0)
    for group in failed:
        logging.error("这些殿堂页读不到（两轮都没成），它们里面的曲子这一趟算不到：%s",
                      "、".join(group))
    logging.info("%s 的殿堂页：读了 %d 个页面、%d 条记录", engine, len(readable), len(entries))
    return entries, readable


# ============================================================ 抓素材：歌曲条目

def _songbox_body(text: str) -> str:
    """歌曲信息框的参数部分（与 `producer_template.songbox_body` 同一套括号配对）。"""
    raw = str(text or "")
    match = SONGBOX_RE.search(raw)
    if not match:
        return ""
    depth, index = 2, match.end()
    while index < len(raw) and depth > 0:
        if raw.startswith("{{", index):
            depth += 2
            index += 2
            continue
        if raw.startswith("}}", index):
            depth -= 2
            index += 2
            continue
        index += 1
    if depth != 0:
        return ""
    body = raw[match.end():index - 2]
    return body[1:] if body.startswith("|") else body


def _unwrap(value: str) -> str:
    """去掉最外层的 `{{lj|…}}` / `{{lang|ja|…}}`，留下名字。"""
    text = str(value or "").strip()
    for _ in range(3):
        match = LJ_RE.fullmatch(text)
        if not match:
            break
        text = match.group(1).strip()
    return text


def honor_ranks(text: str) -> Dict[str, int]:
    """条目顶部 `{{虚拟歌手歌曲荣誉题头|引擎|nrank=2|yrank=4}}` → `{站点: 档}`。

    站上的荣誉题头按站点区分：`nrank` = niconico、`yrank` = YouTube、`brank` = bilibili，
    数值 1 殿堂 / 2 传说 / 3 神话 / 4 破亿（实测 `强风大背头` 是 `|nrank=2|yrank=4`）。
    """
    match = HONOR_HEADER_RE.search(str(text or ""))
    if not match:
        return {}
    ranks: Dict[str, int] = {}
    for chunk in str(match.group(1)).split("|"):
        name, _, value = chunk.partition("=")
        key, number = name.strip().lower(), str(value).strip()
        if not number.isdigit() or int(number) <= 0:
            continue
        for param, station in HONOR_RANK_PARAMS:
            if key == param:
                ranks[station] = max(ranks.get(station, 0), int(number))
    return ranks


def _declared_ja(text: str) -> str:
    """条目自己声明的日文名（`{{标题替换|…}}` / 信息框 `|歌曲名称 = {{lj|…}}`）。"""
    names = declared_song_names(text)
    for name in names:
        if re.search(r"[\u3040-\u30ff]", name):     # 带假名的那个才是日文原名
            return name
    return names[0] if names else ""


def _dates_from_songbox(body: str) -> List[str]:
    """信息框里所有投稿日期（`|nnd_date` / `|yt_date` / `|bb_date` / `|投稿时间` / card）。"""
    values: List[str] = []
    for params in SONGBOX_CARD_RE.findall(body):
        for part in params.split("|"):
            match = DATE_RE.search(part)
            if match:
                values.append(match.group(0))
    for name, value in SONGBOX_PARAM_RE.findall(body):
        if "date" in name.strip().lower() or "时间" in name:
            match = DATE_RE.search(value)
            if match:
                values.append(match.group(0))
    return values


def _date_key(value: str) -> Tuple[int, int, int]:
    match = DATE_RE.search(value or "")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match \
        else (9999, 99, 99)


def _singers(text: str) -> Tuple[str, ...]:
    """信息框 `|演唱 = {{lj|[[歌愛ユキ]]}}、[[重音Teto]]` 里的歌姬名（核对用）。"""
    body = _songbox_body(text)
    match = SINGER_PARAM_RE.search(body) if body else None
    if not match:
        return ()
    names: List[str] = []
    for target, display in WIKI_LINK_RE.findall(match.group(1)):
        for candidate in (_unwrap(target), _unwrap(display)):
            value = clean_title(candidate)
            if value and value not in names:
                names.append(value)
    if not names:
        for part in re.split(r"[、,，/]", _unwrap(match.group(1))):
            value = clean_title(part)
            if value and value not in names:
                names.append(value)
    return tuple(names)


def song_fact(title: str, text: Optional[str], ja_hint: str = "") -> SongFact:
    """从条目正文里读出站点 / 荣誉档 / 日期 / 演唱者。"""
    fact = SongFact(title=title, exists=text is not None)
    if text is None:
        return fact
    fact.is_song = bool(SONGBOX_RE.search(text))
    body = _songbox_body(text)
    stations: List[str] = []
    for param, station in STATION_ID_PARAMS:
        if re.search(r"\|\s*" + param + r"\s*=\s*\S", body) and station not in stations:
            stations.append(station)
    for params in SONGBOX_CARD_RE.findall(body):
        code = _card_site(params)
        if code and code not in stations:
            stations.append(code)
    fact.stations = tuple(station for station in STATIONS if station in stations)
    fact.ranks = honor_ranks(text)
    dates = _dates_from_songbox(body)
    if dates:
        earliest = min(dates, key=_date_key)
        match = DATE_RE.search(earliest)
        if match:
            fact.date = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-" \
                        f"{int(match.group(3)):02d}"
    fact.ja = ja_hint or _declared_ja(text)
    fact.singers = _singers(text)
    return fact


def _card_site(params: str) -> str:
    """`{{…Songbox/card|yt|N8r4bHSqtPU|2022-03-26|…}}` 里的站点。"""
    for part in str(params or "").split("|"):
        station = STATION_ALIASES.get(part.strip().lower(), "")
        if station:
            return station
    return ""


def fetch_song_facts(titles: Sequence[str], progress=None) -> Dict[str, SongFact]:
    """批量抓条目正文并解析成 `SongFact`（每 50 个一批，由 `wiki_api` 负责分批）。

    ⚠️ **正文没拿到不等于页面不存在**：先按 `prop=info` 核一遍存在性，还缺的那些再单独
    重抓一轮 —— 否则一趟网络抖动就会把几十首歌误判成「条目不存在（红链）」，
    而红链的歌仍然会被写进模板（用户 2026-09-30 实测到的）。
    """
    pending = [str(title) for title in titles if str(title).strip()]
    texts = wiki_api.fetch_pages_text(pending) if pending else {}
    missing = [title for title in pending if title not in texts]
    exists = wiki_api.pages_exist(missing) if missing else {}
    retry = [title for title in missing if exists.get(title)]
    if retry:
        logging.warning("有 %d 个条目第一次没读到正文，重抓一轮", len(retry))
        texts.update(wiki_api.fetch_pages_text(retry))
    facts: Dict[str, SongFact] = {}
    for index, title in enumerate(pending, start=1):
        fact = song_fact(title, texts.get(title))
        if title not in texts and exists.get(title):
            fact.exists = True                      # 页面在，只是正文没读到
            logging.error("条目 %s 存在但正文没读到（按「读取失败」处理，不当红链）", title)
        elif title in missing and title not in exists:
            logging.warning("条目 %s 的存在性没查出来（网络？），按读取失败处理", title)
            fact.exists = True
        facts[title] = fact
        if progress is not None and index % 25 == 0:
            progress(f"正在读歌曲条目（{index}/{len(pending)}）…")
    return facts


def belongs_to(text: str, vocalist: str) -> bool:
    """这一页的 `|演唱 =` 里有没有这位歌姬（殿堂页与分类对不上时的最后一道核对）。"""
    return bool(vocalist) and str(vocalist) in set(_singers(text))


# ============================================================ 分栏

def classify(work: VocalistWork, hall_entries: Sequence[HallEntry],
             facts: Dict[str, SongFact], titles: Sequence[str]) -> None:
    """把分类里的曲子分到各栏各站点，写进 `work.songs` 与 `work.flags`。"""
    by_title: Dict[str, List[HallEntry]] = {}
    by_ja: Dict[str, List[HallEntry]] = {}
    for entry in hall_entries:
        by_title.setdefault(entry.title, []).append(entry)
        if entry.ja:
            by_ja.setdefault(entry.ja, []).append(entry)

    for title in titles:
        fact = facts.get(title)
        ja = (fact.ja if fact else "") or title
        entries = list(by_title.get(title) or [])
        if not entries:
            # 殿堂页里写的是日文名、分类里是条目名（或者反过来）：按日文名再找一遍
            entries = list(by_ja.get(ja) or [])
            if entries and ja != title:
                logging.info("「%s」在殿堂页里是按日文名 %s 找到的", title, ja)
        song = VocalistSong(title=title, ja=ja, page_exists=bool(fact and fact.exists))
        if fact is not None:
            song.date = fact.date
            song.year = fact.year
        _fill_from_hall(song, entries)
        if not song.places:
            _fill_other(song, fact, entries)
        _flag_song(song, fact)
        if song.flag and len(work.flags) < MAX_FLAGS:
            work.flags.append({"title": song.title, "ja": song.ja, "reason": song.flag,
                               "rank": song.rank, "station": "、".join(song.all_stations),
                               "year": song.year})
        work.songs.append(song)


def _fill_from_hall(song: VocalistSong, entries: Sequence[HallEntry]) -> None:
    """按殿堂页定分栏：**每个站点各拿自己在那边最高的档**，都记进 `places`。"""
    if not entries:
        return
    levels: Dict[str, int] = {}
    years: Dict[int, str] = {}
    pages: Dict[int, str] = {}
    for entry in entries:
        level = entry.level
        levels[entry.station] = max(levels.get(entry.station, 0), level)
        # 同一档可能出现在好几个年份页里（重投 / 达成时间不同）：取**最早**那一年
        if entry.year and (level not in years or entry.year < years[level]):
            years[level] = entry.year
        pages.setdefault(level, entry.page)
    places: List[Tuple[str, str]] = []
    for station in STATIONS:
        if levels.get(station):
            places.append((LEVEL_TITLES.get(levels[station], RANK_HALL), station))
    places.sort(key=lambda item: (-TITLE_LEVELS.get(item[0], 1), STATIONS.index(item[1])))
    song.places = places
    top = max(levels.values())
    if not song.year and years.get(top):
        song.year = years[top]
    song.source = f"殿堂页：{pages.get(top, '')}"
    song.note = "档次 " + "/".join(f"{station}={levels[station]}"
                                   for station in STATIONS if station in levels)


def _fill_other(song: VocalistSong, fact: Optional[SongFact],
                entries: Sequence[HallEntry]) -> None:
    """其他栏：站点看歌曲条目里的投稿 ID；只有 YouTube 的算「部分YouTube投稿」。"""
    stations = list(fact.stations) if fact else []
    if not stations and entries:
        stations = list(dict.fromkeys(entry.station for entry in entries))
    song.places = [(RANK_OTHER, station) for station in STATIONS if station in stations]
    song.kind = OTHER_YOUTUBE if stations == [STATION_YOUTUBE] else OTHER_UNHALL
    levels = _levels_of(fact)
    if levels:                          # 条目写着有档、殿堂页却没查到 → 记下来人工看
        song.source = "荣誉题头"
        song.note = "殿堂页里没找到，按条目里的荣誉题头算：" + "/".join(
            f"{station}={levels[station]}" for station in STATIONS if station in levels)
        return
    song.source = "分类"


def _levels_of(fact: Optional[SongFact]) -> Dict[str, int]:
    """条目里的荣誉题头 → `{站点: 档}`（只留 ≥1 的）。"""
    if fact is None:
        return {}
    return {station: level for station, level in (fact.ranks or {}).items() if level >= 1}


def _flag_song(song: VocalistSong, fact: Optional[SongFact]) -> None:
    """该人工复核的挑出来（用户 2026-09-30 要求：拿不准的弹窗复核）。"""
    reasons: List[str] = []
    if fact is None:
        reasons.append("分类里写着这首歌，但没能读到它的条目")
    else:
        if not fact.exists:
            reasons.append("条目不存在（红链）")
        elif not fact.is_song:
            reasons.append("这个页面看着不是歌曲条目（没有信息框）")
        levels = _levels_of(fact)
        if levels and song.source.startswith("殿堂页"):
            for rank, station in song.places:
                level = TITLE_LEVELS.get(rank, 1)
                if station in levels and levels[station] != level:
                    reasons.append(f"{station} 上条目写的是第 {levels[station]} 档、"
                                   f"殿堂页算的是第 {level} 档")
        if song.rank == RANK_OTHER and not levels and not fact.stations:
            reasons.append("条目里既没有投稿 ID 也没有荣誉题头，站点与栏都定不下来")
    if not song.year:
        reasons.append("取不到投稿年（拆分成年份子页时要你指定）")
    song.flag = "；".join(dict.fromkeys(reasons))


# ============================================================ 抓全流程

def prepare_work(name: str, split: bool = False, progress=None,
                 max_songs: int = MAX_SONGS) -> VocalistWork:
    """抓一位歌姬的全部素材 → `VocalistWork`（分类 + 殿堂页 + 歌曲条目）。"""
    name = str(name or "").strip()
    work = VocalistWork(name=name, engine=get_engine(name), split=bool(split))
    category = f"Category:{name}歌曲"
    if progress is not None:
        progress(f"正在读分类 {category} …")
    titles = wiki_api.category_members(category)
    if not titles:
        # 空表可能是「分类真的没有」也可能是「这一趟请求失败了」（Cloudflare / 代理抖动）
        facts = wiki_api.fetch_page_facts(category)
        if facts.get("ok") and not facts.get("exists"):
            raise ValueError(f"维基上没有分类「{category}」——歌姬名写对了吗？"
                             "（要写条目名，例如 歌爱雪 / 重音Teto）")
        if facts.get("ok"):
            raise ValueError(f"分类「{category}」是空的：这个歌姬还没有歌曲条目")
        raise ValueError(f"读取分类「{category}」失败（网络 / 站点抖动），过一会儿再试")
    if len(titles) > max_songs:
        raise ValueError(f"分类「{category}」里有 {len(titles)} 首曲子，超过上限 {max_songs} —— "
                         "这么大的歌姬模板没法人工核对，先在曲目页手工删一批吧")
    logging.info("分类 %s 有 %d 首曲子", category, len(titles))
    if progress is not None:
        progress(f"分类里有 {len(titles)} 首曲子；正在读 {work.engine} 的殿堂 / 传说 / 神话页面…")
    hall_entries, hall_pages = fetch_halls(work.engine, progress)
    if progress is not None:
        progress("正在读歌曲条目（站点、荣誉题头、日文名）…")
    facts = fetch_song_facts(titles, progress)
    classify(work, hall_entries, facts, titles)
    work.summary = (f"{len(titles)} 首曲子 · 殿堂页 {len(hall_pages)} 个 · "
                    f"{len(hall_entries)} 条殿堂记录 · {len(work.flags)} 条待复核")
    logging.info("「%s」：%s", name, work.summary)
    return work


def load_existing(work: VocalistWork) -> VocalistWork:
    """读既有的模板与文档页：**继承样式与「相关人物」那一栏**（用户 2026-09-30 选的）。

    ⚠️ 继承时**不要**掺进 `DEFAULT_STYLES`（那是给新建模板用的）：既有模板只写了
    `|groupstyle = background:#f38286`（字色用默认黑），把默认的 `#ffffff` 带进来
    就会白字粉底跟站上不一样。
    """
    title = work.template_title
    texts = wiki_api.fetch_pages_text([title, f"{title}/doc"])
    work.existing = texts.get(title) or ""
    work.existing_doc = texts.get(f"{title}/doc") or ""
    if work.existing:
        work.styles = parse_styles(work.existing)
        work.relation = extract_relation(work.existing)
        logging.info("已继承既有模板 %s 的样式（%s）与「相关人物」栏（正文 %d 字）",
                     title, work.styles or "无", len(work.existing))
    return work


def effective_styles(work: VocalistWork) -> Dict[str, str]:
    """真正要写进模板的六色：新建用默认色，改写既有模板就用从它那儿读回来的那套。"""
    base = parse_styles(work.existing) if work.existing else dict(DEFAULT_STYLES)
    return {**base, **(work.styles or {})}


def parse_styles(text: str) -> Dict[str, str]:
    """从既有模板里读回六色（`titlestyle` / `groupstyle` / `liststyle`）。

    既有模板写的是 `background:#d93a49;color:#f2dfe6`，这里按声明拆开，
    颜色值交给 `style_state.parse_color_or_none()` 归一化（与「样式」页同一套）。

    ⚠️ **只看模板头部**（第一个 `|list…=` 之前）：里面那一堆嵌套 subgroup 各自带着
    `|groupstyle = text-align:center;background:…;color:#333333`，拿全文去扫会把
    内层 subgroup 的字色当成外层的（实测把 `|groupstyle = background:#f38286` 读成
    `background:#f38286;color:#333333`）。
    """
    from utils.ui.style_state import parse_color_or_none, parse_decl_text
    mapping = {"titlestyle": ("titleBg", "titleFg"), "groupstyle": ("groupBg", "groupFg"),
               "liststyle": ("listBg", "listFg")}
    head = re.split(r"\|\s*(?:group|list)\s*\d*\s*=", str(text or ""), maxsplit=1)[0]
    styles: Dict[str, str] = {}
    for param, value in STYLE_PARAM_RE.findall(head):
        keys = mapping.get(param)
        if not keys:
            continue
        for prop, decl in parse_decl_text(value.strip()):
            target = keys[1] if prop == "color" else (
                keys[0] if prop in ("background", "background-color") else None)
            if target is None or styles.get(target):
                continue
            for token in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|[a-zA-Z]{3,20}",
                                    str(decl)):
                parsed = parse_color_or_none(token)
                if parsed:
                    styles[target] = str(parsed[0])
                    break
    return styles


def extract_relation(text: str) -> str:
    """把既有模板里的「相关人物」那一栏**原样**取出来（用户选的：既有就继承，没有留空）。"""
    return extract_group_value(text, "相关人物")


def remap_colors(block: str, mapping: Dict[str, str]) -> str:
    """把继承下来的块里旧颜色换成新颜色（用户在「样式」页改了色时用）。

    「相关人物」那一栏是从既有模板里整块搬过来的，里面写死了旧色；不改的话
    那一栏就会跟新配的三组颜色对不上。
    """
    result = str(block or "")
    for old, new in (mapping or {}).items():
        if old and new and old.lower() != str(new).lower():
            result = re.sub(re.escape(old), str(new), result, flags=re.IGNORECASE)
    return result


def extract_group_value(text: str, group_title: str) -> str:
    """从导航框源码里取「相关人物」那一块。

    站上有两种写法，都要认：

    * `|list1 = {{#invoke:Nav|box|subgroup … |title = 相关人物 …}}`（实测 `歌爱雪` /
      `重音Teto` 都是这种：分组名写在 subgroup 的 `|title =` 里）→ 取**包住它的那个
      subgroup 调用**；
    * `|group1 = 相关人物` + `|list1 = …` → 取那个 `list` 的值。

    找不到返回空串（用户选的：既有就继承，没有就留着自己补）。
    """
    raw = str(text or "")
    marker = re.search(r"\|\s*(?:group\d*|title)\s*=\s*" + re.escape(group_title)
                       + r"\s*(?=\||\n|$)", raw)
    if not marker:
        return ""
    marker_text = raw[marker.start():marker.end()]
    if "title" not in marker_text:                  # `|groupN = 相关人物` 那种
        tail = raw[marker.end():]
        list_match = re.search(r"\|\s*list\d*\s*=\s*", tail)
        if list_match:
            value = _take_param_value(tail[list_match.end():])
            if value:
                return value
    return _enclosing_call(raw, marker.start())


def _take_param_value(text: str) -> str:
    """取一个参数的值：到**括号配平的换行**或下一个顶层 `|参数 =` 为止。"""
    depth, index = 0, 0
    while index < len(text):
        if text.startswith("{{", index):
            depth += 2
            index += 2
            continue
        if text.startswith("}}", index):
            if depth == 0:
                break
            depth -= 2
            index += 2
            continue
        if depth == 0:
            if text[index] == "\n":
                break
            if text[index] == "|" and re.match(r"\|\s*[\w\u4e00-\u9fff]+\s*=", text[index:]):
                break
        index += 1
    return text[:index].strip().rstrip("}").strip()


def _enclosing_call(text: str, position: int) -> str:
    """`position` 这个字符所在的**最内层** `{{…}}` 模板调用的原文（不在里面时返回空串）。

    从左往右扫括号：第一个「起点在 position 左边、终点在 position 右边」的调用就是最内层那个
    （实测 `Template:歌爱雪` 的「相关人物」写在
    `|list1 = {{#invoke:Nav|box|subgroup … |title = 相关人物 …}}` 里，
    要取的正是这个 subgroup 调用，不是外面那层大模板）。
    """
    stack: List[int] = []
    index = 0
    while index < len(text) - 1:
        if text.startswith("{{", index):
            stack.append(index)
            index += 2
            continue
        if text.startswith("}}", index) and stack:
            start = stack.pop()
            if start < position < index:
                return text[start:index + 2]
            index += 2
            continue
        index += 1
    return ""


# ============================================================ 生成模板

def _songs_line(songs: Sequence[VocalistSong]) -> str:
    return " • ".join(song.link for song in songs if song.link)


def _sorted_by_date(songs: Sequence[VocalistSong]) -> List[VocalistSong]:
    return sorted(songs, key=lambda song: (song.date or "", song.year or "",
                                           song.ja or song.title))


def _by_year(songs: Sequence[VocalistSong]) -> List[Tuple[str, List[VocalistSong]]]:
    """按年份分组（数字年份在前、从早到晚；取不到年份的垫最后）。"""
    buckets: Dict[str, List[VocalistSong]] = {}
    for song in songs:
        buckets.setdefault(song.year or "其他", []).append(song)
    keys = sorted(key for key in buckets if key.isdigit())
    keys += [key for key in buckets if not key.isdigit()]
    return [(key, buckets[key]) for key in keys]


def _stations_of(station_map: Dict[str, List[VocalistSong]]
                 ) -> List[Tuple[str, List[VocalistSong]]]:
    """按站点分组（顺序 = niconico / YouTube / bilibili，认不出的垫最后）。"""
    result = [(station, station_map[station]) for station in STATIONS if station in station_map]
    result += [(station, items) for station, items in station_map.items()
               if station not in STATIONS]
    return result


def _placements(songs: Sequence[VocalistSong]) -> List[Tuple[str, Dict[str, List[VocalistSong]]]]:
    """把曲子按 `(栏, 站点)` 摊开 → `[(栏, {站点: [曲子]})]`。

    同一首歌可以同时出现在好几栏里（`催眠者` = 神话曲/niconico + 破亿播放曲目/YouTube）。
    """
    buckets: Dict[str, Dict[str, List[VocalistSong]]] = {}
    for song in songs:
        for rank, station in (song.places or [(RANK_OTHER, STATION_NICO)]):
            buckets.setdefault(rank, {}).setdefault(station, []).append(song)
    result = [(rank, buckets.pop(rank)) for rank in RANK_ORDER if rank in buckets]
    result += [(rank, items) for rank, items in buckets.items()]   # 用户手改的栏名也别丢
    return result


def _other_groups(songs: Sequence[VocalistSong]) -> List[Tuple[str, List[VocalistSong]]]:
    """其他栏的两个子栏（部分未殿堂曲 / 部分YouTube投稿）。"""
    result: List[Tuple[str, List[VocalistSong]]] = []
    for kind in (OTHER_UNHALL, OTHER_YOUTUBE):
        matched = _sorted_by_date([song for song in songs if song.other_kind == kind])
        if matched:
            result.append((kind, matched))
    return result


def _other_label(kind: str) -> str:
    if kind == OTHER_UNHALL:
        return f"{{{{mousetext|{OTHER_UNHALL}|{OTHER_UNHALL_NOTE}}}}}"
    return kind


def _subgroup(groups: Sequence[Tuple[str, object]], styles: Dict[str, str],
              indent: str) -> List[str]:
    """拼一个 `{{Navbox subgroup}}`；`groups` 是 `[(分组名, 值)]`。

    值可以是**一行字符串**，也可以是**已经排好版的行列表**（嵌套子分组，见 `_nested`）。
    """
    params = style_params(styles)
    lines: List[str] = [f"{indent}{{{{Navbox subgroup"]
    if params["groupstyle"]:
        lines.append(f"{indent}    |groupstyle = {params['groupstyle']}")
    if styles.get("groupBg"):
        lines.append(f"{indent}    |evenstyle = background:{{{{ColorOps|-90|"
                     f"{styles['groupBg']}}}}};")
    for index, (label, value) in enumerate(groups, start=1):
        lines.append(f"{indent}    |group{index} = {label}")
        if isinstance(value, str):
            lines.append(f"{indent}    |list{index} = {value}")
        else:
            # 嵌套子分组的第一行接在 `= ` 后面（它自己带着缩进），其余的另起行
            lines.append(f"{indent}    |list{index} = {str(value[0]).lstrip()}")
            lines.extend(value[1:])
    lines.append(f"{indent}}}")
    return lines


def _next_indent(indent: str) -> str:
    return indent + "    "


def _station_value(songs: Sequence[VocalistSong], with_years: bool, styles: Dict[str, str],
                   indent: str) -> object:
    """一个站点格：不分年份 → 一行曲目；分年份 → 再套一层年份小格。"""
    if not with_years:
        return _songs_line(_sorted_by_date(songs))
    groups = [(f"{year}年", _songs_line(_sorted_by_date(items)))
              for year, items in _by_year(songs)]
    return _subgroup(groups, styles, indent)


def _song_value(songs: Sequence[VocalistSong], with_years: bool, styles: Dict[str, str],
                indent: str = "") -> List[str]:
    """「歌曲」那一栏的值：栏 → 站点（→ 年份）。"""
    groups: List[Tuple[str, object]] = []
    for rank, station_map in _placements(songs):
        if rank == RANK_OTHER:
            other = [(_other_label(kind), _songs_line(items))
                     for kind, items in _other_groups(songs)]
            if other:
                groups.append((rank, _subgroup(other, styles, _next_indent(indent))))
            continue
        stations = [(station, _station_value(items, with_years, styles,
                                            _next_indent(_next_indent(indent))))
                    for station, items in _stations_of(station_map)]
        groups.append((rank, _subgroup(stations, styles, _next_indent(indent))))
    return _subgroup(groups, styles, indent)


def _title_line(work: VocalistWork, suffix: str = "") -> str:
    fg = str(effective_styles(work).get("titleFg") or "").strip() or "#333333"
    return f"{{{{coloredlink|{fg}|{work.name}{suffix}}}}}"


def _state_param(style: str) -> str:
    return f"|state = {style}"


def _head(work: VocalistWork, name: str, title: str, styles: Dict[str, str],
          state: str) -> List[str]:
    params = style_params(styles)
    lines = ["{{Navbox", f"|name = {name}", f"|title = {title}", state]
    for key in ("titlestyle", "groupstyle", "liststyle"):
        if params[key]:
            lines.append(f"|{key} = {params[key]}")
    return lines


def _includeonly(work: VocalistWork) -> str:
    """让子模板/主模板在条目里自动加歌姬分类（`|nocate=1` 时跳过，站上就这么写的）。

    ⚠️ 花括号太密，这里**不**用 f-string：站上的写法是
    `<includeonly>{{#if: {{{ nocate | }}} | | {{ac|歌爱雪歌曲}} }}</includeonly>`。
    """
    return ("<includeonly>{{#if: {{{ nocate | }}} | | {{ac|"
            + work.name + "歌曲}} }}</includeonly>")


def build_main_template(work: VocalistWork) -> str:
    """主模板：不拆分时是完整导航框；拆分时只留「相关人物 + 各年份的转接行」。"""
    styles = effective_styles(work)
    if work.split:
        lines = _head(work, work.name, _title_line(work), styles,
                      "|state = {{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|"
                      "mw-collapsible mw-collapsed}}")
        index = 1
        if work.relation:
            lines += ["", f"|group{index} = 相关人物", f"|list{index} = {relation_text(work)}"]
            index += 1
        years = work.years()
        if not years:
            logging.warning("「%s」一首带年份的曲子都没有，拆出来的主模板会是空的", work.name)
        for year in years:
            lines.append(f"|list{index} = {{{{{work.name}/{year}|nocate=1|state=uncollapsed|"
                         "child|noabove}}")
            index += 1
        lines += ["", "}}",
                  f"<noinclude>{{{{Documentation}}}}[[Category:{work.name}"
                  f"{YEAR_CATEGORY_SUFFIX}]]</noinclude>"]
        return "\n".join(lines) + "\n"

    lines = _head(work, work.name, _title_line(work), styles,
                  "|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|"
                  "mw-uncollapsed}}")
    index = 1
    if work.relation:
        lines += ["", f"|group{index} = 相关人物",
                  f"|list{index} = {relation_text(work)}"]
        index += 1
    lines += ["", f"|group{index} = 歌曲",
              f"|list{index} = " + "\n".join(_song_value(work.songs, True, styles, ""))]
    lines += ["", "}}", _includeonly(work), f"<noinclude>{VOCALIST_TEMPLATE_CATEGORY}</noinclude>"]
    return "\n".join(lines) + "\n"


def relation_text(work: VocalistWork) -> str:
    """「相关人物」那一栏的正文：继承下来的块要跟着当前配色换色。"""
    block = str(work.relation or "").strip()
    if not block:
        return ""
    old = parse_styles(work.existing) if work.existing else {}
    mapping: Dict[str, str] = {}
    for key, new_value in (work.styles or {}).items():
        old_value = str(old.get(key) or "")
        if old_value and new_value and old_value.lower() != str(new_value).lower():
            mapping[old_value] = str(new_value)
    return remap_colors(block, mapping)


def build_year_page(work: VocalistWork, year: str) -> str:
    """年份子页 `Template:<歌姬>/<年份>`（只列这一年；年份固定了，就不再套年份小格）。"""
    styles = effective_styles(work)
    songs = work.songs_in(year)
    lines = _head(work, f"{work.name}/{year}", f"{_title_line(work)} {year}年歌曲", styles,
                  "|state = {{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|"
                  "mw-collapsible mw-collapsed}}")
    # 被主模板 transclude 时需要这两个参数（照 `Template:重音Teto/2024`）
    lines += ["|navbar={{#ifeq:{{{1}}}|child|plain|}}",
              "| {{#ifeq:{{{1}}}|child|child|}}"]
    fg = str(styles.get("titleFg") or "").strip() or "#333333"
    # `|above = {{#ifeq:{{{2}}}|noabove||{{虚拟歌姬年份计算|年份=2024|歌姬名=X|color=#333333}}}}`
    lines.append("|above = {{#ifeq:{{{2}}}|noabove||{{虚拟歌姬年份计算|"
                 + "年份=" + str(year) + "|歌姬名=" + work.name + "|color=" + fg
                 + "}}}}")
    for index, (rank, station_map) in enumerate(_placements(songs), start=1):
        # 年份子页里年份固定了，只要「栏 → 站点」两层
        lines += ["", f"|group{index} = {rank}",
                  f"|list{index} = " + "\n".join(_rank_value(rank, station_map, styles))]
    lines += ["", "}}", _includeonly(work),
              f"<noinclude>{VOCALIST_TEMPLATE_CATEGORY}</noinclude>"]
    return "\n".join(lines) + "\n"


def _unique(songs: Sequence[VocalistSong]) -> List[VocalistSong]:
    """按对象去重（`VocalistSong` 是 dataclass，不能当字典键）。"""
    seen: set = set()
    result: List[VocalistSong] = []
    for song in songs:
        if id(song) not in seen:
            seen.add(id(song))
            result.append(song)
    return result


def _rank_value(rank: str, station_map: Dict[str, List[VocalistSong]],
                styles: Dict[str, str]) -> List[str]:
    """一栏的值（年份子页里用：栏 → 站点，不再套年份）。"""
    if rank == RANK_OTHER:
        songs = _unique([song for items in station_map.values() for song in items])
        return _subgroup([(_other_label(kind), _songs_line(items))
                          for kind, items in _other_groups(songs)], styles, "")
    return _subgroup([(station, _songs_line(_sorted_by_date(items)))
                      for station, items in _stations_of(station_map)], styles, "")


def build_doc(work: VocalistWork) -> str:
    """`Template:<歌姬>/doc`：说明 + 各年份子页的清单（照 `Template:重音Teto/doc` 写）。"""
    lines: List[str] = [
        f"* 本模板收录[[{work.name}]]演唱的歌曲，曲子多，按投稿年份拆成子页。",
        "* 传说曲 / 殿堂曲等各栏按投稿时间排序，「其他」栏按收录顺序排。",
        "* 添加'''殿堂'''级歌曲时用 <code><nowiki><!-- 注释 --></nowiki></code> 标注投稿时间。",
        "* 翻唱曲目加「<nowiki>*</nowiki>」号。",
    ]
    years = work.years()
    if years:
        lines.append("* 各年份子页（点「编辑」可直接改）：")
        lines.append("<div>")
        for year in years:
            page = f"{TEMPLATE_PREFIX}{work.name}/{year}"
            lines += ["    <div style=\"display: inline-block; margin-bottom: 3px;\">",
                      "        <div style=\"width:18em; display: inline-block;\">"
                      f"{{{{Space|-2}}}}{{{{Space|2}}}}[[{page}]]</div>",
                      "        <div style=\"width:8em; display: inline-block;\">"
                      f"[{{{{指定页面编辑按钮|{page}|编辑}}}}]</div>",
                      "    </div>"]
        lines.append("</div>")
    return "\n".join(lines) + "\n"


def template_links(text: str) -> List[str]:
    """模板里列到的条目名（`{{lj|[[条目|日文]]}}` / `[[条目]]` 都认，去重）。"""
    plain = LJ_OPEN_RE.sub("", str(text or ""))
    titles: List[str] = []
    for target, _display in WIKI_LINK_RE.findall(plain):
        name = clean_title(target)
        if not name or name.startswith(("Template:", "分类:", "Category:")):
            continue
        if name not in titles:
            titles.append(name)
    return titles


def rank_counts(work: VocalistWork) -> Dict[str, int]:
    """每栏各有多少首（界面上的统计行用；同一首歌可能同时算进好几栏）。"""
    counts: Dict[str, int] = {}
    for song in work.songs:
        for rank, _station in (song.places or [(RANK_OTHER, STATION_NICO)]):
            counts[rank] = counts.get(rank, 0) + 1
    return counts


# ============================================================ 立绘（样式页的参考图）

ILLUSTRATION_PREFIX = "歌姬立绘_"


def illustration_url(name: str) -> str:
    """歌姬条目主图（立绘）的地址：`prop=pageimages` 的 `original.source`。

    实测 `歌爱雪` → `https://voca.wiki/images/e/ee/Yuki_v4.jpg`（站点装了 PageImages）。
    取不到（页面没有主图 / 网络失败）返回空串，样式页会提示自己挑一张图。
    """
    title = str(name or "").strip()
    if not title:
        return ""
    for attempt in (1, 2):
        try:
            response = login.get_api_session().get(wiki_api.api_url(), params={
                "action": "query", "titles": title, "prop": "pageimages",
                "piprop": "original|name", "format": "json", "formatversion": "2",
            }, timeout=wiki_api.REQUEST_TIMEOUT)
            response.raise_for_status()
            pages = (response.json().get("query") or {}).get("pages") or []
            page = pages[0] if pages else {}
            source = str((page.get("original") or {}).get("source") or "")
            if source:
                return source
            return ""
        except Exception as error:                  # noqa: BLE001 - 抖动就再试一次
            logging.warning("取「%s」的立绘地址失败（第 %d 次）：%s", title, attempt, error)
            time.sleep(1.0)
    return ""


def download_illustration(name: str, folder=None) -> Optional[Path]:
    """把歌姬立绘下到输出目录（`歌姬立绘_<名字>.jpg`，已下过不重下）。

    站点的图片地址实测会被 Cloudflare 挡一部分（与头像同一个坑），失败返回 None；
    调用方（样式页）会提示用户自己点「选择参考图…」。
    """
    title = str(name or "").strip()
    url = illustration_url(title)
    if not url:
        logging.warning("「%s」没有立绘地址（条目录目没有主图？）", title)
        return None
    suffix = Path(str(url).split("?")[0]).suffix or ".jpg"
    target = (Path(folder) if folder else get_output_path()).joinpath(
        f"{ILLUSTRATION_PREFIX}{safe_filename(title)}{suffix}")
    if target.is_file() and target.stat().st_size > 0:
        logging.info("立绘已经下过：%s", target)
        return target
    try:
        response = login.get_api_session().get(url, timeout=wiki_api.REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.content
    except Exception as error:                      # noqa: BLE001
        logging.warning("下载立绘失败 %s：%s", url, error)
        return None
    if not data:
        logging.warning("立绘下载回来是空的：%s", url)
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as error:
        logging.error("写入立绘失败 %s：%s", target, error)
        return None
    logging.info("立绘已下载：%s", target)
    return target


# ============================================================ 页面清单 / 文件

def page_specs(work: VocalistWork) -> List[dict]:
    """要生成的页面。**顺序就是提交顺序**：年份子页 → 文档 → 主模板。

    先写子页、最后写主模板：主模板 transclude 各年份子页，反过来的话中间那一小段时间
    主模板上全是红链。
    """
    specs: List[dict] = []
    if work.split:
        for year in work.years():
            specs.append({"name": f"{TEMPLATE_PREFIX}{work.name}/{year}",
                          "text": build_year_page(work, year), "kind": "year",
                          "note": f"{year}年（{len(work.songs_in(year))} 首）"})
        if specs:
            specs.append({"name": f"{TEMPLATE_PREFIX}{work.name}/doc",
                          "text": build_doc(work), "kind": "doc", "note": "模板文档"})
    specs.append({"name": work.template_title, "text": build_main_template(work),
                  "kind": "main",
                  "note": "主模板（各年份的转接）" if work.split else "主模板（不分年份）"})
    for spec in specs:
        spec["file"] = output_path(work.name, spec["kind"], spec["name"])
    return specs


def output_path(name: str, kind: str, page_title: str = "") -> Path:
    """页面写到哪个本地文件（都在输出目录里，跟条目 wikitext 放一起）。"""
    safe = safe_filename(str(name).strip())
    if kind == "year":
        year = str(page_title).rsplit("/", 1)[-1]
        return get_output_path().joinpath(f"歌姬模板_{safe}_{year}.wikitext")
    if kind == "doc":
        return get_output_path().joinpath(f"歌姬模板_{safe}_doc.wikitext")
    return get_output_path().joinpath(f"歌姬模板_{safe}.wikitext")


def write_pages(specs: Sequence[dict]) -> None:
    """把页面写到各自的本地文件（提交页与「保存到本地」都用这一份）。"""
    for spec in specs:
        path = Path(spec["file"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(spec.get("text") or "", encoding="utf-8")
        except OSError as error:
            logging.error("写入 %s 失败：%s", path, error)
            raise


# ============================================================ 回写条目

def template_call_for(work: VocalistWork, title: str) -> Tuple[str, str]:
    """一条条目该写哪个模板 → `(模板名, 花括号里的整串)`。

    拆分时写它那一年的子页并带 `|nocate=1`（`{{重音Teto/2024|nocate=1}}`，与本工具
    给已有条目回写时的写法一致）；没拆分就写主模板（`{{歌爱雪}}`）。
    """
    year = next((song.year for song in work.songs if song.title == title), "")
    if work.split and year:
        return work.name, f"{work.name}/{year}|nocate=1"
    return work.name, work.name


def insert_into_pages_for(work: VocalistWork, titles: Sequence[str],
                          progress=None) -> List[dict]:
    """把模板写进一批条目（按「写哪个子页」分组，每组一次批量提交）。"""
    grouped: Dict[str, List[str]] = {}
    for title in titles:
        value = str(title).strip()
        if not value:
            continue
        call = template_call_for(work, value)[1]
        grouped.setdefault(call, []).append(value)
    results: List[dict] = []
    for call, group in grouped.items():
        results += insert_into_pages(work.name, group, progress=progress, call=call)
    return results


__all__ = [
    "VocalistWork", "VocalistSong", "SongFact", "HallEntry",
    "RANK_HALL", "RANK_LEGEND", "RANK_MYTH", "RANK_BILLION", "RANK_OTHER",
    "STATIONS", "STATION_NICO", "STATION_YOUTUBE", "STATION_BILIBILI",
    "OTHER_UNHALL", "OTHER_YOUTUBE", "OTHER_UNHALL_NOTE",
    "hall_page_titles", "parse_hall_title", "parse_hall_page", "fetch_halls",
    "honor_ranks", "song_fact", "fetch_song_facts", "classify", "prepare_work",
    "load_existing", "parse_styles", "extract_relation", "extract_group_value",
    "belongs_to", "build_main_template", "build_year_page", "build_doc",
    "template_links", "rank_counts", "page_specs", "output_path", "write_pages",
    "template_call_for", "insert_into_pages_for", "effective_styles",
    "illustration_url", "download_illustration",
]
