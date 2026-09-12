# 会话 01A06BD1 接替审查报告

## 任务范围

接替会话 `01A05a86`，继续 SMS Relay Hub Phase 1 的修复、部署、打包与服务器更新收尾。本文记录 2026-09-04 至 2026-09-05 的工作结论，避免后续更新再依赖聊天记录或口头结论。

## 需求时间线

- 接替上一会话并盘点未完成项。
- 保留主端口，支持域名与 `IP:39300` 双模式自动适配。
- 部署包瘦身：不嵌套历史部署包，只保留 Root、快捷指令、采集端与管理端制品。
- 修复 Root 设备二维码按钮缺失。
- 修复软件管理端读取不到信息、补拉遗漏短信和时间戳错乱。
- 修复 Root 控制台与软件管理端排版。
- 排查采集端与 Root 上报链路的超时、重试和队列清理逻辑。
- 处理服务器更新反复失败、双 Compose 栈与数据“看似丢失”问题。

## 已完成

- 采集端 `1.0.14`（versionCode 15）。
- 管理端 `1.0.5`（versionCode 6）。
- Root 模块 `0.3.10`（versionCode 22）。
- 域名与 `IP:39300` 双模式自动适配。
- 部署包从约 133.7MB 瘦身到约 32.9MB。
- Root 设备二维码按钮补齐。
- 软件管理端空发件人解析失败修复，补拉线程改为捕获 `Throwable`。
- 时间戳统一使用 `message_date ?? received_at`，`created_at` 仅代表入库时间。
- Root 上报失败后立即退避，不再逐条等待超时。
- 采集端队列只清理已完结记录，不再挤掉 `PENDING` 短信。
- WS/MQTT 超时从 30 秒降到 10 秒；HTTP 从 15/30 秒降到 5/15 秒。
- Root 控制台改为吸顶分区导航 + 卡片布局。
- 管理端改为固定筛选头 + 独立滚动列表 + 局部刷新不跳顶。
- 更新脚本栈识别缺陷修复（`{{.Label "键"}}`）。
- `deploy/preflight.sh` 与 `deploy/preflight.ps1` 增加单实例守卫；`preflight.sh` 修复“全部通过仍退出 1”的缺陷。
- `deploy/preflight.sh` 修复 `PUBLIC_BASE_URL` 形状校验误报（只要非空就报错）。
- `deploy/package.ps1` 改为手写 zip 条目名，消除服务器 `unzip exit=1` 警告。
- `deploy/package.ps1` 写包内 catalog 改为无 BOM，修复服务器发布历史与全部客户端在线更新 503。
- `backend/app/services/release_catalog.py` 改用 `utf-8-sig` 读取，带 BOM 的历史文件也能解析。
- `deploy/bootstrap.ps1` 写 `.env` 改为无 BOM，避免 BOM 落在第一个键名上。
- 后端 226 项测试通过（其中 `test_deploy.py` 48 项），Web 46 项测试通过，ruff 与 mypy 均为 0 问题。
- 构建环境确认为 JDK 17、Android SDK 与 Gradle 8.9。

## 未完成 / 待办

- 多转发渠道：钉钉、企业微信、飞书、邮箱、Bark、Webhook、Telegram、Server酱、PushPlus、短信转发。
- 远程控制：发短信、查短信、查通话、查话簿、查电量。
- HMS/FCM 厂商推送。
- RCS 原生适配。
- 服务器最终更新结果需以命令回执为准；在服务器验证成功前不得标记为已部署成功。

## 双 Compose 栈事故结论

事故不是数据被删除，而是 Compose 项目名漂移导致入口连到另一套空数据库。

- 历史真实栈：`deploy`。
- 错误新栈：`relay`。
- 真实数据库卷：`deploy_postgres_data`。
- 错误空库卷：`relay_postgres_data`。

早期部署目录为 `deploy/`，Docker Compose 默认推导项目名 `deploy`。后续模板错误加入了 `COMPOSE_PROJECT_NAME=relay`，更新脚本又使用 `-p relay`，因此创建了第二套空栈。入口连接空库后，表现为登录失败、数据“丢失”和管理端不同步。

事故范围只有 `deploy` 与 `relay` 这一对。宿主机上的 `relaysms2_postgres_data`、`relaysms3_postgres_data` 来自此前有意部署的两套额外实例，是正常操作留下的卷，不是事故证据，也不表示项目名再次漂移。判断某个 `*_postgres_data` 卷是否异常，要看它对应的项目是否由本安装的 `deploy/compose.yml` 生成，而不是只看卷的数量。

2026-09-05 行数取证（把卷复制到临时卷后启动临时 postgres 查询，原卷只读挂载）：

| 卷 | devices | messages | admin_accounts |
| --- | --- | --- | --- |
| `deploy_postgres_data` | 3 | 117 | 1 |
| `relay_postgres_data` | 0 | 0 | 1 |
| `relaysms2_postgres_data` | 0 | 0 | 1 |
| `relaysms3_postgres_data` | 0 | 0 | 1 |

三个空卷各有 1 条 `admin_accounts`，是空库初始化时自动创建的管理员。这解释了“管理员密码失效”的完整机理：入口连到空栈后，登录校验的是另一套数据库里的另一个管理员账号，原密码必然不匹配；原数据一直在 `deploy_postgres_data` 里，没有被任何更新脚本删除。

## 单实例边界

- 多用户：已取消，不再作为产品功能。
- 多实例：当前只运行单实例。用户此前有意部署过 `relaysms2`、`relaysms3` 两套实例，因多容器更新繁琐已主动拆除，计划先把单实例更新稳定后再决定是否重新部署。
- 单实例守卫按安装目录判定：只统计使用本安装 `deploy/compose.yml`（或 `working_dir` 等于本安装 `deploy` 目录）的项目，因此另建目录的独立实例不会被它拦住；宿主机存在多个数据库卷只触发警告，不阻塞更新。
- 当前产品按单实例部署。
- 正式栈固定为 `deploy`。
- 正式数据库卷固定为 `deploy_postgres_data`。
- 模板不再默认写入 `COMPOSE_PROJECT_NAME`。
- 禁止使用 `-p relay` 更新正式服务。

## 2026-09-05 更新脚本栈识别缺陷（已修复）

现象：服务器执行 `2026.09.05-compose-project-fix` 包的 `apply-update.sh` 时直接退出，输出 `no existing app or caddy Compose project was detected for this compose file`；但同一时刻 `docker compose -p deploy ... ps` 显示 `deploy-app-1`、`deploy-caddy-1`、`deploy-postgres-1` 全部 `Up`，`/api/v1/health` 返回 `{"status":"ok"}`。即栈完全正常，更新却一次都没落地。

根因：检测函数使用 `docker ps --format '{{ index .Labels "com.docker.compose.project" }}'`。在 `docker ps` 的容器模板里 `.Labels` 是逗号拼接的字符串而不是字典，`index` 作用于字符串属于模板执行错误，输出为空；旧代码又带 `2>/dev/null`，错误被吞掉，于是任何情况下都得到“零个项目”。查询单个标签的正确写法是 `{{.Label "键"}}`。

修复内容（`deploy/apply-update.sh`）：

- 标签一次性取全：`{{.Label "...project"}}|{{.Label "...service"}}|{{.Label "...project.config_files"}}|{{.Label "...project.working_dir"}}`。
- 命中条件为 `config_files` 包含本安装的 `deploy/compose.yml`，或 `working_dir` 等于本安装的 `deploy` 目录，后者覆盖用相对 `-f` 路径启动的历史栈。
- `docker ps` 失败时打印 Docker 原始错误并退出 2，不再静默降级。
- 新增 `RELAY_COMPOSE_PROJECT` 手工指定项目名，脚本仍校验该项目在本机确实有 `app` 或 `caddy` 容器。
- 新增 `RELAY_UPDATE_DETECT_ONLY=1` 只检测模式：只打印识别结果，不备份、不替换文件、不动容器。
- 不再 `. "$TARGET/deploy/.env"`，改为 `env_file_value` 逐键解析，生成的密码不会被当成 shell 代码执行。
- 备份目录改到栈识别成功之后再创建，拒绝更新时不再留下空备份目录。
- 检测失败的报错会打印期望标签值、全部可见容器行和 `RELAY_COMPOSE_PROJECT=<project>` 提示。

回归测试：`backend/tests/test_deploy.py` 新增 7 个行为测试，用桩 `docker` 在只检测模式下跑真实脚本，覆盖 config_files 命中、working_dir 命中、双项目拒绝、无项目拒绝、override 通过、override 校验失败、Docker 查询失败；并静态断言 `{{.Label ...}}` 存在、`index .Labels "com.docker.compose.project"` 不存在、不再 source `.env`。

## 2026-09-05 复查结论与双栈复发防护

对已修复的更新脚本做了一次复查，确认修复在工作区源码和两个部署包内一致，并补上了两个缺口。

复查确认无问题的部分：

- `deploy/apply-update.sh` 使用 `{{.Label "键"}}`，工作区与包内都不含 `index .Labels "com.docker.compose.project"`。
- `deploy/.env.example` 中 `COMPOSE_PROJECT_NAME` 保持注释状态，`bootstrap.sh` / `bootstrap.ps1` 都不会写入该键，并有测试锁定。
- `backend/app/mqtt_bridge.py` 里的 `COMPOSE_PROJECT_NAME` 默认值 `relay` 只用于拼 MQTT 客户端 ID 后缀，与 Docker 语义无关，且 MQTT 是可选 profile，不属于缺陷。
- `deploy/README.md` 的诊断命令、双栈说明和数据量核对方法均已改正。

补上的缺口一：`deploy/preflight.sh` 和 `deploy/preflight.ps1` 之前只做 `docker compose config --quiet`，完全没有双栈检查，也就是说创建第二套空栈的那一刻没有任何环节会提示。现已加入单实例守卫：

- 用 `{{.Label "键"}}` 统计有多少 Compose 项目在用本安装的 `deploy/compose.yml`（命中条件与更新脚本一致：`config_files` 包含本 compose 文件，或 `working_dir` 等于本 `deploy` 目录）。
- 识别到两个及以上项目：报错退出，提示保留有真实数据的项目、只移除已确认为空的项目。
- 识别到一个项目：打印 `Compose project in use: <项目名>`；若 `.env` 的 `COMPOSE_PROJECT_NAME` 与实际项目名不一致则报错，这正是当初产生 `relay` 空栈的操作。
- 没有命中项目但 `.env` 手工写了 `COMPOSE_PROJECT_NAME`：警告。
- 宿主机存在多个 `*_postgres_data` 卷：警告，提示先确认哪个卷有真实数据。
- Docker 不可用或标签查询失败：降级为警告，不阻塞纯配置自检。

补上的缺口二：`deploy/preflight.sh` 结尾原本是 `[ -n "$site_host" ] && printf ...`，在默认的 IP 模式（`RELAY_SITE=:80`、没有 `RELAY_DOMAIN`）下 `site_host` 为空，整个脚本会在打印 `Preflight passed` 之后仍以退出码 1 结束。任何按退出码判断的调用都会误判自检失败。现改成显式 `if` 块并以 `exit 0` 结尾。

回归测试：`backend/tests/test_deploy.py` 新增 6 个测试（合计 34 个），其中 5 个用桩 `docker` 跑真实 `preflight.sh`，覆盖单项目通过并输出项目名、双项目报错退出 1、`COMPOSE_PROJECT_NAME` 与实际项目名不一致报错、多个数据库卷告警、手工设置项目名告警；另有 1 个静态断言两个 preflight 脚本都含 `{{.Label ...}}`、都不含 `index .Labels "com.docker.compose.project"`。第一个测试的环境没有 `RELAY_DOMAIN`，因此同时锁定了“全部通过时退出码必须为 0”。

## 2026-09-05 preflight-guard 上线后的第二轮修复

`2026.09.05-preflight-guard` 已经成功更新到服务器（回执：`using existing Compose project: deploy`、前端真实重建、`deploy-postgres-1` 保持 `Up 5 days` 未被动过、`update complete`、`exit=0`；更新后 `devices=3`、`messages=117`、`admin_accounts=1`，`.env` 的 `COMPOSE_PROJECT_NAME` 已自动回写为 `deploy`）。验收时暴露出两个新缺陷，均与部署链路本身无关，但会误导判断。

### 缺陷一：`preflight.sh` 的 `PUBLIC_BASE_URL` 形状校验必然误报（已修复）

现象：更新后在服务器执行 `sh /root/relay/deploy/preflight.sh /root/relay/deploy/.env`，单实例守卫正常打印 `Compose project in use: deploy`，但脚本以 `ERROR: PUBLIC_BASE_URL must be an origin without credentials, path, query, or fragment` 退出 1。

根因是一条 POSIX glob：`*/[!/]*`。它本意是“URL 里带路径”，但任何 `scheme://host` 都能匹配——`*` 吃掉 `http:/`，字面量 `/` 匹配 `//` 的第二个斜杠，`[!/]` 匹配主机名第一个字符。因此 `https://example.com`、`https://example.com/`、`http://1.2.3.4:39300`、`https://example.com:39301` 全部报错。也就是说：只要 `.env` 里 `PUBLIC_BASE_URL` 非空，自检必然失败；而 `deploy/README.md` 恰恰要求生产环境设置它。

这个缺陷能躲过 34 项测试，是因为测试用的 `_PREFLIGHT_ENV` 里没有 `PUBLIC_BASE_URL`，脚本走的是“空值告警”分支，从未进入形状校验。

修复：先剥掉 scheme 只对 authority 做匹配，并先去掉尾部斜杠（`backend/app/core/config.py` 的 `value.strip().rstrip("/")` 就是这么归一化的，`test_config.py` 也断言 `https://sms.example.test/` 等价于无斜杠版本，所以尾斜杠必须放行）。同时给 `preflight.sh` 补上 `preflight.ps1` 早就有的 `PUBLIC_BASE_URL` 与 `RELAY_DOMAIN` 主机名一致性校验，并给两个脚本都加上“`PUBLIC_BASE_URL` 是 HTTPS 但 `RELAY_DOMAIN` 为空”的告警。

`preflight.ps1` 用 `[Uri]::new()` 判断，从来没有这个缺陷。

回归测试：`_run_preflight` 新增 `extra_env` 参数，用真实脚本覆盖 4 个应通过的 origin（含尾斜杠、含端口、HTTP+IP）、5 个应拒绝的取值（带路径、带凭据、带 query、带 fragment、无 scheme）、域名主机不一致报错、域名模式全绿；另有静态断言锁定旧 glob 不得回归。

### 缺陷二：`package.ps1` 写出反斜杠 zip 条目名（已修复）

现象：服务器 `unzip` 返回 `exit=1`。这只是警告级别，Info-ZIP 会自行把反斜杠当路径分隔符处理，实际解压结果正确（文件数与逐个字节大小都核对过），但每次更新都会看到一个像失败的退出码。

根因：`deploy/package.ps1` 使用 Windows PowerShell 5.1 的内置打包 cmdlet，它写进 zip 的条目名是反斜杠分隔的。`clients/root-module/pack.ps1` 早就手写 `ZipArchive` + `CreateEntryFromFile` + `Replace('\','/')` 解决了同样的问题，所以 Root 包从来没报过这个警告。

修复：`package.ps1` 改成同款写法，`Add-Type` 同时加载 `System.IO.Compression` 与 `System.IO.Compression.FileSystem`；打包后的校验块新增“任何条目名含反斜杠就 throw”的守卫——这条守卫同时保护了它下面所有按路径判断的密钥/数据库/缓存检查（条目名如果是反斜杠，那些检查会静默失效）。`deploy/package-update.ps1` 只是转调 `package.ps1`，所以在线更新包自动同步修复。

改用“只写文件”的循环后，zip 里不再有单独的目录条目（Linux `unzip` 会按文件路径自动建目录，Root 模块包一直如此），文件条目数不变。

回归测试：`test_deploy.py` 新增静态断言——`package.ps1` 不得再调用该 cmdlet、必须含 `CreateEntryFromFile` 与 `Replace('\', '/')`、必须加载 `System.IO.Compression.FileSystem`。

### 缺陷三：包内 release-catalog.json 带 UTF-8 BOM，服务器发布历史全空（已修复）

现象：服务器 `/api/v1/releases/android/history` 返回 503 `release_catalog_invalid`，管理台「客户端与模块下载」显示「暂无发布历史」，采集端 App 内「检查更新」同样失败。不是数据丢失。

根因是 Windows PowerShell 5.1 编码缺陷的第三例，和反斜杠条目名同源：`deploy/package.ps1` 过滤 catalog 后用文本写入 cmdlet 的 `-Encoding UTF8` 写回包内，而 5.1 的每一个 UTF8 编码值都固定输出 BOM。链路是：包内 `web/public/assets/releases/release-catalog.json`（开头 `ef bb bf`）→ Vite 把 `web/public` 原样拷进 `web/dist` → `deploy/Dockerfile` 第 14 行把 `web/dist` 拷到 `/app/app/static` → 容器内 `/app/app/static/assets/releases/release-catalog.json`，正是 `release_manifest_path()` 优先使用的打包路径 → `release_catalog.py` 用 `read_text(encoding="utf-8")` + `json.loads` 读取，`json.loads` 直接拒绝前导 BOM 抛 `ValueError` → `backend/app/api/updates.py` 把 `ValueError` 统一兜底成 503 `release_catalog_invalid`。

本地取证：两个 `preflight-url-zip-fix` 包内的 catalog 条目均为 5,909 字节、开头 `ef bb bf 7b 0d 0a`；工作区同名文件 8,867 字节、开头 `7b 0d 0a`。把工作区这份真实 catalog 加上 BOM 后调用 `load_releases`，抛 `Unexpected UTF-8 BOM (decode using utf-8-sig)`；不加 BOM 则正常读出 9 条记录。所以包内 catalog 内容本身是完整有效的（6 条：android 14/15、manager 5/6、root 21/22，`deploy` 平台按设计不嵌入），只是读不出来。

影响面是所有在线更新通道：管理台发布历史面板、采集端 `AppUpdateClient` 的 `/api/v1/releases/android/latest`、管理端四个平台的发布历史、Root 模块的在线更新检查，全部消费同一份 catalog。且不是今天才坏：09-04 的 `manager-diagnostics` 包用同一个打包脚本生成，大概率从那一版镜像起发布历史就是空的。工作区 catalog 由 `publish-update.ps1` 写入（早就是无 BOM），所以只有打包这条路径中毒。

修复四处：

- `deploy/package.ps1`：catalog 改为 `[System.IO.File]::WriteAllText(..., [System.Text.UTF8Encoding]::new($false))`，并先 `Resolve-Path` 成绝对路径（`WriteAllText` 用的是 .NET 当前目录，不是 PowerShell 的）。读取仍可用 `Get-Content -Encoding UTF8`，读的时候是剥 BOM 而不是加 BOM。
- `deploy/package.ps1` 打包后校验块新增 BOM 守卫：从成品 zip 里取出 catalog 条目，开头是 `EF BB BF` 就 throw，并再 `ConvertFrom-Json` 解析一次。和反斜杠守卫同一思路——同一个缺陷不能再静默发车。
- `backend/app/services/release_catalog.py`：`read_text` 改 `encoding="utf-8-sig"`。有 BOM 剥掉，没 BOM 行为与 `utf-8` 完全一致，因此已部署的旧内容在下次更新后也不再复发。
- `deploy/bootstrap.ps1`：同一家族的潜在缺陷，写 `.env` 也改成 `[System.IO.File]::WriteAllLines(..., [System.Text.UTF8Encoding]::new($false))`。`$defaults` 第一项是 `RELAY_SITE`，是键名不是注释，BOM 会直接粘在键名前面。实测 `sed -n 's/^ADMIN_USERNAME=//p'`（正是 `preflight.sh` 的 `get_value`）对带 BOM 的文件返回空值，也就是自检会把第一个键报成「missing or empty」，Caddy 也拿不到站点地址。`WriteAllLines` 在 Windows 上用 CRLF 并补尾换行，与原 cmdlet 的字节输出一致。

同家族但本次未改的两处（都不在部署包里，属于本机开发用脚本，先报备）：`android/setup-release-signing.ps1` 与 `manager-android/setup-release-signing.ps1` 写 `signing.properties` 时仍用 cmdlet 的 `-Encoding utf8`，第一行同样是键名。它们只在本机配置签名时手工执行，Gradle 读 properties 时通常能容忍 BOM，需要时再改。

回归测试：`test_deploy.py` 新增 2 项（合计 48 项，后端合计 226 项）——静态断言 `package.ps1` 与 `bootstrap.ps1` 都必须含 `[System.Text.UTF8Encoding]::new($false)`、都不得出现 `Set-Content` / `Add-Content` / `Out-File` 任一文本写入 cmdlet、`package.ps1` 必须保留 BOM 守卫；行为测试用带 BOM 的 catalog fixture 断言 `json.loads` 会拒绝、而 `load_releases` 能正常解析，且与不带 BOM 的同一份内容结果相同。另外原有一条断言 `bootstrap.ps1` 使用 cmdlet 写 `.env` 的测试已同步改为断言新写法。

### 影响范围

服务器上当前安装的 `preflight.sh`（7,481 字节）仍是含误报的版本，因此在服务器上跑自检会看到 `exit=1`。这是自检脚本自身的缺陷，不是部署失败：栈识别、数据、健康检查都正常。修复随 `2026.09.05-catalog-bom-fix` 包生效，装上后 `preflight.sh` 应为 8,658 字节。

服务器上发布历史与三端在线更新检查在本次更新落地前都是坏的（503 `release_catalog_invalid`），落地后才恢复。这同样不影响短信中继、设备上报和数据库：故障只在只读的发布目录接口上。

## 服务器上删除空栈的步骤

完整步骤见 `deploy/README.md` 的“只移除已确认的空栈”。要点：

- 先 `docker compose ls -a` 和 `docker volume ls | grep _postgres_data$` 看清两套栈和两个卷。
- 再分别对 `deploy-postgres-1` 和 `relay-postgres-1` 查 `devices` / `messages` / `admin_accounts` 计数，确认哪一套是空的。若结论与预期相反，立即停止操作。
- 删除前先对真实栈做一次 `pg_dump` 兜底备份。
- 只对确认为空的项目执行 `docker compose -p relay --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml down --remove-orphans`，不带 `-v`。
- 事后确认真实栈仍在、`/api/v1/health` 正常、`messages` 计数不为 0。
- 空卷 `relay_postgres_data` 先留作证据，确认不需要后再单独 `docker volume rm relay_postgres_data`，执行前再次核对卷名不是 `deploy_postgres_data`。

2026-09-05 服务器实测结论：`relay` 栈已经不存在容器，`docker compose ls -a` 只剩 `deploy`（running 3，`/root/relay/deploy/compose.yml`）和无关的 `sub2api`，因此不需要执行 `docker compose -p relay ... down`。剩下的只是三个悬空卷：

- `relay_postgres_data`：事故产生的空库卷，确认为空后可删。
- `relaysms2_postgres_data`、`relaysms3_postgres_data`：用户主动拆除的多实例遗留卷，删除前先确认里面是否还有想保留的数据；如果对应实例目录和 `.env` 已经删除，则 `DATA_ENCRYPTION_KEY` 已丢失，卷里的敏感字段永久无法解密，保留价值有限。
- 不要用 `docker volume prune` 或 `docker system prune --volumes` 清理：这两个命令会在栈停止时把 `deploy_postgres_data` 一起删掉。只能按卷名逐个 `docker volume rm`。
- `sub2api` 的卷名不以 `_postgres_data` 结尾，不在清理范围内，不要动。

## 更新安全规则

- 禁止执行 `docker compose down -v`。
- 禁止删除 `deploy_postgres_data` 或任何数据库卷。
- 禁止使用 `-p relay` 更新正式服务。
- 更新脚本必须先通过容器标签确认已有 Compose 项目。
- 标签查询只能用 `{{.Label "键"}}`，禁止 `{{ index .Labels "键" }}`。
- 只发现一个项目时自动锁定该项目。
- 发现两个及以上项目时拒绝更新，避免误连空库。
- 正式更新前先跑 `RELAY_UPDATE_DETECT_ONLY=1`，确认识别到的项目名是 `deploy`。
- 更新前必须完成源码、`.env` 和 PostgreSQL dump 备份。
- 若旧 `.env` 残留 `COMPOSE_PROJECT_NAME=relay`，新脚本在唯一项目确认后自动回写为真实项目名。
- 不要手工编辑 `.env` 里的 `COMPOSE_PROJECT_NAME`；`deploy/preflight.sh` 在它与实际运行项目名不一致时会直接报错。
- 部署或更新前先跑 `sh deploy/preflight.sh deploy/.env`，识别到两套栈时它会拒绝通过。

## 当前部署包

全量包（服务器手工上传使用）：`deploy/dist/sms-relay-hub-deploy-2026.09.05-catalog-bom-fix.zip`

- SHA256：`2fd7d9dd6253de3c79a5770249a1236fd1407115f2fc19fe57ba7e1c83b788ef`
- 大小：32,936,457 字节。

在线更新包（管理台在线更新使用）：`deploy/dist/sms-relay-hub-update-2026.09.05-catalog-bom-fix.zip`

- SHA256：`46f3f977f627f47339a3de379331cc55b1a3a6bc75bccc7d33d15903d914a26c`
- 大小：32,936,457 字节。

两个包内容完全一致，只有文件名与压缩包内时间戳不同；分开命名是为了在服务器上区分手工上传件和在线更新件。

本包相对 `2026.09.05-preflight-url-zip-fix` 的增量：`deploy/package.ps1` 写包内 catalog 改为无 BOM 并新增成品 zip 的 BOM 守卫，`backend/app/services/release_catalog.py` 改用 `utf-8-sig` 读取，`deploy/bootstrap.ps1` 写 `.env` 改为无 BOM；回归测试 `test_deploy.py` 48 项，后端合计 226 项。相对服务器上现装的 `2026.09.05-preflight-guard` 则额外包含 preflight 的 `PUBLIC_BASE_URL` 误报修复和正斜杠 zip 条目名。

哈希以工作区 `docs/SESSION_01A06BD1_HANDOFF.md` 为准。本文件本身被打进包内，所以包内副本无法记录自身哈希：包内那几行是打包时的占位值 `PENDING_*`，构建完成后只回填到工作区副本。在服务器上核对时请以工作区版本或本次交付消息里给出的值为准，并用 `sha256sum` 直接校验上传的文件。

- 包内不嵌套 `deploy/dist`、`web/public/assets/releases/deploy` 或历史部署包。
- 包内保留各客户端平台最近两个版本。
- 包内 `deploy/apply-update.sh` 为 7,993 字节的修复版：含 `{{.Label "键"}}` 标签读取、`RELAY_COMPOSE_PROJECT`、`RELAY_UPDATE_DETECT_ONLY`，不含 `index .Labels "com.docker.compose.project"`。
- 包内 `deploy/preflight.sh` 为 8,658 字节的修复版（服务器上现装的是 7,481 字节的旧版）：含单实例守卫，以 `exit 0` 结尾，`PUBLIC_BASE_URL` 非空时不再误报；`deploy/preflight.ps1` 为 9,750 字节。
- 包内 `deploy/bootstrap.ps1` 为 4,066 字节，写 `.env` 用 `WriteAllLines` + 无 BOM 编码。
- 包内 `web/public/assets/releases/release-catalog.json` 开头为 `7b`（`{`），不含 `EF BB BF`，可被 `json.loads` 直接解析；含 6 条非 deploy 记录。打包脚本在成品 zip 上复核这一点，带 BOM 就直接 throw。
- 包内 122 个条目全部为文件条目，条目名全部为正斜杠，0 个目录条目（Linux `unzip` 按文件路径自动建目录），服务器 `unzip` 不再返回 `exit=1`。已逐条检查 `FullName`，反斜杠条目数为 0。
- 包内更新脚本具备栈识别、多栈拒绝、备份和无卷删除更新能力。

### 已上传但含 preflight 误报的包

`sms-relay-hub-deploy-2026.09.05-preflight-guard.zip`（SHA256 `438aa9b9319770472d48480ac0ca83bfda3bdbeed896ebb797dd6e3d8564abd8`，32,928,172 字节）与同名 update 包（SHA256 `067dbac6e3cc07863b9195dfa0df91be4adb673128e4b24ba362466ac8b2b4b6`）已于 2026-09-05 成功更新到服务器，`apply-update.sh`、单实例守卫和数据都正常，唯一问题是包内 `preflight.sh` 的 `PUBLIC_BASE_URL` 校验必然误报，导致服务器上跑自检返回 `exit=1`；它同时也是带 BOM catalog 的最后一个已上传版本，所以服务器上的发布历史现在是空的。它不是作废包，而是服务器上当前实际安装的版本，`deploy/dist` 里的两个 preflight-guard 包因此保留。

### 未上传即被取代的包

`sms-relay-hub-deploy-2026.09.05-preflight-url-zip-fix.zip`（SHA256 `9d264fa326e9365b57f73409fb3299add37e9f4d3e5a4a87b1ebaf2f84084abb`）与同名 update 包（SHA256 `771bfa06972becfad1d3a0e07e3371770441a965a0db742bd60fafe4700021af`），各 32,931,115 字节。修好了 preflight 误报和反斜杠条目名，但包内 catalog 仍带 BOM（条目 5,909 字节、开头 `ef bb bf`），装上去发布历史依旧是空的，因此一次也没上传，直接被 `2026.09.05-catalog-bom-fix` 取代。两个 zip 与 `web/public/assets/releases/deploy/20260905/` 下的在线副本已移到 `.backups/deploy-dist/`，文件名带 `-SUPERSEDED`。

catalog 中 `platform=deploy`、`version_code=20260905` 的记录已用 `deploy/publish-update.ps1` 按同 `version_code` 替换为 `2026.09.05-catalog-bom-fix`（`sha256=46f3f977f627f47339a3de379331cc55b1a3a6bc75bccc7d33d15903d914a26c`、`size_bytes=32936457`）。移动完成后 `web/public/assets/releases/deploy/` 下 3 个文件与 catalog 的 3 条 `deploy` 记录仍然一一对应。

### deploy 类型的 catalog 记录只是工作区台账

排查更新链路时确认：`platform=deploy` 的 catalog 记录不会到达服务器，也没有任何界面消费它。

- `deploy/package.ps1` 打包时删掉 `web/public/assets/releases/deploy` 目录，并把 catalog 里 `platform=deploy` 的记录全部过滤掉（还有一条断言禁止它们出现在包内）。
- `deploy/apply-update.sh` 更新时先 `rm -rf "$TARGET/web"` 再用包内的树覆盖，服务器原有的 `web/public/assets/releases` 会被整体替换；旧的发布库只进 `$BACKUP_DIR/release-store` 备份，不会回填。
- `backend/app/api/updates.py` 虽然把 `deploy` 列在 `SUPPORTED_RELEASE_PLATFORMS` 里，但 `web/src/App.test.tsx` 断言管理台从不请求 `/releases/deploy/history`，即管理端没有容器在线更新界面。

结论：容器包只能手工上传更新，重新发布 deploy 记录是纯工作区台账操作，不改变已构建的 zip（包内 catalog 无论如何都不含 deploy 记录，字节一致），作用只是让 `20260905/` 目录与 catalog 记录保持一一对应。

### 已作废的包（不要再上传）

`sms-relay-hub-deploy-2026.09.05-compose-project-fix.zip`（32,945,888 字节，17:09 生成）内的 `deploy/apply-update.sh` 只有 5,266 字节，仍是带 `index .Labels` 缺陷的旧版本，正是 2026-09-05 服务器更新失败时上传的那一个。该文件已移动到 `.backups/deploy-dist/sms-relay-hub-deploy-2026.09.05-compose-project-fix-BROKEN-DETECTION.zip` 留证，`deploy/dist` 中不再保留。服务器上的 `/root/sms-relay-hub-deploy-2026.09.05-compose-project-fix.zip` 同样是旧版本，不要复用。

`sms-relay-hub-deploy-2026.09.05-stack-detect-fix.zip` 与 `sms-relay-hub-update-2026.09.05-stack-detect-fix.zip`（各 32,922,753 字节，18:40 生成）内的 `apply-update.sh` 已是修复版，但 `preflight.sh` 仍是旧版：没有单实例守卫，且在没有 `RELAY_DOMAIN` 的默认 IP 模式下即使全部检查通过也返回退出码 1。该对包已被 `2026.09.05-preflight-guard` 取代，未上传过服务器，现移动到 `.backups/deploy-dist/sms-relay-hub-{deploy,update}-2026.09.05-stack-detect-fix-SUPERSEDED.zip`；`web/public/assets/releases/deploy/20260905/` 下的同名在线文件也一并移出，该目录现与 catalog 的 3 条 `deploy` 记录一一对应。

## 服务器安全更新命令

上传 `sms-relay-hub-deploy-2026.09.05-catalog-bom-fix.zip` 到 `/root/` 后按顺序执行。

第一步：校验上传件哈希，确认与工作区 `## 当前部署包` 里记录的 SHA256 一致（包内副本那几行是 `PENDING_*` 占位值，不能用来自校验）。

```bash
sha256sum /root/sms-relay-hub-deploy-2026.09.05-catalog-bom-fix.zip
```

第二步：解压到独立目录。本包所有 zip 条目名都是正斜杠，`unzip` 应返回 `exit=0`。

```bash
rm -rf /root/relay-update-source-catalog-bom-fix
mkdir -p /root/relay-update-source-catalog-bom-fix

unzip -o /root/sms-relay-hub-deploy-2026.09.05-catalog-bom-fix.zip \
  -d /root/relay-update-source-catalog-bom-fix
echo "unzip exit=$?"
```

第三步：确认解压出来的确实是修复版脚本，并且包内 catalog 不带 BOM。应输出 `7993`、`8658`，`{{ index` 计数 0，一条 `.Label` 命中行（行内含 4 处 `{{.Label`），`base_authority=` 计数 2；最后一条必须打印 `7b`（`{`），出现 `efbbbf` 就说明拿错了包。

注意别把第三条 grep 缩短成 `grep -c 'index .Labels'`：脚本第 68 行有一条注释解释旧写法为什么错，那个短模式会命中这条注释返回 1，看起来像缺陷没修。判据用 `{{ index`（模板起始花括号），坏写法必然带它，注释里没有。

```bash
SRC=/root/relay-update-source-catalog-bom-fix
wc -c < $SRC/deploy/apply-update.sh
wc -c < $SRC/deploy/preflight.sh
grep -c '{{ index' $SRC/deploy/apply-update.sh || true
grep -n '{{.Label "com.docker.compose.project"}}' \
  $SRC/deploy/apply-update.sh
grep -c 'base_authority=' $SRC/deploy/preflight.sh
od -An -tx1 -N3 $SRC/web/public/assets/releases/release-catalog.json
```

第四步：只检测模式。必须看到 `detect-only: compose project deploy` 才继续；此步不备份、不替换文件、不改动容器。

```bash
RELAY_UPDATE_DETECT_ONLY=1 sh \
  /root/relay-update-source-catalog-bom-fix/deploy/apply-update.sh \
  /root/relay
```

第五步：正式更新（自带源码、`.env` 和 PostgreSQL dump 备份）。

```bash
sh /root/relay-update-source-catalog-bom-fix/deploy/apply-update.sh \
  /root/relay \
  /root/relay-update-history
```

第六步：验收容器与数据。

```bash
docker compose -p deploy \
  --env-file /root/relay/deploy/.env \
  -f /root/relay/deploy/compose.yml ps

curl -fsS http://127.0.0.1:39300/api/v1/health; echo

docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages;"
```

第七步：验收发布历史（本次更新的主要目标）。镜像内 catalog 应以 `7b` 开头而不是 `ef bb bf`，各平台接口应返回 JSON 而不再是 503 `release_catalog_invalid`。

接口形状注意：`backend/app/api/updates.py` 只为 android 提供 `latest`（`/android/latest`、`/android/history`），其余平台只有 `/{platform}/history`。所以 `/releases/root/latest`、`/releases/manager/latest` 是**不存在的路径**，返回 404 属于设计如此，不是 catalog 故障；别拿它们当验收项。三端各自实际调用的是：采集端 `AppUpdateClient` → `/android/latest`，管理端 `RelayAdminClient` → `/manager/history?channel=stable`（同理还有 android/root/deploy 四个 history），Root 模块在线检查 → `/root/history`。

```bash
docker exec deploy-app-1 od -An -tx1 -N3 \
  /app/app/static/assets/releases/release-catalog.json

curl -fsS http://127.0.0.1:39300/api/v1/releases/android/history | head -c 200; echo
curl -fsS "http://127.0.0.1:39300/api/v1/releases/android/latest?current_version_code=14" | head -c 200; echo
curl -fsS http://127.0.0.1:39300/api/v1/releases/root/history | head -c 200; echo
curl -fsS http://127.0.0.1:39300/api/v1/releases/manager/history | head -c 200; echo
```

第八步：确认自检脚本已换成修复版并且真的通过（更新后 `/root/relay/deploy/preflight.sh` 才是 8,658 字节的新版）。正常应打印 `Compose project in use: deploy` 和 `Preflight passed`，退出码 0；即使 `.env` 里有 `PUBLIC_BASE_URL` 也不再报 `must be an origin`。若打印多于一个项目名则说明空栈还没清理。

```bash
wc -c < /root/relay/deploy/preflight.sh
sh /root/relay/deploy/preflight.sh /root/relay/deploy/.env; echo "exit=$?"
```

注意：`rm -rf` 只允许作用于解压目录 `/root/relay-update-source-catalog-bom-fix`，不允许作用于 `/root/relay`、数据库卷或 Docker 卷。第六步的计数用于确认更新后仍连在有真实数据的 `deploy` 栈上，不是空库。
