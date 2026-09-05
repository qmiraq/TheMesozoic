"use strict";

const FEATURES = Object.freeze({
  skin: { title: "Skin Creator", mode: "player", url: "/?embedded=1" },
  ai: { title: "Custom AI", mode: "headStaff", url: "/Admin/AI" },
  points: { title: "Economy Management", mode: "headStaff", native: true },
  shop: { title: "Shop Management", mode: "headStaff", native: true },
  compensation: { title: "Player Compensation", mode: "headStaff", native: true },
});
const PLAYABLE_SPECIES=["Allosaurus","Austroraptor","Beipiaosaurus","Carnotaurus","Ceratosaurus","Deinosuchus","Diabloceratops","Dilophosaurus","Dryosaurus","Gallimimus","Herrerasaurus","Hypsilophodon","Kentrosaurus","Maiasaura","Omniraptor","Pachycephalosaurus","Pteranodon","Stegosaurus","Tenontosaurus","Triceratops","Troodon","Tyrannosaurus"];
const speciesOptions=value=>PLAYABLE_SPECIES.map(x=>`<option value="${x}"${x===value?" selected":""}>${x}</option>`).join("");

const elements = {
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebarToggle"),
  sidebarOpenButton: document.getElementById("sidebarOpenButton"),
  mobileMenuButton: document.getElementById("mobileMenuButton"),
  mobileScrim: document.getElementById("mobileScrim"),
  modeButton: document.getElementById("modeButton"),
  modeButtonLabel: document.getElementById("modeButtonLabel"),
  brandPanelLabel: document.getElementById("brandPanelLabel"),
  navigationLabel: document.getElementById("navigationLabel"),
  modeEyebrow: document.getElementById("modeEyebrow"),
  pageTitle: document.getElementById("pageTitle"),
  userName: document.getElementById("userName"),
  userAvatar: document.getElementById("userAvatar"),
  userAvatarFallback: document.getElementById("userAvatarFallback"),
  userRole: document.getElementById("userRole"),
  logoutButton: document.getElementById("logoutButton"),
  loadingState: document.getElementById("loadingState"),
  errorState: document.getElementById("errorState"),
  errorMessage: document.getElementById("errorMessage"),
  retryButton: document.getElementById("retryButton"),
  featureFrame: document.getElementById("featureFrame"),
  pointManagementPanel: document.getElementById("pointManagementPanel"),
  shopManagementPanel: document.getElementById("shopManagementPanel"),
  compensationPanel: document.getElementById("compensationPanel"),
  pointStatus: document.getElementById("pointStatus"),
  refreshPointsButton: document.getElementById("refreshPointsButton"),
  boostBadge: document.getElementById("boostBadge"),
  boostSummary: document.getElementById("boostSummary"),
  boostMultiplier: document.getElementById("boostMultiplier"),
  boostMode: document.getElementById("boostMode"),
  boostDuration: document.getElementById("boostDuration"),
  boostUntil: document.getElementById("boostUntil"),
  durationField: document.getElementById("durationField"),
  untilField: document.getElementById("untilField"),
  activateBoostButton: document.getElementById("activateBoostButton"),
  stopBoostButton: document.getElementById("stopBoostButton"),
  supporterRoleRows: document.getElementById("supporterRoleRows"),
  emptyRoles: document.getElementById("emptyRoles"),
  addSupporterRoleButton: document.getElementById("addSupporterRoleButton"),
  saveSupporterRolesButton: document.getElementById("saveSupporterRolesButton"),
};

const state = {
  accessLevel: "player",
  mode: window.location.pathname.toLowerCase().endsWith("/admin") ? "headStaff" : "player",
  feature: null,
};

async function getSession() {
  const response = await fetch("/test/api/session", { credentials: "same-origin", cache: "no-store" });
  if (!response.ok) throw new Error(response.status === 403 ? "This test is not enabled for your Discord account." : `Session check failed (${response.status}).`);
  return response.json();
}

async function getDiscordProfile() {
  const response = await fetch("/Auth/me", { credentials: "same-origin" });
  return response.ok ? response.json() : null;
}

async function logout() {
  elements.logoutButton.disabled = true;
  try {
    const response = await fetch("/Auth/logout", {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok) throw new Error(`Logout failed (${response.status}).`);
    window.location.assign("/");
  } catch (error) {
    elements.logoutButton.disabled = false;
    showFrameError(error instanceof Error ? error.message : "Logout failed.");
  }
}

function setMobileMenu(open) {
  elements.sidebar.classList.toggle("is-mobile-open", open);
  elements.mobileScrim.classList.toggle("is-visible", open);
}

function setMode(mode, updatePath = true) {
  const privilegedMode = state.accessLevel === "headStaff" ? "headStaff" : "admin";
  const canUsePrivilegedMode = state.accessLevel === "admin" || state.accessLevel === "headStaff";
  state.mode = mode !== "player" && canUsePrivilegedMode ? privilegedMode : "player";
  document.body.dataset.mode = state.mode;
  const panelName = state.mode === "headStaff" ? "HEAD STAFF PANEL" : state.mode === "admin" ? "ADMIN PANEL" : "PLAYER PANEL";
  elements.navigationLabel.textContent = state.mode === "player" ? "PLAYER FEATURES" : state.mode === "headStaff" ? "HEAD STAFF FEATURES" : "ADMIN FEATURES";
  elements.modeEyebrow.textContent = panelName;
  elements.brandPanelLabel.textContent = panelName;
  elements.modeButtonLabel.textContent = state.mode !== "player"
    ? "Switch to Player Panel"
    : state.accessLevel === "headStaff" ? "Switch to Head Staff Panel" : "Switch to Admin Panel";

  if (updatePath) {
    history.replaceState({}, "", state.mode === "player" ? "/test" : "/test/admin");
  }
  selectFeature(state.mode === "player" ? "skin" : "ai");
}

async function selectFeature(featureName) {
  const feature = FEATURES[featureName];
  if (!feature || feature.mode !== state.mode) return;
  state.feature = featureName;
  elements.pageTitle.textContent = feature.title;
  document.querySelectorAll(".nav-item").forEach(button => {
    button.classList.toggle("is-active", button.dataset.feature === featureName);
  });
  elements.loadingState.classList.remove("is-hidden");
  elements.errorState.classList.add("is-hidden");
  elements.featureFrame.classList.add("is-hidden");
  elements.pointManagementPanel.classList.add("is-hidden");
  elements.shopManagementPanel.classList.add("is-hidden");
  elements.compensationPanel.classList.add("is-hidden");
  if (feature.native) {
    elements.loadingState.classList.add("is-hidden");
    const panel = featureName === "points" ? elements.pointManagementPanel : featureName === "shop" ? elements.shopManagementPanel : elements.compensationPanel;
    panel.classList.remove("is-hidden");
    if (featureName === "points") await loadPointManagement();
    if (featureName === "shop") await loadShop();
    if (featureName === "compensation") await loadCompensation();
    setMobileMenu(false);
    return;
  }
  elements.featureFrame.removeAttribute("src");
  elements.featureFrame.srcdoc = "";
  try {
    const response = await fetch(feature.url, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`The ${feature.title} returned HTTP ${response.status}.`);
    }
    let html = await response.text();
    const embeddedStyle = featureName === "skin"
      ? `.site-header{display:none!important}.page-shell{width:100%!important;max-width:none!important;padding:18px!important}.hero--login{margin-top:0!important}body{min-width:0!important}`
      : `.topbar,.hero{display:none!important}.shell{width:100%!important;max-width:none!important;padding:18px!important}body{min-width:0!important}`;
    const additions = `<base href="${feature.url}"><style data-mesozoic-panel-embed>${embeddedStyle}</style>`;
    html = html.includes("<head>")
      ? html.replace("<head>", `<head>${additions}`)
      : `${additions}${html}`;
    elements.featureFrame.srcdoc = html;
  } catch (error) {
    showFrameError(error instanceof Error ? error.message : `The ${feature.title} could not be loaded.`);
  }
  setMobileMenu(false);
}

async function headStaff(action, data={}) {
  const response=await fetch("/test/api/headstaff/execute",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({action,...data})});
  const payload=await response.json(); if(!response.ok) throw new Error(payload.error||`Request failed (${response.status})`); return payload;
}
function status(id,message,error=false){const el=document.getElementById(id);el.textContent=message;el.classList.toggle("is-hidden",!message);el.classList.toggle("is-error",error);}
async function loadShop(){try{const data=await headStaff("shop.list");const host=document.getElementById("shopListings");host.innerHTML="";for(const x of data.listings||[]){const row=document.createElement("div");row.className="shop-listing-row";row.innerHTML=`<label>Species<select data-species>${speciesOptions(x.species)}</select></label><label>Price<input data-price type="number" min="10" value="${Number(x.price)}"></label><div class="heading-actions"><button data-save class="small-button" type="button">Save</button><button data-disable class="action-button action-button--danger" type="button">${x.active?"Disable":"Enable"}</button></div>`;const update=async active=>{await headStaff("shop.update",{id:x.id,species:row.querySelector("[data-species]").value,price:Number(row.querySelector("[data-price]").value),active});status("shopStatus","Listing updated.");await loadShop()};row.querySelector("[data-save]").onclick=()=>update(Boolean(x.active));row.querySelector("[data-disable]").onclick=()=>update(!x.active);host.append(row)}status("shopStatus","")}catch(e){status("shopStatus",e.message,true)}}
async function createShop(){try{await headStaff("shop.create",{species:document.getElementById("shopSpecies").value.trim(),price:Number(document.getElementById("shopPrice").value)});status("shopStatus","Shop Token listing created.");await loadShop()}catch(e){status("shopStatus",e.message,true)}}
function target(){return {discordId:document.getElementById("compDiscord").value.trim()||null,steamId:document.getElementById("compSteam").value.trim()||null}}
const mutationFields=["MutationSlot1","MutationSlot2","MutationSlot3","MutationSlot4","ParentMutationSlot1","ParentMutationSlot2","ParentMutationSlot3","ParentMutationSlot4","ElderMutationSlot1A","ElderMutationSlot2A","ElderMutationSlot3A","ElderMutationSlot4A","ElderMutationSlot1B","ElderMutationSlot2B","ElderMutationSlot3B","ElderMutationSlot4B"];
const mutationLabels=["Slot1","Slot2","Slot3","Slot4","Baby1","Baby2","Baby3","Baby4","Entomb1","Entomb2","Entomb3","Entomb4","Entomb5","Entomb6","Entomb7","Entomb8"];
async function loadCompensation(){const species=document.getElementById("compSpecies").value;const data=await headStaff("mutation.list",{species});const host=document.getElementById("compMutationFields");host.innerHTML="";mutationFields.forEach((field,index)=>{const label=document.createElement("label");label.textContent=mutationLabels[index];const select=document.createElement("select");select.dataset.mutation=field;select.innerHTML=`<option value="">Empty</option>`+(data.mutations||[]).map(x=>`<option value="${x}">${x}</option>`).join("");label.append(select);host.append(label)})}
async function givePointsWeb(){try{await headStaff("points.give",{...target(),amount:Number(document.getElementById("compPoints").value)});status("compStatus","Points granted successfully.")}catch(e){status("compStatus",e.message,true)}}
async function giveDinoWeb(){try{const mutations={};let mutationCount=0;document.querySelectorAll("[data-mutation]").forEach((x,index)=>{mutations[x.dataset.mutation]=x.value;if(x.value)mutationCount=index+1});await headStaff("dino.give",{...target(),species:document.getElementById("compSpecies").value,growth:Number(document.getElementById("compGrowth").value),isPrime:document.getElementById("compPrime").value==="true",isFemale:document.getElementById("compFemale").value==="true",entombments:Number(document.getElementById("compEntombs").value),mutationCount,hunger:Number(document.getElementById("compHunger").value),thirst:Number(document.getElementById("compThirst").value),carb:Number(document.getElementById("compCarb").value),protein:Number(document.getElementById("compProtein").value),lipid:Number(document.getElementById("compLipid").value),mutations} );status("compStatus","Replacement dino granted successfully.")}catch(e){status("compStatus",e.message,true)}}

async function pointApi(path = "", options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`/test/api/points${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers,
  });
  const text = await response.text();
  let payload;
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = { error: text }; }
  if (!response.ok) throw new Error(payload.error || `Point Management returned HTTP ${response.status}.`);
  return payload;
}

function setPointStatus(message = "", isError = false) {
  elements.pointStatus.textContent = message;
  elements.pointStatus.classList.toggle("is-hidden", !message);
  elements.pointStatus.classList.toggle("is-error", isError);
}

function renderSupporterRoles(roles = []) {
  elements.supporterRoleRows.innerHTML = "";
  for (const role of roles) addSupporterRoleRow(role.roleId, role.pointsPerFiveMinutes);
  elements.emptyRoles.classList.toggle("is-hidden", roles.length > 0);
}

function addSupporterRoleRow(roleId = "", points = 10) {
  const row = document.createElement("div");
  row.className = "role-row";
  row.innerHTML = `<label>Discord Role ID<input data-role-id inputmode="numeric" maxlength="22" value="${String(roleId).replaceAll('"', '&quot;')}"></label><label>Points per 5 minutes<input data-role-points type="number" min="1" max="10000" step="1" value="${Number(points) || 10}"></label><button class="remove-role" type="button" aria-label="Remove supporter role">×</button>`;
  row.querySelector(".remove-role").addEventListener("click", () => {
    row.remove();
    elements.emptyRoles.classList.toggle("is-hidden", elements.supporterRoleRows.children.length > 0);
  });
  elements.supporterRoleRows.append(row);
  elements.emptyRoles.classList.add("is-hidden");
}

function renderPointManagement(payload) {
  const config = payload.config || {};
  const boost = config.boost;
  const active = payload.boostActive === true;
  elements.boostBadge.textContent = active ? "ACTIVE" : "INACTIVE";
  elements.boostBadge.classList.toggle("is-active", active);
  elements.stopBoostButton.disabled = !active;
  if (active && boost) {
    const end = new Date(boost.endsAtUtc);
    elements.boostSummary.textContent = `${Number(boost.multiplier).toFixed(2).replace(/\.00$/, "")}× point gain until ${end.toLocaleString("en-GB", { timeZone: "Europe/Paris", dateStyle: "medium", timeStyle: "short" })} Paris time.`;
  } else {
    elements.boostSummary.textContent = "No point boost is currently active.";
  }
  renderSupporterRoles(config.supporterRoles || []);
}

async function loadPointManagement() {
  setPointStatus("Loading point settings…");
  try {
    renderPointManagement(await pointApi());
    setPointStatus();
  } catch (error) {
    setPointStatus(error.message || "Point settings could not be loaded.", true);
  }
}

async function activateBoost() {
  setPointStatus("Activating point boost…");
  const body = {
    multiplier: Number(elements.boostMultiplier.value),
    mode: elements.boostMode.value,
    durationMinutes: elements.boostMode.value === "duration" ? Number(elements.boostDuration.value) : null,
    endsAtParis: elements.boostMode.value === "until" ? elements.boostUntil.value : null,
  };
  try {
    renderPointManagement(await pointApi("/boost", { method: "PUT", body: JSON.stringify(body) }));
    setPointStatus("Point boost activated.");
  } catch (error) { setPointStatus(error.message || "Boost could not be activated.", true); }
}

async function stopBoost() {
  setPointStatus("Stopping point boost…");
  try {
    renderPointManagement(await pointApi("/boost", { method: "DELETE" }));
    setPointStatus("Point boost stopped.");
  } catch (error) { setPointStatus(error.message || "Boost could not be stopped.", true); }
}

async function saveSupporterRoles() {
  const roles = [...elements.supporterRoleRows.querySelectorAll(".role-row")].map(row => ({
    roleId: row.querySelector("[data-role-id]").value.trim(),
    pointsPerFiveMinutes: Number(row.querySelector("[data-role-points]").value),
  }));
  setPointStatus("Saving supporter roles…");
  try {
    renderPointManagement(await pointApi("/supporter-roles", { method: "PUT", body: JSON.stringify({ roles }) }));
    setPointStatus("Supporter-role rates saved.");
  } catch (error) { setPointStatus(error.message || "Supporter roles could not be saved.", true); }
}

function showFrameError(message) {
  elements.loadingState.classList.add("is-hidden");
  elements.featureFrame.classList.add("is-hidden");
  elements.errorMessage.textContent = message;
  elements.errorState.classList.remove("is-hidden");
}

async function init() {
  try {
    const [session, profile] = await Promise.all([getSession(), getDiscordProfile()]);
    document.getElementById("shopSpecies").innerHTML=speciesOptions("Tyrannosaurus");
    document.getElementById("compSpecies").innerHTML=speciesOptions("Tyrannosaurus");
    const sessionDiscordId = String(session.discordId || "");
    const hasHeadStaffAccess = session.isHeadStaff === true
      || session.isAdmin === true
      || sessionDiscordId === "323802380007112704"
      || String(session.accessLevel || "").toLowerCase() === "headstaff";
    state.accessLevel = hasHeadStaffAccess
      ? "headStaff"
      : String(session.accessLevel || "").toLowerCase() === "admin" ? "admin" : "player";
    const displayName = String(profile?.displayName || session.displayName || profile?.username || "Discord user");
    elements.userName.textContent = displayName;
    elements.userAvatarFallback.textContent = displayName.slice(0, 1).toUpperCase() || "M";
    if (profile?.avatarUrl) {
      elements.userAvatar.src = profile.avatarUrl;
      elements.userAvatar.classList.remove("is-hidden");
      elements.userAvatarFallback.classList.add("is-hidden");
    }
    elements.userRole.textContent = state.accessLevel === "headStaff"
      ? "HEAD STAFF ACCESS"
      : state.accessLevel === "admin" ? "ADMIN ACCESS" : "PLAYER ACCESS";
    elements.modeButton.classList.toggle("is-hidden", state.accessLevel === "player");
    if (state.mode !== "player" && state.accessLevel === "player") state.mode = "player";
    setMode(state.mode, state.mode === "player" && window.location.pathname.toLowerCase().endsWith("/admin"));
  } catch (error) {
    showFrameError(error instanceof Error ? error.message : "The web panel could not be initialized.");
  }
}

elements.featureFrame.addEventListener("load", () => {
  elements.loadingState.classList.add("is-hidden");
  elements.errorState.classList.add("is-hidden");
  elements.featureFrame.classList.remove("is-hidden");
});
elements.featureFrame.addEventListener("error", () => showFrameError("The selected feature did not load."));
elements.modeButton.addEventListener("click", () => setMode(state.mode === "player" ? state.accessLevel : "player"));
elements.sidebarToggle.addEventListener("click", () => document.body.classList.toggle("sidebar-collapsed"));
elements.sidebarOpenButton.addEventListener("click", () => document.body.classList.remove("sidebar-collapsed"));
elements.mobileMenuButton.addEventListener("click", () => setMobileMenu(true));
elements.mobileScrim.addEventListener("click", () => setMobileMenu(false));
elements.retryButton.addEventListener("click", () => state.feature ? selectFeature(state.feature) : init());
elements.logoutButton.addEventListener("click", logout);
elements.refreshPointsButton.addEventListener("click", loadPointManagement);
elements.boostMode.addEventListener("change", () => {
  const duration = elements.boostMode.value === "duration";
  elements.durationField.classList.toggle("is-hidden", !duration);
  elements.untilField.classList.toggle("is-hidden", duration);
});
elements.activateBoostButton.addEventListener("click", activateBoost);
elements.stopBoostButton.addEventListener("click", stopBoost);
elements.addSupporterRoleButton.addEventListener("click", () => addSupporterRoleRow());
elements.saveSupporterRolesButton.addEventListener("click", saveSupporterRoles);
document.getElementById("refreshShopButton").addEventListener("click",loadShop);
document.getElementById("createShopButton").addEventListener("click",createShop);
document.getElementById("givePointsButton").addEventListener("click",givePointsWeb);
document.getElementById("giveDinoButton").addEventListener("click",giveDinoWeb);
document.getElementById("compSpecies").addEventListener("change",loadCompensation);
document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => selectFeature(button.dataset.feature)));
window.addEventListener("popstate", () => setMode(window.location.pathname.toLowerCase().endsWith("/admin") ? state.accessLevel : "player", false));

init();
