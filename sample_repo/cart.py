"""
Sample e-commerce shopping cart module.
"""

from typing import Dict, List, Optional


class Item:
    def __init__(self, name: str, price: float, quantity: int = 1):
        if price < 0:
            raise ValueError("Price cannot be negative")
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero")
        self.name = name
        self.price = float(price)
        self.quantity = int(quantity)

    def total(self) -> float:
        return self.price * self.quantity


class ShoppingCart:
    def __init__(self):
        self.items: Dict[str, Item] = {}

    def add_item(self, name: str, price: float, quantity: int = 1) -> None:
        """Add an item to the shopping cart or increment quantity."""
        if name in self.items:
            self.items[name].quantity += quantity
        else:
            self.items[name] = Item(name, price, quantity)

    def remove_item(self, name: str) -> None:
        """Remove an item completely from the cart."""
        if name in self.items:
            del self.items[name]
        else:
            raise KeyError(f"Item {name} not found in cart")

    def get_subtotal(self) -> float:
        """Calculate the sum of all item totals."""
        return sum(item.total() for item in self.items.values())

    def item_count(self) -> int:
        """Return total count of individual units in cart."""
        return sum(item.quantity for item in self.items.values())
