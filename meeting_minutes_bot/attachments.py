"""Download metadata validation and local text extraction for meeting attachments."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from docx import Document
from markdown_it import MarkdownIt
from PIL import Image, UnidentifiedImageError


IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
)
DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".markdown"})
ADMIN_CONFIG_SUFFIXES = frozenset({".yaml", ".yml"})
SUPPORTED_FILE_SUFFIXES = IMAGE_SUFFIXES | DOCUMENT_SUFFIXES
IMAGE_FORMATS = frozenset({"PNG", "JPEG", "WEBP", "BMP", "TIFF"})
MESSAGE_TYPES = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
}
MAX_IMAGE_PIXELS = 40_000_000
PREVIEW_LENGTH = 200
UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class AttachmentProcessingError(ValueError):
    """Raised when an inbound attachment cannot be accepted or extracted."""


class AttachmentConfigurationError(RuntimeError):
    """Raised when the local OCR backend cannot be initialized."""


class OcrEngine(Protocol):
    def recognize(self, path: Path) -> str:
        """Return plain text recognized from one local image."""


@dataclass(frozen=True, slots=True)
class AttachmentResource:
    type: str
    file_key: str
    file_name: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractedAttachment:
    file_name: str
    message_type: str
    raw_content: str
    parsed_content: str
    recognition_method: str
    source_path: Path | None = None
    has_embedded_media: bool = False

    @property
    def character_count(self) -> int:
        return len(self.parsed_content)

    @property
    def preview(self) -> str:
        if self.parsed_content:
            text = self.parsed_content[:PREVIEW_LENGTH]
            suffix = "……" if len(self.parsed_content) > PREVIEW_LENGTH else ""
            if self.has_embedded_media:
                return text + suffix + "（含表格/图片，生成纪要时原样嵌入）"
            return text + suffix
        if self.has_embedded_media:
            return "（含表格/图片，无文字摘要；生成纪要时原样嵌入）"
        return ""


def _value(source: object, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _resource_from_object(source: object) -> AttachmentResource | None:
    resource_type = str(
        _value(source, "type") or _value(source, "kind") or ""
    ).strip().lower()
    file_key = str(
        _value(source, "file_key") or _value(source, "image_key") or ""
    ).strip()
    file_name_value = _value(source, "file_name")
    file_name = _safe_display_name(str(file_name_value)) if file_name_value else None
    if resource_type not in {"image", "file"} or not file_key:
        return None
    return AttachmentResource(resource_type, file_key, file_name)


def _safe_display_name(value: str) -> str:
    basename = Path(value.replace("\\", "/")).name.strip().strip(".")
    cleaned = UNSAFE_FILENAME_CHARS.sub("_", basename).strip()
    return cleaned[:180] or "attachment"


def message_attachment_resource(message: object) -> AttachmentResource | None:
    """Return the single image/file resource carried by an inbound message."""

    resources = _value(message, "resources")
    found: list[AttachmentResource] = []
    if isinstance(resources, (list, tuple)):
        for item in resources:
            resource = _resource_from_object(item)
            if resource is not None:
                found.append(resource)

    if not found:
        content = _value(message, "content")
        resource = _resource_from_object(content) if content is not None else None
        if resource is not None:
            found.append(resource)

    if len(found) > 1:
        raise AttachmentProcessingError("每条消息只支持一个图片或文件附件，请分开发送。")
    return found[0] if found else None


def validate_resource_type(
    resource: AttachmentResource,
    *,
    allow_admin_config: bool = False,
) -> str:
    """Validate metadata before downloading and return the intended content type."""

    if resource.type == "image":
        return "image"
    if not resource.file_name:
        raise AttachmentProcessingError("附件缺少文件名，无法判断文件格式。")
    suffix = Path(resource.file_name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in ADMIN_CONFIG_SUFFIXES:
        if not allow_admin_config:
            allowed = "、".join(sorted(SUPPORTED_FILE_SUFFIXES))
            raise AttachmentProcessingError(f"不支持该附件格式，仅支持：{allowed}")
        return "yaml"
    message_type = MESSAGE_TYPES.get(suffix)
    if message_type is None:
        allowed = "、".join(sorted(SUPPORTED_FILE_SUFFIXES))
        if allow_admin_config:
            allowed = "、".join(
                sorted(SUPPORTED_FILE_SUFFIXES | ADMIN_CONFIG_SUFFIXES)
            )
        raise AttachmentProcessingError(f"不支持该附件格式，仅支持：{allowed}")
    return message_type


def _normalize_text(text: str) -> str:
    normalized = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


class LocalRapidOcrEngine:
    """Thin adapter around RapidOCR with compatibility for current result shapes."""

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        except Exception as exc:
            raise AttachmentConfigurationError(f"本地 OCR 初始化失败：{exc}") from exc

    def recognize(self, path: Path) -> str:
        try:
            result = self._engine(str(path))
        except Exception as exc:
            raise AttachmentProcessingError(f"图片 OCR 失败：{exc}") from exc

        texts = getattr(result, "txts", None)
        if texts is not None:
            return _normalize_text("\n".join(str(text) for text in texts if text))

        payload = result[0] if isinstance(result, tuple) and result else result
        if not payload:
            return ""
        extracted: list[str] = []
        for item in payload:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                extracted.append(str(item[1]))
        return _normalize_text("\n".join(extracted))


class _PlainTextHtmlParser(HTMLParser):
    BLOCK_TAGS = frozenset(
        {"address", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "p", "pre"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def _newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK_TAGS:
            self._newline()
        elif tag == "li":
            self._newline()
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS or tag == "li":
            self._newline()

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return _normalize_text("".join(self.parts))


def _decode_markdown(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AttachmentProcessingError("Markdown 文件编码无法识别，请使用 UTF-8 编码。")


def _extract_markdown(path: Path) -> tuple[str, str]:
    raw = _decode_markdown(path)
    parser = _PlainTextHtmlParser()
    parser.feed(MarkdownIt("commonmark").render(raw))
    return raw, parser.text()


def _docx_has_embedded_media(document: Document) -> bool:
    """True when the DOCX body contains tables or inline/related images."""

    if document.tables:
        return True
    if document.inline_shapes:
        return True
    for rel in document.part.rels.values():
        reltype = str(getattr(rel, "reltype", "")).lower()
        target = str(getattr(rel, "target_ref", "")).lower()
        if "image" in reltype or target.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".emf", ".wmf")
        ):
            return True
    return False


def _extract_docx(path: Path) -> tuple[str, bool]:
    """Return (text_summary, has_embedded_media) for a Word attachment."""

    try:
        document = Document(path)
    except Exception as exc:
        raise AttachmentProcessingError(f"Word 文件无法打开或已损坏：{exc}") from exc

    blocks: list[str] = []
    for block in document.iter_inner_content():
        if hasattr(block, "rows"):
            for row in block.rows:
                cells = [_normalize_text(cell.text) for cell in row.cells]
                if any(cells):
                    blocks.append(" | ".join(cells))
        else:
            text = _normalize_text(block.text)
            if text:
                blocks.append(text)
    summary = _normalize_text("\n".join(blocks))
    return summary, _docx_has_embedded_media(document)


def _extract_pdf(path: Path, max_pages: int) -> str:
    try:
        import pymupdf

        document = pymupdf.open(path)
    except Exception as exc:
        raise AttachmentProcessingError(f"PDF 文件无法打开或已损坏：{exc}") from exc

    try:
        if not document.is_pdf:
            raise AttachmentProcessingError("附件扩展名为 PDF，但文件内容不是有效 PDF。")
        if document.needs_pass:
            raise AttachmentProcessingError("不支持加密或需要密码的 PDF 文件。")
        if document.page_count == 0:
            raise AttachmentProcessingError("PDF 文件没有可读取的页面。")
        if document.page_count > max_pages:
            raise AttachmentProcessingError(
                f"PDF 页数超过限制：{document.page_count} 页，最多 {max_pages} 页。"
            )
        pages: list[str] = []
        for index, page in enumerate(document):
            text = _normalize_text(page.get_text("text", sort=True))
            if not text:
                raise AttachmentProcessingError(
                    f"PDF 第 {index + 1} 页没有文字层；不支持扫描版或图片型 PDF。"
                )
            pages.append(text)
        return _normalize_text("\n\n".join(pages))
    finally:
        document.close()


def _validate_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            if image_format not in IMAGE_FORMATS:
                raise AttachmentProcessingError("图片内容格式不受支持。")
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise AttachmentProcessingError(
                    f"图片像素尺寸超过限制，最多支持 {MAX_IMAGE_PIXELS:,} 像素。"
                )
            image.verify()
    except AttachmentProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise AttachmentProcessingError(f"图片无法打开或已损坏：{exc}") from exc


class AttachmentProcessor:
    """Extract supported cached attachments without blocking the event loop."""

    def __init__(
        self,
        *,
        ocr: OcrEngine,
        max_bytes: int,
        max_pdf_pages: int,
    ) -> None:
        self.ocr = ocr
        self.max_bytes = max_bytes
        self.max_pdf_pages = max_pdf_pages
        self._ocr_lock = asyncio.Lock()

    async def extract(
        self, path: str | Path, resource: AttachmentResource
    ) -> ExtractedAttachment:
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise AttachmentProcessingError("附件下载结果不存在，请重新发送。")
        size = resolved.stat().st_size
        if size <= 0:
            raise AttachmentProcessingError("附件内容为空。")
        if size > self.max_bytes:
            raise AttachmentProcessingError(
                f"附件大小超过限制，最多支持 {self.max_bytes // (1024 * 1024)} MB。"
            )

        intended_type = validate_resource_type(resource)
        file_name = _safe_display_name(resource.file_name or resolved.name)
        has_media = False
        source_path: Path | None = None
        if intended_type == "image":
            async with self._ocr_lock:
                parsed = await asyncio.to_thread(self._extract_image, resolved)
            raw = parsed
            method = "本地图片 OCR"
        elif intended_type == "pdf":
            parsed = await asyncio.to_thread(_extract_pdf, resolved, self.max_pdf_pages)
            raw = parsed
            method = "PDF 文字层提取"
        elif intended_type == "docx":
            parsed, has_media = await asyncio.to_thread(_extract_docx, resolved)
            raw = parsed
            method = "Word 正文/表格提取（图片原样保留）"
            source_path = resolved
        else:
            raw, parsed = await asyncio.to_thread(_extract_markdown, resolved)
            method = "Markdown 文本解析"

        parsed = _normalize_text(parsed)
        if not parsed and not has_media:
            raise AttachmentProcessingError("附件中没有识别到有效文字。")
        if not parsed and has_media:
            parsed = "（Word 含表格或图片，无文字摘要）"
            raw = parsed
        return ExtractedAttachment(
            file_name=file_name,
            message_type=intended_type,
            raw_content=raw,
            parsed_content=parsed,
            recognition_method=method,
            source_path=source_path,
            has_embedded_media=has_media,
        )

    def _extract_image(self, path: Path) -> str:
        _validate_image(path)
        return self.ocr.recognize(path)
