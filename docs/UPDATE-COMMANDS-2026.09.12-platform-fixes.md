# 更新命令 · sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip

包指纹（务必先核对）：
- 字节数：33069313
- sha256：0a87055ee08ffbad0824220501a7a85a4c756bacd4babf7a50e9e122fd86e354
- 条目数：132（无反斜杠条目）

> **为什么是 r2**：第一版 `-platform-fixes.zip` 上线失败，根因是 `backend/pyproject.toml`
> 漏声明 `requests`，而 `app/services/forwarding.py:17` 在导入期 `import requests` ——
> 容器只装声明的运行时依赖，于是 `uvicorn app.main:app` 导入即 `ModuleNotFoundError`，
> 进程秒退，compose 5 秒放弃并触发回滚（v3 那次同一个病）。
> r2 修掉了这个声明，并新增 `backend/tests/test_dependencies.py` 作为守卫。
> **不要复用第一版 `-platform-fixes.zip`。**

本包内容要点：
- 迁移到 `20260910_0010_device_commands`（**首次真正落地 0009 转发规则 + 0010 远程指令**）
- **`backend/pyproject.toml` 已声明 `requests>=2.32,<3.0`**（本次失败的修复点）
- `backend/tests/test_dependencies.py`：第三方 import 必须在 pyproject 里声明（已做变异检查）
- 修复版 `deploy/apply-update.sh`（9467 字节，回滚时带 `pg_restore` 源码+库一起退）
- `deploy/compose.yml` 带 `start_period: 60s`
- catalog 无 BOM（`7b 0d 0a`）、已剔除 `deploy` 平台、每平台保留 2 条
- 无 `web/public/downloads`、无 `assets/releases/deploy`、无 node_modules / .env / *.tsbuildinfo

---

## 红线（先读）

- 正式栈固定 `deploy`，**禁止 `-p relay`**
- **禁止 `docker compose down -v`**，禁止删除任何数据库卷
- `rm -rf` 只允许作用于 `/root/relay-update-source-*`，不允许作用于 `/root/relay`

---

## 第 0 步：前置确认（只读）

如果上一次失败更新还没做「方案 A」恢复，**先做方案 A 再更新**，否则 0009 会撞已存在的表。

```bash
ls -1 /root/relay/backend/migrations/versions/
```

期望只看到 `...0001` 到 `...0008`，没有 `20260910_0009` / `0010`。
若看到 0009/0010，先执行 `docs/DEPLOY_INCIDENT_RECOVERY_2026-09-12.md` 第三节「方案 A」。

---

## 第 1 步：上传（在本地 Windows 执行）

```powershell
scp "C:\Users\12071\Documents\Codex\2026-08-14\new-chat\work\sms-relay-hub-phase1\deploy\dist\sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip" root@<服务器IP>:/root/
```

---

## 第 2 步：校验指纹（在服务器执行）

```bash
sha256sum /root/sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip
```

期望输出开头：`0a87055ee08ffbad0824220501a7a85a4c756bacd4babf7a50e9e122fd86e354`
不一致就**停下**，重新上传。

---

## 第 3 步：解压到独立目录

```bash
rm -rf /root/relay-update-source-platform-fixes
mkdir -p /root/relay-update-source-platform-fixes

unzip -o /root/sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip \
  -d /root/relay-update-source-platform-fixes
echo "unzip exit=$?"
```

期望 `unzip exit=0`（本包所有条目名都是正斜杠）。

---

## 第 4 步：解压物自检

```bash
SRC=/root/relay-update-source-platform-fixes

wc -c < $SRC/deploy/apply-update.sh                  # 期望 9467
grep -c 'pg_restore' $SRC/deploy/apply-update.sh     # 期望 >= 1（回滚带库还原）
grep -c 'start_period' $SRC/deploy/compose.yml       # 期望 2（1 处实现 + 1 处注释）
od -An -tx1 -N3 $SRC/web/public/assets/releases/release-catalog.json   # 期望 7b 0d 0a

# ★ 本次事故的修复点：打包进去的 pyproject 必须已声明 requests（无输出 = 拿错包，停下）
grep -c '"requests' $SRC/backend/pyproject.toml      # 期望 1
```

`od` 若出现 `ef bb bf` = BOM，说明拿错了包，停下。

---

## 第 5 步：只检测模式（不备份、不改文件、不动容器）

```bash
RELAY_UPDATE_DETECT_ONLY=1 sh $SRC/deploy/apply-update.sh /root/relay
```

**必须看到 `detect-only: compose project deploy`** 才继续。
若报「发现多个项目」或识别到 `relay`，停下，检查是否有空栈残留。

---

## 第 6 步：正式更新

先开一个窗口盯日志（上次 v3 就是缺这段日志才定位不了病因）：

```bash
docker logs -f deploy-app-1
```

另开一个窗口执行：

```bash
RELAY_COMPOSE_PROJECT=deploy sh \
  /root/relay-update-source-r2/deploy/apply-update.sh \
  /root/relay \
  /root/relay-update-history
```

脚本会自带备份（源码 + `.env` + PostgreSQL dump），成功时打印
`update complete; backup retained at .../relay-backup-<时间戳>`。

---

## 第 7 步：验收

```bash
docker compose -p deploy \
  --env-file /root/relay/deploy/.env \
  -f /root/relay/deploy/compose.yml ps

curl -fsS http://127.0.0.1:39300/api/v1/health; echo

# 迁移版本：期望 20260910_0010
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay \
  -c "select version_num from alembic_version;"

# 数据一条不少：期望 admin_accounts=1；devices/messages 与更新前一致（2026-09-12 那次是 devices=4、messages=200 起）
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'devices',count(*) from devices union all select 'messages',count(*) from messages union all select 'otps',count(*) from otps union all select 'api_clients',count(*) from api_clients union all select 'admin_accounts',count(*) from admin_accounts;"

# 0009/0010 落地的证据：两张新表必须在
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select 'forwarding_rules',count(*) from forwarding_rules union all select 'forwarding_logs',count(*) from forwarding_logs union all select 'device_commands',count(*) from device_commands;"

# 远程指令：这张表就是证据（0009/0010 落地）
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select command_type, status, created_at, sent_at, completed_at from device_commands order by created_at;"

# 采集端轮询是否真的通了 —— 必须看应用访问日志，不要手搓 curl：
# /devices/{id}/commands/pending 是 HMAC 鉴权端点（X-Device-Id/X-Timestamp/X-Nonce/X-Signature），
# 裸 curl 只会拿到 401/403；而把真实 UUID 换成 <设备id> 这种占位符会拿到 422（路由匹配、参数不合法的表现）。
# 更新前这里恒为 404，现在应为 200。
docker logs --since 30m deploy-app-1 2>&1 | grep 'commands/pending' | tail -20

# 发布接口应返回 JSON 而不是 503 release_catalog_invalid
# ⚠️ 必须带平台路径：路由表里没有裸的 /api/v1/releases，请求它一定是 404（这是检查命令写错，不是故障）
curl -fsS "http://127.0.0.1:39300/api/v1/releases/android/history" | head -c 200; echo
curl -fsS "http://127.0.0.1:39300/api/v1/releases/root/history"    | head -c 200; echo
curl -fsS "http://127.0.0.1:39300/api/v1/releases/manager/history" | head -c 200; echo

# 消息断档检查（2026-09-12 回滚窗口 13:43–13:47 UTC = 21:43–21:47 +08）
# 注意 messages 表没有 sender 列（发件人/正文都是密文 + sender_summary 摘要），
# 用 sender_summary；直接 select sender 会报 column "sender" does not exist。
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "set time zone 'UTC';
   select to_char(created_at - (extract(minute from created_at)::int % 5) * interval '1 minute',
                  'HH24:MI') as bucket_utc,
          count(*)
     from messages
    where created_at >= '2026-09-12 13:00+00' and created_at < '2026-09-12 15:00+00'
    group by 1 order by 1;"

# 想看明细就把窗口收窄（sender_summary 是给列表页用的摘要，不是明文号码）
docker exec deploy-postgres-1 psql -U sms_relay -d sms_relay -c \
  "select created_at, sender_summary from messages
    where created_at >= '2026-09-12 13:00+00' and created_at < '2026-09-12 15:00+00'
    order by created_at desc limit 40;"
```

**成功时应当看到**：`deploy-app-1 ... (healthy)`、回执里 `update complete; backup retained at ...`、
`alembic_version = 20260910_0010`、`/api/v1/health` 返回 `{"status":"ok"}`。
2026-09-12 22:19 那次实测首启 **6.5 秒**转 healthy。

---

## 失败怎么办

1. 先看第 6 步那个 `docker logs -f deploy-app-1` 窗口的首启报错，**把日志发我**。
2. **判断是"启动慢"还是"进程退出"**：`compose.yml` 的 `start_period: 60s` + `6×10s` 意味着
   判定 unhealthy 至少要 60 秒。**几秒内就报 `dependency failed to start` = 进程退出了**
   （导入期异常 / 配置校验失败），不是启动慢 —— 去查导入和依赖，不要加超时。
3. **回滚的库还原可能只成功一半**（2026-09-12 两次实测）：`pg_restore --clean --if-exists`
   会被**本包新迁移建立的 FK** 挡住（例：`device_commands.device_id → devices.id` 让 `devices` 无法 DROP），
   结果是 `devices` 保留实时数据、其余表回退到备份时刻。回执里出现
   `errors ignored on restore` 或 `WARNING: database restore failed` 就属于这种情况，
   必须人工核对行数，并**清掉残留的新表后再重跑**。
4. 需要保底整库还原时，走 `docs/DEPLOY_INCIDENT_RECOVERY_2026-09-12.md` 第四节「方案 B」，
   用备份 `/root/relay-update-history/relay-backup-<时间戳>/database.dump`。

---

## 上线记录

| 时间（+08） | 包 | 结果 |
| --- | --- | --- |
| 2026-09-12 21:43 | `-platform-fixes.zip`（v1） | ❌ 失败：缺 `requests` 声明，app 5 秒被判死 → 回滚（库部分还原） |
| 2026-09-12 22:19 | **`-platform-fixes-r2.zip`** | ✅ 成功：app 6.5s healthy，`20260910_0010`，备份 `relay-backup-20260912T141930Z` |
