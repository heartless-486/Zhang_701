# Zhang ADS MCP

把 ADS 操作能力从 MMIC Agent 中独立出来，供支持 MCP stdio 的 AI 客户端使用。

服务调用本机已安装的 **Keysight 官方 ADS MCP**，并增加结构化工程操作、作业隔离、输入哈希、失败记录和部署工具。无需安装原 MMIC/RL 项目。

> 需要用户合法安装的 ADS、相应仿真许可证及用户自己的 PDK。本仓库不分发这些内容。原生支持以运行时能力探测为准；当前实现优先 ADS 2027 / Windows。

## 让 AI 部署

把下面这段交给 AI，并提供你自己的安装路径：

> 请部署此仓库。先读 AGENTS.md 和 docs/DEPLOY_AI.md，运行 scripts/bootstrap.ps1，填写 ADS 和工程目录，执行 doctor、MCP 协议自检及 capabilities，生成客户端配置。保留已有配置；原生检查使用全新的临时工作区。没有明确要求时不要运行 EM、参数搜索或训练。

Windows PowerShell：

```powershell
git clone https://github.com/heartless-486/Zhang_701.git
cd Zhang_701
.\scripts\bootstrap.ps1 -AdsHome 'D:\Program Files\Keysight\ADS2027' -ProjectRoot 'D:\ADS_Projects'
```

脚本创建独立 `.venv`、本地 `ads-mcp.toml` 和 `.ads-mcp/client-config.json`，不改系统 Python 或 AI 客户端已有配置。使用客户端配置中的绝对路径注册 MCP 服务。部署不需要模型 API key。

手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[test]"
Copy-Item examples/ads-mcp.example.toml ads-mcp.toml
# 编辑 ads_home、project_root 和 state_dir
.\.venv\Scripts\python.exe -m ads_mcp --config ads-mcp.toml doctor
.\.venv\Scripts\python.exe scripts/check_mcp.py --config ads-mcp.toml
.\.venv\Scripts\python.exe -m ads_mcp --config ads-mcp.toml capabilities
```

## 提取的能力

| 能力 | MCP 工具 | 实际边界 |
| --- | --- | --- |
| 环境与 API 检查 | ads_doctor / ads_capabilities | 文件存在、MCP可发现、许可证有效分别判断 |
| 版本匹配文档 | ads_get_docs / ads_search_docs | 从用户安装的 ADS 读取，不分发厂商文档 |
| 工作区与 PDK | ads_create_workspace | 新建工作区，外部 PDK 只读引用 |
| 原理图和版图 | ads_create_design / ads_edit_design | 器件、参数、按正终端号连线、点pin、矩形、多边形、路径；编辑前快照 |
| 原生读取 | ads_inspect_design / ads_generate_netlist | 实际 OA 读取与网表哈希，不以描述代替原生对象 |
| 电路仿真 | ads_simulate | 执行设计已有的 SP/DC/AC/HB/Noise 控制器 |
| Momentum | ads_run_momentum | 哈希绑定的、已预处理 proj 输入副本；不自动猜堆叠和端口 |
| 数据与日志 | ads_read_dataset / ads_list_files / ads_read_text | 原生数据块、CSV及完整复数值，分页读取 |
| 作业 | ads_job_status / ads_list_jobs / ads_cancel_job | 独立进程/目录/日志，容量限制、工程锁、失败不自动重试 |

参数写入尚未自动执行PDK callback或验证PCell几何；点pin不能替代有限宽EM端口。

默认不暴露任意 Python。受信任的本机操作者可设置 `allow_execute_python=true`；此时增加 `ads_execute_python`，它拥有 ADS Python 的进程权限，**不是系统沙箱**。

## 运行方式

```text
AI 客户端 -- MCP stdio --> 本服务
                         ├── 配置 / 文档发现 / 输入校验
                         ├── 结构化操作 --> 独立官方 ADS MCP --> 原生 ADS API
                         └── 长任务 --> 独立 worker / 工作区锁 / 证据目录
```

默认 `max_workers=1`。提高并发前核对许可证、CPU、内存和实测吞吐。不会把 Python 数值测试的低线程设置强制传给 Momentum；线程环境会进入作业记录。不同作业不能同时修改同一工作区，任何 ADS/OA 原有锁都不删除。

断开客户端不等于仿真成功或可安全重跑。保存的 job ID、日志和原始返回值是恢复检查入口；取消是有代价的中止，不是求解器检查点。

## 验证与来源

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_mcp.py --config ads-mcp.toml
```

已通过50项离线测试与真实MCP往返，并完成一次三频点理想电阻原生SP验收，完整S矩阵最大绝对误差5.56e-17以内。EM、HB与主动PDK噪声资格另行验证。

详见 [部署说明](docs/DEPLOY_AI.md)、[能力与提取映射](docs/EXTRACTION_MAP.md)、[公开项目对标](docs/UPSTREAM_REVIEW.md)、[验证记录](docs/VALIDATION.md)。

已有控制器能运行不等于标准 HB/Noise 测量已验证，Momentum 求解返回也不等于无源性、噪声闭合或流片资格。本仓库不继承原项目的 MMIC 设计结论或研究授权。

新实现及来自用户授权项目的通用提取代码采用 MIT 许可；不包含第三方项目源码、PDK、Keysight程序或文档。依赖继续遵守各自许可证。
