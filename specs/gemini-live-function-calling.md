# Gemini Live: Function Calling Deep Dive

Relevant to: ADR-001, challenges.md (#2), implementation-plan.md (Week 2)

---

## What Actually Happens: The Protocol

Gemini Live runs over a persistent WebSocket. The session is bidirectional: the client streams audio chunks in, and the server streams audio chunks back. Function calling interrupts this audio flow with a synchronous (by default) side-channel for tool execution.

The exchange at the protocol level:

```
Client                          Gemini Live Server
  |                                   |
  |-- audio chunk (PCM) ------------>|
  |-- audio chunk (PCM) ------------>|
  |-- audio chunk (PCM) ------------>|
  |                                   | (model transcribes, reasons)
  |<-- BidiGenerateContentServerContent (audio bytes: "Ich schaue nach...") --
  |<-- audio bytes -------------------|
  |                                   |
  |<-- ToolCallRequest -------------  | ← model pauses audio output here
  |   {                               |
  |     tool_call: {                  |
  |       function_calls: [{          |
  |         id: "call_abc123",        |
  |         name: "get_calendar_events",
  |         args: {                   |
  |           time_min: "2026-06-01T00:00:00Z",
  |           time_max: "2026-06-07T23:59:59Z"
  |         }                         |
  |       }]                          |
  |     }                             |
  |   }                               |
  |                                   |
  | [client executes Google Calendar API call ~200-400ms]
  |                                   |
  |-- send_tool_response ------------>|
  |   {                               |
  |     function_responses: [{        |
  |       name: "get_calendar_events",|
  |       id: "call_abc123",          |
  |       response: {                 |
  |         result: [                 |
  |           {title: "Zahnarzt",     |
  |            start: "2026-06-03T10:00:00+02:00"},
  |           ...                     |
  |         ]                         |
  |       }                           |
  |     }]                            |
  |   }                               |
  |                                   | (model resumes reasoning with tool result)
  |<-- audio bytes ("Du hast am Mittwoch...") --|
  |<-- audio bytes -------------------|
```

The key detail: **the model pauses its audio output the moment it emits a `ToolCallRequest`**. It will not produce more audio until it receives the `ToolResponse`. This is the blocking mode.

---

## Blocking vs Non-Blocking Tool Calls

### Blocking (default, Gemini 2.5 Flash Live + all Gemini 3.x)

```python
# Tool defined without behavior flag = blocking
{
    "function_declarations": [{
        "name": "get_calendar_events",
        "description": "...",
        "parameters": { ... }
    }]
}
```

Behavior:
- Model emits `ToolCallRequest`, stops all audio output
- Waits for `ToolResponse`
- Resumes audio with the result incorporated
- User hears silence during the tool call

**Latency impact:** For a `get_calendar_events` call to Google Calendar API, expect 150–400ms. The assistant goes quiet for that duration. At 200ms this is barely perceptible; at 400ms+ it sounds like a lag.

### Non-Blocking (Gemini 2.5 Flash Live only, NOT available on Gemini 3.1 Flash)

```python
# Tool defined with NON_BLOCKING behavior
{
    "function_declarations": [{
        "name": "get_calendar_events",
        "description": "...",
        "parameters": { ... },
        "behavior": "NON_BLOCKING"   # ← Gemini 2.5 Flash Live only
    }]
}
```

With `NON_BLOCKING`, the model continues speaking (filler/preamble audio) while your client executes the tool. When you send the `ToolResponse`, you specify how the model should incorporate it:

```python
await session.send_tool_response(
    function_responses=[
        types.FunctionResponse(
            name="get_calendar_events",
            id=tool_call_id,
            response={
                "result": events,
                "scheduling": "WHEN_IDLE"   # or "INTERRUPT"
            }
        )
    ]
)
```

`scheduling` options:
- `WHEN_IDLE` — model incorporates result at the next natural pause. Best for read queries where you don't need to interrupt.
- `INTERRUPT` — immediately stops current audio and restarts with tool result. Use for writes where confirmation must be specific.
- `SILENT` — stores the result as context without changing the audio output. Use for background pre-loading.

**For voice-assist:** Use non-blocking with `WHEN_IDLE` for `get_calendar_events`. Use blocking (or non-blocking with `INTERRUPT`) for `create_event`, `update_event`, `delete_event` — those need explicit confirmation.

---

## Tool Definition Format

The exact format that the `google-genai` Python SDK accepts:

```python
from google import genai
from google.genai import types

# Define tools using the types API
tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="get_calendar_events",
                description=(
                    "Ruft Kalendertermine aus dem Google Kalender ab. "
                    "Verwende diese Funktion wenn der Nutzer nach Terminen fragt."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "time_min": types.Schema(
                            type=types.Type.STRING,
                            description="Startzeit im ISO 8601 Format (z.B. 2026-06-01T00:00:00Z)"
                        ),
                        "time_max": types.Schema(
                            type=types.Type.STRING,
                            description="Endzeit im ISO 8601 Format"
                        ),
                        "query": types.Schema(
                            type=types.Type.STRING,
                            description="Optionaler Suchbegriff zum Filtern von Terminen"
                        ),
                    },
                    required=["time_min", "time_max"]
                ),
                # Non-blocking: model continues speaking while we fetch
                behavior=types.Behavior.NON_BLOCKING,   # Gemini 2.5 Flash Live only
            ),
            types.FunctionDeclaration(
                name="create_calendar_event",
                description=(
                    "Erstellt einen neuen Termin im Google Kalender. "
                    "Bestätige immer den Titel, Datum und Uhrzeit bevor du diese Funktion aufrufst."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "title": types.Schema(type=types.Type.STRING),
                        "start_datetime": types.Schema(
                            type=types.Type.STRING,
                            description="ISO 8601 datetime, z.B. 2026-06-03T14:00:00+02:00"
                        ),
                        "end_datetime": types.Schema(type=types.Type.STRING),
                        "description": types.Schema(type=types.Type.STRING),
                    },
                    required=["title", "start_datetime", "end_datetime"]
                ),
                # Blocking: we need the confirmation to be specific
            ),
            types.FunctionDeclaration(
                name="update_calendar_event",
                description="Aktualisiert einen bestehenden Termin im Google Kalender.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "event_id": types.Schema(type=types.Type.STRING),
                        "title": types.Schema(type=types.Type.STRING),
                        "start_datetime": types.Schema(type=types.Type.STRING),
                        "end_datetime": types.Schema(type=types.Type.STRING),
                    },
                    required=["event_id"]
                ),
            ),
            types.FunctionDeclaration(
                name="delete_calendar_event",
                description="Löscht einen Termin aus dem Google Kalender.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "event_id": types.Schema(type=types.Type.STRING),
                        "confirm": types.Schema(
                            type=types.Type.BOOLEAN,
                            description="Muss true sein, wenn der Nutzer die Löschung bestätigt hat"
                        ),
                    },
                    required=["event_id", "confirm"]
                ),
            ),
        ]
    )
]
```

**Note on descriptions:** Write them in German. The model uses them to decide which tool to call, and German descriptions improve accuracy in German-language sessions.

---

## Session Initialization

```python
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

config = types.LiveConnectConfig(
    model="models/gemini-2.5-flash-live-preview",   # or gemini-2.0-flash-live
    system_instruction=types.Content(
        parts=[types.Part(text=system_prompt)]
    ),
    tools=tools,
    generation_config=types.GenerationConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            language_code="de-DE",
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        )
    ),
    realtime_input_config=types.RealtimeInputConfig(
        # VAD config for barge-in sensitivity
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=20,
            silence_duration_ms=500,
        )
    )
)

async with client.aio.live.connect(config=config) as session:
    await run_session(session)
```

---

## The Tool Call Handler

The complete receive loop handling tool calls:

```python
async def receive_loop(session, websocket, calendar_service):
    """Handles all messages from Gemini Live."""
    async for message in session.receive():

        # Audio output chunk — forward to client
        if message.data:
            await websocket.send_bytes(message.data)

        # Tool call — execute and respond
        if message.tool_call:
            await handle_tool_call(session, message.tool_call, calendar_service)

        # Turn complete signal
        if message.server_content:
            sc = message.server_content
            if sc.turn_complete:
                await websocket.send_json({"type": "turn_complete"})
            # Interrupted turn — Gemini cancelled pending tool calls
            if sc.interrupted:
                await websocket.send_json({"type": "interrupted"})

async def handle_tool_call(session, tool_call, calendar_service):
    """Execute tool calls and send responses back to Gemini."""
    function_responses = []

    for fc in tool_call.function_calls:
        try:
            result = await dispatch_tool(fc.name, fc.args, calendar_service)
            function_responses.append(
                types.FunctionResponse(
                    name=fc.name,
                    id=fc.id,
                    response={"result": result}
                )
            )
        except Exception as e:
            # Return errors as structured data, not exceptions
            # The model will speak the error in German
            function_responses.append(
                types.FunctionResponse(
                    name=fc.name,
                    id=fc.id,
                    response={"error": str(e), "result": None}
                )
            )

    await session.send_tool_response(function_responses=function_responses)

async def dispatch_tool(name: str, args: dict, calendar_service) -> any:
    """Route tool name to implementation."""
    match name:
        case "get_calendar_events":
            return await calendar_service.get_events(
                time_min=args["time_min"],
                time_max=args["time_max"],
                query=args.get("query"),
            )
        case "create_calendar_event":
            return await calendar_service.create_event(
                title=args["title"],
                start_datetime=args["start_datetime"],
                end_datetime=args["end_datetime"],
                description=args.get("description", ""),
            )
        case "update_calendar_event":
            return await calendar_service.update_event(event_id=args["event_id"], **{
                k: v for k, v in args.items() if k != "event_id"
            })
        case "delete_calendar_event":
            if not args.get("confirm"):
                return {"status": "aborted", "reason": "Bestätigung fehlt"}
            return await calendar_service.delete_event(event_id=args["event_id"])
        case _:
            raise ValueError(f"Unknown tool: {name}")
```

---

## What Happens During Barge-In (User Interrupts Mid-Tool-Call)

This is the edge case that will cause bugs if not handled explicitly.

**Scenario:** Model starts speaking, calls `get_calendar_events`, your backend starts the Google Calendar API request. Before you get the API response, Christian speaks again (interrupts).

What Gemini Live sends when interrupted during a pending tool call:

```json
{
  "serverContent": {
    "interrupted": true
  }
}
```

And then also (in the same or a subsequent message):

```json
{
  "serverContent": {
    "modelTurn": {
      "cancelledFunctionCalls": ["call_abc123"]
    }
  }
}
```

**What you must do:**

```python
# Track in-flight tool calls
in_flight_tool_calls: dict[str, asyncio.Task] = {}

async def handle_tool_call(session, tool_call, calendar_service):
    for fc in tool_call.function_calls:
        task = asyncio.create_task(
            execute_and_respond(session, fc, calendar_service)
        )
        in_flight_tool_calls[fc.id] = task

async def handle_interrupted(tool_call_ids_to_cancel: list[str]):
    for call_id in tool_call_ids_to_cancel:
        task = in_flight_tool_calls.pop(call_id, None)
        if task:
            task.cancel()
    # Do NOT send ToolResponse for cancelled calls
    # Gemini Live will ignore them and they may corrupt session state
```

**Critical:** If you send a `ToolResponse` for a cancelled tool call ID, it can corrupt the session or cause the model to re-process stale data. Track cancellations and drop the response.

---

## Latency Budget

For a voice-assist calendar query, the full latency from end-of-speech to start-of-response audio:

| Step | Typical duration |
|---|---|
| Gemini Live speech detection (end of utterance) | 300–500ms (silence duration) |
| Gemini transcription + reasoning | 100–300ms |
| ToolCallRequest emitted | 0ms (within reasoning) |
| Google Calendar API call (over HTTPS) | 150–400ms |
| ToolResponse processing by Gemini | 50–150ms |
| First audio byte back | 50–100ms |
| **Total (blocking tool call)** | **650ms – 1.45s** |

With non-blocking tool call + `WHEN_IDLE`:
- The model starts speaking *before* the Calendar API responds (e.g., "Lass mich kurz nachschauen...")
- The Calendar API response arrives mid-speech, queued for incorporation
- Total perceived latency to first audio: 400–700ms (model doesn't wait)
- The actual data arrives in the next sentence

**Use non-blocking for reads. Use blocking for writes.** The latency budget above is the primary reason.

---

## System Prompt Engineering for Tool Use

The system prompt must tell the model:
1. Which language to respond in (German)
2. The current datetime (critical for date parsing)
3. When and how to use each tool
4. Confirmation policy for writes

```python
from datetime import datetime
import pytz

def build_system_prompt() -> str:
    tz = pytz.timezone("Europe/Berlin")
    now = datetime.now(tz)
    weekdays_de = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]
    date_str = f"{weekdays_de[now.weekday()]}, {now.strftime('%d. %B %Y, %H:%M Uhr')} (Zeitzone: Europe/Berlin)"

    return f"""Du bist ein persönlicher Kalenderassistent von Christian.
Antworte immer auf Deutsch. Sei präzise und knapp.

Heute ist: {date_str}

Werkzeugnutzung:
- Nutze get_calendar_events für alle Fragen über Termine.
- Vor dem Erstellen, Ändern oder Löschen eines Termins, lese immer die Details laut vor und frage nach Bestätigung.
- Bei Erstellungen: gib Titel, Datum und Uhrzeit an und warte auf "ja" oder "stimmt".
- Bei Löschungen: bestätige den Titel und stelle sicher dass confirm=true gesetzt ist.
- Zeitangaben immer in der Zeitzone Europe/Berlin interpretieren.
- Mehrdeutige Zeitangaben (z.B. "nächsten Dienstag") immer konkret benennen: "Du meinst Dienstag, den 3. Juni?"

Wenn ein Werkzeugaufruf fehlschlägt, erkläre das Problem kurz und biete eine Alternative an."""
```

---

## Pipecat Integration Pattern

Pipecat abstracts the protocol details above behind its frame pipeline. The equivalent setup:

```python
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext

# Define tools in OpenAI-compatible format (Pipecat adapts to Gemini format)
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Ruft Kalendertermine ab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                },
                "required": ["time_min", "time_max"]
            }
        }
    }
]

context = OpenAILLMContext(
    messages=[{"role": "system", "content": build_system_prompt()}],
    tools=tools
)

llm = GeminiLiveLLMService(
    api_key=os.environ["GEMINI_API_KEY"],
    model="gemini-2.5-flash-live-preview",
    voice_id="Aoede",
    language="de-DE",
)

# Register tool handler — Pipecat calls this when the model invokes a tool
@llm.function_call_handler
async def handle_function_call(context, function_name, args, result_callback):
    result = await dispatch_tool(function_name, args, calendar_service)
    await result_callback(json.dumps(result))
```

Pipecat handles: session lifecycle, audio frame routing, VAD events, barge-in cancel signals, and the `ToolCallRequest` → `ToolResponse` protocol. You only write the `dispatch_tool` logic.

---

## The 5 Things Most Likely to Go Wrong

1. **Sending a ToolResponse for a cancelled call.** Gemini returns `cancelledFunctionCalls` IDs on interruption. If you respond anyway, the session state can become inconsistent. Always check before sending.

2. **Not injecting the current datetime in the system prompt.** Without it, "nächsten Dienstag" is ambiguous and the model will guess — sometimes wrong. Always include it, in German, at session start.

3. **Returning raw Python exceptions as ToolResponse errors.** If your Google Calendar API call throws, wrap it in a structured dict: `{"error": "Authentifizierungsfehler: Token abgelaufen", "result": null}`. The model can then speak the error in German. Uncaught exceptions will crash your handler and hang the session.

4. **Using non-blocking for write operations.** If `create_event` is non-blocking, the model might start saying "Alles erledigt!" before the Calendar API has confirmed the event exists — or before it failed. Writes must be blocking.

5. **Timezone handling.** `get_calendar_events` needs to pass `time_min` and `time_max` in UTC (as required by Google Calendar API), but the model reasons in Europe/Berlin time. The backend must convert: `datetime.fromisoformat(args["time_min"]).astimezone(pytz.utc).isoformat()`.
