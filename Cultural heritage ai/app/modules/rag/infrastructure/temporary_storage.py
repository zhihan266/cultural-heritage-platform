"""Task-scoped temporary RAG file storage."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

"""
临时RAG根目录，目前直接保存到项目的data
"""
TEMPORARY_RAG_ROOT = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "temporary_rag"
)

# 当前允许作为用户临时 RAG 的文件格式。
ALLOWED_EXTENSIONS = {
    ".txt",
    ".pdf",
    ".docx",
}

# 第一版限制单文件 20 MiB。
MAX_FILE_SIZE = (
    20 * 1024 * 1024
)

# 这个方法仅执行保存文件的功能
async def save_task_document(
    *,
    task_id: str,
    file: UploadFile,
) -> Path:
    if not file.filename:
        raise ValueError(
            "Uploaded file has no filename"
        )

    # 防止 ../xxx.pdf 路径穿越。
    safe_filename = Path(
        file.filename
    ).name

    suffix = Path(
        safe_filename
    ).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(
            "Unsupported file type. "
            "Only .txt, .pdf and .docx "
            "are supported."
        )

    # 创建Task专属目录
    task_dir = (
            TEMPORARY_RAG_ROOT
            / task_id
    )

    task_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    file_path = (
            task_dir
            / safe_filename
    )

    # 流式保存

    total_size = 0

    try:
        with file_path.open("wb") as output:

            while True:
                data = await file.read(
                    1024*1024
                )

                if not data:
                    break

                total_size += len(data)

                if total_size > MAX_FILE_SIZE:
                    raise ValueError(
                        "Too much file size. "
                        "Max file size is 20mib"
                    )

                output.write(data)

        if total_size == 0:
             raise ValueError(
                 "Uploaded file is empty"
             )

        return file_path

    except Exception:
        # 保存到一半失败时，
        # 不留下残缺目录。
        shutil.rmtree(
            task_dir,
            ignore_errors=True,
        )

        raise

def delete_task_documents(
    task_id: str,
)-> None:
    task_dir = (
        TEMPORARY_RAG_ROOT
        / task_id
    )

    shutil.rmtree(
        task_dir,
        ignore_errors=True,
    )

def get_task_document_paths(
        task_id: str,
) -> list[Path]:
    task_dir = (
        TEMPORARY_RAG_ROOT
        / task_id
    )
    # Task 没有上传文件，或者目录不存在。
    if not task_dir.exists():
        return []

    # 只返回文件，不返回子目录。
    return sorted(
        path
        for path in task_dir.iterdir()
        if path.is_file()
    )
