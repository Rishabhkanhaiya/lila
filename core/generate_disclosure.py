import math
import audioop

def generate_beep_ulaw(filename="disclosure.ulaw", duration_seconds=3, frequency=440):
    sample_rate = 8000
    num_samples = int(sample_rate * duration_seconds)
    
    pcm_bytes = bytearray()
    for i in range(num_samples):
        # 16-bit PCM sine wave
        value = int(32767.0 * math.sin(2.0 * math.pi * frequency * i / sample_rate))
        pcm_bytes.extend(value.to_bytes(2, byteorder='little', signed=True))
        
    mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
    
    with open(filename, "wb") as f:
        f.write(mulaw_bytes)
        
    print(f"Generated {filename} (Dummy disclosure beep for {duration_seconds}s)")

if __name__ == "__main__":
    generate_beep_ulaw()
