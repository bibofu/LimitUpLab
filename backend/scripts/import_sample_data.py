import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import get_database_path
from app.repositories import SQLiteLimitUpRepository
from app.services.sample_data import SAMPLE_EVENTS


def main() -> None:
    repository = SQLiteLimitUpRepository()
    repository.replace_events(SAMPLE_EVENTS)
    print(f"Imported {len(SAMPLE_EVENTS)} sample events into {get_database_path()}")


if __name__ == "__main__":
    main()
