"""Exercise actual generated AXI responder C++, without an RTL build/guest."""
import os
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from arti.model import ModuleSignature, Port
from arti.protocols import _memory_bridge_for_axi4


def signature(width):
    directions = {
        'awaddr': 'output', 'awlen': 'output', 'awsize': 'output',
        'awvalid': 'output', 'awready': 'input', 'wdata': 'output',
        'wstrb': 'output', 'wlast': 'output', 'wvalid': 'output',
        'wready': 'input', 'bresp': 'input', 'bvalid': 'input',
        'bready': 'output', 'araddr': 'output', 'arlen': 'output',
        'arsize': 'output', 'arvalid': 'output', 'arready': 'input',
        'rdata': 'input', 'rresp': 'input', 'rlast': 'input',
        'rvalid': 'input', 'rready': 'output', 'awid': 'output',
        'bid': 'input', 'arid': 'output', 'rid': 'input',
        'awburst': 'output', 'arburst': 'output',
    }
    widths = {'wdata': width, 'rdata': width, 'wstrb': width // 8,
              'awaddr': 32, 'araddr': 32, 'awlen': 8, 'arlen': 8,
              'awsize': 3, 'arsize': 3, 'bresp': 2, 'rresp': 2,
              'awburst': 2, 'arburst': 2,
              'awid': 6, 'bid': 6, 'arid': 6, 'rid': 6}
    return ModuleSignature('Signals', [Port('mem_' + k, v, widths.get(k, 1))
                                       for k, v in directions.items()])


class AxiMemoryTest(unittest.TestCase):
    def test_invalid_master_widths_fail_instead_of_disappearing(self):
        for width in (0, 24, 96, 1024):
            with self.subTest(width=width), self.assertRaisesRegex(ValueError, 'data width'):
                _memory_bridge_for_axi4(signature(width))
        ports = signature(256).ports
        for field in ('mem_rdata', 'mem_wstrb'):
            wrong = [Port(p.name, p.direction, 1 if p.name == field else p.width) for p in ports]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'do not match'):
                _memory_bridge_for_axi4(ModuleSignature('Wrong', wrong))

    @unittest.skipUnless(shutil.which('c++'), 'C++ compiler unavailable')
    def test_generated_responder_full_width_bursts_and_faults(self):
        for width in (64, 256, 512):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                sig = signature(width)
                glob, drive, capture = _memory_bridge_for_axi4(sig)
                data_type = 'uint64_t' if width <= 64 else f'std::array<uint32_t,{width // 32}>'
                fields = '\n'.join(f'    {data_type if q.name.endswith("data") else "uint64_t"} {q.name}{{}};'
                                   for q in sig.ports)
                set_byte = ('s.mem_wdata |= (uint64_t)v << (8*i);' if width <= 64 else
                            's.mem_wdata[i/4] |= (uint32_t)v << (8*(i%4));')
                get_byte = ('return (uint8_t)(s.mem_rdata >> (8*i));' if width <= 64 else
                            'return (uint8_t)(s.mem_rdata[i/4] >> (8*(i%4)));')
                source = r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
struct Signals { FIELDS };
static Signals s;
static Signals *g_rtl=&s;
static uint8_t memory[2048];
static unsigned reads, writes;
static int read_cb(uint64_t a,uint8_t *p,unsigned n,uint64_t id) {
    assert(id==17 || id==18); reads++;
    if(a==768) return -1;
    assert(a+n<=sizeof(memory)); memcpy(p,memory+a,n); return 0;
}
static int write_cb(uint64_t a,const uint8_t *p,unsigned n,uint64_t mask,uint64_t id) {
    assert(id==13); writes++;
    if(a==768) return -1;
    assert(a+n<=sizeof(memory));
    for(unsigned i=0;i<n;i++) if(mask & (UINT64_C(1)<<i)) memory[a+i]=p[i];
    return 0;
}
static auto g_mem_read_cb=read_cb;
static auto g_mem_write_cb=write_cb;
GLOBALS
static void drive() { DRIVE }
static void edge() { drive(); CAPTURE }
static void set_byte(unsigned i,uint8_t v) { SETBYTE }
static uint8_t get_byte(unsigned i) { GETBYTE }
static void fill(unsigned bias) {
    s.mem_wdata={}; for(unsigned i=0;i<N;i++) set_byte(i,(uint8_t)(bias+i));
}
static void pop_b(unsigned response) {
    drive(); assert(s.mem_bvalid && s.mem_bid==13 && s.mem_bresp==response);
    for(unsigned i=0;i<3;i++) {edge();assert(s.mem_bvalid && s.mem_bresp==response);}
    s.mem_bready=1;edge();s.mem_bready=0;drive();assert(!s.mem_bvalid);
}
int main() {
    (void)g_arti_memdbg;
    memset(memory,0xa5,sizeof(memory));
    s.mem_awid=13;s.mem_arid=17;s.mem_awburst=s.mem_arburst=1;
    s.mem_awsize=s.mem_arsize=LOG;s.mem_awlen=s.mem_arlen=1;
    // W arrives before AW; all high words and byte strobes remain owned.
    fill(1);s.mem_wstrb=UINT64_MAX;s.mem_wlast=0;s.mem_wvalid=1;edge();
    assert(writes==0);s.mem_wvalid=0;s.mem_awaddr=128;s.mem_awvalid=1;edge();
    s.mem_awvalid=0;assert(writes==1);
    fill(97);s.mem_wvalid=1;s.mem_wlast=1;edge();s.mem_wvalid=0;
    assert(writes==2);pop_b(0);
    for(unsigned i=0;i<N;i++){assert(memory[128+i]==(uint8_t)(1+i));assert(memory[128+N+i]==(uint8_t)(97+i));}
    assert(memory[127]==0xa5 && memory[128+2*N]==0xa5);
    s.mem_araddr=128;s.mem_arvalid=1;edge();s.mem_arvalid=0;drive();
    assert(reads==2 && s.mem_rvalid && !s.mem_rlast && s.mem_rid==17 && !s.mem_rresp);
    for(unsigned t=0;t<3;t++){for(unsigned i=0;i<N;i++)assert(get_byte(i)==(uint8_t)(1+i));edge();}
    s.mem_rready=1;edge();drive();assert(s.mem_rvalid && s.mem_rlast);
    for(unsigned i=0;i<N;i++)assert(get_byte(i)==(uint8_t)(97+i));
    edge();s.mem_rready=0;drive();assert(!s.mem_rvalid);
    // Narrow upper-lane transfer, only two strobes, then an unaligned first beat.
    unsigned a=256+N-4;fill(13);s.mem_awaddr=a;s.mem_awlen=0;s.mem_awsize=2;
    s.mem_awvalid=s.mem_wvalid=1;s.mem_wstrb=UINT64_C(10)<<(N-4);edge();
    s.mem_awvalid=s.mem_wvalid=0;pop_b(0);
    assert(memory[a]==0xa5 && memory[a+1]==(uint8_t)(13+N-3));
    assert(memory[a+2]==0xa5 && memory[a+3]==(uint8_t)(13+N-1));
    fill(41);s.mem_awaddr=449;s.mem_awsize=2;s.mem_wstrb=14;
    s.mem_awvalid=s.mem_wvalid=1;edge();s.mem_awvalid=s.mem_wvalid=0;pop_b(0);
    assert(memory[448]==0xa5 && memory[449]==42 && memory[451]==44 && memory[452]==0xa5);
    s.mem_araddr=449;s.mem_arsize=2;s.mem_arlen=0;s.mem_arvalid=1;edge();s.mem_arvalid=0;drive();
    assert(get_byte(0)==0 && get_byte(1)==42 && get_byte(3)==44 && s.mem_rlast);
    s.mem_rready=1;edge();s.mem_rready=0;
    // No-strobe writes cannot access an invalid address.
    unsigned before=writes;s.mem_awaddr=768;s.mem_wstrb=0;
    s.mem_awvalid=s.mem_wvalid=1;edge();s.mem_awvalid=s.mem_wvalid=0;pop_b(0);assert(writes==before);
    // Actual callback errors propagate on held B/R, including wide high bytes.
    s.mem_wstrb=UINT64_MAX;s.mem_awsize=LOG;s.mem_awvalid=s.mem_wvalid=1;edge();
    s.mem_awvalid=s.mem_wvalid=0;pop_b(2);
    s.mem_araddr=768;s.mem_arsize=LOG;s.mem_arvalid=1;edge();s.mem_arvalid=0;drive();
    assert(s.mem_rvalid && s.mem_rresp==2 && s.mem_rlast);
    for(unsigned i=0;i<N;i++)assert(get_byte(i)==0);
    s.mem_rready=1;edge();s.mem_rready=0;
    // Unsupported transfer sizes and wrapping bursts fail without memory access.
    before=writes;s.mem_awaddr=832;s.mem_awsize=LOG+1;s.mem_awvalid=s.mem_wvalid=1;edge();
    s.mem_awvalid=s.mem_wvalid=0;pop_b(2);assert(writes==before);
    s.mem_awsize=LOG;s.mem_awburst=2;s.mem_awvalid=s.mem_wvalid=1;edge();
    s.mem_awvalid=s.mem_wvalid=0;pop_b(2);assert(writes==before);s.mem_awburst=1;
    before=reads;s.mem_arsize=LOG+1;s.mem_arvalid=1;edge();s.mem_arvalid=0;drive();
    assert(reads==before && s.mem_rvalid && s.mem_rresp==2);
    s.mem_rready=1;edge();s.mem_rready=0;s.mem_arsize=LOG;
    // A malformed WLAST never completes early or produces an OKAY response.
    s.mem_awaddr=896;s.mem_awlen=1;s.mem_awvalid=s.mem_wvalid=1;edge();
    s.mem_awvalid=s.mem_wvalid=0;drive();assert(!s.mem_bvalid);
    s.mem_wvalid=1;edge();s.mem_wvalid=0;pop_b(2);
    // R-ready backpressure retains distinct transaction IDs and data snapshots.
    s.mem_araddr=128;s.mem_arlen=0;s.mem_arvalid=1;edge();
    s.mem_arid=18;s.mem_araddr=128+N;edge();s.mem_arvalid=0;drive();assert(s.mem_rid==17);
    s.mem_rready=1;edge();drive();assert(s.mem_rvalid && s.mem_rid==18);
    edge();drive();assert(!s.mem_rvalid && !s.mem_bvalid && !g_mem_aw_active);
    s.mem_rready=0;
    // FIXED bursts revisit the same address instead of silently incrementing.
    s.mem_awaddr=1024;s.mem_awburst=0;s.mem_awlen=1;fill(201);s.mem_wlast=0;
    s.mem_awvalid=s.mem_wvalid=1;edge();s.mem_awvalid=0;fill(71);s.mem_wlast=1;edge();
    s.mem_wvalid=0;pop_b(0);
    for(unsigned i=0;i<N;i++)assert(memory[1024+i]==(uint8_t)(71+i));
    assert(memory[1024+N]==0xa5);
    s.mem_araddr=1024;s.mem_arburst=0;s.mem_arlen=1;s.mem_arvalid=1;edge();s.mem_arvalid=0;
    for(unsigned b=0;b<2;b++){
        drive();assert(s.mem_rvalid && s.mem_rlast==(b==1));
        for(unsigned i=0;i<N;i++){assert(get_byte(i)==(uint8_t)(71+i));}
        s.mem_rready=1;edge();
    }
    drive();assert(!s.mem_rvalid);
    printf("ARTI generated AXI memory %u-bit responder PASS\n",N*8);
}
'''
                values = {'FIELDS': fields, 'GLOBALS': glob, 'DRIVE': drive, 'CAPTURE': capture,
                          'SETBYTE': set_byte, 'GETBYTE': get_byte, 'LOG': str((width // 8).bit_length()-1),
                          'N': str(width // 8)}
                # Replace standalone markers only; generated C++ identifiers are untouched.
                import re
                source = re.sub(r'\b(' + '|'.join(values) + r')\b', lambda m: values[m[0]], source)
                (p / 'test.cpp').write_text(source)
                built = subprocess.run(['c++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
                                        str(p / 'test.cpp'), '-o', str(p / 'test')], text=True, capture_output=True)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                result = subprocess.run([str(p / 'test')], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'{width}-bit responder PASS', result.stdout)

    def test_invalid_guest_options_fail_before_tools_or_guest(self):
        script = Path(__file__).resolve().parents[1] / 'examples/linux_arti_driver/run_linux_test.sh'
        for env in ({'ARTI_GUEST_RAM_MIB': '2048M'}, {'ARTI_GUEST_RAM_MIB': '0'},
                    {'ARTI_GUEST_RAM_MIB': '32769'}, {'ARTI_GUEST_RAM_MIB': '-1'},
                    {'ARTI_KERNEL_CMDLINE_EXTRA': 'cma=768M\ninit=/bad'},
                    {'ARTI_KERNEL_CMDLINE_EXTRA': '$(echo bad)'},
                    {'ARTI_KERNEL_CMDLINE_EXTRA': 'x' * 4097}):
            with self.subTest(env=env):
                result = subprocess.run(['bash', str(script)], env={**os.environ, **env},
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('FAIL: ARTI_', result.stderr)

    def test_guest_memory_options_preserve_defaults_and_argument_boundaries(self):
        # This executes the real harness against explicitly fake tool/VM files;
        # it verifies argv construction, not a Linux/RTL acceptance result.
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); fake = p / 'bin'; fake.mkdir()
            scripts = {
                'aarch64-linux-gnu-gcc': '#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done\n: > "$2"\n',
                'timeout': '#!/bin/sh\nshift\nexec "$@"\n',
                'cpio': '#!/bin/sh\ncat >/dev/null\nprintf unit\n',
            }
            for name, body in scripts.items():
                f = fake / name; f.write_text(body); f.chmod(0o755)
            qemu = p / 'fake-qemu'
            qemu.write_text('#!/usr/bin/env python3\nimport json,os,pathlib,sys\n'
                            'args=sys.argv[1:]\npathlib.Path(os.environ["UNIT_ARGV"]).write_text(json.dumps(args))\n'
                            'serial=args[args.index("-serial")+1]\n'
                            'pathlib.Path(serial.removeprefix("file:")).write_text("UNIT EXTERNAL PASS\\n")\n')
            qemu.chmod(0o755)
            kernel = p / 'Image'; kernel.write_bytes(b'unit only')
            build = p / 'linux-build'; (build / 'include/config').mkdir(parents=True)
            (build / 'include/config/kernel.release').write_text('unit-release\n')
            module = p / 'unit.ko'; module.write_text('vermagic=unit-release SMP aarch64\n')
            rtl = p / 'unit.v'; rtl.write_text('module unit; endmodule\n')
            config = p / 'integration.yaml'
            config.write_text('rtl:\n  top_module: unit\n  source_files: [unit.v]\n'
                              'bridge:\n  base_address: 0x0B000000\n'
                              'integration:\n  skip_generic_test: true\n')
            env = {**os.environ, 'PATH': str(fake) + os.pathsep + os.environ['PATH'],
                   'INTEGRATION_CONFIG': str(config), 'QEMU': str(qemu), 'KERNEL': str(kernel),
                   'LINUX_BUILD': str(build), 'DRIVER_KO': str(module), 'SKIP_GENERIC_TEST': '1',
                   'GPU_REFERENCE': '0', 'GPU_DRM_TEST': '0', 'ARTI_DISPLAY': '0',
                   'DRIVER_MARKER': 'UNIT EXTERNAL PASS', 'CROSS_COMPILE': 'aarch64-linux-gnu-',
                   'UNIT_ARGV': str(p / 'argv.json')}
            for key in ('ARTI_GUEST_RAM_MIB', 'ARTI_KERNEL_CMDLINE_EXTRA', 'DRIVER_DEPS', 'DRIVER_MANIFEST'):
                env.pop(key, None)
            for ram, extra in ((None, None), ('2048', 'cma=768M@0x60000000 panic=1')):
                with self.subTest(ram=ram):
                    case = {**env, 'WORK': str(p / ('default' if ram is None else 'native'))}
                    if ram:
                        case.update(ARTI_GUEST_RAM_MIB=ram, ARTI_KERNEL_CMDLINE_EXTRA=extra)
                    r = subprocess.run(['bash', str(root / 'examples/linux_arti_driver/run_linux_test.sh')],
                                       env=case, text=True, capture_output=True)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    argv = json.loads((p / 'argv.json').read_text())
                    self.assertEqual(argv[argv.index('-m')+1], ('512' if ram is None else ram)+'M')
                    self.assertEqual(argv[argv.index('-append')+1], 'console=ttyAMA0'+('' if extra is None else ' '+extra))
