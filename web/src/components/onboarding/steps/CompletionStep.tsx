"use client";

import { ArrowRight, CheckCircle2, Compass, FolderOpen, Settings, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompletionStepProps {
  configured: boolean;
  onFinish: () => void;
  onSkip: () => void;
  onBack: () => void;
}

export function CompletionStep({ configured, onFinish, onSkip, onBack }: CompletionStepProps) {
  const Icon = configured ? CheckCircle2 : Compass;
  return (
    <div className="em-onboarding-step em-onboarding-completion">
      <span className="em-onboarding-completion-icon"><Icon aria-hidden="true" /></span>
      <div className="em-onboarding-eyebrow">下一站 · 交互体验</div>
      <h1 tabIndex={-1}>{configured ? "模型已就绪，来试试吧" : "先体验功能，模型稍后连接"}</h1>
      <p>{configured ? "用几个小练习熟悉工作区。" : "演示练习无需 API Key，正式处理任务前再到设置中连接模型。"} 每一步都可以跳过，也可以随时结束。</p>
      <div className="em-onboarding-completion-topics">
        <span><MessageSquare aria-hidden="true" /> 对话与任务</span>
        <span><FolderOpen aria-hidden="true" /> 文件与表格</span>
        <span><Settings aria-hidden="true" /> 模型与插件</span>
      </div>
      <Button onClick={onFinish} className="em-onboarding-primary-action">体验功能引导 <ArrowRight aria-hidden="true" /></Button>
      <button type="button" onClick={onSkip} className="em-onboarding-secondary-action">跳过引导，直接进入工作区</button>
      {!configured && <button type="button" onClick={onBack} className="em-onboarding-secondary-action">返回连接模型</button>}
    </div>
  );
}
