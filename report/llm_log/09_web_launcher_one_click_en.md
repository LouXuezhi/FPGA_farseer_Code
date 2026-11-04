# 09 One-click launch and connection bar for the camera web page: test with a simulated camera first, then on the real device

[中文](09_web_launcher_one_click.md) | English

Session: Making the camera preview service launch with a double click, and adding reconnect and other buttons to the web page　Date: 2026-10-30

## Prompt (condensed)
> Make me an automation script so that one click starts it directly; also add buttons on the website, such as reconnect.

## Model answer (summary)
- The service was restructured so that it "can start without a camera". The web page shows the reason and keeps retrying in the background, instead of failing to start as before.
- New endpoints: reconnect camera (hard reset, check chip ID, restart streaming), reload bitstream, stop streaming, restart service. Without a camera, a 503 placeholder image is returned.
- A connection bar was added at the top of the page: a green/yellow/red status light and reason text (for example "I2C not acknowledging, check the tail cable"). It reconnects automatically when the image stops for 2 s or the connection fails.
- A `.command` launcher was placed on the desktop. It finds the USB network adapter connected to the board and sets its address, waits for the board to come online, compares the code on the Mac and on the board, and uploads and restarts the service only if something changed.

## Self-correction
- Before deploying to the board, the assistant ran the new service on the Mac with a simulated camera, and all 8 test cases passed. It then simulated cable unplugging, recovery and service exit in the browser, and checked the page state and automatic recovery.
- When testing the "restart service" button, the click landed on a `confirm()` dialog and nothing was executed. The assistant instead killed the service process directly to simulate a restart, and confirmed that the page first turned red and then, after about 2 s, the video and file list recovered automatically.
- The assistant switched the network adapter back to automatic addressing to simulate "the Mac forgot its IP", and the launch script could not find the adapter. The cause was that each line of the network service list starts with `(`, while the sed rule assumed lines start with `Hardware Port:`. After the parsing was fixed, the script found the AX88179B on its own and set it back to 192.168.1.253, without affecting Wi-Fi or the default route.

## Result
| Button | Measured |
|---|---|
| Reconnect camera | under 1 second |
| Reload bitstream | about 1 second |
| Restart service | web page recovers automatically after about 1.5 seconds |
| Auto reconnect | recovers on its own after a simulated unplug |

- Exposure and gain stay unchanged after a reconnect or restart, confirmed by reading the sensor registers. After every recovery: 144.8 fps, 800 lines per frame, 0 bad packet headers.
- The real page renders the connection bar with no console errors. Usage is written in `README_live.md`.
