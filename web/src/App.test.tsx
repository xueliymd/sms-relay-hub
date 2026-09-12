import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

const device = {
  id: "device-1",
  name: "Authorized Android",
  client_type: "android_app",
  auth_kind: "hmac",
  active: true,
  last_seen_at: null,
};

const revokedDevice = {
  ...device,
  id: "device-2",
  name: "Revoked iPhone",
  client_type: "ios_shortcut",
  auth_kind: "bearer",
  active: false,
};

const air724Device = {
  ...device,
  id: "device-air724",
  name: "合宙 724 取码器",
  client_type: "air724",
  auth_kind: "script_api",
};

const rootDevice = {
  ...device,
  id: "device-root",
  name: "Authorized Root Module",
  client_type: "root_module",
  client_mode: "root_ingest",
};

const simProfile = {
  device_id: "device-1",
  slot_index: 0,
  subscription_id: 1042,
  phone_number_masked: "******1234",
  carrier_name: "Neutral Carrier",
  source: "auto",
  updated_at: "2026-08-17T00:00:00+00:00",
};

const secondSimProfile = {
  ...simProfile,
  slot_index: 1,
  subscription_id: 1043,
  phone_number_masked: null,
  carrier_name: "Second Carrier",
};

const resolvedMessage = {
  id: "message-1",
  device_id: "device-1",
  client_message_id: "root:provider:1:1",
  received_at: "2026-08-18T00:00:00+00:00",
  sender: "Target Service",
  body: "Your verification code is 482731",
  source: "root_module",
  sim_slot: 0,
  subscription_id: 1042,
  receiving_phone_masked: "********0005",
  receiving_identity_status: "resolved",
  otp_status: "available",
  read_at: null,
  metadata: { subscription_id: 1042 },
  redacted: false,
};

const unresolvedMessage = {
  ...resolvedMessage,
  id: "message-2",
  client_message_id: "root:provider:2:2",
  receiving_phone_masked: null,
  receiving_identity_status: "unresolved",
  otp_status: null,
};

function jsonResponse(value: unknown, ok = true) {
  return { ok, json: async () => value } as Response;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve;
  });
  return { promise, resolve };
}

beforeEach(() => {
  sessionStorage.clear();
  class SilentWebSocket {
    static OPEN = 1;
    readyState = SilentWebSocket.OPEN;
    onopen: ((event: Event) => void) | null = null;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onclose: ((event: CloseEvent) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    send() {}
    close() {}
  }
  vi.stubGlobal("WebSocket", SilentWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/admin/login") && init?.method === "POST") {
        return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
      }
      if (url.endsWith("/admin/account") && init?.method === "PUT") {
        return jsonResponse({ token: "rotated-admin-session", username: "relay-admin", expires_at: "2026-08-22T00:00:00Z" });
      }
      if (url.endsWith("/devices") && init?.method === "POST") {
        const payload = JSON.parse(String(init.body)) as { client_type?: string };
        if (payload.client_type === "air724") {
          return jsonResponse({
            device: air724Device,
            credential: { type: "script_api", secret: "srh_api_air724-one-time-token" },
          });
        }
        return jsonResponse({
          device,
          credential: { type: "hmac", secret: "one-time-neutral-secret" },
        });
      }
      if (url.endsWith("/restore") && init?.method === "POST") {
        return jsonResponse({
          device: { ...revokedDevice, active: true },
          credential: { type: "bearer", secret: "restored-neutral-secret" },
        });
      }
      if (url.endsWith("/api-clients") && init?.method === "POST") {
        return jsonResponse({ token: "srh_api_neutral-script-token" });
      }
      if (url.endsWith("/api-clients") && !init?.method) {
        return jsonResponse({
          clients: [{ id: "api-client-1", token: "srh_api_neutral-script-token", active: true, created_at: "2026-08-19T00:00:00Z" }],
        });
      }
      if (url.endsWith("/enrollment-qr") && init?.method === "POST") {
        const responseDevice = url.includes("/devices/device-root/") ? rootDevice : device;
        return jsonResponse({
          device: { ...responseDevice },
          qr_text: JSON.stringify({
            v: 1,
            type: "sms-relay-enroll",
            url: "https://relay.example.com",
            device_id: device.id,
            token: "a".repeat(43),
            expires_at: "2026-09-02T12:05:00Z",
          }),
          expires_at: "2026-09-02T12:05:00Z",
          ttl_seconds: 300,
        });
      }
      if (url.endsWith("/purge") && init?.method === "DELETE") {
        return { ok: true, status: 204, json: async () => undefined } as Response;
      }
      if (url.endsWith("/devices/device-1/sim-profiles") && !init?.method) {
        return jsonResponse([simProfile]);
      }
      if (url.includes("/devices/device-1/sim-profiles/") && init?.method === "PUT") {
        const slot = Number(url.split("/").at(-1));
        return jsonResponse({ ...simProfile, slot_index: slot });
      }
      if (url.includes("/devices/device-1/sim-profiles/") && init?.method === "DELETE") {
        return { ok: true, status: 204, json: async () => undefined } as Response;
      }
      if (url.endsWith("/admin/runtime")) {
        return jsonResponse({
          database_backend: "sqlite",
          database_fingerprint: "db-test",
          platform_version: "1.0.11",
          public_base_url: "https://relay.example.com",
          relay_site: "relay.example.com",
          http_port: 80,
          https_port: 443,
          app_port: 39300,
          https_enabled: true,
          services: { api: "ok", database: "ok" },
          server_time: "2026-09-01T08:00:00Z",
        });
      }
      if (url.includes("/releases/android/history")) {
        return jsonResponse({ releases: [{
          platform: "android",
          channel: "stable",
          version_code: 12,
          version_name: "1.0.11",
          published_at: "2026-09-01T05:12:06Z",
          min_sdk: 26,
          mandatory: false,
          notes: "Android 在线更新验证版",
          size_bytes: 2211177,
          sha256: "android-sha256",
          signer_sha256: "android-signer",
          download_url: "/assets/releases/android/12/Relay-SMS-Forwarder-1.0.11.apk",
        }] });
      }
      if (url.includes("/releases/root/history")) return jsonResponse({ releases: [{
        platform: "root",
        channel: "stable",
        version_code: 22,
        version_name: "0.3.10",
        published_at: "2026-09-05T06:04:26Z",
        min_sdk: 21,
        mandatory: false,
        notes: "Root 模块 0.3.10",
        size_bytes: 9206923,
        sha256: "root-sha256",
        signer_sha256: null,
        download_url: "/assets/releases/root/22/sms-relay-root-module-0.3.10.zip",
      }] });
      if (url.includes("/releases/manager/history")) return jsonResponse({ releases: [{
        platform: "manager",
        channel: "stable",
        version_code: 1,
        version_name: "1.0.0",
        published_at: "2026-09-01T10:30:00Z",
        min_sdk: 26,
        mandatory: false,
        notes: "原生管理端",
        size_bytes: 4200000,
        sha256: "manager-sha256",
        signer_sha256: "manager-signer",
        download_url: "/assets/releases/manager/1/Relay-Hub-Manager-1.0.0.apk",
      }] });
      if (url.endsWith("/devices")) return jsonResponse([device, rootDevice, revokedDevice]);
      if (url.endsWith("/messages")) return jsonResponse([]);
      if (url.endsWith("/otps")) return jsonResponse([]);
      return jsonResponse({});
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

test("connects to the administration API and renders operational views", async () => {
  render(<App />);

  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByText("Authorized Android")).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/releases/deploy/history"))).toBe(false);
  expect(screen.getByRole("tab", { name: "设备" })).toBeInTheDocument();
  expect(sessionStorage.getItem("relay-admin-token")).toBe("signed-admin-session");
  expect(screen.queryByLabelText("管理令牌")).not.toBeInTheDocument();
});

test("shows shared runtime monitoring, live messages, and release history", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("tab", { name: "总览" }));
  expect(await screen.findByRole("heading", { name: "平台运行信息" })).toBeInTheDocument();
  expect(screen.getByText("平台版本 1.0.11")).toBeInTheDocument();
  expect(screen.getByText("Android 1.0.11")).toBeInTheDocument();
  expect(screen.getByText("管理端 1.0.0")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "消息" }));
  expect(await screen.findByRole("heading", { name: "设备实时短信" })).toBeInTheDocument();
  expect(screen.getByLabelText("选择设备")).toHaveValue("");
  expect(screen.getByRole("option", { name: "请选择设备" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("选择设备"), { target: { value: "device-1" } });
  expect(screen.getByLabelText("选择设备")).toHaveValue("device-1");
  fireEvent.click(screen.getByRole("tab", { name: "总览" }));
  fireEvent.click(screen.getByRole("tab", { name: "消息" }));
  expect(screen.getByLabelText("选择设备")).toHaveValue("");
});

test("logs in with the administrator username and password", async () => {
  render(<App />);

  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByText("Authorized Android")).toBeInTheDocument();
  expect(screen.getByText("登录成功")).toBeInTheDocument();
  expect(sessionStorage.getItem("relay-admin-token")).toBe("signed-admin-session");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/login",
    expect.objectContaining({ method: "POST", body: JSON.stringify({ username: "admin", password: "neutral-admin-password-123456" }) }),
  );
});

test("updates administrator credentials and replaces the session token", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "账号设置" }));
  fireEvent.change(screen.getByLabelText("当前密码"), { target: { value: "old-neutral-password" } });
  fireEvent.change(screen.getByLabelText("新用户名（可选）"), { target: { value: "relay-admin" } });
  fireEvent.change(screen.getByLabelText("新密码（可选，至少 12 位）"), { target: { value: "new-neutral-password-123456" } });
  fireEvent.change(screen.getByLabelText("确认新密码"), { target: { value: "new-neutral-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "保存设置" }));

  await waitFor(() => expect(sessionStorage.getItem("relay-admin-token")).toBe("rotated-admin-session"));
  expect(screen.getByText("管理员账号已更新")).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "管理员账号设置" })).not.toBeInTheDocument();
});

test("restores the persistent script token after a page reload", async () => {
  sessionStorage.setItem("relay-admin-token", "signed-admin-session");

  render(<App />);

  expect(await screen.findByText("Authorized Android")).toBeInTheDocument();
  expect(screen.getByText("连接已恢复")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "总览" }));

  expect(await screen.findByText("srh_api_neutral-script-token")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/api-clients",
    expect.objectContaining({ headers: { "X-Admin-Token": "signed-admin-session" } }),
  );
});

test("enrolls a device and presents its credential once", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "添加设备" }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "Authorized Android" } });
  fireEvent.click(screen.getByRole("button", { name: "创建设备" }));

  expect(await screen.findByText("设备鉴权密钥（仅显示一次）")).toBeInTheDocument();
  expect(await screen.findByText("one-time-neutral-secret")).toBeInTheDocument();
  await waitFor(() => expect(fetch).toHaveBeenCalled());
});

test("generates a single-use QR configuration for Android devices", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "生成扫码配置 Authorized Android" }));

  expect(await screen.findByRole("dialog", { name: "扫码配置设备" })).toBeInTheDocument();
  expect(screen.getByAltText("Authorized Android设备配置二维码")).toBeInTheDocument();
  expect(screen.getByText(/二维码仅有效 5 分钟/)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/devices/device-1/enrollment-qr",
    expect.objectContaining({ method: "POST" }),
  );
});

test("generates a single-use QR configuration for root modules", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Root Module");

  fireEvent.click(screen.getByRole("button", { name: "生成扫码配置 Authorized Root Module" }));

  expect(await screen.findByRole("dialog", { name: "扫码配置设备" })).toBeInTheDocument();
  expect(screen.getByAltText("Authorized Root Module设备配置二维码")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/devices/device-root/enrollment-qr",
    expect.objectContaining({ method: "POST" }),
  );
});

test("distinguishes verification codes from device and script credentials", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  expect(screen.getByRole("tab", { name: "取码记录" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "总览" }));
  expect(await screen.findByText("可领取取码")).toBeInTheDocument();
  expect(screen.getByText("脚本 API 令牌用于外部脚本调用取码接口；不用于设备上报，也不能填入手机模块。"))
    .toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "新建脚本令牌" }));
  expect(await screen.findByText("脚本 API 令牌已创建")).toBeInTheDocument();
  expect(screen.getByText("srh_api_neutral-script-token")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "接口凭据" })).toBeInTheDocument();
  expect(screen.getByText("长期有效，直到你停用或重新创建")).toBeInTheDocument();
  expect(screen.getByText("服务端已加密保存，刷新页面或重启服务后仍可显示。旧令牌若未加密保存，需要重新创建。"))
    .toBeInTheDocument();
  expect(screen.getByText("脚本 API 地址")).toBeInTheDocument();
  const apiContract = screen.getByLabelText("脚本 API 地址和调用方式");
  expect(apiContract).toHaveTextContent(`${window.location.origin}/api/v1/otp/claim.txt?phone_number=%2B8613800138000`);
  expect(apiContract).toHaveTextContent(`${window.location.origin}/api/v1/otp/claim`);
  expect(apiContract).toHaveTextContent(`${window.location.origin.replace(/^http/, "ws")}/api/v1/ws/otp`);
  expect(screen.getByText("Authorization: Bearer srh_api_neutral-script-token")).toBeInTheDocument();
  expect(screen.getByText('POST JSON: {"phone_number":"+8613800138000","sender":"1069","keyword":"验证码"}')).toBeInTheDocument();
});

test("offers every supported client type", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "添加设备" }));

  const clientType = screen.getByLabelText("客户端");
  expect(clientType).toHaveTextContent("Android 应用");
  expect(clientType).toHaveTextContent("Android Root");
  expect(clientType).toHaveTextContent("iPhone 快捷指令");
  expect(clientType).toHaveTextContent("iOS Ve");
  expect(clientType).toHaveTextContent("合宙 724 取码");
  expect(clientType).toHaveTextContent("合宙 724 MQTT 短信上报");
  expect(clientType).not.toHaveTextContent("SIM 卡板");
});

test("opens the Android Root tutorial from the device view", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "演示教程" }));

  expect(await screen.findByRole("heading", { name: "设备接入演示教程" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Android 应用" })).toHaveAttribute("aria-selected", "true");
  // 教程链接跟随发布目录，不再写死版本号（写死会在下次发版后指向旧包）。
  expect(screen.getByRole("link", { name: "下载 Relay短信转发 1.0.11.apk" })).toHaveAttribute("href", "/assets/releases/android/12/Relay-SMS-Forwarder-1.0.11.apk");
  fireEvent.click(screen.getByRole("tab", { name: "Android Root 模块" }));
  expect(screen.getByRole("tab", { name: "Android Root 模块" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("link", { name: "下载 sms-relay-root-module-0.3.10.zip" })).toHaveAttribute("href", "/assets/releases/root/22/sms-relay-root-module-0.3.10.zip");
  expect(screen.getByText((_, element) => element?.tagName === "PRE" && element.textContent?.includes("/data/adb/sms-relay/config.json") === true)).toBeInTheDocument();
  expect(screen.getByText((_, element) => element?.className === "tutorial-warning" && element.textContent?.includes("只转发安装并启用模块后收到的新短信") === true)).toBeInTheDocument();
  expect(screen.getByText("arm64-v8a / armeabi-v7a")).toBeInTheDocument();
});

test("switches the device tutorial to the iPhone Shortcut workflow", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "演示教程" }));
  fireEvent.click(screen.getByRole("tab", { name: "iPhone 快捷指令" }));

  expect(screen.getByRole("tab", { name: "iPhone 快捷指令" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("link", { name: "下载 ios-sms-config.shortcut" })).toHaveAttribute("href", "/assets/downloads/ios-sms-config.shortcut");
  expect(screen.getByRole("tab", { name: "导入现成快捷指令" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getAllByText((_, element) => element?.textContent?.includes("替换第一个“文本”动作中的 Token") === true).length).toBeGreaterThan(0);
  expect(screen.queryByText("Authorization: Bearer <设备Token>")).not.toBeInTheDocument();
  expect(screen.queryByRole("img", { name: "快捷指令请求体参数配置示例" })).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "信息自动化触发配置示例" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "手动创建快捷指令" }));
  expect(screen.getByRole("tab", { name: "手动创建快捷指令" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getAllByText((_, element) => element?.textContent?.includes("请求头添加") === true && element.textContent.includes("Authorization") === true).length).toBeGreaterThan(0);
  expect(screen.getAllByText((_, element) => element?.textContent?.includes("请求体选择 JSON") === true && element.textContent.includes("message") === true).length).toBeGreaterThan(0);
  expect(screen.getAllByText((_, element) => element?.textContent?.includes("方法选择") === true && element.textContent.includes("POST") === true).length).toBeGreaterThan(0);
  expect(screen.getByRole("img", { name: "信息自动化触发配置示例" })).toBeInTheDocument();
});

test("shows Air724 as a managed script consumer instead of an ingest device", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "演示教程" }));
  fireEvent.click(screen.getByRole("tab", { name: "合宙 724 取码" }));

  expect(screen.getByRole("link", { name: "下载 air724-otp-websocket.lua" })).toHaveAttribute(
    "href",
    "/assets/downloads/air724-otp-websocket.lua",
  );
  expect(screen.getByText("在“设备”页添加设备，客户端选择“合宙 724 取码”。")).toBeInTheDocument();
  expect(screen.getAllByText((_, element) => element?.textContent?.includes("断线重连") === true && element.textContent.includes("HTTP Claim") === true).length).toBeGreaterThan(0);
  expect(
    screen.getByText(
      "合宙 Air724 支持两个独立脚本：WebSocket 取码，或 MQTT 短信上报。两者不要用同一个设备记录混配。",
    ),
  ).toBeInTheDocument();
});

test.each([
  ["Android 应用", "android_app", "hmac"],
  ["Android Root", "root_module", "hmac"],
  ["iPhone 快捷指令", "ios_shortcut", "bearer"],
  ["iOS Ve", "ve_tweak", "hmac"],
  ["合宙 724 取码", "air724", "script_api"],
  ["合宙 724 MQTT 短信上报", "air724_mqtt", "hmac"],
])("enrolls %s with its exact client contract", async (label, clientType, authKind) => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "添加设备" }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: `${label} device` } });
  fireEvent.change(screen.getByLabelText("客户端"), { target: { value: clientType } });
  fireEvent.click(screen.getByRole("button", { name: "创建设备" }));

  await screen.findByText(clientType === "air724" ? "srh_api_air724-one-time-token" : "one-time-neutral-secret");
  const createCall = vi.mocked(fetch).mock.calls.find(([input, init]) => {
    return String(input).endsWith("/devices") && init?.method === "POST";
  });
  expect(createCall).toBeDefined();
  expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
    name: `${label} device`,
    client_type: clientType,
    auth_kind: authKind,
  });
});

test("labels the Air724 credential as a one-time script API token", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "添加设备" }));
  fireEvent.change(screen.getByLabelText("设备名称"), { target: { value: "合宙 724 取码器" } });
  fireEvent.change(screen.getByLabelText("客户端"), { target: { value: "air724" } });
  fireEvent.click(screen.getByRole("button", { name: "创建设备" }));

  expect(await screen.findByText("脚本 API 令牌（仅显示一次）")).toBeInTheDocument();
  expect(screen.getByText("srh_api_air724-one-time-token")).toBeInTheDocument();
});

test("does not offer SIM management for an Air724 consumer device", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([air724Device]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/api-clients")) return jsonResponse({ clients: [] });
    return jsonResponse({});
  });

  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  expect(await screen.findByText("合宙 724 取码器")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "设备手机号 合宙 724 取码器" })).not.toBeInTheDocument();
  expect(screen.getByText("脚本 API 令牌")).toBeInTheDocument();
});

test("restores a revoked device with a new one-time credential", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Revoked iPhone");

  fireEvent.click(screen.getByRole("button", { name: "恢复 Revoked iPhone" }));

  expect(await screen.findByText("restored-neutral-secret")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/devices/device-2/restore",
    expect.objectContaining({ method: "POST" }),
  );
});

test("keeps revoke and permanent purge separate and requires the device name", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Revoked iPhone");

  expect(screen.getByRole("button", { name: "撤销 Authorized Android" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "永久删除 Revoked iPhone" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "永久删除 Revoked iPhone" }));

  const confirmButton = screen.getByRole("button", { name: "确认永久删除" });
  expect(confirmButton).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "自动填入当前设备名称" }));
  expect(screen.getByLabelText("输入设备名称以确认")).toHaveValue("Revoked iPhone");
  expect(confirmButton).toBeEnabled();
  fireEvent.change(screen.getByLabelText("输入设备名称以确认"), { target: { value: "wrong" } });
  expect(confirmButton).toBeDisabled();
  fireEvent.change(screen.getByLabelText("输入设备名称以确认"), { target: { value: "wrong" } });
  expect(confirmButton).toBeDisabled();
  fireEvent.change(screen.getByLabelText("输入设备名称以确认"), { target: { value: "Revoked iPhone" } });
  expect(confirmButton).toBeEnabled();
  fireEvent.click(confirmButton);

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/devices/device-2/purge",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

test("revokes an active device through its exact endpoint", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "撤销 Authorized Android" }));

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/devices/device-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

async function connectAndOpenSimProfiles() {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");
  fireEvent.click(screen.getByRole("button", { name: "设备手机号 Authorized Android" }));
  await screen.findByRole("heading", { name: "Authorized Android 手机号" });
}

test("opens and loads masked SIM profiles", async () => {
  await connectAndOpenSimProfiles();

  expect(await screen.findByText("******1234")).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/admin/devices/device-1/sim-profiles",
    expect.objectContaining({ headers: expect.objectContaining({ "X-Admin-Token": "signed-admin-session" }) }),
  );
});

test("shows every detected SIM and edits each slot independently", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices/device-1/sim-profiles") && !init?.method) {
      return jsonResponse([simProfile, secondSimProfile]);
    }
    if (url.includes("/devices/device-1/sim-profiles/") && init?.method === "PUT") {
      const slot = Number(url.split("/").at(-1));
      return jsonResponse(slot === 0 ? simProfile : secondSimProfile);
    }
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    return jsonResponse({});
  });
  await connectAndOpenSimProfiles();

  expect(await screen.findByText("SIM 1")).toBeInTheDocument();
  expect(screen.getByText("SIM 2")).toBeInTheDocument();
  expect(screen.getByText("Subscription 1042")).toBeInTheDocument();
  expect(screen.getByText("Subscription 1043")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "编辑 SIM 2 手机号" }));
  fireEvent.change(screen.getByLabelText("手机号（留空保持不变）"), {
    target: { value: "5550100006" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存映射" }));

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/devices/device-1/sim-profiles/1",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});

test("edits a SIM profile without refilling or resending its masked phone number", async () => {
  await connectAndOpenSimProfiles();
  await screen.findByText("******1234");

  fireEvent.click(screen.getByRole("button", { name: "编辑 SIM 1 手机号" }));
  const phoneInput = screen.getByLabelText("手机号（留空保持不变）") as HTMLInputElement;
  expect(phoneInput.value).toBe("");
  expect(phoneInput.value).not.toBe(simProfile.phone_number_masked);
  fireEvent.click(screen.getByRole("button", { name: "保存映射" }));

  await waitFor(() => {
    const updateCall = vi.mocked(fetch).mock.calls.find(([input, init]) => {
      return String(input).endsWith("/devices/device-1/sim-profiles/0") && init?.method === "PUT";
    });
    expect(updateCall).toBeDefined();
    const body = JSON.parse(String(updateCall?.[1]?.body));
    expect(body).toMatchObject({ source: "manual" });
    expect(body).not.toHaveProperty("carrier_name");
    expect(body).not.toHaveProperty("phone_number");
    expect(String(updateCall?.[1]?.body)).not.toContain(simProfile.phone_number_masked);
  });
});

test("creates the first unused SIM profile and explicitly clears its phone number", async () => {
  await connectAndOpenSimProfiles();
  await screen.findByText("******1234");

  fireEvent.click(screen.getByRole("button", { name: "设置手机号" }));
  fireEvent.change(screen.getByLabelText("设备手机号"), { target: { value: "5550105678" } });
  fireEvent.click(screen.getByRole("button", { name: "保存映射" }));

  await waitFor(() => {
    const createCall = vi.mocked(fetch).mock.calls.find(([input, init]) => {
      return String(input).endsWith("/devices/device-1/sim-profiles/1") && init?.method === "PUT";
    });
    expect(createCall).toBeDefined();
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      phone_number: "5550105678",
      source: "manual",
    });
  });

  fireEvent.click(screen.getByRole("button", { name: "编辑 SIM 2 手机号" }));
  fireEvent.click(screen.getByLabelText("清空现有手机号"));
  fireEvent.click(screen.getByRole("button", { name: "保存映射" }));

  await waitFor(() => {
    const updateCalls = vi.mocked(fetch).mock.calls.filter(([input, init]) => {
      return String(input).endsWith("/devices/device-1/sim-profiles/1") && init?.method === "PUT";
    });
    expect(updateCalls).toHaveLength(2);
    expect(JSON.parse(String(updateCalls[1][1]?.body))).toMatchObject({ phone_number: null });
  });
});

test("deletes a SIM profile from the management dialog", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await connectAndOpenSimProfiles();
  await screen.findByText("******1234");

  fireEvent.click(screen.getByRole("button", { name: "删除 SIM 1 手机号" }));

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/devices/device-1/sim-profiles/0",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

test("refreshes messages in the background after connecting", async () => {
  let messageRequests = 0;
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) {
      messageRequests += 1;
      return jsonResponse(messageRequests > 1 ? [resolvedMessage] : []);
    }
    if (url.endsWith("/otps")) return jsonResponse([]);
    return jsonResponse({});
  });

  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");
  fireEvent.click(screen.getByRole("tab", { name: "消息" }));
  expect(screen.queryByText("Target Service")).not.toBeInTheDocument();

  expect(await screen.findByText("Target Service", {}, { timeout: 3_000 })).toBeInTheDocument();
});

test("ignores an out-of-order SIM profile response after switching devices", async () => {
  const firstProfiles = deferred<Response>();
  const secondProfiles = deferred<Response>();
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices/device-1/sim-profiles")) return firstProfiles.promise;
    if (url.endsWith("/devices/device-2/sim-profiles")) return secondProfiles.promise;
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    return jsonResponse({});
  });

  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("button", { name: "设备手机号 Authorized Android" }));
  await screen.findByRole("heading", { name: "Authorized Android 手机号" });
  fireEvent.click(screen.getByRole("button", { name: "关闭设备手机号窗口" }));
  fireEvent.click(screen.getByRole("button", { name: "设备手机号 Revoked iPhone" }));
  await screen.findByRole("heading", { name: "Revoked iPhone 手机号" });

  await act(async () => {
    secondProfiles.resolve(
      jsonResponse([
        {
          ...simProfile,
          device_id: "device-2",
          phone_number_masked: "******5678",
        },
      ]),
    );
    await Promise.resolve();
  });
  expect(await screen.findByText("******5678")).toBeInTheDocument();

  await act(async () => {
    firstProfiles.resolve(jsonResponse([{ ...simProfile, phone_number_masked: "******9999" }]));
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(screen.getByRole("heading", { name: "Revoked iPhone 手机号" })).toBeInTheDocument();
  expect(screen.getByText("******5678")).toBeInTheDocument();
  expect(screen.queryByText("******9999")).not.toBeInTheDocument();
});

async function connectAndOpenMessages() {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");
  fireEvent.click(screen.getByRole("tab", { name: "消息" }));
}

test("renders receiving identity and offers manual SIM mapping for unresolved messages", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([resolvedMessage, unresolvedMessage]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/devices/device-1/sim-profiles")) return jsonResponse([simProfile]);
    return jsonResponse({});
  });

  await connectAndOpenMessages();

  expect(await screen.findByText("********0005")).toBeInTheDocument();
  expect(screen.queryByText("未解析")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "设置 message-2 的接收手机号" }));
  expect(await screen.findByRole("heading", { name: "Authorized Android 手机号" })).toBeInTheDocument();
});

test("submits receiving phone sender keyword device identity and read filters without exposing SIM slot", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.includes("/messages?")) return jsonResponse([resolvedMessage]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    return jsonResponse({});
  });
  await connectAndOpenMessages();

  fireEvent.change(screen.getByLabelText("接收手机号"), { target: { value: "+15550100005" } });
  fireEvent.change(screen.getByLabelText("发送方"), { target: { value: "Target Service" } });
  fireEvent.change(screen.getByLabelText("正文关键词"), { target: { value: "verification" } });
  fireEvent.change(screen.getByLabelText("设备筛选"), { target: { value: "device-1" } });
  expect(screen.queryByLabelText("SIM 槽")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("已读状态"), { target: { value: "false" } });
  fireEvent.click(screen.getByRole("button", { name: "筛选消息" }));

  await waitFor(() => {
    const filterCall = vi.mocked(fetch).mock.calls.find(([input]) => {
      const url = new URL(String(input), "http://relay.test");
      return url.pathname.endsWith("/messages") && url.searchParams.get("phone_number") === "+15550100005";
    });
    expect(filterCall).toBeDefined();
    const url = new URL(String(filterCall?.[0]), "http://relay.test");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      phone_number: "+15550100005",
      sender: "Target Service",
      keyword: "verification",
      device_id: "device-1",
      read: "false",
    });
  });
});

test("loads bound phone options for the selected device and all devices", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/devices/device-1/sim-profiles")) return jsonResponse([simProfile]);
    if (url.endsWith("/devices/device-2/sim-profiles")) {
      return jsonResponse([{ ...simProfile, device_id: "device-2", slot_index: 1, phone_number_masked: "******5678" }]);
    }
    if (url.includes("/messages?")) return jsonResponse([]);
    return jsonResponse({});
  });

  await connectAndOpenMessages();

  const phonePicker = await screen.findByLabelText("选择已绑定手机号");
  expect(phonePicker).toHaveTextContent("Authorized Android · SIM 1 · ******1234");
  expect(phonePicker).toHaveTextContent("Revoked iPhone · SIM 2 · ******5678");

  fireEvent.change(screen.getByLabelText("设备筛选"), { target: { value: "device-1" } });
  expect(screen.getByLabelText("接收手机号")).toHaveValue("******1234");
  expect(screen.getByLabelText("选择已绑定手机号")).toHaveValue("device-1:0");

  fireEvent.change(screen.getByLabelText("设备筛选"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("选择已绑定手机号"), { target: { value: "device-2:1" } });
  fireEvent.click(screen.getByRole("button", { name: "筛选消息" }));

  await waitFor(() => {
    const filterCall = vi.mocked(fetch).mock.calls.find(([input]) => {
      const url = new URL(String(input), "http://relay.test");
      return url.pathname.endsWith("/messages") && url.searchParams.get("device_id") === "device-2" && url.searchParams.get("sim_slot") === "1";
    });
    expect(filterCall).toBeDefined();
    const url = new URL(String(filterCall?.[0]), "http://relay.test");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({
      device_id: "device-2",
      sim_slot: "1",
    });
    expect(url.searchParams.has("phone_number")).toBe(false);
  });
});

test("clears a selected phone profile when switching to another device filter", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device, revokedDevice]);
    if (url.endsWith("/messages")) return jsonResponse([]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/devices/device-1/sim-profiles")) return jsonResponse([simProfile]);
    if (url.endsWith("/devices/device-2/sim-profiles")) {
      return jsonResponse([{ ...simProfile, device_id: "device-2", slot_index: 1, phone_number_masked: "******5678" }]);
    }
    if (url.includes("/messages?")) return jsonResponse([]);
    return jsonResponse({});
  });

  await connectAndOpenMessages();
  const deviceFilter = screen.getByLabelText("设备筛选");
  const phonePicker = screen.getByLabelText("选择已绑定手机号");
  fireEvent.change(phonePicker, { target: { value: "device-1:0" } });
  fireEvent.change(deviceFilter, { target: { value: "device-2" } });
  expect(phonePicker).toHaveValue("");

  fireEvent.click(screen.getByRole("button", { name: "筛选消息" }));
  await waitFor(() => {
    const filterCall = vi.mocked(fetch).mock.calls.find(([input]) => {
      const url = new URL(String(input), "http://relay.test");
      return url.pathname.endsWith("/messages") && url.searchParams.get("device_id") === "device-2" && url.searchParams.get("limit") === "100";
    });
    expect(filterCall).toBeDefined();
    const url = new URL(String(filterCall?.[0]), "http://relay.test");
    expect(Object.fromEntries(url.searchParams)).toMatchObject({ device_id: "device-2" });
    expect(url.searchParams.has("sim_slot")).toBe(false);
  });
});

test("marks a message read without claiming its OTP", async () => {
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device]);
    if (url.endsWith("/messages")) return jsonResponse([resolvedMessage]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/messages/message-1/read") && init?.method === "POST") {
      return jsonResponse({ ...resolvedMessage, read_at: "2026-08-18T00:01:00+00:00" });
    }
    return jsonResponse({});
  });
  await connectAndOpenMessages();

  fireEvent.click(await screen.findByRole("button", { name: "标记 message-1 为已读" }));

  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/messages/message-1/read",
      expect.objectContaining({ method: "POST" }),
    );
  });
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/otp/claim"))).toBe(false);
  expect(await screen.findByRole("button", { name: "标记 message-1 为未读" })).toBeInTheDocument();
});

test("deletes one message and supports selecting several messages for bulk deletion", async () => {
  const fetchMock = vi.mocked(fetch);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/admin/login")) return jsonResponse({ token: "signed-admin-session", username: "admin", expires_at: "2026-08-22T00:00:00Z" });
    if (url.endsWith("/devices")) return jsonResponse([device]);
    if (url.endsWith("/messages") && !init?.method) return jsonResponse([resolvedMessage, unresolvedMessage]);
    if (url.endsWith("/otps")) return jsonResponse([]);
    if (url.endsWith("/messages/message-1") && init?.method === "DELETE") {
      return { ok: true, status: 204, json: async () => undefined } as Response;
    }
    if (url.endsWith("/messages/bulk-delete") && init?.method === "POST") {
      return jsonResponse({ deleted: 1 });
    }
    return jsonResponse({});
  });

  await connectAndOpenMessages();
  fireEvent.click(screen.getByRole("button", { name: "删除消息 message-1" }));
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/admin/messages/message-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  fireEvent.click(screen.getByRole("button", { name: "批量操作" }));
  fireEvent.click(screen.getByRole("button", { name: "全选" }));
  expect(screen.getByRole("checkbox", { name: "选择消息 message-2" })).toBeChecked();
  expect(screen.getByText("已选 1 条")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "批量删除" }));

  await waitFor(() => {
    const bulkCall = fetchMock.mock.calls.find(([input]) => String(input).endsWith("/messages/bulk-delete"));
    expect(bulkCall).toBeDefined();
    expect(JSON.parse(String(bulkCall?.[1]?.body))).toEqual({ message_ids: ["message-2"] });
  });
  expect(screen.queryByText("已选 1 条")).not.toBeInTheDocument();
});

test("the API tutorial derives the WebSocket URL from the site origin instead of the app port", async () => {
  render(<App />);
  fireEvent.change(screen.getByLabelText("管理员用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("管理员密码"), { target: { value: "neutral-admin-password-123456" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByText("Authorized Android");

  fireEvent.click(screen.getByRole("tab", { name: "总览" }));
  fireEvent.click(await screen.findByRole("button", { name: "API 调用说明" }));
  expect(await screen.findByRole("heading", { name: "API 调用说明" })).toBeInTheDocument();

  // 应用端口只在容器内 expose，对外只有反向代理端口，所以文档不能出现 8000 之类的应用端口。
  const wsUrl = `${window.location.origin.replace(/^http/, "ws")}/api/v1/ws/otp`;
  const pythonSample = screen.getByText(
    (_, element) => element?.tagName === "CODE" && element.textContent?.includes("import asyncio, json, websockets") === true,
  );
  expect(pythonSample).toHaveTextContent(`WS_URL = "${wsUrl}"`);
  expect(pythonSample.textContent).not.toMatch(/:8000|服务器IP/);
  expect(pythonSample).toHaveTextContent("asyncio.wait_for(ws.recv(), timeout=170)");
  expect(screen.getByText((_, element) => element?.className === "tutorial-note" && element.textContent?.includes("连接地址") === true))
    .toHaveTextContent(wsUrl);
});
