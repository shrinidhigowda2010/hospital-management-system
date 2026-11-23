# Hospital Management System

Sanjeevani Multispeciality Hospital, Bengaluru. This is a DBMS case-study app: a Flask website in front of a SQLite database. The SQL is written out in `schema.sql` and in `app.py`, not hidden behind an ORM.

The register covers patients, doctors, departments, appointments, billing, pharmacy, and admissions. Sample rows use Indian names, cities, and rupee amounts.

## Run it

You need Python 3.10 or newer.

```bash
pip install -r requirements.txt
python app.py
```

On Linux, if `python` is missing, use `python3` for both commands.

Then open [http://127.0.0.1:8741](http://127.0.0.1:8741).

The first launch creates `hospital.db` by running `schema.sql` (tables, views, sample rows, and the viva queries). Bootstrap and Chart.js are already in `static/vendor`, so the pages do not need a CDN.

To wipe the database and load the sample again:

```bash
python app.py --reset
```

## Classroom logins

Passwords are stored as PBKDF2 hashes in `APP_USER`. The plain passwords below are only for this demo.

| Role | Username | Password | Who |
| --- | --- | --- | --- |
| Admin | `admin` | `admin123` | Asha Menon, full access |
| Doctor | `doctor` | `doctor123` | Dr. Ananya Sharma, Cardiology |
| Nurse | `nurse` | `nurse123` | Geeta Rao, ward nurse |

What each role can change:

| | Admin | Doctor | Nurse |
| --- | --- | --- | --- |
| View every module and the SQL page | yes | yes | yes |
| Register and edit patients | yes | | yes |
| Delete patients | yes | | |
| Doctors, departments, wards | yes | | |
| Book and edit appointments | yes | | yes |
| Update appointment status | yes | own appointments | yes |
| Bills and payments | yes | | yes |
| Medicines and beds | yes | | yes |
| Prescriptions | yes | yes, as themselves | |
| Admit, edit, discharge | yes | | yes |

## Where to click

The side menu is the whole hospital.

- **Dashboard** shows total patients, current admissions, appointments, and billing. The two charts read the database: bed occupancy from `V_BED_OCCUPANCY`, and monthly billed versus collected from `BILL` and `PAYMENT`. Open “SQL behind this dashboard” to see the statements.
- **Patients** is registration plus contact (phone, address). Open a name for the history: notes, appointments, stays, prescriptions, and bills.
- **Doctors** holds name, speciality, phone, and `dept_id`. The doctor page is the weekly schedule. Slots on the appointment form come from those hours.
- **Departments** are name and location. Several doctors can share one department.
- **Appointments** book a patient with a doctor, date, time, and status (`Scheduled`, `In Queue`, `Completed`, `Cancelled`). The queue column is computed with `ROW_NUMBER()`. It is not stored, so a cancellation cannot leave a stale number.
- **Billing** is the invoice. Charges are rows in `BILL_ITEM`. `BILL.amount` is their sum. `BILL.status` is `Unpaid`, `Partial`, or `Paid`.
- **Pharmacy** has the medicine shelf and prescriptions. The prescription form uses dropdowns of existing patients, doctors, and medicines. Those dropdowns are foreign keys. Saving inserts one `PRESCRIPTION` row and one `PRESCRIPTION_MEDICINE` row per medicine, and reduces `MEDICINE.stock_qty`, in a single transaction. If any medicine is short, the whole prescription is rolled back.
- **Admissions** lists stays, wards, and beds. Admitting a patient inserts the stay and marks the bed `Occupied` together. Discharge sets `Discharged`, fills `discharge_date`, and frees the bed together.
- **SQL queries** runs the join examples from the bottom of `schema.sql` and shows the result tables. That page is the one to open in a viva.

## Schema

Core tables, same columns as the case study:

```text
PATIENT(patient_id, name, age, gender, phone, address)
DOCTOR(doctor_id, name, speciality, phone, dept_id)
DEPARTMENT(dept_id, dept_name, location)
APPOINTMENT(appt_id, patient_id, doctor_id, date, time, status)
BILL(bill_id, patient_id, amount, date, status)
```

The other modules need their own tables. Putting them in the five tables above would repeat groups or duplicate facts.

```text
DOCTOR_SCHEDULE(schedule_id, doctor_id, day_of_week, start_time, end_time)
PATIENT_HISTORY(history_id, patient_id, doctor_id, visit_date, diagnosis, notes)
BILL_ITEM(item_id, bill_id, description, amount)
PAYMENT(payment_id, bill_id, amount, pay_date, method)
WARD(ward_id, ward_name, ward_type, location)
BED(bed_id, ward_id, bed_number, status)
ADMISSION(admission_id, patient_id, doctor_id, bed_id, admit_date, discharge_date, reason, status)
MEDICINE(medicine_id, name, category, unit_price, stock_qty, expiry_date)
PRESCRIPTION(prescription_id, patient_id, doctor_id, date, diagnosis)
PRESCRIPTION_MEDICINE(prescription_id, medicine_id, dosage, duration, quantity)
APP_USER(user_id, username, password_hash, role, full_name, doctor_id)
```

`PRESCRIPTION_MEDICINE` uses the composite primary key `(prescription_id, medicine_id)`.

Views, which store a query rather than a copy of the rows:

- `V_DOCTOR_DIRECTORY` — doctors with department name and location
- `V_BED_OCCUPANCY` — beds per ward, split by status
- `V_BILL_BALANCE` — each bill with amount paid and balance
- `V_CURRENT_INPATIENTS` — patients in a bed right now

## ER relationships

```text
DEPARTMENT 1 ────< DOCTOR 1 ────< DOCTOR_SCHEDULE
PATIENT    1 ────< APPOINTMENT >──── 1 DOCTOR
PATIENT    1 ────< BILL 1 ────< BILL_ITEM
BILL       1 ────< PAYMENT
PATIENT    1 ────< PRESCRIPTION >──── 1 DOCTOR
PRESCRIPTION 1 ──< PRESCRIPTION_MEDICINE >── 1 MEDICINE
WARD       1 ────< BED 1 ────< ADMISSION >──── 1 PATIENT
ADMISSION >──── 1 DOCTOR
PATIENT    1 ────< PATIENT_HISTORY >──── 0..1 DOCTOR
APP_USER   >──── 0..1 DOCTOR
```

How to read that in a viva:

- One department has many doctors. `DOCTOR.dept_id` references `DEPARTMENT.dept_id`. The department name is not copied onto the doctor.
- One patient has many appointments. Each appointment also points at one doctor.
- One bill has many charges and many payments. The balance is `amount` minus the sum of payments.
- A prescription header points at one patient and one doctor. Each medicine on it is a separate row, pointing at `MEDICINE`.
- A ward has many beds. An admission points at one patient, one doctor, and one bed.
- A partial unique index allows only one `Admitted` row per bed, and only one open stay per patient. Another partial unique index stops two live appointments in the same doctor/date/time slot. A cancelled slot can be booked again.

Primary keys are the `*_id` columns (surrogate keys from `AUTOINCREMENT`), except `PRESCRIPTION_MEDICINE`, whose primary key is the pair of foreign keys.

Foreign keys are enforced. `PRAGMA foreign_keys = ON` runs on every connection. Deleting a patient who still has a bill or an appointment fails, and the screen says why. Line tables (`BILL_ITEM`, `PAYMENT`, `PRESCRIPTION_MEDICINE`, `DOCTOR_SCHEDULE`) use `ON DELETE CASCADE`, so removing the parent removes its lines. History notes also go away with the patient. Doctors, departments, wards, and medicines are `ON DELETE RESTRICT` when something still points at them.

## Normalisation

The design is in third normal form. Here is the argument on these tables, not a generic definition.

**First normal form.** Every column holds a single value, and there are no repeating groups. A prescription does not store `"Paracetamol, ORS"` in one cell. Each medicine is a row in `PRESCRIPTION_MEDICINE` with its own dosage, duration, and quantity. A bill does not have `charge1`, `charge2`, `charge3`. Phone is one number.

**Second normal form.** Second normal form matters when the primary key is composite. In `PRESCRIPTION_MEDICINE` the key is `(prescription_id, medicine_id)`.

- `dosage`, `duration`, and `quantity` depend on both parts. The same medicine can be prescribed differently on another sheet, and one sheet can carry several medicines.
- The medicine’s name and price are not stored in this table. They depend only on `medicine_id`, which would be a partial dependency. They live in `MEDICINE`.

**Third normal form.** No non-key column depends on another non-key column.

- `DOCTOR` stores `dept_id`, not `dept_name` or `location`. If the department name were on the doctor, then `dept_name` would depend on `dept_id`, and `dept_id` is not the doctor’s key. That is a transitive dependency. Renaming a department would mean editing every doctor. Here you edit one `DEPARTMENT` row.
- The same split puts `ward_name` on `WARD`, not on `BED` or `ADMISSION`.
- `BILL.status` is updated from the payments, but the payments themselves are not copied onto the bill as a second list. The status is one attribute of the bill. The balance shown on screen is computed by `V_BILL_BALANCE`, so it cannot drift from the receipt rows.
- The queue number is not a column. It is `ROW_NUMBER() OVER (PARTITION BY doctor_id, date ORDER BY time)`. A stored queue number would be a fact derived from other rows, and it would go stale when someone cancelled. Leaving it out avoids that update anomaly.

`PATIENT.address` stays one column because the case study defines address as a single attribute. The app does not search by city, so splitting a `CITY` table would not remove a dependency we actually use.

## Transactions

SQLite is in autocommit mode until a route opens `BEGIN IMMEDIATE`. The helper is `transaction()` in `app.py`. A raised error runs `ROLLBACK`, so a half-finished change is not left behind.

1. **Payment.** `record_payment()` inserts into `PAYMENT` and then updates `BILL.status` to `Paid`, `Partial`, or `Unpaid`. Both statements commit together. You can see the SQL on the invoice page. Paying more than the balance raises an error and inserts nothing.
2. **Admission and discharge.** Admit inserts `ADMISSION` and sets the bed to `Occupied`. Discharge sets `Discharged`, writes `discharge_date`, and sets the bed back to `Available`. A check constraint requires `discharge_date` to be null exactly when the status is `Admitted`.
3. **Prescription.** The header insert, the medicine rows, and the stock decrease commit together. Editing a prescription puts the old quantities back, then applies the new lines, still inside one transaction. Deleting a prescription returns the stock.

## Show the SQL in a viva

The queries are at the bottom of `schema.sql`, each introduced by `-- @query`. The website runs those exact statements on the SQL queries page.

From the shell, on a copy of the database:

```bash
sqlite3 hospital.db
```

```sql
.headers on
.mode column
SELECT d.name AS doctor, d.speciality, dept.dept_name
FROM DOCTOR AS d
JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id;
```

Useful ones to walk through:

1. Doctors and their departments (equijoin).
2. Appointment register with patient and doctor names (two joins).
3. Queue for 22 Nov 2026, with `ROW_NUMBER()` (the sample Sunday camp).
4. Bills that still have a balance (`LEFT JOIN` plus `GROUP BY` and `HAVING`).
5. Bed occupancy by ward (the dashboard chart).
6. Current inpatients (five-table join).
7. Prescriptions with their medicines (the 1NF split, joined back for display).
8. Patients seen by each department (`COUNT DISTINCT` through the doctor).
9. Amount billed and collected each month (`UNION ALL` of bills and payments).

## Project layout

```text
app.py            routes, SQL, transactions, role checks
schema.sql        CREATE TABLE, sample INSERT, views, viva queries
hospital.db       created on first run (not committed)
templates/        Bootstrap pages
static/           CSS, slot picker, and the vendored Bootstrap/Chart.js files
requirements.txt  Flask
```

This is a classroom system. Change the secret and the passwords before using it for anything real. Set `SECRET_KEY` in the environment if you do.
