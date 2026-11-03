// line_fetch: one image row from DDR per video line, into a dual-clock ping-pong buffer.
// Used twice: the background (RGBW8888, whole line) and the camera crop (RAW8).
//
//   vclk: at the first blanking pixel of a line, if the next line shows image
//         row r (y0 <= next y < y0 + rows), request r: row + toggle, held until
//         the next request (one video line), so the aclk side reads it as a bus
//   aclk: fetch n_burst x 128 bytes at addr[store] + r * pitch into bank r[0];
//         at row 0 pick the store written just before frame_ptr (VDMA: never the
//         one being written) — with nfs = 1 always store 0 (static images)
//   vclk: rd_word -> rd_data two cycles later (address registered, then data)
// A request arriving while the previous fetch is still running is counted as
// late and skipped (the old row stays on screen); fetches take ~1-6 us of a
// 15-32 us line.
`timescale 1ns / 1ps

module line_fetch #(
    parameter WORDS  = 64,                     // 64-bit words per bank, power of two
    parameter MAX_BURST = 7                    // n_burst max, < 2^5
) (
    input  wire        aclk,
    input  wire        aresetn,
    output reg  [31:0] m_axi_araddr,
    output wire [7:0]  m_axi_arlen,
    output wire [2:0]  m_axi_arsize,
    output wire [1:0]  m_axi_arburst,
    output wire [3:0]  m_axi_arcache,
    output wire [2:0]  m_axi_arprot,
    output reg         m_axi_arvalid,
    input  wire        m_axi_arready,
    input  wire [63:0] m_axi_rdata,
    input  wire        m_axi_rvalid,
    output wire        m_axi_rready,
    input  wire [31:0] addr0, addr1, addr2, addr3,
    input  wire [15:0] pitch,
    input  wire [2:0]  nfs,
    input  wire [4:0]  n_burst,                // bursts of 16 x 64 bit per row
    input  wire [5:0]  frame_ptr,
    output wire [31:0] stat,                   // {late, lines}
    input  wire        vclk,
    input  wire        vrst,
    input  wire        en,
    input  wire [11:0] x, y, h_active, v_total, y0, rows,
    input  wire        rd_bank,                // row parity of the line being shown
    input  wire [$clog2(WORDS)-1:0] rd_word,
    output reg  [63:0] rd_data
);

    localparam WB = $clog2(WORDS);

    assign m_axi_arlen   = 8'd15;
    assign m_axi_arsize  = 3'd3;
    assign m_axi_arburst = 2'b01;
    assign m_axi_arcache = 4'b0011;
    assign m_axi_arprot  = 3'b000;
    assign m_axi_rready  = 1'b1;

    (* ram_style = "block" *) reg [63:0] line_ram [0:2*WORDS-1];

    // ---------------- vclk: requests ----------------
    wire [11:0] ny   = (y >= v_total - 1) ? 12'd0 : y + 1;
    wire [11:0] nrow = ny - y0;
    wire        nin  = en && (ny >= y0) && (ny < y0 + rows);
    (* DONT_TOUCH = "TRUE" *) reg [11:0] req_row_q;
    (* DONT_TOUCH = "TRUE" *) reg        req_first_q;
    reg         req_tgl_q;
    always @(posedge vclk) begin
        if (vrst) req_tgl_q <= 1'b0;
        else if (x == h_active && nin) begin
            req_row_q   <= nrow;
            req_first_q <= (nrow == 0);
            req_tgl_q   <= ~req_tgl_q;
        end
    end

    // ---------------- aclk: fetch ----------------
    (* ASYNC_REG = "TRUE" *) reg [1:0] req_s;
    (* DONT_TOUCH = "TRUE" *) reg [11:0] row_q;
    (* DONT_TOUCH = "TRUE" *) reg        first_q;
    reg         req_d_q, busy_q, start_q, start2_q;
    reg  [27:0] row_off_q;
    reg  [1:0]  fs_q;
    reg  [4:0]  ar_cnt_q;
    reg  [WB-1:0] beat_q;
    reg  [15:0] lines_q, late_q;
    wire        req_new = req_s[1] ^ req_d_q;
    wire [2:0]  ptr     = frame_ptr[2:0];
    wire [2:0]  prev_fs = (nfs <= 1) ? 3'd0 : (ptr == 0 || ptr >= nfs) ? nfs - 3'd1 : ptr - 3'd1;
    wire [9:0]  last_beat = {n_burst, 4'b0000} - 10'd1;        // n_burst * 16 - 1
    reg  [31:0] fs_addr;
    always @* case (fs_q)
        2'd0: fs_addr = addr0;  2'd1: fs_addr = addr1;
        2'd2: fs_addr = addr2;  default: fs_addr = addr3;
    endcase
    assign stat = {late_q, lines_q};

    always @(posedge aclk) begin
        req_s   <= {req_s[0], req_tgl_q};
        req_d_q <= req_s[1];
        if (!aresetn) begin
            {busy_q, start_q, start2_q, m_axi_arvalid} <= 4'b0000;
            {lines_q, late_q} <= 32'd0;
            fs_q <= 2'd0;
        end else begin
            start_q  <= 1'b0;
            start2_q <= start_q;
            if (req_new) begin
                if (busy_q) late_q <= late_q + 1;
                else begin
                    row_q   <= req_row_q;                 // held since the toggle (CDC bus)
                    first_q <= req_first_q;
                    busy_q  <= 1'b1;
                    start_q <= 1'b1;
                    beat_q  <= 0;
                end
            end
            if (start_q) row_off_q <= row_q * pitch;
            if (start_q && first_q) fs_q <= prev_fs[1:0];
            if (start2_q) begin
                m_axi_araddr  <= fs_addr + row_off_q;
                m_axi_arvalid <= 1'b1;
                ar_cnt_q      <= 0;
            end else if (m_axi_arvalid && m_axi_arready) begin
                ar_cnt_q <= ar_cnt_q + 1;
                if (ar_cnt_q == n_burst - 1) m_axi_arvalid <= 1'b0;
                else                         m_axi_araddr  <= m_axi_araddr + 32'd128;
            end
            if (busy_q && m_axi_rvalid) begin
                beat_q <= beat_q + 1;
                if (beat_q == last_beat[WB-1:0]) begin   // WB <= 10, n_burst * 16 <= WORDS
                    busy_q  <= 1'b0;
                    lines_q <= lines_q + 1;
                end
            end
        end
    end
    always @(posedge aclk)
        if (busy_q && m_axi_rvalid) line_ram[{row_q[0], beat_q}] <= m_axi_rdata;

    // ---------------- vclk: read ----------------
    reg [WB:0] rd_addr_q;
    always @(posedge vclk) begin
        rd_addr_q <= {rd_bank, rd_word};
        rd_data   <= line_ram[rd_addr_q];
    end

endmodule
