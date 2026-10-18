"""封面 AI 超分：「样式」页「AI 提升分辨率」按钮用。

走 waifu2x 的公开接口（免费、无需密钥，适合 VOCALOID 封面这种二次元插画）：

    curl -F "file=@cover.jpg" -F "scale=2" -F "noise=1" -F "style=art" \
         https://api.waifu2x.udp.jp/api

接口偶尔会换域名 / 抽风，所以按 `API_URLS` 依次试。上传前会统一转成 PNG / JPEG
（WebP / GIF / BMP 也能用），并在超过接口上限时压缩 / 缩小。接口被墙、返回错误页或
坏图时改用本地 waifu2x CUnet 模型（onnxruntime，首次使用下载约 5 MB）继续 AI 超分；
本地模型也不可用时才退回 Lanczos + 锐化（`engine: "local"`）。
结果写到系统临时目录的 PNG 文件，由样式页当成「导入的封面」载入；用户点「保存并继续」
时才会写进提交用的封面文件。依赖 requests、Pillow、numpy 与 onnxruntime。
"""
import io
import logging
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import numpy as np
except ImportError:  # Android APK 不打包 numpy；在线超分和普通放大仍可用
    np = None
import requests
from PIL import Image, ImageFilter

from config.config import get_config, get_output_path

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

# 公开接口不可用时的本地 waifu2x CUnet 模型（2 倍、noise1）。
# 模型来自 waifu2x-caffe（MIT, lltcggie）的 ONNX 转换：
#   https://github.com/tcyrus/waifu2x-onnx
LOCAL_UPSCALE_DISABLED_ENV = "VOCAWIKI_NO_LOCAL_UPSCALE"
MODEL_NAME = "waifu2x_cunet_noise1_x2.onnx"
MODEL_URLS = (
    "https://github.com/tcyrus/waifu2x-onnx/raw/master/models/cunet/"
    "noise1_scale2.0x_model.onnx",
    "https://media.githubusercontent.com/media/tcyrus/waifu2x-onnx/master/models/cunet/"
    "noise1_scale2.0x_model.onnx",
)
MODEL_MIN_BYTES = 1_000_000          # 防止把 LFS 指针 / 错误页当成模型
CUNET_TILE = 256
CUNET_BORDER = 18                     # 模型输出 440 = (256 - 18×2) × 2
CUNET_STRIDE = 192                    # 留 28 像素重叠，拼图时取平均
# 小封面（最长边 ≤ 800）直接出 4 倍：预览会把 4 倍图缩到控件大小，看起来才不发虚；
# 大图只出 2 倍，避免生成几千万像素的图片。最多 1600 万像素。
SMALL_COVER_EDGE = 800
MAX_OUTPUT_PIXELS = 16_000_000
_local_session = None


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


def _target_scale(source_size: Tuple[int, int]) -> int:
    """输出倍率：小封面 4 倍、大封面 2 倍，且不超过 `MAX_OUTPUT_PIXELS`。"""
    width, height = source_size
    scale = 4 if max(width, height) <= SMALL_COVER_EDGE else 2
    while scale > 1 and width * scale * height * scale > MAX_OUTPUT_PIXELS:
        scale //= 2
    return max(1, scale)


def _resize_to_target(image: Image.Image,
                      source_size: Tuple[int, int]) -> Tuple[Image.Image, int, int]:
    scale = _target_scale(source_size)
    width, height = source_size[0] * scale, source_size[1] * scale
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image, width, height


def _model_path() -> Path:
    return get_output_path() / "models" / MODEL_NAME


def _download_model() -> Optional[Path]:
    """下载/复用本地 waifu2x 模型；失败返回 None。"""
    target = _model_path()
    if target.is_file() and target.stat().st_size > MODEL_MIN_BYTES:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    session = _session()
    for url in MODEL_URLS:
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except Exception as e:                      # noqa: BLE001 - 换下一个源
            logging.warning("下载本地 waifu2x 模型失败（%s）：%s", url, e)
            continue
        data = response.content or b""
        if len(data) < MODEL_MIN_BYTES:
            logging.warning("下载到的不是模型文件（%s，%d 字节）：%s", url, len(data), url)
            continue
        part = target.with_suffix(target.suffix + ".part")
        try:
            part.write_bytes(data)
            part.replace(target)
        except OSError as e:
            part.unlink(missing_ok=True)
            logging.warning("保存本地 waifu2x 模型失败：%s", e)
            continue
        logging.info("已下载本地 waifu2x 模型：%s", target)
        return target
    return None


def _cunet_positions(length: int) -> List[int]:
    if length <= CUNET_TILE:
        return [0]
    result = list(range(0, length - CUNET_TILE + 1, CUNET_STRIDE))
    if result[-1] != length - CUNET_TILE:
        result.append(length - CUNET_TILE)
    return result


def _local_model_upscale(source: Path) -> dict:
    """本地 waifu2x CUnet：在线接口不可用时的真正 AI 超分（模型 2 倍）。

    CUnet 的输入固定 256×256，输出 440×440；四周各有 18 像素无效边，所以先给
    原图补 18 像素边、按 256 切块（块间留重叠），再取每块中间的有效区域拼回去。
    首次使用会下载约 5 MB 的模型到 `output/models/`，之后离线可用。
    模型输出再按 `_target_scale()` 放大到 4 倍（小图）或 2 倍（大图）。
    """
    if np is None:
        return {"ok": False, "error": "未安装 numpy，无法运行本地模型"}
    if "PyQt5.QtWidgets" in sys.modules and "onnxruntime" not in sys.modules:
        # Windows 上先加载 PyQt 的 DLL 再导入 onnxruntime 会 access violation，
        # 这种情况宁可退回普通放大，也不要让整个程序崩掉。
        return {"ok": False, "error": "onnxruntime 需要在 Qt 之前加载"}
    try:
        import onnxruntime as ort
    except Exception as e:                          # noqa: BLE001 - 没装依赖就退回 Lanczos
        return {"ok": False, "error": f"未安装 onnxruntime：{e}"}
    model = _download_model()
    if model is None:
        return {"ok": False, "error": "本地 waifu2x 模型下载失败"}
    global _local_session
    try:
        if _local_session is None:
            try:
                ort.set_default_logger_severity(3)   # 模型里的初始化器警告别刷日志
            except AttributeError:
                pass
            _local_session = ort.InferenceSession(
                str(model), providers=["CPUExecutionProvider"])
    except Exception as e:                          # noqa: BLE001 - 模型坏了 / 运行时不兼容
        return {"ok": False, "error": f"本地 waifu2x 模型加载失败：{e}"}

    try:
        with Image.open(source) as image:
            image.load()
            rgb = image.convert("RGB")
            source_size = rgb.size
            if rgb.width * rgb.height > MAX_PIXELS:
                ratio = math.sqrt(MAX_PIXELS / float(rgb.width * rgb.height))
                rgb = rgb.resize((max(1, int(rgb.width * ratio)),
                                  max(1, int(rgb.height * ratio))),
                                 Image.Resampling.LANCZOS)
            arr = np.asarray(rgb, dtype=np.float32) / 255.0
    except Exception as e:                          # noqa: BLE001
        return {"ok": False, "error": f"读不到这张图片：{e}"}

    border, scale = CUNET_BORDER, SCALE
    padded = np.pad(arr, ((border, border), (border, border), (0, 0)), mode="edge")
    ph, pw = padded.shape[:2]
    out = np.zeros((ph * scale, pw * scale, 3), dtype=np.float32)
    weight = np.zeros((ph * scale, pw * scale, 1), dtype=np.float32)
    input_name = _local_session.get_inputs()[0].name
    output_name = _local_session.get_outputs()[0].name
    for oy in _cunet_positions(ph):
        for ox in _cunet_positions(pw):
            patch = padded[oy:oy + CUNET_TILE, ox:ox + CUNET_TILE]
            if patch.shape[0] != CUNET_TILE or patch.shape[1] != CUNET_TILE:
                patch = np.pad(patch, ((0, CUNET_TILE - patch.shape[0]),
                                       (0, CUNET_TILE - patch.shape[1]), (0, 0)),
                               mode="edge")
            payload = patch.transpose(2, 0, 1)[None].astype(np.float32)
            enhanced = _local_session.run([output_name], {input_name: payload})[0][0]
            enhanced = enhanced.transpose(1, 2, 0)
            # 模型输出对应当前块内部 [border, tile-border) 的输入区域；贴边块只取落在
            # 补边后图像里的那一截（小图 / 最后一行 / 最后一列都走这里）。
            y0, y1 = oy + border, oy + CUNET_TILE - border
            x0, x1 = ox + border, ox + CUNET_TILE - border
            sy0, sy1 = max(y0, 0), min(y1, ph)
            sx0, sx1 = max(x0, 0), min(x1, pw)
            if sy1 <= sy0 or sx1 <= sx0:
                continue
            crop = enhanced[(sy0 - y0) * scale:(sy1 - y0) * scale,
                            (sx0 - x0) * scale:(sx1 - x0) * scale]
            dy, dx = sy0 * scale, sx0 * scale
            out[dy:dy + crop.shape[0], dx:dx + crop.shape[1]] += crop
            weight[dy:dy + crop.shape[0], dx:dx + crop.shape[1]] += 1.0
    out = out[border * scale:border * scale + arr.shape[0] * scale,
              border * scale:border * scale + arr.shape[1] * scale]
    out = np.clip(out / np.maximum(weight[border * scale:border * scale + arr.shape[0] * scale,
                                           border * scale:border * scale + arr.shape[1] * scale],
                                   1e-6), 0, 1)
    target = _new_target()
    try:
        result = Image.fromarray((out * 255.0).round().astype(np.uint8))
        result, width2, height2 = _resize_to_target(result, source_size)
        result.save(target, format="PNG")
    except Exception as e:                          # noqa: BLE001
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"本地 waifu2x 结果保存失败：{e}"}
    return {"ok": True, "path": str(target), "width": width2,
            "height": height2, "engine": "waifu2x-local"}


def _local_upscale(source: Path) -> dict:
    """在线 / 本地模型都不可用时的最后兜底：Lanczos 放大 + 轻度锐化。

    这不是 AI 超分（不会凭空补细节），但至少能把模糊封面放大、让边缘更清晰；
    返回的 `engine` 是 `local`，样式页会把原因一起写出来。
    """
    target = _new_target()
    try:
        with Image.open(source) as image:
            image.load()
            mode = "RGBA" if _has_alpha(image) else "RGB"
            effective = _target_scale((image.width, image.height))
            enhanced = image.convert(mode).resize(
                (image.width * effective, image.height * effective),
                Image.Resampling.LANCZOS)
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
                result, width2, height2 = _resize_to_target(enhanced.convert("RGB"),
                                                            (width, height))
                result.save(target, format="PNG")
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

    # 接口被墙 / 返回错误页 / 返回坏图：用本地 waifu2x 模型（真正的 AI 超分）。
    # Android APK 不打包 onnxruntime，只允许在线接口。
    if os.environ.get(LOCAL_UPSCALE_DISABLED_ENV) == "1":
        local_ai = {"ok": False, "error": "当前平台不提供本地 waifu2x 模型"}
    else:
        local_ai = _local_model_upscale(source)
    if local_ai.get("ok"):
        local_ai.update({"sourceWidth": width, "sourceHeight": height, "warning": error,
                         "message": f"在线接口不可用，已用本地 waifu2x 模型提升："
                                    f"{width}×{height} → "
                                    f"{local_ai['width']}×{local_ai['height']}"})
        logging.warning("在线 waifu2x 不可用（%s），已改用本地模型", error)
        return local_ai
    logging.warning("本地 waifu2x 模型也不可用（%s），最后用 Lanczos 兜底",
                    local_ai.get("error"))
    # 最后兜底：本地模型 / onnxruntime 也没有时，至少放大 + 锐化，别让按钮直接失败
    local = _local_upscale(source)
    if local.get("ok"):
        local.update({"sourceWidth": width, "sourceHeight": height, "warning": error,
                      "message": f"AI 超分不可用，已改用普通放大："
                                 f"{width}×{height} → {local['width']}×{local['height']}"})
        logging.warning("AI 超分不可用（%s），已改用普通放大", error)
        return local
    return {"ok": False, "error": f"AI 提升失败：{error}"}
