"use strict";

let state = null;
let currentIndex = 0;
let saving = false;
let notesDirty = false;
let noteDraft = null;
let conflictDraft = null;
let taxonomyObserver = null;
let changesetFiles = [];

const $ = (selector) => document.querySelector(selector);
const elements = {
  workspace: $(".workspace"), reviewView: $("#reviewView"),
  evidenceView: $("#evidenceView"), viewSwitch: $("#viewSwitch"),
  showEvidence: $("#showEvidence"), showReview: $("#showReview"),
  changesetSummary: $("#changesetSummary"),
  changedFileTable: $("#changedFileTable"),
  diffFileSelect: $("#diffFileSelect"), diffViewer: $("#diffViewer"),
  recommendationSummary: $("#recommendationSummary"),
  recommendationTable: $("#recommendationTable"),
  evidenceSummary: $("#evidenceSummary"), runInventory: $("#runInventory"),
  coverageTable: $("#coverageTable"), ratePlot: $("#ratePlot"),
  metricTable: $("#metricTable"),
  calibrationMatrix: $("#calibrationMatrix"),
  taxonomyComposition: $("#taxonomyComposition"), gateTable: $("#gateTable"),
  lifecycleTable: $("#lifecycleTable"), proposalTable: $("#proposalTable"),
  changeTable: $("#changeTable"),
  errorPanel: $("#errorPanel"), errorMessage: $("#errorMessage"),
  progressText: $("#progressText"), progressBar: $("#progressBar"),
  progressTrack: $(".progress-track"), casePosition: $("#casePosition"),
  sourceBadge: $("#sourceBadge"), contextText: $("#contextText"), userText: $("#userText"),
  notes: $("#notes"), previous: $("#previousCase"), next: $("#nextCase"),
  conflictNotice: $("#conflictNotice"), conflictSavedNote: $("#conflictSavedNote"),
  conflictSavedJudgment: $("#conflictSavedJudgment"),
  clear: $("#clearLabel"), tieBreaks: $("#tieBreaks"), saveStatus: $("#saveStatus"),
  taskInstruction: $("#taskInstruction"), labelStack: $("#labelStack"),
  pageTitle: $("#pageTitle"), taxonomyEvidence: $("#taxonomyEvidence"),
  contextLabel: $("#contextLabel"), focalLabel: $("#focalLabel"),
  reviewQuestion: $("#reviewQuestion"),
  proposalPanel: $("#proposalPanel"), proposalLabel: $("#proposalLabel"),
  proposalReason: $("#proposalReason"), assessmentControls: $("#assessmentControls"),
  assessmentCorrect: $("#assessmentCorrect"),
  assessmentIncorrect: $("#assessmentIncorrect"),
  assessmentUnsure: $("#assessmentUnsure"),
  groundingControls: $("#groundingControls"), guidanceCard: $("#guidanceCard"),
  interpretationCard: $("#interpretationCard"), reviewKind: $("#reviewKind"),
  situationLabel: $("#situationLabel"), situationSummary: $("#situationSummary"),
  replyBlock: $("#replyBlock"), replyText: $("#replyText"),
  interpretationLabel: $("#interpretationLabel"),
  interpretationText: $("#interpretationText"), rationaleText: $("#rationaleText"),
  expectedAction: $("#expectedAction"), rawContext: $("#rawContext"),
  rawFocal: $("#rawFocal"), rawEvidence: $("#rawEvidence"),
};

const assessmentLabels = {accurate: "Accurate", partly_accurate: "Partial",
  wrong: "Wrong", not_enough_context: "Insufficient"};
const metricLabels = {skill_invocation_rate: "Skill invocation rate",
  repeated_call_rate: "Repeated-call candidates", verified_outcome_rate: "Verified outcomes",
  tool_failure_rate: "Tool-failure candidates",
  input_tokens_per_verified_outcome: "Input tokens / verified outcome"};

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function percent(value, digits = 0) {
  if (value === null || value === undefined) return "Not observed";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function number(value) {
  return Number(value || 0).toLocaleString();
}

function observedCount(value) {
  return value === null || value === undefined ? "—" : number(value);
}

function versions(value) {
  if (Array.isArray(value)) return value.map((item) => `v${item}`).join(", ");
  if (!value || typeof value !== "object") return String(value || "—");
  return Object.entries(value).map(([name, version]) => `${name} v${version}`).join(" · ");
}

function tableCell(text, className = "") {
  return textElement("td", className, text);
}

function stateCell(value) {
  return tableCell(String(value || "unknown").replaceAll("_", " "),
    `state-cell state-${String(value || "unknown").replaceAll("_", "-")}`);
}

function showView(name) {
  const evidence = name === "evidence";
  document.body.classList.toggle("analysis-active", evidence);
  elements.evidenceView.hidden = !evidence;
  elements.reviewView.hidden = evidence;
  elements.showEvidence.setAttribute("aria-pressed", String(evidence));
  elements.showReview.setAttribute("aria-pressed", String(!evidence));
  if (evidence) {
    elements.pageTitle.textContent = "Proposed repository changes";
    elements.taskInstruction.textContent =
      "Inspect the actual file patches first, then audit the recommendation and its supporting measurements.";
  } else {
    render();
    elements.taskInstruction.textContent = state.protocol.human_instruction;
  }
}

function renderChangeset(dashboard) {
  const changeset = dashboard.changeset;
  const files = changeset.files || [];
  changesetFiles = files;
  const revision = String(changeset.base_revision || "").slice(0, 8);
  elements.changesetSummary.textContent =
    `${number(changeset.file_count)} files · +${number(changeset.additions)} / -${number(changeset.deletions)} · ` +
    `${changeset.base_ref} @ ${revision} → ${changeset.target.replaceAll("_", " ")}`;
  const rows = files.map((file) => {
    const row = document.createElement("tr");
    row.append(tableCell(file.path, "code-cell"), tableCell(file.scope),
               stateCell(file.status), tableCell(`+${number(file.additions)}`, "numeric diff-add"),
               tableCell(`-${number(file.deletions)}`, "numeric diff-delete"));
    return row;
  });
  elements.changedFileTable.replaceChildren(...rows);
  const options = files.map((file) => {
    const option = document.createElement("option");
    option.value = file.path;
    option.textContent = `${file.path} (+${file.additions} / -${file.deletions})`;
    return option;
  });
  elements.diffFileSelect.replaceChildren(...options);
  const initial = files.find((file) => file.path === "plugins/p/EVALUATION.md") || files[0];
  if (initial) elements.diffFileSelect.value = initial.path;
  showSelectedPatch();
}

function showSelectedPatch() {
  const file = changesetFiles.find(
    (item) => item.path === elements.diffFileSelect.value);
  const patch = file ? file.patch : "No textual diff available.";
  const lines = patch.split("\n").map((line) => {
    const span = document.createElement("span");
    span.className = diffLineClass(line);
    span.textContent = `${line}\n`;
    return span;
  });
  elements.diffViewer.replaceChildren(...lines);
}

function diffLineClass(line) {
  if (line.startsWith("diff --git") || line.startsWith("index ")
      || line.startsWith("--- ") || line.startsWith("+++ ")
      || line.startsWith("new file mode") || line.startsWith("deleted file mode")) {
    return "diff-line diff-line-file";
  }
  if (line.startsWith("@@")) return "diff-line diff-line-hunk";
  if (line.startsWith("+")) return "diff-line diff-line-addition";
  if (line.startsWith("-")) return "diff-line diff-line-deletion";
  if (line.startsWith("\\ No newline")) return "diff-line diff-line-note";
  return "diff-line diff-line-context";
}

function renderRecommendations(dashboard) {
  const recommendations = dashboard.recommendations || [];
  const rows = recommendations.map((recommendation) => {
    const row = document.createElement("tr");
    row.append(textElement("th", "proposal-id", recommendation.proposal),
               stateCell(recommendation.decision),
               tableCell(recommendation.recommended_action),
               tableCell(recommendation.evidence_basis),
               tableCell(recommendation.revisit_when));
    return row;
  });
  elements.recommendationTable.replaceChildren(...rows);
  elements.recommendationSummary.textContent = recommendations.map(
    (item) => `${item.proposal}: ${item.decision.replaceAll("_", " ")}`).join(" · ");
}

function svgElement(tag, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function renderRun(dashboard) {
  const run = dashboard.run;
  const row = document.createElement("tr");
  row.append(tableCell(run.dataset_id, "code-cell"),
             tableCell(number(run.trace_population), "numeric"),
             tableCell(versions(run.trace_schema_versions), "code-cell"),
             tableCell(versions(run.adapter_versions), "code-cell"),
             tableCell(versions(run.scorer_versions), "code-cell"),
             tableCell(versions(run.rubric_versions), "code-cell"));
  elements.runInventory.replaceChildren(row);
  elements.evidenceSummary.textContent =
    `${number(run.trace_population)} traces · ${dashboard.metrics.length} scorer rows · ` +
    `${dashboard.calibration.completed}/${dashboard.calibration.total} interpretation judgments`;
}

function renderCoverage(dashboard) {
  const rows = Object.entries(dashboard.coverage).map(([harness, values]) => {
    const row = document.createElement("tr");
    row.append(tableCell(harness, "harness-cell"),
               tableCell(number(values.measured), "numeric"),
               tableCell(number(values.not_observable), "numeric"),
               tableCell(number(values.not_scored), "numeric"),
               tableCell(number(values.dependency_unavailable), "numeric"));
    return row;
  });
  elements.coverageTable.replaceChildren(...rows);
}

function metricEstimate(metric, value) {
  if (value === null || value === undefined) return "—";
  return metric.endsWith("_rate") ? percent(value, 2) : Number(value).toLocaleString();
}

function renderMetrics(dashboard) {
  const rows = dashboard.metrics.map((metric) => {
    const row = document.createElement("tr");
    const missingInterval = metric.interval_low === null || metric.interval_low === undefined
      || metric.interval_high === null || metric.interval_high === undefined;
    const noSamplingDesign = metric.uncertainty?.reason === "sampling_design_not_established";
    const interval = missingInterval
      ? (noSamplingDesign ? "No sampling design" : "—")
      : `${metricEstimate(metric.metric_id, metric.interval_low)}–${metricEstimate(metric.metric_id, metric.interval_high)}`;
    const intervalCell = tableCell(interval, "numeric interval-cell");
    if (missingInterval && noSamplingDesign) {
      intervalCell.title = metric.uncertainty.next_action || metric.uncertainty_method;
    }
    const decision = metric.decision_support === true ? "allowed"
      : metric.decision_support === false && ["repeated_call_rate", "tool_failure_rate"].includes(metric.metric_id)
        ? "barred" : "descriptive";
    row.append(tableCell(metric.harness, "harness-cell"),
               tableCell(metricLabels[metric.metric_id] || metric.metric_id),
               tableCell(String(metric.scorer_version), "numeric code-cell"),
               stateCell(metric.state),
               tableCell(observedCount(metric.numerator), "numeric"),
               tableCell(observedCount(metric.eligible), "numeric"),
               tableCell(metricEstimate(metric.metric_id, metric.value), "numeric estimate-cell"),
               intervalCell,
               stateCell(decision));
    return row;
  });
  elements.metricTable.replaceChildren(...rows);
}

function drawRatePlot(dashboard) {
  const rates = dashboard.metrics.filter((metric) =>
    metric.metric_id.endsWith("_rate") && metric.value !== null
    && metric.value !== undefined);
  if (!rates.length) {
    elements.ratePlot.replaceChildren(textElement("p", "analysis-note",
      "No measured rates are available; see the metric table for coverage states."));
    return;
  }
  const plot = textElement("div", "rate-rows", "");
  plot.setAttribute("role", "img");
  plot.setAttribute("aria-label", "Measured rates and available intervals by harness. " +
    rates.map((metric) => `${metric.harness} ${metricLabels[metric.metric_id] || metric.metric_id} ${percent(metric.value, 2)}`).join("; "));
  for (const metric of rates) {
    const row = textElement("div", "rate-row", "");
    const label = textElement("div", "rate-row-label", "");
    label.append(textElement("div", "", metricLabels[metric.metric_id] || metric.metric_id),
      textElement("div", "rate-source", metric.harness));
    const track = textElement("div", "rate-track", "");
    if (metric.interval_low !== null && metric.interval_low !== undefined
        && metric.interval_high !== null && metric.interval_high !== undefined) {
      const interval = textElement("span", "rate-interval", "");
      interval.style.left = `${metric.interval_low * 100}%`;
      interval.style.width = `${(metric.interval_high - metric.interval_low) * 100}%`;
      track.append(interval);
    }
    const marker = textElement("span", "rate-marker", "");
    marker.style.left = `${Number(metric.value) * 100}%`;
    marker.setAttribute("data-harness", metric.harness);
    track.append(marker);
    row.append(label, track);
    plot.append(row);
  }
  const axis = textElement("div", "rate-row rate-axis-row", "");
  const ticks = textElement("div", "rate-ticks", "");
  for (const tick of [0, 0.25, 0.5, 0.75, 1]) {
    ticks.append(textElement("span", "", percent(tick)));
  }
  axis.append(textElement("div", "", ""), ticks);
  plot.append(axis);
  elements.ratePlot.replaceChildren(plot);
}

function drawCompositionChart(container, proposalId, taxonomy) {
  const splits = Object.entries(taxonomy.splits);
  const labels = [...new Set(splits.flatMap(([, data]) => Object.keys(data.proposed_support)))];
  const width = Math.max(340, Math.floor(container.clientWidth || 680));
  const left = width < 540 ? 82 : 110;
  const right = 22;
  const plotWidth = width - left - right;
  const height = 34 + splits.length * 46;
  const svg = svgElement("svg", {viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": `${proposalId} proposal composition by packet split`});
  splits.forEach(([split, data], rowIndex) => {
    const y = 18 + rowIndex * 46;
    const splitLabel = svgElement("text", {x: left - 10, y: y + 17,
      class: "composition-label", "text-anchor": "end"});
    splitLabel.textContent = split;
    let offset = 0;
    labels.forEach((label, labelIndex) => {
      const count = data.proposed_support[label] || 0;
      const segmentWidth = data.sample_size ? (count / data.sample_size) * plotWidth : 0;
      const rect = svgElement("rect", {x: left + offset, y, width: segmentWidth,
        height: 24, class: `composition-segment series-${labelIndex % 6}`});
      const title = svgElement("title");
      title.textContent = `${split} · ${label} · ${count}/${data.sample_size}`;
      rect.append(title);
      svg.append(rect);
      if (segmentWidth >= 28) {
        const value = svgElement("text", {x: left + offset + segmentWidth / 2,
          y: y + 17, class: "composition-value", "text-anchor": "middle"});
        value.textContent = String(count);
        svg.append(value);
      }
      offset += segmentWidth;
    });
  });
  return {svg, labels};
}

function renderTaxonomies(dashboard) {
  const blocks = Object.entries(dashboard.taxonomies).map(([proposalId, taxonomy]) => {
    const block = document.createElement("article");
    block.className = "taxonomy-block";
    const heading = document.createElement("header");
    heading.append(textElement("h3", "", `${proposalId} · rubric v${taxonomy.version}`),
                   textElement("p", "taxonomy-meta",
                     `${taxonomy.sample_size} sampled · ${taxonomy.labeled} assessed · decision support ${taxonomy.validated ? "on" : "off"}`));
    const plot = document.createElement("div");
    plot.className = "composition-plot";
    plot.dataset.proposal = proposalId;
    const {svg, labels} = drawCompositionChart(plot, proposalId, taxonomy);
    plot.append(svg);
    const tableWrap = document.createElement("div");
    tableWrap.className = "table-wrap";
    const table = document.createElement("table");
    table.className = "data-table data-table-compact support-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headRow.append(textElement("th", "", "Proposed class"));
    Object.keys(taxonomy.splits).forEach((split) => headRow.append(
      textElement("th", "numeric", split)));
    head.append(headRow);
    const body = document.createElement("tbody");
    labels.forEach((label, index) => {
      const row = document.createElement("tr");
      const labelCell = tableCell(label.replaceAll("_", " "), `series-label series-${index % 6}`);
      row.append(labelCell);
      Object.values(taxonomy.splits).forEach((split) => row.append(
        tableCell(number(split.proposed_support[label]), "numeric")));
      body.append(row);
    });
    const binding = textElement("p", "binding-line", Object.entries(taxonomy.splits)
      .flatMap(([split, data]) => (data.bindings || [data]).map((binding) =>
        `${split}: protocol v${binding.protocol_version}, round ${binding.adaptive_round}, minimum held-out/class ${binding.minimum_heldout_per_label}`))
      .join(" · "));
    table.append(head, body); tableWrap.append(table);
    block.append(heading, plot, tableWrap, binding);
    return block;
  });
  elements.taxonomyComposition.replaceChildren(...blocks);
  if (taxonomyObserver) taxonomyObserver.disconnect();
  const redraw = (plot) => {
    const proposalId = plot.dataset.proposal;
    plot.replaceChildren(drawCompositionChart(
      plot, proposalId, dashboard.taxonomies[proposalId]).svg);
  };
  elements.taxonomyComposition.querySelectorAll(".composition-plot").forEach(redraw);
  if (window.ResizeObserver) {
    taxonomyObserver = new ResizeObserver((entries) => entries.forEach(
      (entry) => redraw(entry.target)));
    elements.taxonomyComposition.querySelectorAll(".composition-plot").forEach(
      (plot) => taxonomyObserver.observe(plot));
  }
}

function renderCalibration(dashboard) {
  const calibration = dashboard.calibration;
  const kinds = {...calibration.by_review_kind, total: {
    ...calibration.assessment_counts, total: calibration.total}};
  const rows = Object.entries(kinds).map(([kind, counts]) => {
    const row = document.createElement("tr");
    row.append(tableCell(kind.replaceAll("_", " ")),
      ...Object.keys(assessmentLabels).map((key) => tableCell(number(counts[key]), "numeric")),
      tableCell(number(counts.total), "numeric total-cell"));
    return row;
  });
  elements.calibrationMatrix.replaceChildren(...rows);
}

function renderGates(dashboard) {
  const rows = [];
  const definitions = {
    P1: [["precision", ">= 90%"], ["recall", ">= 75%"],
         ["polling false positives", "<= 5%"]],
    P4: [["precision", ">= 90%"], ["recall", ">= 80%"],
         ["agreement", ">= 80%"], ["unknown rate", "<= 10%"]],
  };
  Object.entries(definitions).forEach(([proposalId, gates]) => {
    const taxonomy = dashboard.taxonomies[proposalId];
    const heldout = taxonomy.splits.heldout || {human_labeled: 0, sample_size: 0};
    gates.forEach(([name, required]) => {
      const row = document.createElement("tr");
      row.append(tableCell(`${proposalId} ${name}`), tableCell(required, "numeric"),
                 tableCell("—", "numeric"),
                 tableCell(`${heldout.human_labeled}/${heldout.sample_size} held-out truth`, "numeric"),
                 stateCell("unvalidated"));
      rows.push(row);
    });
  });
  elements.gateTable.replaceChildren(...rows);
}

function lifecycleRow(proposal, measure, observed, target, status, boundary) {
  const row = document.createElement("tr");
  row.append(tableCell(proposal, "proposal-id"), tableCell(measure),
             tableCell(observed, "numeric estimate-cell"), tableCell(target, "numeric"),
             stateCell(status), tableCell(boundary));
  return row;
}

function renderLifecycle(dashboard) {
  const p2 = dashboard.proposals.P2;
  const p3 = dashboard.proposals.P3;
  const p7 = dashboard.proposals.P7;
  const p3Observed = [p3.population, p3.resolved, p3.unresolved].every(
    value => Number.isInteger(value) && value >= 0)
    && p3.population > 0 && p3.resolved + p3.unresolved === p3.population;
  const p3Status = p3Observed
    ? (p3.unresolved === 0 ? "passed" : "failed") : (p3.state === "controlled_pass" ? "not_observed" : p3.state);
  const rows = [
    lifecycleRow("P2", "matched terminals / starts", `${number(p2.matched_terminals)} / ${number(p2.starts)} (${percent(p2.completion_rate, 2)})`, ">= 95%", "parked", "explicit source events"),
    lifecycleRow("P2", "unmatched starts", `${number(p2.unmatched_starts)} (${percent(p2.unmatched_start_rate, 2)})`, "<= 2%", "parked", "explicit source events"),
    lifecycleRow("P2", "missed triggers / opportunities", `${p2.missed_trigger_rate} / ${p2.opportunity_rate}`, "calibrated classifier", "not_observable", "no opportunity surface"),
    lifecycleRow("P3", "manifest resolution", `${observedCount(p3.resolved)} / ${observedCount(p3.population)} (${percent(p3Observed ? p3.resolved / p3.population : null)})`, "100%", p3Status, "declared coverage window"),
    lifecycleRow("P7", "capture precision", percent(p7.precision, 2), ">= 95%", "retired", p7.coverage),
    lifecycleRow("P7", "capture recall", percent(p7.recall, 2), ">= 95%", "retired", p7.coverage),
    lifecycleRow("P7", "unmatched terminals", percent(p7.unmatched_terminal_rate, 2), "<= 2%", "retired", p7.coverage),
    lifecycleRow("P7", "added normalized bytes", percent(p7.added_byte_share, 4), "<= 2%", "retired", p7.coverage),
  ];
  elements.lifecycleTable.replaceChildren(...rows);
}

function proposalBasis(id, dashboard) {
  const p = dashboard.proposals[id];
  const bases = {
    P1: `${observedCount(dashboard.taxonomies.P1.human_labeled)} human labels; ${number(dashboard.taxonomies.P1.labeled)} assessments; scorer barred from decisions.`,
    P2: `${p.matched_terminals}/${p.starts} starts matched; opportunities remain not observable.`,
    P3: `${observedCount(p.resolved)}/${observedCount(p.population)} controlled sessions resolved.`,
    P4: `${observedCount(dashboard.taxonomies.P4.human_labeled)} human labels; ${number(dashboard.taxonomies.P4.labeled)} assessments; taxonomy barred from decisions.`,
    P5: "Comparison fields are enforced; the 30-task experiment was not run.",
    P6: "Suppression preserved unchanged.",
    P7: `Retired. Historical owned wrappers only; harness-wide coverage is ${p.harness_wide_coverage}.`,
    P8: "Previously implemented behavior preserved.",
  };
  return bases[id];
}

function renderProposals(dashboard) {
  const rows = Object.keys(dashboard.proposals).map((id) => {
    const row = document.createElement("tr");
    row.append(textElement("th", "proposal-id", id),
               textElement("td", "proposal-state",
                 dashboard.proposals[id].state.replaceAll("_", " ")),
               textElement("td", "", proposalBasis(id, dashboard)));
    return row;
  });
  elements.proposalTable.replaceChildren(...rows);
}

function renderChanges(changes) {
  const rows = changes.map((change) => {
    const row = document.createElement("tr");
    row.append(tableCell(change.signal, "code-cell"), tableCell(change.change),
               tableCell(change.reason));
    return row;
  });
  elements.changeTable.replaceChildren(...rows);
}

function renderDashboard(dashboard) {
  renderChangeset(dashboard);
  renderRecommendations(dashboard);
  renderRun(dashboard);
  renderCoverage(dashboard);
  renderMetrics(dashboard);
  drawRatePlot(dashboard);
  renderTaxonomies(dashboard);
  renderCalibration(dashboard);
  renderGates(dashboard);
  renderLifecycle(dashboard);
  renderProposals(dashboard);
  renderChanges(dashboard.changes || []);
}

function setStatus(message, error = false) {
  elements.saveStatus.textContent = message;
  elements.saveStatus.classList.toggle("error", error);
}

function currentCase() { return state.cases[currentIndex]; }

function humanizeReason(value) {
  const text = String(value || "").replaceAll("_", " ").trim();
  return text ? text[0].toUpperCase() + text.slice(1) : "No signal recorded";
}

function renderInterpretationCard(item) {
  const understanding = item.review_kind === "user_understanding";
  elements.pageTitle.textContent = understanding
    ? "Did I understand you?" : "Did I judge this correctly?";
  elements.reviewKind.textContent = understanding
    ? "Understanding check" : "Agent judgment";
  elements.situationLabel.textContent = understanding ? "Agent said or did" : "What happened";
  elements.situationSummary.textContent = item.situation_summary;
  elements.replyBlock.hidden = !understanding;
  elements.replyText.textContent = item.user_turn;
  elements.interpretationLabel.textContent = understanding
    ? "My interpretation" : "My assessment";
  elements.interpretationText.textContent = item.interpretation;
  elements.rationaleText.textContent = item.rationale;
  elements.expectedAction.textContent = item.expected_action;
  formatEvidence(elements.rawContext, item.context_blocks, "No earlier evidence captured.");
  formatEvidence(elements.rawFocal, item.user_turn_blocks, "No focal evidence captured.");
  elements.rawEvidence.open = false;
}

function buildLabelButtons(item) {
  const labels = item.proposed_label
    ? state.protocol.decision_order.filter((label) => label !== item.proposed_label)
    : state.protocol.decision_order;
  const buttons = labels.map((label, index) => {
    const prompt = state.protocol.label_prompts[label];
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.label = label;
    const shortcut = document.createElement("kbd");
    shortcut.textContent = String(index + 1);
    const copy = document.createElement("span");
    const action = document.createElement("strong");
    action.textContent = prompt.action;
    const detail = document.createElement("small");
    detail.textContent = prompt.detail;
    copy.append(action, detail);
    button.append(shortcut, copy);
    button.addEventListener("click", () => save(
      label, true, item.proposed_label ? "incorrect" : null));
    return button;
  });
  elements.labelStack.replaceChildren(...buttons);
}

function appendInline(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    const element = document.createElement(token.startsWith("**") ? "strong" : "code");
    element.textContent = token.startsWith("**") ? token.slice(2, -2) : token.slice(1, -1);
    parent.append(element);
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function formatEvidence(container, blocks, emptyMessage) {
  container.replaceChildren();
  if (!blocks.length) blocks = [{ type: "paragraph", text: emptyMessage }];
  let activeList = null;
  let activeType = "";
  for (const block of blocks) {
    if (["ordered", "unordered", "reference"].includes(block.type)) {
      if (!activeList || activeType !== block.type) {
        activeType = block.type;
        activeList = document.createElement(block.type === "ordered" ? "ol" : "ul");
        activeList.className = block.type === "reference"
          ? "evidence-list reference-list" : "evidence-list";
        container.append(activeList);
      }
      const item = document.createElement("li");
      if (block.type === "reference") {
        const reference = document.createElement("code");
        reference.className = "point-reference";
        reference.textContent = block.reference;
        item.append(reference);
      }
      appendInline(item, block.text);
      activeList.append(item);
      continue;
    }
    activeList = null;
    activeType = "";
    const element = document.createElement(block.type === "heading" ? "h3"
      : block.type === "table" ? "pre" : block.type === "divider" ? "h4" : "p");
    element.className = `evidence-${block.type}`;
    appendInline(element, block.text);
    container.append(element);
  }
}

function render() {
  const item = currentCase();
  const grounding = item.review_kind === "user_understanding"
    || item.review_kind === "agent_judgment";
  const { completed, total } = state.progress;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  elements.progressText.textContent = `${completed} of ${total} judged / ${percent}%`;
  elements.progressBar.style.width = `${percent}%`;
  elements.progressTrack.setAttribute("aria-valuenow", String(percent));
  elements.casePosition.textContent = `Case ${currentIndex + 1} of ${total}`;
  elements.sourceBadge.textContent = item.source;
  elements.taxonomyEvidence.hidden = grounding;
  elements.interpretationCard.hidden = !grounding;
  if (grounding) renderInterpretationCard(item);
  else elements.pageTitle.textContent = "Audit one diagnosis";
  formatEvidence(elements.contextText, item.context_blocks,
                 "No preceding assistant context was captured.");
  formatEvidence(elements.userText, item.user_turn_blocks, "No user turn was captured.");
  const draft = conflictDraft && conflictDraft.caseId === item.case_id ? conflictDraft : null;
  elements.notes.value = noteDraft && noteDraft.caseId === item.case_id
    ? noteDraft.notes : draft ? draft.notes : item.notes;
  notesDirty = elements.notes.value !== item.notes;
  elements.conflictNotice.hidden = !draft;
  if (draft) {
    const savedLabel = state.protocol.label_prompts[item.human_label]?.action
      || assessmentLabels[item.human_label] || item.human_label || "None";
    elements.conflictSavedJudgment.textContent = draft.latestAvailable
      ? `Saved label: ${savedLabel}. Saved assessment: ${item.assessment || "None"}.`
      : "The latest saved judgment could not be loaded. Reload before saving.";
    elements.conflictSavedNote.textContent = draft.latestAvailable
      ? item.notes || "No saved note."
      : "The latest saved note is unavailable; your draft is preserved above.";
  }
  elements.previous.disabled = currentIndex === 0;
  elements.next.disabled = currentIndex === total - 1;
  elements.clear.disabled = !(item.human_label || item.assessment);
  const hasProposal = Boolean(item.proposed_label);
  buildLabelButtons(item);
  elements.proposalPanel.hidden = !hasProposal;
  elements.assessmentControls.hidden = grounding || !hasProposal;
  elements.groundingControls.hidden = !grounding;
  elements.labelStack.hidden = grounding || hasProposal;
  elements.guidanceCard.hidden = grounding;
  if (hasProposal) {
    const prompt = state.protocol.label_prompts[item.proposed_label];
    elements.proposalLabel.textContent = prompt ? prompt.action : item.proposed_label;
    elements.proposalReason.textContent = humanizeReason(item.proposal_reason);
  }
  document.querySelectorAll("[data-label]").forEach((button) => {
    const label = button.dataset.label;
    button.classList.toggle("selected", label === item.human_label);
    button.setAttribute("aria-pressed", String(label === item.human_label));
    const prompt = state.protocol.label_prompts[label];
    button.querySelector("strong").textContent = prompt.action;
    button.querySelector("small").textContent = prompt.detail;
  });
  document.querySelectorAll("[data-grounding]").forEach((button) => {
    const label = button.dataset.grounding;
    button.classList.toggle("selected", label === item.assessment);
    button.setAttribute("aria-pressed", String(label === item.assessment));
  });
  setStatus(draft ? "Changed elsewhere. Your draft is unsaved; review the saved judgment before choosing again."
    : notesDirty ? "Your note is unsaved; choose a judgment or leave this case to save it."
    : item.assessment ? `Assessment saved as ${item.assessment}`
      : item.human_label ? `Saved as ${item.human_label}` : "Not yet judged", Boolean(draft));
}

function selectFirstOpen() {
  const open = state.cases.findIndex((item) => item.assessment !== undefined
    ? !item.assessment : !item.human_label);
  currentIndex = open === -1 ? Math.max(0, state.cases.length - 1) : open;
}

async function load(options = {}) {
  const pendingDraft = options.preserveDraft || conflictDraft;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    const nextState = await response.json();
    const draftIndex = pendingDraft
      ? nextState.cases.findIndex((item) => item.case_id === pendingDraft.caseId) : -1;
    if (pendingDraft && draftIndex < 0) {
      throw new Error("The original case is no longer available. Your unsaved note is preserved; copy it before leaving.");
    }
    state = nextState;
    conflictDraft = pendingDraft ? { ...pendingDraft, latestAvailable: true } : null;
    const presentation = state.protocol.presentation;
    elements.contextLabel.textContent = presentation.context_label;
    elements.focalLabel.textContent = presentation.focal_label;
    elements.reviewQuestion.textContent = presentation.review_question;
    elements.taskInstruction.textContent = state.protocol.human_instruction;
    selectFirstOpen();
    if (pendingDraft) currentIndex = draftIndex;
    elements.tieBreaks.replaceChildren(...state.protocol.tie_breaks.map((rule) => {
      const item = document.createElement("li");
      item.textContent = rule;
      return item;
    }));
    const hasDashboard = Boolean(state.dashboard);
    elements.viewSwitch.hidden = !hasDashboard;
    if (hasDashboard) renderDashboard(state.dashboard);
    elements.errorPanel.hidden = true;
    render();
    const completedCalibration = hasDashboard
      && state.dashboard.calibration.total > 0
      && state.dashboard.calibration.completed === state.dashboard.calibration.total;
    showView(completedCalibration && !pendingDraft ? "evidence" : "review");
  } catch (error) {
    elements.reviewView.hidden = !(pendingDraft && state);
    elements.evidenceView.hidden = true;
    elements.errorPanel.hidden = false;
    elements.errorMessage.textContent = error.message;
    if (pendingDraft && state) {
      conflictDraft = { ...pendingDraft, latestAvailable: false };
      render();
      setStatus("Could not refresh the changed case. Your note is still unsaved; reload before saving.", true);
    }
  }
}

async function save(label, advance = true, assessment = null) {
  if (saving) return false;
  if (conflictDraft && !conflictDraft.latestAvailable) {
    setStatus("Reload the changed case before saving. Your unsaved note is preserved.", true);
    return false;
  }
  saving = true;
  setStatus("Saving...");
  const caseId = currentCase().case_id;
  const submittedNotes = elements.notes.value;
  noteDraft = { caseId, notes: submittedNotes };
  try {
    const response = await fetch("/api/labels", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Retro-CSRF": state.csrf_token },
      body: JSON.stringify({
        case_id: caseId, label, assessment, notes: submittedNotes,
        expected_revision: state.revision,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const error = new Error(payload.message || `Save failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    state = payload;
    conflictDraft = null;
    const editedWhileSaving = noteDraft && noteDraft.caseId === caseId
      && noteDraft.notes !== submittedNotes;
    if (!editedWhileSaving) noteDraft = null;
    const savedIndex = state.cases.findIndex((item) => item.case_id === caseId);
    if (!editedWhileSaving && advance && (label || assessment) && savedIndex < state.cases.length - 1) {
      currentIndex = savedIndex + 1;
    }
    else currentIndex = savedIndex;
    render();
    return !editedWhileSaving;
  } catch (error) {
    setStatus(error.message, true);
    if (error.status === 409) {
      conflictDraft = { caseId, notes: noteDraft.notes, latestAvailable: false };
      await load({ preserveDraft: conflictDraft });
    }
    return false;
  } finally {
    saving = false;
  }
}

async function saveNotesBeforeMove(delta) {
  if (saving) return;
  const destination = Math.max(0, Math.min(state.cases.length - 1, currentIndex + delta));
  if (destination === currentIndex) return;
  if (conflictDraft) {
    setStatus("Your draft conflicts with a saved edit. Review it and choose a label or assessment before moving on.", true);
    return;
  }
  if (notesDirty) {
    const saved = await save(currentCase().human_label, false);
    if (!saved) return;
  }
  currentIndex = destination;
  render();
  $("#userText").focus({ preventScroll: true });
}

elements.previous.addEventListener("click", () => saveNotesBeforeMove(-1));
elements.next.addEventListener("click", () => saveNotesBeforeMove(1));
elements.showEvidence.addEventListener("click", () => showView("evidence"));
elements.showReview.addEventListener("click", () => showView("review"));
elements.diffFileSelect.addEventListener("change", showSelectedPatch);
elements.clear.addEventListener("click", () => {
  const item = currentCase();
  save("", false, item.review_kind ? "" : item.proposed_label ? "" : null);
});
elements.notes.addEventListener("input", () => {
  noteDraft = { caseId: currentCase().case_id, notes: elements.notes.value };
  notesDirty = elements.notes.value !== currentCase().notes;
  if (conflictDraft) {
    conflictDraft.notes = elements.notes.value;
    setStatus("Your draft is unsaved. Review the saved judgment before choosing again.", true);
    return;
  }
  setStatus(notesDirty ? "Note will save when you leave this case"
    : currentCase().human_label ? `Saved as ${currentCase().human_label}` : "Not yet judged");
});
elements.assessmentCorrect.addEventListener("click", () => save(
  currentCase().proposed_label, true, "correct"));
elements.assessmentIncorrect.addEventListener("click", () => {
  elements.labelStack.hidden = false;
  setStatus("Choose the better label; no note is required");
});
elements.assessmentUnsure.addEventListener("click", () => save("", true, "unsure"));
document.querySelectorAll("[data-grounding]").forEach((button) => {
  button.addEventListener("click", () => save(
    button.dataset.grounding, true, button.dataset.grounding));
});
$("#retry").addEventListener("click", load);

function handleKeyboard(event /** @type {KeyboardEvent} */) {
  if (!state || elements.reviewView.hidden
      || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.matches("textarea, input")) return;
  if (event.repeat && /^[1-9]$/.test(event.key)) {
    event.preventDefault();
    return;
  }
  const index = Number(event.key) - 1;
  if (currentCase().review_kind && /^[1-4]$/.test(event.key)) {
    event.preventDefault();
    const assessment = ["accurate", "partly_accurate", "wrong",
                        "not_enough_context"][index];
    save(assessment, true, assessment);
  } else if (/^[1-9]$/.test(event.key) && !elements.labelStack.hidden) {
    const choice = elements.labelStack.querySelectorAll("button[data-label]")[index];
    if (choice && !choice.disabled) {
      event.preventDefault();
      choice.click();
    }
  } else if (event.key === "ArrowLeft") {
    event.preventDefault(); saveNotesBeforeMove(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault(); saveNotesBeforeMove(1);
  }
}
document.addEventListener("keydown", handleKeyboard);
load();
