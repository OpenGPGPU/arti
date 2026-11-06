/* CPU-only protocol target for actual generated ARTI C++; no RTL/model. */
#ifndef ARTI_TEST_AXI_CONTROL_STUB_H
#define ARTI_TEST_AXI_CONTROL_STUB_H
#include <cstdint>
class VerilatedContext;
class VControl {
public:
    enum Mode { Together, AwFirst, WFirst, Delayed, NoAw, NoW, NoRequest, NoB };
    inline static Mode mode = Together;
    inline static unsigned error_response = 0;
    uint64_t clk=0,resetn=0;
    uint64_t s_awaddr=0,s_awlen=0,s_awsize=0,s_awburst=0,s_awvalid=0,s_awready=0;
    uint64_t s_wdata=0,s_wstrb=0,s_wlast=0,s_wvalid=0,s_wready=0;
    uint64_t s_bresp=0,s_bvalid=0,s_bready=0;
    uint64_t s_araddr=0,s_arlen=0,s_arsize=0,s_arburst=0,s_arvalid=0,s_arready=0;
    uint64_t s_rdata=0,s_rresp=0,s_rlast=0,s_rvalid=0,s_rready=0,irq=0;
    // A quiescent64-bit lower-memory master, as in an embedded compute host.
    uint64_t mem_awaddr=0,mem_awlen=0,mem_awsize=0,mem_awvalid=0,mem_awready=0;
    uint64_t mem_wdata=0,mem_wstrb=0,mem_wlast=0,mem_wvalid=0,mem_wready=0;
    uint64_t mem_bresp=0,mem_bvalid=0,mem_bready=0;
    uint64_t mem_araddr=0,mem_arlen=0,mem_arsize=0,mem_arvalid=0,mem_arready=0;
    uint64_t mem_rdata=0,mem_rresp=0,mem_rlast=0,mem_rvalid=0,mem_rready=0;
    unsigned edges=0,aw_count=0,w_count=0,b_count=0,withdrawals=0;
    uint64_t captured_address=0,captured_data=0,captured_strobe=0;
    VControl(VerilatedContext *,const char *) {}
    void eval() {
        bool falling=previous_clk && !clk, rising=!previous_clk && clk;
        if (!resetn) {
            have_aw=have_w=false; age=0; aw_stalled=w_stalled=false;
            edges=aw_count=w_count=b_count=withdrawals=0;
        }
        s_awready=s_awvalid && !have_aw && mode!=NoAw && mode!=NoRequest &&
            (mode!=WFirst || have_w) && (mode!=Delayed || edges>=13);
        s_wready=s_wvalid && !have_w && mode!=NoW && mode!=NoRequest &&
            (mode!=AwFirst || have_aw) && (mode!=Delayed || edges>=17);
        s_bvalid=have_aw && have_w && mode!=NoB && (mode!=Delayed || age>=7);
        s_bresp=error_response; s_arready=s_arvalid;
        s_rvalid=0; s_rlast=1;
        if (falling && resetn) {
            // Sample actual low-eval values, including ready's dependency on
            // VALID. Checking READY after posedge would lose these handshakes.
            if (aw_stalled && (!s_awvalid || saved_address!=s_awaddr)) withdrawals++;
            if (w_stalled && (!s_wvalid || saved_data!=s_wdata || saved_strobe!=s_wstrb)) withdrawals++;
            aw_stalled=s_awvalid && !s_awready; w_stalled=s_wvalid && !s_wready;
            saved_address=s_awaddr; saved_data=s_wdata; saved_strobe=s_wstrb;
            take_aw=s_awvalid && s_awready; take_w=s_wvalid && s_wready; take_b=s_bvalid && s_bready;
        }
        if (rising && resetn) {
            edges++; if (have_aw && have_w) age++;
            if (take_aw) { have_aw=true; aw_count++; captured_address=s_awaddr; }
            if (take_w) { have_w=true; w_count++; captured_data=s_wdata; captured_strobe=s_wstrb; }
            if (take_b) { have_aw=have_w=false; age=0; b_count++; }
            // Model a normal slave whose READY drops immediately after the
            // accepted edge, and whose BRESP clears after its accepted B.
            if (take_aw) s_awready=0;
            if (take_w) s_wready=0;
            if (take_b) { s_bvalid=0; s_bresp=0; }
        }
        previous_clk=clk;
    }
private:
    bool previous_clk=false,have_aw=false,have_w=false,aw_stalled=false,w_stalled=false;
    bool take_aw=false,take_w=false,take_b=false;
    uint64_t saved_address=0,saved_data=0,saved_strobe=0;
    unsigned age=0;
};
#endif
