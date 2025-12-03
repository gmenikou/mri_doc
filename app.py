# app.py
import streamlit as st
import os
import io
import pandas as pd
from datetime import datetime

# PDF libs
from pdfrw import PdfReader, PdfWriter, PageMerge
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import pdfplumber
from PIL import Image
import matplotlib.pyplot as plt
import fitz  # PyMuPDF

# -----------------------
# CONFIG
# -----------------------
TEMPLATES_DIR = "pdf_templates"
OUTPUT_DIR = "output"
RESPONSES_CSV = "responses.csv"

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

st.set_page_config(page_title="Έντυπο MRI — Fillable (Tablet)", layout="wide")
st.title("Έντυπο Ελέγχου Ασφαλείας MRI — Web Fill & Exact PDF")

# -----------------------
# utils
# -----------------------
def list_templates(folder):
    return [f for f in os.listdir(folder) if f.lower().endswith(".pdf")]

def try_get_acrofields(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        fields = reader.Root.AcroForm.Fields if hasattr(reader.Root, "AcroForm") else None
        try:
            fdict = reader.get_fields()
            return fdict
        except Exception:
            return None
    except Exception:
        return None

def extract_labels_positions(pdf_path, page_no=0, min_chars=3):
    labels = []
    with pdfplumber.open(pdf_path) as pdf:
        if page_no < 0 or page_no >= len(pdf.pages):
            return []
        page = pdf.pages[page_no]
        for obj in page.extract_words():
            text = obj.get("text", "").strip()
            if len(text) >= min_chars:
                x0, top, x1, bottom = obj["x0"], obj["top"], obj["x1"], obj["bottom"]
                x_mid = (x0 + x1) / 2.0
                page_height = page.height
                y_mid = page_height - ((top + bottom) / 2.0)
                labels.append({
                    "text": text,
                    "x_mid": x_mid,
                    "y_mid": y_mid,
                    "x0": x0, "x1": x1, "top": top, "bottom": bottom,
                    "page_num": page_no,
                    "page_height": page.height
                })
    return labels

def create_overlay_pdf(responses, placements, template_path):
    reader = PdfReader(template_path)
    num_pages = len(reader.pages)
    overlay_paths = []

    for page_idx in range(num_pages):
        page = reader.pages[page_idx]
        try:
            mediabox = page.MediaBox
            llx, lly, urx, ury = [float(mediabox[i]) for i in range(4)]
            width = urx - llx
            height = ury - lly
        except Exception:
            width, height = letter

        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(width, height))

        for p in placements.get(page_idx, []):
            text = str(responses.get(p["field"], ""))
            x = float(p["x"])
            y = float(p["y"])
            fontsize = p.get("fontsize", 11)
            align = p.get("align", "left")
            c.setFont("Helvetica", fontsize)
            if align == "center":
                text_width = c.stringWidth(text, "Helvetica", fontsize)
                c.drawString(x - text_width/2.0, y, text)
            elif align == "right":
                text_width = c.stringWidth(text, "Helvetica", fontsize)
                c.drawString(x - text_width, y, text)
            else:
                c.drawString(x, y, text)

        c.save()
        packet.seek(0)
        overlay_path = os.path.join(OUTPUT_DIR, f"overlay_page_{page_idx}.pdf")
        with open(overlay_path, "wb") as f:
            f.write(packet.read())
        overlay_paths.append(overlay_path)

    out_path = os.path.join(OUTPUT_DIR, f"filled_{os.path.basename(template_path)}")
    writer = PdfWriter()
    template = PdfReader(template_path)
    for i, tpage in enumerate(template.pages):
        if i < len(overlay_paths):
            overlay_pdf = PdfReader(overlay_paths[i])
            overlay_page = overlay_pdf.pages[0]
            merger = PageMerge(tpage)
            merger.add(overlay_page).render()
            writer.addpage(tpage)
        else:
            writer.addpage(tpage)
    writer.write(out_path)
    return out_path

# -----------------------
# UI: pick template
# -----------------------
templates = list_templates(TEMPLATES_DIR)
if not templates:
    st.warning("Στον φάκελο `pdf_templates/` δεν βρέθηκαν αρχεία PDF.")
    st.stop()

selected = st.selectbox("Επίλεξε έντυπο για συμπλήρωση:", templates)
template_path = os.path.join(TEMPLATES_DIR, selected)
st.markdown(f"**Επιλεγμένο έντυπο:** {selected}")

# -----------------------
# Detect AcroForm fields
# -----------------------
st.write("Εντοπισμός πεδίων φόρμας (AcroForm)...")
acro = try_get_acrofields(template_path)
has_acro = bool(acro)
if has_acro:
    st.success("Αυτό το PDF έχει AcroForm fields — θα γεμίσουμε τα πεδία απευθείας (ακριβές).")
else:
    st.info("Δεν εντοπίστηκαν AcroForm fields — θα κάνουμε έξυπνη τοποθέτηση πάνω στο πρότυπο (best-effort).")

# -----------------------
# Build form with unique keys
# -----------------------
responses = {}
placements = {}
if has_acro:
    field_names = list(acro.keys())
    st.subheader("Φόρμα (αυτόματα από AcroForm)")
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        with st.form("main_form"):
            for name in field_names:
                label = name
                low = name.lower()
                field_key = f"acro_{name}"
                if "ημερομην" in low or "date" in low:
                    responses[name] = st.text_input(label, placeholder="DD/MM/YYYY", key=field_key)
                elif "ηλικ" in low or "age" in low or "βάρος" in low:
                    responses[name] = st.text_input(label, key=field_key)
                elif "φύλο" in low or "αρρεν" in low or "θήλυ" in low:
                    responses[name] = st.selectbox(label, ["", "Άρρεν", "Θήλυ", "Άλλο"], key=field_key)
                elif "check" in low or "agree" in low or "ναι" in low or "όχι" in low:
                    responses[name] = st.checkbox(label, key=field_key)
                else:
                    responses[name] = st.text_area(label, height=80, key=field_key)
            submitted = st.form_submit_button("Υποβολή")
else:
    st.subheader("Αυτόματη εξαγωγή ετικετών/θέσεων (πρόχειρη τοποθέτηση)")
    with st.expander("Ρυθμίσεις εξαγωγής (Advanced)", expanded=False):
        sample_page = st.number_input("Σελίδα προεπισκόπησης (0-indexed)", min_value=0, max_value=10, value=0)
        min_chars = st.slider("Ελάχιστοι χαρακτήρες για ετικέτα", 2, 10, 3)
    labels = extract_labels_positions(template_path, page_no=int(sample_page), min_chars=min_chars)
    st.write(f"Βρέθηκαν ~{len(labels)} ετικέτες στην σελίδα {sample_page}.")
    if labels:
        df_labels = pd.DataFrame([{"text": l["text"], "x_mid": round(l["x_mid"],1), "y_mid": round(l["y_mid"],1)} for l in labels])
        st.dataframe(df_labels)

    st.markdown("**Δημιουργία φόρμας από τις εξαγμένες ετικέτες** — ελέγξτε και τροποποιήστε τα πεδία πριν την υποβολή.")
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        with st.form("main_form"):
            chosen = []
            for l in labels:
                t = l["text"]
                if len(t) >= 3 and t.lower() not in ["σελίδα", "σχετικά", "tel", "τηλ", "fax"]:
                    chosen.append(l)
            chosen = chosen[:60]
            for idx, l in enumerate(chosen):
                field_key = f"field_{idx}"
                label_text = l["text"]
                st.markdown("---")
                st.markdown(f"**{label_text}**")
                low = label_text.lower()
                if any(k in low for k in ["ναι", "όχι", "check", "✔", "□", ""]):
                    responses[field_key] = st.checkbox(label_text, key=field_key)
                elif any(k in low for k in ["ημερ", "date", "γενν"]):
                    responses[field_key] = st.text_input(label_text, placeholder="DD/MM/YYYY", key=field_key)
                elif any(k in low for k in ["ηλικ", "βάρος", "ύψος", "τηλ"]):
                    responses[field_key] = st.text_input(label_text, key=field_key)
                else:
                    responses[field_key] = st.text_area(label_text, height=80, key=field_key)

                page_h = l["page_height"]
                x = l["x_mid"] + 20
                y = l["y_mid"]
                fontsize = 11
                placements.setdefault(l["page_num"], []).append({
                    "field": field_key,
                    "label": label_text,
                    "x": x,
                    "y": y,
                    "fontsize": fontsize,
                    "align": "left"
                })
            submitted = st.form_submit_button("Υποβολή")

    # -----------------------
    # Overlay preview
    # -----------------------
    if chosen:
        st.subheader("Preview τοποθέτησης τιμών (Overlay)")
        for page_idx, page_fields in placements.items():
            pdf_doc = fitz.open(template_path)
            page = pdf_doc[page_idx]
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            fig, ax = plt.subplots(figsize=(8, 11))
            ax.imshow(img)
            for f in page_fields:
                x, y = f["x"], f["y"]
                text = str(responses.get(f["field"], ""))
                ax.plot(x, img.height - y, "ro")
                ax.text(x, img.height - y, f"{text}", color="red", fontsize=8, bbox=dict(facecolor='yellow', alpha=0.5))
            ax.axis("off")
            st.pyplot(fig)

# -----------------------
# Submission handling
# -----------------------
if 'submitted' in locals() and submitted:
    st.success("Καταγραφή υποβολής...")
    meta = {"Form": selected, "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    flat_responses = responses.copy()
    flat_responses.update(meta)

    if os.path.exists(RESPONSES_CSV):
        old = pd.read_csv(RESPONSES_CSV)
        new = pd.DataFrame([flat_responses])
        dfall = pd.concat([old, new], ignore_index=True)
        dfall.to_csv(RESPONSES_CSV, index=False)
    else:
        pd.DataFrame([flat_responses]).to_csv(RESPONSES_CSV, index=False)

    st.info("Τα δεδομένα αποθηκεύτηκαν στον κεντρικό κατάλογο.")

    st.info("Παραγωγή τελικού PDF...")
    if has_acro:
        inp_pdf = PdfReader(template_path)
        for page in inp_pdf.pages:
            if hasattr(page, "Annots"):
                for annot in page.Annots:
                    if annot.Subtype == "/Widget" and annot.T:
                        key = annot.T[1:-1]
                        if key in responses:
                            val = responses[key]
                            if isinstance(val, bool):
                                val = "Yes" if val else "No"
                            annot.V = f"({val})"
        out_path = os.path.join(OUTPUT_DIR, f"filled_{selected}")
        PdfWriter().write(out_path, inp_pdf)
        st.success("Παραγωγή PDF ολοκληρώθηκε.")
        with open(out_path, "rb") as f:
            st.download_button("⬇️ Κατέβασμα συμπληρωμένου PDF", f, file_name=f"filled_{selected}", mime="application/pdf")
    else:
        if not placements:
            st.error("Δεν υπάρχουν θέσεις για τοποθέτηση τιμών. Δοκίμασε άλλη σελίδα.")
        else:
            out_path = create_overlay_pdf(responses, placements, template_path)
            st.success("Παραγωγή PDF (overlay) ολοκληρώθηκε.")
            with open(out_path, "rb") as f:
                st.download_button("⬇️ Κατέβασμα συμπληρωμένου PDF", f, file_name="filled_overlay_" + selected, mime="application/pdf")
    st.balloons()

# -----------------------
# Admin panel
# -----------------------
st.markdown("---")
with st.expander("Διαχειριστής: Προβολή Απαντήσεων / Λήψη CSV"):
    if os.path.exists(RESPONSES_CSV):
        df = pd.read_csv(RESPONSES_CSV)
        st.dataframe(df)
        st.download_button("Λήψη όλων (CSV)", df.to_csv(index=False).encode("utf-8"), file_name="all_responses.csv", mime="text/csv")
    else:
        st.info("Δεν υπάρχει αρχείο απαντήσεων ακόμη.")
