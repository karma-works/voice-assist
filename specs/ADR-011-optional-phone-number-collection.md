# ADR-011: Optional Phone Number Collection

**Status:** Decided  
**Date:** 2026-06-02

## Context

The previous booking flow collected the visitor's email address so Google Calendar could send an automatic guest invite. The new requirement is to collect a phone number instead of an email address, and the phone number must be optional.

Spoken phone numbers are easier than emails because they mostly use digits, but they are still error-prone. Users pause mid-number, include country codes, say leading zeros, and use local grouping conventions.

## Decision

Replace required email collection with optional phone-number collection.

The agent must ask whether the visitor wants to provide a phone number. If the visitor declines, booking continues without a phone number. If the visitor accepts, the agent collects and verifies the number using chunked capture.

## Procedure

### Prompt

The agent asks:

```text
Would you like to add a phone number for Christian? It's optional.
```

If yes, the agent sets expectations:

```text
Please say the number slowly, starting with the country code if you want to include one. You can pause after each group.
```

For a local German-first session, the prompt may say:

```text
Please say the number slowly. Include the country code if it is not a German number, and include any leading zero.
```

### Capture

- Do not require the full number in one utterance.
- Accept natural pauses after three or four digits.
- Allow country-code prefixes such as `+49`.
- Preserve leading zeros.
- Ignore spoken separators such as "space", "dash", or short pauses.
- If confidence is low for a chunk, ask only for that chunk again.

### Verification

Read the captured number back using natural grouping:

- Ten-digit local example: `123 456 7890` as "one two three ... four five six ... seven eight nine zero".
- German/mobile/international numbers: group by country code, prefix, then two-to-four digit chunks.
- Never read the number as one continuous string.

Ask:

```text
I heard: [grouped number]. Is that right?
```

Only store the number after explicit confirmation.

## Data Model

Booking state should store:

```json
{
  "visitor_name": "string",
  "visitor_phone": "string | null",
  "visitor_phone_confirmed": "boolean",
  "phone_collection_declined": "boolean"
}
```

Google Calendar event creation should not add the visitor as a guest unless a separate confirmed email channel is reintroduced. Store the optional phone number in a private server-side booking record if one exists. If the MVP has no booking store yet, include the confirmed phone number in the event description only if Christian accepts that visibility in his own calendar.

## Product Consequence

Removing email means the MVP loses automatic visitor email invites from Google Calendar. The assistant must provide an in-session spoken confirmation. Future visitor-side confirmation should be decided separately, most likely via SMS, WhatsApp, or a confirmation page.

## Rejected Options

- **Require phone number:** Rejected because the requirement says phone number is optional.
- **Collect full number in one prompt:** Rejected because long digit strings produce avoidable ASR and memory errors.
- **Infer phone number without confirmation:** Rejected because a wrong phone number is worse than no phone number.
