# 2026-09-12 会话期间审计记录（归档）

这 8 份是从当次工作会话目录复制进来的**时点快照**（原文件不在本仓库，原本只存在于本地会话工作区）。
它们不是当前状态的权威描述——**当前状态请看 `docs/STATUS.md`**，本目录仅作为分析与决策依据留档。

| 文件 | 内容 |
| --- | --- |
| `audit_collector.md` / `audit2_collector.md` | 采集端（Android App）全量审查与复核（两轮） |
| `audit_manager.md` / `audit2_manager.md` | 管理端 App 全量审查与复核（两轮） |
| `audit_github_compare.md` | 本项目 vs 上游开源 `pppscn/SmsForwarder` 对照审计（含保活、远程控制、采集链路、管理端四维度） |
| `audit_transcript_reconcile.md` | 会话记录与代码实际状态的核对（找出"说了但没做"的项） |
| `ANDROID_FIXES_2026-09-12.md` | 采集端保活/权限相关修复清单（磁贴、电池优化、精确闹钟、厂商自启动等） |
| `PATCHES_PENDING_2026-09-12.md` | 当时待打补丁清单（部分已完成，逐项状态以 STATUS.md 为准） |

注意：`audit_github_compare.md` 里提到的"标 A2 的深 Doze 定时唤醒被削弱""结果无加密档位且允许 http"
等结论属于**上游对照发现**，尚未全部处理，已登记在 `docs/STATUS.md` 的"未完成/待办"。
