from __future__ import annotations
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from app.modules.rag.api.dependencies import get_indexing_service
from app.modules.rag.api.schemas import RagDocumentIndexResponse
from app.modules.rag.application.indexing_service import IndexingService

router = APIRouter(prefix="/admin/rag", tags=["admin-rag"])


@router.post("/documents", response_model=RagDocumentIndexResponse)
async def index_document(
    file: UploadFile = File(...), indexing_service: IndexingService = Depends(get_indexing_service)
) -> RagDocumentIndexResponse:
    """将管理员上传的 TXT / PDF / DOCX"""
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file has no filename"
        )
    safe_filename = Path(file.filename).name
    suffix = Path(file.filename).suffix.lower()
    allowed_suffixes = [".txt", ".pdf", ".docx"]
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Only .txt, .pdf and .docx are supported.",
        )
    with TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir) / safe_filename
        max_file_size = 20 * 1024 * 1024
        total_size = 0
        with temp_path.open("wb") as output:
            while True:
                data = await file.read(1024 * 1024)
                if not data:
                    break
                total_size += len(data)
                if total_size > max_file_size:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="File is too large. Maximum size is 20 MiB.",
                    )
                output.write(data)
        if total_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
            )
        document_id = await indexing_service.index_document(temp_path)
    return RagDocumentIndexResponse(filename=safe_filename, document_id=document_id)
