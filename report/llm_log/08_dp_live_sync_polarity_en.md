# 08 Black screen after the PL takes over Mini DP: sync polarity of the live input

[中文](08_dp_live_sync_polarity.md) | English

Session: PL-composed 800×480 interface output to a 7-inch screen through the live input of the ZynqMP DP controller　Date: 2026-11-02

## Prompt (condensed)
> Solve the screen detection first. (Later) There are color bars. 800×480 works. (After the PL takes over) Black screen.

## Model answer (summary)
- The assistant first ran read-only diagnostics: HPD=1, but the AUX channel timed out every time and EDID was 0 bytes. It judged this to be an adapter problem (ZynqMP DP does not support DP++, and a passive adapter behaves like this). After the user changed the cable, EDID read 128 bytes, the model field was CH7218 (an active DP→HDMI chip), and it reported only 1920×1080.
- Using `drm_mode_test.py`, the assistant temporarily switched to 800×480@60 (pixel clock 33.26 MHz, automatically restored after 10 minutes) and drew a test pattern with 1-pixel lines, confirming that the screen does not rescale. The primary plane does not accept XRGB8888, so RGB565 was used instead.
- After switching to the PL live input: the PL steadily output 60 frames per second, and the DP controller also switched to the PL clock and timing, but the screen was black.

## Self-correction
- At the first black screen, the assistant saw that the background read and camera read counters were both 0 and suspected that the PL-to-memory read channel was not working. It then found that only the test color bars were enabled at the time (CTRL=0x2), and by design the color bar mode does not read memory, so this "fault" was a misjudgment. With the real interface enabled, the background fetched 480 lines per frame and the camera fetched 240 lines per frame. The read channel was fine, and the scope narrowed to what comes after the PL output.
- The new suspect was sync polarity. The 800×480 mode uses active-low sync, and the PL output followed that. The Xilinx driver source does not change sync polarity when switching to live, and the documentation does not say which polarity the live input requires, so it had to be tested.
- The assistant made a test with a 20 s automatic rollback: the PL output active-high sync and only color bars. The user reported "I see color bars".
- The same session also found that the user's capture web preview going black was unrelated to programming. After the power loss, the camera exposure had returned to the default of 1 line (RAW mean about 17), and the "auto exposure" checkbox on the web page showed the page default, not the actual state of the board.

## Result
- Conclusion: the live input of the DP controller only accepts active-high sync. `dp_live.py` was changed to always output active-high sync; the polarity on the screen side is still handled by the DP controller according to the mode.
- Readback with the real interface: 60 frames/s, background about 28,800 lines/s, camera about 14,400 lines/s, no timeouts.
- The touchscreen was brought up at the same time: `wch.cn USB2IIC_CTP_CONTROL`, coordinates 0–4096, four-corner calibration error within 20 pixels, written into `ui_layout.py`.
