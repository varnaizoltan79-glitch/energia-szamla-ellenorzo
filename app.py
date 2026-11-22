import base64
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import fitz
import pandas as pd
import streamlit as st
from pypdf import PdfReader
from supabase import create_client

st.set_page_config(page_title="Energiaszámla Ellenőrző", page_icon="⚡", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"].strip()
SUPABASE_KEY = st.secrets["SUPABASE_KEY"].strip()

@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

if "form_ver" not in st.session_state:
    st.session_state["form_ver"] = 0

def urit_urlap():
    st.session_state["form_ver"] += 1

HONAPOK_HU = ("", "január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december")

def szamol_elszamolasi_honap(ig_datum):
    ev, ho = ig_datum.year, ig_datum.month
    if ig_datum.day <= 14:
        ho -= 1
        if ho == 0:
            ho = 12
            ev -= 1
    return f"{ev}-{ho:02d} ({ev}. {HONAPOK_HU[ho]})"

@st.dialog("📄 Számla PDF előnézet", width="large")
def mutat_pdf_ablak(pdf_utvonal, szamlaszam):
    st.subheader(f"Számlaszám: {szamlaszam} ({pdf_utvonal})")
    pdf_bytes = supabase.storage.from_("szamlak-pdf").download(pdf_utvonal)
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    for idx, oldal in enumerate(dok):
        kep = oldal.get_pixmap(dpi=150)
        st.image(kep.tobytes("png"), caption=f"{idx + 1}. oldal", use_container_width=True)
    st.download_button("📥 PDF letöltése a gépre", data=pdf_bytes, file_name=pdf_utvonal, mime="application/pdf")

def feldolgoz_pdf(fajl):
    reader = PdfReader(fajl)
    oldalak = list(p.extract_text() or "" for p in reader.pages)
    szoveg = "\n".join(oldalak)
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": ""}
    m = re.search(r"(?:Számla\s*sorszáma|Számlaszám|Bizonylat\s*sorszáma)[:\s]+([A-Za-z0-9/-]{5,})", szoveg, re.I)
    if m: adat["szamlaszam"] = m.group(1).strip()
    alap_nev = next(iter(fajl.name.rsplit(".", 1)))
    mf = re.search(r"(\d{6,})", alap_nev)
    if not adat["szamlaszam"] and mf: adat["szamlaszam"] = mf.group(1)
    if not adat["szamlaszam"] and len(alap_nev) >= 5: adat["szamlaszam"] = alap_nev
    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk: adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh: adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mi = re.search(r"Elszámolt\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mi:
        adat["tol"] = datetime.strptime(mi.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(mi.group(2), "%Y.%m.%d").date()
    mb = re.search(r"(?:Fizetendő\s*összeg|Számla\s*végösszege)[:\s]+([\d\.\s]+)\s*Ft", szoveg, re.I)
    if mb: adat["brutto"] = float(mb.group(1).replace(".", "").replace(" ", "").replace(",", "."))
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()
    return adat

def get_helyek(uid):
    ma = date.today().isoformat()
    helyek = supabase.table("felhasznalasi_helyek").select("*").eq("aktiv", True).execute().data or list()
    helyett = supabase.table("helyettesitesek").select("*").eq("helyettesito_energetikus_id", uid).eq("aktiv", True).lte("kezdet_datum", ma).gte("vege_datum", ma).execute().data or list()
    lista = list()
    for h in helyek:
        if h.get("felelos_energetikus_id") == uid or h.get("allando_helyettes_id") == uid:
            lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Saját"})
    for hel in helyett:
        for h in helyek:
            if h.get("felelos_energetikus_id") == hel.get("helyettesitett_energetikus_id"):
                if hel.get("felhasznalasi_hely_id") is None or hel.get("felhasznalasi_hely_id") == h["id"]:
                    lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Helyettesítés"})
    return lista

def szamol_tetelek(df):
    ossz_netto = Decimal("0")
    ossz_brutto = Decimal("0")
    tetelek = list()
    for _, row in df.iterrows():
        m_val = row.get("Mennyiség")
        a_val = row.get("Nettó egységár")
        if m_val is None or a_val is None or pd.isna(m_val) or pd.isna(a_val):
            continue
        menny = Decimal(str(m_val))
        ar = Decimal(str(a_val))
        if menny == 0 and ar == 0:
            continue
        afa_val = row.get("ÁFA (%)")
        afa = Decimal(str(afa_val)) if (afa_val is not None and not pd.isna(afa_val)) else Decimal("27.0")
        netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ossz_netto += netto
        ossz_brutto += brutto
        tetelek.append({"megnevezes_eredeti": str(row.get("Megnevezés") or "Díjtétel"), "mennyiseg": float(menny), "mertekegyseg": str(row.get("Mértékegység") or "kWh"), "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})
    return ossz_netto, ossz_brutto, tetelek

def mentes_adatbazisba(uj_szamla, tetelek, fajl):
    try:
        res = supabase.table("szamlak").insert(uj_szamla).execute()
        sid = next(iter(res.data))["id"]
        for t in tetelek:
            t["szamla_id"] = sid
        supabase.table("szamla_tetelek").insert(tetelek).execute()
        if fajl is not None:
            fajl.seek(0)
            fnev = f"{date.today().year}_{uj_szamla['szamlaszam'].replace('/', '_')}.pdf"
            supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
            supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
        st.session_state["utolso_mentes"] = f"✅ Sikeresen megtörtént a számla rögzítése! (Számlaszám: {uj_szamla['szamlaszam']}, Elszámolási hónap: {uj_szamla['elszamolasi_honap']}, ID: #{sid})"
        urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")
st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok")), on_change=urit_urlap)

if menu == "📊 Kimutatások és Riportok":
    st.header("Rögzített számlák és kimutatások")
    lista = supabase.table("szamlak").select("*").order("jovahagyas_ideje", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        st.stop()
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}
    sorok = list({"Számlaszám": s["szamlaszam"], "Elszámolási hónap": s.get("elszamolasi_honap") or "", "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""), "Időszak kezdete": s["idoszak_tol"], "Időszak vége": s["idoszak_ig"], "Bruttó összeg (Ft)": float(s["szamlan_szereplo_brutto"]), "Státusz": s["ellenorzes_statusz"], "PDF fájl": s["pdf_utvonal"]} for s in lista)
    df = pd.DataFrame(sorok)
    st.bar_chart(df.groupby("Felhasználási hely")["Bruttó összeg (Ft)"].sum())
    st.caption("💡 Kattints egy sor elejére a táblázatban a csatolt PDF számla megnyitásához!")
    esemeny = st.dataframe(df, use_container_width=True, on_select="rerun", selection_mode="single-row")
    if esemeny.selection.rows:
        kiv_idx = next(iter(esemeny.selection.rows))
        kiv_szamla = lista[kiv_idx]
        if kiv_szamla.get("pdf_utvonal"):
            mutat_pdf_ablak(kiv_szamla["pdf_utvonal"], kiv_szamla["szamlaszam"])
        else:
            st.warning("Ehhez a számlához nem lett PDF fájl feltöltve.")
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Kimutatás letöltése Excelbe (CSV)", data=csv, file_name=f"energia_riport_{date.today()}.csv", mime="text/csv")
    st.divider()
    st.subheader("🗑️ Számla törlése")
    torles_opciok = {f"#{s['id']} – {s['szamlaszam']} ({h_map.get(s['felhasznalasi_hely_id'], '')}, {float(s['szamlan_szereplo_brutto']):,.0f} Ft)": s for s in lista}
    valasztott_torles = st.selectbox("Válaszd ki a törlendő számlát:", list(torles_opciok.keys()))
    if st.button("🗑️ Kiválasztott számla végleges törlése"):
        kiv = torles_opciok[valasztott_torles]
        supabase.table("szamla_tetelek").delete().eq("szamla_id", kiv["id"]).execute()
        if kiv.get("pdf_utvonal"):
            supabase.storage.from_("szamlak-pdf").remove(list((kiv["pdf_utvonal"],)))
        supabase.table("szamlak").delete().eq("id", kiv["id"]).execute()
        st.success(f"✅ A(z) {kiv['szamlaszam']} számú számla sikeresen törölve lett!")
        st.rerun()
    st.stop()

fv = st.session_state["form_ver"]
col_fej, col_gomb = st.columns((4, 1))
col_fej.header("Energiaszámla ellenőrzése és rögzítése")
if col_gomb.button("🗑️ Új számla / Ürítés"):
    urit_urlap()
    st.rerun()

if "utolso_mentes" in st.session_state:
    st.success(st.session_state.pop("utolso_mentes"))

helyek = get_helyek(user_id)
if not helyek:
    st.warning("Nincs hozzád rendelt felhasználási hely a rendszerben.")
    st.stop()

fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=list(("pdf", "xlsx", "csv")), key=f"fajl_{fv}")
f_id = f"{fv}_{fajl.name}" if fajl else f"{fv}_ures"
pdf_adat = None
if fajl and fajl.name.lower().endswith(".pdf"):
    fajl.seek(0)
    pdf_adat = feldolgoz_pdf(fajl)
    if not pdf_adat["szoveg"]: st.warning("Szkennelt PDF: a tételeket lent kézzel írd be, a PDF fájlt ugyanúgy elmenti a rendszer.")
    if pdf_adat["szamlaszam"]: st.success(f"PDF feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}**")

hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
alap_idx = 0
if pdf_adat and pdf_adat["pod"]:
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if pdf_adat["pod"] in lbl), 0)

st.subheader("2. Számla alapadatok")
c1, c2, c3 = st.columns(3)
v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx, key=f"hely_{f_id}")
hely_id = hely_opciok[v_hely]
szamlaszam = c1.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "", key=f"szsz_{f_id}")

szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()), key=f"szolg_{f_id}")
szolg_id = szolg_opciok[v_szolg]
szamlan_brutto = c2.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=pdf_adat["brutto"] if pdf_adat else 0.0, step=1.0, format="%.2f", key=f"br_{f_id}")

kelte = pdf_adat["kelte"] if pdf_adat else date.today()
hatarido = pdf_adat["hatarido"] if pdf_adat else date.today()
tol = c3.date_input("Elszámolási időszak kezdete", value=pdf_adat["tol"] if pdf_adat else date.today(), key=f"t_{f_id}")
ig = c3.date_input("Elszámolási időszak vége", value=pdf_adat["ig"] if pdf_adat else date.today(), key=f"i_{f_id}")
elszamolasi_honap = szamol_elszamolasi_honap(ig)
c3.info(f"📅 Elszámolási hónap: **{elszamolasi_honap}**")

st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
alap_df = pd.DataFrame(dict(Megnevezés=["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"], Mennyiség=[0.0, 0.0, 0.0, 0.0], Mértékegység=["kWh", "kWh", "kWh", "hó"], Nettó_egységár=[0.0, 0.0, 0.0, 0.0], ÁFA=[27.0, 27.0, 27.0, 27.0]))
alap_df.columns = list(("Megnevezés", "Mennyiség", "Mértékegység", "Nettó egységár", "ÁFA (%)"))
if fajl and fajl.name.lower().endswith(".xlsx"): alap_df = pd.read_excel(fajl)
if fajl and fajl.name.lower().endswith(".csv"): alap_df = pd.read_csv(fajl)

szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True, key=f"ed_{f_id}")
ossz_netto, ossz_brutto, tetelek = szamol_tetelek(szerk_df)

brutto_dec = Decimal(str(szamlan_brutto)).quantize(Decimal("0.01"))
elteres = abs(brutto_dec - ossz_brutto)

m1, m2, m3 = st.columns(3)
m1.metric("Számított Nettó", f"{ossz_netto:,.0f} Ft")
m2.metric("Számított Bruttó", f"{ossz_brutto:,.0f} Ft")
m3.metric("Eltérés a számlától", f"{elteres:,.2f} Ft")

egyezik = (elteres <= Decimal("2.00")) and (len(tetelek) > 0) and (szamlan_brutto > 0)
if egyezik: st.success("✅ A számított bruttó összeg megegyezik a számlán szereplővel!")
if not egyezik: st.warning("A mentés gomb akkor válik aktívvá, ha a számított bruttó összeg egyezik a számla összegével.")

gomb = st.button("💾 PDF feltöltése és Számla mentése az adatbázisba", type="primary", disabled=not egyezik)
forras = "PDF" if pdf_adat else ("TABLAZAT" if fajl else "KEZI")
stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
uj_szamla = {"szamlaszam": szamlaszam, "szolgaltato_id": szolg_id, "felhasznalasi_hely_id": hely_id, "forras_tipus": forras, "szamla_kelte": kelte.isoformat(), "fizetesi_hatarido": hatarido.isoformat(), "idoszak_tol": tol.isoformat(), "idoszak_ig": ig.isoformat(), "elszamolasi_honap": elszamolasi_honap, "szamlan_szereplo_brutto": float(brutto_dec), "szamitott_brutto": float(ossz_brutto), "elteres": float(elteres), "ellenorzes_statusz": stat, "jovahagyta_energetikus_id": user_id}
if gomb and egyezik: mentes_adatbazisba(uj_szamla, tetelek, fajl)
