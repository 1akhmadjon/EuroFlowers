from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import AuditLog, CatalogItem, Lead, Notification, Packaging, PackagingMovement, StockBatch, StockMovement


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


def mark_catalog_sold(item, user, quantity=1):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        quantity = int(quantity or 1)
        if quantity < 1:
            raise ValueError("Sotilgan son 1 dan kam bo‘lmasligi kerak")
        if item.quantity_sold + quantity > item.quantity_total:
            raise ValueError("Sotilgan son katalogdagi umumiy sondan oshib ketdi")
        item.quantity_sold += quantity
        if item.quantity_sold >= item.quantity_total:
            item.status = "sold"
            item.sold_at = timezone.now()
        elif item.status == "draft":
            item.status = "available"
        item.save(update_fields=["quantity_sold", "status", "sold_at", "updated_at"])
        notification = Notification.objects.create(
            branch=item.branch,
            notification_type="stock_pending",
            title_uz=f"{item.name_uz}: sklad chiqimi kutilmoqda",
            title_ru=f"{item.name_ru}: ожидается списание со склада",
            body_uz=f"{quantity} ta kompozitsiya sotildi. Tarkibdagi gullarni sklad hisobidan chiqaring.",
            body_ru=f"Продано композиций: {quantity}. Спишите цветы из состава со склада.",
            reference_type="catalog_item",
            reference_id=item.id,
        )
        AuditLog.objects.create(user=user, action="catalog_sold", entity_type="CatalogItem", entity_id=str(item.id), after={"status": item.status, "quantity": quantity, "quantity_sold": item.quantity_sold, "notification": notification.id})
    return item


def deduct_catalog_stock(item, user, quantity=None):
    with transaction.atomic():
        item = CatalogItem.objects.select_for_update().get(pk=item.pk)
        pending = item.quantity_sold - item.quantity_stock_deducted
        if quantity is None:
            quantity = pending
        quantity = int(quantity or 0)
        if quantity < 1:
            raise ValueError("Skladdan kamaytirish uchun sotilgan, lekin yechilmagan son yo‘q")
        if quantity > pending:
            raise ValueError("Skladdan kamaytirish soni sotilgan, lekin yechilmagan sondan oshib ketdi")
        rows = list(item.composition.select_related("stock_batch").select_for_update())
        shortages = [row for row in rows if row.stock_batch.remaining_stems < row.quantity_stems * quantity]
        if shortages:
            names = ", ".join(row.stock_batch.batch_number for row in shortages)
            raise ValueError(f"Yetarli qoldiq yo‘q: {names}")
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
                reason=f"{item.name_uz} sotildi: {quantity} ta",
                performed_by=user,
            )
        item.quantity_stock_deducted += quantity
        if item.quantity_stock_deducted >= item.quantity_sold:
            item.stock_deducted_at = timezone.now()
            Notification.objects.filter(reference_type="catalog_item", reference_id=item.id, notification_type="stock_pending").update(is_read=True)
        item.save(update_fields=["quantity_stock_deducted", "stock_deducted_at", "updated_at"])
        AuditLog.objects.create(user=user, action="catalog_stock_deducted", entity_type="CatalogItem", entity_id=str(item.id), after={"rows": len(rows), "quantity": quantity, "quantity_stock_deducted": item.quantity_stock_deducted})
    return item


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
        catalog_composition_rows = []
        catalog_shortages = []
        catalog_items = {}
        for catalog_item_id, quantity in catalog_quantities.items():
            item = CatalogItem.objects.select_for_update().get(pk=catalog_item_id)
            catalog_items[catalog_item_id] = item
            if item.quantity_sold + quantity > item.quantity_total:
                catalog_shortages.append(f"{item.name_uz}: katalogda {item.quantity_total - item.quantity_sold} ta qoldi")
                continue
            for composition in item.composition.select_related("stock_batch").select_for_update():
                needed = composition.quantity_stems * quantity
                if composition.stock_batch.remaining_stems < needed:
                    catalog_shortages.append(f"{item.name_uz} / {composition.stock_batch.batch_number}: kerak {needed}, bor {composition.stock_batch.remaining_stems}")
                catalog_composition_rows.append((quantity, item, composition, needed, composition.quantity_bunches * quantity))
        stock_shortages = [row for row in stock_rows if row.stock_batch.remaining_stems < row.quantity_stems]
        packaging_shortages = [row for row in packaging_rows if row.packaging.quantity < row.quantity]
        if stock_shortages or packaging_shortages or catalog_shortages:
            parts = [row.stock_batch.batch_number for row in stock_shortages] + [row.packaging.name_uz for row in packaging_shortages] + catalog_shortages
            raise ValueError("Lead uchun yetarli sklad qoldig‘i yo‘q: " + ", ".join(parts))
        for quantity, item, composition, stems, bunches in catalog_composition_rows:
            batch = composition.stock_batch
            batch.remaining_stems -= stems
            batch.save(update_fields=["remaining_stems", "updated_at"])
            StockMovement.objects.create(
                batch=batch,
                movement_type="out",
                quantity_stems=-stems,
                quantity_bunches=-bunches,
                reference_type="lead",
                reference_id=lead.id,
                reason=f"Lead #{lead.id}: {lead.customer} / {item.name_uz}: {quantity} ta",
                performed_by=user,
            )
        for catalog_item_id, quantity in catalog_quantities.items():
            item = catalog_items[catalog_item_id]
            item.quantity_sold += quantity
            item.quantity_stock_deducted += quantity
            if item.quantity_sold >= item.quantity_total:
                item.status = "sold"
                item.sold_at = timezone.now()
            item.stock_deducted_at = timezone.now()
            item.save(update_fields=["quantity_sold", "quantity_stock_deducted", "status", "sold_at", "stock_deducted_at", "updated_at"])
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
            for composition in item.composition.select_related("stock_batch").select_for_update():
                batch = composition.stock_batch
                stems = composition.quantity_stems * quantity
                bunches = composition.quantity_bunches * quantity
                batch.remaining_stems += stems
                batch.save(update_fields=["remaining_stems", "updated_at"])
                StockMovement.objects.create(
                    batch=batch,
                    movement_type="adjustment",
                    quantity_stems=stems,
                    quantity_bunches=bunches,
                    reference_type="lead",
                    reference_id=lead.id,
                    reason=f"Lead #{lead.id}: status qaytdi / {item.name_uz}: {quantity} ta",
                    performed_by=user,
                )
            item.quantity_sold = max(item.quantity_sold - quantity, 0)
            item.quantity_stock_deducted = max(item.quantity_stock_deducted - quantity, 0)
            if item.quantity_sold < item.quantity_total and item.status == "sold":
                item.status = "available"
                item.sold_at = None
            item.stock_deducted_at = timezone.now() if item.quantity_stock_deducted and item.quantity_stock_deducted >= item.quantity_sold else None
            item.save(update_fields=["quantity_sold", "quantity_stock_deducted", "status", "sold_at", "stock_deducted_at", "updated_at"])
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


def apply_stock_movement(batch, movement_type, quantity_stems, reason, user):
    with transaction.atomic():
        batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
        delta = abs(quantity_stems) if movement_type in ["in", "transfer_in"] else -abs(quantity_stems)
        if batch.remaining_stems + delta < 0:
            raise ValueError("Skladda yetarli gul yo‘q")
        before = batch.remaining_stems
        batch.remaining_stems += delta
        batch.save(update_fields=["remaining_stems", "updated_at"])
        movement = StockMovement.objects.create(
            batch=batch,
            movement_type=movement_type,
            quantity_stems=delta,
            quantity_bunches=Decimal(delta) / Decimal(batch.stems_per_bunch),
            reason=reason,
            performed_by=user,
        )
        AuditLog.objects.create(user=user, action="stock_movement", entity_type="StockBatch", entity_id=str(batch.id), before={"remaining_stems": before}, after={"remaining_stems": batch.remaining_stems, "movement": movement.id})
        if batch.remaining_stems <= batch.minimum_sale_stems:
            Notification.objects.create(
                branch=batch.branch,
                notification_type="low_stock",
                title_uz=f"{batch.variant.flower.name_uz} qoldig‘i kamaydi",
                title_ru=f"Остаток {batch.variant.flower.name_ru} заканчивается",
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
        AuditLog.objects.create(user=user, action="packaging_movement", entity_type="Packaging", entity_id=str(packaging.id), before={"quantity": before}, after={"quantity": packaging.quantity, "movement": movement.id})
    return movement
