package com.excelmanus.android;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import static org.junit.Assert.*;

public class StagedDownloadTest {
    @Rule public TemporaryFolder folder = new TemporaryFolder();

    @Test public void preservesBytesAndRemovesOnlyStagedFile() throws Exception {
        byte[] bytes = "中文 Excel 内容".getBytes(StandardCharsets.UTF_8);
        StagedDownload download = new StagedDownload(folder.getRoot(), "a", "../结果.xlsx", "application/octet-stream", bytes.length);
        download.append(0, bytes);
        download.finish();
        assertTrue(download.isComplete());
        assertEquals("结果.xlsx", download.filename);
        assertArrayEquals(bytes, Files.readAllBytes(download.file.toPath()));
        download.close();
        assertFalse(download.file.exists());
    }
    @Test public void rejectsMissingDuplicateOversizeAndIncompleteChunks() throws Exception {
        try (StagedDownload download = new StagedDownload(folder.getRoot(), "b", "x", "text/plain", 4)) {
            assertThrows(IOException.class, () -> download.append(1, new byte[]{1}));
            download.append(0, new byte[]{1, 2});
            assertThrows(IOException.class, () -> download.append(0, new byte[]{1}));
            assertThrows(IOException.class, () -> download.append(1, new byte[]{1, 2, 3}));
            assertThrows(IOException.class, download::finish);
            assertFalse(download.isComplete());
        }
        assertThrows(IOException.class, () -> new StagedDownload(folder.getRoot(), "x", "x", "x", StagedDownload.MAX_BYTES + 1));
    }
}
