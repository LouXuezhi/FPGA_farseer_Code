# skill — 技能包 Skills

从开发过程中提炼、**换题目、换板卡仍可用**的经验。脚本不读本作品的文件，给路径就能用。
Lessons from this project that **still apply to other topics and boards**. Scripts take paths as arguments and read nothing from this project.

## 通用性实测 Portability (tested)

`./skill/tests/run_tests.sh` 用另一块板的真实构建产物自测（样例在 `skill/tests/samples/`），15 项全部通过。
Self-test on real build outputs from a different board (samples in `skill/tests/samples/`): 15/15 pass.

| 技能 Skill | Zynq-7000 xc7z020（PYNQ-Z2 级）| ZynqMP xczu3eg（本作品）| Vivado 版本 versions | 依赖 Deps |
|---|---|---|---|---|
| 6 `hwh_map.py` | 已测 tested | 已测 tested | 2025.1 / 2026.1 | 标准库 stdlib，Python ≥3.5 |
| 7 `check_vivado_reports.py` | 已测 tested | 已测 tested | 2017.4 / 2025.1 / 2026.1 | 标准库 stdlib |
| 1 `compare_golden.py` | 与器件无关 device-independent | 已测 tested | — | numpy |
| 3 AXI DMA 驱动踩坑 | 未上板；寄存器偏移相同（PG021），按 `.hwh` 换基地址即可 not run; same PG021 offsets | 已测 tested | — | numpy |
| 4 DRM 显示模式 | 不适用（无 DPSUB） N/A | 已测 tested | — | 标准库 stdlib |
| 2、5、8 | 与器件无关 device-independent | | | |

Versal 和纯 FPGA（无 PS）没有样例，未测。Versal and PS-less FPGAs have no samples and are untested.

## 目录 Index

| # | 类型 Type | 内容 Content |
|---|---|---|
| 1 | 案例模板 + 校验脚本 | 定点 CNN：参考模型导出 → RTL 逐位比对 |
| 2 | 踩坑清单 | 必须做综合后网表仿真的情形 |
| 3 | 踩坑清单 | 不依赖 PYNQ 的 AXI DMA 用户态驱动 |
| 4 | 脚本 | ZynqMP 上不用 libdrm 设置任意显示模式 |
| 5 | 代码模板 | 小屏数值显示防抖 |
| 6 | 校验脚本（PYNQ 通用） | 无 PYNQ 时从 `.hwh` 读地址表和 IP 参数 |
| 7 | 校验脚本 | 布线后报告自动判定（时序、资源、功耗） |
| 8 | 提示词工作流 | `prompts/README.md`，四个模板 |

---

## 1. 定点 CNN 参考模型导出 → RTL 逐位比对

- **适用**：任何手写 RTL 的定点算法。
- **做法**：整数参考模型（`src/model/qcnn_int.py`）同时导出权重 `.mem`、参数头文件 `qcnn_params.vh`、测试向量；RTL 只读这些文件，换模型不改 RTL。
- **用法**：
  ```bash
  python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen <inputs.npy>
  ./sim/rtl/run_verilator.sh tb_qcnn_core
  python3 -I skill/scripts/compare_golden.py <硬件输出> <参考.mem> --signed --shape 16,9   # 任何工程通用
  ```
- **已验证**：144/144 逐位一致；人为改一个字时报出 `MISMATCH (0, 4)` 并返回 1。
- **失效**：参考模型的舍入、截断必须与训练时一致；浮点模型不能当参考。
- **来源**：权重曾在 `initial` 块里切片，综合后全为 0（见第 2 项）。

## 2. 必须做综合后网表仿真的情形

| 现象 | 原因 | 处理 |
|---|---|---|
| RTL 仿真通过，综合后权重全 0，报告无异常 | `initial` 块里非常量初始化，综合忽略 | 跑网表仿真（`sim/rtl/run_postsynth.sh`）；存储器改用 `$readmemh` |
| 网表仿真开头丢数据 | 前 100 ns 全局复位 GSR | 复位持续到 GSR 释放后 |
| BRAM 用量异常（一度 139/140） | ROM 地址按计数器位宽推成 8192 深 | 地址位宽按实际深度声明，看 `utilization_hier.rpt` |

- **失效**：非 Vivado 综合器规则可能不同。

## 3. 不依赖 PYNQ 的 AXI DMA 用户态驱动（`/dev/mem`）

代码 `src/host/qcnn_dma.py`，PG021 simple mode。

| 现象 | 原因 | 处理 |
|---|---|---|
| 读结果 SIGBUS，dmesg alignment fault | O_SYNC 映射是 Device 内存，`memcpy` 非对齐读 | 逐个 32 bit 字读出 |
| MM2S 停住，`MM2S_CR = 0x10002` | 任一通道软复位会复位整个 DMA | 只复位一次，等复位位自清再置 RUN |
| 结果先于接收通道就绪 | 先发后收 | 先写 S2MM 地址长度，再写 MM2S |

- 缓冲区放设备树 no-map 保留区，非一致性 HP 口不用做缓存维护。
- **失效**：scatter-gather 模式、带 IOMMU 的内核。可先用第 6 项查 `c_include_sg = 0` 确认是 simple mode。

## 4. ZynqMP 上不用 libdrm 设置任意显示模式

- **适用**：DPSUB 输出，EDID 没有想要的分辨率（本作品转接头只报 1080p，屏是 800×480）。
- **用法**：`python3 board/fz3b/drm_mode_test.py --mode 800x480 --secs 600`，ioctl 直接建 dumb buffer、ADDFB、SETCRTC。
- **注意**：xlnx DPSUB 主平面只接受 RGB565；先停 Xorg。
- **失效**：非 xlnx_drm 驱动，或已有进程持有 DRM master。

## 5. 小屏数值显示防抖

- **做法**：`src/host/ui_filter.py` 五个小类：尖峰确认、数值缓动、末位滞回、状态迟滞、坐标轴粘滞。告警只允许延后结束。
- **已验证**：19 项测试；噪声 ±0.02 下末位翻转从 96 次降到 4 次。

## 6. 无 PYNQ 时从 `.hwh` 读地址表

- **适用**：PYNQ 的 `Overlay` 用 `.hwh` 生成 `ip_dict`；板上没有 PYNQ 或 Python 太老时，用它得到同一张表。
- **用法**：
  ```bash
  python3 -I skill/scripts/hwh_map.py <design>.hwh                          # 实例 / 类型 / 基地址 / 大小
  python3 -I skill/scripts/hwh_map.py <design>.hwh --ip <实例> --grep sg     # 查 IP 参数
  python3 -I skill/scripts/hwh_map.py <design>.hwh --json map.json         # 给驱动读
  ```
- **已验证**：ZU3EG 上 4 个地址段与 `address_map.txt` 一致；xc7z020 上读出 `dma axi_dma 0x40400000`。
- **失效**：`.hwh` 须与板上比特流同一次构建；块设计外的手写从机看不到；只列寄存器窗口。
- **来源**：本作品两种构建的 DMA 基地址不同，节点服务因此改为读构建产出的地址表。

## 7. 布线后报告自动判定

- **用法**：`python3 -I skill/scripts/check_vivado_reports.py <reports 目录> --max-bram 80 [--json m.json]`
  输出指标表，末行 `RESULT PASS` 或 `RESULT FAIL: 原因`，失败退出码 1，可放进构建脚本或 CI。
- **已验证**：3 个器件/版本组合数值与报告原文一致；构造的建立时间违例和资源超限都判为 FAIL。
- **失效**：不判定 `report_cdc` 的 Critical 项，需人工确认。
- **来源**：BRAM 139/140 那次时序是满足的，只看时序会漏，所以资源和时序一起判。

## 8. 提示词工作流

见 `prompts/README.md`：软硬件划分、根据报告定位瓶颈、板上故障先复现再修改、评估前数据泄漏自查。
