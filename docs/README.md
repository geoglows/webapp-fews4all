# FEWS4All documentation

Written documents that ship with the project, kept as source rather than as
binaries so a change can be read in a diff instead of only re-read in full.

## What is here

| Folder | What it is |
|---|---|
| `user-manual/` | The user manual, in LaTeX. The document people are given. |
| `Old/` | Superseded originals — the Word manual, the timelines, the personal reference. Kept on disk, **not in git**. |
| `claude-outputs/` | A landing zone for files delivered from a chat session. **Not in git.** Nothing here should ever be the only copy of anything. |

## Building the manual

```bash
cd docs/user-manual
tectonic main.tex          # writes main.pdf
```

`tectonic` installs with `conda install -c conda-forge tectonic` and fetches
whatever LaTeX packages the document needs on first run.

With a full TeX Live instead, `latexmk -xelatex main.tex` does the same.

**Build from inside the document's folder.** TeX resolves `\input` and
`\includegraphics` against the *current working directory*, not against the file
being compiled, so running `tectonic docs/user-manual/main.tex` from the repo
root fails to find `content.tex` and the logo. It reports a missing picture and
then `Division by 0`, which points at the image and says nothing about the real
cause. From elsewhere, `latexmk -cd` changes directory first and works.

## How a document is laid out

One folder per document, holding its own sources and assets:

```
user-manual/
  main.tex      the LOOK — preamble, title page, table of contents
  content.tex   the PROSE — what the manual actually says
  images/       anything the document includes
```

Keeping the look and the content in separate files is deliberate. Mixing them is
how a document becomes something only its author can edit.

## Conventions

**One sentence per line** in the prose files. It looks odd and changes nothing in
the output — LaTeX reflows paragraphs regardless. The point is the diff: edit one
sentence and the diff is one line, rather than a reflowed paragraph showing up as
"everything changed". This is most of the difference between writing that can be
reviewed in a pull request and writing that can only be re-read.

**Split `content.tex` into `sections/` once it outgrows one file** — somewhere
past ten pages. Until then one file is easier to move around in than ten.

**Nothing built goes in git.** `docs/.gitignore` covers the whole tree, so a new
document folder inherits the rules without anyone setting it up. A committed PDF
shows as a changed binary on every rebuild and tells a reviewer nothing.
