`timescale 1ns/1ps

module arti_gpu_tb;
    reg clk = 1'b0;
    reg resetn = 1'b0;
    reg [7:0] awaddr = 0;
    reg awvalid = 0;
    wire awready;
    reg [31:0] wdata = 0;
    reg [3:0] wstrb = 0;
    reg wvalid = 0;
    wire wready;
    wire [1:0] bresp;
    wire bvalid;
    reg bready = 0;
    reg [7:0] araddr = 0;
    reg arvalid = 0;
    wire arready;
    wire [31:0] rdata;
    wire [1:0] rresp;
    wire rvalid;
    reg rready = 0;
    wire irq;

    always #5 clk = ~clk;

    arti_gpu dut (
        .s_axi_aclk(clk), .s_axi_aresetn(resetn),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid), .s_axi_rready(rready), .irq(irq)
    );

    task automatic write_aw_first(input [7:0] address, input [31:0] value);
        begin
            @(negedge clk);
            awaddr = address;
            awvalid = 1'b1;
            @(negedge clk);
            awvalid = 1'b0;
            wdata = value;
            wstrb = 4'hf;
            wvalid = 1'b1;
            @(negedge clk);
            wvalid = 1'b0;
            wstrb = 0;
            bready = 1'b1;
            wait (bvalid);
            @(posedge clk);
            @(negedge clk);
            bready = 1'b0;
        end
    endtask

    task automatic write_w_first(input [7:0] address, input [31:0] value);
        begin
            @(negedge clk);
            wdata = value;
            wstrb = 4'hf;
            wvalid = 1'b1;
            @(negedge clk);
            wvalid = 1'b0;
            wstrb = 0;
            awaddr = address;
            awvalid = 1'b1;
            @(negedge clk);
            awvalid = 1'b0;
            bready = 1'b1;
            wait (bvalid);
            @(posedge clk);
            @(negedge clk);
            bready = 1'b0;
        end
    endtask

    task automatic read_reg(input [7:0] address, output [31:0] value);
        begin
            @(negedge clk);
            araddr = address;
            arvalid = 1'b1;
            @(negedge clk);
            arvalid = 1'b0;
            rready = 1'b1;
            wait (rvalid);
            value = rdata;
            @(posedge clk);
            @(negedge clk);
            rready = 1'b0;
        end
    endtask

    reg [31:0] value;
    initial begin
        repeat (2) @(negedge clk);
        resetn = 1'b1;
        repeat (2) @(negedge clk);

        write_aw_first(8'h18, 32'd1280);
        read_reg(8'h18, value);
        if (value !== 32'd1280) $fatal(1, "AW-first write failed: %h", value);

        write_w_first(8'h1c, 32'd720);
        read_reg(8'h1c, value);
        if (value !== 32'd720) $fatal(1, "W-first write failed: %h", value);

        write_aw_first(8'h34, 32'h1);
        write_w_first(8'h28, 32'h3);
        repeat (1100) @(negedge clk);
        if (irq !== 1'b1) $fatal(1, "VSYNC IRQ did not assert");
        write_aw_first(8'h30, 32'h1);
        repeat (2) @(negedge clk);
        if (irq !== 1'b0) $fatal(1, "W1C did not clear VSYNC IRQ");
        $display("ARTI GPU RTL TEST PASS");
        $finish;
    end
endmodule
