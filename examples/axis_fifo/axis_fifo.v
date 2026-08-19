module axis_fifo #(
    parameter DW = 32,
    parameter DEPTH = 4
) (
    input  wire             aclk,
    input  wire             aresetn,
    // TX (slave side: we write data in)
    input  wire [DW-1:0]    s_axis_tdata,
    input  wire             s_axis_tvalid,
    output wire             s_axis_tready,
    // RX (master side: we read data out)
    output wire [DW-1:0]    m_axis_tdata,
    output wire             m_axis_tvalid,
    input  wire             m_axis_tready
);
    reg [DW-1:0] fifo [0:DEPTH-1];
    reg [$clog2(DEPTH+1)-1:0] wr_ptr, rd_ptr;
    reg [$clog2(DEPTH+1)-1:0] count;

    wire full  = (count == DEPTH);
    wire empty = (count == 0);

    assign s_axis_tready = !full;
    assign m_axis_tdata  = fifo[rd_ptr];
    assign m_axis_tvalid = !empty;

    always @(posedge aclk) begin
        if (!aresetn) begin
            wr_ptr <= 0;
            rd_ptr <= 0;
            count  <= 0;
        end else begin
            if (s_axis_tvalid && s_axis_tready) begin
                fifo[wr_ptr] <= s_axis_tdata;
                wr_ptr <= (wr_ptr == DEPTH-1) ? 0 : wr_ptr + 1;
                count  <= count + 1;
            end
            if (m_axis_tvalid && m_axis_tready) begin
                rd_ptr <= (rd_ptr == DEPTH-1) ? 0 : rd_ptr + 1;
                count  <= count - 1;
            end
        end
    end
endmodule
