"""Package-owned documents. References are IDs, never workspace file paths."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import re


@dataclass(frozen=True)
class Topic:
    id: str
    title: str
    summary: str
    keywords: str

    def read(self) -> str:
        return files("excelmanus.knowledge").joinpath("topics", self.id + ".md").read_text(encoding="utf-8")


TOPICS = (
    Topic("documentation", "文档服务与查询方法", "搜索定位、准确正文、工具规范、示例与引用。", "文档 检索 查询 门户 search fetch docs documentation examples schema"),
    Topic("architecture", "系统设计与能力边界", "会话、提示词、执行目录、工具调用与结果的关系。", "架构 设计 原理 architecture system agent ExcelManus"),
    Topic("workflows", "表格与文档工作流程", "观察、分析、编辑、核验、视觉预览和交付。", "读取 Excel 数据 新建 编辑 Word 工作流 workflow spreadsheet read"),
    Topic("configuration", "配置来源与生效范围", "持久设置、会话实际值、自我管理和模型能力。", "配置 设置 模型 推理 并发 token config settings model"),
    Topic("execution", "权限、工作区与代码执行", "模式、审批、文件边界、Python SDK 与宿主依赖。", "权限 审批 沙盒 源码 内部文档 Python SDK Docker permission runtime"),
    Topic("context", "上下文、技能与记忆", "按需工具披露、技能加载、压缩、附件与持久记忆。", "上下文 压缩 提示词 记忆 技能 附件 context skill memory prompt"),
    Topic("collaboration", "计划、任务与子代理", "计划模式、同步/后台委派、用户消息与并发依赖。", "计划 子代理 委派 后台 排队 中断 并行 subagent delegate plan dispatch"),
    Topic("recovery", "错误恢复与结果解释", "版本冲突、部分覆盖、审批失败和已提交后失败。", "错误 故障 恢复 回滚 版本 不支持 error failure recovery"),
)
TOPIC_BY_ID = {topic.id: topic for topic in TOPICS}
LINK_RE = re.compile(r"\[[^\]]+\]\(knowledge:([^\s)]+)\)")


def references(text: str) -> list[str]:
    return list(dict.fromkeys(LINK_RE.findall(text)))


def match_score(query: str, text: str) -> float:
    """Small bilingual lexical search; a miss is unknown, never a denial."""
    query, text = query.strip().lower(), text.lower()
    if not query:
        return 0
    if query in text:
        return 10
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", query)
    terms = set()
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token) and len(token) > 2:
            terms.update(token[i:i + 2] for i in range(len(token) - 1))
        else:
            terms.add(token)
    return sum(term in text for term in terms) / max(1, len(terms))
