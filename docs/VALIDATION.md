# 验证记录

日期：2026-11-10，Asia/Shanghai。本记录属于本独立包，不继承原 MMIC 项目的物理资格。

## 本机实测

- Windows，Python 3.13.9，MCP Python SDK 1.30.0，Keysight ADS 2027（原生 DE version 返回 650）。
- 环境检测：36 物理核 / 72 逻辑核，约255 GiB RAM，RTX A4000 16 GB。未宣称 ADS/Momentum 使用 GPU。
- 默认 worker=1；本次只做一次小电路验收，没有用占用率为理由增加实验。未探测许可证并发容量或 Momentum 最优线程数。

| 检查 | 实测结果 | 证明范围 |
| --- | --- | --- |
| 离线测试 | 50 passed，1 skipped | 配置、路径、模型、锁、容量、失败归档、Windows进程子树取消、原生API mock、真实MCP协议 |
| 跳过项 | Windows符号链接创建权限不足 | 该环境没有执行符号链接fixture；链接拒绝实现仍在 |
| MCP stdio | initialize → tools/list → ads_doctor 成功，18工具 | 本服务协议可用；默认无任意Python |
| 官方 MCP | 发现8个真实工具 | 本机官方接口和文档入口可用；发现不取原生会话许可证 |
| 原生 OA | 新建工作区、schematic保存、独立新会话重新读取成功 | 本包可操作真实原生对象 |
| 理想电阻 SP | 独立job，1次求解，1/1.5/2 GHz三点，全4个S元素 | 原理图→网表→worker→CircuitSimulator→dataset→CSV解析链路 |
| 数值对照 | max abs error = 5.551115123125783e-17 | 50Ω串联R接两50Ω端口，S11=S22=1/3，S12=S21=2/3 |
| 数据有效性 | SP1.SP，3行，21个数值，0个非有限值 | 此理想电路的数据完整性 |
| 构建/独立安装 | wheel构建、独立target导入18工具、bootstrap.ps1全程、pip check均通过 | 不依赖原MMIC工程；不证明其他求解任务 |

原生电路验收job ID：77df3e4a4b0c46738706db7972d7fdca。
完整原始证据保留于本地未提交的 projects/.ads-mcp/jobs/<job_id>/、operations/ 和 .ads-mcp/circuit-smoke.json；不把厂商工程或二进制数据上传仓库。

## 明确未验证

- 没有运行新 Momentum/EM；其固定命令、hash、输入隔离、线程保持与错误处理经过离线mock检查。
- HB、DC、Noise可执行已有控制器；本次未分别验证标准测量台、主动PDK模型或noise closure。
- Layout矩形/多边形/路径与point pin接口已提取；未作新的版图DRC、有限宽度EM pin、stack、via、TFR/airbridge、无源性/热噪声或制造资格验收。
- 参数写入后尚未自动执行PDK callback或证明PCell几何同步。
- 未在Linux执行原生验收；Linux分支不能视为已验证。
- 未做多worker许可/吞吐测试，不据此提高默认并发。

## 本次失败与修复

旧的ambient HPEESOF_DIR曾覆盖显式ADS2027配置，能力发现因此失败，没有启动原生求解。现已修复优先级并增加回归测试。初次Unicode分页测试误把Windows写入的CRLF与原始LF比较，已改为明确原始字节fixture；CLI输出固定UTF-8。沙箱账户目录ACL导致的测试启动失败没有转化成RF结果，最终在本机账户范围复验。

所有这些记录与唯一一次电路求解分开；没有自动重试、EM、优化或训练。

## 0.1.1 发布增补

协议自检增加45秒总超时，Windows reparse检测统一使用lstat属性以兼容Python3.11，并增加三项回归检查；最终50通过、1跳过。已重新安装0.1.1并通过真实MCP协议往返。上述一次SP原生回执来自0.1.0；0.1.1修改不涉及电路或求解器参数，没有重跑或追加已付仿真。本次修复并不新授予RF或物理资格。
