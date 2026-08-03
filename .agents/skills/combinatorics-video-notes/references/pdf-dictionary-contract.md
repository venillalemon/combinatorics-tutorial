# Unordered PDF dictionary contract

## Extraction

Treat the PDF as read-only. First use `pdftotext -layout`. If extracted alphanumeric/CJK content is insufficient, OCR a temporary copy with `ocrmypdf --skip-text --deskew --rotate-pages`; alternatively render 300 DPI pages with `pdftoppm` and OCR them with Tesseract `chi_sim+eng`.

Do not overwrite the input PDF. Clean repeated headers, footers, standalone page numbers, line-end hyphenation, and layout-only line breaks. Retain cleaned text only as an auditable dictionary-building intermediate.

## Dictionary schema

Write one JSON object per line in `references/dictionary.jsonl`:

```json
{
  "canonical": "Turan 定理",
  "aliases": ["Turán theorem"],
  "spoken_forms": ["Turan 定理", "Turán theorem"],
  "category": "theorem_or_name",
  "context_terms": ["极值图", "完全图"],
  "source": "unordered_reference_pdf"
}
```

Store only terms, names, mathematical expressions, technical phrases, spoken forms, aliases, and short context keywords. Do not store page numbers or full sentences. Do not assign lecture order.

## Correction policy

For each timestamped transcript segment:

1. Retrieve short candidate terms by spelling and phonetic similarity.
2. Require contextual support within the segment.
3. Auto-apply only high-confidence matches with both phonetic and contextual support.
4. Record medium-confidence proposals as `REVIEW` without modifying the segment.
5. Ignore low-confidence proposals and preserve the raw wording.
6. Preserve word timestamps and the immutable raw transcript.

Each audit record must contain the original text span, proposed/corrected term, reason and component scores, video start/end timestamp, confidence, level, and `AUTO` or `REVIEW` status.

The PDF is never a lecture outline, chronology, citation source, or license to insert absent content. Never replace a teacher's sentence with a PDF sentence. Mark uncertain formulas for audio/keyframe review and do not guess.

