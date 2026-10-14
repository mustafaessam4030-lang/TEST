"""HTTP API and static UI (FastAPI).

    GET    /api/health
    GET    /api/documents                         library with status and page summary
    POST   /api/documents?filename=&equipment=&doc_type=   raw PDF body; 202, or 200 if duplicate
    GET    /api/documents/{id}                    detail with per-page status
    POST   /api/documents/{id}/reindex
    DELETE /api/documents/{id}
    GET    /api/documents/{id}/file               the original PDF (open at #page=N)
    GET    /api/documents/{id}/pages/{n}/image?chunk=   rendered page, cited passage highlighted
    POST   /api/chat  {message, conversation_id?, document_ids?}

Uploads are sent as the raw request body so no multipart parser is needed.
When EKA_API_TOKEN is set every /api call must send it as a Bearer token.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import Settings, load_settings
from .ingest import UploadError
from .service import Assistant

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
PUBLIC_DOC_FIELDS = ("id", "filename", "title", "doc_number", "revision", "revision_date", "equipment",
                     "doc_type", "page_count", "bytes", "status", "error", "progress_done", "progress_total",
                     "stage", "superseded_by", "created_at", "updated_at")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=64)
    document_ids: list[str] | None = Field(default=None, max_length=200)
    retry_of: str | None = Field(default=None, max_length=64)   # replace this earlier answer


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class Feedback(BaseModel):
    value: str = Field(pattern="^(up|down|)$")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.assistant = Assistant(settings)
        log.info("Engineering Knowledge Assistant ready: %s", app.state.assistant.health())
        yield
        app.state.assistant.close()

    app = FastAPI(title="Engineering Knowledge Assistant", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    def assistant(request: Request) -> Assistant:
        return request.app.state.assistant

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if settings.api_token and path.startswith("/api/") and path not in ("/api/health",):
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
                or request.query_params.get("token", "")
            if not hmac.compare_digest(supplied, settings.api_token):
                return JSONResponse({"detail": "missing or invalid API token"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not path.endswith("/file"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    def public(doc: dict, summary: dict | None = None) -> dict:
        out = {k: doc[k] for k in PUBLIC_DOC_FIELDS}
        if summary is not None:
            out["page_status"] = summary.get(doc["id"], {})
        return out

    def get_doc(a: Assistant, doc_id: str) -> dict:
        doc = a.store.document(doc_id)
        if doc is None:
            raise HTTPException(404, "document not found")
        return doc

    @app.get("/api/health")
    def health(request: Request):
        return assistant(request).health()

    @app.get("/api/documents")
    def documents(request: Request):
        a = assistant(request)
        summary = a.store.page_summary()
        return {"documents": [public(d, summary) for d in a.store.documents()]}

    @app.post("/api/documents", status_code=202)
    async def upload(request: Request, filename: str = "document.pdf", equipment: str = "", doc_type: str = ""):
        a = assistant(request)
        limit = settings.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "the file exceeds the {0} MB upload limit".format(settings.max_upload_mb))
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > limit:
                raise HTTPException(413, "the file exceeds the {0} MB upload limit".format(settings.max_upload_mb))
        try:
            doc, duplicate = await run_in_threadpool(
                a.ingest.submit, bytes(body), filename, {"equipment": equipment, "doc_type": doc_type})
        except UploadError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        payload = {"document": public(doc), "duplicate": duplicate}
        if duplicate:
            payload["message"] = "This file is already in the library as \"{0}\"; it was not indexed again.".format(
                doc["title"] or doc["filename"])
            return JSONResponse(payload, status_code=200)
        return payload

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: str, request: Request):
        a = assistant(request)
        doc = get_doc(a, doc_id)
        return {"document": public(doc), "pages": a.store.pages(doc_id)}

    @app.post("/api/documents/{doc_id}/reindex", status_code=202)
    def reindex(doc_id: str, request: Request):
        a = assistant(request)
        if get_doc(a, doc_id)["status"] in ("queued", "processing"):
            raise HTTPException(409, "the document is already queued or being processed")
        return {"document": public(a.ingest.reindex(doc_id))}

    @app.delete("/api/documents/{doc_id}")
    def delete(doc_id: str, request: Request):
        a = assistant(request)
        doc = get_doc(a, doc_id)
        if doc["status"] in ("queued", "processing"):
            raise HTTPException(409, "the document is still being processed; delete it when processing ends")
        a.ingest.delete(doc_id)
        return {"deleted": doc_id}

    @app.get("/api/documents/{doc_id}/file")
    def file(doc_id: str, request: Request):
        a = assistant(request)
        doc = get_doc(a, doc_id)
        path = a.ingest.file_path(doc_id)
        if not path.exists():
            raise HTTPException(404, "the stored file is missing")
        return FileResponse(path, media_type="application/pdf", filename=doc["filename"],
                            content_disposition_type="inline")

    @app.get("/api/documents/{doc_id}/pages/{page_no}/image")
    def page_image(doc_id: str, page_no: int, request: Request, chunk: str = "", zoom: float = 1.6, q: str = ""):
        a = assistant(request)
        doc = get_doc(a, doc_id)
        if not 1 <= page_no <= max(doc["page_count"], 1):
            raise HTTPException(404, "page {0} does not exist in this document".format(page_no))
        boxes = []
        if chunk:
            row = a.store.chunk(chunk)
            if row and row["doc_id"] == doc_id and row["page_no"] == page_no:
                boxes = json.loads(row["bboxes"])
        try:
            png = render_page(a.ingest.file_path(doc_id), page_no, boxes, max(0.5, min(zoom, 3.0)),
                              " ".join(q.split())[:100])
        except Exception as exc:
            raise HTTPException(422, "the page could not be rendered: {0}".format(exc)) from exc
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})

    def apply_retry(a: Assistant, body: ChatRequest):
        if not body.retry_of:
            return
        old = a.store.message(body.retry_of)
        if old is None or old["conversation_id"] != body.conversation_id:
            raise HTTPException(404, "the answer to retry was not found in this conversation")
        a.store.delete_message(body.retry_of)
        a.conversations.forget(body.conversation_id)

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request):
        a = assistant(request)
        apply_retry(a, body)
        return await run_in_threadpool(a.engine.ask, body.message, body.conversation_id, body.document_ids)

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request):
        """Server-sent events: ``stage`` (real pipeline steps), then ``answer`` or ``error``.

        The answer is recorded only after it has been delivered; a client that
        stops the response (disconnects) leaves nothing in the history.
        """
        a = assistant(request)
        apply_retry(a, body)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def put(kind, data):
            loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

        def work():
            try:
                put("answer", a.engine.ask(body.message, body.conversation_id, body.document_ids,
                                           progress=lambda text: put("stage", {"text": text}), persist=False))
            except Exception as exc:  # the engine already turns its own failures into answers
                log.exception("chat stream failed")
                put("error", {"detail": "The answer could not be produced ({0}).".format(type(exc).__name__)})

        loop.run_in_executor(None, work)

        async def events():
            yield "event: stage\ndata: {0}\n\n".format(json.dumps({"text": "Understanding the question"}))
            while True:
                kind, data = await queue.get()
                yield "event: {0}\ndata: {1}\n\n".format(kind, json.dumps(data))
                if kind == "answer":
                    if not await request.is_disconnected():
                        a.engine.commit(data)
                    return
                if kind == "error":
                    return

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/suggestions")
    async def suggestions(request: Request, limit: int = 4):
        a = assistant(request)
        return {"suggestions": await run_in_threadpool(a.engine.suggestions, max(1, min(limit, 6)))}

    # ── conversation history ──
    @app.get("/api/conversations")
    def conversations(request: Request, q: str = ""):
        return {"conversations": assistant(request).store.conversations(q.strip()[:200])}

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str, request: Request):
        a = assistant(request)
        conv = a.store.conversation(conversation_id)
        if conv is None:
            raise HTTPException(404, "conversation not found")
        turns = [{"message_id": m["id"], "question": m["question"], "answer": json.loads(m["answer"]),
                  "feedback": m["feedback"], "created_at": m["created_at"]} for m in a.store.messages(conversation_id)]
        return {"conversation": conv, "turns": turns}

    @app.patch("/api/conversations/{conversation_id}")
    def rename(conversation_id: str, body: Rename, request: Request):
        a = assistant(request)
        if a.store.conversation(conversation_id) is None:
            raise HTTPException(404, "conversation not found")
        a.store.rename_conversation(conversation_id, body.title)
        return {"conversation": a.store.conversation(conversation_id)}

    @app.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str, request: Request):
        a = assistant(request)
        if a.store.conversation(conversation_id) is None:
            raise HTTPException(404, "conversation not found")
        a.store.delete_conversation(conversation_id)
        a.conversations.forget(conversation_id)
        return {"deleted": conversation_id}

    @app.post("/api/messages/{message_id}/feedback")
    def feedback(message_id: str, body: Feedback, request: Request):
        a = assistant(request)
        if a.store.message(message_id) is None:
            raise HTTPException(404, "message not found")
        a.store.set_feedback(message_id, body.value)
        return {"message_id": message_id, "feedback": body.value}

    @app.get("/api/documents/{doc_id}/search")
    def search_document(doc_id: str, request: Request, q: str = ""):
        a = assistant(request)
        get_doc(a, doc_id)
        q = " ".join(q.split())[:100]
        if len(q) < 2:
            raise HTTPException(400, "type at least two characters to search")
        return {"query": q, "results": search_pdf(a, doc_id, q)}

    @app.exception_handler(UploadError)
    async def upload_error(_, exc: UploadError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    return app


def search_pdf(a: Assistant, doc_id: str, q: str) -> list[dict]:
    """Pages containing ``q``: the text layer for native pages, stored OCR text for scanned ones."""
    results = []
    ocr_pages = {p["page_no"] for p in a.store.pages(doc_id) if p["method"] == "ocr"}
    needle = q.lower()
    doc = pymupdf.open(a.ingest.file_path(doc_id))
    try:
        for page in doc:
            page_no = page.number + 1
            if page_no in ocr_pages:
                text = " ".join(c["text"] for c in a.store.all_chunks() if c["doc_id"] == doc_id
                                and c["page_no"] == page_no)
                hits = text.lower().count(needle)
            else:
                text = page.get_text()
                hits = len(page.search_for(q))
            if not hits:
                continue
            flat = " ".join(text.split())
            at = flat.lower().find(needle)
            snippet = flat[max(0, at - 70): at + len(q) + 70] if at >= 0 else flat[:140]
            results.append({"page": page_no, "hits": hits, "snippet": snippet, "ocr": page_no in ocr_pages})
    finally:
        doc.close()
    return results


def render_page(path: Path, page_no: int, boxes: list, zoom: float, query: str = "") -> bytes:
    doc = pymupdf.open(path)
    try:
        page = doc[page_no - 1]
        for rect in (page.search_for(query) if len(query) >= 2 else []):
            page.draw_rect(rect + (-1, -1, 1, 1), color=None, fill=(0.29, 0.31, 0.85), fill_opacity=0.22)
        for box in boxes:
            rect = pymupdf.Rect(box) + (-3, -2, 3, 2)
            page.draw_rect(rect, color=(0.95, 0.6, 0.0), fill=(1.0, 0.85, 0.2), fill_opacity=0.28, width=1.2)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    finally:
        doc.close()
