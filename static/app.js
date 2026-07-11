const $ = id => document.getElementById(id);

// ── Mode state ────────────────────────────────────────────────────────────
let currentMode = 'interviewer';
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
const tabInterviewer = $('tab-interviewer');
const tabApplicant   = $('tab-applicant');

// ── Element refs: interviewer mode ────────────────────────────────────────
const companyInfoEl  = $('company-info');
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

// ── Mode switching ────────────────────────────────────────────────────────

tabInterviewer.addEventListener('click', () => switchMode('interviewer'));
tabApplicant.addEventListener('click',   () => switchMode('applicant'));

function switchMode(mode) {
  const wasRecording = !!ws;
  if (ws) { try { ws.send('stop'); } catch (_) {} }
  stopRecording();
  currentMode = mode;
  $('interviewer-mode').classList.toggle('hidden', mode !== 'interviewer');
  $('applicant-mode').classList.toggle('hidden',   mode !== 'applicant');
  tabInterviewer.classList.toggle('mode-tab--active', mode === 'interviewer');
  tabApplicant.classList.toggle('mode-tab--active',   mode === 'applicant');
  if (wasRecording) {
    const statusEl = mode === 'interviewer' ? voiceStatus : appVoiceStatus;
    statusEl.textContent = 'タブ切り替えのため録音を停止しました。';
    statusEl.className   = 'voice-status';
  }
}

// ── 履歴書貼り付け ────────────────────────────────────────────────────────

pasteResumeBtn.addEventListener('click', async () => {
  resumeEl.focus();
  try {
    const text = await navigator.clipboard.readText();
    if (text) { resumeEl.value = text; saveDraft(); return; }
  } catch (_) {}
  document.execCommand('paste');
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

function startVoice(devSel, micSel) {
  const deviceIndex = devSel.value;
  if (!deviceIndex) return;

  const isInterviewer    = currentMode === 'interviewer';
  const micSpeaker       = isInterviewer ? '面接官' : 'あなた';
  const loopbackSpeaker  = isInterviewer ? '志望者' : '企業担当者';
  const micParam         = micSel.value ? `&mic=${micSel.value}` : '';

  ws = new WebSocket(
    `ws://${location.host}/ws/transcribe?device=${deviceIndex}${micParam}` +
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
      stopRecording();
      statusEl.textContent = `エラー: ${data.error}`;
      statusEl.className   = 'voice-status error';
    } else if (data.speaker && data.text) {
      if (currentMode === 'interviewer') {
        appendToLog(data.speaker, data.text);
      } else {
        appendToAppLog(data.speaker, data.text);
      }
      statusEl.textContent = `● 録音中 — 最後の認識: ${data.speaker}「${data.text.slice(0, 30)}」`;
      statusEl.className   = 'voice-status active';
    }
  };

  ws.onclose = () => stopRecording();
  ws.onerror = () => {
    stopRecording();
    statusEl.textContent = '接続エラーが発生しました。';
    statusEl.className   = 'voice-status error';
  };
}

voiceStart.addEventListener('click',    () => startVoice(deviceSelect,    micSelect));
voiceStop.addEventListener('click',     () => { if (ws) { try { ws.send('stop'); } catch (_) {} } stopRecording(); });
appVoiceStart.addEventListener('click', () => startVoice(appDeviceSelect, appMicSelect));
appVoiceStop.addEventListener('click',  () => { if (ws) { try { ws.send('stop'); } catch (_) {} } stopRecording(); });

function stopRecording() {
  if (ws) {
    // close() 後に onclose が再入してステータス表示を消してしまうため、先にハンドラを外す
    ws.onmessage = null;
    ws.onclose   = null;
    ws.onerror   = null;
    try { ws.close(); } catch (_) {}
    ws = null;
  }
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

    scoring = true;
    stopRecording();
    button.disabled    = true;
    button.textContent = busyLabel;

    section.classList.remove('hidden');
    loading.classList.remove('hidden');
    content.classList.add('hidden');
    errorEl.classList.add('hidden');
    section.scrollIntoView({ behavior: 'smooth' });

    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      const data = await res.json();

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
      loading.classList.add('hidden');
      errorEl.textContent = 'サーバーに接続できませんでした。';
      errorEl.classList.remove('hidden');
    } finally {
      scoring = false;
      button.disabled = false;
      button.textContent = idleLabel();
    }
  });

  return {
    reset() {
      scoredOnce = false;
      disarm();
      section.classList.add('hidden');
      content.classList.add('hidden');
      errorEl.classList.add('hidden');
      loading.classList.remove('hidden');
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
  defaultLabel: '終了して企業を評価する',
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
    `【Noesia の判定】${r.verdict}`,
    '',
    '【Noesia のコメント】', r.reply,
    '',
    '※ これは Noesia の人格による意見です。最終判断は利用者自身が行ってください。',
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

// ── 入力のクリア（2度押しで実行）─────────────────────────────────────────

function setupClear(btn, fields, scoringCtrl, resultKey) {
  let armed = false;
  let timer = null;
  const original = btn.textContent;
  btn.addEventListener('click', () => {
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
    for (const el of fields) el.value = '';
    lastResult[resultKey] = null;
    scoringCtrl.reset();
    saveDraft();
  });
}

setupClear($('clear-btn'),     [companyInfoEl, resumeEl, logEl],        interviewerScoring, 'interviewer');
setupClear($('app-clear-btn'), [appCompanyInfo, appConversationLog],    applicantScoring,   'applicant');

// ── 起動 ─────────────────────────────────────────────────────────────────
init();
