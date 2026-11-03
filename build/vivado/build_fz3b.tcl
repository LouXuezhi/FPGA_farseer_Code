# FZ3B (EdgeBoard, Zynq UltraScale+ ZU3EG, 4 GB DDR4) build:
#   vivado -mode batch -source build/vivado/build_fz3b.tcl [-tclargs <part>]
#   FSF_VIDEO=1 : + DP overlay (video_path)
#   FSF_CAM=1   : + OV9281 camera (src/rtl/cam, the design running on the board):
#                 top = fz3b_cam_top, block design cam_bd, part xczu3eg-sfvc784-1-i,
#                 camera addresses kept (cam_regs 0xA0000000, VDMA 0xA0010000) so
#                 stream.py / cam.py run unchanged; QCNN DMA 0xA0020000, overlay 0xA0030000
# PS runs Linux; the PL holds the QCNN temperature path behind an AXI DMA.
#
# The block design is a shell plus subsystems. Each subsystem creates its cells
# and returns what it needs from the shell:
#   {control slaves on HPM0}  {memory masters to HP0 (DDR)}  {interrupts}
# The shell wires those up, then ties every still-unconnected clock / reset
# input to pl_clk0 (100 MHz) and its reset. New paths (acoustic line-sensor
# CNN, DisplayPort live video) are added as further subsystems.
set root [file normalize [file dirname [info script]]/../..]
proc env_on {name} { expr {[info exists ::env($name)] && $::env($name)} }
set cam  [env_on FSF_CAM]
# the camera design was built and runs on xczu3eg-sfvc784-1-i; FSF_CAM=0 (core + DMA only) keeps the older default
set part [expr {$argc > 0 ? [lindex $argv 0] : ($cam ? "xczu3eg-sfvc784-1-i" : "xazu3eg-sfvc784-1-i")}]
set name [expr {$cam ? "fsf_fz3b" : "qcnn_fz3b"}]
set bd   [expr {$cam ? "cam_bd" : "qcnn_zu"}]           ;# cam: name fixed by fz3b_cam_top.v
set out  $root/build/vivado/[expr {$cam ? "fsf_fz3b" : "fz3b"}]
source $root/build/vivado/qcnn_common.tcl

create_project -force $name $out/project -part $part
add_qcnn_sources $root
create_bd_design $bd

# ---------------- shell: PS, 100 MHz fabric clock, reset ----------------
# HPM0_FPD = register access, HP0_FPD = DMA to DDR, pl_ps_irq0 = interrupts.
# DDR / MIO settings do not matter here: Linux (booted by the board image)
# loads this bitstream at run time and keeps its own PS configuration.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__M_AXI_GP2 0 \
    CONFIG.PSU__USE__S_AXI_GP2 1 CONFIG.PSU__USE__IRQ0 1 \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100 \
    CONFIG.PSU__MAXIGP0__DATA_WIDTH 32 CONFIG.PSU__SAXIGP2__DATA_WIDTH 64] $ps
# Port widths must match what the board's boot image programmed (FPD_SLCR AFI_FS,
# AFIFM2 RDCTRL/WRCTRL: HPM0 32 bit, HP0 64 bit, read on the board 2026-11-02);
# Linux loads this bitstream at run time and does not reprogram them. With the
# 128-bit default every register outside byte lane 0 read back as 0.
create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_100M
connect_bd_net [get_bd_pins ps/pl_clk0]    [get_bd_pins rst_100M/slowest_sync_clk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins rst_100M/ext_reset_in]

# ---------------- subsystems ----------------
# temperature: frames -> AXI DMA (MM2S) -> axis_frame_link (QCNN) -> AXI DMA (S2MM)
proc temperature_path {} {
    set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma temp_dma]
    set_property -dict [list CONFIG.c_include_sg 0 CONFIG.c_sg_length_width 26 \
        CONFIG.c_m_axis_mm2s_tdata_width 8] $dma
    create_bd_cell -type module -reference axis_frame_link temp_link
    connect_bd_intf_net [get_bd_intf_pins temp_dma/M_AXIS_MM2S] [get_bd_intf_pins temp_link/s_axis]
    connect_bd_intf_net [get_bd_intf_pins temp_link/m_axis]     [get_bd_intf_pins temp_dma/S_AXIS_S2MM]
    return [list {temp_dma/S_AXI_LITE} \
                 {temp_dma/M_AXI_MM2S temp_dma/M_AXI_S2MM} \
                 {temp_dma/mm2s_introut temp_dma/s2mm_introut}]
}

# video: the whole 800x480 screen composed in the PL (background from DDR, water
# recoloured from the sensor profile, camera from the VDMA frame stores, text)
# -> PS DisplayPort live *video* layer. Linux keeps the DP link and
# mode; src/host/dp_live.py switches the video layer to live / PL clock /
# external timing and copies the mode into the overlay (see video_overlay_top.v).
proc video_path {} {
    set_property -dict [list CONFIG.PSU__USE__VIDEO 1 CONFIG.PSU__DISPLAYPORT__PERIPHERAL__ENABLE 1 \
        CONFIG.PSU__CRF_APB__DP_VIDEO_REF_CTRL__FREQMHZ 74.25] [get_bd_cells ps]
    create_bd_cell -type module -reference video_overlay_top ovl
    connect_bd_net [get_bd_pins ps/dp_video_ref_clk] [get_bd_pins ovl/vclk]
    foreach p {dp_video_in_clk dp_live_video_in_vsync dp_live_video_in_hsync dp_live_video_in_de
               dp_live_video_in_pixel1} {
        connect_bd_net [get_bd_pins ovl/$p] [get_bd_pins ps/$p]
    }
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant ovl_lock
    connect_bd_net [get_bd_pins ovl_lock/dout] [get_bd_pins ovl/vclk_locked]
    if {[llength [get_bd_cells -quiet vdma]]} {                 ;# camera_path ran first
        connect_bd_net [get_bd_pins vdma/s2mm_frame_ptr_out] [get_bd_pins ovl/cam_frame_ptr]
        return [list {ovl/s_axi} {ovl/m_axi ovl/m_axi_cam} {}]
    }
    set z [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant ovl_noptr]   ;# no camera: preview off
    set_property -dict [list CONFIG.CONST_WIDTH 6 CONFIG.CONST_VAL 0] $z
    connect_bd_net [get_bd_pins ovl_noptr/dout] [get_bd_pins ovl/cam_frame_ptr]
    return [list {ovl/s_axi} {ovl/m_axi ovl/m_axi_cam} {}]
}

# camera: fz3b_cam_top.v (outside the BD) streams pixels into S_VIDEO and gets its
# register window on M_REGS; VDMA S2MM writes the frames to DDR (board/fz3b/cam_service/cam.py programs it)
proc camera_path {} {
    set vdma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_vdma vdma]
    set_property -dict [list CONFIG.c_include_mm2s 0 CONFIG.c_include_s2mm 1 CONFIG.c_s2mm_genlock_mode 0 \
        CONFIG.c_m_axi_s2mm_data_width 64 CONFIG.c_s2mm_max_burst_length 64 CONFIG.c_num_fstores 4 \
        CONFIG.c_use_s2mm_fsync 0 CONFIG.c_s2mm_linebuffer_depth 4096 CONFIG.c_include_s2mm_dre 0 \
        CONFIG.c_mm2s_genlock_mode 0 CONFIG.c_enable_s2mm_frm_counter 1 CONFIG.c_addr_width 32] $vdma
    set f [get_property CONFIG.FREQ_HZ [get_bd_pins ps/pl_clk0]]
    create_bd_port -dir O -type clk aclk
    create_bd_port -dir O -type rst aresetn
    create_bd_intf_port -mode Master -vlnv xilinx.com:interface:aximm_rtl:1.0 M_REGS
    set_property -dict [list CONFIG.PROTOCOL AXI4LITE CONFIG.ADDR_WIDTH 32 CONFIG.DATA_WIDTH 32 CONFIG.FREQ_HZ $f] [get_bd_intf_ports M_REGS]
    create_bd_intf_port -mode Slave -vlnv xilinx.com:interface:axis_rtl:1.0 S_VIDEO
    set_property -dict [list CONFIG.TDATA_NUM_BYTES 8 CONFIG.TUSER_WIDTH 1 CONFIG.HAS_TLAST 1 CONFIG.HAS_TKEEP 1 \
        CONFIG.HAS_TREADY 1 CONFIG.FREQ_HZ $f] [get_bd_intf_ports S_VIDEO]
    set_property -dict [list CONFIG.ASSOCIATED_BUSIF {M_REGS:S_VIDEO} CONFIG.FREQ_HZ $f] [get_bd_ports aclk]
    connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_ports aclk]
    connect_bd_net [get_bd_pins rst_100M/peripheral_aresetn] [get_bd_ports aresetn]
    connect_bd_intf_net [get_bd_intf_ports S_VIDEO] [get_bd_intf_pins vdma/S_AXIS_S2MM]
    return [list {M_REGS vdma/S_AXI_LITE} {vdma/M_AXI_S2MM} {vdma/s2mm_introut}]
}

# next: acoustic_path (line sensor ADC -> 1D CNN -> DMA)
set subsystems {temperature_path}
if {$cam} {
    set subsystems [linsert $subsystems 0 camera_path]   ;# first: its VDMA irq stays pl_ps_irq0[0]
    add_files [glob $root/src/rtl/cam/*.v]
    add_files -fileset constrs_1 $root/build/vivado/fz3b_cam.xdc
}
if {[env_on FSF_VIDEO]} {
    add_files [glob $root/src/rtl/video/*.v] $root/src/rtl/video/heat_lut.mem $root/src/rtl/video/font12x24.mem
    add_files -fileset constrs_1 $root/build/vivado/video_overlay_cdc.xdc
    set_property USED_IN_SYNTHESIS false [get_files video_overlay_cdc.xdc]   ;# names exist after BD synthesis
    lappend subsystems video_path
}

# ---------------- shell wiring ----------------
set ctrl {}; set mem {}; set irqs {}
foreach sub $subsystems {
    lassign [$sub] c m i
    lappend ctrl {*}$c; lappend mem {*}$m; lappend irqs {*}$i
}

# HPM0 -> register slaves
set sc_reg [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect sc_reg]
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI [llength $ctrl]] $sc_reg
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_FPD] [get_bd_intf_pins sc_reg/S00_AXI]
set k 0
proc bd_intf {name} {   ;# "cell/pin" -> BD pin, bare name -> external BD port
    if {[string first / $name] >= 0} { return [get_bd_intf_pins $name] }
    return [get_bd_intf_ports $name]
}
foreach s $ctrl {
    connect_bd_intf_net [get_bd_intf_pins sc_reg/M[format %02d $k]_AXI] [bd_intf $s]
    incr k
}

# memory masters -> HP0 (DDR)
set sc_mem [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect sc_mem]
set_property -dict [list CONFIG.NUM_SI [llength $mem] CONFIG.NUM_MI 1] $sc_mem
set k 0
foreach m $mem {
    connect_bd_intf_net [get_bd_intf_pins $m] [get_bd_intf_pins sc_mem/S[format %02d $k]_AXI]
    incr k
}
connect_bd_intf_net [get_bd_intf_pins sc_mem/M00_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]

# interrupts -> pl_ps_irq0
set irq_concat [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat irq_concat]
set_property CONFIG.NUM_PORTS [llength $irqs] $irq_concat
set k 0
foreach i $irqs {
    connect_bd_net [get_bd_pins $i] [get_bd_pins irq_concat/In$k]
    incr k
}
connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins ps/pl_ps_irq0]

# every clock / reset input still open goes to the 100 MHz domain
foreach pin [get_bd_pins -hier -filter {DIR == I && TYPE == clk}] {
    if {[llength [get_bd_nets -quiet -of_objects $pin]] == 0} {
        connect_bd_net [get_bd_pins ps/pl_clk0] $pin
    }
}
# module-reference resets: Vivado guesses the polarity from the port name
# (rst -> ACTIVE_LOW), so check every one against the polarity the RTL declares
foreach {cell port pol} {temp_link rst ACTIVE_HIGH} {
    set p [get_bd_pins -quiet $cell/$port]
    if {[llength $p] && [get_property CONFIG.POLARITY $p] ne $pol} {
        error "$cell/$port polarity is [get_property CONFIG.POLARITY $p], RTL expects $pol"
    }
}
foreach pin [get_bd_pins -hier -filter {DIR == I && TYPE == rst}] {
    if {[string match /rst_100M/* $pin]} continue    ; # its spare inputs stay open
    if {[llength [get_bd_nets -quiet -of_objects $pin]] == 0} {
        set low [expr {[get_property CONFIG.POLARITY $pin] eq "ACTIVE_LOW"}]
        connect_bd_net [get_bd_pins rst_100M/[expr {$low ? "peripheral_aresetn" : "peripheral_reset"}]] $pin
    }
}

assign_bd_address
if {$cam} {   ;# fixed map: the camera software has these bases hard-coded
    # unassign everything, then place each slave segment (moving in place can collide)
    set slaves {}
    foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data]] {
        lappend slaves [get_property NAME $seg] [get_bd_addr_segs -of_objects $seg]
    }
    delete_bd_objs [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data]]
    foreach {seg_name slave} $slaves {
        set base ""
        foreach {pat b} {*M_REGS* 0xA0000000 *vdma* 0xA0010000 *temp_dma* 0xA0020000 *ovl* 0xA0030000} {
            if {[string match $pat $seg_name]} { set base $b; break }
        }
        if {$base eq ""} { error "no fixed address for $seg_name" }
        assign_bd_address -target_address_space [get_bd_addr_spaces ps/Data] -offset $base -range 64K $slave
    }
}
file mkdir $out
set f [open $out/address_map.txt w]                  ;# register bases for the board software
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data]] {
    puts $f [format "%-40s 0x%08X  %s" [get_property NAME $seg] [get_property OFFSET $seg] [get_property RANGE $seg]]
}
close $f
validate_bd_design
save_bd_design
make_wrapper -files [get_files $bd.bd] -top
add_files [glob $out/project/$name.gen/sources_1/bd/$bd/hdl/${bd}_wrapper.v]
set_property top [expr {$cam ? "fz3b_cam_top" : "${bd}_wrapper"}] [current_fileset]

set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]   ;# .bin for Linux fpga_manager
implement_and_report $out $name $bd
