import asyncio
import traceback
from datetime import datetime

import pytz
from google import genai
from google.genai import types
import logging
logger = logging.getLogger(__name__)

from src.config import GEMINI_API_KEY, GEMINI_MODEL, BUFFER_MINUTES
from src import calendar_service

BERLIN_TZ = pytz.timezone("Europe/Berlin")

TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_available_slots",
                description="Get available meeting time slots. Use this to find when a meeting can be scheduled.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "days_ahead": types.Schema(
                            type=types.Type.INTEGER,
                            description="How many days ahead to search (e.g. 14 for next 2 weeks). Default: 14",
                        ),
                        "duration_minutes": types.Schema(
                            type=types.Type.INTEGER,
                            description="Meeting duration in minutes. Default: 30",
                        ),
                        "slot_type": types.Schema(
                            type=types.Type.STRING,
                            description="'business' (Mon-Fri 7:00-15:00 Berlin) or 'private' (any day 0:00-22:00 Berlin)",
                        ),
                    },
                    required=["slot_type"],
                ),
            ),
            types.FunctionDeclaration(
                name="book_meeting",
                description="Book a meeting in the calendar. Only call after explicit visitor confirmation of time, name, and email.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "title": types.Schema(type=types.Type.STRING, description="Meeting title"),
                        "start_iso": types.Schema(type=types.Type.STRING, description="Start datetime ISO 8601 with timezone"),
                        "end_iso": types.Schema(type=types.Type.STRING, description="End datetime ISO 8601 with timezone"),
                        "visitor_name": types.Schema(type=types.Type.STRING),
                        "visitor_email": types.Schema(type=types.Type.STRING, description="Email to send calendar invite to"),
                        "topic": types.Schema(type=types.Type.STRING, description="Meeting topic/purpose"),
                    },
                    required=["title", "start_iso", "end_iso", "visitor_name", "visitor_email", "topic"],
                ),
            ),
            types.FunctionDeclaration(
                name="find_meeting_at",
                description="Find an existing meeting at an approximate time. Use to identify a meeting before rescheduling.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "approx_datetime_iso": types.Schema(
                            type=types.Type.STRING,
                            description="Approximate datetime ISO 8601 of the existing meeting",
                        ),
                        "visitor_email": types.Schema(
                            type=types.Type.STRING,
                            description="Optional: visitor email to match against guest list",
                        ),
                    },
                    required=["approx_datetime_iso"],
                ),
            ),
            types.FunctionDeclaration(
                name="reschedule_meeting",
                description="Move an existing meeting to a new time. Requires event_id from find_meeting_at. Only call after visitor confirms the new time.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "event_id": types.Schema(type=types.Type.STRING),
                        "new_start_iso": types.Schema(type=types.Type.STRING),
                        "new_end_iso": types.Schema(type=types.Type.STRING),
                    },
                    required=["event_id", "new_start_iso", "new_end_iso"],
                ),
            ),
        ]
    )
]


def build_system_prompt() -> str:
    now = datetime.now(BERLIN_TZ)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    date_str = f"{weekdays[now.weekday()]}, {now.strftime('%B %d, %Y, %H:%M')} (Europe/Berlin)"

    return f"""You are a scheduling assistant helping visitors book or reschedule meetings with Christian.

Today is: {date_str}

LANGUAGE: Detect the visitor's language from their first message and respond in that language for the entire conversation. Never switch languages mid-conversation. If uncertain, use English.

YOUR CAPABILITIES:
- Find available meeting slots
- Book new meetings
- Find and reschedule existing meetings

WHAT YOU CANNOT DO:
- You do not have access to meeting titles, descriptions, or participants of existing calendar entries
- You can only see whether time slots are free or busy
- Never reveal, confirm, or deny what specific meetings exist on the calendar

MEETING TYPE RULES (strictly enforced server-side):
- Business/professional meetings: Monday-Friday, 07:00-15:00 Berlin time only
- Personal/private meetings: Any day, 00:00-22:00 Berlin time
- When classifying: if professional context is mentioned (company, work, project, invoice, collaboration) → business. If personal context → private. When ambiguous, assume business.

BOOKING FLOW:
1. Ask what the meeting is about (to infer type)
2. Call get_available_slots with appropriate slot_type
3. Present 3 concrete options with day and time
4. After visitor picks a slot, ask for their full name
5. Ask for their email address, then repeat it back letter by letter for confirmation
6. Confirm the final details before calling book_meeting
7. After booking: confirm and mention they'll receive a calendar invite

RESCHEDULE FLOW:
1. Ask for the approximate date/time of the existing meeting
2. Call find_meeting_at to locate it
3. If found, ask for preferred new time or call get_available_slots
4. Confirm the new time before calling reschedule_meeting

EMAIL CONFIRMATION: Always repeat email back letter by letter before booking. Example: "I have j-o-h-n at e-x-a-m-p-l-e dot c-o-m — is that correct?"

PRIVACY: If asked about other meetings on the calendar, say: "I can only see availability, not meeting details." Never say what is blocking a time slot."""


async def run_session(websocket) -> None:
    client = genai.Client(api_key=GEMINI_API_KEY)

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=build_system_prompt(),
        tools=TOOLS,
        speech_config=types.SpeechConfig(
            language_code="en-US",
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Charon")
            ),
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=False,
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    try:
        async with client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
            receive_task = asyncio.create_task(_receive_from_gemini(session, websocket))
            send_task = asyncio.create_task(_send_from_client(session, websocket))

            done, pending = await asyncio.wait(
                [receive_task, send_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            for task in done:
                if task.exception():
                    logger.error(f"Session task error: {task.exception()}")

    except Exception as e:
        logger.error(f"Session error: {e}\n{traceback.format_exc()}")
        try:
            await websocket.send_json({"type": "error", "message": "Session error. Please refresh."})
        except Exception:
            pass


async def _send_from_client(session, websocket) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"]:
                audio_data = message["bytes"]
                await session.send_realtime_input(
                    audio=types.Blob(data=audio_data, mime_type="audio/pcm;rate=16000")
                )
            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                    if data.get("type") == "end":
                        break
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        if "disconnect" not in str(e).lower():
            logger.error(f"Client send error: {e}")


async def _receive_from_gemini(session, websocket) -> None:
    try:
        async for response in session.receive():
            if response.data:
                await websocket.send_bytes(response.data)

            if response.tool_call:
                tool_responses = []
                for fc in response.tool_call.function_calls:
                    result = await _dispatch_tool(fc.name, fc.args or {})
                    tool_responses.append(
                        types.FunctionResponse(
                            name=fc.name,
                            id=fc.id,
                            response={"result": result},
                        )
                    )
                await session.send_tool_response(function_responses=tool_responses)

            if response.server_content:
                sc = response.server_content

                if sc.input_transcription and sc.input_transcription.text:
                    await websocket.send_json({
                        "type": "transcript",
                        "role": "user",
                        "text": sc.input_transcription.text,
                    })

                if sc.output_transcription and sc.output_transcription.text:
                    await websocket.send_json({
                        "type": "transcript",
                        "role": "assistant",
                        "text": sc.output_transcription.text,
                    })

                if sc.turn_complete:
                    await websocket.send_json({"type": "turn_complete"})

    except Exception as e:
        if "disconnect" not in str(e).lower():
            logger.error(f"Gemini receive error: {e}")


async def _dispatch_tool(name: str, args: dict) -> dict:
    try:
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
            return {"slots": slots[:6]}

        elif name == "book_meeting":
            return await calendar_service.create_event(
                title=args["title"],
                start_iso=args["start_iso"],
                end_iso=args["end_iso"],
                visitor_name=args["visitor_name"],
                visitor_email=args["visitor_email"],
                topic=args["topic"],
            )

        elif name == "find_meeting_at":
            return await calendar_service.find_meeting_at(
                approx_datetime_iso=args["approx_datetime_iso"],
                visitor_email=args.get("visitor_email"),
            )

        elif name == "reschedule_meeting":
            return await calendar_service.reschedule_meeting(
                event_id=args["event_id"],
                new_start_iso=args["new_start_iso"],
                new_end_iso=args["new_end_iso"],
            )

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.error(f"Tool {name} error: {e}")
        return {"error": str(e)}
