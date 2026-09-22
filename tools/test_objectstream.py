import unittest
import uuid

from objectstream import parse

HEADER = bytes.fromhex('0000000003')
TYPE = uuid.UUID('31b5fb02-b797-edab-f41e-e4576e15c5b9')


class ObjectStreamTests(unittest.TestCase):
    def test_nested_named_versioned_and_extended_value(self):
        # Parent contains a named/versioned child with a two-byte size field.
        value = b'x' * 260
        child = b'\xfa' + bytes.fromhex('12345678') + b'\x02' + TYPE.bytes
        wire = HEADER + b'\x08' + TYPE.bytes + child + b'\x01\x04' + value + b'\0\0\0'
        roots = parse(wire)
        self.assertEqual(len(roots), 1)
        nested = roots[0].children[0]
        self.assertEqual((nested.name, nested.version, nested.type_id),
                         (0x12345678, 2, str(TYPE)))
        self.assertEqual(nested.value, value)
        for end in range(len(wire)):
            with self.subTest(end=end), self.assertRaises(ValueError):
                parse(wire[:end])

    def test_inline_values_and_siblings(self):
        first = b'\x1b' + TYPE.bytes + b'abc\0'
        roots = parse(HEADER + first + b'\x08' + TYPE.bytes + b'\0\0')
        self.assertEqual([x.value for x in roots], [b'abc', b''])

    def test_reject_invalid_or_unsupported_format(self):
        for wire in (HEADER[:-1] + b'\x02\0', HEADER + b'\x10',
                     HEADER + b'\x28', HEADER + b'\x3b' + TYPE.bytes,
                     HEADER + b'\0junk'):
            with self.subTest(wire=wire), self.assertRaises(ValueError):
                parse(wire)


if __name__ == '__main__':
    unittest.main()
