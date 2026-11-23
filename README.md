# Scientific Papers Guidebook

A textbook-style guide to the reference literature of machine learning in
science, organised by topic. For each dataset, method and tool, it gives the
paper to cite, the record that holds the material, and what a result obtained
with it can claim.

**Read it at <https://alkaidcheng.github.io/papers_guidebook/>.**

## Contents

- **Part I: Machine Learning for Particle Physics**: the public datasets used
  to train and benchmark models for jet tagging, generative modelling and fast
  simulation, anomaly detection, event classification and inference, and
  reconstruction.

## Building locally

The site is generated from Markdown by
[bookspine](https://github.com/AlkaidCheng/bookspine), which needs Python 3.11 or
later.

```bash
pip install -r requirements.txt
bookspine build
python3 -m http.server --directory site 8000
```

Then open <http://127.0.0.1:8000/>. The build fails on a broken internal link,
a citation without a reference entry, or a reference that is never cited.

## Writing a chapter

1. Write the chapter as Markdown in its part's directory under `book/`, with a
   YAML front matter block giving its `title`, a one-sentence `summary`, an
   optional `status` (for example `in-preparation`) and a `references` map of
   citation keys to formatted entries.
2. Add the file name to the part's `chapters` list in `book.yaml`. Chapters
   are numbered in that order, continuously through the book.
3. Use `##` for sections and `###` for subsections; the build numbers them. A
   table followed by a paragraph starting with `Table:` becomes a numbered
   table.
4. Cite with `[@key]`; the reference list is generated at the end of the
   chapter in order of first citation. A dataset is cited by its reference
   paper and by its data record, each with its own key.
5. Set material apart with `!!! definition`, `!!! remark`, `!!! warning` or
   `!!! summary` blocks. Definitions and remarks are numbered within the
   chapter.

A new part is a new directory with an `index.md` opener and an entry in
`book.yaml`. Every pull request is built by CI, and each push to `main` is
deployed to GitHub Pages.

## License

[MIT](LICENSE)
