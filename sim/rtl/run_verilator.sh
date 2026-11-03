#!/usr/bin/env bash
# Verilator run of a testbench, for machines without Vivado (e.g. macOS):
#   ./sim/rtl/run_verilator.sh tb_qcnn_core [gen_dir]      gen_dir default src/rtl/gen
#   EXTRA_MEM=build/ui ./sim/rtl/run_verilator.sh tb_video_overlay   (after tools/ui/build_ui.py --cam ...)
# Same testbenches as run_xsim.sh. RTL only: the post-synthesis netlist check
# (run_postsynth.sh) still needs Vivado.
set -euo pipefail
shopt -s nullglob
TOP=${1:-tb_qcnn_core}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
GEN=$(cd "${2:-$ROOT/src/rtl/gen}" && pwd)
EXTRA=${EXTRA_MEM:+$(cd "$EXTRA_MEM" && pwd)}
WORK=${VL_WORK:-/tmp/verilator_fsf}/$TOP-$(basename "$GEN")   # GNU make rejects paths with spaces

mkdir -p "$WORK" && cd "$WORK"
cp "$GEN"/*.mem .                                   # $readmemh looks in the run dir
cp "$ROOT"/src/rtl/video/*.mem . 2>/dev/null || true
[ -n "$EXTRA" ] && cp "$EXTRA"/*.mem .   # e.g. EXTRA_MEM=build/ui for tb_video_overlay
verilator --binary --timing -j 0 -Wno-fatal -Wno-lint -Wno-style -Wno-TIMESCALEMOD -Wno-WIDTHCONCAT -Wno-SELRANGE -Wno-WIDTH \
    -I"$GEN" --top-module "$TOP" -o "$TOP" \
    "$ROOT"/src/rtl/qcnn/*.v "$ROOT"/src/rtl/link/axis_frame_link.v "$ROOT"/src/rtl/video/*.v \
    "$ROOT"/refs/verilog-axis/rtl/axis_fifo.v \
    "$ROOT/sim/rtl/$TOP.v" > verilator.log 2>&1 || { tail -30 verilator.log; exit 1; }
./obj_dir/"$TOP" | tee sim.log | grep -E "RESULT|LAYER|MISMATCH|TIMEOUT|ERROR|Error|PASS|FAIL"
