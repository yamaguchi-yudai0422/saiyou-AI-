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


class _AudioSource(sr.AudioSource):
    """pyaudiowpatch ベースの汎用 AudioSource"""

    def __init__(self, device_index=None):
        self.device_index = device_index
        self.SAMPLE_RATE  = RATE
        self.SAMPLE_WIDTH = 2
        self.CHUNK        = CHUNK
        self.pa     = None
        self.stream = None

    def __enter__(self):
        self.pa = pyaudio.PyAudio()
        kwargs = dict(
            format=pyaudio.paInt16,
            channels=1,
            rate=self.SAMPLE_RATE,
            input=True,
            frames_per_buffer=self.CHUNK,
        )
        if self.device_index is not None:
            kwargs["input_device_index"] = self.device_index
        self.stream = self.pa.open(**kwargs)
        return self

    def __exit__(self, *_):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.pa:
            self.pa.terminate()


def list_microphone_devices() -> list:
    if not HAS_PYAUDIO:
        return []
    result = []
    try:
        with pyaudio.PyAudio() as pa:
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
        with pyaudio.PyAudio() as pa:
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
