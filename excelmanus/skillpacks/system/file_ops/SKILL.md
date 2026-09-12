---
name: file_ops
description: 工作区文件管理（查看、搜索、读取、复制、重命名、删除）。
file_patterns:
  - "*"
version: "2.1.0"
---
路径只在工作区内。写入不覆盖已有文件。删除要 `confirm=true`，且只删文件不删目录。文本默认 UTF-8。
