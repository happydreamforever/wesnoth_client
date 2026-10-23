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
