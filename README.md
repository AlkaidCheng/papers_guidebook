# Scientific Papers Guidebook

A reference guide to the papers, datasets, methods and models of machine learning in
science, organised by topic in compact tables. Each row opens to its description, its paper,
data records and citing papers on INSPIRE-HEP, its citation as BibTeX or text, a link to
share, and the citations between the papers of the guide.

**Read it at <https://alkaidcheng.github.io/papers_guidebook/>.**

## Contents

- **Part I: Machine Learning for Particle Physics**
  - *Public Datasets*: datasets for jet tagging, generative models and fast simulation,
    anomaly detection, event classification and inference, and reconstruction.
  - *Jet-Tagging Architectures*: from jet images and graphs to transformers and
    Lorentz-equivariant networks.
  - *Anomaly Detection*: weakly supervised and unsupervised methods for model-agnostic
    searches.
  - *Simulation-Based Inference*: likelihood-ratio, density and posterior estimators.
  - *Generative Models*: models for calorimeter showers, jets as particle clouds, and
    collision events and phase space.
  - *Foundation Models*: pre-trained and self-supervised models for particle data.
  - *Papers*: every paper of the guide, by kind of contribution, with tags.

## How the pages are built

The catalogue pages are generated from YAML files; the rest of the book is Markdown.

```text
INSPIRE-HEP, DataCite ──fetch──► catalogue/cache.json ─┐
catalogue/papers.yaml ─────────────────────────────────┼─build─► book/ml-hep/*.md, *.bib ──bookspine build──► site/
catalogue/<page>.yaml ─────────────────────────────────┘
```

| Step | What it does |
|---|---|
| `python scripts/catalogue.py fetch` | Fetches the title, authors, journal, identifiers, BibTeX and reference list of each paper from INSPIRE-HEP, and the creators, title, publisher, year and BibTeX of each data record from DataCite, into `catalogue/cache.json`. Only new entries are fetched; `--refresh` fetches all. |
| `python scripts/catalogue.py build` | Writes every catalogue page and its BibTeX file into `book/`. `--check` writes nothing and fails if a page differs from what the catalogue gives. |
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

## Adding papers, entries and pages

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

Every other YAML file in `catalogue/` is a page of entries, such as datasets or
methods, listed in `book.yaml` like any other page. Its `page` gives the path, title,
summary and a short `label` that names the page in the rows of the papers it cites. Its
`table` gives the title of the name column (`name`), the `columns` between the name and
the year, each a `key` and a `title`, and the `facts` shown when a row is opened. The
entries sit in `sections`.

| Entry field | Meaning |
|---|---|
| `id`, `name` | The row's anchor and title. |
| `year` | The year in the row; by default that of the first paper. |
| a column's `key` | The short phrase in that column. |
| a fact's `key` | A value shown with the fact's title when the row is opened. |
| `summary` | One sentence shown when the row is opened. |
| `details` | Markdown shown after it. |
| `papers`, `data` | The papers and data records a result obtained with the entry cites. |
| `related` | Further papers or records listed when the row is opened. |
| `links` | Further links by label, such as `Code`. |
| `uses` | Entries of other pages this one uses, each as `page:id` with the page's file name, such as `datasets:jetclass`. Each is linked from this row, and this row from theirs. |

Data records sit in the page's `records` map. A record with a DOI needs only `doi`, and
`label` when an entry has several records; `title` and `creators` replace poor DataCite
metadata. A record without a DOI gives `title`, `publisher`, `year`, `url` and
`bibtex`.

After editing, run `fetch`, then `build`, and commit the catalogue, the cache and the
generated pages together. Every pull request is built by CI, and each push to `main`
is deployed to GitHub Pages.

## License

[MIT](LICENSE)
