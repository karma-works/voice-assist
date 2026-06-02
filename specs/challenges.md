# Challenges: Assumptions That Could Sink This

_Revised 2026-06-02_

---

## Challenge 1: Meeting type inference is reliable enough to enforce different time windows

**Assumption:** Gemini Live can reliably classify a meeting as "business" or "private" based on a short conversational exchange, and will always ask a clarifying question when ambiguous.

**Most likely failure mode:** A business contact describes their meeting in personal-sounding terms ("I just wanted to catch up about the project") and the agent applies private slot rules, offering a Saturday evening slot — which Christian doesn't want for work meetings.

**Failure consequence:** Christian gets business meetings booked outside business hours. Erosion of trust in the system; he stops generating invite links.

**Counter-evidence strength:** Medium. LLMs are generally good at intent classification, but "business vs. private" is not always clear-cut. The system prompt must be explicit and defensive.

**Mitigation:** The system prompt must instruct the agent to default to `business` when ambiguous — not to ask. A conservative default is better than a misclassification. Only classify as `private` when explicitly stated. Add "if in doubt, treat as business" as a rule.

---

## Challenge 2: The agent never leaks calendar content despite being instructed not to

**Assumption:** A sufficiently careful system prompt prevents Gemini Live from ever revealing event titles, participants, or topics of Christian's existing meetings, even under social engineering by a visitor.

**Most likely failure mode:** A visitor asks "What else does Christian have on Tuesday?" or "Is he in a meeting called X at 2pm?" and the model, being a cooperative language model, partially confirms or denies based on what it can see.

**Failure consequence:** Privacy breach. Christian's existing meetings, clients, or private appointments become visible to strangers who hold an invite link.

**Counter-evidence strength:** Strong. This is a well-documented failure mode of LLM agents: system prompt confidentiality is not reliable under adversarial prompting. A model that can see data tends to leak it.

**Mitigation:**
1. The tools themselves must not return identifying data. `get_available_slots` calls the **freebusy API** — which returns only time ranges, not event titles. The model literally cannot see what the blocking events are called.
2. `find_meeting_at` returns only `{event_id, start, end, found: bool}` — no title, no participants.
3. The system prompt reinforces: "You can only see time availability. You do not have access to meeting titles or participants."
4. This is defense in depth: API design is the primary protection, system prompt is secondary.

---

## Challenge 3: Invite link UUID collision or enumeration

**Assumption:** UUID v4 invite links are not guessable or enumerable by a third party.

**Most likely failure mode:** An attacker who receives one invite link doesn't try to enumerate other UUIDs (2^122 space — computationally infeasible). But: if the invite URL is ever shared publicly (e.g., forwarded in an email chain that goes to wrong people), multiple people can use it within the 10-day window.

**Failure consequence:** Strangers book meetings in Christian's calendar.

**Counter-evidence strength:** Weak on enumeration (UUID v4 is safe). Medium on forwarding (a shared link IS a risk).

**Mitigation:**
- UUID v4 is sufficient against enumeration attacks.
- Document clearly: "Invite links are not passwords. Anyone with the link can use it. Treat them like single-use tokens — don't forward." 
- Post-MVP: optional single-use mode (mark `status: "used"` after first completed booking).

---

## Challenge 4: `find_meeting_at` correctly identifies the right event for reschedule

**Assumption:** A visitor saying "I have a meeting Tuesday at 2pm" gives enough information to uniquely identify the calendar event to reschedule.

**Most likely failure mode:** Christian has two meetings on Tuesday at 2pm (back-to-back), or the visitor misremembers the time by 30 minutes, or it's a recurring event and only one instance should be changed.

**Failure consequence:** Wrong meeting gets rescheduled.

**Counter-evidence strength:** Strong. Meeting time is a fragile identifier. Title would be more reliable but is privacy-violating.

**Mitigation:**
- Search with ±30 minute tolerance, return all matches within that window.
- If multiple matches: ask visitor for more details ("Was it a 30-minute or 1-hour meeting?") until unambiguous.
- For recurring events: confirm "Do you want to change just this one occurrence or all future instances?" — default to single occurrence.
- Never reschedule without explicit verbal confirmation of the matched time.

---

## Challenge 5: Freebusy API is sufficient for accurate availability

**Assumption:** Google Calendar's freebusy API (which returns only busy time ranges, no event details) is sufficient to compute available slots correctly.

**Most likely failure mode:** The freebusy query is made against only the primary calendar. Christian has multiple calendars (work, personal, shared). Events on non-primary calendars don't block availability. A slot appears free but Christian is actually busy.

**Failure consequence:** Double-booked meeting. Embarrassing, requires manual cancellation.

**Counter-evidence strength:** Strong. Multi-calendar is the common case for any person who manages both work and personal calendars in Google.

**Mitigation:** The freebusy API supports querying multiple calendar IDs in a single request. At session start, fetch the list of Christian's calendars (via `calendarList.list`) and pass all of them to the freebusy query. This requires `calendar.readonly` scope, not just the primary calendar.

---

## Challenge 6: The 15-minute buffer causes no-slot-found situations

**Assumption:** Adding 15-minute buffers before and after each existing event is desirable and won't cause so many blocked slots that the agent can't find a free time.

**Most likely failure mode:** On a busy day with meetings from 7:00–15:00 with 30-minute gaps between them, the 15-minute buffer on each side reduces every 30-minute gap to 0 minutes — no slots available. The agent tells the visitor there's no availability for an entire week.

**Failure consequence:** Visitor can't book. Frustrated experience, they email Christian instead.

**Counter-evidence strength:** Medium. This is a configuration issue, not a fundamental flaw.

**Mitigation:** Make buffer size configurable (default: 15 min). Expose a config variable `BUFFER_MINUTES` in Terraform. If no slots found with buffer, agent can offer to check a wider date range or ask if the visitor is flexible on duration.

---

## Challenge 7: Gemini Live language auto-detection works reliably on the first exchange

**Assumption:** Gemini Live correctly identifies and matches the visitor's language (e.g., German, English, French) from their first spoken sentence, and maintains that language for the entire session.

**Most likely failure mode:** Visitor opens with a hesitant non-sentence ("Um... hi?") and the model defaults to English regardless. Or: the model detects the language correctly but switches to English mid-conversation when reciting structured data (times, dates).

**Failure consequence:** Confusing experience — visitor speaking German, agent responding in English.

**Counter-evidence strength:** Weak-medium. Gemini Live's multilingual detection is strong on full sentences. Short utterances are less reliable.

**Mitigation:** The system prompt should say: "Detect the visitor's language from their first message and respond in that language for the entire session. If uncertain, respond in English. Never switch languages mid-conversation." Also: initialize the session with a multilingual greeting that prompts the visitor to speak: "Please say what this meeting is about" in English — their response then establishes the language.

---

## Challenge 8: GitHub Actions `workflow_dispatch` is too slow/clunky for Christian's actual workflow

**Assumption:** Christian will realistically use GitHub Actions to generate invite links whenever he wants to schedule a meeting.

**Most likely failure mode:** The workflow takes 30–60 seconds, requires navigating to GitHub.com, switching to the Actions tab, and copying from the logs. In practice, Christian generates one link and shares it with multiple people to avoid the friction. This defeats the per-meeting intent.

**Failure consequence:** Links get forwarded widely, meetings get booked by wrong people.

**Counter-evidence strength:** Strong. GitHub Actions is a developer-native tool but it's genuinely not quick for a task you might do multiple times per day.

**Mitigation:** GitHub Actions is the MVP answer (minimal code). Post-MVP: an admin endpoint (`GET /admin/invite/new` authenticated via Google OAuth) that generates a link in 200ms and copies it to clipboard. Document the friction upfront so it's a known trade-off, not a surprise.

---

## Challenge 9: Google Calendar freebusy doesn't handle time zones correctly

**Assumption:** The `get_available_slots` implementation correctly converts between UTC (Google Calendar API), Europe/Berlin (business rules), and whatever the visitor's local time zone is.

**Most likely failure mode:** A meeting is booked for "Tuesday at 10am" that the agent describes in UTC, but the visitor is in a different timezone and expects their local time. Or the business hours 7:00–15:00 are applied in UTC instead of Europe/Berlin.

**Failure consequence:** Meeting booked at the wrong local time for either party.

**Counter-evidence strength:** Strong. Timezone bugs are endemic in calendar apps and almost always appear in edge cases (DST transitions, late-night slots near midnight).

**Mitigation:**
- All business rule enforcement happens in `Europe/Berlin` (pytz or zoneinfo).
- All Google Calendar API calls use UTC (ISO 8601 with Z suffix).
- When presenting slots to the visitor, the agent states the time and asks: "What timezone are you in?" if the visitor hasn't volunteered it, then confirms both timezones: "Tuesday June 3rd at 10am Berlin time (that's 9am London time)."
- Test explicitly with DST transitions.

---

## Challenge 10: Cloud Run WebSocket cold start breaks the session before it starts

**Assumption:** When a visitor opens an invite link and clicks Start, the backend WebSocket connection succeeds in under 3 seconds.

**Most likely failure mode:** Cloud Run has scaled to zero (no active instances). Python cold start + Gemini Live session negotiation takes 3–8 seconds. Browser WebSocket timeout fires before the session is established. Visitor sees a connection error on first try.

**Failure consequence:** Confusing first impression. Visitor may think the link is broken.

**Counter-evidence strength:** Medium. This is a real problem for sporadic workloads on Cloud Run.

**Mitigation:** Set `min_instances = 1` on the Cloud Run service. At ~$15/month, this eliminates cold starts entirely for a service that should always be ready for a visitor who just received a link. Alternatively: show a "Connecting..." state in the PWA with a generous 10-second timeout and auto-retry.

---

## Challenge 11: Visitor provides wrong phone number

**Assumption:** If the visitor chooses to provide a phone number, the captured number is correct and usable.

**Most likely failure mode:** Visitor speaks a long number in one utterance, pauses in unexpected places, includes a country code or leading zero, and the system drops or transposes digits.

**Failure consequence:** Christian has a wrong callback number, or the visitor thinks the number was captured when it was not.

**Counter-evidence strength:** Strong. Spoken digit strings are easier than email addresses, but long numbers are still error-prone.

**Mitigation:** Phone number is optional. If provided, collect it in chunks, preserve country codes and leading zeros, and read it back using grouped pacing before storing it. Only store after explicit confirmation. If confirmation fails repeatedly, continue booking without a phone number.

---

## Challenge 12: Rescheduling modifies the wrong calendar event

**Assumption:** The `reschedule_meeting` tool updates the correct event by `event_id`. The agent never guesses an event_id — it only uses IDs returned by `find_meeting_at`.

**Most likely failure mode:** A coding bug in `find_meeting_at` returns the wrong `event_id` (e.g., returns the event immediately after the target rather than the target itself).

**Failure consequence:** A different meeting gets rescheduled. Could disrupt a third party who is a guest on that event.

**Counter-evidence strength:** Medium. This is a correctness requirement, not a design flaw — but it will happen if not explicitly tested.

**Mitigation:** `find_meeting_at` must be unit-tested with overlapping events, recurring events, and all-day events. Before calling `reschedule_meeting`, the agent must verbally confirm: "I found your meeting at [exact time]. I'll move it to [new time]. Shall I confirm?" — requiring an explicit yes before modifying.

---

## Top 3 Risks

1. **Calendar content leakage via social engineering.** The system prompt cannot be the only privacy protection. Tool design (freebusy API, no-title responses) is the real defense.
2. **Invite link forwarding.** Multi-use links are easy to forward. Christian needs to know this is a trade-off, not a bug.
3. **Timezone / business hours misapplication.** The correctness of slot suggestions depends entirely on timezone handling. One DST edge case can book meetings at wrong times.

## Critical Assumption

**If the freebusy API doesn't cover all of Christian's calendars (e.g., a shared work calendar he's subscribed to), availability will appear falsely open.** This must be validated with a real `calendarList.list` call against Christian's account before any demo.
