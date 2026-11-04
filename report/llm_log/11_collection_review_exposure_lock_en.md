# 11 Review of two capture runs: the user questions whether "locked exposure will drift", and the plan changes to locking per session

[中文](11_collection_review_exposure_lock.md) | English

Session: Reviewing the 11-01 overnight and 11-02 daytime capture runs and proposing improvements for the next round　Date: 2026-11-03

## Prompt (condensed)
> Look at the two capture runs so far. What can be improved?
> (Later) What does exposure locking mean? Won't it drift sometimes?

## Model answer (summary)
- Reliability is fine: all 18 sessions completed, no over-temperature, 0 serial reconnects, 0 camera stream drops. Of about 49,000 frames, only 12 `cam_gap` frames and 1 overexposed frame were invalid.
- After the daytime run switched to an independent random walk per channel, the mean correlation of ΔT across the 9 channels dropped from 0.97–0.98 to 0.64–0.86. This change worked.
- The biggest problem: the speckle decorrelates even without heating. With a 10 s interval, the baseline segments are about 0.80 and the fast heating/cooling segments are 0.60–0.70, a difference of only about 0.15.
- Auto exposure was on the whole time. During the day, exposure rose from 58 to 242, and within a single session the correlation between exposure and time was 0.57–0.75. This introduces a confounder strongly correlated with time.
- Too few no-heating controls: each session has only a 60 s baseline. Fixed std steps take up 7/18. Always using the two earliest sessions as the validation set mixes in background drift.

## Self-correction
- The assistant's first suggestion was "lock the exposure (ae=false, fixed at one value, for example around 60)". The user questioned this because the light source drifts.
- The assistant accepted that the concern was valid: during the day the optical power dropped by about 4 times, so locking a single value for the whole day would make the afternoon images much darker. The plan was changed to locking per session: let AE converge before the session starts, freeze exposure and gain when the baseline starts, and restore them after the session ends.
- Two safeguards were added at the same time: lower the target brightness to about 90 at freeze time to leave headroom, and log an event when brightness deviates from the locked value by more than ±30%. The assistant also noted that with exposure locked, the brightness curve directly reflects the real changes in optical power, which AE had previously compensated away.
- The training-side session requested "take 5 consecutive frames every second and save the median". The assistant first measured whether the link could do it, then wrote the code. The offline tests originally changed the exposure settings of the real camera, so the assistant stubbed out these calls to isolate them.

## Result
- All improvements were adopted by the training side and written into handoff specification v1.3: lock exposure per session, pure control sessions, long plateaus, 1 std per round, relaxed upper limit, and validation sets interleaved in time.
- `ramp_collect.py` added exposure locking, a 5-frame median per second, and brightness deviation events. All 4 offline tests passed.
- A 90 s no-heating trial on the real device: exposure fixed at 202, frame interval 1.00 s, 5 frames taken within 0.5 s, and AE restored at the end.
