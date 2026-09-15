# src/rtl — QCNN 推理核与通信链路（Verilog-2001）

## 数据通路

```
             ┌──────────── PL-only (top_pynq_z2_uart) ────────────┐
loulinux ──USB-UART 3 Mbps──► uart_frame_link ─► qcnn_core ─► uart_frame_link ──► loulinux

             ┌──────── PS + PL (build_pynq_z2.tcl dma) ───────────┐
loulinux ──TCP/GbE──► PS: frame_server.py ─AXI DMA─► axis_frame_link ─► qcnn_core ─► DMA ─► PS ──TCP──► loulinux
```

帧协议两条链路一致（`src/host/frame_protocol.py`）：请求 `A5 5A id pixel×3136`，应答 `5A A5 id result(int16 LE)`，T = 25 + 175·result/4096。
网口走 TCP 而非 UDP：Zynq-7000 GEM 不支持巨帧，3139 字节的帧会被 IP 分片。

## qcnn_core

```
loader ─► conv0 ─► conv1 ─► conv2 ─► conv3 ─► head
56²×1     28²×16   14²×32   7²×64    4²×64    GAP → FC64→16 → FC16→1   (12 bit 激活)
```

| 模块 | 作用 |
|---|---|
| `qcnn_frame_loader` | 字节流 → 输入 RAM |
| `qcnn_conv_layer` | 3×3 卷积 + 移位重量化 + 无符号截断；输出通道按 `PAR` 路并行（共享输入读、每路独立权重 ROM 与乘法器），4 级流水 |
| `qcnn_mem` | 同步 ROM / 简单双口 RAM，`keep_hierarchy` 防止读寄存器被 DSP 吸收 |
| `qcnn_head` | GAP + 两层 FC，共用一个乘加流水 |
| `qcnn_core` | 串联各级；每级自带输出 RAM，帧逐级前进（多帧同时在途） |

级间握手：`src_valid / src_taken`、`dst_valid / dst_taken`。一级在"上游有帧且自己输出为空"时启动，读完上游后发 `src_taken`。

参数来自 `gen/`（`python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen`）：权重/偏置 ROM `.mem`、移位量 `qcnn_params.vh`、测试向量。

## 编码约定

- 控制逻辑两段式：`always @*` 计算 `*_d`，`always @(posedge clk)` 打拍到 `*_q`
- 纯流水线寄存器（`sN_*_q`）和存储器在时序块里直接写，不另设 `_d`
- 前缀：`s_axis_* / m_axis_*` 流接口，`src_* / dst_*` 级间接口，`ST_* / RX_* / TX_*` 状态
- 复用开源 IP（MIT，`build/fetch_rtl_deps.sh` 拉取固定版本）：verilog-uart `uart`，verilog-axis `axis_fifo`；Xilinx `axi_dma`、`processing_system7`、`MMCME2_BASE`

## 仿真与构建

```bash
./build/fetch_rtl_deps.sh
./sim/rtl/run_xsim.sh tb_qcnn_core          # 16 帧结果 + 帧 0 各层特征图，对比 Python 定点模型
./sim/rtl/run_xsim.sh tb_uart_frame_link    # UART 3 Mbps 连发 4 帧
./sim/rtl/run_xsim.sh tb_axis_frame_link    # DMA 式批次 + 随机反压
vivado -mode batch -source build/vivado/build_pynq_z2.tcl -tclargs uart   # 或 dma
```
