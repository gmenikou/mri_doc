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
from reportlab.lib.units import mm
from PIL import Image
import pdfplumber

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
    """Try to read AcroForm fields using pdfrw. Returns dict or None."""
    try:
        reader = PdfReader(pdf_path)
        fields = reader.Root.AcroForm.Fields if hasattr(reader.Root, "AcroForm") else None
        # pdfrw returns objects; we'll try reader.get_fields() if available
        try:
            fdict = reader.get_fields()
            return fdict
        except Exception:
            return None
    except Exception:
        return None

def extract_labels_positions(pdf_path, page_no=0, min_chars=3):
    """
    Use pdfplumber to extract text boxes and approximate coordinates.
    Returns list of (text, x_mid, y_mid, bbox)
    Coordinates are in PDF user-space (origin bottom-left).
    """
    labels = []
    with pdfplumber.open(pdf_path) as pdf:
        if page_no < 0 or page_no >= len(pdf.pages):
            return []
        page = pdf.pages[page_no]
        for obj in page.extract_words():  # list of dicts with text and bbox
            text = obj.get("text", "").strip()
            if len(text) >= min_chars:
                x0, top, x1, bottom = obj["x0"], obj["top"], obj["x1"], obj["bottom"]
                # pdfplumber top=distance from top; convert later as needed
                # Compute midpoints
                x_mid = (x0 + x1) / 2.0
                # convert pdfplumber top/bottom to PDF coordinate where origin is bottom-left
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

def create_overlay_pdf(responses, placements, template_path, tmp_overlay_path):
    """
    Create a one-page-per-template overlay PDF with responses drawn at positions.
    `placements` is a dict: page_num -> list of {field, x, y, fontsize, align}
    Coordinates in PDF user space (bottom-left origin).
    """
    # We need to create a same-size PDF for each page in template and draw text.
    reader = PdfReader(template_path)
    num_pages = len(reader.pages)
    packet_pages = []

    # We'll create a PDF in memory for each page and then merge
    overlay_paths = []
    for page_idx in range(num_pages):
        page = reader.pages[page_idx]
        # get page size from /MediaBox
        try:
            mediabox = page.MediaBox
            llx, lly, urx, ury = [float(mediabox[i]) for i in range(4)]
            width = urx - llx
            height = ury - lly
        except Exception:
            # default letter
            width, height = letter

        # create canvas
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(width, height))

        # draw each placement for this page
        for p in placements.get(page_idx, []):
            text = str(responses.get(p["field"], ""))
            if text is None:
                text = ""
            x = float(p["x"])
            y = float(p["y"])
            fontsize = p.get("fontsize", 11)
            align = p.get("align", "left")
            c.setFont("Helvetica", fontsize)
            # adjust alignment
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

    # merge overlays onto template
    out_path = os.path.join(OUTPUT_DIR, f"filled_{os.path.basename(template_path)}")
    writer = PdfWriter()
    template = PdfReader(template_path)
    for i, tpage in enumerate(template.pages):
        # if there is an overlay for this page, merge
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
    st.warning("Στον φάκελο `pdf_templates/` δεν βρέθηκαν αρχεία PDF. Ανέβασε το PDF σου εκεί (ή χρησιμοποίησε το /mnt/data που ανέβασες).")
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
# If AcroForm: build UI from fields
# -----------------------
responses = {}
placements = {}  # page_num -> list of placements
if has_acro:
    # Build form from acro keys
    # acro is dict field_name -> field object
    field_names = list(acro.keys())
    st.subheader("Φόρμα (αυτόματα από AcroForm)")
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        with st.form("main_form"):
            for name in field_names:
                label = name
                # simple type heuristics
                low = name.lower()
                if "ημερομην" in low or "date" in low:
                    responses[name] = st.text_input(label, placeholder="DD/MM/YYYY")
                elif "ηλικ" in low or "age" in low or "βάρος" in low:
                    responses[name] = st.text_input(label)
                elif "φύλο" in low or "αρρεν" in low or "θήλυ" in low:
                    responses[name] = st.selectbox(label, ["", "Άρρεν", "Θήλυ", "Άλλο"])
                elif "check" in low or "agree" in low or "ναι" in low or "όχι" in low:
                    responses[name] = st.checkbox(label)
                else:
                    responses[name] = st.text_area(label, height=80)
            submitted = st.form_submit_button("Υποβολή")
else:
    # No AcroForm: try to auto-extract label positions for the first N pages
    st.subheader("Αυτόματη εξαγωγή ετικετών/θέσεων (πρόχειρη τοποθέτηση)")
    with st.expander("Ρυθμίσεις εξαγωγής (Advanced)", expanded=False):
        sample_page = st.number_input("Σελίδα προεπισκόπησης (0-indexed)", min_value=0, max_value=10, value=0)
        min_chars = st.slider("Ελάχιστοι χαρακτήρες για ετικέτα", 2, 10, 3)
    labels = extract_labels_positions(template_path, page_no=int(sample_page), min_chars=min_chars)
    st.write(f"Βρέθηκαν ~{len(labels)} ετικέτες στην σελίδα {sample_page}.")
    # Show a small table of labels
    if labels:
        df_labels = pd.DataFrame([
            {"text": l["text"], "x_mid": round(l["x_mid"],1), "y_mid": round(l["y_mid"],1)} for l in labels
        ])
        st.dataframe(df_labels)

    st.markdown("**Δημιουργία φόρμας από τις εξαγμένες ετικέτες** — ελέγξτε και τροποποιήστε τα πεδία πριν την υποβολή.")
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        with st.form("main_form"):
            # We'll pick a subset of labels as field names (e.g., long Greek labels like 'Ονοματεπώνυμο', etc.)
            # heuristics: pick labels that are longer than 3 chars and likely to represent a field label
            chosen = []
            for l in labels:
                t = l["text"]
                # Filter out footers or repeated words (very short)
                if len(t) >= 3 and t.lower() not in ["σελίδα", "σχετικά", "tel", "τηλ", "fax"]:
                    chosen.append(l)
            # To avoid explosion, limit chosen to 40 fields (you can expand)
            chosen = chosen[:60]

            # Build an input for each chosen label. We'll store default placement next to label
            for idx, l in enumerate(chosen):
                field_key = f"field_{idx}"
                label_text = l["text"]
                st.markdown("---")
                st.markdown(f"**{label_text}**")
                # choose input type heuristics
                low = label_text.lower()
                if any(k in low for k in ["ναι", "όχι", "check", "✔", "□", ""]):
                    responses[field_key] = st.checkbox(label_text)
                elif any(k in low for k in ["ημερ", "date", "γενν"]):
                    responses[field_key] = st.text_input(label_text, placeholder="DD/MM/YYYY")
                elif any(k in low for k in ["ηλικ", "βάρος", "ύψος", "τηλ"]):
                    responses[field_key] = st.text_input(label_text)
                else:
                    responses[field_key] = st.text_area(label_text, height=80)

                # default placement: place to the right of the label x_mid + offset
                page_h = l["page_height"]
                x = l["x_mid"] + 20  # 20 user-units to the right
                y = l["y_mid"]
                # default fontsize
                fontsize = 11
                # Save placement to allow later adjustment (per-page)
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
# Submission handling
# -----------------------
if 'submitted' in locals() and submitted:
    st.success("Καταγραφή υποβολής...")
    # Add timestamp and form name
    meta = {"Form": selected, "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    flat_responses = responses.copy()
    flat_responses.update(meta)

    # Save CSV
    if os.path.exists(RESPONSES_CSV):
        old = pd.read_csv(RESPONSES_CSV)
        new = pd.DataFrame([flat_responses])
        dfall = pd.concat([old, new], ignore_index=True)
        dfall.to_csv(RESPONSES_CSV, index=False)
    else:
        pd.DataFrame([flat_responses]).to_csv(RESPONSES_CSV, index=False)

    st.info("Τα δεδομένα αποθηκεύτηκαν στον κεντρικό κατάλογο.")

    # -----------------------
    # PDF generation
    # -----------------------
    st.info("Παραγωγή τελικού PDF...")

    if has_acro:
        # Fill acro fields using pdfrw
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
        # Use overlay merge method with placements computed earlier
        if not placements:
            st.error("Δεν υπάρχουν θέσεις για τοποθέτηση τιμών. Ενεργοποίησε την επιλογή 'Advanced' και δοκίμασε άλλη σελίδα.")
        else:
            overlay_tmp = os.path.join(OUTPUT_DIR, "tmp_overlay.pdf")
            out_path = create_overlay_pdf(responses, placements, template_path, overlay_tmp)
            st.success("Παραγωγή PDF (overlay) ολοκληρώθηκε.")
            with open(out_path, "rb") as f:
                st.download_button("⬇️ Κατέβασμα συμπληρωμένου PDF", f, file_name=f"filled_overlay_" + selected, mime="application/pdf")

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
