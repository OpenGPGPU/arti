# Patterns for auto-detecting interrupt output ports.
# A port is considered an interrupt if it is an output, 1-bit wide (or small),
# and its canonical name matches one of these patterns.
INTERRUPT_PATTERNS = [
    "IRQ", "INTERRUPT", "INTR", "INT",
    "IRQOUT", "INTOUT", "INTREQ", "INTO",
]

# Common optional bus signals that are not interrupts but may be detected.
INTERRUPT_EXCLUDE = [
    "AWVALID", "ARVALID", "WVALID", "BVALID", "RVALID",
    "AWREADY", "ARREADY", "WREADY", "BREADY", "RREADY",
    "AWADDR", "ARADDR", "WDATA", "RDATA", "BRESP", "RRESP",
    "WSTRB", "AWPROT", "ARPROT",
    "AWLEN", "AWSIZE", "AWBURST", "WLAST", "ARLEN", "ARSIZE", "ARBURST", "RLAST",
    "PADDR", "PWDATA", "PRDATA", "PWRITE", "PSEL", "PENABLE", "PREADY", "PSLVERR", "PPROT", "PSTRB",
    "HADDR", "HWDATA", "HRDATA", "HWRITE", "HTRANS", "HREADY", "HRESP", "HBURST", "HSIZE", "HPROT", "HSEL",
    "TDATA", "TVALID", "TREADY", "TLAST", "TKEEP", "TSTRB", "TID", "TDEST", "TUSER",
    "ACLK", "ARESETN", "PCLK", "PRESETN", "HCLK", "HRESETN",
]

PROTOCOL_RULES = {
    "axi4": {
        "required": [
            "AWADDR", "AWVALID", "AWREADY", "WDATA", "WVALID", "WREADY",
            "BRESP", "BVALID", "BREADY", "ARADDR", "ARVALID", "ARREADY",
            "RDATA", "RRESP", "RVALID", "RREADY", "AWLEN", "AWSIZE",
            "AWBURST", "WLAST", "ARLEN", "ARSIZE", "ARBURST", "RLAST",
        ],
        "optional": ["WSTRB", "AWPROT", "ARPROT"],
        "clock": ["ACLK"], "reset": ["ARESETN"],
    },
    "axi-lite": {
        "required": [
            "AWADDR", "AWVALID", "AWREADY", "WDATA", "WVALID", "WREADY",
            "BRESP", "BVALID", "BREADY", "ARADDR", "ARVALID", "ARREADY",
            "RDATA", "RRESP", "RVALID", "RREADY",
        ],
        "optional": ["WSTRB", "AWPROT", "ARPROT"],
        "clock": ["ACLK"], "reset": ["ARESETN"],
        "forbidden": ["AWLEN", "AWBURST", "ARLEN", "ARBURST", "WLAST", "RLAST"],
    },
    "axi-stream": {
        "required": ["TDATA", "TVALID", "TREADY"],
        "optional": ["TLAST", "TKEEP", "TSTRB", "TID", "TDEST", "TUSER"],
        "clock": ["ACLK"], "reset": ["ARESETN"],
    },
    "ahb": {
        "required": ["HADDR", "HWDATA", "HRDATA", "HWRITE", "HTRANS", "HREADY"],
        "optional": ["HRESP", "HSIZE", "HBURST", "HPROT", "HSEL"],
        "clock": ["HCLK"], "reset": ["HRESETN"],
    },
    "apb": {
        "required": ["PADDR", "PWDATA", "PRDATA", "PWRITE", "PSEL", "PENABLE"],
        "optional": ["PREADY", "PSLVERR", "PPROT", "PSTRB"],
        "clock": ["PCLK"], "reset": ["PRESETN"],
    },
}
