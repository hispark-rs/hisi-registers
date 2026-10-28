# WS53 AGPIO / GPIO1 时钟入口

固定来源：WS53 SDK `163d9150ab94a02d09a05f623351f9facee3025e`。

- `src/application/ws53/ws53_application/main.c:dev_work_clock_init` 在
  `BOARD_ASIC` 和 FPGA 两种分支之后，无条件对 `0x57030024.bit0` 执行
  `reg16_setbit`，注释为 AGPIO 时钟初始化。
- `src/drivers/chips/ws53/include/acore/platform_core.h` 定义 bit0 为
  `HAL_GPIO_ULP_AON_PCLK_INT_EN_BIT`、bit1 为时钟选择。
- `src/drivers/chips/ws53/include/clock_recover.h` 定义
  `ULP_GPIO_CLK_CFG=0x57030024`。
- `clock_recover.c:pm_gpio_cfg_suspend/pm_peripheral_resume` 写32位3/1，
  注释分别为 GPIO 切32K/PLL；`pm_porting.c:pm_port_save_udsleep_wakeup_src`
  写1后才读取 GPIO1 唤醒状态。因此寄存器并非应用核独占，不能自动关闭共享时钟。

导出半字 `GPIO_CLOCK` 和同址整字 `GPIO_CLOCK_WORD` 两个视图，描述已确认的
enable / slow_clock_select 两位，不假定复位值或其他位。驱动开门时应保留原有
源选择与保留位；本描述不证明 PLL 频率、全部APB/中断路径或低功耗时序。

SPI23首轮Flash实板在 GPIO1 访问阶段进入默认异常，AP0读取GPIO1也失败；同一
测试逻辑此前在原厂初始化后的RAM环境工作。该差异促成本次源码审计，但尚没有
对bit0的受控0/1实测，不能单凭源码加失败关联就声称物理根因已闭合。FlashBoot
环境与完整厂商应用初始化环境必须分别验收，QEMU也不能无条件提供可访问GPIO1。

检查脚本：`uv run scripts/check_ws53_agpio_clock_contract.py --sdk <SDK>
--svd generated/WS53.experimental.svd --output <报告.json>`。它固定并逐文件核对
SDK来源；`scripts/test_export_svd.py` 检查同址双宽度、字段、访问权限及未知复位。
