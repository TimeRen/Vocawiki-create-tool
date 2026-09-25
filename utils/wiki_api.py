"""Vocawiki（MediaWiki）API 封装：解析预览 wikitext、创建 / 编辑页面、抓取站点 CSS。"""
import logging
import re
from typing import Dict, Optional
from urllib.parse import quote, urlsplit

from utils import login

REQUEST_TIMEOUT = 60
REDIRECT_TEMPLATE = "#REDIRECT [[{target}]]"
# MediaWiki 的站点级 CSS 页面：Common 对所有皮肤生效，另一个对当前皮肤生效
SITE_CSS_COMMON_PAGE = "MediaWiki:Common.css"

# 站点 CSS 按“页面名元组”缓存，避免每次预览都重复请求
_site_css_cache: Dict[tuple, str] = {}


def api_url() -> str:
    """Vocawiki 的 API 地址。"""
    return login.api_url()


def origin() -> str:
    """Wiki 站点根地址，例如 https://voca.wiki/（用于预览面板解析相对链接）。"""
    parts = urlsplit(api_url())
    return f"{parts.scheme}://{parts.netloc}/"


def article_url(title: str) -> str:
    """由 API 地址推导条目的浏览地址（用于预览与提交后跳转）。"""
    base = api_url()
    if base.endswith("/api.php"):
        base = base[: -len("api.php")] + "wiki/"
    elif "/" in base:
        base = base.rsplit("/", 1)[0] + "/"
    return base + quote(title.replace(" ", "_"))


def _error_message(payload: dict) -> str:
    error = payload.get("error", {})
    return error.get("info") or error.get("code") or "未知错误"


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
    try:
        response = login.get_session().post(api_url(), data=data, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logging.error("Failed to edit page %s: %s", title, e)
        return {"ok": False, "error": f"无法连接 Vocawiki：{e}"}
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
