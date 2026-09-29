"""P主模板（导航框）生成 —— 数据侧：从 VocaDB 取曲目 / 专辑，拼成 {{Navbox}}，回写链入条目。

对应界面上侧栏的第二个功能「生成P主模板」（`utils/ui/producer_panel.py` 等）。
参考 MGP-tools 的做法，但依赖我们自己已有的 `utils/wiki_api.py` / `utils/helpers.py`。

## 实测（voca.wiki，2026-09）

`Template:雄之助` 的结构：

    <noinclude>
    此模板用于记录[[雄之助]]的作品。
    …（说明 + [[分类:P主模板]]）
    </noinclude>{{Navbox
    |name=雄之助
    |title={{colorlink|#006CAD|雄之助}}
    |state ={{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|mw-uncollapsed}}
    |titlestyle = background:#94ceda;color:#006CAD
    |groupstyle = background:#575134;color:#FFF

    |group1 = 投稿的<br>原创曲目
    |list1 = {{Navbox subgroup          ← 实测也写作 {{Navbox_subgroup}}
        |groupstyle =width:auto;background:#c8b492;color:

        |group1 = 2015年
        |list1 = {{lj|{{links|El Dorado{{!}}{{lj|エル・ドラド}}<!--
                          -->|モノクロ・トリコロール<!--
                          -->}}}}
        …每个年份一格…

    }}
    |group2 = 专辑
    |list2 = {{lj|{{linksplit|c=#|prefix=雄之助|Unique Antique|Pathos|…}}}}
    }}

要点（都按 `action=parse` 渲染核对过）：

* `{{links|A{{!}}B|C}}` → `[[A|B]]`、`[[C]]`；**页面不存在时是红链**（实测
  `{{links|绝不存在的页面名{{!}}不存在的页面名}}` 渲染出 `class="new"`），
  所以列进模板的曲目**不需要**条目已经建好 —— 真模板里也有 `Alexis`、`Catalyst`
  这类红链，属于「留个坑等人来写」的写法。
* `{{linksplit|c=#|prefix=雄之助|Unique Antique}}` 渲染成
  `<a href="/雄之助#Unique_Antique">Unique Antique</a>` —— 专辑是链到 **P主条目里的小节**，
  不是独立条目，所以「链入条目」那一步不会去改它们。
* `titlestyle` / `groupstyle` / `liststyle` 直接落在 `th.navbox-title` /
  `td.navbox-group` / `td.navbox-list` 上，传什么颜色就出什么颜色。

## 写回链入条目（提交后那个弹窗）

`insert_template()` 把 `{{模板名}}` 插到条目的**注释小节里**（不是标题上方）：

    == 注释与外部链接 ==
    <references/>
    {{新模板}}            ← 插在这
    {{NurseRobot TypeT}}  ← 原本在注释标题上方的大家族模板，一并挪下来

* 注释标题上方紧挨着的一串大家族模板会**挪进小节**（用户 2026-10 明确要求
  「如果『== 注释 ==』上方有大家族模板也一并移动至其下」）；`{{clear}}` / `{{-}}`
  这种排版模板不挪。新模板插在这一串里「最靠近标题的那一行」上面，于是那一行自然
  落到新模板下面 —— 站上惯例是「P主模板在前、大家族模板在后」。
* 没有注释小节时退到：插到末尾分类行的上方（分类按惯例守在最末尾），没有分类就追加末尾。
* 实测回放：再见天才 / 曾想与你对称 / Last dinner 三篇（用户 2026-10 手改过的版本）
  逐字节一致 —— 见 `tests/utils/test_producer_template.py::InsertTemplateTest
  .test_real_edits_replay_exactly`。

## 数据来源（VocaDB）

* `/api/artists?query=名字` → 候选 P主；`/api/artists/<id>` 按 id 取。
* `/api/songs?artistId[]=<id>` —— ⚠️ **必须写成 `artistId[]`**：写成 `artistId`
  时 VocaDB 会**静默忽略**这个参数（实测返回全站 49 万首）。
  取 `songTypes=Original` + `onlyWithPvs=true`，按 `PublishDate` 翻页。
* `/api/albums?artistId[]=<id>&discTypes=Album` → 专辑（`discTypes` 实测有效：
  雄之助 Album 11 张、Single 34、EP 13；站上模板收的正是专辑那一批）。
* **两类东西不收进模板**（用户 2026-10 要求）：
  1. 只有「发行商代传的 YouTube 自动投稿」的曲子（简介写着 `Provided to YouTube by …`，
     频道名是 `… - Topic`）—— 实测 S/950828（ワープループ）就是这种，不是 P主 投稿的；
     真实投稿 + 自动投稿混在一起（N 站本家 + 发行商代传）照旧保留，雄之助 120 首里只掉 2 首。
  2. 合辑 / 多艺人发行的专辑（VocaDB 写作 `artistString = "Various artists"`，实测
     Al/54757 Compilation、Al/55475 Single 都是），以及 P主 只是挂名支持（`isSupport`）的专辑
     （如 Ruliea 的 `hologram`）。
* VocaDB **没有中文名**（`lang` 只认 Default / Japanese / Romaji / English，
  传 `Chinese` 直接 400），所以中文条目名另外找：见 `resolve_from_wiki()`。

## 中文条目名怎么来

P主条目（如 `雄之助`）里有一堆 `{{Producer_Song|…|条目 = 中文|标题 = {{lj|日文}}|…}}`
（实测 雄之助 78 个、春卷饭 6 个、匹诺曹P 8 个），这是现成的「日文原名 → 中文条目」字典，
一次请求就能拿到。另外 `[[中文|日文]]` / `titleN = {{lj|[[中文|日文]]}}` 也能补。
剩下的（词典里没有、也不是现成页面标题的）留给用户在「曲目」页上填，
或按需用 `search_missing=True` 逐首搜维基（`generator=search` 一次拿到标题 + 正文）。

## 外部来源的中文名（用户 2026-10 要求）

「曲目」页的「从外部链接获取中文名」按钮：按日文原名去 **bilibili** 与 **网易云** 搜一遍
（`fill_external_names()`）。实测（2026-09-29）：

* 网易云搜索 `music.163.com/api/search/get/web?type=1&s=<日文名>` —— 不用 cookie，
  实测 200；结果里的 `name` 多数就是日文原名，但部分条目带 `transNames` / `alias`
  （官方译名），那才是我们要的中文名。**专辑名再像也不敢用**：实测搜 `アタマモミ`
  能撞上叫「揉揉头」的翻唱专辑（名字恰好对），但别的歌的专辑名就只是专辑名。
  只有「自己名字里就有这首歌」或「署名里有这个 P主」的条目才算命中。
* bilibili 搜索 `api.bilibili.com/x/web-interface/search/type?search_type=video` ——
  ⚠️ **要 cookie**：不带 `buvid3` 时实测直接 **HTTP 412**（风控页，不是 JSON）；
  先访一次首页 `www.bilibili.com` 拿 cookie 再搜就正常（实测三个关键词全 200）。
  搜索页 HTML 是前端渲染的（`__INITIAL_STATE__` 都没有），抓不到结果，别走那条路。
  标题里的中文名靠「**中文名 / 日文名**」这种相邻写法抽（`chinese_from_title()`）：
  实测 `【中文字幕】忧蓝情结/ブルー・マニアック feat.初音ミク【ナルネア】` → 「忧蓝情结」。
  标题噪声很大（`这首歌不该只在我的循环列表里发光｜《ワープループ》自制PV`），
  所以只认**短**、**有汉字没假名**、**贴着日文名**的片段。
* 候选还会批量拿去 wiki 核一遍（`fetch_pages_text()` + `looks_like_song_page()`）：
  **站上真有这个歌曲条目**的候选优先（那基本就是对的），否则只有结构化的
  （网易云 `transNames`/`alias`）或「贴着日文名」的 b 站候选才敢填。
* 拿不到就留空（宁可空着让 `{{links}}` 写红链，也不要填错名字）—— 还有「AI填充中文名」那条路。
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from utils import wiki_api
from utils.helpers import http_get
from utils.string import auto_lj, is_empty

VOCADB_API = "https://vocadb.net/api"
VOCADB_ARTIST_URL = f"{VOCADB_API}/artists"
VOCADB_SONG_URL = f"{VOCADB_API}/songs"
VOCADB_ALBUM_URL = f"{VOCADB_API}/albums"
REQUEST_TIMEOUT = 60
PAGE_SIZE = 100                # VocaDB 一页最多 100 条
MAX_PAGES = 8                  # 最多翻 8 页（800 首），够任何 P主用
RETRY = 2                      # VocaDB 经代理失败时的重试次数
RETRY_DELAY = 1.0              # 重试前等几秒

# —— 不收进模板的东西（用户 2026-10 要求）——
# 唱片公司 / 发行商代传的 YouTube「Topic」频道会自动生成一个视频（简介是
# “Provided to YouTube by NexTone Inc. … Auto-generated by YouTube.”，实测 S/950828）。
# 这种曲子往往**只有**这一条 PV：不是 P主 自己投稿的，不该当作“投稿的原创曲目”。
TOPIC_AUTHOR_SUFFIX = " - Topic"
AUTO_UPLOAD_MARKERS = ("Provided to YouTube by", "Auto-generated by YouTube")
# VocaDB 对「合辑 / 多艺人发行」的写法（实测 Al/54757 Compilation、Al/55475 Single 都是它）：
# 这种专辑里 P主 只是其中一个供稿人，不是他的专辑。
VARIOUS_ARTISTS = "various artists"

TEMPLATE_PREFIX = "Template:"
PRODUCER_CATEGORY = "[[分类:P主模板]]"
SONG_GROUP_TITLE = "投稿的<br>原创曲目"
ALBUM_GROUP_TITLE = "专辑"
UNKNOWN_YEAR_TITLE = "其他"
SECTION_LINK_PREFIX = "#"      # linksplit 的 c=#

# 六色（界面「样式」页的六个格子）：空串 = 这一项不写，用 Navbox 自己的默认色
STYLE_KEYS: Tuple[str, ...] = ("titleBg", "titleFg", "groupBg", "groupFg", "listBg", "listFg")
STYLE_LABELS: Dict[str, str] = {
    "titleBg": "标题栏底色", "titleFg": "标题栏字色",
    "groupBg": "分组栏底色", "groupFg": "分组栏字色",
    "listBg": "列表底色", "listFg": "列表字色",
}
# 默认取 `Template:雄之助` 的那一套（实测渲染正常）。颜色统一写成小写 hex：
# 界面上的颜色格用 `style_state.parse_color_or_none()` 归一化，出来就是小写，
# 这样界面显示、预览文本、真实写入三者一致（`#FFF` 这种简写会被展开成 `#ffffff`）。
DEFAULT_STYLES: Dict[str, str] = {
    "titleBg": "#94ceda", "titleFg": "#006cad",
    "groupBg": "#575134", "groupFg": "#ffffff",
    "listBg": "", "listFg": "",
}

# 「== 注释 ==」这类小节标题（`== 注释与外部链接 ==`、`==注释==` 都算）
NOTE_HEADING_RE = re.compile(r"^={2,}\s*(?:注\s*释|注\s*解|注\s*釋|参\s*考|參\s*考)[^=\n]*=+\s*$",
                             re.MULTILINE)
# 注释小节里的 `<references/>` 行（新模板插在它后面）
REFERENCES_RE = re.compile(r"^\s*<references\s*/>\s*$", re.IGNORECASE)
# 歌曲条目的信息框：`{{VOCALOID Songbox}}` / `{{VOCALOID_Songbox}}` / `{{Synthesizer V Songbox}}` …
# 用它把「只是提到这首歌」的页面挡在外面
SONGBOX_RE = re.compile(r"\{\{\s*[^{}\n|]*Songbox", re.IGNORECASE)

# —— 外部来源搜中文名（bilibili / 网易云，见模块开头那段实测）——
NETEASE_SEARCH_API = "https://music.163.com/api/search/get/web"
NETEASE_REFERER = "https://music.163.com/"
BILIBILI_HOME = "https://www.bilibili.com/"
BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
SEARCH_TIMEOUT = 25
SEARCH_LIMIT = 5               # 每个来源只看前几条（后面的相关度掉得很快）
BILIBILI_RETRY = 1             # b 站接口风控（412）时重试几次
SOURCE_NETEASE = "网易云"
SOURCE_BILIBILI = "bilibili"
# b 站搜索接口的 cookie（`buvid3` / `b_nut`）：一趟流程里预热一次就够了，见 `bilibili_cookies()`
_bili_cookies: Optional[Dict[str, str]] = None
# 假名 / 汉字：中文名不能带假名（那是日文名），但必须有汉字
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# 标题里的分隔符：中文名与日文名多半写在这两种符号两边
TITLE_SEPARATORS = "/／|｜-–—〜~·・:：,，.。!！?？()（）[]［］【】《》「」『』<>＜＞"
# 标题里的修饰词：整段或方括号里是这个就不当名字
TITLE_NOISE = ("中文字幕", "中日字幕", "中文字幕版", "中日双语", "字幕", "中字", "翻译",
               "翻译版", "熟肉", "补档", "搬运", "转载", "授权", "本家投稿", "本家", "官方",
               "官方投稿", "投稿", "自制", "原创", "翻唱", "歌ってみた", "完整版", "全曲",
               "pv", "mv", "pv付", "mv付", "歌词", "双语歌词", "罗马音", "音译", "高音质",
               "无损", "hires", "合集", "重置", "初投稿", "新曲", "hd", "4k", "1080p")
# 一行只放一个模板调用（`{{P主|collapsed}}`、`{{重音Teto/2026}}`），当「大家族模板」看
PLAIN_TEMPLATE_RE = re.compile(r"^\s*\{\{[^{}\n]*\}\}\s*$")
# 模板名（`{{clear|left}}` → clear）
TEMPLATE_NAME_RE = re.compile(r"^\s*\{\{\s*([^|}\s]+)")
# 「排版用」模板：不算大家族模板，别把它们挪进注释小节
LAYOUT_TEMPLATES = ("clear", "clear2", "clr", "break", "-")
# 分类行（`[[分类:…]]` / `[[Category:…]]`）—— 按惯例在最末尾，别把模板插到它们下面
CATEGORY_LINE_RE = re.compile(r"^\s*\[\[\s*(?:分类|Category)\s*:", re.IGNORECASE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 条目名后缀「(P主名)」（消歧义），比对日文名时要去掉
DISAMBIG_SUFFIX_RE = re.compile(r"[（(][^（）()]*[)）]\s*$")


# ============================================================ 小工具

def _get_json(url: str, retries: int = RETRY, **params) -> dict:
    """GET 一个 JSON 接口（VocaDB 走代理，与 `utils/vocadb.py` 一致）。

    代理会把连接时时掐掉（实测 `RemoteDisconnected`、`ConnectionResetError 10054`），
    而生成模板要连着取好几页 —— 失败就重试两次（每次隔 `RETRY_DELAY` 秒），还不行才报错。
    """
    last: Optional[Exception] = None
    for attempt in range(max(0, retries) + 1):
        try:
            response = http_get(url, use_proxy=True, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return json.loads(response.text)
        except Exception as e:                              # noqa: BLE001 - 网络问题重试
            last = e
            if attempt < retries:
                logging.warning("VocaDB 请求失败（%s），%.1f 秒后重试：%s", url, RETRY_DELAY, e)
                time.sleep(RETRY_DELAY)
    raise last if last is not None else RuntimeError(f"VocaDB 请求失败：{url}")


def clean_title(text: str) -> str:
    """把链接里的一串写法还原成纯条目名：`{{lj|X}}` / `[[A|B]]` / `A{{!}}B` / 加粗。"""
    value = str(text or "").strip()
    value = COMMENT_RE.sub("", value).strip()
    value = re.sub(r"^'''|'''$", "", value).strip()
    if "{{!" in value:
        value = value.split("{{!", 1)[0]
    inner = re.match(r"^\{\{\s*lj\s*\|(.*)\}\}$", value, re.S)
    if inner:
        value = inner.group(1).strip()
    link = re.match(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$", value)
    if link:
        value = (link.group(2) or link.group(1)).strip()
    return value.strip(" |")


def template_calls(text: str, name: str) -> List[str]:
    """取出 text 里所有 `{{name|…}}` 的「参数部分」（按花括号配对扫，能处理嵌套）。

    例：`template_calls("{{lj|{{links|A{{!}}B|C}}}}", "links")` → `["A{{!}}B|C"]`
    """
    pattern = re.compile(r"\{\{\s*" + re.escape(name) + r"\s*(?=[|}])", re.IGNORECASE)
    calls: List[str] = []
    for match in pattern.finditer(text or ""):
        depth = 2
        index = match.end()
        while index < len(text) and depth > 0:
            if text.startswith("{{", index):
                depth += 2
                index += 2
                continue
            if text.startswith("}}", index):
                depth -= 2
                index += 2
                continue
            index += 1
        if depth == 0:
            body = text[match.end():index - 2]
            calls.append(body[1:] if body.startswith("|") else body)
    return calls


def split_args(body: str) -> List[str]:
    """按顶层 `|` 拆模板参数（`{{!}}` / 嵌套模板里的 `|` 不算）。"""
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if body.startswith("{{", index):
            depth += 2
            current.append("{{")
            index += 2
            continue
        if body.startswith("}}", index) and depth >= 2:
            depth -= 2
            current.append("}}")
            index += 2
            continue
        if char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


# ============================================================ 数据模型

@dataclass
class ProducerArtist:
    """VocaDB 里的一个 P主。"""
    id: int = 0
    name: str = ""
    name_en: str = ""
    artist_type: str = ""

    def label(self) -> str:
        extra = f"／{self.name_en}" if self.name_en and self.name_en != self.name else ""
        kind = f"（{self.artist_type}）" if self.artist_type else ""
        return f"{self.name}{extra}{kind}"


@dataclass
class ProducerSong:
    """模板里的一条原创投稿曲目。"""
    ja: str = ""                        # 日文原名（VocaDB defaultName / 站上日文标题）
    cn: str = ""                        # 中文条目名（空 = 还没定，就用日文原名当链接目标）
    date: str = ""                      # 投稿日期，能取到就 "YYYY-MM-DD"，否则 "YYYY" 或空
    song_id: int = 0                    # VocaDB 歌曲 id（界面上点开用）
    source: str = "vocadb"              # vocadb / wiki
    page_exists: bool = False           # 链接目标在站上是不是现成页面

    @property
    def year(self) -> str:
        """投稿年份（取不到就是空串）。"""
        match = re.match(r"(\d{4})", str(self.date or ""))
        return match.group(1) if match else ""

    def target(self) -> str:
        """链接目标（`{{links}}` 里 `{{!}}` 左边那一半）。"""
        return self.cn or self.ja

    def link(self) -> str:
        """`{{links|…}}` 里的一项。

        实测（`action=parse`）：

        * `中文条目{{!}}{{lj|日文原名}}` → 链到中文条目、显示日文（真模板里就是这么写的）；
        * 没有中文名时**直接写名字**（日文也行，`{{links|モノクロ・トリコロール}}`
          渲染出红链，链接目标就是这个名字；整段 `{{links|…}}` 外面还包着 `{{lj}}`，
          所以显示没问题）；
        * ⚠️ 别写成孤零零一个 `{{lj|日文}}` —— 实测那样这一项会被 `{{links}}` 整个丢掉。
        """
        ja = str(self.ja or "").strip()
        cn = str(self.cn or "").strip()
        if not ja:
            return cn
        if cn and cn != ja:
            return f"{cn}{{{{!}}}}{auto_lj(ja)}"
        return ja

    def same_as(self, other: "ProducerSong") -> bool:
        """两条是不是同一首歌（日文名或中文名相同就算）。"""
        for left, right in ((self.ja, other.ja), (self.cn, other.cn), (self.ja, other.cn),
                            (self.cn, other.ja)):
            if left and right and strip_disambig(left) == strip_disambig(right):
                return True
        return False


def strip_disambig(name: str) -> str:
    """去掉消歧义后缀：`涅槃(Yunosuke)` → `涅槃`。"""
    return DISAMBIG_SUFFIX_RE.sub("", str(name or "").strip()).strip()


@dataclass
class ProducerWork:
    """一个 P主的全部素材（曲目 + 专辑）。"""
    artist: ProducerArtist = field(default_factory=ProducerArtist)
    songs: List[ProducerSong] = field(default_factory=list)
    albums: List[str] = field(default_factory=list)
    page_name: str = ""                       # P主条目名（模板标题链接到它）
    template_name: str = ""                   # 模板名（不带 Template: 前缀）
    styles: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STYLES))

    def sorted_songs(self) -> List[ProducerSong]:
        """按投稿日期排（没有日期的排最后，同日期保持原顺序）。"""
        return sorted(self.songs, key=lambda s: (s.date or "9999", s.ja))

    def copy(self) -> "ProducerWork":
        return ProducerWork(artist=ProducerArtist(**vars(self.artist)),
                            songs=[ProducerSong(**vars(s)) for s in self.songs],
                            albums=list(self.albums), page_name=self.page_name,
                            template_name=self.template_name, styles=dict(self.styles))


# ============================================================ VocaDB

def parse_artist_id(query: str) -> int:
    """从链接 / 纯数字里取 VocaDB artist id：`/Artist/Details/23981` 或 `23981`。"""
    value = str(query or "").strip()
    if value.isdigit():
        return int(value)
    match = re.search(r"/Artist/(?:Details/)?(\d+)", value, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _to_artist(item: dict) -> ProducerArtist:
    return ProducerArtist(id=int(item.get("id") or 0),
                          name=str(item.get("name") or "").strip(),
                          name_en=str(item.get("additionalNames") or "").strip(),
                          artist_type=str(item.get("artistType") or "").strip())


def search_artists(query: str, limit: int = 10) -> List[ProducerArtist]:
    """按名字 / id 查 P主（返回候选，界面上去重后让用户挑）。"""
    artist_id = parse_artist_id(query)
    if artist_id:
        artist = fetch_artist(artist_id)
        return [artist] if artist else []
    if is_empty(query):
        return []
    data = _get_json(VOCADB_ARTIST_URL, query=query.strip(), maxResults=limit, lang="Default")
    return [_to_artist(item) for item in data.get("items") or [] if item.get("id")]


def fetch_artist(artist_id: int) -> Optional[ProducerArtist]:
    """按 id 取一个 P主。"""
    if not artist_id:
        return None
    data = _get_json(f"{VOCADB_ARTIST_URL}/{artist_id}", lang="Default")
    return _to_artist(data) if data.get("id") else None


def parse_publish_date(value) -> str:
    """VocaDB 的 `2026-08-28T00:00:00Z` → `2026-08-28`（取不到就空串）。"""
    return normalise_date(str(value or "").split("T")[0])


def song_from_vocadb(item: dict) -> ProducerSong:
    """VocaDB 的一首歌 → `ProducerSong`（中文名不在 VocaDB，留空）。"""
    return ProducerSong(ja=str(item.get("defaultName") or item.get("name") or "").strip(),
                        date=parse_publish_date(item.get("publishDate")),
                        song_id=int(item.get("id") or 0), source="vocadb")


def is_auto_upload(pv: dict) -> bool:
    """这条 PV 是不是 YouTube 自动生成的「Topic」上传（发行商/厂牌代传）。

    实测（S/950828 ワープループ）：`author` 是 `Yunosuke - Topic`，
    `description` 里是 `Provided to YouTube by NexTone Inc. … Auto-generated by YouTube.`。
    两种特征任一命中就算（不同年份的自动投稿写法略有差别）。
    """
    author = str((pv or {}).get("author") or "")
    description = str((pv or {}).get("description") or "")
    return (author.rstrip().endswith(TOPIC_AUTHOR_SUFFIX)
            or any(marker in description for marker in AUTO_UPLOAD_MARKERS))


def has_real_pv(item: dict) -> bool:
    """这首歌有没有「真人投稿」的 PV（全是自动投稿就返回 False）。

    只有自动投稿 = 发行商把歌放进流媒体时顺手生成的视频，**不是**投稿作品，
    所以不写进「投稿的原创曲目」（用户 2026-10 要求：S/950828 那种不要加）。
    真实投稿 + 自动投稿混在一起（同一首歌既有 niconico 本家、又被发行商代传）照旧保留。
    没有 PV 信息时按「有」处理（宁可多留一条，也不要莫名奇妙删掉真曲子）。
    """
    pvs = item.get("pvs")
    if not pvs:
        return True
    return not all(is_auto_upload(pv) for pv in pvs)


def is_own_album(item: dict, artist_id: int) -> bool:
    """这张专辑算不算这位 P主 自己的（合辑 / 只是挂名支持都不算）。

    实测：
    * Al/54757 `NIGHT HIKE Compilation Vol.1`：`artistString = "Various artists"`（合辑）；
    * Al/55475 `音速を超えて`：也是 `"Various artists"`，雄之助 在里面只是 Arranger；
    * `hologram`（Ruliea 的专辑）：他 `isSupport = true` —— 只是客串编曲。
    这三类都不写进「专辑」那一格（用户 2026-10 要求）。
    数据缺失时（拿不到 artistString / artists）一律保留：宁可多留，不要误删。
    """
    if str(item.get("artistString") or "").strip().lower() == VARIOUS_ARTISTS:
        return False
    credits = [a for a in (item.get("artists") or [])
               if _credit_artist_id(a) == artist_id]
    if credits and all(bool(a.get("isSupport")) for a in credits):
        return False
    return True


def _credit_artist_id(credit: dict) -> int:
    """专辑艺人条目里的艺人 id（VocaDB 把它嵌在 `artist` 里）。"""
    nested = credit.get("artist")
    if isinstance(nested, dict):
        return int(nested.get("id") or 0)
    return int(credit.get("id") or 0)


def fetch_songs(artist_id: int, max_pages: int = MAX_PAGES) -> List[ProducerSong]:
    """取该 P主的原创投稿曲目（有 PV 的），按投稿日期从早到晚。

    只要 PV 全是「发行商代传的 YouTube 自动投稿」就不收（见 `has_real_pv`）。
    """
    songs: List[ProducerSong] = []
    seen = set()
    skipped: List[str] = []
    for page in range(max_pages):
        data = _get_json(VOCADB_SONG_URL, **{
            "artistId[]": artist_id, "start": page * PAGE_SIZE, "maxResults": PAGE_SIZE,
            "getTotalCount": "true", "sort": "PublishDate", "lang": "Default",
            "fields": "Names,PVs", "songTypes": "Original", "onlyWithPvs": "true",
            "artistParticipationStatus": "Everything"})
        items = data.get("items") or []
        for item in items:
            song = song_from_vocadb(item)
            if not song.ja or song.ja in seen:
                continue
            seen.add(song.ja)
            if not has_real_pv(item):
                skipped.append(song.ja)
                continue
            songs.append(song)
        if len(items) < PAGE_SIZE:
            break
    if skipped:
        logging.info("跳过了 %d 首只有 YouTube 自动投稿的曲目：%s",
                     len(skipped), "、".join(skipped))
    return sort_songs(songs)


def fetch_albums(artist_id: int) -> List[str]:
    """取该 P主的专辑名（`discTypes=Album`，按发行日期排、去重）。

    合辑 / 多艺人发行（`artistString = "Various artists"`）、以及 P主 只是挂名支持
    （`isSupport`）的专辑都不收（见 `is_own_album`）。
    """
    data = _get_json(VOCADB_ALBUM_URL, **{
        "artistId[]": artist_id, "maxResults": PAGE_SIZE, "getTotalCount": "true",
        "lang": "Default", "fields": "Artists,Names", "discTypes": "Album"})
    rows: List[Tuple[str, str]] = []
    skipped: List[str] = []
    for item in data.get("items") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if not is_own_album(item, artist_id):
            skipped.append(name)
            continue
        rows.append((str((item.get("releaseDate") or {}).get("year") or ""), name))
    if skipped:
        logging.info("跳过了 %d 张合辑 / 只是挂名的专辑：%s", len(skipped), "、".join(skipped))
    rows.sort()
    albums: List[str] = []
    for _year, name in rows:
        if name not in albums:
            albums.append(name)
    return albums


def sort_songs(songs: Sequence[ProducerSong]) -> List[ProducerSong]:
    """按日期排序（没日期的排最后）。"""
    return sorted(songs, key=lambda s: (s.date or "9999-99-99", s.ja))


def fetch_works(query: str, artist: Optional[ProducerArtist] = None) -> ProducerWork:
    """一次取全：P主 + 曲目 + 专辑（`query` 是名字或 id / 链接）。"""
    artist = artist or (search_artists(query) or [None])[0]
    if artist is None:
        return ProducerWork()
    work = ProducerWork(artist=artist)
    work.songs = fetch_songs(artist.id)
    work.albums = fetch_albums(artist.id)
    return work


# ============================================================ 中文条目名

def parse_producer_page(text: str) -> Dict[str, str]:
    """从 P主条目里挖「日文原名 → 中文条目」的映射。

    认三种写法（都是站上实测的）：

    * `{{Producer_Song|…|条目 = 中文|标题 = {{lj|日文}}|…}}`
    * `{{lj|[[中文|日文]]}}` / `[[中文|日文]]`
    * `| titleN = {{lj|[[中文|日文]]}}`（tracklist 那一段，专辑曲目）
    """
    mapping: Dict[str, str] = {}
    for body in template_calls(text or "", "Producer_Song"):
        args = split_args(body)
        values: Dict[str, str] = {}
        key = ""
        for arg in args:
            if "=" in arg:
                key, _, value = arg.partition("=")
                key = key.strip()
                values[key] = value.strip()
            elif key:
                values[key] = f"{values.get(key, '')}|{arg}".strip("|")
        cn = clean_title(values.get("条目", ""))
        ja = clean_title(values.get("标题", "")) or clean_title(values.get("日文名", ""))
        if cn and ja and cn != ja:
            mapping.setdefault(ja, cn)
    for left, right in re.findall(r"\[\[([^\[\]|]+)\|([^\[\]]+)\]\]", text or ""):
        cn, ja = clean_title(left), clean_title(right)
        if not (cn and ja) or cn == ja or cn.startswith("#"):     # `[[#Pathos|Pathos]]` 是专辑小节
            continue
        if cn.startswith(("File:", "Category:", "分类:")) or ":" in cn.split("/")[0]:
            continue
        mapping.setdefault(ja, cn)
    return mapping


def apply_wiki_names(songs: Iterable[ProducerSong], mapping: Dict[str, str]) -> int:
    """把「日文原名 → 中文条目」填进曲目；返回填了几条。"""
    filled = 0
    for song in songs:
        if song.cn:
            continue
        for key in (song.ja, strip_disambig(song.ja)):
            name = mapping.get(key)
            if name:
                song.cn = name
                filled += 1
                break
    return filled


def mark_existing(songs: Iterable[ProducerSong], existing: Dict[str, str]) -> None:
    """按「批量取到的页面正文」标出哪些链接目标是现成页面。

    现成页面里原本就写着日文原名的，顺便把中文条目名补上（页面标题就是条目名）。
    """
    for song in songs:
        target = song.target()
        if target in existing:
            song.page_exists = True
            if not song.cn and strip_disambig(target) != strip_disambig(song.ja):
                song.cn = target


def resolve_from_wiki(work: ProducerWork, page_name: str = "") -> ProducerWork:
    """用 P主条目 + 批量存在性检查补全中文条目名（不联网搜索，快）。

    `page_name` 给空时按 P主名字猜（`artist.name`）。
    """
    page = (page_name or work.artist.name or "").strip()
    work.page_name = work.page_name or page
    texts = wiki_api.fetch_pages_text([page]) if page else {}
    page_text = texts.get(page, "")
    if page_text:
        apply_wiki_names(work.songs, parse_producer_page(page_text))
    _merge_page_songs(work, page_text)
    targets = list(dict.fromkeys(song.target() for song in work.songs if song.target()))
    if targets:
        mark_existing(work.songs, wiki_api.fetch_pages_text(targets))
    return work


def _merge_page_songs(work: ProducerWork, page_text: str) -> None:
    """P主条目里列了、VocaDB 没给的曲目也收进来（日期取 `|投稿日期 =`）。"""
    if not page_text:
        return
    known = list(work.songs)
    for body in template_calls(page_text, "Producer_Song"):
        values: Dict[str, str] = {}
        key = ""
        for arg in split_args(body):
            if "=" in arg:
                key, _, value = arg.partition("=")
                values[key.strip()] = value.strip()
            elif key:
                values[key] = f"{values.get(key, '')}|{arg}".strip("|")
        ja = clean_title(values.get("标题", ""))
        cn = clean_title(values.get("条目", ""))
        if not (ja or cn):
            continue
        song = ProducerSong(ja=ja, cn=cn, date=_date_from_text(values.get("投稿日期", "")),
                            source="wiki")
        if any(item.same_as(song) for item in known):
            continue
        known.append(song)
    work.songs = sort_songs(known)


def _date_from_text(value: str) -> str:
    """`2025年8月21日` → `2025-08-21`（认不出就空串）。"""
    return normalise_date(value)


DATE_FULL_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")
DATE_MONTH_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*月?")
DATE_YEAR_RE = re.compile(r"(\d{4})")


def normalise_date(value) -> str:
    """把用户 / VocaDB / 维基上的日期写法统一成 `YYYY-MM-DD`（只有年月就 `YYYY-MM`）。

    认：`2024-8-28`、`2024/08/28`、`2024.8.28`、`2024年8月28日`、`2026-08-28T00:00:00Z`、
    `2024-08`、`2024`。认不出（或没有 4 位年份）返回空串 —— 空日期的曲目在模板里排最后、
    归到「其他」那一格。
    """
    text = str(value or "").strip()
    full = DATE_FULL_RE.search(text)
    if full:
        return (f"{int(full.group(1)):04d}-{int(full.group(2)):02d}-"
                f"{int(full.group(3)):02d}")
    month = DATE_MONTH_RE.search(text)
    if month and 1 <= int(month.group(2)) <= 12:
        return f"{int(month.group(1)):04d}-{int(month.group(2)):02d}"
    year = DATE_YEAR_RE.search(text)
    return year.group(1) if year else ""


def looks_like_song_page(text: str) -> bool:
    """正文里有歌曲信息框（`{{…Songbox}}`）→ 当它是**歌曲条目**。

    实测：歌曲条目都有（时滞记录 / 涅槃(HotaRu) / 再见天才 / Last dinner …）；
    而「只是提到了这首歌」的页面都没有 —— 榜单页 `NICONICO VOCALOID SONGS TOP20/第87期`
    （用 `{{Billboard}}`）、专辑页（`{{Album Infobox}}`）、P主页面（`{{Producer_Song}}`）、
    消歧义页（`{{disambig}}`）。用户 2026-10 报的就是榜单页被当成条目名塞进了曲目表。
    """
    return bool(SONGBOX_RE.search(text or ""))


def search_page_by_song(song: ProducerSong) -> str:
    """按日文原名搜维基，返回对得上的**歌曲条目**名（搜不到返回空串）。

    `generator=search` 一次请求就带回候选页正文，用它核对两件事：

    1. 这一页确实写了这首日文名（免得把同名的无关页面当成歌曲条目）；
    2. 这一页**本身是歌曲条目**（有 `{{…Songbox}}`）—— 榜单页 / 专辑页 / P主页面
       只是「列了这首歌」，拿它们的标题当条目名就全错了（用户 2026-10 报的
       `NICONICO VOCALOID SONGS TOP20/第87期`）。
    """
    term = song.ja or song.cn
    if not term:
        return ""
    try:
        payload = wiki_api.search_pages_with_text(term, limit=5)
    except Exception as e:                              # noqa: BLE001 - 搜不到就当没有
        logging.warning("搜索维基条目失败（%s）：%s", term, e)
        return ""
    for title, text in payload:
        if not looks_like_song_page(text):
            continue
        if strip_disambig(term) in text or term in text:
            return title
    return ""


def fill_missing_names(songs: Sequence[ProducerSong],
                       progress: Optional[Callable[[str], None]] = None) -> int:
    """逐首搜维基补中文条目名；返回补了几条（给界面的「从维基补全条目名」按钮用）。"""
    filled = 0
    for index, song in enumerate(songs, start=1):
        if song.cn or not song.ja:
            continue
        if progress is not None:
            progress(f"（{index}/{len(songs)}）搜索「{song.ja}」…")
        title = search_page_by_song(song)
        if title:
            song.cn = title
            song.page_exists = True
            filled += 1
    return filled


# ============================================================ 外部来源的中文名

def _signature(text: str) -> str:
    """比对用的签名：只留字母 / 数字 / 假名 / 汉字，去掉空白与标点、大小写归一。

    标题里常写 `ネハン / 雄之助 feat. 重音テトSV`，条目里写 `ネハン (feat. 重音テト)`，
    直接 `in` 比会漏，所以先去噪再比。中点 `・`（U+30FB）落在片假名区里，
    但它是标点（`ブルー・マニアック` / `ブルー マニアック` 是同一首），得单独排掉。
    """
    return re.sub(r"[^0-9a-z\u3040-\u30fa\u30fc-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", "",
                  str(text or "").lower())


def is_chinese_name(text: str, limit: int = 14) -> bool:
    """看着像中文歌名：**有汉字、没假名**、长度合适、不像一句话。

    实测这样能把 b 站标题里那些描述性的中文排掉
    （`这首歌不该只在我的循环列表里发光` 是句子、`忧蓝情结` 是歌名）；
    长度放宽到 14 个字（歌名一般不到 10 个），再长就不像名字了。
    注意：`高潮部分真的好棒` 这种短句子长度上是过得去的，最终靠
    `pick_candidate()` 那句「b 站来的必须在维基上核实到歌曲条目」挡下。
    """
    value = str(text or "").strip()
    if not value or len(value) > limit:
        return False
    if KANA_RE.search(value) or not HAN_RE.search(value):
        return False
    return not value.endswith(("。", "，", "！", "？", "…", "、"))


# 整串就是一对括号包着的名字（`《涅槃》` / `「涅槃」`），与「前缀修饰词」分开处理：
# 只用前缀规则的话，`《涅槃》` 会被整串吃掉（实测就是这样变成空串的）。
OUTER_BRACKETS_RE = re.compile(r"^([【\[（(「『《])(.{1,20})([】\]）)」』》])$")


def clean_name(text: str) -> str:
    """清掉歌名外围的写法：`【中文字幕】`、`《》`、`feat. 誰`、多余空白。"""
    value = str(text or "").strip()
    for _ in range(4):
        before = value
        wrapped = OUTER_BRACKETS_RE.match(value)
        if wrapped:
            value = wrapped.group(2).strip()
        value = re.sub(r"^[【\[（(「『《][^】\]）)」』》]{0,12}[】\]）)」』》]\s*", "", value)
        value = re.sub(r"\s*[【\[（(「『《][^】\]）)」』》]{0,12}[】\]）)」』》]$", "", value)
        value = value.strip(TITLE_SEPARATORS + " \t")
        if value == before:
            break
    value = re.sub(r"\s*(?:feat|ft)\.?\s*.*$", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", value).strip()


def _is_noise(segment: str) -> bool:
    """片段是不是修饰词（`中文字幕` / `自制PV` / `中日歌词` 这种）。

    短片段（≤ 6 个字）里**含有**修饰词就算 —— 实测标题里的写法五花八门
    （`中日歌词` / `歌词精讲` / `授权搬运` / `自制PV`），逐个列是列不完的。
    """
    plain = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(segment or "").lower())
    if not plain:
        return False
    if plain in TITLE_NOISE:
        return True
    return len(plain) <= 6 and any(noise in plain for noise in TITLE_NOISE)


def split_title(title: str) -> List[str]:
    """按分隔符把标题切成片段（顺序保留，空片段丢掉）。"""
    parts: List[str] = []
    current: List[str] = []
    for char in str(title or ""):
        if char in TITLE_SEPARATORS:
            if current:
                parts.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def chinese_from_title(title: str, ja: str, artist: str = "") -> Tuple[str, bool]:
    """从 b 站标题里抽中文名 → (名字, 是不是「贴着日文名」那种)。

    实测标题写法：`ネハン / 雄之助 feat. 重音テトSV`（本家，没中文）、
    `【中文字幕】忧蓝情结/ブルー・マニアック feat.初音ミク【ナルネア】`（中文名就在日文名旁边）。
    所以：先找出含日文名的那个片段，优先取**紧邻**它的中文片段；
    没有相邻的就退到随便一个中文片段（这种可信度低一档，`pick_candidate()` 会另作要求）。
    P主 自己的名字（`雄之助` 这种汉字写法）就贴在日文名旁边，得先排掉。
    """
    target = _signature(ja)
    if not target:
        return "", False
    skip = {_signature(artist)} if artist else set()
    parts = split_title(title)
    index = next((row for row, part in enumerate(parts)
                  if target in _signature(part) or _signature(part) in target), -1)
    candidates: List[Tuple[str, bool]] = []
    for row, part in enumerate(parts):
        if row == index or _is_noise(part):
            continue
        name = clean_name(part)
        # P主 的名字（`雄之助` 这种汉字写法）也贴在日文名旁边，先排掉
        if not is_chinese_name(name) or _signature(name) in target or _signature(name) in skip:
            continue
        candidates.append((name, abs(row - index) == 1 if index >= 0 else False))
    if not candidates:
        return "", False
    adjacent = [item for item in candidates if item[1]]
    return (adjacent or candidates)[0]


def chinese_from_netease(item: dict, ja: str, artist: str = "") -> List[str]:
    """网易云条目里的中文名候选（`transNames` / `alias` 优先，其次它自己的名字）。

    只认**确实对得上这首歌**的条目：名字里有日文原名，或者署名里有这个 P主
    （网易云上有的条目直接写中文名，那就只能靠 P主 认）。
    """
    target = _signature(ja)
    if not target:
        return []
    artists = "".join(str(one.get("name") or "") for one in (item.get("artists") or []))
    if target not in _signature(item.get("name")) and not (artist and artist in artists):
        return []
    names: List[str] = []
    for value in [*(item.get("transNames") or []), *(item.get("alias") or []),
                  item.get("name")]:
        name = clean_name(value)
        if name and is_chinese_name(name) and name not in names:
            names.append(name)
    return names


def netease_search(keyword: str, limit: int = SEARCH_LIMIT) -> List[dict]:
    """网易云搜歌 → 结果里的 `songs` 列表（失败返回空列表，只记日志）。"""
    if not str(keyword or "").strip():
        return []
    try:
        response = http_get(NETEASE_SEARCH_API, use_proxy=False,
                            params={"csrf_token": "", "type": 1, "offset": 0,
                                    "total": "true", "limit": limit, "s": keyword},
                            headers={"Referer": NETEASE_REFERER},
                            timeout=SEARCH_TIMEOUT)
        payload = response.json()
    except Exception as e:                              # noqa: BLE001 - 搜不到就当没有
        logging.warning("网易云搜索「%s」失败：%s", keyword, e)
        return []
    return list(((payload.get("result") or {}).get("songs") or []))[:limit]


def bilibili_cookies(refresh: bool = False) -> Dict[str, str]:
    """b 站搜索要的 cookie（`buvid3` / `b_nut`）：一趟流程里只预热一次。

    实测：每个关键词都重新访首页会很浪费（一首歌多一次请求，7 首就多 7 次），
    而 cookie 本身能一直用；被风控（412）时才 `refresh=True` 重拿一次。
    """
    global _bili_cookies
    if _bili_cookies is None or refresh:
        try:
            warm = http_get(BILIBILI_HOME, use_proxy=False, timeout=SEARCH_TIMEOUT)
            _bili_cookies = dict(warm.cookies.items())
        except Exception as e:                          # noqa: BLE001 - 拿不到也照样试一次搜索
            logging.warning("b 站预热 cookie 失败：%s", e)
            return dict(_bili_cookies or {})
    return dict(_bili_cookies or {})


def bilibili_search(keyword: str, limit: int = SEARCH_LIMIT) -> List[str]:
    """b 站搜视频 → 标题列表（相关度排序）。

    ⚠️ **接口要 cookie**：不带 `buvid3` 时实测直接 **HTTP 412**（风控页，不是 JSON）。
    先访一次首页拿 cookie 再搜就正常，所以这里先预热、失败再重拿 cookie 重试一次。
    """
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    for attempt in range(BILIBILI_RETRY + 1):
        try:
            response = http_get(BILIBILI_SEARCH_API, use_proxy=False,
                                params={"search_type": "video", "page": 1,
                                        "keyword": keyword},
                                headers={"Referer": BILIBILI_HOME},
                                cookies=bilibili_cookies(refresh=attempt > 0),
                                timeout=SEARCH_TIMEOUT)
            payload = response.json()
        except Exception as e:                          # noqa: BLE001 - 搜不到就当没有
            logging.warning("b 站搜索「%s」失败：%s", keyword, e)
            continue
        if payload.get("code") == 0:
            results = ((payload.get("data") or {}).get("result") or [])
            return [str(item.get("title") or "") for item in results[:limit]]
        logging.warning("b 站搜索「%s」返回 code=%s（不带 cookie 时是 -412）",
                        keyword, payload.get("code"))
    return []


def external_candidates(song: ProducerSong, artist: str = "") -> List[Tuple[str, str, int]]:
    """一首歌从外部来源拿到的候选名 → `[(名字, 来源, 可信度)]`（可信度高的在前）。

    可信度：3 = 网易云的结构化译名（`transNames` / `alias` / 它自己写的就是中文名）；
    2 = b 站标题里**紧贴着**日文名的中文片段；1 = b 站标题里别处的中文片段。
    """
    candidates: List[Tuple[str, str, int]] = []
    for item in netease_search(song.ja):
        for name in chinese_from_netease(item, song.ja, artist):
            candidates.append((name, SOURCE_NETEASE, 3))
    for title in bilibili_search(song.ja):
        name, adjacent = chinese_from_title(title, song.ja, artist)
        if name:
            candidates.append((name, SOURCE_BILIBILI, 2 if adjacent else 1))
    ranked: Dict[str, Tuple[str, str, int]] = {}
    for name, source, score in candidates:
        if name not in ranked or ranked[name][2] < score:
            ranked[name] = (name, source, score)
    return sorted(ranked.values(), key=lambda item: (-item[2], len(item[0]), item[0]))


def pick_candidate(candidates: Sequence[Tuple[str, str, int]],
                   texts: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """挑一个最可信的候选 → `(名字, 来源)`；都不够格就返回 None。

    排序：**站上真有这个歌曲条目**的最优先（`texts` 里且带信息框），其次看来源可信度，
    同档取短的。门槛：

    * 网易云的结构化译名（可信度 3）可以直接用 —— 名字要么是官方译名，
      要么是个「署名里有这个 P主」的条目名（实测：ネハン → 涅槃、アタマモミ → 揉揉头）；
    * b 站标题里抽出来的（可信度 1 / 2）**必须在维基上核实到歌曲条目**才敢用 ——
      实测 さよなら天才 的标题里能抽出「高潮部分真的好棒」这种句子（就在日文名旁边，
      靠相邻关系分辨不出来），而真名字「再见天才」是现成条目，核实一下就不会错。
    宁可留空：名字填错比空着更糟（模板里的链会全歪）。
    """
    best: Optional[Tuple[Tuple[int, int, int], Tuple[str, str]]] = None
    for name, source, score in candidates:
        body = texts.get(name)
        verified = 1 if (body is not None and looks_like_song_page(body)) else 0
        if not verified and score < 3:
            continue
        rank = (verified, score, -len(name))
        if best is None or rank > best[0]:
            best = (rank, (name, source))
    return best[1] if best else None


def fill_external_names(songs: Sequence[ProducerSong], artist: str = "",
                        progress: Optional[Callable[[str], None]] = None) -> Dict[str, object]:
    """按日文原名去 bilibili / 网易云 搜中文名，填进还没有中文名的曲目。

    返回 `{'ok', 'filled', 'checked', 'by_source', 'names'}`（界面拿来写状态行）。
    """
    pending = [song for song in songs if not song.cn and song.ja]
    if not pending:
        return {"ok": True, "filled": 0, "checked": 0, "by_source": {}, "names": {}}
    found: Dict[str, List[Tuple[str, str, int]]] = {}
    for index, song in enumerate(pending, start=1):
        if progress is not None:
            progress(f"（{index}/{len(pending)}）搜「{song.ja}」…")
        found[song.ja] = external_candidates(song, artist)
    # 候选名批量拿去 wiki 核一遍：站上真有这个歌曲条目的话，基本就是对的
    names = list(dict.fromkeys(name for items in found.values() for name, _s, _c in items))
    texts = wiki_api.fetch_pages_text(names) if names else {}
    filled: Dict[str, str] = {}
    by_source: Dict[str, int] = {}
    for song in pending:
        picked = pick_candidate(found.get(song.ja) or [], texts)
        if not picked:
            continue
        name, source = picked
        song.cn = name
        song.page_exists = name in texts
        filled[song.ja] = name
        by_source[source] = by_source.get(source, 0) + 1
    return {"ok": True, "filled": len(filled), "checked": len(pending),
            "by_source": by_source, "names": filled}


# ============================================================ 生成模板

def style_decl(styles: Dict[str, str], background: str, color: str) -> str:
    """拼一份 `background:X;color:Y`（没设的那半不写）。"""
    parts = []
    if str(styles.get(background) or "").strip():
        parts.append(f"background:{styles[background]}")
    if str(styles.get(color) or "").strip():
        parts.append(f"color:{styles[color]}")
    return ";".join(parts)


def style_params(styles: Dict[str, str]) -> Dict[str, str]:
    """六色 → Navbox 的三个 style 参数（空串表示这一项不写进模板）。"""
    return {
        "titlestyle": style_decl(styles, "titleBg", "titleFg"),
        "groupstyle": style_decl(styles, "groupBg", "groupFg"),
        "liststyle": style_decl(styles, "listBg", "listFg"),
    }


def build_song_groups(songs: Sequence[ProducerSong]) -> List[Tuple[str, List[ProducerSong]]]:
    """曲目按投稿年份分组（年份从早到晚，没年份的放最后「其他」）。"""
    buckets: Dict[str, List[ProducerSong]] = {}
    order: List[str] = []
    for song in sort_songs(songs):
        if not (song.ja or song.cn):
            continue
        year = song.year
        if year not in buckets:
            buckets[year] = []
            order.append(year)
        buckets[year].append(song)
    return [(year or UNKNOWN_YEAR_TITLE, buckets[year]) for year in order]


def build_template(work: ProducerWork) -> str:
    """把 P主 + 曲目 + 专辑拼成整篇模板 wikitext（含 `<noinclude>` 说明与分类）。"""
    styles = {**DEFAULT_STYLES, **(work.styles or {})}
    params = style_params(styles)
    name = (work.template_name or work.artist.name or work.page_name or "").strip()
    page = (work.page_name or work.artist.name or name).strip()
    title_color = str(styles.get("titleFg") or "").strip() or "#006CAD"

    lines: List[str] = [
        "<noinclude>",
        f"此模板用于记录[[{page}]]的作品。",
        "",
        "若有遗漏或未来再有补充，欢迎随时编辑。",
        "",
        "如果想要调用折叠状态的本模板，请使用"
        "<span style=color:blue><nowiki>{{</nowiki>{{PAGENAME}}<nowiki>|collapsed}}</nowiki></span>。"
        + PRODUCER_CATEGORY,
        "</noinclude>{{Navbox",
        f"|name={name}",
        f"|title={{{{colorlink|{title_color}|{work.artist.name or page}}}}}",
        "|state ={{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|mw-uncollapsed}}",
        f"|titlestyle = {params['titlestyle']}",
        f"|groupstyle = {params['groupstyle']}",
    ]
    if params["liststyle"]:
        lines.append(f"|liststyle = {params['liststyle']}")

    groups = build_song_groups(work.songs)
    if groups:
        lines += ["", f"|group1 = {SONG_GROUP_TITLE}", "|list1 = {{Navbox_subgroup",
                  f"    |groupstyle =width:auto;{params['groupstyle']}"]
        for index, (label, songs) in enumerate(groups, start=1):
            links = "|".join(song.link() for song in songs if song.link())
            lines += ["",
                      f"    |group{index} = {label}年" if label.isdigit() else
                      f"    |group{index} = {label}",
                      f"    |list{index} = " + "{{lj|{{links|" + links + "}}}}"]
        lines += ["}}"]

    albums = [name for name in (work.albums or []) if str(name).strip()]
    if albums:
        group_index = 2 if groups else 1
        joined = "|".join(str(name).strip() for name in albums)
        lines += ["",
                  f"|group{group_index} = {ALBUM_GROUP_TITLE}",
                  f"|list{group_index} = "
                  + f"{{{{lj|{{{{linksplit|c={SECTION_LINK_PREFIX}|prefix={page}|"
                  + joined + "}}}}"]

    lines += ["", "}}", ""]
    return "\n".join(lines)


def template_links(text: str) -> List[str]:
    """模板里 `{{links|…}}` 列出的曲目条目名（去重，保持出现顺序）。"""
    titles: List[str] = []
    for body in template_calls(text or "", "links"):
        for item in split_args(body):
            name = clean_title(item)
            if name and name not in titles:
                titles.append(name)
    return titles


# ============================================================ 写回链入条目

def _first_category_line(lines: Sequence[str]) -> Optional[int]:
    """末尾那一段分类行的**第一行**下标（末尾不是分类行就返回 None）。"""
    index = len(lines)
    while index > 0 and not lines[index - 1].strip():
        index -= 1
    end = index
    while index > 0 and CATEGORY_LINE_RE.match(lines[index - 1]):
        index -= 1
    return index if index < end else None


def _blank_before_categories(lines: Sequence[str]) -> List[str]:
    """分类行前面空一行（用户 2026-10 手改后的版本就是这样；对渲染没影响，纯排版）。"""
    start = _first_category_line(lines)
    if start is None or start == 0 or not lines[start - 1].strip():
        return list(lines)
    return [*lines[:start], "", *lines[start:]]


def contains_template(text: str, template_name: str) -> bool:
    """正文里是不是已经有 `{{模板名…}}`（`Template:雄之助` 与 `雄之助` 都认）。"""
    bare = str(template_name or "").strip().split(":")[-1]
    if not bare:
        return False
    return bool(re.search(r"\{\{\s*" + re.escape(bare) + r"\s*(?=[|}])", text or ""))


def _is_layout_template(line: str) -> bool:
    """这一行是不是排版用的模板（`{{clear}}` / `{{-}}`），挪进注释小节反而难看。"""
    match = TEMPLATE_NAME_RE.match(line or "")
    return bool(match) and match.group(1).lower() in LAYOUT_TEMPLATES


def _plain_template_block(lines: Sequence[str], end: int) -> Tuple[int, int]:
    """`lines[:end]` 末尾那一串「一行一个模板」（中间可以有空行）的 `[起, 止)` 下标。

    从后往前走到第一个排版模板（`{{clear}}` / `{{-}}`）为止 —— 那个留着不动。
    """
    index = end
    while index > 0 and not lines[index - 1].strip():
        index -= 1
    stop = index
    while (index > 0 and PLAIN_TEMPLATE_RE.match(lines[index - 1])
           and not _is_layout_template(lines[index - 1])):
        index -= 1
    return index, stop


def insert_template(text: str, template_name: str) -> Tuple[str, str]:
    """把 `{{模板名}}` 插进条目正文；返回 (新正文, 说明)。

    位置（用户 2026-10 用真实编辑拍板，已按 `NEH#` 那几次编辑逐行核对）：

    * 条目里**已经有**这个模板 → 原样返回；
    * 有「== 注释 ==」类小节 → 插到**小节里面**、`<references/>` 的下一行，
      也就是排在注释区那堆大家族模板的最前面；
      ⚠️ **不是**插在注释标题上方（2026-10 之前就是这么写错的：`{{Ruliea}}` 被放在了
      `== 注释与外部链接 ==` 上面，用户手工改了三篇 —— 再见天才 / 曾想与你对称 / Last dinner）；
    * 注释标题上方紧挨着的那一串大家族模板（`{{NurseRobot TypeT}}`、
      `{{The VOCALOID Collection2025冬}}` …）**一并挪进小节**，排在新模板后面
      —— 用户原话「如果『== 注释 ==』上方有大家族模板也一并移动至其下」；
    * 新模板插在这一串里**最靠近标题的那一行上面**：这样那一行（原来离标题最近的那个模板）
      自然落到新模板下面，与站上「P主模板在前、大家族模板在后」的写法一致
      （实测 再见天才 挪下来后是 `{{NurseRobot TypeT}} / {{Ruliea}} / {{The VOCALOID Collection2025冬}}`）；
    * 没有注释小节 → 插到分类行上方（分类按惯例守在最末尾），连分类都没有就追加到末尾。
    """
    name = str(template_name or "").strip()
    if not name or not text:
        return text, ""
    if contains_template(text, name):
        return text, f"已包含 {{{{ {name} }}}}，未改动"

    heading = NOTE_HEADING_RE.search(text)
    if heading is None:
        # 没有注释小节：能插在分类行上方就插（分类按惯例守在最末尾）
        lines = text.rstrip("\n").split("\n")
        first_category = _first_category_line(lines)
        if first_category is not None:
            lines.insert(first_category, f"{{{{{name}}}}}")
            return ("\n".join(_blank_before_categories(lines)) + "\n",
                    f"没有注释小节，插到分类行上方：{{{{{name}}}}}")
        body = text.rstrip("\n")
        return f"{body}\n\n{{{{{name}}}}}\n", f"没有注释小节，追加到末尾：{{{{{name}}}}}"

    before, after = text[:heading.start()], text[heading.start():]
    lines = before.split("\n")
    start, stop = _plain_template_block(lines, len(lines))
    block = [line.strip() for line in lines[start:stop]]
    moved = 0
    if block:
        # 注释标题上方那一串大家族模板：留在原来的相对顺序里，新模板插在最后一个的上面
        moved = len(block)
        block.insert(len(block) - 1, f"{{{{{name}}}}}")
        remain = "\n".join(lines[:start]).rstrip("\n")
        before = f"{remain}\n\n" if remain.strip() else ""
    else:
        block = [f"{{{{{name}}}}}"]

    # 小节里的落点：<references/> 后面；没有 <references/> 就紧跟标题
    tail = after.split("\n")
    index = 1
    for offset, line in enumerate(tail[1:], start=1):
        if REFERENCES_RE.match(line):
            index = offset + 1
            break
    new_after = "\n".join([*tail[:index], *block, *tail[index:]])
    anchor = "小节的 <references/> 后面" if index > 1 else "注释小节里"
    note = f"插到{anchor}"
    if moved:
        note += f"，并把注释上方的 {moved} 个大家族模板一并挪了进来"
    return "\n".join(_blank_before_categories((before + new_after).split("\n"))), note


def insert_into_pages(template_name: str, titles: Sequence[str],
                      progress: Optional[Callable[[dict], None]] = None,
                      summary: str = "") -> List[dict]:
    """把模板插进一批条目；逐页回调 `progress(item)`，返回每页结果。

    每页结果：`{'title', 'ok', 'count', 'kind', 'note'}`（失败带 `'error'`），
    与提交页「修正链入」那套逐页提示共用 `backlink_page_text()`。
    """
    results: List[dict] = []
    summary = summary or f"添加{{{{{template_name}}}}}导航模板"
    pending = [str(title).strip() for title in titles if str(title).strip()]
    texts = wiki_api.fetch_pages_text(pending) if pending else {}
    for title in pending:
        result: Dict[str, object] = {"title": title, "count": 1, "kind": "插入模板"}
        text = texts.get(title)
        if text is None:
            result.update(ok=False, count=0, kind="条目不存在", error="页面上没有这一页")
        else:
            new_text, note = insert_template(text, template_name)
            result["note"] = note
            if new_text == text:
                result.update(ok=True, count=0, kind=note)
            else:
                edited = wiki_api.edit_page(title, new_text, summary)
                if edited.get("ok"):
                    result["ok"] = True
                else:
                    result.update(ok=False, count=0, error=str(edited.get("error") or "写入失败"))
        results.append(result)
        if progress is not None:
            progress(result)
    return results
