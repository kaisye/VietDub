from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import re
import subprocess
import threading
import time
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import numpy as np
import requests
import soundfile as sf
import torch
from fastapi import BackgroundTasks, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


MODEL_ID = os.getenv("OMNIVOICE_MODEL_ID", "k2-fsa/OmniVoice")
DEVICE_MAP = os.getenv("OMNIVOICE_DEVICE_MAP", "cuda:0" if torch.cuda.is_available() else "cpu")
OUTPUT_DIR = Path(os.getenv("OMNIVOICE_OUTPUT_DIR", "/content/omnivoice_outputs")).resolve()
API_KEY = os.getenv("OMNIVOICE_API_KEY", "").strip()
SAMPLE_RATE = 24000
DEFAULT_HOST = os.getenv("OMNIVOICE_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("OMNIVOICE_PORT", "8008"))
MAX_TEXT_CHARS = int(os.getenv("OMNIVOICE_MAX_TEXT_CHARS", "20000"))
MAX_REF_TEXT_CHARS = int(os.getenv("OMNIVOICE_MAX_REF_TEXT_CHARS", "12000"))
MAX_INSTRUCTION_CHARS = int(os.getenv("OMNIVOICE_MAX_INSTRUCTION_CHARS", "1000"))


logging.basicConfig(level=os.getenv("OMNIVOICE_LOG_LEVEL", "INFO"))
logger = logging.getLogger("omnivoice-service")
try:
    SERVICE_DIR = Path(__file__).resolve().parent
except NameError:
    SERVICE_DIR = Path.cwd()
DEFAULT_REF_AUDIO_PATH = Path(os.getenv("OMNIVOICE_DEFAULT_REF_AUDIO_PATH", str(SERVICE_DIR / "Voice_Ref.WAV"))).expanduser()
DEFAULT_REF_TEXT_PATH = Path(os.getenv("OMNIVOICE_DEFAULT_REF_TEXT_PATH", str(SERVICE_DIR / "voice_scripts.txt"))).expanduser()
DEFAULT_INSTRUCTION_PATH = Path(os.getenv("OMNIVOICE_DEFAULT_INSTRUCTION_PATH", str(SERVICE_DIR / "Instruction.txt"))).expanduser()


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    mode: Literal["auto", "design", "clone"] = "auto"
    speed: float | None = Field(default=None, gt=0.5, le=2.0)
    instruct: str | None = Field(default=None, max_length=MAX_INSTRUCTION_CHARS)
    ref_text: str | None = Field(default=None, max_length=MAX_REF_TEXT_CHARS)
    ref_audio_url: str | None = None
    ref_audio_base64: str | None = None
    output_format: Literal["wav"] = "wav"


class SynthesizeResponse(BaseModel):
    audio_url: str
    filename: str
    sample_rate: int = SAMPLE_RATE
    duration_seconds: float
    provider: str = "omnivoice"


class SynthesizeSrtCue(BaseModel):
    id: str
    index: int
    start: float
    end: float
    duration_seconds: float
    target_duration_seconds: float
    audio_url: str
    filename: str
    text: str


class SynthesizeSrtCuesResponse(BaseModel):
    cues: list[SynthesizeSrtCue]
    sample_rate: int = SAMPLE_RATE
    provider: str = "omnivoice"
    timeline_mode: Literal["cue", "chunk"]
    duration_seconds: float


class SynthesizeSrtRequest(BaseModel):
    srt_text: str | None = Field(default=None, max_length=500_000)
    srt_base64: str | None = None
    mode: Literal["auto", "design", "clone"] = "clone"
    timeline_mode: Literal["cue", "chunk"] = "cue"
    speed: float | None = Field(default=None, gt=0.5, le=2.0)
    chunk_min_seconds: float = Field(default=15, gt=0, le=300)
    chunk_max_seconds: float = Field(default=60, gt=0, le=600)
    chunk_max_chars: int = Field(default=6500, gt=100, le=MAX_TEXT_CHARS)
    instruct: str | None = Field(default=None, max_length=MAX_INSTRUCTION_CHARS)
    ref_text: str | None = Field(default=None, max_length=MAX_REF_TEXT_CHARS)
    ref_audio_url: str | None = None
    ref_audio_base64: str | None = None
    output_format: Literal["wav"] = "wav"


_model = None
_model_error: str | None = None
_model_loading = False
_model_lock = threading.Lock()
_srt_jobs: dict[str, dict[str, object]] = {}
_srt_jobs_lock = threading.Lock()
_srt_cue_jobs: dict[str, dict[str, object]] = {}
_srt_cue_jobs_lock = threading.Lock()


def load_model() -> None:
    global _model, _model_error, _model_loading
    with _model_lock:
        if _model is not None or _model_loading:
            return
        _model_loading = True
        _model_error = None

    try:
        from omnivoice import OmniVoice

        dtype = torch.float16 if DEVICE_MAP.startswith("cuda") else torch.float32
        load_asr = os.getenv("OMNIVOICE_LOAD_ASR", "0") == "1"
        logger.info("Loading OmniVoice model %s on %s with dtype=%s load_asr=%s", MODEL_ID, DEVICE_MAP, dtype, load_asr)
        model = OmniVoice.from_pretrained(
            MODEL_ID,
            device_map=DEVICE_MAP,
            dtype=dtype,
            load_asr=load_asr,
        )
        with _model_lock:
            _model = model
        logger.info("OmniVoice model loaded.")
    except Exception as exc:
        with _model_lock:
            _model_error = str(exc)
        logger.exception("Failed to load OmniVoice model.")
        raise
    finally:
        with _model_lock:
            _model_loading = False


def start_model_loading() -> threading.Thread:
    thread = threading.Thread(target=load_model, daemon=True, name="omnivoice-model-loader")
    thread.start()
    return thread


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.getenv("OMNIVOICE_LOAD_ON_STARTUP", "1") == "1":
        start_model_loading()
    yield


app = FastAPI(title="OmniVoice TTS Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(OUTPUT_DIR)), name="audio")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "OmniVoice TTS Service",
        "health": "/health",
        "docs": "/docs",
        "synthesize": "/synthesize",
        "synthesize_file": "/synthesize/file",
        "synthesize_srt": "/synthesize/srt",
        "synthesize_srt_cues": "/synthesize/srt/cues",
        "synthesize_srt_cue_jobs": "/synthesize/srt/cues/jobs",
        "synthesize_srt_file": "/synthesize/srt/file",
        "synthesize_srt_jobs": "/synthesize/srt/jobs",
        "synthesize_srt_upload": "/synthesize/srt/upload",
    }


@app.get("/health")
def health() -> dict[str, str | bool | None]:
    if _model_error:
        status = "error"
    elif _model is not None:
        status = "ready"
    elif _model_loading:
        status = "loading"
    else:
        status = "not_loaded"
    return {
        "status": status,
        "model_loaded": _model is not None,
        "model_loading": _model_loading,
        "model_error": _model_error,
        "model": MODEL_ID,
        "device": DEVICE_MAP,
    }


@app.get("/ready")
def ready() -> dict[str, str | bool]:
    if _model_error:
        raise HTTPException(status_code=503, detail=f"OmniVoice model failed to load: {_model_error}")
    if _model is None:
        raise HTTPException(status_code=503, detail="OmniVoice model is still loading.")
    return {"status": "ready", "model_loaded": True}


@app.post("/synthesize", response_model=SynthesizeResponse)
def synthesize(payload: SynthesizeRequest, request: Request, authorization: str | None = Header(default=None)) -> SynthesizeResponse:
    _verify_api_key(authorization)
    output_path, duration = _synthesize_to_path(payload)
    filename = output_path.name
    return SynthesizeResponse(
        audio_url=str(request.base_url).rstrip("/") + f"/audio/{filename}",
        filename=filename,
        duration_seconds=duration,
    )


@app.post("/synthesize/file")
def synthesize_file(payload: SynthesizeRequest, authorization: str | None = Header(default=None)) -> FileResponse:
    """Convenience endpoint for clients that prefer a direct WAV response."""
    _verify_api_key(authorization)
    output_path, _duration = _synthesize_to_path(payload)
    return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)


@app.post("/synthesize/srt", response_model=SynthesizeResponse)
def synthesize_srt(payload: SynthesizeSrtRequest, request: Request, authorization: str | None = Header(default=None)) -> SynthesizeResponse:
    """Generate one WAV file from SRT text, placing each OmniVoice chunk on the SRT timeline."""
    _verify_api_key(authorization)
    output_path, duration = _synthesize_srt_to_path(payload)
    filename = output_path.name
    return SynthesizeResponse(
        audio_url=str(request.base_url).rstrip("/") + f"/audio/{filename}",
        filename=filename,
        duration_seconds=duration,
    )


@app.post("/synthesize/srt/cues", response_model=SynthesizeSrtCuesResponse)
def synthesize_srt_cues(
    payload: SynthesizeSrtRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> SynthesizeSrtCuesResponse:
    """Generate one WAV file per SRT cue/chunk and return a cue list for manual timeline editing."""
    _verify_api_key(authorization)
    cue_outputs, duration = _synthesize_srt_cues_to_paths(payload)
    base_url = str(request.base_url).rstrip("/")
    return _srt_cues_response(cue_outputs, duration, payload.timeline_mode, base_url)


@app.post("/synthesize/srt/cues/jobs")
def synthesize_srt_cues_job(
    payload: SynthesizeSrtRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Start SRT cue-list synthesis in the background for tunnel-safe polling."""
    _verify_api_key(authorization)
    job_id = str(uuid.uuid4())
    base_url = str(request.base_url).rstrip("/")
    with _srt_cue_jobs_lock:
        _srt_cue_jobs[job_id] = {
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "cues": [],
            "duration_seconds": None,
            "timeline_mode": payload.timeline_mode,
            "error": None,
            "base_url": base_url,
        }
    background_tasks.add_task(_run_srt_cues_job, job_id, payload)
    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"{base_url}/synthesize/srt/cues/jobs/{job_id}",
        "result_url": f"{base_url}/synthesize/srt/cues/jobs/{job_id}/result",
    }


@app.get("/synthesize/srt/cues/jobs/{job_id}")
def synthesize_srt_cues_job_status(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _verify_api_key(authorization)
    job = _get_srt_cue_job(job_id)
    response: dict[str, object] = {
        "job_id": job_id,
        "status": job["status"],
        "duration_seconds": job.get("duration_seconds"),
        "cue_count": len(job.get("cues") or []),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "result_url": f"{job.get('base_url')}/synthesize/srt/cues/jobs/{job_id}/result",
    }
    if job["status"] == "completed":
        response["cues"] = _srt_cues_response(
            list(job.get("cues") or []),
            float(job.get("duration_seconds") or 0.0),
            str(job.get("timeline_mode") or "cue"),
            str(job.get("base_url") or ""),
        ).model_dump()
    return response


@app.get("/synthesize/srt/cues/jobs/{job_id}/result", response_model=SynthesizeSrtCuesResponse)
def synthesize_srt_cues_job_result(job_id: str, authorization: str | None = Header(default=None)) -> SynthesizeSrtCuesResponse:
    _verify_api_key(authorization)
    job = _get_srt_cue_job(job_id)
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error") or "SRT cue synthesis job failed.")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"SRT cue synthesis job is {job['status']}.")
    return _srt_cues_response(
        list(job.get("cues") or []),
        float(job.get("duration_seconds") or 0.0),
        str(job.get("timeline_mode") or "cue"),
        str(job.get("base_url") or ""),
    )


@app.post("/synthesize/srt/file")
def synthesize_srt_file(payload: SynthesizeSrtRequest, authorization: str | None = Header(default=None)) -> FileResponse:
    """Generate one timeline-aligned WAV file from SRT and return it directly."""
    _verify_api_key(authorization)
    output_path, _duration = _synthesize_srt_to_path(payload)
    return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)


@app.post("/synthesize/srt/jobs")
def synthesize_srt_job(
    payload: SynthesizeSrtRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Start a long SRT synthesis job and return immediately for tunnel-safe polling."""
    _verify_api_key(authorization)
    job_id = str(uuid.uuid4())
    with _srt_jobs_lock:
        _srt_jobs[job_id] = {
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "filename": None,
            "duration_seconds": None,
            "error": None,
        }
    background_tasks.add_task(_run_srt_job, job_id, payload)
    base_url = str(request.base_url).rstrip("/")
    return {
        "job_id": job_id,
        "status": "queued",
        "status_url": f"{base_url}/synthesize/srt/jobs/{job_id}",
        "file_url": f"{base_url}/synthesize/srt/jobs/{job_id}/file",
    }


@app.get("/synthesize/srt/jobs/{job_id}")
def synthesize_srt_job_status(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    _verify_api_key(authorization)
    job = _get_srt_job(job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "filename": job.get("filename"),
        "duration_seconds": job.get("duration_seconds"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


@app.get("/synthesize/srt/jobs/{job_id}/file")
def synthesize_srt_job_file(job_id: str, authorization: str | None = Header(default=None)) -> FileResponse:
    _verify_api_key(authorization)
    job = _get_srt_job(job_id)
    if job["status"] == "failed":
        raise HTTPException(status_code=500, detail=job.get("error") or "SRT synthesis job failed.")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"SRT synthesis job is {job['status']}.")

    filename = str(job.get("filename") or "")
    output_path = OUTPUT_DIR / filename
    if not filename or not output_path.exists():
        raise HTTPException(status_code=404, detail="Generated audio file is missing.")
    return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)


@app.post("/synthesize/srt/upload")
async def synthesize_srt_upload(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    mode: Literal["auto", "design", "clone"] = Form("clone"),
    timeline_mode: Literal["cue", "chunk"] = Form("cue"),
    speed: float | None = Form(None),
    chunk_min_seconds: float = Form(15),
    chunk_max_seconds: float = Form(60),
    chunk_max_chars: int = Form(6500),
    instruct: str | None = Form(None),
    ref_text: str | None = Form(None),
    ref_audio_url: str | None = Form(None),
) -> FileResponse:
    """Generate one timeline-aligned WAV file from an uploaded .srt/.txt file."""
    _verify_api_key(authorization)
    filename = file.filename or "uploaded.srt"
    if not filename.lower().endswith((".srt", ".txt")):
        raise HTTPException(status_code=400, detail="Upload must be a .srt or .txt file.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Uploaded SRT file is empty.")

    payload = SynthesizeSrtRequest(
        srt_text=raw.decode("utf-8", errors="ignore"),
        mode=mode,
        timeline_mode=timeline_mode,
        speed=speed,
        chunk_min_seconds=chunk_min_seconds,
        chunk_max_seconds=chunk_max_seconds,
        chunk_max_chars=chunk_max_chars,
        instruct=instruct,
        ref_text=ref_text,
        ref_audio_url=ref_audio_url,
    )
    output_path, _duration = _synthesize_srt_to_path(payload)
    return FileResponse(output_path, media_type="audio/wav", filename=output_path.name)


def _synthesize_to_path(payload: SynthesizeRequest) -> tuple[Path, float]:
    if _model_error:
        raise HTTPException(status_code=503, detail=f"OmniVoice model failed to load: {_model_error}")
    if _model is None:
        raise HTTPException(status_code=503, detail="OmniVoice model is not loaded yet.")

    ref_audio_path: Path | None = None
    should_cleanup_ref_audio = False
    try:
        ref_audio_path, should_cleanup_ref_audio = _prepare_reference_audio(payload)
        generate_kwargs: dict[str, object] = {"text": payload.text}
        if payload.speed is not None:
            generate_kwargs["speed"] = payload.speed
        if payload.mode == "design":
            instruction = payload.instruct or _read_optional_text(DEFAULT_INSTRUCTION_PATH)
            if not instruction:
                raise HTTPException(status_code=400, detail="instruct is required when mode='design'.")
            generate_kwargs["instruct"] = instruction
        elif payload.mode == "clone":
            if not ref_audio_path:
                raise HTTPException(status_code=400, detail="ref_audio_url or ref_audio_base64 is required when mode='clone'.")
            generate_kwargs["ref_audio"] = str(ref_audio_path)
            ref_text = payload.ref_text or _read_optional_text(DEFAULT_REF_TEXT_PATH)
            if ref_text:
                generate_kwargs["ref_text"] = ref_text
            instruction = payload.instruct or _read_optional_text(DEFAULT_INSTRUCTION_PATH)
            if instruction and _clone_instruction_enabled():
                generate_kwargs["instruct"] = instruction

        audio = _generate_with_instruction_fallback(generate_kwargs, payload.mode)
        samples = audio[0]
        filename = f"{uuid.uuid4()}.wav"
        output_path = OUTPUT_DIR / filename
        sf.write(output_path, samples, SAMPLE_RATE)

        duration = len(samples) / SAMPLE_RATE
        return output_path, duration
    finally:
        if ref_audio_path and should_cleanup_ref_audio and ref_audio_path.exists():
            ref_audio_path.unlink(missing_ok=True)


def _run_srt_job(job_id: str, payload: SynthesizeSrtRequest) -> None:
    _update_srt_job(job_id, status="processing", updated_at=time.time())
    try:
        output_path, duration = _synthesize_srt_to_path(payload)
        _update_srt_job(
            job_id,
            status="completed",
            filename=output_path.name,
            duration_seconds=duration,
            updated_at=time.time(),
        )
    except Exception as exc:
        logger.exception("SRT synthesis job %s failed.", job_id)
        _update_srt_job(job_id, status="failed", error=str(exc), updated_at=time.time())


def _run_srt_cues_job(job_id: str, payload: SynthesizeSrtRequest) -> None:
    _update_srt_cue_job(job_id, status="processing", updated_at=time.time())
    try:
        cue_outputs, duration = _synthesize_srt_cues_to_paths(payload)
        _update_srt_cue_job(
            job_id,
            status="completed",
            cues=cue_outputs,
            duration_seconds=duration,
            timeline_mode=payload.timeline_mode,
            updated_at=time.time(),
        )
    except Exception as exc:
        logger.exception("SRT cue synthesis job %s failed.", job_id)
        _update_srt_cue_job(job_id, status="failed", error=str(exc), updated_at=time.time())


def _srt_cues_response(
    cue_outputs: list[dict[str, object]],
    duration: float,
    timeline_mode: str,
    base_url: str,
) -> SynthesizeSrtCuesResponse:
    normalized_timeline_mode: Literal["cue", "chunk"] = "chunk" if timeline_mode == "chunk" else "cue"
    return SynthesizeSrtCuesResponse(
        cues=[
            SynthesizeSrtCue(
                id=str(cue["id"]),
                index=int(cue["index"]),
                start=float(cue["start"]),
                end=float(cue["end"]),
                duration_seconds=float(cue["duration_seconds"]),
                target_duration_seconds=float(cue["target_duration_seconds"]),
                audio_url=f"{base_url}/audio/{cue['filename']}",
                filename=str(cue["filename"]),
                text=str(cue["text"]),
            )
            for cue in cue_outputs
        ],
        timeline_mode=normalized_timeline_mode,
        duration_seconds=duration,
    )


def _get_srt_job(job_id: str) -> dict[str, object]:
    with _srt_jobs_lock:
        job = _srt_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="SRT synthesis job not found.")
        return dict(job)


def _update_srt_job(job_id: str, **updates: object) -> None:
    with _srt_jobs_lock:
        if job_id not in _srt_jobs:
            return
        _srt_jobs[job_id].update(updates)


def _get_srt_cue_job(job_id: str) -> dict[str, object]:
    with _srt_cue_jobs_lock:
        job = _srt_cue_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="SRT cue synthesis job not found.")
        return dict(job)


def _update_srt_cue_job(job_id: str, **updates: object) -> None:
    with _srt_cue_jobs_lock:
        if job_id not in _srt_cue_jobs:
            return
        _srt_cue_jobs[job_id].update(updates)


def _clone_instruction_enabled() -> bool:
    return os.getenv("OMNIVOICE_CLONE_INSTRUCT_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}


def _generate_with_instruction_fallback(generate_kwargs: dict[str, object], mode: str):
    try:
        return _model.generate(**generate_kwargs)
    except TypeError as exc:
        if mode == "clone" and "instruct" in generate_kwargs:
            fallback_kwargs = dict(generate_kwargs)
            fallback_kwargs.pop("instruct", None)
            logger.warning("OmniVoice clone+instruction failed; retrying clone without instruction: %s", exc)
            return _model.generate(**fallback_kwargs)
        raise


def _synthesize_srt_to_path(payload: SynthesizeSrtRequest) -> tuple[Path, float]:
    _cues, units, total_duration = _srt_units_from_payload(payload)
    total_samples = max(1, int((total_duration + 0.5) * SAMPLE_RATE))
    timeline = np.zeros(total_samples, dtype=np.float32)
    for index, unit in enumerate(units, start=1):
        text = " ".join(cue["text"] for cue in unit).strip()
        start = float(unit[0]["start"])
        end = float(unit[-1]["end"])
        request_payload = SynthesizeRequest(
            text=text,
            mode=payload.mode,
            speed=payload.speed,
            instruct=payload.instruct,
            ref_text=payload.ref_text,
            ref_audio_url=payload.ref_audio_url,
            ref_audio_base64=payload.ref_audio_base64,
        )
        audio_path, _duration = _synthesize_to_path(request_payload)
        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
        audio_path.unlink(missing_ok=True)
        samples = _to_mono(samples)
        if sample_rate != SAMPLE_RATE:
            raise HTTPException(
                status_code=500,
                detail=f"Chunk {index} sample rate {sample_rate} does not match service sample rate {SAMPLE_RATE}.",
            )

        start_sample = max(0, int(start * SAMPLE_RATE))
        end_sample = min(total_samples, start_sample + len(samples))
        if end_sample <= start_sample:
            continue
        segment = samples[: end_sample - start_sample]
        timeline[start_sample:end_sample] += segment

    peak = float(np.max(np.abs(timeline))) if timeline.size else 0.0
    if peak > 0.98:
        timeline = timeline / peak * 0.98

    filename = f"{uuid.uuid4()}.srt.wav"
    output_path = OUTPUT_DIR / filename
    sf.write(output_path, timeline, SAMPLE_RATE)
    return output_path, len(timeline) / SAMPLE_RATE


def _synthesize_srt_cues_to_paths(payload: SynthesizeSrtRequest) -> tuple[list[dict[str, object]], float]:
    _cues, units, total_duration = _srt_units_from_payload(payload)
    cue_outputs: list[dict[str, object]] = []

    for index, unit in enumerate(units, start=1):
        text = " ".join(cue["text"] for cue in unit).strip()
        start = float(unit[0]["start"])
        end = float(unit[-1]["end"])
        slot_duration = max(0.1, end - start)
        request_payload = SynthesizeRequest(
            text=text,
            mode=payload.mode,
            speed=payload.speed,
            instruct=payload.instruct,
            ref_text=payload.ref_text,
            ref_audio_url=payload.ref_audio_url,
            ref_audio_base64=payload.ref_audio_base64,
        )
        audio_path, generated_duration = _synthesize_to_path(request_payload)
        samples, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
        samples = _to_mono(samples)
        if sample_rate != SAMPLE_RATE:
            audio_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=500,
                detail=f"Cue {index} sample rate {sample_rate} does not match service sample rate {SAMPLE_RATE}.",
            )

        cue_outputs.append(
            {
                "id": f"cue-{index:04d}",
                "index": index,
                "start": start,
                "end": end,
                "duration_seconds": len(samples) / SAMPLE_RATE if samples.size else generated_duration,
                "target_duration_seconds": slot_duration,
                "filename": audio_path.name,
                "text": text,
            }
        )

    return cue_outputs, total_duration


def _srt_units_from_payload(payload: SynthesizeSrtRequest) -> tuple[list[dict[str, object]], list[list[dict[str, object]]], float]:
    srt_text = _decode_srt_payload(payload)
    cues = _parse_srt_cues(srt_text)
    if not cues:
        raise HTTPException(status_code=400, detail="SRT payload does not contain readable subtitle cues.")

    units = [[cue] for cue in cues] if payload.timeline_mode == "cue" else _chunk_srt_cues(
        cues,
        min_seconds=payload.chunk_min_seconds,
        max_seconds=payload.chunk_max_seconds,
        max_chars=payload.chunk_max_chars,
    )
    if not units:
        raise HTTPException(status_code=400, detail="No TTS units could be created from the SRT payload.")

    return cues, units, max(float(cue["end"]) for cue in cues)


def _decode_srt_payload(payload: SynthesizeSrtRequest) -> str:
    if payload.srt_text and payload.srt_text.strip():
        return payload.srt_text.strip()
    if payload.srt_base64:
        data = payload.srt_base64
        if "," in data:
            _header, data = data.split(",", 1)
        return base64.b64decode(data).decode("utf-8", errors="ignore").strip()
    raise HTTPException(status_code=400, detail="srt_text or srt_base64 is required.")


def _parse_srt_cues(srt_text: str) -> list[dict[str, object]]:
    cues: list[dict[str, object]] = []
    blocks = re.split(r"\n\s*\n", srt_text.replace("\r\n", "\n").replace("\r", "\n").strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_line_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if time_line_index < 0:
            continue
        match = re.match(
            r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})",
            lines[time_line_index],
        )
        if not match:
            continue
        text = " ".join(lines[time_line_index + 1 :]).strip()
        if not text:
            continue
        cues.append(
            {
                "start": _srt_timestamp_to_seconds(match.group("start")),
                "end": _srt_timestamp_to_seconds(match.group("end")),
                "text": text,
            }
        )
    return cues


def _srt_timestamp_to_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds, millis = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _chunk_srt_cues(
    cues: list[dict[str, object]],
    min_seconds: float,
    max_seconds: float,
    max_chars: int,
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for cue in cues:
        proposed = [*current, cue]
        duration = float(proposed[-1]["end"]) - float(proposed[0]["start"])
        char_count = len(" ".join(str(item["text"]) for item in proposed))
        should_flush = bool(current) and duration > max_seconds
        should_flush = should_flush or (bool(current) and char_count > max_chars)
        if should_flush:
            chunks.append(current)
            current = [cue]
            continue
        current = proposed
        if duration >= min_seconds and _looks_like_sentence_end(str(cue["text"])):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _looks_like_sentence_end(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…]['\")\]]?\s*$", text.strip()))


def _to_mono(samples: np.ndarray) -> np.ndarray:
    if samples.ndim == 1:
        return samples.astype(np.float32)
    return samples.mean(axis=1).astype(np.float32)


def _verify_api_key(authorization: str | None) -> None:
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid OmniVoice API key.")


def _prepare_reference_audio(payload: SynthesizeRequest) -> tuple[Path | None, bool]:
    if payload.ref_audio_base64:
        suffix = ".wav"
        if "," in payload.ref_audio_base64:
            header, data = payload.ref_audio_base64.split(",", 1)
            if "mpeg" in header or "mp3" in header:
                suffix = ".mp3"
            elif "flac" in header:
                suffix = ".flac"
        else:
            data = payload.ref_audio_base64
        path = Path(tempfile.gettempdir()) / f"omnivoice_ref_{uuid.uuid4()}{suffix}"
        path.write_bytes(base64.b64decode(data))
        return path, True

    if payload.ref_audio_url:
        suffix = Path(payload.ref_audio_url.split("?")[0]).suffix or ".wav"
        path = Path(tempfile.gettempdir()) / f"omnivoice_ref_{uuid.uuid4()}{suffix}"
        with requests.get(payload.ref_audio_url, stream=True, timeout=60) as response:
            response.raise_for_status()
            with path.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if chunk:
                        file.write(chunk)
        return path, True

    if DEFAULT_REF_AUDIO_PATH.exists():
        return DEFAULT_REF_AUDIO_PATH.resolve(), False

    return None, False


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return text or None


def start_service(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    share: bool = False,
    kill_existing: bool = False,
    auto_port: bool = False,
) -> threading.Thread | None:
    import uvicorn

    if _port_is_in_use(port):
        if kill_existing:
            print(f"Port {port} is already in use. Stopping existing process before loading OmniVoice.")
            _kill_processes_on_port(port)
            time.sleep(2)

        if _port_is_in_use(port) and auto_port:
            old_port = port
            port = _find_available_port(port + 1)
            print(f"Port {old_port} is already in use. Starting on free port {port} instead.")
        elif _port_is_in_use(port):
            raise RuntimeError(
                f"Port {port} is already in use. Run `!lsof -i :{port}` and `!kill -9 <PID>`, "
                "start with `--kill-existing`, or start with `--auto-port`."
            )

    if os.getenv("OMNIVOICE_LOAD_ON_STARTUP", "1") == "1":
        start_model_loading()

    if share:
        public_url = _start_public_tunnel(port)
        if public_url:
            print(f"Public OmniVoice URL: {public_url}")
            print(f"Set Aether .env: OMNIVOICE_API_URL={public_url}")
        else:
            print("No public tunnel was started. The service is still available inside Colab at 127.0.0.1.")

    print(f"Starting OmniVoice service on http://{host}:{port}")
    print(f"Inside Colab, test: http://127.0.0.1:{port}/health")

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        uvicorn.run(app, host=host, port=port, log_level="info")
        return None

    thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port, log_level="info"),
        daemon=False,
        name="omnivoice-uvicorn",
    )
    thread.start()
    time.sleep(2)
    print(f"OmniVoice service thread started. Local runtime URL: http://127.0.0.1:{port}")
    print("If this is Colab, open the ngrok/cloudflared public URL from your laptop.")
    return thread


def _port_is_in_use(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _find_available_port(start_port: int, max_attempts: int = 50) -> int:
    for candidate in range(start_port, start_port + max_attempts):
        if not _port_is_in_use(candidate):
            return candidate
    raise RuntimeError(f"No available port found from {start_port} to {start_port + max_attempts - 1}.")


def _kill_processes_on_port(port: int) -> None:
    if os.name == "nt":
        command = (
            f"for /f \"tokens=5\" %a in ('netstat -ano ^| findstr :{port}') "
            "do taskkill /F /PID %a"
        )
        subprocess.run(command, shell=True, check=False)
        return

    result = subprocess.run(["bash", "-lc", f"lsof -ti :{port}"], capture_output=True, text=True, check=False)
    pids = [pid.strip() for pid in result.stdout.splitlines() if pid.strip()]
    for pid in pids:
        subprocess.run(["kill", "-9", pid], check=False)


def _start_public_tunnel(port: int) -> str | None:
    provider = os.getenv("OMNIVOICE_SHARE_PROVIDER", "auto").strip().lower()
    if provider in {"auto", "ngrok"}:
        public_url = _start_ngrok(port)
        if public_url or provider == "ngrok":
            return public_url
    if provider in {"auto", "cloudflare", "cloudflared"}:
        return _start_cloudflared(port)
    return None


def _start_ngrok(port: int) -> str | None:
    try:
        from pyngrok import ngrok
    except Exception as exc:
        print(f"pyngrok is not installed or failed to import: {exc}")
        print("Install it with: pip install pyngrok")
        return None

    auth_token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if auth_token:
        ngrok.set_auth_token(auth_token)

    try:
        tunnel = ngrok.connect(port, "http")
        return str(tunnel.public_url)
    except Exception as exc:
        print(f"ngrok failed: {exc}")
        print("Tip: set NGROK_AUTHTOKEN, or use Cloudflare Tunnel fallback.")
        return None


def _start_cloudflared(port: int) -> str | None:
    binary = _ensure_cloudflared()
    if not binary:
        return None

    command = [str(binary), "tunnel", "--url", f"http://127.0.0.1:{port}"]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    deadline = time.time() + 45
    while time.time() < deadline:
        line = process.stdout.readline() if process.stdout else ""
        if not line:
            time.sleep(0.2)
            continue
        print(line.rstrip())
        match = re.search(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", line)
        if match:
            return match.group(0)

    print("cloudflared started, but no public URL was detected yet.")
    print("Look for a https://*.trycloudflare.com URL in the Colab output.")
    return None


def _ensure_cloudflared() -> Path | None:
    configured = os.getenv("CLOUDFLARED_BIN", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path("cloudflared"), Path("/content/cloudflared"), Path("./cloudflared")])
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()

    destination = Path("/content/cloudflared")
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    try:
        print("Downloading cloudflared tunnel binary...")
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        destination.write_bytes(response.content)
        destination.chmod(0o755)
        return destination
    except Exception as exc:
        print(f"Unable to install cloudflared automatically: {exc}")
        print("Manual Colab fallback:")
        print("!wget -q -O /content/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64")
        print("!chmod +x /content/cloudflared")
        print("!/content/cloudflared tunnel --url http://127.0.0.1:8008")
        return None



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the OmniVoice FastAPI service.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--share", action="store_true", default=os.getenv("OMNIVOICE_SHARE", "0") == "1")
    parser.add_argument("--kill-existing", action="store_true", default=os.getenv("OMNIVOICE_KILL_EXISTING", "0") == "1")
    parser.add_argument("--auto-port", action="store_true", default=os.getenv("OMNIVOICE_AUTO_PORT", "0") == "1")
    args, _unknown_args = parser.parse_known_args()

    start_service(host=args.host, port=args.port, share=args.share, kill_existing=args.kill_existing, auto_port=args.auto_port)
