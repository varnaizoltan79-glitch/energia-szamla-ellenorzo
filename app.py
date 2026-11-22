import base64
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import altair as alt
import fitz
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
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
    tiszta = re.sub(r"^-\s+", "-", tiszta)
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
    },
    "MVM": {
        "azonositok": ("mvm next", "26713111", "mvmenergiakereskedo"),
        "szolgaltato_nev_kulcs": "MVM",
    }
}

def _vizszintes_sorok(oldal):
    szavak = oldal.get_text("words")
    if not szavak:
        return list()
    rendezett = sorted(szavak, key=lambda w: (round(w[1] / 3.0), w[0]))
    sorok = list()
    _, akt_y, *_ = next(iter(rendezett))
    akt_szavak = list()
    for w in rendezett:
        x0, y0, x1, _, szo, *_ = w
        if abs(y0 - akt_y) <= 3.5:
            akt_szavak.append((x0, x1, szo))
        else:
            akt_szavak.sort(key=lambda item: item[0])
            sor_reszek = list()
            elozo_x1 = None
            for sx0, sx1, sszo in akt_szavak:
                if elozo_x1 is not None:
                    sor_reszek.append("  " if (sx0 - elozo_x1) > 8.5 else " ")
                sor_reszek.append(sszo)
                elozo_x1 = sx1
            sorok.append("".join(sor_reszek))
            akt_y = y0
            akt_szavak = [(x0, x1, szo)]
    if akt_szavak:
        akt_szavak.sort(key=lambda item: item[0])
        sor_reszek = list()
        elozo_x1 = None
        for sx0, sx1, sszo in akt_szavak:
            if elozo_x1 is not None:
                sor_reszek.append("  " if (sx0 - elozo_x1) > 8.5 else " ")
            sor_reszek.append(sszo)
            elozo_x1 = sx1
        sorok.append("".join(sor_reszek))
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

def _feldolgoz_mvm_pod_blokk(sorok, alap_tol, alap_ig, alap_brutto):
    blokk_szoveg = "\n".join(sorok)
    m_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", blokk_szoveg)
    pod = m_pod.group(1).strip() if m_pod else ""

    pod_tol, pod_ig = alap_tol, alap_ig
    m_ido = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", blokk_szoveg)
    if m_ido:
        pod_tol = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        pod_ig = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()

    akt_afa = 27.0
    akt_me = "kWh"
    meroallas_resz = False
    talalatok = list()
    egyes_szam_minta = r"(?:-\s*)?\d{1,3}(?: \d{3})+(?:,\d+)?|(?:-\s*)?\d+(?:,\d+)?"

    for s in sorok:
        s_low = s.lower()
        if "mérőállás" in s_low or "mérőgyári" in s_low or "induló állás" in s_low:
            meroallas_resz = True
        if "kereskedelmi díjak" in s_low or "közvetített szolgáltatás" in s_low or "pénzeszközök" in s_low or "adóalapon kívüli" in s_low:
            meroallas_resz = False
        if meroallas_resz:
            continue

        if "adóalapon kívüli" in s_low or "pénzeszközök" in s_low:
            akt_afa = 0.0
        elif "kereskedelmi díjak" in s_low or "közvetített szolgáltatás" in s_low or "megnevezés" in s_low:
            if "adóalapon kívüli" not in s_low:
                akt_afa = 27.0

        if "(kvarh)" in s_low:
            akt_me = "kVArh"
        elif "(kw)" in s_low:
            akt_me = "kW"
        elif "csatl.pont/db" in s_low or "(db)" in s_low:
            akt_me = "db"
        elif "(kwh)" in s_low:
            akt_me = "kWh"

        s_tagolt = re.sub(r"(\d{4}\.\d{2}\.\d{2}-\d{4}\.\d{2}\.\d{2})(\d)", r"\1  \2", s)
        m_tetel = re.search(r"^(.+?)\s+(\d{4}\.\d{2}\.\d{2}-\d{4}\.\d{2}\.\d{2})\s+(.+)$", s_tagolt)
        if not m_tetel:
            continue

        nev = m_tetel.group(1).strip()
        maradek = m_tetel.group(3).strip()
        szamok_raw = re.findall(egyes_szam_minta, maradek)
        szamok = list(x for x in (_hu_szam(r) for r in szamok_raw) if x is not None)
        if len(szamok) < 3:
            continue

        elso_szam, masodik_szam, *_ = szamok
        netto_ellenorzes = next(reversed(szamok))
        if alap_brutto > 0 and abs(netto_ellenorzes) > alap_brutto * 1.5:
            continue

        if "közbeszerzési" in nev.lower():
            if abs((elso_szam * masodik_szam / 100.0) - netto_ellenorzes) <= 2.5:
                talalatok.append({"Megnevezés": nev, "Mennyiség": 1.0, "Mértékegység": "db", "Nettó egységár": netto_ellenorzes, "ÁFA (%)": 27.0})
        else:
            if abs((elso_szam * masodik_szam) - netto_ellenorzes) <= 2.5:
                talalatok.append({"Megnevezés": nev, "Mennyiség": elso_szam, "Mértékegység": akt_me, "Nettó egységár": masodik_szam, "ÁFA (%)": akt_afa})

    tetelek_df = pd.DataFrame(talalatok) if talalatok else None
    pod_brutto = 0
    afa_bontasban = False
    for s in sorok:
        if "áfa szerinti bontás" in s.lower():
            afa_bontasban = True
        elif afa_bontasban and s.lower().startswith("összesen"):
            szamok_raw = re.findall(egyes_szam_minta, s)
            szamok = list(x for x in (_hu_szam(r) for r in szamok_raw) if x is not None)
            if szamok:
                pod_brutto = int(round(next(reversed(szamok))))
            break

    if pod_brutto == 0 and tetelek_df is not None:
        _, szam_brutto, _ = szamol_tetelek(tetelek_df)
        pod_brutto = int(round(float(szam_brutto)))
    elif pod_brutto == 0:
        pod_brutto = alap_brutto

    return pod, {"pod": pod, "brutto": pod_brutto, "tol": pod_tol, "ig": pod_ig, "tetelek_df": tetelek_df}

def _feldolgoz_mvm_pdf(dok, szoveg, adat):
    adat["szolgaltato_kulcs"] = "MVM"
    m_sz = re.search(r"Számla\s*száma\s*:?\s*([A-Z0-9/-]{6,})", szoveg)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_ido = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg)
    if m_ido:
        adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()

    m_br = re.search(r"Fizetendő\s*:?\s*([\d\s\xa0]+)\s*Ft", szoveg)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))
            adat["foszamla_brutto"] = int(round(br_val))

    osszes_sor = list()
    for oldal in dok:
        osszes_sor.extend(_vizszintes_sorok(oldal))

    kezdo_indexek = list(i for i, s in enumerate(osszes_sor) if "TELEPHELYI RÉSZLETEZŐ" in s.upper())
    if not kezdo_indexek:
        kezdo_indexek = list((0,))

    osszevont_blokkok = list()
    for idx, start_i in enumerate(kezdo_indexek):
        end_i = kezdo_indexek[idx + 1] if idx + 1 < len(kezdo_indexek) else len(osszes_sor)
        blokk = osszes_sor[start_i:end_i]
        van_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", "\n".join(blokk))
        if van_pod or not osszevont_blokkok:
            osszevont_blokkok.append(list(blokk))
        else:
            utolso_blokk = next(reversed(osszevont_blokkok))
            utolso_blokk.extend(blokk)

    pod_adatok = dict()
    for blokk in osszevont_blokkok:
        pod_nev, pod_info = _feldolgoz_mvm_pod_blokk(blokk, adat["tol"], adat["ig"], adat["brutto"])
        if pod_nev:
            pod_adatok[pod_nev] = pod_info

    if pod_adatok:
        adat["pod_adatok"] = pod_adatok
        elso_pod = next(iter(pod_adatok.keys()))
        adat["pod"] = elso_pod
        adat["brutto"] = pod_adatok[elso_pod]["brutto"]
        adat["tol"] = pod_adatok[elso_pod]["tol"]
        adat["ig"] = pod_adatok[elso_pod]["ig"]
        adat["tetelek_df"] = pod_adatok[elso_pod]["tetelek_df"]
    return adat




def feldolgoz_pdf(fajl):
    pdf_bytes = fajl.read()
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    szoveg = "\n".join(list(oldal.get_text("text") or "" for oldal in dok))
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0, "pod": "", "szolgaltato_kulcs": "", "tetelek_df": None, "pod_adatok": {}}

    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk: adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh: adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()

    szoveg_low = szoveg.lower()
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["ALTEO"]["azonositok"]):
        return _feldolgoz_alteo_pdf(dok, szoveg, adat)
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["MVM"]["azonositok"]):
        return _feldolgoz_mvm_pdf(dok, szoveg, adat)

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

def mentes_adatbazisba(uj_szamla, tetelek, fajl, gyujto_marad=False):
    try:
        alap_szamlaszam = uj_szamla["szamlaszam"]
        try:
            res = supabase.table("szamlak").insert(uj_szamla).execute()
        except Exception as ins_hiba:
            if "duplicate" in str(ins_hiba).lower() or "23505" in str(ins_hiba):
                uj_szamla["szamlaszam"] = f"{alap_szamlaszam} (Hely #{uj_szamla['felhasznalasi_hely_id']})"
                try:
                    res = supabase.table("szamlak").insert(uj_szamla).execute()
                except Exception:
                    st.warning("⚠️ Ez a mérési pont (POD) már rögzítve van ehhez a számlaszámhoz! Válaszd a következő POD-ot a legördülő listából, vagy töröld a korábbit a Kimutatások menüben.")
                    return
            else:
                raise ins_hiba

        sid = next(iter(res.data))["id"]
        for t in tetelek:
            t["szamla_id"] = sid
        supabase.table("szamla_tetelek").insert(tetelek).execute()
        if fajl is not None:
            fajl.seek(0)
            tiszta_szam = re.sub(r"[^A-Za-z0-9_-]", "_", alap_szamlaszam)
            fnev = f"{date.today().year}_{tiszta_szam}.pdf"
            supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
            supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
        st.session_state["utolso_mentes"] = f"✅ A POD számlarészlet rögzítése megtörtént (Számlaszám: {uj_szamla['szamlaszam']}, Elszámolási hónap: {uj_szamla['elszamolasi_honap']}, ID: #{sid})"
        if not gyujto_marad:
            urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")


def mutat_helyettesitesek():
    st.header("🔄 Helyettesítések kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    h_map = {h["id"]: h["megnevezes"] for h in osszes_hely}

    helyett_lista = supabase.table("helyettesitesek").select("*").order("kezdet_datum", desc=True).execute().data or list()
    if helyett_lista:
        h_sorok = list({"ID": x["id"], "Helyettesített": u_map.get(x["helyettesitett_energetikus_id"], ""), "Helyettesítő": u_map.get(x["helyettesito_energetikus_id"], ""), "Felhasználási hely": h_map.get(x["felhasznalasi_hely_id"], "Minden telephelye"), "Kezdet": x["kezdet_datum"], "Vége": x["vege_datum"]} for x in helyett_lista)
        st.dataframe(pd.DataFrame(h_sorok), use_container_width=True, hide_index=True)
        torol_h = st.selectbox("Helyettesítés törlése:", list(f"#{x['id']} – {u_map.get(x['helyettesitett_energetikus_id'], '')} -> {u_map.get(x['helyettesito_energetikus_id'], '')}" for x in helyett_lista))
        if st.button("🗑️ Kiválasztott helyettesítés törlése"):
            hid = int(next(iter(torol_h.split(" – "))).replace("#", ""))
            supabase.table("helyettesitesek").delete().eq("id", hid).execute()
            st.rerun()

    st.subheader("➕ Új helyettesítés rögzítése")
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

def mutat_torzsadatok():
    st.header("⚙️ Törzsadatok kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    osszes_szolg = supabase.table("szolgaltatok").select("*").order("nev").execute().data or list()
    energia_opciok = list(("Villamos energia", "Földgáz", "Távhő", "Víziközmű", "PB-gáz"))

    t_hely, t_user, t_szolg = st.tabs(list(("🏭 Felhasználási helyek", "👤 Energetikusok", "🏢 Szolgáltatók")))

    with t_hely:
        if osszes_hely:
            st.caption("💡 Kattints duplán a cellákra a **Megnevezés**, **Energiatípus**, **POD / EIC**, **Felelős**, **Állandó helyettes** vagy **Aktív** mezők módosításához!")
            nev_to_id = {"Nincs kijelölve": None}
            for u in osszes_user:
                nev_to_id[u["nev"]] = u["id"]
            user_nevek = list(nev_to_id.keys())

            h_sorok = list()
            for h in osszes_hely:
                fel_nev = u_map.get(h.get("felelos_energetikus_id")) or "Nincs kijelölve"
                helyett_nev = u_map.get(h.get("allando_helyettes_id")) or "Nincs kijelölve"
                e_tipus = h.get("energiatipus") if h.get("energiatipus") in energia_opciok else "Villamos energia"
                h_sorok.append({"ID": h["id"], "Megnevezés": h["megnevezes"], "Energiatípus": e_tipus, "POD / EIC": h["pod_eic_azonosito"], "Felelős": fel_nev, "Állandó helyettes": helyett_nev, "Aktív": bool(h.get("aktiv", True))})

            szerk_hely_df = st.data_editor(
                pd.DataFrame(h_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Energiatípus": st.column_config.SelectboxColumn("Energiatípus", options=energia_opciok, required=True),
                    "Felelős": st.column_config.SelectboxColumn("Felelős", options=user_nevek, required=True),
                    "Állandó helyettes": st.column_config.SelectboxColumn("Állandó helyettes", options=user_nevek, required=True),
                    "Aktív": st.column_config.CheckboxColumn("Aktív")
                },
                key="torzs_hely_editor"
            )

            if st.button("💾 Felhasználási helyek módosításainak mentése", type="primary"):
                eredeti_h = {h["id"]: h for h in osszes_hely}
                modositva = 0
                for _, sor in szerk_hely_df.iterrows():
                    hid = int(sor["ID"])
                    regi = eredeti_h[hid]
                    uj_adat = {"megnevezes": str(sor["Megnevezés"]).strip(), "energiatipus": sor["Energiatípus"], "pod_eic_azonosito": str(sor["POD / EIC"]).strip(), "felelos_energetikus_id": nev_to_id.get(sor["Felelős"]), "allando_helyettes_id": nev_to_id.get(sor["Állandó helyettes"]), "aktiv": bool(sor["Aktív"])}
                    if uj_adat["megnevezes"] != regi["megnevezes"] or uj_adat["energiatipus"] != (regi.get("energiatipus") or "Villamos energia") or uj_adat["pod_eic_azonosito"] != regi["pod_eic_azonosito"] or uj_adat["felelos_energetikus_id"] != regi.get("felelos_energetikus_id") or uj_adat["allando_helyettes_id"] != regi.get("allando_helyettes_id") or uj_adat["aktiv"] != bool(regi.get("aktiv", True)):
                        supabase.table("felhasznalasi_helyek").update(uj_adat).eq("id", hid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db felhasználási hely módosítása mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új felhasználási hely hozzáadása"):
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
                if st.form_submit_button("💾 Új felhasználási hely mentése", type="primary") and uj_h_nev.strip() and uj_h_pod.strip():
                    try:
                        supabase.table("felhasznalasi_helyek").insert({"megnevezes": uj_h_nev.strip(), "energiatipus": uj_h_energia, "pod_eic_azonosito": uj_h_pod.strip(), "felelos_energetikus_id": fel_opc[uj_h_fel], "allando_helyettes_id": fel_opc[uj_h_helyett], "aktiv": True}).execute()
                        st.rerun()
                    except Exception as hiba:
                        st.error(f"Adatbázis hiba: {hiba}")

        if osszes_hely:
            st.divider()
            st.subheader("🗑️ Felhasználási hely törlése")
            h_ver = st.session_state.get("hely_torles_ver", 0)
            h_torol_opc = {f"#{h['id']} – {h['megnevezes']} ({h['pod_eic_azonosito']})": h for h in osszes_hely}
            val_h_torol = st.selectbox("Válaszd ki a törlendő felhasználási helyet:", list(h_torol_opc.keys()), index=None, placeholder="Válassz ki egy telephelyet...", key=f"h_torol_sel_{h_ver}")
            if val_h_torol:
                kiv_h = h_torol_opc[val_h_torol]
                if st.button("🗑️ Kiválasztott felhasználási hely törlése"):
                    st.session_state["h_torles_megerosites_id"] = kiv_h["id"]
                if st.session_state.get("h_torles_megerosites_id") == kiv_h["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja a felhasználási helyet? (**{kiv_h['megnevezes']}**)")
                    hc1, hc2, _ = st.columns((1.2, 1.2, 4))
                    if hc1.button("✅ Igen", type="primary", key=f"h_igen_{kiv_h['id']}"):
                        try:
                            supabase.table("felhasznalasi_helyek").delete().eq("id", kiv_h["id"]).execute()
                            st.session_state.pop("h_torles_megerosites_id", None)
                            st.session_state["hely_torles_ver"] = h_ver + 1
                            st.success(f"✅ {kiv_h['megnevezes']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez a telephely nem törölhető véglegesen, mert már rögzített számla tartozik hozzá.")
                            if kiv_h.get("aktiv"):
                                if st.button("🚫 Inaktiválás törlés helyett", key=f"h_inakt_{kiv_h['id']}"):
                                    supabase.table("felhasznalasi_helyek").update({"aktiv": False}).eq("id", kiv_h["id"]).execute()
                                    st.session_state.pop("h_torles_megerosites_id", None)
                                    st.session_state["hely_torles_ver"] = h_ver + 1
                                    st.rerun()
                    if hc2.button("❌ Nem", key=f"h_nem_{kiv_h['id']}"):
                        st.session_state.pop("h_torles_megerosites_id", None)
                        st.session_state["hely_torles_ver"] = h_ver + 1
                        st.rerun()

    with t_user:
        szerep_opciok = list(("Energetikus", "Vezető energetikus", "Adminisztrátor"))
        if osszes_user:
            st.caption("💡 Kattints duplán a **Név**, **Szerepkör** vagy **Aktív** cellákra a módosításhoz!")
            u_sorok = list({"ID": u["id"], "Név": u["nev"], "Szerepkör": u["szerepkor"] if u.get("szerepkor") in szerep_opciok else "Energetikus", "Aktív": bool(u.get("aktiv", True))} for u in osszes_user)
            szerk_u_df = st.data_editor(
                pd.DataFrame(u_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Szerepkör": st.column_config.SelectboxColumn("Szerepkör", options=szerep_opciok, required=True),
                    "Aktív": st.column_config.CheckboxColumn("Aktív")
                },
                key="torzs_user_editor"
            )
            if st.button("💾 Energetikusok módosításainak mentése", type="primary"):
                eredeti_u = {u["id"]: u for u in osszes_user}
                modositva = 0
                for _, sor in szerk_u_df.iterrows():
                    uid = int(sor["ID"])
                    regi = eredeti_u[uid]
                    uj_nev = str(sor["Név"]).strip()
                    uj_szerep = sor["Szerepkör"]
                    uj_aktiv = bool(sor["Aktív"])
                    if uj_nev and (uj_nev != regi["nev"] or uj_szerep != regi.get("szerepkor") or uj_aktiv != bool(regi.get("aktiv", True))):
                        supabase.table("energetikusok").update({"nev": uj_nev, "szerepkor": uj_szerep, "aktiv": uj_aktiv}).eq("id", uid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db energetikus adatai mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új energetikus hozzáadása"):
            with st.form("uj_user_form"):
                uc1, uc2 = st.columns(2)
                uj_u_nev = uc1.text_input("Új energetikus teljes neve:")
                uj_u_szerep = uc2.selectbox("Szerepkör:", szerep_opciok)
                if st.form_submit_button("💾 Energetikus mentése", type="primary") and uj_u_nev.strip():
                    uj_adat = {"nev": uj_u_nev.strip(), "szerepkor": uj_u_szerep, "aktiv": True}
                    if osszes_user and "email" in next(iter(osszes_user)):
                        uj_adat["email"] = f"{re.sub(r'[^a-z0-9]', '', uj_u_nev.lower())}@ceg.hu"
                    supabase.table("energetikusok").insert(uj_adat).execute()
                    st.rerun()

        if osszes_user:
            st.divider()
            st.subheader("🗑️ Energetikus törlése")
            u_ver = st.session_state.get("user_torles_ver", 0)
            u_torol_opc = {f"#{u['id']} – {u['nev']} ({u['szerepkor']})": u for u in osszes_user}
            val_u_torol = st.selectbox("Válaszd ki a törlendő energetikust:", list(u_torol_opc.keys()), index=None, placeholder="Válassz ki egy energetikust...", key=f"u_torol_sel_{u_ver}")
            if val_u_torol:
                kiv_u = u_torol_opc[val_u_torol]
                if st.button("🗑️ Kiválasztott energetikus törlése"):
                    st.session_state["u_torles_megerosites_id"] = kiv_u["id"]
                if st.session_state.get("u_torles_megerosites_id") == kiv_u["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja az energetikust? (**{kiv_u['nev']}**)")
                    uc_igen, uc_nem, _ = st.columns((1.2, 1.2, 4))
                    if uc_igen.button("✅ Igen", type="primary", key=f"u_igen_{kiv_u['id']}"):
                        try:
                            supabase.table("energetikusok").delete().eq("id", kiv_u["id"]).execute()
                            st.session_state.pop("u_torles_megerosites_id", None)
                            st.session_state["user_torles_ver"] = u_ver + 1
                            st.success(f"✅ {kiv_u['nev']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez az energetikus nem törölhető véglegesen, mert számlához, telephelyhez vagy helyettesítéshez van kapcsolva.")
                            if kiv_u.get("aktiv"):
                                if st.button("🚫 Inaktiválás törlés helyett", key=f"u_inakt_{kiv_u['id']}"):
                                    supabase.table("energetikusok").update({"aktiv": False}).eq("id", kiv_u["id"]).execute()
                                    st.session_state.pop("u_torles_megerosites_id", None)
                                    st.session_state["user_torles_ver"] = u_ver + 1
                                    st.rerun()
                    if uc_nem.button("❌ Nem", key=f"u_nem_{kiv_u['id']}"):
                        st.session_state.pop("u_torles_megerosites_id", None)
                        st.session_state["user_torles_ver"] = u_ver + 1
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
                nev_to_id_akt = {"Nincs kijelölve": None}
                for u in osszes_user:
                    if u.get("aktiv"):
                        nev_to_id_akt[u["nev"]] = u["id"]
                valaszthato_nevek = list(nev_to_id_akt.keys())

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
                        uj_fel_id = nev_to_id_akt.get(sor["Felelős energetikus"], eredeti_map[hid].get("felelos_energetikus_id"))
                        uj_helyett_id = nev_to_id_akt.get(sor["Állandó helyettes"], eredeti_map[hid].get("allando_helyettes_id"))
                        if uj_fel_id != eredeti_map[hid].get("felelos_energetikus_id") or uj_helyett_id != eredeti_map[hid].get("allando_helyettes_id"):
                            supabase.table("felhasznalasi_helyek").update({"felelos_energetikus_id": uj_fel_id, "allando_helyettes_id": uj_helyett_id}).eq("id", hid).execute()
                            modositva += 1
                    if modositva > 0:
                        st.success(f"✅ {modositva} db felhasználási hely kiosztása sikeresen mentve!")
                        st.rerun()
                    else:
                        st.info("Nem történt változás a kiosztásban.")

    with t_szolg:
        if osszes_szolg:
            st.caption("💡 Kattints duplán a szolgáltató **Név** vagy **Energiatípus** cellájára a módosításhoz!")
            sz_sorok = list({"ID": s["id"], "Név": s["nev"], "Energiatípus": s.get("energiatipus") if s.get("energiatipus") in energia_opciok else "Villamos energia"} for s in osszes_szolg)
            szerk_sz_df = st.data_editor(
                pd.DataFrame(sz_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Energiatípus": st.column_config.SelectboxColumn("Energiatípus", options=energia_opciok, required=True)
                },
                key="torzs_szolg_editor"
            )
            if st.button("💾 Szolgáltatók módosításainak mentése", type="primary"):
                eredeti_sz = {s["id"]: s for s in osszes_szolg}
                modositva = 0
                for _, sor in szerk_sz_df.iterrows():
                    sid = int(sor["ID"])
                    regi = eredeti_sz[sid]
                    uj_nev = str(sor["Név"]).strip()
                    uj_et = sor["Energiatípus"]
                    if uj_nev and (uj_nev != regi["nev"] or uj_et != (regi.get("energiatipus") or "Villamos energia")):
                        supabase.table("szolgaltatok").update({"nev": uj_nev, "energiatipus": uj_et}).eq("id", sid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db szolgáltató módosítása mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új szolgáltató hozzáadása"):
            with st.form("uj_szolg_form"):
                sc1, sc2 = st.columns(2)
                uj_sz_nev = sc1.text_input("Szolgáltató neve:")
                uj_sz_et = sc2.selectbox("Energiatípus:", energia_opciok)
                if st.form_submit_button("💾 Új szolgáltató mentése", type="primary") and uj_sz_nev.strip():
                    try:
                        supabase.table("szolgaltatok").insert({"nev": uj_sz_nev.strip(), "energiatipus": uj_sz_et}).execute()
                        st.rerun()
                    except Exception as hiba:
                        st.error(f"Adatbázis hiba: {hiba}")

        if osszes_szolg:
            st.divider()
            st.subheader("🗑️ Szolgáltató törlése")
            sz_ver = st.session_state.get("szolg_torles_ver", 0)
            sz_torol_opc = {f"#{s['id']} – {s['nev']} ({s.get('energiatipus') or ''})": s for s in osszes_szolg}
            val_sz_torol = st.selectbox("Válaszd ki a törlendő szolgáltatót:", list(sz_torol_opc.keys()), index=None, placeholder="Válassz ki egy szolgáltatót...", key=f"sz_torol_sel_{sz_ver}")
            if val_sz_torol:
                kiv_sz = sz_torol_opc[val_sz_torol]
                if st.button("🗑️ Kiválasztott szolgáltató törlése"):
                    st.session_state["sz_torles_megerosites_id"] = kiv_sz["id"]
                if st.session_state.get("sz_torles_megerosites_id") == kiv_sz["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja a szolgáltatót? (**{kiv_sz['nev']}**)")
                    szc1, szc2, _ = st.columns((1.2, 1.2, 4))
                    if szc1.button("✅ Igen", type="primary", key=f"sz_igen_{kiv_sz['id']}"):
                        try:
                            supabase.table("szolgaltatok").delete().eq("id", kiv_sz["id"]).execute()
                            st.session_state.pop("sz_torles_megerosites_id", None)
                            st.session_state["szolg_torles_ver"] = sz_ver + 1
                            st.success(f"✅ {kiv_sz['nev']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez a szolgáltató nem törölhető, mert már rögzített számla tartozik hozzá.")
                    if szc2.button("❌ Nem", key=f"sz_nem_{kiv_sz['id']}"):
                        st.session_state.pop("sz_torles_megerosites_id", None)
                        st.session_state["szolg_torles_ver"] = sz_ver + 1
                        st.rerun()

def _betolt_szurt_szamlak(kulcs_prefix):
    lista = supabase.table("szamlak").select("*").order("id", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        return None, None, None, None, None, None, None

    legutobbi_id = max(int(s["id"]) for s in lista)
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}

    osszes_honap = sorted(list({tiszta_honap(s.get("elszamolasi_honap")) for s in lista}), key=honap_kulcs, reverse=True)
    osszes_hely = sorted(list({h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen") for s in lista}))

    fs1, fs2 = st.columns(2)
    val_honapok = fs1.multiselect("📅 Szűrés elszámolási hónapra (üresen = összes):", osszes_honap, key=f"{kulcs_prefix}_ho")
    val_helyek = fs2.multiselect("🏭 Szűrés felhasználási helyre (üresen = összes):", osszes_hely, key=f"{kulcs_prefix}_hely")

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
        return None, None, None, None, None, None, None

    szurt_lista.sort(key=lambda s: (honap_kulcs(tiszta_honap(s.get("elszamolasi_honap"))), int(s["id"])), reverse=True)

    szamla_idk = list(s["id"] for s in szurt_lista)
    osszes_tetel = supabase.table("szamla_tetelek").select("*").in_("szamla_id", szamla_idk).execute().data or list()
    netto_per_szamla = dict()
    energia_kwh_per_szamla = dict()
    for t in osszes_tetel:
        sid = t["szamla_id"]
        netto_per_szamla[sid] = netto_per_szamla.get(sid, 0.0) + float(t.get("szamitott_netto") or 0.0)
        megn = str(t.get("megnevezes_eredeti") or "").strip().lower()
        me = str(t.get("mertekegyseg") or "").strip().lower()
        if ("energiadíj" in megn or "energia díj" in megn or "áramdíj" in megn) and me == "kwh":
            energia_kwh_per_szamla[sid] = energia_kwh_per_szamla.get(sid, 0.0) + float(t.get("mennyiseg") or 0.0)

    sorok = list()
    for s in szurt_lista:
        sid = s["id"]
        s_netto = netto_per_szamla.get(sid, 0.0)
        s_kwh = energia_kwh_per_szamla.get(sid, 0.0)
        szumma_egysegar = round(s_netto / s_kwh, 2) if s_kwh > 0 else None
        sorok.append({
            "Számlaszám": s["szamlaszam"],
            "Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")),
            "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""),
            "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""),
            "Időszak kezdete": s["idoszak_tol"],
            "Időszak vége": s["idoszak_ig"],
            "Bruttó összeg (Ft)": int(round(float(s["szamlan_szereplo_brutto"]))),
            "Szumma nettó egységár (Ft/kWh)": szumma_egysegar,
            "Státusz": s["ellenorzes_statusz"],
            "PDF fájl": s["pdf_utvonal"]
        })
    return szurt_lista, pd.DataFrame(sorok), osszes_tetel, netto_per_szamla, energia_kwh_per_szamla, h_map, legutobbi_id

def mutat_rogzitett_szamlak():
    st.header("📑 Rögzített számlák")
    szurt_lista, df, _, _, _, h_map, legutobbi_id = _betolt_szurt_szamlak("rogz")
    if szurt_lista is None:
        return

    def _szinez_utolso(sor):
        if int(szurt_lista[sor.name]["id"]) == legutobbi_id:
            return ["background-color: #d4edda; color: #155724; font-weight: bold"] * len(sor)
        return [""] * len(sor)

    st.yl_df = df.style.apply(_szinez_utolso, axis=1)

    rogz_ver = st.session_state.get("rogzitett_ver", 0)
    st.caption("💡 Az utoljára rögzített számla sora zölddel van kiemelve. Kattints egy sor elejére a csatolt PDF megnyitásához!")
    esemeny = st.dataframe(st.yl_df, use_container_width=True, on_select="rerun", selection_mode="single-row", key=f"rogz_df_{rogz_ver}")
    if esemeny.selection.rows:
        kiv_idx = next(iter(esemeny.selection.rows))
        kiv_szamla = szurt_lista[kiv_idx]
        if kiv_szamla.get("pdf_utvonal"):
            mutat_pdf_ablak(kiv_szamla["pdf_utvonal"], kiv_szamla["szamlaszam"])
        else:
            st.warning("Ehhez a számlához nem lett PDF fájl feltöltve.")

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Rögzített számlák letöltése Excelbe (CSV)", data=csv, file_name=f"rogzitett_szamlak_{date.today()}.csv", mime="text/csv")

    st.divider()
    st.subheader("🗑️ Számlák törlése")
    st.caption("☑️ Pipáld be a törölni kívánt számlák előtti jelölőnégyzetet!")

    torles_sorok = list({
        "Törlés": False,
        "ID": int(s["id"]),
        "Számlaszám": s["szamlaszam"],
        "Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")),
        "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""),
        "Bruttó összeg (Ft)": int(round(float(s["szamlan_szereplo_brutto"])))
    } for s in szurt_lista)

    szerk_torles = st.data_editor(
        pd.DataFrame(torles_sorok),
        use_container_width=True,
        hide_index=True,
        disabled=list(("ID", "Számlaszám", "Elszámolási hónap", "Felhasználási hely", "Bruttó összeg (Ft)")),
        column_config={
            "Törlés": st.column_config.CheckboxColumn("Törlés", default=False)
        },
        key=f"torles_chk_ed_{rogz_ver}"
    )

    kiv_torles_df = szerk_torles[szerk_torles["Törlés"] == True]
    if not kiv_torles_df.empty:
        kiv_idk = list(int(x) for x in kiv_torles_df["ID"])
        kiv_lista = list(s for s in szurt_lista if int(s["id"]) in set(kiv_idk))
        if st.button(f"🗑️ Kijelölt számlák törlése ({len(kiv_lista)} db)"):
            st.session_state["torles_megerosites_idk"] = kiv_idk
        if st.session_state.get("torles_megerosites_idk") == kiv_idk:
            szamok_szoveg = ", ".join(s["szamlaszam"] for s in kiv_lista)
            st.warning(f"⚠️ Biztos, hogy véglegesen törölni kívánja a kijelölt **{len(kiv_lista)} db** számlát? (**{szamok_szoveg}**)")
            tc1, tc2, _ = st.columns((1.2, 1.2, 4))
            if tc1.button("✅ Igen", type="primary"):
                supabase.table("szamla_tetelek").delete().in_("szamla_id", kiv_idk).execute()
                supabase.table("szamlak").delete().in_("id", kiv_idk).execute()
                erintett_pdfek = set(s["pdf_utvonal"] for s in kiv_lista if s.get("pdf_utvonal"))
                for pdf_ut in erintett_pdfek:
                    maradt_e = supabase.table("szamlak").select("id").eq("pdf_utvonal", pdf_ut).execute().data or list()
                    if not maradt_e:
                        supabase.storage.from_("szamlak-pdf").remove(list((pdf_ut,)))
                st.session_state.pop("torles_megerosites_idk", None)
                st.session_state["rogzitett_ver"] = rogz_ver + 1
                st.success(f"✅ {len(kiv_lista)} db számla sikeresen törölve lett!")
                st.rerun()
            if tc2.button("❌ Nem"):
                st.session_state.pop("torles_megerosites_idk", None)
                st.session_state["rogzitett_ver"] = rogz_ver + 1
                st.rerun()


def mutat_kimutatasok():
    st.header("📊 Kimutatások és Riportok")
    szurt_lista, df, osszes_tetel, netto_per_szamla, energia_kwh_per_szamla, h_map, _ = _betolt_szurt_szamlak("kim")
    if szurt_lista is None:
        return

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

def mutat_szamla_rogzites(user_id):
    fv = st.session_state["form_ver"]
    col_fej, col_gomb = st.columns((4, 1))
    col_fej.header("Energiaszámla ellenőrzése és rögzítése")
    if col_gomb.button("🗑️ Új számla / Ürítés"):
        urit_urlap()
        st.rerun()

    if "utolso_mentes" in st.session_state:
        st.success(st.session_state.pop("utolso_mentes"))
        components.html(
            """
            <script>
                const doc = window.parent.document;
                const targets = [
                    doc.querySelector('[data-testid="stMain"]'),
                    doc.querySelector('[data-testid="stAppViewContainer"]'),
                    doc.querySelector('section.main'),
                    doc.documentElement,
                    doc.body
                ];
                targets.forEach(el => { if (el) el.scrollTop = 0; });
                window.parent.scrollTo(0, 0);
            </script>
            """,
            height=0
        )



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
            pod_db = len(pdf_adat.get("pod_adatok") or {})
            if pod_db > 1:
                st.success(f"Gyűjtőszámla feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}** ({pod_db} db mérési pont / POD található benne)")
            else:
                tetel_db = len(pdf_adat["tetelek_df"]) if pdf_adat.get("tetelek_df") is not None else 0
                st.success(f"PDF számla feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}** ({tetel_db} db díjtétel kinyerve)")

    pod_adatok = (pdf_adat.get("pod_adatok") if pdf_adat else None) or {}
    gyujto_mod = len(pod_adatok) > 1
    kiv_pod = pdf_adat["pod"] if pdf_adat else ""
    kiv_pod_info = None

    if pod_adatok:
        foszamla_br = pdf_adat.get("foszamla_brutto", 0) if pdf_adat else 0
        podok_osszesen = sum(p["brutto"] for p in pod_adatok.values())
        if len(pod_adatok) > 1 and foszamla_br > 0:
            gy_elteres = abs(foszamla_br - podok_osszesen)
            g1, g2, g3 = st.columns(3)
            g1.metric("Gyűjtőszámla főösszege", ft_szoveg(foszamla_br))
            g2.metric("POD-ok összesített bruttója", ft_szoveg(podok_osszesen))
            g3.metric("Gyűjtőszámla eltérés", ft_szoveg(gy_elteres))

        hely_pod_nev = {h["pod"]: h["nev"] for h in helyek}
        meglevo_hely_idk = set()
        if pdf_adat and pdf_adat.get("szamlaszam"):
            meglevo = supabase.table("szamlak").select("felhasznalasi_hely_id").ilike("szamlaszam", f"{pdf_adat['szamlaszam']}%").execute().data or list()
            meglevo_hely_idk = set(x["felhasznalasi_hely_id"] for x in meglevo)
        hely_pod_to_id = {h["pod"]: int(h["id"]) for h in helyek}

        pod_cimkek = dict()
        for p_kod, p_info in pod_adatok.items():
            h_nev = hely_pod_nev.get(p_kod, "Nincs a saját telephelyek között")
            hid_check = hely_pod_to_id.get(p_kod)
            stat_jel = " ✅ [Már rögzítve]" if (hid_check and hid_check in meglevo_hely_idk) else ""
            pod_cimkek[f"{p_kod} — {h_nev} ({ft_szoveg(p_info['brutto'])}){stat_jel}"] = p_kod

        val_pod_cimke = st.selectbox("📍 Mérési pont (POD) kiválasztása a számlából:", list(pod_cimkek.keys()), key=f"pod_sel_{f_id}")
        kiv_pod = pod_cimkek[val_pod_cimke]
        kiv_pod_info = pod_adatok[kiv_pod]

    form_kulcs = f"{f_id}_{kiv_pod}" if kiv_pod else f_id

    hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if kiv_pod and kiv_pod in lbl), 0)

    st.subheader("2. Számla alapadatok")
    c1, c2, c3 = st.columns(3)
    v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx, key=f"hely_{form_kulcs}")
    hely_id = hely_opciok[v_hely]
    szamlaszam = c1.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "", key=f"szsz_{form_kulcs}")

    szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
    szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
    szolg_idx = next((i for i, nev in enumerate(szolg_opciok.keys()) if pdf_adat and pdf_adat.get("szolgaltato_kulcs") and pdf_adat["szolgaltato_kulcs"].lower() in nev.lower()), 0)
    v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()), index=szolg_idx, key=f"szolg_{form_kulcs}")
    szolg_id = szolg_opciok[v_szolg]

    br_kulcs = f"br_{form_kulcs}"
    br_forras = kiv_pod_info["brutto"] if kiv_pod_info else (pdf_adat["brutto"] if pdf_adat else 0)
    alap_br_szoveg = f"{int(br_forras):,}".replace(",", " ") if br_forras > 0 else ""
    szamlan_brutto_str = c2.text_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=alap_br_szoveg, placeholder="pl. 6 648", key=br_kulcs, on_change=formaz_ezres, args=(br_kulcs,))
    br_szamjegyek = re.sub(r"\D", "", szamlan_brutto_str)
    szamlan_brutto = int(br_szamjegyek) if br_szamjegyek else 0

    kelte = pdf_adat["kelte"] if pdf_adat else date.today()
    hatarido = pdf_adat["hatarido"] if pdf_adat else date.today()
    alap_tol = kiv_pod_info["tol"] if kiv_pod_info else (pdf_adat["tol"] if pdf_adat else date.today())
    alap_ig = kiv_pod_info["ig"] if kiv_pod_info else (pdf_adat["ig"] if pdf_adat else date.today())
    tol = c3.date_input("Elszámolási időszak kezdete", value=alap_tol, key=f"t_{form_kulcs}")
    ig = c3.date_input("Elszámolási időszak vége", value=alap_ig, key=f"i_{form_kulcs}")
    datum_rendben = tol <= ig
    if not datum_rendben:
        c3.error("⚠️ Az időszak kezdete nem lehet későbbi, mint a vége!")
    elszamolasi_honap = szamol_elszamolasi_honap(ig)
    c3.info(f"📅 Elszámolási hónap: **{elszamolasi_honap}**")

    st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
    alap_df = pd.DataFrame(dict(Megnevezés=["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"], Mennyiség=[0.0, 0.0, 0.0, 0.0], Mértékegység=["kWh", "kWh", "kWh", "hó"], Nettó_egységár=[0.0, 0.0, 0.0, 0.0], ÁFA=[27.0, 27.0, 27.0, 27.0]))
    alap_df.columns = list(("Megnevezés", "Mennyiség", "Mértékegység", "Nettó egységár", "ÁFA (%)"))
    if kiv_pod_info and kiv_pod_info.get("tetelek_df") is not None:
        alap_df = kiv_pod_info["tetelek_df"]
    elif pdf_adat and pdf_adat.get("tetelek_df") is not None:
        alap_df = pdf_adat["tetelek_df"]
    elif fajl and fajl.name.lower().endswith(".xlsx"):
        alap_df = pd.read_excel(fajl)
    elif fajl and fajl.name.lower().endswith(".csv"):
        alap_df = pd.read_csv(fajl)

    szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True, key=f"ed_{form_kulcs}")
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
    if gomb and egyezik: mentes_adatbazisba(uj_szamla, tetelek, mentes_fajl, gyujto_marad=gyujto_mod)

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📑 Rögzített számlák", "📊 Kimutatások és Riportok", "🔄 Helyettesítések", "⚙️ Törzsadatok")), on_change=urit_urlap)
if menu == "🧾 Számla ellenőrzés és feltöltés":
    mutat_szamla_rogzites(int(user_sor["id"]))
elif menu == "📑 Rögzített számlák":
    mutat_rogzitett_szamlak()
elif menu == "📊 Kimutatások és Riportok":
    mutat_kimutatasok()
elif menu == "🔄 Helyettesítések":
    mutat_helyettesitesek()
else:
    mutat_torzsadatok()

