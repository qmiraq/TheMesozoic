"use strict";

const API = {
  status: "/Skin/status",
  inspect: (steamId) => `/Skin/inspect/${encodeURIComponent(steamId)}`,
  apply: "/Skin/apply",
};

// Player-facing color fields. Detail1 and Eyes are intentionally hidden because
// current EVRIMA builds no longer apply those regions visually. The API/Lua still
// support them, so this page preserves their loaded values silently when applying.
const VISIBLE_COLOR_FIELDS = [
  ["body", "Body", "Main body"],
  ["markings", "Markings", "Pattern markings"],
  ["flank", "Flank", "Side color"],
  ["underbelly", "Underbelly", "Lower body"],
  ["maleDisplay", "Male display", "Display color"],
  ["teeth", "Teeth", "Teeth color"],
  ["mouth", "Mouth", "Mouth color"],
  ["claws", "Claws", "Claw color"],
];

const HIDDEN_PRESERVED_COLOR_FIELDS = [
  ["detail1", "detail1"],
  ["eyes", "eyes"],
];

const APPLY_COLOR_KEYS = [
  "body",
  "markings",
  "flank",
  "underbelly",
  "detail1",
  "eyes",
  "maleDisplay",
  "teeth",
  "mouth",
  "claws",
];

const API_TO_UI_COLOR = {
  body: "body",
  markings: "markings",
  flank: "flank",
  underbelly: "underbelly",
  detail1: "detail1",
  eyes: "eyes",
  male_display: "maleDisplay",
  teeth: "teeth",
  mouth: "mouth",
  claws: "claws",
};

const SPECIAL_PATTERN_COUNTS = new Map([
  ["omniraptor", 5],
  ["carnotaurus", 4],
  ["pachycephalosaurus", 4],
  ["herrerasaurus", 4],
  ["austroraptor", 4],
]);

const state = {
  steamId: "",
  loadedSnapshot: null,
  currentSnapshot: null,
  busy: false,
};

const elements = {
  bridgeBadge: document.getElementById("bridgeBadge"),
  loadForm: document.getElementById("loadForm"),
  steamIdInput: document.getElementById("steamIdInput"),
  loadButton: document.getElementById("loadButton"),
  workspace: document.getElementById("workspace"),
  speciesName: document.getElementById("speciesName"),
  lifeSerial: document.getElementById("lifeSerial"),
  genderValue: document.getElementById("genderValue"),
  themeIndexValue: document.getElementById("themeIndexValue"),
  refreshButton: document.getElementById("refreshButton"),
  colorGrid: document.getElementById("colorGrid"),
  patternIndex: document.getElementById("patternIndex"),
  skinVariation: document.getElementById("skinVariation"),
  patternFact: document.getElementById("patternFact"),
  variationFact: document.getElementById("variationFact"),
  resetButton: document.getElementById("resetButton"),
  applyButton: document.getElementById("applyButton"),
  requestStatus: document.getElementById("requestStatus"),
  toast: document.getElementById("toast"),
};

function buildColorEditor() {
  const fragment = document.createDocumentFragment();

  for (const [key, label, hint] of VISIBLE_COLOR_FIELDS) {
    const row = document.createElement("div");
    row.className = "color-row";
    row.innerHTML = `
      <div class="color-picker-wrap">
        <input id="${key}Picker" type="color" value="#ffffff" aria-label="${label} color picker">
      </div>
      <div class="color-field">
        <label for="${key}Hex">${label}<span>${hint}</span></label>
        <input id="${key}Hex" type="text" value="#FFFFFF" maxlength="7" spellcheck="false" autocomplete="off">
      </div>`;
    fragment.appendChild(row);
  }

  elements.colorGrid.appendChild(fragment);

  for (const [key] of VISIBLE_COLOR_FIELDS) {
    const picker = document.getElementById(`${key}Picker`);
    const hex = document.getElementById(`${key}Hex`);

    picker.addEventListener("input", () => {
      hex.value = picker.value.toUpperCase();
      updatePreview();
    });

    hex.addEventListener("input", () => {
      const normalized = normalizeRgb(hex.value);
      if (normalized) {
        picker.value = normalized.toLowerCase();
        hex.value = normalized;
        updatePreview();
      }
    });

    hex.addEventListener("blur", () => {
      const normalized = normalizeRgb(hex.value);
      if (!normalized) {
        hex.value = picker.value.toUpperCase();
      }
    });
  }
}

function normalizeRgb(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  const short = trimmed.length === 9 ? trimmed.slice(0, 7) : trimmed;
  return /^#[0-9a-fA-F]{6}$/.test(short) ? short.toUpperCase() : null;
}

function patternCountForSpecies(species) {
  return SPECIAL_PATTERN_COUNTS.get(String(species || "").toLowerCase()) ?? 3;
}

function configurePatternOptions(species, selectedIndex) {
  const count = patternCountForSpecies(species);
  elements.patternIndex.innerHTML = "";

  for (let index = 0; index < count; index += 1) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `Pattern ${index + 1}`;
    elements.patternIndex.appendChild(option);
  }

  const safeIndex = Number.isInteger(selectedIndex) && selectedIndex >= 0 && selectedIndex < count
    ? selectedIndex
    : 0;

  elements.patternIndex.value = String(safeIndex);
  updateFacts();
}

function snapshotFromForm() {
  const colors = {};

  for (const [key] of VISIBLE_COLOR_FIELDS) {
    const value = normalizeRgb(document.getElementById(`${key}Hex`).value);
    if (!value) {
      throw new Error(`${key} must be a six-digit RGB color like #A1B2C3.`);
    }
    colors[key] = value;
  }

  // Preserve hidden-but-still-required API fields from the latest live snapshot.
  // This prevents applying a skin from accidentally changing Detail1/Eyes even
  // though players cannot edit those currently inactive regions.
  for (const [uiKey, apiKey] of HIDDEN_PRESERVED_COLOR_FIELDS) {
    const loaded = normalizeRgb(state.currentSnapshot?.[apiKey])
      || normalizeRgb(state.loadedSnapshot?.[apiKey])
      || "#FFFFFF";
    colors[uiKey] = loaded;
  }

  for (const key of APPLY_COLOR_KEYS) {
    if (!colors[key]) {
      throw new Error(`Missing required color field: ${key}`);
    }
  }

  return {
    steamId: state.steamId,
    patternIndex: Number.parseInt(elements.patternIndex.value, 10),
    skinVariation: Number.parseInt(elements.skinVariation.value || "0", 10),
    ...colors,
  };
}

function renderSnapshot(snapshot) {
  state.currentSnapshot = snapshot;
  elements.speciesName.textContent = snapshot.species || "Unknown species";
  elements.lifeSerial.textContent = snapshot.life_serial ?? "—";
  elements.genderValue.textContent = snapshot.is_female === true ? "Female" : "Male";
  elements.themeIndexValue.textContent = snapshot.theme_index ?? "—";
  elements.skinVariation.value = Number.isFinite(Number(snapshot.skin_variation))
    ? String(Math.trunc(Number(snapshot.skin_variation)))
    : "0";

  configurePatternOptions(snapshot.species, Number(snapshot.pattern_index));

  for (const [apiKey, uiKey] of Object.entries(API_TO_UI_COLOR)) {
    const normalized = normalizeRgb(snapshot[apiKey]);
    if (normalized) setColor(uiKey, normalized);
  }

  updatePreview();
  elements.workspace.classList.remove("is-hidden");
}

function setColor(key, value) {
  const picker = document.getElementById(`${key}Picker`);
  const hex = document.getElementById(`${key}Hex`);
  if (!picker || !hex) return;
  picker.value = value.toLowerCase();
  hex.value = value;
}

function updatePreview() {
  for (const [key] of VISIBLE_COLOR_FIELDS) {
    const value = normalizeRgb(document.getElementById(`${key}Hex`).value) || "#202820";
    const target = document.querySelector(`[data-preview="${key}"]`);
    if (target) target.style.backgroundColor = value;
  }
  updateFacts();
}

function updateFacts() {
  elements.patternFact.textContent = `Pattern ${Number.parseInt(elements.patternIndex.value || "0", 10) + 1}`;
  elements.variationFact.textContent = `Variation ${elements.skinVariation.value || "0"}`;
}

function setBusy(busy, label = "Working") {
  state.busy = busy;
  elements.loadButton.disabled = busy;
  elements.refreshButton.disabled = busy;
  elements.applyButton.disabled = busy;
  elements.resetButton.disabled = busy;
  if (busy) setRequestStatus("working", label, "Waiting for the miniEniac bridge response…");
}

function setRequestStatus(kind, title, detail) {
  elements.requestStatus.className = `request-status${kind ? ` request-status--${kind}` : ""}`;
  elements.requestStatus.innerHTML = `<strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });

  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { error: text || `HTTP ${response.status}` };
  }

  if (!response.ok) {
    const reason = payload?.reason || payload?.error || payload?.detail || `HTTP ${response.status}`;
    throw new Error(String(reason));
  }

  return payload;
}

async function checkBridge() {
  try {
    const status = await apiJson(API.status, { method: "GET" });
    const online = status?.modRootExists && status?.commandFileExists && status?.resultFileExists;
    elements.bridgeBadge.className = online ? "status-badge status-badge--online" : "status-badge status-badge--offline";
    elements.bridgeBadge.querySelector("span:last-child").textContent = online ? "Bridge online" : "Bridge incomplete";
  } catch {
    elements.bridgeBadge.className = "status-badge status-badge--offline";
    elements.bridgeBadge.querySelector("span:last-child").textContent = "Bridge offline";
  }
}

async function loadSkin(steamId) {
  const trimmed = String(steamId || "").trim();
  if (!/^[0-9]{15,20}$/.test(trimmed)) {
    throw new Error("SteamID must contain 15 to 20 digits.");
  }

  state.steamId = trimmed;
  setBusy(true, "Loading current skin");

  try {
    const snapshot = await apiJson(API.inspect(trimmed), { method: "GET" });
    state.loadedSnapshot = structuredClone(snapshot);
    renderSnapshot(snapshot);
    setRequestStatus("success", "Live skin loaded", `${snapshot.species} · life ${snapshot.life_serial}`);
  } finally {
    setBusy(false);
  }
}

async function applySkin() {
  if (!state.steamId) throw new Error("Load a player first.");

  const payload = snapshotFromForm();
  setBusy(true, "Applying skin");

  try {
    const result = await apiJson(API.apply, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    setRequestStatus("success", "Skin applied", `${result.species || "Dinosaur"} · current life ${result.life_serial ?? "—"}`);
    showToast("Skin applied successfully.");

    // Refresh after the bridge confirms success, so the page reflects the authoritative live state.
    const refreshed = await apiJson(API.inspect(state.steamId), { method: "GET" });
    state.loadedSnapshot = structuredClone(refreshed);
    renderSnapshot(refreshed);
  } finally {
    setBusy(false);
  }
}

function resetToLoaded() {
  if (!state.loadedSnapshot) return;
  renderSnapshot(structuredClone(state.loadedSnapshot));
  setRequestStatus("", "Restored loaded values", "No changes were sent to the game.");
}

let toastTimer = null;
function showToast(message, error = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast is-visible${error ? " is-error" : ""}`;
  toastTimer = setTimeout(() => {
    elements.toast.className = "toast";
  }, 3200);
}

function handleError(error) {
  const message = error instanceof Error ? error.message : String(error);
  setRequestStatus("error", "Request failed", message);
  showToast(message, true);
}

function bindEvents() {
  elements.loadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await loadSkin(elements.steamIdInput.value);
    } catch (error) {
      handleError(error);
      setBusy(false);
    }
  });

  elements.refreshButton.addEventListener("click", async () => {
    try {
      await loadSkin(state.steamId);
    } catch (error) {
      handleError(error);
      setBusy(false);
    }
  });

  elements.applyButton.addEventListener("click", async () => {
    try {
      await applySkin();
    } catch (error) {
      handleError(error);
      setBusy(false);
    }
  });

  elements.resetButton.addEventListener("click", resetToLoaded);
  elements.patternIndex.addEventListener("change", updateFacts);
  elements.skinVariation.addEventListener("input", updateFacts);
}

async function init() {
  buildColorEditor();
  bindEvents();
  updatePreview();
  await checkBridge();
}

init().catch(handleError);
