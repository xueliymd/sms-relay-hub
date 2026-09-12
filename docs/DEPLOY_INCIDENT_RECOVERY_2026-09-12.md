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
