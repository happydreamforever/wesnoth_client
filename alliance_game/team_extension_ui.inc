// SPDX-License-Identifier: GPL-2.0-or-later
// Account UI private helpers. Every result is bound to the current signed-in actor.
class CaptainOrderDialog final : public gui2::dialogs::modal_dialog {
public:
    explicit CaptainOrderDialog(std::string& text):modal_dialog("edit_text"),text_(text) {set_allow_plugin_skip(false);}
private:
    const std::string& window_id() const override {static const std::string id="edit_text";return id;}
    void pre_show() override {
        auto& title=find_widget<gui2::label>("title");title.set_use_markup(false);title.set_label("Captain orders");
        auto& label=find_widget<gui2::label>("label");label.set_use_markup(false);
        label.set_label("Instructions for current team members (3–500 UTF-8 bytes):");
        auto& input=find_widget<gui2::text_box>("text");input.set_max_input_length(500);input.set_value(text_);
        keyboard_capture(&input);add_to_tab_order(&input);find_widget<gui2::button>("ok").set_label("Review");
    }
    void post_show() override {
        auto& input=find_widget<gui2::text_box>("text");
        if(get_retval()==gui2::retval::OK) text_=input.get_value();
        input.set_value("");
    }
    std::string& text_;
};
void captain_orders(const alliance::TrustedProfile& profile)
{
    const auto actor=current_session().profile.account_id;
    for(;;) {
        if(!same_extension_actor(actor)) return;
        account::Request request;request.operation="my_team";
        account::Reply own;
        if(!team_request(profile,request,"Checking your team…",own)) return;
        if(!own.team) {message("Join a team to read its captain's orders.");return;}
        const auto team=*own.team;
        pj::object result;
        if(!extension_request(profile,"coordination_orders",{{"team_id",team.id}},result)) return;
        if(pp::text(result,"team_id",36)!=team.id) throw std::runtime_error("Order team differs.");
        const auto& orders=result.at("orders").as_array();
        BattleView view;view.title="Captain orders — "+team.name;
        view.subtitle="Current team members only  •  Latest 10 orders shown";
        for(const auto& item:orders) {
            const auto& order=item.as_object();
            view.rows.push_back({pp::text(order,"author_name",24),pp::text(order,"created_at",40),
                pp::order_text(order),"icons/letter_and_ale.png",{}});
        }
        if(orders.empty())view.rows.push_back({"No captain orders yet","Instructions will appear here",
            "Your captain can post a plan for current team members. The latest 10 are shown; 50 are retained.","icons/letter_and_ale.png",{}});
        const bool captain=team.captain_id==actor;
        view.actions={{"Refresh orders"},{"Write orders",captain}};
        const int choice=show_battle_view(view).action;
        if(choice<0||!same_extension_actor(actor))return;
        if(choice==0)continue;
        if(choice==1&&captain) {
            std::string text;CaptainOrderDialog dialog(text);dialog.show();
            if(dialog.get_retval()!=gui2::retval::OK) continue;
            pj::object body{{"team_id",team.id},{"text",text}};
            try {pp::request("coordination_post",body);} catch(...) {message("Use 3–500 UTF-8 bytes of readable instructions.");continue;}
            if(select_change("Post captain orders",text,{"Post to my team"})!=0) continue;
            if(!same_extension_actor(actor)) return;
            pj::object saved;
            if(!extension_request(profile,"coordination_post",body,saved)) {
                message("Posting was not confirmed. Refresh the orders before posting again.");return;
            }
            const auto& order=pp::object(saved,"order");
            if(pp::text(order,"author_id",36)!=current_session().profile.account_id||pp::order_text(order)!=text)
                throw std::runtime_error("Posted order differs.");
            message("Captain orders saved.");
        }
    }
}
void shared_result_review(const alliance::TrustedProfile& profile,const account::Battle& battle)
{
    const auto actor=current_session().profile.account_id;
    pj::object result;
    if(!extension_request(profile,"shared_review",{{"run_id",battle.id}},result)) return;
    if(pp::text(result,"run_id",36)!=battle.id) throw std::runtime_error("Result run differs.");
    const auto status=pp::text(result,"review_state",32);
    const auto winner=pp::text(result,"winner_team_id",36,true);
    std::string outcome=result.at("draw").as_bool()?"Draw":"Not determined";
    for(const auto& team:battle.teams) if(team.id==winner) outcome="Winner: "+team.name;
    const auto text="Battle: "+battle.id+"\nResult: "+status+"\n"+outcome+
        "\nPlayer reports: "+std::to_string(pp::number(result,"reports_received"))+"/"+std::to_string(pp::number(result,"required_reports"))+
        "\nCaptain confirmations: "+std::to_string(pp::number(result,"captains_approved"))+"/2"+
        "\nExperience and rating are awarded once after all players' recorded outcomes agree and both captains confirm. "
        "A disputed result requires administrator review. Practice never grants rewards.";
    bool captain=false;
    for(const auto& team:battle.teams) if(team.captain_id==current_session().profile.account_id) captain=true;
    if(status!="awaiting_captains"||!captain) {message(text);return;}
    if(select_change("Review shared battle result",text,{"Confirm this result as captain"})!=0) return;
    if(!same_extension_actor(actor)) return;
    const auto digest=pp::text(result,"candidate_digest",64);
    pj::object confirmed;
    if(extension_request(profile,"shared_confirm",{{"run_id",battle.id},{"candidate_digest",digest}},confirmed)) {
        if(pp::text(confirmed,"run_id",36)!=battle.id||pp::text(confirmed,"candidate_digest",64)!=digest)
            throw std::runtime_error("Result confirmation differs.");
        message("Captain result confirmation saved. Refresh the result to see settlement, then View personal leader and My team for rewards.");
    } else message("Confirmation was not established. Refresh this result before trying again.");
}

void join_shared_battle(const alliance::TrustedProfile& profile,const account::Battle& viewed,bool upload_only)
{
    const auto actor=current_session().profile.account_id;
    const std::string explanation=upload_only
        ? "Send the completed result and exact saved replay for this player. An identical retry cannot grant rewards twice."
        : viewed.state=="running" ? "Reconnect to your existing side. The server replays the recorded battle; no new leader or army is created."
        : "Join your fixed side. The shared battle starts only after every selected, Ready player connects and loads the same scenario.";
    if(select_change(upload_only?"Send saved battle result":"Join shared battle",explanation+"\nBattle: "+viewed.id,
        {upload_only?"Send saved result":"Join my side"})!=0) return;
    if(!same_extension_actor(actor)) return;
    account::Request request;request.operation="battle_detail";request.battle_id=viewed.id;
    account::Reply latest;
    if(!team_request(profile,request,"Checking the shared battle…",latest)||!latest.battle) return;
    if(!same_extension_actor(actor)) return;
    const auto& battle=*latest.battle;
    bool selected=false,all_ready=true;
    for(const auto& team:battle.teams) {
        all_ready=all_ready&&team.approved;
        for(const auto& player:team.players) {selected=selected||player.id==actor;all_ready=all_ready&&player.ready;}
    }
    if(battle.id!=viewed.id||!selected||
       (!upload_only&&battle.state!="running"&&(battle.state!="draft"||!all_ready||battle.validity!="valid"))||
       (upload_only&&battle.state!="running"&&battle.state!="completed")) {
        message("The battle is no longer eligible for this action. Refresh its saved plan.");return;
    }
    SharedAdmission admission{profile,battle.id,actor,current_session().token,upload_only};
    admission.profile.port=15002;
    queue_shared(std::move(admission));
}

void download_shared_replay(const alliance::TrustedProfile& profile,const account::Battle& viewed)
{
    const auto actor=current_session().profile.account_id;
    if(select_change("Download completed battle replay",
        "Retrieve the retained recording from your Windows server. This does not change the battle or grant experience.\nBattle: "+viewed.id,
        {"Download my permitted recording"})!=0) return;
    if(!same_extension_actor(actor)) return;
    account::Request request;request.operation="battle_detail";request.battle_id=viewed.id;
    account::Reply latest;
    if(!team_request(profile,request,"Checking the completed battle…",latest)||!latest.battle) return;
    if(!same_extension_actor(actor)) return;
    bool permitted=false;
    for(const auto& team:latest.battle->teams) {
        permitted=permitted||team.captain_id==actor;
        for(const auto& player:team.players) permitted=permitted||player.id==actor;
    }
    if(latest.battle->id!=viewed.id||latest.battle->state!="completed"||!permitted) {
        message("A completed recording is available only to this battle's recorded participants and captains.");return;
    }
    SharedAdmission admission{profile,viewed.id,actor,current_session().token};
    admission.profile.port=15002;admission.download_only=true;
    queue_shared(std::move(admission));
}
