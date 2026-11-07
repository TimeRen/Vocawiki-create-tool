from __future__ import annotations

import logging
import re
import shutil
import struct
from pathlib import Path
from typing import Union, List, Sequence, Set, Tuple, Optional

from PIL import Image, ImageOps

_BOX = getattr(getattr(Image, "Resampling", Image), "BOX")

from config.config import get_output_path
from models.video import Video, VideoSite
from utils.helpers import http_get


def download_file(url: str, target: Union[str, Path]) -> bool:
    with http_get(url, stream=True, use_proxy=True) as r:
        r.raise_for_status()
        with open(target, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return True


def write_to_file(output: str, filename: Union[str, Path]):
    f = open(filename, "w", encoding="UTF-8")
    f.write(output)
    f.close()


# 判定“黑边”的亮度上限（0-255）：超过该亮度的像素不算黑。这是兜底限制，
# 防止把本来就偏暗的画面整段误判成黑边。
MAX_BLACK_LEVEL = 40.0
# 一行 / 一列里「黑像素」占到该比例才算黑边。真实编码出来的黑边几乎全是纯黑像素
# （比例 1.0），而暗调画面（夜景、深蓝底、大面积阴影）总有高光、字幕或噪点，
# 比例远低于这个值。早先按「整行亮度**均值** ≤ MAX_BLACK_LEVEL」判，暗调封面会被
# 整段当成黑边裁掉（用户 2026-11-06 报「裁多了、误伤画面内容」）。
BLACK_PIXEL_RATIO = 0.995
# 单侧最多允许裁掉的比例。若黑边超过该比例，说明判定不可靠（可能是暗色画面而非黑边），
# 此时放弃**那一轴**的裁剪，避免误伤封面内容。
MAX_BORDER_RATIO = 0.4

# 裁完之后画面比该比例还宽，说明这张封面本身就是「宽画幅 + 黑边」的整幅设计，
# 黑边不是要去掉的假边。实测《暮光剧场》的 nico 封面内容是 1137x482（2.36:1）、
# 《你嘲笑我那天》是 1280x449（2.85:1）；把黑边裁掉只会得到一条又扁又长的长条
# —— 条目里封面按宽 280 显示（280x119），完全不像封面。这种情况保持原图。
MAX_CROP_ASPECT = 2.0

# 被裁掉的面积占比达到该值时，认为这张封面「自带黑边」（拿它当封面要么是长条、
# 要么得留着黑边）。同样清晰时宁可换一张干净的来源，见 `download_first`。
HEAVY_BORDER_RATIO = 0.25


def _otsu_from_hist(hist: List[int]) -> int:
    """纯 Python 版的 Otsu：Android 没有 numpy 时用。"""
    total = sum(hist)
    if total == 0:
        return 0
    sum_all = sum(index * count for index, count in enumerate(hist))
    weight_low = 0
    sum_low = 0
    best_level = 0
    best_between = -1.0
    for index, count in enumerate(hist):
        weight_low += count
        sum_low += index * count
        weight_high = total - weight_low
        if weight_low == 0 or weight_high == 0:
            continue
        mean_low = sum_low / weight_low
        mean_high = (sum_all - sum_low) / weight_high
        between = weight_low * weight_high * (mean_low - mean_high) ** 2
        if between > best_between:
            best_between = between
            best_level = index
    return best_level


def border_length(profile: Sequence[float]) -> Optional[Tuple[int, int]]:
    """从两端向内统计**黑像素占比 ≥ `BLACK_PIXEL_RATIO`** 的连续行 / 列数。

    返回 (前端长度, 后端长度)；任意一侧触到 `MAX_BORDER_RATIO` 上限时返回 None
    （这一轴判不清，由 `detect_border_box` 只放弃这一轴）。
    """
    n = len(profile)
    if n == 0:
        return None
    limit = max(1, int(n * MAX_BORDER_RATIO))
    head = 0
    while head < limit and profile[head] >= BLACK_PIXEL_RATIO:
        head += 1
    tail = 0
    while tail < limit and profile[n - 1 - tail] >= BLACK_PIXEL_RATIO:
        tail += 1
    if head >= limit or tail >= limit:
        return None
    return head, tail


def _black_ratio_profile(gray: Image.Image,
                         black_level: float) -> Tuple[List[float], List[float]]:
    """每行 / 每列的**黑像素占比**（0~1）。

    做法：先按 `black_level` 二值化，再用 BOX 重采样到 1 像素宽 / 高 —— BOX 求的是均值，
    二值化掩膜（0 / 255）的均值正好就是黑像素比例。纯 PIL，numpy 与 Android 走
    同一条路，两边结果不会再有差别。
    """
    width, height = gray.size
    mask = gray.point(lambda value: 255 if value <= black_level else 0, mode="L")
    rows = [value / 255.0 for value in mask.resize((1, height), _BOX).tobytes()]
    cols = [value / 255.0 for value in mask.resize((width, 1), _BOX).tobytes()]
    return rows, cols


def detect_border_box(img: Image.Image) -> Tuple[int, int, int, int]:
    """自动探测封面黑边，返回裁剪区域 (y1, y2, x1, x2)；无可靠黑边时返回整幅图。

    这是**原始**的探测结果（不看 `MAX_CROP_ASPECT`），评价「这张封面有没有黑边」用它；
    真正裁剪请用 `detect_crop_box`。

    横竖两轴**各自判断**：某一轴的黑边超过 `MAX_BORDER_RATIO`（判不清）只放弃那一轴，
    另一轴照裁 —— 早先任一轴判不清就整张不动，上下真黑边 + 左右暗调画面时白放过一次。
    """
    gray = ImageOps.grayscale(img)
    width, height = gray.size
    black_level = min(_otsu_from_hist(gray.histogram()), MAX_BLACK_LEVEL)
    rows, cols = _black_ratio_profile(gray, black_level)
    row_border = border_length(rows)
    col_border = border_length(cols)
    y1, y2 = (0, height) if row_border is None else (row_border[0], height - row_border[1])
    x1, x2 = (0, width) if col_border is None else (col_border[0], width - col_border[1])
    if y1 >= y2 or x1 >= x2:
        return 0, height, 0, width
    return y1, y2, x1, x2


def detect_crop_box(img: Image.Image) -> Tuple[int, int, int, int]:
    """要裁的区域：`detect_border_box` 的结果，但裁完比 `MAX_CROP_ASPECT` 还宽时不裁。

    「去掉黑边反而变成超宽长条」说明黑边是封面美术的一部分（整幅就是宽画幅的设计），
    这时保留原图才是用户看到的正常封面。
    """
    y1, y2, x1, x2 = detect_border_box(img)
    if (x1, y1) != (0, 0) or (x2, y2) != (img.width, img.height):
        if (x2 - x1) / (y2 - y1) > MAX_CROP_ASPECT:
            logging.info("封面去掉黑边会变成%dx%d（比%.1f:1还宽），"
                         "判定为「宽画幅 + 黑边」的整幅设计，保持原图",
                         x2 - x1, y2 - y1, MAX_CROP_ASPECT)
            return 0, img.height, 0, img.width
    return y1, y2, x1, x2


def remove_black_boarders(image_in: Union[str, Path], image_out: Union[str, Path]) -> None:
    """自动识别并裁掉封面四周的黑边，结果写入 image_out。

    无需手动指定裁剪力度：先用 Otsu 方法估计“黑”的亮度线（并限制在纯黑范围内），
    再按「整行 / 整列的黑像素占比」统计连续黑边；某轴判定不可靠时只保该轴原样。
    裁剪用 `Image.crop()`，numpy 有没有都一样。
    """
    img = Image.open(image_in)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    y1, y2, x1, x2 = detect_crop_box(img)
    if (y1, y2, x1, x2) == (0, img.height, 0, img.width):
        img.save(image_out)
        return
    img.crop((x1, y1, x2, y2)).save(image_out)


# 用户导入的图写成封面时的 JPEG 质量：只换编码、不缩放，压太狠会看出块
COVER_JPEG_QUALITY = 95
# 「导入图片」时立刻转出来的 JPEG 在文件名上加这一段：`X封面.jpg` → `X封面.import.jpg`
# （提交时直接复制过去，不再二次编码；写完就删掉，见 `imported_cover_path()`）
IMPORTED_COVER_SUFFIX = ".import"
# 用户在样式页「导入图片」换上的那些封面（解析后的绝对路径）：文件说明页据此挂
# `[[分类:截图]]` 而不是「视频封面」（用户 2026-11-02 要求），见 `is_imported_cover()`
IMPORTED_COVERS: Set[Path] = set()


def is_jpeg(path: Union[str, Path]) -> bool:
    """文件**内容**是不是 JPEG（扩展名不可信：下载 / 截图下来的名字常跟内容对不上）。"""
    try:
        with open(path, "rb") as handle:
            return handle.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _flatten(image: Image.Image) -> Image.Image:
    """转成能存 JPEG 的 RGB；**透明的地方铺白底**。

    以前直接 `convert("RGB")`：带透明的 PNG（截图工具 / AI 超分常产出这种）会被当成
    全黑，转出来的 JPG 边缘一圈黑块（用户 2026-10-23 报「导入 PNG 帮我转成 JPG」时一并修掉）。
    """
    has_alpha = (image.mode in ("RGBA", "LA")
                 or (image.mode == "P" and "transparency" in image.info))
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, (255, 255, 255))
    canvas.paste(rgba, mask=rgba.split()[-1])
    return canvas


def to_jpeg(source: Union[str, Path], target: Union[str, Path]) -> bool:
    """把任意图片写成 **JPEG**（透明处铺白底）；源本来就是 JPEG 就直接复制，不二次编码。

    站上的封面一律叫 `X.jpg`，而 MediaWiki 上传时会核对内容类型与扩展名，所以进工具之前
    就得是 JPEG（用户 2026-10-23：导入的图是 PNG 时也帮转成 JPG）。尺寸不动 ——
    用户导入的就是要提交的那张，不替他裁。写不了（不是图片 / 没权限）返回 False。
    """
    source, target = Path(source), Path(target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_jpeg(source):
            if source.resolve() != target.resolve():
                shutil.copyfile(source, target)
            return True
        with Image.open(source) as raw:
            image = _flatten(raw)
        if source.resolve() == target.resolve():
            # 就地转（源和目标同一个文件）：先写临时文件再换，别一边读一边写
            temporary = target.with_name(target.name + ".tmp")
            image.save(temporary, "JPEG", quality=COVER_JPEG_QUALITY)
            temporary.replace(target)
        else:
            image.save(target, "JPEG", quality=COVER_JPEG_QUALITY)
    except Exception as e:
        logging.error("把%s写成JPEG %s失败：%s", source, target, e)
        return False
    return True


def save_as_cover(source: Union[str, Path], target: Union[str, Path]) -> bool:
    """把用户导入的图片写成**提交用的封面文件**（统一转成 JPEG 存到 target）。

    记一笔「这张封面是导入的」：文件说明页要挂 `[[分类:截图]]`（用户 2026-11-02 要求），
    而导入这件事只发生在样式页，主流程靠 `is_imported_cover()` 查回来。
    """
    written = to_jpeg(source, target)
    if written:
        try:
            IMPORTED_COVERS.add(Path(target).resolve())
        except OSError:                                 # 路径解析失败就不记，不影响换图
            pass
    return written


def is_imported_cover(path: Union[str, Path, None]) -> bool:
    """这个封面文件是不是用户在样式页「导入图片」换上去的那张。"""
    if not path:
        return False
    try:
        return Path(path).resolve() in IMPORTED_COVERS
    except OSError:
        return False


def forget_imported_cover(path: Union[str, Path, None]) -> None:
    """「移除图片」时忘掉这条记录（自动下载的那张重新算作下载封面）。"""
    if not path:
        return
    try:
        IMPORTED_COVERS.discard(Path(path).resolve())
    except OSError:
        pass


def imported_cover_path(target: Union[str, Path]) -> Path:
    """「导入图片」那一刻转出来的 JPEG 存哪儿：跟着这一版要提交的封面文件走。

    `<译名>封面.jpg` → `<译名>封面.import.jpg`（同目录）。各版本互不覆盖，
    提交时由 `save_as_cover()` 复制过去当正式封面，`cleanup_imported()` 再把它删掉。
    """
    target = Path(target)
    return target.with_name(target.stem + IMPORTED_COVER_SUFFIX + target.suffix)


def cleanup_imported(path: Union[str, Path]) -> None:
    """删掉「导入时转出来的那份临时 JPEG」（不是这种文件就什么都不做）。"""
    path = Path(path)
    if path.name.endswith(IMPORTED_COVER_SUFFIX + path.suffix):
        path.unlink(missing_ok=True)


# 识别分辨率时最多读取的头部字节数（只读取图片头部，不下载整张图片）
IMAGE_HEADER_BYTES = 64 * 1024

# YouTube 缩略图的文件名直接对应分辨率，可免请求识别
YOUTUBE_THUMB_SIZES = {
    "maxresdefault": 1280 * 720,
    "sddefault": 640 * 480,
    "hqdefault": 480 * 360,
    "mqdefault": 320 * 180,
    "default": 120 * 90,
}

# niconico 的 CDN 缩略图带尺寸后缀：不带后缀只有 130x100（列表用小图），
# `.M` 是 320x180，`.L` 是 360x270（实测 sm43439171 / sm42606117）。
#
# ⚠️ 后缀**不一定有**：已删除稿件走 nicolog 时拿到的是 `…/thumbnails/<id>/<id>`
# （没有 `.数字` 那一段，实测 sm21899594）—— 旧正则要求末尾是 `.数字`，这类地址就被漏掉，
# 于是条目封面用的是 130x100 的最小图（用户 2026-10-21 反馈「这图好糊啊」）。
NICO_LARGE_SUFFIX = ".L"
NICO_THUMB_URL_RE = re.compile(
    r"^https?://nicovideo\.cdn\.nimg\.jp/thumbnails/\d+/\d+(?:\.\d+)?$")


def _jpeg_size(data: bytes) -> Optional[Tuple[int, int]]:
    """扫描 JPEG 的 SOF 段，返回 (宽, 高)。"""
    index, length = 2, len(data)
    while index + 9 <= length:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xFF:                      # 填充字节
            index += 1
            continue
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7:   # 无长度字段的标记
            index += 2
            continue
        if marker in (0xD9, 0xDA):              # 图像数据开始 / 结束：不再有 SOF
            return None
        segment_length = int.from_bytes(data[index + 2:index + 4], "big")
        if segment_length < 2:
            return None
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5:index + 7], "big")
            width = int.from_bytes(data[index + 7:index + 9], "big")
            return width, height
        index += 2 + segment_length
    return None


def _webp_size(data: bytes) -> Optional[Tuple[int, int]]:
    fmt = data[12:16]
    if fmt == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if fmt == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if fmt == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def parse_image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """从图片字节流头部解析出 (宽, 高)；数据不足或不支持格式时返回 None。"""
    if len(data) < 10:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":                 # PNG
        if len(data) >= 24 and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        return None
    if data[:6] in (b"GIF87a", b"GIF89a"):               # GIF
        return struct.unpack("<HH", data[6:10])
    if data[:2] == b"BM" and len(data) >= 26:            # BMP
        return struct.unpack("<II", data[18:26])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":    # WebP
        return _webp_size(data)
    if data[:2] == b"\xff\xd8":                          # JPEG
        return _jpeg_size(data)
    return None


def remote_image_size(url: str) -> Optional[Tuple[int, int]]:
    """只读取图片头部（Range 请求）识别分辨率，不下载整张图片。"""
    try:
        headers = {"Range": f"bytes=0-{IMAGE_HEADER_BYTES - 1}"}
        with http_get(url, use_proxy=True, headers=headers, stream=True) as resp:
            resp.raise_for_status()
            data = b""
            for chunk in resp.iter_content(chunk_size=8192):
                data += chunk
                size = parse_image_size(data)
                if size is not None:
                    return size
                if len(data) >= IMAGE_HEADER_BYTES:
                    break
    except Exception as e:
        logging.debug("Failed to inspect cover size of %s: %s", url, e)
    return None


def cover_urls(video: Video) -> List[str]:
    """候选视频可用的封面 URL，大的在前。

    niconico 的小图会先换成带 `.L` 的大图，换不到（`download_image` 失败）再退回原 URL，
    这样既不会因为后缀不被支持而丢掉整个 niconico 封面，也不会拿到 130x100 的缩略图。
    **非公開 / 已删的 niconico 稿件不给 `.L`**：那个地址一定取不到（用户 2026-11-02：
    没有稿件的站就不要一个个去试），直接用 VocaDB / nicolog 记着的那张缩略图。
    """
    url = video.thumb_url
    if not url:
        return []
    if not NICO_THUMB_URL_RE.match(url) or nico_unavailable(video):
        return [url]
    return [url + NICO_LARGE_SUFFIX, url]


def cover_pixels(video: Video) -> int:
    """识别封面分辨率（像素总数），不下载整张图片；无法识别时返回 0。

    非公開 / 已删的 niconico 稿件**一个请求都不发**（尺寸探测只会白等），直接按
    「认不出分辨率」处理 —— 排序时排到最后，只有别的来源都没有时才拿它兜底。
    """
    if nico_unavailable(video):
        return 0
    for url in cover_urls(video):
        for name, pixels in YOUTUBE_THUMB_SIZES.items():
            if re.search(r"/" + name + r"\.jpg", url):
                return pixels
        size = remote_image_size(url)
        if size:
            return size[0] * size[1]
    return 0


def order_covers(videos: List[Video]) -> List[Video]:
    """按封面优先级排序：识别到的分辨率越大越优先，认不出时排最后，同样大时 niconico 优先。

    早先是「niconico 无条件排第一」，但 niconico 给的封面并不一定最大：非公開 / 已删视频
    的缩略图只有 130x100（实测 sm43439171 → 条目 column 拿到了裁完只剩 104x53 的封面），
    而同一首歌的 YouTube maxresdefault 有 1280x720。所以改成先比大小，NICO_NICO 只用来打破平局。
    """
    candidates = [v for v in videos if v.thumb_url]
    order = {id(v): index for index, v in enumerate(candidates)}

    def sort_key(video: Video) -> tuple:
        pixels = cover_pixels(video)
        return (pixels <= 0,                                    # 认不出分辨率的排最后
                -pixels,
                0 if video.site == VideoSite.NICO_NICO else 1,   # 一样大时 niconico 优先
                order[id(video)])                               # 其余保持传入顺序

    return sorted(candidates, key=sort_key)


def download_image(url: str, site: VideoSite, index: int) -> Union[Path, None]:
    try:
        temp_dir = get_output_path().joinpath("temp.jpeg")
        logging.info("Downloading cover from " + site.value + " with url " + url)
        download_file(url, temp_dir)
        image_name = get_output_path().joinpath(f"temp{index}.jpeg")
        image_name.unlink(missing_ok=True)
        temp_dir.rename(image_name)
        return image_name
    except Exception as e:
        logging.error("An error occurred while downloading from " + site.value)
        logging.debug("Debugging info: ", exc_info=e)
        return None


# 封面能接受的最低分辨率（像素总数）。
# 下载返回 200 不代表图能用：YouTube 没有 maxresdefault 时会回一张 120x90 的灰图，
# niconico 的缩略图不带 `.L` 时只有 130x100。低于这个值就继续找下一张，都不行才将就。
MIN_COVER_PIXELS = 320 * 180
# 低于这个值就算「偏糊」：niconico 能给的最大缩略图是 360×270（=97200），
# 正好卡在这条线以下 —— 旧曲 / 已删稿的封面基本都是这个尺寸，放进条目一眼就糊
# （用户 2026-10-21 反馈「这图好糊啊」「旧曲的头图还是自己截一个比较好」）。
BLURRY_COVER_PIXELS = 640 * 360


def file_pixels(path: Union[str, Path]) -> int:
    """已下载图片的真实像素总数；读不出来（或根本不是图片）返回 0。"""
    try:
        with Image.open(path) as img:
            return img.width * img.height
    except Exception as e:
        logging.debug("无法识别%s的分辨率：%s", path, e)
        return 0


def usable_pixels(path: Union[str, Path]) -> Tuple[int, float]:
    """已下载封面「真正能用的画面」：(去掉黑边后的像素数, 黑边面积占比)。

    黑边按 `detect_border_box`（**不看** `MAX_CROP_ASPECT`）算，否则「裁完太宽所以不裁」的
    封面会被误判成干净的。读不出图时返回 (0, 0.0)。
    """
    try:
        with Image.open(path) as raw:
            img = raw if raw.mode in ("RGB", "L") else raw.convert("RGB")
            total = img.width * img.height
            y1, y2, x1, x2 = detect_border_box(img)
    except Exception as e:
        logging.debug("无法识别%s的黑边：%s", path, e)
        return 0, 0.0
    usable = (y2 - y1) * (x2 - x1)
    return usable, (1 - usable / total if total else 0.0)


def download_cover_file(video: Video, index: int) -> Optional[Path]:
    """下载某个候选视频的封面：niconico 先试大图（`.L`），失败再退回原 URL。"""
    for url in cover_urls(video):
        image = download_image(url, video.site, index)
        if image is not None:
            return image
    return None


def download_first(videos: List[Video], target: Path) -> Optional[Tuple[Path, Video]]:
    """依次下载封面，返回最合适的一张；全都不行时返回能下的第一张（并记警告）。

    `videos` 需已按优先级排好（见 `order_covers`）。这里之所以还要量一下**下载之后**的
    真实分辨率：服务器给的占位图 / 低清图也会正常返回 200，只看「有没有报错」会把糊图当封面。

    评价标准是**去掉黑边之后真正能用的画面**（`usable_pixels`）：
    - 又够清晰、又不自带黑边的，第一张就收工（绝大多数条目只下载一张）；
    - 自带黑边（黑边占掉 ≥ `HEAVY_BORDER_RATIO` 的面积）的先记着、继续看后面的来源，
      后面有干净的就用干净的 —— 实测《暮光剧场》niconico 那张是「超宽封面美术 + 黑边」，
      裁完只剩 1137x482 的长条，而同一首歌的 YouTube maxresdefault 是干净的 1280x720
      （用户 2026-09-29 报的）。
    - 所有来源都自带黑边时，取**可用画面最多**的那张（保持传入顺序优先）。
    没用上的临时文件都会删掉。
    """
    candidates: List[Tuple[int, float, int, Path, Video]] = []   # (可用像素, 黑边占比, 总像素, 图, 视频)
    for index, video in enumerate(videos):
        image = download_cover_file(video, index)
        if image is None:
            continue
        pixels = file_pixels(image)
        usable, border = usable_pixels(image)
        candidates.append((usable, border, pixels, image, video))
        if pixels < MIN_COVER_PIXELS:
            logging.warning("%s的封面只有%d像素（%s），换一个来源试试",
                            video.site.value, pixels, video.thumb_url)
            continue
        if border >= HEAVY_BORDER_RATIO:
            logging.info("%s的封面自带黑边（占%.0f%%，去掉后只剩%d像素的画面），"
                         "看看有没有更干净的来源", video.site.value, border * 100, usable)
            continue
        break                                   # 又清晰又干净，不用再看后面的
    if not candidates:
        return None
    clear = [c for c in candidates if c[2] >= MIN_COVER_PIXELS and c[1] < HEAVY_BORDER_RATIO]
    ok = [c for c in candidates if c[2] >= MIN_COVER_PIXELS]
    if clear:
        chosen = clear[0]
    elif ok:
        chosen = max(ok, key=lambda c: c[0])    # 都有黑边：可用画面最多的那张
    else:
        chosen = candidates[0]                  # 全是糊图：将就着用最靠前的那张
    for _, _, _, image, _ in candidates:
        if image != chosen[3]:
            image.unlink(missing_ok=True)
    target.unlink(missing_ok=True)
    return chosen[3].rename(target), chosen[4]


def upgrade_blurry_cover(chosen: Tuple[Path, Video],
                         videos: Optional[List[Video]] = None) -> Tuple[Path, Video]:
    """封面偏糊 / 黑边太重时借真浏览器把稿件页上的封面重拍一张，比原图大就用它。

    用户 2026-10-21 的反馈：旧曲的封面常常只有 niconico 的 360×270 缩略图，
    「还是自己截一个比较好」。这里把「自己截」自动化（见 `utils/cover_capture.py`）：
    拍出来的图**必须更大**才替换，拍不到 / 拍小了就保持原样，只在日志里提示一句。

    **看的是「去掉黑边之后的画面」，不是总像素**：用户 2026-11-02 报《720°》——
    niconico 那张 936x720 里 43% 是黑边（可用画面只剩 383760），总像素却过了
    `BLURRY_COVER_PIXELS`，于是被判「不糊」、一次截图都没试；而同一首歌的 B 站页面
    明明能拿到干净的 1280x720。所以「自带黑边」的也照样去拍。

    `videos` 给**全部**候选稿件：拍不了当前这一版的页面（稿件已删 / 页面没有声明封面）时，
    还有 B 站那一版可以试 —— 它的接口被风控时连缩略图都没有，页面上却仍有大图
    （用户 2026-10-23 报「该走 B 站截图却没截」）。
    """
    path, video = chosen
    pixels = file_pixels(path)
    usable, border = usable_pixels(path)
    if pixels >= BLURRY_COVER_PIXELS and border < HEAVY_BORDER_RATIO:
        return chosen
    if border >= HEAVY_BORDER_RATIO:
        logging.info("封面%s有%.0f%%是黑边（可用画面只剩%d像素），试着用浏览器重新取一张",
                     Path(path).name, border * 100, usable)
    else:
        logging.info("封面%s只有%d像素（偏糊），试着用浏览器重新截一张", Path(path).name, pixels)
    better = capture_better_cover(videos or [video], chosen=chosen, target=path)
    if better is None:
        logging.warning("这张封面不够用（%d像素，其中可用%d）又没能重截；"
                        "建议自己截图后在样式页「导入图片」换掉：%s",
                        pixels, usable, video.url)
        return chosen
    logging.info("已改用浏览器截的封面（%d像素，原来是%d）", file_pixels(better[0]), pixels)
    return better


# 同一站点内最多试这么多个稿件（截图一次十几秒；B 站通常只有 1 条）
MAX_CAPTURE_SOURCES = 2
# 截到这么大就算「够清楚了」
CLEAR_COVER_PIXELS = 1280 * 720
# 截图的站点优先级（用户 2026-11-02 指定）：B 站 > YouTube > niconico > 其他。
# 2026-11-06 起**只问最高优先级的那一家**：B 站能取到就不再请求 YouTube / niconico
# （以前三站挨个拍，一轮最多 21 次浏览器截图，白白多等好几分钟）。
CAPTURE_SITE_ORDER: Dict[VideoSite, int] = {
    VideoSite.BILIBILI: 0,
    VideoSite.YOUTUBE: 1,
    VideoSite.NICO_NICO: 2,
}
CAPTURE_SITE_OTHER = 3


def nico_unavailable(video: Optional[Video]) -> bool:
    """这条 niconico 稿件是不是「没有稿件」：非公開 / 已削除（nicolog 记着 `deleted`）。

    用户 2026-11-02：「如果 niconico 没有稿件就不要检测」，并且明确**非公開也算没有稿件**。
    这类页面截图只能白等十几秒（watch 页 404 / 只有一行「非公開」），尺寸探测与 `.L`
    大图也一个个都问不到东西 —— 所以截图不进候选、下载不发请求（见 `cover_urls()`）。
    """
    return getattr(video, "site", None) == VideoSite.NICO_NICO and bool(
        getattr(video, "deleted", False))


def _capture_groups(videos: Sequence[Video],
                    chosen: Optional[Video] = None) -> List[List[Video]]:
    """截图的候选按**站点**分组：`[[B 站…], [YouTube…], [niconico…], [其他…]]`，空组丢掉。

    站点序按用户 2026-11-02 的要求固定成 B 站 > YouTube > niconico（见 `CAPTURE_SITE_ORDER`）：
    B 站排最前面还有两个原因 —— 它的 API 被风控 / 稿件只记在 VocaDB 里时 `thumb_url` 是空的
    （`order_covers()` 会把这类稿件整个滤掉，于是连一次截图机会都没有），而页面上照样有
    1280×720 的封面（用户 2026-10-23 报的那几首就是这样）。
    组内：已删除的稿件排后面，当前用的那一版排**最后**（它的图我们已经下到手了，
    同一页再拍一张基本是同一张）。
    **niconico 的稿件不可用（非公開 / 已删）直接不进候选**（见 `nico_unavailable()`）。
    """
    from utils import cover_capture

    groups: Dict[int, List[Video]] = {}
    seen = set()
    ordered = [video for video in (videos or []) if video is not chosen] + (
        [chosen] if chosen is not None else [])

    def sort_key(video: Video) -> tuple:
        return (1 if getattr(video, "deleted", False) else 0,
                0 if video is not chosen else 1)

    for video in sorted(ordered, key=sort_key):
        if id(video) in seen:
            continue
        if cover_capture.page_url(video) is None or nico_unavailable(video):
            continue
        seen.add(id(video))
        site = CAPTURE_SITE_ORDER.get(getattr(video, "site", None), CAPTURE_SITE_OTHER)
        groups.setdefault(site, []).append(video)
    return [groups[key] for key in sorted(groups)]


def _capture_order(videos: Sequence[Video],
                   chosen: Optional[Video] = None) -> List[Video]:
    """所有候选稿件按截图优先级拍平（`_capture_groups` 的第一个元素在前）。"""
    return [video for group in _capture_groups(videos, chosen) for video in group]


def capture_better_cover(videos: Sequence[Video],
                         chosen: Optional[Tuple[Path, Video]] = None,
                         target: Optional[Path] = None) -> Optional[Tuple[Path, Video]]:
    """借浏览器拍候选稿件的页面封面，返回**比 `chosen` 更耐看**的那张 `(路径, 视频)`。

    `chosen` 是已经下载好的 `(路径, 视频)`，或者 None（一张封面都没下载到时用来兜底）。
    结果一律写成 `target`（不给就写到输出目录的 `cover_capture.png`）；没有任何一张
    比现有封面好时返回 None，**绝不越换越糊**。

    **只问一家**（用户 2026-11-06 要求）：按 `CAPTURE_SITE_ORDER` 从 B 站开始试，
    这一家**取到了候选**（哪怕用户最后没选）就不再请求下一家 —— 以前三站挨个拍，
    一轮能等好几分钟；只有这一家**一张都没取到**（页面 404 / 没有声明封面 / 截图全失败）
    才退到下一站。

    比的是**去掉黑边之后的可用画面**（`usable_pixels`）：带黑边的下载图以前靠总像素
    “以大欺小”，把干净的截图挡在外面（用户 2026-11-02 报《720°》）。
    """
    from utils import cover_capture

    best_usable = usable_pixels(chosen[0])[0] if chosen is not None else 0
    best: Optional[Tuple[int, Path, Video]] = None
    if target is not None:
        base = Path(target)
    else:
        base = get_output_path().joinpath(cover_capture.CAPTURE_NAME)
    for group_index, group in enumerate(_capture_groups(videos, chosen[1] if chosen else None)):
        site_name = group[0].site.value
        logging.info("截图来源：%s（这一家取不到才试下一家）", site_name)
        shot_count = 0
        for index, video in enumerate(group[:MAX_CAPTURE_SOURCES]):
            shot = base.with_name(f"{base.stem}.shot{group_index}.{index}.png")
            try:
                candidates = cover_capture.capture_candidates(video, shot)
            except Exception as e:                          # noqa: BLE001 - 截图只是锦上添花
                logging.debug("借浏览器截图封面失败（%s）：%s", video.site.value, e)
                candidates = []
            if not candidates:
                continue
            shot_count += len(candidates)
            picked = _pick_candidate(candidates, best_usable)
            for path, _label in candidates:
                if path != picked:
                    Path(path).unlink(missing_ok=True)
            if picked is None:
                continue
            usable, border = usable_pixels(picked)
            if best is not None:
                best[1].unlink(missing_ok=True)
            best, best_usable = (usable, Path(picked), video), usable
            if usable >= CLEAR_COVER_PIXELS and border < HEAVY_BORDER_RATIO:
                break
        if shot_count:
            break                      # 这一家已经给出了候选，别再去问下一家
    if best is None:
        return None
    base.parent.mkdir(parents=True, exist_ok=True)
    base.unlink(missing_ok=True)
    return best[1].replace(base), best[2]


def _pick_candidate(candidates: Sequence[Tuple[Path, str]],
                    best_usable: int) -> Optional[Path]:
    """从几张候选封面里挑一张：有界面就让用户挑，没有就挑**可用画面**最多的那张。

    用户 2026-11-02 要求「截几张不同的图再开个窗口让我选」—— 只截一张时常碰上黑屏 /
    过渡帧 / 满屏大字，自动规则看不出「这张适不适合当封面」，只有人能判断。
    「都不合适（保持原图）」返回 None（候选文件由调用方删掉）。

    自动挑的时候按 `usable_pixels()` 算（截图也可能拍进播放器自带的黑边），
    比现有封面（`best_usable`，同样是可用像素）大才作数。
    """
    from utils import ui

    if ui.is_active():
        try:
            picked = ui.ask_cover(candidates)
        except Exception as exc:                            # noqa: BLE001 - 弹窗失败就自动挑
            logging.warning("弹出「选择封面」失败，改按像素挑：%s", exc)
        else:
            return Path(str(picked)) if picked else None
    usable = [(path, usable_pixels(path)[0]) for path, _label in candidates]
    path, pixels = max(usable, key=lambda item: item[1])
    if pixels <= best_usable:
        return None
    return path


def download_thumbnail(videos: List[Video], filename: str,
                       origin: Optional[List[str]] = None) -> Optional[Tuple[Path, Video]]:
    """下载封面图，自动选择分辨率最大的一张。

    选择策略：先按识别到的分辨率（不下载整张图片，只读头部）从大到小排序，
    认不出分辨率的排最后、同样大时 niconico 优先；选中的封面下载后还会复核真实分辨率，
    只有小图（占位图 / 130x100 的 niconico 缩略图）时依次回退到下一张；
    一样清晰时还要看**黑边**（`download_first`），自带黑边（裁完只剩长条）的让位给干净的来源。
    选中的图仍然偏糊（< `BLURRY_COVER_PIXELS`）或**黑边太重**（≥ `HEAVY_BORDER_RATIO`，
    可用画面其实很小）时，再借浏览器把稿件页上的封面重拍一张
    （`upgrade_blurry_cover`，B 站在候选里优先）；**一张都下载不到**时（B 站接口被风控、
    只有没有缩略图的稿件）也照样去拍一张（`capture_better_cover`）。
    """
    target = get_output_path().joinpath(filename)
    from utils import browser_fetch
    # 取数（VocaDB / AtWiki）刚借过浏览器：先把它收回 about:blank，再去下载 / 截图封面 ——
    # 否则窗口里停着 atwiki 页面，用户看到的「本该是 B 站的窗口」就是它（2026-11-02 报）
    browser_fetch.park()
    chosen = download_first(order_covers(videos), target)
    try:
        if chosen is None:
            logging.info("一个封面都没下载到，看看能不能借浏览器直接把稿件页的封面拍下来")
            captured = capture_better_cover(videos, target=target)
            if captured is not None and origin is not None:
                origin.append("capture")
            return captured
        upgraded = upgrade_blurry_cover(chosen, videos)
        if origin is not None and upgraded is not chosen:      # 换上的是截图那张
            origin.append("capture")
        return upgraded
    finally:
        # 封面阶段结束：截图那个标签页也回 about:blank，别留在视频页上
        browser_fetch.park()
