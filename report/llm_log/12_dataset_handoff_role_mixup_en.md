# 12 Dataset handoff scripts: misunderstanding the task, and an unusable bad-pixel rule

[中文](12_dataset_handoff_role_mixup.md) | English

Session: Writing and iterating on development scripts according to the dataset handoff specification　Date: 2026-11-01

## Prompt (condensed)
> This is our dataset handoff specification. Write the development scripts based on a thorough study of the current state and the intent, and keep iterating.
> (Later) What is your task? Is it to write the training script?

## Model answer (summary)
- The assistant wrote `fz3b_data.py`: index building, `load_index`, `FrameStore`, `preprocess`, `make_pairs`. It depends only on numpy and scans the data disk in about 13 s.
- Checked against real data: rerun has 12 sessions / 464 frames and legacy has 2254 frames, consistent with the specification. Interpolated temperatures differ from the nearest reading by at most 0.18 °C. Two hold-segment frames 1 s apart have a correlation of 0.98 after preprocessing (the specification gives 0.99). Preprocessing takes about 2 ms per frame.
- Offline end-to-end test: a fake serial port + fake camera run the capture script, and `fz3b_data.py` reads the results back. On overshoot it stops and turns off heating, and no frame pair spans a camera restart.

## Self-correction
- Bad-pixel rule: the first index build reported 1840 bad pixels, which the assistant judged to be "far too many". It found that under the rule in specification v1, the measured deviation was 0 and the rule was unusable. It was changed to "sample frames at least 30 s apart; a pixel is bad only if it deviates from its neighborhood by 5σ in at least half of the frames", which gave 51, and was written into specification v1.1.
- False alarms in the interval check: when sampling switched from 20 s to 1 s, a 10-frame hold segment was misjudged as `cam_gap`. After the fix, all 464 frames were valid.
- Conflict with another session: the assistant found that `ramp_collect.py` had been overwritten by a 199-line implementation from another session. It first confirmed that the other session was editing it, then negotiated a division of work (the other session owns the capture script, this session owns `fz3b_data.py`). The other session pointed out that the safety check written by the assistant would immediately trigger a false stop on cooling steps (such as 42→39). The assistant accepted this and dropped its own version.
- The user asked "What is your task?". The assistant admitted directly: "No. I misunderstood the task and wrote it from the data side." The specification opens with "for the designer of the training script", and the training side is responsible for the model, loss, training loop and evaluation, which had not been started. The `fz3b_data.py` already written is the layer that the training script will read, so it can be kept.

## Result
- The index is built on the data disk at `FZ3B_dataset/index`: 2718 frames, of which 464 are usable, forming 294 frame pairs.
- New rules: all frames after a temperature stream drop are marked unusable (the fake drop test marked exactly the 16 frames after it); sessions still being recorded are skipped when building the index.
- The specification was raised to v1.1, with an added note: the temperature sensor resolution is 0.0625 °C, so the "<0.05 °C" bin actually means "the reading did not change".
