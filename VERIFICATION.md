# 发布核验记录

版本：`1.0.0+codex.20261008165120` · 2026-10-09（北京时间）

**Windows x64、Apple 芯片 Mac、Intel Mac 均已验证。**全部 129 个技能及 15,708 个插件文件完整保留；MCP 登记 45 个接口。

| 测试环境 | 实际通过的检查 |
|---|---|
| Apple Silicon / macOS 15.7.9 | 新安装、再次更新、全部文件哈希及 427 个可执行文件权限、45 个工具登记、离线合成图片 OCR、异常退出回收、默认 Codex 配置未变 |
| Intel / macOS 15.7.9 | 与 Apple 芯片相同的完整安装、更新、权限、协议、OCR 及异常测试 |
| Windows x64 | 从公开仓库匿名下载后实际更新；15,708 个最终缓存文件与源逐字节相同；45 个工具、屏幕尺寸与离线 OCR 通过；隔离测试未改变正常用户配置 |

Mac 使用官方 Codex CLI 0.161.0、中文和空格路径、系统 OpenSSL 解密。21 项安装器测试和 17 项检查程序测试均通过，无跳过。查看[完整 Mac 验收](https://github.com/zyh051128-beep/baiyi-numa-invite/actions/runs/37812577154)及[Intel 兼容组件构建](https://github.com/zyh051128-beep/baiyi-numa-invite/actions/runs/37808116787)。

Intel 使用固定 nuphus-mcp v0.3.1 源码与 ort 2.0.0-rc.12 的 API23 兼容选项，搭配微软官方 ONNX Runtime 1.23.2。只变更该接口版本选项，保留应用工具代码及全部模型。补丁、独立版本记录、来源哈希与对应许可证随包提供。

匿名下载对应提交：`35a5c63cf8eaab65df5f176d376e30b22a995e2f`。6 个加密分片及整体摘要通过，邀请码保持兼容。加密包 SHA-256：`c1f872ed65dd9cc5cb95cfd3ec4fc84791a842aefcc1eaef406cbaccea8de445`。机器可读记录见 [VERIFICATION.json](VERIFICATION.json)。

Mac 运行组件最低 macOS 14。自动测试没有授予录屏/辅助功能权限，没有操作真实桌面、商业求解器或付费云服务。45 个接口不等于所有上游旧式桌面动作在 Mac 均可用；具体平台限制见 [DEPENDENCIES.md](DEPENDENCIES.md)和随包 MCP 工具参考。软件许可证、外部服务与具体业务任务仍须按接收者环境准备。
