# 01 板上 DMA 读结果时进程 SIGBUS

日期：2026-11-02　会话：板上推理核联调

## 提示词（整理）

> 板上跑 `speckle_node.py --backend dma`，第一次推理就退出，终端只有 `Bus error`。
> dmesg 最后几行：`Unhandled fault: alignment fault ... pc : __memcpy`。
> 环境：Linux 4.14（xilinx-v2018.3），Python 3.6.9，numpy 1.13.3，驱动是 `src/host/qcnn_dma.py`，
> 缓冲区在设备树 no-map 保留区，`/dev/mem` 加 O_SYNC 映射。先不要改代码，列出可能原因和区分它们的最小实验。

## 模型回答（摘要）

1. 最可能：O_SYNC 映射在 ARM64 上是 Device 内存，不允许非对齐访问；`np.frombuffer(...).copy()` 走 libc `memcpy`，会用非对齐的 16 字节加载。
   实验：对结果区逐个 `int32` 读出，如果不再报错即可确认。
2. 其次：缓冲区物理地址不在保留区内，被内核占用。实验：`cat /proc/iomem` 看该地址段。
3. 再次：DMA 没有写完就读。实验：读之前打印 `S2MM_SR`，看 IOC 位。

## 纠错

助手原先写的驱动用 `np.frombuffer(...).copy()` 一次取出结果，这是故障的来源。助手根据 dmesg 里 `pc : __memcpy` 定位到这一行，
说明凡是走 libc `memcpy` 的写法在 Device 内存上都不安全，改为 Python 循环逐字读取：

```python
out = np.frombuffer(self.buf, dtype=np.int32, count=n * self.n_out, offset=self.out_off)
return np.array([int(v) for v in out], np.int32).reshape(n, self.n_out)
```

## 结果

板上不再报错；9 个输出每次只读 9 个字，逐字读取的开销可以忽略（DMA 调用 p50 1.34 ms）。
代码见 `src/host/qcnn_dma.py` 末尾的注释，回归测试在 `sim/host/test_node.py`（按 PG021 行为模拟的寄存器文件）。
