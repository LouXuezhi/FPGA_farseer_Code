# 07 Process gets SIGBUS when reading DMA results on the board

[中文](07_dma_sigbus.md) | English

Session: On-board bring-up of the inference core　Date: 2026-11-02

## Prompt (condensed)

> Running `speckle_node.py --backend dma` on the board, it exits on the first inference, and the terminal only shows `Bus error`.
> Last lines of dmesg: `Unhandled fault: alignment fault ... pc : __memcpy`.
> Environment: Linux 4.14 (xilinx-v2018.3), Python 3.6.9, numpy 1.13.3, the driver is `src/host/qcnn_dma.py`,
> the buffer is in a no-map reserved region in the device tree, mapped through `/dev/mem` with O_SYNC. Do not change the code yet; list the possible causes and the minimal experiments to tell them apart.

## Model answer (summary)

1. Most likely: on ARM64, an O_SYNC mapping is Device memory, which does not allow unaligned access. `np.frombuffer(...).copy()` goes through libc `memcpy`, which uses unaligned 16-byte loads.
   Experiment: read the result area one `int32` at a time; if the error goes away, this is confirmed.
2. Second: the physical address of the buffer is not inside the reserved region and is used by the kernel. Experiment: check that address range with `cat /proc/iomem`.
3. Third: the results are read before the DMA has finished writing. Experiment: print `S2MM_SR` before reading and check the IOC bit.

## Self-correction

The driver the assistant originally wrote used `np.frombuffer(...).copy()` to fetch the results in one go, and this was the source of the fault. The assistant traced it to this line from `pc : __memcpy` in dmesg,
explained that any code path that goes through libc `memcpy` is unsafe on Device memory, and changed it to read word by word in a Python loop:

```python
out = np.frombuffer(self.buf, dtype=np.int32, count=n * self.n_out, offset=self.out_off)
return np.array([int(v) for v in out], np.int32).reshape(n, self.n_out)
```

## Result

The error no longer occurs on the board. Only 9 words are read for the 9 outputs each time, so the overhead of reading word by word is negligible (DMA call p50 1.34 ms).
See the comment at the end of `src/host/qcnn_dma.py` for the code; the regression test is in `sim/host/test_node.py` (a register file simulated according to PG021 behavior).
