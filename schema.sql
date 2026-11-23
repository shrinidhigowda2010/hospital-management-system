-- Sanjeevani Multispeciality Hospital
-- Hospital Management System schema for a DBMS case study.
-- Engine: SQLite. The same file is loaded by the Flask app on first run.
--
-- Core tables match the case-study headings:
--   PATIENT(patient_id, name, age, gender, phone, address)
--   DOCTOR(doctor_id, name, speciality, phone, dept_id)
--   DEPARTMENT(dept_id, dept_name, location)
--   APPOINTMENT(appt_id, patient_id, doctor_id, date, time, status)
--   BILL(bill_id, patient_id, amount, date, status)
--
-- Other modules (ward, bed, admission, medicine, prescription, payment)
-- are separate tables so the design stays in 3NF. See the comments under
-- NORMALISATION and the README.
--
-- ER sketch
--   DEPARTMENT 1──< DOCTOR 1──< DOCTOR_SCHEDULE
--   PATIENT 1──< APPOINTMENT >──1 DOCTOR
--   PATIENT 1──< BILL 1──< BILL_ITEM
--   BILL 1──< PAYMENT
--   PATIENT 1──< PRESCRIPTION >──1 DOCTOR
--   PRESCRIPTION 1──< PRESCRIPTION_MEDICINE >──1 MEDICINE
--   WARD 1──< BED 1──< ADMISSION >──1 PATIENT
--   ADMISSION >──1 DOCTOR
--   PATIENT 1──< PATIENT_HISTORY >──0..1 DOCTOR
--   APP_USER >──0..1 DOCTOR
--
-- NORMALISATION (short version; the README walks through each step)
--   1NF  Every column holds one value. Medicines on a prescription are rows
--        in PRESCRIPTION_MEDICINE, not a comma-separated list.
--   2NF  PRESCRIPTION_MEDICINE is keyed by (prescription_id, medicine_id).
--        dosage, duration, and quantity depend on both. The medicine name
--        does not live here, because it depends only on medicine_id.
--   3NF  DOCTOR stores dept_id, not dept_name. The name and location live in
--        DEPARTMENT, so they are not transitively dependent on doctor_id.
--        The same idea puts ward name on WARD, not on BED or ADMISSION.
--   Queue numbers are NOT stored on APPOINTMENT. They are derived with
--   ROW_NUMBER(), so cancelling a visit cannot leave a stale queue.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

CREATE TABLE DEPARTMENT (
    dept_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    dept_name TEXT    NOT NULL UNIQUE,
    location  TEXT    NOT NULL
);

CREATE TABLE DOCTOR (
    doctor_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    speciality TEXT    NOT NULL,
    phone      TEXT    NOT NULL CHECK (length(phone) >= 10),
    dept_id    INTEGER NOT NULL,
    FOREIGN KEY (dept_id) REFERENCES DEPARTMENT (dept_id) ON DELETE RESTRICT
);

CREATE TABLE DOCTOR_SCHEDULE (
    schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    doctor_id   INTEGER NOT NULL,
    day_of_week TEXT    NOT NULL CHECK (day_of_week IN (
        'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'
    )),
    start_time  TEXT    NOT NULL,
    end_time    TEXT    NOT NULL,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE CASCADE,
    UNIQUE (doctor_id, day_of_week, start_time)
);

CREATE TABLE PATIENT (
    patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    age        INTEGER NOT NULL CHECK (age BETWEEN 1 AND 119),
    gender     TEXT    NOT NULL CHECK (gender IN ('Male', 'Female', 'Other')),
    phone      TEXT    NOT NULL CHECK (length(phone) >= 10),
    address    TEXT    NOT NULL
);

CREATE TABLE PATIENT_HISTORY (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id  INTEGER,
    visit_date TEXT    NOT NULL,
    diagnosis  TEXT    NOT NULL,
    notes      TEXT,
    FOREIGN KEY (patient_id) REFERENCES PATIENT (patient_id) ON DELETE CASCADE,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------------
-- Appointments. status is the only workflow column; the queue is derived.
-- ---------------------------------------------------------------------------

CREATE TABLE APPOINTMENT (
    appt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id  INTEGER NOT NULL,
    date       TEXT    NOT NULL,
    time       TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('Scheduled', 'In Queue', 'Completed', 'Cancelled')),
    FOREIGN KEY (patient_id) REFERENCES PATIENT (patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE RESTRICT
);

-- A cancelled slot can be booked again. A live slot cannot be double-booked.
CREATE UNIQUE INDEX one_live_slot
    ON APPOINTMENT (doctor_id, date, time)
    WHERE status != 'Cancelled';

-- ---------------------------------------------------------------------------
-- Billing. BILL.amount is the invoice total. Line charges live in BILL_ITEM
-- so one bill can carry many charges without a repeating group.
-- PAYMENT is separate so several receipts can clear one invoice.
-- ---------------------------------------------------------------------------

CREATE TABLE BILL (
    bill_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    amount     INTEGER NOT NULL CHECK (amount >= 0),
    date       TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('Unpaid', 'Partial', 'Paid')),
    FOREIGN KEY (patient_id) REFERENCES PATIENT (patient_id) ON DELETE RESTRICT
);

CREATE TABLE BILL_ITEM (
    item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id     INTEGER NOT NULL,
    description TEXT    NOT NULL,
    amount      INTEGER NOT NULL CHECK (amount >= 0),
    FOREIGN KEY (bill_id) REFERENCES BILL (bill_id) ON DELETE CASCADE
);

CREATE TABLE PAYMENT (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id    INTEGER NOT NULL,
    amount     INTEGER NOT NULL CHECK (amount > 0),
    pay_date   TEXT    NOT NULL,
    method     TEXT    NOT NULL CHECK (method IN ('Cash', 'UPI', 'Card', 'Net Banking')),
    FOREIGN KEY (bill_id) REFERENCES BILL (bill_id) ON DELETE CASCADE
);

-- ---------------------------------------------------------------------------
-- Beds and stays. Bed status is changed in the same transaction as the
-- admission or discharge (see the Flask routes).
-- ---------------------------------------------------------------------------

CREATE TABLE WARD (
    ward_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ward_name TEXT    NOT NULL UNIQUE,
    ward_type TEXT    NOT NULL,
    location  TEXT    NOT NULL
);

CREATE TABLE BED (
    bed_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ward_id    INTEGER NOT NULL,
    bed_number TEXT    NOT NULL,
    status     TEXT    NOT NULL CHECK (status IN ('Available', 'Occupied', 'Maintenance')),
    FOREIGN KEY (ward_id) REFERENCES WARD (ward_id) ON DELETE RESTRICT,
    UNIQUE (ward_id, bed_number)
);

CREATE TABLE ADMISSION (
    admission_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id     INTEGER NOT NULL,
    doctor_id      INTEGER NOT NULL,
    bed_id         INTEGER NOT NULL,
    admit_date     TEXT    NOT NULL,
    discharge_date TEXT,
    reason         TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('Admitted', 'Discharged')),
    CHECK (
        (status = 'Admitted' AND discharge_date IS NULL)
        OR (status = 'Discharged' AND discharge_date IS NOT NULL)
    ),
    FOREIGN KEY (patient_id) REFERENCES PATIENT (patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE RESTRICT,
    FOREIGN KEY (bed_id) REFERENCES BED (bed_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX one_current_stay_per_bed
    ON ADMISSION (bed_id) WHERE status = 'Admitted';

CREATE UNIQUE INDEX one_current_stay_per_patient
    ON ADMISSION (patient_id) WHERE status = 'Admitted';

-- ---------------------------------------------------------------------------
-- Pharmacy. Stock lives only on MEDICINE. Dispensing subtracts it in the
-- same transaction that inserts the prescription lines.
-- ---------------------------------------------------------------------------

CREATE TABLE MEDICINE (
    medicine_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    category    TEXT    NOT NULL,
    unit_price  INTEGER NOT NULL CHECK (unit_price >= 0),
    stock_qty   INTEGER NOT NULL CHECK (stock_qty >= 0),
    expiry_date TEXT    NOT NULL
);

CREATE TABLE PRESCRIPTION (
    prescription_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL,
    doctor_id       INTEGER NOT NULL,
    date            TEXT    NOT NULL,
    diagnosis       TEXT    NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES PATIENT (patient_id) ON DELETE RESTRICT,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE RESTRICT
);

-- Composite primary key: one row per medicine on a prescription (2NF example).
CREATE TABLE PRESCRIPTION_MEDICINE (
    prescription_id INTEGER NOT NULL,
    medicine_id     INTEGER NOT NULL,
    dosage          TEXT    NOT NULL,
    duration        TEXT    NOT NULL,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (prescription_id, medicine_id),
    FOREIGN KEY (prescription_id) REFERENCES PRESCRIPTION (prescription_id) ON DELETE CASCADE,
    FOREIGN KEY (medicine_id) REFERENCES MEDICINE (medicine_id) ON DELETE RESTRICT
);

-- Login accounts. Passwords are PBKDF2 hashes, never plain text.
-- doctor_id is set only for doctor logins, so the app can limit that role
-- to the matching DOCTOR row.
CREATE TABLE APP_USER (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'doctor', 'nurse')),
    full_name     TEXT    NOT NULL,
    doctor_id     INTEGER,
    FOREIGN KEY (doctor_id) REFERENCES DOCTOR (doctor_id) ON DELETE RESTRICT
);

CREATE INDEX idx_doctor_dept ON DOCTOR (dept_id);
CREATE INDEX idx_appt_patient ON APPOINTMENT (patient_id);
CREATE INDEX idx_appt_doctor_date ON APPOINTMENT (doctor_id, date);
CREATE INDEX idx_bill_patient ON BILL (patient_id);
CREATE INDEX idx_payment_bill ON PAYMENT (bill_id);
CREATE INDEX idx_bed_ward ON BED (ward_id);
CREATE INDEX idx_admission_patient ON ADMISSION (patient_id);
CREATE INDEX idx_prescription_patient ON PRESCRIPTION (patient_id);

-- ---------------------------------------------------------------------------
-- Views used by the dashboard and the SQL Queries page.
-- A view stores the query text. It does not copy the rows.
-- ---------------------------------------------------------------------------

CREATE VIEW V_DOCTOR_DIRECTORY AS
SELECT d.doctor_id,
       d.name AS doctor,
       d.speciality,
       d.phone,
       dept.dept_name,
       dept.location
FROM DOCTOR AS d
JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id;

CREATE VIEW V_BED_OCCUPANCY AS
SELECT w.ward_id,
       w.ward_name,
       w.location,
       COUNT(b.bed_id) AS total_beds,
       COALESCE(SUM(CASE WHEN b.status = 'Occupied' THEN 1 ELSE 0 END), 0) AS occupied,
       COALESCE(SUM(CASE WHEN b.status = 'Available' THEN 1 ELSE 0 END), 0) AS available,
       COALESCE(SUM(CASE WHEN b.status = 'Maintenance' THEN 1 ELSE 0 END), 0) AS maintenance
FROM WARD AS w
LEFT JOIN BED AS b ON b.ward_id = w.ward_id
GROUP BY w.ward_id, w.ward_name, w.location;

CREATE VIEW V_BILL_BALANCE AS
SELECT b.bill_id,
       b.patient_id,
       p.name AS patient,
       b.amount,
       b.date,
       b.status,
       COALESCE(SUM(pay.amount), 0) AS paid,
       b.amount - COALESCE(SUM(pay.amount), 0) AS balance
FROM BILL AS b
JOIN PATIENT AS p ON p.patient_id = b.patient_id
LEFT JOIN PAYMENT AS pay ON pay.bill_id = b.bill_id
GROUP BY b.bill_id, b.patient_id, p.name, b.amount, b.date, b.status;

CREATE VIEW V_CURRENT_INPATIENTS AS
SELECT ad.admission_id,
       p.patient_id,
       p.name AS patient,
       p.age,
       p.gender,
       d.name AS doctor,
       w.ward_name,
       b.bed_number,
       ad.admit_date,
       ad.reason
FROM ADMISSION AS ad
JOIN PATIENT AS p ON p.patient_id = ad.patient_id
JOIN DOCTOR AS d ON d.doctor_id = ad.doctor_id
JOIN BED AS b ON b.bed_id = ad.bed_id
JOIN WARD AS w ON w.ward_id = b.ward_id
WHERE ad.status = 'Admitted';

-- ---------------------------------------------------------------------------
-- Sample data. Indian names, cities, and rupee amounts. All fictional.
-- medicine.stock_qty is the quantity on the shelf today, after the seeded
-- prescriptions were already dispensed.
-- Explicit primary keys keep the foreign keys easy to read in a viva.
-- ---------------------------------------------------------------------------

INSERT INTO DEPARTMENT (dept_id, dept_name, location) VALUES
    (1, 'Cardiology', 'Block A, 2nd Floor'),
    (2, 'Orthopaedics', 'Block B, 1st Floor'),
    (3, 'Paediatrics', 'Block C, 3rd Floor'),
    (4, 'General Medicine', 'Block A, 1st Floor'),
    (5, 'Obstetrics and Gynaecology', 'Block C, 2nd Floor'),
    (6, 'Neurology', 'Block B, 3rd Floor'),
    (7, 'ENT', 'Block A, Ground Floor'),
    (8, 'Emergency', 'Main Block, Ground Floor');

INSERT INTO DOCTOR (doctor_id, name, speciality, phone, dept_id) VALUES
    (1, 'Ananya Sharma', 'Interventional Cardiology', '9845099001', 1),
    (2, 'Neha Kulkarni', 'Non-invasive Cardiology', '9845099011', 1),
    (3, 'Rajesh Iyer', 'Joint Replacement and Trauma', '9845099002', 2),
    (4, 'Priya Nair', 'Paediatrics', '9845099003', 3),
    (5, 'Mohammed Farhan', 'General Medicine', '9845099004', 4),
    (6, 'Kavita Deshmukh', 'Obstetrics and Gynaecology', '9845099005', 5),
    (7, 'Arjun Reddy', 'Neurology', '9845099006', 6),
    (8, 'Sneha Patil', 'Ear, Nose and Throat', '9845099007', 7),
    (9, 'Vikram Singh', 'Emergency Medicine', '9845099008', 8);

-- OPD for doctors 1-8: Monday to Saturday, 09:00 to 13:00.
INSERT INTO DOCTOR_SCHEDULE (doctor_id, day_of_week, start_time, end_time)
SELECT d.doctor_id, days.day_name, '09:00', '13:00'
FROM DOCTOR AS d
JOIN (
    SELECT 'Monday' AS day_name UNION ALL
    SELECT 'Tuesday' UNION ALL
    SELECT 'Wednesday' UNION ALL
    SELECT 'Thursday' UNION ALL
    SELECT 'Friday' UNION ALL
    SELECT 'Saturday'
) AS days
WHERE d.doctor_id BETWEEN 1 AND 8;

-- Sunday camp on 22 Nov 2026 for three consultants, plus a full emergency roster.
INSERT INTO DOCTOR_SCHEDULE (doctor_id, day_of_week, start_time, end_time) VALUES
    (1, 'Sunday', '09:00', '13:00'),
    (3, 'Sunday', '09:00', '13:00'),
    (6, 'Sunday', '09:00', '13:00');

INSERT INTO DOCTOR_SCHEDULE (doctor_id, day_of_week, start_time, end_time)
SELECT 9, days.day_name, '08:00', '20:00'
FROM (
    SELECT 'Monday' AS day_name UNION ALL
    SELECT 'Tuesday' UNION ALL
    SELECT 'Wednesday' UNION ALL
    SELECT 'Thursday' UNION ALL
    SELECT 'Friday' UNION ALL
    SELECT 'Saturday' UNION ALL
    SELECT 'Sunday'
) AS days;

INSERT INTO PATIENT (patient_id, name, age, gender, phone, address) VALUES
    (1,  'Ramesh Kumar',      58, 'Male',   '9845012345', '14, MG Road, Shivajinagar, Bengaluru, Karnataka'),
    (2,  'Lakshmi Narayanan', 72, 'Female', '9444123780', '22, West Masi Street, Madurai, Tamil Nadu'),
    (3,  'Aisha Siddiqui',    34, 'Female', '9892011456', '8, Road No. 12, Banjara Hills, Hyderabad, Telangana'),
    (4,  'Suresh Patel',      46, 'Male',   '9825098123', '101, CG Road, Navrangpura, Ahmedabad, Gujarat'),
    (5,  'Meera Joshi',        9, 'Female', '9811122098', '5, FC Road, Shivajinagar, Pune, Maharashtra'),
    (6,  'Arvind Menon',      63, 'Male',   '9745612309', '77, Marine Drive, Ernakulam, Kochi, Kerala'),
    (7,  'Fatima Begum',      32, 'Female', '9900123678', '19, Charminar Road, Hyderabad, Telangana'),
    (8,  'Harpreet Singh',    41, 'Male',   '9815012789', '33, Model Town, Ludhiana, Punjab'),
    (9,  'Divya Krishnan',    27, 'Female', '9847011567', '12, 2nd Avenue, Anna Nagar, Chennai, Tamil Nadu'),
    (10, 'Imran Qureshi',     38, 'Male',   '9935012890', '45, Hazratganj, Lucknow, Uttar Pradesh'),
    (11, 'Anjali Deshpande',  55, 'Female', '9823012670', '9, Deccan Gymkhana, Pune, Maharashtra'),
    (12, 'Kiran Reddy',        8, 'Male',   '9700012348', '28, Jubilee Hills, Hyderabad, Telangana'),
    (13, 'Savitri Devi',      80, 'Female', '9414012567', '3, MI Road, Jaipur, Rajasthan'),
    (14, 'Nikhil Bansal',     33, 'Male',   '9810012456', '16, Connaught Place, New Delhi'),
    (15, 'Pooja Nair',        29, 'Female', '9895012344', '41, Panampilly Nagar, Kochi, Kerala'),
    (16, 'Abdul Rahman',      67, 'Male',   '9846012788', '7, Mosque Road, Frazer Town, Bengaluru, Karnataka'),
    (17, 'Shweta Kulkarni',   26, 'Female', '9822012599', '55, Law College Road, Pune, Maharashtra'),
    (18, 'Manoj Tiwari',      52, 'Male',   '9415012677', '18, Ashok Marg, Lucknow, Uttar Pradesh');

INSERT INTO APPOINTMENT (appt_id, patient_id, doctor_id, date, time, status) VALUES
    (1,  10, 9, '2026-11-22', '09:00', 'In Queue'),
    (2,  6,  9, '2026-11-22', '09:30', 'In Queue'),
    (3,  3,  1, '2026-11-22', '10:00', 'Scheduled'),
    (4,  11, 1, '2026-11-22', '10:30', 'Completed'),
    (5,  14, 3, '2026-11-22', '11:00', 'Scheduled'),
    (6,  17, 6, '2026-11-22', '11:30', 'Scheduled'),
    (7,  4,  5, '2026-11-23', '09:30', 'Scheduled'),
    (8,  8,  3, '2026-11-23', '10:00', 'Scheduled'),
    (9,  15, 8, '2026-11-23', '11:00', 'Cancelled'),
    (10, 9,  8, '2026-11-24', '11:00', 'Scheduled'),
    (11, 18, 2, '2026-11-25', '09:00', 'Scheduled'),
    (12, 7,  6, '2026-11-21', '10:00', 'Completed'),
    (13, 2,  7, '2026-11-18', '10:00', 'Completed'),
    (14, 16, 5, '2026-11-15', '09:30', 'Completed'),
    (15, 17, 6, '2026-10-12', '10:30', 'Completed'),
    (16, 12, 4, '2026-10-05', '11:00', 'Completed'),
    (17, 18, 2, '2026-09-20', '09:00', 'Completed'),
    (18, 10, 7, '2026-09-08', '12:30', 'Completed'),
    (19, 13, 3, '2026-08-22', '10:00', 'Completed'),
    (20, 15, 8, '2026-08-14', '09:30', 'Completed'),
    (21, 5,  4, '2026-07-18', '10:00', 'Completed'),
    (22, 14, 3, '2026-06-18', '09:30', 'Completed');

INSERT INTO WARD (ward_id, ward_name, ward_type, location) VALUES
    (1, 'General Ward', 'General', 'Block D, 1st Floor'),
    (2, 'ICU', 'Intensive Care', 'Block D, 2nd Floor'),
    (3, 'Maternity Ward', 'Maternity', 'Block C, 2nd Floor'),
    (4, 'Paediatric Ward', 'Paediatric', 'Block C, 3rd Floor'),
    (5, 'Private Rooms', 'Private', 'Block A, 4th Floor');

INSERT INTO BED (bed_id, ward_id, bed_number, status) VALUES
    (1,  1, 'G-01',  'Occupied'),
    (2,  1, 'G-02',  'Occupied'),
    (3,  1, 'G-03',  'Available'),
    (4,  1, 'G-04',  'Available'),
    (5,  1, 'G-05',  'Available'),
    (6,  1, 'G-06',  'Available'),
    (7,  1, 'G-07',  'Available'),
    (8,  1, 'G-08',  'Available'),
    (9,  2, 'I-01',  'Occupied'),
    (10, 2, 'I-02',  'Occupied'),
    (11, 2, 'I-03',  'Available'),
    (12, 2, 'I-04',  'Maintenance'),
    (13, 3, 'M-01',  'Occupied'),
    (14, 3, 'M-02',  'Occupied'),
    (15, 3, 'M-03',  'Available'),
    (16, 3, 'M-04',  'Available'),
    (17, 4, 'P-01',  'Occupied'),
    (18, 4, 'P-02',  'Occupied'),
    (19, 4, 'P-03',  'Available'),
    (20, 4, 'P-04',  'Available'),
    (21, 5, 'PR-01', 'Occupied'),
    (22, 5, 'PR-02', 'Available'),
    (23, 5, 'PR-03', 'Occupied'),
    (24, 5, 'PR-04', 'Available');

INSERT INTO ADMISSION (
    admission_id, patient_id, doctor_id, bed_id, admit_date, discharge_date, reason, status
) VALUES
    (1,  1,  1, 9,  '2026-11-20', NULL,         'Acute chest pain, rule out acute coronary syndrome', 'Admitted'),
    (2,  2,  7, 1,  '2026-11-18', NULL,         'Ischaemic stroke, inpatient rehabilitation', 'Admitted'),
    (3,  5,  4, 17, '2026-11-22', NULL,         'Dengue fever with warning signs', 'Admitted'),
    (4,  6,  9, 10, '2026-11-22', NULL,         'Acute breathlessness', 'Admitted'),
    (5,  7,  6, 13, '2026-11-21', NULL,         'Postnatal observation', 'Admitted'),
    (6,  12, 4, 18, '2026-11-22', NULL,         'Acute asthma', 'Admitted'),
    (7,  13, 3, 21, '2026-11-15', NULL,         'Hip fracture, post-operative care', 'Admitted'),
    (8,  16, 5, 2,  '2026-11-19', NULL,         'Community-acquired pneumonia', 'Admitted'),
    (9,  17, 6, 14, '2026-11-22', NULL,         'Reduced foetal movement, observation', 'Admitted'),
    (10, 18, 2, 23, '2026-11-21', NULL,         'Unstable angina', 'Admitted'),
    (11, 4,  5, 3,  '2026-11-02', '2026-11-06', 'Viral fever', 'Discharged'),
    (12, 11, 2, 22, '2026-10-20', '2026-10-24', 'Angina workup', 'Discharged'),
    (13, 8,  3, 4,  '2026-09-12', '2026-09-16', 'Closed fracture of the radius, conservative care', 'Discharged'),
    (14, 9,  8, 5,  '2026-08-01', '2026-08-03', 'Acute tonsillitis', 'Discharged'),
    (15, 10, 7, 6,  '2026-09-06', '2026-09-09', 'Seizure evaluation', 'Discharged'),
    (16, 15, 5, 7,  '2026-07-02', '2026-07-04', 'Acute gastroenteritis', 'Discharged');

INSERT INTO MEDICINE (medicine_id, name, category, unit_price, stock_qty, expiry_date) VALUES
    (1,  'Paracetamol 500 mg tablet', 'Analgesic',         2, 140, '2027-08-31'),
    (2,  'Amoxicillin 500 mg capsule', 'Antibiotic',       8,  86, '2027-04-30'),
    (3,  'Azithromycin 500 mg tablet', 'Antibiotic',      18,  16, '2027-02-28'),
    (4,  'Metformin 500 mg tablet', 'Antidiabetic',        4, 210, '2027-09-30'),
    (5,  'Amlodipine 5 mg tablet', 'Antihypertensive',     3,  24, '2027-06-30'),
    (6,  'Cetirizine 10 mg tablet', 'Antihistamine',       2,  18, '2027-12-31'),
    (7,  'Pantoprazole 40 mg tablet', 'Gastric',           6,  64, '2027-05-31'),
    (8,  'Ibuprofen 400 mg tablet', 'Analgesic',           3,  12, '2026-12-20'),
    (9,  'ORS sachet', 'Rehydration',                      8,  90, '2027-11-30'),
    (10, 'Insulin Glargine pen', 'Antidiabetic',         320,   9, '2027-03-31'),
    (11, 'Salbutamol inhaler', 'Respiratory',           125,  14, '2027-07-31'),
    (12, 'Cough syrup 100 ml', 'Cough',                  65,  40, '2027-01-31');

INSERT INTO PRESCRIPTION (prescription_id, patient_id, doctor_id, date, diagnosis) VALUES
    (1,  11, 1, '2026-11-22', 'Stable angina'),
    (2,  5,  4, '2026-11-22', 'Dengue fever'),
    (3,  7,  6, '2026-11-21', 'Postnatal care'),
    (4,  2,  7, '2026-11-18', 'Ischaemic stroke, secondary prevention'),
    (5,  16, 5, '2026-11-15', 'Community-acquired pneumonia'),
    (6,  17, 6, '2026-10-12', 'Antenatal acidity'),
    (7,  12, 4, '2026-10-05', 'Acute asthma'),
    (8,  18, 2, '2026-09-20', 'Type 2 diabetes mellitus'),
    (9,  15, 8, '2026-08-14', 'Allergic rhinitis'),
    (10, 6,  9, '2026-11-22', 'Acute wheeze'),
    (11, 13, 3, '2026-11-16', 'Post-operative pain');

INSERT INTO PRESCRIPTION_MEDICINE (prescription_id, medicine_id, dosage, duration, quantity) VALUES
    (1,  5,  '1 tablet in the morning', '30 days', 30),
    (1,  7,  '1 tablet before breakfast', '15 days', 15),
    (2,  1,  '1 tablet three times a day after food', '5 days', 15),
    (2,  9,  '1 sachet in a glass of water after loose stools', '3 days', 10),
    (3,  1,  '1 tablet twice a day after food', '3 days', 6),
    (3,  7,  '1 tablet before breakfast', '10 days', 10),
    (4,  5,  '1 tablet in the morning', '30 days', 30),
    (5,  2,  '1 capsule three times a day', '7 days', 21),
    (5,  1,  '1 tablet three times a day if fever', '5 days', 10),
    (6,  7,  '1 tablet before breakfast', '14 days', 14),
    (7,  11, '2 puffs when wheezy', '30 days', 1),
    (7,  6,  '1 tablet at night', '5 days', 5),
    (8,  4,  '1 tablet morning and night after food', '30 days', 60),
    (9,  6,  '1 tablet at night', '10 days', 10),
    (10, 11, '2 puffs every 6 hours', '5 days', 1),
    (10, 1,  '1 tablet three times a day if fever', '3 days', 10),
    (11, 8,  '1 tablet twice a day after food', '5 days', 10),
    (11, 7,  '1 tablet before breakfast', '5 days', 5);

INSERT INTO BILL (bill_id, patient_id, amount, date, status) VALUES
    (1,  1,  16500, '2026-11-22', 'Unpaid'),
    (2,  11,  1400, '2026-11-22', 'Paid'),
    (3,  6,   2500, '2026-11-22', 'Partial'),
    (4,  16, 12000, '2026-11-19', 'Paid'),
    (5,  2,   6000, '2026-11-18', 'Unpaid'),
    (6,  13, 40000, '2026-11-16', 'Partial'),
    (7,  5,   2200, '2026-11-22', 'Unpaid'),
    (8,  4,   3400, '2026-11-06', 'Paid'),
    (9,  17,  1800, '2026-10-12', 'Paid'),
    (10, 12,   900, '2026-10-05', 'Paid'),
    (11, 18,  4200, '2026-09-20', 'Paid'),
    (12, 10,  2800, '2026-09-08', 'Paid'),
    (13, 15,   700, '2026-08-14', 'Paid'),
    (14, 8,   5600, '2026-09-14', 'Paid'),
    (15, 9,   1100, '2026-07-30', 'Unpaid'),
    (16, 3,    500, '2026-08-02', 'Paid'),
    (17, 7,  15000, '2026-11-21', 'Partial'),
    (18, 14,  2500, '2026-06-18', 'Paid');

INSERT INTO BILL_ITEM (bill_id, description, amount) VALUES
    (1,  'Emergency admission', 5000),
    (1,  'Cardiac enzymes and ECG', 3500),
    (1,  'ICU bed, 2 days', 8000),
    (2,  'Cardiology consultation', 800),
    (2,  'ECG', 600),
    (3,  'Emergency consultation', 2000),
    (3,  'Nebulisation', 500),
    (4,  'Inpatient general medicine', 7000),
    (4,  'Antibiotics and laboratory tests', 5000),
    (5,  'Neurology consultation', 1500),
    (5,  'CT brain', 4500),
    (6,  'Fracture fixation', 28000),
    (6,  'Implant', 8000),
    (6,  'Private room, 2 days', 4000),
    (7,  'Paediatric inpatient care', 1500),
    (7,  'Dengue panel', 700),
    (8,  'General ward, 4 days', 2400),
    (8,  'Laboratory tests', 1000),
    (9,  'Antenatal consultation', 800),
    (9,  'Ultrasound', 1000),
    (10, 'Paediatric consultation', 500),
    (10, 'Nebulisation', 400),
    (11, 'Cardiology consultation', 800),
    (11, 'Treadmill test', 2200),
    (11, 'Lipid profile', 1200),
    (12, 'Neurology consultation', 1500),
    (12, 'EEG', 1300),
    (13, 'ENT consultation', 700),
    (14, 'Orthopaedics consultation', 800),
    (14, 'X-ray and cast', 1800),
    (14, 'Ward charges', 3000),
    (15, 'ENT consultation', 700),
    (15, 'Audiometry', 400),
    (16, 'General medicine consultation', 500),
    (17, 'Delivery package', 12000),
    (17, 'Maternity ward charges', 3000),
    (18, 'Orthopaedics consultation', 800),
    (18, 'Knee X-ray', 700),
    (18, 'Physiotherapy', 1000);

INSERT INTO PAYMENT (payment_id, bill_id, amount, pay_date, method) VALUES
    (1,  2,  1400, '2026-11-22', 'UPI'),
    (2,  3,  1000, '2026-11-22', 'Cash'),
    (3,  4, 12000, '2026-11-21', 'Card'),
    (4,  6, 20000, '2026-11-18', 'Net Banking'),
    (5,  8,  3400, '2026-11-06', 'Cash'),
    (6,  9,  1800, '2026-10-12', 'UPI'),
    (7,  10,  900, '2026-10-05', 'UPI'),
    (8,  11, 4200, '2026-09-20', 'Card'),
    (9,  12, 2800, '2026-09-08', 'Cash'),
    (10, 13,  700, '2026-08-14', 'UPI'),
    (11, 14, 5600, '2026-09-16', 'Card'),
    (12, 16,  500, '2026-08-02', 'UPI'),
    (13, 17, 8000, '2026-11-21', 'UPI'),
    (14, 18, 2500, '2026-06-18', 'Cash');

INSERT INTO PATIENT_HISTORY (history_id, patient_id, doctor_id, visit_date, diagnosis, notes) VALUES
    (1, 1,  1, '2026-11-20', 'Acute chest pain, rule out acute coronary syndrome',
        'Pain radiating to the left arm since morning. ECG ordered. Admitted to ICU.'),
    (2, 13, 3, '2026-11-15', 'Intertrochanteric fracture of the right femur',
        'Fell in the bathroom. Posted for fixation. Private room arranged.'),
    (3, 5,  4, '2026-11-22', 'Dengue fever with warning signs',
        'NS1 antigen positive. Platelets to be checked daily. ORS started.'),
    (4, 18, 2, '2026-09-20', 'Type 2 diabetes mellitus',
        'HbA1c 8.1 percent. Diet counselling given. Metformin continued.'),
    (5, 16, 5, '2026-11-19', 'Community-acquired pneumonia',
        'Fever and cough for four days. Started on amoxicillin and admitted to the general ward.'),
    (6, 7,  6, '2026-11-21', 'Postnatal day 1',
        'Normal delivery. Mother and baby stable. Watching for fever.');

-- Classroom logins. The hash is PBKDF2-HMAC-SHA256 of the password.
--   admin  / admin123    Asha Menon, administrator
--   doctor / doctor123   Dr. Ananya Sharma
--   nurse  / nurse123    Geeta Rao, ward nurse
INSERT INTO APP_USER (user_id, username, password_hash, role, full_name, doctor_id) VALUES
    (1, 'admin',
     'pbkdf2:sha256:1000000$dNg5X56Z6ht0embc$ad3fc810263047f3b326c6a6c6df370702dc3ac3d5ee04c66289b5c4bf3a5a2f',
     'admin', 'Asha Menon', NULL),
    (2, 'doctor',
     'pbkdf2:sha256:1000000$1vR7foyplvBsGtGq$3aacd151b4ea53b2775dd2640def52fa38e4b9a6b3c6289adfe0db3153688027',
     'doctor', 'Ananya Sharma', 1),
    (3, 'nurse',
     'pbkdf2:sha256:1000000$VIeyLRCUBc4EGj96$2a26d0a606861a6b0bd412a834919a0d8ecf4bcca0cae215e9ce876370f12782',
     'nurse', 'Geeta Rao', NULL);

-- ---------------------------------------------------------------------------
-- VIVA QUERIES
-- The app reads every block that starts with "-- @query".
-- You can also paste one into the sqlite3 shell:
--   sqlite3 hospital.db
--   .headers on
--   .mode column
-- ---------------------------------------------------------------------------

-- @query doctors_departments
-- @title Doctors and their departments
-- @about DOCTOR stores dept_id, not the department name. This equijoin brings back dept_name and location. One department has many doctors.
SELECT d.doctor_id,
       d.name AS doctor,
       d.speciality,
       dept.dept_name,
       dept.location
FROM DOCTOR AS d
JOIN DEPARTMENT AS dept ON dept.dept_id = d.dept_id
ORDER BY dept.dept_name, d.name;

-- @query appointment_register
-- @title Appointment register with patient and doctor names
-- @about APPOINTMENT stores only patient_id and doctor_id. Two joins turn those foreign keys into names.
SELECT a.appt_id,
       p.name AS patient,
       d.name AS doctor,
       a.date,
       a.time,
       a.status
FROM APPOINTMENT AS a
JOIN PATIENT AS p ON p.patient_id = a.patient_id
JOIN DOCTOR AS d ON d.doctor_id = a.doctor_id
ORDER BY a.date DESC, a.time;

-- @query sunday_queue
-- @title Queue for the 22 Nov 2026 OPD camp
-- @about Queue number is derived with ROW_NUMBER, not stored. Cancelling a row cannot leave a stale number behind. Change the date literal to another clinic day if you want.
SELECT a.appt_id,
       d.name AS doctor,
       p.name AS patient,
       a.time,
       a.status,
       ROW_NUMBER() OVER (PARTITION BY a.doctor_id ORDER BY a.time) AS queue_no
FROM APPOINTMENT AS a
JOIN PATIENT AS p ON p.patient_id = a.patient_id
JOIN DOCTOR AS d ON d.doctor_id = a.doctor_id
WHERE a.date = '2026-11-22'
  AND a.status != 'Cancelled'
ORDER BY d.name, a.time;

-- @query outstanding_bills
-- @title Bills that still have a balance
-- @about PAYMENT is its own table, so one bill can have many receipts. The left join keeps unpaid bills, which have no payment row. HAVING drops bills that are fully paid.
SELECT b.bill_id,
       p.name AS patient,
       b.amount,
       b.date,
       b.status,
       COALESCE(SUM(pay.amount), 0) AS paid,
       b.amount - COALESCE(SUM(pay.amount), 0) AS balance
FROM BILL AS b
JOIN PATIENT AS p ON p.patient_id = b.patient_id
LEFT JOIN PAYMENT AS pay ON pay.bill_id = b.bill_id
GROUP BY b.bill_id, p.name, b.amount, b.date, b.status
HAVING b.amount - COALESCE(SUM(pay.amount), 0) > 0
ORDER BY b.date;

-- @query bed_occupancy
-- @title Bed occupancy by ward
-- @about This is the query behind the dashboard chart. The view V_BED_OCCUPANCY is defined above; selecting from a view runs that query, it does not read a second copy of the rows.
SELECT ward_name, location, total_beds, occupied, available, maintenance
FROM V_BED_OCCUPANCY
ORDER BY ward_name;

-- @query current_inpatients
-- @title Patients currently in a bed
-- @about Five tables meet here: the stay, the person, the consultant, the bed, and the ward. V_CURRENT_INPATIENTS is the same join saved as a view.
SELECT ad.admission_id,
       p.name AS patient,
       d.name AS doctor,
       w.ward_name,
       b.bed_number,
       ad.admit_date,
       ad.reason
FROM ADMISSION AS ad
JOIN PATIENT AS p ON p.patient_id = ad.patient_id
JOIN DOCTOR AS d ON d.doctor_id = ad.doctor_id
JOIN BED AS b ON b.bed_id = ad.bed_id
JOIN WARD AS w ON w.ward_id = b.ward_id
WHERE ad.status = 'Admitted'
ORDER BY w.ward_name, b.bed_number;

-- @query prescription_sheet
-- @title Prescriptions and the medicines on them
-- @about Packing several medicines into one PRESCRIPTION column would break 1NF. Each medicine is a row in PRESCRIPTION_MEDICINE. This join rebuilds the sheet the doctor signed.
SELECT pr.prescription_id,
       pr.date,
       p.name AS patient,
       d.name AS doctor,
       pr.diagnosis,
       m.name AS medicine,
       pm.dosage,
       pm.duration,
       pm.quantity
FROM PRESCRIPTION AS pr
JOIN PATIENT AS p ON p.patient_id = pr.patient_id
JOIN DOCTOR AS d ON d.doctor_id = pr.doctor_id
JOIN PRESCRIPTION_MEDICINE AS pm ON pm.prescription_id = pr.prescription_id
JOIN MEDICINE AS m ON m.medicine_id = pm.medicine_id
ORDER BY pr.date DESC, pr.prescription_id, m.name;

-- @query patients_by_department
-- @title Patients seen by each department
-- @about A patient is not stored inside a department. The count travels through APPOINTMENT and the doctor dept_id. COUNT DISTINCT avoids counting the same patient twice.
SELECT dept.dept_name,
       COUNT(DISTINCT a.patient_id) AS patients_seen
FROM DEPARTMENT AS dept
JOIN DOCTOR AS d ON d.dept_id = dept.dept_id
LEFT JOIN APPOINTMENT AS a ON a.doctor_id = d.doctor_id
    AND a.status != 'Cancelled'
GROUP BY dept.dept_id, dept.dept_name
ORDER BY patients_seen DESC, dept.dept_name;

-- @query monthly_accounts
-- @title Amount billed and collected each month
-- @about BILL.date and PAYMENT.pay_date are different facts. UNION ALL stacks them, then GROUP BY month totals each side. Recording a payment and updating BILL.status is the transaction on the invoice screen.
SELECT month,
       SUM(billed) AS billed,
       SUM(collected) AS collected
FROM (
    SELECT substr(date, 1, 7) AS month, amount AS billed, 0 AS collected
    FROM BILL
    UNION ALL
    SELECT substr(pay_date, 1, 7) AS month, 0 AS billed, amount AS collected
    FROM PAYMENT
) AS movements
GROUP BY month
ORDER BY month;
