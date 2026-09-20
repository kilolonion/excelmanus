package com.excelmanus.android;

import android.app.Activity;
import android.net.Uri;
import android.provider.DocumentsContract;
import android.util.Base64;
import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebViewFeature;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** All file I/O is serialized off the UI thread; only complete files are offered for saving. */
final class DownloadController implements AutoCloseable {
    private static boolean cacheCleaned;
    interface Host {
        void chooseDestination(String filename, String mime);
        void notice(String message);
        void downloadBusy(boolean busy);
    }
    private final Activity activity;
    private final Host host;
    private final File directory;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private volatile boolean closed;
    private volatile boolean cancelled;
    private volatile HttpURLConnection connection;
    private StagedDownload transfer;
    private JavaScriptReplyProxy finishReply;
    private String finishRequestId;

    DownloadController(Activity activity, Host host) {
        this.activity = activity;
        this.host = host;
        directory = new File(activity.getCacheDir(), "downloads");
        directory.mkdirs();
        // No file is exposed outside this directory until the system picker returns.
        // Only reap leftovers once per process. A replaced WebView may still
        // be finishing/cancelling I/O in another controller's worker.
        synchronized (DownloadController.class) {
            if (!cacheCleaned) {
                File[] orphaned = directory.listFiles();
                if (orphaned != null) for (File file : orphaned) if (file.isFile()) file.delete();
                cacheCleaned = true;
            }
        }
    }

    void message(JSONObject message, JavaScriptReplyProxy reply) {
        String requestId = message.optString("requestId");
        if (closed) return;
        io.execute(() -> {
            try {
                if (closed) return;
                String type = message.getString("type");
                String id = message.optString("transferId");
                if (type.equals("saveBegin")) {
                    if (transfer != null) throw new IllegalStateException("已有文件正在保存，请稍候。");
                    long size = message.getLong("size");
                    if (size < 0 || id.length() > 100 || id.isEmpty()) throw new IllegalArgumentException("文件大小或标识不正确。");
                    transfer = new StagedDownload(directory, id, message.optString("filename"), message.optString("mime"), size);
                    cancelled = false;
                    busy(true);
                } else {
                    if (transfer == null || !transfer.id.equals(id)) throw new IllegalStateException("文件传输已经取消，请重试。");
                    if (type.equals("saveChunk")) {
                        String data = message.getString("data");
                        if (data.length() > 65536) throw new IllegalArgumentException("文件分块过大。");
                        transfer.append(message.getInt("sequence"), Base64.decode(data, Base64.NO_WRAP));
                    } else if (type.equals("saveFinish")) {
                        transfer.finish();
                        finishReply = reply;
                        finishRequestId = requestId;
                        showPicker();
                        return; // Acknowledge only when the user saves or cancels.
                    } else if (type.equals("saveAbort")) {
                        clean();
                    } else throw new IllegalArgumentException("不支持的文件操作。");
                }
                reply(reply, requestId, true, false, null);
            } catch (Exception error) {
                if (transfer != null && transfer.id.equals(message.optString("transferId"))) clean();
                reply(reply, requestId, false, false, error.getMessage());
            }
        });
    }

    void downloadUrl(ServerAddress server, String token, String url, String filename, String mime, String cookies) {
        if (closed) return;
        io.execute(() -> {
            if (closed) return;
            if (transfer != null) { notice("已有文件正在保存，请稍候。"); return; }
            cancelled = false;
            busy(true);
            try {
                String current = url;
                for (int redirects = 0; redirects <= 5; redirects++) {
                    if (cancelled || closed) throw new IllegalStateException("下载已取消。");
                    if (!server.owns(current)) throw new IllegalArgumentException("仅可下载当前服务器的文件。");
                    HttpURLConnection request = (HttpURLConnection) new URL(current).openConnection();
                    connection = request;
                    request.setInstanceFollowRedirects(false);
                    request.setConnectTimeout(15000);
                    request.setReadTimeout(30000);
                    if (!token.isEmpty()) request.setRequestProperty("Authorization", "Bearer " + token);
                    if (cookies != null && !cookies.isEmpty()) request.setRequestProperty("Cookie", cookies);
                    int status = request.getResponseCode();
                    if (status >= 300 && status < 400) {
                        String location = request.getHeaderField("Location");
                        if (location == null) throw new IllegalStateException("下载跳转地址为空。");
                        current = new URL(new URL(current), location).toString();
                        request.disconnect();
                        continue;
                    }
                    if (status < 200 || status >= 300) throw new IllegalStateException("下载失败（HTTP " + status + "）。");
                    transfer = new StagedDownload(directory, "http-download", filename, mime, request.getContentLengthLong());
                    try (InputStream input = request.getInputStream()) {
                        byte[] buffer = new byte[49152];
                        int sequence = 0;
                        int read;
                        while ((read = input.read(buffer)) != -1) {
                            if (cancelled || closed) throw new IllegalStateException("下载已取消。");
                            transfer.append(sequence++, java.util.Arrays.copyOf(buffer, read));
                        }
                    }
                    transfer.finish();
                    showPicker();
                    return;
                }
                throw new IllegalStateException("下载跳转次数过多。");
            } catch (Exception error) {
                clean();
                if (!cancelled && !closed) notice(error.getMessage() == null ? "文件下载失败。" : error.getMessage());
            } finally {
                HttpURLConnection request = connection;
                connection = null;
                if (request != null) request.disconnect();
            }
        });
    }

    private void showPicker() {
        StagedDownload ready = transfer;
        activity.runOnUiThread(() -> {
            if (!closed && !cancelled && ready != null) host.chooseDestination(ready.filename, ready.mime);
        });
    }

    void destination(Uri uri) {
        if (closed) { removeIncompleteDocument(uri); return; }
        io.execute(() -> {
            try {
                if (transfer == null || !transfer.isComplete() || closed || cancelled) {
                    removeIncompleteDocument(uri);
                    return;
                }
                if (uri == null) {
                    reply(finishReply, finishRequestId, true, true, null);
                    return;
                }
                try (InputStream input = new FileInputStream(transfer.file);
                     OutputStream output = activity.getContentResolver().openOutputStream(uri, "wt")) {
                    if (output == null) throw new IllegalStateException("无法写入所选位置。");
                    byte[] buffer = new byte[49152];
                    int read;
                    while ((read = input.read(buffer)) != -1) {
                        if (cancelled || closed) throw new IllegalStateException("保存已取消。");
                        output.write(buffer, 0, read);
                    }
                }
                reply(finishReply, finishRequestId, true, false, null);
                notice("文件已保存。");
            } catch (Exception error) {
                removeIncompleteDocument(uri);
                reply(finishReply, finishRequestId, false, false, "保存失败，请重新选择位置。");
                if (finishReply == null) notice("保存失败，请重新选择位置。");
            } finally { clean(); }
        });
    }

    void cancel() {
        cancelled = true;
        HttpURLConnection request = connection;
        if (request != null) request.disconnect();
        if (!closed) io.execute(() -> {
            reply(finishReply, finishRequestId, true, true, null);
            clean();
        });
    }

    private void removeIncompleteDocument(Uri uri) {
        // ACTION_CREATE_DOCUMENT creates a new document, never overwrites an existing one.
        if (uri != null) try { DocumentsContract.deleteDocument(activity.getContentResolver(), uri); } catch (Exception ignored) { }
    }
    private void clean() {
        if (transfer != null) transfer.close();
        transfer = null;
        finishReply = null;
        finishRequestId = null;
        busy(false);
    }
    private void busy(boolean value) { activity.runOnUiThread(() -> { if (!closed) host.downloadBusy(value); }); }
    private void notice(String value) { activity.runOnUiThread(() -> { if (!closed) host.notice(value); }); }
    private void reply(JavaScriptReplyProxy proxy, String requestId, boolean ok, boolean cancelled, String error) {
        if (proxy == null || requestId == null) return;
        try {
            JSONObject result = new JSONObject().put("requestId", requestId).put("ok", ok).put("cancelled", cancelled);
            if (error != null) result.put("error", error);
            activity.runOnUiThread(() -> {
                if (!closed && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
                    try { proxy.postMessage(result.toString()); } catch (Exception ignored) { }
                }
            });
        } catch (Exception ignored) { }
    }
    @Override public void close() {
        if (closed) return;
        cancelled = true;
        closed = true;
        HttpURLConnection request = connection;
        if (request != null) request.disconnect();
        io.execute(this::clean);
        io.shutdown();
    }
}
