# 01 Hardware/software partitioning: the assistant corrects the plan to "put MQTT in the PL and attach DP to the PL"

[中文](01_hw_sw_partition_mqtt_dp.md) | English

Session: Three-layer architecture (MQTT messaging, CNN inference, Mini DP color-mapped display), design and implementation　Date: 2026-11-01

## Prompt (condensed)
> The high-level design has the FPGA cover three layers: MQTT IoT message aggregation, CNN model inference, and Mini DP screen output with the model results color-mapped. The three sides need to work together, meet timing, and be fast and efficient. Look at the overall plan, write the code and test it, but do not program the board yet.
> (Later) Of course the camera and the CNN have to run together, otherwise where would the input come from? What does "the display driver in the on-board Linux supports live input" mean?

## Model answer (summary)
- The assistant first corrected two points in the layering. MQTT runs over TCP, and the Ethernet port and the TCP/IP stack are in Linux on the PS, so MQTT runs on the PS and the PL only hands over results through registers. The Mini DP port belongs to the PS DisplayPort controller, so the PL can only send frames through its live input.
- Data flow: camera → VDMA writes to memory → PS preprocessing → PL CNN (fed by DMA) → the results are written to the overlay registers and published over MQTT at the same time.
- The CNN core was changed to 2-frame input and 9 outputs. `mqtt_lite.py` uses only the standard library, and all board-side code is kept parseable by the board's Python 3.6.
- Capture, inference and MQTT do not block each other. Capture keeps only the latest frame. When the network is down, low-priority messages are dropped first, and QoS 1 messages are resent after reconnecting.

## Self-correction
- The first version only provided a bitstream for inference + display. The camera design existed separately, and only one of them could be loaded at a time. The user pointed out that "the camera and the CNN must run together". The assistant copied the camera design currently used on the board (checked against the source of `fz3b_cam.bin` on the board) unchanged as the top level, and put the CNN and the display into the same design. The camera register addresses stayed the same, so not a single line of the capture program changed.
- For "live input", the assistant checked the board kernel (4.14, Xilinx 2018.3). The live input only has an in-kernel interface with no user-space switch, and the code path for "graphics layer uses live" has a bug that turns that layer off. The design was therefore changed so that the PL composes the whole frame and feeds it into the video layer of the DP controller, and Linux does not touch the video layer.
- Testing caught a real bug: an MQTT message sent exactly at the moment of disconnection was lost. It has been fixed. The original `frame_server.py` had two Python 3.8 constructs that would not run on the board, and they were replaced.

## Result
- Inference simulation (Verilator and xsim) matches the Python reference model bit for bit, 144/144, at 0.565 ms per frame.
- All three clock domains of the merged project meet timing (see 04). Resources: logic 22%, DSP 31%, block RAM 21%.
- Later the user proposed connecting the calibration serial port to the FPGA. The assistant objected: the host PC fetches the reference temperature from the capture service interface and sends it down through the MQTT topic `cmd/calibrate`. There is no need to connect the serial port, and this avoids competing with the capture program for the serial port.
