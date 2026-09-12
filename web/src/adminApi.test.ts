import { afterEach, expect, test, vi } from "vitest";

import { adminRequest, adminWebSocketUrl, messageCursorQuery } from "./adminApi";


afterEach(() => {
  vi.restoreAllMocks();
});


test("adminRequest sends the administrator token and parses JSON", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })),
  );

  await expect(adminRequest<{ ok: boolean }>("/api/v1/admin/runtime", "session-token"))
    .resolves.toEqual({ ok: true });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/runtime",
    expect.objectContaining({
      headers: expect.objectContaining({ "X-Admin-Token": "session-token" }),
    }),
  );
});


test("adminRequest exposes a stable backend error code", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ error: { code: "admin_auth_required" } }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })),
  );

  await expect(adminRequest("/api/v1/admin/runtime", "expired-token"))
    .rejects.toThrow("admin_auth_required");
});


test("adminWebSocketUrl converts HTTP origins to WS schemes", () => {
  expect(adminWebSocketUrl("https://sms.test", "/api/v1/ws/admin/messages"))
    .toBe("wss://sms.test/api/v1/ws/admin/messages");
  expect(adminWebSocketUrl("http://127.0.0.1:39300/", "/api/v1/ws/admin/messages"))
    .toBe("ws://127.0.0.1:39300/api/v1/ws/admin/messages");
});


test("messageCursorQuery encodes filters and a stable recovery cursor", () => {
  expect(messageCursorQuery(
    { createdAt: "2026-09-01T01:02:03+00:00", id: "message-id" },
    { deviceId: "device-id", simSlot: 1, limit: 500 },
  )).toBe(
    "?device_id=device-id&sim_slot=1&limit=500&created_after=2026-09-01T01%3A02%3A03%2B00%3A00&after_id=message-id",
  );
});
