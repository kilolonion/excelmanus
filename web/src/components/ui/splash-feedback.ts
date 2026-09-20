export const SPLASH_SLOW_SECONDS = 15;
export const SPLASH_RECOVERY_SECONDS = 30;

export function getSplashFeedback(elapsedSeconds: number, desktop = false) {
  const seconds = Math.max(0, Math.floor(elapsedSeconds));
  const elapsed = seconds < 60
    ? `已等待 ${seconds} 秒`
    : `已等待 ${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hint = seconds >= SPLASH_RECOVERY_SECONDS
    ? desktop
      ? "启动时间较长，可在「帮助」菜单中查看启动日志。"
      : "等待时间较长，你可以继续等待，或重新加载页面。"
    : seconds >= SPLASH_SLOW_SECONDS
      ? "准备时间比平时稍长，完成后会自动进入。"
      : "准备完成后会自动进入。";
  return { elapsed, hint };
}
