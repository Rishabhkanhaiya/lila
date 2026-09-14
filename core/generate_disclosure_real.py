import os
import asyncio
import miniaudio
import audioop

async def generate_real_disclosure():
    text = "Hi, main Rishabh Joshi ka banaya hua AI agent hoon, uski taraf se baat kar raha hoon."
    voice = "hi-IN-SwaraNeural"
    mp3_file = "temp_disclosure.mp3"
    
    # 1. Generate MP3 with native async edge-tts (no shell subprocess)
    print(f"Generating TTS for: {text}")
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_file)
    
    # 2. Decode MP3 to PCM using miniaudio
    print("Decoding MP3...")
    with open(mp3_file, "rb") as f:
        mp3_data = f.read()
    
    decoded = miniaudio.decode(mp3_data, nchannels=1, sample_rate=8000)
    
    # The output from miniaudio is 16-bit PCM at 8000 Hz, 1 channel.
    import struct
    pcm_bytes = struct.pack(f"<{len(decoded.samples)}h", *decoded.samples)
    
    # 3. Convert to 8kHz mulaw
    print("Converting to mulaw...")
    mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
    
    # 4. Save to disclosure.ulaw
    out_file = "disclosure.ulaw"
    with open(out_file, "wb") as f:
        f.write(mulaw_bytes)
        
    print(f"Successfully generated {out_file} (Real TTS Voice).")
    
    # Cleanup
    if os.path.exists(mp3_file):
        os.remove(mp3_file)

if __name__ == "__main__":
    asyncio.run(generate_real_disclosure())
