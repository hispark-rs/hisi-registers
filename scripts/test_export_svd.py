"""Semantic export regressions; run with uv run --project . scripts/test_export_svd.py."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from export_svd import export


class ExportSemantics(unittest.TestCase):
    def test_ws53_unified_trng_consumption_and_halfword_ring_control(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            peripherals = {p.findtext("name"): p for p in ET.parse(dest).findall(".//peripheral")}
            trng = peripherals["TRNG"]
            self.assertEqual(int(trng.findtext("baseAddress"), 0), 0x52024000)
            self.assertIsNone(trng.find("interrupt"))
            regs = {r.findtext("name"): r for r in trng.findall("registers/register")}
            self.assertEqual(set(regs), {"FIFO_DATA", "FIFO_READY", "FRO_SAMPLE_CLK_SEL", "FRO_DIV_CNT"})
            for name, offset, access in (
                ("FIFO_DATA", 0x100, "read-only"), ("FIFO_READY", 0x104, "read-only"),
                ("FRO_SAMPLE_CLK_SEL", 0x174, "read-write"), ("FRO_DIV_CNT", 0x178, "read-write"),
            ):
                self.assertEqual(int(regs[name].findtext("addressOffset"), 0), offset)
                self.assertEqual(regs[name].findtext("access"), access)
                self.assertEqual(regs[name].findtext("size"), "32")
                self.assertIsNone(regs[name].find("resetValue"))
            self.assertEqual(regs["FIFO_DATA"].findtext("fields/field/readAction"), "modifyExternal")
            self.assertIsNone(regs["FIFO_READY"].find(".//readAction"))
            ready_fields = {f.findtext("name"): f for f in regs["FIFO_READY"].findall("fields/field")}
            self.assertEqual(set(ready_fields), {"DATA_READY", "DONE"})
            self.assertEqual(ready_fields["DATA_READY"].findtext("bitOffset"), "0")
            self.assertEqual(ready_fields["DONE"].findtext("bitOffset"), "1")
            self.assertEqual(regs["FRO_DIV_CNT"].findtext("fields/field/bitWidth"), "8")
            ring = peripherals["M_CRG"].find("registers/register[name='TRNG_RING_CONTROL']")
            self.assertEqual(int(ring.findtext("addressOffset"), 0), 0x1600)
            self.assertEqual(ring.findtext("size"), "16")
            self.assertEqual(ring.findtext("access"), "read-write")
            self.assertEqual(ring.findtext("fields/field/bitWidth"), "1")
            self.assertIsNone(ring.find("resetValue"))

    def test_ws53_sdio_command_views_do_not_offer_rmw(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            sdio = ET.parse(dest).find("./peripherals/peripheral[name='SDIO']")
            self.assertEqual(int(sdio.findtext("baseAddress"), 0), 0x52060000)
            self.assertEqual(sdio.findtext("interrupt/value"), "46")
            regs = {r.findtext("name"): r for r in sdio.findall("registers/register")}
            self.assertEqual(len(regs), 28)
            for prefix, command, offset in (
                ("GLOBAL_INTERRUPT", "ACK", 0x1c),
                ("FUNCTION0_INTERRUPT", "ACK", 0x38),
                ("FUNCTION0_RESET", "COMMAND", 0x3c),
                ("FUNCTION1_SEND", "COMMAND", 0x104),
                ("FUNCTION1_INTERRUPT", "ACK", 0x110),
            ):
                status = regs[prefix + "_STATUS"]
                action = regs[prefix + "_" + command]
                self.assertEqual(int(status.findtext("addressOffset"), 0), offset)
                self.assertEqual(status.findtext("access"), "read-only")
                self.assertEqual(int(action.findtext("addressOffset"), 0), offset)
                self.assertEqual(action.findtext("access"), "write-only")
                self.assertEqual(action.findtext("alternateRegister"), prefix + "_STATUS")
            self.assertEqual(regs["ADMA_TABLE"].findtext("addressOffset"), "0x40")
            self.assertEqual(regs["ADMA_TABLE"].findtext("access"), "write-only")
            self.assertEqual(regs["CMD53_ARGUMENT"].findtext("addressOffset"), "0x254")
            self.assertEqual(regs["FUNCTION1_BLOCK_SIZE"].findtext("fields/field/bitWidth"), "12")
            for reg in regs.values():
                self.assertIsNone(reg.find("resetValue"))
                self.assertIsNone(reg.find(".//modifiedWriteValues"))

    def test_ws53_tsensor_polling_subset_does_not_invent_clear_or_irq(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            sensor = ET.parse(dest).find("./peripherals/peripheral[name='TSENSOR']")
            self.assertEqual(int(sensor.findtext("baseAddress"), 0), 0x59006300)
            self.assertIsNone(sensor.find("interrupt"))
            regs = {r.findtext("name"): r for r in sensor.findall("registers/register")}
            for name, offset, access in (
                ("START", 0, "write-only"), ("CONTROL", 4, "read-write"),
                ("AUTO_STATUS", 8, "read-only"), ("SAMPLE_TIME", 16, "write-only"),
                ("AUTO_REFRESH_PERIOD", 48, "write-only"),
                ("AUTO_REFRESH_CONFIG", 52, "write-only"),
            ):
                self.assertEqual(int(regs[name].findtext("addressOffset"), 0), offset)
                self.assertEqual(regs[name].findtext("access"), access)
                self.assertEqual(regs[name].findtext("size"), "32")
                self.assertIsNone(regs[name].find("resetValue"))
            self.assertEqual(len(regs), 6)
            status = regs["AUTO_STATUS"]
            fields = {f.findtext("name"): f for f in status.findall("fields/field")}
            self.assertEqual(set(fields), {"READY", "CODE"})
            self.assertEqual(fields["READY"].findtext("bitOffset"), "1")
            self.assertEqual(fields["CODE"].findtext("bitOffset"), "2")
            self.assertEqual(fields["CODE"].findtext("bitWidth"), "10")
            self.assertIsNone(sensor.find(".//modifiedWriteValues"))
            self.assertIsNone(sensor.find(".//readAction"))

    def test_partial_reset_and_write_only_clear(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "test.rdl"
            dest = Path(work) / "test.svd"
            properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
            source.write_text(f'`include "{properties}"\n' + '''addrmap chip {
                addrmap {
                    reg {
                        field { sw=w; hw=r; onwrite=woclr; } clear[0:0] = 0;
                        field { sw=w; hw=r; } unknown[7:4];
                    } eoi @ 0;
                } timer @ 0x1000;
            };''')
            export(source, dest)
            reg = ET.parse(dest).find(".//register")
            self.assertEqual(reg.findtext("resetMask"), "0x00000001")
            self.assertEqual(reg.findtext("access"), "write-only")
            self.assertEqual(reg.findtext("fields/field/modifiedWriteValues"), "oneToClear")

    def test_ws53_irq_and_uart_integration(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            peripherals = {p.findtext("name"): p for p in ET.parse(dest).findall(".//peripheral")}
            self.assertEqual(peripherals["UART2"].findtext("baseAddress"), "0x57014000")
            self.assertEqual(peripherals["UART2"].get("derivedFrom"), "UART0")
            self.assertEqual(peripherals["UART2"].findtext("interrupt/value"), "42")
            self.assertEqual(peripherals["TIMER"].findtext("interrupt/value"), "53")
            self.assertEqual(peripherals["GPIO1"].findtext("interrupt/value"), "33")
            registers = peripherals["TIMER"].findall("registers/register")
            self.assertNotIn("TIMER_CONTROL2", [r.findtext("name") for r in registers])
            for reg in peripherals["SYSTEM_MEMORY"].findall("registers/register"):
                self.assertIsNone(reg.find("resetValue"))
                self.assertIsNone(reg.find("resetMask"))
            for bank, width in enumerate((23, 12, 3)):
                reg = peripherals[f"GPIO{bank}"].find("registers/register")
                self.assertEqual(int(reg.findtext("fields/field/bitWidth")), width)

    def test_reject_incompatible_derivation(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "test.rdl"
            properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
            source.write_text(f'`include "{properties}"\n' + '''addrmap chip {
                addrmap { reg { field {sw=rw;} value[7:0]; } data @ 0; } a @ 0x1000;
                addrmap { reg { field {sw=r;} value[7:0]; } data @ 0; } b @ 0x2000;
                b->hisi_svd_derived_from = "A";
            };''')
            with self.assertRaisesRegex(ValueError, "non-equivalent"):
                export(source, Path(work) / "test.svd")


    def test_ws53_pin_alias_domains_and_clock_fields(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            peripherals = {p.findtext("name"): p for p in ET.parse(dest).findall(".//peripheral")}
            def registers(peripheral):
                return {r.findtext("name"): r for r in peripherals[peripheral].findall("registers/register")}
            mcu, aon, tzpc = map(registers, ("PINMUX_MCU", "PINMUX_AON", "PINMUX_TZPC"))
            self.assertEqual(len(mcu), 18)
            self.assertEqual(len(aon), 40)
            self.assertEqual(len(tzpc), 3)
            self.assertNotIn("MGPIO1_SEL", mcu)
            self.assertEqual(aon["AGPIO6_SEL"].findtext("addressOffset"), "0xB8")
            self.assertEqual(aon["AGPIO6_PAD"].findtext("addressOffset"), "0x44")
            self.assertEqual(tzpc["MGPIO14_PAD"].findtext("addressOffset"), "0x170")
            self.assertEqual(mcu["MGPIO14_SEL"].findtext("addressOffset"), "0x38")
            self.assertIn("AGPIO12_PAD", aon)
            self.assertNotIn("AGPIO12_SEL", aon)
            extended = {name for name, reg in aon.items() if
                        reg.find("fields/field[name='DRIVE_HIGH']") is not None}
            self.assertEqual(extended, {"MGPIO0_PAD", "MGPIO2_PAD", "MGPIO3_PAD", "MGPIO4_PAD", "AGPIO5_PAD", "AGPIO6_PAD"})
            clocks = registers("M_CRG")
            self.assertEqual(clocks["M_CLKEN1"].findtext("size"), "16")
            for field, bit in (("TIMER_ENABLE", 0), ("LSADC_APB_ENABLE", 2), ("SPI3_MS_ENABLE", 11)):
                self.assertEqual(clocks["M_CLKEN1"].findtext(f"fields/field[name='{field}']/bitOffset"), str(bit))
            for reg in list(mcu.values()) + list(aon.values()) + list(tzpc.values()) + list(clocks.values()):
                self.assertIsNone(reg.find("resetValue"))

    def test_reject_unimplemented_array_and_split_access(self):
        for declaration, message in (
            ("reg {field {sw=rw;} value[31:0];} data[2][2] @ 0 += 4;", "multidimensional register arrays"),
            ("reg {regwidth=32; accesswidth=16; field {sw=rw;} value[31:0];} data @ 0;", "split-width"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as work:
                source = Path(work) / "test.rdl"
                source.write_text("addrmap chip {addrmap {" + declaration + "} peripheral @ 0x1000;};")
                with self.assertRaisesRegex(ValueError, message):
                    export(source, Path(work) / "test.svd")

    def test_fifo_array_views_and_read_side_effect(self):
        with tempfile.TemporaryDirectory() as work:
            source, dest = Path(work) / "test.rdl", Path(work) / "test.svd"
            properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
            source.write_text(f'`include "{properties}"\n' + '''addrmap chip {addrmap {
                external reg {field {sw=r; hw=w; onread=ruser;
                    hisi_svd_read_action="modifyExternal";} data[31:0];} rx[36] @ 0x2c +=4;
                reg {hisi_svd_alternate_register="RX"; field {sw=w; hw=r;} data[31:0];} tx[36] @ 0x2c +=4;
            } spi @ 0x1000;};''')
            export(source, dest)
            rx, tx = ET.parse(dest).findall(".//register")
            self.assertEqual(rx.findtext("name"), "RX[%s]")
            self.assertEqual(rx.findtext("dim"), "36")
            self.assertEqual(rx.findtext("dimIncrement"), "0x4")
            self.assertEqual(rx.findtext("addressOffset"), "0x2C")
            self.assertEqual(rx.findtext("readAction"), "modifyExternal")
            self.assertEqual(rx.findtext("access"), "read-only")
            self.assertEqual(tx.findtext("access"), "write-only")
            self.assertEqual(tx.findtext("alternateRegister"), "RX[%s]")
            source.write_text(source.read_text().replace('hisi_svd_alternate_register="RX"', 'hisi_svd_alternate_register="MISSING"'))
            with self.assertRaisesRegex(ValueError, "invalid alternate"):
                export(source, dest)

    def test_ws53_spi_instances_are_not_interchangeable(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            blocks = {p.findtext("name"): p for p in ET.parse(dest).findall(".//peripheral")}
            for name, base, irq, enable, fifo, busy, tx_enable in (
                ("SPI0", "0x52087000", "65", "0x0", "0x2C", "15", "0"),
                ("QSPI1", "0x5208D000", "43", "0x8", "0x60", "0", "1"),
            ):
                p = blocks[name]
                r = {node.findtext("name"): node for node in p.findall("registers/register")}
                self.assertEqual(p.findtext("baseAddress"), base)
                self.assertEqual(p.findtext("interrupt/value"), irq)
                self.assertEqual(r["ENABLE"].findtext("addressOffset"), enable)
                self.assertEqual(r["RX[%s]"].findtext("addressOffset"), fifo)
                self.assertEqual(r["RX[%s]"].findtext("dim"), "36")
                self.assertEqual(r["RX[%s]"].findtext("readAction"), "modifyExternal")
                self.assertEqual(r["RX[%s]"].findtext("access"), "read-only")
                self.assertEqual(r["TX[%s]"].findtext("access"), "write-only")
                self.assertEqual(r["STATUS"].findtext("fields/field[name='BUSY']/bitOffset"), busy)
                self.assertEqual(r["DMA_CONTROL"].findtext("fields/field[name='TX_ENABLE']/bitOffset"), tx_enable)
                self.assertEqual(r["CLEAR_ALL"].findtext("access"), "read-only")
                self.assertEqual(r["CLEAR_ALL"].findtext("readAction"), "clear")
                self.assertTrue(all(node.find("resetValue") is None for node in r.values()))

    def test_reject_mismatched_alternate_views(self):
        properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
        # These declarations are valid RDL. The alternate-view claim is not:
        # a CMSIS consumer must not merge differently shaped or located data.
        for receive, transmit in (
            ("rx[2] @ 0 += 8", "tx[2] @ 0 += 4"),
            ("rx[2] @ 0 += 4", "tx[3] @ 0 += 4"),
            ("rx @ 0", "tx @ 4"),
        ):
            with self.subTest(receive=receive, transmit=transmit), tempfile.TemporaryDirectory() as work:
                source = Path(work) / "test.rdl"
                source.write_text(f'`include "{properties}"\n' + f'''addrmap chip {{addrmap {{
                    reg {{field {{sw=r; hw=w;}} data[31:0];}} {receive};
                    reg {{hisi_svd_alternate_register="RX";
                        field {{sw=w; hw=r;}} data[31:0];}} {transmit};
                }} spi @ 0x1000;}};''')
                with self.assertRaisesRegex(ValueError, "invalid alternate"):
                    export(source, Path(work) / "test.svd")

    def test_register_file_array_keeps_nested_offsets_and_dimensions(self):
        from check_schema import validate
        with tempfile.TemporaryDirectory() as work:
            source, dest = Path(work) / "test.rdl", Path(work) / "test.svd"
            properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
            source.write_text(f'`include "{properties}"\n' + '''addrmap chip {addrmap {
                regfile {regfile {
                    reg {field {sw=r;} value[31:0];} status[2] @ 0x4 += 4;
                } group[3] @ 0x10 += 0x10;} channels[4] @ 0x100 += 0x40;
            } controller @ 0x40000000;};''')
            export(source, dest)
            validate([dest])
            cluster = ET.parse(dest).find(".//cluster")
            self.assertEqual(cluster.findtext("dim"), "4")
            self.assertEqual(cluster.findtext("dimIncrement"), "0x40")
            self.assertEqual(cluster.findtext("addressOffset"), "0x100")
            nested = cluster.find("cluster")
            self.assertEqual(nested.findtext("dim"), "3")
            self.assertEqual(nested.findtext("addressOffset"), "0x10")
            self.assertEqual(nested.findtext("register/addressOffset"), "0x4")
            self.assertEqual(nested.findtext("register/dim"), "2")
            source.write_text(source.read_text().replace('channels[4]', 'channels[2][2]'))
            with self.assertRaisesRegex(ValueError, "multidimensional register-file"):
                export(source, dest)

    def test_ws53_mdma_channels_and_request_routes(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            peripheral = tree.find(".//peripheral[name='MDMA']")
            self.assertEqual(peripheral.findtext("baseAddress"), "0x52070000")
            self.assertEqual(peripheral.findtext("interrupt/value"), "58")
            self.assertIsNone(tree.find(".//peripheral[name='SDMA']"))
            cluster = peripheral.find("registers/cluster")
            self.assertEqual(cluster.findtext("name"), "CHANNEL[%s]")
            self.assertEqual(cluster.findtext("dim"), "8")
            self.assertEqual(cluster.findtext("dimIncrement"), "0x20")
            self.assertEqual(cluster.findtext("addressOffset"), "0x100")
            offsets = {r.findtext("name"): int(r.findtext("addressOffset"), 0)
                       for r in cluster.findall("register")}
            self.assertEqual(offsets, {"LINKED_LIST": 0, "DESTINATION": 4,
                                      "CONFIGURATION": 8, "SOURCE": 16, "CONTROL": 20})
            clear = peripheral.find("registers/register[name='INTERRUPT_CLEAR']")
            self.assertEqual(clear.findtext("addressOffset"), "0x8")
            self.assertEqual(clear.findtext("access"), "write-only")
            self.assertEqual([f.findtext("modifiedWriteValues") for f in clear.findall("fields/field")],
                             ["oneToClear", "oneToClear"])
            fields = {f.findtext("name"): f for f in cluster.findall("register[name='CONFIGURATION']/fields/field")}
            expected = {"I2C0_TX": 0, "I2C0_RX": 1, "I2C1_TX": 2, "I2C1_RX": 3,
                        "QSPI1_TX": 4, "QSPI1_RX": 5, "SPI0_TX": 6, "SPI0_RX": 7,
                        "UART1_TX": 8, "UART1_RX": 9, "UART0_TX": 10, "UART0_RX": 11,
                        "I2S_TX": 12, "I2S_RX": 13, "PWM": 14, "SPWM": 15}
            for name, bit in (("SOURCE_REQUEST", "1"), ("DESTINATION_REQUEST", "5")):
                self.assertEqual(fields[name].findtext("bitOffset"), bit)
                self.assertEqual(fields[name].findtext("bitWidth"), "4")
                self.assertEqual({e.findtext("name"): int(e.findtext("value"), 0)
                                  for e in fields[name].findall("enumeratedValues/enumeratedValue")}, expected)
            self.assertEqual(fields["ACTIVE"].findtext("access"), "read-only")
            self.assertTrue(all(r.find("resetValue") is None for r in peripheral.findall(".//register")))

    def test_ws53_adc_v155_capture_and_pulse_commands(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            blocks = {p.findtext("name"): p for p in ET.parse(dest).findall(".//peripheral")}
            adc = blocks["ADC"]
            self.assertEqual(adc.findtext("baseAddress"), "0x5208A000")
            self.assertEqual(adc.findtext("interrupt/value"), "80")
            regs = {r.findtext("name"): r for r in adc.findall("registers/register")}
            self.assertNotIn("FIFO", regs)
            for name, offset in (("SCAN_COMMAND", 0x1c), ("ANALOG_CONTROL", 0x24),
                                 ("MANUAL_CONTROL", 0x28), ("OFFSET_STATUS", 0x44),
                                 ("CAP_CALIBRATION", 0x88), ("CAP_STATUS", 0xa0),
                                 ("ANALOG_CONFIG[%s]", 0xf4)):
                self.assertEqual(int(regs[name].findtext("addressOffset"), 0), offset)
            for name in ("OFFSET_REFRESH", "OFFSET_STATE_CLEAR", "CAP_REFRESH",
                         "CAP_FINISH_CLEAR", "GAIN_STATE_CLEAR"):
                reg = regs[name]
                self.assertEqual(reg.findtext("access"), "write-only")
                self.assertIsNone(reg.find(".//modifiedWriteValues"), "pulse must not become W1C")
                self.assertIsNone(reg.find(".//readAction"))
            self.assertTrue(all(r.find("resetValue") is None for r in regs.values()))
            self.assertEqual(regs["ANALOG_CONFIG[%s]"].findtext("dim"), "3")
            self.assertEqual(regs["MANUAL_CONTROL"].findtext("fields/field[name='CHANNEL']/bitWidth"), "2")
            diag = blocks["MCU_DIAG"]
            self.assertEqual(diag.findtext("baseAddress"), "0x52004100")
            self.assertIsNone(diag.find("interrupt"))
            self.assertEqual(diag.findtext("registers/register[name='SAMPLE_DONE']/addressOffset"), "0x128")
            mode = diag.find("registers/register[name='SAMPLE_MODE']")
            self.assertEqual(mode.findtext("addressOffset"), "0x104")
            self.assertEqual(mode.findtext("fields/field[name='ENABLE']/bitOffset"), "8")
            self.assertEqual(mode.findtext("fields/field[name='SYNC']/bitOffset"), "9")
            reset = blocks["M_CRG"].find("registers/register[name='SOFT_RESET1']")
            self.assertEqual(reset.findtext("size"), "32")
            self.assertEqual(reset.findtext("fields/field/bitOffset"), "13")


if __name__ == "__main__":
    unittest.main()
