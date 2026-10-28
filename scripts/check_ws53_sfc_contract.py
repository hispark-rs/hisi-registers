#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Pinned WS53 SFC C layout/helper contract, using host memory, never hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

PIN = "163d9150ab94a02d09a05f623351f9facee3025e"
HAL = "src/drivers/drivers/hal/sfc/"
PORT = "src/drivers/chips/ws53/porting/sfc/"
FILES = [HAL + name for name in ("hal_sfc_v150_regs_def.h", "hal_sfc_v150_regs_op.h",
                                "hal_sfc_v150_regs_op.c", "hal_sfc_v150.c")]
FILES += [PORT + name for name in ("flash_config_info.h", "sfc_porting.c", "sfc_porting.h")]
FILES += ["src/drivers/chips/ws53/include/acore/chip_core_irq.h",
          "src/drivers/chips/ws53/include/control_core/chip_core_irq.h",
          "src/build/config/target_config/ws53/menuconfig/acore/ws53_liteos_app.config"]
REMOVED = {"INT_RAW_STATUS": 0x120, "INT_STATUS": 0x124, "INT_MASK": 0x128,
           "INT_CLEAR": 0x12c, "SOFT_RST_MASK": 0x130}
# register -> (SDK union, C member -> (SVD bit offset, width))
FIELDS = {
    "GLOBAL_CONFIG": ("global_config_t", {"mode": (0, 1), "wp_en": (1, 1),
        "flash_addr_mode": (2, 1), "rd_delay": (3, 3), "sample_test": (6, 4)}),
    "TIMING": ("timing_t", {"tshsl": (0, 4), "tcss": (8, 3), "tcsh": (12, 3)}),
    "BUS_CONFIG1": ("bus_config1_t", {"rd_mem_if_type": (0, 3), "rd_dummy_bytes": (3, 3),
        "rd_prefetch_cnt": (6, 2), "rd_ins": (8, 8), "wr_mem_if_type": (16, 3),
        "wr_dummy_bytes": (19, 3), "wr_ins": (22, 8), "wr_enable": (30, 1), "rd_enable": (31, 1)}),
    "BUS_CONFIG2": ("bus_config2_t", {"wip_locate": (0, 3), "bus_ddr_mode": (3, 1),
        "ddr_rd_dummy_bytes": (4, 4), "ddr_wr_dummy_bytes": (8, 4), "sfc_ddr_tx_delay_enable": (12, 1)}),
    "BUS_DMA_CTRL": ("bus_dma_ctrl_t", {"dma_start": (0, 1), "dma_rw": (1, 1), "dma_sel_cs": (4, 1)}),
    "BUS_DMA_LEN": ("bus_dma_len_t", {"dma_len": (0, 30)}),
    "BUS_DMA_AHB_CTRL": ("bus_dma_ahb_ctrl_t", {"incr4_en": (0, 1), "incr8_en": (1, 1), "incr16_en": (2, 1)}),
    "CMD_CONFIG": ("cmd_config_t", {"start": (0, 1), "sel_cs": (1, 1), "addr_en": (3, 1),
        "dummy_byte_cnt": (4, 3), "data_en": (7, 1), "rw": (8, 1),
        "data_cnt": (9, 6), "mem_if_type": (17, 3)}),
    "CMD_INS": ("cmd_ins_t", {"reg_ins": (0, 8), "cmd_ddr_mode": (8, 1), "ddr_dummy_byte_cnt": (12, 4)}),
    "CMD_ADDR": ("cmd_addr_t", {"cmd_addr": (0, 30)}),
}
SLOTS = {
    "GLOBAL_CONFIG": (0x100, "global_conf_regs_t", "global_config", 0x100),
    "TIMING": (0x110, "global_conf_regs_t", "timing", 0x100),
    "BUS_CONFIG1": (0x200, "bus_regs_t", "bus_config1", 0x200),
    "BUS_CONFIG2": (0x204, "bus_regs_t", "bus_config2", 0x200),
    "BUS_DMA_CTRL": (0x240, "bus_dma_regs_t", "bus_dma_ctrl", 0x240),
    "BUS_DMA_MEM_SADDR": (0x244, "bus_dma_regs_t", "bus_dma_mem_saddr", 0x240),
    "BUS_DMA_FLASH_SADDR": (0x248, "bus_dma_regs_t", "bus_dma_flash_saddr", 0x240),
    "BUS_DMA_LEN": (0x24c, "bus_dma_regs_t", "bus_dma_len", 0x240),
    "BUS_DMA_AHB_CTRL": (0x250, "bus_dma_regs_t", "bus_dma_ahb_ctrl", 0x240),
    "CMD_CONFIG": (0x300, "cmd_regs_t", "cmd_config", 0x300),
    "CMD_INS": (0x308, "cmd_regs_t", "cmd_ins", 0x300),
    "CMD_ADDR": (0x30c, "cmd_regs_t", "cmd_addr", 0x300),
    **{f"CMD_DATABUF_{i}": (0x400+4*i, "cmd_databufs_t", f"cmd_databuf[{i}]", 0x400)
       for i in range(16)},
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_layout(path):
    root = ET.parse(path)
    block = root.find("./peripherals/peripheral[name='SFC_CFG']")
    require(block is not None and int(block.findtext("baseAddress"), 0) == 0x53000000, "SFC base")
    require(not block.findall("interrupt"), "unsupported application SFC IRQ")
    regs = {r.findtext("name"): r for r in block.findall("registers/register")}
    require(not (REMOVED.keys() & regs.keys()), "unproven legacy IRQ/reset slot")
    for reg in regs.values():
        require(reg.find("resetValue") is None and reg.find("resetMask") is None, "unknown SFC reset")
    for name, (offset, _, _, _) in SLOTS.items():
        reg = regs.get(name)
        require(reg is not None, f"missing {name}")
        require(int(reg.findtext("addressOffset"), 0) == offset and reg.findtext("size") == "32", f"{name} layout")
        require(reg.findtext("access") == "read-write", f"{name} access")
        require(all(f.findtext("access") == "read-write" for f in reg.findall("fields/field")), f"{name} field access")
        require(not reg.findall(".//readAction") and not reg.findall(".//modifiedWriteValues"),
                f"{name} invented read-clear/W1C semantics")
    for name, (_, expected) in FIELDS.items():
        actual = {f.findtext("name"): (int(f.findtext("bitOffset")), int(f.findtext("bitWidth")))
                  for f in regs[name].findall("fields/field")}
        require(actual == {key.upper(): value for key, value in expected.items()}, f"{name} field layout")
    for name, field in (("BUS_DMA_MEM_SADDR", "DMA_MEM_SADDR"), ("BUS_DMA_FLASH_SADDR", "DMA_FLASH_SADDR"),
                        *((f"CMD_DATABUF_{i}", f"CMD_DATABUF_{i}") for i in range(16))):
        f = regs[name].find(f"fields/field[name='{field}']")
        require(f is not None and f.findtext("bitOffset") == "0" and f.findtext("bitWidth") == "32", f"{name} word")
    return {"register_slots": len(SLOTS), "union_fields": sum(len(fields) for _, fields in FIELDS.values()),
            "excluded_legacy_slots": REMOVED, "application_irq": None}


def extract_function(source, name):
    match = re.search(r"(?:errcode_t|void) " + re.escape(name) + r"\([^)]*\)\s*\{", source)
    require(match is not None, f"missing source function {name}")
    start, depth, end = match.start(), 1, match.end()
    while depth:
        require(end < len(source), "unbalanced function")
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r'''#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <time.h>
#include "hal_sfc_v150_regs_op.c"
uintptr_t g_sfc_global_conf_regs, g_sfc_bus_regs, g_sfc_bus_dma_regs, g_sfc_cmd_regs, g_sfc_cmd_databuf;
static uint32_t *memory;
static unsigned cases;
static void clean(void) { memset(memory, 0xa5, 4096); }
#define WORD(off) (*(volatile uint32_t*)((char*)memory+(off)))
#define CHECK(x) do { assert(x); cases++; } while (0)
static void poll_wait(int dma) {
    unsigned own=dma?0x240:0x300, other=dma?0x300:0x240;
    WORD(own)=0xa5a55a5b; WORD(other)=1;
    int pipefd[2]; assert(pipe(pipefd)==0);
    pid_t pid=fork(); assert(pid>=0);
    if(pid==0) {
        close(pipefd[0]); alarm(3); char ready=1;
        assert(write(pipefd[1], &ready, 1)==1);
        if(dma) hal_sfc_dma_wait_done(); else hal_sfc_regs_wait_config();
        _exit(0);
    }
    close(pipefd[1]); char ready;
    assert(read(pipefd[0], &ready, 1)==1); close(pipefd[0]);
    struct timespec wait={.tv_sec=0,.tv_nsec=100000000}; nanosleep(&wait,NULL);
    int status=0;
    CHECK(waitpid(pid,&status,WNOHANG)==0); /* Active bit cannot complete by reading it. */
    CHECK(WORD(own)==0xa5a55a5b && WORD(other)==1);
    WORD(own)&=~1u; /* Only the external host actor clears the shared mock word. */
    assert(waitpid(pid,&status,0)==pid);
    CHECK(WIFEXITED(status) && WEXITSTATUS(status)==0);
    CHECK(WORD(own)==0xa5a55a5a && WORD(other)==1);
    if(dma) hal_sfc_dma_wait_done(); else hal_sfc_regs_wait_config();
    CHECK(WORD(own)==0xa5a55a5a && WORD(other)==1);
}
int main(void) {
    memory=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);
    assert(memory!=MAP_FAILED);
    g_sfc_global_conf_regs=(uintptr_t)memory+0x100;
    g_sfc_bus_regs=(uintptr_t)memory+0x200;
    g_sfc_bus_dma_regs=(uintptr_t)memory+0x240;
    g_sfc_cmd_regs=(uintptr_t)memory+0x300;
    g_sfc_cmd_databuf=(uintptr_t)memory+0x400;
    /* GENERATED_LAYOUT */
    unsigned layout_cases=cases; cases=0;
    for(unsigned length=0; length<=65; length++) for(unsigned type=0;type<8;type++)
    for(unsigned dummy=0;dummy<8;dummy++) {
        clean();
        hal_spi_opreation_t op={.opt={.cmd_support=1,.cmd=(length*17)&255,.iftype=type,.size=0},
                               .data_size=length,.dummy_byte=dummy};
        hal_sfc_regs_set_opt(&op);
        CHECK(WORD(0x300)==((type<<17)|(dummy<<4)|(((length-1)&63)<<9)));
        CHECK(WORD(0x308)==((0xa5a5a5a5u&~255u)|((length*17)&255)));
        CHECK(WORD(0x30c)==0xa5a5a5a5u && WORD(0x400)==0xa5a5a5a5u);
    }
    for(unsigned bits=0;bits<8;bits++) {
        WORD(0x300)=0xa5a5a5a5u;
        hal_sfc_regs_set_opt_attr(bits&1,(bits>>1)&1,(bits>>2)&1);
        CHECK(WORD(0x300)==((0xa5a5a5a5u&~0x188u)|3u|((bits&1)<<8)|(((bits>>1)&1)<<7)|(((bits>>2)&1)<<3)));
    }
    const uint32_t addresses[]={0,0x00ffffff,0x01000000,0x3fffffff,0x40000000,0xffffffff};
    for(unsigned i=0;i<sizeof(addresses)/sizeof(*addresses);i++) {
        WORD(0x30c)=0xa5a5a5a5u; hal_sfc_regs_set_cmd_addr(addresses[i]);
        CHECK(WORD(0x30c)==(0x80000000u|(addresses[i]&0x3fffffffu)));
        hal_sfc_regs_set_bus_dma_flash_saddr(addresses[i]); CHECK(WORD(0x248)==addresses[i]);
        hal_sfc_regs_set_bus_dma_mem_addr((uint8_t*)(uintptr_t)addresses[i]); CHECK(WORD(0x244)==addresses[i]);
    }
    const uint32_t lengths[]={0,1,64,65,0x40000000,0x40000001,0xffffffff};
    for(unsigned i=0;i<sizeof(lengths)/sizeof(*lengths);i++) {
        WORD(0x24c)=0xa5a5a5a5u; hal_sfc_regs_set_bus_dma_len(lengths[i]);
        CHECK(WORD(0x24c)==(0x80000000u|((lengths[i]-1)&0x3fffffffu)));
    }
    for(unsigned dir=0;dir<2;dir++) {
        WORD(0x240)=0xa5a5a5a5u; hal_sfc_regs_set_bus_dma_ctrl(dir);
        CHECK(WORD(0x240)==((0xa5a5a5a5u&~2u)|0x11u|(dir<<1)));
    }
    hal_sfc_regs_set_bus_dma_ahb_ctrl(); CHECK(WORD(0x250)==7);
    hal_sfc_regs_set_timing(); CHECK(WORD(0x110)==1);
    for(unsigned i=0;i<16;i++) {
        uint32_t value=0x13579bdfu^(i*0x01010101u);
        hal_sfc_regs_set_databuf(i,value);
        CHECK(WORD(0x400+4*i)==value);
        CHECK(hal_sfc_regs_get_databuf(i)==value && hal_sfc_regs_get_databuf(i)==value);
        CHECK(WORD(0x400+4*i)==value); /* Mock memory is not a consuming FIFO. */
    }
    unsigned helpers=cases; cases=0;
    poll_wait(0); poll_wait(1);
    printf("{\"layout_field_cases\":%u,\"helper_cases\":%u,\"poll_checks\":%u}\n",layout_cases,helpers,cases);
    assert(munmap(memory,4096)==0);
}
'''

READY_HARNESS = r'''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
#include "flash_config_info.h"
typedef int errcode_t;
#define ERRCODE_SUCC 0
#define ERRCODE_SFC_FLASH_TIMEOUT_WAIT_READY -44
#define SPI_CMD_RDSR 5
#define STANDARD_SPI 0
#define READ_MODE 1
#define ENABLE 1
#define DISABLE 0
typedef struct {spi_opreation_t opt; uint32_t data_size, dummy_byte;} hal_spi_opreation_t;
static unsigned phase, commands, reads, delays, delay_us, budget, index_, length_;
static uint32_t values[4];
static uint32_t sfc_port_get_delay_times(void) {return budget;}
static uint32_t sfc_port_get_delay_once_time(void) {return delay_us;}
static void hal_sfc_regs_set_opt(hal_spi_opreation_t *op) {
    assert(phase==0); phase=1; commands++;
    assert(op->opt.cmd==5 && op->opt.iftype==0 && op->data_size==1 && op->dummy_byte==0);
}
static void hal_sfc_regs_set_opt_attr(unsigned rw,unsigned data,unsigned addr) {
    assert(phase==1 && rw==1 && data==1 && addr==0); phase=2;
}
static void hal_sfc_regs_wait_config(void) {assert(phase==2); phase=3;}
static uint32_t hal_sfc_regs_get_databuf(unsigned index) {
    assert(phase==3 && index==0 && index_<length_); phase=0; reads++; return values[index_++];
}
static void uapi_tcxo_delay_us(unsigned us) {assert(phase==0 && us==delay_us); delays++;}
/* ORIGINAL_WAIT_READY */
int main(void) {
    unsigned cases=0;
    for(unsigned bit=0;bit<8;bit++) for(unsigned busy=0;busy<=3;busy++) {
        phase=commands=reads=delays=index_=0; delay_us=100; budget=2;
        length_=busy==3?3:busy+1;
        for(unsigned i=0;i<length_;i++) values[i]=(i<busy)?(1u<<bit):0;
        /* An old data word is deliberately inaccessible until a fresh command has completed. */
        int rc=hal_sfc_regs_wait_ready(bit);
        assert(rc==(busy==3?-44:0));
        assert(commands==length_ && reads==length_ && delays==busy && phase==0); cases++;
    }
    printf("{\"wait_ready_paths\":%u,\"budget\":2,\"max_busy_samples\":3}\n",cases);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--svd", type=Path, default=Path(__file__).resolve().parents[1]/"generated/WS53.experimental.svd")
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    inputs = {}
    for path in FILES:
        blob = subprocess.check_output(["git", "-C", str(sdk), "show", f"{PIN}:{path}"])
        require(blob == (sdk/path).read_bytes(), f"SDK input differs: {path}")
        inputs[path] = blob
    layout = validate_layout(args.svd)
    acore, ccore = [inputs[path].decode() for path in FILES[-3:-1]]
    require("SFC" not in acore, "new application SFC IRQ requires review")
    require(re.search(r"SFC_INT_IRQN\s*=\s*LOCAL_INTERRUPT0\s*\+\s*10", ccore) and
            re.search(r"#define\s+LOCAL_INTERRUPT0\s+26", ccore), "control-core IRQ source changed")
    config = inputs[FILES[-1]].decode()
    require("CONFIG_SFC_ALREADY_INIT=y" in config and "# CONFIG_SFC_SUPPORT_DMA is not set" in config,
            "application SFC product selection changed")
    regs = inputs[FILES[0]].decode()
    require(regs.count("Automatic return to 0 after") == 2, "START source comment changed")
    ops = inputs[FILES[2]].decode()
    for name, ctype, member in (("hal_sfc_regs_wait_config", "cmd_regs_t", "cmd_config"),
                               ("hal_sfc_dma_wait_done", "bus_dma_regs_t", "bus_dma_ctrl")):
        body = extract_function(ops, name)
        require(f"{ctype} *)g_sfc_" in body and f"->{member} & 0x01" in body, "poll target changed")
        require("while" in body and "wait_ready" not in body and "databuf" not in body, "poll meaning changed")
    selected = extract_function(inputs[FILES[3]].decode(), "hal_sfc_regs_wait_ready")
    layout_c = []
    for name, (offset, ctype, member, base) in SLOTS.items():
        layout_c.append(f'_Static_assert({base}+offsetof({ctype},{member})=={offset},"{name}");')
        layout_c.append(f'_Static_assert(sizeof((({ctype}*)0)->{member})==4,"{name} width");')
    layout_c.append('_Static_assert(MAX_DATABUF_NUM==16 && MAX_BYTE_PER_DATABUF==4,"buffer size");')
    for name, (ctype, fields) in FIELDS.items():
        layout_c.append(f'_Static_assert(sizeof({ctype})==4,"{name} union");')
        for member, (low, width) in fields.items():
            maximum = (1 << width)-1
            layout_c.append(f'{{{ctype} x={{.d32=0}}; x.b.{member}={maximum}u; CHECK(x.d32==0x{maximum<<low:08x}u);}}')
    harness = HARNESS.replace("/* GENERATED_LAYOUT */", "\n".join(layout_c))
    ready_harness = READY_HARNESS.replace("/* ORIGINAL_WAIT_READY */", selected)
    with tempfile.TemporaryDirectory(prefix="ws53-sfc-contract-") as temp:
        work = Path(temp)
        for path in FILES[:3] + [PORT+"flash_config_info.h"]:
            (work/Path(path).name).write_bytes(inputs[path])
        (work/"errcode.h").write_text("#pragma once\ntypedef int errcode_t;\n")
        (work/"sfc_porting.h").write_text('#pragma once\n#include "flash_config_info.h"\n')
        for empty in ("common_def.h", "tcxo.h"):
            (work/empty).write_text("#pragma once\n")
        results = {}
        for name, text in (("helpers", harness), ("wait-ready", ready_harness)):
            path = work/f"{name}.c"
            path.write_text(text)
            subprocess.run([args.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(work),
                            str(path), "-o", str(work/name)], check=True, timeout=60)
            results[name] = json.loads(subprocess.check_output([str(work/name)], text=True, timeout=10))
    print(json.dumps({"sdk_commit": PIN, "input_sha256": {p: hashlib.sha256(b).hexdigest() for p,b in inputs.items()},
        "compiler": subprocess.check_output([args.cc,"--version"],text=True).splitlines()[0],
        "layout": layout, "results": results,
        "original_wait_ready_function_sha256": hashlib.sha256(selected.encode()).hexdigest(),
        "host_harness_sha256": {"helpers": hashlib.sha256(harness.encode()).hexdigest(),
                                "wait_ready": hashlib.sha256(ready_harness.encode()).hexdigest()},
        "observations": ["CMD_CONFIG/DMA_START are volatile pollable busy bits, hardware self-clear per SDK comment, not read-clear",
            "set_opt overwrites command configuration but preserves other CMD_INS bits; set_opt_attr RMW sets CS1/START",
            "command length zero/65 wraps to 63/0; DMA length zero/oversize also truncates, not validated by helpers",
            "30-bit command address RMW preserves upper two bits; DMA address helpers write full words without range validation",
            "16 words/64 bytes of ordinary command data; old data is not fresh WIP; each SDK WIP sample issues 0x05, waits command completion, then reads data",
            "polling waits change no hardware register in original C; shared host actor cleared busy, not the read",
            "control-core local IRQ36 is named; no application-core SFC IRQ or interrupt register layout is established by these SDK inputs",
            "application config inherits SFC init and disables optional SFC DMA API"],
        "limitations": ["host memory does not establish silicon read side effects, self-clear timing, abort, restart while busy, or completion ordering",
            "START zero is not NOR WIP clear, posted-write drain, cache coherence, exclusive ownership, or safe buffer reuse",
            "WIP response values/command completion/delay in wait_ready harness are explicit stubs, not a NOR model",
            "DDR fields are layout-only; no exact NOR identity, status-bit semantics, physical capacity or reset values inferred",
            "control-core IRQ identifier does not prove route to the application core or register IRQ acknowledgement semantics"],
        "hardware_accessed": False, "firmware_or_qemu_executed": False}, indent=2))


if __name__ == "__main__":
    main()
