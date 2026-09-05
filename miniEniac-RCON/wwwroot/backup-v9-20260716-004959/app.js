"use strict";

const API = {
  authMe: "/Auth/me",
  login: "/Auth/discord/login",
  logout: "/Auth/logout",
  inspectMine: "/Skin/me",
  applyMine: "/Skin/me/apply",
};

// Player-facing color fields. Detail1 and Eyes stay supported by the API/Lua,
// but remain hidden because current EVRIMA builds no longer use those regions.
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
  elements.refreshButton.disabled = busy;
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
  elements.refreshButton.addEventListener("click", async () => {
    try {
      await loadOwnSkin();
    } catch (error) {
      handleError(error);
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

  elements.retrySkinButton.addEventListener("click", async () => {
    try {
      await loadOwnSkin();
    } catch (error) {
      handleError(error);
    }
  });

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
