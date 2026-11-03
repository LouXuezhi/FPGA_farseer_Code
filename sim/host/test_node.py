"""Board software without a board: MQTT client, DMA driver, overlay driver, node loop.

    python -I sim/host/test_node.py [src/rtl/gen] [qmodel.npz]   # default: data/fusion/qmodel_win.npz

  1. mqtt_lite against an in-process MQTT 3.1.1 broker: QoS 0/1, retain, will,
     subscribe/inbound, broker restart (reconnect + queued messages delivered)
  2. QcnnDma against a fake PG021 register file whose "PL" is the integer model,
     with the dT shape (2 planes in, 9 codes out); compared to tv_output.mem
  3. Overlay register encoding (FakeRegs)
  4. speckle_node end to end: fake camera, golden backend, MQTT state/alarm/calibrate
"""
import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src" / "host"), str(ROOT / "src" / "model"), str(ROOT / "sim" / "rtl")]
import mqtt_lite as m  # noqa: E402
import fsf_ui  # noqa: E402
from qcnn_dma import QcnnDma, layout, S2MM_SR, IOC, MM2S_SA, MM2S_LEN, S2MM_DA, S2MM_LEN  # noqa: E402

FAILS = []


def check(cond, what):
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


# ---------------- tiny broker: enough of MQTT 3.1.1 for the client ----------------
class Broker:
    def __init__(self, port=0):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.log, self.retained, self.subs, self.clients = [], {}, [], []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.clients.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        will = None
        try:
            while True:
                t, flags, body = m.read_packet(c)
                if t == m.CONNECT:
                    cflags = body[7]
                    p = 10
                    n = struct.unpack("!H", body[p:p + 2])[0]; p += 2 + n          # client id
                    if cflags & 0x04:
                        n = struct.unpack("!H", body[p:p + 2])[0]; wt = body[p + 2:p + 2 + n]; p += 2 + n
                        n = struct.unpack("!H", body[p:p + 2])[0]; wm = body[p + 2:p + 2 + n]
                        will = (wt.decode(), wm, bool(cflags & 0x20))
                    c.sendall(m.packet(m.CONNACK, 0, b"\x00\x00"))
                elif t == m.PUBLISH:
                    n = struct.unpack("!H", body[:2])[0]
                    topic, rest, qos = body[2:2 + n].decode(), body[2 + n:], (flags >> 1) & 3
                    if qos:
                        c.sendall(m.packet(m.PUBACK, 0, rest[:2]))
                        rest = rest[2:]
                    self._route(topic, rest, flags & 1)
                elif t == m.SUBSCRIBE:
                    n = struct.unpack("!H", body[2:4])[0]
                    self.subs.append((body[4:4 + n].decode(), c))
                    c.sendall(m.packet(m.SUBACK, 0, body[:2] + b"\x00"))
                elif t == m.PINGREQ:
                    c.sendall(m.packet(m.PINGRESP, 0, b""))
                elif t == m.DISCONNECT:
                    will = None
                    return
        except (OSError, ConnectionError):
            if will:
                self._route(*will)

    def _route(self, topic, payload, retain=False):
        self.log.append((topic, payload))
        if retain:
            self.retained[topic] = payload
        for pat, c in list(self.subs):
            if topic == pat or (pat.endswith("#") and topic.startswith(pat[:-1])):
                try:
                    c.sendall(m.publish_packet(topic, payload))
                except OSError:
                    pass

    def inject(self, topic, payload):
        self._route(topic, payload)

    def close(self):
        self.srv.close()
        for c in self.clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_mqtt():
    b = Broker()
    got = []
    c = m.MqttClient("t1", "127.0.0.1", b.port, keepalive=2, will=("n/status", b"offline", True), log=lambda s: None)
    c.on_message = lambda t, p: got.append((t, p))
    c.start()
    c.subscribe("n/cmd/#")
    check(wait(lambda: c.connected), "mqtt connect")
    c.publish("n/status", "online", qos=1, retain=True)
    for i in range(50):
        c.publish("n/state", json.dumps({"i": i}))
    check(wait(lambda: sum(t == "n/state" for t, _ in b.log) == 50), "mqtt 50 x QoS0 delivered in order")
    check(b.retained.get("n/status") == b"online", "mqtt retained status")
    check(wait(lambda: not c._unacked), "mqtt QoS1 acknowledged")
    b.inject("n/cmd/calibrate", b'{"T": [1]}')
    check(wait(lambda: got == [("n/cmd/calibrate", b'{"T": [1]}')]), "mqtt inbound subscribe")
    time.sleep(2.5)
    check(c.connected, "mqtt keepalive (PINGREQ) keeps the link")
    # broker restart: messages published meanwhile arrive after reconnect
    port = b.port
    b.close()
    check(wait(lambda: not c.connected), "mqtt notices broker loss")
    for i in range(5):
        c.publish("n/state", "offline-%d" % i, qos=1)     # QoS 1: delivery guaranteed across reconnect
    b2 = Broker(port)
    check(wait(lambda: {p for _, p in b2.log if p.startswith(b"offline-")} == {b"offline-%d" % i for i in range(5)}, 15),
          "mqtt reconnect + queued QoS1 delivered")
    # unclean loss -> will
    for s in list(b2.clients):
        s.close()
    c.stop(0.5)
    b2.close()
    b3 = Broker()
    c3 = m.MqttClient("t3", "127.0.0.1", b3.port, will=("w/status", b"offline", True), log=lambda s: None).start()
    wait(lambda: c3.connected)
    c3._stop = True
    c3._sock.shutdown(socket.SHUT_RDWR)                   # no DISCONNECT: broker must publish the will
    check(wait(lambda: b3.retained.get("w/status") == b"offline"), "mqtt last will on unclean loss")
    b3.close()
    # queue bound: QoS 0 dropped first, QoS 1 kept
    c4 = m.MqttClient("t4", "127.0.0.1", 1, queue_len=4, log=lambda s: None)
    c4.publish("a", "x", qos=1)
    for i in range(10):
        c4.publish("b", str(i))
    check(len(c4._q) == 4 and c4._q[0][2] == 1 and c4.stats["dropped"] == 7, "mqtt bounded queue drops QoS0 first")


# ---------------- fake AXI DMA whose PL is the integer model ----------------
class FakeDmaRegs:
    def __init__(self, buf, buf_phys, model):
        self.r, self.buf, self.phys, self.model, self.calls = {}, buf, buf_phys, model, 0

    def __getitem__(self, i):
        return self.r.get(i, 0)

    def __setitem__(self, i, v):
        if i * 4 in (0x04, S2MM_SR):
            self.r[i] = 0                                  # write 1 to clear
            return
        if i * 4 in (0x00, 0x30):                         # CR
            if int(v) & 0x4:                               # soft reset: both channels back to halted
                self.r[0x00 // 4] = self.r[0x30 // 4] = 0x00010002 & ~0x2 | 0x10000
                return
            self.r[i] = int(v)
            return
        self.r[i] = int(v)
        if i * 4 == MM2S_LEN:                              # transfer starts: run the "PL"
            assert self.r[0x00 // 4] & 1 and self.r[0x30 // 4] & 1, "transfer while a channel is halted"
            n_pix = self.r[MM2S_LEN // 4]
            src = self.r[MM2S_SA // 4] - self.phys
            x = np.frombuffer(bytes(self.buf[src:src + n_pix]), np.uint8)
            codes = self.model(x).astype("<i4").tobytes()
            assert len(codes) == self.r[S2MM_LEN // 4], "S2MM length != results produced"
            dst = self.r[S2MM_DA // 4] - self.phys
            self.buf[dst:dst + len(codes)] = codes
            self.r[S2MM_SR // 4] = IOC
            self.calls += 1


def test_dma(gen_dir, qmodel_path):
    from qcnn_int import forward_int, load_qmodel
    q = load_qmodel(qmodel_path)
    n_pixel, n_out = 2 * 56 * 56, 9
    x = np.array([int(v, 16) for v in (gen_dir / "tv_input.mem").read_text().split()], np.uint8).reshape(-1, 2, 56, 56)
    want = np.array([int(v, 16) for v in (gen_dir / "tv_output.mem").read_text().split()], np.int64)
    want = np.where(want >= 32768, want - 65536, want).reshape(-1, n_out)
    phys = 0x7FF00000
    buf = bytearray(layout(n_pixel, n_out, 16)[1])
    regs = FakeDmaRegs(buf, phys, lambda b: np.asarray(forward_int(q, b.reshape(-1, 2, 56, 56))).reshape(-1))
    dma = QcnnDma(regs, buf, phys, n_pixel, n_out, max_batch=16)
    got = np.concatenate([dma.run(x[i:i + 5]) for i in range(0, len(x), 5)])
    check(got.shape == want.shape and (got == want).all(),
          "QcnnDma dT shape %s, %d calls, matches RTL golden tv_output.mem" % (got.shape, regs.calls))
    return q


def test_overlay():
    import ui_layout as L
    lay = json.load(open(str(ROOT / "board" / "fz3b" / "ui" / "layout.json")))
    regs = fsf_ui.FakeRegs()
    scr = fsf_ui.Screen(regs, lay)
    temps = [31.0, 35.0, 40.0, 45.0, 50.0, 55.0, 30.0, 30.0, 30.0]
    scr.show(temps, [1] * 4 + [0] + [1] * 4, [0, 0, 1] + [0] * 6, [0.01] * 9, dict(step=7, mqtt=True))
    prof = [regs.mem[fsf_ui.PROF0 + 4 * i] for i in range(256)]
    xs = lay["sensor_x"]
    at = lambda x: prof[int(x) // 4]                                     # noqa: E731
    first = regs.writes
    scr.show(temps, [1] * 4 + [0] + [1] * 4, [0, 0, 1] + [0] * 6, [0.01] * 9, dict(step=7, mqtt=True))
    again = regs.writes - first
    text = scr.text_ram()
    f = L.sensor_field(2)["temp"]
    s = "".join(chr(text[f[1] * 128 + f[0] + i] & 255) if text[f[1] * 128 + f[0] + i] & 255 else " " for i in range(9))
    check(at(xs[0]) == fsf_ui.temp_index(31.0) and at(xs[3]) == fsf_ui.temp_index(45.0) and prof[0] == prof[1]
          and at(xs[5]) == 255 and prof[-1] == fsf_ui.temp_index(30.0),
          "screen profile: sensor values at their x, clamped, invalid S2 skipped")
    check(s.strip() == "40.00\x80C" and (text[f[1] * 128 + f[0]] >> 8) & 7 == L.ALARM,
          "screen text: card S1 shows 40.00 degC in alarm colour (%r)" % s)
    check(again == 256, "screen: unchanged text is not rewritten (%d writes on repeat)" % again)


def test_node(qmodel_path):
    import speckle_node as sn
    b = Broker()
    sub = []
    args = sn.argparse.Namespace(node="t", in_h=56, q_scale=32.0, q_zero=128.0, dt_lsb=1.0 / 1024,
                                 t_alarm=48.0, rate_alarm=3.0, demo=True)
    mq = m.MqttClient("t", "127.0.0.1", b.port, log=lambda s: None).start()
    mq.subscribe("fsf/t/cmd/#")
    wait(lambda: mq.connected)
    regs = fsf_ui.FakeRegs()
    lay = json.load(open(str(ROOT / "board" / "fz3b" / "ui" / "layout.json")))
    node = sn.Node(args, sn.FakeSource(), sn.GoldenBackend(str(qmodel_path)), fsf_ui.Screen(regs, lay), mq)
    threading.Thread(target=node.capture_loop, daemon=True).start()
    wait(lambda: node.latest is not None)
    node.run_step()                                          # first frame becomes the reference
    b.inject("fsf/t/cmd/calibrate", json.dumps({"T": [35.0] * 9}).encode())
    check(wait(lambda: node.calibrated.all()), "node calibrate via MQTT")
    msgs = []
    for _ in range(5):
        wait(lambda: node.latest is not None)
        msg = node.run_step()
        msgs.append(msg)
    states = lambda: [json.loads(p) for t, p in b.log if t == "fsf/t/state"]
    check(wait(lambda: len(states()) == 5), "node publishes one state per step")
    s = states()[-1]
    T_exp = 35.0 + sum(np.array(x["dT"]) for x in msgs)
    check(np.allclose(s["T"], T_exp, atol=1e-2) and len(s["dT"]) == 9, "node accumulates dT into T")
    prof_writes = sum(1 for k in regs.mem if fsf_ui.PROF0 <= k < fsf_ui.PROF0 + 1024)
    check(prof_writes == 256 and regs.mem.get(fsf_ui.TEXT0 + 4 * (19 * 128 + 1)) == ord("#") | (2 << 8),
          "node draws the screen each step (profile + status line)")
    print("     step latency ms:", s["ms"])
    mq.stop(0.5)
    b.close()


def test_win_fusion():
    """win_fusion: windows fill, then a pattern change raises the score and the 3-in-a-row alarm fires once."""
    import win_fusion as wf
    from qcnn_int import forward_int, load_qmodel
    q = load_qmodel(str(ROOT / "data" / "fusion" / "qmodel_win.npz"))
    cfg = json.load(open(str(ROOT / "data" / "fusion" / "fusion.json")))

    class Golden:
        def run(self, x):
            return forward_int(q, x)
    det = wf.WinFusion(cfg, Golden())
    rng = np.random.RandomState(0)
    a, b = rng.randn(400, 640).astype(np.float32), rng.randn(400, 640).astype(np.float32)
    states, events = [], 0
    for t in range(0, 600, 2):
        img = a if t < 300 else b                                   # pattern swap at 300 s
        st = det.push(float(t), img + 0.05 * rng.randn(400, 640).astype(np.float32))
        states.append((t, dict(st)))
        events += int(st["event"])
    ready = [t for t, st in states if st["ready"]]
    quiet = [st["score"] for t, st in states if st["ready"] and t < 300]
    hot = [st["score"] for t, st in states if st["ready"] and 360 <= t < 420]
    first = cfg["G"] + cfg["min_cover"] * cfg["W"]                    # window A half full
    check(ready and ready[0] >= first, "win_fusion waits until window A is half full (first score at %s s, >= %g)" % (
        ready[0] if ready else None, first))
    check(max(quiet) < cfg["threshold"] < min(hot), "win_fusion: steady pattern below threshold, changed pattern above")
    check(events == 1, "win_fusion: one alarm event per episode (%d)" % events)


class FakeMmio:
    def __init__(self, init=None):
        self.r = dict(init or {})

    def read(self, off):
        return self.r.get(off, 0)

    def write(self, off, val):
        self.r[off] = val & 0xFFFFFFFF


def test_dp_live():
    import dp_live as d

    def dp_regs(hres, hfp, hsw, hbp, vres, vfp, vsw, vbp):   # as zynqmp_dp.c stores a DRM mode
        ht, vt = hres + hfp + hsw + hbp, vres + vfp + vsw + vbp
        return FakeMmio({d.DP_ENABLE_MAIN_STREAM: 1, d.DP_HTOTAL: ht, d.DP_VTOTAL: vt, d.DP_POLARITY: 3,
                         d.DP_HSWIDTH: hsw, d.DP_VSWIDTH: vsw, d.DP_HRES: hres, d.DP_VRES: vres,
                         d.DP_HSTART: ht - (hres + hfp), d.DP_VSTART: vt - (vres + vfp)})
    w720 = d.mode_to_overlay(d.read_mode(dp_regs(1280, 110, 40, 220, 720, 5, 5, 20)))
    w1080 = d.mode_to_overlay(d.read_mode(dp_regs(1920, 88, 44, 148, 1080, 4, 5, 36)))
    # same words as the RTL reset values / the 1080p testbench
    check(w720 == ((1650 << 16) | 1280, 0x80000000 | (1430 << 16) | 1390, (750 << 16) | 720,
                   0x80000000 | (730 << 16) | 725), "dp_live 720p mode -> overlay timing")
    check(w1080 == ((2200 << 16) | 1920, 0x80000000 | (2052 << 16) | 2008, (1125 << 16) | 1080,
                    0x80000000 | (1089 << 16) | 1084), "dp_live 1080p mode -> overlay timing")
    check(d.read_mode(FakeMmio()) is None, "dp_live refuses when the DP stream is off")
    vdma = FakeMmio({d.VDMA_FRMSTORE: 0, d.VDMA_STRIDE: 640,                       # 0: as read on the board
                     **{d.VDMA_START0 + 4 * k: 0x70000000 + k * 0x400000 for k in range(4)}})
    ovl, blend, av = FakeMmio({d.OVL_ID: d.OVL_MAGIC, d.OVL_CTRL: 0x0005}), FakeMmio(), \
        FakeMmio({d.AV_OUTPUT: 0x07, d.AV_CLK_SRC: 0x7})
    d.camera_setup(vdma, ovl)
    d.live_on(dp_regs(1280, 110, 40, 220, 720, 5, 5, 20), blend, av, ovl)
    check(ovl.r[d.OVL_CAMPITCH] == 1280 and ovl.r[d.OVL_CAMNFS] == 4
          and ovl.r[d.OVL_CAMADDR0 + 12] == 0x70C00000 + 80 * 1280 + 128 and ovl.r[d.OVL_CAMSIZE] == 240 << 16
          and ovl.r[d.OVL_CTRL] == 0x000D, "dp_live points the preview at the VDMA frame stores")
    check(av.r[d.AV_OUTPUT] == 0x04 and av.r[d.AV_CLK_SRC] == 0x2 and av.r[d.AV_LIVE_VID_CONFIG] == 1
          and blend.r[d.BL_GLOBAL_ALPHA] == 1 and blend.r[d.BL_LAYER_CTRL_VID] == 2 and blend.r[d.BL_IN1CSC_COEFF0 + 16] == 0x1000,
          "dp_live: video layer live/PL clock/ext timing, graphics hidden, gfx bits untouched")
    try:
        d.live_on(dp_regs(1920, 88, 44, 148, 1080, 4, 5, 36), blend, av, ovl)
        check(False, "dp_live refuses 1080p")
    except RuntimeError:
        check(True, "dp_live refuses 1080p (vclk timed for 720p)")
    w480 = d.mode_to_overlay(d.read_mode(dp_regs(800, 40, 128, 88, 480, 13, 3, 29)))
    check(w480 == ((1056 << 16) | 800, 0x80000000 | (968 << 16) | 840, (525 << 16) | 480,
                   0x80000000 | (496 << 16) | 493), "dp_live 800x480 panel mode -> overlay timing")
    d.live_off(blend, av)
    check(av.r[d.AV_OUTPUT] == 0x07 and av.r[d.AV_CLK_SRC] == 0x7, "dp_live off restores PS clock / internal timing")


if __name__ == "__main__":
    gen_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "src" / "rtl" / "gen"
    qmodel_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "fusion" / "qmodel_win.npz"
    if not qmodel_path.exists():
        from make_dt_testvectors import make_dt_qmodel
        from qcnn_int import load_qmodel, save_qmodel
        save_qmodel(make_dt_qmodel(load_qmodel(str(ROOT / "data" / "qmodel_t1.npz"))),
                    str(qmodel_path))
    test_mqtt()
    test_dma(gen_dir, qmodel_path)
    test_overlay()
    test_node(qmodel_path)
    test_dp_live()
    test_win_fusion()
    print("ALL PASS" if not FAILS else "FAILED: %s" % FAILS)
    sys.exit(1 if FAILS else 0)
