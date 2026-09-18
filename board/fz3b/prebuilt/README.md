# fz3b prebuilt

Vivado 2026.1 build of `build/vivado/build_fz3b.tcl`, part `xazu3eg-sfvc784-1-i`.

| file | size | use |
|---|---|---|
| `qcnn_fz3b.bin` | 5.3 MB | load from Linux with `fpgautil -b` (fpga_manager) |
| `qcnn_fz3b.bit` | 5.3 MB | load over JTAG with Vivado Hardware Manager |
| `qcnn_fz3b.hwh` | 0.3 MB | block design description (PYNQ overlay / reference) |
| `address_map.txt` | - | `temp_dma` registers at `0xA0000000` |
| `sha256sums.txt` | - | check with `sha256sum -c sha256sums.txt` |

Result: WNS +3.861 ns, WHS +0.010 ns at 100 MHz; LUT 10946 (15.5 %), BRAM 29.5 (13.7 %), DSP 104 (28.9 %), 2.73 W.

Only one of `.bin` and `.bit` is needed. Nothing is written to board flash:
boot the vendor SD image, copy this folder and `src/host/` to the board, then follow `board/fz3b/README.md`.

If the chip is marked `XCZU3EG-1SFVC784E`, rebuild with that part first.
