import { pipeline } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1";

const MODEL_ID = "onnx-community/whisper-base_timestamped";
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

  const result = await transcriber(audio, options);
  self.postMessage({ requestId, status: "complete", result });
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
