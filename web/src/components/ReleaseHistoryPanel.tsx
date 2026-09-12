import { Download } from "lucide-react";
import { useState } from "react";

import type { ReleaseEntry } from "../adminApi";


type ReleaseHistoryPanelProps = {
  releases: ReleaseEntry[];
};

const platformLabels: Record<ReleaseEntry["platform"], string> = {
  android: "Android",
  manager: "管理端",
  root: "Root",
};

const releaseFilters: Array<{ value: "all" | ReleaseEntry["platform"]; label: string }> = [
  { value: "all", label: "全部" },
  { value: "android", label: "Android 采集端" },
  { value: "manager", label: "管理端" },
  { value: "root", label: "Root 模块" },
];

function sizeLabel(size: number): string {
  return (size / 1024 / 1024).toFixed(1) + " MB";
}

export function ReleaseHistoryPanel({ releases }: ReleaseHistoryPanelProps) {
  const [filter, setFilter] = useState<"all" | ReleaseEntry["platform"]>("all");
  const ordered = [...releases]
    .filter((release) => filter === "all" || release.platform === filter)
    .sort((left, right) => right.published_at.localeCompare(left.published_at));
  return (
    <section className="release-history-panel" aria-labelledby="release-history-title">
      <div className="section-heading">
        <div>
          <h2 id="release-history-title">客户端与模块下载</h2>
          <span className="section-count">Android、管理端和 Root 模块</span>
        </div>
      </div>
      <div className="release-filters" aria-label="下载类型">
        {releaseFilters.map((option) => (
          <button
            className={filter === option.value ? "release-filter active" : "release-filter"}
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className="release-history-list">
        {ordered.length === 0 ? <p className="empty-state">暂无发布历史</p> : ordered.map((release) => {
          const title = platformLabels[release.platform] + " " + release.version_name;
          return (
            <article key={release.platform + "-" + release.version_code}>
              <div>
                <strong>{title}</strong>
                <span>{new Date(release.published_at).toLocaleString()} · {sizeLabel(release.size_bytes)}</span>
                <p>{release.notes}</p>
                <code>SHA-256 {release.sha256}</code>
              </div>
              <a className="secondary-button" href={release.download_url} aria-label={"下载 " + title}>
                <Download size={15} />下载
              </a>
            </article>
          );
        })}
      </div>
    </section>
  );
}
