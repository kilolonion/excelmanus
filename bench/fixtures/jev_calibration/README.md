# Jev 标定夹具

更新日期：2026-09-19

[Bench 指南](../../README.md) · [Jev 配置](../../../docs/configuration.md)

本目录的 `samples.json` 是离线合成样本，`signed` 保持为 `false`。固定答案用于验证解析与策略路径，不证明真实模型对中文任务的判断能力，也不代表允许启用执行侧影响。

## 离线合成

```bash
uv run python bench/jev_offline_synthesize.py
```

在仓库根目录运行。离线合成使用固定 JSON，经 `policy.synthesize` 检查，不访问模型服务。样本中的用户文本来自评测套件，题包定义位于 `excelmanus/system_one/packs.py`。

## 真实模型标定

先在相应 data home 的主库配置决策提供商与密钥，并安装 `system-one` extra，再运行：

```bash
uv run python bench/jev_live_calibrate.py --limit 8 --source fixture
```

这一步访问外部服务，可能产生费用；缺少可用密钥时退出码为 `2`。报告默认写入 `local/`，其中可能包含中文任务原文，因此该目录不入库。CLI 参数以 `--help` 为准。

## 签字与适用范围

标定器不会自动修改 `SIGNED_ENFORCE_PACKS`、`SIGNED_ENFORCE_FAMILIES` 或运行配置中的标定状态。当前仓库签字集合为空。要应用 enforce 策略，必须满足相应的人工评审、签字及运行配置条件；不能仅将样本的 `signed` 改成 `true`。

评审应记录使用的模型、题包版本、数据来源与结果。离线测试通过、请求成功和真实标定通过是不同结论，报告中应分别说明。
