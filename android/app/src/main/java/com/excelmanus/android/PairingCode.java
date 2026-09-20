package com.excelmanus.android;

import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/** QR codes carry an expiring invitation, never a backend management token. */
public final class PairingCode {
    public final ServerAddress server;
    public final String code;
    public final long expiresAt;

    private PairingCode(ServerAddress server, String code, long expiresAt) {
        this.server = server; this.code = code; this.expiresAt = expiresAt;
    }

    public static PairingCode parse(String raw) {
        try {
            if (raw == null || raw.length() > 2048) throw new IllegalArgumentException();
            URI uri = new URI(raw);
            if (!"excelmanus".equals(uri.getScheme()) || !"pair".equals(uri.getHost()) || uri.getPort() != -1
                    || uri.getRawUserInfo() != null || uri.getRawFragment() != null
                    || !(uri.getPath() == null || uri.getPath().isEmpty()) || uri.getRawQuery() == null) throw new IllegalArgumentException();
            Map<String, String> values = new HashMap<>();
            for (String entry : uri.getRawQuery().split("&")) {
                String[] pair = entry.split("=", 2);
                if (pair.length != 2 || values.put(pair[0], URLDecoder.decode(pair[1], StandardCharsets.UTF_8.name())) != null) throw new IllegalArgumentException();
            }
            if (values.size() != 4 || !"1".equals(values.get("v")) || !values.getOrDefault("code", "").matches("[A-Za-z0-9_-]{43}")) throw new IllegalArgumentException();
            ServerAddress server = ServerAddress.parse(values.get("server"), true);
            // LAN invitations cannot point at localhost or a device-local service.
            String host = new URI(server.origin).getHost();
            if (server.insecure && !(host.startsWith("10.") || host.startsWith("192.168.") || host.matches("172\\.(1[6-9]|2[0-9]|3[01])\\..+"))) throw new IllegalArgumentException();
            long expires = Long.parseLong(values.get("expires"));
            // The computer enforces expiry and one-time use. Phone wall clocks
            // may differ by hours/days; they cannot validate a server timestamp.
            if (expires <= 0) throw new IllegalArgumentException();
            return new PairingCode(server, values.get("code"), expires);
        } catch (Exception error) {
            throw new IllegalArgumentException("这不是 ExcelManus 配对码。请扫描电脑侧边栏“扫码连接”中的二维码。");
        }
    }
}
