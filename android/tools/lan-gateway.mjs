#!/usr/bin/env node
import { networkInterfaces } from 'node:os';
import { pathToFileURL } from 'node:url';
import gateway from '../../web/server/lan-gateway.cjs';
export const { createGateway } = gateway;

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const value = (flag, fallback) => { const index = args.indexOf(flag); return index < 0 ? fallback : args[index + 1]; };
  if (args.includes('--help')) {
    console.log('用法：node android/tools/lan-gateway.mjs [--host 0.0.0.0] [--port 8787] [--frontend http://127.0.0.1:3000] [--backend http://127.0.0.1:8000]\n先设置 EXCELMANUS_MANAGE_TOKEN，并启动前后端服务。此入口仅供可信局域网使用；公网使用 HTTPS 反向代理。');
  } else {
    try {
      const port = Number(value('--port', '8787'));
      if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('端口必须在 1 到 65535 之间。');
      const server = createGateway({ token: process.env.EXCELMANUS_MANAGE_TOKEN, frontend: value('--frontend', 'http://127.0.0.1:3000'), backend: value('--backend', 'http://127.0.0.1:8000') });
      server.on('error', (error) => { console.error('无法启动局域网入口：' + error.code); process.exitCode = 1; });
      server.listen(port, value('--host', '0.0.0.0'), () => {
        console.log('ExcelManus 手机连接入口已启动。请在 APK 中填写下列地址和你设置的管理令牌。');
        for (const entries of Object.values(networkInterfaces())) for (const entry of entries || []) {
          if (entry.family === 'IPv4' && !entry.internal) console.log(`  http://${entry.address}:${port}`);
        }
        console.log('此终端需要保持运行；按 Ctrl+C 停止入口。令牌不会写入日志。');
      });
      const stop = () => { server.close(); server.closeAllConnections(); };
      process.once('SIGINT', stop);
      process.once('SIGTERM', stop);
    } catch (error) { console.error(error.message); process.exitCode = 1; }
  }
}

