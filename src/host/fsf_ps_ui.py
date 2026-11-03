"""Node screen rendered on the PS (Python 3.6, numpy 1.13, PIL 5.1, OpenCV optional).

Replaces the PL text overlay as the user interface; the PL overlay keeps running but
is not shown (do not run dp_live.py on: it hides the graphics layer this draws on).

Data in:
  node     : speckle_node.py --ui-udp 127.0.0.1:47110 sends every step's state here (UDP JSON);
             calibrate commands go back to the sender
  温控     : --telemetry http://127.0.0.1:8877 (acquisition page /api/telemetry, thermocouples)
  camera   : stream.py /raw on 127.0.0.1:8080, ~6 fps, only while a speckle view is on screen
  touch    : evdev of the USB touch panel (ui_layout.TOUCH_DEV), taps only
Out: DRM dumb buffers, RGB565, 800x480 (needs DRM master: stop lxdm first), or PNG files.

    systemctl stop lxdm                                     # Xorg holds the DRM device
    python3 fsf_ps_ui.py --telemetry http://127.0.0.1:8877
    python3 fsf_ps_ui.py --out png --shots ../../build/ui_ps --raw-npy rec_2.npy   # Mac preview
Pipe: board/fz3b/ui/pipe_ps.npz (tools/ui/build_ps_pipe.py from the Blender passes), water recoloured
per frame as a + water * colour(T(x)), the rule the PL overlay uses.
Pages: 启动 -> 总览 / 测点 (-> 详情) / 散斑 / 告警 / 系统 (-> 退出); alarm toast on top.
Fiber alarm (node --win-fusion, win_fusion.py): msg["fiber"] = fused windowed score, threshold, alarm.
Shown as a strip with a 10 min score line on 总览, an entry in 告警, a toast and 光纤 in the top bar.
"""
import argparse
import json
import math
import os
import socket
import struct
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

import ps_gfx as G
import ui_filter as F
import ui_layout as L
from ps_gfx import C

IDS = [7, 16, 1, 12, 2, 9, 4, 3, 10]                    # dT order, S<id>
HIST_S = 600.0                                          # trend window, s
RISE = 1.0                                              # degC/min: "rising" marker (below the alarm)
RISE_MIN = 6.0                                          # s above RISE before a "rising" entry is logged
ALARM_HOLD = 15.0                                       # s without alarm / rise before an entry closes
TOP, NAV = 36, 436                                      # top bar bottom, nav bar top
TABS = [("home", "总览"), ("points", "测点"), ("speckle", "散斑"), ("alarm", "告警"), ("system", "系统")]


def chevron(d, x, y, col):
    d.line([(x, y - 4), (x + 4, y), (x, y + 4)], fill=col, width=1)


def spread(ys, gap):
    """Push label y positions apart to at least gap px, keeping their order."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    out = list(ys)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < gap:
            out[b] = out[a] + gap
    return out


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


# ---------------- state ----------------
class State:
    def __init__(self, t_alarm=48.0, rate_alarm=3.0):
        self.lock = threading.Lock()
        self.t_alarm, self.rate_alarm = t_alarm, rate_alarm
        self.msg = None
        self.msg_t = 0.0
        self.hist = []                                  # (t, [9 temps or None])
        self.ref, self.ref_t = None, 0.0                # thermocouples, dT order
        self.events = []                                # alarm log, newest first
        self.open = {}                                  # sensor k -> open event
        self.rising_open = {}
        self.toast = None                               # (t_start, k)
        self.cam_ok = False
        self.node_addr = None
        self.t0 = time.time()
        self.spike = F.SpikeFilter(9, jump=0.4)         # display only: one-sample outliers held back
        self.cut_t = 0.0                                # rates / trends restart here (node restart)
        self.fiber = None                               # last msg["fiber"] (windowed fused alarm)
        self.fiber_hist = []                            # (t, score) for the strip line
        self.fiber_open = None                          # open fiber alarm entry

    # --- node feed ---
    def on_msg(self, m, now=None):
        now = now or time.time()
        with self.lock:
            ui = m.get("ui", {})
            self.t_alarm = ui.get("t_alarm", self.t_alarm)
            self.rate_alarm = ui.get("rate_alarm", self.rate_alarm)
            first = self.msg is None
            if self.msg is not None and m["step"] < self.msg["step"]:   # node restarted: new segment
                self.spike = F.SpikeFilter(9, jump=0.4)
                self.hist.append((now - 1e-3, [None] * 9))      # gap in the trend lines
                self.cut_t = now
            m = dict(m, T_node=m["T"], T=self.spike(m["T"], bypass=m["alarm"]))
            self.msg, self.msg_t = m, now
            self.hist.append((now, m["T"]))
            while self.hist and now - self.hist[0][0] > HIST_S:
                self.hist.pop(0)
            if first:
                self.log(now, None, "c", "节点上线", "fsf/%s/status → online" % ui.get("node", "fz3b"), done=True)
            self.on_fiber(m.get("fiber"), now)
            rate = self.rates()
            for k in range(9):
                T = m["T"][k]
                r = rate[k]
                if m["alarm"][k]:
                    w = self.rising_open.pop(k, None)
                    if w is not None and w["e"] is not None:
                        w["e"]["done"] = True
                    e = self.open.get(k)
                    if e is None:
                        kind = "温度超限 %.0f °C" % self.t_alarm if T is not None and T > self.t_alarm else "温升速率超限"
                        e = self.log(now, k, "a", "S%d %s" % (IDS[k], kind), "")
                        self.open[k] = e
                        self.toast = (now, k)
                    e["last"] = now
                    e["peak"] = max(e.get("peak", -1e9), T if T is not None else -1e9)
                    e["rate"] = max(e.get("rate", 0.0), abs(r or 0.0))
                    e["sub"] = "峰值 %.2f °C · 最大速率 %+.1f °C/min · 阈值 %.1f °C/min" % (
                        e["peak"], e["rate"], self.rate_alarm)
                elif k in self.open and now - self.open[k]["last"] > ALARM_HOLD:
                    e = self.open.pop(k)                   # closes only after ALARM_HOLD s without alarm
                    e["done"] = True
                    e["sub"] = "峰值 %.2f °C，持续 %d s" % (e["peak"], e["last"] - e["t"])
                if k in self.open:
                    continue
                w = self.rising_open.get(k)
                if r is not None and r > RISE:
                    if w is None:
                        w = self.rising_open[k] = dict(start=now, e=None)
                    w["last"] = now
                    if w["e"] is None and now - w["start"] >= RISE_MIN:
                        w["e"] = self.log(w["start"], k, "w", "S%d 温度上升" % IDS[k], "")
                    if w["e"] is not None:
                        w["e"]["sub"] = "%.2f °C，%+.1f °C/min" % (T, r)
                elif w is not None and now - w["last"] > ALARM_HOLD:
                    self.rising_open.pop(k)
                    if w["e"] is not None:
                        w["e"]["done"] = True

    def on_fiber(self, fb, now):
        """Fiber alarm: entry opens on alarm, closes ALARM_HOLD s after it clears."""
        self.fiber = fb
        if not fb:
            return
        if fb.get("score") is not None and (not self.fiber_hist or now - self.fiber_hist[-1][0] > 4.0):
            self.fiber_hist.append((now, fb["score"]))
            while self.fiber_hist and now - self.fiber_hist[0][0] > HIST_S:
                self.fiber_hist.pop(0)
        if fb.get("alarm"):
            e = self.fiber_open
            if e is None:
                e = self.fiber_open = self.log(now, None, "a", "光纤沿线温升报警", "")
                self.toast = (now, None)
            e["last"] = now
            e["peak"] = max(e.get("peak", -1e9), fb["score"])
            e["sub"] = "报警得分峰值 %.2f（门限 %.2f）" % (e["peak"], fb["threshold"])
        elif self.fiber_open is not None and now - self.fiber_open["last"] > ALARM_HOLD:
            e, self.fiber_open = self.fiber_open, None
            e["done"] = True
            e["sub"] = "报警得分峰值 %.2f，持续 %d s" % (e["peak"], e["last"] - e["t"])

    def log(self, t, k, level, text, sub, done=False):
        e = dict(t=t, k=k, level=level, text=text, sub=sub, done=done)
        self.events.insert(0, e)
        del self.events[60:]
        return e

    def rates(self, span=30.0):
        """degC/min per sensor, least-squares slope over the last span s (None if too few points)."""
        if len(self.hist) < 3:
            return [None] * 9
        t_end = self.hist[-1][0]
        seg = [(t, T) for t, T in self.hist if t >= self.cut_t]
        pts = [(t, T) for t, T in seg if t_end - t <= span]
        if len(pts) < 3:
            pts = seg[-3:]
        out = []
        for k in range(9):
            xs = [t for t, T in pts if T[k] is not None]
            ys = [T[k] for t, T in pts if T[k] is not None]
            if len(xs) < 3:
                out.append(None)
                continue
            x = np.asarray(xs) - xs[0]
            y = np.asarray(ys)
            v = ((x - x.mean()) ** 2).sum()
            out.append(float(((x - x.mean()) * (y - y.mean())).sum() / v * 60.0) if v > 0 else None)
        return out

    def snapshot(self):
        with self.lock:
            m = self.msg
            return dict(msg=m, hist=list(self.hist), ref=self.ref if time.time() - self.ref_t < 10 else None,
                        events=list(self.events), rates=self.rates(), age=time.time() - self.msg_t,
                        t_alarm=self.t_alarm, rate_alarm=self.rate_alarm,
                        fiber=self.fiber, fiber_hist=list(self.fiber_hist))


# ---------------- inputs ----------------
def feed_loop(state, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    while True:
        data, peer = s.recvfrom(65536)
        try:
            m = json.loads(data.decode())
        except ValueError:
            continue
        state.node_addr = (s, peer)
        state.on_msg(m)


def send_node(state, cmd):
    if state.node_addr is None:
        return False
    s, peer = state.node_addr
    try:
        s.sendto(json.dumps(cmd).encode(), peer)
        return True
    except OSError:
        return False


def telemetry_loop(state, url):
    from urllib.request import ProxyHandler, build_opener
    op = build_opener(ProxyHandler({}))
    url = url.rstrip("/") + "/api/telemetry"
    while True:
        try:
            d = json.loads(op.open(url, timeout=3).read().decode())
            temps = d.get("temp") or []
            ref = [temps[i - 1] if i - 1 < len(temps) else None for i in IDS]
            with state.lock:
                state.ref, state.ref_t = ref, time.time()
        except Exception:
            pass
        time.sleep(1.0)


def raw_read(host="127.0.0.1", port=8080):
    """One fresh frame from stream.py /raw (same protocol as speckle_node.RawSource)."""
    s = socket.create_connection((host, port), timeout=5)
    try:
        s.sendall(b"GET /raw HTTP/1.1\r\nHost: local\r\n\r\n")
        f = s.makefile("rb", buffering=1 << 20)
        while f.readline() not in (b"\r\n", b""):
            pass
        while not f.readline().strip().startswith(b"--raw"):
            pass
        hdr = {}
        while True:
            line = f.readline().strip()
            if not line:
                break
            k, _, v = line.partition(b":")
            hdr[k.strip().lower()] = v.strip()
        data = f.read(int(hdr[b"content-length"]))
        return np.frombuffer(data, np.uint8).reshape(int(hdr[b"x-height"]), int(hdr[b"x-width"]))
    finally:
        s.close()


def camera_loop(ui, state, fps, npy=None):
    frames = np.load(npy, mmap_mode="r") if npy else None
    i = 0
    while True:
        t = time.time()
        if ui.page in ("home", "speckle") and not ui.blank:
            try:
                if frames is not None:
                    raw = np.asarray(frames[i % len(frames)])
                    i += 1
                else:
                    raw = raw_read()
                ui.spk.push(time.time(), raw)
                state.cam_ok = True
            except Exception:
                state.cam_ok = False
                time.sleep(1.0)
        time.sleep(max(0.0, 1.0 / fps - (time.time() - t)))


def touch_loop(ui, dev):
    """evdev taps -> ui.tap(x, y). input_event on aarch64: timeval (2 x int64), u16 type, u16 code, s32 value."""
    EV_KEY, EV_ABS, BTN_TOUCH = 1, 3, 0x14A
    fmt = "qqHHi"
    n = struct.calcsize(fmt)
    while True:
        try:
            f = open(dev, "rb")
        except OSError:
            time.sleep(2.0)
            continue
        rx = ry = None
        down = False
        with f:
            while True:
                ev = f.read(n)
                if len(ev) < n:
                    break
                _, _, typ, code, val = struct.unpack(fmt, ev)
                if typ == EV_ABS and code in (0x00, 0x35):
                    rx = val
                elif typ == EV_ABS and code in (0x01, 0x36):
                    ry = val
                elif typ == EV_KEY and code == BTN_TOUCH:
                    if val:
                        down = True
                    elif down and rx is not None and ry is not None:
                        down = False
                        ui.tap(*L.touch_to_screen(rx, ry))


# ---------------- outputs ----------------
class DrmOut:
    """Two dumb buffers on the DP CRTC, flipped with the legacy PAGE_FLIP ioctl (no event)."""

    def __init__(self, mode="800x480"):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "board", "fz3b"))
        import fcntl
        import mmap
        import drm_mode_test as D
        self.fcntl, self.D = fcntl, D
        clk, hd, hss, hse, ht, vd, vss, vse, vt, flags = D.MODES[mode]
        self.fd = os.open("/dev/dri/card0", os.O_RDWR)
        res = D.resources(self.fd)
        conn = [c for c in (D.connector(self.fd, c) for c in res["connectors"]) if c["connected"]]
        if not conn:
            raise RuntimeError("no connected DP monitor")
        conn = conn[0]
        crtc = (D.encoder(self.fd, conn["encoder"])[2] if conn["encoder"] else 0) or res["crtcs"][0]
        self.crtc, self.bufs = crtc, []
        for _ in range(2):
            d = bytearray(32)
            struct.pack_into("4I", d, 0, vd, hd, 16, 0)
            fcntl.ioctl(self.fd, D.CREATE_DUMB, d)
            handle, pitch, size = struct.unpack_from("IIQ", d, 16)
            f = bytearray(28)
            struct.pack_into("7I", f, 0, 0, hd, vd, pitch, 16, 16, handle)
            fcntl.ioctl(self.fd, D.ADDFB, f)
            fb = struct.unpack_from("I", f, 0)[0]
            mp = bytearray(16)
            struct.pack_into("I", mp, 0, handle)
            fcntl.ioctl(self.fd, D.MAP_DUMB, mp)
            m = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                          offset=struct.unpack_from("Q", mp, 8)[0])
            self.bufs.append((fb, m, pitch))
        self.pitch = self.bufs[0][2]
        conns = bytearray(struct.pack("I", conn["id"]))
        self._conns = conns
        modeinfo = struct.pack("I5H5HIII32s", clk, hd, hss, hse, ht, 0, vd, vss, vse, vt, 0, 60, flags, 0,
                               mode.encode())
        s = bytearray(104)
        struct.pack_into("QIIIIIII", s, 0, D.ctypes_addr(conns), 1, crtc, self.bufs[0][0], 0, 0, 0, 1)
        s[36:104] = modeinfo
        fcntl.ioctl(self.fd, D.SETCRTC, s)
        self.front = 0
        self.flip_ok = True
        self.PAGE_FLIP = D.IOWR(0xB0, 24)

    def show(self, rgb):
        px = G.to565(rgb)
        back = 1 - self.front if self.flip_ok else self.front
        fb, m, pitch = self.bufs[back]
        if pitch == G.W * 2:
            m[:G.W * G.H * 2] = px.tobytes()
        else:
            for y in range(G.H):
                m[y * pitch:y * pitch + G.W * 2] = px[y].tobytes()
        if not self.flip_ok:
            return
        req = bytearray(struct.pack("IIIIQ", self.crtc, fb, 0, 0, 0))
        for _ in range(20):
            try:
                self.fcntl.ioctl(self.fd, self.PAGE_FLIP, req)
                self.front = back
                return
            except OSError as e:
                if e.errno == 16:                       # EBUSY: previous flip still pending
                    time.sleep(0.004)
                    continue
                self.flip_ok = False                    # no flip support: draw into the front buffer
                self.front = 0
                return

    def close(self):
        for _, m, _ in self.bufs:
            m.close()
        os.close(self.fd)                               # lastclose: the console mode comes back


class PngOut:
    def __init__(self, path):
        self.path = path

    def show(self, rgb):
        Image.fromarray(rgb).save(self.path)

    def close(self):
        pass


# ---------------- UI ----------------
class UI:
    def __init__(self, state, args):
        self.st, self.a = state, args
        self.tx = G.Text()
        self.spk = G.Speckle(ref_age=args.ref_age)
        self.page, self.parent, self.sel = "splash", "home", 3
        self.cam_mode = "pseudo"
        self.spk_mode = "decorr"
        self.modal = None
        self.blank = False
        self.trans = None                               # (t0, prev body array)
        self.last = None
        self.hits = []
        self.lock = threading.Lock()
        self.t_start = time.time()
        self.quit = False
        self.flash = None                               # (t, text) short confirmation line
        self.ease = [F.Eased(tau=0.8) for _ in range(9)]
        self.stat = [F.Status(on=RISE, off=0.6 * RISE, hold_s=ALARM_HOLD) for _ in range(9)]
        self.q = {}                                     # (field, k) -> Quantiser
        self.ranges = {}                                # chart key -> StickyRange
        self.ok = {k: F.Debounce(t_on=0.0, t_off=5.0) for k in ("mqtt", "ref", "node")}
        self.ema = {}                                   # small number smoothing (speckle stats)
        self.pipe = None                                # (a float32 (h, w, 3), water float32 (h, w, 1), sensor x)
        if args.pipe and os.path.exists(args.pipe):
            z = np.load(args.pipe)
            self.pipe = (z["a"].astype(np.float32), (z["water"].astype(np.float32) / 255)[..., None],
                         [float(v) for v in z["sensor_x"]])

    # --- navigation ---
    def go(self, page, now=None):
        if page == self.page:
            return
        if self.last is not None:
            self.trans = (now or time.time(), self.last[TOP:NAV].copy(), None)
        self.page = page

    def tap(self, x, y):
        with self.lock:
            if self.blank:
                self.blank = False
                return
            for (x0, y0, x1, y1), fn in reversed(self.hits):
                if x0 <= x <= x1 and y0 <= y <= y1:
                    fn()
                    return

    # --- display stage: what is drawn, stabilised ---
    def stabilise(self, snap, now):
        """Eased temperatures, debounced status and link flags. Alarms show at once (Status)."""
        m = snap["msg"]
        if m is None:
            snap["st"] = ["-"] * 9
            snap["ok"] = {k: False for k in self.ok}
            return snap
        T = [self.ease[k](m["T"][k], now) for k in range(9)]
        snap["moving"] = any(self.ease[k].moving(m["T"][k], 0.004) for k in range(9))
        snap["msg"] = dict(m, T=T)
        snap["st"] = [self.stat[k](T[k] is not None, m["alarm"][k], snap["rates"][k], now) for k in range(9)]
        ui = m.get("ui", {})
        snap["ok"] = {"mqtt": self.ok["mqtt"](ui.get("mqtt", False), now),
                      "ref": self.ok["ref"](snap["ref"] is not None, now),
                      "node": self.ok["node"](snap["age"] < 10, now)}
        return snap

    def fq(self, field, k, v, dec, sign=False):
        """Formatted value with display hysteresis (last digit does not toggle on noise)."""
        if v is None:
            return None
        q = self.q.get((field, k, dec))
        if q is None:
            q = self.q[(field, k, dec)] = F.Quantiser(dec)
        return q.fmt(v, sign)

    def rng(self, key, vmin, vmax, now, **kw):
        r = self.ranges.get(key)
        if r is None:
            r = self.ranges[key] = F.StickyRange(**{k: v for k, v in kw.items() if k != "force"})
        return r(vmin, vmax, now, kw.get("force"))

    def smooth(self, key, v, a=0.25, tag=None, rel=0.05):
        """EMA of a small readout, advanced once per new input (tag: e.g. the node step), and
        the shown value only moves when the EMA left it by more than rel (relative)."""
        if v is None:
            return self.ema.get(key, (None, None, None))[1]
        tag = v if tag is None else tag
        p = self.ema.get(key)
        if p is None:
            self.ema[key] = (tag, v, v)
            return v
        last_tag, acc, shown = p
        if tag != last_tag:
            acc = acc + a * (v - acc)
        if abs(acc - shown) > rel * max(abs(shown), 1e-6):
            shown = acc
        self.ema[key] = (tag, acc, shown)
        return shown

    # --- frame ---
    def frame(self, now=None):
        now = now or time.time()
        with self.lock:
            snap = self.stabilise(self.st.snapshot(), now)
            if self.page == "splash":
                ready = snap["msg"] is not None
                if (now - self.t_start > 2.5 and ready) or now - self.t_start > self.a.splash_max:
                    self.go("home", now)
            if self.blank:
                img = np.zeros((G.H, G.W, 3), np.uint8)
                self.last = img
                return img
            if self.trans is not None and self.trans[2] is not None:
                arr = self.trans[2].copy()                      # new page drawn at the start of the slide
            else:
                self.hits = []
                im = Image.new("RGB", (G.W, G.H), C["bg"])
                d = ImageDraw.Draw(im)
                getattr(self, "p_" + self.page)(im, d, snap, now)
                if self.page != "splash":
                    self.topbar(im, d, snap, now)
                    self.nav(im, d, snap)
                arr = np.asarray(im).copy()
                if self.trans is not None:
                    self.trans = (self.trans[0], self.trans[1], arr.copy())
            if self.trans is not None:
                t0, prev, new = self.trans
                e = ease((now - t0) / 0.32)
                if e >= 1:
                    self.trans = None
                else:
                    arr[TOP:NAV] = self._slide(prev, new[TOP:NAV], e)
            self.last = arr
            self._moving = snap.get("moving", False)
            if self.modal:
                arr = self.p_modal(arr, snap, now)
            if snap["msg"] is not None and self.st.toast and self.page != "splash":
                arr = self.p_toast(arr, snap, now)
            return arr

    @staticmethod
    def _slide(prev, new, e):
        """Old body slides 24 px left and fades out, new body comes in from +24 px."""
        s = 24
        a = np.roll(prev, -int(round(s * e)), axis=1)
        b = np.roll(new, int(round(s * (1 - e))), axis=1)
        b[:, :int(round(s * (1 - e)))] = C["bg"]
        return G.blend(a, b, e)

    def animating(self, now):
        toast = self.st.toast is not None and now - self.st.toast[0] < 0.4
        return self.trans is not None or toast or self.page == "splash" or bool(self.modal and now - self.modal[1] < 0.3)

    # ---------------- chrome ----------------
    def topbar(self, im, d, snap, now):
        d.rectangle((0, 0, G.W, TOP - 1), fill=C["bg"])
        G.hline(d, 0, G.W, TOP - 1, C["ln"])
        im.paste(G.logo(20), (16, 8))
        crumbs = {"home": [("总览", 1)], "points": [("测点", 1), ("· 9 路", 0)],
                  "detail": [("测点" if self.parent == "points" else "总览", 0), ("›", 0), ("S%d" % IDS[self.sel], 1)],
                  "speckle": [("散斑", 1), ("›", 0), (dict((k, n) for k, n, _ in G.MODES)[self.spk_mode], 0)],
                  "alarm": [("告警", 1)], "system": [("系统", 1)]}[self.page]
        x = 46
        for s, strong in crumbs:
            if s == "›":
                chevron(d, x + 1, 18, C["dm2"])
                x += 10
                continue
            x += self.tx.draw(im, (x, 18), s, "cjk", 12, C["tx"] if strong else C["dm"], mid=True) + 6
        m = snap["msg"]
        ui = (m or {}).get("ui", {})
        ok = snap["ok"]
        items = [("MQTT", ok["mqtt"]), ("温控", ok["ref"])]
        fb = snap.get("fiber")
        if fb is not None:
            items.append(("光纤 %s" % ("报警" if fb.get("alarm") else "就绪" if fb.get("ready") else "预热"),
                          bool(fb.get("ready")) and not fb.get("alarm")))
        if m is not None and ok["node"]:
            items.append(("PL 推理 %s ms" % "%.2f" % self.smooth("qcnn", m["ms"]["qcnn"], tag=m["step"]), True))
        else:
            items.append(("PL 推理", False))
        x = G.W - 16 - self.tx.size(time.strftime("%H:%M:%S"), "mono", 13)[0]
        self.tx.draw(im, (x, 18), time.strftime("%H:%M:%S"), "mono", 13, C["tx"], mid=True)
        x -= 18
        for s, ok in reversed(items):
            w = self.tx.size(s, "cjk", 11)[0]
            x -= w
            self.tx.draw(im, (x, 18), s, "cjk", 11, C["dm"], mid=True)
            G.dot(d, x - 7, 18, 3, C["ok"] if ok else C["al"])
            x -= 22
        if m is not None and m.get("demo"):
            w = self.tx.size("DEMO 权重", "cjk", 10)[0]
            G.rrect(d, (x - w - 12, 10, x, 26), 3, C["bg"], C["ac"])
            self.tx.draw(im, (x - w - 6, 18), "DEMO 权重", "cjk", 10, C["ac"], mid=True)

    def nav(self, im, d, snap):
        d.rectangle((0, NAV, G.W, G.H), fill=C["bg"])
        G.hline(d, 0, G.W, NAV, C["ln"])
        w = G.W / float(len(TABS))
        cur = "points" if self.page == "detail" else self.page
        n_open = sum(1 for e in snap["events"] if e["level"] == "a" and not e["done"])
        for i, (key, label) in enumerate(TABS):
            cx = int(w * i + w / 2)
            on = key == cur
            col = C["tx"] if on else C["dm"]
            self.icon(d, key, cx - 26, NAV + 22, col)
            self.tx.draw(im, (cx - 14, NAV + 22), label, "cjk", 12, col, mid=True)
            if key == "alarm" and n_open:
                bx = cx + 14
                G.rrect(d, (bx, NAV + 15, bx + 15, NAV + 29), 7, C["al"])
                self.tx.draw(im, (bx + 8, NAV + 22), str(n_open), "mono", 10, (255, 255, 255), align="c", mid=True)
            if on:
                d.rectangle((cx - 26, NAV, cx + 26, NAV + 1), fill=C["ac"])
            self.hits.append(((int(w * i), NAV, int(w * (i + 1)), G.H), lambda k=key: self._tab(k)))

    def _tab(self, key):
        self.modal = None
        if key == "points" and self.page == "detail" and self.parent == "points":
            self.go("points")
        else:
            self.go(key)

    def icon(self, d, key, cx, cy, col):
        if key == "home":
            d.line([(cx - 6, cy), (cx, cy - 5), (cx + 6, cy)], fill=col, width=1)
            d.rectangle((cx - 4, cy, cx + 4, cy + 5), outline=col)
        elif key == "points":
            d.line([(cx - 7, cy), (cx + 7, cy)], fill=col)
            for dx in (-4, 0, 4):
                G.dot(d, cx + dx, cy, 2, col)
        elif key == "speckle":
            d.rectangle((cx - 6, cy - 5, cx + 6, cy + 5), outline=col)
            G.dot(d, cx - 2, cy - 1, 1, col)
            G.dot(d, cx + 2, cy + 1, 2, col)
        elif key == "alarm":
            d.polygon([(cx, cy - 6), (cx + 6, cy + 5), (cx - 6, cy + 5)], outline=col)
            d.line([(cx, cy - 2), (cx, cy + 1)], fill=col)
            d.point((cx, cy + 3), fill=col)
        else:
            d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=col)
            for k in range(8):
                a = k * math.pi / 4
                d.line([(cx + 5 * math.cos(a), cy + 5 * math.sin(a)), (cx + 7 * math.cos(a), cy + 7 * math.sin(a))],
                       fill=col)

    def label(self, im, xy, s, colour=None):
        self.tx.draw(im, xy, s, "cjk", 11, colour or C["dm"], track=1)

    def card(self, d, box, fill=None, outline=None):
        G.rrect(d, box, 4, fill or C["pn"], outline or C["ln"])

    def status(self, snap, k):
        """'a' alarm, 'w' rising, 'n' normal, '-' not calibrated (debounced, see stabilise)."""
        return snap["st"][k]

    def blink_on(self, now):
        return (now % 1.2) < 0.75

    # ---------------- pages ----------------
    def p_splash(self, im, d, snap, now):
        el = now - self.t_start
        a = ease(el / 0.6)
        lg = G.logo(64)
        im.paste(lg, (G.W // 2 - 32, 96 - int(10 * (1 - a))))
        col = tuple(int(C["bg"][i] + (C["tx"][i] - C["bg"][i]) * a) for i in range(3))
        self.tx.draw(im, (G.W // 2 + 4, 196), "单光纤多点温度场感知终端", "cjk", 22, col, align="c", track=4)
        self.tx.draw(im, (G.W // 2, 240), "多模光纤散斑 · FPGA 边缘推理", "cjk", 12,
                     tuple(int(C["bg"][i] + (C["dm"][i] - C["bg"][i]) * a) for i in range(3)), align="c", track=2)
        m = snap["msg"]
        checks = [("相机 OV9281 · MIPI CSI-2", self.st.cam_ok or bool(self.a.raw_npy)),
                  ("PL ΔT 推理核", m is not None),
                  ("温控链路", snap["ref"] is not None or not self.a.telemetry),
                  ("MQTT · %s" % self.a.broker_label, bool(m and m.get("ui", {}).get("mqtt")))]
        x0, x1, y = 250, 550, 300
        for i, (s, ok) in enumerate(checks):
            if el < 0.3 + 0.25 * i:
                break
            self.tx.draw(im, (x0, y + 22 * i), s, "cjk", 11, C["dm"])
            self.tx.draw(im, (x1, y + 22 * i), "就绪" if ok else "连接中…", "cjk", 11, C["ok"] if ok else C["dm2"],
                         align="r")
        p = min(1.0, 0.25 * sum(ok for _, ok in checks) * 0.6 + 0.4 * min(1.0, el / 2.5))
        d.rectangle((x0, 396, x1, 397), fill=C["ln"])
        xe = x0 + int((x1 - x0) * p)
        for x in range(x0, xe):
            d.line([(x, 396), (x, 397)], fill=G.temp_rgb(30 + 20 * (x - x0) / float(x1 - x0)))

    # --- home ---
    def p_home(self, im, d, snap, now):
        m = snap["msg"]
        sx = [x / float(G.W) for x in self.a.sensor_x]
        x0, x1 = 46, 754
        self.tx.draw(im, (x0, 62), "冷却液流向", "cjk", 10, C["dm"])
        d.line([(x0 + 58, 69), (x0 + 118, 69)], fill=C["dm2"])
        d.polygon([(x0 + 118, 66), (x0 + 124, 69), (x0 + 118, 72)], fill=C["dm2"])
        # colour scale
        self.tx.draw(im, (x1 - 186, 68), "30", "mono", 10, C["dm"], align="r", mid=True)
        for i in range(120):
            d.line([(x1 - 180 + i, 66), (x1 - 180 + i, 70)], fill=G.temp_rgb(30 + 20 * i / 119.0))
        self.tx.draw(im, (x1, 68), "50 °C", "mono", 10, C["dm"], align="r", mid=True)
        # pipe: 3D render recoloured every frame, colour = axial profile interpolated between
        # sensors, held flat past the first / last one (same rule as fsf_ui.profile / the PL)
        T = m["T"] if m else [None] * 9
        if self.pipe is not None:
            pa, pw, psx = self.pipe
            px0, py0 = (G.W - pa.shape[1]) // 2, 84
            pts = sorted((x, t) for x, t in zip(psx, T) if t is not None)
            if pts:
                t = np.interp(np.arange(pa.shape[1]), [p[0] for p in pts], [p[1] for p in pts])
                idx = np.round((0.18 + 0.82 * np.clip((t - 30.0) / 20.0, 0, 1)) * 255).astype(np.int64)
                rgb = pa + pw * G.INFERNO[idx].astype(np.float32)[None]
                tile = np.clip(rgb, 0, 255).astype(np.uint8)
            else:
                tile = pa.astype(np.uint8)
            im.paste(Image.fromarray(tile), (px0, py0))
            xs = [px0 + x for x in psx]
            y_lab = py0 + pa.shape[0] + 4
        else:                                                # no asset: flat bar
            xs = [int(x0 + (x1 - x0) * (0.04 + 0.92 * (x - min(sx)) / max(1e-6, max(sx) - min(sx)))) for x in sx]
            pts = sorted((x, t) for x, t in zip(xs, T) if t is not None)
            for xx in range(x0, x1):
                tt = np.interp(xx, [p[0] for p in pts], [p[1] for p in pts]) if pts else None
                d.line([(xx, 96), (xx, 116)], fill=G.temp_rgb(tt) if tt is not None else C["pn2"])
            y_lab = 124
        for k in range(9):
            cx = int(round(xs[k]))
            st = self.status(snap, k)
            col = C["al"] if st == "a" else C["ac"] if st == "w" else C["tx"] if st == "n" else C["dm2"]
            s = self.fq("pipe", k, T[k], 1) or "--"
            self.tx.draw(im, (cx, y_lab), s, "mono", 15, col, align="c")
            self.tx.draw(im, (cx, y_lab + 22), "S%d" % IDS[k], "cjk", 10, C["dm"], align="c")
            if st == "a" and self.blink_on(now):
                G.tri_down(d, cx, 79, 5, C["al"])
            self.hits.append(((cx - 32, 76, cx + 32, y_lab + 36), lambda k=k: self._detail(k, "home")))
        self.fiber_strip(im, d, snap, now, (16, y_lab + 36, 784, y_lab + 56))
        # camera card
        cy0, cy1 = (196, 422) if snap.get("fiber") is None else (y_lab + 62, 428)
        self.card(d, (16, cy0, 316, cy1))
        self.label(im, (26, cy0 + 9), "散斑 · 实时")
        segs = [("raw", "原始"), ("pseudo", "伪彩"), ("decorr", "去相关")]
        sx0 = 306 - sum(self.tx.size(n, "cjk", 10)[0] + 14 for _, n in segs) - 4
        G.rrect(d, (sx0, cy0 + 6, 306, cy0 + 24), 3, C["bg"])
        x = sx0 + 2
        for key, n in segs:
            w = self.tx.size(n, "cjk", 10)[0] + 14
            if key == self.cam_mode:
                G.rrect(d, (x, cy0 + 8, x + w, cy0 + 22), 2, C["pn2"])
            self.tx.draw(im, (x + w // 2, cy0 + 15), n, "cjk", 10, C["tx"] if key == self.cam_mode else C["dm"],
                         align="c", mid=True)
            self.hits.append(((x, cy0 + 4, x + w, cy0 + 26), lambda k=key: setattr(self, "cam_mode", k)))
            x += w
        v = self.spk.view(self.cam_mode, (280, 175))
        if v is not None:
            im.paste(Image.fromarray(v), (26, cy0 + 32))
        else:
            d.rectangle((26, cy0 + 32, 305, cy0 + 206), fill=C["bg"])
            self.tx.draw(im, (166, cy0 + 119), "等待相机", "cjk", 11, C["dm2"], align="c", mid=True)
        self.hits.append(((26, cy0 + 32, 306, cy0 + 207), lambda: self._speckle(self.cam_mode)))
        stt = self.spk.stats
        rho, K, fps = self.spk_stats(stt)
        kv = [("相关 ρ", rho or "—"), ("对比度 K", K or "—"), ("帧率", fps)]
        x = 26
        for k_, v_ in kv:
            x += self.tx.draw(im, (x, cy0 + 212), k_, "cjk", 10, C["dm"]) + 4
            x += self.tx.draw(im, (x, cy0 + 212), v_, "mono", 10, C["tx"]) + 14
        # sensor cards 3x3
        gx0, gx1 = 328, 784
        cw, ch = (gx1 - gx0 - 12) / 3.0, (cy1 - cy0 - 12) / 3.0
        for k in range(9):
            c, r = k % 3, k // 3
            bx, byy = int(gx0 + c * (cw + 6)), int(cy0 + r * (ch + 6))
            box = (bx, byy, int(bx + cw), int(byy + ch))
            self.sensor_card(im, d, box, k, snap, now)
            self.hits.append((box, lambda k=k: self._detail(k, "home")))

    def sensor_card(self, im, d, box, k, snap, now):
        st = self.status(snap, k)
        m = snap["msg"]
        x0, y0, x1, y1 = box
        if st == "a":
            G.rrect(d, box, 4, C["alpn"], (110, 40, 42))
        else:
            self.card(d, box)
        self.tx.draw(im, (x0 + 10, y0 + 7), "S%d" % IDS[k], "cjk", 10, C["dm"])
        if st == "a":
            if self.blink_on(now):
                self.tx.draw(im, (x1 - 10, y0 + 7), "速率" if (snap["rates"][k] or 0) > snap["rate_alarm"] else "超温",
                             "cjk", 10, C["al"], align="r")
                G.tri_up(d, x1 - 40, y0 + 14, 4, C["al"])
        elif st == "w":
            self.tx.draw(im, (x1 - 10, y0 + 7), "上升", "cjk", 10, C["ac"], align="r")
            d.line([(x1 - 38, y0 + 18), (x1 - 38, y0 + 10)], fill=C["ac"])
            d.polygon([(x1 - 41, y0 + 12), (x1 - 38, y0 + 8), (x1 - 35, y0 + 12)], fill=C["ac"])
        elif st == "n":
            G.dot(d, x1 - 13, y0 + 14, 3, C["ok"])
        T = m["T"][k] if m else None
        col = C["al"] if st == "a" else C["ac"] if st == "w" else C["tx"]
        if T is None:
            self.tx.draw(im, (x0 + 10, y0 + 22), "--.--", "mono", 19, C["dm2"])
        else:
            w = self.tx.draw(im, (x0 + 10, y0 + 22), self.fq("card", k, T, 2), "mono", 19, col)
            self.tx.draw(im, (x0 + 12 + w, y0 + 29), "°C", "cjk", 10, C["dm"])
            r = snap["rates"][k]
            self.tx.draw(im, (x0 + 10, y0 + 47), "%s °C/min" % (self.fq("crate", k, r, 1, sign=True) or "—"),
                         "mono", 10, C["dm"])
        ys = [Tt[k] for _, Tt in snap["hist"][-60:]]
        ys = [v for v in ys if v is not None]
        if len(ys) > 2:
            lo, hi = self.rng(("spark", k), min(ys), max(ys), now, step=0.5, span=1.0, pad=0.1, shrink_s=60.0)
            spark_col = C["al"] if st == "a" else C["ac"] if st == "w" else C["dm2"]
            G.chart_lines(im, (x1 - 62, y1 - 26, x1 - 8, y1 - 8),
                          [(np.linspace(0, 1, len(ys)), ys, spark_col, 1.2)], (lo, hi))

    def spk_stats(self, stt):
        n = self.spk.n
        r_, k_, f_ = (self.smooth(name, stt[name], 0.2, tag=n, rel=rr) for name, rr in
                      (("rho", 0.01), ("K", 0.03), ("fps", 0.15)))
        rho = "%.2f" % r_ if r_ is not None else None
        K = "%.2f" % k_ if k_ is not None else None
        fps = "%.0f" % f_ if f_ is not None else "0"
        return rho, K, fps

    def _detail(self, k, parent):
        self.sel, self.parent = k, parent
        self.go("detail")

    def _speckle(self, mode):
        self.spk_mode = mode if mode != "pseudo" else "pseudo"
        self.go("speckle")

    # --- points ---
    def p_points(self, im, d, snap, now):
        self.card(d, (16, 50, 470, 422))
        self.label(im, (28, 60), "沿程 9 路 · 近 10 分钟")
        lx = 458
        for name, col in reversed([("告警", C["al"]), ("上升", C["ac"]), ("正常", C["grey"])]):
            lx -= self.tx.size(name, "cjk", 10)[0]
            self.tx.draw(im, (lx, 66), name, "cjk", 10, C["dm"], mid=True)
            lx -= 16
            d.rectangle((lx, 66, lx + 11, 67), fill=col)
            lx -= 10
        lines, labels = self._trend_lines(snap)
        vals = [v for _, ys, _, _ in lines for v in ys if v is not None]
        lo, hi = self.rng("points", min(vals) if vals else None, max(vals) if vals else None, now)
        box = (56, 86, 458, 386)
        self.grid(im, d, box, lo, hi)
        G.chart_lines(im, box, lines, (lo, hi))
        ys = [box[3] - (snap["msg"]["T"][k] - lo) / float(hi - lo) * (box[3] - box[1]) for k, _ in labels]
        for (k, col), yy, ly in zip(labels, ys, spread(ys, 13)):
            G.dot(d, box[2], yy, 3, col)
            self.tx.draw(im, (box[2] - 8, ly), "S%d" % IDS[k], "cjk", 10, col, align="r", mid=True)
        self.time_axis(im, box, 398)
        # table
        tx0, tx1 = 482, 784
        self.card(d, (tx0, 50, tx1, 422))
        cols = [tx0 + 10, tx0 + 54, tx0 + 116, tx0 + 178, tx1 - 12]
        for x, s, al in zip(cols, ["测点", "节点", "热电偶", "偏差", "°C/min"], "lllll"[:4] + "r"):
            self.tx.draw(im, (x, 62), s, "cjk", 10, C["dm2"], align=al, mid=True)
        G.hline(d, tx0 + 1, tx1 - 1, 74, C["ln"])
        m, ref = snap["msg"], snap["ref"]
        rh = 38
        for k in range(9):
            y = 75 + k * rh
            st = self.status(snap, k)
            if k == self.sel and self.page == "points":
                d.rectangle((tx0 + 1, y, tx1 - 1, y + rh - 1), fill=C["sel"])
                d.rectangle((tx0 + 1, y, tx0 + 2, y + rh - 1), fill=C["ac"])
            yc = y + rh // 2
            T = m["T"][k] if m else None
            R = ref[k] if ref else None
            r = snap["rates"][k]
            self.tx.draw(im, (cols[0], yc), "S%d" % IDS[k], "cjk", 12, C["tx"], mid=True)
            self.tx.draw(im, (cols[1], yc), self.fq("tab", k, T, 2) or "--", "mono", 12,
                         C["al"] if st == "a" else C["tx"], mid=True)
            R = self.ease_ref(k, R, now)
            self.tx.draw(im, (cols[2], yc), self.fq("ref", k, R, 2) or "—", "mono", 12, C["dm"], mid=True)
            dev = T - R if T is not None and R is not None else None
            self.tx.draw(im, (cols[3], yc), self.fq("dev", k, dev, 2, sign=True) or "—", "mono", 12,
                         C["dm"], mid=True)
            self.tx.draw(im, (cols[4] - 12, yc), self.fq("rate", k, r, 2, sign=True) or "—", "mono", 12,
                         C["al"] if st == "a" else C["ac"] if st == "w" else C["dm"], align="r", mid=True)
            chevron(d, cols[4] - 3, yc, C["dm2"])
            G.hline(d, tx0 + 1, tx1 - 1, y + rh - 1, C["ln"])
            self.hits.append(((tx0, y, tx1, y + rh), lambda k=k: self._detail(k, "points")))

    def ease_ref(self, k, R, now):
        e = self.ranges.get(("refease", k))
        if e is None:
            e = self.ranges[("refease", k)] = F.Eased(tau=0.8)
        return e(R, now) if R is not None else None

    def _trend_lines(self, snap, only=None):
        h = snap["hist"]
        if not h:
            return [], []
        t_end = h[-1][0]
        xs = [1 - (t_end - t) / HIST_S for t, _ in h]
        back, front, labels = [], [], []
        for k in range(9):
            if only is not None and k != only:
                continue
            ys = [T[k] for _, T in h]
            st = self.status(snap, k)
            if st == "a":
                front.append((xs, ys, C["al"], 1.8))
                labels.append((k, C["al"]))
            elif st == "w":
                front.insert(0, (xs, ys, C["ac"], 1.4))
                labels.append((k, C["ac"]))
            else:
                back.append((xs, ys, C["grey"], 1.0))
        return back + front, labels

    def grid(self, im, d, box, lo, hi, step=None):
        step = step or max(1, int(math.ceil((hi - lo) / 5.0)))
        v = int(math.ceil(lo))
        while v <= hi:
            y = int(box[3] - (v - lo) / float(hi - lo) * (box[3] - box[1]))
            G.hline(d, box[0], box[2], y, C["ln"])
            self.tx.draw(im, (box[0] - 8, y), str(v), "mono", 10, C["dm2"], align="r", mid=True)
            v += step

    def time_axis(self, im, box, y):
        for i, s in enumerate(["-10", "-8", "-6", "-4", "-2", "0 min"]):
            x = box[0] + (box[2] - box[0]) * i / 5.0
            self.tx.draw(im, (x, y), s, "mono", 10, C["dm2"], align="r" if i == 5 else "c", mid=True)

    # --- detail ---
    def p_detail(self, im, d, snap, now):
        k = self.sel
        m, ref = snap["msg"], snap["ref"]
        back = "返回测点" if self.parent == "points" else "返回总览"
        d.line([(24, 60), (20, 64), (24, 68)], fill=C["dm"], width=2)
        self.tx.draw(im, (32, 64), back, "cjk", 12, C["dm"], mid=True)
        self.hits.append(((10, 48, 140, 80), lambda: self.go(self.parent)))
        st = self.status(snap, k)
        T = m["T"][k] if m else None
        x0 = 20
        self.label(im, (x0, 92), "S%d · 第 %d 测点" % (IDS[k], k + 1))
        col = C["al"] if st == "a" else C["ac"] if st == "w" else C["tx"]
        w = self.tx.draw(im, (x0 - 2, 110), self.fq("big", k, T, 2) or "--.--", "mono", 46, col)
        self.tx.draw(im, (x0 + w + 2, 134), "°C", "cjk", 16, C["dm"])
        chips = []
        r = snap["rates"][k]
        if st == "a":
            rs = self.fq("chip", k, r or 0.0, 1, sign=True)
            chips.append(("▲ 速率 %s °C/min" % rs if (r or 0) > snap["rate_alarm"] else "▲ 超温", C["al"]))
        elif st == "w":
            chips.append(("↑ 上升 %s °C/min" % self.fq("chip", k, r or 0.0, 1, sign=True), C["ac"]))
        chips.append(("阈值 %.0f °C" % snap["t_alarm"], C["dm"]))
        x = x0
        for s, c in chips:
            w = self.tx.size(s, "cjk", 11)[0] + 18
            G.rrect(d, (x, 172, x + w, 194), 10, C["bg"], (110, 40, 42) if c == C["al"] else C["ln2"])
            self.tx.draw(im, (x + w // 2, 183), s, "cjk", 11, c, align="c", mid=True)
            x += w + 6
        ys = [Tt[k] for _, Tt in snap["hist"] if Tt[k] is not None]
        nb = [IDS[j] for j in (k - 1, k + 1) if 0 <= j < 9]
        nbv = [m["T"][j] if m and m["T"][j] is not None else None for j in (k - 1, k + 1) if 0 <= j < 9]
        R = self.ease_ref(k, ref[k] if ref else None, now)
        rows = [("温升速率", "%s °C/min" % (self.fq("drate", k, r, 2, sign=True) or "—")),
                ("热电偶", "%s °C" % self.fq("dref", k, R, 2) if R is not None else "—"),
                ("10 min 最高 / 最低", "%.2f / %.2f" % (max(ys), min(ys)) if ys else "—"),
                ("相邻 " + " / ".join("S%d" % i for i in nb),
                 " / ".join(self.fq("nb", j, v, 2) if v is not None else "--" for j, v in zip(nb, nbv)))]
        for i, (a_, b_) in enumerate(rows):
            y = 214 + 30 * i
            self.tx.draw(im, (x0, y), a_, "cjk", 11, C["dm"], mid=True)
            self.tx.draw(im, (236, y), b_, "mono", 11, C["tx"], align="r", mid=True)
            G.hline(d, x0, 236, y + 14, C["ln"])
        # chart
        self.card(d, (250, 50, 784, 422))
        self.label(im, (262, 60), "近 10 分钟")
        lines, _ = self._trend_lines(snap, only=k)
        near = ys and max(ys) > snap["t_alarm"] - 4                 # alarm line joins the axis when close
        lo, hi = self.rng(("detail", k), min(ys) if ys else None, max(ys) if ys else None, now,
                          force=snap["t_alarm"] + 1 if near else None)
        box = (290, 84, 770, 386)
        self.grid(im, d, box, lo, hi)
        ya = box[3] - (snap["t_alarm"] - lo) / float(hi - lo) * (box[3] - box[1])
        if box[1] <= ya <= box[3]:
            for x in range(box[0], box[2], 7):
                d.line([(x, ya), (x + 3, ya)], fill=(150, 50, 50))
            self.tx.draw(im, (box[2], ya - 12), "%.0f °C 告警线" % snap["t_alarm"], "cjk", 10, (190, 70, 70), align="r")
        if lines:
            xs, yv = lines[0][0], lines[0][1]
            G.chart_lines(im, box, [(xs, yv, C["ac"] if st != "a" else C["al"], 1.6)], (lo, hi),
                          fill=(xs, yv, (36, 22, 18)))
            if T is not None:
                G.dot(d, box[2], box[3] - (T - lo) / float(hi - lo) * (box[3] - box[1]), 3.5, col)
        self.time_axis(im, box, 398)

    # --- speckle ---
    def p_speckle(self, im, d, snap, now):
        v = self.spk.view(self.spk_mode, (480, 300))
        if v is None:
            d.rectangle((16, 50, 495, 349), fill=C["pn"])
            self.tx.draw(im, (256, 200), "等待相机", "cjk", 12, C["dm2"], align="c", mid=True)
        else:
            im.paste(Image.fromarray(v), (16, 50))
        lab = {"raw": ("暗", "亮"), "pseudo": ("暗", "亮"), "diff": ("不变", "变化大"), "decorr": ("稳定", "去相关")}[self.spk_mode]
        self.tx.draw(im, (16, 368), lab[0], "cjk", 10, C["dm"], mid=True)
        wl = self.tx.size(lab[0], "cjk", 10)[0] + 8
        wr = self.tx.size(lab[1], "cjk", 10)[0] + 8
        for x in range(16 + wl, 496 - wr):
            f = (x - 16 - wl) / float(480 - wl - wr)
            c = tuple(int(f * 255) for _ in range(3)) if self.spk_mode == "raw" else tuple(int(c) for c in G.INFERNO[int(f * 255)])
            d.line([(x, 366), (x, 369)], fill=c)
        self.tx.draw(im, (496, 368), lab[1], "cjk", 10, C["dm"], align="r", mid=True)
        stt = self.spk.stats
        rho, K, fps = self.spk_stats(stt)
        info = [("全局相关 ρ", rho or "—"), ("散斑对比度 K", K or "—"),
                ("参考帧", "%.0f s 前" % stt["ref_s"] if stt["ref_s"] is not None else "—"),
                ("帧率", fps + " fps")]
        for i, (a_, b_) in enumerate(info):
            x = 16 + 120 * i
            self.tx.draw(im, (x, 390), a_, "cjk", 10, C["dm"])
            self.tx.draw(im, (x, 405), b_, "mono", 12, C["tx"])
        y = 50
        for key, name, sub in G.MODES:
            on = key == self.spk_mode
            box = (508, y, 784, y + 60)
            G.rrect(d, box, 4, (27, 23, 18) if on else C["pn"], (120, 84, 36) if on else C["ln"])
            th = self.spk.view(key, (120, 75))
            if th is not None:
                im.paste(Image.fromarray(th[17:57, 20:84]), (516, y + 10))
            else:
                d.rectangle((516, y + 10, 579, y + 49), fill=C["bg"])
            self.tx.draw(im, (590, y + 14), name, "cjk", 12, C["tx"])
            self.tx.draw(im, (590, y + 33), sub, "cjk", 10, C["dm"])
            self.hits.append((box, lambda k=key: setattr(self, "spk_mode", k)))
            y += 66
        self.tx.draw(im, (508, y + 6), "参考帧每 %.0f s 更新；ROI 跟随光斑质心" % self.a.ref_age, "cjk", 10, C["dm2"])

    # --- alarm ---
    def p_alarm(self, im, d, snap, now):
        ev = sorted(snap["events"], key=lambda e: (e["done"], {"a": 0, "w": 1}.get(e["level"], 2), -e["t"]))
        if not ev:
            self.tx.draw(im, (G.W // 2, 236), "暂无告警", "cjk", 12, C["dm2"], align="c", mid=True)
            return
        rh = 54
        for i, e in enumerate(ev[:7]):
            y = 46 + i * rh
            col = {"a": C["al"], "w": C["ac"]}.get(e["level"], C["dm2"]) if not e["done"] else C["dm2"]
            G.rrect(d, (22, y + 13, 25, y + 41), 1, col)
            self.tx.draw(im, (40, y + rh // 2), time.strftime("%H:%M:%S", time.localtime(e["t"])), "mono", 12, C["dm"],
                         mid=True)
            self.tx.draw(im, (124, y + 15), e["text"], "cjk", 12, C["tx"] if not e["done"] else C["dm"])
            self.tx.draw(im, (124, y + 33), e["sub"], "cjk", 10, C["dm"])
            if e["level"] == "c":
                s, c = "—", C["dm2"]
            elif e["done"]:
                s, c = "已恢复", C["dm"]
            elif e["level"] == "a":
                s, c = "进行中", C["al"] if self.blink_on(now) else (120, 40, 42)
            else:
                s, c = "观察", C["ac"]
            self.tx.draw(im, (770, y + rh // 2), s, "cjk", 11, c, align="r", mid=True)
            G.hline(d, 16, 784, y + rh - 1, C["ln"])
            if e["k"] is not None:
                self.hits.append(((16, y, 784, y + rh), lambda k=e["k"]: self._detail(k, "points")))

    # --- system ---
    def p_system(self, im, d, snap, now):
        m = snap["msg"]
        self.card(d, (16, 50, 470, 422))
        self.label(im, (28, 60), "运行")
        up = int(now - self.t_start)
        ui = (m or {}).get("ui", {})
        rows = [("推理耗时", "%.2f ms" % m["ms"]["qcnn"] if m else "—"),
                ("预处理", "%.2f s / 步" % (m["ms"]["pre"] / 1e3) if m else "—"),
                ("步数", "#%d · dt %.2f s" % (m["step"], m["dt"]) if m else "—"),
                ("MQTT", "fsf/%s · sent %d" % (ui.get("node", "fz3b"), m["mqtt"]["sent"]) if m else "—"),
                ("温控参考", "在线" if snap["ref"] is not None else "未连接"),
                ("界面运行", "%02d:%02d:%02d" % (up // 3600, up // 60 % 60, up % 60))]
        for i, (a_, b_) in enumerate(rows):
            y = 96 + 40 * i
            self.tx.draw(im, (28, y), a_, "cjk", 12, C["dm"], mid=True)
            self.tx.draw(im, (458, y), b_, "mono" if b_[:1].isdigit() or b_[:1] in "#f" else "cjk", 12, C["tx"],
                         align="r", mid=True)
            G.hline(d, 28, 458, y + 20, C["ln"])
        self.card(d, (482, 50, 784, 422))
        self.label(im, (494, 60), "操作")
        ops = [("零点标定", "以热电偶读数为参考", self._calibrate, None),
               ("关闭屏幕", "触摸唤醒", self._blank, None),
               ("退出", "", lambda: self._modal("exit"), (255, 138, 128))]
        for i, (a_, b_, fn, c) in enumerate(ops):
            y = 84 + 50 * i
            box = (494, y, 772, y + 42)
            G.rrect(d, box, 4, C["pn"], C["ln2"])
            self.tx.draw(im, (508, y + 21), a_, "cjk", 12, c or C["tx"], mid=True)
            if b_:
                self.tx.draw(im, (760, y + 21), b_, "cjk", 10, C["dm"], align="r", mid=True)
            else:
                chevron(d, 757, y + 21, C["dm"])
            self.hits.append((box, fn))
        if self.flash and now - self.flash[0] < 3:
            self.tx.draw(im, (494, 250), self.flash[1], "cjk", 11, C["ok"])

    def _calibrate(self):
        snap = self.st.snapshot()
        if snap["ref"] is None:
            self.flash = (time.time(), "温控未连接，无法标定")
        elif send_node(self.st, {"cmd": "calibrate", "T": snap["ref"]}):
            self.flash = (time.time(), "已按热电偶读数标定")
        else:
            self.flash = (time.time(), "推理服务未连接")

    def _blank(self):
        self.blank = True

    def _modal(self, kind):
        self.modal = (kind, time.time())

    # --- overlays ---
    def p_modal(self, arr, snap, now):
        kind, t0 = self.modal
        e = ease((now - t0) / 0.22)
        out = G.blend(arr, (arr * 0.25).astype(np.uint8), 0.85 * e)
        im = Image.fromarray(out)
        d = ImageDraw.Draw(im)
        if kind == "exit":
            title, sub = "退出界面", "推理与 MQTT 上报由后台服务负责，退出界面不影响它们。"
            opts = [("返回总览", "继续显示", lambda: (setattr(self, "modal", None), self.go("home")), C["tx"]),
                    ("关闭屏幕", "触摸唤醒", lambda: (setattr(self, "modal", None), self._blank()), C["tx"]),
                    ("退出界面", "回到系统控制台", self._exit, (255, 138, 128)),
                    ("关机", "需再次确认", lambda: self._modal("shutdown"), (255, 138, 128))]
        else:
            title, sub = "确认关机", "推理、上报和采集都会停止，需重新上电启动。"
            opts = [("关机", "立即执行", self._shutdown, (255, 138, 128))]
        w, h = 360, 132 + 46 * len(opts)
        x0 = (G.W - w) // 2
        y0 = (G.H - h) // 2 + int(14 * (1 - e))
        G.rrect(d, (x0, y0, x0 + w, y0 + h), 6, C["pn2"], (42, 48, 57))
        self.tx.draw(im, (x0 + 22, y0 + 20), title, "cjk", 15, C["tx"])
        self.tx.draw(im, (x0 + 22, y0 + 48), sub, "cjk", 11, C["dm"])
        self.hits = [((0, 0, G.W, G.H), lambda: None)]          # modal: swallow taps outside
        y = y0 + 74
        for a_, b_, fn, c in opts:
            box = (x0 + 22, y, x0 + w - 22, y + 38)
            G.rrect(d, box, 4, C["pn2"], C["ln2"])
            self.tx.draw(im, (box[0] + 12, y + 19), a_, "cjk", 12, c, mid=True)
            self.tx.draw(im, (box[2] - 12, y + 19), b_, "cjk", 10, C["dm"], align="r", mid=True)
            self.hits.append((box, fn))
            y += 46
        box = (x0 + 22, y + 4, x0 + w - 22, y + 40)
        G.rrect(d, box, 4, C["tx"])
        self.tx.draw(im, ((box[0] + box[2]) // 2, y + 22), "取消", "cjk", 12, C["bg"], align="c", mid=True)
        self.hits.append((box, lambda: setattr(self, "modal", None)))
        return np.asarray(im)

    def _exit(self):
        self.quit = True

    def _shutdown(self):
        self.modal = None
        self.blank = True
        os.system("sync; shutdown -h now")

    def fiber_strip(self, im, d, snap, now, box):
        """One line: fiber alarm state, score / threshold and a 10 min score line. Tap -> 告警."""
        x0, y0, x1, y1 = box
        fb = snap.get("fiber")
        if fb is None:
            return
        al = bool(fb.get("alarm"))
        G.rrect(d, box, 3, C["alpn"] if al else C["pn"], (110, 40, 42) if al else C["ln"])
        ym = (y0 + y1) // 2
        x = x0 + 10
        x += self.tx.draw(im, (x, ym), "光纤沿线", "cjk", 11, C["dm"], mid=True) + 10
        if not fb.get("ready"):
            self.tx.draw(im, (x, ym), "窗口积累中（约 3 min）", "cjk", 11, C["dm2"], mid=True)
        else:
            s, col = ("温升报警", C["al"] if self.blink_on(now) else (150, 50, 52)) if al else ("无异常", C["ok"])
            G.dot(d, x + 3, ym, 3, col)
            x += 12
            x += self.tx.draw(im, (x, ym), s, "cjk", 12, C["tx"], mid=True) + 18
            x += self.tx.draw(im, (x, ym), "报警得分", "cjk", 11, C["dm"], mid=True) + 6
            x += self.tx.draw(im, (x, ym), "%.2f / %.2f" % (fb["score"], fb["threshold"]), "mono", 11, C["tx"], mid=True) + 14
        h = snap.get("fiber_hist") or []
        sx0, sx1, sy0, sy1 = max(x, x1 - 260), x1 - 10, y0 + 4, y1 - 4
        if len(h) >= 2 and sx1 - sx0 > 40:
            thr = fb["threshold"]
            lo, hi = min(min(v for _, v in h), thr) - 0.5, max(max(v for _, v in h), thr) + 0.5
            Y = lambda v: int(sy1 - (sy1 - sy0) * (v - lo) / (hi - lo))
            yt = Y(thr)
            for xx in range(sx0, sx1, 4):
                d.line([(xx, yt), (xx + 1, yt)], fill=C["dm2"])
            t_end = h[-1][0]
            pts = [(sx1 - (sx1 - sx0) * (t_end - t) / HIST_S, Y(v)) for t, v in h if t_end - t <= HIST_S]
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                d.line([(xa, ya), (xb, yb)], fill=C["al"] if min(ya, yb) < yt else C["ac"])
        self.hits.append((box, lambda: self.go("alarm")))

    def p_toast(self, arr, snap, now):
        t0, k = self.st.toast
        el = now - t0
        if el > 6.0:
            self.st.toast = None
            return arr
        e = ease(el / 0.28) if el < 5.7 else 1 - ease((el - 5.7) / 0.3)
        m = snap["msg"]
        im = Image.fromarray(arr)
        d = ImageDraw.Draw(im)
        if k is None:                                   # fiber alarm
            fb = snap.get("fiber") or {}
            a_ = "▲ 光纤沿线温升报警"
            b_ = "得分 %.2f / 门限 %.2f" % (fb.get("score") or 0.0, fb.get("threshold") or 0.0)
        else:
            r = snap["rates"][k] or 0.0
            T = m["T"][k] or 0.0
            a_ = "▲ S%d %s" % (IDS[k], "速率告警" if r > snap["rate_alarm"] or T <= snap["t_alarm"] else "超温告警")
            b_ = "%+.1f °C/min · %.2f °C" % (r, T)
        w = self.tx.size(a_, "cjk", 12)[0] + self.tx.size(b_, "mono", 12)[0] + self.tx.size("查看", "cjk", 12)[0] + 64
        x0 = (G.W - w) // 2
        y0 = int(-34 + 80 * e)
        G.rrect(d, (x0, y0, x0 + w, y0 + 30), 4, (42, 18, 20), (140, 50, 52))
        x = x0 + 14
        x += self.tx.draw(im, (x, y0 + 15), a_, "cjk", 12, C["al"], mid=True) + 14
        x += self.tx.draw(im, (x, y0 + 15), b_, "mono", 12, C["tx"], mid=True) + 14
        x += self.tx.draw(im, (x, y0 + 15), "查看", "cjk", 12, C["dm"], mid=True) + 6
        chevron(d, x, y0 + 15, C["dm"])

        def view(k=k):
            self.st.toast = None
            if k is None:
                self.go("alarm")
            else:
                self._detail(k, "home")
        self.hits.append(((x0, y0, x0 + w, y0 + 30), view))
        return np.asarray(im)


# ---------------- preview (Mac / no board) ----------------
def simulate(state, seconds, period=2.0, seed=3):
    """Fill state with `seconds` of synthetic node messages (fake clock) for the preview set:
    a coolant gradient, slow drift, one hot spot at S2 with a downstream tail, small noise."""
    rng = np.random.RandomState(seed)
    t0 = time.time() - seconds
    base = 36.4 + 0.15 * np.arange(9)
    drift = np.zeros(9)
    T_prev = None
    sent = 0
    n = int(seconds / period)
    for i in range(n + 1):
        t = t0 + i * period
        x = i / float(n)
        drift = 0.97 * drift + 0.01 * rng.randn(9)
        heat = np.zeros(9)
        if x > 0.88:                                    # preview ends on the rising edge (alarm on screen)
            up = 11.5 * (1 - math.exp(-(x - 0.88) / 0.05))
            heat += up * np.array([0, 0, 0.02, 0.18, 1.0, 0.25, 0.12, 0.05, 0.02])
        T = base + drift + heat + 0.006 * rng.randn(9)
        dT = np.zeros(9) if T_prev is None else T - T_prev
        T_prev = T
        rate = dT / period * 60
        alarm = (T > 48.0) | (np.abs(rate) > 3.0)
        sent += 1
        score = 1.2 + 0.4 * rng.randn() + (9.0 * (1 - math.exp(-(x - 0.88) / 0.03)) if x > 0.88 else 0.0)
        fiber = {"ready": t - t0 > 180, "score": round(score, 3), "threshold": 3.26, "alarm": bool(x > 0.9),
                 "cnn": None, "corr": None, "t_alarm": None}
        m = {"step": i + 1, "t": t, "dt": period, "dT": [float(v) for v in dT], "T": [float(v) for v in T],
             "alarm": [bool(v) for v in alarm], "demo": False, "ms": {"pre": 2017.0, "qcnn": 1.34, "ovl": 0.3},
             "fiber": fiber,
             "mqtt": {"sent": sent, "dropped": 0, "reconnects": 0},
             "ui": {"mqtt": True, "t_alarm": 48.0, "rate_alarm": 3.0, "node": "fz3b"}}
        state.on_msg(m, now=t)
        with state.lock:
            state.ref = [float(v) for v in T + 0.08 * rng.randn(9)]
            state.ref_t = time.time()


def shots(ui, state, out_dir, args):
    os.makedirs(out_dir, exist_ok=True)
    simulate(state, 600)
    frames = np.load(args.raw_npy, mmap_mode="r") if args.raw_npy else None
    if frames is not None:                              # reference 20 s back = 2 frames back in this recording
        ui.spk.ref_age = 1e9
        ui.spk.push(time.time() - 20, np.asarray(frames[0]))
        for i in range(1, 8):
            ui.spk.push(time.time() - 1.2 + i * 0.16, np.asarray(frames[1]))
    state.cam_ok = True
    with state.lock:
        state.ref_t = time.time()
    now = time.time()
    todo = []
    ui.t_start = now - 1.6
    ui.page = "splash"
    todo.append(("01_splash", now))
    seq = [("02_home", "home", None), ("03_points", "points", None), ("04_detail", "detail", None),
           ("05_speckle", "speckle", None), ("06_alarm", "alarm", None), ("07_system", "system", None),
           ("08_exit", "system", "exit"), ("09_shutdown", "system", "shutdown")]

    def save(name, t):
        Image.fromarray(ui.frame(t)).save(os.path.join(out_dir, name + ".png"))
    save("01_splash", now)
    ui.t_start = now - 10
    k_al = [k for k in range(9) if state.msg["alarm"][k]]
    ui.sel = k_al[0] if k_al else 3
    for name, page, modal in seq:
        ui.page, ui.trans, ui.modal = page, None, None
        ui.parent = "points"
        if modal:
            ui.modal = (modal, now - 1)
        state.toast = None
        save(name, now)
    # toast + transition frames
    ui.page, ui.modal = "home", None
    state.toast = (now - 1.0, ui.sel)
    save("10_home_toast", now)
    state.toast = None
    ui.page = "home"
    ui.frame(now)
    ui.go("points", now)
    ui.frame(now)
    save("11_transition_mid", now + 0.09)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47110, help="UDP port the node sends to (--ui-udp)")
    ap.add_argument("--telemetry", help="acquisition page base URL (thermocouples), e.g. http://127.0.0.1:8877")
    ap.add_argument("--out", choices=["drm", "png"], default="drm")
    ap.add_argument("--png", default="/tmp/fsf_ui.png", help="--out png: file rewritten every frame")
    ap.add_argument("--fps", type=float, default=2.0, help="idle redraw rate (animations run at 20, speckle page at 8)")
    ap.add_argument("--cam-fps", type=float, default=4.0)
    ap.add_argument("--ref-age", type=float, default=20.0, help="s: reference frame age for diff / decorrelation")
    ap.add_argument("--raw-npy", help="frames from a recording instead of stream.py (preview)")
    ap.add_argument("--layout", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                                     "board", "fz3b", "ui", "layout.json"))
    ap.add_argument("--broker-label", default="192.168.1.253")
    ap.add_argument("--pipe", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                                                   "board", "fz3b", "ui", "pipe_ps.npz"),
                    help="3D pipe asset from tools/ui/build_ps_pipe.py (missing: flat colour bar)")
    ap.add_argument("--splash-max", type=float, default=8.0)
    ap.add_argument("--touch", default=L.TOUCH_DEV)
    ap.add_argument("--shots", help="write the preview set (10 min of synthetic node data) to this dir and exit")
    a = ap.parse_args()
    a.sensor_x = json.load(open(a.layout))["sensor_x"] if os.path.exists(a.layout) else \
        [122.1, 191.6, 261.1, 330.6, 400.0, 469.4, 539.0, 608.4, 677.9]
    state = State()
    ui = UI(state, a)
    if a.shots:
        shots(ui, state, a.shots, a)
        return
    threading.Thread(target=feed_loop, args=(state, a.port), name="feed", daemon=True).start()
    if a.telemetry:
        threading.Thread(target=telemetry_loop, args=(state, a.telemetry), name="telemetry", daemon=True).start()
    threading.Thread(target=camera_loop, args=(ui, state, a.cam_fps, a.raw_npy), name="camera", daemon=True).start()
    if os.path.exists(a.touch):
        threading.Thread(target=touch_loop, args=(ui, a.touch), name="touch", daemon=True).start()
    out = DrmOut() if a.out == "drm" else PngOut(a.png)
    try:
        while not ui.quit:
            t = time.time()
            out.show(ui.frame(t))
            fps = 20.0 if ui.animating(t) else (8.0 if ui.page == "speckle" else
                                                 6.0 if getattr(ui, "_moving", False) else a.fps)
            time.sleep(max(0.005, 1.0 / fps - (time.time() - t)))
    except KeyboardInterrupt:
        pass
    finally:
        out.close()


if __name__ == "__main__":
    main()
