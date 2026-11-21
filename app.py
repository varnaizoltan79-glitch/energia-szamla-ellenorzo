import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
import streamlit as st
from pypdf import PdfReader
from sqlalchemy import create_engine, text
from supabase import create_client

st.set_page_config(page_title="Energiaszámla Ellenőrző", page_icon="⚡", layout="wide")

DB_URL = st.secrets["DATABASE_URL"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

@st.cache_resource
def init_connections():
    eng = create_engine(DB_URL, pool_pre_ping=True)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return eng, sb

engine, supabase = init_connections()

def feldolgoz_pdf(feltoltott_fajl):
    reader = PdfReader(feltoltott_fajl)
    teljes_szoveg = "\n".join([page.extract_text() or "" for page in reader.pages])
    adatok = {
        "szamlaszam": "",
        "szamla_kelte": date.today(),
        "fizetesi_hatarido": date.today(),
        "idoszak_tol": date.today(),
        "idoszak_ig": date.today(),
        "brutto": 0.0,
        "pod": "",
        "tetelek": []
    }

    m_szamlaszam = re.search(r"Számla sorszáma:\s*([A-Za-z0-9/-]+)", teljes_szoveg)
    if m_szamlaszam:
        adatok["szamlaszam"] = m_szamlaszam.group(1).strip()

    m_kelte = re.search(r"Számla kelte:\s*(\d{4}\.\d{2}\.\d{2})", teljes_szoveg)
    if m_kelte:
        adatok["szamla_kelte"] = datetime.strptime(m_kelte.group(1), "%Y.%m.%d").date()

    m_hatarido = re.search(r"Fizetési határidő:\s*(\d{4}\.\d{2}\.\d{2})", teljes_szoveg)
    if m_hatarido:
        adatok["fizetesi_hatarido"] = datetime.strptime(m_hatarido.group(1), "%Y.%m.%d").date()

    m_idoszak = re.search(r"Elszámolt időszak:\s*(\d{4}\.\d{2}\.\d{2})\s*-\s*(\d{4}\.\d{2}\.\d{2})", teljes_szoveg)
    if m_idoszak:
        adatok["idoszak_tol"] = datetime.strptime(m_idoszak.group(1), "%Y.%m.%d").date()
        adatok["idoszak_ig"] = datetime.strptime(m_idoszak.group(2), "%Y.%m.%d").date()

    m_brutto = re.search(r"Fizetendő összeg:\s*([\d\.\s]+)\s*Ft", teljes_szoveg)
    if m_brutto:
        tiszta_szam = m_brutto.group(1).replace(".", "").replace(" ", "").replace(",", ".")
        adatok["brutto"] = float(tiszta_szam)

    m_pod = re.search(r"Mérési pont azonosító:\s*(HU[A-Za-z0-9-]+)", teljes_szoveg)
    if m_pod:
        adatok["pod"] = m_pod.group(1).strip()

    tetel_minta = re.compile(
        r"(.+?)\s+\d{4}\.\d{2}\.\d{2}-\d{4}\.\d{2}\.\d{2}\s+([\d\.,]+)\s+(kWh|hó|m3|MJ|db)\s+([\d\.,]+)\s*Ft/\w+\s+[\d\.,]+\s+(\d+)"
    )
    for sor in teljes_szoveg.splitlines():
        talalat = tetel_minta.search(sor)
        if talalat:
            adatok["tetelek"].append({
                "Megnevezés": talalat.group(1).strip(),
                "Mennyiség": float(talalat.group(2).replace(".", "").replace(",", ".")),
                "Mértékegység": talalat.group(3).strip(),
                "Nettó egységár": float(talalat.group(4).replace(".", "").replace(",", ".")),
                "ÁFA (%)": float(talalat.group(5))
            })
    return adatok

def get_energetikus_helyei(uid: int):
    sql = (
        "SELECT DISTINCT id, megnevezes, pod_eic_azonosito, 'Saját' AS jogosultsag "
        "FROM felhasznalasi_helyek "
        "WHERE aktiv = TRUE AND (felelos_energetikus_id = :uid OR allando_helyettes_id = :uid) "
        "UNION "
        "SELECT DISTINCT fh.id, fh.megnevezes, fh.pod_eic_azonosito, 'Helyettesítés' AS jogosultsag "
        "FROM felhasznalasi_helyek fh "
        "JOIN helyettesitesek h ON fh.felelos_energetikus_id = h.helyettesitett_energetikus_id "
        "WHERE fh.aktiv = TRUE AND h.helyettesito_energetikus_id = :uid "
        "AND h.aktiv = TRUE AND h.kezdet_datum <= CURRENT_DATE AND h.vege_datum >= CURRENT_DATE "
        "AND (h.felhasznalasi_hely_id = fh.id OR h.felhasznalasi_hely_id IS NULL)"
    )
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params={"uid": uid})

def ment_adatbazisba(feltoltott_fajl, szamlaszam, szolgaltato_id, hely_id, pdf_adat, szamla_kelte, fizetesi_hatarido, idoszak_tol, idoszak_ig, szamla_brutto_dec, osszes_brutto, elteres, user_id, mentendo_tetelek):
    try:
        pdf_utvonal = None
        if feltoltott_fajl is not None:
            feltoltott_fajl.seek(0)
            fajl_bytes = feltoltott_fajl.read()
            tiszta_fajlnev = f"{date.today().year}/{szamlaszam.replace('/', '_')}_{feltoltott_fajl.name}"
            supabase.storage.from_("szamlak-pdf").upload(tiszta_fajlnev, fajl_bytes, {"content-type": "application/pdf", "upsert": "true"})
            pdf_utvonal = tiszta_fajlnev

        forras = "PDF" if pdf_adat else ("TABLAZAT" if feltoltott_fajl else "KEZI")
        statusz = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"

        insert_szamla_sql = (
            "INSERT INTO szamlak (szamlaszam, szolgaltato_id, felhasznalasi_hely_id, forras_tipus, "
            "szamla_kelte, fizetesi_hatarido, idoszak_tol, idoszak_ig, szamlan_szereplo_brutto, "
            "szamitott_brutto, elteres, ellenorzes_statusz, pdf_utvonal, jovahagyta_energetikus_id, jovahagyas_ideje) "
            "VALUES (:szamlaszam, :szolg_id, :hely_id, :forras, :kelte, :hatarido, :tol, :ig, "
            ":szamla_br, :szam_br, :elt, :stat, :pdf, :uid, :most) RETURNING id"
        )
        insert_tetel_sql = (
            "INSERT INTO szamla_tetelek (szamla_id, megnevezes_eredeti, mennyiseg, mertekegyseg, "
            "egysegar_netto, szamitott_netto, afa_kulcs, szamitott_brutto) "
            "VALUES (:sid, :megn, :menny, :me, :ar, :netto, :afa, :brutto)"
        )

        with engine.begin() as conn:
            res = conn.execute(text(insert_szamla_sql), {
                "szamlaszam": szamlaszam, "szolg_id": szolgaltato_id, "hely_id": hely_id,
                "forras": forras, "kelte": szamla_kelte, "hatarido": fizetesi_hatarido,
                "tol": idoszak_tol, "ig": idoszak_ig, "szamla_br": szamla_brutto_dec,
                "szam_br": osszes_brutto, "elt": elteres, "stat": statusz,
                "pdf": pdf_utvonal, "uid": user_id, "most": datetime.now()
            })
            uj_szamla_id = res.scalar()

            for t in mentendo_tetelek:
                conn.execute(text(insert_tetel_sql), {
                    "sid": uj_szamla_id, "megn": t["megnevezes"], "menny": t["mennyiseg"],
                    "me": t["mertekegyseg"], "ar": t["egysegar"], "netto": t["netto"],
                    "afa": t["afa"], "brutto": t["brutto"]
                })

        st.balloons()
        st.success(f"Sikeres mentés! A számla és a PDF bekerült az adatbázisba (ID: #{uj_szamla_id}).")
    except Exception as e:
        st.error(f"Hiba történt a mentéskor: {e}")

st.sidebar.title("⚡ Energia Portál")
with engine.connect() as conn:
    users_df = pd.read_sql(text("SELECT id, nev, szerepkor FROM energetikusok WHERE aktiv = TRUE ORDER BY nev"), conn)

if users_df.empty:
    st.error("Nincs aktív energetikus az adatbázisban! Vegyél fel egyet a Supabase Table Editorban.")
    st.stop()

kivalasztott_nev = st.sidebar.selectbox("Belépett energetikus:", users_df["nev"])
user_sor = users_df[users_df["nev"] == kivalasztott_nev].iloc[0]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", ["🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok"])

if menu == "🧾 Számla ellenőrzés és feltöltés":
    st.header("Energiaszámla ellenőrzése és rögzítése")
    helyek_df = get_energetikus_helyei(user_id)
    if helyek_df.empty:
        st.warning("Nincs hozzád rendelt felhasználási hely a rendszerben.")
        st.stop()

    feltoltott_fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=["pdf", "xlsx", "csv"])
    pdf_adat = None
    if feltoltott_fajl and feltoltott_fajl.name.lower().endswith(".pdf"):
        feltoltott_fajl.seek(0)
        pdf_adat = feldolgoz_pdf(feltoltott_fajl)
        if pdf_adat["szamlaszam"]:
            st.success(f"PDF sikeresen kiolvasva! Számlaszám: **{pdf_adat['szamlaszam']}**")

    hely_opciok = {f"{r['megnevezes']} ({r['pod_eic_azonosito']}) [{r['jogosultsag']}]": r["id"] for _, r in helyek_df.iterrows()}
    alap_index = 0
    if pdf_adat and pdf_adat["pod"]:
        for idx, label in enumerate(hely_opciok.keys()):
            if pdf_adat["pod"] in label:
                alap_index = idx
                break

    st.subheader("2. Számla alapadatok")
    c1, c2, c3 = st.columns(3)
    with c1:
        valasztott_hely = st.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_index)
        hely_id = hely_opciok[valasztott_hely]
        szamlaszam = st.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "")
    with c2:
        with engine.connect() as conn:
            szolg_df = pd.read_sql(text("SELECT id, nev FROM szolgaltatok ORDER BY nev"), conn)
        if szolg_df.empty:
            st.error("Nincs szolgáltató az adatbázisban! Vegyél fel egyet a Supabase-ben.")
            st.stop()
        szolg_opciok = dict(zip(szolg_df["nev"], szolg_df["id"]))
        szolg_nev = st.selectbox("Szolgáltató", list(szolg_opciok.keys()))
        szolgaltato_id = szolg_opciok[szolg_nev]
        szamlan_brutto = st.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=pdf_adat["brutto"] if pdf_adat else 0.0, step=1.0, format="%.2f")
    with c3:
        szamla_kelte = st.date_input("Számla kelte", value=pdf_adat["szamla_kelte"] if pdf_adat else date.today())
        fizetesi_hatarido = st.date_input("Fizetési határidő", value=pdf_adat["fizetesi_hatarido"] if pdf_adat else date.today())
        idoszak_tol = st.date_input("Időszak kezdete", value=pdf_adat["idoszak_tol"] if pdf_adat else date.today())
        idoszak_ig = st.date_input("Időszak vége", value=pdf_adat["idoszak_ig"] if pdf_adat else date.today())

    st.subheader("3. Díjtételek ellenőrzése")
    if pdf_adat and len(pdf_adat["tetelek"]) > 0:
