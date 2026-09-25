import logging
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union, Optional

import yaml
from yaml import Loader

from config.path_config import application_path, program_output_path
from i18n.i18n import _, set_language
from utils.string import is_empty


@dataclass
class WikitextConfig(yaml.YAMLObject):
    yaml_tag = u'!WikitextConfig'
    process_lyrics_jap: bool = True
    furigana_local: bool = True
    furigana_all: bool = True
    no_lyrics: bool = False
    no_hover: bool = False
    optimize_Introduction_color: bool = False
    lyrics_chs_fail_fast: bool = True
    uploader_note: bool = False
    producer_template: bool = True


@dataclass
class ColorConfig(yaml.YAMLObject):
    yaml_tag = u'!ColorConfig'
    # 可视化颜色编辑器（弹出窗口修改颜色栏参数）。
    # 取色与文字颜色（含按背景亮度自动选色）均由编辑器内部处理，
    # 不再需要 color_from_image / fg_color_threshold / senyu_mode。
    color_editor: bool = False
    # 编辑器里的「AI 参考封面生成 CSS」按钮（密钥见 wiki_credentials.yaml 的 ai_api_key）。
    # 设为 False 时整个 AI 面板不显示，便于完全断网使用。
    ai_css: bool = True


@dataclass
class ImageConfig(yaml.YAMLObject):
    yaml_tag = u'!ImageConfig'
    download_cover: bool = False
    crop: bool = True


@dataclass
class WikiConfig(yaml.YAMLObject):
    yaml_tag = u'!WikiConfig'
    api_url: str = "https://voca.wiki/api.php"
    # 生成 wikitext 后弹出提交窗口（实时预览 / 编辑 / 提交到 Vocawiki），
    # 而不是直接在 VS Code 中打开输出文件。
    # 封面图片也会在此窗口提交条目时一并上传（唯一的图片上传入口）。
    submit_window: bool = False
    # 提交时若歌曲有日文原名，额外创建指向中文条目的重定向页面
    create_redirect: bool = False


@dataclass
class Config(yaml.YAMLObject):
    yaml_tag = u'!Config'
    lang: str = 'en'
    save_to_file: str = None
    vocadb_manual: bool = False
    vocadb_manual_url: bool = False
    output_dir: str = field(default_factory=str)
    proxies: Optional[str] = None
    wikitext: WikitextConfig = field(default_factory=WikitextConfig)
    color: ColorConfig = field(default_factory=ColorConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    wiki: WikiConfig = field(default_factory=WikiConfig)


config_xxx = Config()


def is_absolute_directory(d: str) -> Optional[Path]:
    p = platform.system()
    if p == 'Windows':
        match = re.search("[A-Z]:\\\\", d)
        if match and match.start() == 0:
            return Path(d)
        return None
    # posix
    if "~" in d or d[0] == '/':
        path = Path(d)
        if "~" in d:
            return path.expanduser()
        return path
    return None


def handle_output_dir():
    global program_output_path
    if is_empty(get_config().output_dir):
        config_xxx.output_dir = "output"
    p = is_absolute_directory(get_config().output_dir)
    if p is not None:
        program_output_path = p
        logging.info(_("abs_path") + str(program_output_path.resolve()))
    else:
        program_output_path = application_path.joinpath(get_config().output_dir)
        logging.info(_("rel_path") + str(get_output_path().resolve()))
    program_output_path.mkdir(exist_ok=True, parents=True)


def load_config(filename: Union[str, Path]):
    global config_xxx
    try:
        with open(filename, mode="r", encoding="UTF-8") as f:
            config_xxx = yaml.load(f.read(), Loader=Loader)
    except Exception as e:
        logging.debug(e, exc_info=e)
        logging.warning("Cannot read config file. Falling back to default config.")
    set_language(config_xxx.lang)
    handle_output_dir()
    config_xxx.proxies = None if is_empty(config_xxx.proxies) else config_xxx.proxies


def get_config() -> Config:
    return config_xxx


def get_output_path() -> Path:
    return program_output_path


def get_resource_path(relative_path):
    """ 获取静态资源的绝对路径（兼容 PyInstaller 打包） """
    return application_path.joinpath(relative_path)


def _read_credentials() -> dict:
    """读取独立凭据文件（wiki_credentials.yaml），失败时返回空字典。"""
    credentials_path = application_path.joinpath("wiki_credentials.yaml")
    try:
        with open(credentials_path, mode="r", encoding="UTF-8") as f:
            data = yaml.load(f.read(), Loader=Loader)
    except Exception as e:
        logging.debug(e, exc_info=e)
        logging.warning("Cannot read %s. Falling back to empty credentials.", credentials_path)
        data = {}
    return data if isinstance(data, dict) else {}


def get_wiki_credentials():
    """从独立凭据文件读取 Vocawiki 登录信息（与 config.yaml 分离）。"""
    data = _read_credentials()
    return data.get("username") or "", data.get("password") or ""


def get_ai_credentials() -> dict:
    """从同一份凭据文件读取 AI 配置：provider / base_url / model / api_key / thinking。"""
    data = _read_credentials()
    return {
        "provider": data.get("ai_provider") or "openai",
        "base_url": data.get("ai_base_url") or "",
        "model": data.get("ai_model") or "",
        "api_key": data.get("ai_api_key") or "",
        "thinking": data.get("ai_thinking"),
    }