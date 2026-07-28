# Speech Center API (Phase 4.5.1)

Host applications use these endpoints to embed Speech Center experiences
(CRM records, meetings, call-center interactions) without a Turing frontend.


## Capability

All endpoints require authentication and ``view_transcript``.

Responses are organization-scoped via the caller's membership (optional
``organization_id`` query param on lookup).


## Lookup by host object

```http
GET /api/turing/v1/speech-center/?external_system=salesforce&external_type=call&external_id=SF-CALL-1
```

Required query parameters:

| Param | Example |
|-------|---------|
| `external_system` | `salesforce`, `zoom`, `twilio`, `teams` |
| `external_type` | `call`, `meeting` |
| `external_id` | Host / connector object id |

Response:

```json
{
  "media": { "id": "...", "use_case": "crm_call", "original_filename": "..." },
  "transcript": { "id": "...", "status": "draft", "full_text": "..." },
  "status": "draft",
  "speakers": [{ "id": "...", "label": "S1", "display_name": "" }],
  "analyses": {
    "summary": { "id": "...", "content": { "summary": "...", "main_points": [] }, "provider": "fake" },
    "topics": { "id": "...", "content": ["renewal"], "provider": "fake" },
    "action_items": { "id": "...", "content": [{ "task": "..." }], "provider": "fake" }
  },
  "external_references": [
    {
      "external_system": "salesforce",
      "external_type": "call",
      "external_id": "SF-CALL-1"
    }
  ]
}
```

Notes:

- Prefer transcript-linked external references; fall back to media + primary transcript.
- If media exists but STT has not produced a transcript yet, ``transcript`` /
  ``status`` / ``speakers`` are empty/null and ``analyses.*`` are ``null``
  (hosts can poll).
- Unknown host keys → ``404``.


## Timeline

```http
GET /api/turing/v1/speech-center/{transcript_id}/timeline/
```

```json
{
  "transcript_id": "...",
  "status": "draft",
  "speakers": [...],
  "segments": [
    {
      "id": "...",
      "sequence": 0,
      "speaker": "...",
      "start_ms": 0,
      "end_ms": 2500,
      "text": "Discuss renewal and follow up."
    }
  ],
  "timestamps": {
    "start_ms": 0,
    "end_ms": 2500,
    "segment_count": 1
  },
  "analysis_references": [
    { "id": "...", "analysis_type": "summary", "provider": "fake" }
  ]
}
```


## Host usage patterns

### CRM

1. Attach ``ExternalReference(salesforce/call/<Id>)`` when ingesting (connector or host).
2. Open the CRM record → call Speech Center lookup with that key.
3. Render summary / topics / action items from ``analyses``.
4. Open the player/timeline via ``…/speech-center/{transcript_id}/timeline/``.

### Meetings

1. Zoom / Teams / Google Meet connectors create ``ExternalReference(<vendor>/meeting/<id>)``.
2. Meeting host UI looks up by vendor meeting id.
3. Use ``status`` to gate review / approve workflows already exposed on transcripts.

### Call center

1. Twilio (or other ``TelephonyConnector``) links ``ExternalReference(twilio/call/<CallSid>)``.
2. Agent or supervisor UI loads Speech Center by Call SID.
3. Timeline segments + speakers support QA review; analysis refs point at latest intelligence rows.


## Service layer

``SpeechCenterService`` (``turing/services/speech_center.py``):

| Method | Purpose |
|--------|---------|
| `get_by_external_reference(...)` | Host key → unified context |
| `get_transcript_context(transcript)` | Build media/transcript/speakers/analyses payload |
| `get_available_intelligence(transcript)` | Latest summary / topics / action_items |
| `get_timeline(transcript)` | Segments, speakers, timestamps, analysis refs |


## Out of scope (later Phase 4.5+)

- Frontend Speech Center UI
- Vector / semantic search
- Sentiment analysis
- New connectors
