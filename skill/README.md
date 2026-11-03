# skill — 技能包

从本作品开发过程中提炼、换题目和换板卡仍可用的经验。每项写明适用场景、用法、已验证效果、失效条件和来源。
脚本只依赖 Python 标准库（`compare_golden.py` 另需 numpy），不读本作品的任何文件，参数里给路径即可用在别的工程上。

| # | 类型 | 内容 | 与本题目无关的适用范围 |
|---|---|---|---|
| 1 | 案例模板 + 校验脚本 | 定点 CNN 参考模型导出 → RTL 逐位比对；`scripts/compare_golden.py` | 任何手写 RTL 的定点算法 |
| 2 | 踩坑清单 | 必须做综合后网表仿真的情形 | 任何用 Vivado 综合存储器初始化的设计 |
| 3 | 踩坑清单 | 不依赖 PYNQ 的 AXI DMA 用户态驱动 | Zynq-7000 / ZynqMP 上的 AXI DMA simple mode |
| 4 | 脚本 | ZynqMP 上不用 libdrm 设置任意显示模式 | ZynqMP DPSUB 输出 |
| 5 | 代码模板 | 小屏实时数值显示的防抖 | 任何嵌入式屏幕 |
| 6 | 校验脚本（PYNQ 通用） | `scripts/hwh_map.py`：无 PYNQ 时从 `.hwh` 读地址表和 IP 参数 | 任何 Vivado 块设计、任何 Zynq 板卡 |
| 7 | 校验脚本 | `scripts/check_vivado_reports.py`：从报告提取时序/资源/功耗，按门限判定 | 任何 AMD 器件的布线后报告 |
| 8 | 提示词工作流 | `prompts/README.md`：软硬件划分、报告定位瓶颈、先复现再修改、数据泄漏自查 | 任何 FPGA 软硬件协同题目 |

## 1. 定点 CNN 的"参考模型导出 → RTL 逐位比对"流程（案例模板 + 校验脚本）

- 适用：任何手写 RTL 的定点神经网络或流水线算法，PYNQ / Zynq / 纯 PL 都可。
- 做法：用一个整数参考模型（`src/model/qcnn_int.py`）同时导出三样东西：权重 ROM（`.mem`）、形状与移位参数头文件（`qcnn_params.vh`）、测试向量（输入、期望输出、帧 0 每层特征图）。
  RTL 的结构参数全部从头文件读，testbench 按层比对特征图、按帧比对输出（`sim/rtl/tb_qcnn_core.v`）。换模型只需重新导出，RTL 不改。
- 用法：`python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen <inputs.npy>`，然后 `./sim/rtl/run_verilator.sh tb_qcnn_core`。
- 已验证：ΔT 形状（2×56² → 9）144/144 逐位一致（Verilator，`sim/results/tb_qcnn_core.log`）；前期单帧形状下 RTL 与综合后网表均与参考模型一致。
- 失效条件：参考模型必须与训练时的量化规则完全一致（舍入方式、截断位置）；浮点模型不能当参考。
- 来源：权重最初在 RTL 的 `initial` 块里按 lane 切片，仿真正常、综合后权重全为 0，而时序和资源报告看不出异常（见第 2 项）。改为由 Python 参考模型按 lane 导出 `.mem`，硬件只读文件，参考模型因此成为唯一的参数来源。

## 2. 必须做综合后网表仿真的情形（踩坑清单）

| 现象 | 触发条件 | 排查 |
|---|---|---|
| RTL 仿真通过，综合后权重全为 0，时序和资源报告无异常 | `initial` 块里用非常量表达式初始化存储器（循环里按参数切片），综合直接忽略 | `./sim/rtl/run_postsynth.sh` 跑整核网表；`run_xsim_layer.sh <n> postsynth` 逐层定位；存储器内容改由外部 `.mem` 文件给出 |
| 网表仿真开头数据丢失 | 门级仿真前 100 ns 有全局复位（GSR） | testbench 复位持续到 GSR 释放后再送数据 |
| BRAM 用量异常高（一度 139/140） | 偏置 ROM 地址按计数器位宽推断（`bias_rom[g*PAR+p]` 用 8 位计数器推成 8192 深），每路占一块 BRAM | 地址位宽按实际深度（`C_OUT`）声明；看 `utilization_hier.rpt` 定位到具体存储器 |

- 失效条件：使用 Vivado 以外的综合器时，忽略 `initial` 的规则可能不同，需要按该工具文档核对。

## 3. 不依赖 PYNQ 的 AXI DMA 用户态驱动（`/dev/mem`）踩坑清单

适用：板上镜像没有 PYNQ 或版本太老（本作品：Linux 4.14、Python 3.6、numpy 1.13），需要直接驱动 AXI DMA simple mode（PG021）。代码：`src/host/qcnn_dma.py`。

| 现象 | 原因 | 处理 |
|---|---|---|
| 读结果时进程 SIGBUS，dmesg 有 alignment fault（板上 2026-11-02 遇到） | `/dev/mem` O_SYNC 映射是 Device 内存，`ndarray.copy()` / libc memcpy 用非对齐 16 字节读 | 结果区逐个 32 bit 字读出 |
| 启动后 MM2S 停住，`MM2S_CR = 0x10002`（板上 2026-11-02 遇到） | 任一通道软复位会复位整个 DMA；先启动 MM2S 再复位 S2MM 会把 MM2S 也停掉 | 只复位一次，等两个通道的复位位都自清，再依次置 RUN |
| （预防）结果在接收通道就绪前到达 | 先启动发送、后布置接收 | 先写 S2MM 地址和长度，再写 MM2S（本作品一直按此顺序，未遇到问题） |

- 缓冲区放在设备树 no-map 保留区内，O_SYNC 映射为非缓存，非一致性 HP 口不需要做缓存维护；换板时先查设备树里哪些区域是 no-map。
- 失效条件：使用 scatter-gather 模式或带 IOMMU 的内核时不适用。
- 验证：`sim/host/test_node.py` 用按 PG021 行为模拟的寄存器文件测试；板上连续运行 2 万步以上无错误（`board/fz3b/results/`）。

## 4. ZynqMP 上不用 libdrm 设置任意显示模式（脚本）

- 适用：Zynq UltraScale+ DPSUB 输出，显示器或转接头的 EDID 不包含想要的分辨率（本作品：转接头只报 1080p，屏是 800×480）。
- 用法：`python3 board/fz3b/drm_mode_test.py --mode 800x480 --secs 600`。脚本用 ioctl 直接创建 dumb buffer、ADDFB、SETCRTC；退出后内核自动恢复控制台模式，不留痕迹。
  `src/host/fsf_ps_ui.py` 的 `DrmOut` 在此基础上做双缓冲。
- 注意：xlnx DPSUB 主平面只接受 RGB565，XRGB8888 会被拒绝；拿 DRM master 前要停掉占用显示的 Xorg。
- 失效条件：内核不是 xlnx_drm 驱动，或已有进程持有 DRM master。

## 5. 小屏实时数值显示的防抖（代码模板）

- 适用：任何嵌入式屏幕上显示带噪声的测量值、状态和趋势图。
- 做法：`src/host/ui_filter.py` 五个独立小类：单点尖峰保留一拍再确认、两步间数值缓动、末位滞回、状态开/关不同阈值和保持时间、坐标轴立即放大延迟缩小。只作用于显示，告警只允许延后结束、不允许延后开始。
- 已验证：`sim/host/test_ui_filter.py` 19 项；噪声 ±0.02 下末位翻转从 96 次降到 4 次。
- 失效条件：数据步长远大于缓动时间常数时，缓动无意义，可关掉。

## 6. 无 PYNQ 时从 `.hwh` 读地址表（PYNQ 通用校验脚本）

- 适用：PYNQ 的 `Overlay` 靠 `.hwh` 生成 `ip_dict`。板上没有 PYNQ、Python 太老（本作品 3.6），或想在 PC 上先检查比特流对应的地址时，用这个脚本得到同一张表。换题目、换板卡都可用。
- 用法：
  ```bash
  python3 -I skill/scripts/hwh_map.py <design>.hwh                    # 实例、类型、基地址、大小
  python3 -I skill/scripts/hwh_map.py <design>.hwh --ip <实例名> --grep sg   # 查某个 IP 的参数，例如 DMA 是否带 SG
  python3 -I skill/scripts/hwh_map.py <design>.hwh --json map.json   # 给驱动读，不再手写基地址
  ```
- 已验证：对本作品的 `board/fz3b/prebuilt/fsf_fz3b.hwh` 输出 4 个地址段（`M_REGS`、`vdma`、`temp_dma`、`ovl`），与 Vivado 写出的 `address_map.txt` 一致；`--ip temp_dma --grep sg` 能看到 `c_include_sg`，可确认 DMA 是 simple mode（第 3 项的前提）。
- 失效条件：`.hwh` 必须与板上加载的比特流同一次构建；块设计外的手写 AXI 从机不在 `.hwh` 里，脚本看不到；只列 AXI-Lite 寄存器窗口，不列 DDR 等存储窗口。
- 来源：本作品有不带相机和带相机（`FSF_CAM=1`）两种构建，推理 DMA 的基地址不同（`speckle_node.py` 的 `--dma-base` 默认值只对后者成立）。节点服务因此改为启动时读构建产出的地址表（`--address-map`）；板上镜像没有 PYNQ，读 `.hwh` 的部分整理成了这个脚本。

## 7. 布线后报告的自动判定（校验脚本）

- 适用：任何 AMD 器件、任何工程的布线后报告（`report_timing_summary`、`report_utilization`、`report_power` 默认格式）。
- 用法：
  ```bash
  python3 -I skill/scripts/check_vivado_reports.py <reports 目录> --max-bram 80 --max-dsp 80 [--json metrics.json]
  ```
  输出 Markdown 指标表（WNS/TNS/WHS/THS、各时钟频率、LUT/FF/BRAM/URAM/DSP、片上功耗），最后一行 `RESULT PASS` 或 `RESULT FAIL: 原因`，失败时退出码 1，可直接放进构建脚本或 CI。
- 已验证：对本作品 `build/vivado/reports/` 输出 WNS +2.545 ns、WHS +0.005 ns、LUT 28.80%、BRAM 24.77%、DSP 30.00%、2.991 W，与报告原文一致，`RESULT PASS`。
- 失效条件：只看报告里的数字。`report_cdc` 里的 Critical 项（异步时钟组之间的跨时钟域）需要人工逐条确认，脚本不判定；报告格式改动较大的 Vivado 版本需要核对正则。
- 来源：设计报告和 README 的指标表都取自这几份报告，用脚本提取可避免手抄；BRAM 139/140 那次（第 2 项）时序是满足的，只看时序会漏掉，所以资源门限和时序一起判定。

## 8. 提示词工作流

见 `prompts/README.md`：四个模板（从算法描述推导软硬件划分、根据报告定位瓶颈、板上故障先复现再修改、评估前的数据泄漏自查），每个写了本作品里的用法、效果和不适用的情形。

## 第 1 项的通用比对脚本

```bash
python3 -I skill/scripts/compare_golden.py <硬件输出 .mem/.npy/.txt> <参考 .mem/.npy> --width 32 --signed --shape 16,9
```

逐字比对，打印前 10 个不一致的位置（给 `--shape` 时显示多维下标，例如 `(帧, 输出)`），全部一致时输出 `RESULT n/n match`，否则退出码 1。
已验证：`src/rtl/gen/tv_output.mem` 自比对 144/144；人为改一个字后报告 `MISMATCH (0, 4)`、143/144 并返回 1。
