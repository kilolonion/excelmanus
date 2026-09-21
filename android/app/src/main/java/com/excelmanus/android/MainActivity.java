package com.excelmanus.android;

import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.webkit.CookieManager;
import android.webkit.MimeTypeMap;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import com.journeyapps.barcodescanner.ScanContract;
import com.journeyapps.barcodescanner.ScanOptions;
import com.google.zxing.client.android.Intents;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

public final class MainActivity extends ComponentActivity implements DownloadController.Host {
    private static final int GREEN = Color.rgb(22, 131, 93);
    private static final int INK = Color.rgb(30, 47, 40);
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private ConnectionStore store;
    private ServerAddress server;
    private String token = "";
    private WebView web;
    private final java.util.Set<WebView> popups = new java.util.HashSet<>();
    private DownloadController downloads;
    private DownloadController pickerOwner;
    private ValueCallback<Uri[]> fileCallback;
    private LinearLayout root;
    private FrameLayout content;
    private TextView subtitle;
    private ProgressBar progress;
    private EditText addressInput;
    private EditText tokenInput;
    private CheckBox httpInput;
    private TextView formStatus;
    private Button connectButton;
    private boolean settingsVisible;
    private boolean connecting;
    private int connectionAttempt;
    private PairingFlow pairing;
    private boolean scanning;
    private boolean restoringUpload;
    private boolean restoringSave;
    private final ActivityResultLauncher<ScanOptions> scanner = registerForActivityResult(new ScanContract(), result -> {
        scanning = false;
        if (result.getContents() != null && pairing != null) pairing.review(result.getContents());
        else if (result.getOriginalIntent() != null && result.getOriginalIntent().getBooleanExtra(Intents.Scan.MISSING_CAMERA_PERMISSION, false)) cameraPermissionHelp();
    });

    private final ActivityResultLauncher<Intent> filePicker = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                ValueCallback<Uri[]> callback = fileCallback;
                fileCallback = null;
                if (callback == null) {
                    if (restoringUpload) notice("客户端已重新打开，请再次选择需要上传的文件。");
                    restoringUpload = false;
                    return;
                }
                Uri[] files = WebChromeClient.FileChooserParams.parseResult(result.getResultCode(), result.getData());
                if (files != null) {
                    for (Uri file : files) {
                        if (!"content".equals(file.getScheme())) { files = null; notice("请选择系统文件列表中的文档。"); break; }
                    }
                }
                callback.onReceiveValue(files);
            });
    private final ActivityResultLauncher<Intent> savePicker = registerForActivityResult(
            new ActivityResultContracts.StartActivityForResult(), result -> {
                DownloadController owner = pickerOwner;
                pickerOwner = null;
                Uri uri = result.getResultCode() == RESULT_OK && result.getData() != null ? result.getData().getData() : null;
                if (owner != null) owner.destination(uri);
                else if (restoringSave) {
                    // The system recreated this Activity while its document picker
                    // was open; the old in-memory transfer can no longer complete.
                    if (uri != null) network.execute(() -> {
                        try { android.provider.DocumentsContract.deleteDocument(getContentResolver(), uri); }
                        catch (Exception ignored) { }
                    });
                    notice("客户端已重新打开，文件尚未保存，请重新下载。");
                }
                restoringSave = false;
            });

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        restoringUpload = state != null && state.getBoolean("uploadPickerPending");
        restoringSave = state != null && state.getBoolean("savePickerPending");
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        store = new ConnectionStore(this);
        pairing = new PairingFlow(this, new PairingFlow.Host() {
            @Override public boolean paired(ServerAddress selected, String credential) {
                try { store.save(selected, credential); }
                catch (Exception failure) { return false; }
                int attempt = ++connectionAttempt;
                connecting = false;
                destroyWeb(); server = selected; token = credential;
                WebStorage.getInstance().deleteAllData();
                CookieManager.getInstance().removeAllCookies(removed -> {
                    if (!isDestroyed() && attempt == connectionAttempt) openWorkspace("/");
                });
                return true;
            }
            @Override public void scanAgain() { scanPairing(); }
        });
        buildChrome();
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override public void handleOnBackPressed() {
                WindowInsetsCompat insets = ViewCompat.getRootWindowInsets(root);
                if (insets != null && insets.isVisible(WindowInsetsCompat.Type.ime())) {
                    WindowCompat.getInsetsController(getWindow(), root).hide(WindowInsetsCompat.Type.ime());
                    return;
                }
                if (settingsVisible && web != null) { showWeb(); return; }
                if (web != null && !settingsVisible) {
                    WebView current = web;
                    current.evaluateJavascript("Boolean(window.excelManusAndroid && window.excelManusAndroid.handleBack())", handled -> {
                        if (current != web) return;
                        if ("true".equals(handled)) return;
                        if (current.canGoBack()) current.goBack(); else moveTaskToBack(true);
                    });
                } else moveTaskToBack(true);
            }
        });
        if (!store.address().isEmpty()) {
            try {
                server = ServerAddress.parse(store.address(), store.allowHttp());
                token = store.token();
                openWorkspace(store.lastPath());
            } catch (Exception error) { showSettings("请重新输入管理令牌并连接。"); }
        } else showSettings("");
        if (state != null && state.getBoolean("pairingPending")) notice("绑定引导已重新打开，请再扫描电脑上的二维码。");
    }

    private void buildChrome() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(247, 249, 247));
        // The Android client is a full-screen WebView shell.  The old native
        // title/address bar duplicated the web app chrome and consumed a
        // sizeable part of the phone viewport, which also made the responsive
        // workspace appear vertically compressed.  Keep the edge-to-edge
        // system-bar insets on the root, but let the web surface occupy the
        // rest of the window.
        ViewCompat.setOnApplyWindowInsetsListener(root, (view, insets) -> {
            Insets bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            view.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            return insets;
        });
        // Keep a status holder for existing connection/download callbacks. It
        // is intentionally detached from the view tree so it cannot re-create
        // a visible top strip; the web app owns all user-facing chrome now.
        subtitle = label("", 1, Color.TRANSPARENT);
        subtitle.setVisibility(View.GONE);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);
        content = new FrameLayout(this);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        WindowCompat.getInsetsController(getWindow(), root).setAppearanceLightStatusBars(true);
        WindowCompat.getInsetsController(getWindow(), root).setAppearanceLightNavigationBars(true);
    }

    private void showMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        menu.getMenu().add("扫一扫连接电脑").setOnMenuItemClickListener(item -> { scanPairing(); return true; });
        menu.getMenu().add("连接设置").setOnMenuItemClickListener(item -> { showSettings(""); return true; });
        menu.getMenu().add("重新加载").setOnMenuItemClickListener(item -> { if (web != null) { showWeb(); web.reload(); } return true; });
        menu.getMenu().add("取消文件保存").setOnMenuItemClickListener(item -> { if (downloads != null) downloads.cancel(); return true; });
        menu.getMenu().add("断开并清除连接").setOnMenuItemClickListener(item -> {
            connectionAttempt++;
            connecting = false;
            destroyWeb();
            store.clear();
            token = "";
            server = null;
            WebStorage.getInstance().deleteAllData();
            CookieManager.getInstance().removeAllCookies(null);
            showSettings("连接已清除。");
            return true;
        });
        menu.show();
    }

    private void scanPairing() {
        if (scanning || connecting) return;
        pairing.cancel();
        scanning = true;
        ScanOptions options = new ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setCaptureActivity(PairingCaptureActivity.class).setOrientationLocked(false)
                .addExtra(Intents.Scan.SHOW_MISSING_CAMERA_PERMISSION_DIALOG, false)
                .setBeepEnabled(false).setPrompt("扫描电脑侧边栏“扫码连接”中的二维码");
        try { scanner.launch(options); }
        catch (Exception error) { scanning = false; cameraPermissionHelp(); }
    }

    private void cameraPermissionHelp() {
        if (isDestroyed() || isFinishing()) return;
        new AlertDialog.Builder(this).setTitle("需要相机权限才能扫码")
                .setMessage("请在应用权限中允许使用相机，再返回点击扫一扫。也可以在连接设置中手动输入电脑地址。")
                .setPositiveButton("打开应用设置", (dialog, which) -> {
                    try { startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()))); }
                    catch (ActivityNotFoundException unavailable) { notice("请在系统设置中找到 ExcelManus 并开启相机权限。"); }
                }).setNegativeButton("暂不扫码", null).show();
    }

    private void showSettings(String message) {
        settingsVisible = true;
        content.removeAllViews();
        subtitle.setText("连接你的工作区");
        progress.setVisibility(View.GONE);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(dp(26), dp(32), dp(26), dp(24));
        TextView title = label("让手机接入\n你的 Excel 工作区", 29, INK);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        form.addView(title);
        TextView description = label("连接已运行的 ExcelManus，上传表格、发起任务，并将结果保存到手机。", 15, Color.DKGRAY);
        description.setLineSpacing(dp(4), 1);
        addWithMargin(form, description, 16);
        Button scan = new Button(this);
        scan.setText("扫一扫连接电脑");
        scan.setTextColor(Color.WHITE);
        scan.setBackground(tinted(GREEN));
        scan.setOnClickListener(view -> scanPairing());
        addWithMargin(form, scan, 20);
        addWithMargin(form, label("在电脑侧边栏点击设置旁的扫码图标，按引导连接同一 Wi-Fi 并生成二维码。", 13, Color.DKGRAY), 8);
        Button wifi = new Button(this);
        wifi.setText("打开 Wi-Fi 设置");
        wifi.setOnClickListener(view -> pairing.openWifi());
        addWithMargin(form, wifi, 8);
        addWithMargin(form, label("或手动填写连接信息", 13, Color.DKGRAY), 20);
        addWithMargin(form, label("服务器地址", 14, INK), 30);
        addressInput = input("https://excel.example.com", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        addressInput.setContentDescription("服务器地址");
        addressInput.setText(store.address());
        addWithMargin(form, addressInput, 8);
        addWithMargin(form, label("管理令牌", 14, INK), 20);
        tokenInput = input("由你的电脑或服务器提供", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        tokenInput.setContentDescription("管理令牌");
        tokenInput.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO);
        try { tokenInput.setText(store.token()); } catch (Exception ignored) { }
        addressInput.addTextChangedListener(new android.text.TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) { }
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                // An edited endpoint must never inherit another server's credential.
                tokenInput.setText("");
            }
            @Override public void afterTextChanged(android.text.Editable value) { }
        });
        addWithMargin(form, tokenInput, 8);
        httpInput = new CheckBox(this);
        httpInput.setText("允许连接可信局域网的 HTTP 地址");
        httpInput.setTextSize(13);
        httpInput.setChecked(store.allowHttp());
        addWithMargin(form, httpInput, 16);
        TextView hint = label("局域网示例：http://192.168.1.10:8787\n填写电脑的网站地址；localhost 指的是手机本身。HTTP 不加密，仅在可信网络使用。", 12, Color.DKGRAY);
        hint.setLineSpacing(dp(3), 1);
        addWithMargin(form, hint, 4);
        connectButton = new Button(this);
        connectButton.setText(connecting ? "正在检查连接…" : "连接工作区");
        connectButton.setTextColor(Color.WHITE);
        connectButton.setBackground(tinted(GREEN));
        connectButton.setEnabled(!connecting);
        connectButton.setOnClickListener(view -> connect());
        addWithMargin(form, connectButton, 26);
        if (web != null) {
            Button cancel = new Button(this);
            cancel.setText("返回当前工作区");
            cancel.setOnClickListener(view -> showWeb());
            addWithMargin(form, cancel, 8);
        }
        formStatus = label(message, 13, Color.rgb(156, 53, 38));
        formStatus.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        addWithMargin(form, formStatus, 12);
        scroll.addView(form);
        content.addView(scroll);
    }

    private void connect() {
        if (connecting) return;
        final ServerAddress selected;
        final String selectedToken = tokenInput.getText().toString().trim();
        try {
            selected = ServerAddress.parse(addressInput.getText().toString(), httpInput.isChecked());
            if (!selectedToken.isEmpty() && (selectedToken.length() < 16 || selectedToken.indexOf('\r') >= 0 || selectedToken.indexOf('\n') >= 0)) {
                throw new IllegalArgumentException("管理令牌至少需要 16 个字符，且不能包含换行。");
            }
        } catch (IllegalArgumentException error) { formStatus.setText(error.getMessage()); return; }
        connecting = true;
        connectButton.setEnabled(false);
        connectButton.setText("正在检查连接…");
        formStatus.setText("");
        int attempt = ++connectionAttempt;
        network.execute(() -> {
            String error = null;
            try {
                JSONObject health = new JSONObject(readHealth(selected));
                if (!health.has("version") || !health.has("api_schema_version")) throw new Exception("地址未返回 ExcelManus 服务，请检查网站和 API 是否使用同一地址。");
                if (health.optBoolean("auth_required") && selectedToken.isEmpty()) throw new Exception("服务器需要管理令牌。");
                checkAuthorization(selected, selectedToken);
            } catch (Exception problem) { error = friendlyConnectionError(problem); }
            String result = error;
            runOnUiThread(() -> {
                if (isDestroyed() || attempt != connectionAttempt) return;
                connecting = false;
                if (result != null) { showSettings(result); addressInput.setText(selected.origin); tokenInput.setText(selectedToken); httpInput.setChecked(selected.insecure); return; }
                try { store.save(selected, selectedToken); }
                catch (Exception failure) { showSettings("无法安全保存连接设置，请重试。"); return; }
                destroyWeb();
                server = selected;
                token = selectedToken;
                WebStorage.getInstance().deleteAllData();
                CookieManager.getInstance().removeAllCookies(removed -> {
                    if (!isDestroyed() && attempt == connectionAttempt) openWorkspace("/");
                });
            });
        });
    }

    private String readHealth(ServerAddress address) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address.origin + "/api/v1/health").openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        try {
            if (connection.getResponseCode() != 200) throw new Exception("无法访问健康检查，请填写最终网站地址并检查 API 转发。");
            try (InputStream input = connection.getInputStream(); java.io.ByteArrayOutputStream body = new java.io.ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096];
                int read;
                while ((read = input.read(buffer)) >= 0) {
                    if (body.size() + read > 256 * 1024) throw new Exception("服务响应格式不正确。");
                    body.write(buffer, 0, read);
                }
                return body.toString(StandardCharsets.UTF_8.name());
            }
        } finally { connection.disconnect(); }
    }

    private void checkAuthorization(ServerAddress address, String value) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(address.origin + "/api/v1/sessions").openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        if (!value.isEmpty()) connection.setRequestProperty("Authorization", "Bearer " + value);
        try {
            int status = connection.getResponseCode();
            if (status == 401 || status == 403) throw new Exception("管理令牌不正确或已失效。");
            if (status != 200) throw new Exception("服务尚不可用（HTTP " + status + "），请检查后端。");
        } finally { connection.disconnect(); }
    }

    private String friendlyConnectionError(Exception error) {
        if (error instanceof javax.net.ssl.SSLException) return "HTTPS 证书校验失败，请使用有效证书。";
        if (error instanceof java.net.SocketTimeoutException) return "连接超时，请确认电脑正在运行，且手机能够访问该网络。";
        if (error instanceof java.io.IOException) return "无法连接服务器，请检查地址、网络和电脑防火墙。";
        if (error instanceof org.json.JSONException) return "该地址没有返回 ExcelManus API，请检查网站地址。";
        return error.getMessage() == null ? "连接失败，请检查服务。" : error.getMessage();
    }

    // Both optional WebView features are checked before constructing this session.
    @SuppressLint({"SetJavaScriptEnabled", "RequiresFeature"})
    private void openWorkspace(String path) {
        if (server == null || isDestroyed()) return;
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
                || !WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            showSettings("请更新 Android System WebView 或 Chrome 后重试。");
            return;
        }
        ((InputMethodManager) getSystemService(INPUT_METHOD_SERVICE)).hideSoftInputFromWindow(root.getWindowToken(), 0);
        web = new WebView(this);
        final WebView session = web;
        final ServerAddress address = server;
        final String credential = token;
        downloads = new DownloadController(this, this);
        final DownloadController sessionDownloads = downloads;
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setUserAgentString(settings.getUserAgentString() + " ExcelManusAndroid/1");
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WorkspaceClient(address));
        web.setWebChromeClient(new WorkspaceChrome());
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> {
            if (!isCurrent(session)) return;
            if (address.owns(url)) sessionDownloads.downloadUrl(address, credential, url, URLUtil.guessFileName(url, disposition, mime), mime, CookieManager.getInstance().getCookie(url));
            else notice("仅支持保存当前服务器的文件。");
        });
        WebViewCompat.addWebMessageListener(web, "ExcelManusNative", Collections.singleton(server.origin), (view, message, origin, mainFrame, reply) -> {
            if (!isCurrent(view) || !mainFrame || !address.owns(origin.toString()) || !address.owns(view.getUrl())) return;
            try {
                String data = message.getData();
                if (data == null || data.length() > 70000) return;
                JSONObject payload = new JSONObject(data);
                if ("scanPairing".equals(payload.optString("type"))) {
                    reply.postMessage(new JSONObject().put("requestId", payload.optString("requestId")).put("ok", true).toString());
                    scanPairing();
                } else if ("connectionSettings".equals(payload.optString("type"))) {
                    reply.postMessage(new JSONObject().put("requestId", payload.optString("requestId")).put("ok", true).toString());
                    runOnUiThread(() -> showSettings(""));
                } else if ("notice".equals(payload.optString("type"))) {
                    String text = payload.optString("message", "文件操作失败。");
                    notice(text.substring(0, Math.min(text.length(), 200)));
                    reply.postMessage(new JSONObject().put("requestId", payload.optString("requestId")).put("ok", true).toString());
                } else if ("copyText".equals(payload.optString("type"))) {
                    String text = payload.getString("text");
                    boolean accepted = text.length() <= 16384;
                    if (accepted) ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("ExcelManus", text));
                    reply.postMessage(new JSONObject().put("requestId", payload.optString("requestId")).put("ok", accepted)
                            .put("error", "复制内容过长，请缩小选择范围。").toString());
                } else sessionDownloads.message(payload, reply);
            } catch (Exception ignored) { notice("文件操作失败，请重试。"); }
        });
        try (InputStream input = getAssets().open("bridge.js")) {
            java.io.ByteArrayOutputStream source = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = input.read(buffer)) != -1) source.write(buffer, 0, read);
            JSONObject config = new JSONObject().put("origin", server.origin).put("token", token);
            String script = "window.__EXCELMANUS_ANDROID_CONFIG__=" + config + ";\n" + source.toString(StandardCharsets.UTF_8.name());
            WebViewCompat.addDocumentStartJavaScript(web, script, Collections.singleton(server.origin));
        } catch (Exception error) { destroyWeb(); showSettings("客户端初始化失败，请重新连接。"); return; }
        showWeb();
        String start = path != null && (path.equals("/") || path.matches("/chat/[A-Za-z0-9_-]+")) ? path : "/";
        web.loadUrl(server.origin + start);
    }

    private void showWeb() {
        if (web == null) return;
        settingsVisible = false;
        content.removeAllViews();
        if (web.getParent() instanceof ViewGroup) ((ViewGroup) web.getParent()).removeView(web);
        content.addView(web, new FrameLayout.LayoutParams(-1, -1));
        subtitle.setText(server.origin);
    }

    private final class WorkspaceClient extends WebViewClient {
        private final ServerAddress address;
        WorkspaceClient(ServerAddress address) { this.address = address; }
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (!isCurrent(view)) return true;
            String url = request.getUrl().toString();
            if (address.owns(url)) return false;
            if (request.isForMainFrame()) openExternal(url);
            return true;
        }
        @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            // WebView invokes this off the UI thread. Use the immutable session origin.
            if (address.allowsRequest(request.getUrl().toString())) return null;
            return new WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", Collections.emptyMap(), new ByteArrayInputStream(new byte[0]));
        }
        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
            if (!isCurrent(view)) return;
            if (!address.owns(url)) { view.stopLoading(); return; }
            if (downloads != null) downloads.cancel();
            progress.setVisibility(View.VISIBLE);
        }
        @Override public void onPageFinished(WebView view, String url) {
            if (!isCurrent(view)) return;
            progress.setVisibility(View.GONE);
            if (address.owns(url)) store.savePath(Uri.parse(url).getPath());
        }
        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (isCurrent(view) && request.isForMainFrame()) showLoadError("工作区暂时无法打开，请检查网络或服务是否运行。");
        }
        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (isCurrent(view) && request.isForMainFrame() && response.getStatusCode() >= 400) showLoadError("网站返回 HTTP " + response.getStatusCode() + "，请检查服务地址。");
        }
        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel();
            if (isCurrent(view)) showLoadError("HTTPS 证书校验失败，请检查服务器证书。");
        }
        @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            if (isCurrent(view)) {
                destroyWeb();
                showSettings("页面进程已关闭，请重新连接。服务端任务可在连接后继续查看。");
            }
            return true;
        }
    }

    private final class WorkspaceChrome extends WebChromeClient {
        @Override public void onProgressChanged(WebView view, int value) { if (isCurrent(view)) progress.setProgress(value); }
        @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams parameters) {
            if (fileCallback != null) { callback.onReceiveValue(null); return true; }
            if (!isCurrent(view) || server == null || !server.owns(view.getUrl())) { callback.onReceiveValue(null); return true; }
            fileCallback = callback;
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, parameters.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
            String[] accepts = parameters.getAcceptTypes();
            java.util.ArrayList<String> types = new java.util.ArrayList<>();
            for (String accept : accepts) for (String entry : accept.split(",")) {
                String type = entry.trim().toLowerCase(java.util.Locale.ROOT);
                if (type.startsWith(".")) type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(type.substring(1));
                if (type != null && type.matches("[\\w.+*-]+/[\\w.+*-]+") && !types.contains(type)) types.add(type);
            }
            if (!types.isEmpty()) intent.putExtra(Intent.EXTRA_MIME_TYPES, types.toArray(new String[0]));
            try { filePicker.launch(intent); }
            catch (ActivityNotFoundException error) { fileCallback = null; callback.onReceiveValue(null); notice("设备上没有可用的文件选择器。"); }
            return true;
        }
        @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean gesture, android.os.Message message) {
            if (!isCurrent(view)) return false;
            // Popup gets no native bridge or credentials; its first real URL goes to the browser.
            WebView popup = new WebView(MainActivity.this);
            popups.add(popup);
            popup.getSettings().setAllowFileAccess(false);
            popup.getSettings().setAllowContentAccess(false);
            popup.setWebViewClient(new WebViewClient() {
                private void dispatch(String url) {
                    if (url == null || url.equals("about:blank")) return;
                    if (!isCurrent(view) || !popups.contains(popup)) { closePopup(popup); return; }
                    if (server != null && server.owns(url) && web != null) web.loadUrl(url); else openExternal(url);
                    popup.post(() -> closePopup(popup));
                }
                @Override public boolean shouldOverrideUrlLoading(WebView child, WebResourceRequest request) { dispatch(request.getUrl().toString()); return true; }
                @Override public void onPageStarted(WebView child, String url, android.graphics.Bitmap icon) { dispatch(url); }
                @Override public boolean onRenderProcessGone(WebView child, RenderProcessGoneDetail detail) { closePopup(child); return true; }
            });
            ((WebView.WebViewTransport) message.obj).setWebView(popup);
            message.sendToTarget();
            popup.postDelayed(() -> closePopup(popup), 30000);
            return true;
        }
    }

    private boolean isCurrent(WebView view) { return view == web && !isDestroyed() && !isFinishing(); }
    private void closePopup(WebView popup) {
        if (popups.remove(popup)) { popup.stopLoading(); popup.destroy(); }
    }

    private void openExternal(String url) {
        try {
            Uri target = Uri.parse(url);
            if (!"https".equalsIgnoreCase(target.getScheme()) || target.getHost() == null || target.getUserInfo() != null) {
                notice("该链接无法在客户端中打开。"); return;
            }
            startActivity(new Intent(Intent.ACTION_VIEW, target).addCategory(Intent.CATEGORY_BROWSABLE));
        } catch (Exception error) { notice("没有可用的浏览器，请安装浏览器后重试。"); }
    }

    private void showLoadError(String message) {
        progress.setVisibility(View.GONE);
        showSettings(message);
    }
    @Override public void chooseDestination(String filename, String mime) {
        if (pickerOwner != null) { downloads.cancel(); notice("请先完成当前保存操作。"); return; }
        pickerOwner = downloads;
        try {
            savePicker.launch(new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                    .setType(mime).putExtra(Intent.EXTRA_TITLE, filename));
        } catch (ActivityNotFoundException error) { pickerOwner = null; downloads.cancel(); notice("设备上没有可用的保存位置选择器。"); }
    }
    @Override public void notice(String message) { if (!isDestroyed()) Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
    @Override public void downloadBusy(boolean busy) { if (!settingsVisible) subtitle.setText(busy ? "正在准备保存文件…" : server == null ? "" : server.origin); }

    @Override protected void onResume() {
        super.onResume();
        if (web != null) {
            web.onResume();
            // Existing SessionSync uses visibilitychange to refresh and resume SSE.
            web.evaluateJavascript("document.dispatchEvent(new Event('visibilitychange'));window.dispatchEvent(new Event('online'));", null);
        }
    }
    @Override protected void onPause() {
        if (web != null) { web.onPause(); if (server != null && server.owns(web.getUrl())) store.savePath(Uri.parse(web.getUrl()).getPath()); }
        super.onPause();
    }
    @Override protected void onSaveInstanceState(Bundle state) {
        state.putBoolean("uploadPickerPending", fileCallback != null || restoringUpload);
        state.putBoolean("savePickerPending", pickerOwner != null || restoringSave);
        state.putBoolean("pairingPending", pairing != null && pairing.isActive());
        // Store only operation markers here; tokens remain in the Keystore-backed store.
        super.onSaveInstanceState(state);
    }
    private void destroyWeb() {
        for (WebView popup : new java.util.ArrayList<>(popups)) closePopup(popup);
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        if (downloads != null) { downloads.close(); downloads = null; }
        if (web != null) {
            WebView previous = web;
            web = null;
            if (previous.getParent() instanceof ViewGroup) ((ViewGroup) previous.getParent()).removeView(previous);
            previous.stopLoading();
            previous.destroy();
        }
    }
    @Override protected void onDestroy() { connectionAttempt++; if (pairing != null) pairing.close(); destroyWeb(); network.shutdownNow(); super.onDestroy(); }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView label(String value, int size, int color) { TextView text = new TextView(this); text.setText(value); text.setTextSize(size); text.setTextColor(color); return text; }
    private EditText input(String hint, int type) {
        EditText input = new EditText(this);
        input.setHint(hint); input.setInputType(type); input.setSingleLine(true); input.setTextSize(15);
        input.setPadding(dp(14), dp(12), dp(14), dp(12)); input.setBackground(tinted(Color.WHITE));
        return input;
    }
    private GradientDrawable tinted(int color) { GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(12)); return shape; }
    private void addWithMargin(LinearLayout parent, View view, int top) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(top); parent.addView(view, params);
    }
}
