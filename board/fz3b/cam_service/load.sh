#!/bin/sh
# Load the camera bitstream into the PL through the Linux FPGA manager, or restore
# the stock EdgeBoard bitstream. Nothing on the SD card boot partition is touched;
# a power cycle always returns to the stock BOOT.BIN configuration.
#   load.sh cam     -> program fz3b_cam.bin
#   load.sh stock   -> reboot is the safe way back (fpgadrv expects its own PL)
set -e
cd /root/cam
case "$1" in
  cam)
    if lsmod | grep -q '^fpgadrv'; then rmmod fpgadrv && echo "fpgadrv unloaded"; fi
    cp -f fz3b_cam.bin /lib/firmware/fz3b_cam.bin
    echo 0 > /sys/class/fpga_manager/fpga0/flags
    echo fz3b_cam.bin > /sys/class/fpga_manager/fpga0/firmware
    echo "fpga_manager state: $(cat /sys/class/fpga_manager/fpga0/state)"
    dmesg | tail -3
    ;;
  stock)
    echo "Stock PL is restored by a reboot (BOOT.BIN on mmcblk1p1 is unchanged)."
    ;;
  *) echo "usage: $0 cam|stock"; exit 1 ;;
esac
