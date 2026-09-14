import os
import uuid
import json
import base64
import audioop
import asyncio
import sqlite3
import speech_recognition as sr
from fastapi import FastAPI, WebSocket, Request, BackgroundTasks, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
import uvicorn
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from dotenv import load_dotenv

from google import genai
from google.genai import types as genai_types

load_dotenv()

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WHITELISTED_NUMBERS = [num.strip() for num in os.environ.get("WHITELISTED_NUMBERS", "").split(",") if num.strip()]

HOST_URL = os.environ.get("HOST_URL")

app = FastAPI(title="Jarvis Outbound Call Orchestrator")

if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None

if GEMINI_API_KEY:
    genai_client = genai.Client(api_key=GEMINI_API_KEY, http_options=genai_types.HttpOptions(api_version='v1beta'))
else:
    genai_client = None

class CallState:
    def __init__(self):
        self.status = "initializing"
        self.to_number = None
        self.task_prompt = "Ask the user what day today is."
        self.stop_event = asyncio.Event()
        self.audio_to_twilio = asyncio.Queue()
        self.audio_to_gemini = asyncio.Queue()
        self.asr_queue = asyncio.Queue()
        self.user_audio_buffer = bytearray()
        self.transcript = []
        self.current_jarvis_turn = ""
        self.summary = ""
        
active_calls = {}

async def pre_warm_gemini(call_task_id: str):
    """Background task to connect Gemini Live while the phone rings."""
    call_state = active_calls[call_task_id]
    
    if not genai_client:
        print("No GEMINI_API_KEY, cannot pre-warm.")
        return
        
    
    # Phase 6: Define end_call tool
    tools = [
        genai_types.Tool(
            function_declarations=[
                genai_types.FunctionDeclaration(
                    name="end_call",
                    description="Call this immediately when the conversation is successfully finished or if the person hangs up. This will terminate the phone call.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "reason": {
                                "type": "STRING",
                                "description": "Reason for ending the call (e.g., 'Task complete', 'User hung up')"
                            },
                            "summary": {
                                "type": "STRING",
                                "description": "A brief 1-2 sentence summary of what was achieved or discussed on the call."
                            }
                        },
                        "required": ["reason", "summary"]
                    }
                )
            ]
        )
    ]
        
    config = genai_types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        tools=tools,
        system_instruction=genai_types.Content(parts=[genai_types.Part.from_text(
            "You are an AI agent created by Rishabh Joshi, currently on a phone call on his behalf. "
            f"TASK: {call_state.task_prompt} "
            "SUCCESS CONDITION: When your task is complete or the user says goodbye, call the 'end_call' tool immediately. "
            "Keep responses extremely short. 1 sentence max."
        )]),
        speech_config=genai_types.SpeechConfig(
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name="Charon")
            )
        ),
        realtime_input_config=genai_types.RealtimeInputConfig(
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                disabled=False,
                start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_HIGH,
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=600,
            ),
        )
    )

    try:
        print(f"[Call {call_task_id}] Pre-warming Gemini...")
        async with genai_client.aio.live.connect(model="models/gemini-2.5-flash-native-audio-preview-12-2025", config=config) as session:
            print(f"[Call {call_task_id}] Gemini connected.")
            
            async def send_to_gemini():
                while not call_state.stop_event.is_set():
                    try:
                        pcm_16k = await asyncio.wait_for(call_state.audio_to_gemini.get(), timeout=1.0)
                        await session.send(genai_types.LiveClientRealtimeInput(
                            media_chunks=[
                                genai_types.Blob(
                                    data=pcm_16k,
                                    mime_type="audio/pcm;rate=16000"
                                )
                            ]
                        ))
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        print(f"Error sending to Gemini: {e}")
                        break

            async def recv_from_gemini():
                async for response in session.receive():
                    if call_state.stop_event.is_set():
                        break
                    server_content = response.server_content
                    
                    # Trigger ASR on user buffer when Gemini starts a turn
                    if server_content is not None and server_content.model_turn and len(call_state.user_audio_buffer) > 0:
                        call_state.asr_queue.put_nowait(bytes(call_state.user_audio_buffer))
                        call_state.user_audio_buffer.clear()
                        
                    if server_content is not None:
                        model_turn = server_content.model_turn
                        if model_turn:
                            for part in model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    pcm_24k = part.inline_data.data
                                    await call_state.audio_to_twilio.put(pcm_24k)
                                elif part.function_call:
                                    if part.function_call.name == "end_call":
                                        args = part.function_call.args
                                        reason = args.get("reason", "Unknown")
                                        summary = args.get("summary", "No summary")
                                        print(f"[Call {call_task_id}] Gemini called end_call! Reason: {reason}")
                                        print(f"[Call {call_task_id}] Call Summary: {summary}")
                                        
                                        # Save summary for Phase 7 (Inject to memory later)
                                        call_state.summary = summary
                                        
                                        # Terminate call
                                        call_state.stop_event.set()
                                        
                                        # Phase 7: Inject summary into memory.db
                                        try:
                                            db_path = os.path.join("data", "jarvis_memory.db")
                                            os.makedirs("data", exist_ok=True)
                                            conn = sqlite3.connect(db_path)
                                            conn.execute("CREATE TABLE IF NOT EXISTS unresolved_topics (id INTEGER PRIMARY KEY, topic TEXT, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                                            conn.execute("INSERT INTO unresolved_topics (topic) VALUES (?)", (f"Outbound Call Summary to {call_state.to_number}: {summary}",))
                                            conn.commit()
                                            conn.close()
                                            print(f"[Call {call_task_id}] Summary injected into {db_path} unresolved_topics.")
                                        except Exception as db_e:
                                            print(f"Failed to write summary to DB: {db_e}")
                                            
                                        break
                                elif part.text:
                                    call_state.current_jarvis_turn += part.text
                                    
                            if server_content.turn_complete and call_state.current_jarvis_turn:
                                call_state.transcript.append({"role": "jarvis", "text": call_state.current_jarvis_turn})
                                print(f"[Transcript] Jarvis: {call_state.current_jarvis_turn}")
                                call_state.current_jarvis_turn = ""
                                
                            if server_content.interrupted:
                                print(f"[Call {call_task_id}] Gemini Interrupted by user.")

            await asyncio.gather(
                send_to_gemini(),
                recv_from_gemini()
            )
    except Exception as e:
        print(f"[Call {call_task_id}] Gemini session error: {e}")
    finally:
        call_state.stop_event.set()
        print(f"[Call {call_task_id}] Gemini session ended.")


@app.post("/api/initiate-call")
async def initiate_call(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    to_number = data.get("to")
    task_prompt = data.get("task")
    
    if not to_number:
        return {"error": "Missing 'to' number"}
    if not twilio_client:
        return {"error": "Twilio credentials not configured"}
        
    if WHITELISTED_NUMBERS and to_number not in WHITELISTED_NUMBERS:
        return {"error": f"Number {to_number} is not in the whitelist"}
        
    global HOST_URL
    if not HOST_URL:
        return {"error": "HOST_URL (ngrok) not configured yet"}
        
    call_task_id = str(uuid.uuid4())
    state = CallState()
    state.to_number = to_number
    if task_prompt:
        state.task_prompt = task_prompt
    state.status = "dialing"
    active_calls[call_task_id] = state
    
    twiml_url = f"{HOST_URL}/twiml/call-start?call_task_id={call_task_id}"
    
    # Phase 4: Pre-warm Gemini in background immediately
    background_tasks.add_task(pre_warm_gemini, call_task_id)
    
    try:
        call = twilio_client.calls.create(
            to=to_number,
            from_=TWILIO_PHONE_NUMBER,
            url=twiml_url
        )
        return {"success": True, "call_sid": call.sid, "call_task_id": call_task_id}
    except Exception as e:
        return {"error": str(e)}

@app.post("/twiml/call-start")
async def twiml_call_start(call_task_id: str):
    global HOST_URL
    WS_URL = HOST_URL.replace("http://", "ws://").replace("https://", "wss://")
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"{WS_URL}/media-stream?call_task_id={call_task_id}")
    response.append(connect)
    return Response(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket, call_task_id: str = None):
    await websocket.accept()
    print(f"[Call {call_task_id}] Media stream connected!")
    
    call_state = active_calls.get(call_task_id)
    if not call_state:
        print("Call state not found, closing.")
        await websocket.close()
        return
        
    stream_sid = None
    disclosure_done = asyncio.Event()
    
    async def enforce_duration_cap():
        await asyncio.sleep(300) # 5 minute hard cap
        print(f"[Call {call_task_id}] Hard duration cap reached. Terminating.")
        call_state.stop_event.set()
        
    async def play_disclosure():
        if os.path.exists("disclosure.ulaw"):
            print(f"[Call {call_task_id}] Playing hardcoded disclosure audio...")
            with open("disclosure.ulaw", "rb") as f:
                ulaw_bytes = f.read()
            chunk_size = 160
            for i in range(0, len(ulaw_bytes), chunk_size):
                if call_state.stop_event.is_set():
                    break
                chunk = ulaw_bytes[i:i+chunk_size]
                if stream_sid:
                    await websocket.send_text(json.dumps({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": base64.b64encode(chunk).decode("utf-8")}
                    }))
                await asyncio.sleep(0.02)
        disclosure_done.set()
        print(f"[Call {call_task_id}] Disclosure complete, AI takes over.")

    async def process_asr():
        r = sr.Recognizer()
        while not call_state.stop_event.is_set():
            try:
                audio_bytes = await asyncio.wait_for(call_state.asr_queue.get(), timeout=1.0)
                if len(audio_bytes) < 16000:  # ignore if < 0.5 sec
                    continue
                def do_recognize():
                    audio_data = sr.AudioData(audio_bytes, 16000, 2)
                    try:
                        return r.recognize_google(audio_data, language="hi-IN")
                    except sr.UnknownValueError:
                        return ""
                    except Exception as e:
                        print(f"ASR Error: {e}")
                        return ""
                text = await asyncio.to_thread(do_recognize)
                if text:
                    call_state.transcript.append({"role": "user", "text": text})
                    print(f"[Transcript] User: {text}")
            except asyncio.TimeoutError:
                continue

    async def send_to_twilio():
        cv_state_down = None
        await disclosure_done.wait()
        while not call_state.stop_event.is_set():
            try:
                pcm_24k = await asyncio.wait_for(call_state.audio_to_twilio.get(), timeout=1.0)
                # Resample 24kHz -> 8kHz
                pcm_8k_out, cv_state_down = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, cv_state_down)
                # Linear PCM -> mulaw
                mulaw_out = audioop.lin2ulaw(pcm_8k_out, 2)
                
                if stream_sid:
                    response_media = {
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {
                            "payload": base64.b64encode(mulaw_out).decode("utf-8")
                        }
                    }
                    await websocket.send_text(json.dumps(response_media))
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error sending to Twilio WS: {e}")
                break

    async def recv_from_twilio():
        nonlocal stream_sid
        cv_state_up = None
        while not call_state.stop_event.is_set():
            try:
                message_text = await websocket.receive_text()
                data = json.loads(message_text)
                event = data.get("event")
                
                if event == "start":
                    stream_sid = data["start"]["streamSid"]
                    print(f"[Call {call_task_id}] Stream started: {stream_sid}")
                    asyncio.create_task(play_disclosure())
                    
                elif event == "media":
                    payload = data["media"]["payload"]
                    chunk = base64.b64decode(payload)
                    # Convert Twilio 8kHz mulaw to 16kHz PCM for Gemini
                    pcm_8k = audioop.ulaw2lin(chunk, 2)
                    pcm_16k, cv_state_up = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, cv_state_up)
                    call_state.user_audio_buffer.extend(pcm_16k)
                    await call_state.audio_to_gemini.put(pcm_16k)
                    
                elif event == "stop":
                    print(f"[Call {call_task_id}] Stream stopped by Twilio")
                    call_state.stop_event.set()
                    break
            except WebSocketDisconnect:
                call_state.stop_event.set()
                break
            except Exception as e:
                print(f"Error receiving from Twilio WS: {e}")
                break

    try:
        await asyncio.gather(
            send_to_twilio(),
            recv_from_twilio(),
            process_asr(),
            enforce_duration_cap()
        )
    finally:
        call_state.stop_event.set()
        
        # Flush remaining ASR buffer
        if len(call_state.user_audio_buffer) > 0:
            r = sr.Recognizer()
            audio_data = sr.AudioData(bytes(call_state.user_audio_buffer), 16000, 2)
            try:
                text = r.recognize_google(audio_data, language="hi-IN")
                if text:
                    call_state.transcript.append({"role": "user", "text": text})
            except Exception:
                pass
                
        # Write full transcript to memory database
        if call_state.transcript:
            try:
                db_path = os.path.join("data", "jarvis_memory.db")
                conn = sqlite3.connect(db_path)
                conn.execute("CREATE TABLE IF NOT EXISTS transcripts (id INTEGER PRIMARY KEY, call_id TEXT, role TEXT, text TEXT, date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                for t in call_state.transcript:
                    conn.execute("INSERT INTO transcripts (call_id, role, text) VALUES (?, ?, ?)", (call_task_id, t["role"], t["text"]))
                conn.commit()
                conn.close()
                print(f"[Call {call_task_id}] Saved full bidirectional transcript to DB.")
            except Exception as e:
                print(f"Failed to save transcript: {e}")
                
        if call_task_id in active_calls:
            del active_calls[call_task_id]
        print(f"[Call {call_task_id}] Media stream closed.")

if __name__ == "__main__":
    port = int(os.environ.get("CALL_PORT", 8001))
    
    if not HOST_URL:
        try:
            from pyngrok import ngrok
            public_url = ngrok.connect(port).public_url
            HOST_URL = public_url
            print(f"Ngrok tunnel established: {HOST_URL}")
        except Exception as e:
            print(f"Failed to start ngrok: {e}")
            
    print(f"Starting Outbound Call Orchestrator on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
