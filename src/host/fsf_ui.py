"""Board driver of the screen (src/rtl/video, disp_regs.v), Python 3.6, no numpy needed.

    scr = Screen.open(0xA0030000, layout_json="ui/layout.json")
    scr.load_background("ui/bg.bin", phys=0x72000000)      # once, into reserved DDR
    scr.show(temps, valid, alarms, dT, info)               # every inference step

The PL draws the screen from the static background (DDR), the water colour
profile (256 entries) and a 12x24 text grid; this driver only writes the
profile and the text cells that changed, so one update is a few hundred
register writes (well under a millisecond), and never waits for the display.
"""
import json
import mmap
import os

import ui_layout as L

ID, CTRL, COMMIT, FRAMES = 0x0000, 0x0004, 0x0008, 0x000C
BGADDR, BGPITCH, BGSIZE, PAL0, PROF0, TEXT0 = 0x0020, 0x0024, 0x0038, 0x0080, 0x0400, 0x4000
DISP_ID = 0x4F564C33                                         # "OVL3"
UI_EN, TEST_BARS, BLINK, CAM_EN = 0x1, 0x2, 0x4, 0x8


class FakeRegs:
    """Register file stand-in (tests, preview): keeps every write."""

    def __init__(self):
        self.mem = {ID: DISP_ID}
        self.writes = 0

    def read(self, off):
        return 0 if off == COMMIT else self.mem.get(off, 0)

    def write(self, off, val):
        self.mem[off] = val & 0xFFFFFFFF
        self.writes += 1


class MmioRegs:
    def __init__(self, base, size=0x8000):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(fd, size, offset=base)
        os.close(fd)

    def read(self, off):
        return int.from_bytes(self.m[off:off + 4], "little")

    def write(self, off, val):
        self.m[off:off + 4] = int(val & 0xFFFFFFFF).to_bytes(4, "little")


def temp_index(t):
    x = (t - L.T_LO) / (L.T_HI - L.T_LO)
    return max(0, min(255, int(round(x * 255))))


def profile(temps, valid, sensor_x):
    """Colormap index for screen x = 4i+2 (i = 0..255): linear between the valid
    sensors along the pipe, held flat past the first / last one."""
    pts = sorted((x, t) for x, t, v in zip(sensor_x, temps, valid) if v)
    out = []
    for i in range(256):
        x = 4 * i + 2
        if not pts:
            t = L.T_LO
        elif x <= pts[0][0]:
            t = pts[0][1]
        elif x >= pts[-1][0]:
            t = pts[-1][1]
        else:
            for (x0, t0), (x1, t1) in zip(pts[:-1], pts[1:]):
                if x <= x1:
                    t = t0 + (t1 - t0) * (x - x0) / float(x1 - x0)
                    break
        out.append(temp_index(t))
    return out


class Screen:
    def __init__(self, regs, layout):
        self.regs, self.lay = regs, layout
        got = regs.read(ID)
        if got != DISP_ID:
            raise RuntimeError("display ID 0x%08x, expected 0x%08x (wrong base or bitstream)" % (got, DISP_ID))
        self.cells = {}                                      # text RAM shadow: addr -> word
        self.ctrl = UI_EN | BLINK
        for k, c in enumerate(L.PALETTE):
            regs.write(PAL0 + 4 * k, c)

    @classmethod
    def open(cls, base, layout_json):
        return cls(MmioRegs(base), json.load(open(layout_json)))

    def load_background(self, path, phys):
        """Copy bg.bin (RGBW8888, W x H) to reserved DDR at phys (uncached /dev/mem)."""
        data = open(path, "rb").read()
        assert len(data) == L.W * L.H * 4, "bg.bin is %d bytes" % len(data)
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        m = mmap.mmap(fd, len(data), offset=phys)
        m[:] = data
        m.close()
        os.close(fd)
        self.set_background(phys)

    def set_background(self, phys):
        self.regs.write(BGADDR, phys)
        self.regs.write(BGPITCH, L.W * 4)
        self.regs.write(BGSIZE, (L.H << 16) | L.W)           # staged: applied with the next commit

    def commit(self, ctrl=None):
        """CTRL / palette / timing are staged: applied at the next vblank."""
        if ctrl is not None:
            self.ctrl = ctrl
        cam = self.regs.read(CTRL) & CAM_EN                  # camera on/off belongs to dp_live.py
        self.regs.write(CTRL, self.ctrl | cam)
        self.regs.write(COMMIT, 1)

    # ---------------- text ----------------
    def put(self, field, s, colour, blink=False):
        col, row, width = field
        s = (s + " " * width)[:width]
        for i, ch in enumerate(s):
            code = L.glyph_code(ch) if ch != " " else 0
            word = code | (colour << 8) | ((1 << 11) if blink and code else 0)
            a = TEXT0 + 4 * (row * L.TEXT_PITCH + col + i)
            if self.cells.get(a) != word:
                self.regs.write(a, word)
                self.cells[a] = word

    def show(self, temps, valid, alarms, dT, info):
        """temps/valid/alarms/dT: 9 each, dT order. info: dict with optional keys
        step, dt, qcnn_ms, pre_ms, mqtt (bool), demo (bool), clock (str)."""
        sx = self.lay["sensor_x"]
        for i, v in enumerate(profile(temps, valid, sx)):
            self.regs.write(PROF0 + 4 * i, v)
        cols = self.lay["pipe_temp_cols"]
        for k in range(9):
            f = L.sensor_field(k)
            colour = L.ALARM if alarms[k] else L.WHITE
            if valid[k]:
                self.put(f["temp"], "%6.2f°C" % temps[k], colour)
                self.put(f["dT"], "Δ%+.3f" % dT[k], L.ALARM if alarms[k] else L.DIM)
                self.put((cols[k], L.PIPE_TEMP_ROW, 4), "%4.1f" % temps[k], colour)
            else:
                self.put(f["temp"], " --.--°C", L.DIM)
                self.put(f["dT"], "Δ  ---", L.DIM)
                self.put((cols[k], L.PIPE_TEMP_ROW, 4), " -- ", L.DIM)
            if alarms[k]:
                self.put(f["alarm"], "▲", L.ALARM, blink=True)
            else:
                self.put(f["alarm"], "●", L.OK if valid[k] else L.DIM)
        self.put(L.FIELDS["badge"], "DEMO WEIGHTS" if info.get("demo") else "", L.WARN)
        self.put(L.FIELDS["status"], "● MQTT", L.OK if info.get("mqtt") else L.ALARM)
        self.put(L.FIELDS["clock"], info.get("clock", ""), L.WHITE)
        self.put(L.FIELDS["mqtt"], "%s  sent %d  q%d" % (info.get("topic", ""), info.get("sent", 0),
                                                         info.get("queued", 0)), L.DIM)
        self.put(L.FIELDS["info"], "#%-5d dt %.2fs  QCNN %.2fms  pre %.0fms" % (
            info.get("step", 0), info.get("dt", 0.0), info.get("qcnn_ms", 0.0), info.get("pre_ms", 0.0)), L.DIM)

    def text_ram(self):
        """Shadow of the text RAM as a 4096-entry list (preview / simulation)."""
        t = [0] * 4096
        for a, w in self.cells.items():
            t[(a - TEXT0) // 4] = w
        return t

    def disable(self):
        self.commit(self.ctrl & ~UI_EN)
