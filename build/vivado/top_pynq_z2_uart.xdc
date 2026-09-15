# PYNQ-Z2 pins (TUL master XDC / PYNQ base.xdc)
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports sysclk]
set_property -dict {PACKAGE_PIN D19 IOSTANDARD LVCMOS33} [get_ports btn0]
set_property -dict {PACKAGE_PIN Y18 IOSTANDARD LVCMOS33} [get_ports uart_rxd]
set_property -dict {PACKAGE_PIN Y19 IOSTANDARD LVCMOS33} [get_ports uart_txd]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33} [get_ports {led[1]}]

create_clock -period 8.000 -name sysclk [get_ports sysclk]
# async inputs and slow outputs
set_false_path -from [get_ports {btn0 uart_rxd}]
set_false_path -to [get_ports {uart_txd led[*]}]
