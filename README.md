# 微信定时发送 V10

一个面向 Windows 微信桌面版的本地定时发送与时间校准工具。

## 主要功能

- 使用 NTP 估算本机时钟偏移。
- 支持多个 NTP 时间源。
- 可结合 TShark 与微信进程链路进行延迟测量。
- 临近目标时间自动重测，并使用 SendInput 发送 Enter。
- 保存校准记录和发送结果，方便复盘。
- 提供提前量、客户端补偿、抢跑保护和贴边修正等参数。

## 环境要求

- Windows 10/11
- Python 3.10 或更高版本
- 微信 Windows 桌面版
- 可选：Wireshark/TShark（用于抓包测向）

## 从源码运行

```powershell
run\start_wechat_timer.bat
```

也可以直接运行：

```powershell
py src\ntp_key_timer_wechat_revised.py
```

## 打包 EXE

双击 `build_windows.bat`。脚本会安装 PyInstaller，生成文件位于 `dist` 目录。

## 使用提示

1. 每次正式使用前重新同步 NTP。
2. 确认光标仍在正确的微信输入框内。
3. 换电脑、网络或网卡后，重新选择 TShark 路径及抓包网卡。
4. 先在测试聊天中验证时间和发送行为，再用于正式场景。

运行过程中可能生成以下本地文件，它们已被 `.gitignore` 排除：

- `wechat_send_calibration.csv`
- `wechat_send_calibration_debug.log`
- `wechat_send_records.csv`

## 注意

本项目仅用于个人设备上的定时任务研究与界面自动化测试。网络和系统调度会造成误差，程序也可能误发消息；请仅操作自己的账号并遵守平台规则。

