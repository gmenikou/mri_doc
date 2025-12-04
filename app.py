###############################################
# app.py — Final Version (All Features Enabled)
###############################################

import streamlit as st
import os, io, json, math, base64
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

# pdf2image (for previews & click positioning)
from pdf2image import convert_from_path

# ---------------------------------------------
# CONFIG
# ---------------------------------------------
TEMPLATES_DIR = "pdf_templates"
OUTPUT_DIR = "output"
PLACEMENTS_FILE = "placements.json"
RESPONSES_CSV = "responses.csv"

os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Register Greek font for PDF output
FONTPATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
if os.path.exists(FONTPATH):
    pdfmetrics.registerFont(TTFont("GreekFont", FONTPATH))
    PDF_FONT = "GreekFont"
else:
    PDF_FONT = "Helvetica"   # Fallback


# ---------------------------------------------
# Streamlit layout
# ---------------------------------------------
st.set_page_config(page_title="MRI PDF Fill", layout="wide")
st.title("📄 Έντυπο Ελέγχου Ασφαλείας MRI — Fillable PDF (Full Version)")


# ---------------------------------------------
# Utility: List templates
# ---------------------------------------------
def list_templates():
    return sorted([f for f in os.listdir(TEMPLATES_DIR) if f.lower().endswith(".pdf")])


# ---------------------------------------------
# Utility: Placements
# ---------------------------------------------
def load_placements():
    if os.path.exists(PLACEMENTS_FILE):
        with open(PLACEMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_placements(d):
    with open(PLACEMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


# ---------------------------------------------
# AcroForm detection
# ---------------------------------------------
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


# ---------------------------------------------
# PDF → Image (for preview & click placement)
# ---------------------------------------------
def pdf_page_to_image(pdf_path, page_no=0, dpi=150):
    pages = convert_from_path(pdf_path, dpi=dpi, first_page=page_no+1, last_page=page_no+1)
    return pages[0]


# ---------------------------------------------
# Click → PDF coordinate conversion
# ---------------------------------------------
def click_to_pdf_coords(click_x, click_y, img_w, img_h, pdf_w, pdf_h):
    px = click_x * (pdf_w / img_w)
    py = pdf_h - (click_y * (pdf_h / img_h))
    return px, py


# ---------------------------------------------
# Word extraction
# ---------------------------------------------
def extract_words_on_page(pdf_path, page_no=0, min_chars=3):
    items = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_no < 0 or page_no >= len(pdf.pages):
                return items
            page = pdf.pages[page_no]
            page_h = page.height
            for w in page.extract_words():
                text = (w["text"] or "").strip()
                if len(text) < min_chars:
                    continue
                x0, x1 = float(w["x0"]), float(w["x1"])
                top, bottom = float(w["top"]), float(w["bottom"])
                xmid = (x0 + x1) / 2
                ymid = page_h - ((top + bottom) / 2)
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


# ---------------------------------------------
# Tickmark drawing
# ---------------------------------------------
def draw_tick(c, x, y, size):
    c.setLineWidth(size * 0.15)
    c.line(x - size*0.4, y, x - size*0.1, y - size*0.4)
    c.line(x - size*0.1, y - size*0.4, x + size*0.6, y + size*0.4)


# ---------------------------------------------
# Create overlay + merge into final PDF
# ---------------------------------------------
def create_overlay_and_merge(responses, placements, signatures, template_path, out_name):
    reader = PdfReader(template_path)
    num_pages = len(reader.pages)
    overlays = []

    for i in range(num_pages):
        page = reader.pages[i]

        try:
            mb = page.MediaBox
            w = float(mb[2]) - float(mb[0])
            h = float(mb[3]) - float(mb[1])
        except:
            w, h = letter

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setFont(PDF_FONT, 12)

        for p in placements.get(str(i), []):
            f = p["field"]
            ftype = p.get("type", "text")
            x = float(p["x"])
            y = float(p["y"])
            fs = float(p.get("fontsize", 11))
            align = p.get("align", "left")

            c.setFont(PDF_FONT, fs)
            val = responses.get(f, "")

            if ftype == "checkbox":
                if val:
                    draw_tick(c, x, y, fs)

            elif ftype == "signature":
                sig = signatures.get(f)
                if sig:
                    try:
                        img = Image.open(io.BytesIO(sig))
                        img_buf = io.BytesIO()
                        img.save(img_buf, format="PNG")
                        img_buf.seek(0)
                        c.drawImage(ImageReader(img_buf), x, y, width=150, height=50, mask="auto")
                    except:
                        c.drawString(x, y, str(val))
                else:
                    c.drawString(x, y, str(val))

            else:
                text = str(val)
                if align == "center":
                    tw = c.stringWidth(text, PDF_FONT, fs)
                    c.drawString(x - tw/2, y, text)
                else:
                    c.drawString(x, y, text)

        c.save()
        buf.seek(0)
        overlay_path = os.path.join(OUTPUT_DIR, f"_overlay_{i}.pdf")
        with open(overlay_path, "wb") as f:
            f.write(buf.read())
        overlays.append(overlay_path)

    result_path = os.path.join(OUTPUT_DIR, out_name)
    w = PdfWriter()

    tmpl = PdfReader(template_path)
    for i, page in enumerate(tmpl.pages):
        if i < len(overlays):
            ov = PdfReader(overlays[i]).pages[0]
            merger = PageMerge(page)
            merger.add(ov).render()
        w.addpage(page)

    w.write(result_path)
    return result_path


#########################################################
#                   MAIN APP UI
#########################################################

templates = list_templates()
if not templates:
    st.error("❗ Δεν υπάρχει κανένα PDF στον φάκελο pdf_templates/")
    st.stop()

selected_template = st.selectbox("📄 Επέλεξε έντυπο:", templates)
template_path = os.path.join(TEMPLATES_DIR, selected_template)

placements_all = load_placements()
current_placements = placements_all.get(selected_template, {})

acro = try_get_acrofields(template_path)
has_acro = acro is not None

if has_acro:
    st.success("🟢 AcroForm fields detected.")
else:
    st.info("ℹ️ No AcroForm. Using overlay mode.")


#########################################################
#                BUILD FORM (Overlay)
#########################################################

responses = {}
signatures = {}

st.header("📝 Συμπλήρωση Φόρμας")

with st.expander("🔍 Προτεινόμενα πεδία από PDF (OCR-like)"):
    preview_page = st.slider("Σελίδα", 0, 10, 0)
    words = extract_words_on_page(template_path, preview_page, min_chars=3)
    st.dataframe(pd.DataFrame(words))

st.subheader("📌 Επιλογή Πεδίου & Συμπλήρωση")
all_candidates = []
for p in range(0, 3):
    all_candidates.extend(extract_words_on_page(template_path, p, 3))

seen = set()
candidates = []
for w in all_candidates:
    key = (w["text"], w["page_num"], round(w["x_mid"], 1))
    if key not in seen:
        seen.add(key)
        candidates.append(w)

for idx, item in enumerate(candidates[:50]):
    t = item["text"]
    key = f"field_{idx}"
    with st.container(border=True):
        st.write(f"**{t}** (σελ {item['page_num']})")

        typ = st.selectbox("Τύπος:", ["text", "checkbox", "signature"], key=f"type_{key}")
        if typ == "checkbox":
            responses[key] = st.checkbox("Τιμή:", key=f"in_{key}")
        elif typ == "signature":
            up = st.file_uploader("Υπογραφή:", type=["png","jpg","jpeg"], key=f"up_{key}")
            if up:
                signatures[key] = up.read()
        else:
            responses[key] = st.text_area("Τιμή:", key=f"in_{key}")

        # place if not exists
        pg = str(item["page_num"])
        if pg not in current_placements:
            current_placements[pg] = []
        exists = any(p["field"] == key for p in current_placements[pg])
        if not exists:
            current_placements[pg].append({
                "field": key,
                "label": t,
                "x": item["x_mid"] + 20,
                "y": item["y_mid"],
                "fontsize": 11,
                "align": "left",
                "type": typ,
            })


#########################################################
#           LIVE PDF PREVIEW WITH MARKERS
#########################################################

st.header("👁️ Προεπισκόπηση Θέσεων")

page_prev = st.number_input("Σελίδα:", 0, 10, 0)
img = pdf_page_to_image(template_path, page_prev, dpi=150)
draw = ImageDraw.Draw(img)

# draw markers
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
        draw.text((ix+10, iy-10), p["field"], fill="red")

st.image(img, use_container_width=True)


#########################################################
#   CLICK-TO-MOVE (Drag-like placement)
#########################################################

st.header("✋ Μετακίνηση Πεδίου με Κλικ")

selected_field = None
if pg_str in current_placements:
    flds = [p["field"] for p in current_placements[pg_str]]
    selected_field = st.selectbox("Πεδίο:", flds)

st.write("👉 Κάνε κλικ στην παραπάνω εικόνα για να μετακινήσεις το πεδίο.")

# Inject JS to capture clicks
click_js = """
<script>
document.addEventListener('click', function(e) {
    const img = Array.from(document.getElementsByTagName('img')).find(i => i.alt === "");
    if (!img) return;
    const rect = img.getBoundingClientRect();
    if (e.clientX < rect.left || e.clientX > rect.right) return;
    if (e.clientY < rect.top  || e.clientY > rect.bottom) return;

    const x = (e.clientX - rect.left) * (img.naturalWidth / img.width);
    const y = (e.clientY - rect.top)  * (img.naturalHeight / img.height);

    window.parent.postMessage({type: "pdf_click", x: x, y: y}, "*");
});
</script>
"""
st.components.v1.html(click_js, height=0)

msg = st.experimental_get_query_params()
if "pdf_click_x" in msg:
    cx = float(msg["pdf_click_x"][0])
    cy = float(msg["pdf_click_y"][0])

    st.success(f"Λήφθηκε κλικ: {cx:.1f}, {cy:.1f}")

    if selected_field:
        mb = PdfReader(template_path).pages[page_prev].MediaBox
        pdf_w = float(mb[2]) - float(mb[0])
        pdf_h = float(mb[3]) - float(mb[1])

        img_w, img_h = img.size
        newx, newy = click_to_pdf_coords(cx, cy, img_w, img_h, pdf_w, pdf_h)

        for p in current_placements[pg_str]:
            if p["field"] == selected_field:
                p["x"] = newx
                p["y"] = newy

        placements_all[selected_template] = current_placements
        save_placements(placements_all)
        st.success("➡️ Μετακινήθηκε & Αποθηκεύτηκε")


#########################################################
#             SUBMIT & GENERATE PDF
#########################################################

st.header("📄 Δημιουργία Συμπληρωμένου PDF")

if st.button("✔ Υποβολή & Παραγωγή PDF"):
    meta = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    record = responses.copy()
    record.update(meta)

    if not os.path.exists(RESPONSES_CSV):
        pd.DataFrame([record]).to_csv(RESPONSES_CSV, index=False)
    else:
        old = pd.read_csv(RESPONSES_CSV)
        pd.concat([old, pd.DataFrame([record])], ignore_index=True)\
            .to_csv(RESPONSES_CSV, index=False)

    out_path = create_overlay_and_merge(
        responses, current_placements, signatures,
        template_path,
        out_name=f"filled_{selected_template}"
    )

    with open(out_path, "rb") as f:
        st.download_button("⬇️ Κατέβασμα PDF", f, file_name=f"filled.pdf", mime="application/pdf")

    st.success("🎉 Το PDF δημιουργήθηκε!")

