# 生产更新事故 恢复手册（2026-09-12）

> 本文是 2026-09-12 生产更新失败后的**权威恢复步骤 + 版本结论**，基于本地迁移链、更新包内容、
> 会话记录与服务器回执交叉核对得出，不再依赖任何推测。

## 一、结论：服务器上跑的是哪一版

**服务器当前（回滚后）运行的旧代码 = 2026-09-10「保活加固」那一代，数据库迁移链最高到
`20260902_0008_device_enrollment_tokens`。**

- 失败的更新是 `sms-relay-hub-update-2026.09.12-remote-control-v3.zip`，
  它携带迁移 `20260910_0009_forwarding_rules` + `20260910_0010_device_commands`。
- **`2026.09.10-forwarding-rules`（第一个带 0009 的包）在记录中没有任何成功回执 → 从未落地。**
  也就是说「转发规则」这个功能此前**没有真正上线过**，服务器上是 08 世代。

### 证据链（可复核）

各更新包内 `backend/migrations/versions/` 的实际内容：

| 更新包 | 包内最高迁移 |
| --- | --- |
| `2026.09.04-manager-diagnostics` | 0008 |
| `2026.09.05-preflight-guard` / `catalog-bom-fix` | 0008 |
| `2026.09.09-shortcut-dedup-fix` | **0008** |
| `2026.09.10-keepalive-hardening` | **0008** |
| `2026.09.10-forwarding-rules` | **0009** ← 第一个带 0009 的包 |
| `2026.09.12-remote-control` / `-v2` / `-v3` | **0010** |

1. 会话记录里**只有一次** `update complete; backup retained at …relay-backup-20260910T025532Z`
   （09-10 10:55），即那一次是成功落地；之后 `forwarding-rules` 只给出了更新命令、没有回执。
2. 服务器备份目录只有三个：`relay-backup-20260910T025532Z`、`relay-backup-20260910T034234Z`、
   `relay-backup-20260912T063106Z`。**没有** 09-10 下午（转发规则包构建于 14:23）对应的备份目录 →
   该包从未执行过 `apply-update.sh`（脚本一进入就会先建备份目录）。
3. 反证：把 `alembic_version` 改成 `20260910_0009` 后应用**仍然崩**。旧代码若不认识 0009，
   说明它的 head ≤ 0008。三条证据互相独立、结论一致。

## 二、第 0 步：先确认（只读，一条命令）

```bash
ls -1 /root/relay/backend/migrations/versions/
```

`/root/relay` 是回滚后恢复的**旧源码**。**期望只看到 `0001 … 0008`，没有 `20260910_0009` / `0010`。**
若看到 0009/0010，说明结论不对，**先停下来**，把输出发我，不要执行下面的 SQL。

## 三、方案 A：外科手术恢复（推荐，2 条语句，不动业务数据）

当前数据库实际状态：`device_commands` 已被删除，`alembic_version = 20260910_0009`（上次误改），
但 `forwarding_rules` / `forwarding_logs` 两张表和 `messages.sender_body_digest` 这一列**还在**
（由失败的 v3 迁移创建）。把它们清干净、版本号退回 0008 即可。

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c "
DROP TABLE IF EXISTS device_commands;
DROP TABLE IF EXISTS forwarding_logs;
DROP TABLE IF EXISTS forwarding_rules;
ALTER TABLE messages DROP COLUMN IF EXISTS sender_body_digest;
UPDATE alembic_version SET version_num = '20260902_0008';
SELECT version_num FROM alembic_version;"
```

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml up -d
sleep 10
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml ps
curl -fsS http://127.0.0.1:39300/api/v1/health; echo
```

**为什么安全**

- 删的是**失败迁移新建的空表**，业务表 `devices` / `messages` / `admin_accounts` 一个字不动。
- `messages.sender_body_digest` 是 0009 新加的列，旧代码不读不写；**必须删掉**，
  否则以后重新部署 0009 时会因“列已存在”而迁移失败。
- `alembic_version` 只是结构版本标签，退到 0008 = 告诉旧代码「库还是你认识的样子」。
- 回滚时 `up -d` 会把 `caddy` 一起带起来（39300 是 Caddy 的端口，之前 `stop app caddy` 后没再起来）。

**数据核对（确认一条不少）**

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices' t, count(*) from devices union all select 'messages', count(*) from messages union all select 'admin_accounts', count(*) from admin_accounts;"
```

预期 `devices = 3`、`messages ≥ 117`、`admin_accounts = 1`。

## 四、方案 B：整库还原（保底，方案 A 不适用时用）

`relay-backup-20260912T063106Z` 是失败更新**在跑迁移之前**自动打的 `pg_dump`，
里面是「旧代码 + 旧库」的完整一致状态，还原它必然对得上。

```bash
docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml stop app

docker exec -i deploy-postgres-1 pg_restore -U sms_relay -d sms_relay --clean --if-exists \
  < /root/relay-update-history/relay-backup-20260912T063106Z/database.dump

docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "DROP TABLE IF EXISTS device_commands; DROP TABLE IF EXISTS forwarding_logs; DROP TABLE IF EXISTS forwarding_rules; ALTER TABLE messages DROP COLUMN IF EXISTS sender_body_digest; SELECT version_num FROM alembic_version;"

docker compose -p deploy --env-file /root/relay/deploy/.env -f /root/relay/deploy/compose.yml up -d
```

## 五、已完成的根治改动（本地仓库，测试 48 passed）

事故根因有两个，都已在本地修好：

1. **`deploy/apply-update.sh` 的回滚只回源码、不回数据库。**
   而容器 `entrypoint.sh` 是**先 `alembic upgrade head`、后健康检查**，所以失败的更新一定已经把迁移
   提交进库了。回滚后「库前移、代码后退」→ 旧代码报
   `Can't locate revision identified by '…'` 并 `Restarting(255)`。
   现已改为：回滚时 `stop app caddy` → 还原源码 → `pg_restore --clean --if-exists` 还原**同一份备份里的
   `database.dump`** → `up --build -d`，源码与库始终一起回退。

2. **`deploy/compose.yml` 的 app 健康检查没有 `start_period`。**
   冷启动要先跑迁移再拉起 uvicorn，6 次失败探测（约 60 秒）就可能把「启动慢但正常」判成 unhealthy，
   让 `compose up -d` 报 `dependency failed to start`，从而**误触发整包回滚**。
   现已加 `start_period: 60s`。

`backend/tests/test_deploy.py` 已补断言锁死这两点（`pg_restore` 必须出现在回滚里、`compose.yml` 必须有
`start_period`）。回归：`48 passed`。

## 六、恢复之后：重上 remote-control 的正确顺序

1. 先用**修正后的脚本**重新打包更新包（`deploy/package-update.ps1`），包内必须同时包含
   修复版 `apply-update.sh` 和带 `start_period` 的 `compose.yml`；**不要**复用 v3 包。
2. 照常上传 → `unzip` 到独立目录 → `RELAY_UPDATE_DETECT_ONLY=1` 确认识别到 `deploy` → 正式执行。
3. **这次一定要在失败时抓日志**：v3 上次到底为什么健康检查不过，目前仍无证据
   （我们看到的那条 `Can't locate revision '20260910_0010'` 是**回滚之后**旧代码报的，不是新代码的病因）。
   新脚本会自带数据库回滚，失败也不会再把服务置于「库/代码错位」的崩溃态；同时建议在更新窗口内
   `docker logs -f deploy-app-1` 抓一次首启日志，定位新代码首启是否真的报错。

## 七、红线（不要碰）

- 禁止 `docker compose down -v`；禁止删除 `deploy_postgres_data` 或任何数据库卷。
- 禁止用 `-p relay` 更新正式服务；正式栈固定 `deploy`。
- `rm -rf` 只允许作用于解压目录（如 `/root/relay-update-source-*`），不允许作用于 `/root/relay`。

---

## 八、第二次更新失败（2026-09-12 21:43 UTC 13:43，包 `2026.09.12-platform-fixes`）

### 8.1 发生了什么

按第六节顺序执行新包，结果与 v3 那次**同一个症状**：

```
compose up -d → ✔ postgres Healthy 0.6s
                ✘ app「dependency app failed to start」5.0s
dependency failed to start: container deploy-app-1 is unhealthy
update failed; restoring previous source, configuration and database
```

关键读数：compose **只等了 5 秒**就放弃 → 这不是 `start_period: 60s` + `retries: 6×10s` 的健康检查超时路径，
而是**新容器在首启极短时间内就没能进入 healthy**（要么进程退出，要么立刻被判 unhealthy）。
**新容器的首启日志没有被抓到**（`docker logs -f` 在 `^C` 时已经结束），这是这次最大的信息缺口。

### 8.2 迁移确实成功提交了（重要）

回滚里的 `pg_restore` 报错本身给出了证据：

```
constraint device_commands_device_id_fkey on table public.device_commands
depends on index public.devices_pkey
```

`device_commands` 存在 → **`alembic upgrade head` 已经把 `20260910_0009` 和 `20260910_0010` 都跑完并提交**。
所以失败点在迁移之后（uvicorn 启动阶段），不是迁移本身。

### 8.3 回滚的数据库还原是「部分成功」

`pg_restore --clean --if-exists` 掉了 5 个错，**全部集中在 `devices` 一张表**：

- `cannot drop constraint devices_pkey … device_commands_device_id_fkey depends on it`
- `cannot drop table public.devices … device_commands depends on it`
- `relation "devices" already exists` / `COPY failed … duplicate key` / `multiple primary keys`

含义：

| 对象 | 结果 |
| --- | --- |
| `devices` | **DROP 失败 → 保留了更新前的实时数据**（未回退） |
| 其余表（`messages`/`otps`/`api_clients`/`admin_accounts`/…） | **被 drop 后从 13:43:26 的 dump 重建 → 回退到备份时刻** |
| `forwarding_rules` / `forwarding_logs` / `device_commands` / `messages.sender_body_digest` | 不在 dump 里 → `--clean` 不会删 → **残留至今** |
| `alembic_version` | 被 dump 覆盖回 `20260902_0008` |

**风险窗口**：13:43:26（备份）到回滚完成之间新产生的 `messages` / `otps` 行**可能已丢失**；
`devices` 反而保留了实时数据。窗口只有约 2–3 分钟，需按 8.4 的 (d) 项核对缺口。

### 8.4 处置顺序

> 根因见 8.7（已确证）。所以现在的顺序是：**确认 → 清残留 → 打新包 → 更新**。
> 可直接粘贴的命令单：`docs/DIAG-COMMANDS-2026.09.12-incident2.md`。

**第 1 步｜2 秒确认根因（在服务器上，对着已解压的包跑）**

```bash
SRC=/root/relay-update-source-platform-fixes
grep -n 'requests' $SRC/backend/pyproject.toml                 # 期望：无输出（没声明）
grep -rn '^import requests' $SRC/backend/app/                  # 期望：services/forwarding.py:17
```

**第 2 步｜取证 + 对账（只读，摸清回滚到底影响了什么）**

(a) `\dt`、`\d messages`、`alembic_version`、各表行数。
(b) 把 13:43:26 的 dump 灌进一次性库 `relay_diag` 与生产库对账。
(c) 查消息缺口：`select created_at, sender from messages order by created_at desc limit 10;`
    看 13:43–13:47 之间是否有断档。

**第 3 步｜清残留**（否则 0009 会撞 `relation already exists`）

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c "
DROP TABLE IF EXISTS forwarding_logs;
DROP TABLE IF EXISTS forwarding_rules;
DROP TABLE IF EXISTS device_commands;
ALTER TABLE messages DROP COLUMN IF EXISTS sender_body_digest;
UPDATE alembic_version SET version_num = '20260902_0008';
SELECT version_num FROM alembic_version;"
```

顺序必须是 logs → rules（`forwarding_logs.rule_id` 有指向 `forwarding_rules.id` 的 FK）。

**第 4 步｜用修好依赖的新包重跑**（`*-r2`）。更新前先 `RELAY_UPDATE_DETECT_ONLY=1` 确认识别到 `deploy`，
失败时务必在另一窗口 `docker logs -f deploy-app-1`。

**第 5 步｜验收**：`alembic_version` 应为 `20260910_0010`，
`/api/v1/releases/{android,root,manager}/history` 应返回 JSON（**注意没有裸的 `/api/v1/releases`**）。

（原「隔离复现」仍可选：想眼见为实就跑一次 `relay_diag` 上的 `docker run`，
会直接打印 `ModuleNotFoundError: No module named 'requests'`；但对修包已无必要。）

### 8.5 已排除的猜测（省得重复查）

- 不是 `entrypoint.sh` / `compose.yml` 没生效：`apply-update.sh:200-205` 先 `cp` 新 `compose.yml` 再整体覆盖 `deploy/`，`start_period: 60s` 在位。
- 不是磁盘写入问题：`backend/app` 全量 grep 无 `mkdir`/`write_text`/`FileHandler`/`open(...)` 写操作。
- 不是配置缺失：`deploy/compose.yml` 给的 `ADMIN_TOKEN`/`ADMIN_USERNAME`/`ADMIN_PASSWORD`/`DATABASE_URL`/`DATA_ENCRYPTION_KEY` 覆盖了 `Settings` 的全部必填项。
- 不是 retetion 改动引入：`workers.retention_loop()` 自己 `except Exception` 吞异常，不会杀死进程；`_cleanup_forwarding_logs` 复用既有 session，没有嵌套 `store.lock`。

### 8.6 测试为什么没拦住（真实覆盖缺口）

`backend/tests/conftest.py:13` 把 `DATABASE_URL` 设成 **SQLite**，`store.configure(..., drop_existing=True)`
走 **`Base.metadata.create_all`**；唯一跑迁移的 `test_migrations.py` **也是 SQLite**。
→ **「迁移链产出的 PostgreSQL 模式」与「SQLAlchemy 模型」之间是否一致，本地 252 项测试完全不覆盖。**
本机也没有 Docker（`docker --version` 无输出），testcontainers 类断言不可能在本沙箱生效。
这一项应补：用 testcontainers Postgres 跑一次 `alembic upgrade head` 后做「模型 vs 库」列级比对。

### 8.7 根因（已确证）：`backend/pyproject.toml` 漏声明 `requests`

**不是迁移、不是配置、不是时序。是一行缺失的依赖声明。**

```
app/main.py:11  →  app/api/relay.py:25  →  app/services/forwarding.py:17
                →  import requests  →  ModuleNotFoundError: No module named 'requests'
```

- `backend/app/services/forwarding.py:17`、`:148/:155/:162/:169/:176`、`:180/:184` 大量使用 `requests`
  （企业微信/钉钉/飞书/Bark/webhook 五个推送渠道）。
- `backend/pyproject.toml` 的 `[project] dependencies` 里**没有 `requests`**。
- 容器只执行 `pip install --no-cache-dir .`，即**只装声明的运行时依赖** → 容器里根本没有 `requests`。
- 于是 `uvicorn app.main:app` 在**导入期**就抛 `ModuleNotFoundError`，进程几秒内退出；
  compose 对已退出的依赖立即放弃 → `dependency failed to start: container deploy-app-1 is unhealthy`（5.0s）。
- **v3 那次是同一个病**（v3 也带 `forwarding.py`），症状完全一致。

**为什么本地 252 项全绿**：`requests` 恰好是 dev 依赖链的传递依赖 ——
`pip show` 级别的证据：`requests` 被 `docker`（`testcontainers` 的依赖）和 `pytest` 拉进 venv
（`.tmp/dep_audit.txt` 的「requests 的来路」一节）。本地 venv 有、容器没有 → 测试天然测不到。

**本地复现证据**（`.tmp/prove_missing_requests.py`）：用一个 meta_path finder 屏蔽 `requests`，
按容器方式 `import app.main`，堆栈精确落在

```
File "backend/app/main.py", line 11, in <module>
File "backend/app/api/relay.py", line 25, in <module>
File "backend/app/services/forwarding.py", line 17, in <module>
    import requests
ModuleNotFoundError: No module named 'requests'
```

**修复**

1. `backend/pyproject.toml` 的 `[project] dependencies` 补 `"requests>=2.32,<3.0"`（含说明注释）。
2. 新增 `backend/tests/test_dependencies.py`：遍历 `backend/app/**/*.py` 的 AST，
   断言每个第三方顶层 import 都能在 pyproject 的 `dependencies` 里找到
   （仅 `argon2→argon2-cffi`、`paho→paho-mqtt` 两个改名别名，以及 `pydantic` 这个传递依赖入白名单）。
   **做过变异检查**：临时摘掉 `requests` 声明 → 该测试 2 项全红且报出行号；还原后字节一致。
3. 修复后**已重新打包**（变更落在镜像构建路径上，必须重打）：

   | 项 | 值 |
   | --- | --- |
   | 包名 | `deploy/dist/sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip` |
   | 字节数 | 33,069,313 |
   | sha256 | `0a87055ee08ffbad0824220501a7a85a4c756bacd4babf7a50e9e122fd86e354` |
   | 条目数 | 132（无反斜杠） |
   | 成品校验 | **34 项全绿**（原 30 项 + 新增「包内 pyproject 已声明 requests」「包内 `backend/app` 第三方 import 全部已声明」） |

   回归：后端全量 **254 passed**（原 252 + 新增 2 项依赖守卫，413.68s）。
   **第一版 `-platform-fixes.zip` 作废，不要复用。**

4. `docs/UPDATE-COMMANDS-2026.09.12-platform-fixes.md` 已同步到 r2 的指纹，
   并在自检步骤里加了 `grep -c '"requests' $SRC/backend/pyproject.toml`（期望 1）作为防拿错包的闸门。

**顺带记录一个同类隐患**：`backend/app/core/config.py:26` 的 `app_port` 默认值仍是 `8000`
（被禁止的历史遗留值）。运行时被 compose 的 `APP_PORT=39200` 覆盖所以没暴露，但默认值本身是错的。

### 8.8 结果：已成功上线（2026-09-12 22:19 +08）

用 r2 包重跑，一次通过。关键回执：

| 观察点 | 结果 |
| --- | --- |
| 包指纹 | `0a87055e…e354` 与预期一致（33,069,313 字节） |
| `unzip exit` | `0`（132 条目，无反斜杠） |
| 包内自检 | `"requests` = 1、`pg_restore` = 1、`start_period` = 2（与预期一致） |
| 残留清理 | 3 张表 DROP 全部成功；`messages.sender_body_digest` **不存在**（印证 `messages` 当时确实被回滚到了 13:43:26Z 的 dump）；`alembic_version` 已回到 `20260902_0008` |
| 只检测 | `detect-only: compose project deploy` |
| **首启** | **`deploy-app-1 Healthy 6.5s`**（上次是 5 秒被判死） |
| 更新回执 | `update complete; backup retained at /root/relay-update-history/relay-backup-20260912T141930Z` |
| **迁移版本** | **`20260910_0010`** ← 0009 转发规则 + 0010 远程指令**首次真正落地** |
| 发布接口 | `/api/v1/releases/android/history` 返回 200 JSON |

**本次事故的三个可复用教训**

1. **容器只装 `pyproject` 的运行时依赖**，本地 venv 的传递依赖会掩盖缺失声明 →
   「本地全绿 + 容器首启秒退 + health 永不通过」。守卫：`backend/tests/test_dependencies.py`
   + `.tmp/package_verify.py` 成品包级检查。
2. **5 秒失败 ≠ 健康检查超时**。`start_period: 60s` + `6×10s` 意味着判定 unhealthy 至少要 60 秒；
   几秒内就失败，说明是**进程退出**（导入/启动期异常），不是"启动慢"。
3. **回滚里的 `pg_restore --clean --if-exists` 会被新迁移建的 FK 挡住**：
   本次 `devices` 因 `device_commands.device_id` 的 FK 无法 DROP，导致
   **`devices` 保留实时数据、其余表回退到备份时刻**的部分还原。
   即"源码与库一起回退"这条设计在**同一次失败已经跑过迁移**时会打折。
   → 后续改进方向：回滚前先 `DROP ... CASCADE` 掉本包新引入的对象，或改用"还原到新库再切换"的方式。

**仍未做（交给用户）**

- 真机验证采集端/管理端：磁贴开关、电池优化页、**远程指令往返**（现在服务端已有 `device_commands`，
  采集端 `/api/v1/devices/<id>/commands/pending` 应从 404 变 200）。
- 核对 13:43–13:47（UTC）区间的 `messages` 是否有断档（回滚窗口内新产生的行可能丢失）。
- 本轮审查遗留项：H2 / H1 / H4 / I1 / J1~J3 / J5 / K2~K3 / L5 / M1，以及 `app_port` 默认值 `8000`。
