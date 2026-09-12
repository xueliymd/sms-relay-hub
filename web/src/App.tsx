import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Ban,
  BookOpen,
  Copy,
  CreditCard,
  Database,
  KeyRound,
  LogOut,
  Mail,
  MailOpen,
  Plus,
  Pencil,
  RefreshCw,
  Radio,
  RotateCw,
  ShieldCheck,
  Search,
  Settings,
  Smartphone,
  Trash2,
  Undo2,
  X, Send } from "lucide-react";
import QRCode from "qrcode";
import type { ReleaseEntry, RuntimeInfo } from "./adminApi";
import { ForwardingPanel } from "./components/ForwardingPanel";
import { DeviceCommandsPanel } from "./components/DeviceCommandsPanel";
import { LiveMessagePanel } from "./components/LiveMessagePanel";
import { ReleaseHistoryPanel } from "./components/ReleaseHistoryPanel";
import { RuntimePanel } from "./components/RuntimePanel";
import "./App.css";

type Device = {
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

type Message = {
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
  read_at: string | null;
  metadata: Record<string, unknown>;
  redacted: boolean;
};

type AccessCode = {
  id: string;
  message_id: string;
  status: "available" | "claimed" | "expired";
  expires_at: string;
};

type ApiClientRecord = {
  id: string;
  token: string | null;
  active: boolean;
  created_at: string;
};

type ApiClientListResponse = {
  clients: ApiClientRecord[];
};

type CredentialResponse = {
  device: Device;
  credential: { type: string; secret: string };
};

type EnrollmentQrResponse = {
  device: Pick<Device, "id" | "name" | "client_type" | "auth_kind">;
  qr_text: string;
  expires_at: string;
  ttl_seconds: number;
};

type AdminSessionResponse = {
  token: string;
  username: string;
  expires_at: string;
};

type SimProfileSource = "auto" | "manual" | "vendor";

type SimProfile = {
  device_id: string;
  slot_index: number;
  subscription_id: number | null;
  phone_number_masked: string | null;
  carrier_name: string | null;
  source: SimProfileSource;
  updated_at: string;
};

type MessagePhoneOption = {
  key: string;
  deviceId: string;
  slotIndex: number;
  phoneNumberMasked: string;
  label: string;
};

type Tab = "overview" | "devices" | "messages" | "codes" | "forwarding" | "commands";

const tabs: { id: Tab; label: string; icon: typeof Activity }[] = [
  { id: "overview", label: "总览", icon: Activity },
  { id: "devices", label: "设备", icon: Smartphone },
  { id: "messages", label: "消息", icon: Mail },
  { id: "codes", label: "取码记录", icon: KeyRound },
  { id: "forwarding", label: "转发规则", icon: Send },
  { id: "commands", label: "远程控制", icon: Radio },
];

const clientOptions: { value: Device["client_type"]; label: string; authKind: Device["auth_kind"] }[] = [
  { value: "android_app", label: "Android 应用", authKind: "hmac" },
  { value: "root_module", label: "Android Root", authKind: "hmac" },
  { value: "ios_shortcut", label: "iPhone 快捷指令", authKind: "bearer" },
  { value: "ve_tweak", label: "iOS Ve", authKind: "hmac" },
  { value: "air724", label: "合宙 724 取码", authKind: "script_api" },
  { value: "air724_mqtt", label: "合宙 724 MQTT 短信上报", authKind: "hmac" },
];

const authLabels: Record<Device["auth_kind"], string> = {
  hmac: "HMAC",
  bearer: "Bearer",
  script_api: "脚本 API 令牌",
};

const clientLabels = Object.fromEntries(clientOptions.map((option) => [option.value, option.label])) as Record<
  Device["client_type"],
  string
>;

async function adminRequest<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
      "X-Admin-Token": token,
    },
  });
  if (!response.ok) {
    let code = `http_${response.status}`;
    try {
      const payload = (await response.json()) as { error?: { code?: string } };
      code = payload.error?.code ?? code;
    } catch {
      // The UI intentionally avoids echoing arbitrary response bodies.
    }
    throw new Error(code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "尚未上报";
}

function isRuntimeInfo(value: unknown): value is RuntimeInfo {
  if (!value || typeof value !== "object") return false;
  const runtime = value as Partial<RuntimeInfo>;
  return (
    typeof runtime.platform_version === "string" &&
    typeof runtime.public_base_url === "string" &&
    typeof runtime.database_backend === "string" &&
    typeof runtime.database_fingerprint === "string" &&
    typeof runtime.http_port === "number" &&
    typeof runtime.https_port === "number" &&
    typeof runtime.app_port === "number" &&
    typeof runtime.https_enabled === "boolean" &&
    Boolean(runtime.services) &&
    typeof runtime.services === "object"
  );
}

function releaseEntries(value: unknown): ReleaseEntry[] {
  if (!value || typeof value !== "object") return [];
  const releases = (value as { releases?: unknown }).releases;
  return Array.isArray(releases) ? releases as ReleaseEntry[] : [];
}

export default function App() {
  const [adminToken, setAdminToken] = useState(() => sessionStorage.getItem("relay-admin-token") ?? "");
  const [adminUsername, setAdminUsername] = useState("");
  const [adminPassword, setAdminPassword] = useState("");
  const [accountCurrentPassword, setAccountCurrentPassword] = useState("");
  const [accountUsername, setAccountUsername] = useState("");
  const [accountPassword, setAccountPassword] = useState("");
  const [accountPasswordConfirm, setAccountPasswordConfirm] = useState("");
  const [showAccountSettings, setShowAccountSettings] = useState(false);
  const [connected, setConnected] = useState(false);
  const [activeTab, setActiveTab] = useState<Tab>("devices");
  const [devices, setDevices] = useState<Device[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [codes, setCodes] = useState<AccessCode[]>([]);
  const [runtimeInfo, setRuntimeInfo] = useState<RuntimeInfo | null>(null);
  const [releaseHistory, setReleaseHistory] = useState<ReleaseEntry[]>([]);
  const [liveDeviceId, setLiveDeviceId] = useState("");
  const [liveSimSlot, setLiveSimSlot] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("等待连接");
  const [showCreate, setShowCreate] = useState(false);
  const [showTutorial, setShowTutorial] = useState(false);
  const [showApiDoc, setShowApiDoc] = useState(false);
  const [deviceName, setDeviceName] = useState("");
  const [clientType, setClientType] = useState<Device["client_type"]>("android_app");
  const [oneTimeSecret, setOneTimeSecret] = useState<string | null>(null);
  const [oneTimeSecretLabel, setOneTimeSecretLabel] = useState("设备鉴权密钥（仅显示一次）");
  const [enrollmentQr, setEnrollmentQr] = useState<(EnrollmentQrResponse & { dataUrl: string }) | null>(null);
  const [apiClientToken, setApiClientToken] = useState<string | null>(null);
  const [purgeTarget, setPurgeTarget] = useState<Device | null>(null);
  const [purgeConfirmation, setPurgeConfirmation] = useState("");
  const [simTarget, setSimTarget] = useState<Device | null>(null);
  const [simProfiles, setSimProfiles] = useState<SimProfile[]>([]);
  const [simLoading, setSimLoading] = useState(false);
  const [simEditorOpen, setSimEditorOpen] = useState(false);
  const [simEditingSlot, setSimEditingSlot] = useState<number | null>(null);
  const [simSlot, setSimSlot] = useState("");
  const [simPhoneNumber, setSimPhoneNumber] = useState("");
  const [simClearPhone, setSimClearPhone] = useState(false);
  const [messagePhone, setMessagePhone] = useState("");
  const [messageSender, setMessageSender] = useState("");
  const [messageKeyword, setMessageKeyword] = useState("");
  const [messageDeviceId, setMessageDeviceId] = useState("");
  const [messageReadStatus, setMessageReadStatus] = useState("");
  const [messagePhoneFilterKey, setMessagePhoneFilterKey] = useState("");
  const [messagePhoneOptions, setMessagePhoneOptions] = useState<MessagePhoneOption[]>([]);
  const [bulkMessageMode, setBulkMessageMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<Set<string>>(new Set());
  const simRequestGeneration = useRef(0);

  const onlineDevices = useMemo(() => devices.filter((device) => device.online === true).length, [devices]);
  const availableCodes = useMemo(() => codes.filter((code) => code.status === "available").length, [codes]);
  const hasMessageFilters = Boolean(
    messagePhone.trim() ||
      messagePhoneFilterKey ||
      messageSender.trim() ||
      messageKeyword.trim() ||
      messageDeviceId ||
      messageReadStatus,
  );
  const visibleMessagePhoneOptions = useMemo(
    () =>
      messageDeviceId
        ? messagePhoneOptions.filter((option) => option.deviceId === messageDeviceId)
        : messagePhoneOptions,
    [messageDeviceId, messagePhoneOptions],
  );

  useEffect(() => {
    if (!connected || !adminToken.trim() || hasMessageFilters) return undefined;
    let active = true;
    let refreshing = false;
    const refresh = async () => {
      if (refreshing) return;
      refreshing = true;
      try {
        const nextMessages = await adminRequest<Message[]>("/api/v1/admin/messages", adminToken);
        if (active) setMessages(nextMessages);
      } catch {
        // Quiet refreshes leave the last usable data on screen.
      } finally {
        refreshing = false;
      }
    };
    const interval = window.setInterval(() => void refresh(), 2_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [adminToken, connected, hasMessageFilters]);

  useEffect(() => {
    if (!connected || activeTab !== "messages" || !adminToken.trim() || devices.length === 0) {
      return undefined;
    }
    let active = true;
    const loadPhoneOptions = async () => {
      const nestedOptions = await Promise.all(
        devices.map(async (device) => {
          try {
            const profiles = await adminRequest<SimProfile[]>(
              `/api/v1/admin/devices/${device.id}/sim-profiles`,
              adminToken,
            );
            if (!Array.isArray(profiles)) return [];
            return profiles
              .filter((profile) => profile.phone_number_masked)
              .map((profile) => ({
                key: `${device.id}:${profile.slot_index}`,
                deviceId: device.id,
                slotIndex: profile.slot_index,
                phoneNumberMasked: profile.phone_number_masked as string,
                label: `${device.name} · SIM ${profile.slot_index + 1} · ${profile.phone_number_masked}`,
              }));
          } catch {
            return [];
          }
        }),
      );
      if (active) setMessagePhoneOptions(nestedOptions.flat());
    };
    void loadPhoneOptions();
    return () => {
      active = false;
    };
  }, [activeTab, adminToken, connected, devices]);

  useEffect(() => {
    if (!messageDeviceId) return;
    const matches = messagePhoneOptions.filter((option) => option.deviceId === messageDeviceId);
    if (matches.length === 1 && (!messagePhone.trim() || messagePhoneFilterKey)) {
      setMessagePhoneFilterKey(matches[0].key);
      setMessagePhone(matches[0].phoneNumberMasked);
      return;
    }
    if (messagePhoneFilterKey && !matches.some((option) => option.key === messagePhoneFilterKey)) {
      setMessagePhoneFilterKey("");
    }
  }, [messageDeviceId, messagePhoneFilterKey, messagePhoneOptions]);

  async function loadAll(token: string, successNotice = "数据已同步") {
    setBusy(true);
    try {
      const headers = { "X-Admin-Token": token };
      const [nextDevices, nextMessages, nextCodes] = await Promise.all([
        fetch("/api/v1/admin/devices", { headers }),
        fetch("/api/v1/admin/messages", { headers }),
        fetch("/api/v1/admin/otps", { headers }),
      ]);
      if (!nextDevices.ok || !nextMessages.ok || !nextCodes.ok) {
        throw new Error("admin_auth_required");
      }
      const loadedDevices = (await nextDevices.json()) as Device[];
      setDevices(loadedDevices);
      setMessages((await nextMessages.json()) as Message[]);
      setCodes((await nextCodes.json()) as AccessCode[]);
      setLiveDeviceId((current) => {
        if (loadedDevices.some((device) => device.id === current && device.active && device.client_type !== "air724")) {
          return current;
        }
        return "";
      });
      try {
        const apiClientsResponse = await fetch("/api/v1/admin/api-clients", { headers });
        if (apiClientsResponse.ok) {
          const apiClients = (await apiClientsResponse.json()) as Partial<ApiClientListResponse>;
          const clients = Array.isArray(apiClients.clients) ? apiClients.clients : [];
          setApiClientToken(clients.find((client) => client.active && client.token)?.token ?? null);
        } else {
          setApiClientToken(null);
        }
      } catch {
        setApiClientToken(null);
      }
      try {
        const runtime = await adminRequest<RuntimeInfo>("/api/v1/admin/runtime", token);
        setRuntimeInfo(isRuntimeInfo(runtime) ? runtime : null);
      } catch {
        setRuntimeInfo(null);
      }
      try {
        const platforms: ReleaseEntry["platform"][] = ["android", "manager", "root"];
        const histories = await Promise.all(platforms.map(async (platform) => {
          const response = await fetch("/api/v1/releases/" + platform + "/history?channel=stable");
          if (!response.ok) return [];
          return releaseEntries(await response.json());
        }));
        setReleaseHistory(histories.flat());
      } catch {
        setReleaseHistory([]);
      }
      sessionStorage.setItem("relay-admin-token", token);
      setConnected(true);
      setNotice(successNotice);
    } catch {
      setNotice("登录会话失效，请重新登录并检查服务状态");
    } finally {
      setBusy(false);
    }
  }

  async function loginAdmin() {
    if (!adminUsername.trim() || !adminPassword) return;
    setBusy(true);
    try {
      const response = await fetch("/api/v1/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: adminUsername.trim(), password: adminPassword }),
      });
      if (!response.ok) throw new Error("admin_auth_required");
      const session = (await response.json()) as AdminSessionResponse;
      if (!session || typeof session.token !== "string" || !session.token.trim()) {
        throw new Error("invalid_admin_session");
      }
      setAdminPassword("");
      setAdminToken(session.token);
      await loadAll(session.token, "登录成功");
    } catch {
      setNotice("登录失败，请检查用户名、密码和服务状态");
      setBusy(false);
    }
  }

  async function updateAdminAccount() {
    if (!accountCurrentPassword) {
      setNotice("请输入当前管理员密码");
      return;
    }
    if (accountPassword && accountPassword !== accountPasswordConfirm) {
      setNotice("两次输入的新密码不一致");
      return;
    }
    if (!accountUsername.trim() && !accountPassword) {
      setNotice("请输入新的用户名或密码");
      return;
    }
    setBusy(true);
    try {
      const session = await adminRequest<AdminSessionResponse>("/api/v1/admin/account", adminToken, {
        method: "PUT",
        body: JSON.stringify({
          current_password: accountCurrentPassword,
          username: accountUsername.trim() || undefined,
          password: accountPassword || undefined,
        }),
      });
      setAdminToken(session.token);
      setShowAccountSettings(false);
      setAccountCurrentPassword("");
      setAccountUsername("");
      setAccountPassword("");
      setAccountPasswordConfirm("");
      await loadAll(session.token, "管理员账号已更新");
    } catch (error) {
      setNotice(`账号更新失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  useEffect(() => {
    const savedToken = sessionStorage.getItem("relay-admin-token")?.trim();
    if (!savedToken) return;
    void loadAll(savedToken, "连接已恢复");
  }, []);

  async function createDevice() {
    if (!deviceName.trim()) {
      setNotice("请输入设备名称");
      return;
    }
    setBusy(true);
    try {
      const authKind = clientOptions.find((option) => option.value === clientType)?.authKind;
      if (!authKind) throw new Error("unsupported_client_type");
      const created = await adminRequest<CredentialResponse>("/api/v1/admin/devices", adminToken, {
        method: "POST",
        body: JSON.stringify({ name: deviceName.trim(), client_type: clientType, auth_kind: authKind }),
      });
      setOneTimeSecretLabel(created.credential.type === "script_api" ? "脚本 API 令牌（仅显示一次）" : "设备鉴权密钥（仅显示一次）");
      setOneTimeSecret(created.credential.secret);
      setShowCreate(false);
      setDeviceName("");
      await loadAll(adminToken, "设备已创建");
    } catch (error) {
      setNotice(`创建失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  async function rotateCredential(device: Device) {
    setBusy(true);
    try {
      const rotated = await adminRequest<CredentialResponse>(
        `/api/v1/admin/devices/${device.id}/rotate-credential`,
        adminToken,
        { method: "POST" },
      );
      setOneTimeSecretLabel(rotated.credential.type === "script_api" ? "脚本 API 令牌（仅显示一次）" : "设备鉴权密钥（仅显示一次）");
      setOneTimeSecret(rotated.credential.secret);
      setNotice("设备密钥已轮换，请立即更新客户端配置");
    } catch (error) {
      setNotice(`轮换失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function generateEnrollmentQr(device: Device) {
    setBusy(true);
    try {
      const response = await adminRequest<EnrollmentQrResponse>(
        `/api/v1/admin/devices/${device.id}/enrollment-qr`,
        adminToken,
        { method: "POST" },
      );
      const svg = await QRCode.toString(response.qr_text, {
        type: "svg",
        margin: 2,
        width: 280,
        errorCorrectionLevel: "M",
      });
      setEnrollmentQr({ ...response, dataUrl: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` });
      setNotice(`已生成“${device.name}”扫码配置，有效期 5 分钟`);
    } catch (error) {
      setNotice(`生成二维码失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function revokeDevice(device: Device) {
    if (!window.confirm(`撤销设备“${device.name}”？`)) return;
    setBusy(true);
    try {
      await adminRequest<void>(`/api/v1/admin/devices/${device.id}`, adminToken, { method: "DELETE" });
      await loadAll(adminToken, "设备已撤销");
    } catch (error) {
      setNotice(`撤销失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  async function restoreDevice(device: Device) {
    setBusy(true);
    try {
      const restored = await adminRequest<CredentialResponse>(
        `/api/v1/admin/devices/${device.id}/restore`,
        adminToken,
        { method: "POST" },
      );
      setOneTimeSecretLabel(restored.credential.type === "script_api" ? "脚本 API 令牌（仅显示一次）" : "设备鉴权密钥（仅显示一次）");
      setOneTimeSecret(restored.credential.secret);
      await loadAll(adminToken, "设备已恢复，旧凭据仍然失效");
    } catch (error) {
      setNotice(`恢复失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  function openPurgeDialog(device: Device) {
    setPurgeTarget(device);
    setPurgeConfirmation("");
  }

  function closePurgeDialog() {
    setPurgeTarget(null);
    setPurgeConfirmation("");
  }

  async function purgeDevice() {
    if (!purgeTarget || purgeConfirmation !== purgeTarget.name) return;
    setBusy(true);
    try {
      await adminRequest<void>(`/api/v1/admin/devices/${purgeTarget.id}/purge`, adminToken, {
        method: "DELETE",
      });
      closePurgeDialog();
      await loadAll(adminToken, "设备及关联数据已永久删除");
    } catch (error) {
      setNotice(`永久删除失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  function resetSimEditor() {
    setSimEditorOpen(false);
    setSimEditingSlot(null);
    setSimSlot("");
    setSimPhoneNumber("");
    setSimClearPhone(false);
  }

  async function openSimProfiles(device: Device) {
    const generation = ++simRequestGeneration.current;
    setSimTarget(device);
    setSimProfiles([]);
    resetSimEditor();
    setSimLoading(true);
    try {
      const profiles = await adminRequest<SimProfile[]>(
        `/api/v1/admin/devices/${device.id}/sim-profiles`,
        adminToken,
      );
      if (generation !== simRequestGeneration.current) return;
      setSimProfiles(profiles);
    } catch (error) {
      if (generation !== simRequestGeneration.current) return;
      setNotice(`手机号加载失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      if (generation === simRequestGeneration.current) setSimLoading(false);
    }
  }

  function closeSimProfiles() {
    simRequestGeneration.current += 1;
    setSimTarget(null);
    setSimProfiles([]);
    setSimLoading(false);
    resetSimEditor();
  }

  function createSimProfile() {
    resetSimEditor();
    const usedSlots = new Set(simProfiles.map((profile) => profile.slot_index));
    let nextSlot = 0;
    while (usedSlots.has(nextSlot)) nextSlot += 1;
    setSimSlot(String(nextSlot));
    setSimEditorOpen(true);
  }

  function editSimProfile(profile: SimProfile) {
    setSimEditorOpen(true);
    setSimEditingSlot(profile.slot_index);
    setSimSlot(String(profile.slot_index));
    setSimPhoneNumber("");
    setSimClearPhone(false);
  }

  async function saveSimProfile() {
    if (!simTarget) return;
    const generation = simRequestGeneration.current;
    const slot = Number(simSlot);
    if (!Number.isInteger(slot) || slot < 0 || !simSlot.trim()) {
      setNotice("槽位必须是非负整数");
      return;
    }
    const payload: {
      phone_number?: string | null;
      source: SimProfileSource;
    } = {
      source: "manual",
    };
    if (simClearPhone) payload.phone_number = null;
    else if (simPhoneNumber.trim()) payload.phone_number = simPhoneNumber;

    setSimLoading(true);
    try {
      const saved = await adminRequest<SimProfile>(
        `/api/v1/admin/devices/${simTarget.id}/sim-profiles/${slot}`,
        adminToken,
        { method: "PUT", body: JSON.stringify(payload) },
      );
      if (generation !== simRequestGeneration.current) return;
      setSimProfiles((current) =>
        [...current.filter((profile) => profile.slot_index !== saved.slot_index), saved].sort(
          (left, right) => left.slot_index - right.slot_index,
        ),
      );
      resetSimEditor();
      setNotice("设备手机号已保存");
    } catch (error) {
      if (generation !== simRequestGeneration.current) return;
      setNotice(`手机号保存失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      if (generation === simRequestGeneration.current) setSimLoading(false);
    }
  }

  async function deleteSimProfile(profile: SimProfile) {
    if (!simTarget || !window.confirm("删除这个设备的手机号映射？")) return;
    const generation = simRequestGeneration.current;
    setSimLoading(true);
    try {
      await adminRequest<void>(
        `/api/v1/admin/devices/${simTarget.id}/sim-profiles/${profile.slot_index}`,
        adminToken,
        { method: "DELETE" },
      );
      if (generation !== simRequestGeneration.current) return;
      setSimProfiles((current) =>
        current.filter((item) => item.slot_index !== profile.slot_index),
      );
      if (simEditingSlot === profile.slot_index) resetSimEditor();
      setNotice("设备手机号映射已删除");
    } catch (error) {
      if (generation !== simRequestGeneration.current) return;
      setNotice(`手机号删除失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      if (generation === simRequestGeneration.current) setSimLoading(false);
    }
  }

  async function runRetention() {
    setBusy(true);
    try {
      const result = await adminRequest<{ redacted_messages: number }>(
        "/api/v1/admin/retention/run",
        adminToken,
        { method: "POST" },
      );
      await loadAll(adminToken, `已清理 ${result.redacted_messages} 条过期正文`);
    } catch (error) {
      setNotice(`清理失败：${error instanceof Error ? error.message : "unknown_error"}`);
      setBusy(false);
    }
  }

  async function filterMessages() {
    const params = new URLSearchParams({ limit: "100", offset: "0" });
    const selectedPhone = messagePhoneOptions.find((option) => option.key === messagePhoneFilterKey);
    if (selectedPhone) {
      params.set("device_id", selectedPhone.deviceId);
      params.set("sim_slot", String(selectedPhone.slotIndex));
    } else if (messagePhone.trim()) {
      params.set("phone_number", messagePhone.trim());
    }
    if (messageSender.trim()) params.set("sender", messageSender.trim());
    if (messageKeyword.trim()) params.set("keyword", messageKeyword.trim());
    if (messageDeviceId && !selectedPhone) params.set("device_id", messageDeviceId);
    if (messageReadStatus) params.set("read", messageReadStatus);
    setBusy(true);
    try {
      const filtered = await adminRequest<Message[]>(
        `/api/v1/admin/messages?${params.toString()}`,
        adminToken,
      );
      setMessages(filtered);
      setSelectedMessageIds(new Set());
      setNotice(`已筛选 ${filtered.length} 条消息`);
    } catch (error) {
      setNotice(`筛选失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function resetMessageFilters() {
    setMessagePhone("");
    setMessagePhoneFilterKey("");
    setMessageSender("");
    setMessageKeyword("");
    setMessageDeviceId("");
    setMessageReadStatus("");
    setSelectedMessageIds(new Set());
    setBusy(true);
    try {
      const nextMessages = await adminRequest<Message[]>("/api/v1/admin/messages", adminToken);
      setMessages(nextMessages);
      setNotice("消息筛选已清除");
    } catch (error) {
      setNotice(`刷新失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function setMessageRead(message: Message, read: boolean) {
    setBusy(true);
    try {
      const updated = await adminRequest<Message>(
        `/api/v1/admin/messages/${message.id}/${read ? "read" : "unread"}`,
        adminToken,
        { method: "POST" },
      );
      setMessages((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setNotice(read ? "消息已标为已读" : "消息已标为未读");
    } catch (error) {
      setNotice(`状态更新失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteMessage(message: Message) {
    if (!window.confirm("确定删除这条消息及其取码记录？")) return;
    setBusy(true);
    try {
      await adminRequest<void>(`/api/v1/admin/messages/${message.id}`, adminToken, { method: "DELETE" });
      setMessages((current) => current.filter((item) => item.id !== message.id));
      setSelectedMessageIds((current) => {
        const next = new Set(current);
        next.delete(message.id);
        return next;
      });
      setNotice("消息已删除");
    } catch (error) {
      setNotice(`删除失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelectedMessages() {
    const ids = [...selectedMessageIds];
    if (!ids.length) return;
    if (!window.confirm(`确定删除已选的 ${ids.length} 条消息及其取码记录？`)) return;
    setBusy(true);
    try {
      const result = await adminRequest<{ deleted: number }>("/api/v1/admin/messages/bulk-delete", adminToken, {
        method: "POST",
        body: JSON.stringify({ message_ids: ids }),
      });
      const selected = new Set(ids);
      setMessages((current) => current.filter((item) => !selected.has(item.id)));
      setSelectedMessageIds(new Set());
      setNotice(`已删除 ${result.deleted} 条消息`);
    } catch (error) {
      setNotice(`批量删除失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  function toggleMessageSelection(messageId: string) {
    setSelectedMessageIds((current) => {
      const next = new Set(current);
      if (next.has(messageId)) next.delete(messageId);
      else next.add(messageId);
      return next;
    });
  }

  function toggleAllVisibleMessages() {
    setSelectedMessageIds((current) => {
      const next = new Set(current);
      const allSelected = messages.length > 0 && messages.every((message) => next.has(message.id));
      messages.forEach((message) => {
        if (allSelected) next.delete(message.id);
        else next.add(message.id);
      });
      return next;
    });
  }

  function openMessageSimMapping(message: Message) {
    const device = devices.find((item) => item.id === message.device_id);
    if (!device) {
      setNotice("消息关联设备不存在");
      return;
    }
    void openSimProfiles(device);
  }

  async function createApiClient() {
    setBusy(true);
    try {
      const result = await adminRequest<{ token: string }>("/api/v1/admin/api-clients", adminToken, {
        method: "POST",
      });
      setApiClientToken(result.token);
      setNotice("脚本 API 令牌已创建");
    } catch (error) {
      setNotice(`创建失败：${error instanceof Error ? error.message : "unknown_error"}`);
    } finally {
      setBusy(false);
    }
  }

  function disconnect() {
    sessionStorage.removeItem("relay-admin-token");
    setConnected(false);
    setDevices([]);
    setMessages([]);
    setCodes([]);
    setRuntimeInfo(null);
    setReleaseHistory([]);
    setLiveDeviceId("");
    setLiveSimSlot(null);
    setMessagePhoneOptions([]);
    setMessagePhoneFilterKey("");
    setMessagePhone("");
    setSelectedMessageIds(new Set());
    setBulkMessageMode(false);
    closeSimProfiles();
    setOneTimeSecret(null);
    setApiClientToken(null);
    setAdminPassword("");
    setShowAccountSettings(false);
    setNotice("已断开");
  }

  function selectTab(tab: Tab) {
    setActiveTab(tab);
    if (tab === "messages") {
      setLiveDeviceId("");
      setLiveSimSlot(null);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <ShieldCheck aria-hidden="true" size={24} />
          <div>
            <strong>Relay Hub</strong>
            <span>运营控制台</span>
          </div>
        </div>
        {connected && (
          <div className="topbar-actions">
            <button className="icon-button" onClick={() => setShowAccountSettings(true)} disabled={busy} title="账号设置" aria-label="账号设置">
              <Settings size={18} />
            </button>
            <button className="icon-button" onClick={() => void loadAll(adminToken)} disabled={busy} title="刷新数据" aria-label="刷新数据">
              <RefreshCw size={18} />
            </button>
            <button className="icon-button" onClick={disconnect} title="断开连接" aria-label="断开连接">
              <LogOut size={18} />
            </button>
          </div>
        )}
      </header>

      {!connected ? (
        <section className="connect-panel" aria-labelledby="connect-title">
          <div>
            <p className="eyebrow">ADMIN ACCESS</p>
            <h1 id="connect-title">连接管理接口</h1>
          </div>
          <div className="connect-login-grid">
            <div>
              <label htmlFor="admin-username">管理员用户名</label>
              <input
                id="admin-username"
                value={adminUsername}
                onChange={(event) => setAdminUsername(event.target.value)}
                autoComplete="username"
              />
            </div>
            <div>
              <label htmlFor="admin-password">管理员密码</label>
              <input
                id="admin-password"
                type="password"
                value={adminPassword}
                onChange={(event) => setAdminPassword(event.target.value)}
                autoComplete="current-password"
                onKeyDown={(event) => {
                  if (event.key === "Enter") void loginAdmin();
                }}
              />
            </div>
          </div>
          <button className="primary-button login-button" onClick={() => void loginAdmin()} disabled={busy || !adminUsername.trim() || !adminPassword}>
            登录
          </button>
          <p role="status" className="status-line">{notice}</p>
        </section>
      ) : (
        <>
          <div className="workspace-heading">
            <div>
              <p className="eyebrow">OPERATIONS</p>
              <h1>消息中转</h1>
            </div>
            <p role="status" className="status-line">{busy ? "处理中" : notice}</p>
          </div>

          <nav className="tabs" role="tablist" aria-label="管理视图">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  className={activeTab === tab.id ? "tab active" : "tab"}
                  onClick={() => selectTab(tab.id)}
                >
                  <Icon size={17} />
                  {tab.label}
                </button>
              );
            })}
          </nav>

          {oneTimeSecret && (
            <section className="credential-banner" aria-live="polite">
              <KeyRound size={20} aria-hidden="true" />
              <div>
                <strong>{oneTimeSecretLabel}</strong>
                <code>{oneTimeSecret}</code>
              </div>
              <button className="icon-button" onClick={() => void navigator.clipboard?.writeText(oneTimeSecret)} title="复制凭据" aria-label="复制凭据">
                <Copy size={18} />
              </button>
              <button className="icon-button" onClick={() => setOneTimeSecret(null)} title="关闭" aria-label="关闭凭据">
                <X size={18} />
              </button>
            </section>
          )}

          {activeTab === "overview" && (
            <section aria-labelledby="overview-title">
              <div className="section-heading">
                <h2 id="overview-title">运行概况</h2>
                <button className="secondary-button" onClick={() => void runRetention()} disabled={busy}>
                  <Database size={17} /> 执行保留清理
                </button>
              </div>
              <div className="stats-grid">
                <Stat label="在线设备" value={onlineDevices} tone="green" />
                <Stat label="已接收消息" value={messages.length} tone="blue" />
                <Stat label="可领取取码" value={availableCodes} tone="amber" />
              </div>
              {runtimeInfo && <RuntimePanel runtime={runtimeInfo} devices={devices} baseUrl="" />}
              <div className="section-heading compact">
                <h2>接口凭据</h2>
                <div className="section-heading-actions">
                  <button className="secondary-button" onClick={() => setShowApiDoc(true)}>
                    <BookOpen size={17} /> API 调用说明
                  </button>
                  <button className="secondary-button" onClick={() => void createApiClient()} disabled={busy}>
                    <Plus size={17} /> 新建脚本令牌
                  </button>
                </div>
              </div>
              <p className="credential-help">
                脚本 API 令牌用于外部脚本调用取码接口；不用于设备上报，也不能填入手机模块。
              </p>
              {apiClientToken && (
                <div className="api-token-card" aria-label="脚本 API 令牌">
                  <div className="api-token-card-heading">
                    <strong>脚本 API 令牌</strong>
                    <span>长期有效，直到你停用或重新创建</span>
                  </div>
                  <div className="api-token-value">
                    <code>{apiClientToken}</code>
                    <button className="icon-button" onClick={() => void navigator.clipboard?.writeText(apiClientToken)} title="复制脚本 API 令牌" aria-label="复制脚本 API 令牌">
                      <Copy size={18} />
                    </button>
                  </div>
                  <p className="credential-help">服务端已加密保存，刷新页面或重启服务后仍可显示。旧令牌若未加密保存，需要重新创建。</p>
                </div>
              )}
              <div className="api-contract" aria-label="脚本 API 地址和调用方式">
                <div className="api-contract-heading">
                  <strong>脚本 API 地址</strong>
                  <code>{window.location.origin}</code>
                </div>
                <p>只用于外部脚本取码；设备上报仍由手机模块使用设备鉴权密钥完成。</p>
                <p className="credential-help">手机号填写规则：脚本传中国大陆 11 位手机号即可，例如 <code>13800138000</code>；平台内部会统一保存为 <code>+8613800138000</code>，三种写法可互相匹配。</p>
                <div className="api-contract-row">
                  <code>{window.location.origin}/api/v1/otp/claim.txt?phone_number=%2B8613800138000</code>
                  <span>返回纯文本取码</span>
                </div>
                <div className="api-contract-row">
                  <code>{window.location.origin}/api/v1/otp/claim</code>
                  <span>返回 JSON 取码</span>
                </div>
                <div className="api-contract-row">
                  <code>{window.location.origin.replace(/^http/, "ws")}/api/v1/ws/otp</code>
                  <span>WebSocket 实时取码</span>
                </div>
                <code className="api-contract-header">Authorization: Bearer {apiClientToken ?? "&lt;脚本 API 令牌&gt;"}</code>
                <code className="api-contract-header">POST JSON: {'{"phone_number":"+8613800138000","sender":"1069","keyword":"验证码"}'}</code>
                <p className="api-contract-example">
                  合宙 724 等外部脚本优先连接 WebSocket 等待推送；服务端只推送订阅手机号的新取码事件，不发送短信正文。脚本 API 令牌只用于 WebSocket 鉴权和断线后的 HTTP Claim 补取；正常连接不轮询接口。没有新码时 Claim 返回 404，不会回退到旧的已领取或已过期记录。
                </p>
              </div>
              <ReleaseHistoryPanel releases={releaseHistory} />
            </section>
          )}

          {activeTab === "devices" && (
            <section aria-labelledby="devices-title">
              <div className="section-heading">
                <div>
                  <h2 id="devices-title">设备</h2>
                  <span className="section-count">{devices.length} 条记录</span>
                </div>
                <div className="section-heading-actions">
                  <button className="secondary-button" onClick={() => setShowTutorial(true)}>
                    <BookOpen size={17} /> 演示教程
                  </button>
                  <button className="primary-button" onClick={() => setShowCreate(true)}>
                    <Plus size={17} /> 添加设备
                  </button>
                </div>
              </div>
              <DataTable headers={["名称", "客户端", "鉴权", "状态", "运行信息", "告警", "最近活动", "操作"]} empty="没有已注册设备">
                {devices.map((device) => (
                  <tr key={device.id}>
                    <td><strong>{device.name}</strong><small>{device.id}</small></td>
                    <td>{clientLabels[device.client_type]}</td>
                    <td><code>{authLabels[device.auth_kind]}</code></td>
                    <td>
                      <StatusBadge status={device.active ? "active" : "revoked"} />
                      {device.active && <span className={device.online ? "device-online" : "device-offline"}>{device.online ? "在线" : "离线"}</span>}
                    </td>
                    <td>
                      <strong>{device.client_version ?? "未上报版本"}</strong>
                      <small>{device.client_mode ?? clientLabels[device.client_type]}</small>
                      {device.client_instance_id && <code>{device.client_instance_id}</code>}
                    </td>
                    <td className="device-warning-cell">
                      {device.credential_reuse_warning && <span className="runtime-warning">凭据多实例</span>}
                      {device.duplicate_collection_warning && <span className="runtime-warning">重复采集</span>}
                      {!device.credential_reuse_warning && !device.duplicate_collection_warning && <span>无</span>}
                    </td>
                    <td>{formatTime(device.last_seen_at)}</td>
                    <td className="row-actions">
                      {(device.client_type === "android_app" || device.client_type === "root_module") && device.active && (
                        <button className="icon-button qr-action" onClick={() => void generateEnrollmentQr(device)} disabled={busy} title="生成扫码配置" aria-label={`生成扫码配置 ${device.name}`}>
                          <Radio size={17} />
                          <span>二维码</span>
                        </button>
                      )}
                      {device.client_type !== "air724" && (
                        <button className="icon-button" onClick={() => void openSimProfiles(device)} disabled={busy} title="设备手机号" aria-label={`设备手机号 ${device.name}`}>
                          <CreditCard size={17} />
                        </button>
                      )}
                      <button className="icon-button" onClick={() => void rotateCredential(device)} disabled={!device.active || busy} title="轮换凭据" aria-label={`轮换 ${device.name}`}>
                        <RotateCw size={17} />
                      </button>
                      <button className="icon-button danger" onClick={() => void revokeDevice(device)} disabled={!device.active || busy} title="撤销设备" aria-label={`撤销 ${device.name}`}>
                        <Ban size={17} />
                      </button>
                      {!device.active && (
                        <>
                          <button className="icon-button" onClick={() => void restoreDevice(device)} disabled={busy} title="恢复设备" aria-label={`恢复 ${device.name}`}>
                            <Undo2 size={17} />
                          </button>
                          <button className="icon-button danger" onClick={() => openPurgeDialog(device)} disabled={busy} title="永久删除" aria-label={`永久删除 ${device.name}`}>
                            <Trash2 size={17} />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </DataTable>
            </section>
          )}

          {activeTab === "messages" && (
            <section aria-labelledby="messages-title">
              <div className="section-heading"><div><h2 id="messages-title">消息</h2><span className="section-count">{messages.length} 条记录</span></div></div>
              <LiveMessagePanel
                baseUrl=""
                token={adminToken}
                devices={devices}
                selectedDeviceId={liveDeviceId}
                selectedSimSlot={liveSimSlot}
                onDeviceChange={setLiveDeviceId}
                onSimSlotChange={setLiveSimSlot}
                onUnauthorized={disconnect}
              />
              <div className="message-filters">
                <label>
                  <span>接收手机号</span>
                  <input
                    value={messagePhone}
                    onChange={(event) => {
                      setMessagePhoneFilterKey("");
                      setMessagePhone(event.target.value);
                    }}
                    autoComplete="off"
                  />
                </label>
                <label>
                  <span>选择已绑定手机号</span>
                  <select
                    value={messagePhoneFilterKey}
                    onChange={(event) => {
                      const key = event.target.value;
                      const option = messagePhoneOptions.find((item) => item.key === key);
                      setMessagePhoneFilterKey(key);
                      setMessagePhone(option?.phoneNumberMasked ?? "");
                    }}
                  >
                    <option value="">手动输入或不限定</option>
                    {visibleMessagePhoneOptions.map((option) => (
                      <option key={option.key} value={option.key}>{option.label}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>发送方</span>
                  <input value={messageSender} onChange={(event) => setMessageSender(event.target.value)} autoComplete="off" />
                </label>
                <label>
                  <span>正文关键词</span>
                  <input value={messageKeyword} onChange={(event) => setMessageKeyword(event.target.value)} autoComplete="off" />
                </label>
                <label>
                  <span>设备筛选</span>
                  <select
                    value={messageDeviceId}
                    onChange={(event) => {
                      const deviceId = event.target.value;
                      setMessageDeviceId(deviceId);
                      if (messagePhoneFilterKey) {
                        const selected = messagePhoneOptions.find((option) => option.key === messagePhoneFilterKey);
                        if (selected && selected.deviceId !== deviceId) {
                          setMessagePhoneFilterKey("");
                          setMessagePhone("");
                        }
                      }
                    }}
                  >
                    <option value="">全部设备</option>
                    {devices.map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}
                  </select>
                </label>
                <label>
                  <span>已读状态</span>
                  <select value={messageReadStatus} onChange={(event) => setMessageReadStatus(event.target.value)}>
                    <option value="">全部</option>
                    <option value="false">未读</option>
                    <option value="true">已读</option>
                  </select>
                </label>
                <div className="message-filter-actions">
                  <button className="primary-button" onClick={() => void filterMessages()} disabled={busy} aria-label="筛选消息">
                    <Search size={17} /> 筛选
                  </button>
                  <button className="secondary-button" onClick={() => void resetMessageFilters()} disabled={busy} aria-label="清除消息筛选">
                    <X size={17} /> 清除
                  </button>
                </div>
              </div>
              <div className="bulk-message-toolbar">
                <button className="secondary-button" onClick={() => setBulkMessageMode((current) => !current)} disabled={busy}>
                  {bulkMessageMode ? "退出批量操作" : "批量操作"}
                </button>
                {bulkMessageMode && (
                  <>
                    <button className="secondary-button" onClick={toggleAllVisibleMessages} disabled={busy || messages.length === 0}>
                      {messages.length > 0 && messages.every((message) => selectedMessageIds.has(message.id)) ? "取消全选" : "全选"}
                    </button>
                    <span className="bulk-message-count">已选 {selectedMessageIds.size} 条</span>
                    <button className="danger-button" onClick={() => void deleteSelectedMessages()} disabled={busy || selectedMessageIds.size === 0}>
                      <Trash2 size={17} /> 批量删除
                    </button>
                  </>
                )}
              </div>
              <DataTable headers={[...(bulkMessageMode ? ["选择"] : []), "短信时间", "接收时间", "接收号码", "发送方", "内容", "状态", "操作"]} empty="暂无消息">
                {messages.map((message) => (
                  <tr key={message.id}>
                    {bulkMessageMode && (
                      <td>
                        <input
                          type="checkbox"
                          checked={selectedMessageIds.has(message.id)}
                          onChange={() => toggleMessageSelection(message.id)}
                          aria-label={`选择消息 ${message.id}`}
                        />
                      </td>
                    )}
                    <td>{formatTime(message.message_date ?? message.received_at)}</td>
                    <td>{formatTime(message.received_at)}</td>
                    <td><code>{message.receiving_phone_masked ?? "未设置"}</code></td>
                    <td>{message.sender}</td>
                    <td className="message-body">{message.body ?? "已按保留策略清理"}</td>
                    <td className="message-statuses">
                      <StatusBadge status={message.otp_status === "claimed" ? "claimed" : message.read_at ? "read" : "unread"} />
                    </td>
                    <td className="row-actions">
                      {message.receiving_identity_status !== "resolved" && (
                        <button className="icon-button" onClick={() => openMessageSimMapping(message)} disabled={busy} title="设置接收手机号" aria-label={`设置 ${message.id} 的接收手机号`}>
                          <CreditCard size={17} />
                        </button>
                      )}
                      <button className="icon-button" onClick={() => void setMessageRead(message, !message.read_at)} disabled={busy} title={message.read_at ? "标为未读" : "标为已读"} aria-label={`标记 ${message.id} 为${message.read_at ? "未读" : "已读"}`}>
                        {message.read_at ? <Mail size={17} /> : <MailOpen size={17} />}
                      </button>
                      <button className="icon-button danger" onClick={() => void deleteMessage(message)} disabled={busy} title="删除消息" aria-label={`删除消息 ${message.id}`}>
                        <Trash2 size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </DataTable>
            </section>
          )}

          {activeTab === "codes" && (
            <section aria-labelledby="codes-title">
              <div className="section-heading"><div><h2 id="codes-title">取码记录</h2><span className="section-count">{codes.length} 条记录</span></div></div>
              <DataTable headers={["状态", "过期时间", "取码记录 ID", "来源消息 ID"]} empty="暂无取码记录">
                {codes.map((code) => (
                  <tr key={code.id}>
                    <td><StatusBadge status={code.status} /></td>
                    <td>{formatTime(code.expires_at)}</td>
                    <td><code>{code.id}</code></td>
                    <td><code>{code.message_id}</code></td>
                  </tr>
                ))}
              </DataTable>
            </section>
          )}
          {activeTab === "forwarding" && (
            <ForwardingPanel token={adminToken} />
          )}
          {activeTab === "commands" && (
            <DeviceCommandsPanel token={adminToken} devices={devices} />
          )}
        </>
      )}

      {showCreate && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowCreate(false)}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="create-device-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="section-heading compact">
              <h2 id="create-device-title">添加设备</h2>
              <button className="icon-button" onClick={() => setShowCreate(false)} title="关闭" aria-label="关闭窗口"><X size={18} /></button>
            </div>
            <label htmlFor="device-name">设备名称</label>
            <input id="device-name" value={deviceName} onChange={(event) => setDeviceName(event.target.value)} autoFocus />
            <label htmlFor="client-type">客户端</label>
            <select id="client-type" value={clientType} onChange={(event) => setClientType(event.target.value as Device["client_type"])}>
              {clientOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <div className="modal-actions">
              <button className="secondary-button" onClick={() => setShowCreate(false)}>取消</button>
              <button className="primary-button" onClick={() => void createDevice()} disabled={busy}>创建设备</button>
            </div>
          </section>
        </div>
      )}

      {enrollmentQr && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setEnrollmentQr(null)}>
          <section className="modal enrollment-qr-modal" role="dialog" aria-modal="true" aria-labelledby="enrollment-qr-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="section-heading compact">
              <h2 id="enrollment-qr-title">扫码配置设备</h2>
              <button className="icon-button" onClick={() => setEnrollmentQr(null)} title="关闭" aria-label="关闭扫码配置窗口"><X size={18} /></button>
            </div>
            <p className="credential-help">设备：{enrollmentQr.device.name}。Android 采集端请点击“扫描后台二维码自动配置”；Root 模块请按模块说明导入扫码内容完成接入。</p>
            <img className="enrollment-qr-image" src={enrollmentQr.dataUrl} alt={`${enrollmentQr.device.name}设备配置二维码`} />
            <p className="enrollment-qr-expiry">二维码仅有效 {Math.round(enrollmentQr.ttl_seconds / 60)} 分钟，且只能成功兑换一次；二维码中不包含长期 HMAC 密钥。</p>
            <details>
              <summary>查看扫码内容（排查网络时使用）</summary>
              <code className="enrollment-qr-text">{enrollmentQr.qr_text}</code>
            </details>
            <div className="modal-actions">
              <button className="secondary-button" onClick={() => void navigator.clipboard?.writeText(enrollmentQr.qr_text)}><Copy size={17} /> 复制扫码内容</button>
              <button className="primary-button" onClick={() => setEnrollmentQr(null)}>完成</button>
            </div>
          </section>
        </div>
      )}

      {showTutorial && <DeviceTutorial onClose={() => setShowTutorial(false)} />}
      {showApiDoc && <ApiDocModal onClose={() => setShowApiDoc(false)} />}

      {showAccountSettings && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setShowAccountSettings(false)}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="account-settings-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="section-heading compact">
              <h2 id="account-settings-title">管理员账号设置</h2>
              <button className="icon-button" onClick={() => setShowAccountSettings(false)} title="关闭" aria-label="关闭账号设置"><X size={18} /></button>
            </div>
            <p className="credential-help">修改后当前会话会自动更新，旧会话立即失效。</p>
            <label htmlFor="account-current-password">当前密码</label>
            <input id="account-current-password" type="password" value={accountCurrentPassword} onChange={(event) => setAccountCurrentPassword(event.target.value)} autoComplete="current-password" autoFocus />
            <label htmlFor="account-username">新用户名（可选）</label>
            <input id="account-username" value={accountUsername} onChange={(event) => setAccountUsername(event.target.value)} autoComplete="username" />
            <label htmlFor="account-password">新密码（可选，至少 12 位）</label>
            <input id="account-password" type="password" value={accountPassword} onChange={(event) => setAccountPassword(event.target.value)} autoComplete="new-password" />
            <label htmlFor="account-password-confirm">确认新密码</label>
            <input id="account-password-confirm" type="password" value={accountPasswordConfirm} onChange={(event) => setAccountPasswordConfirm(event.target.value)} autoComplete="new-password" />
            <div className="modal-actions">
              <button className="secondary-button" onClick={() => setShowAccountSettings(false)}>取消</button>
              <button className="primary-button" onClick={() => void updateAdminAccount()} disabled={busy}>保存设置</button>
            </div>
          </section>
        </div>
      )}

      {purgeTarget && (
        <div className="modal-backdrop" role="presentation" onMouseDown={closePurgeDialog}>
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="purge-device-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="section-heading compact">
              <h2 id="purge-device-title">永久删除设备</h2>
              <button className="icon-button" onClick={closePurgeDialog} title="关闭" aria-label="关闭永久删除窗口"><X size={18} /></button>
            </div>
            <p className="destructive-note">设备、关联消息和取码记录将被永久删除。</p>
            <label htmlFor="purge-confirmation">输入设备名称以确认</label>
            <div className="confirm-name-row">
              <input
                id="purge-confirmation"
                value={purgeConfirmation}
                onChange={(event) => setPurgeConfirmation(event.target.value)}
                autoComplete="off"
                autoFocus
              />
              <button
                type="button"
                className="secondary-button"
                onClick={() => setPurgeConfirmation(purgeTarget.name)}
              >
                <Copy size={17} /> 自动填入当前设备名称
              </button>
            </div>
            <div className="modal-actions">
              <button className="secondary-button" onClick={closePurgeDialog}>取消</button>
              <button
                className="danger-button"
                onClick={() => void purgeDevice()}
                disabled={busy || purgeConfirmation !== purgeTarget.name}
              >
                确认永久删除
              </button>
            </div>
          </section>
        </div>
      )}

      {simTarget && (
        <div className="modal-backdrop" role="presentation" onMouseDown={closeSimProfiles}>
          <section className="modal sim-modal" role="dialog" aria-modal="true" aria-labelledby="sim-profile-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="section-heading compact">
              <div>
                <h2 id="sim-profile-title">{simTarget.name} 手机号</h2>
                <span className="section-count">只保存接收手机号</span>
              </div>
              <button className="icon-button" onClick={closeSimProfiles} title="关闭" aria-label="关闭设备手机号窗口"><X size={18} /></button>
            </div>

            <div className="sim-toolbar">
              <button className="secondary-button" onClick={createSimProfile} disabled={simLoading}>
                <Plus size={17} /> 设置手机号
              </button>
            </div>

            {simLoading && simProfiles.length === 0 ? (
              <p className="empty-state">正在加载设备手机号</p>
            ) : (
              <DataTable headers={["SIM", "手机号", "操作"]} empty="尚未检测到设备 SIM">
                {simProfiles.map((profile) => (
                  <tr key={profile.slot_index}>
                    <td>
                      <strong>SIM {profile.slot_index + 1}</strong>
                      <small>
                        {profile.subscription_id === null
                          ? "Subscription 未识别"
                          : `Subscription ${profile.subscription_id}`}
                      </small>
                    </td>
                    <td><code>{profile.phone_number_masked ?? "未设置"}</code></td>
                    <td className="row-actions">
                      <button className="icon-button" onClick={() => editSimProfile(profile)} disabled={simLoading} title="编辑手机号" aria-label={`编辑 SIM ${profile.slot_index + 1} 手机号`}>
                        <Pencil size={17} />
                      </button>
                      <button className="icon-button danger" onClick={() => void deleteSimProfile(profile)} disabled={simLoading} title="删除手机号" aria-label={`删除 SIM ${profile.slot_index + 1} 手机号`}>
                        <Trash2 size={17} />
                      </button>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}

            {simEditorOpen && (
              <div className="sim-editor" aria-label={simEditingSlot === null ? "设置设备手机号" : "编辑设备手机号"}>
                <div className="section-heading compact">
                  <h3>
                    {simEditingSlot === null
                      ? `设置 SIM ${Number(simSlot) + 1} 手机号`
                      : `编辑 SIM ${simEditingSlot + 1} 手机号`}
                  </h3>
                  <button className="icon-button" onClick={resetSimEditor} title="关闭编辑" aria-label="关闭 SIM 编辑"><X size={17} /></button>
                </div>
                <div className="sim-form-grid">
                  <div className="sim-phone-field">
                    <label htmlFor="sim-phone">{simEditingSlot === null ? "设备手机号" : "手机号（留空保持不变）"}</label>
                    <input id="sim-phone" type="password" value={simPhoneNumber} onChange={(event) => setSimPhoneNumber(event.target.value)} disabled={simClearPhone} autoComplete="off" />
                    {simEditingSlot !== null && simProfiles.find((profile) => profile.slot_index === simEditingSlot)?.phone_number_masked && (
                      <span className="sim-current-phone">当前：{simProfiles.find((profile) => profile.slot_index === simEditingSlot)?.phone_number_masked}</span>
                    )}
                  </div>
                </div>
                {simEditingSlot !== null && simProfiles.find((profile) => profile.slot_index === simEditingSlot)?.phone_number_masked && (
                  <label className="checkbox-row">
                    <input type="checkbox" checked={simClearPhone} onChange={(event) => setSimClearPhone(event.target.checked)} />
                    清空现有手机号
                  </label>
                )}
                <div className="modal-actions">
                  <button className="secondary-button" onClick={resetSimEditor}>取消</button>
                  <button className="primary-button" onClick={() => void saveSimProfile()} disabled={simLoading}>保存映射</button>
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: "green" | "blue" | "amber" }) {
  return <article className={`stat ${tone}`}><span>{label}</span><strong>{value}</strong></article>;
}

function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = {
    active: "启用",
    revoked: "已撤销",
    stored: "已保存",
    redacted: "已清理",
    available: "可用",
    claimed: "已领取",
    resolved: "已解析",
    unresolved: "未解析",
    conflict: "映射冲突",
    read: "已读",
    unread: "未读",
    expired: "已过期",
  };
  return <span className={`status-badge ${status}`}>{labels[status] ?? status}</span>;
}

function DataTable({ headers, empty, children }: { headers: string[]; empty: string; children: React.ReactNode }) {
  const rows = Array.isArray(children) ? children.length : children ? 1 : 0;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>{rows ? children : <tr><td className="empty-state" colSpan={headers.length}>{empty}</td></tr>}</tbody>
      </table>
    </div>
  );
}

function DeviceTutorial({ onClose }: { onClose: () => void }) {
  const [tutorialTab, setTutorialTab] = useState<"androidApp" | "androidRoot" | "ios" | "air724">("androidApp");
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal tutorial-modal" role="dialog" aria-modal="true" aria-labelledby="device-tutorial-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="section-heading compact">
          <div>
            <h2 id="device-tutorial-title">设备接入演示教程</h2>
            <span className="section-count">Android 应用、Root 模块、iPhone 快捷指令与合宙 724 取码</span>
          </div>
          <button className="icon-button" onClick={onClose} title="关闭教程" aria-label="关闭设备教程窗口"><X size={18} /></button>
        </div>
        <div className="tutorial-tabs" role="tablist" aria-label="设备教程类型">
          <button role="tab" aria-selected={tutorialTab === "androidApp"} className={tutorialTab === "androidApp" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setTutorialTab("androidApp")}><Smartphone size={16} /> Android 应用</button>
          <button role="tab" aria-selected={tutorialTab === "androidRoot"} className={tutorialTab === "androidRoot" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setTutorialTab("androidRoot")}><Smartphone size={16} /> Android Root 模块</button>
          <button role="tab" aria-selected={tutorialTab === "ios"} className={tutorialTab === "ios" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setTutorialTab("ios")}><Smartphone size={16} /> iPhone 快捷指令</button>
          <button role="tab" aria-selected={tutorialTab === "air724"} className={tutorialTab === "air724" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setTutorialTab("air724")}><Radio size={16} /> 合宙 724 取码</button>
        </div>
        {tutorialTab === "androidApp" ? <AndroidAppTutorial /> : tutorialTab === "androidRoot" ? <AndroidRootTutorial /> : tutorialTab === "ios" ? <IosTutorial /> : <Air724Tutorial />}
        <div className="modal-actions"><button className="secondary-button" onClick={onClose}>关闭</button></div>
      </section>
    </div>
  );
}

function ApiTutorial() {
  return (
    <div className="tutorial-content">
      <p className="tutorial-lead">脚本取码只用两个接口：WebSocket 负责实时推送新验证码，HTTP Claim 只在断线重连后补取一次。两者共用同一个脚本 API 令牌。</p>
      <div className="tutorial-warning">脚本 API 令牌只用于取码，不能填进手机应用或 Root 模块（设备上报用的是设备鉴权密钥）。手机号按中国大陆 11 位填写即可，平台内部统一保存为 <code>+86</code> 开头的 E.164 形式。</div>
      <section className="tutorial-section" aria-labelledby="api-ws-title">
        <h3 id="api-ws-title">1. WebSocket 实时取码</h3>
        <p className="tutorial-note">连接地址 <code>{window.location.origin.replace(/^http/, "ws")}/api/v1/ws/otp</code>（就是当前后台域名，HTTPS 站点自动是 <code>wss://</code>）。不要自己拼服务端应用端口，对外只发布反向代理端口。连接后必须按顺序发两帧：先鉴权，再订阅。</p>
        <ol>
          <li><strong>鉴权帧</strong>：连接建立后 10 秒内发送，服务端回 <code>{"{"}"type":"auth_ok"{"}"}</code>。</li>
          <li><strong>订阅帧</strong>：鉴权成功后 10 秒内发送，服务端回 <code>subscribed</code> 并带上规范化后的手机号。</li>
          <li><strong>等待推送</strong>：平台收到新短信并解析出验证码后立即推送；一条连接只允许一个订阅。</li>
          <li><strong>保活</strong>：空闲 3 分钟会被关闭，期间发 <code>{"{"}"type":"ping"{"}"}</code> 可续期，服务端回 <code>pong</code>。</li>
        </ol>
      </section>
      <pre className="tutorial-code"><code>{[
          '// 1. 鉴权帧',
          '{"type":"auth","token":"srh_api_你的令牌"}',
          '',
          '// 2. 订阅帧（phone_number 必填，其余可选）',
          '{"type":"subscribe","phone_number":"13800138000","sender":"","keyword":""}',
          '',
          '// 3. 服务端推送（新验证码到达时）',
          '{"type":"otp","otp_id":"...","message_id":"...","code":"583142",',
          ' "phone_number":"+8613800138000","sender":"10687346...",',
          ' "created_at":"...","received_at":"...","expires_at":"..."}',
        ].join("\n")}</code></pre>
      <section className="tutorial-section" aria-labelledby="api-claim-title">
        <h3 id="api-claim-title">2. HTTP Claim 补取</h3>
        <p className="tutorial-note">仅在 WS 断线重连后调用一次，取回断线期间产生的最新验证码。正常连接期间不需要调用。</p>
        <ol>
          <li><code>POST /api/v1/otp/claim</code>，请求头 <code>Authorization: Bearer 你的令牌</code>。</li>
          <li>请求体 JSON：<code>{"{"}"phone_number":"13800138000"{"}"}</code>；可选加 <code>sender</code> 和 <code>keyword</code> 筛选。</li>
          <li>返回 200 + <code>{"{"}"code":"583142","otp_id":"...","expires_at":"..."{"}"}</code>。</li>
          <li>没有可用验证码时返回 404，继续保持 WebSocket 等待即可。</li>
        </ol>
        <p className="tutorial-note">纯文本取码：<code>GET /api/v1/otp/claim.txt?phone_number=13800138000</code>，直接返回验证码数字。</p>
      </section>
      <section className="tutorial-section" aria-labelledby="api-py-title">
        <h3 id="api-py-title">3. Python 脚本示例</h3>
        <pre className="tutorial-code"><code>{[
          'import asyncio, json, websockets',
          '',
          'TOKEN = "srh_api_你的令牌"',
          'PHONE = "13800138000"',
          `WS_URL = "${window.location.origin.replace(/^http/, "ws")}/api/v1/ws/otp"`,
          '',
          'async def get_code():',
          '    async with websockets.connect(WS_URL) as ws:',
          '        await ws.send(json.dumps({"type":"auth","token":TOKEN}))',
          '        auth_resp = json.loads(await ws.recv())',
          '        assert auth_resp["type"] == "auth_ok"',
          '',
          '        await ws.send(json.dumps({"type":"subscribe","phone_number":PHONE}))',
          '        sub_resp = json.loads(await ws.recv())',
          '        assert sub_resp["type"] == "subscribed"',
          '',
          '        # 等待平台推送新验证码（空闲 3 分钟会被关闭，这里限时 170 秒）',
          '        otp_msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=170))',
          '        return otp_msg["code"]',
          '',
          'code = asyncio.run(get_code())',
          'print("验证码:", code)',
        ].join("\n")}</code></pre>
      </section>
      {/* API_DOC_END */}
      <section className="tutorial-section" aria-labelledby="api-err-title">
        <h3 id="api-err-title">4. 常见返回与排查</h3>
        <ul>
          <li><strong>WS 立刻断开：</strong>令牌错误或已停用，重新创建脚本令牌。</li>
          <li><strong>一直收不到推送：</strong>先在“消息”页确认短信已入库；再核对订阅手机号与消息的接收号码是否一致。</li>
          <li><strong>Claim 返回 404：</strong>没有新的可用验证码。已领取或已过期的记录不会被回退，属正常。</li>
          <li><strong>验证码有效期：</strong>默认 10 分钟，过期后自动清理，脚本需在有效期内取码。</li>
        </ul>
      </section>
    </div>
  );
}

function ApiDocModal({ onClose }: { onClose: () => void }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal tutorial-modal" role="dialog" aria-modal="true" aria-labelledby="api-doc-title" onMouseDown={(event) => event.stopPropagation()}>
        <div className="section-heading compact">
          <div>
            <h2 id="api-doc-title">API 调用说明</h2>
            <span className="section-count">WebSocket 实时取码、HTTP Claim 补取与脚本示例</span>
          </div>
          <button className="icon-button" onClick={onClose} title="关闭说明" aria-label="关闭 API 调用说明窗口"><X size={18} /></button>
        </div>
        <ApiTutorial />
        <div className="modal-actions"><button className="secondary-button" onClick={onClose}>关闭</button></div>
      </section>
    </div>
  );
}

function Air724Tutorial() {
  return (
    <div className="tutorial-content">
      <p className="tutorial-lead">合宙 Air724 支持两个独立脚本：WebSocket 取码，或 MQTT 短信上报。两者不要用同一个设备记录混配。</p>
      <div className="tutorial-warning">正常流程使用 WebSocket 长连接等待新验证码；只有断线重连后才调用一次 HTTP Claim 补取，避免重复轮询和旧记录误取。</div>
      <section className="tutorial-section" aria-labelledby="air724-download-title">
        <h3 id="air724-download-title">1. 下载脚本</h3>
        <ol>
          <li><a className="tutorial-download" href="/assets/downloads/air724-otp-websocket.lua" download>下载 air724-otp-websocket.lua</a>。</li>
          <li>将脚本保存到合宙 Air724 的 Lua 脚本目录。</li>
          <li>使用合宙官方 Air724U 固件运行；脚本基于官方 <code>socket.tcp</code>、<code>http.request</code>、<code>json</code> 和 <code>crypto</code> 接口。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="air724-mqtt-download-title">
        <h3 id="air724-mqtt-download-title">1.1 需要短信上报时下载 MQTT 脚本</h3>
        <ol>
          <li><a className="tutorial-download" href="/assets/downloads/air724-mqtt-ingest.lua" download>下载 air724-mqtt-ingest.lua</a>。</li>
          <li>在后台“设备”页添加“合宙 724 MQTT 短信上报”，复制一次性 HMAC 密钥和设备 UUID。</li>
          <li>MQTT 脚本只负责把 Air724 收到的短信上报到平台，不会订阅取码主题，也不会改变现有取码脚本。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="air724-params-title">
        <h3 id="air724-params-title">2. 填写脚本参数</h3>
        <ol>
          <li>在“设备”页添加设备，客户端选择“合宙 724 取码”。</li>
          <li>创建设备后只显示一次脚本 API 令牌，把它填写到脚本参数中。</li>
          <li>在脚本顶部填写服务器 IP、端口、脚本 API 令牌和接收手机号。</li>
          <li>服务器地址填写根地址对应的主机和端口；局域网可使用 <code>ws://</code>，公网使用 <code>wss://</code> 并配置 TLS。</li>
          <li><code>device_id</code>、<code>sender</code>、<code>keyword</code> 可留空；手机号是必填筛选条件。</li>
        </ol>
        <pre className="tutorial-code"><code>{[
          'host = "服务器IP或域名",',
          'port = 8000,',
          'tls = false,',
          'script_api_token = "脚本 API 令牌",',
          'phone_number = "+8613800138000",',
          'device_id = nil,',
          'sender = nil,',
          'keyword = nil,',
        ].join("\n")}</code></pre>
      </section>
      <section className="tutorial-section" aria-labelledby="air724-flow-title">
        <h3 id="air724-flow-title">3. 运行与取码流程</h3>
        <ol>
          <li>脚本连接 <code>/api/v1/ws/otp</code>，先发送脚本 API 令牌，再订阅手机号。</li>
          <li>平台收到新短信并生成取码记录后，立即向匹配连接推送 <code>code</code>、<code>otp_id</code>、<code>message_id</code> 和时间字段。</li>
          <li>脚本按 <code>otp_id</code> 去重；同一验证码不会重复交付。</li>
          <li>连接断开重连时，脚本调用一次 <code>/api/v1/otp/claim</code> 补取最新可用记录，然后继续等待 WebSocket。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="air724-check-title">
        <h3 id="air724-check-title">4. 验证</h3>
        <ol>
          <li>先确认后台“消息”页已有新短信记录。</li>
          <li>再发送一条新的验证码短信，观察脚本日志中的 <code>otp_received</code>。</li>
          <li>若脚本离线后重新上线，只允许执行一次 Claim 补取；没有可用新码时返回 404，继续保持 WebSocket 等待。</li>
          <li>MQTT 上报模式则观察后台消息页新增记录，并确认 MQTT 桥接服务日志只记录状态码、不记录短信正文。</li>
        </ol>
      </section>
    </div>
  );
}

function AndroidAppTutorial() {
  return (
    <div className="tutorial-content">
      <p className="tutorial-lead">适用于普通 Android 手机，不需要 Root。应用只转发安装并配置后收到的新短信。</p>
      <div className="tutorial-warning">后台运行依赖前台服务通知。首次配置后请允许短信权限、后台通知，并在系统设置中把电池策略设为不受限制。</div>
      <div className="tutorial-warning">接入前必须关闭手机的「5G 消息 / 增强信息」（信息 App → 设置 → 5G 消息）。开启时部分运营商的验证码会走 RCS 通道下发，这类消息不写入系统短信数据库、也不发送短信广播，任何第三方应用都无法转发。</div>
      <section className="tutorial-section" aria-labelledby="android-app-download-title">
        <h3 id="android-app-download-title">1. 下载并安装 Android 应用</h3>
        <ol>
          <li><a className="tutorial-download" href="/assets/releases/android/18/Relay-SMS-Forwarder-1.0.17.apk" download>下载 Relay短信转发 1.0.17.apk</a>。</li>
          <li>在手机上安装 APK；系统提示允许安装未知来源时，按提示完成授权。</li>
          <li>打开应用，填写平台地址、设备 ID 和 HMAC 密钥。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="android-app-params-title">
        <h3 id="android-app-params-title">2. 添加设备并填写参数</h3>
        <ol>
          <li>在后台点击“添加设备”，客户端选择“Android 应用”。</li>
          <li>复制创建后显示一次的设备 UUID 和 HMAC 密钥。</li>
          <li>应用中的平台地址填写服务器根地址，例如 <code>http://服务器IP:端口</code>；设备 ID 填 UUID；HMAC 密钥填后台生成的密钥。</li>
          <li>点击“保存配置”。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="android-app-background-title">
        <h3 id="android-app-background-title">3. 允许后台运行</h3>
        <ol>
          <li>点击“授予短信权限”，允许短信权限。</li>
          <li>点击“授予后台通知权限”，允许通知栏常驻。</li>
          <li>在手机系统设置中关闭电池优化，并允许应用自启动、后台活动和“不受限制”的电池策略。</li>
          <li>建议开启应用内的“无障碍保活”（可选）：配合系统心跳闹钟与周期看门狗，应用被杀后会在几分钟内自动唤醒重启服务；不上锁屏杀后台的场景可以不开。</li>
          <li>需要时把应用锁定在最近任务中；应用自身会保持前台服务和常驻通知。</li>
        </ol>
        <p className="tutorial-note">小米/Redmi、华为/Honor、OPPO/OnePlus/realme、vivo、Samsung 等品牌的名称略有不同，目标都是开启自启动、后台活动和不限制电池。</p>
      </section>
      <section className="tutorial-section" aria-labelledby="android-app-verify-title">
        <h3 id="android-app-verify-title">4. 验证转发</h3>
        <ol>
          <li>确认应用页面显示“上报配置：已保存”“后台服务：已启动，通知栏常驻”。</li>
          <li>切到后台后发送一条新的测试短信。</li>
          <li>打开后台“消息”页面，确认收到完整短信内容。</li>
        </ol>
        <p className="tutorial-note">如果部分短信能转发、某个运营商或某类验证码始终收不到，先确认「5G 消息 / 增强信息」已关闭：RCS 消息不触发短信广播，应用收不到。</p>
      </section>
    </div>
  );
}

function AndroidRootTutorial() {
  return (
    <div className="tutorial-content">
      <p className="tutorial-lead">适用于 Magisk、KernelSU、APatch。第一版只保留 ARM 真机运行时：<strong>arm64-v8a / armeabi-v7a</strong>。</p>
      <div className="tutorial-warning">Root 模块只转发安装并启用模块后收到的新短信，不同步手机已有历史短信，也不会修改短信的已读、删除或归档状态。</div>
      <div className="tutorial-warning">接入前必须关闭手机的「5G 消息 / 增强信息」（信息 App → 设置 → 5G 消息）。RCS 消息存放在短信应用自己的数据库里，不写入 <code>mmssms.db</code>，模块读取短信数据库同样拿不到，Root 权限也不例外。</div>
      <section className="tutorial-section" aria-labelledby="android-install-title">
        <h3 id="android-install-title">1. 安装模块</h3>
        <ol>
          <li><a className="tutorial-download" href="/assets/releases/root/21/sms-relay-root-module-0.3.9.zip" download>下载 sms-relay-root-module-0.3.9.zip</a>。</li>
          <li>在 Magisk、KernelSU 或 APatch 的“模块”中选择“从存储安装”，选择 ZIP 后完成安装。</li>
          <li>重启手机并确认模块已启用。打开模块应用，点击“执行”启动控制台并自动跳转；未自动跳转时手动打开 <code>http://127.0.0.1:35000/</code>。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="android-config-title">
        <h3 id="android-config-title">2. 在后台创建设备并填写配置</h3>
        <ol>
          <li>点击本页“添加设备”，客户端选择“Android Root”，记录新生成且只显示一次的设备 UUID 和 HMAC 密钥。</li>
          <li>打开模块应用，点击“执行”启动控制台；默认地址是 <code>http://127.0.0.1:35000/</code>，也可以手动输入该地址。</li>
          <li>首次安装模块，或卸载后重新安装模块，都要重新填写服务器 IP 和端口、设备 UUID、HMAC 密钥；数据库路径留空，让模块自动检测 CE/DE Telephony 数据库。</li>
        </ol>
        <pre className="tutorial-code"><code>{[
          "/data/adb/sms-relay/config.json",
          "{",
          '  "schema_version": 2,',
          '  "enabled": true,',
          '  "base_url": "http://服务器IP:端口",',
          '  "allow_insecure_local_http": true,',
          '  "transport_mode": "http",',
          '  "fallback_to_http": true,',
          '  "mqtt_broker_url": "",',
          '  "mqtt_topic": "",',
          '  "mqtt_username": "",',
          '  "mqtt_password": "",',
          '  "device_id": "后台设备 UUID",',
          '  "hmac_secret": "后台生成的密钥",',
          '  "source": "root_module",',
          '  "android_user_id": 0,',
          '  "initial_sync_mode": "new_only",',
          '  "poll_interval_seconds": 3,',
          '  "watcher_idle_seconds": 1,',
          '  "max_backoff_seconds": 300,',
          '  "sms_database_path": ""',
          "}",
        ].join("\n")}</code></pre>
        <p className="tutorial-note">默认使用 HTTP；把 transport_mode 改为 ws 或 mqtt 可切换通道，mqtt_topic 留空会自动生成标准设备主题。WS/MQTT 失败时由 fallback_to_http 回退 HTTP；配置保存后会原子替换并唤醒 worker，不需要重启。</p>
      </section>
      <section className="tutorial-section" aria-labelledby="android-check-title">
        <h3 id="android-check-title">3. 验证读取、SIM 和上传</h3>
        <ol>
          <li>在手机 Root 终端执行自检：<code>sms-relay-runtime --self-test</code>。</li>
          <li>执行配置检查：<code>sms-relay-runtime check-config --config /data/adb/sms-relay/config.json</code>。</li>
          <li>在模块控制台查看状态，确认 worker 为运行中，数据库诊断显示实际命中路径。</li>
          <li>给手机下发一条全新的测试短信，再到后台“消息”页查看；不要用安装前旧短信判断转发是否成功。</li>
          <li>双卡识别优先使用短信 <code>sub_id</code>、<code>telephony/siminfo</code> 和活动订阅映射；号码被 ROM 隐藏时，在设备手机号里手动绑定。</li>
        </ol>
      </section>
      <section className="tutorial-section" aria-labelledby="android-troubleshoot-title">
        <h3 id="android-troubleshoot-title">4. 未收到或延迟时怎么查</h3>
        <ul>
          <li><strong>没有记录：</strong>先看设备是否启用、UUID/密钥是否来自同一条设备记录、手机是否能访问服务器 IP 和端口。</li>
          <li><strong>只有某个运营商或某类验证码收不到：</strong>基本是 RCS（5G 消息）下发。手机上能看到消息，但系统短信数据库里没有对应记录，关闭「5G 消息 / 增强信息」后即可恢复。</li>
          <li><strong>401：</strong>后台凭据被轮换或撤销，重新轮换设备凭据并在模块控制台替换密钥。</li>
          <li><strong>SIM 未识别：</strong>先看诊断中的 subscription；必要时手动绑定手机号，不要修改短信数据库。</li>
          <li><strong>有记录但延迟：</strong>检查 watcher 是否可用；不可用时才依赖补偿扫描。网络退避只处理失败重试，不会阻塞新短信第一次上传。</li>
          <li><strong>重复或旧短信：</strong>检查是否残留旧 worker；先在模块控制台停止，再启动，避免多个进程竞争队列锁。</li>
        </ul>
      </section>
    </div>
  );
}

function IosTutorial() {
  const [iosFlow, setIosFlow] = useState<"import" | "manual">("import");

  return (
    <div className="tutorial-content">
      <div className="tutorial-tabs tutorial-flow-tabs" role="tablist" aria-label="快捷指令配置方式">
        <button role="tab" aria-selected={iosFlow === "import"} className={iosFlow === "import" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setIosFlow("import")}>导入现成快捷指令</button>
        <button role="tab" aria-selected={iosFlow === "manual"} className={iosFlow === "manual" ? "tutorial-tab active" : "tutorial-tab"} onClick={() => setIosFlow("manual")}>手动创建快捷指令</button>
      </div>

      {iosFlow === "import" ? (
        <section className="tutorial-section" aria-labelledby="ios-import-title">
          <h3 id="ios-import-title">1. 导入快捷指令并修改配置</h3>
          <ol>
            <li><a className="tutorial-download" href="/assets/downloads/ios-sms-config.shortcut" download>下载 ios-sms-config.shortcut</a>，上传到微信后选择“用快捷指令打开”，再导入快捷指令。</li>
            <li>在后台“添加设备”中选择“iPhone 快捷指令”，复制创建后显示一次的 Token。</li>
            <li>打开已导入的快捷指令，替换第一个“文本”动作中的 Token。</li>
            <li>打开“获取 URL 内容”动作，把地址改为 <code>http://服务器IP:端口/api/v1/ingest/shortcut</code>，保存快捷指令。</li>
            <li>打开“快捷指令”App 的“自动化”，选择“信息”触发，设置收到信息时运行这个快捷指令；系统询问时允许使用输入内容。</li>
            <li>发送一条新的测试短信，确认后台“消息”页收到记录。</li>
          </ol>
          <img className="tutorial-image" src="/assets/tutorial/ios/shortcut-automation.jpg" alt="信息自动化触发配置示例" />
        </section>
      ) : (
        <section className="tutorial-section" aria-labelledby="ios-manual-title">
          <h3 id="ios-manual-title">1. 手动创建快捷指令和自动化</h3>
          <ol>
            <li>打开“快捷指令”App，点击右上角“+”新建快捷指令。</li>
            <li>添加“文本”动作，填入后台“iPhone 快捷指令”设备创建后显示一次的 Token。</li>
            <li>添加“获取 URL 内容”动作，地址填写 <code>http://服务器IP:端口/api/v1/ingest/shortcut</code>。</li>
            <li>点击“显示更多”，方法选择 <strong>POST</strong>。</li>
            <li>请求头添加 <code>Authorization: Bearer &lt;设备Token&gt;</code> 和 <code>Content-Type: application/json</code>。</li>
            <li>请求体选择 JSON，只保留 <code>message</code> 字段，值选择“输入快捷指令的信息”。</li>
            <li>保存快捷指令并手动运行一次。</li>
            <li>打开“自动化”，选择“信息”触发，设置收到信息时运行这个快捷指令；系统询问时允许使用输入内容。</li>
            <li>发送一条新的测试短信，确认后台“消息”页收到记录。</li>
          </ol>
          <img className="tutorial-image" src="/assets/tutorial/ios/shortcut-automation.jpg" alt="信息自动化触发配置示例" />
        </section>
      )}

      <section className="tutorial-section" aria-labelledby="ios-received-at-title">
        <h3 id="ios-received-at-title">2. 可选：显示手机上的真实接收时间（received_at）</h3>
        <p className="tutorial-note">不配置也能正常转发（平台用服务器时间，偏差约 1 分钟内）；配置后消息显示 iPhone 收到短信的真实时间。重发同一条短信平台会自动去重，不会重复。</p>
        <ol>
          <li>在快捷指令里、<strong>“获取 URL 内容”之前</strong>添加「日期」动作，选择<strong>当前日期</strong>。</li>
          <li>再添加「格式化日期」动作，日期格式选<strong>自定义</strong>，格式串填 <code>yyyy-MM-dd&apos;T&apos;HH:mm:ssZZZZZ</code>（T 两边有单引号、结尾是五个 Z）。配置正确时预览显示 <code>2026-09-07T22:15:30+08:00</code>。</li>
          <li>回到「获取 URL 内容」→ 请求体 JSON 点“添加新字段”：键名填 <code>received_at</code>，值选择<strong>“格式化的日期”变量</strong>。</li>
        </ol>
        <pre className="tutorial-code"><code>{[
          "message     = 输入快捷指令的信息",
          "received_at = 格式化的日期",
        ].join("\n")}</code></pre>
        <p className="tutorial-note">格式串抄错不会报错——平台自动回退服务器时间。自动化模式下找不到“输入快捷指令的信息”变量时，保持“自动化 → 运行快捷指令”两层结构，该变量只在快捷指令内部可用。</p>
      </section>
    </div>
  );
}
