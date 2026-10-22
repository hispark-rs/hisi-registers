"""Semantic export regressions; run with uv run --project . scripts/test_export_svd.py."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from export_svd import export


class ExportSemantics(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
