from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch_project import BatchProject, ProjectVideo
from ..common.models import STEP_ORDER, StepId
from ..project import VIDEO_PROJECT_FILE


VISIBLE_BATCH_STEPS = (
    StepId.EXTRACT,
    StepId.STT,
    StepId.TRANSLATE,
    StepId.RENDER,
)


class StepBatchDialog(QDialog):
    """Select and order videos for a single-step batch."""

    step1_to_3_requested = Signal(object)
    rebuild_1_2_requested = Signal()
    step3_requested = Signal(object)
    step7_requested = Signal(object)

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.video_ids = [video.id for video in project.ordered_videos()]
        self.checked_video_ids: set[str] = set()
        self.step_buttons: list[QPushButton] = []
        self.batch_busy = False
        self.active_step_number = 1
        self.setWindowTitle("Chạy batch theo step")
        self.resize(1280, 720)
        self.step_statuses = {
            video.id: self._read_step_statuses(video) for video in project.videos
        }
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)
        upper = QWidget()
        layout = QVBoxLayout(upper)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter.addWidget(upper)

        actions = QHBoxLayout()
        self.select_all_button = QPushButton("Chọn tất cả")
        self.clear_selection_button = QPushButton("Bỏ chọn tất cả")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(lambda: self._set_all_checked(False))
        actions.addWidget(self.select_all_button)
        actions.addWidget(self.clear_selection_button)
        actions.addWidget(QLabel("Thứ tự được quản lý tại Thiết lập project → Project & Input."))
        self.selection_label = QLabel()
        actions.addWidget(self.selection_label)
        actions.addStretch()
        layout.addLayout(actions)

        self.table = QTableWidget(0, 4 + len(VISIBLE_BATCH_STEPS))
        self.table.setHorizontalHeaderLabels(
            [
                "Chọn", "STT", "Group", "Video",
                *(f"Step {STEP_ORDER.index(step) + 1}" for step in VISIBLE_BATCH_STEPS),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column in range(4, 4 + len(VISIBLE_BATCH_STEPS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, 160)
        self.table.itemSelectionChanged.connect(self._refresh_actions)
        self.table.itemChanged.connect(self._check_changed)
        layout.addWidget(self.table, 1)

        helper = QLabel(
            "Tích checkbox để chọn video theo thứ tự chung đã lưu trong thiết lập project. "
            "Chạy Step 1 → 3 xử lý lần lượt toàn bộ Step 1, Step 2, tạo story theo Group, "
            "chờ 10 giây rồi dịch Step 3. Chạy lại từ 1–2 chỉ bổ sung output còn thiếu và "
            "tạo lại toàn bộ story. Step 7 render từ output Step 6 hiện được chọn."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)
        steps = QHBoxLayout()
        step1_to_3_button = QPushButton("Chạy Step 1 → 3")
        rebuild_button = QPushButton("Chạy lại từ 1–2")
        step3_button = QPushButton("Chạy Step 3")
        step7_button = QPushButton("Chạy Step 7")
        step1_to_3_button.clicked.connect(self._step1_to_3_clicked)
        rebuild_button.clicked.connect(self._rebuild_1_2_clicked)
        step3_button.clicked.connect(lambda: self._step_clicked(3))
        step7_button.clicked.connect(lambda: self._step_clicked(7))
        self.step_buttons.extend((step1_to_3_button, rebuild_button, step3_button, step7_button))
        steps.addWidget(step1_to_3_button)
        steps.addWidget(rebuild_button)
        steps.addStretch()
        steps.addWidget(step3_button)
        steps.addWidget(step7_button)
        layout.addLayout(steps)
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setValue(0)
        self.batch_label = QLabel("Sẵn sàng chạy chuỗi Step 1 → 3, Step 3 hoặc Step 7.")
        self.batch_label.setWordWrap(True)
        layout.addWidget(self.batch_progress)
        layout.addWidget(self.batch_label)
        lower = QWidget()
        story_layout = QVBoxLayout(lower)
        story_layout.setContentsMargins(0, 0, 0, 0)
        story_layout.addWidget(QLabel("Output Step 2 — Story"))
        story_actions = QHBoxLayout()
        self.story_selector = QComboBox()
        self.story_selector.setMinimumContentsLength(24)
        story_actions.addWidget(self.story_selector, 1)
        self.save_story_button = QPushButton("Lưu story")
        self.save_story_button.setObjectName("primaryButton")
        self.save_story_button.clicked.connect(self._save_story)
        story_actions.addWidget(self.save_story_button)
        story_layout.addLayout(story_actions)
        self.story_editor = QPlainTextEdit()
        self.story_editor.setPlaceholderText(
            "Chưa có output Step 2. Nội dung story được chọn sẽ hiển thị tại đây để xem và chỉnh sửa."
        )
        story_layout.addWidget(self.story_editor, 1)
        self.story_status = QLabel()
        story_layout.addWidget(self.story_status)
        self.story_path: Path | None = None
        self.story_index = -1
        self.story_editor.textChanged.connect(self._refresh_story_actions)
        self.story_selector.currentIndexChanged.connect(self._story_selected)
        splitter.addWidget(lower)
        splitter.setSizes([360, 300])
        self._load_stories()
        self._refresh_table(0)

    def load_story(self, path: str) -> None:
        self._load_stories(path)

    def _load_stories(self, selected_path: str = "") -> None:
        if not self._confirm_story_changes():
            return
        preferred = selected_path or (str(self.story_path) if self.story_path else "")
        folder = self.project.path("stories")
        paths = sorted(
            (path for path in folder.glob("story_*.*") if path.suffix.lower() in {".txt", ".md"} and path.is_file()),
            key=lambda path: path.name,
        )
        self.story_selector.blockSignals(True)
        self.story_selector.clear()
        group_count = (
            len(self.project.ordered_videos()) + self.project.story_group_size - 1
        ) // self.project.story_group_size
        group_by_path = {
            str(path.resolve()): group
            for group in range(1, group_count + 1)
            if (path := self.project.story_path_for_group(group)) is not None
        }
        for path in paths:
            group = group_by_path.get(str(path.resolve()))
            label = f"[Group {group}] {path.stem}" if group is not None else path.stem
            self.story_selector.addItem(label, str(path))
        if not paths:
            self.story_selector.addItem("Chưa có story", None)
        index = self.story_selector.findData(preferred)
        index = max(0, index)
        self.story_selector.setCurrentIndex(index)
        self.story_selector.blockSignals(False)
        self.story_index = -1
        self._story_selected(index)

    def _story_selected(self, index: int) -> None:
        if index == self.story_index:
            return
        if not self._confirm_story_changes():
            self.story_selector.blockSignals(True)
            self.story_selector.setCurrentIndex(self.story_index)
            self.story_selector.blockSignals(False)
            return
        value = self.story_selector.itemData(index)
        path = Path(value) if value else None
        try:
            content = path.read_text(encoding="utf-8-sig") if path else ""
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Không thể đọc story", str(exc))
            self.story_selector.blockSignals(True)
            self.story_selector.setCurrentIndex(self.story_index)
            self.story_selector.blockSignals(False)
            return
        self.story_path = path
        self.story_index = index
        self.story_editor.setPlainText(content)
        self.story_editor.document().setModified(False)
        self.story_editor.setReadOnly(self.batch_busy or path is None)
        self._refresh_story_actions()

    def _refresh_story_actions(self) -> None:
        dirty = self.story_editor.document().isModified()
        self.save_story_button.setEnabled(self.story_path is not None and dirty and not self.batch_busy)
        self.story_status.setText(
            "Có thay đổi chưa lưu" if dirty
            else str(self.story_path) if self.story_path
            else "Chọn video rồi chạy Step 2 để tạo file story bằng AI."
        )

    def _save_story(self) -> bool:
        if self.story_path is None:
            return False
        temporary = self.story_path.with_name(self.story_path.name + ".part")
        try:
            temporary.write_text(self.story_editor.toPlainText(), encoding="utf-8")
            temporary.replace(self.story_path)
        except OSError as exc:
            QMessageBox.warning(self, "Không thể lưu story", str(exc))
            return False
        self.story_editor.document().setModified(False)
        self._refresh_story_actions()
        return True

    def _confirm_story_changes(self) -> bool:
        if not self.story_editor.document().isModified():
            return True
        choice = QMessageBox.question(
            self, "Story chưa được lưu", "Bạn có muốn lưu nội dung story đã chỉnh sửa?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_story()
        try:
            content = self.story_path.read_text(encoding="utf-8-sig") if self.story_path else ""
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Không thể nạp lại story", str(exc))
            return False
        self.story_editor.setPlainText(content)
        self.story_editor.document().setModified(False)
        self._refresh_story_actions()
        return True

    def _read_step_statuses(self, video: ProjectVideo) -> list[tuple[str, str]]:
        manifest = self.project.path(video.workspace, VIDEO_PROJECT_FILE)
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("pipeline", {}), dict):
                raise ValueError("Dữ liệu pipeline không hợp lệ.")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            return [("Không rõ", f"Không đọc được trạng thái từ {manifest}: {exc}") for _ in STEP_ORDER]

        candidate_fields = (
            "audio_candidates", "transcript_candidates", "translation_candidates",
            "tts_candidates", "sync_candidates", "build_audio_candidates", "render_candidates",
        )
        pipeline = data.get("pipeline", {})
        restart_index = next(
            (index for index, step in enumerate(STEP_ORDER) if step.value == video.restart_step),
            len(STEP_ORDER),
        )
        statuses = []
        for index, step in enumerate(STEP_ORDER):
            stored = pipeline.get(step.value)
            metadata = stored.get("metadata", {}) if isinstance(stored, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            has_errors = (
                step == StepId.STT and bool(metadata.get("timeline_error_count", 0))
            ) or (step == StepId.SYNC and bool(metadata.get("error_count", 0)))
            detail = "Trạng thái dựa trên dữ liệu đã lưu của video."
            if video.status == "processing" and video.current_step == step.value:
                label = "Đang chạy"
            elif video.status == "failed" and video.failed_step == step.value:
                label, detail = "Lỗi", video.last_error or "Step chạy thất bại."
            elif index >= restart_index:
                label = "Cần cập nhật"
            elif has_errors:
                label, detail = "Lỗi", "Kết quả còn lỗi cần sửa trước khi tiếp tục."
            elif stored:
                label = "Hoàn thành"
            elif data.get(candidate_fields[index]):
                label, detail = "Cần cập nhật", "Có kết quả cũ nhưng chưa có kết quả trong pipeline hiện tại."
            else:
                label = "Chưa chạy"
            statuses.append((label, detail))
        return statuses

    def _refresh_table(self, selected_row: int) -> None:
        videos = {video.id: video for video in self.project.videos}
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for row, video_id in enumerate(self.video_ids):
            video = videos[video_id]
            self.table.insertRow(row)
            checkbox = QTableWidgetItem()
            checkbox.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
            )
            checkbox.setData(Qt.ItemDataRole.UserRole, video_id)
            checkbox.setCheckState(
                Qt.CheckState.Checked if video_id in self.checked_video_ids else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, checkbox)
            self.table.setItem(row, 1, QTableWidgetItem(str(row + 1)))
            self.table.setItem(row, 2, QTableWidgetItem(str(self.project.video_group(video_id))))
            name = QTableWidgetItem(video.name)
            name.setToolTip(video.source_path)
            self.table.setItem(row, 3, name)
            for column, step in enumerate(VISIBLE_BATCH_STEPS, start=4):
                label, detail = self.step_statuses[video_id][STEP_ORDER.index(step)]
                item = QTableWidgetItem(label)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setToolTip(detail)
                self.table.setItem(row, column, item)
        if self.video_ids:
            self.table.setCurrentCell(selected_row, 3)
            self.table.selectRow(selected_row)
            self.table.scrollToItem(self.table.item(selected_row, 3))
        self.table.blockSignals(False)
        self._refresh_actions()

    def _check_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        video_id = str(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_video_ids.add(video_id)
        else:
            self.checked_video_ids.discard(video_id)
        self._refresh_actions()

    def _set_all_checked(self, checked: bool) -> None:
        self.checked_video_ids = set(self.video_ids) if checked else set()
        self._refresh_table(max(0, self.table.currentRow()))

    def _refresh_actions(self) -> None:
        count = len(self.checked_video_ids)
        self.selection_label.setText(f"Đã chọn {count}/{len(self.video_ids)} video")
        self.select_all_button.setEnabled(not self.batch_busy and count < len(self.video_ids))
        self.clear_selection_button.setEnabled(not self.batch_busy and count > 0)
        for index, button in enumerate(self.step_buttons):
            button.setEnabled(not self.batch_busy and (count > 0 or index == 1))

    def set_batch_busy(self, busy: bool, step_number: int | None = None) -> None:
        self.batch_busy = busy
        if step_number is not None:
            self.active_step_number = step_number
        self.table.setEnabled(not busy)
        self.story_selector.setEnabled(not busy)
        self.story_editor.setReadOnly(busy or self.story_path is None)
        self._refresh_story_actions()
        if busy:
            self.update_batch_progress(0, f"Đang chuẩn bị batch Step {self.active_step_number}…")
        self._refresh_actions()

    def update_batch_progress(self, value: int, message: str) -> None:
        self.batch_progress.setValue(value)
        self.batch_label.setText(message)

    def refresh_video_status(self, video_id: str) -> None:
        video = self.project.video(video_id)
        if video is not None:
            self.step_statuses[video_id] = self._read_step_statuses(video)
            self._refresh_table(max(0, self.table.currentRow()))

    def batch_finished(
        self, succeeded: int, failed: int, story_error: str = "", review_video_count: int = 0,
        story_count: int = 0,
    ) -> None:
        self.set_batch_busy(False)
        title = f"Batch Step {self.active_step_number} hoàn tất"
        message = f"{title} · Thành công: {succeeded} · Lỗi: {failed}."
        if self.active_step_number == 2:
            if story_count:
                message += f"\nĐã tạo {story_count} story theo Group và nạp vào vùng Output Step 2."
            if story_error:
                message += "\nMột số Group chưa tạo được story; transcript đã xử lý vẫn được giữ."
        elif self.active_step_number == 3 and review_video_count:
            message += (
                f"\n{review_video_count} video có cảnh báo bản dịch; mở Kiểm tra / sửa bản dịch ở Step 3 để xem. "
                "Output đã lưu và vẫn có thể dùng cho Step 4 mà không cần sửa."
            )
        self.update_batch_progress(100, message)
        if story_error:
            box = QMessageBox(QMessageBox.Icon.Warning, title, message, parent=self)
            box.setDetailedText(story_error)
            box.exec()
        else:
            QMessageBox.information(self, title, message)

    def _step_clicked(self, number: int) -> None:
        if self.batch_busy or not self.checked_video_ids:
            return
        if number in {3, 7}:
            if number == 3 and not self._confirm_story_changes():
                return
            requested = {
                3: self.step3_requested,
                7: self.step7_requested,
            }[number]
            requested.emit([
                video_id for video_id in self.video_ids if video_id in self.checked_video_ids
            ])
            return

    def _step1_to_3_clicked(self) -> None:
        if self.batch_busy or not self.checked_video_ids or not self._confirm_story_changes():
            return
        self.step1_to_3_requested.emit([
            video_id for video_id in self.video_ids if video_id in self.checked_video_ids
        ])

    def _rebuild_1_2_clicked(self) -> None:
        if self.batch_busy or not self._confirm_story_changes():
            return
        self.rebuild_1_2_requested.emit()

    def done(self, result: int) -> None:
        if self.batch_busy:
            QMessageBox.information(self, "Batch đang chạy", f"Hãy chờ batch Step {self.active_step_number} hoàn tất trước khi đóng popup.")
            return
        if not self._confirm_story_changes():
            return
        super().done(result)
