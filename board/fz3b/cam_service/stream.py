#!/usr/bin/env python3
"""FZ3B OV9281 live view server (lane-0 mode, 1280x800 RAW8 sensor -> 640x800 -> 640x400).

Captures from the PL VDMA ring and serves over HTTP:
    /             viewer page with live controls (exposure, gain, frame length, AE, processing)
    /stream       MJPEG of the processed 640x400 image
    /raw          multipart stream of untouched 640x800 RAW8 frames (for processing on the Mac)
    /stats        JSON counters
    /ctrl?...     change settings live: exp=, gain=, vts=, ae=0|1, target=, mode=, quality=, every=
    /time         board clock (latency measurement)
    /snap?label=&note=                      save one raw frame (PNG, lossless) + the processed view (JPEG)
    /rec/start?secs=&every=&roi=x,y,w,h&label=&note=   record raw frames into RAM, then save .npy + .json
    /rec/stop   /rec/discard   /rec/status
    /files      JSON list;  /files/<name> download;  /files/delete?base=<base>
Processing on the board: built-in modes (none, gamma, stretch, equalize, clahe, edges) or a
user file /root/cam/process.py defining process(img_640x400_uint8) -> uint8 image, reloaded
when the file changes. Requires the fz3b_cam bitstream (version 0xCA928103).
"""
import sys, os, re, math, time, json, shutil, subprocess, threading, argparse, socketserver, http.server, importlib.util, traceback
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, "/root/cam")
import cam
import numpy as np
import cv2

W, H, LW = 1280, 800, 640
EXP_OFFSET = 25          # exposure must stay <= VTS - 25 lines (mainline ov9282 driver)
GAIN_MIN, GAIN_MAX = 0x10, 0xff
PROC_FILE = "/root/cam/process.py"
DATA_DIR = "/root/cam/data"                 # photos and recordings (SD card)
REC_MAX_BYTES = 768 << 20                    # recordings are buffered in RAM, then written to the SD card
DISK_RESERVE = 300 << 20
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
FORMAT_NOTE = "uint8; lane-0 RAW8: each row is a sensor row, columns are the even sensor columns (0,2,4,...)"
cv2.setNumThreads(1)     # parallelism comes from our worker threads

class Sensor:
    """Live exposure / gain / frame-length control with OV9281 group hold (0x3308)."""
    def __init__(self, s, vts, exp, gain):
        self.s = s; self.lock = threading.Lock()
        self.vts, self.exp, self.gain = vts, exp, gain
    def line_us(self): return 1e6 / (self.fps() * self.vts) if self.vts else 0
    def fps(self): return 144.83 * 910 / self.vts      # HTS fixed at 0x2d8; measured 144.83 fps at VTS 910
    def apply(self, vts=None, exp=None, gain=None):
        with self.lock:
            if vts is not None: self.vts = int(min(max(vts, 880), 0xffff))
            if exp is not None: self.exp = int(exp)
            if gain is not None: self.gain = int(gain)
            self.exp = int(min(max(self.exp, 1), self.vts - EXP_OFFSET))
            self.gain = int(min(max(self.gain, GAIN_MIN), GAIN_MAX))
            s = self.s; e = self.exp << 4
            if s is None: return
            s.wr(0x3308, 0x01)                                   # group hold: apply on one frame boundary
            s.wr(0x380e, self.vts >> 8); s.wr(0x380f, self.vts & 0xff)
            s.wr(0x3500, (e >> 16) & 0x0f); s.wr(0x3501, (e >> 8) & 0xff); s.wr(0x3502, e & 0xff)
            s.wr(0x3509, self.gain)
            s.wr(0x3308, 0x00)
    def state(self):
        return dict(vts=self.vts, exposure=self.exp, exposure_us=round(self.exp * self.line_us(), 1),
                    gain=self.gain, gain_x=round(self.gain / 16.0, 2), nominal_fps=round(self.fps(), 2),
                    exp_max=self.vts - EXP_OFFSET)

class Proc:
    """Board-side processing of the 640x400 image."""
    MODES = ("none", "gamma", "stretch", "sharp", "equalize", "clahe", "flat", "edges", "custom")
    def __init__(self):
        # Keep the preview faithful to RAW8 by default.  Flat-field correction
        # is available on demand, but its large Gaussian blur is too expensive
        # for the full-rate stream and can make motion look soft.
        self.mode = "none"; self.gamma = 2.2; self._lut_g = None; self._set_lut()
        self.clahe = cv2.createCLAHE(2.0, (8, 8))
        self.user = None; self.user_mtime = 0; self.user_err = ""
    def _set_lut(self):
        self._lut_g = np.clip(((np.arange(256) / 255.0) ** (1 / self.gamma)) * 255, 0, 255).astype(np.uint8)
    def _load_user(self):
        try: m = os.path.getmtime(PROC_FILE)
        except OSError: self.user = None; self.user_err = "no %s" % PROC_FILE; return
        if m == self.user_mtime: return
        self.user_mtime = m
        try:
            spec = importlib.util.spec_from_file_location("user_process", PROC_FILE)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            self.user = mod.process; self.user_err = ""
        except Exception:
            self.user = None; self.user_err = traceback.format_exc(limit=2)
    def run(self, img):
        m = self.mode
        if m == "none": return img
        if m == "gamma": return cv2.LUT(img, self._lut_g)
        if m == "stretch":
            lo, hi = np.percentile(img[::4, ::4], (1, 99))
            return cv2.convertScaleAbs(img, alpha=255.0 / max(hi - lo, 1), beta=-lo * 255.0 / max(hi - lo, 1))
        if m == "sharp":
            # Display-only mild unsharp mask. RAW and saved training data stay intact.
            lo, hi = np.percentile(img[::4, ::4], (1, 99))
            base = cv2.convertScaleAbs(img, alpha=255.0 / max(hi - lo, 1), beta=-lo * 255.0 / max(hi - lo, 1))
            blur = cv2.GaussianBlur(base, (0, 0), 0.8)
            return cv2.addWeighted(base, 1.18, blur, -0.18, 0)
        if m == "equalize": return cv2.equalizeHist(img)
        if m == "clahe": return self.clahe.apply(img)
        if m == "flat":
            # 只校正预览中的低频照明/环形渐晕；/raw 与训练数据保持原始 RAW8。
            bg = cv2.GaussianBlur(img, (0, 0), 24)
            norm = img.astype(np.float32) / (bg.astype(np.float32) + 1.0) * max(float(bg.mean()), 1.0)
            lo, hi = np.percentile(norm[::4, ::4], (1, 99))
            return np.clip((norm - lo) * 255.0 / max(hi - lo, 1.0), 0, 255).astype(np.uint8)
        if m == "edges": return cv2.Canny(img, 50, 150)
        if m == "custom":
            self._load_user()
            if self.user is None: return img
            try:
                out = self.user(img)
                return out if out.dtype == np.uint8 else np.clip(out, 0, 255).astype(np.uint8)
            except Exception:
                self.user_err = traceback.format_exc(limit=2); return img
        return img

def slug(text):
    t = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_.")
    return t[:40]

def parse_roi(text):
    if not text: return None
    try: x, y, w, h = [int(v) for v in str(text).replace(" ", "").split(",")]
    except ValueError: raise ValueError("ROI must be x,y,w,h")
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > LW or y + h > H:
        raise ValueError("ROI must lie inside the 640x800 frame")
    return (x, y, w, h)

def square_view(img):
    """640x800 lane-0 frame -> 640x400 with square pixels (row pairs averaged)."""
    return ((img[0::2].astype(np.uint16) + img[1::2]) >> 1).astype(np.uint8)

class Recorder:
    """Photos and RAM-buffered raw recordings."""
    def __init__(self, capr):
        self.c = capr; self.lock = threading.Lock()
        self.state = "idle"; self.msg = ""; self.last_file = ""; self.base = ""
        self.buf = None; self.n = 0; self.nmax = 0; self.every = 1; self.k = 0; self.roi = None
        self.meta = {}; self.save_pct = 0.0; self.preview_busy = 0
        os.makedirs(DATA_DIR, exist_ok=True)
        self.x264 = False
        threading.Thread(target=self._probe_ffmpeg, daemon=True).start()

    def _probe_ffmpeg(self):
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=20).stdout.decode(errors="ignore")
            self.x264 = "libx264" in out
        except Exception:
            self.x264 = False

    def fps(self): return self.c.stats.get("sensor_fps") or self.c.sensor.fps()

    def limits(self, roi=None, every=1):
        w, h = (roi[2], roi[3]) if roi else (LW, H)
        fb = w * h
        free = shutil.disk_usage(DATA_DIR).free
        cap = max(0, min(REC_MAX_BYTES, free - DISK_RESERVE)) // fb
        fps = self.fps()
        return dict(max_frames=int(cap), max_secs=round(cap * every / max(fps, 1), 1), fps=round(fps, 2),
                    frame_kb=round(fb / 1024, 1), disk_free_mb=int(free / 2 ** 20))

    def status(self):
        d = dict(state=self.state, n=self.n, nmax=self.nmax, save_pct=round(self.save_pct, 1), msg=self.msg,
                 last_file=self.last_file, base=self.base, preview_busy=self.preview_busy, x264=self.x264)
        try: d["limits"] = self.limits()
        except OSError as e: d["limits"] = {"error": str(e)}
        return d

    # ---------------- recording
    def start(self, secs, every, roi, label, note):
        with self.lock:
            if self.state in ("recording", "saving"): raise ValueError("正在%s，请稍候" % ("录制" if self.state == "recording" else "保存"))
            if not self.c.cam_ok: raise ValueError("相机未连接，不能录制")
            if not (0 < secs <= 3600): raise ValueError("时长要在 0～3600 秒之间")
            if not (1 <= every <= 1000): raise ValueError("每 N 帧取 1：N 要在 1～1000 之间")
            lim = self.limits(roi, every)
            n = int(math.ceil(secs * lim["fps"] / every))
            if n > lim["max_frames"]:
                raise ValueError("太长了：按当前帧率和 ROI，最多能录 %.1f 秒（%d 帧，受内存/磁盘限制）" % (lim["max_secs"], lim["max_frames"]))
            w, h = (roi[2], roi[3]) if roi else (LW, H)
            self.buf = np.empty((max(n, 1), h, w), np.uint8)
            self.n = 0; self.nmax = max(n, 1); self.every = every; self.k = 0; self.roi = roi
            self.times, self.fes, self.exps, self.gains = [], [], [], []
            self.base = time.strftime("rec_%Y%m%d_%H%M%S") + ("_" + slug(label) if slug(label) else "")
            self.meta = dict(kind="recording", base=self.base, label=label, note=note,
                             roi=list(roi) if roi else [0, 0, LW, H], every=every, requested_secs=secs,
                             frames_planned=self.nmax, start_time=time.time(), settings=self.c.settings(),
                             format=FORMAT_NOTE, shape=[self.nmax, h, w])
            self.state = "recording"; self.msg = ""; self.save_pct = 0.0
        return self.status()

    def on_frame(self, img, t, fe):
        if self.state != "recording": return
        with self.lock:
            if self.state != "recording": return
            self.k += 1
            if (self.k - 1) % self.every: return
            if self.roi:
                x, y, w, h = self.roi; self.buf[self.n] = img[y:y + h, x:x + w]
            else:
                self.buf[self.n] = img
            sn = self.c.sensor
            self.times.append(t); self.fes.append(fe); self.exps.append(sn.exp); self.gains.append(sn.gain)
            self.n += 1
            if self.n >= self.nmax: self._finish_locked()

    def _finish_locked(self):
        self.state = "saving"; self.save_pct = 0.0
        threading.Thread(target=self._save, daemon=True).start()

    def stop(self):
        with self.lock:
            if self.state != "recording": return self.status()
            if self.n == 0: self.buf = None; self.state = "idle"; self.msg = "还没录到帧，已取消"
            else: self._finish_locked()
        return self.status()

    def discard(self):
        with self.lock:
            if self.state == "recording":
                self.buf = None; self.state = "idle"; self.n = 0; self.msg = "已放弃本次录制"
        return self.status()

    def _save(self):
        base = self.base
        try:
            n = self.n; buf = self.buf[:n]; m = self.meta
            t = np.array(self.times); fe = np.array(self.fes, np.int64)
            dt = float(t[-1] - t[0]) if n > 1 else 0.0
            fps_rec = (n - 1) / dt if dt > 0 else 0.0
            skipped = int(max(0, (fe[-1] - fe[0]) - self.every * (n - 1))) if n > 1 else 0
            m.update(n=n, frames=n, shape=list(buf.shape), secs=round(dt + (1 / fps_rec if fps_rec else 0), 3),
                     fps_recorded=round(fps_rec, 3), sensor_fps=round(self.fps(), 3), skipped=skipped, torn=0,
                     stopped_early=n < self.nmax, end_time=time.time(),
                     times=[round(float(x), 6) for x in t], fe_counter=[int(x) for x in fe],
                     exposure=self.exps, gain=self.gains)
            path = os.path.join(DATA_DIR, base + ".npy")
            with open(path + ".part", "wb") as f:
                np.lib.format.write_array_header_1_0(f, np.lib.format.header_data_from_array_1_0(buf))
                step = max(1, (8 << 20) // max(buf[0].nbytes, 1))
                for i in range(0, n, step):
                    f.write(buf[i:i + step].tobytes())
                    self.save_pct = 100.0 * min(n, i + step) / n
                f.flush(); os.fsync(f.fileno())
            os.rename(path + ".part", path)
            mid = buf[n // 2]
            th = square_view(mid) if self.roi is None else mid
            cv2.imwrite(os.path.join(DATA_DIR, base + "_thumb.jpg"), cv2.resize(th, (th.shape[1] // 2, th.shape[0] // 2)))
            m["files"] = [base + ".npy", base + ".json"]
            m["preview"] = "pending" if self.x264 else "none"
            json.dump(m, open(os.path.join(DATA_DIR, base + ".json"), "w"), ensure_ascii=False)
            self.last_file = base
            self.msg = "已保存 %s：%d 帧，%.2f 秒，%.1f fps，丢帧 %d" % (base, n, m["secs"], fps_rec, skipped)
        except Exception as e:
            self.msg = "保存失败：%s" % e; traceback.print_exc()
            with self.lock: self.state = "error"; self.buf = None
            return
        with self.lock: self.state = "idle"; self.buf = None
        if self.x264: threading.Thread(target=self._preview, args=(base,), daemon=True).start()

    def _preview(self, base):
        """Browser-playable H.264 preview of a saved recording (low priority, from the file on disk)."""
        self.preview_busy += 1
        try:
            m = json.load(open(os.path.join(DATA_DIR, base + ".json")))
            a = np.load(os.path.join(DATA_DIR, base + ".npy"), mmap_mode="r")
            N, h, w = a.shape
            full = (w == LW and h == H)
            ow, oh = (LW, H // 2) if full else (w - w % 2, h - h % 2)
            fps = max(1.0, min(m.get("fps_recorded") or 30, 240))
            out = os.path.join(DATA_DIR, base + ".mp4")
            p = subprocess.Popen(["nice", "-n", "19", "ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray",
                                  "-s", "%dx%d" % (ow, oh), "-r", "%.3f" % fps, "-i", "-", "-c:v", "libx264", "-preset", "ultrafast",
                                  "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out + ".part.mp4"],
                                 stdin=subprocess.PIPE)
            for i in range(N):
                f = square_view(a[i]) if full else a[i][:oh, :ow]
                p.stdin.write(np.ascontiguousarray(f).tobytes())
            p.stdin.close(); p.wait()
            if p.returncode == 0:
                os.rename(out + ".part.mp4", out)
                m["preview"] = base + ".mp4"; m["files"] = m.get("files", []) + [base + ".mp4"]
            else:
                m["preview"] = "failed"
            json.dump(m, open(os.path.join(DATA_DIR, base + ".json"), "w"), ensure_ascii=False)
        except Exception:
            traceback.print_exc()
        finally:
            self.preview_busy -= 1

    # ---------------- photo
    def snap(self, label, note):
        last = self.c.last
        if not self.c.cam_ok: raise ValueError("相机未连接，不能拍照")
        if last is None: raise ValueError("还没有画面")
        img, t, fe = last
        view = self.c.jpeg
        base = time.strftime("img_%Y%m%d_%H%M%S", time.localtime(t)) + "_%03d" % int((t % 1) * 1000) + ("_" + slug(label) if slug(label) else "")
        meta = dict(kind="photo", base=base, label=label, note=note, time=t, fe_counter=fe, settings=self.c.settings(),
                    shape=[H, LW], format=FORMAT_NOTE, files=[base + ".png", base + "_view.jpg", base + ".json"],
                    max=int(img.max()), mean=round(float(img.mean()), 2), saturated_pct=round(float((img >= 254).mean() * 100), 3))
        threading.Thread(target=self._save_photo, args=(img, view, meta), daemon=True).start()
        self.last_file = base
        return meta

    def _save_photo(self, img, view, meta):
        try:
            base = meta["base"]
            cv2.imwrite(os.path.join(DATA_DIR, base + ".png"), img, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            if view: open(os.path.join(DATA_DIR, base + "_view.jpg"), "wb").write(view)
            json.dump(meta, open(os.path.join(DATA_DIR, base + ".json"), "w"), ensure_ascii=False)
        except Exception as e:
            self.msg = "拍照保存失败：%s" % e; traceback.print_exc()

class Files:
    EXTS = (".npy", ".png", "_view.jpg", "_thumb.jpg", ".mp4", ".json")
    TYPES = {".npy": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".json": "application/json"}
    def __init__(self): self.cache = {}
    def list(self):
        names = os.listdir(DATA_DIR)
        out = []
        for fn in names:
            if not fn.endswith(".json"): continue
            p = os.path.join(DATA_DIR, fn)
            try: stt = os.stat(p)
            except OSError: continue
            key = (stt.st_mtime, stt.st_size)
            c = self.cache.get(fn)
            if not c or c[0] != key:
                try: m = json.load(open(p))
                except (OSError, ValueError): continue
                small = {k: m.get(k) for k in ("kind", "base", "label", "note", "time", "start_time", "frames", "secs",
                                               "fps_recorded", "skipped", "roi", "every", "preview", "settings", "max", "saturated_pct", "stopped_early")}
                c = (key, small); self.cache[fn] = c
            d = dict(c[1]); base = fn[:-5]
            d["base"] = base
            d["files"] = [base + e for e in self.EXTS if (base + e) in names]
            d["size_mb"] = round(sum(os.path.getsize(os.path.join(DATA_DIR, x)) for x in d["files"]) / 2 ** 20, 2)
            d["t"] = d.get("time") or d.get("start_time") or stt.st_mtime
            out.append(d)
        out.sort(key=lambda d: d["t"], reverse=True)
        return out
    def delete(self, base):
        if not SAFE.match(base or ""): raise ValueError("bad name")
        n = 0
        for e in self.EXTS + (".npy.part", ".mp4.part.mp4"):
            p = os.path.join(DATA_DIR, base + e)
            if os.path.exists(p): os.remove(p); n += 1
        self.cache.pop(base + ".json", None)
        return n
    def path(self, name):
        if not SAFE.match(name or ""): return None
        p = os.path.join(DATA_DIR, name)
        return p if os.path.isfile(p) else None
    def ctype(self, name):
        for e, t in self.TYPES.items():
            if name.endswith(e): return t
        return "application/octet-stream"

class Capture:
    """Owns the PL + sensor. The HTTP server keeps running when the camera is missing;
    connect() (re)loads the bitstream if needed, checks the chip ID, initialises the sensor
    and starts streaming. A watchdog reconnects automatically when frames stop."""
    def __init__(self, vts, exposure, gain, quality, workers):
        self.quality = quality; self.every = 5   # preview ~29 fps; RAW/rec still see every frame
        self.lock = threading.Condition()
        self.jpeg = None; self.seq = 0
        self.raw = None; self.raw_seq = 0; self.raw_t = 0.0; self.rawlock = threading.Condition()
        self.stats = dict(captured=0, encoded=0, torn_retry=0, clients=0, raw_clients=0, sensor_fps=0.0,
                          enc_ms=0.0, proc_ms=0.0, kb=0.0, mean=0.0, sat_pct=0.0, lines_per_frame=0, bad_hdr=0)
        self.ae = False; self.ae_target = 110.0
        # RAW auto exposure controller state.  The loop acts on the sensor
        # registers, while the preview processing remains display-only.
        self.ae_i = 0.0; self.ae_prev_error = 0.0; self.ae_prev_t = 0.0; self.ae_last_apply = 0.0
        self.last = None
        self.s = None; self.pl = None; self.mem = None
        self.sensor = Sensor(None, vts, exposure, gain)         # settings survive reconnects
        self.proc = Proc()
        self.cam_ok = False; self.cam_state = "starting"; self.cam_msg = ""; self.chip_id = None
        self.connects = 0; self.auto_reconnect = True; self.conn_lock = threading.Lock()
        self.last_frame_t = 0.0; self.started = time.time()
        self.running = True
        self.q = []; self.qlock = threading.Condition(); self.n_in = 0
        self.rec = Recorder(self); self.files = Files()
        threading.Thread(target=self._grab, daemon=True).start()
        for _ in range(workers): threading.Thread(target=self._encode, daemon=True).start()
        threading.Thread(target=self._rate, daemon=True).start()
        threading.Thread(target=self._ae, daemon=True).start()
        threading.Thread(target=self._watchdog, daemon=True).start()
        threading.Thread(target=self.connect, daemon=True).start()

    # ---------------- camera connection
    def _load_bitstream(self):
        r = subprocess.run(["/bin/sh", "/root/cam/load.sh", "cam"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        out = r.stdout.decode(errors="ignore")
        if r.returncode != 0 or "operating" not in out: raise RuntimeError("加载比特流失败：" + out.strip()[-200:])

    def connect(self, reload_bitstream=False):
        """Bring the camera up. Returns (ok, message). Safe to call at any time."""
        if not self.conn_lock.acquire(blocking=False):
            return False, "正在连接，请稍候"
        try:
            self.cam_ok = False; self.cam_state = "connecting"; self.cam_msg = "正在连接相机"
            if self.rec.state == "recording": self.rec.discard()
            time.sleep(0.15)                          # let the grab/rate threads see cam_ok=False first
            try:
                if self.s is not None:
                    try: self.s.wr(0x0100, 0)
                    except OSError: pass
                pl = cam.PL()
                if reload_bitstream or pl.r.rd(0x04) != 0xCA928103:
                    self.cam_msg = "正在加载相机比特流"
                    self._load_bitstream()
                    pl = cam.PL()
                ver = pl.r.rd(0x04); self.bit_ver = "0x%08x" % ver
                if ver != 0xCA928103:
                    raise RuntimeError("比特流版本不对：0x%08x" % ver)
                if self.mem is None: self.mem = cam.Mem(cam.BUF_PHYS, cam.BUF_SPAN * cam.NFRM)
                # sensor hard reset through RST#/PWDN# from the PL, then MCLK on
                pl.ctrl(0x3e & ~0x6); time.sleep(0.02); pl.ctrl(0x3e | 1); time.sleep(0.02); pl.ctrl(0x3e)
                time.sleep(0.03)
                s = cam.Sensor()
                try:
                    cid = s.rd16(0x300a)
                except OSError:
                    raise RuntimeError("相机在 I2C 上不应答（0x60）。检查相机尾线、25P 排线是否插好，FZ3B/H1 是否上电")
                self.chip_id = cid
                if cid != 0x9281: raise RuntimeError("读到的芯片 ID 是 0x%04x，不是 0x9281" % cid)
                s.wr(0x0100, 0)
                sn = self.sensor
                cam.sensor_init(s, "1280x800", raw8=True, vts=sn.vts, exposure=sn.exp, gain=sn.gain)
                sn.s = s; sn.apply()
                ctrl = 0x3e | (1 << 8) | (LW << 16); pl.ctrl(ctrl | 1); pl.ctrl(ctrl)
                pl.vdma_start(LW, H)
                s.wr(0x0100, 1)
                self.s, self.pl = s, pl
                # wait for frames
                fe0 = pl.r.rd(0x44); t0 = time.time()
                while time.time() - t0 < 1.5 and pl.r.rd(0x44) == fe0: time.sleep(0.02)
                if pl.r.rd(0x44) == fe0:
                    raise RuntimeError("相机已应答，但 1.5 秒内没有收到图像帧（检查 MIPI 排线）")
                self.last_frame_t = time.time()
                self.connects += 1; self.cam_ok = True; self.cam_state = "ok"
                self.cam_msg = "芯片 ID 0x%04x，本次服务第 %d 次连接" % (cid, self.connects)
                return True, self.cam_msg
            except Exception as e:
                self.cam_ok = False; self.cam_state = "error"; self.cam_msg = str(e)
                return False, self.cam_msg
        finally:
            self.conn_lock.release()

    def disconnect(self):
        with self.conn_lock:
            self.cam_ok = False; self.cam_state = "stopped"; self.cam_msg = "已手动断开（停流）"
            if self.rec.state == "recording": self.rec.discard()
            try:
                if self.s: self.s.wr(0x0100, 0)
                if self.pl: self.pl.vdma_stop()
            except OSError: pass
        return True, self.cam_msg

    def _watchdog(self):
        """Reconnect when frames stop for 2 s (cable bump, sensor reset), or retry a failed connect every 5 s."""
        fails = 0
        while self.running:
            time.sleep(1.0)
            if not self.auto_reconnect or self.cam_state in ("connecting", "stopped", "starting"): continue
            if self.cam_state == "ok" and time.time() - self.last_frame_t > 2.0:
                self.cam_ok = False; self.cam_state = "error"; self.cam_msg = "2 秒没有收到图像，正在自动重连"
                fails = 0
            if self.cam_state == "error":
                wait = min(30, 5 * (1 + fails))
                if time.time() - getattr(self, "_last_try", 0) >= wait:
                    self._last_try = time.time()
                    ok, _ = self.connect()
                    fails = 0 if ok else fails + 1

    def cam_status(self):
        return dict(cam_ok=self.cam_ok, cam_state=self.cam_state, cam_msg=self.cam_msg, connects=self.connects,
                    auto_reconnect=self.auto_reconnect, chip_id=("0x%04x" % self.chip_id) if self.chip_id else None,
                    frame_age_s=round(time.time() - self.last_frame_t, 1) if self.last_frame_t else None,
                    uptime_s=int(time.time() - self.started), bitstream=getattr(self, "bit_ver", None))

    def _park(self): return (self.pl.v.rd(0x28) >> 24) & 0x1f

    def _grab(self):
        last = -1
        while self.running:
            if not self.cam_ok: time.sleep(0.05); last = -1; continue
            try: a = self._park()
            except Exception: time.sleep(0.05); continue
            if a == last: time.sleep(0.0005); continue
            slot = (a - 1) % cam.NFRM
            img = np.frombuffer(self.mem.m, np.uint8, LW * H, slot * cam.BUF_SPAN).reshape(H, LW).copy()
            b = self._park()
            if (b - a) % cam.NFRM >= cam.NFRM - 1:     # VDMA lapped the slot while copying
                self.stats["torn_retry"] += 1; continue
            last = a; self.stats["captured"] += 1; t = time.time(); self.last_frame_t = t
            fe = self.pl.r.rd(0x44)                    # sensor frame-end counter, for dropped-frame accounting
            self.last = (img, t, fe)
            self.rec.on_frame(img, t, fe)
            # always refresh: a new /raw client must never be handed a frame from an earlier connection
            with self.rawlock:
                self.raw = img; self.raw_seq += 1; self.raw_t = t; self.rawlock.notify_all()
            self.n_in += 1
            if self.n_in % self.every: continue
            with self.qlock:
                self.q = [(t, img)]                     # newest frame only; encoders drop what they cannot keep up with
                self.qlock.notify()

    def _encode(self):
        while self.running:
            with self.qlock:
                while not self.q: self.qlock.wait()
                t, img = self.q.pop()
            t0 = time.time()
            small = ((img[0::2].astype(np.uint16) + img[1::2]) >> 1).astype(np.uint8)   # 640x400, square pixels
            sub = small[::8, ::8]
            self.stats["mean"] = 0.9 * self.stats["mean"] + 0.1 * float(sub.mean())
            self.stats["sat_pct"] = 0.9 * self.stats["sat_pct"] + 0.1 * float((sub >= 250).mean() * 100)
            out = self.proc.run(small)
            t1 = time.time()
            ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
            t2 = time.time()
            if not ok: continue
            with self.lock:
                b = buf.tobytes(); stamp = ("t=%.6f" % t).encode()
                com = b"\xff\xfe" + (len(stamp) + 2).to_bytes(2, "big") + stamp
                self.jpeg = b[:2] + com + b[2:]; self.seq += 1
                st = self.stats; st["encoded"] += 1
                st["proc_ms"] = 0.9 * st["proc_ms"] + 0.1 * (t1 - t0) * 1000
                st["enc_ms"] = 0.9 * st["enc_ms"] + 0.1 * (t2 - t1) * 1000
                st["kb"] = 0.9 * st["kb"] + 0.1 * len(self.jpeg) / 1024
                self.lock.notify_all()

    def _ae(self):
        """Gentle PID-like RAW auto exposure around a fixed mean target.

        The measurement is taken from the unprocessed RAW8 frame.  Exposure
        is changed first (gain is only used after the exposure limit), and
        each update is bounded so a changing speckle pattern does not make the
        image pump or flicker.
        """
        while self.running:
            time.sleep(0.2)
            if not self.ae or not self.cam_ok: continue
            now = time.time(); dt = max(now - self.ae_prev_t, 0.2) if self.ae_prev_t else 0.2
            self.ae_prev_t = now
            m = max(float(self.stats["mean"]), 1.0)
            # Relative error is stable across target values.  Integral is
            # deliberately small; derivative damps changes in the pattern.
            err = (self.ae_target - m) / max(self.ae_target, 1.0)
            self.ae_i = max(-0.8, min(0.8, self.ae_i + err * dt))
            d = (err - self.ae_prev_error) / dt
            self.ae_prev_error = err
            # Low gains keep a changing speckle pattern from causing visible
            # pumping; the loop converges over several frames instead.
            control = max(-0.045, min(0.045, 0.25 * err + 0.02 * self.ae_i + 0.01 * d))
            # Protect highlights even when a sparse bright pattern leaves the
            # frame mean near the target.
            if self.stats["sat_pct"] > 2.0: control = min(control, -0.035)
            # Ignore tiny noise around the set point and rate-limit writes.
            if abs(control) < 0.012 or now - self.ae_last_apply < 0.25: continue
            sn = self.sensor; emax = sn.vts - EXP_OFFSET
            exp, gain = sn.exp, sn.gain
            factor = math.exp(control)
            new_exp = int(round(exp * factor))
            if control > 0: new_exp = max(exp + 1, new_exp)
            elif control < 0: new_exp = min(exp - 1, new_exp)
            if (control > 0 and exp >= emax) or (control < 0 and exp <= 1):
                new_gain = int(round(gain * factor))
                sn.apply(gain=new_gain)
            else:
                sn.apply(exp=max(1, min(emax, new_exp)))
            self.ae_last_apply = now

    def _rate(self):
        a = None; t = time.time()
        while self.running:
            time.sleep(1.0)
            if not self.cam_ok or self.pl is None:
                self.stats["sensor_fps"] = 0.0; a = None; continue
            try: b = self.pl.stats()
            except Exception: a = None; continue
            n = time.time()
            if a is not None:
                self.stats["sensor_fps"] = (b["fe"] - a["fe"]) / (n - t)
            self.stats["lines_per_frame"] = b["lines_per_frame"]
            self.stats["bad_hdr"] = b["bad_hdr"]
            a, t = b, n

    def set(self, q):
        if "auto_reconnect" in q: self.auto_reconnect = q["auto_reconnect"] in ("1", "true", "on")
        sn = self.sensor
        if "vts" in q: sn.apply(vts=int(q["vts"]))
        if "exp" in q: sn.apply(exp=int(q["exp"]))
        if "exp_us" in q: sn.apply(exp=int(float(q["exp_us"]) / max(sn.line_us(), 1e-3)))
        if "gain" in q: sn.apply(gain=int(q["gain"]))
        if "ae" in q:
            self.ae = q["ae"] in ("1", "true", "on")
            self.ae_i = 0.0; self.ae_prev_error = 0.0; self.ae_prev_t = 0.0
        if "target" in q: self.ae_target = float(min(max(float(q["target"]), 10), 245))
        if "mode" in q and q["mode"] in Proc.MODES: self.proc.mode = q["mode"]
        if "gamma" in q: self.proc.gamma = float(min(max(float(q["gamma"]), 0.2), 5)); self.proc._set_lut()
        if "quality" in q: self.quality = int(min(max(int(q["quality"]), 10), 100))
        if "every" in q: self.every = int(min(max(int(q["every"]), 1), 30))
        return self.state()

    def settings(self):
        d = self.sensor.state(); d.update(ae=self.ae, mode=self.proc.mode); return d

    def state(self):
        d = self.sensor.state()
        d.update(ae=self.ae, target=self.ae_target, mode=self.proc.mode, gamma=self.proc.gamma,
                 quality=self.quality, every=self.every, user_err=self.proc.user_err)
        d.update(self.cam_status())
        return d

    def stop(self):
        self.running = False
        try:
            if self.s: self.s.wr(0x0100, 0)
            if self.pl: self.pl.vdma_stop()
        except OSError: pass

PAGE = r"""<!doctype html><html><head><meta charset=utf-8><title>FZ3B OV9281</title>
<style>
*{box-sizing:border-box} body{background:#111;color:#ddd;font:13px -apple-system,sans-serif;margin:12px}
.top{display:flex;gap:14px;align-items:flex-start} .view{position:relative} #live{width:960px;background:#000;display:block}
#recdot{position:absolute;left:10px;top:8px;color:#f44;font-weight:600;display:none}
.p{width:330px} .box{background:#1b1b1b;border:1px solid #2a2a2a;border-radius:6px;padding:10px;margin-bottom:10px}
.box h3{margin:0 0 6px;font-size:13px;color:#fff} label{display:block;margin:8px 0 2px}
input[type=range]{width:100%} input[type=text],input[type=number]{width:100%;background:#222;color:#ddd;border:1px solid #333;padding:4px;border-radius:3px}
select,button{font:inherit} button{background:#2d6cdf;color:#fff;border:0;border-radius:4px;padding:6px 12px;cursor:pointer}
button.sec{background:#333} button.red{background:#c33} button:disabled{opacity:.45;cursor:default}
.row{display:flex;gap:6px;align-items:center} .row>*{flex:1} .v{float:right;color:#9cf} .note{color:#888;font-size:12px;margin-top:4px}
#msg{min-height:18px;color:#9f9;margin-top:6px} #msg.e{color:#f88}
progress{width:100%;height:10px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:8px} td,th{border-bottom:1px solid #2a2a2a;padding:5px 6px;text-align:left;vertical-align:middle}
th{color:#999;font-weight:500} td img{height:46px;display:block} a{color:#8bf;text-decoration:none;margin-right:8px}
#player{max-width:960px;width:100%;margin-top:8px;display:none;background:#000}
pre{font-size:11px;white-space:pre-wrap;max-height:180px;overflow:auto;margin:0}
.conn{display:flex;align-items:center;gap:10px;background:#1b1b1b;border:1px solid #2a2a2a;border-radius:6px;padding:8px 12px;margin-bottom:10px}
.dot{width:12px;height:12px;border-radius:50%;background:#666;flex:none} .dot.ok{background:#3c3} .dot.err{background:#e33} .dot.busy{background:#fb3}
#connmsg{flex:1;color:#ccc} .conn button{padding:5px 10px} .conn label{display:flex;align-items:center;gap:4px;margin:0;color:#aaa}
#nocam{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.72);color:#fbb;font-size:16px;text-align:center;padding:20px}
</style></head><body>
<div class=conn><div id=dot class=dot></div><div id=connmsg>正在连接服务…</div>
 <label title="画面中断 2 秒或连接失败时自动重试"><input id=autore type=checkbox checked> 自动重连</label>
 <button onclick="camAct('/cam/reconnect')">重新连接相机</button>
 <button class=sec onclick="camAct('/cam/reconnect?reload=1')" title="重新加载 FPGA 相机比特流后再连接">重载比特流</button>
 <button class=sec onclick="camAct('/cam/disconnect')">停流</button>
 <button class=sec onclick=svcRestart() title="重启板子上的服务程序（不重启板子）">重启服务</button>
</div>
<div class=top>
 <div class=view><img id=live src="/stream"><div id=nocam></div><div id=recdot>● REC <span id=recn></span></div>
  <div class="box" style="margin-top:10px;width:960px"><h3>已保存的照片和录像 <button class=sec onclick=loadFiles() style="float:right;padding:2px 8px">刷新</button></h3>
   <div class=note id=disk></div>
   <video id=player controls></video>
   <table><thead><tr><th>预览</th><th>名称</th><th>标签 / 备注</th><th>内容</th><th>大小</th><th>操作</th></tr></thead><tbody id=files></tbody></table>
  </div></div>
 <div class=p>
  <div class=box><h3>拍照 / 录制</h3>
   <label>标签（写进文件名，例：T25C、bend）</label><input id=label type=text maxlength=40>
   <label>备注（只存进 json）</label><input id=note type=text maxlength=200>
   <div class=row style="margin-top:10px"><button id=snap onclick=snap()>拍照</button></div>
   <label>录制时长（秒）<span class=v id=lim></span></label><input id=secs type=number value=5 min=0.1 step=0.5>
   <div class=row><div><label>每 N 帧存 1 帧</label><input id=every type=number value=1 min=1></div>
   <div><label>ROI x,y,w,h（留空=全幅）</label><input id=roi type=text placeholder="0,0,640,800"></div></div>
   <div class=row style="margin-top:10px"><button id=rec onclick=recStart()>开始录制</button><button id=stop class=red onclick=recStop() disabled>停止并保存</button><button id=disc class=sec onclick=recDiscard() disabled>放弃</button></div>
   <progress id=prog value=0 max=100 style="margin-top:8px"></progress>
   <div id=msg></div>
   <div class=note>录像先存进内存，结束后写到 SD 卡：原始 RAW8 .npy（无损）+ .json（每帧时间戳、曝光、增益）。另外生成一个 .mp4 预览，方便在网页上播放（有压缩，不要拿来训练）。拍照存无损 .png + 画面截图 .jpg。</div>
  </div>
  <div class=box><h3>相机</h3>
   <label>曝光 <span class=v id=ev></span></label><input id=exp type=range min=1 max=885 step=1>
   <label>增益 <span class=v id=gv></span></label><input id=gain type=range min=16 max=255 step=1>
   <label>帧长 VTS（帧率）<span class=v id=vv></span></label><input id=vts type=range min=910 max=7280 step=10>
   <label><input id=ae type=checkbox> 自动曝光，目标亮度 <span class=v id=tv></span></label><input id=target type=range min=20 max=230 step=1>
   <label>板端处理（只影响画面和 _view.jpg，不影响原始数据）</label><select id=mode><option>none<option>gamma<option>stretch<option>sharp<option>equalize<option>clahe<option>edges<option>custom</select>
   <label>JPEG 质量 <span class=v id=qv></span></label><input id=quality type=range min=20 max=95 step=1>
  </div>
  <div class=box><h3>状态</h3><pre id=s></pre></div>
 </div></div>
<script>
const $=id=>document.getElementById(id);let st={},rs={};
const esc=t=>String(t==null?'':t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function say(t,err){$('msg').textContent=t||'';$('msg').className=err?'e':''}
async function api(u,attempt=0){
 try{
  let r=await fetch(u,{cache:'no-store'}); if(!r.ok)throw new Error('HTTP '+r.status);
  let d=await r.json(); if(d.error){say(d.error,1);throw new Error(d.error)} return d;
 }catch(e){
  if(attempt<2){await new Promise(r=>setTimeout(r,250*(attempt+1)));return api(u,attempt+1)}
  say('相机服务暂时断开，正在自动重试…',1); throw e;
 }
}
function show(d){st=Object.assign(st,d);$('exp').max=d.exp_max;$('exp').value=d.exposure;$('ev').textContent=d.exposure+' 行 / '+d.exposure_us+' µs';
$('gain').value=d.gain;$('gv').textContent=d.gain_x+'x';$('vts').value=d.vts;$('vv').textContent=d.vts+' / '+d.nominal_fps+' fps';
$('ae').checked=d.ae;$('target').value=d.target;$('tv').textContent=d.target;$('mode').value=d.mode;$('quality').value=d.quality;$('qv').textContent=d.quality}
let ctrlTimers={},ctrlSerial=0,editingCtrl=false;
async function ctrl(q){try{let serial=++ctrlSerial,d=await api('/ctrl?'+q);if(serial===ctrlSerial)show(d)}catch(e){}}
function ctrlLater(k,v){clearTimeout(ctrlTimers[k]);ctrlTimers[k]=setTimeout(()=>ctrl(k+'='+encodeURIComponent(v)),120)}
for(const k of ['exp','gain','vts','target','quality']){
 $(k).oninput=e=>ctrlLater(k,e.target.value);
 $(k).onpointerdown=()=>editingCtrl=true;
 $(k).onpointerup=()=>editingCtrl=false;
 $(k).onpointercancel=()=>editingCtrl=false;
}
window.onpointerup=()=>editingCtrl=false;
$('ae').onchange=e=>ctrl('ae='+(e.target.checked?1:0));$('mode').onchange=e=>ctrl('mode='+e.target.value);
const meta=()=>'label='+encodeURIComponent($('label').value)+'&note='+encodeURIComponent($('note').value);
async function snap(){try{let d=await api('/snap?'+meta());say('已拍照：'+d.base+'（最大 '+d.max+'，削顶 '+d.saturated_pct+'%）');setTimeout(loadFiles,700)}catch(e){}}
async function recStart(){try{let q='secs='+$('secs').value+'&every='+$('every').value+'&'+meta();if($('roi').value.trim())q+='&roi='+encodeURIComponent($('roi').value.trim());
 rec(await api('/rec/start?'+q))}catch(e){}}
async function recStop(){rec(await api('/rec/stop'))} async function recDiscard(){rec(await api('/rec/discard'))}
let wasBusy=false;
function rec(d){rs=d;let busy=d.state=='recording'||d.state=='saving';
 $('rec').disabled=busy;$('snap').disabled=false;$('stop').disabled=d.state!='recording';$('disc').disabled=d.state!='recording';
 $('recdot').style.display=d.state=='recording'?'block':'none';$('recn').textContent=d.n+'/'+d.nmax;
 $('prog').value=d.state=='recording'?100*d.n/Math.max(d.nmax,1):d.state=='saving'?d.save_pct:0;
 if(d.state=='recording')say('录制中 '+d.n+' / '+d.nmax+' 帧');else if(d.state=='saving')say('正在写入 SD 卡 '+d.save_pct+'%');else if(d.msg)say(d.msg,d.state=='error');
 if(d.limits)$('lim').textContent='最多约 '+d.limits.max_secs+' 秒';
 if(wasBusy&&!busy)loadFiles();wasBusy=busy}
function fmt(t){let d=new Date(t*1000);return d.toLocaleString()}
async function loadFiles(){let L=await api('/files');let tb=$('files');tb.innerHTML='';
 for(const f of L){let has=x=>f.files.includes(f.base+x);let th=has('_thumb.jpg')?f.base+'_thumb.jpg':has('_view.jpg')?f.base+'_view.jpg':null;
  let what=f.kind=='photo'?'照片，最大 '+f.max+'，削顶 '+f.saturated_pct+'%':(f.frames+' 帧，'+f.secs+' s，'+(f.fps_recorded||0).toFixed(1)+' fps，丢帧 '+f.skipped+(f.every>1?'，每 '+f.every+' 帧取 1':'')+(f.roi&&!(f.roi[2]==640&&f.roi[3]==800)?'，ROI '+f.roi.join(','):'')+(f.stopped_early?'（提前停止）':''));
  let s=f.settings||{};what+='<br><span class=note>曝光 '+s.exposure+' 行 '+s.exposure_us+' µs，增益 '+s.gain_x+'x，VTS '+s.vts+'</span>';
  let links='';for(const x of f.files){if(x.endsWith('_thumb.jpg'))continue;links+='<a href="/files/'+x+'?dl=1">'+x.slice(f.base.length)+'</a>'}
  if(has('.mp4'))links='<a href=# onclick="play(\''+f.base+'.mp4\');return false">播放</a>'+links;else if(f.kind=='recording'&&f.preview=='pending')links='<span class=note>预览生成中</span> '+links;
  if(f.kind=='photo')links='<a href="/files/'+f.base+'.png" target=_blank>查看</a>'+links;
  let tr=document.createElement('tr');
  tr.innerHTML='<td>'+(th?'<img src="/files/'+th+'">':'')+'</td><td>'+esc(f.base)+'<br><span class=note>'+fmt(f.t)+'</span></td><td>'+esc(f.label)+'<br><span class=note>'+esc(f.note)+'</span></td><td>'+what+'</td><td>'+f.size_mb+' MB</td><td>'+links+' <a href=# style="color:#f88" onclick="del(\''+f.base+'\');return false">删除</a></td>';
  tb.appendChild(tr)}
 if(rs.limits)$('disk').textContent='SD 卡剩余 '+rs.limits.disk_free_mb+' MB；全幅每帧 '+rs.limits.frame_kb+' KB；当前帧率 '+rs.limits.fps+' fps'}
function play(n){let v=$('player');v.style.display='block';v.src='/files/'+n;v.play()}
async function del(b){if(!confirm('删除 '+b+' 的全部文件？'))return;await api('/files/delete?base='+encodeURIComponent(b));loadFiles()}
let online=true,lastOk=Date.now(),streamAt=0;
function reloadStream(){let i=$('live');i.src='/stream?'+Date.now();streamAt=Date.now()}
$('live').onerror=()=>setTimeout(reloadStream,1500);
function showConn(d){let dot=$('dot'),nc=$('nocam');
 if(!online){dot.className='dot err';$('connmsg').textContent='连不上板子上的服务（192.168.1.254:8080）。检查网线、板子是否上电；也可以在 Mac 上再双击一次启动脚本。';nc.style.display='flex';nc.textContent='服务离线';return}
 let st=d.cam_state;dot.className='dot '+(st=='ok'?'ok':st=='connecting'||st=='starting'?'busy':'err');
 let txt={ok:'相机已连接',connecting:'正在连接相机…',starting:'服务刚启动，正在连接相机…',error:'相机未连接',stopped:'已停流'}[st]||st;
 $('connmsg').textContent=txt+(d.cam_msg?'：'+d.cam_msg:'')+(st=='error'&&d.auto_reconnect?'（自动重连中）':'');
 $('autore').checked=d.auto_reconnect;
 if(st=='ok'){nc.style.display='none'}else{nc.style.display='flex';nc.textContent=txt+(d.cam_msg?'\n'+d.cam_msg:'')}}
async function camStatus(){try{let r=await fetch('/cam/status',{cache:'no-store'});let d=await r.json();
 if(!online){online=true;reloadStream();ctrl('');loadFiles()}
 if(d.cam_state=='ok'&&st.cam_state!='ok'&&Date.now()-streamAt>1000)reloadStream();
 st=Object.assign(st,d);showConn(d);lastOk=Date.now()}catch(e){if(Date.now()-lastOk>2500){online=false;showConn({})}}}
async function camAct(u){$('dot').className='dot busy';$('connmsg').textContent='正在执行…';
 try{let r=await fetch(u);let d=await r.json();say(d.message||d.error||'',!d.ok);showConn(d);if(d.ok)reloadStream()}catch(e){say('请求失败：'+e,1)}}
$('autore').onchange=e=>ctrl('auto_reconnect='+(e.target.checked?1:0));
async function svcRestart(){if(!confirm('重启板子上的服务程序？正在进行的录制会被丢弃。'))return;
 try{await fetch('/service/restart')}catch(e){} online=false;showConn({});$('connmsg').textContent='服务正在重启，几秒后自动恢复…'}
setInterval(camStatus,1000);
ctrl('');rec({state:'idle',n:0,nmax:0});
setInterval(async()=>{if(!online)return;try{rec(await api('/rec/status'))}catch(e){}},300);
setInterval(async()=>{if(!online)return;let s;try{s=await (await fetch('/stats')).json()}catch(e){return}
 $('s').textContent='传感器 '+(s.sensor_fps||0).toFixed(1)+' fps，推流 '+(s.sent_fps||0).toFixed(1)+' fps\n平均亮度 '+s.mean.toFixed(1)+'，削顶 '+s.sat_pct.toFixed(2)+'%\n每帧行数 '+s.lines_per_frame+'，坏包头 '+s.bad_hdr+'，撕帧重取 '+s.torn_retry+(s.user_err?'\nprocess.py 错误：'+s.user_err:'');
 if(!editingCtrl)try{show(await api('/ctrl?'))}catch(e){}},1000);
loadFiles();
</script></body></html>""".encode()

def make_handler(capr):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(body)
        def _err(self, e): self._json({"error": str(e)}, 400)
        def _send_file(self, name):
            p = capr.files.path(name)
            if not p: return self.send_error(404)
            size = os.path.getsize(p); start, end = 0, size - 1
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1): start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else size - 1
                else: start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                self.send_response(206); self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            else:
                self.send_response(200)
            self.send_header("Content-Type", capr.files.ctype(name))
            self.send_header("Content-Length", str(end - start + 1)); self.send_header("Accept-Ranges", "bytes")
            if capr.files.ctype(name) == "application/octet-stream" or "dl" in self.path.split("?", 1)[-1]:
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start); left = end - start + 1
                try:
                    while left > 0:
                        b = f.read(min(1 << 20, left))
                        if not b: break
                        self.wfile.write(b); left -= len(b)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        def do_GET(self):
            u = urlparse(self.path); q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            if u.path == "/":
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(PAGE)
            elif u.path == "/time":
                self.send_response(200); self.send_header("Content-Type", "text/plain"); self.end_headers(); self.wfile.write(("%.6f" % time.time()).encode())
            elif u.path == "/stats":
                d = dict(capr.stats); d.update(capr.state()); self._json(d)
            elif u.path == "/ctrl":
                try: self._json(capr.set(q))
                except (ValueError, OSError) as e: self._err(e)
            elif u.path == "/cam/reconnect":
                ok, msg = capr.connect(reload_bitstream=q.get("reload") in ("1", "true"))
                d = capr.state(); d["ok"] = ok; d["message"] = msg; self._json(d, 200 if ok else 503)
            elif u.path == "/cam/disconnect":
                ok, msg = capr.disconnect(); d = capr.state(); d["ok"] = ok; d["message"] = msg; self._json(d)
            elif u.path == "/cam/status": self._json(capr.state())
            elif u.path == "/service/restart":
                self._json({"ok": True, "message": "服务正在重启，约 5 秒后自动恢复"})
                threading.Thread(target=restart_self, daemon=True).start()
            elif u.path == "/snap":
                try: self._json(capr.rec.snap(q.get("label", ""), q.get("note", "")))
                except (ValueError, OSError) as e: self._err(e)
            elif u.path == "/rec/start":
                try:
                    self._json(capr.rec.start(float(q.get("secs", 5)), int(q.get("every", 1)), parse_roi(q.get("roi", "")),
                                              q.get("label", ""), q.get("note", "")))
                except (ValueError, OSError, MemoryError) as e: self._err(e)
            elif u.path == "/rec/stop": self._json(capr.rec.stop())
            elif u.path == "/rec/discard": self._json(capr.rec.discard())
            elif u.path == "/rec/status": self._json(capr.rec.status())
            elif u.path == "/files": self._json(capr.files.list())
            elif u.path == "/files/delete":
                try: self._json({"deleted": capr.files.delete(q.get("base", ""))})
                except (ValueError, OSError) as e: self._err(e)
            elif u.path.startswith("/files/"):
                self._send_file(u.path[len("/files/"):])
            elif u.path == "/stream":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache"); self.end_headers()
                capr.stats["clients"] += 1
                seq = -1; sent = 0; t = time.time()
                try:
                    while True:
                        with capr.lock:
                            t_wait = time.time()
                            while capr.seq == seq and time.time() - t_wait < 1.0: capr.lock.wait(0.5)
                            if capr.jpeg is None: continue
                            seq, jpg = capr.seq, capr.jpeg
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg))
                        self.wfile.write(jpg); self.wfile.write(b"\r\n")
                        sent += 1
                        if time.time() - t >= 1.0:
                            capr.stats["sent_fps"] = sent / (time.time() - t); sent = 0; t = time.time()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    capr.stats["clients"] -= 1
            elif u.path == "/raw":
                # untouched 640x800 RAW8 (even sensor columns). Header per part:
                # X-Seq, X-Time (board capture time), X-Width, X-Height, X-Exposure, X-Gain
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=raw")
                self.send_header("Cache-Control", "no-cache"); self.end_headers()
                capr.stats["raw_clients"] += 1
                with capr.rawlock: seq = capr.raw_seq      # skip whatever is cached; wait for the next capture
                sent = 0; t = time.time()
                try:
                    while True:
                        with capr.rawlock:
                            while (capr.raw_seq == seq or capr.raw is None) and capr.running: capr.rawlock.wait(1.0)
                            seq, img, ts = capr.raw_seq, capr.raw, capr.raw_t
                        sn = capr.sensor
                        hdr = ("--raw\r\nContent-Type: application/octet-stream\r\nContent-Length: %d\r\n"
                               "X-Seq: %d\r\nX-Time: %.6f\r\nX-Width: %d\r\nX-Height: %d\r\nX-Exposure: %d\r\nX-Gain: %d\r\n\r\n"
                               % (img.size, seq, ts, img.shape[1], img.shape[0], sn.exp, sn.gain)).encode()
                        self.wfile.write(hdr); self.wfile.write(img.tobytes()); self.wfile.write(b"\r\n")
                        sent += 1
                        if time.time() - t >= 1.0:
                            capr.stats["raw_fps"] = sent / (time.time() - t); sent = 0; t = time.time()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    traceback.print_exc()
                finally:
                    capr.stats["raw_clients"] -= 1
            else:
                self.send_error(404)
    return H

def restart_self():
    """Re-exec this server with the same arguments (picks up an updated stream.py)."""
    time.sleep(0.5)
    try:
        if CAPR: CAPR.stop()
    except Exception: pass
    try:
        if SRV: SRV.socket.close()
    except Exception: pass
    os.execv(sys.executable, [sys.executable, "-I"] + sys.argv)

CAPR = None
SRV = None

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--vts", type=int, default=910)
    # Default to the shortest exposure and unity gain.  The previous defaults
    # clipped this scene almost completely white before the web UI could tune
    # them; users can still raise them explicitly with --exposure/--gain or
    # through /ctrl.
    ap.add_argument("--exposure", type=int, default=1)
    ap.add_argument("--gain", type=int, default=16)
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    # bind the port before touching the camera: a second instance must fail here,
    # not after it has already reset the sensor/VDMA under the running one
    srv = Server(("0.0.0.0", a.port), None); SRV = srv
    capr = Capture(a.vts, a.exposure, a.gain, a.quality, a.workers); CAPR = capr
    srv.RequestHandlerClass = make_handler(capr)
    print("serving on :%d (vts %d)" % (a.port, a.vts), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        capr.stop()
