from typing import TypeVar

from PySide6.QtCore import QObject


T = TypeVar('T', bound=QObject)


def require_child(root: QObject, widget_type: type[T], object_name: str) -> T:
    child = root.findChild(widget_type, object_name)
    if child is None:
        raise RuntimeError(f'Missing required UI object: {object_name}')
    return child
