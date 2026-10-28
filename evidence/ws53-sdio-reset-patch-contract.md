# WS53 SDIO软复位补丁

来源为SDK `163d9150`的应用核`libplat_patch.a`中`sdio_patch.c.obj`。
`acore.cfg`明确将`sdio_port_soft_reset`替换为`sdio_port_soft_reset_patch`。
SDK补丁脚本优先选择目标同名配置，不存在时选择core配置；此快照没有
`ws53_liteos_app.cfg`。这证明SDK提供的替换规则，不证明某个实板镜像已启用该规则。

对40字节补丁入口逐指令及重定位核对：

1. 32位读取`0x570000c8`，仅清bit1，写回。
2. 唯一外部调用为`uapi_tcxo_delay_us(5)`。
3. 重新32位读取同址，仅置bit1，写回，然后返回。

RDL新增`GLB_CTL.SDIO_SOFT_RESET.SDIO_RESET_N`，保留其他位，不设置默认复位值。
脉冲没有状态轮询；复位作用范围、实际时序、其他总线master及在途写入是否排空
均不能从这40字节证明。不得据此安全释放DMA缓冲区或自动复位共享控制器。
ROM原入口为空的历史观察仍成立，但不能代表含此补丁的SDK应用行为。

同一对象还有`pll_clk_efuse_cfg`，与本入口的唯一调用无关；此审计从不执行厂商
代码、不调用eFuse服务、不访问设备。初始化补丁另有等待host clock和function
ready的循环，尚未作为Rust初始化合同接受；必须独立实现有界等待及取消。

```sh
uv run --script scripts/check_ws53_sdio_patch.py \
  --sdk /path/to/fbb_ws53 \
  --tool-prefix /path/to/vendor/bin/riscv32-linux-musl- \
  --svd generated/WS53.experimental.svd --output /tmp/new-sdio-patch-report.json
uv run --script scripts/test_ws53_sdio_patch.py
```

检查固定完整archive/member摘要、ELF架构/函数范围、全部指令和唯一调用重定位，
再核对SVD地址、宽度、位及访问属性。19项不安全描述变更被拒绝。厂商二进制不再分发。
