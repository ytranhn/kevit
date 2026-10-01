from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.log.emit))
        except Exception as e:  # noqa: BLE001 - hiển thị mọi lỗi lên UI
            self.failed.emit(str(e))
