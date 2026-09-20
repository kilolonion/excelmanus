package com.excelmanus.android;

import org.junit.Test;
import static org.junit.Assert.*;

public class PairingCodeTest {
    private String code(String server, long expiry) {
        return "excelmanus://pair?v=1&server=" + server + "&code=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG&expires=" + expiry;
    }
    @Test public void acceptsOnlyVersionedExpiringInvitation() {
        PairingCode parsed = PairingCode.parse(code("http%3A%2F%2F192.168.1.12%3A8787", 110000));
        assertEquals("http://192.168.1.12:8787", parsed.server.origin);
        assertTrue(parsed.server.insecure);
    }
    @Test public void rejectsCredentialsLoopbackPublicHttpAndDuplicateFields() {
        for (String server : new String[]{"http://127.0.0.1:8787", "http://localhost:8787", "http://8.8.8.8", "http://user:password@192.168.1.12", "http://192.168.1.12/path"}) {
            assertThrows(IllegalArgumentException.class, () -> PairingCode.parse(code(server, 110000)));
        }
        assertThrows(IllegalArgumentException.class, () -> PairingCode.parse(code("http://192.168.1.12", 110000) + "&v=1"));
    }
    @Test public void leavesExpiryToServerRegardlessOfPhoneClock() {
        assertEquals(1, PairingCode.parse(code("http://192.168.1.12", 1)).expiresAt);
        assertEquals(9999999999999L, PairingCode.parse(code("http://192.168.1.12", 9999999999999L)).expiresAt);
    }
    @Test public void rejectsInvalidExpiryAndUnrelatedQrCodes() {
        assertThrows(IllegalArgumentException.class, () -> PairingCode.parse(code("http://192.168.1.12", 0)));
        assertThrows(IllegalArgumentException.class, () -> PairingCode.parse("https://example.com"));
        assertThrows(IllegalArgumentException.class, () -> PairingCode.parse(code("http://192.168.1.12", 110000).replace("v=1", "v=2")));
    }
}
