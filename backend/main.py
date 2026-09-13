import os
import json
import time
import tempfile
import re
import calendar

import cv2
import numpy as np
from datetime import date

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from sarvamai import SarvamAI
from sarvamai.core.api_error import ApiError


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv()

app = FastAPI(title="PackSure API")


# =========================================================
# CORS
# =========================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# SARVAM CLIENT
# =========================================================

api_key = os.getenv("SARVAM_API_KEY")

if not api_key:
    raise RuntimeError(
        "SARVAM_API_KEY was not found. "
        "Make sure it is present in backend/.env"
    )

client = SarvamAI(
    api_subscription_key=api_key
)


# =========================================================
# PACKSURE EXTRACTION SCHEMA
# =========================================================

PACKSURE_SCHEMA = {
    "type": "object",
    "properties": {

        "mrp": {
            "type": "string",
            "description": (
                "Maximum Retail Price or MRP printed on the package. "
                "Return the printed MRP value."
            )
        },

        "net_quantity": {
            "type": "string",
            "description": (
                "Net quantity, net weight, net volume or net content "
                "printed on the package."
            )
        },

        "manufacturer": {
            "type": "string",
            "description": (
                "Manufacturer, packer or importer name and address "
                "printed on the package."
            )
        },

        "batch_number": {
            "type": "string",
            "description": (
                "Batch number, lot number or batch code printed "
                "on the package."
            )
        },

        "manufacturing_date": {
            "type": "string",
            "description": (
                "Manufacturing or packed date. Recognize variants "
                "such as MFD, MFG, MFG DATE, DATE OF MFG, "
                "DATE OF MANUFACTURE, MANUFACTURED ON, PKD, "
                "PACKED ON and PACKED DATE. Dates may be in "
                "DD/MM/YYYY, MM/YYYY, MM/YY, YYYY-MM-DD or "
                "month-year formats."
            )
        },

        "expiry_or_best_before": {
            "type": "string",
            "description": (
                "Expiry, use-by, best-before or shelf-life declaration. "
                "Recognize EXP, EXP DATE, EXPIRY, USE BY, USE BEFORE, "
                "BEST BEFORE, CONSUME BEFORE, VALID FOR, USE WITHIN "
                "and SHELF LIFE. It may be an exact date such as "
                "04/28 or 04/2028, or a duration such as 36 MONTHS."
            )
        },

        "consumer_care": {
            "type": "string",
            "description": (
                "Consumer contact information. Recognize Customer Care, "
                "Consumer Care, Customer Service, Helpline, Toll Free, "
                "Contact Us, queries, phone numbers, email addresses "
                "or other contact information."
            )
        }
    }
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def is_detected(value):

    if value is None:
        return False

    if isinstance(value, (int, float, bool)):
        return True

    cleaned = str(value).strip().lower()

    if cleaned == "":
        return False

    if cleaned in {
        "not detected",
        "not_detected",
        "null",
        "none",
        "n/a",
        "na",
        "unknown",
        "not available",
        "not applicable"
    }:
        return False

    return True


def clean_text(value):

    if not is_detected(value):
        return None

    text = str(value).strip()

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", " ", text)

    return text.strip()


# =========================================================
# NORMALIZE EXTRACTED DATA
# =========================================================

def normalize_extracted_data(data):

    if not isinstance(data, dict):
        return {}

    aliases = {

        "mrp": [
            "mrp",
            "maximum_retail_price",
            "maximum retail price"
        ],

        "net_quantity": [
            "net_quantity",
            "net quantity",
            "net_weight",
            "net weight",
            "net_volume",
            "net volume",
            "net_content",
            "net content"
        ],

        "manufacturer": [
            "manufacturer",
            "manufacturer_details",
            "manufacturer details",
            "packer",
            "importer"
        ],

        "batch_number": [
            "batch_number",
            "batch number",
            "batch",
            "lot_number",
            "lot number",
            "lot"
        ],

        "manufacturing_date": [
            "manufacturing_date",
            "manufacturing date",
            "mfd",
            "mfg",
            "mfg_date",
            "mfg date",
            "packed_date",
            "packed date",
            "pkd",
            "packed_on",
            "packed on"
        ],

        "expiry_or_best_before": [
            "expiry_or_best_before",
            "expiry or best before",
            "expiry",
            "expiry_date",
            "expiry date",
            "best_before",
            "best before",
            "use_by",
            "use by",
            "shelf_life",
            "shelf life"
        ],

        "consumer_care": [
            "consumer_care",
            "consumer care",
            "customer_care",
            "customer care",
            "customer_service",
            "customer service",
            "helpline",
            "contact_us",
            "contact us"
        ]
    }

    lowered = {
        str(k).strip().lower(): v
        for k, v in data.items()
    }

    result = {}

    for canonical, possible_names in aliases.items():

        value = None

        for name in possible_names:

            if name.lower() in lowered:
                value = lowered[name.lower()]
                break

        result[canonical] = clean_text(value)

    return result


# =========================================================
# MRP CHECK
# =========================================================

def check_mrp(value):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": "MRP declaration was not detected."
        }

    text = str(value)

    if re.search(
        r"(₹|rs\.?|inr)?\s*\d+(?:[.,]\d{1,2})?",
        text,
        re.IGNORECASE
    ):

        return {
            "status": "PASS",
            "message": "MRP declaration detected."
        }

    return {
        "status": "REVIEW",
        "message": "MRP was detected but its value needs review."
    }


# =========================================================
# NET QUANTITY CHECK
# =========================================================

def check_net_quantity(value):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": "Net quantity declaration was not detected."
        }

    text = str(value)

    pattern = (
        r"\d+(?:\.\d+)?\s*"
        r"(mg|g|kg|mcg|ml|l|litre|liter|litres|liters|"
        r"lb|lbs|oz|ounce|ounces)"
    )

    if re.search(
        pattern,
        text,
        re.IGNORECASE
    ):

        return {
            "status": "PASS",
            "message": "Net quantity declaration detected."
        }

    return {
        "status": "REVIEW",
        "message": "Net quantity was detected but its format needs review."
    }


# =========================================================
# MANUFACTURER CHECK
# =========================================================

def check_manufacturer(value):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": "Manufacturer details were not detected."
        }

    if len(str(value).strip()) < 3:

        return {
            "status": "REVIEW",
            "message": "Manufacturer information appears incomplete."
        }

    return {
        "status": "PASS",
        "message": "Manufacturer information detected."
    }


# =========================================================
# BATCH CHECK
# =========================================================

def check_batch(value):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": "Batch or lot number was not detected."
        }

    return {
        "status": "PASS",
        "message": "Batch or lot number detected."
    }


# =========================================================
# DATE HELPERS
# =========================================================

MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def normalize_year(year):

    year = int(year)

    if year < 100:
        return 2000 + year

    return year


def is_month_year_format(value):

    if not is_detected(value):
        return False

    text = str(value).strip().upper()

    # MM/YY or MM/YYYY
    if re.search(
        r"\b(0?[1-9]|1[0-2])[\/\-.](\d{2}|\d{4})\b",
        text
    ):
        return True

    # APR-26 / APR 2026
    if re.search(
        r"\b("
        r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|"
        r"SEP|OCT|NOV|DEC"
        r")[ \-/]*(\d{2}|\d{4})\b",
        text
    ):
        return True

    return False


def parse_date_value(value):

    if not is_detected(value):
        return None

    text = str(value).strip().upper()

    # Remove common labels
    text = re.sub(
        r"\b("
        r"MFD|MFG|MANUFACTURED|MANUFACTURING DATE|"
        r"PKD|PACKED ON|PACKED|DATE OF MFG|"
        r"DATE OF MANUFACTURE|EXP|EXPIRY|"
        r"EXP DATE|BEST BEFORE|USE BY|USE BEFORE"
        r")\b[:\s-]*",
        "",
        text
    ).strip()

    # -----------------------------------------------------
    # YYYY-MM-DD
    # -----------------------------------------------------

    match = re.search(
        r"\b(20\d{2}|19\d{2})[\/\-.]"
        r"(\d{1,2})[\/\-.](\d{1,2})\b",
        text
    )

    if match:

        try:

            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3))
            )

        except ValueError:
            pass

    # -----------------------------------------------------
    # DD/MM/YYYY
    # -----------------------------------------------------

    match = re.search(
        r"\b(\d{1,2})[\/\-.]"
        r"(\d{1,2})[\/\-.]"
        r"(\d{2,4})\b",
        text
    )

    if match:

        try:

            day = int(match.group(1))
            month = int(match.group(2))
            year = normalize_year(match.group(3))

            return date(
                year,
                month,
                day
            )

        except ValueError:
            pass

    # -----------------------------------------------------
    # MM/YY or MM/YYYY
    # -----------------------------------------------------

    match = re.search(
        r"\b(0?[1-9]|1[0-2])[\/\-.]"
        r"(\d{2}|\d{4})\b",
        text
    )

    if match:

        try:

            month = int(match.group(1))
            year = normalize_year(match.group(2))

            # Day is internally set to 1.
            # We will NOT display this invented day.
            return date(
                year,
                month,
                1
            )

        except ValueError:
            pass

    # -----------------------------------------------------
    # MONTH NAME + YEAR
    # -----------------------------------------------------

    match = re.search(
        r"\b("
        r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|"
        r"SEP|OCT|NOV|DEC"
        r")[ \-/]*(\d{2}|\d{4})\b",
        text
    )

    if match:

        try:

            month = MONTH_MAP[
                match.group(1)
            ]

            year = normalize_year(
                match.group(2)
            )

            return date(
                year,
                month,
                1
            )

        except ValueError:
            pass

    return None


# =========================================================
# DATE FORMATTING
# =========================================================

def format_date(
    value,
    original_text=None
):

    if value is None:
        return None

    # Preserve month/year precision
    # when the package only gave month/year.
    if original_text and is_month_year_format(
        original_text
    ):

        text = str(
            original_text
        ).strip().upper()

        # MM/YY or MM/YYYY
        match = re.search(
            r"\b(0?[1-9]|1[0-2])[\/\-.]"
            r"(\d{2}|\d{4})\b",
            text
        )

        if match:

            month = int(
                match.group(1)
            )

            year = normalize_year(
                match.group(2)
            )

            return f"{month:02d}/{year}"

        # Month name + year
        match = re.search(
            r"\b("
            r"JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|"
            r"SEP|OCT|NOV|DEC"
            r")[ \-/]*(\d{2}|\d{4})\b",
            text
        )

        if match:

            month = MONTH_MAP[
                match.group(1)
            ]

            year = normalize_year(
                match.group(2)
            )

            return f"{month:02d}/{year}"

    # Full date
    return value.strftime(
        "%d-%m-%Y"
    )


# =========================================================
# MANUFACTURING DATE CHECK
# =========================================================

def check_manufacturing_date(value):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": "Manufacturing or packed date was not detected."
        }

    parsed = parse_date_value(
        value
    )

    if parsed is not None:

        return {
            "status": "PASS",
            "message": "Manufacturing or packed date detected."
        }

    return {
        "status": "REVIEW",
        "message": (
            "Manufacturing date was detected but "
            "its format needs review."
        )
    }


# =========================================================
# SHELF LIFE
# =========================================================

def extract_shelf_life_months(value):

    if not is_detected(value):
        return None

    text = str(value).upper()

    match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*"
        r"(MONTHS?|MTHS?|MOS?|M)\b",
        text,
        re.IGNORECASE
    )

    if match:

        try:

            return int(
                round(
                    float(
                        match.group(1)
                    )
                )
            )

        except ValueError:

            return None

    return None


def add_months(
    original_date,
    months
):

    if original_date is None:
        return None

    month_index = (
        original_date.month
        - 1
        + months
    )

    year = (
        original_date.year
        + month_index // 12
    )

    month = (
        month_index % 12
    ) + 1

    day = min(
        original_date.day,
        calendar.monthrange(
            year,
            month
        )[1]
    )

    return date(
        year,
        month,
        day
    )


# =========================================================
# EXPIRY INTERPRETATION
# =========================================================

def interpret_expiry(
    expiry_value,
    manufacturing_value
):

    result = {

        "type": None,

        "printed_value": None,

        "shelf_life_months": None,

        "manufacturing_date_used": None,

        "calculated_expiry": None,

        "status": "REVIEW",

        "message": ""
    }

    # -----------------------------------------------------
    # NOTHING DETECTED
    # -----------------------------------------------------

    if not is_detected(
        expiry_value
    ):

        result["message"] = (
            "No expiry / best-before declaration was extracted. "
            "Applicability requires review."
        )

        return result

    expiry_text = str(
        expiry_value
    ).strip()

    result["printed_value"] = (
        expiry_text
    )

    # -----------------------------------------------------
    # SHELF LIFE
    # Example:
    # 36 MONTHS
    # SHELF LIFE 24 MONTHS
    # -----------------------------------------------------

    shelf_life = extract_shelf_life_months(
        expiry_text
    )

    if shelf_life is not None:

        result["type"] = (
            "SHELF_LIFE"
        )

        result["shelf_life_months"] = (
            shelf_life
        )

        mfg_date = parse_date_value(
            manufacturing_value
        )

        if mfg_date is None:

            result["message"] = (
                f"Shelf life of {shelf_life} months detected, "
                "but manufacturing date is required."
            )

            return result

        calculated = add_months(
            mfg_date,
            shelf_life
        )

        result["manufacturing_date_used"] = (
            format_date(
                mfg_date,
                manufacturing_value
            )
        )

        # If MFD was MM/YY, don't invent a day
        if is_month_year_format(
            manufacturing_value
        ):

            result["calculated_expiry"] = (
                calculated.strftime(
                    "%m/%Y"
                )
            )

        else:

            result["calculated_expiry"] = (
                format_date(
                    calculated
                )
            )

        result["status"] = (
            "PASS"
        )

        result["message"] = (
            f"Shelf life of {shelf_life} months detected. "
            f"Calculated end date: "
            f"{result['calculated_expiry']}."
        )

        return result

    # -----------------------------------------------------
    # EXPLICIT DATE
    # Example:
    # 04/28
    # 04/2028
    # 15/04/2028
    # -----------------------------------------------------

    explicit_date = parse_date_value(
        expiry_text
    )

    if explicit_date is not None:

        result["type"] = (
            "EXPLICIT_DATE"
        )

        result["calculated_expiry"] = (
            format_date(
                explicit_date,
                expiry_text
            )
        )

        result["status"] = (
            "PASS"
        )

        result["message"] = (
            "Expiry / best-before date detected."
        )

        return result

    # -----------------------------------------------------
    # PRESENT BUT UNKNOWN FORMAT
    # -----------------------------------------------------

    result["type"] = (
        "DECLARATION"
    )

    result["message"] = (
        "Expiry / best-before information was detected, "
        "but its date format needs review."
    )

    return result


# =========================================================
# EXPIRY CHECK
# =========================================================

def check_expiry(
    value,
    manufacturing_date
):

    return interpret_expiry(
        value,
        manufacturing_date
    )


# =========================================================
# CONSUMER CARE CHECK
# =========================================================

def check_consumer_care(
    value
):

    if not is_detected(value):

        return {
            "status": "REVIEW",
            "message": (
                "Consumer care information was not detected."
            )
        }

    text = str(
        value
    ).strip()

    # Email
    email_pattern = (
        r"[A-Z0-9._%+\-]+"
        r"@[A-Z0-9.\-]+\.[A-Z]{2,}"
    )

    # Phone / toll-free
    phone_pattern = (
        r"(?:\+91[\s\-]?)?"
        r"(?:0?\d{2,5}[\s\-]?)?"
        r"\d{6,10}"
    )

    # Different possible labels
    care_label_pattern = (
        r"customer\s*care|"
        r"consumer\s*care|"
        r"customer\s*service|"
        r"consumer\s*service|"
        r"consumer\s*advisor|"
        r"helpline|"
        r"toll[\-\s]?free|"
        r"contact\s*us|"
        r"queries|"
        r"care\s*(?:no|number)?"
    )

    has_email = re.search(
        email_pattern,
        text,
        re.IGNORECASE
    )

    has_phone = re.search(
        phone_pattern,
        text,
        re.IGNORECASE
    )

    has_label = re.search(
        care_label_pattern,
        text,
        re.IGNORECASE
    )

    if has_email or has_phone:

        return {
            "status": "PASS",
            "message": (
                "Consumer care contact information detected."
            )
        }

    if has_label and len(text) >= 8:

        return {
            "status": "PASS",
            "message": (
                "Consumer care information detected; "
                "contact format can be visually verified."
            )
        }

    return {
        "status": "REVIEW",
        "message": (
            "Consumer care information was detected, "
            "but the contact details need review."
        )
    }


# =========================================================
# CONFIDENCE HELPERS
# =========================================================

LOW_CONFIDENCE_THRESHOLD = 0.80


def to_plain_data(value):
    """
    Convert Sarvam/Pydantic annotation objects into normal
    Python dictionaries/lists so confidence values can be read safely.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return {
            key: to_plain_data(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            to_plain_data(item)
            for item in value
        ]

    if hasattr(value, "model_dump"):
        try:
            return to_plain_data(value.model_dump())
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return to_plain_data(value.dict())
        except Exception:
            pass

    return value


def get_field_confidence(annotations, field_name):
    """
    Get Sarvam's extraction confidence for one PackSure field.

    Sarvam annotations mirror the extracted result structure.
    The helper also supports Pydantic/model objects returned by the SDK.
    """
    annotations = to_plain_data(annotations)

    if not isinstance(annotations, dict):
        return None

    field_annotation = annotations.get(field_name)

    if field_annotation is None:
        return None

    if isinstance(field_annotation, dict):
        confidence = field_annotation.get("confidence")

        if confidence is not None:
            try:
                return float(confidence)
            except (TypeError, ValueError):
                return None

    return None


def confidence_percent(confidence):
    """
    Convert a 0-1 confidence score into a whole-number percentage.
    """
    if confidence is None:
        return None

    try:
        value = max(0.0, min(1.0, float(confidence)))
        return round(value * 100)
    except (TypeError, ValueError):
        return None


def add_confidence_to_check(check, confidence):
    """
    Add extraction confidence to a compliance check.

    Low-confidence PASS results are routed to REVIEW because
    PackSure should not treat uncertain OCR extraction as a final pass.
    """
    check = dict(check)

    percent = confidence_percent(confidence)

    if percent is not None:
        check["confidence"] = round(float(confidence), 4)
        check["confidence_percent"] = percent

        if (
            float(confidence) < LOW_CONFIDENCE_THRESHOLD
            and check.get("status") == "PASS"
        ):
            check["status"] = "REVIEW"

            existing_message = check.get("message", "").rstrip(".")

            check["message"] = (
                f"{existing_message}. "
                f"Extraction confidence is {percent}%; "
                "please verify."
            )

    return check


# =========================================================
# VISUAL CHARACTER SIZE & READABILITY
# =========================================================

# Legal Metrology minimum character height depends on the
# principal display panel area. These are the minimum heights
# in millimetres used by the PackSure MVP rule model.
CHARACTER_HEIGHT_RULES = [
    (50, 1.0),
    (100, 1.5),
    (500, 2.5),
    (2500, 4.0),
    (float("inf"), 6.0),
]


def required_character_height_mm(panel_area_cm2):
    """Return the minimum character height for a panel area."""
    if panel_area_cm2 is None:
        return None

    try:
        area = float(panel_area_cm2)
    except (TypeError, ValueError):
        return None

    if area <= 0:
        return None

    for max_area, minimum_mm in CHARACTER_HEIGHT_RULES:
        if area <= max_area:
            return minimum_mm

    return None


def detect_text_heights_px(image_path):
    """
    Estimate character/text heights from the package image using OpenCV.

    This is intentionally an estimate. It does not claim a legal mm
    measurement unless a physical scale reference is supplied.
    """
    image = cv2.imread(image_path)

    if image is None:
        return {
            "success": False,
            "message": "Package image could not be read for visual analysis."
        }

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Local thresholding handles uneven package backgrounds better
    # than one global threshold.
    binary = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        11
    )

    # Join nearby strokes while avoiding very large blobs.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        binary,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    image_height, image_width = gray.shape[:2]
    heights = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        # Ignore tiny noise and very large package/background regions.
        if h < 4 or w < 2:
            continue
        if h > image_height * 0.15:
            continue
        if w > image_width * 0.8:
            continue

        # Character-like aspect ratio and size filter.
        if h / max(w, 1) > 12:
            continue

        heights.append(h)

    if not heights:
        return {
            "success": True,
            "image_width_px": image_width,
            "image_height_px": image_height,
            "detected_text_regions": 0,
            "median_character_height_px": None,
            "min_character_height_px": None,
            "max_character_height_px": None,
            "message": "No reliable text-like regions were detected."
        }

    # Remove extreme outliers before estimating a representative height.
    heights = np.array(heights, dtype=np.float32)
    q1, q3 = np.percentile(heights, [25, 75])
    iqr = max(q3 - q1, 1.0)
    filtered = heights[
        (heights >= q1 - 1.5 * iqr) &
        (heights <= q3 + 1.5 * iqr)
    ]

    if len(filtered) == 0:
        filtered = heights

    return {
        "success": True,
        "image_width_px": image_width,
        "image_height_px": image_height,
        "detected_text_regions": int(len(filtered)),
        "median_character_height_px": round(float(np.median(filtered)), 2),
        "min_character_height_px": round(float(np.min(filtered)), 2),
        "max_character_height_px": round(float(np.max(filtered)), 2),
        "message": "Text-like regions detected for visual size analysis."
    }


def analyze_character_size(
    image_path,
    reference_width_mm=None,
    display_panel_area_cm2=None
):
    """
    Analyze character size and readability.

    Without a known physical reference and principal display panel area,
    PackSure reports the pixel measurement but deliberately does not claim
    a legal pass/fail verdict.
    """
    visual = detect_text_heights_px(image_path)

    result = {
        "status": "REVIEW",
        "message": "Requires visual verification of character size and readability.",
        "calibration": {
            "reference_width_mm": None,
            "reference_width_px": None,
            "pixels_per_mm": None
        },
        "rule": {
            "display_panel_area_cm2": None,
            "minimum_character_height_mm": None
        },
        "measurement": visual
    }

    if not visual.get("success"):
        result["message"] = visual.get(
            "message",
            "Visual character analysis could not be completed."
        )
        return result

    median_px = visual.get("median_character_height_px")

    # No physical scale -> pixel measurement only.
    if reference_width_mm is None:
        result["message"] = (
            f"Estimated text height: {median_px}px. "
            "Physical scale calibration is required before a legal "
            "millimetre-based size verdict can be made."
        )
        return result

    try:
        reference_width_mm = float(reference_width_mm)
    except (TypeError, ValueError):
        result["message"] = (
            "Reference size was invalid; physical scale calibration needs review."
        )
        return result

    if reference_width_mm <= 0:
        result["message"] = (
            "Reference size must be greater than zero; calibration needs review."
        )
        return result

    reference_width_px = float(visual["image_width_px"])
    pixels_per_mm = reference_width_px / reference_width_mm

    result["calibration"] = {
        "reference_width_mm": round(reference_width_mm, 2),
        "reference_width_px": round(reference_width_px, 2),
        "pixels_per_mm": round(pixels_per_mm, 4)
    }

    if median_px is None or pixels_per_mm <= 0:
        result["message"] = "Text height could not be measured reliably."
        return result

    estimated_mm = median_px / pixels_per_mm
    result["measurement"]["estimated_median_character_height_mm"] = round(
        float(estimated_mm), 3
    )

    required_mm = required_character_height_mm(display_panel_area_cm2)

    if required_mm is None:
        result["message"] = (
            f"Estimated median character height: {estimated_mm:.2f} mm. "
            "Principal display panel area is required to determine the applicable minimum."
        )
        return result

    result["rule"] = {
        "display_panel_area_cm2": round(float(display_panel_area_cm2), 2),
        "minimum_character_height_mm": required_mm
    }

    if estimated_mm >= required_mm:
        result["status"] = "PASS"
        result["message"] = (
            f"Estimated character height {estimated_mm:.2f} mm meets the "
            f"minimum {required_mm:.2f} mm requirement."
        )
    else:
        result["status"] = "FAIL"
        result["message"] = (
            f"Estimated character height {estimated_mm:.2f} mm is below the "
            f"minimum {required_mm:.2f} mm requirement."
        )

    return result


# =========================================================
# COMPLIANCE RULE ENGINE
# =========================================================

def run_compliance_check(
    data,
    annotations=None,
    character_analysis=None
):
    data = normalize_extracted_data(data)

    expiry_result = check_expiry(
        data.get("expiry_or_best_before"),
        data.get("manufacturing_date")
    )

    checks = {
        "mrp_declaration": check_mrp(
            data.get("mrp")
        ),

        "net_quantity": check_net_quantity(
            data.get("net_quantity")
        ),

        "manufacturer_details": check_manufacturer(
            data.get("manufacturer")
        ),

        "batch_number": check_batch(
            data.get("batch_number")
        ),

        "manufacturing_date": check_manufacturing_date(
            data.get("manufacturing_date")
        ),

        "expiry_best_before": {
            "status": expiry_result["status"],
            "message": expiry_result["message"]
        },

        "consumer_care": check_consumer_care(
            data.get("consumer_care")
        ),

        "character_size_readability": character_analysis or {
            "status": "REVIEW",
            "message": (
                "Requires visual verification of character "
                "size and readability."
            )
        }
    }

    # ---------------------------------------------------------
    # SARVAM EXTRACTION CONFIDENCE
    # ---------------------------------------------------------

    confidence_fields = {
        "mrp_declaration": "mrp",
        "net_quantity": "net_quantity",
        "manufacturer_details": "manufacturer",
        "batch_number": "batch_number",
        "manufacturing_date": "manufacturing_date",
        "expiry_best_before": "expiry_or_best_before",
        "consumer_care": "consumer_care"
    }

    confidences = {}

    for check_name, field_name in confidence_fields.items():
        confidence = get_field_confidence(
            annotations,
            field_name
        )

        confidences[check_name] = confidence

        checks[check_name] = add_confidence_to_check(
            checks[check_name],
            confidence
        )

    # Character size/readability is a visual measurement check,
    # not an OCR extraction field, so it has no OCR confidence.
    confidences["character_size_readability"] = None

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------

    passed = sum(
        1
        for check in checks.values()
        if check["status"] == "PASS"
    )

    failed = sum(
        1
        for check in checks.values()
        if check["status"] == "FAIL"
    )

    review = sum(
        1
        for check in checks.values()
        if check["status"] == "REVIEW"
    )

    # ---------------------------------------------------------
    # OVERALL RESULT
    # ---------------------------------------------------------

    if failed > 0:
        overall_status = "NON-COMPLIANT"

        overall_message = (
            f"{failed} compliance issue"
            f"{'s' if failed != 1 else ''} found; "
            f"{passed} check"
            f"{'s' if passed != 1 else ''} passed."
        )

    elif review > 0:
        overall_status = "NEEDS REVIEW"

        overall_message = (
            f"{passed} declaration"
            f"{'s' if passed != 1 else ''} passed; "
            f"{review} item"
            f"{'s' if review != 1 else ''} "
            f"require"
            f"{'' if review != 1 else 's'} "
            "visual verification."
        )

    else:
        overall_status = "COMPLIANT"

        overall_message = (
            f"All {passed} compliance checks passed."
        )

    return {
        "overall_status": overall_status,
        "overall_message": overall_message,

        "summary": {
            "passed": passed,
            "failed": failed,
            "review": review
        },

        "checks": checks,

        "confidence": confidences,

        "expiry_interpretation": expiry_result
    }


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return {

        "message": "PackSure API is running",

        "status": "ready"
    }


# =========================================================
# INSPECT PACKAGE
# =========================================================

@app.post("/inspect")
async def inspect_package(
    files: list[UploadFile] = File(...),
    reference_width_mm: float | None = Form(None),
    display_panel_area_cm2: float | None = Form(None)
):
    """
    Inspect one or two package images.

    Camera workflow:
    - Image 1: full package view
    - Optional Image 2: close-up/detail view for small text

    Each image is sent to Sarvam separately and the extracted fields are
    merged. For each field, the result with the highest extraction
    confidence is retained. This avoids forcing the laptop webcam to fit
    the entire package and every tiny declaration into one frame.
    """

    if not files:
        return {
            "success": False,
            "message": "Please capture or upload at least one package image."
        }

    if len(files) > 2:
        return {
            "success": False,
            "message": "PackSure supports a maximum of 2 images per inspection."
        }

    for uploaded_file in files:
        if (
            not uploaded_file.content_type
            or not uploaded_file.content_type.startswith("image/")
        ):
            return {
                "success": False,
                "message": "Please upload image files only."
            }

    temp_paths = []
    extracted_candidates = []
    annotation_candidates = []
    visual_candidates = []
    job_ids = []

    try:
        # =================================================
        # SAVE ALL IMAGES
        # =================================================

        for uploaded_file in files:
            suffix = (
                os.path.splitext(uploaded_file.filename or "")[1]
                or ".jpg"
            )

            with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
            ) as temp_file:
                image_bytes = await uploaded_file.read()
                temp_file.write(image_bytes)
                temp_paths.append(temp_file.name)

        # =================================================
        # PROCESS EACH IMAGE
        # =================================================

        terminal_statuses = {
            "completed",
            "partially_completed",
            "failed",
            "rejected"
        }

        for index, (uploaded_file, temp_path) in enumerate(
            zip(files, temp_paths),
            start=1
        ):
            # -------------------------------------------------
            # SARVAM EXTRACTION
            # -------------------------------------------------

            with open(temp_path, "rb") as image_file:
                job = client.doc_ai.extract(
                    file=[
                        (
                            uploaded_file.filename or f"package-{index}.jpg",
                            image_file,
                            uploaded_file.content_type
                        )
                    ],
                    schema=json.dumps(PACKSURE_SCHEMA),
                    language="en-IN",
                    output_format="json"
                )

            job_ids.append(job.job_id)

            # -------------------------------------------------
            # WAIT FOR SARVAM
            # -------------------------------------------------

            max_wait_seconds = 120
            waited = 0

            while True:
                status = client.doc_ai.get_status(
                    job_id=job.job_id
                )

                current_status = status.status.lower()

                if current_status in terminal_statuses:
                    break

                if waited >= max_wait_seconds:
                    return {
                        "success": False,
                        "message": (
                            f"Sarvam extraction timed out for image {index}. "
                            "Please try again."
                        )
                    }

                time.sleep(2)
                waited += 2

            if current_status not in {
                "completed",
                "partially_completed"
            }:
                return {
                    "success": False,
                    "message": (
                        f"Sarvam extraction failed for image {index}: "
                        f"{current_status}"
                    )
                }

            # -------------------------------------------------
            # GET RESULTS
            # -------------------------------------------------

            result = client.doc_ai.get_results(
                job_id=job.job_id
            )

            extracted_candidates.append(
                normalize_extracted_data(result.result)
            )

            annotation_candidates.append(
                to_plain_data(result.annotations)
            )

            # -------------------------------------------------
            # VISUAL CHARACTER ANALYSIS
            # -------------------------------------------------

            visual_candidates.append(
                analyze_character_size(
                    temp_path,
                    reference_width_mm=reference_width_mm,
                    display_panel_area_cm2=display_panel_area_cm2
                )
            )

        # =================================================
        # MERGE EXTRACTION RESULTS
        # =================================================

        field_names = [
            "mrp",
            "net_quantity",
            "manufacturer",
            "batch_number",
            "manufacturing_date",
            "expiry_or_best_before",
            "consumer_care"
        ]

        merged_data = {}
        merged_annotations = {}
        selected_sources = {}

        for field_name in field_names:
            candidates = []

            for index, extracted in enumerate(extracted_candidates):
                value = extracted.get(field_name)
                annotation = (
                    annotation_candidates[index]
                    if index < len(annotation_candidates)
                    else None
                )

                confidence = get_field_confidence(
                    annotation,
                    field_name
                )

                if is_detected(value):
                    candidates.append({
                        "value": value,
                        "confidence": confidence,
                        "image_index": index + 1
                    })

            if not candidates:
                merged_data[field_name] = None
                continue

            # Prefer the candidate with the highest confidence.
            # If confidence is unavailable, keep the first detected value.
            candidates.sort(
                key=lambda item: (
                    item["confidence"] is not None,
                    item["confidence"] if item["confidence"] is not None else 0.0
                ),
                reverse=True
            )

            selected = candidates[0]
            merged_data[field_name] = selected["value"]
            selected_sources[field_name] = selected["image_index"]

            # Keep the selected field annotation in a plain JSON structure.
            selected_annotation = None
            for annotation in annotation_candidates:
                if isinstance(annotation, dict) and field_name in annotation:
                    candidate_annotation = annotation[field_name]
                    candidate_confidence = None
                    if isinstance(candidate_annotation, dict):
                        candidate_confidence = candidate_annotation.get("confidence")
                    if (
                        selected["confidence"] is not None
                        and candidate_confidence is not None
                        and float(candidate_confidence) == float(selected["confidence"])
                    ):
                        selected_annotation = candidate_annotation
                        break

            if selected_annotation is not None:
                merged_annotations[field_name] = selected_annotation

        # =================================================
        # CHOOSE VISUAL ANALYSIS
        # =================================================

        successful_visuals = [
            item for item in visual_candidates
            if isinstance(item, dict) and item.get("success")
        ]

        if successful_visuals:
            # Prefer the image that detected the most text regions.
            character_analysis = max(
                successful_visuals,
                key=lambda item: item.get("detected_text_regions", 0)
            )
        else:
            character_analysis = {
                "status": "REVIEW",
                "message": "Visual character analysis could not be completed."
            }

        # =================================================
        # COMPLIANCE
        # =================================================

        compliance = run_compliance_check(
            merged_data,
            merged_annotations,
            character_analysis
        )

        # =================================================
        # FINAL RESPONSE
        # =================================================

        return {
            "success": True,
            "status": "completed",
            "data": {
                "extracted": merged_data,
                "compliance": compliance,
                "annotations": merged_annotations,
                "job_id": job_ids[-1] if job_ids else None,
                "image_count": len(files),
                "selected_field_sources": selected_sources,
                "visual_analyses": visual_candidates
            }
        }

    except ApiError as error:
        return {
            "success": False,
            "message": "Sarvam API error",
            "details": str(error)
        }

    except Exception as error:
        return {
            "success": False,
            "message": "Unexpected server error",
            "details": str(error)
        }

    finally:
        for temp_path in temp_paths:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)