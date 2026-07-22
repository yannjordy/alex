import os
import multiprocessing
from pathlib import Path

_llm = None
_LLM_READY = False

MODEL_PATH = os.environ.get(
    "ALEX_GGUF_PATH",
    str(Path(__file__).resolve().parent.parent / "Dolphin3.0-Llama3.1-8B-Q4_K_M.gguf"),
)

N_THREADS = int(os.environ.get("ALEX_THREADS", max(multiprocessing.cpu_count() - 1, 4)))
N_CTX = int(os.environ.get("ALEX_CTX", 4096))


def load():
    global _llm, _LLM_READY
    if _llm is not None:
        return
    if not os.path.isfile(MODEL_PATH):
        print(f"[llm_local] Modèle introuvable : {MODEL_PATH}")
        return
    try:
        from llama_cpp import Llama
        _llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=N_CTX,
            n_threads=N_THREADS,
            n_gpu_layers=0,
            use_mmap=True,
            use_mlock=True,
            verbose=False,
        )
        _LLM_READY = True
        print(f"[llm_local] Modèle chargé : {os.path.basename(MODEL_PATH)} ({N_THREADS} threads, ctx={N_CTX})")
    except Exception as e:
        print(f"[llm_local] Erreur chargement modèle : {e}")


def is_ready() -> bool:
    return _LLM_READY and _llm is not None


def ask(message: str, system_prompt: str, max_tokens: int = 512, temperature: float = 0.7) -> str | None:
    if not is_ready():
        return None
    try:
        resp = _llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["<|im_end|>", "<|im_start|>"],
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[llm_local] Erreur inférence : {e}")
        return None


def ask_stream(message: str, system_prompt: str, max_tokens: int = 512, temperature: float = 0.7):
    if not is_ready():
        yield "data: {\"error\": \"Modèle non chargé\"}\n\n"
        return
    try:
        stream = _llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            stop=["<|im_end|>", "<|im_start|>"],
            stream=True,
        )
        for chunk in stream:
            delta = chunk["choices"][0]["delta"]
            if "content" in delta:
                yield f"data: {delta['content']}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        print(f"[llm_local] Erreur streaming : {e}")
        yield "data: {\"error\": \"Erreur lors de la génération\"}\n\n"
