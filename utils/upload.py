import io
import logging
import math
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from config.config import get_config
from i18n.i18n import _
from models.creators import Person
from utils import login, rate_limit
from utils.helpers import prompt_choices, prompt_response
from utils.name_converter import name_to_cat, name_to_chinese

# 站点虽然允许 24 MB，但超大图（4 倍超分常见 3000+ 像素）在缩略图生成 / 代理层
# 更容易失败；上传前压到这些范围内。JPEG 的尺寸与质量只在超限时才动。
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_UPLOAD_PIXELS = 8_000_000
MAX_UPLOAD_EDGE = 4096
JPEG_QUALITIES = (92, 88, 84, 80, 76)
# 站点不认手里这个 token、自动重新登录也没成功时给用户看的话
LOGIN_EXPIRED_HINT = ("Vocawiki 的登录状态已失效，自动重新登录也没成功"
                      "——请点侧栏头像「重新登录」后再提交一次")


def choose_cover_author(authors: Optional[List[Person]]) -> List[Person]:
    """问「这张图算谁的」：一张封面只挂一个 `[[分类:作者:…]]`。

    VocaDB 记的曲绘可能有好几位（合作曲、或者干脆记错了），站点惯例却是一张图一个作者
    （用户 2026-10-21 反馈「一张图对应一个作者吧」）。所以：
      * 只有一位曲绘 → 直接用他，不问；
      * 好几位 → 列出来让用户挑（默认用法是不动，选完就走）；
      * 用户点最后一项 → 不挂作者分类（返回空表）。
    """
    candidates = [author for author in (authors or []) if author and author.name]
    if len(candidates) <= 1:
        return candidates
    labels = [author.name for author in candidates] + [_("cover_author_none")]
    index = prompt_choices(
        _("cover_author_choose").format(names="、".join(author.name for author in candidates)),
        labels)
    if index >= len(labels):
        return []
    return [candidates[index - 1]]


def choose_characters(vocalists: Optional[List[str]] = None) -> List[str]:
    """在控制台询问封面中出现的歌姬（用于生成分类）。"""
    answer = prompt_choices(_("has_characters"), [_("Yes"), _("No")])
    if answer == 2:
        return []
    vocalists = [name_to_chinese(v) for v in (vocalists or []) if v]
    if vocalists:
        result = []
        for name in vocalists:
            include = prompt_choices(_("has_character_named").format(name=name),
                                     [_("Yes"), _("No")])
            if include == 1:
                result.append(name)
        return result
    characters = []
    while True:
        name = prompt_response(_("character_name_input"))
        if not name:
            break
        characters.append(name)
    return characters


def build_image_description(song_name: str, authors: Optional[List[Person]],
                            source_url: Optional[str],
                            characters: Optional[List[str]] = None) -> str:
    """构建封面文件的说明页 wikitext。

    `authors` 只该有**一位**：站上的惯例是一张图挂一个 `[[分类:作者:…]]`
    （用户 2026-10-21 反馈「一张图对应一个作者吧」——以前把 VocaDB 记的每位曲绘都挂上，
    合作曲就冒出六七个作者分类）。要挑谁由 `choose_cover_author()` 问用户。
    """
    parts = ["== 文件说明 ==", f"歌曲《{song_name}》的封面。", ""]
    if source_url:
        parts.append(f"源地址:{source_url}")
    for author in (authors or [])[:1]:
        parts.append(f"[[分类:作者:{author.name}]]")
    parts.append("[[Category:视频封面]]")
    if characters:
        # 档案页挂的是**分类名**，不是显示名：`File:1 2 FanClub Chinese.jpg` 实测挂
        # `[[分类:Megpoid]]`（不是 `GUMI`）、`File:Synthesizer×Dream.jpg` 挂
        # `[[Category:NAKUMO]]`（不是 `ナクモ`）。用户 2026-10-14 报的 diff 261342
        # 就是这里直接写了歌姬名，`ナクモ` 变成了一个不存在的分类。
        parts.extend(f"[[Category:{name_to_cat(character)}]]" for character in characters)
    parts.append("")
    parts.append("== 授权协议 ==")
    parts.append("{{Copyright}}")
    return "\n".join(parts)


def _save_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _within_upload_limits(width: int, height: int, size: int) -> bool:
    return (size <= MAX_UPLOAD_BYTES
            and width * height <= MAX_UPLOAD_PIXELS
            and max(width, height) <= MAX_UPLOAD_EDGE)


def _prepare_upload(file: Path, filename: str) -> Tuple[bytes, str]:
    """读取上传内容；超大图片先缩到站点更稳妥的尺寸 / 体积。

    返回 (图片字节, MIME)。转换失败时退回原文件，交给站点自己判断。
    """
    raw = file.read_bytes()
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            width, height = source.size
            if _within_upload_limits(width, height, len(raw)):
                return raw, mime
            ratio = min(1.0,
                        MAX_UPLOAD_EDGE / max(width, height),
                        math.sqrt(MAX_UPLOAD_PIXELS / float(width * height)))
            size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
            image = source.convert("RGBA" if source.mode == "RGBA" else "RGB")
            if size != source.size:
                image = image.resize(size, Image.Resampling.LANCZOS)
            if file.suffix.lower() == ".png":
                buffer = io.BytesIO()
                image.save(buffer, "PNG", optimize=True)
                data = buffer.getvalue()
                if len(data) <= MAX_UPLOAD_BYTES:
                    return data, "image/png"
                return raw, mime
            rgb = image.convert("RGB")
            for quality in JPEG_QUALITIES:
                data = _save_jpeg(rgb, quality)
                if len(data) <= MAX_UPLOAD_BYTES:
                    logging.info("封面 %s 超过上传限制，已压成 %dx%d / %.1f MB",
                                 file, size[0], size[1], len(data) / (1024 * 1024))
                    return data, "image/jpeg"
            logging.warning("封面 %s 压缩后仍有 %.1f MB，按原文件上传",
                            file, len(raw) / (1024 * 1024))
    except Exception as e:                              # noqa: BLE001 - 不是图片就原样传
        logging.debug("读取封面 %s 的大小失败：%s", file, e)
    return raw, mime


def upload_image(file: Path, filename: str, song_name: str,
                 authors: Optional[List[Person]] = None,
                 source_url: Optional[str] = None,
                 characters: Optional[List[str]] = None,
                 overwrite: bool = True) -> Dict[str, object]:
    """将封面图片上传到 Vocawiki（非交互，供提交窗口调用）。

    默认覆盖同名文件：样式页换了封面（含 AI 超分）后，同名旧图必须能被替换。
    `overwrite=False` 时保留旧行为（已存在就跳过，返回 `exists: True`）。
    返回 {'ok': True, 'message': ...}；失败返回 {'ok': False, 'error': ...}。
    """
    file = Path(file)
    if not file.exists():
        return {"ok": False, "error": f"封面文件不存在：{file}"}
    if not login.is_logged_in():
        return {"ok": False, "error": "未登录 Vocawiki，无法上传封面"}

    description = build_image_description(song_name, authors, source_url, characters)
    logging.debug("Image description: \n%s", description)

    content, mime = _prepare_upload(file, filename)
    rate_limit.wait_for_slot()                       # 速率墙：上传也算一次编辑
    data = {
        "action": "upload",
        "filename": filename,
        "text": description,
        "comment": "由 Vocawiki条目辅助工具 自动上传",
        "token": login.get_csrf_token(),
        "format": "json",
    }
    if overwrite:
        # 样式页换了封面（尤其是 AI 超分后的新图）时，同名文件必须能覆盖；
        # 否则站点只回 `exists` 警告，新图永远传不上去。
        data["ignorewarnings"] = "1"
    for attempt in (1, 2):
        try:
            response = login.get_session().post(
                get_config().wiki.api_url,
                data=data,
                files={"file": (filename, content, mime)},
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as e:
            logging.error("Upload failed: %s", e)
            return {"ok": False, "error": f"上传封面失败：{e}"}
        # 站点说 token 不认（登录状态轮换 / 掉线）：刷新 token（必要时重新登录）后重传一次
        if attempt == 1 and login.is_token_error(result):
            logging.warning("上传 %s：站点说这个 token 不认（%s），刷新登录状态后重传一次",
                            filename, (result.get("error") or {}).get("code"))
            if login.refresh_token_in(data):
                continue
        break

    if login.is_token_error(result):
        logging.error("上传 %s：站点仍然不认登录状态：%s", filename, result.get("error"))
        return {"ok": False, "error": LOGIN_EXPIRED_HINT}
    if "error" in result:
        error = result["error"]
        logging.error("Upload failed: %s", error)
        return {"ok": False, "error": error.get("info") or error.get("code") or "上传封面失败"}

    upload = result.get("upload", {})
    if upload.get("result") == "Success":
        logging.info("Uploaded cover to Vocawiki: %s", filename)
        return {"ok": True, "message": f"已上传封面「{filename}」"}

    warnings = upload.get("warnings") or {}
    if "exists" in warnings:
        if overwrite:
            # 已经带 ignorewarnings 仍报 exists，通常是内容完全相同的重复上传。
            return {"ok": True, "message": f"封面「{filename}」已是最新版本"}
        return {"ok": False, "exists": True, "error": f"封面「{filename}」已存在，未覆盖"}
    logging.warning("Upload skipped due to warnings: %s", warnings)
    return {"ok": False, "error": f"封面未上传：{warnings or upload}"}

