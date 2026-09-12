import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { AdminDevice, RuntimeInfo } from "../adminApi";
import { RuntimePanel } from "./RuntimePanel";


const runtime: RuntimeInfo = {
  database_backend: "postgresql",
  database_fingerprint: "abc123def456",
  platform_version: "1.1.0",
  latest_releases: {
    manager: {
      version_code: 11,
      version_name: "1.0.10",
      published_at: "2026-09-12T10:45:00Z",
      download_url: "/assets/releases/manager/11/Relay-Hub-Manager-1.0.10.apk",
      sha256: "c709d92e0a47bbbfe80259d89dacc0b8b3dad6e4c712f590a3fc8a644252cc30",
    },
    android: {
      version_code: 23,
      version_name: "1.0.22",
      published_at: "2026-09-12T10:45:00Z",
      download_url: "/assets/releases/android/23/Relay-SMS-Forwarder-1.0.22.apk",
      sha256: "82e1ad7f22f9450e57a14fa50df20aff5ab8a7f1da713d13920f74aa31470531",
    },
  },
  certificate_expires_at: null,
  public_base_url: "http://sms.test:39300",
  relay_site: ":80",
  http_port: 39300,
  https_port: 39301,
  app_port: 39200,
  https_enabled: false,
  services: { app: "healthy", database: "healthy", caddy: "configured" },
  server_time: "2026-09-01T01:00:00+00:00",
};

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
    credential_reuse_warning: true,
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
    duplicate_collection_warning: true,
  },
];


beforeEach(() => {
  Object.assign(navigator, { clipboard: { writeText: vi.fn() } });
});


test("renders runtime ports, HTTP warning, online count, conflicts and API targets", () => {
  render(<RuntimePanel runtime={runtime} devices={devices} baseUrl="" />);

  expect(screen.getByText("平台版本 1.1.0")).toBeInTheDocument();
  expect(screen.getByText("未加密 HTTP")).toBeInTheDocument();
  expect(screen.getByText("39300 / 39200")).toBeInTheDocument();
  expect(screen.getByText("在线设备 1 / 2")).toBeInTheDocument();
  expect(screen.getByText("凭据多实例 1")).toBeInTheDocument();
  expect(screen.getByText("重复采集 1")).toBeInTheDocument();
  expect(screen.getByText("postgresql · abc123def456")).toBeInTheDocument();
  // 各平台最新发布版本按平台名排序拼成一行
  expect(screen.getByText("android 1.0.22 · manager 1.0.10")).toBeInTheDocument();
  expect(screen.getByText("http://sms.test:39300/api/v1/admin/messages")).toBeInTheDocument();
  expect(screen.getByText("ws://sms.test:39300/api/v1/ws/admin/messages")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "复制管理员消息 API" }));
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
    "http://sms.test:39300/api/v1/admin/messages",
  );
});
