export type AdminDevice = {
  id: string;
  name: string;
  client_type: "android_app" | "root_module" | "ios_shortcut" | "ve_tweak" | "air724" | "air724_mqtt";
  auth_kind: "hmac" | "bearer" | "script_api";
  active: boolean;
  online?: boolean;
  last_seen_at: string | null;
  client_instance_id?: string | null;
  client_version?: string | null;
  client_mode?: string | null;
  runtime_updated_at?: string | null;
  credential_reuse_warning?: boolean;
  duplicate_collection_warning?: boolean;
};

export type AdminMessage = {
  id: string;
  device_id: string;
  client_message_id: string;
  received_at: string;
  created_at?: string;
  message_date?: string | null;
  sender: string;
  body: string | null;
  source: string;
  sim_slot: number | null;
  subscription_id: number | null;
  receiving_phone_masked: string | null;
  receiving_identity_status: "resolved" | "unresolved" | "conflict";
  otp_status: "available" | "claimed" | "expired" | null;
  otp_code?: string | null;
  read_at: string | null;
  metadata: Record<string, unknown>;
  redacted: boolean;
};

export type LatestRelease = {
  version_code: number;
  version_name: string;
  published_at: string;
  download_url: string;
  sha256: string;
};

export type RuntimeInfo = {
  database_backend: string;
  database_fingerprint: string;
  platform_version: string;
  // 发布清单里各平台 stable 的最新一条；清单不可读时后端返回空对象。
  latest_releases?: Record<string, LatestRelease>;
  // 证书由上游反代终结，容器内不持有证书，后端恒返回 null。
  certificate_expires_at?: string | null;
  public_base_url: string;
  relay_site: string;
  http_port: number;
  https_port: number;
  app_port: number;
  https_enabled: boolean;
  services: Record<string, string>;
  server_time: string;
};

export type ReleaseEntry = {
  platform: "android" | "manager" | "root";
  channel: string;
  version_code: number;
  version_name: string;
  published_at: string;
  min_sdk: number;
  mandatory: boolean;
  notes: string;
  size_bytes: number;
  sha256: string;
  signer_sha256: string | null;
  download_url: string;
};

export type MessageCursor = {
  createdAt: string;
  id: string;
};

export type MessageQueryFilters = {
  deviceId?: string;
  simSlot?: number | null;
  limit?: number;
};

export async function adminRequest<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body) headers["Content-Type"] = "application/json";
  new Headers(init.headers).forEach((value, key) => {
    headers[key] = value;
  });
  headers["X-Admin-Token"] = token;
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let code = "http_" + response.status;
    try {
      const payload = (await response.json()) as { error?: { code?: string } };
      code = payload.error?.code ?? code;
    } catch {
      code = code;
    }
    throw new Error(code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function adminWebSocketUrl(baseUrl: string, path: string): string {
  const origin = new URL(baseUrl || window.location.origin, window.location.origin);
  origin.protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  origin.pathname = path.startsWith("/") ? path : "/" + path;
  origin.search = "";
  origin.hash = "";
  return origin.toString();
}

export function messageCursorQuery(
  cursor: MessageCursor | null,
  filters: MessageQueryFilters = {},
): string {
  const params = new URLSearchParams();
  if (filters.deviceId) params.set("device_id", filters.deviceId);
  if (filters.simSlot !== null && filters.simSlot !== undefined) {
    params.set("sim_slot", String(filters.simSlot));
  }
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (cursor) {
    params.set("created_after", cursor.createdAt);
    params.set("after_id", cursor.id);
  }
  const query = params.toString();
  return query ? "?" + query : "";
}

export type ForwardingChannel = { id: string; label: string };

export type ForwardingRule = {
  id: string;
  name: string;
  enabled: boolean;
  match_device_id: string | null;
  match_phone_masked: string;
  match_sender: string;
  match_keyword: string;
  channel: string;
  target_masked: string;
  template: string;
  created_at: string;
  updated_at: string;
};

export type ForwardingLog = {
  id: number;
  rule_id: string | null;
  rule_name: string;
  message_id: string | null;
  channel: string;
  target_masked: string;
  status: string;
  attempts: number;
  error: string | null;
  created_at: string;
};

export type ForwardingRuleSpec = {
  name: string;
  enabled: boolean;
  channel: string;
  target: string;
  match_device_id?: string;
  match_phone?: string;
  match_sender?: string;
  match_keyword?: string;
  template?: string;
};

export function listForwardingRules(token: string): Promise<{ channels: ForwardingChannel[]; rules: ForwardingRule[] }> {
  return adminRequest("/api/v1/admin/forwarding/rules", token);
}

export function createForwardingRule(token: string, spec: ForwardingRuleSpec): Promise<{ rule: ForwardingRule }> {
  return adminRequest("/api/v1/admin/forwarding/rules", token, { method: "POST", body: JSON.stringify(spec) });
}

export function updateForwardingRule(token: string, ruleId: string, spec: Partial<ForwardingRuleSpec>): Promise<{ rule: ForwardingRule }> {
  return adminRequest(`/api/v1/admin/forwarding/rules/${encodeURIComponent(ruleId)}`, token, {
    method: "PUT",
    body: JSON.stringify(spec),
  });
}

export function deleteForwardingRule(token: string, ruleId: string): Promise<{ deleted: boolean }> {
  return adminRequest(`/api/v1/admin/forwarding/rules/${encodeURIComponent(ruleId)}`, token, { method: "DELETE" });
}

export function testForwardingRule(token: string, ruleId: string): Promise<{ ok: boolean; message: string; content?: string }> {
  return adminRequest(`/api/v1/admin/forwarding/rules/${encodeURIComponent(ruleId)}/test`, token, { method: "POST" });
}

export function listForwardingLogs(token: string, limit = 50): Promise<{ logs: ForwardingLog[] }> {
  return adminRequest(`/api/v1/admin/forwarding/logs?limit=${limit}`, token);
}

// ---------- 远程控制 v1：设备指令（查电量 / 查最近短信 / 触发立即上传） ----------

export type DeviceCommandType = "query_battery" | "query_recent_sms" | "trigger_upload";

export type DeviceCommand = {
  id: string;
  device_id: string;
  command_type: DeviceCommandType | string;
  status: "pending" | "sent" | "completed" | "expired";
  result: Record<string, unknown> | null;
  created_at: string;
  sent_at: string | null;
  completed_at: string | null;
};

export const DEVICE_COMMAND_LABELS: Record<string, string> = {
  query_battery: "查询电量",
  query_recent_sms: "查询最近短信",
  trigger_upload: "触发立即上传",
};

export function issueDeviceCommand(
  token: string,
  deviceId: string,
  commandType: DeviceCommandType,
): Promise<DeviceCommand> {
  return adminRequest(`/api/v1/admin/devices/${encodeURIComponent(deviceId)}/commands`, token, {
    method: "POST",
    body: JSON.stringify({ command_type: commandType }),
  });
}

export function listDeviceCommands(
  token: string,
  deviceId: string,
  limit = 30,
): Promise<DeviceCommand[]> {
  return adminRequest(
    `/api/v1/admin/devices/${encodeURIComponent(deviceId)}/commands?limit=${limit}`,
    token,
  );
}

/** 把指令结果渲染为一行摘要文本（未完成/无结果时给出状态说明）。 */
export function describeCommandResult(command: DeviceCommand): string {
  if (command.status === "completed" && command.result) {
    const result = command.result;
    if (command.command_type === "query_battery") {
      const percent = result.battery_percent;
      const charging = result.charging;
      return `电量 ${percent ?? "未知"}%${charging === true ? " · 充电中" : charging === false ? " · 未充电" : ""}`;
    }
    if (command.command_type === "query_recent_sms") {
      const items = Array.isArray(result.messages) ? result.messages.length : 0;
      return `设备返回 ${items} 条短信`;
    }
    if (command.command_type === "trigger_upload") {
      const queued = result.queued;
      return typeof queued === "number" ? `已触发上报，队列 ${queued} 条` : "已触发上报";
    }
    return JSON.stringify(result);
  }
  if (command.status === "completed") return "已完成（无结果数据）";
  if (command.status === "expired") return "设备未领取或回传超时，指令已过期";
  if (command.status === "sent") return "设备已领取，等待回传";
  return "等待设备轮询";
}
