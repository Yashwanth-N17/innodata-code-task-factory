import unittest
from cart import ShoppingCart, Item


class TestShoppingCart(unittest.TestCase):
    def test_add_item_and_subtotal(self):
        cart = ShoppingCart()
        cart.add_item("Apple", 1.5, 2)
        cart.add_item("Banana", 0.5, 4)
        self.assertEqual(cart.get_subtotal(), 5.0)
        self.assertEqual(cart.item_count(), 6)

    def test_remove_item(self):
        cart = ShoppingCart()
        cart.add_item("Apple", 1.5, 2)
        cart.remove_item("Apple")
        self.assertEqual(cart.item_count(), 0)
        self.assertEqual(cart.get_subtotal(), 0.0)

    def test_invalid_item_creation(self):
        with self.assertRaises(ValueError):
            Item("Bad", -1.0, 1)
        with self.assertRaises(ValueError):
            Item("Bad", 10.0, 0)

    def test_remove_nonexistent_item(self):
        cart = ShoppingCart()
        with self.assertRaises(KeyError):
            cart.remove_item("Ghost")


if __name__ == "__main__":
    unittest.main()

