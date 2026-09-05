const $ = id => document.getElementById(id);

// ── Mode state ────────────────────────────────────────────────────────────
let currentMode = null;
let ws          = null;

// ── Element refs: shared ──────────────────────────────────────────────────
const apiBadge      = $('api-badge');
const settingsBtn   = $('settings-btn');
const modalOverlay  = $('modal-overlay');
const cfgName       = $('cfg-name');
const cfgKey        = $('cfg-key');
const cfgToggle     = $('cfg-toggle');
const cfgTest       = $('cfg-test');
const cfgCancel     = $('cfg-cancel');
const cfgSave       = $('cfg-save');
const cfgMsg        = $('cfg-msg');
const modeSelection = $('mode-selection');
const modeTabs      = $('mode-tabs');
const chooseInterviewer = $('choose-interviewer');
const chooseApplicant = $('choose-applicant');
const backToModeSelection = $('back-to-mode-selection');
const tabInterviewer = $('tab-interviewer');
const tabApplicant   = $('tab-applicant');

// ── Element refs: interviewer mode ────────────────────────────────────────
const companyInfoEl  = $('company-info');
const resumeEl       = $('resume');
const pasteResumeBtn = $('paste-resume');
const loadResumePdfBtn   = $('load-resume-pdf');
const loadResumeTextBtn  = $('load-resume-text');
const loadResumeImageBtn = $('load-resume-image');
const resumePdfFile      = $('resume-pdf-file');
const resumeTextFile     = $('resume-text-file');
const resumeImageFile    = $('resume-image-file');
const resumeImportStatus = $('resume-import-status');
const logEl          = $('interview-log');
const micSelect      = $('mic-select');
const deviceSelect   = $('device-select');
const voiceStart     = $('voice-start');
const voiceStop      = $('voice-stop');
const voiceStatus    = $('voice-status');
const levelStatus    = $('level-status');
const speakerEl      = $('speaker');
const textInputEl    = $('text-input');
const sendBtn        = $('send-btn');

// ── Element refs: applicant mode ──────────────────────────────────────────
const appCompanyInfo     = $('app-company-info');
const appConversationLog = $('app-conversation-log');
const appMicSelect       = $('app-mic-select');
const appDeviceSelect    = $('app-device-select');
const appVoiceStart      = $('app-voice-start');
const appVoiceStop       = $('app-voice-stop');
const appVoiceStatus     = $('app-voice-status');
const appLevelStatus     = $('app-level-status');
const appSpeakerEl       = $('app-speaker');
const appTextInputEl     = $('app-text-input');
const appSendBtn         = $('app-send-btn');
const appQuestionBtn     = $('app-question-btn');
const appQuestionLoading = $('app-question-loading');
const appQuestionResult  = $('app-question-result');
const appQuestionText    = $('app-question-text');
const appQuestionTokens  = $('app-question-tokens');
const appQuestionError   = $('app-question-error');

// ── Mode switching ────────────────────────────────────────────────────────

tabInterviewer.addEventListener('click', () => switchMode('interviewer'));
tabApplicant.addEventListener('click',   () => switchMode('applicant'));
chooseInterviewer.addEventListener('click', () => switchMode('interviewer'));
chooseApplicant.addEventListener('click', () => switchMode('applicant'));
backToModeSelection.addEventListener('click', showModeSelection);

function switchMode(mode) {
  if (mode !== 'interviewer' && mode !== 'applicant') return;
  if (currentMode === mode) return;
  const wasRecording = !!ws;
  const stopped = wasRecording ? stopVoice() : Promise.resolve();
  currentMode = mode;
  modeSelection.classList.add('hidden');
  modeTabs.classList.remove('hidden');
  $('interviewer-mode').classList.toggle('hidden', mode !== 'interviewer');
  $('applicant-mode').classList.toggle('hidden',   mode !== 'applicant');
  tabInterviewer.classList.toggle('mode-tab--active', mode === 'interviewer');
  tabApplicant.classList.toggle('mode-tab--active',   mode === 'applicant');
  if (wasRecording) {
    // 停止処理は最後の発話の認識を待つので、完了後にメッセージを出す
    stopped.then(() => {
      const statusEl = mode === 'interviewer' ? voiceStatus : appVoiceStatus;
      statusEl.textContent = 'タブ切り替えのため録音を停止しました。';
      statusEl.className   = 'voice-status';
    });
  }
}

async function showModeSelection() {
  if (ws || stopPromise) await stopVoice();
  currentMode = null;
  $('interviewer-mode').classList.add('hidden');
  $('applicant-mode').classList.add('hidden');
  modeTabs.classList.add('hidden');
  modeSelection.classList.remove('hidden');
}

// ── 履歴書の入力・ファイル読込 ────────────────────────────────────────────

let resumeImportId = 0;

function setResumeImportStatus(message, state = '') {
  resumeImportStatus.textContent = message;
  resumeImportStatus.className = `resume-import-status${state ? ` ${state}` : ''}`;
}

function appendResumeText(text, label = '') {
  const trimmed = text.trim();
  if (!trimmed) return false;
  const block = label ? `【${label}】\n${trimmed}` : trimmed;
  resumeEl.value = resumeEl.value.trim()
    ? `${resumeEl.value.trim()}\n\n${block}`
    : block;
  resumeEl.scrollTop = resumeEl.scrollHeight;
  saveDraft();
  return true;
}

function resetResumeImport() {
  resumeImportId += 1;
  resumePdfFile.value = '';
  resumeTextFile.value = '';
  resumeImageFile.value = '';
  loadResumePdfBtn.disabled = false;
  loadResumeTextBtn.disabled = false;
  loadResumeImageBtn.disabled = false;
  setResumeImportStatus('');
}

pasteResumeBtn.addEventListener('click', async () => {
  resumeEl.focus();
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      resumeEl.value = text;
      saveDraft();
      setResumeImportStatus('クリップボードの文字を貼り付けました。', 'ok');
      return;
    }
  } catch (_) {}
  document.execCommand('paste');
});

loadResumePdfBtn.addEventListener('click', () => resumePdfFile.click());
loadResumeTextBtn.addEventListener('click', () => resumeTextFile.click());
loadResumeImageBtn.addEventListener('click', () => resumeImageFile.click());

function decodeResumeText(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.length >= 3 && bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return new TextDecoder('utf-8').decode(bytes.subarray(3));
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (_) {
    return new TextDecoder('shift_jis').decode(bytes);
  }
}

resumeTextFile.addEventListener('change', async () => {
  const file = resumeTextFile.files?.[0];
  resumeTextFile.value = '';
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    setResumeImportStatus('文字ファイルは2MB以下にしてください。', 'error');
    return;
  }

  loadResumeTextBtn.disabled = true;
  setResumeImportStatus(`${file.name} を読み込んでいます...`, 'active');
  try {
    const text = decodeResumeText(await file.arrayBuffer());
    if (!appendResumeText(text, file.name)) {
      throw new Error('文字が入っていません。');
    }
    setResumeImportStatus(`${file.name} の文字を読み込みました。内容を確認・修正できます。`, 'ok');
  } catch (error) {
    setResumeImportStatus(`${file.name} を読み込めませんでした：${error.message}`, 'error');
  } finally {
    loadResumeTextBtn.disabled = false;
  }
});

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunks = [];
  for (let i = 0; i < bytes.length; i += 0x8000) {
    chunks.push(String.fromCharCode(...bytes.subarray(i, i + 0x8000)));
  }
  return btoa(chunks.join(''));
}

resumePdfFile.addEventListener('change', async () => {
  const file = resumePdfFile.files?.[0];
  resumePdfFile.value = '';
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    setResumeImportStatus('PDFは20MB以下にしてください。', 'error');
    return;
  }

  const requestId = ++resumeImportId;
  loadResumePdfBtn.disabled = true;
  setResumeImportStatus(`${file.name} を解析しています...`, 'active');
  try {
    const res = await fetch('/api/resume/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filename: file.name,
        pdf_base64: arrayBufferToBase64(await file.arrayBuffer()),
      }),
    });
    const data = await res.json();
    if (requestId !== resumeImportId) return;
    if (!res.ok) throw new Error(data.detail || 'PDFを読み込めませんでした。');

    appendResumeText(data.text, file.name);
    const pageSummary = `${data.pages}ページ中、${data.ocr_pages}ページを画像OCRしました。`;
    if (data.warnings?.length) {
      setResumeImportStatus(`${pageSummary} ${data.warnings.join(' / ')}`, 'error');
    } else {
      setResumeImportStatus(`${file.name} を読み込みました。${pageSummary}`, 'ok');
    }
  } catch (error) {
    if (requestId === resumeImportId) {
      setResumeImportStatus(`${file.name} を読み込めませんでした：${error.message}`, 'error');
    }
  } finally {
    if (requestId === resumeImportId) loadResumePdfBtn.disabled = false;
  }
});

resumeImageFile.addEventListener('change', async () => {
  const files = Array.from(resumeImageFile.files || []);
  resumeImageFile.value = '';
  if (!files.length) return;
  if (files.length > 5) {
    setResumeImportStatus('画像は一度に5枚まで選択できます。', 'error');
    return;
  }

  const requestId = ++resumeImportId;
  loadResumeImageBtn.disabled = true;
  let imported = 0;
  const errors = [];

  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    if (requestId !== resumeImportId) break;
    if (file.size > 12 * 1024 * 1024) {
      errors.push(`${file.name}：12MBを超えています`);
      continue;
    }

    setResumeImportStatus(`${file.name} をOCR中です（${index + 1}/${files.length}）...`, 'active');
    try {
      const res = await fetch('/api/resume/ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          image_base64: arrayBufferToBase64(await file.arrayBuffer()),
        }),
      });
      const data = await res.json();
      if (requestId !== resumeImportId) break;
      if (!res.ok) throw new Error(data.detail || '文字を認識できませんでした。');
      if (appendResumeText(data.text, file.name)) imported += 1;
    } catch (error) {
      errors.push(`${file.name}：${error.message}`);
    }
  }

  if (requestId === resumeImportId) {
    loadResumeImageBtn.disabled = false;
    if (errors.length) {
      const summary = imported ? `${imported}枚を読み込みました。` : '';
      setResumeImportStatus(`${summary}${errors.join(' / ')}`, 'error');
    } else {
      setResumeImportStatus(`${imported}枚の画像から文字を読み込みました。内容を確認・修正してください。`, 'ok');
    }
  }
});

// ── 下書きの自動保存（localStorage）──────────────────────────────────────

const DRAFT_KEY = 'noesia_draft_v1';
const draftFields = {
  company_info:     companyInfoEl,
  resume:           resumeEl,
  interview_log:    logEl,
  app_company_info: appCompanyInfo,
  app_conversation_log: appConversationLog,
};

let draftTimer = null;
function saveDraft() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    const data = {};
    for (const [key, el] of Object.entries(draftFields)) data[key] = el.value;
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(data)); } catch (_) {}
  }, 400);
}

function restoreDraft() {
  try {
    const data = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}');
    for (const [key, el] of Object.entries(draftFields)) {
      if (data[key]) el.value = data[key];
    }
  } catch (_) {}
}

for (const el of Object.values(draftFields)) el.addEventListener('input', saveDraft);

// ── 起動時初期化 ──────────────────────────────────────────────────────────

async function init() {
  restoreDraft();
  await loadConfig();
  await loadDevices();
}

// ── 設定 ──────────────────────────────────────────────────────────────────

async function loadConfig() {
  try {
    const res  = await fetch('/api/config');
    const data = await res.json();
    cfgName.value = data.api_name || '';
    cfgKey.value  = data.api_key  || '';
    updateApiBadge(!!data.api_key);
    return data;
  } catch (_) {
    return {};
  }
}

function updateApiBadge(ok) {
  apiBadge.textContent = ok ? 'APIキー設定済み' : 'APIキー未設定';
  apiBadge.className   = 'api-badge ' + (ok ? 'api-badge--set' : 'api-badge--none');
}

settingsBtn.addEventListener('click', () => {
  cfgMsg.className = 'cfg-msg hidden';
  modalOverlay.classList.remove('hidden');
});
cfgCancel.addEventListener('click', () => modalOverlay.classList.add('hidden'));
modalOverlay.addEventListener('click', e => {
  if (e.target === modalOverlay) modalOverlay.classList.add('hidden');
});

function makeToggle(inputEl, btn) {
  btn.addEventListener('click', () => {
    const show = inputEl.type === 'password';
    inputEl.type = show ? 'text' : 'password';
    btn.textContent = show ? '隠す' : '表示';
  });
}
makeToggle(cfgKey, cfgToggle);

cfgSave.addEventListener('click', async () => {
  cfgSave.disabled = true;
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        api_name: cfgName.value,
        api_key:  cfgKey.value,
      }),
    });
    if (res.ok) {
      cfgMsg.textContent = '保存しました。';
      cfgMsg.className   = 'cfg-msg ok';
      updateApiBadge(!!cfgKey.value);
      setTimeout(() => modalOverlay.classList.add('hidden'), 800);
    } else {
      cfgMsg.textContent = '保存に失敗しました。';
      cfgMsg.className   = 'cfg-msg fail';
    }
  } catch (_) {
    cfgMsg.textContent = '保存に失敗しました。';
    cfgMsg.className   = 'cfg-msg fail';
  } finally {
    cfgSave.disabled = false;
  }
});

cfgTest.addEventListener('click', async () => {
  cfgTest.disabled = true;
  cfgMsg.textContent = '接続を確認しています...';
  cfgMsg.className   = 'cfg-msg info';
  try {
    const res = await fetch('/api/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: cfgKey.value }),
    });
    const data = await res.json();
    if (res.ok) {
      const preview = (data.reply || '').slice(0, 60);
      cfgMsg.textContent = `接続成功。人格の返答：「${preview}」`;
      cfgMsg.className   = 'cfg-msg ok';
    } else {
      cfgMsg.textContent = data.detail || '接続に失敗しました。';
      cfgMsg.className   = 'cfg-msg fail';
    }
  } catch (_) {
    cfgMsg.textContent = 'サーバーに接続できませんでした。';
    cfgMsg.className   = 'cfg-msg fail';
  } finally {
    cfgTest.disabled = false;
  }
});

// ── デバイス一覧 ──────────────────────────────────────────────────────────

function populateSelect(selectEl, items, savedValue, emptyMsg, autoLabel) {
  selectEl.innerHTML = '';
  if (!items.length && !autoLabel) {
    selectEl.innerHTML = `<option value="">${emptyMsg || 'なし'}</option>`;
    return false;
  }
  if (autoLabel) {
    const opt = document.createElement('option');
    // ループバックデバイスが1つも無くても、マイクだけの録音は成立するので選択肢を残す
    opt.value = '-1';
    opt.textContent = items.length ? autoLabel : 'PC再生音声なし（マイクのみ録音）';
    selectEl.appendChild(opt);
  }
  items.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.index; opt.textContent = d.name;
    selectEl.appendChild(opt);
  });
  if (savedValue != null) selectEl.value = savedValue;
  // 保存されていたデバイスが今は存在しない（indexが変わった等）場合は先頭に戻す
  if (!selectEl.value) selectEl.selectedIndex = 0;
  return true;
}

async function loadDevices() {
  try {
    const [micRes, devRes, cfgRes] = await Promise.all([
      fetch('/api/microphones'),
      fetch('/api/devices'),
      fetch('/api/config'),
    ]);
    const mics    = await micRes.json();
    const devices = await devRes.json();
    const config  = await cfgRes.json();

    populateSelect(micSelect,    mics,    config.mic_device,   'マイクが見つかりません');
    populateSelect(appMicSelect, mics,    config.mic_device,   'マイクが見つかりません');

    const AUTO_LABEL = '自動（すべての再生デバイスを監視・推奨）';
    populateSelect(deviceSelect,    devices, config.audio_device, 'ループバックデバイスが見つかりません', AUTO_LABEL);
    populateSelect(appDeviceSelect, devices, config.audio_device, 'ループバックデバイスが見つかりません', AUTO_LABEL);

    micGainCtrl.set(config.mic_gain || 1);
    loopGainCtrl.set(config.loopback_gain || 1);
  } catch (_) {
    for (const el of [deviceSelect, appDeviceSelect]) {
      el.innerHTML = '<option value="">デバイス取得エラー</option>';
    }
    voiceStart.disabled    = true;
    appVoiceStart.disabled = true;
  }
}

// ── 音量ブースト ──────────────────────────────────────────────────────────
// 両モードのスライダーを同期し、設定に保存。録音中は WebSocket 経由で即時反映する。

function setupGainPair(sliderIds, valIds, configKey, wsKey) {
  const sliders = sliderIds.map($);
  const vals    = valIds.map($);
  let saveTimer = null;

  function apply(value, save) {
    sliders.forEach(s => { s.value = value; });
    vals.forEach(v => { v.textContent = `×${value}`; });
    if (!save) return;
    if (ws) { try { ws.send(JSON.stringify({ [wsKey]: Number(value) })); } catch (_) {} }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [configKey]: Number(value) }),
      }).catch(() => {});
    }, 400);
  }

  sliders.forEach(s => s.addEventListener('input', () => apply(s.value, true)));
  return { set: v => apply(v, false), value: () => sliders[0].value };
}

const micGainCtrl = setupGainPair(
  ['mic-gain', 'app-mic-gain'], ['mic-gain-val', 'app-mic-gain-val'],
  'mic_gain', 'mic_gain');
const loopGainCtrl = setupGainPair(
  ['device-gain', 'app-device-gain'], ['device-gain-val', 'app-device-gain-val'],
  'loopback_gain', 'loopback_gain');

// 選択変更時にすぐ保存し、もう一方のモードのセレクトにも反映する
function persistDeviceOnChange(configKey, selectEl, mirrorEl) {
  selectEl.addEventListener('change', async () => {
    if (mirrorEl) mirrorEl.value = selectEl.value;
    const value = selectEl.value === '' ? null : parseInt(selectEl.value);
    try {
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [configKey]: value }),
      });
    } catch (_) {}
  });
}
persistDeviceOnChange('mic_device',   micSelect,       appMicSelect);
persistDeviceOnChange('mic_device',   appMicSelect,    micSelect);
persistDeviceOnChange('audio_device', deviceSelect,    appDeviceSelect);
persistDeviceOnChange('audio_device', appDeviceSelect, deviceSelect);

// ── 音声認識（WebSocket）─────────────────────────────────────────────────

// 録音中の表示先・ログの追記先。開始時に固定しておくことで、停止処理の途中で
// 届いた認識結果が別モードのログに紛れ込むことがない
let activeUI    = null;
// 停止処理中はこの Promise が立つ。完了＝サーバーが最後の発話まで認識し終えた
let stopPromise = null;
let stopResolve = null;
let stopTimer   = null;

function startVoice(devSel, micSel) {
  // 接続完了前に連打されると ws が上書きされ、止められない録音セッションが残るため入口で弾く
  if (ws || stopPromise) return;

  const isInterviewer   = currentMode === 'interviewer';
  const ui = {
    statusEl: isInterviewer ? voiceStatus : appVoiceStatus,
    levelEl:  isInterviewer ? levelStatus : appLevelStatus,
    startBtn: isInterviewer ? voiceStart  : appVoiceStart,
    stopBtn:  isInterviewer ? voiceStop   : appVoiceStop,
    appendFn: isInterviewer ? appendToLog : appendToAppLog,
  };

  // ループバックデバイスが無い環境でも -1（自動）でマイクのみ録音できる
  const deviceIndex     = devSel.value === '' ? '-1' : devSel.value;
  const micSpeaker      = isInterviewer ? '面接官' : 'あなた';
  const loopbackSpeaker = isInterviewer ? '志望者' : '企業担当者';
  const micParam        = micSel.value === '' ? '' : `&mic=${micSel.value}`;

  activeUI = ui;
  // onopen を待たずに切り替える（待つと連打で複数セッションが立つ）
  ui.startBtn.classList.add('hidden');
  ui.stopBtn.classList.remove('hidden');
  ui.stopBtn.disabled     = false;
  ui.statusEl.textContent = '接続中...';
  ui.statusEl.className   = 'voice-status active';

  ws = new WebSocket(
    `ws://${location.host}/ws/transcribe?device=${deviceIndex}${micParam}` +
    `&mic_speaker=${encodeURIComponent(micSpeaker)}&loopback_speaker=${encodeURIComponent(loopbackSpeaker)}` +
    `&mic_gain=${micGainCtrl.value()}&loopback_gain=${loopGainCtrl.value()}`
  );

  ws.onopen = () => {
    ui.statusEl.textContent = '● 録音中 — 声を自動識別しています';
    ui.statusEl.className   = 'voice-status active';
  };

  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.error) {
      stopRecording();
      ui.statusEl.textContent = `エラー: ${data.error}`;
      ui.statusEl.className   = 'voice-status error';
    } else if (data.warn) {
      // 録音は継続したまま、デバイス選択ミス等のヒントを表示する
      ui.statusEl.textContent = `⚠ ${data.warn}`;
      ui.statusEl.className   = 'voice-status warn';
    } else if (data.status) {
      ui.statusEl.textContent = data.status;
      ui.statusEl.className   = 'voice-status active';
    } else if (data.level) {
      const bar = p => '▮'.repeat(Math.min(10, Math.ceil(p / 10))).padEnd(10, '▯');
      ui.levelEl.textContent =
        `マイク ${bar(data.level.mic)} ${data.level.mic}%　` +
        `PC音声 ${bar(data.level.loopback)} ${data.level.loopback}%`;
    } else if (data.speaker && data.text) {
      ui.appendFn(data.speaker, data.text);
      const head = stopPromise ? '停止処理中' : '● 録音中';
      ui.statusEl.textContent = `${head} — 最後の認識: ${data.speaker}「${data.text.slice(0, 30)}」`;
      ui.statusEl.className   = 'voice-status active';
    }
  };

  ws.onclose = () => stopRecording();
  ws.onerror = () => {
    stopRecording();
    ui.statusEl.textContent = '接続エラーが発生しました。';
    ui.statusEl.className   = 'voice-status error';
  };
}

// 録音停止。サーバーが停止直前の発話を認識し終えてソケットを閉じるまで待つ。
// ここで即 close すると、無音0.8秒による発話確定を待っていた最後の一言が失われる
function stopVoice() {
  if (!ws) {
    stopRecording();
    return Promise.resolve();
  }
  if (stopPromise) return stopPromise;

  const socket = ws;
  const ui     = activeUI;
  if (ui) {
    ui.stopBtn.disabled     = true;
    ui.statusEl.textContent = '停止処理中 — 最後の発話を認識しています...';
    ui.statusEl.className   = 'voice-status active';
  }

  // 待ちの解除は stopRecording() に集約する。認識エラーなど別経路で録音が
  // 終わった場合でも、この Promise が取り残されないようにするため
  stopPromise = new Promise(resolve => { stopResolve = resolve; });
  // サーバーが応答しないまま固まった場合の保険
  stopTimer = setTimeout(stopRecording, 30000);
  socket.onclose = stopRecording;
  socket.onerror = stopRecording;
  try { socket.send('stop'); } catch (_) { stopRecording(); }

  return stopPromise;
}

voiceStart.addEventListener('click',    () => startVoice(deviceSelect,    micSelect));
voiceStop.addEventListener('click',     () => stopVoice());
appVoiceStart.addEventListener('click', () => startVoice(appDeviceSelect, appMicSelect));
appVoiceStop.addEventListener('click',  () => stopVoice());

function stopRecording() {
  clearTimeout(stopTimer);
  stopTimer = null;
  if (ws) {
    // close() 後に onclose が再入してステータス表示を消してしまうため、先にハンドラを外す
    ws.onmessage = null;
    ws.onclose   = null;
    ws.onerror   = null;
    try { ws.close(); } catch (_) {}
    ws = null;
  }
  activeUI = null;
  for (const [status, level, start, stop] of [
    [voiceStatus,    levelStatus,    voiceStart,    voiceStop],
    [appVoiceStatus, appLevelStatus, appVoiceStart, appVoiceStop],
  ]) {
    status.textContent = '';
    status.className   = 'voice-status';
    level.textContent  = '';
    start.classList.remove('hidden');
    stop.classList.add('hidden');
    stop.disabled = false;
  }
  if (stopResolve) {
    const resolve = stopResolve;
    stopResolve = null;
    stopPromise = null;
    resolve();
  }
}

// ── ログ追記 ──────────────────────────────────────────────────────────────

function appendToLog(speaker, text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  const line = `${speaker}：${trimmed}`;
  logEl.value = logEl.value ? logEl.value + '\n' + line : line;
  logEl.scrollTop = logEl.scrollHeight;
  saveDraft();
}

function appendToAppLog(speaker, text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  const line = `${speaker}：${trimmed}`;
  appConversationLog.value = appConversationLog.value ? appConversationLog.value + '\n' + line : line;
  appConversationLog.scrollTop = appConversationLog.scrollHeight;
  saveDraft();
}

// ── テキスト入力 ──────────────────────────────────────────────────────────

sendBtn.addEventListener('click', () => {
  appendToLog(speakerEl.value, textInputEl.value);
  textInputEl.value = '';
  textInputEl.focus();
});
textInputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) sendBtn.click();
});

appSendBtn.addEventListener('click', () => {
  appendToAppLog(appSpeakerEl.value, appTextInputEl.value);
  appTextInputEl.value = '';
  appTextInputEl.focus();
});
appTextInputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) appSendBtn.click();
});

// ── 志望者向けリアルタイム質問生成 ──────────────────────────────────────
// 会話ログのスナップショットを通常のHTTP APIへ送るだけなので、録音WebSocketは止めない。

let appQuestionRequestId = 0;

function resetAppQuestion() {
  appQuestionRequestId += 1;
  appQuestionBtn.disabled = false;
  appQuestionBtn.textContent = '質問を考える';
  appQuestionLoading.classList.add('hidden');
  appQuestionResult.classList.add('hidden');
  appQuestionError.classList.add('hidden');
  appQuestionText.textContent = '';
  appQuestionTokens.textContent = '';
}

appQuestionBtn.addEventListener('click', async () => {
  const conversationSnapshot = appConversationLog.value.trim();
  if (!conversationSnapshot) {
    appQuestionError.textContent = '会話ログがまだありません。録音または手動入力で会話を追加してください。';
    appQuestionError.classList.remove('hidden');
    appQuestionResult.classList.add('hidden');
    return;
  }

  const requestId = ++appQuestionRequestId;
  appQuestionBtn.disabled = true;
  appQuestionBtn.textContent = '質問を生成中...';
  appQuestionLoading.classList.remove('hidden');
  appQuestionResult.classList.add('hidden');
  appQuestionError.classList.add('hidden');

  try {
    const res = await fetch('/api/applicant/question', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_info: appCompanyInfo.value,
        conversation_log: conversationSnapshot,
      }),
    });
    const data = await res.json();
    if (requestId !== appQuestionRequestId) return;

    appQuestionLoading.classList.add('hidden');
    if (!res.ok) {
      appQuestionError.textContent = data.detail || '質問を生成できませんでした。';
      appQuestionError.classList.remove('hidden');
      return;
    }

    appQuestionText.textContent = data.reply || '質問を生成できませんでした。';
    appQuestionTokens.textContent = data.tokens_used != null ? `消費トークン: ${data.tokens_used}` : '';
    appQuestionResult.classList.remove('hidden');
  } catch (_) {
    if (requestId !== appQuestionRequestId) return;
    appQuestionLoading.classList.add('hidden');
    appQuestionError.textContent = 'サーバーに接続できませんでした。';
    appQuestionError.classList.remove('hidden');
  } finally {
    if (requestId === appQuestionRequestId) {
      appQuestionBtn.disabled = false;
      appQuestionBtn.textContent = 'もう一度考える';
    }
  }
});

// ── 判定の解釈 ────────────────────────────────────────────────────────────
// プロンプトで「最初に○○か××を一言で」と頼んでいるため、冒頭部分だけを見る。
// 「不採用」は「採用」を含むので、否定側を先に判定する。

function parseInterviewerVerdict(reply) {
  const head = reply.trim().slice(0, 30);
  if (head.includes('不採用')) return { label: '不採用', cls: 'reject' };
  if (head.includes('採用'))   return { label: '採用',   cls: 'hire' };
  return { label: '判定なし', cls: 'unknown' };
}

function parseApplicantVerdict(reply) {
  const head = reply.trim().slice(0, 30);
  if (head.includes('合わない')) return { label: '合わない', cls: 'no-match' };
  if (head.includes('合う') || head.includes('合ってる') || head.includes('合っている')) {
    return { label: '合う', cls: 'match' };
  }
  return { label: '判定なし', cls: 'unknown' };
}

// ── 採点・評価の実行 ──────────────────────────────────────────────────────
// 誤操作でトークンを消費しないよう、ボタンは2度押しで実行する。
// エラー後や結果表示後もボタンは使えるままにして、ログ修正→再採点できるようにする。

function setupScoring(opts) {
  const {
    button, section, loading, content, badge, replyEl, errorEl, tokensEl,
    defaultLabel, rescoreLabel, busyLabel, endpoint, buildPayload, parseVerdict,
    isLogEmpty, onResult,
  } = opts;

  let scoring = false;
  let scoredOnce = false;
  let armed = false;
  let armTimer = null;
  let requestGeneration = 0;
  let activeController = null;

  function idleLabel() {
    return scoredOnce ? rescoreLabel : defaultLabel;
  }

  function disarm() {
    clearTimeout(armTimer);
    armed = false;
    button.classList.remove('btn-armed');
    button.textContent = idleLabel();
  }

  button.addEventListener('click', async () => {
    if (scoring) return;

    if (!armed) {
      armed = true;
      button.classList.add('btn-armed');
      button.textContent = isLogEmpty()
        ? 'ログが空です — もう一度押すと実行'
        : 'もう一度押すと実行（トークン消費）';
      armTimer = setTimeout(disarm, 5000);
      return;
    }
    disarm();

    const requestId = ++requestGeneration;
    scoring = true;
    button.disabled    = true;
    button.textContent = busyLabel;

    section.classList.remove('hidden');
    loading.classList.remove('hidden');
    content.classList.add('hidden');
    errorEl.classList.add('hidden');
    section.scrollIntoView({ behavior: 'smooth' });

    // 最後の一言までログに入れてから採点する（buildPayload はこの後で呼ぶ）
    await stopVoice();
    if (requestId !== requestGeneration) return;

    try {
      const controller = new AbortController();
      activeController = controller;
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
        signal: controller.signal,
      });
      const data = await res.json();
      if (requestId !== requestGeneration) return;

      loading.classList.add('hidden');

      if (!res.ok) {
        errorEl.textContent = data.detail || 'エラーが発生しました。';
        errorEl.classList.remove('hidden');
        return;
      }

      const reply   = data.reply || '';
      const verdict = parseVerdict(reply);

      badge.textContent = verdict.label;
      badge.className   = 'verdict-badge ' + verdict.cls;
      replyEl.textContent = reply;
      tokensEl.textContent = data.tokens_used != null ? `消費トークン: ${data.tokens_used}` : '';
      content.classList.remove('hidden');
      scoredOnce = true;
      if (onResult) onResult(verdict, reply);

    } catch (_) {
      if (requestId !== requestGeneration) return;
      loading.classList.add('hidden');
      errorEl.textContent = 'サーバーに接続できませんでした。';
      errorEl.classList.remove('hidden');
    } finally {
      if (requestId !== requestGeneration) return;
      scoring = false;
      activeController = null;
      button.disabled = false;
      button.textContent = idleLabel();
    }
  });

  return {
    reset() {
      requestGeneration += 1;
      if (activeController) activeController.abort();
      activeController = null;
      scoring = false;
      scoredOnce = false;
      disarm();
      button.disabled = false;
      button.textContent = defaultLabel;
      section.classList.add('hidden');
      content.classList.add('hidden');
      errorEl.classList.add('hidden');
      loading.classList.remove('hidden');
      badge.textContent = '';
      replyEl.textContent = '';
      tokensEl.textContent = '';
    },
  };
}

const lastResult = {
  interviewer: null,
  applicant:   null,
};

const interviewerScoring = setupScoring({
  button:   $('end-btn'),
  section:  $('final-section'),
  loading:  $('final-loading'),
  content:  $('final-content'),
  badge:    $('verdict-badge'),
  replyEl:  $('final-reply'),
  errorEl:  $('final-error'),
  tokensEl: $('final-tokens'),
  defaultLabel: '面接終了',
  rescoreLabel: '再採点する',
  busyLabel:    '採点中...',
  endpoint:     '/api/final',
  buildPayload: () => ({
    company_info:  companyInfoEl.value,
    resume:        resumeEl.value,
    interview_log: logEl.value,
  }),
  parseVerdict: parseInterviewerVerdict,
  isLogEmpty:   () => !logEl.value.trim(),
  onResult: (verdict, reply) => { lastResult.interviewer = { verdict: verdict.label, reply }; },
});

const applicantScoring = setupScoring({
  button:   $('app-end-btn'),
  section:  $('app-final-section'),
  loading:  $('app-final-loading'),
  content:  $('app-final-content'),
  badge:    $('app-verdict-badge'),
  replyEl:  $('app-final-reply'),
  errorEl:  $('app-final-error'),
  tokensEl: $('app-final-tokens'),
  defaultLabel: 'Noesia なら合うか評価する',
  rescoreLabel: '再評価する',
  busyLabel:    '評価中...',
  endpoint:     '/api/applicant/final',
  buildPayload: () => ({
    company_info:     appCompanyInfo.value,
    conversation_log: appConversationLog.value,
  }),
  parseVerdict: parseApplicantVerdict,
  isLogEmpty:   () => !appConversationLog.value.trim(),
  onResult: (verdict, reply) => { lastResult.applicant = { verdict: verdict.label, reply }; },
});

// ── 結果のコピー・保存 ────────────────────────────────────────────────────

function buildResultText(mode) {
  const now = new Date().toLocaleString('ja-JP');
  if (mode === 'interviewer') {
    const r = lastResult.interviewer || { verdict: '（未採点）', reply: '' };
    return [
      '=== Noesia 採用補助システム 面接記録 ===',
      `日時: ${now}`,
      '',
      '【会社情報・求める人材】', companyInfoEl.value || '（未入力）',
      '',
      '【履歴書】', resumeEl.value || '（未入力）',
      '',
      '【面接ログ】', logEl.value || '（未入力）',
      '',
      `【Noesia の判定】${r.verdict}`,
      '',
      '【Noesia のコメント】', r.reply,
      '',
      '※ これは Noesia の人格による意見です。最終判断は利用者自身が行ってください。',
    ].join('\n');
  }
  const r = lastResult.applicant || { verdict: '（未評価）', reply: '' };
  return [
    '=== Noesia 採用補助システム 企業評価記録 ===',
    `日時: ${now}`,
    '',
    '【企業情報】', appCompanyInfo.value || '（未入力）',
    '',
    '【会話ログ】', appConversationLog.value || '（未入力）',
    '',
    `【Noesia 人格本人の判定】${r.verdict}`,
    '',
    '【Noesia 人格本人のコメント】', r.reply,
    '',
    '※ Noesia 人格本人が志望者として、自分ならこの企業に合うかを判断した意見です。',
  ].join('\n');
}

async function copyText(text, btn) {
  const original = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'コピーしました';
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    btn.textContent = 'コピーしました';
  }
  setTimeout(() => { btn.textContent = original; }, 1500);
}

function downloadText(text, filename) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function timestamp() {
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
}

$('copy-result').addEventListener('click', e => copyText(buildResultText('interviewer'), e.target));
$('save-result').addEventListener('click', () => downloadText(buildResultText('interviewer'), `noesia_面接記録_${timestamp()}.txt`));
$('app-copy-result').addEventListener('click', e => copyText(buildResultText('applicant'), e.target));
$('app-save-result').addEventListener('click', () => downloadText(buildResultText('applicant'), `noesia_企業評価_${timestamp()}.txt`));

// ── 面接内容・Noesiaコメントの個別クリア（2度押しで実行）────────────────

function setupConfirmedClear(btn, action) {
  let armed = false;
  let timer = null;
  const original = btn.textContent;
  btn.addEventListener('click', async () => {
    if (!armed) {
      armed = true;
      btn.textContent = 'もう一度押すとクリア';
      btn.classList.add('btn-armed');
      timer = setTimeout(() => {
        armed = false;
        btn.textContent = original;
        btn.classList.remove('btn-armed');
      }, 4000);
      return;
    }
    clearTimeout(timer);
    armed = false;
    btn.textContent = original;
    btn.classList.remove('btn-armed');
    btn.disabled = true;
    try {
      await action();
      btn.textContent = 'クリアしました';
      setTimeout(() => { btn.textContent = original; }, 1200);
    } finally {
      btn.disabled = false;
    }
  });
}

setupConfirmedClear($('clear-btn'), async () => {
  await stopVoice();
  logEl.value = '';
  textInputEl.value = '';
  saveDraft();
});

setupConfirmedClear($('app-clear-btn'), async () => {
  await stopVoice();
  appConversationLog.value = '';
  appTextInputEl.value = '';
  resetAppQuestion();
  saveDraft();
});

setupConfirmedClear($('clear-result'), () => {
  lastResult.interviewer = null;
  interviewerScoring.reset();
});

setupConfirmedClear($('app-clear-result'), () => {
  lastResult.applicant = null;
  applicantScoring.reset();
});

// ── 起動 ─────────────────────────────────────────────────────────────────
init();
