"""FZ3B node service: camera -> preprocess -> QCNN (PL) -> dT tracking -> DP overlay + MQTT.

Runs on the PS (Python 3.6, numpy 1.13). Three loosely coupled loops:
  capture thread : one fresh camera frame per period (never queues stale frames)
  main loop      : every --period s pairs (reference, current), one DMA call,
                   accumulates dT, writes the screen registers (fsf_ui), queues MQTT
  mqtt thread    : mqtt_lite, bounded queue; a slow or absent broker never
                   stalls inference or the display
Topics (prefix fsf/<node>):  status (retained, LWT "offline"), state (QoS 0 per step),
  alarm (QoS 1, on change), cmd/calibrate {"T": [9 floats]} sets the absolute temperatures.

    python3 speckle_node.py --source raw --backend dma --address-map address_map.txt --broker 192.168.1.253
    python3 speckle_node.py --source fake --backend zero --broker 127.0.0.1 --steps 20   # no board
    ... --ui-udp 127.0.0.1:47110                    # also feed the PS screen (fsf_ps_ui.py)
    ... --win-fusion fusion.json                    # fiber alarm (win_fusion.py): 60 s window-mean patterns
                                                    # 120 s apart -> QCNN rise + 1-corr, fused, 3 in a row
  With --win-fusion, state carries "fiber" (ready, score, threshold, alarm, cnn, corr) and alarm is
  published once per alarm episode; the per-route dT tracking is not used.
"""
import argparse
import json
import socket
import threading
import time

import numpy as np

from mqtt_lite import MqttClient
import fsf_ui
from speckle_preproc import preprocess

SENSORS = ["S7", "S16", "S1", "S12", "S2", "S9", "S4", "S3", "S10"]     # dT order (handover spec)


def read_address_map(path):
    """build_fz3b.tcl address_map.txt -> {"temp_dma": base, "ovl": base}."""
    bases = {}
    for line in open(path):
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("SEG_"):
            name = parts[0][4:].rsplit("_", 1)[0]               # SEG_temp_dma_Reg -> temp_dma, SEG_ovl_reg0 -> ovl
            bases[name] = int(parts[1], 16)
    return bases


# ---------------- frame sources ----------------
class RawSource:
    """stream.py /raw on the board itself (640x800 RAW8); shares the systemd
    fz3b-cam service instead of opening the camera a second time.
    One short connection per sample: /raw streams every capture (145 fps,
    ~74 MB/s) for as long as it stays open, and a new connection is answered
    with the next capture, never a cached one (stream.py skips the cache)."""

    def __init__(self, host="127.0.0.1", port=8080):
        self.addr = (host, port)

    def read(self):
        s = socket.create_connection(self.addr, timeout=5)
        try:
            s.sendall(b"GET /raw HTTP/1.1\r\nHost: local\r\n\r\n")
            f = s.makefile("rb", buffering=1 << 20)
            while f.readline() not in (b"\r\n", b""):
                pass
            hdr = {}
            while not f.readline().strip().startswith(b"--raw"):
                pass
            while True:
                line = f.readline().strip()
                if not line:
                    break
                k, _, v = line.partition(b":")
                hdr[k.strip().lower()] = v.strip()
            data = f.read(int(hdr[b"content-length"]))
            h, w = int(hdr[b"x-height"]), int(hdr[b"x-width"])
            return time.time(), np.frombuffer(data, np.uint8).reshape(h, w)
        finally:
            s.close()


class FakeSource:
    """Synthetic speckle that decorrelates with a slowly ramping 'temperature'."""

    def __init__(self, seed=0):
        rng = np.random.RandomState(seed)
        self.a, self.b = rng.rand(800, 640), rng.rand(800, 640)
        self.t0 = time.time()

    def read(self):
        time.sleep(0.05)
        ph = 0.3 * (time.time() - self.t0)
        img = 110 + 60 * (np.cos(ph) * self.a + np.sin(ph) * self.b - 0.5)
        return time.time(), np.clip(img, 0, 255).astype(np.uint8)


# ---------------- preprocessing (speckle_preproc.py v1), then down to the core input ----------------
def to_plane(img, in_h, q_scale, q_zero):
    """(400, 640) standardised -> (in_h, in_h) uint8: centre crop + block mean."""
    k = img.shape[0] // in_h
    side = k * in_h
    y0, x0 = (img.shape[0] - side) // 2, (img.shape[1] - side) // 2
    crop = img[y0:y0 + side, x0:x0 + side].reshape(in_h, k, in_h, k).mean(axis=(1, 3))
    return np.clip(np.round(crop * q_scale + q_zero), 0, 255).astype(np.uint8)


# ---------------- backends ----------------
class ZeroBackend:
    def __init__(self, n_out):
        self.n_out = n_out

    def run(self, frames):
        return np.zeros((len(frames), self.n_out), np.int32)


class GoldenBackend:
    """Integer reference model (PC only: needs numpy >= 1.20)."""

    def __init__(self, qmodel_path):
        from qcnn_int import forward_int, load_qmodel
        self.q, self.fwd = load_qmodel(qmodel_path), forward_int

    def run(self, frames):
        return np.asarray(self.fwd(self.q, frames)).reshape(len(frames), -1)


# ---------------- node ----------------
class Node:
    def __init__(self, args, source, backend, overlay, mqtt):
        self.a, self.src, self.backend, self.ov, self.mqtt = args, source, backend, overlay, mqtt
        self.latest = None
        self.lock = threading.Lock()
        self.T = np.full(9, np.nan)                            # absolute estimate, unknown until calibrated
        self.calibrated = np.zeros(9, bool)
        self.alarm = np.zeros(9, bool)
        self.ref = None
        self.step = 0
        mqtt.on_message = self.on_message
        self.wf = None
        if getattr(args, "win_fusion", None):                  # windowed QCNN + decorrelation alarm (win_fusion.py)
            from win_fusion import WinFusion
            self.wf = WinFusion.load(args.win_fusion, backend)
        self.ui = None
        if getattr(args, "ui_udp", None):
            host, _, port = args.ui_udp.rpartition(":")
            self.ui_addr = (host or "127.0.0.1", int(port))
            self.ui = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            threading.Thread(target=self.ui_loop, name="ui", daemon=True).start()

    def capture_loop(self):
        while True:
            if self.latest is not None:                         # not consumed yet: no need for another
                time.sleep(0.02)
                continue
            try:
                item = self.src.read()
            except Exception as e:                              # camera restart: back off, keep running
                print("capture: %r" % e)
                time.sleep(1.0)
                continue
            with self.lock:
                self.latest = item

    def on_message(self, topic, payload):
        if topic.endswith("/cmd/calibrate"):
            T = json.loads(payload.decode())["T"]
            with self.lock:
                for k, v in enumerate(T[:9]):
                    if v is not None:
                        self.T[k], self.calibrated[k] = float(v), True

    def run_step(self):
        with self.lock:
            item, self.latest = self.latest, None
        if item is None:
            return None
        t_cap, raw = item
        t0 = time.time()
        img = preprocess(raw)
        if self.wf is not None:
            return self.fusion_step(t_cap, img, t0)
        plane = to_plane(img, self.a.in_h, self.a.q_scale, self.a.q_zero)
        t1 = time.time()
        if self.ref is None:
            self.ref = (t_cap, plane)
            return None
        dt = t_cap - self.ref[0]
        codes = self.backend.run(np.stack([self.ref[1], plane])[None])[0]
        t2 = time.time()
        dT = codes.astype(np.float64) * self.a.dt_lsb
        self.ref = (t_cap, plane)                               # chain pairs: no overlap, no double count
        with self.lock:
            self.T = self.T + dT
            T, cal = self.T.copy(), self.calibrated.copy()
        rate = dT / max(dt, 1e-3) * 60.0                        # degC/min
        alarm = cal & ((T > self.a.t_alarm) | (np.abs(rate) > self.a.rate_alarm))
        self.step += 1
        st = self.mqtt.stats
        self.ov.show([float(v) for v in np.where(cal, T, 0.0)], [bool(v) for v in cal], [bool(v) for v in alarm],
                     [float(v) for v in dT],
                     dict(step=self.step, dt=dt, qcnn_ms=1e3 * (t2 - t1), pre_ms=1e3 * (t1 - t0),
                          mqtt=self.mqtt.connected, clock=time.strftime("%H:%M:%S"),
                          topic="fsf/%s/state" % self.a.node, sent=st["sent"], queued=len(self.mqtt._q)))
        t3 = time.time()
        msg = {"step": self.step, "t": round(t_cap, 3), "dt": round(dt, 3), "sensors": SENSORS,
               "dT": [round(float(v), 4) for v in dT],
               "T": [round(float(v), 3) if c else None for v, c in zip(T, cal)],
               "alarm": [bool(v) for v in alarm],
               "ms": {"pre": round(1e3 * (t1 - t0), 1), "qcnn": round(1e3 * (t2 - t1), 2),
                      "ovl": round(1e3 * (t3 - t2), 2)},
               "mqtt": dict(self.mqtt.stats)}
        topic = "fsf/%s/" % self.a.node
        self.mqtt.publish(topic + "state", json.dumps(msg))
        if (alarm != self.alarm).any():
            self.mqtt.publish(topic + "alarm", json.dumps({"t": msg["t"], "alarm": msg["alarm"], "T": msg["T"]}), qos=1)
        self.alarm = alarm
        if self.ui is not None:                                 # PS screen (fsf_ps_ui.py): same state + display hints
            ui = dict(msg, ui={"mqtt": self.mqtt.connected, "t_alarm": self.a.t_alarm, "rate_alarm": self.a.rate_alarm,
                               "node": self.a.node})
            try:
                self.ui.sendto(json.dumps(ui).encode(), self.ui_addr)
            except OSError:
                pass
        return msg

    def fusion_step(self, t_cap, img, t0):
        """--win-fusion: the fused windowed score drives one fiber alarm (no per-route temperatures)."""
        t1 = time.time()
        st = self.wf.push(t_cap, img)
        t2 = time.time()
        self.step += 1
        topic = "fsf/%s/" % self.a.node
        fiber = {k: st.get(k) for k in ("ready", "score", "threshold", "alarm", "cnn", "corr", "t_alarm")}
        msg = {"step": self.step, "t": round(t_cap, 3), "sensors": SENSORS,
               "fiber": fiber, "T": [None] * 9, "dT": [0.0] * 9, "alarm": [False] * 9,
               "ms": {"pre": round(1e3 * (t1 - t0), 1), "qcnn": round(1e3 * (t2 - t1), 2)},
               "mqtt": dict(self.mqtt.stats)}
        self.mqtt.publish(topic + "state", json.dumps(msg))
        if st["event"]:
            self.mqtt.publish(topic + "alarm", json.dumps({"t": msg["t"], "fiber": fiber}), qos=1)
            print("fiber alarm: score %.2f > %.2f" % (st["score"], st["threshold"]))
        if self.ui is not None:
            ui = dict(msg, ui={"mqtt": self.mqtt.connected, "t_alarm": self.a.t_alarm, "rate_alarm": self.a.rate_alarm,
                               "node": self.a.node})
            try:
                self.ui.sendto(json.dumps(ui).encode(), self.ui_addr)
            except OSError:
                pass
        return msg

    def ui_loop(self):
        """Commands from the PS screen (same JSON as MQTT cmd/calibrate, plus "cmd")."""
        while True:
            try:
                data, _ = self.ui.recvfrom(65536)
                m = json.loads(data.decode())
            except (OSError, ValueError):
                continue
            if m.get("cmd") == "calibrate":
                self.on_message("cmd/calibrate", json.dumps({"T": m["T"]}).encode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", default="fz3b")
    ap.add_argument("--source", choices=["raw", "fake"], default="raw")
    ap.add_argument("--backend", choices=["dma", "golden", "zero"], default="dma")
    ap.add_argument("--qmodel", help="golden backend")
    ap.add_argument("--address-map", help="address_map.txt from the build (sets --dma-base / --ovl-base)")
    ap.add_argument("--dma-base", type=lambda v: int(v, 0), default=0xA0020000, help="FSF_CAM=1 build")
    ap.add_argument("--buf-phys", type=lambda v: int(v, 0), default=0x7FF00000)
    ap.add_argument("--ovl-base", type=lambda v: int(v, 0), help="screen registers; omit = no display")
    ap.add_argument("--ui-dir", default="ui", help="bg.bin + layout.json from tools/ui/build_ui.py")
    ap.add_argument("--bg-phys", type=lambda v: int(v, 0), default=0x72000000,
                    help="reserved DDR for the 1.5 MB background (inside the camera's no-map region)")
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--period", type=float, default=1.0, help="pair interval, s (spec: 1..10)")
    ap.add_argument("--in-h", type=int, default=56)
    ap.add_argument("--n-out", type=int, default=9)
    ap.add_argument("--q-scale", type=float, default=32.0, help="standardised -> uint8, must match training")
    ap.add_argument("--q-zero", type=float, default=128.0)
    ap.add_argument("--dt-lsb", type=float, default=1.0 / 1024, help="degC per output code")
    ap.add_argument("--t-alarm", type=float, default=48.0)
    ap.add_argument("--rate-alarm", type=float, default=3.0, help="degC/min")
    ap.add_argument("--steps", type=int, default=0, help="stop after N steps (0 = run forever)")
    ap.add_argument("--win-fusion", help="fusion.json: fiber alarm from the windowed QCNN + decorrelation (win_fusion.py)")
    ap.add_argument("--ui-udp", help="host:port of fsf_ps_ui.py (PS screen), e.g. 127.0.0.1:47110")
    a = ap.parse_args()
    if a.address_map:
        bases = read_address_map(a.address_map)
        a.dma_base = bases["temp_dma"]
        a.ovl_base = bases.get("ovl", a.ovl_base)
        print("address map: dma 0x%08x overlay %s" % (a.dma_base, "0x%08x" % a.ovl_base if a.ovl_base else "none"))

    source = RawSource() if a.source == "raw" else FakeSource()
    if a.backend == "dma":
        from qcnn_dma import QcnnDma
        backend = QcnnDma.open(a.dma_base, a.buf_phys, n_pixel=2 * a.in_h * a.in_h, n_out=a.n_out)
    elif a.backend == "golden":
        backend = GoldenBackend(a.qmodel)
    else:
        backend = ZeroBackend(a.n_out)
    layout = json.load(open(a.ui_dir + "/layout.json"))
    if a.ovl_base is not None:
        overlay = fsf_ui.Screen.open(a.ovl_base, a.ui_dir + "/layout.json")
        overlay.load_background(a.ui_dir + "/bg.bin", a.bg_phys)
        overlay.commit()
    else:
        overlay = fsf_ui.Screen(fsf_ui.FakeRegs(), layout)
    topic = "fsf/%s/" % a.node
    mqtt = MqttClient(a.node, a.broker, a.port, will=(topic + "status", b"offline", True)).start()
    mqtt.subscribe(topic + "cmd/#")
    mqtt.publish(topic + "status", "online", qos=1, retain=True)
    node = Node(a, source, backend, overlay, mqtt)
    threading.Thread(target=node.capture_loop, name="capture", daemon=True).start()
    next_t = time.time()
    try:
        while not a.steps or node.step < a.steps:
            next_t += a.period
            msg = node.run_step()
            if msg and node.step % 10 == 1:
                print("step %d ms %s%s" % (msg["step"], msg["ms"], "" if "fiber" not in msg else " fiber %s" % msg["fiber"]))
            time.sleep(max(0.0, next_t - time.time()))
    finally:
        mqtt.publish(topic + "status", "offline", qos=1, retain=True)
        overlay.disable()
        mqtt.stop()


if __name__ == "__main__":
    main()
