package com.excelmanus.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class ServerAddressTest {
    @Test public void canonicalOriginAndExactMembership() {
        ServerAddress server = ServerAddress.parse(" HTTPS://EXAMPLE.com:443/ ", false);
        assertEquals("https://example.com", server.origin);
        assertTrue(server.owns("https://example.com/api/v1/sessions"));
        assertTrue(server.owns("https://EXAMPLE.com:443/chat/abc?x=1"));
        for (String url : new String[]{"https://example.com.evil.test/", "https://evil.test/?example.com", "http://example.com/", "https://example.com:8443/", "https://user@example.com/", "file:///etc/passwd"}) {
            assertFalse(url, server.owns(url));
        }
    }
    @Test public void rejectsCredentialsSubpathsAndInvalidAddresses() {
        for (String url : new String[]{"example.com", "javascript:alert(1)", "https://u:p@example.com", "https://example.com/api", "https://example.com?token=secret", "https://example.com/#secret", "https://example.com:0", "https://example.com:99999", "http://public.example.com"}) {
            assertThrows(url, IllegalArgumentException.class, () -> ServerAddress.parse(url, true));
        }
    }
    @Test public void cleartextRequiresOptInAndPrivateLiteral() {
        assertThrows(IllegalArgumentException.class, () -> ServerAddress.parse("http://192.168.1.5:3000", false));
        for (String host : new String[]{"192.168.1.5", "10.0.2.2", "172.16.0.8", "127.0.0.1", "[::1]"}) {
            assertTrue(ServerAddress.parse("http://" + host + ":3000", true).insecure);
        }
        for (String host : new String[]{"172.32.0.1", "192.168.999.1", "192.168.001.2", "8.8.8.8", "lan.example.com"}) {
            assertThrows(host, IllegalArgumentException.class, () -> ServerAddress.parse("http://" + host, true));
        }
        ServerAddress local = ServerAddress.parse("http://10.0.2.2:3000", true);
        assertTrue(local.allowsRequest("http://10.0.2.2:3000/api/v1/health"));
        assertFalse(local.allowsRequest("http://10.0.2.2:8000/api/v1/health"));
        assertFalse(local.allowsRequest("http://evil.example/"));
    }
    @Test public void sanitizesDownloadNamesWithoutLosingChinese() {
        assertEquals("本月结果.xlsx", ServerAddress.filename("C:\\报表\\本月结果.xlsx"));
        assertEquals("report.xlsx", ServerAddress.filename("../report\n.xlsx"));
        assertEquals("download", ServerAddress.filename(".."));
    }
}
