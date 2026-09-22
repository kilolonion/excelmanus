/** Shared chapters keep the same indices when the window rotates or resizes. */
export type PracticeKind = "workspace" | "file" | "prompt" | "run" | "mode" | "model" | "commands" | "sheet" | "history" | "formats" | "rule" | "skill" | "mcp" | "memory" | "runtime" | "subscription";
export interface TourStep {
  target: string;
  title: string;
  description: string;
  icon: string;
  placement: "bottom" | "top" | "right" | "left";
  practice?: PracticeKind;
  interaction?: { type: "click"; hint: string };
  onEnter?: string;
  stagePadding?: number;
  expandTarget?: string;
}
export interface TourScene {
  id: "basic" | "advanced" | "settings";
  label: string;
  steps: TourStep[];
}

export function getTourScenes(isMobile: boolean): TourScene[] {
  return [
    {
      id: "basic", label: "对话与任务", steps: [
        { target: "coach-sidebar-tabs", title: "认识你的工作区", description: "对话保存任务过程，文件集中管理工作资料。先在下面切换一次文件视图。", icon: "MessageSquare", placement: "right", onEnter: "openSidebar_chats", practice: "workspace" },
        { target: "coach-sidebar-file-tools", title: "把文件带进任务", description: isMobile ? "从文件区点选文件即可引用；触屏无需拖拽。试试将示例文件加入下方任务。" : "从文件区拖拽文件到输入框，也可以点选引用。试试添加下面的示例文件。", icon: "FolderOpen", placement: "right", onEnter: "openSidebar_files", practice: "file" },
        { target: "coach-chat-input", title: "描述你想得到的结果", description: "说明文件、处理范围和输出要求，任务会更清楚。试着写一句你的需求。", icon: "MessageSquare", placement: "top", onEnter: "showComposer", practice: "prompt" },
        { target: "coach-chat-input", title: "发送与暂停任务", description: "任务运行时可以暂停。用下面的演示体验发送和暂停，练习不会调用模型。", icon: "Send", placement: "top", onEnter: "showComposer", practice: "run" },
        { target: "coach-mode-presets", title: "选择工作方式", description: "编辑可以修改文件，观察用于只读分析，计划先整理方案。正式任务的写入还受审批策略控制。", icon: "Shield", placement: "top", onEnter: "showComposer", practice: "mode" },
        { target: "coach-model-selector", title: "按任务切换模型", description: "顶部可选择已配置的模型。先在演示中切换一次，实际模型稍后可在设置中连接。", icon: "Cpu", placement: "bottom", onEnter: "showComposer", practice: "model" },
      ],
    },
    {
      id: "advanced", label: "文件与表格", steps: [
        { target: "coach-chat-input", title: "快捷命令", description: "在输入框键入 / 可以查看命令，例如 /plan。下面的练习只展示命令，不会执行。", icon: "Sparkles", placement: "top", onEnter: "showComposer", practice: "commands" },
        { target: "coach-workbook-entry", title: "查看表格与切换工作表", description: "从右上角的工作表入口打开预览，再切换 Sheet 或点选单元格。先在下面的示例中试一遍。", icon: "Table2", placement: "bottom", onEnter: "showComposer", practice: "sheet" },
        { target: "coach-workbook-entry", title: "检查修改与历史", description: "打开工作表后，可在表格功能区查看版本和修改记录。下面可以比较同一单元格的前后值。", icon: "History", placement: "bottom", onEnter: "showComposer", practice: "history" },
        { target: "coach-upload-button", title: "上传与多种文件", description: "输入框左侧的附件入口支持表格、文档与图片。先选择一个示例类型，查看它适合怎样的任务。", icon: "Upload", placement: "top", onEnter: "showComposer", practice: "formats" },
      ],
    },
    {
      id: "settings", label: "模型与扩展", steps: [
        { target: "coach-settings-profiles", title: "管理模型供应商", description: "在供应商页添加连接并测试可用性。这里切换的是演示选项，不会修改你的默认模型。", icon: "Server", placement: "left", onEnter: "openSettings_model", practice: "model" },
        { target: "coach-settings-model-roles", title: "为不同任务分配模型", description: "在模型配置中，为聊天、记忆等任务选择模型。正式下拉列表来自你已添加的供应商。", icon: "Cpu", placement: "left", onEnter: "openSettings_model_roles", practice: "model" },
        { target: "coach-settings-subtab-subscription", title: "订阅与授权", description: "已有订阅时，可在“订阅与 OAuth”中完成授权；API Key 在供应商页配置。授权流程可以稍后进行。", icon: "KeyRound", placement: "bottom", onEnter: "openSettings_subscription", practice: "subscription" },
        { target: "coach-settings-rule-input", title: "让 AI 记住工作规则", description: "规则越具体越容易执行。试着添加一条演示规则，例如“金额保留两位小数”。", icon: "ScrollText", placement: "top", onEnter: "openSettings_rules", practice: "rule" },
        { target: "coach-settings-skills-list", title: "用技能复用工作流程", description: "技能将常用流程整理为指令。点击下面的示例查看内容，实际技能可在此页导入或管理。", icon: "Package", placement: "left", onEnter: "openSettings_skills", practice: "skill" },
        { target: "coach-settings-mcp-add-btn", title: "连接外部工具", description: "MCP 可连接额外工具和数据源。先了解本地进程与网络服务的区别，再按服务文档填写。", icon: "Plug", placement: "bottom", onEnter: "openSettings_mcp", practice: "mcp" },
        { target: "coach-settings-memory-list", title: "查看跨会话记忆", description: "记忆帮助后续对话沿用偏好和上下文。试着展开示例记忆；真实条目可在此页管理。", icon: "Brain", placement: "left", onEnter: "openSettings_memory", practice: "memory" },
        { target: "coach-settings-runtime-compaction", title: "按需调整系统设置", description: "上下文、压缩和其他运行参数在系统页管理。先用演示开关了解自动压缩，正式配置可保持默认。", icon: "SlidersHorizontal", placement: "left", onEnter: "openSettings_runtime", practice: "runtime" },
        { target: "coach-settings-tab-version", title: "更新与重新查看引导", description: "版本页用于检查更新。以后可在“设置 → 系统 → 新手引导”选择任意章节重新体验。", icon: "ArrowUpCircle", placement: "bottom", onEnter: "openSettings_version" },
      ],
    },
  ];
}
