# 远程控制：参考项目对照与本项目建议（2026-09-12）

参考项目：`pppscn/SmsForwarder`（`docs/ANDROID_GITHUB_COMPARISON.md` 已登记的 Android 参考项目）。
核对来源：GitHub README + DeepWiki「Remote Control System」「Server Configuration」「API Endpoints」三页。

---

## 一、参考项目的"远程控制"到底是什么形态

**核心：设备端内置 HTTP Server，控制方直连设备。** 不走任何"后台/服务器"中转。

### 服务端（被控手机）

| 项 | 实现 |
|---|---|
| 服务类 | `HttpServerService`（基于 AndServer 框架，跑在手机上） |
| 默认端口 | `HTTP_SERVER_PORT`，通常 5000 |
| 开机自启 | `enableServerAutorun` |
| 纯客户端模式 | `enablePureClientMode`（开启则本机不监听，省资源） |
| 安全档位 | 4 档：0 无 / 1 Sign（HMAC-SHA256 + 时间戳，时间容差默认 600 秒）/ 2 RSA / 3 SM4 |
| 每类接口独立开关 | 见下表 |

### 服务端暴露的端点

| 端点 | 功能 | 开关 |
|---|---|---|
| `/config/query` | 查服务状态 / SIM 信息 | 始终启用 |
| `/clone/push`、`/clone/pull` | 备份 / 恢复全部设置（一键换新机） | `enableApiClone` |
| `/sms/send` | 远程发短信 | `enableApiSmsSend` |
| `/sms/query` | 查设备短信收件箱 | `enableApiSmsQuery` |
| `/wol/send` | 发送 Wake-on-LAN 魔术包 | `enableApiWol` |
| `/location/query` | 查 GPS / 网络定位 | `enableApiLocation` |
| （开关列表另有） | 查通话记录、查通讯录、新增联系人 | `enableApiCallQuery`、`enableApiContactQuery`、`enableApiContactAdd`、`enableApiBatteryQuery` |

### 控制端（谁在发指令）

是**另一台装了 App 的手机**（App 内 `ClientFragment`）：填被控设备地址 + 安全档位即可远程发短信、查短信、查电量、WOL、定位、克隆配置。
**参考项目里没有"网页后台管理端"这个概念**，控制端就是设备。

### 前置条件（重要）

被控设备必须有**可被控制方访问到的地址**（公网 IP，或同一内网，或走 FRPC 内网穿透——参考项目的克隆配置里就带 `frpc configs`）。
备用机通常在家里的 NAT 后面，**没有公网地址时直连是连不上的**。

---

## 二、与我们当前实现的对比

| 维度 | SmsForwarder（参考） | 我们的 v1 | 差异评价 |
|---|---|---|---|
| 通道方向 | **设备监听 HTTP Server，控制方直连设备** | **平台下行队列 + 设备轮询领取** | 方向相反 |
| 控制端 | 另一台手机 App | 仅网页管理台 | **我们少了手机端入口** |
| 指令集 | 8~10 类（含发短信、查通话、查通讯录、加联系人、WOL、定位、克隆） | 3 类（查电量、查最近短信、触发上传） | 我们少 5~7 类 |
| 开关粒度 | 每类端点独立开关 | 每类指令独立授权（默认关） | 粒度一致，覆盖面小 |
| 安全 | 4 档，含 RSA / SM4 **载荷加密** | HMAC-SHA256 + 时间戳 + nonce（等同其 Sign 档） | 基本对齐，缺载荷加密 |
| 设备网络要求 | 需公网可达或内网/FRPC | 只需能出网（NAT 后也行） | **我们的中转更适合备用机** |
| 自动化 | 有自动任务 / 快捷指令 | 无 | 缺 |

---

## 三、结论与建议

### 为什么"远程控制不是只后台管理有需求才会发送"

参考项目的控制端是**任意控制设备**，不依赖"后台"。我们的 v1 把指令下发入口**只放在网页管理台**，所以看起来是"只有后台管理端需要才会发送"。**差距在于：控制端入口太少 + 指令集太窄**，而不是通道方向错。

### 建议（不建议照搬设备内置 HTTP Server）

**不建议**照搬"设备内置 HTTP Server、控制方直连"：
- 备用机多在 NAT 后，没有公网地址直连不通；要直连就得引入 FRPC 内网穿透，部署复杂度大幅上升
- 设备暴露 HTTP 端口会显著扩大攻击面（我们的中转方案里设备只出网、不开端口）
- 我们已经有一套成熟的设备鉴权（HMAC + 防重放）和平台能力，复用成本低

**建议做这三件事：**

1. **补控制端入口**：管理端 App 增加"远程控制"页（调用与网页同一套 API），做到"不是只有后台管理端能发"
2. **补指令集到参考项目量级**（每类独立授权开关，默认关，下发二次确认）：
   - `send_sms` 发短信（SEND_SMS）
   - `query_call_log` 查通话记录（READ_CALL_LOG）
   - `query_contacts` 查通讯录（READ_CONTACTS）
   - `query_location` 查定位（定位权限）
   - 已有：`query_battery`、`query_recent_sms`（≈参考的 /sms/query）、`trigger_upload`
   - 可选：`query_config`（设备状态/SIM，≈参考的 /config/query）
3. **可选增强**：定时 / 条件自动下发（如"设备失联自动查电量"），对应参考项目的"自动任务"

**不建议做**：`add_contact`（加联系人）、`/clone/*`（配置克隆）——与"平台统一管理"职责冲突，且克隆会把设备配置复制到别处，审计困难。
