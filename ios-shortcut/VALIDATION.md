# iPhone 验收

- 在电脑运行 `node --test ios-shortcut/tests/*.test.js`，确认 HTTP/HTTPS、Bearer 和响应分类测试通过。
- 自动化必须把触发的“信息”作为 `输入快捷指令的信息` 传给“文本”快捷指令。
- “获取 URL 内容”只使用 `/api/v1/ingest/shortcut`、Bearer Token、Content-Type 和一个 `message` JSON 字段。
- 不在手机端创建 `sender`、`body`、日期、SHA-256、`client_message_id` 或 `X-Idempotency-Key`。
- 使用已注册的测试设备完成一次手动快捷指令测试；本机或局域网 IP 可使用 HTTP，公网生产环境建议使用 HTTPS。
- 使用正文 `Your relay access code is 482731`，确认后台出现一条消息和一个 `available` 访问码。
- 重复触发同一条稳定短信输入，确认返回 `200 duplicate=true` 且后台仍只有一条短信。
- 用测试网络故意制造一次超时或 5xx，确认快捷指令只等待约 2 秒并重试一次；401、422 和已成功的 200/201 不应重复发送。
- 含 `Date` 的信息对象应在管理台显示 `message_date`；没有有效设备时间时才回退到服务器接收分钟。
- 真实“信息”自动化测试前台与锁屏行为，记录系统是否要求确认；不能用电脑单测代替真机验收。
- 结果提示只显示“已同步”、重复同步、鉴权失败、请求过大、字段无效、服务不可用或网络失败，不显示 Token 或完整短信正文。
- 轮换后旧 Token 返回 `401`；删除设备后当前 Token 返回 `401`。
- 截图、控制台和临时请求只能放在 `debug/ios-shortcut/`，必须脱敏，不得提交。

真机验收记录至少包含 iOS 版本、是否锁屏、是否需要确认、HTTP 状态码和后台 message ID，不记录 Token 或完整验证码。
