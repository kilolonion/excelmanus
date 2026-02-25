"use client";

import {
  CalendarDays,
  LineChart,
  Palette,
  Calculator,
  type LucideIcon,
} from "lucide-react";

interface Suggestion {
  text: string;
  icon: LucideIcon;
}

const SUGGESTIONS: Suggestion[] = [
  { text: "创建一份本周团队工作排期表", icon: CalendarDays },
  { text: "生成模拟销售数据并绘制趋势图", icon: LineChart },
  { text: "制作带条件格式的考勤表模板", icon: Palette },
  { text: "创建自动计算的成绩统计表", icon: Calculator },
];

interface WelcomePageProps {
  onSuggestionClick: (text: string) => void;
}

export function WelcomePage({ onSuggestionClick }: WelcomePageProps) {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-4 overflow-y-auto">
      {/* Logo */}
      <div className="flex items-center gap-3 mb-3">
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="h-12 w-auto"
        />
      </div>

      {/* Greeting */}
      <h1 className="text-xl font-semibold mb-1">你好！我是你的 Excel 智能助手</h1>
      <p className="text-sm text-muted-foreground mb-8">上传文件或输入任务，我来帮你处理</p>

      {/* Suggestion cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg w-full">
        {SUGGESTIONS.map(({ text, icon: Icon }) => (
          <button
            key={text}
            onClick={() => onSuggestionClick(text)}
            className="group flex items-center gap-3 rounded-xl border border-border/60 bg-card p-4 text-left text-sm
              hover:border-[var(--em-primary-alpha-20)] hover:bg-[var(--em-primary-alpha-06)]
              hover:shadow-[0_0_12px_var(--em-primary-alpha-10)]
              active:bg-[var(--em-primary-alpha-10)] transition-all duration-200 cursor-pointer min-h-[44px]"
          >
            <span className="flex-shrink-0 h-8 w-8 rounded-lg bg-[var(--em-primary-alpha-06)] flex items-center justify-center group-hover:bg-[var(--em-primary-alpha-15)] transition-colors">
              <Icon className="h-4 w-4 text-muted-foreground group-hover:text-[var(--em-primary)] transition-colors" />
            </span>
            <span className="group-hover:text-foreground transition-colors">{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
