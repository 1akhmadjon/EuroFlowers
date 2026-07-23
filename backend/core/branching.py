from .models import Branch


def default_branch():
    branch = Branch.objects.filter(is_active=True).order_by("id").first() or Branch.objects.order_by("id").first()
    if branch:
        return branch
    return Branch.objects.create(name="EuroFlowers", code="MAIN")
