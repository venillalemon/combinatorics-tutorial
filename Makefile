LATEXMK := latexmk
LATEXMK_OPTS := -interaction=nonstopmode -file-line-error
PYTHON := python3

CN_TEX := elegantbook-cn.tex
CN_PDF := $(CN_TEX:.tex=.pdf)
TEST_PDF := $(CN_TEX:.tex=-test.pdf)
CHAPTERS := $(wildcard chapters/*.tex)
NOTES := $(wildcard notes/*.md)
ASSETS := $(wildcard figure/* image/* image/notes/*)

.PHONY: all cn release test check pipeline-help clean distclean watch

all: release

cn: release

release: $(CN_PDF)

test: $(TEST_PDF)

$(CN_PDF): $(CN_TEX) $(CHAPTERS) $(NOTES) elegantbook.cls reference.bib $(ASSETS)
	$(LATEXMK) -pdfxe $(LATEXMK_OPTS) $(CN_TEX)

$(TEST_PDF): $(CN_TEX) $(CHAPTERS) $(NOTES) elegantbook.cls reference.bib $(ASSETS)
	$(LATEXMK) -pdfxe $(LATEXMK_OPTS) -jobname=$(basename $(TEST_PDF)) -usepretex='\def\VideoNotesTestMode{1}' $(CN_TEX)

watch:
	$(LATEXMK) -pvc -pdfxe $(LATEXMK_OPTS) $(CN_TEX)

check: cn
	$(PYTHON) scripts/check_latex.py

pipeline-help:
	$(PYTHON) scripts/video_notes.py --help

clean:
	$(LATEXMK) -c $(CN_TEX)
	$(LATEXMK) -c -jobname=$(basename $(TEST_PDF)) $(CN_TEX)

distclean:
	$(LATEXMK) -C $(CN_TEX)
	$(LATEXMK) -C -jobname=$(basename $(TEST_PDF)) $(CN_TEX)
