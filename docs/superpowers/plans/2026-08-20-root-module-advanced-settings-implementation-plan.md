# Root 模块高级设置实施计划

> **给执行代理：** 必须按任务逐项执行，优先使用测试驱动开发；每项都要运行对应验证。不要把历史短信同步、SQL 执行或短信数据库写入暴露给用户。

**目标：** 为 Root 短信模块增加中文高级设置、数据库自动检测与安全手动覆盖，并修复小米 `sub_id=-1` 延迟、读取状态陈旧和锁竞争误杀问题。

**架构：** Go runtime 负责配置校验、SQLite 候选探测、只读数据库读取和诊断状态；Go 本地 Web 控制台负责中文高级设置、验证/重新检测 API 和脱敏诊断；`service.sh` 只负责 worker 生命周期、watcher/reader 所有权和重载唤醒。数据库路径默认自动识别，手动路径只作为经过安全校验的覆盖项。

**技术栈：** Go、`modernc.org/sqlite`、POSIX `sh`、Root 模块 ZIP、PowerShell 打包脚本、ADB/APatch 真机验证。

---

## 文件边界

- 修改 `clients/root-module/native/runtime/main.go`：配置字段、默认值、配置校验、读取选项传递。
- 修改 `clients/root-module/native/runtime/sqlite_fallback.go`：候选库验证、CE/DE 优先级、手动路径安全校验、诊断信息、MIUI 负数哨兵。
- 修改 `clients/root-module/native/runtime/web_console.go`：中文高级设置页面、数据库验证/重新检测 API、诊断展示。
- 修改 `clients/root-module/native/runtime/*_test.go`：配置、SQLite、控制台回归测试。
- 修改 `clients/root-module/service.sh`：读取恢复状态、锁竞争清理所有权、watcher 重启/回退。
- 修改 `clients/root-module/config.example.json`、`README.md`：中文配置说明和排障说明。
- 修改 `clients/root-module/tests/behavior.sh`：Shell 生命周期、锁竞争和敏感信息回归。
- 修改 `clients/root-module/native/build.ps1`、`pack.ps1`、`module.prop`：双 ABI 构建、版本和发布包。

### 任务 1：补齐配置模型和中文文档

**文件：** `main.go`、`web_console.go`、`config.example.json`、`README.md`。

- [ ] 为 `rootConfig` 与 Web 请求/响应结构增加 `SMSDatabasePath string`，JSON 名为 `sms_database_path`，旧配置缺失时按空字符串处理。
- [ ] 在 `validateConfig` 中校验空值或调用统一的手动路径格式校验；保留已有时间范围校验。
- [ ] 默认配置和示例配置加入空数据库路径；README 明确“空值自动检测、只读、不上传历史短信”。
- [ ] 先写旧配置解码、相对路径和错误文件名测试，再写最小实现。
- [ ] 运行 `go test ./... -run 'Test.*Config' -count=1`，确认通过。

### 任务 2：实现数据库候选验证和路径覆盖

**文件：** `sqlite_fallback.go`、`sqlite_fallback_test.go`。

- [ ] 新增候选构建：手动值非空时只返回经过校验的手动候选；空值返回 CE、user0 别名和 DE 候选。
- [ ] 新增数据库验证：绝对路径、`mmssms.db` 文件名、允许根目录、符号链接不越界、普通文件、只读 SQLite、`sms` 表、必要字段和 `MAX(_id)`。
- [ ] 自动候选优先使用有数据 CE 库，并对 `os.SameFile` 别名去重。
- [ ] 读取时保存实际路径、来源、字段、最大 ID 和脱敏错误代码，不写短信库。
- [ ] 保留 MIUI `sub_id=-1` 作为 nil 的修复，并使用有效 `sim_id` 映射。
- [ ] 先写有效候选、空 DE/有数据 CE、缺表、缺字段、路径越界、只读、手动无效不回退和 MIUI 测试，再实现。
- [ ] 运行 `go test -tags sqlite_fallback ./... -count=1`。

### 任务 3：增加数据库验证和重新检测 API

**文件：** `web_console.go`、`web_console_test.go`。

- [ ] 增加 `POST /api/database/validate`，只返回验证状态、路径、来源、错误代码、最大 ID 和字段。
- [ ] 增加 `POST /api/database/redetect`，自动检测成功后才清空手动覆盖，失败时保留原配置。
- [ ] 在 `GET /api/status` 中合并读取模式、路径来源、监听状态、游标、最近扫描和脱敏错误。
- [ ] API 复用现有 token、Host、Origin、JSON 大小和未知字段限制。
- [ ] 先写未授权、非法路径、有效验证、重新检测失败不覆盖、诊断不含正文/密钥测试，再实现。
- [ ] 运行 `go test ./... -run 'TestWebConsole|Test.*Database' -count=1`。

### 任务 4：实现中文高级设置页面

**文件：** `web_console.go`、`web_console_test.go`。

- [ ] 使用 `<details>` 增加“高级设置”，分为“数据库”“读取与唤醒”“网络重试”“运行诊断”。
- [ ] 增加数据库路径、验证、重新自动检测控件；保存后显示路径来源。
- [ ] 为监听空闲、补偿扫描、网络退避和 Android 用户 ID 写中文用途说明。
- [ ] 诊断只读，不显示短信正文、手机号、验证码、密钥和完整队列。
- [ ] 页面加载兼容旧 API 缺失字段并使用默认值。
- [ ] 运行 `go test ./... -run TestWebConsole -count=1`。

### 任务 5：修复 service 生命周期和 watcher 恢复

**文件：** `service.sh`、`tests/behavior.sh`。

- [ ] `LOCK_HELD=0` 的退出清理不得读取或删除共享 reader/watcher PID 文件。
- [ ] `run_reader` 成功后清除陈旧 `reader_failed`；读取失败使用读取间隔，不使用网络指数退避。
- [ ] watcher 退出或 CE 目录解锁后允许重启；不可用时保持补偿扫描。
- [ ] 增加锁竞争、读取恢复、watcher 缺失和配置热更新回归测试。
- [ ] 运行 Shell 语法检查和 `behavior.sh` 全套。

### 任务 6：打包、静态审查和真机验收

**文件：** `module.prop`、`native/build.ps1`、`pack.ps1`、`abi_contract.ps1`、`README.md`。

- [ ] 递增模块版本，ZIP 只包含 ARM64 和 ARMv7 runtime。
- [ ] 运行 Go、Shell、reader/watcher、ABI 和向量验证。
- [ ] 构建并检查 ZIP，删除临时 probe、数据库副本和短信样本。
- [ ] 通过 ADB/APatch 安装到小米 11，确认 CE SQLite 游标前进，`sub_id=-1` 不再进入慢 Provider 路径。
- [ ] 下发一条新短信进行端到端验收，只记录各阶段时间，不记录正文或验证码。

## 验收标准

- 控制台可用中文修改高级设置，旧配置仍能启动。
- 自动检测失败时有明确提示，可安全验证并保存手动路径。
- 无效路径不覆盖有效配置，模块不写短信数据库。
- 小米 `sub_id=-1` 不再造成 100 秒级 Provider 回退。
- 第二个 service 进程不能杀 watcher，读取恢复后状态不再长期显示失败。
- ARM64/ARMv7 发布包和全部测试通过，ZIP 不包含 x86/x86_64。
