"""Ручные примеры нормализации чисел, версий и идентификаторов."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import norm
import wer


class DecimalNormalizationTests(unittest.TestCase):
    def test_fractional_leading_zero_is_preserved(self):
        self.assertEqual(norm.normalize("1.05"), "один точка ноль пять")
        self.assertEqual(norm.normalize("1.5"), "один точка пять")
        self.assertEqual(wer.score_pair("1.05", "1.5")["deletions"], 1)

    def test_multiple_fractional_zeroes_are_preserved(self):
        self.assertEqual(norm.normalize("1.00"), "один точка ноль ноль")
        self.assertEqual(norm.normalize("1.0"), "один точка ноль")
        self.assertEqual(norm.normalize("1.005"), "один точка ноль ноль пять")

    def test_leading_integer_zero_keeps_decimal_boundary(self):
        self.assertEqual(norm.normalize("007.05"), "ноль ноль семь точка ноль пять")
        self.assertNotEqual(norm.normalize("007.05"), norm.normalize("00705"))

    def test_prefixed_versions_remain_one_identifier(self):
        self.assertEqual(norm.normalize("v1.2.3"), "v1.2.3")
        self.assertNotEqual(norm.normalize("v1.2.3"), norm.normalize("v 1.2.3"))

    def test_ordinary_decimals_versions_and_identifiers(self):
        self.assertEqual(norm.normalize("1.29"), "один точка двадцать девять")
        self.assertEqual(norm.normalize("1,05"), norm.normalize("1.05"))
        self.assertEqual(norm.normalize("1.29.0 10.0.0.0/8 k8s S3"), "1.29.0 10.0.0.0/8 k8s s3")
        self.assertEqual(norm.tokens("1.05 K8S", keep_case=True), ["один", "точка", "ноль", "пять", "K8S"])


if __name__ == "__main__":
    unittest.main()
