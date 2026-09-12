function shortcutRequest(baseUrl, token, message, receivedAt) {
  const normalizedBaseUrl = typeof baseUrl === "string" ? baseUrl.trim().replace(/\/$/, "") : "";
  let parsedUrl;
  try { parsedUrl = new URL(normalizedBaseUrl); } catch { parsedUrl = null; }
  if (!parsedUrl || !["http:", "https:"].includes(parsedUrl.protocol) || !parsedUrl.hostname || parsedUrl.username || parsedUrl.password || parsedUrl.search || parsedUrl.hash) {
    throw new Error("base URL must be an HTTP(S) origin without credentials, query, or fragment");
  }
  if (typeof token !== "string" || !token.trim() || token === "NOT_ENROLLED") throw new Error("Device is not enrolled");
  if (message === undefined || message === null || (typeof message === "string" && !message.trim())) throw new Error("message is required");
  const body = { message };
  if (receivedAt !== undefined) {
    if (typeof receivedAt !== "string" || !receivedAt.trim()) throw new Error("received_at must be a date string");
    const parsedReceivedAt = new Date(receivedAt);
    if (Number.isNaN(parsedReceivedAt.getTime())) throw new Error("received_at must be a valid date");
    body.received_at = parsedReceivedAt.toISOString();
  }
  return {
    url: `${normalizedBaseUrl}/api/v1/ingest/shortcut`,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body,
  };
}
function classifyResponse(status, body) {
  const code = body && body.error && typeof body.error.code === "string" ? body.error.code : "";
  if (status === 201) return { kind: "accepted", text: "已同步" };
  if (status === 200 && body && body.duplicate === true) return { kind: "duplicate", text: "已同步（重复触发，未重复保存）" };
  if (status === 401 || code === "invalid_device_token") return { kind: "auth", text: "设备凭据无效，请更新快捷指令中的 Token" };
  if (status === 413 || code === "request_too_large") return { kind: "request", text: "短信内容过大，未上传" };
  if (status === 422 || code === "invalid_message") return { kind: "payload", text: "短信字段无效，未上传" };
  return { kind: "server", text: status === 503 ? "接收平台暂不可用，请稍后重试" : "接收平台返回错误，请稍后重试" };
}
function classifyNetworkError() {
  return { kind: "network", text: "网络或 HTTP/HTTPS 连接失败，请检查网络后重试" };
}

function shouldRetryNetworkError(attempt = 0) {
  return Number.isInteger(attempt) && attempt < 1;
}

function shouldRetryResponse(status, body, attempt = 0) {
  if (!shouldRetryNetworkError(attempt)) return false;
  const code = body && body.error && typeof body.error.code === "string" ? body.error.code : "";
  if (["invalid_device_token", "request_too_large", "invalid_message", "idempotency_conflict"].includes(code)) {
    return false;
  }
  if (status === 408 || status === 425 || status === 429) return true;
  return Number.isInteger(status) && status >= 500 && status <= 599;
}

function retryDelaySeconds(attempt = 0) {
  return attempt === 0 ? 2 : 0;
}

module.exports = {
  shortcutRequest,
  classifyNetworkError,
  classifyResponse,
  shouldRetryNetworkError,
  shouldRetryResponse,
  retryDelaySeconds,
};
