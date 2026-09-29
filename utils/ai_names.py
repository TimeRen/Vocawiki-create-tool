"""AI 猜中文歌名：给「曲目」页的「AI填充中文名」按钮用。

为什么单独一条路：VocaDB 没有中文名（`lang` 传 Chinese 直接 400），P主条目里的
`{{Producer_Song|条目=…}}` 字典也只覆盖一部分，「从外部链接获取中文名」在 b 站 / 网易云
搜不到的就更少了。剩下的交给大模型猜 —— 但**猜出来的名字要人工复检**：
界面上每填一个都会弹窗（`utils/ui/producer_panel.py` 的 `_review_ai_names()`），
用户可以采用 / 改字 / 跳过，所以这里的定位是「给个靠谱的候选」，不是直接写进条目。

硬性要求（都写进提示词里，见 `SYSTEM_PROMPT`）：

* 只在**确实知道**这首歌在中文圈叫什么时才填（要的是「别人已经这么叫」的名字，
  不是模型自己的直译）；不确定就返回空字符串，宁可空着；
* 名字要短、不带假名、不带 `feat.` / 版本说明 / 书名号等装饰（`clean_names()` 再兜一遍）。

请求构造与密钥走 `utils/ai_css.py` 那一套（provider / base_url / model / 代理），
所以配置方式与「AI 配色」「AI 识别歌词」完全一样：`wiki_credentials.yaml` 里的 `ai_api_key`。
是否显示这两个按钮由 config.yaml 的 `wikitext.producer_names` 决定。
"""
import json
import logging
import re
from typing import Dict, List, Optional, Sequence

from utils import ai_css
from utils import producer_template as pt

# 一批问 20 首：再多容易漏项，再少请求次数太多（一趟 20 首实测几秒）
BATCH_SIZE = 20
MAX_TOKENS = 4096
TEMPERATURE = 0.3                # 起名要稳，不要发挥

SYSTEM_PROMPT = """\
你是中文圈（voca.wiki / bilibili / 网易云）VOCALOID、唱见歌曲的名字整理助手。
用户会给你一个 P主 和一批日文曲名。请给出这些歌在中文圈**通用的中文标题**。

硬性规则：
1. 只输出一个 JSON 对象：键是原样的日文曲名，值是中文标题。不要输出解释或 Markdown。
2. 只在**确实知道**这首歌的中文名时才填（例如 ラグタイムレコード → 时滞记录）。
   不确定、没见过这首歌 → 值写空字符串 ""。**不要直译、不要音译、不要编造。**
3. 值必须是**别人已经这么叫**的标题，不是你的翻译：不要加「之歌」「的…」这类缀词，
   不要带「【】」「《》」、说明文字、罗马音或日文。
4. 中文标题里不要出现假名，也不要带 feat. / 版本说明（如「(VOCALOID ver.)」）。
5. 一般 2~10 个字，最多 20 字；同一批里不要给两首歌同一个名字。
6. 拿不准就留空 —— 留空不影响任何东西（用户会自己填），填错却会把模板里的链接带歪。
"""


def batches(songs: Sequence[dict], size: int = BATCH_SIZE) -> List[List[dict]]:
    """把曲目切成一批一批（一次请求一批）。"""
    return [list(songs[start:start + size]) for start in range(0, len(songs), size)]


def build_prompt(songs: Sequence[dict], artist: str = "") -> str:
    """拼给模型的文字要求（`songs` 是 `[{'ja': 日文原名, 'date': 投稿日期}]`）。"""
    lines = [f"P主：{artist or '（未提供）'}", "曲目（日文原名｜投稿日期）："]
    for index, song in enumerate(songs, start=1):
        lines.append(f"{index}. {song.get('ja', '')}｜{song.get('date') or '（日期未知）'}")
    first = songs[0].get("ja", "") if songs else "日文曲名"
    lines.append("")
    lines.append(f'只输出 JSON，例如：{{"{first}": "中文标题"}}，'
                 "不知道的歌值写空字符串。")
    return "\n".join(lines)


def clean_names(parsed: dict, songs: Sequence[dict]) -> Dict[str, str]:
    """把模型回复的 JSON 收成 `{日文原名: 中文名}`（只认问过的歌，顺带清装饰）。"""
    wanted = {}
    for song in songs:
        ja = str(song.get("ja") or "").strip()
        if ja:
            wanted[_norm(ja)] = ja
    names: Dict[str, str] = {}
    for key, value in (parsed or {}).items():
        ja = wanted.get(_norm(key))
        if not ja or not isinstance(value, (str, int, float)):
            continue
        name = pt.clean_name(str(value))
        if name and pt.is_chinese_name(name) and name != ja:
            names[ja] = name
    return names


def _norm(text) -> str:
    """键的宽松比对：去空白与标点、大小写归一（模型常把 `・` / 空格写歪）。

    中点 `・`（U+30FB）落在片假名区里，得单独排掉，否则 `ラグ タイム・レコード`
    与 `ラグタイムレコード` 对不上。
    """
    return re.sub(r"[^0-9a-z\u3040-\u30fa\u30fc-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", "",
                  str(text or "").lower())


def suggest_names(payload_json: str, progress: Optional[object] = None) -> Dict[str, object]:
    """问模型一批曲目的中文名 → `{'ok', 'names', 'model', 'warning'}`。

    payload（JSON 字符串）：`{'artist': P主名, 'songs': [{'ja': …, 'date': …}]}`。
    `progress` 是逐批回调（验收字符串，界面上显示在状态行）。
    返回的 `names` 只是**候选**，界面会逐个弹窗让用户复检。
    """
    try:
        payload = json.loads(payload_json or "{}")
    except ValueError:
        return {"ok": False, "error": "参数不是合法 JSON"}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "参数格式不正确"}
    songs = [item for item in (payload.get("songs") or [])
             if isinstance(item, dict) and str(item.get("ja") or "").strip()]
    if not songs:
        return {"ok": False, "error": "没有需要起名的曲目"}

    cfg = ai_css.settings()
    if not cfg["api_key"]:
        return {"ok": False, "error": "未配置 ai_api_key（见 wiki_credentials.yaml）"}

    artist = str(payload.get("artist") or "")
    groups = batches(songs)
    names: Dict[str, str] = {}
    warning = ""
    for index, group in enumerate(groups, start=1):
        if progress is not None:
            progress(f"（第 {index}/{len(groups)} 批，{len(group)} 首）问模型…")
        url, headers, body = ai_css.build_request(cfg, build_prompt(group, artist), None,
                                                  system=SYSTEM_PROMPT, max_tokens=MAX_TOKENS,
                                                  temperature=TEMPERATURE)
        data, error = ai_css._post(url, headers, body)
        if data is None:
            logging.warning("AI 起名第 %s 批失败：%s", index, error)
            warning = str(error)
            break
        parsed = ai_css.extract_json(ai_css._reply_text(cfg, data)) or {}
        if not parsed:
            warning = "模型没有返回 JSON"
            logging.warning("AI 起名第 %s 批没返回 JSON：%s", index,
                            ai_css._reply_text(cfg, data)[:120])
            continue
        names.update(clean_names(parsed, group))
    if not names and warning:
        return {"ok": False, "error": warning, "names": {}}
    result: Dict[str, object] = {"ok": True, "names": names, "model": cfg["model"],
                                 "checked": len(songs)}
    if warning:
        result["warning"] = warning
    return result


__all__ = ["SYSTEM_PROMPT", "BATCH_SIZE", "batches", "build_prompt", "clean_names",
           "suggest_names"]
