const test = require("node:test");
const assert = require("node:assert/strict");
const {
  shortcutRequest,
  classifyNetworkError,
  classifyResponse,
  shouldRetryNetworkError,
  shouldRetryResponse,
  retryDelaySeconds,
} = require("../build-payload");

test("builds the minimal HTTP(S) shortcut request", () => {
  const message = { Sender: "Relay Test", Content: "Your relay access code is 482731" };
  assert.deepEqual(shortcutRequest("https://relay.test/", "token", message), {
    url: "https://relay.test/api/v1/ingest/shortcut",
    headers: { Authorization: "Bearer token", "Content-Type": "application/json" },
    body: { message },
  });
});

test("accepts local HTTP and rejects malformed shortcut configuration", () => {
  assert.equal(shortcutRequest("http://192.168.5.7:8000/", "token", "message").url, "http://192.168.5.7:8000/api/v1/ingest/shortcut");
  assert.throws(() => shortcutRequest("relay.test", "token", "message"), /HTTP\(S\) origin/);
  assert.throws(() => shortcutRequest("https://relay.test/path?x=1", "token", "message"), /HTTP\(S\) origin/);
  assert.throws(() => shortcutRequest("https://relay.test", "NOT_ENROLLED", "message"), /not enrolled/);
  assert.throws(() => shortcutRequest("https://relay.test", "token", "  "), /message is required/);
});

test("passes a device-generated ISO timestamp without adding protocol fields", () => {
  const request = shortcutRequest(
    "https://relay.test",
    "token",
    "message",
    "2026-09-02T06:42:29+08:00",
  );

  assert.deepEqual(request.body, {
    message: "message",
    received_at: "2026-09-01T22:42:29.000Z",
  });
});

test("classifies server responses without exposing message bodies", () => {
  assert.deepEqual(classifyResponse(201, { accepted: true }), { kind: "accepted", text: "已同步" });
  assert.deepEqual(classifyResponse(200, { duplicate: true }), { kind: "duplicate", text: "已同步（重复触发，未重复保存）" });
  assert.deepEqual(classifyResponse(401, { error: { code: "invalid_device_token" } }), { kind: "auth", text: "设备凭据无效，请更新快捷指令中的 Token" });
  assert.deepEqual(classifyResponse(413, { error: { code: "request_too_large" } }), { kind: "request", text: "短信内容过大，未上传" });
  assert.deepEqual(classifyResponse(422, { error: { code: "invalid_message" } }), { kind: "payload", text: "短信字段无效，未上传" });
  assert.deepEqual(classifyResponse(503, { status: "unavailable" }), { kind: "server", text: "接收平台暂不可用，请稍后重试" });
  assert.deepEqual(classifyResponse(500, {}), { kind: "server", text: "接收平台返回错误，请稍后重试" });
});

test("classifies network failures without echoing sensitive details", () => {
  assert.deepEqual(classifyNetworkError(new Error("network timeout with Bearer secret")), { kind: "network", text: "网络或 HTTP/HTTPS 连接失败，请检查网络后重试" });
});

test("allows one retry only for transient shortcut failures", () => {
  for (const status of [408, 425, 429, 500, 502, 503, 599]) {
    assert.equal(shouldRetryResponse(status, {}, 0), true, `status ${status}`);
  }
  assert.equal(shouldRetryNetworkError(0), true);
  assert.equal(retryDelaySeconds(0), 2);

  for (const status of [200, 201, 400, 401, 409, 413, 422]) {
    assert.equal(shouldRetryResponse(status, {}, 0), false, `status ${status}`);
  }
  assert.equal(shouldRetryResponse(503, {}, 1), false);
  assert.equal(shouldRetryResponse(400, { error: { code: "invalid_message" } }, 0), false);
  assert.equal(shouldRetryNetworkError(1), false);
  assert.equal(retryDelaySeconds(1), 0);
});
