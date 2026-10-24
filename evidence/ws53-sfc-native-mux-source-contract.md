# WS53 原生 SFC 复用槽：写入来源与 getter 边界

2026-10-25，SDK 固定为 `163d9150ab94a02d09a05f623351f9facee3025e`，
寄存器库基线为 `92a3d202e70e305426c18af07158731e7e95e921`。
这是源码合同和主机 RAM 测试；没有访问设备，也没有取得新的实板交接资格。

本次不新增 RDL 寄存器。现有 `PINMUX_MCU` 没有覆盖
`0x52008060/64/68/6c/70/74`，证据尚不足以建立这些地址的读取视图。
三个芯片的 SVD 与独立导出的基线逐字节相同；未修改 PAC、HAL、QEMU 或冻结镜像。

| 固定 SDK 来源 | 可以确认的事实 | 不能据此推出 |
| --- | --- | --- |
| `src/bootloader/flashboot_ws53/startup/main.c:49–58,108` | `sfc_pin_mode_set` 按地址递增向上述六槽各发出一次整32位写1，启动初始化调用它 | 读取是否合法、有无读副作用、字段宽度/枚举、复位值、板级信号位置、并发下改复用安全 |
| `src/middleware/utils/common_headers/chip_io.h:151–157` | 非 `UT_TEST` 的原 `writel` 宏是 volatile uint32_t 写；测试明确采用这一宏体 | 实物总线一定完成、引脚已按预期复用、总线排空 |
| `src/drivers/drivers/hal/pinmux/ws53/hal_pinctrl_ws53.c:125–198,683–762` | 实际模式表含原始编号0–22、32–43、45–47；`hal_pin_get_config_val` 对24–29在进入原语前返回 `ERRCODE_FAIL`，`hal_pin_ws53_get_mode` 返回 `PIN_MODE_MAX` | 不能用文件顶部“MGPIO0..29”概述或相邻寄存器3位模式字段填出六个缺失 getter |
| `src/include/common_def.h:96,105` | getter 实际包含的头文件定义32位 volatile 读取及移位/掩码；主机保留这些宏体，仅重定向地址 | 并不提供对被模式表排除地址的访问证据 |
| `src/drivers/chips/ws53/porting/pinctrl/pinctrl_porting.c:212–252,392–395` | 注释把编号24–29的模式1分别称为 SFC data0..3、clock、CS，但实际可用模式掩码六项均为0；原 `pin_check_mode_is_valid` 对每项模式0–7全部拒绝 | 注释不是开放的模式枚举，也不能据此新建可用普通GPIO token |
| `src/drivers/chips/ws53/include/acore/platform_core.h:378–383` 与 `control_core/platform_core.h:325–330` | 两份 `pin_t` 定义均仅以注释记载24–29 “not pin out”，没有相应枚举成员 | SDK措辞不是本开发板BOM/封装/走线验证；不能据相似数字把板端MIO或AON编号当成这些原生槽 |

这里的“原始编号”是专门强制转换为 SDK `pin_t` 以检查查表边界的主机输入。
它们不是新增的合法 Rust 引脚标识。模式表会接收一些被公共模式掩码拒绝的数字；
例如它的0–22连续范围不能作为可用板端GPIO清单。`PINMUX_AON` 在
`0x57036000`，与 `PINMUX_MCU@0x52008000` 同偏移也不是同一寄存器。
源码里若干未展开的 QSPI 宏提及 `S_MGPIO25` 等名字，不会补出缺失枚举或模式表项。

## 可复现的主机合同

[`../scripts/check_ws53_sfc_native_mux.py`](../scripts/check_ws53_sfc_native_mux.py)
先将9个 SDK 输入逐字节与固定 Git 对象核对，再提取未经改写的原函数体、
完整 HAL 配置映射/表、`pin_t`/`pin_mode_t` 和完整 port 模式掩码。
volatile 原语被包装以记录逻辑地址，并用原宏体在主机对象上执行实际读写。
仅测试原语地址被重定向；没有打开 `/dev`、J-Link、OpenOCD 或串口。
临时 C 包含厂商片段，执行后删除，不作为公共源码交付。

GCC 16.2.1 和 Clang 22.1.8 均运行两种配置：IE/ST 都关闭，以及都开启。
四份访问记录逐字节相同；每份包含：

- 两组不同旧值下的12次整32位写1，六槽之外的前后哨兵保持原值；不使用RMW。
- 38个实际模式表原始编号，各以三种数据分别调用底层 getter 与 mode getter，
  共228次32位读取；独立期望地址检查 MCU/AON/SGPIO映射和返回的低三位。
- 10个不在模式表内的原始编号（含24–29）全部返回错误，输出参数不变，
  mode getter 返回8，访问计数为0。
- 六个原生编号的48种模式全部被真实 port 校验函数拒绝，另以MGPIO0作正控制。

[`../scripts/test_ws53_sfc_native_mux.py`](../scripts/test_ws53_sfc_native_mux.py)
验证实际编译输出与解析记录一致，并拒绝39种篡改：错误位宽/值/地址/次序、
缺失或重复访问、伪造 native getter 成功/读取、修改可用模式、SVD新增或重叠覆盖等。
布局检查仅支持显式 flat register：遇到 cluster（包括内嵌0x60槽）、`derivedFrom`、
缺失/重复/无效宽度或偏移、无效数组数量/步长及未知容器直接拒绝，不自行解析继承。
允许的主机检查几何范围为8/16/32/64位、最多65536项数组；这是检查器支持范围，
不是对WS53新增访问能力的描述。19项新增布局反例证明这些拒绝路径。
该脚本只是 oracle 的主机测试，不将记录校验称为再次执行硬件。

```sh
bash scripts/generate.sh
uv run --offline --script scripts/check_ws53_sfc_native_mux.py --sdk /path/to/fbb_ws53 --cc gcc > gcc.json
uv run --offline --script scripts/check_ws53_sfc_native_mux.py --sdk /path/to/fbb_ws53 --cc clang > clang.json
uv run --offline --script scripts/test_ws53_sfc_native_mux.py --report gcc.json --report clang.json --svd generated/WS53.experimental.svd
uv run --locked python scripts/test_export_svd.py
uv run --locked python scripts/check_schema.py
```

全部161个已有跟踪文件保持与基线 Git 对象相同。三个芯片的基线独立导出与当前
生成结果相同，29项既有导出测试、三芯片 CMSIS-SVD schema 验证均通过。
固定 svd2rust 0.37.1 的 WS53 RISC-V parser smoke 通过；这是临时解析检查，
未再生正式 PAC。首次 parser 运行因输出目录尚不存在而 exit101，其日志保留；
创建目录后原参数成功，不把第一次失败算作通过。
早期 host 原语取自内容等价的 `td_base.h`，最终改用实际包含的 `common_def.h`
并重跑 GCC/Clang，旧结果另存，不混入最终 source 绑定。
首版布局检查只覆盖当前flat输出；审阅后补上述未支持布局拒绝，再以最终脚本
重跑四份原C合同和39个oracle反例。首版源与证据保留在测试目录
`pre-layout-guard/`。29项既有导出/schema/parser结果属于此前不变RDL和相同SVD，
本次未重复运行这些无关检查；重新核对其输入与三芯片产物哈希保持不变。

同名 JSON 保存精确输入、提取片段、脚本、访问记录、工具和日志哈希。
本次仍没有硅上读取副作用、复位值、物理引脚复用、供电/负载、时钟、总线排空
或安全独占的证明；也不以无法读回原生mux槽为由绕过 SFC HAL 的 unsafe 交接条件。
