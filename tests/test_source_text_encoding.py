from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MOJIBAKE_MARKERS = {
    "web/src/lib/sse-event-handler.ts": [
        "鍏变韩 SSE 浜嬩欢",
        "`浠诲姟 ${",
        "宸插垱寤鸿鍒",
        "鈹€鈹€",
        "正在恢复事件?..",
        "¼ָʧܣڻԴ...",
        "已创建计划?{",
    ],
    "web/src/lib/chat-actions.ts": [
        "鍦ㄥ鎴风鏈湴",
        "鍩轰簬 RAF",
        "闈欓粯澶勭悊 鈥?",
        "ϴļ",
        "ϴͼƬ",
        "ͼƬ»ȡ",
        '"ʧ"',
    ],
    "web/src/components/providers/SessionSync.tsx": [
        "鍒锋柊鍚庢仮澶嶈矾鐢辩姸鎬",
        "鍚姩鏃舵媺鍙",
        "Demo sessions are local-only 鈥?",
        "最后一?assistant",
        "竞?",
    ],
    "web/src/stores/chat-store.ts": [
        "鍐呭瓨蹇€熺紦瀛",
        "锛圽",
        '娑夊強鏂囦欢"',
        "ϴ",
        "ͼƬ #",
        "֮ǰĶԻз",
    ],
}

BROKEN_COPY_MARKERS = {
    "web/src/components/admin/PoolTab.tsx": [
        "确认删除?",
    ],
}


def test_frontend_source_has_no_known_mojibake_markers() -> None:
    found: dict[str, list[str]] = {}

    for rel_path, markers in MOJIBAKE_MARKERS.items():
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        assert "\ufeff" not in text, f"{rel_path} should not contain a UTF-8 BOM"
        hits = [marker for marker in markers if marker in text]
        if hits:
            found[rel_path] = hits

    assert not found, f"found mojibake markers in source files: {found}"


def test_frontend_source_has_no_known_broken_copy_markers() -> None:
    found: dict[str, list[str]] = {}

    for rel_path, markers in BROKEN_COPY_MARKERS.items():
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        hits = [marker for marker in markers if marker in text]
        if hits:
            found[rel_path] = hits

    assert not found, f"found broken copy markers in source files: {found}"
