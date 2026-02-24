"use client";

const SUGGESTIONS = [
  "帮我分析这份销售数据",
  "创建一个数据透视表",
  "对比两张表的差异",
  "生成图表并插入到 Sheet",
];

interface WelcomePageProps {
  onSuggestionClick: (text: string) => void;
}

export function WelcomePage({ onSuggestionClick }: WelcomePageProps) {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center px-4 overflow-y-auto">
      <div className="flex items-center gap-3 mb-4">
        <img
          src="/logo.svg"
          alt="ExcelManus"
          className="h-16 w-auto"
        />
      </div>
      <p className="text-muted-foreground mb-8">基于大语言模型的 Excel 智能代理</p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-lg w-full">
        {SUGGESTIONS.map((text) => (
          <button
            key={text}
            onClick={() => onSuggestionClick(text)}
            className="rounded-xl border border-border bg-card p-4 text-left text-sm
              hover:bg-accent/5 active:bg-accent/10 transition-colors cursor-pointer min-h-[44px]"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}
