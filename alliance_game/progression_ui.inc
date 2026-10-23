// SPDX-License-Identifier: GPL-2.0-or-later
// Included inside account_ui.cpp's private namespace, using its cancellable UI job.
namespace pj = boost::json;
namespace pp = alliance::progression_protocol;

bool same_extension_actor(const std::string& actor)
{
    if(actor.empty()||current_session().profile.account_id!=actor||current_session().token.empty()) {
        message("The signed-in player changed. Reopen Alliance before continuing.");return false;
    }
    return true;
}

bool extension_request(const alliance::TrustedProfile& trusted, const std::string& op,
                         pj::object body, pj::object& result)
{
    const auto actor = current_session().profile.account_id;
    if(actor.empty() || current_session().token.empty()) return false;
    account::Request input;
    input.operation = op;
    input.progression_json = pj::serialize(body);
    account::Reply reply;
    if(!team_request(trusted, std::move(input), "Checking campaign progress…", reply)) return false;
    if(current_session().profile.account_id != actor || current_session().token.empty()) {
        message("The signed-in player changed. Reopen Campaign progression.");
        return false;
    }
    result = account::parse_document(reply.progression_json);
    pp::response(op, result);
    if(const auto* profile = result.if_contains("profile")) {
        if(pp::text(profile->as_object(), "id", 36) != actor) throw std::runtime_error("Progression actor changed.");
        const auto& p = profile->as_object();
        auto& current = current_session().profile;
        current.unit_type=pp::text(p,"unit_type",64); current.level=pp::number(p,"level",1,3);
        current.experience=pp::number(p,"experience"); current.total_experience=pp::number(p,"total_experience");
    }
    if(const auto* run=result.if_contains("run"))
        if(pp::text(run->as_object(),"account_id",36)!=actor) throw std::runtime_error("Progression run owner changed.");
    return true;
}

bool progression_request(const alliance::TrustedProfile& trusted, const std::string& op,
                         pj::object body, pj::object& result)
{
    return extension_request(trusted,"progression_"+op,std::move(body),result);
}

ProgressionRun progression_descriptor(const pj::object& value)
{
    pp::run(value);
    ProgressionRun r;
    r.id=pp::text(value,"id",36); r.account_id=pp::text(value,"account_id",36);
    r.username=pp::text(value,"username",24);r.mission_id=pp::text(value,"mission_id",32);
    r.mission_version=pp::text(value,"mission_version",64);r.ruleset_version=pp::text(value,"ruleset_version",32);
    r.unit_type=pp::text(value,"unit_type",64);r.leader_level=pp::number(value,"leader_level",1,3);
    r.nonce=pp::text(value,"nonce",64);
    if(!valid_progression_run(r)||r.account_id!=current_session().profile.account_id)
        throw std::runtime_error("Invalid progression descriptor.");
    return r;
}

void campaign_history(const alliance::TrustedProfile& trusted)
{
    pj::object result;
    if(!progression_request(trusted,"history",{},result)) return;
    std::vector<std::string> lines;
    for(const auto& value:result.at("runs").as_array()) {
        const auto& r=value.as_object();
        lines.push_back(pp::text(r,"mission_name",100)+" — "+pp::text(r,"state",16)+
            " — "+pp::text(r,"outcome",8,true)+" — XP "+std::to_string(pp::number(r,"experience_awarded"))+
            "\nRun: "+pp::text(r,"id",36)+"\n"+pp::text(r,"created_at",40));
    }
    if(lines.empty()) message("No campaign runs yet.");
    else {
        const auto actor=current_session().profile.account_id;
        const int choice=select("Campaign history","Latest 20 runs. Viewing history never grants experience.",lines);
        if(choice<0||static_cast<std::size_t>(choice)>=result.at("runs").as_array().size()) return;
        const auto& row=result.at("runs").as_array()[choice].as_object();
        if(pp::text(row,"state",16)!="completed") {message("This mission has no completed replay.");return;}
        const auto run=pp::text(row,"id",36);
        try {(void)verified_progression_replay(actor,run);} catch(...) {
            message("This completed mission's replay is not available on this device. Its server result remains saved.");return;
        }
        if(select_change("Completed campaign replay",lines[choice]+"\nWatching a replay grants no experience.",{"Watch replay"})!=0) return;
        if(!same_extension_actor(actor)) return;
        queue_alliance_replay(AllianceReplay::campaign,actor,run);
    }
}

void campaign_pending(const alliance::TrustedProfile& trusted)
{
    const auto actor=current_session().profile.account_id;
    const auto records=inspect_pending_progression(actor);
    std::vector<ProgressionPending> pending;
    std::vector<std::string> labels;
    for(const auto& item:records) if(item.status=="pending") {
        pending.push_back(item);
        labels.push_back(item.run.mission_id+" — "+item.outcome+"\nRun: "+item.run.id);
    }
    if(pending.empty()) {message("No completed campaign results are waiting to be sent.");return;}
    const int index=select("Pending campaign results","Saved on this device until the server confirms them.",labels);
    if(index<0||static_cast<std::size_t>(index)>=pending.size()) return;
    const auto& item=pending[index];
    if(select_change("Send campaign result","Outcome: "+item.outcome+"\nRun: "+item.run.id+
        "\nThe server applies the fixed reward at most once. Your replay stays saved locally.",{"Send result"})!=0) return;
    if(!same_extension_actor(actor)) return;
    pj::object result;
    if(!progression_request(trusted,"finish",{{"run_id",item.run.id},{"mission_id",item.run.mission_id},
        {"mission_version",item.run.mission_version},{"nonce",item.run.nonce},{"outcome",item.outcome},
        {"replay_sha256",item.replay_sha256},{"format","alliance-native-campaign-result-v1"}},result)) {
        message("Result remains saved on this device. Check campaign history before retrying; a confirmed result cannot award twice.");
        return;
    }
    const auto& returned=pp::object(result,"run");
    if(pp::text(returned,"id",36)!=item.run.id||pp::text(returned,"outcome",8)!=item.outcome)
        throw std::runtime_error("Result acknowledgement differs.");
    acknowledge_progression_result(actor,item.run.id,item.outcome);
    message("Campaign result confirmed. Leader experience awarded: "+std::to_string(pp::number(result,"experience_awarded"))+
        "\n\n"+profile_text(current_session().profile));
}

void campaign_progression(const alliance::TrustedProfile& trusted)
{
    const auto actor=current_session().profile.account_id;
    const auto notice=take_progression_notice();if(!notice.empty()) message(notice);
    for(;;) {
        if(!same_extension_actor(actor)||progression_pending()||replay_pending()) return;
        pj::object catalog;
        if(!progression_request(trusted,"catalog",{},catalog)) return;
        const auto& leader=pp::object(catalog,"profile");
        const auto& missions=catalog.at("missions").as_array();
        std::vector<std::string> rows{"Send completed result", "Campaign history", "Choose leader advancement"};
        for(const auto& value:missions) {
            const auto& m=value.as_object();
            rows.push_back(pp::text(m,"name",100)+(m.at("unlocked").as_bool()?"":" — Locked")+
                (m.at("completed").as_bool()?" — Completed":""));
        }
        const bool active=!catalog.at("active_run").is_null();
        if(active) rows.push_back("Inspect / cancel unfinished mission");
        const int choice=select("Campaign progression",
            "Grow your saved personal leader through three connected missions. Victory grants 20 XP. "
            "Permanent advancement is chosen here between games.\n"+
            current_session().profile.unit_type+", level "+std::to_string(current_session().profile.level)+
            "; XP "+std::to_string(current_session().profile.experience)+
            (active?"\nAn unfinished mission is reserved. Send its saved result or cancel it before starting again.":""),rows);
        if(choice<0) return;
        if(choice==0) {campaign_pending(trusted);continue;}
        if(choice==1) {campaign_history(trusted);continue;}
        if(choice==2) {
            std::vector<std::string> options;
            for(const auto& item:leader.at("advancement_choices").as_array()) options.emplace_back(item.as_string());
            if(options.empty()) {message("No advancement is available yet. Your leader follows its normal Wesnoth advancement tree.");continue;}
            const int which=select_change("Choose permanent advancement","This changes your saved leader for future games.",options);
            if(which<0||static_cast<std::size_t>(which)>=options.size()) continue;
            if(!same_extension_actor(actor)) return;
            pj::object result;
            if(progression_request(trusted,"advance",{{"unit_type",options[which]}},result)) message("Leader advanced.\n\n"+profile_text(current_session().profile));
            continue;
        }
        if(active&&choice==6) {
            const auto& run=pp::object(catalog,"active_run");
            const auto id=pp::text(run,"id",36);
            if(select_change("Unfinished mission","Run: "+id+"\nIf you completed it, use Send completed result instead. "
                "Cancelling releases your leader and grants no experience. Unfinished missions must restart.",{"Cancel this mission"})==0) {
                if(!same_extension_actor(actor)) return;
                pj::object result;
                if(progression_request(trusted,"cancel",{{"run_id",id}},result)) {
                    if(pp::text(pp::object(result,"run"),"id",36)!=id) throw std::runtime_error("Cancellation differs.");
                    acknowledge_progression_cancel(current_session().profile.account_id,id);
                    message("Mission cancelled. Your leader is available again. No experience was awarded.");
                }
            }
            continue;
        }
        if(choice<3||choice>5) continue;
        const auto& mission=missions[choice-3].as_object();
        if(!mission.at("unlocked").as_bool()) {message("Complete the previous mission first.");continue;}
        if(active) {message("Send the existing completed result or cancel the unfinished mission first.");continue;}
        const auto id=pp::text(mission,"id",32);
        if(select_change("Start campaign mission",pp::text(mission,"name",100)+
            "\nYour saved leader will be reserved until the result is confirmed or this run is cancelled. "
            "Quitting before completion requires a fresh attempt; loading an old save cannot award account experience.",{"Start mission"})!=0) continue;
        if(!same_extension_actor(actor)) return;
        pj::object result;
        if(!progression_request(trusted,"start",{{"mission_id",id}},result)) {
            message("The start could not be confirmed. Reopen Campaign progression to inspect any reserved run before trying again.");
            return;
        }
        auto run=progression_descriptor(pp::object(result,"run"));
        if(run.mission_id!=id) throw std::runtime_error("Mission differs.");
        if(!same_extension_actor(actor)) return;
        queue_progression(run);
        return;
    }
}
