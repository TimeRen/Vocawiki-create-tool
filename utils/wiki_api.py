"""Vocawiki（MediaWiki）API 封装：解析预览 wikitext、创建 / 编辑页面、抓取站点 CSS。"""
import logging
import re
import time
from typing import Dict, Optional
from urllib.parse import quote, urlsplit

from utils import login, rate_limit

REQUEST_TIMEOUT = 60
REDIRECT_TEMPLATE = "#REDIRECT [[{target}]]"
# 站点级 CSS 页面：Common 对所有皮肤生效，另一个对当前皮肤生效
SITE_CSS_COMMON_PAGE = "MediaWiki:Common.css"
# 判断一个页面是不是歌曲条目（用于消歧义处理时决定能不能移动它）
WIKITEXT_SONGBOX_RE = re.compile(r"\{\{\s*VOCALOID[_ ]Songbox", re.I)
# 一次 query 最多带多少个标题（MediaWiki 对非机器人默认 50）
PAGE_BATCH = 50

# 写操作遇到**站点自己的临时故障**时重试几次（用户 2026-09 报的「Template:Shu 写回失败」就是
# MediaWiki 的 `internal_api_error_DBQueryError`：保存时站点要跑一遍解析（模板里一堆链接 / #invoke），
# 期间只要有一条查询撞上数据库忙 / 锁等待就整个保存失败，等一两秒再来通常就好了）。
# 业务错误（页面被保护、标题非法、没权限…）**不重试**，重试也没用。
RETRY_DELAYS = (2.0, 5.0)
TRANSIENT_CODES = (
    "internal_api_error_dbqueryerror",          # 数据库查询出错（就是用户碰到的那条）
    "internal_api_error_dbconnectionerror",     # 连不上数据库
    "internal_api_error_dbreadonlyerror",
    "dbqueryerror",
    "readonly",                                 # 站点在维护 / 只读
    "readonlytext",
    "locked",
    "ratelimited",                              # 被限流，等一会儿再来
)

# 站点 CSS 按“页面名元组”缓存，避免每次预览都重复请求
_site_css_cache: Dict[tuple, str] = {}


def api_url() -> str:
    """Vocawiki 的 API 地址。"""
    return login.api_url()


def origin() -> str:
    """Wiki 站点根地址，例如 https://voca.wiki/（用于预览面板解析相对链接）。"""
    parts = urlsplit(api_url())
    return f"{parts.scheme}://{parts.netloc}/"


# 站点报的 $wgArticlePath（`/$1`、`/wiki/$1` …）；查过就缓存（查不到记空串，不再反复请求）
_article_path_cache: Optional[str] = None


def article_path() -> str:
    """站点的「条目地址」模板（`$wgArticlePath`，形如 `/$1`、`/wiki/$1`）；取不到返回空串。

    **不要猜**：voca.wiki 上条目直接挂在根下（`https://voca.wiki/歌`，`$wgArticlePath = "/$1"`），
    而不是常见的 `/wiki/歌`。旧实现无条件把 `api.php` 换成 `wiki/`，于是提交后点
    「在浏览器中打开条目」多出一个 `wiki/`（用户 2026-09 报的就是这个）。
    """
    global _article_path_cache
    if _article_path_cache is None:
        path = ""
        try:
            payload = login.get_api_session().get(api_url(), params={
                "action": "query",
                "meta": "siteinfo",
                "siprop": "general",
                "format": "json",
                "formatversion": "2",
            }, timeout=REQUEST_TIMEOUT).json()
            general = (payload.get("query") or {}).get("general") or {}
            path = str(general.get("articlepath") or "")
        except Exception as error:                  # noqa: BLE001 - 取不到就退回 index.php
            logging.warning("取 %s 的 articlepath 失败：%s，条目地址改用 index.php", api_url(), error)
        _article_path_cache = path
    return _article_path_cache


def article_url(title: str) -> str:
    """由 API 地址推导条目的浏览地址（用于预览与提交后跳转）。

    优先照站点报的 `$wgArticlePath` 拼（voca.wiki → `https://voca.wiki/条目名`）；
    问不到站点信息时退回 `index.php?title=条目名` —— 这个地址在任何 MediaWiki 上都能开
    （会自己跳到漂亮地址），总比猜一个错路径好。
    """
    host = origin().rstrip("/")
    quoted = quote(title.replace(" ", "_"))
    path = article_path()
    if "$1" in path:
        return host + path.replace("$1", quoted)
    return f"{host}/index.php?title={quoted}"


def _error_message(payload: dict) -> str:
    error = payload.get("error", {})
    return error.get("info") or error.get("code") or "未知错误"


def _is_transient(code: str) -> bool:
    """这个错误码是不是「站点自己临时抽风，过一下再来就好」。"""
    return str(code or "").strip().lower() in TRANSIENT_CODES


def _post_with_retry(data: Dict[str, object], what: str) -> Dict[str, object]:
    """POST 到 api.php 并返回 JSON；**临时故障会自动重试**，业务错误原样返回。

    重试的情况：站点报数据库出错 / 只读 / 限流，或干脆 5xx / 连接被断（网络抖动）。
    彻底失败时返回 `{'error': {...}}`（`info` 里是给用户看的话），原文照旧写进日志。
    """
    attempts = len(RETRY_DELAYS) + 1
    payload: Dict[str, object] = {}
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            time.sleep(RETRY_DELAYS[attempt - 2])
        failure = ""
        try:
            response = login.get_session().post(api_url(), data=data, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            code = str((payload.get("error") or {}).get("code") or "")
        except Exception as error:                  # noqa: BLE001 - 网络问题一律重试
            payload, code = {}, ""
            failure = str(error)
        retryable = bool(failure) or _is_transient(code)
        if not retryable:
            if code:
                logging.error("%s：站点返回错误 %s（%s）", what, code, _error_message(payload))
            return payload
        if attempt < attempts:
            logging.warning("%s：第 %d 次失败（%s），%.1f 秒后重试",
                            what, attempt, failure or code, RETRY_DELAYS[attempt - 1])
            continue
        if failure:
            logging.error("%s：重试 %d 次都连不上（%s）", what, attempts, failure)
            return {"error": {"code": "connection", "info": f"无法连接 Vocawiki：{failure}"}}
        # 数据库出错这类：把英文报错换成一句人话（原文上面已经记进日志）
        logging.error("%s：站点返回错误 %s（%s），已重试 %d 次",
                      what, code, _error_message(payload), attempts)
        payload["error"]["info"] = (f"站点数据库临时故障，已重试 {attempts} 次仍未成功"
                                     "——过一会儿再跑一次就好（详细报错见「日志」页）")
        return payload
    return payload


def detect_skin(headhtml: str) -> Optional[str]:
    """从 headhtml 识别当前皮肤标识（用于定位 MediaWiki:<皮肤>.css）。

    优先取 ResourceLoader 启动脚本里的 skin= 参数，其次取 <body class="… skin-xxx …">。
    """
    match = re.search(r"skin=([A-Za-z0-9._-]+)", headhtml)
    if match:
        return match.group(1)
    body = re.search(r"<body[^>]*\bclass=\"([^\"]*)\"", headhtml)
    if body:
        # 首个字符必须是字母/数字，避免匹配到 skin--responsive 这类修饰类名
        match = re.search(r"\bskin-([A-Za-z0-9][A-Za-z0-9-]*)", body.group(1))
        if match:
            return match.group(1)
    return None


def _skin_css_page(skin: str) -> str:
    return f"MediaWiki:{skin[:1].upper()}{skin[1:]}.css"


def fetch_site_styles(skin: Optional[str] = None) -> str:
    """抓取站点级 CSS（MediaWiki:Common.css 与当前皮肤的 MediaWiki:<皮肤>.css）。

    条目式 CSS 在预览里无法通过 ResourceLoader 加载（脚本被禁用），这里用 API 取回后内联。
    页面不存在或抓取失败时返回空串；结果会缓存，避免每次预览重复请求。
    """
    titles = [SITE_CSS_COMMON_PAGE]
    if skin:
        titles.append(_skin_css_page(skin))
    key = tuple(titles)
    if key in _site_css_cache:
        return _site_css_cache[key]

    parts = []
    try:
        payload = login.get_api_session().get(api_url(), params={
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT).json()
        for page in payload.get("query", {}).get("pages", []):
            if page.get("missing"):
                continue
            try:
                content = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                continue
            if content and content.strip():
                parts.append(f"/* {page.get('title', '')} */\n{content}")
    except Exception as e:
        logging.warning("无法获取站点 CSS：%s", e)

    css = "\n".join(parts)
    _site_css_cache[key] = css
    return css


def parse_wikitext(text: str, title: str = "沙盒") -> Dict[str, object]:
    """调用 action=parse 渲染 wikitext，供窗口实时预览。

    返回 {'html': ..., 'head': ..., 'css': ...}；失败时返回 {'error': ...}。
    head 是完整文档骨架（DOCTYPE + 皮肤 <head> + <body class="…">），
    css 是内联的站点 CSS（无则空串）。
    """
    try:
        response = login.get_api_session().post(api_url(), data={
            "action": "parse",
            "text": text,
            "title": title,
            "contentmodel": "wikitext",
            "prop": "text|headhtml",
            "disablelimitreport": "1",
            "disableeditsection": "1",
            "format": "json",
            "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.error("Failed to parse wikitext: %s", e)
        return {"error": f"无法连接 Vocawiki：{e}"}
    if "error" in payload:
        return {"error": _error_message(payload)}
    parsed = payload.get("parse", {})
    head = parsed.get("headhtml", "")
    return {
        "html": parsed.get("text", ""),
        "head": head,
        "css": fetch_site_styles(detect_skin(head)),
    }


def edit_page(title: str, text: str, summary: str = "",
              create_only: bool = False) -> Dict[str, object]:
    """调用 action=edit 创建 / 编辑页面。

    返回 {'ok': True, ...}；失败返回 {'ok': False, 'error': ...}；
    若页面已存在且 create_only=True，额外带 'exists': True。
    """
    if not login.is_logged_in():
        return {"ok": False, "error": "未登录 Vocawiki，请检查 wiki_credentials.yaml"}
    data = {
        "action": "edit",
        "title": title,
        "text": text,
        "summary": summary,
        "token": login.get_csrf_token(),
        "bot": "1",
        "format": "json",
        "formatversion": "2",
    }
    if create_only:
        data["createonly"] = "1"
    rate_limit.wait_for_slot()                       # 速率墙：写操作按用户设定的频率排队
    payload = _post_with_retry(data, f"编辑 {title}")
    if "error" in payload:
        result: Dict[str, object] = {"ok": False, "error": _error_message(payload)}
        if payload["error"].get("code") == "articleexists":
            result["exists"] = True
        return result
    edited = payload.get("edit", {})
    if edited.get("result") != "Success":
        return {"ok": False, "error": f"提交失败：{edited or payload}"}
    return {
        "ok": True,
        "title": edited.get("title", title),
        "newrevid": edited.get("newrevid"),
    }


def create_redirect(source_title: str, target_title: str, summary: str = "") -> Dict[str, object]:
    """创建指向目标条目的重定向页面（页面已存在时不覆盖）。"""
    return edit_page(source_title, REDIRECT_TEMPLATE.format(target=target_title),
                     summary=summary, create_only=True)


# ---------------------------------------------------------------- 查询 / 移动

REDIRECT_TARGET_RE = re.compile(r"#\s*redirect\s*\[\[\s*([^\]|]+)", re.I)


def fetch_page_facts(title: str) -> Dict[str, object]:
    """一次拿到页面的存在性 / 是否重定向 / 是否消歧义页 / 正文。

    返回 {'ok', 'title', 'exists', 'redirect', 'redirect_target', 'disambig', 'song', 'text'}。
    消歧义页靠 pageprops 的 disambiguation 标记识别（Template:消歧义页 里有 __DISAMBIG__）；
    查不到（含网络失败）时 exists=False，调用方按「没有同名条目」处理。
    """
    facts: Dict[str, object] = {"ok": False, "title": title, "exists": False, "redirect": False,
                                "redirect_target": "", "disambig": False, "song": False, "text": ""}
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "prop": "info|pageprops|revisions",
            "rvprop": "content", "rvslots": "main", "titles": title,
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("无法查询页面 %s：%s", title, e)
        return facts
    pages = (payload.get("query") or {}).get("pages") or []
    page = pages[0] if pages else {}
    facts["ok"] = True
    if page.get("missing"):
        return facts
    facts["exists"] = True
    facts["redirect"] = "redirect" in page
    props = page.get("pageprops") or {}
    facts["disambig"] = "disambiguation" in props
    try:
        text = page["revisions"][0]["slots"]["main"]["content"]
    except (KeyError, IndexError, TypeError):
        text = ""
    facts["text"] = text or ""
    if facts["redirect"]:
        match = REDIRECT_TARGET_RE.search(text or "")
        facts["redirect_target"] = match.group(1).strip() if match else ""
    facts["song"] = bool(WIKITEXT_SONGBOX_RE.search(text or ""))
    return facts


def list_titles_with_prefix(prefix: str, limit: int = 50) -> list:
    """标题前缀搜索（用于找「歌名(P主名)」这类同名前缀页）。"""
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "list": "prefixsearch", "pssearch": prefix, "pslimit": limit,
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("前缀搜索 %s 失败：%s", prefix, e)
        return []
    return [item.get("title", "") for item in (payload.get("query") or {}).get("prefixsearch") or []]


def category_members(category: str, limit: int = 5000, namespace: int = 0) -> list:
    """分类成员标题（`Category:歌爱雪歌曲` → 歌爱雪唱过的曲子条目）。

    一次 500 条、自动跟着 `continue` 翻页，最多取 `limit` 条（分类可能有上千个成员，
    比如 `Category:初音未来歌曲` 就 5254 条）。取不到就返回空表。
    """
    name = str(category or "").strip()
    if not name:
        return []
    if not name.lower().startswith("category:"):
        name = f"Category:{name}"
    titles: list = []
    cont: Optional[str] = None
    while len(titles) < limit:
        params = {"action": "query", "list": "categorymembers", "cmtitle": name,
                  "cmlimit": min(500, limit - len(titles)), "cmnamespace": namespace,
                  "format": "json", "formatversion": "2"}
        if cont:
            params["cmcontinue"] = cont
        try:
            response = login.get_api_session().get(api_url(), params=params,
                                                   timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:                      # noqa: BLE001 - 网络失败当作空分类
            logging.warning("读取分类 %s 的成员失败：%s", name, e)
            return titles
        query = payload.get("query") or {}
        for item in query.get("categorymembers") or []:
            title = str(item.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
        cont = (payload.get("continue") or {}).get("cmcontinue")
        if not cont:
            break
    return titles


def pages_with_prefix(prefix: str, namespace: int = 0, limit: int = 500) -> list:
    """按**标题前缀**列页面（`action=query&list=allpages&apprefix=…`）。

    与 `list_titles_with_prefix()`（prefixsearch，按相关度排）不同：这个按标题顺序列出
    **全部**同前缀页面 —— 用来摸清一个系列子页有多少（`VOCALOID殿堂曲/` 下的各年份页）。
    取不到返回空表。
    """
    value = str(prefix or "").strip()
    if not value:
        return []
    titles: list = []
    cont: Optional[str] = None
    while len(titles) < limit:
        params = {"action": "query", "list": "allpages", "apprefix": value,
                  "apnamespace": namespace, "aplimit": min(500, limit - len(titles)),
                  "format": "json", "formatversion": "2"}
        if cont:
            params["apcontinue"] = cont
        try:
            response = login.get_api_session().get(api_url(), params=params,
                                                   timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:                      # noqa: BLE001
            logging.warning("按前缀列页面 %s 失败：%s", value, e)
            return titles
        for item in (payload.get("query") or {}).get("allpages") or []:
            title = str(item.get("title") or "").strip()
            if title and title not in titles:
                titles.append(title)
        cont = (payload.get("continue") or {}).get("apcontinue")
        if not cont:
            break
    return titles


def pages_exist(titles) -> Dict[str, bool]:
    """批量问「这些页面在不在」→ `{标题: True/False}`（问不到的标题不出现）。

    与 `fetch_pages_text()` 的区别：那个拿不到正文时**分不清**「页面不存在」与「这一趟请求
    失败了」（歌姬模板按分类逐首核条目，一次抖动会把几十首歌误判成红链 —— 用户 2026-09-30
    实测到的），所以另开一个便宜的 `prop=info` 查询把两者分开。
    """
    pending = [str(title) for title in titles if str(title or "").strip()]
    found: Dict[str, bool] = {}
    for start in range(0, len(pending), PAGE_BATCH):
        batch = pending[start:start + PAGE_BATCH]
        if not batch:
            continue
        try:
            response = login.get_api_session().get(api_url(), params={
                "action": "query", "titles": "|".join(batch), "redirects": "1",
                "prop": "info", "format": "json", "formatversion": "2",
            }, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:                      # noqa: BLE001 - 这一批就当问不到
            logging.warning("查询页面是否存在失败：%s", e)
            continue
        query = payload.get("query") or {}
        normalized = {item.get("from"): item.get("to") for item in query.get("normalized") or []}
        redirected = {item.get("from"): item.get("to") for item in query.get("redirects") or []}
        for page in query.get("pages") or []:
            title = str(page.get("title") or "")
            exists = not page.get("missing")
            found[title] = exists
            for source in (normalized, redirected):
                for original, resolved in source.items():
                    if resolved == title:
                        found.setdefault(original, exists)
    return found


def fetch_backlinks(title: str, limit: int = 500) -> list:
    """链入页面列表（对应 Special:WhatLinksHere，只取条目名字空间）。"""
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "list": "backlinks", "bltitle": title, "blnamespace": 0,
            "bllimit": limit, "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("获取 %s 的链入页面失败：%s", title, e)
        return []
    return [item.get("title", "") for item in (payload.get("query") or {}).get("backlinks") or []]


def redirect_titles(title: str, limit: int = 20) -> list:
    """**指向这个标题的重定向**页标题。

    用来找日文 P主名的罗马音：实测 `雄之助` → `['Yunosuke']`（同名条目就叫 `涅槃(Yunosuke)`）。
    取不到就返回空表，调用方退回用原名。
    """
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "list": "backlinks", "bltitle": title,
            "blfilterredir": "redirects", "blnamespace": 0, "bllimit": limit,
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("获取 %s 的重定向失败：%s", title, e)
        return []
    return [item.get("title", "") for item in (payload.get("query") or {}).get("backlinks") or []]


def file_usage(file_title: str, limit: int = 50) -> list:
    """哪些页面用了这个文件（`File:涅槃.jpg` → `['涅槃']`）；给封面改名判断用。"""
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "list": "imageusage", "iutitle": file_title,
            "iulimit": limit, "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("获取 %s 的使用情况失败：%s", file_title, e)
        return []
    return [item.get("title", "") for item in (payload.get("query") or {}).get("imageusage") or []]


def search_text_references(title: str, namespaces=(10, 828), limit: int = 50) -> list:
    """全文搜索正文里写着 `title` 的页面（`insource:`），补**链入表查不到**的引用。

    模板 / 模块里用 `{{links|条目名{{!}}日文名}}` 罗列曲目时，链接是 Lua 现拼出来的，
    MediaWiki **不会**把它记进链入表 —— 实测 `Template:雄之助` 里有 `涅槃{{!}}ネハン`，
    却不属于 `涅槃` 的 `list=backlinks`（用户 2026-09-29 报的就是这个漏网的模板）。
    默认只搜模板（10）/ 模块（828）命名空间：条目命名空间里的引用都在链入表里。
    取不到（没装 CirrusSearch / 网络失败）就返回空表。
    """
    title = (title or "").strip().replace('"', "")
    if not title:
        return []
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "list": "search", "srsearch": f'insource:"{title}"',
            "srnamespace": "|".join(str(item) for item in namespaces),
            "srlimit": limit, "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("全文搜索 %s 的引用失败：%s", title, e)
        return []
    return [item.get("title", "") for item in (payload.get("query") or {}).get("search") or []
            if item.get("title")]


def search_pages_with_text(term: str, limit: int = 5, namespace: int = 0) -> list:
    """按关键字搜条目，顺带把候选页正文带回来 → [(标题, 正文)]。

    `generator=search` + `prop=revisions` 一次请求就够，省掉「先搜再逐页取正文」的两轮
    （生成 P主模板时要按日文原名找出对应的中文条目，见 `utils/producer_template.py`）。
    取不到就返回空表。
    """
    term = (term or "").strip()
    if not term:
        return []
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "generator": "search", "gsrsearch": term,
            "gsrnamespace": str(namespace), "gsrlimit": limit,
            "prop": "revisions", "rvprop": "content", "rvslots": "main",
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("搜索 %s 失败：%s", term, e)
        return []
    found = []
    for page in (payload.get("query") or {}).get("pages") or []:
        try:
            text = page["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError, TypeError):
            continue
        found.append((page.get("title", ""), text or ""))
    return found


def recent_revision_texts(title: str, limit: int = 12) -> list:
    """一个页面**最近几版的正文** → `[{'revid','timestamp','user','comment','content'}]`（新的在前）。

    为什么要它：歌姬模板拆成年份子页之后，主模板里只剩「年份转接行」，原来那份手写名单
    （红链、翻唱记号）就没有了 —— 靠它往前翻，拿最近一版**带曲目名单**的当「原模板」
    （见 `vocalist_template._previous_template_songs()`，用户 2026-10-01 报的
    「原模板中的红链也消失不见」）。失败返回空表（调用方当「翻不到」处理）。
    """
    pending = str(title or "").strip()
    if not pending:
        return []
    revisions: list = []
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "prop": "revisions", "titles": pending,
            "rvlimit": max(1, int(limit or 1)),
            "rvprop": "ids|timestamp|user|comment|content", "rvslots": "main",
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("读取 %s 的历史版本失败：%s", pending, e)
        return []
    for page in (payload.get("query") or {}).get("pages") or []:
        for item in page.get("revisions") or []:
            try:
                content = item["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                content = ""
            revisions.append({"revid": item.get("revid", 0),
                              "timestamp": item.get("timestamp", ""),
                              "user": item.get("user", ""),
                              "comment": item.get("comment", ""),
                              "content": content or ""})
    return revisions


def redirect_targets(titles) -> Dict[str, str]:
    """`{重定向标题: 真正的条目名}`（不是重定向的标题不会出现在结果里）。

    为什么需要：P主 页面里写的条目名可能是条重定向 —— 实测 読谷あかね 页面写着
    `|条目 = Chilly`，而站上 `Chilly` 与 `散り散り` 都重定向到真条目「四散」；
    用户 2026-09 手改模板时正是把 `Chilly` 换成了「四散」。所以填名字前先问一句：
    这个名字是不是重定向，真条目叫什么。
    """
    pending = [str(title) for title in titles if str(title or "").strip()]
    mapping: Dict[str, str] = {}
    for start in range(0, len(pending), PAGE_BATCH):
        batch = pending[start:start + PAGE_BATCH]
        if not batch:
            continue
        try:
            response = login.get_api_session().get(api_url(), params={
                "action": "query", "titles": "|".join(batch), "redirects": "1",
                "prop": "info", "format": "json", "formatversion": "2",
            }, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except Exception as e:
            logging.warning("查询重定向失败：%s", e)
            continue
        query = payload.get("query") or {}
        normalized = {item.get("from"): item.get("to")
                      for item in query.get("normalized") or []}
        for item in query.get("redirects") or []:
            source, target = item.get("from"), item.get("to")
            if not (source and target):
                continue
            mapping[source] = target
            for original, resolved in normalized.items():   # 下划线 / 首字母大小写的写法
                if resolved == source:
                    mapping[original] = target
    return mapping


def fetch_pages_text(titles, batch: int = PAGE_BATCH) -> Dict[str, str]:
    """批量取多页正文 → {标题: 正文}（取不到的页不出现）。

    重定向会跟着走：正文挂在真条目名下，原标题也当别名给一份（见 `_fetch_pages_text_batch`）。
    `batch` 是每次请求带多少个标题（默认 50）：页面特别大的时候（殿堂曲页 70KB+）
    调用方可以调小一点，避免一次请求拖回好几 MB。
    """
    texts: Dict[str, str] = {}
    pending = [title for title in titles if title]
    size = max(1, int(batch or PAGE_BATCH))
    for start in range(0, len(pending), size):
        texts.update(_fetch_pages_text_batch(pending[start:start + size]))
    return texts


def _fetch_pages_text_batch(titles) -> Dict[str, str]:
    if not titles:
        return {}
    texts: Dict[str, str] = {}
    try:
        response = login.get_api_session().get(api_url(), params={
            "action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
            "titles": "|".join(titles), "redirects": "1",
            "format": "json", "formatversion": "2",
        }, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.warning("批量读取页面正文失败：%s", e)
        return texts
    query = payload.get("query") or {}
    normalized = {item.get("from"): item.get("to") for item in query.get("normalized") or []}
    redirected = {item.get("from"): item.get("to") for item in query.get("redirects") or []}
    for page in query.get("pages") or []:
        if page.get("missing"):
            continue
        try:
            text = page["revisions"][0]["slots"]["main"]["content"]
        except (KeyError, IndexError, TypeError):
            continue
        texts[page.get("title", "")] = text or ""
        for source in (normalized, redirected):
            for original, resolved in source.items():
                if resolved == page.get("title"):
                    texts.setdefault(original, text or "")
    return texts


def move_page(from_title: str, to_title: str, reason: str = "",
              leave_redirect: bool = False) -> Dict[str, object]:
    """移动页面（默认不留重定向）。

    返回 {'ok': True}；失败返回 {'ok': False, 'error': ...}。
    """
    if not login.is_logged_in():
        return {"ok": False, "error": "未登录 Vocawiki，请检查 wiki_credentials.yaml"}
    data = {
        "action": "move",
        "from": from_title,
        "to": to_title,
        "reason": reason,
        "movetalk": "1",
        "token": login.get_csrf_token(),
        "format": "json",
        "formatversion": "2",
    }
    if not leave_redirect:
        data["noredirect"] = "1"
    rate_limit.wait_for_slot()                       # 速率墙：移动也算一次编辑
    payload = _post_with_retry(data, f"移动 {from_title} → {to_title}")
    if "error" in payload:
        return {"ok": False, "error": _error_message(payload)}
    moved = payload.get("move", {})
    return {"ok": True, "from": moved.get("from", from_title), "to": moved.get("to", to_title)}
