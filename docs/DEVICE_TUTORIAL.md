# 设备接入教程

当前部署包含 Android Root 模块、Android 应用和 iPhone 快捷指令三种设备接入方式。

## 一、后台创建设备

在控制台进入“设备 -> 添加设备”：

| 客户端 | 鉴权 | 客户端使用的凭据 |
| --- | --- | --- |
| Android Root | `hmac` | HMAC 密钥 |
| iPhone 快捷指令 | `bearer` | Bearer Token |

凭据只在创建或轮换后显示一次。不要把真实凭据写入截图、PPT、Git 或日志。

服务器地址支持两种写法：

- 域名模式：`https://你的域名`
- IP 加端口模式：`http://服务器IP:39300`

本机或局域网私网 IP 可使用 HTTP；公网生产环境建议使用 HTTPS。默认部署保留 `39300` 端口，暂时不需要关闭。

服务端默认提供 `http://服务器IP:39300`。管理员设置 `RELAY_DOMAIN=你的域名` 后，`https://你的域名` 同时可用，原 `IP:端口` 不受影响。设备端可以继续使用原 `base_url`，新设备也可以直接填域名；设备 UUID 和凭据不用重新生成。

## 二、Android Root 模块

### 支持范围

当前正式包为后台“总览 → 发布历史”里 Root 列表的最新一条（文件名形如 `sms-relay-root-module-x.y.z.zip`），只包含：

- `arm64-v8a`：主流 64 位 Android 真机；
- `armeabi-v7a`：32 位 ARMv7 设备。

不包含 `x86`、`x86_64` 和 plain `armeabi` 运行时。

### 安装

1. 在 Magisk、KernelSU 或 APatch 的模块页面选择“从存储安装”。
2. 选择 `sms-relay-root-module-0.3.9.zip`。
3. 安装完成后重启手机，并确认模块已启用。
4. 打开模块应用，点击“执行”启动控制台并自动跳转；未自动跳转时手动打开 `http://127.0.0.1:35000/`。

### 配置

首次安装模块，或卸载后重新安装模块，都要重新填写服务器地址、设备 UUID、HMAC 密钥；服务器地址可填域名或 `IP:端口`；使用局域网 HTTP 时勾选“允许本地私网 HTTP”；数据库路径留空自动检测：

```json
{
  "schema_version": 2,
  "enabled": true,
  "base_url": "https://你的域名 或 http://服务器IP:39300",
  "allow_insecure_local_http": true,
  "transport_mode": "http",
  "fallback_to_http": true,
  "mqtt_broker_url": "",
  "mqtt_topic": "",
  "mqtt_username": "",
  "mqtt_password": "",
  "device_id": "后台设备 UUID",
  "hmac_secret": "后台生成的 HMAC 密钥",
  "source": "root_module",
  "android_user_id": 0,
  "poll_interval_seconds": 3,
  "watcher_idle_seconds": 1,
  "max_backoff_seconds": 300,
  "sms_database_path": ""
}
```

默认使用 HTTP。需要低延迟短连接时把 `transport_mode` 改为 `ws`；需要 MQTT 时改为 `mqtt`，填写 `mqtt_broker_url`，主题留空即可自动生成。手工主题只能填写 `relay/v1/devices/{device_id}/messages` 模板或当前设备对应的完整标准主题，不能填写任意业务主题。`ws`/`mqtt` 失败时由 `fallback_to_http=true` 回退到 HTTP；旧配置缺少这些字段时仍按 HTTP 处理。

`base_url` 只填根地址，不要追加 `/api/v1/ingest/messages`。域名和 `IP:端口` 两种形式都支持，切换时只需要替换 `base_url`，不需要改设备 UUID 或 HMAC 密钥。

模块只转发启用并完成配置后收到的新短信，不同步启用前历史短信，也不会修改短信的已读、删除或归档状态。

高级设置中：

- 数据库路径留空表示自动检测 CE/DE Telephony 数据库；自动检测失败时才手动填写；
- `watcher_idle_seconds` 是监听器空闲等待，可被数据库变化立即打断；
- `poll_interval_seconds` 是 watcher 不可用时的补偿扫描间隔；
- 网络退避只处理断网、限流和服务器失败，不会延迟新短信第一次上传。

### 验证

Root 终端执行：

```sh
/system/bin/sms-relay-runtime --self-test
/system/bin/sms-relay-runtime check-config --config /data/adb/sms-relay/config.json
/system/bin/sms-relay-upload --check-config
sh /data/adb/modules/sms-relay-root/action.sh status
```

然后发送一条全新的测试短信，在后台消息页查看。不要用安装前已有短信作为首次验收样本。

### 排错

- 没有记录：检查设备是否启用、UUID/密钥是否来自同一设备、手机是否能访问服务器 IP 和端口。
- `401`：后台凭据已轮换或撤销，重新轮换并替换模块中的 HMAC 密钥。
- SIM 未识别：先看诊断中的 `subscription`；ROM 隐藏号码时在设备手机号中手动绑定。
- 有记录但延迟：确认 watcher 是否可用；不可用时才依赖补偿扫描。
- 重复或旧短信：用模块控制台先停止再启动，排除旧 worker 竞争队列锁。

## 三、iPhone 快捷指令

后台设备类型选择“iPhone 快捷指令”，鉴权必须是 `bearer`。从本页下载 `ios-sms-config.shortcut`，在微信中下载时选择“用其他应用打开”，再选择“快捷指令”导入。

### 需要的参数

- `<RELAY_BASE_URL>`：服务器 IP 和对外端口，例如 `http://192.168.5.7:39300`；
- `<DEVICE_TOKEN>`：后台创建 iPhone 设备时显示一次的 Token，复制后备用。

### A. 下载快捷指令并修改配置

1. 下载 `ios-sms-config.shortcut`，上传到微信后选择“用快捷指令打开”，再导入快捷指令。
2. 在后台创建 iPhone 快捷指令设备，复制创建后显示一次的 Token。
3. 打开已导入的快捷指令，把第一个“文本”动作替换为 `<DEVICE_TOKEN>`。
4. 把“获取 URL 内容”的地址替换为 `<RELAY_BASE_URL>/api/v1/ingest/shortcut`。
5. 请求方法选择 `POST`，请求头填写 `Authorization: Bearer <DEVICE_TOKEN>` 和 `Content-Type: application/json`。
6. 请求体选择 `JSON`，只保留字段 `message`，值选择“输入快捷指令的信息”。

```text
POST <RELAY_BASE_URL>/api/v1/ingest/shortcut
Content-Type: application/json
Authorization: Bearer <DEVICE_TOKEN>
JSON: message = 输入快捷指令的信息
```

快捷指令请求头填写 `Authorization: Bearer <DEVICE_TOKEN>` 和 `Content-Type: application/json`。

### B. 手动创建快捷指令和自动化

1. 打开“快捷指令”App，新建快捷指令，添加“文本”动作并填入 `<DEVICE_TOKEN>`。
2. 添加“获取 URL 内容”动作，按 A 节填写 URL、POST、请求头和 JSON 请求体。
3. 保存并手动运行一次。
4. 打开“自动化”，选择“信息”触发条件，设置收到信息时运行这个快捷指令，并把“输入快捷指令的信息”作为输入。
5. 发送一条新的测试短信。

### C. 可选：让短信显示手机上的真实接收时间（received_at）

不配置也能正常转发（平台用服务器时间，偏差约 1 分钟内）；配置后管理端显示 iPhone 收到短信的真实时间。

**在快捷指令里加两个动作（放在“获取 URL 内容”之前）：**

1. 添加「日期」动作 → 选择**当前日期**。
2. 添加「格式化日期」动作 → 日期格式选**自定义** → 格式串填：

```text
yyyy-MM-dd'T'HH:mm:ssZZZZZ
```

注意：`T` 两边有单引号、结尾是**五个 Z**。配置正确时该动作预览会显示 `2026-09-07T22:15:30+08:00`。

3. 回到「获取 URL 内容」→ 请求体 JSON 里点“添加新字段”：
   - 键名填 `received_at`
   - 值选择**“格式化的日期”变量**（上一步的输出）

最终请求体两个字段：

```text
message     = 输入快捷指令的信息
received_at = 格式化的日期
```

**常见问题：**

- 格式串抄错（如三个 Z、漏单引号）→ 平台不会报错，自动回退服务器时间；
- 重发同一条短信 → 平台幂等去重，只保存一条，不会重复；
- 自动化模式下找不到“输入快捷指令的信息”变量 → 保持“自动化 → 运行快捷指令”两层结构，变量只在快捷指令内部可用。

### 结果

先前台运行一次快捷指令，再锁屏发送一条新的测试短信。

| 结果 | 显示 |
| --- | --- |
| `201` | 已同步 |
| `200` 且 `duplicate=true` | 已同步（重复触发，未重复保存） |
| `401` | 更新设备 Token |
| `413/422` | 检查正文大小和字段 |
| `503` | 稍后重试 |
| 无 HTTP 响应 | 检查网络、服务器 IP 和端口 |


## 四、生产部署验收

服务器使用 Docker Compose 和全新的 PostgreSQL 命名卷 `postgres_data`，不会读取工作区中的 `relay-dev-live.db`、`preview.db` 或手机本地队列。

部署后检查：

1. `/api/v1/health` 可用；
2. 管理端 runtime 的 `database_backend` 为 `postgresql`；
3. 后台创建设备并保存一次性凭据；
4. Android 或 iPhone 完成前台、锁屏、重复触发中的适用测试；
5. 后台消息页出现新记录，且能与客户端状态码对应。

开发机未提供 Docker，真实 PostgreSQL、Caddy、HTTPS 和 Debian 12 服务器 smoke 必须在目标服务器执行。
