import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional


class BookingStateName(StrEnum):
    IDLE = "idle"
    COLLECTING_REQUIREMENTS = "collecting_requirements"
    CHECKING_AVAILABILITY = "checking_availability"
    PRESENTING_OPTIONS = "presenting_options"
    AWAITING_SLOT_SELECTION = "awaiting_slot_selection"
    AWAITING_REQUIRED_DETAILS = "awaiting_required_details"
    AWAITING_EXPLICIT_CONFIRMATION = "awaiting_explicit_confirmation"
    BOOKING_IN_PROGRESS = "booking_in_progress"
    BOOKED = "booked"
    FAILED_RECOVERABLE = "failed_recoverable"
    CANCELLED = "cancelled"


class BookingEventName(StrEnum):
    USER_REQUESTED_BOOKING = "user_requested_booking"
    REQUIREMENTS_UPDATED = "requirements_updated"
    AVAILABILITY_CHECKED = "availability_checked"
    SLOT_SELECTED = "slot_selected"
    DETAILS_COMPLETED = "details_completed"
    CONFIRMATION_RECEIVED = "confirmation_received"
    CONFIRMATION_REJECTED = "confirmation_rejected"
    CALENDAR_WRITE_STARTED = "calendar_write_started"
    CALENDAR_WRITE_VERIFIED = "calendar_write_verified"
    CALENDAR_WRITE_FAILED = "calendar_write_failed"
    USER_INTERRUPTED = "user_interrupted"
    SESSION_RECONNECTED = "session_reconnected"


@dataclass
class BookingTransition:
    event: str
    from_state: str
    to_state: str
    metadata: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PreparedBooking:
    operation_id: str
    idempotency_key: str
    facts: dict[str, Any]
    calendar_result: Optional[dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class BookingSession:
    state: BookingStateName = BookingStateName.IDLE
    candidate_facts: dict[str, Any] = field(default_factory=dict)
    confirmed_facts: dict[str, Any] = field(default_factory=dict)
    available_slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    prepared: dict[str, PreparedBooking] = field(default_factory=dict)
    active_operation_id: Optional[str] = None
    verified_event_id: Optional[str] = None
    transitions: list[BookingTransition] = field(default_factory=list)

    def transition(
        self,
        event: BookingEventName,
        to_state: BookingStateName,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BookingTransition:
        transition = BookingTransition(
            event=event.value,
            from_state=self.state.value,
            to_state=to_state.value,
            metadata=metadata or {},
        )
        self.state = to_state
        self.transitions.append(transition)
        return transition

    def record_availability(
        self,
        slots: list[dict[str, Any]],
        *,
        slot_type: str,
        duration_minutes: int,
    ) -> tuple[list[dict[str, Any]], list[BookingTransition]]:
        transitions = [
            self.transition(
                BookingEventName.USER_REQUESTED_BOOKING,
                BookingStateName.COLLECTING_REQUIREMENTS,
                {"slot_type": slot_type, "duration_minutes": duration_minutes},
            ),
            self.transition(
                BookingEventName.AVAILABILITY_CHECKED,
                BookingStateName.PRESENTING_OPTIONS,
                {"slot_count": len(slots)},
            ),
        ]
        self.available_slots.clear()
        decorated = []
        for index, slot in enumerate(slots, start=1):
            slot_id = f"slot-{index}"
            slot_with_id = {**slot, "slot_id": slot_id}
            self.available_slots[slot_id] = slot_with_id
            decorated.append(slot_with_id)
        transitions.append(
            self.transition(
                BookingEventName.REQUIREMENTS_UPDATED,
                BookingStateName.AWAITING_SLOT_SELECTION,
                {"slot_ids": list(self.available_slots.keys())},
            )
        )
        return decorated, transitions

    def prepare_booking(self, args: dict[str, Any]) -> tuple[dict[str, Any], list[BookingTransition]]:
        transitions = []
        missing = [
            field
            for field in ("title", "start_iso", "end_iso", "visitor_name", "topic")
            if not args.get(field)
        ]
        if missing:
            transitions.append(
                self.transition(
                    BookingEventName.REQUIREMENTS_UPDATED,
                    BookingStateName.AWAITING_REQUIRED_DETAILS,
                    {"missing": missing},
                )
            )
            return {
                "success": False,
                "error": "Missing required booking details.",
                "missing": missing,
                "state": self.state.value,
            }, transitions

        selected_slot = self._resolve_slot(args)
        if selected_slot:
            args = {**args, "selected_slot_id": selected_slot["slot_id"]}
            transitions.append(
                self.transition(
                    BookingEventName.SLOT_SELECTED,
                    BookingStateName.AWAITING_REQUIRED_DETAILS,
                    {"slot_id": selected_slot["slot_id"]},
                )
            )

        phone_confirmed = bool(args.get("visitor_phone_confirmed"))
        phone_declined = bool(args.get("phone_collection_declined"))
        if args.get("visitor_phone") and not phone_confirmed:
            transitions.append(
                self.transition(
                    BookingEventName.REQUIREMENTS_UPDATED,
                    BookingStateName.AWAITING_REQUIRED_DETAILS,
                    {"missing": ["visitor_phone_confirmed"]},
                )
            )
            return {
                "success": False,
                "error": "Phone number was provided but not explicitly confirmed.",
                "missing": ["visitor_phone_confirmed"],
                "state": self.state.value,
            }, transitions
        if not args.get("visitor_phone") and not phone_declined:
            transitions.append(
                self.transition(
                    BookingEventName.REQUIREMENTS_UPDATED,
                    BookingStateName.AWAITING_REQUIRED_DETAILS,
                    {"missing": ["phone_collection_declined_or_confirmed_phone"]},
                )
            )
            return {
                "success": False,
                "error": "Ask whether the visitor wants to provide an optional phone number before booking.",
                "missing": ["phone_collection_declined_or_confirmed_phone"],
                "state": self.state.value,
            }, transitions

        facts = self._booking_facts(args)
        idempotency_key = self._idempotency_key(facts)
        existing = self._find_prepared_by_key(idempotency_key)
        if existing:
            self.active_operation_id = existing.operation_id
            prepared = existing
        else:
            operation_id = f"booking-{uuid.uuid4().hex[:12]}"
            prepared = PreparedBooking(
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                facts=facts,
            )
            self.prepared[operation_id] = prepared
            self.active_operation_id = operation_id

        self.candidate_facts = facts
        transitions.extend([
            self.transition(
                BookingEventName.DETAILS_COMPLETED,
                BookingStateName.AWAITING_EXPLICIT_CONFIRMATION,
                {"operation_id": prepared.operation_id},
            )
        ])
        return {
            "success": True,
            "state": self.state.value,
            "booking_operation_id": prepared.operation_id,
            "confirmation_required": True,
            "booking_summary": facts,
            "instructions": "Ask the visitor to explicitly confirm these details before calling book_meeting with this booking_operation_id.",
        }, transitions

    async def book_prepared(
        self,
        args: dict[str, Any],
        create_event,
    ) -> tuple[dict[str, Any], list[BookingTransition]]:
        transitions = []
        operation_id = args.get("booking_operation_id") or self.active_operation_id
        if not operation_id or operation_id not in self.prepared:
            transitions.append(
                self.transition(
                    BookingEventName.CALENDAR_WRITE_FAILED,
                    BookingStateName.FAILED_RECOVERABLE,
                    {"reason": "missing_prepared_booking"},
                )
            )
            return {
                "success": False,
                "error": "No prepared booking is awaiting confirmation. Call prepare_booking first.",
                "state": self.state.value,
            }, transitions

        if not args.get("explicit_confirmation"):
            transitions.append(
                self.transition(
                    BookingEventName.CONFIRMATION_REJECTED,
                    BookingStateName.AWAITING_EXPLICIT_CONFIRMATION,
                    {"operation_id": operation_id},
                )
            )
            return {
                "success": False,
                "error": "Explicit visitor confirmation is required before writing to the calendar.",
                "state": self.state.value,
                "booking_operation_id": operation_id,
            }, transitions

        prepared = self.prepared[operation_id]
        if prepared.calendar_result:
            self.confirmed_facts = prepared.facts
            self.verified_event_id = prepared.calendar_result.get("event_id")
            transitions.append(
                self.transition(
                    BookingEventName.CALENDAR_WRITE_VERIFIED,
                    BookingStateName.BOOKED,
                    {
                        "operation_id": operation_id,
                        "event_id": self.verified_event_id,
                        "idempotent_replay": True,
                    },
                )
            )
            return {
                **prepared.calendar_result,
                "idempotent_replay": True,
                "booking_operation_id": operation_id,
                "state": self.state.value,
            }, transitions

        transitions.extend([
            self.transition(
                BookingEventName.CONFIRMATION_RECEIVED,
                BookingStateName.BOOKING_IN_PROGRESS,
                {"operation_id": operation_id},
            ),
            self.transition(
                BookingEventName.CALENDAR_WRITE_STARTED,
                BookingStateName.BOOKING_IN_PROGRESS,
                {"operation_id": operation_id},
            ),
        ])

        try:
            result = await create_event(**prepared.facts)
        except Exception as exc:
            transitions.append(
                self.transition(
                    BookingEventName.CALENDAR_WRITE_FAILED,
                    BookingStateName.FAILED_RECOVERABLE,
                    {"operation_id": operation_id, "error": str(exc)},
                )
            )
            return {
                "success": False,
                "error": str(exc),
                "state": self.state.value,
                "booking_operation_id": operation_id,
            }, transitions

        if not result.get("success") or not result.get("event_id"):
            transitions.append(
                self.transition(
                    BookingEventName.CALENDAR_WRITE_FAILED,
                    BookingStateName.FAILED_RECOVERABLE,
                    {"operation_id": operation_id, "result": result},
                )
            )
            return {
                "success": False,
                "error": "Calendar write was not verified.",
                "calendar_result": result,
                "state": self.state.value,
                "booking_operation_id": operation_id,
            }, transitions

        prepared.calendar_result = result
        self.confirmed_facts = prepared.facts
        self.verified_event_id = result["event_id"]
        transitions.append(
            self.transition(
                BookingEventName.CALENDAR_WRITE_VERIFIED,
                BookingStateName.BOOKED,
                {"operation_id": operation_id, "event_id": result["event_id"]},
            )
        )
        return {
            **result,
            "booking_operation_id": operation_id,
            "state": self.state.value,
        }, transitions

    def _resolve_slot(self, args: dict[str, Any]) -> Optional[dict[str, Any]]:
        slot_id = args.get("selected_slot_id")
        if slot_id and slot_id in self.available_slots:
            return self.available_slots[slot_id]

        start_iso = args.get("start_iso")
        end_iso = args.get("end_iso")
        for slot in self.available_slots.values():
            if slot.get("start") == start_iso and slot.get("end") == end_iso:
                return slot
        return None

    def _find_prepared_by_key(self, idempotency_key: str) -> Optional[PreparedBooking]:
        for prepared in self.prepared.values():
            if prepared.idempotency_key == idempotency_key:
                return prepared
        return None

    def _booking_facts(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": args["title"],
            "start_iso": args["start_iso"],
            "end_iso": args["end_iso"],
            "visitor_name": args["visitor_name"],
            "topic": args["topic"],
            "visitor_phone": args.get("visitor_phone"),
            "visitor_phone_confirmed": bool(args.get("visitor_phone_confirmed")),
            "meeting_type": args.get("meeting_type"),
            "selected_slot_id": args.get("selected_slot_id"),
            "phone_collection_declined": bool(args.get("phone_collection_declined")),
        }

    def _idempotency_key(self, facts: dict[str, Any]) -> str:
        raw = "|".join(str(facts.get(key) or "") for key in (
            "start_iso",
            "end_iso",
            "visitor_name",
            "topic",
            "visitor_phone",
            "meeting_type",
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
