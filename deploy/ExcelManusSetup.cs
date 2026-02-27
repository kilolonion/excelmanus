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
html,body{height:100%;overflow:hidden;font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,system-ui,sans-serif}
:root{
  --g:#217346;--gl:#33a867;--gd:#1a5c38;--ga1:rgba(33,115,70,.06);--ga2:rgba(33,115,70,.12);--ga3:rgba(33,115,70,.18);
  --bg:#f5f5f7;--card:#fff;--brd:#e5e7eb;--brd2:#d1d5db;
  --red:#d13438;--redl:#e74c3c;--gold:#e5a100;--cyan:#0078d4;
  --t1:#1a1a1a;--t2:#4b5563;--t3:#9ca3af;--t4:#d1d5db;
  --r:10px;--r2:8px;
}
body{background:var(--bg);color:var(--t1);display:flex;flex-direction:column}

/* ── Header ── */
.hdr{
  background:var(--card);border-bottom:1px solid var(--brd);
  padding:0 28px;height:54px;display:flex;align-items:center;gap:14px;flex-shrink:0;
  position:relative;
}
.hdr::after{content:'';position:absolute;bottom:-1px;left:0;width:120px;height:2px;background:linear-gradient(90deg,var(--g),transparent);border-radius:2px}
.logo{
  width:34px;height:34px;position:relative;flex-shrink:0;
}
.logo::before{
  content:'';position:absolute;inset:0;
  background:linear-gradient(135deg,var(--gl),var(--g));
  clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%);
}
.logo::after{
  content:'';position:absolute;inset:5px;
  background:rgba(255,255,255,.25);
  clip-path:polygon(50% 0%,100% 50%,50% 100%,0% 50%);
}
.brand{display:flex;align-items:baseline;gap:10px}
.brand h1{font-size:17px;font-weight:700;color:var(--t1);letter-spacing:-.3px}
.brand span{font-size:11px;color:var(--t3);font-weight:500;letter-spacing:.5px}

/* ── Progress ── */
.pbar{height:2px;background:var(--brd);flex-shrink:0;overflow:hidden}
.pfill{height:100%;width:0%;background:linear-gradient(90deg,var(--g),var(--gl));transition:width .5s ease;position:relative}
.pfill::after{content:'';position:absolute;inset:0;width:60px;background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);animation:shimmer 1.8s infinite}
@keyframes shimmer{from{transform:translateX(-60px)}to{transform:translateX(400px)}}

/* ── Main grid: 3 columns, fill remaining height ── */
.main{
  flex:1;min-height:0;
  display:grid;
  grid-template-columns:210px 1fr 1fr;
  gap:14px;
  padding:14px 20px;
}

/* ── Cards ── */
.card{
  background:var(--card);border:1px solid var(--brd);border-radius:var(--r);
  display:flex;flex-direction:column;overflow:hidden;
}
.card-t{
  font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:1.5px;
  padding:14px 16px 10px;border-bottom:1px solid var(--brd);flex-shrink:0;
  display:flex;align-items:center;gap:8px;
}
.card-t i{color:var(--g);font-style:normal;font-size:13px}
.card-body{padding:10px 14px;flex:1;min-height:0;overflow-y:auto}

/* ── Checks ── */
.ck{display:flex;align-items:center;padding:7px 6px;border-radius:6px;margin-bottom:1px;transition:background .2s}
.ck:hover{background:var(--ga1)}
.dot{width:8px;height:8px;border-radius:50%;margin-right:10px;flex-shrink:0;transition:all .3s}
.dot-0{background:var(--t4)}
.dot-1{background:var(--g);box-shadow:0 0 6px rgba(33,115,70,.35)}
.dot-2{background:var(--red);box-shadow:0 0 6px rgba(209,52,56,.3)}
.dot-3{background:var(--gold);animation:pulse 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.3;transform:scale(.8)}}
.ck-n{flex:1;font-size:12.5px;font-weight:500;color:var(--t1)}
.ck-s{font-size:10.5px;font-weight:600;color:var(--t3);white-space:nowrap}
.ck-s.s1{color:var(--g)}.ck-s.s2{color:var(--red)}.ck-s.s3{color:var(--gold)}

/* ── Config column: 2 stacked cards ── */
.col-cfg{display:flex;flex-direction:column;gap:14px;min-height:0}

/* ── Inputs ── */
.fg{margin-bottom:12px}.fg:last-child{margin-bottom:0}
.fl{font-size:10px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
.fi{
  width:100%;background:var(--bg);border:1.5px solid var(--brd);border-radius:var(--r2);
  padding:8px 12px;color:var(--t1);font-size:13px;font-family:inherit;outline:none;
  transition:border-color .2s,box-shadow .2s;
}
.fi:focus{border-color:var(--g);box-shadow:0 0 0 3px var(--ga1)}
.fi::placeholder{color:var(--t4)}
.pr{display:grid;grid-template-columns:1fr 1fr;gap:10px}

/* ── Checkbox ── */
.chk{display:flex;align-items:center;gap:9px;cursor:pointer;font-size:12.5px;color:var(--t2);user-select:none;padding:2px 0}
.chk:hover{color:var(--t1)}
.cb{
  width:18px;height:18px;border-radius:5px;border:2px solid var(--brd2);
  display:flex;align-items:center;justify-content:center;
  transition:all .2s;flex-shrink:0;font-size:11px;color:transparent;
}
.cb.on{background:var(--g);border-color:var(--g);color:#fff}

/* ── Buttons ── */
.acts{display:flex;gap:8px;padding:12px 14px;border-top:1px solid var(--brd);flex-shrink:0}
.btn{
  padding:8px 18px;border-radius:var(--r2);font-size:12.5px;font-weight:600;
  border:none;cursor:pointer;transition:all .2s;display:inline-flex;align-items:center;gap:6px;font-family:inherit;
}
.btn:active{transform:scale(.97)}
.b1{background:linear-gradient(135deg,var(--g),var(--gd));color:#fff;box-shadow:0 2px 8px rgba(33,115,70,.2)}
.b1:hover{box-shadow:0 4px 14px rgba(33,115,70,.28);transform:translateY(-1px)}
.b2{background:var(--red);color:#fff}.b2:hover{background:var(--redl)}
.b3{background:var(--bg);color:var(--g);border:1.5px solid var(--ga3)}.b3:hover{background:var(--ga1);border-color:var(--g)}
.btn:disabled{opacity:.35;cursor:not-allowed;transform:none!important;box-shadow:none!important}

/* ── Log ── */
.log-card{display:flex;flex-direction:column;min-height:0}
.log-card .card{flex:1;display:flex;flex-direction:column;min-height:0}
.lcon{
  flex:1;min-height:0;overflow-y:auto;
  background:var(--bg);padding:10px 14px;
  font-family:'Cascadia Mono',Consolas,monospace;font-size:12px;line-height:1.65;color:var(--t2);
}
.ll{padding:1px 0;white-space:pre-wrap;word-break:break-all}
.ll.ok{color:var(--g)}.ll.err{color:var(--red)}.ll.warn{color:var(--gold)}.ll.hl{color:var(--cyan)}
</style>
</head>
<body>
<div class='hdr'>
  <div class='logo'></div>
  <div class='brand'>
    <h1>ExcelManus</h1>
    <span>Deploy Tool &middot; v1.0</span>
  </div>
</div>
<div class='pbar'><div class='pfill' id='pf'></div></div>
<div class='main'>
  <!-- Col 1: Env Checks -->
  <div class='card'>
    <div class='card-t'><i>&#10003;</i> &#x73AF;&#x5883;&#x68C0;&#x6D4B;</div>
    <div class='card-body' id='cks'></div>
  </div>
  <!-- Col 2: Config -->
  <div class='col-cfg'>
    <div class='card' style='flex:1'>
      <div class='card-t'><i>&#9881;</i> LLM &#x914D;&#x7F6E;</div>
      <div class='card-body'>
        <div class='fg'><div class='fl'>API KEY</div><input class='fi' type='password' id='f_key' placeholder='sk-...'></div>
        <div class='fg'><div class='fl'>BASE URL</div><input class='fi' id='f_url' placeholder='https://api.openai.com/v1'></div>
        <div class='fg'><div class='fl'>&#x6A21;&#x578B;&#x540D;&#x79F0;</div><input class='fi' id='f_model' placeholder='gpt-4o'></div>
      </div>
    </div>
    <div class='card'>
      <div class='card-t'><i>&#9889;</i> &#x670D;&#x52A1;&#x914D;&#x7F6E;</div>
      <div class='card-body'>
        <div class='pr'>
          <div class='fg'><div class='fl'>&#x540E;&#x7AEF;&#x7AEF;&#x53E3;</div><input class='fi' id='f_bp' value='8000'></div>
          <div class='fg'><div class='fl'>&#x524D;&#x7AEF;&#x7AEF;&#x53E3;</div><input class='fi' id='f_fp' value='3000'></div>
        </div>
        <div class='chk' onclick='tgAuto()'><div class='cb on' id='acb'>&#10003;</div><span>&#x542F;&#x52A8;&#x540E;&#x81EA;&#x52A8;&#x6253;&#x5F00;&#x6D4F;&#x89C8;&#x5668;</span></div>
      </div>
      <div class='acts'>
        <button class='btn b1' id='bS' onclick='doStart()'>&#9654; &#x542F;&#x52A8;&#x90E8;&#x7F72;</button>
        <button class='btn b2' id='bX' onclick='doStop()' disabled>&#9724; &#x505C;&#x6B62;</button>
        <button class='btn b3' onclick='doOpen()'>&#8599; &#x6D4F;&#x89C8;&#x5668;</button>
      </div>
    </div>
  </div>
  <!-- Col 3: Log -->
  <div class='log-card'>
    <div class='card'>
      <div class='card-t'><i>&#9776;</i> &#x8FD0;&#x884C;&#x65E5;&#x5FD7;</div>
      <div class='lcon' id='lc'></div>
    </div>
  </div>
</div>
<script>
var CKS=[{id:'python',n:'Python 3.x'},{id:'node',n:'Node.js'},{id:'npm',n:'npm'},{id:'git',n:'Git'},{id:'backend',n:'\u540E\u7AEF\u4F9D\u8D56'},{id:'frontend',n:'\u524D\u7AEF\u4F9D\u8D56'}];
var autoOpen=true,logIdx=0;
function init(){
var h='';for(var i=0;i<CKS.length;i++){var c=CKS[i];
h+='<div class=""ck""><div class=""dot dot-0"" id=""d_'+c.id+'""></div><span class=""ck-n"">'+c.n+'</span><span class=""ck-s"" id=""s_'+c.id+'"">'+'\u5F85\u68C0\u6D4B'+'</span></div>';}
document.getElementById('cks').innerHTML=h;
fetch('/api/config').then(function(r){return r.json()}).then(function(d){
if(d.apiKey)document.getElementById('f_key').value=d.apiKey;
if(d.baseUrl)document.getElementById('f_url').value=d.baseUrl;
if(d.model)document.getElementById('f_model').value=d.model;
if(d.bePort)document.getElementById('f_bp').value=d.bePort;
if(d.fePort)document.getElementById('f_fp').value=d.fePort;
if(d.autoOpen!==undefined){autoOpen=d.autoOpen;updAuto();}
}).catch(function(){});
setInterval(pollLogs,500);setInterval(pollSt,800);
}
function pollLogs(){
fetch('/api/logs?since='+logIdx).then(function(r){return r.json()}).then(function(d){
var el=document.getElementById('lc');
for(var i=0;i<d.logs.length;i++){var l=d.logs[i];var div=document.createElement('div');div.className='ll '+l.level;div.textContent=l.text;el.appendChild(div);logIdx=l.idx+1;}
if(d.logs.length>0)el.scrollTop=el.scrollHeight;
}).catch(function(){});
}
function pollSt(){
fetch('/api/status').then(function(r){return r.json()}).then(function(d){
for(var i=0;i<CKS.length;i++){var c=CKS[i];var st=d.checks[c.id]||0;
document.getElementById('d_'+c.id).className='dot dot-'+st;
var se=document.getElementById('s_'+c.id);if(d.details&&d.details[c.id])se.textContent=d.details[c.id];
se.className='ck-s s'+st;}
document.getElementById('bS').disabled=d.deploying||d.running;
document.getElementById('bX').disabled=!d.running;
document.getElementById('pf').style.width=d.progress+'%';
}).catch(function(){});
}
function cfg(){return{apiKey:document.getElementById('f_key').value,baseUrl:document.getElementById('f_url').value,model:document.getElementById('f_model').value,bePort:document.getElementById('f_bp').value,fePort:document.getElementById('f_fp').value,autoOpen:autoOpen}}
function doStart(){document.getElementById('bS').disabled=true;fetch('/api/deploy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg())})}
function doStop(){fetch('/api/stop',{method:'POST'})}
function doOpen(){var p=document.getElementById('f_fp').value||'3000';window.open('http://localhost:'+p,'_blank')}
function tgAuto(){autoOpen=!autoOpen;updAuto()}
function updAuto(){var e=document.getElementById('acb');if(autoOpen){e.className='cb on';e.innerHTML='&#10003;';}else{e.className='cb';e.innerHTML='';}}
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
    private string _root;
    private readonly LogStore _log;
    private readonly object _lock = new object();
    private Process _procBE;
    private Process _procFE;
    private bool _running;
    private bool _deploying;
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

        string[] ids = new string[] { "python", "node", "npm", "git", "backend", "frontend" };
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
        if (File.Exists(Path.Combine(d, "pyproject.toml")))
        { _root = d; }
        else
        {
            string p = Directory.GetParent(d) != null ? Directory.GetParent(d).FullName : d;
            _root = File.Exists(Path.Combine(p, "pyproject.toml")) ? p : d;
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
            File.WriteAllLines(EnvPath, new string[] {
                string.Format("EXCELMANUS_API_KEY={0}", _apiKey),
                string.Format("EXCELMANUS_BASE_URL={0}", _baseUrl),
                string.Format("EXCELMANUS_MODEL={0}", _model)
            }, Encoding.UTF8);
            _log.Ok("\u5DF2\u4FDD\u5B58 .env");
        }
        catch (Exception ex) { _log.Err(string.Format("\u4FDD\u5B58 .env \u5931\u8D25: {0}", ex.Message)); }
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
        int done = 0, total = 6;
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

        bool beOk = SetupBE();
        setCk("backend", beOk, beOk ? "\u5C31\u7EEA" : "\u5931\u8D25");
        if (!beOk) { lock (_lock) { _deploying = false; } return; }

        bool feOk = SetupFE();
        setCk("frontend", feOk, feOk ? "\u5C31\u7EEA" : "\u5931\u8D25");
        if (!feOk) { lock (_lock) { _deploying = false; } return; }

        lock (_lock) { _progress = 100; _deploying = false; }
        StartServices();
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
        if (!Directory.Exists(wd)) { _log.Warn("\u672A\u627E\u5230 web \u76EE\u5F55"); return false; }
        if (Directory.Exists(Path.Combine(wd, "node_modules")))
        {
            _log.Ok(string.Format("\u524D\u7AEF\u4F9D\u8D56\u5DF2\u5B58\u5728: {0}", Path.Combine(wd, "node_modules")));
            return true;
        }
        _log.Info("\u5B89\u88C5\u524D\u7AEF\u4F9D\u8D56 (npm install)\uFF0C\u8BF7\u7A0D\u5019...");
        try
        {
            ProcessStartInfo si = new ProcessStartInfo("cmd.exe", "/c npm install");
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
            if (p.ExitCode != 0) { _log.Err("npm install \u5931\u8D25"); return false; }
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
                Thread.Sleep(2000);
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
