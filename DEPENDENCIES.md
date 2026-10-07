# 功能与环境准备

**所有 129 个技能入口及配套资源均在包内。**下表是执行相应任务所需的外部环境，不是缺失技能清单。安装器与 Doctor 只检查，不自动购买、登录服务或启动收费任务。

| 功能 | 需要的环境 | 如何判断可用 |
|---|---|---|
| Codex 技能读取与任务组织 | Windows x64 Codex 桌面版、自己的登录账号 | 插件列表已安装且启用，新聊天可选白衣怒马 |
| 45 个本地桌面/浏览器工具、本地 OCR/图标识别 | 随包 exe/DLL/模型；Microsoft Visual C++ 2015–2022 x64 运行库；交互式桌面；浏览器任务需要 Chrome/Edge | `verify_mcp.py` 实际完成 initialize、tools/list、screen_size；OCR 可再用无隐私样图单测 |
| SolidWorks、AutoCAD、Ansys/Fluent、COMSOL、MATLAB/Simulink | 本机对应软件、版本支持的接口、所需模块/许可证 | 检测安装路径之后，按用户任务另作实际启动/小算例验证；仅发现文件不算求解成功 |
| COMSOL–MATLAB LiveLink | COMSOL LiveLink for MATLAB 授权、匹配 MATLAB；按实际位置设置 `COMSOL_MLI_PATH` 或 `COMSOL_HOME` | 使用随包连接脚本解析路径，需进一步连接验证 |
| FreeCAD、CadQuery、OpenFOAM、Gmsh、ParaView | 各工具官方发行版；OpenFOAM 在 Windows 通常需要适当 Linux/WSL 环境 | 各工具自己的版本命令/最小样例；不替用户开启系统虚拟化或安装大型软件 |
| Python 科研计算与仿真 | Python、对应任务的独立虚拟环境及相关库；部分任务需 GPU/原生库 | 依据所选技能的 requirements/项目声明安装，再实际运行小例；不在全局一次安装互相冲突的所有库 |
| PPT、图表、文档 | 相应 Python/Node 库；需要 Office 自动化时有对应 Office；字体/渲染器按交付目标准备 | 生成并检查实际文件及渲染结果 |
| FFmpeg/Remotion、动画与视频 | FFmpeg；Node.js 及项目锁定依赖；按需浏览器渲染器 | 实际渲染短小测试片段，收费云渲染须另有任务授权 |
| 图像生成与编辑 | 当前 Codex 可用图像工具，或所选提供商账号 | 选择本会话真实可用工具；不会因为包内有说明就假定已开通 |
| image-blaster 图片转三维 | Node.js/Bun 按随包项目要求；World Labs、FAL 对应阶段的账号与额度；本机查看器依赖 | `prepare_workspace.py --check-template` 核对113个模板文件；生成和查看器构建必须分别实际验证 |
| Rowan、云视觉和其他云服务 | 受邀者自己的服务账号、API Key、余额和网络 | 不打印密钥，只报告是否配置；付费调用按用户任务执行 |

Microsoft 运行库官方入口：[最新受支持的 Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist)。使用微软签名的 x64 安装程序；已有运行库满足要求时不重复安装。

已安装、已检测、已实际运行和未运行要分别记录。安装成功保证插件文件完整，不等于全部专业软件、云服务或每一种联合仿真都已实测通过。
