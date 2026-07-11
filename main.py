import sys
import os
import json
import time
import socket
import asyncio
import threading
import urllib.request

import webview
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ai_service import generate_final_review, generate_applicant_review, call_noesia_api, NoesiaError
from audio_service import list_microphone_devices, list_loopback_devices, run_transcription


def resource_path(relative: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(__file__), relative)


def config_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    return os.path.join(base, "config.json")


DEFAULT_CONFIG = {"api_name": "", "api_key": "", "mic_device": None, "audio_device": None}


def load_config() -> dict:
    path = config_path()
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config.update(json.load(f))
    return config


def save_config(data: dict):
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


app = FastAPI()


class InterviewData(BaseModel):
    company_info: str = ""
    resume: str = ""
    interview_log: str = ""


class ApplicantData(BaseModel):
    company_info: str = ""
    conversation_log: str = ""


class ConfigData(BaseModel):
    api_name: str | None = None
    api_key: str | None = None
    mic_device: int | None = None
    audio_device: int | None = None


class TestData(BaseModel):
    api_key: str = ""


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def set_config(data: ConfigData):
    # 送られてきた項目だけ更新し、他の設定は保持する
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


@app.websocket("/ws/transcribe")
async def transcribe_ws(websocket: WebSocket, device: int = 0, mic: int | None = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者"):
    await websocket.accept()

    stop_event = asyncio.Event()

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

    task = asyncio.create_task(run_transcription(device, on_result, stop_event, mic_device=mic, mic_speaker=mic_speaker, loopback_speaker=loopback_speaker, on_error=on_error))

    try:
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if msg == "stop":
                    break
            except asyncio.TimeoutError:
                if stop_event.is_set():
                    break
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
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


if __name__ == "__main__":
    port = find_free_port()
    thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    thread.start()
    wait_for_server(f"http://127.0.0.1:{port}/")

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass

    webview.create_window(
        title="Noesia 採用補助システム",
        url=f"http://127.0.0.1:{port}",
        width=960,
        height=820,
        min_size=(700, 600),
    )
    webview.start()
