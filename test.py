import os, requests
from dotenv import load_dotenv                                          
load_dotenv()
                                                                        
SYSTEM = "You are a pirate. Always respond in pirate speak."            
USER   = "What is the capital of France?"
                                                                        
# --- PublicAI ---                                    
r = requests.post(                                                      
    "https://api.publicai.co/v1/chat/completions",                      
    headers={"Authorization": f"Bearer {os.environ['PUBLICAI_API_KEY']}", "Content-Type": "application/json"},     
    json={                                                              
        "model": "swiss-ai/apertus-70b-instruct",     
        "messages": [                                                   
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": USER},                        
        ],                                            
    },
    timeout=30,                                                         
)
print(r.json()["choices"][0]["message"]["content"])                     
                                                    
# --- Ollama ---
r = requests.post(
    "http://192.168.86.47:11434/api/generate",                              
    json={"model": "MichelRosselli/apertus:latest", "system": SYSTEM, "prompt": USER,
"stream": False},                                                       
    timeout=30,                                       
)                                                                       
print(r.json()["response"])

