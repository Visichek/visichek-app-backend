from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from core.errors import AppException, ErrorCode

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 16 * 1024 * 1024  # 16 MiB
DEFAULT_PADDING_RATIO = 0.2
PDF_RENDER_SCALE = 4.0  # ~288 DPI
PDF_MAGIC = b"%PDF-"

# Roboflow hosted inference config (override via env vars)
ROBOFLOW_API_URL = os.getenv("ROBOFLOW_API_URL", "https://serverless.roboflow.com")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "IDaqV6JQ9wkdFtO5mKeh")
ROBOFLOW_WORKSPACE = os.getenv("ROBOFLOW_WORKSPACE", "final-year-project-x3ycf")
ROBOFLOW_WORKFLOW_ID = os.getenv("ROBOFLOW_WORKFLOW_ID", "general-segmentation-api")
ROBOFLOW_CLASSES = os.getenv("ROBOFLOW_CLASSES", "face")


def _load_cv2() -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise AppException(
            status_code=503,
            code=ErrorCode.INTERNAL_ERROR,
            message="Face cropping unavailable: opencv-python not installed",
        ) from exc
    return cv2


def _load_inference_sdk() -> Any:
    try:
        from inference_sdk import InferenceHTTPClient
    except ImportError as exc:
        raise AppException(
            status_code=503,
            code=ErrorCode.INTERNAL_ERROR,
            message="Face cropping unavailable: inference-sdk not installed",
        ) from exc
    return InferenceHTTPClient


_client_instance: Any = None


def _get_client() -> Any:
    global _client_instance
    if _client_instance is not None:
        return _client_instance

    InferenceHTTPClient = _load_inference_sdk()
    _client_instance = InferenceHTTPClient(
        api_url=ROBOFLOW_API_URL,
        api_key=ROBOFLOW_API_KEY,
    )
    return _client_instance


def _load_pypdfium2() -> Any:
    try:
        import pypdfium2
    except ImportError as exc:
        raise AppException(
            status_code=503,
            code=ErrorCode.INTERNAL_ERROR,
            message="PDF support unavailable: pypdfium2 not installed",
        ) from exc
    return pypdfium2


def _rasterize_pdf_first_page(pdf_bytes: bytes) -> Any:
    """Render the first page of a PDF to a BGR numpy array for OpenCV."""
    pypdfium2 = _load_pypdfium2()

    import numpy as np

    try:
        pdf = pypdfium2.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Could not open PDF (corrupted or password-protected)",
        ) from exc

    if len(pdf) == 0:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="PDF has no pages",
        )

    page = pdf[0]
    pil_image = page.render(scale=PDF_RENDER_SCALE).to_pil().convert("RGB")
    rgb = np.array(pil_image)
    # PIL is RGB; OpenCV works in BGR
    return rgb[:, :, ::-1].copy()


def _prediction_to_bbox(
    pred: dict,
) -> Optional[tuple[int, int, int, int, float]]:
    """Convert a single prediction dict into (x1, y1, x2, y2, confidence).

    Handles the three common Roboflow response shapes:
    - Detection: ``x``, ``y`` (center), ``width``, ``height``, ``confidence``
    - Segmentation polygon: ``points: [{x, y}, ...]`` + ``confidence``
    - Explicit bbox: ``x1, y1, x2, y2``
    """
    if not isinstance(pred, dict):
        return None

    conf = float(pred.get("confidence") or pred.get("score") or 0.0)

    # Explicit bbox
    if all(k in pred for k in ("x1", "y1", "x2", "y2")):
        return (
            int(pred["x1"]),
            int(pred["y1"]),
            int(pred["x2"]),
            int(pred["y2"]),
            conf,
        )

    # YOLO-style center bbox
    if all(k in pred for k in ("x", "y", "width", "height")):
        cx, cy = float(pred["x"]), float(pred["y"])
        w, h = float(pred["width"]), float(pred["height"])
        return (
            int(cx - w / 2),
            int(cy - h / 2),
            int(cx + w / 2),
            int(cy + h / 2),
            conf,
        )

    # Segmentation polygon → compute bbox from points
    points = pred.get("points") or pred.get("polygon")
    if isinstance(points, list) and points:
        xs, ys = [], []
        for p in points:
            if isinstance(p, dict):
                xs.append(float(p.get("x", 0)))
                ys.append(float(p.get("y", 0)))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                xs.append(float(p[0]))
                ys.append(float(p[1]))
        if xs and ys:
            return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)), conf)

    return None


def _describe_shape(obj: Any, depth: int = 0) -> str:
    """Return a compact description of a nested structure's keys/types for logs."""
    if depth > 3:
        return "..."
    if isinstance(obj, dict):
        return (
            "{"
            + ", ".join(
                f"{k}={_describe_shape(v, depth + 1)}"
                for k, v in list(obj.items())[:10]
            )
            + "}"
        )
    if isinstance(obj, list):
        if not obj:
            return "[]"
        return f"[{_describe_shape(obj[0], depth + 1)} * {len(obj)}]"
    if isinstance(obj, str):
        return f"str({len(obj)})"
    return type(obj).__name__


def _extract_best_bbox(
    result: Any,
) -> Optional[tuple[int, int, int, int]]:
    """Walk the Roboflow workflow response and return the highest-confidence bbox.

    The response shape varies by workflow. Rather than looking under a specific
    key, we walk every dict and try to parse it as a prediction — this picks up
    detections stored under ``predictions``, ``detections``, ``faces``, etc.
    """
    best: Optional[tuple[int, int, int, int]] = None
    best_conf: float = -1.0

    def walk(obj: Any) -> None:
        nonlocal best, best_conf
        if isinstance(obj, dict):
            # Skip known non-prediction payloads
            if obj.get("type") == "base64":
                return
            bbox = _prediction_to_bbox(obj)
            if bbox is not None:
                x1, y1, x2, y2, conf = bbox
                if conf > best_conf:
                    best_conf = conf
                    best = (x1, y1, x2, y2)
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(result)
    return best


async def crop_face(
    image_bytes: bytes, padding_ratio: float = DEFAULT_PADDING_RATIO
) -> bytes:
    """Detect a face via Roboflow hosted inference and return the crop as JPEG.

    Accepts JPEG/PNG/WEBP/BMP or PDF (first page rasterized).
    """
    if not image_bytes:
        raise AppException(
            status_code=400,
            code=ErrorCode.VALIDATION_FAILED,
            message="Empty image payload",
        )

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise AppException(
            status_code=413,
            code=ErrorCode.VALIDATION_FAILED,
            message=f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MiB limit",
        )

    cv2 = _load_cv2()

    import numpy as np

    if image_bytes[:5] == PDF_MAGIC:
        image = _rasterize_pdf_first_page(image_bytes)
    else:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            raise AppException(
                status_code=400,
                code=ErrorCode.VALIDATION_FAILED,
                message="Could not decode image (unsupported format or corrupted)",
            )

    height, width = image.shape[:2]

    # Roboflow SDK wants a file path. Write the processed image to a temp file.
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        cv2.imwrite(str(tmp_path), image)

        client = _get_client()
        try:
            result = client.run_workflow(
                workspace_name=ROBOFLOW_WORKSPACE,
                workflow_id=ROBOFLOW_WORKFLOW_ID,
                images={"image": str(tmp_path)},
                parameters={"classes": ROBOFLOW_CLASSES},
                use_cache=True,
            )
        except Exception as exc:
            logger.exception("Roboflow inference failed")
            raise AppException(
                status_code=502,
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Face detection service error: {exc}",
            ) from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    bbox = _extract_best_bbox(result)
    if bbox is None:
        logger.warning(
            "No bbox extracted from Roboflow response; shape=%s",
            _describe_shape(result),
        )
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="No face detected in the image",
        )

    x1, y1, x2, y2 = bbox
    w = x2 - x1
    h = y2 - y1

    pad_x = int(w * padding_ratio)
    pad_y = int(h * padding_ratio)

    x1 = max(x1 - pad_x, 0)
    y1 = max(y1 - pad_y, 0)
    x2 = min(x2 + pad_x, width)
    y2 = min(y2 + pad_y, height)

    if x2 <= x1 or y2 <= y1:
        raise AppException(
            status_code=422,
            code=ErrorCode.VALIDATION_FAILED,
            message="Detected face bounding box is invalid",
        )

    cropped = image[y1:y2, x1:x2]

    success, buffer = cv2.imencode(".jpg", cropped)
    if not success:
        raise AppException(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to encode cropped image",
        )

    return buffer.tobytes()
