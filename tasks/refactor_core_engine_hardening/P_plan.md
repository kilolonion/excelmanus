# 执行计划

1. M1：修改 `config.py` 与 `engine.py` 完成模型切换一致性和系统消息模式对齐。
2. M2：修改 `memory.py` + `engine.py`，新增最终请求前 token 预算裁剪链路。
3. M3：新增工具结果全局硬截断配置与应用点，补充事件协议文档说明。
4. M4：补全测试（engine/config/memory/api）并跑回归；更新 `README.md`。
