"""MmioDmaBackend against a fake AXI DMA register file, no board needed.

The fake follows PG021 where the driver depends on it: soft reset self-clears,
status bits are write-1-to-clear, and writing the MM2S length runs the whole
transfer (frames -> golden model -> results -> IOC). Checks the register
sequence, buffer layout and batching.

  python sim/host/test_mmio_backend.py
"""
import sys
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / "src/host"), str(root / "src/model")]
from frame_protocol import load_ds14_frames  # noqa: E402
from frame_server import MmioDmaBackend as Dma  # noqa: E402
from qcnn_int import load_qmodel, predict_int  # noqa: E402


class FakeAxiDma:
    def __init__(self, buf, qmodel):
        self.r = np.zeros(0x100 // 4, dtype=np.int64)
        self.buf, self.qmodel = buf, qmodel

    def __getitem__(self, i):
        return self.r[i]

    def __setitem__(self, i, v):
        if i in (Dma.MM2S_SR // 4, Dma.S2MM_SR // 4):           # write-1-to-clear
            self.r[i] &= ~v
            return
        self.r[i] = 0 if (i in (Dma.MM2S_CR // 4, Dma.S2MM_CR // 4) and v & Dma.RESET) else v
        if i == Dma.MM2S_LEN // 4:                              # start: run the whole transfer
            assert self.r[Dma.S2MM_LEN // 4] >= 4 * (v // (56 * 56)), "receive not armed first"
            src, dst = int(self.r[Dma.MM2S_SA // 4]), int(self.r[Dma.S2MM_DA // 4])
            frames = np.frombuffer(self.buf, dtype=np.uint8, count=v, offset=src).reshape(-1, 56, 56)
            codes = predict_int(self.qmodel, frames).astype(np.int32)
            self.buf[dst:dst + 4 * len(codes)] = codes.tobytes()
            self.r[Dma.MM2S_SR // 4] |= Dma.IOC
            self.r[Dma.S2MM_SR // 4] |= Dma.IOC


def main():
    qmodel = load_qmodel(root / "runs/qcnn_in56_w16-32-64-64_a12_seed0/qmodel.npz")
    frames, _ = load_ds14_frames(str(root / "data/processed/fss_exp_thermal"), n=200)
    buf = bytearray(Dma.BUF_SIZE)
    backend = Dma(FakeAxiDma(buf, qmodel), buf, buf_phys=0)
    codes = np.concatenate([backend.run(frames[i:i + 64]) for i in range(0, len(frames), 64)])
    golden = predict_int(qmodel, frames)
    print(f"RESULT mmio backend: {(codes == golden).sum()}/{len(frames)} match")


if __name__ == "__main__":
    main()
