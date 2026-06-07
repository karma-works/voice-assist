import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import BUFFER_MINUTES, GOOGLE_CALENDAR_ID as CALENDAR_ID

logger = logging.getLogger(__name__)

BERLIN_TZ = pytz.timezone("Europe/Berlin")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_service = None
_calendar_subscribed = False


def _get_service():
    global _service
    if _service is None:
        creds, _ = google.auth.default(scopes=SCOPES)
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _ensure_calendar_subscribed():
    """Subscribe the service account to the target calendar if not already in its list.

    Service accounts don't auto-accept calendar sharing invites. Without an
    explicit calendarList.insert() the service account can't find the calendar
    and events.insert() returns 404 even when write access has been granted.
    """
    global _calendar_subscribed
    if _calendar_subscribed:
        return
    svc = _get_service()
    try:
        svc.calendarList().get(calendarId=CALENDAR_ID).execute()
        logger.info("Calendar %s already in service account list.", CALENDAR_ID)
        _calendar_subscribed = True
    except HttpError as e:
        if e.resp.status == 404:
            logger.info(
                "Calendar %s not yet in service account list — subscribing.", CALENDAR_ID
            )
            svc.calendarList().insert(body={"id": CALENDAR_ID}).execute()
            logger.info("Successfully subscribed to calendar %s.", CALENDAR_ID)
            _calendar_subscribed = True
        else:
            logger.error(
                "Could not subscribe to calendar %s: %s (status %s)",
                CALENDAR_ID, e, e.resp.status,
            )
            raise


async def get_calendar_ids() -> list[str]:
    def _fetch():
        _ensure_calendar_subscribed()
        svc = _get_service()
        result = svc.calendarList().list().execute()
        ids = [item["id"] for item in result.get("items", [])]
        if CALENDAR_ID not in ids:
            ids.append(CALENDAR_ID)
        return ids
    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def get_busy_times(time_min: datetime, time_max: datetime) -> list[tuple[datetime, datetime]]:
    cal_ids = await get_calendar_ids()

    def _fetch():
        svc = _get_service()
        body = {
            "timeMin": time_min.astimezone(timezone.utc).isoformat(),
            "timeMax": time_max.astimezone(timezone.utc).isoformat(),
            "items": [{"id": cid} for cid in cal_ids],
        }
        result = svc.freebusy().query(body=body).execute()
        busy = []
        for cal_data in result.get("calendars", {}).values():
            for period in cal_data.get("busy", []):
                s = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
                busy.append((s, e))
        busy.sort(key=lambda x: x[0])
        return busy

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def readiness_check() -> dict:
    """Validate calendar connectivity without returning event contents."""
    now = datetime.now(timezone.utc)
    try:
        calendar_ids = await get_calendar_ids()
        if not calendar_ids:
            return {"ready": False, "message": "No calendars are visible to the service account."}

        def _freebusy_probe():
            svc = _get_service()
            body = {
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(minutes=1)).isoformat(),
                "items": [{"id": calendar_ids[0]}],
            }
            svc.freebusy().query(body=body).execute()

        await asyncio.get_event_loop().run_in_executor(None, _freebusy_probe)
        return {"ready": True, "message": "Calendar connected."}
    except Exception as exc:
        return {"ready": False, "message": f"Calendar connection unavailable: {exc}"}


async def get_available_slots(
    date_range_start: datetime,
    date_range_end: datetime,
    duration_minutes: int,
    slot_type: str,
    buffer_minutes: int = 15,
) -> list[dict]:
    busy = await get_busy_times(date_range_start, date_range_end)
    slots = []
    buf = timedelta(minutes=buffer_minutes)
    dur = timedelta(minutes=duration_minutes)
    current = date_range_start.astimezone(BERLIN_TZ)
    end_range = date_range_end.astimezone(BERLIN_TZ)

    while current < end_range and len(slots) < 6:
        hour = current.hour + current.minute / 60.0
        weekday = current.weekday()

        if slot_type == "business":
            if weekday >= 5:
                current = _next_window_start(current, slot_type)
                continue
            if hour < 7:
                current = current.replace(hour=7, minute=0, second=0, microsecond=0)
                continue
            if hour + duration_minutes / 60 > 15:
                current = _next_window_start(current, slot_type)
                continue
        else:
            if hour + duration_minutes / 60 > 22:
                current = _next_window_start(current, slot_type)
                continue

        slot_start = current.astimezone(timezone.utc)
        slot_end = slot_start + dur
        conflict = False

        for bs, be in busy:
            if slot_start < be + buf and slot_end > bs - buf:
                conflict = True
                current = (be + buf).astimezone(BERLIN_TZ)
                current = _round_up_to_quarter(current)
                break

        if not conflict:
            berlin_start = slot_start.astimezone(BERLIN_TZ)
            slots.append({
                "start": slot_start.isoformat(),
                "end": slot_end.isoformat(),
                "display": berlin_start.strftime("%A, %B %d at %H:%M (Berlin time)"),
            })
            current += timedelta(minutes=30)

    return slots


def _round_up_to_quarter(dt: datetime) -> datetime:
    r = dt.minute % 15
    if r == 0:
        return dt.replace(second=0, microsecond=0)
    return dt.replace(minute=dt.minute + (15 - r), second=0, microsecond=0)


def _next_window_start(current: datetime, slot_type: str) -> datetime:
    berlin = current.astimezone(BERLIN_TZ)
    if slot_type == "business":
        nxt = berlin.replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    else:
        return berlin.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)


async def create_event(
    title: str,
    start_iso: str,
    end_iso: str,
    visitor_name: str,
    topic: str,
    visitor_phone: Optional[str] = None,
    visitor_phone_confirmed: bool = False,
    meeting_type: Optional[str] = None,
) -> dict:
    def _create():
        _ensure_calendar_subscribed()
        svc = _get_service()
        description_lines = [
            f"Meeting topic: {topic}",
            "Scheduled via voice assistant.",
        ]
        if visitor_phone and visitor_phone_confirmed:
            description_lines.append(f"Confirmed visitor phone: {visitor_phone}")
        if meeting_type:
            description_lines.append(f"Meeting type: {meeting_type}")
        event = {
            "summary": title,
            "description": "\n".join(description_lines),
            "start": {"dateTime": start_iso, "timeZone": "Europe/Berlin"},
            "end": {"dateTime": end_iso, "timeZone": "Europe/Berlin"},
        }
        return svc.events().insert(calendarId=CALENDAR_ID, body=event, sendUpdates="none").execute()

    result = await asyncio.get_event_loop().run_in_executor(None, _create)
    return {"event_id": result["id"], "html_link": result.get("htmlLink", "")}


async def find_meeting_at(
    approx_datetime_iso: str,
    visitor_phone: Optional[str] = None,
    tolerance_minutes: int = 30,
) -> dict:
    def _find():
        svc = _get_service()
        target = datetime.fromisoformat(approx_datetime_iso.replace("Z", "+00:00"))
        tmin = (target - timedelta(minutes=tolerance_minutes)).isoformat()
        tmax = (target + timedelta(minutes=tolerance_minutes)).isoformat()
        result = svc.events().list(
            calendarId=CALENDAR_ID,
            timeMin=tmin,
            timeMax=tmax,
            singleEvents=True,
        ).execute()
        matches = []
        for event in result.get("items", []):
            if event.get("status") == "cancelled":
                continue
            start_str = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
            end_str = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
            if not start_str or not end_str:
                continue
            if visitor_phone:
                description = event.get("description", "")
                if visitor_phone not in description:
                    continue
            matches.append({"event_id": event["id"], "start": start_str, "end": end_str})

        if not matches:
            return {"found": False}
        if len(matches) == 1:
            return {"found": True, **matches[0]}
        return {"found": True, "multiple": True, "matches": matches}

    return await asyncio.get_event_loop().run_in_executor(None, _find)


async def reschedule_meeting(event_id: str, new_start_iso: str, new_end_iso: str) -> dict:
    def _update():
        svc = _get_service()
        patch = {
            "start": {"dateTime": new_start_iso, "timeZone": "Europe/Berlin"},
            "end": {"dateTime": new_end_iso, "timeZone": "Europe/Berlin"},
        }
        result = svc.events().patch(
            calendarId=CALENDAR_ID, eventId=event_id, body=patch, sendUpdates="all"
        ).execute()
        return {"event_id": result["id"], "start": result["start"]["dateTime"]}

    return await asyncio.get_event_loop().run_in_executor(None, _update)
