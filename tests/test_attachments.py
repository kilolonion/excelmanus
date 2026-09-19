"""DSH-aligned attachment / request / Files / migrate tests."""

from __future__ import annotations

import base64
import json
import time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from excelmanus.attachments.admit import admit_image_bytes
from excelmanus.attachments.files_api import (
    apply_files_transport,
    files_api_enabled,
    invalidate_file_ids,
    is_files_quota_error,
    is_stale_file_error,
)
from excelmanus.attachments.migrate import migrate_messages
from excelmanus.attachments.normalize import detect_image, normalize_image
from excelmanus.attachments.offload import offloaded_image_prefix_count
from excelmanus.attachments.project import assemble_model_request
from excelmanus.attachments.request import request_image_dimensions
from excelmanus.attachments.store import AttachmentStore, reset_attachment_store
from excelmanus.attachments.types import (
    ImageAttachmentRef,
    ImageRequestPolicy,
    RequestImageOffloadPolicy,
)
from excelmanus.config import ExcelManusConfig
from excelmanus.memory import ConversationMemory


MIN_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("EXCELMANUS_HOME", str(tmp_path))
    reset_attachment_store()
    yield
    reset_attachment_store()


def _png_bytes(width: int = 8, height: int = 8, *, mode: str = "RGB", color=(255, 0, 0)) -> bytes:
    if mode == "RGBA":
        fill = (*color[:3], 128)
    else:
        fill = color
    image = Image.new(mode, (width, height), fill)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _palette_png(*, with_alpha: bool = False) -> bytes:
    image = Image.new("P", (16, 16), 1)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    buf = BytesIO()
    kwargs: dict = {"format": "PNG"}
    if with_alpha:
        kwargs["transparency"] = 0
    image.save(buf, **kwargs)
    return buf.getvalue()


def _ref_message(ref: ImageAttachmentRef, text: str = "see") -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image", "attachment": ref.to_dict()},
        ],
    }


class TestNormalize:
    def test_palette_converts_to_8bit(self, tmp_path) -> None:
        raw = _palette_png()
        detected = detect_image(raw)
        assert detected.mode == "P"
        normalized = normalize_image(raw, detected)
        assert normalized.media_type in {"image/jpeg", "image/png", "image/webp"}
        with Image.open(BytesIO(normalized.data)) as probe:
            assert probe.mode in {"RGB", "RGBA", "L", "LA"}

    def test_clean_png_passthrough(self) -> None:
        raw = _png_bytes(32, 24)
        detected = detect_image(raw)
        normalized = normalize_image(raw, detected)
        assert normalized.data == raw
        assert normalized.media_type == "image/png"
        assert (normalized.width, normalized.height) == (32, 24)

    def test_alpha_palette_encodes_webp_ladder(self) -> None:
        raw = _palette_png(with_alpha=True)
        detected = detect_image(raw)
        assert detected.has_alpha
        normalized = normalize_image(raw, detected)
        assert normalized.media_type == "image/webp"

    def test_request_dimensions_fit_640000(self) -> None:
        width, height = request_image_dimensions(2048, 1024, 640_000)
        assert width * height <= 640_000
        assert width >= 1 and height >= 1
        assert abs(width / height - 2.0) < 0.02


class TestOffloadQuantum:
    def test_129x1mib_drops_65(self) -> None:
        lengths = [1024 * 1024] * 129
        policy = RequestImageOffloadPolicy(
            max_images=600,
            max_bytes=128 * 1024 * 1024,
            count_quantum=20,
            byte_quantum=64 * 1024 * 1024,
        )
        assert offloaded_image_prefix_count(lengths, policy) == 65

    def test_prefix_stable_until_192mib(self) -> None:
        policy = RequestImageOffloadPolicy(
            max_images=600,
            max_bytes=128 * 1024 * 1024,
            count_quantum=20,
            byte_quantum=64 * 1024 * 1024,
        )
        assert offloaded_image_prefix_count([1024 * 1024] * 192, policy) == 65
        assert offloaded_image_prefix_count([1024 * 1024] * 193, policy) == 129


class TestHistoryIsAppendOnly:
    def test_text_only_session_does_not_rewrite_history(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        ref = admit_image_bytes(_png_bytes(), name="shot.png", store=store)
        history = [_ref_message(ref)]
        snapshot = [dict(history[0]), dict(history[0]["content"][1])]
        projected = assemble_model_request(
            history,
            vision_capable=False,
            store=store,
        )
        assert history[0]["content"][1]["type"] == "image"
        assert history[0]["content"][1]["attachment"] == snapshot[1]["attachment"]
        assert projected[0]["content"][0]["type"] == "text"
        assert "text only" in projected[0]["content"][1]["text"]
        assert projected is not history

    def test_vision_projection_leaves_durable_refs(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        ref = admit_image_bytes(_png_bytes(), store=store)
        history = [_ref_message(ref)]
        projected = assemble_model_request(
            history,
            vision_capable=True,
            store=store,
            policy=ImageRequestPolicy(max_pixels=640_000, max_bytes=1_048_576),
        )
        assert history[0]["content"][1]["type"] == "image"
        types = [p["type"] for p in projected[0]["content"]]
        assert "image_url" in types
        assert "image" not in types


class TestMigrate:
    def test_old_base64_becomes_ref(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
            "_image_id": 1,
        }]
        migrated = migrate_messages(messages, store=store)
        block = migrated[0]["content"][1]
        assert block["type"] == "image"
        assert block["attachment"]["attachmentId"].startswith("sha256:")
        assert "_image_id" not in migrated[0]

    def test_degraded_placeholder_stays_text(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        messages = [{
            "role": "user",
            "content": "[图片 #3 已在之前的对话中发送]",
        }]
        migrated = migrate_messages(messages, store=store)
        assert migrated[0]["content"] == "[图片 #3 已在之前的对话中发送]"


class _FakeFiles:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.listed: list[dict] = []
        self._n = 0
        self.fail_once: Exception | None = None
        self.fail_always: Exception | None = None

    async def create(self, file, purpose):
        if self.fail_always:
            raise self.fail_always
        if self.fail_once is not None:
            err, self.fail_once = self.fail_once, None
            raise err
        self._n += 1
        self.created.append(purpose)
        return {"id": f"file-api-{self._n}", "expires_at": int(time.time()) + 86400}

    async def list(self):
        return {"data": self.listed}

    async def delete(self, file_id):
        self.deleted.append(file_id)


def _projected_inline(variant: str = "sha256:" + "ab" * 32) -> list[dict]:
    b64 = MIN_PNG_B64
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": "see"},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
                "_variant_id": variant,
            },
        ],
    }]


class TestFilesApi:
    def test_auto_only_enables_deepseek(self) -> None:
        cfg = SimpleNamespace(image_files_api="auto")
        assert files_api_enabled(cfg, "https://api.deepseek.com")
        assert not files_api_enabled(cfg, "https://proxy.example.com/v1")

    @pytest.mark.asyncio
    async def test_index_hit_skips_second_upload(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        client = SimpleNamespace(files=_FakeFiles())
        messages = _projected_inline()
        first = await apply_files_transport(
            messages, client, base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        second = await apply_files_transport(
            messages, client, base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        assert len(client.files.created) == 1
        assert first[0]["content"][1]["type"] == "file"
        assert first[0]["content"][1]["file_id"] == second[0]["content"][1]["file_id"]

    @pytest.mark.asyncio
    async def test_stale_index_reuploads(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        client = SimpleNamespace(files=_FakeFiles())
        messages = _projected_inline()
        first = await apply_files_transport(
            messages, client, base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        file_id = first[0]["content"][1]["file_id"]
        invalidate_file_ids(
            [file_id], base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        second = await apply_files_transport(
            messages, client, base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        assert len(client.files.created) == 2
        assert second[0]["content"][1]["file_id"] != file_id

    @pytest.mark.asyncio
    async def test_resolution_failure_keeps_inline(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        files = _FakeFiles()
        files.fail_always = RuntimeError("upload down")
        client = SimpleNamespace(files=files)
        messages = _projected_inline()
        out = await apply_files_transport(
            messages, client, base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        assert out is messages
        assert out[0]["content"][1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_quota_deletes_oldest_em_and_retries(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        files = _FakeFiles()
        files.fail_once = RuntimeError("user storage quota exceeded")
        files.listed = [
            {"id": "file-old", "filename": "em-old.png", "created_at": 1},
            {"id": "file-keep", "filename": "other.png", "created_at": 2},
        ]
        client = SimpleNamespace(files=files)
        out = await apply_files_transport(
            _projected_inline(), client,
            base_url="https://api.deepseek.com", api_key="sk-test", store=store,
        )
        assert files.deleted == ["file-old"]
        assert len(files.created) == 1
        assert out[0]["content"][1]["type"] == "file"

    def test_error_classifiers(self) -> None:
        assert is_files_quota_error("file count quota exceeded")
        assert not is_files_quota_error("timeout")
        assert is_stale_file_error("file_id file-api-1 expired")
        assert not is_stale_file_error("invalid image_url")


class TestMemoryAdmit:
    def test_add_user_image_writes_ref_not_base64(self) -> None:
        config = ExcelManusConfig(
            api_key="test", base_url="https://example.com/v1", model="test",
        )
        mem = ConversationMemory(config)
        raw = _png_bytes()
        mem.add_user_message([
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}",
            }},
        ])
        content = mem.messages[-1]["content"]
        assert content[1]["type"] == "image"
        assert "base64" not in str(content)
        digest = content[1]["attachment"]["attachmentId"].removeprefix("sha256:")
        assert len(digest) == 64


class TestStoreAndPersist:
    def test_get_ref_roundtrip(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        ref = admit_image_bytes(_png_bytes(), name="shot.png", store=store)
        loaded = store.get_ref(ref.attachment_id)
        assert loaded is not None
        assert loaded.attachment_id == ref.attachment_id
        assert loaded.name == "shot.png"
        assert store.get_ref("sha256:deadbeef") is None

    def test_chat_history_serializes_refs_only(self) -> None:
        from excelmanus.chat_history import ChatHistoryStore

        raw = _png_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                    "_variant_id": "sha256:ignore",
                },
            ],
            "_image_id": 9,
        }
        # persist uses process store via migrate_content
        from excelmanus.attachments.store import reset_attachment_store
        reset_attachment_store()
        dumped = ChatHistoryStore._serialize_content(msg)
        parsed = json.loads(dumped)
        assert "_image_id" not in parsed
        assert parsed["content"][1]["type"] == "image"
        assert parsed["content"][1]["attachment"]["attachmentId"].startswith("sha256:")
        assert "_variant_id" not in parsed["content"][1]


class TestRequestTokensAndProviders:
    def test_count_message_uses_request_dimensions(self) -> None:
        from excelmanus.memory import TokenCounter

        config = SimpleNamespace(image_pixel_budget=640_000, image_max_bytes=1_048_576, base_url="")
        durable = {
            "role": "user",
            "content": [{
                "type": "image",
                "attachment": {
                    "attachmentId": "sha256:aa",
                    "mediaType": "image/png",
                    "bytes": 100,
                    "width": 2048,
                    "height": 1024,
                },
            }],
        }
        raw = TokenCounter.count_message(durable)
        projected = TokenCounter.count_message(durable, config=config)
        assert projected < raw

    def test_responses_maps_file_block(self) -> None:
        from excelmanus.providers.openai_responses import _chat_messages_to_responses_input

        _, items = _chat_messages_to_responses_input([
            {"role": "user", "content": [
                {"type": "text", "text": "see"},
                {"type": "file", "file_id": "file-api-1"},
            ]},
        ])
        user = items[0]
        assert any(p.get("type") == "input_file" and p.get("file_id") == "file-api-1" for p in user["content"])


class TestOffloadPins:
    def test_new_image_does_not_rewrite_earlier_inline(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        ref1 = admit_image_bytes(_png_bytes(8, 8), name="a.png", store=store)
        ref2 = admit_image_bytes(_png_bytes(8, 8, color=(0, 255, 0)), name="b.png", store=store)
        policy = RequestImageOffloadPolicy(
            max_images=10,
            max_bytes=10**9,
            count_quantum=1,
            byte_quantum=1,
        )
        report1: dict = {}
        first = assemble_model_request(
            [_ref_message(ref1, "one")],
            vision_capable=True,
            store=store,
            offload=policy,
            report=report1,
        )
        report2: dict = {}
        second = assemble_model_request(
            [
                _ref_message(ref1, "one"),
                {"role": "assistant", "content": "ok"},
                _ref_message(ref2, "two"),
            ],
            vision_capable=True,
            store=store,
            offload=policy,
            pin_seq=report1.get("pin_seq"),
            report=report2,
        )
        assert report2.get("rewrote_pinned_inline") is False
        assert first[0]["content"] == second[0]["content"]
        types = [p["type"] for p in second[-1]["content"]]
        assert "image_url" in types

    def test_quota_unmet_rewrites_pinned_inline(self, tmp_path) -> None:
        store = AttachmentStore(tmp_path / "attachments")
        ref1 = admit_image_bytes(_png_bytes(8, 8), name="a.png", store=store)
        ref2 = admit_image_bytes(_png_bytes(8, 8, color=(0, 0, 255)), name="b.png", store=store)
        loose = RequestImageOffloadPolicy(
            max_images=10,
            max_bytes=10**9,
            count_quantum=1,
            byte_quantum=1,
        )
        tight = RequestImageOffloadPolicy(
            max_images=0,
            max_bytes=10**9,
            count_quantum=1,
            byte_quantum=1,
        )
        report1: dict = {}
        assemble_model_request(
            [_ref_message(ref1), _ref_message(ref2)],
            vision_capable=True,
            store=store,
            offload=loose,
            report=report1,
        )
        assert report1.get("pin_seq") == ("inline", "inline")
        report2: dict = {}
        projected = assemble_model_request(
            [_ref_message(ref1), _ref_message(ref2)],
            vision_capable=True,
            store=store,
            offload=tight,
            pin_seq=report1.get("pin_seq"),
            report=report2,
        )
        assert report2.get("rewrote_pinned_inline") is True
        assert all(
            not any(isinstance(p, dict) and p.get("type") == "image_url" for p in (m.get("content") or []))
            for m in projected
            if isinstance(m.get("content"), list)
        )
