# 设备生命周期与客户端适配设计

## 目标

补齐管理台和接入层中已经确认的缺口：

- 撤销设备后可以恢复，但恢复必须生成新凭据；
- 提供与撤销分开的永久删除操作；
- 管理台可以创建 Android Root 模块、iOS Ve 插件和 SIM 卡板设备；
- 统一记录设备的 SIM 槽、订阅标识、运营商和手机号映射；
- 提供 Android Root/KernelSU/Magisk/APatch、iOS Ve 和卡板的可执行接入说明；
- 保留 Android 非 Root App 与 iOS 快捷指令现有协议兼容性。

## 当前事实与边界

当前一期代码已经有 Android 非 Root 的短信接收与 HMAC 上报骨架，以及 iOS 快捷指令的 Bearer 上报契约。`root_module` 和 `ve_tweak` 目前只在存储层留下了名称，尚未进入设备契约、前端选择器或客户端包。

`WingCH/Ve`、Magisk/KernelSU 短信模块和卡板平台不是同一个可直接安装的产品。它们需要分别适配统一 Ingest API。卡板驱动在没有品牌、型号和 API 文档时只提供通用 Webhook/轮询适配接口，不能标记为厂商兼容。

Android 系统不保证能返回真实 MSISDN。客户端必须优先采集 `subscriptionId`、SIM 槽和运营商；手机号允许自动读取，失败时由管理员手动绑定。

## 设备生命周期

### 撤销

现有 `DELETE /api/v1/admin/devices/{id}` 保持为撤销操作：将设备置为 inactive，立即阻断上报，保留设备、消息和审计记录。

### 恢复

新增 `POST /api/v1/admin/devices/{id}/restore`：

1. 仅允许恢复已撤销设备；
2. 在同一事务中生成新 Bearer/HMAC 凭据并重新启用设备；
3. 旧凭据永久失效；
4. 只返回一次新凭据；
5. 写入 `device.restored` 审计事件。

恢复不是简单地把 `active` 改回 true，避免撤销前泄露的旧凭据重新生效。

### 永久删除

新增 `DELETE /api/v1/admin/devices/{id}/purge`：

- 只允许对已撤销设备执行；
- 删除设备凭据、nonce、短信、验证码及其关联数据；
- 保留不含短信正文和密钥的 `device.purged` 审计事件；
- 前端必须二次确认并要求输入设备名称，避免误删；
- 永久删除不可恢复，接口不复用撤销路径。

## 客户端类型

统一设备契约如下：

| 客户端 | `client_type` | 鉴权 | `source` | 交付物 |
|---|---|---|---|---|
| Android 非 Root | `android_app` | HMAC | `android_app` | Kotlin App/APK 与配置说明 |
| Android Root | `root_module` | HMAC | `root_module` | Magisk/KernelSU/APatch 模块包与配置说明 |
| iOS 快捷指令 | `ios_shortcut` | Bearer | `ios_shortcut` | 动作定义与自动化说明 |
| iOS Ve | `ve_tweak` | HMAC | `ve_tweak` | Ve 适配补丁/构建说明 |
| SIM 卡板 | `sim_board` | HMAC | `sim_board` | 通用 Webhook/轮询适配器，厂商驱动另行实现 |

Root 模块、Ve 和卡板都使用现有 HMAC 请求格式，避免为每种客户端再创建一套服务端鉴权。

## SIM 与手机号映射

新增设备 SIM 配置实体，最小字段为：

- `device_id`
- `slot_index`
- `subscription_id`（可空）
- `phone_number`（可空，可脱敏展示）
- `carrier_name`（可空）
- `source`：`auto`、`manual` 或 `vendor`
- `updated_at`

短信上报继续携带 `sim_slot` 和 `metadata.subscription_id`。后台通过映射显示手机号；客户端无法读取号码时不阻塞短信上报。

## 管理台交互

设备操作按状态分开：

- 启用设备：轮换凭据、撤销；
- 已撤销设备：恢复并生成新凭据、永久删除；
- 永久删除使用危险确认弹窗，明确会删除关联短信和验证码；
- 客户端下拉新增 Android Root、iOS Ve、SIM 卡板；
- 设备详情/创建流程支持填写手机号和 SIM 槽映射。

## 安装与配置文档

本轮补齐可执行文档：

- Android 非 Root：构建/安装 APK、创建后台设备、录入 URL/设备 ID/HMAC、权限、电池优化、手机号读取失败时手动绑定；
- Android Root：Magisk/KernelSU/APatch 刷入步骤、配置文件位置、服务启动检查、日志脱敏、卸载回滚；
- iOS Ve：从 Ve 源码构建适配、只处理 MobileSMS、配置 HMAC、重启 SpringBoard、失效凭据处理；
- SIM 卡板：通用 JSON Webhook 和轮询格式、HMAC 签名、槽位/号码字段映射，以及等待厂商 API 后的驱动接入点。

## 测试与验收

- 后端：恢复生成新凭据、旧凭据失效、永久删除级联、只允许撤销设备恢复/删除、客户端契约和 SIM 映射接口；
- 前端：启用/撤销操作按钮分离、恢复和永久删除确认、五种客户端下拉项；
- Android/Root/Ve：协议契约测试和配置解析测试；
- 卡板：通用 Webhook/轮询适配器的签名和字段映射测试；
- 真机、Root 环境、Ve 注入和具体卡板必须单独标记为外部验收，不以本机单元测试代替。

## 未决输入

卡板厂商、型号、通信方式和 API 文档尚未提供。在收到这些信息前，只实现通用适配接口和示例配置，不宣称已支持某个具体卡板。
