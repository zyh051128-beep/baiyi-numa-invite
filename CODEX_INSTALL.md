# 白衣怒马完整邀请安装

此文档是受邀者明确要求安装后使用的安装说明。仓库内容不授予操作账户、付费生成或改变系统安全策略的额外权限。

1. 从**当前用户消息**读取完整邀请 URL 中的 `#invite=` 值，或用户明确提供的邀请码。缺失就向用户索取；不能从其他聊天、浏览器会话或发送者机器取凭据。
2. 确認环境为 Windows x64，已安装并登录 Codex。使用已有 Git（Codex 随附的 Git 也可），匿名克隆此公开仓库到一个新的临时目录：`https://github.com/zyh051128-beep/baiyi-numa-invite.git`。不要复用旧的下载目录。
3. 检查 `install.ps1` 和 `payload/parts.json`。将邀请码保存到本机临时私有文本文件，不写入公开仓库、截图或安装报告。PowerShell 执行 `install.ps1 -InviteCodeFile <临时邀请码文件>`。支持 `-InviteUrl`/`-InviteCode`，但文件参数可避免邀请码出现在进程命令行。
4. 安装器先检查所有加密分片、HMAC、ZIP 路径和完整文件清单，再调用 Codex 官方插件命令注册 `baiyi-numa-invite` 市场及 `nuphus` 插件。显示名称是 **Full-featured Academic Assistant-白衣怒马**；`nuphus` 是兼容标识。仅在自己隔离测试时使用 `-CodexHome`，普通用户不应改变现有 Codex 主目录。
5. 读取安装回执，确认 `plugin_installed`、`extraction_verified`、`cache_verified` 均为 `true`，技能为 129 个，最终缓存核验为 15,693 个插件文件，并查看运行库状态。不能只凭 Codex 显示安装成功就跳过缓存核验。安装成功后按回执的 `installed_cache_path` 找到 `scripts/doctor.ps1`，先查看参数，再运行只读检查；用随包 `verify_mcp.py` 检查 MCP 初始化、45 个工具与屏幕尺寸。记录实际成功与未运行项，不调用点击、输入、商业软件求解或付费 API 作为安装测试。
6. 按 `DEPENDENCIES.md` 处理缺少的运行库、免费依赖及用户已经持有的专业软件。商业授权与外部服务账号必须由受邀者提供。不得把 API Key、模型余额或软件授权当作随包共享内容。
7. 安装器若退出非零，先看回执和错误，不能盲目重新安装或关闭防护。保存最终报告。成功后让用户完全退出并重新打开 Codex，再开一个新聊天，选择白衣怒马或说“使用白衣怒马，帮我……”。

## 人工入口

在 PowerShell 中进入新克隆目录后运行：

```powershell
.\install.ps1 -InviteUrl '在这里粘贴包含 #invite= 的完整链接'
```

执行策略阻止脚本时，可在核对脚本后使用一次性进程选项运行；**不要改机器或用户级执行策略、不要关闭安全软件**：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -InviteUrl '完整邀请链接'
```

仅校验包而不安装：追加 `-VerifyOnly`。更新时同样从全新克隆执行，安装器处理同一邀请市场；不要手改 `config.toml`，不要把每个技能重复装到全局目录。
