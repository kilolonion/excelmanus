// Exercises the real Electron supervisor and its installed resources.
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, mkdirSync, existsSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { stopProcess, waitForExit } = require('../src/process-lifecycle.js');
const executable = resolve(process.argv[2]);
const home = process.argv[3] ? resolve(process.argv[3]) : mkdtempSync(join(tmpdir(), 'ExcelManus 中文 空格 '));
mkdirSync(home, {recursive:true});
const env = {...process.env, EXCELMANUS_HOME:home, EXCELMANUS_DESKTOP_CONTROL_STDIN:'1'};
for(const key of Object.keys(env)) if(key.startsWith('PYTHON') || ['EXCELMANUS_DB_PATH','EXCELMANUS_DATA_ROOT','EXCELMANUS_CHAT_HISTORY_DB_PATH','EXCELMANUS_MANAGE_TOKEN'].includes(key)) delete env[key];
for(const key of Object.keys(env)) if(key.toUpperCase()==='PATH') delete env[key];
env.PATH=process.platform==='win32' ? `${process.env.SystemRoot || process.env.SYSTEMROOT}\\System32` : '/usr/bin:/bin';
const resources = process.platform==='darwin' ? resolve(dirname(executable),'../Resources') : join(dirname(executable),'resources');
execFileSync(process.execPath,[fileURLToPath(new URL('./smoke.mjs',import.meta.url)),resources],{stdio:'inherit'});
let output='';
const child=spawn(executable,[],{cwd:home,env,stdio:['pipe','pipe','pipe'],windowsHide:true,detached:process.platform!=='win32'});
child.stdin.on('error',()=>{});
child.on('error',error=>{output+=error.message;});
child.stdout.on('data',b=>{output+=b.toString();}); child.stderr.on('data',b=>{output+=b.toString();});
const logPath=join(home,'logs/desktop.log');
const logs=()=>{try{return readFileSync(logPath,'utf8');}catch{return output;}};
const wait=async(fn)=>{const end=Date.now()+150_000;let last;while(Date.now()<end){if(child.exitCode!==null)throw Error(`App exited ${child.exitCode}: ${output}`);try{const value=await fn();if(value)return value;}catch(e){last=e;}await new Promise(r=>setTimeout(r,250));}throw Error(`Timeout: ${last || ''}\n${logs()}`);};
let bp,fp;
const api=async(path,body)=>{const r=await fetch(`http://127.0.0.1:${bp}/api/v1/${path}`,{method:body?'PUT':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(3000)});if(!r.ok)throw Error(`${path}: ${r.status}`);return r.json();};
try {
 await wait(async()=>{const match=[...logs().matchAll(/启动 backend: .* --port (\d+)/g)].at(-1);if(!match)return false;bp=Number(match[1]);fp=Number(readFileSync(join(home,'frontend-port'),'utf8'));const health=await api('health');return health.skillpack_count>0 && logs().includes('主窗口加载完成');});
 const initialCount=(logs().match(/启动 backend:/g)||[]).length;
 const response=await api('config/runtime',{session_ttl_seconds:1729});
 if(!response.restarting)throw Error('Settings did not request restart');
 await wait(async()=> (logs().match(/启动 backend:/g)||[]).length>initialCount && logs().includes('code=75') && (await api('health')).status==='ok');
 const settings=await api('config/runtime');
 if(settings.session_ttl_seconds!==1729)throw Error('Setting was not preserved after restart');
 if(Number(readFileSync(join(home,'frontend-port'),'utf8'))!==fp)throw Error('Frontend port changed');
 // End the inherited pipe as well: Windows fs reads use a worker thread, and
 // leaving its writer open can keep a read pending during Electron teardown.
 child.stdin.end('shutdown\n');
 await stopProcess(child,'Electron',console.log,75_000);
 if(child.exitCode!==0)throw Error(`App quit failed: ${child.exitCode}`);
 for(const port of [bp,fp]) {try {await fetch(`http://127.0.0.1:${port}/api/v1/health`,{signal:AbortSignal.timeout(1000)});}catch{continue;}throw Error(`Orphan listener ${port}`);}
 if(!logs().includes('API 服务已关闭'))throw Error('Graceful shutdown did not complete');
 if(!existsSync(join(home,'excelmanus.db')))throw Error('Missing profile database');
 writeFileSync(join(home,'smoke-profile-marker'),'keep');
 console.log('APP_SUPERVISOR_OK',JSON.stringify({home,bp,fp}));
} finally {if(child.exitCode===null && child.signalCode===null)await stopProcess(child,'Electron cleanup',console.log,5000);}
