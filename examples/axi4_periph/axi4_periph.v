module axi4_periph #(
    parameter AW = 6,
    parameter DW = 32
) (
    input  wire             aclk,
    input  wire             aresetn,
    // Write address channel
    input  wire [AW-1:0]     awaddr,
    input  wire [7:0]        awlen,
    input  wire [2:0]        awsize,
    input  wire [1:0]        awburst,
    input  wire              awvalid,
    output wire              awready,
    // Write data channel
    input  wire [DW-1:0]     wdata,
    input  wire [DW/8-1:0]   wstrb,
    input  wire              wlast,
    input  wire              wvalid,
    output wire              wready,
    // Write response channel
    output wire [1:0]        bresp,
    output wire              bvalid,
    input  wire              bready,
    // Read address channel
    input  wire [AW-1:0]     araddr,
    input  wire [7:0]        arlen,
    input  wire [2:0]        arsize,
    input  wire [1:0]        arburst,
    input  wire              arvalid,
    output wire              arready,
    // Read data channel
    output wire [DW-1:0]     rdata,
    output wire [1:0]        rresp,
    output wire              rlast,
    output wire              rvalid,
    input  wire              rready,
    // Status output
    output wire [7:0]        status_out
);
    reg [DW-1:0] gpio_reg;
    reg write_pending;
    reg read_pending;
    reg [DW-1:0] read_data;

    assign awready = !write_pending;
    assign wready  = !write_pending;
    assign bresp   = 2'b00;
    assign bvalid  = write_pending;
    assign arready = !read_pending;
    assign rdata   = read_data;
    assign rresp   = 2'b00;
    assign rlast   = read_pending;
    assign rvalid  = read_pending;
    assign status_out = gpio_reg[7:0];

    integer i;
    always @(posedge aclk) begin
        if (!aresetn) begin
            gpio_reg      <= 0;
            write_pending <= 0;
            read_pending  <= 0;
            read_data     <= 0;
        end else begin
            if (!write_pending && awvalid && wvalid) begin
                for (i = 0; i < DW/8; i = i + 1)
                    if (wstrb[i]) gpio_reg[i*8 +: 8] <= wdata[i*8 +: 8];
                write_pending <= 1;
            end else if (write_pending && bready)
                write_pending <= 0;

            if (!read_pending && arvalid) begin
                read_data    <= gpio_reg;
                read_pending <= 1;
            end else if (read_pending && rready)
                read_pending <= 0;
        end
    end
endmodule
