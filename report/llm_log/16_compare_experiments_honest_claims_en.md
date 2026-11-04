# 16 Comparison experiments: marking item by item which advantages hold and which do not

[中文](16_compare_experiments_honest_claims.md) | English

Session: Comparing against off-the-shelf models and other deployment options to show the algorithm and hardware/software co-design advantages　Date: 2026-11-03

## Prompt (condensed)
> Run some off-the-shelf models on our data to show the superiority of our model. Show two aspects: algorithmic superiority, and hardware/software co-design superiority with a focus on the FPGA. Run experiments and make plots.
> (Later) Make a table to see whether our model has these two kinds of superiority. Can off-the-shelf models do reasonably good temperature detection, not just an over-temperature warning?

## Model answer (summary)
- Measured on the board: PL output matches the fixed-point reference bit for bit, 65/65. A single frame (including DMA) takes 1.33 ms; batch 16 takes 0.64 ms/frame.
- The same QCNN on the A53: ONNX Runtime fp32 2.44 ms, NumPy fixed-point 33.5 ms. Off-the-shelf models on the A53 take 96–2668 ms.
- Temperature rise detection on our own captured data: the 1−corr method has AUC 0.998 and detects 97% of ≥1 °C rises at 1% false alarm rate; frame-by-frame ZNCC 33%, ResNet-18 features 18%, ViT features 11%, supervised ridge regression 24%.
- On the temperature measurement ability of off-the-shelf models: with one calibration at power-on and 4-fold hold-out, the heated-point MAE is 9.6 °C for no measurement (constant 0), 8.5 °C for fine-tuned ResNet-18, 8.4 °C for fine-tuned MobileNetV3-S, and 9.0 °C for the QCNN structure. All are only about 1 °C better than "no measurement".

## Self-correction
No notable code correction. The value of this segment is that the assistant did not write conclusions that simply followed the request to "show superiority", and instead put the unfavorable results in the table:
- The same QCNN takes only 0.22 / 0.26 ms on a PC CPU / RTX 4060 Ti, faster than the PL. So against desktop machines only energy is compared (PL 0.29 mJ, GPU running MNet-reg 48 mJ), and it is stated that the two sides are measured differently.
- The whole system takes 2.02 s per frame, with the bottleneck in PS preprocessing (2014 ms), so the PL acceleration does not show up at the system level. The assistant wrote an OpenCV version of the preprocessing (41 ms, correlation 0.99997 with the original), but noted that it "is not yet integrated into the node; this is only an estimate".
- When the same frame was repeated, the PL measured 0.66 ms, shorter than the first-frame latency in simulation, for unknown reasons. The assistant chose not to use this number.

## Result
- The comparison plots and numbers went into Part 3 of the design report (Table 3-2, Figure 3-10, Figure 3-11).
- Report wording: at similar accuracy (within 0.4 °C), the computation is 1/20 to 1/7000 of the off-the-shelf models; 70 to 2000 times faster than the models deployable on the on-board A53; and more than an order of magnitude lower energy per inference than the GPU.
- Based on this, the user decided that the report would only include comparisons on our own captured data, and the hardware/software co-design part would keep only the metrics that hold up (on-board latency, energy, comparison with the A53).
