# ADS MCP 上游调研与取舍

调研日期：**2026-11-10（Asia/Shanghai）**。对象是 Keysight Advanced Design System，排除 Google/Meta/Amazon 广告投放类同名 ADS MCP、仪器控制及无关 ADS 名称。

本文件是公开资料和选定源码的静态审查。未安装或执行第三方项目，未把其自述工具数、测试数或演示指标作为本仓库的验证结果，没有声称穷尽全网。

## 检索方法

搜索引擎交叉查询 `Keysight ADS MCP github`、`Advanced Design System Model Context Protocol`、`ADS Momentum MCP`、`ADS MCP 射频 GitHub 自动化`、`ADS2025_help`、`ADS2027 Python MCP`；检查 GitHub keysight-ads 主题及 GitHub REST 仓库搜索 `keysight ads mcp`、`advanced-design-system mcp`、`keysight ads automation`。API 检索额外发现搜索引擎未列出的 Lavaxzyh/ads-mcp。

对候选读取 README、元数据、许可证、递归目录与关键源码。以下 commit 为调研时默认分支快照；更新时间仅表示发布活动，不证明功能质量。

## 候选与许可

| 项目 | 固定 commit | 许可 | API 最后推送时间（UTC） | 定位 |
|---|---|---|---|---|
| [Lavaxzyh/ads-mcp](https://github.com/Lavaxzyh/ads-mcp) | `2879203568fb7fa9a20da30b7208b866c8d13400` | MIT | 2026-11-07 | ADS2027 原理图、Layout、Momentum、emModel MCP |
| [cottman99/ads-agent-bridge](https://github.com/cottman99/ads-agent-bridge) | `5c3c26d22c55a1ad991c73e80228a770f5390b53` | MIT | 2026-10-31 | 上下文绑定、受控原生执行、会话管理 |
| [geer1895/rfauto](https://github.com/geer1895/rfauto) | `7a5801b46a68a6fe0ca252805bf1fff294fcefb4` | GPL-3.0 | 2026-11-02 | 多引擎 RF 服务、确定性核、MCP |
| [Zhang2070466526/edi-grpc-mcp](https://github.com/Zhang2070466526/edi-grpc-mcp) | `78c788d9064ad98403a13f0722b02ca6dd845ca9` | MIT | 2026-10-27 | EDI gRPC、HFSS、CST 统一服务 |
| [why250/ads-help-mcp](https://github.com/why250/ads-help-mcp) | `023067074e01dbec2b76d63efe2b25818f99d8aa` | 未发现复制/再分发许可证 | 2026-08-16 | 多版本本地 ADS 帮助检索 |
| [a11s11/keysight-ads-simulation-skill](https://github.com/a11s11/keysight-ads-simulation-skill) | `8088ebf4218b9ae278a7b78a3b4426abf65d77b3` | MIT | 2026-07-16 | 操作规范与辅助脚本，非独立 MCP |
| [Jchase0126/ads-agent](https://github.com/Jchase0126/ads-agent) | `11a44a54a2c04da02cacc8f3985c2f6fc100f8fd` | 未选开源许可，README 明示无额外复制授权 | 2026-11-05 | ADS GUI AI 插件，非通用 MCP |
| [cottman99/AI_RFIC_workflow](https://github.com/cottman99/AI_RFIC_workflow) | `561f251b41c9341ff29c4374001aac26d3093bbc` | 代码 PolyForm Noncommercial 1.0.0；文档 CC BY-NC 4.0 | 2026-03-10 | RFPro 像素版图/数据/代理模型，非 MCP |

MIT/GPL 已核对实际 LICENSE 与 API 元数据。[AI_RFIC_workflow LICENSE](https://github.com/cottman99/AI_RFIC_workflow/blob/561f251b41c9341ff29c4374001aac26d3093bbc/LICENSE) 明确非商业限制，不能视为无条件可商用依赖。公开可读不等于复制授权。本仓库没有复制以上项目的实现或文档片段。未来引入第三方代码，必须单独核对许可、NOTICE 和传递义务。

## 官方 ADS2027 基线

Keysight [2026-10-06 官方公告](https://www.keysight.com/zz/en/about/newsroom/catalog/news-release.2026.1006_pr26-095-keysight-advances-its-design-software-with-agentic-ai.html) 明确 ADS2027 已提供 MCP、Python 宏记录和脚本生成；[官方连接演示](https://www.keysight.com/kr/ko/assets/6126-1433/how-to-videos/Connect-AI-to-Keysight-ADS-for-RF-Designs.html) 展示 AI 接入。官方软件受 Keysight 许可约束，不重新分发。

本会话可发现官方工具 list_sessions、connect_session、disconnect_session、start_local_session、execute_python、workspace_summary、search_docs、get_docs；只读 get_docs 已返回 Python API 模块与例程目录。接口可见和文档可读不代表任意求解任务已验证。优先复用本机官方 MCP 做会话与版本匹配文档入口，再增加工程隔离、有限预算、持久回执和数值验收。任意 Python 在 ADS 进程中具有该进程权限，不能描述为安全沙箱。

## 实际代码观察与取舍

### Lavaxzyh/ads-mcp

读取 [ads_engine.py](https://github.com/Lavaxzyh/ads-mcp/blob/2879203568fb7fa9a20da30b7208b866c8d13400/ads_engine.py)、[mcp_server.py](https://github.com/Lavaxzyh/ads-mcp/blob/2879203568fb7fa9a20da30b7208b866c8d13400/mcp_server.py)、[ads_gui.py](https://github.com/Lavaxzyh/ads-mcp/blob/2879203568fb7fa9a20da30b7208b866c8d13400/ads_gui.py) 和 README。DE 用 RLock 串行；Python stdout 转 stderr；GUI 固定函数核对 workspace 后通过官方桥执行。README 限定实测 Windows ADS2027/Python3.14，并区分 emModel、理想器件和厂商模型覆盖。

采纳原生 OA→保存 Setup→Momentum→emModel→电路联合仿真分层、完整复数同网格比较、超时后不重复派发、stdout 纯协议。不照搬默认版本路径、ABI 假设、create_workspace 打开既有目录语义、持 DE 锁的同步求解。新服务应拒绝覆盖并隔离工程；端口文件存在不能替代实际引脚、参考面和无源性验收。

### cottman99/ads-agent-bridge

读取 [session_manager.py](https://github.com/cottman99/ads-agent-bridge/blob/5c3c26d22c55a1ad991c73e80228a770f5390b53/src/ads_agent_bridge/session_manager.py)、[momentum.py](https://github.com/cottman99/ads-agent-bridge/blob/5c3c26d22c55a1ad991c73e80228a770f5390b53/src/ads_agent_bridge/momentum.py)、config.py 和嵌入桥入口。使用 nonce/PID/slot 所有权、生命周期文件锁、原子配置；Momentum 独立副本比较源指纹，检查完整有限 N 端口 CITI。

采纳用户/服务会话分离、不强杀用户 ADS、多安装显式选择、一次性 smoke、源哈希和非覆盖输出。其 Momentum finally 清理未发布 staging，本项目应另保留失败日志和已付成本。有限矩阵完整性不能替代网格收敛、无源性和热噪声验收。完整 Runtime/UI 管理不是本次抽取的必要依赖。

### geer1895/rfauto

读取 [ads_mcp_channel.py](https://github.com/geer1895/rfauto/blob/7a5801b46a68a6fe0ca252805bf1fff294fcefb4/src/rfauto/linkage/ads_mcp_channel.py)、[mcp_tools/ads.py](https://github.com/geer1895/rfauto/blob/7a5801b46a68a6fe0ca252805bf1fff294fcefb4/src/rfauto/mcp_tools/ads.py) 及目录。官方 MCP 是可选通道；工具调用业务服务，异常使用明确封装，结果注释依赖实际产物。

采纳服务/MCP 分离、确定性物理数值、任务/资源/预检独立、能力与证据分级。不为 ADS 引入全部多引擎工具，不用 README 数量代替验证。本仓库不复制 GPL 代码；未来实际派生时须履行 GPL 分发义务，不能改头部为 MIT。

### Zhang2070466526/edi-grpc-mcp

读取 [simulation.py](https://github.com/Zhang2070466526/edi-grpc-mcp/blob/78c788d9064ad98403a13f0722b02ca6dd845ca9/servers/eda/simulation.py)、[settings.py](https://github.com/Zhang2070466526/edi-grpc-mcp/blob/78c788d9064ad98403a13f0722b02ca6dd845ca9/servers/settings.py) 及 proto 目录。异步状态区分 completed、outcome_known、task_success，保留增量日志；默认 loopback。EDA 路径是 .epp 和特定 gRPC 服务，不等同于任意 ADS OA 工作空间接口。

采纳 task_id、传输/求解结束/求解成功区分、未知终态如实报告。不沿用内存任务两小时 TTL、固定单 worker 或 EDI 服务依赖；持久回执和失败成本不因服务重启丢失，工程锁/许可证决定并发，远程监听不默认开启。

### why250/ads-help-mcp

读取 [server.py](https://github.com/why250/ads-help-mcp/blob/023067074e01dbec2b76d63efe2b25818f99d8aa/src/ads_help_mcp/server.py)、release 配置及 README。工具提供版本列表、文档/AEL 搜索、版本比较；缺索引返回错误，不跨版本降级。

借鉴精确版本、可追溯来源、不静默降级思想。本机授权文档就地检索，不提交厂商语料、向量库或模型。优先官方文档。由于未发现 license，不复制源码、索引和语料。

### a11s11/keysight-ads-simulation-skill

读取 [run_momentum.py](https://github.com/a11s11/keysight-ads-simulation-skill/blob/8088ebf4218b9ae278a7b78a3b4426abf65d77b3/keysight-ads-simulation/scripts/run_momentum.py) 和 README。验证 RFPro layout/substrate 绑定、实际端口映射与阻抗；分 flow/release；调用者声称收敛不能自动变成已验证。

采纳数据优先、分离仿真/指标/布局/EM/DRC/制造验收、模型与源绑定。不把特定版本 RFPro 脚本当作跨版本已验证功能；技能规范不能替代可运行 MCP。

### 相邻的 GUI 与研究流程

[Jchase0126/ads-agent](https://github.com/Jchase0126/ads-agent/tree/11a44a54a2c04da02cacc8f3985c2f6fc100f8fd) README/目录将短 ADS 主线程操作、独立数值 worker、仿真子进程分开，且区分请求取消和实际停止。可作架构对照；没有复制许可，不取用实现。

[AI_RFIC_workflow](https://github.com/cottman99/AI_RFIC_workflow/tree/561f251b41c9341ff29c4374001aac26d3093bbc) 是像素几何→RFPro→数据集→代理模型流程，相邻于原项目背景，并非现成 MCP。保持研究数据、预测和精确源 EM 证据区别，不把受非商业限制源码、版图数据或权重装入通用仓库。[wirefree7/ADS_Automation](https://github.com/wirefree7/ADS_Automation) 亦出现在 API 检索，定位为自动化片段；未发现许可证，不纳入实现来源。

## 落地规则

1. 独立接口包含配置、能力探测、类型化操作、异步求解、原始结果和审计。AI 决定实验，执行层固定单位、温度、端口、测量语义和预算。
2. 不打包 ADS 软件、PDK、许可证、厂商模型、帮助语料和研究数据；合法本机安装是原生执行前提。未安装时仍可离线检查协议/配置。
3. 默认 stdio 本机运行，不隐式开放网络或任意代码执行。进程隔离用于回收和资源控制，不宣称安全沙箱。
4. 唯一工程/任务目录、日志和输入哈希；失败保留；工程锁、预算和许可证不能被重试绕过。
5. 离线测试、ADS smoke、原生仿真、EM 物理验收分开。缺当前原生证据就是未验证，第三方演示和测试通过不能替代。

此文记录参考取舍；交付能力和实测验证以本仓库 README、测试与运行回执为准。
