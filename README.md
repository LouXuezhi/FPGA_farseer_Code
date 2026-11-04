# fiber_speckle_thermo

**基于 FPGA 的多模光纤沿线温升实时报警终端**
**FPGA edge terminal for real-time temperature-rise alarms along a multimode fiber**

一根多模光纤绕在液冷管路上，相机采集光纤出射的散斑，EdgeBoard FZ3B 在板上完成采集、QCNN 推理、报警判断、屏幕显示和 MQTT 上报。
A multimode fiber is wound along a liquid-cooling pipe. A camera captures the output speckle, and an EdgeBoard FZ3B does capture, QCNN inference, alarm decision, display and MQTT reporting on the board.

| | |
|---|---|
| 赛道 Track | 2026 全国大学生嵌入式芯片与系统设计竞赛 · AMD 自主选题（初级组） / 2026 NUEDC Embedded Chip & System Design, AMD open topic (junior) |
| 板卡 Board | EdgeBoard FZ3B, Zynq UltraScale+ ZU3EG `xczu3eg-sfvc784-1-i` |
| 相机 Camera | OV9281, MIPI CSI-2 1-lane, RAW8 640×800 |
| 工具 Tools | Vivado 2026.1; board Linux 4.14, Python 3.6.9, numpy 1.13.3 |
| 协议 License | MIT |

![系统组成 System overview](report/figures/system_overview.png)

## 成果 Results

### 报警效果：与其他方法同协议对比 Alarm performance vs. other methods (same protocol)

第三次采集 11 段数据，5 折留出；门限只用训练折的不加热对照数据确定。检出率在 1% 误报下统计，针对 2 min 内单路升温 ≥1 °C 的窗口。
11 recorded sessions, 5-fold hold-out; thresholds set from training-fold no-heat controls only. Detection rate at 1 % false alarms, for windows with ≥1 °C rise on one channel within 2 min.

| 方法 Method | AUC | 检出率 Detection | 事件 Events | 延迟中位 Median delay | 计算量 MMAC | 能上 PL 实时 Real-time on PL |
|---|---|---|---|---|---|---|
| **本设计 This work**（QCNN + 窗口去相关 window decorrelation） | **0.993** | **92%** | **30/30** | **25 s** | **2.6** | **是 Yes** |
| ResNet-18 微调 fine-tuned | 0.994 | 95% | 30/30 | 63 s | 712 | 否 No |
| MobileNetV3-S 微调 fine-tuned | 0.992 | 94% | 30/30 | 33 s | 22 | 否 No |
| 窗口均方差 Window MSE | 0.986 | 86% | 30/30 | 25 s | — | — |
| 逐帧相关 Frame correlation | 0.816 | 30% | 24/30 | 213 s | — | — |
| ResNet-18 特征距离 feature distance | 0.817 | 15% | 25/30 | 125 s | 3628 | 否 No |
| ViT-B/16 特征距离 feature distance | 0.777 | 9% | 14/30 | 205 s | 35126 | 否 No |

检出率与微调大模型相当；延迟是 ResNet-18 的 2/5，计算量是它的 1/270、MobileNetV3-S 的 1/8，且只有本设计能在 PL 上实时运行。
Detection is on par with fine-tuned large models; delay is 2/5 of ResNet-18's, compute is 1/270 of ResNet-18 and 1/8 of MobileNetV3-S, and only this design runs in real time on the PL.

### 板上推理 On-board inference

| 实现 Implementation | 单帧 Per frame | 本设计快 Speed-up | 单次能耗 Energy |
|---|---|---|---|
| **PL 推理核 PL core（本设计 this work）** | **1.33 ms**（含 DMA 调用 incl. DMA call） | — | **0.29 mJ** |
| 同一 QCNN 在 A53 上（定点） Same QCNN on A53, fixed-point | 33.5 ms | 25× | — |
| 现成网络在 A53 上 Off-the-shelf nets on A53 | 96～2668 ms | 70～2000× | — |
| 台式机 GPU 跑 MNet-reg Desktop GPU, MNet-reg | — | — | 48 mJ |

### 推理核优化 Inference-core optimization（XC7Z020，同一模型 same model）

| 实现 Implementation | 单帧 Per frame | DSP | BRAM_18K | 能否布局 Fits |
|---|---|---|---|---|
| 串行（优化前） Serial (before) | 18.06 ms | 7 | — | 能 Yes |
| **并行 + 层间流水（本设计） Parallel + pipelined (this work)** | **0.565 ms** | 104 / 220 | **60 / 280** | **能 Yes** |
| hls4ml，复用因子 576 reuse 576 | 5.36 ms | 106 / 220 | 603 / 280 | 超出 No |
| hls4ml，复用因子 64 reuse 64 | 2.70 ms | 915 / 220 | 1080 / 280 | 超出 No |

比串行快 32 倍；hls4ml 生成的实现 BRAM 超出器件 2～4 倍，无法布局。
32× faster than serial; hls4ml output needs 2–4× the device's BRAM and cannot be placed.

### 硬件指标 Hardware metrics（FZ3B 合并比特流 combined bitstream）

| 指标 Metric | 数值 Value | 来源 Source |
|---|---|---|
| 推理核 Core throughput | 565 µs/帧 frame @100 MHz | `sim/results/tb_qcnn_core.log` |
| 与定点模型一致 Bit-exact vs. fixed-point model | 仿真 sim 144/144，板上 board 40/40 | 同上 / board log |
| 时序 Timing | WNS +2.545 ns，WHS +0.005 ns | `build/vivado/reports/` |
| 资源 Resources | LUT 28.8%，FF 14.2%，BRAM 24.8%，DSP 30.0% | 同上 |
| 片上功耗 On-chip power | 2.99 W（Vivado 估计 estimate） | 同上 |
| 相机采集 Capture | 640×800 @149.8 fps；320×400 @299.0 fps | 板上实测 board |
| 显示 Display | 800×480 @60 Hz，Mini DP | 板上实测 board |

## 复现 Reproduce

```bash
conda env create -f environment.yml && conda activate speckle
./build/fetch_rtl_deps.sh                                   # 开源 RTL open-source RTL (verilog-axis, pinned)

./sim/rtl/run_verilator.sh tb_qcnn_core                     # 推理核 core: 144/144 bit-exact
./sim/rtl/run_verilator.sh tb_axis_frame_link               # DMA 链路 link: 72/72
python -I sim/host/test_node.py                             # 板端软件 board software: ALL PASS
./skill/tests/run_tests.sh                                  # 技能包脚本 skill scripts: ALL PASS

FSF_CAM=1 FSF_VIDEO=1 vivado -mode batch -source build/vivado/build_fz3b.tcl   # 比特流 bitstream
```

上板步骤见 `board/fz3b/README.md`；仓库已带一份构建结果 `board/fz3b/prebuilt/`（带 sha256）。
Board bring-up: `board/fz3b/README.md`. A prebuilt bitstream with sha256 is in `board/fz3b/prebuilt/`.

## 目录 Layout

按 AMD 选题指南 3.3.5.4 推荐结构。Follows the AMD guide §3.3.5.4 layout.

| 目录 Dir | 内容 Content |
|---|---|
| `src/` | RTL（相机采集、QCNN 推理核、DMA 链路、屏幕合成）、PS 侧 Python、训练与定点模型 / RTL, PS-side Python, training & fixed-point model |
| `sim/` | testbench、板端软件测试与结果日志 / testbenches, host tests, result logs |
| `build/` | 构建脚本与综合实现报告 / build scripts and implementation reports |
| `board/` | 上板说明、预编译比特流、实测输出 / bring-up, prebuilt bitstream, board output |
| `data/` | 训练好的模型、报警参数、测试数据 / trained model, alarm parameters, test data |
| `skill/` | 技能包：踩坑清单、通用校验脚本、提示词模板 / skills: pitfalls, generic check scripts, prompt templates |
| `report/` | 设计报告、架构说明与图、大模型协作记录 / design report, architecture, LLM log |
| `tools/` | 界面素材生成、上位机监视页 / UI asset generation, host monitor page |

架构说明 Architecture: [`report/architecture.md`](report/architecture.md)。各目录下有 `README.md`。Each directory has a `README.md`.
