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
    return add_individual_side_hooks(name, text.encode())


def add_auth_side_hooks(source):
    """Compose after the separately pinned ordinary authentication transform."""
    text = source.decode()
    text = once(text, '#include "alliance_game/casual_session.hpp"',
                '#include "alliance_game/casual_session.hpp"\n#include "alliance_game/casual_sides.hpp"')
    text = once(text, '#include "alliance_game/casual_sides.hpp"', '#include "alliance_game/casual_sides.hpp"\n#include "alliance_game/individual_rooms.hpp"')
    text = once(text, '    auto admission = alliance_game::prepare_casual_admission();',
                '    alliance_game::reset_casual_game();\n    alliance_game::reset_individual_capability();\n    auto admission = alliance_game::prepare_casual_admission();')
    text = once(text, '\t} // end connect_engine\n\n\tif(dlg_ok) {',
                '\t} // end connect_engine\n\n    if(dlg_ok && connection) dlg_ok=alliance_game::await_casual_start(*connection);\n\n\tif(dlg_ok) {')
    text = once(text, '\t\tconnection->send_data(config{"leave_game"});\n\t}\n}\n\nvoid mp_manager::enter_wait_mode',
                '\t\tconnection->send_data(config{"leave_game"});\n\t}\n    alliance_game::reset_casual_game();\n}\n\nvoid mp_manager::enter_wait_mode')
    text = once(text, '\tconnection->send_data(config{"leave_game"});\n}\n\nbool mp_manager::post_scenario_staging',
                '\tconnection->send_data(config{"leave_game"});\n    alliance_game::reset_casual_game();\n}\n\nbool mp_manager::post_scenario_staging')
    text = once(text, '\t} else if(gui2::dialogs::mp_create_game::execute(state, connection == nullptr)) {\n\t\tenter_staging_mode(queue_type::type::normal);',
                '\t} else if(gui2::dialogs::mp_create_game::execute(state, connection == nullptr)) {\n        if(connection && !alliance_game::configure_individual_creation(state.get_starting_point(), state.classification().is_normal_mp_game() && state.mp_settings().saved_game == saved_game_mode::type::no)) {\n            connection->send_data(config{"refresh_lobby"}); return;\n        }\n        if(connection && state.get_starting_point()["individual_room"].to_bool())state.mp_settings().password.clear();\n\t\tenter_staging_mode(queue_type::type::normal);')
    text = once(text, 'void mp_manager::enter_staging_mode(queue_type::type queue_type, int queue_id)\n{',
                'void mp_manager::enter_staging_mode(queue_type::type queue_type, int queue_id)\n{\n    if(state.mp_settings().saved_game != saved_game_mode::type::no)\n        state.get_starting_point()["individual_room"]=false; // Saved games retain manual resume.')
    return text.encode()


def add_socket_side_hooks(source):
    """Observe authenticated selection messages on the GUI receive thread."""
    text = source.decode()
    text = once(text, '#include "wesnothd_connection.hpp"',
                '#include "wesnothd_connection.hpp"\n#include "alliance_game/casual_sides.hpp"')
    text = once(text, '\t\t\trecv_queue_.pop();',
                '\t\t\trecv_queue_.pop();\n            if(service_=="15000" && alliance_socket_lookup(this)) alliance_game::observe_casual_message(result);')
    return text.encode()


def add_individual_side_hooks(name, source):
    text = source.decode()
    text = once(text, '#include "alliance_game/casual_sides.hpp"',
                '#include "alliance_game/casual_sides.hpp"\n#include "alliance_game/individual_rooms.hpp"')
    if name.endswith('connect_engine.cpp'):
        text = once(text, '\t\t\t\t"name", params_.name,',
                    '\t\t\t\t"name", params_.name,\n                "individual_room", level_.child_or_empty("scenario")["individual_room"].to_bool(),')
        text = once(text, '\t\t\t\t"ignored", prefs::get().get_ignored_delim(),', '\t\t\t\t"ignored", level_.child_or_empty("scenario")["individual_room"].to_bool() ? std::string() : prefs::get().get_ignored_delim(),')
        text = once(text, '\tmp::send_to_server(config{"start_game"});\n}\n\nvoid connect_engine::start_game_commandline',
                    '''    config start;
    auto& command=start.add_child("start_game");
    if(alliance_game::individual_room_active())command["room_revision"]=alliance_game::individual_room_revision();
    mp::send_to_server(start);
}

void connect_engine::start_game_commandline''')
        text = once(text, 'if(data.has_child("alliance_game") || data.has_child("alliance_selection")) {',
                    'if(data.has_child("alliance_game") || data.has_child("alliance_selection") || data.has_child("individual_start")) {')
    elif name.endswith('mp_staging.cpp'):
        # Do not let the old start button, hotkey or plugin bypass Ready consent.
        text = once(text, 'register_hotkey(hotkey::HOTKEY_MP_START_GAME, [this](auto&&...) { start_game(); return true; });',
                    'register_hotkey(hotkey::HOTKEY_MP_START_GAME, [this](auto&&...) { if(!connect_engine_.scenario()["individual_room"].to_bool()) start_game(); return true; });')
        text = once(text, 'plugins_context_->set_callback("launch", [this](const config&) { set_retval(retval::OK); }, false);',
                    'plugins_context_->set_callback("launch", [this](const config&) { if(!connect_engine_.scenario()["individual_room"].to_bool())set_retval(retval::OK); }, false);')
        text = once(text, '\tfind_widget<button>("ok").set_active(connect_engine_.can_start_game());',
                    '''    auto& start=find_widget<button>("ok");
    const bool individual=connect_engine_.scenario()["individual_room"].to_bool();
    start.set_active(!individual && connect_engine_.can_start_game());
    if(individual) {
        start.set_label("Automatic start");
        auto& status=find_widget<label>("status_label");
        status.set_can_wrap(true); status.set_characters_per_line(24);
        status.set_label(alliance_game::individual_room_active()
            ? "Room #" + std::to_string(connect_engine_.params().game_id) + " — waiting for every seat and Ready"
            : "The server is preparing this individual battle room...");
        start.set_tooltip("Choose your side's hero, review the room settings and set Ready. The full room starts automatically.");
    }''')
        # Read numeric room identity from the server snapshot, not mp settings.
        text = text.replace('"Room #" + std::to_string(connect_engine_.params().game_id) + " — waiting for every seat and Ready"',
                            'std::string("Waiting for every seat and Ready. Open your hero to review room settings.")')
        # Configured seats remain public human slots. Owner cannot assign a
        # second side, turn a slot into AI or silently change gold/team settings.
        text = text.replace('controller_selection.set_active(controller_names.size() > 1);',
                            'controller_selection.set_active(!connect_engine_.scenario()["individual_room"].to_bool() && controller_names.size() > 1);')
        text = text.replace('team_selection.set_active(!lock_team);','team_selection.set_active(!lock_team && !connect_engine_.scenario()["individual_room"].to_bool());')
        text = text.replace('slider_gold.set_active(!lock_gold);','slider_gold.set_active(!lock_gold && !connect_engine_.scenario()["individual_room"].to_bool());')
        text = text.replace('slider_income.set_active(!lock_income);','slider_income.set_active(!lock_income && !connect_engine_.scenario()["individual_room"].to_bool());')
        text = once(text, 'team_selection.set_active(!saved_game);',
                    'team_selection.set_active(!saved_game && !connect_engine_.scenario()["individual_room"].to_bool());')
        text = once(text, '\tslider_setup_helper(slider_gold, side->gold());',
                    '\tslider_setup_helper(slider_gold, side->gold());\n    if(connect_engine_.scenario()["individual_room"].to_bool())slider_gold.set_active(false);')
        text = once(text, '\tslider_setup_helper(slider_income, side->income());',
                    '\tslider_setup_helper(slider_income, side->income());\n    if(connect_engine_.scenario()["individual_room"].to_bool())slider_income.set_active(false);')
        old='''\tif(!state_changed_ && (!network_connection_ || !network_connection_->receive_data(data))) {
\t\treturn;
\t}'''
        new='''    if(!state_changed_ && (!network_connection_ || !network_connection_->receive_data(data))) {
        if(network_connection_ && connect_engine_.can_start_game() && alliance_game::consume_individual_start())
            start_game();
        return;
    }'''
        text = once(text,old,new)
        text = once(text, '\tstate_changed_ = false;\n}\n\nvoid mp_staging::post_show()',
                    '''    state_changed_ = false;
    if(!quit_signal_received && network_connection_ && connect_engine_.can_start_game() && alliance_game::consume_individual_start())
        start_game();
}

void mp_staging::post_show()''')
        text = once(text, '\tif(get_retval() == retval::OK) {\n\t\tconnect_engine_.start_game();',
                    '''    if(get_retval() == retval::OK && connect_engine_.scenario()["individual_room"].to_bool()
            && !alliance_game::individual_start_authorized())set_retval(retval::CANCEL);
    if(get_retval() == retval::OK) {
        connect_engine_.start_game();''')
    return text.encode()
