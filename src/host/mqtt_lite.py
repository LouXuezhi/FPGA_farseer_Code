"""Minimal MQTT 3.1.1 client, standard library only (board: Python 3.6, no pip).

Supports what the node needs: CONNECT (clean session, keepalive, will, user/pass),
PUBLISH QoS 0/1 (retain), SUBSCRIBE QoS 0, PINGREQ, automatic reconnect.
Publishing never blocks the caller: messages go to a bounded queue drained by a
background thread; when the broker is unreachable the oldest QoS 0 messages are
dropped first, so inference and display keep running.

    c = MqttClient("node1", "192.168.1.253", will=("fsf/node1/status", b"offline", True))
    c.on_message = lambda topic, payload: ...
    c.start(); c.subscribe("fsf/node1/cmd/#"); c.publish("fsf/node1/state", b"{...}")
"""
import collections
import socket
import struct
import threading
import time

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK = 1, 2, 3, 4, 8, 9
PINGREQ, PINGRESP, DISCONNECT = 12, 13, 14


def _str(s):
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def _remaining_length(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def packet(ptype, flags, body):
    return bytes([(ptype << 4) | flags]) + _remaining_length(len(body)) + body


def connect_packet(client_id, keepalive, will=None, user=None, password=None):
    flags = 0x02                                              # clean session
    payload = _str(client_id)
    if will:
        topic, msg, retain = will
        flags |= 0x04 | (0x20 if retain else 0)               # will flag, will QoS 0
        payload += _str(topic) + _str(msg)
    if user is not None:
        flags |= 0x80
        payload += _str(user)
    if password is not None:
        flags |= 0x40
        payload += _str(password)
    return packet(CONNECT, 0, _str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive) + payload)


def publish_packet(topic, payload, qos=0, retain=False, pid=0):
    body = _str(topic) + (struct.pack("!H", pid) if qos else b"") + payload
    return packet(PUBLISH, (qos << 1) | (1 if retain else 0), body)


def read_packet(sock):
    """-> (type, flags, body); raises ConnectionError on EOF."""
    def recv(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("broker closed the connection")
            buf += chunk
        return buf
    h = recv(1)[0]
    n, mult = 0, 1
    while True:
        b = recv(1)[0]
        n += (b & 0x7F) * mult
        mult *= 128
        if not b & 0x80:
            break
    return h >> 4, h & 0x0F, recv(n) if n else b""


class MqttClient:
    def __init__(self, client_id, host, port=1883, keepalive=30, will=None, user=None, password=None,
                 queue_len=256, log=print):
        self.client_id, self.host, self.port, self.keepalive = client_id, host, port, keepalive
        self.will, self.user, self.password, self.log = will, user, password, log
        self.on_message = None
        self.connected = False
        self.stats = {"sent": 0, "dropped": 0, "reconnects": 0}
        self._q = collections.deque()
        self._qlen = queue_len
        self._cv = threading.Condition()
        self._subs = []
        self._pid = 0
        self._unacked = {}                                    # pid -> packet (QoS 1, resent on reconnect)
        self._sock = None
        self._stop = False

    # ---------------- public API (thread safe, never blocks on the network) ----------------
    def start(self):
        threading.Thread(target=self._run, name="mqtt", daemon=True).start()
        return self

    def publish(self, topic, payload, qos=0, retain=False):
        if isinstance(payload, str):
            payload = payload.encode()
        with self._cv:
            if len(self._q) >= self._qlen:
                victim = next((m for m in self._q if m[2] == 0), self._q[0])   # oldest QoS 0 first
                self._q.remove(victim)
                self.stats["dropped"] += 1
            self._q.append((topic, payload, qos, retain))
            self._cv.notify()

    def subscribe(self, topic):
        with self._cv:
            self._subs.append(topic)
            if self.connected:
                self._send_subscribe(topic)

    def stop(self, timeout=2.0):
        """Flush the queue (up to timeout), send DISCONNECT, stop the thread."""
        end = time.time() + timeout
        while self._q and self.connected and time.time() < end:
            time.sleep(0.02)
        self._stop = True
        with self._cv:
            self._cv.notify()
        if self._sock:
            try:
                self._sock.sendall(packet(DISCONNECT, 0, b""))
                self._sock.close()
            except OSError:
                pass

    # ---------------- internals ----------------
    def _next_pid(self):
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _send_subscribe(self, topic):
        self._sock.sendall(packet(SUBSCRIBE, 2, struct.pack("!H", self._next_pid()) + _str(topic) + b"\x00"))

    def _connect(self):
        s = socket.create_connection((self.host, self.port), timeout=5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(connect_packet(self.client_id, self.keepalive, self.will, self.user, self.password))
        ptype, _, body = read_packet(s)
        if ptype != CONNACK or len(body) < 2 or body[1] != 0:
            s.close()
            raise ConnectionError("CONNACK refused: %r" % (body,))
        s.settimeout(None)
        self._sock = s
        for topic in self._subs:
            self._send_subscribe(topic)
        for pkt in self._unacked.values():                   # QoS 1 redelivery (DUP flag)
            s.sendall(bytes([pkt[0] | 0x08]) + pkt[1:])
        self.connected = True
        threading.Thread(target=self._reader, args=(s,), name="mqtt-rx", daemon=True).start()

    def _reader(self, s):
        try:
            while True:
                ptype, flags, body = read_packet(s)
                if ptype == PUBLISH:
                    n = struct.unpack("!H", body[:2])[0]
                    topic, rest = body[2:2 + n].decode(), body[2 + n:]
                    if (flags >> 1) & 3:
                        s.sendall(packet(PUBACK, 0, rest[:2]))
                        rest = rest[2:]
                    if self.on_message:
                        try:
                            self.on_message(topic, rest)
                        except Exception as e:           # a bad handler must not kill the link
                            self.log("mqtt: on_message error: %r" % e)
                elif ptype == PUBACK:
                    self._unacked.pop(struct.unpack("!H", body[:2])[0], None)
        except (OSError, ConnectionError):
            pass
        self.connected = False
        with self._cv:
            self._cv.notify()

    def _run(self):
        backoff = 0.5
        while not self._stop:
            if not self.connected:
                try:
                    self._connect()
                    backoff = 0.5
                    self.log("mqtt: connected to %s:%d" % (self.host, self.port))
                except (OSError, ConnectionError) as e:
                    self.log("mqtt: connect failed (%s), retry in %.1fs" % (e, backoff))
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    self.stats["reconnects"] += 1
                    continue
            last_tx = time.time()
            while self.connected and not self._stop:
                with self._cv:
                    if not self._q:
                        self._cv.wait(timeout=max(0.1, self.keepalive / 2 - (time.time() - last_tx)))
                    if not self.connected:                # reader saw EOF: keep the message queued
                        break
                    msg = self._q.popleft() if self._q else None
                try:
                    if msg:
                        topic, payload, qos, retain = msg
                        pid = self._next_pid() if qos else 0
                        pkt = publish_packet(topic, payload, qos, retain, pid)
                        if qos:
                            self._unacked[pid] = pkt
                        self._sock.sendall(pkt)
                        self.stats["sent"] += 1
                        last_tx = time.time()
                    elif time.time() - last_tx > self.keepalive / 2:
                        self._sock.sendall(packet(PINGREQ, 0, b""))
                        last_tx = time.time()
                except OSError:
                    self.connected = False
                    if msg:
                        self._unacked.pop(pid, None)         # resent from the queue instead
                        with self._cv:
                            self._q.appendleft(msg)          # retry after reconnect
            if self._sock and not self.connected:
                try:
                    self._sock.close()
                except OSError:
                    pass
