const $ = id => document.getElementById(id);

// ── Mode state ────────────────────────────────────────────────────────────
let currentMode    = 'interviewer';
let ws             = null;
let interviewEnded = false;
let applicantEnded = false;

// ── Element refs: shared ──────────────────────────────────────────────────
const apiBadge      = $('api-badge');
const settingsBtn   = $('settings-btn');
const modalOverlay  = $('modal-overlay');
const cfgName       = $('cfg-name');
const cfgKey        = $('cfg-key');
const cfgToggle     = $('cfg-toggle');
const cfgCancel     = $('cfg-cancel');
const cfgSave       = $('cfg-save');
const cfgMsg        = $('cfg-msg');
const tabInterviewer = $('tab-interviewer');
const tabApplicant   = $('tab-applicant');

// ── Element refs: interviewer mode ────────────────────────────────────────
const resumeEl       = $('resume');
const pasteResumeBtn = $('paste-resume');
const logEl          = $('interview-log');
const micSelect      = $('mic-select');
const deviceSelect   = $('device-select');
const voiceStart     = $('voice-start');
const voiceStop      = $('voice-stop');
const voiceStatus    = $('voice-status');
const speakerEl      = $('speaker');
const textInputEl    = $('text-input');
const sendBtn        = $('send-btn');
const endBtn         = $('end-btn');
const finalSection   = $('final-section');
const finalLoading   = $('final-loading');
const finalContent   = $('final-content');
const verdictBadge   = $('verdict-badge');
const finalReply     = $('final-reply');
const finalError     = $('final-error');

// ── Element refs: applicant mode ──────────────────────────────────────────
const appCompanyInfo     = $('app-company-info');
const appConversationLog = $('app-conversation-log');
const appMicSelect       = $('app-mic-select');
const appDeviceSelect    = $('app-device-select');
const appVoiceStart      = $('app-voice-start');
const appVoiceStop       = $('app-voice-stop');
const appVoiceStatus     = $('app-voice-status');
const appSpeakerEl       = $('app-speaker');
const appTextInputEl     = $('app-text-input');
const appSendBtn         = $('app-send-btn');
const appEndBtn          = $('app-end-btn');
const appFinalSection    = $('app-final-section');
const appFinalLoading    = $('app-final-loading');
const appFinalContent    = $('app-final-content');
const appVerdictBadge    = $('app-verdict-badge');
const appFinalReply      = $('app-final-reply');
const appFinalError      = $('app-final-error');

// ── Mode switching ────────────────────────────────────────────────────────

tabInterviewer.addEventListener('click', () => switchMode('interviewer'));
tabApplicant.addEventListener('click',   () => switchMode('applicant'));

function switchMode(mode) {
  if (ws) { try { ws.send('stop'); } catch (_) {} }
  stopRecording();
  currentMode = mode;
  $('interviewer-mode').classList.toggle('hidden', mode !== 'interviewer');
  $('applicant-mode').classList.toggle('hidden',   mode !== 'applicant');
  tabInterviewer.classList.toggle('mode-tab--active', mode === 'interviewer');
  tabApplicant.classList.toggle('mode-tab--active',   mode === 'applicant');
}

// ── 履歴書貼り付け ────────────────────────────────────────────────────────

pasteResumeBtn.addEventListener('click', async () => {
  resumeEl.focus();
  try {
    const text = await navigator.clipboard.readText();
    if (text) { resumeEl.value = text; return; }
  } catch (_) {}
  document.execCommand('paste');
});

// ── 起動時初期化 ──────────────────────────────────────────────────────────

async function init() {
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
        api_name:     cfgName.value,
        api_key:      cfgKey.value,
        mic_device:   micSelect.value    ? parseInt(micSelect.value)    : null,
        audio_device: deviceSelect.value ? parseInt(deviceSelect.value) : null,
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

// ── デバイス一覧 ──────────────────────────────────────────────────────────

function populateSelect(selectEl, items, savedValue, emptyMsg) {
  selectEl.innerHTML = '';
  if (!items.length) {
    selectEl.innerHTML = `<option value="">${emptyMsg || 'なし'}</option>`;
    return false;
  }
  items.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.index; opt.textContent = d.name;
    selectEl.appendChild(opt);
  });
  if (savedValue != null) selectEl.value = savedValue;
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

    const hasDevices = populateSelect(deviceSelect,    devices, config.audio_device, 'ループバックデバイスが見つかりません');
    populateSelect(appDeviceSelect, devices, config.audio_device, 'ループバックデバイスが見つかりません');

    if (!hasDevices) {
      voiceStart.disabled    = true;
      appVoiceStart.disabled = true;
    }
  } catch (_) {
    for (const el of [deviceSelect, appDeviceSelect]) {
      el.innerHTML = '<option value="">デバイス取得エラー</option>';
    }
    voiceStart.disabled    = true;
    appVoiceStart.disabled = true;
  }
}

// ── 音声認識（WebSocket）─────────────────────────────────────────────────

function startVoice(devSel, micSel) {
  const deviceIndex = devSel.value;
  if (!deviceIndex) return;

  const isInterviewer    = currentMode === 'interviewer';
  const micSpeaker       = isInterviewer ? '面接官' : 'あなた';
  const loopbackSpeaker  = isInterviewer ? '志望者' : '企業担当者';
  const micParam         = micSel.value ? `&mic=${micSel.value}` : '';

  ws = new WebSocket(
    `ws://127.0.0.1:8000/ws/transcribe?device=${deviceIndex}${micParam}` +
    `&mic_speaker=${encodeURIComponent(micSpeaker)}&loopback_speaker=${encodeURIComponent(loopbackSpeaker)}`
  );

  const statusEl = isInterviewer ? voiceStatus : appVoiceStatus;
  const startBtn = isInterviewer ? voiceStart  : appVoiceStart;
  const stopBtn  = isInterviewer ? voiceStop   : appVoiceStop;

  ws.onopen = () => {
    statusEl.textContent = '● 録音中 — 声を自動識別しています';
    statusEl.className   = 'voice-status active';
    startBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');
  };

  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.error) {
      statusEl.textContent = `エラー: ${data.error}`;
      statusEl.className   = 'voice-status error';
      stopRecording();
    } else if (data.speaker && data.text) {
      if (currentMode === 'interviewer') {
        appendToLog(data.speaker, data.text);
      } else {
        appendToAppLog(data.speaker, data.text);
      }
    }
  };

  ws.onclose = () => stopRecording();
  ws.onerror = () => {
    statusEl.textContent = '接続エラーが発生しました。';
    statusEl.className   = 'voice-status error';
    stopRecording();
  };
}

voiceStart.addEventListener('click',    () => startVoice(deviceSelect,    micSelect));
voiceStop.addEventListener('click',     () => { if (ws) ws.send('stop'); stopRecording(); });
appVoiceStart.addEventListener('click', () => startVoice(appDeviceSelect, appMicSelect));
appVoiceStop.addEventListener('click',  () => { if (ws) ws.send('stop'); stopRecording(); });

function stopRecording() {
  if (ws) { try { ws.close(); } catch (_) {} ws = null; }
  for (const [status, start, stop] of [
    [voiceStatus,    voiceStart,    voiceStop],
    [appVoiceStatus, appVoiceStart, appVoiceStop],
  ]) {
    status.textContent = '';
    status.className   = 'voice-status';
    start.classList.remove('hidden');
    stop.classList.add('hidden');
  }
}

// ── ログ追記 ──────────────────────────────────────────────────────────────

function appendToLog(speaker, text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  const line = `${speaker}：${trimmed}`;
  logEl.value = logEl.value ? logEl.value + '\n' + line : line;
  logEl.scrollTop = logEl.scrollHeight;
}

function appendToAppLog(speaker, text) {
  const trimmed = text.trim();
  if (!trimmed) return;
  const line = `${speaker}：${trimmed}`;
  appConversationLog.value = appConversationLog.value ? appConversationLog.value + '\n' + line : line;
  appConversationLog.scrollTop = appConversationLog.scrollHeight;
}

// ── テキスト入力（面接官モード）──────────────────────────────────────────

sendBtn.addEventListener('click', () => {
  appendToLog(speakerEl.value, textInputEl.value);
  textInputEl.value = '';
  textInputEl.focus();
});
textInputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) sendBtn.click();
});

// ── テキスト入力（志望者モード）──────────────────────────────────────────

appSendBtn.addEventListener('click', () => {
  appendToAppLog(appSpeakerEl.value, appTextInputEl.value);
  appTextInputEl.value = '';
  appTextInputEl.focus();
});
appTextInputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) appSendBtn.click();
});

// ── 面接終了 → 採点（面接官モード）──────────────────────────────────────

endBtn.addEventListener('click', async () => {
  if (interviewEnded) return;
  interviewEnded = true;
  stopRecording();

  endBtn.disabled    = true;
  endBtn.textContent = '採点中...';

  finalSection.classList.remove('hidden');
  finalSection.scrollIntoView({ behavior: 'smooth' });

  try {
    const res  = await fetch('/api/final', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_info:  $('company-info').value,
        resume:        resumeEl.value,
        interview_log: logEl.value,
      }),
    });
    const data = await res.json();

    finalLoading.classList.add('hidden');

    if (!res.ok) {
      finalError.textContent = data.detail || 'エラーが発生しました。';
      finalError.classList.remove('hidden');
      return;
    }

    const reply  = data.reply || '';
    const isHire = /採用/.test(reply) && !/不採用/.test(reply.split('採用')[0]);

    verdictBadge.textContent = isHire ? '採用' : '不採用';
    verdictBadge.className   = 'verdict-badge ' + (isHire ? 'hire' : 'reject');
    finalReply.textContent   = reply;
    finalContent.classList.remove('hidden');

  } catch (_) {
    finalLoading.classList.add('hidden');
    finalError.textContent = 'サーバーに接続できませんでした。';
    finalError.classList.remove('hidden');
  } finally {
    endBtn.textContent = '面接終了';
  }
});

// ── 終了 → 企業評価（志望者モード）──────────────────────────────────────

appEndBtn.addEventListener('click', async () => {
  if (applicantEnded) return;
  applicantEnded = true;
  stopRecording();

  appEndBtn.disabled    = true;
  appEndBtn.textContent = '評価中...';

  appFinalSection.classList.remove('hidden');
  appFinalSection.scrollIntoView({ behavior: 'smooth' });

  try {
    const res  = await fetch('/api/applicant/final', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_info:     appCompanyInfo.value,
        conversation_log: appConversationLog.value,
      }),
    });
    const data = await res.json();

    appFinalLoading.classList.add('hidden');

    if (!res.ok) {
      appFinalError.textContent = data.detail || 'エラーが発生しました。';
      appFinalError.classList.remove('hidden');
      return;
    }

    const reply   = data.reply || '';
    const isMatch = !/合わない/.test(reply);

    appVerdictBadge.textContent = isMatch ? '合う' : '合わない';
    appVerdictBadge.className   = 'verdict-badge ' + (isMatch ? 'match' : 'no-match');
    appFinalReply.textContent   = reply;
    appFinalContent.classList.remove('hidden');

  } catch (_) {
    appFinalLoading.classList.add('hidden');
    appFinalError.textContent = 'サーバーに接続できませんでした。';
    appFinalError.classList.remove('hidden');
  } finally {
    appEndBtn.textContent = '終了して企業を評価する';
  }
});

// ── 起動 ─────────────────────────────────────────────────────────────────
init();
