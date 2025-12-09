import sqlite3
from datetime import date, timedelta, datetime

DB_PATH = "data.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # جيب شوية سيارات
    cur.execute("SELECT id, nom FROM voitures ORDER BY id DESC LIMIT 3")
    cars = cur.fetchall()

    if not cars:
        print("❌ ما كايناش voitures فالداتاباز. زيديهم الأول.")
        return

    today = date.today()
    d1 = today.strftime("%Y-%m-%d")
    d2 = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for c in cars:
        cur.execute("""
            INSERT INTO contrats(voiture_id, client_nom, client_tel, date_debut, date_fin, statut, created_at)
            VALUES(?,?,?,?,?,?,?)
        """, (
            c["id"],
            "Client Test",
            "0600000000",
            d1, d2,
            "Actif",
            now
        ))

        print("✅ Contrat Actif ajouté pour:", c["nom"])

    conn.commit()
    conn.close()
    print("🎉 Seed contrats terminé.")

if __name__ == "__main__":
    main()
