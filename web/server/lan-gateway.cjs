#!/usr/bin/env node
// Foreground, single-user LAN entry point for existing loopback services.
const http = require('node:http');
const { createHash, timingSafeEqual } = require('node:crypto');


const { posix } = require('node:path');

const HOP_HEADERS = new Set(['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']);
function safeHeaders(headers) {
  const connectionHeaders = new Set(String(headers.connection || '').toLowerCase().split(',').map((v) => v.trim()));
  return Object.fromEntries(Object.entries(headers).filter(([key]) => !HOP_HEADERS.has(key.toLowerCase()) && !connectionHeaders.has(key.toLowerCase())));
}
function loopbackTarget(input) {
  const target = new URL(input);
  if (target.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname)
      || target.username || target.password || target.pathname !== '/' || target.search || target.hash) {
    throw new Error('上游地址必须是本机 HTTP 根地址，例如 http://127.0.0.1:8000');
  }
  return target;
}
function matchesToken(provided, expected) {
  return timingSafeEqual(createHash('sha256').update(provided).digest(), createHash('sha256').update(expected).digest());
}
function json(res, status, value) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}

function createGateway({ frontend = 'http://127.0.0.1:3000', backend = 'http://127.0.0.1:8000', token, authorize, handleRequest, onAuthorized }) {
  if (!authorize && (typeof token !== 'string' || token.trim().length < 16 || /[\r\n]/.test(token))) throw new Error('请设置至少 16 个字符的 EXCELMANUS_MANAGE_TOKEN。');
  token = token?.trim() || '';
  const front = loopbackTarget(frontend);
  const back = loopbackTarget(backend);
  return http.createServer(async (req, res) => {
    if (handleRequest) {
      try { if (await handleRequest(req, res)) return; }
      catch { if (!res.headersSent) json(res, 400, { error: '请求无法完成，请重试' }); else res.destroy(); return; }
    }
    let url;
    try {
      if (!req.url?.startsWith('/') || req.url.startsWith('//')) throw new Error('invalid path');
      url = new URL(req.url, 'http://gateway.invalid');
    } catch { json(res, 400, { error: '请求路径不正确' }); return; }
    // Classify encoded paths too, so a frontend rewrite cannot bypass the gate.
    let decodedPath = url.pathname;
    for (let i = 0; i < 8; i++) {
      let next;
      try { next = decodeURIComponent(decodedPath); } catch { break; }
      if (next === decodedPath) break;
      decodedPath = next;
      if (i === 7) { json(res, 400, { error: '请求路径编码过深' }); return; }
    }
    const normalizedPath = posix.normalize(decodedPath.replaceAll('\\', '/'));
    // Device credentials can never open the host's pairing administration.
    if (/^\/api\/mobile-pairing(?:\/|$)/i.test(normalizedPath)) { json(res, 403, { error: '请在电脑上管理手机连接' }); return; }
    const isApi = /^\/api(?:\/|$)/i.test(normalizedPath);
    const isHealth = url.pathname === '/api/v1/health' && req.method === 'GET';
    const bearer = String(req.headers.authorization || '').match(/^Bearer\s+(.+)$/i)?.[1];
    const provided = bearer || String(req.headers['x-excelmanus-token'] || '') || url.searchParams.get('manage_token') || '';
    const upstreamToken = authorize ? authorize(provided) : matchesToken(provided, token) ? token : null;
    if (isApi && !isHealth && upstreamToken === null) {
      json(res, 401, { error: '需要管理令牌', error_id: 'manage_token_required' });
      return;
    }
    if (isApi && !isHealth && onAuthorized) onAuthorized(req, res, provided);
    const target = isApi ? back : front;
    const headers = safeHeaders(req.headers);
    delete headers['x-forwarded-host'];
    delete headers['x-forwarded-for'];
    delete headers['x-forwarded-proto'];
    headers.host = target.host;
    if (authorize) {
      // Per-device secrets terminate here. Only the host knows the backend token.
      delete headers.authorization;
      delete headers['x-excelmanus-token'];
      url.searchParams.delete('manage_token');
      if (isApi && upstreamToken) headers.authorization = `Bearer ${upstreamToken}`;
    }
    if (!isApi) {
      delete headers.authorization;
      delete headers['x-excelmanus-token'];
    }
    headers['accept-encoding'] = 'identity';
    headers['x-forwarded-proto'] = 'http';
    // Fixed upstream host: request paths must never become destination URLs.
    const upstream = http.request({ hostname: target.hostname.replace(/^\[|\]$/g, ''), port: target.port || 80,
      method: req.method, path: url.pathname + url.search, headers }, (response) => {
      const responseHeaders = safeHeaders(response.headers);
      responseHeaders['cache-control'] = isApi ? 'no-store' : responseHeaders['cache-control'] || 'no-cache';
      if (isHealth && response.statusCode === 200) {
        const chunks = [];
        let size = 0;
        response.on('data', (chunk) => {
          size += chunk.length;
          if (size > 256 * 1024) { response.destroy(); if (!res.headersSent) json(res, 502, { error: '健康检查响应过大' }); }
          else chunks.push(chunk);
        });
        response.on('end', () => {
          if (res.writableEnded) return;
          try { json(res, 200, { ...JSON.parse(Buffer.concat(chunks).toString('utf8')), auth_required: true }); }
          catch { json(res, 502, { error: '后端健康检查格式不正确' }); }
        });
      } else {
        res.writeHead(response.statusCode || 502, responseHeaders);
        res.flushHeaders();
        response.pipe(res); // Preserve streaming and backpressure; no SSE buffering.
      }
      response.on('error', () => { if (!res.headersSent) json(res, 502, { error: '上游连接中断' }); else res.destroy(); });
    });
    upstream.setTimeout(600000, () => upstream.destroy(new Error('upstream timeout')));
    upstream.on('error', () => { if (!res.headersSent) json(res, 502, { error: '服务未启动，请检查电脑上的前后端' }); else res.destroy(); });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => upstream.destroy());
    req.pipe(upstream);
  });
}


module.exports = { createGateway };

