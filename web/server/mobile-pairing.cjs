const { randomBytes, randomInt, createHash, timingSafeEqual } = require('node:crypto');
const { networkInterfaces, hostname } = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const { createGateway } = require('./lan-gateway.cjs');

const secret = () => randomBytes(32).toString('base64url');
const digest = (value) => createHash('sha256').update(String(value)).digest('hex');
const equal = (a, b) => timingSafeEqual(Buffer.from(digest(a)), Buffer.from(digest(b)));
const fail = (message, status = 400) => Object.assign(new Error(message), { status });

function privateIpv4(address) {
  const parts = address.split('.');
  if (parts.length !== 4 || parts.some(p => !/^\d{1,3}$/.test(p) || Number(p) > 255)) return false;
  const [a, b] = parts.map(Number);
  return a === 10 || a === 192 && b === 168 || a === 172 && b >= 16 && b <= 31;
}

function lanAddresses(interfaces = networkInterfaces()) {
  return Object.entries(interfaces).flatMap(([name, entries]) => (entries || [])
    .filter(e => e.family === 'IPv4' && !e.internal && privateIpv4(e.address))
    .map(e => ({ name, address: e.address, virtual: /vEthernet|virtual|vmware|docker|wsl|vpn|tailscale|tun|tap/i.test(name) })))
    .sort((a, b) => Number(a.virtual) - Number(b.virtual)).slice(0, 12);
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(body));
}

async function body(req) {
  if (!String(req.headers['content-type'] || '').startsWith('application/json')) throw fail('需要 JSON 请求');
  let size = 0; const chunks = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 4096) throw fail('请求过大', 413);
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

class MobilePairing {
  constructor({ frontend, backend, backendToken = '', stateFile, interfaces = networkInterfaces, now = Date.now, host = '0.0.0.0', port = 8787 }) {
    this.frontend = frontend; this.backend = backend; this.backendToken = backendToken.trim();
    for (const value of [frontend, backend]) {
      const url = new URL(value);
      if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
          || url.pathname !== '/' || url.search || url.hash || url.username || url.password) throw fail('手机连接需要本机运行的网页和后端服务');
    }
    this.stateFile = stateFile; this.interfaces = interfaces; this.now = now; this.host = host;
    this.port = port; this.devices = []; this.pending = new Map(); this.streams = new Map();
    this.server = null; this.starting = null; this.qr = null; this.enabled = false;
    if (stateFile) {
      try {
        const saved = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
        if (saved.version === 1) {
          this.devices = (saved.devices || []).filter(d => typeof d.id === 'string' && typeof d.name === 'string' && /^[a-f0-9]{64}$/.test(d.tokenHash)).slice(0, 20);
          if (Number.isInteger(saved.port) && saved.port > 1023 && saved.port <= 65535) this.port = saved.port;
          this.enabled = saved.enabled === true;
        }
      } catch (error) { if (error.code !== 'ENOENT') throw fail('手机绑定记录无法读取，请检查文件权限或备份'); }
    }
  }

  persist() {
    if (!this.stateFile) return;
    fs.mkdirSync(path.dirname(this.stateFile), { recursive: true });
    const temp = this.stateFile + '.tmp';
    fs.writeFileSync(temp, JSON.stringify({ version: 1, enabled: this.enabled, port: this.port, devices: this.devices }), { mode: 0o600 });
    fs.renameSync(temp, this.stateFile);
  }

  async check() {
    const [front, back] = await Promise.all([
      fetch(this.frontend, { redirect: 'error', signal: AbortSignal.timeout(5000) }).catch(() => null),
      fetch(this.backend + '/api/v1/health', { redirect: 'error', signal: AbortSignal.timeout(5000) }).catch(() => null),
    ]);
    if (!front?.ok) throw fail('网页服务尚未就绪，请先在电脑中正常打开工作区');
    await front.body?.cancel();
    if (!back?.ok) throw fail('后端未连接，请先恢复侧边栏的连接状态');
    const health = await back.json();
    if (!health.version || !health.api_schema_version) throw fail('后端没有返回有效的 ExcelManus 服务信息');
    const auth = await fetch(this.backend + '/api/v1/sessions', {
      headers: this.backendToken ? { Authorization: `Bearer ${this.backendToken}` } : {},
      redirect: 'error', signal: AbortSignal.timeout(5000),
    });
    await auth.body?.cancel();
    if (!auth.ok) throw fail('后端认证未通过，请在启动电脑服务时设置相同的 EXCELMANUS_MANAGE_TOKEN');
    return { frontend: true, backend: true };
  }

  expire() {
    for (const [id, pending] of this.pending) if (pending.expiresAt <= this.now()) this.pending.delete(id);
    if (this.qr?.expiresAt <= this.now()) this.qr = null;
  }

  status() {
    this.expire();
    return {
      enabled: Boolean(this.server?.listening), port: this.port, computerName: hostname(),
      networks: lanAddresses(this.interfaces()),
      qr: this.qr && { value: this.qr.value, expiresAt: this.qr.expiresAt, server: this.qr.server },
      pending: [...this.pending.values()].filter(p => p.state === 'pending').map(p => ({ id: p.id, name: p.name, address: p.address, comparison: p.comparison })),
      devices: this.devices.map(({ tokenHash, ...device }) => device),
    };
  }

  async start() {
    if (this.starting) return this.starting;
    if (this.server?.listening) return this.status();
    this.starting = this.startInternal();
    try { return await this.starting; } finally { this.starting = null; }
  }

  async startInternal() {
    await this.check();
    if (!lanAddresses(this.interfaces()).length) throw fail('没有找到局域网地址，请让电脑连接 Wi-Fi 或网线后重试');
    const server = createGateway({
      frontend: this.frontend, backend: this.backend,
      authorize: token => this.devices.some(d => equal(d.tokenHash, digest(token))) ? this.backendToken : null,
      handleRequest: (req, res) => this.handle(req, res),
      onAuthorized: (_req, res, token) => {
        const hash = digest(token);
        if (!this.streams.has(hash)) this.streams.set(hash, new Set());
        this.streams.get(hash).add(res);
        res.once('close', () => { const active = this.streams.get(hash); active?.delete(res); if (!active?.size) this.streams.delete(hash); });
      },
    });
    server.requestTimeout = 300000;
    const listen = port => new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, this.host, () => { server.removeListener('error', reject); resolve(); });
    });
    try { await listen(this.port); }
    catch (error) {
      // Never silently change an address already saved on a bound phone.
      if (error.code !== 'EADDRINUSE' || this.devices.length) throw fail('手机连接端口不可用，请关闭占用该端口的程序后重试');
      await listen(0);
    }
    this.server = server; this.port = server.address().port; this.enabled = true;
    try { this.persist(); } catch (error) { this.server = null; server.close(); server.closeAllConnections(); throw error; }
    return this.status();
  }

  async issue(address) {
    await this.start();
    const networks = lanAddresses(this.interfaces());
    const selected = address || networks[0]?.address;
    if (!networks.some(n => n.address === selected)) throw fail('所选网络已变化，请刷新网络列表');
    this.expire();
    for (const [id, p] of this.pending) if (p.state === 'pending') this.pending.delete(id);
    const code = secret(); const expiresAt = this.now() + 180000;
    const server = `http://${selected}:${this.port}`;
    const query = new URLSearchParams({ v: '1', server, code, expires: String(expiresAt) });
    this.qr = { codeHash: digest(code), expiresAt, server, value: `excelmanus://pair?${query}` };
    return this.status();
  }

  approve(id) {
    this.expire();
    const p = this.pending.get(id);
    if (!p || p.state !== 'pending') throw fail('绑定请求已过期，请重新扫码');
    if (this.devices.length >= 20) throw fail('最多绑定 20 台设备，请先解除不再使用的设备');
    const token = secret();
    const device = { id: randomBytes(12).toString('hex'), name: p.name, tokenHash: digest(token), createdAt: this.now() };
    this.devices.push(device);
    try { this.persist(); } catch (error) { this.devices.pop(); throw error; }
    p.state = 'approved'; p.token = token; p.deviceId = device.id; p.expiresAt = this.now() + 120000;
    return this.status();
  }

  reject(id) { const p = this.pending.get(id); if (p?.state === 'pending') p.state = 'rejected'; return this.status(); }

  revoke(id) {
    const device = this.devices.find(d => d.id === id);
    const previous = this.devices;
    this.devices = this.devices.filter(d => d.id !== id);
    try { this.persist(); } catch (error) { this.devices = previous; throw error; }
    if (device) {
      for (const response of this.streams.get(device.tokenHash) || []) response.destroy();
      this.streams.delete(device.tokenHash);
      for (const [key, p] of this.pending) if (p.deviceId === id) this.pending.delete(key);
    }
    return this.status();
  }

  async shutdown() {
    if (this.starting) await this.starting.catch(() => {});
    const server = this.server; this.server = null; this.qr = null; this.pending.clear();
    if (server) await new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
  }

  async stop() { await this.shutdown(); this.enabled = false; this.persist(); return this.status(); }

  async handle(req, res) {
    if (!req.url?.startsWith('/__excelmanus_pairing/')) return false;
    // Native client only. The pairing exchange is never a browser CORS API.
    if (req.headers.origin || req.headers['sec-fetch-site']) { json(res, 403, { error: '请使用 Android 客户端扫码' }); return true; }
    try {
      this.expire();
      if (req.method === 'POST' && req.url === '/__excelmanus_pairing/claim') {
        const input = await body(req);
        if (!this.qr || !equal(digest(input.code || ''), this.qr.codeHash)) throw fail('二维码已失效，请在电脑上刷新后重扫', 410);
        if (this.pending.size >= 24) throw fail('绑定请求过多，请稍后重试', 429);
        const name = String(input.name || 'Android 手机').replace(/[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]/g, '').slice(0, 60);
        const id = randomBytes(12).toString('hex'); const pollToken = secret();
        const pending = { id, name, address: req.socket.remoteAddress, pollHash: digest(pollToken), comparison: String(randomInt(100000, 1000000)), state: 'pending', expiresAt: this.now() + 120000 };
        this.pending.set(id, pending); this.qr = null; // One scan consumes the code.
        json(res, 200, { id, pollToken, comparison: pending.comparison, expiresAt: pending.expiresAt });
      } else if (req.method === 'POST' && ['/__excelmanus_pairing/status', '/__excelmanus_pairing/cancel', '/__excelmanus_pairing/ack'].includes(req.url)) {
        const input = await body(req); const p = this.pending.get(input.id);
        const credential = String(req.headers.authorization || '').replace(/^Bearer /, '');
        if (!p || !equal(digest(credential), p.pollHash)) throw fail('绑定请求已过期，请重新扫码', 410);
        if (req.url.endsWith('/cancel')) { if (p.deviceId) this.revoke(p.deviceId); this.pending.delete(p.id); json(res, 200, { state: 'cancelled' }); }
        else if (req.url.endsWith('/ack')) { this.pending.delete(p.id); json(res, 200, { state: 'complete' }); }
        else json(res, 200, { state: p.state, ...(p.state === 'approved' ? { token: p.token } : {}) });
      } else json(res, 404, { error: '连接入口不存在' });
    } catch (error) { if (!res.headersSent) json(res, error.status || 400, { error: error.message || '绑定请求失败' }); }
    return true;
  }
}

module.exports = { MobilePairing, lanAddresses, privateIpv4, equal };
