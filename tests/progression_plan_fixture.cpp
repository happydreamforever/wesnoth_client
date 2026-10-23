// SPDX-License-Identifier: GPL-2.0-or-later
#include "alliance_game/progression_plan.hpp"
#include <cassert>
#include <iostream>
#include <vector>
int main() {
 using namespace alliance_game;
 ProgressionRun good{"10000000-0000-4000-8000-000000000001","20000000-0000-4000-8000-000000000001","Player_1","alliance_training_1",progression_mission_version,"alliance-v1","Spearman",std::string(64,'a'),1};
 int checks=0;auto expect=[&](bool value){assert(value);++checks;};
 expect(valid_progression_run(good));expect(progression_faction(good)=="Loyalists");
 expect(progression_missions().size()==3);
 for(std::size_t size=0;size<160;++size) {
  auto bad=good;bad.nonce=std::string(size,'a');expect(valid_progression_run(bad)==(size==64));
  bad=good;bad.username=std::string(size,'a');expect(valid_progression_run(bad)==(size>=3&&size<=24));
 }
 for(auto member:{&ProgressionRun::id,&ProgressionRun::account_id}) {
  for(const auto& text:std::vector<std::string>{"","../x",std::string(36,'a'),"10000000-0000-4000-8000-00000000000G"}) {auto bad=good;bad.*member=text;expect(!valid_progression_run(bad));}
 }
 for(auto member:{&ProgressionRun::mission_id,&ProgressionRun::mission_version,&ProgressionRun::ruleset_version,&ProgressionRun::unit_type}) {
  auto bad=good;bad.*member="unsupported";expect(!valid_progression_run(bad));expect(!same_progression_run(good,bad));
 }
 for(const auto& u:std::vector<std::string>{"player name","two\nlines","with-dash","<tag>","password=secret"}) {auto bad=good;bad.username=u;expect(!valid_progression_run(bad));}
 for(int level=-1;level<6;++level) {auto bad=good;bad.leader_level=level;expect(valid_progression_run(bad)==(level==1));}
 queue_progression(good);expect(progression_pending());bool rejected=false;try{queue_progression(good);}catch(...){rejected=true;}expect(rejected);
 const auto taken=take_pending_progression();expect(taken&&same_progression_run(*taken,good));expect(!take_pending_progression());expect(!progression_pending());
 std::cout<<"PASS progression plan checks="<<checks<<'\n';
}
