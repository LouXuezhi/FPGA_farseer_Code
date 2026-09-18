# FZ3B (EdgeBoard, Zynq UltraScale+ ZU3EG, 4 GB DDR4) build:
#   vivado -mode batch -source build/vivado/build_fz3b.tcl [-tclargs <part>]
# PS runs Linux; the PL holds the QCNN temperature path behind an AXI DMA.
#
# The block design is a shell plus subsystems. Each subsystem creates its cells
# and returns what it needs from the shell:
#   {control slaves on HPM0}  {memory masters to HP0 (DDR)}  {interrupts}
# The shell wires those up, then ties every still-unconnected clock / reset
# input to pl_clk0 (100 MHz) and its reset. New paths (acoustic line-sensor
# CNN, DisplayPort live video) are added as further subsystems.
set root [file normalize [file dirname [info script]]/../..]
set part [expr {$argc > 0 ? [lindex $argv 0] : "xazu3eg-sfvc784-1-i"}]   ; # check the chip marking
set out  $root/build/vivado/fz3b
source $root/build/vivado/qcnn_common.tcl

create_project -force qcnn_fz3b $out/project -part $part
add_qcnn_sources $root
create_bd_design qcnn_zu

# ---------------- shell: PS, 100 MHz fabric clock, reset ----------------
# HPM0_FPD = register access, HP0_FPD = DMA to DDR, pl_ps_irq0 = interrupts.
# DDR / MIO settings do not matter here: Linux (booted by the board image)
# loads this bitstream at run time and keeps its own PS configuration.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e ps]
set_property -dict [list \
    CONFIG.PSU__USE__M_AXI_GP0 1 CONFIG.PSU__USE__M_AXI_GP1 0 CONFIG.PSU__USE__M_AXI_GP2 0 \
    CONFIG.PSU__USE__S_AXI_GP2 1 CONFIG.PSU__USE__IRQ0 1 \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ 100] $ps
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

# next: acoustic_path (line sensor ADC -> 1D CNN -> DMA), video_path (DP live video)
set subsystems {temperature_path}

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
foreach s $ctrl {
    connect_bd_intf_net [get_bd_intf_pins sc_reg/M[format %02d $k]_AXI] [get_bd_intf_pins $s]
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
foreach pin [get_bd_pins -hier -filter {DIR == I && TYPE == rst}] {
    if {[string match /rst_100M/* $pin]} continue    ; # its spare inputs stay open
    if {[llength [get_bd_nets -quiet -of_objects $pin]] == 0} {
        set low [expr {[get_property CONFIG.POLARITY $pin] eq "ACTIVE_LOW"}]
        connect_bd_net [get_bd_pins rst_100M/[expr {$low ? "peripheral_aresetn" : "peripheral_reset"}]] $pin
    }
}

assign_bd_address
file mkdir $out
set f [open $out/address_map.txt w]                  ;# register bases for the board software
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data]] {
    puts $f [format "%-40s 0x%08X  %s" [get_property NAME $seg] [get_property OFFSET $seg] [get_property RANGE $seg]]
}
close $f
validate_bd_design
save_bd_design
make_wrapper -files [get_files qcnn_zu.bd] -top
add_files [glob $out/project/qcnn_fz3b.gen/sources_1/bd/qcnn_zu/hdl/qcnn_zu_wrapper.v]
set_property top qcnn_zu_wrapper [current_fileset]

set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]   ;# .bin for Linux fpga_manager
implement_and_report $out qcnn_fz3b qcnn_zu
