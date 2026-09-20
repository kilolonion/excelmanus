import { renderToString } from "react-dom/server";
import { LoadingScreen } from "@/components/ui/LoadingScreen";
import { SPLASH_CRITICAL_CSS } from "@/components/ui/splash-critical";

export const markup = renderToString(<LoadingScreen desktop />);
export const css = SPLASH_CRITICAL_CSS;
