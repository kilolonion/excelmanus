"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { Check, Loader2, MonitorSmartphone, RefreshCw, ShieldCheck, Smartphone, Wifi } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { setManageToken } from "@/lib/api";
import { mobilePairing, type MobilePairingAction, type MobilePairingStatus } from "@/lib/mobile-pairing";

export function MobilePairingDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [status, setStatus] = useState<MobilePairingStatus | null>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [sameNetwork, setSameNetwork] = useState(false);
  const [address, setAddress] = useState("");
  const [now, setNow] = useState(Date.now());
  const [desktop, setDesktop] = useState(false);
  const [platform, setPlatform] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const generation = useRef(0);
  const busyRef = useRef(false);
  const remaining = status?.qr ? Math.max(0, Math.ceil((status.qr.expiresAt - now) / 1000)) : 0;

  const run = useCallback(async (action: MobilePairingAction, input: { id?: string; address?: string } = {}) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(""); setNotice("");
    const current = ++generation.current;
    try {
      const next = await mobilePairing(action, input);
      if (current !== generation.current) return;
      setStatus(next);
      setPollError("");
      if (action === "allow-firewall") setNotice("已允许专用网络中的手机连接，请保持电脑网络类型为“专用”，再用手机重试。");
      if (action === "approve") setNotice("已确认绑定，手机正在自动打开工作区。");
    } catch (e) { if (current === generation.current) setError(e instanceof Error ? e.message : "连接暂不可用，请重试"); }
    finally { busyRef.current = false; setBusy(false); }
  }, []);

  useEffect(() => {
    if (!open) return;
    let alive = true; let polling = false;
    setDesktop(Boolean(window.excelManusDesktop?.mobilePairing));
    const poll = async () => {
      if (polling || busyRef.current) return;
      polling = true;
      const current = generation.current;
      try {
        const next = await mobilePairing("status");
        if (!alive || current !== generation.current) return;
        setStatus(next); if (next.platform) setPlatform(next.platform);
        setAddress(previous => next.networks.some(n => n.address === previous) ? previous : next.networks[0]?.address || "");
        setPollError("");
      } catch (e) { if (alive && current === generation.current) setPollError(e instanceof Error ? e.message : "暂时无法检查连接"); }
      finally { polling = false; }
    };
    void poll();
    const timer = setInterval(() => { setNow(Date.now()); void poll(); }, 2000);
    return () => { alive = false; clearInterval(timer); generation.current++; };
  }, [open]);

  const issue = () => run("issue", { address: address || undefined });
  const step = status?.pending.length ? 3 : status?.qr ? 2 : status?.enabled && status.devices.length ? 4 : status?.enabled ? 2 : 1;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[620px] max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><MonitorSmartphone className="h-5 w-5 text-[var(--em-primary)]" />扫码连接手机</DialogTitle>
          <DialogDescription>把 Android 手机连到这台电脑，继续对话、上传表格并保存结果。</DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-3 gap-2 text-xs" aria-label="绑定进度">
          {["连接同一网络", "手机扫码", "确认绑定"].map((label, index) => <div key={label} className={`flex items-center gap-2 rounded-lg px-2 py-3 ${step >= index + 1 ? "bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]" : "bg-muted text-muted-foreground"}`}><span className="font-semibold">{step > index + 1 ? <Check className="h-3.5 w-3.5" /> : index + 1}</span>{label}</div>)}
        </div>
        <section className="space-y-3 rounded-xl border p-4">
          <p className="flex items-center gap-2 text-sm font-medium"><Wifi className="h-4 w-4" />手机和电脑连接同一个路由器</p>
          <p className="text-xs leading-relaxed text-muted-foreground">电脑可以使用网线，手机使用该路由器的 Wi-Fi。请避开访客 Wi-Fi，并暂时关闭影响局域网访问的 VPN。</p>
          {desktop && platform === "win32" && <Button variant="outline" size="sm" disabled={busy} onClick={() => void run("network-settings")}>打开电脑网络设置</Button>}
          {!status?.enabled && <label className="flex cursor-pointer items-center gap-2 text-sm"><input type="checkbox" checked={sameNetwork} onChange={e => setSameNetwork(e.target.checked)} className="accent-[var(--em-primary)]" />手机已连接同一网络</label>}
          {status && status.networks.length > 1 && <label className="block space-y-1 text-xs text-muted-foreground">选择手机所在的电脑网络<select aria-label="电脑网络" value={address} onChange={e => { setAddress(e.target.value); }} className="w-full rounded-md border bg-background p-2 text-sm text-foreground">{status.networks.map(n => <option key={n.address} value={n.address}>{n.name} · {n.address}{n.virtual ? "（虚拟网络）" : ""}</option>)}</select></label>}
          {!status?.enabled && <Button disabled={busy || !sameNetwork || !status} onClick={() => void issue()} className="w-full">{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}检查服务并生成二维码</Button>}
        </section>
        {(error || pollError) && <p role="alert" className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">{error || pollError}</p>}
        {!desktop && (error || pollError).includes("本引导") && <form className="flex flex-wrap gap-2" onSubmit={event => {
          event.preventDefault(); setManageToken(adminToken.trim()); setAdminToken(""); void run("status");
        }}><Input type="password" aria-label="电脑服务管理令牌" placeholder="启动电脑服务时设置的管理令牌" autoComplete="off" minLength={16} required value={adminToken} onChange={event => setAdminToken(event.target.value)} className="min-w-0 flex-1" /><Button disabled={busy} type="submit">验证并继续</Button></form>}
        {notice && <p role="status" className="rounded-lg bg-[var(--em-primary-alpha-10)] p-3 text-sm text-[var(--em-primary)]">{notice}</p>}
        {status?.enabled && <>
          <section className="space-y-3 text-center">
            {status.qr && remaining > 0 ? <>
              <div className="mx-auto w-fit rounded-xl border bg-white p-3"><QRCodeSVG value={status.qr.value} size={208} level="M" marginSize={4} title="使用 ExcelManus Android 客户端扫描此配对码" /></div>
              <p className="text-sm font-medium">在 APK 首页或侧边栏点击“扫一扫”</p>
              <p className="text-xs text-muted-foreground">{status.qr.server} · {remaining} 秒后失效 · 仅可扫描一次</p>
            </> : <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">{status.pending.length ? "手机已扫码，请核对下面的数字。" : status.devices.length ? "手机已绑定，可随时添加另一台设备。" : "二维码已失效，点击下方按钮重新生成。"}</div>}
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void issue()}><RefreshCw className="mr-2 h-3.5 w-3.5" />{status.qr ? "刷新二维码" : "生成新的二维码"}</Button>
          </section>
          {status.pending.map(p => <section key={p.id} className="space-y-3 rounded-xl border border-[var(--em-primary)] bg-[var(--em-primary-alpha-10)] p-4">
            <p className="text-sm font-medium">{p.name} 请求绑定</p><p className="text-xs text-muted-foreground">确认手机上也显示这组数字，然后允许连接。</p>
            <p className="font-mono text-center text-3xl font-semibold tracking-[0.25em]" aria-label={`核对码 ${p.comparison}`}>{p.comparison}</p>
            <div className="flex gap-2"><Button disabled={busy} className="flex-1" onClick={() => void run("approve", { id: p.id })}><Check className="mr-2 h-4 w-4" />是我的手机，允许连接</Button><Button disabled={busy} variant="outline" onClick={() => void run("reject", { id: p.id })}>拒绝</Button></div>
          </section>)}
        </>}
        <details className="rounded-xl border p-4 text-sm">
          <summary className="cursor-pointer font-medium">扫码后连接不上？按这里继续检查</summary>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-xs leading-relaxed text-muted-foreground">
            <li>让电脑保持运行，手机使用 Wi-Fi；检查是否连到了访客网络，或路由器开启了设备隔离。</li>
            <li>Windows 的当前网络应为“专用网络”。仅在信任此网络时修改；不要关闭整个防火墙。</li>
            <li>允许 ExcelManus 通过专用网络防火墙，再在手机点击“重试”。当前入口端口：{status?.port || 8787}。</li>
            <li>多个网卡时，在上方选择与手机相同路由器的地址，再生成新二维码。切换 Wi-Fi 后也需要重新扫码。</li>
          </ol>
          {desktop && platform === "win32" && <div className="mt-3 space-y-2"><Button size="sm" variant="outline" disabled={busy || !status?.enabled} onClick={() => void run("allow-firewall")}><ShieldCheck className="mr-2 h-4 w-4" />允许专用网络连接</Button><p className="text-xs text-muted-foreground">Windows 会请求管理员权限，仅放行本程序的当前端口与本地子网。</p></div>}
          {!desktop && <p className="mt-3 text-xs text-muted-foreground">浏览器无法修改电脑系统设置。请在运行服务的电脑上放行此端口，或使用桌面版的一键引导。</p>}
        </details>
        {Boolean(status?.devices.length) && <section className="space-y-2"><p className="text-sm font-medium">已绑定的手机</p>{status?.devices.map(device => <div key={device.id} className="flex items-center gap-2 rounded-lg bg-muted p-3 text-sm"><Smartphone className="h-4 w-4 shrink-0" /><span className="min-w-0 flex-1 truncate">{device.name}</span><Button variant="ghost" size="sm" disabled={busy} onClick={() => void run("revoke", { id: device.id })}>解除绑定</Button></div>)}</section>}
        <p className="text-xs leading-relaxed text-muted-foreground">手机与这台电脑共用工作区。此连接仅供可信局域网使用；绑定后电脑需要保持运行。</p>
        {status?.enabled && <Button variant="ghost" size="sm" disabled={busy} className="text-muted-foreground" onClick={() => void run("stop")}>关闭手机连接入口</Button>}
      </DialogContent>
    </Dialog>
  );
}
