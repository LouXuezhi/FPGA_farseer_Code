# src/host — 主机收发程序

| 文件 | 运行位置 | 作用 |
|---|---|---|
| `frame_protocol.py` | 全部 | 帧编解码、应答重同步、dataset 14 帧读取（与训练相同的 4×4 整数池化） |
| `qcnn_host.py` | loulinux | 连续发帧、收温度；统计吞吐、延迟 p50/p99、与定点模型逐帧比对、RMSE |
| `frame_server.py` | PYNQ-Z2（`--backend dma`）或 PC（`--backend golden`） | TCP 帧服务：板上走 AXI DMA 进 RTL；PC 上用定点模型代替板卡 |

## 提速手段

- **流水发送**：`--window N` 允许 N 帧在途，不等应答再发下一帧，链路和 RTL 流水线同时保持忙碌
- **批量 DMA**：板端把 socket 里已到的完整帧（最多 64 帧）合成一次 DMA 传输，`tlast` 标记批次末尾，RTL 按批次返回
- **零解析开销**：请求一次性编码成连续字节缓冲区，发送时只切片

## 用法

```bash
python src/host/frame_server.py --backend golden &                  # 无板卡时
python src/host/qcnn_host.py --link tcp --addr 127.0.0.1:5000 --window 8
python src/host/qcnn_host.py --link uart --port /dev/ttyUSB0        # PL-only UART 构建
```

板端需要拷贝 `frame_protocol.py`、`frame_server.py`、`qcnn_dma.bit`、`qcnn_dma.hwh` 到同一目录。
