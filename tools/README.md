# tools — 素材生成与上位机

## ui/ — 屏幕素材

管路模型是 Blender 参数化模型（`pipe_model_snapshot.py`：透明管段、10 个三通球阀、接头、支撑杆）。
`pipe_panel.py` 把冷却液渲染两遍（水为纯黑发光体一次、纯白一次），其余完全相同，于是每个像素：

```
a      = 水为黑色时的画面
b − a  = 有多少水的光能到达相机（透过亚克力管）
显示   = a + (b − a) × 色表[T(x)]        （PL 与 PS 两条显示路径用同一规则）
```

| 文件 | 作用 |
|---|---|
| `pipe_model_snapshot.py` | 管路几何与材质（Blender 5.x） |
| `pipe_panel.py` | 暗场正交相机两遍渲染 → `panel_a.png`、`panel_b.png`、`panel.json`（像素/米、阀门与管段 x） |
| `build_ui.py` | PL 屏幕：`bg.bin`、`layout.json`、`heat_lut.mem`、`font12x24.mem`，以及仿真输入和逐像素参考预览 `build/ui/preview.png` |
| `ui_text.py` | `build_ui.py` 调用的文字与面板绘制（系统 Python + Pillow） |
| `build_ps_pipe.py` | PS 界面：两遍渲染压成 `board/fz3b/ui/pipe_ps.npz`（768×54，`a` + 水体权重 + 测点 x） |

```bash
Blender -b -P tools/ui/pipe_panel.py -- --out build/ui_panel          # 已附渲染结果 build/ui_panel/，可跳过
python -I tools/ui/build_ui.py --cam data/cam_frame_640x800.npy
python -I tools/ui/build_ps_pipe.py
```

测点在管上的位置（`layout.json` 的 `sensor_x`）按"阀间管段中点、按 ΔT 顺序"排列。

## hmi/ — 上位机监视页

| 文件 | 作用 |
|---|---|
| `fsf_hmi.py` | 订阅 `fsf/<node>/{status,state,alarm}`，保留 15 min 状态；从温控采集页 `/api/telemetry` 取热电偶参考温度；网页上并排显示节点温度、参考温度、误差和告警；"标定"按钮向 `cmd/calibrate` 发送参考温度。只用标准库 |
| `hmi.html` | 页面 |
| `mosquitto.conf` | 项目用 broker 配置：只监听本机和板子网口 |

```bash
mosquitto -c tools/hmi/mosquitto.conf
python -I tools/hmi/fsf_hmi.py --broker 127.0.0.1 --node fz3b --acq http://127.0.0.1:8877      # http://127.0.0.1:8890
```
