from app.resource_paths import resource_path


APP_VERSION = resource_path("VERSION.txt").read_text(encoding="utf-8-sig").strip()
