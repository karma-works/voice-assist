import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google.cloud import firestore

from src.config import GCP_PROJECT_ID

logger = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{5,}\d)(?!\w)")
MAX_METADATA_DEPTH = 4
MAX_STRING_LENGTH = 500

_db: firestore.AsyncClient | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def monotonic_ms() -> int:
    return int(time.perf_counter() * 1000)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def redact_value(value: Any, depth: int = 0) -> Any:
    if depth > MAX_METADATA_DEPTH:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        redacted = PHONE_RE.sub("[redacted_phone]", value)
        if len(redacted) > MAX_STRING_LENGTH:
            return redacted[:MAX_STRING_LENGTH] + "...[truncated]"
        return redacted
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            key_str = str(key)
            lowered = key_str.lower()
            if lowered in {"invite", "invite_id", "invite_token", "token", "authorization"}:
                clean[key_str] = "[redacted]"
            elif "phone" in lowered and item:
                clean[key_str] = True if isinstance(item, bool) else "[redacted_phone]"
            else:
                clean[key_str] = redact_value(item, depth + 1)
        return clean
    if isinstance(value, (list, tuple)):
        return [redact_value(item, depth + 1) for item in value[:20]]
    return redact_value(str(value), depth + 1)


def get_db() -> firestore.AsyncClient:
    global _db
    if _db is None:
        _db = firestore.AsyncClient(project=GCP_PROJECT_ID)
    return _db


@dataclass
class TraceLogger:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invite_hash: Optional[str] = None
    started_at: datetime = field(default_factory=utc_now)
    started_monotonic_ms: int = field(default_factory=monotonic_ms)
    firestore_enabled: bool = field(default_factory=lambda: os.environ.get("TRACE_FIRESTORE", "1") != "0")
    local_dir: Path = field(default_factory=lambda: Path(os.environ.get("TRACE_LOCAL_DIR", ".traces")))
    _event_count: int = 0
    _turn_count: int = 0
    _tool_call_count: int = 0
    _first_audio_ms: Optional[int] = None
    _booking_verified: bool = False
    _firestore_failed: bool = False

    @classmethod
    def for_invite(cls, invite: str) -> "TraceLogger":
        return cls(invite_hash=hash_token(invite))

    def elapsed_ms(self) -> int:
        return monotonic_ms() - self.started_monotonic_ms

    async def start_session(self, metadata: Optional[dict[str, Any]] = None) -> None:
        payload = {
            "session_id": self.session_id,
            "invite_hash": self.invite_hash,
            "started_at": self.started_at,
            "status": "active",
            "summary": {
                "turn_count": 0,
                "tool_call_count": 0,
                "booking_verified": False,
                "first_audio_ms": None,
            },
            "metadata": redact_value(metadata or {}),
        }
        await self._write_session(payload, merge=False)
        await self.event("session_start", metadata or {})

    async def end_session(self, status: str = "completed", metadata: Optional[dict[str, Any]] = None) -> None:
        payload = {
            "ended_at": utc_now(),
            "status": status,
            "summary": {
                "turn_count": self._turn_count,
                "tool_call_count": self._tool_call_count,
                "booking_verified": self._booking_verified,
                "first_audio_ms": self._first_audio_ms,
                "event_count": self._event_count,
            },
            "end_metadata": redact_value(metadata or {}),
        }
        await self._write_session(payload, merge=True)
        await self.event("session_end", {"status": status, **(metadata or {})})

    async def event(
        self,
        event_type: str,
        metadata: Optional[dict[str, Any]] = None,
        *,
        turn_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        self._event_count += 1
        if event_type == "assistant_first_audio" and self._first_audio_ms is None:
            self._first_audio_ms = self.elapsed_ms()
        if event_type == "calendar_verified":
            self._booking_verified = True

        payload = {
            "ts": utc_now(),
            "elapsed_ms": self.elapsed_ms(),
            "type": event_type,
            "turn_id": turn_id,
            "duration_ms": duration_ms,
            "metadata": redact_value(metadata or {}),
        }
        await self._write_event(payload)

    async def start_turn(self, metadata: Optional[dict[str, Any]] = None) -> str:
        self._turn_count += 1
        turn_id = f"turn-{self._turn_count:04d}"
        payload = {
            "turn_id": turn_id,
            "started_at": utc_now(),
            "started_elapsed_ms": self.elapsed_ms(),
            "metadata": redact_value(metadata or {}),
        }
        await self._write_turn(turn_id, payload, merge=False)
        await self.event("turn_start", metadata or {}, turn_id=turn_id)
        return turn_id

    async def update_turn(self, turn_id: str, payload: dict[str, Any]) -> None:
        await self._write_turn(turn_id, redact_value(payload), merge=True)

    async def trace_tool(self, name: str, args: dict[str, Any], call):
        self._tool_call_count += 1
        start = monotonic_ms()
        await self.event("tool_start", {"name": name, "args": args})
        try:
            result = await call()
            duration = monotonic_ms() - start
            await self.event(
                "tool_end",
                {
                    "name": name,
                    "success": result.get("success") if isinstance(result, dict) else None,
                    "result": result,
                },
                duration_ms=duration,
            )
            if name == "book_meeting" and isinstance(result, dict) and result.get("success"):
                await self.event("calendar_verified", {"event_id": result.get("event_id")})
            return result
        except Exception as exc:
            duration = monotonic_ms() - start
            await self.event("tool_error", {"name": name, "error": str(exc)}, duration_ms=duration)
            raise

    async def _write_session(self, payload: dict[str, Any], *, merge: bool) -> None:
        await self._write_firestore_doc(("voice_sessions", self.session_id), payload, merge=merge)
        await self._write_local({"kind": "session", **self._jsonable(payload)})

    async def _write_event(self, payload: dict[str, Any]) -> None:
        event_id = f"{self._event_count:06d}-{uuid.uuid4().hex[:8]}"
        await self._write_firestore_doc(
            ("voice_sessions", self.session_id, "events", event_id),
            payload,
            merge=False,
        )
        await self._write_local({"kind": "event", **self._jsonable(payload)})

    async def _write_turn(self, turn_id: str, payload: dict[str, Any], *, merge: bool) -> None:
        await self._write_firestore_doc(
            ("voice_sessions", self.session_id, "turns", turn_id),
            payload,
            merge=merge,
        )
        await self._write_local({"kind": "turn", **self._jsonable(payload)})

    async def _write_firestore_doc(self, path_parts: tuple[str, ...], payload: dict[str, Any], *, merge: bool) -> None:
        if not self.firestore_enabled or self._firestore_failed or not GCP_PROJECT_ID:
            return
        try:
            ref = get_db().collection(path_parts[0]).document(path_parts[1])
            idx = 2
            while idx < len(path_parts):
                ref = ref.collection(path_parts[idx]).document(path_parts[idx + 1])
                idx += 2
            await ref.set(payload, merge=merge)
        except Exception as exc:
            self._firestore_failed = True
            logger.warning("Trace Firestore writes disabled after failure: %s", exc)

    async def _write_local(self, payload: dict[str, Any]) -> None:
        if os.environ.get("TRACE_LOCAL", "1") == "0":
            return

        def _append() -> None:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            path = self.local_dir / f"{self.session_id}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

        await asyncio.get_event_loop().run_in_executor(None, _append)

    def _jsonable(self, payload: dict[str, Any]) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {str(k): convert(v) for k, v in value.items()}
            if isinstance(value, list):
                return [convert(v) for v in value]
            return value

        return convert(payload)
