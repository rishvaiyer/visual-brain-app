const DEFAULT_BACKEND_URL = "http://127.0.0.1:8777";
const form = document.getElementById("settings-form");
const backendInput = document.getElementById("backend-url");
const keyInput = document.getElementById("pairing-key");
const favoriteInput = document.getElementById("favorite-default");
const status = document.getElementById("status");

loadSettings();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  status.textContent = "";
  status.classList.remove("error");

  const backendUrl = validateBackendUrl(backendInput.value);
  if (!backendUrl) {
    showError("Use a local HTTP URL such as http://127.0.0.1:8777.");
    return;
  }
  const pairingKey = keyInput.value.trim();
  if (!pairingKey) {
    showError("Enter the pairing key from your Visual Brain board.");
    return;
  }

  await chrome.storage.local.set({
    settings: {
      backendUrl,
      pairingKey,
      favorite: favoriteInput.checked
    }
  });
  chrome.runtime.sendMessage({type:"VB_REFRESH_BOARDS"}).catch(() => {});
  status.textContent = "Settings saved in this browser.";
});

async function loadSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  backendInput.value = settings?.backendUrl || DEFAULT_BACKEND_URL;
  keyInput.value = settings?.pairingKey || "";
  favoriteInput.checked = settings?.favorite === true;
  chrome.runtime.sendMessage({type:"VB_REFRESH_BOARDS"}).catch(() => {});
}

function validateBackendUrl(value) {
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase();
    const loopback = host === "localhost" || host === "[::1]" || /^127(?:\.\d{1,3}){3}$/.test(host);
    if (!loopback || url.protocol !== "http:" || url.username || url.password || url.search || url.hash) return "";
    return url.origin;
  } catch {
    return "";
  }
}

function showError(message) {
  status.textContent = message;
  status.classList.add("error");
}
