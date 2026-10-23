#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Run pinned WS53 TRNG C HAL/driver with FIFO/ring stubs, without hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/security_unified/hal_cipher/trng/hal_trng.c"
REG = "src/drivers/drivers/hal/security_unified/hal_cipher/trng/hal_trng_reg.h"
DRV = "src/drivers/drivers/driver/security_unified/drv_cipher/drv_code/drv_trng.c"
CONFIG = "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"
PORT = "src/drivers/chips/ws53/porting/security_unified/security_unified_porting.h"
IPC = "src/drivers/chips/ws53/porting/security_unified/trng_ipc_acore_buffer.c"

TYPES = r"""#pragma once
#include <stdint.h>
#include <stddef.h>
typedef uint8_t td_u8; typedef uint16_t td_u16; typedef uint32_t td_u32;
typedef int32_t td_s32; typedef int td_bool; typedef void td_void;
#define TD_TRUE 1
#define TD_FALSE 0
#define TD_NULL NULL
#define TD_SUCCESS 0
#define TD_FAILURE -1
#define EOK 0
"""
COMMON = r"""#pragma once
#include "crypto_type.h"
#define ERROR_NOT_INIT 1
#define ERROR_PARAM_IS_NULL 2
#define ERROR_GET_TRNG_TIMEOUT 3
#define HAL_COMPAT_ERRNO(module, code) (-(code))
#define DRV_COMPAT_ERRNO(module, code) (-(code))
#define crypto_unused(x) ((void)(x))
#define crypto_chk_return(test, error, ...) do { if (test) return (error); } while (0)
#define TRNG_SEC_CFB 0x52001600u
static td_u32 trng_reg_read(td_u32 offset);
static td_u16 crypto_reg_read_u16(td_u32 addr);
static void crypto_ex_reg_write_u16(td_u32 addr, td_u16 value);
static void crypto_udelay(td_u32 us);
static int memcpy_s(void *dst, size_t capacity, const void *src, size_t count);
"""
HARNESS = r"""#include <assert.h>
#include <stdio.h>
#include <string.h>
#define CONFIG_TRNG_RING_ENABLE 1
#include "hal_trng.c"
#undef TRNG_COMPAT_ERRNO
#include "drv_trng.c"
static unsigned ready_reads, fifo_reads, ring_reads, ring_writes, delays;
static unsigned pairs_good, ready_mode, bad_pairs, failure_after;
static td_u16 ring;
static td_u32 first, comparison;
static td_u16 crypto_reg_read_u16(td_u32 addr) {
    assert(addr == TRNG_SEC_CFB); ring_reads++; return ring;
}
static void crypto_ex_reg_write_u16(td_u32 addr, td_u16 value) {
    assert(addr == TRNG_SEC_CFB); ring_writes++; ring = value;
}
static void crypto_udelay(td_u32 us) { assert(us == 200); delays++; }
static int memcpy_s(void *dst, size_t capacity, const void *src, size_t count) {
    assert(count <= capacity); memcpy(dst, src, count); return EOK;
}
static td_u32 trng_reg_read(td_u32 offset) {
    if (offset == HISC_COM_TRNG_FIFO_READY) {
        ready_reads++;
        if (failure_after && pairs_good >= failure_after) return 0;
        if (ready_mode <= 3) return ready_mode;
        if (ready_mode == 4) return (ready_reads & 1) ? 3 : 0;
        assert(ready_mode == 5);
        return ready_reads <= 3 ? 0 : 3;
    }
    assert(offset == HISC_COM_TRNG_FIFO_DATA);
    unsigned pair = fifo_reads / 2;
    td_u32 value;
    if (bad_pairs == 1) value = 0;
    else if (bad_pairs == 2) value = 0xffffffffu;
    else if (bad_pairs == 3) value = 0x13579u;
    else if (bad_pairs == 4 && pair < 3) {
        const td_u32 rejected[] = {0, 7, 0xffffffffu, 8, 9, 9};
        value = rejected[fifo_reads];
    } else value = (fifo_reads & 1) ? comparison : first + pairs_good;
    fifo_reads++;
    if ((fifo_reads & 1) == 0 && (bad_pairs == 0 || (bad_pairs == 4 && pair >= 3))) pairs_good++;
    return value;
}
static void reset(void) {
    ready_reads = fifo_reads = ring_reads = ring_writes = delays = 0;
    pairs_good = bad_pairs = failure_after = 0; ready_mode = 3;
    first = 0x12345678; comparison = 0x87654321;
}
int main(void) {
    unsigned paths = 0, byte_cases = 0;
    td_u32 word = 0xdeadbeef;
    reset();
    assert(hal_cipher_trng_get_random(&word) == -ERROR_NOT_INIT);
    assert(word == 0xdeadbeef && ready_reads == 0 && fifo_reads == 0); paths++;
    ring = 0xa5a4;
    assert(hal_cipher_trng_init() == 0 && ring == 0xa5a5);
    assert(ring_reads == 1 && ring_writes == 1 && delays == 1); paths++;
    reset();
    assert(hal_cipher_trng_init() == 0 && ring == 0xa5a5);
    assert(ring_reads == 1 && ring_writes == 0 && delays == 1); paths++;
    reset(); assert(hal_cipher_trng_get_random(NULL) == -ERROR_PARAM_IS_NULL);
    assert(ready_reads == 0 && fifo_reads == 0); paths++;
    for (ready_mode = 0; ready_mode <= 4; ready_mode++) {
        ready_reads = fifo_reads = pairs_good = 0; word = 0xdeadbeef;
        int result = hal_cipher_trng_get_random(&word);
        if (ready_mode == 3) {
            assert(result == 0 && word == first && ready_reads == 2 && fifo_reads == 2);
        } else {
            assert(result == -ERROR_GET_TRNG_TIMEOUT && word == 0xdeadbeef && fifo_reads == 0);
            assert(ready_reads == (ready_mode == 4 ? 2000000u : 1000000u));
        }
        assert(delays == 0); paths++;
    }
    reset(); ready_mode = 5;
    assert(hal_cipher_trng_get_random(&word) == 0 && word == first && ready_reads == 5 && fifo_reads == 2); paths++;
    for (unsigned bad = 1; bad <= 3; bad++) {
        reset(); bad_pairs = bad; word = 0xdeadbeef;
        assert(hal_cipher_trng_get_random(&word) == -ERROR_GET_TRNG_TIMEOUT);
        assert(word == (bad == 1 ? 0 : bad == 2 ? 0xffffffffu : 0x13579u));
        assert(ready_reads == 2000000 && fifo_reads == 2000000 && delays == 0); paths++;
    }
    reset(); bad_pairs = 4;
    assert(hal_cipher_trng_get_random(&word) == 0 && word == first);
    assert(ready_reads == 8 && fifo_reads == 8 && pairs_good == 1); paths++;
    for (unsigned i = 0; i < 2; i++) {
        reset(); comparison = i ? 0xffffffffu : 0;
        assert(hal_cipher_trng_get_random(&word) == 0 && word == first); paths++;
    }
    reset(); assert(hal_cipher_trng_get_random(&word) == 0); td_u32 previous = word;
    reset(); assert(hal_cipher_trng_get_random(&word) == 0 && word == previous); paths++;
    reset(); assert(hal_cipher_trng_deinit() == 0 && ring == 0xa5a4);
    assert(ring_reads == 1 && ring_writes == 1 && delays == 0); paths++;
    reset(); assert(hal_cipher_trng_deinit() == 0);
    assert(ring_reads == 1 && ring_writes == 0); paths++;
    assert(drv_cipher_trng_get_multi_random(0, NULL) == -ERROR_NOT_INIT); paths++;
    assert(drv_cipher_trng_init() == 0); reset();
    assert(drv_cipher_trng_init() == 0 && ring_reads == 0 && delays == 0); paths++;
    assert(drv_cipher_trng_get_multi_random(0, NULL) == -ERROR_PARAM_IS_NULL); paths++;
    for (unsigned count = 0; count <= 17; count++) {
        td_u8 data[20]; memset(data, 0xa5, sizeof(data)); reset();
        int result = drv_cipher_trng_get_multi_random(count, data + 1);
        assert(result == (count ? 0 : TD_FAILURE));
        assert(fifo_reads == 2 * ((count + 3) / 4));
        assert(data[0] == 0xa5);
        for (unsigned i = 0; i < count; i++) {
            td_u32 expected = first + i / 4;
            assert(data[1+i] == ((td_u8 *)&expected)[i % 4]);
        }
        for (unsigned i = 1+count; i < sizeof(data); i++) assert(data[i] == 0xa5);
        byte_cases++;
    }
    /* A later timeout preserves the written prefix, not an all-or-nothing fill. */
    for (unsigned completed = 1; completed <= 3; completed++) {
        td_u8 data[16]; memset(data, 0xa5, sizeof(data)); reset(); failure_after = completed;
        assert(drv_cipher_trng_get_multi_random(sizeof(data), data) == -ERROR_GET_TRNG_TIMEOUT);
        assert(fifo_reads == 2 * completed);
        for (unsigned i = 0; i < completed*4; i++) {
            td_u32 expected = first + i / 4; assert(data[i] == ((td_u8 *)&expected)[i % 4]);
        }
        for (unsigned i = completed*4; i < sizeof(data); i++) assert(data[i] == 0xa5);
        byte_cases++;
    }
    reset(); assert(drv_cipher_trng_deinit() == 0 && ring == 0xa5a4);
    assert(ring_reads == 1 && ring_writes == 1); paths++;
    reset(); assert(drv_cipher_trng_deinit() == 0 && ring_reads == 0); paths++;
    printf("{\"hal_driver_paths\":%u,\"byte_fill_cases\":%u}\n", paths, byte_cases);
    return 0;
}
"""


def require(value, message):
    if not value:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    inputs = {}
    for path in (HAL, REG, DRV, CONFIG, PORT, IPC):
        inputs[path] = subprocess.check_output(["git", "-C", str(sdk), "show", f"{PIN}:{path}"])
        require(inputs[path] == (sdk/path).read_bytes(), f"SDK input differs from pinned commit: {path}")
    config = inputs[CONFIG].decode()
    for name in ("SECURITY_UNIFIED_SUPPORT_TRNG", "TRNG_RING_ENABLE"):
        require(f"CONFIG_{name}=y" in config, f"unselected option: {name}")
    for name in ("ENTROPY_SOURCES_FROM_FRO", "TRNG_CLK_OPEN"):
        require(f"CONFIG_{name}=y" not in config, f"unsupported option selected: {name}")
    svd = ET.parse(Path(__file__).resolve().parents[1]/"generated/WS53.experimental.svd")
    block = svd.find("./peripherals/peripheral[name='TRNG']")
    require(block is not None and int(block.findtext("baseAddress"), 0) == 0x52024000, "TRNG base differs")
    expected = {"FIFO_DATA": (0x100, "read-only"), "FIFO_READY": (0x104, "read-only"),
                "FRO_SAMPLE_CLK_SEL": (0x174, "read-write"), "FRO_DIV_CNT": (0x178, "read-write")}
    regs = block.findall("registers/register")
    require(len(regs) == len(expected) and not block.findall("interrupt"), "unsupported registers/IRQ")
    for reg in regs:
        require((int(reg.findtext("addressOffset"), 0), reg.findtext("access")) == expected[reg.findtext("name")], "layout/access differs")
        require(reg.findtext("size") == "32" and reg.find("resetValue") is None, "unsupported size/reset")
    fifo = block.find("registers/register[name='FIFO_DATA']")
    require(fifo.findtext("readAction") == "modifyExternal", "FIFO consumption lost")
    ready = block.find("registers/register[name='FIFO_READY']")
    require([(f.findtext("name"), f.findtext("bitOffset"), f.findtext("bitWidth")) for f in ready.findall("fields/field")]
            == [("DATA_READY", "0", "1"), ("DONE", "1", "1")], "ready flags differ")
    ring = svd.find("./peripherals/peripheral[name='M_CRG']/registers/register[name='TRNG_RING_CONTROL']")
    require(ring is not None and ring.findtext("size") == "16" and int(ring.findtext("addressOffset"), 0) == 0x1600,
            "ring access width/offset differs")
    with tempfile.TemporaryDirectory(prefix="ws53-trng-source-") as temp:
        work = Path(temp)
        for path in (HAL, REG, DRV):
            (work/Path(path).name).write_bytes(inputs[path])
        (work/"crypto_type.h").write_text(TYPES)
        (work/"crypto_drv_common.h").write_text(COMMON)
        for header in ("hal_cipher_trng.h", "drv_trng.h", "drv_inner.h", "crypto_common_macro.h"):
            (work/header).write_text('#pragma once\n#include "crypto_type.h"\n')
        (work/"check.c").write_text(HARNESS)
        subprocess.run([args.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(work),
                        str(work/"check.c"), "-o", str(work/"check")], check=True)
        result = json.loads(subprocess.check_output([str(work/"check")], timeout=10))
    require(result == {"hal_driver_paths": 24, "byte_fill_cases": 21}, "missing oracle paths")
    print(json.dumps({"sdk_commit": PIN, "input_sha256": {path: hashlib.sha256(data).hexdigest() for path, data in inputs.items()},
        **result, "observations": ["ring RMW preserves upper 15 bits; 200us init delay even if already enabled",
        "both ready flags checked twice before two destructive FIFO reads; no readiness check between words",
        "zero/all-ones/equal comparison rejected; comparison discarded",
        "comparison itself may be zero/all-ones; same candidate in successive calls is accepted",
        "million loop budget without delay; up to two million status reads and two million FIFO reads",
        "HAL output can contain rejected data on failure; no write when readiness never passes",
        "driver fills native-endian words and one extra candidate for a partial tail",
        "zero-byte non-null fill returns failure; null rejected even for zero length",
        "later timeout leaves written prefix and untouched suffix; no output rollback"],
        "limitations": ["FIFO readiness depth and entropy quality not proven", "no IPC callback execution or ownership handoff",
                        "optional FRO and undefined CLK_OPEN branches not enabled", "return-code plumbing and MMIO/delay/copy are host stubs"],
        "hardware_accessed": False}, indent=2))


if __name__ == "__main__":
    main()
