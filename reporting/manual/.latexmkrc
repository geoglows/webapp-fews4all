# latexmk settings for the user manual.
#
# Lives here rather than in VS Code's settings because it is a property of the
# DOCUMENT, not of one person's editor: .vscode/ is gitignored in this repo, so
# an editor-only setting would not reach anyone else, and a build run from the
# terminal would disagree with a build run from the editor -- two PDFs in two
# places, one of them stale, with no way to tell which you are reading.
#
#   latexmk -pdf main.tex     build; rerun passes until the .aux settles
#   latexmk -c                remove the artefacts, keep the PDF
#   latexmk -C                remove everything, PDF included
#
# NOTE: build from inside this folder. TeX resolves \input and \includegraphics
# against the current working directory, not against main.tex, so building from
# the repo root fails to find content.tex and images/. Use `latexmk -cd` if you
# must run it from elsewhere.

# Everything generated goes in reporting/out/, beside the generated tables and
# figures, so there is ONE generated directory in the whole tree and one line in
# .gitignore covering it. This folder holds only sources.
#
# VS Code's latex-workshop.latex.outDir is set to match; if you change one,
# change the other or the editor and the terminal will build to different
# places, and you will read a stale PDF without knowing which one it is.
$out_dir = '../out/manual';

# pdflatex, matching the TeX Live install on this machine. main.tex also builds
# under xelatex (and tectonic) -- its preamble branches on \ifPDFTeX -- so this
# is a default, not a requirement.
$pdf_mode = 1;
