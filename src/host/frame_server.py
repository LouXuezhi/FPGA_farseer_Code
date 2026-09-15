"""TCP frame server speaking frame_protocol.

  --backend dma    : on the PYNQ-Z2, frames -> AXI DMA -> axis_frame_link (RTL)
  --backend golden : on any PC, integer golden model (board stand-in)

Every complete request already in the socket buffer is sent as one DMA
batch, so throughput grows with load instead of one transfer per frame.

  python3 frame_server.py --backend dma --bit qcnn_dma.bit     # on the board
  python  src/host/frame_server.py --backend golden            # on loulinux
"""
import argparse
import socket
import sys
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


def serve(conn, backend):
    buf = bytearray()
    while chunk := conn.recv(1 << 20):
        buf += chunk
        n = min(len(buf) // REQ_LEN, MAX_BATCH)
        while n:
            assert buf[:2] == REQ_SYNC, "stream out of sync"
            requests = np.frombuffer(bytes(buf[:n * REQ_LEN]), dtype=np.uint8).reshape(n, REQ_LEN)
            del buf[:n * REQ_LEN]
            codes = backend.run(requests[:, 3:].reshape(n, 56, 56))
            conn.sendall(b"".join(encode_reply(i, c) for i, c in zip(requests[:, 2], codes)))
            n = min(len(buf) // REQ_LEN, MAX_BATCH)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["dma", "golden"], required=True)
    parser.add_argument("--bit", default="qcnn_dma.bit")
    parser.add_argument("--qmodel", default="runs/qcnn_in56_seed0/qmodel.npz")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    backend = DmaBackend(args.bit) if args.backend == "dma" else GoldenBackend(args.qmodel)
    with socket.create_server(("0.0.0.0", args.port)) as server:
        print(f"{args.backend} frame server on :{args.port}")
        while True:
            conn, peer = server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with conn:
                print(f"client {peer}")
                serve(conn, backend)


if __name__ == "__main__":
    main()
