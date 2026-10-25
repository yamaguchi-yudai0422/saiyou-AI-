import array
import asyncio
import collections
import glob
import os
import queue
import site
import sys
import threading
import time

import numpy as np
import speech_recognition as sr

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

RATE  = 16000
CHUNK = 1024

# PortAudio の初期化・終了はスレッドセーフでないため、
# 複数スレッドから同時に呼ばれるとクラッシュする。ロックで直列化する。
_pa_lock = threading.Lock()

# Whisper モデルはプロセスで1つだけ読み込み、全セッションで使い回す
_whisper_model = None
_whisper_lock  = threading.Lock()
_cuda_dll_handles = []

# 発話区切りのパラメータ（秒）
PREROLL_SEC     = 0.5   # 発話開始前に遡って含める長さ
END_SILENCE_SEC = 0.8   # この長さ無音が続いたら発話終了
MAX_SEGMENT_SEC = 12.0  # 1発話の最大長（長い発話は分割して随時認識）
MIN_SPEECH_SEC  = 0.3   # これより短い音はノイズとして捨てる
LOOPBACK_END_SILENCE_SEC = 1.6  # 動画や通話音声は短い間で文を切らない
LOOPBACK_MIN_SPEECH_SEC  = 0.15  # 短く弱い質問も相手側では認識へ回す
LOOPBACK_MAX_SEGMENT_SEC = 12.0
LOOPBACK_OVERLAP_SEC = 0.5

# 無音ノイズから Whisper が生成しがちな定型幻覚
_HALLUCINATIONS = (
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "ご清聴ありがとうございました",
    "チャンネル登録",
    "次回予告",
    "日本語の面接",
    "お待ちしております",
    "それではまた会いましょう",
    "さあ行ってみましょう",
    "おやすみなさい",
)


def _resample_audio(samples: np.ndarray, rate: int) -> np.ndarray:
    audio = samples.astype(np.float32) / 32768.0
    if rate == RATE or len(audio) < 2:
        return audio
    n_out = max(1, int(len(audio) * RATE / rate))
    positions = np.arange(n_out, dtype=np.float64) * rate / RATE
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


def _normalize_audio(audio: np.ndarray, noise_floor: float, max_gain: float) -> np.ndarray | None:
    """ピーク1点ではなくRMSを基準に音量を揃え、小さい会話を持ち上げる。"""
    if audio.size == 0:
        return None
    audio = audio.astype(np.float32, copy=True)
    audio -= float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if peak < noise_floor:
        return None
    rms = float(np.sqrt(np.mean(audio * audio)))
    gain = min(max_gain, 0.10 / max(rms, 1e-6), 0.95 / max(peak, 1e-6))
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _enhance_loopback_audio(audio: np.ndarray) -> np.ndarray | None:
    """相手側の定常ノイズをスペクトル減算し、弱い音声を再認識用に整える。"""
    normalized = _normalize_audio(audio, noise_floor=0.00025, max_gain=80.0)
    if normalized is None or len(normalized) < 1024:
        return normalized

    frame_size = 512
    hop = 256
    frame_count = 1 + int(np.ceil(max(0, len(normalized) - frame_size) / hop))
    padded_size = (frame_count - 1) * hop + frame_size
    padded = np.pad(normalized, (0, padded_size - len(normalized)))
    window = np.hanning(frame_size).astype(np.float32)

    spectra = []
    for i in range(frame_count):
        start = i * hop
        spectra.append(np.fft.rfft(padded[start:start + frame_size] * window))
    spectra = np.asarray(spectra)
    magnitudes = np.abs(spectra)
    # 発話前後の無音を含むため、低い20%を定常ノイズの目安にできる。
    noise = np.percentile(magnitudes, 20, axis=0)
    spectral_gain = np.clip((magnitudes - 1.15 * noise) / (magnitudes + 1e-8), 0.18, 1.0)
    cleaned_spectra = spectra * spectral_gain

    output = np.zeros(padded_size, dtype=np.float32)
    weight = np.zeros(padded_size, dtype=np.float32)
    for i, spectrum in enumerate(cleaned_spectra):
        start = i * hop
        frame = np.fft.irfft(spectrum, n=frame_size).astype(np.float32)
        output[start:start + frame_size] += frame * window
        weight[start:start + frame_size] += window * window
    output /= np.maximum(weight, 1e-6)
    return _normalize_audio(output[:len(normalized)], noise_floor=0.0001, max_gain=30.0)


def _decode_whisper(model, audio: np.ndarray, is_loopback: bool, relaxed: bool = False) -> str:
    """発話分割済みの音声を認識する。面接用プロンプトは動画音声を誤変換させるため使わない。"""
    if relaxed:
        min_logprob, beam_size = -1.5, 8
    elif is_loopback:
        min_logprob, beam_size = -1.25, 6
    else:
        min_logprob, beam_size = -1.0, 5

    segments, _ = model.transcribe(
        audio,
        language="ja",
        beam_size=beam_size,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    text = "".join(segment.text for segment in segments if segment.avg_logprob >= min_logprob)
    # 定型句の幻覚だけを除き、同じ区間で聞き取れた言葉は残す。
    for phrase in _HALLUCINATIONS:
        text = text.replace(phrase, "")
    return text.strip(" 、。\t\n")


def _setup_cuda_dlls():
    """pip の nvidia-cublas/cudnn ホイールの DLL を ctranslate2 から見えるようにする"""
    dirs = []
    roots = list(site.getsitepackages())
    # PyInstaller の onedir ビルドでは依存ファイルが sys._MEIPASS（_internal）に入る
    if getattr(sys, "frozen", False):
        roots.insert(0, sys._MEIPASS)
    for pkg in ("cublas", "cudnn", "cuda_nvrtc"):
        for sp in roots:
            for d in glob.glob(os.path.join(sp, "nvidia", pkg, "bin")):
                try:
                    # 返されたハンドルを保持しないとディレクトリ登録が解除される
                    _cuda_dll_handles.append(os.add_dll_directory(d))
                except Exception:
                    pass
                dirs.append(d)
    if dirs:
        # ctranslate2 は add_dll_directory を見ないため PATH にも足す必要がある
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def _get_whisper_model():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model
        _setup_cuda_dlls()
        # GPU があれば精度の高い medium、無ければ CPU で small を使う
        try:
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False
        attempts = []
        if has_cuda:
            # モデルのダウンロードを無駄にしないよう、CUDA が見える時だけ試す。
            # large-v3 が最も高精度（VRAM 約3GB）。載らないGPUでは medium に落とす。
            attempts += [
                ("large-v3", "cuda", "float16", {}),
                ("medium",   "cuda", "float16", {}),
                ("small",    "cuda", "float16", {}),
            ]
        attempts.append(("small", "cpu", "int8", {"cpu_threads": 8}))
        last_error = None
        for name, device, ctype, kwargs in attempts:
            try:
                model = WhisperModel(name, device=device, compute_type=ctype, **kwargs)
                # CUDA はロードは通っても推論時に DLL 不足で落ちることがあるため、
                # 短い無音で実際に動くことを確認してから採用する
                list(model.transcribe(np.zeros(1600, dtype=np.float32), language="ja")[0])
                _whisper_model = model
                return _whisper_model
            except Exception as e:
                last_error = e
        raise last_error


class _StreamStopped(Exception):
    """録音停止によりストリーム読み出しを中断したことを示す"""


class _MonoStream:
    """多チャンネルの生ストリームを読み出し時にモノラル化するラッパー"""

    def __init__(self, stream, channels, source, stop_event=None):
        self._stream   = stream
        self._channels = channels
        self._source   = source
        self._stop     = stop_event

    def available(self) -> bool:
        return self._stream.get_read_available() >= CHUNK

    def read(self, size, exception_on_overflow=False):
        # 無音のループバックデバイスは read() がデータ到着まで返らず
        # 停止操作も効かなくなるため、停止フラグを見ながら到着を待つ
        while self._stream.get_read_available() < size:
            if self._stop is not None and self._stop.is_set():
                raise _StreamStopped()
            time.sleep(0.03)
        data = self._stream.read(size, exception_on_overflow=False)
        arr  = np.frombuffer(data, dtype=np.int16)
        if self._channels > 1:
            arr = (arr.reshape(-1, self._channels).astype(np.int32).sum(axis=1) // self._channels).astype(np.int16)
        gain = self._source.current_gain()
        if arr.size:
            # 入力ブースト（音の小さいデバイス対策）は「発話検出のしきい値」と
            # 「レベル表示」にだけ効かせる。波形そのものを増幅すると int16 で
            # 頭打ちになって歪み、かえって認識精度が落ちる
            raw_peak = int(np.max(np.abs(arr.astype(np.int32))))
            self._source.last_data_time = time.monotonic()
            peak = min(32767, int(raw_peak * gain))
            if peak > self._source.recent_peak:
                self._source.recent_peak = peak
        # Google 認識は Recognizer 側が生の音量でしきい値を決めるため、
        # この経路だけは波形にもブーストをかける
        if self._source.amplify and gain != 1.0:
            arr = np.clip(arr.astype(np.int32) * gain, -32768, 32767).astype(np.int16)
        return arr.tobytes()


class _AudioSource(sr.AudioSource):
    """pyaudiowpatch ベースの汎用 AudioSource"""

    def __init__(self, device_index=None, stop_event=None, is_loopback=False, gain_state=None, amplify=False):
        self.device_index = device_index
        self.stop_event   = stop_event
        self.is_loopback  = is_loopback
        # 録音中でもスライダーで変更できるよう、共有 dict からゲインを毎回読む
        self.gain_state   = gain_state
        self.gain_key     = "loopback" if is_loopback else "mic"
        # 波形そのものを増幅するか（Google 認識経路のみ True）
        self.amplify      = amplify
        self.SAMPLE_RATE  = RATE
        self.SAMPLE_WIDTH = 2
        self.CHUNK        = CHUNK
        self.pa      = None
        self.stream  = None
        self._raw    = None
        # 無音・無信号の検知用（別スレッドの監視ループから参照される）
        self.last_data_time = None
        self.recent_peak    = 0

    def current_gain(self) -> float:
        if not self.gain_state:
            return 1.0
        try:
            return max(1.0, min(30.0, float(self.gain_state.get(self.gain_key, 1.0))))
        except (TypeError, ValueError):
            return 1.0

    def _mme_equivalents(self) -> list:
        """WASAPI デバイスと同名の MME 入力デバイスを探す。
        マイクの WASAPI 共有モード取り込みは数ms毎に波形が壊れることがある
        （Shure MV7 で確認。認識が全滅する）ため、OS がフォーマット変換して
        くれる MME を優先する。MME のデバイス名は31文字に切られる点に注意。"""
        result = []
        try:
            wname   = self.pa.get_device_info_by_index(self.device_index).get("name", "")
            mme_idx = self.pa.get_host_api_info_by_type(pyaudio.paMME)["index"]
            for i in range(self.pa.get_device_count()):
                d = self.pa.get_device_info_by_index(i)
                if d.get("hostApi") != mme_idx or d.get("maxInputChannels", 0) <= 0:
                    continue
                name = d.get("name", "")
                if name and (wname.startswith(name) or name.startswith(wname[:31])):
                    result.append(i)
        except Exception:
            pass
        return result

    def __enter__(self):
        with _pa_lock:
            self.pa = pyaudio.PyAudio()
            # (device_index, rate, channels) の候補を順に試す
            attempts = []
            if self.device_index is None:
                attempts.append((None, RATE, 1))
            else:
                if not self.is_loopback:
                    # マイクは MME の同名デバイスを 16kHz で開くのを最優先にする
                    for idx in self._mme_equivalents():
                        attempts.append((idx, RATE, 1))
                # WASAPI 共有モードはデバイスのネイティブサンプルレートでしか開けないため、
                # 指定デバイスはデバイス情報のレートを使う（16kHz 固定だと -9997 で失敗する）
                info = self.pa.get_device_info_by_index(self.device_index)
                rate = int(info.get("defaultSampleRate") or RATE)
                native_channels = max(1, int(info.get("maxInputChannels") or 1))
                if self.is_loopback and native_channels > 1:
                    # PCの再生音声はステレオで取得してから合成する。
                    # モノラルで開けても波形が崩れるデバイスがある。
                    attempts.append((self.device_index, rate, native_channels))
                    attempts.append((self.device_index, rate, 1))
                else:
                    attempts.append((self.device_index, rate, 1))
                    if native_channels > 1:
                        attempts.append((self.device_index, rate, native_channels))

            last_error = None
            for dev, rate, channels in attempts:
                kwargs = dict(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    frames_per_buffer=self.CHUNK,
                )
                if dev is not None:
                    kwargs["input_device_index"] = dev
                try:
                    self._raw        = self.pa.open(**kwargs)
                    self.SAMPLE_RATE = rate
                    self.stream      = _MonoStream(self._raw, channels, self, self.stop_event)
                    break
                except Exception as e:
                    last_error = e
            if self.stream is None:
                self.pa.terminate()
                self.pa = None
                raise last_error
        return self

    def __exit__(self, *_):
        with _pa_lock:
            if self._raw:
                self._raw.stop_stream()
                self._raw.close()
            if self.pa:
                self.pa.terminate()


def list_microphone_devices() -> list:
    if not HAS_PYAUDIO:
        return []
    result = []
    try:
        with _pa_lock, pyaudio.PyAudio() as pa:
            wasapi_idx = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if (
                    d.get("hostApi") == wasapi_idx
                    and d.get("maxInputChannels", 0) > 0
                    and not d.get("isLoopbackDevice", False)
                ):
                    result.append({"index": i, "name": d["name"]})
    except Exception:
        pass
    return result


def list_loopback_devices() -> list:
    if not HAS_PYAUDIO:
        return []
    result = []
    try:
        with _pa_lock, pyaudio.PyAudio() as pa:
            wasapi_idx = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d.get("hostApi") == wasapi_idx and d.get("isLoopbackDevice"):
                    result.append({"index": i, "name": d["name"]})
    except Exception:
        pass
    return result


# ── Whisper（ローカル認識）経路 ──────────────────────────────────────────────


def _capture_loop(source, speaker: str, segment_queue, stop_event: threading.Event, on_error, loop, on_open_error=None):
    """音声をチャンク単位で読み、音量ベースで発話単位に区切ってキューへ渡す"""

    def report_error(message: str):
        if on_error is not None:
            asyncio.run_coroutine_threadsafe(on_error(message), loop)

    try:
        with source:
            rate      = source.SAMPLE_RATE
            chunk_sec = CHUNK / rate
            preroll   = collections.deque(maxlen=max(1, int(PREROLL_SEC / chunk_sec)))
            buf         = []
            speech_sec  = 0.0
            silence_sec = 0.0
            # 直近ピークを基準にすると、会話が続いた場合に声自身を無音扱いする。
            threshold      = 20 if source.is_loopback else 300
            end_silence    = LOOPBACK_END_SILENCE_SEC if source.is_loopback else END_SILENCE_SEC
            min_speech     = LOOPBACK_MIN_SPEECH_SEC if source.is_loopback else MIN_SPEECH_SEC
            max_segment    = LOOPBACK_MAX_SEGMENT_SEC if source.is_loopback else MAX_SEGMENT_SEC

            def flush():
                nonlocal buf, speech_sec, silence_sec
                if buf and speech_sec >= min_speech:
                    samples = np.frombuffer(b"".join(buf), dtype=np.int16)
                    segment_queue.put((speaker, samples, rate, source.is_loopback))
                buf         = []
                speech_sec  = 0.0
                silence_sec = 0.0

            try:
                while not stop_event.is_set():
                    # データが来ない間も、途中まで溜めた発話を確定できるようにタイムアウト付きで待つ
                    waited = 0.0
                    while not source.stream.available():
                        if stop_event.is_set():
                            raise _StreamStopped()
                        time.sleep(0.03)
                        waited += 0.03
                        if waited >= end_silence and buf:
                            silence_sec = end_silence
                            flush()
                    data     = source.stream.read(CHUNK)
                    samples  = array.array("h", data)
                    raw_peak = max(max(samples), -min(samples)) if samples else 0
                    # 波形は増幅していないので、しきい値判定の側でゲインを掛ける
                    peak     = min(32767, int(raw_peak * source.current_gain()))

                    if peak > threshold:
                        if not buf:
                            buf.extend(preroll)
                            preroll.clear()
                        buf.append(data)
                        speech_sec += chunk_sec
                        silence_sec = 0.0
                    elif buf:
                        buf.append(data)
                        silence_sec += chunk_sec
                        if silence_sec >= end_silence:
                            flush()
                    else:
                        preroll.append(data)

                    if buf and len(buf) * chunk_sec >= max_segment:
                        overlap = list(buf[-max(1, int(LOOPBACK_OVERLAP_SEC / chunk_sec)):]) if source.is_loopback else []
                        flush()
                        preroll.extend(overlap)
            except _StreamStopped:
                pass
            # 録音停止時、無音待ちの途中だった発話を捨てずに認識へ回す
            flush()
    except _StreamStopped:
        pass
    except Exception as e:
        if on_open_error is not None:
            on_open_error(source, e)
        else:
            report_error(f"{speaker}側の音声デバイスを開けませんでした：{e}")


def _whisper_worker(model, segment_queue, loop, callback, stop_event: threading.Event, on_status=None, on_warn=None, result_futures=None):
    """stop_event が立ってもキューを空にしてから終わる。
    録音停止の直前に確定した発話を取りこぼさないため。"""

    def send_status(message: str):
        if on_status is not None:
            asyncio.run_coroutine_threadsafe(on_status(message), loop)

    failure_reported = False

    while True:
        try:
            speaker, samples, rate, is_loopback = segment_queue.get(timeout=0.2)
        except queue.Empty:
            if stop_event.is_set():
                break
            continue
        duration = len(samples) / rate
        if duration >= 1.0:
            send_status(f"{speaker}側の音声を認識しています…")
        try:
            raw_audio = _resample_audio(samples, rate)
            audio = _normalize_audio(
                raw_audio,
                noise_floor=0.00025 if is_loopback else 0.003,
                max_gain=80.0 if is_loopback else 30.0,
            )
            if audio is None:
                continue
            text = _decode_whisper(model, audio, is_loopback)
            if not text and is_loopback:
                # 弱い相手音声だけコストの高い第二認識へ回す。全発話を二重処理すると
                # リアルタイム性が落ちるため、通常認識に失敗した場合に限定する。
                send_status(f"{speaker}側の音声を補正して再認識しています…")
                enhanced = _enhance_loopback_audio(raw_audio)
                if enhanced is not None:
                    text = _decode_whisper(model, enhanced, True, relaxed=True)
        except Exception as e:
            # 黙って捨てると「録音中なのに何も出ない」状態になり原因が分からないため、
            # 最初の1回だけ理由を伝える
            if not failure_reported:
                failure_reported = True
                if on_warn is not None:
                    asyncio.run_coroutine_threadsafe(
                        on_warn(f"音声認識に失敗しました（{e}）。以降の発話も認識できない可能性があります。"), loop)
            continue
        if not text:
            # 音は検出できたのに言葉にならなかったことをユーザーに見せる
            if duration >= 1.0:
                send_status(f"（{speaker}側の音声を検出しましたが、言葉を聞き取れませんでした）")
            continue
        future = asyncio.run_coroutine_threadsafe(callback(speaker, text), loop)
        if result_futures is not None:
            result_futures.append(future)


# ── Google（SpeechRecognition）経路：Whisper が使えないときのフォールバック ──


def _recognize_loop(source, speaker: str, loop, callback, on_error, stop_event: threading.Event, on_open_error=None):
    def report_error(message: str):
        if on_error is not None:
            asyncio.run_coroutine_threadsafe(on_error(message), loop)

    r = sr.Recognizer()
    if getattr(source, "is_loopback", False):
        # ループバックの無音はデジタル的な完全無音なので環境音調整は不要。
        # むしろ adjust はデータ到着（＝発話開始）までブロックして最初の発話を食い、
        # しきい値を発話音量で校正してしまい以降の認識も妨げるため、固定の低しきい値を使う。
        r.dynamic_energy_threshold = False
        r.energy_threshold = 100
    else:
        r.dynamic_energy_threshold = True
    try:
        with source:
            if not getattr(source, "is_loopback", False):
                try:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                except Exception:
                    pass
            while not stop_event.is_set():
                try:
                    audio = r.listen(source, timeout=1.0, phrase_time_limit=15)
                    text  = r.recognize_google(audio, language="ja-JP")
                    if text.strip():
                        asyncio.run_coroutine_threadsafe(callback(speaker, text.strip()), loop)
                except sr.WaitTimeoutError:
                    continue
                except sr.UnknownValueError:
                    continue
                except _StreamStopped:
                    break
                except sr.RequestError:
                    report_error(f"音声認識サービスに接続できませんでした（{speaker}側）。ネットワークを確認してください。")
                    break
                except Exception as e:
                    report_error(f"{speaker}側の音声認識が停止しました：{e}")
                    break
    except _StreamStopped:
        pass
    except Exception as e:
        if on_open_error is not None:
            on_open_error(source, e)
        else:
            report_error(f"{speaker}側の音声デバイスを開けませんでした：{e}")


def _drain_threads(capture_threads, worker_stop, worker_thread):
    """停止時、キャプチャスレッドが最後の発話を送り終え、認識スレッドが
    キューを空にするまで待つ。ここで待たないと停止直前の発話が消える。"""
    for t in capture_threads:
        t.join()
    if worker_stop is not None:
        worker_stop.set()
    if worker_thread is not None:
        worker_thread.join()


async def run_transcription(loopback_device: int | None, on_result, stop_event: asyncio.Event, mic_device: int = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者", on_error=None, on_warn=None, on_level=None, on_status=None, gain_state: dict = None):
    loop        = asyncio.get_event_loop()
    thread_stop = threading.Event()

    async def send_warn(message: str):
        if on_warn is not None:
            await on_warn(message)

    async def send_status(message: str):
        if on_status is not None:
            await on_status(message)

    # ループバックは「自動」（負の値 or 未指定）ならすべての再生デバイスを監視する。
    # 通話アプリの出力先が既定デバイスと違ってもどれかのデバイスで拾えるようにするため。
    auto_mode = loopback_device is None or loopback_device < 0
    if auto_mode:
        loopback_indexes = [d["index"] for d in list_loopback_devices()]
    else:
        loopback_indexes = [loopback_device]

    # デバイスが開けなくても、開けた側だけで録音を続ける。
    # 片方の失敗でセッション全体を落とすと、生きている音声まで失われるため。
    mic_failed    = False
    open_failures = []

    def on_mic_open_error(source, error):
        nonlocal mic_failed
        mic_failed = True
        if on_warn is not None:
            asyncio.run_coroutine_threadsafe(
                on_warn(f"{mic_speaker}側のマイクを開けませんでした（{error}）。PC再生音声のみで記録を続けます。"), loop)

    def on_loopback_open_error(source, error):
        open_failures.append(error)

    # モデル準備中から録音し、最初の発話もキューへ残す。
    engine = "whisper" if HAS_WHISPER else "google"
    # Google 認識は Recognizer が生の音量でしきい値を決めるため波形の増幅が要る。
    # Whisper は発話ごとに正規化するので、増幅すると歪むだけで得がない
    amplify          = engine == "google"
    mic_source       = _AudioSource(mic_device, stop_event=thread_stop, gain_state=gain_state, amplify=amplify)
    loopback_sources = [_AudioSource(i, stop_event=thread_stop, is_loopback=True, gain_state=gain_state, amplify=amplify) for i in loopback_indexes]

    capture_threads = []
    worker_thread   = None
    worker_stop     = None
    result_futures  = []

    if engine == "whisper":
        segment_queue = queue.Queue()
        worker_stop   = threading.Event()
        capture_threads.append(threading.Thread(
            target=_capture_loop,
            args=(mic_source, mic_speaker, segment_queue, thread_stop, on_error, loop),
            kwargs={"on_open_error": on_mic_open_error},
            daemon=True,
        ))
        for src in loopback_sources:
            capture_threads.append(threading.Thread(
                target=_capture_loop,
                args=(src, loopback_speaker, segment_queue, thread_stop, on_error, loop),
                kwargs={"on_open_error": on_loopback_open_error},
                daemon=True,
            ))
        for t in capture_threads:
            t.start()
        try:
            first_load = _whisper_model is None
            if first_load:
                await send_status("音声を録音しています。認識モデルを準備中です…")
            model = await loop.run_in_executor(None, _get_whisper_model)
            if first_load:
                await send_status("音声認識モデルの準備ができました。録音済みの音声も順に認識します。")
            worker_thread = threading.Thread(
                target=_whisper_worker,
                args=(model, segment_queue, loop, on_result, worker_stop),
                kwargs={"on_status": on_status, "on_warn": on_warn, "result_futures": result_futures},
                daemon=True,
            )
            worker_thread.start()
        except Exception as e:
            thread_stop.set()
            for t in capture_threads:
                await loop.run_in_executor(None, t.join)
            engine = "google"
            thread_stop = threading.Event()
            mic_source = _AudioSource(mic_device, stop_event=thread_stop, gain_state=gain_state, amplify=True)
            loopback_sources = [_AudioSource(i, stop_event=thread_stop, is_loopback=True, gain_state=gain_state, amplify=True) for i in loopback_indexes]
            mic_failed = False
            open_failures.clear()
            capture_threads = []
            worker_thread = None
            worker_stop = None
            await send_warn(f"ローカル音声認識モデルを読み込めませんでした（{e}）。Google音声認識で続行します。")

    if engine == "google":
        capture_threads.append(threading.Thread(
            target=_recognize_loop,
            args=(mic_source, mic_speaker, loop, on_result, on_error, thread_stop),
            kwargs={"on_open_error": on_mic_open_error},
            daemon=True,
        ))
        for src in loopback_sources:
            capture_threads.append(threading.Thread(
                target=_recognize_loop,
                args=(src, loopback_speaker, loop, on_result, on_error, thread_stop),
                kwargs={"on_open_error": on_loopback_open_error},
                daemon=True,
            ))

        for t in capture_threads:
            t.start()

    start_time       = time.monotonic()
    mic_silent_ticks = 0
    mic_warned       = False
    loopback_warned  = False
    all_dead_warned  = False

    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=2.0)
                break
            except asyncio.TimeoutError:
                pass
            elapsed = time.monotonic() - start_time

            mic_peak = mic_source.recent_peak
            mic_source.recent_peak = 0
            loop_peak = 0
            for src in loopback_sources:
                loop_peak = max(loop_peak, src.recent_peak)
                src.recent_peak = 0

            # 入力レベルを常時フロントへ送り、デバイス選択ミスを見えるようにする
            if on_level is not None:
                await on_level({
                    "mic":      min(100, round(mic_peak  / 327.68)),
                    "loopback": min(100, round(loop_peak / 327.68)),
                })

            # マイク：データは流れているのに音量がほぼゼロ → 別のマイクに話している可能性
            if mic_source.last_data_time is not None and mic_peak < 100:
                mic_silent_ticks += 1
            else:
                mic_silent_ticks = 0
            if not mic_warned and mic_silent_ticks >= 6:
                mic_warned = True
                await send_warn(f"{mic_speaker}側マイクの音量がほぼゼロです。実際に話しているマイクが選択されているか確認してください。")

            # ループバック
            loopback_dead = not loopback_sources or len(open_failures) >= len(loopback_sources)
            if loopback_dead:
                if not loopback_warned:
                    loopback_warned = True
                    await send_warn(f"{loopback_speaker}側（PC再生音声）のデバイスを開けませんでした。マイクのみで記録を続けます。")
            elif not auto_mode:
                src = loopback_sources[0]
                if not loopback_warned and elapsed > 12 and src.last_data_time is None:
                    loopback_warned = True
                    await send_warn(f"{loopback_speaker}側（PC再生音声）のデータが届いていません。通話アプリの音声が選択した再生デバイスから出ているか確認するか、デバイス選択を「自動」にしてください。")

            # 両側とも死んでいるときだけ、録音そのものを失敗として扱う
            if mic_failed and loopback_dead and not all_dead_warned:
                all_dead_warned = True
                if on_error is not None:
                    await on_error("音声デバイスを1つも開けませんでした。マイクとPC再生音声のデバイス設定を確認してください。")
                break
    finally:
        # キャンセルされても認識スレッドを確実に止める
        thread_stop.set()

    # 通常停止（キャンセルではない）の場合だけここに来る。
    # 停止直前の発話とキューに残った発話を認識しきってから WebSocket を閉じさせる
    await loop.run_in_executor(None, _drain_threads, capture_threads, worker_stop, worker_thread)
    for future in result_futures:
        try:
            await asyncio.wrap_future(future)
        except Exception:
            pass
