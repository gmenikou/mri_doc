###############################################
# app.py — Final Version (pypdfium2 FIXED)
###############################################

import streamlit as st
import os, io, json, base64
from datetime import datetime
import pandas as pd
from PIL import Image, ImageDraw
from pdfrw import PdfReader, PdfWriter, PageMerge
import pdfplumber

# reportlab
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics

# PDF rendering WITHOUT Poppler (works on Streamlit Cloud)
import pypdfium2 as pdfium


############################################################
# CONFIG
############################################################
TEMPLATES_DIR = "pdf_templates"
OUTPUT_DIR = "output"
PLACEMENTS_FILE = "placements.json"
RESPONSES_CSV = "responses.csv"

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Register Greek-compatible font
FONTPATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
if os.path.exists(FONTPATH):
    pdfmetrics.registerFont(TTFont("GreekFont", FONTPATH))
    PDF_FONT = "GreekFont"
else:
    PDF_FONT = "Helvetica"  # fallback


############################################################
# Streamlit setup
############################################################
st.set_page_config(page_title="MRI PDF Fill", layout="wide")
st.title("📄 Έντυπο Ελέγχου Ασφαλείας MRI — Fillable PDF (Cloud-Compatible)")


############################################################
# Utility functions
############################################################
def list_templates():
    return sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(".pdf")])


def load_placements():
    if os.path.exists(PLACEMENTS_FILE):
        with open(PLACEMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_placements(d):
    with open(PLACEMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def try_get_acrofields(pdf_path):
    try:
        rdr = PdfReader(pdf_path)
        try:
            fields = rdr.get_fields()
            return fields or None
        except:
            if hasattr(rdr.Root, "AcroForm"):
                return {}
            return None
    except:
        return None


############################################################
# Render PDF page → Image using pypdfium2 (NO POPPLER)
############################################################
def pdf_page_to_image(pdf_path, page_no=0, scale=2):
    pdf = pdfium.PdfDocument(pdf_path)
    page = pdf[page_no]
    bitmap = page.render(scale=scale)
    return bitmap.to_pil()


############################################################
# Convert click to PDF coordinate space
############################################################
def click_to_pdf_coords(click_x, click_y, img_w, img_h, pdf_w, pdf_h):
    px = click_x * (pdf_w / img_w)
    py = pdf_h - (click_y * (pdf_h / img_h))
    return px, py


############################################################
# Extract labels from PDF (OCR-like)
############################################################
def extract_words_on_page(pdf_path, page_no=0, min_chars=3):
    items = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_no >= len(pdf.pages):
                return items
            page = pdf.pages[page_no]
            page_h = page.height
            for w in page.extract_words():
                text = (w["text"] or "").strip()
                if len(text) < min_chars:
                    continue
                xmid = (float(w["x0"]) + float(w["x1"])) / 2
                ymid = page_h - ((float(w["top"]) + float(w["bottom"])) / 2)
                items.append({
                    "text": text,
                    "x_mid": xmid,
                    "y_mid": ymid,
                    "page_num": page_no,
                    "page_height": page_h,
                })
    except:
        pass
    return items


############################################################
# Draw tickmark ✓
############################################################
def draw_tick(c, x, y, size):
    c.setLineWidth(size * 0.15)
    c.line(x - size*0.4, y, x - size*0.1, y - size*0.4)
    c.line(x - size*0.1, y - size*0.4, x + size*0.6, y + size*0.4)


############################################################
# Create overlay & merge into final PDF
############################################################
def create_overlay_and_merge(responses, placements, signatures, template_path, out_name):
    reader = PdfReader(template_path)
    overlays = []

    for i, page in enumerate(reader.pages):
        mb = page.MediaBox
        w = float(mb[2]) - float(mb[0])
        h = float(mb[3]) - float(mb[1])

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))

        for p in placements.get(str(i), []):
            field = p["field"]
            ftype = p.get("type", "text")
            x, y = float(p["x"]), float(p["y"])
            fs = float(p["fontsize"])
            c.setFont(PDF_FONT, fs)

            val = responses.get(field, "")

            if ftype == "checkbox":
                if val:
                    draw_tick(c, x, y, fs)

            elif ftype == "signature":
                sig = signatures.get(field)
                if sig:
                    img = Image.open(io.BytesIO(sig))
                    b = io.BytesIO()
                    img.save(b, format="PNG")
                    b.seek(0)
                    c.drawImage(ImageReader(b), x, y, width=150, height=50)
                else:
                    c.drawString(x, y, str(val))

            else:
                c.drawString(x, y, str(val))

        c.save()
        buf.seek(0)
        ov_path = os.path.join(OUTPUT_DIR, f"_overlay_{i}.pdf")
        with open(ov_path, "wb") as f:
            f.write(buf.read())
        overlays.append(ov_path)

    result = os.path.join(OUTPUT_DIR, out_name)
    writer = PdfWriter()

    tmpl = PdfReader(template_path)
    for i, page in enumerate(tmpl.pages):
        ov = PdfReader(overlays[i]).pages[0]
        merger = PageMerge(page)
        merger.add(ov).render()
        writer.addpage(page)

    writer.write(result)
    return result


############################################################
# MAIN UI
############################################################

templates = list_templates()
if not templates:
    st.error("🚫 Δεν υπάρχουν PDF στο pdf_templates/")
    st.stop()

selected_template = st.selectbox("📄 Επίλεξε Έντυπο:", templates)
template_path = os.path.join(TEMPLATES_DIR, selected_template)

placements_all = load_placements()
current_placements = placements_all.get(selected_template, {})

acro = try_get_acrofields(template_path)
has_acro = acro is not None

if has_acro:
    st.success("🟢 AcroForm πεδία εντοπίστηκαν.")
else:
    st.info("ℹ️ Χωρίς AcroForm — χρησιμοποιείται overlay mode.")


############################################################
# FORM GENERATION (Overlay)
############################################################
responses = {}
signatures = {}

st.header("📝 Συμπλήρωση Φόρμας")

with st.expander("🔍 Προεπισκόπηση υποψήφιων ετικετών"):
    page_prev = st.number_input("Σελίδα:", 0, 5, 0)
    st.dataframe(pd.DataFrame(extract_words_on_page(template_path, page_prev)))

candidates = []
for p in range(2):
    candidates.extend(extract_words_on_page(template_path, p))

seen = set()
unique = []
for w in candidates:
    k = (w["text"], w["page_num"], round(w["x_mid"], 1))
    if k not in seen:
        seen.add(k)
        unique.append(w)

for idx, item in enumerate(unique[:40]):
    t = item["text"]
    key = f"field_{idx}"

    with st.container(border=True):
        st.write(f"**{t}** (σελ {item['page_num']})")
        typ = st.selectbox("Τύπος:", ["text","checkbox","signature"], key=f"type_{key}")

        if typ == "checkbox":
            responses[key] = st.checkbox("Τιμή:", key=f"in_{key}")
        elif typ == "signature":
            up = st.file_uploader("Υπογραφή:", type=["png","jpg","jpeg"], key=f"up_{key}")
            if up:
                signatures[key] = up.read()
        else:
            responses[key] = st.text_area("Τιμή:", key=f"in_{key}")

        pg = str(item["page_num"])
        current_placements.setdefault(pg, [])
        if not any(p["field"] == key for p in current_placements[pg]):
            current_placements[pg].append({
                "field": key,
                "label": t,
                "x": item["x_mid"] + 20,
                "y": item["y_mid"],
                "fontsize": 11,
                "type": typ
            })

placements_all[selected_template] = current_placements
save_placements(placements_all)


############################################################
# LIVE PREVIEW WITH MARKERS
############################################################
st.header("👁️ Προεπισκόπηση Θέσεων")

page_prev = st.number_input("Σελίδα preview:", 0, 5, 0)
img = pdf_page_to_image(template_path, page_prev, scale=2)
draw = ImageDraw.Draw(img)

pg_str = str(page_prev)
if pg_str in current_placements:
    w, h = img.size
    mb = PdfReader(template_path).pages[page_prev].MediaBox
    pdf_w = float(mb[2]) - float(mb[0])
    pdf_h = float(mb[3]) - float(mb[1])

    for p in current_placements[pg_str]:
        x, y = p["x"], p["y"]
        ix = int((x / pdf_w) * w)
        iy = int(h - (y / pdf_h) * h)
        draw.ellipse((ix-8, iy-8, ix+8, iy+8), fill="red")
        draw.text((ix+12, iy-12), p["field"], fill="red")

st.image(img, use_container_width=True)


############################################################
# CLICK-TO-PLACE / DRAG-LIKE MOVEMENT
############################################################
st.header("✋ Μετακίνηση Πεδίου με Κλικ")

fields_here = [p["field"] for p in current_placements.get(pg_str, [])]
if fields_here:
    selected_field = st.selectbox("Πεδίο:", fields_here)

st.write("👉 Κλίκαρε πάνω στην εικόνα για να μετακινήσεις το πεδίο.")

# JS for click capture
js = """
<script>
document.addEventListener('click', function(e) {
    const img = document.querySelector('img[alt=""]');
    if (!img) return;
    const r = img.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right) return;
    if (e.clientY < r.top || e.clientY > r.bottom) return;
    const x = (e.clientX - r.left) * (img.naturalWidth / img.width);
    const y = (e.clientY - r.top)  * (img.naturalHeight / img.height);
    window.parent.postMessage({type:"pdf_click", x:x, y:y}, "*");
});
</script>
"""
st.components.v1.html(js, height=0)

# Read click from query params
msg = st.experimental_get_query_params()
if "pdf_click_x" in msg:
    cx = float(msg["pdf_click_x"][0])
    cy = float(msg["pdf_click_y"][0])

    st.success(f"Κλικ στις εικόνες: ({cx:.1f}, {cy:.1f})")

    if selected_field:
        mb = PdfReader(template_path).pages[page_prev].MediaBox
        pdf_w = float(mb[2]) - float(mb[0])
        pdf_h = float(mb[3]) - float(mb[1])

        img_w, img_h = img.size
        nx, ny = click_to_pdf_coords(cx, cy, img_w, img_h, pdf_w, pdf_h)

        for p in current_placements.get(pg_str, []):
            if p["field"] == selected_field:
                p["x"] = nx
                p["y"] = ny

        placements_all[selected_template] = current_placements
        save_placements(placements_all)

        st.success(f"➡️ Πεδίο μετακινήθηκε στο ({nx:.1f} , {ny:.1f})")


############################################################
# SUBMIT & GENERATE PDF
############################################################
st.header("📄 Δημιουργία PDF")

if st.button("✔ Υποβολή & Δημιουργία PDF"):

    meta = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    record = responses.copy()
    record.update(meta)

    if os.path.exists(RESPONSES_CSV):
        old = pd.read_csv(RESPONSES_CSV)
        pd.concat([old, pd.DataFrame([record])], ignore_index=True).to_csv(RESPONSES_CSV, index=False)
    else:
        pd.DataFrame([record]).to_csv(RESPONSES_CSV, index=False)

    out_path = create_overlay_and_merge(
        responses, current_placements, signatures,
        template_path,
        out_name=f"filled_{selected_template}"
    )

    with open(out_path, "rb") as f:
        st.download_button("⬇️ Κατέβασμα PDF", f, file_name=f"filled.pdf", mime="application/pdf")

    st.success("🎉 Το PDF δημιουργήθηκε!")


############################################################
# ADMIN PANEL
############################################################
st.header("🛠️ Admin")

with st.expander("📁 Προβολή CSV"):
    if os.path.exists(RESPONSES_CSV):
        df = pd.read_csv(RESPONSES_CSV)
        st.dataframe(df)
        st.download_button("⬇ CSV", df.to_csv(index=False), "responses.csv")
    else:
        st.info("Δεν υπάρχουν απαντήσεις.")

with st.expander("🧩 Placements"):
    st.json(placements_all)
    if st.button("♻ Διαγραφή placements.json"):
        if os.path.exists(PLACEMENTS_FILE):
            os.remove(PLACEMENTS_FILE)
        st.success("OK")

