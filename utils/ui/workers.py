"""把可能很慢的调用（网络请求、AI 生成）放到线程里跑，避免界面卡住。"""
import logging
from typing import Any, Callable

from PyQt5 import QtCore


class FunctionWorker(QtCore.QThread):
    """在线程里调一个函数，结果通过 `done` 信号回到主线程。"""

    done = QtCore.pyqtSignal(object)

    def __init__(self, func: Callable[..., Any], *args: Any, parent=None):
        super().__init__(parent)
        self._func = func
        self._args = args

    def run(self) -> None:                      # noqa: D102 - QThread 约定
        try:
            result = self._func(*self._args)
        except Exception as e:                  # noqa: BLE001 - 任何异常都回给界面
            logging.error("%s 失败：%s", getattr(self._func, "__name__", "后台任务"), e, exc_info=e)
            result = {"ok": False, "error": str(e)}
        self.done.emit(result)
