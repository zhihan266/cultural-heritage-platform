from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

import pymupdf

from app.modules.rag.domain.models import RagDocument
from app.modules.rag.domain.parser import DocumentParser


logger = logging.getLogger(__name__)


class PdfParser(DocumentParser):
    """将 PDF 按页解析为统一的 RagDocument。"""

    def __init__(
        self,
        *,
        max_file_size_mb: int = 100,
        min_text_chars_for_ocr: int = 30,
        enable_ocr: bool = False,
        ocr_language: str = "chi_sim+eng",
        ocr_dpi: int = 300,
        tessdata_dir: Path | None = None,
        password: str | None = None,
    ) -> None:
        """
        Args:
            max_file_size_mb:
                允许解析的最大 PDF 大小，防止超大文件耗尽资源。

            min_text_chars_for_ocr:
                页面原生文本少于该数量，并且页面含图片时，
                才考虑启动 OCR。

            enable_ocr:
                是否允许扫描版 PDF 使用 OCR。
                默认关闭，避免没有配置 Tesseract 时直接报错。

            ocr_language:
                OCR 语言。简体中文和英文通常使用 chi_sim+eng。

            ocr_dpi:
                OCR 渲染分辨率。300 DPI 通常适合文档识别。

            tessdata_dir:
                Tesseract 语言数据目录，可选。

            password:
                加密 PDF 的密码，可选。
        """
        if max_file_size_mb <= 0:
            raise ValueError("max_file_size_mb 必须大于 0")

        if min_text_chars_for_ocr < 0:
            raise ValueError("min_text_chars_for_ocr 不能小于 0")

        if ocr_dpi <= 0:
            raise ValueError("ocr_dpi 必须大于 0")

        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.min_text_chars_for_ocr = min_text_chars_for_ocr

        self.enable_ocr = enable_ocr
        self.ocr_language = ocr_language
        self.ocr_dpi = ocr_dpi
        self.tessdata_dir = tessdata_dir

        self.password = password

    def parse(self, file_path: Path) -> list[RagDocument]:
        """解析 PDF，并按页面返回 RagDocument 列表。"""

        self._validate_file(file_path)

        file_size_bytes = file_path.stat().st_size
        parent_document_id = self._calculate_sha256(file_path)

        pdf_document: pymupdf.Document | None = None

        try:
            pdf_document = pymupdf.open(str(file_path))

            self._authenticate_if_needed(
                pdf_document=pdf_document,
                file_path=file_path,
            )

            if pdf_document.page_count == 0:
                raise ValueError(f"PDF 没有任何页面：{file_path}")

            pdf_metadata = pdf_document.metadata or {}
            total_pages = pdf_document.page_count

            documents: list[RagDocument] = []

            for page_index in range(total_pages):
                page_number = page_index + 1
                page = pdf_document.load_page(page_index)

                try:
                    (
                        content,
                        extraction_method,
                        block_count,
                        image_count,
                    ) = self._extract_page(page)

                except Exception as exc:
                    raise RuntimeError(
                        f"解析 PDF 第 {page_number} 页失败：{file_path.name}"
                    ) from exc

                if not content:
                    logger.warning(
                        "PDF 页面没有提取到有效文本，已跳过："
                        "file=%s, page=%s",
                        file_path,
                        page_number,
                    )
                    continue

                page_document_id = (
                    f"{parent_document_id}:page:{page_number}"
                )

                document = RagDocument(
                    document_id=page_document_id,
                    content=content,
                    source=file_path.name,
                    file_type=".pdf",
                    metadata={
                        # 原始文件信息
                        "file_path": str(file_path),
                        "file_size_bytes": file_size_bytes,
                        "parent_document_id": parent_document_id,

                        # 页码信息
                        "page_number": page_number,
                        "page_index": page_index,
                        "total_pages": total_pages,

                        # PDF 文档属性
                        "title": pdf_metadata.get("title") or "",
                        "author": pdf_metadata.get("author") or "",
                        "subject": pdf_metadata.get("subject") or "",
                        "keywords": pdf_metadata.get("keywords") or "",

                        # 页面属性
                        "page_width": round(page.rect.width, 2),
                        "page_height": round(page.rect.height, 2),
                        "rotation": page.rotation,

                        # 解析质量信息
                        "extraction_method": extraction_method,
                        "block_count": block_count,
                        "image_count": image_count,
                        "char_count": len(content),
                    },
                )

                documents.append(document)

            if not documents:
                message = (
                    f"PDF 中没有提取到可用文本：{file_path.name}。"
                )

                if not self.enable_ocr:
                    message += (
                        "该文件可能是扫描版 PDF，"
                        "可以安装并启用 OCR 后重新解析。"
                    )

                raise ValueError(message)

            return documents

        except (
            pymupdf.EmptyFileError,
            pymupdf.FileDataError,
        ) as exc:
            raise ValueError(
                f"PDF 文件为空、损坏或格式无效：{file_path}"
            ) from exc

        finally:
            if pdf_document is not None:
                pdf_document.close()

    def _validate_file(self, file_path: Path) -> None:
        """在调用 PDF 库前进行基础文件校验。"""

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在：{file_path}")

        if not file_path.is_file():
            raise ValueError(f"传入路径不是文件：{file_path}")

        if file_path.suffix.lower() != ".pdf":
            raise ValueError(
                f"文件扩展名不是 PDF：{file_path.suffix}"
            )

        file_size = file_path.stat().st_size

        if file_size == 0:
            raise ValueError(f"PDF 文件为空：{file_path}")

        if file_size > self.max_file_size_bytes:
            max_mb = self.max_file_size_bytes // (1024 * 1024)

            raise ValueError(
                f"PDF 文件超过大小限制："
                f"{file_size / 1024 / 1024:.2f} MB，"
                f"当前最大允许 {max_mb} MB"
            )

        # 不能只检查 .pdf 后缀，因为普通 TXT 改名后也可能叫 .pdf。
        with file_path.open("rb") as file:
            header = file.read(1024)

        if b"%PDF-" not in header:
            raise ValueError(
                f"文件内容不是有效的 PDF 格式：{file_path}"
            )

    def _authenticate_if_needed(
        self,
        *,
        pdf_document: pymupdf.Document,
        file_path: Path,
    ) -> None:
        """处理需要密码的 PDF。"""

        if not pdf_document.needs_pass:
            return

        if not self.password:
            raise PermissionError(
                f"PDF 已加密，需要提供密码：{file_path.name}"
            )

        authentication_result = pdf_document.authenticate(
            self.password
        )

        if authentication_result <= 0:
            raise PermissionError(
                f"PDF 密码错误：{file_path.name}"
            )

    def _extract_page(
        self,
        page: pymupdf.Page,
    ) -> tuple[str, str, int, int]:
        """
        提取单页文本。

        Returns:
            content:
                清理后的页面文本。

            extraction_method:
                native 或 ocr。

            block_count:
                有效文本块数量。

            image_count:
                页面图片数量。
        """

        image_count = len(page.get_images(full=True))

        blocks = self._get_text_blocks(page)
        content = self._join_blocks(blocks)

        extraction_method = "native"

        visible_char_count = len(
            re.sub(r"\s+", "", content)
        )

        should_use_ocr = (
            self.enable_ocr
            and image_count > 0
            and visible_char_count < self.min_text_chars_for_ocr
        )

        if should_use_ocr:
            blocks = self._get_ocr_blocks(page)
            ocr_content = self._join_blocks(blocks)

            # OCR 有可能反而没有结果，因此只在有内容时替换。
            if ocr_content:
                content = ocr_content
                extraction_method = "ocr"

        return (
            content,
            extraction_method,
            len(blocks),
            image_count,
        )

    def _get_text_blocks(
        self,
        page: pymupdf.Page,
    ) -> list[str]:
        """提取 PDF 自带文本层中的文本块。"""

        raw_blocks = page.get_text(
            "blocks",
            sort=True,
        )

        return self._normalize_blocks(raw_blocks)

    def _get_ocr_blocks(
        self,
        page: pymupdf.Page,
    ) -> list[str]:
        """通过 Tesseract OCR 提取页面文本块。"""

        try:
            text_page = page.get_textpage_ocr(
                language=self.ocr_language,
                dpi=self.ocr_dpi,
                full=True,
                tessdata=(
                    str(self.tessdata_dir)
                    if self.tessdata_dir
                    else None
                ),
            )

            raw_blocks = page.get_text(
                "blocks",
                sort=True,
                textpage=text_page,
            )

            return self._normalize_blocks(raw_blocks)

        except RuntimeError as exc:
            raise RuntimeError(
                "PDF OCR 失败。请确认已安装 Tesseract，"
                f"并安装了语言包：{self.ocr_language}"
            ) from exc

    @staticmethod
    def _normalize_blocks(
        raw_blocks: list[tuple],
    ) -> list[str]:
        """
        过滤图片块并清理文本块。

        PyMuPDF 的 blocks 结果通常包含：
        x0, y0, x1, y1, text, block_number, block_type
        """

        normalized_blocks: list[str] = []

        for block in raw_blocks:
            if len(block) < 7:
                continue

            block_text = block[4]
            block_type = block[6]

            # block_type == 0 表示文本块；
            # 图片块不在这里直接处理。
            if block_type != 0:
                continue

            cleaned_text = PdfParser._clean_block_text(
                str(block_text)
            )

            if cleaned_text:
                normalized_blocks.append(cleaned_text)

        return normalized_blocks

    @staticmethod
    def _clean_block_text(text: str) -> str:
        """
        进行保守清洗。

        这里只清除明显的空白问题，不在 Parser 中修改语义。
        页眉页脚、重复文本、断词等交给后续 Cleaner。
        """

        text = text.replace("\r\n", "\n")
        text = text.replace("\r", "\n")

        cleaned_lines: list[str] = []

        for line in text.split("\n"):
            # 将连续空格、Tab 统一成一个空格。
            line = re.sub(r"[ \t]+", " ", line).strip()

            if line:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    @staticmethod
    def _join_blocks(blocks: list[str]) -> str:
        """使用空行保留不同文本块之间的边界。"""

        return "\n\n".join(blocks).strip()

    @staticmethod
    def _calculate_sha256(file_path: Path) -> str:
        """
        流式计算文件哈希。

        不一次性读取整个 PDF，避免大文件占用大量内存。
        """

        digest = hashlib.sha256()

        with file_path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                digest.update(chunk)

        return digest.hexdigest()
