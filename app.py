"""Sanjeevani Hospital Management System.

A small Flask app for a DBMS case study. Every screen reads and writes
SQLite through the SQL in this file and in schema.sql. Passwords are
hashed. Payments, admissions, and prescriptions each run in one transaction.
"""

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("HMS_DB", os.path.join(BASE_DIR, "hospital.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

HOSPITAL = "Sanjeevani Multispeciality Hospital"
REORDER_BELOW = 20
GENDERS = ("Male", "Female", "Other")
APPT_STATUSES = ("Scheduled", "In Queue", "Completed", "Cancelled")
PAY_METHODS = ("Cash", "UPI", "Card", "Net Banking")
DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
BED_CHOICES = ("Available", "Maintenance")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "sanjeevani-hms-classroom-demo"),
    SESSION_COOKIE_HTTPONLY=True,
    TEMPLATES_AUTO_RELOAD=True,
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    if os.path.exists(DB_PATH):
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = ON")
        with open(SCHEMA_PATH, encoding="utf-8") as handle:
            conn.executescript(handle.read())
        conn.close()
        assert_seed()
    except Exception:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        raise
    print(f"Created {DB_PATH} from schema.sql")


def assert_seed():
    """Fail fast if the sample rows disagree with each other."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    problems = []
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        problems.append("foreign_key_check found a broken reference")
    bad_bills = conn.execute(
        """
        SELECT b.bill_id
        FROM BILL AS b
        LEFT JOIN BILL_ITEM AS i ON i.bill_id = b.bill_id
        GROUP BY b.bill_id
        HAVING COALESCE(SUM(i.amount), 0) != b.amount
        """
    ).fetchall()
    if bad_bills:
        problems.append("a BILL.amount does not equal its BILL_ITEM rows")
    bad_status = conn.execute(
        """
        SELECT b.bill_id
        FROM BILL AS b
        LEFT JOIN PAYMENT AS p ON p.bill_id = b.bill_id
        GROUP BY b.bill_id
        HAVING (b.status = 'Paid' AND COALESCE(SUM(p.amount), 0) != b.amount)
            OR (b.status = 'Unpaid' AND COALESCE(SUM(p.amount), 0) != 0)
            OR (b.status = 'Partial' AND (
                COALESCE(SUM(p.amount), 0) <= 0
                OR COALESCE(SUM(p.amount), 0) >= b.amount
            ))
        """
    ).fetchall()
    if bad_status:
        problems.append("a BILL.status does not match its payments")
    occupied = conn.execute(
        "SELECT COUNT(*) FROM BED WHERE status = 'Occupied'"
    ).fetchone()[0]
    admitted = conn.execute(
        "SELECT COUNT(*) FROM ADMISSION WHERE status = 'Admitted'"
    ).fetchone()[0]
    if occupied != admitted:
        problems.append(f"{occupied} occupied beds but {admitted} open admissions")
    loose = conn.execute(
        """
        SELECT ad.admission_id
        FROM ADMISSION AS ad
        JOIN BED AS b ON b.bed_id = ad.bed_id
        WHERE ad.status = 'Admitted' AND b.status != 'Occupied'
        """
    ).fetchall()
    if loose:
        problems.append("an admitted patient is on a bed that is not Occupied")
    if conn.execute("SELECT COUNT(*) FROM APP_USER").fetchone()[0] != 3:
        problems.append("expected the three classroom logins")
    conn.close()
    if problems:
        raise RuntimeError("Sample data failed its checks: " + "; ".join(problems))


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 3000")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def rows(sql, args=()):
    return get_db().execute(sql, args).fetchall()


def one(sql, args=()):
    return get_db().execute(sql, args).fetchone()


@contextmanager
def transaction():
    """One SQLite transaction. A failure rolls every statement back."""
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def integrity_message(exc):
    text = str(exc).lower()
    if "appointment.doctor_id" in text and "appointment.time" in text:
        return (
            "That doctor already has a live appointment at this time. "
            "Cancelled slots can be booked again."
        )
    if "admission.patient_id" in text:
        return "This patient already has an open admission. Discharge that stay first."
    if "admission.bed_id" in text:
        return "That bed already has a patient in it."
    if "foreign key" in text:
        return (
            "A foreign key stopped this. Another table still points at the row, "
            "or the parent you chose does not exist."
        )
    if "unique" in text:
        return "That value is already used. The database rejected the duplicate."
    if "check constraint" in text:
        return "A check constraint rejected a value. Look at status, age, stock, or amount."
    return "The database rejected the change."


def load_viva_queries():
    """Read the `-- @query` blocks at the bottom of schema.sql."""
    with open(SCHEMA_PATH, encoding="utf-8") as handle:
        text = handle.read()
    queries = []
    for chunk in text.split("-- @query ")[1:]:
        lines = [line.rstrip("\r") for line in chunk.splitlines()]
        title = lines[0].strip()
        about = ""
        sql_lines = []
        for line in lines[1:]:
            if line.startswith("-- @title "):
                title = line[len("-- @title "):].strip()
            elif line.startswith("-- @about "):
                about = line[len("-- @about "):].strip()
            else:
                sql_lines.append(line)
        statement = "\n".join(sql_lines).strip()
        if statement.endswith(";"):
            statement = statement[:-1].strip()
        if statement:
            queries.append(
                {"id": lines[0].strip(), "title": title, "about": about, "sql": statement}
            )
    return queries


def find_query(query_id):
    for query in load_viva_queries():
        if query["id"] == query_id:
            return query
    raise RuntimeError(f"Missing viva query {query_id}")


# ---------------------------------------------------------------------------
# Auth and small helpers
# ---------------------------------------------------------------------------

PERMISSIONS = {
    "patients_write": {"admin", "nurse"},
    "patients_delete": {"admin"},
    "doctors_write": {"admin"},
    "departments_write": {"admin"},
    "appointments_write": {"admin", "nurse"},
    "billing_write": {"admin", "nurse"},
    "medicines_write": {"admin", "nurse"},
    "prescriptions_write": {"admin", "doctor"},
    "admissions_write": {"admin", "nurse"},
    "wards_write": {"admin"},
    "beds_write": {"admin", "nurse"},
    "history_write": {"admin", "doctor", "nurse"},
}

NAV_GROUPS = {
    "dashboard": ("dashboard",),
    "patients": ("patient_", "history_"),
    "doctors": ("doctor_", "schedule_"),
    "departments": ("department_",),
    "appointments": ("appointment_",),
    "billing": ("bill_", "payment_"),
    "pharmacy": ("medicine_", "prescription_"),
    "admissions": ("admission_", "ward_", "bed_"),
    "queries": ("viva_",),
}

ROLE_NOTE = {
    "admin": "You are signed in as the administrator. Every module is open, including delete.",
    "doctor": "You are signed in as a doctor. You can read the register, update your own appointments, and write prescriptions.",
    "nurse": "You are signed in as the ward nurse. You can register patients, book slots, manage beds, and record payments.",
}

STATUS_BADGE = {
    "Scheduled": "primary",
    "In Queue": "warning",
    "Completed": "success",
    "Cancelled": "secondary",
    "Paid": "success",
    "Partial": "warning",
    "Unpaid": "danger",
    "Admitted": "danger",
    "Discharged": "secondary",
    "Available": "success",
    "Occupied": "danger",
    "Maintenance": "secondary",
}


def can(action):
    return session.get("role") in PERMISSIONS.get(action, set())


def can_touch_appointment(doctor_id):
    role = session.get("role")
    if role in ("admin", "nurse"):
        return True
    return role == "doctor" and session.get("doctor_id") == doctor_id


def can_edit_prescription(doctor_id):
    role = session.get("role")
    if role == "admin":
        return True
    return role == "doctor" and session.get("doctor_id") == doctor_id


def can_delete_history(note):
    role = session.get("role")
    if role in ("admin", "nurse"):
        return True
    return role == "doctor" and session.get("doctor_id") == note["doctor_id"]


def nav_on(key):
    endpoint = request.endpoint or ""
    return any(endpoint.startswith(prefix) for prefix in NAV_GROUPS.get(key, ()))


def require(action):
    if can(action):
        return None
    flash("Your role cannot do that. An administrator has full access.", "danger")
    return redirect(url_for("dashboard"))


def forbid():
    flash("Your role cannot change that record.", "danger")
    return redirect(url_for("dashboard"))


def clean(value):
    return " ".join((value or "").split())


def require_text(value, label, limit, errors):
    text = clean(value)
    if not text:
        errors.append(f"{label} is required.")
    elif len(text) > limit:
        errors.append(f"{label} must be {limit} characters or fewer.")
    return text


def parse_date(value):
    text = (value or "").strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def parse_time(value):
    text = (value or "").strip()
    try:
        parsed = datetime.strptime(text[:5], "%H:%M")
    except ValueError:
        return None
    return parsed.strftime("%H:%M")


def parse_rupees(value):
    text = (value or "").strip().replace(",", "")
    if text.isdigit():
        return int(text)
    return None


def minutes(value):
    hour, minute = value.split(":")[:2]
    return int(hour) * 60 + int(minute)


def doctor_label(name):
    if not name:
        return "—"
    if name.startswith("Dr."):
        return name
    return f"Dr. {name}"


def format_inr(value):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return "₹0"
    sign = "-" if number < 0 else ""
    number = abs(number)
    text = str(number)
    if len(text) <= 3:
        grouped = text
    else:
        grouped = text[-3:]
        text = text[:-3]
        while text:
            grouped = text[-2:] + "," + grouped
            text = text[:-2]
    return f"{sign}₹{grouped}"


def pretty_date(value):
    if not value:
        return "—"
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return value


def bill_no(bill_id):
    return f"HMS-{int(bill_id):04d}"


def signed_in_name():
    name = session.get("full_name", "")
    if session.get("role") == "doctor":
        return doctor_label(name)
    return name


def greeting():
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


@app.template_filter("inr")
def inr_filter(value):
    return format_inr(value)


@app.template_filter("pretty_date")
def pretty_date_filter(value):
    return pretty_date(value)


@app.template_filter("doctor")
def doctor_filter(value):
    return doctor_label(value)


@app.template_filter("badge")
def badge_filter(value):
    return STATUS_BADGE.get(value, "secondary")


@app.template_filter("billno")
def billno_filter(value):
    return bill_no(value)


@app.template_filter("expiring")
def expiring_filter(value):
    try:
        expiry = datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    return expiry <= date.today() + timedelta(days=60)


@app.context_processor
def inject_helpers():
    return {
        "can": can,
        "can_touch_appointment": can_touch_appointment,
        "can_edit_prescription": can_edit_prescription,
        "can_delete_history": can_delete_history,
        "nav_on": nav_on,
        "hospital": HOSPITAL,
        "today": date.today().isoformat(),
        "reorder_below": REORDER_BELOW,
        "appt_statuses": APPT_STATUSES,
        "pay_methods": PAY_METHODS,
        "genders": GENDERS,
        "days": DAYS,
        "bed_choices": BED_CHOICES,
        "signed_in_name": signed_in_name,
    }


@app.before_request
def require_login():
    if request.endpoint in {"login", "static"} or request.endpoint is None:
        return None
    if "user_id" not in session:
        nxt = request.path if request.path.startswith("/") else "/"
        return redirect(url_for("login", next=nxt))
    if one("SELECT user_id FROM APP_USER WHERE user_id = ?", (session["user_id"],)) is None:
        session.clear()
        return redirect(url_for("login"))
    return None


def safe_next(value):
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return None


def all_patients():
    return rows("SELECT patient_id, name, age, gender, phone FROM PATIENT ORDER BY name")


def all_doctors():
    return rows(
        """
        SELECT d.doctor_id, d.name, d.speciality, dept.dept_name
        FROM DOCTOR AS d
        JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id
        ORDER BY d.name
        """
    )


def all_departments():
    return rows("SELECT dept_id, dept_name, location FROM DEPARTMENT ORDER BY dept_name")


def all_medicines():
    return rows(
        """
        SELECT medicine_id, name, stock_qty, unit_price
        FROM MEDICINE
        ORDER BY name
        """
    )


APPOINTMENT_FROM = """
SELECT a.appt_id, a.patient_id, a.doctor_id, a.date, a.time, a.status,
       p.name AS patient_name, p.phone AS patient_phone,
       d.name AS doctor_name,
       q.queue_no
FROM APPOINTMENT AS a
JOIN PATIENT AS p ON p.patient_id = a.patient_id
JOIN DOCTOR AS d ON d.doctor_id = a.doctor_id
LEFT JOIN (
    SELECT appt_id,
           ROW_NUMBER() OVER (PARTITION BY doctor_id, date ORDER BY time) AS queue_no
    FROM APPOINTMENT
    WHERE status != 'Cancelled'
) AS q ON q.appt_id = a.appt_id
"""


def half_hour_slots(start, end):
    try:
        cursor = minutes(start)
        stop = minutes(end)
    except (TypeError, ValueError, AttributeError):
        return []
    slots = []
    while cursor < stop:
        slots.append(f"{cursor // 60:02d}:{cursor % 60:02d}")
        cursor += 30
    return slots


def open_slots(conn, doctor_id, day, ignore_id=None):
    try:
        weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    except ValueError:
        return []
    blocks = conn.execute(
        """
        SELECT start_time, end_time
        FROM DOCTOR_SCHEDULE
        WHERE doctor_id = ? AND day_of_week = ?
        """,
        (doctor_id, weekday),
    ).fetchall()
    times = []
    for block in blocks:
        times.extend(half_hour_slots(block["start_time"], block["end_time"]))
    sql = """
        SELECT time FROM APPOINTMENT
        WHERE doctor_id = ? AND date = ? AND status != 'Cancelled'
    """
    args = [doctor_id, day]
    if ignore_id:
        sql += " AND appt_id != ?"
        args.append(ignore_id)
    booked = {row["time"] for row in conn.execute(sql, args)}
    free = []
    for slot in times:
        if slot not in booked and slot not in free:
            free.append(slot)
    return free


def schedule_overlaps(conn, doctor_id, day, start, end, ignore_id=None):
    sql = """
        SELECT schedule_id, start_time, end_time
        FROM DOCTOR_SCHEDULE
        WHERE doctor_id = ? AND day_of_week = ?
    """
    args = [doctor_id, day]
    if ignore_id:
        sql += " AND schedule_id != ?"
        args.append(ignore_id)
    start_m = minutes(start)
    end_m = minutes(end)
    for row in conn.execute(sql, args):
        if start_m < minutes(row["end_time"]) and end_m > minutes(row["start_time"]):
            return True
    return False


def bill_status(paid, amount):
    if paid <= 0:
        return "Unpaid"
    if paid >= amount:
        return "Paid"
    return "Partial"


def refresh_bill_status(conn, bill_id):
    bill = conn.execute("SELECT amount FROM BILL WHERE bill_id = ?", (bill_id,)).fetchone()
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS paid FROM PAYMENT WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()["paid"]
    if paid > bill["amount"]:
        raise ValueError(
            f"The new total is {format_inr(bill['amount'])}, but "
            f"{format_inr(paid)} is already recorded. Raise the total or remove a payment."
        )
    status = bill_status(paid, bill["amount"])
    conn.execute("UPDATE BILL SET status = ? WHERE bill_id = ?", (status, bill_id))
    return status


def record_payment(conn, bill_id, amount, pay_date, method):
    """Insert a receipt and update BILL.status. Caller owns the transaction."""
    bill = conn.execute(
        "SELECT amount FROM BILL WHERE bill_id = ?", (bill_id,)
    ).fetchone()
    if bill is None:
        raise ValueError("That bill does not exist.")
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS paid FROM PAYMENT WHERE bill_id = ?",
        (bill_id,),
    ).fetchone()["paid"]
    balance = bill["amount"] - paid
    if amount > balance:
        raise ValueError(
            f"That is more than the balance of {format_inr(balance)}."
        )
    conn.execute(
        """
        INSERT INTO PAYMENT (bill_id, amount, pay_date, method)
        VALUES (?, ?, ?, ?)
        """,
        (bill_id, amount, pay_date, method),
    )
    return refresh_bill_status(conn, bill_id)


def restore_prescription_lines(conn, prescription_id):
    existing = conn.execute(
        """
        SELECT medicine_id, quantity
        FROM PRESCRIPTION_MEDICINE
        WHERE prescription_id = ?
        """,
        (prescription_id,),
    ).fetchall()
    for line in existing:
        conn.execute(
            "UPDATE MEDICINE SET stock_qty = stock_qty + ? WHERE medicine_id = ?",
            (line["quantity"], line["medicine_id"]),
        )
    conn.execute(
        "DELETE FROM PRESCRIPTION_MEDICINE WHERE prescription_id = ?",
        (prescription_id,),
    )


def apply_prescription_lines(conn, prescription_id, lines):
    for line in lines:
        medicine = conn.execute(
            "SELECT name, stock_qty FROM MEDICINE WHERE medicine_id = ?",
            (line["medicine_id"],),
        ).fetchone()
        if medicine is None:
            raise ValueError("One of the selected medicines no longer exists.")
        if medicine["stock_qty"] < line["quantity"]:
            raise ValueError(
                f"Only {medicine['stock_qty']} units of {medicine['name']} are in stock. "
                "Nothing was saved."
            )
        conn.execute(
            """
            INSERT INTO PRESCRIPTION_MEDICINE
                (prescription_id, medicine_id, dosage, duration, quantity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                prescription_id,
                line["medicine_id"],
                line["dosage"],
                line["duration"],
                line["quantity"],
            ),
        )
        conn.execute(
            "UPDATE MEDICINE SET stock_qty = stock_qty - ? WHERE medicine_id = ?",
            (line["quantity"], line["medicine_id"]),
        )


def occupy_bed(conn, bed_id):
    bed = conn.execute(
        "SELECT bed_number, status FROM BED WHERE bed_id = ?", (bed_id,)
    ).fetchone()
    if bed is None:
        raise ValueError("Choose a bed that exists.")
    if bed["status"] != "Available":
        raise ValueError(
            f"Bed {bed['bed_number']} is {bed['status'].lower()} and cannot be assigned."
        )
    conn.execute("UPDATE BED SET status = 'Occupied' WHERE bed_id = ?", (bed_id,))


def free_bed(conn, bed_id):
    conn.execute(
        """
        UPDATE BED SET status = 'Available'
        WHERE bed_id = ? AND status = 'Occupied'
        """,
        (bed_id,),
    )


def read_medicine_lines(form):
    ids = form.getlist("medicine_id")
    dosages = form.getlist("dosage")
    durations = form.getlist("duration")
    quantities = form.getlist("quantity")
    count = max(len(ids), len(dosages), len(durations), len(quantities), 1)
    lines = []
    for index in range(count):
        lines.append(
            {
                "medicine_id": (ids[index] if index < len(ids) else "").strip(),
                "dosage": clean(dosages[index] if index < len(dosages) else ""),
                "duration": clean(durations[index] if index < len(durations) else ""),
                "quantity": (quantities[index] if index < len(quantities) else "").strip(),
            }
        )
    while len(lines) > 1 and not any(lines[-1].values()):
        lines.pop()
    return lines


def validate_medicine_lines(lines):
    errors = []
    clean_lines = []
    seen = set()
    for index, line in enumerate(lines, start=1):
        if not any(line.values()):
            continue
        if not line["medicine_id"].isdigit():
            errors.append(f"Row {index}: choose a medicine.")
            continue
        if line["medicine_id"] in seen:
            errors.append("Each medicine can appear only once. Add the quantities together.")
            continue
        seen.add(line["medicine_id"])
        if not line["dosage"] or not line["duration"]:
            errors.append(f"Row {index}: dosage and duration are required.")
        if not line["quantity"].isdigit() or int(line["quantity"]) <= 0:
            errors.append(f"Row {index}: quantity must be a positive whole number.")
            continue
        clean_lines.append(
            {
                "medicine_id": int(line["medicine_id"]),
                "dosage": line["dosage"],
                "duration": line["duration"],
                "quantity": int(line["quantity"]),
            }
        )
    if not clean_lines:
        errors.append("Add at least one medicine. Each one is a row in PRESCRIPTION_MEDICINE.")
    return clean_lines, errors


def read_bill_items(form):
    descriptions = form.getlist("description")
    amounts = form.getlist("item_amount")
    count = max(len(descriptions), len(amounts), 1)
    items = []
    for index in range(count):
        items.append(
            {
                "description": clean(descriptions[index] if index < len(descriptions) else ""),
                "amount": (amounts[index] if index < len(amounts) else "").strip(),
            }
        )
    while len(items) > 1 and not items[-1]["description"] and not items[-1]["amount"]:
        items.pop()
    return items


def validate_bill_items(items):
    errors = []
    clean_items = []
    for index, item in enumerate(items, start=1):
        if not item["description"] and not item["amount"]:
            continue
        amount = parse_rupees(item["amount"])
        if not item["description"]:
            errors.append(f"Charge {index} needs a description.")
        if amount is None or amount <= 0:
            errors.append(f"Charge {index} needs a whole-rupee amount above zero.")
        else:
            clean_items.append({"description": item["description"], "amount": amount})
    if not any(item["description"] or item["amount"] for item in items):
        errors.append("Add at least one charge. Charges are saved in BILL_ITEM.")
    elif not clean_items and not errors:
        errors.append("Add at least one charge. Charges are saved in BILL_ITEM.")
    return clean_items, errors


def blank_medicine_line():
    return {"medicine_id": "", "dosage": "", "duration": "", "quantity": ""}


def blank_bill_item():
    return {"description": "", "amount": ""}


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = clean(request.form.get("username")).lower()
        password = request.form.get("password") or ""
        user = one("SELECT * FROM APP_USER WHERE username = ?", (username,))
        if user is None or not check_password_hash(user["password_hash"], password):
            error = "Those credentials were not recognised."
        else:
            session.clear()
            session["user_id"] = user["user_id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            session["doctor_id"] = user["doctor_id"]
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
    return render_template("login.html", error=error, page_title="Sign in")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    today = date.today().isoformat()
    patient_count = one("SELECT COUNT(*) AS n FROM PATIENT")["n"]
    admitted = one(
        "SELECT COUNT(*) AS n FROM ADMISSION WHERE status = 'Admitted'"
    )["n"]
    appointment_count = one("SELECT COUNT(*) AS n FROM APPOINTMENT")["n"]
    today_count = one(
        """
        SELECT COUNT(*) AS n FROM APPOINTMENT
        WHERE date = ? AND status != 'Cancelled'
        """,
        (today,),
    )["n"]
    billed = one("SELECT COALESCE(SUM(amount), 0) AS n FROM BILL")["n"]
    collected = one("SELECT COALESCE(SUM(amount), 0) AS n FROM PAYMENT")["n"]

    occupancy_query = find_query("bed_occupancy")
    money_query = find_query("monthly_accounts")
    occupancy = rows(occupancy_query["sql"])
    money = rows(money_query["sql"])
    queue_sql = APPOINTMENT_FROM + """
        WHERE a.date = ? AND a.status != 'Cancelled'
        ORDER BY a.time, d.name
    """
    queue = rows(queue_sql, (today,))
    horizon = (date.today() + timedelta(days=60)).isoformat()
    low_stock = rows(
        """
        SELECT name, category, stock_qty, unit_price, expiry_date
        FROM MEDICINE
        WHERE stock_qty < ? OR expiry_date <= ?
        ORDER BY stock_qty, expiry_date
        """,
        (REORDER_BELOW, horizon),
    )
    months = []
    for row in money:
        months.append(datetime.strptime(row["month"] + "-01", "%Y-%m-%d").strftime("%b %Y"))
    chart = {
        "wards": [row["ward_name"] for row in occupancy],
        "occupied": [int(row["occupied"]) for row in occupancy],
        "available": [int(row["available"]) for row in occupancy],
        "maintenance": [int(row["maintenance"]) for row in occupancy],
        "months": months,
        "billed": [int(row["billed"]) for row in money],
        "collected": [int(row["collected"]) for row in money],
    }
    sql_notes = [
        ("Patients", "SELECT COUNT(*) AS n FROM PATIENT;"),
        ("Admissions", "SELECT COUNT(*) AS n FROM ADMISSION WHERE status = 'Admitted';"),
        ("Appointments", "SELECT COUNT(*) AS n FROM APPOINTMENT;"),
        ("Billing", "SELECT COALESCE(SUM(amount), 0) AS n FROM BILL;"),
        ("Collected", "SELECT COALESCE(SUM(amount), 0) AS n FROM PAYMENT;"),
        ("Bed occupancy chart", occupancy_query["sql"] + ";"),
        ("Billing chart", money_query["sql"] + ";"),
    ]
    return render_template(
        "dashboard.html",
        page_title="Dashboard",
        greeting=greeting(),
        role_note=ROLE_NOTE.get(session.get("role"), ""),
        stats={
            "patients": patient_count,
            "admitted": admitted,
            "appointments": appointment_count,
            "today": today_count,
            "billed": billed,
            "collected": collected,
            "outstanding": billed - collected,
        },
        queue=queue,
        low_stock=low_stock,
        chart=chart,
        sql_notes=sql_notes,
        today_label=pretty_date(today),
    )


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------

def patient_from_form():
    errors = []
    form = {
        "name": require_text(request.form.get("name"), "Name", 80, errors),
        "age": request.form.get("age", "").strip(),
        "gender": request.form.get("gender", "").strip(),
        "phone": request.form.get("phone", "").strip(),
        "address": require_text(request.form.get("address"), "Address", 240, errors),
    }
    if not form["age"].isdigit() or not 1 <= int(form["age"]) <= 119:
        errors.append("Age must be a whole number from 1 to 119.")
    else:
        form["age"] = int(form["age"])
    if form["gender"] not in GENDERS:
        errors.append("Choose a gender.")
    if not form["phone"].isdigit() or len(form["phone"]) < 10:
        errors.append("Phone must be at least 10 digits.")
    return form, errors


def render_patient_form(form, errors, patient_id=None):
    title = "Edit patient" if patient_id else "Register patient"
    return render_template(
        "patients/form.html",
        page_title=title,
        form=form,
        errors=errors,
        patient_id=patient_id,
    )


@app.route("/patients")
def patient_list():
    term = request.args.get("q", "").strip()
    sql = "SELECT * FROM PATIENT"
    args = []
    if term:
        sql += " WHERE name LIKE ? OR phone LIKE ? OR address LIKE ?"
        like = f"%{term}%"
        args = [like, like, like]
    sql += " ORDER BY name"
    return render_template(
        "patients/list.html",
        page_title="Patients",
        patients=rows(sql, args),
        term=term,
    )


@app.route("/patients/add", methods=["GET", "POST"])
def patient_add():
    blocked = require("patients_write")
    if blocked:
        return blocked
    if request.method == "GET":
        form = {"name": "", "age": "", "gender": "", "phone": "", "address": ""}
        return render_patient_form(form, [])
    form, errors = patient_from_form()
    if errors:
        return render_patient_form(form, errors)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO PATIENT (name, age, gender, phone, address)
            VALUES (?, ?, ?, ?, ?)
            """,
            (form["name"], form["age"], form["gender"], form["phone"], form["address"]),
        )
    flash("Patient registered. One row was inserted into PATIENT.", "success")
    return redirect(url_for("patient_list"))


@app.route("/patients/<int:patient_id>")
def patient_detail(patient_id):
    patient = one("SELECT * FROM PATIENT WHERE patient_id = ?", (patient_id,))
    if patient is None:
        abort(404)
    history = rows(
        """
        SELECT h.*, d.name AS doctor_name
        FROM PATIENT_HISTORY AS h
        LEFT JOIN DOCTOR AS d ON d.doctor_id = h.doctor_id
        WHERE h.patient_id = ?
        ORDER BY h.visit_date DESC, h.history_id DESC
        """,
        (patient_id,),
    )
    appointments = rows(
        APPOINTMENT_FROM + " WHERE a.patient_id = ? ORDER BY a.date DESC, a.time DESC",
        (patient_id,),
    )
    admissions = rows(
        """
        SELECT ad.*, d.name AS doctor_name, b.bed_number, w.ward_name
        FROM ADMISSION AS ad
        JOIN DOCTOR AS d ON d.doctor_id = ad.doctor_id
        JOIN BED AS b ON b.bed_id = ad.bed_id
        JOIN WARD AS w ON w.ward_id = b.ward_id
        WHERE ad.patient_id = ?
        ORDER BY ad.admit_date DESC
        """,
        (patient_id,),
    )
    bills = rows(
        "SELECT * FROM V_BILL_BALANCE WHERE patient_id = ? ORDER BY date DESC",
        (patient_id,),
    )
    prescriptions = rows(
        """
        SELECT pr.prescription_id, pr.date, pr.diagnosis, d.name AS doctor_name,
               COUNT(pm.medicine_id) AS medicine_count
        FROM PRESCRIPTION AS pr
        JOIN DOCTOR AS d ON d.doctor_id = pr.doctor_id
        LEFT JOIN PRESCRIPTION_MEDICINE AS pm ON pm.prescription_id = pr.prescription_id
        WHERE pr.patient_id = ?
        GROUP BY pr.prescription_id
        ORDER BY pr.date DESC
        """,
        (patient_id,),
    )
    return render_template(
        "patients/detail.html",
        page_title=patient["name"],
        patient=patient,
        history=history,
        appointments=appointments,
        admissions=admissions,
        bills=bills,
        prescriptions=prescriptions,
        doctors=all_doctors(),
    )


@app.route("/patients/<int:patient_id>/edit", methods=["GET", "POST"])
def patient_edit(patient_id):
    blocked = require("patients_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM PATIENT WHERE patient_id = ?", (patient_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_patient_form(dict(current), [], patient_id)
    form, errors = patient_from_form()
    if errors:
        return render_patient_form(form, errors, patient_id)
    with transaction() as conn:
        conn.execute(
            """
            UPDATE PATIENT
            SET name = ?, age = ?, gender = ?, phone = ?, address = ?
            WHERE patient_id = ?
            """,
            (form["name"], form["age"], form["gender"], form["phone"], form["address"], patient_id),
        )
    flash("Patient updated.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.post("/patients/<int:patient_id>/delete")
def patient_delete(patient_id):
    blocked = require("patients_delete")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            cur = conn.execute("DELETE FROM PATIENT WHERE patient_id = ?", (patient_id,))
        flash("Patient deleted." if cur.rowcount else "That patient is already gone.", "success" if cur.rowcount else "warning")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("patient_list"))


@app.post("/patients/<int:patient_id>/history")
def history_add(patient_id):
    blocked = require("history_write")
    if blocked:
        return blocked
    if one("SELECT patient_id FROM PATIENT WHERE patient_id = ?", (patient_id,)) is None:
        abort(404)
    errors = []
    visit_date = parse_date(request.form.get("visit_date"))
    diagnosis = require_text(request.form.get("diagnosis"), "Diagnosis", 160, errors)
    notes = clean(request.form.get("notes"))
    doctor_raw = request.form.get("doctor_id", "").strip()
    doctor_id = int(doctor_raw) if doctor_raw.isdigit() else None
    if session.get("role") == "doctor":
        doctor_id = session.get("doctor_id")
    if not visit_date:
        errors.append("Visit date is required.")
    if notes and len(notes) > 400:
        errors.append("Notes must be 400 characters or fewer.")
    if errors:
        flash(" ".join(errors), "danger")
        return redirect(url_for("patient_detail", patient_id=patient_id))
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO PATIENT_HISTORY (patient_id, doctor_id, visit_date, diagnosis, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (patient_id, doctor_id, visit_date, diagnosis, notes or None),
        )
    flash("History note saved.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.route("/patients/<int:patient_id>/history/<int:history_id>/edit", methods=["GET", "POST"])
def history_edit(patient_id, history_id):
    blocked = require("history_write")
    if blocked:
        return blocked
    note = one(
        """
        SELECT * FROM PATIENT_HISTORY
        WHERE history_id = ? AND patient_id = ?
        """,
        (history_id, patient_id),
    )
    if note is None:
        abort(404)
    if not can_delete_history(note) and session.get("role") == "doctor":
        return forbid()
    if request.method == "GET":
        return render_template(
            "patients/history_form.html",
            page_title="Edit history note",
            note=note,
            errors=[],
            doctors=all_doctors(),
            patient_id=patient_id,
        )
    errors = []
    visit_date = parse_date(request.form.get("visit_date"))
    diagnosis = require_text(request.form.get("diagnosis"), "Diagnosis", 160, errors)
    notes = clean(request.form.get("notes"))
    doctor_raw = request.form.get("doctor_id", "").strip()
    doctor_id = int(doctor_raw) if doctor_raw.isdigit() else None
    if session.get("role") == "doctor":
        doctor_id = session.get("doctor_id")
    if not visit_date:
        errors.append("Visit date is required.")
    if notes and len(notes) > 400:
        errors.append("Notes must be 400 characters or fewer.")
    form_note = {
        "history_id": history_id,
        "visit_date": request.form.get("visit_date", ""),
        "diagnosis": request.form.get("diagnosis", ""),
        "notes": request.form.get("notes", ""),
        "doctor_id": doctor_id,
    }
    if errors:
        return render_template(
            "patients/history_form.html",
            page_title="Edit history note",
            note=form_note,
            errors=errors,
            doctors=all_doctors(),
            patient_id=patient_id,
        )
    with transaction() as conn:
        conn.execute(
            """
            UPDATE PATIENT_HISTORY
            SET doctor_id = ?, visit_date = ?, diagnosis = ?, notes = ?
            WHERE history_id = ? AND patient_id = ?
            """,
            (doctor_id, visit_date, diagnosis, notes or None, history_id, patient_id),
        )
    flash("History note updated.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


@app.post("/patients/<int:patient_id>/history/<int:history_id>/delete")
def history_delete(patient_id, history_id):
    note = one(
        "SELECT * FROM PATIENT_HISTORY WHERE history_id = ? AND patient_id = ?",
        (history_id, patient_id),
    )
    if note is None:
        abort(404)
    if not can_delete_history(note):
        return forbid()
    with transaction() as conn:
        conn.execute("DELETE FROM PATIENT_HISTORY WHERE history_id = ?", (history_id,))
    flash("History note deleted.", "success")
    return redirect(url_for("patient_detail", patient_id=patient_id))


# ---------------------------------------------------------------------------
# Doctors, schedules, departments
# ---------------------------------------------------------------------------

def doctor_from_form():
    errors = []
    form = {
        "name": require_text(request.form.get("name"), "Name", 80, errors),
        "speciality": require_text(request.form.get("speciality"), "Speciality", 80, errors),
        "phone": request.form.get("phone", "").strip(),
        "dept_id": request.form.get("dept_id", "").strip(),
    }
    if not form["phone"].isdigit() or len(form["phone"]) < 10:
        errors.append("Phone must be at least 10 digits.")
    if not form["dept_id"].isdigit():
        errors.append("Choose a department. The value stored is dept_id.")
    elif one("SELECT dept_id FROM DEPARTMENT WHERE dept_id = ?", (int(form["dept_id"]),)) is None:
        errors.append("That department does not exist.")
    return form, errors


def render_doctor_form(form, errors, doctor_id=None):
    return render_template(
        "doctors/form.html",
        page_title="Edit doctor" if doctor_id else "Add doctor",
        form=form,
        errors=errors,
        doctor_id=doctor_id,
        departments=all_departments(),
    )


@app.route("/doctors")
def doctor_list():
    term = request.args.get("q", "").strip()
    sql = """
        SELECT d.doctor_id, d.name, d.speciality, d.phone, d.dept_id,
               dept.dept_name, dept.location
        FROM DOCTOR AS d
        JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id
    """
    args = []
    if term:
        sql += " WHERE d.name LIKE ? OR d.speciality LIKE ? OR dept.dept_name LIKE ?"
        like = f"%{term}%"
        args = [like, like, like]
    sql += " ORDER BY d.name"
    return render_template(
        "doctors/list.html", page_title="Doctors", doctors=rows(sql, args), term=term
    )


@app.route("/doctors/add", methods=["GET", "POST"])
def doctor_add():
    blocked = require("doctors_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_doctor_form(
            {"name": "", "speciality": "", "phone": "", "dept_id": ""}, []
        )
    form, errors = doctor_from_form()
    if errors:
        return render_doctor_form(form, errors)
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO DOCTOR (name, speciality, phone, dept_id)
            VALUES (?, ?, ?, ?)
            """,
            (form["name"], form["speciality"], form["phone"], int(form["dept_id"])),
        )
    flash("Doctor saved. dept_id links this row to DEPARTMENT.", "success")
    return redirect(url_for("doctor_list"))


@app.route("/doctors/<int:doctor_id>")
def doctor_detail(doctor_id):
    doctor = one(
        """
        SELECT d.*, dept.dept_name, dept.location
        FROM DOCTOR AS d
        JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id
        WHERE d.doctor_id = ?
        """,
        (doctor_id,),
    )
    if doctor is None:
        abort(404)
    order = {name: index for index, name in enumerate(DAYS)}
    schedules = sorted(
        rows("SELECT * FROM DOCTOR_SCHEDULE WHERE doctor_id = ?", (doctor_id,)),
        key=lambda row: (order.get(row["day_of_week"], 9), row["start_time"]),
    )
    upcoming = rows(
        APPOINTMENT_FROM
        + " WHERE a.doctor_id = ? AND a.date >= ? ORDER BY a.date, a.time LIMIT 12",
        (doctor_id, date.today().isoformat()),
    )
    return render_template(
        "doctors/detail.html",
        page_title=doctor_label(doctor["name"]),
        doctor=doctor,
        schedules=schedules,
        upcoming=upcoming,
    )


@app.route("/doctors/<int:doctor_id>/edit", methods=["GET", "POST"])
def doctor_edit(doctor_id):
    blocked = require("doctors_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM DOCTOR WHERE doctor_id = ?", (doctor_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_doctor_form(dict(current), [], doctor_id)
    form, errors = doctor_from_form()
    if errors:
        return render_doctor_form(form, errors, doctor_id)
    with transaction() as conn:
        conn.execute(
            """
            UPDATE DOCTOR
            SET name = ?, speciality = ?, phone = ?, dept_id = ?
            WHERE doctor_id = ?
            """,
            (form["name"], form["speciality"], form["phone"], int(form["dept_id"]), doctor_id),
        )
    flash("Doctor updated.", "success")
    return redirect(url_for("doctor_detail", doctor_id=doctor_id))


@app.post("/doctors/<int:doctor_id>/delete")
def doctor_delete(doctor_id):
    blocked = require("doctors_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            cur = conn.execute("DELETE FROM DOCTOR WHERE doctor_id = ?", (doctor_id,))
        flash("Doctor deleted." if cur.rowcount else "That doctor is already gone.", "success" if cur.rowcount else "warning")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("doctor_list"))


def schedule_from_form():
    day = request.form.get("day_of_week", "").strip()
    start = parse_time(request.form.get("start_time"))
    end = parse_time(request.form.get("end_time"))
    errors = []
    if day not in DAYS:
        errors.append("Choose a day of the week.")
    if not start or not end:
        errors.append("Start and end must be times.")
    elif minutes(end) <= minutes(start):
        errors.append("End time must be after the start time.")
    return {"day_of_week": day, "start_time": start or "", "end_time": end or ""}, errors


@app.post("/doctors/<int:doctor_id>/schedule")
def schedule_add(doctor_id):
    blocked = require("doctors_write")
    if blocked:
        return blocked
    if one("SELECT doctor_id FROM DOCTOR WHERE doctor_id = ?", (doctor_id,)) is None:
        abort(404)
    form, errors = schedule_from_form()
    if errors:
        flash(" ".join(errors), "danger")
        return redirect(url_for("doctor_detail", doctor_id=doctor_id))
    try:
        with transaction() as conn:
            if schedule_overlaps(
                conn, doctor_id, form["day_of_week"], form["start_time"], form["end_time"]
            ):
                raise ValueError("That block overlaps another clinic on the same day.")
            conn.execute(
                """
                INSERT INTO DOCTOR_SCHEDULE (doctor_id, day_of_week, start_time, end_time)
                VALUES (?, ?, ?, ?)
                """,
                (doctor_id, form["day_of_week"], form["start_time"], form["end_time"]),
            )
    except ValueError as exc:
        flash(str(exc), "danger")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    else:
        flash("Clinic hours saved in DOCTOR_SCHEDULE.", "success")
    return redirect(url_for("doctor_detail", doctor_id=doctor_id))


@app.route("/doctors/<int:doctor_id>/schedule/<int:schedule_id>/edit", methods=["GET", "POST"])
def schedule_edit(doctor_id, schedule_id):
    blocked = require("doctors_write")
    if blocked:
        return blocked
    current = one(
        """
        SELECT * FROM DOCTOR_SCHEDULE
        WHERE schedule_id = ? AND doctor_id = ?
        """,
        (schedule_id, doctor_id),
    )
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "doctors/schedule_form.html",
            page_title="Edit clinic hours",
            form=dict(current),
            errors=[],
            doctor_id=doctor_id,
            schedule_id=schedule_id,
        )
    form, errors = schedule_from_form()
    if errors:
        return render_template(
            "doctors/schedule_form.html",
            page_title="Edit clinic hours",
            form=form,
            errors=errors,
            doctor_id=doctor_id,
            schedule_id=schedule_id,
        )
    try:
        with transaction() as conn:
            if schedule_overlaps(
                conn,
                doctor_id,
                form["day_of_week"],
                form["start_time"],
                form["end_time"],
                schedule_id,
            ):
                raise ValueError("That block overlaps another clinic on the same day.")
            conn.execute(
                """
                UPDATE DOCTOR_SCHEDULE
                SET day_of_week = ?, start_time = ?, end_time = ?
                WHERE schedule_id = ?
                """,
                (form["day_of_week"], form["start_time"], form["end_time"], schedule_id),
            )
    except ValueError as exc:
        return render_template(
            "doctors/schedule_form.html",
            page_title="Edit clinic hours",
            form=form,
            errors=[str(exc)],
            doctor_id=doctor_id,
            schedule_id=schedule_id,
        )
    flash("Clinic hours updated.", "success")
    return redirect(url_for("doctor_detail", doctor_id=doctor_id))


@app.post("/doctors/<int:doctor_id>/schedule/<int:schedule_id>/delete")
def schedule_delete(doctor_id, schedule_id):
    blocked = require("doctors_write")
    if blocked:
        return blocked
    with transaction() as conn:
        conn.execute(
            "DELETE FROM DOCTOR_SCHEDULE WHERE schedule_id = ? AND doctor_id = ?",
            (schedule_id, doctor_id),
        )
    flash("Clinic hours removed.", "success")
    return redirect(url_for("doctor_detail", doctor_id=doctor_id))


def department_from_form():
    errors = []
    form = {
        "dept_name": require_text(request.form.get("dept_name"), "Department name", 80, errors),
        "location": require_text(request.form.get("location"), "Location", 80, errors),
    }
    return form, errors


@app.route("/departments")
def department_list():
    departments = rows(
        """
        SELECT dept.dept_id, dept.dept_name, dept.location,
               COUNT(d.doctor_id) AS doctor_count
        FROM DEPARTMENT AS dept
        LEFT JOIN DOCTOR AS d ON d.dept_id = dept.dept_id
        GROUP BY dept.dept_id, dept.dept_name, dept.location
        ORDER BY dept.dept_name
        """
    )
    return render_template(
        "departments/list.html", page_title="Departments", departments=departments
    )


@app.route("/departments/add", methods=["GET", "POST"])
def department_add():
    blocked = require("departments_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_template(
            "departments/form.html",
            page_title="Add department",
            form={"dept_name": "", "location": ""},
            errors=[],
            dept_id=None,
        )
    form, errors = department_from_form()
    if errors:
        return render_template(
            "departments/form.html",
            page_title="Add department",
            form=form,
            errors=errors,
            dept_id=None,
        )
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO DEPARTMENT (dept_name, location) VALUES (?, ?)",
                (form["dept_name"], form["location"]),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "departments/form.html",
            page_title="Add department",
            form=form,
            errors=[integrity_message(exc)],
            dept_id=None,
        )
    flash("Department saved.", "success")
    return redirect(url_for("department_list"))


@app.route("/departments/<int:dept_id>/edit", methods=["GET", "POST"])
def department_edit(dept_id):
    blocked = require("departments_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM DEPARTMENT WHERE dept_id = ?", (dept_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "departments/form.html",
            page_title="Edit department",
            form=dict(current),
            errors=[],
            dept_id=dept_id,
        )
    form, errors = department_from_form()
    if errors:
        return render_template(
            "departments/form.html",
            page_title="Edit department",
            form=form,
            errors=errors,
            dept_id=dept_id,
        )
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE DEPARTMENT SET dept_name = ?, location = ?
                WHERE dept_id = ?
                """,
                (form["dept_name"], form["location"], dept_id),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "departments/form.html",
            page_title="Edit department",
            form=form,
            errors=[integrity_message(exc)],
            dept_id=dept_id,
        )
    flash("Department updated.", "success")
    return redirect(url_for("department_list"))


@app.post("/departments/<int:dept_id>/delete")
def department_delete(dept_id):
    blocked = require("departments_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM DEPARTMENT WHERE dept_id = ?", (dept_id,))
        flash("Department deleted.", "success")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("department_list"))


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

def render_appointment_form(form, errors, appt_id=None):
    slots = []
    slot_message = "Choose a doctor and a date to load free 30-minute slots."
    if str(form.get("doctor_id") or "").isdigit() and form.get("date"):
        slots = open_slots(get_db(), int(form["doctor_id"]), form["date"], appt_id)
        if form.get("time") and form["time"] not in slots:
            slots = sorted(set(slots) | {form["time"]})
        slot_message = "" if slots else "No free slot that day. Check the doctor schedule."
    return render_template(
        "appointments/form.html",
        page_title="Edit appointment" if appt_id else "Book appointment",
        form=form,
        errors=errors,
        appt_id=appt_id,
        patients=all_patients(),
        doctors=all_doctors(),
        slots=slots,
        slot_message=slot_message,
    )


def appointment_from_form(appt_id=None):
    errors = []
    form = {
        "patient_id": request.form.get("patient_id", "").strip(),
        "doctor_id": request.form.get("doctor_id", "").strip(),
        "date": request.form.get("date", "").strip(),
        "time": parse_time(request.form.get("time")) or "",
        "status": request.form.get("status", "").strip(),
    }
    if not form["patient_id"].isdigit():
        errors.append("Choose a patient. The value stored is patient_id.")
    if not form["doctor_id"].isdigit():
        errors.append("Choose a doctor. The value stored is doctor_id.")
    if not parse_date(form["date"]):
        errors.append("Choose a date.")
    if form["status"] not in APPT_STATUSES:
        errors.append("Choose a status.")
    if not form["time"]:
        errors.append("Choose a time slot.")
    elif (
        not errors
        and form["status"] != "Cancelled"
        and form["time"] not in open_slots(get_db(), int(form["doctor_id"]), form["date"], appt_id)
    ):
        errors.append("That time is not an open slot on this doctor's schedule.")
    return form, errors


@app.route("/appointments")
def appointment_list():
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    day = request.args.get("date", "").strip()
    clauses = []
    args = []
    if term:
        clauses.append("(p.name LIKE ? OR d.name LIKE ?)")
        like = f"%{term}%"
        args.extend([like, like])
    if status in APPT_STATUSES:
        clauses.append("a.status = ?")
        args.append(status)
    if parse_date(day):
        clauses.append("a.date = ?")
        args.append(day)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    appointments = rows(
        APPOINTMENT_FROM + where + " ORDER BY a.date DESC, a.time",
        args,
    )
    return render_template(
        "appointments/list.html",
        page_title="Appointments",
        appointments=appointments,
        term=term,
        status=status,
        day=day,
    )


@app.route("/appointments/slots")
def appointment_slots():
    doctor_id = request.args.get("doctor_id", type=int)
    day = request.args.get("date", "")
    appt_id = request.args.get("appt_id", type=int)
    if not doctor_id or not parse_date(day):
        return jsonify(slots=[], message="Choose a doctor and a date first")
    slots = open_slots(get_db(), doctor_id, day, appt_id)
    weekday = datetime.strptime(day, "%Y-%m-%d").strftime("%A")
    message = "" if slots else f"No free 30-minute slot on {weekday}."
    return jsonify(slots=slots, message=message)


@app.route("/appointments/add", methods=["GET", "POST"])
def appointment_add():
    blocked = require("appointments_write")
    if blocked:
        return blocked
    if request.method == "GET":
        form = {
            "patient_id": request.args.get("patient_id", ""),
            "doctor_id": request.args.get("doctor_id", ""),
            "date": request.args.get("date", date.today().isoformat()),
            "time": "",
            "status": "Scheduled",
        }
        return render_appointment_form(form, [])
    form, errors = appointment_from_form()
    if errors:
        return render_appointment_form(form, errors)
    try:
        with transaction() as conn:
            if form["status"] != "Cancelled" and form["time"] not in open_slots(
                conn, int(form["doctor_id"]), form["date"]
            ):
                raise ValueError("That slot was just taken. Pick another time.")
            conn.execute(
                """
                INSERT INTO APPOINTMENT (patient_id, doctor_id, date, time, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(form["patient_id"]),
                    int(form["doctor_id"]),
                    form["date"],
                    form["time"],
                    form["status"],
                ),
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else integrity_message(exc)
        return render_appointment_form(form, [message])
    flash("Appointment booked. The queue number is calculated, not stored.", "success")
    return redirect(url_for("appointment_list"))


@app.route("/appointments/<int:appt_id>/edit", methods=["GET", "POST"])
def appointment_edit(appt_id):
    blocked = require("appointments_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM APPOINTMENT WHERE appt_id = ?", (appt_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_appointment_form(dict(current), [], appt_id)
    form, errors = appointment_from_form(appt_id)
    if errors:
        return render_appointment_form(form, errors, appt_id)
    try:
        with transaction() as conn:
            if form["status"] != "Cancelled" and form["time"] not in open_slots(
                conn, int(form["doctor_id"]), form["date"], appt_id
            ):
                raise ValueError("That slot was just taken. Pick another time.")
            conn.execute(
                """
                UPDATE APPOINTMENT
                SET patient_id = ?, doctor_id = ?, date = ?, time = ?, status = ?
                WHERE appt_id = ?
                """,
                (
                    int(form["patient_id"]),
                    int(form["doctor_id"]),
                    form["date"],
                    form["time"],
                    form["status"],
                    appt_id,
                ),
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else integrity_message(exc)
        return render_appointment_form(form, [message], appt_id)
    flash("Appointment updated.", "success")
    return redirect(url_for("appointment_list"))


@app.post("/appointments/<int:appt_id>/status")
def appointment_status(appt_id):
    current = one("SELECT * FROM APPOINTMENT WHERE appt_id = ?", (appt_id,))
    if current is None:
        abort(404)
    if not can_touch_appointment(current["doctor_id"]):
        return forbid()
    status = request.form.get("status", "").strip()
    if status not in APPT_STATUSES:
        flash("Choose a valid status.", "danger")
        return redirect(url_for("appointment_list"))
    try:
        with transaction() as conn:
            if status != "Cancelled" and current["time"] not in open_slots(
                conn, current["doctor_id"], current["date"], appt_id
            ):
                raise ValueError(
                    "That slot is already held by another live appointment."
                )
            conn.execute(
                "UPDATE APPOINTMENT SET status = ? WHERE appt_id = ?",
                (status, appt_id),
            )
    except (ValueError, sqlite3.IntegrityError) as exc:
        flash(str(exc) if isinstance(exc, ValueError) else integrity_message(exc), "danger")
    else:
        flash(f"Appointment {appt_id} is now {status}.", "success")
    return redirect(url_for("appointment_list"))


@app.post("/appointments/<int:appt_id>/delete")
def appointment_delete(appt_id):
    blocked = require("appointments_write")
    if blocked:
        return blocked
    with transaction() as conn:
        conn.execute("DELETE FROM APPOINTMENT WHERE appt_id = ?", (appt_id,))
    flash("Appointment deleted.", "success")
    return redirect(url_for("appointment_list"))


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

def render_bill_form(form, items, errors, bill_id=None):
    return render_template(
        "billing/form.html",
        page_title="Edit bill" if bill_id else "New bill",
        form=form,
        items=items or [blank_bill_item()],
        errors=errors,
        bill_id=bill_id,
        patients=all_patients(),
    )


@app.route("/billing")
def bill_list():
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    sql = "SELECT * FROM V_BILL_BALANCE"
    clauses = []
    args = []
    if term:
        clauses.append("patient LIKE ?")
        args.append(f"%{term}%")
    if status in ("Unpaid", "Partial", "Paid"):
        clauses.append("status = ?")
        args.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY date DESC, bill_id DESC"
    return render_template(
        "billing/list.html",
        page_title="Billing",
        bills=rows(sql, args),
        term=term,
        status=status,
    )


@app.route("/billing/add", methods=["GET", "POST"])
def bill_add():
    blocked = require("billing_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_bill_form(
            {
                "patient_id": "",
                "date": date.today().isoformat(),
                "pay_amount": "",
                "pay_method": "UPI",
                "pay_date": date.today().isoformat(),
            },
            [blank_bill_item()],
            [],
        )
    return save_bill(None)


@app.route("/billing/<int:bill_id>/edit", methods=["GET", "POST"])
def bill_edit(bill_id):
    blocked = require("billing_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM BILL WHERE bill_id = ?", (bill_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        items = [
            {"description": row["description"], "amount": row["amount"]}
            for row in rows(
                "SELECT description, amount FROM BILL_ITEM WHERE bill_id = ? ORDER BY item_id",
                (bill_id,),
            )
        ]
        return render_bill_form(dict(current), items or [blank_bill_item()], [], bill_id)
    return save_bill(bill_id)


def save_bill(bill_id):
    errors = []
    form = {
        "patient_id": request.form.get("patient_id", "").strip(),
        "date": request.form.get("date", "").strip(),
        "pay_amount": request.form.get("pay_amount", "").strip(),
        "pay_method": request.form.get("pay_method", "Cash").strip(),
        "pay_date": request.form.get("pay_date", "").strip(),
    }
    raw_items = read_bill_items(request.form)
    clean_items, item_errors = validate_bill_items(raw_items)
    errors.extend(item_errors)
    if not form["patient_id"].isdigit():
        errors.append("Choose a patient. The value stored is patient_id.")
    if not parse_date(form["date"]):
        errors.append("Choose a bill date.")
    pay_amount = None
    if bill_id is None and form["pay_amount"]:
        pay_amount = parse_rupees(form["pay_amount"])
        if pay_amount is None or pay_amount <= 0:
            errors.append("Payment received must be a whole number of rupees.")
        if form["pay_method"] not in PAY_METHODS:
            errors.append("Choose how the payment was received.")
        if not parse_date(form["pay_date"]):
            errors.append("Payment date is required when recording a receipt.")
    if errors:
        return render_bill_form(form, raw_items, errors, bill_id)
    total = sum(item["amount"] for item in clean_items)
    target_id = bill_id
    try:
        with transaction() as conn:
            if target_id is None:
                cur = conn.execute(
                    """
                    INSERT INTO BILL (patient_id, amount, date, status)
                    VALUES (?, ?, ?, 'Unpaid')
                    """,
                    (int(form["patient_id"]), total, form["date"]),
                )
                target_id = cur.lastrowid
            else:
                conn.execute(
                    """
                    UPDATE BILL SET patient_id = ?, amount = ?, date = ?
                    WHERE bill_id = ?
                    """,
                    (int(form["patient_id"]), total, form["date"], target_id),
                )
                conn.execute("DELETE FROM BILL_ITEM WHERE bill_id = ?", (target_id,))
            for item in clean_items:
                conn.execute(
                    """
                    INSERT INTO BILL_ITEM (bill_id, description, amount)
                    VALUES (?, ?, ?)
                    """,
                    (target_id, item["description"], item["amount"]),
                )
            if pay_amount:
                record_payment(conn, target_id, pay_amount, form["pay_date"], form["pay_method"])
            else:
                refresh_bill_status(conn, target_id)
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else integrity_message(exc)
        return render_bill_form(form, raw_items, [message], bill_id)
    flash(
        f"{bill_no(target_id)} saved for {format_inr(total)}. "
        "BILL.amount is the sum of the BILL_ITEM rows.",
        "success",
    )
    return redirect(url_for("bill_invoice", bill_id=target_id))


@app.route("/billing/<int:bill_id>")
def bill_invoice(bill_id):
    bill = one("SELECT * FROM V_BILL_BALANCE WHERE bill_id = ?", (bill_id,))
    if bill is None:
        abort(404)
    patient = one("SELECT * FROM PATIENT WHERE patient_id = ?", (bill["patient_id"],))
    items = rows(
        "SELECT * FROM BILL_ITEM WHERE bill_id = ? ORDER BY item_id", (bill_id,)
    )
    payments = rows(
        "SELECT * FROM PAYMENT WHERE bill_id = ? ORDER BY pay_date, payment_id",
        (bill_id,),
    )
    return render_template(
        "billing/invoice.html",
        page_title=bill_no(bill_id),
        bill=bill,
        patient=patient,
        items=items,
        payments=payments,
    )


@app.post("/billing/<int:bill_id>/payments")
def payment_add(bill_id):
    blocked = require("billing_write")
    if blocked:
        return blocked
    if one("SELECT bill_id FROM BILL WHERE bill_id = ?", (bill_id,)) is None:
        abort(404)
    amount = parse_rupees(request.form.get("amount"))
    pay_date = parse_date(request.form.get("pay_date"))
    method = request.form.get("method", "").strip()
    errors = []
    if amount is None or amount <= 0:
        errors.append("Enter the amount in whole rupees.")
    if not pay_date:
        errors.append("Choose the payment date.")
    if method not in PAY_METHODS:
        errors.append("Choose a payment method.")
    if errors:
        flash(" ".join(errors), "danger")
        return redirect(url_for("bill_invoice", bill_id=bill_id))
    try:
        with transaction() as conn:
            # Atomic pair: the receipt and the bill status commit together.
            status = record_payment(conn, bill_id, amount, pay_date, method)
    except ValueError as exc:
        flash(str(exc), "danger")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    else:
        flash(
            f"Recorded {format_inr(amount)} on {bill_no(bill_id)}. "
            f"BILL.status is now {status}. "
            "INSERT into PAYMENT and UPDATE of BILL were committed together.",
            "success",
        )
    return redirect(url_for("bill_invoice", bill_id=bill_id))


@app.post("/billing/<int:bill_id>/payments/<int:payment_id>/delete")
def payment_delete(bill_id, payment_id):
    blocked = require("billing_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            cur = conn.execute(
                "DELETE FROM PAYMENT WHERE payment_id = ? AND bill_id = ?",
                (payment_id, bill_id),
            )
            if cur.rowcount == 0:
                raise ValueError("That payment was not on this bill.")
            status = refresh_bill_status(conn, bill_id)
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        flash(f"Payment removed. {bill_no(bill_id)} is now {status}.", "success")
    return redirect(url_for("bill_invoice", bill_id=bill_id))


@app.post("/billing/<int:bill_id>/delete")
def bill_delete(bill_id):
    blocked = require("billing_write")
    if blocked:
        return blocked
    with transaction() as conn:
        conn.execute("DELETE FROM BILL WHERE bill_id = ?", (bill_id,))
    flash("Bill deleted. Its charges and payments were removed with it.", "success")
    return redirect(url_for("bill_list"))


# ---------------------------------------------------------------------------
# Pharmacy
# ---------------------------------------------------------------------------

def medicine_from_form():
    errors = []
    form = {
        "name": require_text(request.form.get("name"), "Medicine name", 80, errors),
        "category": require_text(request.form.get("category"), "Category", 40, errors),
        "unit_price": request.form.get("unit_price", "").strip(),
        "stock_qty": request.form.get("stock_qty", "").strip(),
        "expiry_date": request.form.get("expiry_date", "").strip(),
    }
    price = parse_rupees(form["unit_price"])
    if price is None:
        errors.append("Unit price must be a whole number of rupees.")
    else:
        form["unit_price"] = price
    if not form["stock_qty"].isdigit():
        errors.append("Stock must be a whole number.")
    else:
        form["stock_qty"] = int(form["stock_qty"])
    if not parse_date(form["expiry_date"]):
        errors.append("Choose an expiry date.")
    return form, errors


def render_medicine_form(form, errors, medicine_id=None):
    return render_template(
        "pharmacy/medicine_form.html",
        page_title="Edit medicine" if medicine_id else "Add medicine",
        form=form,
        errors=errors,
        medicine_id=medicine_id,
    )


@app.route("/pharmacy/medicines")
def medicine_list():
    term = request.args.get("q", "").strip()
    sql = "SELECT * FROM MEDICINE"
    args = []
    if term:
        sql += " WHERE name LIKE ? OR category LIKE ?"
        like = f"%{term}%"
        args = [like, like]
    sql += " ORDER BY name"
    return render_template(
        "pharmacy/medicines.html",
        page_title="Pharmacy",
        medicines=rows(sql, args),
        term=term,
    )


@app.route("/pharmacy/medicines/add", methods=["GET", "POST"])
def medicine_add():
    blocked = require("medicines_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_medicine_form(
            {"name": "", "category": "", "unit_price": "", "stock_qty": "", "expiry_date": ""},
            [],
        )
    form, errors = medicine_from_form()
    if errors:
        return render_medicine_form(form, errors)
    try:
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO MEDICINE (name, category, unit_price, stock_qty, expiry_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    form["name"],
                    form["category"],
                    form["unit_price"],
                    form["stock_qty"],
                    form["expiry_date"],
                ),
            )
    except sqlite3.IntegrityError as exc:
        return render_medicine_form(form, [integrity_message(exc)])
    flash("Medicine saved.", "success")
    return redirect(url_for("medicine_list"))


@app.route("/pharmacy/medicines/<int:medicine_id>/edit", methods=["GET", "POST"])
def medicine_edit(medicine_id):
    blocked = require("medicines_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM MEDICINE WHERE medicine_id = ?", (medicine_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_medicine_form(dict(current), [], medicine_id)
    form, errors = medicine_from_form()
    if errors:
        return render_medicine_form(form, errors, medicine_id)
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE MEDICINE
                SET name = ?, category = ?, unit_price = ?, stock_qty = ?, expiry_date = ?
                WHERE medicine_id = ?
                """,
                (
                    form["name"],
                    form["category"],
                    form["unit_price"],
                    form["stock_qty"],
                    form["expiry_date"],
                    medicine_id,
                ),
            )
    except sqlite3.IntegrityError as exc:
        return render_medicine_form(form, [integrity_message(exc)], medicine_id)
    flash("Medicine updated.", "success")
    return redirect(url_for("medicine_list"))


@app.post("/pharmacy/medicines/<int:medicine_id>/delete")
def medicine_delete(medicine_id):
    blocked = require("medicines_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM MEDICINE WHERE medicine_id = ?", (medicine_id,))
        flash("Medicine deleted.", "success")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("medicine_list"))


def render_prescription_form(form, lines, errors, prescription_id=None):
    return render_template(
        "pharmacy/prescription_form.html",
        page_title="Edit prescription" if prescription_id else "New prescription",
        form=form,
        lines=lines or [blank_medicine_line()],
        errors=errors,
        prescription_id=prescription_id,
        patients=all_patients(),
        doctors=all_doctors(),
        medicines=all_medicines(),
        locked_doctor=session.get("role") == "doctor",
    )


def prescription_from_form():
    errors = []
    form = {
        "patient_id": request.form.get("patient_id", "").strip(),
        "doctor_id": request.form.get("doctor_id", "").strip(),
        "date": request.form.get("date", "").strip(),
        "diagnosis": require_text(request.form.get("diagnosis"), "Diagnosis", 160, errors),
    }
    if session.get("role") == "doctor":
        form["doctor_id"] = str(session.get("doctor_id") or "")
    if not form["patient_id"].isdigit():
        errors.append("Choose a patient. The value stored is patient_id.")
    if not form["doctor_id"].isdigit():
        errors.append("Choose a doctor. The value stored is doctor_id.")
    if not parse_date(form["date"]):
        errors.append("Choose the prescription date.")
    raw_lines = read_medicine_lines(request.form)
    clean_lines, line_errors = validate_medicine_lines(raw_lines)
    errors.extend(line_errors)
    return form, raw_lines, clean_lines, errors


@app.route("/pharmacy/prescriptions")
def prescription_list():
    term = request.args.get("q", "").strip()
    sql = """
        SELECT pr.prescription_id, pr.date, pr.diagnosis, pr.patient_id, pr.doctor_id,
               p.name AS patient_name, d.name AS doctor_name,
               COUNT(pm.medicine_id) AS medicine_count
        FROM PRESCRIPTION AS pr
        JOIN PATIENT AS p ON p.patient_id = pr.patient_id
        JOIN DOCTOR AS d ON d.doctor_id = pr.doctor_id
        LEFT JOIN PRESCRIPTION_MEDICINE AS pm ON pm.prescription_id = pr.prescription_id
    """
    args = []
    if term:
        sql += " WHERE p.name LIKE ? OR d.name LIKE ? OR pr.diagnosis LIKE ?"
        like = f"%{term}%"
        args = [like, like, like]
    sql += " GROUP BY pr.prescription_id ORDER BY pr.date DESC, pr.prescription_id DESC"
    return render_template(
        "pharmacy/prescriptions.html",
        page_title="Prescriptions",
        prescriptions=rows(sql, args),
        term=term,
    )


@app.route("/pharmacy/prescriptions/add", methods=["GET", "POST"])
def prescription_add():
    blocked = require("prescriptions_write")
    if blocked:
        return blocked
    if session.get("role") == "doctor" and not session.get("doctor_id"):
        flash("This login is not linked to a doctor row.", "danger")
        return redirect(url_for("prescription_list"))
    if request.method == "GET":
        form = {
            "patient_id": request.args.get("patient_id", ""),
            "doctor_id": str(session.get("doctor_id") or ""),
            "date": date.today().isoformat(),
            "diagnosis": "",
        }
        return render_prescription_form(form, [blank_medicine_line()], [])
    form, raw_lines, clean_lines, errors = prescription_from_form()
    if errors:
        return render_prescription_form(form, raw_lines, errors)
    try:
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO PRESCRIPTION (patient_id, doctor_id, date, diagnosis)
                VALUES (?, ?, ?, ?)
                """,
                (int(form["patient_id"]), int(form["doctor_id"]), form["date"], form["diagnosis"]),
            )
            prescription_id = cur.lastrowid
            apply_prescription_lines(conn, prescription_id, clean_lines)
    except ValueError as exc:
        return render_prescription_form(form, raw_lines, [str(exc)])
    except sqlite3.IntegrityError as exc:
        return render_prescription_form(form, raw_lines, [integrity_message(exc)])
    flash(
        f"Prescription {prescription_id} saved. Inserted 1 row into PRESCRIPTION and "
        f"{len(clean_lines)} row(s) into PRESCRIPTION_MEDICINE. Stock was reduced in the same transaction.",
        "success",
    )
    return redirect(url_for("prescription_detail", prescription_id=prescription_id))


@app.route("/pharmacy/prescriptions/<int:prescription_id>")
def prescription_detail(prescription_id):
    header = one(
        """
        SELECT pr.*, p.name AS patient_name, p.age, p.gender, p.phone,
               d.name AS doctor_name, d.speciality
        FROM PRESCRIPTION AS pr
        JOIN PATIENT AS p ON p.patient_id = pr.patient_id
        JOIN DOCTOR AS d ON d.doctor_id = pr.doctor_id
        WHERE pr.prescription_id = ?
        """,
        (prescription_id,),
    )
    if header is None:
        abort(404)
    lines = rows(
        """
        SELECT pm.*, m.name AS medicine_name, m.unit_price
        FROM PRESCRIPTION_MEDICINE AS pm
        JOIN MEDICINE AS m ON m.medicine_id = pm.medicine_id
        WHERE pm.prescription_id = ?
        ORDER BY m.name
        """,
        (prescription_id,),
    )
    return render_template(
        "pharmacy/prescription_detail.html",
        page_title=f"Prescription {prescription_id}",
        header=header,
        lines=lines,
    )


@app.route("/pharmacy/prescriptions/<int:prescription_id>/edit", methods=["GET", "POST"])
def prescription_edit(prescription_id):
    blocked = require("prescriptions_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM PRESCRIPTION WHERE prescription_id = ?", (prescription_id,))
    if current is None:
        abort(404)
    if not can_edit_prescription(current["doctor_id"]):
        return forbid()
    if request.method == "GET":
        lines = [
            {
                "medicine_id": row["medicine_id"],
                "dosage": row["dosage"],
                "duration": row["duration"],
                "quantity": row["quantity"],
            }
            for row in rows(
                """
                SELECT medicine_id, dosage, duration, quantity
                FROM PRESCRIPTION_MEDICINE
                WHERE prescription_id = ?
                """,
                (prescription_id,),
            )
        ]
        return render_prescription_form(dict(current), lines, [], prescription_id)
    form, raw_lines, clean_lines, errors = prescription_from_form()
    if errors:
        return render_prescription_form(form, raw_lines, errors, prescription_id)
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE PRESCRIPTION
                SET patient_id = ?, doctor_id = ?, date = ?, diagnosis = ?
                WHERE prescription_id = ?
                """,
                (
                    int(form["patient_id"]),
                    int(form["doctor_id"]),
                    form["date"],
                    form["diagnosis"],
                    prescription_id,
                ),
            )
            restore_prescription_lines(conn, prescription_id)
            apply_prescription_lines(conn, prescription_id, clean_lines)
    except ValueError as exc:
        return render_prescription_form(form, raw_lines, [str(exc)], prescription_id)
    except sqlite3.IntegrityError as exc:
        return render_prescription_form(form, raw_lines, [integrity_message(exc)], prescription_id)
    flash("Prescription updated. Stock was put back, then reduced again, in one transaction.", "success")
    return redirect(url_for("prescription_detail", prescription_id=prescription_id))


@app.post("/pharmacy/prescriptions/<int:prescription_id>/delete")
def prescription_delete(prescription_id):
    blocked = require("prescriptions_write")
    if blocked:
        return blocked
    current = one(
        "SELECT doctor_id FROM PRESCRIPTION WHERE prescription_id = ?",
        (prescription_id,),
    )
    if current is None:
        abort(404)
    if not can_edit_prescription(current["doctor_id"]):
        return forbid()
    with transaction() as conn:
        restore_prescription_lines(conn, prescription_id)
        conn.execute("DELETE FROM PRESCRIPTION WHERE prescription_id = ?", (prescription_id,))
    flash("Prescription deleted and the stock was returned.", "success")
    return redirect(url_for("prescription_list"))


# ---------------------------------------------------------------------------
# Admissions, wards, beds
# ---------------------------------------------------------------------------

def available_beds(include_bed_id=None):
    return rows(
        """
        SELECT b.bed_id, b.bed_number, b.status, w.ward_name
        FROM BED AS b
        JOIN WARD AS w ON w.ward_id = b.ward_id
        WHERE b.status = 'Available' OR b.bed_id = ?
        ORDER BY w.ward_name, b.bed_number
        """,
        (include_bed_id or 0,),
    )


@app.route("/admissions")
def admission_list():
    term = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    sql = """
        SELECT ad.*, p.name AS patient_name, d.name AS doctor_name,
               b.bed_number, w.ward_name
        FROM ADMISSION AS ad
        JOIN PATIENT AS p ON p.patient_id = ad.patient_id
        JOIN DOCTOR AS d ON d.doctor_id = ad.doctor_id
        JOIN BED AS b ON b.bed_id = ad.bed_id
        JOIN WARD AS w ON w.ward_id = b.ward_id
    """
    clauses = []
    args = []
    if term:
        clauses.append("(p.name LIKE ? OR w.ward_name LIKE ? OR b.bed_number LIKE ?)")
        like = f"%{term}%"
        args.extend([like, like, like])
    if status in ("Admitted", "Discharged"):
        clauses.append("ad.status = ?")
        args.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY CASE ad.status WHEN 'Admitted' THEN 0 ELSE 1 END, ad.admit_date DESC"
    return render_template(
        "admissions/list.html",
        page_title="Admissions",
        admissions=rows(sql, args),
        term=term,
        status=status,
    )


def render_admission_form(form, errors, admission_id=None):
    include = int(form["bed_id"]) if str(form.get("bed_id") or "").isdigit() else None
    return render_template(
        "admissions/form.html",
        page_title="Edit admission" if admission_id else "Admit patient",
        form=form,
        errors=errors,
        admission_id=admission_id,
        patients=all_patients(),
        doctors=all_doctors(),
        beds=available_beds(include),
    )


def admission_from_form():
    errors = []
    form = {
        "patient_id": request.form.get("patient_id", "").strip(),
        "doctor_id": request.form.get("doctor_id", "").strip(),
        "bed_id": request.form.get("bed_id", "").strip(),
        "admit_date": request.form.get("admit_date", "").strip(),
        "reason": require_text(request.form.get("reason"), "Reason", 160, errors),
    }
    if not form["patient_id"].isdigit():
        errors.append("Choose a patient.")
    if not form["doctor_id"].isdigit():
        errors.append("Choose a doctor.")
    if not form["bed_id"].isdigit():
        errors.append("Choose a free bed.")
    if not parse_date(form["admit_date"]):
        errors.append("Choose the admission date.")
    return form, errors


@app.route("/admissions/add", methods=["GET", "POST"])
def admission_add():
    blocked = require("admissions_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_admission_form(
            {
                "patient_id": "",
                "doctor_id": "",
                "bed_id": "",
                "admit_date": date.today().isoformat(),
                "reason": "",
            },
            [],
        )
    form, errors = admission_from_form()
    if errors:
        return render_admission_form(form, errors)
    try:
        with transaction() as conn:
            occupy_bed(conn, int(form["bed_id"]))
            conn.execute(
                """
                INSERT INTO ADMISSION
                    (patient_id, doctor_id, bed_id, admit_date, discharge_date, reason, status)
                VALUES (?, ?, ?, ?, NULL, ?, 'Admitted')
                """,
                (
                    int(form["patient_id"]),
                    int(form["doctor_id"]),
                    int(form["bed_id"]),
                    form["admit_date"],
                    form["reason"],
                ),
            )
    except ValueError as exc:
        return render_admission_form(form, [str(exc)])
    except sqlite3.IntegrityError as exc:
        return render_admission_form(form, [integrity_message(exc)])
    flash("Patient admitted. The bed was marked Occupied in the same transaction.", "success")
    return redirect(url_for("admission_list"))


@app.route("/admissions/<int:admission_id>/edit", methods=["GET", "POST"])
def admission_edit(admission_id):
    blocked = require("admissions_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM ADMISSION WHERE admission_id = ?", (admission_id,))
    if current is None:
        abort(404)
    if current["status"] != "Admitted":
        flash("Discharged stays are kept as history. Admit the patient again if they return.", "warning")
        return redirect(url_for("admission_list"))
    if request.method == "GET":
        return render_admission_form(dict(current), [], admission_id)
    form, errors = admission_from_form()
    if errors:
        return render_admission_form(form, errors, admission_id)
    try:
        with transaction() as conn:
            if int(form["bed_id"]) != current["bed_id"]:
                free_bed(conn, current["bed_id"])
                occupy_bed(conn, int(form["bed_id"]))
            conn.execute(
                """
                UPDATE ADMISSION
                SET patient_id = ?, doctor_id = ?, bed_id = ?, admit_date = ?, reason = ?
                WHERE admission_id = ?
                """,
                (
                    int(form["patient_id"]),
                    int(form["doctor_id"]),
                    int(form["bed_id"]),
                    form["admit_date"],
                    form["reason"],
                    admission_id,
                ),
            )
    except ValueError as exc:
        return render_admission_form(form, [str(exc)], admission_id)
    except sqlite3.IntegrityError as exc:
        return render_admission_form(form, [integrity_message(exc)], admission_id)
    flash("Admission updated.", "success")
    return redirect(url_for("admission_list"))


@app.post("/admissions/<int:admission_id>/discharge")
def admission_discharge(admission_id):
    blocked = require("admissions_write")
    if blocked:
        return blocked
    discharge_date = parse_date(request.form.get("discharge_date")) or date.today().isoformat()
    try:
        with transaction() as conn:
            current = conn.execute(
                "SELECT * FROM ADMISSION WHERE admission_id = ?", (admission_id,)
            ).fetchone()
            if current is None:
                raise ValueError("That admission does not exist.")
            if current["status"] != "Admitted":
                raise ValueError("This stay is already discharged.")
            if discharge_date < current["admit_date"]:
                raise ValueError("Discharge date cannot be before the admission date.")
            conn.execute(
                """
                UPDATE ADMISSION
                SET status = 'Discharged', discharge_date = ?
                WHERE admission_id = ?
                """,
                (discharge_date, admission_id),
            )
            free_bed(conn, current["bed_id"])
    except ValueError as exc:
        flash(str(exc), "danger")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    else:
        flash(
            "Discharged. ADMISSION.status and the bed status were updated together.",
            "success",
        )
    return redirect(url_for("admission_list"))


@app.post("/admissions/<int:admission_id>/delete")
def admission_delete(admission_id):
    blocked = require("admissions_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            current = conn.execute(
                "SELECT * FROM ADMISSION WHERE admission_id = ?", (admission_id,)
            ).fetchone()
            if current is None:
                raise ValueError("That admission is already gone.")
            if current["status"] == "Admitted":
                free_bed(conn, current["bed_id"])
            conn.execute("DELETE FROM ADMISSION WHERE admission_id = ?", (admission_id,))
    except ValueError as exc:
        flash(str(exc), "warning")
    else:
        flash("Admission deleted.", "success")
    return redirect(url_for("admission_list"))


def ward_from_form():
    errors = []
    form = {
        "ward_name": require_text(request.form.get("ward_name"), "Ward name", 60, errors),
        "ward_type": require_text(request.form.get("ward_type"), "Ward type", 40, errors),
        "location": require_text(request.form.get("location"), "Location", 80, errors),
    }
    return form, errors


@app.route("/wards")
def ward_list():
    wards = rows(
        """
        SELECT w.*,
               COUNT(b.bed_id) AS bed_count,
               COALESCE(SUM(CASE WHEN b.status = 'Occupied' THEN 1 ELSE 0 END), 0) AS occupied
        FROM WARD AS w
        LEFT JOIN BED AS b ON b.ward_id = w.ward_id
        GROUP BY w.ward_id
        ORDER BY w.ward_name
        """
    )
    return render_template("admissions/wards.html", page_title="Wards", wards=wards)


@app.route("/wards/add", methods=["GET", "POST"])
def ward_add():
    blocked = require("wards_write")
    if blocked:
        return blocked
    if request.method == "GET":
        return render_template(
            "admissions/ward_form.html",
            page_title="Add ward",
            form={"ward_name": "", "ward_type": "", "location": ""},
            errors=[],
            ward_id=None,
        )
    form, errors = ward_from_form()
    if errors:
        return render_template(
            "admissions/ward_form.html",
            page_title="Add ward",
            form=form,
            errors=errors,
            ward_id=None,
        )
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO WARD (ward_name, ward_type, location) VALUES (?, ?, ?)",
                (form["ward_name"], form["ward_type"], form["location"]),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "admissions/ward_form.html",
            page_title="Add ward",
            form=form,
            errors=[integrity_message(exc)],
            ward_id=None,
        )
    flash("Ward saved.", "success")
    return redirect(url_for("ward_list"))


@app.route("/wards/<int:ward_id>/edit", methods=["GET", "POST"])
def ward_edit(ward_id):
    blocked = require("wards_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM WARD WHERE ward_id = ?", (ward_id,))
    if current is None:
        abort(404)
    if request.method == "GET":
        return render_template(
            "admissions/ward_form.html",
            page_title="Edit ward",
            form=dict(current),
            errors=[],
            ward_id=ward_id,
        )
    form, errors = ward_from_form()
    if errors:
        return render_template(
            "admissions/ward_form.html",
            page_title="Edit ward",
            form=form,
            errors=errors,
            ward_id=ward_id,
        )
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE WARD SET ward_name = ?, ward_type = ?, location = ?
                WHERE ward_id = ?
                """,
                (form["ward_name"], form["ward_type"], form["location"], ward_id),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "admissions/ward_form.html",
            page_title="Edit ward",
            form=form,
            errors=[integrity_message(exc)],
            ward_id=ward_id,
        )
    flash("Ward updated.", "success")
    return redirect(url_for("ward_list"))


@app.post("/wards/<int:ward_id>/delete")
def ward_delete(ward_id):
    blocked = require("wards_write")
    if blocked:
        return blocked
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM WARD WHERE ward_id = ?", (ward_id,))
        flash("Ward deleted.", "success")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("ward_list"))


def bed_from_form(current_status=None):
    errors = []
    form = {
        "ward_id": request.form.get("ward_id", "").strip(),
        "bed_number": require_text(request.form.get("bed_number"), "Bed number", 20, errors),
        "status": request.form.get("status", "").strip(),
    }
    if not form["ward_id"].isdigit():
        errors.append("Choose a ward.")
    if current_status == "Occupied":
        form["status"] = "Occupied"
    elif form["status"] not in BED_CHOICES:
        errors.append("Status can be Available or Maintenance. Occupied is set by an admission.")
    return form, errors


@app.route("/beds")
def bed_list():
    beds = rows(
        """
        SELECT b.*, w.ward_name, p.name AS patient_name
        FROM BED AS b
        JOIN WARD AS w ON w.ward_id = b.ward_id
        LEFT JOIN ADMISSION AS ad ON ad.bed_id = b.bed_id AND ad.status = 'Admitted'
        LEFT JOIN PATIENT AS p ON p.patient_id = ad.patient_id
        ORDER BY w.ward_name, b.bed_number
        """
    )
    return render_template("admissions/beds.html", page_title="Beds", beds=beds)


@app.route("/beds/add", methods=["GET", "POST"])
def bed_add():
    blocked = require("beds_write")
    if blocked:
        return blocked
    wards = rows("SELECT ward_id, ward_name FROM WARD ORDER BY ward_name")
    if request.method == "GET":
        return render_template(
            "admissions/bed_form.html",
            page_title="Add bed",
            form={"ward_id": "", "bed_number": "", "status": "Available"},
            errors=[],
            bed_id=None,
            wards=wards,
            locked_status=False,
        )
    form, errors = bed_from_form()
    if errors:
        return render_template(
            "admissions/bed_form.html",
            page_title="Add bed",
            form=form,
            errors=errors,
            bed_id=None,
            wards=wards,
            locked_status=False,
        )
    try:
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO BED (ward_id, bed_number, status)
                VALUES (?, ?, ?)
                """,
                (int(form["ward_id"]), form["bed_number"], form["status"]),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "admissions/bed_form.html",
            page_title="Add bed",
            form=form,
            errors=[integrity_message(exc)],
            bed_id=None,
            wards=wards,
            locked_status=False,
        )
    flash("Bed saved.", "success")
    return redirect(url_for("bed_list"))


@app.route("/beds/<int:bed_id>/edit", methods=["GET", "POST"])
def bed_edit(bed_id):
    blocked = require("beds_write")
    if blocked:
        return blocked
    current = one("SELECT * FROM BED WHERE bed_id = ?", (bed_id,))
    if current is None:
        abort(404)
    wards = rows("SELECT ward_id, ward_name FROM WARD ORDER BY ward_name")
    locked = current["status"] == "Occupied"
    if request.method == "GET":
        return render_template(
            "admissions/bed_form.html",
            page_title="Edit bed",
            form=dict(current),
            errors=[],
            bed_id=bed_id,
            wards=wards,
            locked_status=locked,
        )
    form, errors = bed_from_form(current["status"])
    if errors:
        return render_template(
            "admissions/bed_form.html",
            page_title="Edit bed",
            form=form,
            errors=errors,
            bed_id=bed_id,
            wards=wards,
            locked_status=locked,
        )
    try:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE BED SET ward_id = ?, bed_number = ?, status = ?
                WHERE bed_id = ?
                """,
                (int(form["ward_id"]), form["bed_number"], form["status"], bed_id),
            )
    except sqlite3.IntegrityError as exc:
        return render_template(
            "admissions/bed_form.html",
            page_title="Edit bed",
            form=form,
            errors=[integrity_message(exc)],
            bed_id=bed_id,
            wards=wards,
            locked_status=locked,
        )
    flash("Bed updated.", "success")
    return redirect(url_for("bed_list"))


@app.post("/beds/<int:bed_id>/delete")
def bed_delete(bed_id):
    blocked = require("beds_write")
    if blocked:
        return blocked
    bed = one("SELECT status FROM BED WHERE bed_id = ?", (bed_id,))
    if bed and bed["status"] == "Occupied":
        flash("Discharge the patient before deleting this bed.", "danger")
        return redirect(url_for("bed_list"))
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM BED WHERE bed_id = ?", (bed_id,))
        flash("Bed deleted.", "success")
    except sqlite3.IntegrityError as exc:
        flash(integrity_message(exc), "danger")
    return redirect(url_for("bed_list"))


# ---------------------------------------------------------------------------
# Viva queries
# ---------------------------------------------------------------------------

@app.route("/queries")
def viva_queries():
    results = []
    conn = get_db()
    for query in load_viva_queries():
        try:
            cursor = conn.execute(query["sql"])
            fetched = cursor.fetchall()
            columns = [item[0] for item in cursor.description] if cursor.description else []
            error = None
        except sqlite3.Error as exc:
            fetched = []
            columns = []
            error = str(exc)
        results.append({**query, "columns": columns, "rows": fetched, "error": error})
    return render_template("queries.html", page_title="SQL queries", queries=results)


@app.errorhandler(404)
def missing(_error):
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("404.html", page_title="Not found"), 404


if "--reset" in sys.argv and os.path.exists(DB_PATH):
    os.remove(DB_PATH)

init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8741"))
    print("Sanjeevani Hospital Management System")
    print(f"  Open http://127.0.0.1:{port}")
    print("  admin / admin123    administrator")
    print("  doctor / doctor123  Dr. Ananya Sharma")
    print("  nurse / nurse123    ward nurse")
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
