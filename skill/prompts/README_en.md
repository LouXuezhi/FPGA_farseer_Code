# Prompt workflow

[中文](README.md) | English

Prompt templates that worked reliably with an LLM coding assistant during this project. Replace the parts in angle brackets with your own topic and keep the rest as is.
Each template is followed by how it was used here, what it achieved, and when not to use it.

---

## P1. Derive the HW/SW partition from an algorithm description

```
Below is an algorithm that must run in real time on <device, e.g. xczu3eg>, processing <input size> every <period>.
<algorithm steps, in order, each with input/output shape and data type>

Give, step by step:
1. the compute (multiply-accumulates) and data volume (bytes) of each step, as a table, showing how you computed them;
2. whether each step goes on the PS or the PL; the only allowed reasons are compute, data volume,
   whether bit-exact reproducibility is needed, and the cost to implement and change; no reasons
   without numbers such as "the FPGA is faster";
3. the PS-PL interface: what is transferred, how large, how often, AXI-Lite or AXI DMA;
4. a separate list of the numbers you are unsure of, and what I must measure on the board to settle them.
Do not write code.
```

- Here: the 4 convolution layers take about 2.62 M multiply-accumulates, 99.9 % of the total (the FC layers take 1168), so they go on the PL; preprocessing has many branches and must match the training side bit for bit, so it stays on the PS. On the board the PS preprocessing takes 2 s per frame and is the current bottleneck. Numbers like this can only be measured on the board, which is why the template asks the assistant to list them as open items.
- Effect: once the template required "show the calculation" and "list unsure numbers separately", the assistant stopped giving unsupported speed-up figures.
- Do not use when: the algorithm itself is not settled; validate the algorithm first.

## P2. Locate bottlenecks from synthesis / implementation reports

```
These are Vivado <version> reports for <top module>:
<paste the Design Timing Summary and the 10 worst paths from timing_summary.rpt;
 the 10 largest hierarchies from utilization_hier.rpt>

Answer:
1. whether timing is met; start point, end point, logic levels and routing share of the worst path;
2. which hierarchy uses an amount of <LUT/BRAM/DSP> out of proportion to its function (give the amount you expect and how you computed it);
3. one minimal change for each of 1 and 2, and how to verify it worked (which line of which report).
Use only numbers that appear in the reports; if the reports do not contain something, say so.
```

- Here: it located a bias ROM inferred from an 8-bit counter as 8192 deep, one BRAM per lane (139/140 BRAM in total at one point); declaring the address width from the real depth fixed it (item 2 of `skill/README_en.md`).
- Companion script: `skill/scripts/check_vivado_reports.py` turns the reports into a metrics table first, which is shorter to paste and avoids copying numbers wrong.
- Do not use when: reading timing from synthesis-only (unrouted) reports; treat that timing as indicative only.

## P3. Make the assistant reproduce before fixing (board faults)

```
Symptom: <paste the error, dmesg and register reads as they are>
Environment: <kernel version, Python version, path of the relevant driver file>

Do not change any code yet. Please:
1. list possible causes, most likely first, each with one minimal experiment that tells it apart (one command or a few lines);
2. after I run the experiments and paste the output, fix only the confirmed cause;
3. after the fix, give a regression test that runs on a machine without the board.
```

- Here: the DMA SIGBUS when reading results (unaligned access on `/dev/mem`) and the halted MM2S (repeated soft reset) were both found this way, and the fixes went into `sim/host/test_node.py` as regression tests.
- Effect: stops the assistant from changing several things at once before the cause is confirmed.

## P4. Data-leakage check before reporting results

```
Below are my train/evaluation split and the resulting metrics: <split, recording time and label of each session, metrics>
Check:
1. whether the training and test sets contain data that are adjacent in time, from the same recording, or from the same calibration;
2. whether thresholds, normalization parameters or hyper-parameters used test data;
3. if so, a nested evaluation that sets these parameters from the training folds only, and how you expect the metrics to change.
```

- Here: it found that control sessions had leaked into training, inflating the hold-out metrics; the fix was to hold out one control session per fold and set thresholds from the training folds' controls only (`src/model/win_alarm/nested.py`).
- Do not use when: there is only one recording; collect more data first, no evaluation scheme can fix that.
