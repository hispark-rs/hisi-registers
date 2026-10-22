"""Optional ROM evidence check; ordinary PAC builds need neither SDK nor board.

uv run --locked scripts/check_ws53_sdio_rom.py --rom /path/to/sdio-rom.bin
Input is the read-only A-board capture of [0x1e872, 0x1f1e4), not redistributed.
This verifies address/access-width evidence, not device behavior or ownership.
The SDK vendor objdump must be used for a full manual control-flow audit: WS53
ROM uses proprietary compressed/48-bit instructions unknown to stock binutils.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

START, END = 0x1e872, 0x1f1e4
SHA256 = "a1ac5c03c613112f973b45bdfbfd399d3f3017b315b61d1f6b0d2a07d2ce4671"
# ROM PCs from SDK acore.sym and manual register-base data-flow audit. Decode
# the actual load/store/address immediate rather than duplicating SVD offsets.
ACCESS = {
    "CCCR_CAPABILITY": 0x1f14c,
    "GLOBAL_INTERRUPT_ENABLE": 0x1ea50,
    "GLOBAL_INTERRUPT_STATUS": 0x1f12c, "GLOBAL_INTERRUPT_ACK": 0x1f12c,
    "MODE": 0x1ebb4, "VOLTAGE_CONTROL": 0x1ec46,
    "CONFIG_2C": 0x1ec4e, "CONFIG_30": 0x1ec5c,
    "FUNCTION0_INTERRUPT_ENABLE": 0x1ea78,
    "FUNCTION0_INTERRUPT_STATUS": 0x1f13a, "FUNCTION0_INTERRUPT_ACK": 0x1f13a,
    "FUNCTION0_RESET_STATUS": 0x1edde, "FUNCTION0_RESET_COMMAND": 0x1edde,
    "ADMA_TABLE": 0x1ee9a, "HOST_CLOCK": 0x1e8ee,
    "FUNCTION1_SEND_STATUS": 0x1ea36, "FUNCTION1_SEND_COMMAND": 0x1ea36,
    "FUNCTION1_CONFIG": 0x1ec20,
    "FUNCTION1_INTERRUPT_STATUS": 0x1f134, "FUNCTION1_INTERRUPT_ACK": 0x1f134,
    "FUNCTION1_INTERRUPT_ENABLE": 0x1eb62, "FUNCTION1_FBR": 0x1eba2,
    "FUNCTION1_READY": 0x1f052, "FUNCTION1_SEND_LENGTH": 0x1ea26,
    "HOST_MESSAGE": 0x1ef9c, "DEVICE_MESSAGE": 0x1e928,
    "CMD53_ARGUMENT": 0x1f0a2, "FUNCTION1_BLOCK_SIZE": 0x1f0a6,
}


def immediate(image, pc):
    half = int.from_bytes(image[pc - START:pc - START + 2], "little")
    if half & 3 == 0 and half >> 13 in (2, 6):  # C.LW / C.SW
        return ((half >> 10 & 7) << 3) | ((half >> 6 & 1) << 2) | ((half >> 5 & 1) << 6)
    if half & 3 == 1 and half >> 13 == 0:  # C.ADDI
        value = (half >> 2 & 31) | ((half >> 12 & 1) << 5)
        return value - 64 if value & 32 else value
    if half & 3 != 3:
        raise ValueError(f"unsupported compressed instruction at {pc:#x}")
    word = int.from_bytes(image[pc - START:pc - START + 4], "little")
    opcode, funct3 = word & 0x7f, word >> 12 & 7
    if (opcode, funct3) in ((3, 2), (0x13, 0)):  # LW / ADDI
        value = word >> 20
    elif (opcode, funct3) == (0x23, 2):  # SW
        value = (word >> 25 << 5) | (word >> 7 & 31)
    else:
        raise ValueError(f"unsupported instruction at {pc:#x}: {word:#x}")
    return value - 4096 if value & 2048 else value


def validate(rom, svd):
    image = rom.read_bytes()
    if len(image) != END - START or hashlib.sha256(image).hexdigest() != SHA256:
        raise ValueError("ROM capture identity/length mismatch; re-audit before using other silicon")
    sdio = ET.parse(svd).find("./peripherals/peripheral[name='SDIO']")
    # sdio_porting_base_addr_get is LUI a0,0x52060; RET.
    getter = int.from_bytes(image[0x1f176 - START:0x1f17a - START], "little")
    if getter & 0xfff != 0x537 or int(sdio.findtext("baseAddress"), 0) != getter & 0xfffff000:
        raise ValueError("SDIO base differs from ROM getter")
    regs = {r.findtext("name"): r for r in sdio.findall("registers/register")}
    if set(regs) != set(ACCESS):
        raise ValueError("SDIO views changed: extend the independent ROM/source audit")
    for name, pc in ACCESS.items():
        if int(regs[name].findtext("addressOffset"), 0) != immediate(image, pc):
            raise ValueError(f"{name} offset differs from ROM access at {pc:#x}")
        if regs[name].findtext("size") != "32":
            raise ValueError(f"{name} access width differs from ROM word access")
    # ROM irq registration table and the direct IRQ-completion constant agree.
    irq = int.from_bytes(image[0x1f1dc - START:0x1f1de - START], "little")
    if int(sdio.findtext("interrupt/value")) != irq or irq != 46:
        raise ValueError("SDIO IRQ differs from ROM registration table")
    if image[0x1f1c8 - START:0x1f1ca - START] != bytes.fromhex("8280"):
        raise ValueError("ROM soft-reset stub changed")
    return {"rom_sha256": SHA256, "rom_start": hex(START), "rom_length": len(image),
            "register_views_checked": len(ACCESS), "irq": irq,
            "rom_soft_reset_is_noop": True, "layout_verified": True,
            "transfer_hardware_verified": False, "ownership_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--svd", type=Path, default=Path(__file__).resolve().parents[1] / "generated/WS53.experimental.svd")
    args = parser.parse_args()
    print(json.dumps(validate(args.rom, args.svd), indent=2))
