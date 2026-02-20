---
name: task_management
version: "3.0.0"
priority: 50
layer: core
---
## 任务管理
- 仅当任务确实复杂（5 步以上、多文件、多阶段）时才用 task_create 建立清单。
- 简单的读取→写入任务（如填写公式、复制数据）禁止使用 task_create，直接执行即可。
- 开始某步前标记 in_progress，完成后立即标记 completed。
- 同一时间只有一个子任务执行中。
- 结束前清理所有任务状态：标记为 completed、failed 或删除已取消项。
