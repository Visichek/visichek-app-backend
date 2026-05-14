"""Public GridFS video streaming — ``GET /videos/{video_id}``.

Lives at the root of the FastAPI app (no ``/v1`` prefix) because the
URLs we hand out from ``save_video_to_mongodb`` look like
``/videos/{id}``. Frontends concatenate these onto the API base URL.

Kept synchronous — GridFS streams are I/O-bound and don't benefit from
the queue. We stream chunks straight from Mongo to the client.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import StreamingResponse

from blog.repositories.media_repo import open_video_stream

router = APIRouter(tags=["Public Media"])


@router.get("/videos/{video_id}")
async def get_video(
    video_id: str = Path(..., description="GridFS file id")
):
    """Stream a stored video.

    Returns the raw byte stream with the original ``content_type`` from
    the GridFS metadata. The route bypasses the ``@document_response``
    envelope because clients (video tags / players) need the raw
    response body.
    """
    try:
        stream = await open_video_stream(video_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Video not found")

    content_type = "video/mp4"
    metadata = getattr(stream, "metadata", None) or {}
    if metadata.get("content_type"):
        content_type = metadata["content_type"]

    return StreamingResponse(stream, media_type=content_type)
