// A simple AXI-Lite register file with 4 registers.
// Uses m_axi_ prefix (different from simple_gpio's s_axi_) to test
// that the framework adapts to different port naming conventions.
module reg_file #(
    parameter AW = 4,
    parameter DW = 32
) (
    input  wire             m_axi_aclk,
    input  wire             m_axi_aresetn,
    input  wire [AW-1:0]    m_axi_awaddr,
    input  wire             m_axi_awvalid,
    output wire             m_axi_awready,
    input  wire [DW-1:0]    m_axi_wdata,
    input  wire [DW/8-1:0]   m_axi_wstrb,
    input  wire             m_axi_wvalid,
    output wire             m_axi_wready,
    output wire [1:0]       m_axi_bresp,
    output wire             m_axi_bvalid,
    input  wire             m_axi_bready,
    input  wire [AW-1:0]    m_axi_araddr,
    input  wire             m_axi_arvalid,
    output wire             m_axi_arready,
    output wire [DW-1:0]    m_axi_rdata,
    output wire [1:0]       m_axi_rresp,
    output wire             m_axi_rvalid,
    input  wire             m_axi_rready
);
    reg [DW-1:0] regs [0:3];
    reg           wr_pending;
    reg           rd_pending;
    reg [AW-1:0]  rd_addr;

    assign m_axi_awready = !wr_pending;
    assign m_axi_wready  = !wr_pending;
    assign m_axi_bresp   = 2'b00;
    assign m_axi_bvalid  = wr_pending;
    assign m_axi_arready = !rd_pending;
    assign m_axi_rdata   = regs[rd_addr[AW-1:2]];
    assign m_axi_rresp   = 2'b00;
    assign m_axi_rvalid  = rd_pending;

    integer i;
    always @(posedge m_axi_aclk) begin
        if (!m_axi_aresetn) begin
            for (i = 0; i < 4; i = i + 1)
                regs[i] <= 0;
            wr_pending <= 0;
            rd_pending <= 0;
        end else begin
            if (!wr_pending && m_axi_awvalid && m_axi_wvalid) begin
                for (i = 0; i < DW/8; i = i + 1) begin
                    if (m_axi_wstrb[i])
                        regs[m_axi_awaddr[AW-1:2]][i*8 +: 8] <= m_axi_wdata[i*8 +: 8];
                end
                wr_pending <= 1;
            end else if (wr_pending && m_axi_bready) begin
                wr_pending <= 0;
            end
            if (!rd_pending && m_axi_arvalid) begin
                rd_addr    <= m_axi_araddr;
                rd_pending <= 1;
            end else if (rd_pending && m_axi_rready) begin
                rd_pending <= 0;
            end
        end
    end
endmodule
