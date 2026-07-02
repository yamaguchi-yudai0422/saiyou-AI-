import sys
import os
import json
import time
import asyncio
import threading
import urllib.request

import webview
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from ai_service import generate_final_review, generate_applicant_review
from audio_service import list_microphone_devices, list_loopback_devices, run_transcription


def resource_path(relative: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(os.path.dirname(__file__), relative)


def config_path() -> str:
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(__file__)
    return os.path.join(base, "config.json")


def load_config() -> dict:
    path = config_path()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"api_name": "", "api_key": "", "audio_device": None}


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
    api_name: str = ""
    api_key: str = ""
    mic_device: int | None = None
    audio_device: int | None = None


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def set_config(data: ConfigData):
    save_config(data.model_dump())
    return {"ok": True}


@app.get("/api/microphones")
def get_microphones():
    return list_microphone_devices()


@app.get("/api/devices")
def get_devices():
    return list_loopback_devices()


@app.post("/api/applicant/final")
async def applicant_final(data: ApplicantData):
    config = load_config()
    api_key = config.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Noesia APIキーが設定されていません。設定ボタンから入力してください。")
    try:
        return await generate_applicant_review(data.company_info, data.conversation_log, api_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/final")
async def final_analysis(data: InterviewData):
    config  = load_config()
    api_key = config.get("api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="Noesia APIキーが設定されていません。設定ボタンから入力してください。")
    try:
        return await generate_final_review(data.company_info, data.resume, data.interview_log, api_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/transcribe")
async def transcribe_ws(websocket: WebSocket, device: int = 0, mic: int = None, mic_speaker: str = "面接官", loopback_speaker: str = "志望者"):
    await websocket.accept()

    stop_event = asyncio.Event()

    async def on_result(speaker: str, text: str):
        try:
            await websocket.send_json({"speaker": speaker, "text": text})
        except Exception:
            stop_event.set()

    task = asyncio.create_task(run_transcription(device, on_result, stop_event, mic_device=mic, mic_speaker=mic_speaker, loopback_speaker=loopback_speaker))

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


def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")


def wait_for_server(url: str, retries: int = 30, interval: float = 0.2):
    for _ in range(retries):
        try:
            urllib.request.urlopen(url)
            return
        except Exception:
            time.sleep(interval)


if __name__ == "__main__":
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    wait_for_server("http://127.0.0.1:8000/")

    webview.create_window(
        title="Noesia 採用補助システム",
        url="http://127.0.0.1:8000",
        width=960,
        height=820,
        min_size=(700, 600),
    )
    webview.start()
