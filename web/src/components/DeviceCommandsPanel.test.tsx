import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { DeviceCommandsPanel } from "./DeviceCommandsPanel";

const mocks = vi.hoisted(() => ({
  issueDeviceCommand: vi.fn(),
  listDeviceCommands: vi.fn(),
}));

vi.mock("../adminApi", async () => {
  const actual = await vi.importActual<typeof import("../adminApi")>("../adminApi");
  return {
    ...actual,
    issueDeviceCommand: mocks.issueDeviceCommand,
    listDeviceCommands: mocks.listDeviceCommands,
  };
});

const devices = [
  {
    id: "device-1",
    name: "备用机 A",
    client_type: "android_app" as const,
    auth_kind: "hmac" as const,
    active: true,
    online: true,
    last_seen_at: "2026-09-12T02:00:00+00:00",
  },
  {
    id: "device-2",
    name: "已撤销机",
    client_type: "android_app" as const,
    auth_kind: "hmac" as const,
    active: false,
    online: false,
    last_seen_at: null,
  },
];

const completedCommand = {
  id: "command-1",
  device_id: "device-1",
  command_type: "query_battery" as const,
  status: "completed" as const,
  result: { battery_percent: 87, charging: true },
  created_at: "2026-09-12T02:00:00+00:00",
  sent_at: "2026-09-12T02:01:00+00:00",
  completed_at: "2026-09-12T02:01:10+00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listDeviceCommands.mockResolvedValue([completedCommand]);
  mocks.issueDeviceCommand.mockResolvedValue({
    ...completedCommand,
    id: "command-2",
    status: "pending",
    result: null,
    completed_at: null,
  });
});

test("只列出启用设备并渲染指令结果", async () => {
  render(<DeviceCommandsPanel token="admin" devices={devices} />);
  await waitFor(() => expect(screen.getByText("电量 87% · 充电中")).toBeInTheDocument());
  expect(screen.queryByText("已撤销机")).not.toBeInTheDocument();
  expect(mocks.listDeviceCommands).toHaveBeenCalledWith("admin", "device-1", 30);
});

test("过滤掉非 HMAC 设备（快捷指令无常驻进程，无法领取指令）", async () => {
  const shortcutDevice = {
    id: "device-ios",
    name: "iPhone 快捷指令",
    client_type: "ios_shortcut" as const,
    auth_kind: "bearer" as const,
    active: true,
    online: true,
    last_seen_at: "2026-09-12T02:00:00+00:00",
  };
  mocks.listDeviceCommands.mockResolvedValue([]);
  render(<DeviceCommandsPanel token="admin" devices={[shortcutDevice]} />);
  await waitFor(() => expect(screen.getByText(/没有启用中的设备/)).toBeInTheDocument());
  expect(screen.queryByText("iPhone 快捷指令")).not.toBeInTheDocument();
});

test("下发指令前有确认且调用接口", async () => {
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<DeviceCommandsPanel token="admin" devices={devices} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "触发立即上传" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "触发立即上传" }));
  await waitFor(() =>
    expect(mocks.issueDeviceCommand).toHaveBeenCalledWith("admin", "device-1", "trigger_upload"),
  );
  expect(confirmSpy).toHaveBeenCalled();
});

test("取消确认时不下发指令", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<DeviceCommandsPanel token="admin" devices={devices} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "查询电量" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "查询电量" }));
  await waitFor(() => expect(mocks.listDeviceCommands).toHaveBeenCalled());
  expect(mocks.issueDeviceCommand).not.toHaveBeenCalled();
});

test("没有启用设备时禁用下发按钮", async () => {
  mocks.listDeviceCommands.mockResolvedValue([]);
  render(<DeviceCommandsPanel token="admin" devices={[devices[1]]} />);
  await waitFor(() => expect(screen.getByText(/没有启用中的设备/)).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "查询电量" })).toBeDisabled();
});

test("未授权结果以友好文案展示而不是原始 JSON", async () => {
  mocks.listDeviceCommands.mockResolvedValue([
    {
      ...completedCommand,
      id: "command-unauth",
      command_type: "query_recent_sms" as const,
      result: { error: "command_not_authorized", message: "设备端未开启该指令授权" },
    },
  ]);
  render(<DeviceCommandsPanel token="admin" devices={devices} />);
  await waitFor(() => expect(screen.getByText(/设备端未开启该指令授权/)).toBeInTheDocument());
  expect(screen.queryByText(/command_not_authorized.*\{/)).not.toBeInTheDocument();
});

test("最近短信结果按条展开显示发件人、时间与正文", async () => {
  mocks.listDeviceCommands.mockResolvedValue([
    {
      ...completedCommand,
      id: "command-sms",
      command_type: "query_recent_sms" as const,
      result: {
        count: 2,
        messages: [
          { sender: "****1234", body: "验证码 9999", received_at: 1789000000 },
          { sender: "10086", body: "余额提醒", received_at: 1789000100 },
        ],
      },
    },
  ]);
  render(<DeviceCommandsPanel token="admin" devices={devices} />);
  await waitFor(() => expect(screen.getByText(/设备返回 2 条短信/)).toBeInTheDocument());
  expect(screen.getByText(/验证码 9999/)).toBeInTheDocument();
  expect(screen.getByText(/余额提醒/)).toBeInTheDocument();
});

test("存在未完成指令时自动轮询刷新结果", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    mocks.listDeviceCommands.mockResolvedValue([
      { ...completedCommand, id: "command-pending", status: "pending" as const, result: null, completed_at: null },
    ]);
    render(<DeviceCommandsPanel token="admin" devices={devices} />);
    await vi.waitFor(() => expect(mocks.listDeviceCommands).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5100);
    });
    expect(mocks.listDeviceCommands.mock.calls.length).toBeGreaterThanOrEqual(2);
  } finally {
    vi.useRealTimers();
  }
});
