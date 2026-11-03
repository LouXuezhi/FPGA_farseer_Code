"""Host HMI for the FZ3B speckle node: MQTT receiver + control page (standard library only).

    python -I tools/hmi/fsf_hmi.py            # http://127.0.0.1:8890
        [--broker 127.0.0.1] [--node fz3b] [--acq http://127.0.0.1:8877] [--port 8890]

Subscribes to fsf/<node>/{status,state,alarm} and keeps the last 15 min of
states. The reference temperatures come from the acquisition page's
/api/telemetry (the STM32 is on its serial port, so this never opens it).
The page shows, per feedback sensor, the FPGA dT-tracked temperature next to
the thermocouple reference, the error, and alarms; "标定" sends the current
reference temperatures to fsf/<node>/cmd/calibrate (all 9, or one).
"""
import argparse
import collections
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import ProxyHandler, build_opener

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "host"))
sys.path.insert(0, HERE)                             # copy run from /tmp keeps mqtt_lite.py beside it
from mqtt_lite import MqttClient  # noqa: E402

SENSORS = [7, 16, 1, 12, 2, 9, 4, 3, 10]           # dT order (FZ3B 数据集交接规范)
HEATERS = [7, 5, 6, 3, 9, 8, 4, 13, 10]
HISTORY_S = 900
NO_PROXY = build_opener(ProxyHandler({}))           # the acquisition page is local: bypass any proxy


class Hub:
    def __init__(self, a):
        self.a, self.lock = a, threading.Lock()
        self.status, self.last_state, self.alarm_log = "unknown", None, collections.deque(maxlen=50)
        self.hist = collections.deque()                 # (t, T[9] fpga, ref[9])
        self.calibrations = collections.deque(maxlen=20)
        self.log = collections.deque(maxlen=200)
        self.ref, self.ref_t, self.ref_err = None, 0.0, ""
        base = "fsf/%s/" % a.node
        self.topic = dict(status=base + "status", state=base + "state", alarm=base + "alarm",
                          cal=base + "cmd/calibrate")
        self.mqtt = MqttClient("fsf-hmi-%d" % os.getpid(), a.broker, a.mqtt_port, log=self.addlog)
        self.mqtt.on_message = self.on_message
        self.mqtt.start()
        for t in ("status", "state", "alarm"):
            self.mqtt.subscribe(self.topic[t])
        threading.Thread(target=self.poll_ref, daemon=True).start()

    def addlog(self, s):
        with self.lock:
            self.log.append(time.strftime("%H:%M:%S ") + s)

    # ---------------- inputs ----------------
    def poll_ref(self):
        while True:
            try:
                d = json.load(NO_PROXY.open(self.a.acq + "/api/telemetry", timeout=3))
                temps = d.get("temp") or []
                ref = [temps[s - 1] if s - 1 < len(temps) else None for s in SENSORS]
                with self.lock:
                    self.ref, self.ref_t, self.ref_err = ref, time.time(), "" if not d.get("error") else d["error"]
            except Exception as e:
                with self.lock:
                    self.ref_err = "参考温度读取失败: %s" % e
            time.sleep(1.0)

    def on_message(self, topic, payload):
        now = time.time()
        if topic == self.topic["status"]:
            with self.lock:
                self.status = payload.decode(errors="replace")
            self.addlog("节点状态 " + self.status)
        elif topic == self.topic["state"]:
            try:
                s = json.loads(payload.decode())
            except ValueError:
                return
            with self.lock:
                self.last_state = dict(s, rx=now)
                self.hist.append((now, s.get("T"), list(self.ref) if self.ref else None))
                while self.hist and self.hist[0][0] < now - HISTORY_S:
                    self.hist.popleft()
        elif topic == self.topic["alarm"]:
            try:
                s = json.loads(payload.decode())
            except ValueError:
                return
            on = [SENSORS[k] for k, v in enumerate(s.get("alarm", [])) if v]
            with self.lock:
                self.alarm_log.appendleft(dict(t=now, on=on))
            self.addlog("报警 " + (", ".join("S%d" % x for x in on) if on else "全部解除"))

    # ---------------- actions ----------------
    def calibrate(self, which=None):
        with self.lock:
            ref, age = self.ref, time.time() - self.ref_t
        if not ref or age > 5:
            return dict(ok=False, msg="没有新鲜的参考温度（8877 采集页 /api/telemetry）")
        T = [ref[k] if (which is None or k == which) else None for k in range(9)]
        if all(v is None for v in T):
            return dict(ok=False, msg="所选通道没有参考读数")
        self.mqtt.publish(self.topic["cal"], json.dumps({"T": T, "source": "hmi", "t": time.time()}), qos=1)
        with self.lock:
            self.calibrations.appendleft(dict(t=time.time(), which=which, T=T))
        self.addlog("发送标定 " + ("全部" if which is None else "S%d" % SENSORS[which]))
        return dict(ok=True, msg="已发送", T=T)

    def snapshot(self):
        with self.lock:
            st = self.last_state
            return dict(
                now=time.time(), broker=self.a.broker, connected=self.mqtt.connected, status=self.status,
                state=st, state_age=(time.time() - st["rx"]) if st else None,
                ref=self.ref, ref_age=(time.time() - self.ref_t) if self.ref_t else None, ref_err=self.ref_err,
                sensors=SENSORS, heaters=HEATERS,
                hist=[(round(t, 2), T, R) for t, T, R in self.hist],
                alarms=list(self.alarm_log), calibrations=list(self.calibrations), log=list(self.log)[-40:])


def PAGE():                                         # re-read per request: edits show on reload
    with open(os.path.join(HERE, "hmi.html"), encoding="utf-8") as f:
        return f.read()


class Handler(BaseHTTPRequestHandler):
    hub = None

    def log_message(self, *_):
        pass

    def send(self, code, body, ctype):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            self.send(200, PAGE(), "text/html; charset=utf-8")
        elif self.path == "/api/snapshot":
            self.send(200, json.dumps(self.hub.snapshot()), "application/json")
        else:
            self.send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path.startswith("/api/calibrate"):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            k = body.get("index")
            r = self.hub.calibrate(None if k is None else int(k))
            self.send(200, json.dumps(r), "application/json")
        else:
            self.send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--node", default="fz3b")
    ap.add_argument("--acq", default="http://127.0.0.1:8877")
    ap.add_argument("--port", type=int, default=8890)
    a = ap.parse_args()
    Handler.hub = Hub(a)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("FSF HMI on http://127.0.0.1:%d  (broker %s, node %s)" % (a.port, a.broker, a.node), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
