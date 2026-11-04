# 02 No data on MIPI lane1: from register assumptions to locating a single broken line

[中文](02_mipi_lane1_fault_location.md) | English

Session: CSI-2 2-lane reception from OV9281 to FZ3B, where lane1 never receives a complete line　Date: 2026-10-29 to 10-30

## Prompt (condensed)
> Get the whole camera link running, then raise the capture rate as far as possible.
> (Added midway) In the OV9281 datasheet the lane count is in 0x3039[7:5]; 0x3018 does not control the lane count. dl1_stop can change, and with hsact=0 you still cannot tell apart a P/N swap, signal integrity, and PHY timing. Do not lock onto a hardware cause too early.
> (Later) We tested continuity on the adapter board and it was all fine, and we also re-established a common ground. Test it again.

## Model answer (summary)
- A self-written D-PHY + CSI-2 parser + VDMA bitstream was put on the board. The lane0 header bytes are `2B 06` (RAW10, high byte of WC=1600), so wire order and polarity are correct. lane1 reports an SoT error every time.
- The assistant rewrote the D-PHY in component mode (IBUFDS_DPHY + IDELAY + ISERDES). Each lane searches for the sync code in both polarities at the same time and sweeps the taps. lane0 hits 100% across taps 0 to 288. lane1 peaks at 69%, with almost all ECC wrong.
- The lane1 hit rate got worse as the rate went down (59% at 800 Mb/s, 3% at 256 Mb/s). This is the opposite of how an ordinary timing margin problem behaves.
- The assistant built a dedicated probe bitstream: the 6 MIPI lines were changed into plain 1.2 V inputs with internal pull-downs, and the fraction of time each line was high was counted. Only D1_N (J8-8) was at 0% in all three states (standby, streaming, reset). The other 5 lines changed with the camera state.
- The assistant gave a comparative measurement method with a multimeter in diode mode: use the P line of the same pair (pin 7) as the reference, and measure in two configurations, "ribbon cable only" and "camera only".

## Self-correction
- The assistant first followed the mainline driver register table and used 0x3018=0xD0 to test "single lane", and concluded from that test that the module could only do 2-lane. The user pointed out that the lane count is in 0x3039[7:5]. The assistant confirmed that the earlier test had not actually switched to single lane, and retested by writing 0x3039=0x12. Readback showed the write took effect, but the lane0 byte structure was still the 2-lane split, so the conclusion was recorded as "undetermined".
- Early judgment leaned toward a "P/N swap", and the user reminded the assistant to keep multiple hypotheses open. The assistant switched to the polarity self-search PHY and pin probing to tell the hypotheses apart, instead of drawing a conclusion directly.
- The user first reported "test B pin 8 OL", and later said "the 16th is 0". The assistant did not accept this directly. It first asked whether the reading was 0.000 or OL, and asked for the 2nd and 15th (GND) to be measured to confirm that the gold-finger numbering had not been counted from the wrong end. After retesting, the user corrected the report to "16 and 18 are both about 0.5 V".
- After switching to a new 25P ribbon cable the results did not change at all. The assistant used this to rule out the cable and narrowed the scope to the camera side.

## Result
- Combined three sets of measurements: with the tail cable unplugged, gold fingers 16 and 18 are both about 0.5 V to GND; with it plugged in, FPC2-8 to GND is OL; the FPC2-8 ↔ FPC1-16 pads are continuous.
- Conclusion: the break is between pin 16 of the FPC1 connector on the adapter board and the gold finger (a cold solder joint on the connector pin, or a contact that is not fully seated). The earlier continuity test points were on the pads and did not go through the connector pin, so the break was not detected.
- Until the fix, the system continued with lane0-only imaging (see 03).
