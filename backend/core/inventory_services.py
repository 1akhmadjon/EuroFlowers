from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, CatalogHistory, CatalogItem, FloristSalaryEntry, FloristVolumeRate, Lead, Notification, Packaging, PackagingMovement, StockBatch, StockMovement


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
        "calculated_component_price": str(item.calculated_component_price),
        "composition": [{"batch": row.stock_batch.batch_number, "flower": str(row.stock_batch.variant), "quantity_stems": row.quantity_stems, "quantity_bunches": str(row.quantity_bunches)} for row in item.composition.select_related("stock_batch__variant__flower")],
        "materials": [{"material": row.packaging.name_uz, "type": row.packaging.packaging_type, "quantity": row.quantity} for row in item.materials.select_related("packaging")],
    }


def create_catalog_history(item, action, user=None, quantity=0, listed_unit_price=None, sold_unit_price=None, discount_reason="", note="", snapshot=None):
    listed = money(listed_unit_price if listed_unit_price is not None else item.price)
    sold = money(sold_unit_price if sold_unit_price is not None else listed)
    quantity = int(quantity or 0)
    discount = max((listed - sold) * Decimal(quantity), Decimal("0"))
    return CatalogHistory.objects.create(
        catalog_item=item,
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


def catalog_component_total(item):
    quantity = int(item.quantity_total or 1)
    stock_total = Decimal("0")
    for row in item.composition.select_related("stock_batch"):
        stock_total += Decimal(row.quantity_stems * quantity) * row.stock_batch.sale_price_per_stem
    material_total = Decimal("0")
    for row in item.materials.select_related("packaging"):
        material_total += Decimal(row.quantity * quantity) * row.packaging.sale_price
    florist_total = Decimal(item.florist_fee or 0) * Decimal(quantity)
    return stock_total + material_total + florist_total


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
    return {
        "flower_cost": flower_cost,
        "material_cost": material_cost,
        "florist_fee_cost": florist_fee_cost,
        "total": flower_cost + material_cost + florist_fee_cost,
    }


def catalog_cost_total(item):
    return catalog_cost_breakdown(item)["total"]


def apply_volume_rate(item):
    if not item.volume or not item.arrangement_type or item.florist_salary_amount:
        return item
    rate = None
    if item.florist_id:
        rate = FloristVolumeRate.objects.filter(florist=item.florist, arrangement_type=item.arrangement_type, volume=item.volume, is_active=True).first()
    if not rate:
        rate = FloristVolumeRate.objects.filter(florist__isnull=True, arrangement_type=item.arrangement_type, volume=item.volume, is_active=True).first()
    if rate:
        item.florist_salary_amount = rate.florist_fee
    return item


def sync_catalog_financials(item):
    item = CatalogItem.objects.get(pk=item.pk)
    apply_volume_rate(item)
    total = catalog_component_total(item)
    cost_total = catalog_cost_total(item)
    sale_total = Decimal(item.price or 0) * Decimal(item.quantity_total or 1)
    item.calculated_cost_price = cost_total
    item.calculated_component_price = total
    item.discount_amount = max(total - sale_total, Decimal("0"))
    item.discount_percent = discount_percent(item.discount_amount, total)
    item.save(update_fields=["florist_salary_amount", "calculated_cost_price", "calculated_component_price", "discount_amount", "discount_percent", "updated_at"])
    return item


def sync_catalog_florist_salary(item, user):
    item = CatalogItem.objects.select_related("florist").get(pk=item.pk)
    if not item.florist_id or not item.florist_salary_amount:
        FloristSalaryEntry.objects.filter(catalog_item=item).delete()
        return None
    source = "custom_catalog" if item.catalog_kind == "custom" else "catalog"
    FloristSalaryEntry.objects.filter(catalog_item=item).exclude(florist=item.florist, source=source).delete()
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
        stock_shortages = [row for row in rows if row.stock_batch.remaining_stems < row.quantity_stems * quantity]
        material_shortages = [row for row in material_rows if row.packaging.quantity < row.quantity * quantity]
        if stock_shortages or material_shortages:
            parts = [row.stock_batch.batch_number for row in stock_shortages] + [row.packaging.name_uz for row in material_shortages]
            raise ValueError("Katalog uchun yetarli qoldiq yo‘q: " + ", ".join(parts))
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


def mark_catalog_sold(item, user, quantity=1, sale_price=None, discount_reason="", payment_type=""):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
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
        if item.quantity_sold >= item.quantity_total:
            item.status = "sold"
            item.sold_at = timezone.now()
        elif item.status == "draft":
            item.status = "available"
        item.save(update_fields=["quantity_sold", "status", "sold_at", "updated_at"])
        snapshot = catalog_snapshot(item)
        snapshot["payment_type"] = payment_type or ""
        history = create_catalog_history(item, "sold", user=user, quantity=quantity, listed_unit_price=listed_price, sold_unit_price=sold_price, discount_reason=discount_reason, snapshot=snapshot)
        notify_florist_catalog(item, "Katalog sotildi", f"{item.name_uz} katalogidan {quantity} ta sotildi.")
        AuditLog.objects.create(user=user, action="catalog_sold", summary=f"{item.name_uz} katalogdan sotildi", entity_type="CatalogItem", entity_id=str(item.id), after={"catalog": item.name_uz, "status": item.status, "quantity": quantity, "quantity_sold": item.quantity_sold, "sold_unit_price": str(sold_price), "payment_type": payment_type or "", "discount_amount": str(history.discount_amount), "discount_percent": str(history.discount_percent), "discount_reason": discount_reason})
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
