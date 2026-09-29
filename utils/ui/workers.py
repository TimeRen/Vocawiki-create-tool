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


class CallbackRelay(QtCore.QObject):
    """把工作线程的结果转回**主线程**再交给回调。

    ⚠️ `worker.done` 直接连普通函数 / lambda 时，PyQt 按**直接调用**处理 ——
    回调还在工作线程里就跑了，在那里碰控件是未定义行为（实测的后果：提交页
    「写了三篇却显示成功 0 个」，逐页事件还排在队列里时计数器就被读了）。
    QObject 自己住在主线程，所以 `worker.done` → `relay.done` 这条**信号连信号**
    是队列连接，真正跑回调时已经在主线程里了。

    用法：`relay = CallbackRelay(handle, parent=widget); worker.done.connect(relay.done)`。
    转发器必须留个引用（挂 parent 或存进列表），否则排队投递前就被回收了。
    """

    done = QtCore.pyqtSignal(object)

    def __init__(self, callback: Callable[[Any], None], parent=None):
        super().__init__(parent)
        self._callback = callback
        self.done.connect(self._deliver)

    @QtCore.pyqtSlot(object)
    def _deliver(self, result: Any) -> None:
        self._callback(result)
