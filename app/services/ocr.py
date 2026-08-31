import os
import shutil
import subprocess
from pathlib import Path

from app.resource_paths import resource_path
from app.services.settings import get_setting, set_setting


INSTALLATION_HELP = (
    "Tesseract OCR est indisponible. Installez une distribution Windows maîtrisée de Tesseract, "
    "puis indiquez le chemin de tesseract.exe dans Paramètres > OCR."
)
DEFAULT_LANGUAGES = "fra+eng+spa"


def _candidate_paths():
    bundled = resource_path("resources", "tesseract", "tesseract.exe")
    configured = get_setting("tesseract_cmd", "") or ""
    environment = os.getenv("TESSERACT_CMD", "").strip()
    discovered = shutil.which("tesseract") or ""
    common = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    if bundled.is_file():
        yield "bundled", bundled
    for source, value in (("interface", configured), ("environment", environment), ("path", discovered)):
        if value:
            yield source, Path(value)
    for value in common:
        if str(value):
            yield "automatic", value


def _language_configuration(installed_languages=None):
    configured = (get_setting("ocr_languages", "") or os.getenv("OCR_LANG", DEFAULT_LANGUAGES)).strip()
    requested = [part.lower() for part in configured.split("+") if part.strip()]
    requested = list(dict.fromkeys(code for code in requested if re_full_language(code)))
    if not requested:
        requested = DEFAULT_LANGUAGES.split("+")
    installed = set(installed_languages or [])
    used = [code for code in requested if not installed or code in installed]
    missing = [code for code in requested if installed and code not in installed]
    return "+".join(requested), used, missing


def re_full_language(value):
    return bool(value) and all(character.isalnum() or character in {"_", "-"} for character in value)


def _run_tesseract(command, timeout=8):
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def ocr_status():
    seen = set()
    for source, candidate in _candidate_paths():
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if not resolved.is_file():
            continue
        try:
            completed = _run_tesseract([str(resolved), "--version"], timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            version = (completed.stdout or completed.stderr or "").splitlines()[0].strip()
            languages_result = _run_tesseract([str(resolved), "--list-langs"], timeout=5)
            language_output = (languages_result.stdout or languages_result.stderr or "").splitlines()
            installed_languages = sorted(
                line.strip() for line in language_output
                if line.strip() and not line.lower().startswith("list of available languages")
            ) if languages_result.returncode == 0 else []
            configured, used, missing = _language_configuration(installed_languages)
            return {"available": True, "path": str(resolved), "source": source,
                    "version": version, "instruction": None,
                    "installed_languages": installed_languages,
                    "configured_languages": configured,
                    "used_languages": used,
                    "missing_languages": missing}
    configured, used, missing = _language_configuration([])
    return {"available": False, "path": None, "source": None, "version": None,
            "instruction": INSTALLATION_HELP, "installed_languages": [],
            "configured_languages": configured, "used_languages": used,
            "missing_languages": missing}


def configure_tesseract(path, languages=None):
    value = (path or "").strip()
    set_setting("tesseract_cmd", value)
    if languages is not None:
        requested = [part.lower() for part in languages.split("+") if part.strip()]
        requested = list(dict.fromkeys(code for code in requested if re_full_language(code)))
        set_setting("ocr_languages", "+".join(requested) or DEFAULT_LANGUAGES)
    return ocr_status()


def _text_and_confidence(image, pytesseract, language):
    data = pytesseract.image_to_data(
        image, lang=language, config="--psm 6", output_type=pytesseract.Output.DICT,
    )
    grouped = {}
    confidences = []
    count = len(data.get("text", []))
    for index in range(count):
        word = str(data["text"][index] or "").strip()
        try:
            confidence = float(data.get("conf", [])[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1
        if word and confidence >= 0:
            confidences.append(confidence)
        if not word:
            continue
        key = tuple(data.get(name, [0] * count)[index] for name in ("page_num", "block_num", "par_num", "line_num"))
        grouped.setdefault(key, []).append(word)
    text = "\n".join(" ".join(words) for words in grouped.values())
    average = round(sum(confidences) / len(confidences), 1) if confidences else None
    return text, average


def ocr_pdf(path):
    try:
        import pytesseract
        import pypdfium2 as pdfium
        from PIL import ImageEnhance, ImageOps
    except Exception as exc:
        return {"ok": False, "text": "", "confidence": None,
                "error": f"Dépendances OCR Python indisponibles: {exc}"}

    status = ocr_status()
    if not status["available"]:
        return {"ok": False, "text": "", "confidence": None, "error": status["instruction"]}
    pytesseract.pytesseract.tesseract_cmd = status["path"]
    if not status["used_languages"]:
        return {"ok": False, "text": "", "confidence": None,
                "error": "Aucune des langues OCR configurées n'est installée dans Tesseract."}
    language = "+".join(status["used_languages"])
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            texts = []
            confidences = []
            candidate_details = []
            selected_methods = []
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=300 / 72)
                    try:
                        image = bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                grayscale = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
                enhanced = ImageEnhance.Contrast(grayscale).enhance(1.15)
                candidates = (("300dpi_rgb", image), ("300dpi_grayscale_autocontrast", enhanced))
                evaluated = []
                for method, candidate in candidates:
                    candidate_text, candidate_confidence = _text_and_confidence(candidate, pytesseract, language)
                    evaluated.append({"method": method, "text": candidate_text,
                                      "confidence": candidate_confidence})
                selected = max(evaluated, key=lambda item: -1 if item["confidence"] is None else item["confidence"])
                texts.append(selected["text"])
                if selected["confidence"] is not None:
                    confidences.append(selected["confidence"])
                selected_methods.append(selected["method"])
                candidate_details.append({
                    "page": index + 1,
                    "selected": selected["method"],
                    "confidences": {item["method"]: item["confidence"] for item in evaluated},
                })
        finally:
            document.close()
        average = round(sum(confidences) / len(confidences), 1) if confidences else None
        return {"ok": True, "text": "\n".join(texts), "confidence": average,
                "tesseract_path": status["path"], "tesseract_version": status["version"],
                "languages": status["used_languages"], "configured_languages": status["configured_languages"],
                "render_dpi": 300, "preprocessing": selected_methods,
                "candidate_confidences": candidate_details,
                "binarization": "tesseract_internal", "deskew": "not_applied_no_reliable_skew_signal"}
    except Exception as exc:
        return {"ok": False, "text": "", "confidence": None,
                "error": f"Échec OCR Tesseract: {exc}"}
