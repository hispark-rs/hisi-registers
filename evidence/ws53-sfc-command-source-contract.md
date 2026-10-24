# WS53 SFC 命令与 DMA 忙位：源码合同

2026-10-24。证据等级为 **SDK-source / host-C**；未接设备、未运行固件或 QEMU。
SDK 固定 `163d9150ab94a02d09a05f623351f9facee3025e`，输入全文哈希、
GCC/Clang 结果和生成差异见同目录 `ws53-sfc-command-source-contract.json`。

## 描述范围及 API 差异

SFC 基址为 `0x53000000`。原 SDK `hal_sfc_v150_regs_def.h:188–349`
定义以下命令/DMA布局，原 `regs_op.c:21–65` 和 `regs_op.h:92–194` 使用它们。
本次用 **原始头文件及原始辅助函数** 编译主机验证器，不以复刻结构代替 SDK。

| 偏移 | 字段与来源行为 |
| --- | --- |
| `0x300 CMD_CONFIG` | START0、CS1、地址使能3、dummy6:4、数据使能7、读写8、长度减一14:9、接口19:17 |
| `0x308 CMD_INS` | 指令7:0；SDK也定义DDR8、DDR dummy15:12，标准命令辅助函数保留这些高位 |
| `0x30c CMD_ADDR` | 地址29:0；原辅助函数读改写并保留31:30，不校验范围 |
| `0x400..0x43c` | 16个32位数据字，共64字节；普通volatile读写，读传输可由硬件更新 |
| `0x240 BUS_DMA_CTRL` | START0、读写1、CS4；其START与命令START是不同状态 |
| `0x244 / 0x248` | RAM/Flash地址完整32位写入；不证明合法RAM范围、别名或所有权 |
| `0x24c BUS_DMA_LEN` | 长度减一29:0，辅助函数保留31:30 |
| `0x250 BUS_DMA_AHB_CTRL` | INCR4/8/16位0/1/2；SDK辅助函数整字写7 |

`GLOBAL_CONFIG.SAMPLE_TEST[9:6]`、`BUS_CONFIG2` 的DDR字段及 `CMD_INS`
的DDR字段共七项仅补齐原头文件布局；**不表示DDR行为已经验证或必须被模型接受**。
原有映射/FAPC只读视图保持不变，没有新增锁、密钥或保护配置写接口。

两个START和16个DATABUF仅在WS53实例改为 `hw=rw`：硬件既消费软件值，也会更新
状态/读取结果。START注释与SDK一致：硬件完成后返回0。没有添加read-clear、W1C、
singlepulse、同步完成时限或强制清零/中止语义。CMSIS-SVD没有独立hw访问属性，
因此动态访问事实由RDL与说明保留，测试直接验证SystemRDL节点。
WS53所有SFC resetValue/resetMask仍省略。

以下五个历史导入槽在固定SDK SFC头、操作函数中没有定义，故**仅从新WS53输出排除**：
`INT_RAW_STATUS@0x120`、`INT_STATUS@0x124`、`INT_MASK@0x128`、
`INT_CLEAR@0x12c`、`SOFT_RST_MASK@0x130`。没有依名称编造W1C或中断路由。
`control_core/chip_core_irq.h:11,25` 的SFC IRQ为26+10=36；应用核IRQ头没有SFC项。
控制核标识不能作为应用核IRQ36的证据。模型遇这些无源槽应拒绝，而非返回零假成功。

调用者只读核对发现主工程通用 `sfc.rs` 仍使用这些历史槽，但只在 `chip-ws63`
编译；WS53 RAM命令诊断和Flash映射前置检查不使用它们。本次**不修改已冻结PAC、
固件或主HAL**；后续PAC再生时需审核上述WS53 API移除。WS63/BS2X输出保持逐字节相同。

## 可轮询不等于排空

SDK `regs_op.c:47–65` 分别反复读取命令START和DMA START，直到零，没有超时、
确认读清或硬件ack写。主机测试用fork共享页，让父进程作为明确的外部mock actor清位；
子进程执行原SDK等待函数。保持1时不返回，清零后返回，其他寄存器和保留位不变。
这证明 **源码的轮询访问方式**，不模拟或验证真实硅的自清时间、读副作用或总线完成顺序。

`hal_sfc_v150.c:48–72` 的WIP查询每次重新构造0x05命令、发起读、等待命令START清零，
之后才读DATABUF。测试提取该函数的原始字节并记录哈希；外部回复/延时是显式stub。
旧DATABUF值不是新的WIP观察；命令START=0也不等于NOR编程完成。预算2时源码允许3次
busy样本，内部命令等待没有总时限。不能由成功或START=0推出posted-write drain、
缓存一致性、停止继承DMA、独占所有权或安全释放缓冲区。

配置 `ws53_liteos_app.config:436,438` 禁用可选SFC DMA API并启用ALREADY_INIT。
本次验证DMA布局/底层辅助函数是为了明确状态合同，不把该配置下的SDK调用覆盖
或安全交接视为已有证明。具体NOR型号、命令含义、容量、NV状态和复位值另行验收。

## 验证方法和边界

```sh
uv run --script scripts/check_ws53_sfc_contract.py --sdk /path/to/fbb_ws53 --cc gcc
uv run --script scripts/check_ws53_sfc_contract.py --sdk /path/to/fbb_ws53 --cc clang
uv run --locked python scripts/test_export_svd.py
```

每个编译器验证28个槽位/32位访问、41个C位域、12757个辅助函数断言、10个忙位等待
断言和32条原始WIP查询路径。覆盖命令长度0..65、所有接口/dummy编码、地址截断及
保留位、DMA长度边界、全部16个数据字。编码可装入字段不等于模式硬件合法：
例如size=0和65分别截断为63和0，原辅助函数没有拒绝，驱动必须自行校验。

导出回归另覆盖13个破坏性SVD变体（错误地址/宽度/访问、伪读清/W1C/reset/IRQ、
恢复无源槽等）以及三个芯片的18个硬件动态属性。全部28项导出测试、三个芯片的
schema/完整映射检查、既有30槽Flash只读布局检查通过。旧芯片字节对照以干净
`c56a24dc54be1698caa7dbcebda1c38f1a7e5fb6`重新生成作为基线。
