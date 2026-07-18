import os
from app import create_app
from models import db, Transaction
from rules import CategoryRules

app = create_app()
category_rules = CategoryRules()

with app.app_context():
    transactions = Transaction.query.all()
    updated = 0
    for t in transactions:
        new_category = category_rules.get_category(t.description)
        if t.category != new_category:
            print(f"Updating transaction {t.id}: '{t.description}' from '{t.category}' to '{new_category}'")
            t.category = new_category
            updated += 1
    db.session.commit()
    print(f"Re-categorization complete. {updated} transaction(s) updated.") 