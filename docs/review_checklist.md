# Skillpack 协议审查清单

## 代码与行为
- [ ] `loader.py` 的目录发现规则与协议文档一致。
- [ ] 外部工具目录（`.openclaw/skills`）仅支持项目级路径，无 `workspace/skills` 兼容残留。
- [ ] 路由语义（slash/fallback）与 README、测试一致。

## 文档同步
- [ ] README 的环境变量描述与当前实现一致。
- [ ] README 的“目录发现优先级”与实现一致。
- [ ] README 内置 Skillpack 清单与 `excelmanus/skillpacks/system/*/SKILL.md` 一致。
- [ ] 协议主描述更新到 `docs/skillpack_protocol.md`。

## 历史文档治理
- [ ] `tasks/` 中命中旧术语（`hint_direct/confident_direct/llm_confirm/fork_plan/Skillpack.context`）的文档已标注“历史文档声明（Skillpack 协议）”。
- [ ] 历史文档声明包含现行协议跳转链接。

## 自动化护栏
- [ ] 运行 `tests/test_skillpack_docs_contract.py` 通过。
- [ ] 运行 `tests/test_skillpacks.py` 通过。
- [ ] 如涉及路由行为，运行 `tests/test_engine.py` 相关子集通过。
