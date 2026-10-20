"""从「来源链接」识别翻译者 / 翻译链接 / 来源，并尽量取回歌词
（歌词整理窗口「从链接填充」按钮用）。

实测结论（2026-09）：
  Vocaloid 中文歌词 wiki（AtWiki）https://w.atwiki.jp/vocaloidchly/pages/1234.html
      页面正文就是中日歌词（有的页只有译文），表头里写着「翻譯：某人」。
      解析交给 `utils/at_wiki.py`（与生成流程取 atwiki 同一套口径），
      所以「从链接填充」拿到的正文与自动取到的一模一样。
  网易云歌曲 https://music.163.com/#/song?id=2637441995
      `/api/song/lyric` 同时给出 transUser（贡献翻译者，如「白夜落星」）与 lyricUser（贡献歌词者）；
      取贡献翻译者，没有才退到贡献歌词者。公开接口里**没有**「滚动歌词贡献者」这一角色。
      同一接口的 `lrc.lyric` / `tlyric.lyric` 是原文与译文（LRC，带时间标签）→
      按时间标签**逐行穿插**成「一行原文、一行译文」（用户 2026-10-17 报《怨灵案件》：
      以前整块拼成两段，歌词页还得再猜行号）。
  b 站视频 https://www.bilibili.com/video/BV1pj421Q7V3
      `/x/web-interface/view` 的 data.owner{mid,name} 就是视频投稿者，主页 space.bilibili.com/{mid}；
      `data.desc`（简介）里常有歌词 —— 一起填进「待归类歌词」，不是歌词就用户自己删。
  b 站视频评论 https://www.bilibili.com/video/BV15x411S7d5?comment_on=1&comment_root_id=2479540599#reply2479540599
      先用 view 接口把 BV 换成 aid，再 `/x/v2/reply/reply?type=1&oid={aid}&root={评论id}`
      取 data.root.member（分享的是子评论时就找 data.replies 里 rpid 对上的那条）→ 评论者。
  b 站图文 / 笔记 / **专栏** / **动态** https://www.bilibili.com/opus/{id}（例 1012509995710808065）
      `/read/cv{id}`（旧专栏）、`/read/mobile?id={id}`、`t.bilibili.com/{id}` 都算这一类。
      笔记自己的接口都要登录态（`x/note/publish/info` 返回请求错误，dynamic/opus detail 返回 -352 风控），
      所以退成「抓页面 HTML」：优先读渲染出来的作者卡片（`opus-module-author__name`），
      再退回 SSR JSON 里 name/mid 成对出现的作者对象。
      来源名按站内写法区分：专栏（`/read/cv…` 与 `/opus/…`）写「bilibili专栏」，
      动态（`t.bilibili.com/…`）写「bilibili动态」（实测站内条目都这么写）。
  分享短链 https://b23.tv/xxxx
      App 里「分享」出来的链接十有八九是这个，直接解析认不出来 → 先跟一次重定向拿真链接再识别。
  评论链接里的子评论 https://www.bilibili.com/video/BVxxx?comment_root_id=111&comment_secondary_id=222
      子评论可能不在接口第一页（默认 20 条一页）→ 按 pn 往后翻几页找分享的那一条；
      都没找到才退回根评论的作者，并在提示里说明（免得静静填错人）。
  巴哈姆特創作大廳 https://home.gamer.com.tw/artwork.php?sn=6402210
      页面里就有作者卡片（`class="user-info-box"`）：`data-gamercard-userid` 是帐号，
      `class="caption-text primary"` 是昵称 → 投稿者；读不到卡片时退回 <title> 里的「xxx的創作」。
      作品正文（`class="article-content main"`，中日歌词都在这儿）一起填进「待归类歌词」。

只依赖 requests（经 utils.helpers.http_get），不引入新依赖。
"""
import logging
import re
from bisect import bisect_right
from collections import defaultdict
from html import unescape
from typing import Dict, List, Optional, Sequence, Tuple

from models.video import av_to_bv
from utils import identity
from utils.helpers import http_get
from utils.string import strip_credit_lines

REQUEST_TIMEOUT = 20

NETEASE_SOURCE_NAME = "网易云音乐"
BILIBILI_SOURCE_NAME = "bilibili"
BILIBILI_COMMENT_SOURCE_NAME = "bilibili视频评论区"
# 站内写法（实测）：专栏（`/read/cv…` 与 `/opus/…`）= 「bilibili专栏」；动态 = 「bilibili动态」
BILIBILI_ARTICLE_SOURCE_NAME = "bilibili专栏"
BILIBILI_DYNAMIC_SOURCE_NAME = "bilibili动态"
BAHAMUT_SOURCE_NAME = "巴哈姆特"
# Vocaloid 中文歌词 wiki（AtWiki）：站上条目「来源」栏就写这个名字
ATWIKI_SOURCE_NAME = "VOCALOID中文歌词wiki"

NETEASE_LYRIC_API = "https://music.163.com/api/song/lyric?os=pc&id={id}&lv=-1&kv=-1&tv=-1"
BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
# 新 av 号（十位以上）算不出 BV 号，只能让接口按 `aid` 查（见 models.video.get_bv）
BILIBILI_VIEW_AID_API = "https://api.bilibili.com/x/web-interface/view?aid={aid}"
# type=1 是视频，oid 必须是 aid（数字），root 是根评论 id；子评论分页：20 条一页（ps/pn）
BILIBILI_REPLY_API = ("https://api.bilibili.com/x/v2/reply/reply"
                      "?type=1&oid={oid}&root={root}&ps=20&pn={pn}")
# 子评论最多往后翻几页找「分享的那一条」（一页 20 条，三页够绝大多数情况）
# 分享的是根评论时也按这个上限把「回答」翻完（回答里可能接着写歌词）
BILIBILI_REPLY_PAGES = 3
# 最多把几条「像歌词的回答」接在评论正文后面（防呆：别把整串回复都塞进歌词栏）
BILIBILI_FOLLOW_UP_MAX = 5

# 专栏 / 图文 / 动态 id：`/opus/{id}`、`/note/{id}`、`/dynamic/{id}`
BILIBILI_NOTE_RE = re.compile(r"/(?:opus|note|dynamic)/(\d{6,})")
# 动态的另一种写法：`t.bilibili.com/{id}`
BILIBILI_DYNAMIC_HOST_RE = re.compile(r"t\.bilibili\.com/(\d{6,})")
# 分享短链（App 里点「分享」拿到的就是这个）
SHORT_LINK_RE = re.compile(r"^https?://(?:b23\.tv|bili2233\.cn)/[^\s?#]+")
# Vocaloid 中文歌词 wiki 的页面地址：`/vocaloidchly/pages/1234.html`（`?pageid=1234` 也认）
ATWIKI_PAGE_RE = re.compile(r"w\.atwiki\.jp/vocaloidchly/pages/(\d+)\.html")
ATWIKI_PAGEID_RE = re.compile(r"[?&]pageid=(\d+)")
# LRC 行首的时间标签 `[00:12.34]` 与元信息标签 `[by:某人]`
LRC_TAG_RE = re.compile(r"^\s*(?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]\s*)+")
LRC_STAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
LRC_META_RE = re.compile(r"^\s*\[[a-z]+:[^\]]*\]\s*$", re.IGNORECASE)
# 巴哈姆特創作大廳的文章正文容器（实测：`<div class="article-content main">…`）
BAHAMUT_ARTICLE_RE = re.compile(
    r'<div[^>]*class="[^"]*article-content main[^"]*"[^>]*>(.*?)</div>\s*</div>', re.DOTALL)
BILIBILI_DESC_MAX_LINES = 200          # 简介当歌词时最多取这么多行（防呆：别把整篇简介塞进去）

# 页面里的作者对象：图文页的 SSR JSON 形如
# {"name":"kmrsc_","name_render":null,"label":"","mid":3493120362678931,"jump_url":"//space.bilibili.com/…"}
# （b 站会把 / 转义成 \u002F；mid 有时是数字、有时是字符串；mid / name 的先后也不固定）
AUTHOR_NAME_FIRST_RE = re.compile(
    r'"name"\s*:\s*"(?P<name>[^"]{1,80})"[^{}]{0,200}?"mid"\s*:\s*"?(?P<mid>\d{1,20})')
AUTHOR_MID_FIRST_RE = re.compile(
    r'"mid"\s*:\s*"?(?P<mid>\d{1,20})[^{}]{0,200}?"name"\s*:\s*"(?P<name>[^"]{1,80})"')
OPUS_AUTHOR_NAME_RE = re.compile(r'opus-module-author__name[^>]*>([^<]{1,80})<')
SPACE_MID_RE = re.compile(r'space\.bilibili\.com(?:\\u002F|/)(\d{1,20})')
AUTHOR_ANCHORS = ('"module_author"', '"author"', '"owner"')

# 巴哈姆特創作大廳的作者卡片：帐号在 data-gamercard-userid，昵称在 caption-text primary
BAHAMUT_USERID_RE = re.compile(r'data-gamercard-userid="([A-Za-z0-9_]{2,30})"')
BAHAMUT_NAME_RE = re.compile(r'caption-text primary[^>]*>([^<]{1,40})<')
BAHAMUT_TITLE_USER_RE = re.compile(r"[-－]\s*([A-Za-z0-9_]{3,30})的創作")

UNKNOWN_LINK_ERROR = ("认不出这个链接：目前支持 Vocaloid 中文歌词 wiki、网易云歌曲、"
                      "bilibili 视频 / 视频评论 / 专栏 / 图文 / 动态、巴哈姆特創作大廳")
NETEASE_NO_USER_ERROR = "这个网易云链接里没有翻译者 / 歌词贡献者信息，请手动填写"
BILIBILI_ARTICLE_ERROR = "读不到这篇 bilibili 专栏 / 图文的作者，请手动填写"
BILIBILI_DYNAMIC_ERROR = "读不到这条 bilibili 动态的作者，请手动填写"
BILIBILI_COMMENT_ERROR = "读不到这条 bilibili 评论的作者（评论可能已删除），请手动填写"
BAHAMUT_ERROR = "读不到这篇巴哈姆特作品的作者，请手动填写"


class SourceError(Exception):
    """识别失败，消息直接给用户看。"""


# ---------------------------------------------------------------- 纯解析

def parse_netease_song(url: str) -> Optional[str]:
    """网易云歌曲 id：`/#/song?id=`、`/song?id=`、`y.music.163.com/m/song?id=` 都认。"""
    if "music.163.com" not in (url or ""):
        return None
    match = re.search(r"[#?&]id=(\d+)", url)
    return match.group(1) if match else None


def parse_bilibili_video(url: str) -> Optional[str]:
    """b 站视频号（BV 号；给的是 av 号就转成 BV 号，转不了的保留 `av…`）。"""
    text = url or ""
    match = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if match:
        return match.group(1)
    match = re.search(r"\bav(\d+)", text, re.IGNORECASE)
    if match:
        try:
            return av_to_bv("av" + match.group(1))
        except ValueError:
            # 新 av 号超出换算范围：原样返回，view 接口会按 aid 查
            return "av" + match.group(1)
    return None


def parse_bilibili_note(url: str) -> Optional[str]:
    """b 站专栏 / 图文 / 动态 id。

    `/opus/{id}`、`/note/{id}`、`/dynamic/{id}`、`t.bilibili.com/{id}`、`?note_id={id}` 直接返回 id；
    专栏（`/read/cv{id}`、`/read/mobile?id={id}`）返回 `cv{id}`。
    """
    text = url or ""
    match = BILIBILI_NOTE_RE.search(text)
    if match:
        return match.group(1)
    match = BILIBILI_DYNAMIC_HOST_RE.search(text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]note_id=(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"/read/mobile\b[^#\s]*[?&](?:id|aid)=(\d+)", text)
    if match:
        return "cv" + match.group(1)
    match = re.search(r"/read/cv(\d+)", text)
    if match:
        return "cv" + match.group(1)
    return None


def bilibili_note_is_dynamic(url: str) -> bool:
    """这条链接是「动态」（`t.bilibili.com/{id}` / `/dynamic/{id}`）还是专栏 / 图文。"""
    text = url or ""
    return "t.bilibili.com" in text or "/dynamic/" in text


def bilibili_note_url(note_id: str, dynamic: bool = False) -> str:
    """图文 / 专栏 / 动态写进 wikitext 的地址（动态现在也在 opus 下有同一页）。"""
    if note_id.startswith("cv"):
        return f"https://www.bilibili.com/read/{note_id}"
    return f"https://www.bilibili.com/opus/{note_id}"


def bilibili_note_fetch_urls(note_id: str) -> List[str]:
    """抓作者信息时依次试的页面地址（与写进 wikitext 的那个不一定相同）。

    实测（2026-09）：旧专栏链接 `/read/cv{id}` 会 301 到 `/read/cv{id}/` —— 那是一个
    **3KB 的 JS 外壳页**，里没有 SSR 数据 → 读不到作者（用户报的「专栏填充失败」就是这个）；
    而 `/read/mobile?id={id}` 与 `/read/cv{id}/` 都 301 到 `/opus/{真 id}` 的 SSR 页（含作者卡片）。
    所以先走 mobile 路由，不行再退到带斜杠的专栏路由。
    """
    if note_id.startswith("cv"):
        cv = note_id[2:]
        return [f"https://www.bilibili.com/read/mobile?id={cv}",
                f"https://www.bilibili.com/read/{note_id}/"]
    return [f"https://www.bilibili.com/opus/{note_id}"]


def netease_translator(payload: dict) -> Tuple[str, dict]:
    """网易云歌词接口里的翻译者：贡献翻译者（transUser）优先，其次贡献歌词者（lyricUser）。"""
    for key in ("transUser", "lyricUser"):
        user = (payload or {}).get(key) or {}
        name = str(user.get("nickname") or "").strip()
        if name:
            return name, user
    return "", {}


def netease_user_url(user: dict) -> str:
    """网易云用户主页（接口里 userid 是主页 id，老数据可能只有 id）。"""
    uid = (user or {}).get("userid") or (user or {}).get("id")
    return f"https://music.163.com/#/user/home?id={uid}" if uid else ""


def netease_song_url(song_id: str) -> str:
    return f"https://music.163.com/#/song?id={song_id}"


def bilibili_owner(data: dict) -> Tuple[str, str]:
    """view 接口 data 里的投稿者：(昵称, mid)。"""
    owner = (data or {}).get("owner") or {}
    return str(owner.get("name") or "").strip(), str(owner.get("mid") or "").strip()


def parse_bilibili_comment(url: str) -> Tuple[str, str, str]:
    """b 站视频评论链接 → (BV 号, 根评论 id, 被分享的那条的 id)。

    形如 `…/video/BV15x411S7d5?comment_on=1&comment_root_id=2479540599#reply2479540599`；
    子评论还有两种写法：`&comment_secondary_id=…`（App 有时不给 `#reply`）与 `#reply…`。
    普通视频链接（没有 comment_root_id / #reply / secondary_id）返回三个空串。
    """
    text = url or ""
    if ("comment_root_id=" not in text and "#reply" not in text
            and "comment_secondary_id=" not in text):
        return "", "", ""
    root = re.search(r"(?:^|[?&])comment_root_id=(\d+)", text)
    share = re.search(r"#reply(\d+)", text)
    secondary = re.search(r"(?:^|[?&])comment_secondary_id=(\d+)", text)
    root_id = root.group(1) if root else ""
    reply_id = (share.group(1) if share else
                (secondary.group(1) if secondary else ""))
    return parse_bilibili_video(text) or "", root_id, reply_id or root_id


def bilibili_comment_by_id(payload: dict, reply_id: str) -> Optional[Tuple[str, str]]:
    """按 rpid 在根评论与子评论里找那一条，返回 (昵称, mid)；没有返回 None。

    跟 `bilibili_comment_author` 的区别：它**不会**退回根评论 —— 「分享的子评论有没有找到」
    得能分清，否则会静静地把根评论的作者填进去。
    """
    if not reply_id:
        return None
    data = (payload or {}).get("data") or {}
    root = data.get("root") or {}
    for comment in [root, *(data.get("replies") or [])]:
        member = (comment or {}).get("member") or {}
        if str(comment.get("rpid") or "") == str(reply_id) and member.get("uname"):
            return str(member.get("uname")).strip(), str(member.get("mid") or "").strip()
    return None


def bilibili_comment_author(payload: dict, root_id: str, reply_id: str) -> Tuple[str, str]:
    """评论接口里的评论者：(昵称, mid)。

    分享的是子评论时（`#reply` 与 `comment_root_id` 不同）优先取那一条，
    取不到再退回根评论。
    """
    for wanted in (reply_id, root_id):
        found = bilibili_comment_by_id(payload, wanted)
        if found:
            return found
    member = (((payload or {}).get("data") or {}).get("root") or {}).get("member") or {}
    return str(member.get("uname") or "").strip(), str(member.get("mid") or "").strip()


def parse_bahamut_artwork(url: str) -> Optional[Tuple[str, str]]:
    """巴哈姆特创作链接 → (页面文件名, 作品号)。支持 artwork.php / creationDetail.php。"""
    text = url or ""
    if "gamer.com.tw" not in text:
        return None
    match = re.search(r"/(artwork|creationDetail)\.php\b[^#\s]*[?&]sn=(\d+)", text)
    if not match:
        return None
    return f"{match.group(1)}.php", match.group(2)


def bahamut_artwork_url(page: str, artwork_id: str) -> str:
    return f"https://home.gamer.com.tw/{page}?sn={artwork_id}"


def bahamut_home_url(account: str) -> str:
    return f"https://home.gamer.com.tw/{account}" if account else ""


def bahamut_author(html: str) -> Tuple[str, str]:
    """巴哈姆特创作页的作者：(昵称, 帐号)；读不到返回空串。

    作者卡片长这样（昵称就是页面上显示的那个名字，帐号跟在后面）：
        <div class="user-info-box"><a … data-gamercard-userid="账号">…
          <a … class="caption-text primary">昵称</a><a …>账号</a>
    没昵称时退到帐号；卡片完全读不到时退到 <title> 里的「xxx的創作」。
    """
    text = html or ""
    start = text.find('class="user-info-box"')
    scope = text[start:start + 1500] if start != -1 else ""
    userid = BAHAMUT_USERID_RE.search(scope) or BAHAMUT_USERID_RE.search(text)
    account = userid.group(1) if userid else ""
    if not account:
        title = BAHAMUT_TITLE_USER_RE.search(text)
        account = title.group(1) if title else ""
    name = BAHAMUT_NAME_RE.search(scope) if scope else None
    nickname = unescape(name.group(1)).strip() if name else ""
    return (nickname or account), account


def bilibili_space_url(mid: str) -> str:
    return f"https://space.bilibili.com/{mid}" if mid else ""


# ---------------------------------------------------------------- 歌词抽取

def parse_at_wiki_page(url: str) -> Optional[str]:
    """Vocaloid 中文歌词 wiki 的页面 id：`…/vocaloidchly/pages/1234.html`、`?pageid=1234` 都认。"""
    text = url or ""
    if "w.atwiki.jp" not in text:
        return None
    match = ATWIKI_PAGE_RE.search(text) or ATWIKI_PAGEID_RE.search(text)
    return match.group(1) if match else None


def at_wiki_page_url(page_id: str) -> str:
    return f"https://w.atwiki.jp/vocaloidchly/pages/{page_id}.html"


def lrc_text(lrc: str) -> str:
    """LRC → 纯歌词行：去掉 `[00:12.34]` 时间标签与 `[by:…]` 这类元信息，滤掉空行。"""
    lines = []
    for line in str(lrc or "").replace("\r\n", "\n").split("\n"):
        if LRC_META_RE.match(line):
            continue
        text = LRC_TAG_RE.sub("", line).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _lrc_entries(lrc: str) -> List[Tuple[int, str]]:
    """LRC → `(毫秒时间戳, 歌词行)`；元信息与空行丢掉，一行可以带多个时间标签。"""
    entries: List[Tuple[int, str]] = []
    for line in str(lrc or "").replace("\r\n", "\n").split("\n"):
        if not line.strip() or LRC_META_RE.match(line):
            continue
        text = LRC_TAG_RE.sub("", line).strip()
        if not text:
            continue
        stamps = list(LRC_STAMP_RE.finditer(line))
        if not stamps:                       # 没有时间标签的行（纯文本歌词）跳过，由调用方回退
            continue
        for stamp in stamps:
            minutes, seconds = int(stamp.group(1)), int(stamp.group(2))
            fraction = int((stamp.group(3) or "0").ljust(3, "0")[:3])
            entries.append((minutes * 60000 + seconds * 1000 + fraction, text))
    return entries


def _pair_lrc_lines(original: List[Tuple[int, str]],
                    translated: List[Tuple[int, str]]) -> str:
    """按时间标签把原文与译文**逐行穿插**：一行日文、一行中文。

    同一时间标签上有好几行（开头常同时标着 `作詞：…` / `作曲：…` / 第一句歌词）时，
    译文跟在**最后**那一行后面，前面的署名行随后会被 `strip_credit_lines()` 摘掉。
    译文比原文多出来的行（译者把一句拆成两行）贴在它前一句原文后面；
    原文没有译文的行就只留原文，不补空行。
    """
    original = sorted(original, key=lambda entry: entry[0])       # 稳定排序，同一时刻保持原序
    translated = sorted(translated, key=lambda entry: entry[0])
    starts = [stamp for stamp, _ in original]
    after: Dict[int, List[str]] = defaultdict(list)
    for stamp, text in translated:
        after[bisect_right(starts, stamp) - 1].append(text)
    result: List[str] = list(after.pop(-1, []))          # 早于第一句原文的译文放在最前
    for index, (_, text) in enumerate(original):
        result.append(text)
        result.extend(after.pop(index, []))
    return "\n".join(result)


def netease_lyrics(payload: dict) -> str:
    """网易云歌词接口 → 待归类歌词（**一行原文、一行译文**）。

    接口给的 `lrc.lyric` 是原文、`tlyric.lyric` 是翻译，两边都带时间标签。
    以前先各自剥掉时间标签、再整块拼起来（原文一段、译文一段），歌词页拿到的
    「待归类歌词」就成了两段：中文全在日文后面，自动 / AI 分栏都得再猜一次行号
    （用户 2026-10-17 报的《怨灵案件》）。现在按时间标签逐行穿插，
    直接就是「日文一行、中文一行」，分栏与排版都不用再猜。

    只有一边、或没有时间标签时退回原来的做法（原文 + 空行 + 译文）。
    """
    lrc = ((payload or {}).get("lrc") or {}).get("lyric")
    tlyric = ((payload or {}).get("tlyric") or {}).get("lyric")
    original, translated = _lrc_entries(lrc), _lrc_entries(tlyric)
    if original and translated:
        return _pair_lrc_lines(original, translated)
    parts = [part for part in (lrc_text(lrc), lrc_text(tlyric)) if part]
    return "\n\n".join(parts)


def bilibili_video_lyrics(data: dict) -> str:
    """b 站视频 → 简介里的歌词（很多转载 / 歌词向视频把歌词就写在简介里）。

    简介是「笔记式」文本：`sm号`、宣传语、本家 / offvocal 链接和歌词混在一起，
    所以这里按 `strip_credit_lines(..., description=True)` 清一遍（连单独的整句宣传语
    也删，见 utils.string 里的说明）。
    """
    desc = str((data or {}).get("desc") or "").replace("\r\n", "\n").strip()
    text = "\n".join(desc.split("\n")[:BILIBILI_DESC_MAX_LINES])
    return strip_credit_lines(text, description=True)


def bilibili_comment_message(payload: dict, reply_id: str, root_id: str) -> str:
    """评论接口里的评论正文：优先分享的那一条，找不到才用根评论。"""
    data = (payload or {}).get("data") or {}
    root = data.get("root") or {}
    for wanted in (reply_id, root_id):
        if not wanted:
            continue
        for comment in [root, *(data.get("replies") or [])]:
            if str((comment or {}).get("rpid") or "") == str(wanted):
                return str(((comment or {}).get("content") or {}).get("message") or "").strip()
    return ""


def comment_follow_up(message: str) -> str:
    """子评论里**像歌词**的那部分；闲聊返回空串。

    译者常把歌词分两次写：根评论一段、下面自己的回答接着写（用户 2026-10-16 给的
    BV1ZG411472n 就是这样，后半首在同一条评论的回复里）。判定：
    清掉杂物行后还剩两行以上才算续写，「回复 @某人：谢谢！」这种单行闲聊丢掉。
    """
    text = strip_credit_lines(str(message or ""))
    lines = [line for line in text.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    head = lines[0].lstrip()
    if head.startswith("@") or head.startswith("回复 @"):
        return ""
    return text


def bilibili_comment_lyrics(payloads: Sequence[dict],
                            root_id: str, reply_id: str) -> Tuple[str, int]:
    """评论正文 + 它下面像歌词的回答 → `(待归类歌词, 接上的回答条数)`。

    分享的是根评论时，把这条评论下的回答（可跨页，`payloads` 按 pn 顺序给）里像歌词的
    部分接在后面 —— 只取根评论会少半首歌。分享的是子评论时不接兄弟评论（那是别人的话）。
    """
    primary = ""
    for payload in payloads:
        primary = bilibili_comment_message(payload, reply_id, root_id)
        if primary:
            break
    blocks = [primary] if primary else []
    follow_ups = 0
    if primary and str(reply_id or "") == str(root_id or ""):
        for payload in payloads:
            if follow_ups >= BILIBILI_FOLLOW_UP_MAX:
                break
            for reply in (payload or {}).get("data", {}).get("replies") or []:
                if follow_ups >= BILIBILI_FOLLOW_UP_MAX:
                    break
                follow = comment_follow_up(((reply or {}).get("content") or {}).get("message"))
                if follow:
                    blocks.append(follow)
                    follow_ups += 1
    return "\n\n".join(blocks), follow_ups


def bahamut_article_text(html: str) -> str:
    """巴哈姆特创作页 → 作品正文（`article-content main` 那个容器）。

    容器里是富文本：`<br>` 与 `</p>` 当换行，其余标签去掉，最后滤掉空行。
    """
    match = BAHAMUT_ARTICLE_RE.search(html or "")
    if not match:
        return ""
    body = match.group(1)
    body = re.sub(r"(?s)<(script|style)\b.*?</\1>", "", body)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"</(?:p|div|li)>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", "", body)
    lines = [unescape(line).strip() for line in body.split("\n")]
    return "\n".join(line for line in lines if line)


def _author_pair(text: str, start: int = 0, end: int = -1) -> Tuple[str, str]:
    """在 text[start:end] 里找 name / mid 成对出现的作者对象 → (昵称, mid)。"""
    window = text[start:] if end < 0 else text[start:end]
    match = AUTHOR_NAME_FIRST_RE.search(window) or AUTHOR_MID_FIRST_RE.search(window)
    if not match:
        return "", ""
    return unescape(match["name"]).strip(), match["mid"]


def bilibili_page_author(html: str) -> Tuple[str, str]:
    """从图文 / 笔记页的 HTML 里读作者：(昵称, mid)；读不到返回空串。

    优先读渲染出来的作者卡片（`opus-module-author__name`，一定是本条页面的作者），
    再用同名搜到它在 SSR JSON 里的 mid；没有卡片时才在 `module_author` 等字段附近
    找 name / mid 成对出现的对象，避免把正文里提到的别人当成作者。
    """
    text = html or ""
    card = OPUS_AUTHOR_NAME_RE.search(text)
    if card:
        name = unescape(card.group(1)).strip()
        if name:
            pair = re.search(r'"name"\s*:\s*"' + re.escape(name) +
                             r'"[^{}]{0,300}?"mid"\s*:\s*"?(\d{1,20})', text)
            if pair:
                return name, pair.group(1)
            space = SPACE_MID_RE.search(text)
            return name, (space.group(1) if space else "")
    for anchor in AUTHOR_ANCHORS:
        start = text.find(anchor)
        while start != -1:
            name, mid = _author_pair(text, start, start + 400)
            if name:
                return name, mid
            start = text.find(anchor, start + 1)
    return _author_pair(text)


# ---------------------------------------------------------------- 联网填充

def _headers(referer: str) -> Dict[str, str]:
    """只给 Referer：UA 由 `utils/helpers.http_get` 统一管（先工具 UA、被挡再降级）。"""
    return {"Referer": referer}


def _fetch(url: str, referer: str, **kwargs):
    # 网易云 / b 站都是国内站点，按 b 站接口的老做法直连（不走代理）
    logging.info("读取来源页面：%s", url)
    response = http_get(url, use_proxy=False, headers=_headers(referer),
                        timeout=REQUEST_TIMEOUT, **kwargs)
    response.raise_for_status()
    return response


def resolve_short_link(url: str) -> str:
    """b23.tv / bili2233.cn 短链 → 真链接（App 里「分享」出来的链接就是这种）。

    只要响应头就够：`stream=True` 不下载正文，requests 会自己跟随重定向，`response.url`
    就是最终地址。解析不出来（断网 / 服务不认）就原样返回，交给后面的识别报「认不出」。
    """
    try:
        response = _fetch(url, "https://www.bilibili.com/", stream=True)
        final = str(getattr(response, "url", "") or "")
        response.close()
    except Exception as e:                       # noqa: BLE001 - 解析失败不该让整个填充报错
        logging.info("短链解析失败（%s）：%s", url, e)
        return url
    if not final.startswith("http"):              # 测试里的 mock / 意外情况：原样返回
        return url
    logging.info("短链解析为：%s", final)
    return final


def _ok(translator: str, translator_url: str, source_name: str, source_url: str,
        message: str, lyrics: str = "") -> dict:
    """填充结果：翻译者 / 翻译链接 / 来源三栏，外加**待归类歌词**（有就一起给）。

    歌词先过 `utils.string.strip_credit_lines()`：来源页 / 简介 / LRC 常把「作詞：…／
    作曲：…／編曲：…／歌：…」这类署名行与链接混在歌词里，摘干净再交给歌词页。
    """
    return {"ok": True, "translator": translator, "translatorUrl": translator_url,
            "sourceName": source_name, "sourceUrl": source_url, "message": message,
            "lyrics": strip_credit_lines(lyrics)}


def fill_from_at_wiki(page_id: str) -> dict:
    """Vocaloid 中文歌词 wiki（AtWiki）：翻译者 = 页面里的「翻譯」，歌词 = 页面正文。

    与生成流程取 atwiki 走同一套解析（`utils/at_wiki.py`），所以「从链接填充」拿到的
    正文与自动取到的一模一样：日文原文 + 中文译文 + 表头。
    """
    from utils import at_wiki
    url = at_wiki_page_url(page_id)
    lyrics = at_wiki.get_page_lyrics(url)
    if lyrics is None:
        return {"ok": False, "error": f"读不到这个页面（可能不是歌词页）：{url}"}
    translator = lyrics.translator or ""
    body = (lyrics.lyrics_chs or "").strip()
    note = "" if translator else "；页面里没写「翻譯」，翻译者请手动填写"
    if not body:
        return {"ok": False, "error": "这个页面里没有歌词正文，请确认链接指向歌词页"}
    return _ok(translator, "", ATWIKI_SOURCE_NAME, url,
               f"已从 {ATWIKI_SOURCE_NAME} 填入歌词" +
               (f"，翻译者：{translator}" if translator else "") + note,
               lyrics=body)


def fill_from_netease(song_id: str) -> dict:
    """网易云歌曲：翻译者 = 贡献翻译者（其次贡献歌词者），歌词 = 原文 + 译文。"""
    payload = _fetch(NETEASE_LYRIC_API.format(id=song_id), "https://music.163.com/").json()
    name, user = netease_translator(payload)
    body = netease_lyrics(payload)
    if not name and not body:
        return {"ok": False, "error": NETEASE_NO_USER_ERROR}
    if not name:
        return _ok("", "", NETEASE_SOURCE_NAME, netease_song_url(song_id),
                   "已填入歌词；这个网易云链接里没有翻译者 / 歌词贡献者信息，请手动填写",
                   lyrics=body)
    role = "贡献翻译者" if (payload or {}).get("transUser") else "贡献歌词者"
    message = f"已按{role}填入翻译者：{name}"
    if body:
        message += "，并填入歌词"
    return _ok(name, netease_user_url(user), NETEASE_SOURCE_NAME, netease_song_url(song_id),
               message, lyrics=body)


def bilibili_view(bvid: str) -> dict:
    """b 站 view 接口的 data（拿去 aid / owner）；接口报错抛 SourceError。

    `bvid` 是 `av…` 时改走 `aid` 参数：新 av 号算不出 BV 号（见 `parse_bilibili_video`）。
    """
    key = str(bvid or "").strip()
    if re.fullmatch(r"av\d+", key, re.IGNORECASE):
        endpoint = BILIBILI_VIEW_AID_API.format(aid=key[2:])
    else:
        endpoint = BILIBILI_VIEW_API.format(bvid=key)
    payload = _fetch(endpoint, "https://www.bilibili.com/").json()
    if payload.get("code") not in (0, None):
        raise SourceError(f"b 站接口返回 {payload.get('code')}：{payload.get('message')}")
    return payload.get("data") or {}


def fill_from_bilibili_video(bvid: str) -> dict:
    """b 站视频：翻译者 = 视频投稿者，歌词 = 简介（歌词向视频常把歌词写在简介里）。

    简介清完（去掉 sm 号 / 署名 / 宣传语 / 链接）什么也不剩时，明说「简介里没有歌词」，
    别让用户以为已经填好了。
    """
    data = bilibili_view(bvid)
    name, mid = bilibili_owner(data)
    body = bilibili_video_lyrics(data)
    if not name and not body:
        return {"ok": False, "error": "这个 b 站视频链接里没有投稿者信息，请手动填写"}
    message = f"已按视频投稿者填入翻译者：{name}" if name else \
        "这个视频没有投稿者信息，翻译者请手动填写"
    if body:
        message += "；简介已填入「待归类歌词」（若里面不是歌词，直接改掉即可）"
    elif str(data.get("desc") or "").strip():
        message += "；简介里没有歌词（sm 号 / 署名 / 宣传语之类已经清掉，没有剩下正文）"
    return _ok(name, bilibili_space_url(mid), BILIBILI_SOURCE_NAME,
               f"https://www.bilibili.com/video/{bvid}", message, lyrics=body)


def fill_from_bilibili_comment(bvid: str, root_id: str, reply_id: str) -> dict:
    """b 站视频评论：翻译者 = 评论者，来源 = bilibili视频评论区。

    歌词 = 分享的那条评论（没翻到才是根评论）**加上它下面像歌词的回答** —— 译者常把
    后半首歌写在自己的回复里（用户 2026-10-16 给的 BV1ZG411472n）。
    子评论可能不在接口第一页（默认 20 条一页，`ps=20&pn=1`）→ 按 pn 往后翻几页找**分享的那一条**；
    分享的是根评论时按总条数把回答也翻完（上限 `BILIBILI_REPLY_PAGES` 页）；
    都没找到才退回根评论的作者，并在提示里说明（免得静静填错人）。
    """
    aid = str(bilibili_view(bvid).get("aid") or "")
    if not aid:
        return {"ok": False, "error": "拿不到这个视频的 aid，无法读取评论"}
    first: Optional[dict] = None
    found: Optional[Tuple[str, str]] = None
    payloads: List[dict] = []                 # 取过的评论页（根评论 + 它的回答可能跨页）
    for pn in range(1, BILIBILI_REPLY_PAGES + 1):
        payload = _fetch(BILIBILI_REPLY_API.format(oid=aid, root=root_id or reply_id, pn=pn),
                         "https://www.bilibili.com/").json()
        if payload.get("code") not in (0, None):
            raise SourceError(f"b 站评论接口返回 {payload.get('code')}：{payload.get('message')}")
        first = first or payload
        payloads.append(payload)
        found = bilibili_comment_by_id(payload, reply_id)
        if found:
            if str(reply_id or "") != str(root_id or ""):
                break                         # 找到分享的那条子评论了
            page = (payload.get("data") or {}).get("page") or {}
            size = max(1, int(page.get("size") or 20))
            if pn * size >= int(page.get("count") or 0):
                break                         # 根评论的回答已经翻完
            continue
        replies = (payload.get("data") or {}).get("replies") or []
        if reply_id == root_id or not replies:
            break                     # 分享的就是根评论 / 这页没有子评论：不用再翻
    note = ""
    if found is None:
        found = bilibili_comment_by_id(first, root_id) or bilibili_comment_author(
            first, root_id, reply_id)
        if found and reply_id != root_id:
            note = "（没翻到你分享的那条子评论，按根评论的作者填入）"
    if not found or not found[0]:
        return {"ok": False, "error": BILIBILI_COMMENT_ERROR}
    name, mid = found
    share_url = f"https://www.bilibili.com/video/{bvid}?comment_on=1&comment_root_id={root_id}"
    if reply_id:
        share_url += f"#reply{reply_id}"
    message = f"已按评论者填入翻译者：{name}{note}"
    body, follow_ups = bilibili_comment_lyrics(payloads or [first or {}], root_id, reply_id)
    if body:
        message += "；评论正文已填入「待归类歌词」"
        if follow_ups:
            message += f"（含这条评论下面的 {follow_ups} 条回答）"
    return _ok(name, bilibili_space_url(mid), BILIBILI_COMMENT_SOURCE_NAME, share_url,
               message, lyrics=body)


def fill_from_bilibili_note(note_id: str, dynamic: bool = False) -> dict:
    """b 站专栏 / 图文 / 动态：翻译者 = 作者（页面 HTML 里的作者卡片 / SSR JSON）。

    来源名按站内写法区分：专栏写「bilibili专栏」、动态写「bilibili动态」（用户 2026-09-29 要求）。
    页面地址见 `bilibili_note_fetch_urls`：旧专栏先走 mobile 路由绕开 JS 外壳页。
    """
    name = mid = ""
    for fetch_url in bilibili_note_fetch_urls(note_id):
        html = _fetch(fetch_url, "https://www.bilibili.com/").text
        name, mid = bilibili_page_author(html)
        if name:
            break                     # 外壳页里读不到，换一个路由再试
    if not name:
        return {"ok": False, "error": BILIBILI_DYNAMIC_ERROR if dynamic else BILIBILI_ARTICLE_ERROR}
    role = "动态作者" if dynamic else "专栏作者"
    return _ok(name, bilibili_space_url(mid),
               BILIBILI_DYNAMIC_SOURCE_NAME if dynamic else BILIBILI_ARTICLE_SOURCE_NAME,
               bilibili_note_url(note_id), f"已按{role}填入翻译者：{name}")


def fill_from_bahamut(page: str, artwork_id: str) -> dict:
    """巴哈姆特創作大廳：翻译者 = 投稿者（昵称，其次帐号），歌词 = 作品正文。"""
    url = bahamut_artwork_url(page, artwork_id)
    html = _fetch(url, "https://home.gamer.com.tw/").text
    name, account = bahamut_author(html)
    if not name:
        return {"ok": False, "error": BAHAMUT_ERROR}
    return _ok(name, bahamut_home_url(account), BAHAMUT_SOURCE_NAME, url,
               f"已按投稿者填入翻译者：{name}", lyrics=bahamut_article_text(html))


def fill_source(url: str) -> dict:
    """按来源链接自动识别翻译者 / 翻译链接 / 来源，并尽量把歌词也取回来。

    成功：`{ok: True, translator, translatorUrl, sourceName, sourceUrl, message, lyrics}`
    （`lyrics` 是给「待归类歌词」栏的原文 + 译文；取不到就是空串。里面已经去掉
    `作詞：…` 这类资讯行，见 `utils.string.strip_credit_lines()`）
    失败：`{ok: False, error}`
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "请先填写来源链接"}
    try:
        if SHORT_LINK_RE.match(url):
            url = resolve_short_link(url)          # App 分享的 b23.tv 短链先还原成真链接
        page_id = parse_at_wiki_page(url)
        if page_id:
            return fill_from_at_wiki(page_id)
        song_id = parse_netease_song(url)
        if song_id:
            return fill_from_netease(song_id)
        bahamut = parse_bahamut_artwork(url)
        if bahamut:
            return fill_from_bahamut(*bahamut)
        # 评论链接里也带 BV 号，必须先于视频判断
        bvid, root_id, reply_id = parse_bilibili_comment(url)
        if root_id or reply_id:
            return fill_from_bilibili_comment(bvid, root_id, reply_id)
        note_id = parse_bilibili_note(url)
        if note_id:
            return fill_from_bilibili_note(note_id, bilibili_note_is_dynamic(url))
        bvid = parse_bilibili_video(url)
        if bvid:
            return fill_from_bilibili_video(bvid)
        if "w.atwiki.jp" in url:
            return {"ok": False,
                    "error": f"只支持 {ATWIKI_SOURCE_NAME}（w.atwiki.jp/vocaloidchly）的页面链接"}
    except SourceError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                       # 网络 / 解析失败都不该让窗口崩掉
        logging.error("读取来源链接失败：%s", e, exc_info=e)
        return {"ok": False, "error": f"读取失败：{e}"}
    return {"ok": False, "error": UNKNOWN_LINK_ERROR}
