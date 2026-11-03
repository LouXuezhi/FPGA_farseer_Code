# sim — 仿真与验证

功能正确性按"定点参考模型为准、硬件逐位比对"确认：`src/model/qcnn_int.py` 同时导出权重 ROM、移位参数和测试向量，RTL 仿真的输出必须与它逐位相同；
屏幕合成同理，以 `src/rtl/video/disp_ref.py` 的逐像素结果为准。

## RTL（`sim/rtl/`）

| testbench | 被测 | 判定 |
|---|---|---|
| `tb_qcnn_core.v` | 整个推理核 `qcnn_core` | 16 帧 × 9 输出与 `tv_output.mem` 逐位一致；帧 0 每层特征图与 `tv_frame0_l*.mem` 一致；输出首帧延迟和稳态帧间隔 |
| `tb_axis_frame_link.v` | `axis_frame_link` + 推理核（DMA 侧接口） | 多帧批次、随机反压下数据与 tlast 位置正确 |
| `tb_conv_layer.v` / `conv_layer_dut.v` | 单个卷积层 | RTL 或综合后网表，与参考特征图一致 |
| `tb_head.v` / `head_dut.v` | GAP + 两层 FC | 同上 |
| `tb_video_overlay.v` | 屏幕合成 `video_overlay_top` | 抓一整帧 800×480，`disp_ref.py check` 逐像素比对；另抓一帧 1280×720 验证运行时改分辨率 |

运行（无 Vivado，Verilator ≥ 5.0）：

```bash
./build/fetch_rtl_deps.sh
./sim/rtl/run_verilator.sh tb_qcnn_core
./sim/rtl/run_verilator.sh tb_axis_frame_link
python -I tools/ui/build_ui.py --cam data/cam_frame_640x800.npy           # 生成背景、文字、剖面的测试输入
EXTRA_MEM=build/ui ./sim/rtl/run_verilator.sh tb_video_overlay
python -I src/rtl/video/disp_ref.py check /tmp/verilator_fsf/tb_video_overlay-gen/frame_ui.rgb build/ui/case_ui.npz
```

Vivado 2026.1（xsim，含综合后网表仿真）：

```bash
./sim/rtl/run_xsim.sh tb_qcnn_core
./sim/rtl/run_xsim_layer.sh 2 postsynth      # 单层 0–3：RTL 或综合后网表
./sim/rtl/run_xsim_head.sh postsynth
./sim/rtl/run_postsynth.sh 1                 # 整核综合后网表功能仿真
```

综合后仿真是必需的：综合会忽略 `initial` 块里的非常量赋值，这类代码 RTL 仿真能过、上板却是错的。门级仿真前 100 ns 有全局复位（GSR），testbench 的复位要持续到它释放之后。

测试向量来源：`src/rtl/gen/` 由训练好的报警模型 `data/fusion/qmodel_win.npz` 导出，输入是 16 组实测窗口平均图样（`data/fusion/tv_pairs_win_16x2x56x56.npy`）。
`test_node.py` 默认用同一个训练模型 `data/fusion/qmodel_win.npz` 测 DMA 驱动和节点主循环；`data/qmodel_dt_synthetic.npz` 是同形状的整数测试模型，只在换形状调试时用。

## 板端软件（`sim/host/`，不需要板卡）

| 测试 | 内容 |
|---|---|
| `test_node.py` | `mqtt_lite` 对进程内 MQTT 3.1.1 broker：QoS 0/1、retain、遗嘱、订阅、broker 重启后重连并补发；`qcnn_dma` 对按 PG021 行为模拟的 DMA 寄存器（"PL" 用整数模型），结果与 `tv_output.mem` 比对；屏幕寄存器编码；节点服务端到端（假相机 + 整数模型后端 + MQTT 校准、累加、告警）；`dp_live` 模式换算 |
| `test_ui_filter.py` | PS 屏幕显示防抖：单点尖峰、末位滞回、状态迟滞、坐标轴粘滞、数值缓动、散斑异常帧门控和时间平滑 |

```bash
python -I sim/host/test_node.py
python -I sim/host/test_ui_filter.py
```

## 结果（`sim/results/`，2026-11-03，macOS + Verilator 5.052）

| 日志 | 结果 |
|---|---|
| `tb_qcnn_core.log` | 各层特征图全部一致；`RESULT 144/144 match (16 frames x 9 outputs), first frame 1117 us, then 565 us/frame`（100 MHz） |
| `tb_axis_frame_link.log` | `RESULT 72/72 match (data + tlast)` |
| `tb_video_overlay.log` + `disp_ref_check.log` | `PASS: 0 errors`；`frame_ui.rgb: 384000/384000 pixels match` |
| `test_node.log` | 29 项 PASS |
| `test_ui_filter.log` | 19 项 PASS |
