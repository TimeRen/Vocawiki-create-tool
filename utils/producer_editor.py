"""P主模板功能的「数据侧 + 流程」：取 VocaDB → 曲目页 / 样式页 → 写文件 → 提交页。

界面在 `utils/ui/producer_panel.py`（曲目）、`utils/ui/producer_style_panel.py`（样式）
与共用的 `utils/ui/submit_panel.py`（提交），本模块只放能在终端里单测的逻辑，
与 `utils/color_editor.py` / `utils/submit_editor.py` 的分工一致。

流程（侧栏第二个功能「生成P主模板」）：

    P主名 / VocaDB 链接
      → 拉 VocaDB 的原创曲目（按投稿年分格）与专辑，并用 P主条目补中文条目名
      → 曲目页（可增删改，行内编辑 + 从维基补名）
      → 样式页（标题栏 / 分组栏 / 列表 三组颜色，可用 AI 按参考图配色）
      → 写出 `output/P主模板_<P主名>.wikitext`
      → 提交页：预览 / 编辑 / 提交 `Template:<P主名>`
      → 提交成功后弹窗：把 `{{<P主名>}}` 加进模板列出的那些曲目条目
        （插在各条目「注释」小节的 `<references/>` 后面；注释标题上方已有的大家族模板
        会一并挪进小节、排在新模板后面）"""
import json
import logging
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from config.config import get_output_path
from utils import login, producer_template, wiki_api
from utils.helpers import prompt_choices, prompt_response
from utils.producer_template import ProducerArtist, ProducerWork
from utils.string import is_empty, safe_filename

DEFAULT_SUMMARY = "由 Vocawiki条目辅助工具 创建"
TEMPLATE_NAMESPACE = "Template:"
# 提交成功后那个弹窗的文案（提交页 `_show_backlink_dialog` 按这几个字段显示）
BACKLINK_TITLE = "把模板加进条目"
BACKLINK_ACTION = "写入选中条目"
BACKLINK_SKIP_NOTE = " —— 条目还没建，跳过"
# 写进条目的写法：带 `|collapsed`（导航框很长，在条目里默认折叠；用户 2026-09-30）
# —— 与歌姬模板（`{{<歌姬>/<年份>|collapsed}}`）同一个口径。
COLLAPSED_PARAM = "collapsed"


def template_title(name: str) -> str:
    """`雄之助` → `Template:雄之助`（已经带前缀的原样返回）。"""
    value = str(name or "").strip()
    if not value:
        return TEMPLATE_NAMESPACE
    if value.startswith(TEMPLATE_NAMESPACE):
        return value
    return f"{TEMPLATE_NAMESPACE}{value}"


def bare_template_name(name: str) -> str:
    """`Template:雄之助` → `雄之助`（`{{…}}` 里写的名字不带命名空间）。"""
    return str(name or "").strip().split(":", 1)[-1].strip()


def output_path(name: str) -> Path:
    """模板 wikitext 的输出文件（写在输出目录里，和条目 wikitext 放一起）。"""
    return get_output_path().joinpath(f"P主模板_{safe_filename(bare_template_name(name))}.wikitext")


# ============================================================ 取素材

def prepare_work(artist: ProducerArtist) -> ProducerWork:
    """按 VocaDB 的 P主 拉素材：曲目 + 专辑，再用 P主条目补中文条目名。"""
    work = ProducerWork(artist=artist, page_name=artist.name, template_name=artist.name)
    work.songs = producer_template.fetch_songs(artist.id)
    work.albums = producer_template.fetch_albums(artist.id)
    logging.info("VocaDB 上「%s」有 %d 首原创曲目、%d 张专辑",
                 artist.name, len(work.songs), len(work.albums))
    return work


def choose_artist(query: str) -> Optional[ProducerArtist]:
    """问 P主名 / id / 链接 → 一个 `ProducerArtist`（找不到就再问；用户留空则放弃）。"""
    name = str(query or "").strip() or prompt_response(
        "P主名（也可以是 VocaDB 艺术家链接或 id，例如 https://vocadb.net/Artist/Details/23981）")
    while True:
        artists = producer_template.search_artists(name)
        if not artists:
            name = prompt_response("VocaDB 上没找到这个 P主，换个名字 / 链接再试"
                                   "（留空放弃这次生成）")
            if is_empty(name):
                return None
            continue
        if len(artists) == 1:
            return artists[0]
        index = prompt_choices("VocaDB 上找到多个同名 / 相似的 P主，选一个：",
                               [artist.label() for artist in artists])
        return artists[max(0, min(len(artists), index)) - 1]


# ============================================================ 提交页的数据侧

class ProducerTemplateApi:
    """「提交」页的数据侧：提交 `Template:<P主>`，并回写模板列出的条目。

    接口形状与 `utils.submit_editor.SubmitApi` 一致（同一个提交页两边共用）。
    """

    def __init__(self, work: ProducerWork, source_path,
                 wikitext: str = ""):
        self.work = work
        self.page_name = template_title(work.template_name or work.artist.name)
        self._source_path = Path(source_path)
        self._wikitext = wikitext or ""

    # —— 提交页需要的上下文 ——
    def get_context(self) -> dict:
        return {
            "kind": "template",
            "page": self.page_name,
            "file": str(self._source_path),
            "pageUrl": wiki_api.article_url(self.page_name),
            "origin": wiki_api.origin(),
            "summary": f"{DEFAULT_SUMMARY}：P主模板",
            "createRedirect": False,
            "redirect": "",
            "redirectTarget": "",
            "canSubmit": login.is_logged_in(),
            "cover": None,
            "family": {"available": False, "templates": [], "producers": [], "honors": [],
                       "collections": []},
            "disambig": {"needed": False, "note": "模板页，不做同名条目处理"},
            "entries": len(producer_template.template_links(self._wikitext)),
        }

    def preview(self, text: str) -> dict:
        """渲染模板（标题用模板页名，`{{PAGENAME}}` 才对）。"""
        return wiki_api.parse_wikitext(text or "", title=self.page_name)

    def save(self, text: str) -> dict:
        if self._write_local(text):
            return {"ok": True, "message": "已保存到本地文件"}
        return {"ok": False, "error": "写入本地文件失败"}

    def open_page(self) -> dict:
        url = wiki_api.article_url(self.page_name)
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
        """写入模板页；顺带算好「要加进哪些条目」，交给界面弹窗让用户勾选。"""
        if not login.is_logged_in():
            return {"ok": False,
                    "error": "未登录 Vocawiki，请在 wiki_credentials.yaml 中配置账号/机器人密码"}
        self._wikitext = text or ""
        self._write_local(self._wikitext)
        result = wiki_api.edit_page(self.page_name, self._wikitext,
                                    summary or f"{DEFAULT_SUMMARY}：P主模板")
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "提交失败")}
        entries = self.plan_entries(self._wikitext)
        return {
            "ok": True,
            "message": f"已提交「{self.page_name}」；模板里列了 {len(entries)} 个条目",
            "url": wiki_api.article_url(self.page_name),
            "newrevid": result.get("newrevid"),
            "backlinks": entries,
            "backlinkTitle": BACKLINK_TITLE,
            "backlinkHeader": f"把 {{{{{self.template_call()}}}}} 加进这些条目"
                              "（导航框在条目里默认折叠；插在各条目「注释」小节的"
                              "<references/> 后面，注释上方的大家族模板会一并挪到小节里）",
            "backlinkAction": BACKLINK_ACTION,
            "backlinkSkipNote": BACKLINK_SKIP_NOTE,
        }

    def _template_name(self) -> str:
        return bare_template_name(self.page_name)

    def template_call(self) -> str:
        """写进条目的那一串（双层花括号里的内容）：`雄之助|collapsed`。

        用户 2026-09-30：「往条目里加入的P主模板也要加上 `|collapsed` 参数」——
        导航框很长，在条目里默认折叠（模板里写的是
        `|state ={{#ifeq:{{{1}}}|collapsed|mw-collapsible mw-collapsed|mw-uncollapsed}}`）。
        """
        return f"{self._template_name()}|{COLLAPSED_PARAM}"

    def plan_entries(self, text: str) -> List[dict]:
        """模板链接到的条目：P主条目在最前，其后是 `{{links|…}}` 列出的曲目。

        每行 `{'title', 'count', 'kind'}`：页面已建 → `count=1`（可写入）；
        还没建 → `count=0`（界面上是灰的，跳过），`kind` 说明原因。
        **看着不像歌曲条目的页面**（正文里没有 `{{…Songbox}}`，比如榜单页
        `NICONICO VOCALOID SONGS TOP20/第87期`、专辑页、P主页面）也灰掉，
        免得把导航模板插到不是歌曲的页面上（用户 2026-10 要求）。
        """
        titles: List[str] = []
        page = str(self.work.page_name or self.work.artist.name or "").strip()
        if page:
            titles.append(page)
        for title in producer_template.template_links(text):
            if title not in titles:
                titles.append(title)
        texts = wiki_api.fetch_pages_text(titles) if titles else {}
        name = self._template_name()
        entries: List[dict] = []
        for title in titles:
            body = texts.get(title)
            if body is None:
                entries.append({"title": title, "count": 0, "kind": "条目还没建",
                                "note": "条目还没建"})
            elif producer_template.contains_template(body, name):
                entries.append({"title": title, "count": 0, "kind": "已有本模板",
                                "note": "已有本模板"})
            elif title != page and not producer_template.looks_like_song_page(body):
                entries.append({"title": title, "count": 0, "kind": "不是歌曲条目",
                                "note": "看着不像歌曲条目（没有信息框）"})
            else:
                entries.append({"title": title, "count": 1, "kind": "加入本模板",
                                "note": "加入本模板"})
        return entries

    def fix_backlinks(self, titles_json: str, progress=None) -> dict:
        """把模板写进选中的条目（`progress` 逐页回调，提交页用它一条一条冒提示）。"""
        try:
            titles = json.loads(titles_json or "[]")
        except ValueError:
            return {"ok": False, "error": "参数不是合法 JSON"}
        titles = [str(title).strip() for title in titles if str(title).strip()]
        if not titles:
            return {"ok": False, "error": "没有选中任何条目"}
        results = producer_template.insert_into_pages(self._template_name(), titles,
                                                      progress=progress,
                                                      call=self.template_call())
        changed = sum(1 for item in results if item.get("ok") and item.get("count"))
        skipped = [item for item in results if not (item.get("ok") and item.get("count"))]
        message = f"已把模板写进 {changed} 个条目"
        if skipped:
            message += f"；{len(skipped)} 个未改动（" + "、".join(
                f"{item['title']}：{item.get('error') or item.get('kind') or '未改动'}"
                for item in skipped) + "）"
        return {"ok": True, "message": message, "results": results}

    def _write_local(self, text: str) -> bool:
        try:
            self._source_path.parent.mkdir(parents=True, exist_ok=True)
            self._source_path.write_text(text or "", encoding="utf-8")
            return True
        except OSError as e:
            logging.error("无法写回模板文件 %s：%s", self._source_path, e)
            return False


# ============================================================ 流程

def open_works_editor(work: ProducerWork) -> Optional[ProducerWork]:
    """打开「曲目」页；用户取消返回 None。"""
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，跳过曲目页。")
        return None
    return ui.open_producer_works(work)


def open_style_editor(work: ProducerWork) -> Optional[Dict[str, str]]:
    """打开「样式」页；用户取消返回 None。"""
    from utils import ui
    if not ui.is_active():
        logging.warning("图形界面没启动，跳过样式页。")
        return None
    return ui.open_producer_style(work)


def generate_producer_template() -> Optional[Path]:
    """侧栏第二个功能的完整流程；返回写出的模板文件（用户中途放弃返回 None）。

    终端模式（`--console --producer`）没有界面：跳过曲目页 / 样式页 / 提交页，
    直接把按默认样式生成的模板写到输出目录并打开那个文件夹。
    """
    from utils import ui
    artist = choose_artist("")
    if artist is None:
        ui.status("已取消生成 P主模板")
        logging.info("已取消生成 P主模板")
        return None
    ui.status(f"正在从 VocaDB 取「{artist.name}」的曲目与专辑…")
    work = prepare_work(artist)
    producer_template.resolve_from_wiki(work, work.page_name)
    named = sum(1 for song in work.songs if song.cn)
    logging.info("已用维基上的 P主条目补出 %d 个中文条目名（共 %d 首）", named, len(work.songs))

    if ui.is_active():
        ui.status("曲目页已打开：可以增删改曲目 / 专辑，改完点「保存并继续」")
        if open_works_editor(work) is None:
            ui.status("已取消生成 P主模板")
            return None
        ui.status("样式页已打开：调标题栏 / 分组栏 / 列表的配色，改完点「保存并继续」")
        styles = open_style_editor(work)
        if styles is not None:
            work.styles = {**work.styles, **styles}

    text = producer_template.build_template(work)
    path = output_path(work.template_name or work.artist.name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        logging.error("无法写出模板文件 %s：%s", path, e)
        raise
    logging.info("P主模板已写入 %s（%d 个条目链接）", path,
                 len(producer_template.template_links(text)))

    if not ui.is_active():
        print(f"P主模板已写入：{path}")
        ui.open_folder(path)
        return path
    api = ProducerTemplateApi(work, path, text)
    if ui.open_template_submit(api):
        return path
    ui.open_folder(path)
    return path
