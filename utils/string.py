import re
import unicodedata
from datetime import datetime
from typing import Union, Callable, Tuple, List, Optional


def needs_lang_tag(s: str) -> bool:
    """这段文字要不要套 `{{lj|…}}`（日文 lang 标记）。

    站上的惯例（用户 2026-10-24 拿到的两笔 diff）：
      * 《-２５-》的 `|歌曲名称` 从 `{{lj|-２５-}}` 改成 `-２５-`：**只有全角数字与连字符**，
        按半角算之后一个日文字都没有；
      * 同一页的 `{{lj|13943号室}}`（汉字）、`{{lj|鳥の詩}}`（假名）、
        `{{lj|VanaN’Ice LIVE 2018 …}}`（弯引号）都照旧套着。

    判断办法：把全角转成半角（NFKC）之后，只要还剩**非 ASCII** 的字符就说明它不只是
    「数字 / 拉丁字母 / 标点」，该套 `{{lj}}`。
    """
    return any(ord(char) > 127 for char in unicodedata.normalize("NFKC", str(s or "")))


def auto_lj(s: str) -> str:
    if not needs_lang_tag(s):
        return s
    return "{{lj|" + s + "}}"


def is_empty(s: str) -> bool:
    return not s or s.isspace() or len(s) == 0


def title_match_key(text: str) -> str:
    """标题比较用的键：全角 / 半角统一（NFKC）、去掉空白、大小写不敏感。

    2026-10-23 踩过 voca.wiki《5℃》：VocaDB 上原标题是**全角**的「５℃」
    （id 46373，Original，P主 おかゆ、GUMI 演唱、sm22630254、2014-01-10），而半角的
    「5℃」（id 78862）是一条 **Cover**（別の人の削除済み動画）。原来的
    `defaultName == name` 只认**完全相同的字符串**：narrow（只要 Original）明明搜到了那条
    全角条目，却因为字符串不等被过滤掉；退到 broad 后正好命中半角那条 Cover ——
    于是整个条目写成了翻唱版（歌姬 / P主 / 投稿日 / sm 号全错，用户按站内修订报的）。
    """
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def split(s: str, regex: str = "[・/，, ]+") -> List[str]:
    return re.split(regex, s)


def datetime_to_ymd(u: datetime) -> str:
    """正文里的日期写法：`2013年9月24日`。"""
    return f"{u.year}年{u.month}月{u.day}日"


def datetime_to_iso(u: datetime) -> str:
    """模板参数里的日期写法：`2013-09-24`。

    Songbox 的 `|nnd_date` / `|bb_date` / `|yt_date` 与 `{{…Songbox/card}}` 的日期参数
    都按站上统一的 ISO 格式写（参 voca.wiki《- melancholy -》2026-10-19「统一Songbox日期格式」）。
    正文句子里的 `于…投稿至…` 仍用 `datetime_to_ymd()`。
    """
    return f"{u.year:04d}-{u.month:02d}-{u.day:02d}"


def assert_str_exists(s: str) -> str:
    return s if not is_empty(s) else ""


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\uff0f]', '_', name)
    name = name.rstrip(" .")
    return name or "untitled"


def split_number(num: int) -> str:
    s = str(num)
    chunks = []
    index = len(s) - 3
    while index > 0:
        chunks.insert(0, s[index:index + 3])
        index -= 3
    chunks.insert(0, s[0:index + 3])
    if num >= 100000:
        chunks[-1] = "000+"
    return ",".join(chunks)


def join_string(lst: list, deliminator: Union[str, Tuple[str, str]] = ("、", "和"),
                inner_wrapper: Tuple[str, str] = ("", ""), outer_wrapper: Tuple[str, str] = ("", ""),
                mapper: Callable = lambda x: x) -> str:
    if isinstance(deliminator, str):
        deliminator = (deliminator, deliminator)
    lst = [(outer_wrapper[0] + mapper(inner_wrapper[0] + elem + inner_wrapper[1]) + outer_wrapper[1]) for elem in lst]
    if len(lst) == 0:
        return ""
    if len(lst) == 1:
        return lst[0]
    front = deliminator[0].join(lst[:len(lst) - 1])
    return front + deliminator[1] + lst[-1]


# 歌词正文里常混进来的「资讯行」：作詞：… / 作曲：… / 曲　動画：… / Lyrics: … 这类署名行。
# 各来源的写法不一样，所以角色名之间允许用「、・,，/&＋」或空白分隔，也允许直接连写
# （`作詞作曲：`）；冒号（半角 / 全角）是判定依据，所以「曲がり角で：」这种歌词行不会被误伤。
CREDIT_ROLES = (r"作詞|作词|作曲|編曲|编曲|詞|词|曲|歌詞|歌词|歌|唄|唱|歌唱|訳|譯|翻譯|翻译|译|"
                r"ボーカル|コーラス|イラスト|插畫|插画|曲绘|繪師|绘师|繪圖|绘图|插圖|動画|动画|"
                r"映像|作品类型|作品類型|类型|類型|简介|簡介|简介补充|備註|备注|說明|说明|公告|"
                r"調声|调声|調教|调教|後期|后期|"
                r"混音|演唱|演出|作画|背景|PV|MV|MIX|ミックス|マスタリング|録音|レコーディング|"
                r"カラオケ|オフボーカル|インスト|本家|原曲|収録|"
                r"Lyrics?|Music|Vocal|Chorus|Illust(?:ration)?|Movie|Arrangement|Arrange[rd]?|"
                r"Compose[rd]?|Mixing?|Mastering|Guitar|Bass|Piano|Drums?|Strings?|Violin|"
                r"Special Thanks|Special Thx|Thanks|Thx|Credit|Credits|Staff|Streaming|Download|DL|"
                r"Off ?Vocal|OffVocal|Inst")
CREDIT_LINE_RE = re.compile(
    r"^[\s\u3000（(【\[「『<＜・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}"
    r"(?:" + CREDIT_ROLES + r")(?:[\s\u3000、・,，/&＆+]{0,2}(?:" + CREDIT_ROLES + r"))*"
    r"\s*[:：]",
    re.IGNORECASE)
# 「角色/名字」这种**没有冒号**的署名行。实测 b 站评论《幽灵(KAIRUI)》（BV1wv41137pk，
# 评论 128854373232）的头两行是 `曲/KAIRUI`、`译/千歲世野`：以前只认冒号，这两行就跟着译文
# 进了中文栏，把整栏顶开（用户 2026-10-29 报）。判定从严，免得吃歌词：
#   * 行首必须是纯角色词（`曲` / `译` / `作詞` …），紧跟 `/` 或 `／`；
#   * 斜杠后面至少两个字（`曲/夢` 这种太短的不算）；
#   * 整行不超过 `CREDIT_SLASH_MAX_CHARS` 个字。
CREDIT_SLASH_RE = re.compile(
    r"^[\s\u3000（(【\[「『<＜・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}"
    r"(?:" + CREDIT_ROLES + r")(?:[\s\u3000、・,，&＆+]{0,2}(?:" + CREDIT_ROLES + r"))*"
    r"\s*[/／]\s*\S{2,}",
    re.IGNORECASE)
CREDIT_SLASH_MAX_CHARS = 24
# 整行只有一条链接（裸 URL、`<url>`、`[文字](url)`）：简介 / 作品开头的「本家」「動画」行那种
LINK_ONLY_RE = re.compile(
    r"^[\s\u3000・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}"
    r"(?:\[[^\]\n]{1,60}\]\(\s*)?<?https?://\S+>?\)?\s*$",
    re.IGNORECASE)
# 行里带网址：歌词行不会带（`offvocal → https://…`、`■ブログ　http://…` 这类说明行才会）
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
# nico / b 站视频号、mylist：`sm43424916`、`失效补档av36439`、`■前作「clock lock works」sm8933166`
VIDEO_ID_RE = re.compile(r"(?:^|[^0-9A-Za-z])(?:sm|nm|so|av)\d{4,}|BV[0-9A-Za-z]{10}|mylist/\d+",
                         re.IGNORECASE)
SEPARATOR_LINE_RE = re.compile(r"^[-=_~ー—–‐]{3,}$")
LAUGH_LINE_RE = re.compile(r"^[wWｗＷ]{2,}$")
NUMBER_LINE_RE = re.compile(r"^[0-9０-９]+$")
# 译者写在正文里的**注释行**：`注：虹搜索=niji sousaku=二次创作`、`※原文如此`。
# 实测 b 站专栏《虹捜索》（`/opus/1036390280160346128`）的第三段就是「注：…」，
# 它会跟着歌词进「待归类歌词」、还让开头那段变成奇数行（整首逐句配对判不出来）。
# 歌词几乎不会以「注：」「※」开头，所以锚在行首判。
NOTE_PREFIX_LINE_RE = re.compile(r"^\s*(?:[＊\*※]\s*)?(?:注|註)\s*[:：]|^\s*[＊\*]?\s*※")
# 译者 / 转载者的**自述**行（不是歌词）：`歌词渣翻。大部分机翻+个人理解。如有错误，欢迎指正`、
# `轉載請註明出處` 这种。只用完整的短语（不用「搬运」「未经允许」这类普通词语）以免误伤歌词 ——
# 歌词里基本不会出现「渣翻 / 机翻 / 如有错误 / 欢迎指正 / 转载」这些说法（用户 2026-10-16 要求）。
NOTE_MARKERS = ("渣翻", "渣译", "渣譯", "机翻", "機翻", "机器翻译", "機器翻譯",
                "如有错误", "如有錯誤", "有错请指正", "有錯請指正", "有误请指正", "有誤請指正",
                "欢迎指正", "歡迎指正", "欢迎纠正", "歡迎糾正", "敬请指正", "敬請指正",
                "请多指教", "請多指教", "请多包涵", "請多包涵",
                "自翻", "个人翻译", "個人翻譯", "翻译练习", "翻譯練習",
                "转载", "轉載", "転載", "转贴", "轉貼", "转自", "轉自", "搬运自", "搬運自",
                "侵删", "侵權即刪", "仅供学习", "僅供學習", "非商业用途", "非商業用途",
                "版权归", "版權歸", "著作权属", "著作權屬", "严禁转载", "嚴禁轉載",
                "以下为歌词", "以下為歌詞", "歌词如下", "歌詞如下")
NOTE_LINE_RE = re.compile("|".join(re.escape(marker) for marker in NOTE_MARKERS))
# 译者的「使用声明」（不是歌词）。实测 AtWiki《273.15》：正文第一行
# 「基於相互尊重，請取用翻譯者不要改動我的翻譯，感謝」—— 没有上面那些常见的渣翻 / 转载字样，
# 于是跟着歌词进了「中文」栏（用户 2026-10-19 报）。这些只要出现就整行丢掉。
TRANSLATOR_NOTE_MARKERS = (
    "請取用", "请取用", "不要改動", "不要改动", "請勿改動", "请勿改动",
    "勿改動", "勿改动", "尊重翻譯", "尊重翻译",
    "請註明", "请注明", "註明出處", "注明出处",
    "禁止上傳", "禁止上传", "二次上傳", "二次上传",
    "禁止搬運", "禁止搬运", "謝絕轉載", "谢绝转载",
)
TRANSLATOR_NOTE_RE = re.compile("|".join(re.escape(marker) for marker in TRANSLATOR_NOTE_MARKERS))
# 译者名后面常跟着一句自述（不是名字的一部分）。实测 AtWiki《パンドラ》
# （pages/2231.html）的表头：`翻譯：cyataku(不當之處敬請指正)` —— 整串被当成译者名写进了
# `*翻译：…`（用户 2026-10-25 报）。括号里是「说明」才去掉，只当名字的（`Cilde`、`紫(し)`）保留。
TRAILING_NOTE_PAREN_RE = re.compile(r"\s*[\(（\[【]([^()（）\[\]【】]{1,40})[\)）\]】]\s*$")
TRANSLATOR_NOTE_WORDS = ("指正", "指教", "請", "请", "感謝", "感谢", "謝謝", "谢谢",
                         "轉載", "转载", "転載", "修改", "校正", "錯誤", "错误", "有誤", "有误",
                         "未經", "未经", "許可", "许可", "機翻", "机翻", "機器", "机器",
                         "日文", "日語", "日语", "學習", "学习", "見諒", "见谅", "抱歉",
                         "歡迎", "欢迎", "交流", "本人", "不足", "敬請", "敬请", "以上", "大概")
# 「说明」至少这么长才认（`(不當之處敬請指正)` 8 个字；`(し)` 这种读音 / 昵称不动）
TRANSLATOR_NOTE_MIN_CHARS = 4


def strip_translator_note(name: str) -> str:
    """去掉译者名后面的自述：`cyataku(不當之處敬請指正)` → `cyataku`。

    只有括号里像「一句话」时才去掉（含指正 / 转载 / 感谢这类字眼，或者不短于
    `TRANSLATOR_NOTE_MIN_CHARS` 个字且没有拉丁字母），所以 `Cilde`、`yanao`、`紫(し)`
    这些正常写法不受影响。可连续去掉多段（`A(b)(c)`），全部去完就没名字时保留原样。
    """
    text = str(name or "").strip()
    while True:
        match = TRAILING_NOTE_PAREN_RE.search(text)
        if not match:
            break
        head = text[:match.start()].strip()
        note = match.group(1).strip()
        if not head or not _looks_like_note(note):
            break
        text = head
    return text


def _looks_like_note(note: str) -> bool:
    """括号里这段是「说明」还是「名字的一部分」。"""
    if not note:
        return False
    if any(word in note for word in TRANSLATOR_NOTE_WORDS):
        return True
    return (len(note) >= TRANSLATOR_NOTE_MIN_CHARS
            and not re.search(r"[A-Za-z]", note))


# 译文里**译者注**的信号（用户 2026-10-29 要求「看括号里的内容判断，是注释才转」）。
# 实测网易云《image(picco)》的译文：「被骗了 笑不活啦（日本网络用语「草」（くさ）来表示“笑”的意思）」
# —— 括号里在讲原文 / 读音 / 意思，是注释；而 `（笑）`、`（我）` 这种语气 / 自称不是注释，
# 必须原样留着（它们是译文的一部分）。所以只有在**看到这些信号**时才认。
TRANSLATOR_NOTE_SIGNALS = (
    "译注", "譯註", "译者注", "譯者註", "原注", "注：", "注:",
    "原文", "日文", "日語", "日语", "漢字", "汉字", "假名", "平假名", "片假名",
    "讀音", "读音", "發音", "发音", "音译", "音譯", "谐音", "諧音", "罗马音", "羅馬音",
    "意思", "意为", "意為", "意指", "表示", "指的是", "是指", "指代", "即指",
    "网络用语", "網絡用語", "網路用語", "俚语", "俚語", "俗语", "俗語", "流行语", "流行語",
    "双关", "雙關", "梗", "歌詞", "歌词", "引用", "直译", "直譯", "意译", "意譯",
    "以下简称", "以下簡稱", "缩写", "縮寫",
)


def is_translator_note(text: str, jap_line: str = "") -> bool:
    """括号里的这段文字是不是**译者注**（不是译文的一部分）。

    判据只看内容（用户 2026-10-29 要求）：
    * 出现译注 / 原文 / 读音 / 意思 / 表示 / 网络用语这类字眼 —— 那是在讲原文；
    * 或者括号里的**汉字词在对应那行日语里就出现过**（`悲惨地叫唤（陷入阿鼻地狱后的呼叫声）`
      对应 `阿鼻叫喚うっさいわ`，`阿鼻` 就在原文里）—— 那是在解释某个词。
    `（笑）`、`（我）`、`（一起）` 这种没有信号、又跟原文无关的一律不算：宁可少转，
    也不要把译文里的括号改成脚注。
    """
    value = str(text or "").strip()
    if len(value) < 2:
        return False
    if any(signal in value for signal in TRANSLATOR_NOTE_SIGNALS):
        return True
    return _quotes_original(value, jap_line)


def _quotes_original(text: str, jap_line: str) -> bool:
    """括号里有没有引用原文：里面某个 2～4 个字的词在日语那一行里也出现。"""
    source = str(jap_line or "")
    if len(source) < 2:
        return False
    run: List[str] = []
    for char in text + "\n":
        if _is_kanji_char(char):
            run.append(char)
            continue
        word = "".join(run)
        for size in range(2, min(4, len(word)) + 1):
            if any(word[start:start + size] in source
                   for start in range(len(word) - size + 1)):
                return True
        run = []
    return False


def _is_kanji_char(char: str) -> bool:
    """是不是汉字（CJK 统一表意文字 / 扩展 A / 兼容区）。"""
    code = ord(char)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
            or 0xF900 <= code <= 0xFAFF)


# 「译注：」这类前缀（括号里已经写明是注释时不再补一个「译者注：」）
NOTE_PREFIX_RE = re.compile(r"^(?:译者注|譯者註|译注|譯註|原注|注释|註釋|注)\s*[:：]?\s*")


def _loose_note_span(line: str, start: int) -> Optional[int]:
    """从 `start` 处的左括号往后找配对的右括号，返回右括号的下标（找不到返回 None）。"""
    depth = 0
    for index in range(start, len(line)):
        char = line[index]
        if char in "（(":
            depth += 1
        elif char in "）)":
            depth -= 1
            if not depth:
                return index
    return None


def to_note_refs(text: str, jap: str = "") -> str:
    """把译文里的**译者注**括号改成行内脚注：`（日本网络用语「草」…）` → `<ref>译者注：…</ref>`。

    站上的写法就是这样（用户 2026-10-29 给的 https://voca.wiki/image(picco)）：:

        被骗了 笑不活啦<ref>译者注：日本网络用语「草」（くさ）来表示“笑”的意思</ref> 别太搞笑了
        悲惨地叫唤<ref>译者注：陷入阿鼻地狱后的呼叫声</ref>真吵啊 别再责怪了

    只转 `is_translator_note()` 认下来的括号，其余原样保留。两条额外的口径：

    * `jap` 给了、且两栏行数与空行位置一致时，按行号把日语那一行传进去 ——
      括号里的词在原文里出现过就算在解释原文（见 `is_translator_note()`）；
    * **同一行只转第一次出现的那一处**（歌词里重复的段落共用同一份译文，脚注重复写会
      在 `<references/>` 里冒出两条一模一样的注释；条目里也是这么处理的）。

    已经是 `<ref>` 的不会再动，所以重复点「AI 识别并填入」是幂等的；缩进 / 空行不受影响。
    """
    chs_lines = str(text or "").split("\n")
    jap_lines = str(jap or "").split("\n")
    paired = len(jap_lines) == len(chs_lines) and bool(str(jap or "").strip())
    result: List[str] = []
    seen: set = set()
    for index, line in enumerate(chs_lines):
        jap_line = jap_lines[index] if paired else ""
        key = re.sub(r"\s+", "", line)
        if not key:
            result.append(line)
            continue
        if key in seen:
            # 重复的段落共用同一份译文：脚注前面已经写过，这里连注释一起省掉
            # （条目《image(picco)》第 4 段就是这样，`<references/>` 里才不会出现两条一样的注释）
            result.append(_line_note_refs(line, jap_line, drop=True))
            continue
        seen.add(key)
        result.append(_line_note_refs(line, jap_line))
    return "\n".join(result)


def _line_note_refs(line: str, jap_line: str = "", drop: bool = False) -> str:
    """把一行里的译者注换成 `<ref>`；`drop=True` 时直接去掉（重复段用，见 `to_note_refs()`）。"""
    result: List[str] = []
    index = 0
    while index < len(line):
        if line[index:index + 5].lower().startswith("<ref"):
            end = line.find("</ref>", index)          # 已经是脚注：整段跳过（幂等）
            end = len(line) if end < 0 else end + len("</ref>")
            result.append(line[index:end])
            index = end
            continue
        char = line[index]
        if char not in "（(":
            result.append(char)
            index += 1
            continue
        end = _loose_note_span(line, index)
        if end is None:
            result.append(char)
            index += 1
            continue
        inner = line[index + 1:end].strip()
        if is_translator_note(inner, jap_line):
            if drop:
                index = end + 1
                continue
            body = inner if NOTE_PREFIX_RE.match(inner) else f"译者注：{inner}"
            result.append(f"<ref>{body}</ref>")
        else:
            result.append(line[index:end + 1])
        index = end + 1
    return "".join(result)
# 「この歌詞は動画から書き起こしたもの」这种**来源说明**（不是歌词）。实测初音ミク Wiki
# 《咲うわ。》正文第一行就是「（動画より書き起こし）」，混进日文歌词会被当成歌词行。
TRANSCRIPTION_NOTE_MARKERS = ("書き起こし", "書きおこし", "書起こし", "聞き起こし",
                              "聞き取り", "耳コピ", "歌詞起こし")
TRANSCRIPTION_NOTE_RE = re.compile("|".join(re.escape(m) for m in TRANSCRIPTION_NOTE_MARKERS))
# 「Sheena版:」「ミスターレイヴン cover版:」这类版本标签（多版本作品的正文小标题）
VERSION_LABEL_RE = re.compile(r"^[^版\n]{0,24}版\s*[:：]?$")
# AtWiki 页尾的标签行（实测 chly《日蝕》最后一节：`tag：すんｚりヴぇｒ,鏡音リン・鏡音レンな,も`）
# 是页面元数据，不是歌词。锚在行首，「歌词里出现 tag 这个词」不会误伤。
TAG_LINE_RE = re.compile(r"^\s*tag\s*[:：]", re.IGNORECASE)
# AtWiki 末尾的修订备注（实测《Liquid Metal》页面最后一行：`12.22　事隔兩年餘修正`）：
# 「日期 + 修正 / 更新」这种小字说明不是歌词。歌词行几乎不会以日期开头。
EDIT_NOTE_RE = re.compile(
    r"^\s*\d{1,4}\s*[./-]\s*\d{1,2}(?:\s*[./-]\s*\d{1,2})?[\s\u3000]*"
    r".*(?:修正|訂正|订正|編集|编辑|更新|追記|追记|補足|补足)")
# 「简介」里还常写成「角色 + 空白 + 名字」不带冒号（`Music 袖野あらわ`、`Illust 薬屋`、
# `Movie Fujiyama`）。只在简介模式认这种：值要么整段没有拉丁字母、要么就是**一个**英文词，
# 否则 `Music is my life` 这种歌词会被误删。
DESC_CREDIT_NO_COLON_RE = re.compile(
    r"^[\s\u3000・◆◇■□▲△▼▽●○*＊#＃>＞]{0,4}(?:" + CREDIT_ROLES + r")"
    r"\s+(?:[^\sA-Za-z]{1,20}|[A-Za-z][A-Za-z.'’\-]{1,17})$",
    re.IGNORECASE)
# 整行就是一个栏目标签
LABEL_LINES = {"歌詞", "歌词", "原文", "譯文", "译文", "中譯", "中译", "中文歌詞", "中文歌词",
               "日文歌詞", "日文歌词", "翻譯", "翻译", "credit", "credits", "staff",
               "クレジット", "スタッフ", "lyrics", "lyric", "translation", "translations"}
# 带括号的栏目标签：`【歌词】` / `[歌詞]` / `「歌词」`（译者 / UP 主在评论里常这么分节）。
# 2026-10-22 踩过：《Short Hand》UP 主的那条评论是「一句说明 + `【歌词】` + 正文」，
# 标签行连同前面的说明一起被当成中文歌词（用户报）。
LABEL_WRAP_RE = re.compile(r"^[【\[「『〔（(]\s*(?P<label>[^】\]」』〕）)]{1,12}?)\s*[】\]」』〕）)]"
                           r"\s*[：:]?$")
# 「单独成段的整句话」的收尾标点（只在简介那种笔记式文本上用，见 strip_credit_lines）
SENTENCE_END_CHARS = "。．.！!？?…~～"
PROSE_MIN_CHARS = 8


def is_label_line(line: str) -> bool:
    """这一行是不是「栏目标签」（`歌词` / `【歌词】` / `[Translation]`）——不是歌词。"""
    text = (line or "").strip().lstrip("・◆◇■□▲△▼▽●○*＊#＃>＞ \u3000").strip()
    if not text:
        return False
    if text.lower() in LABEL_LINES:
        return True
    match = LABEL_WRAP_RE.match(text)
    return bool(match) and match.group("label").strip().lower() in LABEL_LINES


def drop_lead_intro(text: str) -> str:
    """去掉栏目标签**之前**的那段说明（不是歌词）。

    译者 / UP 主在评论区贴词时，常先写一句这首歌讲什么，再用 `【歌词】` 起头：
    《Short Hand》的评论就是「这是一个关于时钟的短针爱上长针的故事」+ `【歌词】` + 正文，
    前两句一起进了「中文」栏（用户 2026-10-22 报）。只有**确实找到标签**、且标签后面还有
    内容时才切，别把正常的歌词正文截掉。
    """
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        if not is_label_line(line):
            continue
        start = index + 1
        while start < len(lines) and not lines[start].strip():
            start += 1                      # 标签后面那几行空行不算歌词
        if start < len(lines):
            return "\n".join(lines[start:])
        break
    return text


# 歌词里的 `~~~`：三个以上半角波浪号在 wikitext 里是**签名**（保存时被展开成
# `[[User:…|…]]（[[User talk:…|留言]]）`）。实测用户 2026-10-30 给的
# 《我是个废柴！》diff 276881：歌词行「果咩T_T，还是想吃肉！！！~~~」被存成了签名，
# 编辑改成 `<nowiki>~~~</nowiki>` 才显示成波浪号。已经包过的先拆开再包，重复生成不会套两层。
TILDE_RUN_RE = re.compile(r"<nowiki>(~{3,})</nowiki>|~{3,}")


def protect_tildes(text: str) -> str:
    """把歌词里 3 个以上连续半角 `~` 包成 `<nowiki>…</nowiki>`（幂等）。"""
    if not text:
        return text

    def wrap(match: "re.Match") -> str:
        return f"<nowiki>{match.group(1) or match.group(0)}</nowiki>"

    return TILDE_RUN_RE.sub(wrap, text)


def is_junk_line(line: str, description: bool = False) -> bool:
    """这一行是不是「资讯 / 杂物」行（不是歌词）？`description` 见 `strip_credit_lines()`。"""
    text = (line or "").strip()
    if not text:
        return False
    stripped = text.lstrip("・◆◇■□▲△▼▽●○*＊#＃>＞ \u3000").strip()
    return bool(CREDIT_LINE_RE.match(text)
                or (len(text) <= CREDIT_SLASH_MAX_CHARS and CREDIT_SLASH_RE.match(text))
                or LINK_ONLY_RE.match(text)
                or URL_RE.search(text)
                or VIDEO_ID_RE.search(text)
                or SEPARATOR_LINE_RE.match(stripped)
                or LAUGH_LINE_RE.match(stripped)
                or NUMBER_LINE_RE.match(stripped)
                or NOTE_PREFIX_LINE_RE.match(text)
                or VERSION_LABEL_RE.match(stripped)
                or TAG_LINE_RE.match(stripped)
                or EDIT_NOTE_RE.match(stripped)
                or NOTE_LINE_RE.search(text)
                or TRANSLATOR_NOTE_RE.search(text)
                or TRANSCRIPTION_NOTE_RE.search(text)
                or stripped.lower() in LABEL_LINES
                or is_label_line(text)
                or (description and DESC_CREDIT_NO_COLON_RE.match(text)))


def _join_with_single_blank_lines(lines: List[str]) -> str:
    """首尾不留空行，连续空行只留一个（与歌词页 `normalize_blank_lines` 同一口径）。"""
    result: List[str] = []
    blank = False
    for line in lines:
        if not line:
            blank = True
            continue
        if blank and result:
            result.append("")
        blank = False
        result.append(line)
    return "\n".join(result)


def _drop_isolated_prose(lines: List[str]) -> List[str]:
    """去掉**单独成段**的整句话（宣传语 / 说明句）。

    只给 b 站视频简介那种「笔记式」文本用：那里除了歌词还写着「期待する方が悪い。」
    「总而言之3千粉啦…」这类话，整句独占一段时删掉。判定是「这一段只有一行 + 以句末
    标点收尾 + 不少于 8 个字」，所以歌词段（多行，哪怕每行都以 ！ 结尾）不会被误删。
    """
    result: List[str] = []
    index = 0
    while index < len(lines):
        if not lines[index]:
            result.append("")
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index]:
            index += 1
        block = lines[start:index]
        single = len(block) == 1
        if not (single and len(block[0]) >= PROSE_MIN_CHARS
                and block[0].rstrip()[-1] in SENTENCE_END_CHARS):
            result.extend(block)
    return result


def strip_credit_lines(text: str, description: bool = False) -> str:
    """摘掉歌词里的**资讯行 / 杂物行**。

    包括：`作詞：ねこぼーろ` / `作詞、作曲: Shin` / `曲　動画：Tonbi` / `Lyrics: …` 这类
    署名行，`曲/KAIRUI` / `译/千歲世野` 这种**用斜杠代替冒号**的写法（见 `CREDIT_SLASH_RE`），
    `注：虹搜索=niji sousaku=二次创作` / `※原文如此` 这种**注释行**（见 `NOTE_PREFIX_LINE_RE`），
    整行只有一条链接的行（`[初音ミク](https://…)`、裸 URL），带网址的说明行
    （`offvocal → https://…`、`Streaming & DL：https://…`），nico / b 站视频号与 mylist
    （`sm43424916`、`失效补档av36439`），`20` 这种纯数字行，`-----` 分隔线，`www` 这类
    笑声行，「Sheena版:」这种版本标签行，整行就是一个栏目标签的「歌詞 / 原文」行，
    以及译者 / 转载者的**自述**行（`歌词渣翻。大部分机翻+个人理解。如有错误，欢迎指正`、
    `轉載請註明出處`，见 `NOTE_MARKERS`）。

    来源页（AtWiki 页面正文、网易云歌词、b 站简介、巴哈马特作品开头）经常把这些粘在歌词
    前面或中间（用户 2026-10-16 报），不摘掉就会被当成歌词行填进「待归类歌词」/「中文」栏；
    只有中文的译文夹着日文署名行时，「只有中文」的判断也会跟着失灵。

    `description=True` 给 b 站视频简介那种**笔记式**文本用（用户 2026-10-17 要的「宣传语也
    一并清掉」）：除了上面那些，还删「单独成段的整句话」与「角色 + 空白 + 名字」的署名行
    （见 `_drop_isolated_prose()` / `DESC_CREDIT_NO_COLON_RE`）；歌词页那些正经来源不开这个，
    免得把歌词里的整句行误删。
    顺手按歌词页的口径规整空行（首尾不留空行、段落之间只留一个）。
    """
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    kept = [line for line in lines if not is_junk_line(line, description)]
    if description:
        kept = _drop_isolated_prose(kept)
    return _join_with_single_blank_lines(kept)
