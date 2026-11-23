"""Tests of scripts/catalogue.py: BibTeX handling, names, loading, cross-references
and the pages a build writes."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

# The generator is a script, not a package, so it is loaded from its path. It is
# registered before it runs, because its dataclasses look their module up there.
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "catalogue.py"
_spec = importlib.util.spec_from_file_location("catalogue", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
catalogue = importlib.util.module_from_spec(_spec)
sys.modules["catalogue"] = catalogue
_spec.loader.exec_module(catalogue)

INSPIRE_ENTRY = """@article{Doe:2020abc,
    author = "Doe, Jane and Roe, Richard",
    title = "{A {Nested} title}",
    eprint = "2001.00001",
    year = "2020"
}"""

DATACITE_ENTRY = """@misc{https://doi.org/10.5281/zenodo.1,
  doi = {10.5281/ZENODO.1},
  author = {Doe, Jane},
  keywords = {Particle physics},
  title = {Plain Title},
  publisher = {Zenodo},
  year = {2021},
  copyright = {Creative Commons Attribution 4.0 International}
}"""

PAPERS_YAML = """
page: {path: part/papers.md, title: Papers, summary: All papers.}
intro: The papers.
categories:
  - {id: method, title: Methods}
  - {id: dataset, title: Datasets}
tags: [jet tagging, open data]
papers:
  Doe:2020abc:
    inspire: 101
    category: method
    tags: [jet tagging]
    short: A method.
  Roe:2021xyz:
    inspire: 102
    category: dataset
    tags: [open data]
    short: A dataset with $H \\to b\\bar b$ jets.
    long: Longer words.
  Manual:2015:
    category: method
    short: A paper without an INSPIRE record.
    title: Manual paper
    authors: ["Smith, Alice"]
    year: 2015
    venue: Proceedings (2015) 1
    url: https://example.org/manual
    bibtex: |
      @inproceedings{Manual:2015,
          title = "{Manual paper}",
          year = "2015"
      }
"""

DATASETS_YAML = """
page: {path: part/datasets.md, title: Datasets, summary: All datasets.}
intro: The datasets.
table:
  name: Dataset
  columns:
    - {key: content, title: Content}
    - {key: level, title: Simulation}
    - {key: size, title: Size}
  facts:
    - {key: simulation, title: Simulation}
sections:
  - title: Jet tagging
    entries:
      - id: jets
        name: Jets
        year: 2021
        content: Tagged jets
        level: Delphes
        summary: Jets for tagging.
        simulation: Pythia 8
        size: 50,000 $t\\bar t$ events
        papers: [Roe:2021xyz]
        data: [data:jets]
        related: [Doe:2020abc]
        details: |
          - **Note:** details.
records:
  data:jets: {doi: 10.5281/zenodo.1}
"""

METHODS_YAML = """
page: {path: part/methods.md, title: Method Catalogue, label: Methods, summary: All methods.}
intro: The methods.
table:
  name: Method
  columns:
    - {key: idea, title: Idea}
sections:
  - title: Networks
    entries:
      - id: a-method
        name: A $k_T$ method
        idea: An idea
        papers: [Doe:2020abc]
        uses: [datasets:jets]
        links: {Code: "https://example.org/code"}
        summary: A method for tagging.
"""

BOOK_YAML = """
title: Book
author: Author
parts:
  - dir: part
    title: Part
    chapters:
      - datasets.md
      - methods.md
      - papers.md
"""


def metadata(**overrides: object) -> dict:
    """A cached metadata record."""
    base = asdict(catalogue.Metadata(title="Title"))
    base.update(overrides)
    return base


CACHE = {
    "papers": {
        "Doe:2020abc": metadata(
            title="A method paper",
            authors=["Doe, Jane", "Roe, Richard"],
            author_count=2,
            year=2020,
            venue="Phys. Rev. D 1 (2020) 2",
            arxiv="2001.00001",
            bibtex=INSPIRE_ENTRY,
            references=[102],
        ),
        "Roe:2021xyz": metadata(
            title="A dataset paper",
            authors=["Roe, Richard"],
            author_count=2900,
            collaboration="ATLAS",
            year=2021,
            arxiv="2101.00002",
            bibtex='@article{Roe:2021xyz,\n    title = "{A dataset paper}"\n}',
            references=[999],
        ),
    },
    "records": {
        "data:jets": metadata(
            title="Plain Title",
            authors=["Doe, Jane"],
            author_count=1,
            year=2021,
            venue="Zenodo",
            doi="10.5281/zenodo.1",
            bibtex=catalogue.normalise_entry(DATACITE_ENTRY, "data:jets"),
        )
    },
}


class TestBibtex(unittest.TestCase):
    def test_split_keeps_nested_braces_and_order(self) -> None:
        text = "% comment\n" + INSPIRE_ENTRY + "\n\n" + DATACITE_ENTRY
        entries = catalogue.split_bibtex(text)
        self.assertEqual(
            list(entries), ["Doe:2020abc", "https://doi.org/10.5281/zenodo.1"]
        )
        self.assertTrue(entries["Doe:2020abc"].endswith('"2020"\n}'))

    def test_split_rejects_an_unclosed_entry(self) -> None:
        with self.assertRaises(ValueError):
            catalogue.split_bibtex("@misc{key, title = {open")

    def test_field_reads_quoted_and_braced_values(self) -> None:
        self.assertEqual(
            catalogue.bibtex_field(INSPIRE_ENTRY, "title"), "{A {Nested} title}"
        )
        self.assertEqual(catalogue.bibtex_field(DATACITE_ENTRY, "publisher"), "Zenodo")
        self.assertIsNone(catalogue.bibtex_field(DATACITE_ENTRY, "journal"))

    def test_field_does_not_match_inside_a_longer_name(self) -> None:
        entry = '@misc{k,\n  booktitle = "{Book}",\n  title = "{Paper}"\n}'
        self.assertEqual(catalogue.bibtex_field(entry, "title"), "{Paper}")

    def test_set_field_replaces_or_adds(self) -> None:
        replaced = catalogue.set_bibtex_field(INSPIRE_ENTRY, "title", "{New}")
        self.assertEqual(catalogue.bibtex_field(replaced, "title"), "{New}")
        added = catalogue.set_bibtex_field(INSPIRE_ENTRY, "note", "Extra")
        self.assertEqual(catalogue.bibtex_field(added, "note"), "Extra")
        self.assertTrue(added.endswith("}"))

    def test_normalise_rekeys_drops_fields_and_protects_title(self) -> None:
        entry = catalogue.normalise_entry(DATACITE_ENTRY, "data:jets")
        self.assertTrue(entry.startswith("@misc{data:jets,"))
        self.assertNotIn("copyright", entry)
        self.assertNotIn("keywords", entry)
        self.assertEqual(catalogue.bibtex_field(entry, "title"), "{Plain Title}")
        self.assertEqual(catalogue.normalise_entry(entry, "data:jets"), entry)

    def test_normalise_keeps_an_inspire_entry(self) -> None:
        self.assertEqual(
            catalogue.normalise_entry(INSPIRE_ENTRY, "Doe:2020abc"), INSPIRE_ENTRY
        )


class TestNames(unittest.TestCase):
    def test_initials(self) -> None:
        self.assertEqual(catalogue.initials("Jean-Roch"), "J.-R.")
        self.assertEqual(catalogue.initials("Eric A."), "E. A.")

    def test_short_name(self) -> None:
        self.assertEqual(catalogue.short_name("de Oliveira, Luke"), "L. de Oliveira")
        self.assertEqual(catalogue.short_name("Congqiao Li"), "Congqiao Li")

    def test_format_authors(self) -> None:
        names = ["Qu, Huilin", "Li, Congqiao", "Qian, Sitian"]
        self.assertEqual(catalogue.format_authors(names, 3), "H. Qu, C. Li and S. Qian")
        self.assertEqual(catalogue.format_authors(names, 4), "H. Qu et al.")
        self.assertEqual(catalogue.format_authors(names[:1], 1), "H. Qu")
        self.assertEqual(
            catalogue.format_authors([], 2900, "ATLAS"), "ATLAS Collaboration"
        )

    def test_journal_reference_spaces_abbreviations(self) -> None:
        info = {
            "journal_title": "Phys.Rev.D",
            "journal_volume": "102",
            "year": 2020,
            "artid": "012010",
        }
        self.assertEqual(
            catalogue.journal_reference(info), "Phys. Rev. D 102 (2020) 012010"
        )
        self.assertEqual(catalogue.journal_reference({}), "")

    def test_citation_adds_the_year_a_publisher_lacks(self) -> None:
        record = catalogue.Metadata.from_dict(CACHE["records"]["data:jets"])
        self.assertEqual(
            record.citation(),
            "J. Doe, “Plain Title”, Zenodo (2021), doi:10.5281/zenodo.1.",
        )
        paper = catalogue.Metadata.from_dict(CACHE["papers"]["Doe:2020abc"])
        self.assertTrue(
            paper.citation().endswith("Phys. Rev. D 1 (2020) 2, arXiv:2001.00001.")
        )

    def test_html_text_escapes_and_marks_math(self) -> None:
        self.assertEqual(
            catalogue.html_text("a < b, $t\\bar t$"), "a &lt; b, \\(t\\bar t\\)"
        )


class CatalogueDir(unittest.TestCase):
    """A temporary catalogue: papers, two entry pages, a cache, book.yaml, a book."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.catalogue_dir = root / "catalogue"
        self.catalogue_dir.mkdir()
        self.cache_path = root / "cache.json"
        self.book_config_path = root / "book.yaml"
        self.book_dir = root / "book"
        self.write(PAPERS_YAML, DATASETS_YAML)
        self.book_config_path.write_text(BOOK_YAML, encoding="utf-8")
        self.cache_path.write_text(json.dumps(CACHE), encoding="utf-8")
        self.settings = catalogue.Settings(
            catalogue_dir=self.catalogue_dir,
            book_config_path=self.book_config_path,
            cache_path=self.cache_path,
            book_dir=self.book_dir,
            request_interval_s=0,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, papers: str, datasets: str, methods: str = METHODS_YAML) -> None:
        for name, text in (
            ("papers.yaml", papers),
            ("datasets.yaml", datasets),
            ("methods.yaml", methods),
        ):
            (self.catalogue_dir / name).write_text(
                textwrap.dedent(text), encoding="utf-8"
            )

    def assert_load_error(
        self, papers: str, datasets: str, message: str, methods: str = METHODS_YAML
    ) -> None:
        self.write(papers, datasets, methods)
        with self.assertRaisesRegex(catalogue.CatalogueError, message):
            self.settings.load()


class TestLoading(CatalogueDir):
    def test_loads_papers_and_pages_in_book_order(self) -> None:
        papers_file, pages = self.settings.load()
        self.assertEqual(
            list(papers_file.papers), ["Doe:2020abc", "Roe:2021xyz", "Manual:2015"]
        )
        self.assertEqual(
            [page.page.path.as_posix() for page in pages],
            ["part/datasets.md", "part/methods.md"],
        )
        datasets, methods = pages
        self.assertEqual([entry.id for entry in datasets.entries], ["jets"])
        (jets,) = datasets.entries
        self.assertEqual(
            jets.cells,
            {
                "content": "Tagged jets",
                "level": "Delphes",
                "size": "50,000 $t\\bar t$ events",
            },
        )
        self.assertEqual(jets.facts, {"simulation": "Pythia 8"})
        self.assertEqual(datasets.page.label, "Datasets")  # defaults to the title
        self.assertEqual(methods.page.label, "Methods")

    def test_book_page_order_reads_files_and_directories(self) -> None:
        self.book_config_path.write_text(
            BOOK_YAML + "      - {dir: deep, sections: [a.md]}\n", encoding="utf-8"
        )
        order = catalogue.book_page_order(self.book_config_path)
        self.assertEqual(order[-1], Path("part/deep/index.md"))

    def test_rejects_a_page_missing_from_the_book(self) -> None:
        self.book_config_path.write_text(
            BOOK_YAML.replace("      - methods.md\n", ""), encoding="utf-8"
        )
        with self.assertRaisesRegex(catalogue.CatalogueError, "not in"):
            self.settings.load()

    def test_rejects_bad_links_and_column_keys(self) -> None:
        self.assert_load_error(
            PAPERS_YAML,
            DATASETS_YAML,
            "not an https address",
            METHODS_YAML.replace("https://example.org/code", "ftp://example.org"),
        )
        self.assert_load_error(
            PAPERS_YAML,
            DATASETS_YAML,
            "are entry keys",
            METHODS_YAML.replace("{key: idea, title: Idea}", "{key: papers, title: P}"),
        )

    def test_rejects_a_use_of_an_unknown_page_or_entry(self) -> None:
        self.assert_load_error(
            PAPERS_YAML,
            DATASETS_YAML,
            "no entry 'nothing'",
            METHODS_YAML.replace("datasets:jets", "datasets:nothing"),
        )
        self.assert_load_error(
            PAPERS_YAML,
            DATASETS_YAML,
            "no catalogue page 'nowhere'",
            METHODS_YAML.replace("datasets:jets", "nowhere:jets"),
        )

    def test_rejects_a_record_defined_twice(self) -> None:
        methods = (
            METHODS_YAML.replace(
                "        papers: [Doe:2020abc]",
                "        papers: [Doe:2020abc]\n        data: [data:jets]",
            )
            + "records:\n  data:jets: {doi: 10.5281/zenodo.1}\n"
        )
        self.assert_load_error(PAPERS_YAML, DATASETS_YAML, "also in", methods)

    def test_rejects_an_unknown_category_tag_or_key(self) -> None:
        self.assert_load_error(
            PAPERS_YAML.replace("category: dataset", "category: tool"),
            DATASETS_YAML,
            "unknown category",
        )
        self.assert_load_error(
            PAPERS_YAML.replace("tags: [open data]", "tags: [optics]"),
            DATASETS_YAML,
            "unknown tags",
        )
        self.assert_load_error(
            PAPERS_YAML.replace("short: A method.", "short: A method.\n    note: x"),
            DATASETS_YAML,
            "unknown keys",
        )

    def test_rejects_a_manual_paper_without_bibtex(self) -> None:
        papers = PAPERS_YAML.split("    bibtex: |")[0]
        self.assert_load_error(papers, DATASETS_YAML, "missing 'bibtex'")

    def test_rejects_unknown_and_unused_references(self) -> None:
        self.assert_load_error(
            PAPERS_YAML,
            DATASETS_YAML.replace("papers: [Roe:2021xyz]", "papers: [Nobody:2000]"),
            "unknown paper",
        )
        extra = DATASETS_YAML + "  data:spare: {doi: 10.5281/zenodo.2}\n"
        self.assert_load_error(PAPERS_YAML, extra, "records no entry cites")

    def test_several_records_need_labels(self) -> None:
        datasets = DATASETS_YAML.replace(
            "data: [data:jets]", "data: [data:jets, data:more]"
        )
        datasets += "  data:more: {doi: 10.5281/zenodo.2}\n"
        self.assert_load_error(PAPERS_YAML, datasets, "needs a label")


class TestBuild(CatalogueDir):
    def test_cross_references(self) -> None:
        papers_file, pages = self.settings.load()
        resolved = catalogue.resolve(papers_file, pages, CACHE)
        self.assertEqual(resolved.cites["Doe:2020abc"], ["Roe:2021xyz"])
        self.assertEqual(resolved.cited_by["Roe:2021xyz"], ["Doe:2020abc"])
        self.assertEqual(resolved.cites["Roe:2021xyz"], [])
        introduced = {
            key: [(page.page.label, entry.id) for page, entry in pairs]
            for key, pairs in resolved.introduces.items()
        }
        self.assertEqual(
            introduced,
            {
                "Roe:2021xyz": [("Datasets", "jets")],
                "Doe:2020abc": [("Methods", "a-method")],
            },
        )

    def test_build_writes_pages_and_bibtex_then_is_up_to_date(self) -> None:
        written = catalogue.build(self.settings)
        names = sorted(path.relative_to(self.book_dir).as_posix() for path in written)
        self.assertEqual(
            names,
            [
                "part/datasets.bib",
                "part/datasets.md",
                "part/methods.bib",
                "part/methods.md",
                "part/papers.bib",
                "part/papers.md",
            ],
        )
        self.assertEqual(catalogue.build(self.settings, check=True), [])

        datasets_page = (self.book_dir / "part/datasets.md").read_text(encoding="utf-8")
        self.assertIn('<tr class="row" id="jets">', datasets_page)
        self.assertIn('aria-controls="jets-detail"', datasets_page)
        self.assertIn(
            '<tr class="row-detail" id="jets-detail"><td colspan="5">', datasets_page
        )
        self.assertIn(
            '<table class="catalogue-table catalogue-entries">', datasets_page
        )
        self.assertIn('href="papers.md#roe-2021xyz"', datasets_page)
        self.assertIn('data-cite="Roe:2021xyz data:jets"', datasets_page)
        self.assertIn(
            '<button type="button" class="share-button" data-share="jets">',
            datasets_page,
        )
        self.assertIn("\\(t\\bar t\\)", datasets_page)
        self.assertIn('class="ref-related"', datasets_page)

        papers_page = (self.book_dir / "part/papers.md").read_text(encoding="utf-8")
        self.assertIn("## Methods {: #methods }", papers_page)
        self.assertIn(
            '<tr class="row" id="roe-2021xyz" data-tags="open data">', papers_page
        )
        self.assertNotIn("<table>", papers_page)  # bookspine would wrap a bare table
        self.assertIn('class="share-button" data-share="roe-2021xyz"', papers_page)
        self.assertIn("ATLAS Collaboration (2900 authors)", papers_page)
        self.assertIn('href="datasets.md#jets"', papers_page)
        self.assertIn('<th>Methods</th><td><a href="methods.md#a-method">', papers_page)
        # Doe and the manual paper have venues; Roe has none and gets no line.
        self.assertEqual(papers_page.count("<th>Venue</th>"), 2)
        self.assertIn("refersto%3Arecid%3A101", papers_page)
        self.assertNotIn("refersto%3Arecid%3ANone", papers_page)

        methods_page = (self.book_dir / "part/methods.md").read_text(encoding="utf-8")
        # The entry gives no year, so the row takes its paper's.
        self.assertIn("<td>An idea</td><td>2020</td></tr>", methods_page)
        self.assertIn('<a href="https://example.org/code">Code</a>', methods_page)
        self.assertIn("<th>Method</th><th>Idea</th><th>Year</th>", methods_page)
        # The use is linked both ways, on the using row and on the dataset's row,
        # with the math of the linked names marked for MathJax.
        self.assertIn(
            '<p class="row-uses">Uses: <a href="datasets.md#jets">Jets</a></p>',
            methods_page,
        )
        self.assertIn(
            '<p class="row-uses">Used in Method Catalogue: '
            '<a href="methods.md#a-method">A \\(k_T\\) method</a></p>',
            datasets_page,
        )

        bib = (self.book_dir / "part/datasets.bib").read_text(encoding="utf-8")
        self.assertEqual(
            list(catalogue.split_bibtex(bib)),
            ["Roe:2021xyz", "data:jets", "Doe:2020abc"],
        )

    def test_detail_rows_keep_markdown_between_blank_lines(self) -> None:
        # CommonMark converts the details only if blank lines separate them from
        # the row's HTML; without them the bullets would show as raw text.
        catalogue.build(self.settings)
        page = (self.book_dir / "part/datasets.md").read_text(encoding="utf-8")
        self.assertIn('<td colspan="5">\n\nJets for tagging.\n\n', page)
        self.assertIn("- **Note:** details.\n\n", page)
        self.assertIn("\n\n</td></tr>", page)

    def test_check_reports_a_stale_page(self) -> None:
        catalogue.build(self.settings)
        self.write(
            PAPERS_YAML.replace("short: A method.", "short: A changed method."),
            DATASETS_YAML,
        )
        stale = catalogue.build(self.settings, check=True)
        self.assertEqual([path.name for path in stale], ["papers.md"])

    def test_build_needs_fetched_metadata(self) -> None:
        self.cache_path.write_text(
            json.dumps({"papers": {}, "records": {}}), encoding="utf-8"
        )
        with self.assertRaisesRegex(catalogue.CatalogueError, "not fetched"):
            catalogue.build(self.settings)


class TestFetch(CatalogueDir):
    def test_fetch_paper_reads_inspire(self) -> None:
        record = {
            "metadata": {
                "titles": [{"title": "A method paper"}],
                "authors": [{"full_name": "Doe, Jane"}, {"full_name": "Roe, Richard"}],
                "author_count": 2,
                "publication_info": [
                    {
                        "journal_title": "Phys.Rev.D",
                        "journal_volume": "1",
                        "year": 2020,
                        "artid": "2",
                    }
                ],
                "arxiv_eprints": [{"value": "2001.00001"}],
                "references": [
                    {"record": {"$ref": "https://inspirehep.net/api/literature/102"}},
                    {"record": {"$ref": "https://inspirehep.net/api/literature/101"}},
                    {"reference": {"title": "unmatched"}},
                ],
            }
        }
        responses = iter([json.dumps(record), INSPIRE_ENTRY])
        papers_file, _ = self.settings.load()
        with mock.patch.object(catalogue, "_get", lambda url, accept: next(responses)):
            meta = catalogue.fetch_paper(papers_file.papers["Doe:2020abc"], 0)
        self.assertEqual(meta.venue, "Phys. Rev. D 1 (2020) 2")
        self.assertEqual(meta.references, (102,))  # the record's own id is dropped
        self.assertEqual(meta.year, 2020)

    def test_fetch_paper_rejects_a_changed_texkey(self) -> None:
        record = {"metadata": {"titles": [{"title": "T"}]}}
        responses = iter(
            [json.dumps(record), INSPIRE_ENTRY.replace("Doe:2020abc", "Doe:2020zzz")]
        )
        papers_file, _ = self.settings.load()
        with (
            mock.patch.object(catalogue, "_get", lambda url, accept: next(responses)),
            self.assertRaisesRegex(catalogue.CatalogueError, "use that key"),
        ):
            catalogue.fetch_paper(papers_file.papers["Doe:2020abc"], 0)

    def test_fetch_record_reads_datacite(self) -> None:
        csl = {
            "title": "ATLAS open data",
            "author": [{"literal": "ATLAS Collaboration"}],
            "publisher": "CERN Open Data Portal",
            "issued": {"date-parts": [[2024]]},
            "URL": "http://opendata.cern.ch/record/1",
        }
        responses = iter([json.dumps(csl), DATACITE_ENTRY])
        _, pages = self.settings.load()
        with mock.patch.object(catalogue, "_get", lambda url, accept: next(responses)):
            meta = catalogue.fetch_record(pages[0].records["data:jets"], 0)
        self.assertEqual(meta.collaboration, "ATLAS")
        self.assertEqual(meta.authors, ())
        self.assertEqual(meta.year, 2024)
        self.assertTrue(meta.bibtex.startswith("@misc{data:jets,"))


if __name__ == "__main__":
    unittest.main()
