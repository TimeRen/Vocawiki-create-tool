import logging
from pathlib import Path
from typing import Dict, List, Optional

from config.config import get_config
from models.creators import Person
from utils import login
from utils.helpers import prompt_choices, prompt_response
from utils.name_converter import name_to_chinese


def choose_characters(vocalists: Optional[List[str]] = None) -> List[str]:
    """在控制台询问封面中出现的歌姬（用于生成分类）。"""
    answer = prompt_choices("该图片是否有出现歌姬？", ["是", "否"])
    if answer == 2:
        return []
    vocalists = [name_to_chinese(v) for v in (vocalists or []) if v]
    if vocalists:
        result = []
        for name in vocalists:
            include = prompt_choices(f"该图片中是否有出现「{name}」？", ["是", "否"])
            if include == 1:
                result.append(name)
        return result
    characters = []
    while True:
        name = prompt_response("请输入出现的歌姬名（如「初音未来」，留空结束）：")
        if not name:
            break
        characters.append(name)
    return characters


def build_image_description(song_name: str, authors: Optional[List[Person]],
                            source_url: Optional[str],
                            characters: Optional[List[str]] = None) -> str:
    """构建封面文件的说明页 wikitext。"""
    parts = ["== 文件说明 ==", f"歌曲《{song_name}》的封面。", ""]
    if source_url:
        parts.append(f"源地址:{source_url}")
    if authors:
        parts.extend(f"[[分类:作者:{author.name}]]" for author in authors)
    parts.append("[[Category:视频封面]]")
    if characters:
        parts.extend(f"[[Category:{character}]]" for character in characters)
    parts.append("")
    parts.append("== 授权协议 ==")
    parts.append("{{Copyright}}")
    return "\n".join(parts)


def upload_image(file: Path, filename: str, song_name: str,
                 authors: Optional[List[Person]] = None,
                 source_url: Optional[str] = None,
                 characters: Optional[List[str]] = None) -> Dict[str, object]:
    """将封面图片上传到 Vocawiki（非交互，供提交窗口调用）。

    返回 {'ok': True, 'message': ...}；失败返回 {'ok': False, 'error': ...}；
    目标文件已存在时额外带 'exists': True。
    """
    file = Path(file)
    if not file.exists():
        return {"ok": False, "error": f"封面文件不存在：{file}"}
    if not login.is_logged_in():
        return {"ok": False, "error": "未登录 Vocawiki，无法上传封面"}

    description = build_image_description(song_name, authors, source_url, characters)
    logging.debug("Image description: \n%s", description)

    try:
        with open(file, "rb") as f:
            response = login.get_session().post(
                get_config().wiki.api_url,
                data={
                    "action": "upload",
                    "filename": filename,
                    "text": description,
                    "comment": "由 Vocawiki条目辅助工具 自动上传",
                    "token": login.get_csrf_token(),
                    "format": "json",
                },
                files={"file": (filename, f)},
                timeout=120,
            )
        response.raise_for_status()
        result = response.json()
    except Exception as e:
        logging.error("Upload failed: %s", e)
        return {"ok": False, "error": f"上传封面失败：{e}"}

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
        return {"ok": False, "exists": True, "error": f"封面「{filename}」已存在，未覆盖"}
    logging.warning("Upload skipped due to warnings: %s", warnings)
    return {"ok": False, "error": f"封面未上传：{warnings or upload}"}

