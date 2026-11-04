# 06 Merged bitstream on the board: AXI width and reset polarity, two errors simulation cannot catch

[中文](06_merged_bitstream_axi_width_reset.md) | English

Session: Loading the merged bitstream at Linux runtime through the FPGA manager　Date: 2026-11-02

## Prompt (condensed)
> OK, program it. (The board just lost power.) First bring the camera service back up, and program only after confirming it works.

## Model answer (summary)
- Before programming, the assistant confirmed the camera service: chip ID 0x9281, 145 fps, and two frame grabs 1 second apart showed the frame counter advancing by 146, confirming that new frames were being fetched.
- The assistant first backed up the original camera bitstream to `/root/cam/backup_20261102/`, then loaded the merged version.
- After loading, the camera service reported "bitstream version 0x00000000". The assistant read the registers of each module one by one and found that everything outside the low 8 bits of each 32-bit word read as 0.
- Cause: at boot, the PS had already configured the PS-PL AXI ports as 32-bit (register port) and 64-bit (memory port), and loading a bitstream at runtime does not change this. The merged project used the Vivado default of 128 bits. Simulation does not go through the PS, so this was not caught.
- The assistant first rolled back to the original camera bitstream (145 fps, 800 lines, correct chip ID), changed the widths, rebuilt, and then programmed again.

## Self-correction
- After the second programming the camera worked (version 0xCA928103), but the PL inference DMA hung, with MM2S status halted.
- The assistant first added a "shared reset" model to the host-side fake DMA test and confirmed that this kind of test could have caught the problem. It then checked the wiring and found that `temp_link/rst` is active high but was connected to the active-low `peripheral_aresetn`. The inference core was held in reset, and the DMA hung as a result.
- After reconnecting it to `peripheral_reset` and rebuilding, timing still closed.
- Another bug: the VDMA frame buffer count register read 0 in this version. `dp_live.py` was changed to count frame buffers from the start addresses that had been set, and the corresponding tests were updated.

## Result
- With the merged bitstream on the board: camera at 144.8 fps, 800 lines per frame, 0 bad packet headers, 0 FIFO overflows. The Mac received 434 frames in 3 s with consecutive frame numbers.
- PL inference actually ran: 4 image pairs with 9 outputs each, 2.94 ms in total.
- The camera register addresses (0xA0000000, 0xA0010000) are unchanged, so the capture scripts need no change. The inference DMA is at 0xA0020000 and the display module is at 0xA0030000.
