# AI 可执行部署流程

## 前提

- Windows + Python 3.11–3.13。主机venv与ADS内部Python解释器分离，不把Keysight模块装进系统Python。当前原生适配以 ADS 2027 为基线；Linux 的 Python 路径和官方 MCP 可由环境探测，但本次没有 Linux 原生验收。
- 合法安装 ADS，安装目录包含 bin/ads-mcp.exe。较老 ADS 若没有官方 MCP，本包应明确报能力缺失，不伪造就绪状态。
- 使用者自行提供电路/EM所需许可证及 PDK。工程数据和厂商内容不上传 GitHub。

## 步骤

1. 克隆仓库；读取 AGENTS.md。若目录已有文件，不覆盖。
2. 运行 scripts/bootstrap.ps1，传入 AdsHome 和 ProjectRoot。未传 AdsHome 时仅从 ADS_HOME/HPEESOF_DIR 获取；不要猜多个安装版本中的一个。
3. 脚本创建独立 .venv，安装本仓库，生成本地 ads-mcp.toml。已存在的配置保留，使用前人工或AI读取确认路径。
4. doctor 只是环境检查，不取许可证。检查 ok / official_mcp 和路径，然后运行 capabilities，确认当前安装真实提供 start_local_session/execute_python/get_docs。
5. scripts/check_mcp.py 使用标准 MCP SDK 启动本服务，完成 initialize/tools/list/ads_doctor 往返。这里不会启动原生 ADS 会话。
6. 从 .ads-mcp/client-config.json 获取标准 stdio 配置。把生成的服务项合并到所用 AI 客户端支持的 MCP 配置中，保留其他服务。客户端 schema 名称以该客户端为准。
7. 按需要单独运行全新工作区原生验收。成功保存/重新打开对象与仿真/EM资格分别记录。

## 手动部署与 Linux

通用命令是 python -m pip install .，然后 python -m ads_mcp --config /absolute/path/ads-mcp.toml serve。
Linux 可使用 python3 -m venv .venv 及 .venv/bin/python，配置本机 ADS_HOME；native Momentum 按平台选择 wrapper/siodump 名称，但当前只有 Windows 原生运行证据，不能声称 Linux Momentum 已验收。

## 配置

优先使用绝对路径。工程文件只能在 project_root 范围，内部符号链接或目录联接也不可越界。state_dir 存放日志、快照、原始返回和作业记录；建议放在 project_root/.ads-mcp 以便统一归档。它不应位于被编辑的 ADS 工作区内部。

max_workers 是用户配置的许可证/资源上限，不是硬件核心数。默认1；独立工作区才能并行。tool_timeout_seconds 既用于官方工具请求，也约束允许的 Momentum 超时。长任务需先设置合适的有限值。

allow_execute_python 默认false。开启后有完整本机 ADS Python 权限，可能执行本机文件与进程操作；typed schema、路径检查及代码静态检查不能构成操作系统安全隔离。仅给受信任的本机 AI/操作者使用。

MCP stdio 服务不监听公网，无额外 HTTP token，无需模型 API key。程序不会复制模型服务密钥给官方 ADS 子进程；ADS所需 license 环境保留。显式配置 ads_home 优先于通用 ADS_HOME/HPEESOF_DIR 回退；ADS_MCP_ADS_HOME 是专用的显式环境覆盖。

## 最小原生工作流

通过结构化工具：

1. ads_create_workspace(workspace="smoke_wrk", library="smoke_lib")
2. ads_create_design(workspace="smoke_wrk", cellview="smoke_lib:test:schematic", kind="schematic")
3. 从官方文档确认当前器件的 master LCV、参数和端口索引，用 ads_edit_design 添加实际器件和连线。
4. ads_inspect_design 重新读取保存对象；ads_generate_netlist 保存实际网表。
5. 用户要求电路仿真时提交一次 ads_simulate，保留返回的 job_id，轮询 ads_job_status。
6. 对返回 dataset 路径调用 ads_read_dataset。ads_read_dataset 支持 project_root 与 state_dir 两个只读根，包含外置 state 中的结果；工程文本工具仍只读取 project_root。

SP、DC、HB、Noise 的设置由实际原生仿真控制器决定。工具不会凭一个 analysis 字符串声称已生成正确测量台架，也不会绕过现有模型警告。

## Momentum

先在已验证的 ADS 流程生成完整 proj 输入。输入必须包含原生 stack、layout、pin、mesh/solver所需文件；来源清单与 expected hashes 一致。ads_run_momentum 仅在副本运行固定的 adsMomWrapper -O -3D proj proj，并读取原生 SIO 结果。输入输出哈希、命令、退出码和日志留在作业目录。

不要从网表/布局截图推断所有非匹配无源器件已包含EM。源接触点、实际有限宽度pin、参考面、TFR/桥层/Pad/via和重复模型删除均需原生审计。原始passivity和noise-C PSD不通过时，不得clip后称为测量。

## 故障与发布

- 安装缺失：修正 ads_home；不自动下载厂商软件。
- 许可证/模拟器错误：保留日志和本次成本，不自动重试。
- 客户端超时：先看job和原始日志，完成状态未知时不得重复发起。
- 工程锁：等待拥有者完成或用户明确处理；不删锁、不绕锁。
- 取消：只允许当前服务器持有进程句柄的worker。Windows worker在启动ADS前加入KILL_ON_JOB_CLOSE Job Object，取消终止自有headless子树；不触碰用户GUI。Linux按自有进程组发送SIGTERM，脱离进程组的子代可能存活。保留partial/unqualified结果，不能报告成可安全续算。
- 发布前运行测试、协议检查和构建检查，排除 .venv、PDK、工程数据、密钥和绝对本机路径。正常git push，不用force。

复现本轮依赖可使用 python -m pip install -c constraints-tested.txt ".[test]"；约束记录本轮Windows/Python3.13环境，其他平台以CI和实际探测为准。
原生无求解检查：python scripts/native_smoke.py --config ads-mcp.toml。显式一次电路验收：python scripts/circuit_smoke.py --config ads-mcp.toml --run；它创建新的理想电阻工作区并仅提交一次SP，失败不重试。
