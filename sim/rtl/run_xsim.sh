#!/usr/bin/env bash
# Vivado simulator (xsim) batch run: ./sim/rtl/run_xsim.sh tb_qcnn_core
set -euo pipefail
shopt -s nullglob
TOP=${1:-tb_qcnn_core}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$ROOT/build/xsim/$TOP
source "${XILINX_ROOT:-/opt/xilinx/2026.1}/Vivado/settings64.sh"

mkdir -p "$WORK" && cd "$WORK"
cp "$ROOT"/src/rtl/gen/*.mem .                     # $readmemh looks in the run dir
xvlog -i "$ROOT/src/rtl/gen" \
    "$ROOT"/src/rtl/qcnn/*.v \
    "$ROOT"/src/rtl/link/*.v \
    "$ROOT"/refs/verilog-axis/rtl/axis_fifo.v \
    "$ROOT/sim/rtl/$TOP.v" > xvlog.log
xelab -debug typical --incr "$TOP" -s "$TOP" > xelab.log
xsim "$TOP" -R | tee xsim.log | grep -E "RESULT|LAYER|MISMATCH|TIMEOUT|ERROR|Error"
