from pathlib import Path
import unittest
from xml.etree import ElementTree


SETTINGS_PATH = (Path(__file__).parents[1] / 'resources' / 'settings.xml')
EXPECTED_SETTINGS = (
    'enabled', 'respect_language', 'notify_selected', 'notify_no_match',
    'remember_manual', 'keywords')


class SettingsSchemaTests(unittest.TestCase):
    def setUp(self):
        self.root = ElementTree.parse(SETTINGS_PATH).getroot()

    def test_all_general_settings_share_a_group(self):
        self.assertEqual('1', self.root.get('version'))
        sections = self.root.findall('section')
        self.assertEqual(1, len(sections))
        categories = sections[0].findall('category')
        self.assertEqual(1, len(categories))
        groups = categories[0].findall('group')
        self.assertEqual(1, len(groups))
        self.assertEqual('1', groups[0].get('id'))
        self.assertEqual(
            list(EXPECTED_SETTINGS),
            [setting.get('id') for setting in groups[0].findall('setting')])
        self.assertEqual([], categories[0].findall('setting'))

    def test_keyword_setting_accepts_an_empty_value(self):
        keywords = self.root.find(
            "section/category/group/setting[@id='keywords']")
        self.assertIsNotNone(keywords)
        self.assertEqual(
            'true', keywords.findtext('constraints/allowempty'))

    def test_failure_notices_are_opt_in_and_memory_can_be_disabled(self):
        for key, default in (('notify_no_match', 'false'), ('remember_manual', 'true')):
            setting = self.root.find("section/category/group/setting[@id='%s']" % key)
            self.assertIsNotNone(setting)
            self.assertEqual(default, setting.findtext('default'))
            self.assertTrue(setting.get('help'))


if __name__ == '__main__':
    unittest.main()
