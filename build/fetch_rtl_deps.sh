#!/usr/bin/env bash
# Fetch open-source RTL used by src/rtl (MIT, Alex Forencich), pinned commits.
set -euo pipefail
cd "$(dirname "$0")/../refs"
fetch() {  # name url commit
    [ -d "$1" ] || git clone -q "$2" "$1"
    git -C "$1" fetch -q --depth 1 origin "$3" && git -C "$1" checkout -q "$3"
}
fetch verilog-uart https://github.com/alexforencich/verilog-uart.git 1b867e53af738e4a8bc7c839ca2f1c07f40382dc
fetch verilog-axis https://github.com/alexforencich/verilog-axis.git 48ff7a7e2ef782cf778d47910cf85835c64b1bce
