from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..batch_project import BatchProject, BatchProjectService
from ..config.gemini import GEMINI_PROVIDER_NAME
from ..config.prompt_templates import DEFAULT_PROMPT_TEMPLATE_ID, load_prompt_template
from ..config.tts import (
    EDGE_TTS_PROVIDER,
    EDGE_VOICES,
    MELO_OPENVOICE_PROVIDER,
    MELO_SPEAKERS,
    PIPER_PROVIDER,
    VIENEU_PROVIDER,
    VIENEU_VOICES,
)
from ..models import StepId
from ..paths import projects_root
from .components import Card, FilePicker, ProviderPanel
from .steps.audio_sync import AudioSyncStepPage
from .steps.build_audio import BuildAudioStepPage
from .steps.extract import ExtractStepPage
from .steps.speech_to_text import SpeechToTextStepPage
from .translation_components import TranslationProviderSelector
from .tts_components.base import device_control, speed_control
from .tts_components.piper import PiperModelPicker, native_parameter


LANGUAGES = ("Chinese", "English", "Vietnamese", "Spanish", "Japanese", "Korean")


def _scroll_page(title: str, description: str, content: QWidget) -> QWidget:
    page = QWidget()
    root = QVBoxLayout(page)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    helper = QLabel(description)
    helper.setObjectName("muted")
    helper.setWordWrap(True)
    root.addWidget(heading)
    root.addWidget(helper)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 12, 12, 12)
    layout.addWidget(content)
    layout.addStretch()
    scroll.setWidget(body)
    root.addWidget(scroll, 1)
    return page


class SingleVoiceTtsSettings(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = Card(
            "Một giọng chung",
            "Mọi speaker và mọi video trong project sử dụng cùng một cấu hình giọng đọc.",
        )
        self.provider = QComboBox()
        self.provider.addItems((VIENEU_PROVIDER, PIPER_PROVIDER, MELO_OPENVOICE_PROVIDER, EDGE_TTS_PROVIDER))
        card.content_layout.addWidget(self.provider)
        self.stack = QStackedWidget()
        self.reference_pages: dict[str, dict[str, QWidget]] = {}
        self._add_reference_page(VIENEU_PROVIDER, VIENEU_VOICES, "Giọng")
        self._add_piper_page()
        self._add_reference_page(MELO_OPENVOICE_PROVIDER, MELO_SPEAKERS, "Giọng nền")
        self._add_edge_page()
        card.content_layout.addWidget(self.stack)
        self.provider.currentIndexChanged.connect(self.stack.setCurrentIndex)
        root.addWidget(card)

    def _add_reference_page(self, provider: str, voices: tuple[str, ...], label: str) -> None:
        page = QWidget()
        form = QFormLayout(page)
        voice = QComboBox()
        voice.addItems(voices)
        speed = speed_control()
        device = device_control()
        reference = FilePicker()
        consent = QCheckBox("Tôi có quyền sử dụng giọng tham chiếu")
        consent.setChecked(True)
        form.addRow(label, voice)
        form.addRow("Tốc độ", speed)
        form.addRow("Thiết bị", device)
        form.addRow("File giọng tham chiếu", reference)
        form.addRow("Quyền sử dụng", consent)
        self.reference_pages[provider] = {
            "voice": voice,
            "speed": speed,
            "device": device,
            "reference": reference,
            "consent": consent,
        }
        self.stack.addWidget(page)

    def _add_piper_page(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.piper_model = PiperModelPicker()
        self.piper_speaker = QSpinBox()
        self.piper_speaker.setRange(0, 999)
        self.piper_length = native_parameter(0.1, 4.0, 1.0)
        self.piper_noise = native_parameter(0.0, 2.0, 0.667)
        self.piper_noise_w = native_parameter(0.0, 2.0, 0.8)
        self.piper_volume = native_parameter(0.0, 4.0, 1.0)
        self.piper_normalize = QCheckBox("Chuẩn hóa biên độ đầu ra")
        self.piper_normalize.setChecked(True)
        form.addRow("Voice model Piper (.onnx)", self.piper_model)
        form.addRow("Speaker ID trong model", self.piper_speaker)
        form.addRow("Độ dài âm vị", self.piper_length)
        form.addRow("Độ biến thiên", self.piper_noise)
        form.addRow("Biến thiên trường độ", self.piper_noise_w)
        form.addRow("Âm lượng", self.piper_volume)
        form.addRow("Xử lý đầu ra", self.piper_normalize)
        self.stack.addWidget(page)

    def _add_edge_page(self) -> None:
        page = QWidget()
        form = QFormLayout(page)
        self.edge_voice = QComboBox()
        self.edge_voice.addItems(EDGE_VOICES)
        self.edge_speed = speed_control()
        form.addRow("Giọng online", self.edge_voice)
        form.addRow("Tốc độ", self.edge_speed)
        self.stack.addWidget(page)

    def values(self) -> dict[str, Any]:
        provider = self.provider.currentText()
        base: dict[str, Any] = {
            "provider": provider,
            "single_voice": True,
            "speaker_profiles": {},
            "speaker_group_profiles": {},
            "speaker_profile_assignments": {},
        }
        if provider in self.reference_pages:
            controls = self.reference_pages[provider]
            voice = controls["voice"].currentText()
            reference = controls["reference"].value()
            profile = {"voice": voice, "reference_voice": reference}
            return {
                **base,
                "voice": voice,
                "speed": controls["speed"].value(),
                "device": controls["device"].currentText(),
                "reference_voice": reference,
                "voice_consent": controls["consent"].isChecked(),
                "shared_speaker_profile": profile,
            }
        if provider == PIPER_PROVIDER:
            model = self.piper_model.value()
            speaker_id = self.piper_speaker.value()
            return {
                **base,
                "voice": model,
                "model_path": model,
                "speed": 1.0,
                "device": "CPU",
                "length_scale": self.piper_length.value(),
                "noise_scale": self.piper_noise.value(),
                "noise_w_scale": self.piper_noise_w.value(),
                "volume": self.piper_volume.value(),
                "normalize_audio": self.piper_normalize.isChecked(),
                "shared_speaker_profile": {
                    "voice": model,
                    "model_path": model,
                    "piper_speaker_id": speaker_id,
                },
            }
        voice = self.edge_voice.currentText()
        return {
            **base,
            "voice": voice,
            "speed": self.edge_speed.value(),
            "shared_speaker_profile": {"voice": voice},
        }

    def set_values(self, values: dict[str, Any]) -> None:
        provider = str(values.get("provider", VIENEU_PROVIDER))
        index = self.provider.findText(provider)
        self.provider.setCurrentIndex(index if index >= 0 else 0)
        profile = values.get("shared_speaker_profile", {})
        profile = profile if isinstance(profile, dict) else {}
        if provider in self.reference_pages:
            controls = self.reference_pages[provider]
            controls["voice"].setCurrentText(str(values.get("voice", profile.get("voice", ""))))
            controls["speed"].setValue(float(values.get("speed", 1.0)))
            controls["device"].setCurrentText(str(values.get("device", "Auto")))
            controls["reference"].field.setText(
                str(values.get("reference_voice", profile.get("reference_voice", "")))
            )
            controls["consent"].setChecked(bool(values.get("voice_consent", True)))
        elif provider == PIPER_PROVIDER:
            self.piper_model.field.setText(str(values.get("model_path", profile.get("model_path", ""))))
            self.piper_speaker.setValue(int(profile.get("piper_speaker_id", 0) or 0))
            self.piper_length.setValue(float(values.get("length_scale", 1.0)))
            self.piper_noise.setValue(float(values.get("noise_scale", 0.667)))
            self.piper_noise_w.setValue(float(values.get("noise_w_scale", 0.8)))
            self.piper_volume.setValue(float(values.get("volume", 1.0)))
            self.piper_normalize.setChecked(bool(values.get("normalize_audio", True)))
        elif provider == EDGE_TTS_PROVIDER:
            self.edge_voice.setCurrentText(str(values.get("voice", profile.get("voice", ""))))
            self.edge_speed.setValue(float(values.get("speed", 1.0)))


class RenderSettings(QWidget):
    def __init__(self) -> None:
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = Card("Thành phần video", "Thiết lập chung áp dụng cho mọi output trong batch.")
        form = QFormLayout()
        voice_volume_control, self.voice_volume = self._volume_control(100)
        self.original_voice = QCheckBox("Trộn voice gốc nếu Step 1 có tách Voice")
        self.original_voice.setChecked(True)
        original_voice_volume_control, self.original_voice_volume = self._volume_control(20)
        self.background = QCheckBox("Trộn background nếu Step 1 có tách Background")
        self.background.setChecked(True)
        background_volume_control, self.background_volume = self._volume_control(80)
        self.subtitle = QCheckBox("Tạo subtitle SRT")
        self.burn_subtitle = QCheckBox("Burn subtitle vào video")
        self.subtitle.toggled.connect(self.burn_subtitle.setEnabled)
        self.burn_subtitle.setEnabled(False)
        form.addRow("Âm lượng voice mới", voice_volume_control)
        form.addRow("Voice gốc", self.original_voice)
        form.addRow("Âm lượng voice gốc", original_voice_volume_control)
        form.addRow("Background", self.background)
        form.addRow("Âm lượng background", background_volume_control)
        form.addRow("Subtitle", self.subtitle)
        form.addRow("Phụ đề trong hình", self.burn_subtitle)
        card.content_layout.addLayout(form)
        root.addWidget(card)

    @staticmethod
    def _volume_control(value: int) -> tuple[QWidget, QSlider]:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        control = QSlider(Qt.Orientation.Horizontal)
        control.setRange(0, 100)
        control.setValue(value)
        value_label = QLabel(f"{value}%")
        value_label.setMinimumWidth(44)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        control.valueChanged.connect(lambda current: value_label.setText(f"{current}%"))
        layout.addWidget(control, 1)
        layout.addWidget(value_label)
        return container, control

    def values(self) -> dict[str, Any]:
        return {
            "provider": "FFmpeg Renderer",
            "include_video": True,
            "include_voice": True,
            "voice_volume": self.voice_volume.value() / 100,
            "include_original_voice": self.original_voice.isChecked(),
            "original_voice_volume": self.original_voice_volume.value() / 100,
            "include_background": self.background.isChecked(),
            "background_volume": self.background_volume.value() / 100,
            "subtitle": self.subtitle.isChecked(),
            "burn_subtitle": self.subtitle.isChecked() and self.burn_subtitle.isChecked(),
        }

    def set_values(self, values: dict[str, Any]) -> None:
        self.voice_volume.setValue(round(float(values.get("voice_volume", 1.0)) * 100))
        self.original_voice.setChecked(bool(values.get("include_original_voice", True)))
        self.original_voice_volume.setValue(round(float(values.get("original_voice_volume", 0.2)) * 100))
        self.background.setChecked(bool(values.get("include_background", True)))
        self.background_volume.setValue(round(float(values.get("background_volume", 0.8)) * 100))
        self.subtitle.setChecked(bool(values.get("subtitle", False)))
        self.burn_subtitle.setChecked(bool(values.get("burn_subtitle", False)))


class ProjectSettingsDialog(QDialog):
    def __init__(self, project: BatchProject | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self._first_show = True
        self.setWindowTitle("Thiết lập project" if project else "Tạo project mới")
        self.setMinimumSize(1100, 700)
        self.resize(1500, 900)
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setFixedWidth(235)
        self.stack = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        self._build_project_page()
        self.step_panels: dict[StepId, ProviderPanel] = {}
        self._add_provider_page(ExtractStepPage.SPEC)
        self._add_provider_page(SpeechToTextStepPage.SPEC)
        self._build_translation_page()
        self._build_tts_page()
        self._add_provider_page(AudioSyncStepPage.SPEC)
        self._add_provider_page(BuildAudioStepPage.SPEC)
        self._build_render_page()
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Lưu thiết lập" if project else "Tạo project"
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if project:
            self._load_project(project)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._first_show:
            return
        self._first_show = False
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            width = min(1600, max(self.minimumWidth(), round(available.width() * 0.94)))
            height = min(1000, max(self.minimumHeight(), round(available.height() * 0.92)))
            self.resize(width, height)
            self.move(available.center() - self.rect().center())

    def _add_section(self, label: str, page: QWidget) -> None:
        self.navigation.addItem(label)
        self.stack.addWidget(page)

    def _build_project_page(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)
        card = Card("Thông tin project", "Chọn folder input và các video nằm trực tiếp trong folder.")
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Ví dụ: gioi-thieu-san-pham")
        self.input_folder = QLineEdit()
        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.addWidget(self.input_folder)
        self.choose_input_button = QPushButton("Chọn thư mục")
        self.choose_input_button.clicked.connect(self._choose_input_folder)
        input_layout.addWidget(self.choose_input_button)
        self.location = QLineEdit(str(projects_root()))
        location_row = QWidget()
        location_layout = QHBoxLayout(location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.addWidget(self.location)
        self.choose_location_button = QPushButton("Chọn nơi lưu")
        self.choose_location_button.clicked.connect(self._choose_location)
        location_layout.addWidget(self.choose_location_button)
        self.source_language = QComboBox()
        self.source_language.addItems(LANGUAGES)
        self.source_language.setCurrentText("Chinese")
        self.target_language = QComboBox()
        self.target_language.addItems(LANGUAGES)
        self.target_language.setCurrentText("Vietnamese")
        form.addRow("Tên project", self.name)
        form.addRow("Thư mục input", input_row)
        form.addRow("Lưu project tại", location_row)
        form.addRow("Ngôn ngữ nguồn", self.source_language)
        form.addRow("Ngôn ngữ đích", self.target_language)
        card.content_layout.addLayout(form)
        layout.addWidget(card)
        self.video_table = QTableWidget(0, 3)
        self.video_table.setHorizontalHeaderLabels(["Chọn", "Video", "Trạng thái"])
        self.video_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.video_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.video_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.video_table.verticalHeader().setVisible(False)
        layout.addWidget(self.video_table, 1)
        self._add_section("Project & Input", _scroll_page("Project & Input", "Thông tin chung và danh sách video.", content))

    def _add_provider_page(self, spec) -> None:
        panel = ProviderPanel(spec.providers)
        self.step_panels[spec.step] = panel
        self._add_section(
            f"Step {spec.number}",
            _scroll_page(f"Step {spec.number} — {spec.title}", spec.description, panel),
        )

    def _build_translation_page(self) -> None:
        self.translation = TranslationProviderSelector()
        self._add_section(
            "Step 03",
            _scroll_page(
                "Step 03 — Translation",
                "Chọn provider mặc định và prompt template dùng riêng cho project.",
                self.translation,
            ),
        )

    def _build_tts_page(self) -> None:
        self.tts = SingleVoiceTtsSettings()
        self._add_section(
            "Step 04",
            _scroll_page("Step 04 — Text to Speech", "Một giọng duy nhất cho mọi speaker.", self.tts),
        )

    def _build_render_page(self) -> None:
        self.render = RenderSettings()
        self._add_section(
            "Step 07",
            _scroll_page("Step 07 — Render & Export", "Thiết lập video đầu ra.", self.render),
        )

    def _choose_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục input", self.input_folder.text())
        if not folder:
            return
        self.input_folder.setText(folder)
        if not self.name.text().strip():
            self.name.setText(Path(folder).name)
        self._load_videos(folder)

    def _choose_location(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Chọn nơi lưu project", self.location.text())
        if folder:
            self.location.setText(folder)

    def _load_videos(self, folder: str, selected: set[str] | None = None) -> None:
        try:
            videos = BatchProjectService.scan_input_folder(folder)
        except Exception as exc:
            QMessageBox.critical(self, "Không thể đọc thư mục input", str(exc))
            return
        self.video_table.setRowCount(0)
        for video in videos:
            row = self.video_table.rowCount()
            self.video_table.insertRow(row)
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            checked.setCheckState(
                Qt.CheckState.Checked if selected is None or video.path in selected else Qt.CheckState.Unchecked
            )
            checked.setData(Qt.ItemDataRole.UserRole, video.path)
            self.video_table.setItem(row, 0, checked)
            self.video_table.setItem(row, 1, QTableWidgetItem(video.name))
            self.video_table.setItem(row, 2, QTableWidgetItem(video.status))

    def _selected_videos(self) -> list[str]:
        return [
            str(self.video_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in range(self.video_table.rowCount())
            if self.video_table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _translation_values(self) -> dict[str, Any]:
        return dict(self.translation.settings())

    def settings_values(self) -> dict[str, dict[str, Any]]:
        return {
            StepId.EXTRACT.value: self.step_panels[StepId.EXTRACT].values(),
            StepId.STT.value: self.step_panels[StepId.STT].values(),
            StepId.TRANSLATE.value: self._translation_values(),
            StepId.TTS.value: self.tts.values(),
            StepId.SYNC.value: self.step_panels[StepId.SYNC].values(),
            StepId.BUILD_AUDIO.value: self.step_panels[StepId.BUILD_AUDIO].values(),
            StepId.RENDER.value: self.render.values(),
        }

    def values(self) -> dict[str, Any]:
        return {
            "name": self.name.text().strip(),
            "input_folder": self.input_folder.text().strip(),
            "selected_videos": self._selected_videos(),
            "parent_folder": self.location.text().strip(),
            "source_language": self.source_language.currentText(),
            "target_language": self.target_language.currentText(),
            "batch_settings": self.settings_values(),
        }

    def _load_project(self, project: BatchProject) -> None:
        self.name.setText(project.name)
        self.name.setReadOnly(True)
        self.input_folder.setText(project.input_folder)
        self.input_folder.setReadOnly(True)
        self.choose_input_button.setEnabled(False)
        self.location.setText(str(project.root_path.parent))
        self.location.setReadOnly(True)
        self.choose_location_button.setEnabled(False)
        self.source_language.setCurrentText(project.source_language)
        self.target_language.setCurrentText(project.target_language)
        self._load_videos(project.input_folder, {video.source_path for video in project.videos})
        for row in range(self.video_table.rowCount()):
            item = self.video_table.item(row, 0)
            if item:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        settings = project.batch_settings
        for step, panel in self.step_panels.items():
            panel.set_values(dict(settings.get(step.value, {})))
        self._set_translation_values(dict(settings.get(StepId.TRANSLATE.value, {})))
        self.tts.set_values(dict(settings.get(StepId.TTS.value, {})))
        self.render.set_values(dict(settings.get(StepId.RENDER.value, {})))

    def _set_translation_values(self, values: dict[str, Any]) -> None:
        provider = str(values.get("provider", GEMINI_PROVIDER_NAME))
        component = self.translation.set_default_provider(provider)
        for key in ("model", "proper_name_mode"):
            control = getattr(component, key, None)
            if isinstance(control, QComboBox):
                control.setCurrentText(str(values.get(key, control.currentText())))
        for key in ("batch_size",):
            control = getattr(component, key, None)
            if isinstance(control, QSpinBox):
                control.setValue(int(values.get(key, control.value())))
        all_project_segments = getattr(component, "all_project_segments", None)
        if isinstance(all_project_segments, QCheckBox):
            all_project_segments.setChecked(
                bool(values.get("all_project_segments", True))
            )
        project_url = getattr(component, "project_url", None)
        if isinstance(project_url, QLineEdit):
            project_url.setText(str(values.get("project_url", "")))
        self.translation.set_prompt_template(
            str(values.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID))
        )

    def _validate_and_accept(self) -> None:
        if not self.name.text().strip():
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "Thiếu thông tin", "Vui lòng nhập tên project.")
            return
        if not self.location.text().strip():
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "Thiếu nơi lưu", "Vui lòng chọn nơi lưu project.")
            return
        if not Path(self.input_folder.text().strip()).is_dir():
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "Thiếu input", "Vui lòng chọn thư mục input hợp lệ.")
            return
        if not self._selected_videos():
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "Chưa chọn video", "Vui lòng chọn ít nhất một video.")
            return
        if self.source_language.currentText() == self.target_language.currentText():
            self.navigation.setCurrentRow(0)
            QMessageBox.warning(self, "Ngôn ngữ không hợp lệ", "Ngôn ngữ nguồn và đích cần khác nhau.")
            return
        translation = self._translation_values()
        try:
            load_prompt_template(
                str(translation.get("provider", GEMINI_PROVIDER_NAME)),
                str(translation.get("prompt_template", DEFAULT_PROMPT_TEMPLATE_ID)),
            )
        except (OSError, ValueError) as exc:
            self.navigation.setCurrentRow(3)
            QMessageBox.warning(self, "Prompt không hợp lệ", str(exc))
            return
        tts = self.tts.values()
        if tts.get("provider") == PIPER_PROVIDER and not tts.get("model_path"):
            self.navigation.setCurrentRow(4)
            QMessageBox.warning(self, "Thiếu model Piper", "Vui lòng chọn voice model Piper (.onnx).")
            return
        if tts.get("reference_voice") and not tts.get("voice_consent"):
            self.navigation.setCurrentRow(4)
            QMessageBox.warning(self, "Chưa xác nhận quyền giọng", "Hãy xác nhận quyền sử dụng giọng tham chiếu.")
            return
        self.accept()
