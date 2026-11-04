# 03 False positives in the frame rate sweep: complete line counts do not mean a valid image

[中文](03_framerate_sweep_false_positive.md) | English

Session: Raising the capture frame rate after lane0-only imaging　Date: 2026-10-30

## Prompt (condensed)
> Get the camera link running, then raise the capture rate as far as possible and see whether it can be fully used. I will check it when I am back.

## Model answer (summary)
- While lane1 was faulty, a lane0-only parsing mode was added first. lane0 carries the even bytes of each line, which as RAW8 gives a 640×800 half-resolution image, so the VDMA → DDR path could be brought up first.
- First frame: 144.8 fps, exactly 800 lines per frame, 0 bad packet headers.
- The assistant wrote `fpssweep.py` to sweep PLL multipliers and VTS/HTS combinations. Each combination records frame rate, lines per frame, bad packet headers, FIFO overflow and VDMA status. The initial pass criterion was "800 lines per frame and no errors".
- Two high-frame-rate configurations each ran continuously for 10 s to confirm stability, and one frame from each was then inspected visually.

## Self-correction
- The first sweep reported two "passing" points, 229 fps and 546 fps. When inspecting sample frames, the assistant found that the images were blank or constant. When HTS is below 0x2d8 or VTS is pushed too low, the sensor still sends 800 lines according to the format, but the content is gone.
- The assistant added "the image has content" to the pass criteria and reran the sweep. The acceptance report states explicitly that the first-round numbers were false results and explains why.
- In the same session, the first Vivado build was killed (exit code 137). The Docker VM had only about 7.75 GB of memory, with 8 synthesis jobs running in parallel. The assistant did not change the global Docker Desktop settings. It switched to a serialized build with `-jobs 2`, which passed.

## Result
| Sensor mode | Actual image | Highest passing frame rate | Data written to DDR |
|---|---|---|---|
| 1280×800 RAW8 | 640×800 | 149.8 fps | about 77 MB/s |
| 640×400 (2×2 binning) | 320×400 | 299 fps | about 38 MB/s |

- The default operating point was set to 1280×800, VTS 910, 144.8 fps (the first real image was taken at this point and visually confirmed by the user).
- Pass criteria: complete line count per frame, no bad packet headers, no VDMA errors, and the image has content, each held for 10 s continuously.
- Later, the live web stream delivered 2173 frames in 15 s to the Mac (144.8 fps), with 0 bad frames.
