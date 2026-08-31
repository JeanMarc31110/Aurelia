"""Create an immediate, consistent local backup of Aurelia's SQLite database."""

from app.db import DB_PATH
from app.local_config import ensure_local_directories, load_local_config
from app.services.local_logging import configure_local_logging
from app.services.sqlite_backups import create_sqlite_backup


def main():
    config = load_local_config()
    ensure_local_directories(config)
    configure_local_logging(config)
    backup = create_sqlite_backup(
        config.backups_dir,
        DB_PATH,
        reason="manual",
        keep_last=config.backup_retention,
    )
    print(f"Backup created: {backup}")


if __name__ == "__main__":
    main()
