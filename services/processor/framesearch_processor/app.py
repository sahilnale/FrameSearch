"""Internal HTTP interface. Run one Uvicorn process so only one model is loaded."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StringConstraints

from .embeddings import OpenClipEmbedder
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


@dataclass
class ModelState:
    embedder: OpenClipEmbedder | None = None
    failed: bool = False


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def create_app(
    settings: Settings | None = None,
    model_loader: Callable[[Settings], OpenClipEmbedder] = OpenClipEmbedder,
) -> FastAPI:
    """Factory permits isolated tests; the runnable app always uses real OpenCLIP."""
    state = ModelState()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configuration = settings if settings is not None else Settings.from_env()
        state.embedder = None
        state.failed = False
        application.state.model = state

        # Both blocking load and inference run off the event loop. The indexing worker
        # will reuse state.embedder, with a separate thread and one job at a time.
        async def initialize() -> None:
            try:
                state.embedder = await asyncio.to_thread(model_loader, configuration)
            except Exception:
                state.failed = True
                logger.exception("OpenCLIP startup failed")

        task = asyncio.create_task(initialize(), name="load-openclip")
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            state.embedder = None

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
        if state.failed:
            return error(
                503, "model_load_failed", "OpenCLIP initialization failed; see processor logs"
            )
        if state.embedder is None:
            return error(503, "model_loading", "OpenCLIP is loading its checkpoint")
        return {"status": "ready", "model_version": state.embedder.model_version}

    @application.post("/embed/text", response_model=TextResponse)
    async def embed(request: TextRequest):
        if state.embedder is None:
            return error(503, "model_unavailable", "OpenCLIP is not ready; check /health/ready")
        try:
            async with inference_lock:
                vector = await asyncio.to_thread(state.embedder.embed_text, request.text)
            return TextResponse(embedding=vector, model_version=state.embedder.model_version)
        except Exception:
            logger.exception("Text inference failed")
            return error(500, "embedding_failed", "Text inference failed; see processor logs")

    return application


app = create_app()
