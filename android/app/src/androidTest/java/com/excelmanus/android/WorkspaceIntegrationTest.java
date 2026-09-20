package com.excelmanus.android;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.os.Build;
import android.provider.MediaStore;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.InputStream;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;

/** Runs in a real Android WebView against a local HTTP fixture, with no model/account calls. */
@RunWith(AndroidJUnit4.class)
public class WorkspaceIntegrationTest {
    private static final String TOKEN = "android-instrumentation-test-token";
    private Instrumentation instrumentation;
    private Context context;
    private MainActivity activity;
    private ServerSocket fixture;
    private WebView web;
    private volatile boolean stopped;
    private Uri output;
    private String origin;

    @Before public void start() throws Exception {
        instrumentation = InstrumentationRegistry.getInstrumentation();
        context = instrumentation.getTargetContext();
        fixture = new ServerSocket(0);
        String address = null;
        for (java.net.NetworkInterface network : java.util.Collections.list(java.net.NetworkInterface.getNetworkInterfaces())) {
            for (java.net.InetAddress ip : java.util.Collections.list(network.getInetAddresses())) {
                if (ip instanceof java.net.Inet4Address && ip.isSiteLocalAddress() && !ip.isLoopbackAddress()) address = ip.getHostAddress();
            }
        }
        assertNotNull("Connect the test device to Wi-Fi before running LAN scenarios", address);
        origin = "http://" + address + ":" + fixture.getLocalPort();
        new Thread(() -> {
            while (!stopped) {
                try (Socket socket = fixture.accept()) {
                    socket.setSoTimeout(5000);
                    java.io.BufferedReader reader = new java.io.BufferedReader(new java.io.InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
                    String first = reader.readLine();
                    if (first == null) continue;
                    boolean authorized = false;
                    String line;
                    while ((line = reader.readLine()) != null && !line.isEmpty()) {
                        if (line.equalsIgnoreCase("Authorization: Bearer " + TOKEN)) authorized = true;
                    }
                    String body;
                    String type;
                    int status = 200;
                    if (first.contains("/api/v1/sessions")) {
                        status = authorized ? 200 : 401;
                        body = "{\"sessions\":[]}";
                        type = "application/json";
                    } else if (first.contains("/api/v1/health")) {
                        body = "{\"version\":\"test\",\"api_schema_version\":1}";
                        type = "application/json";
                    } else {
                        body = "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head><body><h1>Android fixture</h1><input id='file' type='file' accept='.txt'><input id='message' type='text'><script>window.__EXCELMANUS_RUNTIME__={backendOrigin:'http://localhost:54321'};document.getElementById('file').onchange=async function(){window.uploaded=await this.files[0].text();};window.ready=true;</script></body></html>";
                        type = "text/html; charset=utf-8";
                    }
                    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                    socket.getOutputStream().write(("HTTP/1.1 " + status + " OK\r\nContent-Type: " + type + "\r\nContent-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
                    socket.getOutputStream().write(bytes);
                } catch (Exception error) { if (!stopped) throw new RuntimeException(error); }
            }
        }, "android-http-fixture").start();
        new ConnectionStore(context).save(ServerAddress.parse(origin, true), TOKEN);
        activity = (MainActivity) instrumentation.startActivitySync(new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        instrumentation.waitForIdleSync();
        instrumentation.runOnMainSync(() -> web = findWebView(activity.getWindow().getDecorView()));
        assertNotNull("workspace WebView", web);
        awaitValue("window.ready === true", "true");
    }

    @After public void stop() throws Exception {
        if (activity != null) instrumentation.runOnMainSync(activity::finish);
        new ConnectionStore(context).clear();
        if (output != null) context.getContentResolver().delete(output, null, null);
        stopped = true;
        if (fixture != null) fixture.close();
    }

    @Test public void injectsBeforePageCodeAndAuthenticatesFetch() throws Exception {
        assertEquals("1", evaluate("window.excelManusAndroid.version"));
        assertEquals("\"same-origin\"", evaluate("window.__EXCELMANUS_RUNTIME__.backendOrigin"));
        assertEquals("true", evaluate("sessionStorage.getItem('excelmanus_manage_token') === '" + TOKEN + "'"));
        evaluate("fetch('/api/v1/sessions',{headers:{Authorization:'Bearer '+sessionStorage.getItem('excelmanus_manage_token')}}).then(r=>window.authStatus=r.status)");
        awaitValue("window.authStatus", "200");
    }

    @Test public void webSidebarBridgeOpensTheNativeQrScanner() throws Exception {
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(PairingCaptureActivity.class.getName(),
                new Instrumentation.ActivityResult(Activity.RESULT_CANCELED, null), true);
        try {
            assertEquals("\"function\"", evaluate("typeof window.excelManusAndroid.scanPairing"));
            evaluate("window.excelManusAndroid.scanPairing()");
            long deadline = System.currentTimeMillis() + 10000;
            while (monitor.getHits() == 0 && System.currentTimeMillis() < deadline) Thread.sleep(100);
            assertEquals("native QR capture launched from the selected WebView", 1, monitor.getHits());
            assertEquals("true", evaluate("window.ready === true"));
        } finally { instrumentation.removeMonitor(monitor); }
    }

    @Test public void copiesTextFromInsecureLanToTheSystemClipboard() throws Exception {
        assertEquals("false", evaluate("window.isSecureContext"));
        evaluate("navigator.clipboard.writeText('手机复制内容').then(()=>window.copied=true)");
        awaitValue("window.copied", "true");
        AtomicReference<String> copied = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            android.content.ClipboardManager clipboard = (android.content.ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            copied.set(clipboard.getPrimaryClip().getItemAt(0).getText().toString());
        });
        assertEquals("手机复制内容", copied.get());
        assertEquals("\"undefined\"", evaluate("typeof navigator.clipboard.readText"));
    }

    @Test public void cameraPermissionDenialOffersAnApplicationSettingsEntry() throws Exception {
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(PairingCaptureActivity.class.getName(),
                new Instrumentation.ActivityResult(Activity.RESULT_CANCELED, new Intent().putExtra("MISSING_CAMERA_PERMISSION", true)), true);
        try {
            evaluate("window.excelManusAndroid.scanPairing()");
            waitUiText("需要相机权限才能扫码");
            waitUiText("打开应用设置");
            assertTrue(waitUiText("暂不扫码").performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK));
            assertEquals("true", evaluate("window.ready === true"));
        } finally { instrumentation.removeMonitor(monitor); }
    }

    @Test public void backClosesKeyboardThenSidebarWithoutLeavingWorkspace() throws Exception {
        evaluate("document.body.insertAdjacentHTML('beforeend','<aside aria-label=侧栏 aria-hidden=false style=position:fixed><button aria-label=收起侧栏>关闭</button></aside>');document.querySelector('aside button').onclick=function(){this.parentNode.setAttribute('aria-hidden','true');}");
        tapElement("message");
        long deadline = System.currentTimeMillis() + 5000;
        while (!keyboardVisible() && System.currentTimeMillis() < deadline) Thread.sleep(100);
        assertTrue("keyboard opened by a real touch", keyboardVisible());
        instrumentation.sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
        deadline = System.currentTimeMillis() + 5000;
        while (keyboardVisible() && System.currentTimeMillis() < deadline) Thread.sleep(100);
        assertFalse("first back hides keyboard", keyboardVisible());
        assertEquals("\"false\"", evaluate("document.querySelector('aside').getAttribute('aria-hidden')"));
        instrumentation.sendKeyDownUpSync(android.view.KeyEvent.KEYCODE_BACK);
        awaitValue("document.querySelector('aside').getAttribute('aria-hidden')", "\"true\"");
        assertTrue(activity.hasWindowFocus());
    }

    @Test public void returnsFromHomeAndKeepsTheCurrentPageAndDraft() throws Exception {
        evaluate("document.getElementById('message').value='未发送内容';window.resumed=0;window.addEventListener('online',()=>window.resumed++)");
        assertTrue(instrumentation.getUiAutomation().performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME));
        long deadline = System.currentTimeMillis() + 5000;
        while (activity.hasWindowFocus() && System.currentTimeMillis() < deadline) Thread.sleep(100);
        assertFalse(activity.hasWindowFocus());
        context.startActivity(new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        awaitValue("window.resumed > 0", "true");
        assertEquals("\"未发送内容\"", evaluate("document.getElementById('message').value"));
    }

    @Test public void rendererTerminationRecoversAndIgnoresOldPageCallbacks() throws Exception {
        org.junit.Assume.assumeTrue(Build.VERSION.SDK_INT >= 29);
        WebView previous = web;
        AtomicReference<android.webkit.WebViewClient> client = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            client.set(previous.getWebViewClient());
            assertNotNull(previous.getWebViewRenderProcess());
            assertTrue(previous.getWebViewRenderProcess().terminate());
        });
        waitUiText("页面进程已关闭");
        assertFalse(activity.isDestroyed());
        assertTrue(waitUiText("连接工作区").performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK));
        long deadline = System.currentTimeMillis() + 15000;
        do {
            instrumentation.runOnMainSync(() -> web = findWebView(activity.getWindow().getDecorView()));
            if (web != null) break;
            Thread.sleep(100);
        } while (System.currentTimeMillis() < deadline);
        assertNotNull(web);
        assertNotSame(previous, web);
        awaitValue("window.ready", "true");
        instrumentation.runOnMainSync(() -> {
            client.get().onPageFinished(previous, origin + "/chat/stale-page");
            assertTrue(client.get().onRenderProcessGone(previous, null));
            assertSame(web, findWebView(activity.getWindow().getDecorView()));
        });
        assertEquals("/", new ConnectionStore(context).lastPath());
    }

    @Test public void activityRecreationRestoresSavedConnectionAndChatPath() throws Exception {
        evaluate("window.location.pathname='/chat/restored-session'");
        awaitValue("location.pathname", "\"/chat/restored-session\"");
        awaitValue("window.ready", "true");
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(MainActivity.class.getName(), null, false);
        try {
            MainActivity previous = activity;
            instrumentation.runOnMainSync(previous::recreate);
            Activity restored = monitor.waitForActivityWithTimeout(10000);
            assertNotNull("Android recreated the activity", restored);
            activity = (MainActivity) restored;
            instrumentation.waitForIdleSync();
            instrumentation.runOnMainSync(() -> web = findWebView(activity.getWindow().getDecorView()));
            assertNotNull(web);
            awaitValue("window.ready", "true");
            assertEquals("\"/chat/restored-session\"", evaluate("location.pathname"));
            assertEquals("true", evaluate("sessionStorage.getItem('excelmanus_manage_token')==='" + TOKEN + "'"));
            assertNotSame(previous, activity);
        } finally { instrumentation.removeMonitor(monitor); }
    }

    @Test public void savesBinaryThroughSystemDocumentContract() throws Exception {
        org.junit.Assume.assumeTrue(Build.VERSION.SDK_INT >= 29);
        output = createOutput("android-save-test.bin");
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(documentFilter(Intent.ACTION_CREATE_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK, new Intent().setData(output)), true);
        try {
            evaluate("window.saveAcknowledged=false;var original=ExcelManusNative.onmessage;ExcelManusNative.onmessage=function(e){original(e);window.nativeReply=JSON.parse(e.data);if(window.nativeReply.ok)window.saveAcknowledged=true;};window.excelManusAndroid.saveBlob(new Blob([new Uint8Array(110000).fill(173)]),'中文结果.bin')");
            long deadline = System.currentTimeMillis() + 20000;
            while (monitor.getHits() == 0 && System.currentTimeMillis() < deadline) Thread.sleep(100);
            assertEquals("system save picker opened", 1, monitor.getHits());
            byte[] bytes = null;
            while (System.currentTimeMillis() < deadline) {
                try (InputStream input = context.getContentResolver().openInputStream(output)) { bytes = readAll(input); }
                catch (java.io.FileNotFoundException pendingWrite) { /* The result callback has not created the file yet. */ }
                if (bytes != null && bytes.length == 110000) break;
                Thread.sleep(100);
            }
            assertNotNull(bytes);
            assertEquals(110000, bytes.length);
            for (byte value : bytes) assertEquals((byte) 173, value);
        } finally { instrumentation.removeMonitor(monitor); }
    }

    @Test public void uploadsSelectedContentUriToWebFileInput() throws Exception {
        org.junit.Assume.assumeTrue(Build.VERSION.SDK_INT >= 29);
        output = createOutput("android-upload-test.txt");
        try (java.io.OutputStream stream = context.getContentResolver().openOutputStream(output)) {
            stream.write("手机上传内容".getBytes(StandardCharsets.UTF_8));
        }
        Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(documentFilter(Intent.ACTION_OPEN_DOCUMENT),
                new Instrumentation.ActivityResult(Activity.RESULT_OK, new Intent().setData(output)), true);
        try {
            tapFileInput();
            awaitValue("window.uploaded", "\"手机上传内容\"");
            assertEquals(1, monitor.getHits());
        } finally { instrumentation.removeMonitor(monitor); }
    }

    private IntentFilter documentFilter(String action) throws Exception {
        IntentFilter filter = new IntentFilter(action);
        filter.addCategory(Intent.CATEGORY_OPENABLE);
        filter.addDataType("*/*");
        return filter;
    }
    private void tapFileInput() throws Exception {
        tapElement("file");
    }
    private void tapElement(String id) throws Exception {
        org.json.JSONArray box = new org.json.JSONArray(evaluate("(function(){var r=document.getElementById('" + id + "').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2,window.innerWidth];})()"));
        int[] location = new int[2];
        instrumentation.runOnMainSync(() -> web.getLocationOnScreen(location));
        float scale = web.getWidth() / (float) box.getDouble(2);
        float x = location[0] + (float) box.getDouble(0) * scale;
        float y = location[1] + (float) box.getDouble(1) * scale;
        long now = android.os.SystemClock.uptimeMillis();
        android.view.MotionEvent down = android.view.MotionEvent.obtain(now, now, android.view.MotionEvent.ACTION_DOWN, x, y, 0);
        android.view.MotionEvent up = android.view.MotionEvent.obtain(now, now + 80, android.view.MotionEvent.ACTION_UP, x, y, 0);
        down.setSource(android.view.InputDevice.SOURCE_TOUCHSCREEN);
        up.setSource(android.view.InputDevice.SOURCE_TOUCHSCREEN);
        instrumentation.sendPointerSync(down);
        instrumentation.sendPointerSync(up);
        down.recycle();
        up.recycle();
    }

    private boolean keyboardVisible() {
        AtomicReference<Boolean> visible = new AtomicReference<>(false);
        instrumentation.runOnMainSync(() -> {
            androidx.core.view.WindowInsetsCompat insets = androidx.core.view.ViewCompat.getRootWindowInsets(activity.getWindow().getDecorView());
            visible.set(insets != null && insets.isVisible(androidx.core.view.WindowInsetsCompat.Type.ime()));
        });
        return visible.get();
    }
    private android.view.accessibility.AccessibilityNodeInfo waitUiText(String text) throws Exception {
        long deadline = System.currentTimeMillis() + 12000;
        do {
            android.view.accessibility.AccessibilityNodeInfo root = instrumentation.getUiAutomation().getRootInActiveWindow();
            if (root != null) {
                java.util.List<android.view.accessibility.AccessibilityNodeInfo> nodes = root.findAccessibilityNodeInfosByText(text);
                if (!nodes.isEmpty()) return nodes.get(0);
            }
            Thread.sleep(100);
        } while (System.currentTimeMillis() < deadline);
        throw new AssertionError("UI text missing: " + text);
    }

    @android.annotation.TargetApi(29)
    private Uri createOutput(String name) {
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        values.put(MediaStore.MediaColumns.MIME_TYPE, "application/octet-stream");
        values.put(MediaStore.MediaColumns.RELATIVE_PATH, "Download/ExcelManusTests");
        return context.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
    }
    private byte[] readAll(InputStream input) throws Exception {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[4096]; int read;
        while ((read = input.read(buffer)) != -1) out.write(buffer, 0, read);
        return out.toByteArray();
    }
    private String evaluate(String script) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> web.evaluateJavascript(script, value -> { result.set(value); done.countDown(); }));
        assertTrue("JavaScript callback", done.await(5, TimeUnit.SECONDS));
        return result.get();
    }
    private void awaitValue(String script, String expected) throws Exception {
        String actual = null;
        long deadline = System.currentTimeMillis() + 20000;
        while (System.currentTimeMillis() < deadline) {
            actual = evaluate(script);
            if (expected.equals(actual)) return;
            Thread.sleep(100);
        }
        assertEquals(script, expected, actual);
    }
    private WebView findWebView(View view) {
        if (view instanceof WebView) return (WebView) view;
        if (view instanceof ViewGroup) for (int i = 0; i < ((ViewGroup) view).getChildCount(); i++) {
            WebView found = findWebView(((ViewGroup) view).getChildAt(i)); if (found != null) return found;
        }
        return null;
    }
}
