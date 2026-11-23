// Expandable rows, cite menus, BibTeX copy buttons and the tag filter of the
// catalogue pages.
//
// Each table row (tr.row) opens the detail row that follows it (tr.row-detail,
// id "<row id>-detail"). A page's references live in the BibTeX file its
// "Download all" link points to (a.bib-all), and each reference's plain-text
// citation in the data-text of a .citation element with the reference's key. A
// cite button names the keys it cites in data-cite; its menu offers every format
// in FORMATS, so a new format is one more entry.
(function () {
  "use strict";

  // ---- Pure functions, shared with the tests ----------------------------------

  // Split BibTeX source into its entries, by citation key, in source order.
  function parseBibtex(text) {
    const entries = new Map();
    const head = /@\s*(\w+)\s*\{\s*([^,\s]+)\s*,/g;
    let match;
    while ((match = head.exec(text)) !== null) {
      let depth = 0;
      let end = -1;
      for (let i = text.indexOf("{", match.index); i < text.length; i++) {
        if (text[i] === "{") depth++;
        else if (text[i] === "}" && --depth === 0) {
          end = i + 1;
          break;
        }
      }
      if (end < 0) throw new Error("unclosed BibTeX entry " + match[2]);
      entries.set(match[2], text.slice(match.index, end));
      head.lastIndex = end;
    }
    return entries;
  }

  // The formats a cite menu offers. Each writes a list of keys as text, given a
  // source that looks up a key's BibTeX entry and its plain-text citation.
  const FORMATS = [
    {
      id: "bibtex",
      label: "BibTeX",
      extension: "bib",
      mediaType: "application/x-bibtex",
      write: (keys, source) => keys.map((key) => source.bibtex(key)).join("\n\n") + "\n",
    },
    {
      id: "text",
      label: "text",
      extension: "txt",
      mediaType: "text/plain",
      write: (keys, source) => keys.map((key) => source.text(key)).join("\n") + "\n",
    },
  ];

  // Whether an element whose data-tags is `tags` ("a|b") passes the filter `tag`.
  function matchesTag(tags, tag) {
    return !tag || (tags || "").split("|").indexOf(tag) >= 0;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { parseBibtex, FORMATS, matchesTag };
  }
  if (typeof document === "undefined") return;

  // ---- Rows -------------------------------------------------------------------

  function setRow(row, open) {
    const detail = document.getElementById(row.id + "-detail");
    if (!detail) return;
    row.classList.toggle("open", open);
    detail.classList.toggle("open", open);
    const toggle = row.querySelector(".row-toggle");
    if (toggle) toggle.setAttribute("aria-expanded", String(open));
  }

  // Open the row a link points to, as in datasets/#jetclass, and bring it into view.
  function openFromHash() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    let row = document.getElementById(id);
    if (row && row.matches("tr.row-detail")) {
      row = document.getElementById(id.replace(/-detail$/, ""));
    }
    if (!row || !row.matches("tr.row")) return;
    setRow(row, true);
    row.scrollIntoView({ block: "start" });
    row.classList.add("target");
    setTimeout(() => row.classList.remove("target"), 1600);
  }

  function setAll(open) {
    document.querySelectorAll(".catalogue-table tr.row").forEach((row) => setRow(row, open));
    const button = document.querySelector(".expand-all");
    if (button) button.textContent = open ? "Collapse all" : "Expand all";
  }

  // ---- References on the page -----------------------------------------------

  let bibtexPromise = null;

  function loadBibtex() {
    if (!bibtexPromise) {
      const link = document.querySelector("a.bib-all");
      bibtexPromise = link
        ? fetch(link.href)
            .then((response) => {
              if (!response.ok) throw new Error(response.status + " " + link.href);
              return response.text();
            })
            .then(parseBibtex)
        : Promise.reject(new Error("no BibTeX file on this page"));
    }
    return bibtexPromise;
  }

  function sourceFor(entries) {
    return {
      bibtex(key) {
        if (!entries.has(key)) throw new Error("no BibTeX entry " + key);
        return entries.get(key);
      },
      text(key) {
        const element = document.querySelector('.citation[data-key="' + CSS.escape(key) + '"]');
        if (!element) throw new Error("no citation " + key);
        return element.dataset.text;
      },
    };
  }

  function formatted(format, keys) {
    return loadBibtex().then((entries) => format.write(keys, sourceFor(entries)));
  }

  // ---- Clipboard and downloads ----------------------------------------------

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand("copy");
      area.remove();
      copied ? resolve() : reject(new Error("copy refused"));
    });
  }

  function download(text, name, mediaType) {
    const url = URL.createObjectURL(new Blob([text], { type: mediaType }));
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Show `message` on `button` for a moment, then restore its label.
  function flash(button, message) {
    if (!button.dataset.label) button.dataset.label = button.textContent;
    button.textContent = message;
    clearTimeout(button._flash);
    button._flash = setTimeout(() => {
      button.textContent = button.dataset.label;
    }, 1400);
  }

  // ---- Cite menu ------------------------------------------------------------

  let openMenu = null;

  function closeMenu(returnFocus) {
    if (!openMenu) return;
    const { menu, button } = openMenu;
    menu.remove();
    button.setAttribute("aria-expanded", "false");
    if (returnFocus) button.focus();
    openMenu = null;
  }

  function menuItem(label, action) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "cite-menu-item";
    item.setAttribute("role", "menuitem");
    item.textContent = label;
    item.addEventListener("click", action);
    return item;
  }

  function openCiteMenu(button) {
    const keys = button.dataset.cite.split(/\s+/).filter(Boolean);
    const fileName = (button.dataset.file || "references") + ".bib";
    const menu = document.createElement("div");
    menu.className = "cite-menu";
    menu.setAttribute("role", "menu");
    for (const format of FORMATS) {
      menu.appendChild(
        menuItem("Copy " + format.label, (event) => {
          const item = event.currentTarget;
          formatted(format, keys)
            .then(copyText)
            .then(() => {
              flash(button, "Copied");
              closeMenu(true);
            })
            .catch(() => flash(item, "Unavailable"));
        })
      );
    }
    menu.appendChild(
      menuItem("Download .bib", (event) => {
        const item = event.currentTarget;
        formatted(FORMATS[0], keys)
          .then((text) => {
            download(text, fileName, FORMATS[0].mediaType);
            closeMenu(true);
          })
          .catch(() => flash(item, "Unavailable"));
      })
    );
    button.insertAdjacentElement("afterend", menu);
    // The links line is the positioned ancestor; place the menu under the button.
    menu.style.left = button.offsetLeft + "px";
    menu.style.top = button.offsetTop + button.offsetHeight + 4 + "px";
    button.setAttribute("aria-expanded", "true");
    openMenu = { menu, button };
    menu.querySelector("button").focus();
    loadBibtex().catch(() => {});
  }

  // ---- Tag filter -----------------------------------------------------------

  let activeTag = "";

  function applyTag(tag) {
    activeTag = activeTag === tag ? "" : tag;
    let shown = 0;
    document.querySelectorAll(".catalogue-table").forEach((table) => {
      let visibleRows = 0;
      table.querySelectorAll("tr.row, tr.row-detail").forEach((row) => {
        const visible = matchesTag(row.dataset.tags, activeTag);
        row.hidden = !visible;
        if (visible && row.matches("tr.row")) visibleRows++;
      });
      shown += visibleRows;
      // A section with no matching rows disappears with its heading.
      const wrap = table.closest(".table-wrap") || table;
      wrap.hidden = visibleRows === 0;
      const heading = wrap.previousElementSibling;
      if (heading && heading.tagName === "H2") heading.hidden = visibleRows === 0;
    });
    document.querySelectorAll(".tag").forEach((element) => {
      element.classList.toggle("active", element.dataset.tag === activeTag);
      element.setAttribute("aria-pressed", String(element.dataset.tag === activeTag));
    });
    const bar = document.querySelector(".tag-filter");
    if (bar) {
      bar.classList.toggle("filtering", Boolean(activeTag));
      const count = bar.querySelector(".tag-count");
      if (count) count.textContent = activeTag ? shown + " papers" : "";
    }
  }

  // ---- Wiring ---------------------------------------------------------------

  document.documentElement.classList.add("catalogue-js");
  const bar = document.querySelector(".tag-filter");
  if (bar) {
    bar.hidden = false;
    const count = document.createElement("span");
    count.className = "tag-count";
    bar.appendChild(count);
  }
  const expandAll = document.querySelector(".expand-all");
  if (expandAll && document.querySelector(".catalogue-table")) expandAll.hidden = false;

  document.addEventListener("click", (event) => {
    const citeButton = event.target.closest(".cite-button");
    if (citeButton) {
      const reopen = !openMenu || openMenu.button !== citeButton;
      closeMenu(false);
      if (reopen) openCiteMenu(citeButton);
      return;
    }
    if (openMenu && !event.target.closest(".cite-menu")) closeMenu(false);
    const bibButton = event.target.closest(".bib-copy");
    if (bibButton) {
      formatted(FORMATS[0], [bibButton.dataset.cite])
        .then(copyText)
        .then(() => flash(bibButton, "Copied"))
        .catch(() => flash(bibButton, "Unavailable"));
      return;
    }
    const tag = event.target.closest(".tag");
    if (tag) {
      applyTag(tag.dataset.tag);
      return;
    }
    if (event.target.closest(".tag-clear")) {
      activeTag = "";
      applyTag("");
      return;
    }
    const expand = event.target.closest(".expand-all");
    if (expand) {
      setAll(expand.textContent === "Expand all");
      return;
    }
    // A click anywhere on a row opens or closes it; links inside it still work.
    const row = event.target.closest(".catalogue-table tr.row");
    if (row && !event.target.closest("a")) setRow(row, !row.classList.contains("open"));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openMenu) closeMenu(true);
  });
  window.addEventListener("hashchange", openFromHash);
  openFromHash();
})();
