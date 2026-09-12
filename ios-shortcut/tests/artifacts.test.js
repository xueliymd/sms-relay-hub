const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");

test("example configuration is disabled and secret-free", () => {
  const raw = fs.readFileSync(path.join(root, "config.example.json"), "utf8");
  const config = JSON.parse(raw);
  assert.equal(config.base_url, "https://relay.invalid");
  assert.equal(config.device_token, "NOT_ENROLLED");
  assert.deepEqual(Object.keys(config).sort(), ["base_url", "device_token"]);
  assert.doesNotMatch(raw, /Bearer\s+[A-Za-z0-9_-]{24,}/);
});

test("manual definition contains the simplified shortcut request", () => {
  const definition = fs.readFileSync(path.join(root, "shortcut-definition.md"), "utf8");
  for (const marker of ["/api/v1/ingest/shortcut", "Authorization", "message", "立即运行"]) {
    assert.match(definition, new RegExp(marker));
  }
  assert.match(definition, /手机端不要再创建[\s\S]*X-Idempotency-Key/);
  assert.match(definition, /手机端不要再创建[\s\S]*SHA-256/);
});

test("manual definition passes the complete message input without manual field extraction", () => {
  const definition = fs.readFileSync(path.join(root, "shortcut-definition.md"), "utf8");
  for (const marker of [
    "输入快捷指令的信息",
    "整条信息",
    "服务端自动",
    "sender",
    "body",
    "client_message_id",
  ]) {
    assert.match(definition, new RegExp(marker));
  }
  assert.match(definition, /手机端不要再创建[\s\S]*创建名为[“\"]快捷指令名称[”\"]/);
});
