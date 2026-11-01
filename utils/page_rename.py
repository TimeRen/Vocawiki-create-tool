"""提交页改条目名：译名 + 消歧义后缀，牵动的那几处一起改（用户 2026-11-02 要求）。

工具算出来的条目名（`林鵙鹟(TakoyakiKZY)` 这种「译名(后缀)」）未必合适，用户要能在提交前改。
改名字会牵动四处：

1. Songbox 的 `|image = <条目名>.jpg`（上传名必须与它一致）；
2. `|歌曲名称 = {{lj|日文名}}<br/>译名` 里 `<br/>` **后面**那个译名；
3. 简介第一句 `《'''{{lj|日文名}}'''》（译名）` 的括号；
4. 消歧义计划：`our_title` / `base_title`，以及「要被移动的那个条目名」的前缀。

⚠️ **不动**的两处：`{{标题替换|{{lj|…}}}}` 里是**日文原名**，与译名无关；条目正文里通常也没有
指向自己的 `[[旧名]]` 自链接（用户 2026-11-02 指出）。
"""
import logging
import re
from typing import List, Optional, Tuple

TITLE_SPLIT_RE = re.compile(r"^\s*(?P<base>.*?)\s*[（(]\s*(?P<suffix>[^（）()]*?)\s*[)）]\s*$")


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


def _replace_song_name_line(text: str, old_base: str, new_base: str) -> Tuple[str, bool]:
    """`|歌曲名称 = {{lj|日文名}}<br/>旧译名` 里 `<br/>` 后面那半截 → 新译名。

    站上的写法有 `{{lj|…}}<br/>译名`、`<br>`、`<br />`，也可能不套 `{{lj}}`；
    只换**最后一行**（`<br>` 之后那段），日文原名一个字都不动。
    """
    if not old_base or old_base == new_base:
        return text, False
    pattern = re.compile(
        r"(\|\s*歌曲名称\s*=\s*[^\n]*?<br\s*/?>\s*)" + re.escape(old_base) + r"(?=\s*(?:\n|\||\}\}))")
    new_text, count = pattern.subn(lambda m: m.group(1) + new_base, text, count=1)
    return new_text, bool(count)


def _replace_intro_reading(text: str, old_base: str, new_base: str) -> Tuple[str, bool]:
    """简介第一句 `《'''{{lj|日文名}}'''》（旧译名）` 的括号 → 新译名。"""
    if not old_base or old_base == new_base:
        return text, False
    pattern = re.compile(r"(《'''[^\n]*?'''》\s*[（(])" + re.escape(old_base) + r"([)）])")
    new_text, count = pattern.subn(lambda m: m.group(1) + new_base + m.group(2), text, count=1)
    return new_text, bool(count)


def rename_wikitext(text: str, old_title: str, new_title: str,
                    old_base: Optional[str] = None,
                    new_base: Optional[str] = None) -> Tuple[str, List[str]]:
    """把条目名换掉，返回 `(新文本, 改动清单)`。

    只改与名字绑定的三处（见模块说明）：封面文件名、`|歌曲名称` 的译名、简介的括号。
    """
    old_base = split_title(old_title)[0] if old_base is None else old_base
    new_base = split_title(new_title)[0] if new_base is None else new_base
    changes: List[str] = []
    result = str(text or "")
    for label, func in (("Songbox 的 |image 文件名", _replace_image_name),
                        ("|歌曲名称 里的译名", _replace_song_name_line),
                        ("简介里的「（译名）」", _replace_intro_reading)):
        if func is _replace_image_name:
            result, changed = func(result, old_title, new_title)
        else:
            result, changed = func(result, old_base, new_base)
        if changed:
            changes.append(label)
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
