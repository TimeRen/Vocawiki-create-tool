"""AI 歌词识别：用大模型把混在一起的歌词拆成日语 / 中文 / 罗马音三栏。

歌词整理窗口里「自动识别并填入」是纯规则实现（见 utils/lyrics_editor.py），
遇到奇怪的排版会认不准；这个模块提供一条 AI 路线：把整段文本交给模型，
让它按语言分栏，再填回界面。另外还提供：
  * `add_furigana()`：给日语歌词里没写读音的汉字补 `{{photrans|汉字|读音}}`
    （config.yaml 的 `wikitext.furigana_all`）；
  * `mark_translation()`：中文栏和日语栏**行数对不上**时，让模型给出「每行中文对应第几行日语」，
    好把日语栏的演唱者标记搬到中文栏（界面上的「按日语标记中文」，见 lyrics_editor.ai_mark_chs）。

是否**允许使用 AI** 由 config.yaml 的 `wikitext.ai_lyrics` 决定：
  * false → 界面不显示「AI 识别并填入」按钮，recognize() 也会直接拒绝，
    因此不会有任何联网调用（纯规则识别照常可用）；
  * true  → 按钮可用，但仍需在 wiki_credentials.yaml 里填 ai_api_key，
    没填时按钮置灰并说明原因。
振假名那一路受 `wikitext.furigana_all` 控制（默认关），同样需要 ai_api_key。
请求构造（provider / base_url / model / 超时 / 代理）与「AI 参考封面生成 CSS」
共用 utils/ai_css.py 的同一套实现，所以几处只需要配一次密钥。
"""
import json
import logging
import re
from typing import Dict, List, Optional, Sequence

from config.config import get_config
from utils import ai_css, japanese

# 整首歌 + JSON 外壳，1024 个 token 不够，单列放宽（三栏合计仍受模型上下文限制）
MAX_TOKENS = 8192
SYSTEM_PROMPT = """\
你是歌词整理助手。用户会给你一段或多段歌词，里面可能混着日语原文、中文翻译、罗马音，
也可能带编号、时间轴或多余空行。请把它们按语言拆成三栏。

硬性规则：
1. 只输出一个 JSON 对象：{"jap": "...", "chs": "...", "roma": "..."}，不要输出解释或 Markdown。
2. 三栏都是字符串，行与行用 \\n 分隔；**必须逐字保留原文**，不要翻译、改写、合并、拆分或补全，
   也不要添加行号、标点或你自己的注释。
3. 原文里的空行要原样保留（用于对齐段落），不要多留也不要少留。
4. 含假名（平假名 / 片假名）或日语汉字的行放 jap；中文（简体 / 繁体）行放 chs；
   整行都是拉丁字母（罗马音）的放 roma。
5. 认不出语言的行，或同一行里混着两种语言时，优先放进 jap，不要丢掉任何一行。
6. 某一栏没有内容就返回空字符串 ""，不要为了凑数而编造歌词。
7. 如果用户额外提供了「已确认的日语原文」，那是准确的分行参照：中文行按它的行数对齐挑出来。
8. **三栏的分段要一致**：日语原文里用空行分段的地方，中文栏与罗马音栏也要在**同一位置**留空行；
   中文行尽量与日语行一一对应（翻译合并了两句时，把两句写在同一行里），不要挤成一大块或自行多分段。
"""

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")

# ---------------------------------------------------------------- 振假名

FURIGANA_SYSTEM_PROMPT = """\
你是日语歌词的振假名（读音）标注助手。用户会给你一段日语歌词，请给其中**没有写读音**的汉字补上读音。

硬性规则：
1. 只输出歌词本身，不要任何解释、标题或 Markdown 代码围栏。
2. **逐字保留原文**：不要翻译、改写、增删任何文字，也不要增删换行与空行。
3. 补读音的写法是 {{photrans|汉字部分|读音}}，读音一律用**平假名**：
   食べる → {{photrans|食|た}}べる；初音ミク → {{photrans|初音|はつね}}ミク。
   漢字后面的送り假名要留在模板外面（上例的「べる」）。
4. 已经写成「漢字(かんじ)」的地方，改写成 {{photrans|漢字|かんじ}}；
   已经是 {{photrans|…|…}} 的地方原样保留，不要再套一层。
5. 平假名、片假名、数字、拉丁字母、标点，以及 {{…}} / [[…]] 这类模板与链接原样保留。
6. 整行没有汉字就原样输出那一行；一个字都不改也要把整段原样返回。
"""

# {{photrans|漢字|かんじ}}（参数里不允许再出现花括号，避免误呑模板）
PHOTRANS_RE = re.compile(r"\{\{\s*photrans\s*\|([^{}|]*)\|([^{}]*)\}\}")


def _plain_lines(text: str) -> List[str]:
    """比对用：把 {{photrans|漢|かん}} 与「漢(かん)」都还原成裸汉字，逐行去掉行尾空白。"""
    plain = PHOTRANS_RE.sub(lambda match: match.group(1), text or "")
    plain = japanese.furigana_local(plain)
    plain = PHOTRANS_RE.sub(lambda match: match.group(1), plain)
    return [line.rstrip() for line in plain.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def furigana_counts(text: str) -> int:
    """正文里有多少个 {{photrans}}。"""
    return len(PHOTRANS_RE.findall(text or ""))


def build_furigana_prompt(lyrics: str) -> str:
    """拼给模型的文字要求。"""
    return "\n".join(["请给下面这段日语歌词补上振假名，只输出补好的歌词：",
                      "-----", str(lyrics or "").rstrip(), "-----"])


def add_furigana(lyrics: str) -> Dict[str, object]:
    """给日语歌词补振假名。

    返回 {'ok': True, 'lyrics': ..., 'added': n, 'message': ...} 或 {'ok': False, 'error': ...}。
    拿到结果后会再比一次「去掉 {{photrans}} 之后是否与原文逐行一致」，模型改动了歌词就整段丢弃。
    """
    text = str(lyrics or "").strip()
    if not text:
        return {"ok": False, "error": "没有日语歌词"}
    if not furigana_enabled():
        return {"ok": False, "error": "已在 config.yaml 里关闭 AI 生成振假名（wikitext.furigana_all: false）"}
    cfg = ai_css.settings()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 ai_api_key（见 wiki_credentials.yaml）"}

    url, headers, body = ai_css.build_request(cfg, build_furigana_prompt(text), None,
                                              system=FURIGANA_SYSTEM_PROMPT, max_tokens=MAX_TOKENS)
    data, error = ai_css._post(url, headers, body)
    if data is None:
        return {"ok": False, "error": error}
    result = clean_lines(ai_css._reply_text(cfg, data))
    if not result:
        return {"ok": False, "error": "模型没有返回内容，请重试"}
    # 只允许「加注音」：去掉所有 {{photrans}} 后必须与原文逐行相同，否则宁可不要
    if _plain_lines(result) != _plain_lines(text):
        logging.warning("AI 生成的振假名改动了歌词正文，已丢弃。模型返回：%s", result[:200])
        return {"ok": False, "error": "模型改动了歌词内容，已放弃这次结果"}
    added = furigana_counts(result) - furigana_counts(text)
    return {"ok": True, "lyrics": result, "added": added,
            "message": f"已补 {added} 处振假名" if added > 0 else "没有可补的汉字"}


def generate_furigana(lyrics: str) -> str:
    """给日语歌词补振假名，供生成流程调用。

    关闭 / 没密钥 / 失败 / 模型改动正文时都**原样返回**（只记日志），绝不打断生成。
    """
    if not str(lyrics or "").strip() or not furigana_enabled():
        return lyrics
    result = add_furigana(lyrics)
    if not result.get("ok"):
        logging.warning("AI 生成振假名跳过：%s", result.get("error"))
        return lyrics
    logging.info("AI 生成振假名：%s", result.get("message"))
    return str(result.get("lyrics") or lyrics)


# ---------------------------------------------------------------- 配置 / 开关

def enabled() -> bool:
    """config.yaml 是否允许使用 AI 识别歌词（wikitext.ai_lyrics）。"""
    wikitext = getattr(get_config(), "wikitext", None)
    return bool(getattr(wikitext, "ai_lyrics", False))


def furigana_enabled() -> bool:
    """config.yaml 是否要用 AI 生成振假名（wikitext.furigana_all）。"""
    wikitext = getattr(get_config(), "wikitext", None)
    return bool(getattr(wikitext, "furigana_all", False))


def context() -> Dict[str, object]:
    """给歌词整理窗口用：按钮是否可用、是否该整块隐藏、当前模型、不可用原因。"""
    allowed = enabled()
    cfg = ai_css.settings()
    reason = ""
    if not allowed:
        reason = "已在 config.yaml 里关闭（wikitext.ai_lyrics: false）"
    elif not cfg["api_key"]:
        reason = "请在 wiki_credentials.yaml 里填写 ai_api_key"
    return {
        "enabled": allowed and bool(cfg["api_key"]),
        "hidden": not allowed,
        "model": cfg["model"],
        "reason": reason,
    }


# ---------------------------------------------------------------- 请求内容

def build_prompt(text: str, jap: str = "") -> str:
    """拼给模型的文字要求。"""
    lines = ["请把下面的歌词按语言分栏（日语原文 / 中文翻译 / 罗马音）。"]
    if str(jap or "").strip():
        lines += ["", "已确认的日语原文（分行参照，用它挑出对应的中文行）：", str(jap).strip()]
    lines += ["", "待归类歌词：", "-----", str(text or "").rstrip(), "-----", "",
              '只输出 JSON，例如：{"jap": "…", "chs": "…", "roma": ""}']
    return "\n".join(lines)


def clean_lines(value) -> str:
    """清洗模型给出的一栏：去掉代码围栏与行尾空白，压掉首尾空行。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _FENCE_RE.sub("", text).replace("```", "")
    lines: List[str] = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


# ---------------------------------------------------------------- 对外入口

def recognize(payload_json: str) -> Dict[str, object]:
    """给编辑器调用：返回 {'ok': True, 'jap'/'chs'/'roma', 'message'} 或 {'ok': False, 'error'}。

    payload（JSON 字符串）：text 待归类歌词、jap 已有日语栏（可空，作为分行参照）。
    """
    try:
        payload = json.loads(payload_json or "{}")
    except ValueError:
        return {"ok": False, "error": "参数不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "参数格式不正确"}
    if not enabled():
        return {"ok": False, "error": "已在 config.yaml 里关闭 AI 识别歌词（wikitext.ai_lyrics: false）"}

    text = str(payload.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "请先在左边粘贴歌词"}

    cfg = ai_css.settings()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 ai_api_key（见 wiki_credentials.yaml）"}

    url, headers, body = ai_css.build_request(cfg, build_prompt(text, str(payload.get("jap") or "")),
                                              None, system=SYSTEM_PROMPT, max_tokens=MAX_TOKENS)
    data, error = ai_css._post(url, headers, body)
    if data is None:
        return {"ok": False, "error": error}

    result = ai_css.extract_json(ai_css._reply_text(cfg, data))
    if not result:
        return {"ok": False, "error": "模型返回的内容不是 JSON，请重试"}
    jap = clean_lines(result.get("jap"))
    chs = clean_lines(result.get("chs"))
    roma = clean_lines(result.get("roma"))
    if not jap and not chs and not roma:
        return {"ok": False, "error": "模型没有识别出任何歌词，请重试"}
    return {"ok": True, "mode": "ai", "jap": jap, "chs": chs, "roma": roma,
            "message": f"已用 AI 分栏（{cfg['model']}），请核对后再点「完成」"}


# ---------------------------------------------------------------- 中文栏标记对齐

MARK_SYSTEM_PROMPT = """\
你是歌词对齐助手。用户给你**已分行**的日语歌词（每行前面有行号，部分行还写了「谁唱」）
和对应的中文翻译（同样带行号）。请判断每一行中文对应日语的第几行。

硬性规则：
1. 只输出一个 JSON 对象：{"pairs": {"1": [2], "2": [2], "3": [4, 5]}}，不要解释、不要 Markdown。
2. 键是**中文行号**，值是一个数组（对应的**日语行号**），两者都从 1 开始。
3. 一行中文合并了两行日语的翻译时可以给多个日语行号；完全对不上（译者补的说明、语气词等）就给空数组。
4. 行号不能超出范围，也不要编造日语行。
5. **每个中文行都要出现在结果里**（对不上就给空数组），不要漏行。
6. 只判断对应关系，不要翻译、改写或输出歌词本身。
"""


def build_mark_prompt(jap_lines: Sequence[str], chs_lines: Sequence[str],
                      marks: Optional[Dict] = None) -> str:
    """拼「中文行 → 日语行」的对齐要求（带上日语的演唱者标记，方便模型认出合唱 / 对唱）。"""
    marks = marks or {}

    def who(index: int) -> str:
        value = marks.get(str(index)) or marks.get(index)
        if not value:
            return "—"
        segments = value if any(isinstance(item, list) for item in value) else [value]
        names: List[str] = []
        for segment in segments:
            for name in segment if isinstance(segment, list) else []:
                if name and name not in names:
                    names.append(name)
        return "+".join(names) or "—"

    lines = ["日语歌词（行号 | 演唱者）："]
    lines += [f"{index + 1} | {who(index)} | {line}" for index, line in enumerate(jap_lines)]
    lines += ["", "中文歌词（行号）："]
    lines += [f"{index + 1} | {line}" for index, line in enumerate(chs_lines)]
    lines += ["", '只输出 JSON，例如：{"pairs": {"1": [1], "2": [1, 2]}}']
    return "\n".join(lines)


def mark_translation(jap: str, chs: str, marks: Optional[Dict] = None) -> Dict[str, object]:
    """让 AI 对齐中文行与日语行，返回 {'ok': True, 'pairs': {中文行下标: [日语行下标…]}}。

    下标都是 **0 起**（给 `lyrics_colors.retarget_marks` 用），越界 / 非整数的行号会被丢掉。
    """
    jap_lines = str(jap or "").rstrip().split("\n")
    chs_lines = str(chs or "").rstrip().split("\n")
    if not [line for line in jap_lines if line.strip()]:
        return {"ok": False, "error": "日语栏是空的"}
    if not [line for line in chs_lines if line.strip()]:
        return {"ok": False, "error": "中文栏是空的"}
    if not enabled():
        return {"ok": False, "error": "已在 config.yaml 里关闭 AI 识别歌词（wikitext.ai_lyrics: false）"}
    cfg = ai_css.settings()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 ai_api_key（见 wiki_credentials.yaml）"}

    url, headers, body = ai_css.build_request(cfg, build_mark_prompt(jap_lines, chs_lines, marks),
                                              None, system=MARK_SYSTEM_PROMPT,
                                              max_tokens=MAX_TOKENS)
    data, error = ai_css._post(url, headers, body)
    if data is None:
        return {"ok": False, "error": error}
    result = ai_css.extract_json(ai_css._reply_text(cfg, data))
    if not isinstance(result, dict):
        return {"ok": False, "error": "模型返回的内容不是 JSON，请重试"}
    pairs = _clean_pairs(result.get("pairs"), len(jap_lines))
    if not pairs:
        return {"ok": False, "error": "模型没有给出可用的对齐结果，请重试"}
    return {"ok": True, "pairs": pairs, "model": cfg["model"]}


def _clean_pairs(value, jap_count: int) -> Dict[int, List[int]]:
    """把模型给的 {中文行号: [日语行号…]} 洗成 0 起下标、过滤越界与非法值。"""
    if not isinstance(value, dict):
        return {}
    pairs: Dict[int, List[int]] = {}
    for raw_line, raw_sources in value.items():
        try:
            line = int(raw_line) - 1
        except (TypeError, ValueError):
            continue
        if line < 0:
            continue
        sources: List[int] = []
        for item in raw_sources if isinstance(raw_sources, list) else []:
            try:
                source = int(item) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= source < jap_count and source not in sources:
                sources.append(source)
        pairs[line] = sources
    return pairs
