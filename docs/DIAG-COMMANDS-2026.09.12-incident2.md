# 事故 2 诊断命令 · 2026-09-12 21:43（包 `platform-fixes` 更新失败后）

**根因已确证**：`backend/pyproject.toml` 漏声明 `requests`，而 `app/services/forwarding.py:17` 在导入期
`import requests`。容器只装声明的运行时依赖 → `uvicorn app.main:app` 导入即
`ModuleNotFoundError` → 进程秒退 → compose 5 秒放弃 → 回滚。
（本地 252 项测试全绿是因为 `requests` 只是 `testcontainers→docker` 的传递依赖，本地 venv 恰好有。）

**顺序**：确认根因 → 取证对账 → 清残留 → 打新包（`*-r2`）→ 更新 → 验收。
**在清残留之前不要再跑 `apply-update.sh`。**

---

## 阶段 0：2 秒确认根因（对着已解压的包跑）

```bash
SRC=/root/relay-update-source-platform-fixes

grep -n 'requests' $SRC/backend/pyproject.toml   # 期望：无输出（依赖列表里没有 requests）
grep -rn '^import requests' $SRC/backend/app/    # 期望：backend/app/services/forwarding.py:17
```

两条同时成立 = 根因确认。想眼见为实也可以直接看容器里的报错：

```bash
docker build -t relay-diag -f $SRC/deploy/Dockerfile $SRC
docker run --rm --entrypoint python relay-diag -c "import app.main"
# 期望：ModuleNotFoundError: No module named 'requests'
```

---

## 阶段 A：只读取证（不改任何东西）

```bash
# A1 现在库里到底有哪些表 —— 期望能看到 0009/0010 的残留
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c '\dt'

# A2 messages 是否还带着 0009 加的列
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c '\d messages'

# A3 版本号 + 全表计数
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c "select version_num from alembic_version;"
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'otps',count(*) from otps union all select 'api_clients',count(*) from api_clients union all select 'admin_accounts',count(*) from admin_accounts;"

# A4 消息有没有断档（回滚把 messages 退回到了 13:43:26Z 那一版）
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select created_at, sender from messages order by created_at desc limit 10;"

# A5 备份是否都在
ls -la /root/relay-update-history/relay-backup-20260912T134326Z/
```

A1 若看到 `forwarding_rules` / `forwarding_logs` / `device_commands` 三张表 + A2 若看到 `sender_body_digest`，
就证实「0009/0010 已提交、`--clean` 不删 dump 外的对象」这个判断。

---

## 阶段 B：隔离复现（**只碰一次性库 `relay_diag`，不碰生产数据**）

目的：把上次丢掉的**新容器首启报错**抓回来。这是定位根因的唯一可靠手段。

```bash
# B1 用更新前的 dump 造一个一次性诊断库（这份 dump 就是 0008 状态）
docker exec deploy-postgres-1 psql -U sms_relay -d postgres -c "DROP DATABASE IF EXISTS relay_diag;"
docker exec deploy-postgres-1 psql -U sms_relay -d postgres -c "CREATE DATABASE relay_diag OWNER sms_relay;"
docker exec -i deploy-postgres-1 pg_restore -U sms_relay -d relay_diag --no-owner \
  < /root/relay-update-history/relay-backup-20260912T134326Z/database.dump
echo "restore exit=$?"

# B2 与生产库对账：relay_diag 就是备份时刻的样子，差值 = 回滚窗口内可能丢的行
docker exec deploy-postgres-1 psql -U sms_relay -d relay_diag -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'otps',count(*) from otps union all select 'api_clients',count(*) from api_clients union all select 'admin_accounts',count(*) from admin_accounts;"

# B3 构建新镜像（复用构建缓存，约 40 秒；不改任何运行中的容器）
docker build -t relay-diag -f /root/relay-update-source-platform-fixes/deploy/Dockerfile \
  /root/relay-update-source-platform-fixes

# B4 前台跑（关键：不加 --restart，进程退出就直接把堆栈打印出来）
ENVF=/root/relay/deploy/.env
getv() { sed -n "s/^[[:space:]]*$1=//p" "$ENVF" | tail -n 1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/'; }
NET=$(docker inspect deploy-postgres-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
echo "network=$NET"

docker run --rm --init --read-only --tmpfs /tmp --network "$NET" \
  -e ADMIN_TOKEN="$(getv ADMIN_TOKEN)" \
  -e ADMIN_USERNAME="$(getv ADMIN_USERNAME)" \
  -e ADMIN_PASSWORD="$(getv ADMIN_PASSWORD)" \
  -e DATA_ENCRYPTION_KEY="$(getv DATA_ENCRYPTION_KEY)" \
  -e APP_PORT=39200 \
  -e DATABASE_URL="$(getv DATABASE_URL | sed 's#/sms_relay#/relay_diag#')" \
  relay-diag 2>&1 | tee /root/relay-diag-startup.log
```

**B4 的输出就是答案。** 会出现三种情况之一：

| 看到什么 | 说明 |
| --- | --- |
| `ModuleNotFoundError` / `ImportError` / pydantic `ValidationError` 堆栈 | 容器里才复现的导入或配置问题（本地测试用源码树+SQLite，测不到） |
| `alembic` 报错 | 迁移在 0008 库上的问题 |
| 正常打印 `Uvicorn running on http://0.0.0.0:39200` | 说明新代码在隔离库上能起来，问题出在更新时的具体时序/状态，需换角度 |

把 `/root/relay-diag-startup.log` 整段发我。

---

## 阶段 C：清理残留（**等根因定位后再做**，否则 0009 会撞 `relation already exists`）

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c "
DROP TABLE IF EXISTS forwarding_logs;
DROP TABLE IF EXISTS forwarding_rules;
DROP TABLE IF EXISTS device_commands;
ALTER TABLE messages DROP COLUMN IF EXISTS sender_body_digest;
UPDATE alembic_version SET version_num = '20260902_0008';
SELECT version_num FROM alembic_version;"

# 顺序：先 logs 再 rules（logs 有指向 rules 的 FK）
# 若报依赖错误，再考虑给该条加 CASCADE
```

诊断完成后删掉临时库（不影响生产）：

```bash
docker exec deploy-postgres-1 psql -U sms_relay -d postgres -c "DROP DATABASE IF EXISTS relay_diag;"
```

---

## 阶段 D：用 r2 包重跑更新

**第一版 `sms-relay-hub-update-2026.09.12-platform-fixes.zip` 作废，不要复用。**

r2 包指纹：

| 项 | 值 |
| --- | --- |
| 文件 | `sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip` |
| 字节数 | `33069313` |
| sha256 | `0a87055ee08ffbad0824220501a7a85a4c756bacd4babf7a50e9e122fd86e354` |
| 条目数 | 132 |

步骤就是 `docs/UPDATE-COMMANDS-2026.09.12-platform-fixes.md`（已同步到 r2 指纹），
其中第 4 步的自检里新增了一条防拿错包的闸门：

```bash
grep -c '"requests' $SRC/backend/pyproject.toml   # 期望 1，无输出=还是旧包，停下
```

---

## 红线（照旧）

- 正式栈固定 `deploy`，**禁止 `-p relay`**
- **禁止 `docker compose down -v`**，禁止删任何数据库卷
- `rm -rf` 只允许作用于 `/root/relay-update-source-*`
- **阶段 C 之前不要再跑 `apply-update.sh`**
- `docker build`/`docker run` 都是只读式的（B4 用 `--rm`），不会影响 `deploy-app-1`
