LATEXMK := latexmk
LATEXMK_OPTS := -interaction=nonstopmode -file-line-error

CN_TEX := elegantbook-cn.tex
CN_PDF := $(CN_TEX:.tex=.pdf)
CHAPTERS := $(wildcard chapters/*.tex)
NOTES := $(wildcard notes/*.md)
ASSETS := $(wildcard figure/* image/* image/notes/*)

.PHONY: all cn clean distclean watch

all: cn

cn: $(CN_PDF)

$(CN_PDF): $(CN_TEX) $(CHAPTERS) $(NOTES) elegantbook.cls reference.bib $(ASSETS)
	$(LATEXMK) -pdfxe $(LATEXMK_OPTS) $(CN_TEX)

watch:
	$(LATEXMK) -pvc -pdfxe $(LATEXMK_OPTS) $(CN_TEX)

clean:
	$(LATEXMK) -c $(CN_TEX)

distclean:
	$(LATEXMK) -C $(CN_TEX)
