from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot


class BackgroundWorker(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, taskId: int, work: Callable[[], Any]) -> None:
        super().__init__()
        self.taskId = taskId
        self.work = work

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(self.taskId, self.work())
        except Exception as exc:
            self.failed.emit(self.taskId, str(exc))


class BackgroundTaskDispatcher(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class BackgroundTaskMixin:
    def _start_background_task(
        self,
        work: Callable[[], Any],
        onFinished: Callable[[int, Any], None],
        onFailed: Callable[[int, str], None],
    ) -> int:
        taskId = getattr(self, '_backgroundTaskSerial', 0) + 1
        self._backgroundTaskSerial = taskId

        thread = QThread()
        worker = BackgroundWorker(taskId, work)
        dispatcher = BackgroundTaskDispatcher(getattr(self, 'rootWidget', None))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(dispatcher.finished, Qt.QueuedConnection)
        worker.failed.connect(dispatcher.failed, Qt.QueuedConnection)
        dispatcher.finished.connect(onFinished)
        dispatcher.failed.connect(onFailed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        tasks = getattr(self, '_backgroundTasks', None)
        if tasks is None:
            tasks = {}
            self._backgroundTasks = tasks
        tasks[taskId] = (thread, worker, dispatcher)
        thread.finished.connect(lambda taskId=taskId: self._backgroundTasks.pop(taskId, None))
        thread.start()
        return taskId
