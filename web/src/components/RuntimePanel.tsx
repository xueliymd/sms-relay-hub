import { Copy, Database, Globe2, Package, Server, ShieldAlert } from "lucide-react";

import { adminWebSocketUrl, type AdminDevice, type RuntimeInfo } from "../adminApi";


type RuntimePanelProps = {
  runtime: RuntimeInfo;
  devices: AdminDevice[];
  baseUrl: string;
};

function httpUrl(origin: string, path: string): string {
  return new URL(path, origin.endsWith("/") ? origin : origin + "/").toString();
}

export function RuntimePanel({ runtime, devices, baseUrl }: RuntimePanelProps) {
  const origin = runtime.public_base_url || baseUrl || window.location.origin;
  const messageApi = httpUrl(origin, "/api/v1/admin/messages");
  const messageSocket = adminWebSocketUrl(origin, "/api/v1/ws/admin/messages");
  const onlineCount = devices.filter((device) => device.online === true).length;
  const credentialWarnings = devices.filter((device) => device.credential_reuse_warning === true).length;
  const collectionWarnings = devices.filter((device) => device.duplicate_collection_warning === true).length;
  const latestReleases = Object.entries(runtime.latest_releases ?? {})
    .filter(([, release]) => Boolean(release?.version_name))
    .sort(([left], [right]) => left.localeCompare(right));

  return (
    <section className="runtime-panel" aria-labelledby="runtime-title">
      <div className="section-heading">
        <div>
          <h2 id="runtime-title">平台运行信息</h2>
          <span className="section-count">平台版本 {runtime.platform_version}</span>
        </div>
        <span className={runtime.https_enabled ? "runtime-secure" : "runtime-warning"}>
          {runtime.https_enabled ? "HTTPS 已启用" : "未加密 HTTP"}
        </span>
      </div>

      <div className="runtime-grid">
        <article><Globe2 size={18} /><span>外部地址</span><strong>{origin}</strong></article>
        <article><Server size={18} /><span>HTTP 入口 / App 内部（HTTPS 由上游反代终结）</span><strong>{runtime.http_port} / {runtime.app_port}</strong></article>
        <article><Database size={18} /><span>数据库</span><strong>{runtime.database_backend} · {runtime.database_fingerprint}</strong></article>
        <article><ShieldAlert size={18} /><span>设备状态</span><strong>在线设备 {onlineCount} / {devices.length}</strong></article>
        <article>
          <Package size={18} />
          <span>最新发布版本</span>
          <strong>
            {latestReleases.length > 0
              ? latestReleases.map(([platform, release]) => `${platform} ${release.version_name}`).join(" · ")
              : "发布清单不可读"}
          </strong>
        </article>
      </div>

      <div className="runtime-warning-row">
        <span>凭据多实例 {credentialWarnings}</span>
        <span>重复采集 {collectionWarnings}</span>
        {Object.entries(runtime.services).map(([name, state]) => (
          <span key={name}>{name}: {state}</span>
        ))}
      </div>

      <div className="endpoint-list">
        <div>
          <span>管理员消息 API</span>
          <code>{messageApi}</code>
          <button
            className="icon-button"
            aria-label="复制管理员消息 API"
            onClick={() => void navigator.clipboard?.writeText(messageApi)}
          ><Copy size={14} /></button>
        </div>
        <div>
          <span>管理员消息 WebSocket</span>
          <code>{messageSocket}</code>
          <button
            className="icon-button"
            aria-label="复制管理员消息 WebSocket"
            onClick={() => void navigator.clipboard?.writeText(messageSocket)}
          ><Copy size={14} /></button>
        </div>
      </div>
    </section>
  );
}
