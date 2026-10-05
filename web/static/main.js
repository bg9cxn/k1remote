/* K1Remote 前端：WebSocket 屏幕流 + 虚拟键盘 */

const $ = (id) => document.getElementById(id);

const screenCanvas = $("screen");
const ctx = screenCanvas.getContext("2d");
const frame = ctx.createImageData(128, 64);
// 琥珀色磷光风格：bit=1 → 不透明琥珀像素
const ON = [255, 191, 87];

const dotWs = $("dot-ws");
const dotRadio = $("dot-radio");
const ledRx = $("led-rx");
const ledTx = $("led-tx");
const rflogBox = $("rflog");

let ws = null;
let reconnectDelay = 1000;

function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const token = new URLSearchParams(location.search).get("token")
    ?? sessionStorage.getItem("k1token");
  if (token) {
    sessionStorage.setItem("k1token", token); // 记住，刷新/后续连接自动携带
    return `${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`;
  }
  return `${proto}://${location.host}/ws`;
}

function connect() {
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    dotWs.classList.add("on");
    reconnectDelay = 1000;
  };
  ws.onclose = () => {
    dotWs.classList.remove("on");
    dotRadio.classList.remove("on");
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 10000);
  };
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      handleBinary(ev.data);
    } else {
      handleJson(JSON.parse(ev.data));
    }
  };
}

function handleBinary(buf) {
  const view = new Uint8Array(buf);
  if (view[0] !== 0x01 || view.length < 2 + 1024) return;
  drawScreen(view.subarray(2));
}

function drawScreen(fb) {
  const d = frame.data;
  let i = 0;
  for (let y = 0; y < 64; y++) {
    const line = (y >> 3) * 128;
    const bit = 1 << (y & 7);
    for (let x = 0; x < 128; x++) {
      const on = fb[line + x] & bit;
      d[i] = ON[0];
      d[i + 1] = ON[1];
      d[i + 2] = ON[2];
      d[i + 3] = on ? 255 : 0;
      i += 4;
    }
  }
  ctx.putImageData(frame, 0, 0);
}

function handleJson(msg) {
  switch (msg.t) {
    case "hello":
    case "status":
      dotRadio.classList.toggle("on", !!msg.radio);
      ledRx.classList.toggle("active", !!msg.rx);
      ledTx.classList.toggle("active", !!msg.tx);
      break;
    case "rflog":
      renderRfLog(msg.rows || []);
      break;
    case "rtc-answer":
      if (peer) {
        peer.setRemoteDescription({ type: "answer", sdp: msg.sdp })
          .catch((err) => console.warn("answer 失败:", err));
      }
      break;
    case "ptt":
      setPttHeld(!!msg.held);
      if (msg.reason) {
        console.warn("PTT:", msg.reason);
        pttStatus.textContent = "⚠ " + msg.reason + "（请重新按 PTT）";
      }
      break;
    case "error":
      console.warn("服务端:", msg.msg);
      // 拒绝原因上浮到 PTT 状态栏（如"对讲机已在发射"），3 秒后恢复
      pttStatus.textContent = "⚠ " + msg.msg;
      setTimeout(() => { if (!pttHeld && !pttDownLocal) updatePttUi(); }, 3000);
      break;
    default:
      break;
  }
}

function renderRfLog(rows) {
  if (!rows.length) return;
  rflogBox.querySelector(".empty")?.remove();
  for (const r of rows) {
    const div = document.createElement("div");
    div.className = "rfrow" + (r.tx ? " tx" : "");
    const dir = r.tx ? "TX" : "RX";
    const mhz = (r.freq / 1e6).toFixed(4);
    div.innerHTML = `<b class="${r.tx ? "tx" : "rx"}">${dir}</b> ${mhz} MHz · ${r.name || "CH" + r.ch} · ${r.dur}s · S${r.meter}`;
    rflogBox.prepend(div);
  }
  while (rflogBox.children.length > 12) rflogBox.lastChild.remove();
}

function sendKey(name, long = false) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "key", key: name, long }));
  }
}

/* ---- 虚拟键盘：pointer 按下 450ms 触发长按，抬起触发短按 ---- */

const KEYPAD = [
  { k: "menu", label: "MENU" },
  { k: "up", label: "▲" },
  { k: "down", label: "▼" },
  { k: "exit", label: "EXIT" },
  { k: "1" }, { k: "2" }, { k: "3" }, { k: "side1", label: "SIDE1", small: true },
  { k: "4" }, { k: "5" }, { k: "6" }, { k: "side2", label: "SIDE2", small: true },
  { k: "7" }, { k: "8" }, { k: "9" },
  { k: "star", label: "✱", wide: true },
  { k: "f", label: "F (#)", wide: true },
  { k: "0" },
];

function buildKeypad() {
  const grid = $("keypad");
  for (const def of KEYPAD) {
    const btn = document.createElement("button");
    btn.className = "key" + (def.wide ? " wide" : "") + (def.small ? " small" : "");
    btn.textContent = def.label ?? def.k;
    attachPress(btn, def.k);
    grid.appendChild(btn);
  }
}

function attachPress(btn, name) {
  let timer = null;
  let longSent = false;

  const cancel = () => {
    clearTimeout(timer);
    timer = null;
  };

  btn.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    btn.classList.add("pressed");
    longSent = false;
    timer = setTimeout(() => {
      longSent = true;
      sendKey(name, true);
    }, 450);
  });
  const release = () => {
    btn.classList.remove("pressed");
    if (timer) {
      cancel();
      if (!longSent) sendKey(name, false);
    }
  };
  btn.addEventListener("pointerup", release);
  btn.addEventListener("pointercancel", () => {
    btn.classList.remove("pressed");
    cancel();
  });
}

/* ---- 物理键盘映射 ---- */

const KEY_MAP = {
  "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
  "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
  ArrowUp: "up", ArrowDown: "down",
  Enter: "menu", Escape: "exit",
  "*": "star", "#": "f", f: "f", F: "f",
};

function attachPhysicalKeys() {
  const held = new Map();
  window.addEventListener("keydown", (e) => {
    const name = KEY_MAP[e.key];
    if (!name || e.repeat || held.has(name)) return;
    held.set(name, setTimeout(() => {
      held.set(name, null);
      sendKey(name, true);
    }, 450));
  });
  window.addEventListener("keyup", (e) => {
    const name = KEY_MAP[e.key];
    if (!name || !held.has(name)) return;
    const timer = held.get(name);
    held.delete(name);
    if (timer) {
      clearTimeout(timer);
      sendKey(name, false);
    }
  });
}

/* ---- WebRTC 收听 + 麦克风上行：开始/停止 + 音量 ---- */

const listenBtn = $("listen-btn");
const volume = $("volume");
const rxAudio = $("rx-audio");
const listenHint = $("listen-hint");
let peer = null;

listenBtn.addEventListener("click", async () => {
  if (peer) {
    peer.close();
    peer = null;
    listenBtn.classList.remove("active");
    listenBtn.textContent = "▶ 开始收听";
    rxAudio.srcObject = null;
    return;
  }
  peer = new RTCPeerConnection();
  peer.addTransceiver("audio", { direction: "recvonly" });

  // 麦克风上行（localhost/HTTPS 下可用；失败则纯收听）
  let micOk = false;
  try {
    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    mic.getTracks().forEach((t) => peer.addTrack(t, mic));
    micOk = true;
  } catch (err) {
    console.warn("麦克风不可用:", err);
  }

  peer.ontrack = (ev) => {
    rxAudio.srcObject = ev.streams[0];
    rxAudio.volume = volume.value / 100;
    rxAudio.play().catch((e) => console.warn("播放失败:", e));
    // 压低浏览器抖动缓冲（默认自适应可累积到数百 ms）；100ms 平衡延迟与伪音
    for (const recv of peer.getReceivers()) {
      if ("jitterBufferTarget" in recv) recv.jitterBufferTarget = 100;
      else if ("playoutDelayHint" in recv) recv.playoutDelayHint = 0.1;
    }
    listenBtn.classList.add("active");
    listenBtn.textContent = "■ 停止收听";
    listenHint.textContent = micOk
      ? "128×64 · 收听中，麦克风已就绪（按住 PTT 发射）"
      : "128×64 · 仅收听（麦克风不可用：需 HTTPS 或 localhost）";
  };
  peer.onconnectionstatechange = () => {
    if (peer && peer.connectionState === "failed") {
      peer.close();
      peer = null;
      listenBtn.classList.remove("active");
      listenBtn.textContent = "▶ 开始收听";
    }
  };

  const offer = await peer.createOffer();
  await peer.setLocalDescription(offer);
  ws.send(JSON.stringify({ t: "rtc-offer", sdp: peer.localDescription.sdp }));
});

volume.addEventListener("input", () => {
  rxAudio.volume = volume.value / 100;
});

/* ---- PTT：使能 + 按住发射 + 计时 ---- */
/* 本地按压状态与服务器回执解耦：按下即记录、松开即无条件发释放
   （服务器幂等）。否则快速点按时，松开会发生在"已按住"回执到达之前，
   释放指令被吞 → 服务端持续发射 → 按钮卡在"发射中"。 */

const armSwitch = $("arm");
const pttBtn = $("ptt");
const pttStatus = $("ptt-status");
const pttTimer = $("ptt-timer");
let pttHeld = false;       // 服务器确认的锁存态（驱动计时与红色态）
let pttDownLocal = false;  // 本地按住状态（不依赖网络回执）
let ptxTimerInterval = null;

armSwitch.addEventListener("change", () => {
  if (!armSwitch.checked && pttDownLocal) {
    pttDownLocal = false;
    sendPtt(false); // 取消使能即撤发
  }
  updatePttUi();
});

function updatePttUi() {
  pttBtn.disabled = !armSwitch.checked;
  pttBtn.classList.toggle("ready", armSwitch.checked && !pttHeld && !pttDownLocal);
  pttBtn.classList.toggle("held", pttHeld || pttDownLocal);
  if (!pttHeld && !pttDownLocal) {
    pttStatus.textContent = armSwitch.checked ? "已使能，按住 PTT 说话" : "未使能";
  }
}

function sendPtt(on) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ t: "ptt", on }));
  }
}

pttBtn.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  if (!armSwitch.checked || pttDownLocal) return;
  try { pttBtn.setPointerCapture(e.pointerId); } catch (err) { /* 合成事件无有效指针 */ }
  pttDownLocal = true;
  updatePttUi();
  sendPtt(true);
});

// 释放兜底：按钮内松开、指针捕获丢失、移出窗口、系统取消——全部覆盖
function pttRelease() {
  if (!pttDownLocal) return;
  pttDownLocal = false;
  updatePttUi();
  sendPtt(false);
}
pttBtn.addEventListener("pointerup", pttRelease);
pttBtn.addEventListener("pointercancel", pttRelease);
pttBtn.addEventListener("lostpointercapture", pttRelease);
window.addEventListener("pointerup", pttRelease);
window.addEventListener("blur", pttRelease);

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && armSwitch.checked) {
    e.preventDefault();
    if (!pttDownLocal) {
      pttDownLocal = true;
      updatePttUi();
      sendPtt(true);
    }
  }
});
window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    pttRelease();
  }
});

function setPttHeld(held) {
  pttHeld = held;
  updatePttUi();
  if (held) {
    const start = Date.now();
    clearInterval(ptxTimerInterval);
    ptxTimerInterval = setInterval(() => {
      pttTimer.textContent = `${((Date.now() - start) / 1000).toFixed(0)}s`;
    }, 300);
  } else if (!pttDownLocal) {
    clearInterval(ptxTimerInterval);
    ptxTimerInterval = null;
    pttTimer.textContent = "";
  }
}

buildKeypad();
attachPhysicalKeys();
connect();
