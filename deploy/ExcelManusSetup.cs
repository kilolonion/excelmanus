/*
 * ExcelManus - Web UI (Project-Style Light Theme)
 * C# exe with embedded HTTP server + browser-rendered HTML/CSS UI
 * Zero external dependencies - uses built-in .NET Framework
 * Compile: csc.exe /langversion:5 /target:winexe /out:ExcelManus.exe ExcelManusSetup.cs EmbeddedAssets.cs
 * Fallback (no Vite UI): csc.exe /langversion:5 /target:winexe /out:ExcelManus.exe ExcelManusSetup.cs
 */
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;
using System.Windows.Forms;

// ═══════════════════════════════════════════════════════════
//  Embedded HTML Page - Light theme, project brand colors
// ═══════════════════════════════════════════════════════════
public static class Html
{
    public static readonly string Page = @"<!DOCTYPE html>
<html lang='zh'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>ExcelManus</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,system-ui,sans-serif;overflow-y:auto}
:root{
  --g:#217346;--gl:#33a867;--gd:#1a5c38;
  --ga1:rgba(33,115,70,.06);--ga2:rgba(33,115,70,.12);--ga3:rgba(33,115,70,.18);
  --bg:#f5f5f7;--card:#fff;--brd:#e5e7eb;--brd2:#d1d5db;
  --red:#d13438;--redl:#e74c3c;--gold:#e5a100;--cyan:#0078d4;
  --t1:#1a1a1a;--t2:#4b5563;--t3:#9ca3af;--t4:#d1d5db;
  --r:12px;--r2:8px;
}
body{background:linear-gradient(145deg,#f5f7f8 0%,#eef2f0 50%,#f0f5f3 100%);color:var(--t1);display:flex;flex-direction:column;min-height:100%;position:relative}

.hdr{background:rgba(255,255,255,.78);backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);border-bottom:1px solid rgba(229,231,235,.7);padding:0 28px;height:56px;display:flex;align-items:center;gap:14px;flex-shrink:0;position:relative;z-index:10}
.hdr::after{content:'';position:absolute;bottom:-1px;left:0;width:140px;height:2px;background:linear-gradient(90deg,var(--g),var(--gl),transparent);border-radius:2px}
.logo{width:36px;height:36px;flex-shrink:0;animation:logo-breathe 3s ease-in-out infinite}
.logo img{width:100%;height:100%;object-fit:contain;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
@keyframes logo-breathe{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.04);opacity:.92}}
.brand{display:flex;align-items:baseline;gap:10px}
.brand h1{font-size:17px;font-weight:700;letter-spacing:-.3px;background:linear-gradient(135deg,var(--t1),var(--gd));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.brand span{font-size:11px;color:var(--t3);font-weight:500;letter-spacing:.5px}

/* Steps indicator */
.steps-bar{display:flex;align-items:center;justify-content:center;gap:0;padding:22px 20px 6px;flex-shrink:0;position:relative;z-index:1}
.step-ind{display:flex;align-items:center;gap:0}
.step-dot{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;border:2px solid var(--brd2);color:var(--t3);background:var(--card);transition:all .4s cubic-bezier(.4,0,.2,1);flex-shrink:0;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.step-dot.active{border-color:var(--g);color:#fff;background:linear-gradient(135deg,var(--g),#107c41);box-shadow:0 2px 12px rgba(33,115,70,.3),0 0 0 4px var(--ga1)}
.step-dot.done{border-color:var(--gl);color:#fff;background:var(--gl);box-shadow:0 2px 8px rgba(51,168,103,.2)}
.step-line{width:60px;height:2px;background:var(--brd2);transition:background .3s}
.step-line.done{background:var(--gl)}
.step-label{font-size:11px;color:var(--t3);text-align:center;margin-top:6px;font-weight:500}
.step-label.active{color:var(--g);font-weight:700}
.step-col{display:flex;flex-direction:column;align-items:center}

/* Content area */
.content{flex:1;max-width:580px;width:100%;margin:0 auto;padding:16px 20px 30px;position:relative;z-index:1}
.card{background:rgba(255,255,255,.82);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border:1px solid rgba(229,231,235,.6);border-radius:var(--r);overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.04),0 1px 2px rgba(0,0,0,.03);transition:box-shadow .3s,border-color .3s}
.card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06),0 2px 4px rgba(0,0,0,.04)}
.card-t{font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:1.5px;padding:14px 18px 10px;border-bottom:1px solid var(--brd);display:flex;align-items:center;gap:8px}
.card-t i{color:var(--g);font-style:normal;font-size:13px}
.card-body{padding:16px 18px}

/* Step panels */
.step-panel{display:none}.step-panel.show{display:block;animation:panel-in .3s ease-out}
@keyframes panel-in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

/* Env check items */
.env-item{display:flex;align-items:center;padding:12px 0;border-bottom:1px solid var(--bg)}
.env-item:last-child{border-bottom:none}
.env-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;margin-right:14px;flex-shrink:0;background:var(--bg)}
.env-icon.ok{background:rgba(33,115,70,.08);color:var(--g);box-shadow:0 0 0 3px rgba(33,115,70,.05)}
.env-icon.fail{background:rgba(209,52,56,.06);color:var(--red);box-shadow:0 0 0 3px rgba(209,52,56,.05)}
.env-icon.wait{background:rgba(229,161,0,.06);color:var(--gold);box-shadow:0 0 0 3px rgba(229,161,0,.05)}
.env-info{flex:1}
.env-name{font-size:14px;font-weight:600;color:var(--t1)}
.env-detail{font-size:12px;color:var(--t3);margin-top:2px}
.env-detail a{color:var(--cyan);text-decoration:none}.env-detail a:hover{text-decoration:underline}
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{display:inline-block;width:18px;height:18px;border:2.5px solid var(--brd);border-top-color:var(--gold);border-radius:50%;animation:spin .8s linear infinite}

/* Inputs */
.fg{margin-bottom:14px}.fg:last-child{margin-bottom:0}
.fl{font-size:11px;font-weight:600;color:var(--t2);margin-bottom:5px;display:flex;align-items:center;gap:6px}
.fi{width:100%;background:var(--bg);border:1.5px solid var(--brd);border-radius:var(--r2);padding:10px 14px;color:var(--t1);font-size:14px;font-family:inherit;outline:none;transition:border-color .2s,box-shadow .2s}
.fi:focus{border-color:var(--g);box-shadow:0 0 0 3px var(--ga1),0 1px 3px rgba(0,0,0,.06);background:#fff}
.fi::placeholder{color:var(--t4)}
select.fi{cursor:pointer;appearance:none;background-image:url(""data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 5l3 3 3-3' stroke='%239ca3af' fill='none' stroke-width='1.5'/%3E%3C/svg%3E"");background-repeat:no-repeat;background-position:right 12px center}
.help-link{font-size:11px;color:var(--cyan);text-decoration:none;margin-left:auto;font-weight:500}.help-link:hover{text-decoration:underline}

/* Buttons */
.btn{padding:10px 24px;border-radius:var(--r2);font-size:14px;font-weight:600;border:none;cursor:pointer;transition:all .2s;display:inline-flex;align-items:center;gap:8px;font-family:inherit}
.btn:active{transform:scale(.97)}
.b1{background:var(--g);color:#fff;box-shadow:0 2px 8px rgba(33,115,70,.2);position:relative;overflow:hidden}
.b1::after{content:'';position:absolute;inset:0;background:linear-gradient(135deg,rgba(255,255,255,.12),transparent);pointer-events:none}
.b1:hover{background:var(--gd);box-shadow:0 4px 16px rgba(33,115,70,.28);transform:translateY(-1px)}
.b2{background:var(--red);color:#fff}.b2:hover{background:var(--redl)}
.b3{background:var(--bg);color:var(--g);border:1.5px solid var(--ga3)}.b3:hover{background:var(--ga1)}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none!important;box-shadow:none!important}
.btn-row{display:flex;gap:10px;margin-top:18px;justify-content:flex-end}
.btn-big{width:100%;justify-content:center;padding:14px;font-size:16px;border-radius:var(--r)}

/* Test result */
.test-msg{margin-top:8px;padding:8px 12px;border-radius:6px;font-size:12px;font-weight:500;display:none}
.test-msg.ok{display:block;background:rgba(33,115,70,.06);color:var(--gd);border:1px solid rgba(33,115,70,.1)}.test-msg.fail{display:block;background:rgba(209,52,56,.06);color:var(--red);border:1px solid rgba(209,52,56,.1)}

/* Deploy progress */
.deploy-stage{font-size:14px;font-weight:600;color:var(--t1);margin-bottom:8px}
.deploy-sub{font-size:12px;color:var(--t3);margin-bottom:14px}
.pbar{height:6px;background:var(--brd);border-radius:3px;overflow:hidden;margin-bottom:16px}
.pfill{height:100%;width:0%;background:linear-gradient(90deg,var(--g),var(--gl));transition:width .5s ease;border-radius:3px;position:relative}
.pfill::after{content:'';position:absolute;inset:0;width:60px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);animation:shimmer 1.8s infinite}
@keyframes shimmer{from{transform:translateX(-60px)}to{transform:translateX(400px)}}
.log-toggle{font-size:12px;color:var(--cyan);cursor:pointer;user-select:none;display:flex;align-items:center;gap:4px;margin-bottom:8px}
.log-toggle:hover{text-decoration:underline}
.lcon{max-height:200px;overflow-y:auto;background:var(--bg);padding:8px 12px;border-radius:6px;font-family:'Cascadia Mono',Consolas,monospace;font-size:11px;line-height:1.6;color:var(--t2);display:none}
.lcon.show{display:block}
.ll{padding:1px 0;white-space:pre-wrap;word-break:break-all}
.ll.ok{color:var(--g)}.ll.err{color:var(--red)}.ll.warn{color:var(--gold)}.ll.hl{color:var(--cyan)}

/* Success screen */
.success-box{text-align:center;padding:30px 20px}
.success-icon{font-size:56px;margin-bottom:14px;animation:success-pop .5s cubic-bezier(.175,.885,.32,1.275);display:inline-block}
@keyframes success-pop{0%{transform:scale(0);opacity:0}60%{transform:scale(1.2)}100%{transform:scale(1);opacity:1}}
.success-title{font-size:22px;font-weight:700;background:linear-gradient(135deg,var(--g),var(--gl));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:6px;animation:fade-up .4s ease-out .2s both}
.success-sub{font-size:14px;color:var(--t3);margin-bottom:24px;animation:fade-up .4s ease-out .3s both}
@keyframes fade-up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

/* Advanced toggle */
.adv-toggle{font-size:12px;color:var(--t3);cursor:pointer;user-select:none;display:flex;align-items:center;gap:4px;margin-top:12px;padding-top:12px;border-top:1px solid var(--brd)}
.adv-toggle:hover{color:var(--t2)}
.adv-body{display:none;margin-top:10px}.adv-body.show{display:block}
.pr{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.bg-orb{position:fixed;border-radius:50%;filter:blur(80px);pointer-events:none;z-index:0}
.bg-orb-1{width:320px;height:320px;background:radial-gradient(circle,rgba(33,115,70,.1) 0%,transparent 70%);top:-80px;right:-60px;animation:orb-float 8s ease-in-out infinite}
.bg-orb-2{width:260px;height:260px;background:radial-gradient(circle,rgba(33,115,70,.07) 0%,transparent 70%);bottom:-50px;left:-40px;animation:orb-float 10s ease-in-out infinite reverse}
.bg-orb-3{width:180px;height:180px;background:radial-gradient(circle,rgba(0,120,212,.05) 0%,transparent 70%);top:40%;left:50%;animation:orb-float 12s ease-in-out 2s infinite}
@keyframes orb-float{0%,100%{transform:translateY(0) scale(1)}50%{transform:translateY(-20px) scale(1.05)}}
.lcon{scrollbar-width:thin;scrollbar-color:var(--brd2) transparent}.lcon::-webkit-scrollbar{width:4px}.lcon::-webkit-scrollbar-thumb{background:var(--brd2);border-radius:4px}
</style>
</head>
<body>
<div class='bg-orb bg-orb-1'></div><div class='bg-orb bg-orb-2'></div><div class='bg-orb bg-orb-3'></div>
<div class='hdr'>
  <div class='logo'><img src='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAABmJLR0QA/wD/AP+gvaeTAAALO0lEQVRoge1aaXBT1xX+7tNuS/IibzLe8QIYcACzGAIOAQwUSAgQaAtZCqSUzKRJO20y06FJm0nTTOikpWmbkKQNhSZhDSYECA5LzCIwwWDjfbdlS94lW7YW60nv9odBlmXJWBIDkxm+8Xj8zj3n3vO9e+9595xrQinFDxnMg3bAXzwk8KDxkMCDxg+eAP/+D5nXVnxL1zRGZQrK2u2DfzOEkQiE48Qh6+KzHAoPgMC1ruoz2ls+m6+Inu78OIxAga7mhKZwpA0BETC8GKliXexcKSPyeex7A0qcn4YR6Lb057eXjWKbHpwwMyjRTwc4yvnZgzO828R7686y1O7vmBy5u45nkOFHH+/2wC1dU562aMW4Gf54QAkFsDVlSYI0wiH8qDpvTnja5OBYh+RqZ5Xa1LUxccHQ6Hr1Fw0XXHrzehN/WHt6QcQkmUDiteN3MPgGU+TR6U7uSvni+MDwjNCh9ak2dRlY87TQJIekj7UAoGTYBHr9HTCw5kNqlddeO4Fyfu0BQoaZ+xJGD6pV6+Ky5IIAH2z7WHNTfyeAPxTv55Gh12dkLXX97R9Un3JIBuwsgHX5Ox0SG+dm+/lCwGy3Hmq+uiXpcW8NbdT+dumRRlMngB1Tn44OUDhWw7+qTk0PSZwRluxQPqst1rF9P0nMdkgq9M3vlh0FPIfRseMbbdHSqIyYAIVXVntqz1/tqhr8W8oXx0lCHU0CwpcLAqPEwQ6JXCjps5vjJENDNPV1wM8o5ECHRX9cW7g9OWfsJhfayw80qihFdIBCa+oGwHK2f1Sc7Lb0ZSrGj9SnFDzCmDkrAWEIoRQ8hsGdGOAvAQBH1KqFUZMnSKPHolzR07yzLJel7JOxmRwYrakbIO9XnDihvQHgiq4awOWO8ndHfEW/Un/vKhr+FfGdAMvZDzWpfp++7q6aBtb8VunhPps5Wzlla0rOroqvARTp609obwgZ/sqYGbnN1zhKn0nKflyZ4bDK0xZpzd2bkh5zSAq7aj+sPu3SuV+HufNtJetjs9Lk40bRsXK2Pxbv15h0UeKgzUkLZXyJnVIAn9acBfCLtGVrYmfX93cU6RrkwkClZGgPBAnEPVbRsF0hCBzZv18E7JT7oOb032ZsHkXni/qLhboGMRH8etIT8YHhAGx3DiNr4rOejJ0JIEgQAMBgNbaaexyGvazFZB9oswxJDKwRAMi9iEIO3NQ3qDor54ZPcNvay5qONhcAdG38nFlhqQB6rEZ1fxeAIEEggA5zj87Sz4DEBIQW65tYd9mjW/hY3F0//tFZIW6yEAAsZz+lvXln+1IAjDv/bZS70lUNIFgYCOD77joTZwkVSiPEwcXdDWP3xEcCBNiQON85K3dGt7Vv8ObNznEApEI3NZjvdbVVvRqGkHnhaRT0tOYGpcgISQRQoKsZuye+n0anhSZmho4v6HYdLJgn3hS/gAPXz1rLDGoASUHKkeYZwfFbUpeA4+ICw09pCkt7WwjIEmVGpUFTb2y/HwQAvDJh5UbVLudaJwGeislKlil3lhwt7mk02q3JsqhpQQkjbQN4omcSFgAo1jfuqjzJgVufMG9OROqbJYe88sGvC45oSeiq4WXGdFnsM8kLr3fXnWy92WLWAciOShfxBJ56uNpV/WrhXovdOjdiwubxi4p7Gs+0FXvlg783NM8mPhbI3D4ISBnxbyatNtos75Udo6ByfgCAf9ecffPWwcEY74IjTaodNz8f4Ng54akvTVgh5PEud1Z564C/BMJE8kcVEwAQStbEzE6URXxae0Zr0U8Mjvl47va1sXMYQs61lTx3+f1vW4e92lPaGx/VfGuj9qny+B1T1inFwVaOu9ZWeb8JAHghNSeYFzBVHvez1EXftZad0NwE8GxSdqQ4+KWJK/6auTkmQGFgTX8qObyj6HPdQD+A8l7N7uq8Ac62MSn7L7Oel/IlAOyczW73unZ/D66YwiVBy8Knrhm/z8iad9d8Y+XYlbEzs8JuZ8kZIQmfZL34YdXpr1quX+qouKVvejFtWX57eY/VOCssdVPSAiFz2weGEJeE/T4RALB98goAb5ccbrX0hIlk25KXOLeKecJXJq2aoUh+v/LrjgHDO6VfAggTy341caWEcT5I+7Ic7tk1q6qrKq+1mAG2JC1ye3swP3LiB7O3LR93+4pOyPB1w3c2ASHuUqXRcW8ItFt6d1eeBrAoKmN5rMf7G4VY/lr6U7+btEbCE2pN+teLPu+3DSWovrh/TwhwoAcaLzWZOuMkiu1pSz2pHVarTmoLAcQGRdg4DsDWlBypUwrKgBDv98A9IHCutSS3uYAhzLa0ZaEimVsdCnqupchgNdop9/ey4yy1ZUdOylG6FgE8ZQ6jwN9NbGDNnzXmc5SuiZs9L8J9fQ4AAfnn3O0A9tSdqzBoFCLZCynLXM+CxHPq4Bn+zsCuyuMNfR3JcuXW5MWjaxKQGkPr/oZLALamLo4JCBmpw7vPS+i4+vq51lJCmG2pOQH8u9zgWznbe6W5A5xtmjx+qXKaWx0flpDvBNrMvZ/U5VHQpRFTZoYm31W/UJOqqr9VIZK9mrGWceco8VC/GB0+EuBA3yv5spc1x4oV2ycuv6t+nbF1X30+KH6emqOUuFk8AAiIW2Kjw0cCBxsuF/Y0CBjettQlQUI39w7OYDn7OyVHLXbr3LC0RVFTPOoR19r/WOALAY1Fd6Tlih306fh5j0al31U/V3OtxtAaKpS+nL6KT1wLwM7g+RlGxwWErB9eG3Qg3KkieaWjMlWqTJfHPT9+4V0H6LGarrVVTQtJXB6TGTG8rOkCAvJIaGJa8O1SKSGMI6yKGI8vmjz8x9cHjIcEHjQeEnjQ+MET+D9VQmEb2uWJ1QAAAABJRU5ErkJggg==' alt='ExcelManus'></div>
  <div class='brand'><h1>ExcelManus</h1><span>v2.0</span></div>
</div>

<!-- Quick-start overlay (hidden by default, shown when already deployed) -->
<div id='quickStartOverlay' style='display:none;position:absolute;inset:0;z-index:100;background:linear-gradient(145deg,#f5f7f8 0%,#eef2f0 50%,#f0f5f3 100%);display:none;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:40px'>
  <div style='font-size:48px;animation:success-pop .5s cubic-bezier(.175,.885,.32,1.275)' id='qsIcon'>&#9889;</div>
  <div style='font-size:20px;font-weight:700;background:linear-gradient(135deg,var(--g),var(--gl));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text' id='qsTitle'>正在检查更新...</div>
  <div style='font-size:13px;color:var(--t3)' id='qsSub'>检测到已部署的项目，正在检查是否有新版本</div>
  <div class='progress-wrap' style='width:240px;margin-top:8px'><div class='progress-bar' id='qsBar' style='width:30%'></div></div>
  <!-- Update prompt (hidden until update found) -->
  <div id='updatePrompt' style='display:none;text-align:center;margin-top:8px;padding:16px 24px;background:var(--card);border:1px solid var(--brd);border-radius:var(--r);box-shadow:0 2px 8px rgba(0,0,0,.06);max-width:380px'>
    <div style='font-size:15px;font-weight:700;margin-bottom:4px' id='updateTitle'>&#127881; 发现新版本！</div>
    <div style='font-size:13px;color:var(--t2);margin-bottom:12px' id='updateDetail'></div>
    <div style='display:flex;gap:10px;justify-content:center'>
      <button class='btn b1' onclick='doQuickUpdate()'>&#128259; 立即更新</button>
      <button class='btn b3' onclick='skipUpdate()'>跳过，直接启动</button>
    </div>
  </div>
  <div id='qsButtons' style='margin-top:20px'><button class='btn b3' onclick='cancelQuickStart()'>&#9998; 进入完整设置向导</button></div>
</div>

<div class='steps-bar' id='stepsBar'>
  <div class='step-col'><div class='step-dot active' id='sd1'>1</div><div class='step-label active' id='sl1'>环境检测</div></div>
  <div class='step-ind'><div class='step-line' id='sln1'></div></div>
  <div class='step-col'><div class='step-dot' id='sd2'>2</div><div class='step-label' id='sl2'>启动部署</div></div>
</div>

<div class='content'>
  <!-- ══ Step 1: Environment Check ══ -->
  <div class='step-panel show' id='p1'>
    <div class='card'>
      <div class='card-t'><i>&#128269;</i> 正在检测您的电脑环境...</div>
      <div class='card-body' id='env-list'></div>
    </div>
    <div class='btn-row'>
      <button class='btn b3' id='btnRecheck' onclick='doCheckEnv()' style='display:none'>&#x21BB; 重新检测</button>
      <button class='btn b1' id='btnNext1' onclick='goStep(2)' disabled>开始部署 &#8594;</button>
    </div>
  </div>

  <!-- ══ Step 2: Deploy ══ -->
  <div class='step-panel' id='p2'>
    <div class='card' id='deployCard'>
      <div class='card-body'>
        <div id='preDeployView' style='text-align:center;padding:20px 0'>
          <div style='font-size:42px;margin-bottom:10px'>&#128640;</div>
          <div style='font-size:18px;font-weight:700;margin-bottom:6px'>一切就绪，准备部署！</div>
          <div style='font-size:13px;color:var(--t3);margin-bottom:20px'>点击下方按钮，自动完成所有安装与启动</div>
          <button class='btn b1 btn-big' id='btnDeploy' onclick='doDeploy()'>&#9654; 开始部署</button>
        </div>
        <div id='deployingView' style='display:none'>
          <div class='deploy-stage' id='dStage'>正在准备...</div>
          <div class='deploy-sub' id='dSub'>请稍候，首次部署预计需要 5-15 分钟（含前端构建）</div>
          <div class='pbar'><div class='pfill' id='pf'></div></div>
          <div class='log-toggle' onclick='toggleLog()'>📋 <span id='logToggleText'>展开详细日志</span></div>
          <div class='lcon' id='lc'></div>
        </div>
        <div id='successView' style='display:none'>
          <div class='success-box'>
            <div class='success-icon'>&#127881;</div>
            <div class='success-title'>部署成功！</div>
            <div class='success-sub'>服务已启动，即将跳转到 ExcelManus</div>
            <div id='redirectCountdown' style='font-size:13px;color:var(--t3);margin-bottom:16px'><span id='countdownNum'>3</span> 秒后自动跳转... <a href='#' onclick='cancelRedirect();return false' style='color:var(--cyan)'>取消</a></div>
            <button class='btn b1 btn-big' onclick='doOpen()'>&#127760; 立即打开 ExcelManus</button>
            <div style='margin-top:14px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap'>
              <button class='btn b3' id='btnShortcut' onclick='doCreateShortcut()'>&#128194; 创建桌面快捷方式</button>
              <button class='btn b3' id='btnUpdate' onclick='doUpdate()' style='display:none'>&#128259; 检查更新</button>
              <button class='btn b2' onclick='doStop()'>&#9724; 停止服务</button>
            </div>
            <div style='margin-top:10px;font-size:11px;color:var(--t3)'>&#128161; 可通过系统托盘图标随时返回管理面板</div>
            <div id='updateMsg' class='test-msg' style='margin-top:12px'></div>
          </div>
        </div>
      </div>
    </div>
    <div class='btn-row' id='step2Back'>
      <button class='btn b3' onclick='goStep(1)'>&#8592; 上一步</button>
    </div>
  </div>
</div>

<script>
var ENV_ITEMS=[
  {id:'python',name:'Python 3.x',ico:'&#128013;',dl:'https://www.python.org/downloads/'},
  {id:'node',name:'Node.js',ico:'&#9889;',dl:'https://nodejs.org/zh-cn/download/'},
  {id:'git',name:'Git',ico:'&#128230;',dl:'https://git-scm.com/download/win'}
];
var curStep=1,logIdx=0,deploying=false,deployDone=false,fePort='3000';

var quickStartMode=false;
function init(){
  buildEnvList();
  fetch('/api/config').then(function(r){return r.json()}).then(function(d){
    if(d.fePort)fePort=d.fePort;
    if(d.quickStart){
      startQuickStart();
    }else{
      doCheckEnv();
    }
  }).catch(function(){ doCheckEnv(); });
  setInterval(pollLogs,600);
  setInterval(pollSt,900);
}
function startQuickStart(){
  quickStartMode=true;
  document.getElementById('quickStartOverlay').style.display='flex';
  document.getElementById('stepsBar').style.display='none';
  document.querySelectorAll('.content>.step-panel').forEach(function(el){el.style.display='none';});
  document.getElementById('qsTitle').textContent='正在检查更新...';
  document.getElementById('qsSub').textContent='检测到已部署的项目，正在检查是否有新版本';
  document.getElementById('qsBar').style.width='30%';
  document.getElementById('updatePrompt').style.display='none';
  // Check for updates with 8s timeout on JS side too
  var timeout=setTimeout(function(){skipUpdate();},10000);
  fetch('/api/check-update-quick',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    clearTimeout(timeout);
    if(d.has_update){
      document.getElementById('qsIcon').innerHTML='&#127881;';
      document.getElementById('qsTitle').textContent='发现新版本！';
      document.getElementById('qsSub').textContent='';
      document.getElementById('qsBar').style.width='50%';
      document.getElementById('updateDetail').innerHTML='<b>'+d.current+' &#8594; '+d.latest+'</b>（'+d.behind+' 个新提交）';
      document.getElementById('updatePrompt').style.display='block';
    }else{
      skipUpdate();
    }
  }).catch(function(){
    clearTimeout(timeout);
    skipUpdate();
  });
}
function skipUpdate(){
  document.getElementById('updatePrompt').style.display='none';
  document.getElementById('qsIcon').innerHTML='&#9889;';
  document.getElementById('qsTitle').textContent='快速启动中...';
  document.getElementById('qsSub').textContent='正在启动服务，请稍候';
  document.getElementById('qsBar').style.width='85%';
  deploying=true;
  fetch('/api/quick-start',{method:'POST'}).catch(function(){});
}
function doQuickUpdate(){
  document.getElementById('updatePrompt').style.display='none';
  document.getElementById('qsIcon').innerHTML='&#128259;';
  document.getElementById('qsTitle').textContent='正在更新...';
  document.getElementById('qsSub').textContent='下载并安装新版本，完成后自动启动';
  document.getElementById('qsBar').style.width='40%';
  deploying=false;
  fetch('/api/update-apply',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    if(d.success){
      document.getElementById('qsBar').style.width='70%';
      document.getElementById('qsTitle').textContent='更新成功！正在启动...';
      document.getElementById('qsSub').textContent=d.old_version+' \u2192 '+d.new_version;
      deploying=true;
      fetch('/api/quick-start',{method:'POST'}).catch(function(){});
    }else{
      document.getElementById('qsTitle').textContent='更新失败';
      document.getElementById('qsSub').textContent=(d.error||'未知错误')+'，将直接启动当前版本';
      setTimeout(function(){skipUpdate();},2000);
    }
  }).catch(function(e){
    document.getElementById('qsTitle').textContent='更新失败';
    document.getElementById('qsSub').textContent=e+'，将直接启动当前版本';
    setTimeout(function(){skipUpdate();},2000);
  });
}
function cancelQuickStart(){
  quickStartMode=false;
  deploying=false;
  document.getElementById('quickStartOverlay').style.display='none';
  document.getElementById('stepsBar').style.display='';
  document.querySelectorAll('.content>.step-panel').forEach(function(el){el.style.display='';});
  goStep(1);
  doCheckEnv();
}

function buildEnvList(){
  var h='';
  for(var i=0;i<ENV_ITEMS.length;i++){
    var e=ENV_ITEMS[i];
    h+='<div class=""env-item""><div class=""env-icon wait"" id=""ei_'+e.id+'""><div class=""spinner""></div></div>';
    h+='<div class=""env-info""><div class=""env-name"">'+e.name+'</div>';
    h+='<div class=""env-detail"" id=""ed_'+e.id+'"">检测中...</div></div></div>';
  }
  document.getElementById('env-list').innerHTML=h;
}

function doCheckEnv(){
  document.getElementById('btnRecheck').style.display='none';
  document.getElementById('btnNext1').disabled=true;
  buildEnvList();
  fetch('/api/check-env',{method:'POST'}).catch(function(){});
}

function updateEnvUI(checks,details){
  var allOk=true;
  for(var i=0;i<ENV_ITEMS.length;i++){
    var e=ENV_ITEMS[i];
    var st=checks[e.id]||0;
    var el=document.getElementById('ei_'+e.id);
    var dl=document.getElementById('ed_'+e.id);
    if(st===1){el.className='env-icon ok';el.innerHTML='&#10004;';dl.textContent=details[e.id]||'已就绪';}
    else if(st===2){el.className='env-icon fail';el.innerHTML='&#10008;';dl.innerHTML='未找到 — <a href=""'+e.dl+'"" target=""_blank"">点此手动下载安装</a>';allOk=false;}
    else if(st===3){el.className='env-icon wait';el.innerHTML='<div class=""spinner""></div>';dl.textContent=details[e.id]||'检测中...';}
    else{allOk=false;}
  }
  if(checks['python']&&checks['node']&&checks['git']&&checks['python']!==3&&checks['node']!==3&&checks['git']!==3){
    document.getElementById('btnRecheck').style.display='';
    if(allOk){document.getElementById('btnNext1').disabled=false;}
  }
}

/* ── Step navigation ── */
function goStep(n){
  curStep=n;
  for(var i=1;i<=2;i++){
    document.getElementById('p'+i).className='step-panel'+(i===n?' show':'');
    var d=document.getElementById('sd'+i);
    var l=document.getElementById('sl'+i);
    if(i<n){d.className='step-dot done';d.innerHTML='&#10004;';l.className='step-label';}
    else if(i===n){d.className='step-dot active';d.innerHTML=i;l.className='step-label active';}
    else{d.className='step-dot';d.innerHTML=i;l.className='step-label';}
  }
  if(document.getElementById('sln1'))document.getElementById('sln1').className='step-line'+(n>1?' done':'');
}

/* ── Deploy ── */
function doDeploy(){
  document.getElementById('preDeployView').style.display='none';
  document.getElementById('deployingView').style.display='';
  document.getElementById('step2Back').style.display='none';
  deploying=true;
  fetch('/api/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(function(){});
}

var STAGE_NAMES={0:'正在准备...',14:'正在下载源码...',28:'正在下载源码...',42:'正在安装后端依赖...',57:'正在安装后端依赖...',71:'正在安装并构建前端...',85:'正在启动服务...',100:'部署完成！'};
function closestStage(p){var best='';for(var k in STAGE_NAMES){if(parseInt(k)<=p)best=STAGE_NAMES[k];}return best||'正在部署...';}

var redirectIv=null;
function startRedirect(){
  var n=3;document.getElementById('countdownNum').textContent=n;
  redirectIv=setInterval(function(){
    n--;if(n<=0){clearInterval(redirectIv);redirectIv=null;doOpen();}
    else document.getElementById('countdownNum').textContent=n;
  },1000);
}
function cancelRedirect(){
  if(redirectIv){clearInterval(redirectIv);redirectIv=null;}
  var el=document.getElementById('redirectCountdown');if(el)el.style.display='none';
}
function doStop(){cancelRedirect();var resetUI=function(){deploying=false;deployDone=false;
  document.getElementById('preDeployView').style.display='';
  document.getElementById('deployingView').style.display='none';
  document.getElementById('successView').style.display='none';
  document.getElementById('step2Back').style.display='';
};fetch('/api/stop',{method:'POST'}).then(resetUI).catch(resetUI);}
function doOpen(){window.location.href='http://localhost:'+fePort;}

/* ── Polling ── */
function pollLogs(){
  fetch('/api/logs?since='+logIdx).then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById('lc');
    for(var i=0;i<d.logs.length;i++){var l=d.logs[i];var div=document.createElement('div');div.className='ll '+l.level;div.textContent=l.text;el.appendChild(div);logIdx=l.idx+1;}
    if(d.logs.length>0)el.scrollTop=el.scrollHeight;
  }).catch(function(){});
}

function pollSt(){
  fetch('/api/status').then(function(r){return r.json()}).then(function(d){
    updateEnvUI(d.checks,d.details||{});
    if(deploying){
      var pct=d.progress||0;
      if(!quickStartMode){
        document.getElementById('pf').style.width=pct+'%';
        document.getElementById('dStage').textContent=closestStage(pct);
      }else{
        var qsb=document.getElementById('qsBar');
        if(qsb)qsb.style.width=Math.max(pct,85)+'%';
      }
      if(d.running&&!deployDone){
        deployDone=true;
        if(quickStartMode){
          document.getElementById('quickStartOverlay').style.display='none';
          document.getElementById('stepsBar').style.display='';
          document.querySelectorAll('.content>.step-panel').forEach(function(el){el.style.display='';});
        }
        goStep(2);
        document.getElementById('preDeployView').style.display='none';
        document.getElementById('deployingView').style.display='none';
        document.getElementById('successView').style.display='';
        document.getElementById('step2Back').style.display='none';
        document.getElementById('btnUpdate').style.display='';
        startRedirect();
      }
    }
  }).catch(function(){});
}

function toggleLog(){
  var el=document.getElementById('lc');
  var t=document.getElementById('logToggleText');
  if(el.className.indexOf('show')>=0){el.className='lcon';t.textContent='展开详细日志';}
  else{el.className='lcon show';t.textContent='收起日志';el.scrollTop=el.scrollHeight;}
}
/* ── Shortcut ── */
function doCreateShortcut(){
  var btn=document.getElementById('btnShortcut');
  var msg=document.getElementById('updateMsg');
  btn.disabled=true;btn.innerHTML='&#8987; 创建中...';
  fetch('/api/create-shortcut',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    if(d.path){
      msg.className='test-msg ok';msg.style.display='block';
      msg.textContent='✅ 桌面快捷方式已创建: '+d.path;
      btn.style.display='none';
    }else{
      msg.className='test-msg fail';msg.style.display='block';
      msg.textContent='❌ 创建失败: '+(d.error||'未知错误');
    }
  }).catch(function(e){
    msg.className='test-msg fail';msg.style.display='block';
    msg.textContent='❌ 创建失败: '+e;
  }).finally(function(){btn.disabled=false;btn.innerHTML='&#128194; 创建桌面快捷方式';});
}

/* ── Update ── */
function doUpdate(){
  var btn=document.getElementById('btnUpdate');
  var msg=document.getElementById('updateMsg');
  btn.disabled=true;btn.innerHTML='&#8987; 检查中...';
  msg.className='test-msg';msg.style.display='none';
  fetch('/api/update-check',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    if(d.has_update){
      msg.className='test-msg ok';msg.style.display='block';
      msg.innerHTML='🎉 发现新版本: <b>'+d.current+' → '+d.latest+'</b> ('+d.behind+' 个新提交)<br><br>'
        +'<button class=""btn b1"" onclick=""doApplyUpdate()"" style=""margin-top:6px"">🔄 立即更新</button>';
    }else{
      msg.className='test-msg ok';msg.style.display='block';
      msg.textContent='✅ 已是最新版本 ('+d.current+')';
    }
  }).catch(function(e){
    msg.className='test-msg fail';msg.style.display='block';
    msg.textContent='❌ 检查更新失败: '+e;
  }).finally(function(){btn.disabled=false;btn.innerHTML='&#128259; 检查更新';});
}
function doApplyUpdate(){
  var msg=document.getElementById('updateMsg');
  msg.className='test-msg';msg.style.display='block';
  msg.innerHTML='<div class=""spinner""></div> 正在更新，请稍候...';
  fetch('/api/update-apply',{method:'POST'}).then(function(r){return r.json()}).then(function(d){
    if(d.success){
      msg.className='test-msg ok';
      msg.innerHTML='✅ 更新成功！'+d.old_version+' → '+d.new_version+'<br>请重启服务以应用更新。';
    }else{
      msg.className='test-msg fail';
      msg.textContent='❌ 更新失败: '+(d.error||'未知错误');
    }
  }).catch(function(e){msg.className='test-msg fail';msg.textContent='❌ 更新失败: '+e;});
}

init();
</script>
</body>
</html>";
}

// ═══════════════════════════════════════════════════════════
//  Log Storage (thread-safe)
// ═══════════════════════════════════════════════════════════
public class LogEntry
{
    public string Text;
    public string Level;
    public int Idx;
}

public class LogStore
{
    private readonly List<LogEntry> _items = new List<LogEntry>();
    private readonly object _lock = new object();

    public void Add(string text, string level)
    {
        lock (_lock)
        {
            LogEntry e = new LogEntry();
            e.Text = text;
            e.Level = level;
            e.Idx = _items.Count;
            _items.Add(e);
        }
    }

    public LogEntry[] Since(int idx)
    {
        lock (_lock)
        {
            if (idx >= _items.Count) return new LogEntry[0];
            List<LogEntry> result = new List<LogEntry>();
            for (int i = idx; i < _items.Count; i++)
                result.Add(_items[i]);
            return result.ToArray();
        }
    }

    public void Info(string t) { Add(t, "info"); }
    public void Ok(string t) { Add(t, "ok"); }
    public void Err(string t) { Add(t, "err"); }
    public void Warn(string t) { Add(t, "warn"); }
    public void Hl(string t) { Add(t, "hl"); }
}

// ═══════════════════════════════════════════════════════════
//  Deploy Engine
// ═══════════════════════════════════════════════════════════
public class Engine
{
    #region Fields & Constructor

    private const string REPO_URL = "https://github.com/kilolonion/excelmanus.git";
    private const string REPO_URL_GITEE = "https://gitee.com/kilolonion/excelmanus.git";
    private const string REPO_DIR_NAME = "excelmanus";
    private string _root;
    private readonly LogStore _log;
    private readonly object _lock = new object();
    private Process _procBE;
    private Process _procFE;
    private bool _running;
    private bool _deploying;
    private string _deployError;
    private bool _needsClone;
    private readonly Dictionary<string, int> _checks = new Dictionary<string, int>();
    private readonly Dictionary<string, string> _details = new Dictionary<string, string>();
    private int _progress;

    private string _bePort, _fePort;
    private string _pythonExe = "python";

    public LogStore Log { get { return _log; } }

    public Engine()
    {
        _log = new LogStore();
        _bePort = "8000"; _fePort = "3000";

        string[] ids = new string[] { "python", "node", "npm", "git", "repo", "backend", "frontend" };
        foreach (string id in ids)
        {
            _checks[id] = 0;
            _details[id] = "待检测";
        }
        DetectRoot();
        LoadConfig();
    }

    #endregion

    #region Configuration

    private void DetectRoot()
    {
        string d = Path.GetDirectoryName(Application.ExecutablePath);
        // Case 1: exe is in repo root (has pyproject.toml)
        if (File.Exists(Path.Combine(d, "pyproject.toml")))
        { _root = d; _needsClone = false; }
        else
        {
            // Case 2: exe is in deploy/ subfolder
            string p = Directory.GetParent(d) != null ? Directory.GetParent(d).FullName : d;
            if (File.Exists(Path.Combine(p, "pyproject.toml")))
            { _root = p; _needsClone = false; }
            else
            {
                // Case 3: previously cloned repo exists next to exe
                string cloned = Path.Combine(d, REPO_DIR_NAME);
                if (File.Exists(Path.Combine(cloned, "pyproject.toml")))
                { _root = cloned; _needsClone = false; }
                else
                {
                    // Case 4: standalone exe, need to clone
                    _root = d;
                    _needsClone = true;
                    _log.Warn("未找到项目文件，将在部署时自动从 Gitee 克隆");
                }
            }
        }
        _log.Hl(string.Format("项目根目录: {0}", _root));
    }

    private string EnvPath { get { return Path.Combine(_root, ".env"); } }
    private string InstallCompletePath { get { return Path.Combine(_root, ".install_complete"); } }

    private void WriteInstallComplete()
    {
        try
        {
            string ver = ReadVersionFromToml(Path.Combine(_root, "pyproject.toml"));
            File.WriteAllText(InstallCompletePath, ver, new UTF8Encoding(false));
            _log.Ok(string.Format("安装完成标记已写入 (v{0})", ver));
        }
        catch { }
    }

    private bool IsInstallComplete()
    {
        return File.Exists(InstallCompletePath);
    }

    private string GetInstalledVersion()
    {
        try
        {
            if (File.Exists(InstallCompletePath))
                return File.ReadAllText(InstallCompletePath).Trim();
        }
        catch { }
        return null;
    }

    private void LoadConfig()
    {
        if (!File.Exists(EnvPath)) return;
        try
        {
            foreach (string raw in File.ReadAllLines(EnvPath))
            {
                string ln = raw.Trim();
                if (string.IsNullOrEmpty(ln) || ln.StartsWith("#")) continue;
                int eq = ln.IndexOf('=');
                if (eq <= 0) continue;
                string k = ln.Substring(0, eq).Trim();
                string v = ln.Substring(eq + 1).Trim().Trim('"');
                if (k == "EXCELMANUS_BACKEND_PORT") _bePort = ValidatePort(v, _bePort);
                else if (k == "EXCELMANUS_FRONTEND_PORT") _fePort = ValidatePort(v, _fePort);
            }
            _log.Info(string.Format("已加载 .env 配置 (后端端口={0}, 前端端口={1})", _bePort, _fePort));
        }
        catch (Exception ex) { _log.Warn(string.Format("读取 .env 失败: {0}", ex.Message)); }
    }

    public void SetConfig(string bePort, string fePort)
    {
        _bePort = ValidatePort(bePort, "8000");
        _fePort = ValidatePort(fePort, "3000");
    }

    private void SaveEnv()
    {
        try
        {
            // Merge mode: read existing .env, update values in-place, preserve structure
            List<string> lines = new List<string>();
            HashSet<string> seenKeys = new HashSet<string>();

            if (File.Exists(EnvPath))
            {
                foreach (string raw in File.ReadAllLines(EnvPath))
                {
                    string ln = raw.Trim();
                    if (string.IsNullOrEmpty(ln) || ln.StartsWith("#"))
                    {
                        lines.Add(raw);
                        continue;
                    }
                    int eq = ln.IndexOf('=');
                    if (eq <= 0) { lines.Add(raw); continue; }
                    string k = ln.Substring(0, eq).Trim();
                    seenKeys.Add(k);
                    // Update value in-place if we have a new value for this key
                    string newVal = GetEnvOverride(k);
                    if (newVal != null)
                        lines.Add(string.Format("{0}={1}", k, newVal));
                    else
                        lines.Add(raw);
                }
            }

            // Append keys that were not already in the file
            AppendIfMissing(lines, seenKeys, "EXCELMANUS_CORS_ALLOW_ORIGINS",
                string.Format("http://localhost:{0},http://localhost:5173", _fePort));
            AppendIfMissing(lines, seenKeys, "EXCELMANUS_AUTH_ENABLED", "false");
            AppendIfMissing(lines, seenKeys, "EXCELMANUS_EXTERNAL_SAFE_MODE", "false");

            // E37: use UTF-8 without BOM so python-dotenv can parse the first line correctly
            File.WriteAllLines(EnvPath, lines.ToArray(), new UTF8Encoding(false));
            _log.Ok("已保存 .env");
        }
        catch (Exception ex) { _log.Err(string.Format("保存 .env 失败: {0}", ex.Message)); }
    }

    private string GetEnvOverride(string key)
    {
        if (key == "EXCELMANUS_CORS_ALLOW_ORIGINS")
            return string.Format("http://localhost:{0},http://localhost:5173", _fePort);
        // Do not override AUTH_ENABLED / EXTERNAL_SAFE_MODE if user already set them
        return null;
    }

    private static string ValidatePort(string port, string defaultPort)
    {
        if (string.IsNullOrEmpty(port)) return defaultPort;
        int p;
        if (int.TryParse(port.Trim(), out p) && p >= 1 && p <= 65535)
            return p.ToString();
        return defaultPort;
    }

    private void AppendIfMissing(List<string> lines, HashSet<string> seen, string k, string v)
    {
        if (!seen.Contains(k))
        {
            lines.Add(string.Format("{0}={1}", k, v));
            seen.Add(k);
        }
    }

    #endregion

    #region JSON Serialization

    public bool IsReadyForQuickStart()
    {
        if (_needsClone) return false;
        if (!File.Exists(EnvPath)) return false;
        if (!IsInstallComplete()) return false;
        // E35/E40: version consistency check
        string installedVer = GetInstalledVersion();
        string currentVer = ReadVersionFromToml(Path.Combine(_root, "pyproject.toml"));
        if (installedVer != null && currentVer != null && installedVer != currentVer)
        {
            _log.Warn(string.Format("\u7248\u672c\u4e0d\u4e00\u81f4: \u5df2\u5b89\u88c5={0}, \u5f53\u524d\u4ee3\u7801={1}\uff0c\u9700\u8981\u91cd\u65b0\u90e8\u7f72", installedVer, currentVer));
            return false;
        }
        string vpy = Path.Combine(_root, ".venv", "Scripts", "python.exe");
        if (!File.Exists(vpy)) return false;
        string nodeModules = Path.Combine(_root, "web", "node_modules");
        if (!Directory.Exists(nodeModules)) return false;
        string nextBuild = Path.Combine(_root, "web", ".next");
        if (!Directory.Exists(nextBuild)) return false;
        return true;
    }

    public void QuickStart()
    {
        lock (_lock)
        {
            if (_running || _deploying) return;
            _deploying = true;
            _deployError = null;
            _progress = 85;
        }
        _log.Hl("═══ 快速启动模式 ═══");
        _log.Info("检测到已部署的项目，跳过向导直接启动服务...");
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                SaveEnv();
                lock (_lock) { _progress = 100; _deploying = false; }
                StartServices();
            }
            catch (Exception ex)
            {
                _log.Err(string.Format("快速启动失败: {0}", ex.Message));
                lock (_lock) { _deployError = string.Format("快速启动失败: {0}", ex.Message); _deploying = false; }
            }
        });
    }

    public string GetConfigJson()
    {
        bool qs = IsReadyForQuickStart();
        return string.Format("{{\"bePort\":\"{0}\",\"fePort\":\"{1}\",\"quickStart\":{2}}}",
            JE(_bePort), JE(_fePort), qs ? "true" : "false");
    }

    public string GetStatusJson()
    {
        lock (_lock)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"running\":"); sb.Append(_running ? "true" : "false");
            sb.Append(",\"deploying\":"); sb.Append(_deploying ? "true" : "false");
            sb.Append(",\"progress\":"); sb.Append(_progress);
            if (_deployError != null)
            {
                sb.Append(",\"deploy_error\":\""); sb.Append(JE(_deployError)); sb.Append("\"");
            }
            sb.Append(",\"checks\":{");
            bool first = true;
            foreach (KeyValuePair<string, int> kv in _checks)
            {
                if (!first) sb.Append(",");
                sb.Append(string.Format("\"{0}\":{1}", kv.Key, kv.Value));
                first = false;
            }
            sb.Append("},\"details\":{");
            first = true;
            foreach (KeyValuePair<string, string> kv in _details)
            {
                if (!first) sb.Append(",");
                sb.Append(string.Format("\"{0}\":\"{1}\"", kv.Key, JE(kv.Value)));
                first = false;
            }
            sb.Append("}}");
            return sb.ToString();
        }
    }

    public string GetLogsJson(int since)
    {
        LogEntry[] entries = _log.Since(since);
        StringBuilder sb = new StringBuilder();
        sb.Append("{\"logs\":[");
        for (int i = 0; i < entries.Length; i++)
        {
            if (i > 0) sb.Append(",");
            sb.Append(string.Format("{{\"text\":\"{0}\",\"level\":\"{1}\",\"idx\":{2}}}",
                JE(entries[i].Text), entries[i].Level, entries[i].Idx));
        }
        sb.Append("]}");
        return sb.ToString();
    }

    private static string JE(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "").Replace("\t", "\\t");
    }

    private static string ReadVersionFromToml(string tomlPath)
    {
        if (!File.Exists(tomlPath)) return "unknown";
        try
        {
            return ParseVersionFromTomlContent(File.ReadAllText(tomlPath, Encoding.UTF8)) ?? "unknown";
        }
        catch { }
        return "unknown";
    }

    private static string ParseVersionFromTomlContent(string content)
    {
        if (string.IsNullOrEmpty(content)) return null;
        foreach (string line in content.Split('\n'))
        {
            if (line.TrimStart().StartsWith("version") && line.Contains("="))
                return line.Split('=')[1].Trim().Trim('"');
        }
        return null;
    }

    #endregion

    #region Environment Check

    public void CheckEnv()
    {
        lock (_lock)
        {
            _checks["python"] = 3; _details["python"] = "检测中...";
            _checks["node"] = 3; _details["node"] = "检测中...";
            _checks["git"] = 3; _details["git"] = "检测中...";
        }
        ThreadPool.QueueUserWorkItem(delegate { RunCheckEnv(); });
    }

    private string CheckToolVersion(string displayName, string exe, string versionArg, string wingetId, string versionContains)
    {
        return CheckToolVersion(displayName, exe, versionArg, wingetId, versionContains, true);
    }

    private string CheckToolVersion(string displayName, string exe, string versionArg, string wingetId, string versionContains, bool autoInstall)
    {
        string ver = CmdRun(exe, versionArg);
        bool ok = !string.IsNullOrEmpty(ver) && (versionContains == null || ver.Contains(versionContains));
        if (!ok && autoInstall)
        {
            if (TryAutoInstall(displayName, wingetId))
            {
                ver = CmdRun(exe, versionArg);
                ok = !string.IsNullOrEmpty(ver) && (versionContains == null || ver.Contains(versionContains));
            }
        }
        return ok ? ver : null;
    }

    // E6: parse major version from strings like "v20.11.0" or "v18.0.0"
    private int ParseMajorVersion(string ver)
    {
        if (string.IsNullOrEmpty(ver)) return -1;
        string s = ver.Trim();
        if (s.StartsWith("v") || s.StartsWith("V")) s = s.Substring(1);
        int dot = s.IndexOf('.');
        if (dot > 0) s = s.Substring(0, dot);
        int major;
        if (int.TryParse(s, out major)) return major;
        return -1;
    }

    private void RunCheckEnv()
    {
        // Check-only mode: do NOT auto-install via winget (autoInstall=false)
        // Auto-install only happens in RunDeploy()

        // Python (E2: try "python" first, then "py -3" as fallback)
        string pyV = CheckToolVersion("Python", "python", "--version", "Python.Python.3.11", "Python 3", false);
        bool pyOk = pyV != null;
        if (pyOk) { _pythonExe = "python"; }
        else
        {
            // E2: try py launcher
            string pyLV = CmdRun("py", "-3 --version");
            if (!string.IsNullOrEmpty(pyLV) && pyLV.Contains("Python 3"))
            {
                pyV = pyLV; pyOk = true; _pythonExe = "py";
                _log.Info("通过 py launcher 检测到 Python");
            }
        }
        lock (_lock) { _checks["python"] = pyOk ? 1 : 2; _details["python"] = pyOk ? pyV.Replace("Python ", "v") : "未找到"; }
        LogCk("Python", pyOk, pyV);

        // Node.js (includes npm) — E6: require major >= 18
        string ndV = CheckToolVersion("Node.js", "node", "--version", "OpenJS.NodeJS.LTS", null, false);
        bool ndOk = ndV != null;
        if (ndOk)
        {
            int ndMajor = ParseMajorVersion(ndV);
            if (ndMajor >= 0 && ndMajor < 18)
            {
                _log.Warn(string.Format("Node.js {0} \u7248\u672c\u8fc7\u4f4e\uff0c\u9700\u8981 v18+\uff08\u5f53\u524d: {1}\uff09", ndV, ndMajor));
                ndOk = false;
            }
        }
        lock (_lock) { _checks["node"] = ndOk ? 1 : 2; _details["node"] = ndOk ? ndV : (ndV != null ? ndV + " (版本过低, 需 v18+)" : "未找到"); }
        LogCk("Node.js", ndOk, ndV);

        // Git
        string gtV = CheckToolVersion("Git", "git", "--version", "Git.Git", null, false);
        bool gtOk = gtV != null;
        lock (_lock) { _checks["git"] = gtOk ? 1 : 2; _details["git"] = gtOk ? gtV.Replace("git version ", "v") : "未找到"; }
        LogCk("Git", gtOk, gtV);
    }

    #endregion

    #region Deploy

    public void StartDeploy()
    {
        lock (_lock)
        {
            if (_deploying || _running) return;
            _deploying = true;
            _deployError = null;
        }

        SaveEnv();
        _log.Hl("══════════ 开始部署 ══════════");

        lock (_lock)
        {
            _progress = 0;
            foreach (string k in new List<string>(_checks.Keys))
            {
                _checks[k] = 3; _details[k] = "检测中...";
            }
        }

        ThreadPool.QueueUserWorkItem(delegate { RunDeploy(); });
    }

    private void RunDeploy()
    {
        int done = 0, total = 7;
        Action<string, bool, string> setCk = delegate(string id, bool ok, string detail)
        {
            lock (_lock)
            {
                _checks[id] = ok ? 1 : 2;
                _details[id] = detail ?? (ok ? "OK" : "缺失");
                done++;
                _progress = (int)((float)done / total * 100);
            }
        };

        // ── Python (E2: try "python" first, then "py -3" as fallback) ──
        string pyV = CheckToolVersion("Python", "python", "--version", "Python.Python.3.11", "Python 3");
        bool pyOk = pyV != null;
        if (pyOk) { _pythonExe = "python"; }
        else
        {
            string pyLV = CmdRun("py", "-3 --version");
            if (!string.IsNullOrEmpty(pyLV) && pyLV.Contains("Python 3"))
            {
                pyV = pyLV; pyOk = true; _pythonExe = "py";
                _log.Info("通过 py launcher 检测到 Python");
            }
        }
        setCk("python", pyOk, pyOk ? pyV.Replace("Python ", "v") : null);
        LogCk("Python", pyOk, pyV);

        // ── Node.js + npm (E6: require major >= 18) ──
        string ndV = CheckToolVersion("Node.js", "node", "--version", "OpenJS.NodeJS.LTS", null);
        bool ndOk = ndV != null;
        if (ndOk)
        {
            int ndMajor = ParseMajorVersion(ndV);
            if (ndMajor >= 0 && ndMajor < 18)
            {
                _log.Warn(string.Format("Node.js \u7248\u672c\u8fc7\u4f4e\uff0c\u9700\u8981 v18+ (\u5f53\u524d: v{0})", ndMajor));
                ndOk = false;
            }
        }
        string npV = CmdRun("npm", "--version");
        bool npOk = !string.IsNullOrEmpty(npV);
        setCk("node", ndOk, ndOk ? ndV : null);
        LogCk("Node.js", ndOk, ndV);
        setCk("npm", npOk, npOk ? "v" + npV : null);
        LogCk("npm", npOk, npV);

        // ── Git ──
        string gtV = CheckToolVersion("Git", "git", "--version", "Git.Git", null);
        bool gtOk = gtV != null;
        setCk("git", gtOk, gtOk ? gtV.Replace("git version ", "v") : null);
        LogCk("Git", gtOk, gtV);

        if (!pyOk || !ndOk || !npOk)
        {
            _log.Err("缺少必要环境组件，请手动安装后重试");
            lock (_lock) { _deployError = "缺少必要环境组件 (Python/Node.js/npm)，请手动安装后重试"; _deploying = false; }
            return;
        }

        // ── Clone repo if running as standalone exe ──
        if (_needsClone)
        {
            if (!gtOk)
            {
                _log.Err("单独运行模式需要 Git 来克隆仓库，请先安装 Git");
                setCk("repo", false, "需要 Git");
                lock (_lock) { _deployError = "单独运行模式需要 Git，请先安装 Git"; _deploying = false; }
                return;
            }
            bool cloneOk = CloneRepo();
            setCk("repo", cloneOk, cloneOk ? "就绪" : "克隆失败");
            if (!cloneOk)
            {
                lock (_lock) { _deployError = "仓库克隆失败，请检查网络连接"; _deploying = false; }
                return;
            }
        }
        else
        {
            setCk("repo", true, "本地已存在");
        }

        // ── Backend + Frontend in parallel ──
        bool beOk = false, feOk = false;
        _log.Hl("并行安装后端 + 前端依赖...");
        var beThread = new Thread(() => { beOk = SetupBE(); });
        var feThread = new Thread(() => { feOk = SetupFE(); });
        beThread.Start(); feThread.Start();
        beThread.Join(); feThread.Join();

        setCk("backend", beOk, beOk ? "就绪" : "失败");
        if (!beOk) { lock (_lock) { _deployError = "后端依赖安装失败，请查看日志"; _deploying = false; } return; }
        setCk("frontend", feOk, feOk ? "就绪" : "失败");
        if (!feOk) { lock (_lock) { _deployError = "前端依赖安装或构建失败，请查看日志"; _deploying = false; } return; }

        WriteInstallComplete();
        lock (_lock) { _progress = 100; _deploying = false; }
        StartServices();
    }

    private bool CloneRepo()
    {
        string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        string target = Path.Combine(exeDir, REPO_DIR_NAME);

        if (Directory.Exists(target) && File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            _log.Ok(string.Format("仓库已存在: {0}，执行 git pull 更新...", target));
            bool pullOk = RunStreamCmd("cmd.exe", string.Format("/c git -C \"{0}\" pull --ff-only", target), "git pull");
            if (!pullOk) _log.Warn("git pull 失败，继续使用现有代码");
            _root = target;
            _needsClone = false;
            _log.Hl(string.Format("项目根目录已更新: {0}", _root));
            LoadConfig();
            return true;
        }

        // E17: clean up incomplete clone (directory exists but no pyproject.toml)
        if (Directory.Exists(target))
        {
            _log.Warn(string.Format("发现不完整的目录: {0}，清理后重新克隆...", target));
            try { Directory.Delete(target, true); } catch (Exception ex)
            {
                _log.Err(string.Format("无法清理目录: {0}", ex.Message));
                return false;
            }
        }

        _log.Hl(string.Format("正在从 Gitee 克隆仓库到: {0}", target));
        _log.Info(string.Format("git clone {0}", REPO_URL_GITEE));

        bool ok = RunStreamCmd("cmd.exe",
            string.Format("/c git clone --depth 1 \"{0}\" \"{1}\"", REPO_URL_GITEE, target), "git clone");

        if (!ok || !File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            // Fallback to GitHub if Gitee fails
            _log.Warn("Gitee 克隆失败，尝试使用 GitHub...");
            _log.Info(string.Format("git clone {0}", REPO_URL));
            try { if (Directory.Exists(target)) Directory.Delete(target, true); } catch { }
            ok = RunStreamCmd("cmd.exe",
                string.Format("/c git clone --depth 1 \"{0}\" \"{1}\"", REPO_URL, target), "git clone");
        }

        if (!ok || !File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            _log.Err("仓库克隆失败，请检查网络连接或手动 git clone");
            _log.Info(string.Format("手动命令: git clone {0}", REPO_URL_GITEE));
            return false;
        }

        _root = target;
        _needsClone = false;
        _log.Ok("仓库克隆成功");
        _log.Hl(string.Format("项目根目录已更新: {0}", _root));
        LoadConfig();
        return true;
    }

    #endregion

    #region Process Utilities

    private void LogCk(string name, bool ok, string d)
    {
        if (ok) _log.Ok(string.Format("  ✓  {0}: {1}", name, d));
        else _log.Err(string.Format("  ✗  {0}: 未找到", name));
    }

    private static ProcessStartInfo MakeHiddenCmd(string exe, string args)
    {
        ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
            string.Format("/S /C \"\"{0}\" {1}\"", exe, args));
        si.RedirectStandardOutput = true;
        si.RedirectStandardError = true;
        si.UseShellExecute = false;
        si.CreateNoWindow = true;
        si.StandardOutputEncoding = Encoding.UTF8;
        si.StandardErrorEncoding = Encoding.UTF8;
        return si;
    }

    private string CmdRun(string exe, string args)
    {
        try
        {
            Process p = Process.Start(MakeHiddenCmd(exe, args));
            StringBuilder stdout = new StringBuilder();
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) lock (stdout) { stdout.AppendLine(ev.Data); }
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            if (!p.WaitForExit(15000))
            {
                try { p.Kill(); } catch { }
            }
            return stdout.ToString().Trim();
        }
        catch { return null; }
    }

    private int CmdRunExitCode(string exe, string args)
    {
        try
        {
            Process p = Process.Start(MakeHiddenCmd(exe, args));
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) { };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            if (!p.WaitForExit(15000))
            {
                try { p.Kill(); } catch { }
                return -1;
            }
            return p.ExitCode;
        }
        catch { return -1; }
    }

    private void RefreshPath()
    {
        try
        {
            string mp = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.Machine) ?? "";
            string up = Environment.GetEnvironmentVariable("PATH", EnvironmentVariableTarget.User) ?? "";
            Environment.SetEnvironmentVariable("PATH", mp + ";" + up);
        }
        catch { }
    }

    private bool? _domesticCache = null;
    private readonly object _domesticLock = new object();
    private bool IsDomesticNetwork()
    {
        lock (_domesticLock)
        {
            if (_domesticCache.HasValue) return _domesticCache.Value;
            try
            {
                double tMirror = 999, tPypi = 999;
                var t1 = new Thread(() => {
                    try {
                        var sw = System.Diagnostics.Stopwatch.StartNew();
                        using (var tcp = new System.Net.Sockets.TcpClient())
                        {
                            var ar = tcp.BeginConnect("pypi.tuna.tsinghua.edu.cn", 443, null, null);
                            if (ar.AsyncWaitHandle.WaitOne(3000))
                                tcp.EndConnect(ar);
                        }
                        sw.Stop(); tMirror = sw.Elapsed.TotalSeconds;
                    } catch { tMirror = 999; }
                });
                t1.IsBackground = true;
                var t2 = new Thread(() => {
                    try {
                        var sw = System.Diagnostics.Stopwatch.StartNew();
                        using (var tcp = new System.Net.Sockets.TcpClient())
                        {
                            var ar = tcp.BeginConnect("pypi.org", 443, null, null);
                            if (ar.AsyncWaitHandle.WaitOne(3000))
                                tcp.EndConnect(ar);
                        }
                        sw.Stop(); tPypi = sw.Elapsed.TotalSeconds;
                    } catch { tPypi = 999; }
                });
                t2.IsBackground = true;
                t1.Start(); t2.Start();
                t1.Join(4000); t2.Join(4000);
                _domesticCache = tMirror < 5 && (tPypi > 5 || tMirror < tPypi * 0.8);
                if (_domesticCache.Value)
                    _log.Info(string.Format("检测到国内网络 (mirror={0:F3}s pypi={1:F3}s)，启用镜像加速", tMirror, tPypi));
            }
            catch { _domesticCache = false; }
            return _domesticCache.Value;
        }
    }

    private bool TryAutoInstall(string name, string wingetId)
    {
        _log.Warn(string.Format("尝试自动安装 {0} ...", name));
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
                string.Format("/c winget install {0} --silent --accept-source-agreements --accept-package-agreements", wingetId));
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            Process p = Process.Start(si);
            StringBuilder stdout = new StringBuilder();
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) { lock (stdout) { stdout.AppendLine(ev.Data); } _log.Info(ev.Data); }
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) _log.Info(ev.Data);
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            bool exited = p.WaitForExit(600000);
            RefreshPath();
            if (!exited)
            {
                _log.Err(string.Format("{0} 安装超时 (10分钟)，强制终止", name));
                try { p.Kill(); } catch { }
                return false;
            }
            string output = stdout.ToString();
            if (p.ExitCode == 0 || output.Contains("Successfully") || output.Contains("installed"))
            {
                _log.Ok(string.Format("{0} 安装成功", name));
                return true;
            }
            _log.Err(string.Format("{0} 安装失败 (exit {1})", name, p.ExitCode));
            return false;
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("winget 安装异常: {0}", ex.Message));
            return false;
        }
    }

    #endregion

    #region Backend & Frontend Setup

    private bool SetupBE()
    {
        _log.Info("检查后端依赖...");
        string vd = Path.Combine(_root, ".venv");
        string vpy = Path.Combine(vd, "Scripts", "python.exe");
        if (!Directory.Exists(vd))
        {
            _log.Info("创建 Python 虚拟环境 (.venv)...");
            // E2: use detected python executable (may be "py" launcher)
            string venvArgs = _pythonExe == "py"
                ? string.Format("-3 -m venv \"{0}\"", vd)
                : string.Format("-m venv \"{0}\"", vd);
            bool venvOk = RunStreamCmd(_pythonExe, venvArgs, "venv");
            if (!venvOk || !File.Exists(vpy)) { _log.Err("虚拟环境创建失败"); return false; }
            _log.Ok("虚拟环境已创建");
        }
        else
        {
            _log.Ok(string.Format("虚拟环境已存在: {0}", vd));
        }
        int chkCode = CmdRunExitCode(vpy, "-c \"import excelmanus\"");
        if (chkCode == 0)
        {
            _log.Ok("后端依赖已安装，跳过 pip install");
            return true;
        }
        _log.Info("安装后端 Python 依赖 (pip install)，请稍候...");
        bool domestic = IsDomesticNetwork();
        string mirrorArg = domestic ? " -i https://pypi.tuna.tsinghua.edu.cn/simple" : "";
        bool pipOk = RunStreamCmd(vpy, string.Format("-m pip install -e \"{0}\"{1}", _root, mirrorArg), "pip");
        if (!pipOk && !domestic)
        {
            _log.Warn("pip 失败，尝试使用清华镜像源...");
            pipOk = RunStreamCmd(vpy, string.Format("-m pip install -e \"{0}\" -i https://pypi.tuna.tsinghua.edu.cn/simple", _root), "pip");
        }
        if (!pipOk) { _log.Err("后端依赖安装失败"); return false; }
        _log.Ok("后端依赖就绪");
        return true;
    }

    private bool RunStreamCmd(string exe, string args, string tag)
    {
        try
        {
            ProcessStartInfo si = new ProcessStartInfo(exe, args);
            si.WorkingDirectory = _root;
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            si.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Process p = Process.Start(si);
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [{0}] {1}", tag, ev.Data));
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [{0}] {1}", tag, ev.Data));
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            bool exited = p.WaitForExit(600000);
            if (!exited)
            {
                _log.Err(string.Format("{0} 超时 (10分钟)，强制终止", tag));
                try { p.Kill(); } catch { }
                return false;
            }
            return p.ExitCode == 0;
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("{0} 异常: {1}", tag, ex.Message));
            return false;
        }
    }

    private bool SetupFE()
    {
        string wd = Path.Combine(_root, "web");
        if (!Directory.Exists(wd))
        {
            _log.Err(string.Format("未找到 web 目录: {0}", wd));
            _log.Err("请确保已完整克隆仓库，或将 exe 放入仓库根目录后重试");
            return false;
        }
        if (Directory.Exists(Path.Combine(wd, "node_modules")) &&
            File.Exists(Path.Combine(wd, "node_modules", ".package-lock.json")))
        {
            _log.Ok(string.Format("前端依赖已存在: {0}", Path.Combine(wd, "node_modules")));
            return BuildFE(wd);
        }
        _log.Info("安装前端依赖 (npm install)，请稍候...");
        try
        {
            bool domestic = IsDomesticNetwork();
            string npmFirst = domestic ? "/c npm install --registry=https://registry.npmmirror.com" : "/c npm install";
            string npmFallback = domestic ? "/c npm install" : "/c npm install --registry=https://registry.npmmirror.com";
            string npmFallbackLabel = domestic ? "默认源" : "npmmirror 镜像";

            ProcessStartInfo si = new ProcessStartInfo("cmd.exe", npmFirst);
            si.WorkingDirectory = wd;
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            Process p = Process.Start(si);
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [npm] {0}", ev.Data));
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [npm] {0}", ev.Data));
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            bool exited1 = p.WaitForExit(300000);
            if (!exited1)
            {
                _log.Err("npm install 超时 (5分钟)，强制终止");
                try { p.Kill(); } catch { }
            }
            if (!exited1 || p.ExitCode != 0)
            {
                _log.Warn(string.Format("npm install 失败，尝试使用{0}重试...", npmFallbackLabel));
                ProcessStartInfo si2 = new ProcessStartInfo("cmd.exe", npmFallback);
                si2.WorkingDirectory = wd;
                si2.RedirectStandardOutput = true; si2.RedirectStandardError = true;
                si2.UseShellExecute = false; si2.CreateNoWindow = true;
                si2.StandardOutputEncoding = Encoding.UTF8; si2.StandardErrorEncoding = Encoding.UTF8;
                Process p2 = Process.Start(si2);
                p2.OutputDataReceived += delegate(object s2, DataReceivedEventArgs ev2) { if (ev2.Data != null && ev2.Data.Trim().Length > 0) _log.Info(string.Format("  [npm] {0}", ev2.Data)); };
                p2.ErrorDataReceived += delegate(object s2, DataReceivedEventArgs ev2) { if (ev2.Data != null && ev2.Data.Trim().Length > 0) _log.Info(string.Format("  [npm] {0}", ev2.Data)); };
                p2.BeginOutputReadLine(); p2.BeginErrorReadLine();
                bool exited2 = p2.WaitForExit(300000);
                if (!exited2)
                {
                    _log.Err("npm install 重试超时 (5分钟)，强制终止");
                    try { p2.Kill(); } catch { }
                    return false;
                }
                if (p2.ExitCode != 0) { _log.Err("npm install 失败"); return false; }
            }
            _log.Ok("前端依赖就绪");
            return BuildFE(wd);
        }
        catch (Exception ex) { _log.Err(string.Format("npm install 异常: {0}", ex.Message)); return false; }
    }

    private bool BuildFE(string wd)
    {
        string nextDir = Path.Combine(wd, ".next");
        if (Directory.Exists(nextDir))
        {
            _log.Ok("前端已构建，跳过 build");
            return true;
        }
        _log.Info("构建前端生产版本 (npm run build)，请稍候...");
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe", "/c npm run build");
            si.WorkingDirectory = wd;
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            si.EnvironmentVariables["BACKEND_INTERNAL_URL"] = string.Format("http://127.0.0.1:{0}", _bePort);
            si.EnvironmentVariables["NEXT_PUBLIC_BACKEND_ORIGIN"] = string.Format("http://localhost:{0}", _bePort);
            // E38: prevent OOM during Next.js build on low-memory machines
            si.EnvironmentVariables["NODE_OPTIONS"] = "--max-old-space-size=4096";
            Process p = Process.Start(si);
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [build] {0}", ev.Data));
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null && ev.Data.Trim().Length > 0)
                    _log.Info(string.Format("  [build] {0}", ev.Data));
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            bool exited = p.WaitForExit(600000);
            if (!exited)
            {
                _log.Err("npm run build 超时 (10分钟)，强制终止");
                try { p.Kill(); } catch { }
                return false;
            }
            if (p.ExitCode != 0)
            {
                _log.Err("npm run build 失败");
                return false;
            }
            _log.Ok("前端构建完成");
            return true;
        }
        catch (Exception ex) { _log.Err(string.Format("npm run build 异常: {0}", ex.Message)); return false; }
    }

    #endregion

    #region Services

    private void StartServices()
    {
        _log.Hl("启动后端服务...");
        CleanupStalePids();
        KillPort(_bePort); KillPort(_fePort);

        // Start backend process synchronously (no ThreadPool race)
        try
        {
            string vpy = Path.Combine(_root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(vpy)) vpy = "python";
            _log.Info(string.Format("后端可执行文件: {0}", vpy));
            ProcessStartInfo si = new ProcessStartInfo();
            si.FileName = vpy;
            si.Arguments = string.Format("-m uvicorn excelmanus.api:app --host 0.0.0.0 --port {0}", _bePort);
            si.WorkingDirectory = _root;
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            si.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            si.EnvironmentVariables["EXCELMANUS_CORS_ALLOW_ORIGINS"] =
                string.Format("http://localhost:{0},http://localhost:5173", _fePort);
            si.EnvironmentVariables["EXCELMANUS_AUTH_ENABLED"] = "false";
            si.EnvironmentVariables["EXCELMANUS_EXTERNAL_SAFE_MODE"] = "false";
            _procBE = Process.Start(si);
            _procBE.EnableRaisingEvents = true;
            _procBE.Exited += delegate { OnServiceCrash("后端"); };
            _procBE.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
            _procBE.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
            _procBE.BeginOutputReadLine();
            _procBE.BeginErrorReadLine();
            SavePid(_procBE.Id, "backend");
            _log.Ok(string.Format("后端已启动 → http://localhost:{0}", _bePort));
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("后端启动失败: {0}", ex.Message));
            lock (_lock) { _deployError = string.Format("后端启动失败: {0}", ex.Message); }
            return;
        }

        // Health check + frontend startup in a single background thread (backend is already started above)
        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                // Wait for backend to be ready (health check loop)
                _log.Info(string.Format("等待后端就绪 (http://localhost:{0})...", _bePort));
                bool backendReady = false;
                for (int attempt = 0; attempt < 30; attempt++)
                {
                    Thread.Sleep(2000);
                    // Early exit if backend process already died
                    if (_procBE == null || _procBE.HasExited)
                    {
                        _log.Err("后端进程已退出，请检查日志排查问题");
                        break;
                    }
                    try
                    {
                        HttpWebRequest req = (HttpWebRequest)WebRequest.Create(
                            string.Format("http://127.0.0.1:{0}/api/v1/health", _bePort));
                        req.Timeout = 3000;
                        req.Method = "GET";
                        using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
                        {
                            if ((int)resp.StatusCode == 200)
                            {
                                backendReady = true;
                                break;
                            }
                        }
                    }
                    catch { }
                    if (attempt % 5 == 4)
                        _log.Info(string.Format("  后端尚未就绪，已等待 {0} 秒...", (attempt + 1) * 2));
                }
                if (!backendReady)
                {
                    _log.Err("后端在 60 秒内未就绪，请检查日志排查问题");
                    lock (_lock) { _deployError = "后端服务启动超时，请检查 .env 配置和日志"; }
                    KillProc(_procBE); _procBE = null;
                    KillPort(_bePort);
                    return;
                }
                _log.Ok(string.Format("后端已就绪: http://localhost:{0}", _bePort));
                _log.Hl("启动前端服务 (生产模式)...");
                _log.Info(string.Format("前端目录: {0}", Path.Combine(_root, "web")));
                ProcessStartInfo fsi = new ProcessStartInfo();
                fsi.FileName = "cmd.exe";
                fsi.Arguments = string.Format("/c npm run start -- --port {0}", _fePort);
                fsi.WorkingDirectory = Path.Combine(_root, "web");
                fsi.RedirectStandardOutput = true;
                fsi.RedirectStandardError = true;
                fsi.UseShellExecute = false;
                fsi.CreateNoWindow = true;
                fsi.StandardOutputEncoding = Encoding.UTF8;
                fsi.StandardErrorEncoding = Encoding.UTF8;
                fsi.EnvironmentVariables["PORT"] = _fePort;
                fsi.EnvironmentVariables["BACKEND_INTERNAL_URL"] = string.Format("http://127.0.0.1:{0}", _bePort);
                fsi.EnvironmentVariables["NEXT_PUBLIC_BACKEND_ORIGIN"] = string.Format("http://localhost:{0}", _bePort);
                _procFE = Process.Start(fsi);
                _procFE.EnableRaisingEvents = true;
                _procFE.Exited += delegate { OnServiceCrash("前端"); };
                _procFE.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procFE.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procFE.BeginOutputReadLine();
                _procFE.BeginErrorReadLine();
                SavePid(_procFE.Id, "frontend");
                _log.Info(string.Format("等待前端就绪 (http://localhost:{0})...", _fePort));
                bool frontendReady = false;
                for (int fa = 0; fa < 30; fa++)
                {
                    Thread.Sleep(2000);
                    if (_procFE == null || _procFE.HasExited)
                    {
                        _log.Err("前端进程已退出，请检查日志排查问题");
                        break;
                    }
                    try
                    {
                        HttpWebRequest freq = (HttpWebRequest)WebRequest.Create(
                            string.Format("http://127.0.0.1:{0}/", _fePort));
                        freq.Timeout = 3000;
                        freq.Method = "GET";
                        using (HttpWebResponse fresp = (HttpWebResponse)freq.GetResponse())
                        {
                            if ((int)fresp.StatusCode < 500)
                            {
                                frontendReady = true;
                                break;
                            }
                        }
                    }
                    catch { }
                    if (fa % 5 == 4)
                        _log.Info(string.Format("  前端尚未就绪，已等待 {0} 秒...", (fa + 1) * 2));
                }
                if (!frontendReady)
                {
                    _log.Err("前端在 60 秒内未就绪，请检查日志排查问题");
                    _log.Warn("清理前端和后端进程...");
                    lock (_lock) { _deployError = "前端服务启动超时，请检查日志"; }
                    KillProc(_procFE); _procFE = null;
                    KillPort(_fePort);
                    KillProc(_procBE); _procBE = null;
                    KillPort(_bePort);
                    return;
                }
                _log.Ok(string.Format("前端已就绪 → http://localhost:{0}", _fePort));
                lock (_lock) { _running = true; }

                // 自动创建桌面快捷方式
                CreateDesktopShortcut();
            }
            catch (Exception ex)
            {
                _log.Err(string.Format("服务启动失败: {0}", ex.Message));
                lock (_lock) { _deployError = string.Format("服务启动失败: {0}", ex.Message); }
            }
        });
    }

    private void CreateDesktopShortcut()
    {
        try
        {
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            if (string.IsNullOrEmpty(desktop) || !Directory.Exists(desktop)) return;

            string lnkPath = Path.Combine(desktop, "ExcelManus.lnk");
            if (File.Exists(lnkPath))
            {
                _log.Info("桌面快捷方式已存在，跳过创建");
                return;
            }

            string startBat = Path.Combine(_root, "deploy", "start.bat");
            if (!File.Exists(startBat))
            {
                _log.Info("未找到 deploy/start.bat，跳过创建快捷方式");
                return;
            }

            // 使用 PowerShell 创建 .lnk 快捷方式
            string psCmd = string.Format(
                "$ws = New-Object -ComObject WScript.Shell; " +
                "$sc = $ws.CreateShortcut('{0}'); " +
                "$sc.TargetPath = 'cmd.exe'; " +
                "$sc.Arguments = '/c \"{1}\"'; " +
                "$sc.WorkingDirectory = '{2}'; " +
                "$sc.Description = '启动 ExcelManus'; " +
                "$sc.Save()",
                lnkPath.Replace("'", "''"),
                startBat.Replace("'", "''"),
                _root.Replace("'", "''"));

            ProcessStartInfo si = new ProcessStartInfo("powershell.exe",
                string.Format("-NoProfile -Command \"{0}\"", psCmd));
            si.CreateNoWindow = true;
            si.UseShellExecute = false;
            Process p = Process.Start(si);
            p.WaitForExit(10000);

            if (File.Exists(lnkPath))
                _log.Ok(string.Format("桌面快捷方式已创建: {0}", lnkPath));
            else
                _log.Warn("桌面快捷方式创建失败");
        }
        catch (Exception ex)
        {
            _log.Warn(string.Format("创建桌面快捷方式失败（非致命）: {0}", ex.Message));
        }
    }

    private void OnServiceCrash(string name)
    {
        bool wasRunning;
        lock (_lock) { wasRunning = _running; _running = false; }
        if (wasRunning)
        {
            _log.Err(string.Format("{0}服务异常退出！", name));
            _log.Warn("所有服务已停止，请检查日志排查问题后重新部署");
            // Kill the other process too
            try
            {
                if (name == "后端") { KillProc(_procFE); _procFE = null; KillPort(_fePort); }
                else { KillProc(_procBE); _procBE = null; KillPort(_bePort); }
            }
            catch { }
        }
        ClearPidFiles();
    }

    private string PidDir { get { return Path.Combine(_root, ".pids"); } }

    private void SavePid(int pid, string name)
    {
        try
        {
            Directory.CreateDirectory(PidDir);
            File.WriteAllText(Path.Combine(PidDir, name + ".pid"), pid.ToString());
        }
        catch { }
    }

    private void ClearPidFiles()
    {
        try
        {
            if (Directory.Exists(PidDir))
                Directory.Delete(PidDir, true);
        }
        catch { }
    }

    private void CleanupStalePids()
    {
        if (!Directory.Exists(PidDir)) return;
        foreach (string f in Directory.GetFiles(PidDir, "*.pid"))
        {
            try
            {
                int pid;
                if (int.TryParse(File.ReadAllText(f).Trim(), out pid) && pid > 0)
                {
                    _log.Warn(string.Format("发现上次残留进程 PID={0}，正在清理...", pid));
                    ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
                        string.Format("/c taskkill /T /F /PID {0} 2>nul", pid));
                    si.CreateNoWindow = true;
                    si.UseShellExecute = false;
                    Process tk = Process.Start(si);
                    tk.WaitForExit(5000);
                }
            }
            catch { }
        }
        ClearPidFiles();
    }

    public void StopServices()
    {
        _log.Warn("正在停止服务...");
        // Set _running=false BEFORE killing processes so OnServiceCrash (Exited event)
        // sees the flag and doesn't log false crash errors or kill companion processes
        lock (_lock) { _running = false; _deploying = false; }
        KillProc(_procBE); KillProc(_procFE);
        _procBE = null; _procFE = null;
        KillPort(_bePort); KillPort(_fePort);
        ClearPidFiles();
        _log.Ok("所有服务已停止");
    }

    private void KillProc(Process p)
    {
        if (p == null) return;
        try
        {
            if (!p.HasExited)
            {
                // Use taskkill /T to kill entire process tree (important for cmd.exe -> node.exe)
                try
                {
                    ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
                        string.Format("/c taskkill /T /F /PID {0} 2>nul", p.Id));
                    si.CreateNoWindow = true;
                    si.UseShellExecute = false;
                    Process tk = Process.Start(si);
                    tk.WaitForExit(5000);
                }
                catch { }
                if (!p.HasExited) { p.Kill(); }
                p.WaitForExit(3000);
            }
        }
        catch { }
    }

    private void KillPort(string port)
    {
        try
        {
            int portNum;
            if (!int.TryParse(port, out portNum) || portNum < 1 || portNum > 65535) return;
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
                string.Format("/c for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :{0} ^| findstr LISTENING') do taskkill /F /PID %a 2>nul", portNum));
            si.CreateNoWindow = true;
            si.UseShellExecute = false;
            Process p = Process.Start(si);
            p.WaitForExit(5000);
        }
        catch { }
    }

    private void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            _log.Info(string.Format("已打开: {0}", url));
        }
        catch { }
    }

    public bool IsRunning { get { lock (_lock) { return _running; } } }
    public bool IsDeploying { get { lock (_lock) { return _deploying; } } }
    public string Root { get { return _root; } }

    public string GetVersion()
    {
        return ReadVersionFromToml(Path.Combine(_root, "pyproject.toml"));
    }

    #endregion

    #region Update & Shortcut

    private string CmdRunTimeout(string exe, string args, int timeoutMs)
    {
        try
        {
            Process p = Process.Start(MakeHiddenCmd(exe, args));
            StringBuilder stdout = new StringBuilder();
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) lock (stdout) { stdout.AppendLine(ev.Data); }
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            if (!p.WaitForExit(timeoutMs))
            {
                try { p.Kill(); } catch { }
                return null;
            }
            return stdout.ToString().Trim();
        }
        catch { return null; }
    }

    private string CheckUpdateInternal(int fetchTimeoutMs)
    {
        // E48: use --depth=50 instead of --unshallow to avoid timeout on large repos
        string shallowFile = Path.Combine(_root, ".git", "shallow");
        string fetchArgs = File.Exists(shallowFile)
            ? string.Format("-C \"{0}\" fetch origin --depth=50 --tags", _root)
            : string.Format("-C \"{0}\" fetch origin --tags", _root);
        if (File.Exists(shallowFile))
            _log.Info("检测到浅克隆，使用增量获取 (depth=50)...");

        // git fetch (with timeout)
        string fetchOut = CmdRunTimeout("git", fetchArgs, fetchTimeoutMs);
        if (fetchOut == null)
            return string.Format("{{\"has_update\":false,\"timeout\":true,\"current\":\"{0}\"}}", JE(ReadVersionFromToml(Path.Combine(_root, "pyproject.toml"))));

        // current version
        string tomlPath = Path.Combine(_root, "pyproject.toml");
        string currentVer = ReadVersionFromToml(tomlPath);

        // branch (E50: detached HEAD returns "HEAD", fallback to main)
        string branch = CmdRun("git", string.Format("-C \"{0}\" rev-parse --abbrev-ref HEAD", _root));
        if (string.IsNullOrEmpty(branch) || branch == "HEAD") branch = "main";

        // commits behind
        string countStr = CmdRun("git", string.Format("-C \"{0}\" rev-list --count HEAD..origin/{1}", _root, branch));
        int behind = 0;
        int.TryParse(countStr, out behind);

        // remote version
        string latestVer = currentVer;
        if (behind > 0)
        {
            string remoteToml = CmdRun("git", string.Format("-C \"{0}\" show origin/{1}:pyproject.toml", _root, branch));
            if (!string.IsNullOrEmpty(remoteToml))
                latestVer = ParseVersionFromTomlContent(remoteToml) ?? currentVer;
        }

        bool hasUpdate = behind > 0;
        return string.Format("{{\"has_update\":{0},\"current\":\"{1}\",\"latest\":\"{2}\",\"behind\":{3}}}",
            hasUpdate ? "true" : "false", JE(currentVer), JE(latestVer), behind);
    }

    public string CheckUpdate()
    {
        try
        {
            _log.Info("检查更新...");
            string result = CheckUpdateInternal(15000);
            _log.Info(result);
            return result;
        }
        catch (Exception ex)
        {
            return string.Format("{{\"has_update\":false,\"error\":\"{0}\"}}", JE(ex.Message));
        }
    }

    public string CheckUpdateQuick()
    {
        try
        {
            _log.Info("快速检查更新（8秒超时）...");
            string result = CheckUpdateInternal(8000);
            _log.Info(result);
            return result;
        }
        catch (Exception ex)
        {
            return string.Format("{{\"has_update\":false,\"error\":\"{0}\"}}", JE(ex.Message));
        }
    }

    public string ApplyUpdate()
    {
        try
        {
            _log.Hl("═══ 开始更新 ═══");

            // read old version
            string tomlPath = Path.Combine(_root, "pyproject.toml");
            string oldVer = ReadVersionFromToml(tomlPath);

            // backup .env
            string envPath = Path.Combine(_root, ".env");
            string ts = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string backupDir = Path.Combine(_root, "backups", string.Format("backup_{0}_{1}", oldVer, ts));
            Directory.CreateDirectory(backupDir);
            if (File.Exists(envPath))
            {
                File.Copy(envPath, Path.Combine(backupDir, ".env"), true);
                _log.Ok("备份 .env");
            }

            // E35: delete install marker so QuickStart detects version mismatch
            try { if (File.Exists(InstallCompletePath)) File.Delete(InstallCompletePath); } catch { }

            // git stash + pull
            _log.Info("拉取最新代码...");
            CmdRun("git", string.Format("-C \"{0}\" stash --include-untracked", _root));
            string branch = CmdRun("git", string.Format("-C \"{0}\" rev-parse --abbrev-ref HEAD", _root));
            // E50: detached HEAD returns "HEAD", fallback to main
            if (string.IsNullOrEmpty(branch) || branch == "HEAD") branch = "main";

            string pullResult = CmdRun("git", string.Format("-C \"{0}\" pull origin {1} --ff-only", _root, branch));
            if (string.IsNullOrEmpty(pullResult) || pullResult.Contains("fatal"))
            {
                _log.Warn("fast-forward 失败，执行强制覆盖...");
                CmdRun("git", string.Format("-C \"{0}\" reset --hard origin/{1}", _root, branch));
            }
            _log.Ok("代码已更新");

            // reinstall backend deps
            _log.Info("更新后端依赖...");
            string vpy = Path.Combine(_root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(vpy)) vpy = "python";
            RunStreamCmd(vpy, string.Format("-m pip install -e \"{0}\" --quiet", _root), "pip");
            _log.Ok("后端依赖已更新");

            // rebuild frontend
            _log.Info("重新构建前端...");
            string webDir = Path.Combine(_root, "web");
            if (Directory.Exists(webDir))
            {
                RunStreamCmd("cmd.exe", string.Format("/c cd /d \"{0}\" && npm install", webDir), "npm");
                try { string nd = Path.Combine(webDir, ".next"); if (Directory.Exists(nd)) Directory.Delete(nd, true); } catch { }
                BuildFE(webDir);
            }
            _log.Ok("前端已重新构建");

            // read new version
            string newVer = ReadVersionFromToml(tomlPath);

            // E35: write new install marker after successful update
            WriteInstallComplete();

            _log.Hl(string.Format("更新成功！{0} → {1}", oldVer, newVer));
            _log.Info("请重启服务以应用更新（数据库迁移将在启动时自动执行）");
            return string.Format("{{\"success\":true,\"old_version\":\"{0}\",\"new_version\":\"{1}\"}}", JE(oldVer), JE(newVer));
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("更新失败: {0}", ex.Message));
            return string.Format("{{\"success\":false,\"error\":\"{0}\"}}", JE(ex.Message));
        }
    }

    public string CreateShortcut()
    {
        try
        {
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            if (string.IsNullOrEmpty(desktop) || !Directory.Exists(desktop))
            {
                return "{\"error\":\"未找到桌面目录\"}";
            }

            string lnkPath = Path.Combine(desktop, "ExcelManus.lnk");

            // 查找启动脚本
            string target = "";
            string startBat = Path.Combine(_root, "deploy", "start.bat");
            string exePath = Path.Combine(_root, "ExcelManus.exe");
            if (File.Exists(startBat)) target = startBat;
            else if (File.Exists(exePath)) target = exePath;

            if (string.IsNullOrEmpty(target))
            {
                return string.Format("{{\"error\":\"未找到启动脚本\"}}");
            }

            // 使用 PowerShell 创建 .lnk
            string psCmd = string.Format(
                "$ws=New-Object -ComObject WScript.Shell;" +
                "$sc=$ws.CreateShortcut('{0}');" +
                "$sc.TargetPath='cmd.exe';" +
                "$sc.Arguments='/c \"\"{1}\"\"';" +
                "$sc.WorkingDirectory='{2}';" +
                "$sc.Description='ExcelManus';" +
                "$sc.Save()",
                lnkPath.Replace("'", "''"),
                target.Replace("'", "''"),
                _root.Replace("'", "''"));

            var psi = new ProcessStartInfo("powershell", string.Format("-NoProfile -Command \"{0}\"", psCmd))
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            var proc = Process.Start(psi);
            proc.WaitForExit(10000);

            if (File.Exists(lnkPath))
            {
                _log.Ok(string.Format("桌面快捷方式已创建: {0}", lnkPath));
                return string.Format("{{\"path\":\"{0}\"}}", JE(lnkPath));
            }
            return "{\"error\":\"创建快捷方式失败\"}";
        }
        catch (Exception ex)
        {
            return string.Format("{{\"error\":\"{0}\"}}", JE(ex.Message));
        }
    }

    #endregion
}

// ═══════════════════════════════════════════════════════════
//  HTTP API Server
// ═══════════════════════════════════════════════════════════
public class WebServer
{
    private HttpListener _listener;
    private readonly Engine _engine;
    private int _port;
    private bool _alive;

    public WebServer(Engine engine) { _engine = engine; }

    public int Port { get { return _port; } }
    public string Url { get { return string.Format("http://localhost:{0}/", _port); } }

    public bool Start()
    {
        int[] ports = new int[] { 18921, 18922, 18923, 18924, 18925 };
        foreach (int p in ports)
        {
            try
            {
                _listener = new HttpListener();
                _listener.Prefixes.Add(string.Format("http://localhost:{0}/", p));
                _listener.Start();
                _port = p;
                _alive = true;
                Thread t = new Thread(ListenLoop);
                t.IsBackground = true;
                t.Start();
                _engine.Log.Hl(string.Format("部署工具 UI: http://localhost:{0}", p));
                return true;
            }
            catch { try { _listener.Close(); } catch { } }
        }
        return false;
    }

    public void Stop()
    {
        _alive = false;
        try { _listener.Stop(); } catch { }
        try { _listener.Close(); } catch { }
    }

    private void ListenLoop()
    {
        while (_alive)
        {
            try
            {
                HttpListenerContext ctx = _listener.GetContext();
                ThreadPool.QueueUserWorkItem(delegate { Handle(ctx); });
            }
            catch (ObjectDisposedException) { break; }
            catch (HttpListenerException) { break; }
            catch { }
        }
    }

    private void Handle(HttpListenerContext ctx)
    {
        try
        {
            string path = ctx.Request.Url.AbsolutePath;
            string method = ctx.Request.HttpMethod;

            if (path == "/" && method == "GET")
            {
                string html;
                try { html = EmbeddedAssets.IndexHtml; }
                catch { html = Html.Page; }
                Respond(ctx, 200, "text/html; charset=utf-8", html);
            }
            else if (path == "/api/config" && method == "GET")
                Respond(ctx, 200, "application/json", _engine.GetConfigJson());
            else if (path == "/api/status" && method == "GET")
                Respond(ctx, 200, "application/json", _engine.GetStatusJson());
            else if (path == "/api/logs" && method == "GET")
            {
                string ss = ctx.Request.QueryString["since"];
                int since = 0;
                if (!string.IsNullOrEmpty(ss)) int.TryParse(ss, out since);
                Respond(ctx, 200, "application/json", _engine.GetLogsJson(since));
            }
            else if (path == "/api/deploy" && method == "POST")
            {
                _engine.StartDeploy();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/quick-start" && method == "POST")
            {
                _engine.QuickStart();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/check-env" && method == "POST")
            {
                _engine.CheckEnv();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/stop" && method == "POST")
            {
                _engine.StopServices();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/create-shortcut" && method == "POST")
            {
                Respond(ctx, 200, "application/json", _engine.CreateShortcut());
            }
            else if (path == "/api/check-update-quick" && method == "POST")
            {
                Respond(ctx, 200, "application/json", _engine.CheckUpdateQuick());
            }
            else if (path == "/api/update-check" && method == "POST")
            {
                Respond(ctx, 200, "application/json", _engine.CheckUpdate());
            }
            else if (path == "/api/update-apply" && method == "POST")
            {
                Respond(ctx, 200, "application/json", _engine.ApplyUpdate());
            }
            else if (path == "/favicon.ico")
            {
                ServeFavicon(ctx);
            }
            else
                Respond(ctx, 404, "text/plain", "Not Found");
        }
        catch (Exception ex)
        {
            try { Respond(ctx, 500, "text/plain", ex.Message); } catch { }
        }
    }

    private void Respond(HttpListenerContext ctx, int code, string ct, string body)
    {
        byte[] buf = Encoding.UTF8.GetBytes(body);
        ctx.Response.StatusCode = code;
        ctx.Response.ContentType = ct;
        ctx.Response.ContentLength64 = buf.Length;
        ctx.Response.OutputStream.Write(buf, 0, buf.Length);
        ctx.Response.Close();
    }

    private void ServeFavicon(HttpListenerContext ctx)
    {
        try
        {
            foreach (string p in Program.GetIconCandidates())
            {
                if (File.Exists(p))
                {
                    byte[] data = File.ReadAllBytes(p);
                    ctx.Response.StatusCode = 200;
                    ctx.Response.ContentType = "image/x-icon";
                    ctx.Response.ContentLength64 = data.Length;
                    ctx.Response.OutputStream.Write(data, 0, data.Length);
                    ctx.Response.Close();
                    return;
                }
            }
        }
        catch { }
        ctx.Response.StatusCode = 204;
        ctx.Response.Close();
    }

    private string ReadBody(HttpListenerContext ctx)
    {
        using (StreamReader r = new StreamReader(ctx.Request.InputStream, Encoding.UTF8))
        { return r.ReadToEnd(); }
    }

    private static string JVal(string json, string key)
    {
        if (string.IsNullOrEmpty(json)) return null;
        string search = "\"" + key + "\":";
        int idx = json.IndexOf(search);
        if (idx < 0) return null;
        int start = idx + search.Length;
        while (start < json.Length && json[start] == ' ') start++;
        if (start >= json.Length) return null;
        if (json[start] == '"')
        {
            int end = start + 1;
            while (end < json.Length && json[end] != '"') { if (json[end] == '\\') end++; end++; }
            return json.Substring(start + 1, end - start - 1);
        }
        int e2 = start;
        while (e2 < json.Length && json[e2] != ',' && json[e2] != '}') e2++;
        return json.Substring(start, e2 - start).Trim();
    }
}

// ═══════════════════════════════════════════════════════════
//  Modern Tray Menu Styling (matches frontend brand colors)
// ═══════════════════════════════════════════════════════════
public class ModernColorTable : ProfessionalColorTable
{
    public override Color MenuBorder { get { return Color.FromArgb(229, 231, 235); } }
    public override Color MenuItemBorder { get { return Color.Transparent; } }
    public override Color MenuItemSelected { get { return Color.Transparent; } }
    public override Color ToolStripDropDownBackground { get { return Color.White; } }
    public override Color ImageMarginGradientBegin { get { return Color.White; } }
    public override Color ImageMarginGradientMiddle { get { return Color.White; } }
    public override Color ImageMarginGradientEnd { get { return Color.White; } }
    public override Color MenuItemSelectedGradientBegin { get { return Color.Transparent; } }
    public override Color MenuItemSelectedGradientEnd { get { return Color.Transparent; } }
    public override Color MenuItemPressedGradientBegin { get { return Color.Transparent; } }
    public override Color MenuItemPressedGradientEnd { get { return Color.Transparent; } }
    public override Color SeparatorDark { get { return Color.FromArgb(240, 242, 244); } }
    public override Color SeparatorLight { get { return Color.White; } }
}

public class ModernMenuRenderer : ToolStripProfessionalRenderer
{
    // Brand colors matching frontend --em-primary / --em-primary-light / --em-primary-dark
    private static readonly Color BrandGreen = Color.FromArgb(33, 115, 70);
    private static readonly Color HoverBg = Color.FromArgb(236, 247, 241);
    private static readonly Color HoverBorder = Color.FromArgb(200, 230, 214);
    private static readonly Color ExitHoverBg = Color.FromArgb(254, 242, 242);
    private static readonly Color ExitHoverBorder = Color.FromArgb(252, 220, 220);
    private static readonly Color ExitRed = Color.FromArgb(209, 52, 56);
    private static readonly Color TextMain = Color.FromArgb(26, 26, 26);
    private static readonly Color TextSub = Color.FromArgb(156, 163, 175);
    private static readonly Color SepColor = Color.FromArgb(240, 242, 244);
    private static readonly Color HeaderBg = Color.FromArgb(247, 251, 249);

    public ModernMenuRenderer() : base(new ModernColorTable()) { }

    protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e)
    {
        using (SolidBrush b = new SolidBrush(Color.White))
            e.Graphics.FillRectangle(b, e.AffectedBounds);
    }

    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e)
    {
        // Subtle border matching frontend --brd: #e5e7eb
        using (Pen p = new Pen(Color.FromArgb(30, 0, 0, 0)))
        {
            Rectangle r = new Rectangle(0, 0, e.AffectedBounds.Width - 1, e.AffectedBounds.Height - 1);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using (GraphicsPath path = RoundedRect(r, 8))
                e.Graphics.DrawPath(p, path);
        }
    }

    protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e)
    {
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        string tag = e.Item.Tag != null ? e.Item.Tag.ToString() : "";

        // Header area: gradient green-tinted background
        if (tag == "header")
        {
            Rectangle hrc = new Rectangle(4, 0, e.Item.Width - 8, e.Item.Height);
            using (LinearGradientBrush b = new LinearGradientBrush(hrc,
                Color.FromArgb(240, 249, 244), Color.FromArgb(228, 242, 234), 135f))
            using (GraphicsPath p = RoundedRect(hrc, 6))
                e.Graphics.FillPath(b, p);
            return;
        }

        if (!e.Item.Enabled) return;

        if (e.Item.Selected || e.Item.Pressed)
        {
            Rectangle rc = new Rectangle(5, 2, e.Item.Width - 10, e.Item.Height - 4);
            bool isDanger = tag == "exit" || tag == "stop";
            Color bg, brd, accent;
            if (isDanger)
            { bg = ExitHoverBg; brd = ExitHoverBorder; accent = ExitRed; }
            else
            { bg = HoverBg; brd = HoverBorder; accent = BrandGreen; }

            // Lighter top for gradient hover effect
            Color bgTop = isDanger
                ? Color.FromArgb(255, 249, 249)
                : Color.FromArgb(244, 251, 248);

            using (GraphicsPath path = RoundedRect(rc, 7))
            {
                using (LinearGradientBrush gr = new LinearGradientBrush(rc, bgTop, bg, 90f))
                    e.Graphics.FillPath(gr, path);
                using (Pen pen = new Pen(brd))
                    e.Graphics.DrawPath(pen, path);
            }

            // Left accent bar
            int barH = rc.Height - 14;
            if (barH > 4)
            {
                int barY = rc.Y + (rc.Height - barH) / 2;
                using (SolidBrush ab = new SolidBrush(accent))
                    e.Graphics.FillRectangle(ab, 8, barY, 3, barH);
            }
        }
    }

    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e)
    {
        string tag = e.Item.Tag != null ? e.Item.Tag.ToString() : "";
        if (tag == "header")
        {
            e.TextColor = BrandGreen;
            e.TextFont = new Font("Segoe UI", 10.5f, FontStyle.Bold);
        }
        else if (tag == "subtitle")
        {
            e.TextColor = TextSub;
            e.TextFont = new Font("Segoe UI", 8.25f);
        }
        else if (tag == "status")
        {
            // Green when running (● present), gray when stopped
            bool running = e.Item.Text.IndexOf("\u25CF") >= 0;
            e.TextColor = running ? BrandGreen : TextSub;
            e.TextFont = new Font("Segoe UI", 8.25f, running ? FontStyle.Bold : FontStyle.Regular);
        }
        else if (tag == "exit" || tag == "stop")
        {
            e.TextColor = (e.Item.Selected || e.Item.Pressed) ? ExitRed : TextMain;
            e.TextFont = new Font("Segoe UI", 9.5f);
        }
        else
        {
            e.TextColor = TextMain;
            e.TextFont = new Font("Segoe UI", 9.5f);
        }
        base.OnRenderItemText(e);
    }

    protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e)
    {
        int y = e.Item.Height / 2;
        using (Pen p = new Pen(SepColor))
            e.Graphics.DrawLine(p, 20, y, e.Item.Width - 20, y);
    }

    protected override void OnRenderImageMargin(ToolStripRenderEventArgs e)
    {
        // Suppress default gray image margin
    }

    private GraphicsPath RoundedRect(Rectangle rect, int r)
    {
        GraphicsPath p = new GraphicsPath();
        int d = r * 2;
        p.AddArc(rect.X, rect.Y, d, d, 180, 90);
        p.AddArc(rect.Right - d, rect.Y, d, d, 270, 90);
        p.AddArc(rect.Right - d, rect.Bottom - d, d, d, 0, 90);
        p.AddArc(rect.X, rect.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }
}

// ═══════════════════════════════════════════════════════════
//  Tray Icon
// ═══════════════════════════════════════════════════════════
public class AppTray : ApplicationContext
{
    private NotifyIcon _tray;
    private WebServer _server;
    private Engine _engine;
    private ToolStripMenuItem _statusItem;
    private ToolStripMenuItem _toggleItem;
    private System.Windows.Forms.Timer _statusTimer;

    public AppTray(WebServer server, Engine engine)
    {
        _server = server;
        _engine = engine;

        _tray = new NotifyIcon();
        _tray.Icon = MakeIcon();
        _tray.Text = "ExcelManus - \u90E8\u7F72\u5DE5\u5177";
        _tray.Visible = true;

        ContextMenuStrip menu = BuildModernMenu();
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick += delegate { OpenUI(); };

        _tray.ShowBalloonTip(2000, "ExcelManus",
            string.Format("\u90E8\u7F72\u5DE5\u5177\u5DF2\u542F\u52A8\nhttp://localhost:{0}", server.Port),
            ToolTipIcon.Info);

        // Timer to refresh status indicator
        _statusTimer = new System.Windows.Forms.Timer();
        _statusTimer.Interval = 2000;
        _statusTimer.Tick += delegate { RefreshStatus(); };
        _statusTimer.Start();
    }

    private ContextMenuStrip BuildModernMenu()
    {
        ContextMenuStrip menu = new ContextMenuStrip();
        menu.Renderer = new ModernMenuRenderer();
        menu.BackColor = Color.White;
        menu.ShowImageMargin = false;
        menu.Padding = new Padding(2, 8, 2, 8);
        menu.AutoSize = false;
        menu.Width = 280;

        // Apply rounded corners + drop shadow when menu opens
        menu.Opening += delegate
        {
            try
            {
                IntPtr rgn = CreateRoundRectRgn(0, 0, menu.Width + 1, menu.Height + 1, 14, 14);
                if (rgn != IntPtr.Zero) SetWindowRgn(menu.Handle, rgn, true);
                // CS_DROPSHADOW for subtle popup shadow
                int style = GetClassLong(menu.Handle, -26);
                if ((style & 0x20000) == 0)
                    SetClassLong(menu.Handle, -26, style | 0x20000);
            }
            catch { }
        };

        // ── Brand Header ──
        ToolStripMenuItem headerItem = new ToolStripMenuItem("\u2009\u25C6\u2009ExcelManus");
        headerItem.Tag = "header";
        headerItem.Enabled = false;
        headerItem.Padding = new Padding(6, 10, 6, 0);
        menu.Items.Add(headerItem);

        // Subtitle with version
        string ver = _engine.GetVersion();
        ToolStripMenuItem subItem = new ToolStripMenuItem(string.Format("\u2009\u2009\u2009\u2009v{0} \u00B7 \u90E8\u7F72\u5DE5\u5177", ver));
        subItem.Tag = "subtitle";
        subItem.Enabled = false;
        subItem.Padding = new Padding(6, 0, 6, 2);
        menu.Items.Add(subItem);

        // Status indicator
        _statusItem = new ToolStripMenuItem(GetStatusText());
        _statusItem.Tag = "status";
        _statusItem.Enabled = false;
        _statusItem.Padding = new Padding(6, 0, 6, 8);
        menu.Items.Add(_statusItem);

        menu.Items.Add(new ToolStripSeparator());

        // ── Primary Actions ──
        ToolStripMenuItem openItem = new ToolStripMenuItem("\u2009\u25B6\u2009 \u6253\u5F00 ExcelManus");
        openItem.Padding = new Padding(4, 6, 4, 6);
        openItem.Click += delegate { OpenUI(); };
        menu.Items.Add(openItem);

        ToolStripMenuItem panelItem = new ToolStripMenuItem("\u2009\u25B8\u2009 \u7BA1\u7406\u9762\u677F");
        panelItem.Padding = new Padding(4, 6, 4, 6);
        panelItem.Click += delegate { OpenPanel(); };
        menu.Items.Add(panelItem);

        menu.Items.Add(new ToolStripSeparator());

        // ── Service Control ──
        _toggleItem = new ToolStripMenuItem(GetToggleText());
        _toggleItem.Tag = GetToggleTag();
        _toggleItem.Padding = new Padding(4, 6, 4, 6);
        _toggleItem.Click += delegate { ToggleService(); };
        menu.Items.Add(_toggleItem);

        ToolStripMenuItem folderItem = new ToolStripMenuItem("\u2009\u25B8\u2009 \u6253\u5F00\u9879\u76EE\u76EE\u5F55");
        folderItem.Padding = new Padding(4, 6, 4, 6);
        folderItem.Click += delegate { OpenFolder(); };
        menu.Items.Add(folderItem);

        menu.Items.Add(new ToolStripSeparator());

        // ── Exit ──
        ToolStripMenuItem exitItem = new ToolStripMenuItem("\u2009\u00D7\u2009 \u9000\u51FA");
        exitItem.Tag = "exit";
        exitItem.Padding = new Padding(4, 6, 4, 6);
        exitItem.Click += delegate { ExitApp(); };
        menu.Items.Add(exitItem);

        return menu;
    }

    private string GetStatusText()
    {
        if (_engine.IsRunning)
            return "\u2009\u2009\u2009\u2009\u25CF \u670D\u52A1\u8FD0\u884C\u4E2D";
        if (_engine.IsDeploying)
            return "\u2009\u2009\u2009\u2009\u25D4 \u90E8\u7F72\u4E2D...";
        return "\u2009\u2009\u2009\u2009\u25CB \u670D\u52A1\u672A\u542F\u52A8";
    }

    private string GetToggleText()
    {
        if (_engine.IsRunning)
            return "\u2009\u25A0\u2009 \u505C\u6B62\u670D\u52A1";
        return "\u2009\u25B6\u2009 \u542F\u52A8\u670D\u52A1";
    }

    private string GetToggleTag()
    {
        return _engine.IsRunning ? "stop" : "start";
    }

    private void RefreshStatus()
    {
        if (_statusItem != null)
        {
            string text = GetStatusText();
            if (_statusItem.Text != text) _statusItem.Text = text;
        }
        if (_toggleItem != null)
        {
            string toggleText = GetToggleText();
            if (_toggleItem.Text != toggleText)
            {
                _toggleItem.Text = toggleText;
                _toggleItem.Tag = GetToggleTag();
            }
            _toggleItem.Enabled = !_engine.IsDeploying;
        }
        _tray.Text = _engine.IsRunning ? "ExcelManus - \u8FD0\u884C\u4E2D" : "ExcelManus - \u5DF2\u505C\u6B62";
    }

    private void OpenUI()
    {
        try { Program.LaunchAppMode(_server.Url); } catch { }
    }

    private void OpenPanel()
    {
        try { Process.Start(new ProcessStartInfo(_server.Url) { UseShellExecute = true }); } catch { }
    }

    private void ToggleService()
    {
        if (_engine.IsDeploying) return;
        if (_engine.IsRunning)
        {
            _engine.StopServices();
            _tray.ShowBalloonTip(1500, "ExcelManus", "\u670D\u52A1\u5DF2\u505C\u6B62", ToolTipIcon.Info);
        }
        else
        {
            _engine.QuickStart();
            _tray.ShowBalloonTip(1500, "ExcelManus", "\u6B63\u5728\u542F\u52A8\u670D\u52A1...", ToolTipIcon.Info);
        }
        RefreshStatus();
    }

    private void OpenFolder()
    {
        try
        {
            string root = _engine.Root;
            if (root != null && Directory.Exists(root))
                Process.Start(new ProcessStartInfo(root) { UseShellExecute = true });
        }
        catch { }
    }

    private void ExitApp()
    {
        _statusTimer.Stop();
        _statusTimer.Dispose();
        if (_engine.IsRunning) _engine.StopServices();
        _server.Stop();
        _tray.Visible = false;
        _tray.Dispose();
        Application.Exit();
    }

    private Icon MakeIcon()
    {
        // Try to load brand icon from file (icon.ico next to exe, or in deploy/)
        try
        {
            foreach (string p in Program.GetIconCandidates())
            {
                if (File.Exists(p))
                    return new Icon(p, 32, 32);
            }
        }
        catch { }

        // Fallback: programmatic icon
        using (Bitmap bmp = new Bitmap(32, 32))
        {
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);
                PointF[] d = new PointF[] {
                    new PointF(16, 2), new PointF(30, 16), new PointF(16, 30), new PointF(2, 16)
                };
                using (LinearGradientBrush b = new LinearGradientBrush(
                    new Rectangle(0, 0, 32, 32), Color.FromArgb(51, 168, 103), Color.FromArgb(33, 115, 70), 135f))
                { g.FillPolygon(b, d); }
            }
            IntPtr hIcon = bmp.GetHicon();
            Icon tmp = Icon.FromHandle(hIcon);
            Icon result = (Icon)tmp.Clone();
            tmp.Dispose();
            DestroyIcon(hIcon);
            return result;
        }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Auto)]
    private static extern bool DestroyIcon(IntPtr handle);

    [System.Runtime.InteropServices.DllImport("gdi32.dll")]
    private static extern IntPtr CreateRoundRectRgn(int x1, int y1, int x2, int y2, int cx, int cy);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int SetWindowRgn(IntPtr hWnd, IntPtr hRgn, bool bRedraw);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int GetClassLong(IntPtr hWnd, int nIndex);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern int SetClassLong(IntPtr hWnd, int nIndex, int dwNewLong);
}

// ═══════════════════════════════════════════════════════════
//  Entry Point
// ═══════════════════════════════════════════════════════════
public static class Program
{
    public static string[] GetIconCandidates()
    {
        string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        return new string[] {
            Path.Combine(exeDir, "icon.ico"),
            Path.Combine(exeDir, "deploy", "icon.ico"),
            Path.Combine(exeDir, "..", "deploy", "icon.ico"),
            Path.Combine(exeDir, "web", "public", "favicon.ico"),
            Path.Combine(exeDir, "..", "web", "public", "favicon.ico")
        };
    }

    public static void LaunchAppMode(string url)
    {
        string[] candidates = new string[] {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Microsoft", "Edge", "Application", "msedge.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                "Google", "Chrome", "Application", "chrome.exe"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Google", "Chrome", "Application", "chrome.exe")
        };
        foreach (string exe in candidates)
        {
            if (File.Exists(exe))
            {
                try
                {
                    ProcessStartInfo si = new ProcessStartInfo(exe,
                        string.Format("--app={0} --window-size=1100,780 --disable-extensions", url));
                    si.UseShellExecute = false;
                    Process.Start(si);
                    return;
                }
                catch { }
            }
        }
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
    }

    [STAThread]
    public static void Main()
    {
        bool created;
        Mutex mutex = new Mutex(true, "ExcelManus_SingleInstance", out created);
        if (!created)
        {
            MessageBox.Show("部署工具已在运行中。\n请检查系统托盘图标。",
                "ExcelManus", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        try
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            Engine engine = new Engine();
            WebServer server = new WebServer(engine);

            if (!server.Start())
            {
                MessageBox.Show("无法启动本地服务器，端口可能被占用。",
                    "ExcelManus", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            try { LaunchAppMode(server.Url); } catch { }

            Application.Run(new AppTray(server, engine));
        }
        finally { GC.KeepAlive(mutex); mutex.ReleaseMutex(); }
    }
}
