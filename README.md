# SMS Relay Hub

自托管的授权设备消息中转平台。Android 客户端监听本机新消息并通过 HMAC 上报；iPhone 使用“信息”个人自动化和快捷指令通过 Bearer 上报。服务端把敏感字段用 AES-256-GCM 加密后保存到 PostgreSQL，并提供一次性访问码 Claim API。

## 一键部署

**Linux 服务器（一条命令）**

```sh
git clone https://github.com/xueliymd/sms-relay-hub.git && cd sms-relay-hub && sh deploy/install.sh
```

`deploy/install.sh` 依次做四件事：

1. 初始化 `deploy/.env`——自动生成缺失的安全密钥（`ADMIN_PASSWORD` / `ADMIN_TOKEN` / `DATA_ENCRYPTION_KEY` / `POSTGRES_PASSWORD`），**已有的非空值不会被覆盖**；
2. 跑 `deploy/preflight.sh` 做配置闸门（缺键、端口非法就停下让你先改）；
3. `docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml up --build -d`；
4. 打印容器状态、管理台入口和健康检查命令。

可加 `--skip-preflight`（跳过闸门）或 `--no-build`（不重建镜像）。**脚本幂等，重复执行安全。**

前置条件：Docker Engine + `docker compose` v2 插件。默认入口 `http://<服务器IP>:39300/`，管理员用户名默认 `admin`，密码在 `deploy/.env` 里（bootstrap 生成时会在终端只打印一次）。

**Windows**

```powershell
pwsh -File deploy\bootstrap.ps1
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml up --build -d
```

**⚠️ 已有数据的服务器做版本更新不要用上面这条**，改用更新包里的 `deploy/apply-update.sh`——它会先备份源码、`.env` 和 PostgreSQL dump，失败时三者一起回退。上线命令单与回滚手册见 `docs/UPDATE-COMMANDS-2026.09.12-platform-fixes.md` 与 `docs/DEPLOY_INCIDENT_RECOVERY_2026-09-12.md`。

**项目当前状态、未完成项与待办**：见 `docs/STATUS.md`。

## 已实现

- FastAPI + SQLAlchemy + Alembic + PostgreSQL 16 持久化。
- Android HMAC、iPhone Bearer、Root HTTP/WS/MQTT 上报、凭据轮换和设备撤销。
- HMAC 时间窗口与持久 nonce 防重放。
- `(device_id, client_message_id)` 幂等约束；同请求重放返回 `200 duplicate=true`，同键异文返回 `409`。
- 消息正文、发送方、metadata、HMAC 密钥和访问码字段级 AES-GCM 加密。
- 4–8 位访问码识别、默认 30 分钟有效期、API Client 按手机号领取最新验证码、并发 Claim 保护；可用记录到期删除，已领取记录默认保留 1 天后删除。
- 7 天消息保留策略、验证码过期后最多约 60 秒自动清理、管理员手动清理。
- React 管理控制台：设备、消息、访问码、凭据轮换/撤销、保留清理和 API 凭据创建。
- Android 广播即时上传 + Room 加密队列 + WorkManager 失败重试；成功或终态后清空本地发送方和正文密文。
- iOS 快捷指令动作定义、Node 契约测试，以及 Android/iOS 的脱敏协议 smoke 脚本。

## 生产部署

1. 初始化配置（自动生成缺少的安全密钥，不覆盖已有值）：

```powershell
pwsh -File deploy/bootstrap.ps1
```

2. 按需修改 `deploy/.env`：

- `RELAY_SITE`：固定保持 `:80`，负责 `http://服务器IP:39300` 入口。它不是宿主机对外端口，不要改成 `:39300`。
- `RELAY_DOMAIN`：域名双模式开关。留空时只启用 IP/端口；填已解析的真实域名后，Caddy 同时保留 IP/端口入口并自动启用域名 HTTPS。
- `COMPOSE_PROJECT_NAME`：默认不设置。当前产品按单实例部署，Docker Compose 会从 `deploy` 目录推导出稳定项目名；不要手工改成其他值，否则会切到另一个空数据库卷。
- `HTTP_PORT` / `HTTPS_PORT`：服务器宿主机对外端口，默认 `39300` / `39301`。域名使用 `https://你的域名` 不带端口时，把 `HTTPS_PORT` 改为 `443`；保留 `39301` 时域名地址需要写成 `https://你的域名:39301`。
- `APP_PORT`：应用容器内部 FastAPI 端口，默认 `39200`；Caddy 会按同一个 `APP_PORT` 转发到应用容器。
- `ADMIN_USERNAME`：首次登录的管理员用户名，默认 `admin`。
- `ADMIN_PASSWORD`：首次登录的管理员密码；留空时由 bootstrap 自动生成并在终端只打印一次。
- `ADMIN_TOKEN`：兼容现有维护脚本的恢复令牌；管理台不提供填写入口，日常使用用户名和密码登录。
- `DATA_ENCRYPTION_KEY`：32 字节随机值的 base64url 编码。
- `POSTGRES_PASSWORD`：URL 安全的随机密码。
- `DATABASE_URL`：例如 `postgresql+psycopg://sms_relay:<password>@postgres:5432/sms_relay`，初始化脚本会根据 PostgreSQL 参数自动生成。

管理台只使用 `ADMIN_USERNAME` + `ADMIN_PASSWORD` 登录；登录后可在右上角修改账号。登录成功后浏览器自动保存短期会话，用户不需要填写管理令牌。`ADMIN_TOKEN` 的后端兼容入口保留给维护脚本。设备使用设备凭据，脚本取码使用单独的 API Client Bearer Token。

PowerShell 可在本机生成 URL 安全随机值：

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
```

3. 启动并查看状态：

```powershell
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml up --build -d
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml ps
```

启动或更新前先运行 `deploy/preflight.sh deploy/.env`；Windows 本地可用 `pwsh -NoProfile -File deploy/preflight.ps1 -EnvFile deploy/.env -SkipDocker -SkipDns` 做配置检查。

生产更新必须固定 Compose 项目名。已有服务器若真实数据在 `deploy_postgres_data`，所有 Compose 命令都显式使用 `-p deploy`；不要让 Compose 根据解压目录推导项目名。更新脚本会自动锁定已有项目，并在发现两个项目共用同一份 `compose.yml` 时拒绝更新。双栈、空库、备份恢复和 `message_date` / `received_at` / `created_at` 三个时间字段的详细说明见 `deploy/README.md`。

容器启动顺序为 PostgreSQL 健康检查 → Alembic `upgrade head` → FastAPI 健康检查 → Caddy。默认管理界面是 `http://服务器IP:39300/`；域名解析完成后设置 `RELAY_DOMAIN=你的域名`、`PUBLIC_BASE_URL=https://你的域名`，并将 `HTTPS_PORT` 按需改为 `443`，重启后两个入口同时可用。详细说明见 `deploy/README.md`。

## 开发验证

后端只使用项目内虚拟环境：

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app tests migrations
.venv\Scripts\python.exe -m mypy app tests
```

前端与 iOS 契约：

```powershell
cd web
npm test -- --run
npm run typecheck
npm run build

cd ..\ios-shortcut
node --test tests\*.test.js
```

## 存储边界

- 服务器生产数据：只使用 `deploy/.env` 中显式配置的 PostgreSQL `DATABASE_URL`，持久化到 Compose 命名卷 `postgres_data`。
- 本地管理台预览：只使用绝对路径 `debug/preview.db`；带管理员令牌访问 `/api/v1/admin/runtime`，用返回的 `database_fingerprint` 确认当前 8000 端口连接的具体库。
- Android 普通应用：加密待发队列位于应用私有 Room 文件 `sms-relay-queue.db`，由 Android 卸载/清除应用数据管理，不是服务器数据库。
- Android Root 模块：配置、游标、spool、inflight 与运行状态位于 `/data/adb/sms-relay`；模块卸载脚本会停止本模块进程并清理该目录和对应 worker 锁。
- Root 读取器的 SQLite fallback 只读取 Android 系统短信库并维护自己的游标，不会创建或切换服务器数据库。

工作区内其他 `.db` 文件视为测试产物或旧离线备份，默认由 Git 忽略；它们只有在某个启动入口显式引用时才可能成为活动库。

## 客户端认证参数

三类凭据不能混用，按客户端填写：

| 客户端 | 上报或取码接口 | 认证参数 | 设备 ID |
| --- | --- | --- | --- |
| iPhone 快捷指令 | `POST /api/v1/ingest/shortcut` | `Authorization: Bearer <iPhone 设备 Token>` | 手机端不填写，服务端从 Token 识别设备 |
| Android 应用 / Android Root | `POST /api/v1/ingest/messages` | 设备 UUID + HMAC 密钥；客户端生成时间戳、nonce 和签名 | 必填，用于选择对应设备密钥 |
| 合宙 724 / Python / 其他取码脚本 | `WebSocket /api/v1/ws/otp`、`POST /api/v1/otp/claim` | Script API Token + 目标手机号 | 不要求；仅在需要进一步限定来源设备时作为可选筛选条件 |

iPhone 设备 Token、Android/Root HMAC 密钥和 Script API Token 是三种独立凭据。脚本取码认证不会要求 iPhone 快捷指令或脚本提交 Android/Root 的设备 UUID。

Root 模块默认使用 HTTP；也可在模块控制台选择 WebSocket 或 MQTT。MQTT 上报使用 `relay/v1/devices/<device_id>/messages` 标准主题，WS/MQTT 失败时可回退到 HTTP 队列。Root 传输配置、Broker 地址和主题约束见 `clients/root-module/README.md`。

## 核心 API

| Method | Path | 鉴权 | 用途 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | 无 | 健康检查 |
| `POST` | `/api/v1/admin/devices` | `X-Admin-Token` | 注册 Android/iPhone 设备 |
| `POST` | `/api/v1/admin/devices/{id}/rotate-credential` | `X-Admin-Token` | 轮换一次性设备凭据 |
| `DELETE` | `/api/v1/admin/devices/{id}` | `X-Admin-Token` | 撤销设备 |
| `POST` | `/api/v1/ingest/shortcut` | iPhone 设备 Bearer | 接收整条快捷指令输入，由服务端生成协议字段和幂等键 |
| `POST` | `/api/v1/ingest/messages` | HMAC 或 Bearer | 接收 Android 或兼容旧客户端构造的完整协议消息 |
| `POST` | `/api/v1/admin/api-clients` | `X-Admin-Token` | 创建 Claim API Token |
| `WebSocket` | `/api/v1/ws/otp` | Script API Token 首帧鉴权 | 实时订阅并接收新 OTP |
| `POST` | `/api/v1/otp/claim` | API Client Bearer | 原子领取一个可用访问码 |
| `GET` | `/api/v1/admin/messages` | `X-Admin-Token` | 消息记录 |
| `GET` | `/api/v1/admin/otps` | `X-Admin-Token` | 访问码状态 |

### 脚本取码流程

1. 设备把新短信上报后，服务端解析出验证码并创建独立的 OTP `id`；OTP 的 `message_id` 只用于关联来源短信记录，不需要脚本提交或展示短信正文。
2. 正常取码使用 `/api/v1/ws/otp`：先发送 `{"type":"auth","token":"脚本 API 令牌"}`，再发送 `{"type":"subscribe","phone_number":"+8613800138000"}`。平台只推送订阅后新产生并匹配的验证码，不发送短信正文。
3. 断线重连后可以调用一次 `POST /api/v1/otp/claim` 补取，`Authorization: Bearer ...` 使用同一个脚本 API 令牌；请求必须提供 `phone_number`，设备 ID 不要求填写。服务端按入库时间倒序领取最新的匹配记录，返回 `code`、`otp_id`、`message_id`、`created_at` 和 `expires_at`。
4. Claim 的手机号是必填范围，设备、发送方和正文关键词是可选筛选条件；发送方和关键词均为不区分大小写的包含匹配。没有可用记录时返回 `404 otp_not_available`，缺少手机号时返回 `422 phone_number_required`。
5. 更旧但仍有效的 OTP 不会因领取最新一条而被批量删除，所以脚本不能持续轮询 Claim。在线时保持 WebSocket；断线恢复只 Claim 一次，并由客户端按 `otp_id`/`created_at` 去重过滤。
6. 可用 OTP 到期立即清理；成功领取的 OTP 保留 24 小时后清理。领取动作只代表服务端已完成原子选择并返回响应，不等待脚本额外回执，也不需要 ACK 接口。
7. WebSocket 首帧认证和首次手机号订阅分别有 10 秒超时；订阅后 3 分钟没有客户端活动会自动断开。每条控制帧限制为 8 KiB，只允许一次订阅，客户端应在 3 分钟内发送应用层 `{"type":"ping"}` 保持连接；仓库中的合宙和 Python 客户端默认每 25 秒发送一次。
8. 连续使用无效脚本令牌的同一 IP 达到 5 次后会暂时锁定脚本认证；快捷指令、Android 和 Root 的设备上报认证不经过这个脚本令牌锁定。
| `POST` | `/api/v1/admin/retention/run` | `X-Admin-Token` | 立即执行保留清理 |

所有错误统一为 `{"error":{"code":"...","message":"..."}}`。设备密钥和 API Client Token 只在创建或轮换响应中显示一次，不会出现在列表接口。

## 客户端与验收

- Android：`android/README.md`、`android/docs/private-distribution.md`、`android/docs/real-device-validation.md`。
- 会话接替审查报告：`docs/SESSION_01A06BD1_HANDOFF.md`。
- iPhone：`ios-shortcut/README.md`、`ios-shortcut/shortcut-definition.md`、`ios-shortcut/VALIDATION.md`。
- Android 协议 smoke：`tests/android_ingest_e2e.ps1`。
- iPhone 协议 smoke：`ios-shortcut/smoke.ps1`。
- 通用脚本 WebSocket/Claim 客户端：`clients/script_api/otp_client.py`。
- 通用脚本真实 TCP 端到端测试：`backend/tests/test_script_api_network.py`。

smoke 脚本只打印状态码与错误代号；不会输出令牌、设备密钥、发送方或消息正文。真实凭据仅通过环境变量注入。

## 当前验证边界

本仓库的后端 pytest、Ruff、mypy、前端 Vitest/TypeScript/生产构建、iOS Node 契约和 PowerShell 语法均可自动验证。PostgreSQL/Caddy 容器启动需要本机 Docker；Android APK、WorkManager 行为和真机接收需要 JDK 17、Android SDK 与授权测试设备；iOS 自动化需要真实 iPhone。缺少这些外部条件时只能标记为“尚未执行真机/容器验收”，不能据此宣称端到端完成。
