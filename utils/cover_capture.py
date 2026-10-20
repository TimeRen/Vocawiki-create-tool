"""旧曲封面偏糊时的「自救」：借真浏览器把视频页上的封面重拍一张，比下载的那张小才作数。

为什么需要它（用户 2026-10-21 的反馈）：
    旧曲 —— 尤其已被设为非公開 / 削除的稿件 —— 能拿到的封面往往只有 niconico CDN 的缩略图
    （`…/thumbnails/<id>/<id>.L` = 360×270，更早的记录甚至只有 130×100），放进条目里一眼就糊。
    站上的惯例是「自己截一个头图」，所以这里把这一步自动化：用**用户自己的 Chrome / Edge**
    打开稿件页面（见 `utils/browser_fetch.py`，它本来就能过 Cloudflare），把页面上渲染出来的
    那张封面图按 2 倍设备像素比拍下来。

取舍：
  * 只在**下载到的封面确实偏糊**时才走这条路（`utils/image.download_thumbnail()`），
    而且拍出来必须比原图更大才替换 —— 拍糊了 / 页面只有小图时保持原样，绝不越换越差；
  * 已删除的稿件没有页面可拍（watch 页 404），这条路会自然失败，调用方退回缩略图；
  * 任何失败都只是日志，不影响生成流程。
"""
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from models.video import Video, VideoSite
from utils import browser_fetch

# 各站稿件页的地址（截图要用真实页面，不能用 API）
SITE_PAGES = {
    VideoSite.NICO_NICO: "https://www.nicovideo.jp/watch/{id}",
    VideoSite.BILIBILI: "https://www.bilibili.com/video/{id}",
    VideoSite.YOUTUBE: "https://www.youtube.com/watch?v={id}",
}
# 拍下来的图先存这儿（和下载封面同一个输出目录，出错时好找）
CAPTURE_NAME = "cover_capture.png"


def page_url(video: Video) -> Optional[str]:
    """这张稿件可截图的页面地址；认不出站点时返回 None。"""
    template = SITE_PAGES.get(getattr(video, "site", None))
    identifier = str(getattr(video, "identifier", "") or "").strip()
    if not template or not identifier:
        return None
    return template.format(id=identifier)


def capture_cover(video: Video, target: Optional[Path] = None) -> Optional[Path]:
    """把 `video` 页面上最大的那张封面图截下来，存成 PNG；失败返回 None。

    `target` 不给就写到输出目录的 `cover_capture.png`（每次覆盖，调用方要就自己挪走）。
    """
    url = page_url(video)
    if url is None:
        return None
    if target is None:
        from config.config import get_output_path
        target = Path(get_output_path()).joinpath(CAPTURE_NAME)
    data = browser_fetch.screenshot(url)
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width < 1 or image.height < 1:
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, "PNG")
    except Exception as e:                                  # noqa: BLE001 - 截坏了就当没截
        logging.warning("截图封面写入失败：%s", e)
        return None
    logging.info("已用浏览器把 %s 页面的封面拍成 %dx%d 的图（%s）",
                 video.site.value, image.width, image.height, target)
    return target
