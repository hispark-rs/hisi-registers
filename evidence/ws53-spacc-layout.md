# WS53 SPACC/HASH 寄存器来源

`rdl/chips/ws53/spacc.rdl` 是共享 SPACC 的 HASH 子集，基址 `0x52010000`。
固定 SDK `163d9150ab94a02d09a05f623351f9facee3025e` 的来源包括：

- `security_unified_porting.h`：基址、两个上下文、SPACC IRQ59。
- 应用核 `chip_core_irq.h`：`SEC_INT1_IRQN=59`；不是 PLIC 偏移。
- `hal_spacc_reg.h`：寄存器偏移及位域。
- `hal_hash.c`：配置/状态访问方向、clear/完成应答、描述符提交和状态索引窗口。
- `hal_symc.c::inner_spacc_interrupt_enable`：共享 REE/TEE 门控的读改写。

`check_ws53_spacc_layout.py` 编译原头文件，逐项对照生成 SVD 的 34 个展开视图、
65 个展开字段、三个数组和中断。所有输入 SHA、编译探针结果见同名 JSON。
主机源码探针与寄存器描述分开，不在生成时依赖本机 SDK。

| 视图 | 来源布局 | 访问约束 |
|---|---|---|
| 共享中断门控 | +0x4，REE0/TEE4/HPP8 | HPP 只有头文件字段证据，不承诺 WS53 路由 |
| HASH clear 状态/应答 | +0x14，通道1/2位 | RO 状态与 WO 命令别名；无推断 W1C |
| HASH 锁 | +0x40，每通道4位 | 请求后须回查；保留其他通道及未知位 |
| HASH clear 请求 | +0x68 | WO 命令；超时必须传播，不能继续发布描述符 |
| HASH_CHANNEL[0/1] | +0x5100/+0x5200，跨度0x100 | 分别映射 SDK 通道1/2；索引不是原始通道号 |
| 提交指针 | 通道+0x30 | RO 指针与 WO 全字提交；SDK 保留高位、更新低8位后提交 |
| HASH_ERROR[0/1] | +0x8068/+0x8078，跨度0x10 | 分别为 TEE/REE，只读；未加入未审计的错误清除命令 |
| HASH 中断状态/应答/门控 | +0x8600/+0x8604 | 状态/应答分离，门控修改须保留未建模通道 |
| HASH_STATE[0/1] | +0xa080/+0xa100，跨度0x80 | DATA 可读写，INDEX 只写；未声明自动递增 |

源清除/退出缺陷及目标 ABI 见 [HASH 控制审计](ws53-hash-control.md)。任何完成位、
指针相等或数据长度为零都不自动证明 DMA 排空或安全回收。两个软件上下文不是
实测硬件数量；单例 PAC 也不授予跨核排他所有权。未知复位值和读写副作用不填默认值。

SYMC 寄存器、PKE/KM、PCPU/AIDSP 错误窗口及未使用的 `SPACC_CALC_CRG_CFG` 宏留待
相应路径审计。PKE IRQ58 与 MDMA 冲突且在应用配置中关闭，未生成 PKE 中断。

验证：三芯片 CMSIS-SVD schema、30 项导出测试（包含15项 SPACC 错误描述拒绝场景）、
三芯片 svd2rust 解析通过。WS63/BS2X 导出 SHA 未变；SPACC 探针两次报告一致。
没有访问板卡、密钥、安全配置或 Flash，没有验证算法、真实 IRQ、时钟或缓存。

```sh
./scripts/check.sh
uv run --script scripts/check_ws53_spacc_layout.py \
  --sdk /path/to/fbb_ws53 --output /tmp/ws53-spacc-layout-new.json
```
