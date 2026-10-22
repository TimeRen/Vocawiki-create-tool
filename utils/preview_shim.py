"""预览文档里的「小补丁」：让站上靠 JS 才出现的内容在预览里也看得见。

目前处理 `{{bilibiliVideo}}`。站上的模板把播放器交给 `Widget:BilibiliVideo` 的脚本去挂 ——
那段脚本用 `window.RLQ` / jQuery / `mw.config`，预览文档里没有这些，于是
`<div class="bilibili-video-container">` 一直是 `display: none`（Widget 自己的 CSS 就是这么
写的），预览里那块地方看着像「模板没生效」（用户 2026-10-22 报）。

这里不复刻站上的脚本，只补一小段自己的 JS：给每个容器按 `data-id`（BV 号 / av 号）挂
bilibili 官方播放器 iframe。**桌面版预览跑的是 QtWebEngine，它没有 H.264**（实测
`canPlayType('video/mp4; codecs="avc1.42E01E"')` 返回空串，只有 webm），挂上 iframe 也只会
显示「浏览器不支持 HTML5 播放器」，所以那种情况改成一张说明卡片 + 视频链接。
手机版预览走的是系统浏览器（有 H.264），照常出播放器。

属性一律经 DOM API 读取、用 `setAttribute` 写入，wikitext 里的内容不会变成 JS 代码。
"""

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
      var note = document.createElement("div");
      note.className = "vocawiki-bilibili-note";
      note.style.cssText = "border:1px solid #c8ccd1;border-radius:6px;padding:10px 12px;" +
                           "background:#f8f9fa;font-size:13px;line-height:1.7;";
      var link = document.createElement("a");
      link.setAttribute("href", videoHref(id));
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noopener");
      link.textContent = "在 bilibili 观看：" + id;
      note.appendChild(link);
      var hint = document.createElement("div");
      hint.style.cssText = "color:#54595d;font-size:12px;margin-top:4px;";
      hint.textContent = "预览用的浏览器不支持 H.264，播放器没法在这里播放（条目里是正常的）。";
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

_SCRIPT = "<script>" + _MOUNT_JS + "</script>"


def mount_embedded_players(document: str) -> str:
    """把挂播放器的那段脚本插进预览文档（放在 `</body>` 前，拿不到就接在末尾）。"""
    text = str(document or "")
    if not text:
        return text
    index = text.lower().rfind("</body")
    if index < 0:
        return text + _SCRIPT
    return text[:index] + _SCRIPT + text[index:]
