"""Vérifie que l'environnement du POC est correctement installé.

Usage :
    python scripts/check_env.py            # tests hors ligne uniquement
    python scripts/check_env.py --online   # + appel réel à l'API Mistral (clé requise)
"""
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

warnings.filterwarnings("ignore")  # masque les DeprecationWarning de langchain-community

OK, KO = "[OK]", "[ÉCHEC]"
errors = 0


def check(label, func):
    """Exécute un test et affiche le résultat sans interrompre les suivants."""
    global errors
    try:
        detail = func()
        print(f"{OK:8} {label}" + (f" -> {detail}" if detail else ""))
    except Exception as exc:  # noqa: BLE001
        errors += 1
        print(f"{KO:8} {label} -> {type(exc).__name__}: {exc}")


def python_version():
    if sys.version_info < (3, 10):
        raise RuntimeError(f"Python >= 3.10 requis, trouvé {sys.version.split()[0]}")
    return sys.version.split()[0]


def faiss_index():
    import faiss
    import numpy as np

    vecs = np.random.rand(10, 16).astype("float32")
    faiss.normalize_L2(vecs)
    index = faiss.IndexFlatIP(16)
    index.add(vecs)
    _, ids = index.search(vecs[:1], 1)
    assert index.ntotal == 10 and ids[0][0] == 0
    return f"faiss {faiss.__version__}, recherche OK"


def langchain_faiss():
    import tempfile

    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from langchain_core.embeddings import FakeEmbeddings

    emb = FakeEmbeddings(size=16)
    docs = [Document(page_content="Concert de jazz", metadata={"uid": "1"})]
    store = FAISS.from_documents(docs, emb)
    with tempfile.TemporaryDirectory() as tmp:
        store.save_local(tmp)
        loaded = FAISS.load_local(tmp, emb, allow_dangerous_deserialization=True)
    return f"sauvegarde/rechargement OK, {loaded.index.ntotal} vecteur(s)"


def mistral_imports():
    from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings  # noqa: F401
    from mistralai import Mistral  # noqa: F401

    return "langchain_mistralai + SDK mistralai"


def other_imports():
    import bs4, dotenv, fastapi, httpx, pandas, ragas, requests, uvicorn  # noqa: E401,F401
    from fastapi.testclient import TestClient  # noqa: F401
    from ragas.metrics import Faithfulness  # noqa: F401

    return (f"pandas {pandas.__version__}, fastapi {fastapi.__version__}, "
            f"uvicorn {uvicorn.__version__}, ragas {ragas.__version__}")


def api_key():
    from dotenv import load_dotenv

    load_dotenv()
    key = os.getenv("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("MISTRAL_API_KEY absente (créez un fichier .env à partir de .env.example)")
    return "clé trouvée (valeur non affichée)"


def mistral_online():
    from langchain_mistralai import ChatMistralAI, MistralAIEmbeddings

    from rag import config

    vec = MistralAIEmbeddings(model=config.MISTRAL_EMBEDDING_MODEL).embed_query("test")
    llm = ChatMistralAI(model=config.LLM_MODEL, temperature=0)
    answer = llm.invoke("Réponds uniquement par le mot : OK").content
    return f"embedding de dimension {len(vec)}, LLM a répondu « {answer.strip()[:20]} »"


if __name__ == "__main__":
    print("=== Vérification de l'environnement Puls-Events RAG ===")
    check("Version de Python", python_version)
    check("Faiss (index natif)", faiss_index)
    check("LangChain + FAISS", langchain_faiss)
    check("Imports Mistral", mistral_imports)
    check("Autres bibliothèques", other_imports)
    check("Clé API Mistral", api_key)
    if "--online" in sys.argv:
        check("Appel réel à l'API Mistral", mistral_online)
    print("=" * 55)
    print("Environnement prêt." if errors == 0 else f"{errors} problème(s) à corriger.")
    sys.exit(1 if errors else 0)
