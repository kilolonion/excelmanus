"use client";

import { useState } from "react";
import { Check, FileSpreadsheet, Play, Square } from "lucide-react";
import type { PracticeKind } from "./tour-steps";

/** Local exercises: no workspace mutations, uploads, model calls or credentials. */
export function TourPractice({ kind, onDone }: { kind: PracticeKind; onDone: () => void }) {
  const [value, setValue] = useState("");
  const [selected, setSelected] = useState("");
  const [running, setRunning] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [sheet, setSheet] = useState("销售");
  const choose = (next: string) => { setSelected(next); onDone(); };

  let content;
  switch (kind) {
    case "workspace":
      content = <><div className="em-tour-options">{["对话", "文件"].map((tab) => <button key={tab} type="button" aria-pressed={(selected || "对话") === tab} onClick={() => choose(tab)}>{tab}</button>)}</div><p>{selected === "文件" ? "销售数据.xlsx · 预算.csv" : "销售分析 · 本周工作计划"}</p></>;
      break;
    case "file":
      content = <><button type="button" className="em-tour-file" onClick={() => choose("file")}><FileSpreadsheet aria-hidden="true" /> 示例销售数据.xlsx <span>{selected ? "已引用" : "点选引用"}</span></button>{selected && <p role="status">任务已引用 @示例销售数据.xlsx</p>}</>;
      break;
    case "prompt":
    case "commands":
    case "rule":
      content = <form onSubmit={(event) => { event.preventDefault(); if (value.trim()) choose(value.trim()); }}>
        <label htmlFor={`practice-${kind}`}>{kind === "rule" ? "演示规则" : kind === "commands" ? "试着输入 /" : "演示需求"}</label>
        <input id={`practice-${kind}`} value={value} autoComplete="off" onChange={(event) => { setValue(event.target.value); if (kind !== "rule" && event.target.value.trim()) onDone(); }} placeholder={kind === "rule" ? "金额保留两位小数" : kind === "commands" ? "/" : "按区域汇总销售额，并生成趋势图"} />
        {kind === "commands" && value.startsWith("/") && <button type="button" onClick={() => { setValue("/plan"); onDone(); }}>/plan · 先制定计划</button>}
        {kind === "rule" && <button type="submit" disabled={!value.trim()}>{selected ? "已添加演示规则" : "添加到演示"}</button>}
        {kind === "rule" && selected && <p role="status"><Check aria-hidden="true" /> {selected}</p>}
      </form>;
      break;
    case "run":
      content = <><p aria-live="polite">{running ? "正在演示任务执行…试试暂停。" : selected ? "演示已暂停，可以随时重新开始。" : "整理销售数据，按月份汇总"}</p><button type="button" onClick={() => { if (running) { setRunning(false); choose("stopped"); } else setRunning(true); }}>{running ? <><Square aria-hidden="true" /> 暂停演示</> : <><Play aria-hidden="true" /> 发送演示</>}</button></>;
      break;
    case "mode":
      content = <><div className="em-tour-options">{["编辑", "观察", "计划"].map((mode) => <button key={mode} type="button" aria-pressed={selected === mode} onClick={() => choose(mode)}>{mode}</button>)}</div><p>{selected === "观察" ? "只读分析，不修改文件。" : selected === "计划" ? "先确认方案，再决定是否执行。" : selected === "编辑" ? "可以修改文件，按审批策略执行。" : "点选一种模式了解它的用途。"}</p></>;
      break;
    case "model":
      content = <label>演示模型<select value={selected} onChange={(event) => choose(event.target.value)}><option value="" disabled>选择一个模型</option><option>模型 A · 日常整理</option><option>模型 B · 复杂分析</option></select>{selected && <span role="status">已选：{selected}</span>}</label>;
      break;
    case "sheet":
      content = <><div className="em-tour-options">{["销售", "汇总"].map((tab) => <button type="button" key={tab} aria-pressed={sheet === tab} onClick={() => { setSheet(tab); setSelected(""); }}>{tab}</button>)}</div><table aria-label="示例工作表"><thead><tr><th>{sheet === "销售" ? "月份" : "区域"}</th><th>销售额</th></tr></thead><tbody>{(sheet === "销售" ? [["1 月", "12,800"], ["2 月", "15,600"]] : [["华东", "28,400"], ["华南", "21,600"]]).map(([label, amount], i) => <tr key={label}><td>{label}</td><td><button type="button" aria-label={`选择 ${sheet} B${i + 2}`} aria-pressed={selected === `B${i + 2}`} onClick={() => choose(`B${i + 2}`)}>{amount}</button></td></tr>)}</tbody></table><p role="status">{selected ? `已选 ${sheet}!${selected}，可引用到任务中` : "试试点选销售额单元格"}</p></>;
      break;
    case "history":
      content = <><div className="em-tour-options">{["修改前", "修改后"].map((version) => <button key={version} type="button" aria-pressed={(selected || "修改前") === version} onClick={() => choose(version)}>{version}</button>)}</div><p>B2 · {selected === "修改后" ? "¥12,800.00 · 已应用金额格式" : "12800 · 原始数值"}</p></>;
      break;
    case "formats":
      content = <><div className="em-tour-options">{["Excel / CSV", "Word", "图片"].map((format) => <button key={format} type="button" aria-pressed={selected === format} onClick={() => choose(format)}>{format}</button>)}</div><p>{selected === "Word" ? "整理文档内容、调整段落与格式。" : selected === "图片" ? "提取图片中的表格，需要支持图片的模型。" : selected ? "清洗数据、计算公式和生成报表。" : "选择一种文件类型，了解常见用法。"}</p></>;
      break;
    case "skill":
    case "memory":
      content = <><button type="button" aria-expanded={expanded} onClick={() => { setExpanded(!expanded); onDone(); }}>{kind === "skill" ? "财务报表格式化 · 示例技能" : "数值格式偏好 · 示例记忆"}<span>{expanded ? "收起" : "展开"}</span></button>{expanded && <p>{kind === "skill" ? "识别金额列 → 统一小数位 → 添加汇总行 → 设置打印区域。" : "金额保留两位小数；输出说明使用中文。"}</p>}</>;
      break;
    case "mcp":
      content = <><div className="em-tour-options">{["本地进程", "网络服务"].map((transport) => <button key={transport} type="button" aria-pressed={selected === transport} onClick={() => choose(transport)}>{transport}</button>)}</div><p>{selected === "本地进程" ? "stdio：填写命令和参数，由服务端启动进程。" : selected ? "HTTP / SSE：填写工具服务地址和所需凭证。" : "点选连接方式，了解要准备的配置。"}</p></>;
      break;
    case "runtime":
      content = <><button type="button" role="switch" aria-checked={expanded} onClick={() => { setExpanded(!expanded); onDone(); }}>演示自动压缩 <span>{expanded ? "已开启" : "已关闭"}</span></button><p>{expanded ? "上下文接近上限时压缩历史，保留关键内容。" : "运行参数的实际效果与当前模型和配置有关。"}</p></>;
      break;
    case "subscription":
      content = <><div className="em-tour-options">{["API Key", "订阅授权"].map((method) => <button key={method} type="button" aria-pressed={selected === method} onClick={() => choose(method)}>{method}</button>)}</div><p>{selected === "订阅授权" ? "在订阅页发起授权，完成后回到工作区。" : selected ? "在供应商控制台获取密钥，填入供应商配置。" : "选择一种连接方式，了解配置入口。"}</p></>;
  }
  return <section className="em-tour-practice" aria-label="交互练习"><div className="em-tour-practice-label">动手试试 <span>仅演示</span></div>{content}</section>;
}
