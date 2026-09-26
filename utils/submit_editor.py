"""wikitext 提交窗口的数据侧：预览、写回本地文件、提交条目与封面。

界面已经是 PyQt5 主窗口里的「提交」页（utils/ui/submit_panel.py），
本模块只留 SubmitApi——所有能在终端里单测的逻辑都在这里。
"""
import base64
import json
import logging
import mimetypes
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from models.creators import Person
from utils import disambig, family_template, login, wiki_api
from utils.family_template import FamilySync
from utils.upload import upload_image

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
                 cover: Optional[CoverInfo] = None, family: Optional[FamilySync] = None,
                 disambig_plan=None):
        self._page_name = page_name
        self._source_path = Path(source_path)
        self._wikitext = wikitext
        # 日文原名与中文条目名相同时无需重定向
        self._ja_name = ja_name if (ja_name and ja_name != page_name) else None
        self._create_redirect = create_redirect
        self._cover = cover
        self._family = family
        self._disambig = disambig_plan
        self._backlinks: List[dict] = []          # 移动后待确认的链入页面
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
            "disambig": self._disambig_context(),
        }

    def _disambig_context(self) -> dict:
        """同名条目处理计划（供界面显示与决定提交时要不要先移动 / 建消歧义页）。"""
        info = self._disambig.as_dict() if self._disambig is not None else {"needed": False}
        info["canAct"] = login.is_logged_in()
        info["actionDone"] = bool(self._disambig and getattr(self._disambig, "step_page_done", False))
        return info

    def disambig_plan(self) -> dict:
        """同名条目处理的只读预览（准备怎么改，不动维基）。"""
        plan = self._disambig
        if plan is None or not plan.needed:
            return {"ok": True, "lines": [], "action": "none"}
        lines = [f"本条目将使用「{plan.our_title}」（原译名「{plan.base_title}」已被占用）"]
        for other in plan.others:
            lines.append(f"同名条目：{other.title}——{other.description}")
        if plan.mode == disambig.MODE_DISAMBIG:
            lines.append(f"提交时将在已有的消歧义页「{plan.base_title}」里补上本条目")
        elif plan.mode == disambig.MODE_MOVE:
            lines.append(f"提交时会先把「{plan.base_title}」不留重定向移动到"
                         f"「{plan.others[0].title if plan.others else ''}」，再建消歧义页，"
                         "并列出链入页面供确认替换")
        elif plan.note:
            lines.append(plan.note)
        return {"ok": True, "lines": lines, "action": plan.mode}

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
        """兼容旧接口：界面现在是主窗口里的标签页，不再需要关窗。

        html 版提交成功后会自动关闭窗口，那个调用点已经没了；保留这个方法只为
        不让外部脚本报错，永远返回「窗口不可用」。
        """
        return {"ok": False, "error": "窗口不可用（提交页现在是主窗口里的标签页）"}

    def fix_backlinks(self, titles_json: str) -> dict:
        """把链入页面里指向旧条目的链接改成新条目名（同名条目移动之后调用）。"""
        plan = self._disambig
        if plan is None or not plan.backlinks:
            return {"ok": False, "error": "没有待修正的链入页面"}
        try:
            titles = json.loads(titles_json or "[]")
        except ValueError:
            return {"ok": False, "error": "参数不是合法 JSON"}
        titles = [str(title) for title in titles if str(title).strip()]
        if not titles:
            return {"ok": False, "error": "没有选中任何页面"}
        moved = plan.others[0].title if plan.others else ""
        results = disambig.apply_backlinks(plan.base_title, moved, titles)
        changed = sum(1 for item in results if item.get("ok"))
        failed = [item for item in results if not item.get("ok")]
        message = f"已修正 {changed} 个页面的链入"
        if failed:
            message += f"；{len(failed)} 个未改动（" + "、".join(
                f"{item['title']}：{item.get('error')}" for item in failed) + "）"
        return {"ok": True, "message": message, "results": results}

    def submit(self, text: str, summary: str = "", sync_family: bool = False) -> dict:
        """先处理同名条目（移动 / 消歧义页），再上传封面、提交条目、建重定向、同步大家族模板。"""
        if not login.is_logged_in():
            return {"ok": False, "error": "未登录 Vocawiki，请在 wiki_credentials.yaml 中配置账号/机器人密码"}
        self._wikitext = text or ""
        self._write_local(self._wikitext)
        summary = summary or DEFAULT_SUMMARY

        messages = []
        backlinks: List[dict] = []
        if self._disambig is not None and self._disambig.needed:
            outcome = disambig.handle_submit(self._disambig, f"{summary}：同名条目消歧义")
            messages.extend(outcome.get("steps") or [])
            if not outcome.get("ok"):
                return {"ok": False, "error": "；".join([*messages, str(outcome.get("error", "同名条目处理失败"))])}
            titles = outcome.get("backlinks") or []
            if titles:
                backlinks = disambig.plan_backlinks(self._disambig.base_title,
                                                    self._disambig.others[0].title, titles)
        self._backlinks = backlinks

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
            "backlinks": backlinks,
            "backlinkOld": self._disambig.base_title if backlinks else "",
            "backlinkNew": (self._disambig.others[0].title if backlinks and self._disambig.others else ""),
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
                       family: Optional[FamilySync] = None,
                       disambig_plan=None) -> bool:
    """打开主窗口里的「提交」页。

    成功挂上界面返回 True；图形界面不可用时返回 False（调用方回退到写文件后用编辑器打开）。
    """
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，无法打开提交页。")
        return False
    return ui.open_submit_editor(page_name, wikitext, source_path, ja_name, create_redirect,
                                 cover, family, disambig_plan)
