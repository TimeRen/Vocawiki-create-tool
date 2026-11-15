"""侧栏「刊」：生成榜单条目的完整流程。

用户 2026-11-15 要求：点「刊」→ 问生成哪种榜单（目前只有 U 榜）→ 问哪一期 →
下载该期的 niconico 周刊视频、从画面里识别出每个名次的数据 → 打开「榜单」页校对 →
生成条目并转到提交页。

    ask_chart() → ask_index() → collect_work() → open_chart_editor() → 写文件 → 提交页

数据来源分三层（都没有就留空，让用户在表格里手填）：

1. **画面**（`utils/chart_recognize.py`）：名次 / 曲名 / P名 / 再生 / コメント / マイリスト
   —— 这些是**当周统计值**，只能从视频里读；
2. **`getthumbinfo`**（`utils/chart_nico.py`）：稿件编号（按曲名搜）、投稿时间、歌姬（tag）；
3. **人工**：表格里能改任何一格，`其他参数` 一格用来补 `翻唱 = 1` 这类站上才有的参数。
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from config.config import get_config, get_output_path
from utils import chart_nico, chart_recognize, chart_video, chart_wikitext
from utils.helpers import prompt_choices, prompt_response
from utils.string import is_empty, safe_filename

# 「其他参数」一格里的分隔符：`翻唱 = 1; 评论权重 = 0.97`
EXTRA_SEPARATOR_RE = re.compile(r"[;；\n]+")
EXTRA_PAIR_RE = re.compile(r"^\s*([^=＝]+?)\s*[=＝]\s*(.*?)\s*$")


@dataclass
class ChartRow:
    """榜单表格的一行（＝一个名次）。字段名就是「榜单」页上的列名。"""
    rank: str = ""            # 本周
    title: str = ""           # 曲名
    vocalist: str = ""        # 歌姬
    producer: str = ""        # P主
    video_id: str = ""        # 稿件编号（sm…）
    time: str = ""            # 投稿时间（JST，站上就是这么写的）
    score: str = ""           # 得点
    view: str = ""            # 再生
    comment: str = ""         # 评论
    mylist: str = ""          # 收藏（マイリスト）
    like: str = ""            # 点赞（いいね，2023 之后的新版视频才有）
    entry: str = ""           # 条目（站内条目名，与曲名不同时才填）
    last: str = ""            # 上周
    trend: str = ""           # 走势
    rate: str = ""            # rate（前周比）
    other: Dict[str, str] = field(default_factory=dict)   # 其他参数（翻唱 / 评论权重 …）
    note: str = ""            # 备注（补全情况，只读）

    COLUMNS = {
        "rank": "本周", "title": "曲名", "vocalist": "歌姬", "producer": "P主",
        "video_id": "id", "time": "时间", "score": "得点", "view": "再生",
        "comment": "评论", "mylist": "收藏", "like": "点赞", "entry": "条目",
        "last": "上周", "trend": "走势", "rate": "rate",
    }

    # ---------------------------------------------------------- 识别结果 → 行
    @classmethod
    def from_recognized(cls, raw: Dict[str, object]) -> "ChartRow":
        """`chart_recognize` 的一行（键是站上的参数名）→ `ChartRow`。"""
        row = cls()
        reverse = {label: name for name, label in cls.COLUMNS.items()}
        for key, value in (raw or {}).items():
            name = reverse.get(str(key))
            text = "" if value is None else str(value).strip()
            if name:
                setattr(row, name, text)
            elif text:
                row.other[str(key)] = text
        return row

    def is_empty(self) -> bool:
        """曲名 / 稿件编号 / 得点 都没有：空行（`render` 会跳过，只填了名次也算空）。"""
        return all(is_empty(value) for value in (self.title, self.video_id, self.score))

    def to_params(self) -> Dict[str, str]:
        """→ `{{VOCALOID_&_UTAU_Ranking/bricks}}` 的参数（空值不写）。"""
        params: Dict[str, str] = dict(self.other)
        known = {
            "前缀": chart_wikitext.id_prefix(self.video_id),
            "id": chart_wikitext.plain_id(self.video_id),
            "曲名": self.title,
            "条目": self.entry,
            "歌姬": self.vocalist,
            "P主": self.producer,
            "时间": self.time,
            "本周": self.rank,
            "上周": self.last,
            "走势": self.trend,
            "rate": self.rate,
            "得点": self.score,
            "再生": self.view,
            "评论": self.comment,
            "收藏": self.mylist,
            "点赞": self.like,
        }
        for name, value in known.items():
            text = str(value or "").strip()
            if text:
                params[name] = text
            else:
                params.pop(name, None)
        return params

    def other_text(self) -> str:
        """`其他参数` 一格的文本（`翻唱 = 1; 评论权重 = 0.97`）。"""
        return "; ".join(f"{name} = {value}" for name, value in self.other.items()
                         if str(value).strip())

    def set_other_text(self, text: str) -> None:
        self.other = parse_extra(text)

    def key(self) -> str:
        return chart_wikitext.plain_id(self.video_id)


def parse_extra(text: str) -> Dict[str, str]:
    """`翻唱 = 1; 评论权重 = 0.97` → 字典（认不出的碎片忽略）。"""
    params: Dict[str, str] = {}
    for chunk in EXTRA_SEPARATOR_RE.split(str(text or "")):
        match = EXTRA_PAIR_RE.match(chunk or "")
        if match:
            params[match.group(1).strip()] = match.group(2).strip()
    return params


@dataclass
class ChartWork:
    """一次生成的全部数据：「榜单」页与 wikitext 渲染都读它。"""
    spec: chart_nico.ChartSpec
    index: int
    video_id: str = ""
    uploader: str = ""
    publish: str = ""          # 发布时间（UTC+8）
    start: str = ""            # 起始时间（集計期間）
    end: str = ""
    rule: str = ""             # 统计规则（2008 / 2023）
    description: str = ""      # 视频简介（已经按站上的分行习惯拆好）
    bilibili: str = ""         # 转载的 BV 号
    notes: str = ""            # ==杂谈==
    rows: List[ChartRow] = field(default_factory=list)
    frames: int = 0            # 识别时用了几帧
    source: str = ""           # 榜单视频是怎么找到的

    @property
    def title(self) -> str:
        return self.spec.page_title(self.index)

    def summary(self) -> str:
        """标题栏右边那行小字。"""
        parts = [f"{len(self.rows)}个名次"]
        if self.frames:
            parts.append(f"识别{self.frames}帧")
        if self.source:
            parts.append(f"视频来自{self.source}")
        missing = sum(1 for row in self.rows if not row.video_id)
        if missing:
            parts.append(f"{missing}行没找到稿件")
        return " · ".join(parts)


# ---------------------------------------------------------------- 提问

def ask_chart() -> Optional[chart_nico.ChartSpec]:
    """问要生成哪种榜单（目前只有 U 榜，以后往 `CHARTS` 里加就行）。"""
    specs = list(chart_nico.CHARTS.values())
    if len(specs) == 1:
        return specs[0]
    labels = [f"{spec.label}（{spec.page_prefix}）" for spec in specs]
    index = prompt_choices("要生成哪种榜单？", labels)
    return specs[index - 1] if 1 <= index <= len(specs) else None


def ask_index(spec: chart_nico.ChartSpec) -> int:
    """问要生成哪一期（只填数字）。"""
    while True:
        answer = prompt_response(f"要生成《{spec.label}》的哪一期？（填期数，例如52）")
        digits = re.sub(r"\D", "", str(answer or ""))
        if digits:
            return int(digits)
        logging.warning("期数要填数字（收到「%s」）", answer)


def ask_video_id(spec: chart_nico.ChartSpec, index: int) -> str:
    """站上没写、站内也搜不到时，让用户贴榜单视频链接。"""
    answer = prompt_response(
        f"没找到第{index}期《{spec.label}》的榜单视频，请贴niconico链接（sm…）；"
        "留空放弃这次生成")
    return chart_nico.normalize_id(answer)


# ---------------------------------------------------------------- 数据收集

def collect_work(spec: chart_nico.ChartSpec, index: int,
                 progress=None) -> Optional[ChartWork]:
    """找视频 → 抽帧 → 识别 → 补齐每条数据。识别不出名次也照样返回（表格里手填）。"""
    from utils import ui

    notify = progress or ui.status
    cfg = get_config().chart
    notify(f"正在找第{index}期《{spec.label}》的榜单视频…")
    video_id, source = chart_nico.find_ranking_video(spec, index)
    if not video_id:
        video_id = ask_video_id(spec, index)
        source = "手动填写"
    if not video_id:
        logging.info("没有榜单视频，已取消生成榜单条目")
        return None
    info = chart_nico.thumbinfo(video_id)
    if info is None:
        logging.error("取不到%s的niconico元数据（可能已削除或非公开）", video_id)
        ui.status(f"取不到{video_id}的元数据（视频可能已削除），换个编号或放弃")
        return None
    work = ChartWork(spec=spec, index=index, video_id=str(info.get("id") or video_id),
                     uploader=str(info.get("uploader") or ""),
                     publish=chart_nico.format_publish(info["date_jst"]),
                     rule=spec.default_rule,
                     description=chart_nico.split_description(info.get("description") or ""),
                     source=source)
    work.start, work.end = chart_nico.parse_period(info.get("description") or "")
    found_index = chart_nico.parse_index(info.get("title") or "")
    if found_index and found_index != index:
        logging.warning("榜单视频「%s」标的是第%s期，与填写的第%s期不一致",
                        info.get("title"), found_index, index)
        work.notes = (f"# 视频标题标的是 #{found_index}，请核对期数。\n")
    notify(f"正在下载并抽取第{index}期的榜单画面（{cfg.frame_interval:g}秒一帧）…")
    frames = chart_video.video_frames(work.video_id, cfg.frame_interval, cfg.max_frames)
    if not frames:
        notify("下载 / 抽帧没成功，改用浏览器截图…")
        frames = capture_in_browser(work.video_id)
    work.frames = len(frames)
    if frames:
        notify(f"正在识别{len(frames)}帧画面（每批{cfg.ai_batch}张）…")

        def report(done: int, total: int) -> None:
            notify(f"正在识别榜单画面：第{done + 1}/{total}批…")

        recognized = chart_recognize.recognize(frames, spec, index, batch=cfg.ai_batch,
                                               extra=cfg.ai_prompt, progress=report)
        chart_recognize.derive_weights(recognized)
        work.rows = [ChartRow.from_recognized(row) for row in recognized]
    else:
        logging.warning("一帧都没取到，榜单表格留空，请在「榜单」页里手填")
    enrich_rows(work.rows)
    if cfg.bilibili_search:
        notify("正在搜这一期的 bilibili 转载…")
        work.bilibili = chart_nico.find_bilibili(spec, index)
    return work


def capture_in_browser(video_id: str, count: int = 24) -> List[tuple]:
    """没有 yt-dlp / ffmpeg 时的退路：借浏览器拖进度条截图（见 utils/browser_fetch.py）。"""
    from utils import browser_fetch

    url = f"https://www.nicovideo.jp/watch/{video_id}"
    fractions = [index / float(count) for index in range(1, count + 1)]
    return list(browser_fetch.capture_frames(url, fractions))


def enrich_rows(rows: Sequence[ChartRow], search: bool = True) -> None:
    """给每行补 `id` / `时间` / `歌姬`（已经有值的保留），补不到的在备注里说明。"""
    for row in rows:
        data = chart_nico.fill_row({"id": row.video_id, "曲名": row.title, "时间": row.time,
                                    "P主": row.producer, "歌姬": row.vocalist},
                                   search=search)
        row.video_id = str(data.get("id") or row.video_id or "").strip()
        row.time = row.time or str(data.get("时间") or "")
        row.producer = row.producer or str(data.get("P主") or "")
        row.vocalist = row.vocalist or str(data.get("歌姬") or "")
        if not row.video_id:
            row.note = "没找到niconico稿件"
        elif not row.time:
            row.note = "没有投稿时间"


# ---------------------------------------------------------------- 生成

def write_page(work: ChartWork) -> Optional[Path]:
    """把榜单条目写到输出目录 → 文件路径（失败返回 None）。"""
    content = chart_wikitext.render(work)
    if is_empty(str(content).strip()):
        logging.error("榜单条目渲染出来是空的")
        return None
    folder = get_output_path()
    path = Path(folder).joinpath(f"{safe_filename(work.title)}.wikitext")
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        logging.error("写榜单条目失败：%s", e)
        return None
    logging.info("榜单条目已写出：%s（%d行）", path, len(work.rows))
    return path


def open_chart_editor(work: ChartWork) -> Optional[ChartWork]:
    """打开「榜单」页；取消返回 None。图形界面不可用时原样返回（终端模式）。"""
    from utils import ui

    if not ui.is_active():
        logging.warning("图形界面没启动，跳过榜单页。")
        return work
    return ui.open_chart_editor(work)


def generate_chart_entry() -> Optional[Path]:
    """侧栏「刊」的完整流程；返回写出的文件（中途放弃返回 None）。"""
    from utils import ui

    spec = ask_chart()
    if spec is None:
        ui.status("已取消生成榜单条目")
        return None
    index = ask_index(spec)
    work = collect_work(spec, index)
    if work is None:
        ui.status("已取消生成榜单条目")
        return None
    if ui.is_active():
        ui.status("「榜单」页已打开：核对名次 / 曲名 / 得点，改完点「生成条目」")
        edited = open_chart_editor(work)
        if edited is None:
            ui.status("已取消生成榜单条目")
            return None
        work = edited
    path = write_page(work)
    if path is None:
        ui.status("生成榜单条目失败（详见日志）")
        return None
    if ui.is_active() and _open_submit(work, path):
        return path
    ui.open_folder(path)
    return path


def _open_submit(work: ChartWork, path: Path) -> bool:
    """点亮提交页（实时预览 / 提交）；用不了就回退到「打开输出文件夹」。"""
    from utils.submit_editor import open_submit_editor

    return open_submit_editor(page_name=work.title, wikitext=path.read_text(encoding="utf-8"),
                              source_path=path)


__all__ = ["ChartRow", "ChartWork", "collect_work", "enrich_rows", "generate_chart_entry",
           "open_chart_editor", "parse_extra", "write_page", "ask_chart", "ask_index"]
