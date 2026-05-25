"""Convert an uploaded Word/PDF/text file into BlockNote content blocks.

Supported inputs:

* ``.docx``        — via ``mammoth`` → semantic HTML → BlockNote blocks.
  Embedded images are uploaded through the blog media uploader (public
  URL) and emitted as ``image`` blocks.
* ``.pdf``         — via ``pdfplumber`` text extraction. PDFs carry no
  reliable semantic structure, so headings are *heuristic* (short,
  non-terminated, title/upper-case lines). A warning is returned so the
  admin knows to review before publishing.
* ``.txt`` / ``.md`` — split into paragraph blocks on blank lines.

Heavy parsers (``mammoth``, ``pdfplumber``, ``bs4``) are imported lazily
inside the functions so the app boots even before they're ``pip``-installed;
a missing parser surfaces as a clean HTTP 422 instead of an import crash.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

Block = Dict[str, Any]
Inline = Dict[str, Any]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


def _text_node(text: str, styles: Optional[Dict[str, bool]] = None) -> Inline:
    return {"type": "text", "text": text, "styles": styles or {}}


def _block(block_type: str, content: Any = None, props: Optional[dict] = None) -> Block:
    out: Block = {"id": _new_id(), "type": block_type}
    if props is not None:
        out["props"] = props
    if content is not None:
        out["content"] = content
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def detect_kind(filename: str, content_type: str) -> str:
    """Return one of ``docx`` | ``pdf`` | ``text`` | ``unsupported``."""
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    if name.endswith(".docx") or ctype == _DOCX_MIME:
        return "docx"
    if name.endswith(".pdf") or ctype == "application/pdf":
        return "pdf"
    if name.endswith((".txt", ".md", ".markdown")) or ctype.startswith("text/"):
        return "text"
    return "unsupported"


async def convert_upload_to_blocks(
    file_bytes: bytes, filename: str, content_type: str
) -> Tuple[List[Block], List[str]]:
    """Convert raw upload bytes into (blocks, warnings)."""
    kind = detect_kind(filename, content_type)
    if kind == "docx":
        return await _convert_docx(file_bytes)
    if kind == "pdf":
        return _convert_pdf(file_bytes)
    if kind == "text":
        return _convert_text(file_bytes), []
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Unsupported document type for conversion: "
            f"{content_type or filename!r}. Supported: .docx, .pdf, .txt/.md."
        ),
    )


# ---------------------------------------------------------------------------
# DOCX → HTML → blocks
# ---------------------------------------------------------------------------


async def _convert_docx(file_bytes: bytes) -> Tuple[List[Block], List[str]]:
    try:
        import io

        import mammoth  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on optional dep
        logger.warning("mammoth import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Word (.docx) conversion is unavailable (mammoth not installed).",
        )

    warnings: List[str] = []
    try:
        result = mammoth.convert_to_html(io.BytesIO(file_bytes))
        html = result.value or ""
        for msg in getattr(result, "messages", []) or []:
            warnings.append(str(getattr(msg, "message", msg)))
    except Exception as exc:
        logger.exception("mammoth conversion failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to convert Word document: {exc}",
        )

    blocks = await _html_to_blocks(html, warnings)
    if not blocks:
        warnings.append("Document appeared empty after conversion.")
    return blocks, warnings


async def _html_to_blocks(html: str, warnings: List[str]) -> List[Block]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        logger.warning("beautifulsoup4 import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="HTML parsing is unavailable (beautifulsoup4 not installed).",
        )

    soup = BeautifulSoup(html, "html.parser")
    root = soup.body or soup
    blocks: List[Block] = []
    for el in root.find_all(recursive=False):
        await _append_element_blocks(el, blocks, warnings)
    return blocks


async def _append_element_blocks(
    el: Any, blocks: List[Block], warnings: List[str]
) -> None:
    name = getattr(el, "name", None)
    if name is None:
        text = str(el).strip()
        if text:
            blocks.append(_block("paragraph", [_text_node(text)]))
        return

    if name in _HEADING_TAGS:
        blocks.append(
            _block(
                "heading",
                _inline_content(el),
                props={"level": _HEADING_TAGS[name]},
            )
        )
    elif name == "p":
        await _emit_paragraph_or_image(el, blocks, warnings)
    elif name in ("ul", "ol"):
        item_type = "numberedListItem" if name == "ol" else "bulletListItem"
        for li in el.find_all("li", recursive=False):
            blocks.append(_block(item_type, _inline_content(li)))
    elif name == "blockquote":
        blocks.append(_block("quote", _inline_content(el)))
    elif name in ("pre", "code"):
        blocks.append(_block("codeBlock", [_text_node(el.get_text())]))
    elif name == "hr":
        blocks.append(_block("divider"))
    elif name == "img":
        img = await _image_block(el, warnings)
        if img:
            blocks.append(img)
    elif name == "table":
        # Flatten tables into paragraphs; full BlockNote tables are out of
        # MVP scope for imported documents.
        warnings.append("A table was flattened to text during import.")
        text = el.get_text(separator=" ", strip=True)
        if text:
            blocks.append(_block("paragraph", [_text_node(text)]))
    else:
        # Unknown wrapper — descend into children.
        children = el.find_all(recursive=False)
        if children:
            for child in children:
                await _append_element_blocks(child, blocks, warnings)
        else:
            text = el.get_text(strip=True)
            if text:
                blocks.append(_block("paragraph", _inline_content(el)))


async def _emit_paragraph_or_image(
    el: Any, blocks: List[Block], warnings: List[str]
) -> None:
    imgs = el.find_all("img")
    if imgs:
        for img in imgs:
            img_block = await _image_block(img, warnings)
            if img_block:
                blocks.append(img_block)
    content = _inline_content(el)
    if content:
        blocks.append(_block("paragraph", content))


_STYLE_TAGS = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "u": "underline",
    "s": "strike",
    "strike": "strike",
    "del": "strike",
}


def _inline_content(el: Any, styles: Optional[Dict[str, bool]] = None) -> List[Inline]:
    """Walk an element's children into BlockNote inline content."""
    styles = dict(styles or {})
    out: List[Inline] = []
    for child in getattr(el, "children", []):
        cname = getattr(child, "name", None)
        if cname is None:
            text = str(child)
            if text:
                out.append(_text_node(text, styles))
            continue
        if cname == "br":
            out.append(_text_node("\n", styles))
        elif cname == "a":
            href = child.get("href") or ""
            link_content = _inline_content(child, styles)
            text_nodes = [n for n in link_content if n.get("type") == "text"]
            if href and text_nodes:
                out.append({"type": "link", "href": href, "content": text_nodes})
            else:
                out.extend(link_content)
        elif cname in _STYLE_TAGS:
            merged = dict(styles)
            merged[_STYLE_TAGS[cname]] = True
            out.extend(_inline_content(child, merged))
        elif cname == "img":
            # Images inside inline flow are handled at the block level.
            continue
        else:
            out.extend(_inline_content(child, styles))
    # Collapse adjacent identical-style text nodes for a tidier payload.
    return _merge_text_nodes(out)


def _merge_text_nodes(nodes: List[Inline]) -> List[Inline]:
    merged: List[Inline] = []
    for node in nodes:
        if (
            node.get("type") == "text"
            and merged
            and merged[-1].get("type") == "text"
            and merged[-1].get("styles") == node.get("styles")
        ):
            merged[-1]["text"] += node["text"]
        else:
            merged.append(node)
    # Drop empty trailing/leading whitespace-only text nodes.
    return [n for n in merged if n.get("type") != "text" or n.get("text")]


async def _image_block(img_el: Any, warnings: List[str]) -> Optional[Block]:
    src = img_el.get("src") or ""
    alt = img_el.get("alt") or ""
    if not src:
        return None
    if src.startswith("data:"):
        url = await _upload_data_uri(src, warnings)
        if not url:
            return None
    else:
        url = src
    return _block("image", props={"url": url, "caption": alt})


async def _upload_data_uri(data_uri: str, warnings: List[str]) -> Optional[str]:
    """Decode a ``data:`` image and upload it via the blog media uploader."""
    try:
        import base64

        match = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", data_uri, re.DOTALL)
        if not match:
            return None
        mime = match.group(1) or "image/png"
        is_b64 = bool(match.group(2))
        payload = match.group(3)
        raw = base64.b64decode(payload) if is_b64 else payload.encode("utf-8")
        ext = "." + (mime.split("/")[-1] or "png")
        from blog.services.r2_upload import upload_media_bytes

        return await upload_media_bytes(raw, f"legal-import{ext}", mime)
    except Exception as exc:
        logger.warning("Embedded image upload failed during import: %s", exc)
        warnings.append("An embedded image could not be uploaded and was skipped.")
        return None


# ---------------------------------------------------------------------------
# PDF → blocks (heuristic structure)
# ---------------------------------------------------------------------------


def _convert_pdf(file_bytes: bytes) -> Tuple[List[Block], List[str]]:
    try:
        import io

        import pdfplumber  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        logger.warning("pdfplumber import failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PDF conversion is unavailable (pdfplumber not installed).",
        )

    warnings = [
        "PDF has no semantic structure; headings were detected heuristically. "
        "Please review the imported content before publishing."
    ]
    pages_text: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                pages_text.append(page.extract_text() or "")
    except Exception as exc:
        logger.exception("pdfplumber extraction failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract PDF text: {exc}",
        )

    full_text = "\n".join(pages_text)
    blocks = _text_to_blocks(full_text, detect_headings=True)
    if not blocks:
        warnings.append("No extractable text found (the PDF may be scanned images).")
    return blocks, warnings


# ---------------------------------------------------------------------------
# Plain text → blocks
# ---------------------------------------------------------------------------


def _convert_text(file_bytes: bytes) -> List[Block]:
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")
    return _text_to_blocks(text, detect_headings=False)


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if stripped.endswith((".", ",", ";", ":")):
        return False
    words = stripped.split()
    if len(words) > 12:
        return False
    if stripped.isupper():
        return True
    # Title-ish: most words capitalised.
    caps = sum(1 for w in words if w[:1].isupper())
    return len(words) >= 1 and caps / len(words) >= 0.6


def _text_to_blocks(text: str, *, detect_headings: bool) -> List[Block]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Split into paragraphs on blank lines.
    chunks = re.split(r"\n\s*\n", text)
    blocks: List[Block] = []
    for chunk in chunks:
        lines = [ln for ln in chunk.split("\n")]
        joined = " ".join(ln.strip() for ln in lines).strip()
        if not joined:
            continue
        single_line = len([ln for ln in lines if ln.strip()]) == 1
        if detect_headings and single_line and _looks_like_heading(joined):
            blocks.append(_block("heading", [_text_node(joined)], props={"level": 2}))
        else:
            blocks.append(_block("paragraph", [_text_node(joined)]))
    return blocks
