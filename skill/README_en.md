# skill — Skills

[中文](README.md) | English

Lessons from this project that **still apply to other topics and other boards**. The scripts read nothing from this project; pass them paths and they work on any design.

## Portability (tested)

`./skill/tests/run_tests.sh` runs the scripts on real build outputs from a different board (samples in `skill/tests/samples/`). All 15 cases pass.

| Skill | Zynq-7000 xc7z020 (PYNQ-Z2 class) | ZynqMP xczu3eg (this project) | Vivado versions | Dependencies |
|---|---|---|---|---|
| 6 `hwh_map.py` | tested | tested | 2025.1 / 2026.1 | standard library, Python ≥ 3.5 |
| 7 `check_vivado_reports.py` | tested | tested | 2017.4 / 2025.1 / 2026.1 | standard library |
| 1 `compare_golden.py` | device-independent | tested | — | numpy |
| 3 AXI DMA driver pitfalls | not run on the board; same PG021 register offsets, change the base address from the `.hwh` | tested | — | numpy |
| 4 DRM display mode | not applicable (no DPSUB) | tested | — | standard library |
| 2, 5, 8 | device-independent | | | |

Versal and FPGAs without a processing system have no samples and are untested.

## Index

| # | Type | Content |
|---|---|---|
| 1 | Case template + check script | Fixed-point CNN: export from a reference model, bit-exact RTL comparison |
| 2 | Pitfall checklist | When post-synthesis netlist simulation is required |
| 3 | Pitfall checklist | User-space AXI DMA driver without PYNQ |
| 4 | Script | Arbitrary display mode on ZynqMP without libdrm |
| 5 | Code template | Debouncing numbers on a small screen |
| 6 | Check script (generic PYNQ skill) | Address map and IP parameters from a `.hwh` without PYNQ |
| 7 | Check script | Automatic pass/fail on routed reports (timing, resources, power) |
| 8 | Prompt workflow | `prompts/README_en.md`, four templates |

---

## 1. Fixed-point CNN: reference-model export and bit-exact RTL comparison

- **Applies to**: any hand-written RTL implementing a fixed-point algorithm.
- **Method**: one integer reference model (`src/model/qcnn_int.py`) exports the weight `.mem` files, the parameter header `qcnn_params.vh` and the test vectors. The RTL only reads these files, so changing the model does not change the RTL.
- **Usage**:
  ```bash
  python src/model/qcnn_int.py <qmodel.npz> src/rtl/gen <inputs.npy>
  ./sim/rtl/run_verilator.sh tb_qcnn_core
  python3 -I skill/scripts/compare_golden.py <hardware output> <reference .mem> --signed --shape 16,9   # works for any project
  ```
- **Verified**: 144/144 bit-exact; with one word changed the script reports `MISMATCH (0, 4)` and exits 1.
- **Fails when**: the reference model's rounding and truncation differ from training; a floating-point model cannot serve as the reference.
- **Origin**: weights were once sliced inside an `initial` block and came out as all zeros after synthesis (see item 2).

## 2. When post-synthesis netlist simulation is required

| Symptom | Cause | Fix |
|---|---|---|
| RTL simulation passes, weights are all zero after synthesis, reports look normal | non-constant initialization in an `initial` block is ignored by synthesis | run netlist simulation (`sim/rtl/run_postsynth.sh`); initialize memories with `$readmemh` |
| Data lost at the start of netlist simulation | global reset (GSR) during the first 100 ns | hold reset until GSR is released |
| Unexpected BRAM use (139/140 at one point) | ROM address width inferred from a counter, making it 8192 deep | declare the address width from the real depth; check `utilization_hier.rpt` |

- **Fails when**: synthesis tools other than Vivado may follow different rules.

## 3. User-space AXI DMA driver without PYNQ (`/dev/mem`)

Code: `src/host/qcnn_dma.py`, PG021 simple mode.

| Symptom | Cause | Fix |
|---|---|---|
| SIGBUS when reading results, dmesg shows an alignment fault | an O_SYNC mapping is Device memory; `memcpy` issues unaligned loads | read the result one 32-bit word at a time |
| MM2S halts, `MM2S_CR = 0x10002` | a soft reset of either channel resets the whole DMA | reset once, wait for the reset bits to clear, then set RUN |
| Results arrive before the receive channel is ready | transmit started before receive | write the S2MM address and length before MM2S |

- Put the buffer in a device-tree no-map reserved region; the non-coherent HP port then needs no cache maintenance.
- **Fails when**: scatter-gather mode, or a kernel with an IOMMU. Use item 6 to check `c_include_sg = 0` (simple mode) first.

## 4. Arbitrary display mode on ZynqMP without libdrm

- **Applies to**: DPSUB output when the EDID lacks the needed resolution (here the adapter reports only 1080p and the panel is 800×480).
- **Usage**: `python3 board/fz3b/drm_mode_test.py --mode 800x480 --secs 600`; the script creates a dumb buffer, ADDFB and SETCRTC directly through ioctl.
- **Note**: the xlnx DPSUB primary plane accepts only RGB565; stop Xorg first.
- **Fails when**: the driver is not xlnx_drm, or another process holds DRM master.

## 5. Debouncing numbers on a small screen

- **Method**: five small classes in `src/host/ui_filter.py`: spike confirmation, value easing, last-digit hysteresis, state hysteresis, sticky axes. An alarm may end late but never start late.
- **Verified**: 19 tests; with ±0.02 noise, last-digit flips drop from 96 to 4.

## 6. Address map from a `.hwh` without PYNQ

- **Applies to**: PYNQ's `Overlay` builds `ip_dict` from the `.hwh`. When the board has no PYNQ or an old Python, this script gives the same table.
- **Usage**:
  ```bash
  python3 -I skill/scripts/hwh_map.py <design>.hwh                            # instance / type / base / size
  python3 -I skill/scripts/hwh_map.py <design>.hwh --ip <instance> --grep sg   # IP parameters
  python3 -I skill/scripts/hwh_map.py <design>.hwh --json map.json           # for a driver to read
  ```
- **Verified**: on the ZU3EG the 4 address ranges match `address_map.txt`; on the xc7z020 it reads `dma axi_dma 0x40400000`.
- **Fails when**: the `.hwh` comes from a different build than the bitstream on the board; hand-written slaves outside the block design are invisible; only register windows are listed.
- **Origin**: the DMA base address differs between this project's two builds, so the node service reads the build's address map instead of a hard-coded value.

## 7. Automatic pass/fail on routed reports

- **Usage**: `python3 -I skill/scripts/check_vivado_reports.py <reports dir> --max-bram 80 [--json m.json]`
  Prints a metrics table; the last line is `RESULT PASS` or `RESULT FAIL: <reason>`, with exit code 1 on failure, so it can go into a build script or CI.
- **Verified**: values match the report text for 3 device/version combinations; a constructed setup violation and a resource overrun are both reported as FAIL.
- **Fails when**: Critical items in `report_cdc` are not judged; check them by hand.
- **Origin**: when BRAM hit 139/140, timing was still met; checking timing alone would have missed it, so resources and timing are judged together.

## 8. Prompt workflow

See `prompts/README_en.md`: HW/SW partitioning, locating bottlenecks from reports, reproduce-before-fix for board faults, data-leakage check before evaluation.
