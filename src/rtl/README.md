# src/rtl — PL 设计（Verilog-2001）

```
OV9281 ─► cam/ ─AXIS─► VDMA ─► DDR 帧存
                                  │（PS 取帧、预处理）
PS ─AXI DMA─► link/axis_frame_link ─► qcnn/qcnn_core ─► link/axis_frame_link ─AXI DMA─► PS
DDR 背景 + 帧存 ─► video/（屏幕合成）─► DPSUB live 视频层 ─► Mini DP
```

## qcnn/ — ΔT 推理核

```
loader ─► conv0 ─► conv1 ─► conv2 ─► conv3 ─► head
2×56²     28²×16   14²×32   7²×64    4²×64    GAP → FC 64→16 → FC 16→9   （12 bit 激活）
```

输入每帧两个平面：参考帧 56² + 当前帧 56²；输出 9 个 int16 码，`ΔT = 码 × dt_lsb`（节点服务里设置，默认 1/1024 °C）。

| 模块 | 作用 |
|---|---|
| `qcnn_frame_loader.v` | 字节流 → 输入 RAM |
| `qcnn_conv_layer.v` | 3×3 stride-2 卷积 + 移位重量化 + 截断；输出通道按 `PAR` 路并行（共享输入读，每路独立权重 ROM 和乘法器），4 级流水 |
| `qcnn_mem.v` | 同步 ROM / 简单双口 RAM，`keep_hierarchy` 防止读寄存器被 DSP 吸收 |
| `qcnn_head.v` | GAP + 两层 FC，共用一个乘加流水 |
| `qcnn_core.v` | 串联各级；每级自带输出 RAM，帧逐级前进，多帧同时在途；中间特征图不经 DDR |

级间握手：`src_valid / src_taken`、`dst_valid / dst_taken`。一级在"上游有帧且自己输出为空"时启动，读完上游后发 `src_taken`。

## gen/ — 导出的参数（不要手改）

由 `python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen <inputs.npy>` 生成：

| 文件 | 内容 |
|---|---|
| `qcnn_params.vh` | 形状（`IN_CH IN_H N_OUT`、各层通道数）、并行度 `PAR*`、各层移位量 |
| `l*_weight_lane*.mem`、`l*_bias.mem`、`l*_weight.mem` | 卷积按 lane 拆分的权重 ROM、偏置、FC 权重 |
| `tv_input.mem`、`tv_output.mem`、`tv_frame0_l*.mem` | 测试向量：16 帧输入、期望输出、帧 0 各层特征图 |

当前内容由训练好的报警模型 `data/fusion/qmodel_win.npz` 导出，测试向量是 16 组实测窗口平均图样（`data/fusion/tv_pairs_win_16x2x56x56.npy`）；重新训练后的导出步骤见 `build/README.md`。

## link/ — DMA 帧链路

`axis_frame_link.v`：AXI-Stream（AXI DMA MM2S）字节流 → 推理核输入；结果每帧回 `N_OUT` 个 32 bit 字到 S2MM，批末帧的最后一个字带 tlast。
一次 DMA 可以送多帧（批次），推理核按帧流水。

## cam/ — 相机采集

| 模块 | 作用 |
|---|---|
| `dphy_comp_rx.v` | D-PHY 高速接收（lane 0），字节对齐 |
| `csi2_rx_lite.v`、`csi2_l0_only.v` | CSI-2 包解析，RAW8 行 → AXI-Stream video |
| `cam_regs.v` | 控制与状态寄存器（0xA0000000） |
| `fz3b_cam_top.v` | 整片顶层，例化块设计 `cam_bd`（端口 `aclk / aresetn / M_REGS / S_VIDEO`） |

约束 `build/vivado/fz3b_cam.xdc`。PS 侧由 `board/fz3b/cam_service/`（`cam.py` 配置传感器和 VDMA，`stream.py` 提供 `/raw` 取帧）使用。

## video/ — 800×480 屏幕合成

```
aclk 100 MHz                                          vclk（DP 像素时钟）
disp_regs (AXI-Lite) ── commit/ack toggle ──►  vid_timing（运行时模式）
line_fetch u_bg  (m_axi,     RGBW8888 背景)  ─┐
line_fetch u_cam (m_axi_cam, VDMA 帧存)      ─┼► disp_compose ─► DPSUB live 视频层
剖面 / 文字 RAM / 调色板 (disp_regs)          ─┘
```

| 模块 | 作用 |
|---|---|
| `disp_regs.v` | 寄存器、256 点沿程温度剖面 RAM、4096 格文字 RAM；暂存寄存器在下一个 vblank 整体生效 |
| `vid_timing.v` | 时序发生器，分辨率运行时设置（`src/host/dp_live.py` 从 DP 控制器读出模式写入） |
| `line_fetch.v` | 每行从 DDR 取一行进乒乓行缓存；背景整行、相机 384 px，相机取刚写完的那一帧 |
| `disp_compose.v` | 背景 + 水体权重 × 色表[剖面]、相机灰度、12×24 抗锯齿文字、测试彩条 |
| `video_overlay_top.v` | 例化上面四个 |
| `disp_ref.py` | 逐像素参考模型（`check` 子命令比对仿真抓帧） |
| `heat_lut.mem`、`font12x24.mem` | 色表与字形 ROM（`tools/ui/build_ui.py` 生成） |

跨时钟域只走 toggle 同步、保持总线或 FIFO，约束见 `build/vivado/video_overlay_cdc.xdc`。

## 编码约定

- 控制逻辑两段式：`always @*` 算 `*_d`，`always @(posedge clk)` 打拍到 `*_q`；纯流水线寄存器和存储器在时序块里直接写
- 前缀：`s_axis_* / m_axis_*` 流接口，`src_* / dst_*` 级间接口，`ST_*` 状态
- 复用的开源 IP：verilog-axis `axis_fifo`（MIT，`build/fetch_rtl_deps.sh` 固定版本）；Xilinx `axi_dma`、`axi_vdma`、`zynq_ultra_ps_e`
