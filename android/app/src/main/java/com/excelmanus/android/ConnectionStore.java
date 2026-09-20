package com.excelmanus.android;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class ConnectionStore {
    private static final String KEY = "excelmanus.connection.v1";
    private final SharedPreferences prefs;

    ConnectionStore(Context context) { prefs = context.getSharedPreferences("connection", Context.MODE_PRIVATE); }
    String address() { return prefs.getString("address", ""); }
    boolean allowHttp() { return prefs.getBoolean("allowHttp", false); }
    String lastPath() { return prefs.getString("lastPath", "/"); }
    void savePath(String path) {
        // Store only a same-origin pathname. Query strings may contain OAuth credentials.
        if (path != null && (path.equals("/") || path.matches("/chat/[A-Za-z0-9_-]+"))) {
            prefs.edit().putString("lastPath", path).apply();
        }
    }

    String token() throws Exception {
        String value = prefs.getString("token", "");
        if (value.isEmpty()) return "";
        String[] parts = value.split(":", 2);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }

    void save(ServerAddress address, String token) throws Exception {
        String encrypted = "";
        if (!token.isEmpty()) {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            encrypted = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":"
                    + Base64.encodeToString(cipher.doFinal(token.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
        }
        if (!prefs.edit().putString("address", address.origin).putBoolean("allowHttp", address.insecure)
                .putString("token", encrypted).putString("lastPath", "/").commit()) {
            throw new IllegalStateException("无法保存连接设置。");
        }
    }

    void clear() { prefs.edit().clear().apply(); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(KEY)) return ((KeyStore.SecretKeyEntry) store.getEntry(KEY, null)).getSecretKey();
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
        return generator.generateKey();
    }
}
