# iPhone Ve 与 Android 上游复核报告

日期：2026-11-14
范围：`xueliymd/sms-relay-hub` 基线恢复、`WingCH/Ve` 苹果短信集成、Android 参考项目增量复核、本地集成与验证边界。

## 1. 结论摘要

1. GitHub 主仓库已作为唯一基线完成核对：`origin/main` 为
   `209723011f75d2e43f8ea6daf508ddf748a28a54`，干净克隆有 405 个跟踪文件，
   工作树无缺失、无未提交差异。当前工作区里的约 2.9 万个无关未跟踪文件没有被
   纳入备份，也没有执行 `git add -A`、`git add .` 或 `git clean`。
2. `WingCH/Ve` 最新 `main` 为 `a69204defc3e7fb6b14638f32dc8225807655aa9`，
   版本 2.3.4，日期 2026-10-04。该版本是 prerelease；GitHub
   `releases/latest` 仍是 2.2。
3. Ve 的入口是通知层 Hook（`BBServer publishBulletin:destinations:`），不是
   读取短信数据库。它适合作为“新短信通知 -> 中转服务”的入口，但不能保证覆盖
   所有静默/被过滤通知。本项目在此基础上增加了独立开关、严格来源过滤、
   HMAC 签名和隐私日志边界。
4. Ve 2.3.4 的“完整 API request/response capture”会保存未遮蔽 URL、headers、
   token 和明文 body。该能力不适合作为短信中转传输链，也不应默认开启。本次
   集成不读取、不复制这些 capture 数据。
5. Android `pppscn/SmsForwarder` 上游较上次基线前进 5 个提交。最重要的两点是
   服务端默认 fail-closed（CVE-2026-110618 修复）和 Android 16/17 长短信发送
   的本地切分降级。前者与本项目现有安全方向一致；后者只影响未来远程发送短信，
   当前接收链路不受影响。
6. Root 模块 `Magisk_SmsForwarder` 无新提交，仍为 `254575885f9e…` / 1.2。
7. 已完成源码级 Ve 2.3.4 整合补丁、许可与第三方声明、独立开关、来源契约测试、
   文档和隐私清理。补丁已在一个干净 Ve worktree 上通过 `git apply --check`。
8. **未完成真机验收**：Windows 本机没有已授权越狱 iPhone、Theos/RootHide
   环境，也没有 Android SDK/系统 Gradle。不能把源码检查写成“真机可用”。

## 2. GitHub 基线恢复核对

| 项目 | 结果 |
| --- | --- |
| 仓库 | `xueliymd/sms-relay-hub` |
| 远端分支 | `origin/main` |
| 提交 | `209723011f75d2e43f8ea6daf508ddf748a28a54` |
| 跟踪文件 | 405 |
| 缺失跟踪文件 | 0 |
| 干净克隆状态 | clean |
| 当前工作分支 | `codex/ve-tweak-integration` |

核对方式：

- 对主仓库执行 `git fetch --prune origin`，确认远端未变化。
- 使用独立临时目录重新克隆，检查 `git status --porcelain` 为空。
- 只以 GitHub 跟踪树为可信基线；本地旧目录中的实验文件、日志和缓存不参与
  备份。
- 所有后续暂存均使用明确路径，不使用全量暂存。

## 3. WingCH/Ve 上游分析

### 3.1 版本与发布状态

| 项目 | 值 |
| --- | --- |
| Upstream | `https://github.com/WingCH/Ve` |
| main commit | `a69204defc3e7fb6b14638f32dc8225807655aa9` |
| main 日期 | 2026-10-04 |
| 版本 | 2.3.4 |
| 2.3.4 tag | `c71140e99930692c3b71f58b4717759f9813de0c` |
| 2.3.4 发布类型 | prerelease |
| GitHub latest release | 2.2 |
| License | GPL-3.0 |

### 3.2 通知采集架构

Ve 的核心链路是：

```text
SpringBoard / BulletinBoard
    -> BBServer publishBulletin:destinations:
    -> sectionID / title / message / publicationDate / date
    -> 本地通知日志、AI 过滤、Bark 转发
```

关键判断：

- `sectionID == com.apple.MobileSMS` 可以作为“信息”通知的 bundle 过滤条件。
- 该入口依赖私有 selector，必须对每个 selector 做
  `respondsToSelector:` 和类型检查，不能直接假定所有 iOS 版本一致。
- 通知层 Hook 不是短信数据库采集。通知被系统去重、折叠、静默或未发布时，
  不会形成同样可靠的事件流。
- Ve 已处理 respring/reboot 后的通知重放，本项目又通过稳定的
  sender/body/date 消息 ID 和服务端幂等做第二层去重。

### 3.3 2.3.4 的隐私风险

Ve 2.3.4 新增完整 API trace 保存：

- request body、response body 以完整 bytes/Base64 保存；
- UTF-8 body 同时保留原文；
- 不解析或重建 JSON；
- 不遮蔽 token、URL、headers；
- 不截断 body。

这意味着启用该功能后，本地日志可能直接包含 Bark token、AI provider token、
请求头、完整通知正文和响应内容。它与短信中转的“最小字段、脱敏日志”原则冲突。

本次处理：

- 不让 SMS Relay 读取 Ve 的 API capture；
- 不把 Ve 的 `LogManager` 或 Bark 配置作为中转凭据来源；
- 在客户端文档中明确将其视为秘密资料；
- `SMSRelayClient` 只记录固定分类和数字 HTTP 状态，不打印正文、URL、签名、
  Secret、发送方或手机号。

### 3.4 本次集成的设计

补丁文件：

`clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch`

改动点：

1. `Tweak/Core/Makefile` 增加 `Security` framework。
2. `VeCore.m` 导入 `Manager/SMSRelayClient.h`。
3. 只对 `com.apple.MobileSMS` 进入短信中转分支。
4. 对 `sectionID`、`title`、`message`、`publicationDate`、`date` 做 selector
   和类型保护。
5. 日期优先 `publicationDate`，不可用时回退 `date`。
6. Ve 日志开关和 SMS Relay 开关互相独立：
   - Ve 开、Relay 关：保持原 Ve 行为；
   - Ve 关、Relay 开：仍安装 Hook，只运行 Relay；
   - 两者都关：不安装 Hook。
7. 不修改 Ve 的 blocked senders、日志上限、AI、Bark 和原有重放逻辑。

客户端契约：

```json
{
  "schema_version": 1,
  "client_message_id": "<stable sender/body/date id>",
  "received_at": "<UTC ISO-8601>",
  "sender": "<notification title>",
  "body": "<notification message>",
  "source": "ve_tweak",
  "metadata": {
    "client_version": "ve-tweak-1"
  }
}
```

签名输入为：

```text
<unix-seconds>\n<nonce>\n<sha256-hex(body)>
```

使用 HMAC-SHA256，nonce 为 16 字节随机值的 base64url；服务端继续执行
时间窗口、持久 nonce、设备 UUID、来源/鉴权类型绑定和幂等检查。

### 3.5 与独立 Tweak 的关系

仓库同时保留：

- 独立 `VeSMSRelay` Tweak；
- 合并到 VeCore 的补丁模式。

两者都会 Hook 同一个 `BBServer` selector：

```text
BBServer publishBulletin:destinations:
```

**只能安装其中一种。** 同时安装会出现双重 Hook、重复请求和难以排查的行为
差异。

### 3.6 GPL 边界

Ve 为 GPL-3.0。`clients/ve-tweak/COPYING` 已保存上游许可证，根目录
`THIRD_PARTY_NOTICES.md` 说明：

- 与 Ve 合并的派生包需要遵守 GPL-3.0；
- 主仓库其他部分不因该目录被整体重新声明为 GPL-3.0；
- 分发组合包时必须提供对应源代码和许可证。

这不是法律意见；若公开发布整合 `.deb`，应按 GPL-3.0 的源代码义务准备
完整对应源码。

## 4. Android 上游增量

### 4.1 SmsForwarder

上次基线：

`ce931a03739a6a2b0dfc83161bf812a94fd5e45e`

最新 HEAD：

`a142f9270624e96b15ecfae53035581354fb6434`（2026-11-01）

新增 5 个提交：

| 提交 | 变更 | 建议 |
| --- | --- | --- |
| `a3d2302` | 模板标签正则支持 `=` / 先行断言 | 当前无同款模板 DSL，不直接移植；未来引入模板时补样本。 |
| `0466e1d` | 服务端默认 fail-closed，修复 CVE-2026-110618 | 吸收安全原则。本项目已有更窄的设备契约、默认关闭指令授权、HMAC 防重放和幂等。 |
| `fd9852c` | 修复 CI APK 输出路径 | 仅构建流水线问题，不涉及运行时协议。 |
| `45cb86b` | 按定位精度显式选择 provider | 本项目不采集定位，不引入定位权限。 |
| `a142f92` | Android 16/17 部分 ROM 的 `divideMessage()` 异常时本地切分：GSM 7bit 160/153，UCS2 70/67 | 未来远程发短信时必须吸收；当前只接收短信，不受影响。 |

上行亮点：

- Android 16/17 的 `TelephonyManager.getGroupIdLevel1()` 异常会导致部分 ROM
  无法发送中文长短信；上游用本地长度切分绕开框架异常。
- 这是“发送”路径的兼容修复，不是“接收”路径；本项目现有
  `messages.joinToString("")` 合并多段 PDU，不能拿这条补丁替代接收端测试。

### 4.2 SmsForwarder v3.5.0 功能观察

最新 release 仍为 v3.5.0（2026-02-14），包含：

- Bark 随机 IV、重要警告；
- Telegram Topic、MarkdownV2；
- MQTT qos/retained；
- 大文本通知；
- 邮件别名；
- WOL；
- 联系人姓名/归属地标签；
- 免打扰、短信指令、快捷指令等客户端能力。

判断：这些多数是其“单 App 多通道”模型的产品能力。本项目已有集中式服务端
规则引擎、统一加密存储和 OTA/设备管理，不建议为了功能对齐而整体搬运，
否则会扩大权限、依赖和隐私面。

### 4.3 Magisk_SmsForwarder

- HEAD：`254575885f9e3b1cb4ba2b0b9e64d4bb90d9bd7b`
- 标签：1.2
- 本次核对无新提交。
- 继续保持本项目的事件 watcher、spool、HMAC、幂等、退避重试和低频扫描兜底，
  不回退到上游固定 10 秒轮询。

## 5. 已实施与建议优先级

### P0：本次已完成

- 固化 Ve 2.3.4 整合补丁和可重复应用说明；
- 增加 `SMSRelayClient.isEnabled`，实现 Ve 日志关闭时的独立短信转发；
- 补强私有 selector 的运行时保护；
- 增加 `ve_tweak` 来源正确绑定和跨设备来源拒绝测试；
- 增加第三方许可证/来源声明；
- 清理测试/文档中的疑似真实公网 IP 和手机号，统一使用文档保留地址与
  示例号段；
- 将 Ve 源文件统一为 UTF-8 无 BOM、LF，并通过 scoped `.gitattributes`
  固化换行规则。

### P1：后续可做

1. **Android 多段接收回归测试**：把 PDU 合并提取为纯函数，覆盖
   GSM 7bit、UCS2、中文、拼接顺序、空段和时间戳选择。
2. **远程 `send_sms` 前置设计**：先完成指令原子 claim/端侧去重，再接
   `SmsManager`；吸收上游 `divideMessage()` 异常的本地切分降级。
3. **iPhone 通知覆盖增强**：在真实 iOS 版本上验证
   `publicationDate`/`date`、同一通知更新、后台通知和 respring 重放；
   若目标版本不发布 MobileSMS bulletin，再评估是否有更合适的系统入口。
4. **凭据配置体验**：目前通过 0600 plist 管理，安全但不友好；后续可做
   管理端生成配置 + 二维码/文件导入，但必须避免把 Secret 写入普通偏好或日志。

### P2：不建议直接移植

- Ve 的完整 API capture 默认开启；
- SmsForwarder 的多通道全家桶；
- 上游内置 HTTP Server 的直连控制模型；
- 为接收短信引入定位、联系人、通知读取等额外权限。


## 6. 验证记录与未完成边界

已完成：

- GitHub 干净克隆与远端一致性核对；
- Ve 2.3.4 干净 worktree 上整合补丁 `git apply --check`；
- Ve 客户端 HMAC/协议静态契约；
- `ve_tweak` 后端来源契约测试；
- 隐私占位替换与换行/编码检查。

尚未完成：

- Theos/roothide 真实编译；
- 越狱 iPhone 安装、注入和真实通知触发；
- 真实 HTTPS relay 的端到端上传；
- Android SDK/Gradle 编译及 Android 真机长短信测试。

因此本次状态应描述为：

> **源码级集成与自动化契约已完成；真机/端到端验收未完成。**

## 7. 变更文件

- `clients/ve-tweak/README.md`
- `clients/ve-tweak/SMSRelayClient.h`
- `clients/ve-tweak/SMSRelayClient.m`
- `clients/ve-tweak/COPYING`
- `clients/ve-tweak/patches/README.md`
- `clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch`
- `clients/tests/verify_vectors.ps1`
- `backend/tests/test_relay.py`
- `docs/ANDROID_GITHUB_COMPARISON.md`
- `docs/IPHONE_VE_ANDROID_UPSTREAM_REVIEW_2026-11-14.md`
- `THIRD_PARTY_NOTICES.md`
- `.gitattributes`
- 测试/设计文档中的公网 IP 脱敏替换
