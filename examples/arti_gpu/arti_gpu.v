// SPDX-License-Identifier: GPL-2.0
// Minimal ARTI GPU scanout control block.
//
// The QEMU display wrapper owns the guest-visible framebuffer memory. This
// RTL owns the control ABI and VSYNC status/interrupt path that a real GPU
// display controller can replace without changing the Linux handoff.
module arti_gpu #(
    parameter AW = 8,
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
    output wire             irq
);
    localparam [31:0] GPU_ID      = 32'h4152_5449;
    localparam [31:0] GPU_VERSION = 32'h0001_0000;
    localparam [31:0] FORMAT_A8R8G8B8 = 32'd1;
    localparam [31:0] CONTROL_ENABLE = 32'h0000_0001;
    localparam [31:0] CONTROL_VSYNC_IRQ = 32'h0000_0002;
    localparam [31:0] IRQ_VSYNC = 32'h0000_0001;

    reg [31:0] fb_base_lo;
    reg [31:0] fb_base_hi;
    reg [31:0] width;
    reg [31:0] height;
    reg [31:0] stride;
    reg [31:0] format;
    reg [31:0] control;
    reg [31:0] irq_status;
    reg [31:0] irq_mask;
    reg [31:0] vsync_counter;
    reg        aw_pending;
    reg        w_pending;
    reg        write_pending;
    reg        read_pending;
    reg [31:0] read_data;
    reg [AW-1:0] aw_addr_reg;
    reg [DW-1:0] w_data_reg;
    reg [DW/8-1:0] w_strb_reg;

    // AXI-Lite permits AW and W to arrive independently.
    assign s_axi_awready = !aw_pending && !write_pending;
    assign s_axi_wready  = !w_pending && !write_pending;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bvalid  = write_pending;
    assign s_axi_arready = !read_pending;
    assign s_axi_rdata   = read_data;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rvalid  = read_pending;
    assign irq           = irq_status[0] & irq_mask[0] & control[1];

    function [31:0] merge_strobe;
        input [31:0] old_value;
        input [31:0] new_value;
        input [3:0]  strobe;
        integer lane;
        begin
            merge_strobe = old_value;
            for (lane = 0; lane < 4; lane = lane + 1)
                if (strobe[lane])
                    merge_strobe[lane * 8 +: 8] = new_value[lane * 8 +: 8];
        end
    endfunction

    always @(posedge s_axi_aclk) begin
        if (!s_axi_aresetn) begin
            fb_base_lo   <= 32'h0b10_0000;
            fb_base_hi   <= 32'h0000_0000;
            width        <= 32'd1024;
            height       <= 32'd768;
            stride       <= 32'd4096;
            format       <= FORMAT_A8R8G8B8;
            control      <= CONTROL_ENABLE;
            irq_status   <= 32'd0;
            irq_mask     <= 32'd0;
            vsync_counter <= 32'd0;
            aw_pending   <= 1'b0;
            w_pending    <= 1'b0;
            write_pending <= 1'b0;
            read_pending  <= 1'b0;
            aw_addr_reg   <= {AW{1'b0}};
            w_data_reg    <= {DW{1'b0}};
            w_strb_reg    <= {(DW/8){1'b0}};
            read_data     <= 32'd0;
        end else begin
            // A deterministic synthetic VSYNC source keeps the ABI testable
            // before the real pixel clock and timing generator exist.
            if (control[0] && (vsync_counter == 32'd1023)) begin
                vsync_counter <= 32'd0;
                irq_status[0] <= 1'b1;
            end else begin
                vsync_counter <= vsync_counter + 1'b1;
            end

            if (write_pending) begin
                if (s_axi_bready)
                    write_pending <= 1'b0;
            end else begin
                if (s_axi_awvalid && s_axi_awready) begin
                    aw_addr_reg <= s_axi_awaddr;
                    aw_pending <= 1'b1;
                end
                if (s_axi_wvalid && s_axi_wready) begin
                    w_data_reg <= s_axi_wdata;
                    w_strb_reg <= s_axi_wstrb;
                    w_pending <= 1'b1;
                end
                if (aw_pending && w_pending) begin
                    case (aw_addr_reg[7:2])
                        6'h04: fb_base_lo <= merge_strobe(fb_base_lo, w_data_reg, w_strb_reg);
                        6'h05: fb_base_hi <= merge_strobe(fb_base_hi, w_data_reg, w_strb_reg);
                        6'h06: width      <= merge_strobe(width, w_data_reg, w_strb_reg);
                        6'h07: height     <= merge_strobe(height, w_data_reg, w_strb_reg);
                        6'h08: stride     <= merge_strobe(stride, w_data_reg, w_strb_reg);
                        6'h09: format     <= merge_strobe(format, w_data_reg, w_strb_reg);
                        6'h0a: control    <= merge_strobe(control, w_data_reg, w_strb_reg);
                        6'h0c: irq_status <= irq_status & ~w_data_reg;
                        6'h0d: irq_mask   <= merge_strobe(irq_mask, w_data_reg, w_strb_reg);
                        default: ;
                    endcase
                    aw_pending <= 1'b0;
                    w_pending <= 1'b0;
                    write_pending <= 1'b1;
                end
            end

            if (!read_pending && s_axi_arvalid) begin
                case (s_axi_araddr[7:2])
                    6'h00: read_data <= GPU_ID;
                    6'h01: read_data <= GPU_VERSION;
                    6'h04: read_data <= fb_base_lo;
                    6'h05: read_data <= fb_base_hi;
                    6'h06: read_data <= width;
                    6'h07: read_data <= height;
                    6'h08: read_data <= stride;
                    6'h09: read_data <= format;
                    6'h0a: read_data <= control;
                    6'h0c: read_data <= irq_status;
                    6'h0d: read_data <= irq_mask;
                    default: read_data <= 32'd0;
                endcase
                read_pending <= 1'b1;
            end else if (read_pending && s_axi_rready) begin
                read_pending <= 1'b0;
            end
        end
    end
endmodule
