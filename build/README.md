# build — 构建脚本与综合实现报告

## 构建

```bash
./build/fetch_rtl_deps.sh                                              # 开源 RTL，固定提交
FSF_CAM=1 FSF_VIDEO=1 vivado -mode batch -source build/vivado/build_fz3b.tcl
```

| 文件 | 作用 |
|---|---|
| `fetch_rtl_deps.sh` | 拉取 verilog-axis（MIT，`axis_fifo`）到 `refs/`，提交号固定 |
| `vivado/build_fz3b.tcl` | 整片工程：顶层 `fz3b_cam_top`，块设计 `cam_bd`，器件 `xczu3eg-sfvc784-1-i` |
| `vivado/qcnn_common.tcl` | 推理核与链路源文件、实现后统一出报告和比特流 |
| `vivado/fz3b_cam.xdc` | 相机 D-PHY 引脚与时钟约束 |
| `vivado/video_overlay_cdc.xdc` | 屏幕像素时钟与 100 MHz 之间的跨时钟域约束 |

块设计由"外壳 + 子系统"组成：每个子系统是一个 Tcl 过程，返回它需要的寄存器从口、访存主口和中断，外壳负责接到 HPM0 / HP0 / 中断，没接的时钟复位统一接 100 MHz 域。

| 地址 | 模块 |
|---|---|
| 0xA0000000 | `cam_regs`（相机控制） |
| 0xA0010000 | AXI VDMA S2MM（相机帧存） |
| 0xA0020000 | AXI DMA（推理核） |
| 0xA0030000 | `disp_regs`（PL 屏幕合成） |

产物写到 `build/vivado/fsf_fz3b/`（`.bit`、`.bin`、`.hwh`、`address_map.txt`、`reports/`）。本仓库已带一份构建结果：比特流在 `board/fz3b/prebuilt/`，报告在 `build/vivado/reports/`。

## 实现结果（`vivado/reports/`，Vivado 2026.1，2026-11-03）

| 项 | 结果 |
|---|---|
| clk_pl_0 100 MHz（AXI、DMA、推理核、屏幕取数） | WNS +3.039 ns，WHS +0.012 ns，0 个失败端点 |
| vclk（屏幕像素时钟，按 74.25 MHz 约束；800×480 实际约 33 MHz） | WNS +8.074 ns |
| LUT / FF | 20238（28.7%） / 20014（14.2%） |
| BRAM / DSP | 53.5（24.8%） / 108（30.0%） |
| 其中推理核 + DMA 链路 | LUT 10470，BRAM 11 + 35，DSP 104 |
| 片上功耗（估计） | 2.99 W |

报告文件：`utilization.rpt`、`utilization_hier.rpt`、`timing_summary.rpt`、`power.rpt`、`cdc.rpt`、`methodology.rpt`。
`cdc.rpt` 的 Critical 只在相机 D-PHY 域的配置寄存器；推理和显示路径没有。

## 换成训练好的权重

```bash
python src/model/qcnn_int.py <训练得到的 qmodel.npz> src/rtl/gen      # 写 *.mem、qcnn_params.vh、测试向量
./sim/rtl/run_verilator.sh tb_qcnn_core                                # 新权重下仍须逐位一致
FSF_CAM=1 FSF_VIDEO=1 vivado -mode batch -source build/vivado/build_fz3b.tcl
```

推理核结构参数（层宽、并行度、移位量）全部来自 `src/rtl/gen/qcnn_params.vh`，RTL 不用改。
