# APK 签名钥匙

`vocawiki-debug.keystore` 是**固定的** debug 签名钥匙（标准 Android debug key：
别名 `androiddebugkey`，storepass / keypass 都是 `android`，`CN=Android Debug`）。

为什么把它放进仓库：Gradle 打 debug 包时读 `~/.android/debug.keystore`，**文件不存在就
当场随机生成一把**。CI 每次都是干净的机器，于是每次推送发出去的 APK 都是新钥匙签的，
装不进上一个版本（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`，「应用未安装 / 签名冲突」，
用户 2026-10-21 报）。`build_android.sh` 构建前会把这份文件复制到 `~/.android/debug.keystore`
（本地与 CI 都一样），于是所有构建共用一把钥匙，设置页的「检查更新」才能原地覆盖安装。

- 指纹（SHA-1）：`46:7A:0E:B6:E9:CB:10:FC:5E:AE:4C:56:BB:66:CB:61:83:31:72:6C`
- CI 会核对新出来的 APK 是不是这把钥匙签的（见 `.github/workflows/android-apk.yml`）。
- 它是 debug key，口令是公开的 `android`，**不是**机密：想换成自己的钥匙就用
  `build_android.sh … release` 并设置 `P4A_RELEASE_KEYSTORE` / `P4A_RELEASE_KEYSTORE_PASSWD`
  / `P4A_RELEASE_KEYALIAS`。换钥匙后手机上的旧版必须先卸载一次。
