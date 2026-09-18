#!/usr/bin/env bash
# Post-synthesis functional sim of qcnn_core: catches RTL constructs that
# simulate but synthesize differently (e.g. ignored initial-block logic).
#   ./sim/rtl/run_postsynth.sh [n_frames]
set -euo pipefail
N_FRAME=${1:-4}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$ROOT/build/xsim/postsynth
source "${XILINX_ROOT:-/opt/xilinx/2026.1}/Vivado/settings64.sh"

mkdir -p "$WORK" && cd "$WORK"
cp "$ROOT"/src/rtl/gen/*.mem .
cat > synth.tcl <<EOF
read_verilog [glob $ROOT/src/rtl/qcnn/*.v]
synth_design -top qcnn_core -part xc7z020clg400-1 -mode out_of_context -include_dirs $ROOT/src/rtl/gen
write_verilog -mode funcsim -force qcnn_core_funcsim.v
EOF
vivado -mode batch -nojournal -nolog -source synth.tcl > synth.log
grep -E "Synth 8-311|CRITICAL" synth.log | head -5 || true

xvlog -d POSTSYNTH -i "$ROOT/src/rtl/gen" qcnn_core_funcsim.v "$ROOT/sim/rtl/tb_qcnn_core.v" \
    "$XILINX_VIVADO/data/verilog/src/glbl.v" > xvlog.log
xelab -L unisims_ver -generic_top "N_FRAME=$N_FRAME" tb_qcnn_core glbl -s tb_postsynth > xelab.log
xsim tb_postsynth -R | tee xsim.log | grep -E "RESULT|MISMATCH|TIMEOUT"
