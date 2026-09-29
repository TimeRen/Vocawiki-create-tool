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

只依赖 requests（经 utils.helpers.http_get），不引入新依赖。
"""
import logging
import re
from html import unescape
from typing import Dict, Optional, Tuple

from models.video import av_to_bv
from utils import identity
from utils.helpers import http_get

REQUEST_TIMEOUT = 20

NETEASE_SOURCE_NAME = "网易云音乐"
BILIBILI_SOURCE_NAME = "bilibili"
BILIBILI_COMMENT_SOURCE_NAME = "bilibili视频评论区"
# 站内写法（实测）：专栏（`/read/cv…` 与 `/opus/…`）= 「bilibili专栏」；动态 = 「bilibili动态」
BILIBILI_ARTICLE_SOURCE_NAME = "bilibili专栏"
BILIBILI_DYNAMIC_SOURCE_NAME = "bilibili动态"
BAHAMUT_SOURCE_NAME = "巴哈姆特"

NETEASE_LYRIC_API = "https://music.163.com/api/song/lyric?os=pc&id={id}&lv=-1&kv=-1&tv=-1"
BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
# type=1 是视频，oid 必须是 aid（数字），root 是根评论 id；子评论分页：20 条一页（ps/pn）
BILIBILI_REPLY_API = ("https://api.bilibili.com/x/v2/reply/reply"
                      "?type=1&oid={oid}&root={root}&ps=20&pn={pn}")
# 子评论最多往后翻几页找「分享的那一条」（一页 20 条，三页够绝大多数情况）
BILIBILI_REPLY_PAGES = 3

# 专栏 / 图文 / 动态 id：`/opus/{id}`、`/note/{id}`、`/dynamic/{id}`
BILIBILI_NOTE_RE = re.compile(r"/(?:opus|note|dynamic)/(\d{6,})")
# 动态的另一种写法：`t.bilibili.com/{id}`
BILIBILI_DYNAMIC_HOST_RE = re.compile(r"t\.bilibili\.com/(\d{6,})")
# 分享短链（App 里点「分享」拿到的就是这个）
SHORT_LINK_RE = re.compile(r"^https?://(?:b23\.tv|bili2233\.cn)/[^\s?#]+")

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

UNKNOWN_LINK_ERROR = ("认不出这个链接：目前支持网易云歌曲、bilibili 视频 / 视频评论 / "
                      "专栏 / 图文 / 动态、巴哈姆特創作大廳")
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
    """b 站视频评论：翻译者 = 评论者，来源 = bilibili视频评论区。

    子评论可能不在接口第一页（默认 20 条一页，`ps=20&pn=1`）→ 按 pn 往后翻几页找**分享的那一条**；
    都没找到才退回根评论的作者，并在提示里说明（免得静静填错人）。
    """
    aid = str(bilibili_view(bvid).get("aid") or "")
    if not aid:
        return {"ok": False, "error": "拿不到这个视频的 aid，无法读取评论"}
    first: Optional[dict] = None
    found: Optional[Tuple[str, str]] = None
    for pn in range(1, BILIBILI_REPLY_PAGES + 1):
        payload = _fetch(BILIBILI_REPLY_API.format(oid=aid, root=root_id or reply_id, pn=pn),
                         "https://www.bilibili.com/").json()
        if payload.get("code") not in (0, None):
            raise SourceError(f"b 站评论接口返回 {payload.get('code')}：{payload.get('message')}")
        first = first or payload
        found = bilibili_comment_by_id(payload, reply_id)
        if found:
            break
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
    return _ok(name, bilibili_space_url(mid), BILIBILI_COMMENT_SOURCE_NAME, share_url,
               f"已按评论者填入翻译者：{name}{note}")


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
        if SHORT_LINK_RE.match(url):
            url = resolve_short_link(url)          # App 分享的 b23.tv 短链先还原成真链接
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
    except SourceError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:                       # 网络 / 解析失败都不该让窗口崩掉
        logging.error("读取来源链接失败：%s", e, exc_info=e)
        return {"ok": False, "error": f"读取失败：{e}"}
    return {"ok": False, "error": UNKNOWN_LINK_ERROR}
