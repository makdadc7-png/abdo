import os
import sqlite3
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.utils import secure_filename


# =========================
# App config
# =========================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change_me")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "data.db")

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ADMIN_USER = "admin"
ADMIN_PASS = "1234"


# =========================
# DB helpers
# =========================
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn



def get_db():
    return get_conn()


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # جدول العقود
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contrats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        demande_id INTEGER,                -- ممكن تكون NULL
        client_nom TEXT NOT NULL,
        client_cin TEXT,
        client_permis TEXT,
        annee_permis TEXT,

        client2_nom TEXT,
        client2_cin TEXT,
        client2_permis TEXT,
        client2_annee_permis TEXT,

        voiture_nom TEXT NOT NULL,
        immatriculation TEXT,
        categorie TEXT,

        date_debut TEXT,
        date_fin TEXT,
        jours INTEGER,
        prix_jour REAL,
        total REAL,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        statut TEXT DEFAULT 'Actif'
    )
    """)

    # جدول السيارات
    cur.execute("""
        CREATE TABLE IF NOT EXISTS voitures(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT,
            categorie TEXT,
            prix_jour REAL,
            immatriculation TEXT,
            statut TEXT DEFAULT 'Disponible',
            image TEXT
        )
    """)

    # جدول الطلبات
    cur.execute("""
        CREATE TABLE IF NOT EXISTS demandes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT,
            tel TEXT,
            email TEXT,
            ville TEXT,
            date_debut TEXT,
            date_fin TEXT,
            voiture TEXT,
            notes TEXT,
            statut TEXT DEFAULT 'En attente',
            created_at TEXT
        )
    """)

    # جدول الزبناء
    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prenom TEXT,
            nom TEXT,
            tel TEXT,
            cin_num TEXT,
            permis_num TEXT,
            created_at TEXT
        )
    """)

    # جدول رسائل contact
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contacts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT,
            email TEXT,
            message TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# create tables once
init_db()


# =========================
# Auth helper
# =========================
def require_admin():
    return bool(session.get("admin_ok"))


# =========================
# Business helpers
# =========================
def is_car_rented_today(car_id=None, car_name=None):
    """Return True if car is rented today (contrat Actif or demande Confirmée covers today)."""
    conn = get_db()
    cur = conn.cursor()
    today = date.today()

    if car_name:
        # عقود نشطة
        cur.execute("""
            SELECT date_debut, date_fin
            FROM contrats
            WHERE voiture_nom=?
              AND statut='Actif'
        """, (car_name,))
        contrats = cur.fetchall()

        for c in contrats:
            try:
                d1 = datetime.strptime(c["date_debut"], "%Y-%m-%d").date()
                d2 = datetime.strptime(c["date_fin"], "%Y-%m-%d").date()
                if d1 <= today <= d2:
                    conn.close()
                    return True
            except Exception:
                continue

        # طلبات Confirmée
        cur.execute("""
            SELECT date_debut, date_fin
            FROM demandes
            WHERE voiture=?
              AND statut='Confirmée'
        """, (car_name,))
        rows = cur.fetchall()

        for r in rows:
            try:
                d1 = datetime.strptime(r["date_debut"], "%Y-%m-%d").date()
                d2 = datetime.strptime(r["date_fin"], "%Y-%m-%d").date()
                if d1 <= today <= d2:
                    conn.close()
                    return True
            except Exception:
                continue

    conn.close()
    return False


def is_car_available_between(car_name, date_debut, date_fin, ignore_demande_id=None):
    """
    Return True if car is available between date_debut and date_fin.
    Checks:
      - contrats Actif overlapping
      - demandes Confirmée overlapping
    """
    try:
        dd = datetime.strptime(date_debut, "%Y-%m-%d").date()
        df = datetime.strptime(date_fin, "%Y-%m-%d").date()
        if df < dd:
            dd, df = df, dd
    except Exception:
        return False

    conn = get_conn()
    cur = conn.cursor()

    # 1) Overlap with contrats Actif
    cur.execute("""
        SELECT date_debut, date_fin
        FROM contrats
        WHERE voiture_nom=?
          AND statut='Actif'
    """, (car_name,))
    contrats = cur.fetchall()

    for c in contrats:
        try:
            c_dd = datetime.strptime(c["date_debut"], "%Y-%m-%d").date()
            c_df = datetime.strptime(c["date_fin"], "%Y-%m-%d").date()
            # overlap if ranges intersect
            if not (df < c_dd or dd > c_df):
                conn.close()
                return False
        except Exception:
            continue

    # 2) Overlap with demandes Confirmée
    if ignore_demande_id is not None:
        cur.execute("""
            SELECT date_debut, date_fin
            FROM demandes
            WHERE voiture=?
              AND statut='Confirmée'
              AND id != ?
        """, (car_name, ignore_demande_id))
    else:
        cur.execute("""
            SELECT date_debut, date_fin
            FROM demandes
            WHERE voiture=?
              AND statut='Confirmée'
        """, (car_name,))

    demandes = cur.fetchall()
    for d in demandes:
        try:
            d_dd = datetime.strptime(d["date_debut"], "%Y-%m-%d").date()
            d_df = datetime.strptime(d["date_fin"], "%Y-%m-%d").date()
            if not (df < d_dd or dd > d_df):
                conn.close()
                return False
        except Exception:
            continue

    conn.close()
    return True


def refresh_car_statuses():
    """Update voitures.statut based on today's rentals WITHOUT nested connections."""
    conn = get_conn()
    cur = conn.cursor()
    today = date.today().strftime("%Y-%m-%d")

    cur.execute("SELECT id, nom FROM voitures")
    voitures = cur.fetchall()

    for v in voitures:
        name = v["nom"]

        # check contrats Actif that cover today
        cur.execute("""
            SELECT 1
            FROM contrats
            WHERE voiture_nom=?
              AND statut='Actif'
              AND date_debut <= ?
              AND date_fin >= ?
            LIMIT 1
        """, (name, today, today))
        rented = cur.fetchone() is not None

        # if not rented by contrat, check demandes Confirmée
        if not rented:
            cur.execute("""
                SELECT 1
                FROM demandes
                WHERE voiture=?
                  AND statut='Confirmée'
                  AND date_debut <= ?
                  AND date_fin >= ?
                LIMIT 1
            """, (name, today, today))
            rented = cur.fetchone() is not None

        new_status = "Louée" if rented else "Disponible"
        cur.execute("UPDATE voitures SET statut=? WHERE id=?", (new_status, v["id"]))

    conn.commit()
    conn.close()

    """Update voitures.statut based on today's rentals."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, nom FROM voitures")
    voitures = cur.fetchall()

    for v in voitures:
        rented = is_car_rented_today(car_name=v["nom"])
        new_status = "Louée" if rented else "Disponible"
        cur.execute("UPDATE voitures SET statut=? WHERE id=?", (new_status, v["id"]))

    conn.commit()
    conn.close()


# =========================
# Client routes
# =========================
@app.route("/")
def index():
    q = request.args.get("q", "").strip().lower()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM voitures ORDER BY id DESC LIMIT 4")
    popular_voitures = cur.fetchall()

    search_results = []
    if q:
        sql = """
            SELECT * FROM voitures
            WHERE LOWER(nom) LIKE ? OR LOWER(categorie) LIKE ?
        """
        like = f"%{q}%"
        cur.execute(sql, (like, like))
        search_results = cur.fetchall()

    conn.close()

    return render_template(
        "index.html",
        popular_voitures=popular_voitures,
        search_results=search_results,
        q=q
    )


@app.route("/nos-voitures")
def nos_voitures():
    # تحديث statuts تلقائياً
    refresh_car_statuses()

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM voitures ORDER BY id DESC")
    voitures = cur.fetchall()
    conn.close()

    dispo, louees = [], []
    for v in voitures:
        if v["statut"] == "Louée":
            louees.append(v)
        else:
            dispo.append(v)

    return render_template("nos_voitures.html", dispo=dispo, louees=louees)


@app.route("/demande")
def demande():
    car = request.args.get("car", "")
    return render_template("demande.html", car=car, errors={}, form={})


@app.route("/demande", methods=["POST"])
def demande_post():
    nom = request.form.get("nom", "").strip()
    tel = request.form.get("tel", "").strip()
    email = request.form.get("email", "").strip()
    ville = request.form.get("ville", "").strip()
    date_debut = request.form.get("date_debut", "").strip()
    date_fin = request.form.get("date_fin", "").strip()
    voiture = request.form.get("voiture", "").strip()
    notes = request.form.get("notes", "").strip()

    errors = {}
    if not nom:
        errors["nom"] = "Nom obligatoire"
    if not tel:
        errors["tel"] = "Téléphone obligatoire"
    if not ville:
        errors["ville"] = "Ville obligatoire"
    if not date_debut:
        errors["date_debut"] = "Date début obligatoire"
    if not date_fin:
        errors["date_fin"] = "Date fin obligatoire"
    if not voiture:
        errors["voiture"] = "Voiture obligatoire"

    form = dict(
        nom=nom, tel=tel, email=email, ville=ville,
        date_debut=date_debut, date_fin=date_fin,
        voiture=voiture, notes=notes
    )

    if errors:
        return render_template("demande.html", car=voiture, errors=errors, form=form)

    # منع تداخل الحجوزات
    if not is_car_available_between(voiture, date_debut, date_fin):
        errors["voiture"] = "Cette voiture n'est pas disponible pour ces dates."
        return render_template("demande.html", car=voiture, errors=errors, form=form)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO demandes(nom,tel,email,ville,date_debut,date_fin,voiture,notes,statut,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        nom, tel, email, ville, date_debut, date_fin,
        voiture, notes, "En attente",
        datetime.now().strftime("%Y-%m-%d %H:%M")
    ))
    conn.commit()
    conn.close()

    return render_template("demande_confirm.html", form=form)


@app.route("/qui-sommes-nous")
def qui_sommes_nous():
    return render_template("qui_sommes_nous.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    errors = {}
    form = {"nom": "", "email": "", "message": ""}
    sent = False

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()

        form["nom"] = nom
        form["email"] = email
        form["message"] = message

        if not nom:
            errors["nom"] = "Nom obligatoire"
        if not email:
            errors["email"] = "Email obligatoire"
        if not message:
            errors["message"] = "Message obligatoire"

        if not errors:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO contacts(nom,email,message,created_at) VALUES(?,?,?,?)",
                (nom, email, message, datetime.now().strftime("%Y-%m-%d %H:%M")),
            )
            conn.commit()
            conn.close()
            sent = True
            form = {"nom": "", "email": "", "message": ""}

    return render_template("contact.html", errors=errors, form=form, sent=sent)


# =========================
# Admin auth
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["admin_ok"] = True
            return redirect(url_for("admin_dashboard"))
        else:
            return render_template("login.html", error="Identifiants incorrects")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.pop("admin_ok", None)
    return redirect(url_for("login"))


# =========================
# Admin routes
# =========================
@app.route("/admin")
def admin_dashboard():
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM demandes")
    total_res = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM clients")
    total_cli = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM voitures")
    total_voits = cur.fetchone()[0]

    conn.close()

    return render_template(
        "admin_dashboard.html",
        total_res=total_res,
        total_cli=total_cli,
        total_voits=total_voits
    )


@app.route("/admin/reservations")
def admin_reservations():
    if not require_admin():
        return redirect(url_for("login"))

    client_q = request.args.get("client", "").strip()
    voiture_q = request.args.get("voiture", "").strip()
    date_q = request.args.get("date", "").strip()

    conn = get_conn()
    cur = conn.cursor()

    base_sql = "SELECT * FROM demandes"
    where_clauses = []
    params = []

    if client_q:
        where_clauses.append("nom LIKE ?")
        params.append(f"%{client_q}%")

    if voiture_q:
        where_clauses.append("voiture LIKE ?")
        params.append(f"%{voiture_q}%")

    if date_q:
        where_clauses.append("(date_debut <= ? AND date_fin >= ?)")
        params.append(date_q)
        params.append(date_q)

    if where_clauses:
        base_sql += " WHERE " + " AND ".join(where_clauses)

    base_sql += " ORDER BY id DESC"

    cur.execute(base_sql, params)
    demandes = cur.fetchall()
    conn.close()

    return render_template("admin_reservations.html", demandes=demandes)


@app.route("/admin/reservations/<int:rid>")
def admin_res_detail(rid):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM demandes WHERE id=?", (rid,))
    d = cur.fetchone()
    if not d:
        conn.close()
        return "Réservation introuvable", 404

    jours = 1
    try:
        if d["date_debut"] and d["date_fin"]:
            dd = datetime.strptime(d["date_debut"], "%Y-%m-%d")
            df = datetime.strptime(d["date_fin"], "%Y-%m-%d")
            delta = (df - dd).days
            if delta > 0:
                jours = delta
    except Exception:
        jours = 1

    cur.execute(
        "SELECT categorie, prix_jour, image, statut, immatriculation FROM voitures WHERE nom=? LIMIT 1",
        (d["voiture"],),
    )
    v = cur.fetchone()

    prix_jour = None
    total = None
    if v and v["prix_jour"]:
        prix_jour = v["prix_jour"]
        total = prix_jour * jours

    conn.close()

    return render_template(
        "admin_reservation_detail.html",
        d=d,
        v=v,
        jours=jours,
        prix_jour=prix_jour,
        total=total,
    )


@app.route("/admin/reservations/<int:rid>/statut/<string:st>", methods=["GET", "POST"])
def admin_change_statut(rid, st):
    if not require_admin():
        return redirect(url_for("login"))

    if st not in ("En attente", "Confirmée", "Annulée"):
        return redirect(url_for("admin_reservations"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM demandes WHERE id=?", (rid,))
    d = cur.fetchone()
    if not d:
        conn.close()
        return redirect(url_for("admin_reservations"))

    cur.execute("UPDATE demandes SET statut=? WHERE id=?", (st, rid))

    if st == "Confirmée":
        # قبل confirmation نتأكدو من availability (منع التداخل)
        if not is_car_available_between(d["voiture"], d["date_debut"], d["date_fin"], ignore_demande_id=rid):
            conn.close()
            return "هذه السيارة مكراية فهذ التواريخ. مايمكنش نأكد الطلب.", 400

        # السيارة تولّي Louée
        cur.execute(
            "UPDATE voitures SET statut=? WHERE nom=?",
            ("Louée", d["voiture"]),
        )

        # نتأكدو واش كاين contrat لهاد demande
        cur.execute("SELECT id FROM contrats WHERE demande_id=? LIMIT 1", (rid,))
        c = cur.fetchone()

        if not c:
            cur.execute(
                "SELECT categorie, prix_jour, immatriculation FROM voitures WHERE nom=? LIMIT 1",
                (d["voiture"],)
            )
            v = cur.fetchone()

            jours = 1
            try:
                dd = datetime.strptime(d["date_debut"], "%Y-%m-%d")
                df = datetime.strptime(d["date_fin"], "%Y-%m-%d")
                delta = (df - dd).days
                if delta > 0:
                    jours = delta
            except Exception:
                jours = 1

            prix_jour = v["prix_jour"] if v and v["prix_jour"] else None
            total = prix_jour * jours if prix_jour else None

            # كنخلق contrat جديد
            cur.execute("""
                INSERT INTO contrats
                (demande_id, client_nom, voiture_nom, categorie, immatriculation,
                 date_debut, date_fin, jours, prix_jour, total)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                rid,
                d["nom"],
                d["voiture"],
                v["categorie"] if v else None,
                v["immatriculation"] if v else None,
                d["date_debut"],
                d["date_fin"],
                jours,
                prix_jour,
                total
            ))

    conn.commit()
    conn.close()

    if st == "Confirmée":
        return redirect(url_for("admin_facture", rid=rid))
    return redirect(url_for("admin_reservations"))


@app.route("/admin/reservations/<int:rid>/facture")
def admin_facture(rid):
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM demandes WHERE id=?", (rid,))
    d = cur.fetchone()
    if not d:
        conn.close()
        return "Réservation introuvable", 404

    jours = 1
    prix_jour = None
    total = None
    try:
        if d["date_debut"] and d["date_fin"]:
            dd = datetime.strptime(d["date_debut"], "%Y-%m-%d")
            df = datetime.strptime(d["date_fin"], "%Y-%m-%d")
            jours = (df - dd).days
            if jours <= 0:
                jours = 1
    except Exception:
        jours = 1

    # نجيب معلومات السيارة كاملة (1)
    cur.execute(
        "SELECT prix_jour, immatriculation, categorie FROM voitures WHERE nom=? LIMIT 1",
        (d["voiture"],)
    )
    v = cur.fetchone()

    if v and v["prix_jour"]:
        prix_jour = v["prix_jour"]
        total = prix_jour * jours

    conn.close()
    today = datetime.now().strftime("%d/%m/%Y")

    return render_template(
        "admin_facture.html",
        d=d,
        v=v,
        jours=jours,
        prix_jour=prix_jour,
        total=total,
        today=today
    )


@app.route("/admin/voitures", methods=["GET", "POST"])
def admin_voitures():
    if not require_admin():
        return redirect(url_for("login"))

    # تحديث statuts حتى فالadmin (4)
    refresh_car_statuses()

    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        categorie = request.form.get("categorie", "").strip()
        prix_jour = request.form.get("prix_jour", "").strip()
        immatriculation = request.form.get("immatriculation", "").strip()

        file = request.files.get("image")
        filename = None
        if file and file.filename:
            safe_name = secure_filename(file.filename)
            base, ext = os.path.splitext(safe_name)
            from time import time as _time
            filename = f"{base}_{int(_time())}{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, filename))

        if nom:
            try:
                p = float(prix_jour) if prix_jour else None
            except ValueError:
                p = None

            cur.execute(
                "INSERT INTO voitures(nom,categorie,prix_jour,immatriculation,statut,image) VALUES(?,?,?,?,?,?)",
                (nom, categorie, p, immatriculation, "Disponible", filename),
            )
            conn.commit()

    cur.execute("SELECT * FROM voitures ORDER BY id")
    voitures = cur.fetchall()
    conn.close()

    return render_template("admin_voitures.html", voitures=voitures)


@app.route("/admin/clients", methods=["GET", "POST"])
def admin_clients():
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        prenom = request.form.get("prenom", "").strip()
        nom = request.form.get("nom", "").strip()
        tel = request.form.get("tel", "").strip()
        cin_num = request.form.get("cin_num", "").strip()
        permis_num = request.form.get("permis_num", "").strip()

        if prenom or nom:
            cur.execute("""
                INSERT INTO clients(prenom,nom,tel,cin_num,permis_num,created_at)
                VALUES(?,?,?,?,?,?)
            """, (
                prenom, nom, tel, cin_num, permis_num,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            ))
            conn.commit()

    cur.execute("SELECT * FROM clients ORDER BY id DESC")
    clients = cur.fetchall()
    conn.close()

    return render_template("admin_clients.html", clients=clients)


@app.route("/admin/contrats")
def admin_contrats():
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM contrats ORDER BY id DESC")
    contrats = cur.fetchall()
    conn.close()

    return render_template("admin_contrats.html", contrats=contrats)


@app.route("/admin/contrats/new", methods=["GET", "POST"])
def admin_contrat_new():
    if not require_admin():
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT nom, categorie, prix_jour, immatriculation FROM voitures ORDER BY nom")
    voitures = cur.fetchall()

    if request.method == "POST":
        client_nom = request.form.get("client_nom")
        client_cin = request.form.get("client_cin")
        client_permis = request.form.get("client_permis")
        annee_permis = request.form.get("annee_permis")

        client2_nom = request.form.get("client2_nom")
        client2_cin = request.form.get("client2_cin")
        client2_permis = request.form.get("client2_permis")
        client2_annee_permis = request.form.get("client2_annee_permis")

        voiture_nom = request.form.get("voiture_nom")
        date_debut = request.form.get("date_debut")
        date_fin = request.form.get("date_fin")

        cur.execute(
            "SELECT categorie, prix_jour, immatriculation FROM voitures WHERE nom=? LIMIT 1",
            (voiture_nom,)
        )
        v = cur.fetchone()

        jours = 1
        try:
            dd = datetime.strptime(date_debut, "%Y-%m-%d")
            df = datetime.strptime(date_fin, "%Y-%m-%d")
            jours = (df - dd).days
            if jours <= 0:
                jours = 1
        except Exception:
            jours = 1

        prix_jour = v["prix_jour"] if v else None
        total = prix_jour * jours if prix_jour else None

        cur.execute("""
            INSERT INTO contrats
            (demande_id, client_nom, client_cin, client_permis, annee_permis,
             client2_nom, client2_cin, client2_permis, client2_annee_permis,
             voiture_nom, categorie, immatriculation,
             date_debut, date_fin, jours, prix_jour, total)
            VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            client_nom, client_cin, client_permis, annee_permis,
            client2_nom, client2_cin, client2_permis, client2_annee_permis,
            voiture_nom,
            v["categorie"] if v else None,
            v["immatriculation"] if v else None,
            date_debut, date_fin, jours, prix_jour, total
        ))

        conn.commit()
        conn.close()
        return redirect(url_for("admin_contrats"))

    conn.close()
    return render_template("admin_contrat_new.html", voitures=voitures)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
