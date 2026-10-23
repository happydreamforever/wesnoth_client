// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/casual_session.hpp"
#include "alliance_game/account_ui.hpp"
namespace alliance_game {
void clear_casual_secret(std::string& text) noexcept {
    volatile char* bytes=text.empty()?nullptr:&text[0];
    for(std::size_t i=0;i<text.size();++i)bytes[i]=0;
    text.clear();
}
CasualAdmission::~CasualAdmission(){clear_casual_secret(ticket);}
std::optional<CasualAdmission> prepare_casual_admission() {
    const auto session=player_session();if(!session)return std::nullopt;
    alliance::account::Request request;request.operation="casual_ticket";
    alliance::account::Reply reply;if(!player_request(request,reply))return std::nullopt;
    if(reply.account_id!=session->account.account_id||reply.username!=session->account.username||
       reply.database_generation!=session->account.database_generation){clear_casual_secret(reply.ticket);return std::nullopt;}
    CasualAdmission result{session->trusted,std::move(reply.ticket),reply.username,reply.account_id,reply.database_generation};
    result.trusted.port=15000;
    return result;
}
std::optional<std::string> casual_selection_ticket(const std::string& game_id,int side,
    const std::string& hero_id,const std::string& form_id) {
    const auto session=player_session();if(!session)return std::nullopt;
    alliance::account::Request request;request.operation="casual_select";request.game_id=game_id;
    request.side=side;request.hero_id=hero_id;request.form_id=form_id;
    alliance::account::Reply reply;if(!player_request(request,reply))return std::nullopt;
    return std::move(reply.selection_ticket);
}
}
