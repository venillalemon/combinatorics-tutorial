# Existing-template contract

## Preflight inventory

Before generating content, identify and record:

- the only main entry and its `documentclass` options;
- the repository's original build command and engine;
- every recursively referenced `\input` and `\include` file;
- packages and preamble-defined commands;
- existing definition, theorem, lemma, proposition, corollary, proof, example, exercise, problem, note, and remark environments;
- chapter splitting, titles, numbering, labels, references, symbols, bibliography, and index conventions;
- the exact existing file/location that permits insertion.

Compile the untouched template and preserve stdout plus the LaTeX log. Treat existing warnings as baseline observations. Stop on any baseline build failure.

## Non-negotiable constraints

- Do not create another main file or independent LaTeX architecture.
- Do not replace `documentclass` or edit the preamble.
- Do not add packages, commands, macros, theorem environments, or environment redefinitions.
- Do not change directory layout, naming conventions, or existing input/include relations.
- Do not delete, rewrite, or reformat original user content.
- Do not insert content unless the existing architecture makes the destination unambiguous.
- Reuse the closest existing semantic environment when there is no exact one.
- Follow existing numbering, label, cross-reference, font, language, symbol, bibliography, and index rules.
- Escape `%`, `#`, `&`, `_`, and other special characters in prose.
- Balance environments, braces, delimiters, and math mode.

## Content and provenance

Organize the lecture; do not paste subtitles. Do not manufacture a missing definition, proof, example, or exercise. If a proof is incomplete, state `课堂中未给出完整证明`.

Use the existing TODO/remark convention. If none exists, use ordinary visible text such as:

```latex
\textbf{待核实：视频中此处公式的下标不清。}
```

Do not define `\TODO` or any replacement macro.

Use the existing timestamp convention. If none exists, put the following ordinary text inside the relevant environment or immediately after the claim:

```latex
\ifdefined\VideoNotesTestMode
\textbf{视频来源：Lec16，视频时间：00:24:18--00:31:42。}
\fi
```

The conditional is the repository's build-mode convention, not a new command:
`make test` defines `\VideoNotesTestMode` and renders provenance, while
`make release` leaves it undefined and omits provenance. Keep the annotation in
source control, wrap every video-source line, and do not create another main TeX
file or provenance macro.

Never cite the reference PDF. Never use the PDF to supply a theorem, formula, proof step, or example that is absent from the audio.

## Full-build validation

After each TeX modification, use the original full-project command. Check at least:

- LaTeX and package errors;
- undefined control sequences;
- undefined references/citations and duplicate labels;
- unclosed environments and math-mode errors;
- bibliography failures;
- missing glyph, font, and Unicode problems;
- obvious overfull boxes.

Compare the final log against the preserved baseline. Repair only new-content regressions; never weaken or restructure the template to make a build pass.
