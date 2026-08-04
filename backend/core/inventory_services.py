from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, Branch, LeadStockUsage, CatalogComposition, CatalogHistory, CatalogItem, CatalogMaterialUsage, CatalogTransfer, FloristSalaryEntry, FloristStockBalance, FloristStockIssue, FloristVolumeRate, Lead, Notification, Packaging, PackagingMovement, Reservation, StockBatch, StockMovement


def money(value):
    return Decimal(value or 0)


def discount_percent(amount, base):
    base = money(base)
    if base <= 0:
        return Decimal("0")
    return ((money(amount) / base) * Decimal("100")).quantize(Decimal("0.01"))


def catalog_snapshot(item):
    return {
        "catalog": item.name_uz,
        "catalog_kind": item.catalog_kind,
        "arrangement_type": item.arrangement_type,
        "volume": item.volume,
        "price": str(item.price),
        "florist_fee": str(item.florist_fee),
        "florist_salary_amount": str(item.florist_salary_amount),
        "decoration_florist": str(item.decoration_florist) if item.decoration_florist_id else "",
        "decoration_salary_amount": str(item.decoration_salary_amount),
        "calculated_component_price": str(item.calculated_component_price),
        "composition": [{"batch": row.stock_batch.batch_number, "flower": str(row.stock_batch.variant), "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)} for row in item.composition.select_related("stock_batch__variant__flower")],
        "materials": [{"material": row.packaging.name_uz, "type": row.packaging.packaging_type, "quantity": row.quantity} for row in item.materials.select_related("packaging")],
    }


def create_catalog_history(item, action, user=None, quantity=0, listed_unit_price=None, sold_unit_price=None, discount_reason="", note="", snapshot=None, reservation=None):
    listed = money(listed_unit_price if listed_unit_price is not None else item.price)
    sold = money(sold_unit_price if sold_unit_price is not None else listed)
    quantity = int(quantity or 0)
    discount = max((listed - sold) * Decimal(quantity), Decimal("0"))
    return CatalogHistory.objects.create(
        catalog_item=item,
        reservation=reservation,
        action=action,
        quantity=quantity,
        listed_unit_price=listed,
        sold_unit_price=sold,
        discount_amount=discount,
        discount_percent=discount_percent(discount, listed * Decimal(quantity)),
        discount_reason=discount_reason,
        note=note,
        snapshot=snapshot if snapshot is not None else catalog_snapshot(item),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )


def reservation_paid_amount(reservation):
    if not reservation:
        return Decimal("0")
    return sum((Decimal(row.amount or 0) for row in reservation.payments.all()), Decimal("0"))


def sync_reservation_payment_status(reservation):
    reservation = Reservation.objects.get(pk=reservation.pk)
    paid = reservation_paid_amount(reservation)
    estimated = Decimal(reservation.estimated_price or 0)
    if paid <= 0:
        status = "unpaid"
    elif estimated and paid >= estimated:
        status = "paid"
    else:
        status = "deposit"
    if reservation.payment_status != status:
        reservation.payment_status = status
        reservation.save(update_fields=["payment_status", "updated_at"])
    return reservation


def catalog_component_total(item):
    quantity = int(item.quantity_total or 1)
    stock_total = Decimal("0")
    for row in item.composition.select_related("stock_batch"):
        stock_total += Decimal(row.quantity_stems * quantity) * row.stock_batch.sale_price_per_stem
    material_total = Decimal("0")
    for row in item.materials.select_related("packaging"):
        material_total += Decimal(row.quantity * quantity) * row.packaging.sale_price
    florist_total = Decimal(item.florist_fee or 0) * Decimal(quantity)
    decoration_total = Decimal(item.decoration_salary_amount or 0) * Decimal(quantity)
    return stock_total + material_total + florist_total + decoration_total


def catalog_cost_breakdown(item):
    """Katalog mahsuloti tannarxini uchga ajratadi: gul, material va florist haqi."""
    quantity = int(item.quantity_total or 1)
    flower_cost = Decimal("0")
    for row in item.composition.select_related("stock_batch"):
        flower_cost += Decimal(row.quantity_stems * quantity) * row.stock_batch.cost_per_stem
    material_cost = Decimal("0")
    for row in item.materials.select_related("packaging"):
        material_cost += Decimal(row.quantity * quantity) * row.packaging.cost_price
    florist_fee_cost = Decimal(item.florist_fee or 0) * Decimal(quantity)
    decoration_fee_cost = Decimal(item.decoration_salary_amount or 0) * Decimal(quantity)
    return {
        "flower_cost": flower_cost,
        "material_cost": material_cost,
        "florist_fee_cost": florist_fee_cost + decoration_fee_cost,
        "decoration_fee_cost": decoration_fee_cost,
        "total": flower_cost + material_cost + florist_fee_cost + decoration_fee_cost,
    }


def catalog_cost_total(item):
    return catalog_cost_breakdown(item)["total"]


def apply_volume_rate(item):
    if not item.volume or not item.arrangement_type or item.florist_salary_amount:
        return item
    if not item.florist_id:
        return item
    rate = FloristVolumeRate.objects.filter(florist=item.florist, arrangement_type=item.arrangement_type, volume=item.volume, is_active=True).first()
    if rate:
        item.florist_salary_amount = rate.florist_fee
    return item


def apply_decoration_fee(item):
    if item.decoration_florist_id:
        item.decoration_salary_amount = item.decoration_florist.decoration_fee or 0
    else:
        item.decoration_salary_amount = 0
    return item


def sync_catalog_financials(item):
    item = CatalogItem.objects.select_related("decoration_florist").get(pk=item.pk)
    apply_volume_rate(item)
    apply_decoration_fee(item)
    total = catalog_component_total(item)
    cost_total = catalog_cost_total(item)
    sale_total = Decimal(item.price or 0) * Decimal(item.quantity_total or 1)
    item.calculated_cost_price = cost_total
    item.calculated_component_price = total
    item.discount_amount = max(total - sale_total, Decimal("0"))
    item.discount_percent = discount_percent(item.discount_amount, total)
    item.save(update_fields=["florist_salary_amount", "decoration_salary_amount", "calculated_cost_price", "calculated_component_price", "discount_amount", "discount_percent", "updated_at"])
    return item


def sync_catalog_florist_salary(item, user):
    item = CatalogItem.objects.select_related("florist").get(pk=item.pk)
    if not item.florist_id or not item.florist_salary_amount:
        FloristSalaryEntry.objects.filter(catalog_item=item, source__in=["catalog", "custom_catalog"]).delete()
        return None
    source = "custom_catalog" if item.catalog_kind == "custom" else "catalog"
    FloristSalaryEntry.objects.filter(catalog_item=item, source__in=["catalog", "custom_catalog"]).exclude(florist=item.florist, source=source).delete()
    amount = Decimal(item.florist_salary_amount) * Decimal(item.quantity_total or 1)
    existing = FloristSalaryEntry.objects.filter(florist=item.florist, source=source, catalog_item=item).first()
    old_amount = existing.amount if existing else None
    entry, _ = FloristSalaryEntry.objects.update_or_create(
        florist=item.florist,
        source=source,
        catalog_item=item,
        defaults={
            "amount": amount,
            "work_date": timezone.localtime(item.created_at).date() if item.created_at else timezone.localdate(),
            "note": f"{item.name_uz} uchun florist haqi",
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
        )
    if old_amount != entry.amount:
        Notification.objects.create(
            target_user=item.florist.user,
            notification_type="florist_salary",
            title_uz="Ish haqi qo‘shildi",
            title_ru="Ish haqi qo‘shildi",
            body_uz=f"{item.name_uz} uchun {entry.amount} so‘m ish haqi yozildi.",
            body_ru=f"{item.name_uz} uchun {entry.amount} so‘m ish haqi yozildi.",
            reference_type="florist_salary",
            reference_id=entry.id,
        )
    return entry


def sync_catalog_decoration_salary(item, user):
    item = CatalogItem.objects.select_related("decoration_florist").get(pk=item.pk)
    if not item.decoration_florist_id or not item.decoration_salary_amount:
        FloristSalaryEntry.objects.filter(catalog_item=item, source="decoration").delete()
        return None
    FloristSalaryEntry.objects.filter(catalog_item=item, source="decoration").exclude(florist=item.decoration_florist).delete()
    amount = Decimal(item.decoration_salary_amount) * Decimal(item.quantity_total or 1)
    entry, _ = FloristSalaryEntry.objects.update_or_create(
        florist=item.decoration_florist,
        source="decoration",
        catalog_item=item,
        defaults={
            "amount": amount,
            "work_date": timezone.localtime(item.created_at).date() if item.created_at else timezone.localdate(),
            "note": f"{item.name_uz} uchun oformleniya haqi",
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    return entry


def deduct_catalog_sale_materials(item, materials, quantity, history, user):
    rows = []
    for row in materials or []:
        packaging = row["packaging"]
        amount = int(row.get("quantity") or 1) * int(quantity or 1)
        rows.append((packaging, amount))
    if not rows:
        return []
    locked = {row.id: row for row in Packaging.objects.select_for_update().filter(id__in=[packaging.id for packaging, _ in rows])}
    shortages = [locked[packaging.id].name_uz for packaging, amount in rows if locked[packaging.id].quantity < amount]
    if shortages:
        raise ValueError("Sotuv uchun material qoldig‘i yetarli emas: " + ", ".join(shortages))
    snapshot_rows = []
    for packaging, amount in rows:
        packaging = locked[packaging.id]
        packaging.quantity -= amount
        packaging.save(update_fields=["quantity", "updated_at"])
        PackagingMovement.objects.create(
            packaging=packaging,
            movement_type="out",
            quantity=-amount,
            reference_type="catalog_sale",
            reference_id=history.id,
            reason=f"{item.name_uz} sotuviga ishlatildi",
            performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        snapshot_rows.append({"material": packaging.name_uz, "type": packaging.packaging_type, "quantity": amount})
    return snapshot_rows


def add_catalog_sale_decoration_salary(item, florist, quantity, user, sold_at=None):
    if not florist:
        return None
    amount = Decimal(florist.decoration_fee or 0) * Decimal(quantity or 1)
    if amount <= 0:
        return None
    entry, created = FloristSalaryEntry.objects.get_or_create(
        florist=florist,
        source="sale_decoration",
        catalog_item=item,
        defaults={
            "amount": amount,
            "work_date": timezone.localtime(sold_at).date() if sold_at else timezone.localdate(),
            "note": f"{item.name_uz} sotuv oformleniyasi",
            "created_by": user if getattr(user, "is_authenticated", False) else None,
        },
    )
    if not created:
        entry.amount = Decimal(entry.amount or 0) + amount
        entry.work_date = timezone.localtime(sold_at).date() if sold_at else entry.work_date
        entry.created_by = user if getattr(user, "is_authenticated", False) else entry.created_by
        entry.save(update_fields=["amount", "work_date", "created_by", "updated_at"])
    return entry


def notify_florist_catalog(item, title, body, reference_id=None):
    item = CatalogItem.objects.select_related("florist__user").filter(pk=item.pk).first()
    if not item or not item.florist_id:
        return None
    return Notification.objects.create(
        target_user=item.florist.user,
        notification_type="florist_catalog",
        title_uz=title,
        title_ru=title,
        body_uz=body,
        body_ru=body,
        reference_type="catalog_item",
        reference_id=reference_id or item.id,
    )


def ensure_catalog_stock_available(item, quantity=None):
    qty = quantity if quantity is not None else item.quantity_total
    rows = list(item.composition.select_related("stock_batch"))
    shortages = []
    for row in rows:
        needed = row.quantity_stems * qty
        if row.stock_batch.remaining_stems < needed:
            shortages.append(f"{row.stock_batch.batch_number}: kerak {needed}, bor {row.stock_batch.remaining_stems}")
    if shortages:
        raise ValueError("Katalog uchun yetarli qoldiq yo‘q: " + "; ".join(shortages))


def ensure_catalog_materials_available(item, quantity=None):
    qty = quantity if quantity is not None else item.quantity_total
    rows = list(item.materials.select_related("packaging"))
    shortages = []
    for row in rows:
        needed = row.quantity * qty
        if row.packaging.quantity < needed:
            shortages.append(f"{row.packaging.name_uz}: kerak {needed}, bor {row.packaging.quantity}")
    if shortages:
        raise ValueError("Katalog uchun yetarli material qoldig‘i yo‘q: " + "; ".join(shortages))



def stamp_created_at(instance, created_at=None):
    """auto_now_add tufayli created_at yozilmaydi. O'tib ketgan kun uchun
    yozuv yaratilgandan keyin to'g'ridan-to'g'ri yangilanadi."""
    if not created_at:
        return instance
    type(instance).objects.filter(pk=instance.pk).update(created_at=created_at)
    instance.created_at = created_at
    return instance


def florist_balance_row(florist, batch, lock=False):
    queryset = FloristStockBalance.objects.select_for_update() if lock else FloristStockBalance.objects
    row = queryset.filter(florist=florist, batch=batch).first()
    if row:
        return row
    return FloristStockBalance.objects.create(florist=florist, batch=batch, remaining_stems=0)


def issue_stock_to_florist(florist, batch, quantity_stems, reason="", user=None, created_at=None):
    """Skladdan floristga gul chiqaradi. Sklad qoldig'i kamayadi, floristda ko'payadi."""
    quantity_stems = int(quantity_stems or 0)
    if quantity_stems < 1:
        raise ValueError("Chiqariladigan gul soni 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.remaining_stems < quantity_stems:
            raise ValueError(f"{batch.batch_number} partiyasida atigi {batch.remaining_stems} dona qolgan")
        batch.remaining_stems -= quantity_stems
        batch.save(update_fields=["remaining_stems", "updated_at"])
        balance = florist_balance_row(florist, batch, lock=True)
        balance.remaining_stems += quantity_stems
        balance.save(update_fields=["remaining_stems", "updated_at"])
        issue = FloristStockIssue.objects.create(
            florist=florist, batch=batch, kind="issue", quantity_stems=quantity_stems,
            reason=reason, performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        movement = StockMovement.objects.create(
            batch=batch, movement_type="out", quantity_stems=-quantity_stems,
            quantity_bunches=Decimal(quantity_stems) / Decimal(batch.stems_per_bunch or 1),
            reference_type="florist_issue", reference_id=issue.id,
            reason=reason or f"{florist} ga chiqarildi",
            performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        stamp_created_at(issue, created_at)
        stamp_created_at(movement, created_at)
    return issue


def issue_multiple_stock_to_florist(florist, items, reason="", user=None, created_at=None):
    if not items:
        raise ValueError("Kamida bitta gul tanlash kerak")
    with transaction.atomic():
        batch_ids = [row["batch"].id for row in items]
        batches = {row.id: row for row in StockBatch.objects.select_for_update().filter(id__in=batch_ids)}
        totals = {}
        for row in items:
            batch = batches.get(row["batch"].id)
            quantity = int(row.get("quantity_stems") or 0)
            if not batch:
                raise ValueError("Partiya topilmadi")
            if quantity < 1:
                raise ValueError("Chiqariladigan gul soni 1 dan kam bo‘lmasligi kerak")
            totals[batch.id] = totals.get(batch.id, 0) + quantity
        for batch_id, quantity in totals.items():
            batch = batches[batch_id]
            if batch.remaining_stems < quantity:
                raise ValueError(f"{batch.batch_number} partiyasida atigi {batch.remaining_stems} dona qolgan")
        issues = []
        for row in items:
            batch = batches[row["batch"].id]
            quantity = int(row.get("quantity_stems") or 0)
            batch.remaining_stems -= quantity
            batch.save(update_fields=["remaining_stems", "updated_at"])
            balance = florist_balance_row(florist, batch, lock=True)
            balance.remaining_stems += quantity
            balance.save(update_fields=["remaining_stems", "updated_at"])
            issue = FloristStockIssue.objects.create(
                florist=florist, batch=batch, kind="issue", quantity_stems=quantity,
                reason=reason, performed_by=user if getattr(user, "is_authenticated", False) else None,
            )
            movement = StockMovement.objects.create(
                batch=batch, movement_type="out", quantity_stems=-quantity,
                quantity_bunches=Decimal(quantity) / Decimal(batch.stems_per_bunch or 1),
                reference_type="florist_issue", reference_id=issue.id,
                reason=reason or f"{florist} ga chiqarildi",
                performed_by=user if getattr(user, "is_authenticated", False) else None,
            )
            stamp_created_at(issue, created_at)
            stamp_created_at(movement, created_at)
            issues.append(issue)
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="florist_stock_bulk_issued", entity_type="FloristProfile", entity_id=str(florist.id),
            summary=f"{florist} ga {len(issues)} tur gul chiqarildi",
            after={"items": [{"issue": issue.id, "batch": issue.batch_id, "quantity_stems": issue.quantity_stems} for issue in issues]},
        )
    return issues


def return_stock_from_florist(florist, batch, quantity_stems, reason="", user=None, kind="return", created_at=None):
    """Floristdan gulni skladga qaytaradi yoki chiqitga chiqaradi."""
    quantity_stems = int(quantity_stems or 0)
    if quantity_stems < 1:
        raise ValueError("Qaytariladigan gul soni 1 dan kam bo‘lmasligi kerak")
    if kind not in ["return", "waste"]:
        raise ValueError("Noto‘g‘ri amal turi")
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        balance = florist_balance_row(florist, batch, lock=True)
        if balance.remaining_stems < quantity_stems:
            raise ValueError(f"{florist} qo‘lida bu guldan atigi {balance.remaining_stems} dona bor")
        balance.remaining_stems -= quantity_stems
        balance.save(update_fields=["remaining_stems", "updated_at"])
        if kind == "return":
            batch.remaining_stems += quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
        issue = FloristStockIssue.objects.create(
            florist=florist, batch=batch, kind=kind, quantity_stems=quantity_stems,
            reason=reason, performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        movement = StockMovement.objects.create(
            batch=batch,
            movement_type="in" if kind == "return" else "waste",
            quantity_stems=quantity_stems if kind == "return" else -quantity_stems,
            quantity_bunches=Decimal(quantity_stems) / Decimal(batch.stems_per_bunch or 1),
            reference_type=f"florist_{kind}", reference_id=issue.id,
            reason=reason or (f"{florist} qaytardi" if kind == "return" else f"{florist} chiqitga chiqardi"),
            performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        stamp_created_at(issue, created_at)
        stamp_created_at(movement, created_at)
    return issue


def restore_catalog_flowers(item, florist, old_batch, new_batch, quantity_stems, reason="", user=None):
    quantity_stems = int(quantity_stems or 0)
    if quantity_stems < 1:
        raise ValueError("Restavratsiya soni 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        old_batch = StockBatch.objects.select_for_update().get(pk=old_batch.pk)
        new_batch = StockBatch.objects.select_for_update().get(pk=new_batch.pk)
        old_row = CatalogComposition.objects.select_for_update().filter(catalog_item=item, stock_batch=old_batch).first()
        if not old_row:
            raise ValueError("Katalog ichida eski gul topilmadi")
        if old_row.quantity_stems < quantity_stems:
            raise ValueError(f"Katalogda eski guldan bir donasiga {old_row.quantity_stems} dona yozilgan")
        if new_batch.remaining_stems < quantity_stems:
            raise ValueError(f"{new_batch.batch_number} partiyasida atigi {new_batch.remaining_stems} dona qolgan")
        old_row.quantity_stems -= quantity_stems
        old_row.quantity_bunches = (Decimal(old_row.quantity_stems) / Decimal(old_batch.stems_per_bunch or 1)).quantize(Decimal("0.01"))
        if old_row.quantity_stems:
            old_row.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
        else:
            old_row.delete()
        new_row = CatalogComposition.objects.select_for_update().filter(catalog_item=item, stock_batch=new_batch).first()
        if new_row:
            new_row.quantity_stems += quantity_stems
            new_row.quantity_bunches = (Decimal(new_row.quantity_stems) / Decimal(new_batch.stems_per_bunch or 1)).quantize(Decimal("0.01"))
            new_row.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
        else:
            new_row = CatalogComposition.objects.create(
                catalog_item=item,
                stock_batch=new_batch,
                quantity_stems=quantity_stems,
                quantity_bunches=(Decimal(quantity_stems) / Decimal(new_batch.stems_per_bunch or 1)).quantize(Decimal("0.01")),
            )
        StockMovement.objects.create(
            batch=old_batch,
            movement_type="waste",
            quantity_stems=-quantity_stems,
            quantity_bunches=-(Decimal(quantity_stems) / Decimal(old_batch.stems_per_bunch or 1)),
            reference_type="catalog_restoration",
            reference_id=item.id,
            reason=reason or f"{item.name_uz} restavratsiya eski gul chiqiti",
            performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        issue = issue_stock_to_florist(florist, new_batch, quantity_stems, reason or f"{item.name_uz} restavratsiya uchun", user)
        balance = florist_balance_row(florist, new_batch, lock=True)
        if balance.remaining_stems < quantity_stems:
            raise ValueError(f"{florist} qo‘lida yangi guldan atigi {balance.remaining_stems} dona bor")
        balance.remaining_stems -= quantity_stems
        balance.save(update_fields=["remaining_stems", "updated_at"])
        item = sync_catalog_financials(item)
        create_catalog_history(
            item,
            "updated",
            user=user,
            note=f"Restavratsiya: {quantity_stems} dona {old_batch.variant} chiqit, {new_batch.variant} qo‘yildi",
            snapshot=catalog_snapshot(item),
        )
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="catalog_restored_flowers", entity_type="CatalogItem", entity_id=str(item.id),
            summary=f"{item.name_uz} restavratsiya qilindi: {quantity_stems} dona",
            after={"old_batch": old_batch.id, "new_batch": new_batch.id, "florist": florist.id, "quantity_stems": quantity_stems, "issue": issue.id},
        )
    return item


def edit_florist_stock_issue(issue, quantity_stems, reason=None, user=None):
    """Floristga chiqarilgan yoki qaytarilgan gul sonini to'g'rilaydi.

    Farq qancha bo'lsa, sklad va floristdagi qoldiq o'shancha siljiydi.
    Chiqim kamaytirilsa gul skladga qaytadi, oshirilsa skladdan yana yechiladi.
    """
    quantity_stems = int(quantity_stems or 0)
    if quantity_stems < 1:
        raise ValueError("Gul soni 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        issue = FloristStockIssue.objects.select_for_update().select_related("batch", "florist").get(pk=issue.pk)
        batch = StockBatch.objects.select_for_update().get(pk=issue.batch_id)
        balance = florist_balance_row(issue.florist, batch, lock=True)
        delta = quantity_stems - issue.quantity_stems
        if delta:
            if issue.kind == "issue":
                # chiqim oshsa skladdan yana yechiladi, kamaysa skladga qaytadi
                if batch.remaining_stems - delta < 0:
                    raise ValueError(f"{batch.batch_number} partiyasida atigi {batch.remaining_stems} dona qolgan")
                if balance.remaining_stems + delta < 0:
                    raise ValueError(f"{issue.florist} qo‘lida atigi {balance.remaining_stems} dona bor, uni kamaytirib bo‘lmaydi")
                batch.remaining_stems -= delta
                balance.remaining_stems += delta
            else:
                # qaytarish yoki chiqit oshsa floristdan yana yechiladi
                if balance.remaining_stems - delta < 0:
                    raise ValueError(f"{issue.florist} qo‘lida atigi {balance.remaining_stems} dona bor")
                balance.remaining_stems -= delta
                if issue.kind == "return":
                    batch.remaining_stems += delta
            batch.save(update_fields=["remaining_stems", "updated_at"])
            balance.save(update_fields=["remaining_stems", "updated_at"])
        before = {"quantity_stems": issue.quantity_stems, "reason": issue.reason}
        issue.quantity_stems = quantity_stems
        if reason is not None:
            issue.reason = reason
        issue.save(update_fields=["quantity_stems", "reason", "updated_at"])
        movement = StockMovement.objects.filter(reference_type="florist_issue", reference_id=issue.id).first()
        if movement:
            sign = -1 if issue.kind in ["issue", "waste"] else 1
            movement.quantity_stems = sign * quantity_stems
            movement.quantity_bunches = Decimal(sign * quantity_stems) / Decimal(batch.stems_per_bunch or 1)
            movement.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="florist_stock_issue_edited", entity_type="FloristStockIssue", entity_id=str(issue.id),
            summary=f"{issue.florist} chiqimi {before['quantity_stems']} dan {quantity_stems} ga o‘zgartirildi",
            before=before,
            after={"quantity_stems": quantity_stems, "reason": issue.reason, "batch": batch.batch_number,
                   "batch_remaining": batch.remaining_stems, "florist_remaining": balance.remaining_stems},
        )
    return issue


def delete_florist_stock_issue(issue, user=None):
    """Chiqim yozuvini bekor qiladi va qoldiqlarni asl holiga qaytaradi."""
    with transaction.atomic():
        issue = FloristStockIssue.objects.select_for_update().select_related("batch", "florist").get(pk=issue.pk)
        batch = StockBatch.objects.select_for_update().get(pk=issue.batch_id)
        balance = florist_balance_row(issue.florist, batch, lock=True)
        if issue.kind == "issue":
            if balance.remaining_stems < issue.quantity_stems:
                raise ValueError(
                    f"{issue.florist} qo‘lida atigi {balance.remaining_stems} dona bor, "
                    f"{issue.quantity_stems} donalik chiqimni bekor qilib bo‘lmaydi"
                )
            balance.remaining_stems -= issue.quantity_stems
            batch.remaining_stems += issue.quantity_stems
        else:
            if issue.kind == "return" and batch.remaining_stems < issue.quantity_stems:
                raise ValueError(f"{batch.batch_number} partiyasida atigi {batch.remaining_stems} dona bor")
            balance.remaining_stems += issue.quantity_stems
            if issue.kind == "return":
                batch.remaining_stems -= issue.quantity_stems
        batch.save(update_fields=["remaining_stems", "updated_at"])
        balance.save(update_fields=["remaining_stems", "updated_at"])
        StockMovement.objects.filter(reference_type="florist_issue", reference_id=issue.id).delete()
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="florist_stock_issue_deleted", entity_type="FloristStockIssue", entity_id=str(issue.id),
            summary=f"{issue.florist} chiqimi bekor qilindi: {issue.quantity_stems} dona",
            before={"quantity_stems": issue.quantity_stems, "kind": issue.kind, "batch": batch.batch_number},
            after={"batch_remaining": batch.remaining_stems, "florist_remaining": balance.remaining_stems},
        )
        issue.delete()


def consume_florist_stock(florist, rows, quantity, reason="", user=None):
    """Katalog yasalganda floristning qo'lidagi guldan minus qiladi."""
    shortages = []
    balances = {}
    for row in rows:
        needed = row.quantity_stems * quantity
        balance = florist_balance_row(florist, row.stock_batch, lock=True)
        if balance.remaining_stems < needed:
            shortages.append(f"{row.stock_batch.batch_number} ({balance.remaining_stems} dona bor, {needed} kerak)")
        balances[row.id] = (balance, needed)
    if shortages:
        raise ValueError(f"{florist} qo‘lida yetarli gul yo‘q: " + ", ".join(shortages))
    for balance, needed in balances.values():
        balance.remaining_stems -= needed
        balance.save(update_fields=["remaining_stems", "updated_at"])


def restore_florist_stock(florist, rows, quantity):
    """Katalog bekor qilinganda gulni floristning qo'liga qaytaradi."""
    for row in rows:
        balance = florist_balance_row(florist, row.stock_batch, lock=True)
        balance.remaining_stems += row.quantity_stems * quantity
        balance.save(update_fields=["remaining_stems", "updated_at"])


def split_stems_over_items(amount, items, max_per_item=None):
    """Gul sonini kataloglar orasida bo'ladi.

    Katalogda bir nechta dona bo'lishi mumkin, tarkib esa bitta dona uchun yoziladi.
    Shuning uchun bo'lish dona hisobida boradi: katalogga har donaga +1 qo'shilsa,
    undan quantity_total dona gul ketadi.

    Avval hammaga tenglab bo'linadi, teng bo'linmagani navbat bilan oldingi
    kataloglarga bittadan qo'shiladi — ya'ni kimdir bittaga ko'proq oladi.

    items: [(tarkib_qatori, katalogdagi dona soni)]
    max_per_item: {tarkib_qatori_id: har donaga eng ko'pi} — teskari yo'nalishda
        tarkibni nolga tushirib yubormaslik uchun.
    Qaytadi: ({tarkib_qatori_id: har donaga o'zgarish}, joylashmay qolgani)
    """
    plan = {row.id: 0 for row, _ in items}
    caps = {row.id: (max_per_item or {}).get(row.id) for row, _ in items}
    total_units = sum(units for _, units in items)
    amount = int(amount or 0)
    if not total_units or amount <= 0:
        return plan, max(amount, 0)
    base = amount // total_units
    if base:
        for row, units in items:
            cap = caps[row.id]
            add = base if cap is None else min(base, cap)
            if add > 0:
                plan[row.id] += add
                amount -= add * units
    placed = True
    while amount > 0 and placed:
        placed = False
        for row, units in items:
            cap = caps[row.id]
            if units <= amount and (cap is None or plan[row.id] < cap):
                plan[row.id] += 1
                amount -= units
                placed = True
                if amount <= 0:
                    break
    return plan, amount


def florist_leftover_candidates(florist, batch):
    """Qoldiq bo'linadigan kataloglar: shu floristniki va tarkibida shu gul bor.

    Sotilgani ham kiradi — gul unga ham haqiqatda ketgan, shuning uchun
    tannarxi to'g'rilanishi kerak.
    """
    return list(
        # soni hali yozilmagan qatorlar chiqim yopilishini kutayapti, ularga tegilmaydi
        CatalogComposition.objects.filter(stock_batch=batch, catalog_item__florist=florist, quantity_stems__gt=0)
        .select_related("catalog_item")
        .order_by("catalog_item__created_at", "catalog_item_id")
    )


def florist_volume_rate_for(florist, item):
    return FloristVolumeRate.objects.filter(
        florist=florist, arrangement_type=item.arrangement_type, volume=item.volume, is_active=True,
    ).first()


def florist_volume_weight(florist, item):
    """Katalogning bir donasiga standart bo'yicha necha dona gul ketishi."""
    rate = florist_volume_rate_for(florist, item)
    return int(rate.default_stems or 0) if rate else 0


def volume_label(item):
    return f"{item.get_arrangement_type_display()} · {item.volume or 'hajmsiz'}"


def florist_weight_plan(florist, items):
    """Taqsimot og'irligini hisoblaydi.

    Asosiysi — hajm tarifidagi standart dona soni. U kiritilmagan bo'lsa
    florist haqi og'irlik bo'ladi: katta buketning haqi kattaroq, ya'ni
    o'lcham nisbati baribir saqlanadi.

    Qaytadi: ({katalog_id: og'irlik}, muammoli hajmlar ro'yxati, og'irlik manbai)
    """
    rates = {item.id: florist_volume_rate_for(florist, item) for item in items}
    missing = sorted({volume_label(item) for item in items if rates[item.id] is None})
    if missing:
        return {}, missing, ""
    stems = {item.id: int(rates[item.id].default_stems or 0) for item in items}
    if all(value > 0 for value in stems.values()):
        return stems, [], "default_stems"
    if any(value > 0 for value in stems.values()):
        # bir qismida kiritilgan, bir qismida yo'q — aralashtirsak taqsimot buziladi
        blank = sorted({volume_label(item) for item in items if stems[item.id] < 1})
        return {}, blank, ""
    fees = {item.id: int(Decimal(rates[item.id].florist_fee or 0)) for item in items}
    if all(value < 1 for value in fees.values()):
        return {}, sorted({volume_label(item) for item in items}), ""
    return fees, [], "florist_fee"


def split_stems_by_weight(amount, items):
    """Gulni kataloglarga hajm standarti bo'yicha bo'ladi.

    items: [(katalog, dona soni, bir donaga standart gul)]
    Avval har biriga ulushiga qarab butun son tushadi, ortgani eng katta
    kasrga ega kataloglardan boshlab bittadan tarqatiladi.
    Qaytadi: ({katalog_id: har donaga gul}, joylashmay qolgani)
    """
    plan = {item.id: 0 for item, _, _ in items}
    amount = int(amount or 0)
    total_weight = sum(units * weight for _, units, weight in items)
    if amount <= 0 or total_weight <= 0:
        return plan, max(amount, 0)
    order = []
    used = 0
    for item, units, weight in items:
        ideal = Decimal(amount) * Decimal(weight) / Decimal(total_weight)
        base = int(ideal)
        plan[item.id] = base
        used += base * units
        order.append((ideal - base, item, units))
    remaining = amount - used
    order.sort(key=lambda row: -row[0])
    placed = True
    while remaining > 0 and placed:
        placed = False
        for _, item, units in order:
            if units <= remaining:
                plan[item.id] += 1
                remaining -= units
                placed = True
                if remaining <= 0:
                    break
    return plan, remaining


def florist_open_catalog_rows(florist, batch):
    """Shu guldan yasalgan, lekin soni hali yozilmagan katalog qatorlari.

    Florist katalogga gulni tanlaydi, sonini yozmaydi — qator soni 0 bo'lib
    turadi. Chiqim yopilganda aynan shular to'ldiriladi. Shu tufayli qizil
    atirgul faqat qizildan yasalgan buketlarga tushadi.
    """
    return list(
        CatalogComposition.objects.filter(catalog_item__florist=florist, stock_batch=batch, quantity_stems=0)
        .select_related("catalog_item")
        .order_by("catalog_item__created_at", "catalog_item_id")
    )


def florist_close_plan(florist, batch, return_stems=0, absorb_remainder=True):
    """Chiqim yopilganda nima bo'lishini hisoblaydi. Hech narsani o'zgartirmaydi."""
    balance = FloristStockBalance.objects.filter(florist=florist, batch=batch).first()
    held = balance.remaining_stems if balance else 0
    return_stems = int(return_stems or 0)
    amount = max(held - return_stems, 0)
    rows = florist_open_catalog_rows(florist, batch)
    items = [row.catalog_item for row in rows]
    weights, missing, weight_source = florist_weight_plan(florist, items)
    weighted = [(item, int(item.quantity_total or 1), weights.get(item.id, 0)) for item in items]
    plan, unplaced = split_stems_by_weight(amount, weighted) if not missing else ({}, amount)
    rounded_extra = 0
    absorbed_remainder = 0
    if absorb_remainder and unplaced and weighted and not missing:
        item, units, _ = sorted(weighted, key=lambda row: (row[1], row[0].created_at, row[0].id))[0]
        step = (unplaced + units - 1) // units
        plan[item.id] = plan.get(item.id, 0) + step
        absorbed_remainder = unplaced
        rounded_extra = step * units - unplaced
        unplaced = 0
    return {
        "weight_source": weight_source,
        "batch_id": batch.id,
        "batch_number": batch.batch_number,
        "flower": str(batch.variant),
        "florist_stems_now": held,
        "return_stems": return_stems,
        "share_stems": amount,
        "unplaced_stems": unplaced,
        "absorbed_remainder": absorbed_remainder,
        "rounded_extra_stems": rounded_extra,
        "missing_rates": missing,
        "items": [
            {
                "catalog_item": item.id,
                "catalog_name": item.name_uz,
                "arrangement_type": item.arrangement_type,
                "volume": item.volume,
                "quantity_total": units,
                "standard_stems": weight,
                "weight": weight,
                "stems_per_item": plan.get(item.id, 0),
                "stems_total": plan.get(item.id, 0) * units,
            }
            for item, units, weight in weighted
        ],
    }


def close_florist_issue(florist, batch, return_stems=0, user=None, absorb_remainder=True):
    """Chiqarilgan gul tugadi: ortig'i skladga qaytariladi, qolgani kataloglarga bo'linadi.

    Florist katalogga faqat hajmni yozadi, qancha gul ketganini yozmaydi.
    Yopilganda chiqarilgan gul o'sha kataloglarga hajm standartiga qarab taqsimlanadi.
    """
    return_stems = int(return_stems or 0)
    if return_stems < 0:
        raise ValueError("Qaytariladigan son manfiy bo‘lmaydi")
    with transaction.atomic():
        balance = florist_balance_row(florist, batch, lock=True)
        if balance.remaining_stems < 1:
            raise ValueError(f"{florist} qo‘lida bu guldan qoldiq yo‘q")
        if return_stems > balance.remaining_stems:
            raise ValueError(f"{florist} qo‘lida bu guldan atigi {balance.remaining_stems} dona bor")
        if return_stems:
            return_stock_from_florist(florist, batch, return_stems, reason="Chiqim yopildi", user=user, kind="return")
            balance.refresh_from_db()
        amount = balance.remaining_stems
        result = {
            "florist": str(florist),
            "batch_number": batch.batch_number,
            "weight_source": "",
            "returned_stems": return_stems,
            "shared_stems": 0,
            "unplaced_stems": 0,
            "absorbed_remainder": 0,
            "rounded_extra_stems": 0,
            "items": [],
        }
        if amount < 1:
            AuditLog.objects.create(
                user=user if getattr(user, "is_authenticated", False) else None,
                action="florist_issue_closed", entity_type="FloristProfile", entity_id=str(florist.id),
                summary=f"{florist} chiqimi yopildi, {return_stems} dona skladga qaytdi", after=result,
            )
            return result
        rows = florist_open_catalog_rows(florist, batch)
        items = [row.catalog_item for row in rows]
        if not items:
            if absorb_remainder:
                return absorb_florist_remainder(florist, batch, user)
            raise ValueError(
                f"{florist} da bu guldan yasalgan, soni yozilmagan katalog yo‘q. "
                f"Qolgan {amount} dona gulni skladga qaytaring yoki chiqitga yozing."
            )
        weights, missing, weight_source = florist_weight_plan(florist, items)
        if missing:
            raise ValueError(
                f"{florist} uchun hajm tarifi to‘liq emas: " + ", ".join(missing)
                + ". Shu hajmlarga dona sonini yoki florist haqini kiriting."
            )
        weighted = [(item, int(item.quantity_total or 1), weights.get(item.id, 0)) for item in items]
        plan, unplaced = split_stems_by_weight(amount, weighted)
        absorbed_remainder = 0
        rounded_extra = 0
        if absorb_remainder and unplaced and weighted:
            item, units, _ = sorted(weighted, key=lambda row: (row[1], row[0].created_at, row[0].id))[0]
            step = (unplaced + units - 1) // units
            plan[item.id] = plan.get(item.id, 0) + step
            absorbed_remainder = unplaced
            rounded_extra = step * units - unplaced
            unplaced = 0
        by_item = {row.catalog_item_id: row for row in rows}
        moved = 0
        for item, units, weight in weighted:
            stems = plan.get(item.id, 0)
            if stems < 1:
                continue
            row = by_item[item.id]
            row.quantity_stems = stems
            row.quantity_bunches = (Decimal(stems) / Decimal(batch.stems_per_bunch or 1)).quantize(Decimal("0.01"))
            row.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
            moved += stems * units
            result["items"].append({
                "catalog_item": item.id,
                "catalog_name": item.name_uz,
                "arrangement_type": item.arrangement_type,
                "volume": item.volume,
                "quantity_total": units,
                "standard_stems": weight,
                "stems_per_item": stems,
                "stems_total": stems * units,
            })
        if absorbed_remainder:
            balance.remaining_stems = 0
        else:
            balance.remaining_stems -= moved
        balance.save(update_fields=["remaining_stems", "updated_at"])
        result["shared_stems"] = amount if absorbed_remainder else moved
        result["unplaced_stems"] = unplaced
        result["absorbed_remainder"] = absorbed_remainder
        result["rounded_extra_stems"] = rounded_extra
        result["weight_source"] = weight_source
        for item, _, _ in weighted:
            sync_catalog_financials(item)
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="florist_issue_closed", entity_type="FloristProfile", entity_id=str(florist.id),
            summary=f"{florist} chiqimi yopildi: {moved} dona {len(result['items'])} ta katalogga bo‘lindi",
            after=result,
        )
    return result


def absorb_florist_remainder(florist, batch, user=None):
    with transaction.atomic():
        balance = florist_balance_row(florist, batch, lock=True)
        amount = int(balance.remaining_stems or 0)
        if amount < 1:
            raise ValueError(f"{florist} qo‘lida bu guldan qoldiq yo‘q")
        candidates = florist_leftover_candidates(florist, batch)
        if not candidates:
            raise ValueError(
                f"{florist} da bu guldan yasalgan katalog topilmadi. "
                f"Qolgan {amount} dona gulni skladga qaytaring yoki chiqitga yozing."
            )
        items = [(row, int(row.catalog_item.quantity_total or 1)) for row in candidates]
        plan, unplaced = split_stems_over_items(amount, items)
        absorbed_remainder = 0
        rounded_extra = 0
        if unplaced:
            row, units = sorted(items, key=lambda item: (item[1], item[0].catalog_item.created_at, item[0].catalog_item_id))[0]
            step = (unplaced + units - 1) // units
            plan[row.id] = plan.get(row.id, 0) + step
            absorbed_remainder = unplaced
            rounded_extra = step * units - unplaced
            unplaced = 0
        moved = 0
        result = {
            "florist": str(florist),
            "batch_number": batch.batch_number,
            "weight_source": "existing_catalog",
            "returned_stems": 0,
            "shared_stems": amount if absorbed_remainder else 0,
            "unplaced_stems": unplaced,
            "absorbed_remainder": absorbed_remainder,
            "rounded_extra_stems": rounded_extra,
            "items": [],
        }
        touched = {}
        for row, units in items:
            step = plan.get(row.id, 0)
            if step < 1:
                continue
            row.quantity_stems += step
            row.quantity_bunches = (Decimal(row.quantity_stems) / Decimal(batch.stems_per_bunch or 1)).quantize(Decimal("0.01"))
            row.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
            moved += step * units
            touched[row.catalog_item_id] = row.catalog_item
            result["items"].append({
                "catalog_item": row.catalog_item_id,
                "catalog_name": row.catalog_item.name_uz,
                "arrangement_type": row.catalog_item.arrangement_type,
                "volume": row.catalog_item.volume,
                "quantity_total": units,
                "stems_per_item": row.quantity_stems,
                "stems_total": row.quantity_stems * units,
                "added_per_item": step,
            })
        if absorbed_remainder:
            balance.remaining_stems = 0
        else:
            balance.remaining_stems -= moved
            result["shared_stems"] = moved
        balance.save(update_fields=["remaining_stems", "updated_at"])
        for item in touched.values():
            sync_catalog_financials(item)
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="florist_issue_remainder_absorbed", entity_type="FloristProfile", entity_id=str(florist.id),
            summary=f"{florist} qoldig‘i mavjud katalogga qo‘shildi: {amount} dona",
            after=result,
        )
    return result


def close_all_florist_issues(florist, user=None, absorb_remainder=True):
    with transaction.atomic():
        balances = list(
            FloristStockBalance.objects.select_for_update()
            .filter(florist=florist, remaining_stems__gt=0)
            .select_related("batch__variant__flower")
            .order_by("batch__batch_number", "batch_id")
        )
        if not balances:
            raise ValueError(f"{florist} qo‘lida yopiladigan qoldiq yo‘q")
        result = {
            "florist": str(florist),
            "closed_batches": 0,
            "shared_stems": 0,
            "absorbed_remainder": 0,
            "rounded_extra_stems": 0,
            "unplaced_stems": 0,
            "batches": [],
        }
        for balance in balances:
            row = close_florist_issue(florist, balance.batch, 0, user, absorb_remainder)
            result["closed_batches"] += 1
            result["shared_stems"] += int(row.get("shared_stems") or 0)
            result["absorbed_remainder"] += int(row.get("absorbed_remainder") or 0)
            result["rounded_extra_stems"] += int(row.get("rounded_extra_stems") or 0)
            result["unplaced_stems"] += int(row.get("unplaced_stems") or 0)
            result["batches"].append(row)
        return result


TO_CATALOG = "to_catalog"
TO_FLORIST = "to_florist"


def florist_stem_batches(florist, batch, direction):
    """Amal qaysi partiyalar ustida bajarilishini aniqlaydi.

    Katalogga qo'shishda faqat qoldig'i bor partiyalar kerak.
    Floristga qaytarishda esa qoldiq nol bo'lishi ham mumkin — gul katalogda turibdi.
    """
    if direction == TO_CATALOG:
        queryset = FloristStockBalance.objects.filter(florist=florist, remaining_stems__gt=0)
        if batch is not None:
            queryset = queryset.filter(batch=batch)
        return [row.batch for row in queryset.select_related("batch__variant__flower").order_by("batch__batch_number", "batch_id")]
    if batch is None:
        raise ValueError("Katalogdan floristga qaytarishda partiyani tanlash kerak")
    return [batch]


def florist_stem_plan(florist, batch=None, direction=TO_CATALOG, quantity_stems=None):
    """Amal qanday bajarilishini oldindan hisoblab beradi. Hech narsani o'zgartirmaydi."""
    rows = []
    for target in florist_stem_batches(florist, batch, direction):
        candidates = florist_leftover_candidates(florist, target)
        items = [(row, int(row.catalog_item.quantity_total or 1)) for row in candidates]
        balance = FloristStockBalance.objects.filter(florist=florist, batch=target).first()
        held = balance.remaining_stems if balance else 0
        if direction == TO_CATALOG:
            amount = held
            caps = None
            sign = 1
        else:
            amount = int(quantity_stems or 0)
            # tarkibni nolga tushirmaymiz, kamida bitta gul qolsin
            caps = {row.id: max(row.quantity_stems - 1, 0) for row, _ in items}
            sign = -1
        plan, leftover = split_stems_over_items(amount, items, caps)
        rows.append({
            "batch_id": target.id,
            "batch_number": target.batch_number,
            "flower": str(target.variant),
            "florist_stems_now": held,
            "requested_stems": amount,
            "unplaced_stems": leftover,
            "blocked": not candidates,
            "reason": "" if candidates else "Bu guldan yasalgan katalog topilmadi",
            "items": [
                {
                    "catalog_item": row.catalog_item_id,
                    "catalog_name": row.catalog_item.name_uz,
                    "quantity_total": units,
                    "stems_per_item_now": row.quantity_stems,
                    "change_per_item": sign * plan[row.id],
                    "change_total": sign * plan[row.id] * units,
                    "stems_per_item_after": row.quantity_stems + sign * plan[row.id],
                }
                for row, units in items
                if plan[row.id]
            ],
        })
    return rows


def adjust_florist_stems(florist, batch=None, direction=TO_CATALOG, quantity_stems=None, user=None):
    """Florist standartdan farqli gul ishlatganda hisobni to'g'rilaydi.

    to_catalog — florist ko'proq ishlatgan. Uning qo'lida yo'q gul qoldiqda turib
    qolgan, katalog tannarxi esa past ko'rinadi. Qoldiq o'sha guldan yasalgan
    kataloglarga bo'linadi va floristdagi qoldiq nolga tushadi.

    to_florist — florist kamroq ishlatgan. Katalogdan ortiqcha yozilgan gul
    kamaytirilib, floristning qo'liga qaytariladi.

    Ikkala yo'nalishda ham sotilgan kataloglar qamraladi: gul ularga ham
    haqiqatda ketgan, shuning uchun tannarxi to'g'rilanishi kerak.
    """
    if direction not in [TO_CATALOG, TO_FLORIST]:
        raise ValueError("Yo‘nalish noto‘g‘ri")
    if direction == TO_FLORIST and int(quantity_stems or 0) < 1:
        raise ValueError("Qaytariladigan gul soni 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        targets = florist_stem_batches(florist, batch, direction)
        if not targets:
            raise ValueError(f"{florist} qo‘lida bo‘linadigan qoldiq yo‘q")
        blocked = []
        prepared = []
        for target in targets:
            candidates = florist_leftover_candidates(florist, target)
            if not candidates:
                blocked.append(f"{target.batch_number} ({target.variant})")
                continue
            items = [(row, int(row.catalog_item.quantity_total or 1)) for row in candidates]
            balance = florist_balance_row(florist, target, lock=True)
            if direction == TO_CATALOG:
                plan, leftover = split_stems_over_items(balance.remaining_stems, items)
            else:
                caps = {row.id: max(row.quantity_stems - 1, 0) for row, _ in items}
                plan, leftover = split_stems_over_items(quantity_stems, items, caps)
                if leftover:
                    raise ValueError(
                        f"{target.batch_number} bo‘yicha {leftover} dona gulni katalogdan kamaytirib bo‘lmadi. "
                        "Katalogdagi gul soni yetmayapti — sonni kamaytiring."
                    )
            prepared.append((target, balance, items, plan, leftover))
        if blocked:
            raise ValueError(
                f"Bu guldan {florist} yasagan katalog topilmadi: " + ", ".join(blocked)
                + ". Qoldiqni skladga qaytaring yoki chiqitga yozing."
            )
        sign = 1 if direction == TO_CATALOG else -1
        result = {"florist": str(florist), "direction": direction, "batches": [], "moved_stems": 0, "unplaced_stems": 0}
        touched = {}
        for target, balance, items, plan, leftover in prepared:
            moved = 0
            rows = []
            for row, units in items:
                step = plan[row.id]
                if not step:
                    continue
                before = row.quantity_stems
                row.quantity_stems += sign * step
                row.quantity_bunches = (
                    Decimal(row.quantity_stems) / Decimal(target.stems_per_bunch or 1)
                ).quantize(Decimal("0.01"))
                row.save(update_fields=["quantity_stems", "quantity_bunches", "updated_at"])
                moved += step * units
                touched[row.catalog_item_id] = row.catalog_item
                rows.append({
                    "catalog_item": row.catalog_item_id,
                    "catalog_name": row.catalog_item.name_uz,
                    "quantity_total": units,
                    "stems_before": before,
                    "stems_after": row.quantity_stems,
                    "change_total": sign * step * units,
                })
            balance.remaining_stems -= sign * moved
            balance.save(update_fields=["remaining_stems", "updated_at"])
            result["moved_stems"] += moved
            result["unplaced_stems"] += leftover
            result["batches"].append({
                "batch_id": target.id,
                "batch_number": target.batch_number,
                "flower": str(target.variant),
                "moved_stems": sign * moved,
                "florist_stems_after": balance.remaining_stems,
                "items": rows,
            })
        for item in touched.values():
            sync_catalog_financials(item)
        action = "florist_stems_to_catalog" if direction == TO_CATALOG else "florist_stems_to_florist"
        summary = (
            f"{florist} qoldig‘i {len(touched)} ta katalogga bo‘lindi"
            if direction == TO_CATALOG
            else f"{len(touched)} ta katalogdan {result['moved_stems']} dona gul {florist} ga qaytarildi"
        )
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action=action,
            summary=summary,
            entity_type="FloristProfile",
            entity_id=str(florist.id),
            after=result,
        )
    return result



def transfer_catalog_to_branch(item, branch, quantity, target_price=None, note="", user=None):
    """Katalog mahsulotining bir qismini boshqa filialga yuboradi.

    Sklad allaqachon 1-filialda yechilgan, shuning uchun bu yerda sklad tegilmaydi —
    faqat katalog yozuvi bo'linadi va yangi filialda o'z narxi bilan paydo bo'ladi.
    """
    quantity = int(quantity or 0)
    if quantity < 1:
        raise ValueError("Yuboriladigan son 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        if item.branch_id:
            raise ValueError("Faqat asosiy filial katalogini boshqa filialga yuborish mumkin")
        available = int(item.quantity_total or 0) - int(item.quantity_sold or 0)
        if quantity > available:
            raise ValueError(f"Yuborish uchun atigi {available} dona bor")
        total = Decimal(item.quantity_total or 1)
        share = Decimal(quantity) / total if total else Decimal("0")
        source_price = Decimal(item.price or 0)
        price = Decimal(str(target_price)) if target_price not in [None, ""] else source_price
        if price < 0:
            raise ValueError("Narx manfiy bo‘lishi mumkin emas")

        target = CatalogItem.objects.create(
            name_uz=item.name_uz, description_uz=item.description_uz, description_ru=item.description_ru,
            note=item.note, arrangement_type=item.arrangement_type, catalog_kind=item.catalog_kind,
            volume=item.volume, branch=branch, source_item=item, source_price=source_price,
            florist=item.florist, height_cm=item.height_cm, diameter_cm=item.diameter_cm,
            price=price, florist_fee=item.florist_fee, florist_salary_amount=0,
            calculated_cost_price=(Decimal(item.calculated_cost_price or 0) * share).quantize(Decimal("0.01")),
            calculated_component_price=(Decimal(item.calculated_component_price or 0) * share).quantize(Decimal("0.01")),
            status="available", image_url=item.image_url, social_post=item.social_post,
            quantity_total=quantity, quantity_sold=0,
            quantity_stock_deducted=quantity, stock_deducted_at=item.stock_deducted_at,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        # Tarkib nusxalanadi — tannarx va hisobot uchun kerak, sklad qayta yechilmaydi.
        CatalogComposition.objects.bulk_create([
            CatalogComposition(catalog_item=target, stock_batch=row.stock_batch, quantity_stems=row.quantity_stems, quantity_bunches=row.quantity_bunches)
            for row in item.composition.all()
        ])
        CatalogMaterialUsage.objects.bulk_create([
            CatalogMaterialUsage(catalog_item=target, packaging=row.packaging, quantity=row.quantity)
            for row in item.materials.all()
        ])

        item.quantity_total -= quantity
        item.quantity_stock_deducted = max(int(item.quantity_stock_deducted or 0) - quantity, 0)
        item.calculated_cost_price = (Decimal(item.calculated_cost_price or 0) * (Decimal("1") - share)).quantize(Decimal("0.01"))
        item.calculated_component_price = (Decimal(item.calculated_component_price or 0) * (Decimal("1") - share)).quantize(Decimal("0.01"))
        if item.quantity_total <= 0:
            item.status = "archived"
        item.save(update_fields=["quantity_total", "quantity_stock_deducted", "calculated_cost_price", "calculated_component_price", "status", "updated_at"])

        transfer = CatalogTransfer.objects.create(
            source_item=item, target_item=target, branch=branch, quantity=quantity,
            source_price=source_price, target_price=price, note=note,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        create_catalog_history(item, "updated", user=user, note=f"{quantity} dona {branch.name} ga yuborildi")
        create_catalog_history(target, "created", user=user, note=f"{branch.name} ga qabul qilindi")
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="catalog_transferred", summary=f"{item.name_uz} {branch.name} ga yuborildi",
            entity_type="CatalogItem", entity_id=str(target.id),
            after={"branch": branch.name, "quantity": quantity, "source_price": str(source_price), "target_price": str(price)},
        )
    return transfer


def deduct_catalog_inventory(item, user, quantity=None):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        quantity = int(quantity if quantity is not None else item.quantity_total)
        if quantity < 1:
            return item
        if item.quantity_stock_deducted + quantity > item.quantity_total:
            raise ValueError("Katalog sklad qoldig‘i umumiy katalog sonidan oshib ketdi")
        rows = list(item.composition.select_related("stock_batch").select_for_update())
        material_rows = list(item.materials.select_related("packaging").select_for_update())
        material_shortages = [row for row in material_rows if row.packaging.quantity < row.quantity * quantity]
        if material_shortages:
            raise ValueError("Katalog uchun yetarli qoldiq yo‘q: " + ", ".join(row.packaging.name_uz for row in material_shortages))
        # Florist tanlangan bo'lsa gul skladdan emas, floristning qo'lidagi qoldiqdan olinadi.
        if item.florist_id and rows:
            consume_florist_stock(item.florist, rows, quantity, user=user)
            item.quantity_stock_deducted += quantity
            item.stock_deducted_at = timezone.now()
            item.save(update_fields=["quantity_stock_deducted", "stock_deducted_at", "updated_at"])
            for row in material_rows:
                packaging = row.packaging
                units = row.quantity * quantity
                packaging.quantity -= units
                packaging.save(update_fields=["quantity", "updated_at"])
                PackagingMovement.objects.create(
                    packaging=packaging, movement_type="out", quantity=-units,
                    reference_type="catalog_item", reference_id=item.id,
                    reason=f"{item.name_uz} uchun ishlatildi",
                    performed_by=user if getattr(user, "is_authenticated", False) else None,
                )
            return item
        stock_shortages = [row for row in rows if row.stock_batch.remaining_stems < row.quantity_stems * quantity]
        if stock_shortages:
            raise ValueError("Katalog uchun yetarli qoldiq yo‘q: " + ", ".join(row.stock_batch.batch_number for row in stock_shortages))
        for row in rows:
            batch = row.stock_batch
            stems = row.quantity_stems * quantity
            bunches = row.quantity_bunches * quantity
            batch.remaining_stems -= stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-stems,
                quantity_bunches=-bunches,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} katalogga qo‘shildi: {quantity} ta",
                performed_by=user,
            )
        for row in material_rows:
            packaging = row.packaging
            amount = row.quantity * quantity
            packaging.quantity -= amount
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="out",
                quantity=-amount,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} katalogga qo‘shildi: {quantity} ta",
                performed_by=user,
            )
        item.quantity_stock_deducted += quantity
        item.stock_deducted_at = timezone.now()
        item.save(update_fields=["quantity_stock_deducted", "stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="catalog_inventory_deducted", summary=f"{item.name_uz} katalog uchun sklad kamaytirildi", entity_type="CatalogItem", entity_id=str(item.id), after={"catalog": item.name_uz, "stock_rows": [{"batch": row.stock_batch.batch_number, "flower": str(row.stock_batch.variant), "quantity_stems": row.quantity_stems * quantity, "quantity_bunches": str(row.quantity_bunches * quantity)} for row in rows], "material_rows": [{"material": row.packaging.name_uz, "type": row.packaging.packaging_type, "quantity": row.quantity * quantity} for row in material_rows], "quantity": quantity, "quantity_stock_deducted": item.quantity_stock_deducted})
    return item


def restore_catalog_inventory(item, user, quantity=None):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        max_quantity = max(item.quantity_stock_deducted - item.quantity_sold, 0)
        quantity = int(quantity if quantity is not None else max_quantity)
        quantity = min(quantity, max_quantity)
        if quantity < 1:
            return item
        rows = list(item.composition.select_related("stock_batch").select_for_update())
        material_rows = list(item.materials.select_related("packaging").select_for_update())
        if item.florist_id and rows:
            restore_florist_stock(item.florist, rows, quantity)
            rows = []
        for row in rows:
            batch = row.stock_batch
            stems = row.quantity_stems * quantity
            bunches = row.quantity_bunches * quantity
            batch.remaining_stems += stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="adjustment",
                quantity_stems=stems,
                quantity_bunches=bunches,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} katalog qoldig‘i qaytdi: {quantity} ta",
                performed_by=user,
            )
        for row in material_rows:
            packaging = row.packaging
            amount = row.quantity * quantity
            packaging.quantity += amount
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="adjustment",
                quantity=amount,
                reference_type="catalog_item",
                reference_id=item.id,
                reason=f"{item.name_uz} katalog qoldig‘i qaytdi: {quantity} ta",
                performed_by=user,
            )
        item.quantity_stock_deducted = max(item.quantity_stock_deducted - quantity, item.quantity_sold)
        item.stock_deducted_at = timezone.now() if item.quantity_stock_deducted else None
        item.save(update_fields=["quantity_stock_deducted", "stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="catalog_inventory_restored", summary=f"{item.name_uz} katalog qoldig‘i skladga qaytarildi", entity_type="CatalogItem", entity_id=str(item.id), after={"catalog": item.name_uz, "stock_rows": [{"batch": row.stock_batch.batch_number, "flower": str(row.stock_batch.variant), "quantity_stems": row.quantity_stems * quantity, "quantity_bunches": str(row.quantity_bunches * quantity)} for row in rows], "material_rows": [{"material": row.packaging.name_uz, "type": row.packaging.packaging_type, "quantity": row.quantity * quantity} for row in material_rows], "quantity": quantity, "quantity_stock_deducted": item.quantity_stock_deducted})
    return item


def mark_catalog_sold(item, user, quantity=1, sale_price=None, discount_reason="", payment_type="", sold_at=None, reservation=None, materials=None, decoration_florist=None):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        reservation = Reservation.objects.select_for_update().filter(pk=getattr(reservation, "pk", reservation)).first() if reservation else item.reservation
        quantity = int(quantity or 1)
        if quantity < 1:
            raise ValueError("Sotilgan son 1 dan kam bo‘lmasligi kerak")
        if item.quantity_sold + quantity > item.quantity_total:
            raise ValueError("Sotilgan son katalogdagi umumiy sondan oshib ketdi")
        listed_price = Decimal(item.price or 0)
        sold_price = Decimal(str(sale_price)) if sale_price not in [None, ""] else listed_price
        if sold_price < 0:
            raise ValueError("Sotuv narxi 0 dan kam bo‘lishi mumkin emas")
        if sold_price < listed_price and not (discount_reason or "").strip():
            raise ValueError("Skidka bilan sotilganda izoh kiritish majburiy")
        item.quantity_sold += quantity
        if reservation and not item.reservation_id:
            item.reservation = reservation
        if item.quantity_sold >= item.quantity_total:
            item.status = "sold"
            item.sold_at = sold_at or timezone.now()
        elif item.status == "draft":
            item.status = "available"
        item.save(update_fields=["quantity_sold", "status", "sold_at", "reservation", "updated_at"])
        snapshot = catalog_snapshot(item)
        snapshot["payment_type"] = payment_type or ""
        if reservation:
            paid = reservation_paid_amount(reservation)
            total = sold_price * Decimal(quantity)
            snapshot["reservation"] = {
                "id": reservation.id,
                "customer": str(reservation.customer),
                "paid_amount": str(paid),
                "sale_total": str(total),
                "remaining_due": str(max(total - paid, Decimal("0"))),
            }
            reservation.catalog_item = item
            reservation.status = "fulfilled"
            reservation.save(update_fields=["catalog_item", "status", "updated_at"])
            reservation = sync_reservation_payment_status(reservation)
        history = create_catalog_history(item, "sold", user=user, quantity=quantity, listed_unit_price=listed_price, sold_unit_price=sold_price, discount_reason=discount_reason, snapshot=snapshot, reservation=reservation)
        material_rows = deduct_catalog_sale_materials(item, materials, quantity, history, user)
        if material_rows:
            snapshot["sale_materials"] = material_rows
            history.snapshot = snapshot
            history.save(update_fields=["snapshot", "updated_at"])
        decoration_entry = add_catalog_sale_decoration_salary(item, decoration_florist, quantity, user, sold_at=sold_at)
        if decoration_entry:
            decoration_amount = Decimal(decoration_florist.decoration_fee or 0) * Decimal(quantity or 1)
            snapshot["sale_decoration"] = {
                "florist_id": decoration_florist.id,
                "florist": str(decoration_florist),
                "unit_amount": str(decoration_florist.decoration_fee or 0),
                "amount": str(decoration_amount),
                "salary_entry_id": decoration_entry.id,
            }
            history.snapshot = snapshot
            history.save(update_fields=["snapshot", "updated_at"])
        if sold_at:
            CatalogHistory.objects.filter(pk=history.pk).update(created_at=sold_at)
            history.created_at = sold_at
        notify_florist_catalog(item, "Katalog sotildi", f"{item.name_uz} katalogidan {quantity} ta sotildi.")
        AuditLog.objects.create(user=user, action="catalog_sold", summary=f"{item.name_uz} katalogdan sotildi", entity_type="CatalogItem", entity_id=str(item.id), after={"catalog": item.name_uz, "status": item.status, "quantity": quantity, "quantity_sold": item.quantity_sold, "sold_unit_price": str(sold_price), "payment_type": payment_type or "", "discount_amount": str(history.discount_amount), "discount_percent": str(history.discount_percent), "discount_reason": discount_reason, "reservation": reservation.id if reservation else None})
    return item


def deduct_catalog_stock(item, user, quantity=None):
    item = CatalogItem.objects.get(pk=item.pk)
    target = item.quantity_total - item.quantity_stock_deducted if quantity is None else quantity
    if int(target or 0) < 1:
        raise ValueError("Katalog sklad qoldig‘i allaqachon kamaytirilgan")
    return deduct_catalog_inventory(item, user, target)


def deduct_lead_stock(lead, user):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if lead.stock_deducted_at:
            return lead
        stock_rows = list(lead.stock_usage.select_related("stock_batch").select_for_update())
        packaging_rows = list(lead.packaging_usage.select_related("packaging").select_for_update())
        catalog_rows = list(lead.catalog_usage.select_related("catalog_item").select_for_update())
        catalog_quantities = {}
        for row in catalog_rows:
            catalog_quantities[row.catalog_item_id] = catalog_quantities.get(row.catalog_item_id, 0) + row.quantity
        catalog_shortages = []
        catalog_items = {}
        for catalog_item_id, quantity in catalog_quantities.items():
            item = CatalogItem.objects.select_for_update().get(pk=catalog_item_id)
            catalog_items[catalog_item_id] = item
            if item.quantity_sold + quantity > item.quantity_total:
                catalog_shortages.append(f"{item.name_uz}: katalogda {item.quantity_total - item.quantity_sold} ta qoldi")
                continue
        stock_shortages = [row for row in stock_rows if row.stock_batch.remaining_stems < row.quantity_stems]
        packaging_shortages = [row for row in packaging_rows if row.packaging.quantity < row.quantity]
        if stock_shortages or packaging_shortages or catalog_shortages:
            parts = [row.stock_batch.batch_number for row in stock_shortages] + [row.packaging.name_uz for row in packaging_shortages] + catalog_shortages
            raise ValueError("Lead uchun yetarli sklad qoldig‘i yo‘q: " + ", ".join(parts))
        for catalog_item_id, quantity in catalog_quantities.items():
            item = catalog_items[catalog_item_id]
            item.quantity_sold += quantity
            if item.quantity_sold >= item.quantity_total:
                item.status = "sold"
                item.sold_at = timezone.now()
            item.save(update_fields=["quantity_sold", "status", "sold_at", "updated_at"])
            notify_florist_catalog(item, "Katalog sotildi", f"{item.name_uz} katalogidan {quantity} ta sotildi.")
        for row in stock_rows:
            batch = row.stock_batch
            batch.remaining_stems -= row.quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-row.quantity_stems,
                quantity_bunches=-row.quantity_bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer}",
                performed_by=user,
            )
        for row in packaging_rows:
            packaging = row.packaging
            packaging.quantity -= row.quantity
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="out",
                quantity=-row.quantity,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer}",
                performed_by=user,
            )
        lead.stock_deducted_at = timezone.now()
        lead.save(update_fields=["stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="lead_stock_deducted", entity_type="Lead", entity_id=str(lead.id), after={"stock_rows": len(stock_rows), "packaging_rows": len(packaging_rows), "catalog_rows": len(catalog_rows)})
    return lead


def restore_lead_stock(lead, user):
    with transaction.atomic():
        lead = Lead.objects.select_for_update().get(pk=lead.pk)
        if not lead.stock_deducted_at:
            return lead
        stock_rows = list(lead.stock_usage.select_related("stock_batch").select_for_update())
        packaging_rows = list(lead.packaging_usage.select_related("packaging").select_for_update())
        catalog_rows = list(lead.catalog_usage.select_related("catalog_item").select_for_update())
        catalog_quantities = {}
        for row in catalog_rows:
            catalog_quantities[row.catalog_item_id] = catalog_quantities.get(row.catalog_item_id, 0) + row.quantity
        for catalog_item_id, quantity in catalog_quantities.items():
            item = CatalogItem.objects.select_for_update().get(pk=catalog_item_id)
            item.quantity_sold = max(item.quantity_sold - quantity, 0)
            if item.quantity_sold < item.quantity_total and item.status == "sold":
                item.status = "available"
                item.sold_at = None
            item.save(update_fields=["quantity_sold", "status", "sold_at", "updated_at"])
        for row in stock_rows:
            batch = row.stock_batch
            batch.remaining_stems += row.quantity_stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="adjustment",
                quantity_stems=row.quantity_stems,
                quantity_bunches=row.quantity_bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: status qaytdi / {lead.customer}",
                performed_by=user,
            )
        for row in packaging_rows:
            packaging = row.packaging
            packaging.quantity += row.quantity
            packaging.save(update_fields=["quantity", "updated_at"])
            PackagingMovement.objects.create(
                packaging=packaging,
                movement_type="adjustment",
                quantity=row.quantity,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: status qaytdi / {lead.customer}",
                performed_by=user,
            )
        lead.stock_deducted_at = None
        lead.save(update_fields=["stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="lead_stock_restored", entity_type="Lead", entity_id=str(lead.id), after={"stock_rows": len(stock_rows), "packaging_rows": len(packaging_rows), "catalog_rows": len(catalog_rows)})
    return lead


def apply_stock_movement(batch, movement_type, quantity_stems=None, reason="", user=None, quantity_bunches=None):
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        if quantity_stems is None:
            quantity_stems = int(Decimal(quantity_bunches or 0) * Decimal(batch.stems_per_bunch))
        delta = abs(quantity_stems) if movement_type in ["in", "transfer_in"] else -abs(quantity_stems)
        if batch.remaining_stems + delta < 0:
            raise ValueError("Skladda yetarli gul yo‘q")
        before = batch.remaining_stems
        batch.remaining_stems += delta
        batch.save(update_fields=["remaining_stems", "updated_at"])
        movement_bunches = Decimal(delta) / Decimal(batch.stems_per_bunch)
        if quantity_bunches is not None:
            movement_bunches = abs(Decimal(quantity_bunches)) if delta > 0 else -abs(Decimal(quantity_bunches))
        movement = StockMovement.objects.create(
            batch=batch,
            movement_type=movement_type,
            quantity_stems=delta,
            quantity_bunches=movement_bunches,
            reason=reason,
            performed_by=user,
        )
        AuditLog.objects.create(user=user, action="stock_movement", summary=f"{batch.batch_number} partiyada {movement_type} harakati", entity_type="StockBatch", entity_id=str(batch.id), before={"remaining_stems": before, "remaining_bunches": str(Decimal(before) / Decimal(batch.stems_per_bunch))}, after={"batch": batch.batch_number, "flower": str(batch.variant), "supplier": batch.supplier.name if batch.supplier_id else "", "movement": movement.id, "movement_type": movement_type, "quantity_stems": delta, "quantity_bunches": str(movement_bunches), "remaining_stems": batch.remaining_stems, "remaining_bunches": str(batch.remaining_bunches), "reason": reason})
        if batch.remaining_stems <= batch.minimum_sale_stems:
            Notification.objects.create(
                notification_type="low_stock",
                title_uz=f"{batch.variant.flower.name_uz} qoldig‘i kamaydi",
                title_ru=f"{batch.variant.flower.name_uz} qoldig‘i kamaydi",
                body_uz=f"{batch.batch_number} partiyada {batch.remaining_stems} dona qoldi.",
                body_ru=f"В партии {batch.batch_number} осталось {batch.remaining_stems} шт.",
                reference_type="stock_batch",
                reference_id=batch.id,
            )
    return movement


def receive_material_into_delivery(delivery, packaging, quantity, cost_price=None, reason="", user=None):
    """Material partiyasiga material kiritadi.

    Material bitta qator bo'lib qoladi: soni oshadi, tannarxi berilgan bo'lsa
    yangilanadi. Kirim yozuvi partiyaga bog'lanadi, shundan kelib chiqib
    materialning oxirgi postavshigi ham ko'rinadi.
    """
    quantity = int(quantity or 0)
    if quantity < 1:
        raise ValueError("Kiritiladigan son 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        packaging = Packaging.objects.select_for_update().get(pk=packaging.pk)
        before = {"quantity": packaging.quantity, "cost_price": str(packaging.cost_price)}
        packaging.quantity += quantity
        fields = ["quantity", "updated_at"]
        if cost_price not in [None, ""]:
            packaging.cost_price = Decimal(str(cost_price))
            fields.append("cost_price")
        packaging.save(update_fields=fields)
        movement = PackagingMovement.objects.create(
            packaging=packaging,
            delivery=delivery,
            movement_type="in",
            quantity=quantity,
            unit_cost=packaging.cost_price,
            reason=reason or f"{delivery.number} partiya kirimi",
            performed_by=user if getattr(user, "is_authenticated", False) else None,
        )
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="material_received", entity_type="Packaging", entity_id=str(packaging.id),
            summary=f"{packaging.name_uz} {delivery.number} partiyadan {quantity} dona kirim qilindi",
            before=before,
            after={
                "material": packaging.name_uz, "delivery": delivery.number,
                "supplier": delivery.supplier.name if delivery.supplier_id else "",
                "quantity": quantity, "cost_price": str(packaging.cost_price),
                "quantity_after": packaging.quantity, "movement": movement.id,
            },
        )
    return movement


def apply_packaging_movement(packaging, movement_type, quantity, reason, user):
    with transaction.atomic():
        packaging = Packaging.objects.select_for_update().get(pk=packaging.pk)
        delta = quantity if movement_type == "adjustment" else abs(quantity) if movement_type in ["in", "transfer_in"] else -abs(quantity)
        if packaging.quantity + delta < 0:
            raise ValueError("Skladda yetarli qadoqlash/savat yo‘q")
        before = packaging.quantity
        packaging.quantity += delta
        packaging.save(update_fields=["quantity", "updated_at"])
        movement = PackagingMovement.objects.create(
            packaging=packaging,
            movement_type=movement_type,
            quantity=delta,
            reason=reason,
            performed_by=user,
        )
        AuditLog.objects.create(user=user, action="packaging_movement", summary=f"{packaging.name_uz} materialida {movement_type} harakati", entity_type="Packaging", entity_id=str(packaging.id), before={"quantity": before}, after={"material": packaging.name_uz, "type": packaging.packaging_type, "movement": movement.id, "movement_type": movement_type, "quantity_delta": delta, "quantity": packaging.quantity, "reason": reason})
    return movement


def open_debt_for_sale(item, user, customer=None, name="", phone="", note=""):
    """Katalog qarzga sotilganda qarz yozuvini ochadi.

    Mijoz tanlanmagan bo'lsa ism va telefon bo'yicha topiladi yoki yangisi ochiladi.
    Summa oxirgi sotuv yozuvidan olinadi — chegirma bilan sotilgan bo'lsa qarz ham
    chegirmali summa bo'ladi.
    """
    from .models import Debt
    from .serializers import resolve_or_create_customer

    history = CatalogHistory.objects.filter(catalog_item=item, action="sold").order_by("-created_at", "-id").first()
    if history is None:
        raise ValueError("Sotuv yozuvi topilmadi")
    resolved = resolve_or_create_customer(customer=customer, name=name, phone=phone)
    if resolved is None:
        raise ValueError("Qarzga sotishda mijozni tanlang yoki ism bilan telefon raqamini kiriting")
    quantity = int(history.quantity or 1)
    amount = Decimal(history.sold_unit_price or 0) * Decimal(quantity)
    with transaction.atomic():
        debt = Debt.objects.create(
            customer=resolved,
            catalog_item=item,
            catalog_history=history,
            quantity=quantity,
            amount=amount,
            note=note,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="debt_opened", entity_type="Debt", entity_id=str(debt.id),
            summary=f"{resolved} ga {item.name_uz} qarzga berildi: {amount}",
            after={"customer": str(resolved), "catalog": item.name_uz, "quantity": quantity, "amount": str(amount), "note": note},
        )
    return debt


def mark_debt_paid(debt, method, user=None, paid_at=None):
    """Qarz to'landi deb belgilaydi. Savdoga aynan shu kunda kiradi."""
    from .models import Debt

    if method not in [key for key, _ in Debt.METHOD_CHOICES]:
        raise ValueError("To‘lov usuli naqd yoki karta bo‘lishi kerak")
    with transaction.atomic():
        debt = Debt.objects.select_for_update().get(pk=debt.pk)
        if debt.is_paid:
            raise ValueError("Bu qarz allaqachon to‘langan")
        debt.is_paid = True
        debt.paid_at = paid_at or timezone.now()
        debt.paid_method = method
        debt.paid_by = user if getattr(user, "is_authenticated", False) else None
        debt.save(update_fields=["is_paid", "paid_at", "paid_method", "paid_by", "updated_at"])
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="debt_paid", entity_type="Debt", entity_id=str(debt.id),
            summary=f"{debt.customer} qarzi to‘landi: {debt.amount}",
            after={"amount": str(debt.amount), "method": method, "paid_at": debt.paid_at.isoformat()},
        )
    return debt


def stock_batch_usage_summary(batch):
    """Partiya qayerlarda ishlatilganini sanaydi. Almashtirishdan oldin ko'rsatish uchun."""
    catalog_rows = CatalogComposition.objects.filter(stock_batch=batch).select_related("catalog_item")
    catalog_ids = {row.catalog_item_id for row in catalog_rows}
    sold = CatalogItem.objects.filter(id__in=catalog_ids, quantity_sold__gt=0).count()
    return {
        "catalog_items": len(catalog_ids),
        "sold_catalog_items": sold,
        "florist_issues": FloristStockIssue.objects.filter(batch=batch).count(),
        "lead_usages": LeadStockUsage.objects.filter(stock_batch=batch).count(),
        "stock_movements": StockMovement.objects.filter(batch=batch).count(),
        "used_stems": max(int(batch.received_stems or 0) - int(batch.remaining_stems or 0), 0),
    }


def change_stock_batch_variant(batch, variant, reason="", user=None):
    """Partiyaning gul navini almashtiradi va ishlatilgan joylarni ham moslaydi.

    Katalog tarkibi, sklad harakatlari va floristdagi qoldiq partiyaga
    bog'langani uchun ular o'zi yangi navni ko'rsatadi. Sotuv tarixidagi
    muzlatilgan nom esa qo'lda yangilanadi, aks holda eski nom qolib ketardi.

    Narxlar partiyada saqlanadi, shuning uchun pul o'zgarmaydi.
    """
    if variant is None:
        raise ValueError("Yangi navni tanlang")
    if variant.id == batch.variant_id:
        raise ValueError("Bu nav allaqachon tanlangan")
    if not (reason or "").strip():
        raise ValueError("Almashtirish sababini yozing")
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().select_related("variant__flower").get(pk=batch.pk)
        old_label = str(batch.variant)
        usage = stock_batch_usage_summary(batch)
        batch.variant = variant
        batch.save(update_fields=["variant", "updated_at"])
        new_label = str(variant)
        # sotuv tarixidagi muzlatilgan nomni ham yangilaymiz
        touched = 0
        histories = CatalogHistory.objects.filter(catalog_item__composition__stock_batch=batch).distinct()
        for history in histories:
            snapshot = history.snapshot or {}
            rows = snapshot.get("composition") or []
            changed = False
            for row in rows:
                if row.get("batch") == batch.batch_number and row.get("flower") == old_label:
                    row["flower"] = new_label
                    changed = True
            if changed:
                history.snapshot = snapshot
                history.save(update_fields=["snapshot", "updated_at"])
                touched += 1
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="stock_batch_variant_changed", entity_type="StockBatch", entity_id=str(batch.id),
            summary=f"{batch.batch_number} partiyada nav {old_label} dan {new_label} ga almashtirildi",
            before={"variant": old_label},
            after={"variant": new_label, "reason": reason, "usage": usage, "history_rows_updated": touched},
        )
    return {"batch": batch, "old_variant": old_label, "new_variant": new_label, "usage": usage, "history_rows_updated": touched}


def store_sale_image(uploaded=None, image_url=""):
    """Sotuv rasmini saqlaydi va URL qaytaradi."""
    from django.core.files.storage import default_storage

    if uploaded is not None:
        path = default_storage.save(f"sales/{uploaded.name}", uploaded)
        return default_storage.url(path)
    return (image_url or "").strip()


def sale_group_caption(item, history, payment_type, image_url=""):
    """Guruhga boradigan markdown xabar: naqd yoki karta va sotuv summasi."""
    labels = {"cash": "Naqd", "card": "Karta", "debt": "Qarz"}
    quantity = int(history.quantity or 1)
    total = Decimal(history.sold_unit_price or 0) * Decimal(quantity)
    listed = Decimal(history.listed_unit_price or 0) * Decimal(quantity)
    lines = [
        f"*{item.name_uz}*",
        f"To‘lov: *{labels.get(payment_type, 'Aniqlanmagan')}*",
        f"Summa: *{total:,.0f} so‘m*".replace(",", " "),
    ]
    if quantity > 1:
        lines.append(f"Soni: {quantity} ta")
    if listed > total:
        chegirma = listed - total
        lines.append(f"Chegirma: {chegirma:,.0f} so‘m".replace(",", " "))
        if history.discount_reason:
            lines.append(f"Sabab: {history.discount_reason}")
    if item.branch_id:
        lines.append(f"Filial: {item.branch.name}")
    return "\n".join(lines)


def notify_sale_to_group(item, history, payment_type, image_url):
    """Sotilgan mahsulot rasmini alohida telegram guruhga yuboradi."""
    from .models import IntegrationSettings
    from .platform_services import telegram_send_photo_with

    if not image_url:
        return None
    integration, _ = IntegrationSettings.objects.get_or_create(pk=1)
    token = (integration.sale_bot_token or "").strip()
    chat_id = (integration.sale_group_chat_id or "").strip()
    if not token or not chat_id:
        print(f"SALE_GROUP_NOT_CONFIGURED catalog={item.id}", flush=True)
        return None
    caption = sale_group_caption(item, history, payment_type, image_url)
    try:
        return telegram_send_photo_with(token, chat_id, image_url, caption)
    except Exception as error:
        # xabar ketmasa ham sotuv bekor bo'lmaydi
        print(f"SALE_GROUP_SEND_FAILED catalog={item.id} error={error}", flush=True)
        return None


def catalog_unit_cost(item):
    """Katalogning bir donasiga to'g'ri keladigan tannarx."""
    total = Decimal(item.quantity_total or 1)
    if total <= 0:
        return Decimal("0")
    return (Decimal(item.calculated_cost_price or 0) / total).quantize(Decimal("0.01"))


def waste_catalog_item(item, user, quantity=1, reason=""):
    """Sotilmay qolgan katalogni chiqitga chiqaradi.

    Gul allaqachon katalog yasalganda skladdan yechilgan, shuning uchun sklad
    qoldig'iga tegilmaydi. Yo'qotish tannarx bo'yicha hisoblanadi va hisob-kitobda
    alohida ko'rinadi.
    """
    quantity = int(quantity or 0)
    if quantity < 1:
        raise ValueError("Chiqitga chiqariladigan son 1 dan kam bo‘lmasligi kerak")
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        remaining = int(item.quantity_total or 0) - int(item.quantity_sold or 0) - int(item.quantity_wasted or 0)
        if quantity > remaining:
            raise ValueError(f"Katalogda atigi {max(remaining, 0)} dona qolgan")
        unit_cost = catalog_unit_cost(item)
        item.quantity_wasted += quantity
        left = int(item.quantity_total or 0) - int(item.quantity_sold or 0) - int(item.quantity_wasted or 0)
        fields = ["quantity_wasted", "updated_at"]
        if left <= 0 and item.status not in ["sold", "archived"]:
            item.status = "sold" if item.quantity_sold else "archived"
            fields.append("status")
        item.save(update_fields=fields)
        snapshot = catalog_snapshot(item)
        snapshot["waste_reason"] = reason
        snapshot["waste_unit_cost"] = str(unit_cost)
        history = create_catalog_history(
            item, "wasted", user=user, quantity=quantity,
            listed_unit_price=item.price, sold_unit_price=Decimal("0"),
            note=reason or "Chiqitga chiqarildi", snapshot=snapshot,
        )
        AuditLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action="catalog_wasted", entity_type="CatalogItem", entity_id=str(item.id),
            summary=f"{item.name_uz} katalogidan {quantity} ta chiqitga chiqarildi",
            before={"quantity_wasted": item.quantity_wasted - quantity},
            after={"quantity_wasted": item.quantity_wasted, "quantity": quantity,
                   "unit_cost": str(unit_cost), "loss": str(unit_cost * Decimal(quantity)),
                   "reason": reason, "history": history.id},
        )
        notify_florist_catalog(item, "Katalog chiqitga chiqdi", f"{item.name_uz} katalogidan {quantity} ta chiqitga chiqarildi.")
    return item
