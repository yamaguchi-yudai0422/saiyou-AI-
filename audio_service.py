import array
import asyncio
import collections
import difflib
import glob
import os
import queue
import re
import site
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

# 発話区切りのパラメータ（秒）
PREROLL_SEC     = 0.5   # 発話開始前に遡って含める長さ
END_SILENCE_SEC = 0.8   # この長さ無音が続いたら発話終了
MAX_SEGMENT_SEC = 12.0  # 1発話の最大長（長い発話は分割して随時認識）
MIN_SPEECH_SEC  = 0.3   # これより短い音はノイズとして捨てる

# 認識に文脈を与えるプロンプト（同音異義語の選択が安定する）
_INITIAL_PROMPT = "以下は、日本語の面接における丁寧な会話です。"

# 無音ノイズから Whisper が生成しがちな定型幻覚
_HALLUCINATIONS = ("ご視聴ありがとうございました", "チャンネル登録", "おやすみなさい")


def _is_prompt_echo(text: str) -> bool:
    """低品質な音声で initial_prompt がそのまま出力される幻覚を検出する"""
    strip = lambda s: re.sub(r"[、。\s]", "", s)
    return difflib.SequenceMatcher(None, strip(text), strip(_INITIAL_PROMPT)).ratio() > 0.6


def _setup_cuda_dlls():
    """pip の nvidia-cublas/cudnn ホイールの DLL を ctranslate2 から見えるようにする"""
    dirs = []
    for pkg in ("cublas", "cudnn", "cuda_nvrtc"):
        for sp in site.getsitepackages():
            for d in glob.glob(os.path.join(sp, "nvidia", pkg, "bin")):
                try:
                    os.add_dll_directory(d)
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
            arr = arr[0::self._channels]
        # 入力ブースト（音の小さいデバイス対策）。しきい値判定・レベル表示・認識の
        # すべてがブースト後の音を見るよう、読み出しの時点で増幅する
        gain = self._source.current_gain()
        if gain != 1.0:
            arr = np.clip(arr.astype(np.int32) * gain, -32768, 32767).astype(np.int16)
        if arr.size:
            peak = int(np.max(np.abs(arr.astype(np.int32))))
            self._source.last_data_time = time.monotonic()
            if peak > self._source.recent_peak:
                self._source.recent_peak = peak
        return arr.tobytes()


class _AudioSource(sr.AudioSource):
    """pyaudiowpatch ベースの汎用 AudioSource"""

    def __init__(self, device_index=None, stop_event=None, is_loopback=False, gain_state=None):
        self.device_index = device_index
        self.stop_event   = stop_event
        self.is_loopback  = is_loopback
        # 録音中でもスライダーで変更できるよう、共有 dict からゲインを毎回読む
        self.gain_state   = gain_state
        self.gain_key     = "loopback" if is_loopback else "mic"
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
            # ノイズ床は直近8秒のピーク最小値で推定する。ブーストで常時ノイズが
            # しきい値を超えても、発話の合間の谷から床を追従できる（EMA だと
            # 「常にしきい値超え」の状態で床が固まり、無音判定が一切出なくなる）
            base_threshold = 150 if source.is_loopback else 300
            peak_history   = collections.deque(maxlen=max(1, int(8.0 / chunk_sec)))

            def flush():
                nonlocal buf, speech_sec, silence_sec
                if buf and speech_sec >= MIN_SPEECH_SEC:
                    samples = np.frombuffer(b"".join(buf), dtype=np.int16)
                    segment_queue.put((speaker, samples, rate))
                buf         = []
                speech_sec  = 0.0
                silence_sec = 0.0

            while not stop_event.is_set():
                # データが来ない間も、途中まで溜めた発話を確定できるようにタイムアウト付きで待つ
                waited = 0.0
                while not source.stream.available():
                    if stop_event.is_set():
                        raise _StreamStopped()
                    time.sleep(0.03)
                    waited += 0.03
                    if waited >= END_SILENCE_SEC and buf:
                        silence_sec = END_SILENCE_SEC
                        flush()
                data    = source.stream.read(CHUNK)
                samples = array.array("h", data)
                peak    = max(max(samples), -min(samples)) if samples else 0

                peak_history.append(peak)
                threshold = max(base_threshold, min(peak_history) * 2.5 + 100)

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
                    if silence_sec >= END_SILENCE_SEC:
                        flush()
                else:
                    preroll.append(data)

                if buf and (speech_sec + silence_sec) * 1.0 >= MAX_SEGMENT_SEC:
                    flush()
    except _StreamStopped:
        pass
    except Exception as e:
        if on_open_error is not None:
            on_open_error(source, e)
        else:
            report_error(f"{speaker}側の音声デバイスを開けませんでした：{e}")


def _whisper_worker(model, segment_queue, loop, callback, stop_event: threading.Event, on_status=None):
    def send_status(message: str):
        if on_status is not None:
            asyncio.run_coroutine_threadsafe(on_status(message), loop)

    while not stop_event.is_set():
        try:
            speaker, samples, rate = segment_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        # 認識が追いつかない時は古いものから捨てて遅延がのびないようにする
        while segment_queue.qsize() > 4:
            try:
                speaker, samples, rate = segment_queue.get_nowait()
            except queue.Empty:
                break
        duration = len(samples) / rate
        if duration >= 1.0:
            send_status(f"{speaker}側の音声を認識しています…")
        try:
            audio = samples.astype(np.float32) / 32768.0
            # 16kHz へリサンプリング（Whisper の入力仕様）
            if rate != 16000:
                n_out = int(len(audio) * 16000 / rate)
                audio = np.interp(
                    np.linspace(0, len(audio) - 1, n_out),
                    np.arange(len(audio)),
                    audio,
                ).astype(np.float32)
            # 音量の小さいマイクでも認識できるよう正規化（ゲインは30倍まで）
            m = float(np.max(np.abs(audio)))
            if m < 0.003:
                continue
            audio = audio * min(0.9 / m, 30.0)

            segments, _ = model.transcribe(
                audio,
                language="ja",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=_INITIAL_PROMPT,
            )
            # 自信のない認識（かすかな物音などからの「創作」）は捨てる。
            # 表示するより「聞き取れなかった」と伝えるほうが誤記録を防げる
            kept = [
                s for s in segments
                if s.avg_logprob >= -1.0 and s.no_speech_prob <= 0.6
            ]
            text = "".join(s.text for s in kept).strip()
        except Exception:
            continue
        if not text or any(h in text for h in _HALLUCINATIONS) or _is_prompt_echo(text):
            # 音は検出できたのに言葉にならなかったことをユーザーに見せる
            if duration >= 1.0:
                send_status(f"（{speaker}側の音声を検出しましたが、言葉を聞き取れませんでした）")
            continue
        asyncio.run_coroutine_threadsafe(callback(speaker, text), loop)


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


async def run_transcription(loopback_device: int | None, on_result, stop_event: asyncio.Event, mic_device: int = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者", on_error=None, on_warn=None, on_level=None, on_status=None, gain_state: dict = None):
    loop        = asyncio.get_event_loop()
    thread_stop = threading.Event()

    async def send_warn(message: str):
        if on_warn is not None:
            await on_warn(message)

    # ループバックは「自動」（負の値 or 未指定）ならすべての再生デバイスを監視する。
    # 通話アプリの出力先が既定デバイスと違ってもどれかのデバイスで拾えるようにするため。
    auto_mode = loopback_device is None or loopback_device < 0
    if auto_mode:
        loopback_indexes = [d["index"] for d in list_loopback_devices()]
    else:
        loopback_indexes = [loopback_device]

    mic_source       = _AudioSource(mic_device, stop_event=thread_stop, gain_state=gain_state)
    loopback_sources = [_AudioSource(i, stop_event=thread_stop, is_loopback=True, gain_state=gain_state) for i in loopback_indexes]

    # 自動モードでは一部デバイスが開けなくても続行し、全滅したときだけエラーにする
    open_failures = []

    def on_loopback_open_error(source, error):
        open_failures.append(error)

    # ローカルの Whisper モデルが使えればそちらを優先し、無ければ Google 認識で動かす
    engine = "whisper" if HAS_WHISPER else "google"
    if engine == "whisper":
        try:
            first_load = _whisper_model is None
            if first_load:
                await send_warn("音声認識モデルを準備しています。初回はダウンロードに数分かかることがあります…")
            model = await loop.run_in_executor(None, _get_whisper_model)
            if first_load:
                await send_warn("音声認識モデルの準備ができました。")
        except Exception as e:
            engine = "google"
            await send_warn(f"ローカル音声認識モデルを読み込めませんでした（{e}）。Google音声認識で続行します。")

    threads = []
    if engine == "whisper":
        segment_queue = queue.Queue()
        threads.append(threading.Thread(
            target=_capture_loop,
            args=(mic_source, mic_speaker, segment_queue, thread_stop, on_error, loop),
            daemon=True,
        ))
        for src in loopback_sources:
            threads.append(threading.Thread(
                target=_capture_loop,
                args=(src, loopback_speaker, segment_queue, thread_stop, on_error, loop),
                kwargs={"on_open_error": on_loopback_open_error if auto_mode else None},
                daemon=True,
            ))
        threads.append(threading.Thread(
            target=_whisper_worker,
            args=(model, segment_queue, loop, on_result, thread_stop),
            kwargs={"on_status": on_status},
            daemon=True,
        ))
    else:
        threads.append(threading.Thread(
            target=_recognize_loop,
            args=(mic_source, mic_speaker, loop, on_result, on_error, thread_stop),
            daemon=True,
        ))
        for src in loopback_sources:
            threads.append(threading.Thread(
                target=_recognize_loop,
                args=(src, loopback_speaker, loop, on_result, on_error, thread_stop),
                kwargs={"on_open_error": on_loopback_open_error if auto_mode else None},
                daemon=True,
            ))

    for t in threads:
        t.start()

    start_time       = time.monotonic()
    mic_silent_ticks = 0
    mic_warned       = False
    loopback_warned  = False

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
            if auto_mode:
                if loopback_sources and len(open_failures) == len(loopback_sources) and not loopback_warned:
                    loopback_warned = True
                    await send_warn(f"{loopback_speaker}側（PC再生音声）のデバイスを1つも開けませんでした。マイクのみで記録を続けます。")
            else:
                src = loopback_sources[0]
                if not loopback_warned and elapsed > 12 and src.last_data_time is None:
                    loopback_warned = True
                    await send_warn(f"{loopback_speaker}側（PC再生音声）のデータが届いていません。通話アプリの音声が選択した再生デバイスから出ているか確認するか、デバイス選択を「自動」にしてください。")
    finally:
        # キャンセルされても認識スレッドを確実に止める
        thread_stop.set()
