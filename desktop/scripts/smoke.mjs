// Run against the packaged Resources when supplied, otherwise the staging tree.
import { execFileSync, spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { stopProcess } = require('../src/process-lifecycle.js');
const resources = resolve(process.argv[2] || fileURLToPath(new URL('../.build', import.meta.url)));
const appBundle = resolve(resources, '../..');
const verifySignature = () => {
 if (process.platform === 'darwin' && appBundle.endsWith('.app')) execFileSync('codesign', ['--verify', '--deep', '--strict', appBundle], {stdio:'inherit'});
};
verifySignature();
const home = mkdtempSync(join(tmpdir(), 'excelmanus-desktop-smoke-'));
const cwd = join(home, 'data');
mkdirSync(cwd);
const win = process.platform === 'win32';
const python = join(resources, 'runtime/python', win ? 'python.exe' : 'bin/python3');
const backend = join(resources, 'backend/excelmanus-backend', win ? 'excelmanus-backend.exe' : 'excelmanus-backend');
const node = join(resources, 'runtime', win ? 'node.exe' : 'node');
const frontend = join(resources, 'frontend');
const env = { ...process.env, EXCELMANUS_HOME: home, EXCELMANUS_DESKTOP: '1', EXCELMANUS_DESKTOP_CONTROL_STDIN: '1', EXCELMANUS_DEPLOY_MODE: 'standalone', EXCELMANUS_RUN_PYTHON: python, PYTHONPATH: '', PYTHONHOME: '' };
// No developer Python, npm, uv or Node on PATH. The product must supply its own.
for (const key of Object.keys(env)) if (key.toUpperCase() === 'PATH') delete env[key];
env.PATH = win ? `${process.env.SystemRoot || process.env.SYSTEMROOT}\\System32` : '/usr/bin:/bin';
for (const key of ['EXCELMANUS_DB_PATH', 'EXCELMANUS_DATA_ROOT', 'EXCELMANUS_CHAT_HISTORY_DB_PATH', 'EXCELMANUS_MANAGE_TOKEN']) delete env[key];
// Check the interpreter inside the actual package too: staging checks alone
// cannot catch an extraResources filter accidentally dropping runtime data.
execFileSync(python, ['-I', '-B', '-X', 'utf8', fileURLToPath(new URL('./check-python-runtime.py', import.meta.url))], {
 cwd, env, stdio: 'inherit', timeout: 120_000, windowsHide: true,
});
const check = spawnSync(backend, ['--check-runtime'], { cwd, env, encoding: 'utf8', timeout: 90000, windowsHide: true });
if (check.status !== 0 || !check.stdout?.includes('FROZEN_RUNTIME_OK')) throw new Error(check.stderr || check.stdout || String(check.error));
console.log(check.stdout.trim());
const freePort = () => new Promise((resolve, reject) => { const s=net.createServer(); s.on('error', reject); s.listen(0,'127.0.0.1',()=>{ const p=s.address().port; s.close(()=>resolve(p)); }); });
const bp = await freePort(); let fp; do { fp = await freePort(); } while (bp===fp);
env.EXCELMANUS_FRONTEND_PORT=String(fp);
const children=[];
const start=(cmd,args,options)=>{const p=spawn(cmd,args,{...options,stdio:['pipe','pipe','pipe'],windowsHide:true,detached:!win}); p.stdin.on('error',()=>{}); p.on('error',error=>console.error(error)); p.stderr.on('data',b=>process.stderr.write(b)); p.stdout.on('data',()=>{}); children.push(p);return p;};
const wait = async url => {for(let i=0;i<600;i++){try{const r=await fetch(url,{signal:AbortSignal.timeout(1000)});if(r.ok)return r;}catch{} await new Promise(r=>setTimeout(r,250));}throw Error(`Not ready: ${url}`);};
try {
 start(backend,['--host','127.0.0.1','--port',String(bp)],{cwd,env});
 const health=await (await wait(`http://127.0.0.1:${bp}/api/v1/health`)).json();
 if(health.configured) throw Error('Smoke profile unexpectedly configured');
 if(health.skillpack_count < 1 || health.tool_count < 1) throw Error('Bundled skills/tools missing');
 start(node,[join(resources,'frontend-runner.cjs'),join(frontend,'server.js')],{cwd:frontend,env:{...env,PORT:String(fp),HOSTNAME:'127.0.0.1',NODE_PATH:join(frontend,'next_modules'),EXCELMANUS_RUNTIME_BACKEND_ORIGIN:`http://127.0.0.1:${bp}`}});
 const html=await (await wait(`http://127.0.0.1:${fp}/`)).text();
 if(!html.includes(`http://127.0.0.1:${bp}`))throw Error('Runtime backend origin missing');
 const cors=await fetch(`http://127.0.0.1:${bp}/api/v1/health`,{headers:{Origin:`http://127.0.0.1:${fp}`}});
 if(cors.headers.get('access-control-allow-origin')!==`http://127.0.0.1:${fp}`)throw Error('CORS mismatch');
 const version=await (await fetch(`http://127.0.0.1:${bp}/api/v1/version/check`)).json();
 if(version.check_method!=='desktop_installer')throw Error('Desktop updater not gated');
 if(!existsSync(join(home,'excelmanus.db')))throw Error('Database outside profile');
 console.log('DESKTOP_HTTP_OK', {home,version:health.version});
} finally { for (const p of children) await stopProcess(p, 'smoke service', console.log); }

verifySignature();
