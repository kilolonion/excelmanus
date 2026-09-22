"use client";

import { useCallback, useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useWorkbookWorkflowStore, type WorkbookHandoff, type WorkbookWorkflowScope } from "@/stores/workbook-workflow-store";
import { useSessionStore } from "@/stores/session-store";
import { sendWorkbookHandoff, WORKBOOK_OPERATION_LABELS } from "@/lib/workbook-handoff";
import { captureWorkbookDraft, reviewWorkbookMerge, type WorkbookMergeReview } from "@/lib/workbook-merge";

const FIELDS: Record<string, [string, string, string][]> = {
  "merge-workbooks": [["mode", "合并方式", "例如：追加行，或按共同字段关联"], ["keys", "关联字段", "例如：订单号、商品编码"], ["destination", "结果位置", "默认生成新工作簿，保留原文件"]],
  filter: [["column", "筛选列", "例如：金额"], ["condition", "筛选条件", "例如：大于 1000，且状态为已完成"]],
  chart: [["chart_type", "图表类型", "例如：柱状图"], ["categories", "分类列", "例如：月份"], ["series", "数据列", "例如：销售额、成本"], ["destination", "放置位置", "例如：新建汇总工作表"]],
  pivot: [["rows", "行字段", "例如：地区"], ["columns", "列字段", "例如：月份"], ["values", "汇总字段", "例如：销售额求和"], ["destination", "输出位置", "例如：新建透视表工作表"]],
  sort: [["columns", "排序列及顺序", "例如：日期升序，金额降序"]],
  dedupe: [["keys", "重复判定列", "例如：订单号、商品编码"]],
};

export function WorkbookWorkflowDialogs() {
  const handoff = useWorkbookWorkflowStore((s) => s.handoff);
  const conflict = useWorkbookWorkflowStore((s) => s.conflict);
  const sessionId = useSessionStore((s) => s.activeSessionId);
  return <>
    {handoff?.sessionId === sessionId && handoff && <HandoffDialog key={handoff.id} request={handoff} />}
    {conflict?.sessionId === sessionId && conflict && <ConflictDialog key={conflict.id} request={conflict} />}
  </>;
}

function HandoffDialog({ request }: { request: WorkbookHandoff & { id: number } }) {
  const [instruction, setInstruction] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = () => {
    const store = useWorkbookWorkflowStore.getState();
    if (store.handoff?.id === request.id) store.closeHandoff();
  };
  const submit = async () => {
    if (busy) return;
    setBusy(true); setError("");
    try {
      await sendWorkbookHandoff(request, instruction, { ...request.parameters, requested: fields });
      close();
    } catch (error) { setError(error instanceof Error ? error.message : "发送失败，请重试"); }
    finally { setBusy(false); }
  };
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) close(); }}>
    <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
      <DialogTitle>{WORKBOOK_OPERATION_LABELS[request.operation] ?? "表格操作"} · 交给 Agent</DialogTitle>
      <DialogDescription>先读取数据并展示执行方案，确认后再改表。</DialogDescription>
      <p className="text-sm break-all">{request.file.relative} · {request.sheet || "待核对工作表"}{request.range ? `!${request.range}` : ""}</p>
      {request.operation === "merge-workbooks" && Array.isArray(request.parameters?.sources) && <ul className="text-sm space-y-1">
        {request.parameters.sources.map((source: { path: string }, index: number) => <li key={source.path} className="break-all">{index === 0 ? "主表" : "参考表"}：{source.path}</li>)}
      </ul>}
      {request.parameters && Object.keys(request.parameters).length > 0 && <p className="text-xs text-muted-foreground">已带上本次操作的参数和文件版本，可补充或调整以下要求。</p>}
      {(FIELDS[request.operation] ?? []).map(([key, label, placeholder]) => <label key={key} className="grid gap-1 text-sm">{label}
        <input className="border rounded px-2 py-1.5 bg-background" placeholder={placeholder} value={fields[key] ?? ""} maxLength={500}
          onChange={(event) => setFields({ ...fields, [key]: event.target.value })} disabled={busy} />
      </label>)}
      <label className="grid gap-1 text-sm">补充要求<textarea className="border rounded p-2 bg-background min-h-20" value={instruction} maxLength={2000}
        placeholder="可以说明希望保留的数据、输出位置或其他要求" onChange={(event) => setInstruction(event.target.value)} disabled={busy} /></label>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={close}>取消</Button>
        <Button disabled={busy} onClick={() => void submit()}>{busy ? "正在交接…" : "让 Agent 生成方案"}</Button></div>
    </DialogContent>
  </Dialog>;
}

function displayValue(value: unknown) { return value == null ? "（空白）" : String(value); }

function ConflictDialog({ request }: { request: WorkbookWorkflowScope & { id: number } }) {
  const [draft, setDraft] = useState("");
  const [review, setReview] = useState<WorkbookMergeReview | null>(null);
  const [choices, setChoices] = useState<Record<string, "local" | "remote">>({});
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const close = () => {
    const store = useWorkbookWorkflowStore.getState();
    if (store.conflict?.id === request.id) store.closeConflict();
  };
  const inspect = useCallback(async (signal?: AbortSignal) => {
    setBusy(true); setReview(null); setError(""); setChoices({});
    try {
      const captured = await captureWorkbookDraft(request);
      const result = await reviewWorkbookMerge(request, captured);
      if (signal?.aborted) return;
      setDraft(captured); setReview(result);
    } catch (error) { if (!signal?.aborted) setError(error instanceof Error ? error.message : "核对失败"); }
    finally { if (!signal?.aborted) setBusy(false); }
  }, [request]);
  useEffect(() => {
    const controller = new AbortController();
    void inspect(controller.signal);
    return () => controller.abort();
  }, [inspect]);
  const conflicts = review?.cells?.filter((cell) => cell.conflict) ?? [];
  const merge = async () => {
    if (busy || !review?.content_version) return;
    setBusy(true); setError("");
    try {
      const result = await reviewWorkbookMerge(request, draft, { version: review.content_version, choices, operationId: crypto.randomUUID() });
      if (result.status !== "merged") { setReview(result); return; }
      close();
    } catch (error) { setReview(null); setError(error instanceof Error ? error.message : "合并失败，草稿已保留"); }
    finally { setBusy(false); }
  };
  const replan = async () => {
    if (busy || !draft) return;
    setBusy(true); setError("");
    try {
      await sendWorkbookHandoff({ ...request, operation: "conflict-replan", version: review?.content_version },
        "请核对我的未保存修改与当前文件，保留双方非冲突内容；先展示合并方案并等待确认。",
        { draft: JSON.parse(draft), current_version: review?.content_version, comparison: review?.cells, choices });
      close();
    } catch (error) { setError(error instanceof Error ? error.message : "交接失败，草稿已保留"); }
    finally { setBusy(false); }
  };
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) close(); }}>
    <DialogContent className="sm:max-w-4xl max-h-[85vh] overflow-y-auto">
      <DialogTitle>核对并合并修改</DialogTitle>
      <DialogDescription>{request.file.relative} · 比较编辑前、我的草稿和当前文件。当前文件中的其他修改都会保留。</DialogDescription>
      {busy && <p role="status" className="text-sm">正在处理…</p>}
      {review?.status === "replan" && <p className="text-sm">{review.reason}</p>}
      {review?.status === "review" && <>
        <p className="text-sm">{review.safe_count ?? 0} 项本地修改可直接保留，{review.conflict_count ?? 0} 项需要选择。</p>
        <div className="overflow-auto max-h-[45vh]"><table className="w-full text-xs border-collapse">
          <thead><tr>{["位置", "编辑前", "我的修改", "当前文件", "保留哪一方"].map((label) => <th key={label} className="text-left border p-2">{label}</th>)}</tr></thead>
          <tbody>{review.cells?.map((cell) => <tr key={cell.id} className={cell.conflict ? "bg-amber-500/10" : ""}>
            <td className="border p-2 whitespace-nowrap">{cell.sheet}!{cell.cell}<br />{cell.field}</td>
            {[cell.base, cell.local, cell.remote].map((value, index) => <td key={index} className="border p-2 max-w-52"><div className="max-h-24 overflow-auto whitespace-pre-wrap break-all">{displayValue(value)}</div></td>)}
            <td className="border p-2">{cell.conflict ? <select aria-label={`${cell.sheet}!${cell.cell} ${cell.field} 保留哪一方`} value={choices[cell.id] ?? ""} disabled={busy}
              onChange={(event) => setChoices({ ...choices, [cell.id]: event.target.value as "local" | "remote" })} className="border rounded bg-background p-1">
              <option value="" disabled>请选择</option><option value="local">我的修改</option><option value="remote">当前文件</option>
            </select> : "保留双方"}</td>
          </tr>)}</tbody>
        </table></div>
      </>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="outline" disabled={busy} onClick={close}>保留草稿，稍后处理</Button>
        <Button variant="outline" disabled={busy} onClick={() => void inspect()}>重新核对</Button>
        <Button variant="outline" disabled={busy || !draft} onClick={() => void replan()}>让 Agent 重新规划</Button>
        {review?.status === "review" && <Button disabled={busy || conflicts.some((cell) => !choices[cell.id])} onClick={() => void merge()}>确认合并并保存</Button>}
      </div>
    </DialogContent>
  </Dialog>;
}
