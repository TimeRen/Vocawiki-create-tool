import json
import logging
import platform
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Mapping, Union, Optional

import yaml
from yaml import Loader

from config.path_config import application_path, program_output_path
from i18n.i18n import _, set_language
from utils.string import is_empty


@dataclass
class WikitextConfig(yaml.YAMLObject):
    yaml_tag = u'!WikitextConfig'
    # 「歌词括号里的假名」→ {{photrans|漢字|かんじ}}（如「漢字(かんじ)」）。
    # 设置页里已经没有这个开关了：它现在是「歌词」页「自动识别并填入 / AI 识别并填入」的
    # 固定步骤之一（见 utils/lyrics_editor.py）。这里保留字段与默认 True，
    # 是为了让生成阶段先转一遍（终端模式不开歌词窗口也能生效），配 false 可关掉那一步。
    furigana_local: bool = True
    # AI 生成振假名：把日语歌词里**没写读音**的汉字也标上 {{photrans|漢字|かんじ}}。
    # （以前这个位置是「从 Yahoo / vocadb 等站点抓振假名」，那个一直没实现，现在换成
    # 交给大模型生成；需要 wiki_credentials.yaml 里的 ai_api_key，失败就不改歌词。）
    furigana_all: bool = False
    optimize_Introduction_color: bool = False
    # 生成「== 注释 ==」时，用 API 读模板源码判断导航框默认是展开还是折叠：
    # 默认展开的（
    #   |state = {{#ifeq:{{{1}}}|collapsed|…|mw-collapsible mw-uncollapsed}}
    # ）自动补上 |collapsed，默认已折叠的不动。
    collapse_navbox: bool = True
    lyrics_chs_fail_fast: bool = True
    uploader_note: bool = False
    # P主的大家族模板：先查 voca.wiki `Category:P主模板`（含模板重定向）建成的字典，
    # 命中就直接用；字典里没有的才逐个调 API 搜索模板分类。
    producer_template: bool = True
    # 歌词整理窗口里的「AI 识别并填入」按钮：用大模型把混在一起的歌词分成
    # 日语 / 中文 / 罗马音三栏（密钥见 wiki_credentials.yaml 的 ai_api_key）。
    # 设为 False 时界面不显示该按钮，也不会有任何联网调用（纯规则识别不受影响）。
    ai_lyrics: bool = True
    # 询问是否存在「人声本家」（同一首歌的人声演唱版本）：
    # 回答「是」后要求给出它的 niconico / YouTube 链接与 bilibili 链接，
    # 简介末尾追加「另有P主本人演唱的人声本家。」，
    # 「== 歌曲 ==」小节里按版本分块（;VOCALOID本家 / ;人声本家）。
    # 参 voca.wiki 条目 如月车站、红色房间。
    human_original: bool = False
    # 询问是否把「同一首歌的其他版本」也写进条目（参 voca.wiki《鸟之诗》）：
    # 候选列表从 VocaDB 的 alternateVersions 拿，用户逐条选；选中的版本要给出 B 站链接
    # 并回答「是否官方投稿」，然后整页改用 {{tabs}} 按版本切换，
    # 「== 歌曲 ==」一节里按 `;版本名` 列出各版本的 B 站播放器。
    other_versions: bool = True


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
    # AI 生成 CSS 时会预填到「补充要求」里的默认提示词，按编辑器 Tab 分三栏；
    # 留空则不预填，界面上仍可随时改写。
    ai_prompt_songbox: str = ""
    ai_prompt_intro: str = ""
    ai_prompt_lyrics: str = ""


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
    # 同名条目（消歧义）处理：译名与 Vocawiki 上已有条目重名时，
    # 上传用的条目名 / 封面文件名改成「歌名(P主名)」（日文 P主名取 vocadb 的罗马音），
    # 条目顶部自动加 {{About}}（共 2 个）或 {{Otheruseslist}}（3 个以上），
    # 提交时再按情况修订 / 创建消歧义页并修正链入页面。
    disambiguate: bool = True


@dataclass
class Config(yaml.YAMLObject):
    yaml_tag = u'!Config'
    lang: str = 'en'
    save_to_file: str = None
    vocadb_manual: bool = False
    vocadb_manual_url: bool = False
    output_dir: str = field(default_factory=str)
    proxies: Optional[str] = None
    # 点「清除对话记录」时要不要先弹窗问一句（false = 直接清，不再确认）
    confirm_clear_history: bool = True
    # 窗口变大/变小时，字号要不要跟着缩放（false = 一直用基准字号）
    font_scale_with_window: bool = True
    # 界面字体（在「设置」页点字体栏选一个字体**文件**，留空 = 默认的 Segoe UI + 中文字体回退）。
    # font_file 是文件路径（下次启动照它重新加载，字体没装进系统也能用），
    # font_family 是那个文件里的家族名（也是手写 config.yaml 时的老写法）。
    font_family: str = ""
    font_file: str = ""
    wikitext: WikitextConfig = field(default_factory=WikitextConfig)
    color: ColorConfig = field(default_factory=ColorConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    wiki: WikiConfig = field(default_factory=WikiConfig)

    @classmethod
    def from_yaml(cls, loader, node):
        """读 YAML 时把文件里**没写**的项按 dataclass 默认值补上。

        PyYAML 的 `YAMLObject` 只把文件里出现的键塞进实例（缺的键连属性都没有），
        所以老配置少写一行 `output_dir` 就会在 `handle_output_dir()` 里报
        `'Config' object has no attribute 'output_dir'`、程序直接起不来。
        """
        data = loader.construct_mapping(node, deep=True)
        known = {item.name for item in fields(cls)}
        obj = cls(**{key: value for key, value in data.items() if key in known})
        for key, value in data.items():             # 不认识的键也留着，别把老文件里的东西弄丢
            if key not in known:
                setattr(obj, key, value)
        return obj


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


def _warn_about_duplicate_keys(text: str) -> None:
    """顶格的键写了两遍就提醒一句：YAML 只认**最后一个**，很容易让设置「莫名失效」。

    旧版 `save_config_values` 把顶格行一律当成「新的一节」，于是每保存一次就在文件末尾
    再追加一份（2026-09 用户的 config.yaml 就是这样长出一堆重复键的：后面那份
    `font_file: ""` 把前面选好的字体顶掉了）。写回那边已经改成就地改值，这里只是替老文件报个信。
    """
    seen, duplicated = set(), set()
    for line in str(text or "").splitlines():
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:", line)
        if not match:
            continue
        key = match.group(1)
        if key in seen:
            duplicated.add(key)
        seen.add(key)
    if duplicated:
        logging.warning("config.yaml 里有重复的键：%s（YAML 只取最后一个，"
                        "设置可能没生效；在「设置」页点一次「保存」就会统一，"
                        "或者照着 config_simple.yaml 重新写一份）", "、".join(sorted(duplicated)))


def load_config(filename: Union[str, Path]):
    global config_xxx
    try:
        with open(filename, mode="r", encoding="UTF-8") as f:
            text = f.read()
        _warn_about_duplicate_keys(text)
        config_xxx = yaml.load(text, Loader=Loader)
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


def credentials_path() -> Path:
    """凭据文件路径（程序目录下的 wiki_credentials.yaml）。"""
    return application_path.joinpath("wiki_credentials.yaml")


def config_path() -> Path:
    """主配置文件路径（程序目录下的 config.yaml）。"""
    return application_path.joinpath("config.yaml")


def _yaml_scalar(value: Any) -> str:
    """把单个值写成 YAML 标量（字符串统一用 JSON 双引号写法，YAML 直接读）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return '\"\"'
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


# 顶格「键:」= 一个配置节（wikitext / color / image / wiki），缩进的「键:」= 该节的项
_SECTION_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:")
_ITEM_RE = re.compile(r"^(?P<indent>\s+)(?P<key>[A-Za-z_][A-Za-z0-9_-]*)\s*:")


def save_config_values(values: Mapping[str, Any]) -> bool:
    """把若干项配置写回 config.yaml，尽量保留文件里的注释与其它内容。

    values 的键是「节.项」（如 `wikitext.producer_template` / `wiki.api_url`），
    顶格项直接写键名（如 `lang`）。文件里已有的键就地换值（同一键出现多次就**一起换**），
    没有的补在该节末尾。只改值不改结构。
    """
    path = config_path()
    try:
        text = path.read_text(encoding="UTF-8") if path.exists() else ""
    except OSError as e:
        logging.error("无法读取配置文件 %s：%s", path, e)
        return False
    lines = text.splitlines() or ["--- !Config"]

    pending: Dict[tuple, Any] = {}
    for key, value in values.items():
        section, _, name = str(key).rpartition(".")
        pending[(section or None, name)] = value

    written = set()
    out = []
    section = None
    section_end: Dict[Optional[str], int] = {None: 0}
    for line in lines:
        top = _SECTION_RE.match(line)
        if top:
            name = top.group("key")
            wanted = (None, name)
            if wanted in pending:
                # 顶格项就是我们要写的那一项：**就地换值**，别把它当成「新的一节」。
                # 2026-09 踩过：旧写法把顶格行一律当节头，pending 里的值永远配不上，
                # 只好在文件末尾再追加一份 → 每存一次多一份重复键（用户 dist 里的
                # config.yaml 就是这么被撑起来的）；而 YAML 取**最后**一个，
                # 后来追加的那份（值可能是空的）会把前面的设置顶掉，字体变回默认就是这么来的。
                out.append(f"{name}: {_yaml_scalar(pending[wanted])}")
                written.add(wanted)
                section = None
                section_end[None] = len(out)
                continue
            section = name
            section_end.setdefault(section, len(out) + 1)
            out.append(line)
            continue
        item = _ITEM_RE.match(line) if section else None
        if item:
            wanted = (section, item.group("key"))
            if wanted in pending:
                out.append(f"{item.group('indent')}{item.group('key')}: "
                           f"{_yaml_scalar(pending[wanted])}")
                written.add(wanted)
            else:
                out.append(line)
            section_end[section] = len(out)
            continue
        if section is not None and line.strip() and not line.startswith(" "):
            section = None                    # 离开上一节
        out.append(line)
        section_end[section] = len(out)

    # 文件里没有的项：补在它所在节的末尾（同一节里的多次插入从后往前，避免下标错位）
    missing = {key: value for key, value in pending.items() if key not in written}
    for (sec, name), value in sorted(missing.items(), key=lambda kv: -section_end.get(kv[0][0], 0)):
        line = f"{'  ' if sec else ''}{name}: {_yaml_scalar(value)}"
        out.insert(section_end.get(sec, len(out)), line)

    try:
        path.write_text("\n".join(out) + "\n", encoding="UTF-8")
        return True
    except OSError as e:
        logging.error("无法写入配置文件 %s：%s", path, e)
        return False


def _patch_credential_line(key: str, raw: str) -> bool:
    """把 `key: <raw>` 这行写进凭据文件（已有就换那一行，没有就追加）。raw 已是 YAML 文本。"""
    path = credentials_path()
    try:
        text = path.read_text(encoding="UTF-8") if path.exists() else ""
        line = f"{key}: {raw}"
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:.*$")
        if pattern.search(text):
            text = pattern.sub(lambda _match: line, text)
        else:
            text = text.rstrip() + "\n" + line + "\n"
        path.write_text(text, encoding="UTF-8")
        return True
    except OSError as e:
        logging.error("无法写入凭据文件 %s：%s", path, e)
        return False


def save_credential(key: str, value: str) -> bool:
    """把单个字符串凭据写回 wiki_credentials.yaml（保留注释与其它字段）。

    键已存在则只替换那一行（用 json 字符串写法，YAML 可直接读），否则追加到文件末尾。
    界面上的「API 密钥」输入框走的就是这里（现在是「设置」页）。
    """
    return _patch_credential_line(key, json.dumps(value or "", ensure_ascii=False))


def save_credentials(values: Mapping[str, Any]) -> bool:
    """批量写凭据（用户名 / 密码 / AI 配置）；字符串加引号，布尔值写成裸 true/false。"""
    ok = True
    for key, value in values.items():
        raw = ("true" if value else "false") if isinstance(value, bool) else \
            json.dumps(str(value or ""), ensure_ascii=False)
        if not _patch_credential_line(str(key), raw):
            ok = False
    return ok