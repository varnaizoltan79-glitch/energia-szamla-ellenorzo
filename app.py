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
uj_szamla = {"szamlaszam": szamlaszam, "szolgaltato_id": szolg_id, "felhasznalasi_hely_id": hely_id, "forras_tipus": forras, "szamla_kelte": kelte.isoformat(), "fizetesi_hatarido": hatarido.isoformat(), "idoszak_tol": tol.isoformat(), "idoszak_ig": ig.isoformat(), "szamlan_szereplo_brutto": float(brutto_dec), "szamitott_brutto": float(ossz_brutto), "elteres": float(elteres), "ellenorzes_statusz": stat, "jovahagyta_energetikus_id": user_id}
if gomb and egyezik: mentes_adatbazisba(uj_szamla, tetelek, fajl)
