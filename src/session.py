import asyncio
import functools
import json
import traceback
from datetime import datetime

import pytz
import logging
logger = logging.getLogger(__name__)

from src.config import GCP_PROJECT_ID, GCP_LOCATION, GEMINI_MODEL, BUFFER_MINUTES
from src import calendar_service
from src.booking_state import BookingSession
from src.processors import (
    AudioInputProcessor,
    InterruptionProcessor,
    SessionState,
    StateProcessor,
    TextOutputProcessor,
)
from src.observability import TraceLogger

BERLIN_TZ = pytz.timezone("Europe/Berlin")

PIPECAT_TOOLS = [
    {
        "function_declarations": [
            {
                "name": "get_available_slots",
                "description": "Get available meeting time slots. Use this to find when a meeting can be scheduled.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days_ahead": {
                            "type": "integer",
                            "description": "How many days ahead to search. Default: 14",
                        },
                        "duration_minutes": {
                            "type": "integer",
                            "description": "Meeting duration in minutes. Default: 30",
                        },
                        "slot_type": {
                            "type": "string",
                            "description": "'business' or 'private'",
                        },
                    },
                    "required": ["slot_type"],
                },
            },
            {
                "name": "prepare_booking",
                "description": "Prepare a booking after the visitor chose a slot and provided required details. This does not write to the calendar. Use the returned booking_operation_id when asking for explicit confirmation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "start_iso": {"type": "string", "description": "Start datetime as local Europe/Berlin wall-clock ISO 8601 with Berlin offset. Never convert the user's spoken time to UTC."},
                        "end_iso": {"type": "string", "description": "End datetime as local Europe/Berlin wall-clock ISO 8601 with Berlin offset. Never convert the user's spoken time to UTC."},
                        "selected_slot_id": {"type": "string"},
                        "visitor_name": {"type": "string"},
                        "visitor_phone": {"type": "string"},
                        "visitor_phone_confirmed": {"type": "boolean"},
                        "phone_collection_declined": {"type": "boolean"},
                        "meeting_type": {"type": "string"},
                        "topic": {"type": "string"},
                    },
                    "required": ["title", "start_iso", "end_iso", "visitor_name", "topic"],
                },
            },
            {
                "name": "book_meeting",
                "description": "Write a previously prepared booking to the calendar. Only call after prepare_booking returned a booking_operation_id and the visitor explicitly confirmed all details.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "booking_operation_id": {"type": "string"},
                        "explicit_confirmation": {"type": "boolean"},
                    },
                    "required": ["booking_operation_id", "explicit_confirmation"],
                },
            },
            {
                "name": "find_meeting_at",
                "description": "Find an existing meeting at an approximate time. Use to identify a meeting before rescheduling.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "approx_datetime_iso": {"type": "string"},
                        "visitor_phone": {"type": "string"},
                    },
                    "required": ["approx_datetime_iso"],
                },
            },
            {
                "name": "reschedule_meeting",
                "description": "Move an existing meeting to a new time. Requires event_id from find_meeting_at. Only call after visitor confirms the new time.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "new_start_iso": {"type": "string", "description": "New start as local Europe/Berlin wall-clock ISO 8601. Never convert the user's spoken time to UTC."},
                        "new_end_iso": {"type": "string", "description": "New end as local Europe/Berlin wall-clock ISO 8601. Never convert the user's spoken time to UTC."},
                    },
                    "required": ["event_id", "new_start_iso", "new_end_iso"],
                },
            },
        ]
    }
]


def build_system_prompt() -> str:
    now = datetime.now(BERLIN_TZ)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    date_str = f"{weekdays[now.weekday()]}, {now.strftime('%B %d, %Y, %H:%M')} (Europe/Berlin)"

    return f"""You are a friendly scheduling assistant helping visitors book or reschedule meetings with Christian.

Today is: {date_str}

LANGUAGE RULES:
- Default language: German (Hochdeutsch). Greet in German: "Hallo! Ich helfe dir gerne, einen Termin mit Christian zu vereinbaren. Worum geht es bei dem Treffen?"
- If the visitor speaks English, switch to English immediately and stay in English.
- Swiss German is welcome — respond in standard German (Hochdeutsch) if they use Swiss German.
- Never mix languages within a single response.
- Keep responses short and natural — this is a voice conversation.
- Speak in short fragments, with contractions in English where natural.
- Output plain spoken text only: no markdown, bullet points, tables, URLs, code formatting, or emoji.
- Say numbers, dates, times, phone numbers, and symbols in a TTS-friendly way instead of relying on punctuation.

YOUR CAPABILITIES:
- Find available meeting slots
- Book new meetings
- Find and reschedule existing meetings

WHAT YOU CANNOT DO:
- You have no access to meeting titles, descriptions, or participants of existing calendar entries.
- You can only see whether time slots are free or busy.
- Never confirm, deny, or hint at what specific meetings exist.

MEETING TYPE RULES (enforced server-side):
- Business/professional context → slot_type "business": Monday–Friday, 07:00–15:00 Berlin time.
- Personal/private context → slot_type "private": any day, 00:00–22:00 Berlin time.
- When ambiguous, default to business.
- Assume visitors are in Europe/Berlin. Treat spoken appointment times as local Berlin wall-clock times; do not convert them to UTC for booking or rescheduling tool calls.

BOOKING FLOW:
1. Greet and ask what the meeting is about (to infer type).
2. Call get_available_slots, present 3 concrete options with day and time.
3. Visitor picks a slot → ask for full name.
4. Ask whether they want to provide a phone number. Make clear it is optional.
5. If they decline, continue booking without a phone number.
6. If they provide one, collect it in chunks. Preserve country codes and leading zeros. Re-ask only unclear chunks.
7. Read the phone number back in grouped chunks and require explicit confirmation before storing it.
8. Call prepare_booking with the chosen slot, name, optional phone status, meeting type, and topic.
9. Read back the prepared details and ask for an explicit yes/no confirmation.
10. Only after the visitor explicitly confirms, call book_meeting with the booking_operation_id from prepare_booking and explicit_confirmation true.
11. After booking: only say the meeting is booked if book_meeting returns success true and an event_id. If the tool returns an error or success false, say scheduling failed and ask whether to try again. Do not promise an automatic visitor calendar invite.

DETERMINISTIC BOOKING RULES:
- Never call book_meeting directly from extracted conversation details.
- Never invent a booking_operation_id. Use only the value returned by prepare_booking.
- If the visitor changes the date, time, name, phone, meeting type, or topic after prepare_booking, call prepare_booking again and ask for confirmation again.
- If book_meeting returns idempotent_replay true, say the meeting was already booked successfully; do not imply a second appointment was created.
- If a tool says confirmation is missing, ask for confirmation instead of claiming success.

RESCHEDULE FLOW:
1. Ask for approximate date/time of existing meeting.
2. Call find_meeting_at to locate it.
3. Find new slot, confirm, then call reschedule_meeting.

PHONE CONFIRMATION (German example): "Ich habe plus vier neun, eins sieben eins, eins zwei drei vier, fünf sechs sieben. Ist das korrekt?"

If phone confirmation fails twice, say you can continue without a phone number and finish the booking.

PRIVACY: If asked about other calendar entries: "Ich sehe nur die Verfügbarkeit, keine Termindetails." """


class BrowserWebSocketSerializer:
    """Serialize Pipecat frames to the existing browser WebSocket protocol."""

    def __init__(
        self,
        *,
        input_sample_rate: int = 16000,
        output_sample_rate: int = 24000,
        text_processor: TextOutputProcessor | None = None,
        state_processor: StateProcessor | None = None,
        interruption_processor: InterruptionProcessor | None = None,
        trace: TraceLogger | None = None,
    ) -> None:
        from pipecat.serializers.base_serializer import FrameSerializer

        class _Serializer(FrameSerializer):
            async def serialize(inner_self, frame):
                return await self._serialize(frame)

            async def deserialize(inner_self, data):
                return await self._deserialize(data)

        self._serializer = _Serializer()
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.audio_processor = AudioInputProcessor(expected_sample_rate=input_sample_rate)
        self.text_processor = text_processor or TextOutputProcessor()
        self.state_processor = state_processor
        self.interruption_processor = interruption_processor or InterruptionProcessor()
        self.trace = trace
        self._first_audio_in = False
        self._first_audio_out = False
        self._audio_in_frames = 0

    @property
    def serializer(self):
        return self._serializer

    async def _deserialize(self, data: str | bytes):
        from pipecat.frames.frames import (
            EndFrame,
            InputAudioRawFrame,
            InputTextRawFrame,
            InterruptionFrame,
        )

        if isinstance(data, bytes):
            if not self.audio_processor.observe_pcm16(data):
                if self.trace:
                    await self.trace.event("audio_in_dropped", {"bytes": len(data), "reason": "odd_pcm_payload"})
                return None
            self._audio_in_frames += 1
            if self.trace and not self._first_audio_in:
                self._first_audio_in = True
                await self.trace.event("client_first_audio", {"bytes": len(data)})
            elif self.trace and self._audio_in_frames % 100 == 0:
                await self.trace.event("client_audio_summary", {
                    "frames": self._audio_in_frames,
                    "bytes_received": self.audio_processor.metrics.bytes_received,
                })
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self.input_sample_rate,
                num_channels=1,
            )

        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            return None

        msg_type = message.get("type")
        if msg_type == "end":
            return EndFrame(reason="client_end")
        if msg_type == "interrupt":
            if self.trace:
                await self.trace.event("client_interrupt", {
                    "rms": message.get("rms"),
                    "client_at": message.get("at"),
                })
            return InterruptionFrame()
        if msg_type == "trace":
            if self.trace:
                await self.trace.event(
                    f"client_{message.get('event', 'trace')}",
                    message.get("metadata", {}),
                )
            return None
        if msg_type == "transcript_hint":
            text = message.get("text", "")
            if self.interruption_processor.classify_text(text) == "interruption":
                return InterruptionFrame()
            if text:
                return InputTextRawFrame(text=text)
        return None

    async def _serialize(self, frame) -> str | bytes | None:
        from pipecat.frames.frames import (
            ErrorFrame,
            InterruptionFrame,
            LLMFullResponseEndFrame,
            OutputAudioRawFrame,
            OutputTransportMessageFrame,
            OutputTransportMessageUrgentFrame,
            TranscriptionFrame,
            TTSAudioRawFrame,
            TTSTextFrame,
        )

        if isinstance(frame, (OutputAudioRawFrame, TTSAudioRawFrame)):
            if self.trace and not self._first_audio_out:
                self._first_audio_out = True
                await self.trace.event("assistant_first_audio", {"bytes": len(frame.audio)})
            return frame.audio

        if isinstance(frame, InterruptionFrame):
            if self.trace:
                await self.trace.event("server_interrupted")
            return json.dumps({"type": "interrupted"})

        if isinstance(frame, TranscriptionFrame) and frame.text:
            if self.trace:
                await self.trace.event("user_transcript", {"text": frame.text})
            return json.dumps({"type": "transcript", "role": "user", "text": frame.text})

        if isinstance(frame, TTSTextFrame) and frame.text:
            text = self.text_processor.normalize(frame.text)
            if self.state_processor:
                self.state_processor.record_assistant_playback(text)
            if self.trace:
                await self.trace.event("assistant_transcript", {"text": text})
            return json.dumps({"type": "transcript", "role": "assistant", "text": text})

        if isinstance(frame, LLMFullResponseEndFrame):
            if self.trace:
                await self.trace.event("assistant_turn_complete")
            return json.dumps({"type": "turn_complete"})

        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if isinstance(frame.message, str):
                return frame.message
            return json.dumps(frame.message)

        if isinstance(frame, ErrorFrame):
            message = getattr(frame, "error", None) or getattr(frame, "message", None) or "Session error."
            if self.trace:
                await self.trace.event("pipeline_error", {"message": str(message)})
            return json.dumps({"type": "error", "message": str(message)})

        return None


class InitialContextProcessor:
    """Inject an LLM context once so Gemini Live can execute registered tools."""

    def __init__(self, system_prompt: str, context=None) -> None:
        from pipecat.processors.frame_processor import FrameProcessor
        from pipecat.processors.aggregators.llm_context import LLMContext

        _context = context if context is not None else LLMContext()

        class _Processor(FrameProcessor):
            async def process_frame(inner_self, frame, direction):
                from pipecat.frames.frames import LLMContextFrame, StartFrame

                await super(_Processor, inner_self).process_frame(frame, direction)
                await inner_self.push_frame(frame, direction)
                if isinstance(frame, StartFrame):
                    await inner_self.push_frame(LLMContextFrame(_context))

        self.processor = _Processor(name="initial-context")


class ToolCallAggregator:
    """Forward tool call results back to Gemini Live via the shared LLM context.

    Pipecat's Gemini Live service sends tool results through
    _process_completed_function_calls, which only runs when a new
    LLMContextFrame arrives with the result already in the context.
    Without a full context aggregator pair in the pipeline the
    FunctionCallResultFrame broadcast by llm_service never updates
    the context, so send_tool_response is never called and the model
    waits silently.  This minimal processor sits upstream of the LLM,
    catches the upstream-propagating FunctionCallResultFrame, writes
    the result into the shared context, and pushes a fresh
    LLMContextFrame downstream so the LLM service triggers
    send_tool_response.
    """

    def __init__(self, context) -> None:
        import json as _json
        from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

        _context = context

        class _Proc(FrameProcessor):
            async def process_frame(inner_self, frame, direction):
                from pipecat.frames.frames import (
                    FunctionCallResultFrame,
                    LLMContextFrame,
                )

                await super().process_frame(frame, direction)

                if isinstance(frame, FunctionCallResultFrame) and frame.result is not None:
                    result_content = _json.dumps(frame.result, ensure_ascii=False)
                    _context.add_message({
                        "role": "tool",
                        "tool_call_id": frame.tool_call_id,
                        "content": result_content,
                    })
                    await inner_self.push_frame(
                        LLMContextFrame(_context), FrameDirection.DOWNSTREAM
                    )

                await inner_self.push_frame(frame, direction)

        self.processor = _Proc(name="tool-call-aggregator")


async def run_session(websocket, trace: TraceLogger | None = None) -> None:
    await run_pipecat_session(websocket, trace=trace)


async def run_pipecat_session(websocket, trace: TraceLogger | None = None) -> None:
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )
    from pipecat.workers.runner import WorkerRunner

    text_processor = TextOutputProcessor()
    session_state = SessionState()
    state_processor = StateProcessor(session_state)
    browser_protocol = BrowserWebSocketSerializer(
        text_processor=text_processor,
        state_processor=state_processor,
        trace=trace,
    )

    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_in_channels=1,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            audio_out_channels=1,
            serializer=browser_protocol.serializer,
            session_timeout=3600,
        ),
    )

    llm = GeminiLiveVertexLLMService(
        project_id=GCP_PROJECT_ID,
        location=GCP_LOCATION,
        system_instruction=build_system_prompt(),
        tools=PIPECAT_TOOLS,
        settings=GeminiLiveVertexLLMService.Settings(
            model=GEMINI_MODEL,
            voice="Charon",
            language="de-DE",
        ),
        inference_on_context_initialization=True,
    )

    booking_state = BookingSession()
    for tool_name in (
        "get_available_slots",
        "prepare_booking",
        "book_meeting",
        "find_meeting_at",
        "reschedule_meeting",
    ):
        llm.register_function(
            tool_name,
            functools.partial(_handle_pipecat_tool_call, trace=trace, booking_state=booking_state),
        )

    from pipecat.processors.aggregators.llm_context import LLMContext

    shared_context = LLMContext()
    pipeline = Pipeline(
        [
            transport.input(),
            InitialContextProcessor(build_system_prompt(), shared_context).processor,
            ToolCallAggregator(shared_context).processor,
            llm,
            transport.output(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=16000,
            audio_out_sample_rate=24000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=3600,
        enable_rtvi=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        await runner.cancel()

    @transport.event_handler("on_session_timeout")
    async def on_session_timeout(_transport, _client):
        await runner.cancel()

    try:
        logger.info("Starting Pipecat Gemini Live session")
        if trace:
            await trace.event("pipeline_start", {"runtime": "pipecat_gemini_live"})
        await runner.run(worker)
    except Exception as e:
        logger.error("Pipecat session error: %s\n%s", e, traceback.format_exc())
        if trace:
            await trace.event("pipeline_error", {"error": str(e)})
        try:
            await websocket.send_json({"type": "error", "message": "Session error. Please refresh."})
        except Exception:
            pass


async def _handle_pipecat_tool_call(
    params,
    trace: TraceLogger | None = None,
    booking_state: BookingSession | None = None,
) -> None:
    result = await _dispatch_tool(
        params.function_name,
        dict(params.arguments or {}),
        trace=trace,
        booking_state=booking_state,
    )
    await params.result_callback(result)


async def _dispatch_tool(
    name: str,
    args: dict,
    trace: TraceLogger | None = None,
    booking_state: BookingSession | None = None,
    transition_trace: TraceLogger | None = None,
) -> dict:
    if trace:
        return await trace.trace_tool(
            name,
            args,
            lambda: _dispatch_tool(
                name,
                args,
                trace=None,
                booking_state=booking_state,
                transition_trace=trace,
            ),
        )

    try:
        booking_state = booking_state or BookingSession()
        if name == "get_available_slots":
            now = datetime.now(BERLIN_TZ)
            days = args.get("days_ahead", 14)
            duration = args.get("duration_minutes", 30)
            slot_type = args.get("slot_type", "business")
            from datetime import timedelta
            date_end = now + timedelta(days=days)
            slots = await calendar_service.get_available_slots(
                date_range_start=now,
                date_range_end=date_end,
                duration_minutes=duration,
                slot_type=slot_type,
                buffer_minutes=BUFFER_MINUTES,
            )
            slots_with_ids, _transitions = booking_state.record_availability(
                slots[:6],
                slot_type=slot_type,
                duration_minutes=duration,
            )
            await _trace_booking_transitions(transition_trace, _transitions)
            return {"success": True, "state": booking_state.state.value, "slots": slots_with_ids}

        elif name == "prepare_booking":
            result, _transitions = booking_state.prepare_booking(args)
            await _trace_booking_transitions(transition_trace, _transitions)
            return result

        elif name == "book_meeting":
            async def create_event(**facts):
                return await calendar_service.create_event(
                    title=facts["title"],
                    start_iso=facts["start_iso"],
                    end_iso=facts["end_iso"],
                    visitor_name=facts["visitor_name"],
                    topic=facts["topic"],
                    visitor_phone=facts.get("visitor_phone"),
                    visitor_phone_confirmed=bool(facts.get("visitor_phone_confirmed")),
                    meeting_type=facts.get("meeting_type"),
                )

            result, _transitions = await booking_state.book_prepared(
                args,
                create_event=create_event,
            )
            await _trace_booking_transitions(transition_trace, _transitions)
            return result

        elif name == "find_meeting_at":
            return await calendar_service.find_meeting_at(
                approx_datetime_iso=args["approx_datetime_iso"],
                visitor_phone=args.get("visitor_phone"),
            )

        elif name == "reschedule_meeting":
            return await calendar_service.reschedule_meeting(
                event_id=args["event_id"],
                new_start_iso=args["new_start_iso"],
                new_end_iso=args["new_end_iso"],
            )

        else:
            return {"success": False, "error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return {"success": False, "error": str(e)}


async def _trace_booking_transitions(
    trace: TraceLogger | None,
    transitions,
) -> None:
    if not trace:
        return
    for transition in transitions:
        await trace.event(
            "booking_state_transition",
            {
                "event": transition.event,
                "from_state": transition.from_state,
                "to_state": transition.to_state,
                "metadata": transition.metadata,
            },
        )


def build_pipecat_pipeline(websocket):
    """Build the Pipecat pipeline used by the active runtime."""
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.services.google.gemini_live.vertex.llm import GeminiLiveVertexLLMService
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    browser_protocol = BrowserWebSocketSerializer()
    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_in_sample_rate=16000,
            audio_out_enabled=True,
            audio_out_sample_rate=24000,
            serializer=browser_protocol.serializer,
        ),
    )
    llm = GeminiLiveVertexLLMService(
        project_id=GCP_PROJECT_ID,
        location=GCP_LOCATION,
        system_instruction=build_system_prompt(),
        tools=PIPECAT_TOOLS,
        settings=GeminiLiveVertexLLMService.Settings(
            model=GEMINI_MODEL,
            voice="Charon",
            language="de-DE",
        ),
        inference_on_context_initialization=True,
    )
    booking_state = BookingSession()
    for tool_name in (
        "get_available_slots",
        "prepare_booking",
        "book_meeting",
        "find_meeting_at",
        "reschedule_meeting",
    ):
        llm.register_function(
            tool_name,
            functools.partial(_handle_pipecat_tool_call, booking_state=booking_state),
        )
    return Pipeline([transport.input(), InitialContextProcessor(build_system_prompt()).processor, llm, transport.output()])
