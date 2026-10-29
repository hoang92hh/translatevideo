from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable


class _ModelWorker:
    def __init__(self, module: str) -> None:
        self.module = module
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.stderr: list[str] = []

    def execute(
        self, request: dict[str, object], progress: Callable[[int, str], None] | None,
    ) -> tuple[dict[str, object] | None, dict[str, object] | None, str, int]:
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                environment = os.environ.copy()
                environment["PYTHONIOENCODING"] = "utf-8"
                self.process = subprocess.Popen(
                    [sys.executable, "-m", self.module],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self.stderr = []
                assert self.process.stderr is not None

                stderr_pipe = self.process.stderr
                stderr_chunks = self.stderr

                def drain() -> None:
                    for line in stderr_pipe:
                        stderr_chunks.append(line)

                threading.Thread(target=drain, daemon=True).start()
            process = self.process
            assert process.stdin is not None and process.stdout is not None
            stderr_start = len(self.stderr)
            try:
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
            except OSError:
                self._close_locked()
                raise
            result = error = None
            for raw_line in process.stdout:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "progress" and progress:
                    progress(int(event.get("value", 0)), str(event.get("message", "")))
                elif kind == "result":
                    result = event
                    break
                elif kind == "error":
                    error = event
                    break
            code = process.poll()
            return result, error, "".join(self.stderr[stderr_start:]).strip(), code or 0

    def _close_locked(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            assert process.stdin is not None
            process.stdin.write('{"type":"shutdown"}\n')
            process.stdin.flush()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

    def close(self) -> None:
        with self.lock:
            self._close_locked()


_WORKERS: dict[tuple[str, str], _ModelWorker] = {}
_LOCK = threading.Lock()
_MODULES = {
    "stt": "video_translator.steps.step2_transcription.providers.faster_whisper.worker",
    "diarization": "video_translator.steps.step2_transcription.providers.faster_whisper.diarization_worker",
}


def run_batch_worker(
    session_key: str, kind: str, request: dict[str, object],
    progress: Callable[[int, str], None] | None = None,
) -> tuple[dict[str, object] | None, dict[str, object] | None, str, int]:
    key = (session_key, kind)
    with _LOCK:
        worker = _WORKERS.get(key)
        if worker is None:
            worker = _ModelWorker(_MODULES[kind])
            _WORKERS[key] = worker
    return worker.execute(request, progress)


def release_batch_session(session_key: str) -> None:
    with _LOCK:
        workers = [_WORKERS.pop(key) for key in list(_WORKERS) if key[0] == session_key]
    for worker in workers:
        worker.close()
