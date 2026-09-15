// top_pynq_z2_uart: PL-only build, UART on Pmod JA (USB-UART adapter, 3 Mbps).
//   sysclk 125 MHz -> MMCM -> 96 MHz (= 3 Mbps * 8 * 4, exact baud)
//   btn0 = reset, led0 = rx_error, led1 = MMCM locked
`timescale 1ns / 1ps

module top_pynq_z2_uart (
    input  wire       sysclk,       // H16
    input  wire       btn0,         // D19
    input  wire       uart_rxd,     // JA1 Y18, from adapter TXD
    output wire       uart_txd,     // JA2 Y19, to adapter RXD
    output wire [1:0] led
);

    // ---------------- clock: 125 * 48 / 5 / 12.5 = 96 MHz ----------------
    wire clk_96m_raw, clk_fb, mmcm_locked, clk;
    MMCME2_BASE #(
        .CLKIN1_PERIOD(8.0), .DIVCLK_DIVIDE(5), .CLKFBOUT_MULT_F(48.0), .CLKOUT0_DIVIDE_F(12.5)
    ) u_mmcm (
        .CLKIN1(sysclk), .CLKFBIN(clk_fb), .CLKFBOUT(clk_fb), .CLKOUT0(clk_96m_raw),
        .LOCKED(mmcm_locked), .PWRDWN(1'b0), .RST(1'b0),
        .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
        .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6()
    );
    BUFG u_bufg (.I(clk_96m_raw), .O(clk));

    // ---------------- reset: async assert, sync release ----------------
    reg [2:0] rst_sync_q;
    always @(posedge clk or negedge mmcm_locked)
        if (!mmcm_locked) rst_sync_q <= 3'b111;
        else              rst_sync_q <= {rst_sync_q[1:0], btn0};
    wire rst = rst_sync_q[2];

    // ---------------- rxd synchronizer ----------------
    reg [1:0] rxd_sync_q;
    always @(posedge clk) rxd_sync_q <= {rxd_sync_q[0], uart_rxd};

    wire rx_error;
    uart_frame_link #(.PRESCALE(4)) u_link (
        .clk(clk), .rst(rst), .uart_rxd(rxd_sync_q[1]), .uart_txd(uart_txd), .rx_error(rx_error)
    );

    assign led = {mmcm_locked, rx_error};

endmodule
