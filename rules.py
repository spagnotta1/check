import json
import os
import re
from typing import Dict, List, Optional

class CategoryRules:
    def __init__(self, rules_file: str = 'category_rules.json'):
        self.rules_file = rules_file
        self.rules = self._load_rules()
    
    def _load_rules(self) -> Dict[str, List[str]]:
        """Load rules from JSON file or create default if not exists."""
        if os.path.exists(self.rules_file):
            with open(self.rules_file, 'r') as f:
                return json.load(f)
        
        # Default rules
        default_rules = {
            "Student Loan": ["First Tech FCU", "FIRSTMARK"],
            "Investments": ["VANGUARD BUY"],
            "Credit Card": ["CAPITAL ONE", "CHASE CREDIT CRD"],
            "Auto Loan": ["JPMorgan Chase"],
            "Income": ["TEVA PHARMA"]
        }
        
        # Save default rules
        self._save_rules(default_rules)
        return default_rules
    
    def _save_rules(self, rules: Dict[str, List[str]]) -> None:
        """Save rules to JSON file."""
        with open(self.rules_file, 'w') as f:
            json.dump(rules, f, indent=2)
    
    def get_category(self, description: str) -> str:
        """Determine category based on description.
        Keywords wrapped in /.../ are treated as regex patterns."""
        for category, keywords in self.rules.items():
            for keyword in keywords:
                if keyword.startswith('/') and keyword.endswith('/') and len(keyword) > 2:
                    try:
                        if re.search(keyword[1:-1], description, re.IGNORECASE):
                            return category
                    except re.error:
                        pass
                else:
                    if keyword.upper() in description.upper():
                        return category
        return "Uncategorized"

    def reorder(self, new_order: List[str]) -> None:
        """Reorder categories to match new_order list."""
        reordered = {cat: self.rules[cat] for cat in new_order if cat in self.rules}
        for cat in self.rules:
            if cat not in reordered:
                reordered[cat] = self.rules[cat]
        self.rules = reordered
        self._save_rules(reordered)
    
    def add_rule(self, category: str, keyword: str) -> None:
        """Add a new keyword to a category (preserves current priority position)."""
        if category not in self.rules:
            self.rules[category] = []
        if keyword not in self.rules[category]:
            self.rules[category].append(keyword)
            self._save_rules(self.rules)

    def add_rule_first(self, category: str, keyword: str) -> None:
        """Add a keyword and move the category to the top of the priority list.

        Rules are evaluated in insertion order; placing a category first ensures
        it wins over every other rule for matching transactions.
        """
        existing = list(self.rules.get(category, []))
        if keyword not in existing:
            existing.append(keyword)
        # Rebuild ordered dict with this category at position 0.
        new_rules: Dict[str, List[str]] = {category: existing}
        for cat, kws in self.rules.items():
            if cat != category:
                new_rules[cat] = kws
        self.rules = new_rules
        self._save_rules(new_rules)
    
    def remove_rule(self, category: str, keyword: str) -> None:
        """Remove a keyword from a category."""
        if category in self.rules and keyword in self.rules[category]:
            self.rules[category].remove(keyword)
            if not self.rules[category]:  # If category is empty, remove it
                del self.rules[category]
            self._save_rules(self.rules)
    
    def get_all_rules(self) -> Dict[str, List[str]]:
        """Get all current rules."""
        return self.rules 