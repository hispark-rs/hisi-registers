# WS53 SPI 状态读取副作用

来源为 WS53 SDK `163d9150ab94a02d09a05f623351f9facee3025e`，路径前缀
`src/drivers/drivers/hal/spi/v151_100/`。本次只修复应用核 SPI0/QSPI1
已有 STATUS 字段的读取副作用，不改变地址、IRQ、字段名或复位值。

| 实例 | SDK 声明 | 字段 | 行为 |
|---|---|---|---|
| SPI0 | `hal_spi_v151_100_regs_def.h`, `spi_wsr_data_t.dcfe_tfee` | STATUS bit0 | 主机碰撞，读清 |
| QSPI1 | `hal_qspi_v151_100_regs_def.h`, `sr_data_t.txe` | STATUS bit5 | 从机开始传输时 FIFO 为空，读清；不是发送 FIFO 溢出 |
| QSPI1 | 同上，`sr_data_t.dcol` | STATUS bit6 | 主机碰撞，读清 |

QSPI1 SDK 端口只支持主机；不能以主机 FIFO 溢出来制造从机 TXE。
保留现有字段名，避免仅为文档修复改变 PAC 消费者 API。未知复位位仍不填零。
只对上述字段导出 `readAction=clear`，STATUS 整体使用 `modify`，不能把
BUSY/FIFO 实时状态描述为全寄存器读清。原始/屏蔽 IRQ 不继承这些副作用。

SDK getter 各执行一次 volatile 状态字读取，并不额外读取 IRQ 清除寄存器。
这不能证明硬件内部状态和 IRQ 锁存的耦合；二者关系尚未实测。
QEMU 独立保持 IRQ 至显式确认，是保守模型策略，不是新发现的硅片事实。
正常 SPI0 RAM 波形验收时错误位为零，不构成碰撞或从机错误路径的实板验收。

复现：先执行 `uv run --locked scripts/export_svd.py rdl/chips/ws53.rdl
generated/WS53.experimental.svd`，再执行
`uv run --locked scripts/check_ws53_spi_status_contract.py --sdk <WS53 SDK>
--output <报告.json>` 以及 `uv run --locked scripts/test_export_svd.py`。
检查脚本固定 SDK 提交并逐文件校验来源，不接受仅凭字段名猜测语义。
