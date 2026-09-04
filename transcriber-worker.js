import { pipeline } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1";
import * as OpenCC from "https://cdn.jsdelivr.net/npm/opencc-js@1.0.5/dist/esm/t2cn.js";

const MODEL_ID = "onnx-community/whisper-base_timestamped";
const SAMPLE_RATE = 16000;
const TRANSCRIPTION_WINDOW_SECONDS = 30;
const toSimplifiedChinese = OpenCC.Converter({ from: "tw", to: "cn" });
const DEVICE_CONFIG = {
  webgpu: {
    device: "webgpu",
    dtype: {
      encoder_model: "fp32",
      decoder_model_merged: "q4",
    },
  },
  wasm: {
    device: "wasm",
    dtype: "q8",
  },
};

let transcriber = null;
let activeDevice = null;

function cleanTranscriptText(value) {
  return toSimplifiedChinese(String(value || ""))
    .replace(/\uFFFD/g, "")
    .replace(/([\u3400-\u9fff]{2,8}?)\1{2,}/g, "$1$1")
    .replace(/([\u3400-\u9fff])\1{3,}/g, "$1$1$1")
    .replace(/\s+/g, " ")
    .trim();
}

async function loadModel(requestId, device) {
  const selectedDevice = DEVICE_CONFIG[device] ? device : "wasm";
  self.postMessage({
    requestId,
    status: "loading",
    data: `正在加载 ${selectedDevice === "webgpu" ? "WebGPU" : "兼容"} 识别模型…`,
  });

  if (!transcriber || activeDevice !== selectedDevice) {
    transcriber = await pipeline("automatic-speech-recognition", MODEL_ID, {
      ...DEVICE_CONFIG[selectedDevice],
      progress_callback: (progress) => self.postMessage({ requestId, ...progress }),
    });
    activeDevice = selectedDevice;
  }

  self.postMessage({ requestId, status: "ready", device: activeDevice });
}

async function transcribe(requestId, { audio, language }) {
  if (!transcriber) throw new Error("识别模型尚未加载");
  self.postMessage({ requestId, status: "running", device: activeDevice });

  const options = {
    task: "transcribe",
    return_timestamps: true,
    chunk_length_s: 29,
    stride_length_s: 5,
  };
  if (language) options.language = language;

  const samples = audio instanceof Float32Array ? audio : new Float32Array(audio);
  const windowSamples = TRANSCRIPTION_WINDOW_SECONDS * SAMPLE_RATE;
  const duration = samples.length / SAMPLE_RATE;
  const chunks = [];
  const textParts = [];

  for (let start = 0; start < samples.length; start += windowSamples) {
    const end = Math.min(samples.length, start + windowSamples);
    const offsetSeconds = start / SAMPLE_RATE;
    const windowDuration = (end - start) / SAMPLE_RATE;
    const result = await transcriber(samples.subarray(start, end), options);
    const windowChunks = Array.isArray(result?.chunks) ? result.chunks : [];

    if (windowChunks.length) {
      for (const chunk of windowChunks) {
        const relativeStart = Math.max(0, Number(chunk.timestamp?.[0]) || 0);
        const relativeEnd = Number(chunk.timestamp?.[1]);
        const text = cleanTranscriptText(chunk.text);
        if (!text) continue;
        chunks.push({
          ...chunk,
          text,
          timestamp: [
            Math.min(duration, offsetSeconds + relativeStart),
            Number.isFinite(relativeEnd)
              ? Math.min(duration, offsetSeconds + Math.max(relativeStart, relativeEnd))
              : Math.min(duration, offsetSeconds + windowDuration),
          ],
        });
      }
    } else {
      const text = cleanTranscriptText(result?.text);
      if (text) {
        chunks.push({
          text,
          timestamp: [offsetSeconds, Math.min(duration, offsetSeconds + windowDuration)],
        });
      }
    }

    const windowText = cleanTranscriptText(result?.text);
    if (windowText) textParts.push(windowText);
    const progress = Math.round((end / samples.length) * 100);
    self.postMessage({
      requestId,
      status: "partial",
      progress,
      completedSeconds: end / SAMPLE_RATE,
      duration,
      result: { text: textParts.join(" "), chunks: [...chunks] },
    });
  }

  self.postMessage({
    requestId,
    status: "complete",
    result: { text: textParts.join(" "), chunks },
  });
}

self.addEventListener("message", async (event) => {
  const { requestId, type, data = {} } = event.data || {};
  try {
    if (type === "load") {
      await loadModel(requestId, data.device);
    } else if (type === "run") {
      await transcribe(requestId, data);
    } else {
      throw new Error("不支持的识别操作");
    }
  } catch (error) {
    self.postMessage({
      requestId,
      status: "error",
      message: error instanceof Error ? error.message : "浏览器识别失败",
    });
  }
});
