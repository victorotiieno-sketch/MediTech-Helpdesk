from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import sqlite3
import os
import shutil
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.secret_key = "meditech-secure-key-2026"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "database.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")

os.makedirs(BACKUP_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


SLA_TARGETS = {
    "Critical": 4,
    "High": 8,
    "Medium": 24,
    "Low": 72
}


def calculate_sla(created_at, priority, status):
    target_hours = SLA_TARGETS.get(priority, 24)

    if not created_at:
        return {
            "age_hours": 0,
            "age_text": "Unknown",
            "target_hours": target_hours,
            "remaining_hours": 0,
            "sla_status": "Unknown"
        }

    try:
        created_time = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return {
            "age_hours": 0,
            "age_text": "Unknown",
            "target_hours": target_hours,
            "remaining_hours": 0,
            "sla_status": "Unknown"
        }

    age_seconds = max(0, (datetime.now() - created_time).total_seconds())
    age_hours = age_seconds / 3600
    remaining_hours = target_hours - age_hours

    if status in ("Resolved", "Closed"):
        sla_status = "Completed"
    elif age_hours >= target_hours:
        sla_status = "Overdue"
    elif age_hours >= target_hours * 0.75:
        sla_status = "At Risk"
    else:
        sla_status = "On Track"

    if age_hours < 1:
        age_text = f"{int(age_seconds // 60)} min"
    elif age_hours < 24:
        age_text = f"{int(age_hours)} hr"
    else:
        days = int(age_hours // 24)
        hours = int(age_hours % 24)
        age_text = f"{days}d {hours}h" if hours else f"{days}d"

    return {
        "age_hours": round(age_hours, 2),
        "age_text": age_text,
        "target_hours": target_hours,
        "remaining_hours": round(remaining_hours, 2),
        "sla_status": sla_status
    }


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT DEFAULT 'ICT',
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            priority TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open',
            department TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            assigned_to INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (created_by) REFERENCES users (id),
            FOREIGN KEY (assigned_to) REFERENCES users (id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ticket_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            notification_type TEXT NOT NULL DEFAULT 'info',
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (ticket_id) REFERENCES tickets (id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_tag TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            equipment_type TEXT NOT NULL,
            manufacturer TEXT,
            model TEXT,
            serial_number TEXT,
            department TEXT,
            assigned_to INTEGER,
            location TEXT,
            purchase_date TEXT,
            warranty_expiry TEXT,
            status TEXT NOT NULL DEFAULT 'Active',
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (assigned_to) REFERENCES users (id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            location TEXT,
            contact TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS priorities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sla_hours INTEGER NOT NULL DEFAULT 24,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS issue_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT,
            description TEXT,
            active INTEGER DEFAULT 1
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS equipment_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            active INTEGER DEFAULT 1
        )
    """)


    existing_user = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        ("admin",)
    ).fetchone()

    if not existing_user:
        password = generate_password_hash("admin123")

        conn.execute("""
            INSERT INTO users (
                username,
                password,
                full_name,
                role,
                department,
                active
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "admin",
            password,
            "System Administrator",
            "Administrator",
            "ICT",
            1
        ))

    departments = [
        "ICT",
        "Emergency",
        "Outpatient",
        "Laboratory",
        "Pharmacy",
        "Radiology",
        "Finance",
        "Human Resources",
        "Medical Records",
        "Nursing",
        "Administration"
    ]

    for department in departments:
        conn.execute(
            "INSERT OR IGNORE INTO departments (name) VALUES (?)",
            (department,)
        )

    conn.commit()
    conn.close()


init_db()


def log_activity(ticket_id, user_id, action, details):
    conn = get_db()

    conn.execute("""
        INSERT INTO ticket_activity (
            ticket_id,
            user_id,
            action,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        ticket_id,
        user_id,
        action,
        details,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()


def create_notification(user_id, ticket_id, title, message, notification_type="info"):
    conn = get_db()

    conn.execute("""
        INSERT INTO notifications (
            user_id,
            ticket_id,
            title,
            message,
            notification_type,
            is_read,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        ticket_id,
        title,
        message,
        notification_type,
        0,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()


def login_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return decorated_function


def admin_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") != "Administrator":
            flash("Administrator access required.", "error")
            return redirect(url_for("dashboard"))

        return function(*args, **kwargs)

    return decorated_function


def technician_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))

        if session.get("role") not in ["Administrator", "ICT Technician"]:
            flash("Technician access required.", "error")
            return redirect(url_for("dashboard"))

        return function(*args, **kwargs)

    return decorated_function


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            flash("Please enter your username and password.", "error")
            return redirect(url_for("login"))

        conn = get_db()

        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()

        if user and user["active"] == 1 and check_password_hash(
            user["password"],
            password
        ):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            session["department"] = user["department"]

            if user["role"] == "ICT Technician":
                return redirect(url_for("technician_dashboard"))

            return redirect(url_for("dashboard"))

        if user and user["active"] == 0:
            flash("This account has been deactivated.", "error")
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    conn = get_db()
    departments = conn.execute(
        "SELECT * FROM departments ORDER BY name"
    ).fetchall()
    conn.close()

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        department = request.form.get("department", "ICT").strip()

        if not full_name or not username or not password:
            flash("Please complete all account fields.", "error")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing:
            conn.close()
            flash("Username already exists. Please choose another.", "error")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        conn.execute("""
            INSERT INTO users (
                username,
                password,
                full_name,
                role,
                department,
                active
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            username,
            hashed_password,
            full_name,
            "Staff",
            department,
            1
        ))

        conn.commit()
        conn.close()

        flash("Account created successfully. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template(
        "register.html",
        departments=departments,
        full_name="",
        role="Staff"
    )


@app.route("/dashboard")
@login_required
def dashboard():
    if session.get("role") == "ICT Technician":
        return redirect(url_for("technician_dashboard"))

    conn = get_db()

    total = conn.execute(
        "SELECT COUNT(*) FROM tickets"
    ).fetchone()[0]

    open_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status = 'Open'"
    ).fetchone()[0]

    in_progress = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status = 'In Progress'"
    ).fetchone()[0]

    resolved = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE status = 'Resolved'"
    ).fetchone()[0]

    technicians = conn.execute("""
        SELECT COUNT(*) FROM users
        WHERE role = 'ICT Technician'
        AND active = 1
    """).fetchone()[0]

    departments = conn.execute(
        "SELECT COUNT(*) FROM departments"
    ).fetchone()[0]

    recent_tickets = conn.execute("""
        SELECT
            tickets.*,
            creator.full_name AS creator_name,
            technician.full_name AS technician_name
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        LEFT JOIN users technician ON tickets.assigned_to = technician.id
        ORDER BY tickets.id DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        full_name=session["full_name"],
        role=session["role"],
        total=total,
        open_tickets=open_tickets,
        in_progress=in_progress,
        resolved=resolved,
        technicians=technicians,
        departments=departments,
        recent_tickets=recent_tickets
    )


@app.route("/systems-directory")
@login_required
def systems_directory():
    systems = [
        {
            "name": "Access Request System",
            "description": "Submit and track access requests.",
            "url": "http://172.20.0.42:8016/",
            "category": "Operations"
        },
        {
            "name": "AGFA",
            "description": "Access the AGFA clinical imaging system.",
            "url": "https://eisrv01ws.ea.aku.edu/?theme=theme",
            "category": "Clinical Systems"
        },
        {
            "name": "Backup Log Tracker",
            "description": "Review backup activity and logs.",
            "url": "http://172.20.0.42:8004/",
            "category": "Operations"
        },
        {
            "name": "Biomed Helpdesk",
            "description": "Open the biomedical equipment support system.",
            "url": "http://172.20.0.42:8009/",
            "category": "Support"
        },
        {
            "name": "Care Password Reset",
            "description": "Reset Care system credentials.",
            "url": "https://apps.akhskenya.org:9011/generic/care-password-reset",
            "category": "Support"
        },
        {
            "name": "Care Web",
            "description": "Open the Care web application.",
            "url": "https://careweb-05.akhskenya.org:4440/?hostname=",
            "category": "Clinical Systems"
        },
        {
            "name": "Consentra",
            "description": "Access consent management services.",
            "url": "https://apps.akhskenya.org:9023/",
            "category": "Clinical Systems"
        },
        {
            "name": "Contract Management System",
            "description": "Manage hospital contracts and agreements.",
            "url": "http://172.20.0.42:8008/",
            "category": "Operations"
        },
        {
            "name": "CPOE",
            "description": "Open computerized provider order entry.",
            "url": "https://cpoe.akhskenya.org/poe/",
            "category": "Clinical Systems"
        },
        {
            "name": "Credentialing and Privileging",
            "description": "Manage provider credentialing workflows.",
            "url": "https://akhkcnp.com/pages/login.php",
            "category": "Operations"
        }
    ]

    return render_template(
        "systems_directory.html",
        systems=systems,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/technician-dashboard")
@technician_required
def technician_dashboard():
    technician_id = session["user_id"]

    conn = get_db()

    total_assigned = conn.execute("""
        SELECT COUNT(*)
        FROM tickets
        WHERE assigned_to = ?
    """, (technician_id,)).fetchone()[0]

    assigned_open = conn.execute("""
        SELECT COUNT(*)
        FROM tickets
        WHERE assigned_to = ?
        AND status IN ('Open', 'Assigned')
    """, (technician_id,)).fetchone()[0]

    assigned_progress = conn.execute("""
        SELECT COUNT(*)
        FROM tickets
        WHERE assigned_to = ?
        AND status = 'In Progress'
    """, (technician_id,)).fetchone()[0]

    assigned_resolved = conn.execute("""
        SELECT COUNT(*)
        FROM tickets
        WHERE assigned_to = ?
        AND status = 'Resolved'
    """, (technician_id,)).fetchone()[0]

    critical_tickets = conn.execute("""
        SELECT COUNT(*)
        FROM tickets
        WHERE assigned_to = ?
        AND priority = 'Critical'
        AND status NOT IN ('Resolved', 'Closed')
    """, (technician_id,)).fetchone()[0]

    unread_notifications = conn.execute("""
        SELECT COUNT(*)
        FROM notifications
        WHERE user_id = ?
        AND is_read = 0
    """, (technician_id,)).fetchone()[0]

    my_tickets = conn.execute("""
        SELECT
            tickets.*,
            creator.full_name AS creator_name
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        WHERE tickets.assigned_to = ?
        ORDER BY
            CASE tickets.priority
                WHEN 'Critical' THEN 1
                WHEN 'High' THEN 2
                WHEN 'Medium' THEN 3
                WHEN 'Low' THEN 4
            END,
            tickets.id DESC
    """, (technician_id,)).fetchall()

    conn.close()

    return render_template(
        "technician_dashboard.html",
        full_name=session["full_name"],
        role=session["role"],
        department=session["department"],
        total_assigned=total_assigned,
        assigned_open=assigned_open,
        assigned_progress=assigned_progress,
        assigned_resolved=assigned_resolved,
        critical_tickets=critical_tickets,
        unread_notifications=unread_notifications,
        my_tickets=my_tickets
    )


@app.route("/tickets")
@login_required
def tickets():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    department = request.args.get("department", "")
    category = request.args.get("category", "")
    technician = request.args.get("technician", "")

    conn = get_db()

    query = """
        SELECT
            tickets.*,
            creator.full_name AS creator_name,
            technician.full_name AS technician_name
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        LEFT JOIN users technician ON tickets.assigned_to = technician.id
        WHERE 1=1
    """

    parameters = []

    if search:
        if search.startswith("#") and search[1:].isdigit():
            query += " AND tickets.id = ?"
            parameters.append(int(search[1:]))
        elif search.isdigit():
            query += " AND tickets.id = ?"
            parameters.append(int(search))
        else:
            query += """
                AND (
                    tickets.title LIKE ?
                    OR tickets.description LIKE ?
                    OR tickets.category LIKE ?
                )
            """

            search_value = f"%{search}%"

            parameters.extend([
                search_value,
                search_value,
                search_value
            ])

    if status:
        query += " AND tickets.status = ?"
        parameters.append(status)

    if priority:
        query += " AND tickets.priority = ?"
        parameters.append(priority)

    if department:
        query += " AND tickets.department = ?"
        parameters.append(department)

    if category:
        query += " AND tickets.category = ?"
        parameters.append(category)

    if technician:
        if technician == "unassigned":
            query += " AND tickets.assigned_to IS NULL"
        else:
            query += " AND tickets.assigned_to = ?"
            parameters.append(int(technician))

    query += " ORDER BY tickets.id DESC"

    all_tickets = conn.execute(
        query,
        parameters
    ).fetchall()

    tickets_with_sla = []

    for ticket in all_tickets:
        ticket_data = dict(ticket)
        ticket_data["sla"] = calculate_sla(
            ticket["created_at"],
            ticket["priority"],
            ticket["status"]
        )
        tickets_with_sla.append(ticket_data)

    departments = conn.execute(
        "SELECT * FROM departments ORDER BY name"
    ).fetchall()

    technicians = conn.execute("""
        SELECT id, full_name, department
        FROM users
        WHERE role = 'ICT Technician'
        AND active = 1
        ORDER BY full_name
    """).fetchall()

    categories = conn.execute("""
        SELECT DISTINCT category
        FROM tickets
        WHERE category IS NOT NULL
        AND category != ''
        ORDER BY category
    """).fetchall()

    conn.close()

    return render_template(
        "tickets.html",
        tickets=tickets_with_sla,
        departments=departments,
        technicians=technicians,
        categories=categories,
        search=search,
        selected_status=status,
        selected_priority=priority,
        selected_department=department,
        selected_category=category,
        selected_technician=technician,
        ticket_count=len(tickets_with_sla),
        full_name=session["full_name"],
        role=session["role"]
    )

@app.route("/create-ticket", methods=["GET", "POST"])
@login_required
def create_ticket():
    conn = get_db()

    departments = conn.execute(
        "SELECT * FROM departments ORDER BY name"
    ).fetchall()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "")
        priority = request.form.get("priority", "")
        department = request.form.get("department", "")

        if not title or not description or not category or not priority or not department:
            conn.close()
            flash("Please complete all ticket fields.", "error")
            return redirect(url_for("create_ticket"))

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute("""
            INSERT INTO tickets (
                title,
                description,
                category,
                priority,
                status,
                department,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
            description,
            category,
            priority,
            "Open",
            department,
            session["user_id"],
            created_at
        ))

        ticket_id = cursor.lastrowid

        conn.execute("""
            INSERT INTO ticket_activity (
                ticket_id,
                user_id,
                action,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            ticket_id,
            session["user_id"],
            "Ticket Created",
            f"Ticket created with priority {priority} and category {category}.",
            created_at
        ))

        conn.commit()
        conn.close()

        flash("Ticket created successfully.", "success")
        return redirect(url_for("tickets"))

    conn.close()

    return render_template(
        "create_ticket.html",
        departments=departments,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/ticket/<int:ticket_id>")
@login_required
def ticket_details(ticket_id):
    conn = get_db()

    ticket = conn.execute("""
        SELECT
            tickets.*,
            creator.full_name AS creator_name,
            technician.full_name AS technician_name
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        LEFT JOIN users technician ON tickets.assigned_to = technician.id
        WHERE tickets.id = ?
    """, (ticket_id,)).fetchone()

    technicians = conn.execute("""
        SELECT id, full_name
        FROM users
        WHERE role = 'ICT Technician'
        AND active = 1
        ORDER BY full_name
    """).fetchall()

    conn.close()

    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets"))

    sla = calculate_sla(
        ticket["created_at"],
        ticket["priority"],
        ticket["status"]
    )

    return render_template(
        "ticket_details.html",
        ticket=ticket,
        sla=sla,
        technicians=technicians,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/ticket/<int:ticket_id>/activity")
@login_required
def ticket_activity(ticket_id):
    conn = get_db()

    activities = conn.execute("""
        SELECT
            ticket_activity.*,
            users.full_name AS user_name,
            users.role AS user_role
        FROM ticket_activity
        JOIN users ON ticket_activity.user_id = users.id
        WHERE ticket_activity.ticket_id = ?
        ORDER BY ticket_activity.id DESC
    """, (ticket_id,)).fetchall()

    conn.close()

    return render_template(
        "ticket_activity.html",
        activities=activities,
        ticket_id=ticket_id,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/ticket/<int:ticket_id>/status", methods=["POST"])
@login_required
def update_status(ticket_id):
    status = request.form.get("status", "")

    if session.get("role") == "Staff":
        flash("Staff accounts cannot update ticket status.", "error")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    allowed_statuses = [
        "Open",
        "Assigned",
        "In Progress",
        "Pending",
        "Resolved",
        "Closed"
    ]

    if status not in allowed_statuses:
        flash("Invalid ticket status.", "error")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    conn = get_db()

    ticket = conn.execute(
        "SELECT assigned_to FROM tickets WHERE id = ?",
        (ticket_id,)
    ).fetchone()

    if not ticket:
        conn.close()
        flash("Ticket not found.", "error")
        return redirect(url_for("tickets"))

    if session.get("role") == "ICT Technician":
        if ticket["assigned_to"] != session["user_id"]:
            conn.close()
            flash("You can only update tickets assigned to you.", "error")
            return redirect(url_for("technician_dashboard"))

    old_status = conn.execute(
        "SELECT status FROM tickets WHERE id = ?",
        (ticket_id,)
    ).fetchone()

    previous_status = old_status["status"]

    conn.execute(
        "UPDATE tickets SET status = ? WHERE id = ?",
        (status, ticket_id)
    )

    conn.execute("""
        INSERT INTO ticket_activity (
            ticket_id,
            user_id,
            action,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        ticket_id,
        session["user_id"],
        "Status Updated",
        f"Status changed from {previous_status} to {status}.",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))

    conn.commit()
    conn.close()

    flash("Ticket status updated successfully.", "success")

    if session.get("role") == "ICT Technician":
        return redirect(url_for("technician_dashboard"))

    return redirect(
        url_for("ticket_details", ticket_id=ticket_id)
    )


@app.route("/ticket/<int:ticket_id>/assign", methods=["POST"])
@admin_required
def assign_ticket(ticket_id):
    technician_id = request.form.get("technician_id")

    if not technician_id:
        flash("Please select a technician.", "error")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    conn = get_db()

    technician = conn.execute("""
        SELECT id FROM users
        WHERE id = ?
        AND role = 'ICT Technician'
        AND active = 1
    """, (technician_id,)).fetchone()

    if not technician:
        conn.close()
        flash("Invalid or inactive technician selected.", "error")
        return redirect(url_for("ticket_details", ticket_id=ticket_id))

    technician_name = conn.execute(
        "SELECT full_name FROM users WHERE id = ?",
        (technician_id,)
    ).fetchone()["full_name"]

    ticket = conn.execute(
        "SELECT title FROM tickets WHERE id = ?",
        (ticket_id,)
    ).fetchone()

    conn.execute("""
        UPDATE tickets
        SET assigned_to = ?, status = 'Assigned'
        WHERE id = ?
    """, (technician_id, ticket_id))

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("""
        INSERT INTO ticket_activity (
            ticket_id,
            user_id,
            action,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        ticket_id,
        session["user_id"],
        "Ticket Assigned",
        f"Ticket assigned to {technician_name}.",
        created_at
    ))

    conn.execute("""
        INSERT INTO notifications (
            user_id,
            ticket_id,
            title,
            message,
            notification_type,
            is_read,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        technician_id,
        ticket_id,
        "New Ticket Assigned",
        f"Ticket #{ticket_id} - {ticket['title']} has been assigned to you.",
        "ticket",
        0,
        created_at
    ))

    conn.commit()
    conn.close()

    flash("Ticket assigned successfully.", "success")

    return redirect(
        url_for("ticket_details", ticket_id=ticket_id)
    )



@app.route("/configuration")
@admin_required
def configuration():
    conn = get_db()

    facilities = conn.execute(
        "SELECT * FROM facilities ORDER BY name"
    ).fetchall()

    priorities = conn.execute(
        "SELECT * FROM priorities ORDER BY sla_hours"
    ).fetchall()

    categories = conn.execute(
        "SELECT * FROM categories ORDER BY name"
    ).fetchall()

    issue_types = conn.execute(
        "SELECT * FROM issue_types ORDER BY name"
    ).fetchall()

    equipment_types = conn.execute(
        "SELECT * FROM equipment_types ORDER BY name"
    ).fetchall()

    conn.close()

    return render_template(
        "configuration.html",
        facilities=facilities,
        priorities=priorities,
        categories=categories,
        issue_types=issue_types,
        equipment_types=equipment_types,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/configuration/facilities/add", methods=["POST"])
@admin_required
def add_facility():
    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    contact = request.form.get("contact", "").strip()

    if not name:
        flash("Facility name is required.", "error")
        return redirect(url_for("configuration"))

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO facilities (name, location, contact, active) VALUES (?, ?, ?, 1)",
            (name, location or None, contact or None)
        )
        conn.commit()
        flash("Facility added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Facility already exists.", "error")
    finally:
        conn.close()

    return redirect(url_for("configuration"))


@app.route("/configuration/facilities/<int:facility_id>/toggle", methods=["POST"])
@admin_required
def toggle_facility(facility_id):
    conn = get_db()
    facility = conn.execute("SELECT active FROM facilities WHERE id = ?", (facility_id,)).fetchone()
    if not facility:
        conn.close()
        flash("Facility not found.", "error")
        return redirect(url_for("configuration"))

    new_status = 0 if facility["active"] == 1 else 1
    conn.execute("UPDATE facilities SET active = ? WHERE id = ?", (new_status, facility_id))
    conn.commit()
    conn.close()
    flash("Facility status updated.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/facilities/<int:facility_id>/delete", methods=["POST"])
@admin_required
def delete_facility(facility_id):
    conn = get_db()
    conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
    conn.commit()
    conn.close()
    flash("Facility deleted successfully.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/priorities/add", methods=["POST"])
@admin_required
def add_priority():
    name = request.form.get("name", "").strip()
    sla_hours = request.form.get("sla_hours", "").strip()

    if not name or not sla_hours:
        flash("Priority name and SLA hours are required.", "error")
        return redirect(url_for("configuration"))

    try:
        sla_hours = int(sla_hours)
    except ValueError:
        flash("SLA hours must be a valid number.", "error")
        return redirect(url_for("configuration"))

    if sla_hours <= 0:
        flash("SLA hours must be greater than zero.", "error")
        return redirect(url_for("configuration"))

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO priorities (name, sla_hours, active) VALUES (?, ?, 1)",
            (name, sla_hours)
        )
        conn.commit()
        flash("Priority added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Priority already exists.", "error")
    finally:
        conn.close()

    return redirect(url_for("configuration"))


@app.route("/configuration/priorities/<int:priority_id>/toggle", methods=["POST"])
@admin_required
def toggle_priority(priority_id):
    conn = get_db()
    priority = conn.execute("SELECT active FROM priorities WHERE id = ?", (priority_id,)).fetchone()
    if not priority:
        conn.close()
        flash("Priority not found.", "error")
        return redirect(url_for("configuration"))

    new_status = 0 if priority["active"] == 1 else 1
    conn.execute("UPDATE priorities SET active = ? WHERE id = ?", (new_status, priority_id))
    conn.commit()
    conn.close()
    flash("Priority status updated.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/priorities/<int:priority_id>/delete", methods=["POST"])
@admin_required
def delete_priority(priority_id):
    conn = get_db()
    conn.execute("DELETE FROM priorities WHERE id = ?", (priority_id,))
    conn.commit()
    conn.close()
    flash("Priority deleted successfully.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/categories/add", methods=["POST"])
@admin_required
def add_category():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("configuration"))

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO categories (name, description, active) VALUES (?, ?, 1)",
            (name, description or None)
        )
        conn.commit()
        flash("Category added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Category already exists.", "error")
    finally:
        conn.close()

    return redirect(url_for("configuration"))


@app.route("/configuration/categories/<int:category_id>/toggle", methods=["POST"])
@admin_required
def toggle_category(category_id):
    conn = get_db()
    category = conn.execute("SELECT active FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not category:
        conn.close()
        flash("Category not found.", "error")
        return redirect(url_for("configuration"))

    new_status = 0 if category["active"] == 1 else 1
    conn.execute("UPDATE categories SET active = ? WHERE id = ?", (new_status, category_id))
    conn.commit()
    conn.close()
    flash("Category status updated.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/categories/<int:category_id>/delete", methods=["POST"])
@admin_required
def delete_category(category_id):
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    flash("Category deleted successfully.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/issue-types/add", methods=["POST"])
@admin_required
def add_issue_type():
    name = request.form.get("name", "").strip()
    category = request.form.get("category", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Issue type name is required.", "error")
        return redirect(url_for("configuration"))

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO issue_types (name, category, description, active) VALUES (?, ?, ?, 1)",
            (name, category or None, description or None)
        )
        conn.commit()
        flash("Issue type added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Issue type already exists.", "error")
    finally:
        conn.close()

    return redirect(url_for("configuration"))


@app.route("/configuration/issue-types/<int:issue_type_id>/toggle", methods=["POST"])
@admin_required
def toggle_issue_type(issue_type_id):
    conn = get_db()
    issue_type = conn.execute("SELECT active FROM issue_types WHERE id = ?", (issue_type_id,)).fetchone()
    if not issue_type:
        conn.close()
        flash("Issue type not found.", "error")
        return redirect(url_for("configuration"))

    new_status = 0 if issue_type["active"] == 1 else 1
    conn.execute("UPDATE issue_types SET active = ? WHERE id = ?", (new_status, issue_type_id))
    conn.commit()
    conn.close()
    flash("Issue type status updated.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/issue-types/<int:issue_type_id>/delete", methods=["POST"])
@admin_required
def delete_issue_type(issue_type_id):
    conn = get_db()
    conn.execute("DELETE FROM issue_types WHERE id = ?", (issue_type_id,))
    conn.commit()
    conn.close()
    flash("Issue type deleted successfully.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/equipment-types/add", methods=["POST"])
@admin_required
def add_equipment_type():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("Equipment type name is required.", "error")
        return redirect(url_for("configuration"))

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO equipment_types (name, description, active) VALUES (?, ?, 1)",
            (name, description or None)
        )
        conn.commit()
        flash("Equipment type added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Equipment type already exists.", "error")
    finally:
        conn.close()

    return redirect(url_for("configuration"))


@app.route("/configuration/equipment-types/<int:equipment_type_id>/toggle", methods=["POST"])
@admin_required
def toggle_equipment_type(equipment_type_id):
    conn = get_db()
    equipment_type = conn.execute("SELECT active FROM equipment_types WHERE id = ?", (equipment_type_id,)).fetchone()
    if not equipment_type:
        conn.close()
        flash("Equipment type not found.", "error")
        return redirect(url_for("configuration"))

    new_status = 0 if equipment_type["active"] == 1 else 1
    conn.execute("UPDATE equipment_types SET active = ? WHERE id = ?", (new_status, equipment_type_id))
    conn.commit()
    conn.close()
    flash("Equipment type status updated.", "success")
    return redirect(url_for("configuration"))


@app.route("/configuration/equipment-types/<int:equipment_type_id>/delete", methods=["POST"])
@admin_required
def delete_equipment_type(equipment_type_id):
    conn = get_db()
    conn.execute("DELETE FROM equipment_types WHERE id = ?", (equipment_type_id,))
    conn.commit()
    conn.close()
    flash("Equipment type deleted successfully.", "success")
    return redirect(url_for("configuration"))


@app.route("/admin")
@admin_required
def admin_panel():
    conn = get_db()

    technicians = conn.execute("""
        SELECT
            users.id,
            users.full_name,
            users.username,
            users.department,
            users.active,
            COUNT(tickets.id) AS ticket_count
        FROM users
        LEFT JOIN tickets ON users.id = tickets.assigned_to
        WHERE users.role = 'ICT Technician'
        GROUP BY users.id
        ORDER BY users.full_name
    """).fetchall()

    departments = conn.execute("""
        SELECT * FROM departments
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "admin.html",
        technicians=technicians,
        departments=departments,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/technician-performance")
@admin_required
def technician_performance():
    conn = get_db()

    technicians = conn.execute("""
        SELECT
            users.id,
            users.full_name,
            users.username,
            users.department,
            users.active,
            COUNT(tickets.id) AS total_tickets,
            SUM(CASE WHEN tickets.status IN
                ('Open', 'Assigned', 'In Progress', 'Pending')
                THEN 1 ELSE 0 END) AS active_tickets,
            SUM(CASE WHEN tickets.status = 'Resolved'
                THEN 1 ELSE 0 END) AS resolved_tickets,
            SUM(CASE WHEN tickets.status = 'Closed'
                THEN 1 ELSE 0 END) AS closed_tickets
        FROM users
        LEFT JOIN tickets ON users.id = tickets.assigned_to
        WHERE users.role = 'ICT Technician'
        GROUP BY users.id
        ORDER BY total_tickets DESC, users.full_name
    """).fetchall()

    total_assigned = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE assigned_to IS NOT NULL"
    ).fetchone()[0]
    total_active = conn.execute("""
        SELECT COUNT(*) FROM tickets
        WHERE assigned_to IS NOT NULL
        AND status IN ('Open', 'Assigned', 'In Progress', 'Pending')
    """).fetchone()[0]
    total_resolved = conn.execute("""
        SELECT COUNT(*) FROM tickets
        WHERE assigned_to IS NOT NULL AND status = 'Resolved'
    """).fetchone()[0]
    total_closed = conn.execute("""
        SELECT COUNT(*) FROM tickets
        WHERE assigned_to IS NOT NULL AND status = 'Closed'
    """).fetchone()[0]
    department_stats = conn.execute("""
        SELECT tickets.department, COUNT(tickets.id) AS ticket_count
        FROM tickets
        WHERE tickets.assigned_to IS NOT NULL
        GROUP BY tickets.department
        ORDER BY ticket_count DESC
    """).fetchall()

    conn.close()

    return render_template(
        "technician_performance.html",
        technicians=technicians,
        total_assigned=total_assigned,
        total_active=total_active,
        total_resolved=total_resolved,
        total_closed=total_closed,
        department_stats=department_stats,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/admin/users")
@admin_required
def user_management():
    conn = get_db()

    users = conn.execute("""
        SELECT
            users.id,
            users.username,
            users.full_name,
            users.role,
            users.department,
            users.active,
            COUNT(tickets.id) AS ticket_count
        FROM users
        LEFT JOIN tickets ON users.id = tickets.created_by
        GROUP BY users.id
        ORDER BY users.id DESC
    """).fetchall()

    conn.close()

    return render_template(
        "users.html",
        users=users,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/admin/users/add", methods=["POST"])
@admin_required
def add_user():
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "")
    department = request.form.get("department", "ICT")

    allowed_roles = [
        "Administrator",
        "ICT Technician",
        "Staff"
    ]

    if not full_name or not username or not password or role not in allowed_roles:
        flash("Please complete all user fields.", "error")
        return redirect(url_for("user_management"))

    conn = get_db()

    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if existing:
        conn.close()
        flash("Username already exists.", "error")
        return redirect(url_for("user_management"))

    hashed_password = generate_password_hash(password)

    conn.execute("""
        INSERT INTO users (
            username,
            password,
            full_name,
            role,
            department,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        username,
        hashed_password,
        full_name,
        role,
        department,
        1
    ))

    conn.commit()
    conn.close()

    flash("User account created successfully.", "success")

    return redirect(url_for("user_management"))


@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("user_management"))

    conn = get_db()

    user = conn.execute(
        "SELECT id, active FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        flash("User account not found.", "error")
        return redirect(url_for("user_management"))

    new_status = 0 if user["active"] == 1 else 1

    conn.execute(
        "UPDATE users SET active = ? WHERE id = ?",
        (new_status, user_id)
    )

    conn.commit()
    conn.close()

    if new_status == 1:
        flash("User account activated successfully.", "success")
    else:
        flash("User account deactivated successfully.", "success")

    return redirect(url_for("user_management"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("user_management"))

    conn = get_db()

    user = conn.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        flash("User account not found.", "error")
        return redirect(url_for("user_management"))

    created_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE created_by = ?",
        (user_id,)
    ).fetchone()[0]

    assigned_tickets = conn.execute(
        "SELECT COUNT(*) FROM tickets WHERE assigned_to = ?",
        (user_id,)
    ).fetchone()[0]

    if created_tickets > 0 or assigned_tickets > 0:
        conn.close()
        flash(
            "This account is linked to existing tickets. Deactivate it instead of deleting it.",
            "error"
        )
        return redirect(url_for("user_management"))

    conn.execute(
        "DELETE FROM users WHERE id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    flash("User account deleted successfully.", "success")

    return redirect(url_for("user_management"))


@app.route("/admin/add-technician", methods=["POST"])
@admin_required
def add_technician():
    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    department = request.form.get("department", "ICT")

    if not full_name or not username or not password:
        flash("Please complete all technician fields.", "error")
        return redirect(url_for("admin_panel"))

    conn = get_db()

    existing = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if existing:
        conn.close()
        flash("Username already exists.", "error")
        return redirect(url_for("admin_panel"))

    hashed_password = generate_password_hash(password)

    conn.execute("""
        INSERT INTO users (
            username,
            password,
            full_name,
            role,
            department,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        username,
        hashed_password,
        full_name,
        "ICT Technician",
        department,
        1
    ))

    conn.commit()
    conn.close()

    flash("Technician account created successfully.", "success")

    return redirect(url_for("admin_panel"))


@app.route("/admin/add-department", methods=["POST"])
@admin_required
def add_department():
    name = request.form.get("name", "").strip()

    if not name:
        flash("Department name is required.", "error")
        return redirect(url_for("admin_panel"))

    conn = get_db()

    try:
        conn.execute(
            "INSERT INTO departments (name) VALUES (?)",
            (name,)
        )
        conn.commit()
        flash("Department added successfully.", "success")
    except sqlite3.IntegrityError:
        flash("Department already exists.", "error")

    conn.close()

    return redirect(url_for("admin_panel"))


@app.route("/notifications")
@login_required
def notifications():
    conn = get_db()

    user_notifications = conn.execute("""
        SELECT
            notifications.*,
            tickets.title AS ticket_title
        FROM notifications
        LEFT JOIN tickets ON notifications.ticket_id = tickets.id
        WHERE notifications.user_id = ?
        ORDER BY notifications.id DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "notifications.html",
        notifications=user_notifications,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    conn = get_db()

    conn.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE id = ?
        AND user_id = ?
    """, (
        notification_id,
        session["user_id"]
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("notifications"))


@app.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    conn = get_db()

    conn.execute("""
        UPDATE notifications
        SET is_read = 1
        WHERE user_id = ?
    """, (session["user_id"],))

    conn.commit()
    conn.close()

    flash("All notifications marked as read.", "success")

    return redirect(url_for("notifications"))


@app.route("/admin/backup")
@admin_required
def backup_database():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"meditech_backup_{timestamp}.db")
    shutil.copy2(DATABASE, backup_path)

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=os.path.basename(backup_path),
        mimetype="application/octet-stream"
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/reports")
@admin_required
def reports():
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    department = request.args.get("department", "")

    conn = get_db()

    query = """
        SELECT
            tickets.*,
            creator.full_name AS creator_name,
            technician.full_name AS technician_name
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        LEFT JOIN users technician ON tickets.assigned_to = technician.id
        WHERE 1=1
    """

    parameters = []

    if date_from:
        query += " AND tickets.created_at >= ?"
        parameters.append(f"{date_from} 00:00:00")

    if date_to:
        query += " AND tickets.created_at <= ?"
        parameters.append(f"{date_to} 23:59:59")

    if status:
        query += " AND tickets.status = ?"
        parameters.append(status)

    if priority:
        query += " AND tickets.priority = ?"
        parameters.append(priority)

    if department:
        query += " AND tickets.department = ?"
        parameters.append(department)

    query += " ORDER BY tickets.id DESC"

    report_tickets = conn.execute(
        query,
        parameters
    ).fetchall()

    report_tickets_with_sla = []
    for ticket in report_tickets:
        ticket_data = dict(ticket)
        ticket_data["sla"] = calculate_sla(
            ticket["created_at"],
            ticket["priority"],
            ticket["status"]
        )
        report_tickets_with_sla.append(ticket_data)

    sla_counts = {
        "On Track": 0,
        "At Risk": 0,
        "Overdue": 0,
        "Completed": 0
    }
    critical_overdue = 0
    for ticket in report_tickets_with_sla:
        sla_status = ticket["sla"]["sla_status"]
        if sla_status in sla_counts:
            sla_counts[sla_status] += 1
        if ticket["priority"] == "Critical" and sla_status == "Overdue":
            critical_overdue += 1

    total_tickets = len(report_tickets_with_sla)

    open_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "Open"
    )

    assigned_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "Assigned"
    )

    in_progress_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "In Progress"
    )

    pending_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "Pending"
    )

    resolved_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "Resolved"
    )

    closed_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["status"] == "Closed"
    )

    critical_tickets = sum(
        1 for ticket in report_tickets_with_sla
        if ticket["priority"] == "Critical"
        and ticket["status"] not in ("Resolved", "Closed")
    )

    departments = conn.execute(
        "SELECT * FROM departments ORDER BY name"
    ).fetchall()

    conn.close()

    return render_template(
        "reports.html",
        tickets=report_tickets_with_sla,
        departments=departments,
        total_tickets=total_tickets,
        open_tickets=open_tickets,
        assigned_tickets=assigned_tickets,
        in_progress_tickets=in_progress_tickets,
        pending_tickets=pending_tickets,
        resolved_tickets=resolved_tickets,
        closed_tickets=closed_tickets,
        critical_tickets=critical_tickets,
        sla_counts=sla_counts,
        critical_overdue=critical_overdue,
        date_from=date_from,
        date_to=date_to,
        selected_status=status,
        selected_priority=priority,
        selected_department=department,
        full_name=session["full_name"],
        role=session["role"]
    )

import csv
from io import StringIO
from flask import Response

@app.route("/reports/export")
@admin_required
def export_report():
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    department = request.args.get("department", "")

    conn = get_db()

    query = """
        SELECT
            tickets.id,
            tickets.title,
            tickets.description,
            tickets.department,
            tickets.category,
            tickets.priority,
            tickets.status,
            creator.full_name AS creator_name,
            technician.full_name AS technician_name,
            tickets.created_at
        FROM tickets
        JOIN users creator ON tickets.created_by = creator.id
        LEFT JOIN users technician ON tickets.assigned_to = technician.id
        WHERE 1=1
    """

    parameters = []

    if date_from:
        query += " AND tickets.created_at >= ?"
        parameters.append(f"{date_from} 00:00:00")

    if date_to:
        query += " AND tickets.created_at <= ?"
        parameters.append(f"{date_to} 23:59:59")

    if status:
        query += " AND tickets.status = ?"
        parameters.append(status)

    if priority:
        query += " AND tickets.priority = ?"
        parameters.append(priority)

    if department:
        query += " AND tickets.department = ?"
        parameters.append(department)

    query += " ORDER BY tickets.id DESC"

    tickets = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Ticket ID",
        "Issue",
        "Description",
        "Department",
        "Category",
        "Priority",
        "Status",
        "Created By",
        "Technician",
        "Created At"
    ])

    for ticket in tickets:
        writer.writerow([
            ticket["id"],
            ticket["title"],
            ticket["description"],
            ticket["department"],
            ticket["category"],
            ticket["priority"],
            ticket["status"],
            ticket["creator_name"],
            ticket["technician_name"] or "Unassigned",
            ticket["created_at"]
        ])

    filename = "MediTech_Ticket_Report.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )

@app.route("/assets")
@login_required
def assets():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    department = request.args.get("department", "").strip()
    equipment_type = request.args.get("equipment_type", "").strip()

    conn = get_db()

    query = """
        SELECT
            assets.*,
            users.full_name AS assigned_name
        FROM assets
        LEFT JOIN users ON assets.assigned_to = users.id
        WHERE 1=1
    """

    parameters = []

    if search:
        query += """
            AND (
                assets.asset_tag LIKE ?
                OR assets.name LIKE ?
                OR assets.serial_number LIKE ?
                OR assets.manufacturer LIKE ?
                OR assets.model LIKE ?
            )
        """

        search_value = f"%{search}%"

        parameters.extend([
            search_value,
            search_value,
            search_value,
            search_value,
            search_value
        ])

    if status:
        query += " AND assets.status = ?"
        parameters.append(status)

    if department:
        query += " AND assets.department = ?"
        parameters.append(department)

    if equipment_type:
        query += " AND assets.equipment_type = ?"
        parameters.append(equipment_type)

    query += " ORDER BY assets.id DESC"

    assets_list = conn.execute(
        query,
        parameters
    ).fetchall()

    departments = conn.execute("""
        SELECT name
        FROM departments
        ORDER BY name
    """).fetchall()

    equipment_types = conn.execute("""
        SELECT DISTINCT equipment_type
        FROM assets
        WHERE equipment_type IS NOT NULL
        AND equipment_type != ''
        ORDER BY equipment_type
    """).fetchall()

    total_assets = conn.execute(
        "SELECT COUNT(*) FROM assets"
    ).fetchone()[0]

    active_assets = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE status = 'Active'"
    ).fetchone()[0]

    repair_assets = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE status = 'Repair'"
    ).fetchone()[0]

    retired_assets = conn.execute(
        "SELECT COUNT(*) FROM assets WHERE status = 'Retired'"
    ).fetchone()[0]

    conn.close()

    return render_template(
        "assets.html",
        assets=assets_list,
        departments=departments,
        equipment_types=equipment_types,
        total_assets=total_assets,
        active_assets=active_assets,
        repair_assets=repair_assets,
        retired_assets=retired_assets,
        search=search,
        selected_status=status,
        selected_department=department,
        selected_equipment_type=equipment_type,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/assets/create", methods=["GET", "POST"])
@admin_required
def create_asset():
    conn = get_db()

    departments = conn.execute("""
        SELECT name
        FROM departments
        ORDER BY name
    """).fetchall()

    staff = conn.execute("""
        SELECT id, full_name, username, department
        FROM users
        WHERE active = 1
        AND role != 'Administrator'
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":
        asset_tag = request.form.get("asset_tag", "").strip()
        name = request.form.get("name", "").strip()
        equipment_type = request.form.get("equipment_type", "").strip()
        manufacturer = request.form.get("manufacturer", "").strip()
        model = request.form.get("model", "").strip()
        serial_number = request.form.get("serial_number", "").strip()
        department = request.form.get("department", "").strip()
        assigned_to = request.form.get("assigned_to", "").strip()
        location = request.form.get("location", "").strip()
        purchase_date = request.form.get("purchase_date", "").strip()
        warranty_expiry = request.form.get("warranty_expiry", "").strip()
        status = request.form.get("status", "Active").strip()
        notes = request.form.get("notes", "").strip()

        if not asset_tag or not name or not equipment_type:
            conn.close()
            flash(
                "Asset Tag, Asset Name and Equipment Type are required.",
                "error"
            )
            return redirect(url_for("create_asset"))

        try:
            conn.execute("""
                INSERT INTO assets (
                    asset_tag,
                    name,
                    equipment_type,
                    manufacturer,
                    model,
                    serial_number,
                    department,
                    assigned_to,
                    location,
                    purchase_date,
                    warranty_expiry,
                    status,
                    notes,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                asset_tag,
                name,
                equipment_type,
                manufacturer,
                model,
                serial_number,
                department,
                int(assigned_to) if assigned_to else None,
                location,
                purchase_date,
                warranty_expiry,
                status,
                notes,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))

            conn.commit()
            conn.close()

            flash("Asset registered successfully.", "success")
            return redirect(url_for("assets"))

        except sqlite3.IntegrityError:
            conn.close()
            flash(
                "Asset Tag already exists. Use a unique Asset Tag.",
                "error"
            )
            return redirect(url_for("create_asset"))

    conn.close()

    return render_template(
        "create_asset.html",
        departments=departments,
        staff=staff,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/asset/<int:asset_id>")
@login_required
def asset_details(asset_id):
    conn = get_db()

    asset = conn.execute("""
        SELECT
            assets.*,
            users.full_name AS assigned_name,
            users.username AS assigned_username
        FROM assets
        LEFT JOIN users ON assets.assigned_to = users.id
        WHERE assets.id = ?
    """, (asset_id,)).fetchone()

    conn.close()

    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("assets"))

    return render_template(
        "asset_details.html",
        asset=asset,
        full_name=session["full_name"],
        role=session["role"]
    )


@app.route("/asset/<int:asset_id>/status", methods=["POST"])
@admin_required
def update_asset_status(asset_id):
    status = request.form.get("status", "").strip()

    allowed_statuses = [
        "Active",
        "Repair",
        "Retired",
        "Lost"
    ]

    if status not in allowed_statuses:
        flash("Invalid asset status.", "error")
        return redirect(url_for("asset_details", asset_id=asset_id))

    conn = get_db()

    asset = conn.execute(
        "SELECT id FROM assets WHERE id = ?",
        (asset_id,)
    ).fetchone()

    if not asset:
        conn.close()
        flash("Asset not found.", "error")
        return redirect(url_for("assets"))

    conn.execute(
        "UPDATE assets SET status = ? WHERE id = ?",
        (status, asset_id)
    )

    conn.commit()
    conn.close()

    flash("Asset status updated successfully.", "success")

    return redirect(
        url_for("asset_details", asset_id=asset_id)
    )





@app.route("/asset/<int:asset_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_asset(asset_id):
    conn = get_db()

    asset = conn.execute("""
        SELECT *
        FROM assets
        WHERE id = ?
    """, (asset_id,)).fetchone()

    if not asset:
        conn.close()
        flash("Asset not found.", "error")
        return redirect(url_for("assets"))

    departments = conn.execute("""
        SELECT name
        FROM departments
        ORDER BY name
    """).fetchall()

    staff = conn.execute("""
        SELECT id, full_name, username, department
        FROM users
        WHERE active = 1
        AND role != 'Administrator'
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":
        asset_tag = request.form.get("asset_tag", "").strip()
        name = request.form.get("name", "").strip()
        equipment_type = request.form.get("equipment_type", "").strip()
        manufacturer = request.form.get("manufacturer", "").strip()
        model = request.form.get("model", "").strip()
        serial_number = request.form.get("serial_number", "").strip()
        department = request.form.get("department", "").strip()
        assigned_to = request.form.get("assigned_to", "").strip()
        location = request.form.get("location", "").strip()
        purchase_date = request.form.get("purchase_date", "").strip()
        warranty_expiry = request.form.get("warranty_expiry", "").strip()
        status = request.form.get("status", "Active").strip()
        notes = request.form.get("notes", "").strip()

        if not asset_tag or not name or not equipment_type:
            conn.close()
            flash(
                "Asset Tag, Asset Name and Equipment Type are required.",
                "error"
            )
            return redirect(
                url_for("edit_asset", asset_id=asset_id)
            )

        allowed_statuses = [
            "Active",
            "Repair",
            "Retired",
            "Lost"
        ]

        if status not in allowed_statuses:
            conn.close()
            flash("Invalid asset status.", "error")
            return redirect(
                url_for("edit_asset", asset_id=asset_id)
            )

        try:
            conn.execute("""
                UPDATE assets
                SET
                    asset_tag = ?,
                    name = ?,
                    equipment_type = ?,
                    manufacturer = ?,
                    model = ?,
                    serial_number = ?,
                    department = ?,
                    assigned_to = ?,
                    location = ?,
                    purchase_date = ?,
                    warranty_expiry = ?,
                    status = ?,
                    notes = ?
                WHERE id = ?
            """, (
                asset_tag,
                name,
                equipment_type,
                manufacturer,
                model,
                serial_number,
                department,
                int(assigned_to) if assigned_to else None,
                location,
                purchase_date,
                warranty_expiry,
                status,
                notes,
                asset_id
            ))

            conn.commit()
            conn.close()

            flash("Asset updated successfully.", "success")

            return redirect(
                url_for("asset_details", asset_id=asset_id)
            )

        except sqlite3.IntegrityError:
            conn.close()
            flash(
                "Asset Tag already exists. Use a unique Asset Tag.",
                "error"
            )
            return redirect(
                url_for("edit_asset", asset_id=asset_id)
            )

    conn.close()

    return render_template(
        "edit_asset.html",
        asset=asset,
        departments=departments,
        staff=staff,
        full_name=session["full_name"],
        role=session["role"]
    )

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
