#!/usr/bin/env python3
"""OV9281 capture tool for FZ3B with the fz3b_cam bitstream.

PL map:  0xA0000000 cam_regs (ctrl + diagnostics), 0xA0010000 AXI VDMA (S2MM only)
DMA buffers live in DDR 0x7000_0000.. (inside the no-map reserved-memory region
buffer@0 = 0x4000_0000..0x7FFF_FFFF of the stock EdgeBoard device tree).
"""
import ctypes, fcntl, mmap, os, struct, sys, time, argparse

# ---------------------------------------------------------------- /dev/mem
class Mem:
    def __init__(self, base, size):
        self.fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self.fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
        self.base, self.size = base, size
        # 32-bit aligned volatile access through ctypes (mmap slicing may use byte copies)
        self.arr = (ctypes.c_uint32 * (size // 4)).from_buffer(self.m)
    def rd(self, off): return self.arr[off >> 2]
    def wr(self, off, v): self.arr[off >> 2] = v & 0xffffffff

# ---------------------------------------------------------------- I2C (SCCB)
I2C_RDWR = 0x0707
class _Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16), ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_uint8))]
class _Data(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_Msg)), ("nmsgs", ctypes.c_uint32)]
class Sensor:
    ADDR = 0x60
    def __init__(self, bus=0):
        self.fd = os.open("/dev/i2c-%d" % bus, os.O_RDWR)
    def rd(self, reg, n=1):
        w = (ctypes.c_uint8 * 2)(reg >> 8, reg & 0xff); r = (ctypes.c_uint8 * n)()
        m = (_Msg * 2)(_Msg(self.ADDR, 0, 2, w), _Msg(self.ADDR, 1, n, r))
        fcntl.ioctl(self.fd, I2C_RDWR, _Data(m, 2)); return list(r)
    def wr(self, reg, val):
        w = (ctypes.c_uint8 * 3)(reg >> 8, reg & 0xff, val & 0xff)
        m = (_Msg * 1)(_Msg(self.ADDR, 0, 3, w))
        fcntl.ioctl(self.fd, I2C_RDWR, _Data(m, 1))
    def rd16(self, reg):
        a, b = self.rd(reg, 2); return (a << 8) | b

# Register table: Linux mainline ov9282.c common_regs + mode_1280x800_regs (RAW10, 2 lanes,
# 800 Mb/s/lane with 24 MHz XVCLK). Software reset first, as ov9282 power_on does implicitly.
COMMON = [
 (0x0302,0x32),(0x030e,0x02),(0x3001,0x00),(0x3004,0x00),(0x3005,0x00),(0x3006,0x04),
 (0x3011,0x0a),(0x3013,0x18),(0x301c,0xf0),(0x3022,0x01),(0x3030,0x10),(0x3039,0x32),(0x303a,0x00),
 (0x3503,0x08),(0x3505,0x8c),(0x3507,0x03),(0x3508,0x00),(0x3610,0x80),(0x3611,0xa0),(0x3620,0x6e),
 (0x3632,0x56),(0x3633,0x78),(0x3666,0x00),(0x366f,0x5a),(0x3680,0x84),(0x3712,0x80),(0x372d,0x22),
 (0x3731,0x80),(0x3732,0x30),(0x377d,0x22),(0x3788,0x02),(0x3789,0xa4),(0x378a,0x00),(0x378b,0x4a),
 (0x3799,0x20),(0x3881,0x42),(0x38a8,0x02),(0x38a9,0x80),(0x38b1,0x00),(0x38c4,0x00),(0x38c5,0xc0),
 (0x38c6,0x04),(0x38c7,0x80),(0x3920,0xff),(0x4010,0x40),(0x4043,0x40),(0x4307,0x30),(0x4317,0x00),
 (0x4501,0x00),(0x450a,0x08),(0x4601,0x04),(0x470f,0x00),(0x4f07,0x00),(0x5000,0x9f),(0x5001,0x00),
 (0x5e00,0x00),(0x5d00,0x07),(0x5d01,0x00),(0x0101,0x01),(0x1000,0x03),(0x5a08,0x84),
]
MODE_1280x800 = [
 (0x3778,0x00),(0x3800,0x00),(0x3801,0x00),(0x3802,0x00),(0x3803,0x00),(0x3804,0x05),(0x3805,0x0f),
 (0x3806,0x03),(0x3807,0x2f),(0x3808,0x05),(0x3809,0x00),(0x380a,0x03),(0x380b,0x20),(0x3810,0x00),
 (0x3811,0x08),(0x3812,0x00),(0x3813,0x08),(0x3814,0x11),(0x3815,0x11),(0x3820,0x40),(0x3821,0x00),
 (0x4003,0x40),(0x4008,0x04),(0x4009,0x0b),(0x400c,0x00),(0x400d,0x07),(0x4507,0x00),(0x4509,0x00),
]
MODE_640x400 = [   # 2x2 binning/skip from mainline mode_640x400_regs
 (0x3778,0x10),(0x3800,0x00),(0x3801,0x00),(0x3802,0x00),(0x3803,0x00),(0x3804,0x05),(0x3805,0x0f),
 (0x3806,0x03),(0x3807,0x2f),(0x3808,0x02),(0x3809,0x80),(0x380a,0x01),(0x380b,0x90),(0x3810,0x00),
 (0x3811,0x04),(0x3812,0x00),(0x3813,0x04),(0x3814,0x31),(0x3815,0x22),(0x3820,0x60),(0x3821,0x01),
 (0x4008,0x02),(0x4009,0x05),(0x400c,0x00),(0x400d,0x03),(0x4507,0x03),(0x4509,0x80),
]
MODES = {"1280x800": (1280, 800, MODE_1280x800), "640x400": (640, 400, MODE_640x400)}

# ---------------------------------------------------------------- PL
REGS, VDMA = 0xA0000000, 0xA0010000
CTRL_DPHY_RST, CTRL_RST_N, CTRL_PWDN_N, CTRL_MCLK, CTRL_ECC, CTRL_STREAM, CTRL_SWAP = 1, 2, 4, 8, 16, 32, 64
STAT_NAMES = ["fs", "fe", "lines", "pkts", "ecc_err", "bad_hdr", "fifo_ovf", "sot_err", "bursts0", "bursts1",
              "byteclk_cnt", "lines_per_frame", "last_dt_wc", "sniff_l0", "sniff_l1", "phy_flags"]
BUF_PHYS = 0x70000000
BUF_SPAN = 0x00400000    # 4 MB per frame slot
NFRM = 4

class PL:
    def __init__(self):
        self.r = Mem(REGS, 0x1000)
        self.v = Mem(VDMA, 0x1000)
    def ctrl(self, val=None):
        if val is not None: self.r.wr(0, val)
        return self.r.rd(0)
    def stats(self):
        d = {n: self.r.rd(0x40 + 4 * i) for i, n in enumerate(STAT_NAMES)}
        d["aclk_cnt"] = self.r.rd(0x80); d["mmcm_lock"] = (self.r.rd(0x84) >> 1) & 1
        return d
    def vdma_start(self, width_bytes, height):
        v = self.v
        v.wr(0x30, 0x4)                    # S2MM soft reset
        t = time.time()
        while v.rd(0x30) & 0x4:
            if time.time() - t > 0.5: raise RuntimeError("VDMA reset stuck")
        for i in range(NFRM): v.wr(0xAC + 4 * i, BUF_PHYS + i * BUF_SPAN)
        v.wr(0x48, NFRM)                   # frame store count (needs c_num_fstores >= NFRM)
        v.wr(0x30, 0x0003)                 # RS=1, circular mode
        v.wr(0xA8, width_bytes)            # stride
        v.wr(0xA4, width_bytes)            # hsize
        v.wr(0xA0, height)                 # vsize: writing starts the channel
    def vdma_status(self):
        return self.v.rd(0x34), self.v.rd(0x30), self.v.rd(0x28)
    def vdma_stop(self):
        self.v.wr(0x30, 0)

def unpack_raw10(buf, w, h):
    import numpy as np
    a = np.frombuffer(buf, dtype=np.uint8, count=w * h * 10 // 8).reshape(h, w // 4, 5).astype(np.uint16)
    out = np.empty((h, w // 4, 4), np.uint16)
    for k in range(4):
        out[:, :, k] = (a[:, :, k] << 2) | ((a[:, :, 4] >> (2 * k)) & 3)
    return out.reshape(h, w)

# ---------------------------------------------------------------- commands
def sensor_init(s, mode, raw8=False, test=None, hts=None, vts=None, exposure=None, gain=None):
    s.wr(0x0103, 0x01); time.sleep(0.01)          # software reset
    s.wr(0x4800, 0x00)                            # continuous clock (as mainline power_on)
    for r, v in COMMON: s.wr(r, v)
    s.wr(0x030d, 0x60 if raw8 else 0x50)
    s.wr(0x3662, 0x07 if raw8 else 0x05)
    w, h, tab = MODES[mode]
    for r, v in tab: s.wr(r, v)
    if hts is not None: s.wr(0x380c, (hts >> 1) >> 8); s.wr(0x380d, (hts >> 1) & 0xff)   # register holds HTS/2 (see mainline)
    if vts is not None: s.wr(0x380e, vts >> 8); s.wr(0x380f, vts & 0xff)
    if exposure is not None:
        e = exposure << 4; s.wr(0x3500, (e >> 16) & 0x0f); s.wr(0x3501, (e >> 8) & 0xff); s.wr(0x3502, e & 0xff)
    if gain is not None: s.wr(0x3509, gain)
    s.wr(0x5e00, 0x00 if test is None else (0x80 | (test & 0x0f)))
    return w, h

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd")
    ap.add_argument("--mode", default="1280x800")
    ap.add_argument("--raw8", action="store_true")
    ap.add_argument("--test", type=int, default=None)
    ap.add_argument("--hts", type=int); ap.add_argument("--vts", type=int)
    ap.add_argument("--exposure", type=int); ap.add_argument("--gain", type=int)
    ap.add_argument("--secs", type=float, default=2.0)
    ap.add_argument("--out", default="/root/cam/frame")
    ap.add_argument("--nframes", type=int, default=1)
    ap.add_argument("--ctrl", type=lambda x: int(x, 0))
    a = ap.parse_args()

    if a.cmd == "stat":
        pl = PL(); st = pl.stats()
        print("ctrl=0x%x" % pl.ctrl()); [print("%-16s %d (0x%x)" % (k, v, v)) for k, v in st.items()]
        print("vdma sr=0x%08x cr=0x%08x park=0x%08x" % pl.vdma_status()); return
    if a.cmd == "ctrl":
        pl = PL(); print("ctrl=0x%x" % pl.ctrl(a.ctrl)); return
    s = Sensor()
    if a.cmd == "id":
        print("chip id 0x%04x rev 0x%02x" % (s.rd16(0x300a), s.rd(0x300c)[0])); return
    if a.cmd == "stop":
        s.wr(0x0100, 0x00); PL().vdma_stop(); print("stopped"); return
    if a.cmd in ("start", "grab", "rate"):
        pl = PL()
        w, h = sensor_init(s, a.mode, a.raw8, a.test, a.hts, a.vts, a.exposure, a.gain)
        bpp = 8 if a.raw8 else 10
        line_bytes = w * bpp // 8
        pl.vdma_start(line_bytes, h)
        s.wr(0x0100, 0x01)                         # stream on
        time.sleep(0.3)
        st0 = pl.stats(); t0 = time.time()
        time.sleep(a.secs)
        st1 = pl.stats(); t1 = time.time()
        dt = t1 - t0
        fps = (st1["fe"] - st0["fe"]) / dt
        bclk = (st1["byteclk_cnt"] - st0["byteclk_cnt"]) / dt
        dts = st1["last_dt_wc"]
        print("mode %s %s: FS/s %.2f  FE/s %.2f  lines/s %.0f  lines/frame %d  last DT 0x%02x WC %d" % (
            a.mode, "RAW8" if a.raw8 else "RAW10", (st1["fs"] - st0["fs"]) / dt, fps,
            (st1["lines"] - st0["lines"]) / dt, st1["lines_per_frame"], (dts >> 16) & 0x3f, dts & 0xffff))
        print("errors in window: ecc %d bad %d ovf %d sot %d ; byteclk %.2f MHz ; payload %.1f MB/s" % (
            st1["ecc_err"] - st0["ecc_err"], st1["bad_hdr"] - st0["bad_hdr"], st1["fifo_ovf"] - st0["fifo_ovf"],
            st1["sot_err"] - st0["sot_err"], bclk / 1e6, (st1["lines"] - st0["lines"]) / dt * (dts & 0xffff) / 1e6))
        sr, cr, park = pl.vdma_status()
        print("vdma sr=0x%08x cr=0x%08x park=0x%08x (wr frame %d)" % (sr, cr, park, (park >> 24) & 0x1f))
        if a.cmd == "grab":
            mem = Mem(BUF_PHYS, BUF_SPAN * NFRM)
            for n in range(a.nframes):
                park = pl.vdma_status()[2]; cur = (park >> 24) & 0x1f
                slot = (cur - 1) % NFRM
                raw = bytes(mem.m[slot * BUF_SPAN: slot * BUF_SPAN + line_bytes * h])
                fn = "%s_%d.raw" % (a.out, n)
                open(fn, "wb").write(raw)
                try:
                    import numpy as np
                    from PIL import Image
                    img = (np.frombuffer(raw, np.uint8).reshape(h, w) if a.raw8 else (unpack_raw10(raw, w, h) >> 2).astype(np.uint8))
                    Image.fromarray(img).save("%s_%d.png" % (a.out, n))
                    print("saved %s(.png) slot %d mean %.1f min %d max %d" % (fn, slot, img.mean(), img.min(), img.max()))
                except Exception as e:
                    print("saved raw", fn, "png failed:", e)
                time.sleep(0.05)
        if a.cmd != "start":
            s.wr(0x0100, 0x00)
        return
    raise SystemExit("unknown cmd")

if __name__ == "__main__":
    main()
