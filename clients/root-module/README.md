# SMS Relay Root Module

这是 SMS Relay Hub 的 Root-only Android 适配器，支持 Magisk、KernelSU 和 APatch。
它是一个系统模块，不包含 APK，也不需要另外安装短信读取 App。

模块负责完整的本地链路：

```text
Android 短信 Provider
        |
        +-- content://sms/inbox（首选）
        |
        +-- Telephony SQLite sms 表（Provider 不可用时的 arm64 回退）
        v
私有 spool -> HMAC 签名 -> HTTP / WebSocket / MQTT -> SMS Relay Hub
```

模块只读取新收到的入站短信，按消息 ID 和时间生成幂等事件；正文不会写入日志。上传失败的事件会留在本地 spool，网络恢复后继续发送。

## APatch 安装

不要使用 `adb install`，这个包不是 APK。

1. 下载 `sms-relay-root-module.zip`，确认文件在手机的“下载”目录。
2. 打开 APatch，进入“模块”。
3. 点击右下角“安装”或“从存储安装”。
4. 选择“下载/sms-relay-root-module.zip”。
5. 等待安装日志出现 `Installed SMS Relay runtime`。
6. 返回 APatch，点击“重启”。
7. 重启后回到 APatch，确认模块开关已启用。

KernelSU 和 Magisk 的流程也是“模块 -> 从存储安装 -> 选择 ZIP -> 重启”。

## 包内内容

| 文件 | 用途 |
| --- | --- |
| `service.sh` | 开机启动扫描和上传服务 |
| `action.sh` | Root 管理器里的状态/停止/恢复操作 |
| `system/bin/sms-relay-runtime` | 原生短信读取、游标、SIM 元数据和 HMAC 工具 |
| `system/bin/sms-relay-upload` | 读取 spool、签名并上传到后端 |
| `system/bin/sms-relay-wake` | 触发立即扫描的唤醒入口 |
| `system/bin/sms-relay-watch` | 使用系统 `inotifyd` 监听 Telephony 数据库目录与 WAL/SHM 变化 |
| `config.example.json` | 配置示例，不含真实密钥 |

包内只有运行时二进制和 Shell 脚本，没有 APK、DEX 或第三方短信读取程序。

## 配置一台设备

首次安装后，在电脑或手机 Root 终端创建：

```text
/data/adb/sms-relay/config.json
```

权限必须是 `0600`，目录权限建议为 `0700`。配置字段如下：

```json
{
  "schema_version": 2,
  "enabled": true,
  "base_url": "https://你的域名",
  "allow_insecure_local_http": false,
  "transport_mode": "http",
  "fallback_to_http": true,
  "mqtt_broker_url": "",
  "mqtt_topic": "",
  "mqtt_username": "",
  "mqtt_password": "",
  "device_id": "后台创建设备后得到的 UUID",
  "hmac_secret": "后台创建设备后得到的 32 字节 base64url 密钥",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 3,
  "watcher_idle_seconds": 1,
  "max_backoff_seconds": 300,
  "sms_database_path": "",
  "allow_query_battery": false,
  "allow_query_recent_sms": false,
  "allow_trigger_upload": false
}
```

`base_url` 填服务端根地址，不要把 `/api/v1/ingest/messages` 再写进去；上传器会自动追加该路径。

配置文件由 Root 服务按严格的扁平 JSON 读取：每个字段单独占一行，顶层 `{` 和 `}` 单独占一行，不要把对象压缩成单行，也不要使用嵌套对象、数组或 JSON 转义字符串。控制台保存的配置会自动使用兼容格式。

### 远程控制授权

Web 控制台“远程控制授权”中的三项默认全部关闭。后台下发指令后，设备会正常领取并回传明确结果；若授权未开启，结果为 `command_not_authorized`，不会静默失败。

- `allow_query_battery`：允许后台查询电量与充电状态。
- `allow_query_recent_sms`：允许后台读取最近 5 条短信；发件人会打码，正文最多返回 200 个字符。
- `allow_trigger_upload`：允许后台唤醒一次立即上传。

不需要远程控制时保持三项关闭。指令未领取会在 10 分钟后变为 `expired`；已领取但未回传会在 30 分钟后变为 `expired`。

- 生产环境：填 `https://你的域名`；若使用 HTTPS IP 加端口，例如 `https://203.0.113.10:8443`，也支持。
- 本地同一 Wi-Fi 联调：可以显式打开 `allow_insecure_local_http`，并使用私网 IP 加端口，例如 `http://192.168.2.109:39300`。该开关只接受回环或私网 IP，不接受公网 HTTP、主机名 HTTP 或没有端口的地址。
- 默认部署的服务端入口是 `http://服务器IP:39300`；服务端设置 `RELAY_DOMAIN` 后，`https://你的域名` 同时可用。`base_url` 可以继续用 `IP:端口`，也可以换成域名；设备 UUID 和 HMAC 密钥不用变。

不要把 `hmac_secret` 写进公开仓库、截图或聊天记录。`device_id` 和 `hmac_secret`
必须来自后台设备授权页面，不能自行编造。

传输模式由 `transport_mode` 控制，旧配置缺少这些字段时自动使用 `http` 并开启 HTTP 回退：

- `http`：默认模式，直接请求 `/api/v1/ingest/messages`。
- `ws`：通过 `base_url` 派生 `/api/v1/ws/ingest`，适合低延迟短连接上传；WS/MQTT 传输等待上限为 10 秒，HTTP 回退连接等待 5 秒、总等待 15 秒。
- `mqtt`：连接 `mqtt://`、`mqtts://`、`ws://` 或 `wss://` Broker，发布 QoS 1 消息；TCP Broker 地址不能带路径，WebSocket Broker 必须填写合法路径。
- `mqtt_broker_url` 和 MQTT 用户名/密码只在 `mqtt` 模式使用；密码不会显示在控制台响应中。
- `mqtt_topic` 留空时自动使用 `relay/v1/devices/<device_id>/messages`；手工填写时只能使用 `relay/v1/devices/{device_id}/messages` 模板或当前设备对应的完整主题。主题按 UTF-8 字节数限制在 512 字节以内。
- `fallback_to_http=true` 时，WS/MQTT 失败会继续使用原有 HTTP + 本地队列；关闭后失败事件留在队列中等待下一次处理。

配置完成后，模块会：

1. 先校验配置和 HMAC 参数；
2. 第一次运行只建立游标，不补发旧短信；
3. 之后读取新入站短信，写入私有 spool；
4. 上传成功后删除对应 spool 文件；
5. 服务端按 `device_id`、接收 SIM 和发送人做路由，并执行去重/OTP 生命周期。

默认补偿扫描间隔是 3 秒；检测到可用的 `inotifyd` watcher 后，空闲等待使用 `watcher_idle_seconds`（旧配置缺省为 1 秒），并由短信数据库目录或 `mmssms.db`、WAL、SHM 文件变化通过 `SIGUSR1` 立即打断等待。这个值可以在手机 Root 控制台修改并热生效。没有 `inotifyd`、目录不可见或 watcher 退出时，自动回退到配置的 3 秒补偿扫描。等待期间不忙等，也不发送网络心跳；只有新短信或失败重试才上传。

### 本地队列领取和恢复

每条待上传短信先从 `spool` 原子领取到 `run/inflight` 下的唯一 `claim` 目录，再执行网络请求；上传器不会直接读取仍在写入的原始队列文件。这样同一条短信不会被两个 worker 同时发送。设备重启或 worker 中断后，会先恢复 `run/inflight` 中尚未完成的 claim，再读取新的短信。claim 目录和文件名发生冲突时会保留原文件并生成冲突后缀，不会覆盖已有内容。

网络失败、服务器失败或 claim 失败会让本轮立即停止并进入指数退避；不会继续逐条尝试剩余 spool 文件，避免断网时每条短信都等待一次超时导致整体补传变慢。

HTTP 200/201 会删除已完成的 claim；HTTP 409/422 以及本地正文校验失败会转入 quarantine，保留事件供排查；删除 claim 失败会显示为 `claim_delete_failed`，不会把它误报为已完成。

## 手机本地配置控制台

不需要 ADB，也不需要让控制台常驻后台。打开 APatch、KernelSU 或 Magisk 的模块 Action（或在 Root 终端执行下面命令）：

```sh
sh /data/adb/modules/sms-relay-root/action.sh
```

模块会按需启动本机控制台并打开浏览器：

```text
http://127.0.0.1:35000/
```

控制台只监听回环地址，局域网和公网无法访问。页面关闭后连续 10 分钟没有心跳，控制台进程自动退出；短信转发 worker 不受影响。

可以修改后台地址、私网 HTTP 开关、启用状态和设备 ID。页面内的“高级设置”提供短信数据库自动检测/手动路径、监听空闲等待、补偿扫描、网络退避上限和 Android 用户 ID；“短信上报通道”提供 HTTP、WebSocket、MQTT 和 HTTP 回退设置。`sms_database_path` 留空表示自动检测；手动填写时只接受 TelephonyProvider 下的 `mmssms.db`，保存前做只读打开、`sms` 表、必要字段和最大 ID 验证。无效路径不会覆盖当前有效配置，模块不会修改短信已读、删除或归档状态。已有 HMAC 密钥只显示“已设置”，不会回显；只有勾选替换并输入新密钥时才会更换。点击“保存并热生效”后会原子替换配置并唤醒 worker，不需要重启手机。

控制台使用“快速配置 / 上报通道 / 高级诊断”三个分区卡片布局；顶部固定标题和分区导航，状态卡单独显示服务状态与待处理数量，保存按钮固定在底部。手机窄屏下分区导航可横向滑动，表单和按钮自动切换单列，避免所有设置挤在一页。

### 扫码导入后台配置

首次配置不需要手工复制地址、设备 UUID 和 HMAC 密钥：

1. 在网页管理端“设备”页面创建或选择 `client_type=root_module`、`auth_kind=hmac` 的设备。
2. 点击设备操作栏的二维码按钮，生成一次性设备配置二维码；二维码有效 5 分钟，只能兑换一次，不包含长期 HMAC 密钥。
3. 在手机 Root 管理器中打开模块 Action，进入本地控制台，点击“扫描二维码”，允许浏览器使用相机并对准后台二维码。
4. 控制台会通过二维码中的 HTTPS/HTTP 地址向 `/api/v1/enrollment/consume` 兑换当前设备凭据，原子写入 `/data/adb/sms-relay/config.json`，清除停用标记并热启动转发服务。
5. 如果浏览器不支持相机扫码，可选择二维码图片，或把二维码原文粘贴到输入框后点击“导入二维码配置”。

扫码兑换失败或本地写入失败时，不要重复使用同一个二维码；回到网页管理端重新生成。扫码导入只用于 Root 模块配置，短信上传使用配置的 HTTP、WebSocket 或 MQTT + HMAC + 本地 spool/重试链路，不会把 HMAC 密钥放进二维码或日志。

高级设置中的时间参数作用不同：监听空闲等待可被数据库变化立即打断；补偿扫描只在 watcher 不可用时使用；网络退避只处理断网、限流和服务器失败，不会延迟新短信第一次上传。运行诊断会显示实际读取模式、命中路径、自动/手动来源、最大 ID、游标和脱敏错误，不显示短信正文、手机号、验证码或设备密钥。

控制台顶部同时显示短信 worker 的最近状态和待处理数量。状态只包含上传结果码或本地错误分类，不包含短信正文、手机号、设备密钥或脚本令牌。常见状态：

- `鉴权失败（401）`：后台设备凭据已轮换、撤销，或手机保存的 HMAC 密钥不属于当前设备；更新密钥后，队列会自动补传。
- `上传失败（网络错误/HTTP 状态）`：消息仍留在私有队列，按退避上限继续重试。
- `最近上传成功（200/201）`：最近一条待处理消息已被平台接受。
- `短信读取失败`：Android Provider 和数据库回退都不可用，需要检查 ROM、用户 ID 或 Root/SELinux 环境。

状态页面只在控制台打开时每 3 秒读取本机 `127.0.0.1` 接口；控制台关闭后不会继续产生状态请求。页面心跳每 30 秒只访问手机本机控制台，用于 10 分钟空闲退出，不经过短信转发后端。worker 只在状态变化时原子写一次诊断文件，不会按轮询周期持续写入。

如果管理器没有显示 Action 按钮，也可以在 Root 终端使用：

```sh
sh /data/adb/modules/sms-relay-root/action.sh web
```

### 运行时依赖

模块使用 Android 系统自带 toolbox/toybox 命令，不额外安装 APK。服务和上传器需要：`sh`、`curl`、`mktemp`、`od`、`dd`、`date`、`awk`、`grep`、`sed`、`tr`、`wc`、`mkdir`、`rmdir`、`sleep`、`rm`、`mv`、`chmod`、`ln`、`cat`；系统存在 `inotifyd` 时会立即监听短信数据库目录变化，不存在时自动保留周期补偿扫描。系统存在 `flock` 时服务优先使用非阻塞文件锁，遇到 KernelSU/APatch shell 的坏文件描述符或系统缺少 `flock` 时自动回退到同目录原子目录锁。HMAC 和 SHA-256 由包内 `sms-relay-runtime` 提供，不依赖 `openssl`。安装后如果控制台显示 `runtime_unavailable`、`service_dependency_missing` 或 `runtime_probe_failed`，先在 Root 终端确认这些命令存在，再检查 ROM 的 SELinux/工作资料限制；缺命令时模块会停止发送，不会删除本地队列。

### 故障语义和数据边界

- HTTP 400 和 404 按 worker-level 可重试错误处理，消息留在 inflight；HTTP 409 和 422 表示该事件无法按原内容重试，会进入 quarantine。
- `local_body_invalid`、`local_body_too_large`、`local_body_read_failed` 是 machine-readable 本地结果。正文快照上限为 16 KiB（16384 字节），JSON 最大 depth 为 32 levels；超限消息不会发到网络。
- quarantine never overwrites 已有文件；文件名 collisions receive 递增后缀，避免覆盖另一条短信。
- 上传正文必须包含唯一 top-level string 字段 `source` 和 `client_message_id`，嵌套字段不能冒充顶层协议字段。
- `config_disabled` 表示配置明确停用。控制台保存采用同目录 temporary 文件、`0600` 权限和 atomic `mv` 替换；旧配置不会被半写入内容覆盖。
- `stop` 只有在 worker 已释放锁并退出时才返回成功；超时返回 `stop_pending`，不能把仍在运行的 worker 误报为停止。

开发机可运行 `clients/root-module/tests/behavior.sh` 复查实际 Shell 行为；完整协议门禁由 `clients/tests/verify_vectors.ps1` 执行并输出 `root_posix_runtime` 结果。

## 手机号和 SIM 路由

模块会尽力读取短信所属的订阅信息，并按以下优先级识别双卡：

1. 短信记录里的 `sub_id` / `subscription_id`；
2. Android 标准 `content://telephony/siminfo`；
3. Provider 没有返回结果时，读取系统 `dumpsys isub` 的 `subscription_id -> simSlotIndex` 映射；
4. 最后才使用短信记录自身提供的 `sim_id` / `slot_index`。

这和成熟 Android 转发器的处理方式一致：短信广播中的 `slot` 在不同厂商上可能不可靠，`subscription_id` 通常更稳定，再通过活动订阅列表映射到卡槽。模块把这些字段留在加密上报数据中，后台界面不显示 SIM 槽位、运营商或内部订阅号。

一次扫描中的所有新短信共用同一份 SIM 快照，避免每条短信都重复查询 telephony Provider 和 `dumpsys isub`。在一加 `PJZ110` Android 15 真机上，`dumpsys isub` 会把手机号显示成 `[****]`，但 Root 下 `content://telephony/siminfo` 仍可返回活动订阅；当前运行时已实测完成 `短信 sub_id -> 订阅 _id -> SIM 槽位和手机号` 映射。不同系统版本若连 telephony Provider 也隐藏号码，模块仍会自动保存订阅号和 SIM 槽位，只把手机号留给后台手动绑定。

系统权限或厂商接口不提供本机手机号时，不会伪造号码，也不会阻塞短信接收。此时只需在后台“设备 -> 设备手机号”中给对应手机号做一次手动绑定；内部订阅/卡槽线索仍会用于双卡分流。

双卡设备如果系统返回的信息互相冲突，事件不会猜测另一张卡，而是保留订阅线索并进入未解析状态，等待手动绑定后再路由。

## 安装后检查

以下命令需要 Root 终端执行。命令只输出状态，不输出短信正文。

```sh
/system/bin/sms-relay-runtime --self-test
/system/bin/sms-relay-runtime check-config --config /data/adb/sms-relay/config.json
/system/bin/sms-relay-upload --check-config
```

检查服务状态：

```sh
sh /data/adb/modules/sms-relay-root/action.sh status
```

首次安装后不要用旧短信判断结果。先确认服务运行，再给该手机发送一条新的测试短信，最后在后台查看是否出现一条新记录。

如果服务端没有记录，按下面顺序检查：

1. 后台设备是否为“启用”且没有被撤销；
2. `config.json` 中的 URL、设备 UUID、密钥是否来自同一设备；
3. 手机是否能访问 HTTPS 地址；
4. `action.sh status` 是否显示服务在运行；
5. 控制台是否显示 `鉴权失败（401）`；轮换设备凭据后必须立即把新 HMAC 密钥写入模块；
6. 后台是否需要手动维护该 SIM 槽位映射。

## 停止、恢复和卸载

在 Root 管理器中可以停用模块；也可以执行：

```sh
sh /data/adb/modules/sms-relay-root/action.sh stop
sh /data/adb/modules/sms-relay-root/action.sh start
sh /data/adb/modules/sms-relay-root/action.sh status
```

模块配置、短信队列和运行状态放在独立的 `/data/adb/sms-relay/`，普通升级会保留它们。模块管理器执行“卸载”时会调用 `uninstall.sh`：先停止属于本模块的 worker 和 Web 控制台，再清理状态目录、`/data/adb/sms-relay.worker.lock` 以及目录锁 `/data/adb/sms-relay.worker.lock.d`；重新安装后会从空配置开始。只删除模块目录或强制清理管理器缓存，不等同于执行卸载清理。

如果旧版本曾执行过 `stop` 或 `revoke`，状态目录可能保留 `disabled` 标记。确认要恢复转发时先执行：

```sh
rm -f /data/adb/sms-relay/disabled
sh /data/adb/modules/sms-relay-root/action.sh start
```

确认 `action.sh status` 返回 `enabled` 后，再发送新的测试短信。

卸载前先在后台撤销设备。撤销后旧密钥立即失效，模块不会再被服务端接受。需要换设备或重新授权时，在后台恢复/重新注册设备并生成新密钥，不要复用旧密钥。

## 兼容性边界

模块按 Android 短信 Provider 的公开数据字段工作，并内置两种 ARM 运行时：`arm64`（对应 `arm64-v8a`，当前主流 64 位 Android 真机）和 `arm`（对应 `armeabi-v7a`，兼容 32 位 ARMv7 设备）。本包不再携带 `x86_64`、`x86` 或面向更老 ARM 指令集的独立运行时；只有 plain `armeabi` 的设备不属于当前支持范围。不同厂商对短信 Provider、工作资料用户、SELinux 和 telephony 数据库的限制不同，因此“所有机型无需验证”是不真实的承诺。

当前已完成：源码测试、Shell 合约测试、HTTP 分类测试、HMAC 向量测试、双 ABI 运行时自检和包内容检查；并已在小米 `M2011K2C` 的 APatch 环境安装旧版本做过 HTTP 201 联调。`0.2.7` 修复首次安装尚无配置时控制台无法启动；`0.2.9` 将 Provider 游标改为“时间戳 + 行 ID”水位线，避免升级后批量上传历史短信；`0.3.0` 增加目录锁降级并兼容一加 SIM 列差异；`0.3.1` 修复首次配置等待和发行 ABI 体积。`0.3.2` 在 ARM64 上改为直接发现并只读扫描 CE/DE Telephony SQLite。`0.3.3` 修复 MIUI 使用 `sub_id=-1` 时 SQLite 快速路径被错误中断并退回慢 Provider 的问题，增加中文高级设置、数据库验证、CE/DE 诊断、watcher 解锁后恢复、读取失败状态恢复和锁竞争清理保护。`0.3.4` 将同一只读 SQLite 兼容路径带到 ARMv7，并让控制台保留数据库验证的具体错误代码。`0.3.7` 扩展 `mmssms.db` 与 `sms.db` 的 WAL/SHM watcher，正常空闲等待改用事件唤醒，并删除上传路径中未使用的重复独立 SHA-256 计算；`0.3.8` 增加管理端二维码扫码、图片和粘贴导入，自动兑换 Root HMAC 配置并热启动服务；HMAC、spool、幂等和失败重试保持不变。模块不会读取启用前历史短信，也不会修改短信的已读、删除或归档状态。

`0.3.11` 在 Web 控制台新增远程指令授权开关，支持查询电量、查询最近短信和触发立即上传；三项默认关闭，未授权会明确回传，指令到期自动收口。`0.3.10` 收紧上传超时并优化失败退避：WS/MQTT 等待上限降为 10 秒，HTTP 回退改为 5 秒连接 / 15 秒总等待；一条上传失败会立即进入退避，不再逐条等待剩余 spool。控制台同步改为吸顶分区导航、状态卡和卡片式布局。

上述结论只覆盖本次真机和本地私网 HTTP 联调。其他 ROM、其他 Root 管理器、其他 ABI、工作资料用户以及真实生产 HTTPS 仍属于 external acceptance，不能用这一次实测替代；也不能把仅有 source/static 检查的组合写成“所有机型已经转发成功”。
