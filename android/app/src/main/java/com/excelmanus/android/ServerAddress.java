package com.excelmanus.android;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** One explicit, root-mounted server. No token-bearing URL or origin wildcards. */
public final class ServerAddress {
    public final String origin;
    public final boolean insecure;
    private final URI uri;

    private ServerAddress(URI uri) {
        this.uri = uri;
        this.origin = uri.toASCIIString();
        this.insecure = "http".equals(uri.getScheme());
    }

    public static ServerAddress parse(String input, boolean allowLanHttp) {
        try {
            URI value = new URI(input.trim());
            String scheme = value.getScheme() == null ? "" : value.getScheme().toLowerCase(Locale.ROOT);
            String host = value.getHost();
            String path = value.getRawPath();
            if ((!scheme.equals("https") && !scheme.equals("http")) || host == null
                    || value.getRawUserInfo() != null || value.getRawQuery() != null
                    || value.getRawFragment() != null || (path != null && !path.isEmpty() && !path.equals("/"))
                    || value.getPort() == 0 || value.getPort() > 65535) {
                throw new IllegalArgumentException("请填写网站根地址，例如 https://excel.example.com，不要附带路径或令牌。");
            }
            host = host.toLowerCase(Locale.ROOT);
            if (scheme.equals("http") && (!allowLanHttp || !isPrivateHost(host))) {
                throw new IllegalArgumentException("请使用 HTTPS；局域网 HTTP 仅支持私有 IP，并需勾选下方选项。");
            }
            int port = value.getPort();
            if ((scheme.equals("https") && port == 443) || (scheme.equals("http") && port == 80)) port = -1;
            return new ServerAddress(new URI(scheme, null, host, port, null, null, null));
        } catch (URISyntaxException | NullPointerException e) {
            throw new IllegalArgumentException("服务器地址格式不正确。", e);
        }
    }

    private static boolean isPrivateHost(String host) {
        if (host.equals("localhost") || host.equals("[::1]") || host.equals("::1")) return true;
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) return false;
        int[] octets = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!parts[i].matches("0|[1-9][0-9]{0,2}")) return false;
            octets[i] = Integer.parseInt(parts[i]);
            if (octets[i] > 255) return false;
        }
        return octets[0] == 10 || octets[0] == 127
                || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
                || (octets[0] == 192 && octets[1] == 168);
    }

    public boolean owns(String url) {
        try {
            URI target = new URI(url);
            return target.getRawUserInfo() == null && uri.getScheme().equalsIgnoreCase(target.getScheme())
                    && uri.getHost().equalsIgnoreCase(target.getHost()) && port(uri) == port(target);
        } catch (Exception ignored) { return false; }
    }

    public boolean allowsRequest(String url) {
        try {
            URI target = new URI(url);
            String scheme = target.getScheme();
            if ("http".equalsIgnoreCase(scheme)) return insecure && owns(url);
            return "https".equalsIgnoreCase(scheme) || "blob".equalsIgnoreCase(scheme)
                    || "data".equalsIgnoreCase(scheme) || "about:blank".equals(url);
        } catch (Exception ignored) { return false; }
    }

    private static int port(URI value) {
        return value.getPort() >= 0 ? value.getPort() : ("https".equalsIgnoreCase(value.getScheme()) ? 443 : 80);
    }

    public static String filename(String input) {
        String name = input == null ? "" : input.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1).replaceAll("[\\p{Cntrl}\\u202A-\\u202E\\u2066-\\u2069]", "").trim();
        if (name.isEmpty() || name.equals(".") || name.equals("..")) return "download";
        return name.length() > 180 ? name.substring(0, 180) : name;
    }
}

