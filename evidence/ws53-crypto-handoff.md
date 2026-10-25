# WS53 安全模块：时钟、内存与启动环境交接

固定SDK `163d9150ab94a02d09a05f623351f9facee3025e`，以应用核
`ws53_liteos_app` / `MCU_MODE_1` 为基准。所有输入逐份与提交内容比对；
`check_ws53_crypto_handoff.py` 编译并执行25个原始函数，外部分配、缓存指令、
原子计数、PM和MMIO均由主机记录器或匿名内存代替。没有访问板卡、密钥或eFuse。

## 构建选择与内存事实

脚本解析原始产品/继承配置的AST，使用原始CMake选择组件，记录应用、ROMBoot、
SSB、LoaderBoot、FlashBoot五种配置各自的HAL/driver输入列表。应用分支使用
`crypto_osal/cfbb/os`与`init/cfbb/os`，包含HASH/SYMC；Boot分支使用独立实现。
这是源码配置选择，不是最终厂商镜像链接完成或没有弱符号覆盖的证明。

| 事实 | 已核对行为 | 对Rust适配的影响 |
|---|---|---|
| `inner_malloc_coherent` | 忽略MMZ/SMMU参数，经`osal_kmalloc_align`调用`LOS_MemAllocAlign(m_aucSysMem0, size, 32)` | 仅为普通堆对齐申请；不自动得到非缓存映射或DMA可达性 |
| 物理地址转换 | 应用回调及缺省回退均返回同一数值地址；默认CPU为ACPU | 地址数值相同不能证明SPACC总线可访问该范围 |
| OS缓存路径 | flush→`ArchDCacheCleanByVa`，inv→`ArchDCacheInvByVa`，all→`ArchDCacheFlush` | 不能删去缓存协议，也不能从函数名认定硬件一致性 |
| Boot缓存路径 | 默认三个weak函数为空 | 最终链接可能覆盖弱符号；不能把此默认实现视作Rust启动后已完成缓存交接 |
| PM | 当前应用启用安全模块deep-sleep veto，真实枚举ID为9 | 操作期间须独立阻止睡眠/时钟变化；本次只验证顺序调用，未证明跨核原子性 |
| SM配置 | 当前应用未启用`CONFIG_SM_EFUSE_CHECK` | 不能由此推断实际芯片的SM能力；本次没有读取或更改eFuse |

OS环境64项分配/回调路径、6项恒等地址、48项缓存参数转发、11项顺序PM计数检查
通过；Boot仅执行48项默认弱缓存函数检查，没有用OS分配器冒充Boot分配器。
分配测试替换真实堆操作、关闭caller-RA调试分支；缓存测试不执行真实自定义指令。
PM记录原始饱和计数/零计数重复post行为，不把它复制成Rust资源所有权协议。

## 自动门控与未闭合条件

原`system_crg_cfg`使用32位写入配置12处共享自动门控，其中：

- `0x52001114`：安全子系统内SPACC/PKE相关auto-gating。
- `0x5200111C`：安全子系统与公共子系统桥相关auto-gating。

原函数向这两处写零；注释含义是启用auto-gating，不是关闭外设时钟。四种主机初值、
48次完整字写入及每轮52个邻接守卫检查通过，没有推断逐位定义、复位值、读取副作用、
延迟或实际门控效果。`SPACC_CALC_CRG_CFG (+0x8030)`在固定SDK中只有宏定义，
没有调用依据；不能猜测其用途并据此复位或解锁硬件。

因此本轮不增加不明字段或自动时钟切换，不放宽HAL的`from_coherent_static`及
共享SPACC交接条件。后续实板HASH仍需证明实际内存模式、SPACC可达性、缓存/别名、
全块用户交接、时钟保持以及测试退出后的在途请求处置。控制核暂停、环指针相等、
完成标志、CPU停止或写零配置都不能单独证明这些条件。

UBSan trap开启；重复报告应逐字节一致。原IRQ/分配器来源检查另行回归；本次未改
RDL/PAC或固件，没有新增RAM/Flash/冷启动资格，也不代表完整SYMC/PKE/KM已支持。

```sh
uv run --script scripts/check_ws53_crypto_handoff.py \
  --sdk /path/to/fbb_ws53 --output /tmp/ws53-crypto-handoff.json
```

测试使用Linux `MAP_32BIT`匿名内存、C11编译器及CMake；普通Cargo构建不依赖这些工具。
