from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response

from services.face_crop_service import crop_face

router = APIRouter(prefix="/face-crop", tags=["Face Crop (Test)"])


@router.post(
    "/test",
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Cropped face as JPEG"},
        400: {"description": "Invalid or corrupted image/PDF"},
        413: {"description": "File exceeds 16 MiB limit"},
        422: {"description": "No face detected"},
        503: {"description": "Face cropping dependencies not installed"},
    },
)
async def face_crop_test(file: UploadFile = File(...)) -> Response:
    """Public test endpoint: upload an image or PDF, receive the cropped face as JPEG.

    Accepts JPEG, PNG, WEBP, BMP, or PDF (first page is rasterized).
    Uses MediaPipe for face detection and OpenCV for decode/crop/encode.
    No authentication required.
    """
    image_bytes = await file.read()
    cropped = await crop_face(image_bytes)
    return Response(content=cropped, media_type="image/jpeg")
