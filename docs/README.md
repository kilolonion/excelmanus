# ExcelManus 文档导航 · Documentation

本文档集对应 **1.8.0 源码**，最近同步于 **2026-09-21**。安装包是否已发布、支持的系统架构及签名状态，以对应发布资产为准。

These guides describe the **1.8.0 source tree**, updated on **2026-09-21**. Installer availability, architecture, and signing status depend on the published assets.

## 使用与部署 · Using and deploying

| 主题 / Topic | 中文 | English |
| --- | --- | --- |
| 项目介绍与快速开始 / Overview and quick start | [README](../README.md) | [README](../README_EN.md) |
| 配置、模型、工作区与任务恢复 / Settings, models, workspaces, recovery | [配置参考](configuration.md) | [Configuration](configuration_en.md) |
| 启动、部署、备份与排障 / Deployment, backups, troubleshooting | [运维手册](ops-manual.md) | [Operations](ops-manual_en.md) |
| 技能编写与加载 / Skill authoring and loading | [Skillpack 协议](skillpack_protocol.md) | [Skillpack protocol](skillpack_protocol_en.md) |
| 升级方式与边界 / Update behavior | [升级与部署](hot-update-design.md) | Covered in [Operations](ops-manual_en.md) |
| 网页与桌面更新 / Web and desktop updates | [升级与部署](hot-update-design.md) | [Desktop README](../desktop/README.md) |
| 发布打包 / Release packaging | [可重复发布打包](release-packaging.md) | [Desktop README](../desktop/README.md) |
| 隐私与服务说明 / Privacy and terms | [隐私政策](privacy-policy.md) · [用户服务协议](terms-of-service.md) | Chinese documents |

运行服务的 API 请求与响应结构可在 [本机 API 文档](http://localhost:8000/docs) 查看；使用其他端口时相应替换地址。

The running service exposes its request and response schemas at [the local API reference](http://localhost:8000/docs). Adjust the port for your installation.

## 开发与维护 · Development and maintenance

| 文档 / Guide | 内容 / Coverage |
| --- | --- |
| [Web README](../web/README.md) | 前端开发、地址配置、构建与检查 / Frontend development, origin settings, builds, checks |
| [Desktop README](../desktop/README.md) | App 界面、桌面打包、运行时、签名与数据目录 / App UI, packaging, runtimes, signing, profiles |
| [提示词分层与维护](prompt-layering.md) | 提示词、工具参数与执行约束的维护边界 / Prompt, schema, and runtime responsibilities |
| [技能装载与工作区](skill-isolation-design.md) | 技能发现与工作区范围 / Skill discovery and workspace scope |
| [Bench](../bench/README.md) | 真实模型评测、隔离配置与结果解释 / Live-model evaluation and result interpretation |
| [套件字段](../bench/cases/README.md) | 评测输入、断言与权限 / Evaluation inputs, assertions, permissions |
| [夹具生成](../bench/fixtures/README.md) | 体验与办公场景数据 / Experiential and office-task fixtures |
| [Jev 标定夹具](../bench/fixtures/jev_calibration/README.md) | 离线合成与真实标定的区别 / Offline synthesis versus live calibration |
| [固定工作簿场景](../tests/fixtures/scenarios/README.md) | 可重复生成的测试输入 / Reproducible workbook inputs |

## 文档状态 · Document status

以上页面是当前使用与维护入口；本轮同步覆盖版本、配置、部署、更新、工作簿交互、历史和自我管理说明。

The pages above are the current usage and maintenance entry points.

这些历史文件仍可能被 Git 跟踪；文档分类和 `.gitignore` 都不会自动将已跟踪文件排除出源码发布。准备公开源码时，应另行核对实际提交清单。运行时使用的 `excelmanus/prompts/` 和 `excelmanus/skillpacks/` 随 Python 包分发，属于行为指令，应按相应契约维护。

Historical files may still be tracked by Git; this index and `.gitignore` do not exclude tracked files from source distribution. Review the actual commit contents before publication. Runtime prompts and built-in skills ship with the Python package and should be maintained as behavior instructions.
