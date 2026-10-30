from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from ..story_content import load_story_content


VISIBLE_BATCH_STEPS = (
    (StepId.EXTRACT, StepId.STT),
    (StepId.TRANSLATE,),
    (StepId.TTS, StepId.SYNC),
    (StepId.BUILD_AUDIO, StepId.RENDER),
)
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1


class StepBatchDialog(QDialog):
    """Select and order videos for a single-step batch."""

    rebuild_1_2_requested = Signal(object)
    single_step_requested = Signal(object, object)
    regenerate_stories_requested = Signal(object)
    step3_requested = Signal(object)
    step4_5_requested = Signal(object)
    step6_7_requested = Signal(object)

    def __init__(self, project: BatchProject, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.video_ids = [video.id for video in project.ordered_videos()]
        self.checked_video_ids: set[str] = set()
        self.checked_story_groups: set[int] = set()
        self.step_buttons: list[QPushButton] = []
        self.batch_busy = False
        self.model_session_key = ""
        self.loaded_model_steps: set[StepId] = set()
        self.active_step_number: int | float = 1
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
                *("Step " + "&".join(str(STEP_ORDER.index(step) + 1) for step in group)
                  for group in VISIBLE_BATCH_STEPS),
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
            self.table.setColumnWidth(column, 200)
        self.table.itemSelectionChanged.connect(self._refresh_actions)
        self.table.itemChanged.connect(self._check_changed)
        layout.addWidget(self.table, 1)

        helper = QLabel(
            "Tích checkbox để chọn video theo thứ tự chung đã lưu trong thiết lập project. "
            "Chọn một Step trong combobox rồi bấm Chạy step để chạy riêng Step đó cho các video đã tích. "
            "Step 1.5 nhập SRT/TXT cùng tên video trong thư mục input thành output Step 2 mới; ưu tiên SRT. "
            "Chạy lại từ 1–2 tạo output Step 1 và 2 mới "
            "cho video đã tích, không tạo story. Step 2.5 dùng TXT cùng tên trong input; "
            "nếu thiếu TXT, AI sửa ASR tiếng Trung theo story Group và tạo output Step 2 mới. "
            "Step 4&5 tạo voice và đồng bộ; "
            "Step 6&7 ghép audio rồi render video."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)
        single_step_actions = QHBoxLayout()
        self.single_step_selector = QComboBox()
        for label, value in (
            ("Step 1", 1), ("Step 1.5", 1.5), ("Step 2", 2),
            ("Step 2.5", 2.5), ("Step 3", 3), ("Step 4", 4),
            ("Step 5", 5), ("Step 6", 6), ("Step 7", 7),
        ):
            self.single_step_selector.addItem(label, value)
        self.single_step_button = QPushButton("Chạy step")
        self.single_step_button.clicked.connect(self._single_step_clicked)
        self.step_buttons.append(self.single_step_button)
        single_step_actions.addWidget(self.single_step_selector)
        single_step_actions.addWidget(self.single_step_button)
        single_step_actions.addStretch()
        layout.addLayout(single_step_actions)
        steps = QHBoxLayout()
        rebuild_button = QPushButton("Chạy lại từ 1–2")
        step3_button = QPushButton("Chạy Step 3")
        step4_5_button = QPushButton("Chạy Step 4&5")
        step6_7_button = QPushButton("Chạy Step 6&7")
        rebuild_button.clicked.connect(self._rebuild_1_2_clicked)
        step3_button.clicked.connect(lambda: self._step_clicked(3))
        step4_5_button.clicked.connect(lambda: self._step_clicked(4))
        step6_7_button.clicked.connect(lambda: self._step_clicked(6))
        self.step_buttons.extend((rebuild_button, step3_button, step4_5_button, step6_7_button))
        steps.addWidget(rebuild_button)
        steps.addStretch()
        steps.addWidget(step3_button)
        steps.addWidget(step4_5_button)
        steps.addWidget(step6_7_button)
        layout.addLayout(steps)
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 100)
        self.batch_progress.setValue(0)
        self.batch_label = QLabel("Sẵn sàng chạy step đã chọn hoặc các lệnh batch bên dưới.")
        self.batch_label.setWordWrap(True)
        layout.addWidget(self.batch_progress)
        layout.addWidget(self.batch_label)
        lower = QWidget()
        story_layout = QVBoxLayout(lower)
        story_layout.setContentsMargins(0, 0, 0, 0)
        story_layout.addWidget(QLabel("Output Step 2 — Story"))
        story_actions = QHBoxLayout()
        self.story_selector = QComboBox()
        self.story_selector.setMinimumContentsLength(18)
        self.story_selector.setMaximumWidth(330)
        story_actions.addWidget(self.story_selector)
        self.story_group_checkbox = QCheckBox("Group cần sửa")
        self.story_group_checkbox.toggled.connect(self._story_group_toggled)
        story_actions.addWidget(self.story_group_checkbox)
        self.regenerate_story_button = QPushButton("Chạy lại")
        self.regenerate_story_button.clicked.connect(self._regenerate_story_clicked)
        story_actions.addWidget(self.regenerate_story_button)
        story_actions.addStretch()
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
        linked_paths: set[str] = set()
        for group in range(1, group_count + 1):
            path = self.project.story_path_for_group(group)
            if path is None or not path.is_file():
                path = None
            if path is not None:
                linked_paths.add(str(path.resolve()))
            self.story_selector.addItem(
                f"[Group {group}] {path.stem if path else 'Chưa có story'}",
                str(path) if path else None,
            )
            self.story_selector.setItemData(self.story_selector.count() - 1, group, GROUP_ROLE)
        for path in paths:
            if str(path.resolve()) not in linked_paths:
                self.story_selector.addItem(path.stem, str(path))
        if not self.story_selector.count():
            self.story_selector.addItem("Chưa có Group", None)
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
            content = load_story_content(path) if path else ""
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Không thể đọc story", str(exc))
            self.story_selector.blockSignals(True)
            self.story_selector.setCurrentIndex(self.story_index)
            self.story_selector.blockSignals(False)
            return
        self.story_path = path
        self.story_index = index
        group = self.story_selector.itemData(index, GROUP_ROLE)
        self.story_group_checkbox.blockSignals(True)
        self.story_group_checkbox.setChecked(group in self.checked_story_groups)
        self.story_group_checkbox.blockSignals(False)
        self.story_group_checkbox.setEnabled(group is not None and not self.batch_busy)
        self.story_editor.setPlainText(content)
        self.story_editor.document().setModified(False)
        self.story_editor.setReadOnly(self.batch_busy or path is None)
        self._refresh_story_actions()

    def _refresh_story_actions(self) -> None:
        dirty = self.story_editor.document().isModified()
        self.save_story_button.setEnabled(self.story_path is not None and dirty and not self.batch_busy)
        self.regenerate_story_button.setEnabled(bool(self.checked_story_groups) and not self.batch_busy)
        self.story_status.setText(
            "Có thay đổi chưa lưu" if dirty
            else str(self.story_path) if self.story_path
            else "Chọn Group để xem hoặc tạo story từ transcript đã lưu."
        )

    def _story_group_toggled(self, checked: bool) -> None:
        group = self.story_selector.currentData(GROUP_ROLE)
        if group is None:
            return
        if checked:
            self.checked_story_groups.add(int(group))
        else:
            self.checked_story_groups.discard(int(group))
        self._refresh_story_actions()

    def _regenerate_story_clicked(self) -> None:
        if self.batch_busy or not self.checked_story_groups or not self._confirm_story_changes():
            return
        self.regenerate_stories_requested.emit(sorted(self.checked_story_groups))

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
            content = load_story_content(self.story_path) if self.story_path else ""
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
            for column, group in enumerate(VISIBLE_BATCH_STEPS, start=4):
                statuses = [self.step_statuses[video_id][STEP_ORDER.index(step)] for step in group]
                label = statuses[0][0]
                if len(group) > 1:
                    for priority in ("Đang chạy", "Lỗi", "Cần cập nhật", "Chưa chạy", "Không rõ"):
                        matching = next(
                            (step for step, (status, _) in zip(group, statuses) if status == priority),
                            None,
                        )
                        if matching is not None:
                            label = f"{priority} Step {STEP_ORDER.index(matching) + 1}"
                            break
                    else:
                        label = "Hoàn thành"
                detail = "\n".join(
                    f"Step {STEP_ORDER.index(step) + 1}: {message}"
                    for step, (_, message) in zip(group, statuses)
                )
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
        for button in self.step_buttons:
            button.setEnabled(not self.batch_busy and count > 0)
        self.single_step_selector.setEnabled(not self.batch_busy)

    def set_batch_busy(self, busy: bool, step_number: int | float | None = None) -> None:
        self.batch_busy = busy
        if step_number is not None:
            self.active_step_number = step_number
        self.table.setEnabled(not busy)
        self.story_selector.setEnabled(not busy)
        self.story_group_checkbox.setEnabled(
            not busy and self.story_selector.currentData(GROUP_ROLE) is not None
        )
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
        story_count: int = 0, *, single_step: bool = False,
    ) -> None:
        self.set_batch_busy(False)
        step_label = (
            "4&5" if not single_step and self.active_step_number in {4, 5}
            else "6&7" if not single_step and self.active_step_number in {6, 7}
            else str(self.active_step_number)
        )
        title = f"Batch Step {step_label} hoàn tất"
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

    def story_regeneration_finished(self, created: int, total: int, error: str) -> None:
        self.set_batch_busy(False)
        message = f"Đã tạo lại story cho {created}/{total} Group."
        self.update_batch_progress(100, message)
        if error:
            box = QMessageBox(QMessageBox.Icon.Warning, "Tạo lại story", message, parent=self)
            box.setDetailedText(error)
            box.exec()
        else:
            QMessageBox.information(self, "Tạo lại story", message)

    def step2_5_finished(self, succeeded: int, failed: int, details: list[str]) -> None:
        self.set_batch_busy(False)
        title = "Batch Step 2.5 hoàn tất"
        message = f"{title} · Thành công: {succeeded} · Lỗi: {failed}."
        self.update_batch_progress(100, message)
        box = QMessageBox(
            QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information,
            title, message, parent=self,
        )
        if details:
            box.setDetailedText("\n".join(details))
        box.exec()

    def text_import_finished(self, succeeded: int, failed: int, details: list[str]) -> None:
        self.set_batch_busy(False)
        title = "Batch Step 1.5 hoàn tất"
        message = f"{title} · Thành công: {succeeded} · Lỗi: {failed}."
        self.update_batch_progress(100, message)
        box = QMessageBox(
            QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information,
            title, message, parent=self,
        )
        if details:
            box.setDetailedText("\n".join(details))
        box.exec()

    def _step_clicked(self, number: int) -> None:
        if self.batch_busy or not self.checked_video_ids:
            return
        if number in {3, 4, 6}:
            if number == 3 and not self._confirm_story_changes():
                return
            requested = {
                3: self.step3_requested,
                4: self.step4_5_requested,
                6: self.step6_7_requested,
            }[number]
            requested.emit([
                video_id for video_id in self.video_ids if video_id in self.checked_video_ids
            ])
            return

    def _single_step_clicked(self) -> None:
        if self.batch_busy or not self.checked_video_ids:
            return
        number = self.single_step_selector.currentData()
        if number in {2, 3} and not self._confirm_story_changes():
            return
        self.single_step_requested.emit(
            number,
            [video_id for video_id in self.video_ids if video_id in self.checked_video_ids],
        )

    def _rebuild_1_2_clicked(self) -> None:
        if self.batch_busy or not self.checked_video_ids or not self._confirm_story_changes():
            return
        self.rebuild_1_2_requested.emit([
            video_id for video_id in self.video_ids if video_id in self.checked_video_ids
        ])

    def done(self, result: int) -> None:
        if self.batch_busy:
            QMessageBox.information(self, "Batch đang chạy", f"Hãy chờ batch Step {self.active_step_number} hoàn tất trước khi đóng popup.")
            return
        if not self._confirm_story_changes():
            return
        super().done(result)
