"""Owned side-selection hooks for the pinned ordinary multiplayer controller."""
from hashlib import sha256

PINS = {
    "game_initialization/connect_engine.cpp": "0217b6b9f53a59d63b5af3ee4fd7406b1b3fa2e7df1437b0994fc30307587f86",
    "gui/dialogs/multiplayer/mp_staging.cpp": "436c72764a5159d1dc00065479cb0f53fc7f770418843ee58958c04c805ab720",
    "gui/dialogs/multiplayer/mp_join_game.cpp": "3656f1294bab8fe19ae8d09a00a75a6d3dfbcb921bc39843182e98e416e2acf6",
    "gui/dialogs/multiplayer/mp_create_game.cpp": "b4161925045cd73c170e46002e8dbb485f20ca667aa992479db41cd30be37b02",
}


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Pinned multiplayer side anchor differs; original preserved.")
    return text.replace(old, new, 1)


def generate_side_overlay(name, source):
    if name not in PINS or sha256(source).hexdigest() != PINS[name]:
        raise ValueError("Pinned multiplayer side source differs; original preserved.")
    text = source.decode()
    first_include = text.index("#include ")
    text = text[:first_include] + '#include "alliance_game/casual_sides.hpp"\n' + text[first_include:]
    if name.endswith("connect_engine.cpp"):
        text = once(text, 'if(state_.mp_settings().shuffle_sides && !force_lock_settings_',
                    'if(!alliance_game::casual_game_active() && state_.mp_settings().shuffle_sides && !force_lock_settings_')
        text = once(text, 'bool connect_engine::process_network_data(const config& data)\n{',
                    '''bool connect_engine::process_network_data(const config& data)
{
    if(data.has_child("alliance_game") || data.has_child("alliance_selection")) {
        update_and_send_diff();
        return false;
    }''')
        text = once(text, '\treturn res;\n}\n\nbool side_engine::ready_for_start()',
                    '''    if(parent_.params_.saved_game != saved_game_mode::type::midgame)
        alliance_game::apply_casual_side(res);
    return res;
}

bool side_engine::ready_for_start()''')
        text = once(text, '\tif((controller_ == CNTR_COMPUTER) ||',
                    '''    if(alliance_game::casual_game_active() && (controller_ == CNTR_LOCAL || controller_ == CNTR_NETWORK)) {
        return alliance_game::casual_side_ready(index_+1, controller_ == CNTR_LOCAL ? prefs::get().login() : player_id_);
    }

    if((controller_ == CNTR_COMPUTER) ||''')
    elif name.endswith("mp_staging.cpp"):
        text = text.replace('#include "alliance_game/casual_sides.hpp"', '#include "alliance_game/casual_sides.hpp"\n#include "preferences/preferences.hpp"', 1)
        text = once(text, 'void mp_staging::select_leader_callback(const ng::side_engine_ptr& side, grid& row_grid)\n{',
                    '''void mp_staging::select_leader_callback(const ng::side_engine_ptr& side, grid& row_grid)
{
    if(network_connection_) {
        const auto current=side->new_config();
        if(current["controller"].str()=="human") {
            if(current["player_id"].str()==prefs::get().login())
                alliance_game::choose_casual_side(side->index()+1);
            return;
        }
    }''')
        text = once(text, 'leader_select.set_active(!saved_game);', 'leader_select.set_active(network_connection_ || !saved_game);')
        text = once(text, '\t// Sprite\n\tstd::string new_image;',
                    '''    const auto owned_side=side->new_config();
    if(network_connection_ && owned_side["controller"].str()=="human") {
        const auto& owned_leader=owned_side.child_or_empty("leader");
        if(!owned_leader["type"].empty()) current_leader=owned_leader["type"].str();
    }
    // Sprite
    std::string new_image;''')
        text = once(text, '\trow_grid.find_widget<label>("leader_type").set_label(',
                    '''    if(network_connection_ && owned_side["controller"].str()=="human")
        current_leader=alliance_game::casual_side_label(side->index()+1,owned_side["player_id"].str());
    row_grid.find_widget<label>("leader_type").set_label(''')
        text = text.replace('_("Waiting for players to choose factions...")', 'std::string("Waiting for each player to choose a hero and set Ready...")')
    elif name.endswith("mp_join_game.cpp"):
        text = once(text, 'if(side["player_id"] == prefs::get().login() && side["allow_changes"].to_bool(true))',
                    'if(side["player_id"] == prefs::get().login() && (alliance_game::casual_game_active() || side["allow_changes"].to_bool(true)))')
        text = once(text, 'bool mp_join_game::show_flg_select(int side_num, bool first_time)\n{',
                    '''bool mp_join_game::show_flg_select(int side_num, bool first_time)
{
    if(alliance_game::casual_game_active()) {
        alliance_game::choose_casual_side(side_num);
        return true;
    }''')
        text = once(text, '\t\titem["label"] = description;\n\t\tdata.emplace("leader_type", item);',
                    '''        if(alliance_game::casual_game_active() && side["controller"].str()=="human")
            description += " — " + alliance_game::casual_side_label(side_num,side["player_id"].str());
        item["label"] = description;
        data.emplace("leader_type", item);''')
        # The normal scenario diff still supplies all map/side fields. A Ready
        # acknowledgement also needs to refresh the summary immediately.
        text = once(text, 'void mp_join_game::network_handler()\n{', 'void mp_join_game::network_handler()\n{')
        text = once(text, '\tif(data.has_child("stop_updates")) {',
                    '''    if(data.has_child("alliance_selection")) generate_side_list();
    if(data.has_child("stop_updates")) {''')
    else:
        text = once(text, 'void mp_create_game::pre_show()\n{',
                    '''void mp_create_game::pre_show()
{
    if(!local_mode_) {
        shuffle_sides_->set_widget_value(false);
        auto& shuffle=find_widget<toggle_button>("shuffle_sides");
        shuffle.set_active(false);
        shuffle.set_tooltip("Account hero choices stay on the side each player approved. Side shuffling is unavailable for these games.");
    }''')
        text = text.replace('params.shuffle_sides = shuffle_sides_->get_widget_value();',
                            'params.shuffle_sides = local_mode_ && shuffle_sides_->get_widget_value();')
    return text.encode()


def add_auth_side_hooks(source):
    """Compose after the separately pinned ordinary authentication transform."""
    text = source.decode()
    text = once(text, '#include "alliance_game/casual_session.hpp"',
                '#include "alliance_game/casual_session.hpp"\n#include "alliance_game/casual_sides.hpp"')
    text = once(text, '    auto admission = alliance_game::prepare_casual_admission();',
                '    alliance_game::reset_casual_game();\n    auto admission = alliance_game::prepare_casual_admission();')
    text = once(text, '\t} // end connect_engine\n\n\tif(dlg_ok) {',
                '\t} // end connect_engine\n\n    if(dlg_ok && connection) dlg_ok=alliance_game::await_casual_start(*connection);\n\n\tif(dlg_ok) {')
    text = once(text, '\t\tconnection->send_data(config{"leave_game"});\n\t}\n}\n\nvoid mp_manager::enter_wait_mode',
                '\t\tconnection->send_data(config{"leave_game"});\n\t}\n    alliance_game::reset_casual_game();\n}\n\nvoid mp_manager::enter_wait_mode')
    text = once(text, '\tconnection->send_data(config{"leave_game"});\n}\n\nbool mp_manager::post_scenario_staging',
                '\tconnection->send_data(config{"leave_game"});\n    alliance_game::reset_casual_game();\n}\n\nbool mp_manager::post_scenario_staging')
    return text.encode()


def add_socket_side_hooks(source):
    """Observe authenticated selection messages on the GUI receive thread."""
    text = source.decode()
    text = once(text, '#include "wesnothd_connection.hpp"',
                '#include "wesnothd_connection.hpp"\n#include "alliance_game/casual_sides.hpp"')
    text = once(text, '\t\t\trecv_queue_.pop();',
                '\t\t\trecv_queue_.pop();\n            if(service_=="15000" && alliance_socket_lookup(this)) alliance_game::observe_casual_message(result);')
    return text.encode()
