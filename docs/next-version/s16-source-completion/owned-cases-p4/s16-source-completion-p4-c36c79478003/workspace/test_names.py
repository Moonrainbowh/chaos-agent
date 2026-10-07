import unittest
from names import clean_names
class NamesTests(unittest.TestCase):
    def test_empty(self): self.assertEqual(clean_names([]), [])
    def test_trim(self): self.assertEqual(clean_names([' Alice ', 'Bob']), ['Alice', 'Bob'])
    def test_blank(self): self.assertEqual(clean_names([' ', '', '\t']), [])
    def test_duplicates(self): self.assertEqual(clean_names([' A ', 'A']), ['A', 'A'])
    def test_no_mutation(self):
        values = [' A ', ' ']; clean_names(values); self.assertEqual(values, [' A ', ' '])
