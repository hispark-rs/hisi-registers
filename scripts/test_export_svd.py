"""Semantic export regressions; run with uv run --project . scripts/test_export_svd.py."""
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from export_svd import export


class ExportSemantics(unittest.TestCase):
    def test_ws53_sfc_clock_views_reject_invented_layout_or_frequency(self):
        from check_ws53_sfc_clock import validate_layout
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work)/'ws53.svd'
            export(Path(__file__).resolve().parents[1]/'rdl/chips/ws53.rdl', dest)
            layout = validate_layout(dest)
            self.assertEqual(layout['address'], 0x52000570)
            self.assertEqual(layout['access_widths'], [16, 32])
            original = dest.read_bytes()
            mutations = [
                ('XIP_SFC_CLOCK', 'addressOffset', '0x574'),
                ('XIP_SFC_CLOCK', 'size', '32'),
                ('XIP_SFC_CLOCK', 'access', 'read-only'),
                ('XIP_SFC_CLOCK', 'resetValue', '0'),
                ('XIP_SFC_CLOCK', 'resetMask', '0xf'),
                ('XIP_SFC_CLOCK', 'readAction', 'clear'),
                ('XIP_SFC_CLOCK', 'fields/field/modifiedWriteValues', 'oneToClear'),
                ('XIP_SFC_CLOCK', "fields/field[name='SOURCE_CONFIG']/bitOffset", '2'),
                ('XIP_SFC_CLOCK', "fields/field[name='SOURCE_CONFIG']/bitWidth", '4'),
                ('XIP_SFC_CLOCK', "fields/field[name='ENABLE']/access", 'write-only'),
                ('XIP_SFC_CLOCK', 'fields/field/enumeratedValues', ''),
                ('XIP_SFC_CLOCK', 'writeConstraint', ''),
                ('XIP_SFC_CLOCK_WORD', 'alternateRegister', 'XIP_QSPI_CLOCK'),
                ('XIP_SFC_CLOCK_WORD', 'size', '16'),
                ('XIP_SFC_CLOCK_WORD', 'addressOffset', '0x572'),
                ('XIP_SFC_CLOCK_WORD', 'resetValue', '0'),
            ]
            for name, tag, value in mutations:
                with self.subTest(name=name, tag=tag):
                    tree = ET.fromstring(original)
                    reg = tree.find(f"./peripherals/peripheral[name='M_CRG']/registers/register[name='{name}']")
                    node = reg.find(tag)
                    if node is None:
                        parent, _, leaf = tag.rpartition('/')
                        node = ET.SubElement(reg.find(parent) if parent else reg, leaf)
                    node.text = value
                    ET.ElementTree(tree).write(dest)
                    with self.assertRaises(ValueError):
                        validate_layout(dest)
            for kind in ('wrong-block', 'missing-word', 'invented-upper-field'):
                with self.subTest(kind=kind):
                    tree = ET.fromstring(original)
                    block = tree.find("./peripherals/peripheral[name='M_CRG']")
                    if kind == 'wrong-block':
                        block.find('baseAddress').text = '0x57000000'
                    elif kind == 'missing-word':
                        regs = block.find('registers')
                        regs.remove(regs.find("register[name='XIP_SFC_CLOCK_WORD']"))
                    else:
                        reg = block.find("registers/register[name='XIP_SFC_CLOCK_WORD']")
                        field = ET.SubElement(reg.find('fields'), 'field')
                        for tag, value in (('name', 'UPPER'), ('bitOffset', '16'), ('bitWidth', '16'),
                                           ('access', 'read-write')):
                            ET.SubElement(field, tag).text = value
                    ET.ElementTree(tree).write(dest)
                    with self.assertRaises(ValueError):
                        validate_layout(dest)

    def test_ws53_sfc_command_layout_rejects_fabricated_status_contracts(self):
        from check_ws53_sfc_contract import validate_layout
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work)/'ws53.svd'
            export(Path(__file__).resolve().parents[1]/'rdl/chips/ws53.rdl', dest)
            self.assertEqual(validate_layout(dest)['register_slots'], 28)
            original = dest.read_bytes()
            mutations = [
                ('CMD_CONFIG', 'addressOffset', '0x304'),
                ('CMD_CONFIG', 'access', 'write-only'),
                ('CMD_CONFIG', 'readAction', 'clear'),
                ('CMD_CONFIG', 'fields/field/modifiedWriteValues', 'oneToClear'),
                ('CMD_CONFIG', 'resetValue', '0'),
                ('BUS_DMA_CTRL', 'readAction', 'clear'),
                ('BUS_DMA_CTRL', 'fields/field/access', 'write-only'),
                ('CMD_DATABUF_0', 'readAction', 'modifyExternal'),
                ('CMD_DATABUF_15', 'size', '16'),
                ('CMD_INS', "fields/field[name='CMD_DDR_MODE']/bitOffset", '9'),
                ('CMD_ADDR', 'fields/field/bitWidth', '32'),
            ]
            for reg_name, tag, value in mutations:
                with self.subTest(reg=reg_name, tag=tag):
                    tree = ET.fromstring(original)
                    reg = tree.find(f"./peripherals/peripheral[name='SFC_CFG']/registers/register[name='{reg_name}']")
                    node = reg.find(tag)
                    if node is None:
                        parent_path, _, leaf = tag.rpartition('/')
                        node = ET.SubElement(reg.find(parent_path) if parent_path else reg, leaf)
                    node.text = value
                    ET.ElementTree(tree).write(dest)
                    with self.assertRaises(ValueError):
                        validate_layout(dest)
            for kind in ('legacy', 'irq'):
                tree = ET.fromstring(original)
                block = tree.find("./peripherals/peripheral[name='SFC_CFG']")
                if kind == 'legacy':
                    reg = ET.SubElement(block.find('registers'), 'register')
                    ET.SubElement(reg, 'name').text = 'INT_STATUS'
                else:
                    irq = ET.SubElement(block, 'interrupt')
                    ET.SubElement(irq, 'name').text = 'SFC'
                    ET.SubElement(irq, 'value').text = '36'
                ET.ElementTree(tree).write(dest)
                with self.assertRaises(ValueError):
                    validate_layout(dest)

    def test_ws53_sfc_hardware_updates_are_instance_specific(self):
        from systemrdl import RDLCompiler
        from systemrdl.rdltypes import AccessType
        root = Path(__file__).resolve().parents[1]
        for chip in ('ws53', 'ws63', 'bs2x'):
            compiler = RDLCompiler()
            compiler.compile_file(str(root/f'rdl/chips/{chip}.rdl'))
            sfc = next(node for node in compiler.elaborate().top.children()
                       if node.inst_name.lower() == 'sfc_cfg')
            regs = {node.inst_name: node for node in sfc.children()}
            expected = AccessType.rw if chip == 'ws53' else AccessType.r
            for reg, field in [('CMD_CONFIG', 'start'), ('BUS_DMA_CTRL', 'dma_start'),
                               *((f'CMD_DATABUF_{i}', f'cmd_databuf_{i}') for i in range(16))]:
                f = regs[reg].get_child_by_name(field)
                self.assertEqual(f.get_property('hw'), expected, (chip, reg))
                self.assertEqual(f.get_property('sw'), AccessType.rw)
                self.assertIsNone(f.get_property('onread'))
                self.assertIsNone(f.get_property('onwrite'))
            for name in ('INT_RAW_STATUS', 'INT_STATUS', 'INT_MASK', 'INT_CLEAR', 'SOFT_RST_MASK'):
                self.assertEqual(name in regs, chip != 'ws53', (chip, name))

    def test_ws53_msub_word_views_preserve_existing_halfword_contract(self):
        from check_ws53_msub_access import validate_layout
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work)/'ws53.svd'
            export(Path(__file__).resolve().parents[1]/'rdl/chips/ws53.rdl', dest)
            layout = validate_layout(dest)
            self.assertEqual({name: p['address'] for name, p in layout.items()},
                             {'MSUB_NORMAL': 0x520003e0, 'MSUB_SLEEP': 0x520003e4})
            self.assertEqual(layout['MSUB_NORMAL']['fields']['MDMA_ENABLE'], 3)
            self.assertEqual(layout['MSUB_SLEEP']['fields']['SDMA_ENABLE'], 4)
            original = dest.read_bytes()
            for tag, value in (('size', '16'), ('alternateRegister', 'MSUB_SLEEP'),
                               ('addressOffset', '0x3e2'), ('resetValue', '0')):
                with self.subTest(tag=tag):
                    root = ET.fromstring(original)
                    word = root.find("./peripherals/peripheral[name='M_CRG']/registers/register[name='MSUB_NORMAL_WORD']")
                    node = word.find(tag)
                    if node is None:
                        node = ET.SubElement(word, tag)
                    node.text = value
                    ET.ElementTree(root).write(dest)
                    with self.assertRaises(ValueError):
                        validate_layout(dest)

    def word_view_fixture(self, reg_props='', field_props='', name='CTRL_WORD',
                          bits=16, offset=0, tail=4, array='', evidence='source audit'):
        properties = Path(__file__).resolve().parents[1] / 'rdl/properties.rdl'
        return f'''`include "{properties}"
addrmap chip {{ addrmap {{
    reg {{ regwidth={bits}; accesswidth={bits};
        hisi_svd_word_view="{name}"; hisi_evidence="{evidence}"; {reg_props}
        field {{ sw=rw; hw=rw; {field_props} }} enable[2:2];
    }} ctrl{array} @ {offset};
    reg {{ regwidth=16; accesswidth=16; field {{ sw=rw; hw=r; }} value[15:0]; }} next @ {tail};
}} clock @ 0x1000; }};
'''

    def test_word_view_preserves_halfword_fields_without_reset_or_new_bits(self):
        from check_schema import validate
        with tempfile.TemporaryDirectory() as work:
            source, dest = Path(work)/'test.rdl', Path(work)/'test.svd'
            source.write_text(self.word_view_fixture())
            export(source, dest)
            regs = ET.parse(dest).findall('./peripherals/peripheral/registers/register')
            half, word, neighbor = regs
            self.assertEqual([r.findtext('name') for r in regs], ['CTRL', 'CTRL_WORD', 'NEXT'])
            self.assertEqual([r.findtext('size') for r in regs], ['16', '32', '16'])
            self.assertEqual(half.findtext('addressOffset'), word.findtext('addressOffset'))
            self.assertEqual(word.findtext('alternateRegister'), 'CTRL')
            self.assertEqual(ET.tostring(half.find('fields')), ET.tostring(word.find('fields')))
            self.assertEqual(word.findtext('access'), 'read-write')
            self.assertIsNone(word.find('resetValue'))
            self.assertIsNone(word.find('resetMask'))
            self.assertEqual(neighbor.findtext('addressOffset'), '0x4')
            validate([dest])

    def test_word_view_rejects_ambiguous_or_unsafe_declarations(self):
        cases = [dict(name=''), dict(name='NEXT'), dict(name='CTRL'), dict(name='lower'),
                 dict(bits=32), dict(bits=8), dict(offset=2, tail=8), dict(evidence=''),
                 dict(field_props='onwrite=woclr;'), dict(field_props='onread=rclr;'),
                 dict(reg_props='hisi_write_min=0; hisi_write_max=7;'),
                 dict(array='[2]', tail=8), dict(field_props='reset=0;')]
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as work:
                source, dest = Path(work)/'test.rdl', Path(work)/'test.svd'
                source.write_text(self.word_view_fixture(**case))
                with self.assertRaisesRegex(ValueError, 'invalid word access view'):
                    export(source, dest)

    def test_word_view_rejects_neighbor_overlap_and_trailing_extension(self):
        for tail in (2,):
            with tempfile.TemporaryDirectory() as work:
                source, dest = Path(work)/'test.rdl', Path(work)/'test.svd'
                source.write_text(self.word_view_fixture(tail=tail))
                with self.assertRaisesRegex(ValueError, 'exceeds vacant slot'):
                    export(source, dest)
        with tempfile.TemporaryDirectory() as work:
            source, dest = Path(work)/'test.rdl', Path(work)/'test.svd'
            # The exporter cannot silently widen the peripheral's extent.
            body = self.word_view_fixture()
            body = body[:body.rindex('    reg { regwidth=16;')] + '} clock @ 0x1000; };'
            source.write_text(body)
            with self.assertRaisesRegex(ValueError, 'exceeds vacant slot'):
                export(source, dest)

    def test_ws53_i2s_clock_views_keep_shared_field_encodings(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work)/'ws53.svd'
            export(Path(__file__).resolve().parents[1]/'rdl/chips/ws53.rdl', dest)
            tree = ET.parse(dest)
            width_fields = tree.findall("./peripherals/peripheral[name='I2S']/registers/register[name='DATA_WIDTH_SET']/fields/field")
            self.assertEqual(len(width_fields), 2)
            for field in width_fields:
                self.assertEqual({v.findtext('name'): int(v.findtext('value'), 0)
                                  for v in field.findall('enumeratedValues/enumeratedValue')},
                                 {'Bits16': 1, 'Bits18': 2, 'Bits20': 3, 'Bits24': 4, 'Bits32': 5})
                self.assertEqual(field.findtext('writeConstraint/range/minimum'), '1')
                self.assertEqual(field.findtext('writeConstraint/range/maximum'), '5')
            clock = tree.find("./peripherals/peripheral[name='M_CRG']")
            half = clock.find("registers/register[name='M_CLKEN0']")
            word = clock.find("registers/register[name='M_CLKEN0_WORD']")
            self.assertEqual(half.findtext('size'), '16')
            self.assertEqual(word.findtext('size'), '32')
            self.assertEqual(word.findtext('addressOffset'), '0x40')
            self.assertEqual(word.findtext('alternateRegister'), 'M_CLKEN0')
            self.assertEqual(ET.tostring(half.find('fields')), ET.tostring(word.find('fields')))
            self.assertEqual(word.findtext("fields/field[name='I2S_ENABLE']/bitOffset"), '2')
            cmu = tree.find("./peripherals/peripheral[name='AUDIO_CMU']")
            self.assertEqual(cmu.findtext('baseAddress'), '0x57008600')
            self.assertFalse(cmu.findall('interrupt'))
            reg = cmu.find('registers/register')
            self.assertEqual(reg.findtext('size'), '32')
            self.assertEqual(reg.findtext('access'), 'read-write')
            self.assertEqual(reg.findtext('fields/field/bitOffset'), '0')
            self.assertEqual(reg.findtext('fields/field/bitWidth'), '1')
            self.assertIsNone(reg.find('resetValue'))

    def test_ws53_pwm_profile_withholds_resets_and_exposes_event_enable(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            pwm = tree.find("./peripherals/peripheral[name='PWM']")
            regs = {r.findtext("name"): r for r in pwm.findall("registers/register")}
            self.assertEqual(len(regs), 115)
            self.assertEqual(int(pwm.findtext("baseAddress"), 0), 0x52082000)
            self.assertEqual({i.findtext("name"): int(i.findtext("value")) for i in pwm.findall("interrupt")},
                             {"PWM_ABNOR": 71, "PWM_CFG": 72})
            for reg in regs.values():
                self.assertEqual(reg.findtext("size"), "32")
                self.assertIsNone(reg.find("resetValue"))
                self.assertIsNone(reg.find("resetMask"))
                self.assertIsNone(reg.find(".//modifiedWriteValues"), "commands are not assumed W1C storage")
            for group in range(4):
                for suffix, offset in (("SEL", 0), ("STARTCLRCNT_EN", 4), ("START", 8)):
                    reg = regs[f"PWM_{suffix}{group}"]
                    self.assertEqual(int(reg.findtext("addressOffset"), 0), group*16+offset)
                command = regs[f"PWM_START{group}"]
                self.assertEqual(command.findtext("access"), "write-only")
                for bound in ("minimum", "maximum"):
                    self.assertEqual(command.findtext("fields/field/writeConstraint/range/"+bound), "1")
            for channel in range(8):
                for suffix, off in (("OFFSET", 0x0c), ("FREQ", 0x14), ("DUTY", 0x1c)):
                    for half, delta in (("L", 0), ("H", 4)):
                        reg = regs[f"PWM_{suffix}_{half}{channel}"]
                        self.assertEqual(int(reg.findtext("addressOffset"), 0), 0x100+channel*64+off+delta)
                        self.assertEqual(reg.findtext("fields/field/bitWidth"), "16")
                for suffix in ("PERIODLOAD_FLAG", "PERIODCNT"):
                    self.assertEqual(regs[f"PWM_{suffix}{channel}"].findtext("access"), "read-only")
            for suffix in ("ABNOR_STATE_CLR0", "ABNOR_STATE_CLR1", "CFG_INT_CLR0"):
                self.assertEqual(regs[f"PWM_{suffix}"].findtext("access"), "write-only")
            enum = regs["PWM_INT_MASK"].findall("fields/field/enumeratedValues/enumeratedValue")
            self.assertEqual({v.findtext("name"): int(v.findtext("value")) for v in enum},
                             {"Disabled": 0, "AbnormalOnly": 1, "ConfigurationLoadedOnly": 2, "AllEvents": 3})
            clocks = tree.find("./peripherals/peripheral[name='M_CRG']")
            div = clocks.find("registers/register[name='PWM_M_DIV3']")
            self.assertEqual(int(div.findtext("addressOffset"), 0), 0x7c)
            self.assertEqual(div.findtext("size"), "16")
            self.assertIsNone(div.find("resetValue"))
            self.assertEqual({f.findtext("name"): (int(f.findtext("bitOffset")), int(f.findtext("bitWidth")))
                              for f in div.findall("fields/field")},
                             {"ENABLE": (0, 1), "LOAD": (1, 1), "DIVIDER": (2, 5)})

    def test_ws53_gpio_basic_banks_commands_and_unknown_reset_values(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            for bank, width, irq in ((0, 23, 34), (1, 12, 33), (2, 3, 29)):
                p = tree.find(f"./peripherals/peripheral[name='GPIO{bank}']")
                regs = {r.findtext("name"): r for r in p.findall("registers/register")}
                self.assertEqual(len(regs), 14)
                self.assertEqual(p.findtext("interrupt/value"), str(irq))
                self.assertEqual(regs["GPIO_SW_CTL"].findtext("addressOffset"), "0x8")
                self.assertEqual(regs["GPIO_SW_CTL"].findtext("access"), "read-write")
                for reg in regs.values():
                    self.assertEqual(reg.findtext("size"), "32")
                    self.assertEqual(reg.findtext("fields/field/bitWidth"), str(width))
                    self.assertIsNone(reg.find("resetValue"))
                    self.assertIsNone(reg.find("resetMask"))
                    self.assertIsNone(reg.find(".//enumeratedValues"), "per-pin bitmap must not export a whole-field enum")
                for name, action in (("GPIO_INT_EOI", "oneToClear"), ("GPIO_DATA_SET", "oneToSet"), ("GPIO_DATA_CLR", "oneToClear")):
                    self.assertEqual(regs[name].findtext("access"), "write-only")
                    self.assertEqual(regs[name].findtext("fields/field/modifiedWriteValues"), action)
                for name in ("GPIO_INT_RAW", "GPIO_INTR"):
                    self.assertEqual(regs[name].findtext("access"), "read-only")
                self.assertNotIn("GPIO_LOCK", regs)
                self.assertNotIn("GPIO_LP_STATE", regs)

    def test_ws53_uart_reset_widths_and_restricted_route_command(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            for block, base, offset, fields in (
                ("M_CRG", 0x52000000, 0x168, {"UART_H0_RESET_N": 0, "UART_H1_RESET_N": 1}),
                ("GLB_CTL", 0x57000000, 0xb0, {"UART_L0_RESET_N": 7}),
            ):
                peripheral = tree.find(f"./peripherals/peripheral[name='{block}']")
                self.assertEqual(int(peripheral.findtext("baseAddress"), 0), base)
                reg = peripheral.find("registers/register[name='UART_SOFT_RESET']")
                self.assertEqual(int(reg.findtext("addressOffset"), 0), offset)
                self.assertEqual(reg.findtext("size"), "16")
                self.assertEqual(reg.findtext("access"), "read-write")
                self.assertIsNone(reg.find("resetValue"))
                self.assertEqual({f.findtext("name"): int(f.findtext("bitOffset"))
                                  for f in reg.findall("fields/field")}, fields)
            route = tree.find("./peripherals/peripheral[name='GLB_CTL']/registers/register[name='UART_L0_IRQ_ROUTE_COMMAND']")
            self.assertEqual(int(route.findtext("addressOffset"), 0), 0x48)
            self.assertEqual(route.findtext("size"), "32")
            self.assertEqual(route.findtext("access"), "write-only")
            self.assertIsNone(route.find("resetValue"))
            for bound in ("minimum", "maximum"):
                self.assertEqual(route.findtext("fields/field/writeConstraint/range/" + bound), "1")
            # Conflicting legacy UARTL0/H1 clock enums must not become writable gates.
            clocks = tree.find("./peripherals/peripheral[name='M_CRG']")
            for reg, reserved in (("M_CLKEN0", 9), ("M_CLKEN1", 5)):
                fields = clocks.findall(f"registers/register[name='{reg}']/fields/field")
                self.assertNotIn(reserved, [int(f.findtext("bitOffset")) for f in fields])

    def test_ws53_uart_has_word_access_and_separate_fifo_directions(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            uart = tree.find("./peripherals/peripheral[name='UART0']")
            regs = {r.findtext("name"): r for r in uart.findall("registers/register")}
            self.assertNotIn("DATA", regs)
            self.assertTrue(all(r.findtext("size") == "32" for r in regs.values()))
            self.assertIsNone(uart.find(".//resetValue"))
            self.assertEqual(regs["RX"].findtext("access"), "read-only")
            self.assertEqual(regs["RX"].findtext("fields/field/readAction"), "modifyExternal")
            self.assertEqual(regs["TX"].findtext("access"), "write-only")
            self.assertEqual(regs["TX"].findtext("alternateRegister"), "RX")
            self.assertEqual(regs["RX_FIFO_WRITE"].findtext("access"), "write-only")
            self.assertEqual(regs["RX_FIFO_CNT"].findtext("fields/field/name"), "RX_FIFO_LEVEL")
            for field in ("FRAME_ERR", "PARITY_ERR", "OVERRUN_ERR"):
                self.assertEqual(regs["LINE_STATUS"].findtext(f"fields/field[name='{field}']/readAction"), "clear")
            for name, offset in (("RX_FIFO_FULL", 2), ("RX_FIFO_EMPTY", 3), ("UART_BUSY", 4)):
                self.assertEqual(regs["FIFO_STATUS"].findtext(f"fields/field[name='{name}']/bitOffset"), str(offset))
            for i, base, irq in ((1, 0x52080000, 41), (2, 0x57014000, 42)):
                derived = tree.find(f"./peripherals/peripheral[name='UART{i}']")
                self.assertEqual(derived.get("derivedFrom"), "UART0")
                self.assertEqual(int(derived.findtext("baseAddress"), 0), base)
                self.assertEqual(int(derived.findtext("interrupt/value")), irq)

    def test_unverified_inherited_reset_is_omitted_including_arrays(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "test.rdl"
            dest = Path(work) / "test.svd"
            properties = Path(__file__).resolve().parents[1] / "rdl/properties.rdl"
            source.write_text(f'`include "{properties}"\n' + '''addrmap chip {
                addrmap {
                    reg {
                        field { sw=rw; } known[3:0] = 5;
                        field { sw=rw; hisi_reset_unverified=true; } unverified[7:4] = 9;
                    } mixed @ 0;
                } legacy @ 0x1000;
                addrmap {
                    hisi_reset_unverified=true;
                    regfile {
                        reg { field { sw=rw; hisi_reset_unverified=false; } value[7:0] = 7; } data[2] @ 0 += 4;
                    } channels[2] @ 0 += 0x10;
                } fresh @ 0x2000;
            };''')
            export(source, dest)
            tree = ET.parse(dest)
            known = tree.find("./peripherals/peripheral[name='LEGACY']/registers/register")
            self.assertEqual(int(known.findtext("resetValue"), 0), 5)
            self.assertEqual(int(known.findtext("resetMask"), 0), 0xf)
            fresh = tree.find("./peripherals/peripheral[name='FRESH']")
            self.assertIsNone(fresh.find(".//resetValue"))
            self.assertIsNone(fresh.find(".//resetMask"))

    def test_ws53_timer_uses_confirmed_channel_window_without_assumed_resets(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            tree = ET.parse(dest)
            timer = tree.find("./peripherals/peripheral[name='TIMER']")
            self.assertEqual(int(timer.findtext("addressBlock/size"), 0), 0x220)
            regs = {r.findtext("name"): r for r in timer.findall("registers/register")}
            self.assertEqual(len(regs), 12)
            for name in ("EOI_REN", "RAW_INTR_STAT", "ABNOR_IMSK", "TIMER_CONTROL2"):
                self.assertNotIn(name, regs)
            for ch in (0, 1):
                reg = regs[f"TIMER_MASKED_INTR{ch}"]
                self.assertEqual(int(reg.findtext("addressOffset"), 0), 0x11c + ch * 0x100)
                self.assertEqual(reg.findtext("access"), "read-only")
                eoi = regs[f"TIMER_EOI{ch}"]
                self.assertEqual(eoi.findtext("access"), "write-only")
                self.assertEqual(eoi.findtext("fields/field/modifiedWriteValues"), "oneToClear")
            for name in ("TIMER", "TCXO"):
                block = tree.find(f"./peripherals/peripheral[name='{name}']")
                self.assertIsNone(block.find(".//resetValue"))
                self.assertIsNone(block.find(".//resetMask"))

    def test_ws53_efuse_read_view_has_no_otp_writes_or_assumed_reset(self):
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / "ws53.svd"
            export(Path(__file__).resolve().parents[1] / "rdl/chips/ws53.rdl", dest)
            efuse = ET.parse(dest).find("./peripherals/peripheral[name='EFUSE']")
            self.assertEqual(int(efuse.findtext("baseAddress"), 0), 0x57024000)
            self.assertIsNone(efuse.find("interrupt"))
            regs = {r.findtext("name"): r for r in efuse.findall("registers/register")}
            self.assertEqual(set(regs), {"BOOT_STATUS", "MODE_STATUS", "READ_MODE_COMMAND", "CLOCK_PERIOD", "DATA_READ[%s]"})
            for name, offset, access in (
                ("BOOT_STATUS", 0x2c, "read-only"), ("MODE_STATUS", 0x30, "read-only"),
                ("READ_MODE_COMMAND", 0x30, "write-only"), ("CLOCK_PERIOD", 0x34, "read-write"),
                ("DATA_READ[%s]", 0x800, "read-only"),
            ):
                self.assertEqual(int(regs[name].findtext("addressOffset"), 0), offset)
                self.assertEqual(regs[name].findtext("access"), access)
                self.assertEqual(regs[name].findtext("size"), "32")
                self.assertIsNone(regs[name].find("resetValue"))
            cmd = regs["READ_MODE_COMMAND"]
            self.assertEqual(cmd.findtext("alternateRegister"), "MODE_STATUS")
            for bound in ("minimum", "maximum"):
                self.assertEqual(int(cmd.findtext("fields/field/writeConstraint/range/" + bound)), 0x5a5a)
            data = regs["DATA_READ[%s]"]
            self.assertEqual(data.findtext("dim"), "128")
            self.assertEqual(int(data.findtext("dimIncrement"), 0), 4)
            fields = {f.findtext("name"): f for f in data.findall("fields/field")}
            self.assertEqual(set(fields), {"EVEN_BYTE", "ODD_BYTE"})
            for name, shift in (("EVEN_BYTE", 0), ("ODD_BYTE", 8)):
                self.assertEqual(int(fields[name].findtext("bitOffset")), shift)
                self.assertEqual(fields[name].findtext("bitWidth"), "8")

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
            core_reset = peripherals["SYSTEM_MEMORY"].find("registers/register[name='CCORE_RESET_STATUS']")
            self.assertEqual(core_reset.findtext("addressOffset"), "0x96C")
            self.assertEqual(core_reset.findtext("access"), "read-only")
            self.assertEqual(core_reset.findtext("fields/field/bitWidth"), "3")
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
