// Execute the production companion entry point. Only video and game/journal
// boundaries are scripted; account JSON/profile/certificate readers are real.
#include "alliance_game/companion.hpp"
#include "alliance_game/shared_launch.hpp"
#include "alliance_game/shared_result.hpp"
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::string mode;
std::optional<alliance_game::SharedAdmission> queued;
const std::string account="11111111-1111-4111-8111-111111111111";
const std::string run="22222222-2222-4222-8222-222222222222";
int starts=0,reads=0;
}
namespace video { bool headless() { return mode!="nonheadless"; } }
namespace alliance_game {
void queue_shared(SharedAdmission admission) {
    if(mode=="queue_failure")throw std::runtime_error("PRIVATE_QUEUE_ERROR");
    const bool policy=mode.rfind("captain_",0)==0;
    if(admission.profile.port!=15002||admission.computer_controlled==policy||admission.download_only||
       admission.run_id!=run||admission.account_id!=account||admission.token!=std::string(43,'t')||
       admission.upload_only!=(mode=="retry"))throw std::runtime_error("Unexpected fixture admission");
    if(policy) {
        if(admission.captain_action!=mode.substr(8)||admission.after_turn!=3)
            throw std::runtime_error("Unexpected captain policy");
    } else if(!admission.captain_action.empty()||admission.after_turn!=0)
        throw std::runtime_error("Unexpected default policy");
    queued=std::move(admission);
}
void run_pending_shared() {
    if(!queued)throw std::runtime_error("Missing fixture admission");
    ++starts;
    if(mode=="run_failure")throw std::runtime_error("PRIVATE_RUN_ERROR");
}
std::vector<SharedPending> inspect_pending_shared(const std::string& owner) {
    if(owner!=account||starts!=1)throw std::runtime_error("Wrong journal owner");
    ++reads;
    if(mode=="missing_record")return {};
    SharedPending saved;saved.run_id=mode=="wrong_run"?account:run;saved.account_id=account;
    saved.status=mode=="pending"?"pending":"acknowledged";
    return {saved};
}
std::string verified_shared_replay(const std::string& owner,const std::string& id) {
    if(owner!=account||id!=run||reads!=1)throw std::runtime_error("Wrong replay binding");
    if(mode=="replay_failure")throw std::runtime_error("PRIVATE_REPLAY_ERROR");
    return "verified-replay";
}
}
int main(int argc,char** argv) {
    if(argc!=2)return 99;
    mode=argv[1];
    return alliance_game::run_companion();
}
