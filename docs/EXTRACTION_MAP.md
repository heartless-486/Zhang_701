# 原项目能力提取映射

来源项目：用户授权的 Agent_2026_demo。历史实验、GUI默认策略、已验证物理设计和累计预算保持原样，本仓库不导入其结论。

| 原项目来源 | 提取内容 | 独立实现/剔除内容 |
| --- | --- | --- |
| execution/v1_6/modules/schematic/vendor/rfic_agent/src/rfic_agent/execution/tools.py | 官方MCP stdio、版本文档、原生会话调用 | runtime.py；剔除模型API、文献记忆、GUI上下文、固定根路径 |
| 同目录 sdk.py | 工作区/库、DesignBuilder、实例/参数/连线、layout形状、数据集、CircuitSimulator | native.py；剔除JFS默认PDK、特定LNA、CNN/RL/数值目标 |
| rfic_agent/mcp_server.py | 结构化工具、协议stdout隔离、失败可见 | 使用标准MCP Python SDK，不复制手工JSON-RPC实现 |
| ads_standard_execution/service.py 与 mcp_server.py | inventory/plan/prepare/preflight/run 分层和资格区别 | 保留文档化工程原则；固定参考台架和验证状态不迁移为通用已验证能力 |
| tools/owned_ads_jobs.py | 任务拥有权、独立目录、日志、有限执行 | jobs.py/job_worker.py；不按进程名称终止全部ADS |
| execution/v1_6/modules/pixel/app/momentum_worker.py、integration/component_momentum.py、peripheral_em/momentum_r03.py | 已生成Momentum输入、adsMomWrapper与siodump调用 | 仅复制显式哈希绑定输入后运行；不携带JFS堆叠或版图 |
| execution/v1_6/modules/schematic/vendor/rfic_agent/src/rfic_agent/core/evidence.py 等 | 原始返回先保存、来源hash、验证范围 | 本包精简证据记录；不把文件存在当物理资格 |

明确不迁移：优化器/PPO、训练课程、模型权重、PDK二进制/源码、供应商文档、冻结搜索运行、GUI决策默认值、原项目仿真输出及特定20–40 GHz目标。新部署也不需要调用LLM API。

设计选择：元数据检查、MCP连接、原生对象读写、求解完成、数值有效性、噪声/物理资格分别报告。任何已知失败都保留，未测能力保持未知。
