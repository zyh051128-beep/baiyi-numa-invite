# ❄ Full-featured Academic Assistant-白衣怒马

**完整插件邀请安装入口 · Windows 64 位 / macOS**

**Mac 适配已完成**：Apple 芯片与 Intel 均通过完整安装、更新、文件权限、MCP 连接及离线 OCR 测试。桌面组件需 macOS 14+，实际验收系统为 macOS 15.7.9。详见[发布核验记录](VERIFICATION.md)。

本入口分发完整的白衣怒马插件：**129 个技能入口、45 个桌面/浏览器 MCP 工具**，包含所有配套脚本、OCR/图标识别模型、三维查看器源码与模板、参考资料和来源许可记录。安装器对每一个文件校验，不是只有简介或入口的精简包。

## 收到链接后怎么安装

先安装并登录 **Codex 桌面版（Windows 或 Mac）**。将收到的**完整邀请链接（包括末尾 `#invite=...`）**原样发到 Codex 的一个普通聊天中，并加上：

> 请按照此链接仓库的 CODEX_INSTALL.md，安装 Full-featured Academic Assistant-白衣怒马。保留全部 129 个技能和 45 个 MCP 工具，核对完整文件清单，运行环境检查和只读 MCP 连接检查。按 DEPENDENCIES.md 列出已经可用和需要我提供软件许可证、服务账号的功能，保存安装报告。不要把缺少外部软件误报成技能丢失，也不要调用付费生成来测试。完成后提醒我重启 Codex 并新建聊天。

**邀请码缺失时**，向发送者索取同一邀请码，与链接放在同一条消息里。无需 GitHub 账号。不要将只有仓库首页、没有邀请码的链接当成完整邀请。

**Mac 专用入口**：在新下载的仓库目录执行 `sh install.sh`，按提示输入邀请码；安装器会自动识别芯片。也可将完整邀请链接交给 Codex 按上面的说明安装。不要在 Mac 上运行 `install.ps1`。下载过旧版的人请重新下载同一仓库。

## 包含什么

| 类别 | 主要能力 |
|---|---|
| 建模与工程制图（43 项） | SolidWorks、AutoCAD、FreeCAD、CadQuery、ForgeCAD；参数化模型、工程图、尺寸、制造与 3D 打印工作流 |
| 演示、图像、视频、三维场景及辅助（51 项） | 可编辑 PPT、网页幻灯片、图表、海报、图片生成和编辑、FFmpeg/Remotion 视频、字幕；image-blaster 单图转环境、GLB/OBJ 物体、音效与三维查看器 |
| 仿真、科研计算与验证（34 项） | Fluent、OpenFOAM、COMSOL–MATLAB、Ansys、MATLAB/Simulink、Gmsh、ParaView；数值分析、信号、优化、单位及不确定度、流体/分子/量子/离散事件等专项模拟 |
| 统一入口（1 项） | 白衣怒马根据任务选择、组合专项技能，串联建模→仿真→分析→图表→汇报 |
| 本地工具与基础方法 | 45 个桌面/浏览器工具接口（具体动作以平台支持为准）、本地 OCR 与图标检测；六类桌面及流程参考方法 |

所有技能名称见 [RELEASE.json](RELEASE.json)。完整明文文件清单在加密包内的 `release-manifest.json`；安装器检查文件缺失、多出、大小和 SHA-256。

## 安装与使用边界

- Windows x64 和 Mac 都安装**全部 129 项技能及资源**。Mac 自动配置启动器、逐文件核验实际缓存并恢复执行权限；桌面运行组件需要 **macOS 14 或更新**，回执明确显示运行组件和权限状态。
- 包内提供 Apple 芯片和 Intel 两种 Mac 运行组件，安装器自动选择。两种芯片使用各自兼容的本地识别运行库，来源、补丁和许可证均随包保存。
- Mac 的录屏与辅助功能权限由用户授予；部分上游桌面动作只支持 Windows。SolidWorks、Windows COM 及 Ansys/Fluent 求解另有平台要求，见 [DEPENDENCIES.md](DEPENDENCIES.md)。保留完整技能不等于这些软件都能在 Mac 原生执行。
- 插件包含全部技能与资源，**不包含 SolidWorks、MATLAB、COMSOL、Ansys 等商业软件的安装许可**。免费软件和 Python/Node 依赖按任务准备；详见 [DEPENDENCIES.md](DEPENDENCIES.md)。
- 推理使用受邀者自己的 Codex/ChatGPT 账号；World Labs、FAL、云视觉等可选服务使用受邀者自己的账号与额度。不会共享邀请人的任何账号或密钥。
- 加密包使用随机邀请码保护。**持有完整链接的人可以解密，也可以转发**；不是实名白名单。已经下载的副本不能远程收回。
- 各组件保留原有许可和已知限制；完整 `THIRD_PARTY_NOTICES.md` 与许可文本随包提供。本入口不把不同来源统一重新授权。

安装说明：[CODEX_INSTALL.md](CODEX_INSTALL.md) · 环境依赖：[DEPENDENCIES.md](DEPENDENCIES.md) · 发布核验：[VERIFICATION.md](VERIFICATION.md)
