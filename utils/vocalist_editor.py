"""歌姬模板功能的「数据侧 + 流程」：问名字 → 曲目页 / 样式页 → 写文件 → 提交页。

界面在 `utils/ui/vocalist_panel.py`（曲目）、`utils/ui/producer_style_panel.py`（样式，
与 P主模板共用）、`utils/ui/submit_panel.py`（提交，支持多页面），
本模块只放能在终端里单测的逻辑，与 `utils/producer_editor.py` 的分工一致。

流程（侧栏第三个功能「生成歌姬模板」，排在 P主模板 后面 —— 用户 2026-09-30 定的）：

    歌姬名（条目名）
      → 问要不要拆成年份子页（`Template:<歌姬>` 已经存在时默认拆；实测站上
        `Template:重音Teto` 就是拆的，`Template:歌爱雪` 没拆）
      → 抓分类 + 引擎殿堂页 + 歌曲条目，分栏分站点（拿不准的弹窗复核）
      → 曲目页（可改栏 / 站点 / 年份，可删，可恢复）
      → 样式页（配色；参考图默认是歌姬立绘，既有模板的样式会继承过来）
      → 写出各页文件：`歌姬模板_<名>_<年份>.wikitext` / `_doc.wikitext` / `_<名>.wikitext`
      → 提交页：多页面可切换，可以只提交当前页，也可以一次全提交
      → 提交成功后弹窗：把模板写进曲子条目与歌姬条目（拆分了就按年份写子页）
"""
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils import login, vocalist_template as vt, wiki_api
from utils.helpers import prompt_choices, prompt_response
from utils.producer_template import contains_template, looks_like_song_page
from utils.string import is_empty

DEFAULT_SUMMARY = "由 Vocawiki条目辅助工具 创建"
TEMPLATE_NAMESPACE = "Template:"
# 提交成功后那个弹窗的文案（提交页 `_show_backlink_dialog` 按这几个字段显示）
BACKLINK_TITLE = "把模板写进条目"
BACKLINK_ACTION = "写入选中条目"
BACKLINK_SKIP_NOTE = " —— 跳过"


def template_title(name: str) -> str:
    """`歌爱雪` → `Template:歌爱雪`（已经带前缀的原样返回）。"""
    value = str(name or "").strip()
    if not value:
        return TEMPLATE_NAMESPACE
    return value if value.startswith(TEMPLATE_NAMESPACE) else f"{TEMPLATE_NAMESPACE}{value}"


def bare_template_name(name: str) -> str:
    """`Template:歌爱雪` → `歌爱雪`（`{{…}}` 里写的名字不带命名空间）。"""
    return str(name or "").strip().split(":", 1)[-1].strip()


# ============================================================ 提问

def ask_name() -> str:
    """问歌姬名（条目名）；留空放弃。"""
    return str(prompt_response(
        "歌姬名（写维基上的条目名，例如 歌爱雪 / 重音Teto；留空放弃这次生成）") or "").strip()


def ask_split(name: str, exists: bool) -> bool:
    """问要不要拆成年份子页：**既有的模板默认拆**（用户 2026-09-30 要求）。"""
    hint = (f"维基上已经有 Template:{name} 了，默认按年份拆分（像 Template:重音Teto 那样："
            "主模板只留各年份的转接，每一年一个子页）") if exists else \
        (f"维基上还没有 Template:{name}。曲子多的话建议按年份拆成子页"
         "（像 Template:重音Teto），曲子少就一个页面写完")
    choices = ["拆成年份子页（Template:X/2024 …）", "不拆，一个页面写完整"]
    index = prompt_choices(f"{hint}\n怎么生成？", choices)
    return index != 2


# ============================================================ 提交页的数据侧

class VocalistTemplateApi:
    """「提交」页的数据侧：多页面（主模板 / 各年份子页 / 文档）一次挂上去。

    接口形状与 `utils.producer_editor.ProducerTemplateApi` 一致，另外多了给提交页用的
    多页面三部曲：`pages()` / `select(index)` / `set_text(text)`。
    """

    def __init__(self, work: vt.VocalistWork, specs: Sequence[dict]):
        self.work = work
        self.specs = [dict(spec) for spec in specs]
        self._index = 0
        self._texts: List[str] = [str(spec.get("text") or "") for spec in self.specs]
        self._saved: List[str] = list(self._texts)
        self._submitted: List[bool] = [False] * len(self.specs)

    # —— 多页面 ——
    def pages(self) -> List[dict]:
        """提交页要显示的页面清单（顺序 = 提交顺序）。"""
        return [{"name": spec["name"], "file": str(spec["file"]), "kind": spec["kind"],
                 "note": spec.get("note") or ""} for spec in self.specs]

    def select(self, index: int) -> Optional[str]:
        """切到第 index 页；返回那一页的 wikitext（越界返回 None）。"""
        index = int(index)
        if not 0 <= index < len(self.specs):
            return None
        self._index = index
        return self._texts[index]

    def current(self) -> dict:
        return self.specs[self._index] if self.specs else {}

    def set_text(self, text: str) -> None:
        """把界面上编辑过的正文存回内存（切页面 / 保存 / 提交前调）。"""
        if self.specs:
            self._texts[self._index] = str(text)

    def text_of(self, index: int) -> str:
        return self._texts[int(index)] if 0 <= int(index) < len(self._texts) else ""

    @property
    def _wikitext(self) -> str:
        return self._texts[self._index] if self._texts else ""

    # —— 提交页需要的上下文 ——
    def get_context(self) -> dict:
        spec = self.current()
        return {
            "kind": "template",
            "page": spec.get("name") or "",
            "file": str(spec.get("file") or ""),
            "pageUrl": wiki_api.article_url(str(spec.get("name") or "")),
            "origin": wiki_api.origin(),
            "summary": f"{DEFAULT_SUMMARY}：歌姬模板",
            "createRedirect": False,
            "redirect": "",
            "redirectTarget": "",
            "canSubmit": login.is_logged_in(),
            "cover": None,
            "family": {"available": False, "templates": [], "producers": [], "honors": [],
                       "collections": []},
            "disambig": {"needed": False, "note": "模板页，不做同名条目处理"},
            "entries": len(vt.template_links(self._wikitext)),
            "pages": self.pages(),
            "pageIndex": self._index,
        }

    def preview(self, text: str) -> dict:
        """渲染当前页（标题用页面名，`{{PAGENAME}}` 才对）。"""
        page = str(self.current().get("name") or "")
        return wiki_api.parse_wikitext(text or "", title=page)

    def save(self, text: str) -> dict:
        self.set_text(text)
        if self._write_local(self._index):
            return {"ok": True, "message": f"已保存到本地文件："
                                          f"{Path(self.specs[self._index]['file']).name}"}
        return {"ok": False, "error": "写入本地文件失败"}

    def open_page(self) -> dict:
        url = wiki_api.article_url(str(self.current().get("name") or ""))
        try:
            webbrowser.open(url)
            return {"ok": True, "url": url}
        except Exception as e:                          # noqa: BLE001
            logging.error("无法打开模板页面：%s", e)
            return {"ok": False, "error": str(e)}

    def close_window(self) -> dict:
        return {"ok": False, "error": "窗口不可用（提交页现在是主窗口里的标签页）"}

    # —— 提交 ——
    def submit(self, text: str, summary: str = "", sync_family: bool = False) -> dict:
        """提交**当前这一页**（多页面时由提交页的「全部提交」循环调）。"""
        self.set_text(text)
        return self._submit_index(self._index, summary)

    def submit_all(self, summary: str = "", progress=None) -> dict:
        """把**还没提交过、或提交后又改过**的页面全部提交（顺序：年份子页 → 文档 → 主模板）。"""
        results: List[dict] = []
        for index, spec in enumerate(self.specs):
            if self._submitted[index] and self._saved[index] == self._texts[index]:
                results.append({"name": spec["name"], "ok": True, "skipped": True})
                if progress is not None:
                    progress(f"跳过「{spec['name']}」（内容没变）")
                continue
            if progress is not None:
                progress(f"正在提交「{spec['name']}」…")
            result = self._submit_index(index, summary)
            results.append({"name": spec["name"], "ok": bool(result.get("ok")),
                            "error": result.get("error")})
            if progress is not None:
                progress(("✓ 已提交「" + str(spec["name"]) + "」") if result.get("ok")
                         else f"✗ 「{spec['name']}」提交失败：{result.get('error')}")
        failed = [item for item in results if not item.get("ok")]
        done = [item for item in results if item.get("ok") and not item.get("skipped")]
        skipped = [item for item in results if item.get("skipped")]
        if failed and not done:
            return {"ok": False, "error": "；".join(
                f"{item['name']}：{item.get('error')}" for item in failed)}
        message = f"已提交 {len(done)} 个页面"
        if skipped:
            message += f"（{len(skipped)} 个内容没变，跳过）"
        if failed:
            message += f"；{len(failed)} 个失败"
        return self._result(message, ok=not failed, results=results)

    def _submit_index(self, index: int, summary: str = "") -> dict:
        spec = self.specs[index]
        page = str(spec.get("name") or "")
        if not login.is_logged_in():
            return {"ok": False, "error": "未登录 Vocawiki，请在 wiki_credentials.yaml 中配置"
                                          "账号/机器人密码"}
        self._write_local(index)
        result = wiki_api.edit_page(page, self._texts[index],
                                    summary or f"{DEFAULT_SUMMARY}：歌姬模板")
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "提交失败")}
        self._saved[index] = self._texts[index]
        self._submitted[index] = True
        return self._result(f"已提交「{page}」")

    def _result(self, message: str, ok: bool = True, results: Optional[List[dict]] = None) -> dict:
        """提交成功后交给界面的一整套结果（含回写条目那一份计划）。"""
        name = self.work.name
        call = f"{{{{{name}/年份|nocate=1}}}}" if self.work.split else f"{{{{{name}}}}}"
        return {
            "ok": ok,
            "message": message,
            "url": wiki_api.article_url(self.work.template_title),
            "backlinks": self.plan_entries(),
            "backlinkTitle": BACKLINK_TITLE,
            "backlinkHeader": (f"把歌姬模板写进歌姬条目与这些曲子条目（歌曲写 {call}，"
                               "插在各条目「注释」小节的 <references/> 后面）"),
            "backlinkAction": BACKLINK_ACTION,
            "backlinkSkipNote": BACKLINK_SKIP_NOTE,
            "results": results or [],
        }

    def plan_entries(self) -> List[dict]:
        """回写名单：**歌姬条目**在最前，其后是模板里列到的曲子条目。

        每行 `{'title','count','kind','note'}`：能写的 `count=1`，该跳过的 `count=0`
        （界面上灰掉）。看着不像歌曲条目的页面（专辑页 / 榜单页 / 别人的条目）也灰掉 ——
        与 P主模板那套 `plan_entries()` 同一个口径。
        """
        titles = self._titles()
        texts = wiki_api.fetch_pages_text(titles) if titles else {}
        entries: List[dict] = []
        for title in titles:
            body = texts.get(title)
            call_name = vt.template_call_for(self.work, title)[0]
            if body is None:
                entries.append({"title": title, "count": 0, "kind": "条目还没建",
                                "note": "条目还没建"})
            elif contains_template(body, call_name):
                entries.append({"title": title, "count": 0, "kind": "已有本模板",
                                "note": "已有本模板"})
            elif title != self.work.name and not looks_like_song_page(body):
                entries.append({"title": title, "count": 0, "kind": "不是歌曲条目",
                                "note": "看着不像歌曲条目（没有信息框）"})
            else:
                entries.append({"title": title, "count": 1, "kind": "加入本模板",
                                "note": "加入本模板"})
        return entries

    def _titles(self) -> List[str]:
        """回写名单的标题（歌姬条目 + 每一页里列到的曲子，按出现顺序去重）。"""
        titles: List[str] = []
        if self.work.name:
            titles.append(self.work.name)
        for text in self._texts:
            for title in vt.template_links(text):
                if title not in titles:
                    titles.append(title)
        return titles

    def fix_backlinks(self, titles_json: str, progress=None) -> dict:
        """把模板写进选中的条目（`progress` 逐页回调，提交页用它一条一条冒提示）。"""
        try:
            titles = json.loads(titles_json or "[]")
        except ValueError:
            return {"ok": False, "error": "参数不是合法 JSON"}
        titles = [str(title).strip() for title in titles if str(title).strip()]
        if not titles:
            return {"ok": False, "error": "没有选中任何条目"}
        results = vt.insert_into_pages_for(self.work, titles, progress=progress)
        changed = sum(1 for item in results if item.get("ok") and item.get("count"))
        skipped = [item for item in results if not (item.get("ok") and item.get("count"))]
        message = f"已把模板写进 {changed} 个条目"
        if skipped:
            message += f"；{len(skipped)} 个未改动（" + "、".join(
                f"{item['title']}：{item.get('error') or item.get('kind') or '未改动'}"
                for item in skipped) + "）"
        return {"ok": True, "message": message, "results": results}

    def _write_local(self, index: int) -> bool:
        spec = self.specs[index]
        path = Path(spec["file"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._texts[index], encoding="utf-8")
            return True
        except OSError as e:
            logging.error("无法写回模板文件 %s：%s", path, e)
            return False


# ============================================================ 流程

def open_works_editor(work: vt.VocalistWork) -> Optional[vt.VocalistWork]:
    """打开「曲目」页；用户取消返回 None。"""
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，跳过曲目页。")
        return None
    return ui.open_vocalist_works(work)


def open_style_editor(work: vt.VocalistWork) -> Optional[Dict[str, str]]:
    """打开「样式」页；用户取消返回 None。"""
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，跳过样式页。")
        return None
    return ui.open_vocalist_style(work)


def generate_vocalist_template() -> Optional[Path]:
    """侧栏第三个功能的完整流程；返回主模板写出的本地文件（中途放弃返回 None）。

    终端模式（`--console --vocalist`）没有界面：跳过曲目页 / 样式页 / 提交页，
    直接按默认样式把各页写到输出目录并打开那个文件夹。
    """
    from utils import ui
    name = ask_name()
    if is_empty(name):
        ui.status("已取消生成歌姬模板")
        logging.info("已取消生成歌姬模板")
        return None
    bare = bare_template_name(name)
    exists = bool(wiki_api.fetch_page_facts(f"{TEMPLATE_NAMESPACE}{bare}").get("exists"))
    split = ask_split(bare, exists)
    ui.status(f"正在抓「{bare}」的曲目（分类 + 引擎殿堂页 + 歌曲条目），会慢一点…")
    try:
        work = vt.prepare_work(bare, split, progress=ui.status)
    except ValueError as error:
        logging.error("生成歌姬模板失败：%s", error)
        ui.status(f"生成歌姬模板失败：{error}")
        return None
    vt.load_existing(work)
    logging.info("「%s」：%s", bare, work.summary)

    if ui.is_active():
        ui.status("曲目页已打开：可以改栏 / 站点 / 年份，改完点「保存并继续」")
        edited = open_works_editor(work)
        if edited is None:
            ui.status("已取消生成歌姬模板")
            return None
        work = edited
        ui.status("样式页已打开：调配色（参考图默认是歌姬立绘），改完点「保存并继续」")
        styles = open_style_editor(work)
        if styles is not None:
            work.styles = {**work.styles, **styles}

    specs = vt.page_specs(work)
    vt.write_pages(specs)
    main_path = next((Path(spec["file"]) for spec in specs if spec["kind"] == "main"), None)
    logging.info("歌姬模板已写出 %d 个页面：%s", len(specs),
                 "、".join(Path(spec["file"]).name for spec in specs))
    if not ui.is_active():
        print(f"歌姬模板已写出 {len(specs)} 个页面："
              + "、".join(str(spec["file"]) for spec in specs))
        if main_path is not None:
            ui.open_folder(main_path)
        return main_path

    api = VocalistTemplateApi(work, specs)
    if ui.open_template_submit(api):
        return main_path
    if main_path is not None:
        ui.open_folder(main_path)
    return main_path


__all__ = ["VocalistTemplateApi", "generate_vocalist_template", "ask_name", "ask_split",
           "template_title", "bare_template_name", "DEFAULT_SUMMARY"]
