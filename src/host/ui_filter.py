"""Display-side stabilisation for the PS screen (fsf_ps_ui.py). Python 3.6, numpy 1.13.

Only what is drawn is filtered. The node's temperatures, alarms and MQTT messages are
untouched, and an alarm is never delayed on screen: filters may hold a state longer,
never start it later.

  SpikeFilter  one-sample outliers (a jump that the next sample does not confirm)
  Eased        numbers glide to the new value between node steps (~2 s apart)
  Quantiser    shown text changes only once the value is clearly past the next digit
  Debounce     a boolean must hold for t_on / t_off seconds before the shown state flips
  Status       per-sensor normal / rising / alarm with hysteresis on the rate
  StickyRange  chart axis grows at once, shrinks only after the data stayed inside for a while
"""
import math

import numpy as np


class SpikeFilter:
    """Per channel: a sample more than `jump` away from the linear prediction
    (last + last slope) is held back one sample. The next sample decides: if it lands on the
    same side (at least half as far), the step was real and both are taken; otherwise the
    sample is dropped as a lone outlier. Steady ramps pass with no delay; a sudden step is
    shown one sample late. bypass[k] (node alarm on that channel) skips the hold."""

    def __init__(self, n_ch, jump=0.4):
        self.jump = jump
        self.h = [[] for _ in range(n_ch)]              # last two accepted values
        self.pending = [None] * n_ch
        self.dropped = 0

    def __call__(self, vals, bypass=None):
        out = []
        for k, v in enumerate(vals):
            h = self.h[k]
            if v is None:
                out.append(h[-1] if h else None)
                continue
            pred = h[-1] + (h[-1] - h[-2]) if len(h) == 2 else (h[-1] if h else v)
            p = self.pending[k]
            if p is not None:
                self.pending[k] = None
                if (p - pred) * (v - pred) > 0 and abs(v - pred) >= 0.5 * abs(p - pred):
                    h[:] = [p, v]                       # confirmed step
                    out.append(v)
                    continue
                self.dropped += 1                       # lone outlier: forget it
            if h and abs(v - pred) > self.jump and not (bypass and bypass[k]):
                self.pending[k] = v
                out.append(h[-1])
                continue
            h.append(v)
            del h[:-2]
            out.append(v)
        return out


class Eased:
    """First-order glide towards the target: x += (target - x) * (1 - exp(-dt / tau)).
    A jump larger than `snap` (re-calibration) is applied at once."""

    def __init__(self, tau=0.8, snap=5.0):
        self.tau, self.snap = tau, snap
        self.x, self.t = None, None

    def __call__(self, target, now):
        if target is None:
            return self.x
        if self.x is None or abs(target - self.x) > self.snap:
            self.x, self.t = float(target), now
            return self.x
        dt = max(0.0, now - self.t)
        self.t = now
        self.x += (target - self.x) * (1 - math.exp(-dt / self.tau))
        return self.x

    def moving(self, target, eps):
        return target is not None and self.x is not None and abs(target - self.x) > eps


class Quantiser:
    """Rounds to `dec` decimals, but keeps the shown value until the input moves more than
    (0.5 + hyst) LSB away from it, so 36.95 +- noise does not toggle 36.9 / 37.0."""

    def __init__(self, dec, hyst=0.4):
        self.lsb = 10.0 ** -dec
        self.dec, self.hyst = dec, hyst
        self.shown = None

    def __call__(self, v):
        if v is None:
            return None
        if self.shown is None or abs(v - self.shown) > (0.5 + self.hyst) * self.lsb:
            self.shown = round(v / self.lsb) * self.lsb
        return self.shown

    def fmt(self, v, sign=False):
        q = self(v)
        if q is None:
            return None
        return ("%+.*f" if sign else "%.*f") % (self.dec, q)


class Debounce:
    """Shown boolean flips to True after the input held True for t_on s, back after t_off s."""

    def __init__(self, t_on=0.0, t_off=3.0, init=False):
        self.t_on, self.t_off = t_on, t_off
        self.shown, self.since, self.last_in = init, None, init

    def __call__(self, v, now):
        v = bool(v)
        if v != self.last_in:
            self.last_in, self.since = v, now
        if v != self.shown:
            if self.since is None:
                self.since = now
            if now - self.since >= (self.t_on if v else self.t_off):
                self.shown = v
        return self.shown


class Status:
    """'a' alarm / 'w' rising / 'n' normal / '-' no value, per sensor.
    alarm: shown as soon as the node flags it, cleared hold_s after the last flag.
    rising: rate > on for on_s, cleared once rate < off for off_s."""

    def __init__(self, on=1.0, off=0.6, on_s=4.0, off_s=8.0, hold_s=15.0):
        self.on, self.off, self.on_s, self.off_s, self.hold_s = on, off, on_s, off_s, hold_s
        self.last_alarm = None
        self.rising = False
        self.t_cross = None

    def __call__(self, has_value, alarm, rate, now):
        if alarm:
            self.last_alarm = now
        if not has_value:
            return "-"
        if self.last_alarm is not None and now - self.last_alarm <= self.hold_s:
            self.rising, self.t_cross = False, None
            return "a"
        if rate is not None:
            want = rate > self.on if not self.rising else not (rate < self.off)
            if want != self.rising:
                if self.t_cross is None:
                    self.t_cross = now
                if now - self.t_cross >= (self.on_s if want else self.off_s):
                    self.rising, self.t_cross = want, None
            else:
                self.t_cross = None
        return "w" if self.rising else "n"


class StickyRange:
    """Axis (lo, hi) on a `step` grid, at least `span` wide. Grows at once to fit new data;
    shrinks only after a tighter range fitted the data for shrink_s."""

    def __init__(self, step=1.0, span=4.0, pad=0.4, shrink_s=30.0):
        self.step, self.span, self.pad, self.shrink_s = step, span, pad, shrink_s
        self.lo = self.hi = None
        self.t_fit = None

    def _snap(self, vmin, vmax):
        s = self.step
        lo, hi = math.floor((vmin - self.pad) / s) * s, math.ceil((vmax + self.pad) / s) * s
        n = int(math.ceil(self.span / s - 1e-9))
        if (hi - lo) / s < n - 1e-9:
            extra = n - int(round((hi - lo) / s))
            lo -= (extra // 2) * s
            hi = lo + n * s if hi < lo + n * s else hi
        return lo, hi

    def __call__(self, vmin, vmax, now, force=None):
        """force: a value the range must include (e.g. the alarm line)."""
        if vmin is None:
            return (self.lo, self.hi) if self.lo is not None else (35.0, 40.0)
        if force is not None:
            vmin, vmax = min(vmin, force), max(vmax, force)
        lo, hi = self._snap(vmin, vmax)
        if self.lo is None:
            self.lo, self.hi = lo, hi
        elif vmin < self.lo or vmax > self.hi:          # data outside: grow now, keep the other side
            self.lo, self.hi = min(self.lo, lo), max(self.hi, hi)
            self.t_fit = None
        elif lo > self.lo or hi < self.hi:              # tighter range fits: shrink after a while
            if self.t_fit is None:
                self.t_fit = now
            elif now - self.t_fit >= self.shrink_s:
                self.lo, self.hi, self.t_fit = lo, hi, None
        else:
            self.t_fit = None
        return self.lo, self.hi
