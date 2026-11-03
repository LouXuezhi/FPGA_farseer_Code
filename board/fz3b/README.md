# board/fz3b — 上板

EdgeBoard FZ3B：Zynq UltraScale+ ZU3EG（`xczu3eg-sfvc784-1-i`），4 GB DDR4，SD 卡启动厂商镜像（Linux 4.14，xilinx-v2018.3）。
不改 SD 卡启动分区：比特流在 Linux 下通过 fpga_manager 加载，断电重启即回到出厂配置。

## 目录

| 路径 | 内容 |
|---|---|
| `prebuilt/` | 比特流 `fsf_fz3b.bin`（fpga_manager 用）/ `.bit`（JTAG 用）/ `.hwh`、`address_map.txt`、`sha256sums.txt` |
| `cam_service/` | 相机服务：`cam.py`（传感器 I2C、VDMA 配置）、`stream.py`（HTTP 取帧服务，端口 8080，`/raw` 给节点服务取原始帧）、`load.sh`（加载比特流） |
| `ui/` | 屏幕素材：`bg.bin`（PL 屏幕背景，RGBW8888）、`layout.json`（测点位置）、`pipe_ps.npz`（PS 界面的 3D 管路两遍渲染） |
| `drm_mode_test.py` | 设 800×480 原生模式（转接头 EDID 只报 1080p），PS 界面也用它的 DRM 封装 |
| `results/` | 板上实测输出 |

## 板上文件布局

```
/root/cam/
├── fz3b_cam.bin          = prebuilt/fsf_fz3b.bin（stream.py 启动时用 load.sh 加载，检查 cam_regs 版本 0xCA928103）
├── address_map.txt       = prebuilt/address_map.txt
├── cam.py stream.py load.sh               ← cam_service/
└── fsf_ui/
    ├── speckle_node.py win_fusion.py speckle_preproc.py qcnn_dma.py mqtt_lite.py fsf_ui.py ui_layout.py dp_live.py
    ├── fsf_ps_ui.py ps_gfx.py ui_filter.py drm_mode_test.py        ← src/host/ + 本目录
    ├── bg.bin layout.json pipe_ps.npz                              ← ui/
    └── fusion.json                                                 ← data/fusion/
```

```bash
B=192.168.1.254
sha256sum -c board/fz3b/prebuilt/sha256sums.txt
scp board/fz3b/prebuilt/fsf_fz3b.bin root@$B:/root/cam/fz3b_cam.bin
scp board/fz3b/prebuilt/address_map.txt board/fz3b/cam_service/* root@$B:/root/cam/
ssh root@$B mkdir -p /root/cam/fsf_ui
scp src/host/*.py board/fz3b/drm_mode_test.py board/fz3b/ui/* data/fusion/fusion.json root@$B:/root/cam/fsf_ui/
```

## 运行

板子 192.168.1.254，上位机（MQTT broker、温控采集页）192.168.1.253，经网线直连。

```bash
# 1. 相机服务（systemd，开机自启；首次启动时加载比特流）
systemctl restart fz3b-cam            # 单元文件：WorkingDirectory=/root/cam，ExecStart=/usr/bin/python3 -I stream.py
curl -s 127.0.0.1:8080/stats          # 帧计数在涨即正常

# 2. 节点服务
cd /root/cam/fsf_ui
python3 -E speckle_node.py --source raw --backend dma --address-map ../address_map.txt --ui-dir . \
    --broker 192.168.1.253 --ui-udp 127.0.0.1:47110 --win-fusion fusion.json      # fusion.json = data/fusion/fusion.json

# 3. 屏幕：PS 界面
systemctl stop lxdm                   # Xorg 占着 DRM 设备
python3 -E fsf_ps_ui.py --telemetry http://192.168.1.253:8877
```

只能有一个相机服务实例（systemd 管理，不要再手动起一份，两份会互相重置传感器）。同名 MQTT 客户端会互踢，同一时间只跑一个节点服务。
不要在运行 PS 界面时执行 `dp_live.py on`：它把 DPSUB 切到 PL live 视频层并隐藏图形层。

上位机：

```bash
mosquitto -c tools/hmi/mosquitto.conf
python -I tools/hmi/fsf_hmi.py --broker 127.0.0.1 --acq http://127.0.0.1:8877     # http://127.0.0.1:8890
mosquitto_sub -h 127.0.0.1 -t 'fsf/fz3b/#' -v
```

## 一次性准备

- 保留内存：推理 DMA 缓冲区 1 MB 在 `0x7FF0_0000`，PL 屏幕背景在 `0x7200_0000`，相机帧存从 `0x7000_0000` 起；均在厂商设备树 `buffer@0`（`0x4000_0000–0x7FFF_FFFF`，no-map）内，不用改设备树。
- PL 时钟：比特流按 `pl_clk0 = 100 MHz` 收敛。
- 屏幕：Mini DP → 有源 DP 转 HDMI 转接头（CH7218）→ 7 寸 800×480 屏。转接头 EDID 只报 1920×1080，800×480 原生模式由 `drm_mode_test.py` / PS 界面直接设置；主平面只接受 RGB565。

## 实测（`results/`，2026-11-03）

| 文件 | 内容 |
|---|---|
| `board_env.txt` | 内核、Python、numpy 版本，正在运行的比特流 sha256（与 `prebuilt/fsf_fz3b.bin` 一致），fpga_manager 状态 |
| `node_log_excerpt.txt` | 节点服务日志（启动 3 行 + 连续运行到第 22901 步的最后 200 行，每 10 步一行） |

由日志统计（201 行）：

| 项 | p50 | p95 | p99 | max |
|---|---|---|---|---|
| DMA 推理调用 `qcnn` | 0.38 ms | 0.43 ms | 0.48 ms | 0.54 ms |
| 预处理 `pre` | 2017 ms | — | — | 2043 ms |
| 步长 `dt` | 2.02 s | — | — | 2.07 s |

推理调用时间包括 PS 写 DMA 描述符、PL 计算和结果回读；PL 计算本身按仿真为 565 µs/帧（单帧、首帧 1.12 ms 含流水填充）。步长由 PS 上的预处理决定。
