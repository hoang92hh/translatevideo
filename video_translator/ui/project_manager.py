from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..project import PROJECT_FILE, ProjectService, VideoProject
from .components import Card


LANGUAGES = ("Chinese", "English", "Vietnamese", "Spanish", "Japanese", "Korean")


class NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tạo project mới")
        self.setMinimumWidth(620)
        root = QVBoxLayout(self)
        title = QLabel("Project video mới")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        help_text = QLabel("Mỗi project quản lý một video gốc và toàn bộ sản phẩm sinh ra từ video đó.")
        help_text.setObjectName("muted")
        help_text.setWordWrap(True)
        root.addWidget(help_text)

        form = QFormLayout()
        form.setSpacing(14)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ví dụ: gioi-thieu-san-pham")
        form.addRow("Tên project", self.name)

        self.video = QLineEdit()
        self.video.setPlaceholderText("Chọn video nguồn")
        form.addRow("Video gốc", self._path_row(self.video, self._choose_video, "Chọn video"))

        self.location = QLineEdit(str(Path.cwd() / "projects"))
        form.addRow("Lưu project tại", self._path_row(self.location, self._choose_location, "Chọn thư mục"))

        self.source_language = QComboBox()
        self.source_language.addItems(LANGUAGES)
        self.source_language.setCurrentText("Chinese")
        self.target_language = QComboBox()
        self.target_language.addItems(LANGUAGES)
        self.target_language.setCurrentText("Vietnamese")
        form.addRow("Ngôn ngữ nguồn", self.source_language)
        form.addRow("Ngôn ngữ đích", self.target_language)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Tạo project")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _path_row(field: QLineEdit, callback: Any, button_text: str) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        button = QPushButton(button_text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return widget

    def _choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn video gốc", "", "Video (*.mp4 *.mov *.mkv *.avi);;Tất cả file (*)")
        if path:
            self.video.setText(path)
            if not self.name.text().strip():
                self.name.setText(Path(path).stem)

    def _choose_location(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn nơi lưu project", self.location.text())
        if path:
            self.location.setText(path)

    def _validate_and_accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên project.")
            return
        if not Path(self.video.text().strip()).is_file():
            QMessageBox.warning(self, "Thiếu video", "Vui lòng chọn một video nguồn hợp lệ.")
            return
        if not self.location.text().strip():
            QMessageBox.warning(self, "Thiếu thư mục", "Vui lòng chọn nơi lưu project.")
            return
        if self.source_language.currentText() == self.target_language.currentText():
            QMessageBox.warning(self, "Ngôn ngữ không hợp lệ", "Ngôn ngữ nguồn và đích cần khác nhau.")
            return
        self.accept()

    def values(self) -> dict[str, str]:
        return {
            "name": self.name.text().strip(),
            "source_video": self.video.text().strip(),
            "parent_folder": self.location.text().strip(),
            "source_language": self.source_language.currentText(),
            "target_language": self.target_language.currentText(),
        }


class ProjectManagerPage(QWidget):
    project_opened = Signal(object)
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("TransLanguage", "TransLanguage")
        root = QVBoxLayout(self)
        root.setContentsMargins(64, 48, 64, 48)
        root.setSpacing(24)

        brand = QLabel("TRANSLANGUAGE")
        brand.setObjectName("brandLarge")
        title = QLabel("Biến một video thành nhiều ngôn ngữ")
        title.setObjectName("heroTitle")
        subtitle = QLabel("Tạo project mới hoặc tiếp tục một pipeline đã lưu trước đó.")
        subtitle.setObjectName("muted")
        root.addWidget(brand)
        root.addWidget(title)
        root.addWidget(subtitle)

        actions = QHBoxLayout()
        new_button = QPushButton("＋  Tạo project mới")
        new_button.setObjectName("primaryButton")
        new_button.clicked.connect(self.create_new)
        open_button = QPushButton("Mở project có sẵn")
        open_button.clicked.connect(self.open_existing)
        settings_button = QPushButton("Cài đặt API & Providers")
        settings_button.clicked.connect(self.settings_requested.emit)
        actions.addWidget(new_button)
        actions.addWidget(open_button)
        actions.addWidget(settings_button)
        actions.addStretch()
        root.addLayout(actions)

        recent_card = Card("Project gần đây", "Nhấp đúp để mở lại project và tiếp tục từ kết quả đã lưu.")
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentProjects")
        self.recent_list.itemDoubleClicked.connect(self._open_recent_item)
        recent_card.content_layout.addWidget(self.recent_list)
        root.addWidget(recent_card, 1)
        self.refresh_recent()

    def create_new(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            project = ProjectService.create(**dialog.values())
        except Exception as exc:
            QMessageBox.critical(self, "Không thể tạo project", str(exc))
            return
        self.add_recent(project.manifest_path)
        self.project_opened.emit(project)

    def open_existing(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Mở project", "", f"TransLanguage Project ({PROJECT_FILE})")
        if path:
            self._load(path)

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        self._load(item.data(Qt.ItemDataRole.UserRole))

    def _load(self, path: str) -> None:
        try:
            project = ProjectService.load(path)
        except Exception as exc:
            QMessageBox.critical(self, "Không thể mở project", str(exc))
            self.remove_recent(path)
            return
        self.add_recent(project.manifest_path)
        self.project_opened.emit(project)

    def recent_paths(self) -> list[str]:
        value = self.settings.value("recentProjects", [])
        return [value] if isinstance(value, str) else list(value)

    def add_recent(self, path: str | Path) -> None:
        normalized = str(Path(path).resolve())
        paths = [item for item in self.recent_paths() if item != normalized and Path(item).is_file()]
        self.settings.setValue("recentProjects", [normalized, *paths][:10])
        self.refresh_recent()

    def remove_recent(self, path: str) -> None:
        self.settings.setValue("recentProjects", [item for item in self.recent_paths() if item != path])
        self.refresh_recent()

    def refresh_recent(self) -> None:
        self.recent_list.clear()
        for path in self.recent_paths():
            manifest = Path(path)
            if not manifest.is_file():
                continue
            item = QListWidgetItem(f"{manifest.parent.name}\n{manifest.parent}")
            item.setData(Qt.ItemDataRole.UserRole, str(manifest))
            self.recent_list.addItem(item)
        if self.recent_list.count() == 0:
            item = QListWidgetItem("Chưa có project gần đây")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent_list.addItem(item)
