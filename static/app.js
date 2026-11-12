import { AnalysisApi } from "./api.js";
import {
  AudioRecorder,
  browserSupportsRecording,
  isBackendSupportedFilename,
} from "./recorder.js";

const MAX_AUDIO_BYTES = 20 * 1024 * 1024;
const STATE_LABELS = {
  idle: "Готово к записи",
  requesting_permission: "Запрос микрофона",
  recording: "Идет запись",
  uploading: "Отправка файла",
  processing: "Обработка",
  completed: "Готово",
  failed: "Ошибка",
};

const JOB_STATUS_TEXT = {
  PENDING: "Задача принята, ожидаем обработку…",
  TRANSCRIBING: "Распознаем речь…",
  ANALYZING: "Анализируем разговор…",
  COMPLETED: "Анализ завершен",
  FAILED: "Обработка завершилась с ошибкой",
};

const ERROR_MESSAGES = {
  UNSUPPORTED_BROWSER:
    "Браузер не поддерживает запись с микрофона. Загрузите аудиофайл.",
  UNSUPPORTED_MIME:
    "Браузер записывает в формате, который сервер не принимает. Загрузите WAV, MP3, WEBM или M4A.",
  MIC_DENIED: "Доступ к микрофону запрещен. Разрешите доступ в настройках браузера.",
  MIC_NOT_FOUND: "Микрофон не найден. Подключите устройство или загрузите файл.",
  MIC_BUSY: "Микрофон занят другим приложением.",
  MIC_ERROR: "Не удалось получить доступ к микрофону.",
  RECORDER_ERROR: "Ошибка записи. Попробуйте еще раз.",
  EMPTY_RECORDING: "Запись пустая. Проверьте микрофон и повторите.",
  FILE_TOO_LARGE: "Файл больше 20 МБ. Выберите файл меньшего размера.",
  UNSUPPORTED_FILE: "Поддерживаются только WAV, MP3, WEBM и M4A.",
  UPLOAD_IN_PROGRESS: "Отправка уже выполняется.",
  NETWORK: "Нет связи с сервером. Проверьте сеть и попробуйте снова.",
  SERVER: "Сервер временно недоступен. Попробуйте позже.",
  POLL_TIMEOUT: "Ожидание результата превысило лимит. Можно проверить статус снова.",
  TRANSCRIPTION_FAILED: "Не удалось распознать речь.",
  TRANSCRIPTION_TIMEOUT: "Распознавание речи превысило время ожидания.",
  ANALYSIS_FAILED: "Не удалось выполнить AI-анализ.",
  ANALYSIS_TIMEOUT: "AI-анализ превысил время ожидания.",
  ANALYSIS_INVALID: "Сервер вернул некорректный результат анализа.",
  EMPTY_TRANSCRIPT: "Транскрипция пустая - анализ невозможен.",
  AUDIO_UNAVAILABLE: "Аудиофайл недоступен на сервере.",
  STT_FAILED: "Ошибка транскрибации.",
  LLM_FAILED: "Ошибка AI-анализа.",
};

const els = {
  stateChip: document.getElementById("state-chip"),
  stateLabel: document.getElementById("state-label"),
  micStatus: document.getElementById("mic-status"),
  timer: document.getElementById("timer"),
  recIndicator: document.getElementById("rec-indicator"),
  btnStart: document.getElementById("btn-start"),
  btnStop: document.getElementById("btn-stop"),
  recorderHint: document.getElementById("recorder-hint"),
  dropzone: document.getElementById("dropzone"),
  fileInput: document.getElementById("file-input"),
  preview: document.getElementById("preview"),
  previewName: document.getElementById("preview-name"),
  previewSize: document.getElementById("preview-size"),
  previewAudio: document.getElementById("preview-audio"),
  btnUpload: document.getElementById("btn-upload"),
  btnClearPreview: document.getElementById("btn-clear-preview"),
  statusPanel: document.getElementById("status-panel"),
  jobIdLine: document.getElementById("job-id-line"),
  steps: document.getElementById("steps"),
  statusMessage: document.getElementById("status-message"),
  btnResumePoll: document.getElementById("btn-resume-poll"),
  btnReset: document.getElementById("btn-reset"),
  resultsPanel: document.getElementById("results-panel"),
  summaryText: document.getElementById("summary-text"),
  strengthsList: document.getElementById("strengths-list"),
  mistakesList: document.getElementById("mistakes-list"),
  recsList: document.getElementById("recs-list"),
  transcriptDetails: document.getElementById("transcript-details"),
  transcriptText: document.getElementById("transcript-text"),
  alert: document.getElementById("alert"),
  levelMeter: document.getElementById("level-meter"),
};

const state = {
  ui: "idle",
  selectedFile: null,
  objectUrl: null,
  autoUpload: false,
  timerStartedAt: 0,
  timerId: 0,
  lastJobStatus: null,
  pollInterrupted: false,
};

const api = new AnalysisApi({
  onUpdate: handleJobUpdate,
  onTerminal: handleJobTerminal,
  onTransientError: handlePollError,
});

const recorder = new AudioRecorder({
  onLevel: drawLevel,
});

function setUiState(next) {
  state.ui = next;
  els.stateChip.dataset.state = next;
  els.stateLabel.textContent = STATE_LABELS[next] || next;
  syncControls();
}

function syncControls() {
  const busy =
    state.ui === "requesting_permission" ||
    state.ui === "recording" ||
    state.ui === "uploading" ||
    state.ui === "processing";

  els.btnStart.disabled =
    !browserSupportsRecording() ||
    state.ui === "requesting_permission" ||
    state.ui === "recording" ||
    state.ui === "uploading" ||
    state.ui === "processing";

  els.btnStop.disabled = state.ui !== "recording";
  els.btnUpload.hidden = !(state.selectedFile && !state.autoUpload);
  els.btnUpload.disabled = busy || !state.selectedFile;
  els.btnClearPreview.disabled = busy;
  els.dropzone.classList.toggle("is-disabled", busy);
  els.fileInput.disabled = busy;
  els.recIndicator.hidden = state.ui !== "recording";
  els.btnResumePoll.hidden = !(
    state.pollInterrupted &&
    state.ui === "failed" &&
    api.jobId
  );
  els.btnReset.hidden = !(state.ui === "completed" || state.ui === "failed");
}

function showAlert(message) {
  if (!message) {
    els.alert.hidden = true;
    els.alert.textContent = "";
    return;
  }
  els.alert.hidden = false;
  els.alert.textContent = message;
}

function friendlyError(error) {
  const code = error?.code;
  if (code && ERROR_MESSAGES[code]) {
    return ERROR_MESSAGES[code];
  }
  if (error?.message && !String(error.message).includes("Error")) {
    return error.message;
  }
  return "Произошла ошибка. Попробуйте еще раз.";
}

function formatSize(bytes) {
  if (bytes < 1024) {
    return `${bytes} Б`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} КБ`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function formatTimer(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function startTimer() {
  stopTimer();
  state.timerStartedAt = Date.now();
  els.timer.textContent = "00:00";
  state.timerId = window.setInterval(() => {
    els.timer.textContent = formatTimer(Date.now() - state.timerStartedAt);
  }, 250);
}

function stopTimer() {
  if (state.timerId) {
    clearInterval(state.timerId);
    state.timerId = 0;
  }
}

function revokePreviewUrl() {
  if (state.objectUrl) {
    URL.revokeObjectURL(state.objectUrl);
    state.objectUrl = null;
  }
  els.previewAudio.removeAttribute("src");
  els.previewAudio.load();
}

function clearPreview() {
  revokePreviewUrl();
  state.selectedFile = null;
  state.autoUpload = false;
  els.preview.hidden = true;
  els.previewName.textContent = "";
  els.previewSize.textContent = "";
  els.fileInput.value = "";
  syncControls();
}

function setPreview(file, { autoUpload = false } = {}) {
  revokePreviewUrl();
  state.selectedFile = file;
  state.autoUpload = autoUpload;
  state.objectUrl = URL.createObjectURL(file);
  els.preview.hidden = false;
  els.previewName.textContent = file.name;
  els.previewSize.textContent = formatSize(file.size);
  els.previewAudio.src = state.objectUrl;
  syncControls();
}

function validateFile(file) {
  if (!file) {
    return Object.assign(new Error("NO_FILE"), { code: "INVALID_AUDIO" });
  }
  if (!isBackendSupportedFilename(file.name)) {
    return Object.assign(new Error("UNSUPPORTED_FILE"), {
      code: "UNSUPPORTED_FILE",
    });
  }
  if (file.size <= 0) {
    return Object.assign(new Error("EMPTY"), { code: "INVALID_AUDIO" });
  }
  if (file.size > MAX_AUDIO_BYTES) {
    return Object.assign(new Error("FILE_TOO_LARGE"), {
      code: "FILE_TOO_LARGE",
    });
  }
  return null;
}

function resetResults() {
  els.resultsPanel.hidden = true;
  els.summaryText.textContent = "";
  clearList(els.strengthsList);
  clearList(els.mistakesList);
  clearList(els.recsList);
  els.transcriptText.textContent = "";
  els.transcriptDetails.open = false;
  els.jobIdLine.hidden = true;
  els.jobIdLine.textContent = "";
  els.statusMessage.textContent = "";
  updateSteps(null);
  state.lastJobStatus = null;
  state.pollInterrupted = false;
}

function clearList(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function updateSteps(status) {
  const order = ["PENDING", "TRANSCRIBING", "ANALYZING", "COMPLETED"];
  const items = els.steps.querySelectorAll("li");
  items.forEach((item) => {
    item.classList.remove("is-current", "is-done", "is-failed");
  });
  if (!status) {
    return;
  }
  if (status === "FAILED") {
    const current =
      state.lastJobStatus && state.lastJobStatus !== "FAILED"
        ? state.lastJobStatus
        : "PENDING";
    const idx = Math.max(0, order.indexOf(current));
    items.forEach((item, i) => {
      if (i < idx) {
        item.classList.add("is-done");
      } else if (i === idx) {
        item.classList.add("is-failed");
      }
    });
    return;
  }
  const active = order.indexOf(status);
  items.forEach((item, i) => {
    if (i < active) {
      item.classList.add("is-done");
    } else if (i === active) {
      item.classList.add(status === "COMPLETED" ? "is-done" : "is-current");
    }
  });
}

function appendFinding(list, finding) {
  const li = document.createElement("li");
  const desc = document.createElement("p");
  desc.className = "finding-desc";
  desc.textContent = finding.description;
  li.appendChild(desc);
  if (finding.quote) {
    const quote = document.createElement("p");
    quote.className = "finding-quote";
    quote.textContent = finding.quote;
    li.appendChild(quote);
  }
  list.appendChild(li);
}

function renderEmpty(list, text) {
  const p = document.createElement("p");
  p.className = "empty-state";
  p.textContent = text;
  list.appendChild(p);
}

function renderResults(payload) {
  const analysis = payload.analysis;
  els.resultsPanel.hidden = false;

  if (payload.transcript) {
    els.transcriptText.textContent = payload.transcript;
  } else {
    els.transcriptText.textContent = "Транскрипция недоступна.";
  }

  if (!analysis) {
    els.summaryText.textContent = "Анализ недоступен.";
    clearList(els.strengthsList);
    clearList(els.mistakesList);
    clearList(els.recsList);
    renderEmpty(els.strengthsList, "Нет данных");
    renderEmpty(els.mistakesList, "Нет данных");
    renderEmpty(els.recsList, "Нет данных");
    return;
  }

  els.summaryText.textContent = analysis.summary || "";

  clearList(els.strengthsList);
  if (!analysis.strengths?.length) {
    renderEmpty(els.strengthsList, "Сильные стороны не выделены.");
  } else {
    analysis.strengths.forEach((item) => appendFinding(els.strengthsList, item));
  }

  clearList(els.mistakesList);
  if (!analysis.mistakes?.length) {
    renderEmpty(els.mistakesList, "Ошибок не отмечено.");
  } else {
    analysis.mistakes.forEach((item) => appendFinding(els.mistakesList, item));
  }

  clearList(els.recsList);
  if (!analysis.recommendations?.length) {
    renderEmpty(els.recsList, "Рекомендаций нет.");
  } else {
    analysis.recommendations.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      els.recsList.appendChild(li);
    });
  }
}

function stageErrorMessage(error) {
  if (!error) {
    return "Обработка завершилась с ошибкой.";
  }
  if (ERROR_MESSAGES[error.code]) {
    return ERROR_MESSAGES[error.code];
  }
  if (error.stage === "transcription" || error.stage === "stt") {
    return "Ошибка на этапе транскрибации.";
  }
  if (error.stage === "analysis" || error.stage === "llm") {
    return "Ошибка на этапе AI-анализа.";
  }
  return "Обработка завершилась с ошибкой.";
}

function handleJobUpdate(payload) {
  state.lastJobStatus = payload.status;
  els.jobIdLine.hidden = false;
  els.jobIdLine.textContent = `Задача: ${payload.job_id}`;
  els.statusMessage.textContent =
    JOB_STATUS_TEXT[payload.status] || "Обработка…";
  updateSteps(payload.status);
  if (payload.transcript && payload.status !== "COMPLETED") {
    els.resultsPanel.hidden = false;
    els.transcriptText.textContent = payload.transcript;
  }
}

function handleJobTerminal(payload) {
  state.pollInterrupted = false;
  handleJobUpdate(payload);
  if (payload.status === "COMPLETED") {
    setUiState("completed");
    showAlert("");
    renderResults(payload);
    return;
  }
  setUiState("failed");
  const message = stageErrorMessage(payload.error);
  showAlert(message);
  els.statusMessage.textContent = message;
  if (payload.transcript) {
    els.resultsPanel.hidden = false;
    els.transcriptText.textContent = payload.transcript;
    els.summaryText.textContent = "";
    clearList(els.strengthsList);
    clearList(els.mistakesList);
    clearList(els.recsList);
    renderEmpty(els.strengthsList, "Анализ не выполнен.");
    renderEmpty(els.mistakesList, "Анализ не выполнен.");
    renderEmpty(els.recsList, "Анализ не выполнен.");
  }
}

function handlePollError(error) {
  state.pollInterrupted = true;
  setUiState("failed");
  const message = friendlyError(error);
  showAlert(message);
  els.statusMessage.textContent = message;
  syncControls();
}

async function uploadSelectedFile() {
  const file = state.selectedFile;
  if (!file) {
    return;
  }
  const validation = validateFile(file);
  if (validation) {
    showAlert(friendlyError(validation));
    setUiState("failed");
    return;
  }

  showAlert("");
  resetResults();
  setUiState("uploading");
  els.statusMessage.textContent = "Отправляем аудио на сервер…";
  updateSteps("PENDING");

  try {
    const accepted = await api.upload(file);
    state.lastJobStatus = accepted.status || "PENDING";
    els.jobIdLine.hidden = false;
    els.jobIdLine.textContent = `Задача: ${accepted.job_id}`;
    setUiState("processing");
    els.statusMessage.textContent = JOB_STATUS_TEXT.PENDING;
    api.startPolling(accepted.job_id);
  } catch (error) {
    if (error?.name === "AbortError") {
      return;
    }
    setUiState("failed");
    showAlert(friendlyError(error));
    els.statusMessage.textContent = friendlyError(error);
  }
}

async function onStartRecording() {
  showAlert("");
  if (!browserSupportsRecording()) {
    showAlert(ERROR_MESSAGES.UNSUPPORTED_BROWSER);
    return;
  }
  if (!(window.isSecureContext || location.hostname === "localhost")) {
    showAlert(
      "Запись с микрофона доступна только на localhost или по HTTPS.",
    );
    return;
  }

  setUiState("requesting_permission");
  els.micStatus.textContent = "Микрофон: запрашиваем доступ…";
  try {
    await recorder.start();
    clearPreview();
    resetResults();
    api.reset();
    startTimer();
    setUiState("recording");
    els.micStatus.textContent = "Микрофон: доступ разрешен";
    els.recorderHint.textContent =
      "Говорите. После остановки запись отправится автоматически.";
  } catch (error) {
    stopTimer();
    els.timer.textContent = "00:00";
    setUiState("idle");
    if (error?.code === "MIC_DENIED") {
      els.micStatus.textContent = "Микрофон: доступ запрещен";
    } else if (error?.code === "MIC_NOT_FOUND") {
      els.micStatus.textContent = "Микрофон: не найден";
    } else {
      els.micStatus.textContent = "Микрофон: ошибка доступа";
    }
    showAlert(friendlyError(error));
  }
}

async function onStopRecording() {
  if (state.ui !== "recording") {
    return;
  }
  els.btnStop.disabled = true;
  stopTimer();
  try {
    const file = await recorder.stop();
    if (!file) {
      setUiState("idle");
      showAlert(ERROR_MESSAGES.EMPTY_RECORDING);
      return;
    }
    setPreview(file, { autoUpload: true });
    await uploadSelectedFile();
  } catch (error) {
    setUiState("failed");
    showAlert(friendlyError(error));
  }
}

function onFileChosen(file) {
  const validation = validateFile(file);
  if (validation) {
    clearPreview();
    showAlert(friendlyError(validation));
    return;
  }
  showAlert("");
  setPreview(file, { autoUpload: false });
}

function drawLevel(level, frequencyData) {
  const canvas = els.levelMeter;
  if (!canvas) {
    return;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#e8eef2";
  ctx.fillRect(0, 0, width, height);

  if (!frequencyData || state.ui !== "recording") {
    const idle = Math.max(0.04, level || 0);
    ctx.fillStyle = "#9db0be";
    ctx.fillRect(0, height * (1 - idle), width, height * idle);
    return;
  }

  const bars = 32;
  const step = Math.floor(frequencyData.length / bars);
  const gap = 2;
  const barWidth = (width - gap * (bars - 1)) / bars;
  for (let i = 0; i < bars; i += 1) {
    const value = frequencyData[i * step] / 255;
    const barHeight = Math.max(2, value * height);
    const x = i * (barWidth + gap);
    ctx.fillStyle = i % 3 === 0 ? "#0f766e" : "#5eead4";
    ctx.fillRect(x, height - barHeight, barWidth, barHeight);
  }
}

function onNewAnalysis() {
  api.reset();
  recorder.cancel();
  stopTimer();
  els.timer.textContent = "00:00";
  clearPreview();
  resetResults();
  showAlert("");
  setUiState("idle");
  els.statusMessage.textContent = "";
  els.micStatus.textContent = browserSupportsRecording()
    ? "Микрофон: готов к запросу доступа"
    : "Микрофон: запись недоступна в этом браузере";
  els.recorderHint.textContent =
    "После остановки запись отправится на анализ автоматически.";
  drawLevel(0, null);
}

function bindEvents() {
  els.btnStart.addEventListener("click", () => {
    void onStartRecording();
  });
  els.btnStop.addEventListener("click", () => {
    void onStopRecording();
  });
  els.btnUpload.addEventListener("click", () => {
    void uploadSelectedFile();
  });
  els.btnClearPreview.addEventListener("click", () => {
    clearPreview();
  });
  els.btnReset.addEventListener("click", onNewAnalysis);
  els.btnResumePoll.addEventListener("click", () => {
    showAlert("");
    state.pollInterrupted = false;
    setUiState("processing");
    els.statusMessage.textContent = "Повторно проверяем статус…";
    api.resumePolling();
  });

  els.dropzone.addEventListener("click", () => {
    if (!els.fileInput.disabled) {
      els.fileInput.click();
    }
  });
  els.dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!els.fileInput.disabled) {
        els.fileInput.click();
      }
    }
  });
  els.fileInput.addEventListener("change", () => {
    const file = els.fileInput.files?.[0];
    if (file) {
      onFileChosen(file);
    }
  });

  ["dragenter", "dragover"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    els.dropzone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropzone.classList.remove("is-dragover");
    });
  });
  els.dropzone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      onFileChosen(file);
    }
  });

  window.addEventListener("beforeunload", () => {
    recorder.cancel();
    api.reset();
    revokePreviewUrl();
  });
}

function init() {
  bindEvents();
  onNewAnalysis();
}

init();
