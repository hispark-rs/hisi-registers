# WS53 KM 原 C 路径审计

固定 SDK `163d9150ab94a02d09a05f623351f9facee3025e`。脚本
`scripts/check_ws53_km_control.py`逐文件核对 Git 对象，执行34个未修改函数：
8个 keyslot HAL、15个 KLAD HAL、5个 keyslot driver、6个 KLAD driver。
原枚举、配置结构、条件返回宏及 LiteOS 环境的错误编码保留；MMIO、CPU类型、
延时、`memcpy_s/memset_s`由主机记录器替代。所有密钥均为脚本公开生成的测试字节。
UBSan trap、`-Wall -Wextra -Werror`开启，没有访问硬件或读取真实密钥。

| 类别 | 场景数 | 验证范围 |
|---|---:|---|
| 槽申请/释放 | 462 | REE/TEE、两种槽、边界截断、保留位、已有owner、竞争读回、忙、释放失败/超时 |
| common锁 | 1042 | 申请重试、全部CPU枚举、256个owner值和4种解锁失败编码 |
| 配置 | 844 | 算法选择、目的地、6位权限组合、槽地址截断与参数拒绝 |
| 暂存字写入 | 66 | 长度0–32、NULL、4字节倍数、16字节上限和补零 |
| SYMC明文装载 | 94 | 128/192/256位、奇偶段、保留位、逐段故障及超时后清理 |
| HMAC明文装载 | 1016 | 类型0–0x31、长度边界/NULL、4/8个块、逐块错误/超时 |
| driver入口 | 23 | 8/2槽分配、handle校验、FLASH特殊handle、绑定/未绑定和软件销毁 |

共3547个场景。测试计数由脚本固定检查，完整输入/函数哈希见
[`ws53-km-control.json`](ws53-km-control.json)。这是源码执行证据，**不是硅片行为或密码算法结果**。

## 对驱动设计有影响的实际源码行为

- ACPU使用REE命令，状态值1；SCPU使用TEE命令，状态值2。未使用头文件中的
  `TEECPU_LOCK_KEYSLOT=0x10`与三位状态字段/实际调用路径矛盾，应依据原枚举和调用路径。
- HAL槽号会截断为10位；driver分配仅8个MCIPHER槽、2个HMAC槽，不能把位宽当容量。
  申请会核对锁定后的owner；释放先看busy与是否锁定，但不核对原owner是否是自己，
  完成后也不重新读owner。主机桩故意保留owner，源码仍可能返回成功。
- `flush_timeout_err`位2没有参与原释放的成功判定；只有busy位0和unlock_fail位1被检查。
  Rust需要检查已知错误、保留失败状态的所有权，不能因SDK返回成功就断言密钥已擦除。
- common锁只允许ACPU，期望owner=`0xaa`。两位失败字段只拒绝编码1，2/3可能通过；
  common unlock对其他owner直接返回成功。它属于common/root路线，不能据函数名推断
  能保护明文装载路线；原`drv_klad_set_clear_key`并未调用此锁。
- SYMC装载16字节使用调用者奇偶位；24/32字节先偶段16字节、再奇段8/16字节，
  192位尾段补零。输入按小端32位字写入；原函数把指针强转`u32*`，调用者对齐未检查，
  本测试显式提供对齐缓冲区，未人为引入未定义行为。
- 等待START清零后，源码将整个`KL_INT_RAW`读值 OR bit14写回，再检查KL/KC完整错误字。
  不足以认定仅清一个事件或具备W1C语义。超时后仍清零4个DATA_IN字，不能证明硬件停止。
  正常清零也只是暂存区写零，不是目标槽擦除证明。
- HMAC HAL只检查类型小于MAX，因此接受未定义的低值/间隙；SHA384/512用128字节，
  其他分支用64字节。非NULL且超过128被复制桩拒绝，但64字节算法传65–128字节会
  只提交前64字节；NULL会按全零提交，包括超过128的长度。应按具体算法提前验证，
  不能照搬宽松分支。此处不等于所有更高层API均缺校验；没有执行未提供的KAPI。
- driver clear-key入口检查open/set_attr，未检查attached；测试复现未绑定仍提交至默认
  selector。driver detach/destroy只清软件状态，没有硬件清槽命令。
- FLASH槽create返回handle0，destroy(0)却解释为MCIPHER槽0并提交unlock。
  本次仅在主机桩复现，未访问板上Flash密钥，也不新增该操作的HAL授权。

## 寄存器与后续实现边界

KM共用`0x52022000`，KLAD暂存区在`+0x1000`，槽命令在`+0x1b00`；所有选择器、
装载配置及槽归属应受同一个KM所有权管理，不能拆成能同时独占的KLAD/KEYSLOT token。
`kl_int_raw`原C结构因保留尾字段合计33位而占8字节；实际源码读写的是其32位`u32`
成员。布局检查在主机与RV32交叉验证低字位域，SVD只生成32位访问。

后续Rust实现需类型化8/2槽及算法/长度，显式共享资源交接、有限等待、失败后保留槽，
并区分“暂存区已清零”“硬件已完成”“槽已释放/擦除”。不开放根密钥派生、OTP、
永久策略或未审计的PCPU/AIDSP控制。HAL、真实AES/SM4/HMAC模型及实板均尚未验收。

复现：`uv run --script scripts/check_ws53_km_control.py --sdk /path/to/fbb_ws53 --output /tmp/km-control.json`。
