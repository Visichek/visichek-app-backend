"""Convert an uploaded Word/PDF/text file into BlockNote content blocks.

Supported inputs:

* ``.docx``        — via ``mammoth`` → semantic HTML → BlockNote blocks.
  Embedded images are uploaded through the blog media uploader (public
  URL) and emitted as ``image`` blocks.
* ``.pdf``         — via ``pdfplumber`` text extraction. PDFs carry no
  reliable semantic structure, so headings are *heuristic* (short,
  non-terminated, title/upper-case lines). A warning is returned so the
  admin knows to review before publishing.
* ``.md`` / ``.markdown`` — parsed via ``markdown-it-py`` → HTML → the
  same HTML→BlockNote pipeline as ``.docx``, so headings, bold/italic,
  lists, quotes, code, links and rules survive. Falls back to plain
  paragraphs (with a warning) if the parser is unavailable.
* ``.txt``         — genuinely unstructured: split into paragraph blocks
  on blank lines.

Heavy parsers (``mammoth``, ``pdfplumber``, ``bs4``, ``markdown-it-py``)
are imported lazily
inside the functions so the app boots even before they're ``pip``-installed;
a missing parser surfaces as a clean HTTP 422 instead of an import crash.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
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
    """Return one of ``docx`` | ``pdf`` | ``markdown`` | ``text`` | ``unsupported``."""

    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    if name.endswith(".docx") or ctype == _DOCX_MIME:
        return "docx"
    if name.endswith(".pdf") or ctype == "application/pdf":
        return "pdf"
    if name.endswith((".md", ".markdown")) or ctype in (
        "text/markdown",
        "text/x-markdown",
    ):
        return "markdown"
    if name.endswith(".txt") or ctype.startswith("text/"):
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
    if kind == "markdown":
        return await _convert_markdown(file_bytes)
    if kind == "text":
        return _convert_text(file_bytes), []
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Unsupported document type for conversion: "
            f"{content_type or filename!r}. Supported: .docx, .pdf, .md, .txt."
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
        # BlockNote's default heading schema only supports levels 1–3;
        # clamp deeper headings (h4–h6) so the block isn't rejected on load.
        level = min(_HEADING_TAGS[name], 3)
        blocks.append(
            _block(
                "heading",
                _inline_content(el),
                props={"level": level},
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
# Markdown → HTML → blocks
# ---------------------------------------------------------------------------


def _decode(file_bytes: bytes) -> str:
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("latin-1", errors="replace")


async def _convert_markdown(file_bytes: bytes) -> Tuple[List[Block], List[str]]:
    """Render Markdown to HTML, then reuse the HTML→BlockNote pipeline.

    Falls back to plain paragraphs (with a warning) when ``markdown-it-py``
    is unavailable, so import never hard-fails on the parser being missing.
    """
    text = _decode(file_bytes)
    try:
        from markdown_it import MarkdownIt  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dep
        logger.warning("markdown-it-py import failed: %s", exc)
        return (
            _text_to_blocks(text, detect_headings=False),
            [
                "Markdown parser unavailable; imported as plain text. "
                "Install markdown-it-py to preserve headings and formatting."
            ],
        )

    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    html = md.render(text)
    warnings: List[str] = []
    blocks = await _html_to_blocks(html, warnings)
    if not blocks:
        warnings.append("Document appeared empty after conversion.")
    return blocks, warnings


# ---------------------------------------------------------------------------
# PDF → blocks (heuristic structure)
# ---------------------------------------------------------------------------


# Undecodable glyph artifacts pdfplumber emits for unmapped chars, e.g.
# bullets that come out as "(cid:127)".
_CID_RE = re.compile(r"\(cid:\d+\)")
# A leading bullet glyph (or an undecodable cid standing in for one).
_PDF_BULLET_RE = re.compile(r"^\s*(?:\(cid:\d+\)|[•·▪◦‣⁃○●▸▹–—\-\*])\s+")
# A leading "1." / "2)" ordered-list marker.
_PDF_NUM_RE = re.compile(r"^\s*\d{1,3}[.)]\s+")
# A heading is a line whose font is at least this much larger than body text.
_PDF_HEADING_FACTOR = 1.3
# A vertical gap larger than this multiple of the body line height ends a
# paragraph.
_PDF_PARA_GAP_FACTOR = 1.4


def _strip_inline_cid(text: str) -> str:
    return _CID_RE.sub("", text).strip()


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
        "PDF carries no semantic structure; layout was reconstructed "
        "heuristically from font sizes and spacing. Please review the "
        "imported content before publishing."
    ]

    # Per-page list of {text, top, size}; size_counter weights font sizes by
    # character count so the most common size is the body text size.
    page_lines: List[List[Dict[str, Any]]] = []
    size_counter: Counter = Counter()
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                try:
                    raw_lines = page.extract_text_lines()
                except Exception:  # pragma: no cover - layout fallback
                    raw_lines = []
                norm: List[Dict[str, Any]] = []
                for ln in raw_lines:
                    chars = ln.get("chars") or []
                    sizes = [c.get("size") for c in chars if c.get("size")]
                    for s in sizes:
                        size_counter[round(s)] += 1
                    norm.append(
                        {
                            "text": ln.get("text", ""),
                            "top": float(ln.get("top", 0.0)),
                            "size": max(sizes) if sizes else 0.0,
                        }
                    )
                page_lines.append(norm)
    except Exception as exc:
        logger.exception("pdfplumber extraction failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract PDF text: {exc}",
        )

    # No structured lines (older pdfplumber, or odd PDF): fall back to flat
    # text extraction so we degrade rather than fail.
    if not any(page_lines):
        return _convert_pdf_flat(file_bytes, warnings)

    blocks = _pdf_lines_to_blocks(page_lines, size_counter)
    if not blocks:
        warnings.append("No extractable text found (the PDF may be scanned images).")
    return blocks, warnings


def _pdf_lines_to_blocks(
    page_lines: List[List[Dict[str, Any]]], size_counter: Counter
) -> List[Block]:
    body_size = size_counter.most_common(1)[0][0] if size_counter else 0.0

    # Estimate body line height from gaps between consecutive body-size lines
    # (heading / list gaps would otherwise inflate the estimate).
    body_gaps: List[float] = []
    all_gaps: List[float] = []
    for lines in page_lines:
        for prev, cur in zip(lines, lines[1:]):
            gap = cur["top"] - prev["top"]
            if gap <= 0:
                continue
            all_gaps.append(gap)
            if (
                body_size
                and abs(prev["size"] - body_size) <= 1.0
                and abs(cur["size"] - body_size) <= 1.0
            ):
                body_gaps.append(gap)
    # The body line height is the *most common* gap (mode): within-paragraph
    # spacing dominates, while paragraph-break gaps are comparatively rare.
    # Median would be skewed by those break gaps on short documents.
    sample = body_gaps if body_gaps else all_gaps
    if sample:
        line_gap = float(Counter(round(g) for g in sample).most_common(1)[0][0])
    else:
        line_gap = 0.0
    para_break = line_gap * _PDF_PARA_GAP_FACTOR

    # Pass 1: classify every line. Heading levels are assigned after we know
    # the full set of heading font sizes.
    classified: List[Dict[str, Any]] = []
    heading_sizes: set[int] = set()
    for pidx, lines in enumerate(page_lines):
        for ln in lines:
            raw = ln["text"]
            text = _strip_inline_cid(raw)
            size = ln["size"]
            is_big = bool(body_size) and size >= body_size * _PDF_HEADING_FACTOR
            if is_big and text:
                kind, content = "heading", text
                heading_sizes.add(round(size))
            elif _PDF_BULLET_RE.match(raw):
                kind = "bullet"
                content = _strip_inline_cid(_PDF_BULLET_RE.sub("", raw, count=1))
            elif _PDF_NUM_RE.match(text):
                kind = "number"
                content = _PDF_NUM_RE.sub("", text, count=1).strip()
            elif text and _looks_like_heading(text):
                kind, content = "heading", text
            elif text:
                kind, content = "body", text
            else:
                continue
            classified.append(
                {
                    "kind": kind,
                    "text": content,
                    "size": size,
                    "top": ln["top"],
                    "page": pidx,
                }
            )

    size_levels = {
        s: min(i + 1, 3) for i, s in enumerate(sorted(heading_sizes, reverse=True))
    }

    # Pass 2: emit blocks, grouping consecutive body lines into paragraphs and
    # folding wrapped continuation lines back into the preceding list item.
    blocks: List[Block] = []
    para_buf: List[str] = []
    prev_top: Optional[float] = None
    prev_page: Optional[int] = None

    def flush() -> None:
        if para_buf:
            joined = " ".join(para_buf).strip()
            if joined:
                blocks.append(_block("paragraph", [_text_node(joined)]))
            para_buf.clear()

    for ln in classified:
        kind = ln["kind"]
        page_changed = prev_page is not None and ln["page"] != prev_page
        gap = (
            ln["top"] - prev_top if prev_top is not None and not page_changed else None
        )
        if kind == "body":
            wraps_list = (
                not para_buf
                and bool(blocks)
                and blocks[-1]["type"] in ("bulletListItem", "numberedListItem")
                and not page_changed
                and gap is not None
                and gap <= para_break
            )
            if wraps_list:
                blocks[-1]["content"][0]["text"] += " " + ln["text"]
            else:
                if page_changed or (
                    gap is not None and para_break and gap > para_break
                ):
                    flush()
                para_buf.append(ln["text"])
        else:
            flush()
            if kind == "heading":
                level = size_levels.get(round(ln["size"]), 2)
                blocks.append(
                    _block("heading", [_text_node(ln["text"])], props={"level": level})
                )
            elif kind == "bullet":
                blocks.append(_block("bulletListItem", [_text_node(ln["text"])]))
            else:  # number
                blocks.append(_block("numberedListItem", [_text_node(ln["text"])]))
        prev_top = ln["top"]
        prev_page = ln["page"]
    flush()
    return blocks


def _convert_pdf_flat(
    file_bytes: bytes, warnings: List[str]
) -> Tuple[List[Block], List[str]]:
    """Fallback: flat text extraction when line layout is unavailable."""
    import io

    import pdfplumber  # type: ignore

    pages_text: List[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                pages_text.append(_strip_inline_cid(page.extract_text() or ""))
    except Exception as exc:
        logger.exception("pdfplumber extraction failed")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to extract PDF text: {exc}",
        )

    blocks = _text_to_blocks("\n".join(pages_text), detect_headings=True)
    if not blocks:
        warnings.append("No extractable text found (the PDF may be scanned images).")
    return blocks, warnings


# ---------------------------------------------------------------------------
# Plain text → blocks
# ---------------------------------------------------------------------------


def _convert_text(file_bytes: bytes) -> List[Block]:
    return _text_to_blocks(_decode(file_bytes), detect_headings=False)


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
