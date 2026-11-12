const PREFERRED_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/mpeg",
];

const EXTENSION_BY_MIME = {
  "audio/webm": ".webm",
  "audio/mp4": ".m4a",
  "audio/mpeg": ".mp3",
  "audio/wav": ".wav",
  "audio/wave": ".wav",
  "audio/x-wav": ".wav",
};

const BACKEND_EXTENSIONS = new Set([".wav", ".mp3", ".webm", ".m4a"]);

export function browserSupportsRecording() {
  return Boolean(
    typeof window !== "undefined" &&
      window.MediaRecorder &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === "function",
  );
}

export function pickRecorderMimeType() {
  if (!window.MediaRecorder?.isTypeSupported) {
    return "";
  }
  for (const type of PREFERRED_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) {
      return type;
    }
  }
  return "";
}

export function extensionForMime(mimeType) {
  const base = (mimeType || "").split(";")[0].trim().toLowerCase();
  return EXTENSION_BY_MIME[base] || "";
}

export function isBackendSupportedMime(mimeType) {
  const ext = extensionForMime(mimeType);
  return BACKEND_EXTENSIONS.has(ext);
}

export function isBackendSupportedFilename(name) {
  const ext = `.${String(name).split(".").pop()?.toLowerCase() || ""}`;
  return BACKEND_EXTENSIONS.has(ext);
}

export class AudioRecorder {
  constructor({ onLevel } = {}) {
    this._onLevel = onLevel;
    this._stream = null;
    this._recorder = null;
    this._chunks = [];
    this._mimeType = "";
    this._audioContext = null;
    this._analyser = null;
    this._source = null;
    this._raf = 0;
    this._starting = false;
  }

  get isRecording() {
    return this._recorder?.state === "recording";
  }

  async start() {
    if (this._starting || this.isRecording) {
      return;
    }
    if (!browserSupportsRecording()) {
      throw Object.assign(new Error("UNSUPPORTED_BROWSER"), {
        code: "UNSUPPORTED_BROWSER",
      });
    }

    this._starting = true;
    try {
      const mimeType = pickRecorderMimeType();
      if (!mimeType || !isBackendSupportedMime(mimeType)) {
        throw Object.assign(new Error("UNSUPPORTED_MIME"), {
          code: "UNSUPPORTED_MIME",
          mimeType,
        });
      }

      this._stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      this._mimeType = mimeType;
      this._chunks = [];
      this._recorder = new MediaRecorder(this._stream, { mimeType });
      this._recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) {
          this._chunks.push(event.data);
        }
      });

      this._setupMeter(this._stream);
      this._recorder.start(250);
    } catch (error) {
      this._cleanupMedia();
      throw normalizeGetUserMediaError(error);
    } finally {
      this._starting = false;
    }
  }

  stop() {
    return new Promise((resolve, reject) => {
      const recorder = this._recorder;
      if (!recorder || recorder.state === "inactive") {
        this._cleanupMedia();
        resolve(null);
        return;
      }

      const onStop = () => {
        try {
          const blob = new Blob(this._chunks, {
            type: this._mimeType || "application/octet-stream",
          });
          const ext = extensionForMime(blob.type);
          this._cleanupMedia();
          if (!ext || !BACKEND_EXTENSIONS.has(ext)) {
            reject(
              Object.assign(new Error("UNSUPPORTED_MIME"), {
                code: "UNSUPPORTED_MIME",
                mimeType: blob.type,
              }),
            );
            return;
          }
          if (blob.size === 0) {
            reject(
              Object.assign(new Error("EMPTY_RECORDING"), {
                code: "EMPTY_RECORDING",
              }),
            );
            return;
          }
          const file = new File([blob], `recording-${Date.now()}${ext}`, {
            type: blob.type,
            lastModified: Date.now(),
          });
          resolve(file);
        } catch (error) {
          this._cleanupMedia();
          reject(error);
        }
      };

      recorder.addEventListener("stop", onStop, { once: true });
      recorder.addEventListener(
        "error",
        () => {
          this._cleanupMedia();
          reject(
            Object.assign(new Error("RECORDER_ERROR"), {
              code: "RECORDER_ERROR",
            }),
          );
        },
        { once: true },
      );

      try {
        recorder.stop();
      } catch (error) {
        this._cleanupMedia();
        reject(error);
      }
    });
  }

  cancel() {
    try {
      if (this._recorder && this._recorder.state !== "inactive") {
        this._recorder.stop();
      }
    } catch {
      // ignore
    }
    this._cleanupMedia();
  }

  _setupMeter(stream) {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) {
        return;
      }
      this._audioContext = new AudioCtx();
      this._analyser = this._audioContext.createAnalyser();
      this._analyser.fftSize = 256;
      this._source = this._audioContext.createMediaStreamSource(stream);
      this._source.connect(this._analyser);
      const data = new Uint8Array(this._analyser.frequencyBinCount);

      const tick = () => {
        if (!this._analyser) {
          return;
        }
        this._analyser.getByteFrequencyData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          sum += data[i];
        }
        const level = sum / (data.length * 255);
        this._onLevel?.(level, data);
        this._raf = requestAnimationFrame(tick);
      };
      this._raf = requestAnimationFrame(tick);
    } catch {
      this._teardownMeter();
    }
  }

  _teardownMeter() {
    if (this._raf) {
      cancelAnimationFrame(this._raf);
      this._raf = 0;
    }
    try {
      this._source?.disconnect();
    } catch {
      // ignore
    }
    this._source = null;
    this._analyser = null;
    if (this._audioContext) {
      const ctx = this._audioContext;
      this._audioContext = null;
      void ctx.close().catch(() => {});
    }
  }

  _cleanupMedia() {
    this._teardownMeter();
    if (this._stream) {
      for (const track of this._stream.getTracks()) {
        track.stop();
      }
    }
    this._stream = null;
    this._recorder = null;
    this._chunks = [];
  }
}

function normalizeGetUserMediaError(error) {
  const name = error?.name || "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return Object.assign(new Error("MIC_DENIED"), { code: "MIC_DENIED" });
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return Object.assign(new Error("MIC_NOT_FOUND"), { code: "MIC_NOT_FOUND" });
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return Object.assign(new Error("MIC_BUSY"), { code: "MIC_BUSY" });
  }
  if (error?.code === "UNSUPPORTED_MIME" || error?.code === "UNSUPPORTED_BROWSER") {
    return error;
  }
  return Object.assign(new Error("MIC_ERROR"), {
    code: "MIC_ERROR",
    cause: error,
  });
}
