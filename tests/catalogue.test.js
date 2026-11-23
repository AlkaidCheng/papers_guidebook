// Tests of the pure functions in theme/static/catalogue.js. Run with `node --test tests/`.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { parseBibtex, FORMATS, matchesTag, rowUrl } = require("../theme/static/catalogue.js");

const BIB = `% header comment
@article{Qu:2022mxj,
    title = "{Particle {Transformer}}",
    year = "2022"
}

@misc{data:jetclass,
  title = {{JetClass}},
  publisher = {Zenodo}
}
`;

test("parseBibtex splits entries in order and keeps nested braces", () => {
  const entries = parseBibtex(BIB);
  assert.deepEqual([...entries.keys()], ["Qu:2022mxj", "data:jetclass"]);
  assert.ok(entries.get("Qu:2022mxj").endsWith('"2022"\n}'));
  assert.ok(entries.get("data:jetclass").includes("{{JetClass}}"));
});

test("parseBibtex rejects an unclosed entry", () => {
  assert.throws(() => parseBibtex("@misc{key, title = {open"), /unclosed/);
});

test("every format writes the keys in order", () => {
  const source = { bibtex: (key) => "@" + key, text: (key) => "text " + key };
  const byId = Object.fromEntries(FORMATS.map((format) => [format.id, format]));
  assert.equal(byId.bibtex.write(["a", "b"], source), "@a\n\n@b\n");
  assert.equal(byId.text.write(["a", "b"], source), "text a\ntext b\n");
  for (const format of FORMATS) {
    assert.ok(format.label && format.extension && format.mediaType);
  }
});

test("matchesTag", () => {
  assert.equal(matchesTag("jet tagging|open data", "open data"), true);
  assert.equal(matchesTag("jet tagging", "open"), false);
  assert.equal(matchesTag("", ""), true);
  assert.equal(matchesTag(undefined, "jet tagging"), false);
});

test("rowUrl points at the row and drops the query and the old row", () => {
  assert.equal(
    rowUrl("https://example.org/book/datasets/?v=2#jetclass", "top-tagging"),
    "https://example.org/book/datasets/#top-tagging"
  );
  assert.equal(
    rowUrl("http://localhost:8000/papers/", "qu-2022mxj"),
    "http://localhost:8000/papers/#qu-2022mxj"
  );
});
