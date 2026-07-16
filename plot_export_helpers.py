from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


def shift_click_requests_png_file() -> bool:
    return shift_modifier_active()


def shift_click_clears_pinned_annotations() -> bool:
    return shift_modifier_active()


def shift_modifier_active() -> bool:
    return bool(
        QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
    )


def render_plotly_png(figure, selectedFile: str = '') -> bytes:
    pngBytes = figure.to_image(format='png')

    if selectedFile:
        with open(selectedFile, 'wb') as pngFile:
            pngFile.write(pngBytes)

    return pngBytes


def copy_png_bytes_to_clipboard(pngBytes: bytes) -> None:
    """Copy rendered PNG bytes from the Qt main thread."""

    image = QImage()
    if not image.loadFromData(pngBytes, 'PNG'):
        raise ValueError('Failed to load PNG image for clipboard.')

    QApplication.clipboard().setImage(image)


def save_plotly_png_and_copy_to_clipboard(figure, selectedFile: str = '') -> None:
    pngBytes = render_plotly_png(figure, selectedFile)
    copy_png_bytes_to_clipboard(pngBytes)
