from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import uuid4

from ..errors import UserFacingError


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765


@dataclass
class _JobRecord:
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    extension_session_id: str = ""
    completed: threading.Event = field(default_factory=threading.Event)


class WebBridgeService:
    """Thread-safe localhost transport between pipeline workers and the Chrome extension."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: deque[str] = deque()
        self._jobs: dict[str, _JobRecord] = {}
        self._last_seen = 0.0
        self._model_states: dict[str, dict[str, str]] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._start_error = ""

    def start(self) -> None:
        if self._server or self._start_error:
            return
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _send(self, status: int, payload: dict[str, Any] | None = None) -> None:
                body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as exc:
                    raise ValueError("Content-Length không hợp lệ.") from exc
                if length <= 0 or length > 10 * 1024 * 1024:
                    raise ValueError("Request body trống hoặc quá lớn.")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("JSON body phải là object.")
                return value

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/api/jobs/next":
                    session_id = self.headers.get("X-MyTool-Extension-Session", "").strip()
                    if not session_id:
                        self._send(HTTPStatus.FORBIDDEN, {"error": "EXTENSION_SESSION_REQUIRED"})
                        return
                    job = service.next_job(session_id, timeout=20.0)
                    self._send(HTTPStatus.OK, job) if job else self._send(HTTPStatus.NO_CONTENT)
                    return
                if self.path == "/api/connection/status":
                    self._send(HTTPStatus.OK, service.status())
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

            def do_POST(self) -> None:  # noqa: N802
                try:
                    payload = self._read_json()
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "INVALID_REQUEST", "message": str(exc)})
                    return
                if self.path == "/api/connection/model":
                    provider = str(payload.get("provider") or "chatgpt")
                    session_id = self.headers.get("X-MyTool-Extension-Session", "").strip()
                    if not session_id:
                        self._send(HTTPStatus.FORBIDDEN, {"error": "EXTENSION_SESSION_REQUIRED"})
                        return
                    service.record_model_state(provider, payload)
                    self._send(HTTPStatus.OK, {"saved": True})
                    return
                prefix, suffix = "/api/jobs/", "/result"
                if self.path.startswith(prefix) and self.path.endswith(suffix):
                    job_id = self.path[len(prefix) : -len(suffix)]
                    session_id = self.headers.get("X-MyTool-Extension-Session", "").strip()
                    if not job_id or str(payload.get("job_id", "")) != job_id:
                        self._send(HTTPStatus.BAD_REQUEST, {"error": "JOB_ID_MISMATCH"})
                        return
                    record = service.complete_job(job_id, payload, session_id)
                    if record is None:
                        self._send(HTTPStatus.NOT_FOUND, {"error": "JOB_NOT_FOUND"})
                    else:
                        self._send(HTTPStatus.OK, record)
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "NOT_FOUND"})

        try:
            self._server = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), Handler)
        except OSError as exc:
            self._start_error = str(exc)
            return
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="TransLanguageWebBridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def status(self) -> dict[str, Any]:
        age = time.monotonic() - self._last_seen if self._last_seen else None
        return {
            "running": self._server is not None,
            "connected": age is not None and age < 45.0,
            "last_seen_seconds": round(age, 1) if age is not None else None,
            "error": self._start_error,
            "models": dict(self._model_states),
        }

    def next_job(self, extension_session_id: str, timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            self._last_seen = time.monotonic()
            while not self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
                self._last_seen = time.monotonic()
            job_id = self._pending.popleft()
            record = self._jobs.get(job_id)
            if record:
                record.extension_session_id = extension_session_id
            return dict(record.payload) if record else None

    def record_model_state(self, provider: str, payload: dict[str, Any]) -> None:
        state = {
            key: str(payload[key]).strip()
            for key in ("model", "effort")
            if payload.get(key) is not None and str(payload[key]).strip()
        }
        with self._condition:
            self._last_seen = time.monotonic()
            self._model_states[provider] = state

    def complete_job(
        self,
        job_id: str,
        result: dict[str, Any],
        extension_session_id: str,
    ) -> dict[str, Any] | None:
        with self._condition:
            self._last_seen = time.monotonic()
            record = self._jobs.get(job_id)
            if record is None or not extension_session_id or record.extension_session_id != extension_session_id:
                return None
            if record.result is None:
                record.result = dict(result)
                record.completed.set()
            return dict(record.result)

    def submit_prompt(
        self,
        provider: str,
        content: str,
        *,
        model: str = "",
        project_url: str = "",
        conversation_mode: str = "start",
        timeout: float = 240.0,
    ) -> str:
        self.start()
        if not self._server:
            raise UserFacingError(
                "Không thể khởi động Web Bridge",
                f"Ứng dụng không thể mở cổng {BRIDGE_HOST}:{BRIDGE_PORT}.",
                "Đóng ứng dụng đang dùng cổng 8765 rồi mở lại TransLanguage.",
                self._start_error,
            )
        status = self.status()
        if not status["connected"]:
            raise UserFacingError(
                "Chrome extension chưa kết nối",
                "TransLanguage chưa nhận được kết nối từ MyTool Bridge Extension.",
                "Nạp extension, mở một tab provider đã đăng nhập và chờ vài giây rồi chạy lại.",
            )
        job_id = str(uuid4())
        payload = {
            "job_id": job_id,
            "provider": provider,
            "action": "prompt",
            "content": content,
            "model": "" if model.startswith("Theo model") else model,
            "project_url": project_url,
            "conversation_mode": conversation_mode,
            "options": {},
        }
        record = _JobRecord(payload)
        with self._condition:
            self._jobs[job_id] = record
            self._pending.append(job_id)
            self._condition.notify_all()
        if not record.completed.wait(timeout):
            raise UserFacingError(
                "Web provider phản hồi quá lâu",
                f"Không nhận được kết quả cho job {job_id} trước thời hạn.",
                "Kiểm tra tab trình duyệt và console của extension rồi thử lại; prompt sẽ không tự gửi lại.",
            )
        result = record.result or {}
        with self._condition:
            self._jobs.pop(job_id, None)
        if result.get("status") != "completed":
            raise UserFacingError(
                "Web provider không thể hoàn thành",
                str(result.get("message") or result.get("error") or "Extension trả về lỗi không xác định."),
                "Kiểm tra đăng nhập, đóng bớt tab cùng provider và để ô nhập trống trước khi chạy lại.",
                json.dumps(result, ensure_ascii=False),
            )
        text = str(result.get("text", "")).strip()
        if not text:
            raise UserFacingError(
                "Web provider trả về nội dung trống",
                "Extension đã hoàn tất nhưng không đọc được nội dung trả lời.",
                "Kiểm tra giao diện provider có thay đổi rồi cập nhật selector của extension.",
            )
        return text


web_bridge_service = WebBridgeService()
