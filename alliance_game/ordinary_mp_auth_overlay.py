"""Pinned native multiplayer account admission; stock lobby/game flow is retained."""
from hashlib import sha256


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Pinned multiplayer authentication anchor differs.")
    return text.replace(before, after, 1)


def generate_multiplayer_auth_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != "9d369f2a3be3dafe63c287182eb04201077df7eb5a9ecbf7a2f565244bf2a225":
        raise ValueError("Pinned multiplayer source differs; original preserved.")
    text = source.decode()
    text = once(text, '#include "wesnothd_connection.hpp"', '#include "wesnothd_connection.hpp"\n#include "alliance_game/casual_session.hpp"')
    start = text.index('\t// shown_hosts is used to prevent the client being locked in a redirect loop.')
    end = text.index('\n\t// First, spin until we get a handshake from the server.', start)
    text = text[:start] + '''    auto admission = alliance_game::prepare_casual_admission();
    if(!admission) return nullptr;
    // The trusted numeric address is fixed by startup authentication. Neither
    // typed addresses, redirects nor TLS failures can select another server.
    prefs::get().set_login(admission->username);
    gui2::dialogs::loading_screen::progress(loading_stage::connect_to_server);
    auto conn = std::make_unique<wesnothd_connection>(admission->trusted);
''' + text[end:]
    start = text.index('\t\t// Check for "redirect" messages')
    end = text.index('\n\t\tif(data.has_child("version"))', start)
    text = text[:start] + '''        if(data.has_child("redirect"))
            throw wesnothd_error("This game connects only to the trusted Windows server. Server redirects are refused.");
''' + text[end:]
    start = text.index('\t\t// Enter login loop')
    end = text.index('\n\t\tif(const auto join_lobby', start)
    text = text[:start] + '''        if(admission->ticket.empty())
            throw wesnothd_error("The server requested repeated account admission. Return to Multiplayer and try again.");
        config login;
        auto& credentials=login.add_child("login");
        credentials["username"]=admission->username;
        credentials["alliance_ticket"]=admission->ticket;
        conn->send_data(login);
        credentials["alliance_ticket"]="";
        login.clear();
        alliance_game::clear_casual_secret(admission->ticket);
        conn->wait_and_receive_data(data);
        gui2::dialogs::loading_screen::progress(loading_stage::login_response);
        if(data.has_child("error"))
            throw wesnothd_error("Your account could not enter Multiplayer. Return to the main menu and check your session.");
        if(!data.has_child("join_lobby"))
            throw wesnothd_error("The trusted server returned an unsupported account admission response.");
''' + text[end:]
    return text.encode()


def generate_lobby_overlay(source: bytes) -> bytes:
    if sha256(source).hexdigest() != "21f59dd32fede0e66c225b2e210407c740e0ba530278e1eb6a07bff3912994c5":
        raise ValueError("Pinned multiplayer lobby source differs; original preserved.")
    text = source.decode()
    text = once(text, 'return show_prompt(_("Do you really want to log out?"));',
                'return true; // Back leaves the lobby, retaining the startup account session.')
    text = once(text, 'void mp_lobby::pre_show()\n{\n',
                'void mp_lobby::pre_show()\n{\n\tfind_widget<button>("quit").set_label("Back");\n')
    return add_individual_dashboard(text.encode())


def add_individual_dashboard(source):
    text=source.decode()
    text=once(text,'#include "wesnothd_connection.hpp"',
              '#include "wesnothd_connection.hpp"\n#include "alliance_game/individual_rooms.hpp"\n#include "gui/widgets/toggle_button.hpp"')
    text=once(text, '\tfind_widget<button>("create").set_retval(CREATE);',
              '\tfind_widget<button>("create").set_retval(CREATE);\n    find_widget<button>("create").set_label("Create room");\n    find_widget<button>("join_global").set_label("Review and join");\n    find_widget<button>("server_info").set_label("Gold & turns");')
    text=once(text,'\tfilter_text_    = find_widget<text_box>("filter_text", false, true);',
              '''    filter_text_ = find_widget<text_box>("filter_text", false, true);
    filter_text_->set_value("");
    filter_text_->set_tooltip("Search by room or map name. Use the checkboxes and Filters for room type, availability, gold and turn limits.");
    alliance_game::reset_individual_filters();
    filter_friends_->set_widget_value(true);
    filter_ignored_->set_widget_value(false);
    filter_slots_->set_widget_value(false);
    filter_invert_->set_widget_value(false);
    find_widget<toggle_button>("filter_with_friends").set_label("Individual rooms");
    find_widget<toggle_button>("filter_with_ignored").set_label("Standard rooms");
    find_widget<toggle_button>("filter_vacant_slots").set_label("Open places");
    find_widget<toggle_button>("filter_invert").set_label("Running only");
    find_widget<toggle_button>("filter_with_friends").set_tooltip("Show public individual battle rooms.");
    find_widget<toggle_button>("filter_with_ignored").set_tooltip("Show standard manual, computer-side and saved-game rooms.");
    find_widget<toggle_button>("filter_vacant_slots").set_tooltip("Show waiting rooms with a place available to join.");
    find_widget<toggle_button>("filter_invert").set_tooltip("Show battles that have already started. Open places and Running only together may show no rooms.");
    connect_signal_notify_modified(*filter_text_, std::bind(&mp_lobby::update_gamelist_filter,this));''')
    text=once(text,'VGETTEXT("Games: showing $num_shown out of $num_total",','VGETTEXT("Rooms: showing $num_shown out of $num_total",')
    text=once(text,'\tdata.emplace("status", item);\n\n\treturn data;',
              '''    data.emplace("status", item);
    if(const auto* room=alliance_game::individual_lobby_room(lobby_info_.gamelist(),game.id)) {
        data["status"]["label"]=alliance_game::individual_listing_status(*room);
        data["status"]["use_markup"]="false";
        data["name"]["label"]=font::escape_text("#"+(*room)["id"].str()+" "+(*room)["name"].str());
        data["scenario"]["label"]=font::escape_text((*room)["mp_scenario_name"].str())+"\\n"+alliance_game::individual_listing_settings(*room);
    }
    return data;''')
    text=once(text, '\tgrid->find_widget<styled_widget>("status").set_use_markup(true);',
              '''    grid->find_widget<styled_widget>("status").set_use_markup(true);
    if(alliance_game::individual_lobby_room(lobby_info_.gamelist(),game.id)) {
        for(const auto& item : std::vector<std::pair<std::string,unsigned>>{{"name",32},{"scenario",38},{"status",18}}) {
            auto& value=grid->find_widget<label>(item.first);
            value.set_can_wrap(true);value.set_characters_per_line(item.second);
        }
    }''')
    text=once(text,'\tinfo_icon.set_tooltip(ss.str());',
              '''    if(const auto* room=alliance_game::individual_lobby_room(lobby_info_.gamelist(),game.id))
        ss << "\\n\\n" << font::escape_text(alliance_game::individual_listing_review(*room));
    info_icon.set_tooltip(ss.str());''')
    # mp game_info has already sanitized the name; the new plain review takes a
    # copy of raw server publication and binds join to its revision.
    text=once(text, '\tfind_widget<button>("observe_global").set_active(can_observe);',
              '    const bool individual=alliance_game::individual_lobby_room(lobby_info_.gamelist(),selected_game_id_)!=nullptr;\n    find_widget<button>("observe_global").set_label(individual?"Room details":"Observe");\n    find_widget<button>("observe_global").set_active(individual||can_observe);')
    text=once(text, 'void mp_lobby::enter_game(const mp::game_info& game, JOIN_MODE mode)\n{',
              '''void mp_lobby::enter_game(const mp::game_info& game, JOIN_MODE mode)
{
    if(mode==DO_OBSERVE) {
        if(const auto* room=alliance_game::individual_lobby_room(lobby_info_.gamelist(),game.id)) {
            const config details=*room;
            lobby_delay_gamelist_update_guard delay(*this);
            if(!alliance_game::show_individual_details(details,game.can_observe()))return;
        }
    }''')
    text=once(text,'\tconfig join_data;\n\tjoin_data["id"] = std::to_string(game.id);',
              '''    config join_data;
    if(try_join) {
        if(const auto* room=alliance_game::individual_lobby_room(lobby_info_.gamelist(),game.id)) {
            const config reviewed=*room;
            lobby_delay_gamelist_update_guard delay(*this);
            if(!alliance_game::review_individual_join(reviewed))return;
            join_data["room_revision"]=reviewed["room_revision"];
        }
    }
    join_data["id"] = std::to_string(game.id);''')
    # Reuse the native dashboard checkboxes without changing social-filter preferences.
    for preference,default in (("friends_in_game","true"),("blocked_in_game","false"),("vacant_slots","false"),("invert","false")):
        text=once(text,'[]() {return prefs::get().fi_'+preference+'();}', '[]() {return '+default+';}')
        text=once(text,'[](bool v) {prefs::get().set_fi_'+preference+'(v);}', '[](bool) {}')
    text=once(text,'return filter_friends_->get_widget_value() ? info.has_friends == true : true;',
              'return alliance_game::individual_lobby_room(lobby_info_.gamelist(),info.id) ? filter_friends_->get_widget_value() : filter_ignored_->get_widget_value();')
    text=once(text,'return filter_ignored_->get_widget_value() == false ? info.has_ignored == false : true;',
              'return alliance_game::individual_filter_settings(alliance_game::individual_lobby_room(lobby_info_.gamelist(),info.id));')
    text=once(text,'return filter_slots_->get_widget_value() ? info.vacant_slots > 0 : true;',
              'return !filter_slots_->get_widget_value() || (info.vacant_slots > 0 && !info.started);')
    text=once(text,'[this](bool val) { return filter_invert_->get_widget_value() ? !val : val; }',
              '[](bool val) { return val; }')
    text=once(text,'\tlobby_info_.set_game_filter_invert(',
              '    lobby_info_.add_game_filter([this](const mp::game_info& info) { return !filter_invert_->get_widget_value() || info.started; });\n\tlobby_info_.set_game_filter_invert(')
    text=once(text,'\tserver_info::display(server_information_, announcements_);',
              '    if(alliance_game::edit_individual_filters())update_gamelist_filter();')
    text=once(text,'\t// Set up Lua plugin context', '''    // These upstream services are not provided by the local room server.
    // Keep the native room list, chat, Create and Back controls intact.
    tab_bar.select_row(0);
    tab_bar.set_visible(widget::visibility::invisible);
    queue_join_button->set_visible(widget::visibility::invisible);
    queue_leave_button->set_visible(widget::visibility::invisible);
    for(const char* id : {"view_profile", "view_match_history"})
        if(auto* control=find_widget<button>(id,false,false))
            control->set_visible(widget::visibility::invisible);

\t// Set up Lua plugin context''')
    text=once(text,'void mp_lobby::update_gamelist_filter()\n{',
              'void mp_lobby::update_gamelist_filter()\n{\n    auto& filters=find_widget<button>("server_info");\n    filters.set_label("Gold & turns");\n    filters.set_tooltip(alliance_game::individual_filter_label());')
    text=once(text, '#include "formatter.hpp"', '#include "formatter.hpp"\n#include "font/pango/escape.hpp"')
    return text.encode()
