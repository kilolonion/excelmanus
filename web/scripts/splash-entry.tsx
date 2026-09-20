import { renderToString } from "react-dom/server";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { SPLASH_CRITICAL_CSS } from "@/components/ui/splash-critical";
import {
  PROGRESS_FROM,
  PROGRESS_MS,
  PROGRESS_TO,
} from "@/components/ui/loading-visual";

export const markup = renderToString(<LoadingScreen />);
export const css = SPLASH_CRITICAL_CSS;
export const progress = { from: PROGRESS_FROM, to: PROGRESS_TO, ms: PROGRESS_MS };
