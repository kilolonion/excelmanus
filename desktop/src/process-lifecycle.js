const { execFile } = require('node:child_process');
const path = require('node:path');

function exited(child) {
  return !child?.pid || child.exitCode !== null || child.signalCode != null;
}
function waitForExit(child, timeoutMs) {
  if (exited(child)) return Promise.resolve(true);
  return new Promise(resolve => {
    const done = value => { clearTimeout(timer); child.removeListener('exit', onExit); resolve(value); };
    const onExit = () => done(true);
    const timer = setTimeout(() => done(exited(child)), timeoutMs);
    child.once('exit', onExit);
    if (exited(child)) done(true);
  });
}
async function stopProcess(child, label, log = () => {}, graceMs = 60_000) {
  if (exited(child)) return;
  log(`请求正常关闭 ${label}`);
  if (child.stdin?.writable) {
    child.stdin.write('shutdown\n', error => { if (error && !exited(child)) log(`${label} 关闭管道失败: ${error.message}`); });
  }
  if (await waitForExit(child, graceMs)) return;
  if (exited(child)) return;
  log(`${label} 正常关闭超时，终止其进程树`);
  if (process.platform === 'win32') {
    const systemRoot = process.env.SystemRoot || process.env.SYSTEMROOT;
    if (!systemRoot) throw new Error('缺少 SystemRoot，无法定位 taskkill');
    await new Promise((resolve, reject) => {
      execFile(path.join(systemRoot, 'System32', 'taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'],
        { windowsHide: true, timeout: 10_000 }, (error, _stdout, stderr) => {
          if (error && !exited(child)) reject(new Error(`${label} 进程树终止失败: ${stderr || error.message}`));
          else resolve();
        });
    });
  } else {
    try { process.kill(-child.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  }
  if (!await waitForExit(child, 8_000)) throw new Error(`${label} 仍未退出；保留进程引用以便重试`);
}
module.exports = { exited, waitForExit, stopProcess };
