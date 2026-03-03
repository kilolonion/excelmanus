"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link2, Loader2, RefreshCw, ArrowLeftRight } from "lucide-react";
import { useExcelStore, type RelationshipDiscovery } from "@/stores/excel-store";

interface FileRelationshipGraphProps {
  onClickFile?: (path: string) => void;
}

function overlapBg(ratio: number): string {
  if (ratio >= 0.8) return "stroke-green-500 dark:stroke-green-400";
  if (ratio >= 0.5) return "stroke-amber-500 dark:stroke-amber-400";
  return "stroke-muted-foreground/40";
}

function overlapText(ratio: number): string {
  if (ratio >= 0.8) return "text-green-600 dark:text-green-400";
  if (ratio >= 0.5) return "text-amber-600 dark:text-amber-400";
  return "text-muted-foreground";
}

interface NodeLayout {
  path: string;
  name: string;
  x: number;
  y: number;
}

function layoutNodes(files: string[]): NodeLayout[] {
  const count = files.length;
  if (count === 0) return [];

  const centerX = 140;
  const centerY = 80;
  const radiusX = 100;
  const radiusY = 55;

  if (count === 1) {
    return [{ path: files[0], name: files[0].split("/").pop() || files[0], x: centerX, y: centerY }];
  }
  if (count === 2) {
    return [
      { path: files[0], name: files[0].split("/").pop() || files[0], x: centerX - 80, y: centerY },
      { path: files[1], name: files[1].split("/").pop() || files[1], x: centerX + 80, y: centerY },
    ];
  }

  return files.map((f, i) => {
    const angle = (2 * Math.PI * i) / count - Math.PI / 2;
    return {
      path: f,
      name: f.split("/").pop() || f,
      x: centerX + radiusX * Math.cos(angle),
      y: centerY + radiusY * Math.sin(angle),
    };
  });
}

export function FileRelationshipGraph({ onClickFile }: FileRelationshipGraphProps) {
  const relationships = useExcelStore((s) => s.workspaceRelationships);
  const loading = useExcelStore((s) => s.workspaceRelationshipsLoading);
  const fetchRels = useExcelStore((s) => s.fetchWorkspaceRelationships);

  const [hoveredEdge, setHoveredEdge] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!relationships && !loading) {
      fetchRels();
    }
  }, [relationships, loading, fetchRels]);

  const handleRefresh = useCallback(() => {
    fetchRels();
  }, [fetchRels]);

  const data = relationships as RelationshipDiscovery | null;
  const pairs = useMemo(() => data?.file_pairs ?? [], [data]);

  const allFiles = useMemo(() => {
    const set = new Set<string>();
    for (const p of pairs) {
      set.add(p.file_a);
      set.add(p.file_b);
    }
    return Array.from(set);
  }, [pairs]);

  const nodes = useMemo(() => layoutNodes(allFiles), [allFiles]);
  const nodeMap = useMemo(() => {
    const m = new Map<string, NodeLayout>();
    for (const n of nodes) m.set(n.path, n);
    return m;
  }, [nodes]);

  const openCompare = useExcelStore((s) => s.openCompare);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4 gap-2 text-muted-foreground/60 text-[11px]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        分析文件关系...
      </div>
    );
  }

  if (!data || pairs.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <Link2 className="h-5 w-5 text-muted-foreground/30" />
        <span className="text-[11px] text-muted-foreground/50">
          {data ? "未发现文件间的列关联" : "加载文件关系失败"}
        </span>
        <button
          onClick={handleRefresh}
          className="text-[10px] text-muted-foreground/60 hover:text-foreground flex items-center gap-1 transition-colors"
        >
          <RefreshCw className="h-3 w-3" />
          重新分析
        </button>
      </div>
    );
  }

  const svgWidth = 280;
  const svgHeight = 160;

  return (
    <div className="relative">
      {/* 标题栏 */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] text-muted-foreground">
        <Link2 className="h-3 w-3" />
        <span className="font-medium">文件关系</span>
        <span className="text-muted-foreground/50">({pairs.length} 对关联)</span>
        <div className="flex-1" />
        <button
          onClick={handleRefresh}
          className="p-0.5 rounded hover:bg-muted transition-colors"
          title="重新分析"
        >
          <RefreshCw className="h-3 w-3" />
        </button>
      </div>

      {/* SVG 画布 */}
      <div className="px-2 pb-2">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${svgWidth} ${svgHeight}`}
          className="w-full"
          style={{ maxHeight: 180 }}
        >
          {/* 连线 */}
          {pairs.map((pair, i) => {
            const a = nodeMap.get(pair.file_a);
            const b = nodeMap.get(pair.file_b);
            if (!a || !b) return null;
            const bestCol = pair.shared_columns?.[0];
            const ratio = bestCol?.overlap_ratio ?? 0;
            const isHovered = hoveredEdge === i;
            return (
              <g
                key={i}
                onMouseEnter={() => setHoveredEdge(i)}
                onMouseLeave={() => setHoveredEdge(null)}
                onDoubleClick={() => openCompare(pair.file_a, pair.file_b)}
                className="cursor-pointer"
              >
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  className={`${overlapBg(ratio)} ${isHovered ? "opacity-100" : "opacity-60"}`}
                  strokeWidth={isHovered ? 2.5 : 1.5}
                  strokeDasharray={ratio < 0.5 ? "4 3" : undefined}
                />
                {/* 连线标注 */}
                <text
                  x={(a.x + b.x) / 2}
                  y={(a.y + b.y) / 2 - 5}
                  textAnchor="middle"
                  className={`text-[7px] font-medium ${isHovered ? "fill-foreground" : "fill-muted-foreground/70"}`}
                >
                  {bestCol ? (bestCol.col_a === bestCol.col_b ? bestCol.col_a : `${bestCol.col_a}↔${bestCol.col_b}`) : ""}
                </text>
                <text
                  x={(a.x + b.x) / 2}
                  y={(a.y + b.y) / 2 + 5}
                  textAnchor="middle"
                  className={`text-[6px] ${overlapText(ratio)}`}
                >
                  {ratio > 0 ? `${(ratio * 100).toFixed(0)}%` : ""}
                  {pair.shared_columns.length > 1 ? ` +${pair.shared_columns.length - 1}` : ""}
                </text>
              </g>
            );
          })}

          {/* 文件节点 */}
          {nodes.map((node) => (
            <g
              key={node.path}
              onClick={() => onClickFile?.(node.path)}
              className="cursor-pointer"
            >
              <rect
                x={node.x - 36}
                y={node.y - 12}
                width={72}
                height={24}
                rx={5}
                className="fill-background stroke-border hover:stroke-[var(--em-primary)] transition-colors"
                strokeWidth={1}
              />
              <text
                x={node.x}
                y={node.y + 3}
                textAnchor="middle"
                className="text-[7px] fill-foreground/80 font-medium"
              >
                {node.name.length > 12 ? node.name.slice(0, 10) + "…" : node.name}
              </text>
            </g>
          ))}
        </svg>
      </div>

      {/* 悬停详情 */}
      {hoveredEdge !== null && pairs[hoveredEdge] && (
        <div className="absolute left-2 right-2 bottom-0 bg-background/95 border border-border rounded-lg px-2.5 py-1.5 text-[10px] shadow-sm z-10 backdrop-blur-sm">
          <div className="flex items-center gap-1.5 mb-1">
            <ArrowLeftRight className="h-3 w-3 text-muted-foreground" />
            <span className="font-medium text-foreground/80">
              {pairs[hoveredEdge].file_a.split("/").pop()} ↔ {pairs[hoveredEdge].file_b.split("/").pop()}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {pairs[hoveredEdge].shared_columns.slice(0, 4).map((col, j) => (
              <span key={j} className="inline-flex items-center gap-0.5 px-1.5 py-px rounded-full bg-muted text-muted-foreground">
                <span className="font-mono">{col.col_a === col.col_b ? col.col_a : `${col.col_a}↔${col.col_b}`}</span>
                <span className={overlapText(col.overlap_ratio)}>{(col.overlap_ratio * 100).toFixed(0)}%</span>
              </span>
            ))}
          </div>
          <div className="text-[9px] text-muted-foreground/50 mt-1">双击连线打开对比视图</div>
        </div>
      )}
    </div>
  );
}
