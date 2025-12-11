from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import requests
import faiss
import numpy as np
import json
import statistics
import datetime
import uuid
from huggingface_hub import InferenceClient
import os



HF_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
HF_API_KEY= os.getenv("HF_API_KEY")
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_credentials =True,
    allow_methods = ["*"],
    allow_headers =["*"]
)

app.mount("/static",StaticFiles(directory="static"), name ="static")

# Load Embedding model
# model = SentenceTransformer("pritamchoudhury/sentence-transformer-lite")
# model = SentenceTransformer("BAAI/bge-large-en-v1.5")

# Load faiss index
index = faiss.read_index("faiss.index")

# Reference metadata.json contains reference texts/ files
# references = json.load(open("corpus.json", "r"))
metadata = json.load(open("metadata.json", "r"))
# Logger file
LOG_FILE = "chat_logs.jsonl"

# Conversation History
conversation_history = []
MAX_HISTORY = 4

class ChatRequest(BaseModel):
    question: str

def log_interaction(question, answer, ref):
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "question": question,
        "answer": answer,
        "chunks_used": [r["id"] for r in ref],
        "titles": [r["title"] for r in ref],
        "reference": ref
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ---------------------------------------------
# Generate Embeddings
# ---------------------------------------------
def embed_query(payload):
    if not HF_API_KEY:
        raise HTTPException(status_code=500, detail="Hugging face API key missing")
    print(payload)
    url = "https://router.huggingface.co/hf-inference/models/BAAI/bge-large-en-v1.5/pipeline/feature-extraction"
    API_URL = "https://router.huggingface.co/nebius/v1/embeddings"
    headers = {"Authorization": f"Bearer {HF_API_KEY}"}
    try:
        response = requests.post(url=url, headers=headers, json={"inputs":payload}, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HF request failed : {e}")
    
    if response.status_code != 200:
        raise HTTPException(status_code=500,detail=f"HuggingFace Error: {response.status_code} | {response.text}")
    
    try:
        embedding = np.array(response.json()[0], dtype="float32")
        return embedding.reshape(1,-1)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to parse HF embedding.")

def embed_question(q:str):
    print(q)
    client = InferenceClient(provider="hf-inference", api_key="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")
    result = client.feature_extraction(
    q,
    model="BAAI/bge-large-en-v1.5",
)
    print(result)
    return result.reshape(1,-1)

# ---------------------------------------------
# Chat Conversation (Request a Question)
# ---------------------------------------------
@app.post("/api/chat")
async def chat(req:ChatRequest):
    global conversation_history

    question = req.question
    
    # Embed question
    vector = embed_question(question)
    
    # Search FAISS
    distances, ids = index.search(vector, k=3)

    # Get reference texts
    
    matched_refs = [metadata[i] for i in ids[0]]

    # Generate simple answer (could replace with LLM)
    answer = matched_refs[0]["text"] if len(matched_refs[0]["text"]) > 2  else matched_refs[1]["text"] if len(matched_refs[1]["text"]) > 2 else matched_refs[2]["text"]
    # results = []
    # for i, score in zip(ids, distances):
    #     entry = metadata[i]
    #     results.append({
    #         "id": entry["id"],
    #         "title": entry["title"],
    #         "path": entry["path"],
    #         "text": entry["text"],
    #         "full_section": entry["aggregated_content"]
    #     })
    
    # Log Q/A
    log_interaction(question, answer, matched_refs)
    
    # if str(answer) != "":
    #     answer =  "Could not understand! Try Again!!"
    #     matched_refs[0]['path'] = ""
    print( {"question": question,
        "answer": answer,
        "reference": matched_refs})
    return {
        "question": question,
        "answer": answer,
        "reference": matched_refs
    }


# ----------------------------------------------
# Analytics API Calls
# ----------------------------------------------
@app.get("/api/analytics/daily_count")
def daily_count():
    from collections import Counter
    counter = Counter()
    
    with open(LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                date = entry["timestamp"][:10]
                counter[date] += 1
            except Exception as e:
                print("JSON parse error:", e, "-->", line)
                continue
    
    return dict(counter)

@app.get("/", response_class=HTMLResponse)
def validation():
    url = "http://192.168.5.56:8000/static/dashboard.html"
    html_for_link = f"""
    <html>
    <head>
    <title>Base Page</title>
    </head>
    <body>
    <div>
    <button src={url} onclick="window.location.href='http://192.168.5.56:8000/static/dashboard.html'">Dashboard</button>
    </div>
    </body>
    </html>        
"""
    return html_for_link


@app.get("/api/analytics/top_chunks")
def top_chunks():
    from collections import Counter
    counter = Counter()
    
    with open(LOG_FILE, "r") as f:
        for line in f:
            entry = json.loads(line)
            for cid in entry["titles"]:
                counter[cid] += 1
    
    return counter.most_common(20)

@app.get("/api/analytics/answer_length")
def answer_length():
    lengths = []

    with open(LOG_FILE, "r") as f:
        for line in f:
            entry = json.loads(line)
            lengths.append(len(entry["answer"]))
    
    return {
        "avg": statistics.mean(lengths),
        "min":min(lengths),
        "max":max(lengths)
    }

@app.get("/api/analytics/top_questions")
def top_questions():
    from collections import Counter
    counter = Counter()
    
    with open(LOG_FILE, "r") as f:
        for line in f:
            entry = json.loads(line)
            q = entry["question"].strip().lower()
            counter[q] += 1
    
    return counter.most_common(20)

