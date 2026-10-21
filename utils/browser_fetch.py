"""借**用户自己的 Chrome / Edge** 去取 VocaDB 的 JSON / nicolog 的 HTML / AtWiki 的页面 —— Cloudflare 那堵墙的可行出路。

为什么必须借真浏览器（2026-10-03/04 的实测结论，别再试别的）：

* 内置 PyQtWebEngine 5.15 = Chromium 87：页面能打开但**卡死在挑战页**（60 秒正文只有
  “Performing security verification”）；
* 系统 Chrome `--headless=new`（全新 profile / 甚至先用有界面 Chrome 训练过的 profile）
  **照样被挑战** —— Cloudflare 认得出无界面；
* Python 的 requests 也过不去：缺的是 `cf_clearance` 这个 Cookie（实测带它 200、不带 403）；
* 「起个本机页面让它 fetch」也不行：cf_clearance 是跨站 Cookie，从 127.0.0.1 发起的请求
  **带不上它**（SameSite），会一直被挑战（我试了三轮才想明白）。
* 只有**带界面的真浏览器 + 顶层导航**能过 —— 也就是用户手动做的那个动作。

所以这里做的事情就是「把那一步自动化」：开一个带界面的 Chrome（独立 profile 放输出目录，
窗口停到屏幕外），用 **DevTools 协议**导航到那个 JSON 地址、把页面上的文本读回来。
用户不用贴 Cookie、不用配 UA、不用手动粘 JSON —— 这就是「无脑」；
profile 常驻让 `cf_clearance` 一直有效 —— 这就是「一劳永逸」。

**不卡死**的设计（用户 2026-10-04 特别要求）：

1. 浏览器**一次开、整轮复用**（第一次十来秒，之后每条请求亚秒级）；
2. 每一步都有 deadline（连 WS 都用 `settimeout`），超时就 `taskkill /F /T` 连子进程一起收摊，
   返回 `None` 让调用方退回「给提示 + 手动粘」，**绝不做无限等待**；
3. 进程级单例 + `atexit` 收尾；`VOCAWIKI_NO_BROWSER_FETCH=1`（测试、`--console`）直接不用这条路。
"""

import atexit
import base64
import json
import logging
import os
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

DISABLE_ENV = "VOCAWIKI_NO_BROWSER_FETCH"
PROFILE_DIR_NAME = "vocadb_browser"
# 一条请求最多等这么久（拿到就返回，不用等满）
FETCH_TIMEOUT = 60
# 等浏览器把调试端口开出来
READY_TIMEOUT = 30
# 只允许取这些域名的数据（别被顺手拿去抓别的东西）
# nicolog 也是 Cloudflare 挡在前面的（2026-10-06 实测：直接请求 403「Just a moment...」，
# 非公開稿件的投稿日 / 播放量全取不到 → 条目里没有 card 栏、日期退化成 VocaDB 的
# publishDate），所以一并走真浏览器这条路。
# AtWiki（vocaloid中文歌詞wiki）2026-10-16 也挂上了同一堵墙：requests 拿到的永远是挑战页
# （curl 那样的指纹能过，requests 过不去），歌词页「从链接填充」的 atwiki 支持同样靠它。
ALLOWED_HOSTS = ("vocadb.net", "nicolog.jp", "w.atwiki.jp")
# 截图只拍这些视频站的页面（旧曲封面偏糊时重拍一张，见 `screenshot()`）
SCREENSHOT_HOSTS = ("nicovideo.jp", "youtube.com", "youtu.be", "bilibili.com")

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/microsoft-edge",
)


# ---------------------------------------------------------------- 极简 WebSocket（只用到文本帧）

class _WebSocket:
    """够用的 WebSocket 客户端：连 DevTools 用它就够（文本帧 + 续帧 + ping）。"""

    def __init__(self, url: str, timeout: float = 10):
        parts = urlparse(url)
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        handshake = (f"GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(handshake.encode())
        self.buffer = b""
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("DevTools 握手时连接被关闭")
            self.buffer += chunk
        head, _, self.buffer = self.buffer.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise OSError(f"DevTools 握手失败：{status!r}")

    def _read_exact(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("DevTools 连接被关闭")
            self.buffer += chunk
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        return data

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        message = b""
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            if second & 0x80:                                   # 服务端一般不加掩码
                mask = self._read_exact(4)
                data = self._read_exact(length)
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            else:
                data = self._read_exact(length)
            if opcode == 0x8:
                raise OSError("DevTools 连接被对方关闭")
            if opcode == 0x9:                                   # ping → pong
                self.sock.sendall(bytes([0x8A, 0x80]) + secrets.token_bytes(4))
                continue
            message += data
            if first & 0x80:                                    # FIN
                return message.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------- 页面读取用的 JS / 判据

# 页面正文的 JS 表达式（挑战页上看就是那句 `Just a moment...`）
BODY_TEXT_JS = "document.body ? document.body.innerText : ''"
# 整页 HTML：nicolog 那条路要用它 —— 投稿日 / 播放量藏在 `dt/dd` 与统计表里，
# 而且画图用的 `dataProvider` 快照只在源码里（innerText 看不到）。
PAGE_HTML_JS = "document.documentElement ? document.documentElement.outerHTML : ''"
# Cloudflare 挑战页的可见文字：**按浏览器语言**不同（`Just a moment...` / `请稍候…` / …）。
# 少了任何一句，那一页就会被当成「已经渲染好了」，调用方解析出来是空的（见 `_loaded()`）。
CHALLENGE_MARKERS = (
    "Just a moment",
    "请稍候", "請稍候",
    "正在进行安全验证", "正在进行安全检查", "正在验证",
    "正在进行人机验证", "安全验证",
    "しばらくお待ちください", "セキュリティを確認しています",
    "Verifying you are human", "Checking your browser",
    "Checking if the site connection is secure", "Enable JavaScript and cookies to continue",
    "Vérifiez que vous êtes humain", "Un instant",
    "Einen Moment bitte", "Überprüfung",
    "Un momento", "Verificando",
    "잠시만 기다리십시오",
)
# 挑战页的结构判据（比可见文字可靠：Cloudflare 的文案随语言 / A-B 测试变）。
# `window._cf_chl_opt` 是挑战页才有的配置对象；Turnstile 的隐藏 input 也是。
CHALLENGE_JS = """(() => {
  try {
    if (window._cf_chl_opt) return '1';
    if (document.querySelector('input[name="cf-turnstile-response"], #cf-chl-widget')) return '1';
    const title = String(document.title || '');
    if (title.indexOf('请稍候') >= 0 || title.indexOf('請稍候') >= 0 ||
        title.indexOf('Just a moment') >= 0 || title.indexOf('しばらくお待ちください') >= 0) return '1';
    return '';
  } catch (e) { return ''; }
})()"""


def _loaded(text: str) -> bool:
    """页面（不是挑战页）真的渲染出来了：有正文，而且不是 Cloudflare 那句 `Just a moment...`。

    Cloudflare 的挑战页会**按浏览器语言**换标题：中文的写「请稍候…」、日文「しばらくお待ち
    ください」…… 以前只认英文那一句，中文环境下这层判断形同虚设（2026-10-21 实测
    `w.atwiki.jp/hmiku/search` 被浏览器取回来的就是「请稍候…」挑战页，`_loaded()` 却当成功，
    于是搜索页解析出 0 个链接、日文歌词永远取不到）。
    """
    for marker in CHALLENGE_MARKERS:
        if marker in text:
            return False
    return bool(text)


# 截图（`screenshot()`）：把页面里某个元素「拍」下来当封面 / 参考图。
# 返回元素在**文档坐标**里的位置与尺寸（clip 用的是文档坐标，所以要加滚动量），
# 以及图片的**原始像素**（`naturalWidth/Height`）—— 调用方据此把 `scale` 提到原图分辨率，
# 免得拍出来只是一张被放大的小图。
COVER_RECT_JS = """
(() => {
  const src = (img) => String(img.currentSrc || img.src || '');
  // 只拍**页面自己声明为封面**的那张图（og:image / link[rel=image_src] /
  // link[rel=preload][as=image] / meta[name=thumbnail]）——不按「最大」或「离播放器最近」猜，
  // 否则很容易拍到排行榜卡片（带播放量角标）这种根本不是封面图的东西。
  const hints = [];
  for (const el of document.querySelectorAll(
      'meta[property="og:image"], meta[name="twitter:image"], meta[name="thumbnail"],' +
      'meta[itemprop="thumbnailUrl"]')) {
    // niconico 的 og:image 是**播放器画面**（中间一个播放键、还可能打码），不能当封面
    if (el.content && el.content.indexOf('goptim.video.nimg.jp') < 0) hints.push(el.content);
  }
  for (const link of document.querySelectorAll('link[rel="image_src"],' +
                                               'link[rel="preload"][as="image"]')) {
    if (link.href && link.href.indexOf('goptim.video.nimg.jp') < 0) hints.push(link.href);
  }
  if (!hints.length) return '';
  const matches = (text) => {
    const value = String(text || '');
    return !!value && hints.some((hint) => hint && value.indexOf(hint) >= 0);
  };
  const box = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) return null;
    return {x: rect.left + window.scrollX, y: rect.top + window.scrollY,
            width: rect.width, height: rect.height};
  };
  // ① <img src=声明的封面>
  for (const img of document.images) {
    if (img.complete && img.naturalWidth >= 200 && matches(src(img))) {
      const rect = box(img);
      if (rect) {
        return JSON.stringify(Object.assign(rect, {
          naturalWidth: img.naturalWidth, naturalHeight: img.naturalHeight, src: src(img)}));
      }
    }
  }
  // ② <video poster=声明的封面>
  for (const video of document.querySelectorAll('video[poster]')) {
    if (!matches(video.poster)) continue;
    const rect = box(video);
    if (rect) {
      return JSON.stringify(Object.assign(rect, {naturalWidth: 0, naturalHeight: 0,
                                                 src: video.poster}));
    }
  }
  // ③ 背景图（不少站把封面画在 div 的 background-image 上）
  for (const el of document.querySelectorAll(
      '[class*="poster"], [class*="cover"], [class*="thumb"], [class*="thumbnail"],' +
      '[style*="background-image"]')) {
    const style = getComputedStyle(el).backgroundImage || '';
    if (!matches(style)) continue;
    const rect = box(el);
    if (rect) {
      return JSON.stringify(Object.assign(rect, {naturalWidth: 0, naturalHeight: 0, src: style}));
    }
  }
  return '';
})()
"""
# 截图时最多放大到几倍（再大只是把浏览器里的小图插值放大，没有新细节）
MAX_CAPTURE_SCALE = 3.0


# ---------------------------------------------------------------- 浏览器会话

class _Session:
    def __init__(self, browser: str, profile: Path):
        self.browser = browser
        self.profile = profile
        self.process: Optional[subprocess.Popen] = None
        self.port = 0
        self.ws: Optional[_WebSocket] = None
        self.message_id = 0
        self.target_url = ""

    # —— 启动 ——
    def launch(self) -> bool:
        self.profile.mkdir(parents=True, exist_ok=True)
        self.port = _free_port()
        flags = [f"--user-data-dir={self.profile}", f"--remote-debugging-port={self.port}",
                 "--remote-allow-origins=*", "--no-first-run", "--no-default-browser-check",
                 "--disable-gpu", "--disable-extensions", "--disable-background-networking",
                 "--window-position=-32000,-32000", "--window-size=900,700",
                 # ⚠️ 窗口在屏幕外会被 Chrome 当「后台」节流，人机校验的 JS 就跑不完
                 "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                 "--disable-backgrounding-occluded-windows", "about:blank"]
        self.process = subprocess.Popen([self.browser, *flags], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            if self.process.poll() is not None:
                logging.warning("取数用的浏览器刚启动就退出了。")
                return False
            page = self._first_page_target()
            if page:
                try:
                    self.ws = _WebSocket(page, timeout=15)
                    self._call("Page.enable")
                    self._call("Runtime.enable")
                    return True
                except (OSError, ValueError, TimeoutError) as exc:
                    logging.debug("连 DevTools 失败（还会再试）：%s", exc)
                    self.ws = None
            time.sleep(1)
        logging.warning("取数用的浏览器没能连上 DevTools（%s 秒超时）。", READY_TIMEOUT)
        return False

    def _first_page_target(self) -> Optional[str]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=3) as fh:
                targets = json.loads(fh.read().decode("utf-8"))
        except Exception:                                  # noqa: BLE001 - 还没起来
            return None
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return target["webSocketDebuggerUrl"]
        return None

    # —— CDP 调用 ——
    def _call(self, method: str, params: Optional[dict] = None, timeout: float = 15):
        if self.ws is None:
            raise OSError("没有 DevTools 连接")
        self.message_id += 1
        message_id = self.message_id
        self.ws.send_text(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.ws.sock.settimeout(max(0.5, deadline - time.time()))
            try:
                message = json.loads(self.ws.recv_text())
            except (OSError, ValueError) as exc:
                raise OSError(f"DevTools 通信失败：{exc}") from exc
            if message.get("id") == message_id:
                if "error" in message:
                    raise OSError(f"{method} 出错：{message['error'].get('message')}")
                return message.get("result")
        raise TimeoutError(f"{method} 超时")

    def _evaluate(self, expression: str, timeout: float = 10) -> str:
        result = self._call("Runtime.evaluate",
                            {"expression": expression, "returnByValue": True}, timeout=timeout)
        return str(((result or {}).get("result") or {}).get("value") or "")

    # —— 取数：导航过去 → 等页面就绪 → 把正文（或整页 HTML）读回来 ——
    def _challenge_page(self) -> bool:
        """当前停在的页面是不是 Cloudflare 挑战页（结构判据，见 `CHALLENGE_JS`）。"""
        try:
            return self._evaluate(CHALLENGE_JS).strip() == "1"
        except (OSError, TimeoutError) as exc:
            logging.debug("读挑战页判据失败（可能正在导航）：%s", exc)
            return False

    def fetch(self, url: str, timeout: float, ready=None, extract=None) -> Optional[str]:
        """`ready` 判「页面加载完了没」（看 innerText），`extract` 决定最后取回什么。

        默认是 VocaDB 那套：等一段以 `{` 开头的 JSON，取回它本身。
        nicolog 那种要给的是 HTML，就传 `ready=_loaded`、`extract=PAGE_HTML_JS`。
        """
        if self.ws is None:
            return None
        if url != self.target_url:
            self._call("Page.navigate", {"url": url}, timeout=15)
            self.target_url = url
        ready = ready or (lambda text: text.startswith("{"))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                text = self._evaluate(BODY_TEXT_JS).strip()
            except (OSError, TimeoutError) as exc:
                logging.debug("读页面文本失败（可能正在导航）：%s", exc)
                text = ""
            if ready(text) and not self._challenge_page():
                return (self._evaluate(extract).strip() if extract else text)
            time.sleep(2)                       # 挑战页自己会跳回原地址，等它跳完再读
        return None

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            _kill_tree(process.pid)

    # —— 截图：导航过去 → 等元素出现 → 按元素矩形拍一张 PNG ——
    def screenshot(self, url: str, script: str = COVER_RECT_JS, timeout: float = FETCH_TIMEOUT,
                   scale: float = 1.0) -> Optional[bytes]:
        """把页面里 `script` 选中的元素截图下来（PNG 字节）；失败返回 `None`。

        `script` 要返回 `{"x","y","width","height"[,"naturalWidth","naturalHeight"]}` 的 JSON
        （文档坐标，见 `COVER_RECT_JS`）。给了原图尺寸时自动把 `scale` 提到原图分辨率
        （最多 `MAX_CAPTURE_SCALE` 倍）—— 只把浏览器里的小图插值放大没有意义，
        所以默认就是 1 倍、绝不凭空放大。
        """
        if self.ws is None:
            return None
        if url != self.target_url:
            self._call("Page.navigate", {"url": url}, timeout=15)
            self.target_url = url
        deadline = time.time() + timeout
        rect = ""
        while time.time() < deadline:
            try:
                text = self._evaluate(BODY_TEXT_JS).strip()
                if _loaded(text) and not self._challenge_page():
                    rect = self._evaluate(script).strip()
                    if rect:
                        break
            except (OSError, TimeoutError) as exc:
                logging.debug("截图上取元素位置失败（可能还在加载）：%s", exc)
            time.sleep(1)
        if not rect:
            return None
        try:
            box = json.loads(rect)
            width, height = float(box["width"]), float(box["height"])
        except (ValueError, KeyError, TypeError):
            return None
        if width < 1 or height < 1:
            return None
        try:
            natural = float(box.get("naturalWidth") or 0) / width
        except (TypeError, ValueError, ZeroDivisionError):
            natural = 1.0
        scale = max(1.0, min(MAX_CAPTURE_SCALE, max(float(scale), natural)))
        try:
            result = self._call("Page.captureScreenshot", {
                "format": "png",
                "captureBeyondViewport": True,
                "clip": {"x": float(box["x"]), "y": float(box["y"]),
                         "width": width, "height": height, "scale": scale}}, timeout=30)
        except (OSError, TimeoutError) as exc:
            logging.warning("浏览器截图失败：%s", exc)
            return None
        data = (result or {}).get("data")
        if not data:
            return None
        try:
            return base64.b64decode(data)
        except (ValueError, TypeError):
            return None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _kill_tree(pid: int) -> None:
    """连子进程一起杀掉（Chrome 会开一堆小进程，只杀父进程收不干净）。"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        else:
            os.kill(pid, 9)
    except Exception as exc:                               # noqa: BLE001
        logging.debug("结束浏览器进程 %s 失败：%s", pid, exc)


_session: Optional[_Session] = None
_session_lock = threading.Lock()
# 刚失败过就别马上再试：连续几条地址各等满 60 秒会把人等死（`find_translation_page()`
# 一次要问 2~4 个搜索地址）。60 秒后自动恢复，成功一次立刻清零。
FAILURE_GRACE = 60
_failed_at = 0.0


# ---------------------------------------------------------------- 对外接口

def find_browser() -> Optional[str]:
    for candidate in CHROME_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    """现在能不能借真浏览器取数（找得到 Chrome/Edge、没被开关关掉、刚没失败过）。"""
    if os.environ.get(DISABLE_ENV):
        return False
    if time.time() - _failed_at < FAILURE_GRACE:
        return False                          # 刚失败过：先别让调用方一条条等超时
    return find_browser() is not None


def profile_dir() -> Optional[Path]:
    try:
        from config.config import get_output_path
        return Path(get_output_path()) / PROFILE_DIR_NAME
    except Exception:                                      # noqa: BLE001
        return None


def _get_session() -> Optional[_Session]:
    """拿到（必要时创建）浏览器会话；失败返回 `None`。每一步都有 deadline，不会挂死。"""
    global _session
    browser, profile = find_browser(), profile_dir()
    if browser is None or profile is None:
        return None
    with _session_lock:
        if _session is not None and _session.process is not None \
                and _session.process.poll() is None and _session.ws is not None:
            return _session
        session = _Session(browser, profile)
        if not session.launch():
            session.close()
            return None
        _session = session
        return _session


def _allowed(url: str) -> bool:
    """只认白名单里的站点（别被顺手拿去抓别的东西）。"""
    return (urlparse(url).hostname or "").endswith(ALLOWED_HOSTS)


# 「用系统自带的 curl 取一次」这条捷径的参数
CURL_TIMEOUT = 25
CURL_NAMES = ("curl", "curl.exe")
# 用 curl 取时冒充的浏览器（AtWiki 对 `requests` 的 UA 与 TLS 指纹都不认）
CURL_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _curl_program() -> Optional[str]:
    for name in CURL_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def curl_html(url: str, timeout: float = CURL_TIMEOUT) -> Optional[str]:
    """用系统自带的 `curl` 取整页 HTML（不开浏览器）；取不到 / 还是挑战页就返回 `None`。

    2026-10-21 实测：AtWiki 的 Cloudflare **只挡 `requests` 的 TLS 指纹** —— 同一个地址
    `requests` 403「Just a moment…」，`curl` 直接 200（`browser_fetch` 顶上那段说明里也
    记着这件事）。`/list?keyword=…` 这类普通列表页因此能秒回，不必为了它开一个真浏览器
    （那要十几秒，还可能卡在人机验证上，见 `_challenge_page()`）。
    """
    if os.environ.get(DISABLE_ENV) or not _allowed(url):
        return None
    program = _curl_program()
    if program is None:
        return None
    command = [program, "-sS", "--compressed", "--max-time", str(int(timeout)),
               "-A", CURL_USER_AGENT]
    proxy = _proxy_url()
    if proxy:
        command += ["--proxy", proxy]
    command.append(url)
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout + 5,
                              # 打包成窗口程序后别闪出控制台窗口（同 `utils/updater.py`）
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        logging.debug("curl 取 %s 失败：%s", url, exc)
        return None
    if done.returncode != 0 or not done.stdout:
        logging.debug("curl 取 %s 失败（退出码 %s）", url, done.returncode)
        return None
    text = done.stdout.decode("utf-8", "replace")
    if not _loaded(text):
        logging.info("curl 拿到的还是 Cloudflare 挑战页：%s", url)
        return None
    return text


def _proxy_url() -> str:
    """配置里写了代理就用它（与 `http_get(use_proxy=True)` 同一份配置）。"""
    try:
        from config.config import get_config
        return str(get_config().proxies or "")
    except Exception:                                      # noqa: BLE001 - 读不到配置就不用代理
        return ""


def _screenshot_allowed(url: str) -> bool:
    """截图只拍视频站（旧曲封面偏糊时重拍，见 `SCREENSHOT_HOSTS`）。"""
    return (urlparse(url).hostname or "").endswith(SCREENSHOT_HOSTS)


def _fetch_with_browser(url: str, timeout: float, ready, extract,
                        expectation: str) -> Optional[str]:
    """`fetch_text()` / `fetch_html()` 共用的那一层：检查开关与域名 → 借浏览器取 → 收摊。"""
    if not available():
        return None
    if not _allowed(url):
        logging.warning("拒绝用浏览器取未列入白名单的地址：%s", url)
        return None
    try:
        session = _get_session()
        text = None if session is None else session.fetch(url, timeout, ready=ready,
                                                          extract=extract)
    except Exception as exc:                               # noqa: BLE001 - 任何意外都退回老路
        logging.warning("借浏览器取数失败：%s", exc)
        text = None
    if text is None:
        logging.warning("浏览器没能取到（%s 秒内%s）：%s", timeout, expectation, url)
        _mark_failed()
        shutdown()
    else:
        _reset_failure()
    return text


def _mark_failed() -> None:
    global _failed_at
    _failed_at = time.time()


def _reset_failure() -> None:
    global _failed_at
    _failed_at = 0.0


def fetch_text(url: str, timeout: float = FETCH_TIMEOUT) -> Optional[str]:
    """借真浏览器取 `url` 的正文（VocaDB 的 JSON）。失败返回 `None`：不抛异常、不挂死。"""
    return _fetch_with_browser(url, timeout, None, None, "页面一直不是 JSON")


def fetch_html(url: str, timeout: float = FETCH_TIMEOUT) -> Optional[str]:
    """取 `url` 的**整页 HTML**（nicolog / AtWiki 这种被 Cloudflare 挑战的站用）。

    先试系统自带的 `curl`（快、不用开窗口，见 `curl_html()`）；它拿不到的地址再借真浏览器
    （等到挑战页过去（`_loaded`）就把 `document.documentElement.outerHTML` 交回来）。
    """
    text = curl_html(url)
    if text is not None:
        return text
    return _fetch_with_browser(url, timeout, _loaded, PAGE_HTML_JS, "页面没渲染出来")


def screenshot(url: str, script: str = COVER_RECT_JS, timeout: float = FETCH_TIMEOUT,
               scale: float = 1.0) -> Optional[bytes]:
    """借真浏览器把页面上某个元素「拍」成 PNG 字节（拿不到就 `None`）。

    只给**视频页**用（`SCREENSHOT_HOSTS`）：旧曲的封面常常只有 niconico 的
    360×270 缩略图，页面里渲染出来的那张更大时，就靠这一手重新拍一张当封面
    （用户 2026-10-21 反馈「旧曲的头图还是自己截一个比较好」）。
    """
    if not available():
        return None
    if not _screenshot_allowed(url):
        logging.warning("拒绝用浏览器截图未列入白名单的地址：%s", url)
        return None
    try:
        session = _get_session()
        data = None if session is None else session.screenshot(url, script, timeout, scale)
    except Exception as exc:                               # noqa: BLE001 - 意外就退回原图
        logging.warning("借浏览器截图失败：%s", exc)
        data = None
    if data is None:
        logging.info("浏览器没能把 %s 的封面拍下来（%s 秒内）", url, timeout)
    else:
        _reset_failure()
    return data


def shutdown() -> None:
    """收掉浏览器（流程结束、测试收尾都调用；多次调用无害）。"""
    global _session
    with _session_lock:
        session, _session = _session, None
    if session is not None:
        session.close()


atexit.register(shutdown)
