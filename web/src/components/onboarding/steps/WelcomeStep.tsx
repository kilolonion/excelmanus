"use client";

import { motion } from "framer-motion";
import {
  ArrowRight,
  BarChart3,
  Bot,
  Check,
  FileSpreadsheet,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";

const smoothEase: [number, number, number, number] = [0.4, 0, 0.2, 1];

const containerVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08, delayChildren: 0.08 } },
};

const fadeUp = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.45, ease: smoothEase } },
};

const FEATURES = [
  { icon: FileSpreadsheet, title: "读写 Excel", desc: "理解表格结构，保留公式和格式" },
  { icon: BarChart3, title: "分析数据", desc: "发现趋势、异常和可以行动的结论" },
  { icon: Bot, title: "自动执行", desc: "把多步骤任务交给 AI 代理完成" },
];

interface WelcomeStepProps {
  onNext: () => void;
  onSkip?: () => void;
}

export function WelcomeStep({ onNext, onSkip }: WelcomeStepProps) {
  return (
    <motion.div
      className="em-onboarding-step em-onboarding-welcome"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      <div className="onboarding-orb onboarding-orb-1" aria-hidden="true" />
      <div className="onboarding-orb onboarding-orb-2" aria-hidden="true" />

      <div className="em-onboarding-welcome-copy">
        <motion.div variants={fadeUp} className="em-onboarding-eyebrow">
          <Sparkles aria-hidden="true" />
          约 1 分钟完成设置
        </motion.div>
        <motion.h1 variants={fadeUp} tabIndex={-1}>
          让表格工作，<span>交给 AI</span>
        </motion.h1>
        <motion.p variants={fadeUp} className="em-onboarding-welcome-description">
          ExcelManus 能读懂你的工作簿，用自然语言完成整理、分析和格式调整。先连接一个模型，马上开始你的第一个任务。
        </motion.p>

        <motion.div variants={fadeUp} className="em-onboarding-feature-list">
          {FEATURES.map(({ icon: Icon, title, desc }) => (
            <div key={title} className="em-onboarding-feature">
              <span className="em-onboarding-feature-icon"><Icon aria-hidden="true" /></span>
              <span>
                <strong>{title}</strong>
                <small>{desc}</small>
              </span>
            </div>
          ))}
        </motion.div>

        <motion.div variants={fadeUp} className="em-onboarding-welcome-actions">
          <Button size="lg" onClick={onNext} className="em-onboarding-primary-action">
            开始设置
            <ArrowRight aria-hidden="true" />
          </Button>
          {onSkip && (
            <button type="button" onClick={onSkip} className="em-onboarding-secondary-action">
              先看看，稍后在设置中连接
            </button>
          )}
        </motion.div>
      </div>

      <motion.div variants={fadeUp} className="em-onboarding-product-preview" aria-label="ExcelManus 工作流示意">
        <div className="em-onboarding-preview-topbar">
          <span className="em-onboarding-preview-dots" aria-hidden="true"><i /><i /><i /></span>
          <span>月度销售报表.xlsx · 示例</span>
          <span className="em-onboarding-preview-status"><span /> 已准备</span>
        </div>
        <div className="em-onboarding-preview-body">
          <div className="em-onboarding-sheet-preview" aria-hidden="true">
            <div className="em-onboarding-sheet-row em-onboarding-sheet-header"><span>区域</span><span>销售额</span><span>同比</span></div>
            <div className="em-onboarding-sheet-row"><span>华东</span><span>¥248,300</span><span className="positive">+18.4%</span></div>
            <div className="em-onboarding-sheet-row"><span>华南</span><span>¥192,850</span><span className="positive">+11.7%</span></div>
            <div className="em-onboarding-sheet-row"><span>华北</span><span>¥164,200</span><span className="positive">+8.2%</span></div>
            <div className="em-onboarding-sheet-row em-onboarding-sheet-highlight"><span>合计</span><span>¥605,350</span><span className="positive">+13.2%</span></div>
          </div>
          <div className="em-onboarding-prompt-card">
            <div className="em-onboarding-prompt-icon"><Sparkles aria-hidden="true" /></div>
            <div>
              <span>自然语言指令</span>
              <strong>按区域汇总销售额，并生成趋势图</strong>
            </div>
            <div className="em-onboarding-prompt-check"><Check aria-hidden="true" /></div>
          </div>
        </div>
        <div className="em-onboarding-preview-footer">
          <span><span className="em-onboarding-live-dot" /> AI 已理解工作簿结构</span>
          <span>保留原格式</span>
        </div>
      </motion.div>
    </motion.div>
  );
}
