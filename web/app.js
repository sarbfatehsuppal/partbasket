const $ = (id) => document.getElementById(id);
const query = (root, selector) => root.querySelector(selector);
const cad = new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD" });
const demoShipping = { Northstar: "8.00", CircuitHouse: "10.00" };
let mode = "sample";
let quote = null;
let shippingByMode = { sample: { ...demoShipping }, quotes: Object.create(null) };
let lastRequest = null;
let draftRevision = 0;
let quoteRevision = 0;

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

async function api(path, body, method = "POST") {
  const response = await fetch(path, body === undefined && method !== "DELETE" ? {} : {
    method, headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined;
  const payload = response.headers.get("content-type")?.includes("application/json") ? await response.json() : {};
  if (!response.ok) throw new Error(payload.error || "The server couldn’t complete the request.");
  return payload;
}

async function fileText(file) {
  if (!file || file.size > 100_000) throw new Error("Use a CSV file under 100 KB.");
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
  } catch {
    throw new Error("Save the CSV using UTF-8 encoding and try again.");
  }
}

function change() {
  draftRevision++;
  const save = $("results").querySelector("[data-save]");
  if (save && !save.disabled) {
    save.disabled = true;
    $("result-summary").textContent = "Inputs changed. Compare again to update this result.";
  }
  lastRequest = null;
}

function projectCount() {
  const projects = [...$("projects").children];
  $("add-project").disabled = projects.length >= 8;
  for (const card of projects) query(card, ".remove").disabled = projects.length === 1;
}

function addProject(project = {}) {
  const card = $("project-template").content.firstElementChild.cloneNode(true);
  const name = query(card, ".project-name");
  const boards = query(card, ".project-boards");
  const bom = query(card, ".project-bom");
  const details = query(card, ".bom");
  const preview = query(card, ".bom-preview");
  name.value = project.name || "";
  boards.value = project.boards || 1;
  bom.value = project.bom || "";
  details.open = !bom.value.trim();
  const describe = (filename) => {
    const lines = Math.max(0, bom.value.trim().split(/\r?\n/).length - 1);
    preview.textContent = filename || (bom.value.trim() ? lines + (lines === 1 ? " BOM row" : " BOM rows") + " · View or edit" : "Add BOM");
  };
  describe();
  bom.addEventListener("input", () => { describe(); change(); });
  for (const field of [name, boards]) field.addEventListener("input", change);
  query(card, ".project-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      bom.value = await fileText(file);
      describe(file.name);
      details.open = false;
      change();
      $("planner-status").textContent = "";
    } catch (error) {
      $("planner-status").textContent = error.message;
    } finally {
      event.target.value = "";
    }
  });
  query(card, ".remove").addEventListener("click", () => {
    card.remove();
    projectCount();
    change();
  });
  $("projects").append(card);
  projectCount();
  return name;
}

function shippingFields() {
  const names = mode === "sample" ? Object.keys(demoShipping) : (quote?.suppliers || []);
  const known = shippingByMode[mode];
  const labels = names.map((supplier) => {
    const label = node("label", supplier + " shipping (CAD)");
    const field = node("input");
    Object.assign(field, { type: "number", min: "0", max: "1000", step: "0.01", required: true,
      value: Object.hasOwn(known, supplier) ? known[supplier] : "" });
    field.addEventListener("input", () => { known[supplier] = field.value; change(); });
    label.append(field);
    return label;
  });
  $("shipping").replaceChildren(...labels);
}

function selectMode(next) {
  mode = next;
  $("source").value = mode;
  $("quote-upload").hidden = mode !== "quotes";
  $("quote-hint").textContent = mode === "sample"
    ? "Synthetic prices for the included sample projects."
    : "Use your own CAD quotes. Each supplier needs a shipping estimate.";
  shippingFields();
}

function draft() {
  const cards = [...$("projects").children];
  const projects = cards.map((card) => ({
    name: query(card, ".project-name").value.trim(),
    boards: Number(query(card, ".project-boards").value),
    bom: query(card, ".project-bom").value,
  }));
  if (projects.some((project) => !project.bom.trim())) throw new Error("Add a BOM CSV for every project.");
  if (mode === "quotes" && !quote) throw new Error("Upload your supplier quotes first.");
  const names = mode === "sample" ? Object.keys(demoShipping) : quote.suppliers;
  return {
    projects, mode, quote_csv: mode === "quotes" ? quote.text : undefined,
    shipping: Object.fromEntries(names.map((name) => [name, shippingByMode[mode][name] ?? ""])),
  };
}

function table(headers, rows) {
  const wrapper = node("div", undefined, "table-scroll");
  const output = node("table");
  const head = output.createTHead().insertRow();
  for (const h of headers) {
    const cell = node("th", h);
    cell.scope = "col";
    head.append(cell);
  }
  const body = output.createTBody();
  for (const values of rows) {
    const row = body.insertRow();
    for (const value of values) row.insertCell().textContent = String(value ?? "—");
  }
  wrapper.append(output);
  return wrapper;
}

function downloadCsv(filename, headers, rows) {
  const cell = (value) => {
    let text = String(value ?? "");
    // Stop spreadsheet formulas in arbitrary names or quote references.
    if (/^\s*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const content = [headers, ...rows].map((row) => row.map(cell).join(",")).join("\r\n");
  const objectUrl = URL.createObjectURL(new Blob(["\uFEFF", content], {type:"text/csv;charset=utf-8"}));
  const anchor = node("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

function action(label, callback, style = "small") {
  const button = node("button", label, style);
  button.type = "button";
  button.addEventListener("click", callback);
  return button;
}

function renderOrder(result, target, saving = false) {
  target.replaceChildren();
  if (result.unavailable.length) {
    const warning = node("div", undefined, "warning");
    warning.append(node("strong", "No complete order found"),
      node("p", "Supply an in-stock offer that can fill each line."));
    const list = node("ul");
    for (const item of result.unavailable) {
      list.append(node("li", item.mpn + " · " + item.needed + " units · " + item.projects.join(", ")));
    }
    warning.append(list);
    target.append(warning);
    return;
  }
  const cards = node("div", undefined, "summaries");
  for (const [key, title] of [
    ["optimized", "Lowest order total"], ["per_part", "Lowest part prices"], ["single_store", "Best single supplier"],
  ]) {
    const plan = result.plans[key];
    const card = node("article", undefined, key === "optimized" ? "total-card best" : "total-card");
    card.append(node("h3", title), node("strong", plan ? cad.format(plan.total) : "Unavailable", "amount"));
    card.append(node("small", plan ? cad.format(plan.parts) + " parts + " + cad.format(plan.shipping) + " shipping"
      : "No one store can fill every line"));
    cards.append(card);
  }
  target.append(cards, node("p", cad.format(result.savings) +
    " saved compared with selecting each lowest part price.", "insight"));
  const heading = node("div", undefined, "group-heading");
  heading.append(node("h3", "Cost by project"),
    action("Export split CSV", () => downloadCsv("partbasket-project-costs.csv",
      ["Project", "Boards", "Parts CAD", "Shipping CAD", "Total CAD"],
      result.projects.map((p) => [p.name, p.boards, p.parts, p.shipping, p.total]))));
  target.append(heading, table(["Project", "Boards", "Parts", "Shipping", "Total"],
    result.projects.map((p) => [p.name, p.boards, cad.format(p.parts), cad.format(p.shipping), cad.format(p.total)])));
  target.append(node("p", result.allocation_method, "hint"));

  const orderHeading = node("div", undefined, "group-heading");
  orderHeading.append(node("h3", "Supplier order list"),
    action("Export order CSV", () => downloadCsv("partbasket-order.csv",
      ["MPN", "Supplier", "SKU", "Needed", "Ordered", "Unit CAD", "Line CAD", "Quote reference"],
      result.suppliers.flatMap((s) => s.rows.map((r) => [r.mpn,s.name,r.sku,r.needed,r.ordered,r.unit_price,r.cost,r.quote_reference])))));
  target.append(orderHeading);
  for (const supplier of result.suppliers) {
    const details = node("details", undefined, "supplier");
    details.append(node("summary", supplier.name + " · " + supplier.rows.length + " lines · " + cad.format(supplier.total)));
    details.append(table(["Part number", "SKU", "Needed", "Order", "Quote", "Cost"],
      supplier.rows.map((r) => [r.mpn,r.sku,r.needed,r.ordered,r.quote_reference || "—",cad.format(r.cost)])));
    target.append(details);
  }
  target.append(node("p", result.quote_source + " · " + result.limitations.join(" "), "limits"));
  if (saving) {
    const controls = node("div", undefined, "result-actions");
    const status = node("span", "", "hint");
    status.setAttribute("role", "status");
    const save = action("Save order", async () => {
      if (!lastRequest) return;
      const snapshot = lastRequest;
      save.disabled = true;
      try {
        await api("/api/workspace/compare", { ...snapshot, save: true });
        status.textContent = "Saved to order history.";
        save.textContent = "Saved";
      } catch (error) {
        status.textContent = error.message;
        if (lastRequest === snapshot) save.disabled = false;
      }
    }, "primary");
    save.dataset.save = "true";
    controls.append(save, status);
    target.append(controls);
  }
}

async function compare() {
  let request;
  try { request = draft(); }
  catch (error) { $("planner-status").textContent = error.message; return; }
  const revision = draftRevision;
  $("run").disabled = true;
  $("planner-status").textContent = "Comparing orders…";
  try {
    const data = await api("/api/workspace/compare", request);
    if (draftRevision !== revision) {
      $("planner-status").textContent = "Inputs changed. Compare again.";
      return;
    }
    lastRequest = request;
    $("planner-status").textContent = "";
    $("result-summary").textContent = data.distinct_parts + " unique parts · " + data.total_units + " required units";
    const badge = $("result-badge");
    badge.hidden = false;
    badge.className = "badge" + (data.mode === "sample" ? " sample" : "");
    badge.textContent = data.mode === "sample" ? "Sample quotes" : "Your quotes";
    renderOrder(data, $("results"), true);
  } catch (error) {
    if (revision === draftRevision) $("planner-status").textContent = error.message;
  } finally {
    $("run").disabled = false;
  }
}

async function loadExample() {
  const revision = draftRevision;
  const example = await api("/api/workspace/example");
  if (revision !== draftRevision) return;
  $("projects").replaceChildren();
  for (const project of example.projects) addProject(project);
  shippingByMode.sample = { ...example.shipping };
  selectMode("sample");
  change();
  await compare();
}

function reset() {
  quoteRevision++;
  quote = null;
  shippingByMode = { sample: {...demoShipping}, quotes: Object.create(null) };
  $("quote-status").textContent = "";
  $("quotes").value = "";
  $("projects").replaceChildren();
  addProject();
  selectMode("quotes");
  change();
  $("result-badge").hidden = true;
  $("result-summary").textContent = "";
  $("planner-status").textContent = "";
  $("results").replaceChildren(node("div", "Add a BOM and supplier quotes to compare orders.", "empty"));
}

$("planner").addEventListener("submit", (event) => { event.preventDefault(); void compare(); });
$("add-project").addEventListener("click", () => { if ($("projects").children.length < 8) { addProject().focus(); change(); } });
$("example").addEventListener("click", async () => {
  try { await loadExample(); } catch (error) { $("planner-status").textContent = error.message; }
});
$("blank").addEventListener("click", reset);
$("source").addEventListener("change", () => { selectMode($("source").value); change(); });
$("quotes").addEventListener("change", async (event) => {
  const file = event.target.files[0], revision = ++quoteRevision;
  if (!file) return;
  quote = null;
  change();
  shippingFields();
  $("quote-status").textContent = "Checking quotes…";
  try {
    const text = await fileText(file);
    const parsed = await api("/api/workspace/quotes", { quote_csv: text });
    if (quoteRevision !== revision) return;
    quote = { ...parsed, text };
    $("quote-status").textContent = parsed.offers + (parsed.offers === 1 ? " offer for " : " offers for ") +
      parsed.parts + (parsed.parts === 1 ? " part" : " parts");
    shippingFields();
  } catch (error) {
    if (quoteRevision === revision) $("quote-status").textContent = error.message;
  } finally {
    if (quoteRevision === revision) event.target.value = "";
  }
});
$("matcher").addEventListener("submit", async (event) => {
  event.preventDefault();
  const matches = $("matches");
  try {
    const data = await api("/api/suggest", {description: $("description").value});
    const items = data.candidates.map((part) => {
      const li = node("li"); li.append(node("strong", part.mpn), node("small", part.manufacturer + " · " + part.description));
      return li;
    });
    matches.replaceChildren(...(items.length ? items : [node("li", "No candidate in the sample catalog.")]));
  } catch (error) {
    matches.replaceChildren(node("li", error.message));
  }
});

let activeHistoryRequest = 0;
async function openHistoryOrder(id) {
  const requestId = ++activeHistoryRequest;
  const detail = $("history-detail");
  detail.replaceChildren(node("p","Loading saved order…","hint"));
  try {
    const result = await api("/api/workspace/plans/" + id);
    if (requestId !== activeHistoryRequest) return;
    renderOrder(result, detail);
    const remove = action("Delete saved order", async () => {
      if (!window.confirm("Delete this saved order?")) return;
      remove.disabled = true;
      try {
        await api("/api/workspace/plans/" + id, undefined, "DELETE");
        if (requestId === activeHistoryRequest) detail.replaceChildren(node("div", "Select a saved order", "empty"));
        await loadHistory();
      } catch (error) {
        detail.append(node("p", error.message, "message"));
        remove.disabled = false;
      }
    }, "quiet");
    detail.append(remove);
  } catch (error) {
    if (requestId === activeHistoryRequest) detail.replaceChildren(node("p",error.message,"message"));
  }
}

async function loadHistory() {
  const list = $("history-list");
  list.replaceChildren(node("h2", "Order history"), node("p", "Loading…", "hint"));
  try {
    const data = await api("/api/workspace/plans");
    const results = data.orders.map((order) => {
      const button = action("", () => openHistoryOrder(order.id), "history-item");
      const date = new Date(order.created).toLocaleString();
      const label = node("span");
      label.append(node("strong", order.title), node("small", date + (order.mode === "sample" ? " · Sample" : "")));
      button.append(label, node("strong", cad.format(order.total)));
      return button;
    });
    list.replaceChildren(node("h2", "Order history"),
      ...(results.length ? results : [node("p", "Saved orders will appear here.", "hint")]));
  } catch (error) {
    list.replaceChildren(node("p", error.message, "message"));
  }
}

function navigate(history) {
  $("planner-view").hidden = history;
  $("history-view").hidden = !history;
  for (const [id, active] of [["planner-tab", !history], ["history-tab", history]]) {
    const tab = $(id); tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  }
  if (history) void loadHistory();
}
$("planner-tab").addEventListener("click", () => navigate(false));
$("back").addEventListener("click", () => navigate(false));
$("history-tab").addEventListener("click", () => navigate(true));

addProject();
selectMode("sample");
try { await loadExample(); }
catch (error) { $("planner-status").textContent = error.message; }
