package com.excelmanus.android;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.view.ViewGroup;
import android.view.accessibility.AccessibilityNodeInfo;
import android.widget.Button;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Camera intent result is supplied; native review, HTTP exchange, Keystore and WebView are real. */
@RunWith(AndroidJUnit4.class)
public class PairingIntegrationTest {
    @Test public void scannedInvitationWaitsForApprovalDespiteDifferentComputerClock() throws Exception {
        Instrumentation ins = InstrumentationRegistry.getInstrumentation();
        Context context = ins.getTargetContext();
        ConnectionStore store = new ConnectionStore(context); store.clear();
        String invitation = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
        String credential = "ABCDEFGabcdefghijklmnopqrstuvwxyz0123456789";
        String pollToken = "0123456789_abcdefghijklmnopqrstuvwxyzABCDEF";
        AtomicBoolean approved = new AtomicBoolean(false), claimed = new AtomicBoolean(false), stopped = new AtomicBoolean(false);
        ServerSocket server = new ServerSocket(0);
        String address = null;
        for (java.net.NetworkInterface network : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
            for (java.net.InetAddress ip : java.util.Collections.list(network.getInetAddresses())) {
                if (ip instanceof java.net.Inet4Address && ip.isSiteLocalAddress() && !ip.isLoopbackAddress()) address = ip.getHostAddress();
            }
        }
        assertNotNull("Connect the test device to Wi-Fi before running pairing scenarios", address);
        String origin = "http://" + address + ":" + server.getLocalPort();
        Thread fixture = new Thread(() -> {
            while (!stopped.get()) try (Socket socket = server.accept()) {
                socket.setSoTimeout(5000);
                java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                String first = reader.readLine(); if (first == null) continue;
                int size = 0; String line;
                while ((line = reader.readLine()) != null && !line.isEmpty()) if (line.toLowerCase().startsWith("content-length:")) size = Integer.parseInt(line.substring(15).trim());
                char[] input = new char[size]; int read = 0;
                while (read < size) { int n = reader.read(input, read, size - read); if (n < 0) break; read += n; }
                JSONObject result = new JSONObject();
                String body; String type = "application/json";
                if (first.contains("/claim")) {
                    if (new JSONObject(new String(input)).optString("code").equals(invitation)) claimed.set(true);
                    // Computer is one day behind the phone; server expiry remains authoritative.
                    body = result.put("id", "0123456789abcdef01234567").put("pollToken", pollToken).put("comparison", "123456").put("expiresAt", System.currentTimeMillis() - 86400000 + 120000).toString();
                } else if (first.contains("/status")) body = approved.get() ? result.put("state", "approved").put("token", credential).toString() : result.put("state", "pending").toString();
                else if (first.contains("/ack")) body = "{}";
                else { body = "<!doctype html><title>Paired workspace</title><h1>绑定成功</h1>"; type = "text/html; charset=utf-8"; }
                byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: " + type + "\r\nContent-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                socket.getOutputStream().write(bytes);
            } catch (Exception ignored) { }
        }, "pairing-http-fixture"); fixture.start();
        String qr = "excelmanus://pair?v=1&server=" + java.net.URLEncoder.encode(origin, "UTF-8") + "&code=" + invitation + "&expires=" + (System.currentTimeMillis() - 86400000 + 180000);
        Intent scanResult = new Intent().putExtra("SCAN_RESULT", qr).putExtra("SCAN_RESULT_FORMAT", "QR_CODE");
        Instrumentation.ActivityMonitor monitor = ins.addMonitor(PairingCaptureActivity.class.getName(), new Instrumentation.ActivityResult(Activity.RESULT_OK, scanResult), true);
        MainActivity activity = null;
        try {
            activity = (MainActivity) ins.startActivitySync(new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
            MainActivity current = activity;
            ins.runOnMainSync(() -> { Button button = findButton(current.getWindow().getDecorView(), "扫一扫连接电脑"); assertNotNull(button); button.performClick(); });
            clickText(ins, "同一可信网络，继续");
            long deadline = System.currentTimeMillis() + 15000;
            while (!claimed.get() && System.currentTimeMillis() < deadline) Thread.sleep(100);
            assertTrue("invitation sent through native HTTP", claimed.get());
            assertEquals("not connected before approval", "", store.address());
            waitText(ins, "123456");
            approved.set(true);
            deadline = System.currentTimeMillis() + 10000;
            while (store.address().isEmpty() && System.currentTimeMillis() < deadline) Thread.sleep(100);
            assertEquals(origin, store.address()); assertEquals(credential, store.token()); assertTrue(store.allowHttp());
            assertEquals(1, monitor.getHits());
        } finally {
            if (activity != null) { MainActivity current = activity; ins.runOnMainSync(current::finish); }
            ins.removeMonitor(monitor); store.clear(); stopped.set(true); server.close(); fixture.join(2000);
        }
    }

    private static Button findButton(View view, String text) {
        if (view instanceof Button && text.contentEquals(((Button)view).getText())) return (Button)view;
        if (view instanceof ViewGroup) for (int i = 0; i < ((ViewGroup)view).getChildCount(); i++) { Button found = findButton(((ViewGroup)view).getChildAt(i), text); if (found != null) return found; }
        return null;
    }
    private static AccessibilityNodeInfo waitText(Instrumentation ins, String text) throws Exception {
        long deadline = System.currentTimeMillis() + 10000;
        do {
            AccessibilityNodeInfo root = ins.getUiAutomation().getRootInActiveWindow();
            if (root != null) {
                java.util.List<AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(text);
                if (!nodes.isEmpty()) return nodes.get(0);
            }
            Thread.sleep(100);
        } while (System.currentTimeMillis() < deadline);
        throw new AssertionError("UI text missing: " + text);
    }
    private static void clickText(Instrumentation ins, String text) throws Exception { assertTrue(waitText(ins, text).performAction(AccessibilityNodeInfo.ACTION_CLICK)); }
}
