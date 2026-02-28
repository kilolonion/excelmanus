"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Save,
  X,
  Loader2,
  RefreshCw,
  Zap,
  ChevronDown,
  ChevronRight,
  Terminal,
  Globe,
  Radio,
  CheckCircle2,
  XCircle,
  Circle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { settingsCache } from "@/lib/settings-cache";

interface MCPServer {
  name: string;
  config: Record<string, unknown>;
  status: string;
  transport: string;
  tool_count: number;
  tools: string[];
  last_error: string | null;
  auto_approve: string[];
}

interface ServerFormData {
  name: string;
  transport: "stdio" | "sse" | "streamable_http";
  command: string;
  args: string;
  env: string;
  url: string;
  headers: string;
  timeout: number;
  autoApprove: string;
}

const EMPTY_FORM: ServerFormData = {
  name: "",
  transport: "stdio",
  command: "",
  args: "",
  env: "",
  url: "",
  headers: "",
  timeout: 30,
  autoApprove: "",
};

const TRANSPORT_ICONS: Record<string, React.ReactNode> = {
  stdio: <Terminal className="h-3.5 w-3.5" />,
  sse: <Globe className="h-3.5 w-3.5" />,
  streamable_http: <Radio className="h-3.5 w-3.5" />,
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  ready: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />,
  connect_failed: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  discover_failed: <XCircle className="h-3.5 w-3.5 text-orange-500" />,
  not_connected: <Circle className="h-3.5 w-3.5 text-red-400" />,
};

const STATUS_LABELS: Record<string, string> = {
  ready: "已连接",
  connect_failed: "连接失败",
  discover_failed: "发现失败",
  not_connected: "未连接",
};

const STATUS_COLORS: Record<string, string> = {
  ready: "text-green-600 dark:text-green-400",
  connect_failed: "text-red-600 dark:text-red-400",
  discover_failed: "text-orange-600 dark:text-orange-400",
  not_connected: "text-red-500 dark:text-red-400",
};

export function MCPTab() {
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [configPath, setConfigPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedServer, setExpandedServer] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editingServer, setEditingServer] = useState<string | null>(null);
  const [createMode, setCreateMode] = useState<"form" | "json">("form");
  const [formDraft, setFormDraft] = useState<ServerFormData>({ ...EMPTY_FORM });
  const [jsonDraft, setJsonDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const fetchServers = useCallback(async (force = false) => {
    if (!force) {
      const cached = settingsCache.get<{ servers: MCPServer[]; config_path: string }>("/mcp/servers");
      if (cached) { setServers(cached.servers); setConfigPath(cached.config_path); return; }
    }
    setLoading(true);
    try {
      const data = await apiGet<{ servers: MCPServer[]; config_path: string }>("/mcp/servers");
      settingsCache.set("/mcp/servers", data);
      setServers(data.servers);
      setConfigPath(data.config_path);
    } catch {
      // 后端未就绪
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  const handleReload = async () => {
    setReloading(true);
    try {
      await apiPost("/mcp/reload", {});
      await fetchServers(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "重载失败");
    } finally {
      setReloading(false);
    }
  };

  const handleTest = async (name: string) => {
    setTesting(name);
    setTestResult((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    try {
      const res = await apiPost<{ status: string; tool_count?: number; tools?: string[]; error?: string }>(
        `/mcp/servers/${encodeURIComponent(name)}/test`,
        {}
      );
      if (res.status === "ok") {
        setTestResult((prev) => ({
          ...prev,
          [name]: { ok: true, msg: `连接成功，发现 ${res.tool_count} 个工具，正在同步...` },
        }));
        // 测试成功后自动热重载，使连接状态生效
        try {
          await apiPost("/mcp/reload", {});
          await fetchServers(true);
          setTestResult((prev) => ({
            ...prev,
            [name]: { ok: true, msg: `已连接，${res.tool_count} 个工具就绪` },
          }));
        } catch {
          // 重载失败不影响测试结果展示
        }
      } else {
        setTestResult((prev) => ({
          ...prev,
          [name]: { ok: false, msg: res.error || "连接失败" },
        }));
      }
    } catch (err) {
      setTestResult((prev) => ({
        ...prev,
        [name]: { ok: false, msg: err instanceof Error ? err.message : "测试失败" },
      }));
    } finally {
      setTesting(null);
    }
  };

  const formToRequest = (form: ServerFormData) => {
    const req: Record<string, unknown> = {
      name: form.name,
      transport: form.transport,
      timeout: form.timeout,
    };
    if (form.transport === "stdio") {
      req.command = form.command;
      req.args = form.args
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      if (form.env.trim()) {
        try {
          req.env = JSON.parse(form.env);
        } catch {
          req.env = {};
        }
      }
    } else {
      req.url = form.url;
      if (form.headers.trim()) {
        try {
          req.headers = JSON.parse(form.headers);
        } catch {
          req.headers = {};
        }
      }
    }
    if (form.autoApprove.trim()) {
      req.autoApprove = form.autoApprove
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return req;
  };

  const handleCreate = async () => {
    setSaving(true);
    try {
      if (createMode === "json") {
        const parsed = JSON.parse(jsonDraft);
        // 期望格式：{ "name": "...", ...config }
        const name = parsed.name;
        if (!name) throw new Error("JSON 中缺少 name 字段");
        delete parsed.name;
        await apiPost("/mcp/servers", { name, ...parsed });
      } else {
        const req = formToRequest(formDraft);
        await apiPost("/mcp/servers", req);
      }
      setShowCreate(false);
      setFormDraft({ ...EMPTY_FORM });
      setJsonDraft("");
      fetchServers(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "创建失败");
    } finally {
      setSaving(false);
    }
  };

  const handleUpdate = async (originalName: string) => {
    setSaving(true);
    try {
      if (createMode === "json") {
        const parsed = JSON.parse(jsonDraft);
        const newName = parsed.name || originalName;
        delete parsed.name;
        await apiPut(`/mcp/servers/${encodeURIComponent(originalName)}`, {
          name: newName,
          ...parsed,
        });
      } else {
        const req = formToRequest(formDraft);
        await apiPut(`/mcp/servers/${encodeURIComponent(originalName)}`, req);
      }
      setEditingServer(null);
      setFormDraft({ ...EMPTY_FORM });
      setJsonDraft("");
      fetchServers(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "更新失败");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`确定删除 MCP Server "${name}"？`)) return;
    try {
      await apiDelete(`/mcp/servers/${encodeURIComponent(name)}`);
      fetchServers(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  };

  const startEdit = (server: MCPServer) => {
    const cfg = server.config as Record<string, unknown>;
    setFormDraft({
      name: server.name,
      transport: server.transport as "stdio" | "sse" | "streamable_http",
      command: (cfg.command as string) || "",
      args: Array.isArray(cfg.args) ? (cfg.args as string[]).join("\n") : "",
      env: cfg.env ? JSON.stringify(cfg.env, null, 2) : "",
      url: (cfg.url as string) || "",
      headers: cfg.headers ? JSON.stringify(cfg.headers, null, 2) : "",
      timeout: (cfg.timeout as number) || 30,
      autoApprove: Array.isArray(cfg.autoApprove) ? (cfg.autoApprove as string[]).join(", ") : "",
    });
    setJsonDraft(JSON.stringify({ name: server.name, ...cfg }, null, 2));
    setEditingServer(server.name);
    setShowCreate(false);
    setCreateMode("form");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-muted-foreground truncate" title={configPath}>
            配置文件: {configPath ? configPath.split("/").slice(-2).join("/") : "mcp.json"}
          </p>
        </div>
        <div className="flex gap-1.5 flex-shrink-0">
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={handleReload}
            disabled={reloading}
          >
            {reloading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            热重载
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 text-xs gap-1"
            onClick={() => {
              setShowCreate(true);
              setEditingServer(null);
              setFormDraft({ ...EMPTY_FORM });
              setJsonDraft("");
              setCreateMode("form");
            }}
          >
            <Plus className="h-3 w-3" />
            新增
          </Button>
        </div>
      </div>

      {/* Create / Edit form */}
      {(showCreate || editingServer) && (
        <div className="rounded-lg border border-dashed border-border p-3 space-y-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium truncate min-w-0">
              {editingServer ? `编辑: ${editingServer}` : "新增 MCP Server"}
            </span>
            <div className="flex gap-1 shrink-0">
              <Button
                size="sm"
                variant={createMode === "form" ? "default" : "ghost"}
                className="h-7 sm:h-6 text-[11px] sm:text-[10px] px-2.5 sm:px-2"
                onClick={() => setCreateMode("form")}
              >
                表单
              </Button>
              <Button
                size="sm"
                variant={createMode === "json" ? "default" : "ghost"}
                className="h-7 sm:h-6 text-[11px] sm:text-[10px] px-2.5 sm:px-2"
                onClick={() => setCreateMode("json")}
              >
                JSON
              </Button>
            </div>
          </div>

          {createMode === "form" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-muted-foreground">名称 *</label>
                  <Input
                    value={formDraft.name}
                    onChange={(e) => setFormDraft((d) => ({ ...d, name: e.target.value }))}
                    className="h-8 sm:h-7 text-xs"
                    placeholder="server-name"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">传输类型</label>
                  <select
                    value={formDraft.transport}
                    onChange={(e) =>
                      setFormDraft((d) => ({
                        ...d,
                        transport: e.target.value as "stdio" | "sse" | "streamable_http",
                      }))
                    }
                    className="w-full h-9 sm:h-7 rounded-md border border-input bg-background px-2 text-xs"
                  >
                    <option value="stdio">stdio</option>
                    <option value="sse">SSE</option>
                    <option value="streamable_http">Streamable HTTP</option>
                  </select>
                </div>
              </div>

              {formDraft.transport === "stdio" ? (
                <>
                  <div>
                    <label className="text-xs text-muted-foreground">Command *</label>
                    <Input
                      value={formDraft.command}
                      onChange={(e) => setFormDraft((d) => ({ ...d, command: e.target.value }))}
                      className="h-7 text-xs font-mono"
                      placeholder="npx, uvx, node..."
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">Args（每行一个）</label>
                    <textarea
                      value={formDraft.args}
                      onChange={(e) => setFormDraft((d) => ({ ...d, args: e.target.value }))}
                      className="w-full h-16 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y"
                      placeholder={"-y\n@modelcontextprotocol/server-xxx"}
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">
                      Env（JSON 格式，可选）
                    </label>
                    <textarea
                      value={formDraft.env}
                      onChange={(e) => setFormDraft((d) => ({ ...d, env: e.target.value }))}
                      className="w-full h-12 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y"
                      placeholder='{"KEY": "$ENV_VAR"}'
                    />
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="text-xs text-muted-foreground">URL *</label>
                    <Input
                      value={formDraft.url}
                      onChange={(e) => setFormDraft((d) => ({ ...d, url: e.target.value }))}
                      className="h-7 text-xs font-mono"
                      placeholder="http://localhost:3000/sse"
                    />
                  </div>
                  <div>
                    <label className="text-xs text-muted-foreground">
                      Headers（JSON 格式，可选）
                    </label>
                    <textarea
                      value={formDraft.headers}
                      onChange={(e) => setFormDraft((d) => ({ ...d, headers: e.target.value }))}
                      className="w-full h-12 rounded-md border border-input bg-background px-3 py-1 text-xs font-mono resize-y"
                      placeholder='{"Authorization": "Bearer ..."}'
                    />
                  </div>
                </>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div>
                  <label className="text-xs text-muted-foreground">Timeout (秒)</label>
                  <Input
                    type="number"
                    value={formDraft.timeout}
                    onChange={(e) =>
                      setFormDraft((d) => ({ ...d, timeout: parseInt(e.target.value) || 30 }))
                    }
                    className="h-8 sm:h-7 text-xs"
                    min={1}
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    Auto Approve（逗号分隔，* 表示全部）
                  </label>
                  <Input
                    value={formDraft.autoApprove}
                    onChange={(e) => setFormDraft((d) => ({ ...d, autoApprove: e.target.value }))}
                    className="h-8 sm:h-7 text-xs font-mono"
                    placeholder="*, tool1, tool2"
                  />
                </div>
              </div>
            </div>
          ) : (
            <textarea
              value={jsonDraft}
              onChange={(e) => setJsonDraft(e.target.value)}
              className="w-full h-40 rounded-md border border-input bg-background px-3 py-2 text-xs font-mono resize-y"
              placeholder={`{
  "name": "my-server",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-xxx"]
}`}
            />
          )}

          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              className="h-7 text-xs gap-1"
              onClick={() => {
                setShowCreate(false);
                setEditingServer(null);
              }}
            >
              <X className="h-3 w-3" /> 取消
            </Button>
            <Button
              size="sm"
              className="h-7 text-xs gap-1 text-white"
              style={{ backgroundColor: "var(--em-primary)" }}
              disabled={saving || (createMode === "form" && !formDraft.name)}
              onClick={() =>
                editingServer ? handleUpdate(editingServer) : handleCreate()
              }
            >
              {saving ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Save className="h-3 w-3" />
              )}
              {editingServer ? "更新" : "创建"}
            </Button>
          </div>
        </div>
      )}

      {/* Servers list */}
      <div className="space-y-2">
          {servers.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-8">
              暂无 MCP Server 配置，点击"新增"添加
            </p>
          )}
          {servers.map((server) => (
            <div key={server.name} className="rounded-lg border border-border overflow-hidden">
              {/* Card header */}
              <div
                className="px-3 py-3 sm:py-2.5 cursor-pointer hover:bg-muted/50 active:bg-muted/60 transition-colors"
                onClick={() =>
                  setExpandedServer(expandedServer === server.name ? null : server.name)
                }
              >
                {/* Row 1: status dot + name + action buttons */}
                <div className="flex items-center gap-2">
                  {STATUS_ICONS[server.status] || STATUS_ICONS.not_connected}
                  <span className="text-sm font-medium truncate flex-1 min-w-0">{server.name}</span>
                  <div className="flex gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      disabled={testing === server.name}
                      onClick={() => handleTest(server.name)}
                      title="测试连接"
                    >
                      {testing === server.name ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Zap className="h-3 w-3" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => startEdit(server)}
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 text-destructive"
                      onClick={() => handleDelete(server.name)}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                {/* Row 2: transport + tool count + status */}
                <div className="flex items-center gap-1.5 mt-1 ml-[22px]">
                  <Badge variant="secondary" className="text-[10px] px-1.5 py-0 font-mono">
                    {TRANSPORT_ICONS[server.transport]}
                    <span className="ml-1">{server.transport}</span>
                  </Badge>
                  {server.tool_count > 0 && (
                    <Badge variant="outline" className="text-[10px] px-1 py-0">
                      {server.tool_count} 工具
                    </Badge>
                  )}
                  <span className={`text-[11px] ${STATUS_COLORS[server.status] || "text-muted-foreground"}`}>
                    {STATUS_LABELS[server.status] || server.status}
                  </span>
                </div>
              </div>

              {/* Test result banner */}
              {testResult[server.name] && (
                <div
                  className={`px-3 py-1.5 text-xs border-t ${
                    testResult[server.name].ok
                      ? "bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300"
                      : "bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300"
                  }`}
                >
                  {testResult[server.name].ok ? "✓ " : "✗ "}
                  {testResult[server.name].msg}
                </div>
              )}

              {/* Expanded detail */}
              {expandedServer === server.name && (
                <div className="border-t border-border px-3 py-2.5 space-y-2 bg-muted/30">
                  {server.last_error && (
                    <div className="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950 rounded p-2">
                      <span className="font-medium">错误: </span>
                      {server.last_error}
                    </div>
                  )}

                  {server.tools.length > 0 && (
                    <div>
                      <span className="text-[10px] font-medium text-muted-foreground">
                        已注册工具 ({server.tools.length})
                      </span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {server.tools.map((t) => (
                          <Badge key={t} variant="secondary" className="text-[10px] font-mono">
                            {t}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {server.auto_approve.length > 0 && (
                    <div>
                      <span className="text-[10px] font-medium text-muted-foreground">
                        自动批准白名单
                      </span>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {server.auto_approve.map((t) => (
                          <Badge key={t} variant="outline" className="text-[10px] font-mono">
                            {t}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Raw config preview */}
                  <div>
                    <span className="text-[10px] font-medium text-muted-foreground">
                      原始配置
                    </span>
                    <pre className="text-[11px] font-mono bg-background rounded p-2 max-h-32 overflow-auto whitespace-pre-wrap border mt-1">
                      {JSON.stringify(server.config, null, 2)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
