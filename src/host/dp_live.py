"""Switch the ZynqMP DisplayPort video layer to the PL live input (Python 3.6, root).

Why not the kernel: the board runs linux-xlnx 4.14 (xilinx-v2018.3). Live input
there is only an in-kernel xlnx_bridge (no user-space switch), the device tree
has no dp_live_video_in_clk, and live *graphics* is broken (VID2 MEM|LIVE =
NONE). Linux fbdev/DRM owns the graphics layer and rewrites it on every commit,
but it never touches the video layer, so this script takes that one:

  1. read the mode Linux trained (DP main stream attributes) -> overlay HTIM/VTIM
  2. AV_BUF: video layer = live, RGB 8 bpc; clock = PL, timing = external
     (the PL timing then drives the DP stream; vclk is the same VPLL clock)
  3. blender: video layer RGB + identity CSC; graphics layer hidden (global alpha 0)
  restore() puts the PS clock / internal timing / memory video back.

    python3 dp_live.py --address-map address_map.txt on     # needs a monitor + mode set
    python3 dp_live.py off
Register offsets: drivers/gpu/drm/xlnx/zynqmp_disp.c, zynqmp_dp.c (xilinx-v2018.3).
"""
import argparse
import mmap
import os
import sys

DP, BLEND, AV_BUF = 0xFD4A0000, 0xFD4AA000, 0xFD4AB000
# DP main stream attributes (zynqmp_dp.c)
DP_ENABLE_MAIN_STREAM, DP_HTOTAL, DP_VTOTAL, DP_POLARITY = 0x84, 0x180, 0x184, 0x188
DP_HSWIDTH, DP_VSWIDTH, DP_HRES, DP_VRES, DP_HSTART, DP_VSTART = 0x18C, 0x190, 0x194, 0x198, 0x19C, 0x1A0
# blender (zynqmp_disp.c)
BL_GLOBAL_ALPHA, BL_LAYER_CTRL_VID, BL_IN1CSC_COEFF0, BL_IN1CSC_OFFSET0 = 0x0C, 0x18, 0x44, 0x68
BL_RGB = 0x2
# AV buffer manager
AV_OUTPUT, AV_CLK_SRC = 0x70, 0x120
AV_LIVE_VID_SF0, AV_LIVE_VID_CONFIG = 0x218, 0x224
VID1_MASK, VID1_LIVE, VID1_MEM, VID1_NONE = 0x3, 0x0, 0x1, 0x3
CLK_VID_FROM_PS, CLK_VID_INTERNAL_TIMING = 0x1, 0x4
SF_8BIT, LIVE_BPC_8, LIVE_FMT_RGB = 0x10101, 0x1, 0x0 << 4
# overlay (src/rtl/video/overlay_regs.v)
# screen (src/rtl/video/disp_regs.v)
OVL_ID, OVL_CTRL, OVL_COMMIT, OVL_HTIM0 = 0x00, 0x04, 0x08, 0x10
OVL_CAMPOS, OVL_CAMSIZE, OVL_CAMPITCH, OVL_CAMNFS, OVL_CAMADDR0 = 0x28, 0x2C, 0x30, 0x34, 0x40
OVL_MAGIC, OVL_CAM_EN = 0x4F564C33, 0x8
CAM_W = 384                              # preview width in the PL (video_overlay_top CAM_W)
MAX_PIXELS = 1280 * 720                  # vclk is timed for 74.25 MHz (video_overlay_cdc.xdc)
# AXI VDMA S2MM (PG020), programmed by board/fz3b/cam_service/cam.py
VDMA_FRMSTORE, VDMA_STRIDE, VDMA_START0 = 0x48, 0xA8, 0xAC


class Mmio:
    def __init__(self, base, size=0x1000):
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(fd, size, offset=base)
        os.close(fd)

    def read(self, off):
        return int.from_bytes(self.m[off:off + 4], "little")

    def write(self, off, val):
        self.m[off:off + 4] = int(val & 0xFFFFFFFF).to_bytes(4, "little")


def read_mode(dp):
    """DP main stream attributes -> dict, or None if no stream (no monitor / mode)."""
    if not dp.read(DP_ENABLE_MAIN_STREAM) & 1:
        return None
    m = {k: dp.read(o) for k, o in (("htotal", DP_HTOTAL), ("vtotal", DP_VTOTAL), ("hsw", DP_HSWIDTH),
                                    ("vsw", DP_VSWIDTH), ("hres", DP_HRES), ("vres", DP_VRES),
                                    ("hstart", DP_HSTART), ("vstart", DP_VSTART), ("pol", DP_POLARITY))}
    if not (m["hres"] and m["vres"] and m["htotal"] > m["hres"] and m["vtotal"] > m["vres"]):
        return None
    return m


def mode_to_overlay(m):
    """DRM mode (as the DP core stores it) -> overlay HTIM0/HTIM1/VTIM0/VTIM1 words.
    HSTART = htotal - hsync_start, so hsync_start = htotal - HSTART."""
    hs0, vs0 = m["htotal"] - m["hstart"], m["vtotal"] - m["vstart"]
    # The DPSUB live input needs active-high hsync/vsync whatever the DP mode's
    # polarity is (the DP core re-applies the mode's polarity on the link).
    # Tested 2026-11-02 on the 800x480 panel: mode polarity (negative) -> black
    # screen, forced active-high -> picture.
    hpos = vpos = 1
    return ((m["htotal"] << 16) | m["hres"],
            (hpos << 31) | ((hs0 + m["hsw"]) << 16) | hs0,
            (m["vtotal"] << 16) | m["vres"],
            (vpos << 31) | ((vs0 + m["vsw"]) << 16) | vs0)


def overlay_set_mode(ovl, words):
    for k, w in enumerate(words):
        ovl.write(OVL_HTIM0 + 4 * k, w)
    ovl.write(OVL_COMMIT, 1)


def camera_setup(vdma, ovl, x0=16, y0=204, rows=240, crop_x=128, crop_y=80):
    """Point the preview at the VDMA frame stores the camera service set up.
    Displayed row r = raw row 2 * (crop_y + r) (640x800 RAW8 -> 640x400 square
    pixels), columns crop_x .. crop_x + 383: a 384 x rows crop, centred for 640x400.
    The PL fetches 128-byte bursts, so every row start must be 128-byte aligned."""
    # FRMSTORE (0x48) reads 0 on this VDMA build (PG020: read back only when the
    # frame-store register read option is on), so count the programmed start
    # addresses instead: cam.py writes NFRM = 4 stores 4 MB apart.
    stride = vdma.read(VDMA_STRIDE) & 0xFFFF
    starts = [vdma.read(VDMA_START0 + 4 * k) for k in range(4)]
    nfs = vdma.read(VDMA_FRMSTORE) & 0x1F or sum(1 for a in starts if a)
    off = crop_y * 2 * stride + crop_x
    addrs = [vdma.read(VDMA_START0 + 4 * k) + off for k in range(min(nfs, 4))]
    if nfs < 3 or nfs > 4 or not stride or any(a % 128 for a in addrs) or (2 * stride) % 128:
        raise RuntimeError("VDMA not set up as expected (nfs %d stride %d): is the camera service running?"
                           % (nfs, stride))
    for k, a in enumerate(addrs):
        ovl.write(OVL_CAMADDR0 + 4 * k, a)
    ovl.write(OVL_CAMPITCH, 2 * stride)
    ovl.write(OVL_CAMNFS, nfs)
    ovl.write(OVL_CAMPOS, (y0 << 16) | x0)
    ovl.write(OVL_CAMSIZE, rows << 16)
    ovl.write(OVL_CTRL, ovl.read(OVL_CTRL) | OVL_CAM_EN)
    return nfs, stride


def live_on(dp, blend, av, ovl):
    m = read_mode(dp)
    if m is None:
        raise RuntimeError("DP main stream is off: connect a monitor and let Linux set a mode first")
    if m["hres"] * m["vres"] > MAX_PIXELS:
        raise RuntimeError("mode %dx%d is above 1280x720, the PL video clock is not timed for it"
                           % (m["hres"], m["vres"]))
    if ovl.read(OVL_ID) != OVL_MAGIC:
        raise RuntimeError("overlay ID mismatch: wrong bitstream or base address")
    overlay_set_mode(ovl, mode_to_overlay(m))            # PL timing = the trained mode
    for i in range(3):
        av.write(AV_LIVE_VID_SF0 + 4 * i, SF_8BIT)
    av.write(AV_LIVE_VID_CONFIG, LIVE_BPC_8 | LIVE_FMT_RGB)
    for i, c in enumerate((0x1000, 0, 0, 0, 0x1000, 0, 0, 0, 0x1000)):   # identity, RGB in
        blend.write(BL_IN1CSC_COEFF0 + 4 * i, c)
    for i in range(3):
        blend.write(BL_IN1CSC_OFFSET0 + 4 * i, 0)
    blend.write(BL_LAYER_CTRL_VID, BL_RGB)
    blend.write(BL_GLOBAL_ALPHA, (0 << 1) | 1)           # graphics layer weight 0: video only
    av.write(AV_OUTPUT, (av.read(AV_OUTPUT) & ~VID1_MASK) | VID1_LIVE)
    av.write(AV_CLK_SRC, av.read(AV_CLK_SRC) & ~(CLK_VID_FROM_PS | CLK_VID_INTERNAL_TIMING))
    return m


def live_off(blend, av):
    av.write(AV_CLK_SRC, av.read(AV_CLK_SRC) | CLK_VID_FROM_PS | CLK_VID_INTERNAL_TIMING)
    av.write(AV_OUTPUT, (av.read(AV_OUTPUT) & ~VID1_MASK) | VID1_NONE)
    blend.write(BL_LAYER_CTRL_VID, 0)
    blend.write(BL_GLOBAL_ALPHA, (0xFF << 1) | 1)        # graphics layer back on top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["on", "off", "show"])
    ap.add_argument("--address-map", help="address_map.txt (overlay base)")
    ap.add_argument("--ovl-base", type=lambda v: int(v, 0), default=0xA0030000)
    ap.add_argument("--vdma-base", type=lambda v: int(v, 0), default=0xA0010000)
    ap.add_argument("--no-camera", action="store_true", help="tiles only, no preview")
    a = ap.parse_args()
    if a.address_map:
        from speckle_node import read_address_map
        bases = read_address_map(a.address_map)
        a.ovl_base, a.vdma_base = bases.get("ovl", a.ovl_base), bases.get("vdma", a.vdma_base)
    dp, blend, av = Mmio(DP), Mmio(BLEND), Mmio(AV_BUF)
    if a.action == "show":
        m = read_mode(dp)
        print("mode:", m, "\nAV_OUTPUT 0x%x CLK_SRC 0x%x" % (av.read(AV_OUTPUT), av.read(AV_CLK_SRC)))
        return
    if a.action == "off":
        live_off(blend, av)
        print("DP video layer back to PS clock / internal timing")
        return
    ovl = Mmio(a.ovl_base, 0x8000)          # registers + profile + text RAM
    if not a.no_camera:
        nfs, stride = camera_setup(Mmio(a.vdma_base), ovl)
        print("camera preview: %d frame stores, stride %d" % (nfs, stride))
    m = live_on(dp, blend, av, ovl)          # also commits CTRL (camera on) at the next vblank
    print("live: %dx%d, htotal %d vtotal %d" % (m["hres"], m["vres"], m["htotal"], m["vtotal"]))


if __name__ == "__main__":
    sys.exit(main())
