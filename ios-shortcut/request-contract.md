# iOS Shortcut Request Contract

The recommended Shortcut sends an HTTP or HTTPS JSON `POST` to `/api/v1/ingest/shortcut`. Local or private-IP testing may use HTTP; production public access should use HTTPS.

Required headers:

- `Authorization: Bearer <device credential>`
- `Content-Type: application/json`

The request body contains the message and may contain one device-generated timestamp:

```json
{
  "message": "<Shortcut Input>",
  "received_at": "2026-09-01T22:42:29.000Z"
}
```

`message` may arrive as an iOS Message object or as its plain-text representation. For an object, the server reads `Sender`, `Content`, and `Date` when present. The device-provided `Date` is normalized and exposed as `message_date`; the optional top-level `received_at` is the iPhone-generated request time. If the iOS version also includes `PhoneNumber`/`phone_number`, the server uses it as optional receiving-line metadata. For plain text, the server stores the text and uses `iPhone Messages` as the sender label.

The receiving-line field is optional: missing iOS `Subscription` metadata never blocks message ingestion. If the iPhone has multiple bound SIM phone numbers and the input contains neither a receiving-line field nor a usable slot, the message is stored with an unresolved/conflict identity and cannot be delivered to a phone-specific WebSocket subscription until the device SIM mapping is corrected.

The Shortcut should create `received_at` from the iPhone “Current Date” action formatted as ISO 8601 UTC. If that optional field is absent or invalid, the server falls back to its own receive time. The server generates `schema_version`, `client_message_id`, `source`, metadata, and the internal idempotency key. The Shortcut may retry the exact same request once after a transient network/5xx failure; the stable idempotency key makes that retry safe. The legacy `/api/v1/ingest/messages` endpoint remains available for clients that already build the complete protocol payload.

Responses:

- `201`: accepted and stored.
- `200` with `duplicate: true`: an identical stable message input was already stored.
- `401 invalid_device_token`: token is invalid, rotated, or revoked.
- `413 request_too_large`: request exceeds the ingest limit.
- `422 invalid_message`: missing `message`, empty content, or unsupported value.

The device credential must not appear in the JSON body or URL.
