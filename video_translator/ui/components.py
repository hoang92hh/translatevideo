from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .specs import FieldSpec, ProviderSpec


class Card(QFrame):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.content_layout = QVBoxLayout(self)
        self.content_layout.setContentsMargins(18, 16, 18, 18)
        self.content_layout.setSpacing(10)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        self.content_layout.addWidget(title_label)
        if subtitle:
            helper = QLabel(subtitle)
            helper.setObjectName("muted")
            helper.setWordWrap(True)
            self.content_layout.addWidget(helper)


class ProviderPanel(Card):
    def __init__(self, providers: tuple[ProviderSpec, ...]) -> None:
        super().__init__("Phương án xử lý", "Chọn provider; các cài đặt bên dưới thay đổi theo phương án.")
        self.provider_combo = QComboBox()
        self.provider_combo.setObjectName("providerCombo")
        self.stack = QStackedWidget()
        self.controls: list[dict[str, QWidget]] = []

        for provider in providers:
            self.provider_combo.addItem(provider.name)
            index = self.provider_combo.count() - 1
            if not provider.available:
                item = self.provider_combo.model().item(index)
                if item:
                    item.setEnabled(False)
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(0, 8, 0, 0)
            form.setSpacing(12)
            page_controls: dict[str, QWidget] = {}
            for spec in provider.fields:
                control = self._make_control(spec)
                page_controls[spec.key] = control
                form.addRow(spec.label, control)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            self.controls.append(page_controls)
            self.stack.addWidget(page)

        self.content_layout.addWidget(self.provider_combo)
        self.content_layout.addWidget(self.stack)
        self.provider_combo.currentIndexChanged.connect(self.stack.setCurrentIndex)

    @staticmethod
    def _make_control(spec: FieldSpec) -> QWidget:
        if spec.kind == "choice":
            widget = QComboBox()
            widget.addItems(spec.choices)
            widget.setCurrentText(str(spec.default))
            return widget
        if spec.kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(spec.default))
            return widget
        if spec.kind == "int":
            widget = QSpinBox()
            widget.setRange(1, 1000)
            widget.setValue(int(spec.default))
            return widget
        if spec.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(0.25, 4.0)
            widget.setSingleStep(0.05)
            widget.setValue(float(spec.default))
            return widget
        return QLineEdit(str(spec.default))

    def values(self) -> dict[str, Any]:
        values: dict[str, Any] = {"provider": self.provider_combo.currentText()}
        for key, control in self.controls[self.provider_combo.currentIndex()].items():
            if isinstance(control, QComboBox):
                values[key] = control.currentText()
            elif isinstance(control, QCheckBox):
                values[key] = control.isChecked()
            elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
                values[key] = control.value()
            elif isinstance(control, QLineEdit):
                values[key] = control.text()
        return values

