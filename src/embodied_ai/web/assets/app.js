const healthBadge = document.getElementById("healthBadge");
const cameraToggleBtn = document.getElementById("cameraToggleBtn");
const captureBtn = document.getElementById("captureBtn");
const pickImageBtn = document.getElementById("pickImageBtn");
const photoPickerBtn = document.getElementById("photoPickerBtn");
const clearCaptureBtn = document.getElementById("clearCaptureBtn");
const cameraPreview = document.getElementById("cameraPreview");
const captureCanvas = document.getElementById("captureCanvas");
const imagePicker = document.getElementById("imagePicker");
const photoPicker = document.getElementById("photoPicker");
const captureInfo = document.getElementById("captureInfo");
const messageList = document.getElementById("messageList");
const chatForm = document.getElementById("chatForm");
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const speakToggle = document.getElementById("speakToggle");
const voiceIdInput = document.getElementById("voiceIdInput");
const modelSelect = document.getElementById("modelSelect");
const autonomousToggle = document.getElementById("autonomousToggle");
const autonomousIntervalInput = document.getElementById("autonomousIntervalInput");
const messageTemplate = document.getElementById("messageTemplate");

const SPEAK_TOGGLE_STORAGE_KEY = "embodied_ai_speak_toggle";
const SESSION_ID_STORAGE_KEY = "embodied_ai_session_id";
const CONVERSATION_STATE_STORAGE_PREFIX = "embodied_ai_conversation_state_v1";
const MAX_CAPTURE_EDGE = 1280;
const JPEG_QUALITY = 0.82;
const WEEKDAYS_JA = ["日", "月", "火", "水", "木", "金", "土"];
const audioObjectUrls = new Set();

let cameraStream = null;
let capturedImageDataUrl = null;
let defaultModel = "";
let autonomousTimer = null;
let autonomousInFlight = false;
let autonomousMinIntervalSeconds = 3;
let warnedTtsUnavailable = false;
let hasServerConversationStore = false;
const sessionId = getOrCreateSessionId();
let conversationState = loadConversationState();

function getOrCreateSessionId() {
  const existing = localStorage.getItem(SESSION_ID_STORAGE_KEY);
  if (existing && existing.trim()) {
    return existing;
  }

  let next = "";
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    next = window.crypto.randomUUID();
  } else {
    next = `sid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  localStorage.setItem(SESSION_ID_STORAGE_KEY, next);
  return next;
}

function getConversationStateStorageKey() {
  return `${CONVERSATION_STATE_STORAGE_PREFIX}:${sessionId}`;
}

function loadConversationState() {
  const raw = localStorage.getItem(getConversationStateStorageKey());
  if (!raw) {
    return null;
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return null;
    }
    return {
      short_term: Array.isArray(parsed.short_term) ? parsed.short_term : [],
      compressed_context: typeof parsed.compressed_context === "string"
        ? parsed.compressed_context
        : "",
    };
  } catch (error) {
    console.warn("Failed to parse saved conversation state:", error);
    return null;
  }
}

function persistConversationState(nextState) {
  if (!nextState || typeof nextState !== "object") {
    conversationState = null;
    localStorage.removeItem(getConversationStateStorageKey());
    return;
  }

  const normalized = {
    short_term: Array.isArray(nextState.short_term) ? nextState.short_term : [],
    compressed_context: typeof nextState.compressed_context === "string"
      ? nextState.compressed_context
      : "",
  };
  conversationState = normalized;

  try {
    localStorage.setItem(getConversationStateStorageKey(), JSON.stringify(normalized));
  } catch (error) {
    console.warn("Failed to persist conversation state:", error);
  }
}

function getCaptureDimensions(videoWidth, videoHeight) {
  const width = videoWidth || 1280;
  const height = videoHeight || 720;
  const longest = Math.max(width, height);

  if (longest <= MAX_CAPTURE_EDGE) {
    return { width, height };
  }

  const scale = MAX_CAPTURE_EDGE / longest;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function formatJapaneseDateTime(date = new Date()) {
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const weekday = WEEKDAYS_JA[date.getDay()] || "";
  const hour = date.getHours();
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}年${month}月${day}日（${weekday}）${hour}時${minute}分`;
}

function appendMessage(role, text, imageDataUrl = null) {
  const node = messageTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  const bubble = node.querySelector(".bubble");
  bubble.textContent = text;

  if (imageDataUrl) {
    const image = document.createElement("img");
    image.src = imageDataUrl;
    image.alt = "Captured scene";
    bubble.appendChild(image);
  }

  messageList.appendChild(node);
  messageList.scrollTop = messageList.scrollHeight;
  return bubble;
}

function setBusy(isBusy) {
  sendBtn.disabled = isBusy;
  sendBtn.textContent = isBusy ? "Sending..." : "Send";
}

function createPendingAssistantBubble() {
  const bubble = appendMessage("assistant", "");
  bubble.classList.add("pending");

  const row = document.createElement("span");
  row.className = "pending-row";

  const spinner = document.createElement("span");
  spinner.className = "thinking-spinner";
  spinner.setAttribute("aria-hidden", "true");

  const label = document.createElement("span");
  label.className = "thinking-label";
  label.textContent = "考え中...";

  row.appendChild(spinner);
  row.appendChild(label);
  bubble.appendChild(row);

  return {
    bubble,
    resolve(text) {
      bubble.classList.remove("pending");
      bubble.textContent = text;
    },
  };
}

function updateCaptureUI() {
  clearCaptureBtn.disabled = !capturedImageDataUrl;

  if (!capturedImageDataUrl) {
    captureInfo.textContent = "No image attached";
    return;
  }

  captureInfo.innerHTML = "";
  const image = document.createElement("img");
  image.src = capturedImageDataUrl;
  image.alt = "Captured image";
  captureInfo.appendChild(image);
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error(`health status ${response.status}`);
    }
    const data = await response.json();
    healthBadge.textContent = data.status;
    healthBadge.classList.toggle("ready", data.status === "ok");
    if (data.default_model) {
      defaultModel = data.default_model;
    }
    const backend = data.conversation_store?.backend || "memory";
    hasServerConversationStore = backend !== "memory";
    if (!data.tts_enabled && speakToggle.checked && !warnedTtsUnavailable) {
      appendMessage("assistant", "TTSが無効やで。設定を確認してな。");
      warnedTtsUnavailable = true;
    }
    if (data.tts_enabled) {
      warnedTtsUnavailable = false;
    }
    if (data.autonomous_min_interval_seconds) {
      autonomousMinIntervalSeconds = Number(data.autonomous_min_interval_seconds);
      autonomousIntervalInput.min = String(autonomousMinIntervalSeconds);
      const current = Number(autonomousIntervalInput.value || autonomousMinIntervalSeconds);
      if (current < autonomousMinIntervalSeconds) {
        autonomousIntervalInput.value = String(autonomousMinIntervalSeconds);
      }
    }
  } catch (error) {
    healthBadge.textContent = "offline";
    healthBadge.classList.remove("ready");
    console.error(error);
  }
}

function getAutonomousIntervalMs() {
  const raw = Number(autonomousIntervalInput.value || autonomousMinIntervalSeconds);
  const safe = Math.max(autonomousMinIntervalSeconds, Math.min(raw || 0, 120));
  autonomousIntervalInput.value = String(safe);
  return safe * 1000;
}

function scheduleNextAutonomousTick() {
  if (autonomousTimer) {
    clearTimeout(autonomousTimer);
    autonomousTimer = null;
  }
  if (!autonomousToggle.checked) {
    return;
  }
  autonomousTimer = setTimeout(runAutonomousTick, getAutonomousIntervalMs());
}

async function runAutonomousTick() {
  if (!autonomousToggle.checked) {
    return;
  }
  if (autonomousInFlight) {
    scheduleNextAutonomousTick();
    return;
  }

  autonomousInFlight = true;
  try {
    let autonomousImageDataUrl = null;
    if (cameraStream) {
      try {
        autonomousImageDataUrl = captureCurrentFrameDataUrl();
      } catch (error) {
        console.warn("Autonomous frame capture failed:", error);
      }
    }

    const payload = {
      speak: speakToggle.checked,
      model: modelSelect.value,
      session_id: sessionId,
    };
    if (!hasServerConversationStore && conversationState) {
      payload.conversation_state = conversationState;
    }
    const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (browserTimezone) {
      payload.timezone = browserTimezone;
    }
    const voiceId = voiceIdInput.value.trim();
    if (voiceId) {
      payload.voice_id = voiceId;
    }
    if (autonomousImageDataUrl) {
      payload.image_base64 = dataUrlToBase64(autonomousImageDataUrl);
      payload.image_media_type = "image/jpeg";
    }

    const response = await fetch("/api/autonomous/tick", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await response.json();
    if (!response.ok) {
      if (response.status !== 429) {
        appendMessage("assistant", `自律モードエラー: ${data.detail || "Request failed"}`);
      }
      return;
    }

    if (data.conversation_state) {
      persistConversationState(data.conversation_state);
    }

    const timestamp = formatJapaneseDateTime(new Date(data.created_at));
    const bubble = appendMessage("assistant", `[AUTO ${timestamp}] ${data.reply || "(empty response)"}`);

    if (data.audio_base64 && data.audio_mime_type) {
      attachAudioPlayer(bubble, data.audio_base64, data.audio_mime_type);
    } else if (data.tts_error) {
      appendMessage("assistant", `[AUTO TTS] ${data.tts_error}`);
    }
  } catch (error) {
    appendMessage("assistant", `自律モード通信エラー: ${error.message}`);
  } finally {
    autonomousInFlight = false;
    scheduleNextAutonomousTick();
  }
}

function setModelOptions(models = [], preferred = "") {
  modelSelect.innerHTML = "";
  const target = preferred || defaultModel;

  if (!models.length) {
    const fallback = target || "claude-sonnet-4-20250514";
    const option = document.createElement("option");
    option.value = fallback;
    option.textContent = fallback;
    modelSelect.appendChild(option);
    modelSelect.value = fallback;
    return;
  }

  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.display_name || model.id;
    modelSelect.appendChild(option);
  }

  if (target) {
    modelSelect.value = target;
  }
}

async function loadModels() {
  try {
    const response = await fetch("/api/models");
    if (!response.ok) {
      throw new Error(`models status ${response.status}`);
    }
    const data = await response.json();
    defaultModel = data.default_model || defaultModel;
    setModelOptions(data.models || [], data.default_model || "");
  } catch (error) {
    console.error(error);
    setModelOptions([], defaultModel);
  }
}

async function startCamera() {
  if (cameraStream) {
    return;
  }

  if (!window.isSecureContext) {
    appendMessage(
      "assistant",
      "このページが安全な接続(HTTPS)として扱われてへんから、カメラ権限を要求できへん状態や。",
    );
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appendMessage(
      "assistant",
      "このブラウザは `getUserMedia` 非対応みたいや。`Pick Image` で画像添付して使ってな。",
    );
    return;
  }

  if (navigator.permissions && navigator.permissions.query) {
    try {
      const status = await navigator.permissions.query({ name: "camera" });
      if (status.state === "denied") {
        appendMessage(
          "assistant",
          "カメラ権限がブラウザ設定で `deny` 固定になってる。サイト設定からカメラを許可に戻すとダイアログが出るで。",
        );
        return;
      }
    } catch {
      // Some browsers (especially iOS) do not support querying camera permissions.
    }
  }

  try {
    const preferred = {
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    };
    const basic = { video: true, audio: false };
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia(preferred);
    } catch (firstError) {
      const firstName = typeof firstError?.name === "string" ? firstError.name : "";
      if (firstName === "NotFoundError" || firstName === "OverconstrainedError") {
        cameraStream = await navigator.mediaDevices.getUserMedia(basic);
      } else {
        throw firstError;
      }
    }

    cameraPreview.srcObject = cameraStream;
    captureBtn.disabled = false;
    cameraToggleBtn.textContent = "Stop";
  } catch (error) {
    const errorName = typeof error?.name === "string" ? error.name : "";
    if (errorName === "NotAllowedError" || errorName === "PermissionDeniedError") {
      appendMessage(
        "assistant",
        "カメラ権限が拒否されてるみたいや。ブラウザ/OS設定でこのサイトのカメラを許可してな。今は `Pick Image` から画像添付できるで。",
      );
      return;
    }

    if (errorName === "NotFoundError" || errorName === "OverconstrainedError") {
      appendMessage(
        "assistant",
        "使えるカメラが見つからへんかった。別アプリで使用中やないか確認して、無理なら `Pick Image` で送ってな。",
      );
      return;
    }

    if (errorName === "NotReadableError") {
      appendMessage(
        "assistant",
        "カメラにアクセスできへんかった。別アプリで掴まれてる可能性あるから、閉じてから再試行してみて。",
      );
      return;
    }

    appendMessage("assistant", `カメラ起動に失敗: ${error.message}`);
  }
}

function stopCamera() {
  if (!cameraStream) {
    return;
  }
  for (const track of cameraStream.getTracks()) {
    track.stop();
  }
  cameraStream = null;
  cameraPreview.srcObject = null;
  captureBtn.disabled = true;
  cameraToggleBtn.textContent = "Start";
}

function captureFrame() {
  if (!cameraStream) {
    return;
  }
  const { width, height } = getCaptureDimensions(
    cameraPreview.videoWidth,
    cameraPreview.videoHeight,
  );
  captureCanvas.width = width;
  captureCanvas.height = height;

  const context = captureCanvas.getContext("2d");
  context.drawImage(cameraPreview, 0, 0, width, height);
  capturedImageDataUrl = captureCanvas.toDataURL("image/jpeg", JPEG_QUALITY);
  updateCaptureUI();
}

function captureCurrentFrameDataUrl() {
  if (!cameraStream) {
    return null;
  }

  const { width, height } = getCaptureDimensions(
    cameraPreview.videoWidth,
    cameraPreview.videoHeight,
  );
  captureCanvas.width = width;
  captureCanvas.height = height;

  const context = captureCanvas.getContext("2d");
  context.drawImage(cameraPreview, 0, 0, width, height);
  return captureCanvas.toDataURL("image/jpeg", JPEG_QUALITY);
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("画像ファイルの読み込みに失敗した"));
    reader.readAsDataURL(file);
  });
}

function loadImageElement(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("画像のデコードに失敗した"));
    image.src = dataUrl;
  });
}

async function normalizeImageDataUrl(dataUrl) {
  const image = await loadImageElement(dataUrl);
  const { width, height } = getCaptureDimensions(
    image.naturalWidth,
    image.naturalHeight,
  );
  captureCanvas.width = width;
  captureCanvas.height = height;
  const context = captureCanvas.getContext("2d");
  context.drawImage(image, 0, 0, width, height);
  return captureCanvas.toDataURL("image/jpeg", JPEG_QUALITY);
}

async function handleImagePick(file) {
  if (!file) {
    return;
  }

  try {
    const dataUrl = await readFileAsDataUrl(file);
    capturedImageDataUrl = await normalizeImageDataUrl(dataUrl);
    updateCaptureUI();
  } catch (error) {
    appendMessage("assistant", `画像の添付に失敗: ${error.message}`);
  }
}

function dataUrlToBase64(dataUrl) {
  const commaPos = dataUrl.indexOf(",");
  if (commaPos < 0) {
    return dataUrl;
  }
  return dataUrl.slice(commaPos + 1);
}

function base64ToObjectUrl(base64, mimeType) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  const blob = new Blob([bytes], { type: mimeType || "audio/mpeg" });
  const url = URL.createObjectURL(blob);
  audioObjectUrls.add(url);
  return url;
}

function attachAudioPlayer(bubble, base64, mimeType) {
  if (!bubble || !base64) {
    return;
  }

  let src = "";
  try {
    src = base64ToObjectUrl(base64, mimeType);
  } catch (error) {
    appendMessage("assistant", `音声データの変換に失敗: ${error.message}`);
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "tts-wrap";

  const audio = document.createElement("audio");
  audio.className = "tts-audio";
  audio.controls = true;
  audio.preload = "auto";
  audio.src = src;
  wrap.appendChild(audio);
  bubble.appendChild(wrap);

  audio.play().catch(() => {
    const note = document.createElement("div");
    note.className = "tts-note";
    note.textContent = "自動再生できへんかった。再生ボタンを押してな。";
    wrap.appendChild(note);
  });
}

async function sendMessage(event) {
  event.preventDefault();
  const messageText = messageInput.value.trim();
  const imageDataUrl = capturedImageDataUrl || captureCurrentFrameDataUrl();
  const clientDateTime = formatJapaneseDateTime(new Date());

  if (!messageText && !imageDataUrl) {
    return;
  }

  appendMessage("user", messageText || "(image)", imageDataUrl);
  messageInput.value = "";
  setBusy(true);
  const pending = createPendingAssistantBubble();

  try {
    const payload = {
      message: messageText,
      speak: speakToggle.checked,
      model: modelSelect.value,
      session_id: sessionId,
      client_datetime: clientDateTime,
    };
    if (!hasServerConversationStore && conversationState) {
      payload.conversation_state = conversationState;
    }

    const voiceId = voiceIdInput.value.trim();
    if (voiceId) {
      payload.voice_id = voiceId;
    }

    if (imageDataUrl) {
      payload.image_base64 = dataUrlToBase64(imageDataUrl);
      payload.image_media_type = "image/jpeg";
    }

    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) {
      throw new Error(data.detail || `Request failed (${response.status})`);
    }

    if (data.conversation_state) {
      persistConversationState(data.conversation_state);
    }

    pending.resolve(data.reply || "(empty response)");
    const bubble = pending.bubble;

    if (data.audio_base64 && data.audio_mime_type) {
      attachAudioPlayer(bubble, data.audio_base64, data.audio_mime_type);
    } else if (data.tts_error) {
      appendMessage("assistant", `[TTS] ${data.tts_error}`);
    }
  } catch (error) {
    pending.resolve(`エラー: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

cameraToggleBtn.addEventListener("click", () => {
  if (cameraStream) {
    stopCamera();
  } else {
    startCamera();
  }
});

captureBtn.addEventListener("click", captureFrame);

clearCaptureBtn.addEventListener("click", () => {
  capturedImageDataUrl = null;
  updateCaptureUI();
});

pickImageBtn.addEventListener("click", () => {
  imagePicker.click();
});

photoPickerBtn.addEventListener("click", () => {
  photoPicker.click();
});

async function onPickerChange(event) {
  const input = event.target;
  const [file] = input.files || [];
  await handleImagePick(file);
  input.value = "";
}

imagePicker.addEventListener("change", onPickerChange);
photoPicker.addEventListener("change", onPickerChange);

chatForm.addEventListener("submit", sendMessage);

speakToggle.addEventListener("change", () => {
  localStorage.setItem(
    SPEAK_TOGGLE_STORAGE_KEY,
    speakToggle.checked ? "1" : "0",
  );
});

autonomousToggle.addEventListener("change", () => {
  if (!autonomousToggle.checked) {
    if (autonomousTimer) {
      clearTimeout(autonomousTimer);
      autonomousTimer = null;
    }
    return;
  }
  runAutonomousTick();
});

autonomousIntervalInput.addEventListener("change", () => {
  getAutonomousIntervalMs();
  if (autonomousToggle.checked) {
    scheduleNextAutonomousTick();
  }
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      await navigator.serviceWorker.register("/sw.js");
    } catch (error) {
      console.error("Service worker registration failed:", error);
    }
  });
}

window.addEventListener("beforeunload", () => {
  if (autonomousTimer) {
    clearTimeout(autonomousTimer);
  }
  for (const url of audioObjectUrls) {
    URL.revokeObjectURL(url);
  }
  audioObjectUrls.clear();
  stopCamera();
});

const savedSpeakToggle = localStorage.getItem(SPEAK_TOGGLE_STORAGE_KEY);
speakToggle.checked = savedSpeakToggle == null ? true : savedSpeakToggle === "1";
appendMessage("assistant", "カメラ起動か `Take Photo` / `Choose Photo` で画像添付して送ってみて。");
updateCaptureUI();
checkHealth();
loadModels();
setInterval(checkHealth, 15000);
