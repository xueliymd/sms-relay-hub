import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { ForwardingPanel } from "./ForwardingPanel";

const mocks = vi.hoisted(() => ({
  listForwardingRules: vi.fn(),
  listForwardingLogs: vi.fn(),
  createForwardingRule: vi.fn(),
  updateForwardingRule: vi.fn(),
  deleteForwardingRule: vi.fn(),
  testForwardingRule: vi.fn(),
}));

vi.mock("../adminApi", () => ({
  listForwardingRules: mocks.listForwardingRules,
  listForwardingLogs: mocks.listForwardingLogs,
  createForwardingRule: mocks.createForwardingRule,
  updateForwardingRule: mocks.updateForwardingRule,
  deleteForwardingRule: mocks.deleteForwardingRule,
  testForwardingRule: mocks.testForwardingRule,
}));

const rule = {
  id: "rule-1",
  name: "验证码转发",
  enabled: true,
  match_device_id: null,
  match_phone_masked: "",
  match_sender: "10086",
  match_keyword: "验证码",
  channel: "dingtalk",
  target_masked: "https://oapi.dingtalk.com/***",
  template: "",
  created_at: "2026-09-10T00:00:00+00:00",
  updated_at: "2026-09-10T00:00:00+00:00",
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listForwardingRules.mockResolvedValue({
    channels: [{ id: "wecom", label: "企业微信" }, { id: "dingtalk", label: "钉钉机器人" }],
    rules: [rule],
  });
  mocks.listForwardingLogs.mockResolvedValue({
    logs: [
      {
        id: 1,
        rule_id: "rule-1",
        rule_name: "验证码转发",
        message_id: null,
        channel: "dingtalk",
        target_masked: "https://oapi.dingtalk.com/***",
        status: "success",
        attempts: 1,
        error: null,
        created_at: "2026-09-10T01:00:00+00:00",
      },
    ],
  });
});

test("渲染规则与日志", async () => {
  render(<ForwardingPanel token="admin" />);
  await waitFor(() => expect(screen.getAllByText("验证码转发").length).toBeGreaterThan(0));
  expect(screen.getByText(/钉钉机器人/)).toBeInTheDocument();
  expect(screen.getByText("success")).toBeInTheDocument();
  expect(screen.getByText("新建规则")).toBeInTheDocument();
});

test("删除前有确认且调用 API", async () => {
  const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
  mocks.deleteForwardingRule.mockResolvedValue({ deleted: true });
  mocks.listForwardingRules.mockResolvedValueOnce({
    channels: [],
    rules: [rule],
  });
  mocks.listForwardingRules.mockResolvedValue({ channels: [], rules: [] });
  render(<ForwardingPanel token="admin" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  await waitFor(() => expect(mocks.deleteForwardingRule).toHaveBeenCalledWith("admin", "rule-1"));
  expect(confirmSpy).toHaveBeenCalled();
});

test("测试按钮调用测试接口", async () => {
  mocks.testForwardingRule.mockResolvedValue({ ok: true, message: "测试推送成功：ok" });
  render(<ForwardingPanel token="admin" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "测试" })).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "测试" }));
  await waitFor(() => expect(mocks.testForwardingRule).toHaveBeenCalledWith("admin", "rule-1"));
  expect(await screen.findByText(/测试推送成功/)).toBeInTheDocument();
});
