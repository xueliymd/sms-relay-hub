import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AdminDevice } from "../adminApi";
import { LiveMessagePanel } from "./LiveMessagePanel";


class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.onopen?.();
  }

  receive(payload: object): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }

  disconnect(): void {
    this.onclose?.();
  }
}


const devices: AdminDevice[] = [
  {
    id: "device-a",
    name: "Phone A",
    client_type: "android_app",
    auth_kind: "hmac",
    active: true,
    online: true,
    last_seen_at: "2026-09-01T01:00:00Z",
    client_instance_id: "instance-a",
    client_version: "1.0.12",
    client_mode: "android_app",
    runtime_updated_at: "2026-09-01T01:00:00Z",
    credential_reuse_warning: false,
    duplicate_collection_warning: false,
  },
  {
    id: "device-b",
    name: "Phone B",
    client_type: "root_module",
    auth_kind: "hmac",
    active: true,
    online: false,
    last_seen_at: null,
    client_instance_id: null,
    client_version: null,
    client_mode: null,
    runtime_updated_at: null,
    credential_reuse_warning: false,
    duplicate_collection_warning: false,
  },
];


beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })),
  );
  Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
});


afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});


test("subscribes to the selected device and renders full SMS plus OTP", async () => {
  render(
    <LiveMessagePanel
      baseUrl="https://sms.test"
      token="admin-token"
      devices={devices}
      selectedDeviceId="device-a"
      selectedSimSlot={1}
      onDeviceChange={vi.fn()}
      onSimSlotChange={vi.fn()}
      onUnauthorized={vi.fn()}
    />,
  );

  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  expect(socket.url).toBe("wss://sms.test/api/v1/ws/admin/messages");
  act(() => socket.open());
  expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: "auth", token: "admin-token" }));
  act(() => socket.receive({ type: "auth_ok" }));
  expect(socket.send).toHaveBeenCalledWith(JSON.stringify({
    type: "subscribe",
    device_id: "device-a",
    sim_slot: 1,
  }));
  act(() => socket.receive({ type: "subscribed", device_id: "device-a", sim_slot: 1 }));
  expect(screen.getByText("实时连接已建立")).toBeInTheDocument();

  act(() => socket.receive({
    type: "message",
    id: "message-1",
    device_id: "device-a",
    client_message_id: "client-1",
    received_at: "2026-09-01T01:02:03Z",
    created_at: "2026-09-01T01:02:04Z",
    sender: "Bank",
    body: "Your verification code is 482731",
    source: "android_app",
    sim_slot: 1,
    subscription_id: 22,
    receiving_phone_masked: "********0005",
    receiving_identity_status: "resolved",
    otp_status: "available",
    otp_code: "482731",
    read_at: null,
    metadata: {},
    redacted: false,
    cursor: { created_at: "2026-09-01T01:02:04Z", id: "message-1" },
  }));

  expect(screen.getByText("Your verification code is 482731")).toBeInTheDocument();
  expect(screen.getByText("验证码 482731")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "复制验证码 482731" }));
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith("482731");
});


test("recovers from the last cursor after disconnect", async () => {
  render(
    <LiveMessagePanel
      baseUrl=""
      token="admin-token"
      devices={devices}
      selectedDeviceId="device-a"
      selectedSimSlot={null}
      onDeviceChange={vi.fn()}
      onSimSlotChange={vi.fn()}
      onUnauthorized={vi.fn()}
    />,
  );
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const socket = FakeWebSocket.instances[0];
  act(() => socket.open());
  act(() => socket.receive({ type: "auth_ok" }));
  act(() => socket.receive({ type: "subscribed" }));
  act(() => socket.receive({
    type: "message",
    id: "message-2",
    device_id: "device-a",
    client_message_id: "client-2",
    received_at: "2026-09-01T01:02:03Z",
    created_at: "2026-09-01T01:02:04Z",
    sender: "Service",
    body: "Recovery anchor",
    source: "android_app",
    sim_slot: 0,
    subscription_id: null,
    receiving_phone_masked: null,
    receiving_identity_status: "unresolved",
    otp_status: null,
    read_at: null,
    metadata: {},
    redacted: false,
    cursor: { created_at: "2026-09-01T01:02:04Z", id: "message-2" },
  }));
  act(() => socket.disconnect());

  expect(screen.getByText("连接已断开，正在补偿并重连")).toBeInTheDocument();
  await waitFor(() => {
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("created_after=2026-09-01T01%3A02%3A04Z"),
      expect.anything(),
    );
  });
});

test("renders the device received time instead of the server insert time", async () => {
  render(
    <LiveMessagePanel
      baseUrl=""
      token="admin-token"
      devices={devices}
      selectedDeviceId="device-a"
      selectedSimSlot={null}
      onDeviceChange={vi.fn()}
      onSimSlotChange={vi.fn()}
      onUnauthorized={vi.fn()}
    />,
  );

  const socket = FakeWebSocket.instances[0];
  act(() => socket.open());
  act(() => socket.receive({ type: "auth_ok" }));
  act(() => socket.receive({ type: "subscribed" }));
  act(() => socket.receive({
    type: "message",
    id: "message-time",
    device_id: "device-a",
    client_message_id: "client-time",
    received_at: "2026-09-05T04:31:05Z",
    created_at: "2026-09-05T04:31:58Z",
    sender: "Service",
    body: "Time anchor",
    source: "android_app",
    sim_slot: 0,
    subscription_id: null,
    receiving_phone_masked: null,
    receiving_identity_status: "unresolved",
    otp_status: null,
    read_at: null,
    metadata: {},
    redacted: false,
    cursor: { created_at: "2026-09-05T04:31:58Z", id: "message-time" },
  }));

  const expectedTime = new Date("2026-09-05T04:31:05Z").toLocaleString();
  expect(screen.getByText(expectedTime)).toBeInTheDocument();
});


test("switching the selected device clears messages and closes the old socket", async () => {
  const props = {
    baseUrl: "",
    token: "admin-token",
    devices,
    selectedSimSlot: null,
    onDeviceChange: vi.fn(),
    onSimSlotChange: vi.fn(),
    onUnauthorized: vi.fn(),
  };
  const view = render(<LiveMessagePanel {...props} selectedDeviceId="device-a" />);
  await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  const firstSocket = FakeWebSocket.instances[0];
  act(() => firstSocket.open());
  act(() => firstSocket.receive({ type: "auth_ok" }));
  act(() => firstSocket.receive({ type: "subscribed" }));
  act(() => firstSocket.receive({
    type: "message",
    id: "message-old",
    device_id: "device-a",
    client_message_id: "client-old",
    received_at: "2026-09-01T01:02:03Z",
    created_at: "2026-09-01T01:02:04Z",
    sender: "Service",
    body: "Old device message",
    source: "android_app",
    sim_slot: 0,
    subscription_id: null,
    receiving_phone_masked: null,
    receiving_identity_status: "unresolved",
    otp_status: null,
    read_at: null,
    metadata: {},
    redacted: false,
    cursor: { created_at: "2026-09-01T01:02:04Z", id: "message-old" },
  }));
  expect(screen.getByText("Old device message")).toBeInTheDocument();

  view.rerender(<LiveMessagePanel {...props} selectedDeviceId="device-b" />);

  expect(firstSocket.close).toHaveBeenCalled();
  expect(screen.queryByText("Old device message")).not.toBeInTheDocument();
});
