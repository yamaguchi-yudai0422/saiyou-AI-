import sys
import os
import json
import base64
import binascii
import time
import socket
import asyncio
import threading
import urllib.request
import subprocess

import webview
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ai_service import (
    NoesiaError,
    call_noesia_api,
    generate_applicant_question,
    generate_applicant_review,
    generate_final_review,
)
from audio_service import list_microphone_devices, list_loopback_devices, run_transcription
from ocr_service import ResumeOcrError, extract_resume_pdf, recognize_resume_image


def resource_path(relative: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(__file__), relative)


def config_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    return os.path.join(base, "config.json")


DEFAULT_CONFIG = {"api_name": "", "api_key": "", "mic_device": None, "audio_device": None, "mic_gain": 1, "loopback_gain": 1}

# 設定は「読んで→書き換えて→書き戻す」ため、同時保存（ゲイン2本のスライダー等）で
# 片方の更新が消える。FastAPI は同期エンドポイントをスレッドプールで並行実行するのでロックが要る
_config_lock = threading.Lock()


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            config.update(data)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        # 壊れた config.json で全 API が 500 になるのを避け、既定値で起動する。
        # 次の保存で正常な内容に上書きされる
        pass
    return config


def save_config(data: dict):
    # 書き込み途中で落ちても元のファイルを壊さないよう、一時ファイル経由で差し替える
    path = config_path()
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


app = FastAPI()


class InterviewData(BaseModel):
    company_info: str = ""
    resume: str = ""
    interview_log: str = ""


class ApplicantData(BaseModel):
    company_info: str = ""
    conversation_log: str = ""


class ResumeImageData(BaseModel):
    filename: str = "履歴書画像"
    image_base64: str


class ResumePdfData(BaseModel):
    filename: str = "履歴書.pdf"
    pdf_base64: str


class ConfigData(BaseModel):
    api_name: str | None = None
    api_key: str | None = None
    mic_device: int | None = None
    audio_device: int | None = None
    mic_gain: float | None = None
    loopback_gain: float | None = None


class TestData(BaseModel):
    api_key: str = ""


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def set_config(data: ConfigData):
    # 送られてきた項目だけ更新し、他の設定は保持する
    with _config_lock:
        config = load_config()
        config.update(data.model_dump(exclude_unset=True))
        save_config(config)
    return {"ok": True}


@app.get("/api/microphones")
def get_microphones():
    return list_microphone_devices()


@app.get("/api/devices")
def get_devices():
    return list_loopback_devices()


def _decode_base64_upload(encoded: str, max_bytes: int, label: str) -> bytes:
    if len(encoded) > (max_bytes * 4 // 3) + 4:
        raise HTTPException(status_code=413, detail=f"{label}は{max_bytes // (1024 * 1024)}MB以下にしてください。")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail=f"{label}データが壊れています。")
    if len(decoded) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{label}は{max_bytes // (1024 * 1024)}MB以下にしてください。")
    return decoded


@app.post("/api/resume/ocr")
async def resume_ocr(data: ResumeImageData):
    image_bytes = _decode_base64_upload(data.image_base64, 12 * 1024 * 1024, "画像")
    try:
        result = await recognize_resume_image(image_bytes)
        return {**result, "filename": os.path.basename(data.filename)[:200]}
    except ResumeOcrError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/resume/pdf")
async def resume_pdf(data: ResumePdfData):
    pdf_bytes = _decode_base64_upload(data.pdf_base64, 20 * 1024 * 1024, "PDF")
    try:
        result = await extract_resume_pdf(pdf_bytes)
        return {**result, "filename": os.path.basename(data.filename)[:200]}
    except ResumeOcrError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/api/test")
async def test_connection(data: TestData):
    api_key = data.api_key or load_config().get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="APIキーが入力されていません。")
    try:
        result = await call_noesia_api("やあ、聞こえてる？", [], api_key, max_tokens=100)
        return {"ok": True, "reply": result["reply"]}
    except NoesiaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予期しないエラーが発生しました：{e}")


@app.post("/api/applicant/final")
async def applicant_final(data: ApplicantData):
    config = load_config()
    api_key = config.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Noesia APIキーが設定されていません。設定ボタンから入力してください。")
    try:
        return await generate_applicant_review(data.company_info, data.conversation_log, api_key)
    except NoesiaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予期しないエラーが発生しました：{e}")


@app.post("/api/applicant/question")
async def applicant_question(data: ApplicantData):
    config = load_config()
    api_key = config.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Noesia APIキーが設定されていません。設定ボタンから入力してください。")
    if not data.conversation_log.strip():
        raise HTTPException(status_code=400, detail="質問を考えるには、会話ログが必要です。録音または手動入力で会話を追加してください。")
    try:
        return await generate_applicant_question(data.company_info, data.conversation_log, api_key)
    except NoesiaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予期しないエラーが発生しました：{e}")


@app.post("/api/final")
async def final_analysis(data: InterviewData):
    config  = load_config()
    api_key = config.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Noesia APIキーが設定されていません。設定ボタンから入力してください。")
    try:
        return await generate_final_review(data.company_info, data.resume, data.interview_log, api_key)
    except NoesiaError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"予期しないエラーが発生しました：{e}")


def _clamp_gain(value) -> float:
    try:
        return max(1.0, min(30.0, float(value)))
    except (TypeError, ValueError):
        return 1.0


@app.websocket("/ws/transcribe")
async def transcribe_ws(websocket: WebSocket, device: int = -1, mic: int | None = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者", mic_gain: float = 1.0, loopback_gain: float = 1.0):
    await websocket.accept()

    stop_event = asyncio.Event()
    gain_state = {"mic": _clamp_gain(mic_gain), "loopback": _clamp_gain(loopback_gain)}

    async def on_result(speaker: str, text: str):
        try:
            await websocket.send_json({"speaker": speaker, "text": text})
        except Exception:
            stop_event.set()

    async def on_error(message: str):
        try:
            await websocket.send_json({"error": message})
        except Exception:
            pass
        stop_event.set()

    async def on_warn(message: str):
        try:
            await websocket.send_json({"warn": message})
        except Exception:
            pass

    async def on_level(levels: dict):
        try:
            await websocket.send_json({"level": levels})
        except Exception:
            pass

    async def on_status(message: str):
        try:
            await websocket.send_json({"status": message})
        except Exception:
            pass

    task = asyncio.create_task(run_transcription(device if device >= 0 else None, on_result, stop_event, mic_device=mic, mic_speaker=mic_speaker, loopback_speaker=loopback_speaker, on_error=on_error, on_warn=on_warn, on_level=on_level, on_status=on_status, gain_state=gain_state))
    # 認識タスクが何らかの理由で落ちても、受信ループが「録音中」のまま回り続けないようにする
    task.add_done_callback(lambda _: stop_event.set())

    connected = True
    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if msg == "stop":
                    break
                # 録音中のゲイン変更（{"mic_gain": 5} / {"loopback_gain": 3}）
                try:
                    update = json.loads(msg)
                    if "mic_gain" in update:
                        gain_state["mic"] = _clamp_gain(update["mic_gain"])
                    if "loopback_gain" in update:
                        gain_state["loopback"] = _clamp_gain(update["loopback_gain"])
                except (ValueError, TypeError):
                    pass
            except asyncio.TimeoutError:
                if stop_event.is_set():
                    break
    except WebSocketDisconnect:
        connected = False
    finally:
        stop_event.set()
        # 「録音停止」で閉じる場合は、停止直前の発話を認識し終えるまで待つ。
        # ここで即 cancel すると、無音0.8秒による発話確定を待っていた最後の一言が消える
        if connected:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=30.0)
            except Exception:
                pass
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        if connected:
            # 認識しきってから閉じる。フロントはこの close を待って停止処理を終える
            try:
                await websocket.close()
            except Exception:
                pass


static_dir = resource_path("static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "index.html"))


def find_free_port() -> int:
    # 8000 番決め打ちだと他アプリと衝突して別アプリの画面が表示されるため、空きポートを使う
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server(port: int):
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")


def wait_for_server(url: str, retries: int = 30, interval: float = 0.2):
    for _ in range(retries):
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(interval)


def find_edge_executable() -> str | None:
    candidates = []
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    return next((path for path in candidates if os.path.isfile(path)), None)


def run_edge_fallback(url: str) -> bool:
    """Run the local UI in an Edge app window when pythonnet cannot start."""
    edge = find_edge_executable()
    if not edge:
        return False

    local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    profile_dir = os.path.join(local_app_data, "NoesiaHR", "EdgeProfile")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        process = subprocess.Popen([
            edge,
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--disable-default-apps",
            "--disable-background-mode",
        ])
        process.wait()
        return True
    except OSError:
        return False


def show_startup_error() -> None:
    message = (
        "Noesiaの画面を起動できませんでした。\n\n"
        "1. ダウンロードしたZIPを右クリックして［プロパティ］を開く\n"
        "2. ［許可する］または［ブロックの解除］にチェックしてから再解凍する\n"
        "3. 改善しない場合は Microsoft Edge と .NET Framework 4.8 を更新する"
    )
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Noesia 起動エラー", 0x10)
    except Exception:
        pass


if __name__ == "__main__":
    port = find_free_port()
    thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    thread.start()
    app_url = f"http://127.0.0.1:{port}/"
    wait_for_server(app_url)

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass

    webview.create_window(
        title="Noesia 採用補助システム",
        url=app_url,
        width=960,
        height=820,
        min_size=(700, 600),
    )
    try:
        webview.start()
    except Exception:
        # Downloaded DLLs or an older .NET environment can prevent WinForms from loading.
        if not run_edge_fallback(app_url):
            show_startup_error()
