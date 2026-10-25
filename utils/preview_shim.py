"""预览文档里的「小补丁」：让站上靠 JS 才出现的内容在预览里也看得见。

目前处理 `{{bilibiliVideo}}`。站上的模板把播放器交给 `Widget:BilibiliVideo` 的脚本去挂 ——
那段脚本用 `window.RLQ` / jQuery / `mw.config`，预览文档里没有这些，于是
`<div class="bilibili-video-container">` 一直是 `display: none`（Widget 自己的 CSS 就是这么
写的），预览里那块地方看着像「模板没生效」（用户 2026-10-22 报）。

这里不复刻站上的脚本，只补一小段自己的 JS：给每个容器按 `data-id`（BV 号 / av 号）挂
bilibili 官方播放器 iframe。**桌面版预览跑的是 QtWebEngine，它没有 H.264**（实测
`canPlayType('video/mp4; codecs="avc1.42E01E"')` 返回空串，只有 webm），挂上 iframe 也只会
显示 bilibili 的「您当前的浏览器不支持 HTML5 播放器」（用户 2026-10-27 截图确认）——
所以那种情况改成**封面 + 播放按钮**的卡片：封面 / 标题 / UP 主是 `utils/bilibili.py` 在
工作线程里取好的（`cards`），点封面去 bilibili 看，样子跟站上的播放器一致。
取不到封面时才退回一行链接 + 说明。
手机版预览走的是系统浏览器（有 H.264），照常出播放器。

属性一律经 DOM API 读取、用 `setAttribute` / `textContent` 写入，wikitext 里的内容不会变成
JS 代码。
"""
import json
from typing import Any, Dict, Optional


# 只处理 bilibili 容器，别的一概不碰
_MOUNT_JS = """
(function () {
  var H264 = 'video/mp4; codecs="avc1.42E01E"';
  function canPlayH264() {
    try {
      return !!document.createElement("video").canPlayType(H264);
    } catch (e) {
      return false;
    }
  }
  function playerQuery(id) {
    if (/^av\\d+$/i.test(id)) {
      return "aid=" + id.slice(2);
    }
    if (/^BV1[0-9A-Za-z]{9}$/.test(id)) {
      return "bvid=" + id;
    }
    if (/^\\d+$/.test(id)) {
      return "aid=" + id;
    }
    return "";
  }
  function videoHref(id) {
    if (/^av\\d+$/i.test(id)) {
      return "https://www.bilibili.com/video/av" + id.slice(2);
    }
    return "https://www.bilibili.com/video/" + id;
  }
  function posterCard(id, info) {
    var card = document.createElement("a");
    card.className = "vocawiki-bilibili-card";
    card.setAttribute("href", info.url || videoHref(id));
    card.setAttribute("target", "_blank");
    card.setAttribute("rel", "noopener");
    card.style.cssText = "display:block;max-width:100%;border:1px solid #c8ccd1;" +
                         "border-radius:6px;overflow:hidden;background:#000;" +
                         "text-decoration:none;color:inherit;";
    var shot = document.createElement("div");
    shot.style.cssText = "position:relative;display:block;background:#000;line-height:0;";
    var img = document.createElement("img");
    img.setAttribute("src", info.pic);
    img.setAttribute("alt", info.title || id);
    // 跟播放器一样高矮一致（`max-height` + `object-fit`：宽图会左右留黑边，不会顶出一大截）
    img.style.cssText = "display:block;width:100%;max-width:100%;max-height:380px;" +
                        "object-fit:contain;background:#000;";
    shot.appendChild(img);
    var play = document.createElement("span");
    play.textContent = "\\u25B6";
    play.style.cssText = "position:absolute;left:50%;top:50%;margin:-26px 0 0 -26px;" +
                         "width:52px;height:52px;border-radius:50%;" +
                         "background:rgba(0,0,0,0.55);color:#fff;font-size:24px;" +
                         "line-height:52px;text-align:center;";
    shot.appendChild(play);
    card.appendChild(shot);
    var meta = document.createElement("div");
    meta.style.cssText = "background:#f8f9fa;padding:8px 10px;font-size:13px;" +
                         "line-height:1.6;color:#202122;";
    var title = document.createElement("div");
    title.textContent = info.title || id;
    meta.appendChild(title);
    var sub = document.createElement("div");
    sub.style.cssText = "color:#54595d;font-size:12px;margin-top:2px;";
    sub.textContent = [info.owner, "bilibili", id].filter(Boolean).join(" \\u00B7 ");
    meta.appendChild(sub);
    card.appendChild(meta);
    return card;
  }
  function mount(box) {
    var id = (box.getAttribute("data-id") || "").trim();
    var page = (box.getAttribute("data-page") || "1").trim() || "1";
    var query = playerQuery(id);
    if (!query) {
      return;
    }
    var holder = box.querySelector(".bilibili-iframe-container");
    if (!holder) {
      holder = document.createElement("div");
      box.appendChild(holder);
    }
    if (holder.querySelector("iframe") || holder.querySelector(".vocawiki-bilibili-note")) {
      return;
    }
    if (canPlayH264()) {
      var frame = document.createElement("iframe");
      frame.setAttribute("src", "https://player.bilibili.com/player.html?" + query +
                         "&page=" + encodeURIComponent(page) + "&autoplay=0&high_quality=1");
      frame.setAttribute("frameborder", "0");
      frame.setAttribute("scrolling", "no");
      frame.setAttribute("allowfullscreen", "true");
      frame.style.width = "100%";
      frame.style.maxWidth = "100%";
      frame.style.height = "380px";
      frame.style.border = "0";
      holder.appendChild(frame);
    } else {
      var info = (window.VOCAWIKI_BILIBILI || {})[id] || null;
      var note = document.createElement("div");
      note.className = "vocawiki-bilibili-note";
      if (info && info.pic) {
        note.style.cssText = "max-width:100%;";
        note.appendChild(posterCard(id, info));
      } else {
        note.style.cssText = "border:1px solid #c8ccd1;border-radius:6px;padding:10px 12px;" +
                             "background:#f8f9fa;font-size:13px;line-height:1.7;";
        var link = document.createElement("a");
        link.setAttribute("href", videoHref(id));
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener");
        link.textContent = "在 bilibili 观看：" + id;
        note.appendChild(link);
      }
      var hint = document.createElement("div");
      hint.style.cssText = "color:#54595d;font-size:12px;margin-top:4px;line-height:1.6;";
      hint.textContent = info && info.pic
        ? "预览里没法播放（桌面预览的浏览器缺 H.264），点封面去 bilibili 看；条目里是正常的。"
        : "预览用的浏览器不支持 H.264，播放器没法在这里播放（条目里是正常的）。";
      note.appendChild(hint);
      holder.appendChild(note);
    }

    holder.style.display = "block";
    // 站上的 CSS 平时把容器藏起来、等它自己的脚本挂好后加 `exec` 才显示；这里我们自己显示
    box.classList.add("exec");
    box.style.display = "block";
    box.style.width = "100%";
    box.style.maxWidth = "100%";
    // 「宽屏模式 / 显示视频」是站上脚本的开关，我们已经自己渲染了 → 别留着当死按钮
    var toggles = box.querySelectorAll(".bilibili-toggle, .bilibili-widescreen");
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].style.display = "none";
    }
  }
  function run() {
    var boxes = document.querySelectorAll(".bilibili-video-container[data-id]");
    for (var i = 0; i < boxes.length; i++) {
      mount(boxes[i]);
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
"""


def mount_embedded_players(document: str,
                           cards: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """把挂播放器的那段脚本插进预览文档（放在 `</body>` 前，拿不到就接在末尾）。

    `cards` 是 `utils/bilibili.py` 取好的「视频号 -> 封面 / 标题 / UP 主」；没有 H.264 的
    环境（桌面预览）拿它画封面卡片。不给也没关系，那就退回一行链接。
    """
    text = str(document or "")
    if not text:
        return text
    script = _script(cards)
    index = text.lower().rfind("</body")
    if index < 0:
        return text + script
    return text[:index] + script + text[index:]


def _script(cards: Optional[Dict[str, Dict[str, Any]]]) -> str:
    """预览脚本（只有一段 `<script>`）。"""
    return ("<script>var VOCAWIKI_BILIBILI = " + _cards_json(cards) + ";\n"
            + _MOUNT_JS + "</script>")


def _cards_json(cards: Optional[Dict[str, Dict[str, Any]]]) -> str:
    """嵌进脚本的 JSON（`</` 转义掉，免得内容提前结束 `<script>`）。"""
    return json.dumps(cards or {}, ensure_ascii=False).replace("</", "<\\/")
