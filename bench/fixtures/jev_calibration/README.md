# Jev 离线标定夹具

本目录是**骨架**，不是 live 中文对照签字。

- `samples.json`：`signed: false`。`answers` 是手写固定 JSON，测试用 `policy.synthesize` 离线跑，**禁止打网**。
- `state.user_text` 抽自 `bench/cases/suite_write_approval.json` / `suite_experiential.json` / `suite_realistic.json`。
- 题包 `instructions` 仍是英文（`excelmanus/system_one/packs.py`）。
- 把 `signed` 改成 true、或往 `SIGNED_ENFORCE_PACKS` 加 pack，等于宣称可以 enforce——**未对照数据前不要改**。
- `local/`（gitignore）：live 对照 JSON/Markdown，含中文原文，不入库。

跑离线合成：

```bash
uv run python bench/jev_offline_synthesize.py
```

跑 live 对照（有密钥才打网；无密钥退出码 2，不假装成功）：

```bash
uv run python bench/jev_live_calibrate.py --limit 8 --source fixture
```

报告默认写到 `bench/fixtures/jev_calibration/local/`。  
**下一步：人工审阅后才能改 SIGNED_ENFORCE_PACKS / CALIBRATED。标定器永不自动签字。**
