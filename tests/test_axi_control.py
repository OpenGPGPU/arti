"""Compile/run real generated control masters against a small CPU pin target.

No Verilator, RTL, Linux guest, or modeled GPU is built or run. Set
ARTI_CPU_EVIDENCE to a fresh directory to retain generated C++/MMD/logs.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from arti.config import Config
from arti.model import ModuleSignature, Port
from arti.protocols import _common_header, render_axi4_model, render_axi_lite_model


def generated(protocol, width):
    required = ('AWADDR AWVALID AWREADY WDATA WVALID WREADY BRESP BVALID BREADY '
                'ARADDR ARVALID ARREADY RDATA RRESP RVALID RREADY WSTRB ACLK ARESETN').split()
    if protocol == 'axi4':
        required += 'AWLEN AWSIZE AWBURST WLAST ARLEN ARSIZE ARBURST RLAST'.split()
    mapping = {s: 'clk' if s=='ACLK' else 'resetn' if s=='ARESETN' else 's_'+s.lower() for s in required}
    outputs = 'AWREADY WREADY BRESP BVALID ARREADY RDATA RRESP RVALID RLAST'.split()
    widths = {'AWADDR':32,'ARADDR':32,'WDATA':width,'RDATA':width,'WSTRB':width//8,
              'AWLEN':8,'ARLEN':8,'AWSIZE':3,'ARSIZE':3,'AWBURST':2,'ARBURST':2,'BRESP':2,'RRESP':2}
    ports = [Port(mapping[s], 'output' if s in outputs else 'input', widths.get(s,1)) for s in required]
    if protocol=='axi4':
        # Preserve an actual embedded-memory preamble for the old-code repro,
        # instead of injecting missing runtime globals into generated C++.
        master_out='awaddr awlen awsize awvalid wdata wstrb wlast wvalid bready araddr arlen arsize arvalid rready'.split()
        master_in='awready wready bresp bvalid arready rdata rresp rlast rvalid'.split()
        for s in master_out+master_in:
            width_m=64 if s.endswith('data') else 32 if s.endswith('addr') else 8 if s in ('wstrb','awlen','arlen') else 3 if s.endswith('size') else 1
            ports.append(Port('mem_'+s,'output' if s in master_out else 'input',width_m))
    renderer = render_axi4_model if protocol=='axi4' else render_axi_lite_model
    return renderer(Config(data_width=width,timeout_cycles=64), ModuleSignature('Control',ports),
                    mapping,{p.name:p for p in ports},[{'name':'irq'}])


MAIN = r'''
#include <cassert>
#include <string>
int main(int argc,char **argv) {
    MEMORY_DEBUG_USE
    assert(argc==2); std::string mode=argv[1];
    VControl::mode=mode=="aw-first"?VControl::AwFirst:mode=="w-first"?VControl::WFirst:
        mode=="delayed"?VControl::Delayed:mode=="no-aw"?VControl::NoAw:mode=="no-w"?VControl::NoW:
        mode=="no-request"?VControl::NoRequest:mode=="no-b"?VControl::NoB:VControl::Together;
    unsigned response=mode=="slverr"?2:mode=="decerr"?3:0;
    VControl::error_response=response; arti_rtl_model_init();
    unsigned begin=g_rtl->edges;
    int result=arti_rtl_model_write(0x40,0x89abcdef,4);
    bool timeout=mode.rfind("no-",0)==0;
    std::printf("actual generated write mode=%s result=%d AW=%u W=%u B=%u withdrawals=%u edges=%u\n",
        argv[1],result,g_rtl->aw_count,g_rtl->w_count,g_rtl->b_count,g_rtl->withdrawals,g_rtl->edges-begin);
    std::fflush(stdout);
    assert(result==((timeout || response)?-1:0));
    assert(g_rtl->withdrawals==0);
    if (!timeout) {
        assert(g_rtl->aw_count==1 && g_rtl->w_count==1 && g_rtl->b_count==1);
        assert(g_rtl->captured_address==0x40 && g_rtl->captured_data==0x89abcdef);
        assert(g_rtl->captured_strobe==15 && !g_rtl->s_awvalid && !g_rtl->s_wvalid && !g_rtl->s_bready);
        // A completed error response does not poison a drained bus.
        VControl::error_response=0; VControl::mode=VControl::Together;
        assert(!arti_rtl_model_write(0x48,0x12345678,4));
        assert(g_rtl->aw_count==2 && g_rtl->w_count==2 && g_rtl->b_count==2 && !g_rtl->withdrawals);
    } else {
        auto awvalid=g_rtl->s_awvalid, wvalid=g_rtl->s_wvalid, bready=g_rtl->s_bready;
        unsigned edges=g_rtl->edges, aw=g_rtl->aw_count, w=g_rtl->w_count, b=g_rtl->b_count;
        // Refuse reuse without changing the unaccepted original offers. Even
        // later-ready targets cannot clock duplicate/late old transactions.
        VControl::mode=VControl::Together;
        assert(arti_rtl_model_write(0x88,0,4)==-1);
        uint64_t data=0xa5; assert(arti_rtl_model_read(0x80,&data,4)==-1 && data==0xa5);
        g_rtl->irq=1; assert(!arti_rtl_model_check_irq(0));
        for(unsigned i=0;i<4;i++) tick();
        assert(g_rtl->edges==edges && g_rtl->aw_count==aw && g_rtl->w_count==w && g_rtl->b_count==b);
        assert(g_rtl->s_awvalid==awvalid && g_rtl->s_wvalid==wvalid && g_rtl->s_bready==bready);
        assert(g_rtl->s_awaddr==0x40 && g_rtl->s_wdata==0x89abcdef);
    }
    puts("ARTI actual generated control CPU PASS");
    // The real wrapper uses process-lifetime model objects.
    delete g_rtl; g_rtl=nullptr; delete g_ctx; g_ctx=nullptr;
}
'''


class AxiControlTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_generated_split_handshake_errors_and_sticky_timeout(self):
        evidence = os.environ.get('ARTI_CPU_EVIDENCE')
        if evidence:
            root = Path(evidence); root.mkdir(parents=True,exist_ok=False)
            context = None
        else:
            context = tempfile.TemporaryDirectory(); root = Path(context.name)
        try:
            for protocol in ('axi4','axi-lite'):
                for width in (32,64):
                    path=root/(protocol+'-'+str(width)); path.mkdir()
                    (path/'VControl.h').write_bytes(Path(__file__).with_name('axi_control_stub.h').read_bytes())
                    (path/'verilated.h').write_text('#include <cstdlib>\nstruct VerilatedContext {void commandArgs(int,const char **) {}};\n')
                    (path/'arti_rtl_model.h').write_text(_common_header(1))
                    source=path/'actual-generated.cpp'; source.write_text(generated(protocol,width)+
                        MAIN.replace('MEMORY_DEBUG_USE','(void)g_arti_memdbg;' if protocol=='axi4' else ''))
                    binary=path/'control'
                    compiler = ['c++','-std=c++17','-O2','-Wall','-Wextra','-Werror','-UNDEBUG',
                        '-DARTI_MODEL_MMIO_ADVANCE_CYCLES=2','-DARTI_MODEL_IDLE_GRACE=0',
                        '-MMD','-MF',str(path/'actual.d'),str(source),'-o',str(binary)]
                    if os.environ.get('ARTI_CPU_SANITIZE')=='1':
                        compiler[1:1]=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
                    built=subprocess.run(compiler,text=True,capture_output=True)
                    (path/'compile.log').write_text(built.stdout+built.stderr)
                    self.assertEqual(built.returncode,0,built.stdout+built.stderr)
                    for mode in ('together','aw-first','w-first','delayed','slverr','decerr','no-aw','no-w','no-request','no-b'):
                        with self.subTest(protocol=protocol,width=width,mode=mode):
                            result=subprocess.run([str(binary),mode],text=True,capture_output=True,timeout=5)
                            (path/(mode+'.log')).write_text(result.stdout+result.stderr)
                            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        finally:
            if context:
                context.cleanup()


if __name__=='__main__':
    unittest.main()
