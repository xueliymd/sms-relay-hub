# 全量审查报告 2（2026-09-12 下午）

范围：迁移故障根因、采集端、管理端、容器部署包，逐项对照本地 md 文档核对"是否缺代码 / 漏逻辑"。

---

## 一、迁移故障根因（为什么更新会失败）

### 触发链条（代码级，已逐行核对）

1. `deploy/entrypoint.sh`：
   ```sh
   alembic upgrade head
   exec uvicorn app.main:app ...
   ```
   **迁移在 uvicorn 启动之前执行**，也就是在容器健康检查之前就提交进数据库。

2. `deploy/apply-update.sh`：
   - 更新前确实备份了数据库（`pg_dump -Fc > $BACKUP_DIR/database.dump`），备份点正确（迁移之前）
   - 但失败回滚只做：`tar -xzf source.tar.gz` → `compose up --build -d`
   - **回滚不还原数据库、也不 downgrade 迁移**

3. 结果：新容器只要健康检查不通过 → 数据库已停在 0010、源码退回 0009 → 旧代码 `alembic upgrade head` 找不到 revision 0010 → 启动即崩 → `Restarting (255)`。

### 为什么说这不是"没看文档"

三份权威文档（`SESSION_01A06BD1_HANDOFF.md`、`CURRENT_FEATURE_AUDIT_2026-09-01.md`、`FULL_AUDIT_REPORT_2026-09-12.md`）
对回滚的描述只有一句：

> 容器更新前备份 PostgreSQL、`deploy/.env`、发布库和源码，失败自动回滚（恢复源码并重建）

**没有任何一处要求"回滚时必须同时还原数据库 / 不允许留下已前移的迁移"。** 这个场景以前没被触发过（此前几次更新的迁移都顺利通过），是我这次新增的迁移 `0010` 第一次把它暴露出来。

### 附带发现的配置缺口

- `deploy/compose.yml` 的 app 健康检查**没有 `start_period`**（只有 `interval: 10s / timeout: 3s / retries: 6`）。冷启动（alembic + uvicorn + 首次请求）偶发超时即可被判 unhealthy。
- 原触发点（新容器究竟是真崩还是被判超时）**仍需 `docker logs deploy-app-1` 定论**，我没有服务器访问权，无法单方面确认。

---

## 二、采集端（Android collector）对照文档

### 一致的部分
- 保活：文档要求"可选无障碍、只监听窗口状态、不读页面内容、不执行点击" → 代码 `RelayKeepAliveAccessibilityService` 符合
- 上报契约（HMAC / 幂等 / 409 终态 / 退避）与 `docs/superpowers/plans/2026-08-14-android-collector-implementation.md` 锁定契约一致
- `ANDROID_GITHUB_COMPARISON.md`（参考 `pppscn/SmsForwarder`）里"不复制高耗电保活组合""不伪装默认短信应用"等结论，代码仍遵守

### 未在文档中记录的代码（需补文档）
- `READ_SMS` 权限（本轮为远程控制"查最近短信"新增）
- `CommandSettingsStore.kt` / `DeviceCommandClient.kt`（远程控制端侧：授权开关 + 轮询 + 执行器）
- `AccessibilityAutoToggle.kt`（`WRITE_SECURE_SETTINGS` 自动开启/自愈无障碍）、`RelayTileService.kt`（磁贴）

### 已确认缺功能
- 远程控制 v2：发短信 / 查通话 / 查话簿（v1 只做了查电量、查最近短信、触发上传）

---

## 三、管理端（Relay-Hub-Manager）对照文档

### ⚠️ 与设计文档冲突（必须先决策）
`docs/superpowers/specs/2026-09-01-manager-app-and-web-monitoring-design.md` 的**非目标**第 2 条明确写：

> 管理端 App **不执行容器更新、数据库恢复、Root 安装或管理员账号修改**。

但管理端 1.0.7 我加了"**修改管理员账号**"。依据是后来 `DEFERRED_FEATURES.md` 把它列为"有意未做、后续增强项"，加上你要求补齐。**两者矛盾，需要你确认以哪个为准**：要么更新设计文档（承认范围已扩大），要么从 App 里去掉该功能。

### 设计文档要求但代码未实现
- `GET /api/v1/admin/runtime` 设计要求返回**证书到期时间**与**最新 APK/Root/部署包版本**；实际只返回 `database_*`、`platform_version`、`public_base_url`、`relay_site`、三个端口、`https_enabled`、`services`、`server_time` → **缺这两项**（前端目前也没消费，优先级低）

### 已按变更演进、文档需同步
- 设计文档说"管理端必须先选设备才显示短信"；1.0.1 起已改为"默认全部设备、设备/SIM 只作筛选"（`CHANGELOG-2026-09-03.md` 有记录）→ 设计文档需更新

### 已确认缺功能
- 管理端 App 无"远程控制"下发入口（只有网页有）

---

## 四、容器部署包

### 打包红线：全部通过（已实测 v3 包）
无嵌套 deploy/dist、无 `releases/deploy`、catalog 不含 deploy 平台、catalog 无 BOM、条目名全正斜杠、apply-update.sh 用 `{{.Label}}` 且含 `RELAY_UPDATE_DETECT_ONLY`、无 `.env` 真实密钥。

### 发现的问题
1. **回滚不回滚迁移**（第一节，最重要）
2. **健康检查缺 `start_period`**
3. **包内混入 `web/tsconfig.tsbuildinfo`**：这是 TypeScript 增量构建缓存（含本机绝对路径），不该进部署包，应在 `deploy/package.ps1` 排除或加进 `.dockerignore`
4. **文档过期**：`DEPLOYMENT_AUDIT_2026-08-23.md` 写"FastAPI 容器内部固定监听 8000"，实际 `compose.yml` 用 `APP_PORT=39200`
5. **部署流程缺迁移安全检查**：没有"迁移前记录 alembic 版本 / 失败时自动 `alembic downgrade` 或还原 dump"的环节

---

## 五、必须澄清的一点（远程控制需求）

你说："远程控制不是只后台管理有需求才会进行发送"。

我目前实现的是：**网页管理台按需下发 → 设备轮询领取执行**。你的意思我理解为以下哪一种，做法完全不同，所以先问清楚再动手：

- **A**：管理端 App 也要能下发指令（不只是网页）
- **B**：指令不该只由人工触发，还要支持**自动/定时**下发（如设备失联自动查电量）
- **C**：设备侧不该是"被动等平台发"，要有主动上报/自主执行能力
- **D**：其他（请直接描述）

---

## 六、当前服务恢复（最优先）

服务仍处于中断状态，两条路（详见上一条消息的命令）：
- **A 前进**：数据库已在 0010、匹配新代码 → 重跑 `apply-update.sh`
- **B 后退**：`pg_restore` 还原 `database.dump` → 旧代码立刻恢复

**建议先 B 恢复业务，再按本报告逐项修，改完一次性重上。**
