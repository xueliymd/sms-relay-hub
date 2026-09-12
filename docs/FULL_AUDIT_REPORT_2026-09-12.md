# 全量审查报告（2026-09-12）

审查范围：容器部署链 / 后端 API / 网页管理台 / 管理端 App / 采集端 App / 跨端契约一致性 / 逻辑遗漏与效率。

---

## 一、容器部署链审查 ✅ 正常

| 检查项 | 结论 |
|---|---|
| `deploy/Dockerfile` | 两段构建：node 构建 web → `COPY --from=web-build /web/dist /app/app/static`；Python 运行时 `pip install .`；非 root 用户 `relay`；`EXPOSE 39200` ✅ |
| `deploy/compose.yml` | postgres 健康检查 + `depends_on: service_healthy`；app 服务 `read_only: true` + `tmpfs /tmp`；健康检查打 `/api/v1/health`；MQTT bridge 用 profiles 隔离；**Caddy 只暴露 HTTP（39300）**，443/QUIC 已移除由宝塔终结 TLS ✅ |
| 数据库迁移 | `deploy/entrypoint.sh` 启动即 `alembic upgrade head` → **迁移 `0010_device_commands` 会自动应用**，无需手工执行 ✅ |
| 更新流程 `apply-update.sh` | 先备份源码 tar + `.env` + release 库 + `pg_dump` 数据库；替换 backend/clients/deploy/web；`compose build app` + `up -d`；**失败自动回滚**（恢复源码并重建）；检测多 Compose project 时拒绝更新，避免连到空库 ✅ |
| 更新包校验 | 包内 catalog 必须存在且**不能含 `platform: deploy`**，否则拒绝 → 本次包已通过 ✅ |
| 目录挂载 | Caddyfile/domain/entrypoint 只读挂载，caddy_data/config 持久化 ✅ |

---

## 二、跨端契约一致性核对（脚本化比对）✅

用脚本提取"后端 43 条路由"与"网页/管理端引用的所有 `/api/v1/...` 路径"做结构化比对：

- **网页端**：25 条引用路径全部命中后端路由（含本轮新增的 `POST/GET /admin/devices/{id}/commands`）
- **管理端 App**：22 条引用路径全部命中
- WebSocket 路由（`/ws/otp`、`/ws/admin/messages`）与 releases 路由（`/api/v1/releases/{platform}/history`）均存在
- 远程控制 4 条新路由与三端调用一一对应（网页下发/查询、采集端轮询/回传）

---

## 三、发现的问题与修复（4 项）

| # | 层级 | 问题 | 影响 | 处理 |
|---|---|---|---|---|
| 1 | 管理端 App | 接口页"短信上报"显示为 `/api/v1/ingest`，**该端点不存在**（真实为 `/api/v1/ingest/messages`） | 用户照抄地址会 404 | ✅ 已修正，并补充"设备指令轮询"地址 |
| 2 | 采集端 | 指令轮询**无最小间隔**：短信密集时每条短信的上报都会顺带发一次 HTTP GET | 无谓耗电与流量 | ✅ 已加 30 秒最小间隔（轮询标志 + 时间戳双闸） |
| 3 | 后端 | 设备轮询端点未处理"设备不存在"（理论上鉴权后必存在，但缺防御） | 极端情况 500 | ✅ 已补 404 `device_not_found` |
| 4 | 后端 | 未完成指令可无限堆积；设备取走指令后进程被杀会导致指令永久卡在 `sent` | 队列失控 / 指令永久失效 | ✅ 已加：单设备未完成上限 20（超出 429）+ 陈旧 `sent` 超 10 分钟自动重投（v1 指令幂等，重投安全） |

---

## 四、效率与对接检查

| 项 | 结论 |
|---|---|
| 指令下行延迟 | 设计为轮询（非实时）：随上报周期（收到短信时）+ 15 分钟看门狗；网页在有未完成指令时每 5 秒自动刷新结果 ✅ |
| 采集端额外开销 | 每次轮询 1 个 GET（HMAC 签名，空 body），加了 30 秒节流后，短信密集场景从"每条一次"降到"每 30 秒最多一次" ✅ |
| 结果体积 | 后端限 16KB；采集端最多回 5 条短信、正文截断 200 字符 ✅ |
| 号码脱敏 | 采集端回传前对发件人号码保留后 4 位打码；平台结果密文落库（测试已验证密文中不含明文验证码）✅ |
| 数据库占用 | 指令历史 7 天自动清理（借用写入时机，10 分钟最多一次）✅ |
| 前端 bundle | 构建产物 261KB（gzip 80KB），未引入新依赖 ✅ |

---

## 五、逻辑遗漏清单

### 已在本轮修复
- 管理端接口页错误地址；采集端轮询无节流；后端队列无上限/无重投/缺 404 防御

### 仍然存在的已知缺口（非缺陷，属未实现功能）
- 管理端 App 无"远程控制"下发 UI（网页已有）
- 指令结果无主动推送（网页 5 秒轮询代替）
- 转发渠道仅 5 个（Telegram/SMTP/Server酱/PushPlus 未做）
- HMS 推送未实现（无 `push.py`、devices 表无 `push_token`）
- 远程控制 v2 未做（发短信/通话/联系人/定位）
- Git 未提交（93 文件）、GitHub 私有仓库备份、外部总结文档未更新

详见 `docs/PENDING_FEATURES_REPORT_2026-09-12.md`。

---

## 六、验证证据

| 层 | 结果 |
|---|---|
| 后端 | ruff 全过；**247 项测试通过**（含远程控制 10 项：生命周期/幂等/签名无效/设备不一致 403/未知指令/超体积/不支持类型/撤销设备/**队列上限 429**/**陈旧重投**/未知设备 404/**结果密文落库**） |
| 网页 | tsc 无错；**60 项测试通过**；vite 构建成功 |
| 采集端 | 单测 + lint + 签名 release 构建通过（1.0.20 / versionCode 21） |
| 管理端 | 单测 + lint + 签名 release 构建通过（1.0.8 / versionCode 9） |
| 更新包 | `sms-relay-hub-update-2026.09.12-remote-control-v3.zip`（33,047,816 字节，132 条目）：catalog 6 条 release 与文件一一对应、不含 deploy 平台、两个新 APK 哈希与最新发布一致、后端与网页关键代码均在包内 ✅ |

---

## 七、部署清单（服务器）

1. 上传 `deploy/dist/sms-relay-hub-update-2026.09.12-remote-control-v3.zip` 到服务器
2. 解压到安装目录之外 → `sh deploy/apply-update.sh <安装路径>`
   - 脚本自动备份数据库与源码；`entrypoint.sh` 会自动执行迁移 `0010`
3. `sh deploy/preflight.sh` 做部署前自检
4. 验证：`/api/v1/health`、管理台设备列表、网页"远程控制"页下发一次"查询电量"
5. 客户端升级：采集端 → 1.0.20，管理端 → 1.0.8
6. 采集端打开应用勾选需要的指令授权（默认全关）后实测端到端
