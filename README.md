# fiber_speckle_fpga

FPGA 流式多模光纤散斑解调，面向液冷管道温度、振动、泄漏监测。
目标赛事：2026 全国大学生嵌入式芯片与系统设计竞赛 · AMD FPGA 赛道 · 自主选题初级组。

## 平台

| 板卡 | 器件 | 用途 | 状态 |
|---|---|---|---|
| **EdgeBoard FZ3B**（主平台） | Zynq UltraScale+ ZU3EG（XAZU3EG-1SFVC784I），4 GB DDR4 | 温度推理；预留声学分支与 DisplayPort 3D 显示 | 比特流已生成，待上板 |
| PYNQ-Z2（备选） | Zynq-7000 XC7Z020 | 同一份推理 RTL，UART 版与 DMA 版 | 比特流已生成，待上板 |

工具：Vivado / Vitis **2026.1**（赛事推荐版本）。

## 当前进度：T1（公开数据集温度推理）

```
电脑 qcnn_host.py ──网线 TCP──► 板上 frame_server.py ──AXI DMA──► PL: QCNN 推理核 ──► 温度
```

| 项 | 结果 |
|---|---|
| 精度（dataset 14，训练与选型全程不接触） | 定点 QCNN RMSE **2.12 °C**；浮点 MNet-reg 1.73 °C |
| 推理核速度（仿真，100 MHz） | 0.565 ms/帧，约 1770 帧/秒 |
| 验证 | RTL 与综合后网表仿真均与 Python 定点模型逐位一致 |
| 与 hls4ml 对比 | 同一模型 hls4ml 版在 XC7Z020 上放不下，且慢 4.8–9.5 倍 |

详见 `report/results_2026-09-16.md`。

## 复现

```bash
conda env create -f environment.yml && conda activate speckle
./build/fetch_rtl_deps.sh                                   # 开源 RTL（verilog-uart / verilog-axis）
# 数据：见 data/README.md，下载后
python src/model/prepare_dataset.py
python src/model/train_qcnn.py --widths 16,32,64,64 --act-bits 12
python src/model/qcnn_int.py runs/qcnn_in56_w16-32-64-64_a12_seed0/qmodel.npz src/rtl/gen 16

./sim/rtl/run_xsim.sh tb_qcnn_core                          # RTL 与定点模型逐位比对
./sim/rtl/run_postsynth.sh 1                                # 综合后网表仿真

vivado -mode batch -source build/vivado/build_fz3b.tcl      # FZ3B 比特流
vivado -mode batch -source build/vivado/build_pynq_z2.tcl -tclargs dma   # PYNQ-Z2（或 uart）
```

上板步骤：FZ3B 见 `board/fz3b/README.md`；主机程序见 `src/host/README.md`。

## 路线

| 阶段 | 数据源 | 内容 | 状态 |
|---|---|---|---|
| T1 | 公开温度散斑数据集 | 训练、量化、Verilog 推理核、通信链路、主机程序 | 仿真与构建完成，待上板 |
| T2 | 自采温度散斑 | 激光 + 多模光纤 + 相机 + 控温台 | 未开始 |
| V1 | 自采振动 | 线阵传感器 / 光电二极管 + ADC，一维 CNN 声学分支 | 块设计已留接入点 |
| 显示 | — | DisplayPort 输出管道 3D 状态 | 块设计已留接入点 |

## 目录

```
src/model/       训练、量化、定点参考模型
src/rtl/         Verilog：qcnn（推理核）、link（UART / AXI-Stream）、top、gen（生成的权重与参数）
src/host/        主机与板端程序（帧协议、发送接收、板端 DMA 服务）
sim/             RTL / 网表 / 主机程序的测试
build/           Vivado 构建脚本与综合实现报告
board/           各板卡上板说明与共享的预编译文件（board/<板卡>/prebuilt/）
report/          设计报告、阶段结果、大模型协作记录
docs/            方案、赛题指南、器件手册
data/            数据集说明与下载脚本（数据本体不入库）
```

## 约定

- 目录和文件名只用小写英文、数字、下划线、连字符（赛事要求）
- 大模型协作记录放 `report/llm_log/`
- 比特流等二进制文件只在 `board/<板卡>/prebuilt/` 里入库
