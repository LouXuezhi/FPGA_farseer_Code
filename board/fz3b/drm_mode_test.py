"""Temporary DRM mode test for the DP output (board, Python 3.6, root, no libdrm).

    python3 drm_mode_test.py [--secs 600] [--mode 800x480]

Takes the DRM device (no X / compositor runs on the board, so the first
opener is master), creates a dumb buffer with a test pattern and sets the
mode with legacy SETCRTC. The mode does not have to be in the EDID (the
CH7218 DP->HDMI adapter only advertises 1920x1080). After --secs it closes
the device; xlnx_drm's lastclose restores the fbdev console mode, so nothing
persists. Pattern: 1-px white frame on all four edges, 8 colour bars, a
patch of 1-px black/white lines (to see whether the panel rescales).
"""
import argparse
import fcntl
import mmap
import os
import struct
import time

MODES = {   # name: (clock kHz, h active/sync start/sync end/total, v ..., flags)
    "800x480":   (33264, 800, 840, 968, 1056, 480, 493, 496, 525, 0xA),        # -hsync -vsync
    "1024x600":  (51206, 1024, 1184, 1320, 1344, 600, 612, 618, 635, 0xA),
    "1280x720":  (74250, 1280, 1390, 1430, 1650, 720, 725, 730, 750, 0x5),
    "1920x1080": (148500, 1920, 2008, 2052, 2200, 1080, 1084, 1089, 1125, 0x5),
}


def IOWR(nr, size):
    return (3 << 30) | (size << 16) | (ord("d") << 8) | nr


GETRESOURCES, GETCONNECTOR, GETENCODER = IOWR(0xA0, 64), IOWR(0xA7, 80), IOWR(0xA6, 20)
SETCRTC, ADDFB, RMFB = IOWR(0xA2, 104), IOWR(0xAE, 28), IOWR(0xAF, 4)
CREATE_DUMB, MAP_DUMB = IOWR(0xB2, 32), IOWR(0xB3, 16)


def resources(fd):
    r = bytearray(64)
    fcntl.ioctl(fd, GETRESOURCES, r)
    n = struct.unpack_from("8I", r, 32)
    arrs = [bytearray(4 * max(c, 1)) for c in n[:4]]
    bufs = [(ctypes_addr(a)) for a in arrs]
    struct.pack_into("4Q", r, 0, *bufs)
    fcntl.ioctl(fd, GETRESOURCES, r)
    out = [list(struct.unpack("%dI" % c, a[:4 * c])) for a, c in zip(arrs, n[:4])]
    return dict(fbs=out[0], crtcs=out[1], connectors=out[2], encoders=out[3])


def ctypes_addr(b):
    import ctypes
    return ctypes.addressof((ctypes.c_char * len(b)).from_buffer(b))


def connector(fd, cid):
    c = bytearray(80)
    struct.pack_into("I", c, 48, cid)
    fcntl.ioctl(fd, GETCONNECTOR, c)
    count_modes, count_props, count_enc, enc_id, conn_id, ctype, ctid, connection = struct.unpack_from("8I", c, 32)
    return dict(id=conn_id, encoder=enc_id, connected=(connection == 1))


def encoder(fd, eid):
    e = bytearray(20)
    struct.pack_into("I", e, 0, eid)
    fcntl.ioctl(fd, GETENCODER, e)
    return struct.unpack("5I", e)        # id, type, crtc_id, possible_crtcs, possible_clones


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=600)
    ap.add_argument("--mode", default="800x480", choices=sorted(MODES))
    a = ap.parse_args()
    clk, hd, hss, hse, ht, vd, vss, vse, vt, flags = MODES[a.mode]
    fd = os.open("/dev/dri/card0", os.O_RDWR)
    res = resources(fd)
    conn = [connector(fd, c) for c in res["connectors"]]
    conn = [c for c in conn if c["connected"]][0]
    crtc = encoder(fd, conn["encoder"])[2] if conn["encoder"] else res["crtcs"][0]
    crtc = crtc or res["crtcs"][0]
    print("connector %d encoder %d crtc %d" % (conn["id"], conn["encoder"], crtc))

    d = bytearray(32)
    struct.pack_into("4I", d, 0, vd, hd, 16, 0)                 # RGB565: the primary plane rejects XR24
    fcntl.ioctl(fd, CREATE_DUMB, d)
    handle, pitch, size = struct.unpack_from("IIQ", d, 16)
    f = bytearray(28)
    struct.pack_into("7I", f, 0, 0, hd, vd, pitch, 16, 16, handle)
    fcntl.ioctl(fd, ADDFB, f)
    fb_id = struct.unpack_from("I", f, 0)[0]
    mp = bytearray(16)
    struct.pack_into("I", mp, 0, handle)
    fcntl.ioctl(fd, MAP_DUMB, mp)
    offset = struct.unpack_from("Q", mp, 8)[0]
    m = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=offset)

    bars = [0xFFFFFF, 0xFFFF00, 0x00FFFF, 0x00FF00, 0xFF00FF, 0xFF0000, 0x0000FF, 0x000000]
    px = lambda c: struct.pack("<H", ((c >> 19) << 11) | (((c >> 10) & 63) << 5) | ((c >> 3) & 31))  # noqa: E731
    W = px(0xFFFFFF)
    for y in range(vd):
        if y == 0 or y == vd - 1:
            line = W * hd
        elif vd // 3 <= y < 2 * vd // 3:
            line = b"".join(px(0xFFFFFF if (x & 1) else 0) for x in range(hd // 2)) + \
                   b"".join(px(0xFFFFFF if (y & 1) else 0) for x in range(hd // 2, hd))
        else:
            line = b"".join(px(bars[x * 8 // hd]) for x in range(hd))
        line = W + line[2:-2] + W                               # left / right 1-px frame
        m[y * pitch:y * pitch + hd * 2] = line

    conns = bytearray(struct.pack("I", conn["id"]))
    mode = struct.pack("I5H5HIII32s", clk, hd, hss, hse, ht, 0, vd, vss, vse, vt, 0, 60, flags, 0,
                       a.mode.encode())
    s = bytearray(104)
    struct.pack_into("QIIIIIII", s, 0, ctypes_addr(conns), 1, crtc, fb_id, 0, 0, 0, 1)
    s[36:104] = mode
    fcntl.ioctl(fd, SETCRTC, s)
    print("mode %s set (%d kHz); holding %.0f s, then the console mode comes back" % (a.mode, clk, a.secs),
          flush=True)
    time.sleep(a.secs)
    m.close()
    os.close(fd)
    print("released", flush=True)


if __name__ == "__main__":
    main()
