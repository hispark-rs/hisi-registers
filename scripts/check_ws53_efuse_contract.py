#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned eFuse READ paths on protected anonymous host RAM, never hardware.

Linux/x86-64 MAP_32BIT is needed because the original SDK stores addresses in u32.
Programming routines are compiled as context but never invoked. Ordinary builds
and PAC generation do not depend on this optional source-evidence check.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import tempfile

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/efuse/v151/hal_efuse_v151.c"
REG = "src/drivers/drivers/hal/efuse/v151/hal_efuse_v151_reg_def.h"
OP = "src/drivers/drivers/hal/efuse/v151/hal_efuse_v151_reg_op.h"
API = "src/drivers/drivers/hal/efuse/hal_efuse.h"
PORT = "src/drivers/chips/ws53/porting/efuse/efuse_porting.h"
PORT_C = "src/drivers/chips/ws53/porting/efuse/efuse_porting.c"
DRV = "src/drivers/drivers/driver/efuse/efuse.c"
CONFIG = "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"


def require(ok, message):
    if not ok:
        raise ValueError(message)


def function(source, name):
    match = re.search(r"^(?:static )?(?:errcode_t|uint16_t|hal_efuse_region_t) " + name
                      + r"\([^)]*\)\n\{.*?^\}", source, re.M | re.S)
    require(match, f"pinned function not found: {name}")
    return match[0]


HEADER = r"""#define _GNU_SOURCE
#include <assert.h>
#include <sys/mman.h>
#include <unistd.h>
#include <string.h>
#include <limits.h>
#define CONFIG_EFUSE_REGION_NUM 1
#define CONFIG_EFUSE_SWITCH_EN 1
#include "hal_efuse_v151.c"
static efuse_regs_t control;
static efuse_boot_done_regs_t boot;
static efuse_switch_en_regs_t program_switch;
efuse_regs_t *g_efuse_regs[1] = {&control};
efuse_boot_done_regs_t *g_efuse_boot_done_regs = &boot;
efuse_switch_en_regs_t *g_efuse_switch_en_regs = &program_switch;
uint32_t g_efuse_region_read_address[1];
static unsigned calls, fail_at, locks, restores, init_calls;
static uint32_t osal_irq_lock(void) { assert(locks == restores); locks++; return 0xace; }
static void osal_irq_restore(uint32_t value) { assert(value == 0xace && locks == restores+1); restores++; }
int32_t hal_efuse_regs_init(int32_t region) { assert(region == 0); init_calls++; return 0; }
void uapi_tcxo_delay_us(uint64_t us) { (void)us; assert(!"programming delay called"); }
hal_efuse_funcs_t *hal_efuse_get_funcs(void) { return &g_hal_efuse_funcs; }
static errcode_t observed_read(uint32_t address, uint8_t *value) {
    assert(locks == restores+1); calls++;
    if (calls == fail_at) return ERRCODE_FAIL;
    /* Verify original volatile helper preserves upper bits for each read. */
    control.efuse_ctl_data.d32 = 0xcafe1234;
    errcode_t result = hal_efuse_read_byte(address, value);
    assert(control.efuse_ctl_data.d32 == 0xcafe5a5a);
    return result;
}
"""
MAIN = r"""
int main(void) {
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    uint32_t *data = mmap(NULL, page, PROT_READ|PROT_WRITE,
                         MAP_PRIVATE|MAP_ANONYMOUS|MAP_32BIT, -1, 0);
    assert(data != MAP_FAILED && (uintptr_t)data <= UINT32_MAX-page);
    g_efuse_region_read_address[0] = (uint32_t)(uintptr_t)data;
    uint8_t expected[256];
    for (unsigned n=0;n<256;n++) expected[n] = (uint8_t)(n*73+19);
    for (unsigned n=0;n<128;n++) data[n] = 0xdead0000u | expected[n*2] | (expected[n*2+1]<<8);
    /* A regression accidentally writing the OTP window faults in host memory. */
    assert(mprotect(data, page, PROT_READ) == 0);
    control.efuse_ctl_data.d32 = 0xface1234;
    control.efuse_clk_period_data.d32 = 0x12345678;
    control.efuse_avdd_ctl_data.d32 = 0x89abcdef;
    program_switch.efuse_switch_en.d32 = 0xfedcba98;
    uint8_t value=0xa5;
    assert(hal_efuse_read_byte(0,&value) == ERRCODE_EFUSE_NOT_INIT);
    assert(value == 0xa5 && control.efuse_ctl_data.d32 == 0xface1234);
    assert(hal_efuse_init() == 0 && init_calls == 1);
    assert(control.efuse_clk_period_data.d32 == 0x1234561f);
    /* Out-of-field clock input is silently truncated in the C bitfield. */
    hal_efuse_set_clock_period(0x1ff); assert(hal_efuse_init() == 0);
    assert(control.efuse_clk_period_data.d32 == 0x123456ff);
    hal_efuse_set_clock_period(0x1f); assert(hal_efuse_init() == 0);
    unsigned byte_cases=0;
    for (unsigned status=0;status<32;status++) {
        boot.efuse_sts_data.d32 = status;
        assert(hal_efuse_sts_get(0) == ((status>>2)&1));
        for (unsigned n=0;n<256;n++) {
            control.efuse_ctl_data.d32 = 0xcafe4321;
            value=0xa5;
            assert(hal_efuse_read_byte(n,&value)==0 && value==expected[n]);
            assert(control.efuse_ctl_data.d32==0xcafe5a5a);
            byte_cases++;
        }
    }
    const uint32_t invalid[] = {256,257,0xffff,0xffffffff};
    for (unsigned n=0;n<sizeof(invalid)/sizeof(invalid[0]);n++) {
        control.efuse_ctl_data.d32=0xfeed1234; value=0xa5;
        assert(hal_efuse_read_byte(invalid[n],&value)==ERRCODE_INVALID_PARAM);
        assert(control.efuse_ctl_data.d32==0xfeed1234 && value==0xa5);
    }
    assert(hal_efuse_read_byte(0,NULL)==ERRCODE_INVALID_PARAM);
    assert(control.efuse_ctl_data.d32==0xfeed1234);
    g_hal_efuse_funcs.read_byte=observed_read;
    unsigned range_cases=0;
    uint8_t buffer[258];
    for (unsigned start=0;start<256;start++) {
        for (unsigned length=1;length<=256-start;length++) {
            memset(buffer,0xa5,sizeof(buffer)); calls=locks=restores=0;fail_at=0;
            assert(uapi_efuse_read_buffer(buffer+1,start,length)==0);
            assert(calls==length && locks==1 && restores==1);
            assert(memcmp(buffer+1,expected+start,length)==0);
            assert(buffer[0]==0xa5);
            for (unsigned i=length+1;i<sizeof(buffer);i++) assert(buffer[i]==0xa5);
            range_cases++;
        }
    }
    unsigned failures=0;
    for (unsigned first_failure=1;first_failure<=16;first_failure++) {
        memset(buffer,0xa5,sizeof(buffer));calls=locks=restores=0;fail_at=first_failure;
        assert(uapi_efuse_read_buffer(buffer+1,240,16)==ERRCODE_FAIL);
        assert(calls==first_failure && locks==1 && restores==1);
        assert(memcmp(buffer+1,expected+240,first_failure-1)==0);
        assert(buffer[0]==0xa5);
        for (unsigned i=first_failure;i<sizeof(buffer);i++) assert(buffer[i]==0xa5);
        failures++;
    }
    calls=locks=restores=0;
    assert(uapi_efuse_read_buffer(buffer,0,0)==ERRCODE_FAIL);
    assert(uapi_efuse_read_buffer(NULL,0,1)==ERRCODE_FAIL);
    assert(uapi_efuse_read_buffer(buffer,255,2)==ERRCODE_FAIL);
    assert(uapi_efuse_read_buffer(buffer,256,1)==ERRCODE_FAIL);
    assert(uapi_efuse_read_buffer(buffer,UINT32_MAX,1)==ERRCODE_FAIL);
    assert(uapi_efuse_read_buffer(buffer,0,UINT16_MAX)==ERRCODE_FAIL);
    assert(calls==0 && locks==0 && restores==0);
    hal_efuse_deinit();
    assert(hal_efuse_read_byte(0,&value)==ERRCODE_EFUSE_NOT_INIT);
    assert(g_efuse_regs[0]==&control); /* deinit only changes software flag */
    assert(control.efuse_avdd_ctl_data.d32==0x89abcdef);
    assert(program_switch.efuse_switch_en.d32==0xfedcba98);
    for(unsigned n=0;n<128;n++) assert(data[n]==(0xdead0000u|expected[n*2]|(expected[n*2+1]<<8)));
    assert(munmap(data,page)==0);
    printf("{\"status_byte_cases\":%u,\"valid_buffer_ranges\":%u,\"partial_failures\":%u}\n",
           byte_cases,range_cases,failures);
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    require(platform.system() == "Linux" and platform.machine() == "x86_64", "oracle requires Linux/x86-64 anonymous MAP_32BIT")
    sdk = args.sdk.resolve()
    inputs = {}
    for path in (HAL, REG, OP, API, PORT, PORT_C, DRV, CONFIG):
        inputs[path] = subprocess.check_output(["git", "-C", str(sdk), "show", f"{PIN}:{path}"])
        require(inputs[path] == (sdk/path).read_bytes(), f"local SDK differs: {path}")
    config = inputs[CONFIG].decode()
    for setting in ("CONFIG_EFUSE_USING_V151=y", "CONFIG_EFUSE_SWITCH_EN=y", "CONFIG_EFUSE_REGION_NUM=1"):
        require(setting in config, f"application configuration differs: {setting}")
    extracted = [function(inputs[PORT_C].decode(), name) for name in ("hal_efuse_get_region", "hal_efuse_get_byte_offset")]
    extracted.append(function(inputs[DRV].decode(), "uapi_efuse_read_buffer"))
    with tempfile.TemporaryDirectory(prefix="ws53-efuse-read-contract-") as temp:
        work = Path(temp)
        for path in (HAL, REG, OP, API, PORT): (work/Path(path).name).write_bytes(inputs[path])
        (work/"common_def.h").write_text('#pragma once\n#define unused(x) ((void)(x))\n')
        (work/"errcode.h").write_text('''#pragma once
#include <stdint.h>
typedef int32_t errcode_t;
#define ERRCODE_SUCC 0
#define ERRCODE_FAIL -1
#define ERRCODE_INVALID_PARAM -2
#define ERRCODE_EFUSE_NOT_INIT -3
''')
        (work/"securec.h").write_text('#pragma once\n')
        (work/"tcxo.h").write_text('#pragma once\nvoid uapi_tcxo_delay_us(uint64_t us);\n')
        (work/"check.c").write_text(HEADER+'\n'+'\n'.join(extracted)+'\n'+MAIN)
        subprocess.run([args.cc,"-std=c11","-O2","-Wall","-Wextra","-Werror","-I",str(work),
                        str(work/"check.c"),"-o",str(work/"check")],check=True)
        result=json.loads(subprocess.check_output([str(work/"check")],timeout=10))
    require(result=={"status_byte_cases":8192,"valid_buffer_ranges":32896,"partial_failures":16},"missing cases")
    print(json.dumps({"sdk_commit":PIN,"input_sha256":{p:hashlib.sha256(v).hexdigest() for p,v in inputs.items()},
        **result,"read_window":"anonymous host RAM populated then mprotect(PROT_READ); not a device mapping",
        "observations":["read low/high byte from 32-bit stride4 slots; ignores upper16",
        "init sets low8 timing to 0x1f preserving upper24; setter silently truncates wider values",
        "each read selects low16 mode 0x5a5a preserving upper16; no delay",
        "HAL read ignores all manual/boot status combinations, including boot0_done=0",
        "SDK buffer read holds one IRQ lock across entire byte loop; all exits restore it",
        "failed buffer leaves successful prefix and untouched suffix; API does not report prefix length",
        "invalid/null/zero/out-of-range requests do not issue reads or take IRQ lock",
        "deinit only clears software init flag; does not remove power or clear register pointers",
        "OTP window, AVDD and program switch unchanged; no programming delay called"],
        "limitations":["register pointer initialization and IRQ plumbing are host stubs",
        "host memory does not establish silicon read timing, command completion or ownership",
        "no identity/MAC schema chosen; no programming function invoked"],
        "hardware_accessed":False,"otp_programming_performed":False},indent=2))


if __name__ == "__main__": main()
