# 04 The illusion of "all timing met": constraints were not applied

[中文](04_timing_constraints_not_applied.md) | English

Session: Vivado build of the merged camera + QCNN + DP overlay project　Date: 2026-11-01 to 11-02

## Prompt (condensed)
> The three layers (messaging, inference, screen output) need to work together and meet timing. Write the code, simulate and build first; do not program the board yet.

## Model answer (summary)
- After each build, the assistant checked not only WNS but also the clock list and the CDC report, and compared them against a known working project.
- Newly written RTL was first compared bit for bit against the Python reference model in two simulators, Verilator and Vivado xsim (inference 144/144; for the overlay, all 921,600 pixels of one frame matched).

## Self-correction
In three builds that "looked like they passed", the assistant found one constraint that had not taken effect in each:
1. The first build had WNS +3.6 ns in the 100 MHz domain, but the clock list contained only `clk_pl_0`. The 74.25 MHz video clock was not checked because the constraint file was not applied. The assistant rewrote it as a top-level XDC that follows the real hierarchy, tried it on the routed checkpoint, and then rebuilt.
2. After the camera design was merged, the camera byte clock did not appear in the clock table. The assistant compared against the camera-only project in use on the board, filled in the missing constraints, and then checked the report again.
3. The CDC constraints for the screen frame fetch used `foreach`, which XDC does not support, so the whole block of constraints did not take effect. The assistant expanded it into individual constraints and rebuilt.
- When restarting a build, the `pkill -f` pattern matched the command line of the SSH session itself and killed the connection. The assistant switched to stopping the old build by PID.

## Result
| Build | Clocks | Slack |
|---|---|---|
| Inference + overlay (after the constraint fix) | 100 MHz / 74.25 MHz video | +4.09 ns / +10.48 ns |
| Camera + inference + display merged | 100 MHz / camera byte clock / DP pixel clock | +3.98 / +5.88 / +8.41 ns |

- Merged project resources: logic 22%, DSP 31%, block RAM 21%. The remaining Critical items in the CDC report are all in the camera's original D-PHY part.
- Lesson written into the flow: before accepting a positive WNS, first confirm that every clock is in the clock table and that every exception constraint was actually applied.
