---
name: combinatorics-video-notes
description: Build traceable Chinese or bilingual combinatorics notes from a direct MP4 or video-page URL, an unordered reference PDF, a lecture id, and an existing compilable LaTeX template root. Use when Codex must baseline-compile and inspect the full template, download and hash a lecture, create immutable segment- and word-timestamped mlx-whisper output, OCR/extract a terminology-only PDF dictionary, conservatively correct transcription with an audit trail, optionally inspect keyframes, write only template-native LaTeX, and deliver a validated PDF and processing report.
---

# Combinatorics Video Notes

Treat the video as the only source of lecture content, the PDF as an unordered spelling dictionary, and the existing LaTeX draft as the only formatting authority. Preserve unrelated user edits.

## Require inputs

Require all four values before processing:

- `VIDEO_URL`
- `LECTURE_ID`
- `REFERENCE_PDF_PATH`
- `LATEX_TEMPLATE_ROOT`

Accept direct MP4 URLs and ordinary video pages. Reject a missing PDF or template root. Use a new lecture id if the video, audio, or transcription parameters change after raw transcription.

## Announce template understanding and plan

Before downloading anything, inspect the template and tell the user the discovered main entry, document class, build command/engine, include structure, semantic environments, bibliography/index scheme, and intended insertion point. State the execution plan. If the insertion point is not unambiguous, stop and ask; do not guess.

Read [references/latex-contract.md](references/latex-contract.md) before touching TeX. Read [references/pdf-dictionary-contract.md](references/pdf-dictionary-contract.md) before extracting or using the PDF.

## Execute in this exact order

### 1. Check tools and inputs

Run:

```bash
python3 scripts/video_notes.py check-tools
```

Require `yt-dlp`, `ffmpeg`, `ffprobe`, `pdftotext`, `mlx-whisper`, and the template's build tool. OCR additionally requires `ocrmypdf`, or both `pdftoppm` and `tesseract`. Prefer `pypinyin` for Chinese phonetic matching.

### 2. Inspect and compile the untouched template

Run:

```bash
python3 scripts/video_notes.py template-check \
  --id "$LECTURE_ID" \
  --template-root "$LATEX_TEMPLATE_ROOT"
```

Pass `--main-tex` only when multiple entry candidates exist. Pass `--build-command` only when the repository has no discoverable original build command. This stage recursively reads `\input` and `\include` dependencies, writes `lectures/$LECTURE_ID/template-contract.json`, performs a full build, and preserves baseline logs.

If this stage fails, stop immediately and report the original error. Do not download the video and do not attribute the failure to generated content.

### 3. Download and hash the video

```bash
python3 scripts/video_notes.py download --id "$LECTURE_ID" --url "$VIDEO_URL"
```

Keep URL, resolved metadata, duration, download time, processing status, local path, and SHA-256 in `lectures/$LECTURE_ID/source.json`. Skip a verified existing download. Never reuse an id for another URL.

### 4. Extract audio

```bash
python3 scripts/video_notes.py audio --id "$LECTURE_ID"
```

Require ffprobe verification of 16 kHz, mono, PCM audio.

### 5. Create immutable raw transcription

```bash
python3 scripts/video_notes.py transcribe --id "$LECTURE_ID" --language auto
```

Use `auto` for Chinese/English mixtures. Require both segment and word timestamps. Permanently preserve `lectures/$LECTURE_ID/transcript.raw.json`; never overwrite it, including with `--force`. Retain raw TXT, SRT, and VTT sidecars.

### 6. Build the unordered PDF dictionary

```bash
python3 scripts/video_notes.py dictionary \
  --id "$LECTURE_ID" \
  --reference-pdf "$REFERENCE_PDF_PATH"
```

Extract all usable text; OCR only when direct extraction is insufficient. Clean repeated headers/footers, page numbers, broken words, and meaningless line breaks. Save terminology entries to `references/dictionary.jsonl`. Never record page numbers, order entries by lecture sequence, or treat sentences as replacement text.

### 7. Correct conservatively

```bash
python3 scripts/video_notes.py correct --id "$LECTURE_ID"
```

Inspect `lectures/$LECTURE_ID/corrections.jsonl`. Accept automatic edits only when pronunciation and context both support them. Resolve every `REVIEW` proposal against audio and nearby keyframes before writing notes. Leave low-confidence text unchanged. Preserve `transcript.raw.json`; use only `transcript.corrected.json` as the editable derivative.

Never paste a PDF sentence into the transcript. Never add a PDF fact absent from the audio. Mark uncertain spoken formulas for review instead of reconstructing them from reference knowledge.

### 8. Optionally inspect keyframes

```bash
python3 scripts/video_notes.py frames --id "$LECTURE_ID"
```

Use keyframes only to verify slides, board work, and formulas near the same video timestamp.

### 9. Generate template-native notes

Re-read the current main file and every dependency listed in `template-contract.json`; user edits may have occurred since baseline. Synthesize notes from the corrected transcript and verified keyframes rather than copying subtitles.

Include only content actually taught: motivation, definitions, propositions, theorems, lemmas, corollaries, proof ideas or complete proofs, examples, counterexamples, computations, emphasis, pitfalls, connections, exercises, and hints. Do not fill categories that the lecture did not contain. Write “课堂中未给出完整证明” when appropriate; never complete a proof and present it as the lecture's proof.

Modify only the unambiguous insertion file allowed by the existing include structure. Reuse existing sectioning, theorem, proof, example, exercise, remark, numbering, label, reference, language, font, symbol, bibliography, and index conventions. Escape LaTeX special characters and balance every environment, delimiter, and math mode.

For important statements, use the template's existing timestamp style. If none exists, add plain text inside the content:

```latex
\ifdefined\VideoNotesTestMode
\textbf{视频来源：Lec16，视频时间：00:24:18--00:31:42。}
\fi
```

This repository has two build modes. Keep source and timestamp annotations in the
tracked TeX source, but wrap every video-provenance line in the existing
`\ifdefined\VideoNotesTestMode ... \fi` convention. `make test` defines that
switch and produces `elegantbook-cn-test.pdf` with annotations. `make release`
does not define it and produces `elegantbook-cn.pdf` without video-source text.
Do not create another main TeX file or a new provenance macro. GitHub Actions must
compile with `make release`.

If the template has no TODO mechanism, write ordinary visible text such as `\textbf{待核实：公式下标不清}`. Do not add a macro. Never cite the PDF or record its page number in TeX.

### 10. Compile, repair, and report

Run:

```bash
python3 scripts/video_notes.py finalize --id "$LECTURE_ID"
```

After pipeline validation, also run both repository build modes:

```bash
make test
make release
```

This rejects preamble changes, runs the original full build, compares final warnings with the baseline, validates references/labels/control sequences/math/font/Unicode/overfull issues, preserves logs, writes `processing-report.json`, and requires a final PDF.

Fix only problems introduced by generated content. Do not change the document class, preamble, packages, commands, environments, directory structure, or original user text to bypass an error. Repeat `finalize` until it passes, then report every written/modified file and the final PDF.

## Resume safely

Inspect receipts with:

```bash
python3 scripts/video_notes.py status --id "$LECTURE_ID"
```

Each stage records input hashes, parameters, outputs, status, and timestamps. Skip matching completed stages. Use `--force` only for replaceable derived artifacts after deliberate review; it is unavailable for immutable raw transcription.
