"""封面 AI 超分：「样式」页「AI 提升分辨率」按钮用。

走 waifu2x 的公开接口（免费、无需密钥，适合 VOCALOID 封面这种二次元插画）：

    curl -F "file=@cover.jpg" -F "scale=2" -F "noise=1" -F "style=art" \
         https://api.waifu2x.udp.jp/api

接口偶尔会换域名 / 抽风，所以按 `API_URLS` 依次试。结果写到系统临时目录的 PNG 文件，
由样式页当成「导入的封面」载入；用户点「保存并继续」时才会写进提交用的封面文件。
只依赖 requests 与 Pillow。
"""
import io
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

import requests
from PIL import Image

from config.config import get_config

API_URLS = ("https://api.waifu2x.udp.jp/api", "https://waifu2x.udp.jp/api")
TIMEOUT = 180
SCALE = 2
NOISE = 1
STYLE = "art"
# 公开接口的请求体上限（实测 6 MB；更大的封面本来也谈不上「模糊」）
MAX_BYTES = 6 * 1024 * 1024


def _session() -> requests.Session:
    session = requests.Session()
    proxies = getattr(get_config(), "proxies", None)
    if proxies:
        session.proxies.update({"https": proxies, "http": proxies})
    return session


def _mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "image/png"


def _post(session: requests.Session, path: Path, data: bytes,
          scale: int, noise: int) -> Tuple[Optional[bytes], str]:
    """依次试接口；返回 (图片字节, 错误信息)。"""
    last_error = ""
    for url in API_URLS:
        try:
            response = session.post(
                url,
                files={"file": (path.name, data, _mime(path))},
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
        if response.content:
            return response.content, ""
        last_error = f"{url}：返回了空内容"
    return None, last_error or "没有可用的超分接口"


def upscale_cover(path: Optional[Path], scale: int = SCALE,
                  noise: int = NOISE) -> dict:
    """把封面放大 `scale` 倍并降噪，返回 `{'ok': True, 'path': …}` 或错误。

    结果一律另存为临时 PNG，不改动原图；样式页把它当成新导入的封面，保存时才写到
    提交用的 `<译名>封面.jpg`。
    """
    if path is None or not Path(path).is_file():
        return {"ok": False, "error": "还没有封面图"}
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as e:
        return {"ok": False, "error": f"读不到封面图片：{e}"}
    if len(data) > MAX_BYTES:
        return {"ok": False,
                "error": f"封面文件太大（{len(data) / 1024 / 1024:.1f} MB），"
                         "超过 waifu2x 接口的 6 MB 上限"}
    try:
        with Image.open(io.BytesIO(data)) as original:
            original.load()
            width, height = original.size
    except Exception as e:                          # noqa: BLE001 - 不是图片 / 格式不支持
        return {"ok": False, "error": f"认不出这张图片：{e}"}

    payload, error = _post(_session(), source, data, max(1, int(scale)), max(0, int(noise)))
    if payload is None:
        return {"ok": False, "error": f"AI 提升失败：{error}"}

    fd, name = tempfile.mkstemp(prefix="voca_ai_upscale_", suffix=".png")
    os.close(fd)
    target = Path(name)
    try:
        with Image.open(io.BytesIO(payload)) as enhanced:
            enhanced.load()
            width2, height2 = enhanced.size
            enhanced.save(target, format="PNG")
    except Exception as e:                          # noqa: BLE001 - 接口返回了坏图
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"接口返回的不是有效图片：{e}"}

    logging.info("封面已用 waifu2x 提升：%s → %s（%dx%d → %dx%d）",
                 source, target, width, height, width2, height2)
    return {"ok": True, "path": str(target), "width": width2, "height": height2,
            "sourceWidth": width, "sourceHeight": height,
            "message": f"封面已提升：{width}×{height} → {width2}×{height2}"}
