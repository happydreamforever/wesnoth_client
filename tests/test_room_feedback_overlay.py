"""Room feedback generation retains shared inputs and all admission behavior."""
import hashlib
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('room_feedback_overlay',
    ROOT / 'tools/android-presentation/room_feedback_overlay.py')
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class RoomFeedbackOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.creation = (ROOT / 'alliance_game/individual_creation.inc').read_bytes()
        cls.rooms = (ROOT / 'alliance_game/individual_rooms.cpp').read_bytes()
        cls.generated = overlay.generate_creation(cls.creation).decode()

    def test_original_sources_are_pinned_and_only_generated_include_changes(self):
        self.assertEqual(hashlib.sha256(self.creation).hexdigest(), overlay.CREATION_PIN)
        self.assertEqual(hashlib.sha256(self.rooms).hexdigest(), overlay.ROOMS_PIN)
        self.assertEqual(overlay.generate_rooms(self.rooms), self.rooms.replace(
            b'#include "alliance_game/individual_creation.inc"', b'#include "room_feedback_creation.inc"'))
        self.assertEqual((ROOT / 'alliance_game/individual_creation.inc').read_bytes(), self.creation)
        self.assertEqual((ROOT / 'alliance_game/individual_rooms.cpp').read_bytes(), self.rooms)

    def test_source_drift_and_duplicate_boundaries_fail_closed(self):
        for fn, source in ((overlay.generate_creation, self.creation),
                           (overlay.generate_rooms, self.rooms)):
            with self.assertRaises(ValueError):
                fn(source + b'\n')
        for value in ('absent', 'boundary boundary'):
            with self.assertRaises(ValueError):
                overlay.replace(value, 'boundary', 'changed')

    def test_errors_are_field_specific_and_modal_only_on_failed_create(self):
        start = self.generated.index('connect_signal_mouse_left_click(find_widget<gui2::button>("publish")')
        end = self.generated.index('});preview();set_enter_disabled(true);', start)
        callback = self.generated[start:end]
        for message in ('Gold must be 0–1000.', 'Base income must be 0–20.',
                        'Turns must be 1–1000 or Unlimited.', 'Enter a room name.',
                        'Choose a battle map.'):
            self.assertIn(message, callback)
        self.assertEqual(callback.count('gui2::show_message('), 1)
        self.assertIn('}catch(...){find_widget<gui2::label>("room_error").set_label(problem);invalidate_layout();', callback)
        self.assertIn('gui2::show_message("Room settings",problem,gui2::dialogs::message::ok_button,false,false);', callback)
        self.assertNotIn('gui2::show_message("Room settings"', self.generated[:start])
        self.assertNotIn('set_value(', callback)
        self.assertNotIn('player_request(', callback)

    def test_validation_and_admission_are_unchanged(self):
        original = self.creation.decode()
        for boundary in ('std::vector<RoomMap> room_creation_maps(',
                         'bool create_individual_room(saved_game& state)'):
            self.assertEqual(self.generated[self.generated.index(boundary):],
                             original[original.index(boundary):])
        for statement in (
                'gold=number("gold",0,1000);income=number("income",0,20);turn_limit=number("turns",-1,1000);if(!turn_limit)throw std::runtime_error("turns");',
                'if(pos!=text.size()||value<minimum||value>maximum)throw std::runtime_error("range");',
                'if(name.empty())throw std::runtime_error("name");',
                'if(selected<0)throw std::runtime_error("map");',
                'set_retval(gui2::retval::OK);'):
            self.assertIn(statement, self.generated)


if __name__ == '__main__':
    unittest.main()
