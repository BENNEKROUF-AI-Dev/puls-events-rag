"""Récupère, nettoie et sauvegarde les événements OpenAgenda.

Exemples :
    python scripts/fetch_data.py                      # via l'API (par défaut)
    python scripts/fetch_data.py --source csv         # via l'export CSV local
    python scripts/fetch_data.py --department Gironde --reference-date 2026-09-19

Sorties :
    data/raw/events_raw_<departement>.json   (source API uniquement : réponse brute)
    data/processed/events.jsonl              (événements propres, un par ligne)
    data/processed/cleaning_report.json      (ce que chaque étape a retiré)
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag import config  # noqa: E402
from rag.fetch_events import fetch_from_api, load_from_csv, records_to_dataframe  # noqa: E402
from rag.preprocess import clean_events, save_events  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["api", "csv"], default="api")
    parser.add_argument("--csv-path", type=Path, default=config.DEFAULT_CSV_PATH)
    parser.add_argument("--department", default=config.DEPARTMENT)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=None,
                        help="Date « aujourd'hui » (AAAA-MM-JJ). Par défaut : la date du jour.")
    return parser.parse_args()


def print_report(report: dict) -> None:
    print(f"\nDépartement : {report['departement']} | référence : {report['date_de_reference']} "
          f"| fenêtre : {report['fenetre']['debut']} -> {report['fenetre']['fin']}")
    print(f"{'Étape':<42}{'retirés':>10}{'restants':>10}")
    for step in report["etapes"]:
        print(f"{step['etape']:<42}{step['retires']:>10}{step['restants']:>10}")
    print("\nAnomalies repérées :")
    for name, count in report["anomalies"].items():
        print(f"  - {name.replace('_', ' ')} : {count}")
    print(f"\n=> {report['nombre_final']} événements propres")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    args = parse_args()
    ref_date = args.reference_date or config.reference_date()
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    if args.source == "api":
        records = fetch_from_api(args.department, ref_date)
        raw_path = config.RAW_DIR / f"events_raw_{args.department.lower()}.json"
        raw_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        logging.info("Réponse brute sauvegardée : %s", raw_path)
        raw = records_to_dataframe(records)
    else:
        raw = load_from_csv(args.csv_path, department=args.department)

    events, report = clean_events(raw, department=args.department, ref_date=ref_date)
    report["source"] = args.source

    save_events(events, config.PROCESSED_EVENTS_PATH)
    config.CLEANING_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_report(report)
    print(f"Fichiers écrits : {config.PROCESSED_EVENTS_PATH.relative_to(config.ROOT_DIR)}, "
          f"{config.CLEANING_REPORT_PATH.relative_to(config.ROOT_DIR)}")

    if events.empty:
        sys.exit("Aucun événement après nettoyage : vérifiez le département et la date de référence.")


if __name__ == "__main__":
    main()
