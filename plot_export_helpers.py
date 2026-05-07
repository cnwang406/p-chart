from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication


def save_plotly_png_and_copy_to_clipboard(figure, selectedFile: str = '') -> None:
    pngBytes = figure.to_image(format='png')

    if selectedFile:
        with open(selectedFile, 'wb') as pngFile:
            pngFile.write(pngBytes)

    image = QImage()
    if not image.loadFromData(pngBytes, 'PNG'):
        raise ValueError('Failed to load PNG image for clipboard.')

    QApplication.clipboard().setImage(image)
