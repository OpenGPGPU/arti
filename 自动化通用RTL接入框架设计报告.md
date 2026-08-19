# 自动化通用 RTL 接入框架——完整技术设计报告

## 摘要

本报告面向"任意 RTL IP 核一键接入 QEMU 虚拟平台"这一工程目标，给出自动化通用 RTL 接入框架的完整技术设计。框架由三个核心子系统构成：**子系统一**（RTL 接口签名分析与总线协议推断器）对用户提供的 Verilog/SystemVerilog/VHDL 顶层进行 AST 级解析，通过"命名模式匹配 + 时序特征匹配 + 协议完整性验证"三级引擎自动推断 AXI-Lite、AXI4、AXI-Stream、AHB、APB 等总线协议类型并输出端口映射与置信度；**子系统二**（协议无关的 TLM↔RTL 桥接代码生成器）基于层级化双桥接模型（TLM 事务处理器 + 协议级时序发生器），利用模板引擎自动生成 SystemC 桥接代码，完成 QEMU 事务到 RTL 信号级握手的转换；**子系统三**（一体化构建与运行系统）自动生成完整工程目录、CMake 构建脚本与一键启动脚本，打通"配置填写→代码生成→编译→协同仿真→自动比对"全流程。

本报告在初稿基础上完成了全面重写与深化：修正了初稿中 Verilator 信号绑定方式、QEMU 启动参数、置信度归一化公式等技术性错误；补充了 QEMU remote-port 时间同步机制、中断反向通路、协议必备信号完备集、验证指标、性能优化策略、风险清单与实施路线图等缺失内容。框架的工程可行性建立在成熟的开源生态之上：QEMU 与外部仿真器之间通过 **remote-port** 机制以 socket 与共享内存方式交换事务并同步时间[^2^]，SystemC 侧由 libsystemctlm-soc 提供标准 TLM-2.0 封装[^4^]，RTL 侧由 Verilator 以 `--sc` 模式编译为可直接挂接 SystemC 网表的 SC_MODULE[^12^]。

---

## 1. 设计目标与总体架构

### 1.1 设计目标

软硬件协同验证的长期痛点在于：每接入一个新的 RTL IP 核，工程师都需要手工编写 QEMU 设备模型、SystemC 桥接层与测试环境，重复劳动量大且容易出错。本框架的核心设计目标是把这一过程中**可自动化的 80% 工作收归工具完成**，只把确实需要人工决策的部分（协议确认、端口映射修正、性能参数调优）以配置文件形式暴露给用户。具体而言，框架需要满足四项目标：其一，**协议自动识别**——用户无需声明 RTL 使用何种总线，框架从端口命名与时序行为中自动推断，并在置信度不足时给出候选排序供人工确认；其二，**桥接代码自动生成**——桥接层代码完全由模板渲染产出，用户不直接编写任何 SystemC/C++ 代码；其三，**一键构建运行**——从填写配置文件到看到协同仿真比对报告，只需要执行一条命令；其四，**可降级、可覆盖**——任何自动推断结果都可以通过 `manual_mapping` 等配置项人工覆盖，保证框架在推断失败时依然可用。

需要明确框架的适用边界。框架面向的是**具有标准总线接口的从设备型（slave/peripheral）RTL IP 核**——寄存器型加速器、流式数据处理器、简单外设等，这类 IP 占据了协同仿真需求的主体。对于带有主设备接口（主动发起 DMA 的 master）、多层级互联结构或强模拟混合信号行为的 RTL，框架提供模板骨架但不承诺全自动接入，此类场景在第七章风险与限制中进一步讨论。

### 1.2 总体架构

框架总体架构如图 1 所示，呈"推断—生成—运行"三级流水线。三个子系统之间通过两个良定义的中间产物解耦：子系统一向子系统二输出**协议推断结果**（协议类型、端口→总线信号映射表、配置参数、置信度），子系统二向子系统三输出**完整的桥接代码库**（SystemC 源文件、QEMU 侧设备存根、构建脚本）。这种解耦带来两个工程收益：一方面，每个子系统可以独立迭代——例如为推断器新增一种协议支持时，只需扩展规则库与模板，无需改动构建系统；另一方面，中间产物都是人类可读的 YAML/JSON 与 C++ 代码，用户可以在任何一级介入修改，符合"自动化优先、人工可接管"的设计哲学。

![图1 自动化通用 RTL 接入框架总体架构](figs/fig1_overall_architecture.png)

运行时架构采用 QEMU 与 RTL 仿真器**分进程**的组织方式：QEMU 运行客户软件，SystemC+Verilator 进程运行 RTL 仿真，两者之间通过 remote-port 协议通信。这一选择与 Xilinx/AMD 协同仿真方案一致——remote-port 是 QEMU 连接外部仿真环境的底层机制，它基于 socket 与共享内存在仿真器之间传输事务并同步时间[^2^]；libsystemctlm-soc 则把 QEMU 事务序列化/反序列化为 TLM Generic Payload，使 QEMU 在 SystemC 侧看起来就是一个普通的 TLM-2.0 模块[^4^]。分进程架构相比编译进 QEMU 的设备模型方案，具有侵入性低、调试方便（两侧可分别用 GDB/波形工具调试）、RTL 修改无需重编 QEMU 等显著优势。

### 1.3 关键技术选型与依据

框架的技术选型以"成熟开源组件优先、避免重复造轮子"为原则，核心技术栈及选型依据如下表所示。

| 技术环节 | 选型 | 选型依据 |
| --- | --- | --- |
| QEMU↔SystemC 通信 | remote-port（libremote-port）+ libsystemctlm-soc | 基于 socket+共享内存的事务协议，原生支持时间同步与中断线（wires），已被 AMD 协同仿真方案与商业工具链采用[^2^][^15^] |
| RTL 仿真引擎 | Verilator（`--sc` 模式） | 编译型周期精确仿真，单线程比解释型仿真器快约两个数量级，且可直接生成 SC_MODULE[^12^][^32^] |
| 事务级建模标准 | SystemC TLM-2.0（LT 风格 + Generic Payload） | 业界标准，与 libsystemctlm-soc 的封装直接兼容[^4^] |
| RTL 解析 | Pyverilog（Verilog）+ slang/Surelog（SystemVerilog 备选） | Pyverilog 提供解析、数据流与控制流分析全套 Python 工具链[^27^]；其仅支持 Verilog-2005，SV 源码需经 sv2v 预处理或换用 slang[^28^] |
| 代码生成 | Jinja2 模板引擎 | 与推断器同为 Python 技术栈，模板可读性好、易于用户自定义扩展 |
| 构建系统 | CMake + Verilator CMake 集成 | 自动处理 Verilator 编译、SystemC 链接与依赖检测 |

一个必须向用户明示的前置约束是：**remote-port 并非上游 QEMU 的内建特性**，它由 Xilinx 维护的 QEMU 分叉提供，且需要通过 `-machine-path`、`-sync-quantum`、`-icount` 等专用命令行选项以及协仿真设备树（`-hw-dtb`）启用[^2^][^25^]。因此框架的 QEMU 侧支持两种部署形态：优先形态是基于 Xilinx QEMU 分叉（remote-port 开箱可用，但机器类型以 Zynq/Versal 为主）；通用形态是为上游 QEMU 生成一个轻量设备存根（`qemu_device_stub.c`），存根内嵌 remote-port 客户端逻辑，从而把框架扩展到 `virt` 等通用机器类型——这也是初稿目录结构中 `qemu/qemu_device_stub.c` 的设计意图，本报告在 3.2 节与 4.4 节对其生成内容与启动参数做了完整设计。

---

## 2. 子系统一：RTL 接口签名分析与总线协议推断器

### 2.1 语法解析与特征提取

推断器的输入是用户的 RTL 顶层源码，输出是结构化的**端口元数据**与**时序特征**。解析阶段基于 AST（抽象语法树）而非正则文本扫描，因为只有 AST 能可靠地区分端口声明、内部信号、参数化宽度表达式与 always 块中的信号引用关系。对于 Verilog-2005 源码，Pyverilog 是理想的解析底座：它提供从解析器（vparser）、数据流分析（dataflow）到控制流分析（controlflow）的完整工具链，其中控制流分析器可以识别每个信号在何种条件下被激活，这恰好是 2.3 节时序特征匹配所需的能力[^27^]。需要注意的是 Pyverilog 依赖 Icarus Verilog 做预处理（`iverilog -E`），且仅支持 Verilog-2005 语法[^27^]；对于 SystemVerilog 源码，框架提供两条路径——轻量路径是先经 sv2v 转换为 Verilog 再解析[^28^]，重量路径是直接接入 slang/Surelog 这类完整 SV 前端。框架默认采用"Pyverilog + sv2v"的轻量路径，将 slang 作为可选后端，以控制部署依赖的复杂度。

解析阶段的产物定义为如下数据结构（示意）：

```python
@dataclass
class Port:
    name: str            # 端口名，如 "S_AXI_AWADDR"
    direction: str       # input / output / inout
    width: int           # 位宽（参数化表达式在解析期求值）
    clock: str | None    # 关联时钟域（由 2.3 节时钟关联分析填充）

@dataclass
class ModuleSignature:
    module_name: str
    ports: list[Port]
    clocks: list[str]          # 识别出的全部时钟端口
    resets: list[dict]         # 复位端口及其有效极性
    timing_features: list[str] # 时序特征标签，见 2.3
```

端口元数据的提取本身是直接的；真正需要设计的是**时钟/复位关联分析**——即判断每个总线端口属于哪个时钟域。实现思路是：从 always 块的敏感表收集全部时钟候选（`posedge/negedge` 事件信号），再对数据流图做扇入分析，将每个端口寄存器化逻辑所属的 always 块时钟标记为该端口的关联时钟。这一信息是 3.8 节时钟域桥接策略自动选择的输入。复位极性则通过复位信号在敏感表中的边沿方向（`posedge rst` → 高有效，`negedge rst_n` → 低有效）结合命名启发式（`_n`/`_b` 后缀）双重判定。

### 2.2 命名模式匹配规则库

命名模式匹配是第一级推断，建立在"总线信号命名具有强约定"这一经验事实上。规则库以协议为单位组织为可扩展的正则集合，每种协议标注**必备信号**与**可选信号**两类。下表给出五种支持协议的规则库概要（规则针对端口名大小写归一化与常见前缀剥离后匹配）：

| 协议 | 必备命名模式（核心组） | 可选命名模式 | 区分性特征 |
| --- | --- | --- | --- |
| AXI-Lite | `AWADDR/AWVALID/AWREADY`、`WDATA/WVALID/WREADY`、`BRESP/BVALID/BREADY`、`ARADDR/ARVALID/ARREADY`、`RDATA/RRESP/RVALID/RREADY` | `AWPROT/WSTRB/ARPROT` | 五通道齐全但**无突发信号**（无 `AWLEN/AWBURST`） |
| AXI4（Full） | 在 AXI-Lite 基础上另有 `AWLEN/AWSIZE/AWBURST`、`WLAST`、`ARLEN/ARSIZE/ARBURST`、`RLAST` | `AWID/BID/ARID/RID`、`AWQOS/AWREGION` | 存在突发控制信号是区别于 AXI-Lite 的决定性证据 |
| AXI-Stream | `TDATA/TVALID/TREADY` | `TLAST/TKEEP/TSTRB/TID/TDEST/TUSER` | 无地址通道；`TLAST` 标记包边界 |
| AHB | `HADDR/HWDATA/HRDATA/HWRITE/HTRANS/HREADY` | `HRESP/HSIZE/HBURST/HPROT/HSEL` | `HTRANS` 传输类型 + 地址/数据相位重叠 |
| APB | `PADDR/PWDATA/PRDATA/PWRITE/PSEL/PENABLE` | `PREADY/PSLVERR/PPROT/PSTRB` | `PSEL` 与 `PENABLE` 两拍结构独有 |

规则库以独立 YAML 文件存储而非硬编码于 Python 类中，这是相对初稿的一处工程改进：用户遇到厂商自定义命名（如 Xilinx 风格的 `s_axi_awaddr` 小写形式、`S00_AXI_*` 带实例编号形式）时，只需在规则库中追加模式，无需修改推断器代码。匹配前对端口名做统一预处理——剥离实例前缀（如 `S00_`、`m01_`）、大小写归一化、下划线变体折叠——可以显著提升规则库的复用率。

### 2.3 时序特征匹配

当端口命名被缩写、混淆或自定义导致命名匹配无法唯一确定协议时，推断进入第二级：从 always 块中提取**握手行为特征**。时序特征的提取基于 Pyverilog 的控制流分析——它本就能识别"某信号在什么条件组合下被赋值"[^27^]，推断器在此基础上归纳出协议级的行为模式。定义的核心特征标签包括：`VALID_READY_HANDSHAKE`（某输出信号的置位条件中出现另一信号，且两信号呈现 valid/ready 互锁关系，指向 AXI 家族）；`APB_TWO_PHASE`（`PENABLE` 类信号严格滞后于 `PSEL` 类信号一个周期置位，指向 APB）；`AHB_ADDR_DATA_OVERLAP`（写数据寄存器的更新条件引用了上一拍的地址相位信号，指向 AHB 的流水线相位结构）；`BURST_COUNTER`（存在随握手递减/递增的传输计数器并与 `LAST` 类信号联动，指向 AXI4 突发或 AXI-Stream 包）。

时序特征匹配的价值不仅在于命名失效时的兜底，更在于**交叉验证**：当命名模式与时序特征指向同一协议时，置信度显著提升；当两者冲突时（例如端口名像 AXI-Lite 但行为上存在突发计数器），推断器应把冲突本身作为重要信息输出给用户——这通常意味着 RTL 实现了一个非标准的协议子集或超集，自动生成桥接时需要人工确认。初稿中时序分析仅作为打分加分项，本报告将其升级为兼具"加分"与"冲突检测"双重职责。

### 2.4 置信度打分算法

三级匹配的分数汇聚到打分引擎。相对初稿，本报告修正了打分算法的三处缺陷：其一，初稿的置信度公式 `confidence = s_best / Σs` 在所有协议得分均为零时发生除零；其二，命名匹配"按端口×模式双重计数"会导致得分随端口数量线性膨胀，使不同规模 IP 的得分不可比；其三，覆盖率惩罚（`coverage < 0.5` 时得分乘 0.2）是硬阈值跳变，在边界附近行为不稳定。修正后的算法如下：

```python
class ProtocolInferenceEngine:
    W_NAME, W_TIMING = 1.0, 2.0   # 命名匹配权重 / 时序特征权重
    TAU_ACCEPT = 0.60             # 自动接受阈值

    def infer(self, sig: ModuleSignature) -> dict:
        scores = {}
        for proto, rules in RULESET.items():
            # 命名得分：按"信号组命中率"归一，与端口总数解耦
            groups_hit = sum(1 for g in rules.required_groups
                             if any(match(p, g) for p in sig.ports))
            s_name = self.W_NAME * groups_hit / len(rules.required_groups)

            # 时序得分：特征标签→协议映射，命中即加
            s_timing = self.W_TIMING * sum(
                1 for f in sig.timing_features if proto in TIMING_MAP.get(f, []))

            # 覆盖率：软惩罚（线性），覆盖率≥0.8 不惩罚
            coverage = self.signal_coverage(proto, sig.ports)
            penalty = min(1.0, coverage / 0.8)

            scores[proto] = (s_name + s_timing) * penalty

        total = sum(scores.values())
        if total == 0:                      # 修正：除零保护
            return self.fallback_manual(sig)

        best = max(scores, key=scores.get)
        confidence = scores[best] / total   # 归一化到 (0,1]
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        return {
            "protocol": best if confidence >= self.TAU_ACCEPT else None,
            "confidence": round(confidence, 3),
            "candidates": ranked[:3],       # Top-3 候选供人工选择
            "conflicts": self.detect_conflicts(sig),  # 命名/时序冲突
            "port_mapping": self.build_mapping(best, sig),
        }
```

修正后的算法具有三个良好性质：得分与 IP 规模无关（命名分量已按信号组归一）；覆盖率惩罚连续可导，行为平滑；置信度落在 (0,1] 区间且有明确语义——得分最高的协议占全部候选得分的比例。当置信度低于阈值 τ（默认 0.60）时，框架不给出单一结论，而是输出 Top-3 候选及各自得分，引导用户在配置文件中显式指定 `bridge.protocol`（见 4.2 节），实现"自动推断 + 人工确认"的协同。

### 2.5 协议必备信号完备集

协议完整性验证依赖每种协议的**必备信号完备集**（REQUIRED_SET），它同时服务于打分算法的覆盖率计算和桥接代码生成时的信号健全性检查。下表给出框架内置的完备集定义：

| 协议 | 必备信号（缺失即无法生成桥接） | 可选信号（缺失时使用默认值） |
| --- | --- | --- |
| AXI-Lite | AWADDR、AWVALID、AWREADY、WDATA、WVALID、WREADY、BRESP、BVALID、BREADY、ARADDR、ARVALID、ARREADY、RDATA、RRESP、RVALID、RREADY、ACLK、ARESETn | WSTRB（默认全 1）、AWPROT/ARPROT（默认 3'b000）、BRESP/RRESP 在 RTL 恒 OKAY 时可由桥接层补全 |
| AXI4 | AXI-Lite 全部 + AWLEN、AWSIZE、AWBURST、WLAST、ARLEN、ARSIZE、ARBURST、RLAST | ID 系列（默认 0）、AWQOS、AWREGION、RRESP |
| AXI-Stream | TDATA、TVALID、TREADY、ACLK、ARESETn | TLAST（无 TLAST 时按固定包长处理）、TKEEP（默认全 1）、TID/TDEST/TUSER |
| AHB | HADDR、HWDATA、HRDATA、HWRITE、HTRANS、HREADY、HCLK、HRESETn | HSEL（单片选时桥接层常量驱动）、HRESP、HSIZE、HBURST |
| APB | PADDR、PWDATA、PRDATA、PWRITE、PSEL、PENABLE、PCLK、PRESETn | PREADY（无时桥接层一拍完成）、PSLVERR |

完备集的意义在于为"推断成功"给出可判定的工程标准：命名匹配与时序特征给出的是概率性结论，而必备信号的存在性是硬性前提。例如某 RTL 只实现了 AXI-Lite 的写通道（无 AR/R 通道），完备集检查会立即发现读通道缺失，框架此时不会静默生成一个读操作永远超时的桥接，而是降级为"只写设备"模板并向用户明确提示。

### 2.6 边界处理与降级策略

推断器面对现实世界的 RTL 必须假设输入是不完美的，框架定义了四级降级路径。**第一级，部分端口未识别**：未匹配任何规则的端口标记为 `UNKNOWN` 并原样保留在映射表中，生成代码时为其预留用户自定义处理的钩子函数，同时在报告中逐一列出。**第二级，多协议混合**：一个顶层同时暴露 AXI-Lite 控制口与 AXI-Stream 数据口是加速器的典型形态，推断器按"接口组"聚类端口（依据端口前缀分组 + 时钟域分组），对每组独立推断并输出多组结果，桥接生成器为每组生成独立桥接实例。**第三级，置信度不足**：输出 Top-3 候选，等待用户在 `config.yaml` 中显式指定。**第四级，完全无法推断**：回退到"手工模式"——生成仅含信号声明与仿真骨架的模板工程，协议时序部分留空由用户填写，框架仍提供构建系统与运行脚本，保证用户在最坏情况下也能获得子系统三的全部价值。

四级降级路径共通的设计原则是**永不静默失败**：每一次降级都在生成的工程根目录写入 `inference_report.json` 与人类可读的 `inference_report.md`，记录推断依据（命中了哪些规则、覆盖率多少、哪些端口未识别），使调试自动推断行为本身成为一件可复盘的事。

![图2 总线协议推断器的多级匹配引擎](figs/fig2_inference_engine.png)

---

## 3. 子系统二：协议无关的 TLM↔RTL 桥接代码生成器

### 3.1 层级化双桥接模型

桥接生成器是框架的核心创新所在。其设计难点在于跨越三个抽象层次：QEMU 侧是字节粒度的内存读写请求（"向物理地址 0xA000_0004 写 4 字节"），TLM 层是带地址与数据指针的事务对象，而 RTL 侧是逐周期翻转的信号电平。如果用一个巨型模块直接完成三层跨越，模板将随协议数量爆炸且无法复用。框架因此采用**层级化双桥接模型**（图 3）：**桥接层 1（TLM 事务处理器）**与协议无关，负责事务的接收、地址偏移映射、宽窄数据拆分/合并与响应组装，所有五种协议共用同一份实现；**桥接层 2（协议级时序发生器）**与协议相关，负责把桥接层 1 产出的"原子化 beat 请求"翻译为具体总线的信号级握手序列，每种协议对应一个独立模板。分层之后，新增一种协议支持只需新增一个桥接层 2 模板，桥接层 1 与 QEMU 侧代码完全不动，这正是"协议无关"标题的含义。

双桥接模型在运行时的数据通路如图 3 所示：QEMU 的事务经 remote-port 序列化到达 SystemC 进程，由 remote-port TLM 适配层还原为 TLM-2.0 Generic Payload；桥接层 1 的 `b_transport` 回调将事务拆分为总线宽度的 beat 序列推入请求队列；桥接层 2 的时序线程逐拍消费队列并驱动 Verilator 生成的 SC_MODULE 端口；中断则沿反向通路返回——RTL 的中断输出经 wire 桥接模块转换为 remote-port wires 消息送达 QEMU[^16^]。队列解耦是这一模型的关键：`b_transport` 是阻塞式接口且应尽快返回以维持松散时序（LT）仿真的推进效率，而 RTL 握手需要多个时钟周期，两者之间用 FIFO 队列缓冲，使 TLM 世界的"瞬时事务"与 RTL 世界的"多周期握手"各得其所。

![图3 层级化双桥接模型](figs/fig3_dual_bridge.png)

### 3.2 QEMU 侧集成：remote-port 与时间同步

QEMU 侧集成的机制基础是 remote-port。remote-port 使用 UNIX socket 与共享内存在 QEMU 进程与仿真器进程之间传递事务消息与时间同步消息，QEMU 启动时通过 `-machine-path` 指定一个目录用于创建共享内存文件与命名 socket，SystemC 侧作为客户端连接该目录下形如 `qemu-rport-_cosim@0` 的 socket[^2^][^20^]。libsystemctlm-soc 在 SystemC 侧提供现成封装——`remote-port-tlm-memory-master` 承担 TLM 事务通路、`remote-port-tlm-wires` 承担中断线通路，框架生成的桥接顶层直接实例化这两类模块，而非从裸 socket 协议做起[^4^][^16^]。

时间同步是协同仿真最容易被忽视却最影响正确性的环节。QEMU 与 SystemC 各自维护虚拟时间轴，若不同步，QEMU 侧的软件无法感知 RTL 处理耗时，超时判断、中断时序、性能测量都会失真。remote-port 的同步机制由两个 QEMU 选项控制：`-icount N` 启用虚拟指令计数使 QEMU 时间行为确定化，`-sync-quantum N` 设定 TLM 同步量子（纳秒）——QEMU 每推进一个量子就与 SystemC 侧对齐一次，量子越小精度越高但仿真越慢，且两个仿真器必须使用相同的量子值[^2^][^25^]。AMD 官方给出的起始参考值是：Zynq UltraScale+/Versal 平台 `sync-quantum=1000000`、`icount=1`，Zynq-7000 平台 `sync-quantum=100000`、`icount=7`[^2^]。框架将这些参数纳入 `config.yaml` 的 `advanced` 段（见 4.2 节），默认取 `sync-quantum=100000`，并在 6.2 节给出调优指导。生成工程中的 SystemC 侧通过 `tlm_global_quantum` 设置相同量子值，保证两侧一致。

关于 QEMU 形态的决策已在 1.3 节说明：Xilinx QEMU 分叉自带 remote-port 支持，但面向 Zynq/Versal 机器；若用户使用上游 QEMU 与 `virt` 等通用机器，框架生成一个 `qemu_device_stub.c` 设备存根——一个标准的 QEMU 设备模型，其 MMIO 回调把读写请求转发给内嵌的 remote-port 客户端，中断回调接收 remote-port wires 消息并触发 QEMU IRQ 引脚。存根以 QEMU 模块方式编译挂载，避免侵入 QEMU 核心源码，这一设计与初稿"不强制修改 QEMU"的目标一致，但本报告修正了初稿将其视为纯配置问题的简化认识——设备存根本身需要按 QEMU 版本的设备模型 API 生成，框架模板库中维护了针对 QEMU 8.x/9.x 设备 API 的两套存根模板。

### 3.3 Verilator 集成模式与端口类型映射

子系统二与 RTL 侧的接口完全建立在 Verilator 的 SystemC 输出模式之上，这也是初稿中存在最严重技术错误、本报告重点修正的部分。Verilator 以 `--sc` 模式编译时，生成的模型类 `V<top>` 是一个**标准的 SC_MODULE**，可直接作为实例挂接进 SystemC 网表，其端口自动映射为 `sc_in`/`sc_out` 类型[^12^]。初稿模板中 `m_rtl->{{clk_port}} = m_clk;` 与 `m_rtl->{{name}} = {{signal.name}};` 的写法是错误的：SystemC 端口绑定必须使用 `port(signal)` 的调用语法在 elaboration 阶段完成，赋值运算符既不完成绑定也无法驱动时钟。端口位宽到 C++ 类型的映射遵循固定规则，桥接代码生成器必须据此选择 `sc_signal` 模板参数：

| Verilog 引脚宽度 | `--sc` 模式端口类型 | 生成代码中的信号声明 |
| --- | --- | --- |
| 1 bit | `bool` | `sc_signal<bool>` |
| 2–32 bit | `uint32_t` | `sc_signal<uint32_t>` |
| 33–64 bit | `uint64_t`（默认 `--pins-bv 65`） | `sc_signal<uint64_t>` |
| 65 bit 以上 | `sc_bv<W>` | `sc_signal< sc_bv<W> >` |

类型映射规则中有一个性能相关的细节值得在生成器中利用：Verilator 文档明确指出整型（`uint32_t`/`uint64_t`）仿真最快，`sc_bv` 用得越多性能越差，因此默认将 65 位以内端口都用整型（`--pins-bv 65`），只有更宽端口才落入 `sc_bv`[^1^]。桥接生成器遵循同一原则，并在数据宽度超过 64 位时（如 512 bit AXI 数据总线）自动生成 `sc_bv` 与字节数组之间的打包/解包辅助函数。另需说明，Verilator 生成的模型内部并非纯 SystemC 代码——只有顶层端口走 SystemC 互连，内部是高度优化的 C++，这正是其性能优势的来源[^12^]。实践中 `--sc` 模式对个别 SystemVerilog 特性（如某些 struct 类型输出端口）存在已知兼容性问题[^3^]，框架的应对是在生成前对 RTL 做一次 Verilator lint 预检（`verilator --lint-only --sc`），预检失败时把诊断信息纳入 `inference_report` 并提示用户加包一层纯 Verilog wrapper。

### 3.4 AXI-Lite 桥接模板（修正版）

综合 3.1–3.3 节的设计，修正后的 AXI-Lite 桥接模板核心代码如下。与初稿相比，修正点包括：端口绑定使用 `port(signal)` 语法、时钟经 `sc_clock` 绑定而非赋值、读写通道分线程避免相互阻塞、响应状态根据 BRESP/RRESP 回传 TLM 错误码、增加事务超时看门狗。

```cpp
// 模板文件: axi_lite_target_socket.cpp.j2（修正版，节选）
SC_MODULE(AXILiteBridge_{{module_name}}) {
    // QEMU 方向来的 TLM 事务入口
    tlm_utils::simple_target_socket<AXILiteBridge_{{module_name}}> target_socket;

    // 时钟与复位
    sc_clock        m_clk{"m_clk", {{clk_period_ns}}, SC_NS};
    sc_signal<bool> m_rst_n{"m_rst_n"};

    // 与 RTL 端口一一对应的 SystemC 信号（类型按 3.3 节映射表生成）
    {% for name, sig in port_mapping.items() %}
    sc_signal<{{sig.sc_type}}> sig_{{name}}{"sig_{{name}}"};
    {% endfor %}

    // Verilator --sc 生成的 SC_MODULE
    V{{module_name}}* m_rtl;

    SC_CTOR(AXILiteBridge_{{module_name}}) : target_socket("target") {
        target_socket.register_b_transport(
            this, &AXILiteBridge_{{module_name}}::b_transport);

        m_rtl = new V{{module_name}}("m_rtl");
        // 【修正】端口绑定：port(signal) 语法，elaboration 阶段完成
        m_rtl->{{clk_port}}(m_clk);
        m_rtl->{{rst_port}}(m_rst_n);
        {% for name, sig in port_mapping.items() %}
        m_rtl->{{name}}(sig_{{name}});
        {% endfor %}

        // 读/写通道各起一个时序线程，互不阻塞
        SC_THREAD(write_thread);  sensitive << m_clk.posedge_event();
        SC_THREAD(read_thread);   sensitive << m_clk.posedge_event();
        SC_THREAD(reset_thread);  // 上电复位序列（时长来自 config）
    }

    // 桥接层 1：TLM 事务处理（QEMU→RTL 方向）
    void b_transport(tlm::tlm_generic_payload& trans, sc_time& delay) {
        const uint64_t offset = trans.get_address();            // remote-port 已减去基址
        const unsigned bus_bytes = {{data_width}} / 8;
        const unsigned n_beats   = (trans.get_data_length() + bus_bytes - 1) / bus_bytes;

        for (unsigned i = 0; i < n_beats; ++i) {                // 宽事务自动拆分
            Beat b{offset + i * bus_bytes,
                   trans.get_data_ptr() + i * bus_bytes,
                   std::min<uint64_t>(bus_bytes, trans.get_data_length() - i * bus_bytes)};
            if (trans.get_command() == TLM_WRITE_COMMAND) wr_queue.push(b);
            else                                           rd_queue.push(b);
        }
        // 等待桥接层 2 完成全部 beat 后取响应（含 BRESP/RRESP 错误回传）
        auto status = done_event.wait_result({{timeout_ns}}, SC_NS);
        trans.set_response_status(status.ok ? TLM_OK_RESPONSE
                                            : TLM_GENERIC_ERROR_RESPONSE);
        delay += sc_time(n_beats * {{cycles_per_beat}}, SC_NS); // 告知发起方耗时
    }
    // 桥接层 2 的 write_thread / read_thread 实现五通道握手，见下文
};
```

桥接层 2 的写通道线程严格遵循 AXI 的 valid/ready 语义：**valid 置位后必须保持到 ready 采样为高才能撤销**，且地址与数据通道允许并发发起以贴近真实主设备行为。读响应通道采样 RRESP，非 OKAY（SLVERR/DECERR）时通过 `done_event` 把错误码传回桥接层 1，最终映射为 TLM 响应状态——这条错误通路是初稿完全缺失的，缺失的后果是 RTL 返回总线错误时 QEMU 侧软件毫无感知，调试将极为困难。

```cpp
void write_thread() {  // 桥接层 2：AXI-Lite 写时序（AW/W 并发，B 收尾）
    while (true) {
        if (wr_queue.empty()) { wait(wr_queue.data_written_event()); continue; }
        Beat b = wr_queue.front();
        sig_{{awaddr}}.write(b.addr); sig_{{awvalid}}.write(1);
        sig_{{wdata}}.write(load_le(b.ptr, b.len));
        {% if has_wstrb %}sig_{{wstrb}}.write((1u << b.len) - 1);{% endif %}
        sig_{{wvalid}}.write(1);
        wait(m_clk.posedge_event());
        // 各自等待握手完成；valid 在握手前保持
        while (sig_{{awready}}.read() != 1) wait(m_clk.posedge_event());
        sig_{{awvalid}}.write(0);
        while (sig_{{wready}}.read()  != 1) wait(m_clk.posedge_event());
        sig_{{wvalid}}.write(0);
        do { wait(m_clk.posedge_event()); } while (sig_{{bvalid}}.read() != 1);
        wr_queue.pop();
        done_event.notify(sig_{{bresp}}.read() == 0 /*OKAY*/);
    }
}
```

### 3.5 事务拆分与数据宽度适配

QEMU 侧的事务粒度与 RTL 总线宽度往往不一致：客户软件一次 8 字节读在 32 bit AXI-Lite 总线上必须拆为两个 beat，而 QEMU 的 DMA 式大块读写在 AXI4 上则可以合并为突发。桥接层 1 统一实现三种适配。**拆分**：当事务长度超过总线宽度时按总线宽度切分为 beat 序列，AXI-Lite/APB 上逐 beat 独立完成握手，AXI4 上转换为 `AWLEN = n_beats - 1` 的单次突发。**字节使能**：非对齐访问（地址未按总线宽度对齐或长度非总线宽度整数倍）时自动生成 `WSTRB`/字节掩码，小端序转换在 beat 装载函数 `load_le` 中完成。**位宽换算**：当 QEMU 侧视图（如设备树声明的 64 bit 寄存器）与 RTL 实际位宽不同时，按配置 `data_width_adapt: split | truncate | error` 三种策略处理，默认 `split` 并输出警告日志。

需要强调拆分策略对**寄存器语义**的影响：某些设备寄存器具有"读后清零""写 1 清零"等副作用语义，把一次宽事务拆成多次窄事务可能改变软件观察到的行为。框架无法自动识别这类语义，因此在 `config.yaml` 中提供 `register_semantics` 段允许用户声明敏感地址区间，桥接层 1 对这些区间拒绝拆分并强制要求事务粒度匹配，不匹配时返回 TLM 错误而非静默产生错误行为。

### 3.6 AXI-Stream 流式桥接

AXI-Stream 没有地址概念，无法像 memory-mapped 总线那样直接承载 QEMU 的事务语义，初稿提出的"MMIO 寄存器模拟流式注入"方案在方向上是正确的，本报告将其完善为带反压与状态观测的完整设计（图 4）。QEMU 侧看到的是一组 MMIO 寄存器：写 `STREAM_DATA` 向 SystemC 侧的 `sc_fifo` 推入一个 beat；写 `STREAM_FLUSH` 在下一拍标记 `TLAST` 结束当前包；读 `STREAM_STATUS` 返回 FIFO 剩余深度与对端已接收 beat 计数。SystemC 侧时序线程从 FIFO 弹出 beat 驱动 `TVALID/TDATA`，严格等待 `TREADY` 握手。

相对初稿的关键补充是**反压机制**：当 RTL 消费速度低于软件注入速度导致 FIFO 满时，初稿未定义行为（数据将被静默丢弃）。本设计提供两种可配置策略——`on_fifo_full: stall` 时桥接层 1 延迟该 MMIO 写事务的 TLM 响应，把反压传导回 QEMU 侧软件（写操作变慢但不丢数据）；`on_fifo_full: error` 时立即返回 SLVERR，由软件重试。前者语义安全、后者便于暴露性能瓶颈，默认取 `stall`。FIFO 深度默认 512 beat，可在 `config.yaml` 中调整；对于万兆级持续流场景，文档建议软件改用"写满即读状态轮询"的批量注入模式。

![图4 AXI-Stream 环形 FIFO 桥接](figs/fig4_axistream_fifo.png)

| MMIO 偏移 | 寄存器 | 方向 | 行为 |
| --- | --- | --- | --- |
| 0x00 | STREAM_CTRL | W | bit0：复位 FIFO；bit1：清空统计计数 |
| 0x04 | STREAM_DATA | W | 向 FIFO 推入一个 beat（自动字节序转换） |
| 0x08 | STREAM_FLUSH | W | 标记下一 beat 携带 TLAST，结束当前包 |
| 0x0C | STREAM_STATUS | R | [15:0] FIFO 剩余深度；[31:16] 对端已接收 beat 计数（饱和） |
| 0x10 | STREAM_RX_DATA | R | （M_AXIS 方向）弹出对端发来的一个 beat |
| 0x14 | STREAM_RX_STATUS | R | M_AXIS 方向可读深度与 TLAST 标志 |

对于 RTL 作为流输出方（M_AXIS）的反向通路，初稿未涉及，本报告补充对称设计：RTL 的 M_AXIS 输出被桥接层 2 接收并存入 RX FIFO，软件通过 `STREAM_RX_DATA/STREAM_RX_STATUS` 寄存器轮询读取；当 RX FIFO 非空超过阈值时可通过中断线通知 QEMU，避免纯轮询开销。

### 3.7 中断桥接

中断是 RTL 到 QEMU 的反向通路，语义上与事务通路完全不同——中断是**电平/脉冲信号**而非事务，因此 remote-port 为其提供独立的 wires 通道：SystemC 侧的 `remote-port-tlm-wires` 模块把线信号变化编码为 remote-port 消息，QEMU 侧设备存根收到后驱动对应 IRQ 引脚[^16^][^20^]。框架生成的中断桥接模块非常薄：监听推断出的中断输出端口（命名规则 `irq|intr|interrupt`，或用户在 `config.yaml` 中显式指定），在电平变化沿调用 wires 模块的 `set_level`，并按配置支持**电平触发**与**边沿触发**两种模式——电平模式直接透传，边沿模式在脉冲后自动清除。

中断设计中最值得注意的陷阱是**中断风暴与清除协议**：许多 RTL 的中断需要软件写寄存器清除（write-to-clear），而清除动作本身是一次 MMIO 写事务，走正向事务通路。这意味着中断通路与事务通路存在闭环依赖——RTL 拉高 IRQ → QEMU 软件进中断处理 → 软件写清除寄存器 → 事务到达 RTL → RTL 拉低 IRQ。框架在生成工程中内置一条自检用例验证该闭环时延，并在 `advanced.irq_settle_timeout_ns` 超时未清除时输出告警，因为该闭环断裂（例如清除寄存器映射错误）是协同仿真中最常见的挂死原因之一。

### 3.8 时钟域桥接

时钟域策略的自动选择逻辑在初稿中已有雏形，本报告将其形式化为图 5 所示的决策树，并补充每种策略的实现约束。策略 A（单时钟直接绑定）适用于绝大多数 IP：唯一的时钟端口直接绑定 `sc_clock`，无额外开销。策略 B（异步 FIFO 桥接）适用于 RTL 内部多时钟但对外接口单一时钟源的场景：桥接层在事务通路插入异步 FIFO 适配器，QEMU 侧时钟域写、RTL 时钟域读。策略 C（多时钟域桥接）适用于 RTL 暴露多个异频时钟的场景：为每个时钟域生成独立 `sc_clock`，频率比来自 `config.yaml` 的 `clock_freqs`，跨域信号经 CDC 保护逻辑。

初稿的异步 FIFO 示意代码存在两处问题需要修正：其一，`sc_fifo` 是 SystemC 的同步 FIFO 抽象，直接用于跨时钟域在语义上成立（SystemC 进程按事件调度，无真实亚稳态），但**它掩盖了真实硬件中的亚稳态风险**——框架生成的适配器在注释与文档中必须明确说明"仿真中的跨域 FIFO 不建模亚稳态，流片前需以真实异步 FIFO IP 替换"；其二，初稿示例中 `wait(qemu_clk.posedge_event())` 出现在普通成员函数中，只有 `SC_THREAD` 进程才能调用阻塞式 `wait()`，生成器对此做了严格的模板检查。此外，策略 B/C 下复位信号的跨域同步（复位释放必须同步到目标时钟域）由生成器自动插入双触发器同步链模板处理。

![图5 时钟域桥接策略自动选择](figs/fig5_clock_strategy.png)

---

## 4. 子系统三：一体化构建与运行系统

### 4.1 生成工程目录结构

子系统三把前两个子系统的产出组织为一个自包含、可版本管理的工程。目录结构在初稿基础上做了三处调整：`qemu/` 下增加设备树片段（Xilinx QEMU 形态需要 `-hw-dtb` 传入协仿真设备树[^2^]）；新增 `reports/` 目录统一存放推断报告与比对报告；`tb/` 下区分裸机测试与 Linux 驱动测试两层。

```text
output/
├── rtl/                          # 用户 RTL 源码（原样拷贝，保持只读）
├── bridge/                       # ★ 生成的桥接代码
│   ├── bridge_top.h/.cpp         # SC_MODULE 顶层（实例化桥接 + RTL + remote-port）
│   ├── axi_lite_target.h/.cpp    # 协议桥接（按推断结果生成对应协议）
│   ├── interrupt_bridge.h/.cpp   # 中断 wires 桥接
│   ├── stream_fifo.h/.cpp        # AXI-Stream FIFO（按需生成）
│   └── signal_map.h              # 端口映射表（由推断结果渲染）
├── qemu/
│   ├── qemu_device_stub.c/.h     # 上游 QEMU 设备存根（内嵌 remote-port 客户端）
│   ├── cosim.dtsi                # Xilinx QEMU 形态的协仿真设备树片段
│   └── qemu_args.txt             # 生成的 QEMU 启动参数说明
├── build/
│   ├── CMakeLists.txt
│   └── run_cosim.sh              # 一键启动脚本
├── tb/
│   ├── baremetal/test_baremetal.c    # 裸机测试（直接 MMIO 读写）
│   ├── linux/test_driver.c           # Linux 驱动层测试（可选）
│   └── compare_results.py            # 黄金参考自动比对
├── reports/                      # 推断报告 / 比对报告 / 波形
└── config.yaml                   # ★ 用户唯一需要填写的文件
```

### 4.2 用户配置文件 config.yaml

配置文件是用户与框架的唯一交互界面，设计原则是"必填项最少化、推断项可覆盖"。相对初稿，本版补充了 remote-port 时间同步参数（`sync_quantum`/`machine_path`，依据 3.2 节机制）、波形跟踪开关、FIFO 反压策略与寄存器副作用声明：

```yaml
project:
  name: my_accelerator_cosim
  qemu_flavor: xilinx            # xilinx（remote-port 内建）| upstream（生成设备存根）
  qemu_machine: zynqmp-zcu102    # 或 virt（upstream 形态）

rtl:
  top_module: my_accelerator
  source_files: [rtl/my_accelerator.v, rtl/my_accelerator_pkg.v]
  clk_freq_mhz: 100
  # clk_port / rst_port 默认自动推断，可在此显式覆盖

bridge:
  protocol: auto                 # auto | axi-lite | axi4 | axi-stream | ahb | apb
  base_address: "0xA000_0000"
  data_width: 32
  irq_number: 5
  stream:
    fifo_depth: 512
    on_fifo_full: stall          # stall | error

advanced:
  sync_quantum_ns: 100000        # 必须与 SystemC 侧量子值一致
  icount: 1
  machine_path: /tmp/cosim
  timeout_ns: 10000
  trace: fst                     # none | vcd | fst
  log_level: info
  register_semantics:            # 具副作用寄存器，禁止事务拆分
    - { offset: "0x40", access: read_clear }

manual_mapping: {}               # 推断不准时的端口映射覆盖
```

### 4.3 CMake 构建脚本（修正版）

初稿 CMake 中 `verilate(Vtop V{{module_name}} SOURCES ...)` 的调用形式与 Verilator 官方 CMake 集成不符，且未指定 SystemC 模式。修正版使用 `verilate()` 的标准签名，通过 `SYSTEMC` 关键字启用 `--sc` 输出，并将波形跟踪格式与配置联动：

```cmake
cmake_minimum_required(VERSION 3.16)
project({{project_name}} CXX)

find_package(SystemC REQUIRED)
find_package(verilator HINTS $ENV{VERILATOR_ROOT} REQUIRED)

add_executable(cosim
    sc_main.cpp
    bridge/bridge_top.cpp
    bridge/{{protocol}}_target.cpp
    bridge/interrupt_bridge.cpp
    # libremote-port / libsystemctlm-soc 源文件由生成器按依赖展开
)

# Verilator CMake 集成：SYSTEMC 即 --sc 模式
verilate(cosim SYSTEMC TRACE_FST COVERAGE
    TOP_MODULE {{top_module}}
    PREFIX V{{top_module}}
    SOURCES {{#each rtl_sources}}{{this}} {{/each}}
)

target_link_libraries(cosim PRIVATE ${SystemC_LIBRARIES})
target_include_directories(cosim PRIVATE ${SystemC_INCLUDE_DIRS})
```

构建系统还承担一项质量门禁职责：在 `verilate` 之前先执行 `verilator --lint-only` 对 RTL 做静态检查，lint 警告按配置 `--Wall` 级别汇总进构建日志；lint 报错则中止生成并给出修复建议。这道门禁把大量"RTL 本身写得不规范导致桥接失效"的问题拦截在构建期而非仿真运行期。

### 4.4 一键启动脚本与运行流程

一键启动脚本把"构建→起仿真→起 QEMU→收集结果"编排为一条命令，流程见图 7。相对初稿，修正版脚本补全了 QEMU 侧必需的时间同步参数与 socket 等待逻辑（QEMU 会先挂起等待 remote-port 连接，SystemC 侧作为客户端接入[^20^]），并增加失败清理 trap：

```bash
#!/bin/bash
set -euo pipefail
trap 'kill ${COSIM_PID:-} ${QEMU_PID:-} 2>/dev/null || true' EXIT

cd build && cmake .. && make -j"$(nproc)"            # ① 构建

./cosim & COSIM_PID=$!                               # ② 起 SystemC（remote-port 客户端）

qemu-system-aarch64 \                                # ③ 起 QEMU
    -M {{qemu_machine}} -m 256M -nographic -serial stdio \
    -kernel ../tb/baremetal/test_baremetal.elf \
    {{#if qemu_flavor_xilinx}}
    -hw-dtb ../qemu/cosim.dtb \
    {{/if}}
    -machine-path {{machine_path}} \                 # 创建 qemu-rport socket
    -icount {{icount}} -sync-quantum {{sync_quantum_ns}} \
    & QEMU_PID=$!

wait ${QEMU_PID}                                     # ④ QEMU 退出即仿真结束
python3 ../tb/compare_results.py ../reports/         # ⑤ 自动比对
```

![图7 一键构建与运行流程](figs/fig7_build_flow.png)

### 4.5 调试与可观测性

协同仿真横跨两个进程、三个抽象层，可观测性设计直接决定框架的可用性。框架内置四级观测手段：**波形级**，Verilator 以 FST/VCD 格式输出全信号波形（`trace: fst`），可用 GTKWave 查看，libsystemctlm-soc 的示例工程同样采用该方式验证[^4^]；**事务级**，桥接层 1 对每个 TLM 事务记录时间戳、地址、长度、响应与耗时，输出 CSV 供性能分析；**协议级**，桥接层 2 在 `log_level: debug` 下打印每次握手的周期计数，可快速定位"卡在某个 ready 等不到"的典型问题；**系统级**，remote-port 的 socket 流量可通过 `socat` 旁路抓取，用于排查 QEMU 侧连接问题[^20^]。四级手段按粒度从粗到细组织，文档引导用户先从事务日志定位异常事务，再用波形深挖对应时间窗，避免一上来就被全信号波形淹没。

---

## 5. 验证方案

### 5.1 验证案例矩阵

验证方案的目标是覆盖框架的三条核心能力链路：协议推断正确性、桥接功能正确性、时钟域处理正确性。初稿的四个案例被补全并扩展为六个，形成递进式矩阵——每个案例只引入一个新变量，确保失败时能定位到具体能力链路。

| 案例 | 总线协议 | RTL 规模 | 引入变量 | 主要验证目标 | 通过判据 |
| --- | --- | --- | --- | --- | --- |
| C1 Simple GPIO | AXI-Lite | ~50 行 | 无（基线） | 端到端通路打通：寄存器读写正确性 | 32 组随机读写与黄金参考逐位一致 |
| C2 FIR Filter | AXI-Stream | ~150 行 | 流式接口 | FIFO 注入、TLAST 包边界、反压 stall/error 两策略 | 输出序列与软件参考模型误差为 0；FIFO 满时无数据丢失 |
| C3 Matrix Mul | AXI-Lite + 中断 | ~200 行 | 中断闭环 | 中断触发→软件处理→写清除寄存器→中断撤销全闭环 | 中断延迟在预期窗内；无中断风暴（重复触发 ≤1 次/事务） |
| C4 Dual-Clock FIFO | AXI-Lite | ~100 行 | 双时钟域 | 策略 B 异步 FIFO 桥接自动选择与正确性 | 跨域数据无丢失无重复；策略选择结果符合决策树 |
| C5 Burst DMA 引擎 | AXI4 | ~250 行 | 突发传输 | 宽事务拆分/合并为突发、WLAST/RLAST 处理 | 4 KiB 块传输数据一致；突发长度与拆分计算相符 |
| C6 命名混淆 IP | AXI-Lite（自定义命名） | ~80 行 | 非标准命名 | 时序特征匹配兜底与置信度降级 | 推断给出正确 Top-3 候选；人工指定后功能同 C1 |

### 5.2 验证指标与通过判据

案例之外，框架定义四项量化验收指标。**推断准确率**：在案例集与外部收集的开源 IP（如 OpenCores/common AXI 封装）上，协议推断 Top-1 准确率目标 ≥85%、Top-3 ≥95%，未达标项全部归因分析并沉淀为规则库补充。**功能正确性**：全部案例的数据比对逐位一致，不允许以"近似正确"通过。**闭环时延**：C3 中断闭环时延与 C1 单拍寄存器读时延纳入基准库，版本回归时劣化超过 20% 即告警。**接入效率**：从拿到符合规范的 RTL 到完成 C1 级验证的人工耗时 ≤30 分钟（不含环境安装），这是"自动化"价值的直接度量。

回归体系上，六个案例全部脚本化并接入 CI：每次改动推断器或模板库后全量运行，比对报告（`compare_results.py` 产出）汇总为 HTML。案例的 RTL、测试软件与黄金参考全部纳入版本库，保证任何协作者能复现同一份验证结论。

---

## 6. 性能分析与优化

### 6.1 RTL 仿真性能基础

框架性能的地基是 Verilator 的编译型仿真。Verilator 官方数据表明，其生成模型单线程即比独立 SystemC 快 10 倍以上、比 Icarus Verilog 等解释型仿真器快约 100 倍，多线程还可再获 2–10 倍提升[^32^]；第三方基准在 OpenRISC SoC 上测得 Verilator 42.66 kHz 对 Icarus 1.48 kHz，约 29 倍差距（图 6）[^23^]。这意味着对协同仿真而言，瓶颈通常不在 RTL 侧，而在跨进程的事务通路——这正是 6.2、6.3 节优化聚焦的位置。

![图6 RTL 仿真器性能对比](figs/fig6_sim_perf.png)

### 6.2 时间同步量子的精度—性能权衡

协同仿真性能与精度的主控旋钮是同步量子（`-sync-quantum`）：QEMU 每推进一个量子与 SystemC 对齐一次，量子越小时间精度越高，但同步开销越大、整体越慢，且两侧必须取相同值[^2^][^25^]。工程上的调优策略是**分阶段取值**：开发调试期取小量子（1–10 µs 级）保证中断与超时语义精确；回归跑批期放大量子（100 µs–1 ms 级）换取吞吐；若客户软件含有依赖精确外设定时的逻辑（如轮询超时），则必须以功能正确性优先。框架在 `compare_results.py` 中内置一项"量子敏感性检查"——对同一测试分别以两档量子运行并比对结果，若结果随量子变化则提示软件存在时间敏感路径，这是一个低成本的正确性预警。

### 6.3 桥接层优化策略

桥接层自身有四处可挖掘的性能空间。**DMI（直接内存接口）直通**：对 AXI4 大块传输，桥接层 1 支持 TLM DMI 提示，让 QEMU 侧直接读写共享内存区，绕开逐 beat 的 socket 序列化，这是 remote-port 共享内存机制的直接红利[^2^]。**批量 beat 流水**：桥接层 2 的突发模板允许 AW 通道提前发起下一事务，与当前事务的 W/B 通道交叠，逼近真实主设备的流水行为。**eval 调用合并**：SystemC 侧每个时钟沿触发一次 Verilator `eval()`，生成器把多个独立桥接实例挂到同一时钟进程，避免每实例各自的调度开销。**日志按需开启**：事务级日志在 `log_level: info` 时仅保留统计计数，详细记录只在 debug 模式启用，避免 I/O 成为隐性瓶颈。

---

## 7. 风险、限制与应对

| 风险/限制 | 影响 | 应对 |
| --- | --- | --- |
| remote-port 依赖 Xilinx QEMU 分叉，上游 QEMU 不支持[^2^] | 非 Zynq/Versal 平台接入受限 | 生成上游 QEMU 设备存根模板（3.2 节）；长期跟踪上游合入动态 |
| Verilator `--sc` 对个别 SV 特性兼容性问题（如 struct 输出端口）[^3^] | 部分 RTL 无法直接编译 | 生成前 lint 预检；引导用户加纯 Verilog wrapper（3.3 节） |
| Verilator 为两态、周期精确仿真，不建模 X 态与亚稳态[^32^] | 跨时钟域与复位异常类 bug 可能漏检 | 文档明示边界；关键 CDC 场景建议商业仿真器复核（7 节下方说明） |
| 协议推断对极端自定义命名失效 | 推断置信度低 | Top-3 候选 + 人工指定 + 规则库可扩展（2.6 节四级降级） |
| 主设备型 RTL（主动 DMA）未覆盖 | 部分加速器无法接入 | 路线图 M4 规划 initiator socket 模板（第 8 节） |
| QEMU 与 SystemC 版本演进带来的 API 漂移 | 生成代码随时间失效 | 模板库按 QEMU 8.x/9.x、SystemC 2.3.x/3.0 多版本维护；CI 覆盖版本矩阵 |
| 寄存器副作用语义无法自动识别 | 事务拆分可能改变行为 | `register_semantics` 显式声明 + 拒绝拆分（3.5 节） |

需要单独强调的是两态仿真限制的本质含义：框架产出的是**功能正确性**证据，而非时序/电气正确性证据。对于流片前的 sign-off 级验证，本框架定位是"前端快速迭代工具"，与商业事件驱动仿真器是互补而非替代关系——Aldec 等厂商基于同一 libsystemctlm-soc 体系提供商业协同仿真方案这一事实，恰好说明两条路线共享同一套事务接口，用户在框架中验证过的桥接配置可以平滑迁移到商业环境做深度验证[^15^]。

---

## 8. 实施路线图

| 里程碑 | 内容 | 验收标准 | 建议周期 |
| --- | --- | --- | --- |
| M1 最小可用通路 | AXI-Lite 单协议 + 单时钟 + Xilinx QEMU 形态；手工协议声明 | C1 通过 | 4 周 |
| M2 自动推断 | 命名模式 + 时序特征 + 打分引擎；规则库 YAML 化 | C1/C6 通过；Top-3 准确率 ≥95% | 4 周 |
| M3 协议扩展 | AXI-Stream FIFO 桥接 + 中断闭环 + 上游 QEMU 存根 | C2/C3 通过 | 4 周 |
| M4 高级能力 | AXI4 突发 + 多时钟域策略 B/C +  initiator 模板（主设备） | C4/C5 通过 | 5 周 |
| M5 工程化 | CI 回归矩阵、HTML 报告、文档、性能基线库 | 第 5.2 节四项指标全达标 | 3 周 |

路线图按"通路优先、自动化其次、广度最后"排序：M1 先打通端到端通路以暴露集成风险（remote-port 连接、Verilator 编译、CMake 链路），M2 才投入推断算法——这一顺序避免了"推断器做得很好但桥接跑不起来"的资源错配。M4 的 initiator 模板是架构上预留的最大扩展点，双桥接模型对此天然兼容：方向反转后桥接层 1 变为事务发起方，桥接层 2 变为协议主设备时序发生器，分层结构无需改动。

---

## 9. 相对初稿的主要修订说明

为便于已有初稿阅读背景的读者快速定位变化，下表汇总本次重写的关键修订：

| 位置 | 初稿内容 | 本报告修订 | 修订原因 |
| --- | --- | --- | --- |
| 打分算法 | `confidence = s_best / Σs`，覆盖率硬阈值惩罚 | 命名分量归一化、除零保护、软惩罚、接受阈值 τ | 原公式在零得分时崩溃，得分随 IP 规模膨胀不可比 |
| 桥接模板 | `m_rtl->clk = m_clk;`、`m_rtl->port = signal;` | `port(signal)` 绑定语法 + `sc_signal` 驱动 | `--sc` 模式端口是 `sc_in/sc_out`，赋值无法完成绑定[^12^] |
| QEMU 启动 | 仅 `-device ...,addr=...,irq=...` | 补全 `-machine-path/-icount/-sync-quantum` 与设备树；说明 Xilinx 分叉依赖 | remote-port 必需的同步参数，且非上游 QEMU 内建[^2^][^25^] |
| 中断通路 | 未设计 | wires 桥接 + 清除闭环自检 | 中断是协同仿真最常见挂死源[^16^] |
| AXI-Stream | MMIO 注入思想，无反压 | 补全 FIFO 满 stall/error 策略与 RX 反向通路 | 初稿 FIFO 满时静默丢数据 |
| 错误通路 | 响应恒 `TLM_OK_RESPONSE` | BRESP/RRESP → TLM 错误码回传 | 总线错误对软件不可见会导致调试困难 |
| 验证方案 | C4 行截断，无判据 | 六案例矩阵 + 四项量化指标 | 原矩阵不完整且不可判定 |
| 构建脚本 | `verilate(Vtop V... )` 参数形式错误 | 官方 CMake 集成签名 + lint 门禁 | 按 Verilator CMake 文档修正 |

---

[^1^]: https://www.veripool.org/ftp/verilator_doc.pdf
[^2^]: https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/862421112/Co-simulation
[^3^]: https://github.com/verilator/verilator/issues/6329
[^4^]: https://github.com/edgarigl/libsystemctlm-soc
[^12^]: https://verilator.org/guide/latest/connecting.html
[^15^]: https://www.aldec.com/en/solutions/functional_verification/qemu_co_sim
[^16^]: https://blog.reds.ch/?p=1180
[^20^]: https://blog.reds.ch/?p=1180
[^23^]: https://www.embecosm.com/appnotes/ean6/html/ch06s05s01.html
[^25^]: https://docs.amd.com/api/khub/documents/DlUASu~dMwZk66LZyWGAFg/content
[^27^]: https://github.com/felisis/Pyverilog-1
[^28^]: https://github.com/merledu/Pyverilog-sv2v
[^32^]: https://github.com/verilator/verilator
