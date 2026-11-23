"""Build the catalogue pages of the book from paper records and entry pages.

``catalogue/papers.yaml`` holds one record per paper. Every other YAML file in
``catalogue/`` is one page of entries, such as datasets or methods: it declares the
columns of its tables and holds the entries, each citing papers of ``papers.yaml`` and,
for datasets, the data records that hold the files. These files carry only what a person
writes. The bibliographic metadata (titles, authors, journals, identifiers, BibTeX and
reference lists) comes from INSPIRE-HEP for papers and from DataCite for data records.
``fetch`` stores it in ``catalogue/cache.json``, and ``build`` reads that cache, so a
build never needs the network.

Usage::

    python scripts/catalogue.py fetch            # fetch what the cache lacks
    python scripts/catalogue.py fetch --refresh  # fetch everything again
    python scripts/catalogue.py build            # write the pages
    python scripts/catalogue.py build --check    # fail if a page is out of date
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---- Paths -------------------------------------------------------------------

#: The repository root: this script lives in ``scripts/``.
REPO_DIR = Path(__file__).resolve().parent.parent
#: The catalogue sources.
CATALOGUE_DIR = REPO_DIR / "catalogue"
#: Metadata fetched from INSPIRE-HEP and DataCite.
CACHE_PATH = CATALOGUE_DIR / "cache.json"
#: The book's sources, which ``bookspine build`` reads.
BOOK_DIR = REPO_DIR / "book"
#: The book's configuration, whose parts list the pages in reading order.
BOOK_CONFIG_PATH = REPO_DIR / "book.yaml"

# ---- Remote services ---------------------------------------------------------

#: An INSPIRE literature record, as JSON or, with ``format=bibtex``, as BibTeX.
#: https://github.com/inspirehep/rest-api-doc#changing-formats
INSPIRE_RECORD_API = "https://inspirehep.net/api/literature/{recid}"
INSPIRE_FIELDS = (
    "titles,authors.full_name,author_count,collaborations,publication_info,"
    "arxiv_eprints,dois,preprint_date,earliest_date,references.record"
)
#: The page of an INSPIRE record, and the search for the papers that cite it.
#: https://help.inspirehep.net/knowledge-base/inspire-paper-search/
INSPIRE_RECORD_URL = "https://inspirehep.net/literature/{recid}"
INSPIRE_SEARCH_URL = "https://inspirehep.net/literature?q={query}"
INSPIRE_CITED_BY_QUERY = "refersto:recid:{recid}"
#: A DOI resolved through content negotiation, which DataCite answers with BibTeX or
#: CSL-JSON. https://support.datacite.org/docs/datacite-content-resolver
DOI_URL = "https://doi.org/{doi}"
BIBTEX_MEDIA_TYPE = "application/x-bibtex"
CSL_JSON_MEDIA_TYPE = "application/vnd.citationstyles.csl+json"
#: The abstract page of an arXiv e-print. https://info.arxiv.org/help/arxiv_identifier.html
ARXIV_URL = "https://arxiv.org/abs/{eprint}"
#: INSPIRE allows 15 requests in 5 s from one address; one request every 0.4 s stays
#: inside the limit. https://github.com/inspirehep/rest-api-doc#rate-limiting
REQUEST_INTERVAL_S = 0.4
REQUEST_TIMEOUT_S = 60

# ---- Citations ---------------------------------------------------------------

#: Fields DataCite adds to BibTeX that a citation does not need.
DROPPED_BIBTEX_FIELDS = ("copyright", "keywords")
#: Authors kept in the cache and listed on a card; the count gives the rest.
AUTHOR_LIMIT = 10
#: Authors named in a citation before "et al." takes over.
CITATION_AUTHOR_LIMIT = 3

# ---- Page --------------------------------------------------------------------

PAPER_LABEL = "Paper"
DATA_LABEL = "Data"
CITED_BY_LABEL = "Cited by"
#: The lines of a detail row that link entries on different pages: what an entry uses,
#: such as a method's datasets, and, page by page, which entries use it.
USES_LABEL = "Uses"
USED_IN_LABEL = "Used in"
#: The last column of every entries table.
YEAR_COLUMN = "Year"
#: The columns of the papers tables; every other fact appears when a row is opened.
PAPER_COLUMNS = ("Paper", "Authors", "Year")
#: The file of paper records; every other YAML file in the catalogue is a page.
PAPERS_FILE_NAME = "papers.yaml"
#: The keys each kind of mapping in the catalogue files may hold.
PAPERS_FILE_KEYS = {"page", "intro", "categories", "tags", "papers"}
ENTRIES_FILE_KEYS = {"page", "intro", "table", "sections", "see_also", "records"}
PAGE_KEYS = {"path", "title", "label", "summary"}
TABLE_KEYS = {"name", "columns", "facts"}
COLUMN_KEYS = {"key", "title"}
PAPER_KEYS = {
    "inspire",
    "category",
    "tags",
    "short",
    "long",
    "venue",
    "title",
    "authors",
    "year",
    "url",
    "bibtex",
}
RECORD_KEYS = {
    "doi",
    "label",
    "title",
    "creators",
    "publisher",
    "year",
    "url",
    "bibtex",
}
SECTION_KEYS = {"title", "id", "entries"}
#: The keys every entry may hold; the page's own columns and facts add theirs.
ENTRY_KEYS = {
    "id",
    "name",
    "year",
    "summary",
    "papers",
    "data",
    "related",
    "details",
    "links",
    "uses",
}


class CatalogueError(Exception):
    """A catalogue file is malformed or refers to something that does not exist."""


# ---- BibTeX ------------------------------------------------------------------


def split_bibtex(text: str) -> dict[str, str]:
    """Split BibTeX source into its entries.

    Parameters
    ----------
    text : str
        BibTeX source. Text outside entries, such as ``%`` comments, is ignored.

    Returns
    -------
    dict of str to str
        Each entry's source, from ``@`` to its closing brace, by citation key, in the
        order of the source.

    Raises
    ------
    ValueError
        If an entry's braces do not close.
    """
    entries: dict[str, str] = {}
    for match in re.finditer(r"@\s*(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        depth, end = 0, None
        for index in range(text.index("{", match.start()), len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end is None:
            raise ValueError(f"unclosed BibTeX entry {match.group(2)!r}")
        entries[match.group(2)] = text[match.start() : end]
    return entries


def bibtex_field(entry: str, name: str) -> str | None:
    """Return the value of field `name` in `entry`, without its delimiters.

    A value is written either as ``name = "value"`` or as ``name = {value}``, and a
    value in braces may hold balanced braces of its own.
    """
    match = re.search(rf"\b{name}\s*=\s*([\"{{])", entry, re.IGNORECASE)
    if match is None:
        return None
    start = match.end()
    if match.group(1) == '"':
        return entry[start : entry.index('"', start)]
    depth = 1
    for index in range(start, len(entry)):
        if entry[index] == "{":
            depth += 1
        elif entry[index] == "}":
            depth -= 1
            if depth == 0:
                return entry[start:index]
    return None


def set_bibtex_field(entry: str, name: str, value: str) -> str:
    """Return `entry` with field `name` set to ``{value}``, added if it is absent."""
    current = bibtex_field(entry, name)
    match = re.search(rf"\b{name}\s*=\s*", entry, re.IGNORECASE)
    if current is None or match is None:
        return re.sub(r"\s*\}$", f",\n  {name} = {{{value}}}\n}}", entry)
    start = match.end()
    # The current value sits between two delimiters, quotes or braces.
    return entry[:start] + "{" + value + "}" + entry[start + len(current) + 2 :]


def normalise_entry(entry: str, key: str) -> str:
    """Give `entry` the citation key `key`, drop unneeded fields and protect its title.

    DataCite keys its entries by the DOI's URL and adds a copyright field; INSPIRE's
    entries keep their own key, which is the record's texkey.
    """
    entry = re.sub(
        r"^@\s*(\w+)\s*\{\s*[^,\s]+\s*,", rf"@\1{{{key},", entry.strip(), count=1
    )
    for name in DROPPED_BIBTEX_FIELDS:
        entry = re.sub(
            rf"\n\s*{name}\s*=\s*\{{[^\n]*\}},?", "", entry, flags=re.IGNORECASE
        )
    # Bibliography styles lower-case a title unless braces protect it, which would
    # turn "Top Quark Tagging Reference Dataset" into "Top quark tagging ...".
    title = bibtex_field(entry, "title")
    if title is not None and not title.startswith("{"):
        entry = entry.replace(f"{{{title}}}", f"{{{{{title}}}}}", 1)
    return re.sub(r",(\s*)\}$", r"\1}", entry)


# ---- Names and citations -----------------------------------------------------


def initials(given: str) -> str:
    """Abbreviate given names: ``"Jean-Roch"`` gives ``"J.-R."``, ``"Eric A."`` ``"E. A."``."""
    words = []
    for word in given.split():
        parts = [part for part in word.split("-") if part]
        words.append("-".join(f"{part[0]}." for part in parts))
    return " ".join(words)


def short_name(full_name: str) -> str:
    """Turn ``"Last, First"`` into ``"F. Last"``; leave any other form as it is."""
    if "," not in full_name:
        return full_name.strip()
    last, given = (part.strip() for part in full_name.split(",", 1))
    return f"{initials(given)} {last}".strip()


def format_authors(
    authors: Iterable[str],
    count: int,
    collaboration: str = "",
    limit: int = CITATION_AUTHOR_LIMIT,
) -> str:
    """Return the author list of a citation.

    A collaboration paper is cited by the collaboration's name; up to `limit` authors
    are named in full; beyond that the first author is followed by "et al.".
    """
    if collaboration:
        return f"{collaboration} Collaboration"
    names = [short_name(name) for name in authors]
    if not names:
        return ""
    if count > limit or len(names) > limit:
        return f"{names[0]} et al."
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def journal_reference(info: dict) -> str:
    """Return ``"Journal Volume (Year) Article"`` from an INSPIRE publication record."""
    title = info.get("journal_title")
    if not title:
        return ""
    title = re.sub(r"\.(?=[A-Z])", ". ", title)
    volume = info.get("journal_volume", "")
    year = info.get("year", "")
    article = info.get("artid") or info.get("page_start") or ""
    parts = [title, volume, f"({year})" if year else "", article]
    return " ".join(part for part in parts if part)


# ---- Model -------------------------------------------------------------------


@dataclass(frozen=True)
class Metadata:
    """The bibliographic facts of a paper or a data record."""

    title: str
    authors: tuple[str, ...] = ()
    author_count: int = 0
    collaboration: str = ""
    year: int | None = None
    venue: str = ""
    arxiv: str = ""
    doi: str = ""
    url: str = ""
    bibtex: str = ""
    references: tuple[int, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> Metadata:
        """Rebuild metadata stored in the cache."""
        data = dict(data)
        data["authors"] = tuple(data.get("authors", ()))
        data["references"] = tuple(data.get("references", ()))
        return cls(**data)

    def citation(self) -> str:
        """Return the citation as plain text, with its title in quotation marks."""
        authors = format_authors(self.authors, self.author_count, self.collaboration)
        venue = self.venue
        # A journal reference holds its year; a publisher such as Zenodo does not.
        if venue and self.year and str(self.year) not in venue:
            venue = f"{venue} ({self.year})"
        parts = [authors, f"“{self.title}”", venue]
        if self.arxiv:
            parts.append(f"arXiv:{self.arxiv}")
        elif self.doi and not self.venue.endswith(self.doi):
            parts.append(f"doi:{self.doi}")
        return ", ".join(part for part in parts if part) + "."


@dataclass(frozen=True)
class Paper:
    """A paper of the guide: what a person writes about it, and where it is found."""

    key: str
    category: str
    tags: tuple[str, ...]
    short: str
    long: str = ""
    inspire: int | None = None
    venue: str = ""
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    url: str = ""
    bibtex: str = ""

    @property
    def anchor(self) -> str:
        """The paper's card anchor on the papers page."""
        return slug(self.key)


@dataclass(frozen=True)
class Record:
    """A data record: an archive holding a dataset's files."""

    key: str
    label: str = ""
    doi: str = ""
    title: str = ""
    creators: tuple[str, ...] = ()
    publisher: str = ""
    year: int | None = None
    url: str = ""
    bibtex: str = ""


@dataclass(frozen=True)
class Entry:
    """One row of an entries page: a dataset, a method or a model."""

    id: str
    name: str
    summary: str
    papers: tuple[str, ...]
    year: int | None = None
    data: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    details: str = ""
    cells: dict[str, str] = field(default_factory=dict)
    facts: dict[str, str] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    uses: tuple[str, ...] = ()

    @property
    def cited(self) -> tuple[str, ...]:
        """What a result obtained with this entry cites: its papers and records."""
        return self.papers + self.data


@dataclass(frozen=True)
class Section:
    """A group of entries under one heading."""

    title: str
    id: str
    entries: tuple[Entry, ...]


@dataclass(frozen=True)
class Column:
    """A value every entry of a page gives, under a title."""

    key: str
    title: str


@dataclass(frozen=True)
class Table:
    """The shape of a page's tables.

    The first column holds the entry's name under the title `name`, the last its year;
    `columns` lie between. `facts` are shown, with their titles, when a row is opened.
    """

    name: str
    columns: tuple[Column, ...] = ()
    facts: tuple[Column, ...] = ()


@dataclass(frozen=True)
class Page:
    """Where a catalogue page goes and what it says at its head.

    `label` names the page in the rows of the papers it cites, as in "Datasets".
    """

    path: Path
    title: str
    label: str
    summary: str
    intro: str


@dataclass(frozen=True)
class PapersFile:
    """The contents of ``papers.yaml``."""

    source_path: Path
    page: Page
    categories: dict[str, str]
    tags: tuple[str, ...]
    papers: dict[str, Paper]


@dataclass(frozen=True)
class EntriesFile:
    """The contents of one entries page's YAML file."""

    source_path: Path
    page: Page
    table: Table
    sections: tuple[Section, ...]
    records: dict[str, Record]
    see_also: str = ""

    @property
    def entries(self) -> Iterator[Entry]:
        """Every entry, in page order."""
        for section in self.sections:
            yield from section.entries

    @property
    def name(self) -> str:
        """The page's name in entry references, as in ``datasets:jetclass``."""
        return self.source_path.stem


def find_entry(pages: Iterable[EntriesFile], ref: str) -> tuple[EntriesFile, Entry]:
    """Return the page and entry a reference ``page:id`` names.

    Raises
    ------
    CatalogueError
        If no page has that name, or the page has no entry with that id.
    """
    name, _, entry_id = ref.partition(":")
    for page in pages:
        if page.name == name:
            for entry in page.entries:
                if entry.id == entry_id:
                    return page, entry
            raise CatalogueError(
                f"{page.source_path}: no entry {entry_id!r} for {ref!r}"
            )
    raise CatalogueError(f"no catalogue page {name!r} for {ref!r}")


# ---- Loading -----------------------------------------------------------------


def _mapping(value: object, allowed: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        raise CatalogueError(f"{where}: expected a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise CatalogueError(f"{where}: unknown keys {sorted(unknown)}")
    return value


def _required(mapping: dict, name: str, where: str) -> Any:
    if mapping.get(name) in (None, "", [], {}):
        raise CatalogueError(f"{where}: missing {name!r}")
    return mapping[name]


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _page(raw: dict, where: str) -> Page:
    page = _mapping(_required(raw, "page", where), PAGE_KEYS, f"{where}: page")
    return Page(
        path=Path(str(_required(page, "path", f"{where}: page"))),
        title=str(_required(page, "title", f"{where}: page")),
        label=str(page.get("label") or page["title"]),
        summary=str(_required(page, "summary", f"{where}: page")),
        intro=str(_required(raw, "intro", where)).strip(),
    )


def load_papers(path: Path) -> PapersFile:
    """Read and check ``papers.yaml``.

    Raises
    ------
    CatalogueError
        On an unknown or missing key, an unknown category or tag, or a paper with
        neither an INSPIRE record nor a title, authors, year and BibTeX of its own.
    """
    where = str(path)
    raw = _mapping(_read_yaml(path), PAPERS_FILE_KEYS, where)
    categories = {
        str(_required(item, "id", where)): str(_required(item, "title", where))
        for item in _required(raw, "categories", where)
    }
    tags = tuple(str(tag) for tag in _required(raw, "tags", where))
    papers = {}
    for key, spec in _required(raw, "papers", where).items():
        paper_where = f"{where}: paper {key}"
        spec = _mapping(spec, PAPER_KEYS, paper_where)
        paper = Paper(
            key=key,
            category=str(_required(spec, "category", paper_where)),
            tags=tuple(spec.get("tags") or ()),
            short=str(_required(spec, "short", paper_where)).strip(),
            long=str(spec.get("long") or "").strip(),
            inspire=spec.get("inspire"),
            venue=str(spec.get("venue") or ""),
            title=str(spec.get("title") or ""),
            authors=tuple(spec.get("authors") or ()),
            year=spec.get("year"),
            url=str(spec.get("url") or ""),
            bibtex=str(spec.get("bibtex") or "").strip(),
        )
        if paper.category not in categories:
            raise CatalogueError(f"{paper_where}: unknown category {paper.category!r}")
        unknown_tags = set(paper.tags) - set(tags)
        if unknown_tags:
            raise CatalogueError(f"{paper_where}: unknown tags {sorted(unknown_tags)}")
        if paper.inspire is None:
            for name in ("title", "authors", "year", "bibtex"):
                _required(spec, name, f"{paper_where} (no INSPIRE record)")
            if key not in split_bibtex(paper.bibtex):
                raise CatalogueError(
                    f"{paper_where}: its bibtex must use the key {key!r}"
                )
        papers[key] = paper
    return PapersFile(path, _page(raw, where), categories, tags, papers)


def _columns(items: Any, where: str) -> tuple[Column, ...]:
    columns = []
    for item in items or ():
        item = _mapping(item, COLUMN_KEYS, where)
        key = str(_required(item, "key", where))
        columns.append(Column(key, str(_required(item, "title", where))))
    return tuple(columns)


def _records(raw: Any, where: str) -> dict[str, Record]:
    records = {}
    for key, spec in (raw or {}).items():
        record_where = f"{where}: record {key}"
        spec = _mapping(spec, RECORD_KEYS, record_where)
        record = Record(
            key=key,
            label=str(spec.get("label") or ""),
            doi=str(spec.get("doi") or ""),
            title=str(spec.get("title") or ""),
            creators=tuple(spec.get("creators") or ()),
            publisher=str(spec.get("publisher") or ""),
            year=spec.get("year"),
            url=str(spec.get("url") or ""),
            bibtex=str(spec.get("bibtex") or "").strip(),
        )
        if not record.doi:
            for name in ("title", "publisher", "url", "bibtex"):
                _required(spec, name, f"{record_where} (no DOI)")
            if key not in split_bibtex(record.bibtex):
                raise CatalogueError(
                    f"{record_where}: its bibtex must use the key {key!r}"
                )
        records[key] = record
    return records


def _entry(raw: Any, table: Table, where: str) -> Entry:
    own = {column.key for column in table.columns + table.facts}
    raw = _mapping(raw, ENTRY_KEYS | own, where)
    entry_id = str(_required(raw, "id", where))
    where = f"{where}: entry {entry_id}"
    links = _mapping(raw.get("links") or {}, set(raw.get("links") or {}), where)
    for label, url in links.items():
        if not str(url).startswith("https://"):
            raise CatalogueError(f"{where}: link {label!r} is not an https address")
    return Entry(
        id=entry_id,
        name=str(_required(raw, "name", where)),
        summary=str(_required(raw, "summary", where)).strip(),
        papers=tuple(_required(raw, "papers", where)),
        year=int(raw["year"]) if raw.get("year") else None,
        data=tuple(raw.get("data") or ()),
        related=tuple(raw.get("related") or ()),
        details=str(raw.get("details") or "").strip(),
        cells={column.key: str(raw.get(column.key) or "") for column in table.columns},
        facts={
            column.key: str(raw[column.key])
            for column in table.facts
            if raw.get(column.key)
        },
        links={str(label): str(url) for label, url in links.items()},
        uses=tuple(str(ref) for ref in raw.get("uses") or ()),
    )


def load_entries(path: Path, papers: dict[str, Paper]) -> EntriesFile:
    """Read and check one entries page against the papers it cites.

    Raises
    ------
    CatalogueError
        On an unknown or missing key, a column whose key an entry already uses, a
        repeated entry id, a citation of an unknown paper or record, a record no entry
        cites, an entry with several records of which one has no label, or a link that
        is not an https address.
    """
    where = str(path)
    raw = _mapping(_read_yaml(path), ENTRIES_FILE_KEYS, where)
    spec = _mapping(_required(raw, "table", where), TABLE_KEYS, f"{where}: table")
    table = Table(
        name=str(_required(spec, "name", f"{where}: table")),
        columns=_columns(spec.get("columns"), f"{where}: table columns"),
        facts=_columns(spec.get("facts"), f"{where}: table facts"),
    )
    clash = {column.key for column in table.columns + table.facts} & ENTRY_KEYS
    if clash:
        raise CatalogueError(f"{where}: table keys {sorted(clash)} are entry keys")
    records = _records(raw.get("records"), where)

    sections: list[Section] = []
    seen: set[str] = set()
    cited: set[str] = set()
    for number, raw_section in enumerate(_required(raw, "sections", where), 1):
        section_where = f"{where}: section {number}"
        raw_section = _mapping(raw_section, SECTION_KEYS, section_where)
        entries = []
        for raw_entry in _required(raw_section, "entries", section_where):
            entry = _entry(raw_entry, table, section_where)
            entry_where = f"{where}: entry {entry.id}"
            if entry.id in seen:
                raise CatalogueError(f"{entry_where}: id used twice")
            seen.add(entry.id)
            for key in entry.papers:
                if key not in papers:
                    raise CatalogueError(f"{entry_where}: unknown paper {key!r}")
            for key in entry.data:
                if key not in records:
                    raise CatalogueError(f"{entry_where}: unknown record {key!r}")
            for key in entry.related:
                if key not in papers and key not in records:
                    raise CatalogueError(f"{entry_where}: unknown reference {key!r}")
            if len(entry.data) > 1 and not all(
                records[key].label for key in entry.data
            ):
                raise CatalogueError(
                    f"{entry_where}: each of several data records needs a label"
                )
            cited.update(entry.data, entry.related)
            entries.append(entry)
        title = str(_required(raw_section, "title", section_where))
        section_id = str(raw_section.get("id") or slug(title))
        sections.append(Section(title, section_id, tuple(entries)))

    unused = set(records) - cited
    if unused:
        raise CatalogueError(f"{where}: records no entry cites: {sorted(unused)}")
    return EntriesFile(
        source_path=path,
        page=_page(raw, where),
        table=table,
        sections=tuple(sections),
        records=records,
        see_also=str(raw.get("see_also") or "").strip(),
    )


def book_page_order(book_config_path: Path) -> list[Path]:
    """Return the pages of ``book.yaml`` in reading order, as paths under ``book/``."""
    config = _read_yaml(book_config_path) or {}
    order = []
    for part in config.get("parts") or ():
        for chapter in part.get("chapters") or ():
            if isinstance(chapter, str):
                order.append(Path(part["dir"]) / chapter)
            elif isinstance(chapter, dict) and chapter.get("dir"):
                order.append(Path(part["dir"]) / chapter["dir"] / "index.md")
    return order


def slug(text: str) -> str:
    """Return a lower-case, hyphenated anchor for `text`."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def display_path(path: Path) -> str:
    """Return `path` relative to the repository, or as given if it lies outside."""
    try:
        return path.resolve().relative_to(REPO_DIR).as_posix()
    except ValueError:
        return path.as_posix()


# ---- Fetching ----------------------------------------------------------------


def _get(url: str, accept: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        return response.read().decode("utf-8")


def fetch_paper(paper: Paper, interval_s: float) -> Metadata:
    """Fetch a paper's metadata and BibTeX from its INSPIRE record."""
    url = INSPIRE_RECORD_API.format(recid=paper.inspire)
    record = json.loads(_get(f"{url}?fields={INSPIRE_FIELDS}", "application/json"))
    meta = record["metadata"]
    time.sleep(interval_s)
    entries = split_bibtex(_get(f"{url}?format=bibtex", BIBTEX_MEDIA_TYPE))
    time.sleep(interval_s)
    if list(entries) != [paper.key]:
        raise CatalogueError(
            f"paper {paper.key}: INSPIRE record {paper.inspire} has the key "
            f"{list(entries)}; use that key in papers.yaml"
        )
    info: dict = next(
        (
            item
            for item in meta.get("publication_info", [])
            if item.get("journal_title")
        ),
        {},
    )
    date = meta.get("preprint_date") or meta.get("earliest_date") or ""
    references = sorted(
        {
            int(item["record"]["$ref"].rsplit("/", 1)[1])
            for item in meta.get("references", [])
            if "record" in item
        }
        - {paper.inspire}
    )
    collaborations = meta.get("collaborations") or []
    return Metadata(
        title=meta["titles"][0]["title"],
        authors=tuple(a["full_name"] for a in meta.get("authors", [])[:AUTHOR_LIMIT]),
        author_count=int(meta.get("author_count") or len(meta.get("authors", []))),
        collaboration=collaborations[0]["value"] if collaborations else "",
        year=int(info.get("year") or date[:4]) if (info.get("year") or date) else None,
        venue=journal_reference(info),
        arxiv=(meta.get("arxiv_eprints") or [{}])[0].get("value", ""),
        doi=(meta.get("dois") or [{}])[0].get("value", ""),
        bibtex=normalise_entry(entries[paper.key], paper.key),
        references=tuple(references),
    )


def fetch_record(record: Record, interval_s: float) -> Metadata:
    """Fetch a data record's metadata and BibTeX from DataCite."""
    url = DOI_URL.format(doi=record.doi)
    csl = json.loads(_get(url, CSL_JSON_MEDIA_TYPE))
    time.sleep(interval_s)
    entries = split_bibtex(_get(url, BIBTEX_MEDIA_TYPE))
    time.sleep(interval_s)
    if len(entries) != 1:
        raise CatalogueError(
            f"record {record.key}: expected one BibTeX entry from {url}"
        )
    creators = []
    for author in csl.get("author", []):
        if "literal" in author:
            creators.append(author["literal"])
        else:
            creators.append(
                f"{author.get('family', '')}, {author.get('given', '')}".strip(", ")
            )
    collaboration = ""
    if len(creators) == 1 and creators[0].endswith(" Collaboration"):
        collaboration = creators.pop().removesuffix(" Collaboration")
    issued = csl.get("issued", {}).get("date-parts", [[None]])[0][0]
    return Metadata(
        title=csl.get("title", ""),
        authors=tuple(creators[:AUTHOR_LIMIT]),
        author_count=len(creators),
        collaboration=collaboration,
        year=int(issued) if issued else None,
        venue=csl.get("publisher", ""),
        doi=record.doi,
        url=csl.get("URL", ""),
        bibtex=normalise_entry(next(iter(entries.values())), record.key),
    )


# ---- Resolved catalogue ------------------------------------------------------


@dataclass
class Catalogue:
    """The papers and entry pages with their metadata and cross-references resolved.

    `introduces` holds, for each paper, the entries citing it as their paper, with the
    page each is on.
    """

    papers_file: PapersFile
    pages: list[EntriesFile]
    metadata: dict[str, Metadata]
    cites: dict[str, list[str]] = field(default_factory=dict)
    cited_by: dict[str, list[str]] = field(default_factory=dict)
    introduces: dict[str, list[tuple[EntriesFile, Entry]]] = field(default_factory=dict)
    used_by: dict[str, list[tuple[EntriesFile, Entry]]] = field(default_factory=dict)

    @property
    def papers(self) -> dict[str, Paper]:
        """Every paper, by key."""
        return self.papers_file.papers

    @property
    def records(self) -> dict[str, Record]:
        """Every data record of every page, by key."""
        return {
            key: record for page in self.pages for key, record in page.records.items()
        }


def paper_metadata(paper: Paper, cache: dict) -> Metadata:
    """Return a paper's metadata: fetched, with the catalogue's own values on top."""
    if paper.inspire is None:
        return Metadata(
            title=paper.title,
            authors=paper.authors,
            author_count=len(paper.authors),
            year=paper.year,
            venue=paper.venue,
            url=paper.url,
            bibtex=paper.bibtex,
        )
    if paper.key not in cache:
        raise CatalogueError(
            f"paper {paper.key}: not fetched; run `catalogue.py fetch`"
        )
    meta = Metadata.from_dict(cache[paper.key])
    # Normalising again applies the current rules to entries fetched under older ones.
    meta = Metadata(
        **{**asdict(meta), "bibtex": normalise_entry(meta.bibtex, paper.key)}
    )
    if paper.venue:
        meta = Metadata(**{**asdict(meta), "venue": paper.venue})
    return meta


def record_metadata(record: Record, cache: dict) -> Metadata:
    """Return a record's metadata: fetched, with the catalogue's own values on top."""
    if not record.doi:
        return Metadata(
            title=record.title,
            authors=record.creators,
            author_count=len(record.creators),
            year=record.year,
            venue=record.publisher,
            url=record.url,
            bibtex=record.bibtex,
        )
    if record.key not in cache:
        raise CatalogueError(
            f"record {record.key}: not fetched; run `catalogue.py fetch`"
        )
    meta = Metadata.from_dict(cache[record.key])
    overrides: dict[str, object] = {}
    bibtex = normalise_entry(meta.bibtex, record.key)
    if record.title:
        overrides["title"] = record.title
        bibtex = set_bibtex_field(bibtex, "title", "{" + record.title + "}")
    if record.creators:
        overrides.update(
            authors=record.creators, author_count=len(record.creators), collaboration=""
        )
        bibtex = set_bibtex_field(bibtex, "author", " and ".join(record.creators))
    if record.year:
        overrides["year"] = record.year
        bibtex = set_bibtex_field(bibtex, "year", str(record.year))
    overrides["bibtex"] = bibtex
    return Metadata(**{**asdict(meta), **overrides})


def resolve(
    papers_file: PapersFile, pages: list[EntriesFile], cache: dict
) -> Catalogue:
    """Combine the catalogue files with the cache and work out the cross-references."""
    metadata = {
        key: paper_metadata(paper, cache.get("papers", {}))
        for key, paper in papers_file.papers.items()
    }
    for page in pages:
        for key, record in page.records.items():
            metadata[key] = record_metadata(record, cache.get("records", {}))
    catalogue = Catalogue(papers_file, pages, metadata)
    by_recid = {
        paper.inspire: key for key, paper in papers_file.papers.items() if paper.inspire
    }
    for key in papers_file.papers:
        cites = [
            by_recid[recid] for recid in metadata[key].references if recid in by_recid
        ]
        catalogue.cites[key] = sorted(
            cites, key=lambda other: _paper_order(catalogue, other)
        )
        for other in cites:
            catalogue.cited_by.setdefault(other, []).append(key)
    for key in catalogue.cited_by:
        catalogue.cited_by[key].sort(key=lambda other: _paper_order(catalogue, other))
    for page in pages:
        for entry in page.entries:
            for key in entry.papers:
                catalogue.introduces.setdefault(key, []).append((page, entry))
            for ref in entry.uses:
                catalogue.used_by.setdefault(ref, []).append((page, entry))
    return catalogue


def _paper_order(catalogue: Catalogue, key: str) -> tuple[int, str]:
    """Newest first, then by key."""
    return (-(catalogue.metadata[key].year or 0), key)


# ---- Links -------------------------------------------------------------------


def primary_url(meta: Metadata) -> str:
    """Where a reference is read: its arXiv abstract, else its DOI, else its URL."""
    if meta.arxiv:
        return ARXIV_URL.format(eprint=meta.arxiv)
    if meta.doi:
        return DOI_URL.format(doi=meta.doi)
    return meta.url


def cited_by_url(paper: Paper) -> str:
    """The INSPIRE search for the papers that cite `paper`, or nothing without a record."""
    if paper.inspire is None:
        return ""
    query = INSPIRE_CITED_BY_QUERY.format(recid=paper.inspire)
    return INSPIRE_SEARCH_URL.format(query=urllib.parse.quote(query, safe=""))


def html_text(text: str) -> str:
    """Escape `text` for HTML and mark its ``$…$`` math as ``\\(…\\)`` for MathJax.

    bookspine converts the math of Markdown text, but the HTML these pages are built
    from passes through untouched, and the theme's MathJax reads only ``\\(…\\)``.
    """
    return re.sub(r"\$([^$]+)\$", r"\\(\1\\)", html.escape(text))


def _a(href: str, label: str, css_class: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f'<a{class_attr} href="{html.escape(href)}">{html_text(label)}</a>'


def _cite_button(keys: Iterable[str], file_name: str) -> str:
    """A button that opens the cite menu for `keys`."""
    return (
        f'<button type="button" class="cite-button" aria-haspopup="menu" '
        f'aria-expanded="false" data-cite="{" ".join(keys)}" '
        f'data-file="{html.escape(file_name)}">Cite</button>'
    )


def _bibtex_button(key: str) -> str:
    """A button that copies the BibTeX of one reference."""
    return f'<button type="button" class="bib-copy" data-cite="{html.escape(key)}">BibTeX</button>'


def _citation_html(key: str, meta: Metadata) -> str:
    """A citation as HTML, marked so the cite menu can copy it as text."""
    text = meta.citation()
    return (
        f'<span class="citation" data-key="{html.escape(key)}" '
        f'data-text="{html.escape(text)}">{html_text(text)}</span>'
    )


# ---- Pages -------------------------------------------------------------------


def _front_matter(page: Page, source: Path) -> list[str]:
    front = yaml.safe_dump(
        {"title": page.title, "summary": page.summary},
        allow_unicode=True,
        sort_keys=False,
        width=1000,
    )
    return [
        "---",
        f"# Generated by scripts/catalogue.py from {display_path(source)}.",
        "# Edit that file, then run `python scripts/catalogue.py build`.",
        front.rstrip(),
        "---",
        "",
        page.intro,
        "",
    ]


def _tools(page: Page) -> str:
    """The page's tools: open every row, and download every reference as BibTeX."""
    name = page.path.with_suffix(".bib").name
    return (
        '<p class="catalogue-tools">'
        '<button type="button" class="expand-all" hidden>Expand all</button>'
        f'<a class="bib-all" href="{name}" download>Download all as BibTeX</a></p>'
    )


def _paper_link(catalogue: Catalogue, key: str, from_page: Page) -> str:
    """A link to a paper's row, labelled by its first author and year."""
    meta = catalogue.metadata[key]
    papers_page = catalogue.papers_file.page
    href = _relative(from_page, papers_page) + "#" + catalogue.papers[key].anchor
    return _a(href, f"{_first_author(meta)} ({meta.year})")


def _first_author(meta: Metadata) -> str:
    """The first author followed by "et al.", or the collaboration."""
    name = format_authors(meta.authors[:1], 1, meta.collaboration)
    if meta.author_count > 1 and not meta.collaboration:
        name += " et al."
    return name


def _relative(from_page: Page, to_page: Page) -> str:
    """A link from one page's Markdown file to another's, which bookspine resolves."""
    if from_page.path == to_page.path:
        return ""
    return Path(os.path.relpath(to_page.path, from_page.path.parent)).as_posix()


def _table(kind: str, columns: list[str], rows: list[str]) -> str:
    """The Markdown of a table whose rows each open a detail row beneath them.

    The table carries a class, so bookspine leaves it out of its export and enlarge
    tools, which would copy the detail rows as data.
    """
    head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    return "\n".join(
        [
            (
                f'<div class="table-wrap"><table class="catalogue-table catalogue-{kind}">'
                f"<thead><tr>{head}</tr></thead><tbody>"
            ),
            *rows,
            "</tbody></table></div>",
            "",
        ]
    )


def _main_row(row_id: str, cells: list[str], tags: str = "") -> str:
    """A table row whose first cell is the button that opens the detail row."""
    toggle = (
        f'<button type="button" class="row-toggle" aria-expanded="false" '
        f'aria-controls="{row_id}-detail">{cells[0]}</button>'
    )
    tags_attr = f' data-tags="{html.escape(tags)}"' if tags else ""
    tds = "".join(f"<td>{cell}</td>" for cell in [toggle, *cells[1:]])
    return f'<tr class="row" id="{row_id}"{tags_attr}>{tds}</tr>'


def _detail_row(row_id: str, span: int, body: list[str], tags: str = "") -> str:
    """A detail row whose cell holds Markdown blocks separated by blank lines.

    CommonMark ends an HTML block at a blank line, so the blocks between the cell's
    opening and closing tags are converted like the rest of the page, math included.
    """
    tags_attr = f' data-tags="{html.escape(tags)}"' if tags else ""
    blocks = "\n\n".join(block for block in body if block)
    return "\n".join(
        [
            (
                f'<tr class="row-detail" id="{row_id}-detail"{tags_attr}>'
                f'<td colspan="{span}">'
            ),
            "",
            blocks,
            "",
            "</td></tr>",
        ]
    )


def entry_rows_markdown(
    catalogue: Catalogue, page_file: EntriesFile, entry: Entry
) -> str:
    """Return an entry's table row and the detail row it opens."""
    page = page_file.page
    first = entry.papers[0]
    first_meta = catalogue.metadata[first]
    papers_href = _relative(page, catalogue.papers_file.page)
    links = [_a(papers_href + "#" + catalogue.papers[first].anchor, PAPER_LABEL)]
    if first_meta.arxiv:
        links.append(_a(primary_url(first_meta), "arXiv"))
    for key in entry.data:
        label = catalogue.records[key].label or DATA_LABEL
        links.append(_a(primary_url(catalogue.metadata[key]), label))
    for label, url in entry.links.items():
        links.append(_a(url, label))
    links.append(_cite_button(entry.cited, entry.id))
    if url := cited_by_url(catalogue.papers[first]):
        links.append(_a(url, CITED_BY_LABEL, "cited-by"))

    items = []
    for key in entry.cited + entry.related:
        role = "related" if key in entry.related else "cite"
        items.append(
            f'<li class="ref-{role}">{_citation_html(key, catalogue.metadata[key])} '
            f"{_bibtex_button(key)}</li>"
        )
    facts = "".join(
        f'<p class="row-facts">{html.escape(column.title)}: '
        f"{html_text(entry.facts[column.key])}</p>"
        for column in page_file.table.facts
        if column.key in entry.facts
    )
    body = [
        entry.summary,
        facts,
        _uses_html(catalogue, page_file, entry),
        entry.details,
        f'<p class="row-links">{"".join(links)}</p>',
        f'<p class="row-label">References</p><ul class="row-refs">{"".join(items)}</ul>',
    ]
    year = entry.year or first_meta.year
    cells = [
        entry.name,
        *(entry.cells[column.key] for column in page_file.table.columns),
        str(year or ""),
    ]
    main = _main_row(entry.id, [html_text(cell) for cell in cells])
    return main + "\n" + _detail_row(entry.id, len(cells), body)


def _uses_html(catalogue: Catalogue, page_file: EntriesFile, entry: Entry) -> str:
    """The lines linking an entry to the entries it uses and to those that use it.

    The entries that use it are listed one line per page, in reading order.
    """
    lines = []
    if entry.uses:
        targets = [find_entry(catalogue.pages, ref) for ref in entry.uses]
        links = ", ".join(
            _a(_relative(page_file.page, page.page) + "#" + target.id, target.name)
            for page, target in targets
        )
        lines.append(f'<p class="row-uses">{USES_LABEL}: {links}</p>')
    users = catalogue.used_by.get(f"{page_file.name}:{entry.id}", [])
    for user_page in catalogue.pages:
        names = [user for page, user in users if page is user_page]
        if names:
            href = _relative(page_file.page, user_page.page)
            links = ", ".join(_a(href + "#" + user.id, user.name) for user in names)
            title = html.escape(user_page.page.title)
            lines.append(f'<p class="row-uses">{USED_IN_LABEL} {title}: {links}</p>')
    return "".join(lines)


def entries_page_markdown(catalogue: Catalogue, page_file: EntriesFile) -> str:
    """Return the Markdown of an entries page: one table per section."""
    table = page_file.table
    columns = [table.name, *(column.title for column in table.columns), YEAR_COLUMN]
    lines = _front_matter(page_file.page, page_file.source_path)
    lines += [_tools(page_file.page), ""]
    for section in page_file.sections:
        rows = [entry_rows_markdown(catalogue, page_file, e) for e in section.entries]
        lines += [f"## {section.title} {{: #{section.id} }}", ""]
        lines += [_table("entries", columns, rows)]
    if page_file.see_also:
        lines += ["## See also {: #see-also }", "", page_file.see_also, ""]
    return "\n".join(lines).rstrip() + "\n"


def _paper_list(catalogue: Catalogue, keys: list[str], page: Page) -> str:
    return ", ".join(_paper_link(catalogue, key, page) for key in keys)


def paper_rows_markdown(catalogue: Catalogue, paper: Paper) -> str:
    """Return a paper's table row and the detail row it opens."""
    page = catalogue.papers_file.page
    meta = catalogue.metadata[paper.key]
    tags = "|".join(paper.tags)
    tags_html = "".join(
        f'<button type="button" class="tag" data-tag="{html.escape(tag)}">'
        f"{html.escape(tag)}</button>"
        for tag in paper.tags
    )

    links = []
    if meta.arxiv:
        links.append(_a(ARXIV_URL.format(eprint=meta.arxiv), "arXiv"))
    if meta.doi:
        links.append(_a(DOI_URL.format(doi=meta.doi), "Journal"))
    elif meta.url:
        links.append(_a(meta.url, "Proceedings"))
    if paper.inspire:
        links.append(_a(INSPIRE_RECORD_URL.format(recid=paper.inspire), "INSPIRE"))
    links.append(_cite_button([paper.key], paper.anchor))
    if url := cited_by_url(paper):
        links.append(_a(url, CITED_BY_LABEL, "cited-by"))

    facts: list[tuple[str, str]] = []
    # A paper INSPIRE knows no journal for gets no venue line rather than a guess.
    if meta.venue:
        venue = meta.venue
        if meta.year and str(meta.year) not in venue:
            venue = f"{venue} ({meta.year})"
        facts.append(("Venue", html_text(venue)))
    if meta.collaboration:
        authors = f"{meta.collaboration} Collaboration ({meta.author_count} authors)"
        facts.append(("Authors", html.escape(authors)))
    elif meta.authors:
        names = ", ".join(short_name(name) for name in meta.authors)
        more = meta.author_count - len(meta.authors)
        facts.append(
            ("Authors", html.escape(names + (f" and {more} more" if more > 0 else "")))
        )
    for entries_file in catalogue.pages:
        introduced = [
            entry
            for page_file, entry in catalogue.introduces.get(paper.key, ())
            if page_file is entries_file
        ]
        if introduced:
            href = _relative(page, entries_file.page)
            names = ", ".join(_a(href + "#" + e.id, e.name) for e in introduced)
            facts.append((entries_file.page.label, names))
    if cites := catalogue.cites.get(paper.key):
        facts.append(("Cites in this guide", _paper_list(catalogue, cites, page)))
    if cited := catalogue.cited_by.get(paper.key):
        facts.append(("Cited in this guide by", _paper_list(catalogue, cited, page)))
    facts.append(("Citation", _citation_html(paper.key, meta)))
    info = "".join(f"<tr><th>{name}</th><td>{value}</td></tr>" for name, value in facts)

    body = [
        paper.short,
        paper.long,
        f'<p class="row-tags">{tags_html}</p>' if tags_html else "",
        f'<p class="row-links">{"".join(links)}</p>',
        f'<table class="row-info">{info}</table>',
    ]
    cells = [
        html_text(meta.title),
        html.escape(_first_author(meta)),
        str(meta.year or ""),
    ]
    main = _main_row(paper.anchor, cells, tags)
    return main + "\n" + _detail_row(paper.anchor, len(cells), body, tags)


def papers_page_markdown(catalogue: Catalogue) -> str:
    """Return the Markdown of the papers page: tag filter, one table per category."""
    papers_file = catalogue.papers_file
    page = papers_file.page
    ordered = sorted(
        papers_file.papers.values(),
        key=lambda paper: _paper_order(catalogue, paper.key),
    )
    used_tags = [
        tag for tag in papers_file.tags if any(tag in paper.tags for paper in ordered)
    ]
    filter_buttons = "".join(
        f'<button type="button" class="tag" data-tag="{html.escape(tag)}">'
        f"{html.escape(tag)}</button>"
        for tag in used_tags
    )
    lines = _front_matter(page, papers_file.source_path)
    lines += [
        _tools(page),
        "",
        (
            '<div class="tag-filter" hidden><span class="tag-filter-label">Filter</span>'
            f'{filter_buttons}<button type="button" class="tag-clear">Show all</button>'
            "</div>"
        ),
        "",
    ]
    for category, title in papers_file.categories.items():
        members = [paper for paper in ordered if paper.category == category]
        if not members:
            continue
        rows = [paper_rows_markdown(catalogue, paper) for paper in members]
        lines += [f"## {title} {{: #{slug(title)} }}", ""]
        lines += [_table("papers", list(PAPER_COLUMNS), rows)]
    return "\n".join(lines).rstrip() + "\n"


def bibtex_file(entries: Iterable[str]) -> str:
    """Return a BibTeX file holding `entries`."""
    return "\n\n".join(entries) + "\n"


# ---- Commands ----------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """Where the catalogue is read from and the pages written to."""

    catalogue_dir: Path = CATALOGUE_DIR
    book_config_path: Path = BOOK_CONFIG_PATH
    cache_path: Path = CACHE_PATH
    book_dir: Path = BOOK_DIR
    request_interval_s: float = REQUEST_INTERVAL_S

    @property
    def papers_path(self) -> Path:
        """The file of paper records."""
        return self.catalogue_dir / PAPERS_FILE_NAME

    def load(self) -> tuple[PapersFile, list[EntriesFile]]:
        """Read and check the paper records and every entries page.

        The pages come in the reading order of ``book.yaml``.

        Raises
        ------
        CatalogueError
            If a page is not in ``book.yaml``, or two pages define the same record.
        """
        papers_file = load_papers(self.papers_path)
        pages = [
            load_entries(path, papers_file.papers)
            for path in sorted(self.catalogue_dir.glob("*.yaml"))
            if path.name != PAPERS_FILE_NAME
        ]
        order = book_page_order(self.book_config_path)
        for page in pages:
            if page.page.path not in order:
                raise CatalogueError(
                    f"{page.source_path}: page {page.page.path} is not in "
                    f"{display_path(self.book_config_path)}"
                )
        pages.sort(key=lambda page: order.index(page.page.path))
        seen: dict[str, Path] = {}
        for page in pages:
            for key in page.records:
                if key in seen:
                    raise CatalogueError(
                        f"{page.source_path}: record {key!r} is also in {seen[key]}"
                    )
                seen[key] = page.source_path
        for page in pages:
            for entry in page.entries:
                for ref in entry.uses:
                    try:
                        find_entry(pages, ref)
                    except CatalogueError as error:
                        raise CatalogueError(
                            f"{page.source_path}: entry {entry.id}: {error}"
                        ) from None
        return papers_file, pages

    def read_cache(self) -> dict:
        """Return the fetched metadata, or an empty cache if none exists yet."""
        if not self.cache_path.is_file():
            return {"papers": {}, "records": {}}
        return json.loads(self.cache_path.read_text(encoding="utf-8"))


def outputs(catalogue: Catalogue, book_dir: Path) -> dict[Path, str]:
    """Return every file the build writes, with its content."""
    files = {}
    for page_file in catalogue.pages:
        keys: dict[str, None] = {}
        for entry in page_file.entries:
            for key in entry.cited + entry.related:
                keys.setdefault(key)
        page_path = book_dir / page_file.page.path
        files[page_path] = entries_page_markdown(catalogue, page_file)
        files[page_path.with_suffix(".bib")] = bibtex_file(
            catalogue.metadata[key].bibtex for key in keys
        )
    papers_page = catalogue.papers_file.page
    paper_keys = sorted(catalogue.papers, key=lambda key: _paper_order(catalogue, key))
    files[book_dir / papers_page.path] = papers_page_markdown(catalogue)
    files[book_dir / papers_page.path.with_suffix(".bib")] = bibtex_file(
        catalogue.metadata[key].bibtex for key in paper_keys
    )
    return files


def build(settings: Settings, check: bool = False) -> list[Path]:
    """Write the catalogue pages and their BibTeX files.

    Parameters
    ----------
    settings : Settings
        The paths to read and write.
    check : bool
        Write nothing and return the files whose content would change.

    Returns
    -------
    list of Path
        The files written, or with `check`, the files that are out of date.
    """
    papers_file, pages = settings.load()
    catalogue = resolve(papers_file, pages, settings.read_cache())
    changed = []
    for path, content in outputs(catalogue, settings.book_dir).items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == content:
            continue
        changed.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    return changed


def fetch(settings: Settings, refresh: bool = False) -> list[str]:
    """Fetch the metadata of every paper and record into the cache.

    Parameters
    ----------
    settings : Settings
        The paths to read and write, and the interval between requests.
    refresh : bool
        Fetch what the cache already holds as well.

    Returns
    -------
    list of str
        The keys fetched.
    """
    papers_file, pages = settings.load()
    records = {key: record for page in pages for key, record in page.records.items()}
    cache = settings.read_cache()
    fetched = []
    for key, paper in papers_file.papers.items():
        if paper.inspire is not None and (refresh or key not in cache["papers"]):
            cache["papers"][key] = asdict(
                fetch_paper(paper, settings.request_interval_s)
            )
            fetched.append(key)
    for key, record in records.items():
        if record.doi and (refresh or key not in cache["records"]):
            cache["records"][key] = asdict(
                fetch_record(record, settings.request_interval_s)
            )
            fetched.append(key)
    cache = {
        "papers": {
            key: cache["papers"][key]
            for key in sorted(cache["papers"])
            if key in papers_file.papers
        },
        "records": {
            key: cache["records"][key]
            for key in sorted(cache["records"])
            if key in records
        },
    }
    settings.cache_path.write_text(
        json.dumps(cache, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return fetched


def main(argv: Iterable[str] | None = None) -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser("build", help="write the catalogue pages")
    build_parser.add_argument(
        "--check", action="store_true", help="fail if a page is out of date"
    )
    fetch_parser = commands.add_parser("fetch", help="fetch metadata into the cache")
    fetch_parser.add_argument(
        "--refresh", action="store_true", help="fetch cached entries again"
    )
    args = parser.parse_args(argv)
    settings = Settings()
    try:
        if args.command == "fetch":
            keys = fetch(settings, refresh=args.refresh)
            print(
                f"fetched {len(keys)} entries into {display_path(settings.cache_path)}"
            )
            return 0
        changed = build(settings, check=args.check)
    except (CatalogueError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    names = ", ".join(display_path(path) for path in changed)
    if args.check:
        if changed:
            print(f"out of date: {names}; run `python scripts/catalogue.py build`")
            return 1
        print("catalogue pages are up to date")
        return 0
    print(f"wrote {names}" if changed else "catalogue pages are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
