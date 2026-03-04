import { useState, useEffect, useRef, useCallback } from "react";
import Header from "./components/Header";
import StepsBar from "./components/StepsBar";
import EnvCheck from "./components/EnvCheck";
import DeployPanel from "./components/DeployPanel";
import QuickStartOverlay from "./components/QuickStartOverlay";
import BackgroundOrbs from "./components/BackgroundOrbs";
import { api } from "./api";
import { LogoIcon } from "./components/Icons";
import type {
  StatusResponse,
  ConfigResponse,
  LogEntry,
  EnvCheck as EnvCheckType,
  EnvDetails,
} from "./types";

export default function App() {
  const [step, setStep] = useState(1);
  const [checks, setChecks] = useState<EnvCheckType>({
    python: 0,
    node: 0,
    git: 0,
  });
  const [details, setDetails] = useState<EnvDetails>({});
  const [progress, setProgress] = useState(0);
  const [running, setRunning] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [config, setConfig] = useState<ConfigResponse>({
    bePort: "8000",
    fePort: "3000",
    quickStart: false,
  });
  const [quickStartMode, setQuickStartMode] = useState(false);

  const logSince = useRef(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load config on mount
  useEffect(() => {
    api
      .getConfig()
      .then((c) => {
        setConfig(c);
        if (c.quickStart) setQuickStartMode(true);
      })
      .catch(() => {});

    // Trigger env check immediately
    api.checkEnv().catch(() => {});
  }, []);

  // Poll status + logs
  const poll = useCallback(async () => {
    try {
      const [status, logRes] = await Promise.all([
        api.getStatus(),
        api.getLogs(logSince.current),
      ]);
      setChecks(status.checks);
      setDetails(status.details);
      setProgress(status.progress);
      setRunning(status.running);
      setDeploying(status.deploying);
      setDeployError(status.deploy_error ?? null);
      if (logRes.logs && logRes.logs.length > 0) {
        setLogs((prev) => {
          const merged = [...prev, ...logRes.logs];
          // Keep last 500 entries
          return merged.slice(-500);
        });
        logSince.current =
          logRes.logs[logRes.logs.length - 1].idx + 1;
      }
    } catch {}
  }, []);

  useEffect(() => {
    poll(); // initial
    pollRef.current = setInterval(poll, 1500);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [poll]);

  const recheck = () => {
    setChecks({ python: 0, node: 0, git: 0 });
    setDetails({});
    api.checkEnv().catch(() => {});
  };

  if (quickStartMode) {
    return (
      <QuickStartOverlay
        onCancel={() => setQuickStartMode(false)}
        onStarted={() => {}}
        progress={progress}
        running={running}
        fePort={config.fePort}
        deployError={deployError}
      />
    );
  }

  return (
    <div className="noise-overlay dot-grid relative min-h-screen overflow-hidden">
      <BackgroundOrbs />
      <div className="relative z-[1] flex min-h-screen flex-col">
        <Header />
        <main className="mx-auto w-full max-w-[520px] flex-1 px-5 pb-10 pt-1">
          <StepsBar current={step} />
          <div className="mt-5">
            {step === 1 && (
              <EnvCheck
                checks={checks}
                details={details}
                onRecheck={recheck}
                onNext={() => setStep(2)}
              />
            )}
            {step === 2 && (
              <DeployPanel
                progress={progress}
                running={running}
                logs={logs}
                fePort={config.fePort}
                deployError={deployError}
                onBack={() => setStep(1)}
              />
            )}
          </div>
        </main>
        <footer className="relative py-4 text-center">
          <div className="pointer-events-none absolute left-1/2 top-0 h-[1px] w-32 -translate-x-1/2 bg-gradient-to-r from-transparent via-em-border to-transparent" />
          <div className="flex items-center justify-center gap-1.5 text-[10px] text-em-t4">
            <LogoIcon size={14} className="opacity-40" />
            <span>ExcelManus · 开源 AI Excel 助手</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
