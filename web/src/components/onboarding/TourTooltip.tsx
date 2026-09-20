"use client";

import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, ArrowRight, CheckCircle2, ChevronDown, ChevronUp, LocateFixed, X } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import type { CoachPhase } from "@/stores/onboarding-state";
import type { TourStep } from "./tour-steps";
import { useGuideViewport } from "./useTargetRect";
import { getTourCardMaxHeight, placeTourCard } from "./tour-layout";
import { TourPractice } from "./TourPractice";

interface TourTooltipProps {
  step: TourStep; stepIndex: number; totalSteps: number;
  phase: string; phaseLabel: string; interactionDone: boolean;
  targetRect: DOMRect | null; isMobile: boolean; hasPrevious: boolean;
  onNext: () => void; onPrevious: () => void; onSkip: () => void;
  onSkipSection: () => void; onSectionChange: (phase: CoachPhase) => void;
  onLocate: () => void; onPracticeDone: () => void;
}

export function TourTooltip({ step, stepIndex, totalSteps, phase, phaseLabel, interactionDone, targetRect, onNext, onPrevious, hasPrevious, onSkip, onSkipSection, onSectionChange, onLocate, onPracticeDone, isMobile }: TourTooltipProps) {
  const tooltipRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(isMobile ? 280 : 460);
  const [collapsed, setCollapsed] = useState(false);
  const viewport = useGuideViewport();
  const reduceMotion = useReducedMotion();
  const stepId = `${phase}-${stepIndex}`;
  const practiceDisclosureKey = `${stepId}-${isMobile ? "mobile" : "desktop"}`;
  const [practiceDisclosure, setPracticeDisclosure] = useState({ key: practiceDisclosureKey, expanded: !isMobile });
  const practiceExpanded = practiceDisclosure.key === practiceDisclosureKey ? practiceDisclosure.expanded : !isMobile;

  useEffect(() => {
    const el = tooltipRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setHeight(el.getBoundingClientRect().height));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    bodyRef.current?.scrollTo(0, 0);
    headingRef.current?.focus({ preventScroll: true });
  }, [stepId]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onSkip();
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onSkip]);

  const cardMaxHeight = getTourCardMaxHeight(viewport.height, isMobile, practiceExpanded);
  const position = placeTourCard(targetRect, viewport, isMobile ? 352 : 380, Math.min(height, cardMaxHeight), step.placement);
  return createPortal(
    <div ref={tooltipRef} className="em-tour-card" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-description" data-compact={viewport.height < 460} data-mobile={isMobile} data-practice-expanded={practiceExpanded} style={{ ...position, maxHeight: Math.min(position.maxHeight, cardMaxHeight), transition: reduceMotion ? "none" : "left 160ms ease, top 160ms ease" }}>
      <header className="em-tour-header">
        <label className="sr-only" htmlFor="tour-chapter">选择引导章节</label>
        <select id="tour-chapter" value={phase} onChange={(event) => onSectionChange(event.target.value as CoachPhase)}>
          <option value="basic">对话与任务</option><option value="advanced">文件与表格</option><option value="settings">模型与插件</option>
        </select>
        <span aria-label={`${phaseLabel} 第 ${stepIndex + 1} 步，共 ${totalSteps} 步`}>{stepIndex + 1} / {totalSteps}</span>
        <button type="button" aria-label={collapsed ? "展开引导" : "收起引导"} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ChevronDown /> : <ChevronUp />}</button>
        <button type="button" aria-label="结束全部引导" onClick={onSkip}><X /></button>
      </header>
      <div className="em-tour-progress" role="progressbar" aria-label="本节进度" aria-valuenow={stepIndex + 1} aria-valuemin={1} aria-valuemax={totalSteps}><span style={{ width: `${((stepIndex + 1) / totalSteps) * 100}%` }} /></div>
      <div ref={bodyRef} className="em-tour-body">
        <motion.div key={stepId} initial={reduceMotion ? false : { opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.16 }}>
        <h2 ref={headingRef} tabIndex={-1} id="tour-title">{step.title}</h2>
        <div hidden={collapsed}>
          <p id="tour-description">{step.description}</p>
          {step.practice && isMobile && (
            <button
              type="button"
              className="em-tour-practice-toggle"
              aria-expanded={practiceExpanded}
              onClick={() => setPracticeDisclosure({ key: practiceDisclosureKey, expanded: !practiceExpanded })}
            >
              {practiceExpanded ? "收起互动练习" : "展开互动练习"}
              {practiceExpanded ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
            </button>
          )}
          {step.practice && (!isMobile || practiceExpanded) && <TourPractice key={stepId} kind={step.practice} onDone={onPracticeDone} />}
          <div className="em-tour-status" aria-live="polite">
            {interactionDone ? <><CheckCircle2 aria-hidden="true" /> 已完成练习，准备好后继续</> : step.practice ? "可先试一试，也可以直接跳过" : "随时可从系统设置重新查看"}
          </div>
          {!targetRect && <button type="button" className="em-tour-locate" onClick={onLocate}><LocateFixed aria-hidden="true" /> 重新定位功能区域</button>}
        </div>
        </motion.div>
      </div>
      <footer className="em-tour-footer">
        <div className="em-tour-navigation">
          <button type="button" onClick={onPrevious} disabled={!hasPrevious} aria-label="上一步"><ArrowLeft /></button>
          <button type="button" onClick={onNext}>跳过此步</button>
          <button type="button" className="em-tour-next" onClick={onNext}>{stepIndex === totalSteps - 1 ? "完成本节" : "下一步"}<ArrowRight /></button>
        </div>
        <div className="em-tour-exit"><button type="button" onClick={onSkipSection}>跳过本节</button><button type="button" onClick={onSkip}>结束引导</button></div>
      </footer>
    </div>,
    document.body,
  );
}
