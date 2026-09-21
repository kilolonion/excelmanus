import { getSplashFeedback } from "@/components/ui/splash-feedback";

// The desktop file has no React runtime. Keep feedback alive while its local
// services start; animation itself is entirely CSS and needs no JS frames.
const w = window as Window & { __emSplashStartedAt?: number; __emSplashTimer?: number };
const root = document.querySelector(".em-splash");
const elapsed = root?.querySelector(".em-splash-elapsed");
const hint = root?.querySelector(".em-splash-hint");
const status = root?.querySelector(".em-splash-status-text");
// The SSR markup ships an inline ticker with the same start timestamp;
// replace it so a single interval drives the elapsed text.
if (w.__emSplashTimer) window.clearInterval(w.__emSplashTimer);
const startedAt = w.__emSplashStartedAt ?? (w.__emSplashStartedAt = Date.now());

(window as Window & { __emSplashSetStatus?: (message: string) => void }).__emSplashSetStatus = (message) => {
  if (status) status.textContent = message;
};

const timer = window.setInterval(() => {
  const feedback = getSplashFeedback((Date.now() - startedAt) / 1000, true);
  if (elapsed) elapsed.textContent = feedback.elapsed;
  if (hint && hint.textContent !== feedback.hint) hint.textContent = feedback.hint;
}, 1000);
window.addEventListener("pagehide", () => window.clearInterval(timer), { once: true });
