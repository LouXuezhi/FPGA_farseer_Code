#!/bin/sh
# Self-test of the skill scripts on designs other than this project's board.
#   ./skill/tests/run_tests.sh            (from the repository root; python3 >= 3.5, numpy for compare_golden)
# Samples: skill/tests/samples/  (Zynq-7000 xc7z020 / PYNQ-Z2 class, Vivado 2025.1 and 2026.1;
#          ZynqMP xczu3eg, Vivado 2017.4). Each case prints PASS/FAIL; exit 1 if any case fails.
cd "$(dirname "$0")/../.." || exit 1
S=skill/scripts
T=skill/tests/samples
PY=${PY:-python3}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fails=0

expect() {   # expect <name> <wanted exit code> <grep pattern> <command...>
    name=$1 want=$2 pat=$3
    shift 3
    out=$("$@" 2>&1)
    rc=$?
    if [ "$rc" = "$want" ] && printf '%s\n' "$out" | grep -q -- "$pat"; then
        echo "PASS $name"
    else
        echo "FAIL $name (exit $rc, want $want, pattern '$pat')"
        printf '%s\n' "$out" | tail -3
        fails=$((fails + 1))
    fi
}

# check_vivado_reports.py: three devices / tool versions, plus the failing branches
expect "reports zynq7000 v2026.1"   0 "LUT | 11110 / 53200"  $PY -I $S/check_vivado_reports.py $T/zynq7000_z7020_v2026_1
expect "reports zynq7000 v2025.1"   0 "WNS / TNS | 0.213"    $PY -I $S/check_vivado_reports.py $T/zynq7000_z7020_v2025_1
expect "reports zynqmp v2017.4"     0 "BRAM | 12.5 / 216"    $PY -I $S/check_vivado_reports.py $T/zynqmp_zu3eg_v2017_4
expect "reports this board"         0 "RESULT PASS"          $PY -I $S/check_vivado_reports.py build/vivado/reports
expect "reports resource limit"     1 "DSP 47.3% > 40.0%"    $PY -I $S/check_vivado_reports.py $T/zynq7000_z7020_v2026_1 --max-dsp 40
mkdir "$tmp/neg" && cp $T/zynq7000_z7020_v2026_1/*.rpt "$tmp/neg/"
sed -e 's/^      0\.224        0\.000  /     -0.412       -3.100  /' \
    -e 's/All user specified timing constraints are met\./Timing constraints are not met./' \
    $T/zynq7000_z7020_v2026_1/timing_summary.rpt > "$tmp/neg/timing_summary.rpt"
expect "reports setup violation"    1 "setup violated"       $PY -I $S/check_vivado_reports.py "$tmp/neg"

# hwh_map.py: Zynq-7000 and ZynqMP block designs
expect "hwh zynq7000 address"       0 "dma  *axi_dma  *0x40400000" $PY -I $S/hwh_map.py $T/zynq7000_z7020.hwh
expect "hwh zynq7000 device"        0 "xc7z020-clg400-1"     $PY -I $S/hwh_map.py $T/zynq7000_z7020.hwh
expect "hwh zynq7000 ip params"     0 "c_include_sg = 0"     $PY -I $S/hwh_map.py $T/zynq7000_z7020.hwh --ip dma --grep include_sg
expect "hwh zynqmp address"         0 "temp_dma  *axi_dma  *0xA0020000" $PY -I $S/hwh_map.py board/fz3b/prebuilt/fsf_fz3b.hwh
expect "hwh unknown ip"             1 "no instance"          $PY -I $S/hwh_map.py $T/zynq7000_z7020.hwh --ip nope

# compare_golden.py: identical, one word changed, length differs
printf '002c\n0014\nfffffffe\n' > "$tmp/want.mem"
printf '002c\n0015\nfffffffe\n' > "$tmp/bad.mem"
printf '002c\n0014\n' > "$tmp/short.mem"
expect "golden identical"           0 "RESULT 3/3 match"     $PY -I $S/compare_golden.py "$tmp/want.mem" "$tmp/want.mem" --signed
expect "golden mismatch"            1 "MISMATCH 1 got 21 want 20" $PY -I $S/compare_golden.py "$tmp/bad.mem" "$tmp/want.mem" --signed
printf '002c\n0014\nffffffff\n' > "$tmp/neg.mem"
expect "golden signed decode"       1 "got -1 want -2"       $PY -I $S/compare_golden.py "$tmp/neg.mem" "$tmp/want.mem" --signed
expect "golden length"              1 "LENGTH got 2 want 3"  $PY -I $S/compare_golden.py "$tmp/short.mem" "$tmp/want.mem"

[ $fails = 0 ] && echo "ALL PASS" || echo "FAILED: $fails"
[ $fails = 0 ]
