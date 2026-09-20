import { getAuthHeaders } from "@/lib/api";

export interface MobilePairingStatus {
  enabled: boolean;
  port: number;
  computerName: string;
  platform?: string;
  networks: { name: string; address: string; virtual: boolean }[];
  qr: { value: string; expiresAt: number; server: string } | null;
  pending: { id: string; name: string; address: string; comparison: string }[];
  devices: { id: string; name: string; createdAt: number }[];
}

export type MobilePairingAction = "status" | "issue" | "approve" | "reject" | "revoke" | "stop" | "network-settings" | "allow-firewall";

export async function mobilePairing(action: MobilePairingAction, input: { id?: string; address?: string } = {}): Promise<MobilePairingStatus> {
  if (window.excelManusDesktop?.mobilePairing) return window.excelManusDesktop.mobilePairing(action, input);
  const response = await fetch("/api/mobile-pairing", {
    method: action === "status" ? "GET" : "POST",
    headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
    ...(action === "status" ? {} : { body: JSON.stringify({ action, ...input }) }),
    signal: AbortSignal.timeout(20000), cache: "no-store",
  });
  const result = await response.json().catch(() => ({ error: "此服务暂不支持扫码绑定，请更新电脑端后重试。" }));
  if (!response.ok) throw new Error(result.error || "手机连接暂不可用");
  return result;
}
