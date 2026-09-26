"""参考封面图，用多模态 AI 生成与图片设计 / 配色协调的 CSS 声明。

编辑器（utils/color_editor.py）通过 pywebview 调用本模块：

    封面图 + 各对象的当前样式与可用属性  ->  {对象 id: "CSS 声明文本"}

补充要求（note）可由用户在编辑器里临时填写；编辑器打开时会用 config.yaml 里
color.ai_prompt_songbox / ai_prompt_intro / ai_prompt_lyrics 预填三栏的默认值。
只依赖 requests 与 Pillow（后者用于把图片缩小后再发送，控制体积与费用）；
密钥与 Vocawiki 凭据同放在 wiki_credentials.yaml 里。
"""
import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from config.config import get_ai_credentials, get_config

TIMEOUT = 180
MAX_IMAGE_EDGE = 768          # 送给模型的图片最长边（够看清配色，又能省 token）
JPEG_QUALITY = 82
DEFAULT_PROVIDER = "openai"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"          # DeepSeek，OpenAI 兼容
DEFAULT_MODEL = "deepseek-flash"                         # = DeepSeek-V4.1-Flash，支持看图
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-latest"
ANTHROPIC_VERSION = "2023-06-01"
MAX_TOKENS = 1024
MAX_CSS_LENGTH = 1200

PROP_RE = re.compile(r"^-{0,2}[a-zA-Z][a-zA-Z0-9-]*$")
COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
DATA_URI_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", re.S)

SYSTEM_PROMPT = """\
你是资深 CSS 视觉设计师，擅长照着参考图配色。用户会给你一张歌曲封面图，
以及若干「可编辑对象」的当前样式和可用属性。请参考封面图的配色、明暗与气质，
为每个对象生成一份 CSS 声明。

硬性规则：
1. 只输出一个 JSON 对象：键是对象 id，值是 CSS 声明文本。不要输出解释、Markdown 或任何多余文字。
2. 值必须是「属性: 值;」串联的纯声明，不能含选择器、大括号、注释、换行、@ 规则或 !important。
3. 只能使用该对象列出的属性；颜色写成 #rrggbb 或 rgba(...)，不要用颜色名或变量。
4. 主色与辅助色要从封面图里取（可略作明度 / 饱和度调整），整体调性统一。
5. 保证可读性：文字与其背后底色的对比度至少 4.5:1，不要出现看不清的文字。
6. 底色一律用 background-color 或 background 表达（工具会自动转换成模板需要的写法）。
7. 当前样式只作参考，可以按封面图重做；但不要引入没有列出的属性。
8. colorOnly 为 true 时，只能使用颜色相关属性，不要写尺寸、间距、字号等。
"""


# ---------------------------------------------------------------- 配置

def settings() -> Dict[str, object]:
    """读取 AI 配置并补上默认值（默认 DeepSeek-V4.1-Flash 的 OpenAI 兼容接口）。"""
    creds = get_ai_credentials()
    provider = str(creds.get("provider") or DEFAULT_PROVIDER).strip().lower()
    if provider not in ("openai", "anthropic"):
        provider = DEFAULT_PROVIDER
    base_url = str(creds.get("base_url") or "").strip().rstrip("/")
    model = str(creds.get("model") or "").strip()
    if not base_url:
        base_url = DEFAULT_ANTHROPIC_BASE_URL if provider == "anthropic" else DEFAULT_BASE_URL
    if not model:
        model = DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else DEFAULT_MODEL
    thinking = str(creds.get("thinking") or "").strip().lower()
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": str(creds.get("api_key") or "").strip(),
        "thinking": thinking in ("1", "true", "yes", "on"),
    }


def is_deepseek(base_url: object) -> bool:
    """是否 DeepSeek 端点（只有它认 thinking 开关）。"""
    return "deepseek" in str(base_url or "").lower()


# 编辑器 Tab -> config.yaml 里的默认提示词字段名
PROMPT_KEYS = (("songbox", "ai_prompt_songbox"),
               ("intro", "ai_prompt_intro"),
               ("lyrics", "ai_prompt_lyrics"))


def prompt_defaults() -> Dict[str, str]:
    """config.yaml 里 color.ai_prompt_* 的三栏默认提示词（Songbox / Introduction / 歌词）。

    编辑器打开时会把它预填进 AI 面板的「补充要求」，用户仍可在界面上改写。
    """
    color = getattr(get_config(), "color", None)
    return {key: str(getattr(color, field, "") or "").strip() for key, field in PROMPT_KEYS}


def context() -> Dict[str, object]:
    """给编辑器用：按钮是否可用、是否该整块隐藏、当前模型、三栏默认提示词。"""
    cfg = settings()
    enabled_switch = bool(getattr(get_config().color, "ai_css", True))
    reason = ""
    if not enabled_switch:
        reason = "已在 config.yaml 里关闭（color.ai_css: false）"
    elif not cfg["api_key"]:
        reason = "请在 wiki_credentials.yaml 里填写 ai_api_key"
    return {
        "enabled": enabled_switch and bool(cfg["api_key"]),
        "hidden": not enabled_switch,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "reason": reason,
        "prompts": prompt_defaults(),
    }


# ---------------------------------------------------------------- 图片

def encode_image_bytes(data: bytes) -> Optional[Tuple[str, str]]:
    """把图片字节缩小后编码成 (mime, base64)；无法处理时返回 None。"""
    try:
        from PIL import Image
    except ImportError:                                     # 没装 Pillow 也要能用
        logging.warning("未安装 Pillow，AI 生成将直接使用原图，体积可能较大")
        return ("image/jpeg", base64.b64encode(data).decode("ascii")) if data else None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:
        logging.debug("AI：图片解码失败 %s", e)
        return None
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    width, height = image.size
    longest = max(width, height)
    if longest > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / float(longest)
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return "image/jpeg", base64.b64encode(buffer.getvalue()).decode("ascii")


def encode_data_uri(uri: str) -> Optional[Tuple[str, str]]:
    """解析编辑器传来的 data URI（封面或手动导入的参考图）。"""
    match = DATA_URI_RE.match(str(uri or "").strip())
    if not match:
        return None
    try:
        return encode_image_bytes(base64.b64decode(match.group(2)))
    except Exception as e:
        logging.debug("AI：data URI 解析失败 %s", e)
        return None


def encode_image_file(path) -> Optional[Tuple[str, str]]:
    """读取磁盘上的封面图。"""
    if not path:
        return None
    try:
        return encode_image_bytes(Path(path).read_bytes())
    except Exception as e:
        logging.debug("AI：读取封面失败 %s", e)
        return None


# ---------------------------------------------------------------- 输出清洗

def clean_css(text: str) -> str:
    """把模型输出清洗成单行「属性: 值;」声明，丢掉选择器 / 注释 / 不安全内容。"""
    raw = COMMENT_RE.sub("", str(text or ""))
    raw = raw.replace("```css", " ").replace("```", " ")
    raw = raw.replace("{", ";").replace("}", ";")           # 规则块一律拆成声明
    raw = re.sub(r"!important", "", raw, flags=re.I)
    raw = re.sub(r"url\s*\([^)]*\)", "none", raw, flags=re.I)  # 不允许外链资源
    parts: List[str] = []
    for segment in raw.split(";"):
        segment = " ".join(segment.split())
        if not segment or ":" not in segment:
            continue
        prop, _, value = segment.partition(":")
        prop = prop.strip()
        if not PROP_RE.match(prop):                          # 例如「.tag color」→ 取最后一段
            prop = prop.split()[-1] if prop.split() else ""
        if not PROP_RE.match(prop):
            continue
        value = value.strip()
        if not value or len(value) > 200:
            continue
        parts.append(f"{prop}: {value}")
    css = "; ".join(parts)
    if len(css) > MAX_CSS_LENGTH:
        css = css[:MAX_CSS_LENGTH].rsplit(";", 1)[0]
    return css + ";" if css else ""


def extract_json(text: str) -> Optional[dict]:
    """从模型回复里抠出 JSON 对象（容忍 ```json 包裹或前后多余文字）。"""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 请求构造

def build_prompt(targets: List[dict], color_only: bool, note: str) -> str:
    """把对象列表拼成给模型的文字要求。"""
    lines = ["请为下面每个对象生成 CSS 声明。",
             f"colorOnly = {'true' if color_only else 'false'}"]
    for item in targets:
        lines.append("")
        lines.append(f"- id: {item.get('id')}")
        lines.append(f"  对象: {item.get('label') or item.get('id')}")
        props = item.get("props") or []
        lines.append(f"  可用属性: {', '.join(props) if props else '（不限）'}")
        if item.get("current"):
            lines.append(f"  当前样式（仅作参考）: {item['current']}")
    if note:
        lines.append("")
        lines.append(f"用户补充要求：{note}")
    first = targets[0].get("id") if targets else "id"
    lines.append("")
    lines.append('只输出 JSON，例如：{"%s": "background-color: #2f5d8a; color: #ffffff;"}' % first)
    return "\n".join(lines)


def build_request(cfg: Dict[str, object], prompt: str, image: Optional[Tuple[str, str]],
                  system: str = SYSTEM_PROMPT,
                  max_tokens: int = MAX_TOKENS) -> Tuple[str, Dict[str, str], dict]:
    """构造 (url, headers, body)；同时兼容 OpenAI 兼容接口与 Anthropic。

    图片一律放在 user 消息里（DeepSeek 等要求图片不能出现在 system / assistant 消息中）。
    system / max_tokens 可覆盖，供其他 AI 功能（如 AI 歌词识别）复用同一套请求构造。
    """
    if cfg["provider"] == "anthropic":
        content: List[dict] = []
        if image:
            content.append({"type": "image",
                            "source": {"type": "base64", "media_type": image[0], "data": image[1]}})
        content.append({"type": "text", "text": prompt})
        body = {
            "model": cfg["model"],
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"x-api-key": cfg["api_key"], "anthropic-version": ANTHROPIC_VERSION,
                   "content-type": "application/json"}
        return str(cfg["base_url"]) + "/v1/messages", headers, body

    content = [{"type": "text", "text": prompt}]
    if image:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:{image[0]};base64,{image[1]}"}})
    body = {
        "model": cfg["model"],
        "temperature": 0.6,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    if is_deepseek(cfg["base_url"]):
        # DeepSeek 默认开思考模式：CoT 会拖慢响应并占 max_tokens，抽配色不需要，显式关掉
        body["thinking"] = {"type": "enabled" if cfg.get("thinking") else "disabled"}
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "content-type": "application/json"}
    url = str(cfg["base_url"])
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    return url, headers, body


def _reply_text(cfg: Dict[str, object], data: dict) -> str:
    """从响应里取出模型文字。"""
    if cfg["provider"] == "anthropic":
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):                            # 少数服务返回分段内容
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _post(url: str, headers: Dict[str, str], body: dict) -> Tuple[Optional[dict], str]:
    """发请求；返回 (json, 错误信息)。"""
    session = requests.Session()
    proxies = get_config().proxies
    if proxies:
        session.proxies.update({"https": proxies, "http": proxies})
    try:
        response = session.post(url, headers=headers, json=body, timeout=TIMEOUT)
    except Exception as e:
        return None, f"网络请求失败：{e}"
    if response.status_code >= 400:
        detail = response.text.strip().replace("\n", " ")[:200]
        return None, f"接口返回 {response.status_code}：{detail}"
    try:
        return response.json(), ""
    except ValueError:
        return None, "接口返回的不是 JSON，请检查 base_url 是否为兼容接口"


# ---------------------------------------------------------------- 对外入口

def generate_css(payload_json: str, cover_image=None) -> Dict[str, object]:
    """给编辑器调用：返回 {'ok': True, 'css': {id: 声明}} 或 {'ok': False, 'error': ...}。

    payload（JSON 字符串）：
        targets  [{id, label, props, current}]
        colorOnly / note / image(data URI)
    cover_image 是磁盘封面路径，作为没有 data URI 时的兜底。
    """
    try:
        payload = json.loads(payload_json or "{}")
    except ValueError:
        return {"ok": False, "error": "参数不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "参数格式不正确"}

    cfg = settings()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 ai_api_key（见 wiki_credentials.yaml）"}

    targets = [item for item in (payload.get("targets") or [])
               if isinstance(item, dict) and item.get("id")]
    if not targets:
        return {"ok": False, "error": "没有需要生成的对象"}

    image = encode_data_uri(payload.get("image") or "") or encode_image_file(cover_image)
    if image is None:
        return {"ok": False, "error": "没有可用的封面图：请先在预览区导入一张图片"}

    prompt = build_prompt(targets, bool(payload.get("colorOnly")),
                          str(payload.get("note") or "").strip())
    url, headers, body = build_request(cfg, prompt, image)
    data, error = _post(url, headers, body)
    # 部分接口不认可选参数（response_format / thinking），去掉后重试一次
    for optional in ("response_format", "thinking"):
        if data is None and error and optional in error and optional in body:
            logging.info("AI：接口不认 %s，去掉后重试", optional)
            body.pop(optional, None)
            data, error = _post(url, headers, body)
    if data is None:
        return {"ok": False, "error": error}

    reply = _reply_text(cfg, data)
    if not reply.strip():
        return {"ok": False, "error": "模型没有返回内容"}

    parsed = extract_json(reply)
    result: Dict[str, str] = {}
    if parsed:
        for item in targets:
            raw = parsed.get(item["id"])
            if isinstance(raw, list):                        # 偶尔会包成数组
                raw = "; ".join(str(x) for x in raw)
            css = clean_css(raw or "")
            if css:
                result[item["id"]] = css
    elif len(targets) == 1:
        css = clean_css(reply)
        if css:
            result[targets[0]["id"]] = css
    if not result:
        return {"ok": False, "error": "没能从模型回复里解析出 CSS，可重试一次"}

    logging.info("AI 生成 CSS：%s / %s", cfg["provider"], cfg["model"])
    return {"ok": True, "css": result, "provider": cfg["provider"], "model": cfg["model"]}
