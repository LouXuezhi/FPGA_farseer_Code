# board/fz3b — EdgeBoard FZ3B 上板说明

FZ3B：Zynq UltraScale+ ZU3EG，4 GB DDR4（64 bit），8 GB eMMC，SD 启动，ALINX 制板。
当前只做温度（公开数据集回放）；声学与 3D 显示的接入点见最后一节。

## 数据通路

```
loulinux: qcnn_host.py ──TCP/GbE──► FZ3B Linux: frame_server.py --backend mmio
                                        │ /dev/mem 驱动 AXI DMA（不依赖 PYNQ）
                                        ▼
                    PL: temp_dma ─► axis_frame_link / qcnn_core ─► temp_dma ─► DDR
```

PL 设计与 PYNQ-Z2 DMA 版是同一份 RTL（`src/rtl`），只换了 PS（`zynq_ultra_ps_e`）和互连。

## 1. 构建（loulinux）

先确认芯片丝印，决定器件型号（资料里出现过两种）：

| 丝印 | `-tclargs` |
|---|---|
| XAZU3EG-1SFVC784I（车规，默认） | 不用加 |
| XCZU3EG-1SFVC784E | `xczu3eg-sfvc784-1-e` |

```bash
vivado -mode batch -source build/vivado/build_fz3b.tcl [-tclargs xczu3eg-sfvc784-1-e]
```

产物在 `build/vivado/fz3b/`：`qcnn_fz3b.bit / .bin / .hwh`、`address_map.txt`（DMA 寄存器基址）、`reports/`。

## 2. 板上准备（一次性）

1. **JTAG**：板上没有 USB-JTAG 芯片，需外接 Xilinx / Digilent JTAG 下载器（只在调试 ILA 时需要；日常用 Linux 加载比特流）。
2. **预留 1 MB 物理内存**给 DMA 缓冲区，Linux 不能占用。在设备树加：
   ```dts
   reserved-memory {
       #address-cells = <2>; #size-cells = <2>; ranges;
       qcnn_buf: qcnn@7ff00000 { reg = <0x0 0x7ff00000 0x0 0x100000>; no-map; };
   };
   ```
   地址要落在低 2 GB DDR 内（ZynqMP 低段 DDR 为 `0x0000_0000–0x7FFF_FFFF`），与 `--buf-phys` 一致。
3. **PL 时钟**：比特流按 `pl_clk0 = 100 MHz` 收敛时序。加载前确认 Linux 设置的 PL0 频率不高于 100 MHz：
   `cat /sys/kernel/debug/clk/pl0_ref/clk_rate`

## 3. 运行

```bash
# 板上
sudo cp qcnn_fz3b.bin /lib/firmware/
sudo fpgautil -b /lib/firmware/qcnn_fz3b.bin          # 或 echo qcnn_fz3b.bin > /sys/class/fpga_manager/fpga0/firmware
sudo python3 frame_server.py --backend mmio --dma-base <address_map.txt 中 temp_dma 的基址> --buf-phys 0x7FF00000

# loulinux
python src/host/qcnn_host.py --link tcp --addr <板子 IP>:5000 --window 16
```

板上需要的文件：`frame_server.py`、`frame_protocol.py`、`qcnn_fz3b.bin`，以及 Python 3 + numpy。

## 已验证 / 未验证

| 项 | 状态 |
|---|---|
| RTL（与 PYNQ-Z2 同一份）RTL 仿真、综合后网表仿真 | 已通过（Vivado 2026.1） |
| `MmioDmaBackend` 寄存器时序（`sim/host/test_mmio_backend.py`，按 PG021 行为的假 DMA） | 200/200 一致 |
| FZ3B 块设计生成、综合实现 | 待 Vivado 装上 Zynq UltraScale+ 器件 |
| 上板 | 待板卡 |

## 扩展点（声学、3D 显示）

`build/vivado/build_fz3b.tcl` 的块设计由"外壳 + 子系统"组成。每个子系统是一个 Tcl 过程，返回它需要的寄存器从口、访存主口、中断；外壳负责连到 HPM0 / HP0 / 中断，并把没接的时钟复位接到 100 MHz 域。加新功能只需新写一个过程并加入 `subsystems` 列表：

| 计划的子系统 | 内容 | 需要补的 |
|---|---|---|
| `acoustic_path` | 线阵传感器 / 光电二极管 → 外接 ADC（经 44 针扩展口）→ 一维 CNN → DMA | ADC 接口 RTL；扩展口引脚约束（按原理图） |
| `video_path` | PL 着色结果 → DisplayPort 实时视频输入（PS 的 DP 控制器）；或由 Mali-400 GPU 在 PS 侧渲染 | 打开 PS 的 DP 实时视频输入与视频时钟；GPU 路线需确认镜像里有 libmali |

PL 资源估计（温度核占用）：LUT 约 16%、DSP 约 29%、BRAM 约 14%，余量足够容纳上表两项。
