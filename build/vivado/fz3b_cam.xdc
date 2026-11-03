# FZ3B OV9281 capture constraints (pins from FZ3B-2019-11-15 schematic p5/p6 and hardware manual sec.14/19)
# MIPI D-PHY, bank 64 (component-mode receiver)
set_property PACKAGE_PIN AD5 [get_ports clk_rxp]
set_property PACKAGE_PIN AD4 [get_ports clk_rxn]
set_property PACKAGE_PIN AC4 [get_ports {data_rxp[0]}]
set_property PACKAGE_PIN AC3 [get_ports {data_rxn[0]}]
set_property PACKAGE_PIN AB4 [get_ports {data_rxp[1]}]
set_property PACKAGE_PIN AB3 [get_ports {data_rxn[1]}]
set_property IOSTANDARD MIPI_DPHY_DCI [get_ports {clk_rxp clk_rxn data_rxp[*] data_rxn[*]}]
create_clock -period 2.500 -name mipi_hs_clk [get_ports clk_rxp]
set_false_path -from [get_ports {data_rxp[*] data_rxn[*]}]
set_property PACKAGE_PIN D4 [get_ports pl_ref_clk]
set_property IOSTANDARD LVCMOS18 [get_ports pl_ref_clk]
create_clock -period 40.000 -name pl_ref_clk [get_ports pl_ref_clk]

set_property PACKAGE_PIN L7 [get_ports cam_mclk]
set_property PACKAGE_PIN U9 [get_ports cam_rst_n]
set_property PACKAGE_PIN Y8 [get_ports cam_pwdn_n]
set_property IOSTANDARD LVCMOS18 [get_ports {cam_mclk cam_rst_n cam_pwdn_n}]
set_property DRIVE 8 [get_ports {cam_mclk cam_rst_n cam_pwdn_n}]
set_property SLEW SLOW [get_ports {cam_rst_n cam_pwdn_n}]


# async crossings
set_false_path -to [get_cells -hier -filter {NAME =~ *mclk_en_s_reg[0]*}]
set_false_path -to [get_cells -hier -filter {NAME =~ *ecc_en_s_reg[0]*}]
set_false_path -to [get_cells -hier -filter {NAME =~ *stream_en_s_reg[0]*}]
set_false_path -to [get_cells -hier -filter {NAME =~ *swap_s_reg[0]*}]
set_false_path -to [get_cells -hier -filter {NAME =~ *tgl_s_reg[0]*}]
set_false_path -to [get_cells -hier -filter {NAME =~ *lock_s_reg[0]*}]
set_max_delay -datapath_only 8.0 -from [get_cells -hier -filter {NAME =~ *sh_reg*}] -to [get_cells -hier -filter {NAME =~ *st_reg*}]
set_false_path -from [get_cells -hier -filter {NAME =~ *regs/ctrl_reg[0]}]
# HS receiver enable is quasi-static: sync search starts >=3 byte clocks after it toggles
set_false_path -from [get_cells -hier -filter {NAME =~ *dphy/al*/term_dis_reg}]
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks mipi_hs_clk] -group [get_clocks -of [get_pins -hier -filter {NAME =~ *ps/inst/PS8_i/PLCLK[0]}]] -group [get_clocks -include_generated_clocks pl_ref_clk]
