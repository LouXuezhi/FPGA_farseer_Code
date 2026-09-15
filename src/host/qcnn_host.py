"""Host side: stream dataset-14 frames to the FPGA, collect temperatures.

Pipelined: the sender keeps up to --window frames in flight instead of
waiting for each reply, so the link and the RTL pipeline both stay busy.

  python src/host/qcnn_host.py --link tcp --addr 192.168.2.99:5000      # PYNQ bridge
  python src/host/qcnn_host.py --link tcp --addr 127.0.0.1:5000         # frame_server.py --backend golden
  python src/host/qcnn_host.py --link uart --port /dev/ttyUSB0          # PL-only UART build
"""
import argparse
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
from frame_protocol import REQ_LEN, ReplyParser, code_to_celsius, encode_requests, load_ds14_frames  # noqa: E402
from qcnn_int import load_qmodel, predict_int  # noqa: E402


def open_link(args):
    """Return (write, read) callables for the chosen link."""
    if args.link == "uart":
        import serial
        port = serial.Serial(args.port, args.baud, timeout=0.1)
        return port.write, lambda: port.read(4096)
    host, tcp_port = args.addr.split(":")
    sock = socket.create_connection((host, int(tcp_port)))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(0.1)

    def read():
        try:
            return sock.recv(65536)
        except socket.timeout:
            return b""
    return sock.sendall, read


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", choices=["uart", "tcp"], required=True)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=3_000_000)
    parser.add_argument("--addr", default="127.0.0.1:5000")
    parser.add_argument("--frames", type=int, default=1752)
    parser.add_argument("--window", type=int, default=4, help="max frames in flight")
    parser.add_argument("--qmodel", default="runs/qcnn_in56_seed0/qmodel.npz")
    args = parser.parse_args()

    frames, temps = load_ds14_frames(n=args.frames)
    golden = predict_int(load_qmodel(args.qmodel), frames)       # expected FPGA codes
    stream = encode_requests(frames)
    write, read = open_link(args)

    n = len(frames)
    sent_at = np.zeros(n)
    got_at = np.zeros(n)
    codes = np.zeros(n, dtype=np.int64)
    in_flight = threading.Semaphore(args.window)
    done = threading.Event()

    def receiver():
        reply_parser, n_got = ReplyParser(), 0
        while n_got < n:
            for frame_id, code in reply_parser.feed(read()):
                k = n_got                                   # replies arrive in send order
                assert frame_id == k & 0xFF, f"reply id {frame_id} != expected {k & 0xFF}"
                codes[k], got_at[k] = code, time.perf_counter()
                n_got += 1
                in_flight.release()
        done.set()

    threading.Thread(target=receiver, daemon=True).start()
    t0 = time.perf_counter()
    for k in range(n):
        in_flight.acquire()
        write(stream[k * REQ_LEN:(k + 1) * REQ_LEN])
        sent_at[k] = time.perf_counter()
    done.wait()
    elapsed = time.perf_counter() - t0

    latency_ms = (got_at - sent_at) * 1e3
    err = code_to_celsius(codes) - temps
    print(f"frames {n}  throughput {n / elapsed:.1f} fps  "
          f"latency p50 {np.percentile(latency_ms, 50):.2f} ms  p99 {np.percentile(latency_ms, 99):.2f} ms")
    print(f"bit-exact vs golden: {(codes == golden).sum()}/{n}")
    print(f"RMSE {np.sqrt((err ** 2).mean()):.3f} degC  MAE {np.abs(err).mean():.3f} degC")


if __name__ == "__main__":
    main()
