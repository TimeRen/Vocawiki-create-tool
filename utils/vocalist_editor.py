"""歌姬模板功能的「数据侧 + 流程」：问名字 → 曲目页 / 样式页 → 写文件 → 提交页。

界面在 `utils/ui/vocalist_panel.py`（曲目）、`utils/ui/producer_style_panel.py`（样式，
与 P主模板共用）、`utils/ui/submit_panel.py`（提交，支持多页面），
本模块只放能在终端里单测的逻辑，与 `utils/producer_editor.py` 的分工一致。

流程（侧栏第三个功能「生成歌姬模板」，排在 P主模板 后面 —— 用户 2026-09-30 定的）：

    歌姬名（条目名）
      → 问怎么生成（`ask_layout()`）：拆成年份子页 + 重写主模板 / **只新建年份子页、
        不动既有主模板**（`Template:初音未来` 那种手写大导航框）/ 不拆一个页面写完整
        （`Template:<歌姬>` 已经存在时默认拆；实测站上 `Template:重音Teto` 就是拆的，
        `Template:歌爱雪` 没拆）
      → 选了「不拆」时再问一句：「其他」栏的曲目**平铺**还是**按年份分层**
        （`ask_other_layout()`，两种站上都有）
      → 抓分类 + 引擎殿堂页 + 歌曲条目，分栏分站点（拿不准的弹窗复核）
      → 曲目页（可改栏 / 站点 / 年份，可删，可恢复）
      → 样式页（配色；参考图默认是歌姬立绘，既有模板的样式会继承过来）
      → 写出各页文件：`歌姬模板_<名>_<年份>.wikitext` / `_doc.wikitext` / `_<名>.wikitext`
      → 提交页：多页面可切换，可以只提交当前页，也可以一次全提交
      → 提交成功后弹窗：把 `{{歌姬/年份|collapsed}}` 写进曲子条目、把 `{{歌姬|nocate=1}}`
        写进歌姬条目（位置：注释小节的 <references/> 后面、排在 P主模板之后活动模板之前）
"""
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from utils import login, vocalist_template as vt, wiki_api
from utils.helpers import prompt_choices, prompt_response
from utils.producer_template import looks_like_song_page, template_state
from utils.string import is_empty

DEFAULT_SUMMARY = "由 Vocawiki条目辅助工具 创建"
TEMPLATE_NAMESPACE = "Template:"
# 三种生成方式（`ask_layout()` 的返回值，用户 2026-09-30 定的三种）
LAYOUT_SPLIT = "split"                 # 拆成年份子页 + 重写主模板（站上 Template:重音Teto 的形态）
LAYOUT_SUBPAGES_ONLY = "subpages"      # 只新建年份子页，不动既有主模板（初音未来 那种手写大导航框）
LAYOUT_ONE_PAGE = "single"             # 不拆，一个页面写完整
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


def ask_layout(name: str, exists: bool) -> str:
    """问怎么生成 → `LAYOUT_SPLIT` / `LAYOUT_SUBPAGES_ONLY` / `LAYOUT_ONE_PAGE`。

    * `Template:<歌姬>` **已经存在**（用户 2026-09-30 要求）：多给一个
      「只新建年份子页，不动既有主模板」—— 适合 `Template:初音未来` 那种**手写大导航框**
      （它是 `{{Navbox with collapsible groups}}` + `|selected`，分组是「角色 / 官方专辑 /
      2009年…2019年 / 演唱会 …」，并不是按年份拆子页的结构）：既有主模板原样不动，
      我们只把 `Template:<歌姬>/<年份>` 写好，之后自己把子页挂上去。
    * 默认还是「拆成年份子页 + 重写主模板」（站上 `Template:重音Teto` 就是这个形态）。
    """
    if exists:
        hint = (f"维基上已经有 Template:{name} 了。\n"
                "  · 拆成年份子页：像 Template:重音Teto 那样，主模板只留各年份的转接，每一年一个子页；\n"
                f"  · 只新建年份子页：**不动** Template:{name}（那种手写大导航框别让工具重写），\n"
                "    只写出各年份子页，之后自己把子页挂上去；\n"
                "  · 不拆：把主模板整个重写成一个完整导航框（曲子多的话会很长）")
    else:
        hint = (f"维基上还没有 Template:{name}。曲子多的话建议按年份拆成子页"
                "（像 Template:重音Teto），曲子少就一个页面写完")
    choices = ["拆成年份子页，并重写主模板（Template:X/2024 …）",
               "只新建年份子页，不动既有主模板",
               "不拆，一个页面写完整"]
    index = prompt_choices(f"{hint}\n怎么生成？", choices)
    if index == 3:
        return LAYOUT_ONE_PAGE
    if index == 2 and exists:
        return LAYOUT_SUBPAGES_ONLY
    return LAYOUT_SPLIT


def ask_other_layout(name: str) -> bool:
    """问「其他」栏里的曲目怎么排 → True = **按年份分层**，False = 平铺（用户 2026-09-30）。

    两种站上都有人用：

    * 平铺（默认，站上主流）：NurseRobot TypeT / 琴叶茜 / 琴叶葵 / 双叶凑音 / SeeU；
    * 按年份分层：里命 / 狐子 / 鸣花姬·尊（`其他 → 2022年 / 2023年 → 曲目`）。

    只在「不拆，一个页面写完整」时才问：拆成年份子页时每页的年份已经固定了，
    「其他」栏再按年份分层没有意义。
    """
    choices = ["平铺（站上多数模板：NurseRobot TypeT / 琴叶茜 / SeeU …）",
               "按年份分层（里命 / 狐子 / 鸣花姬·尊：其他 → 2022年 / 2023年 → 曲目）"]
    index = prompt_choices(
        f"「{name}」主模板里「其他」栏的曲目怎么排？（这里只是定个默认值，\n"
        "生成后在「曲目」页上还能随时切，改完预览立刻跟着变）\n"
        "（两者站上都有；只有「其他」栏受影响，殿堂 / 传说 / 神话那几栏不变）", choices)
    return index == 2


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
        # 用户手动标「跳过」的页（「全部提交」不会碰它们，用户 2026-09-30 要求）
        self._skipped: List[bool] = [False] * len(self.specs)
        # 这些页**维基上有没有**（`check_existing()` 查过一次就缓存；没查过是 None）
        self._exists: Optional[Dict[int, Optional[bool]]] = None

    # —— 多页面 ——
    def pages(self) -> List[dict]:
        """提交页要显示的页面清单（顺序 = 提交顺序）。

        `exists`：维基上已有这一页（未查过是 `None`）；`skipped`：被标成跳过了。
        """
        return [{"name": spec["name"], "file": str(spec["file"]), "kind": spec["kind"],
                 "note": spec.get("note") or "",
                 "exists": self.existing(index), "skipped": bool(self._skipped[index])}
                for index, spec in enumerate(self.specs)]

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

    # —— 跳过（用户 2026-09-30 要求）——
    def check_existing(self) -> Dict[int, Optional[bool]]:
        """批量问「这些页面**维基上有没有**」→ `{页码: 有/没有/不知道}`（只查一次）。

        提交页拿它做三件事：列表里标「维基上已有」、「跳过已存在的」按它标跳过、
        分类页只在**确定还没有**时才建（已有的分类页可能是别人写的，不能覆盖）。
        查不出来时记 `None`（不知道）—— 这种情况**不**当「没有」。
        """
        if self._exists is None:
            titles = [str(spec.get("name") or "") for spec in self.specs]
            try:
                found = wiki_api.pages_exist(titles) or {}
            except Exception as e:                      # noqa: BLE001 - 查不到就当不知道
                logging.warning("查页面在不在失败：%s", e)
                found = {}
            self._exists = {index: found.get(title) for index, title in enumerate(titles)}
        return self._exists

    def existing(self, index: int) -> Optional[bool]:
        """第 index 页维基上有没有（`None` = 还没查 / 查不出来）。"""
        if self._exists is None:
            return None
        return self._exists.get(int(index))

    def skipped(self, index: int) -> bool:
        return 0 <= int(index) < len(self._skipped) and bool(self._skipped[int(index)])

    def skip(self, index: int, value: bool = True) -> dict:
        """把第 index 页标成「跳过」/ 取消跳过（「全部提交」不会提交被跳过的页）。"""
        index = int(index)
        if not 0 <= index < len(self.specs):
            return {"ok": False, "error": "页码越界"}
        self._skipped[index] = bool(value)
        return {"ok": True, "index": index, "skipped": self._skipped[index],
                "name": str(self.specs[index].get("name") or "")}

    def skip_existing(self) -> dict:
        """把所有「维基上已经存在」的页面一次标成跳过（用户 2026-09-30 要求）。

        典型场景：年份子页之前已经传过（或在站上手改过，例如自己补了分类），
        不想再被工具覆盖。可以逐页再取消（`skip(i, False)`）。
        """
        exists = self.check_existing()
        marked: List[str] = []
        for index, found in sorted(exists.items()):
            if not found:
                continue                                  # None（不知道）/ False（没有）都不动
            if not self._skipped[index]:
                marked.append(str(self.specs[index].get("name") or ""))
            self._skipped[index] = True
        already = [str(spec.get("name") or "")
                   for index, spec in enumerate(self.specs) if self._skipped[index]]
        return {"ok": True, "skipped": marked, "count": len(marked), "all": already,
                "known": sum(1 for found in exists.values() if found)}

    def pending(self) -> List[int]:
        """还没交、也没标跳过的页码（提交页据此决定交完这一页要不要收摊）。

        「已经交过、之后没改」的页不算（交不交都一样）；拆成年份子页时十几二十页，
        分几次交很常见，所以交完一页不能就把窗口收了（用户 2026-09-30）。
        """
        return [index for index, _spec in enumerate(self.specs)
                if not self._skipped[index]
                and not (self._submitted[index] and self._saved[index] == self._texts[index])]

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
            "skipped": self.skipped(self._index),
            "exists": self.existing(self._index),
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
        """提交**当前这一页**（多页面时由提交页的「全部提交」循环调）。

        手动点「提交到 Vocawiki」会**取消**这一页的「跳过」—— 用户明摆着就是想传它。
        """
        self.set_text(text)
        self._skipped[self._index] = False
        return self._submit_index(self._index, summary)

    def submit_all(self, summary: str = "", progress=None) -> dict:
        """把**还没提交过、或提交后又改过**的页面全部提交（顺序：分类页 → 年份子页 → 文档 → 主模板）。

        被标成「跳过」的页（用户手动标的 / 「跳过已存在的」标的）一概不碰；
        分类页维基上已经有了也自动跳过（不覆盖既有的分类页）。
        """
        results: List[dict] = []
        for index, spec in enumerate(self.specs):
            if self._skipped[index]:
                results.append({"name": spec["name"], "ok": True, "skipped": True})
                if progress is not None:
                    progress(f"跳过「{spec['name']}」（已标记跳过）")
                continue
            if self._submitted[index] and self._saved[index] == self._texts[index]:
                results.append({"name": spec["name"], "ok": True, "skipped": True})
                if progress is not None:
                    progress(f"跳过「{spec['name']}」（内容没变）")
                continue
            if progress is not None:
                progress(f"正在提交「{spec['name']}」…")
            result = self._submit_index(index, summary)
            if result.get("skipped"):
                results.append({"name": spec["name"], "ok": True, "skipped": True,
                                "reason": result.get("reason") or ""})
                if progress is not None:
                    progress(f"跳过「{spec['name']}」（{result.get('reason') or '不需要提交'}）")
                continue
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
            message += f"（{len(skipped)} 个跳过）"
        if failed:
            message += f"；{len(failed)} 个失败"
        return self._result(message, ok=not failed, results=results)

    def _submit_index(self, index: int, summary: str = "") -> dict:
        spec = self.specs[index]
        page = str(spec.get("name") or "")
        if not login.is_logged_in():
            return {"ok": False, "error": "未登录 Vocawiki，请在 wiki_credentials.yaml 中配置"
                                          "账号/机器人密码"}
        if spec.get("kind") == "category":
            known = self._exists_on_wiki(page)
            if known is not False:
                self._skipped[index] = True
                reason = ("维基上已经有这个分类了" if known
                          else "没查出来这个分类在不在，这次先不动它")
                return {"ok": True, "skipped": True, "reason": reason,
                        "message": f"「{page}」{reason}，跳过（不覆盖既有分类页）"}
        self._write_local(index)
        result = wiki_api.edit_page(page, self._texts[index],
                                    summary or f"{DEFAULT_SUMMARY}：歌姬模板")
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "提交失败")}
        self._saved[index] = self._texts[index]
        self._submitted[index] = True
        return self._result(f"已提交「{page}」")

    def _exists_on_wiki(self, page: str) -> Optional[bool]:
        """这一页维基上有没有（`None` = 查不出来）。先看 `check_existing()` 的缓存。"""
        for index, spec in enumerate(self.specs):
            if str(spec.get("name") or "") == page:
                known = self.existing(index)
                if known is not None:
                    return known
        try:
            found = wiki_api.pages_exist([page]) or {}
        except Exception as e:                          # noqa: BLE001 - 查不到就说不知道
            logging.warning("查「%s」在不在失败：%s", page, e)
            return None
        return found.get(page) if page in found else None

    def _result(self, message: str, ok: bool = True, results: Optional[List[dict]] = None) -> dict:
        """提交成功后交给界面的一整套结果（含回写条目那一份计划）。"""
        name = self.work.name
        song_call = f"{{{{{name}/年份|collapsed}}}}" if self.work.split else \
            f"{{{{{name}|collapsed}}}}"
        # 只新建年份子页时主模板不是我们动的，链接就指向当前页
        target = self.current().get("name") if self.work.subpages_only else \
            self.work.template_title
        return {
            "ok": ok,
            "message": message,
            "url": wiki_api.article_url(str(target or self.work.template_title)),
            "backlinks": self.plan_entries(),
            "backlinkTitle": BACKLINK_TITLE,
            "backlinkHeader": (f"曲子条目写 {song_call}、歌姬条目写 {{{{{name}|nocate=1}}}}；"
                               "插在各条目「注释」小节的 <references/> 后面"
                               "（排在 P主模板之后、活动模板之前），"
                               f"并把曲子里手写的「{name}歌曲」分类删掉（模板会自己加）"),
            "backlinkAction": BACKLINK_ACTION,
            "backlinkSkipNote": BACKLINK_SKIP_NOTE,
            "results": results or [],
        }

    def plan_entries(self) -> List[dict]:
        """回写名单：**歌姬条目**在最前，其后是模板里列到的曲子条目。

        每行 `{'title','count','kind','note'}`：能写的 `count=1`，该跳过的 `count=0`
        （界面上灰掉）。看着不像歌曲条目的页面（专辑页 / 榜单页 / 别人的条目）也灰掉 ——
        与 P主模板那套 `plan_entries()` 同一个口径。

        **页面里已经有这个模板时不跳过**：写法不一样（`{{歌爱雪}}` / 旧年份子页 /
        少了 `|collapsed`）就改写成目标写法（用户 2026-09-30 报的：「已经加入了模板不会执行
        替换，就没法加入年份和 `|collapsed`」）；带着别的参数（`|state=…`）的不动。
        """
        titles = self._titles()
        texts = wiki_api.fetch_pages_text(titles) if titles else {}
        entries: List[dict] = []
        for title in titles:
            body = texts.get(title)
            call_name, call = vt.template_call_for(self.work, title)
            state, found = (template_state(body, call_name, call) if body is not None
                            else ("missing", []))
            if body is None:
                entries.append({"title": title, "count": 0, "kind": "条目还没建",
                                "note": "条目还没建"})
            elif state == "exact":
                entries.append({"title": title, "count": 0, "kind": "已有本模板",
                                "note": "已有本模板"})
            elif state == "rewritable":
                # 已经有了、只是写法不对（主模板 / 旧年份 / 少了 |collapsed）→ 可以改写成目标写法
                entries.append({"title": title, "count": 1, "kind": "改写模板",
                                "note": f"{found[0]} → {{{{{call}}}}}"})
            elif state == "other":
                entries.append({"title": title, "count": 0,
                                "kind": "已有本模板（带其它参数）",
                                "note": "已有本模板，且带其它参数（不动它）"})
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
    layout = ask_layout(bare, exists)
    split = layout != LAYOUT_ONE_PAGE
    # 「其他」栏怎么排只在「不拆」时才有意义（拆分时每页的年份已经固定）
    other_years = ask_other_layout(bare) if not split else False
    ui.status(f"正在抓「{bare}」的曲目（分类 + 引擎殿堂页 + 歌曲条目），会慢一点…")
    try:
        work = vt.prepare_work(bare, split,
                               subpages_only=(layout == LAYOUT_SUBPAGES_ONLY),
                               other_years=other_years, progress=ui.status)
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
    # 「只新建年份子页」时没有主模板，就打开 / 返回第一个年份子页（分类页不算）
    first_year = next((Path(spec["file"]) for spec in specs if spec["kind"] == "year"), None)
    target = main_path or first_year or (Path(specs[0]["file"]) if specs else None)
    logging.info("歌姬模板已写出 %d 个页面：%s", len(specs),
                 "、".join(Path(spec["file"]).name for spec in specs))
    if not ui.is_active():
        print(f"歌姬模板已写出 {len(specs)} 个页面："
              + "、".join(str(spec["file"]) for spec in specs))
        if target is not None:
            ui.open_folder(target)
        return target

    api = VocalistTemplateApi(work, specs)
    if ui.open_template_submit(api):
        return target
    if target is not None:
        ui.open_folder(target)
    return target


__all__ = ["VocalistTemplateApi", "generate_vocalist_template", "ask_name", "ask_layout",
           "ask_other_layout", "template_title", "bare_template_name", "DEFAULT_SUMMARY",
           "LAYOUT_SPLIT", "LAYOUT_SUBPAGES_ONLY", "LAYOUT_ONE_PAGE"]
