# 14 Laser slow drift study: all de-drifting methods fail, switching to time-averaged change detection

[中文](14_laser_drift_study.md) | English

Session: Handling slow laser drift without changing the hardware　Date: 2026-11-03

## Prompt (condensed)
> Start a study on how to handle the slow drift caused by the laser without changing the hardware.
> (Later) Does the deployment use a sliding window? The laser is 450 nm. Can you do a sensitivity ablation, and does it need retraining?
> (Later) So is it an alarm now? Not absolute temperature detection?

## Model answer (summary)
- The drift is high-dimensional: 20 principal components are needed to explain 80% of the variance. The state does not repeat: a few hours apart, even the most similar frame has a correlation of only 0.08.
- Tried and ineffective: learning a drift subspace with PCA and subtracting it (drift energy in another session dropped by only 2–3%), learning the subspace from the baseline at the start of a session, regressing the pattern on brightness, choosing reference frames by brightness, and block-wise local detection.
- The approach that works: 60 s averaged patterns, comparing 1−corr against the averaged pattern 120 s apart, with the threshold set at the 99th percentile of the no-heating controls.
- The ablation does not need retraining. The detector has no trainable parameters, so each parameter set only needs its threshold recomputed once, and everything ran in about 4 minutes.
- On absolute temperature, the assistant gave a direct negative answer: when the same channel was heated to the same temperature twice, the two steady-state patterns had a correlation of −0.01, so patterns and temperatures cannot be mapped one to one.

## Self-correction
- A deployment simulation (evaluate every 10 s, alarm after 3 consecutive exceedances, 300 s cooldown) showed 3 false alarms on the 01:33 control. The assistant did not adjust the threshold to hide them. It pointed out that this was the first session of the night and the laser might not have warmed up enough, stated that this was only a guess, and said another control recorded right after power-on was needed to confirm it.
- During replay it found that the alarm interval was almost exactly 305 s. The assistant used this to check the steady-state segments and found that when heating was on but the probe reading had stabilized, 30–58% of windows still exceeded the threshold (only 0.5% without heating). The assistant corrected its description of what the detector means: "it answers whether there is local heating right now, not whether the probe reading is changing", and noted that in the defense it should not be described as "alarms only at the moment of heating".
- The user proposed using long and short windows or a multi-head CNN to output actual degrees. The assistant tested this with data and objected: accumulating rates into a total temperature rise gave estimates 3–4 times larger than actual, with a rank correlation of 0.33, so it did not recommend changing the network structure and re-synthesizing.

## Result
| Temperature rise (single channel within 2 minutes) | Detection rate |
|---|---|
| ≥ 2 °C | 100% |
| 1–2 °C | 93% |
| 0.5–1 °C | 66% |

- The rank correlation between decorrelation and temperature change rose from 0.16–0.28 with frame-by-frame comparison to 0.69–0.79.
- All 36 heating steps (≥ 2 °C) were detected, with a median latency of 111 s.
- The three rate levels (none/slow/fast) were classified correctly 82% of the time on sessions that had not finished recording at fitting time, with 0 confusions between "none" and "fast".
- The report wording was changed to "minute-scale local temperature rise detection and alarm"; absolute temperature across hours is described as future work requiring a frequency-stabilized light source.
