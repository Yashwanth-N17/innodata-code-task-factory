import pytest
from cart import ShoppingCart, Item


def test_add_item_and_subtotal():
    cart = ShoppingCart()
    cart.add_item("Apple", 1.5, 2)
    cart.add_item("Banana", 0.5, 4)
    assert cart.get_subtotal() == 5.0
    assert cart.item_count() == 6


def test_remove_item():
    cart = ShoppingCart()
    cart.add_item("Apple", 1.5, 2)
    cart.remove_item("Apple")
    assert cart.item_count() == 0
    assert cart.get_subtotal() == 0.0


def test_invalid_item_creation():
    with pytest.raises(ValueError):
        Item("Bad", -1.0, 1)
    with pytest.raises(ValueError):
        Item("Bad", 10.0, 0)


def test_remove_nonexistent_item():
    cart = ShoppingCart()
    with pytest.raises(KeyError):
        cart.remove_item("Ghost")
