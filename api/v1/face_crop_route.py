from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from security.auth import verify_any_token
from security.principal import AuthPrincipal
from services.face_crop_service import crop_face

router = APIRouter(prefix="/face-crop", tags=["Face Crop (Test)"])


@router.post(
    "/test",
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "Cropped face as JPEG"},
        400: {"description": "Invalid or corrupted image/PDF"},
        401: {"description": "Unauthorized — authentication required"},
        413: {"description": "File exceeds 16 MiB limit"},
        422: {"description": "No face detected"},
        503: {"description": "Face cropping dependencies not installed"},
    },
)
async def face_crop_test(
    file: UploadFile = File(...),
    _principal: AuthPrincipal = Depends(verify_any_token),
) -> Response:
    """Authenticated diagnostic: upload an image or PDF, receive the cropped face.

    Accepts JPEG, PNG, WEBP, BMP, or PDF (first page is rasterized). Uses
    MediaPipe for face detection and OpenCV for decode/crop/encode.

    Requires authentication — face cropping is CPU-intensive, so leaving it
    open anonymously was an abuse / DoS surface. The real check-in flow crops
    faces server-side internally; this endpoint is a staff diagnostic only.
    """
    image_bytes = await file.read()
    cropped = await crop_face(image_bytes)
    return Response(content=cropped, media_type="image/jpeg")
