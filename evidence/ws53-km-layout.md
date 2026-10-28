# WS53 KM 寄存器布局

来源为SDK `163d9150` 的`hal_keyslot_reg.h`、`hal_klad_reg.h`、对应C调用路径及
WS53 port/acore IRQ头文件。`rdl/chips/ws53/km.rdl`新增一个共享KM实例，基址
`0x52022000`，中断SEC_INT3=61；IRQ这里只核对编号，没有投递/屏蔽/清除实板证明。

包含25个展开视图、46个字段、一个4字暂存数组：配置、错误、clear-route控制与应答、
common锁、REE/TEE槽命令及状态。命令使用WO视图，与读回状态通过alternateRegister
关联；不生成未知reset、读副作用或推测W1C。DATA_IN只写，两个错误字只读。
没有RKP、根密钥禁用、OTP、PCPU/AIDSP命令或任意清错误接口。

`scripts/check_ws53_km_layout.py`原头文件编译检查全部25偏移及32种位域掩码，
主机与RV32IMFC/ilp32f对象字节一致。额外3种槽命令位域用于核对源码编码，WO命令
本身保留完整字访问。`kl_int_raw`原类型为8字节的异常已明确处理：检查其低32位并
确认高字为零，没有把结构体大小误当MMIO宽度。结果见
[`ws53-km-layout.json`](ws53-km-layout.json)。

布局验证还拒绝20种篡改：错误基址/IRQ、DATA_IN数组跨度/长度、命令读改写、状态可写、
64位中断访问、错误位掩码、猜测复位/W1C及错误别名。其余34个WS53外设和旧两芯片
输出需保持原样，独立差异检查保存于集成工程证据目录。

这些接口只表达经过核对的源码访问。尚未证明资源交接、可用密钥槽、电源/时钟、
清零/擦除完成或硬件算法行为，不能据此宣布加密驱动已支持。
