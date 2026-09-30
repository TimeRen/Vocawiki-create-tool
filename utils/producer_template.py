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

    |group1 = 投稿的</br>原创曲目
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
* 标题栏那个带颜色的 P主 名有两种写法（`title_template()`）：名字里有**假名**（日文）时用
  `{{Cj|颜色|名字}}` —— 实测 `Template:Cj` = `<span style="color:…">{{lang|ja|…}}</span>`
  （参数顺序跟 `colorlink` 一样），站上 `Template:Yomitan Akane` 写的就是
  `|title={{Cj|#ffffff|読谷あかね}}`（用户 2026-09 要求）；纯拉丁 / 纯汉字的（`Ruliea`、`雄之助`）
  仍写 `{{colorlink|颜色|名字}}`。
* `<noinclude>` 里那句「此模板用于记录…的作品」在**条目名跟 P主 名不一样**时带上显示名：
  `[[Yomitan Akane|読谷あかね]]`（用户手改过这行），一样时就是 `[[雄之助]]`。

## 写回链入条目（提交后那个弹窗）

`insert_template()` 把 `{{模板名}}` 插到条目的**注释小节里**（不是标题上方）：

    == 注释与外部链接 ==
    <references/>
    {{新模板}}                   ← P主模板：总插在那一串模板的**最前面**
    {{重音Teto/2024|nocate=1}}   ← 原本在注释标题上方的大家族模板，一并挪下来（排在它后面）
    {{重音Teto/2026|nocate=1}}
    {{The VOCALOID Collection2025冬}}

* 注释标题上方紧挨着的一串大家族模板会**挪进小节**（用户 2026-10 明确要求
  「如果『== 注释 ==』上方有大家族模板也一并移动至其下」）；`{{clear}}` / `{{-}}`
  这种排版模板不挪。
* 插在那一串里的**第几个**由 `position` 决定：
  * `POSITION_TOP`（默认，P主模板）：**一律最前面**（不拆散整串）——
    不管是本来就在小节里的（咕呶呶 / 厚颜无耻的报酬系统 / 向灭绝问好 …）还是从注释标题
    上方挪进来的。`2代目閻魔` 那篇用户试了三次才定：中间（251489）→ 最后（251574）
    → **最前**（revid 251587，`<references/>` / `{{Yomitan Akane}}` / 两个 `重音Teto`）。
  * `POSITION_AFTER_PRODUCER`（歌姬模板）：**P主/歌手模板之后、活动模板之前**
    （用户 2026-09-30：「歌姬模板的位置在P主模板和活动模板之间」）；
    活动模板由 `is_activity_template()` 认（`The VOCALOID Collection2025冬` / `ボカコレ2024冬` /
    名字末尾是「年份+季节」的），一串里没有活动模板时就落在末尾。
* 没有注释小节时同样按 `position` 处理：末尾那一串大家族模板里（`top` = 整串上方，
  `after_producer` = 歌手模板后、活动模板前），没那一串就插分类行上方
  （分类按惯例守在最末尾），没有分类就追加末尾。
* 实测回放：再见天才 / 曾想与你对称 / Last dinner / 虽然是人类。 四篇（用户 2026-10
  手改过的版本）逐字节一致（再见天才 那篇的模板顺序按新规则；见测试里的说明）
  —— `tests/utils/test_producer_template.py::InsertTemplateTest
  .test_real_edits_replay_exactly`。

## 数据来源（VocaDB）

* `/api/artists?query=名字` → 候选 P主；`/api/artists/<id>` 按 id 取。
* `/api/songs?artistId[]=<id>` —— ⚠️ **必须写成 `artistId[]`**：写成 `artistId`
  时 VocaDB 会**静默忽略**这个参数（实测返回全站 49 万首）。
  取 `songTypes=Original` + `onlyWithPvs=true`，按 `PublishDate` 翻页。
* ⚠️ **排序看「稿件的投稿日期」，不看 `publishDate`**（用户 2026-09 要求）：
  VocaDB 的 `publishDate` 对收录进专辑的歌写的是**专辑发行日**（实测 Ruliea 的
  《エキセントリックブルー》写 2024-01-15、而 Nico / YouTube 稿件是 2024-12-20；
  読谷あかね 那批 2026-04-25 的也全是专辑发行日），所以取**最早的官方投稿 PV**
  （`pv_date()`：跳过 YouTube 自动生成的 `… - Topic` 代传），一个都没有才退回 `publishDate`。
* `/api/albums?artistId[]=<id>&discTypes=Album` → 专辑（`discTypes` 实测有效：
  雄之助 Album 11 张、Single 34、EP 13；站上模板收的正是专辑那一批）。
* **两类东西不收进模板**（用户 2026-10 要求）：
  1. 只有「发行商代传的 YouTube 自动投稿」的曲子（简介写着 `Provided to YouTube by …`，
     频道名是 `… - Topic`）—— 实测 S/950828（ワープループ）就是这种，不是 P主 投稿的；
     真实投稿 + 自动投稿混在一起（N 站本家 + 发行商代传）照旧保留，雄之助 120 首里只掉 2 首。
  2. 合辑 / 多艺人发行的专辑（VocaDB 写作 `artistString = "Various artists"`，实测
     Al/54757 Compilation、Al/55475 Single 都是），以及 P主 只是挂名支持（`isSupport`）的专辑
     （如 `Ruins Record`、`ベルの音が鳴る`；⚠️ Ruliea 的 `hologram` 虽然有 `Various`，
     但有他自己的非支持署名，**保留**）。
  3. 曲目里**不是他当制作人**的（`is_own_song()`，用户 2026-09 要求）——
     他只做母带 / 演奏 / PV / 曲绘（`Mastering` / `Instrumentalist` / `Animator` /
     `Illustrator` / `Other` …）的「参与曲目」一律不收，只留署名里有 `Default`（主艺人）
     或 `Composer`（作曲）的。实测 Ruliea《絶滅によろしく》《セプテントリオー》、
     読谷あかね《ポリへドロン》《頭ン痛》《ファサード・クエスチョン》都被滤掉。
* VocaDB **没有中文名**（`lang` 只认 Default / Japanese / Romaji / English，
  传 `Chinese` 直接 400），所以中文条目名另外找：见 `resolve_from_wiki()`。

## 中文条目名怎么来

P主条目（如 `雄之助`）里有一堆 `{{Producer_Song|…|条目 = 中文|标题 = {{lj|日文}}|…}}`
（实测 雄之助 78 个、春卷饭 6 个、匹诺曹P 8 个），这是现成的「日文原名 → 中文条目」字典，
一次请求就能拿到。另外 `[[中文|日文]]` / `titleN = {{lj|[[中文|日文]]}}` 也能补。
剩下的（词典里没有、也不是现成页面标题的）留给用户在「曲目」页上填，
或按需用 `search_missing=True` 逐首搜维基（`generator=search` 一次拿到标题 + 正文）。

⚠️ **三条「跟着用户手改的模板学到的」规矩**（2026-09，用户拿 `Template:Yomitan Akane`
与 `Template:Ruliea` 的修订历史报的）：

1. **名字指向重定向时要换成真条目名**（`canonicalise_names()`）：P主 页面里写的是
   `|条目 = Chilly`，而站上 `Chilly` 与 `散り散り` 都重定向到真条目「四散」—— 用户手改模板时
   写的就是「四散」。所以填完名字先问一句 `wiki_api.redirect_targets()`：是重定向就换真名；
   日文原名本身就是重定向的（`散り散り` → `四散`）那种，真条目名就是它的中文名。
2. **「搜到了」不等于「就是它」**：搜出来的条目必须**自称**是这首歌
   （`{{标题替换|{{lj|散り散り}}}}` / 信息框的 `|歌曲名称 =`，见 `page_is_song_entry()`），
   而且日文名要是**完整的名字**（`has_name()`）—— 否则搜「マニュア」会拿到「わたしマニュアル」
   的条目「自我手册」、搜「エオ」会拿到 P主 叫 `EO(エオ)` 的「青果实」、
   搜「ぽい」会拿到「神っぽいな」的条目「像神一样呐」（用户 2026-09 报的）。
   宁可留空（模板里写红链，站上本来就这么写），也不要填错 —— 名字错了链接全歪。
3. **外部来源核实到「是歌曲条目」还不够，得是**这首歌**（`pick_candidate(..., ja)`）：
   实测《リボン》被填成 `迷途孩子的缎带` —— 那名字在站上确实是个真条目，只是不是这首歌；
   核实的时候要拿日文原名比对条目自称的歌名（同一条规矩的第 2 点）。
4. **同名但是别人的歌 → 加消歧义后缀**（`page_by_other_producer()` + `disambiguated()`，
   用户 2026-09-29 拿 `Template:Shikisai` 与条目 `偏执狂` 的差异报的）：《パラノイア》
   有两首 —— shikisai 的（2022）与 全て奴等の所為です。 的（2020，条目就叫 `偏执狂`），
   两首自认的歌名都是 `パラノイア`，光看歌名分不出来。这时比对信息框里的 `|P主 =`：
   里面**没有我们**就不链过去，改写成站上消歧义写法的 `偏执狂(shikisai)`（半角括号，
   后缀是 P主 名；实测站上也有 `偏执狂(全奴等)` 这种页面）。写了消歧义名之后
   `page_exists` 记 False（站上还没这个页面 → 红链），回写链入条目时也不会再去改
   那首别人的歌（用户之前已经销过一笔错：工具往 `偏执狂` 里写了 `{{shikisai}}`）。

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
* 候选还会批量拿去 wiki 核一遍（`fetch_pages_text()` + `page_is_song_entry()`）：
  **站上真有这个歌曲条目、而且它就是这首歌**的候选优先（那基本就是对的），否则只有结构化的
  （网易云 `transNames`/`alias`）或「贴着日文名」的 b 站候选才敢填。
  ⚠️ 「是歌曲条目」还不够：实测《リボン》曾被填成 `迷途孩子的缎带`（另一个真条目），
  所以核实时要拿日文原名去比对条目自称的歌名（`pick_candidate(..., ja)`）。
* 拿不到就留空（宁可空着让 `{{links}}` 写红链，也不要填错名字）—— 还有「AI填充中文名」那条路。
* ⚠️ 外部来源的匹配同样要**完整名字**（`has_name()`）：搜「ぽい」时网易云会返回
  `神っぽいな (feat. 重音テト) [Cover]`（译名「像神明一样呢」）和原曲条目
  （`transNames = 像神一样呐`）—— 以前用 `日文名 in 签名` 比，`ぽい` 就命中 `神っぽいな`，
  于是把「像神一样呐」当成《ぽい》的中文名（用户 2026-09 报的）。
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from config.config import get_output_path
from utils import image, wiki_api
from utils.helpers import http_get
from utils.string import auto_lj, is_empty, safe_filename

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
# 这种曲子往往**只有**这一条 PV：不是 P主 自己投稿的，不该当作他的作品。
TOPIC_AUTHOR_SUFFIX = " - Topic"
AUTO_UPLOAD_MARKERS = ("Provided to YouTube by", "Auto-generated by YouTube")
# VocaDB 对「合辑 / 多艺人发行」的写法（实测 Al/54757 Compilation、Al/55475 Single 都是它）：
# 这种专辑里 P主 只是其中一个供稿人，不是他的专辑。
VARIOUS_ARTISTS = "various artists"
# 只有这些署名算「**他是制作人**」（用户 2026-09 收窄后的规则：参与曲目全删）：
# `Default` = VocaDB 的「主艺人」角色（也就是 `artistString` 里那个），`Composer` = 作曲。
# 其余的（`Mastering` 母带 / `Arranger` / `Lyricist` / `Instrumentalist` 演奏 /
# `Animator` 动画 / `Illustrator` 曲绘 / `Other` 挂名）都当「参与」，不进模板。
PRODUCER_ROLES = {"default", "composer"}

TEMPLATE_PREFIX = "Template:"
PRODUCER_CATEGORY = "[[分类:P主模板]]"
# 曲目那一格的分组名。只收「他是制作人」的曲目（见 `is_own_song`）。
# ⚠️ 历史：照 `Template:雄之助` 写成「投稿的<br>原创曲目」→ 列表里混进「参与曲目」后
# 用户手改成「原创/参与曲目」→ 2026-09 用户要求把参与曲目全删掉，分组名又改回来，
# 写法用用户给的「投稿的</br>原创曲目」（`</br>`，跟他手改模板时的写法一致）。
SONG_GROUP_TITLE = "投稿的</br>原创曲目"
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
# 活动模板（注释区里「The VOCALOID Collection2025冬」这一串）：歌姬模板要插在它们**前面**
# （用户 2026-09-30：「歌姬模板的位置在P主模板和活动模板之间」）。
# 写法来自 `family_template.collection_template_name()`：`The VOCALOID Collection<名>`；
# 另外把「名字末尾是 年份+季节」的也一并当活动模板（活动模板都是这个形状）。
#
# ⚠️ 这里的模板名可以含空格（`TEMPLATE_NAME_RE` 到空格就停了，认不出活动模板名），
# 所以单独用一条允许空格的表达式在整行上认。
ACTIVITY_TEMPLATE_NAME_RE = re.compile(r"^\s*\{\{\s*([^{}|]+)")
ACTIVITY_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:vocaloid\s*collection|ボカコレ|vocacolle)", re.IGNORECASE)
ACTIVITY_SEASON_RE = re.compile(r"\d{4}\s*[春夏秋冬]$")
# 插入位置：
# * `top` —— 一律插在这一串的**最前面**（P主模板用，用户 2026-10 用真实编辑拍板的）；
# * `after_producer` —— 插在「P主/歌手模板」之后、「活动模板」之前（歌姬模板用）。
POSITION_TOP = "top"
POSITION_AFTER_PRODUCER = "after_producer"
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
    picture: str = ""                      # 头像地址（空串 = 没有；当「样式」页的参考图）

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

# 头像存到输出目录时的文件名前缀（「样式」页的默认参考图）
AVATAR_PREFIX = "P主头像_"
# VocaDB 头像的几个尺寸：原图优先，取不到退到缩略图
AVATAR_KEYS = ("urlOriginal", "urlThumb", "urlSmallThumb", "urlTinyThumb")
# ⚠️ 头像要 `fields=MainPicture` 才返回（不传这个参数时响应里只有 `pictureMime`，实测）
ARTIST_FIELDS = "MainPicture"


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
                          artist_type=str(item.get("artistType") or "").strip(),
                          picture=avatar_url_from(item.get("mainPicture")))


def search_artists(query: str, limit: int = 10) -> List[ProducerArtist]:
    """按名字 / id 查 P主（返回候选，界面上去重后让用户挑）。"""
    artist_id = parse_artist_id(query)
    if artist_id:
        artist = fetch_artist(artist_id)
        return [artist] if artist else []
    if is_empty(query):
        return []
    data = _get_json(VOCADB_ARTIST_URL, query=query.strip(), maxResults=limit,
                     lang="Default", fields=ARTIST_FIELDS)
    return [_to_artist(item) for item in data.get("items") or [] if item.get("id")]


def fetch_artist(artist_id: int) -> Optional[ProducerArtist]:
    """按 id 取一个 P主。"""
    if not artist_id:
        return None
    data = _get_json(f"{VOCADB_ARTIST_URL}/{artist_id}", lang="Default",
                     fields=ARTIST_FIELDS)
    return _to_artist(data) if data.get("id") else None


def avatar_url_from(picture) -> str:
    """VocaDB 的 `mainPicture` → 头像地址（实测要 `?fields=MainPicture` 才返回这个字段）。

    实测 `Ar/23981`：`{"mime": "image/jpeg", "urlOriginal":
    "https://static.vocadb.net/img/Artist/mainOrig/23981.jpg?v=37", "urlThumb": …}`，
    原图优先（当 AI 配色参考图，原图的信息量比缩略图多）；都取不到就空串。
    """
    for key in AVATAR_KEYS:
        value = str((picture or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def avatar_url(artist) -> str:
    """P主头像地址（空串 = 站上没有 / 没请求到）。"""
    return str(getattr(artist, "picture", "") or "").strip()


def avatar_filename(artist) -> str:
    """头像存盘时用的文件名（带原图后缀，AI 那边按后缀判 mime）。"""
    suffix = Path(avatar_url(artist).split("?", 1)[0]).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
        suffix = ".jpg"
    name = str(getattr(artist, "name", "") or getattr(artist, "id", "") or "P主")
    return f"{AVATAR_PREFIX}{safe_filename(name)}{suffix}"


def download_avatar(artist, folder=None) -> Optional[Path]:
    """把 P主头像下载到输出目录（「样式」页的默认参考图），返回文件路径。

    没有头像 / 下载失败返回 None（界面上退回「自己选参考图」那一条路，只记日志）。
    已经下过就直接用现成文件（头像换了的活自己删一下）。
    """
    url = avatar_url(artist)
    if not url:
        return None
    target = Path(folder) if folder else get_output_path()
    target = Path(target) / avatar_filename(artist)
    try:
        if not (target.is_file() and target.stat().st_size):
            target.parent.mkdir(parents=True, exist_ok=True)
            image.download_file(url, target)
    except Exception as e:                              # noqa: BLE001 - 下载失败不当错误
        logging.warning("下载 P主头像失败（%s）：%s", url, e)
        return None
    return target if target.is_file() and target.stat().st_size else None


def parse_publish_date(value) -> str:
    """VocaDB 的 `2026-08-28T00:00:00Z` → `2026-08-28`（取不到就空串）。"""
    return normalise_date(str(value or "").split("T")[0])


def song_from_vocadb(item: dict) -> ProducerSong:
    """VocaDB 的一首歌 → `ProducerSong`（中文名不在 VocaDB，留空）。"""
    return ProducerSong(ja=str(item.get("defaultName") or item.get("name") or "").strip(),
                        date=pv_date(item) or parse_publish_date(item.get("publishDate")),
                        song_id=int(item.get("id") or 0), source="vocadb")


def pv_date(item: dict) -> str:
    """这首歌**最早的官方投稿**日期（`YYYY-MM-DD`）；拿不到就空串。

    ⚠️ VocaDB 的 `publishDate` **不等于投稿日期**：收录在专辑里的歌，它写的是**专辑发行日**
    —— 实测 Ruliea 的《エキセントリックブルー》`publishDate = 2024-01-15`，而 Nico / YouTube
    上的稿件是 2024-12-20；読谷あかね 那一批 `2026-04-25` 的歌也全是整张专辑的发行日。
    用户 2026-09 要求：排序看**稿件**的投稿日期 —— 取所有 PV 里最早的「官方投稿」
    （YouTube 自动生成的 `… - Topic` 代传不算，见 `is_auto_upload()`），一个都没有才退回
    `publishDate`。
    """
    dates: List[str] = []
    for pv in item.get("pvs") or []:
        if is_auto_upload(pv):
            continue
        value = parse_publish_date(pv.get("publishDate"))
        if value:
            dates.append(value)
    return min(dates) if dates else ""


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
    所以不写进「投稿的原创曲目」那一格（用户 2026-10 要求：S/950828 那种不要加）。
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
    * `Ruins Record` / `ベルの音が鳴る`：他所有署名都 `isSupport = true`（只是客串编曲）。
    ⚠️ Ruliea 的 `hologram`（`artistString = "Ruliea feat. various"`）**不属于**这一类 ——
    里面有他自己的非支持署名，站上 `Template:Ruliea` 也收着它。
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
    """专辑 / 曲目艺人条目里的艺人 id（VocaDB 把它嵌在 `artist` 里）。"""
    nested = credit.get("artist")
    if isinstance(nested, dict):
        return int(nested.get("id") or 0)
    return int(credit.get("id") or 0)


def credit_roles(credit: dict) -> set:
    """一条署名里的角色（小写集合）：`Default` / `Composer` / `Mastering` / `Other` …

    VocaDB 写成 `roles = "Instrumentalist, Mastering"` 这种逗号串，
    `effectiveRoles` 是同一回事（取两边的并集，哪个有值算哪个）。
    """
    roles = set()
    for value in (credit.get("roles"), credit.get("effectiveRoles")):
        for role in str(value or "").split(","):
            role = role.strip().lower()
            if role:
                roles.add(role)
    return roles


def is_own_song(item: dict, artist_id: int) -> bool:
    """这首歌的**制作人**是不是这位 P主 本人（只是参与的不算）。

    用户 2026-09 定的规矩（先后两次，后者更严）：

    * 先：「职位是 Other 和演奏者（Instrumentalist）的就不收」；
    * 再：「将所有的参与曲目从模板中删除，只保留制作人为 P主本人的条目」——
      所以现在**只留他当制作人的**：署名里有 `Default`（VocaDB 的「主艺人」角色，
      也就是 `artistString` 里那个）或 `Composer`（作曲）。

    由此被滤掉的角色：`Mastering`（母带）、`Arranger`、`Lyricist`（只写词）、
    `Instrumentalist`（演奏）、`Animator` / `Illustrator`（做 PV / 曲绘）、`Other`（挂名）。
    实测：Ruliea《セプテントリオー》他是 `Mastering`、《贅沢と君とカプチーノ》是
    `Instrumentalist, Mastering` —— 都是别人的曲子 → 不收；
    読谷あかね 那一批 `Animator` / `Illustrator` 的（《ポリへドロン》《頭ン痛》
    《ファサード・クエスチョン》…）同理不收，只留他自己投稿的。
    拿不到署名数据（`artists` 缺失 / 里面没有他）时一律保留：宁可多留一条让用户删。
    """
    credits = [a for a in (item.get("artists") or []) if _credit_artist_id(a) == artist_id]
    if not credits:
        return True
    roles: set = set()
    for credit in credits:
        roles |= credit_roles(credit)
    return bool(roles & PRODUCER_ROLES)


def fetch_songs(artist_id: int, max_pages: int = MAX_PAGES) -> List[ProducerSong]:
    """取该 P主的原创投稿曲目（有 PV 的），按投稿日期从早到晚。

    不收三类（用户 2026-10 / 2026-09 要求）：

    * PV 全是「发行商代传的 YouTube 自动投稿」（`has_real_pv()`）；
    * 他不是制作人（只有 `Mastering` / `Animator` / `Illustrator` / `Other` … 署名，
      见 `is_own_song()`）—— 「参与曲目」不进模板。
    """
    songs: List[ProducerSong] = []
    seen = set()
    skipped: List[str] = []
    support_only: List[str] = []
    for page in range(max_pages):
        data = _get_json(VOCADB_SONG_URL, **{
            "artistId[]": artist_id, "start": page * PAGE_SIZE, "maxResults": PAGE_SIZE,
            "getTotalCount": "true", "sort": "PublishDate", "lang": "Default",
            "fields": "Names,PVs,Artists", "songTypes": "Original", "onlyWithPvs": "true",
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
            if not is_own_song(item, artist_id):
                support_only.append(song.ja)
                continue
            songs.append(song)
        if len(items) < PAGE_SIZE:
            break
    if skipped:
        logging.info("跳过了 %d 首只有 YouTube 自动投稿的曲目：%s",
                     len(skipped), "、".join(skipped))
    if support_only:
        logging.info("跳过了 %d 首他只有「参与」署名（不是他当制作人）的曲目：%s",
                     len(support_only), "、".join(support_only))
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


def canonicalise_names(songs: Iterable[ProducerSong], extra: Sequence[str] = ()) -> int:
    """把「重定向名字」换成**真条目名**（`Chilly` → `四散`），返回改了几条。

    实测（用户 2026-09 拿 `Template:Yomitan Akane` 的修订历史报的）：P主 页面里写着
    `|条目 = Chilly`，而站上 `Chilly` 和 `散り散り` 都重定向到真条目「四散」——
    用户手改模板时把 Chilly 换成了四散。所以：

    * 已经填的中文名是重定向 → 换成真条目名；
    * 还没中文名、但日文原名本身重定向到某个条目（`散り散り` → `四散`）→ 那个真条目名
      就是它的中文条目名（模板里就该链到它）。
    """
    pending = [song for song in songs if song.ja or song.cn]
    if not pending:
        return 0
    wanted: List[str] = []
    for song in pending:
        for value in (song.cn, song.ja):
            if value and value not in wanted:
                wanted.append(value)
    wanted.extend(name for name in extra if name and name not in wanted)
    try:
        mapping = wiki_api.redirect_targets(wanted)
    except Exception as e:                              # noqa: BLE001 - 查不到就保持原样
        logging.warning("查询重定向失败：%s", e)
        return 0
    if not mapping:
        return 0
    changed = 0
    for song in pending:
        target = mapping.get(song.cn) if song.cn else None
        if target and strip_disambig(target) != strip_disambig(song.cn):
            song.cn = target
            song.page_exists = True
            changed += 1
            continue
        if not song.cn:
            target = mapping.get(song.ja)
            if target and strip_disambig(target) != strip_disambig(song.ja):
                song.cn = target
                song.page_exists = True
                changed += 1
    return changed


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
    # P主 页面 / 条目名可能是重定向（实测 `|条目 = Chilly` 实际是「四散」）→ 换成真条目名
    canonicalise_names(work.songs)
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


# 名字左右紧邻假名 / 汉字 → 说明它只是「更长词的一部分」。
# 实测：`ぽい` 是 `神っぽいな` 的一部分、`マニュア` 是 `わたしマニュアル` 的一部分 ——
# 用 `name in text` 会把「像神一样呐」「自我手册」当成它们的条目名（用户 2026-09 报的）。
# 字符范围与 `_signature()` 对齐（中点 U+30FB 是标点，排掉）。
NAME_EDGE = (r"\u3040-\u30fa\u30fc-\u30ff"        # 假名
             r"\u3005\u3006"                      # 々 〆
             r"\u3400-\u4dbf\u4e00-\u9fff")       # 汉字
# 条目自己声明的歌名：`{{标题替换|{{lj|日文名}}}}` 与信息框里的这些参数
SONG_NAME_TEMPLATE = "标题替换"
SONG_NAME_PARAMS = ("歌曲名称", "日文名", "原文名", "曲名", "歌名", "标题")
SONG_NAME_PARAM_RE = re.compile(r"\|\s*(?:%s)\s*=\s*([^\n]*)" % "|".join(SONG_NAME_PARAMS))
# 信息框里的歌名会有多个写法，用 `<br>` 分开（`{{lj|散り散り}}<br>四散`）
LINE_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def has_name(text: str, name: str) -> bool:
    """`name` 是不是**作为一个完整的名字**出现在 `text` 里。

    不能直接用 `name in text`：中文 / 日文没有词边界，`ぽい` 是 `神っぽいな` 的一部分、
    `マニュア` 是 `わたしマニュアル` 的一部分，直接比会把别的歌（甚至别人的 P主 名）
    当成这首歌的别名。所以要求左右**不紧邻假名 / 汉字**。
    """
    value = str(name or "").strip()
    if not value:
        return False
    pattern = re.compile("(?<![%s])%s(?![%s])"
                         % (NAME_EDGE, re.escape(value), NAME_EDGE))
    return bool(pattern.search(str(text or "")))


def songbox_body(text: str) -> str:
    """歌曲信息框 `{{…Songbox|…}}` 的参数部分（没信息框就返回空串）。

    ⚠️ 歌名只在**信息框里面**找：整个条目的 `|标题 =` / `|歌名 =` 到处都是
    （`{{其他版本|标题=…}}`、`{{导航标题|2023}}`），全篇扫会抳错东西。
    """
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


def declared_song_names(text: str) -> List[str]:
    """条目**自己声明**的歌名（日文名 / 中文名都可能在这里）。

    只认两处（都是站上实测的写法）：

    * `{{标题替换|{{lj|散り散り}}}}` —— 条目用这个模板把标题显示成日文原名；
    * 信息框里的 `|歌曲名称 = {{lj|散り散り}}<br>四散`（也有 `|日文名 =` / `|标题 =` 的写法）。

    比「正文里出现过这几个字」可靠得多：榜单页 / 别人的条目只在正文里提一嘴，
    这里不会写 —— 实测搜「マニュア」会搜到「わたしマニュアル」的条目、
    搜「ぽい」会搜到「神っぽいな」的条目，它们自己声明的都不是搜的那个名字。
    """
    values: List[str] = []
    for body in template_calls(text or "", SONG_NAME_TEMPLATE):
        values.extend(LINE_BREAK_RE.split(body))
    body = songbox_body(text)
    if body:
        for match in SONG_NAME_PARAM_RE.finditer(body):
            values.extend(LINE_BREAK_RE.split(match.group(1)))
    names: List[str] = []
    for value in values:
        name = clean_title(value)
        if name and name not in names:          # 同一个名字可能两处都写着
            names.append(name)
    return names


def page_is_song_entry(title: str, text: str, ja: str) -> bool:
    """这一页是不是「**就是**日文名为 `ja` 的这首歌」。

    两个信号：页面标题就是日文原名（去消歧义后缀后相等），或者它**自称**的歌名
    （`declared_song_names()`）里有这一个。两者都要求是**完整的名字**（`has_name()`），
    不然 `ぽい` 会在 `神っぽいな` 上匹配到。

    故意**不看**「正文里随便出现过」：实测过三种误判 —— `ぽい` 在 `神っぽいな` 里
    （用户 2026-09 报的）、`マニュア` 在 `わたしマニュアル` 里、`エオ` 是别人的 P主 名
    `EO(エオ)`（拿它的条目当《エオ》的中文名，用户手改模板时把这三个都撇了）。
    """
    if not looks_like_song_page(text):
        return False
    target = strip_disambig(str(ja or "").strip())
    if not target:
        return False
    if strip_disambig(str(title or "").strip()) == target:
        return True
    signature = _signature(target)
    return any(has_name(name, target) or _signature(name) == signature
               for name in declared_song_names(text))


# 信息框里的 P主 字段（`|P主 = {{lj|[[読谷あかね]]}}` / `|P主 = [[shikisai]]`）
SONGBOX_PRODUCER_RE = re.compile(r"\|\s*P\s*主\s*=\s*([^\n]*)")


def producer_names(producers) -> List[str]:
    """把「P主 名」参数规整成一串：**第一个**用来写消歧义后缀，整串用来核对。"""
    if isinstance(producers, str):
        producers = [producers]
    names: List[str] = []
    for name in producers or []:
        value = str(name or "").strip()
        if value and value not in names:                 # 条目名 / 模板名 常常是同一个
            names.append(value)
    return names


def work_producer_names(work: "ProducerWork") -> List[str]:
    """一位 P主 的几种写法：**条目名**（第一个，写消歧义后缀用）/ 模板名 / VocaDB 名。

    用户 2026-09-29 挑的是「跟在「曲目」页「P主」框里填的那个名字一致」（条目名）。
    三种都拿去核对信息框里的 `|P主 =`：实测页面里既有 `[[読谷あかね]]` 也有
    `[[shikisai]]`，还有 `[[Yomitan_Akane|{{lj|読谷あかね}}]]` 这种带下划线的写法。
    """
    return producer_names([work.page_name, work.template_name, work.artist.name])


def song_producers(text: str) -> List[str]:
    """歌曲信息框里 `|P主 = …` 写的人（原样返回；读不到就是空表）。"""
    body = songbox_body(text)
    if not body:
        return []
    return [match.group(1).strip() for match in SONGBOX_PRODUCER_RE.finditer(body)]


def page_by_other_producer(text: str, producers) -> bool:
    """这一页是不是「**同名**，但是别人的歌」。

    歌名对得上（`page_is_song_entry()` 过了）**不代表**就是这首歌 —— 实测（用户 2026-09-29
    拿 `Template:Shikisai` 与条目 `偏执狂` 的差异报的）：《パラノイア》有两首，shikisai 的（2022）
    与 全て奴等の所為です。 的（2020，条目就叫 `偏执狂`），两首条目自称的歌名都是 `パラノイア`，
    光看歌名分不出来。这时得比对信息框里的 `|P主 =`：里面**没有我们**才算重名冲突。
    读不到 `|P主` 字段时返回 False（不敢乱判，照旧用这个名字）。
    """
    values = song_producers(text)
    if not values:
        return False
    signature = "".join(_signature(value) for value in values)
    return not any(_signature(name) and _signature(name) in signature
                   for name in producer_names(producers))


def disambiguated(name: str, producer: str) -> str:
    """重名时加消歧义后缀：`偏执狂` + `shikisai` → `偏执狂(shikisai)`。

    “消歧义”是站上本来就有的写法（实测 `偏执狂(全奴等)` 这个页面后来才被搬到 `偏执狂`）；
    用户 2026-09-29 手改 `Template:Shikisai` 时把 `偏执狂` 改成的就是 `偏执狂(shikisai)` ——
    半角括号、后缀是 P主 名。宁可写个红链，也不要链到别人的条目上。
    """
    plain = strip_disambig(str(name or "").strip())
    return f"{plain}({producer})" if plain and producer else plain


def is_disambiguated(name: str, producer: str = "") -> bool:
    """这个名字是不是刚加过消歧义后缀（站上多半还没这个页面 → 红链）。"""
    return bool(producer) and str(name or "").endswith(f"({producer})")


def search_page_by_song(song: ProducerSong, producer="") -> str:
    """按日文原名搜维基，返回对得上的**歌曲条目**名（搜不到返回空串）。

    `generator=search` 一次请求就带回候选页正文，用它核对四件事：

    1. 这一页**本身是歌曲条目**（有 `{{…Songbox}}`）—— 榜单页 / 专辑页 / P主页面
       只是「列了这首歌」，拿它们的标题当条目名就全错了（用户 2026-10 报的
       `NICONICO VOCALOID SONGS TOP20/第87期`）；
    2. 这一页**自称**的歌名就是这首（`{{标题替换|…}}` / 信息框的 `|歌曲名称 =`），
       而不是正文里恰好出现过这几个字 —— 实测搜「マニュア」会搜到「わたしマニュアル」
       的条目「自我手册」、搜「エオ」会搜到 P主 叫「EO(エオ)」的「青果实」；
    3. 出现时得是**完整的名字**：搜「ぽい」不能拿「神っぽいな」的条目
       「像神一样呐」当答案（用户 2026-09 报的就是这个）。
    4. 同名但是**别人的**歌时（`page_by_other_producer()`），不链过去 ——
       改用消歧义名（`偏执狂(shikisai)`，用户 2026-09-29 报的），搜不到别人的同名页
       才轮到它。

    `producer` 是这位 P主 的几种写法（条目名 / 模板名 / VocaDB 名）：**第一个**用来写
    消歧义后缀，整串用来核对信息框里的 `|P主 =`。
    """
    term = song.ja or song.cn
    if not term:
        return ""
    try:
        payload = wiki_api.search_pages_with_text(term, limit=SEARCH_LIMIT)
    except Exception as e:                              # noqa: BLE001 - 搜不到就当没有
        logging.warning("搜索维基条目失败（%s）：%s", term, e)
        return ""
    names = producer_names(producer)
    taken = ""
    for title, text in payload:
        if not page_is_song_entry(title, text, term):
            continue
        if names and page_by_other_producer(text, names):
            # 同名但是别人的歌：链过去就指错人了，先记下来
            taken = taken or disambiguated(title, names[0])
            continue
        return title
    if taken:
        logging.info("《%s》在站上跟别人的歌重名，改用消歧义名「%s」", term, taken)
    return taken


def fill_missing_names(songs: Sequence[ProducerSong], producer="",
                       progress: Optional[Callable[[str], None]] = None) -> int:
    """逐首搜维基补中文条目名；返回补了几条（给界面的「从维基补全条目名」按钮用）。

    `producer` 见 `search_page_by_song()`：搜到的同名条目要是**别人的**歌，不链过去，
    改写成站上消歧义写法的 `偏执狂(shikisai)`（那个名字站上还没页面 → 红链，`page_exists`
    记 False，后面也不会往那个页面里写导航框）。
    """
    names = producer_names(producer)
    filled = 0
    for index, song in enumerate(songs, start=1):
        if song.cn or not song.ja:
            continue
        if progress is not None:
            progress(f"（{index}/{len(songs)}）搜索「{song.ja}」…")
        title = search_page_by_song(song, names)
        if title:
            song.cn = title
            song.page_exists = not is_disambiguated(title, names[0] if names else "")
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


def _part_is_name(part: str, ja: str) -> bool:
    """标题里的这一段是不是「就是这首歌的名字」。

    实测标题写法：`ネハン / 雄之助 feat. 重音テトSV`、`忧蓝情结/ブルー・マニアック feat.初音ミク`
    —— 切成片段后**整段相等**（去噪后）就算；其次是这一整段里**完整地**写着日文名
    （`低画質の人` 后面跟着 `feat.` 这种可以），或者反过来：这一整段就是日文名本身
    （`【必見】朝の作り方` 里抽出 `朝の作り方`）。

    ⚠️ 不能用 `日文名 in 片段`：那样 `ぽい` 会在 `神っぽいな` 上匹配（用户 2026-09 报的）。
    """
    target = _signature(ja)
    signature = _signature(part)
    if not target or not signature:
        return False
    if signature == target:
        return True
    if has_name(part, ja):
        return True
    return len(str(part or "").strip()) >= 3 and has_name(ja, part)


def chinese_from_title(title: str, ja: str, artist: str = "") -> Tuple[str, bool]:
    """从 b 站标题里抽中文名 → (名字, 是不是「贴着日文名」那种)。

    实测标题写法：`ネハン / 雄之助 feat. 重音テトSV`（本家，没中文）、
    `【中文字幕】忧蓝情结/ブルー・マニアック feat.初音ミク【ナルネア】`（中文名就在日文名旁边）。
    所以：先找出含日文名的那个片段，优先取**紧邻**它的中文片段；
    没有相邻的就退到随便一个中文片段（这种可信度低一档，`pick_candidate()` 会另作要求）。
    P主 自己的名字（`雄之助` 这种汉字写法）就贴在日文名旁边，得先排掉。

    ⚠️ 标题里**根本没出现**这首歌的名字时，一个候选都不给（返回空）—— 实测搜「ぽい」会
    返回「神っぽいな」的 **b 站视频**，标题里没中文名也能从旁边的句子凑出一个，
    而那个名字在维基上恰好是个真条目（「像神一样呐」），于是被当成《ぽい》的中文名
    （用户 2026-09 报的）。宁可少填一条，也不要填错。
    """
    target = _signature(ja)
    if not target:
        return "", False
    skip = {_signature(artist)} if artist else set()
    parts = split_title(title)
    index = next((row for row, part in enumerate(parts) if _part_is_name(part, ja)), -1)
    if index < 0:
        return "", False
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

    只认**确实对得上这首歌**的条目：名字里完整地写着日文原名（`ぽい` 不算
    `神っぽいな` —— 用户 2026-09 报的「ぽい 被识别成 像神一样呐」），或者署名里有这个 P主
    （网易云上有的条目直接写中文名，那就只能靠 P主 认）。
    """
    target = _signature(ja)
    if not target:
        return []
    item_name = str(item.get("name") or "")
    artists = "".join(str(one.get("name") or "") for one in (item.get("artists") or []))
    named = has_name(item_name, ja) or _signature(item_name) == target
    if not named and not (artist and artist in artists):
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
                   texts: Dict[str, str], ja: str = "",
                   producers=()) -> Optional[Tuple[str, str]]:
    """挑一个最可信的候选 → `(名字, 来源)`；都不够格就返回 None。

    `ja` 是这首歌的日文原名，`producers` 是这位 P主 的几种写法（见 `producer_names()`）。
    站上有这个条目时，不光要看它「是不是歌曲条目」，还要看它**是不是这首歌**
    （`page_is_song_entry()`）—— 实测《リボン》曾被填成
    `迷途孩子的缎带`（另一个真条目，用户 2026-09 手改掉了）；`ja` 给空时只能退到
    「是不是歌曲条目」这一步。

    排序：**站上真有这个歌曲条目**的最优先（`texts` 里且核实通过），其次看来源可信度，
    同档取短的。门槛：

    * 站上**已经有**这个名字、但它不是这首歌（自认的歌名对不上）→ 不管来源多可信都不用
      （链会指到别人条目上）；
    * 站上这个名字是**同名但是别人的歌**（歌名对得上、信息框 `|P主 =` 里没有我们，
      `page_by_other_producer()`）→ 不链过去，改用消歧义名 `偏执狂(shikisai)`
      （比「站上没这个名字」的候选优先：站上确实有一首同名歌，只是不是我们的）；
    * 站上没这个名字：网易云的结构化译名（可信度 3）可以直接用 —— 要么是官方译名，
      要么是个「署名里有这个 P主」的条目名（实测：ネハン → 涅槃、アタマモミ → 揉揉头）；
    * 站上没这个名字、又是 b 站标题里抽出来的（可信度 1 / 2）→ **不敢用**：
      实测 さよなら天才 的标题里能抽出「高潮部分真的好棒」这种句子（就在日文名旁边，
      靠相邻关系分辨不出来）。
    宁可留空：名字填错比空着更糟（模板里的链会全歪）。
    """
    names = producer_names(producers)
    best: Optional[Tuple[Tuple[int, int, int], Tuple[str, str]]] = None
    for name, source, score in candidates:
        body = texts.get(name)
        if body is not None:
            ok = page_is_song_entry(name, body, ja) if ja else looks_like_song_page(body)
            if not ok:
                # 站上**已经有**这个名字，但它不是这首歌 → 绝对不能用（链会指到别人的条目上）
                continue
            if names and page_by_other_producer(body, names):
                # 同名但是别人的歌 → 不能链过去；改写成消歧义名（比「站上没这个名字」强一档）
                rank = (1, score, -len(name))
                picked = (disambiguated(name, names[0]), source)
            else:
                rank = (2, score, -len(name))
                picked = (name, source)
        else:
            # 站上没这个名字：只有网易云的结构化译名（可信度 3）敢直接用
            if score < 3:
                continue
            rank = (0, score, -len(name))
            picked = (name, source)
        if best is None or rank > best[0]:
            best = (rank, picked)
    return best[1] if best else None


def fill_external_names(songs: Sequence[ProducerSong], artist: str = "",
                        progress: Optional[Callable[[str], None]] = None,
                        producers=()) -> Dict[str, object]:
    """按日文原名去 bilibili / 网易云 搜中文名，填进还没有中文名的曲目。

    `artist` 是 VocaDB 上的 P主 名（用来从标题里认出「这是 P主 名不是歌名」）；
    `producers` 是这位 P主 的几种写法（条目名 / 模板名 / VocaDB 名）—— 名字跟站上
    别人的同名歌撞了的时候，用来写消歧义名（`偏执狂(shikisai)`）。

    最后过一道 `canonicalise_names()`：填进来的名字如果是重定向（或者日文原名本身就是
    指向真条目的重定向），一律用真条目名 —— 免得模板链到重定向（用户 2026-09 报的
    `Chilly` 应该是「四散」）。

    返回 `{'ok', 'filled', 'checked', 'by_source', 'names'}`（界面拿来写状态行）。
    """
    names = producer_names(producers)
    pending = [song for song in songs if not song.cn and song.ja]
    if not pending:
        return {"ok": True, "filled": 0, "checked": 0, "by_source": {}, "names": {}}
    found: Dict[str, List[Tuple[str, str, int]]] = {}
    for index, song in enumerate(pending, start=1):
        if progress is not None:
            progress(f"（{index}/{len(pending)}）搜「{song.ja}」…")
        found[song.ja] = external_candidates(song, artist)
    # 候选名批量拿去 wiki 核一遍：站上真有这个歌曲条目的话，基本就是对的
    lookup = list(dict.fromkeys(name for items in found.values() for name, _s, _c in items))
    texts = wiki_api.fetch_pages_text(lookup) if lookup else {}
    by_source: Dict[str, int] = {}
    for song in pending:
        picked = pick_candidate(found.get(song.ja) or [], texts, song.ja, names)
        if not picked:
            continue
        name, source = picked
        song.cn = name
        song.page_exists = name in texts
        by_source[source] = by_source.get(source, 0) + 1
    canonicalise_names(pending)
    filled = {song.ja: song.cn for song in pending if song.cn}
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


def title_template(artist_name: str, color: str) -> str:
    """标题栏里那个「带颜色的 P主 名」怎么写。

    名字里有假名（日文）→ `{{Cj|颜色|名字}}`（用户 2026-09-29 要求）；
    纯拉丁 / 纯汉字的（`Ruliea`、`雄之助`）→ 照旧 `{{colorlink|颜色|名字}}`。
    实测 `Template:Cj` = `<span style="color:…">{{lang|ja|…}}</span>`，参数顺序与 `colorlink` 一样，
    站上 `Template:Yomitan Akane` 就是 `|title={{Cj|#ffffff|読谷あかね}}`。
    """
    template = "Cj" if KANA_RE.search(str(artist_name or "")) else "colorlink"
    return f"{{{{{template}|{color}|{artist_name}}}}}"


def build_template(work: ProducerWork) -> str:
    """把 P主 + 曲目 + 专辑拼成整篇模板 wikitext（含 `<noinclude>` 说明与分类）。"""
    styles = {**DEFAULT_STYLES, **(work.styles or {})}
    params = style_params(styles)
    name = (work.template_name or work.artist.name or work.page_name or "").strip()
    page = (work.page_name or work.artist.name or name).strip()
    artist_name = (work.artist.name or page).strip()
    title_color = str(styles.get("titleFg") or "").strip() or "#006CAD"
    # 条目名跟 P主 名不一样时（`Yomitan Akane` vs `読谷あかね`）带个显示名（用户手改过这里）
    linked = f"[[{page}|{artist_name}]]" if artist_name and artist_name != page else f"[[{page}]]"

    lines: List[str] = [
        "<noinclude>",
        f"此模板用于记录{linked}的作品。",
        "",
        "若有遗漏或未来再有补充，欢迎随时编辑。",
        "",
        "如果想要调用折叠状态的本模板，请使用"
        "<span style=color:blue><nowiki>{{</nowiki>{{PAGENAME}}<nowiki>|collapsed}}</nowiki></span>。"
        + PRODUCER_CATEGORY,
        "</noinclude>{{Navbox",
        f"|name={name}",
        f"|title={title_template(artist_name, title_color)}",
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


def _is_template_line(line: str) -> bool:
    """这一行是不是「独占一行的模板调用」（注释小节里那一串都是这个形状）。"""
    return bool(TEMPLATE_NAME_RE.match(str(line or "").strip()))


def is_activity_template(line: str) -> bool:
    """这一行是不是**活动模板**（`{{The VOCALOID Collection2025冬}}`）。

    歌姬模板要插在活动模板**前面**（用户 2026-09-30）；P主模板与其它歌手模板都算「前者」。
    """
    match = ACTIVITY_TEMPLATE_NAME_RE.match(line or "")
    if not match:
        return False
    name = match.group(1).strip()
    return bool(ACTIVITY_PREFIX_RE.match(name)) or bool(ACTIVITY_SEASON_RE.search(name))


def _insert_offset(block: Sequence[str], position: str) -> int:
    """在一串模板里的落点（下标）。

    * `top` → `0`（最前面）；
    * `after_producer` → **最后一个非活动模板之后、第一个活动模板之前**：
      没有活动模板时就落在整串末尾（= 跟在 P主/歌手模板后面）；
      整串里面全是活动模板、或者活动模板排在前面时，落在第一个活动模板之前。
    """
    if position != POSITION_AFTER_PRODUCER:
        return 0
    first_activity = len(block)
    last_other = -1
    for index, line in enumerate(block):
        if is_activity_template(line):
            first_activity = index
            break
        last_other = index
    return min(first_activity, last_other + 1)


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


def insert_template(text: str, template_name: str, call: str = "",
                    position: str = POSITION_TOP) -> Tuple[str, str]:
    """把 `{{模板名}}` 插进条目正文；返回 (新正文, 说明)。

    `call` 是「要写进双层花括号里的整串」（默认为模板名）—— 歌姬模板往歌曲条目里写的是
    `{{重音Teto/2024|collapsed}}`（在歌曲条目里默认折叠），往歌姬条目里写的是
    `{{重音Teto|nocate=1}}`，这里传 `call="…"` 即可；判定「是否已经有」仍用模板名。

    `position` 决定落点（见 `POSITION_*`）：P主模板一直是**最前**（`top`，
    真实编辑逐字节核对过，别改）；歌姬模板用 `after_producer` —— 跟在 P主/歌手模板后面、
    活动模板（`{{The VOCALOID Collection2025冬}}`）前面（用户 2026-09-30 要求）。

    具体位置（用户 2026-10 用真实编辑拍板，已按 `NEH#` 那几次编辑逐行核对）：

    * 条目里**已经有**这个模板 → 原样返回；
    * 有「== 注释 ==」类小节 → 插到**小节里面**、`<references/>` 的下一行；
      ⚠️ **不是**插在注释标题上方（2026-10 之前就是这么写错的：`{{Ruliea}}` 被放在了
      `== 注释与外部链接 ==` 上面，用户手工改了三篇 —— 再见天才 / 曾想与你对称 / Last dinner）；
    * 注释标题上方紧挨着的那一串大家族模板（`{{NurseRobot TypeT}}`、
      `{{The VOCALOID Collection2025冬}}` …）**一并挪进小节**
      —— 用户原话「如果『== 注释 ==』上方有大家族模板也一并移动至其下」；
    * 那一串里**插在第几个**由 `position` 决定：
      * `top`（P主模板）—— 插在整串**最前面**（实测 `2代目閻魔`：用户先试过排在中间（251489）
        又排到最后（251574），最后定在**最前面**（revid 251587）——
        `<references/>` / `{{Yomitan Akane}}` / `{{重音Teto/2024|nocate=1}}` / `{{重音Teto/2026|nocate=1}}`）；
      * `after_producer`（歌姬模板）—— 插在 **P主/歌手模板之后、活动模板之前**
        （用户 2026-09-30：「歌姬模板的位置在P主模板和活动模板之间」）；
    * 没有注释小节 → 末尾若有一串大家族模板（`{{The VOCALOID Collection2026夏}}`…），
      同样按 `position` 插进**这一串里面**（分类按惯例守在最末尾）；
      没有大家族模板才插到分类行上方，连分类都没有就追加到末尾。

    实测（虽然是人类。）：原版末尾是 `}}\n\n{{The VOCALOID Collection2026夏}}\n\n[[Category:…]]`，
    用户手改后（revid 251458）把 `{{Ruliea}}` 放在**大家族模板之上**；
    2026-10 之前这里走的是「插到分类行上方」，结果插到了大家族模板下面（revid 251450，用户报的）。

    ⚠️ 回放真实页面时比「逐字节」要忽略**行尾那一个换行**：MediaWiki 存正文时会把它去掉，
    而本函数总是以 `\n` 结尾（`insert_into_pages()` 提交的就是这一份）。
    """
    name = str(template_name or "").strip()
    if not name or not text:
        return text, ""
    if contains_template(text, name):
        return text, f"已包含 {{{{ {name} }}}}，未改动"
    inner = str(call or "").strip() or name

    heading = NOTE_HEADING_RE.search(text)
    if heading is None:
        lines = text.rstrip("\n").split("\n")
        first_category = _first_category_line(lines)
        end = first_category if first_category is not None else len(lines)
        start, stop = _plain_template_block(lines, end)
        if stop > start:
            # 末尾那一串大家族模板：按落点插进去（P主模板 = 整串最前）
            block = [line.strip() for line in lines[start:stop]]
            offset = _insert_offset(block, position)
            lines.insert(start + offset, f"{{{{{inner}}}}}")
            if position == POSITION_AFTER_PRODUCER:
                message = (f"没有注释小节，插到末尾大家族模板里的第 {offset + 1}/{len(block) + 1} 行"
                           f"（P主/歌手模板后面、活动模板前面）：{{{{{inner}}}}}")
            else:
                message = (f"没有注释小节，插到末尾大家族模板上方（{stop - start} 个）："
                           f"{{{{{inner}}}}}")
            return "\n".join(_blank_before_categories(lines)) + "\n", message
        if first_category is not None:
            # 没有大家族模板：插在分类行上方（分类按惯例守在最末尾）
            lines.insert(first_category, f"{{{{{inner}}}}}")
            return ("\n".join(_blank_before_categories(lines)) + "\n",
                    f"没有注释小节，插到分类行上方：{{{{{inner}}}}}")
        body = text.rstrip("\n")
        return f"{body}\n\n{{{{{inner}}}}}\n", f"没有注释小节，追加到末尾：{{{{{inner}}}}}"

    before, after = text[:heading.start()], text[heading.start():]
    lines = before.split("\n")
    start, stop = _plain_template_block(lines, len(lines))
    block = [line.strip() for line in lines[start:stop]]
    moved = len(block)
    if moved:
        # 注释标题上方那一串大家族模板：一并挪进小节（落点下面再算）
        remain = "\n".join(lines[:start]).rstrip("\n")
        before = f"{remain}\n\n" if remain.strip() else ""

    # 小节里的落点：<references/> 后面；没有 <references/> 就紧跟标题
    tail = after.split("\n")
    index = 1
    for line_index, line in enumerate(tail[1:], start=1):
        if REFERENCES_RE.match(line):
            index = line_index + 1
            break
    # 落点后面紧跟的那一串模板行（与「从标题上方挪下来的」连成同一串）
    run_end = index
    while run_end < len(tail) and _is_template_line(tail[run_end]):
        run_end += 1
    run = [*block, *tail[index:run_end]]        # 原样保留（只拿 strip 过的副本算落点）
    offset = _insert_offset([line.strip() for line in run], position)
    new_after = "\n".join([*tail[:index], *run[:offset], f"{{{{{inner}}}}}",
                           *run[offset:], *tail[run_end:]])
    anchor = "小节的 <references/> 后面" if index > 1 else "注释小节里"
    note = f"插到{anchor}"
    if moved:
        note += f"，并把注释上方的 {moved} 个大家族模板一并挪了进来"
        if position == POSITION_AFTER_PRODUCER:
            note += "（新模板排在 P主/歌手模板后面、活动模板前面）"
        else:
            note += "（新模板排在它们前面）"
    elif position == POSITION_AFTER_PRODUCER and offset:
        note += f"（排在整串里第 {offset + 1} 个：P主/歌手模板后面、活动模板前面）"
    return "\n".join(_blank_before_categories((before + new_after).split("\n"))), note


def insert_into_pages(template_name: str, titles: Sequence[str],
                      progress: Optional[Callable[[dict], None]] = None,
                      summary: str = "", call: str = "",
                      position: str = POSITION_TOP) -> List[dict]:
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
            new_text, note = insert_template(text, template_name, call, position)
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
