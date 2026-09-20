const { spawn } = require('node:child_process');

// These actions are only dispatched by a click in the trusted desktop main frame.
function allowPrivateNetwork(port, executable = process.execPath) {
  if (process.platform !== 'win32') throw new Error('请在系统防火墙中允许 ExcelManus 的局域网连接');
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('请先开启手机连接');
  const quote = value => "'" + value.replaceAll("'", "''") + "'";
  const rule = `ExcelManus-Mobile-${port}`;
  const script = `$ErrorActionPreference='Stop'; Get-NetFirewallRule -Name ${quote(rule)} -ErrorAction SilentlyContinue | Remove-NetFirewallRule; New-NetFirewallRule -Name ${quote(rule)} -DisplayName 'ExcelManus 手机连接（专用局域网）' -Direction Inbound -Action Allow -Protocol TCP -LocalPort ${port} -Profile Private -RemoteAddress LocalSubnet -Program ${quote(executable)} | Out-Null`;
  const encoded = Buffer.from(script, 'utf16le').toString('base64');
  const launcher = `$p=Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','${encoded}'; exit $p.ExitCode`;
  return new Promise((resolve, reject) => {
    const child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(launcher, 'utf16le').toString('base64')], { windowsHide: true, stdio: 'ignore' });
    child.once('error', () => reject(new Error('无法打开系统权限确认，请手动检查防火墙')));
    child.once('exit', code => code === 0 ? resolve(true) : reject(new Error('尚未放行端口；可重试并接受 Windows 权限提示，或手动设置防火墙')));
  });
}

module.exports = { allowPrivateNetwork };
