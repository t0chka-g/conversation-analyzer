const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

export class AnalysisApi {
  constructor({ onUpdate, onTerminal, onTransientError } = {}) {
    this._onUpdate = onUpdate;
    this._onTerminal = onTerminal;
    this._onTransientError = onTransientError;
    this._pollTimer = null;
    this._inflight = null;
    this._controller = null;
    this._jobId = null;
    this._startedAt = 0;
    this._uploadController = null;
  }

  get jobId() {
    return this._jobId;
  }

  stopPolling() {
    if (this._pollTimer !== null) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
    if (this._controller) {
      this._controller.abort();
      this._controller = null;
    }
    this._inflight = null;
  }

  reset() {
    this.stopPolling();
    if (this._uploadController) {
      this._uploadController.abort();
      this._uploadController = null;
    }
    this._jobId = null;
    this._startedAt = 0;
  }

  async upload(file) {
    if (this._uploadController) {
      throw new Error("UPLOAD_IN_PROGRESS");
    }
    this.stopPolling();
    this._jobId = null;
    this._uploadController = new AbortController();
    const body = new FormData();
    body.append("file", file, file.name);

    try {
      const response = await fetch("/api/v1/analyses", {
        method: "POST",
        body,
        signal: this._uploadController.signal,
      });
      const payload = await readJsonSafe(response);
      if (!response.ok) {
        throw mapHttpError(response.status, payload);
      }
      const jobId = payload?.job_id;
      if (!jobId) {
        throw Object.assign(new Error("Некорректный ответ сервера"), {
          code: "BAD_RESPONSE",
        });
      }
      this._jobId = jobId;
      this._startedAt = Date.now();
      return payload;
    } finally {
      this._uploadController = null;
    }
  }

  startPolling(jobId = this._jobId) {
    if (!jobId) {
      throw new Error("NO_JOB");
    }
    this.stopPolling();
    this._jobId = jobId;
    if (!this._startedAt) {
      this._startedAt = Date.now();
    }
    this._schedule(0);
  }

  resumePolling() {
    if (!this._jobId) {
      return;
    }
    this._startedAt = Date.now();
    this.startPolling(this._jobId);
  }

  _schedule(delay) {
    this._pollTimer = setTimeout(() => {
      void this._tick();
    }, delay);
  }

  async _tick() {
    if (!this._jobId || this._inflight) {
      return;
    }
    if (Date.now() - this._startedAt > POLL_TIMEOUT_MS) {
      this.stopPolling();
      this._onTransientError?.(
        Object.assign(new Error("TIMEOUT"), {
          code: "POLL_TIMEOUT",
          message: "Обработка занимает слишком много времени",
        }),
      );
      return;
    }

    this._controller = new AbortController();
    const request = fetch(`/api/v1/analyses/${this._jobId}`, {
      signal: this._controller.signal,
    });
    this._inflight = request;

    try {
      const response = await request;
      const payload = await readJsonSafe(response);
      if (!response.ok) {
        throw mapHttpError(response.status, payload);
      }
      this._onUpdate?.(payload);
      const status = payload?.status;
      if (status === "COMPLETED" || status === "FAILED") {
        this.stopPolling();
        this._onTerminal?.(payload);
        return;
      }
      this._schedule(POLL_INTERVAL_MS);
    } catch (error) {
      if (error?.name === "AbortError") {
        return;
      }
      this.stopPolling();
      this._onTransientError?.(error);
    } finally {
      this._inflight = null;
      this._controller = null;
    }
  }
}

async function readJsonSafe(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function mapHttpError(status, payload) {
  const detail = typeof payload?.detail === "string" ? payload.detail : "";
  let code = "HTTP_ERROR";
  let message = "Не удалось выполнить запрос";

  if (status === 400) {
    code = "INVALID_AUDIO";
    message = "Файл пустой или поврежден";
  } else if (status === 413) {
    code = "AUDIO_TOO_LARGE";
    message = "Файл слишком большой для сервера";
  } else if (status === 415) {
    code = "UNSUPPORTED_AUDIO";
    message = "Формат файла не поддерживается";
  } else if (status === 404) {
    code = "NOT_FOUND";
    message = "Задача анализа не найдена";
  } else if (status === 422) {
    code = "VALIDATION";
    message = "Некорректный запрос";
  } else if (status >= 500) {
    code = "SERVER";
    message = "Сервер временно недоступен";
  } else if (!responseOkNetwork(status)) {
    code = "NETWORK";
    message = "Нет связи с сервером";
  }

  return Object.assign(new Error(message), { code, status, detail });
}

function responseOkNetwork(status) {
  return Number.isFinite(status) && status > 0;
}
