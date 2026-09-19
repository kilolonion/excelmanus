/**
 * 等待页首屏关键 CSS。必须内联进 layout <head>，不能进 Tailwind 大包：
 * 开发态编译 globals.css 之前浏览器就会画出 LoadingScreen HTML，
 * SVG 在自定义属性未定义时填充为黑，看起来像两块黑板。
 */
export const SPLASH_CRITICAL_CSS = `
.em-splash{
  --em-primary:#0b6b4f;
  --em-hairline:#dce7e0;
  --em-text:#18241e;
  --em-text-secondary:#66756c;
  --card:#fff;
  position:relative;
  display:flex;
  flex-direction:column;
  height:100vh;
  height:100dvh;
  min-height:100vh;
  min-height:100dvh;
  overflow:hidden;
  background:#fff;
  color:#18241e;
  user-select:none;
  font-family:ui-sans-serif,system-ui,sans-serif;
}
.em-splash-glow{
  pointer-events:none;
  position:absolute;
  inset:0;
}
.em-splash-glow::before{
  content:"";
  position:absolute;
  left:50%;
  top:42%;
  width:min(72vw,520px);
  height:min(72vw,520px);
  transform:translate(-50%,-50%);
  border-radius:999px;
  background:#0b6b4f;
  opacity:.045;
  filter:blur(90px);
}
.em-splash-header{
  position:relative;
  z-index:10;
  display:flex;
  flex-wrap:nowrap;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  padding:max(1.25rem,env(safe-area-inset-top)) 16px 0;
}
.em-splash-wordmark{
  display:inline-flex;
  align-items:center;
  gap:8px;
}
.em-splash-wordmark img{
  width:28px;
  height:28px;
  object-fit:contain;
}
.em-splash-wordmark-text{
  font-size:17px;
  font-weight:600;
  letter-spacing:-.02em;
  color:#0b6b4f;
}
.em-splash-tag{
  display:none;
  white-space:nowrap;
  font-size:13px;
  letter-spacing:.02em;
  color:#66756c;
}
.em-splash-main{
  position:relative;
  z-index:10;
  flex:1;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  padding:0 20px;
  text-align:center;
}
.em-splash-mark{
  position:relative;
  width:272px;
  height:214px;
}
.em-splash-mark-glow{
  pointer-events:none;
  position:absolute;
  left:50%;
  top:48%;
  width:250px;
  height:250px;
  transform:translate(-50%,-50%);
  border-radius:999px;
  background:#0b6b4f;
  opacity:.08;
  filter:blur(58px);
}
.em-splash-sheet{
  position:absolute;
  width:128px;
  opacity:.9;
  filter:drop-shadow(0 16px 32px rgba(11,107,79,.18));
}
.em-splash-sheet svg{
  display:block;
  width:100%;
  height:auto;
}
.em-splash-sheet-left{
  left:8px;
  top:58px;
  transform:rotate(-16deg);
}
.em-splash-sheet-right{
  right:4px;
  top:34px;
  transform:rotate(13deg);
}
.em-splash-logo-wrap{
  position:absolute;
  left:50%;
  top:48%;
  width:148px;
  height:148px;
  transform:translate(-50%,-50%);
  display:flex;
  align-items:center;
  justify-content:center;
}
.em-splash-arc{
  position:absolute;
  inset:0;
  animation:em-splash-arc-spin 10s linear infinite;
  transform-origin:50% 50%;
}
.em-splash-logo{
  position:relative;
  z-index:10;
  width:92px;
  height:92px;
  display:flex;
  align-items:center;
  justify-content:center;
  overflow:hidden;
  border-radius:999px;
  background:#fff;
  box-shadow:0 10px 28px rgba(11,107,79,.16),0 0 0 1px rgba(11,107,79,.1);
}
.em-splash-logo img{
  width:72%;
  height:72%;
  object-fit:contain;
}
.em-splash-title{
  margin:28px 0 0;
  white-space:nowrap;
  font-size:1.5rem;
  font-weight:600;
  line-height:1;
  letter-spacing:-.02em;
  color:#18241e;
}
.em-splash-subtitle{
  margin:10px 0 0;
  white-space:nowrap;
  font-size:13px;
  line-height:1;
  color:#66756c;
}
.em-splash-status{
  margin:32px 0 0;
  display:flex;
  flex-wrap:nowrap;
  align-items:center;
  gap:8px;
  white-space:nowrap;
  font-size:13px;
  color:#66756c;
}
.em-splash-spinner{
  flex-shrink:0;
  width:14px;
  height:14px;
  border-radius:999px;
  border:2px solid rgba(11,107,79,.15);
  border-top-color:#0b6b4f;
  animation:em-splash-status-spin .8s linear infinite;
}
.em-splash-progress{
  margin:14px 0 0;
  width:min(70vw,220px);
  height:5px;
  overflow:hidden;
  border-radius:999px;
  background:#dce7e0;
}
.em-splash-progress-fill{
  height:100%;
  border-radius:999px;
  background:#0b6b4f;
}
.em-splash-footer{
  position:relative;
  z-index:10;
  display:flex;
  justify-content:center;
  overflow:hidden;
  padding:0 12px max(1.75rem,env(safe-area-inset-bottom));
}
.em-splash-tip{
  display:flex;
  max-width:100%;
  align-items:center;
  gap:6px;
  font-size:11px;
  line-height:1;
  color:#66756c;
}
.em-splash-tip svg{
  flex-shrink:0;
  width:14px;
  height:14px;
  color:#0b6b4f;
}
.em-splash-tip span{
  white-space:nowrap;
}
@keyframes em-splash-arc-spin{
  from{transform:rotate(0deg)}
  to{transform:rotate(360deg)}
}
@keyframes em-splash-status-spin{
  from{transform:rotate(0deg)}
  to{transform:rotate(360deg)}
}
@media (min-width:768px){
  .em-splash-glow::before{
    top:50%;
    width:560px;
    height:560px;
  }
  .em-splash-header{
    padding:32px 40px 0;
  }
  .em-splash-tag{display:inline}
  .em-splash-main{padding:0 64px}
  .em-splash-mark{
    width:308px;
    height:236px;
  }
  .em-splash-mark-glow{
    width:300px;
    height:300px;
  }
  .em-splash-sheet{width:140px}
  .em-splash-sheet-left{
    left:16px;
    top:62px;
  }
  .em-splash-sheet-right{
    right:8px;
    top:30px;
  }
  .em-splash-logo-wrap{
    width:162px;
    height:162px;
  }
  .em-splash-logo{
    width:102px;
    height:102px;
  }
  .em-splash-title{
    margin-top:36px;
    font-size:2rem;
  }
  .em-splash-subtitle{font-size:15px}
  .em-splash-status{margin-top:36px}
  .em-splash-progress{width:168px}
  .em-splash-footer{padding:0 40px 40px}
  .em-splash-tip{
    gap:8px;
    font-size:13px;
  }
  .em-splash-tip svg{
    width:16px;
    height:16px;
  }
}
@media (prefers-reduced-motion:reduce){
  .em-splash-arc,.em-splash-spinner{animation:none}
}
`.trim();
