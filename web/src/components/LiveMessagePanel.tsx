import { useEffect, useRef, useState } from "react";
import { Copy, Radio } from "lucide-react";

import {
  adminRequest,
  adminWebSocketUrl,
  messageCursorQuery,
  type AdminDevice,
  type AdminMessage,
  type MessageCursor,
} from "../adminApi";


type LiveMessageEvent = AdminMessage & {
  type: "message";
  cursor: { created_at: string; id: string };
};

type LiveMessagePanelProps = {
  baseUrl: string;
  token: string;
  devices: AdminDevice[];
  selectedDeviceId: string;
  selectedSimSlot: number | null;
  onDeviceChange: (deviceId: string) => void;
  onSimSlotChange: (simSlot: number | null) => void;
  onUnauthorized: () => void;
};

const reconnectDelays = [1000, 2000, 5000, 10000, 30000];

function apiUrl(baseUrl: string, path: string): string {
  return baseUrl ? new URL(path, baseUrl.endsWith("/") ? baseUrl : baseUrl + "/").toString() : path;
}

function compareMessages(left: AdminMessage, right: AdminMessage): number {
  const created = (right.created_at ?? right.received_at).localeCompare(left.created_at ?? left.received_at);
  return created || right.id.localeCompare(left.id);
}

export function LiveMessagePanel({
  baseUrl,
  token,
  devices,
  selectedDeviceId,
  selectedSimSlot,
  onDeviceChange,
  onSimSlotChange,
  onUnauthorized,
}: LiveMessagePanelProps) {
  const [messages, setMessages] = useState<AdminMessage[]>([]);
  const [status, setStatus] = useState("正在连接实时消息");
  const cursorRef = useRef<MessageCursor | null>(null);
  const unauthorizedRef = useRef(onUnauthorized);

  useEffect(() => {
    unauthorizedRef.current = onUnauthorized;
  }, [onUnauthorized]);

  useEffect(() => {
    setMessages([]);
    cursorRef.current = null;
    if (!token || !selectedDeviceId) {
      setStatus("请选择设备");
      return;
    }

    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectAttempt = 0;

    const mergeMessages = (incoming: AdminMessage[]) => {
      if (!active || incoming.length === 0) return;
      setMessages((current) => {
        const byId = new Map(current.map((message) => [message.id, message]));
        incoming.forEach((message) => byId.set(message.id, message));
        return [...byId.values()].sort(compareMessages).slice(0, 500);
      });
    };

    const recover = async (cursor: MessageCursor | null) => {
      const query = messageCursorQuery(cursor, {
        deviceId: selectedDeviceId,
        simSlot: selectedSimSlot,
        limit: 500,
      });
      try {
        const payload = await adminRequest<unknown>(
          apiUrl(baseUrl, "/api/v1/admin/messages" + query),
          token,
        );
        const recovered = Array.isArray(payload) ? payload as AdminMessage[] : [];
        mergeMessages(recovered);
        if (!cursorRef.current && recovered.length > 0) {
          const latest = [...recovered].sort(compareMessages)[0];
          cursorRef.current = { createdAt: latest.created_at ?? latest.received_at, id: latest.id };
        }
      } catch (error) {
        if (error instanceof Error && error.message === "admin_auth_required") {
          unauthorizedRef.current();
        }
      }
    };

    const connect = () => {
      if (!active) return;
      setStatus(reconnectAttempt === 0 ? "正在连接实时消息" : "正在重新连接实时消息");
      socket = new WebSocket(adminWebSocketUrl(baseUrl, "/api/v1/ws/admin/messages"));
      socket.onopen = () => {
        socket?.send(JSON.stringify({ type: "auth", token }));
      };
      socket.onmessage = (event) => {
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(event.data) as Record<string, unknown>;
        } catch {
          return;
        }
        if (payload.type === "auth_ok") {
          const subscription: Record<string, unknown> = {
            type: "subscribe",
            device_id: selectedDeviceId,
          };
          if (selectedSimSlot !== null) subscription.sim_slot = selectedSimSlot;
          socket?.send(JSON.stringify(subscription));
          return;
        }
        if (payload.type === "subscribed") {
          reconnectAttempt = 0;
          setStatus("实时连接已建立");
          if (pingTimer) clearInterval(pingTimer);
          pingTimer = setInterval(() => {
            socket?.send(JSON.stringify({ type: "ping" }));
          }, 30000);
          return;
        }
        if (payload.type === "message") {
          const message = payload as unknown as LiveMessageEvent;
          cursorRef.current = {
            createdAt: message.cursor.created_at,
            id: message.cursor.id,
          };
          mergeMessages([message]);
          return;
        }
        if (payload.type === "error" && payload.code === "admin_auth_required") {
          unauthorizedRef.current();
          socket?.close();
        }
      };
      socket.onclose = () => {
        if (!active) return;
        if (pingTimer) clearInterval(pingTimer);
        setStatus("连接已断开，正在补偿并重连");
        void recover(cursorRef.current).finally(() => {
          if (!active) return;
          const delay = reconnectDelays[Math.min(reconnectAttempt, reconnectDelays.length - 1)];
          reconnectAttempt += 1;
          reconnectTimer = setTimeout(connect, delay);
        });
      };
      socket.onerror = () => {
        setStatus("实时连接异常");
      };
    };

    void recover(null);
    connect();

    return () => {
      active = false;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (pingTimer) clearInterval(pingTimer);
      socket?.close();
    };
  }, [baseUrl, selectedDeviceId, selectedSimSlot, token]);

  return (
    <section className="live-message-panel" aria-labelledby="live-message-title">
      <div className="section-heading">
        <div>
          <h2 id="live-message-title">设备实时短信</h2>
          <span className="section-count">最多保留最近 500 条</span>
        </div>
        <span className="live-status"><Radio size={15} />{status}</span>
      </div>

      <div className="live-filter-grid">
        <label>
          选择设备
          <select value={selectedDeviceId} onChange={(event) => onDeviceChange(event.target.value)}>
            <option value="">请选择设备</option>
            {devices.filter((device) => device.active).map((device) => (
              <option key={device.id} value={device.id}>{device.name}</option>
            ))}
          </select>
        </label>
        <label>
          选择 SIM
          <select
            value={selectedSimSlot === null ? "all" : String(selectedSimSlot)}
            onChange={(event) => onSimSlotChange(event.target.value === "all" ? null : Number(event.target.value))}
          >
            <option value="all">全部 SIM</option>
            <option value="0">SIM1</option>
            <option value="1">SIM2</option>
          </select>
        </label>
      </div>

      <div className="live-message-list">
        {messages.length === 0 ? <p className="empty-state">当前设备暂无短信</p> : messages.map((message) => (
          <article className="live-message-card" key={message.id}>
            <div className="live-message-meta">
              <strong>{message.sender}</strong>
              <span>{new Date(message.message_date ?? message.received_at).toLocaleString()}</span>
              <span>{message.sim_slot === null ? "未知 SIM" : message.sim_slot === 0 ? "SIM1" : "SIM2"}</span>
            </div>
            <p>{message.body ?? "短信正文已清除"}</p>
            <div className="live-message-actions">
              {message.otp_code ? (
                <button
                  className="secondary-button"
                  onClick={() => void navigator.clipboard?.writeText(message.otp_code ?? "")}
                  aria-label={"复制验证码 " + message.otp_code}
                >
                  <Copy size={14} />验证码 {message.otp_code}
                </button>
              ) : null}
              {message.body ? (
                <button
                  className="icon-button"
                  onClick={() => void navigator.clipboard?.writeText(message.body ?? "")}
                  aria-label={"复制短信 " + message.id}
                >
                  <Copy size={14} />
                </button>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
