"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const featureNames = { hyde: "HyDE", rerank: "Reranking", crag: "CRAG", web: "Web fallback" };
const stageIcons = {
  guard_input: "shield", guard_output: "shield", route: "flow", hyde: "spark",
  retrieve_local: "layers", retrieve_web: "globe", chunk_web: "book", rank_web: "rank",
  rerank: "rank", select: "book", grade: "shield", finish: "layers", generate: "spark",
  dataset: "database", list_tables: "database", schema: "database", check: "shield",
  execute: "database", answer: "book", initialization: "info",
};
const examples = {
  evidence: "What does WHO HEARTS-D say about HbA1c monitoring for adults with type 2 diabetes?",
  cohort: "How many synthetic adults have both type 2 diabetes and hypertension?",
  both: "Count synthetic adults with both type 2 diabetes and hypertension, and summarize WHO guidance on HbA1c monitoring for adults with type 2 diabetes.",
};
let currentRun = null;
let isRunning = false;
let activeTab = "answer";
let toastTimer;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function icon(name, extra = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("class", `icon ${extra}`.trim());
  node.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  node.append(use);
  return node;
}

function selectedFeatures() {
  return Object.fromEntries($$("#feature-list input").map(input => [input.name, input.checked]));
}

function updateFeatures(event) {
  const target = event?.target;
  if (target?.name === "web" && target.checked && !$("input[name=crag]").checked) {
    $("input[name=crag]").checked = true;
    toast("CRAG enabled: web fallback needs an evidence check.");
  }
  if (target?.name === "crag" && !target.checked && $("input[name=web]").checked) {
    $("input[name=web]").checked = false;
    toast("Web fallback disabled because CRAG is off.");
  }
  const features = selectedFeatures();
  const enabled = Object.keys(features).filter(key => features[key]);
  $("#feature-count").textContent = `${enabled.length} optional`;
  $("#selected-features").replaceChildren(el("span", "feature-chip base", "Hybrid"));
  enabled.forEach(key => $("#selected-features").append(el("span", "feature-chip", featureNames[key])));
  const nodes = [["shield", "Input check"], ["flow", "Routing"]];
  if (features.hyde) nodes.push(["spark", "HyDE", true]);
  nodes.push(["layers", "Hybrid search"]);
  if (features.rerank) nodes.push(["rank", "Rerank", true]);
  if (features.crag) nodes.push(["shield", "CRAG", true]);
  if (features.web) nodes.push(["globe", "Web if needed", true]);
  nodes.push(["book", "Answer"]);
  const flow = $("#planned-flow");
  flow.replaceChildren();
  nodes.forEach(([name, title, optional], index) => {
    if (index) flow.append(icon("arrow", "planned-arrow"));
    const node = el("div", `planned-node${optional ? " optional" : ""}`);
    node.append(icon(name), el("span", "", title));
    flow.append(node);
  });
  $("#dependency-message").textContent = `${enabled.length} optional features selected. ${enabled.map(key => featureNames[key]).join(", ")}`;
}

function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4000);
}

function counted(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function duration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined) return "";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)}s`;
  return `${Math.floor(milliseconds / 60000)}m ${Math.floor((milliseconds % 60000) / 1000)}s`;
}

function codeBlock(value, extraClass = "") {
  const pre = el("pre", `code-output ${extraClass}`.trim());
  pre.append(el("code", "", typeof value === "string" ? value : JSON.stringify(value, null, 2)));
  return pre;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function appendInline(parent, text) {
  const tokens = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\[\d+\])/g);
  tokens.forEach(token => {
    if (token.startsWith("**") && token.endsWith("**")) {
      parent.append(el("strong", "", token.slice(2, -2)));
    } else if (token.startsWith("`") && token.endsWith("`")) {
      parent.append(el("code", "", token.slice(1, -1)));
    } else if (/^\[\d+\]$/.test(token)) {
      const number = Number(token.slice(1, -1));
      const source = currentRun?.result.published_evidence?.sources?.find(item => item.citation_number === number);
      if (!source) { parent.append(document.createTextNode(token)); return; }
      const button = el("button", "citation-button", token);
      button.type = "button";
      button.setAttribute("aria-label", `View source ${number}`);
      button.addEventListener("click", () => {
        selectTab("sources");
        const card = $(`#source-${number}`);
        if (card) {
          card.open = true;
          card.scrollIntoView({ behavior: reducedMotion() ? "instant" : "smooth", block: "nearest" });
          $("summary", card).focus();
        }
      });
      parent.append(button);
    } else parent.append(document.createTextNode(token));
  });
}

function richText(value) {
  const root = el("div", "answer-text");
  let list = null;
  for (const line of String(value || "").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) { list = null; continue; }
    const heading = trimmed.match(/^#{1,4}\s+(.+)/);
    const bullet = trimmed.match(/^(?:[-*]|\d+[.)])\s+(.+)/);
    if (heading) {
      list = null;
      const h = el("h3");
      appendInline(h, heading[1]);
      root.append(h);
    } else if (bullet) {
      if (!list) { list = el("ul"); root.append(list); }
      const item = el("li");
      appendInline(item, bullet[1]);
      list.append(item);
    } else {
      list = null;
      const paragraph = el("p");
      appendInline(paragraph, trimmed);
      root.append(paragraph);
    }
  }
  return root;
}

function table(rows) {
  const wrapper = el("div", "data-table-wrap");
  if (!rows?.length) { wrapper.append(el("p", "source-intro", "No result rows returned.")); return wrapper; }
  const columns = Object.keys(rows[0]);
  const node = el("table", "data-table");
  const head = el("thead");
  const headings = el("tr");
  columns.forEach(column => { const th = el("th", "", column); th.scope = "col"; headings.append(th); });
  head.append(headings);
  const body = el("tbody");
  rows.forEach(row => {
    const tr = el("tr");
    columns.forEach(column => tr.append(el("td", "", row[column] === null ? "null" : typeof row[column] === "object" ? JSON.stringify(row[column]) : row[column])));
    body.append(tr);
  });
  node.append(head, body);
  wrapper.append(node);
  return wrapper;
}

function sourceCard(source, index, main = false) {
  const metadata = source.metadata || source;
  const number = source.citation_number || index + 1;
  const card = el("details", "source-card");
  if (main) card.id = `source-${number}`;
  const summary = el("summary");
  const heading = el("span", "source-heading");
  heading.append(el("strong", "", metadata.title || metadata.source_id || "Evidence passage"));
  const bits = [metadata.publisher, metadata.publication_date, metadata.pdf_page_start ? `PDF p. ${metadata.pdf_page_start}` : null].filter(Boolean);
  heading.append(el("small", "", bits.join(" · ") || metadata.section_id || "Source metadata not supplied"));
  summary.append(el("span", "source-number", number), heading, icon("down"));
  const body = el("div", "source-body");
  body.append(el("p", "source-excerpt", source.excerpt || source.page_content || "No excerpt returned."));
  const url = safeUrl(metadata.canonical_url);
  if (url) {
    const link = el("a", "source-link", "Open original source");
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.append(icon("external"));
    body.append(link);
  }
  const details = [metadata.origin ? `Origin: ${metadata.origin}` : null, metadata.geographic_scope, metadata.section_id,
    metadata.rerank_score !== undefined ? `Rerank score: ${metadata.rerank_score}` : null,
    metadata.retrieved_at ? `Retrieved: ${metadata.retrieved_at}` : null].filter(Boolean);
  if (details.length) body.append(el("p", "source-meta", details.join(" · ")));
  card.append(summary, body);
  return card;
}

function detail(label, value) {
  const node = el("details", "inline-detail");
  node.append(el("summary", "", label), codeBlock(value));
  return node;
}

function renderAnswer(run) {
  const result = run.result;
  const panel = $("#panel-answer");
  panel.replaceChildren();
  const statuses = {
    completed: ["check", "Research complete", ""], partial: ["info", "Partially completed", "warning"],
    refused: ["shield", "Request stopped by safety checks", "warning"],
    needs_clarification: ["info", "A little more detail is needed", "warning"],
    not_completed: ["info", "Research did not complete", "warning"], error: ["info", "The run encountered an error", "error"],
  };
  const [statusIcon, statusTitle, statusClass] = statuses[result.status] || ["info", result.status, "warning"];
  const status = el("div", `answer-status ${statusClass}`);
  status.append(icon(statusIcon), document.createTextNode(`${statusTitle} · ${duration(run.duration_ms)}`));
  panel.append(status);
  if (result.message) panel.append(richText(result.message));
  if (result.error_type) panel.append(el("p", "source-meta", `Error: ${result.error_type}. Please try again later.`));
  const cohort = result.synthetic_cohort;
  if (cohort) {
    const section = el("section", "result-branch");
    const heading = el("h3", "branch-heading");
    heading.append(icon("database"), document.createTextNode("Synthetic cohort findings"));
    section.append(heading);
    if (cohort.count !== null && cohort.count !== undefined) section.append(el("div", "cohort-count", Number(cohort.count).toLocaleString()), el("p", "count-label", "synthetic patients in this cohort"));
    const text = cohort.answer || cohort.reason || cohort.message || cohort.error;
    if (text) section.append(richText(text));
    if (cohort.rows) section.append(table(cohort.rows));
    const warnings = (cohort.warnings || []).filter(warning => warning !==
      "This dataset is not current. Missing recent observations may reflect delayed or discontinued ingestion rather than missing clinical care.");
    warnings.forEach(warning => section.append(el("div", "notice", warning)));
    const metadata = el("div", "metadata-pills");
    if (result.analysis_date) metadata.append(el("span", "metadata-pill", `Analysis: ${result.analysis_date}`));
    if (cohort.dataset?.run_id) metadata.append(el("span", "metadata-pill", `Run ${cohort.dataset.run_id} · Synthea`));
    section.append(metadata);
    if (cohort.sql) section.append(detail("Inspect generated SQL & parameters", { sql: cohort.sql, parameters: cohort.parameters }));
    if (cohort.dataset) section.append(detail("Inspect dataset version", cohort.dataset));
    if (cohort.error_type) section.append(el("p", "source-meta", `Error: ${cohort.error_type}. Verify PostgreSQL and synthetic data ingestion.`));
    panel.append(section);
  }
  const evidence = result.published_evidence;
  if (evidence) {
    const section = el("section", "result-branch");
    const heading = el("h3", "branch-heading");
    heading.append(icon("book"), document.createTextNode("Published clinical evidence"));
    section.append(heading);
    section.append(richText(evidence.answer || evidence.message || "No evidence answer was returned."));
    if (evidence.error_type) section.append(el("p", "source-meta", `Error: ${evidence.error_type}. Verify your database, evidence index, and model configuration.`));
    if (evidence.sources?.length) {
      const metadata = el("div", "metadata-pills");
      metadata.append(el("span", "metadata-pill", counted(evidence.sources.length, "source excerpt")));
      if (evidence.crag) metadata.append(el("span", "metadata-pill", evidence.crag.abstained ? "CRAG: insufficient support" : "CRAG: support found"));
      section.append(metadata);
    }
    panel.append(section);
  }
  if (!cohort && !evidence && !result.message) panel.append(richText("No answer was returned. Inspect the execution flow for details."));
}

function stageDescription(step) {
  const output = step.output;
  if (step.status === "error") return output.error_type || output.message || "This stage could not finish.";
  if (step.status === "blocked") return "Stopped by a safety check.";
  const name = step.stage.split(".").pop();
  if (name === "route") return ({ evidence: "Routed to published evidence.", cohort: "Routed to synthetic cohort analysis.", both: "Routed to cohort analysis and evidence." })[output.plan?.route] || output.message || "Question reviewed.";
  if (name === "hyde") return "Hypothetical search passage generated.";
  if (name === "retrieve_web") return output.web_error ? `Fallback failed: ${output.web_error}` : `${counted(output.web_documents?.length || 0, "web document")} retrieved.`;
  if (output.candidates) return `${counted(output.candidates.length, name === "rerank" ? "passage" : "candidate passage")} ${name === "rerank" ? "reranked" : "found"}.`;
  if (name === "select") return `${counted((output.documents || output.local_documents || []).length, "passage")} selected.`;
  if (name === "grade") return output.sufficient ? "Supporting quotations matched to sources." : "Evidence insufficient; fallback or abstention follows.";
  if (name === "finish") return `${counted(output.documents?.length || 0, "passage")} in the answer context.`;
  if (name === "execute") return output.execution?.status === "completed" ? `${counted(output.execution.rows?.length || 0, "result row")} returned.` : "Query error; a retry may follow.";
  if (name === "schema") return "Live columns, keys, and synthetic samples loaded.";
  if (name === "dataset") return `Synthea ingestion run ${output.dataset?.run_id || "selected"}.`;
  if (name === "list_tables") return `${counted(output.tables?.length || 0, "table")} available.`;
  if (output.draft) return output.draft.supported ? "Read-only SQL prepared." : "Question not supported by this dataset.";
  if (name === "guard_output") return "Generated output checked.";
  if (name === "guard_input") return "Input checks passed.";
  if (output.answer) return "Answer prepared for output checks.";
  if (output.pending_trace?.extracted_chunk_count !== undefined) return `${output.pending_trace.extracted_chunk_count} web passages extracted.`;
  return "Stage completed. Inspect the output below.";
}

function stageOutput(step) {
  const root = el("div", "stage-output");
  const output = step.output;
  const name = step.stage.split(".").pop();
  if (name === "hyde") {
    root.append(el("div", "stage-warning", "Generated search text, not source evidence."), el("p", "", output.hypothesis));
  }
  if (output.answer) root.append(richText(output.answer));
  if (output.draft?.sql) root.append(codeBlock(output.draft.sql));
  if (output.execution?.rows) root.append(table(output.execution.rows));
  const documents = output.candidates || output.documents || output.web_documents;
  if (documents?.length) {
    root.append(el("div", "stage-label", counted(documents.length, "output passage")));
    documents.forEach((document, index) => root.append(sourceCard(document, index)));
  }
  // The structured output is always available, including branch decisions and scores.
  if (root.childNodes.length) root.append(detail("View full stage output", output));
  else root.append(codeBlock(output));
  return root;
}

function renderFlow(run) {
  const steps = $("#flow-steps");
  steps.replaceChildren();
  $("#flow-summary").textContent = `${counted(run.flow.length, "recorded step")} · ${duration(run.duration_ms)}`;
  $("#expand-all").textContent = "Expand all";
  $("#expand-all").disabled = !run.flow.length;
  const notes = [];
  if (run.features.web && !run.flow.some(step => step.stage === "evidence.retrieve_web")) notes.push("Web fallback was selected but did not run.");
  if (run.result.plan?.route === "cohort" && Object.keys(featureNames).some(key => run.features[key])) notes.push("Optional retrieval features apply to evidence questions. This run used the cohort graph.");
  if (!run.flow.length) notes.push("The pipeline stopped before a graph step could run. Check the error and project configuration.");
  $("#flow-notes").textContent = notes.join(" ");
  $("#flow-notes").hidden = !notes.length;
  run.flow.forEach((step, index) => {
    const node = el("details", `flow-step ${step.status}`);
    const summary = el("summary");
    const marker = el("span", "step-marker");
    marker.append(icon(stageIcons[step.stage.split(".").pop()] || "flow"));
    const heading = el("span", "step-heading");
    heading.append(el("span", "step-title", step.title), el("span", "step-time", duration(step.duration_ms)));
    const toggle = el("span", "step-toggle");
    toggle.append(el("span", "more-details", "More details"), el("span", "less-details", "Hide details"), icon("chevron"));
    summary.append(marker, heading, el("span", "step-description", stageDescription(step)), toggle);
    summary.setAttribute("aria-label", `Step ${index + 1}: ${step.title}. ${step.status}. More details`);
    node.append(summary, stageOutput(step));
    if (step.status === "error" || step.status === "blocked") node.open = true;
    steps.append(node);
  });
}

function selectTab(name, focus = false) {
  activeTab = name;
  $$("[data-tab]").forEach(button => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    if (active && focus) button.focus();
  });
  ["answer", "sources", "data"].forEach(key => { $(`#panel-${key}`).hidden = key !== name; });
}

function renderRun(run) {
  currentRun = run;
  renderAnswer(run);
  const sources = run.result.published_evidence?.sources || [];
  $("#source-count").textContent = sources.length;
  const panel = $("#panel-sources");
  panel.replaceChildren(el("p", "source-intro", sources.length ? "Source excerpts supplied to the evidence answer. Open a source to inspect its passage and metadata." : "No source excerpts were returned for this run. Cohort results come from the synthetic database."));
  sources.forEach((source, index) => panel.append(sourceCard(source, index, true)));
  $("#panel-data").replaceChildren(codeBlock(run, "raw-data"));
  renderFlow(run);
  selectTab("answer");
  $("#empty-state").hidden = true;
  $("#result-content").hidden = false;
}

function setRunning(value) {
  isRunning = value;
  $$("#question-form input, #question-form textarea, #question-form button, #feature-list input, #new-research").forEach(control => { control.disabled = value; });
  $(".results-section").setAttribute("aria-busy", String(value));
  $("#loading-state").hidden = !value;
  $("#run-label").textContent = value ? "Running…" : "Run research";
  $("#run-icon").classList.toggle("spinning", value);
  $("#run-icon use").setAttribute("href", value ? "#i-flow" : "#i-arrow");
  if (value) {
    $("#empty-state").hidden = true;
    $("#result-content").hidden = true;
  }
}

async function runResearch(event) {
  event.preventDefault();
  if (isRunning) return;
  const question = $("#question").value.trim();
  if (!question) { $("#question").focus(); return; }
  const payload = { question, features: selectedFeatures(), analysis_date: null, run_id: null };
  $("#form-error").hidden = true;
  setRunning(true);
  const started = Date.now();
  $("#elapsed-time").textContent = "0s elapsed";
  const timer = setInterval(() => { $("#elapsed-time").textContent = `${Math.floor((Date.now() - started) / 1000)}s elapsed`; }, 1000);
  try {
    const response = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || "The request failed. Try again.");
    renderRun(data);
  } catch (error) {
    $("#form-error").textContent = error instanceof TypeError ? "Could not reach the assistant. Please try again." : error.message;
    $("#form-error").hidden = false;
    if (currentRun) {
      $("#result-content").hidden = false;
    } else {
      $("#empty-state").hidden = false;
    }
  } finally {
    clearInterval(timer);
    setRunning(false);
  }
}

function newResearch() {
  if (isRunning) return;
  currentRun = null;
  $("#question-form").reset();
  $("#empty-state").hidden = false;
  $("#result-content").hidden = true;
  $("#form-error").hidden = true;
  $("#question").focus();
  window.scrollTo({ top: 0, behavior: reducedMotion() ? "instant" : "smooth" });
}

function reducedMotion() { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; }

$("#feature-list").addEventListener("change", updateFeatures);
$("#question-form").addEventListener("submit", runResearch);
$("#question").addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    $("#question-form").requestSubmit();
  }
});
$$("[data-example]").forEach(button => button.addEventListener("click", () => {
  $("#question").value = examples[button.dataset.example];
  $("#question").focus();
}));
$("#new-research").addEventListener("click", newResearch);
$$("[data-tab]").forEach(button => {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
  button.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = ["answer", "sources", "data"];
    const index = tabs.indexOf(activeTab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : -1) + 3) % 3;
    selectTab(tabs[next], true);
  });
});
$("#expand-all").addEventListener("click", () => {
  const details = $$("#flow-steps > details");
  const shouldOpen = details.some(node => !node.open);
  details.forEach(node => { node.open = shouldOpen; });
  $("#expand-all").textContent = shouldOpen ? "Collapse all" : "Expand all";
});
$("#flow-steps").addEventListener("toggle", () => {
  const details = $$("#flow-steps > details");
  $("#expand-all").textContent = details.length && details.every(node => node.open) ? "Collapse all" : "Expand all";
}, true);
$("#copy-button").addEventListener("click", async () => {
  try {
    const text = activeTab === "data" ? JSON.stringify(currentRun, null, 2) : $(`#panel-${activeTab}`).innerText;
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard.");
  } catch { toast("Clipboard unavailable. Select the text and copy it manually."); }
});
updateFeatures();
