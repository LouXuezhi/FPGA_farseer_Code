# 10 Overexposed images during overnight capture: step-by-step elimination, and admitting a careless judgment

[中文](10_overexposure_root_cause.md) | English

Session: The image looks "noisy" while automatic capture is running; investigating the cause　Date: 2026-11-01

## Prompt (condensed)
> Why does the image look a bit noisy now? (Later) Pause the capture first and find where the overexposure starts. The temperature looks normal, so why is it overexposed? Analyze whether it gets worse gradually over time, or turns like this as soon as it reaches 50 °C.

## Model answer (summary)
- The assistant first ruled out the transport: frame rate about 144 fps, 0 bad packet headers, 0 torn frames. Mean brightness was 254 (previously about 106), with 98.8% clipped, so this was overexposure, not image corruption. It did not change the exposure before the user confirmed, to avoid affecting data being saved.
- After pausing AUTO, turning off heating and stopping the scheduled re-measurements, it scanned all saved RAW: mean about 114 at 22:29, about 156 at 01:30, about 190 at 02:50, about 244 at 03:42, and above 250 after 04:12.
- Statistics over 2254 frames, 129 files, about 13.8 h: at the same 50 °C, the mean was about 139 at 00:58 and about 252 at 04:54. It did not recover when the target later went back to 40 °C. This rejects "it turns all white as soon as it reaches 50 °C".
- Following the user's plan, only one part was restarted at a time: after bitstream reload + camera reset, still 254.6/99.4%; after a full board power cycle, still 254.6/99.3%; after restarting the laser, 249.4/92.2%; with the laser off, fresh frames had a mean of 18.8.

## Self-correction
- The assistant had earlier taken the "exposure 100 lines, gain 6×" shown on the web page as the actual sensor state. It later explicitly corrected itself: "I previously took the parameters in the camera status as the actual exposure state; that judgment was not rigorous enough", and "the data shows that brightness worsened over time; it has not yet shown that the exposure register went out of control".
- The assistant admitted that the earlier report of "23/45 groups completed" only checked temperature control stability and file existence, not image quality, so it does not mean 23 groups are usable.
- During the laser-off test, it found that a new connection to the RAW interface first sends a cached frame, and that cache is not updated when there is no client. The assistant pointed out that the earlier sampling method of "short connection, take only the first frame" and some of the judgments after partial restarts may have used stale images. It then switched to discarding the first 5 frames and taking 81 consecutive frames before judging.
- In an earlier investigation of a board hang, the assistant first guessed that 0x70000000, written by the VDMA, was not reserved by the kernel. After checking the device tree and finding it inside the reserved region `buffer@0`, it withdrew this guess on its own.

## Result
- Localization: the saturation is related to the incident laser / optical path, and the camera parameters did not change. The user observed that laser scattering had become clearly larger, which agrees with the data.
- RAW from the second half of 45 °C and from 50 °C was judged unusable; exposure is to be corrected before re-measuring.
- This led to changes in the capture procedure: lock exposure per session and log brightness deviation events (see 11).
