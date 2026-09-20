package com.excelmanus.android;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;

/** Bounded, sequential transfer. Only a fully received file can reach the picker. */
final class StagedDownload implements AutoCloseable {
    static final long MAX_BYTES = 256L * 1024 * 1024;
    final String id;
    final String filename;
    final String mime;
    final File file;
    private final long expectedSize;
    private FileOutputStream output;
    private long received;
    private int nextSequence;
    private boolean complete;

    StagedDownload(File directory, String id, String filename, String mime, long size) throws IOException {
        if (size < -1 || size > MAX_BYTES) throw new IOException("单个文件最多保存 256 MB。");
        this.id = id;
        this.filename = ServerAddress.filename(filename);
        this.mime = mime != null && mime.matches("[\\w.+-]+/[\\w.+-]+") ? mime : "application/octet-stream";
        this.expectedSize = size;
        this.file = File.createTempFile("download-", ".part", directory);
        this.output = new FileOutputStream(file);
    }

    void append(int sequence, byte[] bytes) throws IOException {
        if (output == null || complete) throw new IOException("文件传输已经结束。");
        if (sequence != nextSequence || bytes.length > 49152 || received + bytes.length > MAX_BYTES
                || (expectedSize >= 0 && received + bytes.length > expectedSize)) {
            throw new IOException("文件分块或大小不正确，请重新下载。");
        }
        output.write(bytes);
        received += bytes.length;
        nextSequence++;
    }

    void finish() throws IOException {
        if (output == null || complete || (expectedSize >= 0 && received != expectedSize)) {
            throw new IOException("文件未接收完整，请重新下载。");
        }
        output.close();
        output = null;
        complete = true;
    }

    boolean isComplete() { return complete; }

    @Override public void close() {
        try { if (output != null) output.close(); } catch (IOException ignored) { }
        output = null;
        // This is an app-created cache file, never the user's original document.
        if (file.exists()) file.delete();
    }
}

