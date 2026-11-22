import base64
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import altair as alt
import fitz
import pandas as pd
import streamlit as st
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

def formaz_ezres(kulcs):
    nyers = re.sub(r"\D", "", st.session_state.get(kulcs, ""))
    st.session_state[kulcs] = f"{int(nyers):,}".replace(",", " ") if nyers else ""

def ft_szoveg(ertek):
    return f"{int(round(float(ertek))):,} Ft".replace(",", " ")

HONAPOK_HU = ("", "január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december")

def szamol_elszamolasi_honap(ig_datum):
    ev, ho = ig_datum.year, ig_datum.month
    if ig_datum.day <= 14:
        ho -= 1
        if ho == 0:
            ho = 12
            ev -= 1
    return f"{ev}. {HONAPOK_HU[ho]}"

def tiszta_honap(cimke):
    if not cimke:
        return "Nincs megadva"
    m = re.search(r"\((\d{4}\.\s*[a-záéíóöőúüű]+)\)", cimke)
    return m.group(1) if m else cimke

def honap_kulcs(cimke):
    m = re.match(r"(\d{4})\.\s*([a-záéíóöőúüű]+)", cimke)
    if m and m.group(2) in HONAPOK_HU:
        return (int(m.group(1)), HONAPOK_HU.index(m.group(2)))
    return (9999, 99)

@st.dialog("📄 Számla PDF előnézet", width="large")
def mutat_pdf_ablak(pdf_utvonal, szamlaszam):
    st.subheader(f"Számlaszám: {szamlaszam} ({pdf_utvonal})")
    pdf_bytes = supabase.storage.from_("szamlak-pdf").download(pdf_utvonal)
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    for idx, oldal in enumerate(dok):
        kep = oldal.get_pixmap(dpi=150)
        st.image(kep.tobytes("png"), caption=f"{idx + 1}. oldal", use_container_width=True)
    st.download_button("📥 PDF letöltése a gépre", data=pdf_bytes, file_name=pdf_utvonal, mime="application/pdf")

def _hu_szam(szoveg):
    if not szoveg:
        return None
    tiszta = szoveg.replace("\xa0", " ").strip()
    tiszta = re.sub(r"(?<=\d)\s+(?=\d)", "", tiszta)
    if "," in tiszta and "." in tiszta:
        tiszta = tiszta.replace(".", "").replace(",", ".")
    elif "," in tiszta:
        tiszta = tiszta.replace(",", ".")
    elif tiszta.count(".") > 1:
        tiszta = tiszta.replace(".", "")
    try:
        return float(tiszta)
    except ValueError:
        return None

SZOLGALTATO_SABLONOK = {
    "ALTEO": {
        "azonositok": ("alteo energiakereskedő", "17783893"),
        "szolgaltato_nev_kulcs": "ALTEO",
        "tetelek": (
            "Villamos energia díj",
            "Kedvezményes árú vill. energia támogatás",
            "Kapcsolt termelésszerk.-átalakítási díj",
            "Jövedéki adó",
            "Elosztói forgalmi díj",
            "Elosztói alapdíj",
            "Elosztói lekötött teljesítménydíj",
            "Meddő energia díj",
            "Átviteli forgalmi díj",
            "Közbeszerzési díj villamosenergiadíj alapján",
            "EKR díj",
            "KÁT pénzeszköz visszatérítés",
        )
    }
}

def _vizszintes_sorok(oldal):
    szavak = oldal.get_text("words")
    if not szavak:
        return list()
    rendezett = sorted(szavak, key=lambda w: (round(w[1] / 3.0), w[0]))
    sorok = list()
    _, akt_y, _, _, _, *_ = rendezett[0]
    akt_szavak = list()
    for w in rendezett:
        x0, y0, _, _, szo, *_ = w
        if abs(y0 - akt_y) <= 3.5:
            akt_szavak.append((x0, szo))
        else:
            akt_szavak.sort(key=lambda item: item[0])
            sorok.append(" ".join(item[1] for item in akt_szavak))
            akt_y = y0
            akt_szavak = [(x0, szo)]
    if akt_szavak:
        akt_szavak.sort(key=lambda item: item[0])
        sorok.append(" ".join(item[1] for item in akt_szavak))
    return sorok

def _feldolgoz_alteo_pdf(dok, szoveg, adat):
    adat["szolgaltato_kulcs"] = "ALTEO"
    elso_oldal = next(iter(dok))
    oldal1_sorok = _vizszintes_sorok(elso_oldal)

    m_sz_teljes = re.search(r"Számlaszám\s*:?\s*(\d{6,})", szoveg)
    if m_sz_teljes:
        adat["szamlaszam"] = m_sz_teljes.group(1)

    for s in oldal1_sorok:
        m_sz = re.search(r"Számlaszám[:\s]+(\d{6,})", s)
        if m_sz:
            adat["szamlaszam"] = m_sz.group(1)
        m_ido = re.search(r"Időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", s)
        if m_ido:
            adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
            adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()
        m_k = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_k:
            adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()
        m_h = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_h:
            adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

    m_br = re.search(r"Fizetendő\s*összesen[:\s]+([\d\.\s\xa0]+,\d{2})\s*Ft", szoveg)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))

    szam_minta = r"-?\d[\d\.]*,\d+"
    me_minta = r"(kWh|MWh|csp|kW|kVAhr|db|hó|m3|MJ|GJ)"
    tetel_minta = re.compile(rf"^(.+?)\s+({szam_minta})\s+{me_minta}\s+({szam_minta})\s*Ft\s+({szam_minta}|-?\d[\d\.]*)\s+(27|5|18|0|AHK)\b")
    kizaro = ("összesen", "Tétel megnevezés", "Pénzeszközök", "Energia-jövedéki", "Rendszerhasználati", "Közbeszerzési díjak", "EKR díj", "Számlaszám", "Oldal:", "Forgalmi tételek")

    talalatok = list()
    for idx, s in enumerate(oldal1_sorok):
        m = tetel_minta.match(s)
        if not m:
            continue
        nev = m.group(1).strip()
        menny = _hu_szam(m.group(2))
        me = m.group(3)
        ar = _hu_szam(m.group(4))
        afa_nyers = m.group(6)
        afa = 0.0 if afa_nyers == "AHK" else float(afa_nyers)

        if idx + 1 < len(oldal1_sorok):
            kov = oldal1_sorok[idx + 1].strip()
            if not tetel_minta.match(kov) and not any(kz.lower() in kov.lower() for kz in kizaro) and not re.search(r"szolgáltatást tartalmaz", kov, re.I):
                nev = f"{nev} {kov}".strip()

        if menny is not None and ar is not None:
            talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    if talalatok:
        adat["tetelek_df"] = pd.DataFrame(talalatok)
    return adat




def feldolgoz_pdf(fajl):
    pdf_bytes = fajl.read()
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    szoveg = "\n".join(list(oldal.get_text("text") or "" for oldal in dok))
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0, "pod": "", "szolgaltato_kulcs": "", "tetelek_df": None}

    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk: adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh: adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()

    szoveg_low = szoveg.lower()
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["ALTEO"]["azonositok"]):
        return _feldolgoz_alteo_pdf(dok, szoveg, adat)

    m = re.search(r"(?:Számla\s*sorszáma|Számlaszám|Bizonylat\s*sorszáma)[:\s]+([A-Za-z0-9/-]{5,})", szoveg, re.I)
    if m: adat["szamlaszam"] = m.group(1).strip()
    alap_nev = next(iter(fajl.name.rsplit(".", 1)))
    mf = re.search(r"(\d{6,})", alap_nev)
    if not adat["szamlaszam"] and mf: adat["szamlaszam"] = mf.group(1)
    if not adat["szamlaszam"] and len(alap_nev) >= 5: adat["szamlaszam"] = alap_nev

    mi = re.search(r"(?:Elszámolt|Teljesítési|Elszámolási)\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mi:
        adat["tol"] = datetime.strptime(mi.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(mi.group(2), "%Y.%m.%d").date()
    mb = re.search(r"(?:Fizetendő\s*összeg|Fizetendő\s*összesen|Számla\s*végösszege)[:\s]+([\d\.\s\xa0]+(?:,\d+)?)\s*Ft", szoveg, re.I)
    if mb:
        br_val = _hu_szam(mb.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))
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
        st.session_state["utolso_mentes"] = f"✅ A számla rögzítése megtörtént (Számlaszám: {uj_szamla['szamlaszam']}, Elszámolási hónap: {uj_szamla['elszamolasi_honap']}, ID: #{sid})"
        urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")

def mutat_torzsadatok():
    st.header("⚙️ Törzsadatok és Helyettesítések kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    h_map = {h["id"]: h["megnevezes"] for h in osszes_hely}

    t_helyett, t_hely, t_user = st.tabs(list(("🔄 Helyettesítések", "🏭 Felhasználási helyek", "👤 Energetikusok")))
    with t_helyett:
        helyett_lista = supabase.table("helyettesitesek").select("*").order("kezdet_datum", desc=True).execute().data or list()
        if helyett_lista:
            h_sorok = list({"ID": x["id"], "Helyettesített": u_map.get(x["helyettesitett_energetikus_id"], ""), "Helyettesítő": u_map.get(x["helyettesito_energetikus_id"], ""), "Felhasználási hely": h_map.get(x["felhasznalasi_hely_id"], "Minden telephelye"), "Kezdet": x["kezdet_datum"], "Vége": x["vege_datum"]} for x in helyett_lista)
            st.dataframe(pd.DataFrame(h_sorok), use_container_width=True, hide_index=True)
            torol_h = st.selectbox("Helyettesítés törlése:", list(f"#{x['id']} – {u_map.get(x['helyettesitett_energetikus_id'], '')} -> {u_map.get(x['helyettesito_energetikus_id'], '')}" for x in helyett_lista))
            if st.button("🗑️ Kiválasztott helyettesítés törlése"):
                hid = int(next(iter(torol_h.split(" – "))).replace("#", ""))
                supabase.table("helyettesitesek").delete().eq("id", hid).execute()
                st.rerun()
        with st.form("uj_helyettesites_form"):
            hc1, hc2 = st.columns(2)
            kit = hc1.selectbox("Helyettesített energetikus:", list(aktiv_user_opciok.keys()))
            kivel = hc2.selectbox("Helyettesítő energetikus:", list(aktiv_user_opciok.keys()))
            hely_valasztek = {"Minden telephelyére érvényes": None}
            for h in osszes_hely:
                hely_valasztek[f"{h['megnevezes']} ({h['pod_eic_azonosito']})"] = h["id"]
            melyik_hely = st.selectbox("Érintett felhasználási hely:", list(hely_valasztek.keys()))
            hd1, hd2 = st.columns(2)
            h_tol = hd1.date_input("Kezdet", value=date.today())
            h_ig = hd2.date_input("Vége", value=date.today())
            if st.form_submit_button("💾 Helyettesítés mentése", type="primary"):
                if aktiv_user_opciok[kit] != aktiv_user_opciok[kivel] and h_tol <= h_ig:
                    supabase.table("helyettesitesek").insert({"helyettesitett_energetikus_id": aktiv_user_opciok[kit], "helyettesito_energetikus_id": aktiv_user_opciok[kivel], "felhasznalasi_hely_id": hely_valasztek[melyik_hely], "kezdet_datum": h_tol.isoformat(), "vege_datum": h_ig.isoformat(), "aktiv": True}).execute()
                    st.rerun()
    with t_hely:
        energia_opciok = list(("Villamos energia", "Földgáz", "Távhő", "Víziközmű", "PB-gáz"))
        if osszes_hely:
            st.dataframe(pd.DataFrame(list({"ID": h["id"], "Megnevezés": h["megnevezes"], "Energiatípus": h.get("energiatipus") or "", "POD / EIC": h["pod_eic_azonosito"], "Felelős": u_map.get(h.get("felelos_energetikus_id"), ""), "Állandó helyettes": u_map.get(h.get("allando_helyettes_id"), "Nincs")} for h in osszes_hely)), use_container_width=True, hide_index=True)
        with st.form("uj_hely_form"):
            fc1, fc2, fc3 = st.columns(3)
            uj_h_nev = fc1.text_input("Megnevezés:")
            uj_h_energia = fc2.selectbox("Energiatípus:", energia_opciok)
            uj_h_pod = fc3.text_input("POD / EIC azonosító:")
            fel_opc = {"Nincs kijelölve": None}
            fel_opc.update(aktiv_user_opciok)
            fc4, fc5 = st.columns(2)
            uj_h_fel = fc4.selectbox("Felelős energetikus:", list(fel_opc.keys()))
            uj_h_helyett = fc5.selectbox("Állandó helyettes:", list(fel_opc.keys()))
            if st.form_submit_button("💾 Felhasználási hely mentése", type="primary") and uj_h_nev.strip() and uj_h_pod.strip():
                supabase.table("felhasznalasi_helyek").insert({"megnevezes": uj_h_nev.strip(), "energiatipus": uj_h_energia, "pod_eic_azonosito": uj_h_pod.strip(), "felelos_energetikus_id": fel_opc[uj_h_fel], "allando_helyettes_id": fel_opc[uj_h_helyett], "aktiv": True}).execute()
                st.rerun()

    with t_user:
        if osszes_user:
            st.dataframe(pd.DataFrame(list({"ID": u["id"], "Név": u["nev"], "Szerepkör": u["szerepkor"], "Státusz": "Aktív" if u.get("aktiv") else "Inaktív"} for u in osszes_user)), use_container_width=True, hide_index=True)
        with st.form("uj_user_form"):
            uc1, uc2 = st.columns(2)
            uj_u_nev = uc1.text_input("Új energetikus teljes neve:")
            uj_u_szerep = uc2.selectbox("Szerepkör:", list(("Energetikus", "Vezető energetikus", "Adminisztrátor")))
            if st.form_submit_button("💾 Energetikus mentése", type="primary") and uj_u_nev.strip():
                uj_adat = {"nev": uj_u_nev.strip(), "szerepkor": uj_u_szerep, "aktiv": True}
                if osszes_user and "email" in next(iter(osszes_user)):
                    uj_adat["email"] = f"{re.sub(r'[^a-z0-9]', '', uj_u_nev.lower())}@ceg.hu"
                supabase.table("energetikusok").insert(uj_adat).execute()
                st.rerun()

        if osszes_user:
            st.divider()
            st.subheader("🔄 Energetikus inaktiválása / újraaktiválása")
            u_ver = st.session_state.get("user_stat_ver", 0)
            u_stat_opciok = {f"#{u['id']} – {u['nev']} ({u['szerepkor']}) [{'Aktív' if u.get('aktiv') else 'Inaktív'}]": u for u in osszes_user}
            val_u_stat = st.selectbox("Válaszd ki az energetikust:", list(u_stat_opciok.keys()), index=None, placeholder="Válassz ki egy energetikust...", key=f"u_stat_sel_{u_ver}")
            if val_u_stat:
                kiv_u = u_stat_opciok[val_u_stat]
                if kiv_u.get("aktiv"):
                    if st.button("🚫 Energetikus inaktiválása"):
                        st.session_state["u_inakt_megerosites_id"] = kiv_u["id"]
                    if st.session_state.get("u_inakt_megerosites_id") == kiv_u["id"]:
                        st.warning(f"⚠️ Biztos, hogy inaktiválni kívánja az energetikust? (**{kiv_u['nev']}**)")
                        uc_igen, uc_nem, _ = st.columns((1.2, 1.2, 4))
                        if uc_igen.button("✅ Igen", type="primary", key=f"u_inakt_igen_{kiv_u['id']}"):
                            supabase.table("energetikusok").update({"aktiv": False}).eq("id", kiv_u["id"]).execute()
                            st.session_state.pop("u_inakt_megerosites_id", None)
                            st.session_state["user_stat_ver"] = u_ver + 1
                            st.success(f"✅ {kiv_u['nev']} inaktiválva lett!")
                            st.rerun()
                        if uc_nem.button("❌ Nem", key=f"u_inakt_nem_{kiv_u['id']}"):
                            st.session_state.pop("u_inakt_megerosites_id", None)
                            st.session_state["user_stat_ver"] = u_ver + 1
                            st.rerun()
                else:
                    if st.button("✅ Energetikus újraaktiválása", type="primary"):
                        supabase.table("energetikusok").update({"aktiv": True}).eq("id", kiv_u["id"]).execute()
                        st.session_state["user_stat_ver"] = u_ver + 1
                        st.success(f"✅ {kiv_u['nev']} újra aktív!")
                        st.rerun()

            st.divider()
            st.subheader("🔀 Telephelyek szétosztása energetikusok között")
            rendezett_userek = sorted(osszes_user, key=lambda x: (bool(x.get("aktiv")), x["nev"]))
            szuro_opciok = {"Összes felhasználási hely": None}
            for u in rendezett_userek:
                szuro_opciok[f"{u['nev']} [{'Inaktív' if not u.get('aktiv') else 'Aktív'}]"] = u["id"]
            val_szuro = st.selectbox("Kinek a telephelyeit szeretnéd szétosztani?", list(szuro_opciok.keys()))
            szurt_forras_id = szuro_opciok[val_szuro]

            szurt_helyek = osszes_hely if szurt_forras_id is None else list(h for h in osszes_hely if h.get("felelos_energetikus_id") == szurt_forras_id or h.get("allando_helyettes_id") == szurt_forras_id)
            if not szurt_helyek:
                st.info("A kiválasztott szűrőhöz nem tartozik felhasználási hely.")
            else:
                st.caption("💡 Kattints duplán a **Felelős energetikus** vagy az **Állandó helyettes** oszlop celláira a kiosztás módosításához!")
                nev_to_id = {"Nincs kijelölve": None}
                for u in osszes_user:
                    if u.get("aktiv"):
                        nev_to_id[u["nev"]] = u["id"]
                valaszthato_nevek = list(nev_to_id.keys())

                kiosztas_sorok = list()
                for h in szurt_helyek:
                    fel_nev = u_map.get(h.get("felelos_energetikus_id")) or "Nincs kijelölve"
                    helyett_nev = u_map.get(h.get("allando_helyettes_id")) or "Nincs kijelölve"
                    if fel_nev not in valaszthato_nevek:
                        valaszthato_nevek.append(fel_nev)
                    if helyett_nev not in valaszthato_nevek:
                        valaszthato_nevek.append(helyett_nev)
                    kiosztas_sorok.append({"ID": h["id"], "Megnevezés": h["megnevezes"], "Energiatípus": h.get("energiatipus") or "", "POD / EIC": h["pod_eic_azonosito"], "Felelős energetikus": fel_nev, "Állandó helyettes": helyett_nev})

                szerk_kiosztas = st.data_editor(
                    pd.DataFrame(kiosztas_sorok),
                    use_container_width=True,
                    hide_index=True,
                    disabled=list(("ID", "Megnevezés", "Energiatípus", "POD / EIC")),
                    column_config={
                        "Felelős energetikus": st.column_config.SelectboxColumn("Felelős energetikus", options=valaszthato_nevek, required=True),
                        "Állandó helyettes": st.column_config.SelectboxColumn("Állandó helyettes", options=valaszthato_nevek, required=True)
                    },
                    key=f"kiosztas_ed_{szurt_forras_id}"
                )

                if st.button("💾 Telephely-kiosztás mentése", type="primary"):
                    eredeti_map = {h["id"]: h for h in szurt_helyek}
                    modositva = 0
                    for _, sor in szerk_kiosztas.iterrows():
                        hid = int(sor["ID"])
                        uj_fel_id = nev_to_id.get(sor["Felelős energetikus"], eredeti_map[hid].get("felelos_energetikus_id"))
                        uj_helyett_id = nev_to_id.get(sor["Állandó helyettes"], eredeti_map[hid].get("allando_helyettes_id"))
                        if uj_fel_id != eredeti_map[hid].get("felelos_energetikus_id") or uj_helyett_id != eredeti_map[hid].get("allando_helyettes_id"):
                            supabase.table("felhasznalasi_helyek").update({"felelos_energetikus_id": uj_fel_id, "allando_helyettes_id": uj_helyett_id}).eq("id", hid).execute()
                            modositva += 1
                    if modositva > 0:
                        st.success(f"✅ {modositva} db felhasználási hely kiosztása sikeresen mentve!")
                        st.rerun()
                    else:
                        st.info("Nem történt változás a kiosztásban.")

def mutat_kimutatasok():
    st.header("Rögzített számlák és havi kimutatások")
    lista = supabase.table("szamlak").select("*").order("jovahagyas_ideje", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        return
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}

    osszes_honap = sorted(list({tiszta_honap(s.get("elszamolasi_honap")) for s in lista}), key=honap_kulcs, reverse=True)
    osszes_hely = sorted(list({h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen") for s in lista}))

    fs1, fs2 = st.columns(2)
    val_honapok = fs1.multiselect("📅 Szűrés elszámolási hónapra (üresen = összes):", osszes_honap)
    val_helyek = fs2.multiselect("🏭 Szűrés felhasználási helyre (üresen = összes):", osszes_hely)

    szurt_lista = list()
    for s in lista:
        ho_cimke = tiszta_honap(s.get("elszamolasi_honap"))
        hely_cimke = h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen")
        if val_honapok and ho_cimke not in val_honapok:
            continue
        if val_helyek and hely_cimke not in val_helyek:
            continue
        szurt_lista.append(s)

    if not szurt_lista:
        st.warning("A megadott szűrőkkel nincs találat.")
        return

    szamla_idk = list(s["id"] for s in szurt_lista)
    osszes_tetel = supabase.table("szamla_tetelek").select("*").in_("szamla_id", szamla_idk).execute().data or list()
    netto_per_szamla = dict()
    energia_kwh_per_szamla = dict()
    for t in osszes_tetel:
        sid = t["szamla_id"]
        netto_per_szamla[sid] = netto_per_szamla.get(sid, 0.0) + float(t.get("szamitott_netto") or 0.0)
        megn = str(t.get("megnevezes_eredeti") or "").strip().lower()
        me = str(t.get("mertekegyseg") or "").strip().lower()
        if ("energiadíj" in megn or "energia díj" in megn) and me == "kwh":
            energia_kwh_per_szamla[sid] = energia_kwh_per_szamla.get(sid, 0.0) + float(t.get("mennyiseg") or 0.0)

    sorok = list()
    for s in szurt_lista:
        sid = s["id"]
        s_netto = netto_per_szamla.get(sid, 0.0)
        s_kwh = energia_kwh_per_szamla.get(sid, 0.0)
        szumma_egysegar = round(s_netto / s_kwh, 2) if s_kwh > 0 else None
        sorok.append({"Számlaszám": s["szamlaszam"], "Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")), "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""), "Időszak kezdete": s["idoszak_tol"], "Időszak vége": s["idoszak_ig"], "Bruttó összeg (Ft)": int(round(float(s["szamlan_szereplo_brutto"]))), "Szumma nettó egységár (Ft/kWh)": szumma_egysegar, "Státusz": s["ellenorzes_statusz"], "PDF fájl": s["pdf_utvonal"]})
    df = pd.DataFrame(sorok)

    ossz_netto_mind = sum(netto_per_szamla.values())
    ossz_kwh_mind = sum(energia_kwh_per_szamla.values())
    atlag_egysegar_str = f"{ossz_netto_mind / ossz_kwh_mind:,.2f} Ft/kWh".replace(",", " ").replace(".", ",") if ossz_kwh_mind > 0 else "Nincs adat"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Számlák száma", f"{len(df)} db")
    k2.metric("Összesített bruttó költség", ft_szoveg(df["Bruttó összeg (Ft)"].sum()))
    k3.metric("Átl. szumma nettó egységár", atlag_egysegar_str)
    k4.metric("Érintett hónapok", f"{df['Elszámolási hónap'].nunique()} hónap")

    y_tengely = alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ') + ' Ft'", title="Bruttó összeg (Ft)")
    tab_ho, tab_hely, tab_egysegar, tab_tetel = st.tabs(list(("📅 Havi bontású költségek", "🏭 Telephelyek szerinti összesítés", "📈 Szumma nettó egységár", "⚡ Fogyasztás (kWh) és Díjtételek")))
    with tab_ho:
        ho_df = df.groupby(["Elszámolási hónap", "Felhasználási hely"], as_index=False)["Bruttó összeg (Ft)"].sum()
        ho_df["Összeg"] = ho_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        ho_sorrend = sorted(list(ho_df["Elszámolási hónap"].unique()), key=honap_kulcs)
        st.altair_chart(alt.Chart(ho_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")), y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely), color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"), xOffset="Felhasználási hely:N", tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]).properties(height=360), use_container_width=True)
    with tab_hely:
        hely_df = df.groupby("Felhasználási hely", as_index=False)["Bruttó összeg (Ft)"].sum()
        hely_df["Összeg"] = hely_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        st.altair_chart(alt.Chart(hely_df).mark_bar(color="#0068c9").encode(x=alt.X("Felhasználási hely:N", axis=alt.Axis(labelAngle=0, title="Felhasználási hely")), y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely), tooltip=[alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]).properties(height=360), use_container_width=True)
    with tab_egysegar:
        ea_sorok = list({"Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")), "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Nettó": netto_per_szamla.get(s["id"], 0.0), "kWh": energia_kwh_per_szamla.get(s["id"], 0.0)} for s in szurt_lista)
        ea_df = pd.DataFrame(ea_sorok).groupby(["Elszámolási hónap", "Felhasználási hely"], as_index=False)[["Nettó", "kWh"]].sum()
        ea_df = ea_df[ea_df["kWh"] > 0].copy()
        if ea_df.empty:
            st.info("Nincs kWh alapú Energiadíj tétel a kiválasztott számlákban.")
        else:
            ea_df["Szumma nettó egységár (Ft/kWh)"] = (ea_df["Nettó"] / ea_df["kWh"]).round(2)
            ea_sorrend = sorted(list(ea_df["Elszámolási hónap"].unique()), key=honap_kulcs)
            st.altair_chart(alt.Chart(ea_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ea_sorrend, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")), y=alt.Y("Szumma nettó egységár (Ft/kWh):Q", axis=alt.Axis(title="Szumma nettó egységár (Ft/kWh)")), color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"), xOffset="Felhasználási hely:N", tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("Szumma nettó egységár (Ft/kWh):Q", format=",.2f")]).properties(height=360), use_container_width=True)
    with tab_tetel:
        szamla_map = {s["id"]: s for s in szurt_lista}
        if not osszes_tetel:
            st.info("A kiválasztott számlákhoz nem tartozik rögzített díjtétel.")
        else:
            t_sorok = list({"Elszámolási hónap": tiszta_honap(szamla_map.get(t["szamla_id"], {}).get("elszamolasi_honap")), "Felhasználási hely": h_map.get(szamla_map.get(t["szamla_id"], {}).get("felhasznalasi_hely_id"), ""), "Számlaszám": szamla_map.get(t["szamla_id"], {}).get("szamlaszam", ""), "Díjtétel": t["megnevezes_eredeti"], "Mennyiség": float(t["mennyiseg"]), "Mértékegység": t["mertekegyseg"], "Nettó egységár (Ft)": float(t["egysegar_netto"]), "Nettó összeg (Ft)": int(round(float(t["szamitott_netto"]))), "Bruttó összeg (Ft)": int(round(float(t["szamitott_brutto"])))} for t in osszes_tetel)
            t_df = pd.DataFrame(t_sorok)
            val_tetel = st.selectbox("🔍 Díjtétel kiválasztása:", sorted(list(t_df["Díjtétel"].unique())))
            szurt_t_df = t_df[t_df["Díjtétel"] == val_tetel].copy()
            ho_sorrend_t = sorted(list(szurt_t_df["Elszámolási hónap"].unique()), key=honap_kulcs)
            tc1, tc2 = st.columns(2)
            with tc1:
                st.altair_chart(alt.Chart(szurt_t_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0)), y=alt.Y("sum(Mennyiség):Q", axis=alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ')", title="Mennyiség")), color="Felhasználási hely:N", xOffset="Felhasználási hely:N").properties(height=280), use_container_width=True)
            with tc2:
                st.altair_chart(alt.Chart(szurt_t_df).mark_line(point=True).encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0)), y=alt.Y("mean(Nettó egységár (Ft)):Q", axis=alt.Axis(title="Nettó egységár (Ft)")), color="Felhasználási hely:N").properties(height=280), use_container_width=True)
            st.dataframe(t_df, use_container_width=True, hide_index=True)

    kim_ver = st.session_state.get("kimutatas_ver", 0)
    st.caption("💡 Kattints egy sor elejére a táblázatban a csatolt PDF számla megnyitásához!")
    esemeny = st.dataframe(df, use_container_width=True, on_select="rerun", selection_mode="single-row", key=f"kim_df_{kim_ver}")
    if esemeny.selection.rows:
        kiv_idx = next(iter(esemeny.selection.rows))
        kiv_szamla = szurt_lista[kiv_idx]
        if kiv_szamla.get("pdf_utvonal"):
            mutat_pdf_ablak(kiv_szamla["pdf_utvonal"], kiv_szamla["szamlaszam"])
        else:
            st.warning("Ehhez a számlához nem lett PDF fájl feltöltve.")

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Szűrt kimutatás letöltése Excelbe (CSV)", data=csv, file_name=f"energia_riport_{date.today()}.csv", mime="text/csv")

    st.divider()
    st.subheader("🗑️ Számla törlése")
    torles_opciok = {f"#{s['id']} – {s['szamlaszam']} ({tiszta_honap(s.get('elszamolasi_honap'))}, {h_map.get(s['felhasznalasi_hely_id'], '')}, {ft_szoveg(s['szamlan_szereplo_brutto'])})": s for s in szurt_lista}
    valasztott_torles = st.selectbox("Válaszd ki a törlendő számlát:", list(torles_opciok.keys()), index=None, placeholder="Válassz ki egy számlát a törléshez...", key=f"torles_sel_{kim_ver}")
    if valasztott_torles:
        kiv_torles = torles_opciok[valasztott_torles]
        if st.button("🗑️ Kiválasztott számla törlése"):
            st.session_state["torles_megerosites_id"] = kiv_torles["id"]
        if st.session_state.get("torles_megerosites_id") == kiv_torles["id"]:
            st.warning(f"⚠️ Biztos, hogy törölni kívánja a számlát? (**{kiv_torles['szamlaszam']}**)")
            tc1, tc2, _ = st.columns((1.2, 1.2, 4))
            if tc1.button("✅ Igen", type="primary"):
                supabase.table("szamla_tetelek").delete().eq("szamla_id", kiv_torles["id"]).execute()
                if kiv_torles.get("pdf_utvonal"):
                    supabase.storage.from_("szamlak-pdf").remove(list((kiv_torles["pdf_utvonal"],)))
                supabase.table("szamlak").delete().eq("id", kiv_torles["id"]).execute()
                st.session_state.pop("torles_megerosites_id", None)
                st.session_state["kimutatas_ver"] = kim_ver + 1
                st.success(f"✅ A(z) {kiv_torles['szamlaszam']} számú számla sikeresen törölve lett!")
                st.rerun()
            if tc2.button("❌ Nem"):
                st.session_state.pop("torles_megerosites_id", None)
                st.session_state["kimutatas_ver"] = kim_ver + 1
                st.rerun()
def mutat_szamla_rogzites(user_id):
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
        return

    fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=list(("pdf", "xlsx", "csv")), key=f"fajl_{fv}")
    f_id = f"{fv}_{fajl.name}" if fajl else f"{fv}_ures"
    pdf_adat = None
    if fajl and fajl.name.lower().endswith(".pdf"):
        fajl.seek(0)
        pdf_adat = feldolgoz_pdf(fajl)
        if not pdf_adat["szoveg"]:
            st.warning("Szkennelt PDF: a tételeket lent kézzel írd be, a PDF fájlt ugyanúgy elmenti a rendszer.")
        else:
            tetel_db = len(pdf_adat["tetelek_df"]) if pdf_adat.get("tetelek_df") is not None else 0
            st.success(f"PDF számla feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}** ({tetel_db} db díjtétel kinyerve)")

    hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if pdf_adat["pod"] in lbl), 0) if (pdf_adat and pdf_adat["pod"]) else 0

    st.subheader("2. Számla alapadatok")
    c1, c2, c3 = st.columns(3)
    v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx, key=f"hely_{f_id}")
    hely_id = hely_opciok[v_hely]
    szamlaszam = c1.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "", key=f"szsz_{f_id}")

    szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
    szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
    szolg_idx = next((i for i, nev in enumerate(szolg_opciok.keys()) if pdf_adat and pdf_adat.get("szolgaltato_kulcs") and pdf_adat["szolgaltato_kulcs"].lower() in nev.lower()), 0)
    v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()), index=szolg_idx, key=f"szolg_{f_id}")
    szolg_id = szolg_opciok[v_szolg]

    br_kulcs = f"br_{f_id}"
    alap_br_szoveg = f"{int(pdf_adat['brutto']):,}".replace(",", " ") if (pdf_adat and pdf_adat["brutto"] > 0) else ""
    szamlan_brutto_str = c2.text_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=alap_br_szoveg, placeholder="pl. 6 648", key=br_kulcs, on_change=formaz_ezres, args=(br_kulcs,))
    br_szamjegyek = re.sub(r"\D", "", szamlan_brutto_str)
    szamlan_brutto = int(br_szamjegyek) if br_szamjegyek else 0

    kelte = pdf_adat["kelte"] if pdf_adat else date.today()
    hatarido = pdf_adat["hatarido"] if pdf_adat else date.today()
    tol = c3.date_input("Elszámolási időszak kezdete", value=pdf_adat["tol"] if pdf_adat else date.today(), key=f"t_{f_id}")
    ig = c3.date_input("Elszámolási időszak vége", value=pdf_adat["ig"] if pdf_adat else date.today(), key=f"i_{f_id}")
    datum_rendben = tol <= ig
    if not datum_rendben:
        c3.error("⚠️ Az időszak kezdete nem lehet későbbi, mint a vége!")
    elszamolasi_honap = szamol_elszamolasi_honap(ig)
    c3.info(f"📅 Elszámolási hónap: **{elszamolasi_honap}**")

    st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
    alap_df = pd.DataFrame(dict(Megnevezés=["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"], Mennyiség=[0.0, 0.0, 0.0, 0.0], Mértékegység=["kWh", "kWh", "kWh", "hó"], Nettó_egységár=[0.0, 0.0, 0.0, 0.0], ÁFA=[27.0, 27.0, 27.0, 27.0]))
    alap_df.columns = list(("Megnevezés", "Mennyiség", "Mértékegység", "Nettó egységár", "ÁFA (%)"))
    if pdf_adat and pdf_adat.get("tetelek_df") is not None:
        alap_df = pdf_adat["tetelek_df"]
    elif fajl and fajl.name.lower().endswith(".xlsx"):
        alap_df = pd.read_excel(fajl)
    elif fajl and fajl.name.lower().endswith(".csv"):
        alap_df = pd.read_csv(fajl)

    szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True, key=f"ed_{f_id}")
    ossz_netto, ossz_brutto, tetelek = szamol_tetelek(szerk_df)

    brutto_dec = Decimal(szamlan_brutto)
    elteres = abs(brutto_dec - ossz_brutto)

    m1, m2, m3 = st.columns(3)
    m1.metric("Számított Nettó", ft_szoveg(ossz_netto))
    m2.metric("Számított Bruttó", ft_szoveg(ossz_brutto))
    m3.metric("Eltérés a számlától", ft_szoveg(elteres))

    egyezik = (elteres <= Decimal("2")) and (len(tetelek) > 0) and (szamlan_brutto > 0) and datum_rendben
    if egyezik: st.success("✅ A számított bruttó összeg megegyezik a számlán szereplővel!")
    if not egyezik: st.warning("A mentés gomb akkor válik aktívvá, ha az összegek egyeznek és a dátumok helyesek.")

    mentes_fajl = fajl if (fajl and fajl.name.lower().endswith(".pdf")) else None
    gomb = st.button("💾 PDF feltöltése és Számla mentése az adatbázisba", type="primary", disabled=not egyezik)
    forras = "PDF" if pdf_adat else ("TABLAZAT" if fajl else "KEZI")
    stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
    uj_szamla = {"szamlaszam": szamlaszam, "szolgaltato_id": szolg_id, "felhasznalasi_hely_id": hely_id, "forras_tipus": forras, "szamla_kelte": kelte.isoformat(), "fizetesi_hatarido": hatarido.isoformat(), "idoszak_tol": tol.isoformat(), "idoszak_ig": ig.isoformat(), "elszamolasi_honap": elszamolasi_honap, "szamlan_szereplo_brutto": float(brutto_dec), "szamitott_brutto": float(ossz_brutto), "elteres": float(elteres), "ellenorzes_statusz": stat, "jovahagyta_energetikus_id": user_id}
    if gomb and egyezik: mentes_adatbazisba(uj_szamla, tetelek, mentes_fajl)

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok", "⚙️ Törzsadatok és Helyettesítések")), on_change=urit_urlap)
if menu == "🧾 Számla ellenőrzés és feltöltés":
    mutat_szamla_rogzites(int(user_sor["id"]))
elif menu == "📊 Kimutatások és Riportok":
    mutat_kimutatasok()
else:
    mutat_torzsadatok()
