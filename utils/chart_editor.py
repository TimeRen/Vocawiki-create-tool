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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from config.config import get_config, get_output_path
from utils import chart_cache, chart_nico, chart_recognize, chart_video, chart_wikitext, user_dict
from utils.helpers import prompt_choices, prompt_response
from utils.string import is_empty, safe_filename

# 「其他参数」一格里的分隔符：`翻唱 = 1; 评论权重 = 0.97`
EXTRA_SEPARATOR_RE = re.compile(r"[;；\n]+")
EXTRA_PAIR_RE = re.compile(r"^\s*([^=＝]+?)\s*[=＝]\s*(.*?)\s*$")
# 歌姬名的分隔符（`重音Teto、初音未来`）
NAME_SEPARATOR_RE = re.compile(r"[、,，/／]+")


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
    cover: str = ""           # 翻唱（1 = 是；按 n 站标题 / 简介自动判断，可改）
    last: str = ""            # 上周
    trend: str = ""           # 走势
    rate: str = ""            # rate（前周比）
    section: str = ""         # 小节：空＝主榜，`番外`
    badge: str = ""           # 角标：PICKUP / TOPIC / 上周冠军
    other: Dict[str, str] = field(default_factory=dict)   # 其他参数（评论权重 …）
    note: str = ""            # 备注（补全情况，只读）
    raw_title: str = ""       # 模型抄的原文曲名（去掉【…】之前）
    raw_vocalist: str = ""    # 模型抄的原文歌姬（归一化之前，用来学进用户词典）
    auto_entry: str = ""      # 程序自动填的「条目」（用户手改过才会与 entry 不同）

    COLUMNS = {
        "rank": "本周", "title": "曲名", "vocalist": "歌姬", "producer": "P主",
        "video_id": "id", "time": "时间", "score": "得点", "view": "再生",
        "comment": "评论", "mylist": "收藏", "like": "点赞", "entry": "条目",
        "cover": "翻唱", "last": "上周", "trend": "走势", "rate": "rate",
        "section": "小节", "badge": "角标",
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
        # 曲名去掉 `【…】`（那是歌姬，不是曲名的一部分），原文留着给「歌姬」兜底
        row.raw_title = row.title
        row.title = chart_nico.clean_song_title(row.title) or row.title
        row.raw_vocalist = row.vocalist
        if not row.vocalist:
            row.vocalist = chart_nico.vocalists_from_title(row.raw_title)
        if row.vocalist:
            row.vocalist = chart_nico.normalize_vocalists(row.vocalist)
        row.section = chart_recognize.normalize_section(row.section)
        row.badge = chart_recognize.normalize_badge(row.badge)
        row.entry = user_dict.lookup_title(row.title)
        row.auto_entry = row.entry
        return row


    def is_empty(self) -> bool:
        """曲名 / 稿件编号 / 得点 都没有：空行（`render` 会跳过，只填了名次也算空）。"""
        return all(is_empty(value) for value in (self.title, self.video_id, self.score))

    def to_params(self) -> Dict[str, str]:
        """→ `{{VOCALOID_&_UTAU_Ranking/bricks}}` 的参数（空值不写）。"""
        params: Dict[str, str] = dict(self.other)
        # `翻唱` 有独立一列；旧数据 / 手写在「其他参数」里的 `翻唱 = 1` 照样认
        cover = str(self.cover or self.other.get("翻唱") or "").strip()
        known = {
            "前缀": chart_wikitext.id_prefix(self.video_id),
            "id": chart_wikitext.plain_id(self.video_id),
            "曲名": self.title,
            "条目": self.entry,
            "翻唱": cover,
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
        # 角标（PICKUP / TOPIC / 上周冠军）自动补 `|color` 与 `|bottom-column`；
        # 用户在「其他参数」里手填的 `color` 优先，不会被覆盖。
        params.update(chart_wikitext.badge_params(self.badge, params))
        return params

    def to_dict(self) -> Dict[str, object]:
        """→ 可写进 JSON 缓存的普通字典。"""
        return {item.name: (dict(self.other) if item.name == "other" else getattr(self, item.name))
                for item in fields(self)}

    @classmethod
    def from_dict(cls, data: object) -> "ChartRow":
        """JSON 缓存里的字典 → `ChartRow`（缺的字段用默认值）。"""
        row = cls()
        for item in fields(cls):
            if not isinstance(data, dict) or item.name not in data:
                continue
            value = data[item.name]
            if item.name == "other":
                row.other = {str(key): str(text) for key, text in dict(value or {}).items()
                             if str(text).strip()}
            else:
                setattr(row, item.name, "" if value is None else str(value))
        return row


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
    from_cache: bool = False   # 是不是直接沿用上次的识别结果

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
        if self.from_cache:
            parts.append("沿用上次识别结果")
        missing = sum(1 for row in self.rows if not row.video_id)
        if missing:
            parts.append(f"{missing}行没找到稿件")
        extra = sum(1 for row in self.rows if str(row.section or "").strip() == "番外")
        if extra:
            parts.append(f"{extra}行在番外榜")
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

def ask_reuse(spec: chart_nico.ChartSpec, index: int, payload: dict) -> bool:
    """有缓存时问一句：沿用上次结果（True）还是重新下载并识别（False）。

    `prompt_choices` 自己会按「图形界面 / 终端」分开问（界面上是按钮，终端里是编号菜单），
    两种都是「1 = 沿用」。
    """
    prompt = (f"{chart_cache.reuse_hint(spec, index, payload)}。"
              "沿用上次结果，还是重新下载并识别？")
    return prompt_choices(prompt, ["沿用上次结果", "重新下载并识别"]) == 1


def work_from_cache(spec: chart_nico.ChartSpec, index: int, payload: dict) -> ChartWork:
    """缓存 → `ChartWork`（一次网络请求都不发）。"""
    work = ChartWork(spec=spec, index=index, from_cache=True)
    for name in chart_cache.WORK_FIELDS:
        value = (payload or {}).get(name)
        if value not in (None, ""):
            setattr(work, name, value)
    work.rows = [ChartRow.from_dict(item) for item in (payload or {}).get("rows") or []]
    work.frames = int(payload.get("frames") or 0)
    work.source = str(payload.get("source") or "上次识别结果")
    return work


# 底部进度条的分段（百分比）：找视频 → 下载 → 抽帧 → 识别 → 补全 → 收尾。
# 各段之和是 100，且只在段内单调递增（调用方会取 max，绝不往回跳）。
PROGRESS_RANGES = {
    "find": (0, 5),
    "download": (5, 45),
    "extract": (45, 55),
    "recognize": (55, 85),
    "enrich": (85, 96),
    "finish": (96, 100),
}


class ProgressReporter:
    """把各阶段的小进度换算成 0~100 的百分比，拼在「正在…」后面。

    用户 2026-11-16 的要求：选完期数后，最下面那行「正在…」后面要有百分比。
    这里的百分比**只会往上走**（同一阶段内取历史最大值），所以重试、回退都不会让数字倒退。
    """

    def __init__(self, notify: Callable[[str], None]) -> None:
        self._notify = notify
        self._floor = 0

    def at(self, stage: str, text: str, fraction: float = 0.0) -> None:
        """报一句带百分比的状态：`notify("正在下载榜单视频…（32%）")`。"""
        low, high = PROGRESS_RANGES.get(stage, (0, 100))
        try:
            ratio = max(0.0, min(1.0, float(fraction or 0)))
        except (TypeError, ValueError):
            ratio = 0.0
        percent = int(round(low + (high - low) * ratio))
        percent = max(self._floor, min(high, percent))
        self._floor = percent
        self._notify(f"{text}（{percent}%）")

    def done(self, text: str) -> None:
        """整段跑完：直接顶到 100%。"""
        self.at("finish", text, 1.0)


def collect_work(spec: chart_nico.ChartSpec, index: int, progress=None,
                 reuse=None) -> Optional[ChartWork]:
    """找视频 → 抽帧 → 识别 → 补齐每条数据。识别不出名次也照样返回（表格里手填）。

    `reuse` 是「有缓存时问一句」的回调（签名 `(spec, index, payload) -> bool`，
    测试里可以直接传假的）；`progress` 是状态栏文字的回调。
    每个耗时步骤之间都调一次 `ui.check_run_cancelled()`：用户点了「清除对话记录」/
    切了功能就立刻抛 `RunCancelled`，不会在后台偷偷下载完 53 MB 再识别 28 批。
    """
    from utils import ui

    notify = progress or ui.status
    reporter = ProgressReporter(notify)
    cached = chart_cache.load(spec, index)
    if chart_cache.is_usable(cached):
        chooser = reuse or ask_reuse
        if chooser(spec, index, cached):
            work = work_from_cache(spec, index, cached)
            logging.info("沿用上次识别结果：第%s期《%s》%d行", index, spec.label, len(work.rows))
            notify(f"已沿用第{index}期上次的识别结果（{len(work.rows)}行）")
            return work
    ui.check_run_cancelled()
    reporter.at("find", f"正在找第{index}期《{spec.label}》的榜单视频…")
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
    ui.check_run_cancelled()
    cfg = get_config().chart
    reporter.at("download", "正在下载榜单视频…")

    def report_video(stage: str, fraction: float) -> None:
        if stage == "download":
            reporter.at("download", "正在下载榜单视频…", fraction)
        else:
            reporter.at("extract", "正在抽取榜单画面…", fraction)

    frames = chart_video.video_frames(work.video_id, cfg.frame_interval, cfg.max_frames,
                                      cancel=ui.check_run_cancelled, progress=report_video)
    if not frames:
        ui.check_run_cancelled()
        reporter.at("extract", "下载 / 抽帧没成功，改用浏览器截图…")
        frames = capture_in_browser(work.video_id)
    work.frames = len(frames)
    if frames:
        ui.check_run_cancelled()
        reporter.at("recognize", f"正在识别{len(frames)}帧榜单画面…")

        def report(done: int, total: int) -> None:
            reporter.at("recognize", f"正在识别榜单画面：第{min(done + 1, total)}/{total}批…",
                        done / total if total else 0.0)

        recognized = chart_recognize.recognize(frames, spec, index, batch=cfg.ai_batch,
                                              extra=cfg.ai_prompt, progress=report,
                                              main_limit=int(cfg.main_limit or 0))
        chart_recognize.derive_weights(recognized)
        work.rows = [ChartRow.from_recognized(row) for row in recognized]
        report_missing(work.rows)
    else:
        logging.warning("一帧都没取到，榜单表格留空，请在「榜单」页里手填")
    ui.check_run_cancelled()

    def report_enrich(done: int, total: int) -> None:
        reporter.at("enrich", f"正在补全稿件编号：第{done}/{total}行…",
                    done / total if total else 0.0)

    enrich_rows(work.rows, progress=report_enrich, window=search_window(work))
    if cfg.bilibili_search:
        ui.check_run_cancelled()
        reporter.at("finish", "正在搜这一期的 bilibili 转载…")
        work.bilibili = find_bilibili(spec, index,
                                      previous=str((cached or {}).get("bilibili") or ""))
    chart_cache.save(spec, index, work)
    reporter.done(f"第{index}期识别完成")
    return work


def report_missing(rows: Sequence[ChartRow]) -> None:
    """识别完把「缺了哪些名次」列出来（不改数据），由调用方写进日志 / 状态栏。"""
    payload = [{"本周": row.rank, "小节": row.section} for row in rows]
    gaps = chart_recognize.missing_ranks(payload, limit=main_limit())
    if not gaps:
        return
    parts = []
    for section, lacking in gaps.items():
        label = chart_recognize.NAME_OF_SECTION.get(section, section or "主榜")
        parts.append(f"{label}缺第{'、'.join(str(rank) for rank in lacking)}名")
    logging.warning("%s（可点「添加行」补）", "；".join(parts))


def main_limit() -> int:
    """配置里的「主榜只收录前几名」（读不到就当 20）。"""
    try:
        return int(get_config().chart.main_limit or 0)
    except Exception:                                  # noqa: BLE001 - 配置缺项不该崩
        return 20


def find_bilibili(spec: chart_nico.ChartSpec, index: int, previous: str = "") -> str:
    """找这一期的 B站转载：页面已有 → 搜 → 问一次 → 沿用上次填过的。

    `previous` 是缓存里存着的 BV 号：重新识别时保留它（用户上次手填的答案不该丢）。
    """
    def ask() -> str:
        # `prompt_response` 自己按「图形界面 / 终端」分开问，不用在这里分支
        return prompt_response(f"没搜到第{index}期的B站转载，贴BV号或链接（留空跳过）")
    # 上次已经填过就直接沿用，不再问一遍（用户手填的答案不该每次都重填）
    found = chart_nico.find_bilibili(spec, index, ask=None if previous else ask)
    if found:
        return found
    if previous:
        logging.info("这一期的B站转载沿用上次填的%s", previous)
        return previous
    logging.info("第%s期没有B站转载（可在「榜单」页的「B站转载」栏直接填BV号）", index)
    return ""


def capture_in_browser(video_id: str, count: int = 24) -> List[tuple]:
    """没有 yt-dlp / ffmpeg 时的退路：借浏览器拖进度条截图（见 utils/browser_fetch.py）。"""
    from utils import browser_fetch

    url = f"https://www.nicovideo.jp/watch/{video_id}"
    fractions = [index / float(count) for index in range(1, count + 1)]
    return list(browser_fetch.capture_frames(url, fractions))


def fix_row_names(rows: Sequence[ChartRow]) -> int:
    """纠正「P主」一列里被填成歌姬名 / 抄成曲名的情况（截图 5：110 / 125 / 139）。

    模型经常把卡片的歌手名抄进「P主」；核对方式是**只认名字**：只要这个值能对上
    已知歌姬（`chart_nico.vocalist_lookup()`）或者就等于本行的歌姬，就把它挪到「歌姬」、
    把「P主」清空，让后面的 `fill_row()` 用上传者名重新填。返回纠正了几行。
    """
    table = chart_nico.vocalist_lookup() or {}
    known = {str(name).strip() for name in table.values() if str(name).strip()}
    known.update(str(name).strip() for name in table if str(name).strip())
    fixed = 0
    for row in rows or []:
        producer = str(row.producer or "").strip()
        if not producer:
            continue
        normalized = chart_nico.normalize_vocalists(producer) or producer
        title = str(row.title or "").strip()
        if title and (producer == title or normalized == title):
            row.producer = ""
            fixed += 1
            continue
        vocalist = str(row.vocalist or "").strip()
        # 归一化没改动原值时不能算「认出来了」，否则任何 P主 都会被当成歌姬挪走
        is_vocalist = (producer in known
                       or (normalized != producer and normalized in known)
                       or producer == vocalist)
        if not is_vocalist:
            continue
        extra = normalized if normalized not in vocalist else ""
        if extra:
            row.vocalist = f"{vocalist}、{extra}" if vocalist else extra
        row.producer = ""
        fixed += 1
    if fixed:
        logging.info("榜单：%d行的「P主」其实是歌姬名，已挪到「歌姬」", fixed)
    return fixed


def search_window(work: "ChartWork"):
    """这一期的投稿时间窗（`集計対象動画期間`），补全稿件时用它过滤候选。"""
    try:
        return chart_nico.parse_target_period(getattr(work, "description", "") or "")
    except Exception:                                  # noqa: BLE001 - 认不出就当没有窗口
        return (None, None)


def enrich_rows(rows: Sequence[ChartRow], search: bool = True, progress=None,
                window=(None, None)) -> None:
    """给每行补 `id` / `时间` / `P主` / `歌姬`（已经有值的保留），补不到的在备注里说明。

    `progress(已完成, 总数)` 逐行报进度，界面上能看出「还在跑第几行」；
    整个补全只写**一条**汇总日志（以前每行一行，日志页刷得看不清）。
    `window` 是这一期的投稿时间窗：搜索会带 `filters[startTime]`，短关键词也能用。
    每行之间会检查一次「这一轮是不是被放弃了」—— 40 多行 × 每行几次网络请求，
    不管的话用户点了取消还得等好几分钟。
    """
    total = len(list(rows))
    fix_row_names(rows)
    found = missing = ambiguous = 0
    for position, row in enumerate(rows, start=1):
        chart_nico.check_cancelled()
        if progress and (position == 1 or position == total or position % 5 == 0):
            progress(position, total)
        data = chart_nico.fill_row({"id": row.video_id, "曲名": row.title, "时间": row.time,
                                    "P主": row.producer, "歌姬": row.vocalist,
                                    "翻唱": row.cover},
                                   search=search, window=window)
        row.video_id = str(data.get("id") or row.video_id or "").strip()
        row.time = row.time or str(data.get("时间") or "")
        row.producer = row.producer or str(data.get("P主") or "")
        row.vocalist = row.vocalist or str(data.get("歌姬") or "")
        row.cover = row.cover or str(data.get("翻唱") or "")
        if data.get("ambiguous"):
            row.note = "同名稿多个，请选一个填进id"
            ambiguous += 1
        elif not row.video_id:
            row.note = "没找到稿件（曲名可能有乱码，建议手填id）"
            missing += 1
        elif not row.time:
            row.note = "没有投稿时间"
        elif data.get("loose"):
            row.note = "自动匹配（只按时间窗+相似度），请核对稿件"
        else:
            found += 1
    if total:
        extra = []
        if missing:
            extra.append(f"{missing}行没找到稿件")
        if ambiguous:
            extra.append(f"{ambiguous}行同名待确认")
        logging.info("榜单补全：%d/%d行拿到稿件编号%s", found, total,
                     f"（{'，'.join(extra)}）" if extra else "")



# ---------------------------------------------------------------- 生成

def remember_user_names(work: ChartWork) -> None:
    """把用户手填过的「曲名→条目」「日文歌姬→中文歌姬」记进 `user_names.yaml`。

    只记**用户真改过的**：跟程序自动算出来的 `auto_entry` / `raw_vocalist` 比，
    不一样才记 —— 这样文件里永远只有人的判断，程序升级也不会写脏。
    """
    titles = vocalists = 0
    for row in work.rows or []:
        title = str(row.title or "").strip()
        entry = str(row.entry or "").strip()
        if title and entry and entry != title and entry != str(row.auto_entry or "").strip():
            if user_dict.remember_title(title, entry):
                titles += 1
        raw = str(row.raw_vocalist or "").strip()
        final = str(row.vocalist or "").strip()
        if raw and final and raw != final and not NAME_SEPARATOR_RE.search(raw):
            if user_dict.remember_vocalist(raw, final):
                vocalists += 1
    if titles or vocalists:
        logging.info("已把手填的名字记进%s（曲名%d条、歌姬%d条）",
                     user_dict.path().name, titles, vocalists)


def write_page(work: ChartWork) -> Optional[Path]:
    """把榜单条目写到输出目录 → 文件路径（失败返回 None）。"""
    remember_user_names(work)
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
           "open_chart_editor", "parse_extra", "write_page", "ask_chart", "ask_index",
           "ask_reuse", "work_from_cache", "fix_row_names", "remember_user_names",
           "find_bilibili", "report_missing", "main_limit", "search_window",
           "ProgressReporter", "PROGRESS_RANGES"]
