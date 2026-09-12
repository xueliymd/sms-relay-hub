# Android 私有分发

1. 使用组织控制的 signing keystore 构建 release APK；keystore、alias 和密码只能放在 CI Secret 或本机私密配置。
2. 不要提交 `local.properties`、`*.jks`、`*.keystore`、签名后的 APK 或真实设备配置。
3. 通过 MDM、受控文件共享或 USB `adb install -r` 分发给授权设备。
4. 首次安装后人工核对包名、签名指纹、HTTPS 域名和短信权限。
5. 轮换服务端设备凭据时，先更新手机，再撤销旧凭据；若旧凭据已经失效，队列会停在 `AUTH_BLOCKED`，不会继续发送后续记录。

Google Play 对短信权限有严格限制。该客户端面向本人或组织自有设备的私有分发，不应把短信权限用途伪装成公开商店功能。
