module apb_gpio #(
    parameter AW = 4,
    parameter DW = 32
) (
    input  wire             pclk,
    input  wire             presetn,
    input  wire [AW-1:0]    paddr,
    input  wire [DW-1:0]    pwdata,
    output wire [DW-1:0]    prdata,
    input  wire             pwrite,
    input  wire             psel,
    input  wire             penable,
    output wire             pready,
    output wire [7:0]       gpio_out
);
    reg [DW-1:0] gpio_reg;
    reg [DW-1:0] prdata_reg;
    reg           pready_reg;

    assign prdata  = prdata_reg;
    assign pready  = pready_reg;
    assign gpio_out = gpio_reg[7:0];

    always @(posedge pclk) begin
        if (!presetn) begin
            gpio_reg   <= 0;
            prdata_reg <= 0;
            pready_reg <= 1;
        end else begin
            pready_reg <= 1;
            if (psel && penable && pready) begin
                if (pwrite)
                    gpio_reg <= pwdata;
                else
                    prdata_reg <= gpio_reg;
            end
        end
    end
endmodule
