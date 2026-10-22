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


if __name__ == "__main__":
    unittest.main()
