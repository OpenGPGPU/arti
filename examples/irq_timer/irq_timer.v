module irq_timer #(
    parameter AW = 4,
    parameter DW = 32
) (
    input  wire             s_axi_aclk,
    input  wire             s_axi_aresetn,
    input  wire [AW-1:0]    s_axi_awaddr,
    input  wire             s_axi_awvalid,
    output wire             s_axi_awready,
    input  wire [DW-1:0]    s_axi_wdata,
    input  wire [DW/8-1:0]  s_axi_wstrb,
    input  wire             s_axi_wvalid,
    output wire             s_axi_wready,
    output wire [1:0]       s_axi_bresp,
    output wire             s_axi_bvalid,
    input  wire             s_axi_bready,
    input  wire [AW-1:0]    s_axi_araddr,
    input  wire             s_axi_arvalid,
    output wire             s_axi_arready,
    output wire [DW-1:0]    s_axi_rdata,
    output wire [1:0]       s_axi_rresp,
    output wire             s_axi_rvalid,
    input  wire             s_axi_rready,
    // Interrupt output
    output wire             irq
);
    reg [DW-1:0] reload_value;
    reg [DW-1:0] counter;
    reg          irq_flag;
    reg          write_pending;
    reg          read_pending;
    reg [DW-1:0] read_data;
    reg          irq_enable;

    assign s_axi_awready = !write_pending;
    assign s_axi_wready  = !write_pending;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bvalid  = write_pending;
    assign s_axi_arready = !read_pending;
    assign s_axi_rdata   = read_data;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rvalid  = read_pending;
    assign irq           = irq_flag & irq_enable;

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            reload_value  <= 32'd100;
            counter       <= 32'd100;
            irq_flag      <= 0;
            irq_enable    <= 0;
            write_pending <= 0;
            read_pending  <= 0;
            read_data     <= 0;
        end else begin
            // Timer countdown
            if (counter > 0) begin
                counter <= counter - 1;
                if (counter == 1) begin
                    irq_flag <= 1;
                    counter  <= reload_value;
                end
            end

            // AXI-Lite write
            if (!write_pending && s_axi_awvalid && s_axi_wvalid) begin
                case (s_axi_awaddr[3:2])
                    2'd0: begin reload_value <= s_axi_wdata; counter <= s_axi_wdata; end
                    2'd1: irq_enable  <= s_axi_wdata[0];
                    2'd2: irq_flag    <= 0;  // clear IRQ
                    default: ;
                endcase
                write_pending <= 1;
            end else if (write_pending && s_axi_bready)
                write_pending <= 0;

            // AXI-Lite read
            if (!read_pending && s_axi_arvalid) begin
                case (s_axi_araddr[3:2])
                    2'd0: read_data <= reload_value;
                    2'd1: read_data <= {31'b0, irq_enable};
                    2'd2: read_data <= {31'b0, irq_flag};
                    2'd3: read_data <= counter;
                    default: read_data <= 0;
                endcase
                read_pending <= 1;
            end else if (read_pending && s_axi_rready)
                read_pending <= 0;
        end
    end
endmodule
