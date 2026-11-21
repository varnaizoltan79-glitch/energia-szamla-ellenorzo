import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
import streamlit as st
from pypdf import PdfReader
from supabase import create_client

st.set_page_config(page_title="Energiaszámla Ellenőrző", page_icon="⚡", layout="wide")

try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"].strip()
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"].strip()
except Exception as e:
    st.error(f"Hiányoznak a Secrets beállítások: {e}")
    st.stop()

@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    supabase = get_supabase()
except Exception as e:
    st.error(f"Kapcsolódási hiba a Supabase-hez: {e}")
    st.stop()

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
    ma_str = date.today().isoformat()
    helyek = supabase.table("felhasznalasi_helyek").select("*").eq("aktiv", True).execute().data or []
    helyettesitesek = (
        supabase.table("helyettesitesek")
        .select("*")
        .eq("helyettesito_energetikus_id", uid)
        .eq("aktiv", True)
        .lte("kezdet_datum", ma_str)
        .gte("vege_datum", ma_str)
        .execute()
        .data or []
    )
    
    eredmeny = []
    latott_ids = set()
    for h in helyek:
        if h.get("felelos_energetikus_id") == uid or h.get("allando_helyettes_id") == uid:
            eredmeny.append({
                "id": h["id"],
                "megnevezes": h["megnevezes"],
                "pod_eic_azonosito": h["pod_eic_azonosito"],
                "jogosultsag": "Saját"
            })
            latott_ids.add(h["id"])
            
    for hel in helyettesitesek:
        tavollevo_id = hel.get("helyettesitett_energetikus_id")
        spec_hely_id = hel.get("felhasznalasi_hely_id")
        for h in helyek:
            if h["id"] in latott_ids:
                continue
            if h.get("felelos_energetikus_id") == tavollevo_id:
                if spec_hely_id is None or spec_hely_id == h["id"]:
                    eredmeny.append({
                        "id": h["id"],
                        "megnevezes": h["megnevezes"],
                        "pod_eic_azonosito": h["pod_eic_azonosito"],
                        "jogosultsag": "Helyettesítés"
                    })
                    latott_ids.add(h["id"])
    return pd.DataFrame(eredmeny)

# --- FELÜLET INDÍTÁSA ---
st.sidebar.title("⚡ Energia Portál")

try:
    users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or []
    users_df = pd.DataFrame(users_data)
except Exception as e:
    st.error(f"Adatbázis olvasási hiba: {e}")
    st.stop()

if users_df.empty:
    st.warning("Nincs aktív energetikus az adatbázisban! Nyisd meg a Supabase Table Editorban az 'energetikusok' táblát, és vegyél fel legalább egy sort.")
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
        st.warning("Nincs hozzád rendelt felhasználási hely a rendszerben. Rendelj hozzá egyet a Supabase 'felhasznalasi_helyek' táblájában!")
        st.stop()

    feltoltott_fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=["pdf", "xlsx", "csv"])
    pdf_adat = None
    if feltoltott_fajl and feltoltott_fajl.name.lower().endswith(".pdf"):
        feltoltott_fajl.seek(0)
        pdf_adat = feldolgoz_pdf(feltoltott_fajl)
        if pdf_adat["szamlaszam"]:
            st.success(f"PDF sikeresen kiolvasva! Számlaszám: **{pdf_adat['szamlaszam']}**")

    hely_opciok = {f"{r['megnevezes']} ({r['pod_eic_azonosito']}) [{r['jogosultsag']}]": int(r["id"]) for _, r in helyek_df.iterrows()}
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
        szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or []
        szolg_df = pd.DataFrame(szolg_data)
        if szolg_df.empty:
            st.error("Nincs szolgáltató az adatbázisban! Vegyél fel egyet a Supabase 'szolgaltatok' táblájában.")
            st.stop()
        szolg_opciok = dict(zip(szolg_df["nev"], szolg_df["id"]))
        szolg_nev = st.selectbox("Szolgáltató", list(szolg_opciok.keys()))
        szolgaltato_id = int(szolg_opciok[szolg_nev])
        szamlan_brutto = st.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=pdf_adat["brutto"] if pdf_adat else 0.0, step=1.0, format="%.2f")
    with c3:
        szamla_kelte = st.date_input("Számla kelte", value=pdf_adat["szamla_kelte"] if pdf_adat else date.today())
        fizetesi_hatarido = st.date_input("Fizetési határidő", value=pdf_adat["fizetesi_hatarido"] if pdf_adat else date.today())
        idoszak_tol = st.date_input("Időszak kezdete", value=pdf_adat["idoszak_tol"] if pdf_adat else date.today())
        idoszak_ig = st.date_input("Időszak vége", value=pdf_adat["idoszak_ig"] if pdf_adat else date.today())

    st.subheader("3. Díjtételek ellenőrzése")
    if pdf_adat and len(pdf_adat["tetelek"]) > 0:
        alap_tetelek = pd.DataFrame(pdf_adat["tetelek"])
    elif feltoltott_fajl and feltoltott_fajl.name.lower().endswith(".xlsx"):
        alap_tetelek = pd.read_excel(feltoltott_fajl)
    elif feltoltott_fajl and feltoltott_fajl.name.lower().endswith(".csv"):
        alap_tetelek = pd.read_csv(feltoltott_fajl)
    else:
        alap_tetelek = pd.DataFrame([
            {"Megnevezés": "Energiadíj", "Mennyiség": 0.0, "Mértékegység": "kWh", "Nettó egységár": 0.0, "ÁFA (%)": 27.0}
        ])

    szerkesztett_df = st.data_editor(alap_tetelek, num_rows="dynamic", use_container_width=True)

    osszes_netto = Decimal("0")
    osszes_brutto = Decimal("0")
    mentendo_tetelek = []

    for _, row in szerkesztett_df.iterrows():
        if pd.isna(row.get("Mennyiség")) or pd.isna(row.get("Nettó egységár")):
            continue
        menny = Decimal(str(row["Mennyiség"]))
        ar = Decimal(str(row["Nettó egységár"]))
        afa = Decimal(str(row.get("ÁFA (%)", 27.0)))

        netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        osszes_netto += netto
        osszes_brutto += brutto
        mentendo_tetelek.append({
            "megnevezes": str(row.get("Megnevezés", "Díjtétel")),
            "mennyiseg": float(menny),
            "mertekegyseg": str(row.get("Mértékegység", "kWh")),
            "egysegar": float(ar),
            "netto": float(netto),
            "afa": float(afa),
            "brutto": float(brutto)
        })

    szamla_brutto_dec = Decimal(str(szamlan_brutto)).quantize(Decimal("0.01"))
    elteres = abs(szamla_brutto_dec - osszes_brutto)

