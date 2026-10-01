from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .autonomous_reels import canonical_json
from .visual_critic import VisualCriticProviderError


GEMINI_PROVIDER_NAME = "google_gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class GeminiNativeVideoError(VisualCriticProviderError):
    pass


class GeminiNativeVideoTransportError(GeminiNativeVideoError):
    pass


class GeminiNativeVideoMalformedOutput(GeminiNativeVideoError):
    pass


class GeminiNativeVideoDisabled(GeminiNativeVideoError):
    pass


class GeminiTransport(Protocol):
    is_fake: bool

    def upload_video(
        self,
        path: Path,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        ...

    def get_file(
        self,
        name: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        ...

    def interact(
        self,
        *,
        model: str,
        input_parts: Sequence[Mapping[str, Any]],
        api_key: str | None,
        timeout_seconds: float,
    ) -> str:
        ...

    def delete_file(
        self,
        name: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> None:
        ...


ProbeRunner = Callable[[Path, float], float]


@dataclass(frozen=True)
class GeminiNativeVideoConfig:
    model: str = DEFAULT_GEMINI_MODEL
    processing_mode: str = "static"
    static_fps: float | None = 2.0
    max_upload_bytes: int = 100 * 1024 * 1024
    max_retries: int = 2
    request_timeout_seconds: float = 75.0
    upload_timeout_seconds: float = 75.0
    poll_interval_seconds: float = 2.0
    max_poll_attempts: int = 45
    ffprobe_timeout_seconds: float = 8.0
    duration_tolerance_seconds: float = 0.35
    pairwise_min_confidence: float = 0.65

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model:
            raise GeminiNativeVideoError("Gemini model must be non-empty")
        if self.processing_mode not in {"static", "agentic"}:
            raise GeminiNativeVideoError(
                "processing_mode must be static or agentic"
            )
        if self.processing_mode == "agentic" and self.static_fps is not None:
            raise GeminiNativeVideoError(
                "static_fps must be null in agentic mode"
            )
        if self.static_fps is not None:
            if (
                isinstance(self.static_fps, bool)
                or not isinstance(self.static_fps, (int, float))
                or not 0.1 <= float(self.static_fps) <= 10.0
            ):
                raise GeminiNativeVideoError(
                    "static_fps must be in [0.1,10]"
                )
        for value, field in (
            (self.max_upload_bytes, "max_upload_bytes"),
            (self.max_retries, "max_retries"),
            (self.max_poll_attempts, "max_poll_attempts"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise GeminiNativeVideoError(
                    f"{field} must be a non-negative integer"
                )
        if self.max_upload_bytes < 1 or self.max_poll_attempts < 1:
            raise GeminiNativeVideoError(
                "upload bound and poll attempts must be positive"
            )
        for value, field in (
            (self.request_timeout_seconds, "request_timeout_seconds"),
            (self.upload_timeout_seconds, "upload_timeout_seconds"),
            (self.poll_interval_seconds, "poll_interval_seconds"),
            (self.ffprobe_timeout_seconds, "ffprobe_timeout_seconds"),
            (
                self.duration_tolerance_seconds,
                "duration_tolerance_seconds",
            ),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or float(value) <= 0
            ):
                raise GeminiNativeVideoError(
                    f"{field} must be positive"
                )
        if (
            isinstance(self.pairwise_min_confidence, bool)
            or not isinstance(
                self.pairwise_min_confidence,
                (int, float),
            )
            or not 0 <= float(self.pairwise_min_confidence) <= 1
        ):
            raise GeminiNativeVideoError(
                "pairwise_min_confidence must be in [0,1]"
            )

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "GeminiNativeVideoConfig":
        source = os.environ if env is None else env
        model = source.get("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip()
        mode = source.get("GEMINI_VIDEO_MODE", "static").strip().lower()
        raw_fps = source.get("GEMINI_VIDEO_FPS", "2.0").strip()
        fps = None if mode == "agentic" else float(raw_fps)
        return cls(
            model=model,
            processing_mode=mode,
            static_fps=fps,
        )


def ffprobe_duration_seconds(
    path: Path,
    timeout_seconds: float,
) -> float:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise GeminiNativeVideoError(
            "ffprobe duration check failed"
        ) from exc
    try:
        value = float(completed.stdout.strip())
    except ValueError as exc:
        raise GeminiNativeVideoError(
            "ffprobe returned malformed duration"
        ) from exc
    if not (0.0 < value < 24 * 60 * 60):
        raise GeminiNativeVideoError(
            "ffprobe duration is outside accepted bounds"
        )
    return value


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


class UrllibGeminiTransport:
    is_fake = False
    _api_root = "https://generativelanguage.googleapis.com"
    _upload_root = (
        "https://generativelanguage.googleapis.com/upload/v1beta/files"
    )

    @staticmethod
    def _require_key(api_key: str | None) -> str:
        if not isinstance(api_key, str) or not api_key:
            raise GeminiNativeVideoDisabled(
                "GEMINI_API_KEY is required for live Gemini transport"
            )
        return api_key

    @staticmethod
    def _decode_json_response(response: Any) -> dict[str, Any]:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini HTTP response was not valid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise GeminiNativeVideoTransportError(
                "Gemini HTTP response was not a JSON object"
            )
        return payload

    def upload_video(
        self,
        path: Path,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        key = self._require_key(api_key)
        size = path.stat().st_size
        metadata = canonical_json({
            "file": {"display_name": "native-video.mp4"}
        }).encode("utf-8")
        start_request = urllib.request.Request(
            self._upload_root,
            data=metadata,
            method="POST",
            headers={
                "x-goog-api-key": key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": "video/mp4",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                start_request,
                timeout=timeout_seconds,
            ) as response:
                upload_url = response.headers.get(
                    "X-Goog-Upload-URL"
                )
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini resumable-upload start failed"
            ) from exc
        if not upload_url:
            raise GeminiNativeVideoTransportError(
                "Gemini resumable-upload URL missing"
            )
        data = path.read_bytes()
        upload_request = urllib.request.Request(
            upload_url,
            data=data,
            method="POST",
            headers={
                "Content-Length": str(size),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
                "Content-Type": "video/mp4",
            },
        )
        try:
            with urllib.request.urlopen(
                upload_request,
                timeout=timeout_seconds,
            ) as response:
                payload = self._decode_json_response(response)
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini video upload failed"
            ) from exc
        file_payload = payload.get("file")
        if not isinstance(file_payload, Mapping):
            raise GeminiNativeVideoTransportError(
                "Gemini upload response missing file object"
            )
        return dict(file_payload)

    def get_file(
        self,
        name: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        key = self._require_key(api_key)
        normalized = name.lstrip("/")
        request = urllib.request.Request(
            f"{self._api_root}/v1beta/{normalized}",
            method="GET",
            headers={"x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                return self._decode_json_response(response)
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini file status request failed"
            ) from exc

    @staticmethod
    def _extract_output_text(payload: Mapping[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text
        outputs = payload.get("outputs")
        if isinstance(outputs, list):
            texts = [
                item.get("text")
                for item in outputs
                if isinstance(item, Mapping)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ]
            if texts:
                return "\n".join(texts)
        steps = payload.get("steps")
        if isinstance(steps, list):
            texts: list[str] = []
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                content = step.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if (
                        isinstance(part, Mapping)
                        and isinstance(part.get("text"), str)
                    ):
                        texts.append(part["text"])
            if texts:
                return texts[-1]
        raise GeminiNativeVideoTransportError(
            "Gemini interaction response contained no text output"
        )

    def interact(
        self,
        *,
        model: str,
        input_parts: Sequence[Mapping[str, Any]],
        api_key: str | None,
        timeout_seconds: float,
    ) -> str:
        key = self._require_key(api_key)
        body = canonical_json({
            "model": model,
            "input": list(input_parts),
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self._api_root}/v1beta/interactions",
            data=body,
            method="POST",
            headers={
                "x-goog-api-key": key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                payload = self._decode_json_response(response)
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini interaction request failed"
            ) from exc
        return self._extract_output_text(payload)

    def delete_file(
        self,
        name: str,
        *,
        api_key: str | None,
        timeout_seconds: float,
    ) -> None:
        key = self._require_key(api_key)
        normalized = name.lstrip("/")
        request = urllib.request.Request(
            f"{self._api_root}/v1beta/{normalized}",
            method="DELETE",
            headers={"x-goog-api-key": key},
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ):
                return
        except (OSError, urllib.error.URLError) as exc:
            raise GeminiNativeVideoTransportError(
                "Gemini uploaded-file cleanup failed"
            ) from exc


@dataclass
class GeminiRuntime:
    config: GeminiNativeVideoConfig
    transport: GeminiTransport
    probe_runner: ProbeRunner
    api_key: str | None
    sleep_fn: Callable[[float], None]

    def retry(self, fn: Callable[[], Any]) -> Any:
        last: BaseException | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return fn()
            except GeminiNativeVideoTransportError as exc:
                last = exc
                if attempt >= self.config.max_retries:
                    raise
        if last is not None:
            raise last
        raise GeminiNativeVideoTransportError(
            "bounded retry loop produced no result"
        )

    def validate_local_video(
        self,
        path: Path,
        *,
        expected_sha256: str,
        expected_duration_seconds: float,
    ) -> dict[str, Any]:
        if not path.is_file():
            raise GeminiNativeVideoError(
                "native video path does not exist"
            )
        if path.suffix.lower() != ".mp4":
            raise GeminiNativeVideoError(
                "Gemini native-video critic requires an MP4 file"
            )
        size = path.stat().st_size
        if size < 1 or size > self.config.max_upload_bytes:
            raise GeminiNativeVideoError(
                "native video exceeds bounded upload size"
            )
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha256:
            raise GeminiNativeVideoError(
                "native video bytes do not match render artifact digest"
            )
        duration = self.probe_runner(
            path,
            self.config.ffprobe_timeout_seconds,
        )
        if (
            abs(duration - expected_duration_seconds)
            > self.config.duration_tolerance_seconds
        ):
            raise GeminiNativeVideoError(
                "ffprobe duration differs from critic render duration"
            )
        return {
            "video_sha256": actual_sha,
            "video_size_bytes": size,
            "ffprobe_duration_seconds": round(duration, 6),
        }

    def upload_active(self, path: Path) -> dict[str, Any]:
        uploaded = dict(self.retry(
            lambda: self.transport.upload_video(
                path,
                api_key=self.api_key,
                timeout_seconds=
                    self.config.upload_timeout_seconds,
            )
        ))
        current = uploaded
        for attempt in range(self.config.max_poll_attempts):
            state = current.get("state")
            if isinstance(state, Mapping):
                state = state.get("name")
            if isinstance(state, str):
                state = state.upper()
            if state == "ACTIVE":
                return current
            if state == "FAILED":
                raise GeminiNativeVideoTransportError(
                    "Gemini video processing failed"
                )
            name = current.get("name")
            if not isinstance(name, str) or not name:
                raise GeminiNativeVideoTransportError(
                    "Gemini uploaded file name missing"
                )
            if attempt:
                self.sleep_fn(
                    self.config.poll_interval_seconds
                )
            current = dict(self.retry(
                lambda: self.transport.get_file(
                    name,
                    api_key=self.api_key,
                    timeout_seconds=
                        self.config.request_timeout_seconds,
                )
            ))
        raise GeminiNativeVideoTransportError(
            "Gemini file processing poll limit exceeded"
        )

    def interact(
        self,
        input_parts: Sequence[Mapping[str, Any]],
    ) -> str:
        return self.retry(
            lambda: self.transport.interact(
                model=self.config.model,
                input_parts=input_parts,
                api_key=self.api_key,
                timeout_seconds=
                    self.config.request_timeout_seconds,
            )
        )

    def cleanup(self, name: str) -> None:
        self.retry(
            lambda: self.transport.delete_file(
                name,
                api_key=self.api_key,
                timeout_seconds=
                    self.config.request_timeout_seconds,
            )
        )


def runtime_from_environment(
    *,
    env: Mapping[str, str] | None = None,
    transport: GeminiTransport | None = None,
    probe_runner: ProbeRunner = ffprobe_duration_seconds,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> GeminiRuntime | None:
    source = os.environ if env is None else env
    api_key = source.get("GEMINI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        return None
    return GeminiRuntime(
        config=GeminiNativeVideoConfig.from_environment(source),
        transport=(
            UrllibGeminiTransport()
            if transport is None
            else transport
        ),
        probe_runner=probe_runner,
        api_key=api_key,
        sleep_fn=sleep_fn,
    )


def runtime_for_fake(
    *,
    transport: GeminiTransport,
    probe_runner: ProbeRunner,
    config: GeminiNativeVideoConfig | None = None,
) -> GeminiRuntime:
    if not getattr(transport, "is_fake", False):
        raise GeminiNativeVideoError(
            "fake runtime requires transport.is_fake=true"
        )
    return GeminiRuntime(
        config=config or GeminiNativeVideoConfig(
            model="gemini-fixture-model",
            processing_mode="static",
            static_fps=2.0,
            max_retries=1,
            request_timeout_seconds=2.0,
            upload_timeout_seconds=2.0,
            poll_interval_seconds=0.001,
            max_poll_attempts=3,
            ffprobe_timeout_seconds=1.0,
        ),
        transport=transport,
        probe_runner=probe_runner,
        api_key=None,
        sleep_fn=lambda _seconds: None,
    )


def video_part(
    *,
    uri: str,
    config: GeminiNativeVideoConfig,
) -> dict[str, Any]:
    part: dict[str, Any] = {
        "type": "video",
        "uri": uri,
        "mime_type": "video/mp4",
    }
    if config.processing_mode == "static":
        processing: dict[str, Any] = {"type": "static"}
        if config.static_fps is not None:
            processing["fps"] = float(config.static_fps)
        part["processing"] = processing
    else:
        part["processing"] = {"type": "agentic"}
    return part
