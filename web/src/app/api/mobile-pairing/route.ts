import { NextRequest, NextResponse } from "next/server";
import path from "node:path";
import os from "node:os";
import { MobilePairing, equal } from "../../../../server/mobile-pairing.cjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const state = globalThis as typeof globalThis & { excelmanusMobilePairing?: InstanceType<typeof MobilePairing> };

async function handle(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  try {
    // Unlike the desktop IPC, a web server may listen on public interfaces.
    // No implicit localhost trust, forwarded-header trust, or unauthenticated setup.
    const token = process.env.EXCELMANUS_MANAGE_TOKEN?.trim();
    if (!token || token.length < 16) return NextResponse.json({ error: "请在电脑桌面版使用扫码连接；源码部署需在启动前为前后端设置同一个 EXCELMANUS_MANAGE_TOKEN（至少 16 字符）。" }, { status: 503, headers });
    const bearer = request.headers.get("authorization")?.replace(/^Bearer /, "") || "";
    if (!equal(token, bearer)) return NextResponse.json({ error: "请在本引导中填写启动电脑服务时设置的管理令牌。" }, { status: 401, headers });
    if (!state.excelmanusMobilePairing) {
      state.excelmanusMobilePairing = new MobilePairing({
        frontend: `http://127.0.0.1:${process.env.PORT || process.env.EXCELMANUS_FRONTEND_PORT || 3000}`,
        backend: process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000",
        backendToken: token,
        stateFile: path.join(process.env.EXCELMANUS_HOME || path.join(os.homedir(), ".excelmanus"), "web-mobile-pairing.json"),
      });
      if (state.excelmanusMobilePairing.enabled) await state.excelmanusMobilePairing.start();
    }
    const pairing = state.excelmanusMobilePairing;
    if (request.method === "GET") return NextResponse.json(pairing.status(), { headers });
    if (Number(request.headers.get("content-length")) > 2048) throw new Error("请求过大");
    const { action, address, id } = await request.json();
    let result;
    if (action === "issue") result = await pairing.issue(address);
    else if (action === "approve") result = pairing.approve(String(id));
    else if (action === "reject") result = pairing.reject(String(id));
    else if (action === "revoke") result = pairing.revoke(String(id));
    else if (action === "stop") result = await pairing.stop();
    else throw new Error("不支持的手机连接操作");
    return NextResponse.json(result, { headers });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "手机连接暂不可用" }, { status: 400, headers });
  }
}

export const GET = handle;
export const POST = handle;
