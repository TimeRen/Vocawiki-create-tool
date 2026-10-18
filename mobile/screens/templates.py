"""P主模板 / 歌姬模板的曲目页与样式页。"""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.switch import Switch

from mobile.screens.common import (COLORS, ScreenBase, button, label, spacer,
                                   text_input)
from utils.producer_template import ProducerSong
from utils.vocalist_template import VocalistSong


class ProducerWorksScreen(ScreenBase):
    def __init__(self, app, work, done):
        super().__init__(app, "P主模板 · 曲目", on_back=lambda: self._cancel())
        self.work = work
        self.done = done
        self.rows = []
        self.add(label("模板 / 条目名", color=COLORS["muted"], size=13))
        self.page_name = self.add(text_input(work.page_name or work.artist.name))
        self.template_name = self.add(text_input(work.template_name or work.artist.name))
        self.add(label("曲目（日文名 / 中文条目名 / 投稿日期）", color=COLORS["muted"], size=13))
        self.list_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.list_box.bind(minimum_height=lambda box, value: setattr(box, "height", value))
        self.add(self.list_box)
        for song in list(work.songs):
            self._add_row(song)
        self.add(button("增加一曲", lambda: self._add_row(ProducerSong())))
        self.add(spacer(6))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        row.add_widget(button("取消", self._cancel, color=COLORS["muted"]))
        row.add_widget(button("保存并继续", self._save))
        self.add(row)

    def _add_row(self, song: ProducerSong):
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        ja = text_input(song.ja)
        cn = text_input(song.cn)
        date = text_input(song.date)
        row.add_widget(ja)
        row.add_widget(cn)
        row.add_widget(date)
        remove = button("×", None, color=COLORS["danger"], size_hint_x=None, width=dp(44))
        row.add_widget(remove)
        self.rows.append((song, row, ja, cn, date))
        remove.bind(on_release=lambda *_: self._remove(row))
        self.list_box.add_widget(row)

    def _remove(self, row):
        self.rows = [item for item in self.rows if item[1] is not row]
        self.list_box.remove_widget(row)

    def _save(self):
        self.work.page_name = self.page_name.text.strip()
        self.work.template_name = self.template_name.text.strip()
        songs = []
        for song, _row, ja, cn, date in self.rows:
            if not (ja.text.strip() or cn.text.strip()):
                continue
            song.ja = ja.text.strip()
            song.cn = cn.text.strip()
            song.date = date.text.strip()
            songs.append(song)
        self.work.songs = songs
        self.app.pop_screen()
        self.done(self.work)

    def _cancel(self):
        self.app.pop_screen()
        self.done(None)


class ProducerStyleScreen(ScreenBase):
    def __init__(self, app, work, done):
        super().__init__(app, "P主模板 · 样式", on_back=lambda: self._cancel())
        self.work = work
        self.done = done
        self.fields = {}
        from utils.producer_template import DEFAULT_STYLES
        styles = dict(DEFAULT_STYLES)
        styles.update(work.styles or {})
        for key, value in styles.items():
            self.add(label(str(key), color=COLORS["muted"], size=13))
            self.fields[key] = self.add(text_input(str(value or "")))
        self.add(spacer(6))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        row.add_widget(button("取消", self._cancel, color=COLORS["muted"]))
        row.add_widget(button("保存并继续", self._save))
        self.add(row)

    def _save(self):
        self.app.pop_screen()
        self.done({key: widget.text.strip() for key, widget in self.fields.items()})

    def _cancel(self):
        self.app.pop_screen()
        self.done(None)


class VocalistWorksScreen(ScreenBase):
    def __init__(self, app, work, done):
        super().__init__(app, "歌姬模板 · 曲目", on_back=lambda: self._cancel())
        self.work = work
        self.done = done
        self.rows = []
        self.add(label("歌姬 / 引擎", color=COLORS["muted"], size=13))
        self.vocalist_name = self.add(text_input(work.name))
        self.engine = self.add(text_input(work.engine))
        split_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        split_row.add_widget(label("拆成年份子页", size=14))
        self.split = Switch(active=bool(work.split))
        split_row.add_widget(self.split)
        self.add(split_row)
        self.add(label("曲目（条目名 / 日文名 / 年份 / 日期）", color=COLORS["muted"], size=13))
        self.list_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        self.list_box.bind(minimum_height=lambda box, value: setattr(box, "height", value))
        self.add(self.list_box)
        for song in list(work.songs):
            self._add_row(song)
        self.add(button("增加一曲", lambda: self._add_row(VocalistSong(title=""))))
        self.add(spacer(6))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        row.add_widget(button("取消", self._cancel, color=COLORS["muted"]))
        row.add_widget(button("保存并继续", self._save))
        self.add(row)

    def _add_row(self, song):
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(4))
        title = text_input(song.title)
        ja = text_input(song.ja)
        year = text_input(song.year)
        date = text_input(song.date)
        row.add_widget(title)
        row.add_widget(ja)
        row.add_widget(year)
        row.add_widget(date)
        remove = button("×", None, color=COLORS["danger"], size_hint_x=None, width=dp(44))
        row.add_widget(remove)
        self.rows.append((song, row, title, ja, year, date))
        remove.bind(on_release=lambda *_: self._remove(row))
        self.list_box.add_widget(row)

    def _remove(self, row):
        self.rows = [item for item in self.rows if item[1] is not row]
        self.list_box.remove_widget(row)

    def _save(self):
        self.work.name = self.vocalist_name.text.strip()
        self.work.engine = self.engine.text.strip()
        self.work.split = bool(self.split.active)
        songs = []
        for song, _row, title, ja, year, date in self.rows:
            if not (title.text.strip() or ja.text.strip()):
                continue
            song.title = title.text.strip()
            song.ja = ja.text.strip()
            song.year = year.text.strip()
            song.date = date.text.strip()
            songs.append(song)
        self.work.songs = songs
        self.app.pop_screen()
        self.done(self.work)

    def _cancel(self):
        self.app.pop_screen()
        self.done(None)


class VocalistStyleScreen(ScreenBase):
    def __init__(self, app, payload, done):
        super().__init__(app, "歌姬模板 · 样式", on_back=lambda: self._cancel())
        self.payload = payload
        self.done = done
        self.fields = {}
        defaults = payload.get("defaults") or {}
        for key, value in defaults.items():
            self.add(label(str(key), color=COLORS["muted"], size=13))
            self.fields[key] = self.add(text_input(str(value or "")))
        self.add(spacer(6))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        row.add_widget(button("取消", self._cancel, color=COLORS["muted"]))
        row.add_widget(button("保存并继续", self._save))
        self.add(row)

    def _save(self):
        self.app.pop_screen()
        self.done({key: widget.text.strip() for key, widget in self.fields.items()})

    def _cancel(self):
        self.app.pop_screen()
        self.done(None)
