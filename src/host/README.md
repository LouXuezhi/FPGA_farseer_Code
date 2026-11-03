# src/host — PS 侧软件（板上 Python 3.6 + numpy 1.13，无其他依赖）

| 文件 | 作用 |
|---|---|
| `speckle_node.py` | 节点服务：取帧 → 预处理 → DMA 送推理核 → ΔT 累加 → 告警 → PL 屏幕寄存器 + MQTT；可同时把每步状态发给 PS 屏幕界面 |
| `speckle_preproc.py` | 散斑预处理 v1，与训练侧逐位一致 |
| `qcnn_dma.py` | AXI DMA（PG021 simple mode）驱动，`/dev/mem` + 保留内存，不依赖 PYNQ |
| `mqtt_lite.py` | MQTT 3.1.1 客户端（QoS 0/1、retain、遗嘱、自动重连、有界队列），只用标准库 |
| `fsf_ui.py`、`ui_layout.py` | PL 屏幕合成（`src/rtl/video`）的寄存器驱动和版面定义 |
| `dp_live.py` | 把 DPSUB 视频层切到 PL live 输入（使用 PL 屏幕合成时） |
| `fsf_ps_ui.py` | PS 屏幕界面：PS 渲染 800×480 RGB565 到 DRM，带页面、动画和触摸 |
| `ps_gfx.py` | 界面绘图：inferno 色表、中文/数字混排、3D 管路着色、散斑视图（原始 / 伪彩 / 帧差 / 局部去相关） |
| `ui_filter.py` | 界面显示防抖：单点尖峰、数值缓动、末位滞回、状态迟滞、坐标轴粘滞 |

## 节点服务

```
capture 线程 : 每周期从 stream.py /raw 取一帧新帧（短连接，不堆积旧帧）
主循环       : 每 --period s 取 (参考帧, 当前帧)，一次 DMA，累加 ΔT，判定告警，写屏幕，入 MQTT 队列
mqtt 线程    : 有界队列；broker 慢或断开都不阻塞推理和显示
```

MQTT 主题（前缀 `fsf/<node>/`）：

| 主题 | QoS | 内容 |
|---|---|---|
| `status` | 1，retain | `online`；遗嘱 `offline` |
| `state` | 0，每步 | `step t dt sensors dT T alarm ms{pre,qcnn,ovl} mqtt demo` |
| `alarm` | 1，变化时 | `t alarm T` |
| `cmd/calibrate` | 订阅 | `{"T": [9 个温度]}` 设定绝对温度 |

告警：已校准测点 `T > --t-alarm`（默认 48 °C），或单步 `|ΔT|/dt > --rate-alarm`（默认 3 °C/min）。

```bash
# 板上
python3 -E speckle_node.py --source raw --backend dma --address-map address_map.txt --ui-dir ui \
    --broker 192.168.1.253 --ui-udp 127.0.0.1:47110 --win-fusion fusion.json
# 无板卡（Mac）
python speckle_node.py --source fake --backend zero --ui-dir ../../board/fz3b/ui --broker 127.0.0.1 --steps 20
```

`--win-fusion`：按 `data/fusion/fusion.json` 做窗口融合报警（`win_fusion.py`）。`--demo` 只在推理核载入未训练的测试模型时使用，会在屏幕和 MQTT 上标出，正常运行不加。

## PS 屏幕界面

页面：启动 → 总览（3D 管路按沿程温度实时着色、散斑实时视图、9 路测点卡片）/ 测点（9 路趋势 + 热电偶对照表，点行进详情）/ 散斑（4 种预处理视图）/ 告警记录 / 系统（零点标定、关屏、退出）。
告警时顶部弹条滑入。显示防抖只作用于画面，不改节点数据，告警不延迟显示。

```bash
systemctl stop lxdm                                       # Xorg 占着 DRM 设备
python3 -E fsf_ps_ui.py --telemetry http://192.168.1.253:8877
python fsf_ps_ui.py --shots ../../build/ui_ps --raw-npy ../../data/speckle_pair_2x800x640.npy   # Mac 上出预览图
```

板上实测（numpy 1.13，单核）：整页 70–125 ms，页面切换帧 23 ms，处理一帧散斑 11 ms；空闲 2 fps、数值过渡 6 fps、动画 20 fps，不影响 2 s 的推理步长。
