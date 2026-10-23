#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Execute pinned non-PRO UART baud/frame helpers on host RAM; no hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from check_ws53_uart_dma_contract import function, typedef, UART
from check_ws53_mdma_contract import PIN, CONFIG, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--vectors', type=Path, help='Write original-C baud vectors for HAL host tests')
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    paths = [UART+'hal_uart_v151.c', UART+'hal_uart_v151_regs_def.h',
             UART+'hal_uart_v151_regs_op.h', 'src/drivers/drivers/hal/uart/hal_uart.h',
             'src/drivers/chips/ws53/porting/uart/uart_porting.h', CONFIG]
    sources = {}
    for path in paths:
        data = (sdk/path).read_bytes()
        require(data == subprocess.check_output(['git','-C',str(sdk),'show',f'{PIN}:{path}']), path+' changed')
        sources[path] = data.decode()
    source = sources[paths[0]]
    ops = sources[paths[2]]
    types = sources[paths[3]]
    require('# CONFIG_UART_IP_VERSION_V151_PRO is not set' in sources[CONFIG], 'unexpected PRO configuration')
    dlf = re.search(r'^#define UART_DLF_SIZE\s+(\d+)', sources[paths[4]], re.M)
    require(dlf is not None and int(dlf[1]) == 6, 'fraction width changed')
    defines = '\n'.join(line for line in source.splitlines() if line.startswith((
        '#define round_off(', '#define hal_uart_get_high_8bit(', '#define hal_uart_get_low_8bit(',
        '#define IBRD_NEED_BAUD_OFFSET_NUM ', '#define REMAINDER_NEED_BAUD_OFFSET_NUM ',
        '#define IBRD_EIGHT_NEED_BAUD_OFFSET_NUM ', '#define REMAINDER_EIGHT_NEED_BAUD_OFFSET_NUM ')))
    helpers = ['hal_uart_set_diven','hal_uart_set_baud_div','hal_uart_set_baud_rate',
               'hal_uart_check_en','hal_uart_parity_en','hal_uart_set_data_bits',
               'hal_uart_set_stop_bits','hal_uart_set_parity']
    code = ['#include <stdint.h>', '#include <stdbool.h>', '#include <stdio.h>', '#include <assert.h>',
            '#include <stdlib.h>', '#include <string.h>', '#define UART_DLF_SIZE 6', defines,
            '#include '+json.dumps(str(sdk/paths[1])),
            typedef(ops,'hal_uart_sample_baud_rate_t'), typedef(ops,'hal_rx_sample_phase_t'),
            typedef(types,'hal_uart_data_bit_t'), typedef(types,'hal_uart_stop_bit_t'),
            typedef(types,'hal_uart_parity_t'),
            'static uart_reg_t banks[3]; uart_reg_t *g_hal_uart_reg[3]={&banks[0],&banks[1],&banks[2]};']
    code += [function(source,name) for name in helpers]
    code += [r'''
#define bank banks[port]
int main(int argc, char **argv) {
    if (argc != 4) return 2;
    unsigned port=strtoul(argv[1],0,0), clock=strtoul(argv[2],0,0), baud=strtoul(argv[3],0,0);
    if (port > 2) return 2;
    memset(banks,0,sizeof(banks)); bank.uart_ctl=0xfc; bank.baud_ctl=0xa500;
    hal_uart_set_baud_rate(port,baud,clock);
    printf("%u %u %u %u %u\n",bank.div_l & 255,bank.div_h & 255,
           bank.div_fra & 63,bank.baud_ctl,bank.uart_ctl);
    for (unsigned bits=0; bits<4; bits++) for (unsigned stop=0; stop<2; stop++)
        for (unsigned parity=0; parity<3; parity++) {
            bank.uart_ctl=0;
            hal_uart_set_data_bits(port,bits); hal_uart_set_stop_bits(port,stop);
            hal_uart_set_parity(port,parity);
            printf("%u %u %u %u\n",bits,stop,parity,bank.uart_ctl);
        }
    const uart_reg_t zero={0};
    for (unsigned i=0; i<3; i++) if (i!=port) assert(memcmp(&banks[i],&zero,sizeof(zero))==0);
}
''']
    cases=[]
    with tempfile.TemporaryDirectory(prefix='ws53-uart-config-') as tmp:
        work=Path(tmp)
        (work/'uart_porting.h').write_text('#include <stdint.h>\n#define UART_BUS_MAX_NUMBER 3\ntypedef uint32_t uart_bus_t;\n')
        binary=work/'config'
        # Original helpers use partially initialized register unions. Zero-init
        # compiler policy makes reserved bits deterministic; only modeled bits
        # are compared and no silicon reset/reserved-bit fact is inferred.
        subprocess.run([args.cc,'-std=c11','-Wall','-Werror','-ftrivial-auto-var-init=zero',
                        '-I',str(work),'-x','c','-','-o',str(binary)],
                       input='\n'.join(code),text=True,check=True)
        for clock in (24_000_000,32_000_000,40_000_000):
            for baud in (31,300,9600,115200,921600,clock//16,clock//16+1,clock//8,clock//8+1):
                sample=16 if baud*16<=clock else 8
                integer=clock//(baud*sample)
                fraction=(clock%(baud*sample))*64//(baud*sample)
                for port in range(3):
                    lines=subprocess.check_output([str(binary),str(port),str(clock),str(baud)],text=True).splitlines()
                    words=list(map(int,lines[0].split()))
                    expected=[integer&255,(integer>>8)&255,fraction,0xa500|(0x7f if sample==16 else 0x37),0xfc]
                    require(words==expected, f'original baud helper mismatch {clock}/{baud}/{port}: {words}')
                    for row in lines[1:]:
                        bits,stop,parity,value=map(int,row.split())
                        require(value==(bits<<2)|(stop<<7)|(0x20 if parity else 0)|(0x10 if parity==2 else 0), 'frame encoding mismatch')
                    require(len(lines)==25,'missing frame cases')
                    cases.append(dict(port=port,clock_hz=clock,baud=baud,sampling=sample,
                        integer=integer,fraction=fraction,register_words=words,
                        valid_divider=1<=integer<=65535))
        # Extreme input wraps baud<<4 to zero in the unguarded SDK. It is
        # deliberately NOT executed; a HAL constructor must reject it first.
    if args.vectors:
        args.vectors.write_text('# Original SDK 163d9150 outputs; check_ws53_uart_config_contract.py\n'
            '# port clock baud div_l div_h div_fra baud_ctl uart_ctl valid\n'+''.join(
                ' '.join(map(str,[c['port'],c['clock_hz'],c['baud'],*c['register_words'],int(c['valid_divider'])]))+'\n'
                for c in cases))
    print(json.dumps(dict(sdk_commit=PIN,source_sha256={p:hashlib.sha256(sources[p].encode()).hexdigest() for p in paths},
        hardware_verified=False,baud_cases=cases,frame_case_count=len(cases)*24,
        conclusions=['SDK fractional division truncates: round_off uses integer 1/2 == 0',
                     'Use widened arithmetic and reject integer dividers outside 1..65535',
                     'Reject baud above clock/8 rather than programming integer divisor zero',
                     'STP=1 means 1.5 stop bits for five data bits, two otherwise',
                     'Clock gating, pad ownership, idle handoff and reset remain separate requirements'],
        not_proven=['physical baud accuracy','safe clock/reset provisioning','reserved register bits']),indent=2))


if __name__ == '__main__':
    main()
