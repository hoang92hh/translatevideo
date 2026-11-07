import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from video_translator.steps.step2_transcription.service import SpeechToTextService


_WORKER_PATH = Path(__file__).resolve().parents[1] / "worker.py"
_SPEC = importlib.util.spec_from_file_location("worker_timing_under_test", _WORKER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
worker = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(worker)


def word(start, end, text, probability=0.9):
    return {"start": start, "end": end, "word": text, "probability": probability}


class FakeRecoveryModel:
    def __init__(self, segments):
        self.segments = segments
        self.calls = 0

    def transcribe(self, *_args, **_kwargs):
        self.calls += 1
        return iter(self.segments), None


def recognized(start, end, text, words):
    return SimpleNamespace(
        start=start, end=end, text=text,
        words=[SimpleNamespace(**item) for item in words],
        avg_logprob=-0.1, no_speech_prob=0.01, compression_ratio=1.0,
    )


class MainTimingTests(unittest.TestCase):
    def test_long_main_end_is_shortened_only_with_words_and_vad(self):
        main = [{"start": 0.0, "end": 30.0, "text": "hello", "words": [word(1.0, 1.5, "hello")]}]
        speech = [{"start": 800, "end": 1800}, {"start": 10000, "end": 11000}]
        audit, uncertain = worker._inspect_main_timing(main, speech, 1000)
        self.assertEqual(main[0]["end"], 1.65)
        self.assertEqual(audit[0]["before_end"], 30.0)
        self.assertEqual(audit[0]["action"], "corrected")
        self.assertFalse(uncertain)

    def test_missing_or_unreliable_words_preserve_main_and_warn(self):
        for words in ([], [word(1.0, 1.5, "different", 0.1)], [{"start": "bad", "end": 1.5}]):
            with self.subTest(words=words):
                main = [{"start": 0.0, "end": 30.0, "text": "hello", "words": words}]
                audit, uncertain = worker._inspect_main_timing(main, [{"start": 800, "end": 1800}], 1000)
                self.assertEqual(main[0]["end"], 30.0)
                self.assertEqual(audit[0]["action"], "unverified")
                self.assertEqual(uncertain, {0})

    def test_no_cut_to_next_segment_start(self):
        main = [
            {"start": 0.0, "end": 30.0, "text": "hello", "words": []},
            {"start": 10.0, "end": 11.0, "text": "world", "words": [word(10.0, 11.0, "world")]},
        ]
        worker._inspect_main_timing(main, [{"start": 800, "end": 1800}, {"start": 10000, "end": 11000}], 1000)
        self.assertEqual(main[0]["end"], 30.0)

    def test_partially_corrected_main_remains_suspicious_for_recovery(self):
        main = [{"start": 0.0, "end": 30.0, "text": "hello", "words": [word(3.0, 4.0, "hello")]}]
        audit, uncertain = worker._inspect_main_timing(
            main, [{"start": 2800, "end": 9000}], 1000,
        )
        self.assertEqual(main[0]["start"], 2.85)
        self.assertEqual(main[0]["end"], 30.0)
        self.assertEqual(audit[0]["action"], "partially_corrected_unverified")
        self.assertEqual(uncertain, {0})


class RecoveryTests(unittest.TestCase):
    def test_recovery_new_phrase_survives_bad_main_timing_overlap(self):
        main = [{"start": 0.0, "end": 30.0, "text": "hello", "words": []}]
        model = FakeRecoveryModel([recognized(10.0, 11.0, "world", [word(10.0, 11.0, "world")])])
        audio = np.full(30_000, 0.1, dtype=np.float32)
        recovered, metadata = worker._recover_missing_segments(
            model, audio, 1000, "en", 30.0, main,
            [{"start": 0, "end": 30000}], uncertain_main_indices={0},
        )
        self.assertEqual(model.calls, 1)
        self.assertEqual([item["text"] for item in recovered], ["world"])
        self.assertEqual(metadata["accepted_segments"][0]["reason"], "overlap_with_unverified_main_timing")

    def test_same_recognition_is_duplicate(self):
        main = {"start": 10.0, "end": 11.0, "text": "hello", "words": [word(10.0, 11.0, "hello")]}
        recovery = {"start": 10.1, "end": 10.9, "text": "hello", "words": [word(10.1, 10.9, "hello")]}
        self.assertTrue(worker._is_duplicate(recovery, [main]))

    def test_same_words_spoken_later_are_not_duplicate(self):
        main = {"start": 10.0, "end": 11.0, "text": "hello", "words": [word(10.0, 11.0, "hello")]}
        recovery = {"start": 13.0, "end": 14.0, "text": "hello", "words": [word(13.0, 14.0, "hello")]}
        self.assertFalse(worker._is_duplicate(recovery, [main]))

    def test_two_speakers_overlap_without_merging(self):
        raw = [
            {"start": 10.0, "end": 11.0, "text": "hello", "words": [word(10.0, 11.0, "hello")]},
            {"start": 10.5, "end": 11.5, "text": "world", "words": [word(10.5, 11.5, "world")]},
        ]
        turns = [
            {"start": 10.0, "end": 10.5, "speaker_id": "SPEAKER_00"},
            {"start": 10.5, "end": 11.5, "speaker_id": "SPEAKER_01"},
        ]
        result = SpeechToTextService._segments_with_speakers(raw, turns, merge_adjacent_segments=True)
        self.assertEqual(len(result), 2)
        self.assertEqual([item.text for item in result], ["hello", "world"])

    def test_distinct_recovery_crossing_main_boundary_keeps_full_word_time(self):
        main = [{"start": 10.0, "end": 11.0, "text": "hello", "words": [word(10.0, 11.0, "hello")]}]
        model = FakeRecoveryModel([
            recognized(10.5, 12.5, "world", [word(10.5, 12.5, "world")]),
        ])
        audio = np.full(14_000, 0.1, dtype=np.float32)
        recovered, metadata = worker._recover_missing_segments(
            model, audio, 1000, "en", 14.0, main,
            [{"start": 10000, "end": 13000}],
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual((recovered[0]["start"], recovered[0]["end"]), (10.5, 12.5))
        self.assertTrue(metadata["accepted_segments"][0]["preserved_across_recovery_boundary"])


class ServiceIntegrationTests(unittest.TestCase):
    def test_recovery_kept_with_and_without_diarization(self):
        worker_result = {
            "segments": [{"start": 0.0, "end": 30.0, "text": "hello", "words": []}],
            "recovered_segments": [{
                "start": 10.0, "end": 11.0, "before_start": 9.8, "before_end": 11.2,
                "alignment_start": 10.0, "alignment_end": 11.0,
                "text": "world", "words": [word(10.0, 11.0, "world")],
            }],
            "asr_recovery": {"main_timing_audit": [{
                "before_start": 0.0, "before_end": 30.0,
                "after_start": 0.0, "after_end": 30.0,
                "action": "unverified", "reason": "missing_or_unreliable_word_timestamps",
            }]},
            "language": "en", "duration_seconds": 30.0,
        }
        for diarization in (False, True):
            with self.subTest(diarization=diarization), TemporaryDirectory() as folder:
                source = Path(folder) / "audio.wav"
                source.write_bytes(b"wav")
                service = SpeechToTextService("medium", "CPU", "en", True, diarization)
                turns = [{"start": 0.0, "end": 9.0, "speaker_id": "SPEAKER_00"},
                         {"start": 9.0, "end": 30.0, "speaker_id": "SPEAKER_01"}]
                with patch.object(service, "_run_worker", return_value=worker_result), \
                     patch.object(service, "model_cache_dir", return_value=Path(folder)), \
                     patch("video_translator.steps.step2_transcription.service.configured_model_path", return_value=Path(folder)), \
                     patch("video_translator.steps.step2_transcription.service.missing_model_files", return_value=[]), \
                     patch.object(service, "_run_diarization_worker", return_value={"turns": turns, "model": "fake", "actual_device": "CPU"}):
                    result = service.transcribe(source)
                self.assertEqual([item.text for item in result.segments], ["hello", "world"])
                self.assertEqual(result.asr_recovery["final_accepted_segment_count"], 1)
                self.assertEqual(result.asr_recovery["final_segment_sources"][1]["source"], "recovery")
                self.assertTrue(result.asr_recovery["postprocess_overlap_warnings"])
