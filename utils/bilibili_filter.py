"""b站搜索结果的净化：强相关过滤 + 黑名单（移植自 bilibili-search-purify）。

工具通过 `api.bilibili.com/x/web-interface/search/type` 搜视频，接口按「相关度」给前若干条，
但算法常把**毫不相关**的擦边球 / 标题党 / 营销号视频塞进结果（搜歌名时尤其明显）。
这里的规则照 [houliabc/bilibili-search-purify](https://github.com/houliabc/bilibili-search-purify)
（油猴脚本，MIT）搬过来，只保留对**接口返回的条目**有用的部分：

  * `is_relevant()`：标题里没有搜索词的任何片段 → 不相关（脚本的 STRICT_MATCH_MODE）；
  * 黑名单：擦边 / 标题党 / 烂梗 / 引流诈骗 / 迷信 / 血腥违法 的正则表 + UP 主黑名单；
  * `purify_items()`：一条都不剩时**整批保留**（脚本的「白屏兜底」，宁可脏也不能没有）。

与脚本的差别（工具这边是**找某一首歌的稿件**，不是给人刷搜索页）：
  * 脚本里那些会误伤 VOCALOID 曲名的词（`传奇`、`顶级`、`必看`、`深夜`…）没有收进来；
  * 内置词表只在候选评分里**降权**（见 `models/video.py`），直接丢掉只在
    「还有干净候选」或「用户自己写的屏蔽词」时才做。

----------------------------------------------------------------------------
bilibili-search-purify 的部分规则（黑名单词表与相关性判定）移植自
https://github.com/houliabc/bilibili-search-purify
Copyright (c) 2026 厚礼，MIT License：

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
----------------------------------------------------------------------------
"""
import logging
import re
from typing import List, Optional, Sequence, Tuple

# 搜索词的停用词：匹配前统一剔除，免得「的 / 了 / 是怎么」这类助词把整词匹配打断
STOP_WORDS_RE = re.compile(
    r"的|了|也|都|是|在|与|和|或|并|而|及|以及|怎么|如何|为什么|什么|居然|竟然|原来是|居然是|啊|呀|吧|呢|吗")
# 标点 / 空白 / 装饰符号：`【初音ミク】ハナビ` 与 `初音未来 ハナビ` 要能对上
PUNCT_RE = re.compile(
    r"[\s\u3000,，.。!！?？、；：:;\"'“”‘’()（）【】\[\]{}<>《》+\-—_/\\|~`@#$%^&*=×·・〜～*＊]+")
# 一个搜索词里至少要有这么多个字才拿来当「片段」比对（脚本的 `slice(i, i + 2)`）
FRAGMENT_SIZE = 2
# 接口返回的标题带 `<em class="keyword">` 高亮标记，比对前按可见文字去掉（脚本取的是 DOM 文字）
TAG_RE = re.compile(r"<[^<>\n]{0,80}>")

# 内置黑名单：照脚本的分类搬，去掉会误伤曲名的词（见模块说明）。
# 分成几组是为了日志 / 惩罚能分开看，命中任意一组都算。
BUILTIN_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("擦边", (
        "姿势", r"坚持\d+分钟", "好色", "擦边", "福利", "丝袜", "黑丝", "白丝", "性感", "走光",
        "露底", "诱惑", "大长腿", "腿控", "足控", "烧鸡", "懂的都懂", "老司机", "车牌", "福利姬",
        "女友", "绿茶", "肉丝", "网袜", "蕾丝", "深V", "事业线", "真空", "凸点", "私拍", "挑逗",
        "调情", "情趣", "内衣", "泳装", "比基尼", "火辣", "暴露", "清凉", "撩人", "魅惑", "玉足",
        "软色情", "擦边球", "打擦边", "私密", "暧昧", "技师",
    )),
    ("标题党", (
        "震惊", "颠覆认知", "离谱", "破防", "绝绝子", "封神", "吓尿", "看呆", "看傻", "千万不要",
        "删前速看", "全网最", "史诗级", "吊打", "秒杀", "惊呆", "毁三观", "避雷", "这谁顶得住",
        "谁能拒绝", "谁懂啊", "家人们", "血压", "炸了", "绝了", "yyds", "震碎三观", "大开眼界",
        "万万没想到", "你敢信", "破大防", "蚌埠住了", "无语死了", "杀疯了", "卷疯了", "赢麻了",
        "输麻了", "碾压", "史上最强", "全网第一", "刷新认知", "不敢相信", "难以置信", "重磅",
        "炸锅", "沸腾", "笑不活了", "笑死人", "社会性死亡", "离谱到家", r"99%的人不知道",
    )),
    ("梗", (
        "少爷梦", "大型纪录片", "不要笑", "人类早期", "珍贵影像", "补档", "防失联", "某音", "某手",
        "纯爱战士", "牛头人", "注意看", "这个男人叫小帅", "这个女人叫小美", "佛波勒", "退退退",
        "挖呀挖", "听我说谢谢你", "奥利给", r"双击\d+", "老铁", "集美", "咱就是说", "一整个",
        "尊嘟假嘟", "泰裤辣", "耶斯莫拉", "我太难了", "摆烂", "躺平", "打工人", "搬砖", "摸鱼",
        "划水", "没毛病", "干就完了",
    )),
    ("辱骂", (
        "脑残", "智障", "废物", "人渣", "败类", "杂种", "狗东西", "王八蛋", "兔崽子", "贱人",
        "去死", "该死", "菜鸡", "辣鸡", "弱鸡", "屌丝", "网暴", "人肉搜索",
    )),
    ("引流", (
        "免费送", "点赞领", "关注领", "评论领", "私信领", "加微信", r"加v", "扣扣", r"qq",
        "联系方式", "私下", "私聊", "私我", "加好友", "扫码", "二维码", "小黄车", "橱窗",
        "下单", "带货", "直播带货", "赚大钱", "暴富", "发财", "兼职", "日结", "在家赚钱",
        "月入过万", "轻松赚钱", "零成本", "无门槛", "创业", "加盟", "代理", "微商", "刷单",
        "跑分", "赌博", "博彩", "彩票", "赌球", "下注", "回本", "上岸", "网贷", "贷款",
        "高利贷", "套路贷", "校园贷", "私下转账", "线下交易", "进群",
    )),
    ("迷信", (
        "算命", "算卦", "风水", "占卜", "八字", "招财", "旺运", "辟邪", "镇宅", "转运",
        "改运势", "求姻缘", "化解小人", "逢凶化吉", "根治", "百分百见效", "无效退款",
        "终身保修", "零风险", "包治百病",
    )),
    ("违法", (
        "血腥", "暴力", "打人", "打架", "自杀", "自残", "跳楼", "割腕", "上吊", "毒品",
        "吸毒", "贩毒", "枪支", "管制刀具", "炸药",
    )),
)

# 编译好的内置表：`(组名, 正则)`
BUILTIN_WORDS: Tuple[Tuple[str, "re.Pattern"], ...] = tuple(
    (group, re.compile("|".join(patterns), re.IGNORECASE))
    for group, patterns in BUILTIN_GROUPS)


def clean_text(text: object) -> str:
    """清洗文本：去 HTML 标记、去停用词、去标点与空白、转小写（脚本的 `cleanText`）。"""
    value = TAG_RE.sub("", str(text or "")).lower()
    return PUNCT_RE.sub("", STOP_WORDS_RE.sub("", value)).strip()


def _fragments(text: str) -> List[str]:
    """文本里所有连续的 `FRAGMENT_SIZE` 字片段。"""
    if len(text) < FRAGMENT_SIZE:
        return []
    return [text[index:index + FRAGMENT_SIZE]
            for index in range(len(text) - FRAGMENT_SIZE + 1)]


def is_cjk(text: str) -> bool:
    """整串都是中日文（不含拉丁字母 / 数字）：只有这种搜索词才按 2 字片段兜底。"""
    return bool(text) and not re.search(r"[0-9a-z]", text)


def keywords_of(keyword: object) -> List[str]:
    """搜索词 → 清洗过的 token 列表（`"初音ミク ハナビ"` → `["初音ミク", "ハナビ"]`）。"""
    raw = str(keyword or "")
    parts = [clean_text(piece) for piece in re.split(r"[\s\u3000]+", raw.strip()) if piece.strip()]
    parts = [part for part in parts if part]
    whole = clean_text(raw)
    ordered = [whole] if whole else []
    return list(dict.fromkeys([*ordered, *parts]))


def is_relevant(title: object, keyword: object) -> bool:
    """标题里有没有搜索词的任何片段（脚本的 `isTitleRelevant`，放宽了拉丁词的处理）。

    1. 清洗后标题含清洗后**整词** → 相关；
    2. 搜索词按空白切出的 token（清洗后 ≥2 字）出现在标题里 → 相关；
    3. 中日文搜索词再按**连续 2 字片段**兜一层（`打ち上げ花火` 因此能命中标题里的 `花火`）；
    4. 都不命中 → 不相关。搜索词为空一律算相关（没有可比的东西）。
    """
    clean_title = clean_text(title)
    tokens = keywords_of(keyword)
    if not tokens:
        return True
    clean_keyword = clean_text(keyword)
    if clean_keyword and clean_keyword in clean_title:
        return True
    for token in tokens:
        if len(token) >= FRAGMENT_SIZE and token in clean_title:
            return True
        if is_cjk(token):
            if any(fragment in clean_title for fragment in _fragments(token)):
                return True
    return False


def _user_words(raw: object) -> List["re.Pattern"]:
    """用户自己填的屏蔽词 → 正则列表（`词` 当普通子串，`/…/` 当正则）。"""
    patterns: List["re.Pattern"] = []
    for piece in re.split(r"[,，、;；\n]+", str(raw or "")):
        word = piece.strip()
        if not word:
            continue
        try:
            if len(word) > 2 and word.startswith("/") and word.endswith("/"):
                patterns.append(re.compile(word[1:-1], re.IGNORECASE))
            else:
                patterns.append(re.compile(re.escape(word), re.IGNORECASE))
        except re.error as e:  # noqa: BLE001 - 写错的正则就别用它，别把搜索带崩
            logging.warning("屏蔽词「%s」不是合法正则，已忽略：%s", word, e)
    return patterns


def extra_blacklist() -> List["re.Pattern"]:
    """配置里 `bilibili_blacklist` 那串自己追加的屏蔽词。"""
    from config.config import get_config

    return _user_words(getattr(get_config(), "bilibili_blacklist", ""))


def extra_blacklist_up() -> List[str]:
    """配置里 `bilibili_blacklist_up` 那串自己屏蔽的 UP 主（小写、去空白后比对）。"""
    from config.config import get_config

    raw = str(getattr(get_config(), "bilibili_blacklist_up", "") or "")
    return [clean_text(piece) for piece in re.split(r"[,，、;；\n]+", raw) if piece.strip()]


def _user_hit(title: str, author: str) -> str:
    """命中用户自己的屏蔽词 / UP 主 → 原因；没命中返回空串。"""
    for pattern in extra_blacklist():
        if pattern.search(title):
            return f"自定屏蔽词：{pattern.pattern}"
    author_key = clean_text(author)
    if author_key and author_key in extra_blacklist_up():
        return f"屏蔽的UP主：{author}"
    return ""


def spam_reason(title: object, author: object = "", keywords: Sequence[str] = ()) -> str:
    """标题 / UP 主命中黑名单 → 原因（`擦边：福利`）；没命中返回空串。

    `keywords` 是「这首歌自己的名字」之类的词：**搜索词 / 歌名里本来就有的词不算**。
    （脚本的 `if (regex.test(currentKeyword)) continue`）—— 否则《泳装》《震惊》这种
    正经曲名会被自己的名字判成垃圾。用户自己写的屏蔽词不受这条豁免（他明确说了不要）。
    """
    text = TAG_RE.sub("", str(title or ""))
    user = _user_hit(text, str(author or ""))
    if user:
        return user
    exempt = clean_text("".join(str(word or "") for word in keywords))
    for group, pattern in BUILTIN_WORDS:
        match = pattern.search(text)
        if not match:
            continue
        word = match.group(0)
        if exempt and clean_text(word) in exempt:
            continue
        return f"{group}：{word}"
    return ""


def purify_enabled() -> bool:
    """配置里的 `bilibili_purify`（默认开）。"""
    from config.config import get_config

    return bool(getattr(get_config(), "bilibili_purify", True))


def purify_items(items: Sequence[dict], keyword: object,
                 enabled: Optional[bool] = None) -> Tuple[List[dict], int]:
    """按相关性 + 用户屏蔽词过滤 b 站搜索结果 → `(留下的条目, 丢掉几条)`。

    **一条都不剩时整批保留**：脚本里那是「白屏兜底」，这边对应「宁可候选脏一点，
    也不能因为关键词写法刁钻就一首歌都搜不到」—— 后面的评分（`models/video.py`）还会再筛一遍。
    """
    rows = [item for item in items if isinstance(item, dict)]
    if enabled is None:
        enabled = purify_enabled()
    if not enabled or not rows:
        return rows, 0
    allowed = [item for item in rows
               if not _user_hit(str(item.get("title") or ""), str(item.get("author") or ""))]
    kept = [item for item in allowed
            if is_relevant(str(item.get("title") or ""), keyword)]
    if not kept:
        # 白屏兜底只救「相关度」这一层：用户自己屏蔽的东西绝不回流
        kept = allowed
    return kept, len(rows) - len(kept)
