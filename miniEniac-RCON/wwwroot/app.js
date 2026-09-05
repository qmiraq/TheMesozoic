"use strict";

const API = {
  authMe: "/Auth/me",
  login: "/Auth/discord/login",
  logout: "/Auth/logout",
  inspectMine: "/Skin/me",
  applyMine: "/Skin/me/apply",
  patternCounts: "/Skin/presets/pattern-counts",
};

// Player-facing color fields. Detail1 is available again; Eyes stays supported
// by the API/Lua but remains hidden because current EVRIMA builds do not use it.
const VISIBLE_COLOR_COLUMNS = [
  [
    ["maleDisplay", "Male Display", "Display color"],
    ["markings", "Markings", "Pattern markings"],
    ["flank", "Flank", "Side color"],
    ["body", "Body", "Main body"],
    ["underbelly", "Underbelly", "Lower body"],
  ],
  [
    ["detail1", "Details", "Detail color"],
    ["claws", "Claws", "Claw color"],
    ["teeth", "Teeth", "Teeth color"],
    ["mouth", "Mouth", "Mouth color"],
  ],
];

const VISIBLE_COLOR_FIELDS = VISIBLE_COLOR_COLUMNS.flat();

const HIDDEN_PRESERVED_COLOR_FIELDS = [
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
  ["allosaurus", 4],
  ["omniraptor", 6],
  ["carnotaurus", 4],
  ["pachycephalosaurus", 5],
  ["herrerasaurus", 5],
  ["austroraptor", 4],
  ["stegosaurus", 4],
  ["triceratops", 6],
  ["tyrannosaurus", 5],
]);

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

const state = {
  user: null,
  loadedSnapshot: null,
  currentSnapshot: null,
  isFemale: false,
  busy: false,
};

const elements = {
  profileArea: document.getElementById("profileArea"),
  headerLoginButton: document.getElementById("headerLoginButton"),
  profileCard: document.getElementById("profileCard"),
  profileAvatar: document.getElementById("profileAvatar"),
  profileAvatarFallback: document.getElementById("profileAvatarFallback"),
  profileDisplayName: document.getElementById("profileDisplayName"),
  profileUsername: document.getElementById("profileUsername"),
  logoutButton: document.getElementById("logoutButton"),
  loginHero: document.getElementById("loginHero"),
  loadingGate: document.getElementById("loadingGate"),
  authGate: document.getElementById("authGate"),
  linkGate: document.getElementById("linkGate"),
  offlineGate: document.getElementById("offlineGate"),
  retryLinkButton: document.getElementById("retryLinkButton"),
  retrySkinButton: document.getElementById("retrySkinButton"),
  workspace: document.getElementById("workspace"),
  speciesName: document.getElementById("speciesName"),
  refreshButton: document.getElementById("refreshButton"),
  colorGrid: document.getElementById("colorGrid"),
  patternIndex: document.getElementById("patternIndex"),
  skinVariation: document.getElementById("skinVariation"),
  skinVariationValue: document.getElementById("skinVariationValue"),
  maleButton: document.getElementById("maleButton"),
  femaleButton: document.getElementById("femaleButton"),
  resetButton: document.getElementById("resetButton"),
  applyButton: document.getElementById("applyButton"),
  requestStatus: document.getElementById("requestStatus"),
  toast: document.getElementById("toast"),
};

function buildColorEditor() {
  const fragment = document.createDocumentFragment();

  for (const [columnIndex, fields] of VISIBLE_COLOR_COLUMNS.entries()) {
    const column = document.createElement("div");
    column.className = columnIndex === 0
      ? "color-column color-column--compact"
      : "color-column color-column--spaced";

    for (const [key, label, hint] of fields) {
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
      column.appendChild(row);
    }

    fragment.appendChild(column);
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

async function loadAuthoritativePatternCounts() {
  const counts = await apiJson(API.patternCounts, { method: "GET", cache: "no-store" });
  for (const [species, count] of Object.entries(counts || {})) {
    const normalized = String(species || "").toLowerCase();
    const parsed = Number(count);
    if (normalized && Number.isInteger(parsed) && parsed > 0) {
      SPECIAL_PATTERN_COUNTS.set(normalized, parsed);
    }
  }
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
}

function setAssetSex(isFemale) {
  state.isFemale = isFemale === true;
  elements.maleButton.classList.toggle("is-selected", !state.isFemale);
  elements.femaleButton.classList.toggle("is-selected", state.isFemale);
  elements.maleButton.setAttribute("aria-pressed", String(!state.isFemale));
  elements.femaleButton.setAttribute("aria-pressed", String(state.isFemale));
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
    patternIndex: Number.parseInt(elements.patternIndex.value, 10),
    skinVariation: Number.parseInt(elements.skinVariation.value || "0", 10),
    isFemale: state.isFemale,
    ...colors,
  };
}

function renderSnapshot(snapshot) {
  state.currentSnapshot = snapshot;
  elements.speciesName.textContent = snapshot.species || "Unknown species";

  const variation = Number.isFinite(Number(snapshot.skin_variation))
    ? Math.trunc(Number(snapshot.skin_variation))
    : 0;
  elements.skinVariation.max = String(Math.max(10, variation));
  elements.skinVariation.value = String(Math.max(0, variation));
  elements.skinVariationValue.value = elements.skinVariation.value;
  elements.skinVariationValue.textContent = elements.skinVariation.value;

  configurePatternOptions(snapshot.species, Number(snapshot.pattern_index));
  setAssetSex(snapshot.is_female === true);

  for (const [apiKey, uiKey] of Object.entries(API_TO_UI_COLOR)) {
    const normalized = normalizeRgb(snapshot[apiKey]);
    if (normalized) setColor(uiKey, normalized);
  }

  updatePreview();
  showOnly("workspace");
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
}

function updateVariationLabel() {
  elements.skinVariationValue.value = elements.skinVariation.value;
  elements.skinVariationValue.textContent = elements.skinVariation.value;
}

function renderProfile(user) {
  const authenticated = Boolean(user);
  elements.headerLoginButton.classList.toggle("is-hidden", authenticated);
  elements.profileCard.classList.toggle("is-hidden", !authenticated);

  if (!authenticated) return;

  elements.profileDisplayName.textContent = user.displayName || "Discord user";
  elements.profileUsername.textContent = user.username ? `@${user.username}` : "";

  const fallback = String(user.displayName || user.username || "M")
    .trim()
    .charAt(0)
    .toUpperCase() || "M";
  elements.profileAvatarFallback.textContent = fallback;

  if (user.avatarUrl) {
    elements.profileAvatar.src = user.avatarUrl;
    elements.profileAvatar.classList.remove("is-hidden");
    elements.profileAvatarFallback.classList.add("is-hidden");
  } else {
    elements.profileAvatar.src = "";
    elements.profileAvatar.classList.add("is-hidden");
    elements.profileAvatarFallback.classList.remove("is-hidden");
  }
}

function showOnly(target) {
  const sections = {
    loadingGate: elements.loadingGate,
    authGate: elements.authGate,
    linkGate: elements.linkGate,
    offlineGate: elements.offlineGate,
    workspace: elements.workspace,
  };

  for (const [name, element] of Object.entries(sections)) {
    element.classList.toggle("is-hidden", name !== target);
  }

  // The redesigned hero is specifically a Discord-login introduction.
  // Hide it once the player is authenticated and moves into linked-player states.
  const showLoginHero = target === "loadingGate" || target === "authGate";
  elements.loginHero?.classList.toggle("is-hidden", !showLoginHero);
}

function setBusy(busy, label = "Working") {
  state.busy = busy;
  if (elements.refreshButton) elements.refreshButton.disabled = busy;
  elements.applyButton.disabled = busy;
  elements.resetButton.disabled = busy;
  elements.patternIndex.disabled = busy;
  elements.skinVariation.disabled = busy;
  elements.maleButton.disabled = busy;
  elements.femaleButton.disabled = busy;

  for (const [key] of VISIBLE_COLOR_FIELDS) {
    document.getElementById(`${key}Picker`).disabled = busy;
    document.getElementById(`${key}Hex`).disabled = busy;
  }

  if (busy) {
    setRequestStatus("working", label, "Waiting for miniEniac to confirm the live game state…");
  }
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
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers,
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
    throw new ApiError(String(reason), response.status, payload);
  }

  return payload;
}

async function loadSessionAndSkin() {
  showOnly("loadingGate");

  try {
    const user = await apiJson(API.authMe, { method: "GET" });
    state.user = user;
    renderProfile(user);

    if (user.hasSteamLink !== true) {
      showOnly("linkGate");
      return;
    }

    try {
      await loadAuthoritativePatternCounts();
    } catch (error) {
      console.warn("Authoritative pattern catalog unavailable; using bundled counts.", error);
    }
    await loadOwnSkin();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.user = null;
      renderProfile(null);
      showOnly("authGate");
      return;
    }

    showOnly("authGate");
    handleError(error);
  }
}

async function loadOwnSkin() {
  setBusy(true, "Loading current skin");

  try {
    const snapshot = await apiJson(API.inspectMine, { method: "GET" });
    state.loadedSnapshot = structuredClone(snapshot);
    renderSnapshot(snapshot);
    setRequestStatus("success", "Live skin loaded", snapshot.species || "Current dinosaur ready.");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.payload?.code === "steam-link-missing") {
      showOnly("linkGate");
      return;
    }

    if (error instanceof ApiError && error.payload?.reason === "player-offline-or-no-active-dinosaur") {
      showOnly("offlineGate");
      return;
    }

    showOnly("offlineGate");
    throw error;
  } finally {
    setBusy(false);
  }
}

async function applySkin() {
  if (!state.currentSnapshot) {
    throw new Error("Load your current dinosaur first.");
  }

  const payload = snapshotFromForm();
  setBusy(true, "Applying skin");

  try {
    const result = await apiJson(API.applyMine, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    setRequestStatus("success", "Skin applied", result.species || "Your live dinosaur was updated.");
    showToast("Skin applied successfully.");

    const refreshed = await apiJson(API.inspectMine, { method: "GET" });
    state.loadedSnapshot = structuredClone(refreshed);
    renderSnapshot(refreshed);
    setRequestStatus("success", "Skin applied", `${refreshed.species || "Dinosaur"} updated successfully.`);
  } finally {
    setBusy(false);
  }
}

function randomHexColor() {
  const bytes = new Uint8Array(3);

  if (window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  return `#${Array.from(bytes, value =>
    value.toString(16).padStart(2, "0")
  ).join("").toUpperCase()}`;
}

async function randomizeSkin() {
  if (!state.currentSnapshot) {
    throw new Error("Join the server and spawn a dinosaur first.");
  }

  const hiddenColors = {};

  for (const key of APPLY_COLOR_KEYS) {
    const color = randomHexColor();
    const picker = document.getElementById(`${key}Picker`);
    const hex = document.getElementById(`${key}Hex`);

    if (picker && hex) {
      setColor(key, color);
    } else {
      hiddenColors[key] = color;
    }
  }

  state.currentSnapshot = {
    ...state.currentSnapshot,
    detail1: hiddenColors.detail1 || state.currentSnapshot.detail1,
    eyes: hiddenColors.eyes || state.currentSnapshot.eyes,
  };

  updatePreview();

  if (typeof v9ClearRequestStatus === "function") {
    v9ClearRequestStatus();
  }

  if (typeof v9ScheduleInstantApply === "function") {
    v9ScheduleInstantApply();
  } else {
    await apiJson(API.applyMine, {
      method: "POST",
      body: JSON.stringify(snapshotFromForm()),
    });
  }

  showToast("Random skin generated.");
}

function resetToLoaded() {
  if (!state.loadedSnapshot) return;
  renderSnapshot(structuredClone(state.loadedSnapshot));
  setRequestStatus("", "Restored loaded values", "No changes were sent to the game.");
}

async function logout() {
  await apiJson(API.logout, {
    method: "POST",
    body: JSON.stringify({}),
  });

  state.user = null;
  state.loadedSnapshot = null;
  state.currentSnapshot = null;
  renderProfile(null);
  showOnly("authGate");
  showToast("Logged out.");
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
  if (!elements.workspace.classList.contains("is-hidden")) {
    setRequestStatus("error", "Request failed", message);
  }
  showToast(message, true);
}

function bindEvents() {
  if (elements.refreshButton) {
    elements.refreshButton.addEventListener("click", async () => {
      try {
        await loadOwnSkin();
      } catch (error) {
        handleError(error);
      }
    });
  }

  elements.applyButton.addEventListener("click", async () => {
    try {
      await randomizeSkin();
    } catch (error) {
      handleError(error);
      setBusy(false);
    }
  });

  elements.resetButton.addEventListener("click", resetToLoaded);
  elements.skinVariation.addEventListener("input", updateVariationLabel);
  elements.maleButton.addEventListener("click", () => setAssetSex(false));
  elements.femaleButton.addEventListener("click", () => setAssetSex(true));

  elements.retryLinkButton.addEventListener("click", async () => {
    try {
      await loadSessionAndSkin();
    } catch (error) {
      handleError(error);
    }
  });

  if (elements.retrySkinButton) {
    elements.retrySkinButton.addEventListener("click", async () => {
      try {
        await loadOwnSkin();
      } catch (error) {
        handleError(error);
      }
    });
  }

  elements.logoutButton.addEventListener("click", async () => {
    try {
      await logout();
    } catch (error) {
      handleError(error);
    }
  });
}

function surfaceAuthQueryResult() {
  const url = new URL(window.location.href);
  const auth = url.searchParams.get("auth");
  if (!auth) return;

  const messages = {
    cancelled: "Discord login was cancelled.",
    "invalid-callback": "Discord returned an invalid login callback.",
    "invalid-state": "Discord login security validation failed. Please try again.",
    "discord-error": "Discord login failed. Please try again.",
  };

  showToast(messages[auth] || "Discord login failed.", true);
  url.searchParams.delete("auth");
  history.replaceState({}, document.title, `${url.pathname}${url.search}${url.hash}`);
}

async function init() {
  buildColorEditor();
  bindEvents();
  updatePreview();
  updateVariationLabel();
  setAssetSex(false);
  surfaceAuthQueryResult();
  await loadSessionAndSkin();
}

init().catch(handleError);
/* === miniEniac v9 live editor enhancements === */

const V9_EDITOR_COLUMNS = VISIBLE_COLOR_COLUMNS.map(fields =>
  fields.map(([key, label]) => [key, label])
);

const V9_EDITOR_FIELDS = V9_EDITOR_COLUMNS.flat();

const V9_DINOSAUR_POLL_MS = 15000;
const V9_INSTANT_APPLY_DELAY_MS = 180;

let v9DinosaurKey = "";
let v9PollInFlight = false;
let v9InstantTimer = null;
let v9InstantApplyInFlight = false;
let v9InstantApplyPending = false;
let v16LastAppliedSignature = "";

function v9BuildColorEditor() {
  elements.colorGrid.innerHTML = "";
  const fragment = document.createDocumentFragment();

  for (const [columnIndex, fields] of V9_EDITOR_COLUMNS.entries()) {
    const column = document.createElement("div");
    column.className = columnIndex === 0
      ? "color-column color-column--compact"
      : "color-column color-column--spaced";

    for (const [key, label] of fields) {
      const row = document.createElement("div");
      row.className = "color-row";
      row.innerHTML = `
        <div class="color-picker-wrap">
          <input id="${key}Picker" type="color" value="#ffffff" aria-label="${label} color picker">
        </div>
        <div class="color-field">
          <label for="${key}Hex">${label}</label>
          <input id="${key}Hex" type="text" value="#FFFFFF" maxlength="7"
                 spellcheck="false" autocomplete="off">
        </div>`;

      column.appendChild(row);
    }

    fragment.appendChild(column);
  }

  elements.colorGrid.appendChild(fragment);

  for (const [key] of V9_EDITOR_FIELDS) {
    const picker = document.getElementById(`${key}Picker`);
    const hex = document.getElementById(`${key}Hex`);

    picker.addEventListener("input", () => {
      hex.value = picker.value.toUpperCase();
      updatePreview();
      v9ScheduleInstantApply();
    });

    hex.addEventListener("input", () => {
      const normalized = normalizeRgb(hex.value);
      if (!normalized) return;

      picker.value = normalized.toLowerCase();
      hex.value = normalized;
      updatePreview();
      v9ScheduleInstantApply();
    });

    hex.addEventListener("blur", () => {
      const normalized = normalizeRgb(hex.value);
      if (!normalized) {
        hex.value = picker.value.toUpperCase();
      }
    });
  }
}

function v9SnapshotKey(snapshot) {
  const species = String(snapshot?.species || "").trim().toLowerCase();
  const lifeSerial = snapshot?.life_serial ?? snapshot?.lifeSerial ?? "";
  return `${species}|${lifeSerial}`;
}

function v9IsNoDinosaurError(error) {
  return error instanceof ApiError
    && error.payload?.reason === "player-offline-or-no-active-dinosaur";
}

function v9ClearRequestStatus() {
  elements.requestStatus.className = "request-status";
  elements.requestStatus.innerHTML = "";
}

function v9SetOffline() {
  clearTimeout(v9InstantTimer);
  v9InstantTimer = null;
  v9InstantApplyPending = false;
  v9DinosaurKey = "";
  state.loadedSnapshot = null;
  state.currentSnapshot = null;
  showOnly("offlineGate");
  v9ClearRequestStatus();
}

function v9LoadSnapshot(snapshot, announce = false) {
  const previousKey = v9DinosaurKey;

  state.loadedSnapshot = structuredClone(snapshot);
  renderSnapshot(snapshot);

  v9DinosaurKey = v9SnapshotKey(snapshot);
  v9ClearRequestStatus();

  if (announce && previousKey && previousKey !== v9DinosaurKey) {
    showToast(`${snapshot.species || "New dinosaur"} detected.`);
  }
}

async function v9PollForDinosaur() {
  if (
    !state.user
    || state.user.hasSteamLink !== true
    || state.busy
    || v9PollInFlight
    || v9InstantApplyInFlight
  ) {
    return;
  }

  v9PollInFlight = true;

  try {
    const snapshot = await apiJson(API.inspectMine, { method: "GET" });
    const nextKey = v9SnapshotKey(snapshot);

    if (!state.currentSnapshot || nextKey !== v9DinosaurKey) {
      v9LoadSnapshot(snapshot, true);
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      state.user = null;
      state.loadedSnapshot = null;
      state.currentSnapshot = null;
      v9DinosaurKey = "";
      renderProfile(null);
      showOnly("authGate");
      return;
    }

    if (
      error instanceof ApiError
      && error.status === 404
      && error.payload?.code === "steam-link-missing"
    ) {
      state.loadedSnapshot = null;
      state.currentSnapshot = null;
      v9DinosaurKey = "";
      showOnly("linkGate");
      return;
    }

    if (v9IsNoDinosaurError(error)) {
      if (state.currentSnapshot || !elements.workspace.classList.contains("is-hidden")) {
        v9SetOffline();
      }
      return;
    }

    // Do not show a repeating toast every four seconds for temporary bridge errors.
    console.warn("Automatic dinosaur refresh failed:", error);
  } finally {
    v9PollInFlight = false;
  }
}

function v9ScheduleInstantApply() {
  if (!state.currentSnapshot || state.busy) return;

  clearTimeout(v9InstantTimer);
  v9InstantTimer = setTimeout(() => {
    v9FlushInstantApply().catch(handleError);
  }, V9_INSTANT_APPLY_DELAY_MS);
}

async function v9FlushInstantApply() {
  clearTimeout(v9InstantTimer);
  v9InstantTimer = null;

  if (!state.currentSnapshot || state.busy) {
    return;
  }

  /*
   * Only one game-bridge request may run at once. When the player
   * continues editing, remember that another update is needed, but
   * collapse all intermediate changes into the latest form values.
   */
  if (v9InstantApplyInFlight) {
    v9InstantApplyPending = true;
    return;
  }

  let payload;
  let payloadText;
  let payloadSignature;

  try {
    payload = snapshotFromForm();
    payloadText = JSON.stringify(payload);
    payloadSignature = `${v9DinosaurKey}|${payloadText}`;
  } catch (error) {
    handleError(error);
    return;
  }

  /*
   * Color inputs can emit multiple identical browser events. Do not
   * send the same skin to the same dinosaur more than once.
   */
  if (payloadSignature === v16LastAppliedSignature) {
    v9InstantApplyPending = false;
    return;
  }

  v9InstantApplyInFlight = true;
  v9InstantApplyPending = false;

  try {
    await apiJson(API.applyMine, {
      method: "POST",
      body: payloadText,
    });

    v16LastAppliedSignature = payloadSignature;
    v9ClearRequestStatus();
  } catch (error) {
    if (v9IsNoDinosaurError(error)) {
      v16LastAppliedSignature = "";
      v9SetOffline();
      return;
    }

    handleError(error);
  } finally {
    v9InstantApplyInFlight = false;

    /*
     * A request may have completed while the player was still editing.
     * Wait for another quiet period instead of immediately flooding
     * the bridge with the next request.
     */
    if (v9InstantApplyPending && state.currentSnapshot) {
      v9InstantApplyPending = false;

      clearTimeout(v9InstantTimer);

      v9InstantTimer = setTimeout(() => {
        v9FlushInstantApply().catch(handleError);
      }, V9_INSTANT_APPLY_DELAY_MS);
    }
  }
}

async function v9WaitForInstantApply() {
  while (v9InstantApplyInFlight) {
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}

/*
 * Replace the original load function. The page now waits automatically when
 * there is no active dinosaur, while the four-second monitor keeps checking.
 */
loadOwnSkin = async function () {
  setBusy(true, "Loading current skin");

  try {
    const snapshot = await apiJson(API.inspectMine, { method: "GET" });
    v9LoadSnapshot(snapshot, false);
  } catch (error) {
    if (
      error instanceof ApiError
      && error.status === 404
      && error.payload?.code === "steam-link-missing"
    ) {
      showOnly("linkGate");
      return;
    }

    if (v9IsNoDinosaurError(error)) {
      v9SetOffline();
      return;
    }

    if (error instanceof ApiError && error.status === 401) {
      state.user = null;
      renderProfile(null);
      showOnly("authGate");
      return;
    }

    showOnly("offlineGate");
    handleError(error);
  } finally {
    setBusy(false);
  }
};

/*
 * Keep the manual Apply button as a definite save action. Instant updates use
 * the same API, but do not disable the controls while the player is dragging.
 */
applySkin = async function () {
  if (!state.currentSnapshot) {
    throw new Error("Load your current dinosaur first.");
  }

  clearTimeout(v9InstantTimer);
  v9InstantTimer = null;
  v9InstantApplyPending = false;

  await v9WaitForInstantApply();

  const payload = snapshotFromForm();
  setBusy(true, "Applying skin");

  try {
    await apiJson(API.applyMine, {
      method: "POST",
      body: JSON.stringify(payload),
    });

    v9ClearRequestStatus();
    showToast("Skin applied.");
  } catch (error) {
    if (v9IsNoDinosaurError(error)) {
      v9SetOffline();
      return;
    }

    throw error;
  } finally {
    setBusy(false);
  }
};

function v9ReplaceResetButton() {
  const oldButton = elements.resetButton;
  const newButton = oldButton.cloneNode(true);

  oldButton.replaceWith(newButton);
  elements.resetButton = newButton;

  newButton.addEventListener("click", () => {
    if (!state.loadedSnapshot) return;

    renderSnapshot(structuredClone(state.loadedSnapshot));
    v9ClearRequestStatus();
    v9ScheduleInstantApply();
  });
}

function v9BindLiveControls() {
  elements.patternIndex.addEventListener("change", v9ScheduleInstantApply);

  elements.skinVariation.addEventListener("input", () => {
    updateVariationLabel();
    v9ScheduleInstantApply();
  });

  // The original click handlers update state.isFemale first. These listeners
  // then send the updated value to the live dinosaur.
  elements.maleButton.addEventListener("click", v9ScheduleInstantApply);
  elements.femaleButton.addEventListener("click", v9ScheduleInstantApply);
}

v9BuildColorEditor();
v9ReplaceResetButton();
v9BindLiveControls();
updatePreview();
v9ClearRequestStatus();

/* miniEniac v16 visibility refresh */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    v9PollForDinosaur().catch(error => {
      console.warn("Visibility dinosaur refresh failed:", error);
    });
  }
});
setInterval(() => {
  v9PollForDinosaur().catch(error => {
    console.warn("Automatic dinosaur monitor failed:", error);
  });
}, V9_DINOSAUR_POLL_MS);

setTimeout(() => {
  v9PollForDinosaur().catch(error => {
    console.warn("Initial dinosaur monitor failed:", error);
  });
}, 1200);
/* === miniEniac v10 import export presets === */

API.presets = "/Skin/presets";

Object.assign(elements, {
  presetPanel: document.getElementById("skinPresetPanel"),
  presetName: document.getElementById("presetName"),
  savePresetButton: document.getElementById("savePresetButton"),
  importSkinButton: document.getElementById("importSkinButton"),

  exportCurrentSkinButton: document.getElementById("exportCurrentSkinButton"),
  presetSpeciesLabel: document.getElementById("presetSpeciesLabel"),
  presetCounter: document.getElementById("presetCounter"),
  presetList: document.getElementById("presetList"),
});

const V10_EXPORT_FORMAT = "mesozoic-skin";
const V10_EXPORT_VERSION = 1;
const V10_PRESET_LIMIT = 40;
const V10_MAX_IMPORT_BYTES = 100000;

const V10_VISIBLE_COLOR_KEYS = [
  "maleDisplay",
  "markings",
  "flank",
  "body",
  "underbelly",
  "detail1",
  "claws",
  "teeth",
  "mouth",
];

const V10_ALL_COLOR_KEYS = [
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

let v10Presets = [];
let v10PresetSpecies = "";
let v10PresetRefreshInFlight = false;

const V19_COPY_SPECIES = Object.freeze([
  "Allosaurus",
  "Austroraptor",
  "Beipiaosaurus",
  "Carnotaurus",
  "Ceratosaurus",
  "Deinosuchus",
  "Diabloceratops",
  "Dilophosaurus",
  "Dryosaurus",
  "Gallimimus",
  "Herrerasaurus",
  "Hypsilophodon",
  "Kentrosaurus",
  "Maiasaura",
  "Omniraptor",
  "Pachycephalosaurus",
  "Pteranodon",
  "Stegosaurus",
  "Tenontosaurus",
  "Triceratops",
  "Troodon",
  "Tyrannosaurus",
]);

function v19CopySpeciesOptions(sourceSpecies) {
  const source = String(sourceSpecies || "").trim().toLowerCase();

  const individualOptions = V19_COPY_SPECIES
    .filter(species => species.toLowerCase() !== source)
    .map(species =>
      `<option value="${escapeHtml(species)}">${escapeHtml(species)}</option>`
    )
    .join("");

  return `<option value="" selected disabled>Copy skin to</option><option value="*">All dinosaurs</option>${individualOptions}`;
}

function v10CurrentSpecies() {
  return String(state.currentSnapshot?.species || "").trim();
}

function v10NormalizeName(value, fallback = "") {
  const normalized = String(value || fallback).trim();

  if (!normalized || normalized.length > 40) {
    throw new Error("Preset names must contain between 1 and 40 characters.");
  }

  return normalized;
}

function v10Color(value, fieldName, fallback = null) {
  const normalized = normalizeRgb(value);

  if (normalized) return normalized;

  if (fallback) {
    const normalizedFallback = normalizeRgb(fallback);
    if (normalizedFallback) return normalizedFallback;
  }

  throw new Error(`${fieldName} must be a six-digit color such as #A1B2C3.`);
}

function v10SkinFromForm() {
  const form = snapshotFromForm();

  return {
    patternIndex: form.patternIndex,
    skinVariation: form.skinVariation,
    isFemale: form.isFemale,
    body: form.body,
    markings: form.markings,
    flank: form.flank,
    underbelly: form.underbelly,
    detail1: form.detail1,
    eyes: form.eyes,
    maleDisplay: form.maleDisplay,
    teeth: form.teeth,
    mouth: form.mouth,
    claws: form.claws,
  };
}

function v10ValidateSkinData(rawSkin, species) {
  if (!rawSkin || typeof rawSkin !== "object") {
    throw new Error("The imported file does not contain skin data.");
  }

  const colors = rawSkin.colors && typeof rawSkin.colors === "object"
    ? rawSkin.colors
    : rawSkin;

  const patternIndex = Number(rawSkin.patternIndex);
  const skinVariation = Number(rawSkin.skinVariation);

  const patternCount = patternCountForSpecies(species);

  if (
    !Number.isInteger(patternIndex)
    || patternIndex < 0
    || patternIndex >= patternCount
  ) {
    throw new Error(
      `Pattern must be between 1 and ${patternCount} for ${species}.`
    );
  }

  if (
    !Number.isInteger(skinVariation)
    || skinVariation < 0
    || skinVariation > 100
  ) {
    throw new Error("Skin variation must be a whole number between 0 and 100.");
  }

  const hiddenDetailFallback =
    state.currentSnapshot?.detail1
    || state.loadedSnapshot?.detail1
    || "#FFFFFF";

  const hiddenEyesFallback =
    state.currentSnapshot?.eyes
    || state.loadedSnapshot?.eyes
    || "#FFFFFF";

  return {
    patternIndex,
    skinVariation,
    isFemale: rawSkin.isFemale === true,
    body: v10Color(colors.body, "Body"),
    markings: v10Color(colors.markings, "Markings"),
    flank: v10Color(colors.flank, "Flank"),
    underbelly: v10Color(colors.underbelly, "Underbelly"),
    detail1: v10Color(
      colors.detail1,
      "Detail",
      hiddenDetailFallback
    ),
    eyes: v10Color(
      colors.eyes,
      "Eyes",
      hiddenEyesFallback
    ),
    maleDisplay: v10Color(colors.maleDisplay, "Male display"),
    teeth: v10Color(colors.teeth, "Teeth"),
    mouth: v10Color(colors.mouth, "Mouth"),
    claws: v10Color(colors.claws, "Claws"),
  };
}

const V11_SHARE_COLOR_ORDER = [
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

function v11HexToShareColor(value) {
  const normalized = normalizeRgb(value);

  if (!normalized) {
    throw new Error(`Invalid color: ${value}`);
  }

  const r = Number.parseInt(normalized.slice(1, 3), 16);
  const g = Number.parseInt(normalized.slice(3, 5), 16);
  const b = Number.parseInt(normalized.slice(5, 7), 16);

  // Mirror the server-side black safeguard used by the Lua bridge.
  if (r === 0 && g === 0 && b === 0) {
    return [0.001, 0.001, 0.001, 1.000];
  }

  return [
    r / 255,
    g / 255,
    b / 255,
    1.000,
  ];
}

function v11ShareColorToHex(value, fieldName) {
  if (!Array.isArray(value) || value.length < 3) {
    throw new Error(`${fieldName} contains invalid color data.`);
  }

  const channels = value.slice(0, 3).map(channel => {
    const number = Number(channel);

    if (!Number.isFinite(number) || number < 0 || number > 1) {
      throw new Error(`${fieldName} contains an invalid color channel.`);
    }

    return Math.max(
      0,
      Math.min(255, Math.round(number * 255))
    );
  });

  return `#${channels
    .map(channel => channel.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase()}`;
}

function v10ExportDocument(_name, _species, skin) {
  const colors = V11_SHARE_COLOR_ORDER.map(key =>
    v11HexToShareColor(skin[key])
  );

  const colorText = colors.map(color =>
    `[${color.map(channel => Number(channel).toFixed(3)).join(",")}]`
  ).join(",");

  const pattern = Number(skin.patternIndex);
  const variation = Number(skin.skinVariation);
  const gender = skin.isFemale === true ? 1 : 0;

  return `{"v":1,"c":[${colorText}],"p":${pattern},"var":${variation.toFixed(3)},"g":${gender}}`;
}

function v12LegacyCopy(text) {
  const textarea = document.createElement("textarea");

  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  textarea.style.opacity = "0";

  document.body.appendChild(textarea);
  textarea.select();

  const copied = document.execCommand("copy");
  textarea.remove();

  if (!copied) {
    throw new Error("Clipboard access was blocked by the browser.");
  }
}

function v10DownloadSkin(code) {
  const text = String(code || "").trim();

  if (!text) {
    throw new Error("No skin code was generated.");
  }

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).catch(error => {
      try {
        v12LegacyCopy(text);
      } catch {
        handleError(error);
      }
    });

    return;
  }

  v12LegacyCopy(text);
}

function v10ApplySkinData(rawSkin, fileSpecies = null) {
  const currentSpecies = v10CurrentSpecies();

  if (!currentSpecies) {
    throw new Error("Join the server and spawn a dinosaur first.");
  }

  if (
    fileSpecies
    && String(fileSpecies).trim().toLowerCase()
      !== currentSpecies.toLowerCase()
  ) {
    throw new Error(
      `This skin is for ${fileSpecies}, but your current dinosaur is ${currentSpecies}.`
    );
  }

  const skin = v10ValidateSkinData(rawSkin, currentSpecies);

  configurePatternOptions(currentSpecies, skin.patternIndex);

  elements.skinVariation.max = String(
    Math.max(10, skin.skinVariation)
  );

  elements.skinVariation.value = String(skin.skinVariation);
  updateVariationLabel();
  setAssetSex(skin.isFemale);

  for (const key of V10_VISIBLE_COLOR_KEYS) {
    setColor(key, skin[key]);
  }

  state.currentSnapshot = {
    ...state.currentSnapshot,
    pattern_index: skin.patternIndex,
    skin_variation: skin.skinVariation,
    is_female: skin.isFemale,
    detail1: skin.detail1,
    eyes: skin.eyes,
  };

  updatePreview();
  v9ClearRequestStatus();
  v9ScheduleInstantApply();

  return skin;
}

function v10FormatUpdatedDate(value) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) return "";

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function v10RenderPresets() {
  const species = v10CurrentSpecies() || "Current species";

  elements.presetSpeciesLabel.textContent = species;
  elements.presetCounter.textContent =
    `${v10Presets.length}/${V10_PRESET_LIMIT}`;

  if (v10Presets.length === 0) {
    elements.presetList.innerHTML =
      '<div class="preset-empty">No presets saved for this species yet.</div>';
    return;
  }

  elements.presetList.innerHTML = v10Presets.map(preset => {
    const skin = preset.skin || {};

    const swatches = V10_VISIBLE_COLOR_KEYS.map(key => {
      const color = normalizeRgb(skin[key]) || "#202020";

      return `
        <span
          class="preset-swatch"
          style="background:${escapeHtml(color)}"
          title="${escapeHtml(key)}"></span>`;
    }).join("");

    const updated = v10FormatUpdatedDate(preset.updatedAt);

    return `
      <article class="preset-item" data-preset-id="${Number(preset.id)}">
        <div class="preset-item-copy">
          <strong>${escapeHtml(preset.name)}</strong>
          <small>${updated ? `Updated ${escapeHtml(updated)}` : escapeHtml(preset.species)}</small>
          <div class="preset-swatches" aria-hidden="true">
            ${swatches}
          </div>
        </div>

        <div class="preset-item-actions">
          <button class="preset-action preset-action--load" type="button" data-action="load">
            Load
          </button>

          <button class="preset-action preset-action--update" type="button" data-action="update">
            Update
          </button>

          <button class="preset-action" type="button" data-action="export">
            Export
          </button>

          <button class="preset-action" type="button" data-action="rename">
            Rename
          </button>

          <div class="preset-copy-control">
            <select
              class="preset-copy-select"
              data-copy-species
              aria-label="Copy ${escapeHtml(preset.name)} to another dinosaur species">
              ${v19CopySpeciesOptions(preset.species)}
            </select>

            <button class="preset-action preset-action--copy" type="button" data-action="copy">
              <span class="preset-copy-check" aria-hidden="true">✓</span>
              <span>Confirm</span>
            </button>
          </div>

          <button class="preset-action preset-action--delete" type="button" data-action="delete">
            Delete
          </button>
        </div>
      </article>`;
  }).join("");
}

async function v10RefreshPresets(force = false) {
  const species = v10CurrentSpecies();

  if (!species) {
    v10Presets = [];
    v10PresetSpecies = "";
    v10RenderPresets();
    return;
  }

  if (
    !force
    && species.toLowerCase() === v10PresetSpecies.toLowerCase()
  ) {
    return;
  }

  if (v10PresetRefreshInFlight) return;

  v10PresetRefreshInFlight = true;

  try {
    const presets = await apiJson(
      `${API.presets}?species=${encodeURIComponent(species)}`,
      { method: "GET" }
    );

    v10Presets = Array.isArray(presets) ? presets : [];
    v10PresetSpecies = species;
    v10RenderPresets();
  } finally {
    v10PresetRefreshInFlight = false;
  }
}

async function v10SavePreset() {
  const species = v10CurrentSpecies();

  if (!species) {
    throw new Error("Join the server and spawn a dinosaur first.");
  }

  const name = v10NormalizeName(elements.presetName.value);
  const skin = v10ValidateSkinData(v10SkinFromForm(), species);

  const savedPreset = await apiJson(API.presets, {
    method: "POST",
    body: JSON.stringify({
      name,
      species,
      skin,
    }),
  });

  showToast(`Preset "${savedPreset?.name || name}" saved.`);
  await v10RefreshPresets(true);
}

function v11CompactCodeToSkin(documentData) {
  if (Number(documentData?.v) !== 1) {
    throw new Error("Unsupported skin-code version.");
  }

  if (
    !Array.isArray(documentData.c)
    || documentData.c.length !== V11_SHARE_COLOR_ORDER.length
  ) {
    throw new Error("The skin code must contain exactly 10 colors.");
  }

  const skin = {
    patternIndex: Number(documentData.p),
    skinVariation: Number(documentData.var),
    isFemale: Number(documentData.g) === 1,
  };

  V11_SHARE_COLOR_ORDER.forEach((key, index) => {
    skin[key] = v11ShareColorToHex(
      documentData.c[index],
      key
    );
  });

  return skin;
}

async function v10ImportCode(text) {
  const trimmed = String(text || "").trim();

  if (!trimmed) {
    throw new Error("Paste a skin code into the text box first.");
  }

  let documentData;

  try {
    documentData = JSON.parse(trimmed);
  } catch {
    throw new Error("The pasted skin code is not valid JSON.");
  }

  let skinData;
  let importedName = "";

  if (
    Number(documentData?.v) === 1
    && Array.isArray(documentData?.c)
  ) {
    skinData = v11CompactCodeToSkin(documentData);
  } else if (
    documentData?.format === V10_EXPORT_FORMAT
    && Number(documentData?.version) === V10_EXPORT_VERSION
  ) {
    skinData = documentData;
    importedName = String(documentData.name || "").trim();
  } else {
    throw new Error("This is not a supported Mesozoic skin code.");
  }

  v10ApplySkinData(skinData);

  if (importedName) {
    elements.presetName.value = importedName.slice(0, 40);
  }

  showToast("Skin code loaded.");
}

elements.savePresetButton.addEventListener("click", async () => {
  try {
    await v10SavePreset();
  } catch (error) {
    handleError(error);
  }
});

elements.presetName.addEventListener("keydown", async event => {
  if (event.key !== "Enter") return;

  event.preventDefault();

  try {
    await v10SavePreset();
  } catch (error) {
    handleError(error);
  }
});

elements.exportCurrentSkinButton.addEventListener("click", () => {
  try {
    const species = v10CurrentSpecies();

    if (!species) {
      throw new Error("Join the server and spawn a dinosaur first.");
    }

    const skin = v10ValidateSkinData(
      v10SkinFromForm(),
      species
    );

    const requestedName = elements.presetName.value.trim();
    const name = requestedName || `${species} Skin`;

    v10DownloadSkin(
      v10ExportDocument(name, species, skin)
    );

    showToast("Skin exported to clipboard.");
  } catch (error) {
    handleError(error);
  }
});

elements.importSkinButton.addEventListener("click", async () => {
  try {
    if (!navigator.clipboard || !window.isSecureContext) {
      throw new Error(
        "Importing from the clipboard requires HTTPS or localhost."
      );
    }

    const text = String(
      await navigator.clipboard.readText()
    ).trim();

    if (!text) {
      throw new Error("Your clipboard does not contain a skin code.");
    }

    await v10ImportCode(text);
  } catch (error) {
    handleError(error);
  }
});
elements.presetList.addEventListener("click", async event => {
  const button = event.target.closest("[data-action]");
  const item = event.target.closest("[data-preset-id]");

  if (!button || !item) return;

  const presetId = Number(item.dataset.presetId);
  const preset = v10Presets.find(
    candidate => Number(candidate.id) === presetId
  );

  if (!preset) return;

  const action = button.dataset.action;

  try {
    if (action === "load") {
      v10ApplySkinData(preset.skin, preset.species);
      elements.presetName.value = preset.name;
      showToast(`Preset "${preset.name}" loaded.`);
      return;
    }

    if (action === "update") {
      const currentSpecies = v10CurrentSpecies();

      if (
        !currentSpecies
        || currentSpecies.toLowerCase() !== preset.species.toLowerCase()
      ) {
        throw new Error(
          "You can only update a preset for your current dinosaur species."
        );
      }

      const skin = v10ValidateSkinData(
        v10SkinFromForm(),
        currentSpecies
      );

      await apiJson(
        `${API.presets}/${presetId}/skin`,
        {
          method: "PATCH",
          body: JSON.stringify({ skin }),
        }
      );

      showToast(`Preset "${preset.name}" updated.`);
      await v10RefreshPresets(true);
      return;
    }

    if (action === "export") {
      const skin = v10ValidateSkinData(
        preset.skin,
        preset.species
      );

      v10DownloadSkin(
        v10ExportDocument(
          preset.name,
          preset.species,
          skin
        )
      );

      showToast(`Preset "${preset.name}" exported to clipboard.`);
      return;
    }

    if (action === "rename") {
      const newName = window.prompt(
        "Enter a new preset name:",
        preset.name
      );

      if (newName === null) return;

      const normalizedName = v10NormalizeName(newName);

      await apiJson(
        `${API.presets}/${presetId}/name`,
        {
          method: "PATCH",
          body: JSON.stringify({ name: normalizedName }),
        }
      );

      showToast("Preset renamed.");
      await v10RefreshPresets(true);
      return;
    }

    if (action === "copy") {
      const copySelect = item.querySelector("[data-copy-species]");
      const targetSpecies = String(copySelect?.value || "").trim();

      if (!targetSpecies) {
        throw new Error("Choose a dinosaur species to copy this preset to.");
      }

      const result = await apiJson(
        `${API.presets}/${presetId}/copy`,
        {
          method: "POST",
          body: JSON.stringify({ targetSpecies }),
        }
      );

      const copiedCount = Number(result?.copiedCount || 0);
      const skippedCount = Number(result?.skippedCount || 0);

      if (targetSpecies === "*") {
        const skippedText = skippedCount > 0
          ? ` ${skippedCount} skipped because their libraries are full.`
          : "";

        showToast(
          `Preset copied to ${copiedCount} dinosaur species.${skippedText}`
        );
      } else {
        showToast(`Preset copied to ${targetSpecies}.`);
      }

      if (copySelect) {
        copySelect.value = "";
      }

      return;
    }

    if (action === "delete") {
      const confirmed = window.confirm(
        `Delete the preset "${preset.name}"?`
      );

      if (!confirmed) return;

      await apiJson(
        `${API.presets}/${presetId}`,
        { method: "DELETE" }
      );

      showToast("Preset deleted.");
      await v10RefreshPresets(true);
    }
  } catch (error) {
    handleError(error);
  }
});

setInterval(() => {
  const species = v10CurrentSpecies();

  if (!species) {
    if (v10PresetSpecies || v10Presets.length > 0) {
      v10PresetSpecies = "";
      v10Presets = [];
      v10RenderPresets();
    }

    return;
  }

  if (
    species.toLowerCase()
    !== v10PresetSpecies.toLowerCase()
  ) {
    v10RefreshPresets(true).catch(error => {
      console.warn("Preset refresh failed:", error);
    });
  }
}, 1000);

setTimeout(() => {
  v10RefreshPresets(true).catch(error => {
    console.warn("Initial preset refresh failed:", error);
  });
}, 1600);

/* === miniEniac v18 responsive live controls === */

(() => {
  /*
   * The website may receive dozens of native color-picker input events
   * per second. The preview may follow every frame, but game updates are
   * capped and serialized.
   */
  const LIVE_APPLY_INTERVAL_MS = 225;
  const RETRY_WHILE_BUSY_MS = 60;

  let previewFrame = 0;
  let applyTimer = 0;
  let applyQueued = false;
  let applyRunning = false;
  let lastApplyStartedAt = 0;

  const pendingPreviewColors = new Map();
  const previewTargets = new Map();

  for (const [key] of V9_EDITOR_FIELDS) {
    previewTargets.set(
      key,
      document.querySelector(`[data-preview="${key}"]`)
    );
  }

  function queuePreviewColor(key, value) {
    pendingPreviewColors.set(key, value);

    if (previewFrame) {
      return;
    }

    previewFrame = requestAnimationFrame(() => {
      previewFrame = 0;

      for (const [pendingKey, pendingValue] of pendingPreviewColors) {
        const target = previewTargets.get(pendingKey);

        if (target) {
          target.style.backgroundColor = pendingValue;
        }
      }

      pendingPreviewColors.clear();
    });
  }

  function cancelOldApplyTimer() {
    clearTimeout(v9InstantTimer);
    v9InstantTimer = null;
  }

  function queueLiveApply(forceFinal = false) {
    if (!state.currentSnapshot) {
      return;
    }

    applyQueued = true;
    cancelOldApplyTimer();

    if (applyRunning) {
      return;
    }

    if (applyTimer) {
      if (!forceFinal) {
        return;
      }

      clearTimeout(applyTimer);
      applyTimer = 0;
    }

    const elapsed = performance.now() - lastApplyStartedAt;
    const delay = forceFinal
      ? 0
      : Math.max(0, LIVE_APPLY_INTERVAL_MS - elapsed);

    applyTimer = window.setTimeout(
      flushLiveApply,
      delay
    );
  }

  async function flushLiveApply() {
    applyTimer = 0;

    if (!applyQueued || !state.currentSnapshot) {
      return;
    }

    /*
     * Manual Apply, initial loading, or another bridge request may be
     * active. Retain the latest edit and retry instead of dropping it.
     */
    if (
      state.busy
      || applyRunning
      || v9InstantApplyInFlight
    ) {
      applyTimer = window.setTimeout(
        flushLiveApply,
        RETRY_WHILE_BUSY_MS
      );

      return;
    }

    applyQueued = false;
    applyRunning = true;
    lastApplyStartedAt = performance.now();

    try {
      await v9FlushInstantApply();
    } catch (error) {
      handleError(error);
    } finally {
      applyRunning = false;

      /*
       * More picker movements may have happened while the previous
       * request was travelling to the server. Send only the newest state.
       */
      if (applyQueued && state.currentSnapshot) {
        queueLiveApply(false);
      }
    }
  }

  /*
   * Clone color controls to remove the previous commit-only listeners.
   */
  for (const [key] of V9_EDITOR_FIELDS) {
    const oldPicker = document.getElementById(`${key}Picker`);
    const oldHex = document.getElementById(`${key}Hex`);

    if (!oldPicker || !oldHex) {
      continue;
    }

    const picker = oldPicker.cloneNode(true);
    const hex = oldHex.cloneNode(true);

    oldPicker.replaceWith(picker);
    oldHex.replaceWith(hex);

    picker.addEventListener("input", () => {
      const value = picker.value.toUpperCase();

      hex.value = value;
      queuePreviewColor(key, value);
      queueLiveApply(false);
    });

    /*
     * Native browsers normally emit change when the palette closes.
     * Force one final immediate request so the selected final color is
     * guaranteed to reach the game.
     */
    picker.addEventListener("change", () => {
      const value = picker.value.toUpperCase();

      hex.value = value;
      queuePreviewColor(key, value);
      queueLiveApply(true);
    });

    hex.addEventListener("input", () => {
      const normalized = normalizeRgb(hex.value);

      if (!normalized) {
        return;
      }

      picker.value = normalized.toLowerCase();
      hex.value = normalized;

      queuePreviewColor(key, normalized);
      queueLiveApply(false);
    });

    function commitHexColor() {
      const normalized = normalizeRgb(hex.value);

      if (!normalized) {
        hex.value = picker.value.toUpperCase();
        return;
      }

      picker.value = normalized.toLowerCase();
      hex.value = normalized;

      queuePreviewColor(key, normalized);
      queueLiveApply(true);
    }

    hex.addEventListener("change", commitHexColor);
    hex.addEventListener("blur", commitHexColor);

    hex.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        hex.blur();
      }
    });
  }

  /*
   * Make the variation slider live while dragging instead of waiting
   * until the mouse or pointer is released.
   */
  if (elements.skinVariation) {
    const oldVariation = elements.skinVariation;
    const variation = oldVariation.cloneNode(true);

    oldVariation.replaceWith(variation);
    elements.skinVariation = variation;

    variation.addEventListener("input", () => {
      updateVariationLabel();
      queueLiveApply(false);
    });

    variation.addEventListener("change", () => {
      updateVariationLabel();
      queueLiveApply(true);
    });
  }

  /*
   * Replace pattern listener so it uses the same serialized request queue.
   */
  if (elements.patternIndex) {
    const oldPattern = elements.patternIndex;
    const pattern = oldPattern.cloneNode(true);

    oldPattern.replaceWith(pattern);
    elements.patternIndex = pattern;

    pattern.addEventListener("change", () => {
      queueLiveApply(true);
    });
  }

  /*
   * Recreate the sex controls after cloning them, preventing duplicate
   * listeners left by the earlier live-editor versions.
   */
  if (elements.maleButton && elements.femaleButton) {
    const oldMale = elements.maleButton;
    const oldFemale = elements.femaleButton;

    const male = oldMale.cloneNode(true);
    const female = oldFemale.cloneNode(true);

    oldMale.replaceWith(male);
    oldFemale.replaceWith(female);

    elements.maleButton = male;
    elements.femaleButton = female;

    male.addEventListener("click", () => {
      setAssetSex(false);
      queueLiveApply(true);
    });

    female.addEventListener("click", () => {
      setAssetSex(true);
      queueLiveApply(true);
    });
  }

  /*
   * The existing interval may still fire while the browser tab is hidden.
   * Avoid making bridge requests until the player returns to the page.
   */
  const originalPollForDinosaur = v9PollForDinosaur;

  v9PollForDinosaur = async function () {
    if (document.hidden) {
      return;
    }

    return originalPollForDinosaur();
  };

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      v9PollForDinosaur().catch(error => {
        console.warn(
          "Visibility dinosaur refresh failed:",
          error
        );
      });
    }
  });

  updatePreview();
  updateVariationLabel();
})();
