#!/usr/bin/env python3
"""Resumable video/PDF evidence pipeline for template-bound lecture notes."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_ENVIRONMENTS = (
    "definition", "theorem", "lemma", "proposition", "corollary",
    "proof", "example", "exercise", "problem", "remark", "note",
)


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, payload: Any) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    atomic_text(
        path,
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
    )


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(
    command: list[str], *, cwd: Path | None = None, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def require(command: str) -> str:
    found = shutil.which(command)
    if not found:
        fail(f"missing executable {command!r}; install it before running this stage")
    return found


def validate_id(lecture_id: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}", lecture_id):
        fail("lecture id must match [a-zA-Z0-9][a-zA-Z0-9._-]{0,79}")
    return lecture_id


class Layout:
    def __init__(self, root: Path, lecture_id: str):
        self.root = root.resolve()
        self.lecture_id = validate_id(lecture_id)
        self.lecture = self.root / "lectures" / lecture_id
        self.source = self.lecture / "source.json"
        self.state = self.lecture / "processing-state.json"
        self.contract = self.lecture / "template-contract.json"
        self.baseline_stdout = self.lecture / "build-baseline.stdout.log"
        self.baseline_latex = self.lecture / "build-baseline.latex.log"
        self.final_stdout = self.lecture / "build-final.stdout.log"
        self.final_latex = self.lecture / "build-final.latex.log"
        self.raw = self.lecture / "transcript.raw.json"
        self.corrected = self.lecture / "transcript.corrected.json"
        self.corrections = self.lecture / "corrections.jsonl"
        self.frames = self.lecture / "frames"
        self.frames_index = self.lecture / "frames.json"
        self.report = self.lecture / "processing-report.json"
        self.videos = self.root / "data" / "videos"
        self.audio = self.root / "data" / "audio" / f"{lecture_id}.wav"
        self.dictionary = self.root / "references" / "dictionary.jsonl"
        self.dictionary_state = self.root / "references" / "dictionary.state.json"
        self.reference_text = self.root / "references" / "reference.cleaned.txt"

    def read_state(self) -> dict[str, Any]:
        return load_json(
            self.state,
            {"schema": 2, "lecture_id": self.lecture_id, "stages": {}},
        )

    def record(self, stage: str, payload: dict[str, Any], status: str = "completed") -> None:
        state = self.read_state()
        state.setdefault("stages", {})[stage] = {
            "status": status,
            "updated_at": now(),
            **payload,
        }
        atomic_json(self.state, state)

    def stage(self, name: str) -> dict[str, Any]:
        return self.read_state().get("stages", {}).get(name, {})

    def require_baseline(self) -> None:
        if self.stage("template_baseline").get("status") != "completed":
            fail("compile the untouched LaTeX template with the template-check stage first")


def strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in text.splitlines())


def find_main_tex(template_root: Path, explicit: str | None = None) -> Path:
    root = template_root.resolve()
    if explicit:
        candidate = (root / explicit).resolve()
        if not candidate.is_file() or root not in candidate.parents:
            fail(f"main TeX file is invalid or outside template root: {candidate}")
        return candidate
    candidates = []
    for path in root.glob("*.tex"):
        if re.search(r"\\documentclass(?:\[[^]]*\])?\{[^}]+\}", strip_comments(path.read_text(encoding="utf-8"))):
            candidates.append(path.resolve())
    if len(candidates) != 1:
        fail(f"expected exactly one top-level TeX entry, found {len(candidates)}; pass --main-tex")
    return candidates[0]


def resolve_tex_dependencies(main: Path, root: Path) -> list[Path]:
    pending = [main]
    resolved: list[Path] = []
    seen: set[Path] = set()
    while pending:
        path = pending.pop(0).resolve()
        if path in seen:
            continue
        if not path.is_file():
            fail(f"referenced TeX file does not exist: {path}")
        if path != main and root not in path.parents:
            fail(f"referenced TeX file leaves template root: {path}")
        seen.add(path)
        resolved.append(path)
        text = strip_comments(path.read_text(encoding="utf-8"))
        for reference in re.findall(r"\\(?:input|include)\s*\{([^}]+)\}", text):
            relative = Path(reference)
            if not relative.suffix:
                relative = relative.with_suffix(".tex")
            candidate = (root / relative).resolve()
            if not candidate.exists():
                candidate = (path.parent / relative).resolve()
            pending.append(candidate)
    return resolved


def infer_build_command(template_root: Path, override: str | None) -> list[str]:
    if override:
        return shlex.split(override)
    if (template_root / "Makefile").is_file():
        return ["make"]
    fail("cannot identify the template's original build command; pass --build-command")


def execute_full_build(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    output = []
    if command == ["make"]:
        makefile = root / "Makefile"
        makefile_text = makefile.read_text(encoding="utf-8", errors="ignore") if makefile.is_file() else ""
        clean_target = "distclean" if re.search(r"(?m)^distclean\s*:", makefile_text) else "clean"
        if re.search(rf"(?m)^{clean_target}\s*:", makefile_text):
            clean = subprocess.run(
                ["make", clean_target], cwd=root, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            output.append(clean.stdout)
            if clean.returncode != 0:
                return subprocess.CompletedProcess(command, clean.returncode, "".join(output), None)
    build_command = ["make", "-B"] if command == ["make"] else command
    completed = subprocess.run(
        build_command, cwd=root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    output.append(completed.stdout)
    return subprocess.CompletedProcess(command, completed.returncode, "".join(output), None)


def inspect_template(template_root: Path, main_tex: str | None, build_command: str | None) -> dict[str, Any]:
    root = template_root.resolve()
    if not root.is_dir():
        fail(f"LaTeX template root is not a directory: {root}")
    main = find_main_tex(root, main_tex)
    files = resolve_tex_dependencies(main, root)
    main_text = strip_comments(main.read_text(encoding="utf-8"))
    documentclass_match = re.search(r"\\documentclass(?:\[([^]]*)\])?\{([^}]+)\}", main_text)
    if not documentclass_match:
        fail("main TeX file has no documentclass")
    class_name = documentclass_match.group(2)
    class_file = root / f"{class_name}.cls"
    searchable = list(files)
    if class_file.is_file():
        searchable.append(class_file.resolve())
    combined = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in searchable)
    preamble = main_text.split(r"\begin{document}", 1)[0]
    environments = sorted(
        environment for environment in SEMANTIC_ENVIRONMENTS
        if re.search(rf"\\(?:begin|newenvironment|newtheorem)\{{{environment}\}}", combined)
        or re.search(rf"\{{{environment}\}}", combined)
    )
    includes = re.findall(r"\\(input|include)\s*\{([^}]+)\}", main_text)
    bibliography = re.findall(r"\\(?:addbibresource|bibliography)\s*(?:\[[^]]*\])?\{([^}]+)\}", main_text)
    sectioning = []
    for path in files:
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for command, title in re.findall(r"\\(chapter|section|subsection|subsubsection)\*?\{([^}]*)\}", text):
            sectioning.append({"file": str(path.relative_to(root)), "command": command, "title": title})
    numbering_commands = [
        line.strip() for line in combined.splitlines()
        if re.search(r"\\(?:setcounter|numberwithin|counterwithin|renewcommand\{\\the)", line)
    ][:100]
    label_values = re.findall(r"\\label\{([^}]+)\}", combined)
    label_prefixes = sorted(set(label.split(":", 1)[0] for label in label_values if ":" in label))
    title_metadata = {}
    for command in ("title", "subtitle", "author", "date", "version"):
        match = re.search(rf"\\{command}\{{([^}}]*)\}}", main_text)
        if match:
            title_metadata[command] = match.group(1)
    makefile = root / "Makefile"
    makefile_text = makefile.read_text(encoding="utf-8", errors="ignore") if makefile.is_file() else ""
    return {
        "schema": 1,
        "inspected_at": now(),
        "template_root": str(root),
        "main_tex": str(main.relative_to(root)),
        "documentclass": {"name": class_name, "options": documentclass_match.group(1) or ""},
        "build_command": infer_build_command(root, build_command),
        "compile_engine": "XeLaTeX" if "pdfxe" in makefile_text else "template-defined",
        "included_tex": [str(path.relative_to(root)) for path in files],
        "include_relations": [{"command": command, "target": target} for command, target in includes],
        "packages": sorted(set(re.findall(r"\\(?:usepackage|RequirePackage)(?:\[[^]]*\])?\{([^}]+)\}", combined))),
        "custom_commands": sorted(set(re.findall(r"\\(?:newcommand|renewcommand|NewDocumentCommand)\*?\{?\\([A-Za-z@]+)", combined))),
        "semantic_environments": environments,
        "labels": sorted(set(label_values)),
        "label_prefixes": label_prefixes,
        "sectioning": sectioning,
        "title_metadata": title_metadata,
        "numbering_commands": numbering_commands,
        "notation_files": [str(path.relative_to(root)) for path in files if "notation" in path.name.casefold()],
        "bibliography": bibliography,
        "has_index": bool(re.search(r"\\(?:makeindex|printindex)\b", combined)),
        "preamble_sha256": hashlib.sha256(preamble.encode("utf-8")).hexdigest(),
        "class_sha256": sha256(class_file) if class_file.is_file() else None,
        "file_sha256": {str(path.relative_to(root)): sha256(path) for path in searchable},
        "allowed_insertion_candidates": [target for command, target in includes if command in ("input", "include")],
    }


def template_check(
    layout: Layout,
    template_root: Path,
    main_tex: str | None,
    build_command: str | None,
) -> Path:
    contract = inspect_template(template_root, main_tex, build_command)
    root = Path(contract["template_root"])
    main = root / contract["main_tex"]
    layout.lecture.mkdir(parents=True, exist_ok=True)
    completed = execute_full_build(contract["build_command"], root)
    atomic_text(layout.baseline_stdout, completed.stdout)
    log = root / f"{main.stem}.log"
    if log.is_file():
        shutil.copy2(log, layout.baseline_latex)
    if completed.returncode != 0:
        layout.record(
            "template_baseline",
            {"returncode": completed.returncode, "log": str(layout.baseline_stdout)},
            status="failed",
        )
        fail(f"original template compilation failed; see {layout.baseline_stdout}")
    pdf = root / f"{main.stem}.pdf"
    if not pdf.is_file():
        fail(f"template command succeeded but did not create expected PDF: {pdf}")
    contract["baseline_pdf"] = str(pdf)
    contract["baseline_pdf_sha256"] = sha256(pdf)
    atomic_json(layout.contract, contract)
    layout.record(
        "template_baseline",
        {
            "template_root": str(root),
            "main_tex": contract["main_tex"],
            "contract_sha256": sha256(layout.contract),
            "pdf": str(pdf),
            "pdf_sha256": sha256(pdf),
        },
    )
    return pdf


def video_path(layout: Layout) -> Path:
    source = load_json(layout.source, {})
    relative = source.get("video_file")
    if not relative:
        fail(f"download metadata is missing: {layout.source}")
    path = layout.root / relative
    if not path.is_file():
        fail(f"downloaded video is missing: {path}")
    return path


def download(layout: Layout, url: str, force: bool) -> Path:
    layout.require_baseline()
    prior = load_json(layout.source, {})
    if prior and prior.get("original_url") != url:
        fail("this lecture id already belongs to another URL; choose a new id")
    if prior and not force:
        path = layout.root / prior.get("video_file", "")
        if path.is_file() and sha256(path) == prior.get("sha256"):
            print(f"skip download: verified {path}")
            return path
        if path.is_file():
            fail("downloaded file exists but its hash changed; inspect it and pass --force only to replace it")
    yt_dlp = require("yt-dlp")
    ffprobe = require("ffprobe")
    layout.videos.mkdir(parents=True, exist_ok=True)
    layout.lecture.mkdir(parents=True, exist_ok=True)
    output = str(layout.videos / f"{layout.lecture_id}.%(ext)s")
    run([
        yt_dlp, "--no-playlist", "--force-overwrites" if force else "--no-overwrites",
        "--write-info-json", "--output", output, url,
    ])
    candidates = sorted(
        path for path in layout.videos.glob(f"{layout.lecture_id}.*")
        if path.is_file() and not path.name.endswith((".info.json", ".part", ".ytdl"))
    )
    if len(candidates) != 1:
        fail(f"expected one downloaded video, found {len(candidates)}: {candidates}")
    path = candidates[0]
    sidecar = layout.videos / f"{layout.lecture_id}.info.json"
    yt_metadata = load_json(sidecar, {})
    if sidecar.exists():
        os.replace(sidecar, layout.lecture / "yt-dlp.info.json")
    probe = run([
        ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path),
    ], capture=True)
    duration = float(json.loads(probe.stdout).get("format", {}).get("duration") or 0)
    digest = sha256(path)
    source = {
        "schema": 1,
        "lecture_id": layout.lecture_id,
        "original_url": url,
        "webpage_url": yt_metadata.get("webpage_url") or url,
        "title": yt_metadata.get("title") or layout.lecture_id,
        "extractor": yt_metadata.get("extractor"),
        "duration_seconds": duration or yt_metadata.get("duration"),
        "downloaded_at": now(),
        "processing_status": "downloaded",
        "video_file": str(path.relative_to(layout.root)),
        "sha256": digest,
    }
    atomic_json(layout.source, source)
    layout.record("download", {"input_url": url, "output_sha256": digest, "duration_seconds": source["duration_seconds"]})
    return path


def extract_audio(layout: Layout, force: bool) -> Path:
    layout.require_baseline()
    video = video_path(layout)
    digest = sha256(video)
    prior = layout.stage("audio")
    if layout.audio.is_file() and prior.get("input_sha256") == digest and not force:
        print(f"skip audio: {layout.audio}")
        return layout.audio
    ffmpeg = require("ffmpeg")
    ffprobe = require("ffprobe")
    layout.audio.parent.mkdir(parents=True, exist_ok=True)
    temporary = layout.audio.with_suffix(".wav.tmp")
    run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(temporary),
    ])
    os.replace(temporary, layout.audio)
    probe = run([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels", "-of", "json", str(layout.audio),
    ], capture=True)
    streams = json.loads(probe.stdout).get("streams", [])
    if not streams or streams[0].get("sample_rate") != "16000" or streams[0].get("channels") != 1:
        fail("ffprobe verification failed: extracted audio is not 16 kHz mono")
    layout.record("audio", {"input_sha256": digest, "output_sha256": sha256(layout.audio), "sample_rate": 16000, "channels": 1})
    return layout.audio


def timestamp(value: float) -> str:
    milliseconds = max(0, round(float(value) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def write_raw_sidecars(layout: Layout, result: dict[str, Any]) -> None:
    segments = result.get("segments", [])
    plain = "".join(str(segment.get("text", "")) for segment in segments).strip() + "\n"
    srt: list[str] = []
    vtt = ["WEBVTT", ""]
    for index, segment in enumerate(segments, 1):
        start, end = float(segment["start"]), float(segment["end"])
        text = str(segment.get("text", "")).strip()
        srt.extend([str(index), f"{timestamp(start).replace('.', ',')} --> {timestamp(end).replace('.', ',')}", text, ""])
        vtt.extend([f"{timestamp(start)} --> {timestamp(end)}", text, ""])
    atomic_text(layout.lecture / "transcript.raw.txt", plain)
    atomic_text(layout.lecture / "transcript.raw.srt", "\n".join(srt))
    atomic_text(layout.lecture / "transcript.raw.vtt", "\n".join(vtt))


def transcribe(layout: Layout, model: str, language: str) -> Path:
    layout.require_baseline()
    if not layout.audio.is_file():
        fail("audio is missing; run the audio stage first")
    audio_hash = sha256(layout.audio)
    parameters = {"model": model, "language": language, "word_timestamps": True}
    prior = layout.stage("transcribe")
    if layout.raw.exists():
        if (
            prior.get("input_sha256") == audio_hash
            and prior.get("parameters") == parameters
            and prior.get("output_sha256") == sha256(layout.raw)
        ):
            write_raw_sidecars(layout, load_json(layout.raw))
            print(f"skip immutable raw transcription: {layout.raw}")
            return layout.raw
        fail("transcript.raw.json is immutable; use a new lecture id for different audio or parameters")
    try:
        import mlx_whisper  # type: ignore
    except ImportError:
        fail("Python package 'mlx-whisper' is unavailable; install it in this interpreter")
    kwargs: dict[str, Any] = {
        "path_or_hf_repo": model,
        "word_timestamps": True,
        "verbose": False,
    }
    if language != "auto":
        kwargs["language"] = language
    result = mlx_whisper.transcribe(str(layout.audio), **kwargs)
    if any("words" not in segment for segment in result.get("segments", [])):
        fail("mlx-whisper result lacks word timestamps; raw transcript was not written")
    layout.lecture.mkdir(parents=True, exist_ok=True)
    atomic_json(layout.raw, result)
    write_raw_sidecars(layout, result)
    layout.record("transcribe", {"input_sha256": audio_hash, "parameters": parameters, "output_sha256": sha256(layout.raw), "immutable": True})
    return layout.raw


def text_quality(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", text))


def extract_pdf_text(reference_pdf: Path) -> tuple[str, str]:
    pdf = reference_pdf.resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        fail(f"reference PDF is missing or invalid: {pdf}")
    pdftotext = require("pdftotext")
    direct = run([pdftotext, "-layout", str(pdf), "-"], capture=True).stdout
    if text_quality(direct) >= 200:
        return direct, "pdftotext"
    with tempfile.TemporaryDirectory(prefix="combinatorics-pdf-") as temp_name:
        temp = Path(temp_name)
        ocrmypdf = shutil.which("ocrmypdf")
        if ocrmypdf:
            searchable = temp / "searchable.pdf"
            run([ocrmypdf, "--skip-text", "--deskew", "--rotate-pages", str(pdf), str(searchable)])
            return run([pdftotext, "-layout", str(searchable), "-"], capture=True).stdout, "ocrmypdf"
        pdftoppm, tesseract = shutil.which("pdftoppm"), shutil.which("tesseract")
        if not pdftoppm or not tesseract:
            fail("PDF appears scanned; install ocrmypdf, or both pdftoppm and tesseract")
        prefix = temp / "page"
        run([pdftoppm, "-r", "300", "-png", str(pdf), str(prefix)])
        pages = []
        for image in sorted(temp.glob("page-*.png")):
            completed = run([tesseract, str(image), "stdout", "-l", "chi_sim+eng"], capture=True)
            pages.append(completed.stdout)
        return "\f".join(pages), "tesseract"


def normalize_repeated_line(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip())).casefold()


def clean_pdf_text(raw: str) -> str:
    pages = [page.splitlines() for page in raw.split("\f") if page.strip()]
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({
            normalize_repeated_line(line)
            for line in page if line.strip() and len(line.strip()) < 120
        })
    threshold = max(3, round(len(pages) * 0.3))
    repeated = {line for line, count in counts.items() if count >= threshold}
    cleaned_pages: list[str] = []
    for page in pages:
        lines = []
        for line in page:
            stripped = re.sub(r"\s+", " ", line).strip()
            if not stripped:
                lines.append("")
                continue
            if normalize_repeated_line(stripped) in repeated:
                continue
            if re.fullmatch(r"(?:第\s*)?[-–—]?\s*\d+\s*[-–—]?(?:\s*页)?", stripped):
                continue
            if lines and lines[-1].endswith("-") and re.match(r"^[a-z]", stripped):
                lines[-1] = lines[-1][:-1] + stripped
            else:
                lines.append(stripped)
        paragraphs: list[str] = []
        current: list[str] = []
        for line in lines + [""]:
            if line:
                current.append(line)
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        cleaned_pages.append("\n".join(paragraphs))
    return "\n".join(cleaned_pages).strip() + "\n"


def classify_term(term: str) -> str:
    lowered = term.casefold()
    if re.search(r"定理|引理|猜想|theorem|lemma|conjecture", lowered):
        return "theorem_or_name"
    if re.search(r"[=<>≤≥∑∏√]|\\(?:sum|prod|sqrt)", term):
        return "mathematical_expression"
    if re.search(r"图|集|数|概率|组合|graph|set|number|probability", lowered):
        return "combinatorics_term"
    return "technical_phrase"


def context_terms(text: str, canonical: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z-]{2,}|[\u3400-\u9fff]{2,8}", text)
    canonical_folded = canonical.casefold()
    return [token for token in dict.fromkeys(tokens) if token.casefold() not in canonical_folded][:6]


def dictionary_candidates(cleaned: str) -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}

    def add(canonical: str, aliases: list[str], context: str) -> None:
        canonical = re.sub(r"\s+", " ", canonical).strip(" :：,，;；()（）")
        aliases = [re.sub(r"\s+", " ", alias).strip() for alias in aliases]
        aliases = [alias for alias in aliases if alias and alias.casefold() != canonical.casefold()]
        minimum_length = 1 if canonical in "∑∏√≤≥≠" else 2
        if not minimum_length <= len(canonical) <= 64 or len(canonical.split()) > 8:
            return
        key = canonical.casefold()
        record = entries.setdefault(key, {
            "canonical": canonical,
            "aliases": [],
            "spoken_forms": [],
            "category": classify_term(canonical),
            "context_terms": [],
            "source": "unordered_reference_pdf",
        })
        record["aliases"] = list(dict.fromkeys(record["aliases"] + aliases))
        record["spoken_forms"] = list(dict.fromkeys(record["spoken_forms"] + aliases + [canonical]))
        record["context_terms"] = list(dict.fromkeys(record["context_terms"] + context_terms(context, canonical)))[:8]

    for line in cleaned.splitlines():
        line = line.strip()
        if not line or len(line) > 240:
            continue
        for mixed, english in re.findall(r"([A-Za-z\u3400-\u9fff· -]{2,40})\s*[（(]\s*([A-Za-z][A-Za-z -]{1,60})\s*[)）]", line):
            add(mixed, [english], line)
        for chinese, english in re.findall(r"([\u3400-\u9fff·]{2,30})\s*[（(]\s*([A-Za-z][A-Za-z -]{1,60})\s*[)）]", line):
            add(chinese, [english], line)
        for english, chinese in re.findall(r"([A-Za-z][A-Za-z -]{1,60})\s*[（(]\s*([\u3400-\u9fff·]{2,30})\s*[)）]", line):
            add(chinese, [english], line)
        for named in re.findall(r"\b(?:[A-Z][A-Za-z'’-]+\s+){0,3}(?:Theorem|Lemma|Conjecture|Identity|Principle)\b", line):
            add(named, [], line)
        for named in re.findall(r"[A-Za-z\u3400-\u9fff·-]{2,32}(?:定理|引理|猜想|恒等式|原理)", line):
            add(named, [], line)
        for symbol, spoken in re.findall(r"([∑∏√≤≥≠])\s*[（(]\s*([^()（）]{1,20})\s*[)）]", line):
            add(symbol, [spoken], line)
        for expression in re.findall(r"[A-Za-z0-9_{}()]+(?:\s*[=<>≤≥+*/^~-]\s*[A-Za-z0-9_{}()]+)+", line):
            add(expression, [], line)
        separator = re.match(r"^([^:：]{2,50})\s*[:：]\s*(.{2,})$", line)
        if separator and not re.search(r"[()（）]", separator.group(1)):
            add(separator.group(1), [], line)
        alias = re.match(r"^(.{2,40}?)\s*(?:又称|亦称|aka|also known as)\s*(.{2,40})$", line, flags=re.IGNORECASE)
        if alias:
            add(alias.group(1), [alias.group(2)], line)
    return sorted(entries.values(), key=lambda item: item["canonical"].casefold())


def build_dictionary(layout: Layout, reference_pdf: Path, force: bool) -> Path:
    layout.require_baseline()
    pdf = reference_pdf.resolve()
    if not pdf.is_file():
        fail(f"reference PDF does not exist: {pdf}")
    digest = sha256(pdf)
    state = load_json(layout.dictionary_state, {})
    if layout.dictionary.is_file() and state.get("pdf_sha256") == digest and not force:
        print(f"skip dictionary: {layout.dictionary}")
        layout.record("dictionary", {"pdf_sha256": digest, "dictionary_sha256": sha256(layout.dictionary)})
        return layout.dictionary
    if layout.dictionary.is_file() and state.get("pdf_sha256") not in (None, digest) and not force:
        fail("dictionary belongs to another PDF; pass --force only if replacement is intended")
    raw, method = extract_pdf_text(pdf)
    cleaned = clean_pdf_text(raw)
    entries = dictionary_candidates(cleaned)
    if not entries:
        fail("no dictionary terms could be extracted; inspect OCR/text quality before continuing")
    atomic_text(layout.reference_text, cleaned)
    atomic_jsonl(layout.dictionary, entries)
    atomic_json(layout.dictionary_state, {
        "schema": 1,
        "reference_pdf": str(pdf),
        "pdf_sha256": digest,
        "extraction_method": method,
        "cleaned_text_sha256": sha256(layout.reference_text),
        "dictionary_sha256": sha256(layout.dictionary),
        "entry_count": len(entries),
        "built_at": now(),
        "ordered": False,
        "page_numbers_retained": False,
    })
    layout.record("dictionary", {"pdf_sha256": digest, "dictionary_sha256": sha256(layout.dictionary), "entry_count": len(entries), "ordered": False})
    return layout.dictionary


def phonetic_key(text: str) -> str:
    chinese = re.findall(r"[\u3400-\u9fff]", text)
    if chinese:
        try:
            from pypinyin import lazy_pinyin  # type: ignore
            return "".join(lazy_pinyin(text)).casefold()
        except ImportError:
            return re.sub(r"\s+", "", text).casefold()
    lowered = re.sub(r"[^a-z]", "", text.casefold())
    for source, target in (("ph", "f"), ("th", "t"), ("ck", "k"), ("qu", "k"), ("c", "k")):
        lowered = lowered.replace(source, target)
    return lowered[:1] + re.sub(r"[aeiouy]", "", lowered[1:])


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.casefold(), right.casefold()).ratio()


def candidate_windows(text: str, term: str) -> list[str]:
    if re.search(r"[\u3400-\u9fff]", term):
        compact = re.sub(r"\s+", "", text)
        length = len(re.sub(r"\s+", "", term))
        return [compact[index:index + size] for size in range(max(2, length - 1), length + 2) for index in range(max(0, len(compact) - size + 1))]
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    length = len(term.split())
    return [" ".join(words[index:index + size]) for size in range(max(1, length - 1), length + 2) for index in range(max(0, len(words) - size + 1))]


def correction_proposal(text: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    canonical = str(entry["canonical"])
    if canonical.casefold() in text.casefold():
        return None
    contexts = [str(item) for item in entry.get("context_terms", [])]
    context_score = 1.0 if any(item.casefold() in text.casefold() for item in contexts) else 0.0
    best: dict[str, Any] | None = None
    for form in [canonical, *entry.get("spoken_forms", []), *entry.get("aliases", [])]:
        for window in candidate_windows(text, str(form)):
            spelling = similarity(window, str(form))
            phonetic = similarity(phonetic_key(window), phonetic_key(str(form)))
            confidence = 0.55 * phonetic + 0.25 * spelling + 0.20 * context_score
            proposal = {
                "original": window,
                "corrected": canonical,
                "spelling_score": round(spelling, 4),
                "phonetic_score": round(phonetic, 4),
                "context_score": context_score,
                "confidence": round(confidence, 4),
            }
            if best is None or proposal["confidence"] > best["confidence"]:
                best = proposal
    if not best or best["phonetic_score"] < 0.78 or best["confidence"] < 0.78:
        return None
    best["level"] = "HIGH" if best["confidence"] >= 0.92 and best["context_score"] == 1.0 else "MEDIUM"
    best["status"] = "AUTO" if best["level"] == "HIGH" else "REVIEW"
    best["reason"] = (
        f"phonetic={best['phonetic_score']}, spelling={best['spelling_score']}, "
        f"context={best['context_score']}; PDF dictionary used only as unordered terminology evidence"
    )
    return best


def bigrams(value: str) -> set[str]:
    compact = re.sub(r"\s+", "", value.casefold())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def retrieve_entries(text: str, entries: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    query = bigrams(phonetic_key(text))
    ranked = []
    for entry in entries:
        forms = [str(entry["canonical"]), *map(str, entry.get("spoken_forms", [])), *map(str, entry.get("aliases", []))]
        best = 0.0
        for form in forms:
            keys = bigrams(phonetic_key(form))
            if keys:
                best = max(best, len(query & keys) / len(keys))
        if best > 0:
            ranked.append((best, entry))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in ranked[:limit]]


def needs_math_review(text: str) -> bool:
    return bool(re.search(r"公式|等于|不等于|小于|大于|求和|乘积|根号|组合数|choose|equals|formula", text, flags=re.IGNORECASE))


def correct_transcript(layout: Layout, force: bool) -> Path:
    layout.require_baseline()
    if not layout.raw.is_file() or not layout.dictionary.is_file():
        fail("raw transcript and PDF dictionary are required before correction")
    raw_hash, dictionary_hash = sha256(layout.raw), sha256(layout.dictionary)
    parameters = {"raw_sha256": raw_hash, "dictionary_sha256": dictionary_hash, "algorithm": 1}
    prior = layout.stage("correct")
    if layout.corrected.is_file() and layout.corrections.is_file() and prior.get("parameters") == parameters and not force:
        print(f"skip correction: {layout.corrected}")
        return layout.corrected
    raw = load_json(layout.raw)
    corrected = copy.deepcopy(raw)
    entries = load_jsonl(layout.dictionary)
    audit: list[dict[str, Any]] = []
    for index, segment in enumerate(corrected.get("segments", [])):
        original_text = str(segment.get("text", ""))
        updated_text = original_text
        reviews = []
        proposals = []
        for entry in retrieve_entries(original_text, entries):
            proposal = correction_proposal(original_text, entry)
            if proposal:
                proposals.append(proposal)
        proposals.sort(key=lambda item: item["confidence"], reverse=True)
        used_originals: set[str] = set()
        for proposal in proposals:
            original = proposal["original"]
            if original in used_originals or not original:
                continue
            used_originals.add(original)
            record = {
                "segment_index": index,
                "timestamp": {"start": segment.get("start"), "end": segment.get("end")},
                **proposal,
            }
            if proposal["status"] == "AUTO" and original in updated_text:
                updated_text = updated_text.replace(original, proposal["corrected"], 1)
            else:
                record["status"] = "REVIEW"
                record["level"] = "MEDIUM"
                reviews.append({**proposal, "status": "REVIEW", "level": "MEDIUM"})
            audit.append(record)
        segment["text"] = updated_text
        segment["correction_review"] = reviews
        if needs_math_review(updated_text):
            segment["math_review"] = "REVIEW_REQUIRED: verify spoken formula against audio/keyframes; do not guess"
    corrected["correction_metadata"] = {
        "raw_sha256": raw_hash,
        "dictionary_sha256": dictionary_hash,
        "corrected_at": now(),
        "pdf_used_for_ordering": False,
        "pdf_sentences_copied": False,
        "word_tokens_preserved_from_raw": True,
    }
    atomic_json(layout.corrected, corrected)
    atomic_jsonl(layout.corrections, audit)
    layout.record("correct", {"parameters": parameters, "output_sha256": sha256(layout.corrected), "correction_count": len(audit), "auto_count": sum(item["status"] == "AUTO" for item in audit), "review_count": sum(item["status"] == "REVIEW" for item in audit)})
    return layout.corrected


def extract_frames(layout: Layout, threshold: float, force: bool) -> Path:
    layout.require_baseline()
    if not 0.0 <= threshold <= 1.0:
        fail("scene threshold must be between 0 and 1")
    video = video_path(layout)
    digest = sha256(video)
    parameters = {"scene_threshold": threshold}
    prior = layout.stage("frames")
    if layout.frames_index.is_file() and prior.get("input_sha256") == digest and prior.get("parameters") == parameters and not force:
        print(f"skip frames: {layout.frames_index}")
        return layout.frames_index
    if layout.frames.exists() and any(layout.frames.iterdir()) and not force:
        fail("frame parameters changed; pass --force to replace generated keyframes")
    ffmpeg = require("ffmpeg")
    layout.lecture.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{layout.lecture_id}-frames-") as temp_name:
        temp = Path(temp_name)
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-i", str(video),
            "-vf", f"select='eq(n,0)+gt(scene,{threshold})',showinfo", "-fps_mode", "vfr",
            "-q:v", "2", str(temp / "frame-%05d.jpg"),
        ]
        completed = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        times = [float(item) for item in re.findall(r"pts_time:([0-9.]+)", completed.stderr)]
        images = sorted(temp.glob("frame-*.jpg"))
        if len(times) < len(images):
            fail("ffmpeg did not report timestamps for every extracted frame")
        if layout.frames.exists():
            shutil.rmtree(layout.frames)
        layout.frames.mkdir(parents=True)
        records = []
        for index, (image, seconds) in enumerate(zip(images, times), 1):
            name = f"frame-{index:04d}-{timestamp(seconds).replace(':', '-').replace('.', '-')}.jpg"
            destination = layout.frames / name
            os.replace(image, destination)
            records.append({"file": str(destination.relative_to(layout.root)), "timestamp": timestamp(seconds)})
    atomic_json(layout.frames_index, {"schema": 1, "lecture_id": layout.lecture_id, "frames": records})
    layout.record("frames", {"input_sha256": digest, "parameters": parameters, "count": len(records)})
    return layout.frames_index


def current_preamble_hash(contract: dict[str, Any]) -> str:
    root = Path(contract["template_root"])
    main = root / contract["main_tex"]
    preamble = strip_comments(main.read_text(encoding="utf-8")).split(r"\begin{document}", 1)[0]
    return hashlib.sha256(preamble.encode("utf-8")).hexdigest()


def current_include_relations(contract: dict[str, Any]) -> list[dict[str, str]]:
    root = Path(contract["template_root"])
    main = root / contract["main_tex"]
    text = strip_comments(main.read_text(encoding="utf-8"))
    return [
        {"command": command, "target": target}
        for command, target in re.findall(r"\\(input|include)\s*\{([^}]+)\}", text)
    ]


def finalize(layout: Layout) -> Path:
    layout.require_baseline()
    contract = load_json(layout.contract)
    if not contract:
        fail("template contract is missing")
    if current_preamble_hash(contract) != contract["preamble_sha256"]:
        fail("template preamble changed after baseline; stop and review instead of compiling")
    class_path = Path(contract["template_root"]) / f"{contract['documentclass']['name']}.cls"
    if contract.get("class_sha256") and (not class_path.is_file() or sha256(class_path) != contract["class_sha256"]):
        fail("template document class changed after baseline; stop and review")
    if current_include_relations(contract) != contract["include_relations"]:
        fail("template input/include relationships changed after baseline; stop and review")
    root = Path(contract["template_root"])
    main = root / contract["main_tex"]
    completed = execute_full_build(contract["build_command"], root)
    atomic_text(layout.final_stdout, completed.stdout)
    log = root / f"{main.stem}.log"
    if log.is_file():
        shutil.copy2(log, layout.final_latex)
    if completed.returncode != 0:
        layout.record("final_compile", {"returncode": completed.returncode, "log": str(layout.final_stdout)}, status="failed")
        fail(f"final template compilation failed; see {layout.final_stdout}")
    pdf = root / f"{main.stem}.pdf"
    if not pdf.is_file():
        fail(f"final PDF is missing: {pdf}")
    checker = SCRIPT_ROOT / "scripts" / "check_latex.py"
    check = subprocess.run([
        sys.executable, str(checker), "--root", str(root), "--log", str(layout.final_latex),
        "--baseline-log", str(layout.baseline_latex),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    atomic_text(layout.lecture / "validation.log", check.stdout)
    if check.returncode != 0:
        fail(f"final LaTeX validation failed; see {layout.lecture / 'validation.log'}")
    layout.record("final_compile", {"pdf": str(pdf), "pdf_sha256": sha256(pdf), "validation_log": str(layout.lecture / "validation.log")})
    write_report(layout, pdf)
    return pdf


def write_report(layout: Layout, pdf: Path | None = None) -> Path:
    files = [layout.source, layout.contract, layout.raw, layout.corrected, layout.corrections, layout.dictionary]
    contract = load_json(layout.contract, {})
    template_changes = []
    if contract:
        root = Path(contract["template_root"])
        for relative, baseline_hash in contract.get("file_sha256", {}).items():
            path = root / relative
            if path.is_file() and sha256(path) != baseline_hash:
                template_changes.append({"path": str(path), "baseline_sha256": baseline_hash, "final_sha256": sha256(path)})
    report = {
        "schema": 1,
        "lecture_id": layout.lecture_id,
        "generated_at": now(),
        "state": layout.read_state(),
        "artifacts": [
            {"path": str(path), "sha256": sha256(path)} for path in files if path.is_file()
        ],
        "raw_transcript_immutable": layout.raw.is_file(),
        "reference_pdf_cited_in_latex": False,
        "final_pdf": str(pdf) if pdf else None,
        "final_pdf_sha256": sha256(pdf) if pdf and pdf.is_file() else None,
        "modified_template_files": template_changes,
    }
    atomic_json(layout.report, report)
    return layout.report


def check_tools() -> int:
    required = ("yt-dlp", "ffmpeg", "ffprobe", "pdftotext", "latexmk")
    missing = []
    for command in required:
        found = shutil.which(command)
        print(f"{command}: {found or 'MISSING'}")
        if not found:
            missing.append(command)
    for command in ("ocrmypdf", "pdftoppm", "tesseract"):
        print(f"{command} (OCR fallback): {shutil.which(command) or 'MISSING'}")
    try:
        import mlx_whisper  # type: ignore  # noqa: F401
        print("mlx-whisper: importable")
    except ImportError:
        print("mlx-whisper: MISSING")
        missing.append("mlx-whisper")
    try:
        import pypinyin  # type: ignore  # noqa: F401
        print("pypinyin (recommended for Chinese phonetics): importable")
    except ImportError:
        print("pypinyin (recommended for Chinese phonetics): MISSING")
    return 1 if missing else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT, help="pipeline repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-tools")
    for name in ("template-check", "download", "audio", "transcribe", "dictionary", "correct", "frames", "finalize", "status", "report", "run"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--id", required=True, dest="lecture_id")
        if name in ("template-check", "run"):
            sub.add_argument("--template-root", required=True, type=Path)
            sub.add_argument("--main-tex")
            sub.add_argument("--build-command")
        if name in ("download", "run"):
            sub.add_argument("--url", required=True)
        if name in ("transcribe", "run"):
            sub.add_argument("--model", default=DEFAULT_MODEL)
            sub.add_argument("--language", default="auto", choices=("auto", "zh", "en"))
        if name in ("dictionary", "run"):
            sub.add_argument("--reference-pdf", required=True, type=Path)
        if name in ("download", "audio", "dictionary", "correct", "frames"):
            sub.add_argument("--force", action="store_true")
        if name in ("frames", "run"):
            sub.add_argument("--scene-threshold", type=float, default=0.35)
        if name == "run":
            sub.add_argument("--frames", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "check-tools":
        return check_tools()
    layout = Layout(args.root, args.lecture_id)
    if args.command == "template-check":
        template_check(layout, args.template_root, args.main_tex, args.build_command)
    elif args.command == "download":
        download(layout, args.url, args.force)
    elif args.command == "audio":
        extract_audio(layout, args.force)
    elif args.command == "transcribe":
        transcribe(layout, args.model, args.language)
    elif args.command == "dictionary":
        build_dictionary(layout, args.reference_pdf, args.force)
    elif args.command == "correct":
        correct_transcript(layout, args.force)
    elif args.command == "frames":
        extract_frames(layout, args.scene_threshold, args.force)
    elif args.command == "finalize":
        print(finalize(layout))
    elif args.command == "status":
        print(json.dumps(layout.read_state(), ensure_ascii=False, indent=2))
    elif args.command == "report":
        print(write_report(layout))
    elif args.command == "run":
        if check_tools():
            fail("required tools are missing")
        if not args.reference_pdf.is_file():
            fail(f"reference PDF does not exist: {args.reference_pdf}")
        if not args.template_root.is_dir():
            fail(f"template root does not exist: {args.template_root}")
        template_check(layout, args.template_root, args.main_tex, args.build_command)
        download(layout, args.url, False)
        extract_audio(layout, False)
        transcribe(layout, args.model, args.language)
        build_dictionary(layout, args.reference_pdf, False)
        correct_transcript(layout, False)
        if args.frames:
            extract_frames(layout, args.scene_threshold, False)
        write_report(layout)
        print("Evidence preparation complete. Review corrections, generate template-native LaTeX, then run finalize.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
