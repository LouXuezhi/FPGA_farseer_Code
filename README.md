# fiber_speckle_thermo

基于 FPGA 的单光纤沿线温升实时报警边缘终端。

一根多模光纤 + 一台相机 + 一块 FPGA 开发板：光纤沿线出现异常温升时报警，在板上完成采集、推理、判断、显示和 MQTT 上报。

- 赛事：2026 全国大学生嵌入式芯片与系统设计竞赛 · FPGA 赛道 · AMD 自主选题赛道（初级组）
- 板卡：EdgeBoard FZ3B，Zynq UltraScale+ ZU3EG（`xczu3eg-sfvc784-1-i`），4 GB DDR4
- 相机：OV9281 全局快门，MIPI CSI-2 1-lane，RAW8 640×800
- 工具：Vivado 2026.1；板上 Linux 4.14（xilinx-v2018.3），Python 3.6.9，numpy 1.13.3
- 开源协议：MIT（`LICENSE`）

## 系统组成

![系统组成](report/figures/system_overview.png)

系统架构图、数据流图、软硬件划分和各模块框图见 [`report/architecture.md`](report/architecture.md)。

```
多模光纤出射散斑
   │
OV9281 ─CSI-2─► PL: D-PHY 接收 → CSI-2 解析 → VDMA ─► DDR 帧存 ×4
                                                    │
         PS: 帧采集 → 预处理（平场 + 标准化 → 2×56² uint8）
                    │ AXI DMA
                    ▼
         PL: QCNN 推理核（参考帧 + 当前帧 → 9 路 ΔT）
                    │ AXI DMA
                    ▼
         PS: ΔT 累加 + 校准 → 温度、阈值/速率告警
                    ├─► 屏幕界面（800×480，Mini DP）
                    └─► MQTT（status / state / alarm，cmd/calibrate 远程校准）
```

| 部分 | 实现 | 位置 |
|---|---|---|
| 相机采集 | 自写 RTL：D-PHY 接收、CSI-2 解析、寄存器 | `src/rtl/cam/` |
| QCNN 推理核 | 自写 RTL：4 层 3×3 s2 卷积 + GAP + 2 层 FC，12 bit 定点，层内按输出通道并行、层间按帧流水 | `src/rtl/qcnn/` |
| DMA 帧链路 | 自写 RTL：AXI-Stream ↔ 推理核 | `src/rtl/link/` |
| PL 屏幕合成 | 自写 RTL：背景 + 沿程着色 + 相机直读 + 字符层 | `src/rtl/video/` |
| 节点服务 | Python：采集、预处理、60 s 窗口、DMA 调度、窗口融合报警、MQTT | `src/host/speckle_node.py`、`src/host/win_fusion.py` |
| PS 屏幕界面 | Python：多页界面、3D 管路实时着色、散斑预处理视图、显示防抖 | `src/host/fsf_ps_ui.py` |
| 训练与定点模型 | PyTorch 训练、量化、整数参考模型、RTL 参数导出；报警方法的训练与评估 | `src/model/`、`src/model/win_alarm/` |

## 主要指标

| 指标 | 数值 | 条件 | 来源 |
|---|---|---|---|
| 温升报警 | ≥1 °C/2 min 检出 92%（1% 误报），30/30 次升温事件报警，延迟中位 25 s | 第三次采集 11 段，5 折留出 | `src/model/win_alarm/` |
| 推理核吞吐 | 565 µs/帧（首帧 1117 µs） | 100 MHz，2×56² → 9，训练权重 | RTL 仿真，`sim/results/tb_qcnn_core.log` |
| 推理核与定点模型一致性 | 144/144 逐位一致（16 组实测窗口 × 9 输出）；板上 40/40 | 同上 | RTL 仿真；板上实测 |
| 板上 DMA 推理调用 | p50 1.34 ms，max 1.48 ms（每次换一组输入） | 40 次 | 板上实测 |
| 板上预处理 | p50 2017 ms | PS 单核 numpy | 板上实测 |
| 步长 | p50 2.02 s | 采集 + 预处理 + 推理 + 上报 | 板上实测 |
| 时序 | clk_pl_0 100 MHz WNS +2.545 ns，WHS +0.005 ns；vclk WNS +8.263 ns | 布线后 | `build/vivado/reports/` |
| PL 资源 | LUT 20322（28.8%），FF 20041（14.2%），BRAM 53.5（24.8%），DSP 108（30.0%） | 合并比特流 | 同上 |
| 片上功耗 | 2.99 W | Vivado 估计 | 同上 |
| 显示 | 800×480 @ 60 Hz | Mini DP，7 英寸屏 | 板上实测 |

推理核载入的是训练好的权重（`data/fusion/qmodel_win.npz`），`src/rtl/gen/` 与 `board/fz3b/prebuilt/` 都由它生成。
报警参数在 `data/fusion/fusion.json`；换光路后按 `src/model/win_alarm/README.md` 录一段不加热数据重新标定门限。

## 复现

### 1. 环境

```bash
conda env create -f environment.yml && conda activate speckle     # 训练、导出、仿真脚本
./build/fetch_rtl_deps.sh                                         # 开源 RTL（verilog-axis，固定提交）
```

仿真用 Verilator（≥ 5.0，无 Vivado 时）或 Vivado 2026.1 自带的 xsim。

### 2. 仿真（不需要板卡）

```bash
./sim/rtl/run_verilator.sh tb_qcnn_core                    # 推理核（训练权重）：144/144 与定点模型逐位一致
./sim/rtl/run_verilator.sh tb_axis_frame_link              # DMA 帧链路，随机反压
python -I tools/ui/build_ui.py --cam data/cam_frame_640x800.npy
EXTRA_MEM=build/ui ./sim/rtl/run_verilator.sh tb_video_overlay   # PL 屏幕合成，逐像素对参考模型
python -I sim/host/test_node.py                            # MQTT、DMA 驱动、屏幕寄存器、节点主循环、窗口融合报警
python -I sim/host/test_ui_filter.py                       # PS 界面显示防抖
```

预期输出与说明见 `sim/README.md`。

### 3. 构建比特流

```bash
FSF_CAM=1 FSF_VIDEO=1 vivado -mode batch -source build/vivado/build_fz3b.tcl
```

产物与报告见 `build/README.md`；本仓库已带一份构建结果（`board/fz3b/prebuilt/`，带 sha256）。

### 4. 上板

见 `board/fz3b/README.md`：加载比特流、启动相机服务、启动节点服务和屏幕界面、接 MQTT。

## 目录

与赛事指南 3.3.5.4 推荐结构一致，目录名和文件名均为小写英文。

```
fiber_speckle_thermo/
├── README.md          项目简介 + 复现步骤（本文件）
├── LICENSE            MIT
├── environment.yml    训练与仿真环境
├── src/               设计源码
│   ├── rtl/           Verilog：qcnn（推理核）、link（DMA 帧链路）、cam（相机采集）、video（屏幕合成）、gen（导出的权重与参数）
│   ├── host/          PS 侧 Python：节点服务、DMA 驱动、MQTT、屏幕界面
│   └── model/         训练、量化、定点参考模型、RTL 导出
├── sim/               仿真与验证：rtl/（testbench 与运行脚本）、host/（板端软件测试）
├── build/             构建脚本 + 综合与实现报告
├── board/fz3b/        上板说明、预编译比特流、界面素材、相机服务、实测输出
├── tools/             界面素材生成（ui/）、上位机 MQTT 监视页（hmi/）
├── data/              测试数据与参考：定点模型、散斑样帧、公开数据集下载脚本
├── skill/             技能包
└── report/            设计报告、系统架构说明（architecture.md）与架构图（figures/）
```

各目录下有 `README.md` 说明文件作用和用法。
