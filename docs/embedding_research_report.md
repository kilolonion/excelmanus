# ExcelManus Embedding 方案调研报告

> 方案基础：`openai.embeddings.create` + `numpy` cosine similarity
> 日期：2025-02-21

---

## 一、技术方案概述

### 1.1 核心组件

| 组件 | 说明 |
|------|------|
| `openai.embeddings.create` | 调用 OpenAI Embedding API（推荐 `text-embedding-3-small`） |
| `numpy` cosine similarity | `np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))` |

### 1.2 成本基线（text-embedding-3-small）

| 指标 | 数值 |
|------|------|
| **价格** | $0.02 / 1M tokens |
| **维度** | 1536（可降维至 512/256） |
| **最大输入** | 8191 tokens |
| **延迟** | ~50-150ms / 请求（单条） |
| **批量** | 支持单次最多 2048 条输入 |

### 1.3 通用实现骨架

```python
import numpy as np
import openai

async def embed(client: openai.AsyncOpenAI, texts: list[str], model: str = "text-embedding-3-small") -> np.ndarray:
    resp = await client.embeddings.create(input=texts, model=model)
    return np.array([d.embedding for d in resp.data])

def cosine_top_k(query_vec: np.ndarray, corpus_vecs: np.ndarray, k: int = 5) -> list[tuple[int, float]]:
    sims = corpus_vecs @ query_vec / (np.linalg.norm(corpus_vecs, axis=1) * np.linalg.norm(query_vec) + 1e-9)
    indices = np.argsort(sims)[::-1][:k]
    return [(int(i), float(sims[i])) for i in indices]
```

---

## 二、场景全景分析

通过对 ExcelManus 代码库的全面审查，识别出以下 **10 个** 可能使用 embedding 的场景：

| # | 场景 | 当前方案 | 所在模块 | 推荐接入 |
|---|------|----------|----------|----------|
| 1 | 持久记忆语义检索 | 按主题文件加载全文 | `persistent_memory.py` / `memory_tools.py` | ⭐⭐⭐ **强烈推荐** |
| 2 | Compaction 关键信息保留 | LLM 全文摘要 | `compaction.py` | ⭐⭐ 推荐 |
| 3 | Skill 路由匹配 | 斜杠直连 + regex + LLM | `skillpacks/router.py` | ⭐ 可选 |
| 4 | 子代理自动选择 | 硬编码返回默认 | `engine.py:_auto_select_subagent` | ⭐ 可选 |
| 5 | 工作区文件语义搜索 | manifest 全量注入 | `workspace_manifest.py` | ⭐⭐ 推荐 |
| 6 | 工具推荐 / 预筛选 | 依赖 LLM 理解工具描述 | `capability_map.py` | ❌ 不推荐 |
| 7 | 记忆去重 | 精确字符串归一 | `persistent_memory.py` | ⭐ 可选 |
| 8 | 任务分类 (write_hint/tags) | regex + LLM | `skillpacks/router.py` | ❌ 不推荐 |
| 9 | Hook 匹配 | glob 模式 | `hooks/matcher.py` | ❌ 不推荐 |
| 10 | PromptComposer 策略选择 | 条件规则匹配 | `prompt_composer.py` | ❌ 不推荐 |

---

## 三、逐场景详细分析

### 场景 1：持久记忆语义检索 ⭐⭐⭐ 强烈推荐

**当前状态：**
- `persistent_memory.py` 中 `load_core()` 加载最近 200 行核心记忆注入 system prompt
- `memory_tools.py` 中 `memory_read_topic()` 按主题名（file_patterns / user_prefs / error_solutions / general）整文件加载
- 无任何语义过滤，记忆增长后大量无关条目占用上下文窗口

**痛点：**
- 记忆条目增长到 100+ 后，`load_core()` 的 200 行窗口中大量条目与当前任务无关
- 主题分类粒度太粗（只有 4 类），无法按语义精准检索
- 容量管理 (`_enforce_capacity`) 用行数硬截断，可能丢失重要但较早的记忆

**Embedding 方案：**
```
写入时：
  MemoryEntry → embed(entry.content) → 存储 (content, category, timestamp, vector)

检索时（替代 load_core 和 memory_read_topic）：
  用户消息 / 当前文件上下文 → embed(query)
  → cosine_top_k(query_vec, all_memory_vecs, k=10)
  → 只注入 top-k 高相关记忆到 system prompt
```

**效果评估：**
- **精准度提升**：从「最近 200 行」到「语义最相关 top-k」，显著减少噪声
- **上下文节省**：当前 load_core 可能注入 2000-4000 tokens 的不相关记忆；embedding 筛选后可压缩到 500-1000 tokens
- **跨主题关联**：用户问「销售报表」时，能同时召回 file_patterns 中的文件结构和 error_solutions 中的相关报错

**成本：**
- **存储**：每条记忆 1536 维 float32 = 6KB；500 条 ≈ 3MB（可忽略）
- **API 调用**：写入时每条 1 次（~50 tokens → $0.000001）；检索时每次查询 1 次
- **延迟**：查询增加 ~100ms（可接受，相比 LLM 调用 1-5s 微不足道）
- **numpy 内存**：500×1536 矩阵 ≈ 3MB（完全内存级操作，无需外部向量库）

**实现复杂度：低-中**
- 需要在 `PersistentMemory` 中增加向量存储层（JSON/npy 文件持久化）
- 需要修改 `load_core()` 和 `memory_read_topic()` 的调用路径
- 需要新增 `embed_and_store()` 和 `semantic_search()` 方法

**结论：这是 embedding 最值得投入的场景。** 投入产出比最高，直接改善用户体验（记忆更精准），且减少上下文占用为其他信息腾出空间。

---

### 场景 2：Compaction 关键信息保留 ⭐⭐ 推荐

**当前状态：**
- `compaction.py` 中 `_do_compact()` 将旧消息全部发给 LLM 做摘要
- 摘要模型消费格式化后的对话历史（最多 60K 字符），生成 ≤800 字摘要
- 摘要质量完全依赖 LLM，无法保证关键信息不丢失

**痛点：**
- 当对话很长时，格式化后的文本可能截断重要消息（`max_content_chars=800`/条）
- LLM 摘要可能遗漏用户在早期提到的关键约束或文件路径
- 摘要后若需要回溯某个早期操作，信息已永久丢失

**Embedding 方案：**
```
压缩前：
  1. 对每条消息 embed → 得到消息向量矩阵
  2. 对用户最近消息 embed → 得到 "当前意图" 向量
  3. cosine_top_k(当前意图, 旧消息向量, k=20) → 保留语义相关的旧消息
  4. 将 top-k 消息 + 摘要一起作为压缩后上下文

或：辅助摘要模型，在摘要 prompt 中标注 "以下消息与当前任务高度相关" 的标记
```

**效果评估：**
- **信息保留**：确保与当前任务相关的早期消息不被摘要丢弃
- **摘要质量**：LLM 得到「这些消息更重要」的提示后，摘要更聚焦

**成本：**
- **API 调用**：压缩时需要 embed 所有旧消息（通常 20-100 条，可批量，~$0.0001）
- **延迟**：压缩本身非实时路径（后台执行），延迟可接受
- **复杂度**：中等，需要修改 `_do_compact()` 流程

**结论：推荐但优先级低于场景 1。** 作为 compaction 的增强手段，在对话特别长时价值明显。

---

### 场景 3：Skill 路由匹配 ⭐ 可选

**当前状态：**
- `skillpacks/router.py` 中 `route()` 方法：
  - 斜杠命令 → `_find_skill_by_name()` 精确/模糊匹配
  - 非斜杠 → 默认全量工具模式（`all_tools`），通过 `activate_skill` 元工具让 LLM 自行选择
- `_classify_task()` 用 regex + LLM 判断 write_hint 和 task_tags

**Embedding 方案：**
```
启动时：embed(每个 skill 的 description + name) → 缓存 skill_vectors
用户消息 → embed(user_message) → cosine_top_k(user_vec, skill_vectors, k=3)
→ 在 activate_skill 的 enum 中优先推荐 top-k skill
```

**为何仅「可选」：**
- 当前方案已经工作良好：斜杠精确路由 + LLM 自主选择 skill 的组合覆盖了绝大多数场景
- Skillpack 数量通常 <20 个，LLM 完全能理解所有 skill description 并自行选择
- 引入 embedding 匹配可能导致非预期的 skill 被推荐（特别是中文任务描述匹配英文 skill 名）

**成本：** 低（仅启动时 embed 一次 skill 列表；每次请求 1 次 embed 查询）
**实现复杂度：** 低

**结论：当 Skillpack 数量增长到 50+ 时才值得考虑。** 当前阶段不推荐接入。

---

### 场景 4：子代理自动选择 ⭐ 可选

**当前状态：**
- `engine.py:_auto_select_subagent()` 直接返回第一个候选（硬编码逻辑）
- 注释说明 `v5.2: 不再调用 LLM，直接返回默认 subagent`
- `SubagentRegistry` 支持 builtin/user/project 三层覆盖

**Embedding 方案：**
```
embed(每个 subagent 的 description) → 缓存
task_text → embed → cosine_top_k → 选择最匹配的子代理
```

**为何仅「可选」：**
- 当前只有一个内建 subagent，选择问题不存在
- 即使用户自定义多个 subagent，数量通常 <10，LLM 或简单规则即可胜任
- 已有的 `_SUBAGENT_NAME_ALIASES` 映射表已覆盖旧角色名归一

**成本：** 极低
**实现复杂度：** 极低

**结论：等子代理生态丰富后再考虑。** 当前 ROI 为零。

---

### 场景 5：工作区文件语义搜索 ⭐⭐ 推荐

**当前状态：**
- `workspace_manifest.py` 中 `build_manifest()` 扫描工作区所有 Excel 文件
- `get_system_prompt_summary()` 按文件数选择注入详细度（≤20 完整/20-100 紧凑/>100 统计）
- 用户提及文件时，`_collect_candidate_file_paths()` 通过正则从消息中提取文件路径

**痛点：**
- 当工作区有 50+ Excel 文件时，完整注入太长，统计模式又丢失细节
- 用户可能说「上个月的销售数据」而非精确文件名，当前正则无法匹配
- `inspect_excel_files` 的 search 功能仅支持文件名/sheet 名字面匹配

**Embedding 方案：**
```
build_manifest 时：
  embed(f"{file.name} | sheets: {sheet_names} | headers: {all_headers}")
  → 缓存 file_vectors

用户消息 → embed → cosine_top_k(user_vec, file_vectors, k=5)
→ 仅注入最相关的 5 个文件的完整元数据到 system prompt
```

**效果评估：**
- **精准度**：「上个月的销售报表」→ 自动关联到 `2025年1月销售数据.xlsx`
- **上下文节省**：50 文件场景下，从注入全量紧凑摘要（~2000 tokens）到仅注入 top-5 完整信息（~500 tokens）
- **用户体验**：减少「请指定文件路径」的追问次数

**成本：**
- **API 调用**：manifest 构建时一次性 embed（50 文件 ≈ 50 条 ≈ $0.00005）；每次请求 1 次查询
- **缓存策略**：manifest 有 mtime 增量更新，仅变更文件需要重新 embed
- **延迟**：查询 ~100ms（manifest 构建时已是后台任务，embed 可并行执行）

**实现复杂度：低-中**
- 在 `WorkspaceManifest` 中增加 `vectors` 字段
- 修改 `get_system_prompt_summary()` 支持 query-aware 模式

**结论：文件数较多时价值明显，推荐作为第二优先级实施。**

---

### 场景 6：工具推荐 / 预筛选 ❌ 不推荐

**当前状态：**
- `capability_map.py` 生成结构化能力图谱注入 system prompt
- LLM 通过理解工具描述自主选择调用哪个工具
- `TOOL_CATEGORIES` 和 `TOOL_SHORT_DESCRIPTIONS` 提供分类索引

**为何不推荐：**
- ExcelManus 的工具总数 ~30-40 个，LLM 完全能处理
- 工具选择是 LLM 的核心能力（tool calling），embedding 预筛选反而可能遗漏工具
- 已有的分类索引（data_read/data_write/format/chart 等）已足够高效
- 如果预筛选错误，agent 将无法调用正确的工具，后果严重

**结论：工具选择应完全交给 LLM，embedding 在此场景弊大于利。**

---

### 场景 7：记忆去重 ⭐ 可选

**当前状态：**
- `persistent_memory.py` 中 `_dedupe_new_entries()` 使用精确字符串归一 `(category, normalized_content)` 作为去重键
- `_normalize_content_key()` 仅做空白归一

**Embedding 方案：**
```
写入前：embed(new_entry.content)
→ 与同 category 的已有向量计算 cosine similarity
→ similarity > 0.95 → 判定为语义重复，跳过写入
```

**为何仅「可选」：**
- 当前精确去重对「完全相同内容」有效
- 语义去重可能误杀：「销售数据.xlsx 有5列」vs「销售数据.xlsx 有5列和3个sheet」→ 高相似但信息量不同
- 去重错误不可逆（丢失的记忆无法恢复）

**成本：** 低（每次写入额外 1 次 embed）
**实现复杂度：** 低

**结论：作为精确去重的补充，但阈值需要保守（>0.98），且需要保留回退机制。优先级低。**

---

### 场景 8：任务分类 (write_hint / task_tags) ❌ 不推荐

**当前状态：**
- `_classify_write_hint_lexical()` 和 `_classify_task_tags_lexical()` 用正则零延迟分类
- 兜底调用 router 小模型做 JSON 分类

**为何不推荐：**
- 分类是**分类问题**，不是**检索问题**，embedding 不适合
- 当前 regex + LLM 的双层方案延迟和准确率都很好
- 分类标签固定（may_write/read_only + 5 种 task_tags），不需要开放域匹配

**结论：不适合 embedding 方案。**

---

### 场景 9：Hook 匹配 ❌ 不推荐

**当前状态：**
- `hooks/matcher.py` 中 `match_tool()` 用 `fnmatch` glob 模式匹配工具名

**为何不推荐：**
- Hook 匹配是精确的、确定性的结构化匹配
- 引入语义匹配会破坏 Hook 的可预测性（安全关键路径）
- 需求本身就是 glob 模式（`read_*`、`write_*`），embedding 完全不合适

**结论：绝对不推荐。Hook 匹配必须保持确定性。**

---

### 场景 10：PromptComposer 策略选择 ❌ 不推荐

**当前状态：**
- `prompt_composer.py` 中 `_match_conditions()` 根据 `PromptContext`（write_hint/sheet_count/total_rows/task_tags）做条件匹配
- 策略段有明确的 `conditions` 字段

**为何不推荐：**
- 策略选择基于结构化条件（if sheet_count > 3 and write_hint == "may_write"），不是语义问题
- 策略数量 <20，条件匹配已足够
- 语义匹配可能导致不恰当的策略被激活

**结论：不适合 embedding 方案。**

---

## 四、实施路线图

### Phase 1：持久记忆语义检索（推荐首先实施）

**目标：** 将 `load_core()` 从「最近 200 行」升级为「语义最相关 top-k」

**实现方案：**

```
excelmanus/
├── embedding/
│   ├── __init__.py
│   ├── client.py          # OpenAI Embedding 客户端封装
│   ├── store.py           # 向量存储（内存 + JSON 持久化）
│   └── search.py          # cosine similarity 检索
```

**关键设计决策：**

1. **向量存储格式**：JSON Lines 文件（每行 `{content_hash, vector, timestamp}`），与 MEMORY.md 共存
2. **缓存策略**：内存中维护 `np.ndarray` 矩阵，启动时从文件加载
3. **增量更新**：`save_entries()` 时同步 embed 并追加向量
4. **降级策略**：API 不可用时回退到当前 `load_core()` 行为

**预计工作量：** 3-5 天
**预计效果：** system prompt 中记忆噪声减少 60-80%

### Phase 2：工作区文件语义搜索

**目标：** 让 agent 能通过语义理解用户意图，精准定位工作区文件

**预计工作量：** 2-3 天（基于 Phase 1 的基础设施复用）

### Phase 3：Compaction 增强（可选）

**目标：** 压缩时保留与当前任务语义相关的关键信息

**预计工作量：** 1-2 天

---

## 五、成本综合估算

### 5.1 API 成本（按典型使用模式）

| 场景 | 调用频率 | 单次 tokens | 月估算（1000 次会话） |
|------|----------|-------------|----------------------|
| 记忆检索（查询） | 每会话 1-3 次 | ~100 | 300K tokens → $0.006 |
| 记忆写入（embed） | 每会话 0-5 条 | ~50/条 | 250K tokens → $0.005 |
| 文件搜索（查询） | 每会话 1 次 | ~100 | 100K tokens → $0.002 |
| 文件 manifest embed | 增量更新 | ~200/文件 | 可忽略 |
| **月总计** | | | **~$0.015** |

**结论：API 成本几乎可以忽略不计。** text-embedding-3-small 的定价极低，即使日活 1000 用户，月成本 <$0.5。

### 5.2 延迟成本

| 操作 | 额外延迟 | 影响 |
|------|----------|------|
| 记忆检索 | +100-150ms | 首轮响应延迟增加，但相比 LLM 响应 2-10s 可忽略 |
| 记忆写入 | +100ms（异步） | 不影响用户感知 |
| 文件搜索 | +100ms | 同记忆检索 |
| numpy 计算 | <1ms（500 条） | 完全可忽略 |

### 5.3 维护成本

| 项目 | 说明 |
|------|------|
| 新增依赖 | `numpy`（已在 Excel 处理中隐式依赖） |
| API 依赖 | 复用现有 `openai.AsyncOpenAI` 客户端 |
| 存储 | 向量 JSON 文件，与 MEMORY.md 同目录，自动管理 |
| 降级路径 | API 不可用时无缝回退到现有行为 |

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| OpenAI Embedding API 不可用 | 低 | 中 | 降级到 load_core() 原有行为 |
| 向量文件损坏 | 极低 | 低 | 自动从 MEMORY.md 重建向量 |
| 语义匹配不准 | 中 | 低 | 混合策略：embedding top-k + 最近 N 条兜底 |
| 多语言匹配偏差 | 中 | 低 | text-embedding-3-small 对中文支持良好 |
| numpy 内存占用 | 极低 | 极低 | 500 条记忆仅 3MB |

---

## 七、与现有架构的兼容性

### 7.1 客户端复用

ExcelManus 已有完整的 `openai.AsyncOpenAI` 客户端体系：
- `engine.py` 中的 `_client`、`_router_client`、`_advisor_client`
- `providers.py` 中的 `create_client()`

Embedding 客户端可复用 `_client` 或创建独立实例（推荐复用，减少连接数）。

### 7.2 配置集成

在 `ExcelManusConfig` 中新增：
```python
embedding_enabled: bool = True
embedding_model: str = "text-embedding-3-small"
embedding_dimensions: int = 1536  # 可降维
memory_semantic_top_k: int = 10
memory_semantic_fallback_recent: int = 5  # 兜底最近 N 条
```

### 7.3 不影响现有测试

- 所有现有测试通过 mock `openai` 客户端运行
- Embedding 功能通过配置开关控制，测试中默认关闭
- 降级路径确保 embedding 不可用时行为与当前一致

---

## 八、最终建议

### 推荐实施（按优先级）

1. **✅ 持久记忆语义检索** — ROI 最高，直接改善用户体验和上下文利用率
2. **✅ 工作区文件语义搜索** — 文件多时价值明显，基础设施可复用
3. **⏳ Compaction 增强** — 作为 Phase 3 选做，长对话场景受益

### 不推荐实施

- **❌ 工具推荐** — LLM 自身能力足够，预筛选反而有害
- **❌ 任务分类** — 分类问题不适合检索方案
- **❌ Hook 匹配** — 安全关键路径必须保持确定性
- **❌ 策略选择** — 结构化条件匹配更合适

### 暂缓实施

- **⏸ Skill 路由** — Skillpack <20 时不需要
- **⏸ 子代理选择** — 子代理数量 <5 时不需要
- **⏸ 记忆语义去重** — 精确去重已够用，语义去重风险高
