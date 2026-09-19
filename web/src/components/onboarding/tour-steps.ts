/**
 * Tour step definitions — pure data, no React components or side effects.
 * Side effects are handled by the tour controller via onEnter/onInteractionDone string keys.
 */

// ── Types ──

export type InteractionType = "click" | "input" | "navigate" | "drag";

export interface StepInteraction {
  type: InteractionType;
  hint: string;
  inputTrigger?: string;
  autoAdvanceMs?: number;
}

export interface TourStep {
  target: string;
  title: string;
  description: string;
  icon: string;
  placement: "bottom" | "top" | "right" | "left";
  interaction?: StepInteraction;
  onEnter?: string;
  onInteractionDone?: string;
  stagePadding?: number;
  allowActiveInteraction?: boolean;
  /** When set, the highlight rect expands to include this secondary element (e.g. a popover). */
  expandTarget?: string;
}

export interface TourScene {
  id: string;
  label: string;
  steps: TourStep[];
  onSceneEnter?: string;
  onSceneExit?: string;
}

// ── Basic Tour (Desktop) ──

const BASIC_DESKTOP: TourStep[] = [
  {
    target: "coach-sidebar",
    title: "对话管理",
    description: "在这里创建新对话、查看历史记录，左侧栏是你的工作区入口",
    icon: "MessageSquare",
    placement: "right",
    onEnter: "ensureDemoSession_openSidebar",
  },
  {
    target: "coach-sidebar-tabs",
    title: "对话 & 文件",
    description: "切换「对话」和「文件」标签，快速在聊天记录和 Excel 文件间导航",
    icon: "FolderOpen",
    placement: "right",
    onEnter: "openSidebar_chats",
    interaction: { type: "click", hint: "👆 试试点击「文件」标签", autoAdvanceMs: 800 },
  },
  {
    target: "coach-demo-file",
    title: "拖拽文件到输入框",
    description: "从侧边栏拖拽文件到输入框，即可快速引用文件开始工作",
    icon: "GripVertical",
    placement: "right",
    onEnter: "openSidebar_files_injectDemo",
    interaction: { type: "drag", hint: "👈 拖拽这个示例文件到右侧输入框", autoAdvanceMs: 1200 },
    onInteractionDone: "clearInput_delayed",
  },
  {
    target: "coach-chat-input",
    title: "输入你的需求",
    description: "在这里用自然语言描述任务，也可以直接拖拽上传 Excel/CSV 文件",
    icon: "Upload",
    placement: "top",
    onEnter: "switchSidebarToChats",
    interaction: { type: "input", hint: "✍️ 试试在输入框中输入任何内容", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-send-btn",
    title: "发送消息",
    description: "输入完成后，点击发送按钮让 AI 开始处理你的任务",
    icon: "Send",
    placement: "top",
    onEnter: "prefillDemoInput",
    interaction: { type: "click", hint: "👆 点击发送按钮", autoAdvanceMs: 1200 },
    onInteractionDone: "injectMockStreaming",
  },
  {
    target: "coach-stop-btn",
    title: "暂停生成",
    description: "AI 正在工作时，你可以随时点击暂停按钮中止当前生成",
    icon: "Pause",
    placement: "top",
    onEnter: "ensureMockStreaming",
    interaction: { type: "click", hint: "👆 试试点击暂停按钮", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-model-selector",
    title: "切换 AI 模型",
    description: "点击这里切换不同的 AI 模型，不同模型在速度和质量上各有侧重",
    icon: "Cpu",
    placement: "bottom",
    onEnter: "cleanupMockStreaming",
  },
  {
    target: "coach-mode-badges",
    title: "审批策略",
    description: "询问会在写入前提示确认；跳过则自动写入，但仍不出工作区。编辑 / 观察 / 计划在输入框左侧切换。",
    icon: "Shield",
    placement: "top",
    interaction: { type: "click", hint: "👆 试试切换审批策略", autoAdvanceMs: 800 },
  },
  {
    target: "coach-settings",
    title: "系统设置",
    description: "随时可以在这里修改模型配置、管理技能、规则和记忆",
    icon: "Settings",
    placement: "right",
    onEnter: "openSidebar_chats",
  },
];

// ── Basic Tour (Mobile) ──

const BASIC_MOBILE: TourStep[] = [
  {
    target: "coach-chat-input",
    title: "输入你的需求",
    description: "在这里用自然语言描述任务，也可以上传 Excel/CSV 文件",
    icon: "Upload",
    placement: "top",
    onEnter: "ensureDemoSession",
    interaction: { type: "input", hint: "✍️ 试试输入任何内容", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-send-btn",
    title: "发送消息",
    description: "点击发送按钮让 AI 开始处理你的任务",
    icon: "Send",
    placement: "top",
    onEnter: "prefillDemoInput",
    interaction: { type: "click", hint: "👆 点击发送按钮", autoAdvanceMs: 1200 },
    onInteractionDone: "injectMockStreaming",
  },
  {
    target: "coach-stop-btn",
    title: "暂停生成",
    description: "AI 工作时，点击暂停按钮可以中止当前生成",
    icon: "Pause",
    placement: "top",
    interaction: { type: "click", hint: "👆 试试点击暂停按钮", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-model-selector",
    title: "切换 AI 模型",
    description: "点击这里切换不同的 AI 模型",
    icon: "Cpu",
    placement: "bottom",
    onEnter: "cleanupMockStreaming",
  },
  {
    target: "coach-mode-badges",
    title: "审批策略",
    description: "询问会在写入前确认；跳过则自动写入，但仍不出工作区。",
    icon: "Shield",
    placement: "top",
    interaction: { type: "click", hint: "👆 试试切换审批策略", autoAdvanceMs: 800 },
  },
];

// ── Advanced Tour (Desktop) ──

const ADVANCED_DESKTOP: TourStep[] = [
  {
    target: "coach-chat-input",
    title: "斜杠命令",
    description: "输入 / 可以触发快捷命令，包括模式切换、撤销操作等",
    icon: "Sparkles",
    placement: "top",
    onEnter: "ensureDemoSession_closeSettings_switchChats",
    interaction: { type: "input", hint: "✍️ 试试在输入框中输入 /", inputTrigger: "/", autoAdvanceMs: 1200 },
    expandTarget: "coach-command-popover",
  },
  {
    target: "coach-chat-input",
    title: "切换对话模式",
    description: "用输入框左侧的编辑 / 观察 / 计划切换权限。编辑可写表；观察只读；计划先写方案，改表须批准退出。斜杠只用 /plan。",
    icon: "BookOpen",
    placement: "top",
    interaction: { type: "input", hint: "✍️ 输入 /plan 看看计划模式命令", inputTrigger: "/plan", autoAdvanceMs: 1500 },
    expandTarget: "coach-command-popover",
  },
  {
    target: "coach-demo-file",
    title: "文件实时预览",
    description: "AI 修改 Excel 后，点击侧边栏「文件」中的文件即可实时预览修改结果和前后对比",
    icon: "Eye",
    placement: "right",
    onEnter: "clearInput_openSidebar_files_injectDemo",
    interaction: { type: "click", hint: "👆 点击示例文件查看预览", autoAdvanceMs: 1200 },
    onInteractionDone: "openExcelPanel",
  },
  {
    target: "coach-excel-panel",
    title: "Excel 预览面板",
    description: "在这里查看完整表格、切换 Sheet。功能区「开始」改格式，「公式」插入函数或让 AI 解释/生成，「数据」做质量检查与筛选分析，「历史」看版本；右侧可做选区引用、刷新、下载或展开到聊天",
    icon: "Table2",
    placement: "left",
    onEnter: "ensureExcelPanelOpen",
  },
  {
    target: "coach-settings",
    title: "技能与规则",
    description: "在设置中可以添加自定义技能包和规则，让 AI 按你的风格和流程工作",
    icon: "Sparkles",
    placement: "right",
    onEnter: "cleanupExcelPreview_openSidebar",
    interaction: { type: "navigate", hint: "👆 点击打开设置面板", autoAdvanceMs: 1500 },
  },
];

// ── Advanced Tour (Mobile) ──

const ADVANCED_MOBILE: TourStep[] = [
  {
    target: "coach-chat-input",
    title: "斜杠命令",
    description: "输入 / 可以触发快捷命令，包括模式切换、撤销等",
    icon: "Sparkles",
    placement: "top",
    onEnter: "closeSettings",
    interaction: { type: "input", hint: "✍️ 试试输入 /", inputTrigger: "/", autoAdvanceMs: 1200 },
    expandTarget: "coach-command-popover",
  },
  {
    target: "coach-chat-input",
    title: "切换对话模式",
    description: "左侧切换编辑 / 观察 / 计划。斜杠只用 /plan，没有 /write 或 /read。",
    icon: "BookOpen",
    placement: "top",
    interaction: { type: "input", hint: "✍️ 输入 /plan 看看计划模式命令", inputTrigger: "/plan", autoAdvanceMs: 1500 },
    expandTarget: "coach-command-popover",
  },
];

// ── Settings Tour (Desktop) ──

const SETTINGS_DESKTOP: TourStep[] = [
  {
    target: "coach-settings-tabs",
    title: "设置导航栏",
    description: "设置面板分为 7 个功能区：模型、规则、技能、MCP、记忆、系统、版本。点击标签可以快速切换",
    icon: "Settings",
    placement: "bottom",
    onEnter: "openSettings_model",
  },
  {
    target: "coach-settings-content-model",
    title: "模型配置",
    description: "「供应商」里添加模型服务，「模型配置」里为聊天、记忆和决策任务选择模型",
    icon: "Server",
    placement: "left",
    onEnter: "openSettings_model",
  },
  {
    target: "coach-settings-profiles",
    title: "模型提供商",
    description: "先添加提供商并填写 API Key。点击已有提供商可将其设为默认。TypeSafe 与 Vercel 的 Jev 在下方单独配置",
    icon: "Server",
    placement: "left",
  },
  {
    target: "coach-settings-model-roles",
    title: "任务模型",
    description: "从已添加的提供商中选择聊天模型和记忆模型。下拉项会同时显示提供商和模型名称",
    icon: "Server",
    placement: "left",
    onEnter: "openSettings_model_roles",
  },
  {
    target: "coach-settings-tab-rules",
    title: "规则管理",
    description: "规则可以约束 AI 的行为。支持「全局规则」和「会话规则」，例如：\"不要删除原始数据列\"",
    icon: "ScrollText",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到规则页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-content-rules",
    title: "规则列表",
    description: "这里展示所有已添加的规则。每条规则可以单独开关或删除",
    icon: "ScrollText",
    placement: "left",
    onEnter: "openSettings_rules_withDemo",
  },
  {
    target: "coach-settings-rule-input",
    title: "试试添加规则",
    description: "在输入框中输入一条规则，体验规则添加流程",
    icon: "ScrollText",
    placement: "top",
    interaction: { type: "input", hint: "✍️ 试试在输入框中输入任意规则内容", autoAdvanceMs: 1200 },
    allowActiveInteraction: true,
  },
  {
    target: "coach-settings-tab-skills",
    title: "技能包管理",
    description: "技能包让 AI 学会特定领域的工作流程。支持从文件、Gitee/GitHub 导入，也可以手动创建",
    icon: "Package",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到技能页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-skills-list",
    title: "技能包列表",
    description: "每个技能包包含描述、文件匹配模式和执行指令。点击卡片可以展开查看详情",
    icon: "Package",
    placement: "left",
    onEnter: "openSettings_skills",
    interaction: { type: "click", hint: "👆 点击任意技能卡片展开详情", autoAdvanceMs: 1500 },
  },
  {
    target: "coach-settings-tab-mcp",
    title: "MCP 工具服务",
    description: "MCP (Model Context Protocol) 让 AI 连接外部工具和数据源，扩展 AI 的能力边界",
    icon: "Plug",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到 MCP 页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-mcp-add-btn",
    title: "添加 MCP 服务器",
    description: "点击这里可以添加新的 MCP 服务器。支持 stdio、SSE、HTTP 三种传输方式",
    icon: "Plug",
    placement: "bottom",
    onEnter: "openSettings_mcp",
  },
  {
    target: "coach-settings-tab-memory",
    title: "记忆系统",
    description: "AI 会自动从对话中提取关键信息存为记忆，下次对话时自动加载",
    icon: "Brain",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到记忆页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-memory-list",
    title: "记忆条目",
    description: "这里展示 AI 自动提取的记忆。按类别分组，点击可展开查看完整内容，不需要的可单独删除",
    icon: "Brain",
    placement: "left",
    onEnter: "openSettings_memory_withDemo",
  },
  {
    target: "coach-settings-tab-runtime",
    title: "系统运行时",
    description: "基础区是代码模式、默认上下文、压缩、记忆、图片识别和子代理。会话配额、代码风险分级和技能发现在高级设置",
    icon: "SlidersHorizontal",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到系统页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-content-runtime",
    title: "运行时参数",
    description: "系统页分为基础和高级。默认上下文窗口和压缩保存后会立刻同步到已打开的对话；多数其他项要新开对话或重启后生效",
    icon: "SlidersHorizontal",
    placement: "left",
    onEnter: "openSettings_runtime",
  },
  {
    target: "coach-settings-advanced-toggle",
    title: "高级设置",
    description: "展开后可调会话配额、压缩细节、图片请求投影、代码风险分级、技能发现等。建议保持默认值",
    icon: "SlidersHorizontal",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击展开高级设置", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-tab-version",
    title: "版本与更新",
    description: "查看当前版本号、检查更新、查看更新日志",
    icon: "ArrowUpCircle",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到版本页面", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-content-version",
    title: "版本信息",
    description: "这里显示当前运行版本和最新版本。恭喜你完成了所有引导！",
    icon: "ArrowUpCircle",
    placement: "left",
    onEnter: "openSettings_version",
  },
];

// ── Settings Tour (Mobile) ──

const SETTINGS_MOBILE: TourStep[] = [
  {
    target: "coach-settings-tabs",
    title: "设置导航",
    description: "设置面板分为模型、规则、技能、MCP、记忆、系统、版本 7 个区域",
    icon: "Settings",
    placement: "bottom",
    onEnter: "openSettings_model",
  },
  {
    target: "coach-settings-content-model",
    title: "模型配置",
    description: "「供应商」里添加模型服务，「模型配置」里为聊天、记忆和决策任务选择模型",
    icon: "Server",
    placement: "top",
    onEnter: "openSettings_model",
  },
  {
    target: "coach-settings-tab-rules",
    title: "规则",
    description: "添加全局/会话规则约束 AI 行为",
    icon: "ScrollText",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到规则", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-rule-input",
    title: "试试添加规则",
    description: "在输入框中输入规则内容，点击添加即可生效",
    icon: "ScrollText",
    placement: "top",
    onEnter: "openSettings_rules_withDemo",
    interaction: { type: "input", hint: "✍️ 试试输入任意规则", autoAdvanceMs: 1200 },
    allowActiveInteraction: true,
  },
  {
    target: "coach-settings-tab-skills",
    title: "技能包",
    description: "从文件或 Gitee/GitHub 导入技能包",
    icon: "Package",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到技能", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-tab-mcp",
    title: "MCP 工具",
    description: "连接外部工具和数据源扩展 AI 能力",
    icon: "Plug",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到 MCP", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-tab-memory",
    title: "记忆",
    description: "查看和删除跨会话记忆。记忆由模型或你主动写入",
    icon: "Brain",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到记忆", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-tab-runtime",
    title: "系统设置",
    description: "代码模式、上下文、记忆、图片识别和子代理。深度参数在高级设置",
    icon: "SlidersHorizontal",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击切换到系统", autoAdvanceMs: 1000 },
  },
  {
    target: "coach-settings-tab-version",
    title: "版本更新",
    description: "查看版本号、检查更新。恭喜完成所有引导！",
    icon: "ArrowUpCircle",
    placement: "bottom",
    interaction: { type: "click", hint: "👆 点击查看版本", autoAdvanceMs: 1000 },
  },
];

// ── Scene Definitions ──

export function getTourScenes(isMobile: boolean): TourScene[] {
  return [
    {
      id: "basic",
      label: "基础引导",
      steps: isMobile ? BASIC_MOBILE : BASIC_DESKTOP,
      onSceneEnter: "ensureDemoSession",
      onSceneExit: "cleanupDemoSession",
    },
    {
      id: "advanced",
      label: "进阶探索",
      steps: isMobile ? ADVANCED_MOBILE : ADVANCED_DESKTOP,
      onSceneEnter: "ensureDemoSession",
      onSceneExit: "cleanupAdvancedScene",
    },
    {
      id: "settings",
      label: "设置引导",
      steps: isMobile ? SETTINGS_MOBILE : SETTINGS_DESKTOP,
      onSceneEnter: "lockSettings",
      onSceneExit: "unlockAndCloseSettings",
    },
  ];
}
