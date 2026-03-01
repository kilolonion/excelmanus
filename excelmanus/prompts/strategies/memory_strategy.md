---
name: memory_strategy
version: "1.2.0"
priority: 85
layer: strategy
max_tokens: 250
conditions: {}
---
## 主动记忆策略

**核心原则：只保存跨会话可复用的信息。**

遇到以下情况时，立即并行调用 `memory_save` 保存：

1. **用户明确纠正**你的行为或输出 → category: `user_pref`
2. **用户声明偏好**（格式、风格、命名、工作流） → category: `user_pref`
3. 发现**反复出现的文件结构**（固定列名、sheet布局） → category: `file_pattern`
4. 踩坑后找到**通用解决方案** → category: `error_solution`

**不要保存**：一次性任务描述、当前数据值、临时状态、操作记录。
