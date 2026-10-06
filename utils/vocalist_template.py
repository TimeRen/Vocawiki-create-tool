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
* **翻唱曲目**在曲目后面写一个 `*`（用户 2026-10-01，引站上文档「翻唱曲目添加「*」号」）；
  这个记号以**既有模板里写的**为准（殿堂页里的 `(翻)` 不能信：一张殿堂页上同名条目多半是
  *别的歌姬* 翻的 —— 实测 `强风大背头` 有 KAITO / 音街鳗 / 初音未来 好几个翻版）。
* `Category:<歌姬>歌曲` 只收**已经建好条目**的歌，翻唱与还没建条目的红链曲目都不在里面 ——
  既有的模板 / 年份子页才是「这位歌姬唱过哪些」的完整名单，`load_existing()` 会把里面
  我们不知道的曲子按原样搬过来（红链就写红链、站上按「无法收录」处理的只写日文名）；
  主模板已经拆成「年份转接行」时，往前翻几版找最近一版带名单的（用户 2026-10-01）。
* **拿不准的**（殿堂页里查不到、条目里的荣誉题头跟殿堂页打架、连投稿年都取不到）
  都记进 `work.flags`：曲目页会弹窗让用户人工复核（用户 2026-09-30 要求）。

⚠️ 不要拿 `name_to_chinese()` 归一化歌姬名再查引擎（见 `/memories/repo/wikitext-generation.md`）：
`結月ゆかり` 会被搬去 CeVIO。这里的歌姬名一律是**条目名**（中文），`get_engine()` 直接吃。
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from config.config import get_output_path
from utils import login, nicolog, vocadb, wiki_api
from utils.helpers import http_get
from utils.name_converter import get_engine, vocaloid_names
from utils.producer_template import (DEFAULT_STYLES, POSITION_AFTER_PRODUCER,
                                     clean_title, declared_song_names, insert_into_pages,
                                     remove_template_from_pages, style_params)
from utils.string import safe_filename

TEMPLATE_PREFIX = "Template:"
# 站上的分类：不分年份的歌姬模板挂在「Category:虚拟歌手模板」（`Template:歌爱雪` 实测）
VOCALIST_TEMPLATE_CATEGORY = "[[Category:虚拟歌手模板]]"
# 拆成年份子页时主模板挂的分类（`Template:重音Teto` 实测挂在「Category:重音Teto模板」）
YEAR_CATEGORY_SUFFIX = "模板"
# 分类页本身的正文（`Category:歌爱雪模板` / `Category:重音Teto模板` / `Category:初音未来模板`
# / `Category:可不模板` 四页实测逐字一致）—— 维基上还没有这个分类时由工具一并建
CATEGORY_PAGE_TEXT = "{{catnav|内容模板|虚拟歌手模板}}\n[[Category:虚拟歌手模板]]"
# 文档页的分类（`Template:重音Teto/doc` 实测挂在「Category:模板文档」）
DOC_CATEGORY = "<noinclude>[[Category:模板文档]]</noinclude>"

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
# 栏在模板里的出场顺序：**破亿播放曲目排最前**，然后神话 → 传说 → 殿堂 → 其他
# （实测站上 `Template:重音Teto/2024` / `Template:Flower/2020` 都是这个顺序；
#  没有破亿那一栏的模板（`Template:重音Teto/2025`）自然就表现不到）
RANK_ORDER: Tuple[str, ...] = (RANK_BILLION, RANK_MYTH, RANK_LEGEND, RANK_HALL, RANK_OTHER)

STATION_NICO = "niconico"
STATION_YOUTUBE = "YouTube"
STATION_BILIBILI = "bilibili"
# 站点的显示顺序（`Template:重音Teto/2024` 是 niconico / YouTube / bilibili）
STATIONS: Tuple[str, ...] = (STATION_NICO, STATION_YOUTUBE, STATION_BILIBILI)
STATION_ALIASES: Dict[str, str] = {
    "niconico": STATION_NICO, "nico": STATION_NICO, "nn": STATION_NICO,
    # ⚠️ 信息框里的投稿卡片写的是 `{{…Songbox/card|nnd|sm13549415|2011年2月10日}}`：
    # 站点码就是 **nnd**（实测站上 23 张卡里 15 张是它）—— 不认它就会把整首歌判成
    # 「既没有投稿 ID 也没有荣誉题头」，站点与栏都定不下来（用户 2026-09-30 报的「深海(たると)」）。
    "nnd": STATION_NICO,
    "youtube": STATION_YOUTUBE, "yt": STATION_YOUTUBE, "y": STATION_YOUTUBE,
    "bilibili": STATION_BILIBILI, "bili": STATION_BILIBILI, "bb": STATION_BILIBILI,
    "b": STATION_BILIBILI,
}
# 信息框里的站点 ID 参数（`|nnd_id` / `|yt_id` / `|bb_id`；`投稿 =` 里的 card 用 yt/bb）
STATION_ID_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("nnd_id", STATION_NICO), ("nn_id", STATION_NICO), ("yt_id", STATION_YOUTUBE),
    ("bb_id", STATION_BILIBILI),
)
# 信息框里的投稿**日期**参数前缀 → 站点（`|nnd_date = 2012-04-11`、`|yt_date = 2013-01-07`）。
# ⚠️ 「一个站一年」：`六兆年零一夜的故事` nico 2012-04-11 / YouTube 2013-01-07，站上
# `Template:IA/2012` 把它放在**神话曲/niconico**、`Template:IA/2013` 放在**神话曲/YouTube**
# （用户 2026-10-01 拿这两个页面修订指出来）—— 拿「最早那个年份」一刀切会两边都写错。
STATION_DATE_CODES: Tuple[Tuple[str, str], ...] = (
    ("nnd", STATION_NICO), ("nn", STATION_NICO), ("yt", STATION_YOUTUBE),
    ("bb", STATION_BILIBILI),
)
# 其他栏的两个子栏（`Template:歌爱雪` 的小字提示）
OTHER_UNHALL = "部分未殿堂曲"
OTHER_YOUTUBE = "部分YouTube投稿"
OTHER_UNHALL_NOTE = "指niconico及bilibili投稿"
# 按日文名兜底找殿堂记录时名字至少要这么长（见 `classify()`）：1~2 个字的名字太容易撞车
MIN_JA_MATCH_LEN = 3
# 「其他」栏的**栏名**：站上主流写法就是 `其他{{注||收录Vocawiki已有条目。}}`
# （实测 里命 / 狐子 / 鸣花姬·尊 / NurseRobot TypeT / 琴叶茜 / 琴叶葵 / 双叶凑音 / SeeU 都这么写；
# 老模板 `Template:歌爱雪` 只写「其他」）。`{{注}}` 的第一个参数是前缀，站上留空。
OTHER_LABEL = "其他{{注||收录Vocawiki已有条目。}}"
# 荣誉题头里的站点档位参数：nrank = niconico、yrank = YouTube、brank = bilibili
HONOR_RANK_PARAMS: Tuple[Tuple[str, str], ...] = (
    ("nrank", STATION_NICO), ("yrank", STATION_YOUTUBE), ("brank", STATION_BILIBILI),
)
# 站上标「翻唱曲目」的记号（用户 2026-10-01 引站上文档「翻唱曲目添加「*」号」）：
# 模板里写在曲目**后面**（`[[凤仙花|{{lj|鳳仙花}}]]*`），殿堂页里写成 `(翻)`
# （实测 `VOCALOID殿堂曲/2014年投稿` 的 `|曲目 = [[凤仙花]](翻)`）。
COVER_MARK = "*"
COVER_WORD = "(翻)"
# 殿堂页里「无法收录」的写法：`{{假链|条目名|理由}}`（条目不链接）。模板里对应的是
# 只写日文名不给链接 —— 实测 `VOCALOID殿堂曲/2023年投稿` 写
# `|曲目 = {{假链|Paraona Boy|由于歌词为AI自动生成，不符合收录条件，无法收录。}} (翻)`，
# 旧模板 `Template:歌爱雪`（revid 251675）写 `{{lj|パラオナボーイ}}*`。
FAKE_LINK_TEMPLATE = "假链"

# ---------------------------------------------------------------- 正则
HONOR_HEADER_RE = re.compile(r"\{\{\s*虚拟歌手歌曲荣誉题头\s*\|([^}]*)\}\}")
# 曲目后面那个「上标」：站上用它标这一版唱的是哪个声库（实测 `Template:IA` 里
# `[[脑内disco|ノウナイディスコ]]<sup>CeVIO</sup>`、`[[鸟之诗|鳥之詩]]<sup>CeVIO</sup>*`）
SUP_MARK_RE = re.compile(r"<sup>\s*([^<]*?)\s*</sup>", re.IGNORECASE)
# 歌曲信息框：`{{VOCALOID Songbox}}` 之外还有 `{{Infobox Song}}`（实测 `Captain little`，
# 那种「只收在专辑里」的曲子用的就是它）。⚠️ 别改 `producer_template.SONGBOX_RE`，
# 那是 P主那边共用的（改了会把专辑页当成歌曲）。
VOCALIST_SONGBOX_RE = re.compile(r"\{\{\s*(?:[^{}\n|]*Songbox|Infobox Song)", re.IGNORECASE)
# 「收录专辑」参数：写了它、又一个投稿 ID 都没有 → 专辑曲（`Captain little` / `八十八键的宇宙`）
ALBUM_PARAM_RE = re.compile(r"\|\s*(?:收录专辑|专辑|Album)\s*=\s*\S", re.IGNORECASE)
# ⚠️ 专辑信息**不一定写在参数里**：实测 `超次元爱歌`（用户 2026-10-01 报的）信息框只有
# 图片 / 颜色 / 演唱 / 歌曲名称 / P主，专辑写在**正文**里 ——
# 「由[[IA]]演唱，收录于专辑'''{{lj|[[未完成エイトビーツ]]}}'''中」，而它原本那个
# niconico 投稿（`二次元の女の子に恋をしてしまって辛い…w`）早就被作者删了，页面里只有
# 专辑版的 `{{music163}}`。所以正文里的「收录（于/在）…专辑」也要认。
ALBUM_WORD_RE = re.compile(r"收录[于在]?[^。\n]{0,12}?专辑")
# 站上把一长串曲目打包的写法：`{{Links|条目{{!}}日文|条目2}}`（`{{!}}` 是转义竖线）
LINKS_TEMPLATE_RE = re.compile(r"\{\{\s*Links\s*\|", re.IGNORECASE)
# 主模板里除了「歌曲」「相关人物」之外要**原样保留**的栏（用户 2026-10-01：拆 IA 时
# 「演唱会」「官方专辑」不能丢）
EXTRA_GROUP_TITLES: Tuple[str, ...] = ("演唱会", "官方专辑")
# 页面标题：<引擎><殿堂曲|传说曲|神话曲|破亿曲>[/<站点>投稿][/<年份>年投稿]
HALL_TITLE_RE = re.compile(
    r"^(?P<engine>[^/]+?)(?P<word>殿堂曲|传说曲|神话曲|破亿曲)"
    r"(?:/(?P<station>[A-Za-z]+)投稿)?"
    r"(?:/(?P<year>\d{4})年投稿)?$")
# 殿堂页里每首歌的写法：`{{Temple Song|…|曲目 = [[条目名|日文名]]}}`（niconico 页）
# 与 `{{Song Honor|歌手 = …|条目 = [[条目名]]}}`（YouTube / bilibili 页）；
# ⚠️ YouTube 那些页写的是**下划线**版 `{{Temple_Song|…}}`（实测
# `VOCALOID传说曲/YouTube投稿/2023年投稿` 90 个全是下划线、空格版只有 1 个）——
# 不认下划线就会把 YouTube 的传说 / 神话曲整页漏掉。
HALL_ENTRY_TEMPLATES = ("Temple[ _]?Song", "Song[ _]?Honor")
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
# `{{lang|ja|六兆年と一夜物語}}`（有些条目把它套在 `{{lj|…}}` 里）→ 取里面那个裸日文名
LANG_JA_RE = re.compile(r"\{\{\s*lang\s*\|\s*ja\s*\|([^{}]*)\}\}", re.IGNORECASE)
SONGBOX_PARAM_RE = re.compile(r"\|\s*([^=|\n]+?)\s*=\s*([^\n]*)")
# 混音版的曲名：`アンチビート／DIVELA REMIX` → 条目名要写 `Antibeat/DIVELA`
# （见 `_remix_entry_title()`；只认 `／<名字> REMIX` 这种写法）
REMIX_ENTRY_RE = re.compile(r"^.+?[／/]\s*(?P<who>[^／/」\s]+)\s*(?i:remix)\s*$")
# 名字里带「remix」的曲目（`透明エレジー -Morimoto hiroCt Remix-` / `…〜和風REMIX〜`）：
# 站上把它们当**另一首独立的歌**，不适用「歌唱栏认不出歌姬就不收」那条
REMIX_WORD_RE = re.compile(r"(?i:remix)|リミックス")
SONGBOX_CARD_RE = re.compile(r"\{\{\s*[^|{}\n]*Songbox/card\s*\|([^}]*)\}\}", re.IGNORECASE)
# 信息框里的「收录专辑」：写了它、又一个投稿 ID 都没有 → 专辑曲（见 `song_fact()`）
SONGBOX_ALBUM_RE = re.compile(r"\|\s*(?:收录专辑|专辑|Album)\s*=\s*\S", re.IGNORECASE)
SINGER_PARAM_RE = re.compile(r"\|\s*演唱\s*=\s*([^\n]*)")
# 「演唱」里挂在括号里的说明：`[[IA]]（IA精选碟）` —— 这种写法说明她只是**收在专辑里**，
# 不是她投的稿（用户 2026-10-01：`夜明けと蛍` 的「初音ミク（投稿）、IA（IA精选碟）」被从
# `Template:IA/2014` 里删了）。括号里写的是「投稿」「翻调」这类就不算。
ALBUM_CREDIT_RE = re.compile(r"专辑|專輯|精选|精選|选辑|選輯|合辑|album|収録|收录", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})")
YEAR_RE = re.compile(r"(\d{4})")
# 从既有模板里继承的两处：样式（titlestyle / groupstyle / liststyle）与「相关人物」那一栏
STYLE_PARAM_RE = re.compile(r"\|\s*(titlestyle|groupstyle|liststyle|evenstyle)\s*=\s*([^\n]*)")
# 既有模板 / 年份子页的分组行：`|groupN = 殿堂曲`、`|title = 歌曲`（subgroup 的写法）
# 与 `|listN = …`（按**缩进**认层级，见 `template_song_entries()`）
TEMPLATE_GROUP_RE = re.compile(r"^(\s*)\|\s*(?:group\s*\d*|title)\s*=\s*(.+?)\s*$")
TEMPLATE_LIST_RE = re.compile(r"^(\s*)\|\s*list\s*\d*\s*=\s*(.*)$")
# 不是曲目的栏（「相关人物」那一栏里全是人物名，别当成曲子搬过来）
NON_SONG_SECTIONS = ("相关人物",)
# 「歌曲」栏里的年份小格（`|groupN = 2014年`）
YEAR_GROUP_RE = re.compile(r"^(\d{4})\s*年$")
# 模板里标「翻唱」的记号：`…]]*` / `…}}*`
COVER_MARK_RE = re.compile(r"(?:\]\]|\}\})\s*\*")
# 殿堂页里标「翻唱」的写法：`[[凤仙花]](翻)` / `{{假链|…}} (翻)`
HALL_COVER_RE = re.compile(r"(?:\]\]|\}\})\s*\(翻\)")
# 殿堂页里「无法收录」的写法：`{{假链|条目名|理由}}`
FAKE_LINK_RE = re.compile(r"\{\{\s*假链\s*\|([^|}\n]*)\|?([^|}\n]*)\}\}")
# 殿堂页 / `Song Honor` 里的歌姬名（niconico 那些 `Temple Song` 没有这个参数）。
# 只记录、不用它去补曲目：实测 97 张殿堂页里「歌手=歌爱雪」独占 85 条、合唱带她的 51 条
# （`白色幸福` = 初音未来、歌爱雪、VY1、POYOROID …），而站上歌姬模板只列这位歌姬自己的
# 曲子 —— 完整名单靠既有模板（见 `_merge_existing_songs()`）。
HALL_SINGER_RE = re.compile(r"\|\s*歌手\s*=\s*([^\n|]*)")
# 主模板里「各年份的转接行」：`|listN = {{歌爱雪/2023|nocate=1|…}}` —— 这不是曲目
YEAR_CALL_RE = re.compile(r"^\{\{\s*[^|{}\n]*/\d{4}\b")
# 嵌套子分块：站上既有 `{{Navbox subgroup`（我们生成的）也有 `{{#invoke:Nav|box|subgroup`
# （手写的那些，实测旧 `Template:歌爱雪`）—— 两条都要认，不然子分块那一行会被当曲目行。
SUBGROUP_RE = re.compile(r"subgroup", re.IGNORECASE)
# 模板里的 HTML 注释（曲目后面常挂着人工写的说明）
TEMPLATE_COMMENT_RE = re.compile(r"<!--([\s\S]*?)-->")
# 曲目行的**续行**：`• <!--` 换行后接 `-->[[下一首]]`（站上与我们的写法都是这样）
CONTINUATION_RE = re.compile(r"^\s*-->")

# 一个歌姬分类里最多处理多少首（**硬上限**，防止误传一个超大分类刷爆接口；
# `Category:初音未来歌曲` 实测 5254 首，得配「只新建年份子页」用）
MAX_SONGS = 20000
MAX_FLAGS = 200                          # 弹窗复核的条数上限（再多也没人点得完）
# 主模板已拆成年份子页时，往前翻几版找「带曲目名单的那一版」（见 `_previous_template_songs()`）
PREVIOUS_TEMPLATE_LIMIT = 12
# VocaDB：红链的歌按原始名去搜（投稿日期 / 歌姬名单），见 `fill_from_vocadb()`
VOCADB_SONG_QUERY_URL = "https://vocadb.net/api/songs"
VOCADB_SONG_PAGE = "https://vocadb.net/S/"
# 一首红链歌最多查几个 PV 的播放量（nicolog / b 站 view 接口各一次请求）
MAX_PLAY_COUNT_LOOKUPS = 4
# b 站 view 接口：数字 aid 那种 PV ID 直接用它（BV 号走 `source_filler.bilibili_view()`）
BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view?aid={0}"
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
    cover: bool = False                 # 站上标了「翻唱」（`(翻)`）—— 模板里要写 `*`
    unlinked: bool = False              # 站上按「无法收录」处理（`{{假链|…}}`）：只写名字、不给链接
    singer: str = ""                    # `|歌手 = …`（只有 `Song Honor` 那种写法里有；只记录）

    @property
    def level(self) -> int:
        return RANK_LEVELS.get(self.rank, 1)


@dataclass
class SongFact:
    """一首歌曲条目里读出来的事实（信息框 + 荣誉题头）。"""

    title: str
    exists: bool = False
    is_song: bool = False
    # 只收在专辑里的曲子（用户 2026-10-01：像 `八十八键的宇宙` 那样，信息框写了
    # `|收录专辑 =` 却一个投稿 ID 都没有）—— 不进模板，与 P主模板的专辑处理一致。
    album_only: bool = False
    # 荣誉题头里写的引擎（`{{虚拟歌手歌曲荣誉题头|CeVIO|nrank=1}}` → `CeVIO`）：
    # 跟歌姬的主引擎不一致时，模板里要给这条曲子加 `<sup>引擎</sup>`。
    engines: Tuple[str, ...] = ()
    # 这条目是不是**多版本**（`{{tabs}}` / 好几个 Songbox）：多版本时题头里的引擎说的
    # 往往是另一个版本，不能拿来当「我们这条曲子用什么声库」（实测 `相思相爱`：题头写
    # `UTAU|yrank=1`，可页里同时有初音未来 ver 与 IA ver —— 用户 2026-10-01 指出
    # 那是 VOCALOID 曲目，不该标 UTAU）。
    multi_version: bool = False
    ja: str = ""
    stations: Tuple[str, ...] = ()
    ranks: Dict[str, int] = field(default_factory=dict)     # 站点 → 荣誉题头里的档
    date: str = ""                                          # 投稿日期（YYYY-MM-DD，最早那个）
    dates: Tuple[str, ...] = ()                             # **所有**投稿日期（一个站一个，见 `song_fact()`）
    station_years: Dict[str, str] = field(default_factory=dict)   # 站点 → 投稿年（各算各的）
    # 条目的「== 二次创作 ==」段落里点到了这位歌姬（站上把翻唱 / 翻调版写在那一段）：
    # 条目信息框的「演唱」栏里认不出她时，就靠这段定收不收（用户 2026-10-01）
    secondary: bool = False
    singers: Tuple[str, ...] = ()
    # 「演唱」里**只挂在精选碟 / 专辑上**的歌姬（`[[IA]]（IA精选碟）`）：
    # 这位歌姬不是这首歌的演唱者 → 不算她的曲子（见 `_album_only_singers()`）
    album_singers: Tuple[str, ...] = ()

    @property
    def years(self) -> Tuple[str, ...]:
        """这首歌跨到的年份（从早到晚）：信息框里各站点投稿日期的年份，并上最早那个。

        实测 `面包屑`：`nnd_date = 2023/8/4` + `yt_date = 2023/8/5` + `bb_date = 2026/7/10`
        → `('2023', '2026')`，站上条目里就挂着 `{{歌爱雪/2023}}` 与 `{{歌爱雪/2026}}`。
        """
        found = {value[:4] for value in (self.dates or ())
                 if len(str(value)) >= 4 and str(value)[:4].isdigit()}
        if str(self.date)[:4].isdigit():
            found.add(str(self.date)[:4])
        return tuple(sorted(found))

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
    # 这首歌**跨到的年份**（一首歌可能两边都有投稿：实测 `面包屑` nico 2023 / bilibili
    # 2026 → 站上条目里挂着 `{{歌爱雪/2023}}` 与 `{{歌爱雪/2026}}` 两条，用户 2026-10-01）。
    # 空 = 就用 `year` 这一个。
    years: List[str] = field(default_factory=list)
    places: List[Tuple[str, str]] = field(default_factory=list)   # [(栏, 站点)]
    # **每个（栏, 站点）自己的投稿年**：一个站一年（实测 `六兆年と一夜物語` nico 2012、
    # YouTube 2013 → 站上 `/2012` 里只在 niconico 那一格、`/2013` 里只在 YouTube 那一格）。
    # 空 = 这个位置不知道是哪一年，归页时按 `year`（最早那年）算。
    place_years: Dict[Tuple[str, str], str] = field(default_factory=dict)
    kind: str = ""                              # 其他栏的子栏（部分未殿堂曲 / 部分YouTube投稿）
    source: str = ""                            # 殿堂页 / 荣誉题头 / 分类
    note: str = ""                              # 界面上的说明（为什么在这个栏）
    page_exists: bool = True
    flag: str = ""                              # 需要人工复核的原因（空 = 没问题）
    # 站上标了「翻唱」：模板里要在曲目后面写 `*`（用户 2026-10-01）。
    # 殿堂页的 `(翻)` 与既有模板的 `*` 都往这里收。
    cover: bool = False
    # 站上按「无法收录」处理（殿堂页写 `{{假链|条目名|理由}}`）：模板里**只写日文名、
    # 不给链接**（实测旧 `Template:歌爱雪` 的 `{{lj|パラオナボーイ}}*`）。
    unlinked: bool = False
    # 曲目后面的「上标」：这一版唱的是哪个声库（`[[鳥之詩]]<sup>CeVIO</sup>`）。
    # 歌姬有好几个声库时站上就这么标（实测 `Template:IA`）；既有模板里写了就继承，
    # 条目荣誉题头的引擎跟这位歌姬的主引擎不一致时也自己补上。
    super_engine: str = ""
    # 链接要挂的锚点（`[[胸部××××#二次创作|胸部××××]]`）：条目「演唱」栏里认不出歌姬、
    # 但「二次创作」段落里点到她时写这个（用户 2026-10-01 的 `Template:IA/2023` 修订）。
    anchor: str = ""
    # 「== 二次创作 ==」段落里点到了这位歌姬（同上，决定收不收）
    secondary: bool = False
    # 这首歌的条目「演唱」栏里认不认得出这位歌姬（默认信其有 —— 从既有模板搬过来的
    # 曲子没读条目时就当认得）。认不出的翻唱曲要剔掉，见 `prune_cover_mismatch()`。
    own_version: bool = True
    # 条目「演唱」栏里把她**只写在专辑 / 精选碟**上（`[[IA]]（IA精选碟）`）：
    # 她没给这首歌投稿 → 不算她的曲子（用户 2026-10-01 的 `夜明けと蛍`）。
    album_credit: bool = False

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
        """条目链接的写法：`{{lj|[[中文|日文]]}}` / `[[中文]]` / `{{lj|日文}}`（红链）。

        `unlinked`（站上按「无法收录」处理）时**只写日文名、不给链接** —— 实测旧
        `Template:歌爱雪` 里那一首写的就是 `{{lj|パラオナボーイ}}*`。
        """
        title, ja = str(self.title or "").strip(), str(self.ja or "").strip()
        if self.anchor and "#" not in title:
            title = f"{title}#{self.anchor}"
        if self.unlinked or not title:
            return f"{{{{lj|{ja or title}}}}}" if (ja or title) else ""
        if not ja or ja == title:
            return f"[[{title}]]"
        return f"{{{{lj|[[{title}|{ja}]]}}}}"

    @property
    def other_kind(self) -> str:
        """其他栏里的子栏（认不出的按「部分未殿堂曲」算）。"""
        return self.kind if self.kind in (OTHER_UNHALL, OTHER_YOUTUBE) else OTHER_UNHALL

    @property
    def all_years(self) -> List[str]:
        """这首歌要挂的年份子页（跨年的按从早到晚；取不到年份时是空表）。

        年份 = 各（栏, 站点）自己的投稿年（`place_years`）并上信息框里所有投稿日期的年份
        —— `六兆年と一夜物語` = 2012（niconico）+ 2013（YouTube）→ `['2012', '2013']`。
        ⚠️ `place_years` 有值时**不再并**信息框里那些日期：翻唱曲的信息框写的是**原曲**的
        投稿日（实测 `magnet` 2009-05-01），并进来会多出一张不该有的年份页。
        """
        values = [str(own).strip() for own in (self.place_years or {}).values()
                  if str(own).strip()]
        if not values:
            values = [str(year).strip() for year in (self.years or []) if str(year).strip()]
        if not values and self.year:
            values = [self.year]
        return sorted(dict.fromkeys(values))

    def year_of(self, rank: str, station: str) -> str:
        """这一栏这一站在哪一年（年份子页 / 回写年份子页时看它）。"""
        return str(self.place_years.get((rank, station), "") or "")

    def places_in(self, year: str, until: str = "") -> List[Tuple[str, str]]:
        """**某一年**的年份子页里该列哪几个（栏, 站点）。

        各站点挂在各自投稿的那一年（用户 2026-10-01 拿 `Template:IA/2012`·`/2013` 的两个
        修订指出的）：`六兆年と一夜物語` 只出现在 `/2012` 的 niconico 格与 `/2013` 的
        YouTube 格里，不重复。

        不知道年份的位置（殿堂页没给、信息框也没写日期）→ 只挂在**最早那一年**，不跨年重复。

        `until` 给了就是**合并子页**（`Template:可不/2021及以前`，见 `work.merge_until`）：
        `year <= until` 的位置都算在这一页里（页内的年份小栏由 `_placements()` 分）。
        """
        places = list(self.places or [(RANK_OTHER, STATION_NICO)])
        fallback = self.year or (self.all_years[0] if self.all_years else "")
        target = str(year or "")
        limit = str(until or "")

        def matched(own: str) -> bool:
            own = str(own or "")
            if own == target:
                return True
            return bool(limit) and own.isdigit() and target == limit and int(own) <= int(limit)

        return [place for place in places if matched(self.year_of(*place) or fallback)]

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
    # ⚠️ 默认**空**，不是 `DEFAULT_STYLES`：新建模板的配色**从空开始**（用户 2026-09-30：
    # 「歌姬模板样式改成从空开始」）—— 不写 style 参数就是 Navbox 默认灰底；
    # 既有模板的样式由 `parse_styles()` 原样顶上（见 `effective_styles()` 的注释）。
    styles: Dict[str, str] = field(default_factory=dict)
    relation: str = ""                          # 「相关人物」那一栏的原文（从既有模板继承）
    # 除了「歌曲」「相关人物」之外**原样保留**的栏（`[(栏名, 块原文)]`）——
    # 用户 2026-10-01：拆 `Template:IA` 时「演唱会」「官方专辑」要留在主模板里。
    extra_groups: List[Tuple[str, str]] = field(default_factory=list)
    existing: str = ""                          # 既有模板的正文（空 = 新建）
    existing_doc: str = ""                      # 既有文档页的正文
    # 既有的**年份子页**正文（空 = 这位歌姬还没有年份子页）：拿它对齐两处站上不统一的
    # 写法 —— 标题里名字与年份之间要不要空格、要不要 `|abovestyle`（见 `build_year_page()`）。
    existing_year: str = ""
    # **合并子页**：站上把早年的曲子并在同一张子页里（实测 `Template:可不/2021及以前`，
    # 里面按年分小栏）。这里是那个年份（`"2021"`）；空 = 一年一页。
    # 值由 `merge_until_from_wiki()`（联网查 `Template:<歌姬>/*`）自动认出来，
    # 不用新增 / 拆分后一个个手动改（用户 2026-10-03 要求）。
    merge_until: str = ""
    # **只新建年份子页、不动既有主模板**（用户 2026-09-30）：适合 `Template:初音未来`
    # 那种手写大导航框 —— 既有主模板保持原样，我们只把 `Template:<歌姬>/<年份>` 写好，
    # 之后由用户自己把子页挂上去（拆分时才有意义，见 `page_specs()`）。
    subpages_only: bool = False
    # 「其他」栏里的曲目**按年份分层**（`其他 → 2022年 / 2023年 → 曲目`，站上 里命 / 狐子 /
    # 鸣花姬·尊 的写法）还是**平铺**（NurseRobot TypeT / 琴叶茜 / 双叶凑音 / SeeU 的写法）。
    # 用户 2026-09-30：「平铺和按年份分层都行，可以给出选项让我选择」→ 不拆时问一句。
    # 只影响不分年份的主模板；年份子页里年份已经固定，再分层没意义。
    other_years: bool = False
    summary: str = ""                           # 抓取过程的一句话统计（界面显示）
    flags: List[dict] = field(default_factory=list)   # 需要人工复核的项
    # 歌唱栏（信息框 `|演唱 =`）里认不出这位歌姬的翻唱曲：**整首不收**（用户 2026-10-01）。
    # 既有模板 / 既有年份子页里也**不能**再按「以模板为准」把它们搬回来。
    skipped_covers: List[str] = field(default_factory=list)

    @property
    def page_name(self) -> str:
        return self.name

    @property
    def template_title(self) -> str:
        return f"{TEMPLATE_PREFIX}{self.name}"

    def copy(self) -> "VocalistWork":
        """深一点的拷贝（曲目与分栏都换成新的列表）——「恢复原样」靠它。"""
        songs = [VocalistSong(**{**vars(song), "places": list(song.places),
                                 "years": list(song.years),
                                 "place_years": dict(song.place_years)})
                 for song in self.songs]
        return VocalistWork(name=self.name, engine=self.engine, split=self.split,
                            songs=songs, styles=dict(self.styles), relation=self.relation,
                            extra_groups=[(title, block) for title, block in self.extra_groups],
                            existing=self.existing, existing_doc=self.existing_doc,
                            existing_year=self.existing_year,
                            merge_until=self.merge_until,
                            subpages_only=self.subpages_only, other_years=self.other_years,
                            summary=self.summary, flags=[dict(flag) for flag in self.flags],
                            skipped_covers=list(self.skipped_covers))

    def years(self) -> List[str]:
        """**真的要生成的**年份页（从早到晚）—— 拆分子页时只生成这些。

        = 至少有一首曲子会落到这一年的那几年（跨年的歌 `六兆年と一夜物語` 把 2012 / 2013
        都带上）；只有「日期在这个年份、但没有任何（栏, 站点）落在这一年」的歌不算数
        —— 否则会生成一张空页（实测 IA 那些 2007 年的曲目就是这种：封面曲的原始年份
        被当成投稿年，页面上一条曲子都没有）。

        有合并子页（`merge_until`）时，≤ 它的年份全归到它那一年（页名 `2021及以前`）。
        """
        found = {year for song in self.songs for year in song.all_years
                 if song.places_in(year, self.merge_until)}
        return sorted({self.page_year(year) for year in found})

    def page_year(self, year: str) -> str:
        """曲子上的年 → 它落在哪一张年份子页（合并子页把 ≤ `merge_until` 的算到一起）。"""
        value = str(year or "").strip()
        limit = str(self.merge_until or "").strip()
        if limit and value.isdigit() and limit.isdigit() and int(value) <= int(limit):
            return limit
        return value

    def year_suffix(self, year: str) -> str:
        """年份子页标题里那一段（`2021` → `2021及以前`）。"""
        value = str(year or "").strip()
        limit = str(self.merge_until or "").strip()
        if limit and value == limit:
            return f"{limit}及以前"
        return value

    def merged_page(self, year: str) -> bool:
        """这一页是不是那张合并子页（`<Y>及以前`）。"""
        limit = str(self.merge_until or "").strip()
        return bool(limit) and str(year or "").strip() == limit

    def songs_in(self, year: str) -> List[VocalistSong]:
        """这一年页里的曲子（跨年的那首两边都要出现，具体栏目由 `places_in()` 挑）。"""
        target = str(year)
        return [song for song in self.songs
                if target in {self.page_year(item) for item in song.all_years}
                and song.places_in(target, self.merge_until)]

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


def station_from_title(title: str) -> str:
    """标题里能认出的站点 → `YouTube` / `bilibili` / `niconico`；认不出返回空串。

    站上 `VOCALOID破亿曲` 是个**重定向** → `YouTube上播放数量超过1亿的VOCALOID歌曲`：
    这种「列表页」标题里没有 `/YouTube投稿` 那套后缀，站点只能从标题正文里认
    （不认就会把 YouTube 的破亿曲全算成 niconico，用户 2026-09-30 报的破亿栏就是这里错的）。
    """
    lowered = str(title or "").lower()
    for word, station in (("youtube", STATION_YOUTUBE), ("bilibili", STATION_BILIBILI),
                          ("niconico", STATION_NICO)):
        if word in lowered:
            return station
    return ""


def parse_hall_title(title: str, redirect_to: str = "") -> Optional[dict]:
    """页面标题 → `{'rank','station','year'}`（认不出返回 None）。

    `redirect_to` 是这个标题的重定向目标（见 `wiki_api.redirect_targets()`）：
    标题里没有站点后缀时，去目标标题里再认一遍站点。
    """
    match = HALL_TITLE_RE.match(str(title or "").strip())
    if not match:
        return None
    word = match.group("word")
    raw_station = str(match.group("station") or "").lower()
    station = STATION_ALIASES.get(raw_station, "")
    if raw_station and not station:
        return None                             # 认不出的站点（`/acfun投稿` 之类）先不收
    if not station:
        station = station_from_title(redirect_to or title) or STATION_NICO
    return {"rank": word, "station": station,
            "year": match.group("year") or ""}


def parse_hall_page(title: str, text: str, redirect_to: str = "") -> List[HallEntry]:
    """一个殿堂 / 传说 / 神话页面里的所有歌 → `HallEntry` 列表。

    页面写法（实测 niconico 页与 YouTube / bilibili 页两种）：

        {{Temple Song|nnd_id = sm1924663|曲目 = [[把你给碾到哭哦|把你给辗到哭喔♪]](翻)|…}}
        {{Song Honor|歌手 = 初音未来|yt_id = tktcOUi-x-A|条目 = [[细菌污染]]|…}}

    两处坑：

    * 殿堂曲页面里**也**列着已经升到传说 / 神话的歌，靠 `|传说 = 1` / `|神话 = 1`
      标出来（实测 `VOCALOID殿堂曲/2008年投稿` 的 `四叶草♣俱乐部`）—— 不读标记就会把
      传说曲当成殿堂曲（用户 2026-09-30 报的）；
    * 红链写成裸名字的（`|曲目 = {{lj|パラオナボーイ}}*`）也收，目标名就是那串日文；
    * `VOCALOID破亿曲` 是重定向，站点得从**目标标题**里认（见 `parse_hall_title()`）；
    * 站上标「翻唱」的写法是 `(翻)`（`|曲目 = [[凤仙花]](翻)`）、模板里是 `*`；
    * 站上说「无法收录」的写法是 `{{假链|条目名|理由}}`（条目不链接）—— 实测
      `VOCALOID殿堂曲/2023年投稿` 的 `Paraona Boy`，旧模板里对应写 `{{lj|パラオナボーイ}}*`。
    """
    info = parse_hall_title(title, redirect_to)
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
        # ⚠️ 值要截到**本次调用的边界**：不少殿堂页是整页压成一行的，`|曲目 = …` 一路
        # 吃到行尾会把下一首曲子的 `(翻)` 也算成这一首的（`_take_param_value()` 到下一个
        # 顶层 `|参数 =` 或换行为止）。
        raw = _take_param_value(block[value.start(1):]).strip()
        cover = bool(HALL_COVER_RE.search(raw))
        fake = FAKE_LINK_RE.search(raw)
        links = WIKI_LINK_RE.findall(raw)
        if fake:
            # `{{假链|条目名|理由}}`：条目名是第一个参数（`Paraona Boy`），
            # 日文名在后面的脚注里（`《[[パラオナボーイ]]》原稿…`）
            target = clean_title(fake.group(1))
            ja = clean_title(links[0][0]) if links else ""
        elif links:
            target = clean_title(links[0][0])
            ja = clean_title(links[0][1])
        else:
            # 红链写成裸名字：`{{lj|パラオナボーイ}}*` → `パラオナボーイ`
            # （`*` 是站上标「翻唱」的记号，不是名字的一部分）
            plain = re.sub(r"[{}]", "", LJ_OPEN_RE.sub("", raw)).strip()
            cover = cover or plain.endswith(COVER_MARK)
            plain = plain.rstrip(COVER_MARK).strip()
            target = clean_title(plain)
            ja = ""
        if not target:
            continue
        singer = HALL_SINGER_RE.search(block)
        entries.append(HallEntry(title=target, ja=ja or target,
                                 rank=LEVEL_TITLES.get(level, RANK_HALL),
                                 station=info["station"], year=info["year"], page=title,
                                 cover=cover, unlinked=bool(fake),
                                 singer=clean_title(singer.group(1)) if singer else ""))
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
    # 其中几个是重定向（`VOCALOID破亿曲` → `YouTube上播放数量超过1亿的VOCALOID歌曲`）；
    # 站点信息只在目标标题里，先一次问清（两个请求就够）
    redirects = wiki_api.redirect_targets(titles)
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
                entries += parse_hall_page(title, text, redirects.get(title, ""))
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

def _songbox_bodies(text: str) -> List[str]:
    """正文里**每个** Songbox 的参数部分（多版本 / `{{tabs}}` 条目会有好几个）。

    与 `producer_template.songbox_body` 同一套括号配对。
    """
    raw = str(text or "")
    bodies: List[str] = []
    for match in VOCALIST_SONGBOX_RE.finditer(raw):
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
            continue
        body = raw[match.end():index - 2]
        bodies.append(body[1:] if body.startswith("|") else body)
    return bodies


def _songbox_body(text: str) -> str:
    """第一个 Songbox 的参数部分（要按歌姬挑版本就用 `_pick_songbox()`）。"""
    bodies = _songbox_bodies(text)
    return bodies[0] if bodies else ""


def _singer_matches(singers: Sequence[str], wanted: str) -> bool:
    """`|演唱 =` 里的名字算不算这位歌姬。

    站上写得两种都有：`|演唱 = [[歌爱雪]]`（中文条目名）或 `{{lj|[[歌愛ユキ]]}}`
    （日文名）—— 实测歌爱雪那 142 个多版本条目里有 **34 个只写了日文名**，
    不靠 `vocaloid_names` 归一化就会一律退回第一个 Songbox。
    """
    for name in singers:
        if name == wanted or vocaloid_names.get(name, "") == wanted:
            return True
    return False


def _pick_songbox(text: str, vocalist: str = "") -> str:
    """多版本（`{{tabs}}`）条目里挑**这位歌姬唱的那个版本**的参数部分（用户 2026-09-30）。

    站上的多版本条目是一串 Songbox（一个版本一个 tab），各自的 `|演唱 =` 不同；
    默认取第一个会把年份 / 站点算成**原版**的 —— 实测 `深海(たると)`：原版是初音未来
    2011 年，歌爱雪唱的那版是 2015 年（用户要的），所以年份得按版本取。

    挑法：`|演唱 =` 里出现这位歌姬的 Songbox（中文名、日文名都认，见 `_singer_matches()`）；
    好几个都命中时取**投稿日期最早**的那个（同一歌手重投多次时以最早那版为准）；
    一个都没命中（或没给歌姬名）就用第一个。
    """
    bodies = _songbox_bodies(text)
    if not bodies:
        return ""
    wanted = str(vocalist or "").strip()
    if not wanted:
        return bodies[0]
    matched = [body for body in bodies if _singer_matches(_body_singers(body), wanted)]
    if not matched:
        return bodies[0]
    return min(matched, key=lambda body: min(
        (_date_key(value) for value in _dates_from_songbox(body)), default=(9999, 99, 99)))


SECONDARY_SECTION_RE = re.compile(r"^=+\s*二次创作\s*=+\s*$", re.MULTILINE)
SECONDARY_HEADING_RE = re.compile(r"^=+[^=\n]+=+\s*$", re.MULTILINE)
SECONDARY_ANCHOR = "二次创作"


def secondary_text(text: str) -> str:
    """条目的「== 二次创作 ==」段落正文（站上把翻唱 / 翻调版写在这一段里）。

    实测 `胸部××××`：那一段写「由[[初音ミク]]…，[[IA]]，IA（Rock），[[GUMI]]…演唱」
    —— 条目信息框的「演唱」栏只写了原版，但这首歌她确实唱过（用户 2026-10-01 把它
    收进 `Template:IA/2023`，链接写成 `[[胸部××××#二次创作|胸部××××]]`）。
    """
    raw = str(text or "")
    match = SECONDARY_SECTION_RE.search(raw)
    if not match:
        return ""
    rest = raw[match.end():]
    stop = SECONDARY_HEADING_RE.search(rest)
    return rest[:stop.start()] if stop else rest


def secondary_has_singer(text: str, vocalist: str) -> bool:
    """「二次创作」段落里点没点到这位歌姬（`[[IA]]` / 日文名都认）。"""
    body = secondary_text(text)
    wanted = str(vocalist or "").strip()
    if not body or not wanted:
        return False
    names: List[str] = []
    for target, display in WIKI_LINK_RE.findall(body):
        names.extend([clean_title(target), clean_title(display)])
    names.extend(_singers(body))          # 段落里嵌 Songbox 时读它的 `|演唱 =`
    return _singer_matches(tuple(names), wanted)


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


def honor_engines(text: str) -> Tuple[str, ...]:
    """荣誉题头里的**引擎**（`{{虚拟歌手歌曲荣誉题头|CeVIO|nrank=1}}` → `CeVIO`）。

    站上用它区分「这一版唱的是哪个声库」：`Template:IA` 里给了 `CeVIO` 那些曲子都带
    `<sup>CeVIO</sup>`（用户 2026-10-01 要求拆分子页时把上标保留下来）。
    """
    match = HONOR_HEADER_RE.search(str(text or ""))
    if not match:
        return ()
    names: List[str] = []
    for chunk in str(match.group(1)).split("|"):
        name = chunk.partition("=")[0].strip()
        if not name or "=" in chunk:
            continue                              # 只看位置参数（`|引擎|`）
        name = clean_title(name)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _declared_ja(text: str) -> str:
    """条目自己声明的日文名（`{{标题替换|…}}` / 信息框 `|歌曲名称 = {{lj|…}}`）。

    ⚠️ 信息框里那一串往往还带着**别名与加粗**：实测 `如月专注` 写的是
    `|歌曲名称 = '''{{lj|如月アテンション}}'''(如月Attention/如月专注)<br />…` ——
    不收拾就成了 `如月アテンション(如月Attention/如月专注)`（用户 2026-10-01 要求去掉）。
    """
    names = declared_song_names(text)
    cleaned: List[Tuple[str, str]] = []          # (收拾干净的名字, 原文)
    for name in names:
        raw = str(name or "")
        # `'''夜咄ディセイブ'''/夜咄Deceive/夜谈欺骗` —— 站上把**日文名**写在最前面，后面用 `/`
        # 接别名与中文名（实测 `夜谈欺骗`；`俄罗斯套娃` 写的是 `マトリョシカ/Matryoshka`）。
        # `declared_song_names()` 已经把加粗去掉了，所以这里直接看「第一段带假名 + 后面还有段」
        # 就当别名链截掉 —— 用户 2026-10-01 把 `Template:IA/2013` 里的
        # `夜咄ディセイブ/夜咄Deceive/夜谈欺骗` 改成了 `夜咄ディセイブ`。
        # ⚠️ 先把**尾部的括号别名**摘掉再截 `/`：`{{lj|如月アテンション}}(如月Attention/如月专注)`
        # 里的那个斜杠在括号里，先截就会把名字切成 `{{lj|如月アテンション}}(如月Attention`。
        raw = re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", raw).strip()
        parts = re.split(r"[/／]", raw, maxsplit=1)
        if len(parts) > 1 and parts[0].strip() and re.search(r"[\u3040-\u30ff]", parts[0]):
            raw = parts[0]
        # 名字后面挂的括号别名（`(如月Attention/如月专注)`）先去掉，再交给 `clean_title()`
        # —— 它只认「整串就是 `{{lj|…}}`」的那种写法（实测 `如月专注`）。
        value = re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", raw).strip()
        value = re.sub(r"'{2,}", "", clean_title(value)).strip()
        value = re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", value).strip()
        # `{{lang|ja|六兆年と一夜物語}}` → `六兆年と一夜物語`（站上模板里写的是裸日文名）
        for _ in range(3):
            match = LANG_JA_RE.fullmatch(value)
            if not match:
                break
            value = clean_title(match.group(1)).strip()
        if value:
            cleaned.append((value, raw))
    for value, raw in cleaned:
        if re.match(r"^\s*\d{1,2}\s*[:：]", value):
            continue        # 专辑曲目单里的「07:目を奪う話」这种（本页第一首才是歌名）
        # 明显标了日文（`{{lj|…}}` / `{{ruby|…}}` / `{{lang|ja|…}}`）或带假名的那个才是日文原名
        if re.search(r"\{\{\s*(?:lj|ruby|ルビ|lang)\s*\|", raw, re.IGNORECASE) \
                or re.search(r"[\u3040-\u30ff]", value):
            return value
    return cleaned[0][0] if cleaned else ""


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


def _station_dates(body: str) -> Dict[str, str]:
    """一个版本的信息框里**每个站点**的投稿日期 → `{站点: 'YYYY-MM-DD'}`。

    站上写法：`|nnd_date = 2012-04-11` / `|yt_date = 2013-01-07` / `|bb_date = 2026/7/10`，
    投稿卡片则是 `{{…Songbox/card|nnd|sm27471201|2015年10月29日}}`。同一站点写了两次取**最早**的。
    用途：年份子页里「哪个站点该上哪一年」（`六兆年と一夜物語` nico 2012 / YouTube 2013）。
    """
    found: Dict[str, str] = {}
    for name, value in SONGBOX_PARAM_RE.findall(body):
        key = name.strip().lower()
        for code, station in STATION_DATE_CODES:
            if key not in (code + "_date", code + "date", code + "_time", code + "date_time"):
                continue
            match = DATE_RE.search(value)
            if match and _date_key(match.group(0)) < _date_key(found.get(station, "")):
                found[station] = match.group(0)
    for params in SONGBOX_CARD_RE.findall(body):
        station = _card_site(params)
        if not station:
            continue
        match = DATE_RE.search(params)
        if match and _date_key(match.group(0)) < _date_key(found.get(station, "")):
            found[station] = match.group(0)
    return {station: _iso_date(value) for station, value in found.items()}


def _iso_date(value: str) -> str:
    """`2023年8月4日` / `2023/8/4` → `2023-08-04`（取不到日期时原样返回）。"""
    match = DATE_RE.search(str(value or ""))
    if not match:
        return str(value or "").strip()
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _body_singers(body: str) -> Tuple[str, ...]:
    """一个 Songbox 的 `|演唱 = {{lj|[[歌愛ユキ]]}}、[[重音Teto]]` 里的歌姬名。"""
    match = SINGER_PARAM_RE.search(str(body or ""))
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


def _singers(text: str) -> Tuple[str, ...]:
    """条目里**所有**版本唱过这首歌的歌姬（多版本 / `{{tabs}}` 条目取并集）。"""
    names: List[str] = []
    for body in _songbox_bodies(text):
        for name in _body_singers(body):
            if name not in names:
                names.append(name)
    return tuple(names)


def _body_singer_credits(body: str) -> Dict[str, Dict[str, bool]]:
    """一个 Songbox 的 `|演唱 =` 里每个歌姬的**写法** → `{'plain': 正常写过, 'album': 挂过专辑}`。

    `[[初音ミク]]（投稿）、[[IA]]（IA精选碟）` → `初音ミク: plain` / `IA: album`。
    """
    match = SINGER_PARAM_RE.search(str(body or ""))
    if not match:
        return {}
    credits: Dict[str, Dict[str, bool]] = {}
    for part in re.split(r"[、,，/]", _unwrap(match.group(1))):
        notes = " ".join(re.findall(r"[（(]([^（()）]*)[)）]", part))
        stem = re.sub(r"[（(][^（()）]*[)）]", " ", part)
        names: List[str] = []
        for target, display in WIKI_LINK_RE.findall(stem):
            for candidate in (_unwrap(target), _unwrap(display)):
                value = clean_title(candidate)
                if value and value not in names:
                    names.append(value)
        if not names:
            value = clean_title(stem)
            if value:
                names.append(value)
        for name in names:
            flags = credits.setdefault(name, {"plain": False, "album": False})
            if notes and ALBUM_CREDIT_RE.search(notes):
                flags["album"] = True
            else:
                flags["plain"] = True
    return credits


def _album_only_singers(text: str) -> Tuple[str, ...]:
    """条目里**只挂在精选碟 / 专辑上**的歌姬（`[[IA]]（IA精选碟）`）。

    这样写的歌姬不是这首歌的演唱者（只是专辑里收了她那一版）→ 不算她的曲子
    （用户 2026-10-01：`夜明けと蛍` 的「演唱」是「初音ミク（投稿）、IA（IA精选碟）」，
    被从 `Template:IA/2014` 里删了）。同一个名字在别处**正常写过**就不算。
    """
    merged: Dict[str, Dict[str, bool]] = {}
    for body in _songbox_bodies(text):
        for name, flags in _body_singer_credits(body).items():
            current = merged.setdefault(name, {"plain": False, "album": False})
            current["plain"] = current["plain"] or flags["plain"]
            current["album"] = current["album"] or flags["album"]
    return tuple(name for name, flags in merged.items()
                 if flags["album"] and not flags["plain"])


def song_fact(title: str, text: Optional[str], ja_hint: str = "",
              vocalist: str = "") -> SongFact:
    """从条目正文里读出站点 / 荣誉档 / 日期 / 演唱者。

    `vocalist` 给歌姬名时，多版本（`{{tabs}}`）条目按 `|演唱 =` **挑这位歌姬唱的那一版**：
    年份与站点都用那一版的数据（用户 2026-09-30：`深海(たると)` 要歌爱雪那版的 2015 年，
    不是原版初音未来的 2011 年）。不给歌姬名就用第一个 Songbox。
    """
    fact = SongFact(title=title, exists=text is not None)
    if text is None:
        return fact
    fact.is_song = bool(VOCALIST_SONGBOX_RE.search(text))
    body = _pick_songbox(text, vocalist)
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
    fact.engines = honor_engines(text)
    dates = _dates_from_songbox(body)
    if dates:
        fact.dates = tuple(dict.fromkeys(_iso_date(value)
                                        for value in sorted(dates, key=_date_key)))
        earliest = min(dates, key=_date_key)
        match = DATE_RE.search(earliest)
        if match:
            fact.date = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-" \
                        f"{int(match.group(3)):02d}"
    # 站点 → 投稿年：年份子页 / 回写用的年份子页链接都按**每个站点自己的年**算
    # （`六兆年と一夜物語` nico 2012 / YouTube 2013，见 `STATION_DATE_CODES`）
    fact.station_years = {station: value[:4] for station, value in _station_dates(body).items()
                          if len(value) >= 4 and value[:4].isdigit()}
    fact.ja = ja_hint or _declared_ja(text)
    fact.singers = _singers(text)          # 所有版本（用来核对「分类里是这位歌姬唱的吗」）
    fact.album_singers = _album_only_singers(text)   # 只挂在精选碟 / 专辑上的那些
    fact.multi_version = len(_songbox_bodies(text)) > 1
    # 条目的「== 二次创作 ==」段落里点到这位歌姬了吗（信息框认不出她时靠这条定收不收）
    fact.secondary = secondary_has_singer(text, vocalist)
    # 专辑曲：信息框写了「收录专辑」但**一个投稿 ID 都没有**（实测 `八十八键的宇宙`、
    # `Captain little`：`{{Infobox Song|…|收录专辑=…}}` + 没有 nnd_id / yt_id / bb_id）——
    # 这类歌没有自己的投稿页，模板里不收（用户 2026-10-01）。
    # ⚠️ 专辑也可能只写在**正文**里（`超次元爱歌`：「收录于专辑《未完成エイトビーツ》中」，
    # 原 niconico 投稿已被作者删除）→ 参数与正文两种写法都算（见 `ALBUM_WORD_RE`）。
    fact.album_only = (bool(ALBUM_PARAM_RE.search(text))
                       or bool(ALBUM_WORD_RE.search(text))) and not fact.stations
    return fact


def _card_site(params: str) -> str:
    """`{{…Songbox/card|yt|N8r4bHSqtPU|2022-03-26|…}}` 里的站点。"""
    for part in str(params or "").split("|"):
        station = STATION_ALIASES.get(part.strip().lower(), "")
        if station:
            return station
    return ""


def fetch_song_facts(titles: Sequence[str], progress=None,
                     vocalist: str = "") -> Dict[str, SongFact]:
    """批量抓条目正文并解析成 `SongFact`（每 50 个一批，由 `wiki_api` 负责分批）。

    `vocalist` 会传给 `song_fact()`（多版本条目按 `|演唱 =` 挑这位歌姬那一版）。

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
        fact = song_fact(title, texts.get(title), vocalist=vocalist)
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
    """这一页的 `|演唱 =` 里有没有这位歌姬（殿堂页与分类对不上时的最后一道核对）。

    中文名 / 日文名都认（与 `_pick_songbox()` 用同一套判定）。
    """
    return bool(vocalist) and _singer_matches(_singers(text), str(vocalist))


# ============================================================ 分栏

def classify(work: VocalistWork, hall_entries: Sequence[HallEntry],
             facts: Dict[str, SongFact], titles: Sequence[str]) -> None:
    """把分类里的曲子分到各栏各站点，写进 `work.songs` 与 `work.flags`。

    ⚠️ **歌曲条目自己的荣誉题头优先**（用户 2026-09-30 要求）：条目的
    `|nrank=2` / `|yrank=4` 与殿堂 / 传说 / 神话 / 破亿页面冲突时按**条目**来
    （不再丢待复核，只在备注里写一句）；殿堂页只负责补条目没写的站点。
    另两面：`VOCALOID破亿曲` 是**重定向**（→ `YouTube上播放数量超过1亿的VOCALOID歌曲`），
    站点得从目标标题认，否则 20 条破亿全算成 niconico；**YouTube 的殿堂曲不列**
    （站上殿埂曲栏只有 niconico / bilibili，见 `_drop_youtube_hall()`）。
    一步接一步：殿堂页 → 其他 → 荣誉题头 → 剔 YouTube 殿堂 → 待复核。
    """
    by_title: Dict[str, List[HallEntry]] = {}
    by_ja: Dict[str, List[HallEntry]] = {}
    skipped_albums: List[str] = []
    for entry in hall_entries:
        by_title.setdefault(entry.title, []).append(entry)
        if entry.ja:
            by_ja.setdefault(entry.ja, []).append(entry)

    for title in titles:
        fact = facts.get(title)
        ja = (fact.ja if fact else "") or title
        entries = list(by_title.get(title) or [])
        if not entries:
            # 殿堂页里写的是**裸日文名**（`{{Temple Song|曲目 = {{lj|パラオナボーイ}}*}}`）、
            # 分类里是条目名：按日文名再找一遍。7 首靠这条路找回来（`狐日和` / `な` /
            # `パラオナボーイ` / `夏に咲く` …）。
            #
            # ⚠️ 两条门槛，只认比较可靠的：
            # ① 页面本来就拿日文名当标题（`entry.title == entry.ja`，即`{{lj|パラオナボーイ}}` 那种裸红链）；
            # ② 名字够长（≥ 3 字）。
            # 不挡的话会被短名字撞车：实测 `歌爱雪` 的 `N(take)`（日文名就一个字母 `N`）
            # 会被错配到 `N(水母P)`（另一个人写的另一首歌，日文名恰好也是 `N`）的殿堂记录上，
            # 而站上 `Template:歌爱雪/2012` 是把 `N(take)` 放在「其他」栏的。
            entries = [entry for entry in by_ja.get(ja) or []
                       if entry.title == entry.ja or len(str(entry.ja)) >= MIN_JA_MATCH_LEN]
            if entries and ja != title:
                logging.info("「%s」在殿堂页里是按日文名 %s 找到的", title, ja)
        # 只收在专辑里的曲子不进模板（用户 2026-10-01：`八十八键的宇宙` / `Captain little` /
        # `超次元爱歌`）。⚠️ 判定放在**殿堂页查过之后**：上了殿堂/传说/神话页的歌照样要收
        # （哪怕条目的信息框漏写了投稿 ID，年份还能从殿堂页拿到），只有殿堂页里也没有的
        # 才算「没有自己的投稿页」。
        if fact is not None and fact.album_only and not entries:
            logging.info("「%s」只收在专辑里（没有投稿 ID），不进歌姬模板", title)
            skipped_albums.append(title)
            continue
        song = VocalistSong(title=title, ja=ja, page_exists=bool(fact and fact.exists))
        if fact is not None:
            song.date = fact.date
            song.year = fact.year
            song.years = list(fact.years)      # 跨年的歌（一个站一年）要挂好几张年份子页
        # 翻唱曲：殿堂页里带 `(翻)` 的记录说明这位歌姬翻过这首歌（给她标上 `*`，
        # 用户 2026-10-01）。⚠️ 但**条目「演唱」栏里认不出这位歌姬的翻唱曲不收** ——
        # 那判定要等既有页面合并完才做（要认得「混音版」），见 `prune_cover_mismatch()`。
        # ⚠️ `|演唱 =` 没读到东西时（singers 空）**当认得** —— 解析不出来就别删人家。
        own_version = (not fact or not fact.singers
                       or _singer_matches(fact.singers, work.name))
        # 「演唱」里把她只写在精选碟 / 专辑上（`[[IA]]（IA精选碟）`）→ 她没投过这首歌的稿，
        # 不算她那一版（用户 2026-10-01：`夜明けと蛍` 被从 `Template:IA/2014` 里删了）
        if own_version and fact is not None and fact.album_singers \
                and _singer_matches(fact.album_singers, work.name):
            song.album_credit = True
            own_version = False
        song.own_version = own_version
        # 「演唱」栏里认不出她，但条目的「二次创作」段落里点到她 → 收，链接挂 `#二次创作`
        song.secondary = bool(fact and fact.secondary)
        if song.secondary and not own_version:
            song.anchor = SECONDARY_ANCHOR
        if not own_version and any(entry.cover for entry in entries):
            song.cover = True
        _fill_from_hall(song, entries)
        if not song.places:
            _fill_other(song, fact, entries)
        _apply_honor_header(song, fact)          # ★ 条目自己的荣誉题头优先（用户 2026-09-30）
        # 上标（`<sup>CeVIO</sup>`）：条目荣誉题头里的引擎跟这位歌姬的主引擎不一样就标出来
        # （用户 2026-10-01：拆 `Template:IA` 时那些 CeVIO 版的上标要保留）
        main_engine = str(work.engine or "").strip()
        if main_engine and fact is not None and not fact.multi_version:
            for engine in fact.engines:
                if engine.lower() != main_engine.lower():
                    song.super_engine = song.super_engine or engine
                    break
        _drop_youtube_hall(song, fact, entries)   # ★ YouTube 的殿堂曲不列（用户 2026-09-30）
        _fill_place_years(song, fact, prefer_station_dates=own_version)   # ★ 每（栏,站点）的年
        _flag_song(song, fact)
        if song.flag and len(work.flags) < MAX_FLAGS:
            work.flags.append({"title": song.title, "ja": song.ja, "reason": song.flag,
                               "rank": song.rank, "station": "、".join(song.all_stations),
                               "year": song.year})
        work.songs.append(song)
    if skipped_albums:
        logging.info("只收在专辑里的曲子 %d 首没有写进模板：%s",
                     len(skipped_albums), "、".join(skipped_albums))
        # 也记进「不再收录」那份：这些曲子的条目里要是还留着模板调用，回写时一并删掉
        work.skipped_covers = list(dict.fromkeys([*work.skipped_covers, *skipped_albums]))


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def vocadb_song(name: str) -> Optional[dict]:
    """按名字在 VocaDB 上找一首歌 → `{'name','date','year','favorited','rating','singers','pvs'}`。

    用户 2026-10-01：「遇到红链先在 VocaDB 搜索原始名称并爬取相关数据（包括投稿日期和播放量），
    查不到才放复核」+「殿堂 / 传说 / 神话页里的红链合唱曲，去 VocaDB 查歌姬里有没有这位歌姬」。

    ⚠️ VocaDB 的 song API **不给播放量**：`pvCount` 实测一直是 null，`pvs` 里只有站点 + PV ID
    —— 所以这里带回 `pvs` 的 `(站点, PV ID)`，播放量另去站点上取（见 `play_counts()`）。
    查不到 / 网络失败返回 None。
    """
    query = str(name or "").strip()
    if not query:
        return None
    try:
        response = http_get(VOCADB_SONG_QUERY_URL, use_proxy=True, params={
            "query": query, "lang": "Default", "maxResults": 10, "fields": "Artists,PVs",
            "nameMatchMode": "Auto", "getTotalCount": "false"})
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception as e:
        logging.warning("在 VocaDB 上查「%s」失败：%s", query, e)
        return None
    exact = [item for item in items
             if str(item.get("defaultName") or item.get("name") or "").strip() == query]
    if not exact:
        return None                           # 名字对不上就不硬套（实测搜「無」回来一堆不相干的）
    for item in exact:
        singers = tuple(str(artist.get("name") or "").strip()
                        for artist in item.get("artists") or []
                        if str(artist.get("categories") or "") == "Vocalist"
                        or artist.get("artistType") in vocadb.VOICE_ARTIST_TYPES)
        date = str(item.get("publishDate") or "")[:10]
        return {"name": str(item.get("defaultName") or item.get("name") or query).strip(),
                "date": date if len(date) == 10 else "",
                "year": date[:4] if len(date) >= 4 and date[:4].isdigit() else "",
                "favorited": _int_or_zero(item.get("favoritedTimes")),
                "rating": _int_or_zero(item.get("ratingScore")),
                "singers": tuple(value for value in singers if value),
                "pvs": tuple((str(pv.get("service") or ""), str(pv.get("pvId") or ""))
                             for pv in item.get("pvs") or []),
                "url": f"{VOCADB_SONG_PAGE}{item.get('id')}" if item.get("id") else ""}
    return None


def play_counts(pvs: Sequence[Tuple[str, str]],
                limit: int = MAX_PLAY_COUNT_LOOKUPS) -> List[str]:
    """`[(站点, PV ID)]` → `['nico 1,081,622', 'bilibili 12,345']`（取不到的不出现）。

    用户 2026-10-01 要的「播放量」：VocaDB 的 song API 不给（`pvCount` 恒 null），得按 PV ID
    去站点上取 —— nico 走 `nicolog.fetch()`（nicolog.jp 上最后一次记录的播放量），
    bilibili 走 `source_filler.bilibili_view()` 的 `stat.view`。YouTube 走
    `models.video.get_yt_info()`（解析 watch 页的 WatchAction 播放量）。每个站点只查第一个 PV
    （一个站点给的 PV 再多、播放量也是同一个），最多查 `MAX_PLAY_COUNT_LOOKUPS` 个。
    """
    from utils import source_filler                     # 懒导入：免得与它的依赖绕成环
    from models.video import get_yt_info                # 同理（它是 bs4 + 网络那一层）
    found: List[str] = []
    seen: set = set()
    for entry in list(pvs or ()):
        if len(found) >= max(1, limit):
            break
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        service, pv_id = entry
        identifier = str(pv_id or "").strip()
        if not identifier:
            continue
        # 站点名归一化（VocaDB 写 Youtube / NicoNicoDouga / Bilibili）
        label = ("YouTube" if service.lower().startswith("you")
                 else "nico" if service.lower().startswith("nico") or identifier.startswith("sm")
                 else "bilibili" if (identifier.startswith("BV") or identifier.isdigit())
                 else service)
        if label in seen:
            continue
        seen.add(label)
        try:
            if label == "nico":
                video = nicolog.fetch(identifier)
                views = _int_or_zero(getattr(video, "views", 0)) if video else 0
                if views:
                    found.append(f"nico {views:,}")
            elif label == "YouTube":
                video = get_yt_info(identifier)
                views = _int_or_zero(getattr(video, "views", 0)) if video else 0
                if views:
                    found.append(f"YouTube {views:,}")
            elif identifier.startswith("BV"):
                data = source_filler.bilibili_view(identifier) or {}
                views = _int_or_zero((data.get("stat") or {}).get("view"))
                if views:
                    found.append(f"bilibili {views:,}")
            elif identifier.isdigit():
                # VocaDB 的 Bilibili PV 一半给的是数字 aid（实测 浮遊月光街 = 38760155）
                response = http_get(BILIBILI_VIEW_API.format(identifier),
                                    headers={"Referer": "https://www.bilibili.com/"},
                                    use_proxy=True)
                response.raise_for_status()
                payload = response.json()
                views = _int_or_zero(((payload.get("data") or {}).get("stat") or {})
                                     .get("view")) if payload.get("code") in (0, None) else 0
                if views:
                    found.append(f"bilibili {views:,}")
        except Exception as e:                          # 取不到就当没有，别打断生成流程
            logging.warning("取 PV「%s」的播放量失败：%s", identifier, e)
    return found


def _vocadb_note(found: dict, counts: Sequence[str] = ()) -> str:
    """把 VocaDB 查到的东西写成一句备注（投稿日期 / 播放量 / 收藏 / 评分 / 站点 / 歌姬）。"""
    parts = [f"VocaDB：{found['name']}"]
    if found.get("date"):
        parts.append(f"投稿 {found['date']}")
    if counts:
        parts.append("播放量 " + "、".join(counts))
    if found.get("favorited"):
        parts.append(f"收藏 {found['favorited']}")
    if found.get("rating"):
        parts.append(f"评分 {found['rating']}")
    if found.get("pvs"):
        services = [service for service, _pv in found["pvs"] if service]
        if services:
            parts.append("、".join(dict.fromkeys(services)))
    if found.get("singers"):
        parts.append("歌姬 " + "、".join(found["singers"]))
    return "（" + "；".join(parts) + "）"


def fill_from_vocadb(work: VocalistWork, hall_entries: Sequence[HallEntry] = (),
                     progress=None) -> int:
    """红链的歌去 VocaDB 补数据；红链**合唱曲**按 VocaDB 的歌姬名单决定收不收（用户 2026-10-01）。

    两步：

    * **补投稿年**：连条目都没有（红链）又没有年份的曲子，按原始名去 VocaDB 查投稿日期 ——
      查到就把年份补上（拆分成年份子页时才归得了页），查不到才留在待复核里；
    * **红链合唱曲**：殿堂 / 传说 / 神话页里那些还没建条目、歌手栏里带这位歌姬的条目，去 VocaDB
      查歌姬名单，名单里确实有她才补进模板（合唱曲的名单不能只看殿堂页的 `|歌手 =`，实测
      歌爱雪那 97 张页里「合唱带她」的 51 条很多是别人翻的、只是合唱里带了她一句）。

    返回补进来的合唱曲条数；`progress` 用来给界面报进度。
    """
    name = str(work.name or "").strip()
    added = 0
    # ① 红链 + 没年份 → VocaDB 补投稿年
    for song in work.songs:
        if song.year or song.page_exists:
            continue
        if progress is not None:
            progress(f"正在 VocaDB 上查红链「{song.title or song.ja}」…")
        found = vocadb_song(song.ja or song.title)
        if not found:
            continue
        if found.get("singers") and name and not _singer_matches(found["singers"], name):
            continue                          # VocaDB 上这首歌不是这位歌姬唱的 → 数据八成不是它
        if found.get("year"):
            song.year = found["year"]
            song.date = found["date"] or song.date
            song.years = [found["year"]]
        counts = play_counts(found.get("pvs") or ())
        song.note = "；".join(filter(None, [song.note, _vocadb_note(found, counts)]))
        if song.year and "取不到投稿年" in str(song.flag):
            song.flag = "；".join(part for part in str(song.flag).split("；")
                                 if "取不到投稿年" not in part)
            for flag in work.flags:
                if flag.get("title") == song.title and flag.get("ja") == song.ja:
                    flag["year"] = song.year
                    flag["reason"] = song.flag
                    break
        logging.info("红链「%s」在 VocaDB 上补到了投稿年 %s", song.title or song.ja, song.year)
    # ② 红链合唱曲 → VocaDB 歌姬名单里有她才收
    if not name:
        return added
    known = {str(song.title or "").strip() for song in work.songs}
    known |= {str(song.ja or "").strip() for song in work.songs}
    buckets: Dict[str, List[HallEntry]] = {}
    for entry in hall_entries:
        if name not in _hall_singers(entry.singer):
            continue
        if entry.title in known or (entry.ja and entry.ja in known):
            continue
        buckets.setdefault(entry.title, []).append(entry)
    if not buckets:
        return added
    if progress is not None:
        progress(f"正在核 {len(buckets)} 首红链合唱曲的歌姬名单…")
    missing = wiki_api.pages_exist(list(buckets))
    for title, entries in buckets.items():
        if missing.get(title, False):
            continue                          # 条目已经有了 —— 由分类那条路管
        found = vocadb_song(entries[0].ja or title)
        if not found or not _singer_matches(found.get("singers") or (), name):
            continue                          # VocaDB 上歌姬名单里没有这位歌姬
        song = VocalistSong(title=title, ja=entries[0].ja or title,
                            year=found.get("year") or entries[0].year,
                            date=found.get("date", ""), page_exists=False,
                            source="殿堂页（合唱，VocaDB 核对）")
        _fill_from_hall(song, entries)
        if found.get("year") and "取不到投稿年" in str(song.note):
            song.note = song.note.replace("；取不到投稿年", "")
        song.note = "；".join(filter(None, [
            f"殿堂页里写着「歌手 = {entries[0].singer}」但条目还没建（红链）；"
            f"VocaDB 歌姬名单里有 {name}，按原样补进来", song.note,
            _vocadb_note(found, play_counts(found.get("pvs") or ()))]))
        song.flag = song.note + ("" if song.year else "；取不到投稿年（拆分成年份子页时要你指定）")
        work.songs.append(song)
        added += 1
        if len(work.flags) < MAX_FLAGS:
            work.flags.append({"title": song.title, "ja": song.ja, "reason": song.flag,
                               "rank": song.rank, "station": "、".join(song.all_stations),
                               "year": song.year})
    if added:
        logging.info("红链合唱曲里 VocaDB 确认有 %s 的 %d 首，已补进模板", name, added)
        work.summary = "；".join(filter(None, [work.summary, f"VocaDB 补了 {added} 首红链合唱曲"]))
    return added


def _hall_singers(value: str) -> Tuple[str, ...]:
    """殿堂页 `|歌手 = …` 里的歌姬名（可能是「歌爱雪、Poyoroid」这种一串）。"""
    text = LJ_RE.sub(r"\1", str(value or ""))
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)     # `[[A|B]]` → 显示名 B
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    return tuple(part.strip() for part in re.split(r"[、,，/&＋+]", text) if part.strip())


def _fill_from_hall(song: VocalistSong, entries: Sequence[HallEntry],
                    place_entries: Optional[Sequence[HallEntry]] = None) -> None:
    """按殿堂页定分栏：**每个站点各拿自己在那边最高的档**，都记进 `places`。

    `place_entries` 给定时用它算栏 / 站点 / 年份（默认就是 `entries`）：
    **翻唱曲**要传「带 `(翻)` 的那几条」—— 同一条目名在殿堂页里往往既有**原曲**的记录
    （实测 `magnet` 的 2009 传说曲/niconico）又有**一堆翻唱版**的记录（2010~2013 殿堂曲），
    歌姬模板里该用的是翻唱那条（用户 2026-10-01：站上 `Template:IA/2012` 写的就是
    `殿堂曲/niconico` 的 `[[magnet]]*`）。
    """
    if not entries:
        return
    source = list(place_entries if place_entries is not None else entries)
    if not source:
        source = list(entries)
    levels: Dict[str, int] = {}
    years: Dict[int, str] = {}
    pages: Dict[int, str] = {}
    # 站点 → (最高的那一档, 那一档所在年份页给的投稿年)：同一站在好几个年份页里出现过时，
    # 按**最高档**那一页的年份算（实测 `六兆年と一夜物語`：niconico 只出现在
    # `VOCALOID传说曲/2012年投稿` → 2012；YouTube 只出现在
    # `VOCALOID传说曲/YouTube投稿/2013年投稿` → 2013）。
    best: Dict[str, Tuple[int, str]] = {}
    for entry in source:
        level = entry.level
        levels[entry.station] = max(levels.get(entry.station, 0), level)
        # 同一档可能出现在好几个年份页里（重投 / 达成时间不同）：取**最早**那一年
        if entry.year and (level not in years or entry.year < years[level]):
            years[level] = entry.year
        pages.setdefault(level, entry.page)
        own = str(entry.year or "")
        current = best.get(entry.station)
        if current is None or level > current[0] or (level == current[0] and own
                                                     and (not current[1] or own < current[1])):
            best[entry.station] = (level, own)
    places: List[Tuple[str, str]] = []
    for station in STATIONS:
        if levels.get(station):
            places.append((LEVEL_TITLES.get(levels[station], RANK_HALL), station))
    places.sort(key=lambda item: (-TITLE_LEVELS.get(item[0], 1), STATIONS.index(item[1])))
    song.places = places
    # 每个（栏, 站点）记下**它自己那一年的殿堂页**给的投稿年
    for station, (level, own) in best.items():
        if own:
            song.place_years[(LEVEL_TITLES.get(level, RANK_HALL), station)] = own
    top = max(levels.values())
    if not song.year and years.get(top):
        song.year = years[top]
    song.source = f"殿堂页：{pages.get(top, '')}"
    song.note = "档次 " + "/".join(f"{station}={levels[station]}"
                                   for station in STATIONS if station in levels)
    # 「无法收录」与「翻唱」两件事在殿堂页上都有记号（`{{假链|…}}` / `(翻)`）：
    # 前者照搬（只写日文名、不给链接）；后者**只认那条假链**里的 `(翻)` —— 殿堂页里的
    # 同名条目多半是**别的歌姬**翻的（实测 `强风大背头` 的殿堂页里有 KAITO / 音街鳗 /
    # 初音未来 好几个翻版，都带 `(翻)`），拿它们当「这首歌是翻唱」是错的。
    # 我们自己的翻唱记号以既有模板里的 `*` 为准（见 `_merge_existing_songs()`）。
    if any(entry.unlinked for entry in entries):
        song.unlinked = True
        song.ja = next((entry.ja for entry in entries if entry.unlinked and entry.ja),
                       song.ja)
        song.note = "；".join(filter(None, [song.note,
                                            "殿堂页写的是 `{{假链}}`（无法收录），"
                                            "按站上写法只写日文名、不给链接"]))
        if any(entry.cover for entry in entries):
            song.cover = True
            song.note = "；".join(filter(None, [song.note, "站上标了「翻唱」，曲目后面要写 `*`"]))


def _fill_other(song: VocalistSong, fact: Optional[SongFact],
                entries: Sequence[HallEntry]) -> None:
    """其他栏：站点看歌曲条目里的投稿 ID；只有 YouTube 的算「部分YouTube投稿」。

    条目里写了荣誉题头的那种由 `_apply_honor_header()` 接手（会从「其他」里拿出来）。
    """
    stations = list(fact.stations) if fact else []
    if not stations and entries:
        stations = list(dict.fromkeys(entry.station for entry in entries))
    song.places = [(RANK_OTHER, station) for station in STATIONS if station in stations]
    song.kind = OTHER_YOUTUBE if stations == [STATION_YOUTUBE] else OTHER_UNHALL
    song.source = "分类"


def _levels_of(fact: Optional[SongFact]) -> Dict[str, int]:
    """条目里的荣誉题头 → `{站点: 档}`（只留 ≥1 的）。"""
    if fact is None:
        return {}
    return {station: level for station, level in (fact.ranks or {}).items() if level >= 1}


def _apply_honor_header(song: VocalistSong, fact: Optional[SongFact]) -> None:
    """把条目荣誉题头里的档并进分栏 —— **条目优先**（用户 2026-09-30 要求）。

    两件事：

    * 与殿堂 / 传说 / 神话 / 破亿页面冲突时，按条目里的档写栏（原来会丢进待复核）；
    * 殿堂页里没有的档也能定出来：`VOCALOID破亿曲` 其实是个重定向
      （→ `YouTube上播放数量超过1亿的VOCALOID歌曲`，所以破亿页只当 YouTube 的），
      条目里的 `|nrank=4` / `|yrank=4` / `|brank=4` 也算一路
      （实测 `Template:重音Teto/2024` 的「破亿播放曲目」栏就是这么来的）。

    条目没写的站点保留殿堂页给的档；两边都没有的站点不动（还是「其他」）。
    差异写进 `song.note`（曲目页的「备注」列看得到），**不**算待复核。
    """
    levels = _levels_of(fact)
    if not levels:
        return
    hall_levels = {station: TITLE_LEVELS.get(rank, 1) for rank, station in song.places
                   if rank != RANK_OTHER}
    merged = {**hall_levels,
              **{station: level for station, level in levels.items() if station in STATIONS}}
    if not merged:
        return
    notes: List[str] = []
    for station in STATIONS:
        if station not in levels:
            continue
        level = levels[station]
        if station in hall_levels and hall_levels[station] != level:
            notes.append(f"{station}：条目写第 {level} 档、殿堂页算第 "
                         f"{hall_levels[station]} 档，按条目")
        elif station not in hall_levels:
            notes.append(f"{station}：第 {level} 档只有条目里写着（殿堂页里没有）")
    places = [(LEVEL_TITLES.get(level, RANK_HALL), station)
              for station, level in merged.items()]
    places.sort(key=lambda item: (-TITLE_LEVELS.get(item[0], 1), STATIONS.index(item[1])))
    song.places = places
    if any(rank != RANK_OTHER for rank, _station in places):
        song.kind = ""                       # 有荣誉栏就不算「其他」的子栏了
    if not hall_levels:
        song.source = "荣誉题头"              # 整个栏都是条目给的（殿堂页里没有）
    if notes:
        song.note = "；".join(filter(None, [song.note, *notes]))


def _drop_youtube_hall(song: VocalistSong, fact: Optional[SongFact],
                       entries: Sequence[HallEntry]) -> None:
    """**YouTube 上的殿堂曲不写进模板**（用户 2026-09-30）。

    站上就是这么定的：`VOCALOID殿堂曲/YouTube投稿` 页面写着「YouTube上播放数量超过
    10万的VOCALOID歌曲过于众多，难以对其全数收录及维护更新，故本站不罗列此列表」，
    所以站上的「殿堂曲」栏只有 niconico 与 bilibili（实测 `Template:重音Teto/2025` /
    `Template:Flower/2020` / `Template:歌爱雪`）；YouTube 从**传说曲**（100 万）起才列。

    别的站点还在时，歌照旧留在殿堂曲（niconico / bilibili）里；只剩 YouTube 这一个时
    整个分栏就空了 → 退回「其他」（YouTube 独占的算「部分YouTube投稿」）。
    """
    kept = [place for place in song.places if place != (RANK_HALL, STATION_YOUTUBE)]
    if len(kept) == len(song.places):
        return
    song.places = kept
    if kept:
        song.note = "；".join(filter(None, [song.note,
                                           "YouTube 上的殿堂曲不在模板里列（站上惯例）"]))
        return
    source = song.source
    _fill_other(song, fact, entries)
    song.source = f"{source}（YouTube 殿堂不列）" if source else song.source
    song.note = "；".join(filter(None, [song.note,
                                       "YouTube 上的殿堂曲不在模板里列，这条归入「其他」"]))


def _fill_place_years(song: VocalistSong, fact: Optional[SongFact],
                      prefer_station_dates: bool = False) -> None:
    """给每个（栏, 站点）补上它自己的投稿年。

    优先级：
    1. `prefer_station_dates=True`（**条目里就是这位歌姬那一版**）→ 用条目里该站的投稿年
       （实测 `鸟之诗`：条目写 nico 2021，殿堂页里却拄着 2007 / 2013 那几条别的版本）；
    2. 殿堂页里带 `(翻)` 的记录给的年份（翻唱曲就靠它）；
    3. 信息框里该站点的日期；都没有就留空 —— `places_in()` 会把「不知道哪一年」的位置
       只放在最早那一年，不跨年重复。
    """
    for place in song.places:
        station = place[1]
        own = str((fact.station_years or {}).get(station, "")) if (prefer_station_dates
                                                                  and fact) else ""
        own = own or song.place_years.get(place, "")
        # 同一个站点在别的栏里已经记过年份（换栏 / 档次变了）就沿用
        own = own or next((value for (rank, site), value in song.place_years.items()
                           if site == station and value), "")
        own = own or str((fact.station_years or {}).get(station, "") if fact else "")
        if own:
            song.place_years[place] = str(own)


def _flag_song(song: VocalistSong, fact: Optional[SongFact]) -> None:
    """该人工复核的挑出来（用户 2026-09-30 要求：拿不准的弹窗复核）。

    ⚠️ 「条目与殿堂页对不上」**不再**算待复核：用户 2026-09-30 定了「以歌曲页面本身数据为准」，
    冲突已经按条目解决，差异只写进备注（见 `_apply_honor_header()`）。
    """
    reasons: List[str] = []
    if fact is None:
        reasons.append("分类里写着这首歌，但没能读到它的条目")
    else:
        if not fact.exists:
            reasons.append("条目不存在（红链）")
        elif not fact.is_song:
            reasons.append("这个页面看着不是歌曲条目（没有信息框）")
        if song.rank == RANK_OTHER and not _levels_of(fact) and not fact.stations:
            reasons.append("条目里既没有投稿 ID 也没有荣誉题头，站点与栏都定不下来")
    if not song.year:
        reasons.append("取不到投稿年（拆分成年份子页时要你指定）")
    song.flag = "；".join(dict.fromkeys(reasons))


# ============================================================ 抓全流程

def prepare_work(name: str, split: bool = False, progress=None,
                 max_songs: int = MAX_SONGS, subpages_only: bool = False,
                 other_years: bool = False, vocadb: bool = True) -> VocalistWork:
    """抓一位歌姬的全部素材 → `VocalistWork`（分类 + 殿堂页 + 歌曲条目）。

    `subpages_only=True`（**只新建年份子页、不动既有主模板**，用户 2026-09-30 要求）只是
    给工程打个标记，抓取口径不变：`page_specs()` 会只生成年份子页。
    `other_years=True` 也一样只是个排版开关：「其他」栏按年份分层而不是平铺（见 `_other_value()`）。
    `vocadb=False` 可以关掉最后那一步「红链上 VocaDB 查数据 / 核合唱曲歌姬名单」（见
    `fill_from_vocadb()`）—— 单测里就该关掉它（那边会碰真网络）。
    """
    name = str(name or "").strip()
    work = VocalistWork(name=name, engine=get_engine(name), split=bool(split),
                        subpages_only=bool(subpages_only), other_years=bool(other_years))
    category = f"Category:{name}歌曲"
    if progress is not None:
        progress(f"正在读分类 {category} …")
    titles = fetch_category_titles(category, max_songs + 1, progress)
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
        raise ValueError(f"分类「{category}」里有 {len(titles)} 首以上的曲子，"
                         f"超过上限 {max_songs} —— 这个歌姬怕不是被当成了整个系列"
                         "（要先手工把分类拆一遍）")
    logging.info("分类 %s 有 %d 首曲子", category, len(titles))
    if progress is not None:
        progress(f"分类里有 {len(titles)} 首曲子；正在读 {work.engine} 的殿堂 / 传说 / 神话页面…")
    hall_entries, hall_pages = fetch_halls(work.engine, progress)
    if progress is not None:
        progress("正在读歌曲条目（站点、荣誉题头、日文名）…")
    facts = fetch_song_facts(titles, progress, vocalist=name)
    classify(work, hall_entries, facts, titles)
    if vocadb:
        # 红链：去 VocaDB 补投稿年 / 核红链合唱曲的歌姬名单（用户 2026-10-01）
        fill_from_vocadb(work, hall_entries, progress)
    work.summary = (f"{len(titles)} 首曲子 · 殿堂页 {len(hall_pages)} 个 · "
                    f"{len(hall_entries)} 条殿堂记录 · {len(work.flags)} 条待复核")
    logging.info("「%s」：%s", name, work.summary)
    return work


def _clean_label(label: str) -> str:
    """分组小标签的纯文字：`其他{{注||收录Vocawiki已有条目。}}` → `其他`。

    `{{mousetext|部分未殿堂曲|指niconico及bilibili投稿}}`（站上「其他」栏的两个子栏）也要认。
    """
    value = str(label or "").strip()
    moused = re.match(r"^\{\{\s*mousetext\s*\|([^|}]*)(?:\|[^{}]*)?\}\}$", value, re.IGNORECASE)
    if moused:
        value = moused.group(1)
    value = clean_title(re.sub(r"\{\{[^{}]*\}\}", "", value))
    return value.strip()


def _entry_placement(labels: Sequence[str], default_year: str = "") -> Dict[str, str]:
    """一串分组标签 → 曲目的位置（`栏 / 站点 / 年份 / 其他栏的子栏`）。

    站上写法是 `歌曲 → 栏 → 站点 → 年份`（不拆分的模板）或 `栏 → 站点`（年份子页里年份
    看页面标题）；「其他」栏有时平铺（`其他 → 部分YouTube投稿 → 曲目`）—— 那种没有年份，
    站点按子栏算（`部分YouTube投稿` 就是 YouTube）。
    """
    rank = station = kind = ""
    year = str(default_year or "")
    for index, label in enumerate(labels):
        name = _clean_label(label)
        if not name:
            continue
        year_match = YEAR_GROUP_RE.match(name)
        if year_match:
            # 年份只在**最内层**标签上才算数：旧模板里有行缩进混了制表符（实测
            # `\t\t\t   |group5 = 2021年`），不挡的话这个年份会一直赖在栈里，后面
            # 「其他」欄（本来就平铺、没年份）的曲子会被错安到 2021 年上。
            if index == len(labels) - 1:
                year = year_match.group(1)
            continue
        if name in STATIONS:
            station = name
            continue
        if name.lower() in STATION_ALIASES:
            station = STATION_ALIASES[name.lower()]
            continue
        if name in (OTHER_UNHALL, OTHER_YOUTUBE):
            kind = name
            continue
        if name in TITLE_LEVELS or name == RANK_OTHER:
            rank = name
            continue
    if not station:
        station = STATION_YOUTUBE if kind == OTHER_YOUTUBE else STATION_NICO
    return {"rank": rank or RANK_OTHER, "station": station, "kind": kind, "year": year}


def _entry_item(part: str) -> Optional[Dict[str, object]]:
    """既有页面里的**一个曲目项** → `{title, ja, cover, unlinked, comment}`。

    三种写法都认（都是站上真实存在的）：

    * `[[凤仙花|{{lj|鳳仙花}}]]*` —— 有条目 + 翻唱记号；
    * `[[Five Nights At Freddy's Song]]*` —— 红链（条目还没建）；
    * `{{lj|パラオナボーイ}}*` —— 站上按「无法收录」处理：只写日文名、不给链接。
    """
    raw = str(part or "").strip()
    comment = ""
    found = TEMPLATE_COMMENT_RE.search(raw)          # 人工写的说明（`… <!-- Paraona Boy，…-->`）
    if found:
        comment = re.sub(r"\s+", " ", found.group(1)).strip()
        if re.fullmatch(r"\d{1,4}[-\d\s:.月日年]*", comment or ""):
            comment = ""                             # 只是投稿时间（`10-27 19:00`）不算说明
        raw = TEMPLATE_COMMENT_RE.sub("", raw).strip()
    # 曲目一条一行时，分隔符 `{{W}}<!--` / `-->` 会把注释拆成两半：把残片剥掉
    raw = re.sub(r"^-->\s*", "", raw)
    raw = re.sub(r"\s*<!--$", "", raw).strip()
    if not raw or YEAR_CALL_RE.match(raw):           # 主模板里的「年份转接行」不是曲目
        return None
    # 站上有手写的 HTML 表头 / 折叠开关混在曲目行里（实测 `Template:IA` 的
    # `<tr><th class="mw-customtoggle-1 navbox-title" …>其他歌曲<sub>(点击展开)</sub></th></tr>`）——
    # 那不是曲子，别拿它去补一条无名曲目（用户 2026-10-01 在人工复核里看到的就是它）。
    if re.search(r"</?(?:tr|th|td|table|div|span|caption)\b", raw, re.IGNORECASE):
        return None
    cover = bool(COVER_MARK_RE.search(raw)) or raw.endswith(COVER_MARK)
    if cover:
        raw = raw.rstrip(COVER_MARK).rstrip()
    links = WIKI_LINK_RE.findall(raw)
    anchor = ""
    if links:
        title = clean_title(links[0][0])
        # `[[胸部××××#二次创作|胸部××××]]`：锚点单独存（写回去时还要用，别把 `#` 当条目名）
        title, anchor = _split_anchor(title)
        ja = clean_title(links[0][1])
    else:
        title = ""
        ja = clean_title(re.sub(r"[{}]", "", LJ_OPEN_RE.sub("", raw)))
    if not title and not ja:
        return None
    if not re.search(r"[0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]", title or ja):
        return None                                  # 只剩引号 / 标点的（`''`）也不要
    sup = SUP_MARK_RE.search(raw)
    return {"title": title, "ja": ja, "cover": cover, "unlinked": not title,
            "anchor": anchor,
            "engine": clean_title(sup.group(1)) if sup else "", "comment": comment}


def _split_anchor(value: str) -> Tuple[str, str]:
    """`胸部××××#二次创作` → `('胸部××××', '二次创作')`（没有锚点时第二个是空串）。

    站上把翻唱 / 翻调版写在条目的「== 二次创作 ==」段落里，链接就挂这个锚点
    （用户 2026-10-01 拿 `Template:IA/2023` 的 255002 指出来）。
    """
    text = str(value or "").strip()
    title, sep, anchor = text.partition("#")
    return (title.strip(), anchor.strip()) if sep else (text, "")


def _expand_links_templates(value: str) -> str:
    """把 `{{Links|条目{{!}}日文|条目2}}` 摊成 `[[条目|日文]] • [[条目2]]`。

    站上用它把一长串曲目打包在一起（实测 `Template:IA` 的
    `|list8 = {{lj|{{Links|Superhero(Guiano){{!}}スーパーヒーロー|赎罪(Kasamura Tota){{!}}贖罪}}}}`）
    —— 不摊开的话整串会被当**一首**曲子，于是它又认不出、又跟分类那边的同一首歌对不上号
    （用户 2026-10-01 报的「`六兆年と一夜物語` 人工判断了多次」就是这么来的）。
    """
    text = str(value or "")
    for _ in range(20):                               # 最多摊 20 个，防意外死循环
        match = LINKS_TEMPLATE_RE.search(text)
        if not match:
            return text
        call = _balanced_call(text, match.start())
        body = call[call.find("|") + 1:-2] if "|" in call else ""
        placeholder = "\x00"                          # `{{!}}` = 转义竖线，先藏起来再切参数
        items: List[str] = []
        for chunk in body.replace("{{!}}", placeholder).split("|"):
            parts = [clean_title(part) for part in chunk.split(placeholder)]
            name = parts[0] if parts else ""
            ja = parts[1] if len(parts) > 1 else ""
            if name:
                items.append(f"[[{name}|{ja}]]" if ja and ja != name else f"[[{name}]]")
        text = text[:match.start()] + (" • ".join(items) or body) + text[len(call) + match.start():]
    return text


def _has_open_comment(text: str) -> bool:
    """`<!--` 比 `-->` 多 = 注释还开着 → 下一行是它的续行（曲目一条一行时都是这样接的）。"""
    value = str(text or "")
    return value.count("<!--") > value.count("-->")


def _entries_of(value: str, labels: Sequence[str],
                default_year: str = "") -> List[Dict[str, object]]:
    """一条 `|listN = …`（含它的续行） → 里面的曲目。"""
    placement = _entry_placement(labels, default_year)
    found: List[Dict[str, object]] = []
    for part in re.split(r"\{\{W\}\}|\s*•\s*", _expand_links_templates(value)):
        text = re.sub(r"^-->\s*", "", str(part or "").strip())
        only_comment = TEMPLATE_COMMENT_RE.fullmatch(text)
        if only_comment:
            # `• <!-- 说明 -->`：人工写的说明挂在**前一**首曲子后面（站上写法就是这样，实测
            # 旧 `Template:歌爱雪` 的 `{{lj|パラオナボーイ}}* • <!-- Paraona Boy，…-->`）
            note = re.sub(r"\s+", " ", only_comment.group(1)).strip()
            if found and note and not found[-1]["comment"]:
                found[-1]["comment"] = note
            continue
        item = _entry_item(text)
        if item is not None:
            found.append({**item, **placement})
    return found


def template_song_entries(text: str, default_year: str = "") -> List[Dict[str, object]]:
    """既有模板 / 年份子页里列到的**曲目** → 一串条目（含翻唱与红链）。

    为什么不用 `template_links()`：那个只认 `[[条目]]`，红链与「只写日文名」的曲子
    （`{{lj|パラオナボーイ}}*`）认不出来，而这两类正是按分类重建时会丢掉的东西。
    这里按**缩进**认层级（站上写法是 `歌曲 → 栏 → 站点 → 年份`，缩进每层深 4~9 格）：
    `|groupN = …` 那行记下标签，叶子 `|listN = …`（值里不再套子分块）
    就是曲目行，它的位置由外层标签决定（`_entry_placement()`）。

    ⚠️ 曲目**一条一行**，从第二行起是 `-->[[X]] • <!--` 这种续行（不带参数名）——
    必须把它们接回上一条 `|listN`，不然每一条 list 只能读到第一首曲子
    （用户 2026-10-01 报的「原模板中的红链也消失不见」就是卡在这里）。

    `default_year` 给年份子页用（年份看页面标题，页里只有 `栏 → 站点` 两层）。
    """
    levels: Dict[int, str] = {}
    entries: List[Dict[str, object]] = []
    pending: Optional[Tuple[str, List[str]]] = None         # 正在读的那一条曲目行
    for raw_line in str(text or "").split("\n"):
        # 缩进混用制表符的行按 8 列展开再量（实测旧 `Template:歌爱雪` 里有一行是
        # `\t\t\t   |group5 = 2021年`，不展开的话它看着比同级标签浅 3 级）
        line = raw_line.expandtabs(8)
        # 续行：上一段还「开着注释」（`…{{W}}<!--`）就是同一列曲目。
        # 站上有两种写法：`-->[[曲]]`（我们生成的、旧 `Template:歌爱雪`）与
        # `10-27 19:00 -->[[曲]]`（日期写在注释里，实测 `Template:IA`）—— 所以不能只看行首。
        if pending is not None and _has_open_comment(pending[0]):
            pending = (pending[0] + " " + line.strip(), pending[1])
            continue
        if pending is not None:
            entries.extend(_entries_of(pending[0], pending[1], default_year))
            pending = None
        group = TEMPLATE_GROUP_RE.match(line)
        if group:
            indent = len(group.group(1))
            levels = {key: value for key, value in levels.items() if key < indent}
            levels[indent] = group.group(2).strip()
            continue
        value = TEMPLATE_LIST_RE.match(line)
        if not value:
            continue
        if SUBGROUP_RE.search(value.group(2)):
            continue                       # 这一层下面是子分组，曲目在更深的那层
        labels = [_clean_label(label) for key, label
                  in sorted(levels.items()) if key <= len(value.group(1))]
        if any(label in NON_SONG_SECTIONS for label in labels):
            continue                       # 「相关人物」那一栏里全是人物名
        if not any(label == "歌曲" or label in TITLE_LEVELS or label == RANK_OTHER
                   for label in labels):
            continue                       # 不在「歌曲」栏里（专辑 / 其他作品之类）
        pending = (value.group(2), labels)
    if pending is not None:
        entries.extend(_entries_of(pending[0], pending[1], default_year))
    return entries


MERGED_YEAR_RE = re.compile(r"^(\d{4})及以前$")


def template_family_pages(name: str) -> List[str]:
    """**联网查**这个歌姬的模板子页名（`Template:<歌姬>/*`）→ 完整标题表。

    为什么要它（用户 2026-10-03：「歌姬模板的识别新增联网搜索功能，这样不用新增 / 拆分后
    一个个手动改」）：站上不是每位歌姬都一年一页 —— 实测 `Template:可不/2021及以前`
    把早年的曲子并在一张子页里（里面按年分小栏），其他年份才是一年一页。
    拿一次 `list=allpages&appprefix=` 就能把真相拿回来，不用猜。

    查不到（没登录 / 网络失败）返回空表，调用方退回「一年一页」。
    """
    query = str(name or "").strip()
    if not query:
        return []
    try:
        response = login.get_api_session().get(wiki_api.api_url(), params={
            "action": "query", "list": "allpages", "apprefix": f"{query}/", "apnamespace": "10",
            "aplimit": "200", "format": "json", "formatversion": "2"},
            timeout=wiki_api.REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:                       # noqa: BLE001 - 查不到就当没有
        logging.warning("查模板子页失败（%s）：%s", query, e)
        return []
    return [str(item.get("title") or "")
            for item in (payload.get("query") or {}).get("allpages") or []]


def merge_until_from_pages(name: str, pages: Sequence[str]) -> str:
    """从子页名里认出**合并子页**（`Template:可不/2021及以前`）→ `"2021"`。

    有的话，≤ 2021 的年份全部归到这一页（见 `VocalistWork.merge_until`）。
    多张（罕见）就取最早那一张。
    """
    prefix = f"{TEMPLATE_PREFIX}{str(name or '').strip()}/"
    found: List[str] = []
    for page in pages or ():
        title = str(page)
        if not title.startswith(prefix):
            continue
        match = MERGED_YEAR_RE.match(title[len(prefix):])
        if match:
            found.append(match.group(1))
    return min(found) if found else ""


def load_existing(work: VocalistWork) -> VocalistWork:
    """读既有的模板、文档页与年份子页：**继承样式 / 「相关人物」那一栏 / 年份子页的写法**，
    并把既有页面里列到、而我们按分类抓不到的曲子（翻唱 / 红链）搬过来。

    ⚠️ 继承时**不要**掺进 `DEFAULT_STYLES`（那是给新建模板用的）：既有模板只写了
    `|groupstyle = background:#f38286`（字色用默认黑），把默认的 `#ffffff` 带进来
    就会白字粉底跟站上不一样。
    """
    title = work.template_title
    # 联网认一下这位歌姬的年份子页名（站上可能有 `2021及以前` 这种合并页）——
    # 认不出来就当作一年一页（用户 2026-10-03）
    work.merge_until = work.merge_until or merge_until_from_pages(
        work.name, template_family_pages(work.name))
    if work.merge_until:
        logging.info("站上 %s 有合并子页 `%s及以前`：≤ %s 的年份都写进那一页",
                     work.name, work.merge_until, work.merge_until)
    years = work.years() if work.split else []
    wanted = [title, f"{title}/doc"] + [f"{title}/{work.year_suffix(year)}" for year in years]
    texts = wiki_api.fetch_pages_text(wanted)
    work.existing = texts.get(title) or ""
    work.existing_doc = texts.get(f"{title}/doc") or ""
    if work.existing:
        work.styles = parse_styles(work.existing)
        work.relation = extract_relation(work.existing)
        # 「演唱会」「官方专辑」这类栏（用户 2026-10-01）：原样搬过来，拆分时也不能丢
        work.extra_groups = [(title, block) for title in EXTRA_GROUP_TITLES
                             if (block := extract_group_value(work.existing, title))]
        logging.info("已继承既有模板 %s 的样式（%s）与「相关人物」栏（正文 %d 字）",
                     title, work.styles or "无", len(work.existing))
    _load_existing_year(work, texts, years)
    _merge_existing_songs(work, texts, years)
    prune_cover_mismatch(work)
    return work


def _load_existing_year(work: VocalistWork, texts: Dict[str, str],
                        years: Sequence[str]) -> None:
    """把这位歌姬**已有的一张年份子页**读回来（拿来对齐标题空格 / `|abovestyle`）。

    只看最早那几年里第一张存在的：站上这两种写法不统一，实测 50 张年份子页里
    `标题}}年`（不带空格）与 `标题}} 年`（带空格）各占一半、38 张写了 `|abovestyle` 12 张没写
    —— 既然歌手之间的习惯不一样，就跟着他自己已有的子页走（用户 2026-09-30 给的两个修订里
    两页都是「不带空格 + 无 abovestyle」）。取不到就不管，用默认写法。
    """
    for year in years:
        text = texts.get(f"{work.template_title}/{work.year_suffix(year)}")
        if text:
            work.existing_year = text
            logging.info("已参照既有年份子页 %s/%s 的写法（标题空格：%s；abovestyle：%s）",
                         work.template_title, work.year_suffix(year),
                         _year_title_joiner(work) or "无",
                         _year_above_style(work) or "无")
            return


def _previous_template_songs(work: VocalistWork) -> Tuple[str, str]:
    """主模板已经拆成「年份转接行」时，往前翻最近的**带曲目名单**的那一版（用户 2026-10-01）。

    实测 `Template:歌爱雪`：手写版（revid 251675）里列着 10 条红链（`Castle on a Cloud` /
    `Five Nights At Freddy's Song` / `パラオナボーイ` …）与 3 个翻唱记号；拆成年份子页之后
    主模板只剩转接行，年份子页里又是我们按分类生成的名单 —— 那批红链与翻唱就这么没了。
    所以这里往前翻 `PREVIOUS_TEMPLATE_LIMIT` 版，拿**最近一版有曲目名单的**当「原模板」：
    补进来的每一条都会写进待复核（用户能逐条删），不会悄悄改掉现在的页面。

    返回 `(正文, 一句话说明)`；翻不到就返回空串。
    """
    revisions = wiki_api.recent_revision_texts(work.template_title,
                                              limit=PREVIOUS_TEMPLATE_LIMIT)
    for revision in revisions:
        content = str(revision.get("content") or "")
        if not content or content == work.existing:
            continue
        if not template_song_entries(content):
            continue                       # 这一版也没有曲目名单（比如也是拆分后的）
        logging.info("主模板里没有曲目名单，改用第 %s 版（%s，%s）当「原模板」",
                     revision.get("revid"), revision.get("timestamp"), revision.get("user"))
        return content, (f"主模板已是拆分后的形态，红链 / 翻唱名单取自更早的一版"
                         f"（revid {revision.get('revid')}，{revision.get('timestamp')}）")
    return "", ""


def _adopt_existing_places(song: VocalistSong, places: Sequence[Tuple[str, str, str]]) -> None:
    """翻唱曲：位置（栏 / 站点 / 年份）改用**既有页面里写的那几条**。

    为什么单翻唱这么做：同一条目名在殿堂页里既有原曲的记录又有好多翻唱版的记录，
    而老式 `{{Temple Song}}` 页不写 `|歌手 =`，光看殿堂页没法知道哪条是这位歌姬唱的
    （实测 `magnet`：原曲 2009 传说曲/niconico、翻唱版 2010~2013 殿堂曲/niconico，
    站上 `Template:IA/2012` 把它放在 2012 的殿堂曲）。既有页面是人工校对过的，
    就以它为准（用户 2026-10-01）。

    ⚠️ 传**一整束**（`鸟之诗` 在 2021 那页同时挂在 殿堂曲/niconico 与殿堂曲/bilibili 下）
    —— 一条一条改会把前一条覆盖掉。
    """
    clean = list(dict.fromkeys((str(rank), str(station), str(year))
                               for rank, station, year in places
                               if rank and station and year))
    if not clean:
        return
    # ⚠️ 按 (栏, 站点) 再去一次重：既有页面里同一首歌可能在**两张年份子页**上各写了一遍
    # （年份不同），不去重就会在同一个格子把这首歌列**两遍**（用户 2026-10-01 手工删过
    # `Template:IA/2012` 里重复的 `视力检查` / `Historia:opening theme`）。
    song.places = list(dict.fromkeys((rank, station) for rank, station, _year in clean))
    song.place_years = {(rank, station): year for rank, station, year in clean}
    song.year = min(song.place_years.values())
    song.years = sorted(set(song.place_years.values()))
    song.note = "；".join(filter(None, [song.note, "翻唱曲：位置按既有页面"]))


def _remix_entry_title(title: str, ja: str) -> str:
    """混音版曲目的条目名要写成 `<原曲条目>/<remixer>`（用户 2026-10-01 的 `Template:IA/2014` 修订）。

    实测：`{{lj|[[Antibeat|アンチビート／DIVELA REMIX]]}}*` →
    `{{lj|[[Antibeat/DIVELA|アンチビート／DIVELA REMIX]]}}*`，
    `ロストワンの号哭／DIVELA REMIX` → `Lost one的号哭/DIVELA`。

    ⚠️ 只认「`／<名字> REMIX`」这种写法（全角/半角斜杠 + 名字 + Remix）：
    `透明エレジー -Morimoto hiroCt Remix-` 那种用连字符的站上没这么改，不动它。
    """
    match = REMIX_ENTRY_RE.match(str(ja or "").strip())
    if not match or not title or "/" in str(title):
        return str(title or "")
    return f"{title}/{match.group('who')}"


def _merge_existing_songs(work: VocalistWork, texts: Dict[str, str],
                          years: Sequence[str]) -> None:
    """把既有模板 / 既有年份子页里列到的曲子并进 `work.songs`（用户 2026-10-01）。

    为什么要这一步：`Category:<歌姬>歌曲` 只收**已经建好条目**的歌，翻唱与还没建条目的
    红链曲目都不在里面 —— 实测拆分前的 `Template:歌爱雪`（revid 251675）列着 10 条红链
    （`Castle on a Cloud` / `Five Nights At Freddy's Song` / `パラオナボーイ` …）与 3 个
    「翻唱」记号，光按分类重建就会整批丢掉。**既有的模板才是「这位歌姬唱过哪些」的完整名单。**

    已有的歌按条目名 / 日文名 / 重定向目标对号（只补 `cover` / `unlinked` 两个记号，位置仍以
    我们的数据为准）；对不上号的（红链、翻唱）按**它在既有页面里的位置**补进来并写进待复核。
    """
    sources: List[Tuple[str, str]] = []
    previous_note = ""
    if work.existing:
        if template_song_entries(work.existing):
            sources.append((work.existing, ""))
        else:
            previous, previous_note = _previous_template_songs(work)
            if previous:
                sources.append((previous, ""))
    for year in years:
        text = texts.get(f"{work.template_title}/{year}")
        if text:
            sources.append((text, year))
    entries: List[Dict[str, object]] = []
    for text, default_year in sources:
        entries.extend(template_song_entries(text, default_year))
    if not entries:
        return
    # 「相关人物」栏里的名字不能当曲子搬（实测旧 `Template:歌爱雪` 的「其他」栏里编着一个 `kagomeP`）
    people = set(template_links(work.relation))
    if people:
        entries = [entry for entry in entries
                   if str(entry["title"] or "") not in people
                   and str(entry["ja"] or "") not in people]
        if not entries:
            return
    redirects = wiki_api.redirect_targets(
        [str(entry["title"]) for entry in entries if entry["title"]])
    by_name: Dict[str, VocalistSong] = {}
    for song in work.songs:
        for value in (song.title, song.ja):
            name = str(value or "").strip()
            if name:
                by_name.setdefault(name, song)
    skipped = {str(value) for value in (work.skipped_covers or []) if str(value).strip()}
    added = 0
    covers: List[VocalistSong] = []            # 既有页面里标了 `*` 的曲子（位置以既有页面为准）
    imported_covers: List[VocalistSong] = []   # 从既有模板补进来的翻唱曲（下面要核「演唱」）
    adopted: Dict[int, List[Tuple[str, str, str]]] = {}
    for entry in entries:
        title = str(entry["title"] or "")
        ja = str(entry["ja"] or "")
        # 歌唱栏里认不出这位歌姬的翻唱曲：**既有页面里也不再搬回来**（用户 2026-10-01）
        if skipped and (title in skipped or ja in skipped):
            logging.info("「%s」是歌唱栏里认不出这位歌姬的翻唱曲 —— 既有模板里也不收录", title or ja)
            continue
        song = by_name.get(title) or by_name.get(redirects.get(title, title))
        if song is None and ja:
            # 日文名兜底：门槛跟 `classify()` 一样（短名字太容易撞车）
            candidate = by_name.get(ja)
            if candidate is not None and (not title or len(ja) >= MIN_JA_MATCH_LEN):
                song = candidate
        if song is not None:
            if entry["cover"]:
                song.cover = True
                # 显示名：翻唱那一版常常带版本信息（既有页面写 `透明エレジー -Morimoto hiroCt
                # Remix-`，条目里是原名 `透明エレジー`）—— 只在「比原名多出一截」时采纳
                ja_value = str(entry["ja"] or "")
                if ja_value and song.ja and ja_value != song.ja and song.ja in ja_value:
                    song.ja = ja_value
                rank, station = str(entry["rank"] or ""), str(entry["station"] or "")
                year = str(entry["year"] or "")
                if rank and station and year:
                    adopted.setdefault(id(song), []).append((rank, station, year))
                    if song not in covers:
                        covers.append(song)
            if entry.get("engine") and not song.super_engine:
                song.super_engine = str(entry["engine"])          # 上标（`<sup>CeVIO</sup>`）
            if entry.get("anchor") and not song.anchor:
                song.anchor = str(entry["anchor"])                # `[[条目#二次创作|…]]`
            if entry["unlinked"] and not song.unlinked:
                # 站上按「无法收录」处理：只写日文名、不给链接（`{{lj|パラオナボーイ}}*`）
                song.unlinked = True
                song.ja = ja or song.ja
            if entry["comment"] and entry["comment"] not in song.note:
                song.note = "；".join(filter(None, [song.note, f"既有模板注：{entry['comment']}"]))
            continue
        title = _remix_entry_title(title, ja)      # 混音版：`<原曲>/<remixer>`
        song = VocalistSong(title=title, ja=ja or title, year=str(entry["year"] or ""),
                            kind=str(entry["kind"] or ""), cover=bool(entry["cover"]),
                            unlinked=bool(entry["unlinked"]), page_exists=False,
                            super_engine=str(entry.get("engine") or ""),
                            anchor=str(entry.get("anchor") or ""),
                            source="既有模板")
        song.places = [(str(entry["rank"]), str(entry["station"]))]
        song.note = "；".join(filter(None, [
            "既有模板（`Template:" + work.name + "`）里列着这首歌，但分类里没有 —— 按原样搬过来",
            previous_note,
            f"既有模板注：{entry['comment']}" if entry["comment"] else ""]))
        # 用户 2026-10-01：「分类没有而模板有的以模板为准，不用人工判断」——
        # 只有**连年份都拿不到**（拆分时归不了页）才留一条提醒。
        song.flag = "" if song.year else "取不到投稿年（拆分成年份子页时要你指定）"
        work.songs.append(song)
        by_name.setdefault(title or ja, song)
        by_name.setdefault(str(entry["title"] or "") or ja, song)
        if song.cover:
            imported_covers.append(song)
        added += 1
        if len(work.flags) < MAX_FLAGS:
            work.flags.append({"title": song.title, "ja": song.ja, "reason": song.flag,
                               "rank": song.rank, "station": "、".join(song.all_stations),
                               "year": song.year})
    # 翻唱曲：把既有页面里校对过的位置**整束**收下（`鸟之诗` 那种一页挂两栏的别只留一条）
    for song in covers:
        _adopt_existing_places(song, adopted.get(id(song), []))
        logging.info("「%s」是翻唱曲：位置按既有页面（%s）", song.title, song.places_text())
    # 分类里没有、只从既有模板搬进来的翻唱曲：去条目里核一遍「演唱」栏认不认得出这位歌姬
    # （`prune_cover_mismatch()` 靠 `own_version` 决定收不收）。条目不存在（红链）→ 当认得。
    if imported_covers:
        texts = wiki_api.fetch_pages_text([song.title for song in imported_covers])
        for song in imported_covers:
            text = texts.get(song.title)
            if text is not None:
                singers = _singers(text)
                song.own_version = (not singers) or belongs_to(text, work.name)
                song.secondary = secondary_has_singer(text, work.name)
                if song.secondary and not song.own_version and not song.anchor:
                    song.anchor = SECONDARY_ANCHOR
    if added:
        logging.info("既有模板 / 年份子页里另有 %d 首分类里没有的曲子（翻唱 / 红链），已按原样搬过来",
                     added)
        work.summary = re.sub(r"· \d+ 条待复核", f"· {len(work.flags)} 条待复核",
                              str(work.summary or ""))
        work.summary = "；".join(filter(None, [work.summary,
                                              f"从既有模板补了 {added} 首（翻唱 / 红链）"]))


def prune_cover_mismatch(work: VocalistWork) -> List[str]:
    """删掉「歌唱栏里认不出这位歌姬」的翻唱曲 → 被删的标题。

    用户 2026-10-01 拿 `Template:IA/2012` 的 255000 定下来的：`视力检查` 的条目写的是
    GUMI、`magnet` 写的是初音未来×巡音流歌 —— 这两首站上都从模板里删了；
    而 `Ave Maria` / `Historia:opening theme` 的 `|演唱 = [[IA]]` 留着。

    ⚠️ **混音版不算**：`Antibeat/DIVELA`、`透明エレジー -Morimoto hiroCt Remix-` 这种在站上
    是**另一首独立的歌**（名字里带 REMIX / リミックス），照样收 —— 它们也是用户亲手改的标题。

    什么时候调：`load_existing()` 末尾（要等既有页面合并完，才看得到那边写的显示名）。
    """
    dropped: List[str] = []
    kept: List[VocalistSong] = []
    for song in work.songs:
        if song.own_version or song.secondary \
                or REMIX_WORD_RE.search(f"{song.title} {song.ja}") \
                or not (song.cover or song.album_credit):
            kept.append(song)
            continue
        dropped.append(song.title or song.ja)
        if song.album_credit:
            logging.info("「%s」（%s）在条目「演唱」里只挂在专辑 / 精选碟上 → 不收",
                         song.title, song.ja)
        else:
            logging.info("「%s」（%s）是翻唱曲，但条目「演唱」里认不出 %s → 不收",
                         song.title, song.ja, work.name)
    if dropped:
        work.songs = kept
        work.flags = [flag for flag in work.flags if flag.get("title") not in set(dropped)]
        work.summary = "；".join(filter(None, [work.summary,
                                              f"没收 {len(dropped)} 首不算她的曲子"
                                              f"（唱栏认不出歌姬的翻唱 / 只挂在专辑上）"]))
    work.skipped_covers = list(dict.fromkeys([*work.skipped_covers, *dropped]))
    return dropped


def effective_styles(work: VocalistWork) -> Dict[str, str]:
    """真正要写进模板的六色。

    * 既有模板 → 从它那儿读回来的那套；
    * 新建模板 → **从空开始**（不套 P主模板那套蓝黄）：歌姬模板的配色是照立绘配的，
      用户 2026-09-30 要求「想要什么色就在样式页里（或 AI）配」，不配就用 Navbox 默认灰底。
    """
    base = parse_styles(work.existing) if work.existing else {}
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

def _sep_indent(list_indent: str) -> str:
    """分隔符续行的缩进：跟着 `|listN = ` 那行的缩进再深 6 格（照 `Template:重音Teto/2024`）。"""
    return list_indent + "      "


def _songs_line(songs: Sequence[VocalistSong], list_indent: str = "") -> str:
    """曲目列：**一首一行**，中间用 `{{W}}<!--\n<缩进>-->` 接起来。

    `{{W}}` 本身就是「` • `」（实测 `Template:W` = `<includeonly><nowiki> • </nowiki></includeonly>`），
    所以渲染结果跟原来一样，但 wikitext 一行一首 —— 好比对也好手改，
    而且那条 HTML 注释把换行吃掉，不会在页面上多出空白（用户 2026-10-01 要求）。
    站上 `Template:Flower/*` / `Template:重音Teto/*` 就是这个写法。

    翻唱曲目后面要跟一个 `*`（用户 2026-10-01；实测旧 `Template:歌爱雪` 的
    `[[凤仙花|{{lj|鳳仙花}}]]*`）—— `*` 写在链接**外面**，注释分隔符之前。
    """
    links = [song.link + (f"<sup>{song.super_engine}</sup>" if song.super_engine else "")
             + (COVER_MARK if song.cover else "")
             for song in songs if song.link]
    return ("{{W}}<!--\n" + _sep_indent(list_indent) + "-->").join(links)


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


def _placements(songs: Sequence[VocalistSong], year: str = "", until: str = ""
                ) -> List[Tuple[str, Dict[str, List[VocalistSong]]]]:
    """把曲子按 `(栏, 站点)` 摊开 → `[(栏, {站点: [曲子]})]`。

    同一首歌可以同时出现在好几栏里（`催眠者` = 神话曲/niconico + 破亿播放曲目/YouTube）。
    `year` 给了就是**年份子页**：只摊开属于这一年的那些位置（`六兆年と一夜物語` 的
    niconico 只上 2012 那一页、YouTube 只上 2013 那一页）；`until` 是合并子页（
    ≤ 它的年份全在这一页）。
    """
    buckets: Dict[str, Dict[str, List[VocalistSong]]] = {}
    for song in songs:
        places = (song.places_in(year, until) if year
                  else (song.places or [(RANK_OTHER, STATION_NICO)]))
        for rank, station in places:
            items = buckets.setdefault(rank, {}).setdefault(station, [])
            # 同一个格子里同一首歌只列一次（同名同显示名算同一首）—— 站上出现过重复
            # （用户 2026-10-01 手工删过 `Template:IA/2012` 里重复的 `视力检查`），
            # 这里兜一道底，别再让它冒出来。
            if not any(item.title == song.title and item.ja == song.ja for item in items):
                items.append(song)
    result = [(rank, buckets.pop(rank)) for rank in RANK_ORDER if rank in buckets]
    result += [(rank, items) for rank, items in buckets.items()]   # 用户手改的栏名也别丢
    return result


def _other_groups(songs: Sequence[VocalistSong]) -> List[Tuple[str, List[VocalistSong]]]:
    """其他栏的两个子栏（部分未殿堂曲 / 部分YouTube投稿）。只看栏是「其他」的曲子。"""
    candidates = _other_songs(songs)
    result: List[Tuple[str, List[VocalistSong]]] = []
    for kind in (OTHER_UNHALL, OTHER_YOUTUBE):
        matched = _sorted_by_date([song for song in candidates if song.other_kind == kind])
        if matched:
            result.append((kind, matched))
    return result


def _other_label(kind: str) -> str:
    """其他栏里两种子栏同时存在时的小栏名（照 `Template:歌爱雪`）。"""
    if kind == OTHER_UNHALL:
        return f"{{{{mousetext|{OTHER_UNHALL}|{OTHER_UNHALL_NOTE}}}}}"
    return kind


def _rank_label(rank: str) -> str:
    """栏名：其他栏按站上写法带上那个注（`其他{{注||收录Vocawiki已有条目。}}`）。"""
    return OTHER_LABEL if rank == RANK_OTHER else rank


def _other_value(songs: Sequence[VocalistSong], styles: Dict[str, str],
                 indent: str, by_year: bool = False) -> object:
    """「其他」栏的值。三种写法（都是站上真实存在的）：

    * `by_year=True` 且曲子跨**两个以上**年份 → 按年份分层
      （`其他 → 2022年 / 2023年 → 曲目`，站上 里命 / 狐子 / 鸣花姬·尊）；
    * 否则只有**一种**子栏（多数歌姬只有 niconico+bilibili）→ **直接平铺曲目**
      （站上 NurseRobot TypeT / 琴叶茜 / 琴叶葵 / 双叶凑音 / SeeU）；
    * 两种子栏都有 → 套「部分未殿堂曲 / 部分YouTube投稿」（`Template:歌爱雪`）。
    """
    if by_year:
        years = _by_year(_other_songs(songs))
        if len(years) > 1:
            inner = _next_indent(indent)          # 里面那层 `|listN = ` 的缩进
            return _subgroup([(_year_label(year), _songs_line(items, inner))
                              for year, items in years], styles, indent)
    groups = _other_groups(songs)
    if len(groups) <= 1:
        items = groups[0][1] if groups else _sorted_by_date(_other_songs(songs))
        return _songs_line(items, indent)          # 平铺时它自己就在 `|listN = ` 那一行上
    return _subgroup([(_other_label(kind), _songs_line(items, _next_indent(indent)))
                      for kind, items in groups], styles, indent)


def _other_songs(songs: Sequence[VocalistSong]) -> List[VocalistSong]:
    """**栏是「其他」**的曲子。

    ⚠️ 「其他」栏的两种写法（按年份分层 / 按 kind 分子栏）都得先过这一道：
    `other_kind` 对认不出的 kind（殿堂 / 传说 / 神话曲的 `kind` 正是空的）会兜底成
    「部分未殿堂曲」，`_by_year()` 又只看年份 —— 不筛就会把殿堂曲一起列进「其他」栏。
    """
    return [song for song in songs if song.rank == RANK_OTHER]


def _year_label(year: str) -> str:
    """年份小栏的标签（`2022年`；取不到年份的那一组写「年份未知」）。"""
    value = str(year or "").strip()
    return f"{value}年" if value.isdigit() else "年份未知"


def _merged_label(year: str) -> str:
    """合并子页的年份小栏标签：`2021及以前`（照 `Template:可不/2021及以前`）。"""
    return f"{year}及以前"


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
        # 站上写的是 `|evenstyle = background:{{ColorOps|-90|#f38286}}`（不带尾分号）
        lines.append(f"{indent}    |evenstyle = background:{{{{ColorOps|-90|"
                     f"{styles['groupBg']}}}}}")
    for index, (label, value) in enumerate(groups, start=1):
        lines.append(f"{indent}    |group{index} = {label}")
        if isinstance(value, str):
            lines.append(f"{indent}    |list{index} = {value}")
        else:
            # 嵌套子分组的第一行接在 `= ` 后面（它自己带着缩进），其余的另起行
            lines.append(f"{indent}    |list{index} = {str(value[0]).lstrip()}")
            lines.extend(value[1:])
    # ⚠️ f-string 里 `{indent}` 后面要写 **4 个**右花括号才是「两个右花括号」（`}}}}` → `}}`）；
    # 写 2 个（`}}`）只会输出一个 `}`：`Template:弗里摩侠` 里的 `{{Navbox subgroup}}` 就全都只
    # 关了一个 `}`（用户 2026-09-30 报的）。
    # 缩进：跟子分组自身一样；**最外层**（缩进为空）的收尾再缩进 2 格（即 `  }}`）——
    # 用户手改后发到站上的那份 `Template:弗里摩侠` 就是这么收的。
    lines.append(f"{indent or '  '}}}}}")
    return lines
    return lines


def _next_indent(indent: str) -> str:
    return indent + "    "


def _station_value(songs: Sequence[VocalistSong], with_years: bool, styles: Dict[str, str],
                   indent: str) -> object:
    """一个站点格：不分年份 → 一行曲目；分年份 → 再套一层年份小格。"""
    if not with_years:
        return _songs_line(_sorted_by_date(songs), indent)
    inner = _next_indent(indent)
    groups = [(f"{year}年", _songs_line(_sorted_by_date(items), inner))
              for year, items in _by_year(songs)]
    return _subgroup(groups, styles, indent)


def _song_value(songs: Sequence[VocalistSong], with_years: bool, styles: Dict[str, str],
                indent: str = "", other_years: bool = False) -> List[str]:
    """「歌曲」那一栏的值：栏 → 站点（→ 年份）。"""
    groups: List[Tuple[str, object]] = []
    for rank, station_map in _placements(songs):
        if rank == RANK_OTHER:
            groups.append((_rank_label(rank),
                           _other_value(songs, styles, _next_indent(indent), other_years)))
            continue
        stations = [(station, _station_value(items, with_years, styles,
                                            _next_indent(_next_indent(indent))))
                    for station, items in _stations_of(station_map)]
        groups.append((_rank_label(rank), _subgroup(stations, styles, _next_indent(indent))))
    return _subgroup(groups, styles, indent)


def _title_line(work: VocalistWork, suffix: str = "") -> str:
    fg = str(effective_styles(work).get("titleFg") or "").strip() or "#333333"
    return f"{{{{coloredlink|{fg}|{work.name}{suffix}}}}}"


def _state_param(style: str) -> str:
    return f"|state = {style}"


def _head(work: VocalistWork, name: str, title: str, styles: Dict[str, str],
          state: str, extra: Sequence[str] = ()) -> List[str]:
    """Navbox 头部（`extra` 用来插 `|abovestyle = …` 这种额外参数，位置照站上）。"""
    params = style_params(styles)
    lines = ["{{Navbox", f"|name = {name}", f"|title = {title}", state]
    for key in ("titlestyle", "groupstyle", "liststyle"):
        if params[key]:
            lines.append(f"|{key} = {params[key]}")
    lines.extend(extra)
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
            # ⚠️ **不写** `|groupN = 相关人物`：那个块自己就是 `{{#invoke:Nav|box|subgroup
            # … |title = 相关人物 …}}`，外面再挂一个标签会多出左栏
            # （用户 2026-10-01 从 `Template:歌爱雪` 上把这个标签删掉了：revid 252602）。
            # 也不要在它前面留空行 —— 站上那张模板头部与这一行是紧挨着的。
            lines.append(f"|list{index} = {relation_text(work)}")
            index += 1
        for title, block in work.extra_groups:
            # 「演唱会」「官方专辑」这类栏原样搬过来（用户 2026-10-01）
            lines.append(f"|list{index} = {group_text(work, block)}")
            index += 1
        years = work.years()
        if not years:
            logging.warning("「%s」一首带年份的曲子都没有，拆出来的主模板会是空的", work.name)
        for year in years:
            lines.append(f"|list{index} = {{{{{work.name}/{year}|nocate=1|state=uncollapsed|"
                         "child|noabove}}")
            index += 1
        # 收尾的 `}}` 紧跟内容，不再空一行（与用户手改后的 `Template:弗里摩侠` 一致）
        lines += ["}}",
                  f"<noinclude>{{{{Documentation}}}}[[Category:{work.name}"
                  f"{YEAR_CATEGORY_SUFFIX}]]</noinclude>"]
        return "\n".join(lines) + "\n"
    lines = _head(work, work.name, _title_line(work), styles,
                  "|state = {{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|"
                  "mw-uncollapsed}}")
    index = 1
    if work.relation:
        lines.append(f"|list{index} = {relation_text(work)}")     # 同上：不带标签、不空行
        index += 1
    for _title, block in work.extra_groups:
        lines.append(f"|list{index} = {group_text(work, block)}")   # 演唱会 / 官方专辑
        index += 1
    lines += ["", f"|group{index} = 歌曲",
              f"|list{index} = " + "\n".join(
                  _song_value(work.songs, True, styles, "", other_years=work.other_years))]
    # 收尾的 `}}` 紧跟内容，不再空一行（与用户手改后的 `Template:弗里摩侠` 一致）
    lines += ["}}", _includeonly(work), f"<noinclude>{VOCALIST_TEMPLATE_CATEGORY}</noinclude>"]
    return "\n".join(lines) + "\n"


def group_text(work: VocalistWork, block: str) -> str:
    """继承下来的一栏（「相关人物」/「演唱会」/「官方专辑」）的正文：跟着当前配色换色。"""
    value = str(block or "").strip()
    if not value:
        return ""
    old = parse_styles(work.existing) if work.existing else {}
    mapping: Dict[str, str] = {}
    for key, new_value in (work.styles or {}).items():
        old_value = str(old.get(key) or "")
        if old_value and new_value and old_value.lower() != str(new_value).lower():
            mapping[old_value] = str(new_value)
    return remap_colors(value, mapping)


def relation_text(work: VocalistWork) -> str:
    """「相关人物」那一栏的正文：继承下来的块要跟着当前配色换色。"""
    return group_text(work, work.relation)


def first_year(work: VocalistWork) -> str:
    """这一套年份子页里 `|above` 要填的年份（`{{虚拟歌姬年份计算}}` 从它循环到今年）。

    ⚠️ **优先照既有年份子页写的那个值**：站上填的是歌姬的出道年（`Template:IA/*` 一律写
    2011），而我们从曲子日期里算出来的「最早那年」可能是封面曲的原始年份（实测 IA 算成
    2007，而那三年根本不该有子页）。既没有可参照的子页时才用算出来的最早那一年。
    """
    match = YEAR_ABOVE_PARAM_RE.search(str(work.existing_year or ""))
    if match:
        return match.group(1)
    years = work.years()
    return years[0] if years else ""


YEAR_TITLE_JOIN_RE = re.compile(r"\}\}(\s*)\d{4}年(?:及以前)?歌曲")
# 既有年份子页 `|above` 上写的 `年份=`（站上填的是**歌姬出道年**，不是最早那首曲子的年）
YEAR_ABOVE_PARAM_RE = re.compile(r"\|above\s*=[^\n]*?\|\s*年份\s*=\s*(\d{4})")
YEAR_ABOVE_STYLE_RE = re.compile(r"^\s*\|abovestyle\s*=\s*([^\n]*)$", re.MULTILINE)


def _year_title_joiner(work: VocalistWork) -> str:
    """标题里名字与年份之间写不写空格：跟着既有年份子页走（拿不到就拿带空格的写法）。"""
    match = YEAR_TITLE_JOIN_RE.search(str(work.existing_year or ""))
    if match:
        return match.group(1)
    return " "


def _year_above_style(work: VocalistWork) -> str:
    """`|abovestyle` 那一行（既有子页没写就不写；没参照时照继承到的配色自己拼一条）。"""
    if work.existing_year:
        match = YEAR_ABOVE_STYLE_RE.search(work.existing_year)
        return match.group(1).strip() if match else ""
    styles = effective_styles(work)
    bg = str(styles.get("titleBg") or "").strip()
    fg = str(styles.get("titleFg") or "").strip()
    if bg and fg:
        return f"background:{bg};color:{fg}"
    return ""


def build_year_page(work: VocalistWork, year: str) -> str:
    """年份子页 `Template:<歌姬>/<年份>`（只列这一年；年份固定了，就不再套年份小格）。

    三处站上不统一 / 容易写错的地方都有对照（2026-09-30 用户拿
    `Template:歌爱雪/2019`、`/2022` 两个修订指出）：

    * `.标题`：名字与年份之间写不写空格 —— 跟着这位歌姬**已有的**年份子页走（`_year_title_joiner()`）；
    * `|above` 的 `年份=`：填**最早那一年**（`{{虚拟歌姬年份计算}}` 是从它循环到今年）——
      填成本页年份的话题头会漏掉前面的年份；
    * `|abovestyle`：既有子页写了就照搬，没写就不写（`_year_above_style()`）；
    * 分类挂的是 **`[[Category:<歌姬>模板]]`**、不是「虚拟歌手模板」。
    """
    styles = effective_styles(work)
    songs = work.songs_in(year)
    suffix = work.year_suffix(year)
    merged = work.merged_page(year)
    # 合并子页的标题写「<Y>年及以前歌曲」（照 `Template:可不/2021及以前`）
    year_text = f"{year}年及以前" if merged else f"{suffix}年"
    title = f"{_title_line(work)}{_year_title_joiner(work)}{year_text}歌曲"
    above_style = _year_above_style(work)
    extra = [f"|abovestyle = {above_style}"] if above_style else []
    lines = _head(work, f"{work.name}/{suffix}", title, styles,
                  "|state = {{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|"
                  "mw-collapsible mw-collapsed}}", extra=extra)
    # 被主模板 transclude 时需要这两个参数（照 `Template:重音Teto/2024`）
    lines += ["|navbar={{#ifeq:{{{1}}}|child|plain|}}",
              "| {{#ifeq:{{{1}}}|child|child|}}"]
    fg = str(styles.get("titleFg") or "").strip() or "#333333"
    # `|above = {{#ifeq:{{{2}}}|noabove||{{虚拟歌姬年份计算|年份=X|歌姬名=…|color=#333333}}}}`
    # ⚠️ `年份` 是**最早那一年**（不是本页年份）：`{{虚拟歌姬年份计算}}` 里是
    # `#invoke:loop|count=年份 → 今年`，拿它挨年挨年生成 `Template:<歌姬>/<年>` 的链接。
    # 实测站上 `Template:歌爱雪/*` 一律填 2009、`Template:重音Teto/*` 一律填 2008。
    lines.append("|above = {{#ifeq:{{{2}}}|noabove||{{虚拟歌姬年份计算|"
                 + "年份=" + (year if merged else first_year(work)) + "|歌姬名=" + work.name
                 + ("|前导=1" if merged else "") + "|color=" + fg
                 + "}}}}")
    for index, (rank, station_map) in enumerate(_placements(songs, year, work.merge_until),
                                                start=1):
        # 年份子页里年份固定了，只要「栏 → 站点」两层；合并子页（`<Y>及以前`）再多一层年份
        lines += ["", f"|group{index} = {_rank_label(rank)}",
                  f"|list{index} = " + "\n".join(
                      _rank_value(rank, station_map, styles, by_year=merged))]
    lines += ["}}", _includeonly(work),
              f"<noinclude>{year_category(work)}</noinclude>"]
    return "\n".join(lines) + "\n"


def year_category(work: VocalistWork) -> str:
    """年份子页 / 拆分后的主模板挂的分类：`[[Category:歌爱雪模板]]`。

    用户 2026-09-30 指出：`Template:歌爱雪/2009` 的分类应该是「歌爱雪模板」而不是
    「虚拟歌手模板」（站上 `Template:重音Teto/2024` 也是这样）。
    """
    return f"[[Category:{work.name}{YEAR_CATEGORY_SUFFIX}]]"


# 分类成员名单的「上次读到什么」缓存（跨启动记在 `output/category_cache.json`）：
# 站上的分类查询偶尔会**少回几条**，少读了很难发现 —— 比上次少就重读一次（用户 2026-10-01）。
CATEGORY_CACHE_NAME = "category_cache.json"
_category_cache: Optional[Dict[str, List[str]]] = None


def _category_cache_path() -> Optional[Path]:
    """缓存文件路径（拿不到就只用内存里那份，不当错误）。"""
    try:
        return Path(get_output_path()) / CATEGORY_CACHE_NAME
    except Exception as e:                       # noqa: BLE001 - 路径取不到就退化
        logging.debug("取输出目录失败，分类缓存只放内存里：%s", e)
        return None


def _load_category_cache() -> Dict[str, List[str]]:
    global _category_cache
    if _category_cache is None:
        data: Dict[str, object] = {}
        path = _category_cache_path()
        if path is not None and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8")) or {}
            except (OSError, ValueError) as e:
                logging.warning("读分类缓存失败（%s）：%s", path, e)
                data = {}
        _category_cache = {str(key): [str(item) for item in value]
                           for key, value in data.items() if isinstance(value, list)}
    return _category_cache


def _save_category_cache() -> None:
    path = _category_cache_path()
    if path is None or _category_cache is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_category_cache, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logging.warning("写分类缓存失败（%s）：%s", path, e)


def fetch_category_titles(category: str, limit: int, progress=None) -> List[str]:
    """读分类成员；**比上次少就重读一次**（用户 2026-10-01）。

    为什么需要：站上的分类查询偶尔会少回几条 —— 实测 `Category:IA歌曲` 在 512 / 514 之间跳，
    `magnet` 就漏过一次（它不在分类里，只能靠既有模板那条路进来）。少读了根本看不出来，
    而重读一次的代价很小。上次的名单记在 `output/category_cache.json`（跨启动也记得）。
    """
    titles = wiki_api.category_members(category, limit=limit)
    known = set(_load_category_cache().get(category) or [])
    if titles and known:
        missing = sorted(known - set(titles))
        if missing:
            logging.warning("分类 %s 这次比上次少 %d 条（%s……），重读一次",
                            category, len(missing), "、".join(missing[:5]))
            if progress is not None:
                progress(f"分类比上次少读了 {len(missing)} 条，重读一次…")
            again = wiki_api.category_members(category, limit=limit)
            if len(again) > len(titles):
                titles = again
            still = sorted(known - set(titles))
            if still:
                logging.warning("分类 %s 重读后仍少 %d 条（%s……）—— 可能真的被移出分类了",
                                category, len(still), "、".join(still[:5]))
    if titles:
        cache = _load_category_cache()
        cache[category] = list(titles)
        _save_category_cache()
    return titles


def category_page_title(work: VocalistWork) -> str:
    """上面那个分类的分类页标题：`Category:歌爱雪模板`。"""
    return f"Category:{work.name}{YEAR_CATEGORY_SUFFIX}"


def build_category_page(work: VocalistWork) -> str:
    """分类页正文（照站上 `Category:歌爱雪模板` / `Category:重音Teto模板` 那几页写）。

    ⚠️ **只在维基上还没有这个分类时才提交**（见 `VocalistTemplateApi.submit_all()`）：
    分类页可能已经有说明 / 排序键 / 别的模板，不能拿这一小段把它盖掉。
    """
    return CATEGORY_PAGE_TEXT + "\n"


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
                styles: Dict[str, str], by_year: bool = False) -> List[str]:
    """一栏的值（年份子页里用：栏 → 站点，不再套年份）。

    `by_year=True` 就是**合并子页**（`Template:可不/2021及以前`）：站点下面再套一层
    年份小栏（`niconico → 2020年 / 2021年 → 曲目`，实测站上写法）。
    """
    if rank == RANK_OTHER:
        songs = _unique([song for items in station_map.values() for song in items])
        value = _other_value(songs, styles, "", by_year=by_year)
        # 单一子栏时 `_other_value()` 直接给一行曲目，不要再套一层（栏名已经写在外面了）
        return [value] if isinstance(value, str) else value
    inner = _next_indent("")
    year_inner = _next_indent(inner)
    groups: List[Tuple[str, object]] = []
    for station, items in _stations_of(station_map):
        if not by_year:
            groups.append((station, _songs_line(_sorted_by_date(items), inner)))
            continue
        buckets: Dict[str, List[VocalistSong]] = {}
        for song in items:
            buckets.setdefault(str(song.year or ""), []).append(song)
        keys = sorted(key for key in buckets if key.isdigit())
        keys += [key for key in buckets if not key.isdigit()]
        # 合并子页里**总是**套一层年份小栏（照站上：哪怕这一站只有一年）
        groups.append((station, _subgroup(
            [(_year_label(key), _songs_line(_sorted_by_date(buckets[key]), year_inner))
             for key in keys], styles, inner)))
    return _subgroup(groups, styles, "")


def build_doc(work: VocalistWork) -> str:
    """`Template:<歌姬>/doc`：说明 + 各年份子页的清单（照 `Template:重音Teto/doc` 写）。"""
    lines: List[str] = [
        f"* 本模板收录[[{work.name}]]演唱的歌曲，曲子多，按投稿年份拆成子页。",
        "* 传说曲 / 殿堂曲等各栏按投稿时间排序，「其他」栏按收录顺序排。",
        "* 添加'''殿堂'''级歌曲时用 <code><nowiki><!-- 注释 --></nowiki></code> 标注投稿时间。",
        "* 翻唱曲目加「<nowiki>*</nowiki>」号。",
    ]
    if work.subpages_only:
        lines.append("* 子页由工具生成；主模板未动，各年份子页需要自己挂到主模板上。")
    years = work.years()
    if years:
        lines.append("* 各年份子页（点「编辑」可直接改）：")
        lines.append("<div>")
        for year in years:
            page = f"{TEMPLATE_PREFIX}{work.name}/{work.year_suffix(year)}"
            lines += ["    <div style=\"display: inline-block; margin-bottom: 3px;\">",
                      "        <div style=\"width:18em; display: inline-block;\">"
                      f"{{{{Space|-2}}}}{{{{Space|2}}}}[[{page}]]</div>",
                      "        <div style=\"width:8em; display: inline-block;\">"
                      f"[{{{{指定页面编辑按钮|{page}|编辑}}}}]</div>",
                      "    </div>"]
        lines.append("</div>")
    lines.append(DOC_CATEGORY)                 # `Template:重音Teto/doc` 实测
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
    """要生成的页面。**顺序就是提交顺序**：分类页 → 年份子页 → 文档 → 主模板。

    先写子页、最后写主模板：主模板 transclude 各年份子页，反过来的话中间那一小段时间
    主模板上全是红链。

    **分类页**（`Category:<歌姬>模板`，用户 2026-09-30 要求）：年份子页挂的就是这个分类，
    维基上没有的话提交时一并建（内容照站上 `Category:歌爱雪模板` 那几页）；
    已经有了就**不碰**（`VocalistTemplateApi` 提交前会核一下）。

    `work.subpages_only`（**只新建年份子页、不动既有主模板**，用户 2026-09-30）时
    只出年份子页（与分类页）：既有主模板（例如 `Template:初音未来` 那种手写大导航框）保持原样，
    文档页也不动（它可能已经有自己的内容）。
    """
    specs: List[dict] = []
    if work.split:
        specs.append({"name": category_page_title(work), "text": build_category_page(work),
                      "kind": "category", "note": "分类页"})
        for year in work.years():
            suffix = work.year_suffix(year)
            specs.append({"name": f"{TEMPLATE_PREFIX}{work.name}/{suffix}",
                          "text": build_year_page(work, year), "kind": "year",
                          "note": (f"{year}年及以前（{len(work.songs_in(year))} 首）"
                                   if work.merged_page(year) else
                                   f"{suffix}年（{len(work.songs_in(year))} 首）")})
        if specs and not work.subpages_only:
            specs.append({"name": f"{TEMPLATE_PREFIX}{work.name}/doc",
                          "text": build_doc(work), "kind": "doc", "note": "模板文档"})
    if work.subpages_only:
        return _with_files(work, specs)
    specs.append({"name": work.template_title, "text": build_main_template(work),
                  "kind": "main",
                  "note": "主模板（各年份的转接）" if work.split else "主模板（不分年份）"})
    return _with_files(work, specs)


def _with_files(work: VocalistWork, specs: List[dict]) -> List[dict]:
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
    if kind == "category":
        return get_output_path().joinpath(f"歌姬模板_{safe}_分类页.wikitext")
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

def template_calls_for(work: VocalistWork, title: str) -> List[str]:
    """一条条目要写**哪几条**调用 → 花括号里的整串（按顺序）。

    用户 2026-09-30 / 2026-10-01 定的写法：

    * **歌姬条目自己** → `{{重音Teto|nocate=1}}`（不带分类）；
    * **曲子条目**（拆分时）→ `{{重音Teto/2024}}` —— **不带 `|collapsed`**：
      年份子页的 `|state` 是 `{{#ifeq:{{{state|}}}|uncollapsed|mw-uncollapsed|mw-collapsible
      mw-collapsed}}`，默认**就是折叠的**，传 `|collapsed` 反而落到 `{{{1}}}`（那个只认 `child`）。
      用户 2026-10-01 拿 `面包屑`（revid 252752）指出来的；
    * **跨年的曲子**（一个站一年：实测 `面包屑` nico 2023 / bilibili 2026）→ 每一年各写一条，
      站上那篇条目里就是 `{{歌爱雪/2023}}` + `{{歌爱雪/2026}}` 两行（`世界去死` 也一样）；
    * **没拆分** → `{{<歌姬>|collapsed}}`（主模板的 `|state` 认 `{{{1}}}=collapsed`，这个有用）。

    插入位置由 `insert_into_pages_for()` 传 `position="after_producer"`：
    跟在 P主/歌手模板后面、活动模板前面。
    """
    if title == work.name:
        return [f"{work.name}|nocate=1"]
    song = next((item for item in work.songs if item.title == title), None)
    if work.split and song is not None:
        place_years = [song.year_of(*place).strip() for place in song.places]
        years = {year for year in place_years if year}
        if place_years:
            fallback = song.year or min(years or song.all_years, default="")
            years.update(fallback for year in place_years if not year and fallback)
        if not years:
            years = set(song.all_years)
        if years:
            return [f"{work.name}/{work.year_suffix(work.page_year(year))}"
                    for year in sorted(years)]
    return [f"{work.name}|collapsed"]


def dropped_titles(work: VocalistWork) -> List[str]:
    """不再收进模板的曲子（`skipped_covers`：唱栏认不出歌姬的翻唱 / 只挂在专辑上的）。

    这些曲子的条目里可能还留着**早先写下的**模板调用 —— 回写时要顺手删掉
    （用户 2026-10-01 在 `magnet` 上手工删的 `{{IA/2012}}`）。
    既有页面里就不收的曲子会自动进这份名单（见 `load_existing()`）。
    """
    return [str(value) for value in (work.skipped_covers or []) if str(value).strip()]


def insert_into_pages_for(work: VocalistWork, titles: Sequence[str],
                          progress=None) -> List[dict]:
    """把模板写进一批条目（按「写哪几条」分组，每组一次批量提交）。

    位置用 `after_producer`：插在 P主/歌手模板后面、活动模板（`{{The VOCALOID
    Collection…}}`）前面（用户 2026-09-30）。

    曲子条目还会顺手删掉手写的 `[[分类:<歌姬>歌曲]]`（`drop_category`）—— 模板自己会加这个
    分类，不删就重复（用户 2026-09-30 报的 `阿卡贝拉一起唱！！`）。

    ⚠️ 名单里的曲子已经是「不再收录」那份（`dropped_titles()`）时反过来做：
    把条目里**残留的**调用删掉（用户 2026-10-01 拿 `magnet` 指出的）。
    """
    gone = set(dropped_titles(work))
    grouped: Dict[Tuple[str, ...], List[str]] = {}
    remove: List[str] = []
    for title in titles:
        value = str(title).strip()
        if not value:
            continue
        if value in gone:
            remove.append(value)
            continue
        calls = template_calls_for(work, value)
        grouped.setdefault(tuple(calls), []).append(value)
    results: List[dict] = remove_template_from_pages(work.name, remove, progress=progress) \
        if remove else []
    for calls, group in grouped.items():
        # 歌姬模板自己会在条目里加「<歌姬>歌曲」分类（模板的 `<includeonly>` 里写着 `{{ac|…歌曲}}`），
        # 所以往**曲子条目**里插模板时要顺手删掉条目里手写的那一行（用户 2026-09-30 报的：
        # `阿卡贝拉一起唱！！` 同时有 `{{弗里摩侠|collapsed}}` 与 `[[分类:弗里摩侠歌曲]]`）。
        # 歌姬条目那一份带 `nocate=1`，不加分类，也就没什么可删的。
        drop = "" if any("nocate" in call for call in calls) else f"{work.name}歌曲"
        # `rewrite=True`：页面里已经有旧写法（`{{歌爱雪}}` / 旧年份子页 / 少了 `|collapsed`）时
        # **改写成**目标写法，而不是跳过 —— 否则拆分后就换不上年份（用户 2026-09-30 报的）。
        results += insert_into_pages(work.name, group, progress=progress, call=list(calls),
                                     position=POSITION_AFTER_PRODUCER, drop_category=drop,
                                     rewrite=True)
    return results


__all__ = [
    "VocalistWork", "VocalistSong", "SongFact", "HallEntry",
    "RANK_HALL", "RANK_LEGEND", "RANK_MYTH", "RANK_BILLION", "RANK_OTHER",
    "STATIONS", "STATION_NICO", "STATION_YOUTUBE", "STATION_BILIBILI",
    "OTHER_UNHALL", "OTHER_YOUTUBE", "OTHER_UNHALL_NOTE",
    "hall_page_titles", "parse_hall_title", "parse_hall_page", "fetch_halls",
    "honor_ranks", "song_fact", "fetch_song_facts", "classify", "prepare_work",
    "load_existing", "parse_styles", "extract_relation", "extract_group_value",
    "belongs_to", "build_main_template", "build_year_page", "build_doc", "first_year",
    "template_family_pages", "merge_until_from_pages",
    "year_category", "category_page_title", "build_category_page", "fetch_category_titles",
    "template_links", "rank_counts", "page_specs", "output_path", "write_pages",
    "template_calls_for", "insert_into_pages_for", "effective_styles",
    "dropped_titles",
    "illustration_url", "download_illustration",
]
