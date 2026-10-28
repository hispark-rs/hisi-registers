# WS53 SFC 时钟访问视图：源码合同

2026-10-25。范围为固定 SDK `163d9150ab94a02d09a05f623351f9facee3025e`
的源码与主机匿名 RAM 检查，不是实板时钟、切频或所有权验收。

`clock_recover.c:12,137–147` 与 `flashboot_ws53/startup/main.c:89–97`
均使用 `0x52000570`。它属于 `M_CRG` 基址 `0x52000000`，不是相邻
`XIP_QSPI_CLOCK@0x574`，也不是 `GLB_CTL`。新增 `XIP_SFC_CLOCK` 为16位视图，
`XIP_SFC_CLOCK_WORD` 是同址32位视图，使用已有导出器的 `alternateRegister`
机制。两者共享字段源，不能当作两份可独占资源。

| 字段 | 已证实源码行为 | 未证明的范围 |
| --- | --- | --- |
| ENABLE bit0 | 原 `reg_clrbit/reg_setbit` 先清后置；原 `readl` 用32位读回 | 上游时钟存在、实际门控响应、启动状态、ready/drain |
| SOURCE_CONFIG bits3:1 | 原 `reg16_setbits/reg16_clrbits` 用16位 RMW；`pll=true` 写6、false写0；FlashBoot写6 | 其他编码、内部选择/分频拆分、输出频率、任意时刻切频安全 |

`chip_io.h:162,178,183,187,191,259–273` 定义这些原始访问宏。只描述低4位；
bit4..31 不补成保留零或具名字段。两个视图均无 resetValue/resetMask、读清、
W1C 或合法频率枚举。寄存器 RW 描述保留真实源码访问，不提供自动切频 HAL。

原恢复函数在清使能、更新配置后请求1µs延时，再置使能和读回；FlashBoot函数
分别在清使能、更新配置和置使能后请求1µs延时。主机检查执行原函数体和原
`chip_io.h` 宏，将地址重定向到匿名 MAP_32BIT RAM，并用明确的延时 stub 记录
各观察点。它检查邻位/高半字与相邻 QSPI 哨兵保留，**不把 stub 当作时间证明**。

切频的调用者还涉及跨核协调、PLL、缓存和低功耗：
`acore_clock_recover.c:74–87,140–177,230–236`。本次不描述这些协议为已完成，
不因 enable=1 或代码请求延时就判定控制器安全可用，不改变 Flash/XIP 映射。

复现（不访问设备）：

```sh
bash scripts/generate.sh
uv run --script scripts/check_ws53_sfc_clock.py --sdk /path/to/fbb_ws53 --cc gcc
uv run --script scripts/check_ws53_sfc_clock.py --sdk /path/to/fbb_ws53 --cc clang
uv run --locked python scripts/test_export_svd.py
```

详细输入哈希、主机检查计数、旧芯片逐字节对照及 PAC parser 结果记录在同名 JSON。
本轮 GCC/Clang 各通过288个原函数案例、480个延时 stub 观察点和5项宏访问宽度
静态断言；29项导出测试通过，其中新增19种时钟描述篡改拒绝。三芯片 schema、
完整映射检查及固定 svd2rust 0.37.1 parser 通过，WS53 使用显式 RISC-V 目标。
相对 `a5f81ca` 独立导出，仅新增上述两个WS53视图，WS63/BS2X保持逐字节一致；
WS53二次导出一致。首次离线依赖缺失与复制空工具环境导致的两次准备失败日志
单独保留；最终使用本机已有锁定依赖的独立副本，没有联网补依赖。
不手改 PAC；后续必须从本 RDL 提交再生。本次不修改原 SFC 命令分支、冻结 PAC、
HAL、QEMU 或硬件，不宣称 Flash/冷启动的新验收。
