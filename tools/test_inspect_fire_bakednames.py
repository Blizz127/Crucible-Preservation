import unittest
import struct
from types import SimpleNamespace
from unittest.mock import patch

import inspect_fire_bakednames as fire
from objectstream import crc


def element(name, value=None, children=b''):
    prefix = crc(name).to_bytes(4, 'big') + bytes(16)
    if value is None:
        return b'\x48' + prefix + children + b'\0'
    return (b'\x7a' + prefix + len(value).to_bytes(2, 'big')
            + value + children + b'\0')


def stream(children):
    return bytes.fromhex('0000000003') + element('root', children=children) + b'\0'


def asset_reference(hint):
    encoded = hint.encode('utf-8')
    return bytes(48) + len(encoded).to_bytes(8, 'big') + encoded


class ProjectileRequestInspectionTests(unittest.TestCase):
    def test_wire_names_include_static_prefix(self):
        self.assertEqual(fire.combine_names(['None', 'Camera'], ['Muzzle']),
                         ['None', 'Camera', 'Muzzle'])
        with self.assertRaises(ValueError):
            fire.combine_names(['None'], ['none'])

    def test_static_prefix_from_handler_and_reject_changed_getter(self):
        base, getter, counter, array = 0x140000000, 0x100, 0x200, 0x300
        data = {
            fire.STATIC_HANDLERS['attachmentpoint'] + 0x20:
                struct.pack('<QQ', base + getter, base + counter),
            getter: b'\x48\x8d\x05' + struct.pack('<i', array - getter - 7) + b'\xc3',
            counter: b'\xb8\x02\0\0\0\xc3',
            array: struct.pack('<QQ', base + 0x400, base + 0x500),
            0x400: b'None\0', 0x500: b'Camera\0',
        }
        with patch('pefile.PE') as pe:
            image = pe.return_value.__enter__.return_value
            image.OPTIONAL_HEADER = SimpleNamespace(ImageBase=base)
            image.get_data.side_effect = lambda rva, size: data[rva][:size]
            self.assertEqual(fire.static_names('attachmentpoint', 'unused'), ['None', 'Camera'])
            data[counter] = b'\x31\xc0\xc3\0\0\0'
            with self.assertRaises(ValueError):
                fire.static_names('attachmentpoint', 'unused')

    def test_request_slots_preserve_nulls_and_exact_order(self):
        hints = ['', 'Assets/Player/Marine/Primary.projectile'] + [''] * 14
        raw = stream(element('AllowedAssets', children=b''.join(
            element('element', asset_reference(hint)) for hint in hints)))
        slots = fire.request_assets(raw)
        self.assertEqual([slot['asset'] for slot in slots], hints)
        self.assertEqual([slot['index'] for slot in slots], list(range(16)))
        self.assertTrue(all(slot['offset'] > 0 for slot in slots))

    def test_reject_malformed_asset_hint_and_wrong_slot_count(self):
        for raw in (bytes(55), bytes(48) + (2).to_bytes(8, 'big') + b'x'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                fire.asset_hint(raw)
        for children in (b'', element('AllowedAssets')):
            with self.subTest(children=children), self.assertRaises(ValueError):
                fire.request_assets(stream(children))

    def test_attachment_string_is_asset_field_not_muzzle_guess(self):
        text = b'Spitter_Head_Vfx'
        raw = stream(element('Attachment', b'\x01\xff\xff\xff'
                             + len(text).to_bytes(4, 'big') + text))
        self.assertEqual(fire.projectile_attachment(raw), 'Spitter_Head_Vfx')
        with self.assertRaises(ValueError):
            fire.projectile_attachment(stream(element('Attachment', b'bad')))


if __name__ == '__main__':
    unittest.main()
