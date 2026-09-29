import logging
import re
import struct
from pathlib import Path
from typing import Union, List, Tuple, Optional

import numpy as np
from PIL import Image, ImageOps

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


# 判定“黑边”的亮度上限（0-255）：只有平均亮度不超过该值的行/列才会被当作纯黑边。
# 这是兜底限制，防止把本来就偏暗的画面误判成黑边。
MAX_BLACK_LEVEL = 40.0
# 单侧最多允许裁掉的比例。若黑边超过该比例，说明判定不可靠（可能是暗色画面而非黑边），
# 此时放弃裁剪，避免误伤封面内容。
MAX_BORDER_RATIO = 0.4

# 裁完之后画面比该比例还宽，说明这张封面本身就是「宽画幅 + 黑边」的整幅设计，
# 黑边不是要去掉的假边。实测《暮光剧场》的 nico 封面内容是 1137x482（2.36:1）、
# 《你嘲笑我那天》是 1280x449（2.85:1）；把黑边裁掉只会得到一条又扁又长的长条
# —— 条目里封面按宽 280 显示（280x119），完全不像封面。这种情况保持原图。
MAX_CROP_ASPECT = 2.0

# 被裁掉的面积占比达到该值时，认为这张封面「自带黑边」（拿它当封面要么是长条、
# 要么得留着黑边）。同样清晰时宁可换一张干净的来源，见 `download_first`。
HEAVY_BORDER_RATIO = 0.25


def otsu_threshold(values: np.ndarray) -> int:
    """用 Otsu 方法在 0-255 灰度上求“黑边 / 画面”的最佳分割阈值。"""
    hist = np.histogram(values, bins=256, range=(0, 256))[0].astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 0
    levels = np.arange(256, dtype=np.float64)
    weight_low = np.cumsum(hist)
    weight_high = total - weight_low
    cum_sum = np.cumsum(hist * levels)
    sum_all = cum_sum[-1]
    valid = (weight_low > 0) & (weight_high > 0)
    if not valid.any():
        return 0
    mean_low = np.divide(cum_sum, weight_low, out=np.zeros(256), where=weight_low > 0)
    mean_high = np.divide(sum_all - cum_sum, weight_high, out=np.zeros(256), where=weight_high > 0)
    between = np.zeros(256)
    between[valid] = weight_low[valid] * weight_high[valid] * (mean_low[valid] - mean_high[valid]) ** 2
    return int(np.argmax(between))


def border_length(profile: np.ndarray, black_level: float) -> Optional[Tuple[int, int]]:
    """从两端向内统计亮度不超过 black_level 的连续元素个数。

    返回 (前端长度, 后端长度)；若任意一侧达到 MAX_BORDER_RATIO 上限，
    说明没有清晰黑边（判定不可靠），返回 None。
    """
    n = len(profile)
    if n == 0:
        return None
    limit = max(1, int(n * MAX_BORDER_RATIO))
    head = 0
    while head < limit and profile[head] <= black_level:
        head += 1
    tail = 0
    while tail < limit and profile[n - 1 - tail] <= black_level:
        tail += 1
    if head >= limit or tail >= limit:
        return None
    return head, tail


def detect_border_box(img: Image.Image) -> Tuple[int, int, int, int]:
    """自动探测封面黑边，返回裁剪区域 (y1, y2, x1, x2)；无可靠黑边时返回整幅图。

    这是**原始**的探测结果（不看 `MAX_CROP_ASPECT`），评价「这张封面有没有黑边」用它；
    真正裁剪请用 `detect_crop_box`。
    """
    gray = np.asarray(ImageOps.grayscale(img), dtype=np.float64)
    height, width = gray.shape
    black_level = min(otsu_threshold(gray), MAX_BLACK_LEVEL)
    rows = border_length(gray.mean(axis=1), black_level)
    cols = border_length(gray.mean(axis=0), black_level)
    if rows is None or cols is None:
        return 0, height, 0, width
    y1, y2 = rows[0], height - rows[1]
    x1, x2 = cols[0], width - cols[1]
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
            logging.info("封面去掉黑边会变成 %dx%d（比 %.1f:1 还宽），"
                         "判定为「宽画幅 + 黑边」的整幅设计，保持原图",
                         x2 - x1, y2 - y1, MAX_CROP_ASPECT)
            return 0, img.height, 0, img.width
    return y1, y2, x1, x2


def remove_black_boarders(image_in: Union[str, Path], image_out: Union[str, Path]) -> None:
    """自动识别并裁掉封面四周的黑边，结果写入 image_out。

    无需手动指定裁剪力度：先用 Otsu 方法估计“黑”的亮度线（并限制在纯黑范围内），
    再从四边向内统计连续黑边长度；判定不可靠时保持原图不变。
    """
    img = Image.open(image_in)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    y1, y2, x1, x2 = detect_crop_box(img)
    if (y1, y2, x1, x2) == (0, img.height, 0, img.width):
        img.save(image_out)
        return
    Image.fromarray(np.asarray(img)[y1:y2, x1:x2]).save(image_out)


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
# 不带后缀的换成 `.L`，否则条目封面会糊成 130x100。
NICO_LARGE_SUFFIX = ".L"
NICO_THUMB_URL_RE = re.compile(
    r"^https?://nicovideo\.cdn\.nimg\.jp/thumbnails/\d+/\d+\.\d+$")


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
    """
    url = video.thumb_url
    if not url:
        return []
    if not NICO_THUMB_URL_RE.match(url):
        return [url]
    return [url + NICO_LARGE_SUFFIX, url]


def cover_pixels(video: Video) -> int:
    """识别封面分辨率（像素总数），不下载整张图片；无法识别时返回 0。"""
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


def file_pixels(path: Union[str, Path]) -> int:
    """已下载图片的真实像素总数；读不出来（或根本不是图片）返回 0。"""
    try:
        with Image.open(path) as img:
            return img.width * img.height
    except Exception as e:
        logging.debug("无法识别 %s 的分辨率：%s", path, e)
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
        logging.debug("无法识别 %s 的黑边：%s", path, e)
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
            logging.warning("%s 的封面只有 %d 像素（%s），换一个来源试试",
                            video.site.value, pixels, video.thumb_url)
            continue
        if border >= HEAVY_BORDER_RATIO:
            logging.info("%s 的封面自带黑边（占 %.0f%%，去掉后只剩 %d 像素的画面），"
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


def download_thumbnail(videos: List[Video], filename: str) -> Optional[Tuple[Path, Video]]:
    """下载封面图，自动选择分辨率最大的一张。

    选择策略：先按识别到的分辨率（不下载整张图片，只读头部）从大到小排序，
    认不出分辨率的排最后、同样大时 niconico 优先；选中的封面下载后还会复核真实分辨率，
    只有小图（占位图 / 130x100 的 niconico 缩略图）时依次回退到下一张；
    一样清晰时还要看**黑边**（`download_first`），自带黑边（裁完只剩长条）的让位给干净的来源。
    """
    target = get_output_path().joinpath(filename)
    return download_first(order_covers(videos), target)
