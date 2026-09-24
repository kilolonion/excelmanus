# 评测夹具

适用版本：1.8.1 源码 · 更新日期：2026-09-21

[Bench 指南](../README.md) · [套件字段](../cases/README.md)

夹具生成器创建可重复的办公文件，供真实模型评测使用。生成文件本身不调用模型，也不代表任务已经通过。

| 生成器或目录 | 内容 | 默认输出 |
| --- | --- | --- |
| `build_experiential.py` | 包含缺失、冲突和混合格式的体验任务输入 | `experiential/` |
| `build_realistic.py` | Excel、CSV、Word、大表，以及匹配的标准答案 | `realistic/` |
| `jev_calibration/` | 固定的 Jev 离线合成样本 | 见该目录说明 |

在仓库根目录运行：

```bash
uv run python bench/fixtures/build_experiential.py
uv run python bench/fixtures/build_realistic.py
```

办公夹具默认生成 60,000 行大表；位置参数可调整大表行数和随机种子：

```bash
uv run python bench/fixtures/build_realistic.py 20000 20240914
```

重新生成会改写同名夹具。输入文件和 `answers.json` 必须来自同一次生成；不能用新输入配旧答案。`answers.json` 供评测器检查结果，不应作为 Agent 的任务附件。

`experiential/` 与 `realistic/` 是生成目录，不提交到仓库。套件按这些相对路径引用附件，运行前应先生成。更多说明见 [Jev 标定夹具](jev_calibration/README.md)。
