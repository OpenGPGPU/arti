module ahb_gpio #(
    parameter AW = 6,
    parameter DW = 32
) (
    input  wire             hclk,
    input  wire             hresetn,
    input  wire [AW-1:0]    haddr,
    input  wire [DW-1:0]    hwdata,
    output wire [DW-1:0]    hrdata,
    input  wire             hwrite,
    input  wire [1:0]       htrans,
    output wire             hready,
    output wire [7:0]       gpio_out
);
    reg [DW-1:0] gpio_reg;
    reg [DW-1:0] hrdata_reg;
    reg          hready_reg;
    reg          wr_en;

    assign hrdata   = hrdata_reg;
    assign hready   = hready_reg;
    assign gpio_out = gpio_reg[7:0];

    always @(posedge hclk) begin
        if (!hresetn) begin
            gpio_reg   <= 0;
            hrdata_reg <= 0;
            hready_reg <= 1;
            wr_en      <= 0;
        end else begin
            hready_reg <= 1;
            // Address phase: htrans == 2 (NONSEQ)
            if (htrans == 2'b10) begin
                wr_en <= hwrite;
                if (!hwrite)
                    hrdata_reg <= gpio_reg;
            end
            // Data phase
            if (wr_en && hready)
                gpio_reg <= hwdata;
            wr_en <= (htrans == 2'b10) ? hwrite : 1'b0;
        end
    end
endmodule
