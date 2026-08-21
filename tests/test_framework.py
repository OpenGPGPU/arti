import json
import os
import shutil
import socket
import struct
import time
import subprocess
import tempfile
import unittest
from pathlib import Path

from arti.cli import main
from arti.inference import infer_protocol
from arti.integration import load_integration
from arti.parser import parse_verilog


ROOT = Path(__file__).parents[1]
RTL = ROOT / "examples/simple_gpio/simple_gpio.v"
CONFIG = ROOT / "examples/simple_gpio/config.yaml"


class FrameworkTest(unittest.TestCase):
    def test_parse_and_infer_axi_lite(self):
        signature = parse_verilog(RTL, "simple_gpio")
        ports = {port.name: port for port in signature.ports}
        self.assertEqual(ports["s_axi_wdata"].width, 32)
        self.assertEqual(ports["s_axi_bresp"].width, 2)
        result = infer_protocol(signature)
        self.assertEqual(result["protocol"], "axi-lite")
        self.assertEqual(result["port_mapping"]["AWADDR"], "s_axi_awaddr")
        self.assertIn("gpio_out", result["unknown_ports"])

    def test_example_has_single_sequential_block(self):
        rtl = RTL.read_text()
        self.assertEqual(rtl.count("always @(posedge s_axi_aclk)"), 1)


    def test_generate_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(CONFIG), "--output", str(output)]), 0)
            report = json.loads((output / "reports/inference_report.json").read_text())
            self.assertEqual(report["inference"]["protocol"], "axi-lite")
            bridge = (output / "bridge/bridge_top.h").read_text()
            self.assertIn("rtl.s_axi_aclk(clk);", bridge)
            self.assertIn("simple_target_socket<BridgeTop>", bridge)
            self.assertIn("void b_transport(", bridge)
            self.assertIn("bool write_beat(", bridge)
            self.assertIn("bool read_beat(", bridge)
            self.assertIn("address % BUS_BYTES", bridge)
            self.assertTrue((output / "build/run_cosim.sh").stat().st_mode & 0o111)
            testbench = (output / "tb/local_testbench.h").read_text()
            self.assertIn("simple_initiator_socket<LocalTestbench>", testbench)
            self.assertIn("ARTI COSIM PASS", testbench)
            sc_main = (output / "sc_main.cpp").read_text()
            self.assertIn("testbench.initiator.bind(top.target_socket);", sc_main)

    @unittest.skipUnless(shutil.which("verilator") and shutil.which("pkg-config"),
                         "Verilator/SystemC build dependencies are unavailable")
    def test_real_systemc_cosimulation(self):
        if subprocess.run(["pkg-config", "--exists", "systemc"], check=False).returncode:
            self.skipTest("SystemC pkg-config metadata is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(CONFIG), "--output", str(output)]), 0)
            result = subprocess.run(
                [str(output / "build/run_cosim.sh")], check=True, text=True,
                capture_output=True, timeout=120,
            )
            self.assertIn("ARTI COSIM PASS", result.stdout)


    def test_generate_qemu_sysbus_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(CONFIG.read_text().replace(
                "bridge:\n",
                "bridge:\n  mode: qemu-sysbus\n  device_model: sysbus\n"
            ))
            (Path(tmp) / "simple_gpio.v").write_text(RTL.read_text())
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            stub = (output / "qemu/arti-rtl.c").read_text()
            self.assertIn("TYPE_SYS_BUS_DEVICE", stub)
            self.assertIn("memory_region_init_io", stub)
            qemu_args = (output / "qemu/qemu_args.txt").read_text()
            self.assertIn("-chardev socket,id=arti", qemu_args)
            self.assertNotIn("vfio", qemu_args.lower())
            self.assertNotIn("xilinx", stub.lower())

    def test_generate_qemu_display_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "rtl:\n  top_module: simple_gpio\n  source_files: [simple_gpio.v]\n"
                "bridge:\n  mode: qemu-embedded\n  base_address: \"0x0B000000\"\n"
                "  data_width: 32\n"
                "display:\n  enabled: true\n  width: 800\n  height: 600\n"
                "  framebuffer_offset: 0x100000\n  framebuffer_size: 0x960000\n"
            )
            (Path(tmp) / "simple_gpio.v").write_text(RTL.read_text())
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            stub = (output / "qemu/arti-rtl.c").read_text()
            self.assertIn("GraphicHwOps", stub)
            self.assertIn("qemu_graphic_console_create", stub)
            self.assertIn("ARTI_FB_OFFSET", stub)
            self.assertIn("ARTI_MMIO_SIZE", stub)
            self.assertIn("ARTI_MMIO_EXTENT", stub)
            self.assertIn("ARTI_IRQ_COUNT", stub)
            self.assertIn("qemu_console_update_full", stub)
            self.assertIn("memcpy(s->vram", stub)


    @unittest.skipUnless(shutil.which("verilator") and shutil.which("pkg-config"),
                         "Verilator/SystemC build dependencies are unavailable")
    def test_qemu_socket_protocol_cosimulation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            socket_path = root / "arti.sock"
            config.write_text(CONFIG.read_text().replace(
                "bridge:\n", f"bridge:\n  mode: qemu-sysbus\n  socket_path: {socket_path}\n"
            ))
            (root / "simple_gpio.v").write_text(RTL.read_text())
            output = root / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            subprocess.run(["cmake", "-S", str(output), "-B", str(output / "build/cmake")], check=True, capture_output=True)
            subprocess.run(["cmake", "--build", str(output / "build/cmake"), "--parallel"], check=True, capture_output=True)
            process = subprocess.Popen([str(output / "build/cmake/cosim"), str(socket_path)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
            try:
                for _ in range(100):
                    if socket_path.exists(): break
                    time.sleep(0.01)
                with socket.socket(socket.AF_UNIX) as client:
                    client.connect(str(socket_path))
                    def transfer(command, size, address, data):
                        request = struct.pack("<IHHQQ", 0x41525449, command, size, address, data)
                        client.sendall(request[:7]); time.sleep(0.01); client.sendall(request[7:])
                        response = b""
                        while len(response) < 16: response += client.recv(16 - len(response))
                        return struct.unpack("<IiQ", response)
                    self.assertEqual(transfer(2, 4, 0, 0x123456A5)[1], 0)
                    magic, status, value = transfer(1, 4, 0, 0)
                    self.assertEqual((magic, status, value), (0x41525449, 0, 0x123456A5))
            finally:
                process.terminate()
                process.wait(timeout=5)


    def test_inspect_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            self.assertEqual(main(["inspect", str(RTL), "--output", str(report)]), 0)
            self.assertEqual(json.loads(report.read_text())["inference"]["protocol"], "axi-lite")


if __name__ == "__main__":
    unittest.main()


class MultiProtocolTest(unittest.TestCase):
    """Tests for multi-protocol support (APB, AXI4, AHB, AXI-Stream) and interrupts."""

    def test_apb_protocol_detection(self):
        sig = parse_verilog(ROOT / "examples/apb_gpio/apb_gpio.v", "apb_gpio")
        result = infer_protocol(sig)
        self.assertEqual(result["protocol"], "apb")
        self.assertIn("PADDR", result["port_mapping"])
        self.assertIn("PENABLE", result["port_mapping"])

    def test_axi4_protocol_detection(self):
        sig = parse_verilog(ROOT / "examples/axi4_periph/axi4_periph.v", "axi4_periph")
        result = infer_protocol(sig)
        self.assertEqual(result["protocol"], "axi4")
        self.assertIn("AWLEN", result["port_mapping"])
        self.assertIn("RLAST", result["port_mapping"])

    def test_ahb_protocol_detection(self):
        sig = parse_verilog(ROOT / "examples/ahb_gpio/ahb_gpio.v", "ahb_gpio")
        result = infer_protocol(sig)
        self.assertEqual(result["protocol"], "ahb")
        self.assertIn("HTRANS", result["port_mapping"])
        self.assertIn("HREADY", result["port_mapping"])

    def test_axi_stream_protocol_detection(self):
        sig = parse_verilog(ROOT / "examples/axis_fifo/axis_fifo.v", "axis_fifo")
        result = infer_protocol(sig)
        self.assertEqual(result["protocol"], "axi-stream")
        self.assertIn("TDATA", result["port_mapping"])
        self.assertIn("TVALID", result["port_mapping"])

    def test_interrupt_detection(self):
        sig = parse_verilog(ROOT / "examples/irq_timer/irq_timer.v", "irq_timer")
        result = infer_protocol(sig)
        self.assertEqual(result["protocol"], "axi-lite")
        self.assertTrue(len(result["interrupts"]) >= 1)
        self.assertEqual(result["interrupts"][0]["name"], "irq")
        self.assertNotIn("irq", result["unknown_ports"])

    def test_generate_arti_gpu_abi_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text((ROOT / "examples/arti_gpu/config.yaml").read_text())
            (Path(tmp) / "arti_gpu.v").write_text(
                (ROOT / "examples/arti_gpu/arti_gpu.v").read_text()
            )
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            wrapper = (output / "embedded/arti_rtl_model.cpp").read_text()
            stub = (output / "qemu/arti-rtl.c").read_text()
            self.assertIn("Varti_gpu", wrapper)
            self.assertIn("arti_rtl_model_check_irq", wrapper)
            self.assertIn("GraphicHwOps", stub)
            self.assertIn("ARTI_MMIO_EXTENT", stub)

    def test_load_generic_integration_profile(self):
        integration = load_integration(ROOT / "examples/linux_arti_driver/integration.yaml")
        self.assertEqual(integration.config.top_module, "simple_gpio")
        self.assertEqual(integration.config.base_address, 0x0B000000)
        self.assertEqual(integration.dt_compat, ("arti,rtl",))
        self.assertEqual(integration.driver_deps, "")
        self.assertFalse(integration.gpu_reference)

    def test_load_gpu_integration_profile_preserves_compatible_commas(self):
        integration = load_integration(
            ROOT / "examples/linux_arti_driver/integration_gpu_reference.yaml"
        )
        self.assertEqual(integration.config.top_module, "arti_gpu")
        self.assertEqual(integration.dt_compat, ("arti,rtl-gpu", "arti,rtl"))
        self.assertTrue(integration.gpu_reference)

    def test_load_integration_profile_resolves_driver_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profiles" / "gpu.yaml"
            profile.parent.mkdir()
            profile.write_text(
                "rtl:\n"
                "  top_module: my_gpu\n"
                "  source_files: [my_gpu.v]\n"
                "integration:\n"
                "  driver_ko: driver/my_gpu.ko\n"
                "  driver_deps: deps/drm.ko:deps/helper.ko\n"
            )

            integration = load_integration(profile)

            self.assertEqual(
                integration.driver_ko,
                str((profile.parent / "driver/my_gpu.ko").resolve()),
            )
            self.assertEqual(
                integration.driver_deps,
                ":".join(
                    str((profile.parent / path).resolve())
                    for path in ("deps/drm.ko", "deps/helper.ko")
                ),
            )

    def test_linux_harness_rejects_driver_vermagic_before_qemu(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_compiler = fake_bin / "aarch64-linux-gnu-gcc"
            fake_compiler.write_text("#!/bin/sh\nexit 0\n")
            fake_compiler.chmod(0o755)

            linux_build = root / "linux-build"
            release_file = linux_build / "include/config/kernel.release"
            release_file.parent.mkdir(parents=True)
            release_file.write_text("expected-release\n")
            (linux_build / "Makefile").write_text("# test fixture\n")

            driver = root / "my_gpu.ko"
            driver.write_text("vermagic=wrong-release SMP aarch64\n")
            kernel = root / "Image"
            kernel.write_bytes(b"kernel")
            qemu = root / "qemu-system-aarch64"
            qemu.write_text("#!/bin/sh\necho QEMU SHOULD NOT RUN\n")
            qemu.chmod(0o755)

            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "QEMU": str(qemu),
                "KERNEL": str(kernel),
                "LINUX_BUILD": str(linux_build),
                "DRIVER_KO": str(driver),
                "SKIP_GENERIC_TEST": "1",
                "WORK": str(root / "work"),
            }
            result = subprocess.run(
                ["bash", str(ROOT / "examples/linux_arti_driver/run_linux_test.sh")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("vermagic mismatch", output)
            self.assertNotIn("QEMU SHOULD NOT RUN", output)

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"),
                         "Icarus Verilog is unavailable")
    def test_arti_gpu_rtl_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "arti_gpu_tb.vvp"
            subprocess.run([
                "iverilog", "-g2012", "-s", "arti_gpu_tb", "-o", str(output),
                str(ROOT / "examples/arti_gpu/arti_gpu.v"),
                str(ROOT / "examples/arti_gpu/arti_gpu_tb.v"),
            ], check=True, capture_output=True, text=True)
            result = subprocess.run(["vvp", str(output)], check=True,
                                    capture_output=True, text=True)
            self.assertIn("ARTI GPU RTL TEST PASS", result.stdout)

    def test_generate_apb_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "rtl:\n  top_module: apb_gpio\n  source_files: [apb_gpio.v]\n"
                "bridge:\n  mode: qemu-embedded\n  base_address: \"0x0B000000\"\n  data_width: 32\n"
            )
            (Path(tmp) / "apb_gpio.v").write_text(
                (ROOT / "examples/apb_gpio/apb_gpio.v").read_text()
            )
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            wrapper = (output / "embedded/arti_rtl_model.cpp").read_text()
            self.assertIn("pclk", wrapper)
            self.assertIn("psel", wrapper)
            self.assertIn("penable", wrapper)
            stub = (output / "qemu/arti-rtl.c").read_text()
            self.assertIn("TYPE_SYS_BUS_DEVICE", stub)

    def test_generate_irq_timer_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "rtl:\n  top_module: irq_timer\n  source_files: [irq_timer.v]\n"
                "bridge:\n  mode: qemu-embedded\n  base_address: \"0x0B000000\"\n  data_width: 32\n"
            )
            (Path(tmp) / "irq_timer.v").write_text(
                (ROOT / "examples/irq_timer/irq_timer.v").read_text()
            )
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            header = (output / "embedded/arti_rtl_model.h").read_text()
            self.assertIn("arti_rtl_model_check_irq", header)
            wrapper = (output / "embedded/arti_rtl_model.cpp").read_text()
            self.assertIn("arti_rtl_model_check_irq", wrapper)
            self.assertIn("g_rtl->irq", wrapper)
            stub = (output / "qemu/arti-rtl.c").read_text()
            self.assertIn("sysbus_init_irq", stub)
            self.assertIn("arti_irq_timer", stub)
            self.assertNotIn("ARTI_FB_OFFSET", stub)
            self.assertNotIn("s->vram", stub)

    def test_generate_axi4_embedded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "rtl:\n  top_module: axi4_periph\n  source_files: [axi4_periph.v]\n"
                "bridge:\n  mode: qemu-embedded\n  base_address: \"0x0B000000\"\n  data_width: 32\n"
            )
            (Path(tmp) / "axi4_periph.v").write_text(
                (ROOT / "examples/axi4_periph/axi4_periph.v").read_text()
            )
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            wrapper = (output / "embedded/arti_rtl_model.cpp").read_text()
            self.assertIn("awlen", wrapper.lower())
            self.assertIn("wlast", wrapper.lower())
            self.assertIn("arlen", wrapper.lower())

    def test_non_axilite_protocol_in_local_mode(self):
        """Non-AXI-Lite protocols should generate a generic bridge in local mode."""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(
                "rtl:\n  top_module: apb_gpio\n  source_files: [apb_gpio.v]\n"
                "bridge:\n  base_address: \"0x0B000000\"\n  data_width: 32\n"
            )
            (Path(tmp) / "apb_gpio.v").write_text(
                (ROOT / "examples/apb_gpio/apb_gpio.v").read_text()
            )
            output = Path(tmp) / "generated"
            self.assertEqual(main(["generate", str(config), "--output", str(output)]), 0)
            bridge = (output / "bridge/bridge_top.h").read_text()
            self.assertIn("BridgeTop", bridge)
            self.assertIn("b_transport", bridge)
