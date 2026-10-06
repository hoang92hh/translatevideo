import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...models import StepId, StepResult
from ..sync_repair_dialog import SyncRepairDialog
from ..specs import FieldSpec, ProviderSpec, StepSpec
from .base import StepPage


class AudioSyncStepPage(StepPage):
    ai_rewrite_requested = Signal(str, object)
    batch_repair_requested = Signal(str, object)
    neighbor_borrow_requested = Signal(str)

    SPEC = StepSpec(
        StepId.SYNC,
        "05",
        "Audio Sync",
        "Căn thời lượng audio mới vào timestamp của video gốc.",
        (
            ProviderSpec(
                "FFmpeg — Duration Sync",
                (
                    FieldSpec("max_speed", "Tốc độ tối đa", "float", 1.35),
                    FieldSpec("use_gap", "Tận dụng khoảng trống kế tiếp", "bool", True),
                    FieldSpec("trim_silence", "Cắt khoảng lặng đầu/cuối", "bool", True),
                ),
            ),
        ),
    )

    def __init__(self, state) -> None:
        self._candidate_payload: dict[str, object] = {}
        self._loaded_manifest_signature: tuple[str, int, int] | None = None
        self._repairing = False
        self._repair_dialog: SyncRepairDialog | None = None
        super().__init__(state)

    def build_result_extra(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        title = QLabel("Kiểm tra output Audio Sync")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        self.sync_combo = QComboBox()
        self.sync_combo.currentIndexChanged.connect(self._candidate_changed)
        layout.addWidget(self.sync_combo)
        self.sync_info = QLabel("Chưa có output Step 5")
        self.sync_info.setObjectName("muted")
        self.sync_info.setWordWrap(True)
        layout.addWidget(self.sync_info)

        error_title = QLabel("Segment cần xử lý")
        error_title.setObjectName("cardTitle")
        layout.addWidget(error_title)
        self.error_info = QLabel("Không có segment đang lỗi")
        self.error_info.setObjectName("muted")
        self.error_info.setWordWrap(True)
        layout.addWidget(self.error_info)
        self.repair_button = QPushButton("Mở danh sách xử lý segment")
        self.repair_button.clicked.connect(self._open_repair_dialog)
        layout.addWidget(self.repair_button)

        actions = QHBoxLayout()
        use_button = QPushButton("Dùng làm input Step 6")
        use_button.clicked.connect(self._select_for_step_six)
        open_manifest = QPushButton("Mở manifest")
        open_manifest.clicked.connect(self._open_manifest)
        open_folder = QPushButton("Mở thư mục")
        open_folder.clicked.connect(self._open_folder)
        delete_button = QPushButton("Xóa output")
        delete_button.clicked.connect(self._delete_output)
        for button in (use_button, open_manifest, open_folder, delete_button):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return container

    def _show_result(self, result: StepResult) -> None:
        super()._show_result(result)
        for row in range(self.table.rowCount()):
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def refresh_special(self) -> None:
        if hasattr(self, "sync_combo") and not self._repairing:
            self._refresh_candidates()

    def set_busy(self, busy: bool) -> None:
        super().set_busy(busy)
        if not busy and hasattr(self, "sync_combo"):
            self._refresh_candidates(prefer_current=True)

    def _refresh_candidates(self, prefer_current: bool = False) -> None:
        current = self.sync_combo.currentData()
        result = self.state.results.get(StepId.SYNC)
        result_id = str(result.metadata.get("sync_candidate_id", "")) if result else ""
        self.sync_combo.blockSignals(True)
        self.sync_combo.clear()
        for candidate in self.state.sync_candidates.values():
            suffix = "  [Input Step 6]" if candidate.id == self.state.selected_sync_candidate_id else ""
            if candidate.error_count:
                suffix += f"  [Cần sửa: {candidate.error_count}]"
            self.sync_combo.addItem(f"{candidate.label}{suffix}", candidate.id)
        target_id = result_id if prefer_current else (current or result_id)
        target = self.sync_combo.findData(target_id)
        self.sync_combo.setCurrentIndex(target if target >= 0 else 0)
        self.sync_combo.blockSignals(False)
        self._candidate_changed()

    def _current_candidate(self):
        return self.state.sync_candidate(str(self.sync_combo.currentData() or ""))

    def _candidate_changed(self, *_: object) -> None:
        candidate = self._current_candidate()
        if not candidate:
            self._candidate_payload = {}
            self._loaded_manifest_signature = None
            self.sync_info.setText("Chưa có output Step 5")
            self._fill_segments([])
            return
        manifest = Path(candidate.path)
        try:
            stat = manifest.stat()
            signature = (str(manifest.resolve()), stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        if signature and signature == self._loaded_manifest_signature and self._candidate_payload:
            return
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._candidate_payload = {}
            self._loaded_manifest_signature = None
            self.sync_info.setText(f"Không thể đọc manifest: {exc}")
            self._fill_segments([])
            return
        self._candidate_payload = payload
        self._loaded_manifest_signature = signature
        errors = int(payload.get("error_count", 0))
        status = "Hoàn thành" if not errors else f"Cần sửa {errors} segment"
        self.sync_info.setText(f"{status} · {candidate.segment_count} segment\n{candidate.folder}")
        raw_segments = payload.get("segments", [])
        self._fill_segments(raw_segments if isinstance(raw_segments, list) else [])

    def _fill_segments(self, segments: list[object]) -> None:
        valid = [item for item in segments if isinstance(item, dict)]
        errors = [item for item in valid if item.get("status") != "ready"]
        if errors:
            self.error_info.setText(
                f"{len(errors)} segment đang lỗi. "
                "Mở popup để AI chỉnh sửa theo checkbox hoặc sửa nội dung thủ công."
            )
            self.repair_button.setEnabled(not self._repairing)
        else:
            self.error_info.setText("Không có segment đang lỗi.")
            self.repair_button.setEnabled(False)

    def _segments(self) -> list[dict[str, object]]:
        raw = self._candidate_payload.get("segments", [])
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    def _open_repair_dialog(self) -> None:
        candidate = self._current_candidate()
        if not candidate or not self._candidate_payload:
            return
        if self._repair_dialog and self._repair_dialog.isVisible():
            self._repair_dialog.raise_()
            self._repair_dialog.activateWindow()
            return
        dialog = SyncRepairDialog(candidate.id, dict(self._candidate_payload), self)
        dialog.ai_requested.connect(lambda texts: self.ai_rewrite_requested.emit(candidate.id, texts))
        dialog.process_requested.connect(lambda texts: self.batch_repair_requested.emit(candidate.id, texts))
        dialog.borrow_requested.connect(lambda: self.neighbor_borrow_requested.emit(candidate.id))
        dialog.drafts_changed.connect(lambda texts: self._save_repair_drafts(candidate.id, texts))
        dialog.finished.connect(lambda *_: setattr(self, "_repair_dialog", None))
        self._repair_dialog = dialog
        dialog.show()

    def _save_repair_drafts(self, candidate_id: str, texts: dict[int, str]) -> None:
        candidate = self.state.sync_candidate(candidate_id)
        if not candidate or not Path(candidate.path).is_file():
            return
        try:
            payload = json.loads(Path(candidate.path).read_text(encoding="utf-8"))
            raw_segments = payload.get("segments", [])
            if not isinstance(raw_segments, list):
                return
            changed = False
            for item in raw_segments:
                if not isinstance(item, dict):
                    continue
                segment_id = int(item.get("id", -1))
                if segment_id not in texts:
                    continue
                text = texts[segment_id].strip()
                previous = str(item.get("draft_text") or item.get("translated_text", "")).strip()
                if text and text != previous:
                    item["draft_text"] = text
                    item["edit_source"] = "manual"
                    item["repair_status"] = "awaiting_voice"
                    changed = True
            if not changed:
                return
            manifest = Path(candidate.path)
            temporary = manifest.with_suffix(f"{manifest.suffix}.part")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(manifest)
            self._candidate_payload = payload
            self._loaded_manifest_signature = None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return

    def set_repair_busy(self, busy: bool, message: str = "") -> None:
        self._repairing = busy
        self.repair_button.setEnabled(not busy and any(
            item.get("status") != "ready"
            for item in self._segments()
        ))
        self.repair_button.setText("Đang xử lý…" if busy else "Mở danh sách xử lý segment")
        if self._repair_dialog:
            self._repair_dialog.set_busy(busy, message)
        self.run_button.setEnabled(not busy and self.state.can_run(StepId.SYNC))

    def ai_rewrite_finished(self, translations: dict[int, str], message: str) -> None:
        self.set_repair_busy(False)
        for item in self._segments():
            segment_id = int(item.get("id", -1))
            if segment_id in translations:
                item["draft_text"] = translations[segment_id]
                item["edit_source"] = "ai"
                item["repair_status"] = "awaiting_voice"
        self._loaded_manifest_signature = None
        if self._repair_dialog:
            self._repair_dialog.apply_ai_results(translations)
            self._repair_dialog.set_busy(False, message)

    def repair_finished(self, candidate_id: str) -> None:
        self.set_repair_busy(False)
        self._loaded_manifest_signature = None
        self._refresh_candidates(prefer_current=True)
        target = self.sync_combo.findData(candidate_id)
        if target >= 0:
            self.sync_combo.setCurrentIndex(target)
        if self._repair_dialog and self._repair_dialog.candidate_id == candidate_id:
            self._repair_dialog.load_payload(dict(self._candidate_payload))

    def _select_for_step_six(self) -> None:
        candidate = self._current_candidate()
        if candidate and self.state.select_sync_candidate(candidate.id):
            self._refresh_candidates()
            return
        QMessageBox.information(
            self,
            "Output chưa sẵn sàng",
            "Hãy sửa hết segment lỗi và bảo đảm các file đã đồng bộ còn tồn tại.",
        )

    def _open_manifest(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.path))

    def _open_folder(self) -> None:
        candidate = self._current_candidate()
        if candidate and Path(candidate.folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(candidate.folder))

    def _delete_output(self) -> None:
        candidate = self._current_candidate()
        if not candidate:
            return
        answer = QMessageBox.question(
            self,
            "Xóa output Audio Sync",
            f"Xóa {candidate.label} và toàn bộ file của lần chạy này?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        target = Path(candidate.folder).resolve()
        if self.state.project:
            root = self.state.project.path("synchronized_audio").resolve()
            if target.is_relative_to(root) and target != root and target.is_dir():
                shutil.rmtree(target)
        self.state.remove_sync_candidate(candidate.id)
        self._refresh_candidates()
