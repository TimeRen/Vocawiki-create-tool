"""旧曲封面偏糊时的「自救」：借真浏览器把视频页上的封面重取一张，比下载的那张小才作数。

为什么需要它（用户 2026-10-21 的反馈）：
    旧曲 —— 尤其已被设为非公開 / 削除的稿件 —— 能拿到的封面往往只有 niconico CDN 的缩略图
    （`…/thumbnails/<id>/<id>.L` = 360×270，更早的记录甚至只有 130×100），放进条目里一眼就糊。
    站上的惯例是「自己截一个头图」，所以这里把这一步自动化：用**用户自己的 Chrome / Edge**
    打开稿件页面（见 `utils/browser_fetch.py`，它本来就能过 Cloudflare）。

两条取图的路（先 ① 再 ②）：
  ① **页面声明的封面地址**（`og:image` / `image_src` / `preload[as=image]`）→ 去掉 CDN 的
     缩放后缀（B 站 `…jpg@1200w_630h`）把**原图**下回来。这是 2026-10-23 补的：实测 B 站
     页面上根本没有渲染出那张封面 <img>，截图必然拍空，而 `og:image` 一直都在；
  ② 把页面上**渲染出来的那张封面图**截图（niconico 的 `og:image` 是播放器画面，已在
     `COVER_RECT_JS` 里过滤掉，只能走这条）。

取舍：
  * 只在**下载到的封面确实偏糊**、或**一张都没下载到**时才走这条路
    （`utils/image.download_thumbnail()`），而且必须比原图更大才替换 —— 绝不越换越差；
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
from utils.helpers import http_get

# 各站稿件页的地址（截图要用真实页面，不能用 API）
SITE_PAGES = {
    VideoSite.NICO_NICO: "https://www.nicovideo.jp/watch/{id}",
    VideoSite.BILIBILI: "https://www.bilibili.com/video/{id}",
    VideoSite.YOUTUBE: "https://www.youtube.com/watch?v={id}",
}
# 拍下来的图先存这儿（和下载封面同一个输出目录，出错时好找）
CAPTURE_NAME = "cover_capture.png"
# 一张封面最多等这么久：封面页通常 3~10 秒就渲染好，等满 60 秒（取数用的默认值）太浪费 ——
# 一张下载不到 / 拍不到的页面要连试几个候选，每个都等一分钟会把生成流程拖成好几分钟。
CAPTURE_TIMEOUT = 25


def page_url(video: Video) -> Optional[str]:
    """这张稿件可截图的页面地址；认不出站点时返回 None。"""
    template = SITE_PAGES.get(getattr(video, "site", None))
    identifier = str(getattr(video, "identifier", "") or "").strip()
    if not template or not identifier:
        return None
    return template.format(id=identifier)


def _looks_like_cover_url(url: str) -> bool:
    return bool(url) and "goptim.video.nimg.jp" not in url


COVER_URL_JS = """(() => {
  // 页面自己声明的**封面地址**（与 `COVER_RECT_JS` 同一套取法，只是不要元素、只要地址）。
  // 拍不到那张图时用它直接把原图下回来：B 站就是这种情况 —— og:image 给的是
  // `…/archive/xxx.jpg@1200w_630h`（带缩放后缀），而页面上根本没有渲染出这张 <img>。
  const ok = (value) => !!value && value.indexOf('goptim.video.nimg.jp') < 0;
  for (const selector of ['meta[property="og:image"]', 'meta[name="twitter:image"]',
                          'meta[name="thumbnail"]', 'meta[name="itemprop"]']) {
    const el = document.querySelector(selector);
    if (el && ok(el.content)) return String(el.content);
  }
  for (const selector of ['meta[itemprop="thumbnailUrl"]']) {
    const el = document.querySelector(selector);
    if (el && ok(el.content)) return String(el.content);
  }
  for (const link of document.querySelectorAll('link[rel="image_src"],' +
                                               'link[rel="preload"][as="image"]')) {
    if (ok(link.href)) return String(link.href);
  }
  return '';
})()"""


def full_size_url(url: str) -> str:
    """去掉 CDN 的缩放后缀，取**原图**：B 站 `…jpg@1200w_630h`、`…webp@96w` 都是缩略变体。"""
    return str(url or "").split("@", 1)[0]


def _absolute(url: str) -> str:
    """页面里偶尔写协议相对的地址（`//i0.hdslb.com/...`）。"""
    if url.startswith("//"):
        return "https:" + url
    return url


def _declared_cover_bytes(url: str, timeout: float) -> Optional[bytes]:
    """问浏览器要「页面声明的封面地址」，再把**原图**下回来；拿不到返回 None。

    比截图更好也更快：截图最多只能拿到页面上渲染出来的尺寸（`screenshot()` 不会凭空放大），
    而声明的地址去掉 `@…` 后缀就是 CDN 上的原图（B 站实测 `og:image` 是 `@1200w_630h`，
    原图 1280×720 更完整）。
    """
    declared = browser_fetch.image_url(url, COVER_URL_JS, timeout)
    if not declared:
        return None
    target = _absolute(full_size_url(declared))
    logging.info("用页面声明的封面地址取原图：%s", target)
    try:
        response = http_get(target, use_proxy=True, timeout=timeout)
        response.raise_for_status()
        content = response.content
    except Exception as e:                                  # noqa: BLE001 - 取不到再退截图
        logging.warning("下载页面声明的封面失败（%s）：%s", target, e)
        return None
    return content or None


def capture_cover(video: Video, target: Optional[Path] = None,
                  timeout: float = CAPTURE_TIMEOUT) -> Optional[Path]:
    """取 `video` 页面上的封面，存成 PNG；取不到返回 None。

    `target` 不给就写到输出目录的 `cover_capture.png`（每次覆盖，调用方要就自己挪走）。

    顺序：**页面声明的封面地址**（下载原图，B 站只有这条路走得通）→ 把页面上那张图**截图**
    （niconico 的 og:image 是播放器画面、已被过滤，只能拍页面里那张 <img>）。
    """
    url = page_url(video)
    if url is None:
        return None
    if target is None:
        from config.config import get_output_path
        target = Path(get_output_path()).joinpath(CAPTURE_NAME)
    data = _declared_cover_bytes(url, timeout)
    if not data:
        data = browser_fetch.screenshot(url, timeout=timeout)
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
