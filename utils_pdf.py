
def _sanitize_for_pdf(s: str) -> str:
    replacements = {
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "–": "-", "—": "-", "−": "-",
        "•": "-", "·": "-", "►": ">", "→": "->", "←": "<-", "✓": "[ok]",
        "…": "...", " ": " ", " ": " ", " ": " ", " ": " ",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    s = ''.join(ch if ord(ch) < 128 else '?' for ch in s)
    return s

def markdown_to_basic_pdf_bytes(md_text: str) -> bytes:
    import textwrap
    plain = md_text.replace("\r\n", "\n")
    for ch in ["`"]:
        plain = plain.replace(ch, "")
    lines_in = [line.rstrip() for line in plain.split("\n")]

    width, height = 612, 792
    margin = 54
    font_size = 12
    leading = 16
    max_chars = 90
    usable_lines = int((height - 2*margin) // leading) - 1
    if usable_lines < 10:
        usable_lines = 10

    lines = []
    for raw in lines_in:
        stripped = raw.lstrip("# ").lstrip("* ").lstrip("- ").lstrip("> ")
        stripped = _sanitize_for_pdf(stripped)
        if not stripped:
            lines.append("")
            continue
        wrapped = textwrap.wrap(stripped, width=max_chars) or [""]
        lines.extend(wrapped)

    pages = [lines[i:i+usable_lines] for i in range(0, len(lines), usable_lines)] or [[]]

    def make_page(page_lines):
        start_x = margin
        start_y = height - margin
        content_lines = [
            "BT",
            f"/F1 {font_size} Tf",
            f"{start_x} {start_y} Td",
            f"{leading} TL",
        ]
        first = True
        for line in page_lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if not first:
                content_lines.append("T*")
            first = False
            content_lines.append(f"({esc}) Tj")
        content_lines.append("ET")
        return "\n".join(content_lines).encode("ascii", "ignore")

    objects = []
    kids = []
    obj_id = 1

    def add_object(obj_bytes):
        nonlocal obj_id
        objects.append(obj_bytes if isinstance(obj_bytes, (bytes, bytearray)) else obj_bytes.encode("utf-8"))
        obj_id += 1
        return obj_id-1

    catalog_id = add_object(b"<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add_object(b"<< /Type /Pages /Kids [] /Count 0 >>")
    font_id = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Name /F1 >>")

    page_ids = []
    for pg in pages:
        stream = make_page(pg)
        content_obj = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream\n"
        content_id = add_object(content_obj)
        page_dict = f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        page_id = add_object(page_dict)
        page_ids.append(page_id)

    offsets = []
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode("ascii") + obj + b"endobj\n"
    kids_str = " ".join(f"{pid} 0 R" for pid in page_ids)
    pages_obj = f"<< /Type /Pages /Kids [{kids_str}] /Count {len(page_ids)} >>".encode("ascii")
    objects2 = [b"<< /Type /Catalog /Pages 2 0 R >>", pages_obj] + objects[2:]
    offsets = []
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for i, obj in enumerate(objects2, start=1):
        offsets.append(len(pdf))
        pdf += f"{i} 0 obj\n".encode("ascii") + obj + b"endobj\n"
    xref_start = len(pdf)
    pdf += f"xref\n0 {len(objects2)+1}\n".encode("ascii")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode("ascii")
    pdf += b"trailer\n"
    pdf += f"<< /Size {len(objects2)+1} /Root 1 0 R >>\n".encode("ascii")
    pdf += b"startxref\n"
    pdf += f"{xref_start}\n".encode("ascii")
    pdf += b"%%EOF"
    return bytes(pdf)
