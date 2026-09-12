import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import type { ReleaseEntry } from "../adminApi";
import { ReleaseHistoryPanel } from "./ReleaseHistoryPanel";


const releases: ReleaseEntry[] = [
  {
    platform: "manager",
    channel: "stable",
    version_code: 1,
    version_name: "1.0.0",
    published_at: "2026-09-01T05:30:00Z",
    min_sdk: 26,
    mandatory: false,
    notes: "Manager app",
    size_bytes: 1800000,
    sha256: "g".repeat(64),
    signer_sha256: "h".repeat(64),
    download_url: "/assets/releases/manager/1/Relay-Hub-Manager-1.0.0.apk",
  },
  {
    platform: "android",
    channel: "stable",
    version_code: 12,
    version_name: "1.0.11",
    published_at: "2026-09-01T05:12:06Z",
    min_sdk: 26,
    mandatory: false,
    notes: "Android collector update",
    size_bytes: 2211177,
    sha256: "a".repeat(64),
    signer_sha256: "b".repeat(64),
    download_url: "/assets/releases/android/12/Relay-SMS-Forwarder-1.0.11.apk",
  },
  {
    platform: "root",
    channel: "stable",
    version_code: 307,
    version_name: "0.3.7",
    published_at: "2026-09-01T06:00:00Z",
    min_sdk: 21,
    mandatory: false,
    notes: "Root module update",
    size_bytes: 1200000,
    sha256: "c".repeat(64),
    signer_sha256: "d".repeat(64),
    download_url: "/assets/releases/root/307/relay-root-0.3.7.zip",
  },
];


test("renders Android, Root and management release histories", () => {
  render(<ReleaseHistoryPanel releases={releases} />);

  expect(screen.getByText("Android 1.0.11")).toBeInTheDocument();
  expect(screen.getByText("Root 0.3.7")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载 Android 1.0.11" })).toHaveAttribute(
    "href",
    "/assets/releases/android/12/Relay-SMS-Forwarder-1.0.11.apk",
  );
});

test("filters the download center to management app releases", () => {
  render(<ReleaseHistoryPanel releases={releases} />);

  fireEvent.click(screen.getByRole("button", { name: "管理端" }));

  expect(screen.getByText("管理端 1.0.0")).toBeInTheDocument();
  expect(screen.queryByText("Android 1.0.11")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "下载 管理端 1.0.0" })).toHaveAttribute(
    "href",
    "/assets/releases/manager/1/Relay-Hub-Manager-1.0.0.apk",
  );
});
