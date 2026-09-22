"use client";

import { useMemo, useState } from "react";
import { CheckCircle2, CircleHelp, Clock3, ListTodo, Loader2, Pause, Play, RefreshCw, Send, Square, XCircle } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { FileTypeIcon } from "@/components/ui/file-type-icon";
import type { SubagentControlAction } from "@/lib/api";
import { openWorkspaceFile } from "@/lib/open-workspace-file";
import { normalizeTaskItems } from "@/lib/sse-event-handler";
import { isSubagentActive, subagentChangedFiles, SUBAGENT_STATUS_LABELS } from "@/lib/subagent-runs";
import { basenameOf, toolActionTitle } from "@/lib/tool-labels";
import { displayFilePath } from "@/lib/file-identity";
import type { SubagentRun } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/stores/session-store";
import { TaskList } from "./TaskList";
import { useBackgroundTasks } from "./use-background-tasks";

type ControlTask = (runId: string, action: SubagentControlAction, message?: string) => Promise<SubagentRun>;

export function BackgroundTaskCard({ run, onControl, onOpenFile }: {
  run: SubagentRun;
  onControl: ControlTask;
  onOpenFile: (path: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState<SubagentControlAction | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const active = isSubagentActive(run.status);
  const question = run.pending_question;
  const failed = ["error", "max-tokens", "refusal"].includes(run.status);
  const Icon = run.status === "running" ? Loader2
    : run.status === "waiting_input" ? CircleHelp
      : run.status === "queued" ? Clock3
        : run.status === "completed" ? CheckCircle2
          : failed ? XCircle : Pause;
  const files = subagentChangedFiles(run);
  const answer = question && selected.length
    ? [...selected.map((index) => String(index + 1)), message.trim()].filter(Boolean).join("\n")
    : message.trim();

  async function act(action: SubagentControlAction) {
    if (pending) return;
    setPending(action);
    setError("");
    setNotice("");
    try {
      await onControl(run.run_id, action, action === "send" ? answer : message.trim());
      if (action === "send" || action === "resume") {
        setMessage("");
        setSelected([]);
      }
      setNotice(action === "send"
        ? question ? "回答已提交" : "指令已发送，将在下一步处理"
        : action === "resume" ? "已创建后续任务，可在列表中查看" : "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作未完成，请刷新任务状态后重试");
    } finally {
      setPending(null);
    }
  }

  return (
    <article className="rounded-2xl border border-[var(--em-hairline)] bg-background p-4 space-y-3" aria-label={run.task}>
      <div className="flex items-start gap-2.5">
        <Icon className={cn("mt-0.5 h-4 w-4 shrink-0 text-[var(--em-primary)]", run.status === "running" && "animate-spin", failed && "text-red-500")} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium whitespace-pre-wrap break-words">{run.task}</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            {run.agent_name === "explorer" ? "探索器" : run.agent_name === "subagent" ? "通用子代理" : run.agent_name}
            {" · "}{run.iteration} 轮 · {run.tool_calls} 次调用
          </p>
        </div>
        <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]", failed && "bg-red-500/10 text-red-600")}>
          {SUBAGENT_STATUS_LABELS[run.status]}
        </span>
      </div>

      {run.resumed_from && <p className="text-xs text-muted-foreground">接续已有任务的对话</p>}
      {active && run.last_tool && <p className="text-xs text-muted-foreground">最近操作：{toolActionTitle(run.last_tool)}</p>}
      {run.status === "interrupted" && <p className="text-xs text-muted-foreground">上次执行已中断，可以从已保存的对话继续。</p>}
      {run.status === "paused" && <p className="text-xs text-muted-foreground">已保留对话和完成的文件改动，可以继续。</p>}
      {run.result?.diagnostic && failed && <p role="alert" className="text-sm text-red-600 break-words">{run.result.diagnostic}</p>}
      {run.result?.output && (
        <details>
          <summary className="cursor-pointer text-sm text-[var(--em-primary)]">查看执行结果</summary>
          <div className="prose prose-sm dark:prose-invert max-w-none mt-2 break-words max-h-80 overflow-auto">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{run.result.output}</ReactMarkdown>
          </div>
        </details>
      )}
      {files.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">已修改的文件</p>
          {files.map((path) => (
            <button key={path} type="button" onClick={() => onOpenFile(path)} title={displayFilePath(path)}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-[var(--em-fill)]">
              <FileTypeIcon filename={basenameOf(path)} className="h-4 w-4 shrink-0" />
              <span className="truncate">{basenameOf(path)}</span>
            </button>
          ))}
        </div>
      )}

      {question && (
        <fieldset className="rounded-xl bg-[var(--em-fill)] p-3 space-y-2" disabled={!!pending}>
          <legend className="max-w-full truncate text-xs font-medium px-1" title={question.header || "需要你的回答"}>{question.header || "需要你的回答"}{question.multi_select ? "（可多选）" : ""}</legend>
          <p className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words text-sm">{question.text}</p>
          {question.options.map((option, index) => (
            <label key={index} className="flex items-start gap-2 text-sm cursor-pointer">
              <input type={question.multi_select ? "checkbox" : "radio"} name={`answer-${run.run_id}`}
                checked={selected.includes(index)} className="mt-1 accent-[var(--em-primary)]"
                onChange={() => setSelected((previous) => question.multi_select
                  ? previous.includes(index) ? previous.filter((value) => value !== index) : [...previous, index]
                  : [index])} />
              <span className="min-w-0 break-words">{option.is_other ? "其他" : option.label}<span className="block text-xs text-muted-foreground">{option.description}</span></span>
            </label>
          ))}
        </fieldset>
      )}

      <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); if (!active || answer) void act(active ? "send" : "resume"); }}>
        <Textarea value={message} onChange={(event) => setMessage(event.target.value)} disabled={!!pending}
          aria-label={question ? "填写回答" : active ? "补充任务指令" : "继续任务的补充说明"}
          placeholder={question ? "选择上方选项，或直接填写回答…" : active ? "补充指令，下一个步骤开始时处理…" : "可填写补充说明，也可以直接继续"}
          className="min-h-16 resize-y text-sm" rows={2} />
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" size="sm" disabled={!!pending || (active && !answer)}>
            {pending === "send" || pending === "resume" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : active ? <Send className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            {active ? question ? "提交回答" : "发送指令" : "继续任务"}
          </Button>
          {active && <>
            <Button type="button" variant="outline" size="sm" disabled={!!pending} onClick={() => void act("pause")}>
              {pending === "pause" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}暂停
            </Button>
            <Button type="button" variant="ghost" size="sm" disabled={!!pending} onClick={() => void act("cancel")}>
              {pending === "cancel" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}取消任务
            </Button>
          </>}
        </div>
      </form>
      {(pending === "pause" || pending === "cancel") && <p role="status" className="text-xs text-muted-foreground">正在停止，等待当前操作收尾…</p>}
      {notice && <p role="status" className="text-xs text-muted-foreground">{notice}</p>}
      {error && <p role="alert" className="text-xs text-red-600 break-words">{error}</p>}
    </article>
  );
}

function SessionBackgroundTasks({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const { runs, taskList, loading, error, refresh, control } = useBackgroundTasks(sessionId, open);
  const taskItems = useMemo(() => normalizeTaskItems(taskList), [taskList]);
  const activeCount = runs.filter((run) => isSubagentActive(run.status)).length;
  const waitingCount = runs.filter((run) => run.status === "waiting_input").length;
  const ordered = [...runs].sort((a, b) => {
    const rank = (run: SubagentRun) => run.status === "waiting_input" ? 0 : isSubagentActive(run.status) ? 1 : 2;
    return rank(a) - rank(b) || b.created_at - a.created_at;
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="h-8 gap-1.5 px-2 text-muted-foreground"
          title={waitingCount ? `${waitingCount} 个后台任务等待回答` : "后台任务"}
          aria-label={waitingCount ? `后台任务，${waitingCount} 个等待回答` : `后台任务${activeCount ? `，${activeCount} 个未结束` : ""}`}>
          <ListTodo className="h-4 w-4" />
          {activeCount > 0 && <span className={cn("rounded-full min-w-4 px-1 text-[10px] bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]", waitingCount > 0 && "bg-amber-500/15 text-amber-700 dark:text-amber-400")}>{activeCount}</span>}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl max-h-[85dvh] flex flex-col overflow-hidden p-0 gap-0">
        <DialogHeader className="p-5 pr-14 border-b border-[var(--em-hairline)] text-left">
          <DialogTitle>后台任务</DialogTitle>
          <DialogDescription>当前会话的任务清单与后台任务。后台任务在对话结束后仍会继续，关闭此面板不影响执行。</DialogDescription>
        </DialogHeader>
        <div className="overflow-y-auto min-h-0 p-4 space-y-3">
          {taskItems.length > 0 && (
            <section className="space-y-1.5" aria-label="任务清单">
              <p className="text-xs text-muted-foreground">任务清单{taskList?.title ? ` · ${taskList.title}` : ""}</p>
              <TaskList items={taskItems} />
            </section>
          )}
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span aria-live="polite">{runs.length} 个后台任务{activeCount ? ` · ${activeCount} 个未结束` : ""}{waitingCount ? ` · ${waitingCount} 个等待回答` : ""}</span>
            <Button variant="ghost" size="sm" onClick={refresh} disabled={loading} className="h-7 text-xs">
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />刷新
            </Button>
          </div>
          {error && <p role="alert" className="text-sm text-red-600">状态更新失败：{error}。{runs.length ? "当前显示上次获取的状态。" : ""}</p>}
          {runs.length === 0 && <p className={cn("text-center text-sm text-muted-foreground", taskItems.length === 0 && "py-10")}>{loading ? "正在读取任务…" : error ? "暂时无法获取任务" : taskItems.length > 0 ? "暂无后台任务。" : "还没有后台任务。可以在对话中让助手把独立任务放到后台执行。"}</p>}
          {ordered.map((run) => (
            <BackgroundTaskCard key={`${run.run_id}:${run.pending_question?.question_id ?? ""}`} run={run} onControl={control}
              onOpenFile={(path) => { setOpen(false); openWorkspaceFile(path); }} />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function BackgroundTasks() {
  const sessionId = useSessionStore((s) => s.activeSessionId);
  if (!sessionId || sessionId.startsWith("__onboarding_demo__")) return null;
  // 切换会话卸载旧查询和输入状态，避免旧请求回填到新会话。
  return <SessionBackgroundTasks key={sessionId} sessionId={sessionId} />;
}
