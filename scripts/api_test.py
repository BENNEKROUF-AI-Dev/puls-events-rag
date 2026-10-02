"""Vérifie une API déjà démarrée, en tapant dessus comme le ferait un client.

    python scripts/serve.py            # dans un premier terminal
    python scripts/api_test.py        # dans un second

Différence avec `pytest tests/test_api.py` : les tests automatiques appellent
l'application en mémoire, avec un index factice. Ce script-là interroge un vrai
serveur HTTP, sur le vrai index — c'est la vérification de bout en bout, celle
qu'on montre pendant la soutenance.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402

QUESTIONS = [
    "quels concerts de musique classique à venir ?",
    "une exposition à voir en famille à Nantes",
    "un atelier pour apprendre à cuisiner",
    "des idées de sortie gratuite ce week-end",
    "un spectacle de danse contemporaine",
]

OK, KO = "  OK  ", " ÉCHEC"
resultats: list[bool] = []


def verifier(libelle: str, condition: bool, detail: str = "") -> bool:
    resultats.append(bool(condition))
    print(f"[{OK if condition else KO}] {libelle}{f' — {detail}' if detail else ''}")
    return bool(condition)


def tester_health(client: httpx.Client) -> None:
    print("\n--- GET /health ---")
    response = client.get("/health")
    verifier("le service répond", response.status_code == 200, f"HTTP {response.status_code}")
    if response.status_code != 200:
        return
    body = response.json()
    verifier("un index est en place", body["index_disponible"] is True)
    verifier("l'index contient des documents", body["index"].get("nombre_documents", 0) > 0,
             f"{body['index'].get('nombre_documents')} vecteurs")
    print(f"         modèle de rédaction : {body['modele_llm']}")
    print(f"         modèle d'embeddings : {body['index'].get('model')}")


def tester_ask(client: httpx.Client) -> None:
    print("\n--- POST /ask ---")
    degrade = 0
    for question in QUESTIONS:
        response = client.post("/ask", json={"question": question})
        if not verifier(f"« {question} »", response.status_code == 200,
                        f"HTTP {response.status_code}"):
            continue
        body = response.json()
        verifier("    des sources sont citées", bool(body["sources"]),
                 f"{len(body['sources'])} événements, {body['duree_secondes']} s")
        verifier("    tous les liens pointent vers Open Agenda",
                 all(s["lien"].startswith("https://openagenda.com/") for s in body["sources"]))
        if not body["llm_disponible"]:
            degrade += 1
    if degrade:
        print(f"\n  Note : {degrade} réponse(s) en mode dégradé (LLM indisponible). "
              "La recherche fonctionne, la rédaction non — vérifiez le quota Mistral.")


def tester_validation(client: httpx.Client) -> None:
    print("\n--- Refus des demandes invalides ---")
    for libelle, charge in [("question absente", {}),
                            ("question vide", {"question": ""}),
                            ("question d'un caractère", {"question": "?"}),
                            ("k hors bornes", {"question": "concert", "k": 999})]:
        code = client.post("/ask", json=charge).status_code
        verifier(f"{libelle} -> 422", code == 422, f"HTTP {code}")


def tester_rebuild(client: httpx.Client) -> None:
    print("\n--- POST /rebuild (protection) ---")
    code = client.post("/rebuild", json={}, headers={"X-Rebuild-Token": "jeton-volontairement-faux"}).status_code
    verifier("un jeton invalide est refusé", code in (401, 403), f"HTTP {code}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true",
                        help="Affiche aussi la première réponse complète, telle que reçue")
    args = parser.parse_args()

    print(f"API testée : {args.url}")
    try:
        with httpx.Client(base_url=args.url, timeout=120.0) as client:
            tester_health(client)
            tester_ask(client)
            tester_validation(client)
            tester_rebuild(client)
            if args.json:
                print("\n--- Réponse brute (POST /ask) ---")
                body = client.post("/ask", json={"question": QUESTIONS[0]}).json()
                print(json.dumps(body, ensure_ascii=False, indent=2))
    except httpx.ConnectError:
        print(f"\nAucun serveur sur {args.url}. Lancez d'abord : python scripts/serve.py")
        sys.exit(1)

    reussis = sum(resultats)
    print(f"\n===== {reussis} / {len(resultats)} vérifications réussies =====")
    sys.exit(0 if reussis == len(resultats) else 1)


if __name__ == "__main__":
    main()
