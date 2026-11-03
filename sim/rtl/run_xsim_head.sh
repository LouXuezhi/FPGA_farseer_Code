#!/usr/bin/env bash
# Head (GAP + FC) sim, RTL or post-synthesis netlist:
#   ./sim/rtl/run_xsim_head.sh [rtl|postsynth]
set -euo pipefail
MODE=${1:-rtl}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$ROOT/build/xsim/head_${MODE}
source "${XILINX_ROOT:-/opt/xilinx/2026.1}/Vivado/settings64.sh"

rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
cp "$ROOT"/src/rtl/gen/*.mem .
if [ "$MODE" = postsynth ]; then
    cat > synth.tcl <<TCL
read_verilog [glob $ROOT/src/rtl/qcnn/*.v] $ROOT/sim/rtl/head_dut.v
synth_design -top head_dut -part ${FSF_PART:-xczu3eg-sfvc784-1-i} -mode out_of_context -include_dirs $ROOT/src/rtl/gen
write_verilog -mode funcsim -force head_funcsim.v
TCL
    vivado -mode batch -nojournal -nolog -source synth.tcl > synth.log
    echo "ignored-initial warnings: $(grep -cE 'Synth 8-311' synth.log || true)"
    xvlog -i "$ROOT/src/rtl/gen" head_funcsim.v "$XILINX_VIVADO/data/verilog/src/glbl.v" \
        "$ROOT/sim/rtl/tb_head.v" > xvlog.log
    xelab -L unisims_ver tb_head glbl -s tb_head_sim > xelab.log
else
    xvlog -i "$ROOT/src/rtl/gen" "$ROOT"/src/rtl/qcnn/*.v "$ROOT"/sim/rtl/head_dut.v \
        "$ROOT/sim/rtl/tb_head.v" > xvlog.log
    xelab tb_head -s tb_head_sim > xelab.log
fi
xsim tb_head_sim -R | tee xsim.log | grep -E "RESULT|TIMEOUT"
