# 夹具

`build_experiential.py` 生成体验套件用的脏办公文件，默认写到 `experiential/`。

```powershell
uv run python bench/fixtures/build_experiential.py
```

生成物不入库。套件 JSON 里的附件路径是 `bench/fixtures/experiential/...`，跑之前必须先生成。
