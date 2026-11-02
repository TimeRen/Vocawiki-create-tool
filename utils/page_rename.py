"""提交页改条目名：译名 + 消歧义后缀，牵动的那几处一起改（用户 2026-11-02 要求）。

工具算出来的条目名（`林鵙鹟(TakoyakiKZY)` 这种「译名(后缀)」）未必合适，用户要能在提交前改。
改名字会牵动四处：

1. Songbox 的 `|image = <条目名>.jpg`（上传名必须与它一致，输出目录里那张实体封面也跟着改）；
2. `|歌曲名称 = {{lj|日文名}}<br/>译名` 里的**译名**那一截；没有 `<br/>`（这话本来就没有译名，
   比如《EDEN》《404》）时就直接把那一截名字换掉，**不补**新的译名；
3. 简介第一句 `《'''{{lj|日文名}}'''》（译名）` 的括号；没有括号时同样只换掉歌名那一截；
4. 消歧义计划：`our_title` / `base_title`，以及「要被移动的那个条目名」的前缀。

⚠️ **不动**的两处：`{{标题替换|{{lj|…}}}}` 里是**日文原名**，与译名无关；条目正文里通常也没有
指向自己的 `[[旧名]]` 自链接（用户 2026-11-02 指出）。
"""
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from utils import string

TITLE_SPLIT_RE = re.compile(r"^\s*(?P<base>.*?)\s*[（(]\s*(?P<suffix>[^（）()]*?)\s*[)）]\s*$")
# `|歌曲名称` 里分隔多个名字的 `<br/>`（`<br>` / `<br />` 也认）
NAME_SEPARATOR_RE = re.compile(r"(<\s*br\s*/?\s*>)", re.IGNORECASE)


def split_title(title: str) -> Tuple[str, str]:
    """`林鵙鹟(TakoyakiKZY)` → `("林鵙鹟", "TakoyakiKZY")`；没有括号时后缀是空串。

    全角括号（`林鵙鹟（TakoyakiKZY）`）也认 —— 站上两种写法都有。
    """
    text = str(title or "").strip()
    match = TITLE_SPLIT_RE.match(text)
    if not match or not match.group("base"):
        return text, ""
    return match.group("base").strip(), match.group("suffix").strip()


def join_title(base: str, suffix: str = "") -> str:
    """反过来：`("林鵙鹟", "TakoyakiKZY")` → `林鵙鹟(TakoyakiKZY)`；后缀为空就不带括号。"""
    base = str(base or "").strip()
    suffix = str(suffix or "").strip()
    return f"{base}({suffix})" if suffix else base


def _replace_image_name(text: str, old_title: str, new_title: str) -> Tuple[str, bool]:
    """`|image = <旧条目名>.jpg` → 新条目名（上传名与它必须一致）。"""
    if not old_title:
        return text, False
    pattern = re.compile(r"(\|\s*image\s*=\s*)" + re.escape(old_title) + r"(\.(?:jpg|jpeg|png))",
                         re.IGNORECASE)
    new_text, count = pattern.subn(lambda m: m.group(1) + new_title + m.group(2), text)
    return new_text, bool(count)


def _unwrap_lj(segment: str) -> str:
    """`{{lj|720°}}` → `720°`；不是这个写法就原样返回（去空白）。"""
    match = re.fullmatch(r"\s*\{\{\s*lj\s*\|\s*(.*?)\s*\}\}\s*", segment or "", re.DOTALL)
    return (match.group(1) if match else str(segment or "")).strip()


def _replace_song_name_line(text: str, old_base: str, new_base: str) -> Tuple[str, bool]:
    """`|歌曲名称` 那一行里的旧名字 → 新名字。

    这一行可以有多截名字（`{{lj|日文名}}<br/>译名<br/>别名`，见 `main.get_song_names()`）：
    * 有译名时（`<br/>` 后面那截与旧译名相等），只换那一截，日文原名与别名一个字都不动；
    * 后面几截都对不上、只有**第一截**（日文原名）等于旧名字时，说明这首歌本来没有单独的
      译名、条目名就是那截 —— 按用户 2026-11-02 的要求**直接换掉**它，
      并用 `string.auto_lj()` 决定新名字还要不要套 `{{lj}}`；**不新增** `<br/>`
      （没有译名通常是不需要翻译，不是漏了翻译，参《EDEN》《404》）。
    """
    if not old_base or old_base == new_base:
        return text, False
    pattern = re.compile(r"^([ \t]*\|[ \t]*歌曲名称[ \t]*=[ \t]*)([^\n]*)$", re.MULTILINE)
    hit = False

    def replace(match: "re.Match") -> str:
        nonlocal hit
        prefix, value = match.group(1), match.group(2)
        parts = NAME_SEPARATOR_RE.split(value)          # [名字, 分隔符, 名字, …]
        names = [index for index in range(0, len(parts), 2)]
        matched = [index for index in names[1:] if _unwrap_lj(parts[index]) == old_base]
        if matched:
            parts[matched[-1]] = new_base               # 译名 / 别名那一截：纯文本
        elif _unwrap_lj(parts[names[0]]) == old_base:
            parts[names[0]] = string.auto_lj(new_base)  # 只有日文原名那一截：就是条目名
        else:
            return match.group(0)
        hit = True
        return prefix + "".join(parts)

    result = pattern.sub(replace, text, count=1)
    return result, hit


def _replace_intro_reading(text: str, old_base: str, new_base: str) -> Tuple[str, bool]:
    """简介第一句 `《'''{{lj|日文名}}'''》（旧译名）` 的括号 → 新译名。"""
    if not old_base or old_base == new_base:
        return text, False
    pattern = re.compile(r"(《'''[^\n]*?'''》\s*[（(])" + re.escape(old_base) + r"([)）])")
    new_text, count = pattern.subn(lambda m: m.group(1) + new_base + m.group(2), text, count=1)
    return new_text, bool(count)


def _replace_intro_name(text: str, old_base: str, new_base: str) -> Tuple[str, bool]:
    """简介里没有「（译名）」时，直接把 `《'''旧名'''》` 那一截换成新名字。

    这类条目本来就没有译名（`main.intro_sentence()` 在译名 == 原名时**不写**括号），
    所以不能补一个括号进去 —— 用户 2026-11-02：「不是补充新译名，而是直接修改旧译名」。
    后面紧跟着 `（…）` 的不算（那是「日文原名（译名）」的写法，歌名该由
    `_replace_intro_reading()` 去动括号，`《…》` 里是日文原名，一个字都不改）。
    """
    if not old_base or old_base == new_base:
        return text, False
    pattern = re.compile(r"(《''')([^\n]*?)('''》)(?!\s*[（(])")
    hit = False

    def replace(match: "re.Match") -> str:
        nonlocal hit
        if _unwrap_lj(match.group(2)) != old_base:
            return match.group(0)
        hit = True
        return match.group(1) + string.auto_lj(new_base) + match.group(3)

    result = pattern.sub(replace, text, count=1)
    return result, hit


def rename_wikitext(text: str, old_title: str, new_title: str,
                    old_base: Optional[str] = None,
                    new_base: Optional[str] = None) -> Tuple[str, List[str]]:
    """把条目名换掉，返回 `(新文本, 改动清单)`。

    只改与名字绑定的三处（见模块说明）：封面文件名、`|歌曲名称` 的名字、简介的名字。
    """
    old_base = split_title(old_title)[0] if old_base is None else old_base
    new_base = split_title(new_title)[0] if new_base is None else new_base
    changes: List[str] = []
    result = str(text or "")
    result, changed = _replace_image_name(result, old_title, new_title)
    if changed:
        changes.append("Songbox 的 |image 文件名")
    result, changed = _replace_song_name_line(result, old_base, new_base)
    if changed:
        changes.append("|歌曲名称 里的名字")
    result, changed = _replace_intro_reading(result, old_base, new_base)
    if changed:
        changes.append("简介里的「（译名）」")
    else:
        # 没有「（译名）」：这首歌本来就没译名，直接把歌名那一截改掉
        result, changed = _replace_intro_name(result, old_base, new_base)
        if changed:
            changes.append("简介里的歌名")
    if not changes and old_title != new_title:
        logging.info("改条目名：%s 在 wikitext 里没有出现（只改标题与模板链接）", old_title)
    return result, changes


def rename_plan(plan, old_title: str, new_title: str, new_base: Optional[str] = None):
    """消歧义计划跟着改名字：`our_title` / `base_title` / 被移动那个条目的前缀。

    被移动的是**别人**的条目（`林鵙鹟(nanahoshi管弦乐团)`）：前缀换成新译名，括号里那位 P主
    原样保留 —— 用户改自己的后缀（`(TakoyakiKZY)`）不该动到别人的名字。
    """
    if plan is None:
        return None
    old_base = split_title(old_title)[0]
    new_base = split_title(new_title)[0] if new_base is None else new_base
    plan.our_title = new_title
    plan.base_title = new_base
    for other in getattr(plan, "others", None) or []:
        suffix = split_title(other.title)[1]
        renamed = join_title(new_base, suffix) if suffix else new_base
        if getattr(other, "line", ""):
            other.line = str(other.line).replace(old_base, new_base)
        other.title = renamed
    note = getattr(plan, "note", "")
    if note and old_base and new_base:
        plan.note = str(note).replace(old_base, new_base)
    return plan


def rename_cover(cover, new_title: str):
    """封面上传名跟着条目名走（Songbox 的 `|image` 与它必须一致）。"""
    if cover is None:
        return None
    cover.wiki_name = f"{new_title}.jpg"
    return cover


def rename_cover_file(cover, old_title: str, new_title: str) -> Optional[Path]:
    """输出目录里那张**实体封面文件**也改名（`720°封面.jpg` → `七二零度封面.jpg`）。

    用户 2026-11-02 要求「图片名也一起改」：只改 wikitext 的 `|image` 与上传名的话，
    本地文件还叫旧名字，提交页显示的路径、`output/` 里翻出来的图都对不上条目。
    只换名字里的**旧译名**那一截（文件名是 `safe_filename(译名) + "封面.jpg"`，见
    `vocadb.get_song_by_name()`）；文件不存在 / 目标已存在 / 改名失败都原样不动，
    返回新路径（没改就返回 None）。
    """
    if cover is None:
        return None
    path = Path(getattr(cover, "path", "") or "")
    if not str(path) or not path.name:
        return None
    old_part = string.safe_filename(split_title(old_title)[0])
    new_part = string.safe_filename(split_title(new_title)[0])
    if not old_part or old_part == new_part or old_part not in path.name:
        return None
    target = path.with_name(path.name.replace(old_part, new_part, 1))
    if target == path or target.exists() or not path.exists():
        return None
    try:
        path.rename(target)
    except OSError as e:                            # noqa: BLE001 - 盘上改不动只是不方便
        logging.warning("封面文件改名失败（%s → %s）：%s", path.name, target.name, e)
        return None
    cover.path = target
    return target
