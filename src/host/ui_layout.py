"""Screen layout of the 800x480 panel, shared by the asset builder (tools/ui) and the
board driver (fsf_ui.py). Python 3.6, no dependencies.

The PL draws three things over a static background image (bg.bin in DDR):
  water   : pipe coolant pixels recoloured every frame from the axial profile
  camera  : live speckle crop from the VDMA frame stores
  text    : a 12x24 character grid (font ROM in the PL); dynamic numbers only
Everything else (titles, panels, colorbar, ticks, sensor labels) is baked
into the background, so all static text can be any font, including Chinese.
"""
W, H = 800, 480
CELL_W, CELL_H = 12, 24
TEXT_PITCH = 128                         # text RAM: address = row * 128 + col

# palette indices for text (8 entries, RGB888)
PALETTE = [0x000000, 0xE6EDF3, 0x8B949E, 0x39C5CF, 0x3FB950, 0xF0B429, 0xF85149, 0x58A6FF]
BLACK, WHITE, DIM, ACCENT, OK, WARN, ALARM, BLUE = range(8)
BG, PANEL, BORDER = 0x0B0F14, 0x121821, 0x263040        # background colours (baked)

# temperature scale of the water colours and the colorbar (degC)
T_LO, T_HI = 30.0, 50.0

# regions (x, y, w, h) in pixels
PIPE_PANEL = (8, 26, 784, 146)           # panel box: pipe, heat strip, sensor temperatures, colorbar
PIPE = (12, 30, 776, 55)                 # Blender render (2x, rows 40..150 of 1552x220) downscaled
HEAT_STRIP = (12, 88, 776, 14)           # W = 255, black: the PL paints the axial profile here
SENSOR_NAME_Y = 104                      # baked sensor names under the strip (10 px)
PIPE_TEMP_ROW = 5                        # dynamic temperatures, text row 5 (y 120..143)
COLORBAR = (100, 148, 600, 6)            # baked, ticks + labels below
CAM_PANEL = (8, 176, 400, 276)
CAM = (16, 204, 384, 240)                # live camera window (multiple of 128 wide)
CARDS_X, CARDS_Y, CARD_W, CARD_H = (416, 540, 664), 192, 120, 72   # 3x3 grid, 3 text rows each
MQTT_ROW = 17                            # MQTT topic / counters under the cards
INFO_ROW = 19                            # bottom status line (text row 19, y 456..479)

# touch panel (wch.cn USB2IIC_CTP_CONTROL, /dev/input/by-id/usb-wch.cn_USB2IIC_CTP_CONTROL-event-if00):
# raw ABS_X / ABS_Y 0..4096, same orientation as the screen. Corner taps 2026-11-02:
# TL (174, 238)  TR (3793, 273)  BR (3891, 3942)  BL (225..240, 3993..4044)
TOUCH_RAW = (200, 255, 3842, 3993)       # raw x at screen x=0 / 799, y at y=0 / 479 (corner taps ~= 8 px in)
TOUCH_DEV = "/dev/input/by-id/usb-wch.cn_USB2IIC_CTP_CONTROL-event-if00"


def touch_to_screen(rx, ry):
    """Raw touch -> screen pixel (clamped). Linear fit of the four corner taps."""
    x0, y0, x1, y1 = TOUCH_RAW
    x = (rx - x0) * (W - 17) / float(x1 - x0) + 8
    y = (ry - y0) * (H - 17) / float(y1 - y0) + 8
    return max(0, min(W - 1, int(round(x)))), max(0, min(H - 1, int(round(y))))


# glyphs in the PL font ROM: printable ASCII, then these (text code 0x80 + index, ROM glyph 95 + index)
EXTRA_GLYPHS = u"°Δ●▲↑↓±"    # ° Δ ● ▲ ↑ ↓ ±
FONT_FIRST, FONT_N = 0x20, 95 + 7         # ROM glyph index = code - 0x20 (extras at 95..)


def glyph_code(ch):
    """Character -> text RAM code (0 = blank)."""
    o = ord(ch)
    if 0x20 <= o < 0x7F:
        return o
    i = EXTRA_GLYPHS.find(ch)
    return 0x7F + i + 1 if i >= 0 else ord("?")      # 0x80.. : ROM index 96..


def glyph_index(code):
    """Text RAM code -> font ROM glyph index."""
    return code - 0x20 if code < 0x7F else 95 + (code - 0x80)


# dynamic text fields: name -> (col, row, width)
FIELDS = {
    "badge":   (33, 0, 13),               # reserved status text (blank)
    "status":  (47, 0, 11),               # ● MQTT
    "clock":   (58, 0, 8),                # 23:59:59
    "info":    (1, INFO_ROW, 40),         # #1234  dt 1.00s  QCNN 0.57ms  pre 312ms
    "mqtt":    (35, MQTT_ROW, 31),        # fsf/fz3b/state  sent 1284 q0
}

def sensor_field(k):
    """Card k (0..8, dT order): alarm glyph on the name row, temperature, dT."""
    c, r = k % 3, k // 3
    col = CARDS_X[c] // CELL_W + 1
    row = CARDS_Y // CELL_H + 3 * r
    return {"alarm": (col + 8, row, 1), "temp": (col, row + 1, 9), "dT": (col, row + 2, 9)}


def pipe_field_cols(xs_px):
    """Columns for the per-sensor temperatures under the pipe (4 chars, centred)."""
    return [int(round((PIPE[0] + x) / CELL_W - 2)) for x in xs_px]

