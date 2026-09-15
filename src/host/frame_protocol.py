"""Frame protocol shared by host, board bridge and emulator (same bytes as UART RTL).

request : A5 5A id pixel[0..3135]            (56x56 uint8, raster order)
reply   : 5A A5 id result_lo result_hi       (int16, T = 25 + 175 * result / 4096)
"""
import numpy as np

N_PIXEL = 56 * 56
REQ_SYNC, REPLY_SYNC = b"\xa5\x5a", b"\x5a\xa5"
REQ_LEN, REPLY_LEN = 3 + N_PIXEL, 5
T_MIN, T_SPAN, OUT_FRAC = 25.0, 175.0, 12


def code_to_celsius(code):
    return T_MIN + T_SPAN * np.asarray(code, dtype=np.float64) / 2 ** OUT_FRAC


def encode_requests(frames_u8, first_id=0):
    """(N, 56, 56) uint8 -> one contiguous byte buffer of N requests."""
    n = len(frames_u8)
    buf = np.empty((n, REQ_LEN), dtype=np.uint8)
    buf[:, 0:2] = np.frombuffer(REQ_SYNC, dtype=np.uint8)
    buf[:, 2] = (np.arange(n) + first_id) & 0xFF
    buf[:, 3:] = frames_u8.reshape(n, N_PIXEL)
    return buf.tobytes()


def encode_reply(frame_id, code):
    return REPLY_SYNC + bytes([frame_id & 0xFF]) + int(code).to_bytes(2, "little", signed=True)


class ReplyParser:
    """Byte stream -> (id, code) tuples; resynchronises on the reply header."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            start = self.buf.find(REPLY_SYNC)
            if start < 0:
                del self.buf[:-1]            # last byte may be half a header
                return out
            if len(self.buf) - start < REPLY_LEN:
                del self.buf[:start]
                return out
            frame_id = self.buf[start + 2]
            code = int.from_bytes(self.buf[start + 3:start + 5], "little", signed=True)
            out.append((frame_id, code))
            del self.buf[:start + REPLY_LEN]


def load_ds14_frames(data_dir="data/processed/fss_exp_thermal", n=None):
    """Dataset-14 frames pooled 224 -> 56 exactly as in training, plus labels."""
    labels = np.load(f"{data_dir}/labels.npz")
    idx = np.flatnonzero(labels["dataset_id"] == 14)[:n]
    x = np.load(f"{data_dir}/images.npy", mmap_mode="r")[idx].astype(np.int64)
    frames = ((x.reshape(len(idx), 56, 4, 56, 4).sum((2, 4)) + 8) >> 4).astype(np.uint8)
    return frames, labels["temperature"][idx]
