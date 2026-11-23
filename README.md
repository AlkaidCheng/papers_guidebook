# Scientific Papers Guidebook

A reference guide to the papers, datasets and tools of machine learning in science,
organised by topic in compact tables. Each row opens to its description, its paper,
data records and citing papers on INSPIRE-HEP, its citation as BibTeX or text, and the
citations between the papers of the guide.

**Read it at <https://alkaidcheng.github.io/papers_guidebook/>.**

## Contents

- **Part I: Machine Learning for Particle Physics**
  - *Public Datasets*: datasets for jet tagging, jets in collision data, generative
    models and fast simulation, anomaly detection, event classification and inference,
    and reconstruction.
  - *Papers*: the papers behind them, by kind of contribution, with tags.

## How the pages are built

The catalogue pages are generated from YAML files; the rest of the book is Markdown.

```text
INSPIRE-HEP, DataCite ──fetch──► catalogue/cache.json ─┐
catalogue/papers.yaml ─────────────────────────────────┼─build─► book/ml-hep/*.md, *.bib ──bookspine build──► site/
catalogue/datasets.yaml ───────────────────────────────┘
```

| Step | What it does |
|---|---|
| `python scripts/catalogue.py fetch` | Fetches the title, authors, journal, identifiers, BibTeX and reference list of each paper from INSPIRE-HEP, and the creators, title, publisher, year and BibTeX of each data record from DataCite, into `catalogue/cache.json`. Only new entries are fetched; `--refresh` fetches all. |
| `python scripts/catalogue.py build` | Writes the datasets and papers pages and their BibTeX files into `book/`. `--check` writes nothing and fails if a page differs from what the catalogue gives. |
| `bookspine build` | Builds the site from `book/` with [bookspine](https://github.com/AlkaidCheng/bookspine). |

## Building locally

Python 3.11 or later:

```bash
pip install -r requirements.txt
python scripts/catalogue.py build
bookspine build
python3 -m http.server --directory site 8000
```

Then open <http://127.0.0.1:8000/>.

## Adding a paper or a dataset

A paper is a record in `catalogue/papers.yaml`, keyed by its INSPIRE texkey. Only what
a person writes goes there; the rest is fetched.

| Field | Meaning |
|---|---|
| `inspire` | The INSPIRE record number. |
| `category` | One of the `categories` listed at the top of the file. |
| `tags` | Any of the `tags` listed at the top of the file. |
| `short` | One sentence shown when the row is opened. |
| `long` | A few sentences shown after it. |
| `venue` | Replaces the journal reference where INSPIRE has none, as for proceedings. |
| `title`, `authors`, `year`, `url`, `bibtex` | For a paper without an INSPIRE record only. |

A dataset is an entry in a section of `catalogue/datasets.yaml`, and its files are
records in the `records` map of the same file.

| Field | Meaning |
|---|---|
| `id`, `name`, `year` | The row's anchor, title and year of release. |
| `content`, `level`, `size` | The short phrases in the row: what the data are, the level of detector simulation and the size. |
| `summary`, `simulation` | One sentence and the full simulation chain, shown when the row is opened. |
| `details` | Markdown shown when the row is opened. |
| `papers`, `data` | The papers and records a result obtained with the dataset cites. |
| `related` | Further papers or records listed when the row is opened. |

A record with a DOI needs only `doi`, and `label` when an entry has several records;
`title` and `creators` replace poor DataCite metadata. A record without a DOI gives
`title`, `publisher`, `year`, `url` and `bibtex`.

After editing, run `fetch`, then `build`, and commit the catalogue, the cache and the
generated pages together. Every pull request is built by CI, and each push to `main`
is deployed to GitHub Pages.

## License

[MIT](LICENSE)
