# Shared by all board builds: sources and post-implementation reports.

# QCNN core, DMA stream link, generated ROMs and the open-source RTL they use
proc add_qcnn_sources {root} {
    add_files [glob $root/src/rtl/qcnn/*.v $root/src/rtl/link/*.v]
    add_files [glob $root/src/rtl/gen/*.mem] $root/src/rtl/gen/qcnn_params.vh
    add_files $root/refs/verilog-axis/rtl/axis_fifo.v
    set_property include_dirs $root/src/rtl/gen [current_fileset]
}

# implement, then write bitstream, .hwh (if a block design exists) and reports into $out
proc implement_and_report {out name {bd ""}} {
    launch_runs impl_1 -to_step write_bitstream -jobs 8
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} { error "impl_1 failed" }
    open_run impl_1
    file mkdir $out/reports
    report_utilization    -file $out/reports/utilization.rpt
    report_utilization    -hierarchical -hierarchical_depth 4 -file $out/reports/utilization_hier.rpt
    report_timing_summary -file $out/reports/timing_summary.rpt
    report_power          -file $out/reports/power.rpt
    report_cdc            -file $out/reports/cdc.rpt
    report_methodology    -file $out/reports/methodology.rpt
    set run_dir [get_property DIRECTORY [get_runs impl_1]]
    file copy -force [glob $run_dir/*.bit] $out/$name.bit
    foreach bin [glob -nocomplain $run_dir/*.bin] { file copy -force $bin $out/$name.bin }   ;# for Linux fpga_manager
    if {$bd ne ""} {
        file copy -force [glob [get_property DIRECTORY [current_project]]/*.gen/sources_1/bd/$bd/hw_handoff/$bd.hwh] $out/$name.hwh
    }
}
