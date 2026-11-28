import zlib
from datetime import datetime


PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89
MARGIN_X = 34.0
CONTENT_WIDTH = PAGE_WIDTH - (MARGIN_X * 2)

# Status groups for accounting summary
CANCELLED_STATUSES = {'CANCELLED', 'RETURNED', 'DELIVERY_FAILED', 'REFUNDED'}
ACTIVE_STATUSES = {
    'PENDING', 'CONFIRMED', 'COURIER_CREATED',
    'PICKED_UP', 'IN_TRANSIT', 'OUT_FOR_DELIVERY',
    'ON_HOLD', 'RETURN_REQUESTED',
}


def _safe_ascii(text, max_len=None):
    """Sanitize text to clean WinAnsi/ASCII-safe string for Type1 Helvetica fonts."""
    if text is None:
        return ""
    s = str(text)
    replacements = {
        "\u09f3": "BDT ",   # Taka symbol ৳
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "*",
        "\u00a0": " ",
        "\r": " ",
        "\n": " ",
    }
    for src, dst in replacements.items():
        s = s.replace(src, dst)

    # Keep printable ASCII (0x20..0x7E) cleanly; collapse multiple spaces
    cleaned_chars = []
    for ch in s:
        code = ord(ch)
        if 32 <= code <= 126:
            cleaned_chars.append(ch)
        elif 160 <= code <= 255:
            cleaned_chars.append(ch)
        else:
            cleaned_chars.append(" ")
    cleaned = " ".join("".join(cleaned_chars).split())
    if max_len and len(cleaned) > max_len:
        if max_len > 3:
            cleaned = cleaned[: max_len - 3].rstrip() + "..."
        else:
            cleaned = cleaned[:max_len]
    return cleaned


def _pdf_escape(text):
    """Escape backslashes and parentheses inside PDF string literals."""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _fmt_money(amount):
    try:
        val = int(round(float(amount or 0)))
    except (TypeError, ValueError):
        val = 0
    return f"BDT {val:,}"


def _rgb_fill(r, g, b):
    return f"{r:.3f} {g:.3f} {b:.3f} rg"


def _rgb_stroke(r, g, b):
    return f"{r:.3f} {g:.3f} {b:.3f} RG"


def _draw_rect(x, y, w, h, fill_rgb=None, stroke_rgb=None, line_width=0.6):
    cmds = []
    if fill_rgb:
        cmds.append(_rgb_fill(*fill_rgb))
    if stroke_rgb:
        cmds.append(_rgb_stroke(*stroke_rgb))
        cmds.append(f"{line_width:.2f} w")
    cmds.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re")
    if fill_rgb and stroke_rgb:
        cmds.append("B")
    elif fill_rgb:
        cmds.append("f")
    else:
        cmds.append("S")
    return "\n".join(cmds)


def _draw_line(x1, y1, x2, y2, stroke_rgb=(0.85, 0.88, 0.90), line_width=0.5):
    return (
        f"{_rgb_stroke(*stroke_rgb)}\n"
        f"{line_width:.2f} w\n"
        f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"
    )


def _draw_text(x, y, text, font="F1", size=9.0, rgb=(0.10, 0.12, 0.14)):
    safe = _pdf_escape(_safe_ascii(text))
    return (
        f"BT\n"
        f"/{font} {size:.2f} Tf\n"
        f"{_rgb_fill(*rgb)}\n"
        f"1 0 0 1 {x:.2f} {y:.2f} Tm\n"
        f"({safe}) Tj\n"
        f"ET"
    )


def _draw_text_right(x_right, y, text, font="F1", size=9.0, rgb=(0.10, 0.12, 0.14)):
    safe = _safe_ascii(text)
    # Helvetica average character width approximation (~0.52 * size; digits/caps slightly wider)
    approx_w = 0.0
    for ch in safe:
        if ch in "il.,':;!| ":
            approx_w += size * 0.28
        elif ch in "MW@#%&":
            approx_w += size * 0.78
        elif ch.isupper():
            approx_w += size * 0.62
        else:
            approx_w += size * 0.52
    return _draw_text(max(MARGIN_X, x_right - approx_w), y, safe, font=font, size=size, rgb=rgb)


def generate_order_statement_pdf(
    orders,
    period_label="Weekly Statement",
    start_date_str="",
    end_date_str="",
    status_filter="ALL",
    shop_name="AmarDokan",
    shop_hotline="",
    shop_email="",
    generated_at_str="",
):
    """
    Build a valid multi-page A4 PDF (version 1.4) order statement from live Order rows.
    Uses standard Python libraries only so no external PDF dependency is required.
    """
    if not generated_at_str:
        generated_at_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Compute financial & order KPI summary
    total_orders = len(orders)
    delivered_count = 0
    active_count = 0
    cancelled_count = 0
    valid_revenue = 0
    all_gross_total = 0
    paid_revenue = 0
    pending_cod_total = 0
    total_delivery_fee = 0
    total_discount = 0

    for o in orders:
        st = (getattr(o, "status", "") or "PENDING").upper()
        pay_st = (getattr(o, "paymentStatus", "") or "UNPAID").upper()
        total_amt = int(getattr(o, "total", 0) or 0)
        fee_amt = int(getattr(o, "deliveryFee", 0) or 0)
        disc_amt = int(getattr(o, "discount", 0) or 0)

        all_gross_total += total_amt
        total_delivery_fee += fee_amt
        total_discount += disc_amt

        if st == "DELIVERED":
            delivered_count += 1
        elif st in CANCELLED_STATUSES:
            cancelled_count += 1
        else:
            active_count += 1

        if st not in CANCELLED_STATUSES:
            net_amt = max(0, total_amt - fee_amt)
            valid_revenue += net_amt
            if pay_st == "PAID" or st == "DELIVERED":
                paid_revenue += net_amt
            else:
                pending_cod_total += net_amt

    # Table column definition (total width == 527.28)
    # X positions inside MARGIN_X (34) .. (561.28)
    cols = [
        {"label": "Order ID", "x": 38.0, "w": 64.0},
        {"label": "Date", "x": 104.0, "w": 72.0},
        {"label": "Customer / Phone", "x": 178.0, "w": 116.0},
        {"label": "District", "x": 296.0, "w": 56.0},
        {"label": "Items", "x": 354.0, "w": 32.0},
        {"label": "Payment", "x": 388.0, "w": 58.0},
        {"label": "Status", "x": 448.0, "w": 56.0},
        {"label": "Total (BDT)", "x": 557.0, "w": 52.0, "right": True},
    ]

    pages_cmds = []

    def start_page(is_first_page=False):
        cmds = []
        # Top compact header on every page, fuller hero on page 1
        if is_first_page:
            # Hero banner box
            banner_h = 68.0
            banner_y = PAGE_HEIGHT - 30.0 - banner_h
            cmds.append(_draw_rect(MARGIN_X, banner_y, CONTENT_WIDTH, banner_h, fill_rgb=(0.008, 0.192, 0.180)))
            cmds.append(_draw_text(MARGIN_X + 14, banner_y + 44, _safe_ascii(shop_name, 40) or "AmarDokan", font="F2", size=16.0, rgb=(1.0, 1.0, 1.0)))
            contact_parts = []
            if shop_hotline:
                contact_parts.append(f"Hotline: {_safe_ascii(shop_hotline, 24)}")
            if shop_email:
                contact_parts.append(f"Email: {_safe_ascii(shop_email, 36)}")
            contact_line = " | ".join(contact_parts) if contact_parts else "Official Merchant Statement"
            cmds.append(_draw_text(MARGIN_X + 14, banner_y + 27, contact_line, font="F1", size=8.5, rgb=(0.82, 0.94, 0.90)))
            cmds.append(_draw_text(MARGIN_X + 14, banner_y + 12, f"Generated: {generated_at_str} (BDT)", font="F1", size=8.0, rgb=(0.68, 0.86, 0.80)))

            cmds.append(_draw_text_right(MARGIN_X + CONTENT_WIDTH - 14, banner_y + 44, "ORDER STATEMENT", font="F2", size=14.0, rgb=(0.98, 0.78, 0.24)))
            cmds.append(_draw_text_right(MARGIN_X + CONTENT_WIDTH - 14, banner_y + 27, _safe_ascii(period_label, 38), font="F2", size=9.5, rgb=(1.0, 1.0, 1.0)))
            date_win = f"{start_date_str} to {end_date_str}" if (start_date_str and end_date_str) else "All Dates"
            cmds.append(_draw_text_right(MARGIN_X + CONTENT_WIDTH - 14, banner_y + 12, f"Period: {date_win} | Status: {status_filter}", font="F1", size=8.0, rgb=(0.82, 0.94, 0.90)))

            # KPI Summary Box (2 rows x 4 columns)
            kpi_h = 74.0
            kpi_y = banner_y - 10.0 - kpi_h
            cmds.append(_draw_rect(MARGIN_X, kpi_y, CONTENT_WIDTH, kpi_h, fill_rgb=(0.969, 0.980, 0.984), stroke_rgb=(0.82, 0.87, 0.89), line_width=0.7))
            cmds.append(_draw_line(MARGIN_X, kpi_y + 37.0, MARGIN_X + CONTENT_WIDTH, kpi_y + 37.0, stroke_rgb=(0.86, 0.90, 0.91), line_width=0.5))

            col_w = CONTENT_WIDTH / 4.0
            for idx_sep in (1, 2, 3):
                sx = MARGIN_X + col_w * idx_sep
                cmds.append(_draw_line(sx, kpi_y, sx, kpi_y + kpi_h, stroke_rgb=(0.86, 0.90, 0.91), line_width=0.5))

            kpis_row1 = [
                ("TOTAL ORDERS", f"{total_orders}"),
                ("DELIVERED", f"{delivered_count}"),
                ("PENDING / ACTIVE", f"{active_count}"),
                ("CANCELLED / RETURNED", f"{cancelled_count}"),
            ]
            kpis_row2 = [
                ("NET ORDER REVENUE", _fmt_money(valid_revenue)),
                ("COLLECTED / PAID", _fmt_money(paid_revenue)),
                ("PENDING COD DUE", _fmt_money(pending_cod_total)),
                ("DELIVERY FEES TOTAL", _fmt_money(total_delivery_fee)),
            ]
            for idx_k, (lbl, val) in enumerate(kpis_row1):
                kx = MARGIN_X + idx_k * col_w + 10.0
                cmds.append(_draw_text(kx, kpi_y + 59.0, lbl, font="F2", size=7.2, rgb=(0.40, 0.46, 0.50)))
                cmds.append(_draw_text(kx, kpi_y + 44.0, val, font="F2", size=11.5, rgb=(0.05, 0.14, 0.13)))

            for idx_k, (lbl, val) in enumerate(kpis_row2):
                kx = MARGIN_X + idx_k * col_w + 10.0
                cmds.append(_draw_text(kx, kpi_y + 22.0, lbl, font="F2", size=7.2, rgb=(0.40, 0.46, 0.50)))
                val_rgb = (0.02, 0.42, 0.28) if idx_k in (0, 1) else (0.12, 0.16, 0.20)
                cmds.append(_draw_text(kx, kpi_y + 7.5, val, font="F2", size=10.5, rgb=val_rgb))

            table_top_y = kpi_y - 12.0
        else:
            # Continuation page compact header
            hdr_y = PAGE_HEIGHT - 44.0
            cmds.append(_draw_text(MARGIN_X, hdr_y, f"{_safe_ascii(shop_name, 36)} - Order Statement ({_safe_ascii(period_label, 30)})", font="F2", size=10.0, rgb=(0.008, 0.192, 0.180)))
            date_win = f"{start_date_str} to {end_date_str}" if (start_date_str and end_date_str) else "All Dates"
            cmds.append(_draw_text_right(MARGIN_X + CONTENT_WIDTH, hdr_y, f"Period: {date_win}", font="F1", size=8.5, rgb=(0.42, 0.47, 0.52)))
            table_top_y = hdr_y - 10.0

        # Draw Table Header Row
        hdr_h = 22.0
        hdr_row_y = table_top_y - hdr_h
        cmds.append(_draw_rect(MARGIN_X, hdr_row_y, CONTENT_WIDTH, hdr_h, fill_rgb=(0.91, 0.94, 0.95), stroke_rgb=(0.78, 0.83, 0.86), line_width=0.6))
        for col in cols:
            if col.get("right"):
                cmds.append(_draw_text_right(col["x"], hdr_row_y + 7.5, col["label"], font="F2", size=7.8, rgb=(0.12, 0.18, 0.22)))
            else:
                cmds.append(_draw_text(col["x"], hdr_row_y + 7.5, col["label"], font="F2", size=7.8, rgb=(0.12, 0.18, 0.22)))

        return cmds, hdr_row_y

    current_cmds, cursor_y = start_page(is_first_page=True)
    bottom_limit = 54.0
    row_h = 26.0

    if not orders:
        empty_h = 46.0
        empty_y = cursor_y - empty_h
        current_cmds.append(_draw_rect(MARGIN_X, empty_y, CONTENT_WIDTH, empty_h, fill_rgb=(0.99, 0.99, 0.99), stroke_rgb=(0.86, 0.89, 0.91), line_width=0.5))
        current_cmds.append(_draw_text(MARGIN_X + 135.0, empty_y + 19.0, "No orders found for the selected statement period and filters.", font="F1", size=9.5, rgb=(0.45, 0.50, 0.55)))
        cursor_y = empty_y
    else:
        for idx_row, order in enumerate(orders):
            if cursor_y - row_h < bottom_limit:
                pages_cmds.append(current_cmds)
                current_cmds, cursor_y = start_page(is_first_page=False)

            row_y = cursor_y - row_h
            bg = (0.985, 0.990, 0.992) if (idx_row % 2 == 1) else (1.0, 1.0, 1.0)
            current_cmds.append(_draw_rect(MARGIN_X, row_y, CONTENT_WIDTH, row_h, fill_rgb=bg))
            current_cmds.append(_draw_line(MARGIN_X, row_y, MARGIN_X + CONTENT_WIDTH, row_y, stroke_rgb=(0.90, 0.92, 0.94), line_width=0.45))

            addr = getattr(order, "address", None)
            if not isinstance(addr, dict):
                addr = {}
            items_list = getattr(order, "items", None)
            if not isinstance(items_list, list):
                items_list = []

            oid = _safe_ascii(getattr(order, "id", ""), 14)
            created_raw = _safe_ascii(getattr(order, "createdAt", ""), 19)
            date_part = created_raw[:10] if len(created_raw) >= 10 else created_raw
            time_part = created_raw[11:16] if len(created_raw) >= 16 else ""

            cust_name = _safe_ascii(addr.get("customerName") or "Guest Customer", 22) or "Customer"
            cust_phone = _safe_ascii(addr.get("phone") or "", 16)
            district = _safe_ascii(addr.get("district") or "Dhaka", 12) or "Dhaka"

            qty_total = 0
            for it in items_list:
                if isinstance(it, dict):
                    try:
                        qty_total += int(it.get("quantity", 1) or 1)
                    except (TypeError, ValueError):
                        qty_total += 1
            items_str = f"{qty_total} pcs"

            pay_method = _safe_ascii(getattr(order, "paymentMethod", "COD"), 10)
            pay_status = _safe_ascii(getattr(order, "paymentStatus", "UNPAID"), 10)

            st_val = _safe_ascii(getattr(order, "status", "PENDING"), 16)
            st_upper = st_val.upper()
            if st_upper == "DELIVERED":
                st_rgb = (0.02, 0.45, 0.29)
            elif st_upper in CANCELLED_STATUSES:
                st_rgb = (0.76, 0.14, 0.22)
            else:
                st_rgb = (0.70, 0.42, 0.02)

            tot_str = _fmt_money(getattr(order, "total", 0))

            top_text_y = row_y + 14.5
            sub_text_y = row_y + 5.2

            current_cmds.append(_draw_text(cols[0]["x"], top_text_y, oid, font="F2", size=7.8, rgb=(0.08, 0.10, 0.12)))
            current_cmds.append(_draw_text(cols[1]["x"], top_text_y, date_part, font="F1", size=7.6, rgb=(0.18, 0.22, 0.25)))
            if time_part:
                current_cmds.append(_draw_text(cols[1]["x"], sub_text_y, time_part, font="F1", size=6.8, rgb=(0.45, 0.50, 0.55)))

            current_cmds.append(_draw_text(cols[2]["x"], top_text_y, cust_name, font="F2", size=7.6, rgb=(0.10, 0.12, 0.14)))
            if cust_phone:
                current_cmds.append(_draw_text(cols[2]["x"], sub_text_y, cust_phone, font="F1", size=6.8, rgb=(0.42, 0.47, 0.52)))

            current_cmds.append(_draw_text(cols[3]["x"], top_text_y, district, font="F1", size=7.5, rgb=(0.20, 0.24, 0.28)))
            current_cmds.append(_draw_text(cols[4]["x"], top_text_y, items_str, font="F1", size=7.5, rgb=(0.20, 0.24, 0.28)))

            current_cmds.append(_draw_text(cols[5]["x"], top_text_y, pay_method, font="F2", size=7.2, rgb=(0.15, 0.18, 0.22)))
            current_cmds.append(_draw_text(cols[5]["x"], sub_text_y, pay_status, font="F1", size=6.6, rgb=(0.42, 0.47, 0.52)))

            current_cmds.append(_draw_text(cols[6]["x"], top_text_y, st_val, font="F2", size=7.0, rgb=st_rgb))
            current_cmds.append(_draw_text_right(cols[7]["x"], top_text_y, tot_str, font="F2", size=8.0, rgb=(0.05, 0.14, 0.13)))

            cursor_y = row_y

        # Summary Totals Footer Row at end of table
        totals_h = 26.0
        if cursor_y - totals_h < bottom_limit:
            pages_cmds.append(current_cmds)
            current_cmds, cursor_y = start_page(is_first_page=False)
        tot_y = cursor_y - totals_h
        current_cmds.append(_draw_rect(MARGIN_X, tot_y, CONTENT_WIDTH, totals_h, fill_rgb=(0.93, 0.96, 0.95), stroke_rgb=(0.74, 0.82, 0.80), line_width=0.7))
        current_cmds.append(_draw_text(MARGIN_X + 8.0, tot_y + 9.0, f"TOTALS ({total_orders} orders) | Discounts: {_fmt_money(total_discount)} | Delivery Fees: {_fmt_money(total_delivery_fee)}", font="F2", size=7.8, rgb=(0.06, 0.20, 0.16)))
        current_cmds.append(_draw_text_right(cols[7]["x"], tot_y + 9.0, f"All: {_fmt_money(all_gross_total)}", font="F2", size=8.2, rgb=(0.008, 0.192, 0.180)))

    pages_cmds.append(current_cmds)

    total_pages = len(pages_cmds)
    page_streams = []
    for p_idx, p_cmds in enumerate(pages_cmds, start=1):
        # Page footer
        p_cmds.append(_draw_line(MARGIN_X, 38.0, MARGIN_X + CONTENT_WIDTH, 38.0, stroke_rgb=(0.86, 0.89, 0.91), line_width=0.5))
        p_cmds.append(_draw_text(MARGIN_X, 26.0, f"{_safe_ascii(shop_name, 36)} - Dynamic Order Statement (Live Database)", font="F1", size=7.5, rgb=(0.48, 0.52, 0.56)))
        p_cmds.append(_draw_text_right(MARGIN_X + CONTENT_WIDTH, 26.0, f"Page {p_idx} of {total_pages}", font="F2", size=7.5, rgb=(0.35, 0.40, 0.45)))

        raw_stream = "\n".join(p_cmds).encode("latin-1", errors="replace")
        page_streams.append(zlib.compress(raw_stream))

    # Assemble PDF 1.4 binary with exact byte offsets
    objects = []
    # Obj 1: Catalog
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")

    # Page object IDs: 5, 7, 9, ...
    page_obj_ids = [5 + i * 2 for i in range(total_pages)]
    kids_str = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    # Obj 2: Pages
    objects.append(f"<< /Type /Pages /Kids [{kids_str}] /Count {total_pages} >>".encode("ascii"))
    # Obj 3: Helvetica Regular
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    # Obj 4: Helvetica Bold
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    for i, comp_stream in enumerate(page_streams):
        content_obj_id = 6 + i * 2
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH:.2f} {PAGE_HEIGHT:.2f}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {content_obj_id} 0 R >>"
        ).encode("ascii")
        objects.append(page_dict)

        stream_obj = (
            f"<< /Length {len(comp_stream)} /Filter /FlateDecode >>\nstream\n".encode("ascii")
            + comp_stream
            + b"\nendstream"
        )
        objects.append(stream_obj)

    buf = bytearray()
    buf.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx_obj, obj_bytes in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf.extend(f"{idx_obj} 0 obj\n".encode("ascii"))
        buf.extend(obj_bytes)
        buf.extend(b"\nendobj\n")

    xref_offset = len(buf)
    total_objs = len(objects) + 1
    buf.extend(f"xref\n0 {total_objs}\n".encode("ascii"))
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    buf.extend(
        (
            f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(buf)

