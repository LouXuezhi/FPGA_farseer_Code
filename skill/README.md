# skill — 技能包

从本作品开发过程中提炼、换题目和换板卡仍可用的经验。每项写明适用场景、用法、失效条件和来源。

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
