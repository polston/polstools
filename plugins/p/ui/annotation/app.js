"use strict";

let state = null;
let currentIndex = 0;
let saving = false;
let notesDirty = false;

const $ = (selector) => document.querySelector(selector);
const elements = {
  workspace: $(".workspace"), errorPanel: $("#errorPanel"), errorMessage: $("#errorMessage"),
  progressText: $("#progressText"), progressBar: $("#progressBar"),
  progressTrack: $(".progress-track"), casePosition: $("#casePosition"),
  sourceBadge: $("#sourceBadge"), contextText: $("#contextText"), userText: $("#userText"),
  notes: $("#notes"), previous: $("#previousCase"), next: $("#nextCase"),
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
  elements.notes.value = item.notes;
  notesDirty = false;
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
  setStatus(item.assessment ? `Assessment saved as ${item.assessment}`
    : item.human_label ? `Saved as ${item.human_label}` : "Not yet judged");
}

function selectFirstOpen() {
  const open = state.cases.findIndex((item) => item.assessment !== undefined
    ? !item.assessment : !item.human_label);
  currentIndex = open === -1 ? Math.max(0, state.cases.length - 1) : open;
}

async function load() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`Server returned ${response.status}`);
    state = await response.json();
    const presentation = state.protocol.presentation;
    elements.contextLabel.textContent = presentation.context_label;
    elements.focalLabel.textContent = presentation.focal_label;
    elements.reviewQuestion.textContent = presentation.review_question;
    elements.taskInstruction.textContent = state.protocol.human_instruction;
    selectFirstOpen();
    elements.tieBreaks.replaceChildren(...state.protocol.tie_breaks.map((rule) => {
      const item = document.createElement("li");
      item.textContent = rule;
      return item;
    }));
    elements.workspace.hidden = false;
    elements.errorPanel.hidden = true;
    render();
  } catch (error) {
    elements.workspace.hidden = true;
    elements.errorPanel.hidden = false;
    elements.errorMessage.textContent = error.message;
  }
}

async function save(label, advance = true, assessment = null) {
  if (saving) return false;
  saving = true;
  setStatus("Saving...");
  const caseId = currentCase().case_id;
  try {
    const response = await fetch("/api/labels", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Retro-CSRF": state.csrf_token },
      body: JSON.stringify({
        case_id: caseId, label, assessment, notes: elements.notes.value,
        expected_revision: state.revision,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || `Save failed (${response.status})`);
    state = payload;
    const savedIndex = state.cases.findIndex((item) => item.case_id === caseId);
    if (advance && (label || assessment) && savedIndex < state.cases.length - 1) {
      currentIndex = savedIndex + 1;
    }
    else currentIndex = savedIndex;
    render();
    return true;
  } catch (error) {
    setStatus(error.message, true);
    if (String(error.message).includes("changed")) await load();
    return false;
  } finally {
    saving = false;
  }
}

async function saveNotesBeforeMove(delta) {
  if (saving) return;
  const destination = Math.max(0, Math.min(state.cases.length - 1, currentIndex + delta));
  if (destination === currentIndex) return;
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
elements.clear.addEventListener("click", () => {
  const item = currentCase();
  save("", false, item.review_kind ? "" : item.proposed_label ? "" : null);
});
elements.notes.addEventListener("input", () => {
  notesDirty = elements.notes.value !== currentCase().notes;
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
  if (!state || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.matches("textarea, input")) return;
  const labels = state.protocol.decision_order;
  const index = Number(event.key) - 1;
  if (currentCase().review_kind && /^[1-4]$/.test(event.key)) {
    event.preventDefault();
    const assessment = ["accurate", "partly_accurate", "wrong",
                        "not_enough_context"][index];
    save(assessment, true, assessment);
  } else if (!currentCase().proposed_label
      && /^[1-9]$/.test(event.key) && index < labels.length) {
    event.preventDefault();
    save(labels[index]);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault(); saveNotesBeforeMove(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault(); saveNotesBeforeMove(1);
  }
}
document.addEventListener("keydown", handleKeyboard);
load();
