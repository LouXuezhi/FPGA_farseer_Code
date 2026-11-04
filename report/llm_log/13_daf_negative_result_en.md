# 13 Digital aperture filtering (DAF) preprocessing: a small-batch trial gives a negative result

[中文](13_daf_negative_result.md) | English

Session: Trying DAF preprocessing + CNN on existing captured data on the remote training machine, to see whether temperature regression improves　Date: 2026-11-02

## Prompt (condensed)
> There is captured data on the remote machine. Try digital aperture filtering (DAF) preprocessing first, then look at the results with the CNN. Try a small batch to see whether it has any effect.

## Model answer (summary)
- The assistant first confirmed whether the camera captures a near-field or far-field spot, because DAF is implemented differently in the two cases.
- Two kinds of DAF were implemented: a frequency-domain pupil (square root of intensity as an approximation of amplitude, keeping 0.7 or 0.5 times the original pupil radius after the FFT) and a spatial aperture (keeping only a central circular region).
- Training used 6 ramp sessions and validation used 2 fixed sessions, with training settings matching the current pipeline defaults (2 epochs).
- Two additional controls were set up: blank (all image values set to 0) and an all-zero prediction.
- The experiment scripts were placed in a separate new directory, without changing `pipeline.py` or the model repository, which another session was modifying.

## Self-correction
No notable correction. The value of this segment is that the assistant added an "all-zero image" control on its own initiative, and used it to move the question from "does DAF help" to "does the CNN learn anything from the image":

| Variant | Reference point + residual | Image only |
|---|---|---|
| No filtering | 0.0584 | 0.0808 |
| Frequency-domain pupil 0.7 | 0.0585 | 0.0811 |
| Frequency-domain pupil 0.5 | 0.0584 | 0.0807 |
| Spatial aperture 0.6 | 0.0582 | 0.0807 |
| blank (all-zero image) | 0.0585 | 0.0811 |

(Metric: MAE on denoised labels of the validation set)

- The assistant also stated that the evidence is limited: each variant was run with only 1 to 2 seeds, the full paper is paywalled and only the abstract was available, and the aperture parameters were chosen from first principles. The conclusion was therefore written as "no clear effect", not "definitely ineffective".

## Result
- The differences between the DAF variants and no filtering are within ±0.0003, on par with the all-zero image control.
- A check without training: the rank correlation between frame-pair decorrelation 1−corr and |ΔT| did not increase after DAF (image-only group 0.396 → 0.386–0.391).
- Interpretation: the paper targets cases where higher-order modes make the speckle decorrelate too fast. In this data, frame pairs 3–10 s apart decorrelate by only 0.2–0.3, so the bottleneck is not there.
- The follow-up direction shifted to first finding out "why the image contributes nothing to ΔT", which led to the laser slow drift study (see 14).
