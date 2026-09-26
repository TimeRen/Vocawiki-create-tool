"""AI 歌词识别：用大模型把混在一起的歌词拆成日语 / 中文 / 罗马音三栏。

歌词整理窗口里「自动识别并填入」是纯规则实现（见 utils/lyrics_editor.py），
遇到奇怪的排版会认不准；这个模块提供一条 AI 路线：把整段文本交给模型，
让它按语言分栏，再填回界面。

是否**允许使用 AI** 由 config.yaml 的 `wikitext.ai_lyrics` 决定：
  * false → 界面不显示「AI 识别并填入」按钮，recognize() 也会直接拒绝，
    因此不会有任何联网调用（纯规则识别照常可用）；
  * true  → 按钮可用，但仍需在 wiki_credentials.yaml 里填 ai_api_key，
    没填时按钮置灰并说明原因。
请求构造（provider / base_url / model / 超时 / 代理）与「AI 参考封面生成 CSS」
共用 utils/ai_css.py 的同一套实现，所以两处只需要配一次密钥。
"""
import json
import re
from typing import Dict, List

from config.config import get_config
from utils import ai_css

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
"""

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


# ---------------------------------------------------------------- 配置 / 开关

def enabled() -> bool:
    """config.yaml 是否允许使用 AI 识别歌词（wikitext.ai_lyrics）。"""
    wikitext = getattr(get_config(), "wikitext", None)
    return bool(getattr(wikitext, "ai_lyrics", False))


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
