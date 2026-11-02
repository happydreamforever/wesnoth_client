"""Guarded Android-only Court presentation. Shared worker inputs stay intact."""
from hashlib import sha256
from pathlib import Path

WORKSPACE_PIN = 'aeb9a9d638b9fdb8f8961e54d706cf452385fd46237d410eb13f52ca667804d8'
ACCOUNT_PIN = 'a2be9cb9acc20a143cecf3d740898d4f526430195f8780d4e6fcbfff4426bec9'
ROOT = Path(__file__).resolve().parent
HEADERS = ('court_mobile_gesture.hpp', 'court_mobile_camera.hpp', 'court_mobile_viewport.hpp',
           'court_mobile_members.inc', 'court_mobile_workspace.inc')

def replace(source, before, after, count=1):
    if source.count(before) != count:
        raise ValueError('Court mobile presentation boundary differs: '+repr(before[:100])+'; found '+str(source.count(before))+'; originals preserved.')
    return source.replace(before, after)

def section(source, begin, end):
    if source.count(begin) != 1 or source.count(end) != 1:
        raise ValueError('Court mobile method boundary differs.')
    return source[source.index(begin):source.index(end)]

def generate_account_ui(source):
    if sha256(source).hexdigest() != ACCOUNT_PIN:
        raise ValueError('Pinned Court account UI changed; originals preserved.')
    return replace(source, b'#include "alliance_game/court_ui.hpp"', b'#include "court_mobile_workspace.hpp"')

def generate_workspace(source):
    if sha256(source).hexdigest() != WORKSPACE_PIN:
        raise ValueError('Pinned Court workspace changed; originals preserved.')
    s=source.decode('utf-8')
    s=replace(s, '#include "alliance_game/court_map.hpp"', '#include "alliance_game/court_map.hpp"\n#include "court_mobile_viewport.hpp"\n#include "alliance_game/native_menu_theme.hpp"\n#include "gui/dialogs/message.hpp"')
    s=replace(s, 'court_map::Camera world_camera,area_camera,bracket_camera;', 'court_map::Camera world_camera,area_camera,bracket_camera;\n    std::map<std::string,court_map::Camera> area_cameras;std::map<std::string,std::string> map_selections;std::map<std::string,bool> map_camera_initialized;')
    s=replace(s, 'struct Action {std::string label;std::function<void()> run;};', 'struct Action {std::string label;std::function<void()> run;bool navigation=false;};')
    old=section(s,'inline std::string workspace_window(){','class Workspace final:')
    form=section(old,'    auto& form_layer=','    auto& more=')
    layout=(ROOT/'court_mobile_layout.inc').read_text().replace('    /*COURT_FORM*/',form.rstrip())
    s=replace(s,old,layout+'\n')
    s=replace(s,'    void actions();void refresh_widgets();', '#include "court_mobile_members.inc"\n    void actions();void refresh_widgets();')
    # Blocking action feedback must not depend on the empty footer label's
    # previous geometry. Ordinary background refresh/status stays nonmodal.
    s=replace(s,'void hint(const std::string& value){find_widget<gui2::label>("court_notice").set_label(value);}',
        'void hint(const std::string& value){auto& notice=find_widget<gui2::label>("court_notice");if(notice.get_label().str()!=value){notice.set_label(value);invalidate_layout();notice.queue_redraw();}}')
    for text,count in (
        ('"Finish or cancel your current changes first."',2),
        ('"This action is unconfirmed. Refresh its status before another purchase."',1),
        ('"Earn or unlock a hero before joining a battle."',1),
        ('"Enter a valid whole number."',1),
        ('"No eligible choice is available for "+field.label+"."',1),
        ('"Income must be 0–20; turn limit must be 1–1000."',1),
    ):
        s=replace(s,'hint('+text+');','mobile_problem('+text+');',count)
    s=replace(s,'if(team_id().empty()){hint("Join a Team first.");return;}',
        'if(team_id().empty()){mobile_problem("Join a Team first.");return;}')
    s=replace(s,'hint(e.what());','mobile_problem(e.what());',2)
    s=replace(s,'void process()override{if(!visible_)return;',
        'void process()override{if(!visible_||mobile_alert_open_)return;')
    s=replace(s,'if(!reply.ok){hint(reply.message.empty()?"Could not update. Try Refresh.":reply.message);',
        'if(!reply.ok){const auto message=reply.message.empty()?"Could not update. Try Refresh.":reply.message;if((mutation||operation_=="command_status")&&!connection_error(reply.code))mobile_problem(message,"Action could not finish");else hint(message);')
    # Input-change validation still updates inline feedback, but a transient
    # empty/partial number must never open a modal while the player is typing.
    s=replace(s,'if(updating_||!form_)return;collect_fields();form_->reviewed=false;map();actions();',
        'if(updating_||!form_)return;mobile_edit([this]{collect_fields();});form_->reviewed=false;map();actions();')
    s=replace(s,'return state_.view=="competitions"?state_.bracket_camera:state_.depth?state_.area_camera:state_.world_camera;', 'return state_.view=="competitions"?state_.bracket_camera:state_.depth?state_.area_cameras[state_.area]:state_.world_camera;')
    old=section(s,'inline void Workspace::map(){','inline void Workspace::select(){')
    s=replace(s,old,'#include "court_mobile_workspace.inc"\n')
    old=section(s,'inline void Workspace::handle_event(', 'inline void Workspace::pre_show()')
    s=replace(s,old,'')
    # Explicitly tag only navigation actions. All command lambdas are retained.
    s=replace(s,'void add(std::string name,std::function<void()> action){actions_.push_back({std::move(name),std::move(action)});}',
        'void add(std::string name,std::function<void()> action){actions_.push_back({std::move(name),std::move(action),false});}\n    void add_navigation(std::string name,std::function<void()> action){actions_.push_back({std::move(name),std::move(action),true});}')
    for name in ('Enter Area','Open Castle','Find Team','Area Teams','Battle history','Wallet history','My applications'):
        s=replace(s,'add("'+name+'",','add_navigation("'+name+'",')
    s=replace(s,'primary.set_active(!job_&&!actions_.empty());','primary.set_active(mobile_can_run(0));')
    s=replace(s,'std::vector<std::string> names{"More actions"};','std::vector<std::string> names{"More"};')
    s=replace(s,'if(job_)return;if(form_)submit();else if(!actions_.empty())actions_[0].run();','if(form_)submit();else if(mobile_can_run(0))actions_[0].run();')
    # Form and mutation controls retain their existing busy guard.
    s=replace(s,'more.set_active(!job_);','more.set_active(!job_||(!mutation_&&std::any_of(actions_.begin(),actions_.end(),[](const auto& a){return a.navigation;})));')
    s=replace(s,'if(updating_||job_)return;const auto n=', 'if(updating_||mutation_||(job_&&form_))return;const auto n=')
    s=replace(s,'if(n>0&&std::size_t(n)<actions_.size()){auto fn=', 'if(n>0&&mobile_can_run(std::size_t(n))){auto fn=')
    # Retain complete authentication/journal/consent processing, changing only
    # when presentation is refreshed. A response for another view stays cached.
    s=replace(s,'const auto value=object(reply.document,"data");if(!mutation)',
        'const auto value=object(reply.document,"data");const bool presentation_changed=mutation||operation_=="command_status"||data(key_)!=value;if(!mutation)')
    s=replace(s,'hint("Saved");refresh();}refresh_widgets();}', 'hint("Saved");refresh();}if(presentation_changed)refresh_widgets();}')
    s=replace(s,'    }if(!form_&&std::chrono::steady_clock::now()>=countdown_)','    }mobile_present_problem();mobile_tick();if(!form_&&std::chrono::steady_clock::now()>=countdown_)')
    s=replace(s,'queued_.clear();refresh_widgets();refresh();}', 'queued_.clear();mobile_cancel();refresh_widgets();refresh();}',2)
    s=replace(s,'void back(){if(form_)', 'void back(){mobile_cancel();if(form_)')
    s=replace(s,'~Workspace(){visible_=false;', '~Workspace(){mobile_cancel();visible_=false;')
    s=replace(s,'state_.map=!state_.map;map();invalidate_layout();','mobile_cancel();state_.map=!state_.map;mobile_list_dirty_=true;refresh_widgets();invalidate_layout();')
    s=replace(s,'auto& c=active_camera();c.zoom_at','mobile_gesture_.stop();auto& c=active_camera();mobile_->invalidate();c.zoom_at',2)
    s=replace(s,'active_camera().centre();map();','mobile_gesture_.stop();active_camera().centre();mobile_->invalidate();map();')
    # A typed search switches to the readable list without changing its camera.
    s=replace(s,'state_.search=find_widget<gui2::text_box>("court_search").get_value();','state_.search=find_widget<gui2::text_box>("court_search").get_value();if(state_.view.empty()&&state_.depth<2&&!state_.search.empty()){mobile_cancel();state_.map=false;}')
    s=replace(s,'state_.depth=depth;state_.view.clear();state_.selected.clear();', 'if(state_.view.empty()&&state_.depth<2)state_.map_selections[state_.depth?state_.area:""]=state_.selected;state_.depth=depth;state_.view.clear();state_.selected=depth<2?state_.map_selections[depth?id:""]:"";')
    s=replace(s,'state_.view=view;state_.history_castle=', 'if(state_.view.empty()&&state_.depth<2)state_.map_selections[state_.depth?state_.area:""]=state_.selected;state_.view=view;state_.history_castle=')
    s=replace(s,'state_.view.clear();state_.selected.clear();refresh_widgets();refresh();', 'state_.view.clear();state_.selected=state_.depth<2?state_.map_selections[state_.depth?state_.area:""]:"";refresh_widgets();refresh();')
    # Avoid synchronizing invisible list widgets. The card projection remains
    # the original authoritative source of labels/selection/action eligibility.
    a=s.index('    auto& list=find_widget<gui2::listbox>("court_list");const auto scroll=')
    b=s.index('    find_widget<gui2::menu_button>("court_filter").set_visible',a)
    block=s[a:b]
    block=block.replace('bool same=old.size()==cards_.size();','bool same=!mobile_list_dirty_&&old.size()==cards_.size();')
    s=s[:a]+'    if(!mobile_geo()){\n'+block+'mobile_list_dirty_=false;}else mobile_list_dirty_=true;\n'+s[b:]
    s=replace(s,'updating_=false;map();if(form_)render_form();else detail();actions();invalidate_layout();}', 'updating_=false;map();if(form_)render_form();else detail();actions();mobile_layout();}')
    s=replace(s,'    auto bind=[this]', '''    find_widget<gui2::menu_button>("court_choices").set_visible(gui2::widget::visibility::invisible);
    gui2::event::connect_signal_notify_modified(find_widget<gui2::menu_button>("court_choices"),[this](auto&&...){if(updating_)return;int n=find_widget<gui2::menu_button>("court_choices").get_value();if(n>0&&std::size_t(n)<=mobile_candidates_.size()){auto id=mobile_candidates_[n-1];mobile_choose(id);}});
    gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("court_collapse"),[this](auto&&...){mobile_gesture_.stop();mobile_expanded_=!mobile_expanded_;mobile_layout();});
    gui2::event::connect_signal_mouse_left_click(find_widget<gui2::button>("court_search_toggle"),[this](auto&&...){mobile_cancel();state_.map=false;mobile_list_dirty_=true;refresh_widgets();keyboard_capture(&find_widget<gui2::text_box>("court_search"));});
    auto bind=[this]''')
    return s.encode('utf-8')
