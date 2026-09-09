import unittest
from tokens import estimate_tokens, truncate_to_tokens
from paths import canonical_path_key, logical_lines
class Regression(unittest.TestCase):
 def test_token_rounding(self):
  self.assertEqual(estimate_tokens('abc'),1)
  self.assertEqual(estimate_tokens('a'*5),2)
  self.assertEqual(estimate_tokens('中a'),2)
  self.assertEqual(estimate_tokens(''),0)
  self.assertLessEqual(estimate_tokens(truncate_to_tokens('abcdefghi',1)),1)
 def test_line_styles(self):
  self.assertEqual(logical_lines('a\r\nb\r\n'),('a','b'))
  self.assertEqual(logical_lines(''),())
 def test_path_case(self):
  self.assertEqual(canonical_path_key('Src/File.py',case_insensitive=False),'Src/File.py')
  self.assertEqual(canonical_path_key('Src/File.py',case_insensitive=True),'src/file.py')
