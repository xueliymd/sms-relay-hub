import { useCallback, useEffect, useMemo, useState } from "react";

import {
  AdminDevice,
  DEVICE_COMMAND_LABELS,
  DeviceCommand,
  DeviceCommandType,
  describeCommandResult,
  issueDeviceCommand,
  listDeviceCommands,
} from "../adminApi";

const COMMAND_TYPES: DeviceCommandType[] = ["query_battery", "query_recent_sms", "trigger_upload"];

const COMMAND_HINTS: Record<DeviceCommandType, string> = {
  query_battery: "读取设备电量与充电状态，结果脱敏后回传平台。",
  query_recent_sms: "读取设备最近若干条短信摘要（需设备端开启该指令授权）。",
  trigger_upload: "让设备立即执行一次队列上报，用于排障与补传。",
};

const COMMAND_STATUS_LABELS: Record<string, string> = {
  pending: "等待设备领取（默认 10 分钟内有效）",
  sent: "设备已领取，等待回传（默认 30 分钟内有效）",
  completed: "已完成",
  expired: "已过期（设备未领取或未回传）",
};

function commandStatusLabel(status: string): string {
  return COMMAND_STATUS_LABELS[status] ?? status;
}

/** 设备端回传中的业务错误（未授权/缺权限等），单独友好展示。 */
function commandErrorMessage(command: DeviceCommand): string | null {
  const result = command.result;
  if (!result) return null;
  const message = result.message;
  if (typeof message === "string" && message.trim()) {
    const code = typeof result.error === "string" ? result.error : "command_error";
    return `${message}（${code}）`;
  }
  return null;
}

function recentSmsItems(command: DeviceCommand): { sender: string; body: string; received_at: number }[] {
  const messages = command.result?.messages;
  if (!Array.isArray(messages)) return [];
  return messages
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((item) => ({
      sender: typeof item.sender === "string" ? item.sender : "未知发件人",
      body: typeof item.body === "string" ? item.body : "",
      received_at: typeof item.received_at === "number" ? item.received_at : 0,
    }));
}

function formatUnixSeconds(value: number): string {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}

/** 远程控制面板：向选定设备下发 v1 指令并查看执行结果。 */
export function DeviceCommandsPanel({
  token,
  devices,
}: {
  token: string;
  devices: AdminDevice[];
}) {
  const activeDevices = useMemo(
    () =>
      devices.filter(
        (device) =>
          device.active &&
          // v1 只有 Android 应用与 Root 模块带常驻签名轮询器。iOS Ve 虽然也是
          // HMAC，但 SpringBoard 内没有命令轮询线程，不能进入目标设备列表。
          (device.client_type === "android_app" || device.client_type === "root_module") &&
          device.auth_kind === "hmac",
      ),
    [devices],
  );
  const [deviceId, setDeviceId] = useState<string>("");
  const [commands, setCommands] = useState<DeviceCommand[]>([]);
  const [notice, setNotice] = useState("请选择设备后下发指令");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!deviceId && activeDevices.length > 0) {
      setDeviceId(activeDevices[0].id);
    }
    if (deviceId && !activeDevices.some((device) => device.id === deviceId)) {
      setDeviceId(activeDevices[0]?.id ?? "");
    }
  }, [activeDevices, deviceId]);

  const refresh = useCallback(
    async (message?: string) => {
      if (!deviceId) {
        setCommands([]);
        return;
      }
      try {
        const payload = await listDeviceCommands(token, deviceId, 30);
        setCommands(payload);
        setNotice(message ?? `已加载 ${payload.length} 条指令记录`);
      } catch (error) {
        setNotice(`加载失败：${String(error)}`);
      }
    },
    [deviceId, token],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!deviceId) return;
    // 有未完成指令时自动轮询结果；Android/Root 通过约 60 秒心跳领取。
    const hasOutstanding = commands.some(
      (command) => command.status === "pending" || command.status === "sent",
    );
    if (!hasOutstanding) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [commands, deviceId, refresh]);

  const handleIssue = useCallback(
    async (commandType: DeviceCommandType) => {
      if (!deviceId) {
        setNotice("请先选择目标设备");
        return;
      }
      const deviceName = activeDevices.find((device) => device.id === deviceId)?.name ?? deviceId;
      const label = DEVICE_COMMAND_LABELS[commandType] ?? commandType;
      if (!window.confirm(`向设备「${deviceName}」下发「${label}」指令？\n\n设备需已开启该指令的端侧授权，否则会返回未授权说明。`)) {
        return;
      }
      setBusy(true);
      try {
        await issueDeviceCommand(token, deviceId, commandType);
        await refresh(`已下发「${label}」，等待设备心跳领取并执行`);
      } catch (error) {
        setNotice(`下发失败：${String(error)}`);
      } finally {
        setBusy(false);
      }
    },
    [activeDevices, deviceId, refresh, token],
  );

  return (
    <section aria-labelledby="device-commands-title">
      <div className="section-heading">
        <div>
          <h2 id="device-commands-title">远程控制（设备指令）</h2>
          <span className="section-count">
            下行采用心跳轮询：Android/Root 约每 60 秒领取指令并回传结果，非实时；每类指令需设备端单独授权。
            仅支持 Android 应用 / Root 模块；其他客户端没有常驻指令轮询器，不能接收下行指令。
          </span>
        </div>
        <button className="secondary-button" onClick={() => void refresh()} disabled={busy}>
          刷新结果
        </button>
      </div>

      <div role="status" className="credential-help" aria-live="polite">
        {notice}
      </div>

      <label className="device-command-field">
        目标设备
        <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
          {activeDevices.length === 0 ? <option value="">（没有启用中的设备）</option> : null}
          {activeDevices.map((device) => (
            <option key={device.id} value={device.id}>
              {device.name}
              {device.online ? " · 在线" : " · 离线"}
            </option>
          ))}
        </select>
      </label>

      <div className="device-command-actions">
        {COMMAND_TYPES.map((commandType) => (
          <button
            key={commandType}
            onClick={() => void handleIssue(commandType)}
            disabled={busy || !deviceId}
            title={COMMAND_HINTS[commandType]}
          >
            {DEVICE_COMMAND_LABELS[commandType] ?? commandType}
          </button>
        ))}
      </div>
      <ul className="credential-help">
        {COMMAND_TYPES.map((commandType) => (
          <li key={commandType}>
            {DEVICE_COMMAND_LABELS[commandType]}：{COMMAND_HINTS[commandType]}
          </li>
        ))}
      </ul>

      <table aria-label="设备指令记录">
        <thead>
          <tr>
            <th>下发时间</th>
            <th>指令</th>
            <th>状态</th>
            <th>结果</th>
            <th>结束时间</th>
          </tr>
        </thead>
        <tbody>
          {commands.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty-state">
                暂无指令记录。选择设备后点击上方的指令按钮下发。
              </td>
            </tr>
          ) : (
            commands.map((command) => {
              const errorText = commandErrorMessage(command);
              const smsItems = command.command_type === "query_recent_sms" ? recentSmsItems(command) : [];
              return (
                <tr key={command.id}>
                  <td>{command.created_at}</td>
                  <td>{DEVICE_COMMAND_LABELS[command.command_type] ?? command.command_type}</td>
                  <td><span className={`status-badge ${command.status}`}>{commandStatusLabel(command.status)}</span></td>
                  <td>
                    {errorText ? <span className="command-result-error">{errorText}</span> : describeCommandResult(command)}
                    {smsItems.length > 0 && (
                      <ul className="command-result-list">
                        {smsItems.map((item, index) => (
                          <li key={`${command.id}-${index}`}>
                            {item.sender} · {formatUnixSeconds(item.received_at)} · {item.body}
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td>{command.completed_at ?? "-"}</td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </section>
  );
}
