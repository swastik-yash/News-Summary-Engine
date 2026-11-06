import os
import csv
import logging
from typing import List, Dict

logger = logging.getLogger("news_engine.countries")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "countries.csv")

def ensure_countries_file() -> None:
    """Ensures that the countries.csv file exists, generating it if necessary."""
    if not os.path.exists(DATA_PATH):
        logger.info(f"Countries file not found at {DATA_PATH}. Triggering World Bank ingestion...")
        from scripts.fetch_worldbank_countries import fetch_and_save
        fetch_and_save()

def load_countries() -> List[str]:
    """Returns a sorted list of all sovereign country names."""
    ensure_countries_file()
    countries = []
    with open(DATA_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("country_name", "").strip()
            if name:
                countries.append(name)
    unique_sorted = sorted(list(set(countries)))
    logger.info(f"Loaded {len(unique_sorted)} sovereign countries from {DATA_PATH}")
    return unique_sorted

def load_countries_map() -> Dict[str, str]:
    """Returns a mapping of country name to ISO code."""
    ensure_countries_file()
    mapping = {}
    with open(DATA_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("country_name", "").strip()
            code = row.get("iso_code", "").strip()
            if name:
                mapping[name] = code
    return mapping

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    c_list = load_countries()
    print("Sample:", c_list[:5])
