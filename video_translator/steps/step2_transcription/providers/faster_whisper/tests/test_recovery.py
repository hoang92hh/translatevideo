import unittest
import importlib.util
from pathlib import Path

_WORKER_PATH = Path(__file__).resolve().parents[1] / "worker.py"
_SPEC = importlib.util.spec_from_file_location("faster_whisper_worker_under_test", _WORKER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_WORKER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_WORKER)
_is_duplicate = _WORKER._is_duplicate


class RecoveryDuplicateTests(unittest.TestCase):
    def test_recovery_does_not_duplicate_short_overlapping_asr_segment(self) -> None:
        main = {
            "start": 101.70,
            "end": 102.10,
            "text": "哥",
        }
        recovery = {
            "start": 101.46,
            "end": 101.72,
            "text": "哥",
        }
        self.assertTrue(_is_duplicate(recovery, [main]))

    def test_repeated_word_with_a_pause_is_preserved(self) -> None:
        first = {"start": 10.0, "end": 10.4, "text": "哥"}
        second = {"start": 10.8, "end": 11.2, "text": "哥"}
        self.assertFalse(_is_duplicate(second, [first]))

    def test_recovery_text_with_small_boundary_gap_is_duplicate(self) -> None:
        main = {"start": 101.70, "end": 102.10, "text": "哥"}
        recovery = {"start": 101.46, "end": 101.55, "text": "哥"}
        self.assertTrue(_is_duplicate(recovery, [main]))

    def test_different_text_at_a_boundary_is_not_duplicate(self) -> None:
        main = {"start": 20.0, "end": 20.8, "text": "你好"}
        recovery = {"start": 20.7, "end": 21.0, "text": "谢谢"}
        self.assertFalse(_is_duplicate(recovery, [main]))
