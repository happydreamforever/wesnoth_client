// SPDX-License-Identifier: GPL-2.0-or-later
// Included inside account_ui.cpp's private namespace, using its cancellable UI job.
namespace pj = boost::json;
namespace pp = alliance::progression_protocol;

bool same_extension_actor(const std::string& actor)
{
    if(actor.empty()||current_session().profile.account_id!=actor||current_session().token.empty()) {
        message("The signed-in player changed. Return to the main menu before continuing.");return false;
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
    if(!team_request(trusted, std::move(input), "Checking hero training…", reply)) return false;
    if(current_session().profile.account_id != actor || current_session().token.empty()) {
        message("The signed-in player changed. Reopen Hero training.");
        return false;
    }
    result = account::parse_document(reply.progression_json);
    pp::response(op, result);
    if(const auto* profile = result.if_contains("profile")) {
        if(pp::text(profile->as_object(), "id", 36) != actor) throw std::runtime_error("Progression actor changed.");
        if(!reload_player(trusted)) return false;
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
    r.unit_type=pp::text(value,"unit_type",100);r.leader_level=pp::number(value,"leader_level",0,20);
    r.hero_id=pp::text(value,"hero_id",80);r.form_id=pp::text(value,"form_id",100);r.database_generation=pp::text(value,"database_generation",36);r.hero_level=value.at("hero_level").as_int64();
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
        const int choice=select("Training history","Latest 20 runs. Viewing history never grants experience.",lines);
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
    message("Training result confirmed. Hero experience awarded: "+std::to_string(pp::number(result,"experience_awarded"))+
        "\n\n"+profile_text(current_session().profile));
}

void campaign_progression(const alliance::TrustedProfile& trusted)
{
    const auto actor=current_session().profile.account_id;
    retry_training_results();
    const auto notice=take_progression_notice();if(!notice.empty()) message(notice);
    for(;;) {
        if(!same_extension_actor(actor)||progression_pending()||replay_pending())return;
        pj::object catalog;if(!progression_request(trusted,"catalog",{},catalog))return;
        const auto& missions=catalog.at("missions").as_array();
        std::vector<std::string> rows{"Training history", "Retry pending results"};
        for(const auto& value:missions){const auto& m=value.as_object();rows.push_back(pp::text(m,"name",100)+(m.at("unlocked").as_bool()?"":" — Locked"));}
        const bool active=!catalog.at("active_run").is_null();if(active)rows.push_back("Inspect / cancel unfinished training");
        const int choice=select("Hero training", "Use an owned campaign hero. Victory grants that hero 20 experience; results are sent automatically. Story campaigns unlock heroes and forms.",rows);
        if(choice<0)return;
        if(choice==0){campaign_history(trusted);continue;}
        if(choice==1){retry_training_results();continue;}
        if(active&&choice==5){const auto& run=pp::object(catalog,"active_run");const auto id=pp::text(run,"id",36);
            if(select_change("Unfinished training","Cancelling grants no experience. A saved completed result should be retried instead.",{"Cancel training"})==0){pj::object result;if(progression_request(trusted,"cancel",{{"run_id",id}},result)){acknowledge_progression_cancel(actor,id);message("Training cancelled.");}}continue;}
        if(choice<2||choice>4)continue;const auto& mission=missions[choice-2].as_object();
        if(!mission.at("unlocked").as_bool()){message("Complete the previous training mission first.");continue;}
        if(active){message("Complete or cancel the unfinished training run first.");continue;}
        std::string hero,form;if(!choose_owned_hero(hero,form))continue;
        const auto id=pp::text(mission,"id",32);
        if(select_change("Start hero training",pp::text(mission,"name",100)+"\nYour selected hero is reserved until completion or cancellation. The story campaign remains unchanged.",{"Start training"})!=0)continue;
        if(!same_extension_actor(actor))return;pj::object result;
        if(!progression_request(trusted,"start",{{"mission_id",id},{"hero_id",hero},{"form_id",form}},result))return;
        auto run=progression_descriptor(pp::object(result,"run"));if(run.mission_id!=id||run.hero_id!=hero||run.form_id!=form)throw std::runtime_error("Training registration differs.");
        queue_progression(run);return;
    }
}
