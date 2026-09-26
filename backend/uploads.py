import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from limiter import limiter
from models import Upload, User

router = APIRouter(tags=["uploads"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# maps file signature (magic bytes) -> (extension, content-type). Checked against
# the actual bytes, not the client-supplied Content-Type header, which is trivial
# to spoof (e.g. curl -F "file=@payload.exe;type=image/png").
SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": (".png", "image/png"),
    b"\xff\xd8\xff": (".jpg", "image/jpeg"),
}


def sniff(contents: bytes) -> tuple[str, str] | None:
    for magic, ext_type in SIGNATURES.items():
        if contents.startswith(magic):
            return ext_type
    if contents[:4] == b"RIFF" and contents[8:12] == b"WEBP":
        return (".webp", "image/webp")
    return None


@router.post("/api/upload")
@limiter.limit("60/minute")
async def upload_file(
    request: Request, file: UploadFile, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    # Oversized requests are refused by the Content-Length middleware in main.py before
    # the body is read at all; this read cap is the backstop for bodies with no length.
    contents = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 5MB)")

    sniffed = sniff(contents)
    if sniffed is None:
        raise HTTPException(status_code=400, detail="Unsupported file type (must be PNG, JPEG, or WEBP)")
    ext, content_type = sniffed

    name = f"{uuid.uuid4().hex}{ext}"
    db.add(Upload(name=name, content_type=content_type, data=contents, user_id=user.id))
    db.commit()

    return {"filename": name, "url": f"/uploads/{name}"}


@router.get("/uploads/{name}")
def serve_upload(name: str, db: Session = Depends(get_db)):
    upload = db.query(Upload).filter(Upload.name == name).first()
    if upload is None:
        raise HTTPException(status_code=404, detail="Not found")
    # names are unguessable uuids, so a year of caching is safe
    return Response(
        content=upload.data,
        media_type=upload.content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
