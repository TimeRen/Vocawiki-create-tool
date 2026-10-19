"""Kivy 主应用：把现有生成流程接到移动端页面。"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.screenmanager import ScreenManager
from kivy.uix.switch import Switch
from kivy.utils import platform as kivy_platform

from mobile import platform, settings_io
from mobile.backend import install_backend
from mobile.screens.common import (COLORS, ScreenBase, apply_theme, button, is_dark,
                                   label, row, section, spacer, switch_row,
                                   text_input)


CJK_FONTS = (
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/DroidSansFallback.ttf",
    "C:/Windows/Fonts/simhei.ttf",
)


def _font_can_render(font_path: str) -> bool:
    """先试着用这个字体画一个汉字：坏字体（截断的 ttc 等）会让 Kivy 在启动时崩掉。"""
    try:
        from kivy.core.text import Label as CoreLabel

        probe = CoreLabel(text="歌", font_name=font_path, font_size=24)
        probe.refresh()
        texture = probe.texture
        return texture is not None and texture.width > 0 and texture.height > 0
    except Exception as e:  # noqa: BLE001 - 字体坏了就换下一个
        logging.warning("字体无法用于渲染：%s（%s）", font_path, e)
        return False


def _register_cjk_font() -> None:
    for path in CJK_FONTS:
        if not Path(path).is_file():
            continue
        if not _font_can_render(path):
            continue
        try:
            LabelBase.register("Roboto", fn_regular=path)
            logging.info("使用中文字体：%s", path)
            return
        except Exception as e:  # noqa: BLE001
            logging.warning("注册字体失败：%s（%s）", path, e)
    logging.warning("没有可用的中文字体，界面里的中文可能显示成方框")


class HomeScreen(ScreenBase):
    def __init__(self, app):
        super().__init__(app, "Vocawiki 条目辅助工具")
        self.add(section("生成内容"))
        self.add(button("生成歌曲条目", lambda: app.start_feature("entry")))
        self.add(button("生成 P主模板", lambda: app.start_feature("producer")))
        self.add(button("生成歌姬模板", lambda: app.start_feature("vocalist")))
        self.add(spacer(4))
        self.add(row(button("设置", lambda: app.open_settings(), variant="secondary"),
                     button("查看日志", lambda: app.open_logs(), variant="secondary")))
        self.status = self.add(label("就绪", color=COLORS["muted"], size=14))

    def set_status(self, text: str) -> None:
        self.status.text = text or ""


class WorkScreen(ScreenBase):
    def __init__(self, app):
        super().__init__(app, "生成中", on_back=lambda: app.cancel_flow())
        self.status = self.add(label("准备中…", color=COLORS["accent"], size=15))
        self.log_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.log_box.bind(minimum_height=lambda box, value: setattr(box, "height", value))
        self.add(self.log_box)
        self.add(button("取消本轮", lambda: app.cancel_flow(), variant="danger"))

    def set_status(self, text: str) -> None:
        self.status.text = text or ""

    def add_log(self, text: str) -> None:
        self.log_box.add_widget(label(text, color=COLORS["muted"], size=12))

    def _cancel(self) -> None:
        self.app.cancel_flow()


class PromptScreen(ScreenBase):
    def __init__(self, app, prompt: str, on_done: Callable[[str], None],
                 checker: Optional[Callable[[str], bool]] = None, multiline: bool = False):
        super().__init__(app, "请回答", on_back=lambda: self._cancel())
        self.on_done = on_done
        self.checker = checker
        self.add(label(prompt, size=16))
        self.input = self.add(text_input(
            multiline=multiline, height=260 if multiline else 48))
        self.error = self.add(label("", color=COLORS["danger"], size=13))
        self.add(button("确定", self._submit))

    def _submit(self) -> None:
        value = self.input.text
        if self.checker is not None:
            try:
                valid = self.checker(value)
            except Exception as e:  # noqa: BLE001 - 校验器本身出错也留在页面上
                self.error.text = str(e)
                return
            if not valid:
                self.error.text = "输入不符合要求，请修改后重试"
                return
        self.app.pop_screen()
        self.on_done(value)

    def _cancel(self) -> None:
        # 返回键表示放弃本轮，而不是把空字符串当成用户输入交给流程。
        self.app.cancel_flow()


class ChoiceScreen(ScreenBase):
    def __init__(self, app, prompt: str, choices, on_done: Callable[[int], None],
                 allow_zero: bool = False):
        super().__init__(app, "请选择", on_back=lambda: self._cancel())
        self.on_done = on_done
        self.add(label(prompt, size=16))
        for index, choice in enumerate(choices, 1):
            self.add(button(f"{index}. {choice}", lambda i=index: self._pick(i)))
        if allow_zero:
            self.add(button("0. 跳过", lambda: self._pick(0), variant="secondary"))

    def _pick(self, index: int) -> None:
        self.app.pop_screen()
        self.on_done(index)

    def _cancel(self) -> None:
        self.app.cancel_flow()


class MultilineScreen(ScreenBase):
    def __init__(self, app, prompt: str, on_done: Callable[[list[str]], None],
                 auto_strip: bool = True):
        super().__init__(app, "粘贴内容", on_back=lambda: self._cancel())
        self.on_done = on_done
        self.auto_strip = auto_strip
        self.add(label(prompt, size=16))
        self.input = self.add(text_input(multiline=True, height=420))
        self.add(button("完成", self._done))

    def _done(self) -> None:
        lines = []
        for line in self.input.text.splitlines():
            value = line.strip() if self.auto_strip else line
            if value:
                lines.append(value)
        self.app.pop_screen()
        self.on_done(lines)

    def _cancel(self) -> None:
        self.app.cancel_flow()


class LogScreen(ScreenBase):
    def __init__(self, app):
        super().__init__(app, "日志", on_back=lambda: app.pop_screen())
        self.box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.box.bind(minimum_height=lambda box, value: setattr(box, "height", value))
        self.add(self.box)
        for line in app.log_lines:
            self.box.add_widget(label(line, color=COLORS["muted"], size=12))
        self.add(button("复制日志", lambda: platform.copy_text("\n".join(app.log_lines))))


class ErrorScreen(ScreenBase):
    def __init__(self, app, message: str):
        super().__init__(app, "出错了", on_back=lambda: app.pop_screen())
        self.add(label(message, color=COLORS["danger"], size=15))
        self.add(button("复制错误", lambda: platform.copy_text(message)))
        self.add(button("返回首页", lambda: app.go_home()))


class DoneScreen(ScreenBase):
    def __init__(self, app, output_dir: Any = None):
        super().__init__(app, "完成", on_back=lambda: app.go_home())
        self.add(label(f"输出目录：{output_dir or platform.app_dir() / 'output'}", size=14))
        target = str(output_dir or platform.app_dir() / "output")
        self.add(button("分享输出目录", lambda: platform.share_file(target)))
        self.add(button("返回首页", lambda: app.go_home()))


class SettingsScreen(ScreenBase):
    def __init__(self, app):
        super().__init__(app, "设置", on_back=lambda: app.pop_screen())
        from config.config import get_config, get_wiki_credentials, save_config_values, save_credentials

        cfg = get_config()
        self.fields = {}
        self.add(section("外观"))
        self.dark_switch = Switch(active=is_dark())
        self.dark_switch.bind(active=self._toggle_dark)
        self.add(switch_row("深色模式", self.dark_switch, "切换后立即生效，记在 config.yaml 的 dark_mode"))
        self.add(section("导入设置文件"))
        self.add(row(button("导入 config.yaml", lambda: self._import(settings_io.CONFIG_NAME),
                            variant="secondary"),
                     button("导入 wiki_credentials.yaml",
                            lambda: self._import(settings_io.CREDENTIALS_NAME),
                            variant="secondary"),
                     height=48, spacing=6))
        self.tip = self.add(label(
            f"从手机里选一份文件，覆盖应用目录下的 config.yaml / wiki_credentials.yaml"
            f"（旧文件自动备份为 .bak）：{platform.app_dir()}", color=COLORS["muted"], size=12))
        self.add(section("Vocawiki"))
        self._add_field("wiki.username", "用户名", get_wiki_credentials()[0])
        self._add_field("wiki.password", "密码 / 机器人密码", get_wiki_credentials()[1], password=True)
        self.add(section("AI"))
        from config.config import get_ai_credentials

        ai = get_ai_credentials()
        self._add_field("ai.api_key", "AI API Key", ai.get("api_key", ""), password=True)
        self._add_field("ai.base_url", "AI Base URL", ai.get("base_url", ""))
        self._add_field("ai.model", "AI 模型", ai.get("model", ""))
        self.add(section("生成选项"))
        self._add_field("wikitext.lyrics_chs_fail_fast", "歌词找不到时直接留空 (true/false)",
                        str(getattr(cfg.wikitext, "lyrics_chs_fail_fast", False)))
        self._add_field("wiki.submit_window", "生成后打开提交页 (true/false)",
                        str(getattr(cfg.wiki, "submit_window", True)))
        self._add_field("image.download_cover", "下载封面 (true/false)",
                        str(getattr(cfg.image, "download_cover", True)))
        self._add_field("wikitext.ai_css", "AI CSS 按钮 (true/false)",
                        str(getattr(cfg.wikitext, "ai_css", True)))
        self._add_field("wikitext.ai_lyrics", "AI 歌词按钮 (true/false)",
                        str(getattr(cfg.wikitext, "ai_lyrics", True)))
        self.add(button("保存设置", self._save))

    def _toggle_dark(self, _switch, active: bool) -> None:
        self.app.apply_theme_and_reopen_settings(bool(active))

    def _import(self, name: str) -> None:
        """选一份 `config.yaml` / `wiki_credentials.yaml` 覆盖应用目录里的同名文件。"""
        self.tip.text = f"正在选择 {name}…"

        def picked(raw, filename):
            if raw is None:
                self.tip.text = "已取消导入"
                return
            ok, message = settings_io.import_settings_bytes(raw, platform.app_dir() / name)
            if ok and filename and filename != name:
                message += f"（来源：{filename}）"
            self.tip.text = message
            if not ok:
                return
            if name == settings_io.CONFIG_NAME:
                self.app.reload_settings()      # 配置换了：重新读一遍（深浅色也跟着变）
            else:
                self.app.set_status("凭据已导入")
                self.app.reopen_settings()

        platform.pick_document(picked)

    def _add_field(self, key: str, title: str, value: str, password: bool = False):
        self.add(label(title, color=COLORS["muted"], size=13))
        widget = text_input(str(value or ""), password=password)
        self.fields[key] = widget
        self.add(widget)

    def _save(self) -> None:
        from config.config import save_config_values, save_credentials

        values = {}
        credentials = {}
        for key, widget in self.fields.items():
            value = widget.text.strip()
            if key.startswith("wiki.username"):
                credentials["username"] = value
            elif key.startswith("wiki.password"):
                credentials["password"] = value
            elif key == "ai.api_key":
                credentials["ai_api_key"] = value
            elif key == "ai.base_url":
                credentials["ai_base_url"] = value
            elif key == "ai.model":
                credentials["ai_model"] = value
            elif value.lower() in ("true", "false"):
                values[key] = value.lower() == "true"
            else:
                values[key] = value
        ok = save_credentials(credentials) if credentials else True
        ok = save_config_values(values) and ok
        self.app.set_status("设置已保存" if ok else "设置保存失败")


class VocawikiApp(App):
    title = "Vocawiki 条目辅助工具"

    def build(self):
        platform.ensure_app_dirs()
        platform.install_exception_logger()
        _register_cjk_font()
        self.log_lines: list[str] = []
        self._flow_running = False
        self._stack = []
        self._prepare_config()               # 先备好 config.yaml，下面才有深色模式可读
        self._apply_theme_from_config()
        self.sm = ScreenManager()
        self.backend = install_backend(self)
        self.home = HomeScreen(self)
        self.sm.add_widget(self.home)
        self._stack = [self.home]
        from kivy.core.window import Window
        Window.softinput_mode = "below_target"
        Window.bind(on_keyboard=self._on_keyboard)
        return self.sm

    # ------------------------------------------------------------------ 主题
    def _apply_theme_from_config(self) -> None:
        dark = False
        try:
            from config.config import get_config

            dark = bool(getattr(get_config(), "dark_mode", False))
        except Exception:  # noqa: BLE001 - 配置读不出来也得能进界面
            logging.warning("读取深色模式设置失败", exc_info=True)
        apply_theme("dark" if dark else "light")
        self._sync_window_color()

    def _sync_window_color(self) -> None:
        try:
            from kivy.core.window import Window
        except Exception:  # noqa: BLE001 - 没有窗口（无头测试）时跳过
            return
        Window.clearcolor = COLORS["bg"]

    def apply_theme_and_reopen_settings(self, dark: bool) -> None:
        """切换亮 / 暗色：颜色是建控件时定下来的，所以重建首页并重开设置页。"""
        from config.config import save_config_values

        if not save_config_values({"dark_mode": bool(dark)}):
            logging.warning("深色模式没能写进配置文件")
        apply_theme("dark" if dark else "light")
        self._sync_window_color()
        self.reopen_settings()

    def reload_settings(self) -> None:
        """导入 config.yaml 之后重新读一遍（深浅色可能也变了），再重建页面。"""
        from config.config import config_path, load_config

        load_config(config_path())
        self._apply_theme_from_config()
        self.reopen_settings()

    def reopen_settings(self) -> None:
        """按当前配色重建首页与设置页 —— 导入文件 / 切换主题都靠它让界面立刻变样。"""
        leaving = list(self._stack[1:])       # 栈底是首页，其余（当前的设置页）都换掉
        old_home = self.home
        self._stack = []
        for screen in leaving:
            self.sm.remove_widget(screen)
        self.sm.remove_widget(old_home)
        self.home = HomeScreen(self)
        self._stack = [self.home]
        self.sm.add_widget(self.home)
        self.sm.current = self.home.name
        self.open_settings()

    def _on_keyboard(self, _window, key, *_args):
        # Android 返回键 / 桌面 Esc：退一层页面，首页不拦截。
        if key in (27, 1001) and len(self._stack) > 1:
            screen = self._stack[-1]
            handler = getattr(screen, "_cancel", None) or getattr(screen, "_close", None)
            if handler is not None:
                handler()
            else:
                self.pop_screen()
            return True
        return False

    # ------------------------------------------------------------------ 配置
    def _prepare_config(self) -> None:
        root = platform.app_dir()
        target = root / "config.yaml"
        credentials = root / "wiki_credentials.yaml"
        if not credentials.exists():
            try:
                from build import write_credentials_template

                write_credentials_template(credentials)
            except Exception:  # noqa: BLE001 - 缺模板时用户仍可在设置页手填
                credentials.write_text('username: ""\npassword: ""\n', encoding="utf-8")
        i18n_target = root / "i18n"
        if not i18n_target.exists():
            source_i18n = Path(__file__).resolve().parent.parent / "i18n"
            if source_i18n.is_dir():
                import shutil

                shutil.copytree(source_i18n, i18n_target)
        created = not target.exists()
        if created:
            candidates = [
                Path(__file__).resolve().parent.parent / "config_simple.yaml",
                Path(os.environ.get("ANDROID_APP_PATH", ".")) / "config_simple.yaml",
            ]
            for source in candidates:
                if source.is_file():
                    target.write_bytes(source.read_bytes())
                    break
        try:
            from config.config import load_config, save_config_values
            load_config(target)
            if created:
                # 移动端默认把生成结果直接带到提交页；用户之后可以在设置里改回去。
                save_config_values({"wiki.submit_window": True,
                                    "image.download_cover": True})
                load_config(target)
        except Exception as e:  # noqa: BLE001
            logging.warning("初始化移动端配置失败：%s", e)

    # ------------------------------------------------------------------ 导航
    def push_screen(self, screen) -> None:
        self.sm.add_widget(screen)
        self._stack.append(screen)
        self._slide("left")                       # 新页面从右往左进来
        self.sm.current = screen.name

    def pop_screen(self) -> None:
        if len(self._stack) <= 1:
            return
        screen = self._stack.pop()
        self._slide("right")                      # 退回旧页面时反方向：从左往右回来
        self.sm.current = self._stack[-1].name
        self._drop_after_transition(screen)

    def go_home(self) -> None:
        if len(self._stack) <= 1:
            self.sm.current = self.home.name
            return
        leaving = self._stack[1:]
        del self._stack[1:]
        self._slide("right")
        self.sm.current = self.home.name
        for screen in leaving:
            self._drop_after_transition(screen)

    def _slide(self, direction: str) -> None:
        """`left` = 新页面从右边滑进来（前进），`right` = 反过来（后退）。"""
        transition = self.sm.transition
        if transition is not None:
            transition.direction = direction

    def _drop_after_transition(self, screen) -> None:
        """等滑动画完再摘掉页面：立刻 remove_widget 就没有退场动画了（硬切）。"""
        transition = self.sm.transition
        delay = float(getattr(transition, "duration", 0.3) or 0.0) if transition else 0.0

        def _drop(_dt):
            if screen is not self.sm.current_screen and screen in self.sm.screens:
                self.sm.remove_widget(screen)

        Clock.schedule_once(_drop, delay)

    # ------------------------------------------------------------------ 流程
    def start_feature(self, key: str) -> None:
        if self._flow_running:
            self.set_status("当前还有一轮生成没有结束")
            return
        self._flow_running = True
        work = WorkScreen(self)
        self.push_screen(work)
        threading.Thread(target=self._run_feature, args=(key,), daemon=True).start()

    def _run_feature(self, key: str) -> None:
        from utils import ui

        ui.begin_run()
        try:
            # Buildozer 把根目录 main.py 当 __main__ 执行；直接 import main 会再执行一遍。
            flow = sys.modules.get("__main__")
            if flow is None or not hasattr(flow, "generate"):
                import main as flow
            if key == "producer":
                result = flow.generate_producer_template()
            elif key == "vocalist":
                result = flow.generate_vocalist_template()
            else:
                result = flow.generate()
        except ui.RunCancelled:
            logging.info("移动端生成已取消")
            return
        except Exception as e:  # noqa: BLE001
            logging.error("移动端生成失败：%s", traceback.format_exc())
            self.report_error(str(e) or e.__class__.__name__)
        else:
            self.report_done(result)
        finally:
            self._flow_running = False

    def cancel_flow(self) -> None:
        from utils import ui

        ui.cancel_run()
        self.backend.cancel_pending()
        self.set_status("已请求取消本轮…")
        self.go_home()

    # ------------------------------------------------------------------ 基础问答
    def prompt_text(self, prompt: str, on_done, checker=None):
        self.push_screen(PromptScreen(self, prompt, on_done, checker))

    def prompt_choices(self, prompt: str, choices, on_done, allow_zero=False):
        self.push_screen(ChoiceScreen(self, prompt, choices, on_done, allow_zero))

    def prompt_multiline(self, prompt: str, on_done, auto_strip=True):
        self.push_screen(MultilineScreen(self, prompt, on_done, auto_strip))

    # ------------------------------------------------------------------ 状态
    def set_status(self, text: str) -> None:
        if self._stack:
            screen = self._stack[-1]
            if hasattr(screen, "set_status"):
                clock_safe(lambda: screen.set_status(text))

    def append_log(self, text: str) -> None:
        self.log_lines.append(str(text))
        if len(self.log_lines) > 500:
            self.log_lines = self.log_lines[-500:]
        if self._stack and hasattr(self._stack[-1], "add_log"):
            clock_safe(lambda: self._stack[-1].add_log(text))

    def report_done(self, output_dir=None) -> None:
        Clock.schedule_once(lambda _dt: self.push_screen(DoneScreen(self, output_dir)), 0)

    def report_error(self, message: str) -> None:
        Clock.schedule_once(lambda _dt: self.push_screen(ErrorScreen(self, message)), 0)

    def open_settings(self) -> None:
        self.push_screen(SettingsScreen(self))

    def open_logs(self) -> None:
        self.push_screen(LogScreen(self))

    # ------------------------------------------------------------------ 编辑器
    def show_style_editor(self, initial_wiki, cover_image, lyrics_hover,
                          cover_file, versions, done):
        from mobile.screens.editors import StyleEditorScreen
        self.push_screen(StyleEditorScreen(self, initial_wiki, cover_image,
                                           lyrics_hover, cover_file, versions, done))

    def show_lyrics_editor(self, api, done):
        from mobile.screens.editors import LyricsEditorScreen
        self.push_screen(LyricsEditorScreen(self, api, done))

    def show_submit_editor(self, api, done):
        from mobile.screens.editors import SubmitEditorScreen
        self.push_screen(SubmitEditorScreen(self, api, done))

    def show_producer_works(self, work, done):
        from mobile.screens.templates import ProducerWorksScreen
        self.push_screen(ProducerWorksScreen(self, work, done))

    def show_producer_style(self, work, done):
        from mobile.screens.templates import ProducerStyleScreen
        self.push_screen(ProducerStyleScreen(self, work, done))

    def show_vocalist_works(self, work, done):
        from mobile.screens.templates import VocalistWorksScreen
        self.push_screen(VocalistWorksScreen(self, work, done))

    def show_vocalist_style(self, payload, done):
        from mobile.screens.templates import VocalistStyleScreen
        self.push_screen(VocalistStyleScreen(self, payload, done))


def clock_safe(callback: Callable[[], None]) -> None:
    """从后台线程更新界面。"""
    try:
        Clock.schedule_once(lambda _dt: callback(), 0)
    except Exception:  # noqa: BLE001
        pass
