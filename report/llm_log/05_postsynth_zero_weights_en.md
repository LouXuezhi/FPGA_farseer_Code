# 05 RTL simulation passes, but the weights are all 0 after synthesis

[中文](05_postsynth_zero_weights.md) | English

Session: Early verification of the QCNN inference core (single-frame shape, XC7Z020)　Date: 2026-09-16 (based on the results record of that day, `results_2026-09-16`)

## Prompt (condensed)
> (Restated from the records of the time) The inference core RTL simulation matches the reference model bit for bit, but the post-synthesis netlist simulation output is wrong, as if the weights were all 0. The timing and resource reports are normal.
> The weight ROM is sliced per lane with a loop inside an `initial` block. Find the cause.

## Model answer (summary)
- Vivado synthesis only handles constant assignments and `$readmemh` inside `initial` blocks. Filling a memory with indices computed from parameters in a loop is non-constant initialization, which synthesis discards, so the ROM becomes all 0. RTL simulators execute `initial` blocks, so RTL simulation does not reveal the problem.
- Neither the timing report nor the resource report warns about ROM contents being 0. The problem can only be found with netlist simulation.

## Self-correction
- The `initial` code for weight slicing came from RTL that the assistant itself wrote. The problem surfaced in post-synthesis netlist simulation.
- Fix: the Python reference model exports `.mem` files per lane directly, and the RTL only reads these files with `$readmemh`. Memory contents are kept separate from the hardware structure.
- Two other problems of the same kind were found in the same phase and recorded together. The bias ROM was inferred as 8192 deep from an 8-bit counter, and BRAM usage at one point reached 139/140. In gate-level simulation, the GSR reset during the first 100 ns swallows data that is sent in early.

## Result
- Post-synthesis functional simulation is now a fixed part of the flow: `sim/rtl/run_postsynth.sh`, `run_xsim_layer.sh`, `run_xsim_head.sh`.
- The reference model (`src/model/qcnn_int.py`) became the single source of weights, shift parameters and test vectors, so changing the model does not change the RTL. With the current trained weights, 144/144 match bit for bit.
- The three lessons were organized as items 1 and 2 of the skill pack.
