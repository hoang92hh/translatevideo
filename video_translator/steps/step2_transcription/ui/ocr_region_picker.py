"""Select a subtitle crop directly on a frame of the project's first video."""

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QGridLayout, QLabel, QSlider, QVBoxLayout, QWidget

from ..ocr_region import OcrRegion

_PERCENT_DECIMALS = 4
_MIN_SIZE_PERCENT = 10 ** -_PERCENT_DECIMALS


class _FrameCanvas(QWidget):
    region_selected = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(230)
        self.setMaximumHeight(330)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.image = QImage()
        self.region: OcrRegion | None = None
        self._drag_start: QPointF | None = None
        self._drag_end: QPointF | None = None

    def _frame_rect(self) -> QRectF:
        if self.image.isNull():
            return QRectF()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def _point_in_frame(self, point: QPointF) -> QPointF:
        rect = self._frame_rect()
        return QPointF(
            min(1.0, max(0.0, (point.x() - rect.x()) / rect.width())),
            min(1.0, max(0.0, (point.y() - rect.y()) / rect.height())),
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#16191d"))
        if self.image.isNull():
            return
        rect = self._frame_rect()
        painter.drawImage(rect, self.image)
        region = self.region
        if self._drag_start is not None and self._drag_end is not None:
            drag = QRectF(self._drag_start, self._drag_end).normalized()
            region = drag.x(), drag.y(), drag.width(), drag.height()
        if region:
            x, y, width, height = region
            overlay = QRectF(rect.x() + x * rect.width(), rect.y() + y * rect.height(),
                             width * rect.width(), height * rect.height())
            painter.setPen(QPen(QColor("#53e0a1"), 2))
            painter.setBrush(QColor(83, 224, 161, 40))
            painter.drawRect(overlay.adjusted(1, 1, -1, -1))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._frame_rect().contains(event.position()):
            self._drag_start = self._drag_end = self._point_in_frame(event.position())
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_start is not None and not self.image.isNull():
            self._drag_end = self._point_in_frame(event.position())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_start is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.image.isNull():
            rect = QRectF(self._drag_start, self._point_in_frame(event.position())).normalized()
            if rect.width() * self.image.width() >= 1 and rect.height() * self.image.height() >= 1:
                self.region_selected.emit((rect.x(), rect.y(), rect.width(), rect.height()))
        self._drag_start = self._drag_end = None
        self.update()


class OcrRegionPicker(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.region: OcrRegion | None = None
        self._video = ""
        self._loaded = False
        self._fps = 0.0
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.canvas = _FrameCanvas()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.time = QLabel()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(90)
        self.timer.timeout.connect(self._refresh_frame)
        self.slider.valueChanged.connect(self._schedule_refresh)
        self.canvas.region_selected.connect(self.set_region)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.hint)
        layout.addWidget(self.canvas)
        layout.addWidget(self.slider)
        layout.addWidget(self.time)

        coordinates = QGridLayout()
        self._coordinate_fields: list[QDoubleSpinBox] = []
        for column, title in enumerate(("X từ mép trái", "Y từ mép trên", "Chiều rộng", "Chiều cao")):
            field = QDoubleSpinBox()
            field.setDecimals(_PERCENT_DECIMALS)
            field.setRange(0 if column < 2 else _MIN_SIZE_PERCENT, 100)
            field.setSingleStep(0.1)
            field.setSuffix(" %")
            field.setKeyboardTracking(False)
            label = QLabel(title)
            label.setBuddy(field)
            coordinates.addWidget(label, 0, column)
            coordinates.addWidget(field, 1, column)
            self._coordinate_fields.append(field)
            field.valueChanged.connect(self._coordinates_changed)
        layout.addLayout(coordinates)

    def set_region(self, region: OcrRegion) -> None:
        x, y, width, height = (round(value * 100, _PERCENT_DECIMALS) for value in region)
        # Round once for the editable fields and keep the right and bottom edges inside the frame.
        x = min(x, 100 - _MIN_SIZE_PERCENT)
        y = min(y, 100 - _MIN_SIZE_PERCENT)
        width = max(_MIN_SIZE_PERCENT, min(width, 100 - x))
        height = max(_MIN_SIZE_PERCENT, min(height, 100 - y))
        for field, value in zip(self._coordinate_fields, (x, y, width, height), strict=True):
            with QSignalBlocker(field):
                field.setMaximum(100)
                field.setValue(value)
        self._coordinates_changed()

    def _coordinates_changed(self, *_: object) -> None:
        x_field, y_field, width_field, height_field = self._coordinate_fields
        x, y, width, height = (field.value() for field in self._coordinate_fields)
        for field, maximum in (
            (x_field, 100 - width),
            (y_field, 100 - height),
            (width_field, 100 - x),
            (height_field, 100 - y),
        ):
            with QSignalBlocker(field):
                field.setMaximum(round(maximum, _PERCENT_DECIMALS))
        self.region = (x / 100, y / 100, width / 100, height / 100)
        self.canvas.region = self.region
        self.canvas.update()

    def set_video(self, path: str) -> None:
        if self._video != path:
            self._video = path
            self._loaded = False
            self.timer.stop()
            if self.isVisible():
                self._load_video()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._load_video()

    def _load_video(self) -> None:
        self._loaded = True
        self.canvas.image = QImage()
        self.canvas.update()
        self.slider.setEnabled(False)
        self.time.clear()
        if not self._video:
            self.hint.setText("Hãy chọn video trong Project & Input để khoanh vùng OCR.")
            return
        self._refresh_frame(initialize=True)

    def _schedule_refresh(self, index: int) -> None:
        if self._fps > 0:
            self.time.setText(f"{index / self._fps:.2f} giây")
        self.timer.start()

    def _refresh_frame(self, initialize: bool = False) -> None:
        if not self.isVisible() or not self._video:
            return
        capture = None
        try:
            import cv2

            capture = cv2.VideoCapture(self._video)
            if not capture.isOpened():
                raise ValueError("Không mở được video.")
            if initialize:
                self._fps = capture.get(cv2.CAP_PROP_FPS)
                frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                if not math.isfinite(self._fps) or self._fps <= 0:
                    self._fps = 0.0
                last = max(0, int(frame_count) - 1) if math.isfinite(frame_count) else 0
                self.slider.blockSignals(True)
                try:
                    self.slider.setRange(0, last)
                    self.slider.setValue(min(last, max(0, round(10 * self._fps))))
                finally:
                    self.slider.blockSignals(False)
                self.slider.setEnabled(last > 0)
            frame_index = self.slider.value()
            if frame_index or not initialize:
                if not capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index):
                    raise ValueError("Không thể tua đến khung hình này.")
            valid, frame = capture.read()
            if not valid:
                raise ValueError("Không đọc được khung hình này.")
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            self.canvas.image = QImage(
                rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888,
            ).copy()
            self.hint.setText(f"{Path(self._video).name} · Tua đến đoạn có phụ đề rồi kéo chuột để khoanh vùng.")
            if self._fps > 0:
                self.time.setText(f"{frame_index / self._fps:.2f} giây")
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self.canvas.image = QImage()
            self.hint.setText(f"Không thể xem trước video OCR: {exc}")
        finally:
            if capture is not None:
                capture.release()
            self.canvas.update()
