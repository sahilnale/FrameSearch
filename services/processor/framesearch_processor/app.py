"""Internal HTTP interface. Run one Uvicorn process so only one model is loaded."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .embeddings import OpenClipEmbedder
from .runtime import ProcessorRuntime, WorkerFactory, build_worker
from .settings import MAX_TEXT_CHARACTERS, Settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class TextRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARACTERS)
    ]


class TextResponse(BaseModel):
    embedding: list[float]
    model_version: str


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    settings: Settings | None = None,
    model_loader: Callable[[Settings], OpenClipEmbedder] = OpenClipEmbedder,
    *,
    worker_factory: WorkerFactory | None = build_worker,
) -> FastAPI:
    """Factory permits isolated tests; the runnable app always uses real OpenCLIP."""
    state: ProcessorRuntime | None = None

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        nonlocal state
        configuration = settings if settings is not None else Settings.from_env()
        state = ProcessorRuntime(configuration, model_loader, worker_factory)
        application.state.model = state
        state.start()
        try:
            yield
        finally:
            state.stop.set()
            await asyncio.to_thread(state.join)
            state.embedder = None
            state.worker = None

    application = FastAPI(
        title="FrameSearch processor",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Keep only one HTTP inference active, in addition to the embedder's per-batch lock.
    inference_lock = asyncio.Lock()

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_, exc: RequestValidationError) -> JSONResponse:
        details = "; ".join(item["msg"] for item in exc.errors())
        return error(422, "invalid_request", details)

    @application.get("/health/live")
    async def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready():
        if state is not None and state.stop.is_set():
            return error(503, "processor_stopping", "Processor is finishing its current work")
        if state is not None and state.model_failed.is_set():
            return error(
                503, "model_load_failed", "OpenCLIP initialization failed; see processor logs"
            )
        if state is None or state.embedder is None:
            return error(503, "model_loading", "OpenCLIP is loading its checkpoint")
        if state.worker_failed.is_set():
            return error(
                503, "worker_failed", "Ingestion stopped; inspect processor logs and pending work"
            )
        if state.worker_enabled and (state.worker is None or not state.worker.ready.is_set()):
            return error(
                503, "worker_starting", "Ingestion is checking database, storage, and Kafka"
            )
        return {"status": "ready", "model_version": state.embedder.model_version}

    @application.post("/embed/text", response_model=TextResponse)
    async def embed(request: TextRequest):
        model = state.embedder if state is not None and not state.stop.is_set() else None
        if model is None:
            return error(503, "model_unavailable", "OpenCLIP is not ready; check /health/ready")
        try:
            async with inference_lock:
                vector = await asyncio.to_thread(model.embed_text, request.text)
            return TextResponse(embedding=vector, model_version=model.model_version)
        except Exception:
            logger.exception("Text inference failed")
            return error(500, "embedding_failed", "Text inference failed; see processor logs")

    return application


app = create_app()
