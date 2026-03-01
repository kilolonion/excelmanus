export interface EnvCheck {
  python: number; // 0=unknown, 1=ok, 2=fail, 3=checking
  node: number;
  git: number;
  [key: string]: number;
}

export interface EnvDetails {
  python?: string;
  node?: string;
  git?: string;
  [key: string]: string | undefined;
}

export interface StatusResponse {
  running: boolean;
  deploying: boolean;
  progress: number;
  checks: EnvCheck;
  details: EnvDetails;
}

export interface ConfigResponse {
  bePort: string;
  fePort: string;
  quickStart: boolean;
}

export interface LogEntry {
  text: string;
  level: string;
  idx: number;
}

export interface UpdateInfo {
  has_update: boolean;
  current?: string;
  latest?: string;
  behind?: number;
  timeout?: boolean;
  error?: string;
}

export interface UpdateResult {
  success: boolean;
  old_version?: string;
  new_version?: string;
  error?: string;
}

export interface EnvItem {
  id: string;
  name: string;
  icon: string;
  downloadUrl: string;
}

export const ENV_ITEMS: EnvItem[] = [
  {
    id: "python",
    name: "Python 3.x",
    icon: "🐍",
    downloadUrl: "https://www.python.org/downloads/",
  },
  {
    id: "node",
    name: "Node.js",
    icon: "⚡",
    downloadUrl: "https://nodejs.org/zh-cn/download/",
  },
  {
    id: "git",
    name: "Git",
    icon: "📦",
    downloadUrl: "https://git-scm.com/download/win",
  },
];
