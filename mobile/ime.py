"""Android 软键盘的输入类型 —— 不显式声明 `input_type` 时中文 / 日文根本打不出来。

Kivy 2.1 起 `FocusBehavior.input_type` 的默认值从 `'text'` 改成了 **`'null'`**
（见 `kivy/uix/behaviors/focus.py`：*Changed default value from `text` to `null`*）。
`'null'` 在 Android 上对应 `InputType.TYPE_NULL`，含义是「只要按键事件、不要输入法组词」——
搜狗 / Gboard 于是停在英文键盘上：候选区不出现，也切不到中文 / 日文
（用户 2026-10-23 报「输入法被强锁在只能输入英文」，填「歌曲原名」时只能敲字母）。

所以 `mobile` 里每个输入框都要显式 `input_type="text"`（对应 `TYPE_CLASS_TEXT`，
输入法组词正常，中文 / 日文 / 罗马音都能打）。桌面端这个属性不起作用，设了也无妨。

**纯常量 + 纯函数**，不导入 Kivy：单测可以直接跑（Kivy 装不进 CI 的 Python 3.14）。
"""
from __future__ import annotations

from typing import Dict

# `InputType.TYPE_CLASS_TEXT`：正常文本输入，允许输入法组词（中文 / 日文）
INPUT_TYPE = "text"
# 让输入法显示候选词（Kivy 2.1 起默认就是 True，这里写明意图）
KEYBOARD_SUGGESTIONS = True


def input_options() -> Dict[str, object]:
    """`TextInput` 的键盘相关参数（`mobile/screens/common.py` 的 `text_input()` 用）。"""
    return {"input_type": INPUT_TYPE, "keyboard_suggestions": KEYBOARD_SUGGESTIONS}
