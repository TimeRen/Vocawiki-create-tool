"""样式 / 歌词 / 提交三个编辑器页。"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from kivy.metrics import dp
from kivy.uix.image import Image
from kivy.uix.switch import Switch

from mobile import platform, preview
from mobile.screens.common import (COLORS, ScreenBase, button, label, row,
                                   section, spacer, switch_row, text_input)


class StyleEditorScreen(ScreenBase):
    def __init__(self, app, initial_wiki, cover_image, lyrics_hover,
                 cover_file, versions, done):
        super().__init__(app, "样式", on_back=lambda: self._cancel())
        self.done = done
        self.cover_file = Path(cover_file) if cover_file else None
        self.cover_path = Path(cover_image) if cover_image else None
        self.versions = list(versions or [])
        self.version_inputs = {}

        from utils.color_editor import EditorApi

        self.api = EditorApi(self.cover_path)
        self.add(section("颜色 / CSS 参数（按「|参数 = 值」编辑）"))
        self.text = self.add(text_input(initial_wiki or "", multiline=True, height=340))
        hover_row = switch_row("LyricsKai/hover", Switch(active=bool(lyrics_hover)),
                               "歌词里加 hover 注音")
        self.hover = hover_row.switch
        self.add(hover_row)

        self.cover_label = self.add(label(self._cover_text(), color=COLORS["muted"], size=13))
        self.preview = None
        self._refresh_preview()
        self.add(row(button("导入封面", self._pick_cover, variant="secondary"),
                     button("AI 超分", self._upscale, variant="secondary"),
                     button("AI CSS", self._ai_css, variant="secondary"),
                     height=48, spacing=6))

        if self.versions:
            self.add(section("其他版本（只填该版本的 Songbox 颜色）"))
            for item in self.versions:
                key = str(item.get("key") or "")
                if not key:
                    continue
                self.add(label(str(item.get("label") or key), color=COLORS["muted"], size=13))
                self.version_inputs[key] = self.add(
                    text_input(str(item.get("colors") or ""), multiline=True, height=120))

        self.add(spacer(6))
        self.add(row(button("取消", self._cancel, variant="secondary"),
                     button("保存并继续", self._save), height=48, spacing=6))

    def _cover_text(self) -> str:
        return f"当前封面：{self.cover_path.name}" if self.cover_path else "未选择封面"

    def _refresh_preview(self) -> None:
        if self.preview is not None:
            self.body.remove_widget(self.preview)
            self.preview = None
        if self.cover_path and self.cover_path.is_file():
            try:
                self.preview = Image(source=str(self.cover_path), size_hint_y=None,
                                     height=dp(180))
                self.body.add_widget(self.preview, index=2)
            except Exception:  # noqa: BLE001
                self.preview = None

    def _pick_cover(self) -> None:
        def picked(path):
            if not path:
                return
            self.cover_path = Path(path)
            self.api.set_cover_image(self.cover_path)
            self.cover_label.text = self._cover_text()
            self._refresh_preview()

        platform.pick_file(picked)

    def _upscale(self) -> None:
        if not self.cover_path:
            self.app.set_status("先导入封面")
            return
        self.cover_label.text = "正在在线超分…"
        threading.Thread(target=self._run_upscale, daemon=True).start()

    def _run_upscale(self) -> None:
        result = self.api.ai_upscale()
        def apply():
            if result.get("ok"):
                self.cover_path = Path(result["path"])
                self.api.set_cover_image(self.cover_path)
                self.cover_label.text = str(result.get("message") or "超分完成")
                self._refresh_preview()
            else:
                self.cover_label.text = f"超分失败：{result.get('error')}"
        self.app_clock(apply)

    def _ai_css(self) -> None:
        # 移动端简化：把当前文本作为编辑目标交给 AI，返回的 css 直接替换文本框。
        self.cover_label.text = "正在生成 CSS…"
        threading.Thread(target=self._run_ai_css, daemon=True).start()

    def _run_ai_css(self) -> None:
        payload = json.dumps({"scope": "all", "colorOnly": True, "note": "",
                              "targets": []}, ensure_ascii=False)
        result = self.api.ai_generate(payload)
        def apply():
            if not result.get("ok"):
                self.cover_label.text = f"AI CSS 失败：{result.get('error')}"
                return
            css = result.get("css") or {}
            if isinstance(css, dict) and css:
                self.text.text = "\n".join(str(value) for value in css.values())
            self.cover_label.text = "AI CSS 已更新"
        self.app_clock(apply)

    def app_clock(self, callback):
        from kivy.clock import Clock
        Clock.schedule_once(lambda _dt: callback(), 0)

    def _save(self) -> None:
        hover = bool(self.hover.active)
        version_styles = {}
        for key, widget in self.version_inputs.items():
            version_styles[key] = {"colors": widget.text.strip(), "image": ""}
        if self.cover_path and self.cover_file is not None:
            try:
                from utils.image import save_as_cover
                save_as_cover(self.cover_path, self.cover_file)
            except Exception as e:  # noqa: BLE001
                self.app.set_status(f"封面写入失败：{e}")
        self.app.pop_screen()
        self.done((self.text.text, hover, version_styles))

    def _cancel(self) -> None:
        self.app.pop_screen()
        self.done(None)


class LyricsEditorScreen(ScreenBase):
    def __init__(self, app, api, done):
        super().__init__(app, "歌词", on_back=lambda: self._cancel())
        self.api = api
        self.done = done
        ctx = api.get_context()
        self.add(section("待归类歌词"))
        self.pending = self.add(text_input(ctx.get("initial") or "", multiline=True, height=180))
        self.add(section("日语"))
        self.jap = self.add(text_input(ctx.get("jap") or "", multiline=True, height=180))
        self.add(section("中文"))
        self.chs = self.add(text_input("", multiline=True, height=180))
        self.add(section("罗马音"))
        self.roma = self.add(text_input("", multiline=True, height=120))
        self.source_hint = self.add(text_input(ctx.get("sourceHint") or ""))
        self.add(section("从链接填充"))
        self.source_url = self.add(text_input(ctx.get("sourceHint") or ""))
        self.add(section("翻译者 / 来源（可留空）"))
        meta_row = row(height=44, spacing=6)
        self.translator = text_input("")
        self.translator_url = text_input("")
        self.source_name = text_input("")
        self.source_url_value = text_input("")
        for widget, hint in ((self.translator, "翻译者"), (self.translator_url, "翻译链接"),
                             (self.source_name, "来源"), (self.source_url_value, "来源链接")):
            widget.hint_text = hint
            meta_row.add_widget(widget)
        self.add(meta_row)
        self.use_hover = Switch(active=bool(ctx.get("useHover")))
        self.use_colors = Switch(active=bool(ctx.get("useColors")))
        self.use_roma = Switch(active=bool(ctx.get("useRoma")) or bool(self.roma.text.strip()))
        self.add(switch_row("保留 hover", self.use_hover, "歌词里加 {{hover|…}} 注音"))
        self.add(switch_row("自动配色", self.use_colors, "按歌曲生成歌词配色参数"))
        self.add(switch_row("罗马音", self.use_roma, "填了罗马音栏才生效"))
        self.add(row(button("从链接填充", self._fill),
                     button("自动识别", self._auto),
                     button("AI 分栏", self._ai),
                     height=48, spacing=6))
        self.tip = self.add(label("", color=COLORS["muted"], size=12))
        self.add(section("按行号切分（自动识别失败时用）"))
        layout_row = row(height=44, spacing=6)
        self.group_length = text_input("")
        self.jap_line = text_input("")
        self.chs_line = text_input("")
        self.roma_line = text_input("")
        for widget, hint in ((self.group_length, "每组行数"), (self.jap_line, "日"),
                             (self.chs_line, "中"), (self.roma_line, "罗")):
            widget.hint_text = hint
            layout_row.add_widget(widget)
        self.add(layout_row)
        self.add(button("按行号转换", self._convert, variant="secondary"))
        self.add(row(button("取消", self._cancel, variant="secondary"),
                     button("完成", self._save), height=48, spacing=6))
        self.add(spacer(6))

    def _payload(self) -> str:
        return json.dumps({"text": self.pending.text, "jap": self.jap.text,
                           "chs": self.chs.text, "roma": self.roma.text,
                           "translator": self.translator.text,
                           "translatorUrl": self.translator_url.text,
                           "sourceName": self.source_name.text,
                           "sourceUrl": self.source_url_value.text,
                           "useHover": bool(self.use_hover.active),
                           "useColors": bool(self.use_colors.active),
                           "useRoma": bool(self.use_roma.active)},
                          ensure_ascii=False)

    def _fill(self) -> None:
        url = self.source_url.text.strip()
        if not url:
            self.tip.text = "请先填来源链接"
            return
        self.tip.text = "正在抓取…"
        def work():
            result = self.api.fill_source(url)
            def apply():
                if not result.get("ok"):
                    self.tip.text = f"抓取失败：{result.get('error')}"
                    return
                target = result.get("target") or "source"
                lyrics = str(result.get("lyrics") or "")
                if target == "chs":
                    self.chs.text = lyrics
                else:
                    self.pending.text = lyrics
                for key, widget in (("translator", self.translator),
                                    ("translatorUrl", self.translator_url),
                                    ("sourceName", self.source_name),
                                    ("sourceUrl", self.source_url_value)):
                    if result.get(key):
                        widget.text = str(result[key])
                self.tip.text = str(result.get("message") or "已填入歌词")
            self._later(apply)
        threading.Thread(target=work, daemon=True).start()

    def _auto(self) -> None:
        result = self.api.auto(self._payload())
        layout = result.get("layout") or {}
        if layout:
            self.group_length.text = str(layout.get("group_length") or "")
            self.jap_line.text = str(layout.get("jap_line") or "")
            self.chs_line.text = str(layout.get("chs_line") or "")
            self.roma_line.text = str(layout.get("roma_line") or "")
        self._apply_result(result)

    def _convert(self) -> None:
        payload = json.dumps({"text": self.pending.text,
                              "groupLength": self.group_length.text,
                              "japLine": self.jap_line.text,
                              "chsLine": self.chs_line.text,
                              "romaLine": self.roma_line.text},
                             ensure_ascii=False)
        self._apply_result(self.api.convert(payload))

    def _ai(self) -> None:
        self.tip.text = "AI 分栏中…"
        def work():
            result = self.api.ai_auto(self._payload())
            self._later(lambda: self._apply_result(result))
        threading.Thread(target=work, daemon=True).start()

    def _apply_result(self, result):
        if not result.get("ok"):
            self.tip.text = f"失败：{result.get('error')}"
            return
        if result.get("jap") is not None:
            self.jap.text = result.get("jap") or ""
        if result.get("chs") is not None:
            self.chs.text = result.get("chs") or ""
        if result.get("roma") is not None:
            self.roma.text = result.get("roma") or ""
        self.tip.text = str(result.get("message") or "已更新")

    def _later(self, callback):
        from kivy.clock import Clock
        Clock.schedule_once(lambda _dt: callback(), 0)

    def _save(self) -> None:
        result = self.api.save(self._payload())
        if not result.get("ok"):
            self.tip.text = f"保存失败：{result.get('error')}"
            return
        self.app.pop_screen()
        self.done(self.api.result)

    def _cancel(self) -> None:
        self.api.cancel()
        self.app.pop_screen()
        self.done(None)


class SubmitEditorScreen(ScreenBase):
    def __init__(self, app, api, done):
        super().__init__(app, "提交", on_back=lambda: self._close())
        self.api = api
        self.done = done
        self.context = api.get_context() if hasattr(api, "get_context") else {}
        self.add(label(str(self.context.get("page") or "页面"), size=16))
        source = self.context.get("file")
        initial = ""
        try:
            if source and Path(source).is_file():
                initial = Path(source).read_text(encoding="utf-8")
        except OSError:
            pass
        pages = self.context.get("pages") or []
        if pages:
            page_row = row(height=44, spacing=6)
            for index, page in enumerate(pages):
                page_row.add_widget(button(str(page.get("name") or index),
                                           lambda i=index: self._select_page(i),
                                           variant="secondary"))
            self.add(page_row)
        self.text = self.add(text_input(initial, multiline=True, height=420))
        self.add(section("编辑摘要"))
        self.summary = self.add(text_input(str(self.context.get("summary") or "")))
        self.add(row(button("预览", self._preview, variant="secondary"),
                     button("保存", self._save, variant="secondary"),
                     button("提交", self._submit),
                     height=48, spacing=6))
        if hasattr(api, "submit_all"):
            self.add(button("全部提交", self._submit_all, variant="secondary"))
        self.tip = self.add(label("", color=COLORS["muted"], size=12))

    def _select_page(self, index: int) -> None:
        if not hasattr(self.api, "select"):
            return
        text = self.api.select(index)
        if text is not None:
            self.text.text = text

    def _preview(self) -> None:
        result = self.api.preview(self.text.text)
        if result.get("error"):
            self.tip.text = f"预览失败：{result['error']}"
            return
        document = preview.build_document(result, str(self.context.get("origin") or ""))
        url = preview.serve(document)
        if platform.open_url(url):
            self.tip.text = "已在浏览器打开预览"
        else:
            target = platform.app_dir() / "preview.html"
            try:
                target.write_text(document, encoding="utf-8")
            except OSError:
                pass
            self.tip.text = "打不开浏览器，预览已存到 preview.html"

    def _save(self) -> None:
        result = self.api.save(self.text.text)
        self.tip.text = str(result.get("message") or result.get("error") or "已保存")

    def _submit(self) -> None:
        self.tip.text = "提交中…"
        def work():
            result = self.api.submit(self.text.text, self.summary.text)
            self._later(lambda: self._finish(result))
        threading.Thread(target=work, daemon=True).start()

    def _submit_all(self) -> None:
        self.tip.text = "逐页提交中…"
        def work():
            result = self.api.submit_all(self.summary.text)
            self._later(lambda: self._finish(result))
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, result) -> None:
        if result.get("ok"):
            self.tip.text = str(result.get("message") or "提交完成")
            self.done(True)
        else:
            self.tip.text = f"提交失败：{result.get('error')}"

    def _close(self) -> None:
        self.app.pop_screen()
        self.done(False)

    def _later(self, callback):
        from kivy.clock import Clock
        Clock.schedule_once(lambda _dt: callback(), 0)
