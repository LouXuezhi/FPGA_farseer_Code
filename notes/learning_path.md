# 学习路线（fiber_speckle_fpga）

> 前提：已有 RTL/CPU 设计与 Vivado 仿真综合经验（class_cpu、RISC-V、ibex 等），Python/PyTorch 熟练。
> 所以**跳过 Verilog 基础**，重点补：Zynq 软硬件协同、HLS、模型量化部署、XADC/时序接口、DSP、散斑光学。
> 原则：每一步都对应项目里的一个可运行产出，边学边做，不单独刷教程。

## 总览

| 周 | 主题 | 检查点（做到才算过） |
|---|---|---|
| 1 | Zynq + PYNQ 上手 | 板上 Jupyter 用自定义 AXI-Lite IP 点灯、读寄存器 |
| 2 | HLS + AXI-Stream + DMA | HLS 流式 FIR 核，Python 经 DMA 送数据并拿回结果，与 numpy 逐样本一致 |
| 3 | 量化与 hls4ml 部署 | 公开温度散斑数据集上的小网络跑在板上，给出精度/延迟/资源表 |
| 4 | DSP 与定点化 | ZNCC/FFT 特征核（HLS），与 PC 金标准比对误差 < 1 LSB 量级 |
| 5 | 散斑物理 + pyMMF 合成 | 生成 D1 合成动态散斑数据集，完成增益 K 扫描 |
| 6 | XADC + 线阵时序（RTL） | TSL1401 时序发生器 + XADC 同步采样，仿真通过，上板读到一行数据 |
| 7+ | 实物光路与台架 | 进入 T2/V1 |

---

## 第 1 周：Zynq 与 PYNQ

**学**
- Zynq PS/PL 划分、AXI4-Lite 寄存器映射、Block Design、`.bit` + `.hwh`
- PYNQ 的 `Overlay`、`MMIO`、`register_map`

**资料**
- 《The Zynq Book》（免费 PDF）第 1–3、10 章
- PYNQ 官方文档 Overlay 部分：https://pynq.readthedocs.io
- `refs/PYNQ/boards/Pynq-Z2/base`：看官方 base overlay 的 Block Design

**练**
1. 烧 PYNQ-Z2 镜像，通过网线进入 Jupyter
2. Vivado 里自己打包一个 AXI-Lite 计数器 IP，导出 `.bit/.hwh`，Python 读写寄存器

**你已有的优势**：RTL 和时序约束不用重学，重点是 IP 打包和 PS 侧调用。

## 第 2 周：HLS 与数据通路

**学**
- Vitis HLS：`pragma`（PIPELINE、ARRAY_PARTITION、DATAFLOW）、`ap_fixed`、`hls::stream`
- AXI-Stream 握手、AXI DMA、PYNQ `allocate` 与缓存一致性

**资料**
- AMD XUP HLS 课程（赛题指南推荐）：https://xilinx.github.io/xup_high_level_synthesis_design_flow/
- UG1399 Vitis HLS 用户指南：pragma 章节

**练**
- HLS 写流式 FIR/滑动均值 → AXI-Stream IP → DMA → Python 回读
- 写一个"黄金比对脚本"（numpy 结果 vs 板上结果），**这就是 Skill 包的第一项**

## 第 3 周：模型量化与部署

**学**
- 定点量化（位宽、QAT 与 PTQ 的区别）、hls4ml 的 reuse factor / 精度配置
- FINN 流程只需了解（PYNQ-Z2 已退出官方支持，作为备选）

**资料**
- hls4ml 教程：https://github.com/fastmachinelearning/hls4ml-tutorial（第 1–4 部分 + `VivadoAccelerator` 部分）
- `refs/hls4ml/hls4ml/templates/vivado_accelerator/pynq-z2/`
- `refs/brevitas` 示例（QAT）

**练**
- `data/raw/osf_zfmp5_synthetic_thermal` → 缩到 32×32 → 小 CNN/MLP → 量化 → hls4ml → PYNQ-Z2
- 记录：浮点精度 vs 定点精度、LUT/FF/DSP/BRAM、延迟、功耗

## 第 4 周：DSP 与定点化

**学**
- 采样定理、抗混叠、窗函数与 FFT、互相关/ZNCC、带通滤波
- 定点数的溢出、截断与舍入，CORDIC / 查表开方

**资料**
- 《Understanding Digital Signal Processing》（Lyons）第 1–5、13 章
- scipy.signal 文档，先在 Python 里做一遍

**练**
- 用 Mendeley 泄漏数据（`data/raw/mendeley_...`）在 Python 里做频谱分析，确认泄漏/无泄漏频带差异
- HLS 实现 128 点 ZNCC + 分帧 FFT 特征核，与 Python 比对

## 第 5 周：散斑物理与合成数据

**学**
- 多模光纤模式、传播常数、模间干涉、散斑颗粒尺寸 λz/D、去相关
- 光纤应变–相位关系（ξ = 1 − p_e），薄壁管环向应变

**资料**
- 本地调研 `~/Desktop/WiseStars/光纤/调研/index.md` 第 2.1 节
- `refs/pyMMF/docs/examples/Benchmark_SI.ipynb`
- 三篇散斑论文（本地 `前沿技术论文/散斑*`），重点看实验装置和标定方法

**练**
- 按方案报告第 12 节生成 D1 数据集，做数据划分与耦合增益 K 扫描
- 输出 AXI 帧格式二进制，接第 2 周的回放通路

## 第 6 周：XADC 与线阵时序（RTL）

**学**
- XADC 通道、序列模式、采样率与建立时间（UG480）
- TSL1401 时序：SI/CLK、积分时间、保持输出（手册 `docs/datasheets/tsl1401cl_datasheet.pdf`）

**练**
- RTL：SI/CLK 发生器 + 曝光控制 + XADC 同步 → AXI-Stream（格式与 D1 回放一致）
- 先 testbench（用行为模型模拟传感器输出），再上板读实物
- **这一部分是初级组"状态机/时序/接口"的核心得分点，建议你亲自写**

## 贯穿全程

| 事项 | 做法 |
|---|---|
| 大模型协作记录（赛事必交 15 分） | 每次用 AI 的提示词、回答、纠错过程存 `report/llm_log/日期_主题.md` |
| Skill 包 | 从第 2 周黄金比对脚本开始积累，写清适用场景和失效条件 |
| 版本管理 | 每个检查点打一次 git tag |

## 两人分工建议

| 线 | 内容 |
|---|---|
| 硬件线 | 第 1、2、6 周 + PL 集成、时序收敛、资源报告 |
| 算法线 | 第 3、4、5 周 + 数据合成、模型量化、PC 金标准 |

第 2 周末、第 4 周末各对齐一次接口（AXI-Stream 帧格式、定点位宽）。
