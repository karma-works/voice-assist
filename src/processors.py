import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


PHONE_DIGITS_RE = re.compile(r"^\+?[0-9][0-9\s().-]{5,}$")
MARKDOWN_REPLACEMENTS = (
    (re.compile(r"[*_`#]+"), ""),
    (re.compile(r"\[(.*?)\]\((.*?)\)"), r"\1"),
    (re.compile(r"^\s*[-+]\s+", re.MULTILINE), ""),
)


@dataclass
class AudioInputMetrics:
    frames: int = 0
    bytes_received: int = 0
    dropped_frames: int = 0
    last_rms: float = 0.0
    expected_sample_rate: int = 16000


@dataclass
class PhoneState:
    visitor_phone: Optional[str] = None
    visitor_phone_confirmed: bool = False
    phone_collection_declined: bool = False


@dataclass
class SessionState:
    invite_id: Optional[str] = None
    language: Optional[str] = None
    visitor_name: Optional[str] = None
    phone: PhoneState = field(default_factory=PhoneState)
    meeting_type: Optional[str] = None
    topic: Optional[str] = None
    selected_slot: Optional[dict] = None
    confirmation_status: Optional[str] = None
    spoken_assistant_text: list[str] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)


class AudioInputProcessor:
    """Tracks basic input audio metrics before frames are sent to Gemini/Pipecat."""

    def __init__(self, expected_sample_rate: int = 16000) -> None:
        self.metrics = AudioInputMetrics(expected_sample_rate=expected_sample_rate)

    def observe_pcm16(self, payload: bytes, rms: float = 0.0) -> bool:
        self.metrics.frames += 1
        self.metrics.bytes_received += len(payload)
        self.metrics.last_rms = rms
        if len(payload) % 2:
            self.metrics.dropped_frames += 1
            return False
        return True


class InterruptionProcessor:
    # Backchannels are acknowledgements that should NOT take the floor from the
    # assistant. The client's two-stage VAD gate already suppresses brief bursts
    # by duration; this lexicon is the semantic layer, applied when a short
    # transcript hint is available (see BrowserWebSocketSerializer.transcript_hint).
    BACKCHANNELS = {
        # English
        "uh-huh", "uh huh", "mhm", "mm-hmm", "okay", "ok", "yes", "yeah",
        "right", "sure", "i see",
        # German affirmations / acknowledgements
        "ja", "jaja", "jo", "joa", "jep", "genau", "aha", "ah", "hm", "hmm",
        "mhmm", "klar", "na klar", "alles klar", "gut", "okay gut", "verstehe",
        "stimmt", "richtig", "eben", "ach so", "achso",
    }
    INTERRUPT_WORDS = {
        "stop", "stopp", "halt", "warte", "wait", "moment", "nein", "no",
        "cancel", "abbrechen", "quatsch",
    }

    def classify_text(self, text: str) -> str:
        normalized = text.strip().lower().strip(".!?")
        if normalized in self.INTERRUPT_WORDS:
            return "interruption"
        if normalized in self.BACKCHANNELS:
            return "backchannel"
        return "speech"


class TextOutputProcessor:
    """Keeps assistant text suitable for speech and avoids private-calendar wording."""

    BLOCKED_CALENDAR_DETAIL_RE = re.compile(
        r"\b(title|description|participant|attendee|guest|agenda|meeting details?)\b",
        re.IGNORECASE,
    )

    def normalize(self, text: str) -> str:
        cleaned = text
        for pattern, replacement in MARKDOWN_REPLACEMENTS:
            cleaned = pattern.sub(replacement, cleaned)
        cleaned = self.BLOCKED_CALENDAR_DETAIL_RE.sub("calendar detail", cleaned)
        return " ".join(cleaned.split())

    def format_phone_readback(self, phone: str) -> str:
        digits = re.sub(r"\D", "", phone)
        prefix = "+" if phone.strip().startswith("+") else ""
        if not digits:
            return phone
        if len(digits) == 10 and not prefix:
            chunks = [digits[:3], digits[3:7], digits[7:]]
        else:
            chunks = []
            cursor = 0
            if prefix and len(digits) > 10:
                country_len = min(3, max(1, len(digits) - 10))
                chunks.append(prefix + digits[:country_len])
                cursor = country_len
            while cursor < len(digits):
                remaining = len(digits) - cursor
                chunk_len = 3 if remaining <= 6 else 4
                chunks.append(digits[cursor:cursor + chunk_len])
                cursor += chunk_len
        spoken_chunks = []
        for chunk in chunks:
            if chunk.startswith("+"):
                spoken_chunks.append("plus " + " ".join(chunk[1:]))
            else:
                spoken_chunks.append(" ".join(chunk))
        return ", ".join(spoken_chunks)


class StateProcessor:
    def __init__(self, state: SessionState) -> None:
        self.state = state

    def record_assistant_playback(self, text: str) -> None:
        if text:
            self.state.spoken_assistant_text.append(text)
            self.state.touch()

    def rewind_unplayed_assistant_tail(self) -> None:
        if self.state.spoken_assistant_text:
            self.state.spoken_assistant_text.pop()
            self.state.touch()
