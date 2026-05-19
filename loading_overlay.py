from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget


class LoadingOverlay(QObject):
    def __init__(self, targetWidget: QWidget, message: str = 'Loading...') -> None:
        super().__init__(targetWidget)
        self.targetWidget = targetWidget
        self.label = QLabel(message, targetWidget)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.label.setStyleSheet(
            '''
            QLabel {
                background-color: rgba(255, 255, 255, 220);
                color: rgba(32, 32, 32, 230);
                border: 1px solid rgba(120, 120, 120, 90);
                font-size: 28pt;
                font-weight: 700;
            }
            '''
        )
        self.label.hide()
        self.targetWidget.installEventFilter(self)

    def show(self, message: str = 'Loading...') -> None:
        self.label.setText(message)
        self._resize_to_target()
        self.label.show()
        self.label.raise_()
        QApplication.processEvents()

    def hide(self) -> None:
        self.label.hide()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.targetWidget and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._resize_to_target()
        return super().eventFilter(watched, event)

    def _resize_to_target(self) -> None:
        self.label.setGeometry(self.targetWidget.rect())
