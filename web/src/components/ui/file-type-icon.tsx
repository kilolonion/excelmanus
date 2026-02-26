import { type SVGProps } from "react";

interface FileTypeIconProps extends SVGProps<SVGSVGElement> {
  filename: string;
}

const EXT_MAP: Record<string, { color: string; label: string }> = {
  // 电子表格
  xlsx: { color: "#21a366", label: "XL" },
  xls:  { color: "#21a366", label: "XL" },
  csv:  { color: "#21a366", label: "CV" },

  // Python 相关
  py:   { color: "#3572a5", label: "PY" },
  pyi:  { color: "#3572a5", label: "PY" },
  ipynb:{ color: "#f37626", label: "NB" },

  // JavaScript / TypeScript 相关
  js:   { color: "#f1e05a", label: "JS" },
  jsx:  { color: "#61dafb", label: "JX" },
  ts:   { color: "#3178c6", label: "TS" },
  tsx:  { color: "#3178c6", label: "TX" },
  mjs:  { color: "#f1e05a", label: "MJ" },
  cjs:  { color: "#f1e05a", label: "CJ" },

  // 前端 / Web
  html: { color: "#e34c26", label: "HT" },
  css:  { color: "#563d7c", label: "CS" },
  scss: { color: "#c6538c", label: "SC" },
  less: { color: "#1d365d", label: "LE" },
  svg:  { color: "#ffb13b", label: "SV" },

  // 数据 / 配置
  json: { color: "#a4cc82", label: "{}" },
  yaml: { color: "#cb171e", label: "YA" },
  yml:  { color: "#cb171e", label: "YM" },
  toml: { color: "#9c4121", label: "TL" },
  xml:  { color: "#0060ac", label: "XM" },
  ini:  { color: "#8b8b8b", label: "IN" },
  env:  { color: "#ecd53f", label: ".E" },

  // 文档
  md:   { color: "#519aba", label: "MD" },
  mdx:  { color: "#519aba", label: "MX" },
  txt:  { color: "#8b8b8b", label: "TX" },
  rst:  { color: "#8b8b8b", label: "RS" },
  pdf:  { color: "#e34726", label: "PD" },
  doc:  { color: "#2b579a", label: "DC" },
  docx: { color: "#2b579a", label: "DC" },

  // 图片
  png:  { color: "#a074c4", label: "PN" },
  jpg:  { color: "#a074c4", label: "JP" },
  jpeg: { color: "#a074c4", label: "JP" },
  gif:  { color: "#a074c4", label: "GF" },
  webp: { color: "#a074c4", label: "WP" },
  ico:  { color: "#a074c4", label: "IC" },

  // Shell / 系统
  sh:   { color: "#89e051", label: "SH" },
  bash: { color: "#89e051", label: "SH" },
  zsh:  { color: "#89e051", label: "SH" },
  bat:  { color: "#c1f12e", label: "BA" },
  ps1:  { color: "#012456", label: "PS" },
  dockerfile: { color: "#384d54", label: "DK" },

  // Rust / Go / C / C++ / Java 等
  rs:   { color: "#dea584", label: "RS" },
  go:   { color: "#00add8", label: "GO" },
  c:    { color: "#555555", label: "C" },
  cpp:  { color: "#f34b7d", label: "C+" },
  h:    { color: "#555555", label: "H" },
  java: { color: "#b07219", label: "JA" },
  kt:   { color: "#a97bff", label: "KT" },
  swift:{ color: "#f05138", label: "SW" },
  rb:   { color: "#701516", label: "RB" },
  php:  { color: "#4f5d95", label: "PH" },
  lua:  { color: "#000080", label: "LU" },
  sql:  { color: "#e38c00", label: "SQ" },

  // 锁文件 / 配置
  lock: { color: "#8b8b8b", label: "LK" },
  cfg:  { color: "#8b8b8b", label: "CF" },
};

const FALLBACK = { color: "#8b8b8b", label: "" };

function getExtInfo(filename: string): { color: string; label: string } {
  const lower = filename.toLowerCase();

  if (lower === "dockerfile" || lower.startsWith("dockerfile."))
    return EXT_MAP.dockerfile;
  if (lower === ".env" || lower.startsWith(".env."))
    return EXT_MAP.env;

  const dotIdx = lower.lastIndexOf(".");
  if (dotIdx === -1) return FALLBACK;
  const ext = lower.slice(dotIdx + 1);
  return EXT_MAP[ext] ?? FALLBACK;
}

export function FileTypeIcon({ filename, className, ...props }: FileTypeIconProps) {
  const { color, label } = getExtInfo(filename);

  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      {/* File body */}
      <path
        d="M3 1.5A.5.5 0 013.5 1h6l3.5 3.5V14a.5.5 0 01-.5.5H3.5A.5.5 0 013 14V1.5z"
        fill={color}
        opacity={0.15}
        stroke={color}
        strokeWidth={0.8}
      />
      {/* Fold corner */}
      <path
        d="M9.5 1v3a.5.5 0 00.5.5h3"
        stroke={color}
        strokeWidth={0.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Extension label */}
      {label && (
        <text
          x="8"
          y="12"
          textAnchor="middle"
          fontSize="5"
          fontWeight="700"
          fontFamily="ui-monospace, monospace"
          fill={color}
        >
          {label}
        </text>
      )}
    </svg>
  );
}

const EXCEL_EXTS = new Set(["xlsx", "xls", "csv"]);

export function isExcelFile(filename: string): boolean {
  const dotIdx = filename.lastIndexOf(".");
  if (dotIdx === -1) return false;
  return EXCEL_EXTS.has(filename.slice(dotIdx + 1).toLowerCase());
}
