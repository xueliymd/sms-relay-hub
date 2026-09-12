# 项目状态总账（已完成 / 未完成 / 待办）

最后更新：2026-09-12 23:20（+08）
当前分支 `feature/phase-1`，远端 `origin` = `https://github.com/xueliymd/sms-relay-hub`（私有），默认分支 `main`。

> 本文是**恢复入口**：换机器/清空本地后，照本文就能接着干。
> 相关文档：`docs/UPDATE-COMMANDS-2026.09.12-platform-fixes.md`（上线命令单）、
> `docs/DIAG-COMMANDS-2026.09.12-incident2.md`（事故诊断）、
> `docs/DEPLOY_INCIDENT_RECOVERY_2026-09-12.md`（事故恢复手册）、
> `docs/GITHUB_UPLOAD.md`（上传说明）。

---

## 一、当前生产状态（已上线）

| 项 | 值 |
| --- | --- |
| 服务器迁移版本 | **`20260910_0010_device_commands`**（0009 转发规则 + 0010 远程指令**首次真正落地**） |
| 现役更新包 | `sms-relay-hub-update-2026.09.12-platform-fixes-r2.zip` |
| 包指纹 | 33,069,313 字节 / sha256 `0a87055ee08ffeed…`（完整值见命令单） |
| 上线时间 | 2026-09-12 22:19（+08），app 首启 **6.5 秒** healthy |
| 服务器备份 | `/root/relay-update-history/relay-backup-20260912T141930Z` |
| 客户端最新 | 采集端 `1.0.22`(code 23) / 管理端 `1.0.10`(code 11) / Root `0.3.10`(code 22) |
| 对外入口 | `http://<服务器IP>:39300`（Caddy `:80`）；应用容器内部 `APP_PORT=39200` |

---

## 二、已完成

### 2.1 平台全量审查与修复（`docs/PLATFORM_FULL_REVIEW_2026-09-12.md`）

- **E1** Git 基线建仓（首个提交 402 文件），工作区首次 0 残留。
- **B1** 管理端"修改管理员账号"：**保留功能**，就地更正设计文档非目标并补"范围变更记录"。
- **C** 垃圾清理，释放 212MB（空壳 jadx、Gradle `build/`、嵌套复制、16 个孤儿 APK、根级 `dist/` 等）。
- **F1/F2 + N2** 接口层号码明文泄漏修复：新增 `mask_phone_number` / `scrub_metadata`，`_serialize_message` 单点收口。
- **J4** 转发日志保留策略真正接入 `run_retention()`（此前"保留 7 天"是空话，磁盘只增不减）。
- **N1** 教程下载链接改为从发布清单取最新（删除写死的旧版本号）。
- **L3** 95MB 历史容器部署包改为**构建期剔除**（源归档与 catalog 记录保持不动）。
- **L6** 清掉客户端/文档里最后一处过期端口 `8000`。
- **L7/L8** 删除孤儿 root 包；发现并修掉遗留的 `web/public/downloads/` 目录。
- **D/D1** 文档纠错 + 合并重复审计报告。
- 全量复验：后端 **254 passed**、网页 `tsc`+`vitest 60`+`build`、`ios-shortcut 9`、`clients/script_api 5`。

### 2.2 部署事故 1（回滚不带库还原 / 健康检查误判）

- `deploy/apply-update.sh` 回滚改为**源码 + `.env` + PostgreSQL dump 一起退**（补 `pg_restore`）。
- `deploy/compose.yml` 的 app 健康检查补 `start_period: 60s`（此前 6×10s 会把"启动慢"判成 unhealthy 并误触发整包回滚）。
- `backend/tests/test_deploy.py` 补断言锁死这两点。

### 2.3 部署事故 2（更新失败 + 回滚库还原部分失败）

- **根因**：`backend/pyproject.toml` 漏声明 `requests`，而 `app/services/forwarding.py` 导入期即 `import requests`
  → 容器只装声明的运行时依赖 → `uvicorn app.main:app` 导入即 `ModuleNotFoundError`，进程秒退。
- 修复：`pyproject.toml` 补 `requests>=2.32,<3.0`；新增 `backend/tests/test_dependencies.py` 守卫
  （AST 扫 `backend/app/**` 的第三方 import，必须在 pyproject 里声明；已做变异检查）。
- 用 r2 包重跑**一次通过**，`20260910_0010` 落地。
- 事故两条判据已写入命令单：**①几秒内失败 = 进程退出（≠ 健康检查超时）；②回滚的 `pg_restore --clean` 会被新迁移的 FK 挡住导致部分还原。**

### 2.4 远程指令（0009/0010 的功能面）

- 三端打通：`query_battery` / `query_recent_sms` / `trigger_upload`。
- **已在网页管理台实测**：三条指令全部 `completed`（电量 68%·未充电、最近 5 条短信、触发上报队列 0 条）。
- 采集端侧：每类指令**独立授权开关、默认全关**；未授权只回传 `command_not_authorized`。

### 2.5 参考实现审查（`docs/ANDROID_GITHUB_COMPARISON.md`）

- 核对 `pppscn/SmsForwarder`（`main` HEAD `ce931a0…`）与 `zgcwkjOpenProject/Magisk_SmsForwarder`。
- 已量化上游"发短信"实现（`SmsController.kt` 2246B + `PhoneUtils.sendSms` 62 行），并列出**3 个不要照抄的点**。

### 2.6 交付与归档

- GitHub 私有仓建仓 + 首次推送成功（394 文件 / 16 提交 / 远端 HEAD 与本地一致）。
- 部署文件归档到 `F:\jd\私人学习项目1\部署文件\2026-09-12-平台修复与上线\`（62 文件 / 562MB）。
- 新增运维文档：上线命令单、事故诊断命令单、事故恢复手册、上传说明、本总账。

---

## 三、未完成 / 待办

### 3.1 远程「发短信」`send_sms`（最大能力缺口）

参考实现已核对，**≈1 天量级**。建议顺序：

1. **前置**：指令原子 claim / 端侧去重（否则进程在"取指令→执行→回传"之间被杀会**重复发送**）。
2. `device_commands` 目前**只有类型、没有参数** → 需要新迁移（参数列），且号码必须按本项目红线**加密存**（`nonce`+`ciphertext`+`key_version`）。
3. 后端：`DEVICE_COMMAND_TYPES` 加 `send_sms`、下发接口带参数、审计。
4. 采集端：`SEND_SMS` 运行时权限引导、授权开关、执行分支（可借鉴上游 62 行，但**必须强制指定 subId**，不要抄它 `SmsInfoList[i]` 直接下标、也不要用 `getDefault()` 兜底）。
5. 网页 + 管理端 App + Root 模块：表单 + **二次确认**。
6. 双卡：按 `sim_slot` 查 `device_sim_profiles.subscription_id`，取不到明确回 `sim_slot_unavailable`，**不猜默认卡**。

### 3.2 平台审查遗留项（都不阻塞上线）

| 项 | 内容 |
| --- | --- |
| **H2** | 网页"消息记录"列表不显示验证码，与管理端 App 行为不一致（同一功能两端不同） |
| **H1** | `adminRequest` 两份实现（`adminApi.ts` + `App.tsx`），且有 `code = code;` 死语句 |
| **H4** | `.danger-button` 重复定义、两份颜色相反，后者覆盖前者 |
| **I1** | 死依赖 `@tanstack/react-query`、`react-router-dom`；`@types/qrcode` 位置错 |
| **J1~J3** | 后端死代码：`bind_engine`、`verify_mqtt_signature`、5 个 store 旧接口无调用点 |
| **J5** | `ruff` 关掉 `E501`/`UP017`（用放宽 lint 代替修代码） |
| **K2/K3** | 术语只改一半；已跟踪的规划文档被改写（不改变行为，但文档与实现脱钩） |
| **L5** | `clients/air724/README.md` 双份漂移（打包副本停在 08-23，缺 MQTT/ping 说明） |
| **M1/M2** | 测试夹具用旧命名路径；`App.test.tsx` 残留为已删除 deploy 平台的守卫断言 |

### 3.3 其他

- **`backend/app/core/config.py` 的 `app_port` 默认值仍是历史遗留的 `8000`**（运行时被 compose 的 `APP_PORT=39200` 覆盖，所以未暴露，但默认值本身是错的）。
- 上游审计的中/高优先级项（`audit_github_compare`）：远程指令结果**无加密档位且允许 http**、移除心跳闹钟后**深 Doze 定时唤醒被削弱**、缺**按厂商关闭验证码安全保护**的引导、管理端缺**转发规则/发送通道**可视化配置。

### 3.4 需要真机才能做的

- 采集端：通知栏磁贴开关、电池优化/后台不受限跳转是否真的跳对页面。
- 端侧授权开关打开后，真实收发一条短信核对（与 `send_sms` 一起做）。
- 核对 13:43–13:47（UTC）区间 `messages` 是否有断档（事故 2 回滚窗口内新到短信可能丢失）。

### 3.5 环境/流程债

- **发布签名私钥 `~\.sms-relay\signing\` 必须单独备份**——丢了就再也无法给老用户原包升级。（它**不能**上传到任何仓库。）
- **GitHub 连接器当前看不到本私有仓**（`list_commits` 404）：如需用它开 PR / 管 issue，要先把该仓加入连接器的可访问仓库列表。
- 后端全量测试是 **SQLite + `create_all`**，唯一碰迁移的 `test_migrations.py` 也是 SQLite
  → **「PostgreSQL 迁移链产出的模式 vs SQLAlchemy 模型」零覆盖**。已实测当前零漂移，但机制上仍是盲区，建议补 testcontainers Postgres 的列级比对。
- 后端全量测试约 7 分钟（含极慢的 `test_deploy`）；快速回归加 `--ignore=tests/test_deploy.py`。

---

## 四、本机开发环境（换机器时照抄）

```powershell
# 全套工具链（不在 PATH，也不在标准位置）
$tc = "$env:LOCALAPPDATA\codex-m1-toolchains"
#   jdk17\jdk-17.0.20+8   → javac 17.0.20
#   gradle\gradle-8.9     → Gradle 8.9
#   android-sdk           → platforms/android-34,35 / build-tools/33.0.1,34.0.0,35.0.0 / platform-tools / licenses（无 ndk）
$env:JAVA_HOME = "$tc\jdk17\jdk-17.0.20+8"
$env:ANDROID_HOME = "$tc\android-sdk"
$env:ANDROID_SDK_ROOT = "$tc\android-sdk"
$env:PATH = "$env:JAVA_HOME\bin;$tc\gradle\gradle-8.9\bin;$env:ANDROID_HOME\platform-tools;$env:PATH"
```

| 目标 | 命令 |
| --- | --- |
| 采集端 release APK | `android\build-release.ps1`（内部 `gradle -p android clean testDebugUnitTest lintDebug assembleRelease --no-daemon`） |
| 管理端 release APK | `manager-android\build-release.ps1` |
| Root 模块 | `clients\root-module\pack.ps1` |
| 后端测试 | `backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests -q`（在 `backend\` 下） |
| 网页 | `npm run typecheck` / `npm run test` / `npm run build`（在 `web\` 下） |
| 打包更新包 | `deploy\package-update.ps1 -Output deploy\dist\<名>.zip` |

**坑**：`gradle.bat` 会把警告写到 **stderr**，PowerShell 会报 `NativeCommandError` 看起来像失败——**判据看日志里的 `BUILD SUCCESSFUL`**。

---

## 五、红线（不要碰）

1. 正式栈固定 `deploy`，禁止 `-p relay`；**禁止 `docker compose down -v`**；禁止删数据库卷。
2. **不要为瘦身删发布物**：`backend/tests/test_deploy.py` 断言发布清单与归档的 size/sha256 逐一匹配。
3. **绝不提交** `deploy/.env` 与 `~\.sms-relay\signing\*.p12`（已 gitignore，新增文件前先 `git status`）。
4. 不要 `git push --force`（尤其别对 `main`）。
5. 更新前先确认库回到 `0008` 且清掉上次失败的残留表，否则 0009 撞 `relation already exists`。
