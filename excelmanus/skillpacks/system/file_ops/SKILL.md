---
name: file_ops
description: 工作区文件管理技能包（查看、搜索、读取、复制、重命名、删除）
allowed_tools:
  - list_directory
  - get_file_info
  - search_files
  - read_text_file
  - copy_file
  - rename_file
  - delete_file
  - read_excel
triggers:
  - 文件
  - 目录
  - 列出
  - 查看文件
  - 打开
  - 搜索
  - 查找文件
  - 文件信息
  - 文件详情
  - 读取文本
  - CSV
  - TXT
  - 复制
  - 备份
  - 重命名
  - 移动
  - 删除
  - 移除
file_patterns:
  - "*"
priority: 4
version: "2.0.0"
---
文件操作只允许在工作区内进行，所有路径经安全校验：
1. 先列目录或搜索文件，确认目标存在后再操作。
2. 默认不展示隐藏文件，除非用户明确要求。
3. 写入操作（复制、重命名）不覆盖已有文件。
4. 删除操作需二次确认（confirm=true），且仅限文件，不删除目录。
5. 读取文本文件时注意编码，默认 UTF-8。
