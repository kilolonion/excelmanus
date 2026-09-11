# 技能装载（单用户）

ExcelManus 是单进程、单工作区。技能不再按登录用户分目录。

进程启动时一次性装载：

```
api.py lifespan
├─ SkillpackLoader.load_all()
│   ├─ system:  excelmanus/skillpacks/system
│   ├─ user:    ~/.excelmanus/skillpacks
│   └─ project: .excelmanus/skillpacks
├─ SkillRouter
└─ SkillpackManager
```

仓库根下的 `skills/` 仍按现有扫描规则装入，不要删除。

旧的 per-user 技能目录（`users/{id}/skillpacks/`）不再创建、不再迁移。若本地还留着，运维自行挑一份拷到 `~/.excelmanus/skillpacks` 或项目 `.excelmanus/skillpacks`。
