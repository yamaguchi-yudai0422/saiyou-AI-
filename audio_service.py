import array
import asyncio
import threading
import speech_recognition as sr

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

RATE  = 16000
CHUNK = 1024

# PortAudio の初期化・終了はスレッドセーフでないため、
# 複数スレッドから同時に呼ばれるとクラッシュする。ロックで直列化する。
_pa_lock = threading.Lock()


class _MonoStream:
    """多チャンネルの生ストリームを読み出し時にモノラル化するラッパー"""

    def __init__(self, stream, channels):
        self._stream   = stream
        self._channels = channels

    def read(self, size, exception_on_overflow=False):
        data = self._stream.read(size, exception_on_overflow=False)
        if self._channels == 1:
            return data
        samples = array.array("h", data)
        return samples[0::self._channels].tobytes()


class _AudioSource(sr.AudioSource):
    """pyaudiowpatch ベースの汎用 AudioSource"""

    def __init__(self, device_index=None):
        self.device_index = device_index
        self.SAMPLE_RATE  = RATE
        self.SAMPLE_WIDTH = 2
        self.CHUNK        = CHUNK
        self.pa      = None
        self.stream  = None
        self._raw    = None

    def __enter__(self):
        with _pa_lock:
            self.pa = pyaudio.PyAudio()
            # WASAPI 共有モードはデバイスのネイティブサンプルレートでしか開けないため、
            # 指定デバイスはデバイス情報のレートを使う（16kHz 固定だと -9997 で失敗する）
            if self.device_index is not None:
                info = self.pa.get_device_info_by_index(self.device_index)
                self.SAMPLE_RATE = int(info.get("defaultSampleRate") or RATE)
                native_channels  = max(1, int(info.get("maxInputChannels") or 1))
                attempts = [1] if native_channels == 1 else [1, native_channels]
            else:
                attempts = [1]

            last_error = None
            for channels in attempts:
                kwargs = dict(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=self.SAMPLE_RATE,
                    input=True,
                    frames_per_buffer=self.CHUNK,
                )
                if self.device_index is not None:
                    kwargs["input_device_index"] = self.device_index
                try:
                    self._raw   = self.pa.open(**kwargs)
                    self.stream = _MonoStream(self._raw, channels)
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


def _recognize_loop(source, speaker: str, loop, callback, on_error, stop_event: threading.Event):
    def report_error(message: str):
        if on_error is not None:
            asyncio.run_coroutine_threadsafe(on_error(message), loop)

    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    try:
        with source:
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
                except sr.RequestError:
                    report_error(f"音声認識サービスに接続できませんでした（{speaker}側）。ネットワークを確認してください。")
                    break
                except Exception as e:
                    report_error(f"{speaker}側の音声認識が停止しました：{e}")
                    break
    except Exception as e:
        report_error(f"{speaker}側の音声デバイスを開けませんでした：{e}")


async def run_transcription(loopback_device: int, on_result, stop_event: asyncio.Event, mic_device: int = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者", on_error=None):
    loop        = asyncio.get_event_loop()
    thread_stop = threading.Event()

    mic_thread = threading.Thread(
        target=_recognize_loop,
        args=(_AudioSource(mic_device), mic_speaker, loop, on_result, on_error, thread_stop),
        daemon=True,
    )
    desktop_thread = threading.Thread(
        target=_recognize_loop,
        args=(_AudioSource(loopback_device), loopback_speaker, loop, on_result, on_error, thread_stop),
        daemon=True,
    )

    mic_thread.start()
    desktop_thread.start()

    try:
        await stop_event.wait()
    finally:
        # キャンセルされても認識スレッドを確実に止める
        thread_stop.set()
