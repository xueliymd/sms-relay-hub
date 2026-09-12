# 部署审查 2026-08-23

> **时效说明**：标题里的 08-23 是本文**首次写作日期**；正文已在 **2026-09-12** 修订
> （容器端口 `8000` → `APP_PORT` 默认 `39200`、443/QUIC 映射已移除、失败回滚改为**同时还原数据库**、
> 健康检查补 `start_period: 60s`）。请以文末「2026-09-12 更正与补充」一节为准，
> 不要据文件名判断时效。

## 结论

当前发布包可以部署 Android 应用、Android Root、iPhone 快捷指令、Air724 取码客户端和通用脚本 API 客户端。Air724 与普通脚本均使用平台 WebSocket 实时接收 OTP，HTTP Claim 只用于断线恢复。

部署必须保持一个 FastAPI worker。当前 WebSocket broker 是进程内对象，多 worker 会把连接和新 OTP 分散到不同进程。需要横向扩容时，必须先把 broker 替换为 Redis Pub/Sub、PostgreSQL LISTEN/NOTIFY 或其他共享消息总线。

## 发布内容

- 后端 API、数据库迁移和定时清理任务。
- React 管理台和教程资源。
- `Relay-SMS-Forwarder.apk` Android 应用。
- `sms-relay-root-module-0.3.7.zip` Android Root 模块。
- `ios-sms-config.shortcut` iPhone 快捷指令。
- `air724-otp-websocket.lua` Air724 单文件客户端。
- `air724-otp-websocket/` Air724 客户端、参数示例和说明。
- `clients/script_api/otp_client.py` 普通 Python/自动化脚本的 WebSocket 与 Claim 参考客户端。

旧 SIM 卡板主动适配器已经从活动代码和设备创建选项中删除。设备 SIM 号码绑定仍保留，因为 Android 和 Root 双卡路由依赖这组数据。

## 网络

- Caddy 对外只映射 `.env` 中的 `HTTP_PORT`（容器内固定监听 `:80`）。`HTTPS_PORT` 仍保留在 `.env`，但仅供 `/admin/runtime` 运行时信息展示：443/QUIC 端口映射已移除，TLS 由上游反代（宝塔 nginx）终结。
- FastAPI 容器内部监听 `APP_PORT`（默认 `39200`），不直接暴露公网。
- `RELAY_SITE=:80` 支持纯 HTTP/IP 部署，客户端使用 `http://服务器IP` 和 `ws://服务器IP/api/v1/ws/otp`。
- 使用真实域名时，将 `RELAY_SITE` 改为已经解析到服务器的域名；证书在上游反代申请，Caddy 侧只做 HTTP 上游。
- Caddy `reverse_proxy app:{$APP_PORT}` 自动处理 WebSocket Upgrade，不需要额外 WebSocket 端口。

## 凭据

- `ADMIN_TOKEN`：管理台连接和管理 API。
- `DATA_ENCRYPTION_KEY`：数据库敏感字段加密，恢复数据库时必须使用原值。
- 设备 HMAC/Bearer：Android、Root 和 iPhone 上报认证。
- Script API Token：Air724 或普通脚本的 WebSocket 和 HTTP Claim 认证。
- 合宙 724 设备令牌：从“设备”页添加“合宙 724 取码”设备时生成；它关联设备生命周期，仅允许取码认证，不允许短信上报。

`deploy/bootstrap.ps1` 仅为缺失项生成随机值，不覆盖已有非空配置。生产备份必须同时保管数据库和原 `DATA_ENCRYPTION_KEY`。

## Air724 版本

当前客户端面向 Air724/Air724U，使用合宙旧版 `openLuat/Luat_Lua_Air724U` Lua 5.1 API。新版 LuatOS 的 WebSocket API 不用于这个脚本。后续适配新版硬件时复用服务端帧协议，单独提供新的客户端入口。

## 验收

1. `docker compose ... ps` 显示 `postgres`、`app`、`caddy` 正常。
2. `/api/v1/health` 返回成功。
3. `/api/v1/admin/runtime` 显示 `database_backend=postgresql`。
4. 管理台可创建上报设备和 Script API Token。
5. Android、Root 或 iPhone 上报真实短信后，管理台出现一条去重后的消息。
6. Air724 或普通脚本 WebSocket 完成 `auth`、`subscribe`，并收到对应号码的新 `otp` 事件。
7. 中断网络后恢复，客户端只执行一次 HTTP Claim 恢复。
8. `backend/tests/test_script_api_network.py` 通过真实 Uvicorn TCP 端口验证普通脚本的 WebSocket 推送、精确验证码、`message_id` 对齐和 Claim。

自动化测试可以验证协议和发布文件。真实 Air724 网络、固件和 Lua 运行时属于硬件验收，不能由本机单元测试替代。

---

## 2026-09-12 更正与补充

本文写于 2026-08-23，以下内容已随代码演进，阅读"网络"一节时以上文更正后的说法为准：

1. **容器端口**：`8000` → `APP_PORT`（默认 `39200`）。`deploy/Caddyfile` 用 `reverse_proxy app:{$APP_PORT}`，compose 的 app、mqtt-bridge 健康检查与内部地址全部用该端口。旧文档写的 `8000` 已失效。
2. **对外监听**：Caddy 只映射 `HTTP_PORT`（默认 `39300` → 容器 `:80`），443/QUIC 已移除；TLS 由上游反代终结。
3. **回滚语义变更（重要）**：`deploy/apply-update.sh` 现在失败回滚时**同时还原数据库**（`pg_restore --clean --if-exists`），因为 `entrypoint.sh` 会先跑 `alembic upgrade head` 再做健康检查，迁移必然已提交。此前"只恢复源码"的做法会在库版本前移时把旧代码打崩（2026-09-12 生产事故的根因）。
4. **健康检查**：app 增加 `start_period: 60s`，避免冷启动被 6×10s 探测误判为 unhealthy 从而误触发整包回滚。
5. **发布内容**：本文"发布内容"一节是 2026-08-23 的快照，此后新增了管理端 App、远程控制（`device_commands` 迁移 0010）、转发规则（迁移 0009）等，请以 `web/public/assets/releases/release-catalog.json` 为准。
6. **验收第 4 条**：管理台除创建上报设备与 Script API Token 外，还可创建管理端 App 使用的 HMAC 设备。设备上报路由集中在 `backend/app/api/relay.py`，仓库中**没有**独立的 `api/devices.py`。
