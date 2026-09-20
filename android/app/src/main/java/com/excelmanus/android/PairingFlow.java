package com.excelmanus.android;

import android.app.AlertDialog;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.provider.Settings;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.json.JSONObject;

/** Native review, pairing exchange and resumable polling, outside the WebView. */
final class PairingFlow {
    private static final class ServiceError extends Exception {
        ServiceError(String message) { super(message); }
    }
    interface Host { boolean paired(ServerAddress server, String token); void scanAgain(); }
    private final MainActivity activity;
    private final Host host;
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    private volatile int generation;
    private volatile boolean closed;
    private AlertDialog dialog;
    private PairingCode invitation;
    private String pendingId;
    private String pollToken;
    private long deadline;

    PairingFlow(MainActivity activity, Host host) { this.activity = activity; this.host = host; }

    void openWifi() {
        try { activity.startActivity(new Intent(Build.VERSION.SDK_INT >= 29 ? Settings.Panel.ACTION_WIFI : Settings.ACTION_WIFI_SETTINGS)); }
        catch (Exception ignored) { activity.notice("请在手机系统设置中打开 Wi-Fi，连接电脑所在的路由器。"); }
    }

    void review(String raw) {
        cancel();
        try { invitation = PairingCode.parse(raw); }
        catch (IllegalArgumentException error) { failure(error.getMessage(), false); return; }
        dialog = new AlertDialog.Builder(activity).setTitle("连接这台电脑")
                .setMessage("电脑地址：" + invitation.server.origin + "\n\n请让手机与电脑连接同一个路由器。" + (invitation.server.insecure ? "此连接使用局域网 HTTP，请仅在可信网络继续。" : ""))
                .setPositiveButton("同一可信网络，继续", (d, which) -> claim())
                .setNeutralButton("Wi-Fi 设置", null)
                .setNegativeButton("取消", (d, which) -> cancel()).create();
        dialog.setOnCancelListener(d -> cancel());
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> openWifi()));
        dialog.show();
    }

    private void claim() {
        final int current = ++generation;
        final PairingCode selected = invitation;
        pendingId = null; pollToken = null;
        showProgress("正在检查局域网连接…", "正在连接电脑，请保持电脑上的绑定引导打开。");
        worker.execute(() -> {
            try {
                JSONObject result = request(selected, "claim", new JSONObject().put("code", selected.code).put("name", Build.MANUFACTURER + " " + Build.MODEL), "");
                String id = result.getString("id"), credential = result.getString("pollToken");
                if (!id.matches("[a-f0-9]{24}") || !credential.matches("[A-Za-z0-9_-]{43}") || !result.getString("comparison").matches("[0-9]{6}")) throw new Exception("配对响应格式不正确");
                if (closed || current != generation) { abandon(selected, id, credential); return; }
                pendingId = id; pollToken = credential;
                deadline = SystemClock.elapsedRealtime() + 120000;
                activity.runOnUiThread(() -> {
                    if (closed || current != generation) return;
                    showProgress("请在电脑上确认", "请核对电脑和手机上的数字一致：\n\n" + result.optString("comparison") + "\n\n然后在电脑点击“是我的手机，允许连接”。");
                });
                poll(current, selected, id, credential);
            } catch (Exception error) { report(current, error, true); }
        });
    }

    private void poll(int current, PairingCode selected, String id, String credential) {
        if (closed || current != generation) return;
        if (SystemClock.elapsedRealtime() >= deadline) { report(current, new Exception("等待确认已超时，请在电脑生成新二维码后重扫。"), false); return; }
        try {
            JSONObject result = request(selected, "status", new JSONObject().put("id", id), credential);
            if (closed || current != generation) return;
            switch (result.getString("state")) {
                case "pending": worker.schedule(() -> poll(current, selected, id, credential), 1500, TimeUnit.MILLISECONDS); break;
                case "rejected": report(current, new Exception("电脑拒绝了此次绑定。请确认扫描的是你自己的电脑。"), false); break;
                case "approved":
                    String token = result.getString("token");
                    if (!token.matches("[A-Za-z0-9_-]{43}")) throw new Exception("连接凭据格式不正确");
                    activity.runOnUiThread(() -> {
                        if (closed || current != generation) return;
                        // Persist before acknowledgement; a lost response can still be retried.
                        if (!host.paired(selected.server, token)) { failure("无法安全保存连接设置，请重试。", false); return; }
                        dismiss(); generation++; pendingId = null; pollToken = null;
                        worker.execute(() -> { try { request(selected, "ack", new JSONObject().put("id", id), credential); } catch (Exception ignored) { } });
                    });
                    break;
                default: throw new Exception("绑定状态无效，请重新扫描。");
            }
        } catch (java.io.IOException transientFailure) {
            // Wi-Fi can briefly disappear while returning from system settings.
            worker.schedule(() -> poll(current, selected, id, credential), 2000, TimeUnit.MILLISECONDS);
        } catch (Exception error) { report(current, error, false); }
    }

    private void showProgress(String title, String message) {
        dismiss();
        dialog = new AlertDialog.Builder(activity).setTitle(title).setMessage(message)
                .setNegativeButton("取消绑定", (d, which) -> cancel()).create();
        dialog.setOnCancelListener(d -> cancel());
        dialog.show();
    }

    private void report(int current, Exception error, boolean retry) {
        activity.runOnUiThread(() -> {
            if (closed || current != generation) return;
            String message = error instanceof java.io.IOException
                    ? "没有连上电脑。请检查：\n1. 手机与电脑连接同一路由器，避开访客 Wi-Fi。\n2. 电脑引导中点击“允许专用网络连接”。\n3. 暂时关闭 VPN，或切换电脑网卡后生成新二维码。"
                    : error.getMessage();
            // An expired/consumed invitation cannot be repaired by retrying it.
            failure(message, retry && !(error instanceof ServiceError));
        });
    }

    private void failure(String message, boolean retry) {
        dismiss();
        AlertDialog.Builder builder = new AlertDialog.Builder(activity).setTitle("继续完成连接").setMessage(message)
                .setNeutralButton("Wi-Fi 设置", null).setNegativeButton("取消", (d, w) -> cancel());
        builder.setPositiveButton(retry && invitation != null ? "重试连接" : "重新扫码", (d, w) -> {
            if (retry && invitation != null) claim();
            else { cancel(); host.scanAgain(); }
        });
        dialog = builder.create(); dialog.setOnCancelListener(d -> cancel());
        dialog.setOnShowListener(d -> dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> openWifi()));
        dialog.show();
    }

    private static JSONObject request(PairingCode selected, String route, JSONObject input, String credential) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(selected.server.origin + "/__excelmanus_pairing/" + route).openConnection();
        connection.setConnectTimeout(5000); connection.setReadTimeout(8000); connection.setInstanceFollowRedirects(false);
        connection.setRequestMethod("POST"); connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        if (!credential.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + credential);
        try {
            byte[] bytes = input.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(bytes.length);
            try (java.io.OutputStream output = connection.getOutputStream()) { output.write(bytes); }
            int status = connection.getResponseCode();
            InputStream source = status == 200 ? connection.getInputStream() : connection.getErrorStream();
            if (source == null) throw new Exception("电脑连接入口未就绪，请刷新二维码。");
            try (InputStream stream = source; ByteArrayOutputStream data = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[2048]; int count;
                while ((count = stream.read(buffer)) != -1) { if (data.size() + count > 16384) throw new Exception("配对响应过大"); data.write(buffer, 0, count); }
                JSONObject result = new JSONObject(data.toString(StandardCharsets.UTF_8.name()));
                if (status != 200) throw new ServiceError(result.optString("error", "绑定请求失败，请重新扫描。"));
                return result;
            }
        } finally { connection.disconnect(); }
    }

    private void abandon(PairingCode code, String id, String credential) {
        try { request(code, "cancel", new JSONObject().put("id", id), credential); } catch (Exception ignored) { }
    }
    private void dismiss() { if (dialog != null) { dialog.dismiss(); dialog = null; } }
    boolean isActive() { return dialog != null; }
    void cancel() {
        generation++; dismiss();
        PairingCode code = invitation; String id = pendingId, credential = pollToken;
        pendingId = null; pollToken = null;
        if (code != null && id != null && credential != null && !worker.isShutdown()) worker.execute(() -> abandon(code, id, credential));
    }
    void close() { cancel(); closed = true; worker.shutdown(); }
}
