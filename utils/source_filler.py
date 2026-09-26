"""从「来源链接」识别翻译者 / 翻译链接 / 来源（歌词整理窗口的「从链接填充」按钮用）。

实测结论（2026-09）：
  网易云歌曲 https://music.163.com/#/song?id=2637441995
      `/api/song/lyric` 同时给出 transUser（贡献翻译者，如「白夜落星」）与 lyricUser（贡献歌词者）；
      取贡献翻译者，没有才退到贡献歌词者。公开接口里**没有**「滚动歌词贡献者」这一角色。
  b 站视频 https://www.bilibili.com/video/BV1pj421Q7V3
      `/x/web-interface/view` 的 data.owner{mid,name} 就是视频投稿者，主页 space.bilibili.com/{mid}。
  b 站视频评论 https://www.bilibili.com/video/BV15x411S7d5?comment_on=1&comment_root_id=2479540599#reply2479540599
      先用 view 接口把 BV 换成 aid，再 `/x/v2/reply/reply?type=1&oid={aid}&root={评论id}`
      取 data.root.member（分享的是子评论时就找 data.replies 里 rpid 对上的那条）→ 评论者。
  b 站图文 / 笔记 https://www.bilibili.com/opus/{id}
      笔记自己的接口都要登录态（`x/note/publish/info` 返回请求错误，dynamic/opus detail 返回 -352 风控），
      所以退成「抓页面 HTML」：优先读渲染出来的作者卡片（`opus-module-author__name`），
      再退回 SSR JSON 里 name/mid 成对出现的作者对象。
  巴哈姆特創作大廳 https://home.gamer.com.tw/artwork.php?sn=6402210
      页面里就有作者卡片（`class="user-info-box"`）：`data-gamercard-userid` 是帐号，
      `class="caption-text primary"` 是昵称 → 投稿者；读不到卡片时退回 <title> 里的「xxx的創作」。

只依赖 requests（经 utils.helpers.http_get），不引入新依赖。
"""
import logging
import re
from html import unescape
from typing import Dict, Optional, Tuple

from models.video import av_to_bv
from utils.helpers import http_get

REQUEST_TIMEOUT = 20
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

NETEASE_SOURCE_NAME = "网易云音乐"
BILIBILI_SOURCE_NAME = "bilibili"
BILIBILI_COMMENT_SOURCE_NAME = "bilibili视频评论区"
BAHAMUT_SOURCE_NAME = "巴哈姆特"

NETEASE_LYRIC_API = "https://music.163.com/api/song/lyric?os=pc&id={id}&lv=-1&kv=-1&tv=-1"
BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
# type=1 是视频，oid 必须是 aid（数字），root 是根评论 id
BILIBILI_REPLY_API = "https://api.bilibili.com/x/v2/reply/reply?type=1&oid={oid}&root={root}&ps=20&pn=1"

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

UNKNOWN_LINK_ERROR = "认不出这个链接：目前支持网易云歌曲、bilibili 视频 / 评论 / 图文笔记、巴哈姆特創作大廳"
NETEASE_NO_USER_ERROR = "这个网易云链接里没有翻译者 / 歌词贡献者信息，请手动填写"
BILIBILI_NOTE_ERROR = "读不到这条 bilibili 笔记的作者（笔记接口需要登录态），请手动填写"
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
    """b 站视频号（BV 号；给的是 av 号就转成 BV 号）。"""
    text = url or ""
    match = re.search(r"(BV[0-9A-Za-z]{10})", text)
    if match:
        return match.group(1)
    match = re.search(r"\bav(\d+)", text, re.IGNORECASE)
    if match:
        return av_to_bv("av" + match.group(1))
    return None


def parse_bilibili_note(url: str) -> Optional[str]:
    """b 站图文 / 笔记 id：`/opus/{id}`、`/note/{id}`、`?note_id={id}`；专栏返回 `cv{id}`。"""
    text = url or ""
    match = re.search(r"/(?:opus|note|dynamic)/(\d{6,})", text)
    if match:
        return match.group(1)
    match = re.search(r"[?&]note_id=(\d+)", text)
    if match:
        return match.group(1)
    match = re.search(r"/read/cv(\d+)", text)
    if match:
        return "cv" + match.group(1)
    return None


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
    普通视频链接（没有 comment_root_id / #reply）返回三个空串。同一分享链接里两个 id 相同。
    """
    text = url or ""
    if "comment_root_id=" not in text and "#reply" not in text:
        return "", "", ""
    root = re.search(r"(?:^|[?&])comment_root_id=(\d+)", text)
    share = re.search(r"#reply(\d+)", text)
    root_id = root.group(1) if root else ""
    reply_id = share.group(1) if share else ""
    return parse_bilibili_video(text) or "", root_id, reply_id or root_id


def bilibili_comment_author(payload: dict, root_id: str, reply_id: str) -> Tuple[str, str]:
    """评论接口里的评论者：(昵称, mid)。

    分享的是子评论时（`#reply` 与 `comment_root_id` 不同）优先取那一条，
    取不到再退回根评论。
    """
    data = (payload or {}).get("data") or {}
    root = data.get("root") or {}
    comments = [root, *(data.get("replies") or [])]
    for wanted in (reply_id, root_id):
        if not wanted:
            continue
        for comment in comments:
            member = (comment or {}).get("member") or {}
            if str(comment.get("rpid") or "") == wanted and member.get("uname"):
                return str(member.get("uname")).strip(), str(member.get("mid") or "").strip()
    member = root.get("member") or {}
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
    return {"User-Agent": USER_AGENT, "Referer": referer}


def _fetch(url: str, referer: str):
    # 网易云 / b 站都是国内站点，按 b 站接口的老做法直连（不走代理）
    logging.info("读取来源页面：%s", url)
    response = http_get(url, use_proxy=False, headers=_headers(referer), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response


def _ok(translator: str, translator_url: str, source_name: str, source_url: str,
        message: str) -> dict:
    return {"ok": True, "translator": translator, "translatorUrl": translator_url,
            "sourceName": source_name, "sourceUrl": source_url, "message": message}


def fill_from_netease(song_id: str) -> dict:
    """网易云歌曲：翻译者 = 贡献翻译者（其次贡献歌词者）。"""
    payload = _fetch(NETEASE_LYRIC_API.format(id=song_id), "https://music.163.com/").json()
    name, user = netease_translator(payload)
    if not name:
        return {"ok": False, "error": NETEASE_NO_USER_ERROR}
    role = "贡献翻译者" if (payload or {}).get("transUser") else "贡献歌词者"
    return _ok(name, netease_user_url(user), NETEASE_SOURCE_NAME, netease_song_url(song_id),
               f"已按{role}填入翻译者：{name}")


def bilibili_view(bvid: str) -> dict:
    """b 站 view 接口的 data（拿去 aid / owner）；接口报错抛 SourceError。"""
    payload = _fetch(BILIBILI_VIEW_API.format(bvid=bvid), "https://www.bilibili.com/").json()
    if payload.get("code") not in (0, None):
        raise SourceError(f"b 站接口返回 {payload.get('code')}：{payload.get('message')}")
    return payload.get("data") or {}


def fill_from_bilibili_video(bvid: str) -> dict:
    """b 站视频：翻译者 = 视频投稿者。"""
    name, mid = bilibili_owner(bilibili_view(bvid))
    if not name:
        return {"ok": False, "error": "这个 b 站视频链接里没有投稿者信息，请手动填写"}
    return _ok(name, bilibili_space_url(mid), BILIBILI_SOURCE_NAME,
               f"https://www.bilibili.com/video/{bvid}",
               f"已按视频投稿者填入翻译者：{name}")


def fill_from_bilibili_comment(bvid: str, root_id: str, reply_id: str) -> dict:
    """b 站视频评论：翻译者 = 评论者，来源 = bilibili视频评论区。"""
    aid = str(bilibili_view(bvid).get("aid") or "")
    if not aid:
        return {"ok": False, "error": "拿不到这个视频的 aid，无法读取评论"}
    payload = _fetch(BILIBILI_REPLY_API.format(oid=aid, root=root_id or reply_id),
                     "https://www.bilibili.com/").json()
    if payload.get("code") not in (0, None):
        raise SourceError(f"b 站评论接口返回 {payload.get('code')}：{payload.get('message')}")
    name, mid = bilibili_comment_author(payload, root_id, reply_id)
    if not name:
        return {"ok": False, "error": BILIBILI_COMMENT_ERROR}
    share_url = f"https://www.bilibili.com/video/{bvid}?comment_on=1&comment_root_id={root_id}"
    if reply_id:
        share_url += f"#reply{reply_id}"
    return _ok(name, bilibili_space_url(mid), BILIBILI_COMMENT_SOURCE_NAME, share_url,
               f"已按评论者填入翻译者：{name}")


def fill_from_bilibili_note(note_id: str) -> dict:
    """b 站图文 / 笔记：翻译者 = 笔记撰写者（页面 HTML 里的作者卡片 / SSR JSON）。"""
    url = f"https://www.bilibili.com/opus/{note_id}" if not note_id.startswith("cv") \
        else f"https://www.bilibili.com/read/{note_id}"
    html = _fetch(url, "https://www.bilibili.com/").text
    name, mid = bilibili_page_author(html)
    if not name:
        return {"ok": False, "error": BILIBILI_NOTE_ERROR}
    return _ok(name, bilibili_space_url(mid), BILIBILI_SOURCE_NAME, url,
               f"已按笔记撰写者填入翻译者：{name}")


def fill_from_bahamut(page: str, artwork_id: str) -> dict:
    """巴哈姆特創作大廳：翻译者 = 投稿者（昵称，其次帐号）。"""
    url = bahamut_artwork_url(page, artwork_id)
    html = _fetch(url, "https://home.gamer.com.tw/").text
    name, account = bahamut_author(html)
    if not name:
        return {"ok": False, "error": BAHAMUT_ERROR}
    return _ok(name, bahamut_home_url(account), BAHAMUT_SOURCE_NAME, url,
               f"已按投稿者填入翻译者：{name}")


def fill_source(url: str) -> dict:
    """按来源链接自动识别翻译者 / 翻译链接 / 来源，返回给界面的字典。

    成功：`{ok: True, translator, translatorUrl, sourceName, sourceUrl, message}`
    失败：`{ok: False, error}`
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "请先填写来源链接"}
    try:
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
            return fill_from_bilibili_note(note_id)
        bvid = parse_bilibili_video(url)
        if bvid:
            return fill_from_bilibili_video(bvid)
    except SourceError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                       # 网络 / 解析失败都不该让窗口崩掉
        logging.error("读取来源链接失败：%s", e, exc_info=e)
        return {"ok": False, "error": f"读取失败：{e}"}
    return {"ok": False, "error": UNKNOWN_LINK_ERROR}
