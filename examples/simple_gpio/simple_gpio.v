module simple_gpio #(
    parameter ADDR_WIDTH = 6,
    parameter DATA_WIDTH = 32
) (
    input wire                       s_axi_aclk,
    input wire                       s_axi_aresetn,
    input wire [ADDR_WIDTH-1:0]      s_axi_awaddr,
    input wire                       s_axi_awvalid,
    output wire                      s_axi_awready,
    input wire [DATA_WIDTH-1:0]      s_axi_wdata,
    input wire [(DATA_WIDTH/8)-1:0]  s_axi_wstrb,
    input wire                       s_axi_wvalid,
    output wire                      s_axi_wready,
    output wire [1:0]                s_axi_bresp,
    output wire                      s_axi_bvalid,
    input wire                       s_axi_bready,
    input wire [ADDR_WIDTH-1:0]      s_axi_araddr,
    input wire                       s_axi_arvalid,
    output wire                      s_axi_arready,
    output wire [DATA_WIDTH-1:0]     s_axi_rdata,
    output wire [1:0]                s_axi_rresp,
    output wire                      s_axi_rvalid,
    input wire                       s_axi_rready,
    output wire [7:0]                gpio_out
);
    reg [DATA_WIDTH-1:0] gpio_reg;
    reg write_pending;
    reg read_pending;
    assign s_axi_awready = !write_pending;
    assign s_axi_wready = !write_pending;
    assign s_axi_bresp = 2'b00;
    assign s_axi_bvalid = write_pending;
    assign s_axi_arready = !read_pending;
    assign s_axi_rdata = gpio_reg;
    assign s_axi_rresp = 2'b00;
    assign s_axi_rvalid = read_pending;
    assign gpio_out = gpio_reg[7:0];

    integer i;
    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            gpio_reg <= 0;
            write_pending <= 0;
            read_pending <= 0;
        end else begin
            if (!write_pending && s_axi_awvalid && s_axi_wvalid) begin
                for (i = 0; i < DATA_WIDTH/8; i = i + 1)
                    if (s_axi_wstrb[i]) gpio_reg[i*8 +: 8] <= s_axi_wdata[i*8 +: 8];
                write_pending <= 1;
            end else if (write_pending && s_axi_bready)
                write_pending <= 0;
            if (!read_pending && s_axi_arvalid)
                read_pending <= 1;
            else if (read_pending && s_axi_rready)
                read_pending <= 0;
        end
    end
endmodule
