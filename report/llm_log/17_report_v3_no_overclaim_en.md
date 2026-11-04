# 17 Report version 3: only conclusions the data supports

[中文](17_report_v3_no_overclaim.md) | English

Session: Refining the project report according to the AMD contest requirements and template　Date: 2026-11-03 to 11-04

## Prompt (condensed)
> Keep refining the report according to the AMD contest requirements and the template in ref. Keep the language concise and do not use coined terms; leave room for improvements before the defense; use 2 to 3 color families in the figures.
> (Later) We now use CNN + sliding window, so the report only covers alarms, not per-channel absolute temperature. Present CNN + sliding window as the method of this design, compare it with other methods, and write the main text with figures and text.

## Model answer (summary)
- Comparing against the template and AMD 3.3.5.3, the assistant listed five items missing from the current draft: the rationale for the hardware/software partition, a before/after optimization comparison table, the LLM collaboration log, the process of distilling the skill pack, and reproduction instructions.
- It pointed out that numbers in the current draft were out of date: LUT 22.4%, DSP 30.8%, WNS +3.98 ns. The post-route report of the submission directory gives LUT 28.7%, DSP 30.0%, WNS +3.039 ns, and the latter should be used.
- Method: pairs of 60 s window-averaged patterns are fed into the QCNN and fused with 1−corr; an alarm is raised after 3 consecutive exceedances, with a 300 s cooldown. 5 folds, each holding out 1 long plateau segment and 1 control segment, with the threshold set only from the controls in the training folds.
- The user asked why the FPGA is slower than a PC CPU. The assistant explained: the network is only a few MMAC, and a CPU at 4–5 GHz with vector instructions finishes it in 0.22 ms. The inference core runs at 100 MHz with 104 DSPs, and inter-layer pipelining raises throughput without shortening single-frame latency. The on-board 1.33 ms also includes the overhead of calling DMA from Python.

## Self-correction
No notable correction. The value of this segment is that the assistant limited how conclusions were written based on the comparison data:
| Method | AUC ≥1 °C | Detection (1% false alarm) | Event detection | Median latency |
|---|---|---|---|---|
| This design | 0.993 | 92% | 30/30 | 25 s |
| Fine-tuned ResNet-18 | 0.994 | 95% | 30/30 | 62.5 s |
| Fine-tuned MobileNetV3-S | 0.992 | 94% | 30/30 | 32.5 s |

- The assistant wrote: "The detection rate is on par with fine-tuned ResNet-18, not better. The report does not claim the best accuracy; the judges would see through it as soon as they checked the raw data." What holds up is: half the latency, about 1/270 of its computation, and only this design can run in real time on the PL.
- The control data had 4 false alarms in 1.5 h, all from the same control segment, and the report states this as is.
- After going on the board, the fused score crossed the threshold even without heating. The assistant found that the camera exposure was stuck at 1 (brightness 19). After restoring auto exposure there were still occasional false alarms, which it attributed to the optical path state differing from calibration: 21.5 min of no-heating data was recorded on the board, with a median block correlation of 0.43, versus 0.23 at calibration. The assistant did not simply raise the threshold. It recommended recalibrating with the newly recorded control data following the steps in `src/model/win_alarm/README.md`.

## Result
- Report v3 has 24 pages, with 10 new figures drawn in three color families: navy, teal and orange.
- The trained weights were exported to RTL, and simulation matched bit for bit 144/144. The rebuilt bitstream has WNS +2.55 ns in the 100 MHz domain and LUT 28.8%.
- On the board, PL inference matched bit for bit 40/40, at 1.34 ms per frame. Improvements left for before the defense are written in Part 4 (preprocessing 2 s → 41 ms not yet integrated, clock margin, more control data).
