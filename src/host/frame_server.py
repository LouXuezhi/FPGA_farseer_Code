"""Frame server speaking frame_protocol, over TCP or a pseudo terminal.

  --backend dma    : on a PYNQ image (PYNQ-Z2), frames -> AXI DMA -> axis_frame_link (RTL)
  --backend mmio   : same path without PYNQ (e.g. FZ3B stock Linux): AXI DMA
                     registers through /dev/mem, reserved buffer at --buf-phys
  --backend golden : on any PC, integer golden model (board stand-in)
  --backend echo   : replies zeros, to measure link and host overhead alone
  --link pty       : serve on a pseudo terminal, so the host UART path can be
                     tested without a board (same framing as uart_frame_link)

Every complete request already in the buffer is answered as one batch, so
throughput grows with load instead of one transfer per frame.

  python3 frame_server.py --backend dma --bit qcnn_dma.bit     # PYNQ-Z2
  python3 frame_server.py --backend mmio --dma-base 0xA0000000 --buf-phys 0x7FF00000   # FZ3B
  python  src/host/frame_server.py --backend golden            # TCP stand-in
  python  src/host/frame_server.py --backend golden --link pty # UART stand-in
"""
import argparse
import mmap
import os
import pty
import socket
import sys
import tty
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
from frame_protocol import N_PIXEL, REQ_LEN, REQ_SYNC, encode_reply  # noqa: E402

MAX_BATCH = 64


class GoldenBackend:
    def __init__(self, qmodel_path):
        from qcnn_int import load_qmodel, predict_int
        self.qmodel, self.predict = load_qmodel(qmodel_path), predict_int

    def run(self, frames_u8):
        return self.predict(self.qmodel, frames_u8)


class EchoBackend:
    """No inference: measures the link and the host program alone."""

    def run(self, frames_u8):
        return np.zeros(len(frames_u8), dtype=np.int32)


class DmaBackend:
    def __init__(self, bit_path):
        from pynq import Overlay, allocate
        self.dma = Overlay(bit_path).dma
        self.in_buf = allocate(shape=(MAX_BATCH * N_PIXEL,), dtype=np.uint8)
        self.out_buf = allocate(shape=(MAX_BATCH,), dtype=np.int32)

    def run(self, frames_u8):
        n = len(frames_u8)
        self.in_buf[:n * N_PIXEL] = frames_u8.reshape(-1)
        self.dma.recvchannel.transfer(self.out_buf, nbytes=4 * n)   # arm receive first
        self.dma.sendchannel.transfer(self.in_buf, nbytes=n * N_PIXEL)  # tlast after frame n-1
        self.dma.sendchannel.wait()
        self.dma.recvchannel.wait()
        return self.out_buf[:n].copy()


class MmioDmaBackend:
    """AXI DMA (simple mode, PG021) through /dev/mem, for Linux images without PYNQ.

    buf_phys: physically contiguous memory Linux does not use (reserved-memory
    node or mem= boot argument). /dev/mem with O_SYNC maps it uncached, so no
    cache maintenance is needed around the non-coherent HP port.
    """
    MM2S_CR, MM2S_SR, MM2S_SA, MM2S_LEN = 0x00, 0x04, 0x18, 0x28
    S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN = 0x30, 0x34, 0x48, 0x58
    RUN, RESET, IOC = 0x1, 0x4, 0x1000
    OUT_OFF = -(-MAX_BATCH * N_PIXEL // 4096) * 4096           # results start on the next page
    BUF_SIZE = OUT_OFF + 4 * MAX_BATCH

    def __init__(self, regs, buf, buf_phys):
        """regs: 32-bit register array, buf: BUF_SIZE bytes seen by the DMA at buf_phys."""
        self.regs, self.buf = regs, buf
        self.in_phys, self.out_phys = buf_phys, buf_phys + self.OUT_OFF
        for cr in (self.MM2S_CR, self.S2MM_CR):
            regs[cr // 4] = self.RESET
            while regs[cr // 4] & self.RESET:                   # self-clears when done
                pass
            regs[cr // 4] = self.RUN

    @classmethod
    def open(cls, dma_base, buf_phys):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        regs = np.frombuffer(mmap.mmap(fd, 0x1000, offset=dma_base), dtype=np.uint32)
        return cls(regs, mmap.mmap(fd, cls.BUF_SIZE, offset=buf_phys), buf_phys)

    def run(self, frames_u8):
        n, r = len(frames_u8), self.regs
        self.buf[:n * N_PIXEL] = frames_u8.tobytes()
        r[self.S2MM_DA // 4], r[self.S2MM_LEN // 4] = self.out_phys, 4 * n       # arm receive first
        r[self.MM2S_SA // 4], r[self.MM2S_LEN // 4] = self.in_phys, n * N_PIXEL  # tlast after frame n-1
        while not r[self.S2MM_SR // 4] & self.IOC:                               # batch written back
            pass
        r[self.MM2S_SR // 4] = r[self.S2MM_SR // 4] = self.IOC                   # clear (write 1)
        return np.frombuffer(self.buf, dtype=np.int32, count=n, offset=self.OUT_OFF).copy()


def serve(read, write, backend):
    buf = bytearray()
    while chunk := read():
        buf += chunk
        n = min(len(buf) // REQ_LEN, MAX_BATCH)
        while n:
            assert buf[:2] == REQ_SYNC, "stream out of sync"
            requests = np.frombuffer(bytes(buf[:n * REQ_LEN]), dtype=np.uint8).reshape(n, REQ_LEN)
            del buf[:n * REQ_LEN]
            codes = backend.run(requests[:, 3:].reshape(n, 56, 56))
            write(b"".join(encode_reply(i, c) for i, c in zip(requests[:, 2], codes)))
            n = min(len(buf) // REQ_LEN, MAX_BATCH)


def serve_pty(backend):
    """Pseudo terminal: the slave path behaves like a serial port for the host."""
    master, slave = pty.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    print(f"pty {os.ttyname(slave)}", flush=True)

    def write(data):
        while data:
            data = data[os.write(master, data):]
    serve(lambda: os.read(master, 1 << 16), write, backend)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["dma", "mmio", "golden", "echo"], required=True)
    parser.add_argument("--link", choices=["tcp", "pty"], default="tcp")
    parser.add_argument("--bit", default="qcnn_dma.bit")
    parser.add_argument("--qmodel", default="runs/qcnn_in56_w16-32-64-64_a12_seed0/qmodel.npz")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--dma-base", type=lambda v: int(v, 0), default=0xA0000000, help="mmio: AXI DMA registers")
    parser.add_argument("--buf-phys", type=lambda v: int(v, 0), default=0x7FF00000, help="mmio: reserved buffer (1 MB)")
    args = parser.parse_args()
    backend = {"dma": lambda: DmaBackend(args.bit), "mmio": lambda: MmioDmaBackend.open(args.dma_base, args.buf_phys),
               "golden": lambda: GoldenBackend(args.qmodel),
               "echo": EchoBackend}[args.backend]()
    if args.link == "pty":
        serve_pty(backend)
        return
    with socket.create_server(("0.0.0.0", args.port)) as server:
        print(f"{args.backend} frame server on :{args.port}")
        while True:
            conn, peer = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with conn:
                print(f"client {peer}")
                serve(lambda: conn.recv(1 << 20), conn.sendall, backend)


if __name__ == "__main__":
    main()
