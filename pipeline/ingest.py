#!/usr/bin/env python3
"""ai-brief: ingest stage.

Raw sources -> normalized documents/chunks JSONL.
"""
from __future__ import annotations

import argparse
import csv
import os
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

try:
    import ftfy  # type: ignore
except Exception:  # noqa: BLE001
    ftfy = None  # type: ignore

from common import env_path, relative_display_path, sha256_text, token_count, write_jsonl

SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".markdown", ".mdx", ".txt", ".text", ".note", ".notes",
    ".srt", ".vtt", ".jsonl", ".csv", ".html", ".htm",
}

SOURCE_TYPES = {
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".txt": "text",
    ".text": "text",
    ".note": "notes",
    ".notes": "notes",
    ".srt": "transcript",
    ".vtt": "transcript",
    ".jsonl": "jsonl",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
}


@dataclass(frozen=True)
class ParsedDocument:
    path: Path
    source_type: str
    title: str
    text: str
    metadata: dict[str, object]


def warn(message: str) -> None:
    print(message, file=sys.stderr)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if ftfy is not None:
        text = ftfy.fix_text(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def title_from_text(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", stripped)
        if heading:
            return heading.group(1).strip()[:160]
        if len(stripped) <= 160:
            return stripped.strip("#*` ") or path.stem
        break
    return path.stem


def parse_pdf(path: Path) -> tuple[str, dict[str, object]]:
    errors: list[str] = []
    try:
        import fitz  # type: ignore

        parts: list[str] = []
        page_count = 0
        with fitz.open(path) as doc:  # type: ignore[attr-defined]
            page_count = len(doc)
            for page in doc:
                parts.append(page.get_text("text"))
        return "\n\n".join(parts), {"parser": "pymupdf", "pages": page_count}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pymupdf: {exc}")

    try:
        import pdfplumber  # type: ignore

        parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
            page_count = len(pdf.pages)
        return "\n\n".join(parts), {"parser": "pdfplumber", "pages": page_count}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pdfplumber: {exc}")

    raise RuntimeError("; ".join(errors) or "no PDF parser available")


def parse_html(path: Path) -> tuple[str, dict[str, object]]:
    raw = read_text_file(path)
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        text = soup.get_text("\n")
        return text, {"parser": "beautifulsoup4", **({"html_title": title} if title else {})}
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", raw)
        return text, {"parser": "html-regex-fallback"}


def parse_srt(path: Path) -> tuple[str, dict[str, object]]:
    raw = read_text_file(path)
    try:
        import srt  # type: ignore

        subtitles = list(srt.parse(raw))
        return "\n".join(sub.content.replace("\n", " ").strip() for sub in subtitles if sub.content.strip()), {
            "parser": "srt", "items": len(subtitles)
        }
    except Exception:  # noqa: BLE001
        lines = []
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped or stripped.isdigit() or "-->" in stripped:
                continue
            lines.append(stripped)
        return "\n".join(lines), {"parser": "srt-inline-fallback"}


def parse_vtt(path: Path) -> tuple[str, dict[str, object]]:
    raw = read_text_file(path)
    lines: list[str] = []
    skip_note = False
    for line in raw.splitlines():
        stripped = line.strip("\ufeff ")
        if not stripped:
            skip_note = False
            continue
        upper = stripped.upper()
        if upper.startswith(("WEBVTT", "STYLE", "REGION")):
            continue
        if upper.startswith("NOTE"):
            skip_note = True
            continue
        if skip_note:
            continue
        if "-->" in stripped:
            continue
        if re.match(r"^[A-Za-z0-9_-]+$", stripped) and not lines:
            continue
        stripped = re.sub(r"<[^>]+>", "", stripped)
        if stripped:
            lines.append(stripped)
    return "\n".join(lines), {"parser": "vtt-inline"}


def parse_csv(path: Path) -> tuple[str, dict[str, object]]:
    raw = read_text_file(path)
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
    rows: list[str] = []
    row_count = 0
    if reader.fieldnames:
        for row in reader:
            row_count += 1
            fields = [f"{k}: {v}" for k, v in row.items() if k and v not in (None, "")]
            if fields:
                rows.append("; ".join(fields))
    else:
        plain = csv.reader(io.StringIO(raw), dialect=dialect)
        for row in plain:
            row_count += 1
            rows.append("; ".join(cell for cell in row if cell))
    return "\n".join(rows), {"parser": "csv", "rows": row_count}


def parse_jsonl(path: Path) -> tuple[str, dict[str, object]]:
    lines: list[str] = []
    count = 0
    for line_no, line in enumerate(read_text_file(path).splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSONL line {line_no}: {exc}") from exc
        count += 1
        if isinstance(obj, dict):
            pieces: list[str] = []
            for key in ("title", "heading", "summary", "text", "content", "body", "note"):
                value = obj.get(key)
                if isinstance(value, str) and value.strip():
                    pieces.append(value.strip())
            lines.append("\n".join(pieces) if pieces else json.dumps(obj, ensure_ascii=False, sort_keys=True))
        else:
            lines.append(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    return "\n\n".join(lines), {"parser": "jsonl", "records": count}


def parse_markdown(path: Path) -> tuple[str, dict[str, object]]:
    raw = read_text_file(path)
    # Strip a leading YAML frontmatter block (common in .md/.mdx), but keep the
    # human-meaningful title/description as leading text — good retrieval signal,
    # without the structural noise (isPublished, orderBy, thumbnail, ...).
    fm = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", raw, flags=re.DOTALL)
    if fm:
        carried = []
        for key in ("title", "description"):
            m = re.search(rf"^{key}:\s*(.+?)\s*$", fm.group(1), flags=re.MULTILINE)
            if m:
                carried.append(m.group(1).strip().strip("'\""))
        raw = (". ".join(carried) + "\n\n" if carried else "") + raw[fm.end():]
    # MDX ships JSX component tags (Capitalized by convention: <Note>, <Tabs>,
    # <Accordion>). Strip only those. Deliberately NOT lowercase <...>: docs use
    # <package>, <owner>, <name> as CLI placeholders — real content to preserve —
    # and a permissive lowercase match also eats prose like "a<b and c>d".
    raw = re.sub(r"</?[A-Z][A-Za-z0-9.]*(?:\s[^>]*?)?/?>", " ", raw)
    # Keep this deterministic and dependency-light: remove structural markup
    # while preserving the words authors wrote.
    raw = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
    raw = re.sub(r"`([^`]+)`", r"\1", raw)
    raw = re.sub(r"^\s{0,3}#{1,6}\s+", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", raw)
    raw = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", raw)
    raw = re.sub(r"^\s{0,3}[-*+]\s+", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"^\s{0,3}>\s?", "", raw, flags=re.MULTILINE)
    raw = raw.replace("**", "").replace("__", "").replace("*", "")
    return raw, {"parser": "markdown-inline"}


def parse_plain(path: Path) -> tuple[str, dict[str, object]]:
    return read_text_file(path), {"parser": "text"}


def parser_for(path: Path) -> Callable[[Path], tuple[str, dict[str, object]]] | None:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return parse_pdf
    if ext in {".html", ".htm"}:
        return parse_html
    if ext == ".srt":
        return parse_srt
    if ext == ".vtt":
        return parse_vtt
    if ext == ".csv":
        return parse_csv
    if ext == ".jsonl":
        return parse_jsonl
    if ext in {".md", ".markdown", ".mdx"}:
        return parse_markdown
    if ext in {".txt", ".text", ".note", ".notes"}:
        return parse_plain
    return None


def discover_inputs(paths: list[str], sources_dir: Path) -> tuple[list[Path], list[Path]]:
    roots = [Path(p).expanduser() for p in paths] if paths else [sources_dir]
    files: list[Path] = []
    missing: list[Path] = []
    for root in roots:
        if not root.exists():
            missing.append(root)
            continue
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files.extend(p for p in root.rglob("*") if p.is_file())
        else:
            warn(f"skipped: {root}: not a regular file or directory")
    files = sorted({p.resolve() for p in files}, key=lambda p: p.as_posix().lower())
    return files, missing


def parse_document(path: Path, sources_dir: Path) -> ParsedDocument | None:
    parser = parser_for(path)
    if parser is None:
        warn(f"skipped: {relative_display_path(path, sources_dir)}: unsupported file type")
        return None
    try:
        text, metadata = parser(path)
        normalized = normalize_text(text)
        if not normalized:
            warn(f"skipped: {relative_display_path(path, sources_dir)}: no extractable text")
            return None
        source_type = SOURCE_TYPES.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "unknown")
        metadata = {**metadata, "extension": path.suffix.lower()}
        return ParsedDocument(path=path, source_type=source_type, title=title_from_text(path, normalized), text=normalized, metadata=metadata)
    except Exception as exc:  # noqa: BLE001
        warn(f"skipped: {relative_display_path(path, sources_dir)}: {exc}")
        return None


def fallback_text_segments(text: str) -> list[tuple[int, int, str]]:
    """Deterministic emergency chunker used only in explicit dev/sandbox mode."""
    segments: list[tuple[int, int, str]] = []
    paragraph_re = re.compile(r"\S.*?(?=\n\s*\n|\Z)", re.DOTALL)
    for para in paragraph_re.finditer(text):
        raw = para.group(0).strip()
        if not raw:
            continue
        para_start = para.start() + (len(para.group(0)) - len(para.group(0).lstrip()))
        para_end = para_start + len(raw)
        if token_count(raw) <= 120:
            segments.append((para_start, para_end, re.sub(r"\s+", " ", raw).strip()))
            continue
        for sent_match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", raw):
            sent = sent_match.group(0).strip()
            if not sent:
                continue
            start = para_start + sent_match.start() + (len(sent_match.group(0)) - len(sent_match.group(0).lstrip()))
            end = start + len(sent)
            if token_count(sent) <= 120:
                segments.append((start, end, re.sub(r"\s+", " ", sent).strip()))
            else:
                words = list(re.finditer(r"\S+", sent))
                for i in range(0, len(words), 90):
                    part_words = words[i:i + 90]
                    start = para_start + sent_match.start() + part_words[0].start()
                    end = para_start + sent_match.start() + part_words[-1].end()
                    part = " ".join(w.group(0) for w in part_words)
                    segments.append((start, end, part))
    if not segments and text.strip():
        stripped = text.strip()
        start = text.index(stripped[0])
        segments.append((start, start + len(stripped), re.sub(r"\s+", " ", stripped)))
    return segments


def infer_offsets(text: str, chunks: list[str]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks:
        needle = chunk.strip()
        if not needle:
            offsets.append((cursor, cursor))
            continue
        idx = text.find(needle, cursor)
        if idx < 0:
            compact_needle = re.sub(r"\s+", " ", needle)
            compact_text = re.sub(r"\s+", " ", text[cursor:])
            raise RuntimeError(f"semchunk returned a chunk whose offset could not be inferred: {compact_needle[:120]!r} in {compact_text[:120]!r}")
        end = idx + len(needle)
        offsets.append((idx, end))
        cursor = end
    return offsets


def normalize_segment(text: str, start: int, end: int, fallback_text: str | None = None) -> tuple[int, int, str]:
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw.rstrip())
    start += leading
    end = start + max(0, trailing - leading)
    raw = text[start:end] if start <= end else ""
    cleaned = normalize_text(raw)
    if not cleaned and fallback_text:
        cleaned = normalize_text(fallback_text)
    return start, end, cleaned


def semchunk_text_segments(text: str, chunk_tokens: int) -> list[tuple[int, int, str]]:
    try:
        import semchunk  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "semchunk is required for ingest chunking by default. Activate the composed Flox environment with semchunk available. "
            "For sandbox-only smoke tests, set AI_BRIEF_ALLOW_CHUNK_FALLBACK=1 or pass --dev-fallback-chunker."
        ) from exc

    try:
        chunker = semchunk.chunkerify(token_count, chunk_tokens)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"failed to construct semchunk chunker: {exc}") from exc

    chunk_texts: list[str]
    offsets: list[tuple[int, int]]
    try:
        result = chunker(text, offsets=True)
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], list)
            and isinstance(result[1], list)
        ):
            chunk_texts = [str(item) for item in result[0]]
            offsets = [(int(start), int(end)) for start, end in result[1]]
        else:
            raise RuntimeError(f"unexpected semchunk offsets result: {type(result).__name__}")
    except TypeError:
        raw_chunks = chunker(text)
        if not isinstance(raw_chunks, list):
            raise RuntimeError(f"unexpected semchunk result: {type(raw_chunks).__name__}")
        chunk_texts = [str(item) for item in raw_chunks]
        offsets = infer_offsets(text, chunk_texts)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"semchunk failed while splitting text: {exc}") from exc

    if len(chunk_texts) != len(offsets):
        raise RuntimeError(f"semchunk returned {len(chunk_texts)} chunks but {len(offsets)} offsets")

    segments: list[tuple[int, int, str]] = []
    for chunk_text, (start, end) in zip(chunk_texts, offsets, strict=True):
        seg_start, seg_end, cleaned = normalize_segment(text, start, end, chunk_text)
        if cleaned:
            segments.append((seg_start, seg_end, cleaned))
    if not segments and text.strip():
        stripped = text.strip()
        start = text.index(stripped[0])
        segments.append((start, start + len(stripped), normalize_text(stripped)))
    return segments


def deterministic_fallback_segments(text: str, target_tokens: int) -> list[tuple[int, int, str]]:
    if token_count(text) <= target_tokens and text.strip():
        stripped = text.strip()
        start = text.index(stripped[0])
        return [(start, start + len(stripped), normalize_text(stripped))]
    return fallback_text_segments(text)


def build_segments(text: str, target_tokens: int, max_tokens: int, allow_fallback: bool) -> tuple[list[tuple[int, int, str]], str]:
    try:
        segments = semchunk_text_segments(text, target_tokens)
        chunker_name = "semchunk"
    except RuntimeError:
        if not allow_fallback:
            raise
        warn("ingest: explicit development mode enabled: semchunk unavailable or failed; using deterministic fallback chunker")
        segments = deterministic_fallback_segments(text, target_tokens)
        chunker_name = "fallback-inline"

    oversized = [(start, end, seg, token_count(seg)) for start, end, seg in segments if token_count(seg) > max_tokens]
    if oversized and chunker_name == "semchunk":
        segments = semchunk_text_segments(text, max_tokens)
        chunker_name = "semchunk"
        oversized = [(start, end, seg, token_count(seg)) for start, end, seg in segments if token_count(seg) > max_tokens]
    if oversized and not allow_fallback:
        count = oversized[0][3]
        raise RuntimeError(f"chunker produced a {count}-token chunk above --max-tokens={max_tokens}")
    if oversized and allow_fallback:
        warn("ingest: explicit development mode enabled: chunker produced oversized chunks; using deterministic fallback chunker")
        segments = deterministic_fallback_segments(text, target_tokens)
        chunker_name = "fallback-inline"
    return segments, chunker_name


def chunk_document(
    doc: ParsedDocument,
    document_id: str,
    source_path: str,
    target_tokens: int,
    max_tokens: int,
    *,
    allow_fallback_chunker: bool,
) -> list[dict[str, object]]:
    segments, chunker_name = build_segments(doc.text, target_tokens, max_tokens, allow_fallback_chunker)
    chunks: list[dict[str, object]] = []
    for ordinal, (char_start, char_end, text) in enumerate(segments):
        if not text:
            continue
        chunk_id = "chunk_" + sha256_text(f"{document_id}\0{ordinal}\0{char_start}\0{char_end}\0{text}")[:16]
        chunks.append({
            "id": chunk_id,
            "document_id": document_id,
            "ordinal": ordinal,
            "text": text,
            "char_start": char_start,
            "char_end": char_end,
            "token_count": token_count(text),
            "source_path": source_path,
            "metadata": {"title": doc.title, "source_type": doc.source_type, "chunker": chunker_name},
        })
    return chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ingest", description="normalize raw sources into JSONL")
    parser.add_argument("paths", nargs="*", help="optional explicit source files or directories")
    parser.add_argument("--target-tokens", type=int, default=220, help="semchunk target chunk size in rough tokens")
    parser.add_argument("--max-tokens", type=int, default=300, help="hard chunk size in rough tokens")
    parser.add_argument(
        "--dev-fallback-chunker",
        action="store_true",
        help="explicit sandbox/dev mode: permit deterministic inline chunking if semchunk is unavailable",
    )
    args = parser.parse_args(argv)

    if args.target_tokens <= 0 or args.max_tokens <= 0 or args.target_tokens > args.max_tokens:
        print("error: require 0 < --target-tokens <= --max-tokens", file=sys.stderr)
        return 2

    sources_dir = env_path("SOURCES_DIR", "sources")
    work_dir = env_path("WORK_DIR", "work")
    files, missing = discover_inputs(args.paths, sources_dir)
    if missing and args.paths:
        for path in missing:
            warn(f"error: explicit input path does not exist: {path}")
        return 2
    for path in missing:
        warn(f"skipped: {path}: path does not exist")

    documents: list[dict[str, object]] = []
    chunks: list[dict[str, object]] = []
    seen_doc_ids: set[str] = set()

    for path in files:
        doc = parse_document(path, sources_dir)
        if doc is None:
            continue
        source_path = relative_display_path(path, Path.cwd())
        size = path.stat().st_size
        document_id = "doc_" + sha256_text(f"{source_path}\0{doc.source_type}\0{doc.text}")[:16]
        if document_id in seen_doc_ids:
            warn(f"skipped: {source_path}: duplicate document id {document_id}")
            continue
        seen_doc_ids.add(document_id)
        documents.append({
            "id": document_id,
            "source_path": source_path,
            "source_type": doc.source_type,
            "title": doc.title,
            "text": doc.text,
            "bytes": size,
            "metadata": doc.metadata,
        })
        try:
            new_chunks = chunk_document(
                doc,
                document_id,
                source_path,
                args.target_tokens,
                args.max_tokens,
                allow_fallback_chunker=args.dev_fallback_chunker or env_flag("AI_BRIEF_ALLOW_CHUNK_FALLBACK"),
            )
        except RuntimeError as exc:
            print(f"error: failed to chunk {source_path}: {exc}", file=sys.stderr)
            return 1
        chunks.extend(new_chunks)

    documents.sort(key=lambda rec: (str(rec["id"]), str(rec["source_path"])))
    chunks.sort(key=lambda rec: (str(rec["document_id"]), int(rec["ordinal"])))

    work_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(work_dir / "documents.jsonl", documents)
    write_jsonl(work_dir / "chunks.jsonl", chunks)

    print(f"ingest: wrote {len(documents)} document(s), {len(chunks)} chunk(s) to {work_dir}")
    if not documents:
        warn("ingest: no supported documents found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
