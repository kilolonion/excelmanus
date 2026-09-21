export const SPLASH_SLOW_SECONDS = 15;
export const SPLASH_RECOVERY_SECONDS = 30;
export const SPLASH_HINT_READY = "准备完成后会自动进入。";
export const SPLASH_HINT_SLOW = "准备时间比平时稍长，完成后会自动进入。";
export const SPLASH_HINT_RECOVERY_DESKTOP = "启动时间较长，可在「帮助」菜单中查看启动日志。";
export const SPLASH_HINT_RECOVERY_WEB = "等待时间较长，你可以继续等待，或重新加载页面。";

export function getSplashFeedback(elapsedSeconds: number, desktop = false) {
  const seconds = Math.max(0, Math.floor(elapsedSeconds));
  const elapsed = seconds < 60
    ? `已等待 ${seconds} 秒`
    : `已等待 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hint = seconds >= SPLASH_RECOVERY_SECONDS
    ? desktop
      ? SPLASH_HINT_RECOVERY_DESKTOP
      : SPLASH_HINT_RECOVERY_WEB
    : seconds >= SPLASH_SLOW_SECONDS
      ? SPLASH_HINT_SLOW
      : SPLASH_HINT_READY;
  return { elapsed, hint };
}

/**
 * 生成随 SSR HTML 一起下发的内联脚本源码：等待页在 React 水合之前
 * （或水合失败时）也要持续更新等待时长与提示语，否则用户看到的
 * 「已等待 0 秒」会一直冻结。脚本与 LoadingScreen 的 useEffect 以及
 * 桌面端 splash-runtime 通过 window.__emSplashStartedAt /
 * window.__emSplashTimer 共享同一个起点和定时器，水合后由 React 接管。
 */
export function splashPreHydrationScript(desktop: boolean): string {
  const hints = {
    ready: SPLASH_HINT_READY,
    slow: SPLASH_HINT_SLOW,
    recovery: desktop ? SPLASH_HINT_RECOVERY_DESKTOP : SPLASH_HINT_RECOVERY_WEB,
  };
  return `(function(){var w=window;var t=w.__emSplashStartedAt||(w.__emSplashStartedAt=Date.now());var H=${JSON.stringify(hints)};w.__emSplashTimer=w.setInterval(function(){var r=document.querySelector(".em-splash");if(!r)return;var s=Math.max(0,Math.floor((Date.now()-t)/1000));var e=r.querySelector(".em-splash-elapsed");if(e)e.textContent=s<60?"已等待 "+s+" 秒":"已等待 "+Math.floor(s/60)+" 分 "+s%60+" 秒";var h=r.querySelector(".em-splash-hint");if(h){var n=s>=${SPLASH_RECOVERY_SECONDS}?H.recovery:s>=${SPLASH_SLOW_SECONDS}?H.slow:H.ready;if(h.textContent!==n)h.textContent=n}},1000);w.addEventListener("pagehide",function(){w.clearInterval(w.__emSplashTimer)},{once:true})})();`;
}
