/*
 * ExcelManus Deploy Tool - Web UI (Project-Style Light Theme)
 * C# exe with embedded HTTP server + browser-rendered HTML/CSS UI
 * Zero external dependencies - uses built-in .NET Framework
 * Compile: csc.exe /langversion:5 /target:winexe /out:ExcelManusDeployTool.exe ExcelManusSetup.cs
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
<title>ExcelManus Deploy Tool</title>
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
body{background:var(--bg);color:var(--t1);display:flex;flex-direction:column;min-height:100%}

.hdr{background:var(--card);border-bottom:1px solid var(--brd);padding:0 28px;height:54px;display:flex;align-items:center;gap:14px;flex-shrink:0;position:relative}
.hdr::after{content:'';position:absolute;bottom:-1px;left:0;width:120px;height:2px;background:linear-gradient(90deg,var(--g),transparent);border-radius:2px}
.logo{width:34px;height:34px;position:relative;flex-shrink:0}
.logo::before{content:'';position:absolute;inset:0;background:linear-gradient(135deg,var(--gl),var(--g));clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%)}
.logo::after{content:'';position:absolute;inset:5px;background:rgba(255,255,255,.25);clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%)}
.brand{display:flex;align-items:baseline;gap:10px}
.brand h1{font-size:17px;font-weight:700;letter-spacing:-.3px}
.brand span{font-size:11px;color:var(--t3);font-weight:500;letter-spacing:.5px}

/* Steps indicator */
.steps-bar{display:flex;align-items:center;justify-content:center;gap:0;padding:20px 20px 4px;flex-shrink:0}
.step-ind{display:flex;align-items:center;gap:0}
.step-dot{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;border:2px solid var(--brd2);color:var(--t3);background:var(--card);transition:all .3s;flex-shrink:0}
.step-dot.active{border-color:var(--g);color:#fff;background:var(--g);box-shadow:0 2px 8px rgba(33,115,70,.3)}
.step-dot.done{border-color:var(--g);color:#fff;background:var(--gl)}
.step-line{width:60px;height:2px;background:var(--brd2);transition:background .3s}
.step-line.done{background:var(--gl)}
.step-label{font-size:11px;color:var(--t3);text-align:center;margin-top:6px;font-weight:500}
.step-label.active{color:var(--g);font-weight:700}
.step-col{display:flex;flex-direction:column;align-items:center}

/* Content area */
.content{flex:1;max-width:580px;width:100%;margin:0 auto;padding:16px 20px 30px}
.card{background:var(--card);border:1px solid var(--brd);border-radius:var(--r);overflow:hidden}
.card-t{font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:1.5px;padding:14px 18px 10px;border-bottom:1px solid var(--brd);display:flex;align-items:center;gap:8px}
.card-t i{color:var(--g);font-style:normal;font-size:13px}
.card-body{padding:16px 18px}

/* Step panels */
.step-panel{display:none}.step-panel.show{display:block}

/* Env check items */
.env-item{display:flex;align-items:center;padding:12px 0;border-bottom:1px solid var(--bg)}
.env-item:last-child{border-bottom:none}
.env-icon{width:36px;height:36px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:18px;margin-right:14px;flex-shrink:0;background:var(--bg)}
.env-icon.ok{background:#e8f5e9;color:var(--g)}
.env-icon.fail{background:#fde8e8;color:var(--red)}
.env-icon.wait{background:#fff8e1;color:var(--gold)}
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
.fi:focus{border-color:var(--g);box-shadow:0 0 0 3px var(--ga1)}
.fi::placeholder{color:var(--t4)}
select.fi{cursor:pointer;appearance:none;background-image:url(""data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M3 5l3 3 3-3' stroke='%239ca3af' fill='none' stroke-width='1.5'/%3E%3C/svg%3E"");background-repeat:no-repeat;background-position:right 12px center}
.help-link{font-size:11px;color:var(--cyan);text-decoration:none;margin-left:auto;font-weight:500}.help-link:hover{text-decoration:underline}

/* Buttons */
.btn{padding:10px 24px;border-radius:var(--r2);font-size:14px;font-weight:600;border:none;cursor:pointer;transition:all .2s;display:inline-flex;align-items:center;gap:8px;font-family:inherit}
.btn:active{transform:scale(.97)}
.b1{background:linear-gradient(135deg,var(--g),var(--gd));color:#fff;box-shadow:0 2px 8px rgba(33,115,70,.2)}
.b1:hover{box-shadow:0 4px 14px rgba(33,115,70,.28);transform:translateY(-1px)}
.b2{background:var(--red);color:#fff}.b2:hover{background:var(--redl)}
.b3{background:var(--bg);color:var(--g);border:1.5px solid var(--ga3)}.b3:hover{background:var(--ga1)}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none!important;box-shadow:none!important}
.btn-row{display:flex;gap:10px;margin-top:18px;justify-content:flex-end}
.btn-big{width:100%;justify-content:center;padding:14px;font-size:16px;border-radius:var(--r)}

/* Test result */
.test-msg{margin-top:8px;padding:8px 12px;border-radius:6px;font-size:12px;font-weight:500;display:none}
.test-msg.ok{display:block;background:#e8f5e9;color:var(--gd)}.test-msg.fail{display:block;background:#fde8e8;color:var(--red)}

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
.success-icon{font-size:56px;margin-bottom:12px}
.success-title{font-size:22px;font-weight:700;color:var(--g);margin-bottom:6px}
.success-sub{font-size:14px;color:var(--t3);margin-bottom:24px}

/* Advanced toggle */
.adv-toggle{font-size:12px;color:var(--t3);cursor:pointer;user-select:none;display:flex;align-items:center;gap:4px;margin-top:12px;padding-top:12px;border-top:1px solid var(--brd)}
.adv-toggle:hover{color:var(--t2)}
.adv-body{display:none;margin-top:10px}.adv-body.show{display:block}
.pr{display:grid;grid-template-columns:1fr 1fr;gap:10px}
</style>
</head>
<body>
<div class='hdr'>
  <div class='logo'></div>
  <div class='brand'><h1>ExcelManus</h1><span>Deploy Tool &middot; v2.0</span></div>
</div>

<div class='steps-bar'>
  <div class='step-col'><div class='step-dot active' id='sd1'>1</div><div class='step-label active' id='sl1'>\u73AF\u5883\u68C0\u6D4B</div></div>
  <div class='step-ind'><div class='step-line' id='sln1'></div></div>
  <div class='step-col'><div class='step-dot' id='sd2'>2</div><div class='step-label' id='sl2'>\u914D\u7F6E LLM</div></div>
  <div class='step-ind'><div class='step-line' id='sln2'></div></div>
  <div class='step-col'><div class='step-dot' id='sd3'>3</div><div class='step-label' id='sl3'>\u542F\u52A8\u90E8\u7F72</div></div>
</div>

<div class='content'>
  <!-- ══ Step 1: Environment Check ══ -->
  <div class='step-panel show' id='p1'>
    <div class='card'>
      <div class='card-t'><i>&#128269;</i> \u6B63\u5728\u68C0\u6D4B\u60A8\u7684\u7535\u8111\u73AF\u5883...</div>
      <div class='card-body' id='env-list'></div>
    </div>
    <div class='btn-row'>
      <button class='btn b3' id='btnRecheck' onclick='doCheckEnv()' style='display:none'>&#x21BB; \u91CD\u65B0\u68C0\u6D4B</button>
      <button class='btn b1' id='btnNext1' onclick='goStep(2)' disabled>\u4E0B\u4E00\u6B65 &#8594;</button>
    </div>
  </div>

  <!-- ══ Step 2: LLM Config ══ -->
  <div class='step-panel' id='p2'>
    <div class='card'>
      <div class='card-t'><i>&#9881;</i> \u914D\u7F6E AI \u6A21\u578B</div>
      <div class='card-body'>
        <div class='fg'>
          <div class='fl'>\u9009\u62E9\u670D\u52A1\u63D0\u4F9B\u5546</div>
          <select class='fi' id='f_provider' onchange='onProvider()'>
            <option value=''>\u2014 \u8BF7\u9009\u62E9 \u2014</option>
            <option value='deepseek'>DeepSeek (\u63A8\u8350\u56FD\u5185\u7528\u6237)</option>
            <option value='siliconflow'>\u7845\u57FA\u6D41\u52A8 SiliconFlow</option>
            <option value='openai'>OpenAI</option>
            <option value='custom'>\u81EA\u5B9A\u4E49 / \u5176\u4ED6\u63D0\u4F9B\u5546</option>
          </select>
        </div>
        <div class='fg'>
          <div class='fl'>API Key <a class='help-link' id='helpLink' href='#' target='_blank' style='display:none'>\u2753 \u5982\u4F55\u83B7\u53D6?</a></div>
          <input class='fi' type='password' id='f_key' placeholder='\u8BF7\u8F93\u5165\u60A8\u7684 API Key'>
        </div>
        <div class='fg' id='fgUrl' style='display:none'>
          <div class='fl'>Base URL</div>
          <input class='fi' id='f_url' placeholder='https://api.example.com/v1'>
        </div>
        <div class='fg'>
          <div class='fl'>\u6A21\u578B</div>
          <select class='fi' id='f_model_sel' style='display:none'></select>
          <input class='fi' id='f_model' placeholder='\u6A21\u578B\u540D\u79F0' style='display:none'>
        </div>
        <button class='btn b3' id='btnTest' onclick='doTestLLM()' style='margin-top:4px' disabled>&#128268; \u6D4B\u8BD5\u8FDE\u63A5</button>
        <div class='test-msg' id='testMsg'></div>

        <div class='adv-toggle' onclick='toggleAdv()'>&#9881; \u9AD8\u7EA7\u8BBE\u7F6E <span id='advArr'>&#9654;</span></div>
        <div class='adv-body' id='advBody'>
          <div class='pr'>
            <div class='fg'><div class='fl'>\u540E\u7AEF\u7AEF\u53E3</div><input class='fi' id='f_bp' value='8000'></div>
            <div class='fg'><div class='fl'>\u524D\u7AEF\u7AEF\u53E3</div><input class='fi' id='f_fp' value='3000'></div>
          </div>
        </div>
      </div>
    </div>
    <div class='btn-row'>
      <button class='btn b3' onclick='goStep(1)'>&#8592; \u4E0A\u4E00\u6B65</button>
      <button class='btn b1' id='btnNext2' onclick='goStep(3)' disabled>\u4E0B\u4E00\u6B65 &#8594;</button>
    </div>
  </div>

  <!-- ══ Step 3: Deploy ══ -->
  <div class='step-panel' id='p3'>
    <div class='card' id='deployCard'>
      <div class='card-body'>
        <div id='preDeployView' style='text-align:center;padding:20px 0'>
          <div style='font-size:42px;margin-bottom:10px'>&#128640;</div>
          <div style='font-size:18px;font-weight:700;margin-bottom:6px'>\u4E00\u5207\u5C31\u7EEA\uFF0C\u51C6\u5907\u90E8\u7F72\uFF01</div>
          <div style='font-size:13px;color:var(--t3);margin-bottom:20px'>\u70B9\u51FB\u4E0B\u65B9\u6309\u94AE\uFF0C\u81EA\u52A8\u5B8C\u6210\u6240\u6709\u5B89\u88C5\u4E0E\u542F\u52A8</div>
          <button class='btn b1 btn-big' id='btnDeploy' onclick='doDeploy()'>&#9654; \u5F00\u59CB\u90E8\u7F72</button>
        </div>
        <div id='deployingView' style='display:none'>
          <div class='deploy-stage' id='dStage'>\u6B63\u5728\u51C6\u5907...</div>
          <div class='deploy-sub' id='dSub'>\u8BF7\u7A0D\u5019\uFF0C\u9996\u6B21\u90E8\u7F72\u9884\u8BA1\u9700\u8981 3-8 \u5206\u949F</div>
          <div class='pbar'><div class='pfill' id='pf'></div></div>
          <div class='log-toggle' onclick='toggleLog()'>\u{1F4CB} <span id='logToggleText'>\u5C55\u5F00\u8BE6\u7EC6\u65E5\u5FD7</span></div>
          <div class='lcon' id='lc'></div>
        </div>
        <div id='successView' style='display:none'>
          <div class='success-box'>
            <div class='success-icon'>&#127881;</div>
            <div class='success-title'>\u90E8\u7F72\u6210\u529F\uFF01</div>
            <div class='success-sub'>\u670D\u52A1\u5DF2\u542F\u52A8\uFF0C\u70B9\u51FB\u4E0B\u65B9\u6309\u94AE\u6253\u5F00 ExcelManus</div>
            <button class='btn b1 btn-big' onclick='doOpen()'>&#127760; \u6253\u5F00 ExcelManus</button>
            <div style='margin-top:14px'>
              <button class='btn b2' onclick='doStop()'>&#9724; \u505C\u6B62\u670D\u52A1</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class='btn-row' id='step3Back'>
      <button class='btn b3' onclick='goStep(2)'>&#8592; \u4E0A\u4E00\u6B65</button>
    </div>
  </div>
</div>

<script>
/* ── Provider presets ── */
var PROVIDERS={
  deepseek:{name:'DeepSeek',url:'https://api.deepseek.com/v1',models:['deepseek-chat','deepseek-reasoner'],help:'https://platform.deepseek.com/api_keys'},
  siliconflow:{name:'\u7845\u57FA\u6D41\u52A8',url:'https://api.siliconflow.cn/v1',models:['Qwen/Qwen2.5-72B-Instruct','deepseek-ai/DeepSeek-V3','deepseek-ai/DeepSeek-R1'],help:'https://cloud.siliconflow.cn/account/ak'},
  openai:{name:'OpenAI',url:'https://api.openai.com/v1',models:['gpt-4o','gpt-4o-mini','o3-mini'],help:'https://platform.openai.com/api-keys'}
};
var ENV_ITEMS=[
  {id:'python',name:'Python 3.x',ico:'&#128013;',dl:'https://www.python.org/downloads/'},
  {id:'node',name:'Node.js',ico:'&#9889;',dl:'https://nodejs.org/zh-cn/download/'},
  {id:'git',name:'Git',ico:'&#128230;',dl:'https://git-scm.com/download/win'}
];
var curStep=1,logIdx=0,autoOpen=true,deploying=false,deployDone=false;

function init(){
  buildEnvList();
  fetch('/api/config').then(function(r){return r.json()}).then(function(d){
    if(d.apiKey)document.getElementById('f_key').value=d.apiKey;
    if(d.baseUrl)document.getElementById('f_url').value=d.baseUrl;
    if(d.model)document.getElementById('f_model').value=d.model;
    if(d.bePort)document.getElementById('f_bp').value=d.bePort;
    if(d.fePort)document.getElementById('f_fp').value=d.fePort;
    if(d.autoOpen!==undefined)autoOpen=d.autoOpen;
    guessProvider(d.baseUrl);
  }).catch(function(){});
  setInterval(pollLogs,600);
  setInterval(pollSt,900);
  doCheckEnv();
}

function guessProvider(url){
  if(!url)return;
  var sel=document.getElementById('f_provider');
  for(var k in PROVIDERS){if(PROVIDERS[k].url===url){sel.value=k;onProvider();return;}}
  sel.value='custom';onProvider();
}

function buildEnvList(){
  var h='';
  for(var i=0;i<ENV_ITEMS.length;i++){
    var e=ENV_ITEMS[i];
    h+='<div class=""env-item""><div class=""env-icon wait"" id=""ei_'+e.id+'""><div class=""spinner""></div></div>';
    h+='<div class=""env-info""><div class=""env-name"">'+e.name+'</div>';
    h+='<div class=""env-detail"" id=""ed_'+e.id+'"">\u68C0\u6D4B\u4E2D...</div></div></div>';
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
    if(st===1){el.className='env-icon ok';el.innerHTML='&#10004;';dl.textContent=details[e.id]||'\u5DF2\u5C31\u7EEA';}
    else if(st===2){el.className='env-icon fail';el.innerHTML='&#10008;';dl.innerHTML='\u672A\u627E\u5230 \u2014 <a href=""'+e.dl+'"" target=""_blank"">\u70B9\u6B64\u624B\u52A8\u4E0B\u8F7D\u5B89\u88C5</a>';allOk=false;}
    else if(st===3){el.className='env-icon wait';el.innerHTML='<div class=""spinner""></div>';dl.textContent=details[e.id]||'\u68C0\u6D4B\u4E2D...';}
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
  for(var i=1;i<=3;i++){
    document.getElementById('p'+i).className='step-panel'+(i===n?' show':'');
    var d=document.getElementById('sd'+i);
    var l=document.getElementById('sl'+i);
    if(i<n){d.className='step-dot done';d.innerHTML='&#10004;';l.className='step-label';}
    else if(i===n){d.className='step-dot active';d.innerHTML=i;l.className='step-label active';}
    else{d.className='step-dot';d.innerHTML=i;l.className='step-label';}
  }
  if(document.getElementById('sln1'))document.getElementById('sln1').className='step-line'+(n>1?' done':'');
  if(document.getElementById('sln2'))document.getElementById('sln2').className='step-line'+(n>2?' done':'');
}

/* ── Provider selection ── */
function onProvider(){
  var v=document.getElementById('f_provider').value;
  var p=PROVIDERS[v];
  var urlG=document.getElementById('fgUrl');
  var mSel=document.getElementById('f_model_sel');
  var mInp=document.getElementById('f_model');
  var hl=document.getElementById('helpLink');
  if(p){
    urlG.style.display='none';
    document.getElementById('f_url').value=p.url;
    mSel.innerHTML='';for(var i=0;i<p.models.length;i++){var o=document.createElement('option');o.value=p.models[i];o.textContent=p.models[i];mSel.appendChild(o);}
    mSel.style.display='';mInp.style.display='none';
    hl.href=p.help;hl.style.display='';
  }else if(v==='custom'){
    urlG.style.display='';mSel.style.display='none';mInp.style.display='';hl.style.display='none';
  }else{
    urlG.style.display='none';mSel.style.display='none';mInp.style.display='none';hl.style.display='none';
  }
  checkStep2Ready();
}

function getModel(){
  var mSel=document.getElementById('f_model_sel');
  if(mSel.style.display!=='none')return mSel.value;
  return document.getElementById('f_model').value;
}

function checkStep2Ready(){
  var key=document.getElementById('f_key').value.trim();
  var prov=document.getElementById('f_provider').value;
  document.getElementById('btnTest').disabled=!key||!prov;
  document.getElementById('btnNext2').disabled=!key||!prov;
}
document.getElementById('f_key').addEventListener('input',checkStep2Ready);
document.getElementById('f_model').addEventListener('input',checkStep2Ready);

/* ── Test LLM ── */
function doTestLLM(){
  var msg=document.getElementById('testMsg');
  msg.className='test-msg';msg.style.display='none';
  document.getElementById('btnTest').disabled=true;
  document.getElementById('btnTest').textContent='\u6D4B\u8BD5\u4E2D...';
  var body=JSON.stringify({apiKey:document.getElementById('f_key').value,baseUrl:document.getElementById('f_url').value,model:getModel()});
  fetch('/api/test-llm',{method:'POST',headers:{'Content-Type':'application/json'},body:body})
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.ok){msg.className='test-msg ok';msg.textContent='\u2705 \u8FDE\u63A5\u6210\u529F\uFF01\u6A21\u578B\u54CD\u5E94\u6B63\u5E38\u3002';}
      else{msg.className='test-msg fail';msg.textContent='\u274C \u8FDE\u63A5\u5931\u8D25: '+(d.error||'\u8BF7\u68C0\u67E5 API Key \u548C\u7F51\u7EDC');}
    })
    .catch(function(){msg.className='test-msg fail';msg.textContent='\u274C \u7F51\u7EDC\u8BF7\u6C42\u5931\u8D25';})
    .finally(function(){document.getElementById('btnTest').disabled=false;document.getElementById('btnTest').innerHTML='&#128268; \u6D4B\u8BD5\u8FDE\u63A5';});
}

/* ── Deploy ── */
function doDeploy(){
  document.getElementById('preDeployView').style.display='none';
  document.getElementById('deployingView').style.display='';
  document.getElementById('step3Back').style.display='none';
  deploying=true;
  var body=JSON.stringify({apiKey:document.getElementById('f_key').value,baseUrl:document.getElementById('f_url').value,model:getModel(),bePort:document.getElementById('f_bp').value,fePort:document.getElementById('f_fp').value,autoOpen:autoOpen});
  fetch('/api/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:body}).catch(function(){});
}

var STAGE_NAMES={0:'\u6B63\u5728\u51C6\u5907...',14:'\u6B63\u5728\u4E0B\u8F7D\u6E90\u7801...',28:'\u6B63\u5728\u4E0B\u8F7D\u6E90\u7801...',42:'\u6B63\u5728\u5B89\u88C5\u540E\u7AEF\u4F9D\u8D56...',57:'\u6B63\u5728\u5B89\u88C5\u540E\u7AEF\u4F9D\u8D56...',71:'\u6B63\u5728\u5B89\u88C5\u524D\u7AEF\u4F9D\u8D56...',85:'\u6B63\u5728\u542F\u52A8\u670D\u52A1...',100:'\u90E8\u7F72\u5B8C\u6210\uFF01'};
function closestStage(p){var best='';for(var k in STAGE_NAMES){if(parseInt(k)<=p)best=STAGE_NAMES[k];}return best||'\u6B63\u5728\u90E8\u7F72...';}

function doStop(){fetch('/api/stop',{method:'POST'}).then(function(){
  deploying=false;deployDone=false;
  document.getElementById('preDeployView').style.display='';
  document.getElementById('deployingView').style.display='none';
  document.getElementById('successView').style.display='none';
  document.getElementById('step3Back').style.display='';
});}
function doOpen(){var p=document.getElementById('f_fp').value||'3000';window.open('http://localhost:'+p,'_blank');}

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
      document.getElementById('pf').style.width=pct+'%';
      document.getElementById('dStage').textContent=closestStage(pct);
      if(d.running&&!deployDone){
        deployDone=true;
        document.getElementById('deployingView').style.display='none';
        document.getElementById('successView').style.display='';
      }
    }
  }).catch(function(){});
}

function toggleLog(){
  var el=document.getElementById('lc');
  var t=document.getElementById('logToggleText');
  if(el.className.indexOf('show')>=0){el.className='lcon';t.textContent='\u5C55\u5F00\u8BE6\u7EC6\u65E5\u5FD7';}
  else{el.className='lcon show';t.textContent='\u6536\u8D77\u65E5\u5FD7';el.scrollTop=el.scrollHeight;}
}
function toggleAdv(){
  var el=document.getElementById('advBody');
  var a=document.getElementById('advArr');
  if(el.className.indexOf('show')>=0){el.className='adv-body';a.innerHTML='&#9654;';}
  else{el.className='adv-body show';a.innerHTML='&#9660;';}
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
    private bool _needsClone;
    private readonly Dictionary<string, int> _checks = new Dictionary<string, int>();
    private readonly Dictionary<string, string> _details = new Dictionary<string, string>();
    private int _progress;

    private string _apiKey, _baseUrl, _model, _bePort, _fePort;
    private bool _autoOpen;

    public LogStore Log { get { return _log; } }

    public Engine()
    {
        _log = new LogStore();
        _apiKey = ""; _baseUrl = ""; _model = "";
        _bePort = "8000"; _fePort = "3000"; _autoOpen = true;

        string[] ids = new string[] { "python", "node", "npm", "git", "repo", "backend", "frontend" };
        foreach (string id in ids)
        {
            _checks[id] = 0;
            _details[id] = "\u5F85\u68C0\u6D4B";
        }
        DetectRoot();
        LoadConfig();
    }

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
                    _log.Warn("\u672A\u627E\u5230\u9879\u76EE\u6587\u4EF6\uFF0C\u5C06\u5728\u90E8\u7F72\u65F6\u81EA\u52A8\u4ECE GitHub \u514B\u9686");
                }
            }
        }
        _log.Hl(string.Format("\u9879\u76EE\u6839\u76EE\u5F55: {0}", _root));
    }

    private string EnvPath { get { return Path.Combine(_root, ".env"); } }

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
                string v = ln.Substring(eq + 1).Trim();
                if (k == "EXCELMANUS_API_KEY") _apiKey = v;
                else if (k == "EXCELMANUS_BASE_URL") _baseUrl = v;
                else if (k == "EXCELMANUS_MODEL") _model = v;
            }
            _log.Info("\u5DF2\u52A0\u8F7D .env \u914D\u7F6E");
        }
        catch (Exception ex) { _log.Warn(string.Format("\u8BFB\u53D6 .env \u5931\u8D25: {0}", ex.Message)); }
    }

    public void SetConfig(string apiKey, string baseUrl, string model, string bePort, string fePort, bool autoOpen)
    {
        _apiKey = apiKey ?? ""; _baseUrl = baseUrl ?? ""; _model = model ?? "";
        _bePort = string.IsNullOrEmpty(bePort) ? "8000" : bePort;
        _fePort = string.IsNullOrEmpty(fePort) ? "3000" : fePort;
        _autoOpen = autoOpen;
    }

    private void SaveEnv()
    {
        try
        {
            // Merge mode: read existing .env, update/add keys, preserve everything else
            Dictionary<string, string> envMap = new Dictionary<string, string>();
            List<string> orderedKeys = new List<string>();
            List<string> comments = new List<string>();

            if (File.Exists(EnvPath))
            {
                foreach (string raw in File.ReadAllLines(EnvPath))
                {
                    string ln = raw.Trim();
                    if (string.IsNullOrEmpty(ln) || ln.StartsWith("#"))
                    {
                        comments.Add(raw);
                        continue;
                    }
                    int eq = ln.IndexOf('=');
                    if (eq <= 0) { comments.Add(raw); continue; }
                    string k = ln.Substring(0, eq).Trim();
                    string v = ln.Substring(eq + 1).Trim();
                    envMap[k] = v;
                    if (!orderedKeys.Contains(k)) orderedKeys.Add(k);
                }
            }

            // Upsert user-provided values
            UpsertEnv(envMap, orderedKeys, "EXCELMANUS_API_KEY", _apiKey);
            UpsertEnv(envMap, orderedKeys, "EXCELMANUS_BASE_URL", _baseUrl);
            UpsertEnv(envMap, orderedKeys, "EXCELMANUS_MODEL", _model);

            // Essential defaults for new installations
            EnsureEnvDefault(envMap, orderedKeys, "EXCELMANUS_CORS_ALLOW_ORIGINS",
                string.Format("http://localhost:{0},http://localhost:5173", _fePort));
            EnsureEnvDefault(envMap, orderedKeys, "EXCELMANUS_AUTH_ENABLED", "false");
            EnsureEnvDefault(envMap, orderedKeys, "EXCELMANUS_EXTERNAL_SAFE_MODE", "false");

            // Write back
            List<string> lines = new List<string>();
            foreach (string c in comments) lines.Add(c);
            foreach (string k in orderedKeys)
            {
                lines.Add(string.Format("{0}={1}", k, envMap[k]));
            }
            File.WriteAllLines(EnvPath, lines.ToArray(), Encoding.UTF8);
            _log.Ok("\u5DF2\u4FDD\u5B58 .env");
        }
        catch (Exception ex) { _log.Err(string.Format("\u4FDD\u5B58 .env \u5931\u8D25: {0}", ex.Message)); }
    }

    private void UpsertEnv(Dictionary<string, string> map, List<string> keys, string k, string v)
    {
        map[k] = v;
        if (!keys.Contains(k)) keys.Add(k);
    }

    private void EnsureEnvDefault(Dictionary<string, string> map, List<string> keys, string k, string v)
    {
        if (!map.ContainsKey(k))
        {
            map[k] = v;
            if (!keys.Contains(k)) keys.Add(k);
        }
    }

    public string GetConfigJson()
    {
        return string.Format("{{\"apiKey\":\"{0}\",\"baseUrl\":\"{1}\",\"model\":\"{2}\",\"bePort\":\"{3}\",\"fePort\":\"{4}\",\"autoOpen\":{5}}}",
            JE(_apiKey), JE(_baseUrl), JE(_model), JE(_bePort), JE(_fePort), _autoOpen ? "true" : "false");
    }

    public string GetStatusJson()
    {
        lock (_lock)
        {
            StringBuilder sb = new StringBuilder();
            sb.Append("{\"running\":"); sb.Append(_running ? "true" : "false");
            sb.Append(",\"deploying\":"); sb.Append(_deploying ? "true" : "false");
            sb.Append(",\"progress\":"); sb.Append(_progress);
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

    public void CheckEnv()
    {
        lock (_lock)
        {
            _checks["python"] = 3; _details["python"] = "\u68C0\u6D4B\u4E2D...";
            _checks["node"] = 3; _details["node"] = "\u68C0\u6D4B\u4E2D...";
            _checks["git"] = 3; _details["git"] = "\u68C0\u6D4B\u4E2D...";
        }
        ThreadPool.QueueUserWorkItem(delegate { RunCheckEnv(); });
    }

    private void RunCheckEnv()
    {
        // Python
        string pyV = CmdRun("python", "--version");
        bool pyOk = !string.IsNullOrEmpty(pyV) && pyV.Contains("Python 3");
        if (!pyOk)
        {
            if (TryAutoInstall("Python", "Python.Python.3.11"))
            {
                pyV = CmdRun("python", "--version");
                pyOk = !string.IsNullOrEmpty(pyV) && pyV.Contains("Python 3");
            }
        }
        lock (_lock) { _checks["python"] = pyOk ? 1 : 2; _details["python"] = pyOk ? pyV.Replace("Python ", "v") : "\u672A\u627E\u5230"; }
        LogCk("Python", pyOk, pyV);

        // Node.js (includes npm)
        string ndV = CmdRun("node", "--version");
        bool ndOk = !string.IsNullOrEmpty(ndV);
        if (!ndOk)
        {
            if (TryAutoInstall("Node.js", "OpenJS.NodeJS.LTS"))
            {
                ndV = CmdRun("node", "--version");
                ndOk = !string.IsNullOrEmpty(ndV);
            }
        }
        lock (_lock) { _checks["node"] = ndOk ? 1 : 2; _details["node"] = ndOk ? ndV : "\u672A\u627E\u5230"; }
        LogCk("Node.js", ndOk, ndV);

        // Git
        string gtV = CmdRun("git", "--version");
        bool gtOk = !string.IsNullOrEmpty(gtV);
        if (!gtOk)
        {
            if (TryAutoInstall("Git", "Git.Git"))
            {
                gtV = CmdRun("git", "--version");
                gtOk = !string.IsNullOrEmpty(gtV);
            }
        }
        lock (_lock) { _checks["git"] = gtOk ? 1 : 2; _details["git"] = gtOk ? gtV.Replace("git version ", "v") : "\u672A\u627E\u5230"; }
        LogCk("Git", gtOk, gtV);
    }

    public string TestLLM(string apiKey, string baseUrl, string model)
    {
        try
        {
            string url = baseUrl.TrimEnd('/') + "/chat/completions";
            HttpWebRequest req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "POST";
            req.ContentType = "application/json";
            req.Timeout = 15000;
            req.Headers.Add("Authorization", "Bearer " + apiKey);
            string payload = string.Format(
                "{{\"model\":\"{0}\",\"messages\":[{{\"role\":\"user\",\"content\":\"Hi\"}}],\"max_tokens\":5}}",
                model.Replace("\"", "\\\""));
            byte[] data = Encoding.UTF8.GetBytes(payload);
            req.ContentLength = data.Length;
            using (Stream s = req.GetRequestStream()) { s.Write(data, 0, data.Length); }
            using (HttpWebResponse resp = (HttpWebResponse)req.GetResponse())
            {
                if ((int)resp.StatusCode >= 200 && (int)resp.StatusCode < 300)
                    return "{\"ok\":true}";
                return string.Format("{{\"ok\":false,\"error\":\"HTTP {0}\"}}", (int)resp.StatusCode);
            }
        }
        catch (WebException wex)
        {
            string msg = "\u7F51\u7EDC\u8BF7\u6C42\u5931\u8D25";
            if (wex.Response != null)
            {
                try
                {
                    using (StreamReader sr = new StreamReader(wex.Response.GetResponseStream(), Encoding.UTF8))
                    {
                        string body = sr.ReadToEnd();
                        int sc = (int)((HttpWebResponse)wex.Response).StatusCode;
                        if (sc == 401) msg = "API Key \u65E0\u6548\u6216\u5DF2\u8FC7\u671F";
                        else if (sc == 404) msg = "\u6A21\u578B\u4E0D\u5B58\u5728\u6216 Base URL \u9519\u8BEF";
                        else msg = string.Format("HTTP {0}", sc);
                    }
                }
                catch { }
            }
            return string.Format("{{\"ok\":false,\"error\":\"{0}\"}}", JE(msg));
        }
        catch (Exception ex)
        {
            return string.Format("{{\"ok\":false,\"error\":\"{0}\"}}", JE(ex.Message));
        }
    }

    public void StartDeploy()
    {
        lock (_lock)
        {
            if (_deploying || _running) return;
            _deploying = true;
        }

        SaveEnv();
        _log.Hl("\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550 \u5F00\u59CB\u90E8\u7F72 \u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550");

        lock (_lock)
        {
            _progress = 0;
            foreach (string k in new List<string>(_checks.Keys))
            {
                _checks[k] = 3; _details[k] = "\u68C0\u6D4B\u4E2D...";
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
                _details[id] = detail ?? (ok ? "OK" : "\u7F3A\u5931");
                done++;
                _progress = (int)((float)done / total * 100);
            }
        };

        // ── Python ──
        string pyV = CmdRun("python", "--version");
        bool pyOk = !string.IsNullOrEmpty(pyV) && pyV.Contains("Python 3");
        if (!pyOk)
        {
            if (TryAutoInstall("Python", "Python.Python.3.11"))
            {
                pyV = CmdRun("python", "--version");
                pyOk = !string.IsNullOrEmpty(pyV) && pyV.Contains("Python 3");
            }
        }
        setCk("python", pyOk, pyOk ? pyV.Replace("Python ", "v") : null);
        LogCk("Python", pyOk, pyV);

        // ── Node.js + npm ──
        string ndV = CmdRun("node", "--version");
        bool ndOk = !string.IsNullOrEmpty(ndV);
        string npV = CmdRun("npm", "--version");
        bool npOk = !string.IsNullOrEmpty(npV);
        if (!ndOk || !npOk)
        {
            if (TryAutoInstall("Node.js", "OpenJS.NodeJS.LTS"))
            {
                ndV = CmdRun("node", "--version");
                ndOk = !string.IsNullOrEmpty(ndV);
                npV = CmdRun("npm", "--version");
                npOk = !string.IsNullOrEmpty(npV);
            }
        }
        setCk("node", ndOk, ndOk ? ndV : null);
        LogCk("Node.js", ndOk, ndV);
        setCk("npm", npOk, npOk ? "v" + npV : null);
        LogCk("npm", npOk, npV);

        // ── Git ──
        string gtV = CmdRun("git", "--version");
        bool gtOk = !string.IsNullOrEmpty(gtV);
        if (!gtOk)
        {
            if (TryAutoInstall("Git", "Git.Git"))
            {
                gtV = CmdRun("git", "--version");
                gtOk = !string.IsNullOrEmpty(gtV);
            }
        }
        setCk("git", gtOk, gtOk ? gtV.Replace("git version ", "v") : null);
        LogCk("Git", gtOk, gtV);

        if (!pyOk || !ndOk || !npOk)
        {
            _log.Err("\u7F3A\u5C11\u5FC5\u8981\u73AF\u5883\u7EC4\u4EF6\uFF0C\u8BF7\u624B\u52A8\u5B89\u88C5\u540E\u91CD\u8BD5");
            lock (_lock) { _deploying = false; }
            return;
        }

        // ── Clone repo if running as standalone exe ──
        if (_needsClone)
        {
            if (!gtOk)
            {
                _log.Err("\u5355\u72EC\u8FD0\u884C\u6A21\u5F0F\u9700\u8981 Git \u6765\u514B\u9686\u4ED3\u5E93\uFF0C\u8BF7\u5148\u5B89\u88C5 Git");
                setCk("repo", false, "\u9700\u8981 Git");
                lock (_lock) { _deploying = false; }
                return;
            }
            bool cloneOk = CloneRepo();
            setCk("repo", cloneOk, cloneOk ? "\u5C31\u7EEA" : "\u514B\u9686\u5931\u8D25");
            if (!cloneOk)
            {
                lock (_lock) { _deploying = false; }
                return;
            }
        }
        else
        {
            setCk("repo", true, "\u672C\u5730\u5DF2\u5B58\u5728");
        }

        bool beOk = SetupBE();
        setCk("backend", beOk, beOk ? "\u5C31\u7EEA" : "\u5931\u8D25");
        if (!beOk) { lock (_lock) { _deploying = false; } return; }

        bool feOk = SetupFE();
        setCk("frontend", feOk, feOk ? "\u5C31\u7EEA" : "\u5931\u8D25");
        if (!feOk) { lock (_lock) { _deploying = false; } return; }

        lock (_lock) { _progress = 100; _deploying = false; }
        StartServices();
    }

    private bool CloneRepo()
    {
        string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
        string target = Path.Combine(exeDir, REPO_DIR_NAME);

        if (Directory.Exists(target) && File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            _log.Ok(string.Format("\u4ED3\u5E93\u5DF2\u5B58\u5728: {0}\uFF0C\u6267\u884C git pull \u66F4\u65B0...", target));
            bool pullOk = RunStreamCmd("cmd.exe", string.Format("/c git -C \"{0}\" pull --ff-only", target), "git pull");
            if (!pullOk) _log.Warn("git pull \u5931\u8D25\uFF0C\u7EE7\u7EED\u4F7F\u7528\u73B0\u6709\u4EE3\u7801");
            _root = target;
            _needsClone = false;
            _log.Hl(string.Format("\u9879\u76EE\u6839\u76EE\u5F55\u5DF2\u66F4\u65B0: {0}", _root));
            LoadConfig();
            return true;
        }

        _log.Hl(string.Format("\u6B63\u5728\u4ECE GitHub \u514B\u9686\u4ED3\u5E93\u5230: {0}", target));
        _log.Info(string.Format("git clone {0}", REPO_URL));

        bool ok = RunStreamCmd("cmd.exe",
            string.Format("/c git clone --depth 1 \"{0}\" \"{1}\"", REPO_URL, target), "git clone");

        if (!ok || !File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            // Fallback to Gitee mirror for users in China
            _log.Warn("GitHub \u514B\u9686\u5931\u8D25\uFF0C\u5C1D\u8BD5\u4F7F\u7528 Gitee \u955C\u50CF...");
            _log.Info(string.Format("git clone {0}", REPO_URL_GITEE));
            try { if (Directory.Exists(target)) Directory.Delete(target, true); } catch { }
            ok = RunStreamCmd("cmd.exe",
                string.Format("/c git clone --depth 1 \"{0}\" \"{1}\"", REPO_URL_GITEE, target), "git clone");
        }

        if (!ok || !File.Exists(Path.Combine(target, "pyproject.toml")))
        {
            _log.Err("\u4ED3\u5E93\u514B\u9686\u5931\u8D25\uFF0C\u8BF7\u68C0\u67E5\u7F51\u7EDC\u8FDE\u63A5\u6216\u624B\u52A8 git clone");
            _log.Info(string.Format("\u624B\u52A8\u547D\u4EE4: git clone {0}", REPO_URL));
            return false;
        }

        _root = target;
        _needsClone = false;
        _log.Ok("\u4ED3\u5E93\u514B\u9686\u6210\u529F");
        _log.Hl(string.Format("\u9879\u76EE\u6839\u76EE\u5F55\u5DF2\u66F4\u65B0: {0}", _root));
        LoadConfig();
        return true;
    }

    private void LogCk(string name, bool ok, string d)
    {
        if (ok) _log.Ok(string.Format("  \u2713  {0}: {1}", name, d));
        else _log.Err(string.Format("  \u2717  {0}: \u672A\u627E\u5230", name));
    }

    private string CmdRun(string exe, string args)
    {
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe", "/c " + exe + " " + args);
            si.RedirectStandardOutput = true;
            si.RedirectStandardError = true;
            si.UseShellExecute = false;
            si.CreateNoWindow = true;
            si.StandardOutputEncoding = Encoding.UTF8;
            si.StandardErrorEncoding = Encoding.UTF8;
            Process p = Process.Start(si);
            string o = p.StandardOutput.ReadToEnd().Trim();
            p.WaitForExit(15000);
            return o;
        }
        catch { return null; }
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

    private bool TryAutoInstall(string name, string wingetId)
    {
        _log.Warn(string.Format("\u5C1D\u8BD5\u81EA\u52A8\u5B89\u88C5 {0} ...", name));
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
            string stdout = "";
            p.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) { stdout += ev.Data + "\n"; _log.Info(ev.Data); }
            };
            p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) {
                if (ev.Data != null) _log.Info(ev.Data);
            };
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            p.WaitForExit(600000);
            RefreshPath();
            if (p.ExitCode == 0 || stdout.Contains("Successfully") || stdout.Contains("installed"))
            {
                _log.Ok(string.Format("{0} \u5B89\u88C5\u6210\u529F", name));
                return true;
            }
            _log.Err(string.Format("{0} \u5B89\u88C5\u5931\u8D25 (exit {1})", name, p.ExitCode));
            return false;
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("winget \u5B89\u88C5\u5F02\u5E38: {0}", ex.Message));
            return false;
        }
    }

    private bool SetupBE()
    {
        _log.Info("\u68C0\u67E5\u540E\u7AEF\u4F9D\u8D56...");
        string vd = Path.Combine(_root, ".venv");
        string vpy = Path.Combine(vd, "Scripts", "python.exe");
        if (!Directory.Exists(vd))
        {
            _log.Info("\u521B\u5EFA Python \u865A\u62DF\u73AF\u5883 (.venv)...");
            CmdRun("python", string.Format("-m venv \"{0}\"", vd));
            if (!File.Exists(vpy)) { _log.Err("\u865A\u62DF\u73AF\u5883\u521B\u5EFA\u5931\u8D25"); return false; }
            _log.Ok("\u865A\u62DF\u73AF\u5883\u5DF2\u521B\u5EFA");
        }
        else
        {
            _log.Ok(string.Format("\u865A\u62DF\u73AF\u5883\u5DF2\u5B58\u5728: {0}", vd));
        }
        string chk = CmdRun(vpy, "-c \"import excelmanus\"");
        if (chk != null)
        {
            _log.Ok("\u540E\u7AEF\u4F9D\u8D56\u5DF2\u5B89\u88C5\uFF0C\u8DF3\u8FC7 pip install");
            return true;
        }
        _log.Info("\u5B89\u88C5\u540E\u7AEF Python \u4F9D\u8D56 (pip install)\uFF0C\u8BF7\u7A0D\u5019...");
        bool pipOk = RunStreamCmd(vpy, string.Format("-m pip install -e \"{0}\"", _root), "pip");
        if (!pipOk)
        {
            _log.Warn("pip \u5931\u8D25\uFF0C\u5C1D\u8BD5\u4F7F\u7528\u6E05\u534E\u955C\u50CF\u6E90...");
            pipOk = RunStreamCmd(vpy, string.Format("-m pip install -e \"{0}\" -i https://pypi.tuna.tsinghua.edu.cn/simple", _root), "pip");
        }
        if (!pipOk) { _log.Err("\u540E\u7AEF\u4F9D\u8D56\u5B89\u88C5\u5931\u8D25"); return false; }
        _log.Ok("\u540E\u7AEF\u4F9D\u8D56\u5C31\u7EEA");
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
            p.WaitForExit(600000);
            return p.ExitCode == 0;
        }
        catch (Exception ex)
        {
            _log.Err(string.Format("{0} \u5F02\u5E38: {1}", tag, ex.Message));
            return false;
        }
    }

    private bool SetupFE()
    {
        string wd = Path.Combine(_root, "web");
        if (!Directory.Exists(wd))
        {
            _log.Err(string.Format("\u672A\u627E\u5230 web \u76EE\u5F55: {0}", wd));
            _log.Err("\u8BF7\u786E\u4FDD\u5DF2\u5B8C\u6574\u514B\u9686\u4ED3\u5E93\uFF0C\u6216\u5C06 exe \u653E\u5165\u4ED3\u5E93\u6839\u76EE\u5F55\u540E\u91CD\u8BD5");
            return false;
        }
        if (Directory.Exists(Path.Combine(wd, "node_modules")))
        {
            _log.Ok(string.Format("\u524D\u7AEF\u4F9D\u8D56\u5DF2\u5B58\u5728: {0}", Path.Combine(wd, "node_modules")));
            return true;
        }
        _log.Info("\u5B89\u88C5\u524D\u7AEF\u4F9D\u8D56 (npm install)\uFF0C\u8BF7\u7A0D\u5019...");
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe", "/c npm install --registry=https://registry.npmmirror.com");
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
            p.WaitForExit(300000);
            if (p.ExitCode != 0)
            {
                _log.Warn("npm install \u5931\u8D25\uFF0C\u5C1D\u8BD5\u4F7F\u7528\u9ED8\u8BA4\u6E90\u91CD\u8BD5...");
                ProcessStartInfo si2 = new ProcessStartInfo("cmd.exe", "/c npm install");
                si2.WorkingDirectory = wd;
                si2.RedirectStandardOutput = true; si2.RedirectStandardError = true;
                si2.UseShellExecute = false; si2.CreateNoWindow = true;
                si2.StandardOutputEncoding = Encoding.UTF8; si2.StandardErrorEncoding = Encoding.UTF8;
                Process p2 = Process.Start(si2);
                p2.OutputDataReceived += delegate(object s2, DataReceivedEventArgs ev2) { if (ev2.Data != null && ev2.Data.Trim().Length > 0) _log.Info(string.Format("  [npm] {0}", ev2.Data)); };
                p2.ErrorDataReceived += delegate(object s2, DataReceivedEventArgs ev2) { if (ev2.Data != null && ev2.Data.Trim().Length > 0) _log.Info(string.Format("  [npm] {0}", ev2.Data)); };
                p2.BeginOutputReadLine(); p2.BeginErrorReadLine();
                p2.WaitForExit(300000);
                if (p2.ExitCode != 0) { _log.Err("npm install \u5931\u8D25"); return false; }
            }
            _log.Ok("\u524D\u7AEF\u4F9D\u8D56\u5C31\u7EEA");
            return true;
        }
        catch (Exception ex) { _log.Err(string.Format("npm install \u5F02\u5E38: {0}", ex.Message)); return false; }
    }

    private void StartServices()
    {
        lock (_lock) { _running = true; }
        _log.Hl("\u542F\u52A8\u540E\u7AEF\u670D\u52A1...");
        KillPort(_bePort); KillPort(_fePort);

        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                string vpy = Path.Combine(_root, ".venv", "Scripts", "python.exe");
                if (!File.Exists(vpy)) vpy = "python";
                _log.Info(string.Format("\u540E\u7AEF\u53EF\u6267\u884C\u6587\u4EF6: {0}", vpy));
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
                si.EnvironmentVariables["EXCELMANUS_API_KEY"] = _apiKey;
                si.EnvironmentVariables["EXCELMANUS_BASE_URL"] = _baseUrl;
                si.EnvironmentVariables["EXCELMANUS_MODEL"] = _model;
                si.EnvironmentVariables["EXCELMANUS_CORS_ALLOW_ORIGINS"] =
                    string.Format("http://localhost:{0},http://localhost:5173", _fePort);
                si.EnvironmentVariables["EXCELMANUS_AUTH_ENABLED"] = "false";
                si.EnvironmentVariables["EXCELMANUS_EXTERNAL_SAFE_MODE"] = "false";
                _procBE = Process.Start(si);
                _procBE.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procBE.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procBE.BeginOutputReadLine();
                _procBE.BeginErrorReadLine();
                _log.Ok(string.Format("\u540E\u7AEF\u5DF2\u542F\u52A8 \u2192 http://localhost:{0}", _bePort));
            }
            catch (Exception ex) { _log.Err(string.Format("\u540E\u7AEF\u542F\u52A8\u5931\u8D25: {0}", ex.Message)); }
        });

        ThreadPool.QueueUserWorkItem(delegate
        {
            try
            {
                // Wait for backend to be ready (health check loop)
                _log.Info(string.Format("\u7B49\u5F85\u540E\u7AEF\u5C31\u7EEA (http://localhost:{0})...", _bePort));
                bool backendReady = false;
                for (int attempt = 0; attempt < 30; attempt++)
                {
                    Thread.Sleep(2000);
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
                        _log.Info(string.Format("  \u540E\u7AEF\u5C1A\u672A\u5C31\u7EEA\uFF0C\u5DF2\u7B49\u5F85 {0} \u79D2...", (attempt + 1) * 2));
                }
                if (!backendReady)
                {
                    _log.Err("\u540E\u7AEF\u5728 60 \u79D2\u5185\u672A\u5C31\u7EEA\uFF0C\u8BF7\u68C0\u67E5\u65E5\u5FD7\u6392\u67E5\u95EE\u9898");
                    return;
                }
                _log.Ok(string.Format("\u540E\u7AEF\u5DF2\u5C31\u7EEA: http://localhost:{0}", _bePort));
                _log.Hl("\u542F\u52A8\u524D\u7AEF\u670D\u52A1...");
                _log.Info(string.Format("\u524D\u7AEF\u76EE\u5F55: {0}", Path.Combine(_root, "web")));
                ProcessStartInfo si = new ProcessStartInfo();
                si.FileName = "cmd.exe";
                si.Arguments = string.Format("/c npm run dev -- --port {0}", _fePort);
                si.WorkingDirectory = Path.Combine(_root, "web");
                si.RedirectStandardOutput = true;
                si.RedirectStandardError = true;
                si.UseShellExecute = false;
                si.CreateNoWindow = true;
                si.StandardOutputEncoding = Encoding.UTF8;
                si.StandardErrorEncoding = Encoding.UTF8;
                si.EnvironmentVariables["NEXT_PUBLIC_API_URL"] = string.Format("http://localhost:{0}", _bePort);
                _procFE = Process.Start(si);
                _procFE.OutputDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procFE.ErrorDataReceived += delegate(object s, DataReceivedEventArgs ev) { if (ev.Data != null) _log.Info(ev.Data); };
                _procFE.BeginOutputReadLine();
                _procFE.BeginErrorReadLine();
                _log.Ok(string.Format("\u524D\u7AEF\u5DF2\u542F\u52A8 \u2192 http://localhost:{0}", _fePort));
                if (_autoOpen)
                {
                    Thread.Sleep(3000);
                    OpenUrl(string.Format("http://localhost:{0}", _fePort));
                }
            }
            catch (Exception ex) { _log.Err(string.Format("\u524D\u7AEF\u542F\u52A8\u5931\u8D25: {0}", ex.Message)); }
        });
    }

    public void StopServices()
    {
        _log.Warn("\u6B63\u5728\u505C\u6B62\u670D\u52A1...");
        KillProc(_procBE); KillProc(_procFE);
        _procBE = null; _procFE = null;
        KillPort(_bePort); KillPort(_fePort);
        lock (_lock) { _running = false; }
        _log.Ok("\u6240\u6709\u670D\u52A1\u5DF2\u505C\u6B62");
    }

    private void KillProc(Process p)
    {
        try { if (p != null && !p.HasExited) { p.Kill(); p.WaitForExit(3000); } } catch { }
    }

    private void KillPort(string port)
    {
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe",
                string.Format("/c for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :{0} ^| findstr LISTENING') do taskkill /F /PID %a 2>nul", port));
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
            _log.Info(string.Format("\u5DF2\u6253\u5F00: {0}", url));
        }
        catch { }
    }

    public bool IsRunning { get { lock (_lock) { return _running; } } }
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
                _engine.Log.Hl(string.Format("\u90E8\u7F72\u5DE5\u5177 UI: http://localhost:{0}", p));
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
                Respond(ctx, 200, "text/html; charset=utf-8", Html.Page);
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
                string body = ReadBody(ctx);
                _engine.SetConfig(JVal(body, "apiKey"), JVal(body, "baseUrl"), JVal(body, "model"),
                    JVal(body, "bePort"), JVal(body, "fePort"), JVal(body, "autoOpen") == "true");
                _engine.StartDeploy();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/check-env" && method == "POST")
            {
                _engine.CheckEnv();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/api/test-llm" && method == "POST")
            {
                string body = ReadBody(ctx);
                string result = _engine.TestLLM(JVal(body, "apiKey"), JVal(body, "baseUrl"), JVal(body, "model"));
                Respond(ctx, 200, "application/json", result);
            }
            else if (path == "/api/stop" && method == "POST")
            {
                _engine.StopServices();
                Respond(ctx, 200, "application/json", "{\"ok\":true}");
            }
            else if (path == "/favicon.ico")
            {
                ctx.Response.StatusCode = 204; ctx.Response.Close();
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
//  Tray Icon
// ═══════════════════════════════════════════════════════════
public class AppTray : ApplicationContext
{
    private NotifyIcon _tray;
    private WebServer _server;
    private Engine _engine;

    public AppTray(WebServer server, Engine engine)
    {
        _server = server;
        _engine = engine;

        _tray = new NotifyIcon();
        _tray.Icon = MakeIcon();
        _tray.Text = "ExcelManus Deploy Tool";
        _tray.Visible = true;

        ContextMenuStrip menu = new ContextMenuStrip();
        menu.Items.Add("\u6253\u5F00\u754C\u9762", null, delegate { OpenUI(); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("\u9000\u51FA", null, delegate { ExitApp(); });
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick += delegate { OpenUI(); };

        _tray.ShowBalloonTip(2000, "ExcelManus",
            string.Format("\u90E8\u7F72\u5DE5\u5177\u5DF2\u542F\u52A8\nhttp://localhost:{0}", server.Port),
            ToolTipIcon.Info);
    }

    private void OpenUI()
    {
        try { Process.Start(new ProcessStartInfo(_server.Url) { UseShellExecute = true }); } catch { }
    }

    private void ExitApp()
    {
        if (_engine.IsRunning) _engine.StopServices();
        _server.Stop();
        _tray.Visible = false;
        _tray.Dispose();
        Application.Exit();
    }

    private Icon MakeIcon()
    {
        Bitmap bmp = new Bitmap(32, 32);
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
        return Icon.FromHandle(bmp.GetHicon());
    }
}

// ═══════════════════════════════════════════════════════════
//  Entry Point
// ═══════════════════════════════════════════════════════════
public static class Program
{
    [STAThread]
    public static void Main()
    {
        bool created;
        Mutex mutex = new Mutex(true, "ExcelManusDeployTool_SingleInstance", out created);
        if (!created)
        {
            MessageBox.Show("\u90E8\u7F72\u5DE5\u5177\u5DF2\u5728\u8FD0\u884C\u4E2D\u3002\n\u8BF7\u68C0\u67E5\u7CFB\u7EDF\u6258\u76D8\u56FE\u6807\u3002",
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
                MessageBox.Show("\u65E0\u6CD5\u542F\u52A8\u672C\u5730\u670D\u52A1\u5668\uFF0C\u7AEF\u53E3\u53EF\u80FD\u88AB\u5360\u7528\u3002",
                    "ExcelManus", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            try { Process.Start(new ProcessStartInfo(server.Url) { UseShellExecute = true }); } catch { }

            Application.Run(new AppTray(server, engine));
        }
        finally { mutex.ReleaseMutex(); }
    }
}
