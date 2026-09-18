#!/usr/bin/env bash
# One conv layer, RTL or post-synthesis netlist:
#   ./sim/rtl/run_xsim_layer.sh <layer> [rtl|postsynth]
set -euo pipefail
LAYER=${1:-0}
MODE=${2:-rtl}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=$ROOT/build/xsim/conv${LAYER}_${MODE}
source "${XILINX_ROOT:-/opt/xilinx/2026.1}/Vivado/settings64.sh"

rm -rf "$WORK" && mkdir -p "$WORK" && cd "$WORK"
cp "$ROOT"/src/rtl/gen/*.mem .
if [ "$MODE" = postsynth ]; then
    cat > synth.tcl <<TCL
read_verilog [glob $ROOT/src/rtl/qcnn/*.v] $ROOT/sim/rtl/conv_layer_dut.v
synth_design -top conv_layer_dut -part xc7z020clg400-1 -mode out_of_context \\
    -include_dirs $ROOT/src/rtl/gen -generic LAYER=$LAYER
write_verilog -mode funcsim -force dut_funcsim.v
TCL
    vivado -mode batch -nojournal -nolog -source synth.tcl > synth.log
    echo "ignored-initial warnings: $(grep -cE 'Synth 8-311' synth.log || true)"
    xvlog -d POSTSYNTH -i "$ROOT/src/rtl/gen" dut_funcsim.v "$XILINX_VIVADO/data/verilog/src/glbl.v" \
        "$ROOT/sim/rtl/tb_conv_layer.v" > xvlog.log
    xelab -L unisims_ver -generic_top "LAYER=$LAYER" tb_conv_layer glbl -s tb_layer > xelab.log
else
    xvlog -i "$ROOT/src/rtl/gen" "$ROOT"/src/rtl/qcnn/*.v "$ROOT"/sim/rtl/conv_layer_dut.v \
        "$ROOT/sim/rtl/tb_conv_layer.v" > xvlog.log
    xelab -generic_top "LAYER=$LAYER" tb_conv_layer -s tb_layer > xelab.log
fi
xsim tb_layer -R | tee xsim.log | grep -E "RESULT|LAYER|TIMEOUT"
