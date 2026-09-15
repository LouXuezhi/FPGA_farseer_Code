# PYNQ-Z2 build: vivado -mode batch -source build/vivado/build_pynq_z2.tcl -tclargs <uart|dma>
#   uart : PL-only top, UART on Pmod JA
#   dma  : Zynq PS7 + AXI DMA + axis_frame_link, bitstream + .hwh for PYNQ
set variant [lindex $argv 0]
set root    [file normalize [file dirname [info script]]/../..]
set out     $root/build/vivado/$variant
set part    xc7z020clg400-1

create_project -force qcnn_$variant $out/project -part $part
add_files [glob $root/src/rtl/qcnn/*.v $root/src/rtl/link/*.v]
add_files [glob $root/src/rtl/gen/*.mem] $root/src/rtl/gen/qcnn_params.vh
add_files $root/refs/verilog-uart/rtl/uart.v $root/refs/verilog-uart/rtl/uart_rx.v \
          $root/refs/verilog-uart/rtl/uart_tx.v $root/refs/verilog-axis/rtl/axis_fifo.v
set_property include_dirs $root/src/rtl/gen [current_fileset]

if {$variant eq "uart"} {
    add_files $root/src/rtl/top/top_pynq_z2_uart.v
    add_files -fileset constrs_1 $root/build/vivado/top_pynq_z2_uart.xdc
    set_property top top_pynq_z2_uart [current_fileset]
} else {
    # ---------------- block design: PS7 + DMA + frame link ----------------
    create_bd_design qcnn_dma
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7]
    set_property -dict [list CONFIG.PCW_USE_S_AXI_HP0 1 CONFIG.PCW_USE_FABRIC_INTERRUPT 1 \
        CONFIG.PCW_IRQ_F2P_INTR 1 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 100] $ps
    set dma [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dma dma]
    set_property -dict [list CONFIG.c_include_sg 0 CONFIG.c_sg_length_width 26 \
        CONFIG.c_m_axis_mm2s_tdata_width 8] $dma
    set link [create_bd_cell -type module -reference axis_frame_link link]
    set irq  [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconcat irq_concat]

    make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR] [get_bd_intf_pins ps7/FIXED_IO]
    connect_bd_intf_net [get_bd_intf_pins dma/M_AXIS_MM2S] [get_bd_intf_pins link/s_axis]
    connect_bd_intf_net [get_bd_intf_pins link/m_axis] [get_bd_intf_pins dma/S_AXIS_S2MM]
    connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins link/clk]
    connect_bd_net [get_bd_pins dma/mm2s_introut] [get_bd_pins irq_concat/In0]
    connect_bd_net [get_bd_pins dma/s2mm_introut] [get_bd_pins irq_concat/In1]
    connect_bd_net [get_bd_pins irq_concat/dout] [get_bd_pins ps7/IRQ_F2P]
    # GP0 -> DMA control, DMA masters -> HP0 (automation adds interconnects and resets)
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master /ps7/M_AXI_GP0 Clk Auto} [get_bd_intf_pins dma/S_AXI_LITE]
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Slave /ps7/S_AXI_HP0 Clk Auto} [get_bd_intf_pins dma/M_AXI_MM2S]
    apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Slave /ps7/S_AXI_HP0 Clk Auto} [get_bd_intf_pins dma/M_AXI_S2MM]
    connect_bd_net [get_bd_pins link/rst] [get_bd_pins rst_ps7_100M/peripheral_reset]
    validate_bd_design
    save_bd_design
    make_wrapper -files [get_files qcnn_dma.bd] -top
    add_files $out/project/qcnn_$variant.gen/sources_1/bd/qcnn_dma/hdl/qcnn_dma_wrapper.v
    set_property top qcnn_dma_wrapper [current_fileset]
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
open_run impl_1
file mkdir $out/reports
report_utilization    -file $out/reports/utilization.rpt
report_utilization    -hierarchical -hierarchical_depth 4 -file $out/reports/utilization_hier.rpt
report_timing_summary -file $out/reports/timing_summary.rpt
report_power          -file $out/reports/power.rpt
file copy -force [glob $out/project/qcnn_$variant.runs/impl_1/*.bit] $out/qcnn_$variant.bit
if {$variant eq "dma"} {
    file copy -force $out/project/qcnn_$variant.gen/sources_1/bd/qcnn_dma/hw_handoff/qcnn_dma.hwh $out/qcnn_$variant.hwh
}
