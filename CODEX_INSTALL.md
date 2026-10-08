# 白衣怒马完整邀请安装（Windows / Mac）

此文档供受邀者明确要求安装后使用。仓库不授予操作账户、付费生成或改变系统安全策略的额外权限。

1. 从**当前用户消息**读取完整 URL 中 `#invite=` 值或用户明确提供的邀请码。缺失时索取；不能从其他聊天、浏览器会话或发送者机器取凭据。
2. 确认已安装并登录 Codex，检测系统。用已有 Git（可用 Codex 附带 Git）匿名克隆到新临时目录：`https://github.com/zyh051128-beep/baiyi-numa-invite.git`。不要复用旧下载目录。
3. 检查当前系统安装器和 `payload/parts.json`。将邀请码保存到临时私有文本文件，不放进仓库、截图、报告或命令行。Mac 用 `sh install.sh --invite-code-file <临时文件>`；Windows 用 `install.ps1 -InviteCodeFile <临时文件>`。完成后移除自己创建的临时邀请码文件。
4. Mac 自动检测芯片，使用已有 Python 3.9+ 和系统 OpenSSL（也支持已有 cryptography）。Python 或 Codex 命令缺失时，先定位 Codex 附带环境，确实缺失再按官方方式准备。Mac 不运行 PowerShell 安装器。桌面运行库需要 macOS 14+；旧系统可完整安装技能，但须如实报告桌面组件不支持。
5. 安装器核验分片、HMAC、ZIP 路径和逐文件清单，然后通过 Codex 官方命令注册市场 `baiyi-numa-invite` 与插件 `nuphus`。显示名为 **Full-featured Academic Assistant-白衣怒马**。Mac 按认证清单应用 `.mcp.json` 并恢复脚本执行位，保存派生清单。不要手改 Codex 配置或重复将所有技能安装到全局目录。
6. 回执中 `plugin_installed`、`extraction_verified`、`cache_verified` 均须为 `true`；技能 129 个，文件数以本次 `RELEASE.json` 为准。既校验解压目录，也校验 Codex 实际缓存全部文件。普通安装不要传 `--codex-home` / `-CodexHome`；这些用于隔离测试。
7. 按 `installed_cache_path` 找到检查脚本。Windows 用 `scripts/doctor.ps1` / `scripts/verify_mcp.py`。Mac 先用 `scripts/verify_mcp_macos.py --skip-screen-check` 核验协议与工具登记；用户授予录屏权限后才能做真实屏幕检查。可用无隐私样图测试离线 OCR。不点击、不输入、不求解商业算例、不调用付费 API。45 个接口登记不等于所有平台动作均已验证。
8. 按 [DEPENDENCIES.md](DEPENDENCIES.md) 区分就绪、缺少依赖、平台不支持与未测试项。录屏和辅助功能由用户在系统设置中授予实际启动应用。不得关闭 Gatekeeper、自动移除隔离属性或代授权限。软件许可、云服务账号和额度由受邀者提供。
9. 非零退出先看回执及错误，不盲目重试。退出码 2 表示完整安装后桌面环境仍不满足条件，不能报全部就绪。成功后完全退出再打开 Codex，**新建聊天**，选择白衣怒马或说“使用白衣怒马，帮我……”。

## 人工入口

在新克隆目录执行。Mac 会提示输入邀请码且不回显：

```sh
sh install.sh
```

Windows：

```powershell
.\install.ps1 -InviteUrl '包含 #invite= 的完整邀请链接'
```

Mac 支持 `--invite-url`、`--invite-code-file`、`--codex-path`；如已有 Python 不在常用位置，可设置 `BAIYI_PYTHON` 为完整解释器路径。`--verify-only` / Windows `-VerifyOnly` 只校验不安装。Windows 策略阻止时，核对脚本后可用一次性 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`；不要改机器或用户级策略。

更新从**全新克隆**执行同一系统入口、使用原邀请码。保留旧安装以便回滚，避免覆盖原目录。
