# Compose 部署

## 配置

在服务器上只上传发布包，不上传工作区和 `deploy/.env`：

```powershell
pwsh -NoProfile -File deploy/package.ps1
```

把生成的 `deploy/dist/sms-relay-hub-deploy.zip` 上传到服务器后解压，再在服务器上执行下面的初始化和 Compose 命令。

全量部署包不嵌入容器全量包和容器在线更新包，只内置 Root 模块、iPhone 快捷指令、Android 采集端、Android 管理端和 Air724 脚本资源；各客户端发布历史最多打包最近两个版本。执行 `pwsh -NoProfile -File deploy/prune-dist.ps1 -Keep 2` 可把本机 `deploy/dist` 中更旧的全量/更新包移动到 `.backups/deploy-dist`，不会删除文件。

```powershell
pwsh -File deploy/bootstrap.ps1
```

初始化脚本只在缺少值时生成 `ADMIN_PASSWORD`、`ADMIN_TOKEN`、`DATA_ENCRYPTION_KEY`、`POSTGRES_PASSWORD` 和 `DATABASE_URL`，不会覆盖已有的非空凭据。首次生成管理员密码或恢复令牌时，会在终端打印管理员用户名、密码和恢复令牌；恢复令牌保留给维护脚本使用，管理台不提供填写入口。重复执行不会再次打印已有密码。生成的 `deploy/.env` 不要提交到 Git。`DATA_ENCRYPTION_KEY` 是 32 字节随机值的 base64url 编码，数据库密码会写入连接串并自动 URL 编码。

管理台只使用初始化输出的管理员用户名和密码登录；登录后可在右上角账号设置中修改用户名和密码，浏览器会自动保存短期会话。恢复令牌不在管理台显示或填写，但后端兼容入口仍可供维护脚本使用。Android/Root/iPhone/Ve 设备使用设备页创建时显示一次的 HMAC/Bearer 凭据；合宙 724 在设备页选择“合宙 724 取码”后显示一次脚本 API 令牌；普通外部脚本仍在总览的“接口凭据”中创建独立 API Client Bearer Token。令牌用途不同，不能互换。

合宙 Air724 取码脚本连接同一站点的 `/api/v1/ws/otp`。Caddy 的 `reverse_proxy` 会自动转发 WebSocket Upgrade，不需要单独开放一个 WebSocket 端口；HTTP 站点对应 `ws://`，HTTPS 站点对应 `wss://`。

当前实时 OTP broker 保存在应用进程内存中，因此应用固定使用 `--workers 1`。在接入 Redis 或 PostgreSQL 通知 broker 前，不要横向扩容应用 worker，否则不同进程中的 WebSocket 连接无法收到彼此创建的 OTP 事件。PostgreSQL 仍是消息和取码记录的持久数据源，HTTP Claim 可用于断线恢复。

端口默认值是宿主机 HTTP 入口 `39300`、容器内部应用 `39200`；应用容器不直接暴露到公网。部署前可在 `.env` 自定义宿主机 `HTTP_PORT` 和应用容器内部 `APP_PORT`。Compose 的映射关系是：宿主机 `39300 -> Caddy 容器 80`、Caddy 容器再转发到应用容器 `39200`。

TLS 由上游反向代理（如宝塔 nginx、Cloudflare）终结：外部 `https://域名` 的请求在上游完成解密后，以 HTTP 转发到 `39300`，Caddy 不再监听 443（`HTTPS_PORT` 仍保留在 `.env` 中，仅供运行时信息展示）。历史版本曾默认映射宿主机 `39301 -> Caddy 容器 443`（Caddy 自管证书的域名模式）；当前部署如果 TLS 已由上游反代承担，该端口无用，已从 Compose 中移除。如需让 Caddy 自己管理 TLS 证书，手动在 `compose.yml` 补回 `"${HTTPS_PORT:-39301}:443"` 映射并在 `.env` 设置 `RELAY_DOMAIN` 即可。

支持双模式自动适配：

- **IP 加端口入口**：`RELAY_SITE=:80` 固定不变，客户端使用 `http://服务器IP:39300`。默认部署就是这种模式。
- **域名 HTTPS 入口**：推荐由上游反向代理（宝塔 nginx / Cloudflare）终结 TLS 后转发到 `39300`；客户端使用 `https://你的域名`。

设备侧可以继续用原来的 `IP:端口`，也可以直接填域名，设备 UUID 和密钥不变。

### Android 管理端连接

生产服务器上的 Android 管理端填写网页管理台同一个入口：域名模式填 `https://你的域名`，IP 模式填 `http://服务器IP:39300`。在电脑上的雷电模拟器内测试时，不要填 `127.0.0.1` 或 `10.0.2.2`，应填电脑当前局域网 IP，例如 `http://192.168.5.7:39300`。1.0.2 起，连接失败会显示实际请求地址、HTTP 状态或系统原因，便于区分地址错误、鉴权失败和后台无数据。

公开手机接入时，`RELAY_DOMAIN` 必须是已解析到服务器的真实域名，由 Caddy 自动申请可信证书。HTTP 私网测试不要求域名或 HTTPS，可以直接使用 `IP:端口`。不要手工设置或修改 `COMPOSE_PROJECT_NAME`：Docker Compose 会沿用 `deploy` 目录推导出的历史项目名，手工改成其他值会切到另一套空数据库卷。该键只由 `deploy/apply-update.sh` 在识别出真实项目名后自动回写，`deploy/preflight.sh` 会在它与实际运行的项目名不一致时直接报错。

### Android / Root 设备二维码配置

网页管理台的“设备”页面可为已启用的 Android HMAC 设备和 Root 模块设备生成“扫码配置”二维码。二维码只包含服务器地址、设备 ID 和短期一次性 enrollment token，不包含长期 HMAC 密钥；客户端扫码或导入配置后由服务器一次性下发凭据。Android 采集端会保存到 Android Keystore，Root 模块按模块配置说明写入模块配置。二维码默认 5 分钟有效且只能成功使用一次，设备凭据轮换、撤销、恢复或删除时，未使用的二维码也会失效。

生产环境请在 `deploy/.env` 设置 `PUBLIC_BASE_URL=https://你的域名`，然后重启应用；二维码会优先使用该地址。若不设置，后端会根据反向代理传入的 `X-Forwarded-Proto` 和 `X-Forwarded-Host` 推导地址，因此必须确保 Caddy/网关正确转发这两个请求头。不要把 `127.0.0.1`、容器名或仅服务器内网可访问的地址放进二维码。

使用流程：网页管理台登录 → 设备页 → 选择 Android HMAC 或 Root 模块设备 → 点击二维码图标。Android 采集端点击“扫描后台二维码自动配置”；Root 模块按弹窗中的扫码内容完成导入或手动配置。扫码成功后，客户端会自动写入 API 地址和设备凭据并启动转发；手动填写方式仍可作为备用方案。

部署或更新前先运行配置自检：

```bash
sh deploy/preflight.sh deploy/.env
```

Windows 本地验证可使用：

```powershell
pwsh -NoProfile -File deploy/preflight.ps1 -EnvFile deploy/.env -SkipDocker -SkipDns
```

自检会检查必需凭据、HTTP/HTTPS/App 端口、`RELAY_SITE`/`PUBLIC_BASE_URL` 形状、DNS、Compose 配置和宿主机端口占用。更新已有实例时，端口占用只作为警告，因为监听端口可能正是当前实例。

自检还包含单实例守卫，用于在部署前就拦住双栈状态：

- 读取容器标签（只用 `{{.Label "键"}}`），统计有多少 Compose 项目在用本安装的 `deploy/compose.yml`。
- 识别到两个及以上项目时报错退出，提示保留有真实数据的项目、只移除已确认为空的项目。
- 识别到一个项目时打印 `Compose project in use: <项目名>`；若 `.env` 里的 `COMPOSE_PROJECT_NAME` 与它不一致则报错，这正是当初生成 `relay` 空栈的操作。
- 没有匹配到项目但 `.env` 手工写了 `COMPOSE_PROJECT_NAME` 时给出警告。
- 宿主机存在多个 `*_postgres_data` 卷时给出警告，提示先确认哪个卷有真实数据。

Docker 不可用或标签查询失败时，这些检查降级为警告，不会阻塞纯配置自检。

## Compose 项目名与数据隔离（必须先读）

Compose 的项目名决定容器名和命名卷名。以项目名 `deploy` 为例：

| 对象 | 名称 |
| --- | --- |
| Compose 项目 | `deploy` |
| PostgreSQL 容器 | `deploy-postgres-1` |
| 应用容器 | `deploy-app-1` |
| Caddy 容器 | `deploy-caddy-1` |
| PostgreSQL 数据卷 | `deploy_postgres_data` |

同一个 `compose.yml` 如果被两个项目名启动，就会得到两套容器和两个数据库卷。切换项目名不会迁移数据，只会让新栈连接到另一个空卷。此前服务器同时出现 `deploy` 和 `relay` 两个项目，真实数据在 `deploy_postgres_data`，`relay_postgres_data` 是新空库；所谓“密码失效、设备短信消失”就是入口连到了空栈，不是原数据被更新脚本删除。

### 更新前检查

在服务器上先执行：

```bash
docker compose ls -a
docker ps -a \
  --filter label=com.docker.compose.project \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}\t{{.Label "com.docker.compose.project.config_files"}}\t{{.Label "com.docker.compose.project.working_dir"}}'
docker volume ls --format '{{.Name}}' | grep -E '_postgres_data$'
```

`docker ps` 的 `{{.Labels}}` 是一个逗号拼接的字符串，不是字典，因此 `{{ index .Labels "键" }}` 属于模板错误，输出为空并会误判成“没有任何 Compose 项目”。查询单个标签必须使用 `{{.Label "键"}}`。

确认真实栈的方法是查数据量，而不是只看容器是否 healthy：

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'admin_accounts',count(*) from admin_accounts;"
```

如果 `relay-postgres-1` 也是由同一份 `/root/relay/deploy/compose.yml` 创建的，同样查询它。设备、短信和管理员密码匹配的一套才是真实栈；`devices=0`、`messages=0` 的一套是空栈。

`deploy/apply-update.sh` 会读取容器标签识别真实栈，判定规则是：

- 只统计 `com.docker.compose.service` 为 `app` 或 `caddy` 的容器（`docker ps -a`，包含已停止容器）。
- 命中条件是 `com.docker.compose.project.config_files` 包含本安装的 `deploy/compose.yml`，**或** `com.docker.compose.project.working_dir` 等于本安装的 `deploy` 目录。第二个条件保证用相对 `-f` 路径启动的栈也能被识别。
- 只发现一个项目时自动锁定该项目；发现两个及以上时直接拒绝更新，避免误连空库。
- `docker ps` 本身失败时直接报错退出，不再静默当成“没有项目”。
- 检测不到已有项目时不会创建备份目录，也不会改动任何文件；错误信息会打印期望的标签值和全部可见容器行。

确认真实项目名后可以强制指定，脚本仍会校验该项目在本机确实有 `app` 或 `caddy` 容器：

```bash
RELAY_COMPOSE_PROJECT=deploy sh /root/relay-update-source/deploy/apply-update.sh /root/relay /root/relay-update-history
```

正式更新前建议先跑一次只检测模式，它只输出识别结果，不备份、不替换文件、不改动容器：

```bash
RELAY_UPDATE_DETECT_ONLY=1 sh /root/relay-update-source/deploy/apply-update.sh /root/relay
```

### 只移除已确认的空栈

按顺序执行，示例空栈项目名为 `relay`，真实栈为 `deploy`。每一步的回执都要看过再进入下一步。

第一步：列出两套栈和两个卷，确认要删的是哪一个。

```bash
docker compose ls -a
docker volume ls --format '{{.Name}}' | grep -E '_postgres_data$'
```

第二步：分别查数据量。真实栈有设备和短信，空栈应为 0。

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'admin_accounts',count(*) from admin_accounts;"

docker exec relay-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'admin_accounts',count(*) from admin_accounts;"
```

如果 `relay-postgres-1` 已经停止，先 `docker start relay-postgres-1` 再查；如果这一步显示 `relay` 有数据而 `deploy` 为空，立即停止操作，重新判断哪一套是真实栈。

第三步：给真实栈做一次数据库备份，作为后续任何操作的兜底。

```bash
docker exec deploy-postgres-1 pg_dump -U sms_relay -d sms_relay -Fc \
  > /root/relay-backup-before-cleanup.dump
ls -l /root/relay-backup-before-cleanup.dump
```

第四步：只对已确认 `devices=0`、`messages=0` 的空项目执行 `down`。执行前把 `relay` 换成实际空栈项目名。

```bash
docker compose -p relay --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml down --remove-orphans
```

第五步：确认真实栈还在，并且入口仍然连着有数据的那一套。

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml ps
curl -fsS http://127.0.0.1:39300/api/v1/health; echo
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select count(*) from messages;"
```

`down` 不带 `-v` 不会删除命名卷。空卷 `relay_postgres_data` 先保留，作为事故证据和回退余地；确认不需要后再人工执行 `docker volume rm relay_postgres_data`，并且执行前必须再次确认卷名不是 `deploy_postgres_data`。绝对禁止对真实栈执行 `docker compose down -v`、`docker volume rm deploy_postgres_data`、`docker rm` 后重建卷，或把 `DATABASE_URL` 改到另一个空卷。真实栈若已停止，用下面的命令恢复启动：

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml up -d
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml ps
curl -fsS http://127.0.0.1:39300/api/v1/health; echo
```

### 备份、导出和恢复必须绑定项目名

所有 `pg_dump`、`pg_restore`、日志查看和容器 exec 都必须显式带 `-p 项目名`。例如真实栈为 `deploy`：

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml \
  exec -T postgres pg_dump -U sms_relay -d sms_relay -Fc > /root/relay-backup.dump
```

恢复前先停止 `app`，在测试环境验证备份；确认要恢复时再执行：

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml stop app
cat /root/relay-backup.dump | docker compose -p deploy --env-file /root/relay/deploy/.env \
  -f /root/relay/deploy/compose.yml exec -T postgres \
  pg_restore -U sms_relay -d sms_relay --clean --if-exists
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml up -d
```

`DATA_ENCRYPTION_KEY` 必须和备份时一致，否则数据库里的敏感字段无法解密。不要用“当前目录推导出的项目名”执行数据库操作；目录名、解压目录名和 Compose 项目名是三个独立概念。

## 短信时间字段口径

平台里有三个时间，不能混用：

| 字段 | 含义 | 来源 |
| --- | --- | --- |
| `message_date` | 短信原始时间 | 设备/快捷指令 metadata，能取到时最优先 |
| `received_at` | 设备上报时间 | 客户端 payload，代表设备当时的时间 |
| `created_at` | 服务端入库时间 | PostgreSQL/后端生成，用于分页和补偿游标 |

管理端和 Web 实时短信的主显示时间统一为 `message_date ?? received_at`；`created_at` 只作为“上报时间/入库时间”单独显示，不当作短信时间。实时断线补偿的 cursor 继续使用 `created_at`，因为后端接口按入库时间分页，这是为了不漏消息，不代表两个时间必须一致。

如果看到 `12:31:05` 与 `12:31:58`，本质上是“设备收到/上报时间”和“服务端入库时间”相差 53 秒，可能来自设备时钟偏差、队列延迟、网络重试或入库处理耗时；双栈不会造成同一条记录的两个时间字段相差几十秒。双栈的典型症状是整套设备、短信、管理员账号完全不一致，或一边为空。

## 启停

以下示例假设真实项目名为 `deploy`；如果你的实例不是 `deploy`，先按上一节确认项目名，并把 `-p deploy` 替换成实际值。

```powershell
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml up --build -d
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml ps
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml logs --tail 100 app
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml down
```

### 可选 MQTT 桥接（Air724 短信上报）

MQTT 不是默认依赖。只有使用 `clients/air724/mqtt_ingest.lua` 把 Air724 收到的短信上报时，才在 `deploy/.env` 设置 `MQTT_BROKER_HOST`、可选的账号/TLS 参数，并生成一段不少于 32 位的 `RELAY_MQTT_BRIDGE_TOKEN`。该令牌只在内部桥接容器调用 `/api/v1/ingest/mqtt` 时使用，不是 Air724 的设备 HMAC 密钥。

```bash
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml --profile mqtt up --build -d
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml --profile mqtt ps
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml logs --tail 100 mqtt-bridge
```

外部 Broker 的账号、TLS 和 WebSocket 路径由 `MQTT_*` 环境变量控制；不启用 `mqtt` profile 时，桥接服务不会启动，也不会改变现有 HTTP/WebSocket 上报链路，且普通更新/启停不要求填写 MQTT 变量。只有明确执行 `--profile mqtt` 时，才需要在 `.env` 中填写 Broker 地址和 `RELAY_MQTT_BRIDGE_TOKEN`；缺少时桥接容器会直接退出并在日志中提示缺少配置。
`MQTT_CLIENT_ID` 使用默认值即可；只有高级运维明确修改部署拓扑时才需要调整，避免 Broker 互相踢下线。

应用容器为只读根文件系统，启动时先执行 Alembic 迁移。生产数据库只由显式 `DATABASE_URL` 指向 PostgreSQL；持久数据固定保存在命名卷 `postgres_data`，不会读取工作区中的 `.db`、`debug/preview.db` 或客户端本地队列。Caddy 数据保存在 `caddy_data` 和 `caddy_config`。

部署后先检查公开的 `/api/v1/health`，再从管理台登录后检查 `/api/v1/admin/runtime`：`database_backend` 必须是 `postgresql`。`database_fingerprint` 是排除密码后的数据库连接身份短哈希，只用于比对是否连到预期实例，不会回显连接串或密码。

Air724 部署验收还应检查：浏览器或脚本可以连接 `ws(s)://你的站点/api/v1/ws/otp`；发送 `auth` 后收到 `auth_ok`；发送 `subscribe` 后收到 `subscribed`；新短信进入平台后收到 `otp`。真实 Air724/Air724U 仍需使用旧版 Lua 5.1 固件完成硬件验收。

## 备份与恢复

备份只应写入服务器受控目录，且文件权限应限制为管理员：

```powershell
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml exec -T postgres pg_dump -U sms_relay -d sms_relay -Fc > relay-backup.dump
```

恢复前先停止 `app`，在独立测试环境验证备份，然后使用 `pg_restore --clean --if-exists` 恢复。`DATA_ENCRYPTION_KEY` 必须与备份时一致，否则敏感字段无法解密；该密钥应和数据库备份分开保管。

## 更新

```powershell
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml build --pull app
docker compose -p deploy --env-file deploy/.env -f deploy/compose.yml up -d
```

每次更新都会先运行幂等 Alembic 迁移。更新后检查 `/api/v1/health`、管理端设备列表和一组脱敏协议 smoke。

`deploy/apply-update.sh` 更新现有实例时会对 `app` 执行 `docker compose build app`。Docker 按内容寻址失效层：`COPY backend/`、`COPY web/` 的源码一变，对应构建层必然重建，更新一定生效；未变化的基础镜像与依赖层则复用，避免每次更新往构建缓存里堆 1~2 GB（历史版本曾用 `--no-cache` 全量重建，是服务器磁盘被构建缓存吃满的主因）。仅当怀疑构建缓存异常时手动执行一次 `docker compose build --no-cache app`，随后用 `docker builder prune -f` 回收。

在线更新包会携带过滤后的客户端 release 库，更新后服务器在线目录只保留当前包声明的 Root 模块、快捷指令、采集端、管理端等客户端资源，不会继续提供历史容器部署包。旧在线文件会完整保存在本次更新备份目录；本机 `deploy/dist` 的更旧包由 `prune-dist.ps1` 移到 `.backups/deploy-dist`。
