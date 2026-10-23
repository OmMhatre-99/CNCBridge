"""
The PDF output: a printable report of one machine file, made with the standard library only.

A4 landscape. The fonts are Helvetica and Helvetica-Bold, which every PDF reader has built in, so nothing is
embedded; their character set is the Windows code page 1252, and a character outside it is printed as '?'.
The report has the file, time, station and typed values at the top, then one section per selection: a single
value as 'name: value', a table with its column names repeated on every page. A table too wide for the page is
printed in groups of columns, one after the other; a cell too long for its column is cut short with '...'.
"""

PAGE_W, PAGE_H = 842, 595          # A4 landscape, in points
MARGIN, TOP, BOTTOM = 36, 40, 44
USABLE = PAGE_W - 2 * MARGIN
MIN_COLUMN = 64                    # narrower columns would cut nearly every cell
ROW, SIZE, HEAD_SIZE, TITLE_SIZE = 13, 8, 8, 11
CHAR = 0.52                        # average width of a Helvetica character, in em: a safe estimate for cutting


def _text(value):
    """Text as the code page the built-in fonts use; what it cannot hold becomes '?'."""
    value = str("" if value is None else value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return value.encode("cp1252", "replace").decode("cp1252")


def _fit(value, width, size):
    value = _text(value)
    room = max(1, int(width / (CHAR * size)))
    return value if len(value) <= room else value[:max(1, room - 1)] + "…"


def _widths(columns, rows):
    """Each column as wide as its longest text needs (within limits), scaled to fill the page."""
    need = []
    for i, name in enumerate(columns):
        longest = max([len(_text(name))] + [len(_text(r[i])) if i < len(r) else 0 for r in rows[:200]])
        need.append(min(max(longest * CHAR * SIZE + 8, MIN_COLUMN), 260))
    scale = USABLE / sum(need) if sum(need) > USABLE or len(columns) > 1 else 1
    return [w * scale for w in need]


def _groups(columns, rows):
    """The columns in groups that fit across the page, each with its widths."""
    groups, start = [], 0
    while start < len(columns):
        end, used = start, 0
        for i in range(start, len(columns)):
            longest = max([len(_text(columns[i]))] + [len(_text(r[i])) if i < len(r) else 0 for r in rows[:200]])
            w = min(max(longest * CHAR * SIZE + 8, MIN_COLUMN), 260)
            if end > start and used + w > USABLE:
                break
            used += w
            end = i + 1
        cols = list(range(start, end))
        groups.append((cols, _widths([columns[i] for i in cols], [[r[i] if i < len(r) else "" for i in cols]
                                                                   for r in rows])))
        start = end
    return groups


class _Pages:
    def __init__(self, footer):
        self.pages, self.ops, self.y, self.footer = [], [], PAGE_H - TOP, footer

    def new_page(self):
        if self.ops:
            self.pages.append(self.ops)
        self.ops, self.y = [], PAGE_H - TOP

    def room(self, height):
        """True when it fits on this page; otherwise a new page is started first and False is returned."""
        if self.y - height < BOTTOM:
            self.new_page()
            return False
        return True

    def text(self, value, x, size=SIZE, bold=False, grey=False):
        colour = "0.42 0.45 0.48 rg " if grey else "0 0 0 rg "
        data = _text(value).encode("cp1252").hex().upper()
        self.ops.append(f"BT {colour}/{'F2' if bold else 'F1'} {size} Tf {x:.2f} {self.y - size:.2f} Td <{data}> Tj ET")

    def line(self, x1, x2, y, grey=0.75, width=0.6):
        self.ops.append(f"{grey} G {width} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def shade(self, x, y, w, h, grey=0.94):
        self.ops.append(f"{grey} g {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def finish(self):
        if self.ops or not self.pages:
            self.pages.append(self.ops)
        return self.pages


def _table(pdf, title, columns, rows):
    """A table under its title; the column names again at the top of each new page."""
    groups = _groups(columns, rows) if columns else [(list(range(max((len(r) for r in rows), default=0))), None)]
    for n, (cols, widths) in enumerate(groups):
        if widths is None:
            widths = _widths([""] * len(cols), rows) if cols else []
        heading = title + (f"  (columns {cols[0] + 1}-{cols[-1] + 1} of {len(columns)})" if len(groups) > 1 else "")

        def head(more=""):
            pdf.room(TITLE_SIZE + 6 + (ROW + 4 if columns else 0) + ROW)
            pdf.text(heading + more, MARGIN, TITLE_SIZE, bold=True)
            pdf.y -= TITLE_SIZE + 6
            if columns:
                pdf.shade(MARGIN, pdf.y - ROW, sum(widths), ROW + 1)
                x = MARGIN
                for i, w in zip(cols, widths):
                    pdf.text(_fit(columns[i], w - 6, HEAD_SIZE), x + 3, HEAD_SIZE, bold=True)
                    x += w
                pdf.y -= ROW + 2
                pdf.line(MARGIN, MARGIN + sum(widths), pdf.y + 1)
                pdf.y -= 2

        head()
        if not rows:
            pdf.text("(no rows in this file)", MARGIN, SIZE, grey=True)
            pdf.y -= ROW
        for row in rows:
            if not pdf.room(ROW):
                head("  (continued)")
            x = MARGIN
            for i, w in zip(cols, widths):
                pdf.text(_fit(row[i] if i < len(row) else "", w - 6, SIZE), x + 3)
                x += w
            pdf.y -= ROW
        pdf.y -= 10


def render_pdf(meta, blocks, producer="CNCBridge"):
    """The report as the bytes of a PDF file. meta and blocks as for output.render."""
    footer = f"{meta.get('station') or ''}   {meta.get('source_file', '')}".strip()
    pdf = _Pages(footer)
    pdf.text(meta.get("source_file", ""), MARGIN, 14, bold=True)
    pdf.y -= 22
    facts = [("Processed at", meta.get("processed_at", "")), ("Station", meta.get("station", ""))]
    facts += list((meta.get("manual_inputs") or {}).items())
    for name, value in facts:
        pdf.text(f"{name}:", MARGIN, 9, grey=True)
        pdf.text(value, MARGIN + 110, 9)
        pdf.y -= 13
    pdf.y -= 4
    pdf.line(MARGIN, PAGE_W - MARGIN, pdf.y, grey=0.6, width=0.8)
    pdf.y -= 14

    for block in blocks:
        where = block.get("cells") or block.get("range", "")
        title = f"{block['name']}   [{where}]" if where and where != block["name"] else block["name"]
        if "value" in block:
            pdf.room(ROW + 4)
            pdf.text(f"{block['name']}:", MARGIN, 9, bold=True)
            pdf.text(block["value"], MARGIN + 160, 9)
            pdf.y -= ROW + 4
            continue
        _table(pdf, title, block.get("columns") or [], block.get("rows") or [])

    return _assemble(pdf.finish(), footer, meta.get("source_file", ""), producer)


def _assemble(pages, footer, title, producer):
    objects = [b""]

    def add(data):
        objects.append(data if isinstance(data, bytes) else data.encode("latin-1"))
        return len(objects) - 1

    def literal(value):
        value = _text(value).encode("cp1252")
        return b"(" + value.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"

    catalog, tree = add(""), add("")
    regular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    kids = []
    for number, ops in enumerate(pages, 1):
        bottom = _Pages(footer)
        bottom.y = 26 + 7
        bottom.text(footer, MARGIN, 7, grey=True)
        bottom.text(f"Page {number} of {len(pages)}", PAGE_W - MARGIN - 60, 7, grey=True)
        stream = "\n".join(ops + bottom.ops).encode("latin-1")
        content = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        kids.append(add(f"<< /Type /Page /Parent {tree} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                        f"/Resources << /Font << /F1 {regular} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"))
    objects[tree] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>".encode()
    objects[catalog] = f"<< /Type /Catalog /Pages {tree} 0 R >>".encode()
    info = add(b"<< /Title " + literal(title) + b" /Producer " + literal(producer) + b" >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, data in enumerate(objects[1:], 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + data + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects)}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (f"trailer\n<< /Size {len(objects)} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
            .encode())
    return bytes(out)


def page_count(data):
    """How many pages a PDF made here has (for the preview)."""
    return data.count(b"/Type /Page /Parent")
