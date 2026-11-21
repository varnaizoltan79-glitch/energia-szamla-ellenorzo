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

supabase = get_supabase()

def feldolgoz_pdf(feltoltott_fajl):
    reader = PdfReader(feltoltott_fajl)
    oldalak_szovege = []
    for page in reader.pages:
        try:
            oldalak_szovege.append(page.extract_text(extraction_mode="layout") or "")
        except Exception:
            oldalak_szovege.append(page.extract_text() or "")
    teljes_szoveg = "\n".join(oldalak_szovege)

    adatok = {
        "nyers_szoveg": teljes_szoveg.strip(),
        "szamlaszam": "",
        "szamla_kelte": date.today(),
        "fizetesi_hatarido": date.today(),
        "idoszak_tol": date.today(),
        "idoszak_ig": date.today(),
        "brutto": 0.0,
        "pod": "",
        "tetelek": []
    }

    # 1. Számlaszám keresése (szövegből vagy fájlnévből)
    m_szamlaszam = re.search(r"Számla\s*sorszáma[:\s]+([A-Za-z0-9/-]{6,})", teljes_szoveg, re.IGNORECASE)
    if m_szamlaszam:
        adatok["szamlaszam"] = m_szamlaszam.group(1).strip()
    else:
        m_fajl = re.search(r"(\d{8,})", feltoltott_fajl.name)
        if m_fajl:
            adatok["szamlaszam"] = m_fajl.group(1)

    # 2. Dátumok keresése
    m_kelte = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", teljes_szoveg, re.IGNORECASE)
    if m_kelte:
        adatok["szamla_kelte"] = datetime.strptime(m_kelte.group(1), "%Y.%m.%d").date()

    m_hatarido = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", teljes_szoveg, re.IGNORECASE)
    if m_hatarido:
        adatok["fizetesi_hatarido"] = datetime.strptime(m_hatarido.group(1), "%Y.%m.%d").date()

    m_idoszak = re.search(r"Elszámolt\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", teljes_szoveg, re.IGNORECASE)
    if m_idoszak:
        adatok["idoszak_tol"] = datetime.strptime(m_idoszak.group(1), "%Y.%m.%d").date()
        adatok["idoszak_ig"] = datetime.strptime(m_idoszak.group(2), "%Y.%m.%d").date()

    # 3. Bruttó végösszeg keresése
    m_brutto = re.search(r"(?:Fizetendő\s*összeg|Számla\s*végösszege|Bruttó\s*összesen)[:\s]+([\d\.\s]+)\s*Ft", teljes_szoveg, re.IGNORECASE)
    if m_brutto:
        tiszta_szam = m_brutto.group(1).replace(".", "").replace(" ", "").replace(",", ".")
        try:
            adatok["brutto"] = float(tiszta_szam)
        except ValueError:
            pass

    # 4. POD / mérési pont azonosító keresése
    m_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", teljes_szoveg)
    if m_pod:
        adatok["pod"] = m_pod.group(1).strip()

    # 5. Díjtételek sorainak keresése (rugalmas táblázatos minta)
    tetel_minta = re.compile(
        r"([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű\"'\(\)\-\s]{4,}?)\s+(?:\d{4}\.\d{2}\.\d{2}\s*[-–]\s*\d{4}\.\d{2}\.\d{2}\s+)?([\d\s\.,]+)\s+(kWh|hó|m3|MJ|db)\s+([\d\s\.,]+)\s*(?:Ft/\w+)?\s+[\d\s\.,]+\s+(\d{1,2})"
    )
    for sor in teljes_szoveg.splitlines():
        talalat = tetel_minta.search(sor)
        if talalat:
            nev = re.sub(r"\s+", " ", talalat.group(1)).strip()
            if len(nev) < 4 or "összesen" in nev.lower():
                continue
            try:
                menny = float(talalat.group(2).replace(" ", "").replace(".", "").replace(",", "."))
                me = talalat.group(3).strip()
                ar = float(talalat.group(4).replace(" ", "").replace(".", "").replace(",", "."))
                afa = float(talalat.group(5))
                if menny > 0 and ar > 0:
                    adatok["tetelek"].append({
                        "Megnevezés": nev,
                        "Mennyiség": menny,
                        "Mértékegység": me,
                        "Nettó egységár": ar,
                        "ÁFA (%)": afa
                    })
            except ValueError:
                continue

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
    st.warning("Nincs aktív energetikus az adatbázisban!")
    st.stop()

kivalasztott_nev = st.sidebar.selectbox("Belépett energetikus:", users_df["nev"])
user_sor = users_df[users_df["nev"] == kivalasztott_nev].iloc
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
        if not pdf_adat["nyers_szoveg"]:
            st.warning("⚠️ Ez a PDF szkennelt képként készült (nem tartalmaz kijelölhető szöveget), ezért az adatokat lent kézzel kell beírni, de a PDF fájlt ugyanúgy elmenti a rendszer!")
        elif pdf_adat["szamlaszam"] or pdf_adat["brutto"] > 0:
            st.success(f"✅ PDF feldolgozva! Talált számlaszám: **{pdf_adat['szamlaszam']}**, Bruttó: **{pdf_adat['brutto']:,.0f} Ft**, Tételek: **{len(pdf_adat['tetelek'])} db**")
        
        with st.expander("🔍 Kiolvasott PDF szöveg megtekintése (ellenőrzéshez)"):
            st.text(pdf_adat["nyers_szoveg"][:3000] if pdf_adat["nyers_szoveg"] else "Üres (képalapú szkennelt PDF)")

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
