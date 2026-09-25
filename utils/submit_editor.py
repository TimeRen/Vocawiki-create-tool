"""通过 pywebview 打开 wikitext 提交窗口：实时预览（Vocawiki API）、编辑、提交条目与封面。"""
import base64
import json
import logging
import mimetypes
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from config.config import application_path
from models.creators import Person
from utils import family_template, login, wiki_api
from utils.family_template import FamilySync
from utils.upload import upload_image

EDITOR_DIR = "html"                      # 界面文件统一放在程序目录的 html/ 下
EDITOR_FILE = "wikitext-editor.html"
DEFAULT_SUMMARY = "由 Vocawiki条目辅助工具 创建"


@dataclass
class CoverInfo:
    """随条目一并提交的封面信息（也是本工具唯一的上传图片入口）。"""
    path: Path                                   # 本地封面文件
    wiki_name: str                               # 上传到 Vocawiki 的文件名，与 Songbox |image 保持一致
    source_url: Optional[str] = None
    authors: Optional[List[Person]] = None
    characters: Optional[List[str]] = field(default_factory=list)


def _read_data_uri(path: Path) -> Optional[str]:
    """把本地图片读成 data URI，供预览直接显示（避免依赖尚未上传的 wiki 文件）。"""
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as e:
        logging.error("无法读取封面图片 %s：%s", path, e)
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64,{data}"


class SubmitApi:
    """暴露给前端 JS 的接口，负责预览、写回本地文件与提交条目/封面。"""

    def __init__(self, page_name: str, source_path: Union[str, Path], wikitext: str,
                 ja_name: Optional[str] = None, create_redirect: bool = False,
                 cover: Optional[CoverInfo] = None, family: Optional[FamilySync] = None):
        self._page_name = page_name
        self._source_path = Path(source_path)
        self._wikitext = wikitext
        # 日文原名与中文条目名相同时无需重定向
        self._ja_name = ja_name if (ja_name and ja_name != page_name) else None
        self._create_redirect = create_redirect
        self._cover = cover
        self._family = family
        self._cover_uri: Optional[str] = None
        self._cover_uri_loaded = False
        self._window = None

    def get_context(self) -> dict:
        cover = None
        if self._cover is not None:
            cover = {
                "wikiName": self._cover.wiki_name,
                "file": str(self._cover.path),
                "exists": self._cover.path.exists(),
                "characters": list(self._cover.characters or []),
                "pageUrl": wiki_api.article_url(f"File:{self._cover.wiki_name}"),
            }
        honors = list(self._family.honors) if self._family else []
        family = {
            "available": bool(self._family and self._family.available),
            "templates": list(self._family.templates) if self._family else [],
            "producers": list(self._family.producers) if self._family else [],
            "honors": [{"site": site, "views": views} for site, views in honors],
            "collections": [{"template": item.template, "track": item.track, "rank": item.rank}
                            for item in (self._family.collections if self._family else [])],
        }
        return {
            "page": self._page_name,
            "file": str(self._source_path),
            "pageUrl": wiki_api.article_url(self._page_name),
            "origin": wiki_api.origin(),
            "summary": DEFAULT_SUMMARY,
            "createRedirect": self._create_redirect,
            "redirect": self._ja_name,
            "canSubmit": login.is_logged_in(),
            "cover": cover,
            "family": family,
        }

    def family_plan(self) -> dict:
        """同步大家族模板前的预览说明（不改动任何东西）。"""
        if self._family is None or not self._family.available:
            return {"ok": True, "lines": []}
        try:
            lines = family_template.plan(self._family, self._page_name, self._ja_name)
        except Exception as e:
            logging.error("生成大家族模板同步计划失败：%s", e, exc_info=e)
            return {"ok": False, "error": str(e)}
        return {"ok": True, "lines": lines or ["没有需要改动的地方"]}

    def preview(self, text: str) -> dict:
        """调用 Vocawiki API 渲染 wikitext；同时带上本地封面，供预览替换尚未上传的图片。"""
        result = wiki_api.parse_wikitext(text or "", title=self._page_name)
        if result.get("error"):
            return result
        data = self._cover_data_uri()
        if data:
            result["cover"] = {"name": self._cover.wiki_name, "data": data}
        return result

    def save(self, text: str) -> dict:
        """仅把窗口中的内容写回本地 wikitext 文件。"""
        if self._write_local(text):
            return {"ok": True, "message": "已保存到本地文件"}
        return {"ok": False, "error": "写入本地文件失败"}

    def open_page(self) -> dict:
        """在系统默认浏览器中打开条目页面，便于核对提交结果。"""
        url = wiki_api.article_url(self._page_name)
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception as e:
            logging.error("无法打开条目页面：%s", e)
            return {"ok": False, "error": str(e)}

    def close_window(self) -> dict:
        """关闭提交窗口。

        提交**成功**时前端会先弹出完成通知，等 3 秒再调这里把窗口关掉；
        提交失败时前端不会调用本方法，窗口保持打开以便修改后重试。
        （pywebview 5 的 Window.destroy() 可以在 JS 接口线程里调用。）
        """
        window = self._window
        if window is None:
            return {"ok": False, "error": "窗口不可用（例如在浏览器里调试）"}
        try:
            window.destroy()
            return {"ok": True}
        except Exception as e:
            logging.error("关闭提交窗口失败：%s", e)
            return {"ok": False, "error": str(e)}

    def submit(self, text: str, summary: str = "", sync_family: bool = False) -> dict:
        """先上传封面，再提交条目，然后按要求创建重定向、同步大家族模板。"""
        if not login.is_logged_in():
            return {"ok": False, "error": "未登录 Vocawiki，请在 wiki_credentials.yaml 中配置账号/机器人密码"}
        self._wikitext = text or ""
        self._write_local(self._wikitext)
        summary = summary or DEFAULT_SUMMARY

        messages = []
        if self._cover is not None:
            messages.append(self._upload_cover())

        result = wiki_api.edit_page(self._page_name, self._wikitext, summary)
        if not result.get("ok"):
            error = result.get("error", "提交失败")
            if messages:
                error = "；".join([*messages, error])
            return {"ok": False, "error": error}
        messages.append(f"已提交「{self._page_name}」")

        if self._create_redirect and self._ja_name:
            redirect = wiki_api.create_redirect(
                self._ja_name, self._page_name, f"创建重定向至「{self._page_name}」")
            if redirect.get("ok"):
                messages.append(f"已创建重定向「{self._ja_name}」")
            elif redirect.get("exists"):
                messages.append(f"重定向「{self._ja_name}」已存在，未覆盖")
            else:
                messages.append(f"重定向创建失败：{redirect.get('error')}")

        # 条目提交成功后再同步大家族模板（失败只提示，不影响条目本身）
        if sync_family and self._family is not None and self._family.available:
            try:
                messages.extend(family_template.sync(self._family, self._page_name, self._ja_name))
            except Exception as e:
                logging.error("同步大家族模板失败：%s", e, exc_info=e)
                messages.append(f"大家族模板同步失败：{e}")

        return {
            "ok": True,
            "message": "；".join(messages),
            "url": wiki_api.article_url(self._page_name),
            "newrevid": result.get("newrevid"),
        }

    def _upload_cover(self) -> str:
        """上传封面，返回用于展示的提示文本（失败不阻断条目提交）。"""
        cover = self._cover
        result = upload_image(cover.path, cover.wiki_name, self._page_name,
                              authors=cover.authors, source_url=cover.source_url,
                              characters=cover.characters)
        if result.get("ok"):
            return str(result.get("message", "已上传封面"))
        return str(result.get("error", "封面上传失败"))

    def _cover_data_uri(self) -> Optional[str]:
        if self._cover is None:
            return None
        if not self._cover_uri_loaded:
            self._cover_uri = _read_data_uri(self._cover.path)
            self._cover_uri_loaded = True
        return self._cover_uri

    def _write_local(self, text: str) -> bool:
        try:
            self._source_path.write_text(text, encoding="utf-8")
            return True
        except OSError as e:
            logging.error("无法写回 wikitext 文件 %s：%s", self._source_path, e)
            return False


def open_submit_editor(page_name: str, wikitext: str, source_path: Union[str, Path],
                       ja_name: Optional[str] = None, create_redirect: bool = False,
                       cover: Optional[CoverInfo] = None,
                       family: Optional[FamilySync] = None) -> bool:
    """打开 wikitext 提交窗口。

    成功弹出窗口返回 True；pywebview 不可用或打开失败返回 False（调用方可回退到别处打开）。
    """
    try:
        import webview
    except ImportError:
        logging.error("未安装 pywebview，无法打开提交窗口。请执行 pip install pywebview")
        return False

    html_path = application_path.joinpath(EDITOR_DIR, EDITOR_FILE)
    if not html_path.exists():
        logging.error("找不到提交窗口文件：%s", html_path)
        return False

    api = SubmitApi(page_name, source_path, wikitext, ja_name, create_redirect, cover, family)
    window = webview.create_window("Vocawiki 提交", str(html_path), js_api=api,
                                   width=1320, height=880)
    api._window = window
    payload = json.dumps({"wikitext": wikitext, "context": api.get_context()},
                         ensure_ascii=False)

    def inject():
        # 等待页面脚本就绪后再注入内容，避免加载时序问题
        window.evaluate_js(
            "(function(){var d=%s,t=0;(function go(){"
            "if(window.__vocawikiInit){window.__vocawikiInit(d);return;}"
            "if(t++<50)setTimeout(go,100);})();})();" % payload
        )

    try:
        webview.start(func=inject)
    except Exception as e:
        logging.error("打开提交窗口失败：%s", e)
        return False
    return True
