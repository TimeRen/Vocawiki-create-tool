"""封面 AI 超分：「样式」页「AI 提升分辨率」按钮用。

走 waifu2x 的公开接口（免费、无需密钥，适合 VOCALOID 封面这种二次元插画）：

    curl -F "file=@cover.jpg" -F "scale=2" -F "noise=1" -F "style=art" \
         https://api.waifu2x.udp.jp/api

接口偶尔会换域名 / 抽风，所以按 `API_URLS` 依次试。上传前会统一转成 PNG / JPEG
（WebP / GIF / BMP 也能用），并在超过接口上限时压缩 / 缩小。接口被墙、返回错误页或
坏图时退回本地 Lanczos + 锐化（`engine: "local"`），至少不让按钮直接失败。
结果写到系统临时目录的 PNG 文件，由样式页当成「导入的封面」载入；用户点「保存并继续」
时才会写进提交用的封面文件。只依赖 requests 与 Pillow。
"""
import io
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import requests
from PIL import Image, ImageFilter

from config.config import get_config

API_URLS = ("https://api.waifu2x.udp.jp/api", "https://waifu2x.udp.jp/api")
TIMEOUT = 180
SCALE = 2
NOISE = 1
STYLE = "art"
# 公开接口的请求体上限（`max_body_size` 实测 5 MB）
MAX_BYTES = 5 * 1024 * 1024
# 缩放模型允许的**输入**像素上限：官方 `max_pixels` 是 3000×3000 的**输出**，
# 所以 2 倍放大时输入最多 1500×1500（225 万像素）；超过会被接口 400 拒绝。
MAX_PIXELS = 1500 * 1500


def _session() -> requests.Session:
    session = requests.Session()
    proxies = getattr(get_config(), "proxies", None)
    if proxies:
        session.proxies.update({"https": proxies, "http": proxies})
    return session


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)


def _upload_image(source: Path) -> Tuple[bytes, str, str, Tuple[int, int]]:
    """统一转成 PNG / JPEG 再上传：waifu2x 对 WebP / GIF 之类的支持并不可靠。

    返回 `(字节, 文件名, MIME, 原图尺寸)`；PNG 超过接口上限时改用 JPEG，仍然太大就
    按 90% 逐步缩小（这种超大图本来就不缺分辨率）。
    """
    with Image.open(source) as image:
        image.load()
        width, height = image.size
        mode = "RGBA" if _has_alpha(image) else "RGB"
        converted = image.convert(mode)
        if converted.width * converted.height > MAX_PIXELS:
            ratio = math.sqrt(MAX_PIXELS / float(converted.width * converted.height))
            converted = converted.resize(
                (max(1, int(converted.width * ratio)), max(1, int(converted.height * ratio))),
                Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        converted.save(buffer, format="PNG")
        data, mime, suffix = buffer.getvalue(), "image/png", ".png"
        if len(data) > MAX_BYTES and mode == "RGB":
            buffer = io.BytesIO()
            converted.save(buffer, format="JPEG", quality=95)
            candidate = buffer.getvalue()
            if len(candidate) < len(data):
                data, mime, suffix = candidate, "image/jpeg", ".jpg"
        while len(data) > MAX_BYTES and max(converted.size) > 32:
            converted = converted.resize((max(1, converted.width * 9 // 10),
                                          max(1, converted.height * 9 // 10)),
                                         Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            if mode == "RGB":
                converted.save(buffer, format="JPEG", quality=92)
                data, mime, suffix = buffer.getvalue(), "image/jpeg", ".jpg"
            else:
                converted.save(buffer, format="PNG")
                data, mime, suffix = buffer.getvalue(), "image/png", ".png"
    return data, source.stem + suffix, mime, (width, height)


def _looks_like_image(data: bytes) -> bool:
    """接口有时会拿 200 + HTML / JSON 报错；这里只认常见图片文件头。"""
    return (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8")
            or data.startswith(b"GIF8")
            or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"))


def _post(session: requests.Session, filename: str, mime: str, data: bytes,
          scale: int, noise: int) -> Tuple[Optional[bytes], str]:
    """依次试接口；返回 (图片字节, 错误信息)。"""
    last_error = ""
    for url in API_URLS:
        try:
            response = session.post(
                url,
                files={"file": (filename, data, mime)},
                data={"scale": str(scale), "noise": str(noise), "style": STYLE},
                timeout=TIMEOUT,
            )
        except Exception as e:                      # noqa: BLE001 - 换下一个域名再试
            last_error = f"{url}：{e}"
            logging.warning("waifu2x 请求失败（%s）：%s", url, e)
            continue
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:200]
            last_error = f"接口返回 {response.status_code}：{detail}"
            logging.warning("waifu2x 提升失败：%s", last_error)
            continue
        content = response.content or b""
        if _looks_like_image(content):
            return content, ""
        headers = getattr(response, "headers", None) or {}
        content_type = str(headers.get("content-type") or "未知类型")
        snippet = content[:160].decode("utf-8", "replace").replace("\n", " ")
        last_error = f"接口返回的不是图片（{content_type}）：{snippet or '空内容'}"
        logging.warning("waifu2x 提升失败：%s", last_error)
    return None, last_error or "没有可用的超分接口"


def _new_target() -> Path:
    fd, name = tempfile.mkstemp(prefix="voca_ai_upscale_", suffix=".png")
    os.close(fd)
    return Path(name)


def _local_upscale(source: Path, scale: int) -> dict:
    """waifu2x 不可用时的本地兜底：Lanczos 放大 + 轻度锐化。

    这不是 AI 超分（不会凭空补细节），但至少能把模糊封面放大、让边缘更清晰；
    返回的 `engine` 是 `local`，样式页会把原因一起写出来。
    """
    target = _new_target()
    try:
        with Image.open(source) as image:
            image.load()
            mode = "RGBA" if _has_alpha(image) else "RGB"
            enhanced = image.convert(mode).resize(
                (image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
            enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=1.4, percent=120,
                                                               threshold=2))
            enhanced.save(target, format="PNG")
            width, height = enhanced.size
    except Exception as e:                          # noqa: BLE001 - 本地也失败就如实报错
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"本地超分也失败：{e}"}
    return {"ok": True, "path": str(target), "width": width, "height": height,
            "engine": "local"}


def upscale_cover(path: Optional[Path], scale: int = SCALE,
                  noise: int = NOISE) -> dict:
    """把封面放大 `scale` 倍并降噪，返回 `{'ok': True, 'path': …}` 或错误。

    结果一律另存为临时 PNG，不改动原图；样式页把它当成新导入的封面，保存时才写到
    提交用的 `<译名>封面.jpg`。
    """
    if path is None or not Path(path).is_file():
        return {"ok": False, "error": "还没有封面图"}
    source = Path(path)
    scale = max(1, int(scale))
    noise = max(0, int(noise))
    try:
        data, filename, mime, (width, height) = _upload_image(source)
    except Exception as e:                          # noqa: BLE001 - 不是图片 / 格式不支持
        return {"ok": False, "error": f"认不出这张图片：{e}"}

    payload, error = _post(_session(), filename, mime, data, scale, noise)
    if payload is not None:
        target = _new_target()
        try:
            with Image.open(io.BytesIO(payload)) as enhanced:
                enhanced.load()
                width2, height2 = enhanced.size
                enhanced.save(target, format="PNG")
        except Exception as e:                      # noqa: BLE001 - 接口返回了坏图
            target.unlink(missing_ok=True)
            error = f"接口返回的不是有效图片：{e}"
            logging.warning("waifu2x 提升失败：%s", error)
        else:
            logging.info("封面已用 waifu2x 提升：%s → %s（%dx%d → %dx%d）",
                         source, target, width, height, width2, height2)
            return {"ok": True, "path": str(target), "width": width2, "height": height2,
                    "sourceWidth": width, "sourceHeight": height, "engine": "waifu2x",
                    "message": f"封面已提升：{width}×{height} → {width2}×{height2}"}

    # 接口被墙 / 返回错误页 / 返回坏图：退回本地超分，至少别让按钮直接失败
    local = _local_upscale(source, scale)
    if local.get("ok"):
        local.update({"sourceWidth": width, "sourceHeight": height, "warning": error,
                      "message": f"waifu2x 接口不可用，已改用本地超分："
                                 f"{width}×{height} → {local['width']}×{local['height']}"})
        logging.warning("waifu2x 不可用（%s），已改用本地超分", error)
        return local
    return {"ok": False, "error": f"AI 提升失败：{error}"}
