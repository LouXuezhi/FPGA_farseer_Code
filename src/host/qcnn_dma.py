"""AXI DMA (simple mode, PG021) driver for axis_frame_link, Python 3.6 / numpy 1.13.

Any core shape: n_pixel bytes in
per frame (IN_CH planes), n_out int32 words back per frame.

    dma = QcnnDma.open(0xA0000000, 0x7FF00000, n_pixel=2 * 56 * 56, n_out=9)
    codes = dma.run(frames_u8)          # (n, n_pixel) uint8 -> (n, n_out) int32

buf_phys must be physically contiguous memory Linux does not use (reserved-
memory node); /dev/mem with O_SYNC maps it uncached, so the non-coherent HP
port needs no cache maintenance.
"""
import mmap
import os
import time

import numpy as np

MM2S_CR, MM2S_SR, MM2S_SA, MM2S_LEN = 0x00, 0x04, 0x18, 0x28
S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN = 0x30, 0x34, 0x48, 0x58
RUN, RESET, IOC, ERR_MASK = 0x1, 0x4, 0x1000, 0x70


def layout(n_pixel, n_out, max_batch):
    """-> (out_offset, buffer_size): results start on the page after the frames."""
    out_off = -(-max_batch * n_pixel // 4096) * 4096
    return out_off, out_off + 4 * n_out * max_batch


class QcnnDma:
    def __init__(self, regs, buf, buf_phys, n_pixel, n_out, max_batch=16, timeout_s=0.5):
        """regs: 32-bit register array (index = offset / 4); buf: bytes-like seen at buf_phys."""
        self.regs, self.buf = regs, buf
        self.n_pixel, self.n_out, self.max_batch, self.timeout_s = n_pixel, n_out, max_batch, timeout_s
        self.out_off, size = layout(n_pixel, n_out, max_batch)
        assert len(buf) >= size, "DMA buffer too small: %d < %d" % (len(buf), size)
        self.in_phys, self.out_phys = buf_phys, buf_phys + self.out_off
        self.reset()

    @classmethod
    def open(cls, dma_base, buf_phys, n_pixel, n_out, max_batch=16):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        regs = np.frombuffer(mmap.mmap(fd, 0x1000, offset=dma_base), dtype=np.uint32)
        buf = mmap.mmap(fd, layout(n_pixel, n_out, max_batch)[1], offset=buf_phys)
        os.close(fd)
        return cls(regs, buf, buf_phys, n_pixel, n_out, max_batch)

    def reset(self):
        # PG021: the soft reset of either channel resets the whole DMA, so reset
        # once and only then start both channels (resetting S2MM after starting
        # MM2S halts MM2S again; seen on the board 2026-11-02, MM2S_CR = 0x10002).
        r = self.regs
        r[MM2S_CR // 4] = RESET
        end = time.time() + 0.1
        while r[MM2S_CR // 4] & RESET or r[S2MM_CR // 4] & RESET:   # self-clears when done
            if time.time() > end:
                raise RuntimeError("AXI DMA reset did not complete (bitstream loaded? clock on?)")
        r[MM2S_CR // 4] = RUN
        r[S2MM_CR // 4] = RUN
        if not (r[MM2S_CR // 4] & RUN and r[S2MM_CR // 4] & RUN):
            raise RuntimeError("AXI DMA did not start: MM2S_CR=0x%x S2MM_CR=0x%x"
                               % (int(r[MM2S_CR // 4]), int(r[S2MM_CR // 4])))

    def run(self, frames_u8):
        frames = np.ascontiguousarray(frames_u8, dtype=np.uint8).reshape(-1, self.n_pixel)
        n, r = len(frames), self.regs
        assert 0 < n <= self.max_batch
        self.buf[:n * self.n_pixel] = frames.tobytes()
        r[S2MM_DA // 4], r[S2MM_LEN // 4] = self.out_phys, 4 * self.n_out * n   # arm receive first
        r[MM2S_SA // 4], r[MM2S_LEN // 4] = self.in_phys, n * self.n_pixel      # tlast after frame n-1
        end = time.time() + self.timeout_s
        while not r[S2MM_SR // 4] & IOC:
            if r[S2MM_SR // 4] & ERR_MASK or r[MM2S_SR // 4] & ERR_MASK or time.time() > end:
                sr = (int(r[MM2S_SR // 4]), int(r[S2MM_SR // 4]))
                self.reset()
                raise RuntimeError("DMA error/timeout, MM2S_SR=0x%x S2MM_SR=0x%x" % sr)
        r[MM2S_SR // 4] = IOC                                                   # clear (write 1)
        r[S2MM_SR // 4] = IOC
        # word by word: the buffer is Device memory (/dev/mem O_SYNC) and libc memcpy
        # (.copy()) uses unaligned 16-byte loads -> alignment fault / SIGBUS (2026-11-02)
        out = np.frombuffer(self.buf, dtype=np.int32, count=n * self.n_out, offset=self.out_off)
        return np.array([int(v) for v in out], np.int32).reshape(n, self.n_out)
