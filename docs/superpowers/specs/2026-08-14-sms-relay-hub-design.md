# SMS Relay Hub 设计规格

- 日期：2026-08-14
- 状态：待最终确认
- 架构：方案 C，统一后台协议 + 多种手机采集端
- 使用范围：单管理员、多台本人持有或明确授权的设备

## 1. 目标

构建一套可通过 Docker Compose 自托管的短信中转系统，统一接收 Android 和 iPhone 上报的短信，识别验证码，并向管理后台、受控 API 和通知通道提供查询或一次性领取能力。

第一阶段必须能够直接运行并完成真实链路：

1. 非 Root Android 手机收到短信后自动上报。
2. iPhone 通过“信息”个人自动化和快捷指令上报。
3. 后台展示设备、短信、验证码、领取状态和统计。
4. 脚本通过 API 原子领取仍在有效期内且尚未领取的验证码。
5. Docker Compose 一条命令启动 API、Web 管理界面和 PostgreSQL。

第二阶段在不改变后台协议的前提下增加 Magisk/KernelSU/APatch 采集端和越狱 iOS Ve 插件适配。

## 2. 非目标

- 不提供公开注册、多租户、号码出租或验证码交易功能。
- 不允许匿名访问短信、验证码或设备信息。
- 不主动删除手机系统短信。
- 不把第三方账号验证码分发给无权访问的人员或系统。
- 第一阶段不依赖 Redis、消息队列或 Kubernetes。

## 3. 总体架构

```text
Android App -----------+
Magisk/KSU/APatch -----+
iOS Shortcut ----------+--> HTTPS Ingest API --> PostgreSQL
iOS Ve tweak ----------+          |                  |
                                  |                  +--> retention cleanup
                                  +--> OTP parser
                                  +--> audit log
                                  +--> notification routing
                                  +--> Web dashboard / Claim API / SSE
```

所有采集端共享版本化 JSON 协议、设备身份、幂等键和错误码。设备端可以更换，后台数据模型和业务 API 不变。

## 4. 技术选型

### 4.1 服务端

- Python 3.12 + FastAPI
- SQLAlchemy 2 + Alembic
- PostgreSQL 16
- Pydantic Settings 管理环境变量
- 应用层 AES-256-GCM 加密短信正文和验证码
- pytest 覆盖协议、鉴权、幂等、过期和并发领取
- 同一 FastAPI 容器提供编译后的 Web 管理界面和 SSE

选择 FastAPI 是为了快速提供清晰的 OpenAPI、便于维护移动端协议，并避免第一阶段拆出多余服务。验证码原子领取依赖 PostgreSQL 行锁，不需要 Redis。

### 4.2 管理界面

- React + TypeScript + Vite
- 紧凑型后台布局，不复刻参考图，但覆盖同等业务能力
- 桌面和移动端响应式页面
- 图标使用 Lucide React
- 视图：概览、短信收件箱、验证码、设备、通知规则、审计与设置

### 4.3 非 Root Android

- Android Studio 工程
- Kotlin + Jetpack Compose
- `BroadcastReceiver` 监听 `android.provider.Telephony.SMS_RECEIVED`
- `RECEIVE_SMS` / `READ_SMS` 权限，用户显式授权
- WorkManager 负责断网重试和指数退避
- Room 保存尚未得到服务器确认的待上传记录
- Android Keystore 保存设备密钥
- 前台状态页展示最近接收、上传、失败和重试信息

这类用途最合适的是原生 Kotlin App。Flutter/React Native 仍需编写原生短信接收插件，后台限制、权限和进程恢复反而更难控制。由于 Google Play 对短信权限有限制，第一阶段按私有签名 APK/内部发布设计。

### 4.4 iOS 快捷指令

- “信息”个人自动化触发器按发件人或正文关键字筛选
- 将 Shortcut Input 的正文、发件人和当前时间组成 JSON
- 使用“获取 URL 内容”向 Ingest API 发起 HTTPS POST
- 每台 iPhone 使用独立、可撤销的长随机 Bearer Token
- 服务端通过客户端幂等键去重

快捷指令不能保证所有 iOS 版本和所有触发条件都完全静默执行，部署文档必须明确要求关闭“运行前询问”或启用系统提供的自动运行选项，并用真实短信完成一次验收。

### 4.5 Root Android 与越狱 iOS

- Magisk/KernelSU/APatch：第二阶段提供共享 shell/Go daemon，优先从系统短信数据库变化或受控通知事件读取，再调用统一 Ingest API。
- Ve：保留 `BBServer publishBulletin:destinations:` 的通知捕获方式，只处理 `com.apple.MobileSMS`，把原 Bark 上报替换为统一 HTTPS 客户端。
- 两者都使用 HMAC-SHA256、时间戳、nonce 和设备密钥；密钥不得写入构建产物中的公共默认值。

## 5. 设备与鉴权

系统只有一个管理员，不开放注册。

### 5.1 管理员

- 首次启动从环境变量读取管理员用户名和初始密码。
- 密码使用 Argon2id 保存，首次登录可在后台修改。
- Web 登录使用 Secure、HttpOnly、SameSite=Lax 会话 Cookie。
- 修改设备、查看明文短信、显示验证码、生成 Token 和调用历史 API都写入审计日志。

### 5.2 设备

- 管理员在后台创建设备并选择接入类型。
- 服务端只展示一次设备凭据，之后只能撤销和重新生成。
- Shortcut 使用 Bearer Token；服务端只保存 Token 哈希。
- Android App、Root 模块和 Ve 使用 HMAC-SHA256；服务端加密保存验证密钥。
- 请求包含设备 ID、时间戳、nonce 和幂等键；时间偏差默认不超过 5 分钟。

## 6. 上报协议

### 6.1 端点

```http
POST /api/v1/ingest/messages
Content-Type: application/json
Authorization: Bearer <device-token>
X-Idempotency-Key: <stable-client-message-id>
```

支持 HMAC 的客户端额外发送：

```text
X-Device-Id
X-Timestamp
X-Nonce
X-Signature = base64url(HMAC-SHA256(secret, timestamp + "\n" + nonce + "\n" + sha256(body)))
```

### 6.2 请求体

```json
{
  "schema_version": 1,
  "client_message_id": "device-stable-id",
  "received_at": "2026-08-14T10:23:48+08:00",
  "sender": "service-name-or-number",
  "body": "完整短信正文",
  "source": "android_app",
  "sim_slot": 1,
  "metadata": {
    "subscription_id": 3
  }
}
```

服务端限制正文和 metadata 大小，日志永不输出正文、验证码或设备凭据。`device_id + client_message_id` 建唯一约束，重试不会生成重复短信。

## 7. 验证码解析与状态

默认解析规则：

- 正文包含“验证码、校验码、动态码、OTP、code”等关键词时提高置信度。
- 提取 4-8 位数字，排除手机号、年份、金额和明显订单号。
- 支持按发送者设置自定义正则和有效期。
- 无法可靠识别时只保存短信，不创建 OTP 记录。

状态机：

```text
received -> available -> claimed
                   \-> expired
```

- 默认 OTP API 有效期：从平台接收时间起 10 分钟。
- `available`：脚本可以领取。
- `claimed`：已被某个 API 客户端原子领取，普通领取接口不再返回。
- `expired`：超过有效期，普通领取接口不再返回。
- 管理后台在短信正文保存期内仍可查看已领取或已过期记录。

领取接口：

```http
POST /api/v1/otp/claim
Authorization: Bearer <api-client-token>
Content-Type: application/json
```

请求可按设备、发送者、正文关键字和最早接收时间过滤。服务端在事务中使用 `SELECT ... FOR UPDATE SKIP LOCKED`，确保并发请求不会领取同一个验证码。

## 8. 保存与删除策略

- 手机系统短信：平台不修改或删除。
- 手机端待上传队列：得到服务器成功确认后删除正文，只保留最小状态信息。
- 平台完整短信：默认保存 7 天，可在后台配置。
- OTP API 有效期：默认 10 分钟，可全局或按规则配置。
- 完整短信过期后：清除正文、验证码密文和可恢复内容，保留设备、发送者摘要、时间、处理结果和审计关联。
- 管理员可以手动立即删除单条或批量短信。

清理任务必须幂等，并记录删除数量，不记录被删除的敏感正文。

## 9. 管理后台

第一阶段页面：

1. 概览：设备在线情况、今日短信、可领取 OTP、失败上传和最近事件。
2. 短信收件箱：按设备、发送者、时间、OTP 状态筛选；默认遮罩敏感内容。
3. 验证码：待领取、已领取、已过期；管理员可复制、作废和查看来源短信。
4. 设备：创建、启停、轮换凭据、最后上报时间、客户端类型和版本。
5. API 客户端：创建只具备 Claim 权限的 Token，限制设备和发送者范围。
6. 通知规则：Webhook、Bark、Telegram、企业微信等出口先实现通用 Webhook，其他通道后续以适配器增加。
7. 审计与设置：登录、查看明文、复制验证码、设备变更、保留期和解析规则。

实时刷新使用 SSE；断线后页面自动重连，数据库仍是唯一事实来源。

## 10. Docker Compose

默认 Compose 服务：

- `app`：FastAPI、静态管理界面、清理任务和 API 文档。
- `db`：PostgreSQL 16，使用命名卷。
- `caddy`：可选生产入口，提供 HTTPS 和反向代理；本地开发可直接访问 `app`。

交付文件包括：

- `compose.yml`
- `.env.example`
- 健康检查
- 数据库迁移入口
- 首次启动说明
- 备份与恢复说明

生产部署要求 HTTPS；Shortcut Bearer Token 不允许通过明文 HTTP 跨网络发送。

## 11. 错误处理

- 设备端仅在 2xx 后确认上传完成。
- 401/403 停止自动重试并提示凭据失效。
- 409 返回已存在记录，设备端视为成功。
- 429 和 5xx 使用带抖动的指数退避。
- Android 本地队列设置容量上限，达到上限时保留最新记录并明确告警。
- Webhook 出口独立重试，不阻塞短信入库。

## 12. 验证计划

### 12.1 服务端自动测试

- 管理员登录、会话和权限隔离。
- Bearer 与 HMAC 正确/错误/过期/重放请求。
- 重复上报幂等。
- OTP 正确识别与误识别样本。
- 10 分钟过期边界。
- 并发 Claim 只有一个请求成功取得同一 OTP。
- 7 天清理只删除敏感内容，不破坏审计统计。
- 日志中不出现短信正文、验证码和密钥。

### 12.2 客户端验证

- Android 真机：单卡、双卡、锁屏、断网、重启、进程被杀、恢复网络。
- iOS 真机：快捷指令手动测试、个人自动化真实短信测试、失败响应可见。
- Root/Ve：第二阶段分别在至少一台受支持设备验证，不用模拟结果冒充完成。

### 12.3 Compose 验收

1. 空目录复制 `.env.example` 并设置真实随机密钥。
2. `docker compose up -d --build`。
3. 数据库迁移和健康检查通过。
4. 用真实设备 Token 上报一条测试短信。
5. 管理后台可查看，Claim API 可领取一次且第二次无法重复领取。
6. 重启 Compose 后数据仍存在。

## 13. 分阶段交付

### Phase 1

- Docker Compose 后台、PostgreSQL、管理 UI
- 统一 Ingest、设备、消息、OTP Claim、审计 API
- 非 Root Android Kotlin App
- iOS 快捷指令模板与安装文档
- 通用 Webhook 出口

### Phase 2

- Magisk/KernelSU/APatch 模块
- Ve 插件的统一 API 适配
- 更多通知通道

### Phase 3

- 备份恢复工具、密钥轮换、细粒度告警
- 更多设备兼容性测试和发布包签名流程

## 14. 已确认决策

- 采用混合接入方案 C。
- 单管理员、多台本人持有或已授权设备。
- 平台完整短信默认保存 7 天，手机原短信不受影响。
- OTP 默认 10 分钟内可由普通 API 领取；过期后后台在保存期内仍可查看。
- 保存期和 OTP 有效期相互独立，均可配置。

