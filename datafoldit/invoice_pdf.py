from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import db
from .transaction_import import extract_docx_text, extract_xlsx_text


MONEY_RE = re.compile(r"\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})|[0-9]+(?:\.[0-9]{2}))")
INVOICE_RE = re.compile(r"\b(INV[-\s]?(?=[A-Z0-9-]*\d)[A-Z0-9-]{3,}|INV[0-9]{4,})\b", re.I)
LABELED_INVOICE_RE = re.compile(r"\binvoice\s*(?:number|#|no\.?)?\s*[:#]\s*([A-Z0-9-]{4,})\b", re.I)
DATE_RE = re.compile(
    r"\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b"
    r"|\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)


def extract_invoice_from_pdf(pdf_path: str | Path) -> dict[str, Any]:
    path = Path(pdf_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)
    text = extract_text(path)
    fields = parse_invoice_text(text)
    fields["source_pdf"] = str(path)
    fields["raw_text_excerpt"] = text[:3000]
    return fields


def extract_invoices_from_file(pdf_path: str | Path) -> list[dict[str, Any]]:
    """Read every PDF page; preserve page failures instead of silently losing invoices."""
    path = Path(pdf_path).expanduser()
    with path.open("rb") as source:
        is_pdf = source.read(5) == b"%PDF-"
    if not is_pdf:
        return [extract_invoice_from_pdf(path)]
    info_tool = find_tool("pdfinfo")
    if not info_tool:
        raise RuntimeError("Install Poppler to import all pages of a PDF safely.")
    info = subprocess.run([info_tool, str(path)], check=True, capture_output=True, text=True, timeout=20).stdout
    match = re.search(r"^Pages:\s+(\d+)", info, re.M)
    if not match:
        raise ValueError("Could not determine PDF page count.")
    count = int(match[1])
    if count > 100:
        raise ValueError("Upload PDFs with at most 100 pages per file.")
    pages = []
    text_tool = find_tool("pdftotext")
    if text_tool:
        try:
            text = subprocess.run([text_tool, "-layout", str(path), "-"], check=True, capture_output=True, text=True, timeout=30).stdout
            pages = text.split("\f")
        except (subprocess.SubprocessError, OSError):
            pass
    page_texts = []
    for index in range(count):
        text = pages[index] if index < len(pages) else ""
        if not useful_text(text):
            text = extract_text_with_pdftoppm_ocr(path, page_number=index + 1)
        page_texts.append(text)
    return invoice_rows_from_pages(page_texts, path)


def invoice_rows_from_pages(pages: list[str], path: Path) -> list[dict[str, Any]]:
    groups = []
    for index, text in enumerate(pages, 1):
        number = extract_invoice_number(normalize_text(text)) if useful_text(text) else None
        if number and groups and groups[-1]["number"] == number:
            groups[-1]["text"] += "\n" + text
            groups[-1]["pages"].append(index)
        else:
            groups.append({"number": number, "text": text, "pages": [index]})
    rows = []
    for group in groups:
        row = parse_invoice_text(group["text"]) if useful_text(group["text"]) else {}
        row.update(source_pdf=str(path), source_pages=group["pages"], raw_text_excerpt=group["text"][:3000])
        if not useful_text(group["text"]):
            row.update(_ok=False, error="No readable invoice text on this page. Upload a clearer copy.")
        elif not group["number"]:
            row["_warning"] = "Invoice number not detected. This may be a continuation page; review before selecting."
        rows.append(row)
    return rows


def extract_text(pdf_path: Path) -> str:
    text = extract_structured_file_text(pdf_path)
    if useful_text(text):
        return text
    text = extract_text_with_image_ocr(pdf_path) if is_image_file(pdf_path) else ""
    if useful_text(text):
        return text
    text = extract_text_with_pdftoppm_ocr(pdf_path)
    if not useful_text(text):
        text = extract_text_with_quicklook_ocr(pdf_path)
    if not useful_text(text):
        raise RuntimeError(
            "Could not extract readable invoice text. Install tesseract plus poppler-utils, or use macOS Quick Look locally."
        )
    return text


def extract_structured_file_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".xlsx", ".xlsm"}:
            return extract_xlsx_text(path)
        if suffix == ".docx":
            return extract_docx_text(path)
        if suffix in {".txt", ".csv", ".tsv"}:
            return path.read_text(errors="replace")
    except Exception:
        return ""
    return ""


def extract_text_with_pdftoppm_ocr(pdf_path: Path, page_number: int = 1) -> str:
    pdftoppm = find_tool("pdftoppm")
    tesseract = find_tool("tesseract")
    if not pdftoppm or not tesseract:
        return ""
    with tempfile.TemporaryDirectory(prefix="datafoldit-invoice-ocr-") as tmp:
        image_prefix = Path(tmp) / "page"
        try:
            subprocess.run(
                [pdftoppm, "-f", str(page_number), "-l", str(page_number), "-png", "-singlefile", "-r", "220", str(pdf_path), str(image_prefix)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            result = subprocess.run(
                [tesseract, str(image_prefix.with_suffix(".png")), "stdout"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=25,
            )
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""


def extract_text_with_quicklook_ocr(pdf_path: Path) -> str:
    qlmanage = find_tool("qlmanage")
    tesseract = find_tool("tesseract")
    if not qlmanage or not tesseract:
        return ""
    with tempfile.TemporaryDirectory(prefix="datafoldit-invoice-ocr-") as tmp:
        tmp_path = Path(tmp)
        try:
            subprocess.run(
                [qlmanage, "-t", "-s", "1800", "-o", str(tmp_path), str(pdf_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            images = sorted(tmp_path.glob("*.png"))
            if not images:
                return ""
            result = subprocess.run(
                [tesseract, str(images[0]), "stdout"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=25,
            )
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""


def extract_text_with_image_ocr(image_path: Path) -> str:
    tesseract = find_tool("tesseract")
    if not tesseract:
        return ""
    try:
        result = subprocess.run(
            [tesseract, str(image_path), "stdout"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=25,
        )
        return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def find_tool(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        candidate = Path(directory) / name
        if candidate.exists():
            return str(candidate)
    return None


def is_image_file(path: Path) -> bool:
    try:
        header = path.read_bytes()[:32]
    except OSError:
        return False
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return True
    return (
        header.startswith(b"\x89PNG\r\n\x1a\n")
        or header.startswith(b"\xff\xd8\xff")
        or header.startswith((b"II*\x00", b"MM\x00*"))
        or header.startswith(b"BM")
        or (header.startswith(b"RIFF") and b"WEBP" in header[:16])
    )


def useful_text(text: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", text or "")
    return len(words) >= 5


def parse_invoice_text(text: str) -> dict[str, Any]:
    normalized = normalize_text(text)
    invoice_number = extract_invoice_number(normalized)
    dates = extract_dates(normalized)
    invoice_date = extract_labeled_date(normalized, ["Invoice Date", "Date"]) or (dates[0] if dates else None)
    due_date = extract_labeled_date(normalized, ["Due Date", "Payment Due", "Due on"])
    if not due_date and "due upon receipt" in normalized.lower():
        due_date = invoice_date
    if not due_date:
        due_date = infer_due_date(invoice_date)
    total_amount = extract_total_amount(normalized)
    extracted_balance_due = extract_balance_due(normalized)
    customer = extract_customer(normalized)
    status = "Open"
    if extracted_balance_due is not None and extracted_balance_due <= 0:
        status = "Paid"
    elif "paid" in normalized.lower() and "balance due" not in normalized.lower():
        status = "Paid"
    balance_due = extracted_balance_due if extracted_balance_due is not None else (0.0 if status == "Paid" else total_amount)
    return {
        "date": invoice_date,
        "invoice_number": invoice_number,
        "customer": customer,
        "due_date": due_date,
        "amount": total_amount,
        "status": status,
        "received": "Y" if status == "Paid" else "N",
        "balance_due": balance_due,
    }


def normalize_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def extract_invoice_number(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "invoice" in line.lower():
            labeled = LABELED_INVOICE_RE.search(line)
            if labeled:
                candidate = normalize_invoice_candidate(labeled.group(1))
                if candidate:
                    return candidate
            match = INVOICE_RE.search(line)
            if match:
                candidate = normalize_invoice_candidate(match.group(1))
                if candidate:
                    return candidate
            for following in lines[index + 1 : index + 4]:
                match = INVOICE_RE.search(following)
                if match:
                    candidate = normalize_invoice_candidate(match.group(1))
                    if candidate:
                        return candidate
    match = INVOICE_RE.search(text)
    return normalize_invoice_candidate(match.group(1)) if match else None


def normalize_invoice_candidate(value: str) -> str | None:
    candidate = value.replace(" ", "-").upper()
    return candidate if re.search(r"\d", candidate) else None


def extract_dates(text: str) -> list[str]:
    dates = []
    for match in DATE_RE.finditer(text):
        parsed = parse_date(match.group(0))
        if parsed and parsed not in dates:
            dates.append(parsed)
    return dates


def extract_labeled_date(text: str, labels: list[str]) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        for label in labels:
            if label.lower() in line.lower():
                inline = DATE_RE.search(line)
                if inline:
                    return parse_date(inline.group(0))
                if index + 1 < len(lines):
                    next_line = DATE_RE.search(lines[index + 1])
                    if next_line:
                        return parse_date(next_line.group(0))
    return None


def parse_date(value: str) -> str | None:
    value = re.sub(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+", "", value.strip(), flags=re.I)
    for fmt in (
        "%b %d, %Y",
        "%B %d, %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return db.normalize_date(value)


def infer_due_date(invoice_date: str | None) -> str | None:
    if not invoice_date:
        return None
    parsed = db.normalize_date(invoice_date)
    if not parsed:
        return None
    return (datetime.strptime(parsed, "%Y-%m-%d").date() + timedelta(days=30)).isoformat()


def extract_total_amount(text: str) -> float:
    lines = text.splitlines()
    ranked: list[float] = []
    labels = (
        "total (including",
        "amount due",
        "balance due",
        "total due",
        "invoice total",
        "total",
    )
    for line in lines:
        lower = line.lower()
        if any(label in lower for label in labels):
            amounts = [db.amount_value(match.group(1)) for match in MONEY_RE.finditer(line)]
            ranked.extend(amount for amount in amounts if amount > 0)
    if ranked:
        return ranked[-1]
    all_amounts = [db.amount_value(match.group(1)) for match in MONEY_RE.finditer(text)]
    meaningful = [amount for amount in all_amounts if amount > 0]
    return max(meaningful) if meaningful else 0.0


def extract_balance_due(text: str) -> float | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "balance due" not in line.lower():
            continue
        amounts = [db.amount_value(match.group(1)) for match in MONEY_RE.finditer(line)]
        if amounts:
            return amounts[-1]
        for following in lines[index + 1 : index + 4]:
            amounts = [db.amount_value(match.group(1)) for match in MONEY_RE.finditer(following)]
            if amounts:
                return amounts[-1]
    return None


def extract_customer(text: str) -> str | None:
    lines = text.splitlines()
    for label in ("Bill To", "Bill To Address", "Sold To", "Sold To Address", "Customer"):
        for index, line in enumerate(lines):
            if label.lower() in line.lower():
                remainder = re.sub(rf"^{re.escape(label)}\s*:?", "", line, flags=re.I).strip()
                if remainder:
                    candidate = clean_customer_candidate(remainder)
                    if looks_like_customer_name(candidate):
                        return candidate
                for following in lines[index + 1 : index + 5]:
                    if not following or "address" in following.lower() or "charge" in following.lower():
                        continue
                    following = clean_customer_candidate(following)
                    if re.search(r"@|\d{3,}|\$|payment", following, re.I):
                        continue
                    if looks_like_customer_name(following):
                        return following
    for label in ("Bill To", "Bill To Address", "Sold To", "Sold To Address", "Account Information", "Customer"):
        for index, line in enumerate(lines):
            if label.lower() in line.lower():
                for following in lines[index + 1 : index + 35]:
                    if looks_like_customer_name(following):
                        return clean_customer_candidate(following)
    for line in lines[:12]:
        if re.search(r"\b(LLC|Inc\.?|Corporation|Technologies|Systems)\b", line, re.I):
            return clean_customer_candidate(line)
    return None


def clean_customer_candidate(line: str) -> str:
    candidate = re.split(
        r"\b(?:Customer|Invoice Date|Due Date|Terms|Invoice #|Balance Due)\b\s*:?",
        line,
        maxsplit=1,
        flags=re.I,
    )[0]
    return candidate.strip(" .,:;-")


def looks_like_customer_name(line: str) -> bool:
    line = clean_customer_candidate(line)
    if not line or re.search(r"@|\$|invoice|date|subtotal|total|quantity|unit price|charge|payment|method", line, re.I):
        return False
    return bool(re.search(r"(LLC|L\.L\.C\.|Inc\.?|Corporation|Corp\.?|Technologies|Systems|Services)\b", line, re.I))
