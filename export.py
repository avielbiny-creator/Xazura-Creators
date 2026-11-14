"""Выгрузка выплат, зачислений и обменов за период в Excel."""
from datetime import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import db
from config import METHODS

HEADER_FILL = PatternFill("solid", fgColor="DCE6FF")


def _dt(ts: int | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else ""


def _creator(row) -> str:
    return (row["full_name"] or "") + (f" (@{row['username']})" if row["username"] else "")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], total: list | None = None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
    for row in rows:
        ws.append(row)
    if total:
        ws.append(total)
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for i, column in enumerate(ws.columns, 1):
        width = max(len(str(c.value or "")) for c in column)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    ws.freeze_panes = "A2"


async def build(start: int, end: int) -> bytes:
    data = await db.export_rows(start, end)
    wb = Workbook()
    wb.remove(wb.active)

    videos = data["videos"]
    _sheet(
        wb, "Выплаты за видео",
        ["Заявка", "Выплачено", "Креатор", "ID", "Просмотры", "Сумма ₽", "Способ", "Реквизиты", "Комментарий", "Админ"],
        [[v["id"], _dt(v["paid_at"]), _creator(v), v["uid"], v["total_views"], v["amount_rub"],
          METHODS.get(v["method"], v["method"]), v["requisites"], v["note"] or "", v["admin_name"] or ""]
         for v in videos],
        ["Итого", "", "", "", sum(v["total_views"] for v in videos), sum(v["amount_rub"] for v in videos)],
    )

    comments = data["comments"]
    _sheet(
        wb, "Комментарии",
        ["Заявка", "Зачислено", "Креатор", "ID", "Скриншотов", "Зачислено 💬", "Админ"],
        [[c["id"], _dt(c["decided_at"]), _creator(c), c["uid"], c["count"], c["credited"], c["admin_name"] or ""]
         for c in comments],
        ["Итого", "", "", "", sum(c["count"] for c in comments), sum(c["credited"] for c in comments)],
    )

    exchanges = data["exchanges"]
    _sheet(
        wb, "Выданные аккаунты",
        ["Обмен", "Выдан", "Креатор", "ID", "Аккаунт", "Номер", "Списано 💬", "Закупка ₽", "Кто выдал"],
        [[e["id"], _dt(e["decided_at"]), _creator(e), e["uid"], e["offer_title"], e["phone"] or "", e["cost"],
          round(e["buy_price"] or 0, 2), e["admin_name"] or ""]
         for e in exchanges],
        ["Итого", "", "", "", len(exchanges), "", sum(e["cost"] for e in exchanges),
         round(sum(e["buy_price"] or 0 for e in exchanges), 2)],
    )

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
